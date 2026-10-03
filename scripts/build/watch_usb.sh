#!/bin/bash
# ============================================================================
#  watch_usb.sh —— 守候手机上线，三层同时盯
#
#  为什么要分三层：只盯 adb 的话，一旦手机在总线上但 adb 没起来，你会误判成
#  "线没插好"，从而去换线；反之只盯 USB 也会漏掉"总线没变但 adb 通了"的情况。
#
#    第 1 层 USB 总线（ioreg）：设备树变了 ⇒ 物理层通了
#    第 2 层 adb：认到 device ⇒ recovery/系统起来了
#    第 3 层 fastboot：认到 ⇒ 在 bootloader
#
#  用法： ./watch_usb.sh [超时秒数，默认 1800]
# ============================================================================
set -u
PT="$HOME/Documents/oppo/platform-tools"
ADB="$PT/adb"; FB="$PT/fastboot"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMO="${1:-1800}"

# 只统计"非坞/Hub"的设备名。注意 ioreg 的行带树形前缀，必须 sed 只取引号内的值，
# 否则前缀会混进指纹里（且 grep 的排除词会失效）。
usb_sig() {
  ioreg -r -c IOUSBHostDevice -w0 2>/dev/null \
    | sed -n 's/.*"USB Product Name" = "\(.*\)"[[:space:]]*$/\1/p' \
    | grep -viE 'hub|dock|keyboard|mouse|trackpad|display|camera|audio|ethernet|usb 3\.0 device|usb 2\.1' \
    | sort | tr '\n' '|'
}

say() { echo "[$(date +%H:%M:%S)] $*"; }

BASE="$(usb_sig)"
say "开始守候（最长 ${TMO}s）"
say "USB 基线（非 Hub 设备）: ${BASE:-（空）}"
say "现在只插手机、别做别的；一有变化我立刻打印全貌并自动取证。"
echo

i=0; last_report=0
while [ "$i" -lt "$TMO" ]; do
  sleep 2; i=$((i+2))

  # ---- 第 1 层：USB 总线 ----
  CUR="$(usb_sig)"
  if [ "$CUR" != "$BASE" ]; then
    echo
    say "★★★ USB 总线发生变化！"
    say "  之前: ${BASE:-（空）}"
    say "  现在: ${CUR:-（空）}"
    ioreg -r -c IOUSBHostDevice -w0 2>/dev/null \
      | grep -E '"USB Product Name"|"USB Vendor Name"|"idVendor" =|"idProduct" =' \
      | sed 's/^/      /'
    echo
  fi

  # ---- 第 2 层：adb ----
  if "$ADB" devices 2>/dev/null | tail -n +2 | grep -q "device$"; then
    say "★★★ adb 认到设备，立即取证"
    "$ADB" devices -l
    echo
    cd "$SCRIPT_DIR" && ./reflash_a16.sh --forensics
    exit 0
  fi

  # ---- 第 3 层：fastboot ----
  if "$FB" devices 2>/dev/null | grep -q .; then
    say "★★★ fastboot 认到设备（说明在 bootloader，不在 recovery）"
    "$FB" devices
    exit 0
  fi

  # ---- 每 60 秒报一次心跳，让用户知道还活着 ----
  if [ $((i - last_report)) -ge 60 ]; then
    say "… 守候中 ${i}s / ${TMO}s（USB 仍为基线，adb/fastboot 均空）"
    last_report=$i
  fi
done

echo
say "超时（${TMO}s）—— 期间 USB 总线一次都没变过。"
say "结论：手机完全没有被 Mac 枚举。按优先级排查："
echo "   1) 换一根【确认能传数据】的 USB-C 线（最常见原因：线只有充电线芯）"
echo "   2) 直插 Mac 机身的口，不要经过扩展坞/显示器 Hub"
echo "   3) 换 Mac 上另一个口"
echo "   4) 手机重新进一次 TWRP（拔插后再进）"
echo "   5) 打开「系统信息 → USB」，看插上瞬间有没有任何设备一闪而过"
