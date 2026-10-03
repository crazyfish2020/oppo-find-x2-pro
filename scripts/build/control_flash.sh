#!/bin/bash
# ============================================================================
#  control_flash.sh —— 控制实验：刷回 X2 Pro 已知可用 boot，观察 patched system 能否启动
#
#  当前设备上 system / system_ext 已经是重建后的版本（阶段1/2刷入成功）。
#  只需刷回 X2 Pro 自己的 boot（4.19.157 内核），重启，观察。
# ============================================================================
set -u

PT="$HOME/Documents/oppo/platform-tools"
FB="$PT/fastboot"
ADB="$PT/adb"
BOOT_GOOD="$HOME/Documents/oppo/2026-安卓16/04-日志/dump_202707/boot.img"

C_B=$'\033[36m'; C_G=$'\033[32m'; C_R=$'\033[31m'; C_0=$'\033[0m'
say()  { echo "${C_B}[$(date +%H:%M:%S)]${C_0} $*"; }
ok()   { echo "  ${C_G}✓${C_0} $*"; }
err()  { echo "  ${C_R}✗${C_0} $*"; }

# ---- 等 bootloader ----
say "控制实验：等 bootloader 出现（最多 300 秒）"
i=0
while [ $i -lt 300 ]; do
  if "$FB" devices 2>/dev/null | grep -q .; then
    ok "bootloader 就绪: $("$FB" devices | tr '\n' ' ')"
    break
  fi
  sleep 3; i=$((i+3))
  [ $((i % 30)) -eq 0 ] && say "  等待中… $i 秒"
done
[ $i -ge 300 ] && { err "超时"; exit 1; }

# ---- 刷 boot ----
say "刷入 X2 Pro 已知可用 boot（4.19.157 内核）"
"$FB" flash boot "$BOOT_GOOD" 2>&1 | sed 's/^/    /'
[ "${PIPESTATUS[0]}" -eq 0 ] && ok "boot 刷入完成" || { err "boot 刷入失败"; exit 1; }

# ---- 重启 ----
say "重启到系统"
"$FB" reboot
ok "已发出重启指令"

# ---- 观察 150 秒 ----
say "观察 150 秒…"
i=0
while [ $i -lt 150 ]; do
  if "$ADB" devices 2>/dev/null | tail -n +2 | grep -qE "device|recovery"; then
    ok "★ adb 出现！ patched system + X2 Pro 内核 能启动！"
    "$ADB" shell "getprop ro.build.version.release; getprop ro.build.version.sdk; getprop ro.build.display.id; ls /apex" 2>/dev/null | sed 's/^/    /'
    exit 0
  fi
  if "$FB" devices 2>/dev/null | grep -q .; then
    err "⚠ fastboot 出现（掉回 bootloader）—— 启动失败"
    exit 2
  fi
  sleep 3; i=$((i+3))
done

err "✗ 150 秒超时，完全无 USB —— 启动失败"
exit 3
