#!/bin/bash
# ============================================================================
#  flash-m1.sh —— Apple Silicon (M1 / M2 / M3) 一键刷机脚本
#
#  目标机  : OPPO Find X2 Pro  (PDEM30 / OP4A7A / kona / SM8250)
#  刷入内容: ColorOS 16 / Android 16 (SDK 36) 移植版
#            boot + system + system_ext 三个分区
#  保留不动: vendor / odm / product / my_* / dtbo / vbmeta / 基带固件
#  镜像位置: 自动在 脚本同目录 / images/ / 上级目录 中查找三个 .img
#
#  用法:
#    ./flash-m1.sh              自检 + 刷机（默认）
#    ./flash-m1.sh --check      只做自检与 sha256 校验，不碰设备
#    ./flash-m1.sh --status     只打印设备当前状态
#    ./flash-m1.sh --reboot     只重启到系统
#    ./flash-m1.sh --help       显示帮助
#
#  环境变量:
#    ADB=/path/to/adb  FASTBOOT=/path/to/fastboot   手动指定工具
#    SKIP_HASH=1                                    跳过 sha256 校验（不推荐）
#
#  ---- 为什么单独为 M1 写 ----
#  1) Apple Silicon 的 Homebrew 在 /opt/homebrew/bin（Intel 是 /usr/local/bin），
#     脚本按 arm64 / x86_64 自动选路径。
#  2) ★ `fastboot getvar` 在设备缺失时会【无限阻塞】，而 macOS 没有 `timeout`
#     命令可以兜底。本脚本完全不用 getvar，改用 `fastboot devices` 的输出
#     （fastboot / fastbootd）判断模式，不会卡死。
#  3) M1 上 USB 会随模式切换短暂重新枚举，脚本对每个状态转换都做轮询等待。
# ============================================================================
set -uo pipefail

# ---------------------------------------------------------------- 颜色 / 输出
if [ -t 1 ]; then
  R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; B=$'\033[34m'; N=$'\033[0m'; BD=$'\033[1m'
else
  R=''; G=''; Y=''; B=''; N=''; BD=''
fi
say()  { printf '%s[%s]%s %s\n' "$B" "$(date +%H:%M:%S)" "$N" "$*"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*"; }
err()  { printf '  %s✗%s %s\n' "$R" "$N" "$*" >&2; }
die()  { err "$*"; exit 1; }
rule() { printf '%s%s%s\n' "$BD" "------------------------------------------------------------" "$N"; }

# ---------------------------------------------------------------- 镜像定位
HERE="$(cd "$(dirname "$0")" && pwd)"

# 自动在常见位置寻找三个 .img，依次尝试：
#   脚本同目录 / 同目录下 images/ / 上级目录 / 上级 images/ / 上级 release-assets/
IMG=""
for cand in "$HERE" "$HERE/images" "$HERE/.." "$HERE/../images" \
            "$HERE/../release-assets" "$HERE/../../release-assets"; do
  [ -d "$cand" ] || continue
  if [ -f "$cand/boot_permissive.img" ] && [ -f "$cand/system_diag6.img" ]; then
    IMG="$(cd "$cand" && pwd)"; break
  fi
done
[ -n "$IMG" ] || IMG="$HERE/images"   # 找不到则退回默认，由自检给出明确报错

BOOT_IMG="$IMG/boot_permissive.img"
SYSTEM_IMG="$IMG/system_diag6.img"
SYSTEM_EXT_IMG="$IMG/system_ext_fix2.img"

# SHA256SUMS.txt 同样多位置查找
find_sums() {
  local c
  for c in "$IMG/SHA256SUMS.txt" "$HERE/SHA256SUMS.txt" \
           "$HERE/../SHA256SUMS.txt" "$HERE/../release-assets/SHA256SUMS.txt" \
           "$HERE/../../release-assets/SHA256SUMS.txt"; do
    [ -f "$c" ] && { printf '%s' "$c"; return 0; }
  done
  return 1
}

# ---------------------------------------------------------------- 工具定位
find_tool() {
  local name="$1" p d
  # 注意：必须用数组。写成 `for p in $dirs/$name` 会在分词时错位
  # （$dirs 含空格 → 展开成 "/opt/homebrew/bin" 和 "/usr/local/bin/adb"），
  # 而且目录本身 `-x` 也为真，会把目录误当成可执行文件。
  local dirs
  if [ "$(uname -m)" = "arm64" ]; then
    dirs="/opt/homebrew/bin /usr/local/bin"
  else
    dirs="/usr/local/bin /opt/homebrew/bin"
  fi
  for d in $dirs "$HERE/../tools" "$HERE/../platform-tools" \
                  "$HOME/Documents/oppo/platform-tools" \
                  "$HOME/Library/Android/sdk/platform-tools"; do
    p="$d/$name"
    if [ -f "$p" ] && [ -x "$p" ]; then printf '%s' "$p"; return 0; fi
  done
  p="$(command -v "$name" 2>/dev/null || true)"
  if [ -n "$p" ] && [ -f "$p" ]; then printf '%s' "$p"; return 0; fi
  return 1
}

ADB="${ADB:-}"; FB="${FASTBOOT:-}"
if [ -z "$ADB" ]; then ADB="$(find_tool adb || true)"; fi
if [ -z "$FB" ];  then FB="$(find_tool fastboot || true)"; fi

# ---------------------------------------------------------------- 设备状态
# 输出: none | adb | recovery | fastboot | fastbootd
dev_state() {
  local s
  if [ -n "$FB" ]; then
    s="$("$FB" devices 2>/dev/null | awk 'NR==1{print $2}' | tr -d '\r')"
    case "$s" in
      fastboot)  printf 'fastboot';  return ;;
      fastbootd) printf 'fastbootd'; return ;;
    esac
  fi
  if [ -n "$ADB" ]; then
    s="$("$ADB" get-state 2>/dev/null | tr -d '\r')"
    case "$s" in
      device)   printf 'adb';      return ;;
      recovery) printf 'recovery'; return ;;
    esac
  fi
  printf 'none'
}

