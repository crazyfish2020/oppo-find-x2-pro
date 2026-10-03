#!/bin/bash
# ============================================================================
#  reflash_system.sh —— 只重刷 system 一个分区 (保留其余布局不动)
#
#  用途: 迭代调补丁时用。比整包 flash_coloros16.sh 快得多、风险小得多,
#        因为不碰 super 的布局 (不删/不建/不 resize 任何分区)。
#
#  用法:
#     ./reflash_system.sh <system.img 路径>
#     ./reflash_system.sh                 # 默认用 a16_patched/system_a16final.img
#
#  前置: 设备需在 TWRP(recovery) 或已连 adb; 脚本自己会进 fastbootd。
# ============================================================================
set -u

PT="$HOME/Documents/oppo/platform-tools"
ADB="$PT/adb"
FB="$PT/fastboot"
# ★ 默认必须是最终镜像（32 个 APEX 已重建 + netbpfload/wb_netbpfload.rc 已注入）。
#   system.img 是 v1（旧 APEX，incompat=0x3，4.19 内核挂不上）——刷了等于白刷。
DEFAULT_IMG="$HOME/Documents/oppo/2026-安卓16/02-移植素材/a16_patched/system_a16final.img"

IMG="${1:-$DEFAULT_IMG}"

C_R=$'\033[31m'; C_G=$'\033[32m'; C_Y=$'\033[33m'; C_B=$'\033[36m'; C_0=$'\033[0m'
say()  { echo "${C_B}[$(date +%H:%M:%S)]${C_0} $*"; }
ok()   { echo "  ${C_G}✓${C_0} $*"; }
warn() { echo "  ${C_Y}⚠${C_0} $*"; }
err()  { echo "  ${C_R}✗${C_0} $*"; }

[ -f "$IMG" ] || { err "镜像不存在: $IMG"; exit 1; }
SZ=$(stat -f%z "$IMG")
say "待刷镜像: $IMG"
say "大小     : $SZ 字节"

# ---- 1. 进 bootloader ----
say "步骤 1/4: 进入 bootloader"
if "$ADB" devices 2>/dev/null | tail -n +2 | grep -q "recovery"; then
  "$ADB" reboot bootloader
elif "$ADB" devices 2>/dev/null | tail -n +2 | grep -q "device"; then
  "$ADB" reboot bootloader
elif "$FB" devices 2>/dev/null | grep -q .; then
  ok "已在 bootloader"
else
  err "既没有 adb 设备也没有 fastboot 设备"; exit 1
fi
sleep 8
# ★ fastboot 36.0.1 没有 wait-for-device 子命令（那是 adb 的），改用轮询
i=0
until "$FB" devices 2>/dev/null | grep -q .; do
  sleep 2; i=$((i+1)); [ $i -gt 40 ] && { err "等待 bootloader 超时"; exit 1; }
done
ok "bootloader 就绪"

# ---- 2. 进 fastbootd ----
say "步骤 2/4: 进入 fastbootd (逻辑分区必须在用户空间刷)"
"$FB" reboot fastboot
i=0
until "$FB" devices 2>/dev/null | grep -q .; do
  sleep 2; i=$((i+1)); [ $i -gt 40 ] && { err "fastbootd 超时"; exit 1; }
done
sleep 3
ok "fastbootd 就绪"

# ---- 3. 记录分区尺寸 (仅作提示) ----
# 注意: fastboot 刷逻辑分区时会自动 "Resizing 'system'", 只要 super 组内有空余
#       就会把分区撑到镜像大小。所以「镜像 > 当前分区」不是错误, 只提示。
PSZ=$("$FB" getvar partition-size:system 2>&1 | grep -o '0x[0-9a-fA-F]*' | tail -1)
if [ -n "$PSZ" ]; then
  PDEC=$((PSZ))
  say "system 分区当前大小: $PDEC 字节"
  if [ "$SZ" -gt "$PDEC" ]; then
    warn "镜像比当前分区大 $((SZ - PDEC)) 字节 —— fastboot 会自动扩容 (需 super 组有空余)"
  else
    ok "镜像可容纳 (余 $((PDEC - SZ)) 字节)"
  fi
fi

# ---- 4. 刷入 ----
say "步骤 3/4: 刷入 system"
# 不能用 `if cmd | sed ...` —— 退出码来自 sed, 恒为 0。必须取 PIPESTATUS[0]。
"$FB" flash system "$IMG" 2>&1 | sed 's/^/    /'
rc=${PIPESTATUS[0]}
if [ "$rc" -eq 0 ]; then
  ok "system 刷入完成"
else
  err "system 刷入失败 (rc=$rc)"; exit 1
fi

say "步骤 4/4: 重启到系统"
"$FB" reboot
ok "已发出重启指令"
echo
echo "  接下来可用 boot_capture.py 观察启动过程:"
echo "    python3 03-脚本/boot_capture.py"
