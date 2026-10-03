#!/bin/bash
# ============================================================================
#  build_system_ext_fix.sh —— 修复 system_ext 里唯一漏掉的 APEX
#
#  背景
#  ====
#  /system 分区里那 33 个 APEX 已经全部重建为 incompat=0x1（4.19.157 可挂）。
#  但 **system_ext 分区** 是从 8T 供体原样刷入的，它里面还有一个：
#
#      /apex/com.android.compos.apex   →  payload incompat=0x3（含 COMPR_CFGS）
#                                          ⇒ 4.19.157 内核挂不上
#
#  （另一个 /apex/com.android.vndk.v30.apex 的 payload 是 ext4，magic=0x260，
#    不受 COMPR_CFGS 影响，保持原样。）
#
#  做法
#  ====
#  0. 用 fsck.erofs --extract --xattrs 解包 system_ext 目录树
#     ★ 必须带 --xattrs，否则 3800+ 个文件全部丢 security.selinux 标签
#  1. 从 donor 的 system_ext.img 里抽出 com.android.compos.apex
#  2. apex_rebuild.py：payload 重建为 incompat=0x1 + 自备密钥重签 AVB + 换 apex_pubkey
#  3. 校验新包（EROFS incompat + AVB 哈希树根摘要）
#  4. erofs_rebuild.py 重建 system_ext.img，替换这一个 APEX
#  5. 校验新镜像（超级块 + fsck + 文件清单比对 + 抽回该 APEX 再验一次）
#
#  输出：02-移植素材/a16_patched/system_ext_fix.img
# ============================================================================
set -u

PROJ="$HOME/Documents/oppo/2026-安卓16"
DONOR="$HOME/Documents/oppo/donor_8t/extracted"
PY=/Users/skeletondie/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3
EROFSTOOL=/Users/skeletondie/.workbuddy-ai/tools/erofs/erofs-utils/1.9.4/bin

SRC="$DONOR/system_ext.img"
TREE="$PROJ/07-重建/tree_system_ext"
KEY="$PROJ/07-重建/_avb/apex_key.pem"
APEX_REBUILD="$PROJ/03-脚本/apex_rebuild.py"
EROFS_REBUILD="$PROJ/03-脚本/erofs_rebuild.py"
APEX_AVB="$PROJ/03-脚本/apex_avb_dump.py"
OUT="${1:-$PROJ/02-移植素材/a16_patched/system_ext_fix.img}"

WORK=/tmp/se_fix
TARGET=/apex/com.android.compos.apex
TARGET_NOSLASH=apex/com.android.compos.apex

C_R=$'\033[31m'; C_G=$'\033[32m'; C_Y=$'\033[33m'; C_B=$'\033[36m'; C_0=$'\033[0m'
say()  { echo "${C_B}[$(date +%H:%M:%S)]${C_0} $*"; }
ok()   { echo "  ${C_G}✓${C_0} $*"; }
warn() { echo "  ${C_Y}⚠${C_0} $*"; }
err()  { echo "  ${C_R}✗${C_0} $*"; }

for f in "$SRC" "$KEY" "$APEX_REBUILD" "$EROFS_REBUILD" "$APEX_AVB"; do
  [ -e "$f" ] || { err "缺输入: $f"; exit 1; }
done
mkdir -p "$WORK"

say "源镜像 : $SRC ($(stat -f%z "$SRC") 字节)"
say "目录树 : $TREE"
say "输出   : $OUT"

# ---- 0. 解包目录树（必须带 --xattrs）----
say "步骤 0/6: 解包 system_ext 目录树（--xattrs，保住 SELinux 标签）"
# 注意：不要用 rm -rf 清空旧树（几千个文件会触发宿主批量删除防护钩子）。
#       改成「旧树改名让位」——改名不是删除，不会触发钩子，且旧树仍可回查。
if [ -e "$TREE" ] && [ -n "$(ls -A "$TREE" 2>/dev/null)" ]; then
  OLD="${TREE}.old.$(date +%s)"
  mv "$TREE" "$OLD" || { err "旧树改名失败: $TREE"; exit 1; }
  warn "旧树已改名让位: $OLD （确认无用后可自行删除）"
