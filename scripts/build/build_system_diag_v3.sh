#!/bin/bash
# ============================================================================
#  build_system_diag_v3.sh —— 构建 ColorOS16 移植的【v3 诊断 system 镜像】
#
#  v3 相对 v1/v2 的根本变化（详见 05-分析/根因-修正-卡点已定位-apexd-bootstrap失败与17个apex软链.md）
#  ---------------------------------------------------------------
#   A. 面包屑彻底零依赖
#      v1/v2 用 `exec_start wb_diag_xxx` + `/system/bin/sh /system/etc/wb_diag.sh`。
#      但 /system/bin/sh 的 PT_INTERP = /system/bin/linker64，而 /system/bin/linker64
#      是指向 /apex/com.android.runtime/bin/linker64 的【软链】——
#      /apex 要到 on early-init 的【最后】才由 apexd-bootstrap 挂上，
#      所以 v1/v2 的面包屑必然静默失败（假阴性）。
#      v3 全部改用 init 内建 mkdir/write/copy/setenforce，不 exec 任何东西。
#
#   B. ★ 把 runtime APEX 的 15 个软链【实体化】
#      让 /system/bin/linker64 变成真文件，于是 sh/toybox/logd/adbd
#      在 /apex 为空时也能 exec。
#
#   C. 包住 apexd-bootstrap，抓它的真实退出码与报错（v1/v2 只看到"失败"）。
#
#   D. 注释掉全部 reboot_on_failure（v1 只注释了 system 的，apexd.rc 里还有 2 条），
#      失败后停现场而不是重启跑掉。
#
#  用法:
#    ./build_system_diag_v3.sh [输出镜像路径]
#
#  环境变量可覆盖:
#    SRC  源镜像(元数据基底)  默认 02-移植素材/a16_patched/system_diag.img  (v1)
#    TREE 解包目录树          默认 07-重建/tree
#    DIAG v3 补丁目录         默认 /tmp/diag3
#    RT   实体化素材目录      默认 /tmp/runtime_tree
#    OUT  输出镜像
# ============================================================================
set -u

PROJ="$HOME/Documents/oppo/2026-安卓16"
PY="/Users/skeletondie/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3"
REBUILD="$PROJ/03-脚本/erofs_rebuild.py"
EROFS_BIN="/Users/skeletondie/.workbuddy-ai/tools/erofs/erofs-utils/1.9.4/bin"
FS="$EROFS_BIN/fsck.erofs"
DUMP="$EROFS_BIN/dump.erofs"

SRC="${SRC:-$PROJ/02-移植素材/a16_patched/system_diag.img}"
TREE="${TREE:-$PROJ/07-重建/tree}"
DIAG="${DIAG:-/tmp/diag3}"
RT="${RT:-/tmp/runtime_tree}"
NEW="$PROJ/07-重建/新增文件"
OUT="${1:-${OUT:-$PROJ/02-移植素材/a16_patched/system_diag3.img}}"

C_R=$'\033[31m'; C_G=$'\033[32m'; C_Y=$'\033[33m'; C_B=$'\033[36m'; C_0=$'\033[0m'
ok()   { printf '  %s✓%s %s\n' "$C_G" "$C_0" "$*"; }
warn() { printf '  %s⚠%s %s\n' "$C_Y" "$C_0" "$*"; }
err()  { printf '  %s✗%s %s\n' "$C_R" "$C_0" "$*"; }
say()  { printf '%s·%s %s\n' "$C_B" "$C_0" "$*"; }
hr()   { printf '%s\n' "----------------------------------------------------------------------"; }

SCRIPTS="$PROJ/03-脚本"

