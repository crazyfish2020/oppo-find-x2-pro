#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tree_compare.py — 逐条目对比两棵解包树：类型 / 权限 / 大小 / SHA256 / 符号链接目标。

用法: tree_compare.py <树A> <树B> [忽略前缀...]
"""
import os
import sys
import stat
import hashlib


def read_all(path):
    """用 os.open 读取（绕过可能的代理层）。"""
    fd = os.open(path, os.O_RDONLY)
    try:
        chunks = []
        while True:
            c = os.read(fd, 1 << 20)
            if not c:
                break
            chunks.append(c)
        return b''.join(chunks)
    finally:
        os.close(fd)


def walk(root):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for n in list(dirnames) + list(filenames):
            p = os.path.join(dirpath, n)
            rel = os.path.relpath(p, root)
            st = os.lstat(p)
            mode = stat.S_IMODE(st.st_mode)
            if stat.S_ISLNK(st.st_mode):
                out[rel] = ('l', mode, 0, os.readlink(p))
            elif stat.S_ISDIR(st.st_mode):
                out[rel] = ('d', mode, 0, None)
            elif stat.S_ISREG(st.st_mode):
                out[rel] = ('f', mode, st.st_size,
                            hashlib.sha256(read_all(p)).hexdigest())
            else:
                out[rel] = ('?', mode, 0, None)
    return out


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    root_a, root_b = sys.argv[1], sys.argv[2]
    ignore = sys.argv[3:]

    def skip(p):
        return any(p == i or p.startswith(i.rstrip('/') + '/') for i in ignore)

    print(f"A = {root_a}")
    print(f"B = {root_b}")
    a = walk(root_a)
    b = walk(root_b)
    print(f"\n条目数: A={len(a)}  B={len(b)}")

    only_a = sorted(x for x in set(a) - set(b) if not skip(x))
    only_b = sorted(x for x in set(b) - set(a) if not skip(x))
    diff = [x for x in sorted(set(a) & set(b)) if not skip(x) and a[x] != b[x]]

    print(f"仅 A 有 : {len(only_a)}")
    print(f"仅 B 有 : {len(only_b)}")
    print(f"内容/属性不同: {len(diff)}")

    for label, lst in (("仅 A 有", only_a), ("仅 B 有", only_b)):
        for p in lst[:15]:
            print(f"   [{label}] {p}")

    for p in diff[:30]:
        print(f"   [不同] {p}")
        print(f"        A: 类型={a[p][0]} 权限={oct(a[p][1])} 大小={a[p][2]} "
              f"hash={(a[p][3] or '')[:16]}")
        print(f"        B: 类型={b[p][0]} 权限={oct(b[p][1])} 大小={b[p][2]} "
              f"hash={(b[p][3] or '')[:16]}")

    if not (only_a or only_b or diff):
        print("\n✓✓ 完全一致（含 SHA256 与符号链接目标）")
        return 0
    print("\n⚠ 存在差异")
    return 1


if __name__ == '__main__':
    sys.exit(main())
