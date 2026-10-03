#!/usr/bin/env python3
"""按用户批准的清单执行永久删除（直接释放空间）。

批准范围（2026-09-28）：
  A. donor_8t/part_*.bin + part_*.bin.tmp   —— A16 下载残留，已 SHA256 证明是 ota_full_8t.zip 的切块
  B. 2026old/opppo16-2026/                  —— ColorOS 15 分区补丁，已归档
  C. .workbuddy-ai/backup/刷机日志-ColorOS15.0.2Pro-*.log

明确排除（不删）：
  platform-tools/FindX2Pro_ColorOS_15.0.2_...zip   ← 唯一完整回滚包
  FindX2Pro_ColorOS_15.0.2_.../                    ← 含 boot.img / firmware-update
  platform-tools/TWRP-13-Compass-*.img             ← 刷机工具

规则：分批 ≤10 个，每批后核对空间，任一失败立即停止。
"""
import os
import shutil
import subprocess
import glob
import sys

BASE = os.path.expanduser('~/Documents/oppo')
BATCH = 10


def free_gib():
    s = subprocess.run(['df', '-k', '/System/Volumes/Data'],
                       capture_output=True, text=True).stdout
    return int(s.splitlines()[1].split()[3]) / 1024 / 1024


def build_targets():
    files, dirs = [], []
    # A. 剩余分片（精确枚举，不用松散通配符）
    for p in sorted(glob.glob(os.path.join(BASE, 'donor_8t', 'part_*.bin*'))):
        files.append(p)
    # B. 归档区里的 ColorOS 15 补丁目录
    d = os.path.join(BASE, '2026old', 'opppo16-2026')
    if os.path.isdir(d):
        dirs.append(d)
    # C. 刷机日志
    for p in glob.glob(os.path.join(BASE, '.workbuddy-ai', 'backup',
                                    '刷机日志-ColorOS15.0.2Pro-*.log')):
        files.append(p)
    return files, dirs


def main():
    files, dirs = build_targets()
    print('=== 待删清单 ===')
    tot = 0
    for p in files:
        sz = os.path.getsize(p)
        tot += sz
        print(f'  [F] {sz:>13,}  {os.path.relpath(p, BASE)}')
    for d in dirs:
        sz = sum(os.path.getsize(os.path.join(r, f))
                 for r, _, fs in os.walk(d) for f in fs)
        tot += sz
        print(f'  [D] {sz:>13,}  {os.path.relpath(d, BASE)}/  (整目录)')
    print(f'\n  文件 {len(files)} 个 + 目录 {len(dirs)} 个，合计 {tot:,} 字节 ({tot/2**30:.2f} GiB)')
    print(f'  删除前可用空间: {free_gib():.2f} GiB\n')

    if not files and not dirs:
        print('无待删项，退出。')
        return

    # 分批删文件
    ok = 0
    failed = []
    for b in range(0, len(files), BATCH):
        chunk = files[b:b + BATCH]
        n = b // BATCH + 1
        before = free_gib()
        for p in chunk:
            try:
                os.remove(p)
                ok += 1
            except Exception as e:
                failed.append((p, repr(e)))
        after = free_gib()
        print(f'  批 {n}: {len(chunk) - len([f for f in failed if f[0] in chunk])}/{len(chunk)} 成功'
              f'   可用 {before:.2f} → {after:.2f} GiB')
        if failed:
            print('  ✗ 失败，立即停止:')
            for p, e in failed:
                print(f'      {os.path.relpath(p, BASE)}: {e}')
            sys.exit(1)

    # 删目录
    for d in dirs:
        before = free_gib()
        try:
            shutil.rmtree(d)
            ok += 1
            print(f'  目录 {os.path.relpath(d, BASE)}/ 已删   可用 {before:.2f} → {free_gib():.2f} GiB')
        except Exception as e:
            print(f'  ✗ 目录删除失败 {d}: {e!r}')
            sys.exit(1)

    print(f'\n=== 完成：删除 {ok} 项 ===')
    print(f'  当前可用空间: {free_gib():.2f} GiB')


if __name__ == '__main__':
    main()