# --------------------------------------------- -1) 素材自动补齐（完全离线）
# /tmp 会被系统清理。v3 补丁或实体化素材一旦缺失，这里自动重建，
# 使整条链路【离线自足】—— 不再依赖设备侧 losetup 提取。
#
# ★ 关键事实：APEX 的 apex_payload.img 是 erofs（magic 0xE0F5E1E2），
#   不是 ext4 ⇒ 可以用 fsck.erofs --extract 在本地直接解开。
if [ ! -f "$DIAG/init.rc" ] || [ ! -f "$RT/bin/linker64" ]; then
  hr
  say "===== -1) 素材缺失，自动重建 ====="
  if [ ! -f "$DIAG/init.rc" ]; then
    mkdir -p "$DIAG"
    say "生成 v3 补丁 -> $DIAG"
    OUT="$DIAG" "$PY" "$SCRIPTS/make_diag_v3.py" || { err "make_diag_v3.py 失败"; exit 1; }
  else
    ok "v3 补丁已存在，跳过 ($DIAG)"
  fi
  if [ ! -f "$RT/bin/linker64" ]; then
    say "提取实体化素材 -> $RT  (从 com.android.runtime.apex 解 erofs)"
    RT="$RT" "$SCRIPTS/make_runtime_tree.sh" || { err "make_runtime_tree.sh 失败"; exit 1; }
  else
    ok "实体化素材已存在，跳过 ($RT)"
  fi
fi

# ---------------------------------------------------------------- 0) 自检
say "===== 0) 前置自检 ====="
fail=0
chk() { [ -e "$1" ] && ok "$2" || { err "缺 $1"; fail=1; }; }
chk "$SRC"                  "源镜像 $(basename "$SRC") $(stat -f%z "$SRC" 2>/dev/null) 字节"
chk "$TREE"                 "解包树 $TREE"
chk "$DIAG/init.rc"         "v3 init.rc"
chk "$DIAG/apexd.rc"        "v3 apexd.rc"
chk "$DIAG/wb_diag.sh"      "v3 wb_diag.sh"
chk "$DIAG/wb_diag.rc"      "v3 wb_diag.rc"
chk "$DIAG/wb_apexd_wrap.sh" "v3 wb_apexd_wrap.sh"
chk "$NEW/netbpfload"       "netbpfload"
chk "$NEW/wb_netbpfload.rc" "wb_netbpfload.rc"
[ -x "$REBUILD" ] || [ -f "$REBUILD" ] && ok "erofs_rebuild.py" || { err "缺 $REBUILD"; fail=1; }

# 实体化素材
MAT_SRC=(
  "$RT/bin/linker64" "$RT/bin/linker"
  "$RT/lib64/bionic/libc.so" "$RT/lib64/bionic/libm.so"
  "$RT/lib64/bionic/libdl.so" "$RT/lib64/bionic/libdl_android.so"
  "$RT/lib64/bionic/hwasan/libc.so"
  "$RT/lib64/bionic/libclang_rt.hwasan-aarch64-android.so"
  "$RT/lib/bionic/libc.so" "$RT/lib/bionic/libm.so"
  "$RT/lib/bionic/libdl.so" "$RT/lib/bionic/libdl_android.so"
)
miss=0
for f in "${MAT_SRC[@]}"; do [ -f "$f" ] || { err "缺实体化素材 $f"; miss=1; }; done
[ $miss -eq 0 ] && ok "实体化素材 12 个齐全 ($RT)" || fail=1

# xattr 模板
for f in system/bin/bootstrap/linker64 system/lib64/libcrypto.so system/lib/libcrypto.so; do
  if [ -f "$TREE/$f" ]; then
    lb=$(xattr -p security.selinux "$TREE/$f" 2>/dev/null || echo '(无)')
    ok "模板 $f  →  $lb"
  else
    err "缺 xattr 模板 $TREE/$f"; fail=1
  fi
done

[ $fail -eq 0 ] || { echo; err "自检失败，中止"; exit 1; }

# ---------------------------------------------------------------- 1) 源镜像现状
hr
say "===== 1) 源镜像(v1)中相关条目的现状 ====="
"$PY" - "$SRC" <<'PY'
import sys, os
sys.path.insert(0, os.path.join(os.path.expanduser("~"),
                  "Documents/oppo/2026-安卓16/03-脚本"))
from erofs_read import Erofs
e = Erofs(sys.argv[1])

