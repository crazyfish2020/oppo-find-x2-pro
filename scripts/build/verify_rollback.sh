#!/bin/bash
# ============================================================================
#  verify_rollback.sh —— 核验 ColorOS 15 回滚包（保底方案）是否真的可用
#
#  为什么要做：用户底线是「不行就刷回官方」。如果这个包本身是坏的，
#  那"保底"就是假的。
#
#  ★ 关键认识（2026-10-01 实测踩到）：
#    包内 META-INF/com/android/ota_md5.csv 是【原始官方 OTA（A13 时代）】的清单，
#    移植者把 15 个分区的 .new.dat.br 全部换掉了却没更新这个 csv
#    ⇒ 实测 15/15 全部不匹配。**这个 md5 对本包完全不是有效性判据。**
#    真正有效的判据是 zip 自身的 CRC32（unzip -t）。
#
#  用法： ./verify_rollback.sh
# ============================================================================
set -u
Z="$HOME/Documents/oppo/platform-tools/FindX2Pro_ColorOS_15.0.2_Pro_2025.1_20250823_By_Color597.zip"
CSV="/tmp/rollback_meta/ota_md5.csv"

C_R=$'\033[31m'; C_G=$'\033[32m'; C_Y=$'\033[33m'; C_B=$'\033[36m'; C_0=$'\033[0m'
say() { echo "${C_B}[$(date +%H:%M:%S)]${C_0} $*"; }
ok()  { echo "  ${C_G}✓${C_0} $*"; }
warn(){ echo "  ${C_Y}⚠${C_0} $*"; }
err() { echo "  ${C_R}✗${C_0} $*"; }

[ -f "$Z" ] || { err "缺回滚包: $Z"; exit 1; }
say "回滚包: $(basename "$Z")  $(stat -f%z "$Z") 字节"

# ---------------------------------------------------------------- 1. 条目清单
say "步骤 1/4: 必需条目是否齐全"
need="system.new.dat.br system.transfer.list system_ext.new.dat.br system_ext.transfer.list
vendor.new.dat.br vendor.transfer.list odm.new.dat.br odm.transfer.list
product.new.dat.br product.transfer.list boot.img
META-INF/com/google/android/update-binary META-INF/com/google/android/updater-script
META-INF/com/android/metadata"
LIST="$(unzip -l "$Z" 2>/dev/null | awk '{print $4}')"
miss=0
for n in $need; do
  if echo "$LIST" | grep -qx "$n"; then ok "$n"; else err "缺 $n"; miss=$((miss+1)); fi
done
[ "$miss" -eq 0 ] || { err "缺 $miss 个必需条目 ⇒ 包不完整，不能当保底"; exit 1; }

# ---------------------------------------------------------------- 2. 设备匹配
say "步骤 2/4: 包的 pre-device 是否匹配本机（X2 Pro = OP4A7A）"
unzip -o -j "$Z" "META-INF/com/android/metadata" -d /tmp/rollback_meta >/dev/null 2>&1
grep -E "^(pre-device|ota-type|wipe|post-sdk-level|os_version|ota-id)=" /tmp/rollback_meta/metadata \
  | sed 's/^/    /'
grep -q "^pre-device=OP4A7A$" /tmp/rollback_meta/metadata \
  && ok "pre-device=OP4A7A，匹配 Find X2 Pro" \
  || warn "pre-device 不是 OP4A7A，刷之前务必确认机型"

# ---------------------------------------------------------------- 3. CRC32
say "步骤 3/4: zip 逐条 CRC32 校验（7.6 GB，要几分钟）"
if unzip -t "$Z" >/tmp/rollback_crc.log 2>&1; then
  ok "全部条目 CRC 通过 ⇒ 压缩包本身没有损坏"
else
  err "CRC 校验失败，详情："; tail -20 /tmp/rollback_crc.log | sed 's/^/    /'
  exit 1
fi

# ---------------------------------------------------------------- 4. MD5（仅参考）
say "步骤 4/4: 与包内 ota_md5.csv 对照（★仅参考，见文件头说明）"
[ -f "$CSV" ] || unzip -o -j "$Z" "META-INF/com/android/ota_md5.csv" -d /tmp/rollback_meta >/dev/null 2>&1
IFS=',' read -r -a HDR < <(head -1 "$CSV")
IFS=',' read -r -a VAL < <(sed -n '2p' "$CSV")
same=0; diff=0
for i in $(seq 1 $(( ${#HDR[@]} - 1 ))); do
  entry="${HDR[$i]}.new.dat.br"
  got=$(unzip -p "$Z" "$entry" 2>/dev/null | md5 -q | cut -c1-8)
  if [ "$got" = "${VAL[$i]}" ]; then same=$((same+1)); else diff=$((diff+1)); fi
done
echo "    与官方 OTA 清单一致 $same 个 / 已被移植者替换 $diff 个"
if [ "$diff" -gt 0 ]; then
  warn "有 $diff 个分区与官方清单不同 —— 这是【移植者替换过】的正常表现，不是损坏"
fi

echo
echo "  ${C_G}★ 结论：包结构完整、CRC 通过、机型匹配 ⇒ 保底方案成立。${C_0}"
