#!/bin/bash
# ============================================================================
#  reflash_a16.sh —— Find X2 Pro 上「ColorOS 16 移植」的干净重刷 + 取证
#
#  设计原则（来自 2026-09-29/30 的事故复盘）
#  ---------------------------------------------------------------
#  1. boot 分区是当前最大变量：rawdump 证明最近一次「正常开机」的 initrd
#     与 X2 Pro 移植 boot 的 ramdisk 不符（内核 unpack 失败 → VFS panic）。
#     所以每次重刷**第一步就是刷回已知可用 boot**（sha256 fc60a85e…）。
#  2. 取证要先立基线：进 TWRP 先拉一次 rawdump 记 md5，启动失败后再拉一次
#     做 diff。rawdump 是环形区，不立基线无法判断「有没有新 dump」。
#  3. 不依赖 `fastboot wait-for-device`（本机 fastboot 36.0.2 没有该子命令，
#     曾导致静默空转），一律用轮询。
#  4. 判断 fastboot 命令成败必须取 PIPESTATUS[0]（管道后退出码是 sed 的）。
#
#  用法
#  ---------------------------------------------------------------
#    ./reflash_a16.sh --status          # 只看设备在哪
#    ./reflash_a16.sh --forensics       # (TWRP) ★先跑这个★ 读 /metadata/wbdiag 面包屑
#    ./reflash_a16.sh --baseline        # (TWRP) 拉 rawdump 立基线
#    ./reflash_a16.sh --flash           # 刷回已知可用 boot + 诊断 system/ext
#    ./reflash_a16.sh --observe [秒]    # 重启并观察 USB，然后拉 rawdump 对比
#    ./reflash_a16.sh --rawdump         # (TWRP) 拉 rawdump 并打印关键标记
#    ./reflash_a16.sh --markers         # (TWRP) 读 misc 偏移 512KB 的阶段标记
#    ./reflash_a16.sh --all             # forensics → baseline → flash → observe → rawdump
#
#  推荐顺序（2026-10-01 起）
#  ---------------------------------------------------------------
#    ① 手机进 TWRP → ./reflash_a16.sh --forensics   （零成本，可能一击命中）
#    ② 判读 f_trace.txt；若为空再走 ③
#    ③ ./reflash_a16.sh --all                       （刷加固版 v2 并取证）
#
#  刷机前请确认手机已解锁 BL、电量 > 60%。
# ============================================================================
set -u

BASE="$HOME/Documents/oppo/2026-安卓16"
PT="$HOME/Documents/oppo/platform-tools"
ADB="$PT/adb"
FB="$PT/fastboot"

# 2026-10-02: ★ boot 默认改用【permissive 版】。
#   原版 boot.img（dump_202707/boot.img）的 cmdline 里没有
#   androidboot.selinux=permissive，刷回去会让 apexd-bootstrap 重新被
#   SELinux 三重障碍挡死（见 05-分析/根因-已定位-SELinux三重障碍阻断apexd-bootstrap.md）。
#   permissive 版 = 原版 boot + cmdline 追加 "androidboot.selinux=permissive enforcing=0"
#   （只改 boot header offset 64 的 512 字节 cmdline 定长字段，kernel/ramdisk 一字未动，
#    由 03-脚本/patch_boot_cmdline.py 生成）。
#   想回到原版：BOOT_IMG="$BASE/04-日志/dump_202707/boot.img" ./reflash_a16.sh --flash
BOOT_IMG="${BOOT_IMG:-$BASE/07-重建/新增文件/boot_permissive.img}"
GOOD_BOOT="$BOOT_IMG"
GOOD_BOOT_SHA="1a02f68ff04d581fa1e353a43a0e500a29ba43b4c7a0058c7229bd0e2ba27d11"

