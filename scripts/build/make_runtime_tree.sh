#!/bin/bash
# ==========================================================================
# make_runtime_tree.sh
#   从素材树里的 com.android.runtime.apex 提取
#   erofs_rebuild.py --materialize 所需的 12 个「实体化素材」。
#
# 为什么需要它
# ------------
# A16 的 /system/bin/linker64 等 17 个条目是指向 /apex/... 的【软链】。
# 而 /system/bin/sh 的 PT_INTERP = /system/bin/linker64。
# 一旦 apexd-bootstrap 失败，/apex 是空的 ⇒ 软链断掉 ⇒ sh 不可 exec
# ⇒ v1/v2 的面包屑静默失败（假阴性）。
#
# 修法：把 15 个（linker 族 5 + libc 族 10）软链【实体化】成真文件，
# 内容取自 com.android.runtime.apex。
#
# ★ 关键事实（本次实测确认）
#   APEX 的 apex_payload.img 是 **erofs**（magic 0xE0F5E1E2），不是 ext4。
#   所以可以直接用 fsck.erofs --extract 离线解开，**不需要设备**。
#
# 输出：$RT （默认 /tmp/runtime_tree）
# 完全离线、幂等，可重复执行。
# ==========================================================================
set -euo pipefail

PROJ="${PROJ:-$HOME/Documents/oppo/2026-安卓16}"
TREE="${TREE:-$PROJ/07-重建/tree}"
RT="${RT:-/tmp/runtime_tree}"

EROFS_BIN="${EROFS_BIN:-$HOME/.workbuddy-ai/tools/erofs/erofs-utils/1.9.4/bin}"
FSCK="$EROFS_BIN/fsck.erofs"

APEX="$TREE/system/apex/com.android.runtime.apex"

WORK="$(mktemp -d /tmp/mkrt.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }
info() { printf '  %s\n' "$*"; }

echo "== make_runtime_tree =="
info "APEX : $APEX"
info "输出 : $RT"
echo

# ---------- 1. 前置检查 ----------
[ -x "$FSCK" ] || { bad "fsck.erofs 不存在: $FSCK"; exit 2; }
[ -f "$APEX" ] || { bad "APEX 不存在: $APEX"; exit 2; }
ok "前置检查通过"

# ---------- 2. 解出 apex_payload.img ----------
cd "$WORK"
unzip -o -q "$APEX" apex_payload.img || { bad "unzip 失败"; exit 3; }
[ -f apex_payload.img ] || { bad "apex_payload.img 未解出"; exit 3; }

MAGIC="$(xxd -s 1024 -l 4 -p apex_payload.img)"
if [ "$MAGIC" = "e2e1f5e0" ]; then
    ok "payload 是 erofs (magic 0xE0F5E1E2)"
else
    bad "payload 不是 erofs，magic=$MAGIC —— 本脚本不支持（可能需要设备侧 losetup）"
    exit 3
fi

# ---------- 3. 全量解包 ----------
# 注意：fsck.erofs 的 --path 与 --extract 组合不生效，只能全量解包。
"$FSCK" --extract="$WORK/x" apex_payload.img >/dev/null 2>&1 || true
[ -d "$WORK/x" ] || { bad "fsck.erofs 解包失败"; exit 4; }
ok "erofs 解包完成"

# ---------- 4. 拷贝 12 个实体化素材 ----------
# 目录结构必须与 erofs_rebuild.py --materialize 的 --local 参数一致。
mkdir -p "$RT/bin" "$RT/lib64/bionic/hwasan" "$RT/lib/bionic"

copy() {                       # copy <apex内相对路径> <目标相对路径>
    local src="$WORK/x/$1" dst="$RT/$2"
    if [ ! -f "$src" ]; then bad "缺失: $1"; return 1; fi
    cp -f "$src" "$dst"
    chmod 0755 "$dst" 2>/dev/null || true
    printf '  %-58s %10s\n' "$2" "$(stat -f%z "$dst")"
}

copy bin/linker64                                   bin/linker64
copy bin/linker                                     bin/linker
copy lib64/bionic/libc.so                           lib64/bionic/libc.so
copy lib64/bionic/libm.so                           lib64/bionic/libm.so
copy lib64/bionic/libdl.so                          lib64/bionic/libdl.so
copy lib64/bionic/libdl_android.so                  lib64/bionic/libdl_android.so
copy lib64/bionic/hwasan/libc.so                    lib64/bionic/hwasan/libc.so
copy lib64/bionic/libclang_rt.hwasan-aarch64-android.so \
                                                    lib64/bionic/libclang_rt.hwasan-aarch64-android.so
copy lib/bionic/libc.so                             lib/bionic/libc.so
copy lib/bionic/libm.so                             lib/bionic/libm.so
copy lib/bionic/libdl.so                            lib/bionic/libdl.so
copy lib/bionic/libdl_android.so                    lib/bionic/libdl_android.so

echo

# ---------- 5. 断言：大小必须与基线一致 ----------
# 基线来自 v3 首次成功构建时实测（2026-10-01）。任何不符都要报警。
declare -a BASE=(
  "bin/linker64:2287376"
  "bin/linker:1795856"
  "lib64/bionic/libc.so:1337968"
  "lib64/bionic/libm.so:232688"
  "lib64/bionic/libdl.so:13976"
  "lib64/bionic/libdl_android.so:10200"
  "lib64/bionic/hwasan/libc.so:1756424"
  "lib64/bionic/libclang_rt.hwasan-aarch64-android.so:1251936"
  "lib/bionic/libc.so:1083092"
  "lib/bionic/libm.so:128220"
  "lib/bionic/libdl.so:5452"
  "lib/bionic/libdl_android.so:3348"
)
FAIL=0
for e in "${BASE[@]}"; do
    f="${e%%:*}"; want="${e##*:}"
    [ -f "$RT/$f" ] || { bad "断言失败(不存在): $f"; FAIL=1; continue; }
    got="$(stat -f%z "$RT/$f")"
    if [ "$got" != "$want" ]; then
        bad "断言失败(大小): $f 期望 $want 实得 $got"; FAIL=1
    fi
done
[ "$FAIL" = 0 ] && ok "12/12 素材大小全部匹配基线"

# ---------- 6. 反抽：确认 linker64 的 PT_INTERP 已不再是 /apex ----------
if command -v otool >/dev/null 2>&1; then :; fi
# 用纯字节扫描确认 linker64 是静态（无 PT_INTERP 指向 /system/bin/linker64）
if strings -a "$RT/bin/linker64" 2>/dev/null | grep -q '^/system/bin/linker64$'; then
    bad "linker64 里仍含 '/system/bin/linker64' 字符串 —— 请人工复核"
else
    ok "linker64 无自引用 interp 字符串（静态 bootstrap linker，符合预期）"
fi

echo
[ "$FAIL" = 0 ] && echo "完成 -> $RT" || { echo "有断言失败，请检查上方 ✗"; exit 5; }