# 轮询等待目标状态，最多 tmo 秒
wait_state() {
  local want="$1" tmo="${2:-60}" i=0
  while [ "$i" -lt "$tmo" ]; do
    if [ "$(dev_state)" = "$want" ]; then printf '\n' >&2; return 0; fi
    sleep 2; i=$((i+2)); printf '.' >&2
  done
  printf '\n' >&2
  return 1
}

show_status() {
  local s; s="$(dev_state)"
  printf '  设备状态 : %s%s%s\n' "$BD" "$s" "$N"
  case "$s" in
    fastboot)  printf '  说明     : bootloader（只能刷 boot/dtbo/vbmeta 等物理分区）\n' ;;
    fastbootd) printf '  说明     : fastbootd（可刷 system/system_ext 等逻辑分区）\n' ;;
    recovery)  printf '  说明     : recovery（TWRP，adb 已具 root 权限）\n' ;;
    adb)       printf '  说明     : 已进入系统\n' ;;
    none)      printf '  说明     : 未检测到设备，请检查 USB 连接\n' ;;
  esac
}

# ---------------------------------------------------------------- 自检
do_check() {
  say "===== 环境自检 ====="
  printf '  架构     : %s\n' "$(uname -m)"
  printf '  系统     : macOS %s\n' "$(sw_vers -productVersion 2>/dev/null || echo '?')"
  [ -n "$ADB" ] && printf '  adb      : %s\n' "$ADB" || err "未找到 adb"
  [ -n "$FB" ]  && printf '  fastboot : %s\n' "$FB"  || err "未找到 fastboot"
  [ -n "$ADB" ] && [ -n "$FB" ] || return 1
  "$ADB" version 2>/dev/null | head -1 | sed 's/^/             /'
  "$FB" --version 2>/dev/null | head -1 | sed 's/^/             /'

  say "===== 镜像检查 ====="
  local missing=0 f
  for f in "$BOOT_IMG" "$SYSTEM_IMG" "$SYSTEM_EXT_IMG"; do
    if [ -f "$f" ]; then
      printf '  %s✓%s %-24s %s\n' "$G" "$N" "$(basename "$f")" "$(stat -f %z "$f") B"
    else
      err "缺失 $(basename "$f")"; missing=1
    fi
  done
  [ "$missing" -eq 0 ] || return 1

  if [ "${SKIP_HASH:-0}" = "1" ]; then
    warn "SKIP_HASH=1，跳过 sha256 校验"
  else
    say "===== sha256 校验 ====="
    sums="$(find_sums || true)"
    if [ -n "$sums" ]; then
      if ( cd "$IMG" && shasum -a 256 -c "$sums" ); then
        ok "全部镜像校验通过"
      else
        err "镜像校验失败，禁止刷入"; return 1
      fi
    else
      warn "无 SHA256SUMS.txt，跳过"
    fi
  fi
  return 0
}