# 2026-10-01: 默认改用【加固版 v2】。
#   v2 相对 v1 多了三件事：
#     · 4 个 boringssl 服务的命令换成 /system/bin/true（真正绕开自检，而不只是不重启）
#     · wb_diag.sh 增加 boringssl 探针（记录 rc / 文件是否存在 / SELinux 标签）
#     · 每阶段在 misc 偏移 512 KB 写标记（不依赖任何文件系统）
#   ⚠ 刷 v2 之前，务必先跑 --forensics 把 v1 已经写进 /metadata/wbdiag/ 的面包屑读出来！
# 2026-10-02: 默认改用【v5 = v4 + SELinux 标签修复】。
#   v4 把 netd 的内核 5.4 硬门限修掉后，netd 不再因「平台不支持」abort，
#   但暴露出下一层：/sys/fs/bpf 全空 ⇒ netd 报
#     "Failed to get program from /sys/fs/bpf/netd_shared/prog_netd_skfilter_allowlist_xtbpf"
#   根因（dmesg 原话）：
#     init: Command 'exec_start wb_netbpfload' ... failed:
#       File /system/bin/netbpfload(labeled "u:object_r:unlabeled:s0") has
#       incorrect label or no domain transition from u:r:init:s0 ...
#   —— erofs_rebuild.py 的 --replace 默认从「树里的同路径」取 xattr，
#   而 netbpfload / wb_diag.rc / wb_diag.sh / wb_netbpfload.rc 这几个
#   只在源镜像里有、树里没有 ⇒ xattr 全丢 ⇒ unlabeled。
#   v5 修法：--replace 支持 "#<树内模板>"，四个文件补上模板；
#            并给构建脚本加了【SELinux 标签断言】(10 项)，不通过就不许刷。
#   详见 05-分析/根因-netd内核5.4硬门限导致bootloop.md 与
#        05-分析/根因-SELinux标签丢失导致bpfloader无法启动.md
#
# 2026-10-02: 默认改用【v6 = v5 + netbpfload 的 4.19.236 硬门限修复 + BPF 专项探针】。
#   v5 修好标签后，wb_netbpfload 能启动了，但仍拿不到 BPF 程序 —— 因为
#   netbpfload 自身还有一道「平台内核版本」硬门限（反汇编定位，0xbe5c）：
#       Android V+ requires %d.%d kernel to be %d.%d.%d+.   → 4.19 kernel 需 >= 4.19.236
#   本机内核 4.19.157 < 4.19.236 ⇒ netbpfload 退出 ⇒ /sys/fs/bpf 依旧为空。
#   供体 OnePlus 8T 是 4.19.325 ⇒ 天然通过 —— 这就是「同 SoC 供体能启动、本机不能」的
#   决定性差异。
#   v6 修法（03-脚本/patch_netbpfload.py，共三处）：
#     0xbdfc  tbz  w0,#0,#0xc0dc  → nop        （越过 "25Q2 requires kernel 5.4."）
#     0xbe1c  tbnz w0,#0,#0xbe38  → b #0xbe38  （越过 "V+ only supports LTS kernels."）
#     0xbe5c  tbnz w0,#0,#0xbe90  → b #0xbe90  （越过 "V+ requires 4.19.236+"）★ 真凶
#   ⚠ 注意 G4/G5 的方向：原指令是「条件成立跳【正常】路径」，
#     必须改成无条件 b，不能像 G3 那样改 nop（那会掉进报错分支）。
#   netbpfload md5: 553aefcf… → 9a6bdd2ccc56cb2011b0e9c8f315b14f
#   另加 BPF 专项探针：on bpf-progs-loaded → exec_start wb_probe_bpf
#     （/system/bin/sh /system/etc/wb_diag.sh bpf → /metadata/wbdiag/probe_bpf.log，
#       内含 getprop / ls -laR /sys/fs/bpf / dmesg|grep bpf / logcat -t 200）
#   详见 05-分析/根因-netbpfload-419-236硬门限.md
SYS_DIAG="${SYS_DIAG:-$BASE/02-移植素材/a16_patched/system_diag6.img}"
SYS_EXT="${SYS_EXT:-$BASE/02-移植素材/a16_patched/system_ext_fix2.img}"

DUMPDIR="$BASE/04-日志/reflash_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$DUMPDIR"

