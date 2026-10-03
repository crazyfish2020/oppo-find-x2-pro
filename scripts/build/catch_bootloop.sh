#!/bin/bash
# ==========================================================================
# catch_bootloop.sh —— 抢抓 boot loop 窗口内的日志
#
# 背景
# ----
# permissive 修复后，A16 能启动到 adbd（adb devices 显示 device），
# 但只存活约 18 秒就消失 ⇒ boot loop。
# 必须在【adb 可用的那十几秒内】把 logcat / dmesg / dropbox 抢下来，
# 否则永远看不到崩溃原因。
#
# 用法:
#   ./catch_bootloop.sh [等待上限秒数]     # 默认 600
# ==========================================================================
set -u

PT="$HOME/Documents/oppo/platform-tools"
ADB="$PT/adb"
MAX="${1:-600}"
OUT="$HOME/Documents/oppo/2026-安卓16/04-日志/bootloop_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUT"

echo "输出目录: $OUT"
echo "轮询 adb（最多 $MAX 秒），一旦出现就抢抓..."

grab() {                       # grab <文件名> <设备内命令>
  "$ADB" shell "$2" > "$OUT/$1" 2>&1
  printf '  %-22s %8s 字节\n' "$1" "$(wc -c < "$OUT/$1" | tr -d ' ')"
}

for i in $(seq 1 "$MAX"); do
  if "$ADB" devices 2>/dev/null | grep -qE "device$"; then
    echo "[$(date +%H:%M:%S)] ★ 设备出现（第 $i 秒），立刻抓取"

    # 先抓最关键的：当前属性 + 最近日志
    grab props.txt "getprop | grep -E 'sys.boot_completed|dev.bootcomplete|init.svc.(zygote|surfaceflinger|system_server|servicemanager|apexd|netd)|ro.build.version.release|persist.sys.boot.reason'"

    # logcat 全缓冲（崩溃原因通常在这里）
    "$ADB" logcat -d -b all -v threadtime > "$OUT/logcat_all.txt" 2>&1
    printf '  %-22s %8s 字节\n' "logcat_all.txt" "$(wc -c < "$OUT/logcat_all.txt" | tr -d ' ')"

    # 崩溃相关的缓冲区
    "$ADB" logcat -d -b crash -v threadtime > "$OUT/logcat_crash.txt" 2>&1
    printf '  %-22s %8s 字节\n' "logcat_crash.txt" "$(wc -c < "$OUT/logcat_crash.txt" | tr -d ' ')"

    grab dmesg.txt          "dmesg"
    grab last_kmsg.txt      "cat /proc/last_kmsg"
    grab dropbox.txt        "ls -la /data/system/dropbox"
    grab tombstones.txt     "ls -la /data/tombstones"
    grab apex_list.txt      "ls -la /apex"
    grab mounts.txt         "cat /proc/mounts"
    grab wbdiag.txt         "ls -la /metadata/wbdiag; echo ---; cat /metadata/wbdiag/apexd_bootstrap.log 2>/dev/null | tail -40"

    echo "[$(date +%H:%M:%S)] 抓取完成"
    echo
    echo "=== 关键属性 ==="
    head -40 "$OUT/props.txt"
    echo
    echo "=== logcat_crash 尾部 ==="
    tail -30 "$OUT/logcat_crash.txt"
    exit 0
  fi
  sleep 1
done

echo "✗ 等待超时，设备始终未出现"
exit 1
