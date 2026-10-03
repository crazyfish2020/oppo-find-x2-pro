#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apex_v2_sign.py —— 给已重建的 APEX 补回 APK Signature Scheme v2 签名块（+ SELinux 标签）

为什么需要这个脚本（第 6 号根因）
=================================
apex_rebuild.py 为了绕开「big-pcluster 16KB 物理簇 / incompat=0x3」而重打 zip，
但它用 python zipfile 逐条写条目，**把原包的 APK Signing Block 整块丢了**。

Android 16 的 PackageManagerService.scanApexPackages 会对 /system/apex/*.apex 调
    ApkSignatureVerifier.verify(path, SIGNING_SCHEME_V2)
拿不到签名块就直接抛：
    PackageManagerException: No APK Signature Scheme v2 signature in package
      Caused by: SignatureNotFoundException: No APK Signing Block before ZIP Central Directory
⇒ system_server 在 PackageManagerService.<init> 阶段 FATAL ⇒ 整机 boot loop。
实测症状（v5 日志）：
    E System : java.lang.IllegalStateException: Failed to scan: /system/apex/com.android.adbd.apex
    E AndroidRuntime: *** FATAL EXCEPTION IN SYSTEM PROCESS: main
    dmesg   : init: [PHOENIX] phx_is_bootup_critical_service: bootup critical service zygote crashed

顺带修掉的第二个坑
==================
apex_rebuild_all.py 产出的新 APEX 是 python 新建的文件，**security.selinux 一条都没带**
（实测 32/33 个包 xattr 为空，只有没被重建的 com.android.apex.cts.shim.apex 还留着
u:object_r:system_file:s0）。permissive 下能跑，enforcing 下 /system/apex/* 会变成
unlabeled。本脚本一并补回 u:object_r:system_file:s0。

做法
====
用 AOSP 官方 apksig 库（tools/ApexSign.java，就是 apksigner 的内核）补 v2 签名。
  * 只签 v2，不签 v1/v3：APEX 不该有 META-INF v1 签名；v3 要 lineage 结构。
  * 签名者证书 = 07-重建/_avb/apex_key.pem 自签 ⇒ 证书公钥与包内 apex_pubkey、
    以及 AVB 签名用的公钥三者同源。
  * apksig 只在 Central Directory 之前插入 Signing Block，**不动任何既有条目数据**
    ⇒ apex_payload.img 的 data offset % 4096 == 0 天然保持（APEX 挂载硬要求）。

幂等：已有签名块的包跳过签名（只补 xattr）。

用法
====
  apex_v2_sign.py [--dir DIR] [--only NAME[,NAME...]] [--dry-run] [--no-verify] [--jobs N]

  --dry-run   只扫描报告，不落盘
"""

import argparse
import ctypes
import glob
import os
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
TOOLS = os.path.join(HERE, "tools")
DEFAULT_DIR = os.path.join(PROJ, "07-重建", "tree", "system", "apex")
TMPROOT = os.path.join(PROJ, "07-重建", "_apexsign_tmp")
KS = os.path.join(PROJ, "07-重建", "_avb", "apex.p12")
ALIAS = "apexkey"
PASS = "android"
JAR = "/Users/skeletondie/.workbuddy-ai/tools/apksig/apksig-8.7.3.jar"
JAVA = "/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home/bin/java"

APEX_SELINUX = b"u:object_r:system_file:s0"
MAGIC = b"APK Sig Block 42"
ALIGN = 4096

_libc = ctypes.CDLL("/usr/lib/libc.dylib", use_errno=True)
_libc.listxattr.restype = ctypes.c_ssize_t
_libc.getxattr.restype = ctypes.c_ssize_t
_libc.setxattr.restype = ctypes.c_int


# --------------------------------------------------------------------------- #
# macOS xattr
# --------------------------------------------------------------------------- #
def listxattr(path):
    p = path.encode()
    n = _libc.listxattr(p, None, 0, 0)
    if n <= 0:
        return []
    buf = ctypes.create_string_buffer(n)
    n = _libc.listxattr(p, buf, n, 0)
    if n <= 0:
        return []
    return [x.decode() for x in buf.raw[:n].split(b"\x00") if x]


def getxattr(path, name):
    p = path.encode()
    nm = name.encode()
    n = _libc.getxattr(p, nm, None, 0, 0, 0)
    if n < 0:
        return None
    buf = ctypes.create_string_buffer(n)
    n = _libc.getxattr(p, nm, buf, n, 0, 0, 0)
    if n < 0:
        return None
    return buf.raw[:n]


def setxattr(path, name, value):
    p = path.encode()
    nm = name.encode()
    return _libc.setxattr(p, nm, value, len(value), 0, 0) == 0


def copy_xattrs(src, dst, extra=None):
    """把 src 的全部 xattr 复制到 dst；extra 是 {name: value} 兜底补写。"""
    names = listxattr(src)
    n = 0
    for x in names:
        v = getxattr(src, x)
        if v is not None and setxattr(dst, x, v):
            n += 1
    for k, v in (extra or {}).items():
        if k not in names:
            if setxattr(dst, k, v):
                n += 1
    return n


# --------------------------------------------------------------------------- #
# zip / apex 探测
# --------------------------------------------------------------------------- #
def _le32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def _le64(b, o):
    return struct.unpack_from("<Q", b, o)[0]


def sigblock_info(path):
    """返回 (有无签名块, sigblock总长, cd_off, cd_size) ；解析失败抛异常。"""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        sz = f.tell()
        n = min(sz, 65536 + 22)
        f.seek(sz - n)
        buf = f.read(n)
        i = buf.rfind(b"PK\x05\x06")
        if i < 0:
            raise RuntimeError("找不到 EOCD: " + path)
        eo = sz - n + i
        cd_size = _le32(buf, i + 12)
        cd_off = _le32(buf, i + 16)
        if cd_off == 0xFFFFFFFF:
            return (False, 0, cd_off, cd_size)
        if cd_off < 24:
            return (False, 0, cd_off, cd_size)
        f.seek(cd_off - 24)
        tail = f.read(24)
        if len(tail) < 24:
            return (False, 0, cd_off, cd_size)
        blk = _le64(tail, 0)
        if tail[8:24] == MAGIC:
            return (True, blk + 8, cd_off, cd_size)
        return (False, 0, cd_off, cd_size)


def payload_offset(path):
    """读第一个 local file header（应当是 apex_payload.img），返回其 data offset。"""
    with open(path, "rb") as f:
        lh = f.read(30)
        if lh[:4] != b"PK\x03\x04":
            return None
        nlen = struct.unpack_from("<H", lh, 26)[0]
        elen = struct.unpack_from("<H", lh, 28)[0]
        name = f.read(nlen).decode("utf-8", "replace")
        return name, 30 + nlen + elen


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def sign_one(path, verify=True, dry=False):
    """返回 (状态字符串, ok)。"""
    name = os.path.basename(path)
    has_sig, blklen, cd_off, cd_size = sigblock_info(path)
    xattrs = listxattr(path)
    need_xattr = "security.selinux" not in xattrs

    if has_sig and not need_xattr:
        return ("跳过（签名块与标签都在）", True)

    if dry:
        act = []
        if not has_sig:
            act.append("补 v2 签名")
        if need_xattr:
            act.append("补 security.selinux")
        return ("dry-run: " + " + ".join(act), True)

    # ---- 补 xattr（不需要签名时直接就地补） ----
    if has_sig and need_xattr:
        if setxattr(path, "security.selinux", APEX_SELINUX):
            return ("已补 security.selinux", True)
        return ("补 xattr 失败", False)

    # ---- 需要签名：写 tmp → 复制 xattr → rename ----
    if not os.path.exists(JAR):
        return ("缺少 apksig jar: " + JAR, False)
    if not os.path.exists(JAVA):
        return ("缺少 java: " + JAVA, False)

    os.makedirs(TMPROOT, exist_ok=True)
    tmp = os.path.join(TMPROOT, name + ".signed.tmp")
    if os.path.exists(tmp):
        os.remove(tmp)

    st = os.stat(path)
    rc, out = run([JAVA, "-cp", JAR + ":" + TOOLS, "ApexSign", path, tmp, KS, ALIAS, PASS, "30"])
    if rc != 0 or not os.path.exists(tmp):
        return ("签名失败 rc=%d: %s" % (rc, out.strip().splitlines()[-1:] or ""), False)

    # apksig 新建的文件 mode 可能是 0644/0600，显式对齐原 mode
    os.chmod(tmp, st.st_mode & 0o7777)
    nx = copy_xattrs(path, tmp, extra={"security.selinux": APEX_SELINUX})

    # 自检：签名块在、payload 4096 对齐
    h2, blk2, cdo2, cds2 = sigblock_info(tmp)
    po = payload_offset(tmp)
    if not h2:
        os.remove(tmp)
        return ("自检失败：签名块没写进去", False)
    if po and po[1] % ALIGN != 0:
        os.remove(tmp)
        return ("自检失败：payload data offset=%d 不是 4096 倍数" % po[1], False)

    if verify:
        rc, out = run([JAVA, "-cp", JAR + ":" + TOOLS, "ApexVerify", tmp])
        if rc != 0:
            os.remove(tmp)
            return ("签名校验未通过: " + out.strip().splitlines()[-1:][0] if out.strip() else "verify 失败", False)

    os.replace(tmp, path)
    return ("已补 v2 签名（sigblock=%d, xattr=%d, payload@%d）" % (blk2, nx, po[1]), True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=DEFAULT_DIR)
    ap.add_argument("--only", default=None, help="只处理这些包（逗号分隔，不带 .apex 也行）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-verify", action="store_true")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.dir, "*.apex")))
    if args.only:
        want = set(x.strip() for x in args.only.split(",") if x.strip())
        files = [f for f in files
                 if os.path.basename(f) in want or os.path.basename(f)[:-5] in want]
    if not files:
        print("没有找到 APEX：%s" % args.dir)
        return 1

    print("目录: %s   共 %d 个包%s" % (args.dir, len(files), "  [dry-run]" if args.dry_run else ""))
    print("-" * 78)
    n_ok = n_bad = 0
    bad = []
    for p in files:
        nm = os.path.basename(p)
        try:
            msg, ok = sign_one(p, verify=not args.no_verify, dry=args.dry_run)
        except Exception as e:                       # noqa: BLE001
            msg, ok = ("异常: %s: %s" % (type(e).__name__, e), False)
        print("  %-4s %-46s %s" % ("OK" if ok else "FAIL", nm, msg))
        if ok:
            n_ok += 1
        else:
            n_bad += 1
            bad.append(nm)
    print("-" * 78)
    print("完成: OK=%d  FAIL=%d" % (n_ok, n_bad))
    if bad:
        print("失败清单: " + ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