C_R=$'\033[31m'; C_G=$'\033[32m'; C_Y=$'\033[33m'; C_B=$'\033[36m'; C_0=$'\033[0m'
say()  { echo "${C_B}[$(date +%H:%M:%S)]${C_0} $*"; }
ok()   { echo "  ${C_G}✓${C_0} $*"; }
warn() { echo "  ${C_Y}⚠${C_0} $*"; }
err()  { echo "  ${C_R}✗${C_0} $*"; }

# ---------------------------------------------------------------- 设备探测
dev_state() {
  # 输出: none | fastboot | fastbootd | adb | adb-recovery
  if "$FB" devices 2>/dev/null | grep -q .; then
    local p
    p=$("$FB" getvar current-slot 2>&1 | head -1)
    if "$FB" getvar is-userspace 2>/dev/null | grep -qi "yes"; then
      echo fastbootd
    else
      echo fastboot
    fi
  # 2026-10-01 修 bug：TWRP 的 adb 状态是 "recovery"，不是 "device"，
  # 原来只匹配 device$ 会把 TWRP 误判成 none，导致 --forensics/--flash 全部拒绝执行。
  elif "$ADB" devices 2>/dev/null | tail -n +2 | grep -qE "(device|recovery|sideload)$"; then
    if "$ADB" shell "test -f /sbin/recovery -o -d /twres" 2>/dev/null; then
      echo adb-recovery
    else
      echo adb
    fi
  else
    echo none
  fi
}

usb_probe() {
  # 返回 USB 上看到的 Android/Qualcomm 相关节点数
  system_profiler SPUSBDataType 2>/dev/null \
    | grep -Eci "oppo|oneplus|qualcomm|android|9008|QUSB|D00D" || true
}

wait_for() {  # wait_for <fastboot|adb|none≠> <超时秒>
  local want="$1" tmo="${2:-300}" i=0
  while [ $i -lt "$tmo" ]; do
    local s; s=$(dev_state)
    case "$want" in
      fastboot) [ "$s" = fastboot ] || [ "$s" = fastbootd ] && { echo "$s"; return 0; } ;;
      adb)      [ "$s" = adb ] || [ "$s" = adb-recovery ] && { echo "$s"; return 0; } ;;
      any)      [ "$s" != none ] && { echo "$s"; return 0; } ;;
    esac
    sleep 2; i=$((i+2))
  done
  return 1
}

# ---------------------------------------------------------------- 前置检查
precheck() {
  say "===== 前置检查 ====="
  local fail=0
  [ -x "$ADB" ] && ok "adb 就绪" || { err "缺 adb"; fail=1; }
  [ -x "$FB" ]  && ok "fastboot 就绪" || { err "缺 fastboot"; fail=1; }

  local sha; sha=$(shasum -a 256 "$GOOD_BOOT" | awk '{print $1}')
  if [ "$sha" = "$GOOD_BOOT_SHA" ]; then
    ok "boot 校验通过（permissive 版）"
  elif [ -f "$GOOD_BOOT" ]; then
    warn "boot 不是默认的 permissive 版（sha=$sha）"
    warn "  默认应为 $GOOD_BOOT_SHA"
    warn "  若这是你主动 BOOT_IMG= 指定的镜像，可忽略"
  else
    err "缺 boot 镜像: $GOOD_BOOT"; fail=1
  fi
  # ★ 关键：确认 cmdline 里真的有 androidboot.selinux=permissive
  #   （boot header offset 64 起是 512 字节定长 cmdline 字段）
  if [ -f "$GOOD_BOOT" ]; then
    if dd if="$GOOD_BOOT" bs=1 skip=64 count=512 2>/dev/null | strings | grep -q "androidboot.selinux=permissive"; then
      ok "cmdline 含 androidboot.selinux=permissive"
    else
      warn "cmdline 里【没有】androidboot.selinux=permissive"
      warn "  ⇒ apexd-bootstrap 会被 SELinux 挡死，A16 起不来"
      warn "  生成: 03-脚本/patch_boot_cmdline.py"
    fi
  fi
  if [ -f "$GOOD_BOOT" ]; then
    python3 - "$GOOD_BOOT" <<'PY' 2>/dev/null || true
import struct,sys,re
d=open(sys.argv[1],'rb').read(4096)
g=lambda o: struct.unpack_from('<I',d,o)[0]
k=g(8); ps=g(36); f=open(sys.argv[1],'rb'); f.seek(ps); blob=f.read(min(k,70000000))
m=re.search(rb'Linux version [^\x00\n]{10,90}', blob)
print("    kernel : %s" % (m.group(0).decode(errors='replace') if m else '?'))
print("    kernel_size=%d ramdisk_size=%d dtb_size=%d hv=%d ps=%d" % (k,g(16),g(1648),g(40),ps))
PY
  fi

  for f in "$SYS_DIAG" "$SYS_EXT"; do
    [ -f "$f" ] && ok "$(basename "$f") $(stat -f%z "$f") 字节" || { err "缺 $f"; fail=1; }
  done
  echo "  取证目录: $DUMPDIR"
  [ $fail -eq 0 ] && ok "前置检查通过" || err "前置检查有失败项"
  return $fail
}

