#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apex_rebuild_all.py —— 批量重建 system/apex 下所有 EROFS payload（去掉 big-pcluster）并重签

为什么
======
Android 16 的 APEX payload 用 mkfs.erofs 的 big-pcluster（16 KB 物理簇）构建，
超级块 feature_incompat = 0x3。X2 Pro 的 4.19.157 内核只认 0x1，挂载返回 -EINVAL
⇒ apexd --bootstrap 失败 ⇒ reboot,bootloader ⇒ 开机 54 秒掉 fastboot。
修法 = 把每个 payload 重建为 incompat=0x1，再用自备密钥重签 AVB 并替换包内 apex_pubkey。

用法
====
  apex_rebuild_all.py [--src-dir DIR] [--out-dir DIR] [--work-root DIR]
                      [--key FILE] [--only NAME[,NAME...]] [--force]
                      [--install] [--no-verify] [--jobs N]

  --install   重建并校验通过后，把新 APEX 覆盖进 <tree>/system/apex/
              （覆盖前请确认 _apexorig 里已有硬链接备份）

三层校验（每个包都做，任一层不过就标记失败）
============================================
  L1  包内自检：由 apex_rebuild.py 完成
      payload 首个条目 + STORED + 数据偏移 %4096==0 + incompat==0x1 +
      末尾 AVBf + apex_pubkey 已替换 + 条目数不变
  L2  元数据逐条目比对：walk_meta(原 payload) vs walk_meta(新 payload)
      比 (相对路径, 类型, mode, uid, gid, 非目录的 size, 符号链接目标) 全集
  L3  内容 + xattr 逐文件比对：
      把新 payload 解包成 tree2，与构建用的 tree 比
      (路径集合, 普通文件 md5, 符号链接目标, security.selinux 等 xattr)

设计约束（踩过的坑）
====================
  * 构建产物一律写项目目录，不要用 /tmp（本机会被清）。
  * 非 EROFS 的 payload（如 com.android.apex.cts.shim）跳过——它本来就不受影响。
  * 逐包串行跑（mkfs.erofs 是 CPU 密集），--jobs>1 意义不大但保留。
