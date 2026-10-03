#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
erofs_compare.py — 逐条目对比两个 EROFS 镜像的元数据(路径/类型/权限/uid/gid/大小/链接目标)。

用法: erofs_compare.py <原镜像> <新镜像> [忽略路径前缀...]
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from erofs_read import Erofs, ErofsError   # noqa: E402


def walk(img):
    e = Erofs(img)
    out = {}

    def rec(ino, rel, depth=0):
        if depth > 60:
            return
        for name, nid, ftype in e.listdir(ino):
            if name in ('.', '..'):
                continue
            ci = e.read_inode(nid)
            mode = ci['i_mode']
            kind = mode & 0o170000
            sub = f"{rel}/{name}" if rel else name
            link = None
            if kind == 0o120000:
                try:
                    link = e.read_data(ci).decode('utf-8', 'replace')
                except ErofsError:
                    link = '<读取失败>'
            out['/' + sub] = (kind, mode & 0o7777, ci['i_uid'], ci['i_gid'],
                              ci['i_size'], link)
            if kind == 0o040000:
                rec(ci, sub, depth + 1)

    root = e.read_inode(e.root_nid)
    out['/'] = (root['i_mode'] & 0o170000, root['i_mode'] & 0o7777,
                root['i_uid'], root['i_gid'], root['i_size'], None)
    rec(root, '')
    e.close()
    return out


def main():
    a = walk(sys.argv[1])
    b = walk(sys.argv[2])
    ignore = sys.argv[3:]

    def skipped(p):
        return any(p.startswith(i) for i in ignore)

    ka, kb = set(a), set(b)
    only_a = sorted(x for x in ka - kb if not skipped(x))
    only_b = sorted(x for x in kb - ka if not skipped(x))

    diff = []
    for p in sorted(ka & kb):
        if skipped(p):
            continue
        if a[p] != b[p]:
            diff.append((p, a[p], b[p]))

    print(f"原镜像条目: {len(a)}    新镜像条目: {len(b)}")
    print(f"仅原镜像有: {len(only_a)}    仅新镜像有: {len(only_b)}    元数据不同: {len(diff)}")
    print()
    if only_a:
        print("--- 仅原镜像有 (前20) ---")
        for p in only_a[:20]:
            print("   ", p)
    if only_b:
        print("--- 仅新镜像有 (前20) ---")
        for p in only_b[:20]:
            print("   ", p)
    if diff:
        print("--- 元数据不同 (前20) ---")
        for p, x, y in diff[:20]:
            print(f"    {p}")
            print(f"       原: type={oct(x[0])} mode={oct(x[1])} uid={x[2]} gid={x[3]} size={x[4]} link={x[5]}")
            print(f"       新: type={oct(y[0])} mode={oct(y[1])} uid={y[2]} gid={y[3]} size={y[4]} link={y[5]}")
    if not (only_a or only_b or diff):
        print("✓ 元数据完全一致")
    return 0 if not (only_a or only_b or diff) else 1


if __name__ == '__main__':
    sys.exit(main())
