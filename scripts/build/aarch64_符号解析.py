#!/usr/bin/env python3
# 解析 libstagefright.so 中地址对应的符号
from elftools.elf.elffile import ELFFile

SO = "/tmp/libstagefright.so"
f = open(SO, "rb")
elf = ELFFile(f)

syms = []
for secname in (".dynsym", ".symtab"):
    sec = elf.get_section_by_name(secname)
    if sec is None:
        continue
    for s in sec.iter_symbols():
        if s["st_value"] and s["st_info"]["type"] in ("STT_FUNC", "STT_OBJECT", "STT_NOTYPE"):
            syms.append((s["st_value"], s["st_size"], s.name, secname))
syms.sort()

def resolve(addr):
    best = None
    for v, size, name, sec in syms:
        if v <= addr and (best is None or v > best[0]):
            best = (v, size, name, sec)
    if best is None:
        return "??"
    v, size, name, sec = best
    if size and addr >= v + size:
        return f"{name}+{addr-v:#x} (?)"
    return f"{name}+{addr-v:#x}"

print("== 关键调用点解析 ==")
for a in (0x20ab90, 0x208898, 0x2088e0, 0x0d265c):
    print(f"  {a:#x} -> {resolve(a)}")

print("\n== 0xd265c 所在函数附近符号 ==")
for v, size, name, sec in syms:
    if 0xd0000 <= v <= 0xd4000:
        print(f"  {v:#x} size={size:#x} {name}")
f.close()
