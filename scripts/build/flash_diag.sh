#!/bin/bash
# ============================================================================
#  flash_diag.sh —— 一键：恢复 boot 分区 + 刷入诊断版 system + 修复版 system_ext
#
#  为什么必须先恢复 boot：
#    上一轮我们把 /tmp/hybrid_A16init_x2fstab.img（X2Pro 内核 + X2Pro dtb +
#    A16 的 ramdisk init + X2Pro fstab）刷进了 boot。结果设备从 USB 上**彻底消失**
#    （120 秒内既无 fastboot 也无 adb，连 USB 节点都没有）。
#    说明这个混合 ramdisk 在**第一阶段 init** 就挂了，连 USB 都没枚举。
#    ⇒ 必须先把 boot 刷回已验证可用的那份（sha256 fc60a85e05b6d953），
#      回到「能进系统、能到 on early-init」的已知状态。
#
#  为什么要刷诊断版 system：
#    apexd-bootstrap（init.rc 里，在 on early-init 中）是系统里唯一带
#    `reboot_on_failure reboot,bootloader` 的服务，一失败就重启跑掉。
#    诊断版注释掉了它 ⇒ 失败也不再重启，init 会继续往下走，
#    而且会顺路把状态倾倒到 /metadata/wbdiag/。
#
#  为什么要刷修复版 system_ext：
#    供体 8T 的 system_ext 里 /apex/com.android.compos.apex 的 payload 是
#    EROFS incompat=0x3（含 COMPR_CFGS），4.19.157 内核挂不上；
#    修复版已把它重建为 incompat=0x1，并且**带全 xattr**（SELinux 标签）。
#
#  用法:
#     ./flash_diag.sh                          # 默认：刷回已知可用 boot + 诊断 system + 修复版 system_ext
#     ./flash_diag.sh <system.img>             # 指定诊断镜像
#     ./flash_diag.sh --kernel <boot.img>      # 刷指定内核 boot（实验用，见下）
#     ./flash_diag.sh --no-preflight           # 跳过刷机前预检（不建议）
#
#  --kernel 说明（实验：验证"内核缺 erofs_load_compr_cfgs"这个根因）
#  ================================================================
#  boot.img (header v2) 由 [header][kernel][ramdisk][dtb] 四段组成，彼此独立。
#  03-脚本/build_hybrid_kernel.py 可以做出「8T 的 4.19.325 内核 + X2 Pro 的 dtb
#  + X2 Pro 的 ramdisk」的混合 boot —— 只换内核，硬件描述表和 ramdisk 都不动：
#
#     ./03-脚本/flash_diag.sh --kernel 02-移植素材/a16_patched/hybrid_8tkernel.img
#
#  ★ 关键顺序（踩过的坑）：
#    system / system_ext 是**逻辑分区**，只能在 fastbootd（用户空间 fastboot）里刷；
#    而 fastbootd 是由**能启动的 boot ramdisk** 提供的。
#    如果先刷实验内核、再想进 fastbootd —— 实验内核一旦起不来，fastbootd 就没了，
#    system 也就刷不进去了（卡死在半路）。
#    所以本脚本把实验内核放到**最后一步**：
#      ① 先刷回已知可用 boot  → ② 进 fastbootd → ③ 刷 system / system_ext
#      → ④ 回 bootloader 刷实验内核 → ⑤ 重启
#    这样无论实验内核成不成，system 都已经就位。
#
#  若这样能起来 ⇒ 根因确证（就是内核缺 erofs_load_compr_cfgs）；
#  若还起不来 ⇒ 根因不在 EROFS 压缩配置。
#  起不来时，不加 --kernel 再跑一次即可刷回已知可用 boot。
# ============================================================================
set -u

PT="$HOME/Documents/oppo/platform-tools"
ADB="$PT/adb"
FB="$PT/fastboot"
PROJ="$HOME/Documents/oppo/2026-安卓16"
PY=/Users/skeletondie/.workbuddy-ai/binaries/python/versions/3.13.12/bin/python3

BOOT_GOOD="$PROJ/04-日志/dump_202707/boot.img"      # sha256 fc60a85e05b6d953
BOOT_USE="$BOOT_GOOD"                                # 实际要刷的 boot（--kernel 可改）
SYS_DIAG="$PROJ/02-移植素材/a16_patched/system_diag.img"
SYS_EXT="$PROJ/02-移植素材/a16_patched/system_ext_fix.img"

