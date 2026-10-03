# 根因（第 5 号）：`--replace` 丢掉 SELinux xattr → `netbpfload` 变 `unlabeled` → bpfloader 起不来 → netd crashloop

- **日期**：2026-10-02
- **机型**：OPPO Find X2 Pro（PDEM30 / kona / SM8250），内核 4.19.157-color597
- **系统**：ColorOS 16 移植（Android 16 / SDK 36，25Q2 基线）
- **状态**：✅ 已定位、已修、已加断言；v5 镜像待刷机验证
- **发现路径**：v4（修掉 netd 内核 5.4 门限）刷入后，netd 不再因「平台不支持」abort，
  但暴露出**下一层**故障。

---

## 1. 现象（v4 实测）

netd 仍然 SIGABRT，但**报错换了**（说明 5.4 门限确实已绕过）：

```
I NetdUpdatable: libnetd_updatable_init: Initializing
E NetdUpdatable: libnetd_updatable_init: Failed: (2) [No such file or directory]
                 : Failed to get program from /sys/fs/bpf/netd_shared/prog_netd_skfilter_allowlist_xtbpf
F libc         : Fatal signal 6 (SIGABRT) ... (netd)
  #01 pc 0xa668  /apex/com.android.tethering/lib64/libnetd_updatable.so (libnetd_updatable_init.cfi+600)
```

设备侧：
```
$ adb shell ls -la /sys/fs/bpf/
total 0        ← 全空！一个 BPF 程序都没加载
$ adb shell getprop init.svc.wb_netbpfload
(空)           ← 服务根本没启动过
```

---

## 2. dmesg 给出确切原因

```
[    3.665709] init: Parsing file /system/etc/init/wb_netbpfload.rc...
[    6.143298] init: processing action (load-bpf-programs) from (/system/etc/init/hw/init.rc:1856)
[    6.144263] init: Command 'exec_start wb_netbpfload' action=load-bpf-programs
              (/system/etc/init/hw/init.rc:1857) took 0ms and failed:
              Could not start exec service:
              File /system/bin/netbpfload(labeled "u:object_r:unlabeled:s0")
              has incorrect label or no domain transition from u:r:init:s0 to
              another SELinux domain defined. Have you configured your service
              correctly?
[    6.144283] init: processing action (bpf-progs-loaded) from (/system/etc/init/hw/init.rc:1859)
[    6.144599] init: starting service 'netd'...
[    6.785643] init: Service 'netd' (pid 1088) received signal 6
[    6.785957] init: [PHOENIX] phx_is_bootup_critical_service: bootup critical service netd crashed
```

**`/system/bin/netbpfload` 的 SELinux 标签是 `u:object_r:unlabeled:s0`。**

> ⚠ 关键点：这个错误**在 permissive 下照样报**。init 的 `exec_start` 会先做
> 「文件标签 + 域转换」检查，失败就拒绝启动服务，与当前 enforcing/permissive 无关。
> 所以「已经 permissive 了为什么还拦」这个直觉是错的。

连锁反应：
```
netbpfload 起不来
  ⇒ /sys/fs/bpf 空、bpf.progs_loaded 永不置位
  ⇒ netd 的 libnetd_updatable_init 找不到 BPF 程序
  ⇒ netd SIGABRT（还是 0xa668 那个 abort 出口，只是前面的原因换了）
  ⇒ Phoenix 判 netd 为 bootup critical ⇒ 重启
  ⇒ boot loop
```

---

## 3. 根因：`erofs_rebuild.py --replace` 的 xattr 来源

`erofs_rebuild.py` 的写入计划里，每个条目的 xattr 来源是：

```python
# 原代码（erofs_rebuild.py 第 354-361 行附近）
xattr_src = None
if xtmpl:                                  # xtmpl 默认 = rel 自己
    cand = os.path.join(tree, xtmpl)
    if os.path.exists(cand):
        xattr_src = cand                   # ← 优先「树里的同路径」
if xattr_src is None and local and os.path.exists(local):
    xattr_src = local                      # ← 否则本地文件（--replace 时= tree/rel，同样不存在）
```

而 `--replace` 的目标里有 **4 个文件只在【源镜像】里有、`07-重建/tree` 里根本没有**
（它们是前几轮补丁加进去的）：

| 文件 | src(v1) 标签 | v4 实际标签 |
|---|---|---|
| `/system/bin/netbpfload` | `u:object_r:bpfloader_exec:s0` | **（无 xattr）** |
| `/system/etc/init/wb_netbpfload.rc` | `u:object_r:system_file:s0` | **（无 xattr）** |
| `/system/etc/init/wb_diag.rc` | `u:object_r:system_file:s0` | **（无 xattr）** |
| `/system/etc/wb_diag.sh` | `u:object_r:system_file:s0` | **（无 xattr）** |

⇒ 树里找不到模板、本地文件也没带 xattr ⇒ **一条 xattr 都写不进 tar**
⇒ 内核按「无标签」处理 ⇒ `unlabeled`。

对照：`init.rc` / `apexd.rc` 因为**在树里存在**，模板解析成功，标签正常。
`linker64` / `libc.so` 等 15 个实体化条目因为构建脚本**显式给了 `#模板`**，也正常。

### 为什么 v1 是对的、v3/v4 是错的

