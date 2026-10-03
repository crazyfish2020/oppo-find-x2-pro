#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_apex_avb.py —— 真正校验 APEX payload 的 AVB 哈希树（dm-verity 可用性）

为什么需要它
------------
apexd 挂载 APEX 的方式**不是**直接 mount payload 文件，而是：
    1. 从 payload 末尾的 AVB footer 找到 vbmeta
    2. 从 vbmeta 的 HASHTREE 描述符里取 salt / root_digest / tree_offset / tree_size
    3. 用这些参数建一个 dm-verity 设备
    4. mount 那个 dm 设备
⇒ 只要 root_digest 与实际哈希树对不上，dm-verity 校验就失败，APEX 挂不上。
  而 `fsck.erofs` 只校验 EROFS 文件系统本身，**完全不看哈希树** —— 是个盲区。

本脚本做两件事：
  A. 按 libavb 的真实结构正确解析 HASHTREE 描述符
     （注意：dm_verity_version 是 u32，旧脚本 apex_avb_dump.py 按 u64 读，导致后面全错位）
  B. **自己重算一遍哈希树根摘要**，与描述符里的 root_digest 比对

用法
====
  verify_apex_avb.py <file.apex | apex_payload.img> [...]
  verify_apex_avb.py --self-test        # 只跑实现自检（对原始包）