fi
mkdir -p "$TREE"
"$EROFSTOOL/fsck.erofs" --extract="$TREE" --xattrs "$SRC" >/dev/null 2>&1
[ -d "$TREE" ] || { err "解包失败: $TREE"; exit 1; }
NXA=$(find "$TREE" -type f 2>/dev/null | head -400 \
      | while read -r f; do xattr "$f" 2>/dev/null; done | wc -l | tr -d ' ')
say "  xattr 覆盖率（前 400 个文件）: $NXA 条"
[ "$NXA" -gt 0 ] || { err "目录树没有 xattr —— 解包失败或镜像本身无 xattr，中止"; exit 1; }
ok "目录树解包完成，xattr 正常"

# ---- 1. 抽出 compos.apex ----
say "步骤 1/6: 从 system_ext.img 抽出 $TARGET"
rm -f "$WORK/compos_orig.apex"
"$EROFSTOOL/fsck.erofs" --extract="$WORK/compos_orig.apex" --path="$TARGET" "$SRC" >/dev/null 2>&1
[ -f "$WORK/compos_orig.apex" ] || { err "抽取失败"; exit 1; }
ok "抽出 $(stat -f%z "$WORK/compos_orig.apex") 字节"

say "  原包状态："
"$PY" - "$WORK/compos_orig.apex" <<'PYEOF'
import zipfile, struct, sys
with zipfile.ZipFile(sys.argv[1]) as z:
    n = [x for x in z.namelist() if x.endswith("apex_payload.img")][0]
    b = z.read(n)[:1120]
inc = struct.unpack_from("<I", b, 1024 + 80)[0]
print("    payload incompat = %#x %s" % (inc, "← 就是它，4.19.157 挂不上" if inc & 2 else ""))
PYEOF

# ---- 2. 重建 payload ----
say "步骤 2/6: apex_rebuild.py（payload → incompat=0x1 + 重签 AVB）"
rm -f "$WORK/compos_new.apex"
"$PY" "$APEX_REBUILD" "$WORK/compos_orig.apex" "$WORK/compos_new.apex" \
      --key "$KEY" --work "$WORK/apexwork" 2>&1 | sed 's/^/    /' | tail -25
rc=${PIPESTATUS[0]}
[ "$rc" -eq 0 ] && [ -f "$WORK/compos_new.apex" ] || { err "apex_rebuild 失败 rc=$rc"; exit 1; }
ok "新包 $(stat -f%z "$WORK/compos_new.apex") 字节"

# ---- 3. 校验新包 ----
say "步骤 3/6: 校验新包（EROFS incompat + AVB 哈希树）"
"$PY" - "$WORK/compos_new.apex" <<'PYEOF'
import zipfile, struct, sys
with zipfile.ZipFile(sys.argv[1]) as z:
    n = [x for x in z.namelist() if x.endswith("apex_payload.img")][0]
    b = z.read(n)[:1120]
magic = struct.unpack_from("<I", b, 1024)[0]
inc = struct.unpack_from("<I", b, 1024 + 80)[0]
print("    magic=%#x incompat=%#x" % (magic, inc))
if magic != 0xE0F5E1E2 or inc != 0x1:
    print("    ✗ 不合格"); sys.exit(1)
print("    ✓ incompat=0x1")
PYEOF
[ $? -eq 0 ] || { err "新包 EROFS 不合格"; exit 1; }

"$PY" "$APEX_AVB" "$WORK/compos_new.apex" 2>&1 | grep -E "结论|★★|✗" | sed 's/^/    /'

# ---- 4. 重建 system_ext.img ----
say "步骤 4/6: erofs_rebuild.py 重建 system_ext.img（只替换这一个 APEX）"
rm -f "$OUT"
"$PY" "$EROFS_REBUILD" "$SRC" "$TREE" "$OUT" \
  --zopts "-zlz4hc,level=9" \
  --add "$TARGET=$WORK/compos_new.apex#$TARGET_NOSLASH" 2>&1 \
  | grep -vE "^Processing " | sed 's/^/    /' | tail -20
rc=${PIPESTATUS[0]}
[ "$rc" -eq 0 ] && [ -f "$OUT" ] || { err "erofs_rebuild 失败 rc=$rc"; exit 1; }
ok "新镜像 $(stat -f%z "$OUT") 字节"