v1（`system_diag.img`）由更早的 `build_system_diag.sh` 构建，那批文件是用
带模板的 `--add` 写进去的，标签正确；此后 `--replace` 一直**保留源镜像的元数据**……
直到 v3/v4 改用「树内容 + `--replace`」的组合，才把 xattr 这条链路断开。

**这个 bug 从 v3 起就存在，但一直没被发现** —— 因为 v3 卡在 SELinux 三重障碍
（`apexd-bootstrap` 根本没跑到），根本没机会走到 bpfloader 这一步。

---

## 4. 修法

### 4.1 `erofs_rebuild.py`：`--replace` 支持 xattr 模板

```python
# 新语法
--replace <镜像内路径>=<本地新文件>[#<模板路径(树内相对路径)>]

# 解析
if a == '--replace':
    k, v = args[i + 1].split('=', 1)
    xt = None
    if '#' in v:
        v, xt = v.split('#', 1)
    replaces[k.lstrip('/')] = dict(local=v, xtmpl=xt)

# 计划表：显式模板优先，否则仍用 rel 自身
xt = (replaces.get(rel, {}).get('xtmpl') or rel) or None
plan.append((rel, mode, uid, gid, link, None, xt))
```

同时把 `replaces[rel]` 的三处取值改成 `replaces[rel]['local']`（值从 str 变 dict）。

### 4.2 `build_system_diag_v3.sh`：给每个 `--replace` 补模板

```bash
--replace "/system/etc/init/hw/init.rc=$DIAG/init.rc#system/etc/init/hw/init.rc" \
--replace "/system/etc/init/apexd.rc=$DIAG/apexd.rc#system/etc/init/hw/init.rc" \
--replace "/system/etc/init/wb_diag.rc=$DIAG/wb_diag.rc#system/etc/init/hw/init.rc" \
--replace "/system/etc/wb_diag.sh=$DIAG/wb_diag.sh#system/etc/init/hw/init.rc" \
--replace "/system/bin/netbpfload=$NEW/netbpfload#system/bin/bpfloader" \
--replace "/system/etc/init/wb_netbpfload.rc=$NEW/wb_netbpfload.rc#system/etc/init/hw/init.rc" \
```

模板选择：
- 可执行文件 → `system/bin/bpfloader`（`u:object_r:bpfloader_exec:s0`）
- `/system/etc/init/*.rc`、`/system/etc/*.sh` → `system/etc/init/hw/init.rc`（`system_file`）

### 4.3 ★ 新增「SELinux 标签断言」（防止再犯）

`build_system_diag_v3.sh` 的第 4 步反抽断言里新增 **F 段**：对 10 个关键路径
逐个用 `fsck.erofs --extract=<tmp> --path=/<p> --xattrs` 抽出真实 xattr，
校验等于期望标签，**不通过就 `exit 1`（不许刷）**。

```
  SELinux 标签: 10/10 通过
```

断言清单：
```
system/bin/netbpfload            u:object_r:bpfloader_exec:s0
system/bin/bpfloader             u:object_r:bpfloader_exec:s0
system/etc/init/hw/init.rc       u:object_r:system_file:s0
system/etc/init/apexd.rc         u:object_r:system_file:s0
system/etc/init/wb_diag.rc       u:object_r:system_file:s0
system/etc/init/wb_netbpfload.rc u:object_r:system_file:s0
system/etc/wb_diag.sh            u:object_r:system_file:s0
system/etc/wb_apexd_wrap.sh      u:object_r:system_file:s0
system/bin/linker64              u:object_r:system_linker_exec:s0
system/lib64/libc.so             u:object_r:system_lib_file:s0
```

---

## 5. 构建产物

- `02-移植素材/a16_patched/system_diag5.img`（1,072,717,824 B，sha256 `f791fdba…`）
- 断言全绿：实体化 15/15、init.rc 面包屑 16 条、reboot_on_failure 全注释、
  netbpfload 86432 B、**SELinux 标签 10/10**

---

## 6. 经验教训（可复用）

1. **EROFS 里「内容」和「元数据」是两条独立的链路**。
   换内容（`--replace`）时必须显式确认**标签也跟上了**，否则会静默变 `unlabeled`。
2. **`unlabeled` 的错误在 permissive 下照样报** —— 别拿「已经 permissive」当免死金牌。
   init 的 `exec_start` 检查的是「文件标签 + 域转换」，与全局 enforcing 状态无关。
3. **构建脚本必须有「反抽真实 xattr」的断言**，只对比内容/大小/权限是查不出这个问题的。
4. **诊断顺序**：先看 `dmesg | grep -i 'init: Command'`（init 内建命令的失败原因
   只在 dmesg 里，logcat 没有），再看服务自己的 logcat。
5. `fsck.erofs --extract=<输出文件路径> --path=/x/y --xattrs <img>` 可以**只抽一个文件
   并带上 xattr**（注意：给了 `--path` 时 `--extract` 的值是**文件路径**不是目录，
   且目标不能预先存在）。
6. **BSD grep 不支持 `\|` 交替**，`grep -n "a\|b"` 会**静默返回空** ——
   用 `grep -E "a|b"`。本次差点因此误判「netd 日志里没有 NetdUpdatable 记录」。
