#!/usr/bin/env python3
"""AArch64 ELF 调用图分析：定位函数边界、解析符号、构建调用关系。

用法:
  elf_callgraph.py <elf> symbols                      列出动态符号（函数）
  elf_callgraph.py <elf> calls <符号名>                列出该符号的所有调用点及其所在函数
  elf_callgraph.py <elf> func <地址>                  列出包含该地址的函数区间与全部调用
依赖: capstone (venv)
"""
import sys
import struct
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN


def parse_elf(d):
    e_shoff = struct.unpack_from('<Q', d, 0x28)[0]
    e_shentsize = struct.unpack_from('<H', d, 0x3a)[0]
    e_shnum = struct.unpack_from('<H', d, 0x3c)[0]
    e_shstrndx = struct.unpack_from('<H', d, 0x3e)[0]
    e_entry = struct.unpack_from('<Q', d, 0x18)[0]
    shstr = struct.unpack_from('<IIQQQQIIQQ', d, e_shoff + e_shstrndx * e_shentsize)
    st = shstr[4]
    secs = {}
    for i in range(e_shnum):
        s = struct.unpack_from('<IIQQQQIIQQ', d, e_shoff + i * e_shentsize)
        name = d[st + s[0]:d.index(b'\0', st + s[0])].decode()
        secs[name] = {'addr': s[3], 'off': s[4], 'size': s[5], 'link': s[6], 'entsize': s[9]}
    return secs, e_entry


def parse_dynsym(d, secs):
    """返回 {符号名: 地址}（只保留 FUNC 类型）"""
    ds, st = secs['.dynsym'], secs['.dynstr']
    n = ds['size'] // 24
    out = {}
    for i in range(n):
        o = ds['off'] + i * 24
        nameoff, info, other, shndx, value, size = struct.unpack_from('<IBBHQQ', d, o)
        if (info & 0xf) != 2:      # STT_FUNC
            continue
        if value == 0:
            continue
        nm = d[st['off'] + nameoff:d.index(b'\0', st['off'] + nameoff)].decode()
        if nm:
            out[nm] = value
    return out


def get_code(d, secs):
    t = secs['.text']
    return d[t['off']:t['off'] + t['size']], t['addr']


def disasm(d, secs):
    code, base = get_code(d, secs)
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    return list(md.disasm(code, base)), base


def find_func_bounds(insns, base):
    """用 stp x29,x30 / paciasp 之类的前导识别函数起点，返回 [(start, end)]"""
    starts = []
    for i, ins in enumerate(insns):
        if ins.mnemonic in ('paciasp',):
            starts.append(ins.address)
        elif ins.mnemonic == 'stp' and 'x29, x30' in ins.op_str:
            starts.append(ins.address)
        elif ins.mnemonic == 'sub' and ins.op_str.startswith('sp, sp, #') and i > 0:
            pass
    starts = sorted(set(starts))
    bounds = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else base + len(insns) * 4
        bounds.append((s, e))
    return bounds


def owner(bounds, addr):
    for s, e in bounds:
        if s <= addr < e:
            return s
    return None


def main():
    path = sys.argv[1]
    mode = sys.argv[2]
    d = open(path, 'rb').read()
    secs, entry = parse_elf(d)
    syms = parse_dynsym(d, secs)
    insns, base = disasm(d, secs)

    if mode == 'symbols':
        print(f'e_entry = {entry:#x}')
        print(f'共 {len(syms)} 个函数符号:')
        for nm, a in sorted(syms.items(), key=lambda x: x[1]):
            print(f'  {a:#08x}  {nm}')
        return

    target_name = sys.argv[3]
    if target_name.startswith('0x'):
        target = int(target_name, 16)
    else:
        # 支持子串匹配
        cands = [(nm, a) for nm, a in syms.items() if target_name in nm]
        if not cands:
            print(f'未找到符号: {target_name}')
            return
        for nm, a in cands:
            print(f'匹配符号: {a:#08x}  {nm}')
        target = cands[0][1]

    bounds = find_func_bounds(insns, base)
    callers = []
    for ins in insns:
        if ins.mnemonic in ('bl', 'b') and ins.op_str.startswith('#'):
            try:
                t = int(ins.op_str[1:], 16)
            except ValueError:
                continue
            if t == target:
                callers.append(ins)

    print(f'\n=== 调用 {target:#x} 的位置（{len(callers)} 处）===')
    for ins in callers:
        o = owner(bounds, ins.address)
        print(f'  call @ {ins.address:#08x}   所在函数起始 {o:#08x}' if o else
              f'  call @ {ins.address:#08x}   (未识别函数边界)')

    if mode == 'func':
        o = owner(bounds, target)
        if o:
            e = dict(bounds)[o] if o in dict(bounds) else None
            print(f'\n=== 包含 {target:#x} 的函数: {o:#x} ~ {e:#x} ===')
            # 列出该函数内所有调用
            for ins in insns:
                if o <= ins.address < e and ins.mnemonic in ('bl', 'b') and ins.op_str.startswith('#'):
                    t = int(ins.op_str[1:], 16)
                    nm = next((k for k, v in syms.items() if v == t), '')
                    print(f'  {ins.address:#08x}  {ins.mnemonic:<4} {ins.op_str:<14} {nm}')


if __name__ == '__main__':
    main()
