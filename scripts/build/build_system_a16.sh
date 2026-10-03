#!/bin/bash
# ============================================================================
#  build_system_a16.sh —— 重建最终版 system.img（32 个 APEX 已去 pcluster + 重签）
#
#  输入:  07-重建/tree                    已解包的 A16 system 全树
#           └─ system/apex/*.apex          已由 apex_rebuild_all.py --install 换成新包
#           └─ system/etc/init/hw/init.rc  已改成 exec_start wb_netbpfload
#         07-重建/新增文件/netbpfload      NOP 掉内核门限的 netbpfload
#         07-重建/新增文件/wb_netbpfload.rc
#
#  输出:  02-移植素材/a16_patched/system_a16final.img
#
#  用法:  ./build_system_a16.sh [输出路径]
#
#  说明（为什么是这两个 --add）:
#    A16 原本 /system/etc/init/netbpfload.rc 里是
#        service bpfloader /system/bin/false ... updatable
#    带 updatable ⇒ 会被 tethering APEX 内的 rc 覆盖成
#        /apex/com.android.tethering/bin/netbpfload   （含 "25Q2 requires kernel 5.4" 硬门限）
#    该服务带 reboot_on_failure ⇒ 门限命中就是 bootloop。
#    做法：把 NOP 过的二进制放到 /system/bin/netbpfload，init.rc 的
#    on load-bpf-programs 改 exec_start wb_netbpfload，绕开被 APEX 覆盖的名字；
#    新服务【不带】reboot_on_failure，万一失败也只是静默失败，便于定位。
#    SELinux 标签模板：#bin/bpfloader 给 bpfloader_exec，#etc/init/hw/init.rc 给 system_file。
# ============================================================================
set -u

PROJ="$HOME/Documents/oppo/2026-安卓16"
PY=/Users/skeletondie/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3
REBUILD="$PROJ/03-脚本/erofs_rebuild.py"

SRC="$PROJ/02-移植素材/a16_patched/system.img"     # 只借它的元数据（与原版一致）
TREE="$PROJ/07-重建/tree"
NEW="$PROJ/07-重建/新增文件"
OUT="${1:-$PROJ/02-移植素材/a16_patched/system_a16final.img}"

C_R=$'\033[31m'; C_G=$'\033[32m'; C_Y=$'\033[33m'; C_B=$'\033[36m'; C_0=$'\033[0m'
say()  { echo "${C_B}[$(date +%H:%M:%S)]${C_0} $*"; }
ok()   { echo "  ${C_G}✓${C_0} $*"; }
warn() { echo "  ${C_Y}⚠${C_0} $*"; }
err()  { echo "  ${C_R}✗${C_0} $*"; }

for f in "$SRC" "$TREE" "$NEW/netbpfload" "$NEW/wb_netbpfload.rc" "$REBUILD"; do
  [ -e "$f" ] || { err "缺输入: $f"; exit 1; }
done

say "源元数据: $SRC"
say "目录树  : $TREE"
say "输出    : $OUT"

# ---- 构建前自检: 树里的 APEX 必须都已替换成 incompat=0x1 的新包 ----
say "步骤 1/3: 构建前自检（树里 32 个 APEX 是否都已是新包）"
"$PY" - "$TREE" <<'PYEOF'
import os, struct, sys, zipfile
tree = sys.argv[1]
apexdir = os.path.join(tree, "system", "apex")
old = []
n = 0
for fn in sorted(os.listdir(apexdir)):
    if not fn.endswith(".apex"):
        continue
    with zipfile.ZipFile(os.path.join(apexdir, fn)) as z:
        names = [x for x in z.namelist() if x.endswith("apex_payload.img")]
        if not names:
            continue
        b = z.read(names[0])[:1120]
    if len(b) < 1100 or struct.unpack_from("<I", b, 1024)[0] != 0xE0F5E1E2:
        continue                      # 非 EROFS（cts.shim），不受影响
    inc = struct.unpack_from("<I", b, 1024 + 80)[0]
    n += 1
    if inc != 0x1:
        old.append("%s(incompat=%#x)" % (fn, inc))