PREFLIGHT=1
while [ $# -gt 0 ]; do
  case "$1" in
    --no-preflight) PREFLIGHT=0; shift ;;
    --kernel)       BOOT_USE="$2"; shift 2 ;;
    -*) echo "未知参数: $1"; exit 2 ;;
    *)  SYS_DIAG="$1"; shift ;;
  esac
done

C_R=$'\033[31m'; C_G=$'\033[32m'; C_Y=$'\033[33m'; C_B=$'\033[36m'; C_0=$'\033[0m'
say()  { echo "${C_B}[$(date +%H:%M:%S)]${C_0} $*"; }
ok()   { echo "  ${C_G}✓${C_0} $*"; }
warn() { echo "  ${C_Y}⚠${C_0} $*"; }
err()  { echo "  ${C_R}✗${C_0} $*"; }

for f in "$BOOT_USE" "$SYS_DIAG"; do
  [ -f "$f" ] || { err "缺文件: $f"; exit 1; }
done

# ---- 0. 刷机前预检 ----
if [ "$PREFLIGHT" = 1 ]; then
  say "步骤 0/7: 刷机前预检"
  BS=$(shasum -a 256 "$BOOT_USE" | cut -c1-16)
  if [ "$BOOT_USE" = "$BOOT_GOOD" ]; then
    if [ "$BS" = "fc60a85e05b6d953" ]; then
      ok "boot 恢复镜像 sha256 正确 ($BS)"
    else
      err "boot 恢复镜像 sha256 = ${BS}，应为 fc60a85e05b6d953 —— 拒绝继续"
      exit 1
    fi
  else
    warn "boot 不是已知可用的那份，而是指定内核: $BOOT_USE"
    warn "   sha256 = ${BS}（没有对照值，仅记录）"
    warn "   ★ 若这次起不来，用不加 --kernel 的方式再跑一次即可刷回已知可用 boot"
  fi

  "$PY" - "$BOOT_USE" <<'PYEOF' || exit 1
import re, struct, sys
p = sys.argv[1]
d = open(p, "rb").read()
if d[:8] != b"ANDROID!":
    print("    ✗ 不是 Android boot.img"); sys.exit(1)
g = lambda o: struct.unpack_from("<I", d, o)[0]
ks, rs, ss, ps, hv = g(8), g(16), g(24), g(36), g(40)
ds = g(1648)
kern = d[ps:ps + ks]
m = re.search(rb'Linux version [^\x00\n]{10,140}', kern)
print("    boot 内核 : %s" % (m.group().decode('utf-8', 'replace') if m else "(未找到版本串)"))
print("    kernel=%d ramdisk=%d dtb=%d header_version=%d pagesize=%d"
      % (ks, rs, ds, hv, ps))
for s in (b'erofs_load_compr_cfgs', b'z_erofs_load_lz4_config'):
    print("    含 %-28s : %s" % (s.decode(), s in kern))
PYEOF

  "$PY" - "$SYS_DIAG" "$SYS_EXT" <<'PYEOF' || exit 1
import os, struct, subprocess, sys, tempfile, ctypes, ctypes.util, shutil
SYS_DIAG, SYS_EXT = sys.argv[1], sys.argv[2]
EROFSTOOL = "/Users/skeletondie/.workbuddy-ai/tools/erofs/erofs-utils/1.9.4/bin"
FSCK = os.path.join(EROFSTOOL, "fsck.erofs")
bad = 0

def sb(p):
    with open(p, "rb") as f:
        f.seek(1024); b = f.read(96)
    return (struct.unpack_from("<I", b, 0)[0],
            struct.unpack_from("<I", b, 8)[0],
            b[12],
            struct.unpack_from("<I", b, 80)[0])

def check_img(tag, p, want_incompat=0x1):
    global bad
    m, c, bb, i = sb(p)
    okk = (m == 0xE0F5E1E2 and i == want_incompat and bb == 12)
    print("    %-10s magic=%#x compat=%#x incompat=%#x blkszbits=%d  %s  (%d 字节)"
          % (tag, m, c, i, bb, "OK" if okk else "**不合格**", os.path.getsize(p)))
    if not okk:
        bad += 1
    r = subprocess.run([FSCK, p], capture_output=True, text=True)
    if r.returncode != 0:
        print("      ✗ fsck.erofs 报错")
        bad += 1
    else:
        print("      ✓ fsck.erofs 干净")

_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
_libc.listxattr.restype = ctypes.c_ssize_t
_libc.listxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int]

