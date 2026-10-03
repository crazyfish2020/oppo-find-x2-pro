#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patch_boot_cmdline.py —— 修改 Android boot 镜像的 cmdline 字段

背景
----
A16 的 boot.img cmdline 里没有 androidboot.selinux，默认 = enforcing，
且 init 是 buildvariant=user ⇒ setenforce 内建命令被编译期禁用。
于是 v3 在 on early-init 里加的 setenforce 0 形同虚设，
SELinux 把 apexd-bootstrap 的 sh 挡死（apexd 域无 shell_exec execute 权限）。

解法
----
在 cmdline 里加 androidboot.selinux=permissive。
init 启动时会读 ro.boot.selinux 并调用 security_setenforce(0)，
这条路径【不受】ALLOW_PERMISSIVE_SELINUX 编译选项限制。
（参照：TWRP 的 recovery 分区 cmdline 里就是 androidboot.selinux=permissive，
  所以它 getenforce 返回 Permissive —— 同一颗内核，证明内核支持。）

用法:
    patch_boot_cmdline.py <src_boot.img> <dst_boot.img> [追加的参数...]
"""
import sys
import struct

CMD_OFF = 64          # boot header: cmdline 字段偏移
CMD_LEN = 512         # cmdline 字段长度（定长）
EXTRA_OFF = 64 + CMD_LEN + 32   # extra_cmdline: 64+512+id(32)
EXTRA_LEN = 1024


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    src, dst = sys.argv[1], sys.argv[2]
    adds = sys.argv[3:] or ["androidboot.selinux=permissive"]

    with open(src, 'rb') as f:
        data = bytearray(f.read())

    if data[:8] != b'ANDROID!':
        print(f"✗ 不是 Android boot 镜像 (magic={bytes(data[:8])!r})")
        return 2

    kernel_size, = struct.unpack_from('<I', data, 8)
    ramdisk_size, = struct.unpack_from('<I', data, 16)
    page_size, = struct.unpack_from('<I', data, 36)
    header_version, = struct.unpack_from('<I', data, 40)

    print(f"源镜像      : {src}  ({len(data)} 字节)")
    print(f"kernel_size : {kernel_size}")
    print(f"ramdisk_size: {ramdisk_size}")
    print(f"page_size   : {page_size}")
    print(f"header_ver  : {header_version}")
    print()

    old = data[CMD_OFF:CMD_OFF + CMD_LEN].split(b'\x00')[0].decode('utf-8', 'replace')
    extra = data[EXTRA_OFF:EXTRA_OFF + EXTRA_LEN].split(b'\x00')[0].decode('utf-8', 'replace')

    print(f"原 cmdline       ({len(old)} 字节):")
    print(f"  {old}")
    print(f"原 extra_cmdline ({len(extra)} 字节):")
    print(f"  {extra or '(空)'}")
    print()

    # 追加到 cmdline
    new = old
    added = []
    for a in adds:
        if a in new or a in extra:
            print(f"· 已存在，跳过: {a}")
            continue
        new = (new + ' ' + a).strip()
        added.append(a)

    nb = new.encode('utf-8')
    if len(nb) >= CMD_LEN:
        print(f"✗ 新 cmdline 太长: {len(nb)} >= {CMD_LEN}")
        return 3

    print(f"新 cmdline       ({len(nb)} 字节, 剩余 {CMD_LEN - 1 - len(nb)} 字节):")
    print(f"  {new}")
    print(f"新增: {added}")
    print()

    data[CMD_OFF:CMD_OFF + CMD_LEN] = nb + b'\x00' * (CMD_LEN - len(nb))

    with open(dst, 'wb') as f:
        f.write(data)

    # 回读校验
    with open(dst, 'rb') as f:
        back = f.read()
    chk = back[CMD_OFF:CMD_OFF + CMD_LEN].split(b'\x00')[0].decode('utf-8', 'replace')
    ok = all(a in chk for a in adds)
    print(f"已写出: {dst} ({len(back)} 字节)")
    print(f"回读校验: {'✓ 通过' if ok else '✗ 失败'}")
    print(f"  {chk}")
    return 0 if ok else 4


if __name__ == '__main__':
    sys.exit(main())
