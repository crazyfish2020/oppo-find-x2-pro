#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe_semantics.py — 判定 transfer.list 的两种候选语义，并全盘搜索 EROFS 魔数。
只读，不写文件。

候选语义:
  S1 "源区间": 读 newdat[begin:end] → 顺序写入 out 当前位置
  S2 "目标区间": 顺序读 newdat → 写入 out[begin:end]

判定方法: 输出镜像的 EROFS 魔数应在输出字节偏移 1024 (块0, 偏移1024)。
分别反推该字节来自 newdat 的哪个偏移，去那里查魔数。
另外全盘扫描 newdat 里所有 EROFS 魔数出现位置。
"""
import sys
import struct

EROFS_MAGIC = b'\xe2\xe1\xf5\xe0'
BLK = 4096


def load_news(path):
    with open(path, 'r') as f:
        lines = [l.rstrip('\n') for l in f if l.strip()]
    total = int(lines[1])
    news = []
    for l in lines[4:]:
        op, rest = l.split(' ', 1)
        if op != 'new':
            continue
        nums = [int(x) for x in rest.split(',')]
        news.append(nums)   # [count, begin, end]
    return total, news


def main(newdat, transfer):
    total, news = load_news(transfer)
    print(f"total_blocks = {total}, new 条目 = {len(news)}")

    # --- S1: 源区间 begin:end，目标顺序推进 ---
    # out 当前位置从 0 开始；块0 来自第一条 new 的源区间起点
    s1_first = news[0]          # out[0:span] <- newdat[begin:end]
    s1_off = s1_first[1] * BLK + 1024
    # --- S2: 目标区间 begin:end，源顺序推进 ---
    # 找到覆盖目标块0的那条命令，其源偏移 = 之前所有 span 之和
    src_cursor = 0
    s2_off = None
    for cnt, b, e in news:
        if b <= 0 < e:
            s2_off = src_cursor * BLK + 1024
            break
        src_cursor += (e - b)

    print(f"\n[S1 源区间] 输出字节1024 应来自 newdat 偏移 {s1_off}")
    print(f"[S2 目标区间] 输出字节1024 应来自 newdat 偏移 {s2_off}")

    with open(newdat, 'rb') as f:
        for name, off in (('S1', s1_off), ('S2', s2_off)):
            if off is None:
                print(f"  {name}: 无候选偏移")
                continue
            f.seek(off)
            got = f.read(4)
            print(f"  {name} @ {off}: {got.hex()}  {'✓ 是 EROFS 魔数!' if got == EROFS_MAGIC else '✗ 不是'}")

        # 全盘扫描 EROFS 魔数
        print("\n全盘扫描 EROFS 魔数 (e2e1f5e0):")
        f.seek(0)
        CH = 8 * 1024 * 1024
        found = []
        pos = 0
        prev = b''
        while True:
            chunk = f.read(CH)
            if not chunk:
                break
            buf = prev + chunk
            base = pos - len(prev)
            i = buf.find(EROFS_MAGIC)
            while i != -1:
                abs_off = base + i
                # 只报告形如 n*4096+1024 的位置（分区内 superblock）
                if abs_off % BLK == 1024 % BLK or (abs_off - 1024) % BLK == 0:
                    found.append(abs_off)
                else:
                    found.append(abs_off)
                i = buf.find(EROFS_MAGIC, i + 1)
            prev = buf[-3:]
            pos += len(chunk)
        if not found:
            print("  未找到任何 EROFS 魔数 —— newdat 可能被加密/压缩/异或")
        else:
            for o in found[:20]:
                blk = o // BLK
                rem = o % BLK
                tag = "  (块内偏移1024, 合法 superblock)" if rem == 1024 else ""
                print(f"  偏移 {o}  (块 {blk}, 块内 +{rem}){tag}")
            print(f"  共 {len(found)} 处")

        # 附加：看看有没有 ext4 / f2fs / 稀疏 / zstd 特征
        print("\n其他特征探测:")
        f.seek(0x438)
        ext4 = f.read(2)
        print(f"  0x438 ext4 魔数(53ef): {ext4.hex()}")
        f.seek(1024)
        f2fs = f.read(4)
        print(f"  0x400 f2fs 魔数(1020f5f2): {f2fs.hex()}")
        f.seek(0)
        print(f"  头部16字节: {f.read(16).hex()}")


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
