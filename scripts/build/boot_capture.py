#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
boot_capture.py — 受控启动 + 全程日志抓取

流程:
  1. adb reboot（从 TWRP 触发正常启动）
  2. 每秒轮询 adb / fastboot 状态，记录时间线
  3. 一旦 adb 以 device 状态出现，立刻抓 dmesg / logcat / getprop / init 状态
  4. 一旦 fastboot 出现（掉回 bootloader），记录耗时并停止
输出: 日志目录下的 timeline.txt + 各 dump 文件
"""
import subprocess
import sys
import os
import time

ARGS = sys.argv[1:]
NO_REBOOT = "--no-reboot" in ARGS
POS = [a for a in ARGS if not a.startswith("--")]
OUT = POS[0] if POS else os.path.expanduser(
    "~/Documents/oppo/2026-安卓16/04-日志/boot_attempt")
os.makedirs(OUT, exist_ok=True)

TIMELINE = os.path.join(OUT, "timeline.txt")
tf = open(TIMELINE, "w", buffering=1)


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] +{time.time()-T0:6.1f}s  {msg}"
    print(line)
    tf.write(line + "\n")


def sh(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True,
                           text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return "<timeout>"
    except Exception as e:
        return f"<err {e}>"


def adb_state():
    out = sh("adb devices")
    for ln in out.splitlines()[1:]:
        parts = ln.split()
        if len(parts) >= 2:
            return parts[1]
    return ""


def fb_state():
    out = sh("fastboot devices")
    out = out.strip()
    return out if out else ""


T0 = time.time()
log("=== 开始：%s ===" % ("只观测（不触发重启）" if NO_REBOOT else "触发正常启动"))

prev_adb, prev_fb = adb_state(), fb_state()
log(f"初始 adb={prev_adb!r} fastboot={prev_fb!r}")

if NO_REBOOT:
    log("--no-reboot：跳过 adb reboot，直接开始轮询")
else:
    log("执行 adb reboot ...")
    sh("adb reboot", timeout=20)
time.sleep(2)

dumped = False
captured = False
for i in range(180):
    a, f = adb_state(), fb_state()
    if a != prev_adb:
        log(f"adb 状态变化: {prev_adb!r} -> {a!r}")
        prev_adb = a
    if f != prev_fb:
        log(f"fastboot 状态变化: {prev_fb!r} -> {f!r}")
        prev_fb = f
        if f:
            log(">>> 设备掉回 bootloader，启动失败")
            break
    if a == "device" and not captured:
        log(">>> adb 已连接（device），立即抓取日志")
        captured = True
        for name, cmd in [
            ("getprop", "adb shell getprop"),
            ("dmesg", "adb shell dmesg"),
            ("logcat_all", "adb shell logcat -d -b all -v threadtime"),
            ("logcat_crash", "adb shell logcat -d -b crash -v threadtime"),
            ("ps", "adb shell ps -A"),
            ("mount", "adb shell mount"),
            ("init_svc", "adb shell 'getprop | grep init.svc'"),
            ("boot_completed", "adb shell getprop sys.boot_completed"),
            ("bootanim", "adb shell getprop init.svc.bootanim"),
        ]:
            data = sh(cmd, timeout=40)
            p = os.path.join(OUT, f"{name}.txt")
            open(p, "w").write(data)
            log(f"  已保存 {name}.txt ({len(data)} 字节)")
        bc = sh("adb shell getprop sys.boot_completed").strip()
        if bc == "1":
            log(">>> ★ sys.boot_completed=1 —— 启动成功！")
            break
    time.sleep(1)

log(f"=== 结束（总耗时 {time.time()-T0:.1f}s）adb={prev_adb!r} fastboot={prev_fb!r} ===")
tf.close()
print("\n日志目录:", OUT)
