#!/bin/bash
# ============================================================================
#  flash_coloros16.sh —— 把 ColorOS 16 (Android 16, 一加 8T 供体) 刷入
#                        OPPO Find X2 Pro (PDEM30)
#
#  ⚠️  高危操作。设备为 A-only、无 B 槽，刷坏只能靠 fastboot / EDL 救。
#      刷机前务必确认：
#        1. 手机已解锁 BL
#        2. 电量 > 60%
#        3. 已用 ColorOS15 回滚包做过演练（至少确认 fastboot 能识别）
#        4. 本脚本已完整读过一遍
#
#  用法:
#     ./flash_coloros16.sh --check          # 只做前置检查, 不刷
#     ./flash_coloros16.sh --flash          # 真正刷机
#     ./flash_coloros16.sh --rollback       # 回滚到 ColorOS 15
# ============================================================================
set -u

# ---------- 路径配置 ----------
PT="$HOME/Documents/oppo/platform-tools"
ADB="$PT/adb"
FB="$PT/fastboot"
DONOR="$HOME/Documents/oppo/donor_8t/extracted"          # 8T A16 原镜像
PATCHED="$HOME/Documents/oppo/2026-安卓16/02-移植素材/a16_patched"   # 打过补丁的镜像
ROLLBACK_ZIP="$PT/FindX2Pro_ColorOS_15.0.2_Pro_2025.1_20250823_By_Color597.zip"

# ★ system 用最终重建版: 32 个 APEX 已重签为 EROFS incompat=0x1(4.19 可挂),
#   并注入 /system/bin/netbpfload(内核门限已 NOP) + wb_netbpfload.rc。
#   system.img 是 v1(旧 APEX, incompat=0x3) —— 刷了必然还在 54s 掉 fastboot。
SYSIMG="system_a16final.img"
SYSIMG_SIZE=1063677952

# ---------- 待刷分区表: "分区名:镜像文件:目标大小(字节)" ----------
# system 用重建版(烘入了 netbpfload 补丁), 其余用 8T 原版。
# vendor / odm 保持 X2 Pro 原样, 不刷。
PARTITIONS=(
  "system:$SYSIMG:$SYSIMG_SIZE"
  "system_ext:system_ext.img:979513344"
  "product:product.img:9703424"
  "my_product:my_product.img:1442086912"
  "my_stock:my_stock.img:4385828864"
  "my_company:my_company.img:4096"
  "my_region:my_region.img:3411968"
  "my_bigball:my_bigball.img:4096"
  "my_carrier:my_carrier.img:4096"
  "my_heytap:my_heytap.img:4096"
  "my_preload:my_preload.img:8192"
  "my_engineering:my_engineering.img:12288"
  "my_manifest:my_manifest.img:638976"
)

# ---------- 需要新建的分区: "分区名:大小(字节)" ----------
# 设备当前是 LineageOS GSI 布局, 没有 my_stock; 8T 的 ColorOS 需要它。
CREATE=(
  "my_stock:4385828864"
)

# ---------- 需要调整尺寸的分区: "分区名:新大小(字节)" ----------
# 只列【镜像 > 当前分区】必须扩容的。其余一律不动, 减少操作面。
RESIZE=(
  "product:9703424"          #     9,322,496 ->   9,703,424
  "my_product:1442086912"    # 1,416,716,288 -> 1,442,086,912
  "my_region:3411968"        #     2,351,104 ->   3,411,968
  "my_manifest:638976"       #       520,192 ->     638,976
)

# 8T 没有的分区 —— 删掉释放空间, 也让布局与供体一致
DELETE_PARTS=("my_colorospro")     # 释放 589,312,000 字节

# 不刷、保留 X2 Pro 自己的: vendor odm boot dtbo vbmeta 及全部固件分区
#
# 空间账 (qti_dynamic_partitions 上限 9,927,917,568):
#   操作前占用  4,937,031,680
#   删除        -589,312,000
#   新建 my_stock  +4,385,828,864
#   4 处扩容      +26,931,200
#   ------------------------------------
#   操作后占用  8,760,479,744   余量 1,167,437,824 ✓

