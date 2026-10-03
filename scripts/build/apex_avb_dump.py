#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apex_avb_dump.py —— 检查 APEX payload 的 AVB/dm-verity 可用性（已重写）

★ 为什么重写
============
旧版本手写解析 HASHTREE 描述符，**字段布局是错的**，导致打印出一堆垃圾
（dm_verity_version=4294967296、image_size=69770609852153856、salt/root_digest 全空），
却仍被当成「验证通过」的依据 —— 这是上一轮排查里最危险的一个假阳性来源。

真正的布局（见 07-重建/_avb/avbtool.py 第 1432 行 AvbHashtreeDescriptor.__init__）：
    tag, num_bytes_following,
    dm_verity_version(u32), image_size(u64), tree_offset(u64), tree_size(u64),
    data_block_size(u32), hash_block_size(u32), fec_num_roots(u32),
    fec_offset(u64), fec_size(u64),
    hash_algorithm(定长字段！不是长度前缀),
    partition_name_len(u32), salt_len(u32), root_digest_len(u32), flags(u32),
    ...padding...
    然后变长数据 partition_name / salt / root_digest 追加在定长结构之后
旧脚本把 hash_algorithm 当「u32 长度 + 字符串」，从那里开始整体错位。

★ 现在的做法
============
不再手写解析 —— **直接调用权威 avbtool 的 info_image**（avbtool 自己的解析才是标准），
然后额外做一件旧脚本从没做过、但恰恰是最关键的事：
    **自己重算一遍 dm-verity 哈希树的根摘要，与描述符里的 root_digest 比对。**
因为 apexd 是靠 Hashtree 描述符建 dm-verity 来挂载 APEX 的，
而 `fsck.erofs` 只校验 EROFS 文件系统本身、**完全不看哈希树** —— 那是个盲区。

用法
====
    apex_avb_dump.py <file.apex | apex_payload.img> [...]
    apex_avb_dump.py --no-tree <file>      # 跳过哈希树重算（大文件时更快）