# ---------------------------------------------------------------- rawdump
pull_rawdump() {   # pull_rawdump <输出文件>
  local out="$1"
  local s; s=$(dev_state)
  if [ "$s" != adb-recovery ] && [ "$s" != adb ]; then
    err "需要 TWRP/root adb 才能读 rawdump（当前: $s）"; return 1
  fi
  say "拉取 rawdump（128 MB）→ $out"
  "$ADB" exec-out "dd if=/dev/block/by-name/rawdump bs=1M 2>/dev/null" > "$out" 2>/dev/null
  local sz; sz=$(stat -f%z "$out" 2>/dev/null || echo 0)
  if [ "$sz" -ne 134217728 ]; then
    err "rawdump 尺寸异常: $sz（期望 134217728）"; return 1
  fi
  ok "rawdump 已拉取 $(shasum -a 256 "$out" | awk '{print $1}')"
}

rawdump_baseline() {
  pull_rawdump "$DUMPDIR/rawdump_before.bin" || return 1
  shasum -a 256 "$DUMPDIR/rawdump_before.bin" | awk '{print $1}' > "$DUMPDIR/rawdump_before.md5"
  ok "基线 md5 已记录: $(cat "$DUMPDIR/rawdump_before.md5")"
}

rawdump_report() {   # 打印关键标记
  local f="$1"
  [ -f "$f" ] || { err "缺 $f"; return 1; }
  say "rawdump 关键标记分析: $f"
  python3 - "$f" <<'PY'
import re,sys
d=open(sys.argv[1],'rb').read()
def hits(pat,label):
    n=len(re.findall(re.escape(pat), d))
    print("   %-34s %d" % (label, n))
    return n
print("   size = %d" % len(d))
hits(b'Kernel panic',                'Kernel panic')
hits(b'Unable to mount root fs',     'VFS unable to mount root fs')
hits(b'rootfs image is not initramfs','rootfs image is not initramfs')
hits(b'no cpio magic',               'no cpio magic')
hits(b'RAMDISK: gzip image found',   'RAMDISK gzip found (legacy initrd)')
hits(b'KERNEL_INIT_DONE',            'KERNEL_INIT_DONE (内核走完)')
hits(b'apexd',                       'apexd (二阶段用户空间)')
hits(b'init: ',                      'init: (一阶段)')
for m in re.finditer(rb'Freeing initrd memory: (\d+)K', d):
    print("   Freeing initrd memory: %sK" % m.group(1).decode())
for m in re.finditer(rb'Linux version [^\x00\n]{10,80}', d):
    print("   %s" % m.group(0).decode(errors='replace'))
for m in re.finditer(rb'Now kernel boot count (\d+)', d):
    print("   kernel boot count = %s" % m.group(1).decode())
PY
}

