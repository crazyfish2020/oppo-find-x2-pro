#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_system_a16.py —— 校验最终 system.img 是否就是我们要的东西

检查项
======
  A. 超级块（magic / compat=0x7 / incompat=0x1 / blkszbits=12）
  B. 元数据全集 vs 源镜像：期望「仅多 2 个新条目」+「32 个 APEX 的 size 变大」，
     其余任何差异都算失败
  C. 两个新文件：/system/bin/netbpfload（0750, uid0:gid0, 86432B, xattr=bpfloader_exec）
                 /system/etc/init/wb_netbpfload.rc（0644, 1255B, xattr=system_file）
  D. 从镜像里反提 netbpfload，md5 必须 = 1e0f9e37e83c1f32de5bb700f84ca299
  E. init.rc 里必须是 exec_start wb_netbpfload
  F. 32 个 APEX 的内容 md5 必须等于 07-重建/_apexout/ 里的新包

用法
====
  verify_system_a16.py <system.img> [--src <源system.img>] [--apexout <目录>]
"""

import argparse
import hashlib
import os
import struct
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
EROFSTOOL = "/Users/skeletondie/.workbuddy-ai/tools/erofs/erofs-utils/1.9.4/bin"
FSCK = os.path.join(EROFSTOOL, "fsck.erofs")
TMP = os.path.join(PROJ, "07-重建", "_verify")
sys.path.insert(0, HERE)
import erofs_rebuild as ER          # noqa: E402

NETBPF_MD5 = "1e0f9e37e83c1f32de5bb700f84ca299"
NETBPF_SIZE = 86432
RC_SIZE = 1255


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def sb(img):
    with open(img, "rb") as f:
        f.seek(1024)
        b = f.read(96)
    return {
        "magic": struct.unpack_from("<I", b, 0)[0],
        "compat": struct.unpack_from("<I", b, 8)[0],
        "incompat": struct.unpack_from("<I", b, 80)[0],
        "blkszbits": b[12],
        "blocks": struct.unpack_from("<I", b, 36)[0],
        "inos": struct.unpack_from("<Q", b, 16)[0],
    }


def walk(img):
    """{relpath: (ftype, mode, uid, gid, size, link)}"""
    d = {}
    for rel, ftype, mode, uid, gid, size, link in ER.walk_meta(img):
        d[rel] = (ftype, mode, uid, gid, size, link)
    return d


def extract(img, path, out):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    p = subprocess.run([FSCK, "--extract=" + out, "--path=" + path,
                        "--xattrs", "--overwrite", "--force", img],
                       capture_output=True, text=True)
    if p.returncode != 0:
        p = subprocess.run([FSCK, "--extract", out, "--path", path,
                            "--xattrs", "--overwrite", "--force", img],
                           capture_output=True, text=True)
    return p.returncode == 0


def xattrs(p):
    out = {}
    for n in ER.mac_listxattr(p):
        if n.startswith("com.apple."):
            continue
        v = ER.mac_getxattr(p, n)
        if v is not None:
            out[n] = v.decode("utf-8", "surrogateescape")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("img")
    ap.add_argument("--src", default=os.path.join(
        PROJ, "02-移植素材", "a16_patched", "system.img"))
    ap.add_argument("--apexout", default=os.path.join(PROJ, "07-重建", "_apexout"))
    a = ap.parse_args()

    fails = []

    def chk(cond, msg, extra=""):
        print("  %s %s%s" % ("✓" if cond else "✗", msg, ("  " + extra) if extra else ""))
        if not cond:
            fails.append(msg)

    print("=" * 74)
    print("校验 %s (%d 字节)" % (a.img, os.path.getsize(a.img)))
    print("=" * 74)

    # ---- A 超级块 ----
    print("[A] 超级块")
    s = sb(a.img)
    chk(s["magic"] == 0xE0F5E1E2, "magic")
    chk(s["compat"] == 0x7, "compat=0x7", "实得 %#x" % s["compat"])
    chk(s["incompat"] == 0x1, "incompat=0x1（4 KB 簇，4.19 内核可读）",
        "实得 %#x" % s["incompat"])
    chk(s["blkszbits"] == 12, "blkszbits=12 (4096B)")
    chk(os.path.getsize(a.img) % 4096 == 0, "大小是 4096 整数倍")

    # ---- B 元数据全集 ----
    print("[B] 元数据全集 vs 源镜像")

    def norm(d):
        """目录的 size 由 mkfs 决定，不参与比对（与 apex_rebuild_all 口径一致）。"""
        out = {}
        for k, (ftype, mode, uid, gid, size, link) in d.items():
            if (mode & 0o170000) == 0o040000:
                size = None
            out[k] = (ftype, mode, uid, gid, size, link)
        return out

    new, old = norm(walk(a.img)), norm(walk(a.src))
    only_new = sorted(set(new) - set(old))
    only_old = sorted(set(old) - set(new))
    chk(not only_old, "没有丢失条目", "丢失 %s" % only_old[:5] if only_old else "")
    chk(set(only_new) == {"system/bin/netbpfload",
                          "system/etc/init/wb_netbpfload.rc"},
        "新增条目恰好是预期的 2 个", "实得 %s" % only_new)

    diffs = [(k, old[k], new[k]) for k in sorted(set(new) & set(old))
             if old[k] != new[k]]

    def only_size_changed(o, n):
        """除 size 外全同"""
        return o[:4] == n[:4] and o[5] == n[5] and o[4] != n[4]

    # 已知且预期的 size 变化：
    #   system/apex/*.apex  —— 重建后变大
    #   system/etc/init/hw/init.rc —— exec_start bpfloader(20) -> exec_start wb_netbpfload(24)，+4 字节
    EXPECT_SIZE_DELTA = {"system/etc/init/hw/init.rc": 4}
    unexpected = []
    n_apx = 0
    for k, o, n in diffs:
        if not only_size_changed(o, n):
            unexpected.append((k, o, n))
            continue
        if k.startswith("system/apex/") and k.endswith(".apex"):
            n_apx += 1
            continue
        if k in EXPECT_SIZE_DELTA and (n[4] - o[4]) == EXPECT_SIZE_DELTA[k]:
            continue
        unexpected.append((k, o, n))
    chk(not unexpected, "除 APEX 与 init.rc(+4B) 外无其它元数据差异",
        "实得 %d 条: %s" % (len(unexpected), unexpected[:3]) if unexpected else "")
    chk(n_apx >= 31, "差异全部集中在重建过的 APEX（size 变大）",
        "实得 %d 个 APEX 变化（cts.shim 未重建，故为 31）" % n_apx)

    # ---- C 两个新文件 ----
    print("[C] 两个新文件")
    nb = new.get("system/bin/netbpfload")
    chk(nb is not None, "/system/bin/netbpfload 存在")
    if nb:
        chk(nb[1] & 0o7777 == 0o750, "权限 0750", "实得 %o" % (nb[1] & 0o7777))
        chk((nb[2], nb[3]) == (0, 0), "uid:gid = 0:0", "实得 %d:%d" % (nb[2], nb[3]))
        chk(nb[4] == NETBPF_SIZE, "大小 %d" % NETBPF_SIZE, "实得 %s" % nb[4])
    rc = new.get("system/etc/init/wb_netbpfload.rc")
    chk(rc is not None, "/system/etc/init/wb_netbpfload.rc 存在")
    if rc:
        chk(rc[1] & 0o7777 == 0o644, "权限 0644", "实得 %o" % (rc[1] & 0o7777))
        chk(rc[4] == RC_SIZE, "大小 %d" % RC_SIZE, "实得 %s" % rc[4])

    # ---- D/E 反提内容 ----
    print("[D] 反提 /system/bin/netbpfload")
    p = os.path.join(TMP, "netbpfload")
    chk(extract(a.img, "/system/bin/netbpfload", p), "解包成功")
    if os.path.exists(p):
        m = md5(p)
        chk(m == NETBPF_MD5, "md5 = %s（内核门限已 NOP）" % NETBPF_MD5, "实得 %s" % m)
        xa = xattrs(p)
        chk(xa.get("security.selinux") == "u:object_r:bpfloader_exec:s0",
            "SELinux = bpfloader_exec", "实得 %s" % xa)

    print("[D2] 反提 /system/etc/init/wb_netbpfload.rc")
    p2 = os.path.join(TMP, "wb_netbpfload.rc")
    chk(extract(a.img, "/system/etc/init/wb_netbpfload.rc", p2), "解包成功")
    if os.path.exists(p2):
        xa = xattrs(p2)
        chk(xa.get("security.selinux") == "u:object_r:system_file:s0",
            "SELinux = system_file", "实得 %s" % xa)
        txt = open(p2, encoding="utf-8", errors="replace").read()
        chk("service wb_netbpfload /system/bin/netbpfload" in txt,
            "服务定义指向 /system/bin/netbpfload")
        # 只检查【真正的指令行】——注释里提到 reboot_on_failure 不算
        live = [ln.strip() for ln in txt.splitlines()
                if ln.strip() and not ln.strip().startswith("#")]
        chk(not any(ln.startswith("reboot_on_failure") for ln in live),
            "该服务【不带】reboot_on_failure（失败也不会 bootloop）",
            "实得 %s" % [ln for ln in live if "reboot" in ln])

    print("[E] 反提 /system/etc/init/hw/init.rc")
    p3 = os.path.join(TMP, "init.rc")
    chk(extract(a.img, "/system/etc/init/hw/init.rc", p3), "解包成功")
    if os.path.exists(p3):
        txt = open(p3, encoding="utf-8", errors="replace").read()
        chk("exec_start wb_netbpfload" in txt, "含 exec_start wb_netbpfload")
        chk("exec_start bpfloader\n" not in txt,
            "已不含裸的 exec_start bpfloader")

    # ---- F 32 个 APEX ----
    print("[F] 32 个 APEX 的内容 md5")
    bad = []
    for fn in sorted(os.listdir(a.apexout)):
        if not fn.endswith(".apex"):
            continue
        dst = os.path.join(TMP, "apex_" + fn)
        if not extract(a.img, "/system/apex/" + fn, dst):
            bad.append(fn + "(解包失败)")
            continue
        if md5(dst) != md5(os.path.join(a.apexout, fn)):
            bad.append(fn)
    n = len([f for f in os.listdir(a.apexout) if f.endswith(".apex")])
    chk(not bad, "镜像内 %d 个新 APEX 与 _apexout 逐字节一致" % n,
        "不一致: %s" % bad[:5] if bad else "")

    print("=" * 74)
    if fails:
        print("✗ %d 项未通过: %s" % (len(fails), fails))
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
