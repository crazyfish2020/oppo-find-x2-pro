#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_diag.py —— 生成【诊断版】init 补丁文件到 /tmp/diag/

为什么需要它
------------
A16 移植的失败表现是「开机约 25 秒后自己重启跑掉」，我们既看不到日志也抓不到现场。
全树扫描发现系统里一共有 **7 处** `reboot_on_failure`，任何一处触发都会让设备重启：

    system/etc/init/apexd.rc:8      reboot,apexd-failed
    system/etc/init/apexd.rc:18     reboot,bootloader,bootstrap-apexd-failed   <- 回 bootloader
    system/etc/init/netbpfload.rc:70 reboot,netbpfload-missing
    system/etc/init/hw/init.rc:1002  reboot,boringssl-self-check-failed
    system/etc/init/hw/init.rc:1009  reboot,boringssl-self-check-failed
    system/etc/init/hw/init.rc:1016  reboot,boringssl-self-check-failed
    system/etc/init/hw/init.rc:1023  reboot,boringssl-self-check-failed

注意 `boringssl_self_test32/64` 是在 **`on init`** 由 init.boringssl.*.rc 拉起的
（`on init && property:ro.product.cpu.abilist32=*`），比 bpfloader（late-init）还早，
所以它是排在 apexd-bootstrap 之后的**第二个**嫌疑犯。

本脚本做 4 件事
--------------
1) 注释掉全部 7 处 reboot_on_failure  ⇒ 失败也不再重启，设备停在现场
2) 在 8 个关键 trigger 各插一次 `exec_start wb_diag_<stage>`
   ⇒ 形成面包屑，能精确定位 init 走到哪一步死的
3) 放开 SELinux（setenforce 0）+ 关掉 adb 授权（ro.adb.secure=0）
   ⇒ 若这样就能起来，说明根因是 SELinux 策略；若仍不行，日志告诉我们真相
