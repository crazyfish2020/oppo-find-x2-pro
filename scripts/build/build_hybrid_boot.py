#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建 hybrid boot.img：X2 Pro 内核 + X2 Pro dtb + A16(8T) ramdisk
用途：fastboot boot 零风险验证「ramdisk/init 版本不匹配」假设
"""
import struct, os, sys, hashlib

BASE = '/Users/skeletondie/Documents/oppo/2026-安卓16'
DEV_BOOT = os.path.join(BASE, '04-日志/dump_202707/boot.img')   # 设备当前刷着的 boot（A15 ramdisk）
A16_BOOT = os.path.join(BASE, '06-校验/8t_boot.img')            # 8T/A16 原厂 boot

def rd(p):
    return open(p, 'rb').read()

def parse_hdr(d):
    assert d[:8] == b'ANDROID!', 'bad magic'
    g = lambda o: struct.unpack_from('<I', d, o)[0]
    ps = g(36)
    h = dict(kernel_size=g(8), kernel_addr=g(12), ramdisk_size=g(16), ramdisk_addr=g(20),
             second_size=g(24), second_addr=g(28), tags_addr=g(32), page_size=ps,
             header_version=g(40), os_version=g(44))
    h['cmdline'] = d[64:576].split(b'\0')[0]
    h['extra_cmdline'] = d[608:1632].split(b'\0')[0]
    h['header_size'] = g(1644); h['dtb_size'] = g(1648); h['dtb_addr'] = g(1652)
    # ★ v1/v2 头在 second 与 dtb 之间还有一段 recovery_dtbo（TWRP 镜像该字段非 0，
    #   漏掉它会把 dtb 偏移算错 → ABL 读到垃圾 dtb → 内核拿到错的 initrd 区间）。
    h['recovery_dtbo_size'] = g(1632)
    h['recovery_dtbo_offset'] = struct.unpack_from('<Q', d, 1636)[0]
    off = ps
    h['_koff'] = off
    off += (h['kernel_size'] + ps - 1) // ps * ps
    h['_roff'] = off
    off += (h['ramdisk_size'] + ps - 1) // ps * ps
    h['_soff'] = off
    off += (h['second_size'] + ps - 1) // ps * ps
    h['_recoff'] = off
    off += (h['recovery_dtbo_size'] + ps - 1) // ps * ps
    h['_doff'] = off
    return h

def align(n, a):
    return (n + a - 1) // a * a

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else '/tmp/hybrid_a16rd.img'
    ramdisk_src = sys.argv[2] if len(sys.argv) > 2 else 'a16'   # a16 | dev | dev_a16init

    db = rd(DEV_BOOT)
    ab = rd(A16_BOOT)
    hd = parse_hdr(db)
    ha = parse_hdr(ab)
    ps = hd['page_size']

    kernel = db[hd['_koff']:hd['_koff'] + hd['kernel_size']]
    dtb = db[hd['_doff']:hd['_doff'] + hd['dtb_size']]

    if ramdisk_src == 'a16':
        ramdisk = ab[ha['_roff']:ha['_roff'] + ha['ramdisk_size']]
        note = 'A16(8T) 原厂 ramdisk'
    elif ramdisk_src == 'dev':
        ramdisk = db[hd['_roff']:hd['_roff'] + hd['ramdisk_size']]
        note = '设备现用 ramdisk (A15)'
    elif ramdisk_src.startswith('file:'):
        path = ramdisk_src[5:]
        ramdisk = open(path, 'rb').read()
        note = '外部 ramdisk %s' % path
    else:
        raise SystemExit('unknown ramdisk_src')

    # 骨架里 second / recovery_dtbo 两段原样保留，否则段偏移与头部声明不符
    second = db[hd['_soff']:hd['_soff'] + hd['second_size']]
    rdtbo = db[hd['_recoff']:hd['_recoff'] + hd['recovery_dtbo_size']]

    # 头部：以设备头为骨架，改 ramdisk_size / dtb_size（second / recovery_dtbo 不变）
    hdr = bytearray(db[:ps])
    struct.pack_into('<I', hdr, 16, len(ramdisk))
    struct.pack_into('<I', hdr, 1648, len(dtb))

    body = bytearray()
    for blob in (kernel, ramdisk, second, rdtbo, dtb):
        body += blob + b'\0' * (align(len(blob), ps) - len(blob))

    if hd['recovery_dtbo_size']:
        print('  ⚠ 骨架含 recovery_dtbo %d 字节（已原样保留，段偏移按正规算法计算）'
              % hd['recovery_dtbo_size'])

    content_len = ps + len(body)

    # AVB 区：沿用设备 boot 的 vbmeta blob 结构（boot 校验已禁用，仅保持结构一致）
    dev_vbmeta_off = struct.unpack_from('>Q', db, len(db) - 64 + 20)[0]
    dev_vbmeta_sz = struct.unpack_from('>Q', db, len(db) - 64 + 28)[0]
    vbm = db[dev_vbmeta_off:dev_vbmeta_off + dev_vbmeta_sz]

    # 把 vbmeta 放到 content 之后
    total_target = len(db)  # 与设备 boot 同尺寸 100663296
    img = bytearray(hdr) + body
    img += b'\0' * (align(len(img), ps) - len(img))
    vb_off = len(img)
    img += vbm
    img += b'\0' * (total_target - 64 - len(img))

    # AVBf footer（大端）
    ft = bytearray(64)
    ft[0:4] = b'AVBf'
    struct.pack_into('>I', ft, 4, 1)
    struct.pack_into('>I', ft, 8, 0)
    struct.pack_into('>Q', ft, 12, content_len)
    struct.pack_into('>Q', ft, 20, vb_off)
    struct.pack_into('>Q', ft, 28, len(vbm))
    img += ft

    assert len(img) == total_target, (len(img), total_target)
    open(out, 'wb').write(img)

    print('== hybrid boot 已生成: %s' % out)
    print('   ramdisk 来源 : %s (%d B)' % (note, len(ramdisk)))
    print('   kernel       : X2 Pro %d B @%d' % (len(kernel), hd['kernel_addr']))
    print('   dtb          : X2 Pro %d B @%d' % (len(dtb), hd['dtb_addr']))
    print('   内容长度     : %d   vbmeta@%d size=%d' % (content_len, vb_off, len(vbm)))
    print('   文件大小     : %d  sha256=%s' % (len(img), hashlib.sha256(img).hexdigest()[:16]))
    # 回读校验
    v = parse_hdr(bytes(img[:4096]))
    print('   回读: k=%d r=%d d=%d  (r 应为 %d)' % (v['kernel_size'], v['ramdisk_size'], v['dtb_size'], len(ramdisk)))

if __name__ == '__main__':
    main()