def find(path):
    ci = e.read_inode(e.root_nid)
    for p in [x for x in path.strip('/').split('/') if x]:
        hit = None
        for name, nid, ft in e.listdir(ci):
            if name == p:
                hit = nid; break
        if hit is None:
            return None
        ci = e.read_inode(hit)
    return ci

TYPES = {0o040000:'DIR', 0o100000:'REG', 0o120000:'LNK'}
REPL = ["system/etc/init/hw/init.rc","system/etc/init/apexd.rc",
        "system/etc/init/wb_diag.rc","system/etc/wb_diag.sh",
        "system/bin/netbpfload","system/etc/init/wb_netbpfload.rc"]
MAT = ["system/bin/linker64","system/bin/linker","system/bin/linker_asan",
       "system/bin/linker_asan64","system/bin/linker_hwasan64",
       "system/lib64/libc.so","system/lib64/libm.so","system/lib64/libdl.so",
       "system/lib64/libdl_android.so","system/lib64/hwasan/libc.so",
       "system/lib64/libclang_rt.hwasan-aarch64-android.so",
       "system/lib/libc.so","system/lib/libm.so","system/lib/libdl.so",
       "system/lib/libdl_android.so"]
print("  --replace 目标:")
bad = 0
for t in REPL:
    ci = find(t)
    if ci is None:
        print(f"    ✗ {t}  <== 不存在，--replace 会被静默忽略!"); bad += 1
    else:
        k = ci['i_mode'] & 0o170000
        print(f"    {TYPES.get(k,'?'):<3} {oct(ci['i_mode']&0o7777):<6} "
              f"{ci['i_size']:>8}  {t}")
print("  --materialize 目标(必须都是 LNK):")
for t in MAT:
    ci = find(t)
    if ci is None:
        print(f"    ✗ {t}  <== 不存在!"); bad += 1
    else:
        k = ci['i_mode'] & 0o170000
        mark = 'OK' if k == 0o120000 else '✗ 不是软链!'
        if k != 0o120000: bad += 1
        print(f"    {TYPES.get(k,'?'):<3} {mark:<12} {t}")
print("  --add 目标(必须不存在):")
ci = find("system/etc/wb_apexd_wrap.sh")
print(f"    {'不存在 ✓' if ci is None else '已存在 ✗'}  system/etc/wb_apexd_wrap.sh")
if ci is not None: bad += 1
e.close()
sys.exit(1 if bad else 0)
PY
[ $? -eq 0 ] || { echo; err "源镜像现状检查不通过，中止"; exit 1; }

# ---------------------------------------------------------------- 2) 构建
hr
say "===== 2) 构建 v3 镜像 ====="
echo "    ⚠ 用【落盘 tar】而不是 --stream 管道：mkfs.erofs 对不可 seek 的输入无法"
echo "      预知镜像大小，一旦中途 ENOSPC 就留下一个表观 2 TiB 的残骸文件，"
echo "      反而把磁盘彻底吃满（本项目 2026-10-01 踩过两次）。"
echo "      落盘 tar 可 seek ⇒ 精确 ftruncate，失败也不会留残骸。"
echo "    ⚠ --sort=none 保留：我的 plan 已按路径排序，无需 mkfs 再缓冲排序。"
echo "    源   : $SRC"
echo "    树   : $TREE"
echo "    补丁 : $DIAG"
echo "    输出 : $OUT"
avail=$(df -k /System/Volumes/Data 2>/dev/null | tail -1 | awk '{print $4}')
echo "    可用 : $(( avail / 1024 )) MiB   (峰值需求 ≈ 2.5 GiB = tar 1.4G + 镜像 1.06G)"
if [ "${avail:-0}" -lt 3000000 ]; then
  warn "可用空间不足 3 GiB，构建很可能中途 ENOSPC"
  warn "先释放空间再跑；失败残骸请用 /bin/rm 清（见文件末尾说明）"
fi
echo

