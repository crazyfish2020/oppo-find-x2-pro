#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_diag_v3.py —— 生成 A16 诊断版 v3 的补丁文件

与 v1/v2 的根本区别
-------------------
v1/v2 的面包屑是 `exec_start wb_diag_xxx`，服务体是
`/system/bin/sh /system/etc/wb_diag.sh ...`。
而 `/system/bin/sh` 的 PT_INTERP = `/system/bin/linker64`，
后者是软链 → `/apex/com.android.runtime/bin/linker64`。
`apexd-bootstrap` 一旦失败，`/apex` 就是空的，这条链断掉，
**面包屑必然静默失败** —— 所以 v1/v2 的"没留下任何痕迹"是假阴性。

v3 做三件事：
  1) 面包屑全部改用 init【内建命令】 `mkdir` / `write`（不 exec 任何东西），
     并额外在 `on early-init` 顶部 `setenforce 0`，排除 SELinux 干扰。
  2) 把 `apexd-bootstrap` 用 sh 包一层（/system/etc/wb_apexd_wrap.sh），
     把它的 stdout/stderr/退出码/前后 /apex 状态/dmesg 全部落到
     `/metadata/wbdiag/apexd_bootstrap.log` —— 直接拿真实报错。
  3) 注释掉 init.rc 里 4 条 boringssl 的 `reboot_on_failure`，
     让失败之后不要立刻重启跑掉。

（把 sh 变成可用的那一步 —— 实体化 17 个指向 /apex 的软链 —— 由 build 脚本用
  erofs_rebuild.py --materialize 完成，不在这里做。）

