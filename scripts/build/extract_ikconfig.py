#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_ikconfig.py —— 从 Android boot/recovery 镜像里抠出内核内嵌的 .config

为什么需要它
============
内核若编译时开了 CONFIG_IKCONFIG=y，会在内核镜像里内嵌一段 gzip 压缩的 .config，
以 ASCII 锚点 "IKCFG_ST" / "IKCFG_ED" 包裹。有了它就能**离线**判定某个内核支持哪些能力，
不必刷机、不必等设备可用。

典型用途（本项目真实踩过的坑）
------------------------------
* 判断 initramfs 解压器：`CONFIG_RD_GZIP / RD_LZ4 / RD_XZ / RD_LZO / RD_BZIP2 / RD_LZMA`
  —— 若 ramdisk 用了内核未启用的压缩格式，`decompress_method()` 认不出魔数，
  会被当裸 cpio 解析，**瞬间**报 `rootfs image is not initramfs (no cpio magic)`，
  随后走老式 initrd 降级路径 → `mount_block_root` → `panic: VFS: Unable to mount root fs`。
  ★ 注意 `CONFIG_DECOMPRESS_LZ4=y` ≠ `CONFIG_RD_LZ4=y`：前者只服务"压缩内核镜像"，
    **对 initramfs 无效**。
* 判断 `CONFIG_BLK_DEV_RAM` / `RAM_COUNT` / `RAM_SIZE`（老式 initrd 路径才会用到）
* 判断 `CONFIG_PSTORE*`、`CONFIG_MAGIC_SYSRQ` 等取证相关开关
* 判断 `CONFIG_EROFS_FS` 及 erofs 的压缩/特性支持

用法
====
    extract_ikconfig.py <boot.img 或 recovery.img ...> [--dump <目录>] [--grep KEY]

    --dump <目录>   把每个内核的完整 .config 写到 <目录>/<镜像名>.config
    --grep KEY      只打印匹配 KEY 的行（可多次）

注意
====
* 只处理 `ANDROID!` 魔数的 boot v0~v4 镜像；段偏移按 v1/v2 头（含 recovery_dtbo）计算。
* 内核段必须**未压缩**（裸 arm64 Image）—— 本项目全部镜像都满足。
* 若某内核没找到 IKCFG_ST，说明 CONFIG_IKCONFIG=n，此时无法离线取配置，
  可改在设备上 `zcat /proc/config.gz`（需 CONFIG_IKCONFIG_PROC=y）。
