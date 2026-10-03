#!/bin/bash
# ============================================================================
#  one_shot_flash.sh —— 设备一上线就把【最终 A16 system】刷进去, 并抓启动日志
#
#  安全设计（很重要, 别删）:
#    * 闸门 1 —— 只有设备处于 recovery(TWRP) 或 bootloader / fastbootd 时才动手。
#                 这代表用户已经主动把手机放进了刷机状态。
#                 如果手机是【正常开机】(adb 显示 device), 一律不碰, 退出码 2,
#                 绝不把用户正在使用的手机抢去刷机。
#    * 闸门 2 —— 只刷一次。刷完写哨兵 04-日志/.a16_system_flashed,
#                 之后任何调用直接退出码 4, 避免掉回 fastboot 后被反复重刷。
#                 要重刷就手动删掉那个哨兵文件。
#
#  退出码:
#     0 = 已刷入并发出重启, 启动日志见 04-日志/oneshot_*
#     1 = 出错（镜像缺失 / 刷入失败）
#     2 = 手机正常开机中, 未动作（安全闸门 1）
#     3 = 没有任何设备在线
#     4 = 已刷过一次（安全闸门 2, 哨兵存在）
# ============================================================================
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
PROJ="$(dirname "$HERE")"
PT="$HOME/Documents/oppo/platform-tools"
ADB="$PT/adb"
FB="$PT/fastboot"
IMG="$PROJ/02-移植素材/a16_patched/system_a16final.img"
SENTINEL="$PROJ/04-日志/.a16_system_flashed"
LOGDIR="$PROJ/04-日志"
mkdir -p "$LOGDIR"

STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="$LOGDIR/oneshot_$STAMP"
mkdir -p "$LOG"

say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG/run.log"; }
die() { say "$*"; exit "${2:-1}"; }

# ---------------- 闸门 2: 只刷一次 ----------------
if [ -f "$SENTINEL" ]; then
  say "已刷过一次（哨兵存在: ${SENTINEL}）—— 本次不动作。"
  say "要重刷请先删掉该哨兵文件。"
  exit 4
fi

# ---------------- 镜像体检 ----------------
if [ ! -f "$IMG" ]; then
  die "镜像不存在: $IMG"
fi
SZ=$(stat -f%z "$IMG")
if [ "$SZ" != "1063677952" ]; then
  die "镜像大小异常: ${SZ}（应为 1063677952）—— 拒绝刷入"
fi
say "镜像: $IMG  ($SZ 字节)"

# ---------------- 探测设备状态 ----------------
ADB_OUT="$("$ADB" devices 2>/dev/null | tail -n +2 | grep -v '^$' || true)"
FB_OUT="$("$FB" devices 2>/dev/null | grep -v '^$' || true)"

say "adb    : ${ADB_OUT:-（无）}"
say "fastboot: ${FB_OUT:-（无）}"

STATE="none"
if echo "$ADB_OUT" | grep -q "recovery"; then
  STATE="recovery"
elif echo "$FB_OUT" | grep -q "fastboot"; then
  STATE="fastboot"
elif echo "$ADB_OUT" | grep -q "device"; then
  STATE="booted"
fi
say "判定状态: $STATE"

# ---------------- 闸门 1: 正常开机绝不碰 ----------------
if [ "$STATE" = "booted" ]; then
  say "手机处于【正常开机】状态 —— 不动作（安全闸门 1）。"
  say "如确认要刷机, 请手动进入 TWRP 或 bootloader 后再运行本脚本。"
  exit 2
fi

if [ "$STATE" = "none" ]; then
  say "没有任何设备在线（既无 adb 也无 fastboot）—— 不动作。"
  exit 3
fi

# ---------------- 开刷 ----------------
say "开始刷入 system（reflash_system.sh 会自行进 fastbootd）…"
bash "$HERE/reflash_system.sh" "$IMG" 2>&1 | tee -a "$LOG/run.log"
rc=${PIPESTATUS[0]}
if [ "$rc" -ne 0 ]; then
  say "✗ reflash_system.sh 失败 (rc=$rc) —— 不写哨兵, 允许下次重试"
  exit 1
fi

# 刷入成功 -> 写哨兵
echo "$(date '+%F %T')  flashed $IMG ($SZ bytes)" > "$SENTINEL"
say "✓ 已写入哨兵: $SENTINEL"

# ---------------- 抓启动日志 ----------------
# reflash_system.sh 末尾已经 fastboot reboot 了, 所以这里只观测不重启。
# boot_capture.py 内部用的是裸 `adb` / `fastboot`, 必须把 platform-tools 塞进 PATH。
say "开始抓启动过程（最多约 3 分钟, 掉回 fastboot 或进入系统都会结束）…"
export PATH="$PT:$PATH"
/Users/skeletondie/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3 \
  "$HERE/boot_capture.py" --no-reboot "$LOG" 2>&1 | tee -a "$LOG/run.log"

say "完成。日志目录: $LOG"
say "判读方法见 05-分析/根因-*.md 的 §7.5 决策表。"
exit 0
