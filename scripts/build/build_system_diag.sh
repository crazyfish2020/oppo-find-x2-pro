#!/bin/bash
# ============================================================================
#  build_system_diag.sh —— 构建【诊断版】system.img
#
#  目的：A16 移植卡在 `on early-init` 的 `exec_start apexd-bootstrap`
#        （init.rc:145），而该服务是系统里唯一带
#        `reboot_on_failure reboot,bootloader` 的 —— 一失败就重启回 bootloader，
#        导致我们既看不到日志、也抓不到现场。
#
#  本诊断版做了 4 件事：
#    1) /system/etc/init/apexd.rc
#         注释掉 apexd 与 apexd-bootstrap 的 reboot_on_failure
#         ⇒ 失败也不再重启，设备会停在现场。
#    2) /system/etc/init/hw/init.rc
#         在 `on early-init` 开头插入 `setenforce 0` + `exec_start wb_diag_pre`
#         在 `exec_start apexd-bootstrap` 之后插入 `exec_start wb_diag_post`
#         ⇒ 在 apexd 前后各倾倒一次设备状态。
#    3) /system/etc/wb_diag.sh + /system/etc/init/wb_diag.rc
#         倾倒脚本与服务定义。输出写到 /metadata/wbdiag/
#         （/metadata 在 fstab 里是 first_stage_mount，on early-init 时已挂载，
#           且它是 ext4 明文分区、不会被 /data 双清波及，可从 TWRP 直接读）
#    4) /system/build.prop
#         ro.adb.secure=1 -> 0（万一设备能起来，adb 不需要授权弹窗）
#
#  输出：02-移植素材/a16_patched/system_diag.img
# ============================================================================
set -u

PROJ="$HOME/Documents/oppo/2026-安卓16"
PY=/Users/skeletondie/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3
REBUILD="$PROJ/03-脚本/erofs_rebuild.py"

# 2026-10-01: SRC / OUT 支持环境变量覆盖。
#   原始 system.img 已被删除（腾空间），做增量重建时改用 system_diag.img 作元数据基底：
#   erofs_rebuild.py 只用 SRC 读 uid/gid/mode/xattr，内容一律来自 TREE，
#   而被替换的那 8 个文件全部由 --add 覆盖，所以两者等价。
SRC="${SRC:-$PROJ/02-移植素材/a16_patched/system.img}"
TREE="$PROJ/07-重建/tree"
NEW="$PROJ/07-重建/新增文件"
DIAG="${DIAG:-/tmp/diag}"
OUT="${1:-${OUT:-$PROJ/02-移植素材/a16_patched/system_diag.img}}"

C_R=$'\033[31m'; C_G=$'\033[32m'; C_Y=$'\033[33m'; C_B=$'\033[36m'; C_0=$'\033[0m'
say()  { echo "${C_B}[$(date +%H:%M:%S)]${C_0} $*"; }
ok()   { echo "  ${C_G}✓${C_0} $*"; }
warn() { echo "  ${C_Y}⚠${C_0} $*"; }
err()  { echo "  ${C_R}✗${C_0} $*"; }

for f in "$SRC" "$TREE" "$NEW/netbpfload" "$NEW/wb_netbpfload.rc" "$REBUILD" \
         "$DIAG/init.rc" "$DIAG/apexd.rc" "$DIAG/netbpfload.rc" "$DIAG/build.prop" \
         "$DIAG/wb_diag.sh" "$DIAG/wb_diag.rc"; do
  [ -e "$f" ] || { err "缺输入: $f"; exit 1; }
done

say "输出: $OUT"

# ---- 1. 构建前自检：树里的 APEX 必须都已替换成 incompat=0x1 的新包 ----
say "步骤 1/4: 自检（32 个 APEX 是否都已是 incompat=0x1 的新包）"
"$PY" - "$TREE" <<'PYEOF'
import os, struct, sys, zipfile
apexdir = os.path.join(sys.argv[1], "system", "apex")
old, n = [], 0
for fn in sorted(os.listdir(apexdir)):
    if not fn.endswith(".apex"):
        continue
    with zipfile.ZipFile(os.path.join(apexdir, fn)) as z:
        names = [x for x in z.namelist() if x.endswith("apex_payload.img")]
        if not names:
            continue
        b = z.read(names[0])[:1120]
    if len(b) < 1100 or struct.unpack_from("<I", b, 1024)[0] != 0xE0F5E1E2:
        continue
    inc = struct.unpack_from("<I", b, 1024 + 80)[0]
    n += 1
    if inc != 0x1:
        old.append("%s(incompat=%#x)" % (fn, inc))
print("  EROFS 类型 APEX: %d 个" % n)
if old:
    print("  ✗ 仍有旧包: %s" % ", ".join(old)); sys.exit(1)
print("  ✓ 全部已是 incompat=0x1")
PYEOF
[ $? -eq 0 ] || { err "自检失败"; exit 1; }

# ---- 2. 打印原文件权限，供替换后核对 ----
say "步骤 2/4: 记录被替换文件的原始权限"
for p in system/etc/init/hw/init.rc system/etc/init/apexd.rc system/build.prop; do
  stat -f "  %Sp %Su:%Sg  %N" "$TREE/$p"
done