C_R=$'\033[31m'; C_G=$'\033[32m'; C_Y=$'\033[33m'; C_B=$'\033[36m'; C_0=$'\033[0m'
say()  { echo "${C_B}[$(date +%H:%M:%S)]${C_0} $*"; }
ok()   { echo "  ${C_G}✓${C_0} $*"; }
warn() { echo "  ${C_Y}⚠${C_0} $*"; }
err()  { echo "  ${C_R}✗${C_0} $*"; }

# ============================================================================
precheck() {
  say "===== 前置检查 ====="
  local fail=0

  [ -x "$ADB" ] && ok "adb 存在" || { err "缺 adb: $ADB"; fail=1; }
  [ -x "$FB" ]  && ok "fastboot 存在" || { err "缺 fastboot: $FB"; fail=1; }

  say "检查待刷镜像"
  for e in "${PARTITIONS[@]}"; do
    IFS=':' read -r part img size <<< "$e"
    local src="$DONOR/$img"
    [ "$part" = "system" ] && src="$PATCHED/$SYSIMG"
    if [ ! -f "$src" ]; then err "缺镜像 $src"; fail=1; continue; fi
    local actual; actual=$(stat -f%z "$src")
    if [ "$actual" != "$size" ]; then
      warn "$part: 镜像 $actual 字节 ≠ 预期 $size 字节"
    else
      ok "$part  $img  $actual 字节"
    fi
  done

  say "检查 vendor / odm (X2 Pro 侧, 保留不刷)"
  for f in vendor.img odm.img; do
    [ -f "$HOME/Documents/oppo/2026-安卓16/02-移植素材/x2pro_cos15/$f" ] \
      && ok "$f 已备份" || warn "$f 未找到备份"
  done

  say "检查回滚包"
  [ -f "$ROLLBACK_ZIP" ] && ok "ColorOS15 回滚包存在" || { err "缺回滚包!"; fail=1; }

  say "检查设备连接"
  "$ADB" devices | tail -n +2 | grep -q "device$" && ok "adb 已连" || warn "adb 未连接设备"
  "$FB" devices 2>/dev/null | grep -q . && ok "fastboot 已识别" || warn "fastboot 未识别设备"

  echo
  [ $fail -eq 0 ] && ok "前置检查通过" || err "前置检查有失败项, 请先解决"
  return $fail
}

