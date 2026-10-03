#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
boot_watch.py — 观测一次启动，比 boot_capture.py 多记 USB gadget 状态

为什么需要它
============
只盯 adb / fastboot 不够：设备可能既不出现 adb 也不出现 fastboot，
而是"USB 上挂着但一个功能都没配"。那种情况下要能看出：

  * USB 上还有没有这台设备（掉了 = 断电/重启中）
  * idProduct 是多少（0xD00D = init 的 gadget 默认态，一个功能都没启用；
    变成 0x4EE1/0x4EE2/0x4EE7 之类 = 框架已把 USB 配成 MTP/ADB）
  * 节点 id 有没有变（变了 = 重新枚举 = 重启循环）

用法
====
  boot_watch.py <输出目录> [--minutes N] [--reboot]

  --reboot   先执行 `adb reboot`（设备在 adb 上时用；在 fastboot 上请自己
             先 `fastboot reboot`，然后不带 --reboot 跑本脚本）
"""

import os
import re
import subprocess
import sys
import time

ARGS = sys.argv[1:]
POS = [a for a in ARGS if not a.startswith("--")]
OUT = POS[0] if POS else os.path.expanduser(
    "~/Documents/oppo/2026-安卓16/04-日志/boot_watch")
MINUTES = 10.0
for i, a in enumerate(ARGS):
    if a == "--minutes" and i + 1 < len(ARGS):
        MINUTES = float(ARGS[i + 1])
DO_REBOOT = "--reboot" in ARGS

os.makedirs(OUT, exist_ok=True)
LOG = os.path.join(OUT, "watch.txt")
tf = open(LOG, "w", buffering=1)
T0 = time.time()


def log(msg):
    line = "[%s] +%6.1fs  %s" % (time.strftime("%H:%M:%S"), time.time() - T0, msg)
    print(line)
    tf.write(line + "\n")


def sh(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True,
                           text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return "<timeout>"
    except Exception as e:
        return "<err %s>" % e


def adb_state():
    for ln in sh("adb devices").splitlines()[1:]:
        p = ln.split()
        if len(p) >= 2:
            return p[1]
    return ""


def fb_state():
    out = sh("fastboot devices").strip()
    return out if out else ""


ANDROID_RE = re.compile(r'"kUSBProductString" = "Android"')
PID_RE = re.compile(r'"idProduct" = (\d+)')
NODEID_RE = re.compile(r'id (0x[0-9a-f]+)')

PID_NAMES = {
    0xD00D: "0xD00D(gadget 默认态/未配任何功能)",
    0x4EE1: "0x4EE1(MTP+ADB)",
    0x4EE2: "0x4EE2(MTP)",
    0x4EE7: "0x4EE7(ADB only)",
    0x4EE0: "0x4EE0(fastboot)",
}


def usb_info():
    """返回 (pid, node_id)；设备不在 USB 上则 (None, None)"""
    out = sh("ioreg -p IOUSB -l -w 0", timeout=40)
    lines = out.splitlines()
    for i, ln in enumerate(lines):
        if ANDROID_RE.search(ln):
            pid = None
            for j in range(i - 1, max(-1, i - 60), -1):
                m = PID_RE.search(lines[j])
                if m:
                    pid = int(m.group(1))
                    break
            node = None
            for j in range(i - 1, max(-1, i - 60), -1):
                m = NODEID_RE.search(lines[j])
                if m:
                    node = m.group(1)
                    break
            return pid, node
    return None, None


def dump_logs(tag):
    for name, cmd in [
        ("getprop", "adb shell getprop"),
        ("dmesg", "adb shell dmesg"),
        ("logcat_all", "adb shell logcat -d -b all -v threadtime"),
        ("logcat_crash", "adb shell logcat -d -b crash -v threadtime"),
        ("mount", "adb shell mount"),
        ("apex_ls", "adb shell 'ls -la /apex/'"),
        ("init_svc", "adb shell 'getprop | grep init.svc'"),
        ("boot_completed", "adb shell getprop sys.boot_completed"),
        ("bootanim", "adb shell getprop init.svc.bootanim"),
        ("proc_apex", "adb shell 'cat /proc/mounts | grep apex'"),
    ]:
        data = sh(cmd, timeout=60)
        p = os.path.join(OUT, "%s_%s.txt" % (tag, name))
        open(p, "w").write(data)
        log("    已保存 %s_%s.txt (%d 字节)" % (tag, name, len(data)))


log("=== 开始观测（最多 %.0f 分钟）===" % MINUTES)
if DO_REBOOT:
    log("执行 adb reboot ...")
    sh("adb reboot", timeout=20)
    time.sleep(2)

prev = (adb_state(), fb_state(), None, None)
log("初始 adb=%r fastboot=%r pid=%s node=%s"
    % (prev[0], prev[1], PID_NAMES.get(prev[2], prev[2]), prev[3]))
captured = set()

deadline = time.time() + MINUTES * 60
while time.time() < deadline:
    a, f = adb_state(), fb_state()
    pid, node = usb_info()
    cur = (a, f, pid, node)
    if cur != prev:
        what = []
        if a != prev[0]:
            what.append("adb %r->%r" % (prev[0], a))
        if f != prev[1]:
            what.append("fastboot %r->%r" % (prev[1], f))
        if pid != prev[2]:
            what.append("USB pid %s -> %s"
                        % (PID_NAMES.get(prev[2], prev[2]),
                           PID_NAMES.get(pid, pid)))
        if node != prev[3]:
            what.append("USB 节点 id %s -> %s（变了=重新枚举/重启）"
                        % (prev[3], node))
        log("变化: " + "；".join(what))
        prev = cur
        if pid is not None and pid != 0xD00D:
            log(">>> USB 已配出功能（框架起来了！）")
        if f:
            log(">>> 设备在 fastboot —— 启动失败落回 bootloader")
    if a == "device":
        bc = sh("adb shell getprop sys.boot_completed").strip()
        if "device" not in captured:
            log(">>> adb 已连接（device），抓第一份日志")
            captured.add("device")
            dump_logs("t%ds" % int(time.time() - T0))
        if bc == "1":
            log(">>> ★ sys.boot_completed=1 —— 启动成功！")
            dump_logs("booted")
            break
    if a == "sideload":
        log(">>> adb 处于 sideload")
        break
    time.sleep(2)

log("=== 结束 adb=%r fastboot=%r pid=%s ===" % (prev[0], prev[1],
                                               PID_NAMES.get(prev[2], prev[2])))
tf.close()
print("\n日志: " + LOG)
