#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_hybrid_kernel.py — 构建「换内核」的混合 boot.img

思路
====
Android boot.img (header v2) 由 4 段组成，彼此独立：

    [header(4096)] [kernel] [ramdisk] [dtb] [AVB 元数据+footer]

其中 **dtb 是独立一段**，由 bootloader(ABL) 读出来交给内核。
所以"换内核"不需要动 dtb/ramdisk —— 只要把 kernel 段换成别的，其余照抄。

用途
====
验证「X2 Pro 的 4.19.157 内核缺 erofs_load_compr_cfgs」这个根因假设：
把 8T 的 4.19.325-KSU_NEXT 内核（**有**该符号）塞进 X2 Pro 的 boot，
dtb 与 ramdisk 保持 X2 Pro 原样。若这样能起来 ⇒ 根因确证。

用法
====
  build_hybrid_kernel.py <kernel来源boot> <骨架boot> <输出> [选项]

  --ramdisk-from <boot.img|base|kernel-src|file:PATH>   默认 base
  --dtb-from     <boot.img|base|kernel-src|file:PATH>   默认 base

  base / kernel-src 分别指「骨架 boot」和「内核来源 boot」。

注意
====
* 只适用于 header_version = 2 且内核**未压缩**（裸 arm64 Image）的 boot.img。
* 产物保持与骨架 boot **完全相同的文件大小**，并沿用其 AVB footer/vbmeta 结构
  （本机 boot 校验已关闭，只保持结构一致，不做重新签名）。
