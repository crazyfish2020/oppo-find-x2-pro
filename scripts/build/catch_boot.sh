#!/bin/sh
# catch_boot.sh —— 抓一次完整的「启动→重启」窗口日志
#
# 背景：A16 移植版目前 system_server 能起来并进入包扫描，但约 100s 后整机重启。
# 需要抓到重启前最后时刻的完整日志（logcat 全量 + dmesg + tombstones + dropbox + wbdiag 面包屑）。
#
# 用法：
#   ./catch_boot.sh [输出目录]
# 默认输出到 04-日志/catch_YYYYmmdd_HHMMSS/
#
# 依赖：adb 在 PATH（或项目 platform-tools）
set -u

ADB="${ADB:-$(command -v adb || echo "$HOME/Documents/oppo/2026-安卓16/02-移植素材/platform-tools/adb")}"
PROJ="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$PROJ/04-日志/catch_$(date +%Y%m%d_%H%M%S)}"
mkdir -p "$OUT"

say() { echo "[$(date +%H:%M:%S)] $*"; }

say "等待 adb 设备出现（最多 180s）..."
i=0
while [ $i -lt 180 ]; do
  st="$("$ADB" get-state 2>/dev/null || echo none)"
  [ "$st" = device ] || [ "$st" = recovery ] && break
  i=$((i+3)); sleep 3
done
[ "$st" = device ] || [ "$st" = recovery ] || { say "✗ 未等到 adb（state=${st}）"; exit 1; }
say "★ 捕获到 adb 窗口（state=${st}），开始抓取"

# 1) 立即 dump 已有 logcat 缓冲区（最关键）
"$ADB" logcat -d -b all > "$OUT/logcat_all.txt" 2>&1
"$ADB" logcat -d -b crash > "$OUT/logcat_crash.txt" 2>&1
say "  logcat_all.txt  $(wc -l < "$OUT/logcat_all.txt" 2>/dev/null || echo 0) 行"

# 2) 属性快照
"$ADB" shell 'getprop' > "$OUT/props_full.txt" 2>&1

# 3) dmesg
"$ADB" shell 'dmesg' > "$OUT/dmesg.txt" 2>&1
say "  dmesg.txt  $(wc -l < "$OUT/dmesg.txt" 2>/dev/null || echo 0) 行"

# 4) 流式 logcat，直到掉线（覆盖窗口后半段）
( "$ADB" logcat -b all -v threadtime > "$OUT/logcat_stream.txt" 2>&1 ) &
STREAM_PID=$!

# 5) 周期性记录状态 + 抓 tombstones/dropbox/wbdiag
k=0
while [ $k -lt 240 ]; do
  sleep 5; k=$((k+5))
  st="$("$ADB" get-state 2>/dev/null || echo none)"
  bc="$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')"
  ba="$("$ADB" shell getprop init.svc.bootanim 2>/dev/null | tr -d '\r')"
  echo "$k s  state=$st boot_completed=$bc bootanim=$ba" >> "$OUT/watch.log"
  if [ "$st" != device ] && [ "$st" != recovery ]; then
    say "★ 设备掉线（t=${k}s）"
    break
  fi
  if [ $((k % 15)) -eq 0 ]; then
    "$ADB" shell 'ls -la /data/tombstones /data/system/dropbox /metadata/wbdiag 2>/dev/null' \
        >> "$OUT/listings.txt" 2>&1
  fi
done

kill $STREAM_PID 2>/dev/null

# 6) 掉线前最后一刻再抢救一次（可能已经掉了，尽力）
"$ADB" shell 'dmesg' > "$OUT/dmesg_last.txt" 2>&1

say "抓取结束 -> $OUT"
ls -la "$OUT"
