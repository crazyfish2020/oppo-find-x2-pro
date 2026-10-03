#!/usr/bin/env python3
"""对 8T A16 的 netbpfload 打【内核门限】补丁。

背景（2026-10-02 反汇编定位，见 05-分析/根因-netbpfload-419-236硬门限.md）
--------------------------------------------------------------------------
netbpfload 是 ColorOS16(A16/25Q2) 的 BPF 加载器，它在加载 BPF 对象前做三道
「平台内核版本」硬门限检查（全部是 ALOGx + 直接退出，退出后 /sys/fs/bpf 为空）：

  G1  Android S & T require kernel 4.9.      (0xbdac → 0xc0b4)   4.19 ✓ 通过
  G2  Android U requires kernel 4.14.        (0xbdd8 → 0xc0c8)   4.19 ✓ 通过
  G3  Android 25Q2 requires kernel 5.4.      (0xbdfc → 0xc0dc)   4.19 ✗ 【需绕】
  G4  Android V+ only supports LTS kernels.  (0xbe1c → 0xbe38)   待绕
  G5  Android V+ requires 4.19 kernel to be 4.19.236+.
                                             (0xbe5c → 0xbe90)   4.19.157 ✗ 【需绕】

G5 是关键：本机内核 4.19.157 < 4.19.236，而供体 OnePlus 8T 是 4.19.325 ≥ 4.19.236
—— 这正是「同 SoC 供体正常、本机 netd SIGABRT 开机循环」的决定性差异。

补丁（三处，各 4 字节）
--------------------------------------------------------------------------
  0xbdfc  tbz   w0, #0, #0xc0dc   00 17 00 36  →  nop            1f 20 03 d5
          （无条件落到 0xbe00，越过 "25Q2 requires kernel 5.4."）
  0xbe1c  tbnz  w0, #0, #0xbe38   e0 00 00 37  →  b #0xbe38      07 00 00 14
          （无条件跳过 "V+ only supports LTS kernels." 的报错退出）
  0xbe5c  tbnz  w0, #0, #0xbe90   a0 01 00 37  →  b #0xbe90      0d 00 00 14
          （无条件跳过 "V+ requires 4.19 kernel to be 4.19.236+." 的报错退出）

  说明：G4/G5 改成无条件 `b <正常继续>` 而不是 nop，因为原指令是
        「条件成立就跳到正常路径」，nop 会掉进报错分支（方向相反）。

用法
--------------------------------------------------------------------------
  python3 patch_netbpfload.py            # 写 .../donor_a16/netbpfload.patched
  python3 patch_netbpfload.py <源> <目标>
"""
import hashlib
import os
import sys

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN

HOME = os.path.expanduser('~')
DEF_SRC = f'{HOME}/Documents/oppo/2026-安卓16/02-移植素材/donor_a16/netbpfload'
DEF_DST = f'{HOME}/Documents/oppo/2026-安卓16/07-重建/新增文件/netbpfload'

# (文件偏移, 原字节, 新字节, 说明)
PATCHES = [
    (0xbdfc, bytes.fromhex('00170036'), bytes.fromhex('1f2003d5'),
     'G3 tbz  w0,#0,#0xc0dc  →  nop        (越过 "25Q2 requires kernel 5.4.")'),
    (0xbe1c, bytes.fromhex('e0000037'), bytes.fromhex('07000014'),
     'G4 tbnz w0,#0,#0xbe38  →  b #0xbe38  (越过 "V+ only supports LTS kernels.")'),
    (0xbe5c, bytes.fromhex('a0010037'), bytes.fromhex('0d000014'),
     'G5 tbnz w0,#0,#0xbe90  →  b #0xbe90  (越过 "V+ requires 4.19.236+.")'),
]

CHECKS = [  # 改后反汇编窗口
    (0xbdf8, 4),
    (0xbe14, 4),
    (0xbe54, 4),
]


def md5(p):
    h = hashlib.md5()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def disasm(data, va, n):
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    return list(md.disasm(data[va:va + n * 4], va))


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else DEF_SRC
    dst = sys.argv[2] if len(sys.argv) > 2 else DEF_DST

    print('=== 1) 源文件 ===')
    print(f'  {src}')
    print(f'  大小 {os.path.getsize(src):,} 字节   md5 {md5(src)}')

    orig = open(src, 'rb').read()
    data = bytearray(orig)

    print('\n=== 2) 改前校验（3 处）===')
    bad = False
    for off, old, new, desc in PATCHES:
        cur = bytes(data[off:off + len(old)])
        if cur == old:
            print(f'  {off:#x}: 期望 {old.hex()}  ✅ 匹配')
        else:
            print(f'  {off:#x}: 期望 {old.hex()}  ❌ 实际 {cur.hex()}')
            bad = True
    if bad:
        print('  ⚠️ 源文件与预期不符，中止，不写任何补丁。')
        sys.exit(1)

    print('\n=== 3) 写入补丁 ===')
    for off, old, new, desc in PATCHES:
        data[off:off + len(new)] = new
        print(f'  {off:#x}: {old.hex()} → {new.hex()}')
        print(f'          {desc}')

    os.makedirs(os.path.dirname(dst), exist_ok=True)
    open(dst, 'wb').write(bytes(data))

    print('\n=== 4) 改后反汇编复核 ===')
    patched = open(dst, 'rb').read()
    for va, n in CHECKS:
        for ins in disasm(patched, va, n):
            print(f'  0x{ins.address:06x}: {ins.bytes.hex():<10} {ins.mnemonic:<9} {ins.op_str}')
        print('  ' + '-' * 46)

    print('\n=== 5) 产物 ===')
    print(f'  {dst}')
    print(f'  大小 {os.path.getsize(dst):,} 字节   md5 {md5(dst)}')

    diff = [i for i in range(len(orig)) if orig[i] != patched[i]]
    expect = set()
    for off, old, new, _ in PATCHES:
        expect |= set(range(off, off + len(new)))
    print(f'  差异字节数: {len(diff)}')
    if set(diff) <= expect:
        print(f'  ✅ 修改范围限于预期的 {len(expect)} 字节窗口内，无副作用'
              f'（其中 {len(expect) - len(diff)} 字节新值与旧值巧合相同）')
    else:
        print(f'  ❌ 意外修改: {[hex(i) for i in diff if i not in expect][:20]}')
        sys.exit(1)


if __name__ == '__main__':
    main()