# ---------------------------------------------------------------- 刷机
do_flash() {
  say "===== 开始刷机 ====="
  local s; s="$(dev_state)"
  printf '  当前状态 : %s\n' "$s"

  # ---- 阶段 0：把设备带到 bootloader ----
  case "$s" in
    none) die "未检测到设备。请确认 USB 已连接、已解锁 bootloader。" ;;
    adb|recovery)
      say "阶段 0/3：从 $s 重启到 bootloader"
      "$ADB" reboot bootloader >/dev/null 2>&1 || true
      if wait_state fastboot 90; then ok "已进入 bootloader"; else
        die "90 秒内未进入 bootloader（当前 $(dev_state)）"
      fi ;;
    fastbootd)
      say "阶段 0/3：从 fastbootd 退回 bootloader"
      "$FB" reboot bootloader >/dev/null 2>&1 || true
      if wait_state fastboot 90; then ok "已回到 bootloader"; else
        die "90 秒内未回到 bootloader（当前 $(dev_state)）"
      fi ;;
    fastboot) ok "已在 bootloader" ;;
  esac

  # ---- 阶段 1：刷 boot ----
  say "阶段 1/3：刷 boot"
  "$FB" flash boot "$BOOT_IMG" 2>&1 | sed 's/^/      /'
  [ "${PIPESTATUS[0]}" -eq 0 ] || die "boot 刷入失败"
  ok "boot 完成"

  # ---- 阶段 2：进入 fastbootd ----
  say "阶段 2/3：进入 fastbootd（system/system_ext 是 super 内的逻辑分区）"
  "$FB" reboot fastboot >/dev/null 2>&1 || true
  if wait_state fastbootd 120; then ok "已进入 fastbootd"; else
    die "120 秒内未进入 fastbootd（当前 $(dev_state)）。若卡在 bootloader，可手动执行: $FB reboot fastboot"
  fi

  # ---- 阶段 3：刷 system / system_ext ----
  say "阶段 3/3：刷 system / system_ext"
  local part src rc
  for pair in "system:$SYSTEM_IMG" "system_ext:$SYSTEM_EXT_IMG"; do
    part="${pair%%:*}"; src="${pair#*:}"
    printf '      写入 %-12s <- %s\n' "$part" "$(basename "$src")"
    "$FB" flash "$part" "$src" 2>&1 | sed 's/^/      /'
    rc="${PIPESTATUS[0]}"
    [ "$rc" -eq 0 ] || die "$part 刷入失败 (rc=$rc)"
    ok "$part 完成"
  done

  say "===== 刷机完成，重启到系统 ====="
  "$FB" reboot >/dev/null 2>&1 || true
  ok "已发出重启指令"
  echo
  printf '  预期：约 50 秒熄屏动画，约 55 秒后 %ssys.boot_completed=1%s\n' "$BD" "$N"
  printf '  验证：%s%s shell getprop sys.boot_completed%s\n' "$BD" "$ADB" "$N"
  printf '        %s%s shell getprop ro.build.version.release%s  (期望 16)\n' "$BD" "$ADB" "$N"
  echo
  printf '  %s提醒%s：若相机闪退，那是移植包相机与目标机配置不匹配所致，\n' "$Y" "$N"
  printf '        与本脚本无关，详见 docs/root-cause/ 下的相机说明。\n'
  printf '  %s提醒%s：刷完请务必安装 Magisk 模块\n' "$Y" "$N"
  printf '        magisk/aac-c2-preference-fix-v1.zip\n'
  printf '        否则第三方相机录像会“无法保存视频”。详见 README。\n'
}

# ---------------------------------------------------------------- main
case "${1:-}" in
  --check)  do_check ;;
  --status) show_status ;;
  --reboot)
    case "$(dev_state)" in
      adb|recovery) "$ADB" reboot; ok "已发出重启指令" ;;
      fastboot|fastbootd) "$FB" reboot; ok "已发出重启指令" ;;
      *) die "未检测到设备" ;;
    esac ;;
  --help|-h)
    sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'
    ;;
  *)
    do_check || die "自检未通过，已中止"
    echo
    do_flash
    ;;
esac