"""
import hashlib
import os
import struct
import sys


def align(n, a):
    return (n + a - 1) // a * a


def parse_hdr(d):
    if d[:8] != b'ANDROID!':
        raise SystemExit("不是 Android boot.img")
    g = lambda o: struct.unpack_from('<I', d, o)[0]
    g64 = lambda o: struct.unpack_from('<Q', d, o)[0]
    ps = g(36)
    h = dict(page_size=ps, header_version=g(40), header_size=g(1644),
             kernel_size=g(8), kernel_addr=g(12),
             ramdisk_size=g(16), ramdisk_addr=g(20),
             second_size=g(24), second_addr=g(28), tags_addr=g(32),
             dtb_size=g(1648), dtb_addr=g64(1652),
             # ★ v1/v2 头在 second 与 dtb 之间还有 recovery_dtbo 段；
             #   TWRP 系镜像该字段非 0，漏算会把 dtb 偏移算错。
             recovery_dtbo_size=g(1632), recovery_dtbo_offset=g64(1636))
    h['cmdline'] = d[64:576].split(b'\0')[0].decode('utf-8', 'replace')
    off = ps
    h['_koff'] = off; off += align(h['kernel_size'], ps)
    h['_roff'] = off; off += align(h['ramdisk_size'], ps)
    h['_soff'] = off; off += align(h['second_size'], ps)
    h['_recoff'] = off; off += align(h['recovery_dtbo_size'], ps)
    h['_doff'] = off
    h['_size'] = len(d)
    return h


def seg(d, h, which):
    if which == 'kernel':
        return d[h['_koff']:h['_koff'] + h['kernel_size']]
    if which == 'ramdisk':
        return d[h['_roff']:h['_roff'] + h['ramdisk_size']]
    if which == 'second':
        return d[h['_soff']:h['_soff'] + h['second_size']]
    if which == 'recovery_dtbo':
        return d[h['_recoff']:h['_recoff'] + h['recovery_dtbo_size']]
    if which == 'dtb':
        return d[h['_doff']:h['_doff'] + h['dtb_size']]
    raise SystemExit(which)


def load_any(spec, base_d, base_h, ks_d, ks_h, what):
    if spec in ('base', None):
        return seg(base_d, base_h, what), '骨架 boot'
    if spec == 'kernel-src':
        return seg(ks_d, ks_h, what), '内核来源 boot'
    if spec.startswith('file:'):
        p = spec[5:]
        return open(p, 'rb').read(), p
    p = spec
    d = open(p, 'rb').read()
    h = parse_hdr(d)
    return seg(d, h, what), p


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 1
    ks_path, base_path, out = sys.argv[1], sys.argv[2], sys.argv[3]
    ramdisk_from = 'base'
    dtb_from = 'base'
    args = sys.argv[4:]
    i = 0
    while i < len(args):
        if args[i] == '--ramdisk-from':
            ramdisk_from = args[i + 1]; i += 2
        elif args[i] == '--dtb-from':
            dtb_from = args[i + 1]; i += 2
        else:
            i += 1

    ks_d = open(ks_path, 'rb').read()
    base_d = open(base_path, 'rb').read()
    ks_h = parse_hdr(ks_d)
    base_h = parse_hdr(base_d)

    for tag, h in (('内核来源', ks_h), ('骨架', base_h)):
        if h['header_version'] != 2:
            raise SystemExit(f"{tag} boot 的 header_version={h['header_version']}，本脚本只支持 2")

    kernel = seg(ks_d, ks_h, 'kernel')
    ramdisk, rd_src = load_any(ramdisk_from, base_d, base_h, ks_d, ks_h, 'ramdisk')
    dtb, dtb_src = load_any(dtb_from, base_d, base_h, ks_d, ks_h, 'dtb')
    ps = base_h['page_size']

    if ks_h['kernel_addr'] != base_h['kernel_addr']:
        print("  ⚠ 内核加载地址不同: 内核来源 %#x / 骨架 %#x"
              % (ks_h['kernel_addr'], base_h['kernel_addr']))

    # ---- 骨架里 second / recovery_dtbo 原样保留（否则段偏移与头部声明不符）----
    second = seg(base_d, base_h, 'second')
    rdtbo = seg(base_d, base_h, 'recovery_dtbo')
    if base_h['recovery_dtbo_size']:
        print("  ⚠ 骨架含 recovery_dtbo %d 字节（已原样保留）"
              % base_h['recovery_dtbo_size'])

    # ---- 头部：以骨架为模板，改 kernel_size / ramdisk_size / dtb_size ----
    hdr = bytearray(base_d[:ps])
    struct.pack_into('<I', hdr, 8, len(kernel))
    struct.pack_into('<I', hdr, 16, len(ramdisk))
    struct.pack_into('<I', hdr, 1648, len(dtb))

    body = bytearray()
    for blob in (kernel, ramdisk, second, rdtbo, dtb):
        body += blob + b'\0' * (align(len(blob), ps) - len(blob))

    # ---- 沿用骨架 boot 的 AVB 元数据 + footer 结构 ----
    off_footer = base_h['_size'] - 64
    vb_off = struct.unpack_from('>Q', base_d, off_footer + 20)[0]
    vb_sz = struct.unpack_from('>Q', base_d, off_footer + 28)[0]
    vbm = base_d[vb_off:vb_off + vb_sz]

    total = base_h['_size']
    img = bytearray(hdr) + body
    img += b'\0' * (align(len(img), ps) - len(img))
    new_vb_off = len(img)
    img += vbm
    img += b'\0' * (total - 64 - len(img))

    ft = bytearray(64)
    ft[0:4] = b'AVBf'
    struct.pack_into('>I', ft, 4, 1)
    struct.pack_into('>I', ft, 8, 0)
    struct.pack_into('>Q', ft, 12, new_vb_off)       # original_image_size 占位
    struct.pack_into('>Q', ft, 20, new_vb_off)
    struct.pack_into('>Q', ft, 28, len(vbm))
    img += ft

    if len(img) != total:
        raise SystemExit("尺寸不匹配: %d != %d" % (len(img), total))
    open(out, 'wb').write(img)

    # ---- 回读校验 ----
    v = parse_hdr(bytes(img))
    ok = (v['kernel_size'] == len(kernel) and v['ramdisk_size'] == len(ramdisk)
          and v['dtb_size'] == len(dtb))
    dtb_magic = img[v['_doff']:v['_doff'] + 4] == b'\xd0\x0d\xfe\xed'

    print("== 混合 boot 已生成: %s" % out)
    print("   内核来源   : %s  (%s)" % (ks_path, _vermagic(kernel)))
    print("   骨架/头部  : %s" % base_path)
    print("   kernel     : %d 字节 @%#x" % (len(kernel), base_h['kernel_addr']))
    print("   ramdisk    : %d 字节  来源=%s" % (len(ramdisk), rd_src))
    print("   dtb        : %d 字节  来源=%s  FDT魔数=%s" % (len(dtb), dtb_src, dtb_magic))
    print("   文件大小   : %d  sha256=%s" % (len(img), hashlib.sha256(img).hexdigest()[:16]))
    print("   回读校验   : %s" % ("通过" if ok else "**失败**"))
    return 0 if (ok and dtb_magic) else 2


def _vermagic(k):
    import re
    m = re.search(rb'Linux version [^\x00\n]{10,120}', k)
    return m.group().decode('utf-8', 'replace') if m else '?'


if __name__ == '__main__':
    sys.exit(main())
