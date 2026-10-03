#!/bin/bash
# ==========================================================================
# flash_boot_permissive.sh
#   刷入「SELinux permissive」版 boot 镜像，用于验证 A16 移植的 SELinux 卡点。
#
# 背景
# ----
# v3 诊断镜像已证明：
#   · 面包屑 10/18/20 全部落盘 ⇒ init 确实跑到了 on early-init 的
#     exec_start apexd-bootstrap 之后
#   · 但 21a_apexdbootstrap_running 缺失、apexd_bootstrap.log 不存在
#     ⇒ service 从未进入 running，wrapper 一行没跑
# 根因（已用 SELinux 策略文件证实）：
#   apexd 域【没有】shell_exec 的 execute 权限
#     (allow apexd shell_exec ...) —— 不存在
#   (allow apexd system_file ...) —— 不存在（连 read 都没有）
#   (allow apexd metadata_file (dir (search))) —— 只有 search，不能 write
#   ⇒ 三重障碍全在 SELinux
# 而 v3 在 on early-init 顶部加的 setenforce 0 之所以无效，是因为
#   buildvariant=user ⇒ init 的 setenforce 内建命令被编译期禁用。
#
# 解法
# ----
# 在 boot 的 cmdline 里加 androidboot.selinux=permissive
#   ⇒ init 读 ro.boot.selinux 后调 security_setenforce(0)，
#     这条路径不受 ALLOW_PERMISSIVE_SELINUX 限制。
# （同一颗内核的 recovery 分区 cmdline 就是 androidboot.selinux=permissive，
#   所以 TWRP 里 getenforce 返回 Permissive —— 证明内核支持。）
#
# 用法:
#   ./flash_boot_permissive.sh [/tmp/boot_permissive.img]
#
# 兜底:
#   ./restore_boot.sh          # 还原成原版 A16 boot
# ==========================================================================
set -u

PT="$HOME/Documents/oppo/platform-tools"
ADB="$PT/adb"
FB="$PT/fastboot"
IMG="${1:-/tmp/boot_permissive.img}"
BASE="$HOME/Documents/oppo/2026-安卓16"

C_R=$'\033[31m'; C_G=$'\033[32m'; C_Y=$'\033[33m'; C_B=$'\033[36m'; C_0=$'\033[0m'
ok()   { printf '  %s✓%s %s\n' "$C_G" "$C_0" "$*"; }
warn() { printf '  %s⚠%s %s\n' "$C_Y" "$C_0" "$*"; }
err()  { printf '  %s✗%s %s\n' "$C_R" "$C_0" "$*"; }
say()  { printf '%s·%s %s\n' "$C_B" "$C_0" "$*"; }

say "===== 0) 前置检查 ====="
[ -f "$IMG" ] || { err "镜像不存在: $IMG"; exit 1; }
SZ=$(stat -f%z "$IMG")
ok "镜像 $IMG ($SZ 字节)"
[ "$SZ" = "100663296" ] || warn "大小不是 100663296，请确认"

# 确认 cmdline 里确实有 permissive
if dd if="$IMG" bs=1 skip=64 count=512 2>/dev/null | strings | grep -q "androidboot.selinux=permissive"; then
  ok "cmdline 含 androidboot.selinux=permissive"
else
  err "cmdline 里没有 androidboot.selinux=permissive —— 不要刷"; exit 1
fi

say "===== 1) 进入 bootloader ====="
STATE=""
if "$ADB" devices 2>/dev/null | grep -qE "recovery|device$"; then
  say "从 adb 重启到 bootloader"
  "$ADB" reboot bootloader 2>&1 | sed 's/^/    /'
elif "$FB" devices 2>/dev/null | grep -q fastboot; then
  ok "已在 fastboot 模式"
  STATE=fastboot
fi

if [ -z "$STATE" ]; then
  say "等待 fastboot（最多 60 秒）"
  for i in $(seq 1 60); do
    if "$FB" devices 2>/dev/null | grep -q fastboot; then STATE=fastboot; break; fi
    sleep 1
  done
fi
[ "$STATE" = "fastboot" ] || { err "没能进入 fastboot，请手动进 bootloader 后重跑"; exit 1; }
ok "fastboot 就绪"
"$FB" devices 2>&1 | sed 's/^/    /'

say "===== 2) 刷入 boot ====="
"$FB" flash boot "$IMG" 2>&1 | sed 's/^/    /'
RC=${PIPESTATUS[0]}
[ "$RC" = "0" ] || { err "刷入失败 rc=$RC"; exit 1; }
ok "boot 刷入成功"

say "===== 3) 重启 ====="
"$FB" reboot 2>&1 | sed 's/^/    /'
ok "已下发重启"

echo
say "下一步："
echo "    观察 USB 是否枚举（permissive 下 adbd 应当能起来）："
echo "      cd $BASE/03-脚本 && ./reflash_a16.sh --observe 180"
echo "    若仍失败，进 TWRP 取证（这次 apexd_bootstrap.log 应该有了）："
echo "      cd $BASE/03-脚本 && ./reflash_a16.sh --forensics"
echo "    兜底还原 boot："
echo "      cd $BASE/03-脚本 && ./restore_boot.sh"