# ---- 5. 收尾校验 ----
say "步骤 5/6: 收尾校验"
"$PY" - "$SRC" "$OUT" <<'PYEOF'
import os, struct, sys
def sb(p):
    with open(p, "rb") as f:
        f.seek(1024); b = f.read(96)
    return (struct.unpack_from("<I", b, 0)[0], struct.unpack_from("<I", b, 8)[0],
            b[12], struct.unpack_from("<I", b, 80)[0])
for tag, p in [("原", sys.argv[1]), ("新", sys.argv[2])]:
    m, c, bb, i = sb(p)
    print("    %s: magic=%#x compat=%#x incompat=%#x blkszbits=%d  (%d 字节)"
          % (tag, m, c, i, bb, os.path.getsize(p)))
PYEOF
"$EROFSTOOL/fsck.erofs" "$OUT" >/dev/null 2>&1 && ok "fsck.erofs 干净通过" || { err "fsck.erofs 报错"; exit 1; }

say "  抽回替换后的 APEX 再验一次"
rm -f "$WORK/verify.apex"
"$EROFSTOOL/fsck.erofs" --extract="$WORK/verify.apex" --path="$TARGET" "$OUT" >/dev/null 2>&1
"$PY" - "$WORK/verify.apex" <<'PYEOF'
import zipfile, struct, sys
with zipfile.ZipFile(sys.argv[1]) as z:
    n = [x for x in z.namelist() if x.endswith("apex_payload.img")][0]
    b = z.read(n)[:1120]
inc = struct.unpack_from("<I", b, 1024 + 80)[0]
print("    镜像内该 APEX 的 payload incompat = %#x  %s"
      % (inc, "✓ 合格" if inc == 0x1 else "✗ 不合格"))
sys.exit(0 if inc == 0x1 else 1)
PYEOF
[ $? -eq 0 ] || { err "镜像内 APEX 校验失败"; exit 1; }

say "  顶层文件清单比对"
diff <("$EROFSTOOL/dump.erofs" --ls --path=/ "$SRC" 2>&1 | awk 'NR>8{print $NF}' | sort) \
     <("$EROFSTOOL/dump.erofs" --ls --path=/ "$OUT" 2>&1 | awk 'NR>8{print $NF}' | sort) \
  && ok "顶层清单一致" || warn "顶层清单有差异（请人工确认）"

say "  xattr 抽查（新镜像必须也带 SELinux 标签）"
$PY - "$SRC" "$OUT" "$TARGET" <<'PYEOF'
import os, re, struct, subprocess, sys
src, out, target = sys.argv[1], sys.argv[2], sys.argv[3]
dump = "/Users/skeletondie/.workbuddy-ai/tools/erofs/erofs-utils/1.9.4/bin/dump.erofs"
# 注意: dump.erofs 把 Xattr size 打在 Inode size 同一行
#       "Inode size: 32   Xattr size: 16" —— 不能用 startswith 匹配（踩过）
RE = re.compile(r'Xattr size:\s*(\d+)')
def xs(img, path):
    r = subprocess.run([dump, "--path=" + path, img], capture_output=True, text=True)
    m = RE.search(r.stdout)
    return int(m.group(1)) if m else None
def compat(img):
    with open(img, "rb") as f:
        f.seek(1024); b = f.read(96)
    return struct.unpack_from("<I", b, 8)[0]
bad = 0
for p in [target, "/apex", "/framework", "/priv-app", "/etc"]:
    a, b = xs(src, p), xs(out, p)
    if a is None or b is None:
        print("    %-42s 原=%s 新=%s  **解析失败**" % (p, a, b)); bad += 1; continue
    flag = "OK" if a > 0 and b > 0 else "**缺失**"
    if flag != "OK":
        bad += 1
    print("    %-42s 原=%d 新=%d  %s" % (p, a, b, flag))
c = compat(out)
print("    新镜像 compat = %#x  %s" % (c, "✓ 含 XATTR_FILTER" if c == 0x7 else "✗ 不是 0x7"))
sys.exit(1 if bad or c != 0x7 else 0)
PYEOF
[ $? -eq 0 ] || { err "xattr / compat 校验未通过"; exit 1; }
ok "xattr 与 compat 位均合格"

echo
ok "完成: $OUT"
say "下一步: 把 system_ext 也刷进去（flash_diag.sh 已包含）"
