#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
erofs_rebuild.py — 在 macOS(非 root) 上重建 Android EROFS 镜像，保真 uid/gid/xattr。

原理:
  1. 用自带的 EROFS 解析器读取【原镜像】的元数据 (uid/gid/mode/类型/符号链接目标)
     —— 只读 inode 与目录数据，不解压文件内容，因此不需要实现 lz4 解码。
  2. 文件内容从【已解包的目录树】读取 (fsck.erofs --extract --xattrs 已还原内容+xattr+权限)。
  3. 用 Python 的 tarfile 组装 tar: uid/gid/mode 来自原镜像, xattr 以 PAX
     SCHILY.xattr.<name> 记录写入, 文件内容来自目录树。
  4. 调用 mkfs.erofs --tar=f 由 tar 构建镜像。

用法:
  erofs_rebuild.py <原镜像> <解包目录> <输出镜像> [选项]...

  --replace <镜像内路径>=<本地新文件>[#<模板路径(树内相对路径)>]
        替换已有文件 (路径为镜像内绝对路径, 如 /bin/netbpfload)。
        ★ xattr 默认取 <镜像内路径> 在【树】里的 xattr。若该文件只在源镜像里、
          树里没有 (典型：前几轮补丁加进去的 /system/bin/netbpfload、
          /system/etc/init/wb_diag.rc …)，就【一条 xattr 都取不到】，
          写出来的文件会变成 u:object_r:unlabeled:s0 —— init 会直接拒绝
          exec_start，而且这个错误在 permissive 下照样报。
          此时必须用 #<模板路径> 显式指定标签来源，例如
            --replace /system/bin/netbpfload=x/netbpfload#system/bin/bpfloader

  --add <镜像内路径>=<本地文件>[#<模板路径(树内相对路径)>]
        新增一个【文件】条目。uid/gid 固定为 0:0, 权限默认 0644
        (可被 --addmode 覆盖)。
        xattr 优先取 <模板路径> 在树中的 xattr, 否则取本地文件自身的 xattr。
        xattr 模板用于给新文件打上正确的 SELinux 标签。

  --addmode <镜像内路径>=<八进制权限>
        覆盖 --add 新增文件的权限。可执行文件必须写 0755,
        否则 init 无法 exec 它 (例如 system/bin/netbpfload=0755)。

  --materialize <镜像内路径>=<本地文件>[#<模板路径>]
        ★ 把镜像里【原本是符号链接】的条目改成【实体文件】。
        这是 --replace / --add 做不到的:
          * --add  遇到已存在的路径会退化成替换语义;
          * --replace 会保留源镜像里的条目类型(符号链接),
            于是 tar 里写出的仍是 SYMTYPE,本地文件内容被忽略。
        典型用途: /system/bin/linker64 在 A16 里是软链到
        /apex/com.android.runtime/bin/linker64, 而 /apex 是运行期才挂的
        tmpfs —— 一旦 apexd 起不来, 所有 PT_INTERP=/system/bin/linker64
        的程序(sh/toybox/logd/adbd...)全部 exec 不了。
        用 --materialize 把真实 linker 放进 /system, 就能让它们脱离 /apex 运行。
        uid/gid 沿用源镜像条目, 权限默认 0755(可被 --addmode 覆盖),
        xattr 取 <模板路径> 在树中的 xattr(用来打正确的 SELinux 标签)。

  --adddir <镜像内路径>
        新增一个【目录】条目 (uid/gid 0:0, 权限 0755)。

  --stream
        ★ 不落盘 tar，直接把 tar 流通过管道喂给 mkfs.erofs
        (mkfs.erofs <out.img> /dev/stdin，1.9.4 实测可用)。
        默认行为是先把 tar 写成 <out>.tar 再调 mkfs，峰值占用 =
        tar + 成品镜像 ≈ 2.4 GB；--stream 把峰值降到【只有成品镜像】
        ≈ 1.06 GB。~/Documents 长期 99% 占用时必开。
        注意：--stream 下没有中间 tar，--keep-tar 自动失效。

  --zopts "<mkfs z 参数>"
        覆盖压缩参数。必须与【原镜像】feature_incompat 对应:
          incompat=0x1 -> "-zlz4hc,level=9"
          incompat=0x3 -> "-zlz4hc,level=9 -C16384"

  --keep-tar
        保留中间 tar 文件（默认构建成功后删除；tar 与成品镜像差不多大，
        如 system_ext 的 tar 有 1.68 GB，而 ~/Documents 长期 99% 占用）。

注意事项:
  * 【解包目录】必须是用 `fsck.erofs --extract=<tree> --xattrs <img>` 解出来的，
    否则 xattr(SELinux 标签) 全部丢失。脚本已内置防护：若 xattr 记录为 0
    而原镜像根目录带 xattr，会直接报错退出(2)。
"""
import sys
import os
import io
import re
import tarfile
import subprocess
import ctypes
import ctypes.util
import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from erofs_read import Erofs, ErofsError, DATALAYOUT   # noqa: E402

MKFS = "/Users/skeletondie/.workbuddy-ai/tools/erofs/erofs-utils/1.9.4/bin/mkfs.erofs"

# ---------- macOS xattr 读取 (ctypes) ----------
_libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)
_libc.listxattr.restype = ctypes.c_ssize_t
_libc.listxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int]
_libc.getxattr.restype = ctypes.c_ssize_t
_libc.getxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                           ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]


def mac_listxattr(path):
    p = path.encode()
    n = _libc.listxattr(p, None, 0, 0)
    if n <= 0:
        return []
    buf = ctypes.create_string_buffer(n)
    n = _libc.listxattr(p, buf, n, 0)
    if n <= 0:
        return []
    return [x.decode() for x in buf.raw[:n].split(b'\x00') if x]


def mac_getxattr(path, name):
    p = path.encode()
    nm = name.encode()
    n = _libc.getxattr(p, nm, None, 0, 0, 0)
    if n < 0:
        return None
    buf = ctypes.create_string_buffer(n)
    n = _libc.getxattr(p, nm, buf, n, 0, 0)
    if n < 0:
        return None
    return buf.raw[:n]


# ---------- 遍历 EROFS 元数据 ----------
def walk_meta(img):
    """返回 [(relpath, type, mode, uid, gid, size, linktarget)]，relpath 无前导 /"""
    e = Erofs(img)
    out = []

    def rec(ino, rel):
        ents = e.listdir(ino)
        for name, nid, ftype in ents:
            if name in ('.', '..'):
                continue
            ci = e.read_inode(nid)
            mode = ci['i_mode']
            is_dir = (mode & 0o170000) == 0o040000
            is_lnk = (mode & 0o170000) == 0o120000
            sub = f"{rel}/{name}" if rel else name
            link = None
            if is_lnk:
                try:
                    link = e.read_data(ci).decode('utf-8', 'replace')
                except ErofsError:
                    link = None
            out.append((sub, ftype, mode, ci['i_uid'], ci['i_gid'], ci['i_size'], link))
            if is_dir:
                rec(ci, sub)

    root = e.read_inode(e.root_nid)
    out.append(('', 2, root['i_mode'], root['i_uid'], root['i_gid'], root['i_size'], None))
    rec(root, '')
    e.close()
    return out


# ---------- 从 dump.erofs 输出里取 xattr 尺寸 ----------
#   注意: dump.erofs 把 Xattr size 打在 Inode size 同一行，形如
#         "Inode size: 32   Xattr size: 16"
#   所以不能用 startswith("Xattr size:") —— 那样永远匹配不到（本项目踩过）。
_XATTR_RE = re.compile(r'Xattr size:\s*(\d+)')


def src_root_xattr(img):
    """原镜像根目录的 xattr 字节数；取不到返回 None"""
    dump = os.path.join(os.path.dirname(MKFS), "dump.erofs")
    try:
        rr = subprocess.run([dump, "--path=/", img], capture_output=True, text=True)
    except Exception:
        return None
    m = _XATTR_RE.search(rr.stdout)
    return int(m.group(1)) if m else None


def tree_has_xattr(tree, limit=200):
    """目录树里抽查若干文件，返回带 xattr 的文件数（>0 即认为树保住了 xattr）"""
    n = 0
    seen = 0
    for root, _dirs, files in os.walk(tree):
        for f in files:
            seen += 1
            if mac_listxattr(os.path.join(root, f)):
                return 1
            if seen >= limit:
                return 0
    return n


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    src_img, tree, out_img = sys.argv[1], sys.argv[2], sys.argv[3]
    replaces = {}
    adds = {}
    adddirs = []
    addmodes = {}
    materializes = {}
    zopts = "-zlz4hc,level=9"
    keep_tar = False
    stream = False
    args = sys.argv[4:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--replace':
            k, v = args[i + 1].split('=', 1)
            xt = None
            if '#' in v:
                v, xt = v.split('#', 1)
            replaces[k.lstrip('/')] = dict(local=v, xtmpl=xt)
            i += 2
        elif a == '--add':
            k, v = args[i + 1].split('=', 1)
            xt = None
            if '#' in v:
                v, xt = v.split('#', 1)
            adds[k.lstrip('/')] = dict(local=v, xtmpl=xt)
            i += 2
        elif a == '--addmode':
            k, v = args[i + 1].split('=', 1)
            addmodes[k.lstrip('/')] = int(v, 8)
            i += 2
        elif a == '--materialize':
            k, v = args[i + 1].split('=', 1)
            xt = None
            if '#' in v:
                v, xt = v.split('#', 1)
            materializes[k.lstrip('/')] = dict(local=v, xtmpl=xt)
            i += 2
        elif a == '--adddir':
            adddirs.append(args[i + 1].lstrip('/'))
            i += 2
        elif a == '--stream':
            stream = True
            i += 1
        elif a == '--zopts':
            zopts = args[i + 1]
            i += 2
        elif a == '--keep-tar':
            keep_tar = True
            i += 1
        else:
            i += 1

    print(f"原镜像 : {src_img}")
    print(f"目录树 : {tree}")
    print(f"输出   : {out_img}")
    if replaces:
        print(f"替换   : {list(replaces)}")
    if adddirs:
        print(f"新增目录: {adddirs}")
    if adds:
        print(f"新增文件: {list(adds)}")
    if materializes:
        print(f"实体化   : {list(materializes)}")
    if stream:
        print("tar 模式 : --stream（管道直喂 mkfs，不落盘）")

    # ---- 防护 A（前置，失败得快）：目录树抽查不到 xattr，而原镜像有 ----
    srx = src_root_xattr(src_img)
    if srx and srx > 0 and tree_has_xattr(tree) == 0:
        print()
        print("  ✗ 致命：原镜像根目录带 xattr (%d 字节)，但目录树里抽查不到任何 xattr。" % srx)
        print("     几乎可以肯定是解包时漏了 --xattrs。")
        print("     正确解包: fsck.erofs --extract=<tree> --xattrs <src.img>")
        print("     若强行继续，重建出的镜像会丢掉全部 SELinux 标签。已中止。")
        sys.exit(2)

    meta = walk_meta(src_img)
    print(f"元数据条目: {len(meta)}")
    # 类型分布
    from collections import Counter
    print("  类型分布:", dict(Counter(ft for _, ft, *_ in meta)))
    uidgid = Counter((u, g) for _, _, _, u, g, _, _ in meta)
    print(f"  uid:gid 分布(前8): {uidgid.most_common(8)}")

    meta_map = {rel: (mode, uid, gid, size, link)
                for rel, _ft, mode, uid, gid, size, link in meta}

    # ---- materialize 前置校验（失败得快）----
    for rel in list(materializes):
        if rel not in meta_map:
            print(f"  ✗ 致命：--materialize 目标不存在于源镜像: {rel}")
            sys.exit(3)
        if not os.path.isfile(materializes[rel]['local']):
            print(f"  ✗ 致命：--materialize 本地文件不存在: {materializes[rel]['local']}")
            sys.exit(3)
        if not materializes[rel]['xtmpl']:
            print(f"  ⚠ --materialize {rel} 未给 xattr 模板(#<树内路径>)，"
                  f"SELinux 标签可能丢失")
        if rel in replaces:
            print(f"  ⚠ {rel} 同时在 --replace 与 --materialize 里，以 --materialize 为准")
            del replaces[rel]

    # ---- 统一成一张待写入计划表 (rel, mode, uid, gid, link, local_override, xtmpl) ----
    plan = []
    n_materialize = 0
    for rel, _ft, mode, uid, gid, _size, link in meta:
        if rel in materializes:
            spec = materializes[rel]
            old_kind = mode & 0o170000
            if old_kind != 0o120000:
                print(f"  ⚠ --materialize 目标在源镜像里不是符号链接"
                      f" (mode {oct(mode)})，仍按实体文件写入: {rel}")
            nmode = 0o100000 | addmodes.get(rel, 0o755)
            plan.append((rel, nmode, uid, gid, None, spec['local'],
                         spec['xtmpl'] or rel))
            n_materialize += 1
            continue
        # ★ --replace 的 xattr 模板：默认仍用 rel 自身在树里的 xattr；
        #   但若该文件【只在源镜像里、树里没有】（例如前几轮补丁加进去的
        #   /system/bin/netbpfload、/system/etc/init/wb_diag.rc …），
        #   os.path.join(tree, rel) 不存在 ⇒ xattr 一条都取不到 ⇒ 写出来的文件
        #   变成 u:object_r:unlabeled:s0。实测后果：init 报
        #   "File /system/bin/netbpfload(labeled "u:object_r:unlabeled:s0") has
        #    incorrect label or no domain transition"，exec_start 直接失败，
        #   /sys/fs/bpf 空 ⇒ netd 找不到 BPF 程序 ⇒ netd crashloop。
        #   所以 --replace 也支持 "#<树内模板路径>" 显式指定标签来源。
        xt = (replaces.get(rel, {}).get('xtmpl') or rel) or None
        plan.append((rel, mode, uid, gid, link, None, xt))
    n_new_dir = n_new_file = 0
    for rel in adddirs:
        if rel in meta_map:
            print(f"  ⚠ --adddir 目标已存在，跳过: {rel}")
            continue
        plan.append((rel, 0o040755, 0, 0, None, None, None))
        n_new_dir += 1
    for rel, spec in adds.items():
        if rel in meta_map:
            print(f"  ⚠ --add 目标已存在，改用替换语义: {rel}")
            replaces[rel] = dict(local=spec['local'], xtmpl=spec['xtmpl'])
            continue
        amode = 0o100000 | addmodes.get(rel, 0o644)
        plan.append((rel, amode, 0, 0, None, spec['local'], spec['xtmpl']))
        n_new_file += 1
    plan.sort(key=lambda x: x[0])          # 路径字典序 => 父目录必在子项之前
    print(f"写入计划: {len(plan)} 条 (新增目录 {n_new_dir}, 新增文件 {n_new_file}, "
          f"实体化 {n_materialize})")

    mat_set = set(materializes)
    zargs = zopts.split()

    # ---- 打开 tar 目标：落盘文件 或 直喂 mkfs 的管道 ----
    proc = None
    tar_path = None
    if stream:
        cmd = [MKFS] + zargs + ["-b4096", "--tar=f", "-T", "0", out_img, "/dev/stdin"]
        print("执行(stream):", " ".join(cmd))
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        tf = tarfile.open(fileobj=proc.stdin, mode='w|', format=tarfile.PAX_FORMAT)
    else:
        tar_path = out_img + ".tar"
        tf = tarfile.open(tar_path, 'w', format=tarfile.PAX_FORMAT)

    n_xattr = 0
    n_mat_written = 0
    try:
        for rel, mode, uid, gid, link, local_override, xtmpl in plan:
            local = local_override if local_override else (
                os.path.join(tree, rel) if rel else tree)
            arc = rel if rel else '.'

            ti = tarfile.TarInfo(name=arc)
            ti.mode = mode & 0o7777
            ti.uid = uid
            ti.gid = gid
            ti.mtime = 0
            ti.uname = ''
            ti.gname = ''

            # ---- xattr -> PAX SCHILY.xattr.* (先填, 再写入 tar) ----
            xattr_src = None
            if xtmpl:
                cand = os.path.join(tree, xtmpl)
                if os.path.exists(cand):
                    xattr_src = cand
            if xattr_src is None and local and os.path.exists(local):
                xattr_src = local
            if xattr_src:
                for nm in mac_listxattr(xattr_src):
                    if nm.startswith('com.apple.'):
                        continue
                    val = mac_getxattr(xattr_src, nm)
                    if val is None:
                        continue
                    ti.pax_headers[f'SCHILY.xattr.{nm}'] = val.decode('utf-8', 'surrogateescape')
                    n_xattr += 1

            kind = mode & 0o170000
            if kind == 0o040000:                      # 目录
                ti.type = tarfile.DIRTYPE
                ti.size = 0
                tf.addfile(ti)
            elif kind == 0o120000:                    # 符号链接
                ti.type = tarfile.SYMTYPE
                ti.linkname = link if link is not None else ''
                ti.size = 0
                tf.addfile(ti)
            elif kind == 0o100000:                    # 普通文件
                # ★ 必须用【文件对象流式】喂给 tarfile，不能一次 read() 进内存。
                #   树里有上百 MB 的单文件（libcrypto/dex/apex 等），全读进内存
                #   会触发大量 swap，而 macOS 的 swap 卷与 Data 卷共用同一个
                #   APFS 容器 —— 实测会把可用空间瞬间吃光，导致构建中途 ENOSPC。
                if rel in mat_set:
                    src = materializes[rel]['local']
                    old_link = meta_map.get(rel, (0, 0, 0, 0, None))[4]
                    print(f"  ★ 实体化 {rel}: {os.path.getsize(src)} 字节"
                          f"  (原软链 -> {old_link})")
                    n_mat_written += 1
                elif rel in replaces:
                    src = replaces[rel]['local']
                    old = meta_map.get(rel, (0, 0, 0, 0, None))[3]
                    print(f"  ★ 替换 {rel}: {os.path.getsize(src)} 字节 (原 {old})")
                else:
                    src = local
                ti.type = tarfile.REGTYPE
                ti.size = os.path.getsize(src)
                with open(src, 'rb') as f:
                    tf.addfile(ti, f)
            else:
                print(f"  ⚠ 跳过特殊文件 {rel} (mode {oct(mode)})")
                continue

    except BrokenPipeError:
        print("  ✗ mkfs.erofs 提前退出（管道断开）—— 见下方 STDERR")
    finally:
        try:
            tf.close()
        except Exception:
            pass

    if stream:
        try:
            proc.stdin.close()
        except Exception:
            pass
        out, err = proc.communicate()
        print(out.decode('utf-8', 'replace')[-2000:])
    else:
        print(f"tar 写出: {tar_path} ({os.path.getsize(tar_path)} 字节), xattr 记录 {n_xattr} 条")

    # ---- 防护 B（后置，兜底）：源镜像有 xattr，但一条都没写进 tar ----
    if n_xattr == 0:
        srx2 = src_root_xattr(src_img)
        if srx2 and srx2 > 0:
            print()
            print("  ✗ 致命：源镜像根目录带 xattr (%d 字节)，但 tar 里一条 xattr 都没有。" % srx2)
            print("     正确解包: fsck.erofs --extract=<tree> --xattrs <src.img>")
            print("     若强行继续，重建出的镜像会丢掉全部 SELinux 标签。已中止。")
            if tar_path:
                try:
                    os.remove(tar_path)
                except OSError:
                    pass
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
            sys.exit(2)
        print("  ⚠ 源镜像与目录树都没有 xattr —— 继续（某些小分区确实无 xattr）")

    # ---- 生成 EROFS ----
    #   压缩参数由 --zopts 指定, 必须与【原镜像】的 feature_incompat 对应:
    #     incompat=0x1 (仅 LZ4_0PADDING)          -> "-zlz4hc,level=9"
    #     incompat=0x3 (再加 BIG_PCLUSTER,16KB簇) -> "-zlz4hc,level=9 -C16384"
    if stream:
        if proc.returncode != 0:
            print("STDERR:", err.decode('utf-8', 'replace')[-3000:])
            sys.exit(proc.returncode)
        print(f"完成: {out_img}  {os.path.getsize(out_img)} 字节 "
              f"(实体化 {n_mat_written} 条)")
    else:
        cmd = [MKFS] + zargs + ["-b4096", "--tar=f", "-T", "0", out_img, tar_path]
        print("执行:", " ".join(cmd))
        r = subprocess.run(cmd, capture_output=True, text=True)
        print(r.stdout[-2000:])
        if r.returncode != 0:
            print("STDERR:", r.stderr[-3000:])
            sys.exit(r.returncode)
        print(f"完成: {out_img}  {os.path.getsize(out_img)} 字节 "
              f"(实体化 {n_mat_written} 条)")

    # ---- 清理中间 tar ----
    #   tar 中间件和成品镜像差不多大（system_ext 的 tar 有 1.68 GB），
    #   而 ~/Documents 长期处于 99% 占用。默认删掉，需要留档用 --keep-tar。
    if tar_path is None:
        print("（--stream 模式：没有中间 tar 需要清理）")
    elif keep_tar:
        print(f"保留中间 tar（--keep-tar）: {tar_path}")
    else:
        try:
            sz = os.path.getsize(tar_path)
            os.remove(tar_path)
            print(f"已删除中间 tar: {tar_path} ({sz} 字节)")
        except Exception as e:
            # 不能只 catch OSError：本机存在删除拦截 hook，抛出的异常类型不定，
            # 漏掉会导致整个脚本以 rc=1 结束，把"镜像其实已构建成功"误报成失败。
            print(f"⚠ 中间 tar 删除失败（镜像本身已生成，可手动删）: {e}")
            print(f"    手动清理: /bin/rm -f {tar_path}")


if __name__ == '__main__':
    main()
