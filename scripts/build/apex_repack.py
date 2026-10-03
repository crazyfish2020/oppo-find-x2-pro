#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apex_repack.py — 用新 payload 替换 APEX 内的 apex_payload.img，保持 ZIP 结构零位移。

为什么能做到零位移:
  原 APEX 的 apex_payload.img 条目为 Stored(不压缩) 且大小 16,384,000。
  重建后的 payload 更小(16,244,736)，尾部补 0 补回 16,384,000，
  于是所有条目的偏移、大小、extra 字段全部不变，
  只需改写 payload 数据区 + 两处 CRC32(本地头 / 中央目录)。

用法: apex_repack.py <原apex> <新payload.img> <输出apex>
"""
import sys
import struct
import zlib
import os

LFH = b'PK\x03\x04'
CDH = b'PK\x01\x02'
EOCD = b'PK\x05\x06'


def find_local_headers(data):
    out = []
    off = 0
    n = len(data)
    while off + 30 <= n and data[off:off + 4] == LFH:
        (ver, flags, method, mtime, mdate, crc, csize, usize, nlen, elen) = \
            struct.unpack_from('<HHHHHIIIHH', data, off + 4)
        name = data[off + 30:off + 30 + nlen].decode('utf-8', 'replace')
        dstart = off + 30 + nlen + elen
        out.append(dict(off=off, name=name, method=method, flags=flags,
                        crc=crc, csize=csize, usize=usize,
                        dstart=dstart, nlen=nlen, elen=elen))
        off = dstart + csize
    return out


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    src, newpayload, dst = sys.argv[1], sys.argv[2], sys.argv[3]

    data = bytearray(open(src, 'rb').read())
    pl = open(newpayload, 'rb').read()
    print(f"原 APEX     : {src}  ({len(data)} 字节)")
    print(f"新 payload  : {newpayload}  ({len(pl)} 字节)")

    locals_ = find_local_headers(bytes(data))
    print(f"本地文件头 {len(locals_)} 个:")
    for e in locals_:
        print(f"   {e['name']:<30} method={e['method']} csize={e['csize']:>9} data@{e['dstart']}")

    ent = next((e for e in locals_ if e['name'] == 'apex_payload.img'), None)
    if ent is None:
        print("✗ 未找到 apex_payload.img")
        sys.exit(1)
    if ent['method'] != 0:
        print("✗ apex_payload.img 不是 Stored，无法零位移替换")
        sys.exit(1)

    target = ent['csize']
    if len(pl) > target:
        print(f"✗ 新 payload ({len(pl)}) 大于原条目 ({target})，无法零位移")
        sys.exit(1)
    padded = pl + b'\x00' * (target - len(pl))
    print(f"补齐: {len(pl)} -> {len(padded)} (补 {target-len(pl)} 字节 0)")

    new_crc = zlib.crc32(padded) & 0xFFFFFFFF
    print(f"新 CRC32: {new_crc:#010x}  (原 {ent['crc']:#010x})")

    # 1) 写数据区
    data[ent['dstart']:ent['dstart'] + target] = padded
    # 2) 本地头 CRC (偏移 14)
    struct.pack_into('<I', data, ent['off'] + 14, new_crc)

    # 3) 中央目录里的 CRC
    eocd_pos = bytes(data).rfind(EOCD)
    if eocd_pos < 0:
        print("✗ 未找到 EOCD")
        sys.exit(1)
    cd_size, cd_off = struct.unpack_from('<II', data, eocd_pos + 12)
    print(f"中央目录 @ {cd_off}, 大小 {cd_size}")
    p = cd_off
    fixed = 0
    while p < cd_off + cd_size and bytes(data[p:p + 4]) == CDH:
        (ver_made, ver_need, flags, method, mtime, mdate, crc, csize, usize,
         nlen, elen, clen, disk, iattr, eattr, lho) = struct.unpack_from('<HHHHHHIIIHHHHHII', data, p + 4)
        name = bytes(data[p + 46:p + 46 + nlen]).decode('utf-8', 'replace')
        if name == 'apex_payload.img':
            struct.pack_into('<I', data, p + 16, new_crc)
            fixed += 1
            print(f"   已修正中央目录 CRC: {name}")
        p += 46 + nlen + elen + clen
    if fixed == 0:
        print("✗ 中央目录未找到 apex_payload.img")
        sys.exit(1)

    open(dst, 'wb').write(bytes(data))
    print(f"完成: {dst}  ({os.path.getsize(dst)} 字节)  大小是否不变: {os.path.getsize(dst) == len(data)}")


if __name__ == '__main__':
    main()
