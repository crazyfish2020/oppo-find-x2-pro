#!/bin/bash
# ============================================================================
#  wait_and_flash.sh —— 守候设备出现，出现即自动执行 flash_diag.sh
#
#  背景：设备从 USB 上消失后，需要人工把它送进 bootloader。
#        与其让用户来回问「插上了吗」，不如挂一个守候进程：
#        每 3 秒探测一次 fastboot / adb，一旦发现设备就立刻跑刷机流程。
#
#  用法:  ./wait_and_flash.sh [最长等待秒数，默认 5400] [传给 flash_diag.sh 的额外参数…]
#  例:    ./wait_and_flash.sh 5400 --kernel 02-移植素材/a16_patched/hybrid_8tkernel.img
#  日志:  04-日志/wait_flash_<ts>.log
# ============================================================================
set -u

PROJ="$HOME/Documents/oppo/2026-安卓16"
PT="$HOME/Documents/oppo/platform-tools"
FB="$PT/fastboot"
ADB="$PT/adb"
MAX="${1:-5400}"
shift || true
EXTRA="$*"

TS=$(date +%Y%m%d_%H%M%S)
LOG="$PROJ/04-日志/wait_flash_$TS.log"
mkdir -p "$PROJ/04-日志"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

log "守候开始，最长 $MAX 秒（日志 ${LOG}）"
if [ -n "$EXTRA" ]; then
  log "将带参数执行 flash_diag.sh: ${EXTRA}"
else
  log "将不带参数执行 flash_diag.sh（默认：已知可用 boot + 诊断 system + 修复 system_ext）"
fi

i=0
while [ $i -lt $MAX ]; do
  if "$FB" devices 2>/dev/null | grep -q .; then
    log "★ 检测到 fastboot 设备: $("$FB" devices | tr '\n' ' ')"
    break
  fi
  if "$ADB" devices 2>/dev/null | tail -n +2 | grep -qE "device|recovery|sideload"; then
    log "★ 检测到 adb 设备: $("$ADB" devices | tail -n +2 | tr '\n' ' ')"
    break
  fi
  sleep 3
  i=$((i+3))
  if [ $((i % 60)) -eq 0 ]; then
    log "  等待中… 已 $i 秒，未发现设备"
  fi
done

if [ $i -ge $MAX ]; then
  log "✗ 超时 $MAX 秒，未发现设备。退出。"
  exit 1
fi

sleep 3
log "开始执行 flash_diag.sh ${EXTRA} …"
"$PROJ/03-脚本/flash_diag.sh" $EXTRA 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
log "flash_diag.sh 结束，退出码 $rc"
exit $rc
