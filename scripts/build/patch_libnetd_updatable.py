#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""patch_libnetd_updatable.py —— 绕过 A16(25Q2) 的「内核必须 >= 5.4」硬门限。

背景
----
tethering APEX 的 lib64/libnetd_updatable.so 里，BpfHandler::init() -> initPrograms()
有一道硬门限（源码 packages/modules/Connectivity/bpf/netd/BpfHandler.cpp:104-107）：

    // 25Q2 bumps the kernel requirement up to 5.4
    if (isAtLeast25Q2 && !isAtLeastKernelVersion(5, 4, 0)) {
        return Status("25Q2+ platform with kernel version < 5.4.0 is unsupported");
    }

本机平台 = 25Q2 (SDK 36)，内核 = 4.19.157 (编码 0x04139D00) < 5.4.0 (0x05040000)
⇒ 必然命中 ⇒ BpfHandler::init 返回错误 ⇒ libnetd_updatable_init 里
   LOG(ERROR) << "libnetd_updatable_init: Failed: (...) " << ret.msg(); abort();
⇒ netd SIGABRT，被 OPPO Phoenix 判为 bootup critical ⇒ 无限重启。

反汇编实证（VA，与文件偏移一致，本 .so 首个 PT_LOAD 的 vaddr=0）：
    0x4b70: adrp x22, #0xd000
    0x4b74: ldrb w8, [x22, #0x74]      ; isAtLeast25Q2 标志（0xd074）
    0x4b78: cmp  w8, #1
    0x4b7c: b.ne #0x4b9c               ; 非 25Q2 ⇒ 跳过本门限
    0x4b80: ldarb w8, [x23]            ; 内核版本是否已计算
    0x4b84: tbz  w8, #0, #0x57e4       ; 未计算 ⇒ 去 0x57e4 惰性计算
    0x4b88: adrp x8, #0xd000
    0x4b8c: add  w9, w20, #0xa0, lsl #12   ; 4.19 门槛（V+ 检查用）
    0x4b90: ldr  w8, [x8, #0x78]       ; 内核版本编码（0xd078）
    0x4b94: cmp  w8, w9
    0x4b98: b.ls #0x535c               ; ⇒ "V+ platform with kernel version < 4.19.0..."
    0x4b9c: adrp x8, #0xd000
    0x4ba0: ldrb w8, [x8, #0x68]       ; isAtLeastV 标志（0xd068）
    0x4ba4: cmp  w8, #1
    0x4ba8: b.ne #0x4bc8
    0x4bac: ldarb w8, [x23]
    0x4bb0: adrp x19, #0xd000
    0x4bb4: tbz  w8, #0, #0x581c
    0x4bb8: ldr  w8, [x19, #0x78]      ; 内核版本编码
    0x4bbc: add  w9, w20, #0xfb0, lsl #12  ; 0x04090000 + 0xFB0000 = 0x05040000 = 5.4.0
    0x4bc0: cmp  w8, w9
    0x4bc4: b.ls #0x5438               ; ★★ 命中点：kver <= 5.4.0 ⇒ 报 25Q2 门限错误
    0x4bc8: mov  x0, x1                ; ← 正常继续（open(cg2_path) 等）

补丁
----
0x4bc4  b.ls #0x5438   (a9 43 00 54)  →  nop  (1f 20 03 d5)
⇒ 无条件落空，等价于「内核 >= 5.4」成立 ⇒ 跳过该门限。

说明：这是保守策略而非能力不足 —— A16 tethering APEX 的 BPF 对象自带 `$4_19` 变体
（见 2026old/LineageOS-2026/04-取证归档/根因-netbpfload-25Q2内核门限.md §5），
4.19 是被正式支持的。

用法:
  patch_libnetd_updatable.py <输入.so> [输出.so]
"""
import hashlib
import os
import sys

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN

PATCHES = [
    (0x4BC4, bytes.fromhex('a9430054'), bytes.fromhex('1f2003d5'),
     'b.ls #0x5438  →  nop   (绕过 "25Q2+ platform with kernel version < 5.4.0")'),
]


def md5(p):
    h = hashlib.md5()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def disasm(data, va, n=6):
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    return list(md.disasm(data[va:va + n * 4], va))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else src.replace('.so', '.patched.so')
    print('=== 1) 源文件 ===')
    print(f'  {src}')
    print(f'  大小 {os.path.getsize(src):,} 字节   md5 {md5(src)}')

    data = bytearray(open(src, 'rb').read())

    print('\n=== 2) 改前校验 ===')
    ok = True
    for off, old, new, desc in PATCHES:
        cur = bytes(data[off:off + len(old)])
        good = cur == old
        ok &= good
        print(f'  {off:#x}: 期望 {old.hex()}  实际 {cur.hex()}  {"✅" if good else "❌"}')
        print(f'         {desc}')
    if not ok:
        print('\n⚠️ 源文件与预期不符（可能是不同版本的 .so），中止，不写任何补丁。')
        sys.exit(1)

    print('\n=== 3) 改前反汇编 ===')
    for ins in disasm(bytes(data), 0x4bb8, 5):
        print(f'  {ins.address:#08x}: {ins.bytes.hex():<8} {ins.mnemonic:<8} {ins.op_str}')

    print('\n=== 4) 写入补丁 ===')
    for off, old, new, desc in PATCHES:
        data[off:off + len(new)] = new
        print(f'  {off:#x}: {old.hex()} → {new.hex()}')

    open(dst, 'wb').write(bytes(data))

    print('\n=== 5) 改后反汇编复核 ===')
    patched = open(dst, 'rb').read()
    for ins in disasm(patched, 0x4bb8, 5):
        print(f'  {ins.address:#08x}: {ins.bytes.hex():<8} {ins.mnemonic:<8} {ins.op_str}')

    print('\n=== 6) 产物 ===')
    print(f'  {dst}')
    print(f'  大小 {os.path.getsize(dst):,} 字节   md5 {md5(dst)}')

    a = open(src, 'rb').read()
    diff = [i for i in range(len(a)) if a[i] != patched[i]]
    expect = set(range(0x4BC4, 0x4BC4 + 4))
    if set(diff) == expect:
        print('  ✅ 仅 0x4bc4~0x4bc7 四字节被修改，无副作用')
    else:
        print(f'  ❌ 意外修改: {[hex(i) for i in diff][:20]}')
        sys.exit(1)


if __name__ == '__main__':
    main()