# ============================================================================
do_flash() {
  echo
  echo "${C_R}================ 即将刷入 ColorOS 16 ================${C_0}"
  echo "  将刷入 ${#PARTITIONS[@]} 个分区, 删除 ${#DELETE_PARTS[@]} 个分区"
  echo "  保留设备现有: vendor / odm / boot / dtbo / vbmeta / 全部固件"
  echo
  echo "${C_Y}  风险: A-only 设备, 无 B 槽, 刷坏需 fastboot/EDL 救砖${C_0}"
  echo
  read -r -p "  确认继续? 输入大写 YES: " ans
  [ "$ans" = "YES" ] || { err "已取消"; exit 1; }

  say "步骤 1/4: 重启进入 fastboot"
  "$ADB" reboot bootloader
  sleep 8
  # ★ fastboot 36.0.1 没有 wait-for-device 子命令（那是 adb 的），改用轮询
  i=0
  until "$FB" devices 2>/dev/null | grep -q .; do
    sleep 2; i=$((i+1)); [ $i -gt 40 ] && { echo "  等待 bootloader 超时"; return 1; }
  done
  ok "已进入 bootloader"

  say "步骤 2/4: 进入 fastbootd (用户空间, 才能操作逻辑分区)"
  "$FB" reboot fastboot
  sleep 10
  local i=0
  until "$FB" devices 2>/dev/null | grep -q .; do
    sleep 2; i=$((i+1)); [ $i -gt 30 ] && { err "fastbootd 超时"; exit 1; }
  done
  ok "已进入 fastbootd"

  say "步骤 3/4: 调整分区布局 (先释放 → 再新建 → 最后扩容)"
  for p in "${DELETE_PARTS[@]}"; do
    say "  删除逻辑分区 $p"
    "$FB" delete-logical-partition "$p" 2>&1 | sed 's/^/    /'
  done
  for e in "${CREATE[@]}"; do
    IFS=':' read -r part size <<< "$e"
    say "  新建逻辑分区 $part -> $size"
    "$FB" create-logical-partition "$part" "$size" 2>&1 | sed 's/^/    /'
  done
  for e in "${RESIZE[@]}"; do
    IFS=':' read -r part size <<< "$e"
    say "  resize $part -> $size"
    "$FB" resize-logical-partition "$part" "$size" 2>&1 | sed 's/^/    /'
  done

  say "  复核布局"
  for e in "${CREATE[@]}" "${RESIZE[@]}"; do
    IFS=':' read -r part size <<< "$e"
    got=$("$FB" getvar partition-size:"$part" 2>&1 | grep -o '[0-9]*' | tail -1)
    if [ "$got" = "$size" ]; then ok "$part = $got"
    else warn "$part 期望 $size 实得 $got"; fi
  done
  "$FB" getvar partition-size:my_colorospro 2>&1 | grep -q "0x0\|0" \
    && ok "my_colorospro 已删除" || warn "my_colorospro 可能仍存在"
  ok "布局调整完成 (system 保持不动)"

  say "步骤 4/4: 刷入镜像"
  for e in "${PARTITIONS[@]}"; do
    IFS=':' read -r part img size <<< "$e"
    local src="$DONOR/$img"
    [ "$part" = "system" ] && src="$PATCHED/$SYSIMG"
    say "  flash $part  <- $src"
    # 注意: 不能用 `if cmd | sed ...` 判断成败 —— 退出码来自 sed, 恒为 0。
    # 必须取 PIPESTATUS[0]。另外 fastboot 对非 sparse 的原始镜像会打印
    # "Invalid sparse file format at header magic" 后回退裸传, 这是正常的。
    "$FB" flash "$part" "$src" 2>&1 | sed 's/^/    /'
    rc=${PIPESTATUS[0]}
    if [ "$rc" -eq 0 ]; then
      ok "$part 完成"
    else
      err "$part 失败 (rc=$rc) —— 停止!"
      exit 1
    fi
  done

  echo
  ok "全部刷入完成"
  say "重启到系统"
  "$FB" reboot
  echo
  echo "  首次开机可能较慢 (5-15 分钟), 属正常。"
  echo "  如出现无限重启, 参考报告里的『故障处置』一节。"
}

# ============================================================================
do_rollback() {
  echo
  echo "${C_Y}================ 回滚到 ColorOS 15 ================${C_0}"
  echo "  回滚包: $(basename "$ROLLBACK_ZIP")"
  echo "  方式: 用 TWRP 刷入该 zip (它是完整 OTA, 会重建分区布局)"
  echo
  echo "  手动步骤:"
  echo "    1. fastboot flash boot   $PT/TWRP-13-Compass-Color597-V2.0.img"
  echo "    2. fastboot reboot recovery"
  echo "    3. 在 TWRP 里刷入: $ROLLBACK_ZIP"
  echo
  read -r -p "  现在自动执行步骤 1-2? 输入 YES: " ans
  [ "$ans" = "YES" ] || exit 0
  "$ADB" reboot bootloader
  sleep 8
  # ★ fastboot 36.0.1 没有 wait-for-device 子命令（那是 adb 的），改用轮询
  i=0
  until "$FB" devices 2>/dev/null | grep -q .; do
    sleep 2; i=$((i+1)); [ $i -gt 40 ] && { echo "  等待 bootloader 超时"; return 1; }
  done
  "$FB" flash boot "$PT/TWRP-13-Compass-Color597-V2.0.img"
  "$FB" reboot recovery
  ok "已进入 TWRP, 请在 TWRP 里手动刷入回滚包"
}

# ============================================================================
case "${1:-}" in
  --check)    precheck ;;
  --flash)    precheck || exit 1; do_flash ;;
  --rollback) do_rollback ;;
  *) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//' ;;
esac