"""

import hashlib
import os
import re
import struct
import subprocess
import sys
import tempfile
import zipfile

PROJ = os.path.expanduser("~/Documents/oppo/2026-安卓16")
AVBTOOL = os.path.join(PROJ, "07-重建", "_avb", "avbtool.py")
PYEXE = sys.executable


# ---------------------------------------------------------------------------
def get_payload(path):
    """返回 (payload 字节, 临时文件路径或 None)"""
    if path.endswith(".apex") or zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.endswith("apex_payload.img")]
            if not names:
                raise ValueError("包里没有 apex_payload.img")
            data = z.read(names[0])
        fd, tmp = tempfile.mkstemp(suffix=".img")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return data, tmp
    return open(path, "rb").read(), None


def avbtool_info(payload_path):
    """调用权威 avbtool info_image，解析成 dict"""
    r = subprocess.run([PYEXE, AVBTOOL, "info_image", "--image", payload_path],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("avbtool info_image 失败: %s" % (r.stderr or r.stdout))
    d = {}
    for ln in r.stdout.splitlines():
        if ":" in ln:
            k, v = ln.split(":", 1)
            d[k.strip()] = v.strip()
    return d, r.stdout


def as_int(v):
    return int(re.sub(r"[^0-9]", "", v))


def compute_root_digest(data, image_size, data_bs, hash_bs, salt, alg):
    """独立重算 dm-verity 哈希树根摘要（与 avbtool 的算法一致）"""
    ds = hashlib.new(alg).digest_size
    img = data[:image_size]
    nb = (image_size + data_bs - 1) // data_bs
    cur = bytearray()
    for i in range(nb):
        blk = img[i * data_bs:(i + 1) * data_bs]
        if len(blk) < data_bs:
            blk += b"\0" * (data_bs - len(blk))
        h = hashlib.new(alg)
        h.update(salt)
        h.update(blk)
        cur += h.digest()
    per = hash_bs // ds
    while True:
        nhb = (len(cur) // ds + per - 1) // per
        if nhb <= 1:
            blk = bytes(cur[:hash_bs])
            blk += b"\0" * (hash_bs - len(blk))
            h = hashlib.new(alg)
            h.update(salt)
            h.update(blk)
            return h.digest(), nb, len(cur) // ds
        nxt = bytearray()
        for i in range(nhb):
            blk = bytes(cur[i * hash_bs:(i + 1) * hash_bs])
            blk += b"\0" * (hash_bs - len(blk))
            h = hashlib.new(alg)
            h.update(salt)
            h.update(blk)
            nxt += h.digest()
        cur = nxt


# ---------------------------------------------------------------------------
def check(path, do_tree=True):
    res = {"path": path, "ok": False, "notes": []}
    payload, tmp = get_payload(path)
    try:
        res["payload_size"] = len(payload)
        d, _ = avbtool_info(tmp or path)
        for k in ("Original image size", "VBMeta offset", "VBMeta size",
                  "Algorithm", "Public key (sha1)", "Release String"):
            if k in d:
                res[k] = d[k]
        ht = {}
        for k in ("Image Size", "Tree Offset", "Tree Size", "Data Block Size",
                  "Hash Block Size", "Hash Algorithm", "Salt", "Root Digest",
                  "Partition Name"):
            if k in d:
                ht[k] = d[k]
        res["hashtree"] = ht
        if "Salt" not in ht or "Root Digest" not in ht:
            res["notes"].append("✗ 没有 Hashtree 描述符（apexd 取不到 verity 参数）")
            return res

        # 结构自洽：tree_size 必须等于「各层哈希块数之和 × 哈希块大小」
        img_size = as_int(ht["Image Size"])
        data_bs = as_int(ht["Data Block Size"])
        hash_bs = as_int(ht["Hash Block Size"])
        tree_size = as_int(ht["Tree Size"])
        ds = hashlib.new(ht["Hash Algorithm"]).digest_size
        n_data = (img_size + data_bs - 1) // data_bs
        need = (n_data * ds + hash_bs - 1) // hash_bs      # 第 0 层哈希块数
        total_blocks = need
        n = need
        while n > 1:
            n = (n * ds + hash_bs - 1) // hash_bs
            total_blocks += n
        expect_tree = total_blocks * hash_bs
        res["tree_expected"] = expect_tree
        if expect_tree != tree_size:
            res["notes"].append("✗ tree_size 不自洽：声明 %d / 应为 %d"
                                % (tree_size, expect_tree))
        else:
            res["notes"].append("✓ tree_size 自洽 (%d = %d 块 × %d)"
                                % (tree_size, total_blocks, hash_bs))
        if as_int(ht["Tree Offset"]) + tree_size > len(payload):
            res["notes"].append("✗ 哈希树区间超出文件末尾")

        if not do_tree:
            res["ok"] = not any(n.startswith("✗") for n in res["notes"])
            return res

        salt = bytes.fromhex(ht["Salt"])
        rd = bytes.fromhex(ht["Root Digest"])
        calc, nb, nh = compute_root_digest(payload, img_size, data_bs, hash_bs,
                                           salt, ht["Hash Algorithm"])
        res["calc_root"] = calc.hex()
        res["data_blocks"] = nb
        res["hash_level0"] = nh
        if calc == rd:
            res["notes"].append("★★ 重算 root_digest 与描述符一致 —— dm-verity 可正常建立")
            res["ok"] = not any(n.startswith("✗") for n in res["notes"])
        else:
            res["notes"].append("✗ 重算 root_digest 不一致：算出 %s / 描述符 %s"
                                % (calc.hex(), rd.hex()))
    finally:
        if tmp:
            os.remove(tmp)
    return res


def main(argv):
    do_tree = True
    args = []
    for a in argv:
        if a == "--no-tree":
            do_tree = False
        else:
            args.append(a)
    if not args:
        print(__doc__)
        return 2
    bad = 0
    for p in args:
        try:
            r = check(p, do_tree)
        except Exception as e:
            print("=" * 78)
            print("%s\n  异常: %r" % (p, e))
            bad += 1
            continue
        print("=" * 78)
        print(p)
        print("  payload_size   %s" % r.get("payload_size"))
        print("  算法           %s   %s" % (r.get("Algorithm"), r.get("Release String")))
        print("  pubkey sha1    %s" % r.get("Public key (sha1)"))
        ht = r.get("hashtree", {})
        if ht:
            print("  HASHTREE       image_size=%s tree_offset=%s tree_size=%s"
                  % (ht.get("Image Size"), ht.get("Tree Offset"), ht.get("Tree Size")))
            print("                 data_bs=%s hash_bs=%s alg=%s"
                  % (ht.get("Data Block Size"), ht.get("Hash Block Size"),
                     ht.get("Hash Algorithm")))
            print("                 salt=%s" % ht.get("Salt"))
            print("                 root=%s" % ht.get("Root Digest"))
        if "calc_root" in r:
            print("                 重算 root=%s" % r["calc_root"])
            print("                 数据块=%d 0层哈希块=%d" % (r["data_blocks"], r["hash_level0"]))
        for n in r["notes"]:
            print("  %s" % n)
        print("  结论: %s" % ("通过" if r["ok"] else "不通过"))
        if not r["ok"]:
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
