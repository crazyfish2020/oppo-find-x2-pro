#!/bin/sh
# ==========================================================================
# hook_tethering_patch.sh —— apex_rebuild.py 的 --hook 脚本
#
# 在「解包后 / 重建前」对 tethering APEX 的文件树打内核版本门限补丁。
# 调用方式:  /bin/sh hook_tethering_patch.sh <tree>
#
# 背景
# ----
# A16(25Q2) 有两处硬编码「内核必须 >= 5.4」的门限，本机内核 4.19.157 必然命中：
#
#  1) lib64/libnetd_updatable.so
#     源码 packages/modules/Connectivity/bpf/netd/BpfHandler.cpp:104-107
#         if (isAtLeast25Q2 && !isAtLeastKernelVersion(5, 4, 0))
#             return Status("25Q2+ platform with kernel version < 5.4.0 is unsupported");
#     命中后 libnetd_updatable_init() 里 LOG(ERROR)+abort()
#     ⇒ netd SIGABRT ⇒ OPPO Phoenix 判为 bootup critical ⇒ 无限重启。
#     补丁点 VA/文件偏移 0x4bc4:  b.ls #0x5438 (a9 43 00 54) -> nop (1f 20 03 d5)
#
#  2) bin/netbpfload
#     补丁点 0xbdfc:  tbz w0, #0, #0xc0dc (00 17 00 36) -> nop (1f 20 03 d5)
#     绕过 "Android 25Q2 requires kernel 5.4."
#
# 说明：这是 Google 的保守策略而非能力不足 —— A16 tethering APEX 的 BPF 对象
# 自带 `$4_19` 变体（netd.o 有 $4_19/$4_9，clatd.o 有 $4_14/$4_9），4.19 被正式支持。
# ==========================================================================
set -e

TREE="$1"
if [ -z "$TREE" ] || [ ! -d "$TREE" ]; then
    echo "hook: 用法: $0 <tree目录>" >&2
    exit 1
fi

# patch4 <file> <hexoffset> <expect_hex> <new_printf_octal> <desc>
patch4() {
    _f="$1"; _off="$2"; _exp="$3"; _new="$4"; _desc="$5"
    if [ ! -f "$_f" ]; then
        echo "hook: 缺文件 $_f" >&2
        exit 1
    fi
    _cur=$(xxd -p -l 4 -s "$_off" "$_f")
    if [ "$_cur" != "$_exp" ]; then
        echo "hook: $_f @$_off 期望 $_exp 实际 $_cur —— 不匹配，中止（不写任何补丁）" >&2
        exit 1
    fi
    printf "$_new" | dd of="$_f" bs=1 seek="$_off" conv=notrunc 2>/dev/null
    _after=$(xxd -p -l 4 -s "$_off" "$_f")
    if [ "$_after" != "1f2003d5" ]; then
        echo "hook: $_f @$_off 写入失败（实际 $_after）" >&2
        exit 1
    fi
    echo "hook: [OK] $_desc"
    echo "         $_f @$(printf '0x%x' "$_off"):  $_cur -> $_after"
}

echo "hook: tree = $TREE"

# 1) libnetd_updatable.so  —— 25Q2 / 内核 5.4 门限（netd 崩溃的真凶）
patch4 "$TREE/lib64/libnetd_updatable.so" "$((0x4bc4))" a9430054 '\037\040\003\325' \
       'libnetd_updatable.so  BpfHandler::init 25Q2 门限'

# 2) bin/netbpfload —— 25Q2 / 内核 5.4 门限（BPF 程序加载）
patch4 "$TREE/bin/netbpfload" "$((0xbdfc))" 00170036 '\037\040\003\325' \
       'netbpfload  25Q2 门限'

echo "hook: 全部补丁完成"
