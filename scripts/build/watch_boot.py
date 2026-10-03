#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
watch_boot.py —— 观察 A16 启动，判定「框架有没有起来」

为什么要看 USB 的 PID 而不是等 adb
==================================
USB 调试在新装系统上默认是**关**的，所以「adb 出现」这个信号不可靠。
但 `UsbDeviceManager`（住在 system_server 里）一旦启动，就会把
`sys.usb.config` 设成 mtp / mtp,adb —— 表现为 USB 上的 PID 变化：

    PID 0xD00D  产品名 "Android"  → gadget 已枚举但【一个 USB 功能都没配】
                                     = 框架还没起来（或 USB 用途是「仅充电」）
    PID 0x4EE2  → MTP（无 adb）    ← 框架起来了！
    PID 0x4EE1  → MTP + ADB        ← 框架起来了，而且 USB 调试是开的
    PID 0x4EE7  → MTP + ADB + …    ← 同上

失败信号
========
    fastboot 出现  → 掉回 bootloader（apexd bootstrap 失败的特征）
    PID 一直 0xD00D → 仍卡在框架之前

用法
====
  watch_boot.py [--seconds 540] [--reboot] [--out <目录>]
"""

import argparse
import os
import re
import subprocess
import time

PT = os.path.expanduser("~/Documents/oppo/platform-tools")
FB = os.path.join(PT, "fastboot")
ADB = os.path.join(PT, "adb")

# AOSP gadget 的 PID 语义
# ★★ 重要更正（2026-09-29 20:2x 实测）：
#    0x18D1:0xD00D **就是这台手机 fastboot（bootloader）模式的描述符**！
#    实测 fastboot 下的 UsbDeviceSignature = <d1180dd0 32336534 66653663 000000ff 4203>
#                                            └18d1:d00d┘ └"23e4fe6c"(序列号)┘
#    产品名 "Android"，bcdDevice=256，iSerialNumber=3，且挂了一个 `fastboot@0` 接口。
#    ⇒ 看到 0xD00D **不等于**"在启动"，它就是"待在 bootloader 里"。
#    曾因此把"停在 fastboot"误读成"启动到某阶段后稳定" —— 别再犯。
PID_FASTBOOT = "0xD00D"
# 真正代表"框架起来了"的是这些 MTP/ADB 组合 PID
PID_OK = {"0x4EE2", "0x4EE1", "0x4EE7", "0x4EE8", "0x4EE3", "0x4EE4"}

A16_MARKERS = {"0xD00D": "fastboot / bootloader（或未配置功能的 gadget）"}


def sh(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return "<timeout>"
    except Exception as e:
        return "<err %s>" % e


def adb_state():
    out = sh('"%s" devices' % ADB)
    for ln in out.splitlines()[1:]:
        p = ln.split()
        if len(p) >= 2:
            return p[1]
    return ""


def fb_state():
    out = sh('"%s" devices' % FB).strip()
    return out if out else ""


def usb_info():
    """返回 (pid, 接口名, 序列号)。

    注意两点（都踩过）：
      1. ioreg 里 `idProduct` 出现在 `kUSBProductString` **之前**，必须往前找再取最后一个，
         不能用 -A（会取到隔壁设备的）。
      2. `UsbDeviceSignature` 形如 <d1180dd0 3233... >：前 4 字节是 VID/PID（小端），
         紧接着是**序列号的 ASCII**。这是区分 fastboot 与 Android gadget 最可靠的字段。
    """
    out = sh("ioreg -p IOUSB -l -w 0 2>/dev/null")
    if '"kUSBProductString" = "Android"' not in out:
        return (None, "", "")
    lines = out.splitlines()
    pid, iface, serial = None, "", ""
    for i, ln in enumerate(lines):
        if '"kUSBProductString" = "Android"' in ln:
            seg = lines[max(0, i - 24):i + 1]
            for s in reversed(seg):
                m = re.search(r'"idProduct" = (\d+)', s)
                if m:
                    pid = "0x%04X" % int(m.group(1))
                    break
            # 序列号从 UsbDeviceSignature 里解（在设备节点内，位于 product string 之后也可能之前）
            win = "\n".join(lines[max(0, i - 40):i + 40])
            m = re.search(r'"UsbDeviceSignature" = <([0-9a-f]+)>', win)
            if m and len(m.group(1)) >= 24:
                raw = bytes.fromhex(m.group(1))
                try:
                    serial = raw[4:16].decode("ascii", "replace").rstrip("\x00")
                except Exception:
                    serial = ""
            break
    # 看这个设备下有没有绑 fastboot 接口
    for j, ln in enumerate(lines):
        if "IOUSBHostInterface" in ln and "@0" in ln:
            ctx = "\n".join(lines[j:j + 6])
            if "fastboot" in ctx:
                iface = "fastboot"
                break
    return (pid, iface, serial)


def usb_pid():
    return usb_info()[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=540)
    ap.add_argument("--reboot", action="store_true",
                    help="先 fastboot reboot 再观察（默认直接观察）")
    ap.add_argument("--boot-img", default=None,
                    help="改成 fastboot boot <img>：把镜像只加载进内存启动，"
                         "不写入任何分区（安全的实验通道）")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    out = a.out or os.path.expanduser(
        "~/Documents/oppo/2026-安卓16/04-日志/watch_boot_%s"
        % time.strftime("%H%M%S"))
    os.makedirs(out, exist_ok=True)
    logp = os.path.join(out, "timeline.txt")
    lf = open(logp, "w", buffering=1)

    t0 = time.time()

    def log(msg):
        line = "[%s] +%5.0fs  %s" % (time.strftime("%H:%M:%S"),
                                     time.time() - t0, msg)
        print(line)
        lf.write(line + "\n")

    log("=== 开始观察（%d 秒）===" % a.seconds)
    p0, i0, s0 = usb_info()
    log("初始: adb=%r fastboot=%r usb_pid=%s iface=%r serial=%r"
        % (adb_state(), fb_state(), p0, i0, s0))

    if a.boot_img:
        log("fastboot boot %s …" % a.boot_img)
        r = sh('"%s" boot "%s"' % (FB, a.boot_img), timeout=180)
        log("  -> %s" % r.strip().replace("\n", " | "))
        time.sleep(3)
    elif a.reboot:
        log("fastboot reboot …")
        r = sh('"%s" reboot' % FB)
        log("  -> %s" % r.strip().replace("\n", " | "))
        time.sleep(3)

    def snap():
        a_, f_, (p_, i_, s_) = adb_state(), fb_state(), usb_info()
        return (a_, f_, p_, i_, s_)

    prev = snap()
    log("基线: adb=%r fb=%r pid=%s iface=%r serial=%r" % prev)

    verdict = None
    samples = 0
    seen_gone = False          # 设备是否曾经消失（= 真的重启过）
    while time.time() - t0 < a.seconds:
        cur = snap()
        samples += 1
        if cur != prev:
            log("变化: adb %r->%r | fb %r->%r | pid %s->%s | iface %r->%r"
                % (prev[0], cur[0], prev[1], cur[1], prev[2], cur[2],
                   prev[3], cur[3]))
            prev = cur
        if cur[2] is None:
            seen_gone = True
        # ---- 判定 ----
        if cur[0] == "device":
            verdict = ("OK-ADB", "adb 已连接（device）——系统起来了，且 USB 调试开着")
            break
        if cur[2] and cur[2].upper() in PID_OK:
            verdict = ("OK-FW",
                       "USB PID 变成 %s（不再是 0xD00D）—— UsbDeviceManager 已配 USB，"
                       "框架起来了" % cur[2])
            break
        if cur[1] and seen_gone:
            verdict = ("FAIL",
                       "设备消失过又重新出现在 fastboot —— 启动失败掉回 bootloader")
            break
        time.sleep(3)

    log("采样 %d 次" % samples)
    if verdict:
        log(">>> 判定: %s  %s" % verdict)
    else:
        log(">>> 判定: 超时无结论；末态 adb=%r fb=%r pid=%s iface=%r（%s）"
            % (prev[0], prev[1], prev[2], prev[3],
               A16_MARKERS.get(prev[2] or "", "无 USB 节点")))

    # 若 adb 可用，抓日志
    if adb_state() == "device":
        log("抓取系统日志 …")
        for name, cmd in [("getprop", "getprop"),
                          ("boot_completed", "getprop sys.boot_completed"),
                          ("bootanim", "getprop init.svc.bootanim"),
                          ("dmesg", "dmesg"),
                          ("logcat_crash", "logcat -d -b crash -v threadtime"),
                          ("logcat_main", "logcat -d -b main -v threadtime"),
                          ("apex_mount", "cat /proc/mounts | grep apex"),
                          ("netbpfload", "ls -la /system/bin/netbpfload"),
                          ("tethering", "ls /apex/com.android.tethering/")]:
            d = sh('"%s" shell %s' % (ADB, cmd), timeout=60)
            open(os.path.join(out, name + ".txt"), "w").write(d)
            log("  已保存 %s.txt (%d 字节)" % (name, len(d)))

    lf.close()
    print("\n日志: %s" % logp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