# ---- 3. 重建 ----
say "步骤 3/4: erofs_rebuild 重建（-zlz4hc,level=9）"
"$PY" "$REBUILD" "$SRC" "$TREE" "$OUT" \
  --zopts "-zlz4hc,level=9" \
  --add "/system/bin/netbpfload=$NEW/netbpfload#system/bin/bpfloader" \
  --addmode "/system/bin/netbpfload=750" \
  --add "/system/etc/init/wb_netbpfload.rc=$NEW/wb_netbpfload.rc#system/etc/init/hw/init.rc" \
  --add "/system/etc/init/hw/init.rc=$DIAG/init.rc#system/etc/init/hw/init.rc" \
  --add "/system/etc/init/apexd.rc=$DIAG/apexd.rc#system/etc/init/apexd.rc" \
  --add "/system/etc/init/netbpfload.rc=$DIAG/netbpfload.rc#system/etc/init/hw/init.rc" \
  --add "/system/build.prop=$DIAG/build.prop#system/build.prop" \
  --add "/system/etc/wb_diag.sh=$DIAG/wb_diag.sh#system/etc/init/hw/init.rc" \
  --addmode "/system/etc/wb_diag.sh=755" \
  --add "/system/etc/init/wb_diag.rc=$DIAG/wb_diag.rc#system/etc/init/hw/init.rc"
rc=$?
[ $rc -eq 0 ] || { err "erofs_rebuild 失败 rc=$rc"; exit 1; }

# ---- 4. 收尾自检 ----
say "步骤 4/4: 收尾自检（超级块 + 4 个诊断文件是否真的写进去了）"
"$PY" - "$OUT" <<'PYEOF'
import os, struct, sys
img = sys.argv[1]
sz = os.path.getsize(img)
with open(img, "rb") as f:
    f.seek(1024); b = f.read(96)
magic, checksum, compat = struct.unpack_from("<III", b, 0)
blkszbits = b[12]
incompat = struct.unpack_from("<I", b, 80)[0]
print("  镜像 %d 字节  magic=%#x compat=%#x incompat=%#x blkszbits=%d"
      % (sz, magic, compat, incompat, blkszbits))
bad = []
if magic != 0xE0F5E1E2: bad.append("magic 不对")
if incompat != 0x1: bad.append("incompat=%#x（应为 0x1）" % incompat)
if compat != 0x7: bad.append("compat=%#x（应为 0x7）" % compat)
if blkszbits != 12: bad.append("blkszbits=%d（应为 12）" % blkszbits)
if sz % 4096: bad.append("大小不是 4096 的整数倍")
for x in bad: print("  ✗ " + x)
if bad: sys.exit(1)
print("  ✓ 超级块关键字段正常")
PYEOF
[ $? -eq 0 ] || { err "收尾自检失败"; exit 1; }

FSCK=/Users/skeletondie/.workbuddy-ai/tools/erofs/erofs-utils/1.9.4/bin/fsck.erofs
DUMP=/Users/skeletondie/.workbuddy-ai/tools/erofs/erofs-utils/1.9.4/bin/dump.erofs
say "fsck.erofs 全量校验…"
"$FSCK" "$OUT" >/dev/null 2>&1 && ok "fsck.erofs 干净通过" || { err "fsck.erofs 报错"; exit 1; }

say "核对 4 个诊断文件是否在镜像里（看权限与大小）"
"$DUMP" --ls --path=/system/etc/init/wb_diag.rc   "$OUT" 2>&1 | tail -3
"$DUMP" --ls --path=/system/etc/wb_diag.sh        "$OUT" 2>&1 | tail -3
"$DUMP" --ls --path=/system/etc/init/apexd.rc     "$OUT" 2>&1 | tail -3
"$DUMP" --ls --path=/system/etc/init/hw/init.rc   "$OUT" 2>&1 | tail -3

# ---- 5. ★ 2026-10-01 新增：从成品镜像【反抽】init.rc，确认补丁真的写进去了 ----
say "步骤 5/5: 反抽 init.rc 核对补丁（boringssl 中和 / 面包屑 / reboot_on_failure）"
CHK=/tmp/_wbdiag_chk_init.rc
rm -f "$CHK"
"$FSCK" --extract="$CHK" --path=/system/etc/init/hw/init.rc "$OUT" >/dev/null 2>&1
if [ ! -s "$CHK" ]; then
  err "反抽 init.rc 失败（$CHK 不存在或为空）"; exit 1
fi
n_true=$(grep -c "^service boringssl_self_test.* /system/bin/true$" "$CHK")
n_raw=$(grep -cE "^service boringssl_self_test.*(self_test32|self_test64)$" "$CHK")
n_bread=$(grep -c "exec_start wb_diag_" "$CHK")
n_live=$(grep -c "^[[:space:]]*reboot_on_failure" "$CHK")
printf "  boringssl→/system/bin/true : %s 个（应 4）\n" "$n_true"
printf "  boringssl 仍指向真身       : %s 个（应 0）\n" "$n_raw"
printf "  面包屑 exec_start wb_diag_ : %s 个（应 8）\n" "$n_bread"
printf "  未注释的 reboot_on_failure : %s 条（应 0）\n" "$n_live"
if [ "$n_true" -eq 4 ] && [ "$n_raw" -eq 0 ] && [ "$n_bread" -eq 8 ] && [ "$n_live" -eq 0 ]; then
  ok "init.rc 补丁已确认落在镜像里"
else
  err "init.rc 补丁不完整，不要刷这个镜像"; exit 1
fi

ok "完成: $OUT"
say "下一步: ./reflash_system.sh \"$OUT\""