# ---------------------------------------------------------------- 刷机
do_flash() {
  say "===== 干净重刷：已知可用 boot + 诊断 system + 修复 system_ext ====="
  echo
  echo "${C_R}  ⚠ 刷之前请确认：已经跑过 ./reflash_a16.sh --forensics${C_0}"
  echo "${C_Y}     v1 写进 /metadata/wbdiag/ 的面包屑只会在这次刷机后被覆盖/追加，${C_0}"
  echo "${C_Y}     那是零成本就能拿到的答案，别把它刷掉。${C_0}"
  echo
  echo "${C_Y}  将执行:${C_0}"
  echo "    1) 刷 boot                  (permissive 版：$(basename "$GOOD_BOOT"))"
  echo "    2) 刷入 system_diag        (A16 重建版 + netd 门限补丁，已注释 reboot_on_failure)"
  echo "    3) 刷入 system_ext_fix     (A16 重建版)"
  echo "    4) 保留 vendor / odm / dtbo / vbmeta 不动"
  echo
  if [ "${WB_YES:-}" = "1" ]; then
    ok "WB_YES=1，跳过交互确认"
  else
    read -r -p "  确认继续? 输入大写 YES: " ans
    [ "$ans" = "YES" ] || { err "已取消"; exit 1; }
  fi

  local s; s=$(dev_state)
  if [ "$s" = adb ] || [ "$s" = adb-recovery ]; then
    say "从 adb 重启到 bootloader"
    "$ADB" reboot bootloader
    s=$(wait_for fastboot 60) || { err "未进入 bootloader"; return 1; }
  fi
  s=$(dev_state)
  if [ "$s" = fastbootd ]; then
    say "当前在 fastbootd，先回 bootloader"
    "$FB" reboot bootloader >/dev/null 2>&1
    s=$(wait_for fastboot 60) || { err "未回到 bootloader"; return 1; }
  fi
  [ "$(dev_state)" = fastboot ] || { err "当前不在 bootloader（$(dev_state)）"; return 1; }
  ok "bootloader 就绪"

  say "步骤 1/3: 刷回已知可用 boot"
  "$FB" flash boot "$GOOD_BOOT" 2>&1 | sed 's/^/    /'
  local rc=${PIPESTATUS[0]}
  [ "$rc" -eq 0 ] && ok "boot 刷入完成" || { err "boot 刷入失败 rc=$rc"; return 1; }

  say "步骤 2/3: 进入 fastbootd"
  "$FB" reboot fastboot >/dev/null 2>&1
  s=$(wait_for fastboot 90) || { err "fastbootd 超时"; return 1; }
  [ "$(dev_state)" = fastbootd ] && ok "fastbootd 就绪" || warn "未确认 fastbootd（$(dev_state)）"

  say "步骤 3/3: 刷入 system_diag / system_ext_fix"
  local part src
  for pair in "system:$SYS_DIAG" "system_ext:$SYS_EXT"; do
    part="${pair%%:*}"; src="${pair#*:}"
    say "  flash $part <- $(basename "$src")"
    "$FB" flash "$part" "$src" 2>&1 | sed 's/^/    /'
    rc=${PIPESTATUS[0]}
    [ "$rc" -eq 0 ] && ok "$part 完成" || { err "$part 失败 rc=$rc"; return 1; }
  done

  ok "重刷完成"
  say "重启到系统"
  "$FB" reboot >/dev/null 2>&1
  ok "已发出重启指令"
}

# ---------------------------------------------------------------- 观察
do_observe() {
  local tmo="${1:-150}" i=0
  say "===== 观察启动（最长 ${tmo}s，每 3s 采一次）====="
  : > "$DUMPDIR/bootwatch.log"
  while [ $i -lt "$tmo" ]; do
    sleep 3; i=$((i+3))
    local st usb
    st=$(dev_state); usb=$(usb_probe)
    printf "%4ds  state=%-12s usb_nodes=%s\n" "$i" "$st" "$usb" | tee -a "$DUMPDIR/bootwatch.log"
    if [ "$st" = adb ] || [ "$st" = adb-recovery ]; then
      ok "设备已进用户空间（$st）—— 启动成功或已到 recovery"
      break
    fi
  done
  local fin; fin=$(dev_state)
  echo
  case "$fin" in
    adb)          ok "结论: 进入系统（adb 可用）" ;;
    adb-recovery) ok "结论: 进入 recovery" ;;
    fastboot|fastbootd) warn "结论: 落回 bootloader ⇒ 启动失败（无 USB 枚举或自行重启）" ;;
    none)         warn "结论: 全程无 USB 节点 ⇒ 失败在内核/一阶段 init 之前" ;;
  esac
  echo "  观察日志: $DUMPDIR/bootwatch.log"
}