输出目录：/tmp/diag3/
"""
import os
import re
import sys

PROJ = os.path.expanduser("~/Documents/oppo/2026-安卓16")
TREE = os.path.join(PROJ, "07-重建/tree")
OUT = os.environ.get("OUT", "/tmp/diag3")

SRC_INIT = os.path.join(TREE, "system/etc/init/hw/init.rc")
SRC_APEXD = os.path.join(TREE, "system/etc/init/apexd.rc")


def log(*a):
    print(" ", *a)


# --------------------------------------------------------------------------
# 面包屑：阶段名 -> 写入的标记文件
# --------------------------------------------------------------------------
STAGES = [
    ("10_earlyinit",            "on early-init 开头"),
    ("18_pre_apexdbootstrap",   "exec_start apexd-bootstrap 之前"),
    ("20_post_apexdbootstrap",  "exec_start apexd-bootstrap 之后"),
    ("30_on_init",              "on init 开头"),
    ("40_on_late_init",         "on late-init 开头"),
    ("42_on_early_fs",          "on early-fs 开头"),
    ("44_on_post_fs",           "on post-fs 开头"),
    ("46_on_late_fs",           "on late-fs 开头"),
    ("48_on_post_fs_data",      "on post-fs-data 开头"),
    ("50_on_boot",              "on boot 开头"),
]


def marker(name):
    """init 内建 write，不依赖任何二进制"""
    return f"    write /metadata/wbdiag/{name} 1"


# --------------------------------------------------------------------------
# 1) init.rc
# --------------------------------------------------------------------------
def patch_init_rc():
    with open(SRC_INIT, "r", encoding="utf-8", errors="surrogateescape") as f:
        lines = f.readlines()

    out = []
    n_marker = 0
    n_comment = 0
    inserted = {}

    # 已经处理过的触发器名，避免重复插入
    def emit(s):
        out.append(s + "\n")

    i = 0
    while i < len(lines):
        ln = lines[i]
        raw = ln.rstrip("\n")

        # ---- ① on early-init：顶部插入 mkdir / 记录 selinux / setenforce 0 / 面包屑 ----
        if raw == "on early-init" and "earlyinit" not in inserted:
            emit(raw)
            emit("    # ===== wb_diag v3: 零依赖面包屑（init 内建，不 exec 任何东西）=====")
            emit("    mkdir /metadata/wbdiag 0777 root root")
            emit("    # 先记下原本的 SELinux 状态，再关掉，排除 SELinux 干扰")
            emit("    copy /sys/fs/selinux/enforce /metadata/wbdiag/00_selinux_enforce")
            emit("    setenforce 0")
            emit(marker("10_earlyinit"))
            n_marker += 1
            inserted["earlyinit"] = i
            i += 1
            continue

        # ---- ② exec_start apexd-bootstrap：前后各插一个面包屑，之后启动探针 ----
        if raw.strip() == "exec_start apexd-bootstrap" and "apexdboot" not in inserted:
            emit(marker("18_pre_apexdbootstrap"))
            emit(raw)
            emit(marker("20_post_apexdbootstrap"))
            emit("    # 采集证据：/apex 状态 / mounts / dmesg / avc")
            emit("    exec_start wb_probe")
            n_marker += 2
            inserted["apexdboot"] = i
            i += 1
            continue

        # ---- ③ 其余阶段：在触发器行之后插面包屑 ----
        m = re.match(r"^on (init|late-init|early-fs|post-fs|late-fs|post-fs-data|boot)$", raw)
        if m:
            stage = m.group(1)
            key = stage
            # late-fs 在文件里出现两次，只给第一个插
            if key not in inserted:
                emit(raw)
                name = {
                    "init": "30_on_init",
                    "late-init": "40_on_late_init",
                    "early-fs": "42_on_early_fs",
                    "post-fs": "44_on_post_fs",
                    "late-fs": "46_on_late_fs",
                    "post-fs-data": "48_on_post_fs_data",
                    "boot": "50_on_boot",
                }[stage]
                emit(marker(name))
                n_marker += 1
                if stage == "boot":
                    emit("    # 第二次采集：这次 dmesg 里已经有全部阶段痕迹")
                    emit("    exec_start wb_probe_late")
                inserted[key] = i
                i += 1
                continue

        # ---- ④ 注释掉 boringssl 的 reboot_on_failure ----
        if re.match(r"^\s*reboot_on_failure\s+reboot,boringssl-self-check-failed\s*$", raw):
            emit("    # wb_diag v3: 注释掉，失败后不要立刻重启跑掉")
            emit("    #" + raw)
            n_comment += 1
            i += 1
            continue

        emit(raw)
        i += 1

    # ---- ⑤ 末尾追加 on property 触发器（探测服务启动结果，同样零依赖）----
    out.append("\n")
    out.append("# ===== wb_diag v3: 用 init 内建属性触发器探测服务结果 =====\n")
    out.append("#   init 自己维护 init.svc.<name>，值 running/stopped，"
               "触发器不需要任何二进制\n")
    for prop, name in [
        ("init.svc.apexd-bootstrap=running", "21a_apexdbootstrap_running"),
        ("init.svc.apexd-bootstrap=stopped", "21b_apexdbootstrap_stopped"),
        ("apexd.status=ready",               "22_apexd_status_ready"),
        ("init.svc.boringssl_self_test64=running", "31a_boringssl64_running"),
        ("init.svc.boringssl_self_test64=stopped", "31b_boringssl64_stopped"),
        ("init.svc.wb_probe=stopped",        "23_wb_probe_stopped"),
    ]:
        out.append(f"on property:{prop}\n")
        out.append(f"    write /metadata/wbdiag/{name} 1\n")

    text = "".join(out)
    return text, n_marker, n_comment, len(inserted)


# --------------------------------------------------------------------------
# 2) apexd.rc —— 把 apexd-bootstrap 用 sh 包一层
# --------------------------------------------------------------------------
def patch_apexd_rc():
    with open(SRC_APEXD, "r", encoding="utf-8", errors="surrogateescape") as f:
        text = f.read()

    # 2.1 apexd-bootstrap 的命令行换成 wrapper
    old = "service apexd-bootstrap /system/bin/apexd --bootstrap"
    new = ("# wb_diag v3: 用 sh 包一层，把 stdout/stderr/退出码落到 /metadata/wbdiag/\n"
           "service apexd-bootstrap /system/bin/sh /system/etc/wb_apexd_wrap.sh")
    if old not in text:
        raise SystemExit(f"  ✗ apexd.rc 里找不到: {old}")
    text = text.replace(old, new, 1)

    # 2.2 注释掉 reboot_on_failure
    n = 0
    out = []
    for ln in text.split("\n"):
        if re.match(r"^\s*reboot_on_failure\s", ln):
            out.append("    # wb_diag v3: 注释掉，失败后停现场")
            out.append("    #" + ln)
            n += 1
        else:
            out.append(ln)
    text = "\n".join(out)

    # 2.3 确保有 stdio_to_kmsg（双通道：文件 + kmsg）
    if "stdio_to_kmsg" not in text:
        text = text.replace(
            "service apexd-bootstrap /system/bin/sh /system/etc/wb_apexd_wrap.sh",
            "service apexd-bootstrap /system/bin/sh /system/etc/wb_apexd_wrap.sh\n"
            "    stdio_to_kmsg", 1)
    return text, n


# --------------------------------------------------------------------------
# 3) wrapper 脚本
# --------------------------------------------------------------------------
WRAP = r'''#!/system/bin/sh
# wb_apexd_wrap.sh —— 包住 apexd-bootstrap，把真实报错留下来
# 由 init 以 `service apexd-bootstrap /system/bin/sh <本脚本>` 启动。
LOG=/metadata/wbdiag/apexd_bootstrap.log
{
  echo "=================================================="
  echo "=== wb_apexd_wrap: apexd --bootstrap 开始 ==="
  echo "--- id ---"
  id
  echo "--- /apex 之前 ---"
  ls -la /apex
  echo "--- /dev/block/loop* ---"
  ls -l /dev/block/loop* /dev/block/loop-control
  echo "--- /dev/block/mapper ---"
  ls -l /dev/block/mapper
  echo "--- /system/apex 数量 ---"
  ls /system/apex | wc -l
  echo "--- bootstrap 三件套 ---"
  ls -l /system/apex/com.android.runtime.apex \
        /system/apex/com.android.i18n.apex \
        /system/apex/com.android.tzdata.apex
  echo "--- /metadata 可写性 ---"
  touch /metadata/wbdiag/_wtest && echo "metadata 可写 OK" && rm -f /metadata/wbdiag/_wtest
} >>"$LOG" 2>&1

/system/bin/apexd --bootstrap >>"$LOG" 2>&1
RC=$?

{
  echo "--- apexd --bootstrap 退出码 = $RC ---"
  echo "--- /apex 之后 ---"
  ls -la /apex
  echo "--- apex-info-list ---"
  ls -l /apex/apex-info-list.xml /metadata/apex 2>&1
  echo "--- dmesg 尾部 250 行（apexd 的 stdio_to_kmsg 也会在这里）---"
  dmesg | tail -250
  echo "--- dmesg 里的 AVC 拒绝 ---"
  dmesg | grep -i 'avc:' | tail -80
  echo "=== wb_apexd_wrap 结束 ==="
} >>"$LOG" 2>&1

exit $RC
'''

PROBE = r'''#!/system/bin/sh
# wb_probe.sh —— 分阶段采集现场，落到 /metadata/wbdiag/
# 参数: early | late
STAGE="${1:-early}"
OUT="/metadata/wbdiag/probe_${STAGE}.log"
{
  echo "=================================================="
  echo "=== wb_probe stage=$STAGE ==="
  echo "--- SELinux ---"
  cat /sys/fs/selinux/enforce
  echo "--- 挂载表 ---"
  cat /proc/mounts
  echo "--- /apex ---"
  ls -la /apex
  echo "--- /apex/com.android.runtime/bin ---"
  ls -la /apex/com.android.runtime/bin
  echo "--- /system/bin/linker64 现在是什么 ---"
  ls -l /system/bin/linker64
  echo "--- /metadata/apex ---"
  ls -la /metadata/apex
  echo "--- /proc/bootprof ---"
  cat /proc/bootprof
  echo "--- dmesg 尾部 200 行 ---"
  dmesg | tail -200
} >>"$OUT" 2>&1
exit 0
'''

PROBE_RC = '''# wb_diag v3: 证据采集服务
#   脚本沿用 v1 的路径 /system/etc/wb_diag.sh，但内容已是 v3 版
#   （v1/v2 的"exec_start + sh 脚本"机制在 /apex 为空时必然失效，已整条换掉）
service wb_probe /system/bin/sh /system/etc/wb_diag.sh early
    class core
    oneshot
    user root
    group root
    disabled
    stdio_to_kmsg

service wb_probe_late /system/bin/sh /system/etc/wb_diag.sh late
    class core
    oneshot
    user root
    group root
    disabled
    stdio_to_kmsg
'''


def main():
    os.makedirs(OUT, exist_ok=True)
    print("wb_diag v3 补丁生成")
    print(f"  输入: {SRC_INIT}")
    print(f"  输出: {OUT}")

    # init.rc
    init_text, n_marker, n_comment, n_ins = patch_init_rc()
    p = os.path.join(OUT, "init.rc")
    with open(p, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(init_text)
    log(f"init.rc            {len(init_text)} 字节  面包屑 {n_marker} 条  "
        f"注释 reboot_on_failure {n_comment} 条  触发器锚点 {n_ins} 个")

    # apexd.rc
    apexd_text, n = patch_apexd_rc()
    p = os.path.join(OUT, "apexd.rc")
    with open(p, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(apexd_text)
    log(f"apexd.rc           {len(apexd_text)} 字节  注释 reboot_on_failure {n} 条")

    # 两个脚本
    for name, body in [("wb_apexd_wrap.sh", WRAP), ("wb_diag.sh", PROBE)]:
        p = os.path.join(OUT, name)
        with open(p, "w", encoding="utf-8", errors="surrogateescape") as f:
            f.write(body)
        os.chmod(p, 0o755)
        log(f"{name:18s} {len(body)} 字节  0755")

    # 探针 rc
    p = os.path.join(OUT, "wb_diag.rc")
    with open(p, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(PROBE_RC)
    log(f"wb_diag.rc         {len(PROBE_RC)} 字节")

    # ---------------- 断言 ----------------
    print()
    print("  断言:")
    bad = []
    if n_marker != 10:
        bad.append(f"面包屑应 10 条，实际 {n_marker}")
    if n_comment != 4:
        bad.append(f"应注释 4 条 boringssl reboot_on_failure，实际 {n_comment}")
    if n_ins < 9:
        bad.append(f"触发器锚点应 >=9，实际 {n_ins}")
    for tok in ["mkdir /metadata/wbdiag", "setenforce 0",
                "exec_start apexd-bootstrap", "exec_start wb_probe",
                "exec_start wb_probe_late",
                "on property:init.svc.apexd-bootstrap=stopped"]:
        if tok not in init_text:
            bad.append(f"init.rc 里缺 {tok!r}")
    # 不该再有 exec_start wb_diag_*
    if "exec_start wb_diag_" in init_text:
        bad.append("init.rc 里还残留旧的 exec_start wb_diag_*（那是 v1/v2 的假阴性机制）")
    # 面包屑不许再依赖任何二进制
    for ln in init_text.split("\n"):
        if "wbdiag" in ln and ("exec" in ln or "/system/bin" in ln):
            bad.append(f"面包屑仍依赖二进制: {ln.strip()}")
    # apexd.rc 必须已经包上 wrapper
    if "wb_apexd_wrap.sh" not in apexd_text:
        bad.append("apexd.rc 没有包上 wb_apexd_wrap.sh")
    if re.search(r"^\s*reboot_on_failure", apexd_text, re.M):
        bad.append("apexd.rc 里还有未注释的 reboot_on_failure")
    if bad:
        for b in bad:
            print(f"    ✗ {b}")
        sys.exit(1)
    for b in ["面包屑 10 条且全部为 init 内建",
              "4 条 boringssl reboot_on_failure 已注释",
              "apexd-bootstrap 已包 sh 抓报错",
              "无残留 exec_start wb_diag_*"]:
        print(f"    ✓ {b}")
    print()
    print(f"  完成 -> {OUT}")


if __name__ == "__main__":
    main()
