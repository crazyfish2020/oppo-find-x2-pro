#!/bin/bash
# ============================================================================
#  kernel_ab_test.sh —— 无人值守的「换内核」A/B 实验
#
#  目的：一次刷机只能得到 1 个数据点。人不在旁边时，把可能的组合排成序列
#        自动跑完，天亮就能看到结论。
#
#  序列（每阶段都跑完整的 flash_diag.sh，只改 --kernel）：
#    阶段 1  8T 内核 + X2 Pro ramdisk + X2 Pro dtb   (hybrid_8tkernel.img)
#             —— 变量最少，最干净地检验"内核"这一个假设
#    阶段 2  8T 内核 + A16 ramdisk   + X2 Pro dtb   (hybrid_8tkernel_a16rd.img)
#             —— 顺带试 A16 的 first-stage init
#    阶段 3  回退：不加 --kernel，刷回已知可用 boot + 诊断 system + 修复 system_ext
#
#  每阶段后等 BOOT_WAIT 秒观察：出现 adb ⇒ 成功，收工并抓取现场信息。
#
#  安全边界：
#    - 每一阶段的 flash_diag.sh 内部都会**先刷回已知可用 boot**，把 fastbootd 拿回来，
#      所以任何一阶段失败都不会让设备卡在"刷不进 system"的半路。
#    - 若某阶段后**完全无 USB**（连节点都没有），说明失败在内核/一阶段 init 之前，
#      自动流程无法继续（拿不到 fastboot）⇒ 停下并提示人工按键进 bootloader。
#    - 任何阶段只要进系统就立刻停止，不会覆盖成功状态。
#
#  用法:  ./kernel_ab_test.sh [最长等待设备秒数，默认 28800]
#  日志:  04-日志/kernel_ab_<ts>.log
# ============================================================================
set -u

PROJ="$HOME/Documents/oppo/2026-安卓16"
PT="$HOME/Documents/oppo/platform-tools"
FB="$PT/fastboot"
ADB="$PT/adb"
MAX="${1:-28800}"

STAGE1="$PROJ/02-移植素材/a16_patched/hybrid_8tkernel.img"
STAGE2="$PROJ/02-移植素材/a16_patched/hybrid_8tkernel_a16rd.img"
BOOT_WAIT=150

TS=$(date +%Y%m%d_%H%M%S)
LOG="$PROJ/04-日志/kernel_ab_$TS.log"
OUT="$PROJ/04-日志/kernel_ab_$TS"
mkdir -p "$PROJ/04-日志" "$OUT"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

# ---- 探测设备状态：adb / fastboot / none ----
probe() {
  local i=0
  while [ $i -lt "$BOOT_WAIT" ]; do
    if "$ADB" devices 2>/dev/null | tail -n +2 | grep -qE "device|recovery|sideload"; then
      echo adb; return
    fi
    if "$FB" devices 2>/dev/null | grep -q .; then
      echo fastboot; return
    fi
    sleep 3
    i=$((i+3))
  done
  echo none
}

# ---- 等设备出现（最长 MAX 秒）----
wait_device() {
  local i=0
  while [ $i -lt "$MAX" ]; do
    if "$FB" devices 2>/dev/null | grep -q .; then
      log "★ 检测到 fastboot 设备"; return 0
    fi
    if "$ADB" devices 2>/dev/null | tail -n +2 | grep -qE "device|recovery|sideload"; then
      log "★ 检测到 adb 设备"; return 0
    fi
    sleep 3
    i=$((i+3))
    [ $((i % 300)) -eq 0 ] && log "  等待设备… 已 $i 秒"
  done
  log "✗ 等待设备超时 $MAX 秒"
  return 1
}

# ---- 成功时抓取现场信息 ----
capture() {
  local tag="$1"
  log "抓取现场信息 → $OUT/ok_$tag/"
  mkdir -p "$OUT/ok_$tag"
  for c in "getprop ro.build.version.release" "getprop ro.build.version.sdk" \
           "getprop ro.build.display.id" "getprop ro.product.model" \
           "ls /apex" "ls -l /system/bin/netbpiload" "dmesg" "logcat -d -b all"; do
    fn=$(echo "$c" | tr ' /' '__')
    "$ADB" shell "$c" > "$OUT/ok_$tag/$fn.txt" 2>&1
  done
  log "已保存。关键值："
  "$ADB" shell getprop ro.build.version.release | sed 's/^/    Android /' | tee -a "$LOG"
  "$ADB" shell getprop ro.build.version.sdk | sed 's/^/    SDK /' | tee -a "$LOG"
  "$ADB" shell getprop ro.build.display.id | sed 's/^/    版本号 /' | tee -a "$LOG"
  "$ADB" shell ls /apex 2>/dev/null | tr '\n' ' ' | sed 's/^/    \/apex: /' | tee -a "$LOG"
  echo "" | tee -a "$LOG"
}