do_rawdump() {
  local out="$DUMPDIR/rawdump_after.bin"
  pull_rawdump "$out" || return 1
  rawdump_report "$out"
  if [ -f "$DUMPDIR/rawdump_before.md5" ]; then
    local now before
    now=$(shasum -a 256 "$out" | awk '{print $1}')
    before=$(cat "$DUMPDIR/rawdump_before.md5")
    echo
    if [ "$now" = "$before" ]; then
      warn "rawdump 与基线【相同】⇒ 本次启动【没有产生新 dump】（纯卡死，非 panic）"
    else
      ok "rawdump 与基线【不同】⇒ 产生了新 dump，上面标记即为本次失败原因"
    fi
  fi
}

# ---------------------------------------------------------------- 现场取证
# 2026-10-01 新增。
# 依据：两台机器的 fstab 里 `/metadata` 都带 `first_stage_mount` 标志 —— 也就是
#      说 first-stage init 就把它挂上了，**早于**二阶段 init 的 on early-init。
#      所以 2026-09-29 23:18 刷入的 system_diag 写的面包屑
#      （/metadata/wbdiag/trace.txt）很可能仍然留在手机上。
#      这条路零成本、不用刷机，应该永远优先于任何重刷实验。
ashell() { "$ADB" shell "$1" 2>&1; }

grab() {   # grab <文件名> <设备内命令>
  local name="$1" cmd="$2"
  { echo "\$ $cmd"; ashell "$cmd"; echo; } >> "$DUMPDIR/f_$name.txt"
}

