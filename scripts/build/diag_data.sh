#!/bin/sh
# diag_data.sh —— 等一个 adb 窗口，检查 /data/system 与 locksettings.db 的可读性
#
# 背景：A16 移植版 v7 实测 system_server 走到 startOtherServices 后，
#   LockSettingsService.onBootPhase(480) 抛
#     SQLiteCantOpenDatabaseException: Cannot open database
#       '/data/system/locksettings.db' ... File /data/system/locksettings.db is not readable
#   ⇒ uncaught ⇒ system_server SIGKILL 自己 ⇒ zygote 重启 ⇒ 整机重启。
# 需要判断这是 DAC、SELinux（permissive 下不该拦，但要看 avc）、还是 /data 挂载/FBE 问题。
set -u
ADB="${ADB:-$(command -v adb)}"
OUT="${1:-/tmp/diag_data_$(date +%H%M%S)}"
mkdir -p "$OUT"

say() { echo "[$(date +%H:%M:%S)] $*"; }

say "等 adb（最多 240s）..."
i=0
while [ $i -lt 240 ]; do
  st="$("$ADB" get-state 2>/dev/null || echo none)"
  case "$st" in device|recovery) break;; esac
  i=$((i+3)); sleep 3
done
case "$st" in device|recovery) ;; *) say "✗ 没等到 adb（state=${st}）"; exit 1;; esac
say "★ adb 就绪，开始取证 -> $OUT"

run() { name="$1"; shift; { echo "### $*"; "$ADB" shell "$@" 2>&1; echo; } >> "$OUT/data.txt"; }

: > "$OUT/data.txt"

run mounts   'mount | grep -E " /data| /metadata| /mnt/vendor"'
run datals   'ls -lad /data /data/system 2>&1'
run db       'ls -la /data/system/locksettings.db* /data/system/locksettings* 2>&1'
run dbstat   'stat /data/system/locksettings.db 2>&1'
run canread  'id; echo "---"; cat /data/system/locksettings.db > /dev/null 2>&1 && echo "root can read DB" || echo "root CANNOT read DB"'
run suread   'su -c "id; cat /data/system/locksettings.db >/dev/null 2>&1 && echo ok || echo fail" 2>&1'
run datadir  'ls -la /data/system/ 2>&1 | head -40'
run fbe      'getprop | grep -iE "crypto|fbe|fde|vold" | head -30'
run vold     'ls -la /data/vendor 2>&1; echo "---"; ls -la /data/misc/vold 2>&1 | head'
run avc      'dmesg | grep -iE "avc:|denied" | tail -30'
run avc2     'logcat -d -b all | grep -iE "avc:.*locksettings|denied.*locksettings" | tail -20'
run lockset  'logcat -d -b all | grep -iE "LockSettings|locksettings" | tail -40'
run selinux  'getenforce; getprop ro.boot.selinux; getprop ro.build.selinux; cat /proc/cmdline'

say "完成 -> $OUT/data.txt"
wc -l "$OUT/data.txt"