run_flash() {
  local extra="$1" label="$2"
  log "—— 执行 flash_diag.sh ${extra} （${label}）——"
  "$PROJ/03-脚本/flash_diag.sh" $extra 2>&1 | tee -a "$LOG"
  local rc=${PIPESTATUS[0]}
  log "flash_diag.sh 退出码 $rc"
  return $rc
}

log "=============================================================="
log "换内核 A/B 实验开始（最长等设备 $MAX 秒，每阶段观察 $BOOT_WAIT 秒）"
log "阶段1: $(basename "$STAGE1")"
log "阶段2: $(basename "$STAGE2")"
log "阶段3: 回退（不加 --kernel）"
log "=============================================================="

for f in "$STAGE1" "$STAGE2"; do
  [ -f "$f" ] || { log "✗ 缺文件 ${f}，中止"; exit 1; }
done

# ================= 阶段 1 =================
log ""
log "=== 阶段 1/3：8T 内核 + X2 Pro ramdisk + X2 Pro dtb ==="
wait_device || exit 1
if ! run_flash "--kernel $STAGE1" "阶段1"; then
  log "✗ 阶段1 的刷机流程**没有成功执行**（非零退出）⇒ 观察结果无意义，中止。"
  log "  先排查 flash_diag.sh 为何失败（本次就是踩到 fastboot 没有 wait-for-device），再重跑。"
  exit 4
fi
log "等待 $BOOT_WAIT 秒观察启动结果…"
R=$(probe)
log "阶段1 观察结果: $R"
if [ "$R" = "adb" ]; then
  log "★★★ 阶段1 成功：8T 的 4.19.325 内核 + X2 Pro dtb/ramdisk 能进系统！"
  log "    ⇒ 根因确证：X2 Pro 的 4.19.157 内核缺 erofs_load_compr_cfgs，挂不上 A16 APEX。"
  capture "stage1"
  exit 0
fi
if [ "$R" = "none" ]; then
  log "✗ 阶段1 后完全无 USB —— 失败在内核/一阶段 init 之前。"
  log "  自动流程无法继续（拿不到 fastboot）。请人工送进 bootloader："
  log "    音量+ 和 音量- 一起按住，再按住电源键约 10 秒。"
  log "  然后手动跑：./03-脚本/kernel_ab_test.sh  或  ./03-脚本/flash_diag.sh"
  exit 2
fi
log "阶段1 未进系统（${R}）⇒ 进入阶段2"

# ================= 阶段 2 =================
log ""
log "=== 阶段 2/3：8T 内核 + A16 ramdisk + X2 Pro dtb ==="
wait_device || exit 1
if ! run_flash "--kernel $STAGE2" "阶段2"; then
  log "✗ 阶段2 的刷机流程**没有成功执行**（非零退出）⇒ 观察结果无意义，中止。"
  exit 4
fi
log "等待 $BOOT_WAIT 秒观察启动结果…"
R=$(probe)
log "阶段2 观察结果: $R"
if [ "$R" = "adb" ]; then
  log "★★★ 阶段2 成功：8T 内核 + A16 ramdisk 能进系统！"
  log "    ⇒ 说明 A16 的 first-stage init 也需要，X2 Pro 的 ramdisk 是另一个障碍。"
  capture "stage2"
  exit 0
fi
if [ "$R" = "none" ]; then
  log "✗ 阶段2 后完全无 USB。请人工送进 bootloader 后手动跑 flash_diag.sh 回退。"
  exit 2
fi
log "阶段2 未进系统（${R}）⇒ 进入阶段3（回退）"

# ================= 阶段 3：回退 =================
log ""
log "=== 阶段 3/3：回退到已知可用 boot + 诊断 system + 修复 system_ext ==="
wait_device || exit 1
if ! run_flash "" "回退"; then
  log "✗ 阶段3（回退）的刷机流程**没有成功执行**（非零退出）⇒ 请手动排查。"
  exit 4
fi
log "等待 $BOOT_WAIT 秒观察…"
R=$(probe)
log "回退后观察结果: $R"
if [ "$R" = "adb" ]; then
  log "★ 回退后能进 adb —— 诊断版生效，可读 /metadata/wbdiag/"
  capture "revert"
  exit 0
fi
log ""
log "=============================================================="
log "结论汇总"
log "  阶段1（8T核+X2rd）      : 未进系统"
log "  阶段2（8T核+A16rd）     : 未进系统"
log "  阶段3（回退已知可用）   : $R"
log ""
log "  ⇒ 换内核**不能**解决问题 ⇒ 根因不在 EROFS 压缩配置（或不止于此）。"
log "  ⇒ 下一步：进 TWRP 读 /metadata/wbdiag/{trace.txt,report_*.txt,dmesg_*.txt}"
log "=============================================================="
exit 3
