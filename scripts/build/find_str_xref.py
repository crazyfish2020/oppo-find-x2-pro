#!/usr/bin/env python3
"""定位 AArch64 二进制中某字符串的引用点，并反汇编其上下文。

用法: find_str_xref.py <elf> <substring> [上下文指令数]
依赖: capstone (venv: /Users/skeletondie/.workbuddy-ai/binaries/python/envs/default)
"""
import sys
import struct
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
from capstone.arm64 import ARM64_OP_IMM, ARM64_OP_REG, ARM64_OP_MEM


def load_sections(d):
    e_shoff = struct.unpack_from('<Q', d, 0x28)[0]
    e_shentsize = struct.unpack_from('<H', d, 0x3a)[0]
    e_shnum = struct.unpack_from('<H', d, 0x3c)[0]
    e_shstrndx = struct.unpack_from('<H', d, 0x3e)[0]
    shstr = struct.unpack_from('<IIQQQQIIQQ', d, e_shoff + e_shstrndx * e_shentsize)
    strtab_off = shstr[4]
    secs = []
    for i in range(e_shnum):
        s = struct.unpack_from('<IIQQQQIIQQ', d, e_shoff + i * e_shentsize)
        end = d.index(b'\0', strtab_off + s[0])
        name = d[strtab_off + s[0]:end].decode()
        secs.append({'name': name, 'addr': s[3], 'off': s[4], 'size': s[5]})
    return secs


def off_to_vaddr(secs, off):
    for s in secs:
        if s['off'] <= off < s['off'] + s['size']:
            return s['addr'] + (off - s['off']), s['name']
    return None, None


def main():
    path, needle = sys.argv[1], sys.argv[2].encode()
    ctx = int(sys.argv[3]) if len(sys.argv) > 3 else 12
    d = open(path, 'rb').read()
    secs = load_sections(d)

    # 1) 找字符串
    hits = []
    start = 0
    while True:
        i = d.find(needle, start)
        if i < 0:
            break
        va, sec = off_to_vaddr(secs, i)
        hits.append((i, va, sec))
        start = i + 1
    if not hits:
        print(f'未找到字符串: {needle!r}')
        return
    print(f'=== 字符串 {needle!r} 出现 {len(hits)} 次 ===')
    for off, va, sec in hits:
        print(f'  文件偏移 {off:#x}  →  虚拟地址 {va:#x}  (节 {sec})')

    text = next(s for s in secs if s['name'] == '.text')
    code = d[text['off']:text['off'] + text['size']]
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    md.detail = True
    insns = list(md.disasm(code, text['addr']))

    # 2) 扫描 ADRP + ADD 组合，解析目标地址
    refs = []
    for idx, ins in enumerate(insns):
        if ins.mnemonic != 'adrp':
            continue
        try:
            page = ins.operands[1].imm
        except Exception:
            continue
        # 往后看 4 条指令找 ADD imm
        for j in range(idx + 1, min(idx + 5, len(insns))):
            nxt = insns[j]
            if nxt.mnemonic == 'add' and len(nxt.operands) == 3:
                o2 = nxt.operands[2]
                if o2.type == ARM64_OP_IMM:
                    tgt = page + o2.imm
                    for off, va, sec in hits:
                        if tgt == va:
                            refs.append((ins.address, nxt.address, tgt))
            if nxt.mnemonic in ('adrp', 'ret', 'b', 'bl'):
                break

    if not refs:
        print('\n未在 .text 中找到 ADRP+ADD 引用（可能用了 GOT/字面量池）')
    else:
        print(f'\n=== 找到 {len(refs)} 处引用 ===')
        for a, b, tgt in refs:
            print(f'\n--- 引用 @ {a:#x} (add @ {b:#x}) → {tgt:#x} ---')
            lo = max(0, insns.index(next(x for x in insns if x.address == a)) - ctx)
            hi = lo + ctx * 2 + 4
            for ins in insns[lo:hi]:
                mark = '  <<<' if ins.address in (a, b) else ''
                print(f'  {ins.address:#08x}: {ins.bytes.hex():<8} {ins.mnemonic:<8} {ins.op_str}{mark}')


if __name__ == '__main__':
    main()
