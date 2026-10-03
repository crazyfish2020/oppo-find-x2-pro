#!/bin/sh
# ============================================================================
# patch_netd_gate.sh —— NOP 掉 libnetd_updatable.so 里的三条内核版本硬门限
#
# 用法（作为 apex_rebuild.py 的 --hook 调用，参数就是解包出来的 APEX 文件树）:
#     /bin/sh patch_netd_gate.sh <apex_tree_dir>
#
# 背景
# ====
# 本机内核 4.19.157，而 Android 16（25Q2+）的 netd 侧 BPF 初始化要求内核 >= 5.4。
# 不满足时 BpfHandler::init() 直接返回一个 code=0x7FFFFFFF 的 Status，
# libnetd_updatable_init() 拿到后走 LOG(FATAL) → abort()。
# netd 被 OPPO Phoenix 判为 bootup critical service ⇒ 崩一次就整机重启 ⇒ boot loop。
#
# 实测日志（logcat）:
#   I NetdUpdatable: libnetd_updatable_init: Initializing
#   E NetdUpdatable: libnetd_updatable_init: Failed: (2147483647) 25Q2+ platform with kernel version < 5.4.0 is unsupported
#   F libc    : Fatal signal 6 (SIGABRT), code -1 (SI_QUEUE) in tid 3426 (netd), pid 3426 (netd)
#     #00 libc.so (abort+160)
#     #01 /apex/com.android.tethering/lib64/libnetd_updatable.so (libnetd_updatable_init.cfi+600)
#     #02 /system/bin/netd (main.cfi+228)
#
# 门限算法（反汇编 0x45f4 BpfHandler::init 得出）
# ==============================================
#   内核版本用 KERNEL_VERSION 风格编码：major<<24 | minor<<16 | sub<<8
#     w20 = 0x0408FFFF              = 4.9.0 - 1   （基址）
#     门限① 0x4948: w9 = w20 + 0x00050000 = 0x040DFFFF = 4.14.0 - 1
#     门限② 0x4b98: w9 = w20 + 0x000A0000 = 0x0412FFFF = 4.19.0 - 1
#     门限③ 0x4bc4: w9 = w20 + 0x00FB0000 = 0x0503FFFF = 5.4.0  - 1
#     比较：w8 = 实际内核版本(全局 0xd078)；cmp w8,w9；b.ls → 跳去构造错误 Status
#
#   本机 4.19.157 → 0x04139D00
#     门限① 0x04139D00 > 0x040DFFFF  通过（不跳）
#     门限② 0x04139D00 > 0x0412FFFF  通过（不跳）
#     门限③ 0x04139D00 <= 0x0503FFFF 命中 → SIGABRT   ★ 就是它
#
# 修法
# ====
#   把三处 `b.ls <错误分支>` 全部改成 NOP（0xD503201F）。
#   ①②本来就不跳，改 NOP 行为等价（只是不再依赖版本探测结果，更稳）；
#   ③改 NOP 后顺序执行到 0x4bc8（正常的 open(cg2_path, O_DIRECTORY|O_CLOEXEC) 流程）。
#
#   ⚠ 这是「绕过」而不是「修复」：25Q2 的 netd 在 4.19 内核上确实缺一些 BPF 能力，
#     后续 initMaps / attachProgramToCgroup 仍可能失败——但那些失败会走正常
#     错误返回路径（netd 打日志、返回错误码），不会 abort 拖垮整机。
#
# 注意
# ====
#   * .text 段在文件里的偏移 == 虚拟地址（都是 0x4000 起），所以下面的常量
#     既是 vaddr 也是 file offset。脚本里会先用原字节自检，不匹配就报错退出。
#   * 只改 4 字节指令，不增删任何字节 ⇒ 文件大小/段表/重定位全部不变。
# ============================================================================
set -e

TREE="$1"
if [ -z "$TREE" ]; then
    echo "用法: $0 <apex_tree_dir>" >&2
    exit 2
fi

PY=""
for c in /Users/skeletondie/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 python3 /usr/bin/python3; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "找不到 python3" >&2; exit 1; }

FOUND=0
for SO in "$TREE/lib64/libnetd_updatable.so" "$TREE/lib/libnetd_updatable.so"; do
    [ -f "$SO" ] || continue
    FOUND=1
    echo "补丁目标: $SO"
    "$PY" - "$SO" <<'PYEOF'
import struct, sys

PATH = sys.argv[1]

# (file offset == vaddr) : 原始指令编码
SITES = {
    0x4948: (0x54005009, "b.ls 0x5348  → \"U+ platform with kernel version < 4.14.0 is unsupported\""),
    0x4b98: (0x54003e29, "b.ls 0x535c  → \"V+ platform with kernel version < 4.19.0 is unsupported\""),
    0x4bc4: (0x540043a9, "b.ls 0x5438  → \"25Q2+ platform with kernel version < 5.4.0 is unsupported\""),
}
NOP = 0xd503201f

with open(PATH, "r+b") as f:
    for off, (expect, desc) in sorted(SITES.items()):
        f.seek(off)
        raw = f.read(4)
        if len(raw) != 4:
            raise SystemExit("  读取 %#x 失败" % off)
        cur, = struct.unpack("<I", raw)
        if cur == NOP:
            print("  = %#06x 已是 NOP，跳过" % off)
            continue
        if cur != expect:
            raise SystemExit(
                "  ✗ %#x 原字节不符：实际 %#010x，期望 %#010x\n"
                "    （可能这份 .so 已被改过或版本不同，拒绝继续）" % (off, cur, expect))
        f.seek(off)
        f.write(struct.pack("<I", NOP))
        print("  ✓ %#06x  %#010x -> NOP   %s" % (off, cur, desc))

    # 回读自检
    bad = []
    for off, (expect, desc) in SITES.items():
        f.seek(off)
        cur, = struct.unpack("<I", f.read(4))
        if cur != NOP:
            bad.append(off)
    if bad:
        raise SystemExit("  ✗ 回读校验失败: %s" % [hex(b) for b in bad])
    print("  回读校验: 3/3 均为 NOP ✓")
PYEOF
done

if [ "$FOUND" = "0" ]; then
    echo "树里找不到 libnetd_updatable.so（$TREE）" >&2
    exit 1
fi

echo "补丁完成。"