do_forensics() {
  local s; s=$(dev_state)
  if [ "$s" != adb-recovery ] && [ "$s" != adb ]; then
    err "需要 TWRP/root adb 才能取证（当前: $s）"
    echo "    请把手机开机进 TWRP（或 bootloader 下 fastboot boot 你的 TWRP），再重跑本命令。"
    return 1
  fi
  say "===== 现场取证（全部只读，不改动设备）====="
  : > "$DUMPDIR/forensics.log"
  for f in f_*.txt; do :; done

  rm -f "$DUMPDIR"/f_*.txt

  # ---- 0) 确保 /metadata 已挂载 ----
  # ★ TWRP 默认不一定挂 /metadata（它是 first_stage_mount，由 Android 的
  #   first-stage init 挂载；TWRP 是独立环境，不会自动挂）。
  #   不挂就读不到任何面包屑 ⇒ 这一步是取证的前提。
  say "0) 挂载 /metadata（TWRP 默认可能没挂）"
  grab mount_metadata 'grep -q " /metadata " /proc/mounts || { mkdir -p /metadata; mount -t ext4 /dev/block/by-name/metadata /metadata 2>&1 || mount /dev/block/by-name/metadata /metadata 2>&1; }; echo "--- /proc/mounts 中的 metadata ---"; grep metadata /proc/mounts; echo "--- /metadata 顶层 ---"; ls -la /metadata 2>&1 | head -20'
  cat "$DUMPDIR/f_mount_metadata.txt" 2>/dev/null | sed 's/^/    /'

  say "1) ★ /metadata/wbdiag —— 诊断面包屑（本命令存在的唯一理由）"
  grab wbdiag_ls   'ls -laR /metadata/wbdiag 2>&1'

  # ---- ★ v3 标记文件（init 内建 write，内容恒为 "1"，看【存在性】）----
  # ★ 注意：v3 的文件名与 v1 完全不同。v1 是 report_*.txt / trace.txt，
  #   v3 是 NN_<阶段> 空标记文件（10_earlyinit / 18_pre_apexdbootstrap ...）。
  #   取证必须按 v3 的命名读，否则除目录列表外全部落空。
  grab v3_markers 'cd /metadata/wbdiag 2>/dev/null || exit 0; for f in 00_selinux_enforce 10_earlyinit 18_pre_apexdbootstrap 20_post_apexdbootstrap 21a_apexdbootstrap_running 21b_apexdbootstrap_stopped 22_apexd_status_ready 23_wb_probe_stopped 30_on_init 31a_boringssl64_running 31b_boringssl64_stopped 40_on_late_init 42_on_early_fs 44_on_post_fs 46_on_late_fs 48_on_post_fs_data 50_on_boot 60_loadbpf_start 61_loadbpf_done 62_bpfprogs_loaded; do if [ -e "$f" ]; then echo "存在  $f  (值=$(cat "$f" 2>/dev/null))"; else echo "缺失  $f"; fi; done'

  # ---- ★★ 最关键：apexd-bootstrap 的真实报错与退出码 ----
  # 这是 v3 存在的全部意义：wrapper 把 apexd --bootstrap 的 stdout/stderr/
  # 退出码、/apex 前后状态、loop/mapper 设备、/metadata 可写性、dmesg 尾部
  # 全部落盘。v1/v2 只能看到"失败"两个字。
  grab apexd_log      'cat /metadata/wbdiag/apexd_bootstrap.log 2>&1'
  grab apexd_log_tail 'tail -150 /metadata/wbdiag/apexd_bootstrap.log 2>&1'

  # ---- wb_diag.sh 的分阶段现场（含 /proc/mounts、/apex、linker64 实况）----
  grab probe_early 'cat /metadata/wbdiag/probe_early.log 2>&1'
  grab probe_late  'cat /metadata/wbdiag/probe_late.log 2>&1'
  # ---- ★ v6 新增：BPF 专项现场（getprop / ls -laR /sys/fs/bpf / dmesg|grep bpf / logcat）----
  grab probe_bpf   'cat /metadata/wbdiag/probe_bpf.log 2>&1'
  # ---- ★ v6 新增：直接问内核 BPF 状态（不依赖任何落盘文件）----
  grab bpf_state   'echo "== /sys/fs/bpf =="; ls -laR /sys/fs/bpf 2>&1; echo "== getprop =="; getprop 2>/dev/null | grep -i "bpf\|netd" ; echo "== /sys/fs/bpf/netd_shared =="; ls -la /sys/fs/bpf/netd_shared 2>&1'

  # ---- v1 兼容（旧命名，仅历史镜像会用）----
  grab trace       'cat /metadata/wbdiag/trace.txt 2>&1'
  grab report_earlyinit 'cat /metadata/wbdiag/report_earlyinit.txt 2>&1'
  grab report_postapexd 'cat /metadata/wbdiag/report_postapexd.txt 2>&1'
  grab report_init 'cat /metadata/wbdiag/report_init.txt 2>&1'
  grab report_lateinit  'cat /metadata/wbdiag/report_lateinit.txt 2>&1'
  grab report_boot      'cat /metadata/wbdiag/report_boot.txt 2>&1'

  # ---- 本地即时判定：直接告诉用户卡在哪一步 ----
  echo "    ---- 阶段判定（按 v3 标记的存在性推断）----"
  local mk="$DUMPDIR/f_v3_markers.txt"
  if [ -s "$mk" ]; then
    sed 's/^/    /' "$mk"
    echo
    if   grep -q '存在.*50_on_boot'            "$mk"; then
      echo "    ⇒ 启动走到了 on boot（远超 apexd 阶段）"
    elif grep -q '存在.*30_on_init'            "$mk"; then
      echo "    ⇒ 已过 on init 起点；对照 apexd_bootstrap.log 看 apexd 结果"
    elif grep -q '存在.*18_pre_apexdbootstrap' "$mk"; then
      echo "    ⇒ 停在 apexd-bootstrap 前后 ⇒ 重点看 f_apexd_log_tail.txt"
    elif grep -q '存在.*10_earlyinit'          "$mk"; then
      echo "    ⇒ 进了 on early-init，但没到 apexd-bootstrap ⇒ 卡在 early-init 中段"
    else
      echo "    ⇒ 连 10_earlyinit 都没有 ⇒ init 没进 on early-init（或 /metadata 未挂载）"
    fi
  else
    echo "    ⇒ 读不到标记文件（见下方 f_wbdiag_ls.txt）"
  fi
  echo
  echo "    ---- apexd_bootstrap.log 尾部 ----"
  cat "$DUMPDIR/f_apexd_log_tail.txt" 2>/dev/null | tail -60 | sed 's/^/    /'
  echo
  echo "    ---- /metadata/wbdiag 目录列表 ----"
  cat "$DUMPDIR/f_wbdiag_ls.txt" 2>/dev/null | head -40 | sed 's/^/    /'

  say "2) /metadata 全景 + bootstat"
  grab metadata_ls  'ls -la /metadata 2>&1'
  grab metadata_all 'ls -laR /metadata 2>/dev/null | head -200'
  grab bootreason   'cat /metadata/bootstat/persist.sys.boot.reason 2>&1'
  grab bootstat_ls  'ls -la /metadata/bootstat 2>&1'

  say "3) 备用落点 /data/wbdiag + dropbox"
  grab data_wbdiag  'ls -la /data/wbdiag 2>&1; cat /data/wbdiag/trace.txt 2>&1'
  grab data_ls      'ls -la /data 2>&1'
  grab dropbox      'ls -la /data/system/dropbox 2>/dev/null | tail -40'

  say "4) pstore / last_kmsg / misc(BCB)"
  grab pstore_ls    'ls -laR /sys/fs/pstore 2>&1; cat /sys/fs/pstore/* 2>/dev/null | head -100'
  grab lastkmsg     'cat /proc/last_kmsg 2>&1 | tail -200'
  grab misc_bcb     'dd if=/dev/block/by-name/misc bs=1 count=64 2>/dev/null | od -c'
  grab misc_ls      'ls -la /dev/block/by-name/ 2>&1'

  say "5) 分区实况（对照 lpdump，确认 metadata/oplusreserve* 是否存在）"
  grab byname       'ls -la /dev/block/by-name/ 2>&1'

  say "6) rawdump 标记分析"
  if pull_rawdump "$DUMPDIR/rawdump_forensics.bin" >/dev/null 2>&1; then
    rawdump_report "$DUMPDIR/rawdump_forensics.bin"
  else
    warn "rawdump 拉取失败（可忽略）"
  fi

  echo
  say "取证文件：$DUMPDIR/f_*.txt"
  echo "${C_Y}  ★ 判读要点${C_0}"
  echo "    · f_trace.txt 非空 ⇒ 直接读出 init 走到哪一步死的（阶段名 + uptime）"
  echo "    · f_wbdiag_ls.txt 显示目录不存在/为空 ⇒ 二阶段 init 根本没跑到 on early-init"
  echo "    · 两者都空 ⇒ 失败在 first-stage（比二阶段更早）"
}