"""

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PY = sys.executable
REBUILD_ONE = os.path.join(HERE, "apex_rebuild.py")
EROFSTOOL = "/Users/skeletondie/.workbuddy-ai/tools/erofs/erofs-utils/1.9.4/bin"
FSCK = os.path.join(EROFSTOOL, "fsck.erofs")
DEFAULT_KEY = os.path.join(PROJ, "07-重建", "_avb", "apex_key.pem")
DEFAULT_SRC = os.path.join(PROJ, "07-重建", "_apexorig")
DEFAULT_OUT = os.path.join(PROJ, "07-重建", "_apexout")
DEFAULT_WORK = os.path.join(PROJ, "07-重建", "_apexwork")
L3_ROOT = os.path.join(PROJ, "07-重建", "_l3verify")
TREE = os.path.join(PROJ, "07-重建", "tree")

sys.path.insert(0, HERE)
import erofs_rebuild as ER          # noqa: E402  walk_meta / mac_listxattr / mac_getxattr


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def erofs_sb(path):
    """读 EROFS 超级块 → (compat, incompat, blkszbits, blocks)；非 EROFS 返回 None。"""
    with open(path, "rb") as f:
        f.seek(1024)
        b = f.read(96)
    if len(b) < 96:
        return None
    if struct.unpack_from("<I", b, 0)[0] != 0xE0F5E1E2:
        return None
    f12 = struct.unpack_from("<IIIBBHQQIIII", b, 0)
    return f12[2], struct.unpack_from("<I", b, 80)[0], f12[3], f12[9]


def md5_file(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            c = f.read(1 << 20)
            if not c:
                break
            h.update(c)
    return h.hexdigest()


def read_payload(apex_path, out_path):
    """从 APEX 里取出 apex_payload.img 写到 out_path。"""
    with zipfile.ZipFile(apex_path) as z:
        names = [n for n in z.namelist() if n.endswith("apex_payload.img")]
        if not names:
            return None
        data = z.read(names[0])
    with open(out_path, "wb") as f:
        f.write(data)
    return out_path


def payload_entry_name(apex_path):
    with zipfile.ZipFile(apex_path) as z:
        names = [n for n in z.namelist() if n.endswith("apex_payload.img")]
        return names[0] if names else None


def apex_entry_count(apex_path):
    with zipfile.ZipFile(apex_path) as z:
        return len(z.namelist())


def apex_pubkey(apex_path):
    with zipfile.ZipFile(apex_path) as z:
        names = [n for n in z.namelist() if n.endswith("apex_pubkey")]
        return z.read(names[0]) if names else None


# --------------------------------------------------------------------------- #
# L2 元数据比对
# --------------------------------------------------------------------------- #
def norm_meta(img):
    """walk_meta 归一化 → {relpath: (type, mode, uid, gid, size_or_link)}"""
    out = {}
    for rel, ftype, mode, uid, gid, size, link in ER.walk_meta(img):
        kind = mode & 0o170000
        if kind == 0o040000:          # 目录: size 由 mkfs 决定，不参与比对
            val = (ftype, mode, uid, gid, None)
        elif kind == 0o120000:        # 符号链接
            val = (ftype, mode, uid, gid, link)
        else:
            val = (ftype, mode, uid, gid, size)
        out[rel] = val
    return out


def cmp_meta(orig_img, new_img):
    a, b = norm_meta(orig_img), norm_meta(new_img)
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    diff = []
    for k in sorted(set(a) & set(b)):
        if a[k] != b[k]:
            diff.append((k, a[k], b[k]))
    return {"n_orig": len(a), "n_new": len(b),
            "only_orig": only_a[:20], "only_new": only_b[:20],
            "n_only_orig": len(only_a), "n_only_new": len(only_b),
            "n_diff": len(diff), "diff": diff[:20],
            "ok": not only_a and not only_b and not diff}


# --------------------------------------------------------------------------- #
# L3 内容 + xattr 比对
# --------------------------------------------------------------------------- #
def scan_tree(root):
    """{relpath: (kind, payload, xattrs)}；payload = md5(普通文件) / link 目标 / None"""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for nm in [None] + sorted(filenames):
            p = dirpath if nm is None else os.path.join(dirpath, nm)
            rel = os.path.relpath(p, root)
            if rel == ".":
                rel = ""
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if nm is None:
                kind = "dir"
                payload = None
            elif os.path.islink(p):
                kind = "lnk"
                payload = os.readlink(p)
            else:
                kind = "file"
                payload = md5_file(p)
            xa = {}
            for x in ER.mac_listxattr(p):
                if x.startswith("com.apple."):
                    continue
                v = ER.mac_getxattr(p, x)
                if v is not None:
                    xa[x] = v.decode("utf-8", "surrogateescape")
            out[rel] = (kind, payload, tuple(sorted(xa.items())))
    return out


def cmp_tree(a, b):
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    diff = []
    for k in sorted(set(a) & set(b)):
        if a[k] != b[k]:
            ka, pa, xa = a[k]
            kb, pb, xb = b[k]
            why = []
            if ka != kb:
                why.append("kind %s->%s" % (ka, kb))
            if pa != pb:
                why.append("payload %r->%r" % (
                    (pa or "")[:24], (pb or "")[:24]))
            if xa != xb:
                why.append("xattr %s->%s" % (dict(xa), dict(xb)))
            diff.append((k, "; ".join(why)))
    return {"n_orig": len(a), "n_new": len(b),
            "only_orig": only_a[:20], "only_new": only_b[:20],
            "n_only_orig": len(only_a), "n_only_new": len(only_b),
            "n_diff": len(diff), "diff": diff[:20],
            "ok": not only_a and not only_b and not diff}


# --------------------------------------------------------------------------- #
# 单个 APEX 的处理
# --------------------------------------------------------------------------- #
def process(name, src, out, work_root, key, force, do_verify, log):
    t0 = time.time()
    r = {"name": name, "src": src, "out": out, "ok": False, "skipped": False,
         "errors": [], "l1": None, "l2": None, "l3": None,
         "orig_payload": None, "new_payload": None, "orig_apex": None,
         "new_apex": None, "secs": 0}

    # ---- 是否 EROFS ----
    tmp = os.path.join(work_root, "_probe.img")
    os.makedirs(work_root, exist_ok=True)
    if read_payload(src, tmp) is None:
        r["skipped"] = True
        r["errors"].append("APEX 里没有 apex_payload.img")
        return r
    sb = erofs_sb(tmp)
    r["orig_payload"] = os.path.getsize(tmp)
    r["orig_apex"] = os.path.getsize(src)
    if sb is None:
        r["skipped"] = True
        r["errors"].append("payload 不是 EROFS（本来就与 big-pcluster 无关），跳过")
        return r
    if sb[1] == 0x1:
        r["skipped"] = True
        r["errors"].append("原 payload 已是 incompat=0x1，跳过")
        return r

    # ---- 重建 ----
    if force or not os.path.exists(out):
        log("  [重建] %s  (原 payload %d B, incompat=%#x)"
            % (name, r["orig_payload"], sb[1]))
        p = subprocess.run(
            [PY, REBUILD_ONE, src, out, "--key", key,
             "--work", os.path.join(work_root, name), "--keep"],
            capture_output=True, text=True)
        if p.returncode != 0:
            r["errors"].append("apex_rebuild.py rc=%d\n%s\n%s"
                               % (p.returncode, p.stdout[-1500:], p.stderr[-1500:]))
            r["secs"] = round(time.time() - t0, 1)
            return r
        r["l1"] = p.stdout
    else:
        log("  [跳过重建] %s（输出已存在，--force 可强制重做）" % name)

    if not os.path.exists(out):
        r["errors"].append("输出不存在: " + out)
        return r
    r["new_apex"] = os.path.getsize(out)

    # ---- 取出新 payload ----
    nw = os.path.join(work_root, name, "payload_from_new_apex.img")
    read_payload(out, nw)
    r["new_payload"] = os.path.getsize(nw)
    sbn = erofs_sb(nw)
    if sbn is None or sbn[1] != 0x1:
        r["errors"].append("新 payload incompat=%s，应为 0x1"
                           % (None if sbn is None else hex(sbn[1])))
        return r

    # ---- 原 payload（用于比对）----
    op = os.path.join(work_root, name, "payload_orig.img")
    if not os.path.exists(op):
        read_payload(src, op)

    if do_verify:
        # ---- L2 元数据 ----
        m = cmp_meta(op, nw)
        r["l2"] = m
        if not m["ok"]:
            r["errors"].append("L2 元数据不一致: 仅原 %d / 仅新 %d / 差异 %d"
                               % (m["n_only_orig"], m["n_only_new"], m["n_diff"]))
            for d in m["diff"][:5]:
                r["errors"].append("    %s: %s -> %s" % d)
        # ---- L3 内容 + xattr ----
        tree = os.path.join(work_root, name, "tree")
        # ★ 不做任何删除：本机有「单轮批量删除 >50 文件需确认」的保护钩子，
        #   会直接掐断整条流水线。每个包解到独立目录并保留，用 --overwrite 覆盖重跑。
        tree2 = os.path.join(L3_ROOT, name)
        os.makedirs(tree2, exist_ok=True)
        q = subprocess.run([FSCK, "--extract=" + tree2, "--xattrs",
                            "--overwrite", nw],
                           capture_output=True, text=True)
        if q.returncode != 0:
            q = subprocess.run([FSCK, "--extract", tree2, "--xattrs",
                                "--overwrite", nw],
                               capture_output=True, text=True)
        if q.returncode != 0:
            r["errors"].append("fsck.erofs 解包新 payload 失败 rc=%d: %s"
                               % (q.returncode, q.stderr[-500:]))
        else:
            t = cmp_tree(scan_tree(tree), scan_tree(tree2))
            r["l3"] = t
            if not t["ok"]:
                r["errors"].append("L3 内容/xattr 不一致: 仅原 %d / 仅新 %d / 差异 %d"
                                   % (t["n_only_orig"], t["n_only_new"], t["n_diff"]))
                for d in t["diff"][:5]:
                    r["errors"].append("    %s: %s" % d)

    # ---- 条目数与 pubkey 尺寸 ----
    try:
        if apex_entry_count(out) != apex_entry_count(src):
            r["errors"].append("条目数不一致")
        if len(apex_pubkey(out)) != len(apex_pubkey(src)):
            r["errors"].append("apex_pubkey 尺寸不一致")
    except Exception as e:
        r["errors"].append("zip 检查异常: %s" % e)

    r["ok"] = not r["errors"]
    r["secs"] = round(time.time() - t0, 1)
    return r


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--src-dir", default=DEFAULT_SRC)
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    ap.add_argument("--work-root", default=DEFAULT_WORK)
    ap.add_argument("--key", default=DEFAULT_KEY)
    ap.add_argument("--only", default=None, help="只处理这些包名，逗号分隔")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--install", action="store_true",
                    help="校验通过后覆盖进 tree/system/apex/")
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--report", default=None, help="JSON 报告输出路径")
    args = ap.parse_args()

    def log(s):
        print(s, flush=True)

    os.makedirs(args.out_dir, exist_ok=True)
    os.makedirs(args.work_root, exist_ok=True)

    names = sorted(f for f in os.listdir(args.src_dir) if f.endswith(".apex"))
    if args.only:
        want = set(x.strip() for x in args.only.split(",") if x.strip())
        names = [n for n in names if n[:-5] in want or n in want]
    log("=" * 78)
    log("批量 APEX 重建  共 %d 个包" % len(names))
    log("  源  : %s" % args.src_dir)
    log("  出  : %s" % args.out_dir)
    log("  密钥: %s" % args.key)
    log("=" * 78)

    results = []
    for i, fn in enumerate(names, 1):
        name = fn[:-5]
        log("[%d/%d] %s" % (i, len(names), name))
        r = process(name, os.path.join(args.src_dir, fn),
                    os.path.join(args.out_dir, fn), args.work_root,
                    args.key, args.force, not args.no_verify, log)
        if r["skipped"]:
            log("  ⊘ 跳过: %s" % "; ".join(r["errors"]))
        elif r["ok"]:
            log("  ✓ 通过  payload %d → %d B (%+.1f%%)  APEX %d → %d B  用时 %.1fs"
                % (r["orig_payload"], r["new_payload"],
                   100.0 * (r["new_payload"] / r["orig_payload"] - 1),
                   r["orig_apex"], r["new_apex"], r["secs"]))
            if r["l2"]:
                log("      L2 元数据 %d 条，0 差异" % r["l2"]["n_orig"])
            if r["l3"]:
                log("      L3 内容+xattr %d 条，0 差异" % r["l3"]["n_orig"])
        else:
            log("  ✗ 失败  (%d 个问题) 用时 %.1fs" % (len(r["errors"]), r["secs"]))
            for e in r["errors"][:12]:
                log("      " + str(e).replace("\n", "\n      ")[:400])
        results.append(r)

    # ---- 汇总 ----
    ok = [r for r in results if r["ok"]]
    skip = [r for r in results if r["skipped"]]
    bad = [r for r in results if not r["ok"] and not r["skipped"]]
    log("=" * 78)
    log("汇总: 通过 %d / 跳过 %d / 失败 %d" % (len(ok), len(skip), len(bad)))
    if bad:
        log("失败清单: %s" % ", ".join(r["name"] for r in bad))
    tot_o = sum(r["orig_payload"] for r in ok)
    tot_n = sum(r["new_payload"] for r in ok)
    if ok:
        log("payload 总量: %d → %d B (%+.1f%%, %+.1f MB)"
            % (tot_o, tot_n, 100.0 * (tot_n / tot_o - 1),
               (tot_n - tot_o) / 1048576.0))
    log("APEX 总量   : %d → %d B"
        % (sum(r["orig_apex"] for r in ok), sum(r["new_apex"] for r in ok)))

    if args.report:
        with open(args.report, "w") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
        log("报告: " + args.report)

    # ---- 安装 ----
    if args.install:
        if bad:
            log("⚠ 有失败项，为安全起见【不执行安装】。修好后再跑 --install")
            return 1
        dst_dir = os.path.join(TREE, "system", "apex")
        n = 0
        for r in ok:
            dst = os.path.join(dst_dir, r["name"] + ".apex")
            # ★ 不能直接 shutil.copy2 覆盖目标：
            #   目标文件与 _apexorig/ 里的备份是【硬链接】，copy2 是"打开目标并覆写"，
            #   会把备份内容一起改掉（备份就废了，下次跑还会把新包当成原包）。
            #   先写 .new 再 os.replace —— rename 换的是目录项，旧 inode 及其
            #   其它硬链接都保持原样。
            tmp = dst + ".new"
            shutil.copy2(r["out"], tmp)
            os.replace(tmp, dst)
            n += 1
            log("  安装 %s -> %s" % (r["name"] + ".apex", dst))
        log("已安装 %d 个新 APEX 到 %s" % (n, dst_dir))

        # ---- ★ 收尾：补 APK Signature Scheme v2 签名 + SELinux 标签 ----
        # 2026-10-02 新增（第 6 号根因，血的教训）：
        #   apex_rebuild.py 重打 zip 时会把原包的 APK Signing Block 整块丢掉，
        #   而 A16 的 PackageManagerService.scanApexPackages 会对每个
        #   /system/apex/*.apex 调 ApkSignatureVerifier.verify(path, 2) ⇒
        #     PackageManagerException: No APK Signature Scheme v2 signature in package
        #       Caused by: SignatureNotFoundException: No APK Signing Block before ZIP Central Directory
        #   ⇒ system_server 在 PackageManagerService.<init> FATAL ⇒ 整机 boot loop。
        #   （实测 33 个包里 32 个缺签名块；唯一幸存的 cts.shim 恰好没被重建。）
        #   同时 shutil.copy2 不带 xattr ⇒ security.selinux 也会丢（enforcing 下 unlabeled）。
        #   两者都由 apex_v2_sign.py 一并补齐，且该脚本幂等，重复跑安全。
        signer = os.path.join(HERE, "apex_v2_sign.py")
        if os.path.exists(signer):
            log("")
            log("---- 收尾：补 APK v2 签名块 + SELinux 标签 ----")
            rc2 = subprocess.call([PY, signer, "--dir", dst_dir])
            if rc2 != 0:
                log("⚠ apex_v2_sign.py 失败 rc=%d" % rc2)
                log("⚠ 这个 tree 【不要】拿去构建镜像 —— 会 boot loop")
                return 1
        else:
            log("⚠ 找不到 apex_v2_sign.py，跳过补签")
            log("⚠ 直接用这个 tree 构建的镜像会在 system_server 阶段 FATAL")
            return 1
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
