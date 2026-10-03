#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
解析 Android AVB vbmeta 镜像，输出头部标志位与所有 descriptor。

关键点（踩过的坑，务必保留）:
  * vbmeta 头部是【大端序】(big-endian) —— libavb 全程用 avb_be32toh/avb_be64toh。
  * 头部真实布局（avbtool: '!4sIIQQI' + 11×Q + 'II' + '48s80s' = 256 字节）:
        0   magic(4s)            4   required_major(I)   8   required_minor(I)
        12  auth_block_size(Q)   20  aux_block_size(Q)   28  algorithm_type(I)
        32  hash_offset(Q)       40  hash_size(Q)
        48  signature_offset(Q)  56  signature_size(Q)
        64  public_key_offset(Q) 72  public_key_size(Q)
        80  pk_metadata_offset(Q)88  pk_metadata_size(Q)
        96  descriptors_offset(Q)104 descriptors_size(Q)
        112 rollback_index(Q)    120 flags(I)            124 rollback_index_location(I)
        128 release_string(48s)  176 reserved(80s)
    ★ 曾经把 flags 读成 +124，结果读到 rollback_index_location，
      得出「校验全开」的错误结论。flags 在 **+120**。
  * 描述符区起点 = 256 + auth_block_size（descriptors_offset 相对 auth 块末尾，通常为 0）。
  * flags: 0x1 = HASHTREE_DISABLED, 0x2 = VERIFICATION_DISABLED。
    VERIFICATION_DISABLED 置位 ⇒ bootloader 不再校验该 vbmeta 覆盖的分区
    （且 CHAIN 描述符不再被跟进 ⇒ 被链的 vbmeta_system / vbmeta_vendor 也一并不校验）。