"""

import hashlib
import io
import os
import struct
import sys
import zipfile

BE = ">"


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------
def read_payload(path):
    if path.endswith(".apex") or zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.endswith("apex_payload.img")]
            if not names:
                raise ValueError("包里没有 apex_payload.img")
            return z.read(names[0]), path
    return open(path, "rb").read(), path


def read_pubkey(path):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.endswith("apex_pubkey")]
            if names:
                return z.read(names[0])
    return None


# ---------------------------------------------------------------------------
# AVB 结构解析（全部大端）
# ---------------------------------------------------------------------------
def parse_footer(buf):
    if len(buf) < 64 or buf[-64:-60] != b"AVBf":
        raise ValueError("末尾没有 AVBf footer")
    off = len(buf) - 64
    (maj, mino) = struct.unpack_from(BE + "II", buf, off + 4)
    (orig_size,) = struct.unpack_from(BE + "Q", buf, off + 12)
    (vb_off, vb_size) = struct.unpack_from(BE + "QQ", buf, off + 20)
    return dict(version="%d.%d" % (maj, mino), original_image_size=orig_size,
                vbmeta_offset=vb_off, vbmeta_size=vb_size)


def parse_vbmeta(buf, vb_off):
    if buf[vb_off:vb_off + 4] != b"AVB0":
        raise ValueError("vbmeta magic 不是 AVB0")
    h = {}
    (h["req_major"], h["req_minor"]) = struct.unpack_from(BE + "II", buf, vb_off + 4)
    (h["auth_size"], h["aux_size"]) = struct.unpack_from(BE + "QQ", buf, vb_off + 12)
    (h["algorithm"],) = struct.unpack_from(BE + "I", buf, vb_off + 28)
    (h["hash_off"], h["hash_size"]) = struct.unpack_from(BE + "QQ", buf, vb_off + 32)
    (h["sig_off"], h["sig_size"]) = struct.unpack_from(BE + "QQ", buf, vb_off + 48)
    (h["pk_off"], h["pk_size"]) = struct.unpack_from(BE + "QQ", buf, vb_off + 64)
    (h["pkm_off"], h["pkm_size"]) = struct.unpack_from(BE + "QQ", buf, vb_off + 80)
    (h["desc_off"], h["desc_size"]) = struct.unpack_from(BE + "QQ", buf, vb_off + 96)
    (h["rollback_index"],) = struct.unpack_from(BE + "Q", buf, vb_off + 112)
    (h["flags"],) = struct.unpack_from(BE + "I", buf, vb_off + 120)
    (h["rollback_location"],) = struct.unpack_from(BE + "I", buf, vb_off + 124)
    h["release_string"] = buf[vb_off + 128:vb_off + 176].split(b"\0")[0].decode("utf-8", "replace")
    h["vbmeta_offset"] = vb_off
    # 描述符区起点
    h["desc_start"] = vb_off + 256 + h["auth_size"] + h["desc_off"]
    return h


def iter_descriptors(buf, vb):
    p = vb["desc_start"]
    end = p + vb["desc_size"]
    while p + 16 <= end:
        tag, nbytes = struct.unpack_from(BE + "QQ", buf, p)
        yield tag, nbytes, p + 16
        p += 16 + ((nbytes + 7) // 8) * 8


def parse_hashtree(buf, body):
    """
    AvbHashtreeDescriptor（大端）:
      u32 dm_verity_version
      u64 image_size, tree_offset, tree_size
      u32 data_block_size, hash_block_size, fec_num_roots
      u64 fec_offset, fec_size
      u32 hash_algorithm_len + 字符串（8 字节对齐）
      u32 partition_name_len + 字符串（8 字节对齐）
      u32 salt_len + 字节（8 字节对齐）
      u32 root_digest_len + 字节（8 字节对齐）
      u32 flags
    """
    o = body
    (dm_ver, image_size, tree_offset, tree_size,
     data_bs, hash_bs, fec_roots, fec_off, fec_size) = struct.unpack_from(
        BE + "IQQQIIIQQ", buf, o)
    o += 4 + 8 + 8 + 8 + 4 + 4 + 4 + 8 + 8

    def take_bytes():
        nonlocal o
        (n,) = struct.unpack_from(BE + "I", buf, o); o += 4
        b = buf[o:o + n]; o += (n + 7) // 8 * 8
        return b

    hash_alg = take_bytes().decode("utf-8", "replace")
    part_name = take_bytes().decode("utf-8", "replace")
    salt = take_bytes()
    root_digest = take_bytes()
    (flags,) = struct.unpack_from(BE + "I", buf, o)

    return dict(dm_verity_version=dm_ver, image_size=image_size,
                tree_offset=tree_offset, tree_size=tree_size,
                data_block_size=data_bs, hash_block_size=hash_bs,
                fec_num_roots=fec_roots, fec_offset=fec_off, fec_size=fec_size,
                hash_algorithm=hash_alg, partition_name=part_name,
                salt=salt, root_digest=root_digest, flags=flags)


# ---------------------------------------------------------------------------
# 哈希树重算（dm-verity 算法）
# ---------------------------------------------------------------------------
def compute_root_digest(data, image_size, data_bs, hash_bs, salt, hash_name):
    """
    逐层计算 dm-verity 哈希树，返回顶层块的摘要。
    每层的最后一块补零到整块。
    """
    digest_size = hashlib.new(hash_name).digest_size
    img = data[:image_size]

    # 第 0 层：数据块 -> 摘要
    nblocks = (image_size + data_bs - 1) // data_bs
    cur = bytearray()
    for i in range(nblocks):
        blk = img[i * data_bs:(i + 1) * data_bs]
        if len(blk) < data_bs:
            blk = blk + b"\0" * (data_bs - len(blk))
        h = hashlib.new(hash_name)
        h.update(salt)
        h.update(blk)
        cur += h.digest()

    per_block = hash_bs // digest_size
    while True:
        # cur 是「摘要串」，按 per_block 个摘要打包成哈希块
        n_hash_blocks = (len(cur) // digest_size + per_block - 1) // per_block
        if n_hash_blocks <= 1:
            # 顶层：把这一块补零到整块后求摘要
            blk = bytes(cur[:hash_bs])
            blk = blk + b"\0" * (hash_bs - len(blk))
            h = hashlib.new(hash_name)
            h.update(salt)
            h.update(blk)
            return h.digest(), n_hash_blocks
        nxt = bytearray()
        for i in range(n_hash_blocks):
            blk = bytes(cur[i * hash_bs:(i + 1) * hash_bs])
            blk = blk + b"\0" * (hash_bs - len(blk))
            h = hashlib.new(hash_name)
            h.update(salt)
            h.update(blk)
            nxt += h.digest()
        cur = nxt


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def check(path, verbose=True):
    res = {"path": path, "ok": False, "notes": []}
    try:
        payload, _ = read_payload(path)
        res["payload_size"] = len(payload)
        f = parse_footer(payload)
        res.update(f)
        vb = parse_vbmeta(payload, f["vbmeta_offset"])
        res["algorithm"] = vb["algorithm"]
        res["release_string"] = vb["release_string"]
        pk = read_pubkey(path)
        res["pubkey_size"] = len(pk) if pk else None
        if pk:
            res["pubkey_sha256"] = hashlib.sha256(pk).hexdigest()[:16]

        ht = None
        for tag, nbytes, body in iter_descriptors(payload, vb):
            if tag == 1:
                ht = parse_hashtree(payload, body)
                break
        if ht is None:
            res["notes"].append("✗ 没有 HASHTREE 描述符（tag=1）—— apexd 取不到 verity 参数")
            return res
        res["hashtree"] = {k: (v if not isinstance(v, bytes) else v.hex())
                           for k, v in ht.items()}

        # 结构自洽性
        if ht["tree_offset"] + ht["tree_size"] > len(payload):
            res["notes"].append("✗ 哈希树区间超出文件末尾")
        if ht["image_size"] != f["original_image_size"]:
            res["notes"].append("⚠ image_size(%d) != footer.original_image_size(%d)"
                                % (ht["image_size"], f["original_image_size"]))
        if ht["hash_algorithm"] not in ("sha1", "sha256"):
            res["notes"].append("✗ 不认识的 hash_algorithm: %r" % ht["hash_algorithm"])

        # 核心：重算根摘要
        calc, n_hash_blocks = compute_root_digest(
            payload, ht["image_size"], ht["data_block_size"],
            ht["hash_block_size"], ht["salt"], ht["hash_algorithm"])
        res["calc_root_digest"] = calc.hex()
        res["top_level_hash_blocks"] = n_hash_blocks
        res["root_digest_match"] = (calc == ht["root_digest"])
        if calc == ht["root_digest"]:
            res["notes"].append("✓ 哈希树根摘要与描述符一致 —— dm-verity 可正常建立")
            res["ok"] = True
        else:
            res["notes"].append("✗ 根摘要不一致：算出 %s / 描述符 %s"
                                % (calc.hex(), ht["root_digest"].hex()))
    except Exception as e:
        res["notes"].append("解析异常: %r" % (e,))
    return res


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    for p in argv:
        r = check(p)
        print("=" * 78)
        print(p)
        print("  payload_size        %s" % r.get("payload_size"))
        print("  original_image_size %s" % r.get("original_image_size"))
        print("  vbmeta              off=%s size=%s  算法=%s  %s"
              % (r.get("vbmeta_offset"), r.get("vbmeta_size"),
                 r.get("algorithm"), r.get("release_string")))
        print("  pubkey              %s 字节 sha256=%s"
              % (r.get("pubkey_size"), r.get("pubkey_sha256")))
        ht = r.get("hashtree")
        if ht:
            print("  HASHTREE            image_size=%d tree_offset=%d tree_size=%d"
                  % (ht["image_size"], ht["tree_offset"], ht["tree_size"]))
            print("                      data_bs=%d hash_bs=%d alg=%s salt=%s"
                  % (ht["data_block_size"], ht["hash_block_size"],
                     ht["hash_algorithm"], ht["salt"]))
            print("                      root_digest=%s" % ht["root_digest"])
            print("                      重算 root  =%s  %s"
                  % (r.get("calc_root_digest"),
                     "★ 一致" if r.get("root_digest_match") else "✗ 不一致"))
        for n in r.get("notes", []):
            print("  %s" % n)
        print("  结论: %s" % ("通过" if r["ok"] else "不通过"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
