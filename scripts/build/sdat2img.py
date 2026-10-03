#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sdat2img.py — 把 Android OTA 的 transfer.list + new.dat 还原为原始分区镜像。

格式说明(实测确认，v4):
  头部 4 行: version / total_blocks / 0 / 0
  命令:      new <count>,<tgt_begin>,<tgt_end>
    - 逗号分隔
    - <count> 恒为 2，是工具写死的标记位，无实际意义(不要当块数用!)
    - 真实数据长度 = tgt_end - tgt_begin
    - **区间是「目标区间」**: new.dat 被【顺序】读取，
      每次读 (end-begin) 块，写到输出镜像的 [begin,end) 块位置。

判定依据: vendor 镜像 EROFS 魔数出现在 newdat 偏移 99872*4096+1024，
恰好等于覆盖目标块0的命令(`new 2,0,1`)在源流中的游标位置。

用法:
  sdat2img.py <transfer.list> <new.dat> <out.img>
"""
import sys
import os
import hashlib

BLK = 4096


def parse_transfer(path):
    with open(path, 'r') as f:
        lines = [l.rstrip('\n') for l in f if l.strip() != '']
    version = int(lines[0])
    total_blocks = int(lines[1])
    # v3+ 头部多两行
    hdr = 2 if version < 3 else 4
    cmds = []
    for l in lines[hdr:]:
        parts = l.split(' ', 1)
        op = parts[0]
        if op == 'new':
            nums = [int(x) for x in parts[1].split(',')]
            assert len(nums) == 3, f"意外的 new 参数: {l}"
            cmds.append(('new', nums[1], nums[2]))     # (op, tgt_begin, tgt_end)
        elif op in ('erase',):
            nums = [int(x) for x in parts[1].split(',')]
            cmds.append(('erase', nums[0], nums[1]))
        else:
            raise ValueError(f"未支持的命令: {l}")
    return version, total_blocks, cmds


def convert(transfer_path, newdat_path, out_path):
    version, total_blocks, cmds = parse_transfer(transfer_path)
    out_size = total_blocks * BLK
    newdat_size = os.path.getsize(newdat_path)

    print(f"transfer 版本   : {version}")
    print(f"总块数          : {total_blocks}  ({out_size} 字节 = {out_size/1048576:.1f} MiB)")
    print(f"命令数          : {len(cmds)}")
    print(f"new.dat 大小    : {newdat_size} 字节")

    total_new = sum(e - b for op, b, e in cmds if op == 'new')
    print(f"new 数据总块数  : {total_new}  ({total_new*BLK} 字节)")
    if total_new * BLK != newdat_size:
        print(f"  ⚠ 警告: new 数据块数×4096 ({total_new*BLK}) != new.dat 大小 ({newdat_size})，差 {newdat_size - total_new*BLK}")

    # 校验目标区间铺满 [0,total) 且不重叠
    ivs = sorted((b, e) for op, b, e in cmds if op == 'new')
    cursor = 0
    ok = True
    for b, e in ivs:
        if b != cursor:
            ok = False
            print(f"  ⚠ 目标区间不连续: 期望从 {cursor} 开始，实际 {b}")
            break
        cursor = e
    if ok and cursor != total_blocks:
        ok = False
        print(f"  ⚠ 目标区间未铺满: 结束于 {cursor}，期望 {total_blocks}")
    print(f"目标区间完整性  : {'✓ 无缝铺满 [0,total)' if ok else '✗ 有洞/重叠'}")

    # 开始写
    h = hashlib.sha256()
    with open(newdat_path, 'rb') as src, open(out_path, 'wb') as dst:
        dst.truncate(out_size)          # 预分配(稀疏)
        src_cursor = 0
        done = 0
        for i, (op, b, e) in enumerate(cmds):
            n = e - b
            if op != 'new':
                continue
            data = src.read(n * BLK)
            if len(data) != n * BLK:
                raise IOError(f"new.dat 读取不足: 需要 {n*BLK} 字节，实际 {len(data)} (第 {i} 条)")
            dst.seek(b * BLK)
            dst.write(data)
            h.update(data)
            src_cursor += n
            done += n
            if (i + 1) % 50 == 0 or i == len(cmds) - 1:
                pct = done / total_new * 100 if total_new else 100
                print(f"  进度 {i+1}/{len(cmds)}  已写 {done}/{total_new} 块 ({pct:.1f}%)")

    print(f"\n完成: {out_path}")
    print(f"输出大小: {os.path.getsize(out_path)} 字节")
    print(f"new 数据流 SHA256: {h.hexdigest()}")

    # 校验输出镜像头部
    with open(out_path, 'rb') as f:
        f.seek(1024)
        magic = f.read(4)
    EROFS = bytes.fromhex('e2e1f5e0')
    print(f"偏移1024 魔数: {magic.hex()}  {'✓ EROFS' if magic == EROFS else '✗ 非 EROFS'}")


if __name__ == '__main__':
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    convert(sys.argv[1], sys.argv[2], sys.argv[3])
