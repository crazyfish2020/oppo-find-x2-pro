#!/usr/bin/env python3
# 定位 libstagefright.so 中字符串的引用点与上下文
import sys
from elftools.elf.elffile import ELFFile
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
from capstone.arm64 import ARM64_OP_REG, ARM64_OP_IMM

SO = "/tmp/libstagefright.so"
NEEDLE = b"Unexpected nullptr for codec information"

f = open(SO, "rb")
elf = ELFFile(f)

# 1. 找字符串
target_va = None
for sec in elf.iter_sections():
    if sec["sh_type"] != "SHT_PROGBITS":
        continue
    try:
        data = sec.data()
    except Exception:
        continue
    off = data.find(NEEDLE)
    if off >= 0:
        target_va = sec["sh_addr"] + off
        print(f"[str] section={sec.name} fileoff={off:#x} va={target_va:#x}")
        break

if target_va is None:
    print("string not found")
    sys.exit(1)

# 2. 附近字符串（同 section）
sec = elf.get_section_by_name(".rodata") or None
def all_strings(sec, lo, hi):
    data = sec.data()
    base = sec["sh_addr"]
    out = []
    cur = b""
    start = 0
    for i, b in enumerate(data):
        if 0x20 <= b < 0x7f:
            if not cur:
                start = i
            cur += bytes([b])
        else:
            if len(cur) >= 6:
                out.append((base + start, cur.decode()))
            cur = b""
    return [(a, s) for a, s in out if lo <= a <= hi]

print("\n=== 邻近字符串 ===")
for a, s in all_strings(sec, target_va - 0x400, target_va + 0x400):
    mark = "  <<<" if a == target_va else ""
    print(f"{a:#x}  {s[:110]}{mark}")

# 3. 找 ADRP+ADD 引用
text = elf.get_section_by_name(".text")
tdata = text.data()
taddr = text["sh_addr"]
md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
md.detail = True

page = target_va & ~0xFFF
offs = target_va & 0xFFF

hits = []
insns = list(md.disasm(tdata, taddr))
for i, ins in enumerate(insns):
    if ins.mnemonic != "adrp":
        continue
    if len(ins.operands) != 2 or ins.operands[1].type != ARM64_OP_IMM:
        continue
    if ins.operands[1].imm != page:
        continue
    # 看后面 1-3 条
    for j in range(i + 1, min(i + 4, len(insns))):
        nxt = insns[j]
        if nxt.mnemonic in ("add", "ldr") and len(nxt.operands) == 3:
            if nxt.operands[0].type == ARM64_OP_REG and nxt.operands[1].type == ARM64_OP_REG:
                if nxt.operands[1].reg != ins.operands[0].reg:
                    continue
                if nxt.operands[2].type == ARM64_OP_IMM and nxt.operands[2].imm == offs:
                    hits.append(i)
                elif nxt.mnemonic == "ldr" and nxt.operands[2].type != ARM64_OP_IMM:
                    # 可能是 [reg, imm]
                    pass
                break
            break

print(f"\n=== 引用点 {len(hits)} 处 ===")
for i in hits:
    print(f"\n--- 引用 @ {insns[i].address:#x} ---")
    for ins in insns[max(0, i - 14):i + 8]:
        print(f"  {ins.address:#010x}: {ins.mnemonic:8s} {ins.op_str}")
f.close()