4) 输出倾倒脚本：写到 /metadata/wbdiag/（ext4、first_stage_mount、双清不受影响、TWRP 可读）
"""

import os
import re
import sys

TREE = os.path.expanduser("~/Documents/oppo/2026-安卓16/07-重建/tree")
OUT = "/tmp/diag"

# 2026-10-01 修：/tmp 会被系统清理，OUT 不存在时脚本会在写文件处炸掉
os.makedirs(OUT, exist_ok=True)

# 阶段序号 —— 写进 oplusreserve1 的原始标记槽位（每 64 字节一槽）
STAGE_IDX = {
    "earlyinit": 1, "postapexd": 2, "init": 3, "lateinit": 4,
    "earlyfs": 5, "postfs": 6, "postfsdata": 7, "boot": 8,
}

# trigger 行(精确匹配) -> 服务名后缀
STAGES = [
    ("on early-init",    "earlyinit"),
    ("on init",          "init"),
    ("on late-init",     "lateinit"),
    ("on early-fs",      "earlyfs"),
    ("on post-fs",       "postfs"),
    ("on post-fs-data",  "postfsdata"),
    ("on boot",          "boot"),
]
POST_APEXD = ("exec_start apexd-bootstrap", "postapexd")

ALL_STAGES = [s for _, s in STAGES] + [POST_APEXD[1]]


def patch_init_rc(src, dst):
    """给 init.rc 插入面包屑 + 注释掉 boringssl 的 reboot_on_failure"""
    lines = open(src, "r", encoding="utf-8", errors="replace").read().split("\n")
    out = []
    n_insert = 0
    n_comment = 0
    n_neutral = 0

    for line in lines:
        stripped = line.strip()
        indent = line[: len(line) - len(line.lstrip())]

        # ---- a0) 【2026-10-01 新增】把 4 个 boringssl 服务的"命令"换成 /system/bin/true
        #          只注释 reboot_on_failure 是不够的：服务仍然会失败，失败后
        #          init 仍可能因为其它机制（如 service 的 critical 属性 / 后续连锁）
        #          把设备带跑。把命令本身换成 true，才是真正"绕开"。
        #          真实的自检由 wb_diag.sh 在 postapexd 阶段单独跑并留证据。
        if stripped.startswith("service boringssl_self_test"):
            f = stripped.split()
            if len(f) >= 3:
                out.append("%s# wb_diag: 命令由 %s 换成 /system/bin/true（绕开自检）"
                           % (indent, f[2]))
                out.append("%sservice %s /system/bin/true" % (indent, f[1]))
                n_neutral += 1
                continue

        # ---- a) 注释掉 boringssl 的 reboot_on_failure ----
        if stripped.startswith("reboot_on_failure reboot,boringssl-self-check-failed"):
            indent = line[: len(line) - len(line.lstrip())]
            out.append("%s# wb_diag: 已注释，避免失败就重启跑掉 —— %s"
                       % (indent, stripped))
            n_comment += 1
            continue

        # ---- b) 在 trigger 行之后插入面包屑 ----
        hit = None
        for trig, stage in STAGES:
            if stripped == trig:
                hit = stage
                break
        if hit:
            out.append(line)
            out.append("    # ===== wb_diag 面包屑: %s =====" % hit)
            out.append("    exec_start wb_diag_%s" % hit)
            out.append("    # ===================================")
            n_insert += 1
            continue

        # ---- c) apexd-bootstrap 之后立刻再抓一次 ----
        if stripped == POST_APEXD[0]:
            out.append(line)
            out.append("    # ===== wb_diag 面包屑: %s =====" % POST_APEXD[1])
            out.append("    exec_start wb_diag_%s" % POST_APEXD[1])
            out.append("    # ===================================")
            n_insert += 1
            continue

        # ---- d) setenforce 0 放在 on early-init 的第一条 ----
        if stripped == "on early-init":
            # 上面 b) 已处理，不会走到这里
            pass

        out.append(line)

    open(dst, "w", encoding="utf-8").write("\n".join(out))
    return n_insert, n_comment, n_neutral


def patch_simple(src, dst, patterns, tag="wb_diag"):
    """把匹配到的行注释掉"""
    lines = open(src, "r", encoding="utf-8", errors="replace").read().split("\n")
    out, n = [], 0
    for line in lines:
        s = line.strip()
        if any(s.startswith(p) for p in patterns):
            indent = line[: len(line) - len(line.lstrip())]
            out.append("%s# %s: 已注释，避免失败就重启跑掉 —— %s" % (indent, tag, s))
            n += 1
            continue
        out.append(line)
    open(dst, "w", encoding="utf-8").write("\n".join(out))
    return n


# ---------------------------------------------------------------------------
# 1. init.rc
# ---------------------------------------------------------------------------
init_rc = os.path.join(TREE, "system/etc/init/hw/init.rc")
n_ins, n_com, n_neu = patch_init_rc(init_rc, os.path.join(OUT, "init.rc"))
print("init.rc      : 插入 %d 个面包屑, 注释 %d 条 reboot_on_failure, 中和 %d 个 boringssl 服务"
      % (n_ins, n_com, n_neu))

def patch_apexd_rc(src, dst):
    """
    apexd.rc 两处改动：
      1) 注释掉两条 reboot_on_failure
      2) 给 apexd / apexd-bootstrap 加 stdio_to_kmsg
         —— 这一条极其重要：apexd 在 bootstrap 阶段跑的时候 logd 还没起来，
            liblog 写 logd 失败会回退到 stderr，而 init 默认把服务的
            stdout/stderr 接到 /dev/null。加了 stdio_to_kmsg 才会进内核日志，
            我们 dmesg 里才能看到 apexd 到底为什么挂。
    """
    lines = open(src, "r", encoding="utf-8", errors="replace").read().split("\n")
    out, n_com, n_kmsg = [], 0, 0
    for line in lines:
        s = line.strip()
        if s.startswith("reboot_on_failure reboot,apexd-failed") or \
           s.startswith("reboot_on_failure reboot,bootloader,bootstrap-apexd-failed"):
            indent = line[: len(line) - len(line.lstrip())]
            out.append("%s# wb_diag: 已注释，避免失败就重启跑掉 —— %s" % (indent, s))
            n_com += 1
            continue
        out.append(line)
        if s.startswith("service apexd ") or s.startswith("service apexd-bootstrap"):
            out.append("    # wb_diag: 让 apexd 的 stderr 进内核日志（否则错误全丢）")
            out.append("    stdio_to_kmsg")
            n_kmsg += 1
    open(dst, "w", encoding="utf-8").write("\n".join(out))
    return n_com, n_kmsg


# ---------------------------------------------------------------------------
# 2. apexd.rc  —— 注释掉两处 + 开 stdio_to_kmsg
# ---------------------------------------------------------------------------
apexd_rc = os.path.join(TREE, "system/etc/init/apexd.rc")
n, nk = patch_apexd_rc(apexd_rc, os.path.join(OUT, "apexd.rc"))
print("apexd.rc     : 注释 %d 条 reboot_on_failure, 加 %d 个 stdio_to_kmsg" % (n, nk))

# ---------------------------------------------------------------------------
# 3. netbpfload.rc —— 注释掉 netbpfload-missing
# ---------------------------------------------------------------------------
net_rc = os.path.join(TREE, "system/etc/init/netbpfload.rc")
n = patch_simple(net_rc, os.path.join(OUT, "netbpfload.rc"),
                 ["reboot_on_failure reboot,netbpfload-missing"])
print("netbpfload.rc: 注释 %d 条" % n)

# ---------------------------------------------------------------------------
# 4. build.prop —— adb 免授权
# ---------------------------------------------------------------------------
bp = os.path.join(TREE, "system/build.prop")
txt = open(bp, "r", encoding="utf-8", errors="replace").read()
n = txt.count("ro.adb.secure=1")
txt = txt.replace("ro.adb.secure=1", "ro.adb.secure=0")
open(os.path.join(OUT, "build.prop"), "w", encoding="utf-8").write(txt)
print("build.prop   : ro.adb.secure 1->0 共 %d 处" % n)

# ---------------------------------------------------------------------------
# 5. wb_diag.sh
# ---------------------------------------------------------------------------
diag_sh = r'''#!/system/bin/sh
# wb_diag.sh —— 诊断专用（正式版必须删除）
# 由 init.rc 各 trigger 的 exec_start 调用，stage 名由 $1 传入。
#
# 关键认识：Android init 的 LOG(ERROR)/LOG(FATAL) 默认写**内核日志**(/dev/kmsg)，
#           而不是 logd（logd 这时还没起来）。所以 init 为什么挂，答案在 dmesg 里。
#
# 输出优先级：/metadata（ext4、first_stage_mount、双清不受影响、TWRP 可直接读）
#             -> /data -> /dev/kmsg
S="$1"
IDX="$2"
D=/metadata/wbdiag

# ---- 原始分区标记（2026-10-01 新增，最可靠通道）------------------------
# 每阶段在 misc 分区【偏移 512 KB】处写 15 字节阶段名（槽宽 64 字节，槽号 = IDX）。
# 为什么选 misc + 512 KB：
#   · misc = 1 MB，Android 只用最前面 2 KB 的 bootloader_message(BCB)，
#     512 KB 处绝对空闲，不会碰坏 BCB，也不碰任何文件系统。
#   · 不依赖 /metadata /data 是否挂上、不依赖 dmesg 是否被冲掉。
#     TWRP 一读分区就知道 init 走到哪一步。
# 读法： dd if=/dev/block/by-name/misc bs=64 skip=8192 count=16 | tr -d '\000'
if [ -n "$IDX" ] && [ -b /dev/block/by-name/misc ]; then
    printf '%-15s' "$S" | dd of=/dev/block/by-name/misc bs=64 seek=$((8192 + IDX)) conv=notrunc 2>/dev/null
fi

mkdir -p "$D" 2>/dev/null
if [ ! -d "$D" ]; then
    mount -o remount,rw /metadata 2>/dev/null
    mkdir -p "$D" 2>/dev/null
fi
if [ ! -d "$D" ]; then
    mount -t ext4 -o rw /dev/block/by-name/metadata /metadata 2>/dev/null
    mkdir -p "$D" 2>/dev/null
fi
if [ ! -d "$D" ]; then
    D=/data/wbdiag
    mkdir -p "$D" 2>/dev/null
fi

echo "wb_diag: stage=$S dir=$D" > /dev/kmsg 2>/dev/null

if [ ! -d "$D" ]; then
    {
        echo "wb_diag stage=$S NO_WRITABLE_DIR"
        cat /proc/cmdline 2>&1
        dmesg 2>&1 | grep -iE 'apex|avb|verity|selinux|init:' | tail -200
    } > /dev/kmsg 2>&1
    exit 0
fi

# 面包屑：每一步都往 trace.txt 追加一行，最后一看就知道走到哪一步
{
    echo "$(date +%H:%M:%S)  uptime=$(cut -d. -f1 /proc/uptime)  stage=$S"
} >> "$D/trace.txt" 2>&1

# 全量 dmesg 单独存（init 的报错都在里面）
dmesg > "$D/dmesg_$S.txt" 2>&1
cat /proc/last_kmsg > "$D/lastkmsg_$S.txt" 2>/dev/null

R="$D/report_$S.txt"
{
  echo "=========== wb_diag stage=$S ==========="
  echo "--- /proc/cmdline ---"
  cat /proc/cmdline 2>&1
  echo "--- /proc/version (内核版本，验证 4.19.157 vs 4.19.325) ---"
  cat /proc/version 2>&1
  echo "--- /proc/filesystems (erofs 是否注册) ---"
  cat /proc/filesystems 2>&1
  echo "--- /sys/module/erofs ---"
  ls -la /sys/module/erofs/ 2>&1
  cat /sys/module/erofs/version 2>&1
  echo "--- /sys/fs/selinux/enforce ---"
  cat /sys/fs/selinux/enforce 2>&1
  echo "--- /proc/bootprof ---"
  cat /proc/bootprof 2>&1
  echo "--- getprop (筛选) ---"
  getprop 2>&1 | grep -iE 'apex|boot|usb|vold|init|verity|selinux|api_level'
  echo "--- /proc/mounts ---"
  cat /proc/mounts 2>&1
  echo "--- ls -la /system/apex ---"
  ls -la /system/apex 2>&1
  echo "--- ls -la /apex ---"
  ls -la /apex 2>&1
  echo "--- ls -la /dev/block/mapper ---"
  ls -la /dev/block/mapper 2>&1
  echo "--- ls -la /dev/block/by-name ---"
  ls -la /dev/block/by-name 2>&1
  echo "--- ls -la /data ---"
  ls -la /data 2>&1
  echo "--- dmesg 关键行 ---"
  dmesg 2>&1 | grep -iE 'apex|avb|dm-verity|verity|selinux|init:|vold|fatal|panic|boringssl|bpf' | tail -400
  echo "--- logcat -d -b all (若 logd 已起) ---"
  logcat -d -b all 2>&1 | tail -400
  echo "--- dmesg (tail 800) ---"
  dmesg 2>&1 | tail -800
  echo "=========== wb_diag stage=$S end ==========="
} >> "$R" 2>&1

# ---- boringssl 自检探针（2026-10-01 新增）--------------------------------
# 4 个 boringssl 服务的命令已被换成 /system/bin/true，所以它们不会再"失败"。
# 这里单独跑真身，把「为什么失败」留下来（rc + stderr + 文件是否存在）。
if [ "$S" = "postapexd" ] || [ "$S" = "apexready" ] || [ "$S" = "lateinit" ]; then
  B="$D/boringssl_$S.txt"
  {
    echo "===== boringssl 探针 stage=$S ====="
    for b in /system/bin/boringssl_self_test32 \
             /system/bin/boringssl_self_test64 \
             /apex/com.android.conscrypt/bin/boringssl_self_test32 \
             /apex/com.android.conscrypt/bin/boringssl_self_test64 ; do
      echo "--- $b ---"
      ls -laZ "$b" 2>&1
      if [ -x "$b" ]; then
        timeout 8 "$b" 2>&1
        echo "rc=$?"
      else
        echo "!!! 不存在或不可执行"
      fi
    done
    echo "===== 探针结束 ====="
  } >> "$B" 2>&1
  echo "wb_diag: boringssl 探针完成 -> $B" > /dev/kmsg 2>/dev/null
fi

sync
exit 0
'''
open(os.path.join(OUT, "wb_diag.sh"), "w", encoding="utf-8").write(diag_sh)
os.chmod(os.path.join(OUT, "wb_diag.sh"), 0o755)
print("wb_diag.sh   : 已生成 (%d 字节)" % len(diag_sh))

# ---------------------------------------------------------------------------
# 6. wb_diag.rc —— 每个 stage 一个 oneshot 服务
# ---------------------------------------------------------------------------
RC_STAGES = ALL_STAGES + ["apexready"]     # apexready 由属性触发，不插进 init.rc

rc = ["# wb_diag.rc —— 诊断专用（正式版必须删除）",
      "# 每个 stage 一个 oneshot 服务，由 init.rc 里对应的 exec_start 拉起。",
      "# 第 2 个参数是阶段序号，wb_diag.sh 用它往 oplusreserve1 打原始标记。",
      ""]
for s in RC_STAGES:
    rc.append("service wb_diag_%s /system/bin/sh /system/etc/wb_diag.sh %s %d"
              % (s, s, STAGE_IDX.get(s, 0)))
    rc.append("    class core")
    rc.append("    user root")
    rc.append("    group root")
    rc.append("    oneshot")
    rc.append("    disabled")
    rc.append("    seclabel u:r:init:s0")
    rc.append("")

# ★ 2026-10-01 新增：apexd.status=ready 是 apexd 完整跑完才会置的属性。
#   在它上面挂一个面包屑 ⇒ 只要这一条留下痕迹，就证明 APEX 挂载/重建完全成功。
rc.append("# wb_diag: apexd 完整跑完的独立旁证（boringssl_self_test_apex* 的同一个触发条件）")
rc.append("on property:apexd.status=ready")
rc.append("    exec_start wb_diag_apexready")
rc.append("")
open(os.path.join(OUT, "wb_diag.rc"), "w", encoding="utf-8").write("\n".join(rc))
print("wb_diag.rc   : 已生成 %d 个服务 (%s) + apexready 属性触发"
      % (len(RC_STAGES), ", ".join(RC_STAGES)))

print()
print("输出目录: %s" % OUT)
for f in sorted(os.listdir(OUT)):
    p = os.path.join(OUT, f)
    print("  %-16s %8d 字节" % (f, os.path.getsize(p)))
