#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_ramdisk.py —— 把目录打包成 Android boot 用的 ramdisk（newc cpio + gzip）

为什么需要它
============
本机（Find X2 Pro）的 boot ramdisk 只有 4 个文件：
    init / fstab.qcom / oplus.fstab / avb/{q,r,s}-gsi.avbpubkey
结构极简，所以可以精确地做「只换 init，不换 fstab」这类变量隔离实验。

用法
====
    build_ramdisk.py <源目录> <输出文件> [--gzip-level N]
    build_ramdisk.py <源目录> <输出文件> --list        # 只列出内容，不打包

newc cpio 头（每项 110 字节）
============================
    "070701" + 13 个 8 位十六进制字段：
    ino mode uid gid nlink mtime filesize devmajor devminor rdevmajor rdevminor
    namesize check
    随后是 name（含结尾 NUL，按 4 字节对齐），再是数据（按 4 字节对齐）。
    结尾项的名字是 "TRAILER!!!"，filesize=0。
"""

import argparse
import gzip
import os
import stat as statmod
import struct
import sys

MAGIC = b"070701"
HDR = 110


def _walk(root):
    """返回 [(相对路径, 绝对路径, 是否目录)]，父目录排在子项前（cpio 要求）。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        rel = os.path.relpath(dirpath, root)
        if rel != ".":
            out.append((rel, dirpath, True))
        for fn in filenames:
            ap = os.path.join(dirpath, fn)
            rp = os.path.relpath(ap, root)
            if os.path.islink(ap):
                continue  # 本 ramdisk 不含符号链接；遇到就跳过并告警
            out.append((rp, ap, False))
    return out


def build(src, out_path, gzlevel=9, verbose=True):
    entries = _walk(src)
    if not entries:
        raise SystemExit("源目录为空: %s" % src)

    # 检查有没有符号链接被跳过
    skipped = []
    for dirpath, _dn, fns in os.walk(src):
        for fn in fns:
            ap = os.path.join(dirpath, fn)
            if os.path.islink(ap):
                skipped.append(os.path.relpath(ap, src))
    if skipped:
        print("  ⚠ 跳过符号链接: %s" % ", ".join(skipped))

    buf = bytearray()
    ino = 300000  # 从一个不冲突的 inode 号开始

    def add(rel, data, mode, is_dir, nlink=1):
        nonlocal ino
        ino += 1
        name = rel.encode() + b"\0"
        h = MAGIC
        for v in (ino, mode, 0, 0, nlink, 0, len(data), 0, 0, 0, 0, len(name), 0):
            h += b"%08X" % v
        assert len(h) == HDR, len(h)
        buf.extend(h)
        buf.extend(name)
        buf.extend(b"\0" * ((4 - len(name) % 4) % 4))
        if not is_dir:
            buf.extend(data)
            buf.extend(b"\0" * ((4 - len(data) % 4) % 4))

    for rel, ap, is_dir in entries:
        st = os.lstat(ap)
        if is_dir:
            add(rel, b"", statmod.S_IFDIR | 0o755, True, nlink=2)
        else:
            with open(ap, "rb") as f:
                data = f.read()
            add(rel, data, statmod.S_IFREG | (st.st_mode & 0o7777), False)

    # TRAILER
    name = b"TRAILER!!!\0"
    ino += 1
    h = MAGIC
    for v in (0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, len(name), 0):
        h += b"%08X" % v
    buf.extend(h)
    buf.extend(name)
    buf.extend(b"\0" * ((4 - len(name) % 4) % 4))
    # cpio 惯例：整体再补齐到 512 字节
    buf.extend(b"\0" * ((512 - len(buf) % 512) % 512))

    with open(out_path, "wb") as f:
        with gzip.GzipFile(fileobj=f, mode="wb", compresslevel=gzlevel, mtime=0) as gz:
            gz.write(bytes(buf))

    if verbose:
        print("  打包 %d 项，cpio %d 字节 → gzip %d 字节" % (len(entries), len(buf), os.path.getsize(out_path)))
        for rel, ap, is_dir in entries:
            print("    %s %s" % ("d" if is_dir else "-", rel))
    return out_path


def list_only(src):
    for rel, ap, is_dir in _walk(src):
        print("%s %s" % ("d" if is_dir else "-", rel))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out", nargs="?")
    ap.add_argument("--gzip-level", type=int, default=9)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        list_only(a.src)
        return
    if not a.out:
        raise SystemExit("需要输出路径")
    build(a.src, a.out, a.gzip_level)


if __name__ == "__main__":
    main()
