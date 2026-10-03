#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
br_decompress.py — 用 brotli 流式解压 .new.dat.br，避免一次性载入内存。
用法: br_decompress.py <in.br> <out>
"""
import sys
import os
import time
import brotli

CHUNK = 4 * 1024 * 1024


def main(src, dst):
    total_in = os.path.getsize(src)
    print(f"输入: {src} ({total_in} 字节)")
    print(f"输出: {dst}")
    t0 = time.time()
    with open(src, 'rb') as fi, open(dst, 'wb') as fo:
        d = brotli.Decompressor()
        read = 0
        written = 0
        last = 0
        while True:
            buf = fi.read(CHUNK)
            if not buf:
                break
            read += len(buf)
            out = d.process(buf)
            fo.write(out)
            written += len(out)
            now = time.time()
            if now - last > 3:
                last = now
                pct = read / total_in * 100
                print(f"  读入 {read}/{total_in} ({pct:.1f}%)  写出 {written} 字节  {time.time()-t0:.0f}s")
        # 收尾
        try:
            out = d.finish()
            if out:
                fo.write(out)
                written += len(out)
        except Exception as e:
            print(f"  finish() 提示: {e}")
    print(f"完成: 写出 {written} 字节, 用时 {time.time()-t0:.1f}s")
    print(f"校验大小: {os.path.getsize(dst)}")


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
