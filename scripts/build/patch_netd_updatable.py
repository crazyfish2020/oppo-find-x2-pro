#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patch_netd_updatable.py —— 去掉 libnetd_updatable.so 的「内核 < 5.4.0」硬检查

背景
----
A16 的 `libnetd_updatable_init` 里有三级内核版本检查（`isKernelVersionAtLeast`）：

    @0x51b8  isKernelVersionAtLeast(5, 10)   4.19 → false ⇒ tbz 跳 0x51e4（正常路径）✓
    @0x51f8  isKernelVersionAtLeast(4, 19)   4.19 → true  ⇒ tbz 不跳（正常路径）   ✓
    @0x52e0  isKernelVersionAtLeast(5,  4)   4.19 → false ⇒ tbz 跳 0x5330（★错误路径）✗

最后一条在 4.19 内核上必然失败，netd 直接 abort：

    E NetdUpdatable: libnetd_updatable_init: Failed: (2147483647)
                     25Q2+ platform with kernel version < 5.4.0 is unsupported
    F libc: Fatal signal 6 (SIGABRT) ... (netd)

⇒ zygote 反复重启、`sys.boot_completed` 永不置位（开机动画无限循环）。

修法
----
把 0x52e4 的 `tbz w0, #0, #0x5330` 改成 `nop`，
使「内核 < 5.4」时不再跳入错误路径，继续执行 0x52e8 的正常流程。
另外两处检查的跳转方向本来就是对的，**保持原样不动**。

指令编码（AArch64，小端）:
    tbz w0, #0, #0x5330  →  60 02 00 36
    nop                  →  1f 20 03 d5

用法: patch_netd_updatable.py <in.so> <out.so>
"""
import sys

PATCH_OFF = 0x52E4
OLD = bytes.fromhex('60020036')          # tbz w0, #0, #0x5330
NEW = bytes.fromhex('1f2003d5')          # nop

# 另外两处检查：仅用于校验，不修改
KEEP = [
    (0x51BC, '40010036', 'isKernelVersionAtLeast(5,10) false 时跳 0x51e4（正常）'),
    (0x51FC, 'e0060036', 'isKernelVersionAtLeast(4,19) true  时不跳（正常）'),
]


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    src, dst = sys.argv[1], sys.argv[2]
    with open(src, 'rb') as f:
        d = bytearray(f.read())

    print(f"源  : {src} ({len(d)} 字节)")
    got = bytes(d[PATCH_OFF:PATCH_OFF + 4])
    print(f"  0x{PATCH_OFF:x}: {got.hex()}   期望 {OLD.hex()} (tbz w0,#0,#0x5330)")
    if got != OLD:
        print("✗ 字节不匹配，拒绝 patch（可能不是同一版本）")
        return 3

    d[PATCH_OFF:PATCH_OFF + 4] = NEW
    with open(dst, 'wb') as f:
        f.write(d)

    with open(dst, 'rb') as f:
        back = f.read()

    print(f"已写出: {dst}")
    print(f"  0x{PATCH_OFF:x}: {bytes(back[PATCH_OFF:PATCH_OFF + 4]).hex()}   (nop) ✓")
    print("  其余两处检查保持不变：")
    bad = 0
    for off, want, why in KEEP:
        cur = bytes(back[off:off + 4]).hex()
        if cur == want:
            print(f"    ✓ 0x{off:x}: {cur}  ({why})")
        else:
            print(f"    ✗ 0x{off:x}: {cur}  期望 {want}  ({why})")
            bad += 1
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