用法: vbmeta_parse.py <vbmeta.img> [...]
"""
import struct
import sys
import os

MAGIC = b'AVB0'
HEADER_SIZE = 256

FLAG_HASHTREE_DISABLED = 0x1
FLAG_VERIFICATION_DISABLED = 0x2

ALGO = {
    0: 'NONE', 1: 'SHA256_RSA2048', 2: 'SHA256_RSA4096', 3: 'SHA256_RSA8192',
    4: 'SHA512_RSA2048', 5: 'SHA512_RSA4096', 6: 'SHA512_RSA8192',
}

TAG_PROPERTY = 0
TAG_HASHTREE = 1
TAG_HASH = 2
TAG_KERNEL_CMDLINE = 3
TAG_CHAIN_PARTITION = 4
TAG_CHAIN_PARTITION_SET = 5

# 头部字段偏移（唯一权威定义）
OFF_AUTH = 12
OFF_ALGO = 28
OFF_DESC_OFF = 96
OFF_DESC_SZ = 104
OFF_ROLLBACK = 112
OFF_FLAGS = 120
OFF_ROLLBACK_LOC = 124
OFF_RELEASE = 128


def rd(fmt, buf, off):
    return struct.unpack_from('>' + fmt, buf, off)


def _blob(data, off):
    """读一个 length-prefixed blob（u32 长度 + 数据 + 补到 8 字节）"""
    n = rd('I', data, off)[0]
    raw = data[off + 4:off + 4 + n]
    nxt = off + 4 + n
    nxt += (8 - (nxt % 8)) % 8
    return raw, nxt


def _strlist(data, off, count, limit=64):
    """读一串 length-prefixed 字符串（CHAIN / CHAINSET 用）"""
    names = []
    for _ in range(min(count, limit)):
        n = rd('I', data, off)[0]
        if n > 512:
            names.append('<len=%d 异常>' % n)
            break
        names.append(data[off + 4:off + 4 + n].split(b'\x00')[0].decode('utf-8', 'replace'))
        off += 4 + n
        off += (8 - (off % 8)) % 8
    if count > limit:
        names.append('...(共 %d 个)' % count)
    return names


def parse(path):
    size = os.path.getsize(path)
    with open(path, 'rb') as f:
        data = f.read()
    print("===== %s  (%d 字节)" % (os.path.basename(path), size))
    if data[:4] != MAGIC:
        print("  ✗ magic 不是 AVB0，实际 %r\n" % data[:4])
        return
    major, minor = rd('II', data, 4)
    auth_blk, aux_blk = rd('QQ', data, OFF_AUTH)
    (algo, hash_off, hash_sz, sig_off, sig_sz,
     pk_off, pk_sz, pkm_off, pkm_sz,
     desc_off, desc_sz) = rd('IQQQQQQQQQQ', data, OFF_ALGO)
    rollback = rd('Q', data, OFF_ROLLBACK)[0]
    flags = rd('I', data, OFF_FLAGS)[0]
    rollback_loc = rd('I', data, OFF_ROLLBACK_LOC)[0]
    rel = data[OFF_RELEASE:OFF_RELEASE + 48].split(b'\x00')[0].decode('utf-8', 'replace')

    fl = []
    if flags & FLAG_HASHTREE_DISABLED:
        fl.append('HASHTREE_DISABLED(0x1)')
    if flags & FLAG_VERIFICATION_DISABLED:
        fl.append('VERIFICATION_DISABLED(0x2)')

    print("  avb 版本        : %d.%d" % (major, minor))
    print("  auth_block_size : %d    aux_block_size: %d" % (auth_blk, aux_blk))
    print("  algorithm       : %d (%s)" % (algo, ALGO.get(algo, '?')))
    print("  描述符区        : off=%d(相对auth末尾) size=%d" % (desc_off, desc_sz))
    print("  rollback_index  : %d (loc=%d)" % (rollback, rollback_loc))
    print("  ★ flags         : 0x%x  -> %s" % (flags, ' + '.join(fl) if fl else '校验全开 (0)'))
    print("  release_string  : %r" % rel)

    base = HEADER_SIZE + auth_blk + desc_off
    end = base + desc_sz
    if end > size:
        print("  ⚠ 描述符区超出文件 (base=%d + %d > %d)" % (base, desc_sz, size))
    end = min(end, size)

    off = base
    n = 0
    while off + 16 <= end and n < 200:
        tag, dsiz = rd('QQ', data, off)
        body = off + 16
        if dsiz <= 0 or body + dsiz > end:
            print("    [%d] tag=%d dsiz=%d  ✗ 越界，停止" % (n, tag, dsiz))
            break
        if tag == TAG_PROPERTY:
            kl, vl = rd('QQ', data, body)
            k = data[body + 16:body + 16 + kl].split(b'\x00')[0].decode('utf-8', 'replace')
            v = data[body + 16 + kl:body + 16 + kl + vl]
            print("    [%d] PROPERTY  %s = %r" % (n, k, v))
        elif tag == TAG_HASHTREE:
            (dm_ver, img_sz, t_off, t_sz,
             d_blk, h_blk, fec_roots, fec_off, fec_sz) = rd('IQQQIIIQQ', data, body)
            p = body + 56
            alg, p = _blob(data, p)
            pname, p = _blob(data, p)
            salt, p = _blob(data, p)
            root, p = _blob(data, p)
            hf = rd('I', data, p)[0] if p + 4 <= end else 0
            print("    [%d] HASHTREE  part=%r image=%d tree_off=%d tree=%d data_blk=%d hash_blk=%d"
                  % (n, pname.decode('utf-8', 'replace'), img_sz, t_off, t_sz, d_blk, h_blk))
            print("                 alg=%r fec_roots=%d fec=%dB flags=0x%x"
                  % (alg.decode('utf-8', 'replace'), fec_roots, fec_sz, hf))
            print("                 salt=%s root=%s" % (salt.hex(), root.hex()))
        elif tag == TAG_HASH:
            img_sz, h_off, h_sz, algo2, salgo, s_off, s_sz, f2 = rd('QQQ I Q Q Q I', data, body)
            print("    [%d] HASH      image=%dB hash=%dB alg=%s salt=%s"
                  % (n, img_sz, h_sz, ALGO.get(algo2, algo2), salgo))
        elif tag == TAG_KERNEL_CMDLINE:
            klen = rd('Q', data, body)[0]
            print("    [%d] CMDLINE   %r" % (n, data[body + 8:body + 8 + klen]))
        elif tag == TAG_CHAIN_PARTITION:
            rloc, nparts = rd('II', data, body)
            names = _strlist(data, body + 8, nparts)
            print("    [%d] CHAIN     rollback_loc=%d -> %s" % (n, rloc, names))
        elif tag == TAG_CHAIN_PARTITION_SET:
            rloc, nparts = rd('II', data, body)
            names = _strlist(data, body + 8, nparts)
            print("    [%d] CHAINSET  rollback_loc=%d -> %s" % (n, rloc, names))
        else:
            print("    [%d] tag=%d size=%d (未识别)" % (n, tag, dsiz))
        n += 1
        off = body + dsiz
    print("  ── 共 %d 个描述符\n" % n)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for a in sys.argv[1:]:
        parse(a)