# 读原始标记分区（配合加固版诊断：每阶段在 oplusreserve1 打一个标记）
do_markers() {
  local s; s=$(dev_state)
  if [ "$s" != adb-recovery ] && [ "$s" != adb ]; then
    err "需要 TWRP/root adb（当前: $s）"; return 1
  fi
  say "读取原始标记（misc 偏移 512 KB 起，槽宽 64 字节，槽号 = 阶段序号）"
  echo "  槽号: 1 earlyinit  2 postapexd  3 init  4 lateinit  5 earlyfs"
  echo "        6 postfs     7 postfsdata 8 boot  9 apexready"
  ashell 'dd if=/dev/block/by-name/misc bs=64 skip=8192 count=16 2>/dev/null' \
    | tr -d '\000' | sed 's/^/    /'
}

# ---------------------------------------------------------------- 入口
case "${1:-}" in
  --status)
    say "设备状态: $(dev_state)   USB 相关节点: $(usb_probe)"
    ;;
  --forensics) do_forensics ;;
  --markers)   do_markers ;;
  --baseline)  precheck && rawdump_baseline ;;
  --flash)     precheck && do_flash ;;
  --observe)   do_observe "${2:-150}" ;;
  --rawdump)   rawdump_report "$DUMPDIR/rawdump_after.bin" 2>/dev/null; do_rawdump ;;
  --all)       precheck && do_forensics && rawdump_baseline && do_flash && do_observe 180 && do_rawdump ;;
  *)
    sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
    ;;
esac
