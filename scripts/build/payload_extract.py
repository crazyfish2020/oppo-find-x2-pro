#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
payload_extract.py — 从 Android A/B OTA 的 payload.bin 中抽取指定分区镜像。

payload.bin 结构:
  magic "CrAU" (4B) | version (u64) | manifest_size (u64) |
  metadata_signature_size (u32) | manifest(protobuf) | metadata_signature | data blobs

manifest = DeltaArchiveManifest (update_metadata.proto)
  field 13 : repeated PartitionUpdate partitions
PartitionUpdate
  field 1  : string partition_name
  field 8  : repeated InstallOperation operations
InstallOperation
  field 1  : enum type   (0=REPLACE 1=REPLACE_BZ 8=REPLACE_XZ 6=ZERO 7=DISCARD)
  field 2  : uint64 data_offset
  field 3  : uint64 data_length

用法:
  payload_extract.py <payload.bin> list
  payload_extract.py <payload.bin> extract <分区名> <输出文件>
"""
import sys
import os
import struct
import lzma
import bz2

MAGIC = b'CrAU'

OP_NAMES = {0: 'REPLACE', 1: 'REPLACE_BZ', 2: 'MOVE', 3: 'BSDIFF',
            4: 'SOURCE_COPY', 5: 'SOURCE_BSDIFF', 6: 'ZERO', 7: 'DISCARD',
            8: 'REPLACE_XZ', 9: 'PUFFDIFF', 10: 'BROTLI_BSDIFF',
            11: 'ZUCCHINI', 12: 'LZ4DIFF_BSDIFF', 13: 'LZ4DIFF_PUFFDIFF'}


# ---------------- 极简 protobuf 解析 ----------------
def read_varint(buf, i):
    r = 0
    s = 0
    while True:
        b = buf[i]
        i += 1
        r |= (b & 0x7F) << s
        if not (b & 0x80):
            return r, i
        s += 7


def iter_fields(buf):
    """产出 (field_no, wire_type, value)。value: int / bytes"""
    i = 0
    n = len(buf)
    while i < n:
        key, i = read_varint(buf, i)
        fn, wt = key >> 3, key & 7
        if wt == 0:
            v, i = read_varint(buf, i)
        elif wt == 2:
            ln, i = read_varint(buf, i)
            v = buf[i:i + ln]
            i += ln
        elif wt == 5:
            v = buf[i:i + 4]
            i += 4
        elif wt == 1:
            v = buf[i:i + 8]
            i += 8
        else:
            raise ValueError(f"不支持的 wire type {wt} (field {fn})")
        yield fn, wt, v


def parse_manifest(m):
    parts = []
    for fn, wt, v in iter_fields(m):
        if fn == 13 and wt == 2:          # PartitionUpdate
            name = None
            ops = []
            for f2, w2, v2 in iter_fields(v):
                if f2 == 1 and w2 == 2:
                    name = v2.decode('utf-8', 'replace')
                elif f2 == 8 and w2 == 2:  # InstallOperation
                    otype = doff = dlen = None
                    for f3, w3, v3 in iter_fields(v2):
                        if f3 == 1 and w3 == 0:
                            otype = v3
                        elif f3 == 2 and w3 == 0:
                            doff = v3
                        elif f3 == 3 and w3 == 0:
                            dlen = v3
                    ops.append((otype, doff, dlen))
            parts.append((name, ops))
    return parts


def find_zip_entry_data_offset(zip_path, entry_name=b'payload.bin'):
    """在 zip 中定位 entry 的数据起点。要求该 entry 是 STORED(未压缩)。支持 Zip64。"""
    with open(zip_path, 'rb') as f:
        # --- 读 EOCD (从尾部往前找签名 PK\x05\x06) ---
        f.seek(0, os.SEEK_END)
        fsize = f.tell()
        tail_len = min(fsize, 65557 + 64)
        f.seek(fsize - tail_len)
        tail = f.read(tail_len)
        i = tail.rfind(b'PK\x05\x06')
        if i < 0:
            raise SystemExit("找不到 zip EOCD")
        cd_size, cd_off = struct.unpack_from('<II', tail, i + 12)
        # Zip64 EOCD 定位器 (PK\x06\x07) 在 EOCD 前 20 字节
        j64 = tail.rfind(b'PK\x06\x07')
        if j64 >= 0 and j64 + 20 <= len(tail):
            z64_off, = struct.unpack_from('<Q', tail, j64 + 8)
            f.seek(z64_off)
            z64 = f.read(56)
            if z64[:4] == b'PK\x06\x06':
                cd_size, = struct.unpack_from('<Q', z64, 40)
                cd_off, = struct.unpack_from('<Q', z64, 48)
        # --- 遍历中央目录 ---
        f.seek(cd_off)
        cd = f.read(cd_size)
        j = 0
        names = []
        while j + 46 <= len(cd) and cd[j:j + 4] == b'PK\x01\x02':
            method, = struct.unpack_from('<H', cd, j + 10)
            csize, usize = struct.unpack_from('<II', cd, j + 20)
            nlen, elen, clen = struct.unpack_from('<HHH', cd, j + 28)
            lho, = struct.unpack_from('<I', cd, j + 42)
            name = cd[j + 46:j + 46 + nlen]
            extra = cd[j + 46 + nlen:j + 46 + nlen + elen]
            # --- Zip64 扩展字段 ---
            k = 0
            while k + 4 <= len(extra):
                hid, hsz = struct.unpack_from('<HH', extra, k)
                body = extra[k + 4:k + 4 + hsz]
                if hid == 0x0001:
                    p = 0
                    if usize == 0xFFFFFFFF and p + 8 <= len(body):
                        usize, = struct.unpack_from('<Q', body, p); p += 8
                    if csize == 0xFFFFFFFF and p + 8 <= len(body):
                        csize, = struct.unpack_from('<Q', body, p); p += 8
                    if lho == 0xFFFFFFFF and p + 8 <= len(body):
                        lho, = struct.unpack_from('<Q', body, p); p += 8
                k += 4 + hsz
            names.append(name.decode('utf-8', 'replace'))
            if name == entry_name or name.endswith(b'/' + entry_name):
                if method != 0:
                    raise SystemExit(f"{entry_name} 在 zip 中是压缩存储(method={method})，"
                                     f"请先解出到磁盘")
                f.seek(lho)
                lh = f.read(30)
                if lh[:4] != b'PK\x03\x04':
                    raise SystemExit("本地文件头签名错误")
                lnlen, lelen = struct.unpack_from('<HH', lh, 26)
                return lho + 30 + lnlen + lelen, usize
            j += 46 + nlen + elen + clen
        print("zip 内条目:", ", ".join(names[:40]))
    raise SystemExit(f"zip 里找不到 {entry_name!r}")


def resolve_source(path):
    """返回 (可读文件路径, 基偏移)。支持 .zip（内含 STORED 的 payload.bin）。"""
    if path.lower().endswith('.zip'):
        off, size = find_zip_entry_data_offset(path)
        print(f"[zip] payload.bin 数据起点 {off} (大小 {size})")
        return path, off
    return path, 0


def load_payload(path, base=0):
    """注意: payload 头部的 version / manifest_size / metadata_signature_size
    是大端序 (network order)，protobuf manifest 本身是标准编码。"""
    with open(path, 'rb') as f:
        f.seek(base)
        head = f.read(24)
        if head[:4] != MAGIC:
            raise SystemExit(f"不是 payload.bin (magic={head[:4]!r})")
        ver = struct.unpack_from('>Q', head, 4)[0]
        msize = struct.unpack_from('>Q', head, 12)[0]
        mssize = struct.unpack_from('>I', head, 20)[0] if ver >= 2 else 0
        manifest = f.read(msize)
        data_start = base + 24 + msize + mssize
    return ver, manifest, data_start


def extract(path, part_name, out_path, base=0):
    ver, manifest, data_start = load_payload(path, base)
    parts = parse_manifest(manifest)
    tgt = None
    for name, ops in parts:
        if name == part_name:
            tgt = ops
            break
    if tgt is None:
        raise SystemExit(f"未找到分区 {part_name}；可用: "
                         + ", ".join(n for n, _ in parts))
    print(f"分区 {part_name}: {len(tgt)} 个操作")
    total = 0
    with open(path, 'rb') as f, open(out_path, 'wb') as out:
        for idx, (otype, doff, dlen) in enumerate(tgt):
            tn = OP_NAMES.get(otype, str(otype))
            if otype in (6, 7):           # ZERO / DISCARD -> 写入长度未知，跳过
                print(f"  [{idx}] {tn} (跳过)")
                continue
            f.seek(data_start + doff)
            blob = f.read(dlen)
            if otype == 8:
                data = lzma.decompress(blob)
            elif otype == 1:
                data = bz2.decompress(blob)
            elif otype == 0:
                data = blob
            else:
                raise SystemExit(f"不支持的操作类型 {tn} (第 {idx} 个)")
            out.write(data)
            total += len(data)
            print(f"  [{idx}] {tn} 读 {dlen} -> 解出 {len(data)} 字节 (累计 {total})")
    print(f"写出: {out_path}  {os.path.getsize(out_path)} 字节")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    p, cmd = sys.argv[1], sys.argv[2]
    src, base = resolve_source(p)
    ver, manifest, data_start = load_payload(src, base)
    if cmd == 'list':
        parts = parse_manifest(manifest)
        print(f"payload 版本 {ver}, manifest {len(manifest)} 字节, "
              f"数据起点 {data_start}")
        print(f"分区数: {len(parts)}")
        for name, ops in parts:
            tot = sum(d for _, _, d in ops if d)
            print(f"  {name:24} 操作 {len(ops):4}  压缩数据 {tot} 字节")
    elif cmd == 'extract':
        if len(sys.argv) < 5:
            print("用法: payload_extract.py <payload.bin|zip> extract <分区名> <输出文件>")
            return 1
        extract(src, sys.argv[3], sys.argv[4], base)
    return 0


if __name__ == '__main__':
    sys.exit(main())