MAT_ARGS=()
mat() { MAT_ARGS+=(--materialize "$1=$2#$3"); }
#   linker 系列 -> 模板 system/bin/bootstrap/linker64  (u:object_r:system_linker_exec:s0)
mat /system/bin/linker64          "$RT/bin/linker64" system/bin/bootstrap/linker64
mat /system/bin/linker            "$RT/bin/linker"   system/bin/bootstrap/linker64
mat /system/bin/linker_asan64     "$RT/bin/linker64" system/bin/bootstrap/linker64
mat /system/bin/linker_asan       "$RT/bin/linker"   system/bin/bootstrap/linker64
mat /system/bin/linker_hwasan64   "$RT/bin/linker64" system/bin/bootstrap/linker64
#   64 位 lib -> 模板 system/lib64/libcrypto.so      (u:object_r:system_lib_file:s0)
mat /system/lib64/libc.so                             "$RT/lib64/bionic/libc.so"                             system/lib64/libcrypto.so
mat /system/lib64/libm.so                             "$RT/lib64/bionic/libm.so"                             system/lib64/libcrypto.so
mat /system/lib64/libdl.so                            "$RT/lib64/bionic/libdl.so"                            system/lib64/libcrypto.so
mat /system/lib64/libdl_android.so                    "$RT/lib64/bionic/libdl_android.so"                    system/lib64/libcrypto.so
mat /system/lib64/hwasan/libc.so                      "$RT/lib64/bionic/hwasan/libc.so"                      system/lib64/libcrypto.so
mat /system/lib64/libclang_rt.hwasan-aarch64-android.so "$RT/lib64/bionic/libclang_rt.hwasan-aarch64-android.so" system/lib64/libcrypto.so
#   32 位 lib -> 模板 system/lib/libcrypto.so        (u:object_r:system_lib_file:s0)
mat /system/lib/libc.so                               "$RT/lib/bionic/libc.so"                               system/lib/libcrypto.so
mat /system/lib/libm.so                               "$RT/lib/bionic/libm.so"                               system/lib/libcrypto.so
mat /system/lib/libdl.so                              "$RT/lib/bionic/libdl.so"                              system/lib/libcrypto.so
mat /system/lib/libdl_android.so                      "$RT/lib/bionic/libdl_android.so"                      system/lib/libcrypto.so

set -o pipefail
# ★ 每个 --replace 都要带 "#<树内模板路径>" 指定 SELinux 标签来源。
#   原因（2026-10-02 实测踩到，害了 v3/v4 两轮）：
#     --replace 的 xattr 默认取「该路径在【树】里的 xattr」。而这几个补丁文件
#     （netbpfload / wb_diag.rc / wb_diag.sh / wb_netbpfload.rc）只在【源镜像】里、
#     树里根本没有 ⇒ 一条 xattr 都取不到 ⇒ 写出来变成 u:object_r:unlabeled:s0。
#     后果：init 报 "File /system/bin/netbpfload(labeled "u:object_r:unlabeled:s0")
#     has incorrect label or no domain transition" ⇒ exec_start wb_netbpfload 直接
#     失败 ⇒ /sys/fs/bpf 空 ⇒ netd 找不到 BPF 程序 ⇒ netd crashloop。
#     ⚠ 这个错误在 permissive 下【照样报】（init 故意的，便于审计）。
#   模板选择：可执行文件用 system/bin/bpfloader (bpfloader_exec)；
#             /system/etc/init/*.rc 与 /system/etc/*.sh 用 system/etc/init/hw/init.rc (system_file)。
"$PY" "$REBUILD" "$SRC" "$TREE" "$OUT" \
  --zopts "-zlz4hc,level=9 --sort=none" \
  --replace "/system/etc/init/hw/init.rc=$DIAG/init.rc#system/etc/init/hw/init.rc" \
  --replace "/system/etc/init/apexd.rc=$DIAG/apexd.rc#system/etc/init/hw/init.rc" \
  --replace "/system/etc/init/wb_diag.rc=$DIAG/wb_diag.rc#system/etc/init/hw/init.rc" \
  --replace "/system/etc/wb_diag.sh=$DIAG/wb_diag.sh#system/etc/init/hw/init.rc" \
  --replace "/system/bin/netbpfload=$NEW/netbpfload#system/bin/bpfloader" \
  --replace "/system/etc/init/wb_netbpfload.rc=$NEW/wb_netbpfload.rc#system/etc/init/hw/init.rc" \
  --add "/system/etc/wb_apexd_wrap.sh=$DIAG/wb_apexd_wrap.sh#system/etc/init/hw/init.rc" \
  --addmode /system/etc/wb_apexd_wrap.sh=0755 \
  "${MAT_ARGS[@]}"
