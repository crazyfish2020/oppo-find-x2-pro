#!/bin/sh
# restore_boot.sh —— 把 boot 分区还原成 A16 的 boot.img
#
# 背景：2026-10-01 为了绕开"TWRP 里 adb 不能用"，把 TWRP 镜像写进了 boot 分区，
#       结果 TWRP 从 boot 分区起不来（它的 ramdisk 要 androidboot.mode=recovery，
#       只有从 recovery 分区启动时 ABL 才会传这个参数）。
#       本脚本把 boot 分区还原回 A16 的 boot.img。
#
# 用法：
#   ./restore_boot.sh            # 自动识别 adb / fastboot 模式
#   ./restore_boot.sh --check    # 只检查，不写
#
# 备份 sha256 已核对：fc60a85e05b6d9532470d59ebfa7705467214e2934e3ca9bb47b940442f1bbea

set -u

BASE="$HOME/Documents/oppo/2026-安卓16"
PT="$HOME/Documents/oppo/platform-tools"
ADB="$PT/adb"
FB="$PT/fastboot"
TWRP="$PT/TWRP-13-Compass-Color597-V2.0.img"
BOOT_IMG="$BASE/04-日志/dump_202707/boot.img"
WANT="fc60a85e05b6d9532470d59ebfa7705467214e2934e3ca9bb47b940442f1bbea"

ok()   { printf '\033[32m✓\033[0m %s\n' "$*"; }
err()  { printf '\033[31m✗\033[0m %s\n' "$*"; }
info() { printf '\033[36m·\033[0m %s\n' "$*"; }

CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

# ---------- 1) 本地备份必须完好 ----------
[ -f "$BOOT_IMG" ] || { err "找不到备份 $BOOT_IMG"; exit 1; }
got=$(shasum -a 256 "$BOOT_IMG" | awk '{print $1}')
if [ "$got" = "$WANT" ]; then
  ok "备份校验通过 ($got)"
else
  err "备份 sha256 不符！期望 $WANT，实际 $got —— 拒绝继续"
  exit 1
fi
[ "$CHECK_ONLY" = "1" ] && { info "--check 模式，结束"; exit 0; }

# ---------- 2) 找设备 ----------
dev_state() {
  if "$ADB" devices 2>/dev/null | tail -n +2 | grep -qE "(device|recovery|sideload)$"; then
    echo adb
  elif "$FB" devices 2>/dev/null | grep -q .; then
    echo fastboot
  else
    echo none
  fi
}

ST=$(dev_state)
info "设备状态：$ST"

case "$ST" in
  adb)
    info "用 adb 直接写 boot 分区（adbd 在 TWRP 里是 root）"
    "$ADB" push "$BOOT_IMG" /dev/block/by-name/boot || { err "写入失败"; exit 1; }
    info "回读校验…"
    rm -f /tmp/_rb_boot.img
    "$ADB" pull /dev/block/by-name/boot /tmp/_rb_boot.img >/dev/null 2>&1
    now=$(shasum -a 256 /tmp/_rb_boot.img 2>/dev/null | awk '{print $1}')
    rm -f /tmp/_rb_boot.img
    if [ "$now" = "$WANT" ]; then
      ok "boot 分区已还原，逐字节一致"
    else
      err "回读不一致：$now —— 请改用 fastboot 方式重刷"
      exit 1
    fi
    ;;
  fastboot)
    info "用 fastboot 刷 boot"
    "$FB" flash boot "$BOOT_IMG" || { err "fastboot flash 失败"; exit 1; }
    ok "fastboot flash boot 完成"
    ;;
  none)
    err "找不到设备。请先让手机进入 TWRP 或 fastboot 模式："
    cat <<'EOF'

  进 TWRP（recovery 分区里的 TWRP 本次没被动过，肯定还在）：
     长按电源键 ~15 秒强制关机 → 再用你原来进 TWRP 的按键组合开机

  或者进 fastboot：
     关机后按住 音量上+音量下（或音量下）再插 USB

  设备就绪后重新运行本脚本即可。
EOF
    exit 1
    ;;
esac

echo
ok "boot 分区已回到 A16 状态。"
info "TWRP 仍然在 recovery 分区，随时可用按键组合进入。"
info "如果还要临时进 TWRP 而不改 boot："
echo "    $FB boot $TWRP"
