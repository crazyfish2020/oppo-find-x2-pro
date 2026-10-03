#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
erofs_read.py — 纯 Python EROFS 只读解析器（无外部依赖）。

支持: FLAT_PLAIN / FLAT_INLINE 数据布局；compact(32B) / extended(64B) inode。
不支持: 压缩(datalayout 1/3)、CHUNK_BASED(4) —— 遇到会明确报错。

用法:
  erofs_read.py probe  <img>
  erofs_read.py ls     <img> <路径>          # 默认 /
  erofs_read.py cat    <img> <路径> <输出文件>
  erofs_read.py tree   <img> [最大深度]      # 递归列目录
  erofs_read.py size   <img> <路径>          # 只报大小/布局
"""
import sys
import struct
import os

EROFS_MAGIC = 0xE0F5E1E2
EROFS_NULL_ADDR = 0xFFFFFFFF

DATALAYOUT = {
    0: 'FLAT_PLAIN',
    1: 'COMPRESSED_FULL',
    2: 'FLAT_INLINE',
    3: 'COMPRESSED_COMPACT',
    4: 'CHUNK_BASED',
}

# 文件类型(erofs_dirent.file_type)
FT_UNKNOWN = 0
FT_REG = 1
FT_DIR = 2
FT_CHR = 3
FT_BLK = 4
FT_FIFO = 5
FT_SOCK = 6
FT_SYMLINK = 7
FT_MAX = 8

FT_NAME = {0: '?', 1: '-', 2: 'd', 3: 'c', 4: 'b', 5: 'p', 6: 's', 7: 'l'}


class ErofsError(Exception):
    pass


class Erofs:
    def __init__(self, path, iloc_mode='A'):
        self.path = path
        self.f = open(path, 'rb')
        self.size = os.path.getsize(path)
        self.f.seek(1024)
        sb = self.f.read(128)
        if len(sb) < 128:
            raise ErofsError("超级块读取失败")
        (self.magic, self.checksum, self.feature_compat) = struct.unpack_from('<III', sb, 0)
        if self.magic != EROFS_MAGIC:
            raise ErofsError(f"魔数不对: {self.magic:#x}")
        self.blkszbits = sb[12]
        self.sb_extslots = sb[13]
        (self.root_nid,) = struct.unpack_from('<H', sb, 14)
        (self.inos, self.build_time) = struct.unpack_from('<QQ', sb, 16)
        (self.build_time_nsec, self.blocks, self.meta_blkaddr, self.xattr_blkaddr) = \
            struct.unpack_from('<IIII', sb, 32)
        self.uuid = sb[48:64]
        self.volume_name = sb[64:80].split(b'\x00')[0].decode('utf-8', 'replace')
        (self.feature_incompat,) = struct.unpack_from('<I', sb, 80)
        self.blksz = 1 << self.blkszbits
        self.iloc_mode = iloc_mode

    # ---------- inode 定位 ----------
    def iloc(self, nid):
        if self.iloc_mode == 'A':
            return nid << 5
        # 模式 B: nid = (block << slots_per_block_bits) | slot
        spb = self.blksz >> 5
        return (nid // spb) * self.blksz + (nid % spb) * 32

    def read_inode(self, nid):
        off = self.iloc(nid)
        if off + 32 > self.size:
            raise ErofsError(f"nid {nid} -> 偏移 {off} 越界")
        self.f.seek(off)
        raw = self.f.read(64)
        (i_format, i_xattr_icount) = struct.unpack_from('<HH', raw, 0)
        version = i_format & 1
        datalayout = (i_format >> 1) & 7
        if version == 0:
            (i_mode, i_nlink) = struct.unpack_from('<HH', raw, 4)
            (i_size, i_reserved) = struct.unpack_from('<II', raw, 8)
            (i_u,) = struct.unpack_from('<I', raw, 16)
            (i_ino,) = struct.unpack_from('<I', raw, 20)
            (i_uid, i_gid) = struct.unpack_from('<HH', raw, 24)
            inode_size = 32
            i_mtime = 0
        else:
            (i_mode, i_reserved) = struct.unpack_from('<HH', raw, 4)
            (i_size, i_mtime) = struct.unpack_from('<QQ', raw, 8)
            (i_mtime_nsec, i_nlink, i_uid, i_gid, i_reserved2) = \
                struct.unpack_from('<IIIII', raw, 24)
            (i_u,) = struct.unpack_from('<I', raw, 44)
            (i_ino,) = struct.unpack_from('<I', raw, 48)
            inode_size = 64
        # xattr 内联体大小
        #   erofs_xattr_ibody_size(icount) =
        #       icount ? sizeof(erofs_xattr_ibody_header)=12 + 4*(icount-1) : 0
        #   即 8 + 4*icount (icount>=1)
        #   (实测校准: vendor 根 inode icount=2 -> 内联数据始于 iloc+32+16)
        if i_xattr_icount == 0:
            xattr_isize = 0
        elif i_xattr_icount & 0x8000:
            xattr_isize = 0            # 共享 xattr (EROFS_I_EA_INITED)，无内联体
        else:
            xattr_isize = 8 + 4 * (i_xattr_icount & 0x7fff)
        return {
            'nid': nid, 'off': off, 'version': version, 'datalayout': datalayout,
            'i_mode': i_mode, 'i_nlink': i_nlink, 'i_size': i_size, 'i_u': i_u,
            'i_ino': i_ino, 'i_uid': i_uid, 'i_gid': i_gid, 'i_mtime': i_mtime,
            'inode_size': inode_size, 'xattr_isize': xattr_isize,
            'inline_off': off + inode_size + xattr_isize,
            'is_dir': (i_mode & 0o170000) == 0o040000,
        }

    # ---------- 文件数据 ----------
    def read_data(self, ino):
        dl = ino['datalayout']
        size = ino['i_size']
        if dl in (1, 3):
            raise ErofsError(f"压缩布局 {DATALAYOUT[dl]} 暂不支持 (nid {ino['nid']})")
        if dl == 4:
            raise ErofsError(f"CHUNK_BASED 暂不支持 (nid {ino['nid']})")
        if size == 0:
            return b''
        blksz = self.blksz
        nblocks = (size + blksz - 1) // blksz
        out = bytearray()
        if dl == 0:  # FLAT_PLAIN
            if ino['i_u'] == EROFS_NULL_ADDR:
                return b'\x00' * size
            self.f.seek(ino['i_u'] * blksz)
            out = bytearray(self.f.read(size))
        elif dl == 2:  # FLAT_INLINE
            ext_blocks = nblocks - 1
            ext_bytes = ext_blocks * blksz
            if ext_blocks > 0:
                if ino['i_u'] == EROFS_NULL_ADDR:
                    out += b'\x00' * ext_bytes
                else:
                    self.f.seek(ino['i_u'] * blksz)
                    out += self.f.read(ext_bytes)
            inline_len = size - ext_bytes
            self.f.seek(ino['inline_off'])
            out += self.f.read(inline_len)
        else:
            raise ErofsError(f"未知布局 {dl}")
        if len(out) != size:
            raise ErofsError(f"数据长度不符: 期望 {size}, 实得 {len(out)} (nid {ino['nid']})")
        return bytes(out)

    # ---------- 目录 ----------
    def listdir(self, ino):
        data = self.read_data(ino)
        blksz = self.blksz
        entries = []
        pos = 0
        while pos < len(data):
            blk = data[pos:pos + blksz]
            if len(blk) < 12:
                break
            (first_nameoff,) = struct.unpack_from('<H', blk, 8)
            n = first_nameoff // 12
            if n == 0:
                break
            for i in range(n):
                e_off = i * 12
                (nid, nameoff, ftype, _r) = struct.unpack_from('<QHBB', blk, e_off)
                if i + 1 < n:
                    (next_nameoff,) = struct.unpack_from('<H', blk, (i + 1) * 12 + 8)
                else:
                    next_nameoff = len(blk)
                # EROFS 目录块尾部用 0 填充; 内核用 strnlen 截断, 这里同样处理,
                # 否则最后一个 dirent 的名字会带上 \x00 填充。
                name = blk[nameoff:next_nameoff].split(b'\x00', 1)[0]
                if name:
                    entries.append((name.decode('utf-8', 'replace'), nid, ftype))
            pos += blksz
        return entries

    def resolve(self, path):
        """返回 (inode, name)"""
        ino = self.read_inode(self.root_nid)
        parts = [p for p in path.split('/') if p]
        cur_name = '/'
        for p in parts:
            if not ino['is_dir']:
                raise ErofsError(f"{cur_name} 不是目录")
            found = None
            for name, nid, ftype in self.listdir(ino):
                if name == p:
                    found = (nid, ftype)
                    break
            if not found:
                raise ErofsError(f"路径不存在: {path} (在 {cur_name} 下找不到 {p})")
            ino = self.read_inode(found[0])
            cur_name = p
        return ino

    def close(self):
        self.f.close()


# ---------------- CLI ----------------
def cmd_probe(img):
    print(f"=== {img} ===")
    for mode in ('A', 'B'):
        try:
            e = Erofs(img, iloc_mode=mode)
        except ErofsError as ex:
            print(f"  模式{mode}: {ex}")
            continue
        print(f"\n--- iloc 模式 {mode} (公式 {'nid*32' if mode=='A' else '(nid//128)*4096+(nid%128)*32'}) ---")
        print(f"  魔数         : {e.magic:#x} ✓")
        print(f"  blkszbits    : {e.blkszbits}  (块大小 {e.blksz})")
        print(f"  feature_compat   : {e.feature_compat:#010x}")
        print(f"  feature_incompat : {e.feature_incompat:#010x}")
        print(f"  root_nid     : {e.root_nid}  -> 偏移 {e.iloc(e.root_nid)}")
        print(f"  inos         : {e.inos}")
        print(f"  blocks       : {e.blocks}  ({e.blocks*e.blksz} 字节 = {e.blocks*e.blksz/1048576:.1f} MiB)")
        print(f"  meta_blkaddr : {e.meta_blkaddr}   xattr_blkaddr: {e.xattr_blkaddr}")
        print(f"  uuid         : {e.uuid.hex()}")
        print(f"  卷名         : {e.volume_name!r}")
        import datetime
        try:
            print(f"  构建时间     : {datetime.datetime.fromtimestamp(e.build_time)}")
        except Exception:
            pass
        try:
            ino = e.read_inode(e.root_nid)
            print(f"  根 inode     : datalayout={DATALAYOUT.get(ino['datalayout'])} "
                  f"version={'ext' if ino['version'] else 'compact'} size={ino['i_size']} "
                  f"mode={oct(ino['i_mode'])} is_dir={ino['is_dir']}")
            if ino['is_dir']:
                ents = e.listdir(ino)
                print(f"  根目录条目({len(ents)}): {[n for n, _, _ in ents][:20]}")
        except ErofsError as ex:
            print(f"  根目录解析失败: {ex}")
        e.close()
    print()


def cmd_ls(img, path='/'):
    e = Erofs(img)
    ino = e.resolve(path)
    if not ino['is_dir']:
        print(f"{path}: 不是目录 (size={ino['i_size']}, {DATALAYOUT.get(ino['datalayout'])})")
        return
    ents = e.listdir(ino)
    print(f"{path}  共 {len(ents)} 项")
    for name, nid, ftype in sorted(ents, key=lambda x: x[0]):
        t = FT_NAME.get(ftype, '?')
        try:
            ci = e.read_inode(nid)
            extra = f"size={ci['i_size']:>10}  {DATALAYOUT.get(ci['datalayout'], ci['datalayout'])}"
        except ErofsError:
            extra = ""
        print(f"  {t} {name:<40} nid={nid:<8} {extra}")
    e.close()


def cmd_size(img, path):
    e = Erofs(img)
    ino = e.resolve(path)
    print(f"{path}: size={ino['i_size']} datalayout={DATALAYOUT.get(ino['datalayout'])} "
          f"version={'ext' if ino['version'] else 'compact'} nid={ino['nid']} "
          f"raw_blkaddr={ino['i_u']} inline_off={ino['inline_off']}")
    e.close()


def cmd_cat(img, path, out):
    e = Erofs(img)
    ino = e.resolve(path)
    data = e.read_data(ino)
    with open(out, 'wb') as f:
        f.write(data)
    print(f"提取 {path} -> {out}  ({len(data)} 字节)")
    e.close()


def cmd_tree(img, maxdepth=3):
    e = Erofs(img)
    root = e.read_inode(e.root_nid)
    total = [0, 0]

    def walk(ino, prefix, depth):
        if depth > maxdepth:
            return
        try:
            ents = e.listdir(ino)
        except ErofsError as ex:
            print(f"{prefix}  <错误: {ex}>")
            return
        for name, nid, ftype in sorted(ents, key=lambda x: x[0]):
            try:
                ci = e.read_inode(nid)
            except ErofsError:
                continue
            total[0] += 1
            total[1] += ci['i_size']
            mark = '/' if ci['is_dir'] else ''
            if depth <= maxdepth:
                print(f"{prefix}{FT_NAME.get(ftype,'?')} {name}{mark}"
                      f"{'' if ci['is_dir'] else '  ' + str(ci['i_size'])}")
            if ci['is_dir']:
                walk(ci, prefix + '    ', depth + 1)

    print(f"{img}:")
    walk(root, '  ', 1)
    print(f"\n合计 {total[0]} 项, 累计 size {total[1]} 字节")
    e.close()


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    img = sys.argv[2]
    if cmd == 'probe':
        cmd_probe(img)
    elif cmd == 'ls':
        cmd_ls(img, sys.argv[3] if len(sys.argv) > 3 else '/')
    elif cmd == 'cat':
        cmd_cat(img, sys.argv[3], sys.argv[4])
    elif cmd == 'tree':
        cmd_tree(img, int(sys.argv[3]) if len(sys.argv) > 3 else 3)
    elif cmd == 'size':
        cmd_size(img, sys.argv[3])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == '__main__':
    main()