rc=$?
[ $rc -eq 0 ] || { echo; err "erofs_rebuild.py 失败 rc=$rc"; exit $rc; }

# ---------------------------------------------------------------- 3) 超级块自检
hr
say "===== 3) 超级块自检 ====="
"$PY" - "$SRC" "$OUT" <<'PY'
import struct, sys
def sb(p):
    with open(p, 'rb') as f:
        f.seek(1024); d = f.read(128)
    return dict(magic=struct.unpack_from('<I', d, 0)[0],
                feature_compat=struct.unpack_from('<I', d, 8)[0],
                blkszbits=d[12],
                feature_incompat=struct.unpack_from('<I', d, 80)[0])
a, b = sb(sys.argv[1]), sb(sys.argv[2])
print(f"  源  : magic=0x{a['magic']:08X} compat=0x{a['feature_compat']:X} "
      f"incompat=0x{a['feature_incompat']:X} blkszbits={a['blkszbits']}")
print(f"  新  : magic=0x{b['magic']:08X} compat=0x{b['feature_compat']:X} "
      f"incompat=0x{b['feature_incompat']:X} blkszbits={b['blkszbits']}")
ok = (a['magic'] == 0xE0F5E1E2 and b['magic'] == 0xE0F5E1E2
      and a['feature_incompat'] == b['feature_incompat']
      and a['blkszbits'] == b['blkszbits'])
print("  " + ("✓ 与源镜像一致" if ok else "✗ 不一致（内核可能拒绝挂载）"))
sys.exit(0 if ok else 1)
PY
[ $? -eq 0 ] || { err "超级块不一致，中止"; exit 1; }

# ---------------------------------------------------------------- 4) 反抽断言
hr
say "===== 4) 反抽断言（直接读新镜像元数据，不做全量解包以省空间）====="
"$PY" - "$OUT" <<'PY'
import sys, os
sys.path.insert(0, os.path.join(os.path.expanduser("~"),
                  "Documents/oppo/2026-安卓16/03-脚本"))
from erofs_read import Erofs
e = Erofs(sys.argv[1])
bad = []

# ★ erofs_read.py 不支持 COMPRESSED_COMPACT（Layout 3），而 v1/v3 的 init.rc
#   都是 Layout 3。这是【自写解析器的局限】，不是镜像问题 —— 设备上跑过的
#   v1 也是 Layout 3，已被 4.19 内核正常读取（有 persist.sys.boot.reason 铁证）。
#   ⇒ 一旦 read_data 抛 COMPACT 异常，就回退到 fsck.erofs 全量解包后读真实文件。
FSBIN = os.path.expanduser(
    "~/.workbuddy-ai/tools/erofs/erofs-utils/1.9.4/bin/fsck.erofs")
