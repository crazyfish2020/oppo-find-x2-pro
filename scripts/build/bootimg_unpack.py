#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bootimg_unpack.py — 解包 Android boot 镜像，列出 ramdisk 内容并抽取指定文件。

用法:
  bootimg_unpack.py <boot.img> list                 # 列出 ramdisk 条目
  bootimg_unpack.py <boot.img> cat <内部路径>       # 打印某个文件内容
  bootimg_unpack.py <boot.img> extract <输出目录>   # 全部解出
"""
import sys
import os
import struct
import gzip
import io
import zlib

MAGIC = b'ANDROID!'


def parse_header(data):
    if data[:8] != MAGIC:
        raise SystemExit(f"不是 Android boot 镜像 (magic={data[:8]!r})")
    (kernel_size, kernel_addr, ramdisk_size, ramdisk_addr,
     second_size, second_addr, tags_addr, page_size,
     header_version, os_version) = struct.unpack_from('<10I', data, 8)
    name = data[48:64].split(b'\x00', 1)[0].decode('utf-8', 'replace')
    cmdline = data[64:576].split(b'\x00', 1)[0].decode('utf-8', 'replace')
    extra = data[608:1632].split(b'\x00', 1)[0].decode('utf-8', 'replace')
    return dict(kernel_size=kernel_size, ramdisk_size=ramdisk_size,
                second_size=second_size, page_size=page_size,
                header_version=header_version, os_version=os_version,
                name=name, cmdline=cmdline, extra_cmdline=extra,
                kernel_addr=kernel_addr, ramdisk_addr=ramdisk_addr)


def page_align(n, page):
    return (n + page - 1) // page * page


def decompress(buf):
    """尝试 gzip / lzma / lz4 / zstd / 原始 cpio。"""
    if buf[:2] == b'\x1f\x8b':
        return gzip.decompress(buf), 'gzip'
    if buf[:4] == b'\xfd7zX':      # xz
        import lzma
        return lzma.decompress(buf), 'xz'
    if buf[:5] == b'\x04\x22\x4d\x18':   # lz4 frame
        try:
            import lz4.frame
            return lz4.frame.decompress(buf), 'lz4-frame'
        except ImportError:
            raise SystemExit("需要 lz4 模块: pip install lz4")
    if buf[:4] == b'\x28\xb5\x2f\xfd':   # zstd
        try:
            import zstandard
            return zstandard.ZstdDecompressor().decompress(buf), 'zstd'
        except ImportError:
            raise SystemExit("需要 zstandard 模块")
    if buf[:6] == b'070701' or buf[:6] == b'070702':
        return buf, 'cpio-raw'
    # 退化：尝试 gzip 忽略头
    try:
        return gzip.decompress(buf), 'gzip?'
    except Exception:
        pass
    return buf, 'unknown'


def parse_cpio(buf):
    """解析 newc cpio，返回 [(name, mode, size, data_offset, data)]。"""
    out = []
    off = 0
    n = len(buf)
    while off + 110 <= n:
        magic = buf[off:off + 6]
        if magic not in (b'070701', b'070702'):
            break
        f = lambda i: int(buf[off + i:off + i + 8], 16)
        mode = f(14)
        size = f(54)
        namesize = f(94)
        name = buf[off + 110:off + 110 + namesize - 1].decode('utf-8', 'replace')
        data_start = off + 110 + namesize
        data_start = (data_start + 3) // 4 * 4
        data = buf[data_start:data_start + size]
        out.append((name, mode, size, data_start, data))
        off = data_start + size
        off = (off + 3) // 4 * 4
        if name == 'TRAILER!!!':
            break
    return out


def load(path):
    with open(path, 'rb') as fp:
        data = fp.read()
    h = parse_header(data)
    page = h['page_size'] or 4096
    off = page
    off += page_align(h['kernel_size'], page)
    ram = data[off:off + h['ramdisk_size']]
    raw, kind = decompress(ram)
    return h, raw, kind


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    img, cmd = sys.argv[1], sys.argv[2]
    h, raw, kind = load(img)

    print("=== boot 头 ===")
    for k in ('name', 'header_version', 'page_size', 'kernel_size',
              'ramdisk_size', 'second_size', 'os_version'):
        print(f"  {k:16} = {h[k]}")
    print(f"  cmdline          = {h['cmdline']}")
    if h['extra_cmdline']:
        print(f"  extra_cmdline    = {h['extra_cmdline']}")
    print(f"  ramdisk 解压: {kind} -> {len(raw)} 字节")

    entries = parse_cpio(raw)
    print(f"\n=== ramdisk 条目: {len(entries)} ===")

    if cmd == 'list':
        for name, mode, size, _, _ in entries:
            if name == 'TRAILER!!!':
                continue
            print(f"  {oct(mode & 0o7777):>8} {size:>10}  {name}")
    elif cmd == 'cat':
        target = sys.argv[3].lstrip('/')
        for name, mode, size, _, data in entries:
            if name.lstrip('./') == target or name == sys.argv[3]:
                sys.stdout.write(data.decode('utf-8', 'replace'))
                return 0
        print(f"未找到 {target}", file=sys.stderr)
        return 1
    elif cmd == 'extract':
        import stat as _stat

        def _mkdir(d):
            # 沙箱 broker 会把「目录已存在」抛成 PermissionError，
            # 所以先判断再建，不能只依赖 exist_ok。
            if d and not os.path.isdir(d):
                try:
                    os.makedirs(d, exist_ok=True)
                except (FileExistsError, PermissionError):
                    pass

        outdir = sys.argv[3]
        _mkdir(outdir)
        n_dir = n_file = n_link = 0
        for name, mode, size, _, data in entries:
            if name == 'TRAILER!!!':
                continue
            rel = name.lstrip('./').rstrip('/')
            if not rel:
                continue
            p = os.path.join(outdir, rel)
            if _stat.S_ISDIR(mode) or name.endswith('/'):
                _mkdir(p)
                n_dir += 1
            elif _stat.S_ISLNK(mode):
                _mkdir(os.path.dirname(p))
                tgt = data.decode('utf-8', 'replace')
                try:
                    if os.path.lexists(p):
                        os.remove(p)
                    os.symlink(tgt, p)
                except OSError:
                    pass
                n_link += 1
            else:
                _mkdir(os.path.dirname(p))
                with open(p, 'wb') as f:
                    f.write(data)
                os.chmod(p, mode & 0o7777)
                n_file += 1
        print(f"已解出到 {outdir}  (目录 {n_dir} / 文件 {n_file} / 符号链接 {n_link})")
    return 0


if __name__ == '__main__':
    sys.exit(main())