def has_xattr(p):
    n = _libc.listxattr(p.encode(), None, 0, 1)
    if n <= 0:
        return False
    buf = ctypes.create_string_buffer(n)
    n = _libc.listxattr(p.encode(), buf, n, 1)
    return any(x and not x.startswith(b"com.apple.")
               for x in buf.raw[:max(n, 0)].split(b"\0"))

def check_xattr(tag, img, paths):
    global bad
    tmp = tempfile.mkdtemp(prefix="pf_")
    try:
        miss = []
        checked = 0
        for p in paths:
            dst = os.path.join(tmp, os.path.basename(p) or "root")
            subprocess.run([FSCK, "--extract=" + dst, "--path=" + p, "--xattrs", img],
                           capture_output=True, text=True)
            # 若抽出来是目录（--path 指向目录），取里面第一个普通文件
            probe = dst
            if os.path.isdir(dst):
                probe = None
                for root, _dirs, files in os.walk(dst):
                    if files:
                        probe = os.path.join(root, files[0])
                        break
            if probe is None or not os.path.exists(probe):
                miss.append(p + "(抽不出)")
                continue
            checked += 1
            if not has_xattr(probe):
                miss.append(p)
            shutil.rmtree(dst, ignore_errors=True)
        if miss:
            print("    %-10s xattr 缺失 %d/%d: %s" % (tag, len(miss), checked + len(miss), miss[:3]))
            bad += 1
        else:
            print("    %-10s xattr 抽查 %d/%d 全部带 SELinux 标签" % (tag, checked, checked))
    except Exception as e:
        print("    %-10s xattr 抽查异常: %s" % (tag, e))
        bad += 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

print("    镜像超级块:")
check_img("system", SYS_DIAG)
check_img("system_ext", SYS_EXT)
print("    xattr 抽查:")
check_xattr("system", SYS_DIAG, ["/system/bin/init", "/system/etc/init/hw/init.rc"])
if os.path.exists(SYS_EXT):
    check_xattr("system_ext", SYS_EXT,
                ["/apex/com.android.compos.apex", "/lib64", "/framework", "/priv-app"])

# system_ext 修复版必须 compat 含 XATTR_FILTER(0x4)
if os.path.exists(SYS_EXT):
    _, c, _, _ = sb(SYS_EXT)
    if c & 0x4:
        print("    system_ext compat 含 XATTR_FILTER(0x4) ✓")
    else:
        print("    ✗ system_ext compat=%#x 缺 XATTR_FILTER —— 说明 xattr 全丢，拒绝刷入" % c)
        bad += 1

sys.exit(1 if bad else 0)
PYEOF
  [ $? -eq 0 ] || { err "预检未通过 —— 拒绝刷机"; exit 1; }
  ok "预检通过"
else
  warn "已跳过预检（--no-preflight）"
fi

say "boot  待刷镜像: $BOOT_USE"
say "system 诊断镜像: $SYS_DIAG ($(stat -f%z "$SYS_DIAG") 字节)"
say "system_ext 修复: $SYS_EXT ($(stat -f%z "$SYS_EXT" 2>/dev/null || echo 0) 字节)"

# ---- 1. 进 bootloader ----
say "步骤 1/7: 进入 bootloader"
if "$FB" devices 2>/dev/null | grep -q .; then
  ok "已在 bootloader"
elif "$ADB" devices 2>/dev/null | tail -n +2 | grep -qE "device|recovery"; then
  "$ADB" reboot bootloader
  sleep 6
else
  err "既没有 adb 也没有 fastboot 设备"
  echo
  echo "  请手动把手机送进 bootloader："
  echo "    音量+ 和 音量- 一起按住，再按住电源键约 10 秒，出现 bootloader 界面后松开。"
  echo "    (若完全无反应，先插充电器 10 分钟再试 —— 可能只是没电了)"
  exit 1
fi
# ★ 注意：fastboot 36.0.1 **没有** wait-for-device 子命令（那是 adb 的）。
#   之前写 "$FB" wait-for-device 会让整个刷机流程在这一行静默失败退出
#   （表现为"跑了 20 秒但什么都没刷"）。改成轮询 devices。
i=0
until "$FB" devices 2>/dev/null | grep -q .; do
  sleep 2; i=$((i+1)); [ $i -gt 40 ] && { err "等待 bootloader 超时"; exit 1; }
done
ok "bootloader 就绪"

# ---- 2. 刷回已知可用 boot（永远先做这步：把 fastbootd 能力拿回来）----
say "步骤 2/7: 刷回已知可用 boot（拿回 fastbootd 能力）"
if [ "$BOOT_USE" != "$BOOT_GOOD" ]; then
  say "         实验内核将放到最后一步再刷：$BOOT_USE"