_EXDIR = [None]
def read_text(rel, ci):
    try:
        return e.read_data(ci).decode('utf-8', 'replace')
    except Exception:
        pass
    if _EXDIR[0] is None:
        import tempfile, subprocess
        _EXDIR[0] = tempfile.mkdtemp(prefix='wbv3_')
        print(f"  (erofs_read 遇 COMPACT 数据，回退 fsck.erofs 解包 -> {_EXDIR[0]})")
        subprocess.run([FSBIN, f"--extract={_EXDIR[0]}", sys.argv[1]],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p = os.path.join(_EXDIR[0], rel)
    if not os.path.isfile(p):
        raise RuntimeError(f"解包后仍找不到 {rel}")
    with open(p, 'rb') as fh:
        return fh.read().decode('utf-8', 'replace')

def find(path):
    ci = e.read_inode(e.root_nid)
    for p in [x for x in path.strip('/').split('/') if x]:
        hit = None
        for name, nid, ft in e.listdir(ci):
            if name == p:
                hit = nid; break
        if hit is None:
            return None
        ci = e.read_inode(hit)
    return ci

# --- A. 15 个软链必须已实体化 ---
MAT = {
 "system/bin/linker64": 2287376, "system/bin/linker": 1795856,
 "system/bin/linker_asan64": 2287376, "system/bin/linker_asan": 1795856,
 "system/bin/linker_hwasan64": 2287376,
 "system/lib64/libc.so": 1337968, "system/lib64/libm.so": 232688,
 "system/lib64/libdl.so": 13976, "system/lib64/libdl_android.so": 10200,
 "system/lib64/hwasan/libc.so": 1756424,
 "system/lib64/libclang_rt.hwasan-aarch64-android.so": 1251936,
 "system/lib/libc.so": 1083092, "system/lib/libm.so": 128220,
 "system/lib/libdl.so": 5452, "system/lib/libdl_android.so": 3348,
}
n_ok = 0
for t, want in MAT.items():
    ci = find(t)
    if ci is None:
        bad.append(f"{t} 不存在"); continue
    k = ci['i_mode'] & 0o170000
    if k == 0o120000:
        bad.append(f"{t} 仍是符号链接（未实体化）"); continue
    if k != 0o100000:
        bad.append(f"{t} 类型异常 {oct(k)}"); continue
    if ci['i_size'] != want:
        bad.append(f"{t} 大小 {ci['i_size']} != {want}"); continue
    if (ci['i_mode'] & 0o7777) != 0o755:
        bad.append(f"{t} 权限 {oct(ci['i_mode']&0o7777)} != 0755"); continue
    n_ok += 1
print(f"  实体化: {n_ok}/15 通过")

# --- B. init.rc 面包屑（零依赖）---
ci = find("system/etc/init/hw/init.rc")
if ci is None:
    bad.append("init.rc 不存在")
else:
    txt = read_text("system/etc/init/hw/init.rc", ci)
    n_bread = txt.count("write /metadata/wbdiag/")
    if n_bread < 10:
        bad.append(f"init.rc 面包屑只有 {n_bread} 条（应 >=10）")
    else:
        print(f"  init.rc 面包屑: {n_bread} 条")
    for tok in ["mkdir /metadata/wbdiag", "setenforce 0",
                "exec_start apexd-bootstrap", "exec_start wb_probe"]:
        if tok not in txt:
            bad.append(f"init.rc 缺 {tok!r}")
    if "exec_start wb_diag_" in txt:
        bad.append("init.rc 还残留 v1/v2 的 exec_start wb_diag_*")
    import re
    n_rf = len(re.findall(r'^\s*reboot_on_failure', txt, re.M))
    if n_rf:
        bad.append(f"init.rc 还有 {n_rf} 条未注释的 reboot_on_failure")
    else:
        print("  init.rc reboot_on_failure: 已全部注释")

# --- C. apexd.rc 包了 wrapper ---
ci = find("system/etc/init/apexd.rc")
if ci is None:
    bad.append("apexd.rc 不存在")
else:
    txt = read_text("system/etc/init/apexd.rc", ci)
    if "wb_apexd_wrap.sh" not in txt:
        bad.append("apexd.rc 没有包 wb_apexd_wrap.sh")
    else:
        print("  apexd.rc: 已包 wb_apexd_wrap.sh")
    import re
    if re.search(r'^\s*reboot_on_failure', txt, re.M):
        bad.append("apexd.rc 还有未注释的 reboot_on_failure")

# --- D. 新增/替换的脚本 ---
for t, minz, want_v3 in [("system/etc/wb_apexd_wrap.sh", 800, "wb_apexd_wrap"),
                         ("system/etc/wb_diag.sh", 400, "wb_probe stage")]:
    ci = find(t)
    if ci is None:
        bad.append(f"{t} 不存在"); continue
    if (ci['i_mode'] & 0o7777) != 0o755:
        bad.append(f"{t} 权限不是 0755"); continue
    body = read_text(t, ci)
    if want_v3 not in body:
        bad.append(f"{t} 内容不是 v3 版（缺 {want_v3!r}）")
    elif ci['i_size'] < minz:
        bad.append(f"{t} 太小 ({ci['i_size']})")
    else:
        print(f"  {t}: {ci['i_size']} 字节 0755 ✓")

# --- E. netbpfload 保留 ---
ci = find("system/bin/netbpfload")
if ci is None or ci['i_size'] != 86432:
    bad.append("system/bin/netbpfload 丢失或大小不对")
else:
    print(f"  system/bin/netbpfload: {ci['i_size']} 字节 ✓")

# --- F. ★ SELinux 标签断言（2026-10-02 新增，血的教训）---
#   --replace / --add 的 xattr 来源是「树里的模板路径」。模板一旦缺失，
#   写出来的文件就是 u:object_r:unlabeled:s0 —— init 会直接拒绝 exec_start，
#   而且【permissive 下照样报错】（init 故意的，便于审计）。
#   v3 / v4 就是栽在 /system/bin/netbpfload 上：
#     init: Command 'exec_start wb_netbpfload' ... failed:
#       File /system/bin/netbpfload(labeled "u:object_r:unlabeled:s0") has
#       incorrect label or no domain transition from u:r:init:s0 ...
#   ⇒ /sys/fs/bpf 全空 ⇒ netd 找不到 BPF 程序 ⇒ netd crashloop ⇒ boot loop。
#   所以这里逐个反抽真实 xattr，不通过就不许刷。
import subprocess, tempfile, shutil as _sh
LABELS = {
    "system/bin/netbpfload":            "u:object_r:bpfloader_exec:s0",
    "system/bin/bpfloader":             "u:object_r:bpfloader_exec:s0",
    "system/etc/init/hw/init.rc":       "u:object_r:system_file:s0",
    "system/etc/init/apexd.rc":         "u:object_r:system_file:s0",
    "system/etc/init/wb_diag.rc":       "u:object_r:system_file:s0",
    "system/etc/init/wb_netbpfload.rc": "u:object_r:system_file:s0",
    "system/etc/wb_diag.sh":            "u:object_r:system_file:s0",
    "system/etc/wb_apexd_wrap.sh":      "u:object_r:system_file:s0",
    "system/bin/linker64":              "u:object_r:system_linker_exec:s0",
    "system/lib64/libc.so":             "u:object_r:system_lib_file:s0",
    # ★ 2026-10-02 补：apex_rebuild_all.py 产出的新 APEX 是 python 新建文件，
    #   32/33 个包的 security.selinux 全丢（只有没被重建的 cts.shim 还留着）。
    #   permissive 下能跑，enforcing 下 /system/apex/* 会变 unlabeled。
    #   由 apex_v2_sign.py 一并补回。
    "system/apex/com.android.scheduling.apex": "u:object_r:system_file:s0",
    "system/apex/com.android.adbd.apex":       "u:object_r:system_file:s0",
}
_tmpd = tempfile.mkdtemp(prefix='wblabel_')
n_lab = 0
for rel, want in LABELS.items():
    outp = os.path.join(_tmpd, rel.replace('/', '_'))
    subprocess.run([FSBIN, f"--extract={outp}", f"--path=/{rel}", "--xattrs",
                    sys.argv[1]],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not os.path.exists(outp):
        bad.append(f"[标签] {rel} 抽不出来（不存在？）"); continue
    g = subprocess.run(["xattr", "-p", "security.selinux", outp],
                       capture_output=True, text=True)
    got = (g.stdout or g.stderr).strip()
    if g.returncode != 0 or not got.startswith("u:object_r:"):
        bad.append(f"[标签] {rel} 的 security.selinux = {got!r}（期望 {want}）"); continue
    if got != want:
        bad.append(f"[标签] {rel} = {got}，期望 {want}"); continue
    n_lab += 1
_sh.rmtree(_tmpd, ignore_errors=True)
print(f"  SELinux 标签: {n_lab}/{len(LABELS)} 通过")

# --- G. ★ APEX v2 签名块断言（2026-10-02 新增，第 6 号根因）---
#   A16 的 PackageManagerService.scanApexPackages 会对每个 /system/apex/*.apex 调
#     ApkSignatureVerifier.verify(path, SIGNING_SCHEME_V2)
#   apex_rebuild.py 用 python zipfile 重打 zip 时把原包的 APK Signing Block 整块丢了 ⇒
#     PackageManagerException: No APK Signature Scheme v2 signature in package
#       Caused by: SignatureNotFoundException: No APK Signing Block before ZIP Central Directory
#   ⇒ system_server 在 PackageManagerService.<init> 就 FATAL ⇒ boot loop。
#   实测原话（v5 日志）：
#     E System : java.lang.IllegalStateException: Failed to scan: /system/apex/com.android.adbd.apex
#     E AndroidRuntime: *** FATAL EXCEPTION IN SYSTEM PROCESS: main
#   补签由 apex_v2_sign.py 完成（AOSP apksig 库）。这里从镜像反抽真实文件复核。
import struct as _st
_tmpd2 = tempfile.mkdtemp(prefix='wbapexsig_')
APEX_CHECK = [
    "system/apex/com.android.scheduling.apex",   # 最小（176 KB），抽得快
    "system/apex/com.android.adbd.apex",        # 实际报错的那个
]
n_ax = 0
for rel in APEX_CHECK:
    outp = os.path.join(_tmpd2, rel.replace('/', '_'))
    subprocess.run([FSBIN, f"--extract={outp}", f"--path=/{rel}", sys.argv[1]],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not os.path.exists(outp):
        bad.append(f"[apex签名] {rel} 抽不出来（不存在？）"); continue
    with open(outp, 'rb') as _f:
        _d = _f.read()
    _i = _d.rfind(b"PK\x05\x06")
    if _i < 0:
        bad.append(f"[apex签名] {rel} 找不到 EOCD"); continue
    _cdo = _st.unpack_from("<I", _d, _i + 16)[0]
    if _cdo < 24 or _d[_cdo - 24 + 8:_cdo - 24 + 24] != b"APK Sig Block 42":
        bad.append(f"[apex签名] {rel} 没有 APK Signing Block（system_server 会 FATAL）")
        continue
    _nlen = _st.unpack_from("<H", _d, 26)[0]
    _elen = _st.unpack_from("<H", _d, 28)[0]
    _doff = 30 + _nlen + _elen
    if _doff % 4096 != 0:
        bad.append(f"[apex签名] {rel} payload offset={_doff} 不是 4096 倍数（apexd 挂不上）")
        continue
    n_ax += 1
_sh.rmtree(_tmpd2, ignore_errors=True)
print(f"  APEX v2 签名块: {n_ax}/{len(APEX_CHECK)} 通过")

e.close()
if _EXDIR[0]:
    import shutil
    shutil.rmtree(_EXDIR[0], ignore_errors=True)
    print(f"  (已清理临时解包目录 {_EXDIR[0]})")
print()
if bad:
    for b in bad:
        print(f"  ✗ {b}")
    sys.exit(1)
print("  ✓ 全部断言通过")
PY
[ $? -eq 0 ] || { echo; err "反抽断言失败 —— 不要刷这个镜像"; exit 1; }

# ---------------------------------------------------------------- 5) 收尾
hr
say "===== 5) 完成 ====="
ls -la "$OUT"
echo "  sha256: $(shasum -a 256 "$OUT" | awk '{print $1}')"
echo "  可用空间: $(df -h /System/Volumes/Data | tail -1 | awk '{print $4}')"
echo
echo "${C_Y}  下一步:${C_0}"
echo "    1) 设备进 fastboot → WB_YES=1 SYS_DIAG=\"$OUT\" ./reflash_a16.sh --flash"
echo "    2) ./reflash_a16.sh --observe 180"
echo "    3) 回 TWRP → ./reflash_a16.sh --forensics  → 读 /metadata/wbdiag/"