"""

import argparse
import os
import re
import struct
import sys
import zlib

IKCFG_ST = b'IKCFG_ST'
IKCFG_ED = b'IKCFG_ED'


def align(n, a):
    return (n + a - 1) // a * a


def parse_boot(path):
    """解析 boot 镜像头部，返回 (kernel_bytes, header_dict)"""
    with open(path, 'rb') as f:
        head = f.read(4096)
        if head[:8] != b'ANDROID!':
            raise SystemExit("%s: 不是 ANDROID! 镜像" % path)
        g = lambda o: struct.unpack_from('<I', head, o)[0]
        g64 = lambda o: struct.unpack_from('<Q', head, o)[0]
        ps = g(36)
        h = dict(page_size=ps, header_version=g(40),
                 kernel_size=g(8), ramdisk_size=g(16), second_size=g(24),
                 recovery_dtbo_size=g(1632), recovery_dtbo_offset=g64(1636),
                 header_size=g(1644), dtb_size=g(1648), dtb_addr=g64(1652))
        if h['header_version'] >= 3:
            ps = 4096
            h['page_size'] = ps
        off = ps
        h['_koff'] = off
        off += align(h['kernel_size'], ps)
        h['_roff'] = off
        off += align(h['ramdisk_size'], ps)
        h['_soff'] = off
        off += align(h['second_size'], ps)
        h['_recoff'] = off
        off += align(h['recovery_dtbo_size'], ps)
        h['_doff'] = off
        f.seek(h['_koff'])
        kernel = f.read(h['kernel_size'])
    return kernel, h


def extract_ikconfig(kernel):
    """在内核里找 IKCFG_ST 并解出 gzip 配置文本；失败返回 None"""
    i = kernel.find(IKCFG_ST)
    if i < 0:
        return None
    start = i + len(IKCFG_ST)
    for off in range(start, start + 64):
        try:
            d = zlib.decompressobj(16 + zlib.MAX_WBITS)
            out = d.decompress(kernel[off:])
            if not out:
                continue
            return out.decode('utf-8', 'replace')
        except Exception:
            continue
    return None


def summarize(text, keys):
    d = {}
    for line in text.split('\n'):
        line = line.strip()
        if line.startswith('# ') and line.endswith(' is not set'):
            d[line[2:].split(' ')[0]] = 'n'
        elif '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            d[k] = v
    out = []
    for k in keys:
        if k in d:
            out.append((k, d[k]))
    return out, d


WATCH_KEYS = [
    'CONFIG_LOCALVERSION',
    'CONFIG_RD_GZIP', 'CONFIG_RD_BZIP2', 'CONFIG_RD_LZMA',
    'CONFIG_RD_XZ', 'CONFIG_RD_LZO', 'CONFIG_RD_LZ4',
    'CONFIG_BLK_DEV_INITRD', 'CONFIG_BLK_DEV_RAM',
    'CONFIG_BLK_DEV_RAM_COUNT', 'CONFIG_BLK_DEV_RAM_SIZE',
    'CONFIG_DECOMPRESS_GZIP', 'CONFIG_DECOMPRESS_LZ4', 'CONFIG_DECOMPRESS_LZMA',
    'CONFIG_PSTORE', 'CONFIG_PSTORE_RAM', 'CONFIG_PSTORE_CONSOLE', 'CONFIG_PSTORE_PMSG',
    'CONFIG_MAGIC_SYSRQ',
    'CONFIG_EROFS_FS', 'CONFIG_EROFS_FS_ZIP', 'CONFIG_EROFS_FS_ZIP_LZMA',
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('images', nargs='+')
    ap.add_argument('--dump')
    ap.add_argument('--grep', action='append', default=[])
    a = ap.parse_args()

    if a.dump:
        os.makedirs(a.dump, exist_ok=True)

    for path in a.images:
        print("=" * 88)
        print(path)
        try:
            kernel, h = parse_boot(path)
        except SystemExit as e:
            print("  ✗ %s" % e)
            continue
        print("  header_version=%d page_size=%d kernel=%d ramdisk=%d dtb=%d recovery_dtbo=%d"
              % (h['header_version'], h['page_size'], h['kernel_size'],
                 h['ramdisk_size'], h['dtb_size'], h['recovery_dtbo_size']))
        m = re.search(rb'Linux version [^\x00\n]{10,110}', kernel)
        print("  kernel: %s" % (m.group(0).decode('utf-8', 'replace') if m else '?'))

        cfg = extract_ikconfig(kernel)
        if cfg is None:
            print("  ✗ 未找到 IKCFG_ST ⇒ CONFIG_IKCONFIG=n，无法离线取配置")
            continue
        print("  ✓ .config 解出 %d 字节" % len(cfg))

        if a.dump:
            name = os.path.splitext(os.path.basename(path))[0] + '.config'
            with open(os.path.join(a.dump, name), 'w') as f:
                f.write(cfg)
            print("  → 已写出 %s" % os.path.join(a.dump, name))

        if a.grep:
            pat = re.compile('|'.join(re.escape(k) for k in a.grep))
            for line in cfg.split('\n'):
                if pat.search(line):
                    print("    %s" % line.strip())
        else:
            rows, _ = summarize(cfg, WATCH_KEYS)
            for k, v in rows:
                flag = ''
                if k.startswith('CONFIG_RD_'):
                    flag = '  ←✅启用' if v != 'n' else '  ←❌关闭'
                print("    %-34s = %s%s" % (k, v, flag))
            missing = [k for k in WATCH_KEYS if k not in dict(rows)]
            if missing:
                print("    (未出现: %s)" % ', '.join(missing))


if __name__ == '__main__':
    main()
