#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_transfer.py — 纯只读分析 AOSP transfer.list v4 的真实语义。
不写任何文件，只打印统计。目的：确定 new 命令三个数字的含义。
"""
import sys
import os
from collections import Counter

def main(path):
    with open(path, 'r') as f:
        raw = [l.rstrip('\n') for l in f]

    # 保留非空行，同时记录原始行号
    lines = [(i, l) for i, l in enumerate(raw) if l.strip() != '']
    print(f"文件: {path}")
    print(f"总行数(含空): {len(raw)}   非空行: {len(lines)}")
    print(f"前 6 行(原始): {[l for _, l in lines[:6]]}")
    print()

    version = int(lines[0][1])
    total_blocks = int(lines[1][1])
    print(f"version      = {version}")
    print(f"total_blocks = {total_blocks}")
    # v3+ 头部多两行
    extra = [l for _, l in lines[2:4]]
    print(f"头部额外 2 行 = {extra}")
    print()

    body = [l for _, l in lines[4:]]
    ops = Counter(l.split(' ')[0] for l in body)
    print(f"命令统计: {dict(ops)}")
    print()

    # 逐条解析 new  a,b,c
    news = []
    others = []
    for l in body:
        parts = l.split(' ', 1)
        op = parts[0]
        rest = parts[1] if len(parts) > 1 else ''
        if op == 'new':
            nums = [int(x) for x in rest.split(',')]
            news.append(nums)
        else:
            others.append(l)

    print(f"new 条目数 = {len(news)}   其他命令 = {others}")
    print(f"每条 new 的数字个数分布: {Counter(len(n) for n in news)}")
    print()

    # 三种可能语义的求和
    sum_first = sum(n[0] for n in news)
    sum_span  = sum(n[2] - n[1] for n in news)
    print(f"[A] sum(count)        = {sum_first}   (期望? )")
    print(f"[B] sum(end-begin)    = {sum_span}   (期望 == {total_blocks}?)")
    print()

    # 各条 span 的分布
    spans = Counter(n[2] - n[1] for n in news)
    print(f"span(end-begin) 分布(前10): {spans.most_common(10)}")
    counts = Counter(n[0] for n in news)
    print(f"第一个数字分布: {counts.most_common(10)}")
    print()

    # 打印全部 src 区间，检查是否连续/覆盖
    begins = sorted(n[1] for n in news)
    ends   = sorted(n[2] for n in news)
    print(f"src_begin 最小/最大 = {begins[0]} / {begins[-1]}")
    print(f"src_end   最小/最大 = {ends[0]} / {ends[-1]}")
    print()

    # 检查区间是否互不重叠且拼成 [0, max)
    ivs = sorted((n[1], n[2]) for n in news)
    overlap = False
    gap = False
    cursor = ivs[0][0]
    total_span_union = 0
    for b, e in ivs:
        if b < cursor:
            overlap = True
        elif b > cursor:
            gap = True
        total_span_union += (e - b)
        cursor = max(cursor, e)
    print(f"区间并集长度 = {total_span_union}")
    print(f"是否重叠 = {overlap}   是否有空隙 = {gap}")
    print(f"并集覆盖范围 = [{ivs[0][0]}, {cursor})  长度 {cursor - ivs[0][0]}")
    print()

    # 打印前 15 条与后 15 条原始 new 行
    print("前 15 条 new:")
    for l in body[:15]:
        print("   ", l)
    print("后 15 条 new:")
    for l in body[-15:]:
        print("   ", l)

if __name__ == '__main__':
    main(sys.argv[1])
