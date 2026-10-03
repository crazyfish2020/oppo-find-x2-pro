#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_apex_incompat.py — 体检：把所有会被刷进设备的分区里，
每一个 APEX / OPEX 容器的 payload EROFS 特征位读出来。

为什么需要它：
  4.19.157 内核的 EROFS 驱动只认 feature_incompat = 0x1 (LZ4_0PADDING)。
  含 0x2 (COMPR_RECS / big pcluster, 5.13+) 的镜像挂不上 → mount -EINVAL
  → apexd --bootstrap 失败 → apexd.rc:18 reboot,bootloader → 掉 fastboot。
  之前只重建了 system 里 /system/apex 的包，**别的分区（system_ext 的
  /apex 与 /opex）没动** —— 这个脚本就是用来把"漏网的包"全揪出来的。

用法:
  check_apex_incompat.py                # 用内置的分区/路径清单全量体检
  check_apex_incompat.py <img> <path>   # 只查一个
"""
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

HOME = os.path.expanduser("~")
DONOR = f"{HOME}/Documents/oppo/donor_8t/extracted"
PATCHED = f"{HOME}/Documents/oppo/2026-安卓16/02-移植素材/a16_patched"
BIN = f"{HOME}/.workbuddy-ai/tools/erofs/erofs-utils/1.9.4/bin"
FSCK = f"{BIN}/fsck.erofs"
DUMP = f"{BIN}/dump.erofs"

EROFS_MAGIC = 0xE0F5E1E2


def erofs_sb_incompat(buf):
    """从一段以 EROFS 超级块开头的 buffer 里读 (magic, incompat, blkszbits, compat)"""
    if len(buf) < 1104:
        return None
    magic, checksum, compat = struct.unpack_from("<III", buf, 1024)
    if magic != EROFS_MAGIC:
        return None
    blkszbits = buf[1024 + 12]
    incompat = struct.unpack_from("<I", buf, 1024 + 80)[0]
    return magic, incompat, blkszbits, compat


def apex_payload_incompat(path):
    """读一个 APEX/OPEX 容器内 apex_payload.img 的 EROFS 特征位。
    返回 (kind, incompat, detail)。kind: 'erofs' | 'non-erofs' | 'zip-fail'"""
    try:
        with zipfile.ZipFile(path) as z:
            names = [x for x in z.namelist() if x.endswith("apex_payload.img")]
            if not names:
                return ("non-erofs", None, "无 apex_payload.img")
            with z.open(names[0]) as f:
                head = f.read(1120)
    except zipfile.BadZipFile:
        # 不是 zip —— 直接当裸 EROFS 试
        with open(path, "rb") as f:
            head = f.read(1120)
        r = erofs_sb_incompat(head)
        if r:
            return ("erofs", r[1], "裸 EROFS")
        return ("zip-fail", None, "非 zip 且非 EROFS")
    r = erofs_sb_incompat(head)
    if not r:
        return ("non-erofs", None, "payload 非 EROFS")
    return ("erofs", r[1], "payload")


def list_dir(img, path):
    """用 dump.erofs --ls 列目录，返回文件名列表"""
    out = subprocess.run([DUMP, "--ls", f"--path={path}", img],
                         capture_output=True, text=True).stdout
    names = []
    for ln in out.splitlines():
        m = re.match(r'\s*\d+\s+(\d)\s+(.+?)\s*$', ln)
        if m and m.group(2) not in (".", ".."):
            names.append((m.group(2), int(m.group(1))))   # (名字, file_type)
    return names


def extract_one(img, path, tmpdir):
    """把 img 里的 path 提取到 tmpdir 下的一个文件，返回该文件路径"""
    dst = os.path.join(tmpdir, "blob")
    if os.path.exists(dst):
        os.remove(dst)
    subprocess.run([FSCK, f"--extract={dst}", f"--path={path}", img],
                   capture_output=True)
    return dst if os.path.exists(dst) else None


def probe_image(img, dirs):
    """体检一个镜像里若干个目录下的所有文件"""
    rows = []
    if not os.path.exists(img):
        print(f"  ✗ 镜像不存在: {img}")
        return rows
    tmpdir = tempfile.mkdtemp(prefix="apexchk_")
    try:
        for d in dirs:
            entries = list_dir(img, d)
            if not entries:
                continue
            for name, ftype in entries:
                if ftype != 1:        # 只关心普通文件
                    continue
                p = d.rstrip("/") + "/" + name
                blob = extract_one(img, p, tmpdir)
                if not blob:
                    rows.append((os.path.basename(img), p, "?", None, "提取失败"))
                    continue
                kind, inc, detail = apex_payload_incompat(blob)
                rows.append((os.path.basename(img), p, kind, inc, detail))
                os.remove(blob)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return rows


def verdict(inc):
    if inc is None:
        return "—"
    if inc == 0x1:
        return "✓ 0x1 可挂(4.19)"
    if inc == 0x3:
        return "✗ 0x3 不可挂(需 5.13+)"
    return "⚠ 0x%x 需人工判断" % inc


def main():
    if len(sys.argv) == 3:
        rows = probe_image(sys.argv[1], [os.path.dirname(sys.argv[2])])
        rows = [r for r in rows if r[1] == sys.argv[2]]
    else:
        targets = [
            # (镜像, [要扫的目录])
            (f"{PATCHED}/system_a16final.img", ["/system/apex", "/system/bootstrap-apex"]),
            (f"{DONOR}/system_ext.img",        ["/apex", "/opex"]),
            (f"{DONOR}/my_stock.img",          ["/opex", "/apex"]),
            (f"{DONOR}/my_product.img",        ["/apex", "/opex"]),
            (f"{DONOR}/product.img",           ["/apex", "/opex"]),
            (f"{DONOR}/odm.img",               ["/apex", "/opex"]),
            (f"{DONOR}/vendor.img",            ["/apex", "/opex"]),
            (f"{DONOR}/my_region.img",         ["/apex", "/opex"]),
            (f"{DONOR}/my_manifest.img",       ["/apex", "/opex"]),
            (f"{DONOR}/my_bigball.img",        ["/apex", "/opex"]),
        ]
        rows = []
        for img, dirs in targets:
            print(f"##### {os.path.basename(img)}")
            r = probe_image(img, dirs)
            if not r:
                print("   （无 APEX/OPEX）")
            for x in r:
                print("   %-58s %-9s %s" % (x[1], x[2], verdict(x[3])))
            rows += r

    print()
    print("=" * 100)
    bad = [r for r in rows if r[3] == 0x3]
    ok = [r for r in rows if r[3] == 0x1]
    print("合计 %d 个容器；incompat=0x1 %d 个，incompat=0x3 %d 个" % (len(rows), len(ok), len(bad)))
    if bad:
        print()
        print("✗✗ 下面这些包 4.19 内核挂不上，必须重建：")
        for r in bad:
            print("   %s :: %s" % (r[0], r[1]))
    else:
        print("✓ 没有发现 0x3 的包")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