fi
"$FB" flash boot "$BOOT_GOOD" 2>&1 | sed 's/^/    /'
[ "${PIPESTATUS[0]}" -eq 0 ] && ok "boot 刷入完成（已知可用）" || { err "boot 刷入失败"; exit 1; }

# ---- 3. 进 fastbootd ----
say "步骤 3/7: 进入 fastbootd（逻辑分区必须在用户空间刷）"
"$FB" reboot fastboot
i=0
until "$FB" devices 2>/dev/null | grep -q .; do
  sleep 2; i=$((i+1)); [ $i -gt 40 ] && { err "fastbootd 超时"; exit 1; }
done
sleep 3
ok "fastbootd 就绪"

# ---- 4. 刷诊断 system ----
say "步骤 4/7: 刷入诊断版 system"
"$FB" flash system "$SYS_DIAG" 2>&1 | sed 's/^/    /'
[ "${PIPESTATUS[0]}" -eq 0 ] && ok "system 刷入完成" || { err "system 刷入失败"; exit 1; }

# ---- 5. 刷修复版 system_ext ----
if [ -f "$SYS_EXT" ]; then
  say "步骤 5/7: 刷入修复版 system_ext"
  PSZ=$("$FB" getvar partition-size:system_ext 2>&1 | grep -o '0x[0-9a-fA-F]*' | tail -1)
  ESZ=$(stat -f%z "$SYS_EXT")
  if [ -n "$PSZ" ]; then
    PDEC=$((PSZ))
    if [ "$ESZ" -gt "$PDEC" ]; then
      warn "修复版比当前分区大 $((ESZ - PDEC)) 字节 —— fastboot 会自动扩容"
    fi
  fi
  "$FB" flash system_ext "$SYS_EXT" 2>&1 | sed 's/^/    /'
  [ "${PIPESTATUS[0]}" -eq 0 ] && ok "system_ext 刷入完成" || warn "system_ext 刷入失败（继续，先看 system 的效果）"
else
  say "步骤 5/7: 跳过 system_ext（$SYS_EXT 不存在）"
  warn "注意：system_ext 里 com.android.compos.apex 的 payload 仍是 incompat=0x3，4.19.157 挂不上"
fi

# ---- 6. 实验内核（可选）：放到最后，刷完再重启 ----
if [ "$BOOT_USE" != "$BOOT_GOOD" ]; then
  say "步骤 6/7: 刷入实验内核 boot（最后一步，system 已就位）"
  say "         来源: $BOOT_USE"
  say "         回到 bootloader（boot 分区只能在 bootloader 里刷）"
  "$FB" reboot bootloader
  i=0
  until "$FB" devices 2>/dev/null | grep -q .; do
    sleep 2; i=$((i+1)); [ $i -gt 40 ] && { err "回 bootloader 超时"; exit 1; }
  done
  sleep 2
  ok "bootloader 就绪"
  "$FB" flash boot "$BOOT_USE" 2>&1 | sed 's/^/    /'
  [ "${PIPESTATUS[0]}" -eq 0 ] && ok "实验内核刷入完成" || { err "实验内核刷入失败"; exit 1; }
  echo
  echo "  ★ 实验记录：本次 boot = $BOOT_USE"
  echo "     sha256 = $(shasum -a 256 "$BOOT_USE" | cut -c1-16)"
  echo "     若起不来 ⇒ 不加 --kernel 再跑一次，即刷回已知可用 boot。"
else
  say "步骤 6/7: 跳过实验内核（本次用已知可用 boot）"
fi

# ---- 7. 重启 ----
say "步骤 7/7: 重启到系统"
"$FB" reboot
ok "已发出重启指令"
echo
echo "  ★ 预期行为："
echo "    - apexd-bootstrap 即使失败，也不会再重启跑掉（诊断版已注释 reboot_on_failure）"
echo "    - 若 init 继续往下走，USB 可能真的起来 ⇒ adb 可用（ro.adb.secure=0，不需授权）"
echo "    - 无论如何，状态已倾倒到 /metadata/wbdiag/ ⇒ 进 TWRP 读："
echo "        mount /dev/block/by-name/metadata /metadata"
echo "        ls -la /metadata/wbdiag/"
echo "        cat /metadata/wbdiag/report_pre.txt"
echo "        cat /metadata/wbdiag/dmesg_pre.txt"
