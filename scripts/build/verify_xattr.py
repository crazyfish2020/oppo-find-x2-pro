#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_xattr.py — 用「真实 xattr 内容」比对两个 EROFS 镜像（或 EROFS payload）

为什么不能只看 dump.erofs 的 "Xattr size"
=========================================
同一个 SELinux 标签，原包可能内联存储（Xattr size=16），重建后走共享区
（Xattr size=48）。**数值不等 ≠ 标签不同**。本脚本改用：

    fsck.erofs --extract=<tmp> --path=<p> --xattrs <img>
    -> ctypes listxattr/getxattr 读出真实 (name,value) 集合 -> 逐条比对

用法
====
  verify_xattr.py <ref.img> <cand.img> [--paths p1 p2 ...]
  verify_xattr.py <ref.img> <cand.img> --auto <dir> [<dir> ...] [--per-dir N]
  verify_xattr.py --apex <ref.apex> <cand.apex> --auto-in-payload

  --paths   : 显式给出要比对的路径（两镜像内路径相同）
  --auto    : 在给定目录下自动抽样 N 个文件
  --apex    : 输入是 APEX zip，脚本自动解出 apex_payload.img 再比

退出码: 0=全部一致  1=有差异  2=用法/环境错误
"""
import argparse
import ctypes
import ctypes.util
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

EROFSTOOL = "/Users/skeletondie/.workbuddy-ai/tools/erofs/erofs-utils/1.9.4/bin"
FSCK = os.path.join(EROFSTOOL, "fsck.erofs")
DUMP = os.path.join(EROFSTOOL, "dump.erofs")

# ---------- macOS xattr via ctypes ----------
_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
_libc.listxattr.restype = ctypes.c_ssize_t
_libc.listxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int]
_libc.getxattr.restype = ctypes.c_ssize_t
_libc.getxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                           ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
XATTR_NOFOLLOW = 0x0001


def _xattr_names(path):
    p = path.encode()
    n = _libc.listxattr(p, None, 0, XATTR_NOFOLLOW)
    if n <= 0:
        return []
    buf = ctypes.create_string_buffer(n)
    n = _libc.listxattr(p, buf, n, XATTR_NOFOLLOW)
    if n <= 0:
        return []
    return [x for x in buf.raw[:n].split(b"\0") if x]


def read_xattrs(path):
    """返回 {name: bytes}；跳过 macOS 自带命名空间"""
    out = {}
    for nm in _xattr_names(path):
        if nm.startswith(b"com.apple."):
            continue
        vn = _libc.getxattr(path.encode(), nm, None, 0, 0, XATTR_NOFOLLOW)
        if vn <= 0:
            continue
        buf = ctypes.create_string_buffer(vn)
        vn = _libc.getxattr(path.encode(), nm, buf, vn, 0, XATTR_NOFOLLOW)
        if vn <= 0:
            continue
        out[nm.decode("utf-8", "replace")] = buf.raw[:vn]
    return out


# ---------- EROFS 单文件抽取 ----------
def extract_one(img, path, dst):
    if os.path.exists(dst):
        os.remove(dst)
    r = subprocess.run([FSCK, "--extract=" + dst, "--path=" + path, "--xattrs", img],
                       capture_output=True, text=True)
    if not os.path.exists(dst):
        return None, (r.stdout + r.stderr).strip()[:200]
    return dst, None


def list_dir(img, path):
    """列出目录下的 (name, type) —— type: 1=文件 2=目录 7=符号链接"""
    r = subprocess.run([DUMP, "--ls", "--path=" + path, img],
                       capture_output=True, text=True)
    ents = []
    seen_header = False
    for ln in r.stdout.splitlines():
        if ln.strip().startswith("NID TYPE"):
            seen_header = True
            continue
        if not seen_header:
            continue
        parts = ln.split()
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
            name = parts[-1]
            if name in (".", ".."):
                continue
            ents.append((name, int(parts[1])))
    return ents


def sample_files(img, dirs, per_dir):
    """在给定目录里自动抽样文件路径"""
    out = []
    for d in dirs:
        ents = list_dir(img, d)
        files = [n for n, t in ents if t == 1]
        for n in files[:per_dir]:
            out.append(d.rstrip("/") + "/" + n)
        # 递归进子目录，直到凑够
        for n, t in ents:
            if t == 2 and len(out) < per_dir * len(dirs) * 2:
                sub = d.rstrip("/") + "/" + n
                for sn, st in list_dir(img, sub):
                    if st == 1:
                        out.append(sub + "/" + sn)
                        if len(out) >= per_dir * len(dirs) * 2:
                            break
                if len(out) >= per_dir * len(dirs) * 2:
                    break
    # 去重保序
    seen = set()
    res = []
    for p in out:
        if p not in seen:
            seen.add(p)
            res.append(p)
    return res


def unwrap_apex(p, tmpdir, tag):
    """把 APEX zip 里的 apex_payload.img 解出来"""
    if not p.endswith(".apex"):
        return p
    with zipfile.ZipFile(p) as z:
        names = [x for x in z.namelist() if x.endswith("apex_payload.img")]
        if not names:
            raise SystemExit("不是 APEX: " + p)
        out = os.path.join(tmpdir, tag + "_payload.img")
        with open(out, "wb") as f:
            f.write(z.read(names[0]))
    return out


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("ref")
    ap.add_argument("cand")
    ap.add_argument("--paths", nargs="*", default=[])
    ap.add_argument("--auto", nargs="*", default=[])
    ap.add_argument("--per-dir", type=int, default=4)
    ap.add_argument("--apex", action="store_true")
    ap.add_argument("-h", "--help", action="store_true")
    a = ap.parse_args()
    if a.help:
        print(__doc__)
        return 0

    tmp = tempfile.mkdtemp(prefix="vxa_")
    try:
        ref = unwrap_apex(a.ref, tmp, "ref") if a.apex else a.ref
        cand = unwrap_apex(a.cand, tmp, "cand") if a.apex else a.cand

        paths = list(a.paths)
        if a.auto:
            paths += sample_files(ref, a.auto, a.per_dir)
        if not paths:
            print("没有要比对的路径（用 --paths 或 --auto）")
            return 2

        n_ok = n_bad = n_skip = 0
        for i, p in enumerate(paths):
            dr, er = extract_one(ref, p, os.path.join(tmp, "r_%d" % i))
            dc, ec = extract_one(cand, p, os.path.join(tmp, "c_%d" % i))
            if dr is None or dc is None:
                n_skip += 1
                continue
            xr, xc = read_xattrs(dr), read_xattrs(dc)
            if xr == xc:
                n_ok += 1
                print("  ✓ %-60s %s" % (p, _fmt(xr)))
            else:
                n_bad += 1
                print("  ✗ %-60s" % p)
                print("      原: %s" % _fmt(xr))
                print("      新: %s" % _fmt(xc))
            os.remove(dr)
            os.remove(dc)

        print()
        print("  合计: 一致 %d / 不一致 %d / 跳过 %d" % (n_ok, n_bad, n_skip))
        if n_ok == 0:
            print("  ✗ 一个都没比对上 —— 检查镜像/路径是否正确")
            return 2
        return 1 if n_bad else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _fmt(d):
    if not d:
        return "(无 xattr)"
    return " ".join("%s=%s" % (k, v.decode("utf-8", "replace")) for k, v in sorted(d.items()))


if __name__ == "__main__":
    sys.exit(main())
