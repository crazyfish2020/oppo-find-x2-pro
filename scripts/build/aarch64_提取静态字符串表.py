#!/usr/bin/env python3
# 完整提取静态初始化函数中引用的所有字符串（找出 c2 XML 名字列表）
from elftools.elf.elffile import ELFFile
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
from capstone.arm64 import ARM64_OP_REG, ARM64_OP_IMM, ARM64_OP_MEM

SO = "/tmp/ccodec.so"
f = open(SO, "rb")
elf = ELFFile(f)

secs = [(s["sh_offset"], s["sh_offset"] + s["sh_size"], s["sh_addr"], s.name)
        for s in elf.iter_sections() if s["sh_type"] != "SHT_NOBITS"]

def va2off(va):
    for lo, hi, base, name in secs:
        if base <= va < base + (hi - lo):
            return lo + (va - base), name
    return None, None

def read_str(va):
    off, sec = va2off(va)
    if off is None:
        return None
    f.seek(off)
    buf = f.read(160)
    end = buf.find(b"\x00")
    if end < 0:
        return None
    try:
        return buf[:end].decode()
    except UnicodeDecodeError:
        return None

text = elf.get_section_by_name(".text")
tdata, taddr = text.data(), text["sh_addr"]
md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
md.detail = True

# 目标函数范围（静态初始化，0xe0000-0xe1500）
LO, HI = 0xE0000, 0xE1500
insns = list(md.disasm(tdata[LO - taddr:HI - taddr], LO))

found = {}
for i, ins in enumerate(insns):
    ops = ins.operands
    # ADRP + ADD / LDR
    if ins.mnemonic == "adrp" and len(ops) == 2 and ops[1].type == ARM64_OP_IMM:
        page = ops[1].imm
        for j in range(i + 1, min(i + 4, len(insns))):
            n = insns[j]
            if n.mnemonic in ("add", "ldr") and len(n.operands) >= 3 \
               and n.operands[1].type == ARM64_OP_REG and n.operands[1].reg == ops[0].reg \
               and n.operands[2].type == ARM64_OP_IMM:
                va = page + n.operands[2].imm
                s = read_str(va)
                if s and len(s) > 3:
                    found.setdefault(va, (s, n.address))
                break
            if n.mnemonic not in ("add", "ldr"):
                break

print(f"共 {len(found)} 个字符串引用\n")
for va, (s, at) in sorted(found.items()):
    if any(k in s for k in ("xml", "media_codecs", "/etc", "variant", "ro.", "debug.", "vendor")):
        print(f"{at:#08x} -> {va:#08x}  {s}")