print("  EROFS 类型 APEX: %d 个" % n)
if old:
    print("  ✗ 仍有旧包（未替换）: %s" % ", ".join(old))
    sys.exit(1)
print("  ✓ 全部已是 incompat=0x1")
PYEOF
[ $? -eq 0 ] || { err "自检失败，先跑 apex_rebuild_all.py --install"; exit 1; }

# ---- 重建 ----
# ★ 路径必须是 /system/bin/... 而不是 /bin/...！
#   SAR 布局里根目录的 `bin` 是【符号链接】→ /system/bin。
#   往 `bin/netbpfload` 加文件时 mkfs.erofs 会报
#       <E> erofs: Could not format the device : [Error 20] Not a directory
#   因为符号链接下面不可能有真实目录项。同理 xattr 模板要用 system/bin/bpfloader。
say "步骤 2/3: erofs_rebuild 重建 system.img（-zlz4hc,level=9，不含 -C）"
"$PY" "$REBUILD" "$SRC" "$TREE" "$OUT" \
  --zopts "-zlz4hc,level=9" \
  --add "/system/bin/netbpfload=$NEW/netbpfload#system/bin/bpfloader" \
  --addmode "/system/bin/netbpfload=750" \
  --add "/system/etc/init/wb_netbpfload.rc=$NEW/wb_netbpfload.rc#system/etc/init/hw/init.rc"
rc=$?
[ $rc -eq 0 ] || { err "erofs_rebuild 失败 rc=$rc"; exit 1; }

# ---- 收尾自检 ----
say "步骤 3/3: 收尾自检"
"$PY" - "$OUT" <<'PYEOF'
import os, struct, sys
img = sys.argv[1]
sz = os.path.getsize(img)
with open(img, "rb") as f:
    f.seek(1024); b = f.read(96)
# EROFS 超级块: 0 magic / 4 checksum / 8 feature_compat / 12 blkszbits /
#               14 root_nid / 16 inos(u64) / 36 blocks / 80 feature_incompat / 84 u1
magic, checksum, compat = struct.unpack_from("<III", b, 0)
blkszbits = b[12]
inos = struct.unpack_from("<Q", b, 16)[0]
blocks = struct.unpack_from("<I", b, 36)[0]
incompat = struct.unpack_from("<I", b, 80)[0]
u1 = struct.unpack_from("<H", b, 84)[0]
print("  镜像 %d 字节" % sz)
print("  超级块: magic=%#x checksum=%#x compat=%#x incompat=%#x blkszbits=%d"
      % (magic, checksum, compat, incompat, blkszbits))
print("          blocks=%d inos=%d u1=%d" % (blocks, inos, u1))
bad = []
if magic != 0xE0F5E1E2:
    bad.append("magic 不对")
if incompat != 0x1:
    bad.append("incompat=%#x（应为 0x1）" % incompat)
if compat != 0x7:
    bad.append("compat=%#x（应为 0x7）" % compat)
if blkszbits != 12:
    bad.append("blkszbits=%d（应为 12 = 4096B）" % blkszbits)
if sz % 4096:
    bad.append("大小不是 4096 的整数倍")
for x in bad:
    print("  ✗ " + x)
if bad:
    sys.exit(1)
print("  ✓ 超级块关键字段正常")
PYEOF
[ $? -eq 0 ] || { err "收尾自检失败"; exit 1; }

FSCK=/Users/skeletondie/.workbuddy-ai/tools/erofs/erofs-utils/1.9.4/bin/fsck.erofs
say "fsck.erofs 全量校验…"
"$FSCK" "$OUT" >/dev/null 2>&1
if [ $? -eq 0 ]; then ok "fsck.erofs 干净通过"; else err "fsck.erofs 报错"; exit 1; fi

ok "完成: $OUT"
say "下一步: ./reflash_system.sh \"$OUT\""
