# 根因已定位：SELinux 三重障碍阻断 `apexd-bootstrap`

> 时间：2026-10-02 00:20
> 状态：**根因已用实测证据定位，修复方案已实施并在验证中**
> 取代此前的所有"卡点猜测"

---

## 一、决定性证据链

### 1.1 面包屑：init 确实跑到了 `exec_start apexd-bootstrap` **之后**

从设备 `/metadata/wbdiag/` 实测读出（TWRP，v3 镜像）：

```
存在  00_selinux_enforce  (值=1)
存在  10_earlyinit        (值=1)
存在  18_pre_apexdbootstrap  (值=1)
存在  20_post_apexdbootstrap (值=1)
缺失  21a_apexdbootstrap_running
缺失  21b_apexdbootstrap_stopped
缺失  22_apexd_status_ready
缺失  23_wb_probe_stopped
缺失  30_on_init
缺失  31a_boringssl64_running
缺失  31b_boringssl64_stopped
缺失  40_on_late_init
缺失  42_on_early_fs
缺失  44_on_post_fs
缺失  46_on_late_fs
缺失  48_on_post_fs_data
缺失  50_on_boot
```

对照 `init.rc` 的实际顺序：

```rc
151:    write /metadata/wbdiag/18_pre_apexdbootstrap 1
152:    exec_start apexd-bootstrap
153:    write /metadata/wbdiag/20_post_apexdbootstrap 1
155:    exec_start wb_probe
156:    perform_apex_config --bootstrap
...
192: on init
193:    write /metadata/wbdiag/30_on_init 1
```

**结论**：
- `20_` 存在 ⇒ init **越过了** 152 行的 `exec_start apexd-bootstrap`；
- `21a_apexdbootstrap_running` 缺失 ⇒ `on property:init.svc.apexd-bootstrap=running`
  **从未触发** ⇒ service **从未进入 running 状态**；
- `30_on_init` 缺失 ⇒ init **没能走到 `on init`**，卡在 153~192 之间
  （嫌疑最大：156 行 `perform_apex_config --bootstrap`，其内部会等待 apexd 就绪）。

### 1.2 `apexd_bootstrap.log` 不存在 —— wrapper 一行都没跑

```
$ cat /metadata/wbdiag/apexd_bootstrap.log
cat: /metadata/wbdiag/apexd_bootstrap.log: No such file or directory
```

⇒ `service apexd-bootstrap /system/bin/sh /system/etc/wb_apexd_wrap.sh`
**根本没有启动起来**（不是"跑了但没写日志"，是"没跑"）。

### 1.3 实体化 100% 生效 —— 排除镜像问题

在 TWRP 里挂载真正的 system 分区（`/dev/block/mapper/system` → dm-13，
OPPO 的 `system` 分区是根文件系统容器，真正的 system 在 `/system/` 子目录下）：

```
/mnt/a16sys/system/bin/linker64     2287376   ← 实体化生效 ✓
/mnt/a16sys/system/bin/linker       1795856   ← 实体化生效 ✓
/mnt/a16sys/system/bin/sh            302200
/mnt/a16sys/system/bin/toybox        578448
/mnt/a16sys/system/bin/apexd        1011760
/mnt/a16sys/system/lib64/libc.so    1337968   ← 实体化生效 ✓
/mnt/a16sys/system/lib64/libm.so     232688   ← 实体化生效 ✓
/mnt/a16sys/system/lib64/libdl.so     13976   ← 实体化生效 ✓

ro.build.version.release = 16
ro.build.version.sdk     = 36
```

⇒ 刷入成功、实体化 15/15 生效。

### 1.4 `sh` 本身完全能跑 —— 排除二进制与库问题

在设备上直接用 A16 自己的 linker 跑 A16 自己的 sh：

```
$ /mnt/a16sys/system/bin/linker64 /mnt/a16sys/system/bin/sh -c 'echo WB_SH_OK'
WB_SH_OK
```

⇒ `sh` 能 exec、`libc.so` 能找到、linker 工作正常。
**⇒ 所以 init 启动时 `sh` 起不来，不是二进制/库/linker 的问题。**

---

## 二、根因：SELinux 三重障碍

从 `/system/etc/selinux/plat_sepolicy.cil` 与 `plat_file_contexts` 实测提取：

### 障碍 1（主因）：`apexd` 域**没有** `shell_exec` 的 execute 权限

`apexd-bootstrap` service 定义（v3）**没有 `seclabel`**，因此按
`plat_service_contexts` 运行在 **`apexd` 域**。而 `apexd` 域的全部 execute 权限只有：

```
(allow apexd apexd_exec                 (file (... execute ...)))
(allow apexd system_bootstrap_lib_file  (file (... execute ...)))
(allow apexd toolbox_exec               (file (... execute ...)))
(allow apexd derive_classpath_exec      (file (... execute ...)))
```

**`(allow apexd shell_exec ...)` —— 不存在。**
**`(allow apexd system_file ...)` —— 不存在。**

而文件标签是：

```
/system/bin/sh      --  u:object_r:shell_exec:s0     ← apexd 域无权 exec
/system/bin/toybox  --  u:object_r:toolbox_exec:s0   ← apexd 域【可以】exec
```

⇒ **init 执行 `exec_start apexd-bootstrap` 时，SELinux 直接拒绝 exec `/system/bin/sh`**
⇒ service 启动失败 ⇒ wrapper 一行没跑 ⇒ 日志当然不存在。

**这也解释了 v1/v2 的面包屑为什么"静默失败"** —— 不只是 `/apex` 软链断掉，
**即使软链修好了，SELinux 也会把它挡死**。

### 障碍 2：`apexd` 域对 `system_file` **连 read 都没有**

wrapper 脚本 `/system/etc/wb_apexd_wrap.sh` 的标签是 `system_file`
（v3 用 `--add ...#system/etc/init/hw/init.rc` 的 xattr 模板，即 `system_file`）。
`apexd` 域对 `system_file` 没有任何规则 ⇒ 即使能 exec sh，也**读不到脚本**。

### 障碍 3：`apexd` / `shell` 域对 `metadata_file` **只有 search，不能 write**

```
/metadata(/.*)?  u:object_r:metadata_file:s0        （plat_file_contexts:691）
(allow apexd metadata_file (dir (search)))          ← 只有 dir search
(allow shell metadata_file (dir (search)))          ← 同样只有 dir search
```

⇒ `/metadata/wbdiag/` 是 `metadata_file`，`apexd` 域**写不了日志**。

---

## 三、为什么 v3 的 `setenforce 0` 形同虚设

v3 已经在 `on early-init` 的**第一条**就放了 `setenforce 0`（init.rc 第 75 行），
但它没有起作用。原因：

1. **boot cmdline 里没有 `androidboot.selinux=permissive`**（实测确认），
   ⇒ 内核/init 默认 enforcing；
2. **init 是 `buildvariant=user` 构建**
   （boot cmdline 实测：`... kpti=off buildvariant=user`）
   ⇒ init 的 `setenforce` **内建命令在编译期被禁用**
   （`ALLOW_PERMISSIVE_SELINUX=0`），执行时直接返回错误。

**旁证**：TWRP 的 recovery 分区 cmdline 是
`... androidboot.selinux=permissive buildvariant=eng`，
所以 TWRP 里 `getenforce` 返回 **Permissive**、`/sys/fs/selinux/enforce` = 0。
**同一颗内核 ⇒ 内核完全支持 permissive。**

---

## 四、v4 修复方案（已实施，验证中）

**最小改动：只改 boot 的 cmdline，不动 system 镜像。**

```
androidboot.selinux=permissive enforcing=0
```

- `androidboot.selinux=permissive` ⇒ init 读 `ro.boot.selinux` 后调用
  `security_setenforce(0)`，**这条路径不受 `ALLOW_PERMISSIVE_SELINUX` 限制**；
- `enforcing=0` ⇒ 内核参数（`CONFIG_SECURITY_SELINUX_BOOTPARAM`），双保险。

实施方式：`03-脚本/patch_boot_cmdline.py`
（直接改 boot header offset 64 的 512 字节定长 cmdline 字段，
 390 字节 → 433 字节，剩余 78 字节，kernel/ramdisk 一字未动）。

刷入：`03-脚本/flash_boot_permissive.sh`
兜底：`03-脚本/restore_boot.sh`（还原原版 A16 boot，
      sha256 `fc60a85e05b6d9532470d59ebfa7705467214e2934e3ca9bb47b940442f1bbea`）

### 预期结果

**permissive 下三重障碍全部消失** ⇒ `sh` 能 exec、脚本能读、日志能写
⇒ `apexd_bootstrap.log` 应当生成，里面会有：

```
--- apexd --bootstrap 退出码 = N ---
--- /apex 之前 / 之后 ---
--- /dev/block/loop* / mapper ---
--- /metadata 可写性 ---
--- dmesg 尾部 250 行 ---
--- dmesg 里的 AVC 拒绝 ---
```

**这就是本项目一直缺的那块拼图。**

---

## 五、最终修复方向（回到 enforcing）

permissive 只是**诊断手段**。拿到 apexd 的真实报错后，最终要在
**enforcing 下**让 A16 正常启动。可选路径：

### 方案 A：给 `apexd-bootstrap` 指定 `seclabel u:r:shell:s0`

实测 `shell` 域权限（`plat_sepolicy.cil`）：

```
(allow shell shell_exec (file (read getattr map execute open entrypoint)))       ✅ 能 exec sh
(allow shell system_file (file (ioctl read getattr lock map open watch watch_reads)))  ✅ 能读脚本
(allow shell system_file (file (getattr map execute execute_no_trans)))          ✅
(allow init shell (process (transition)))                                        ✅ init 可转入
```

⇒ 障碍 1、2 解决；**障碍 3（写 `/metadata`）仍在** ⇒
需把日志改写到 `shell` 域可写的位置，或追加一条策略规则。

### 方案 B：自定义 SELinux 策略（最正）

在 system 镜像里追加策略规则（`plat_sepolicy.cil` 或独立 cil）：

```
(allow apexd shell_exec   (file (read getattr map execute open entrypoint)))
(allow apexd system_file  (file (read getattr map open)))
(allow apexd metadata_file (dir (add_name write create)))   ← 或换用 apex_metadata_file
(allow apexd metadata_file (file (write create open)))
```

⇒ 最干净，但需要重建 system 镜像并确保策略被正确加载
（注意 `plat_sepolicy_and_mapping.sha256` 与 `precompiled_sepolicy` 的校验）。

### 方案 C：把 wrapper 换成 `toolbox` applet 组合

`apexd` 域**允许** exec `toolbox_exec`（`/system/bin/toybox`）。
但实测 **A16 的 toybox 没有编译 `sh` applet**：

```
$ toybox sh -c 'echo hi'
toybox: Unknown command sh
```

⇒ 这条路的可行性受限，除非能找到别的、`apexd` 域允许执行且能重定向输出的程序。

**当前判断：方案 B 最正；方案 A 最快。**

---

## 六、附：本次排除的错误方向

| 曾怀疑 | 实际结论 |
|---|---|
| `/apex` 软链断掉导致 `sh` 起不来 | **是原因之一，但不是唯一** —— 实体化修好软链后 `sh` 仍起不来，因为 SELinux |
| `setenforce 0` 能解决问题 | **无效** —— user 版 init 禁用了该内建命令 |
| 镜像/实体化没生效 | **已生效** —— 设备上实测 15/15、大小精确匹配 |
| `sh`/`libc.so`/linker 有问题 | **无问题** —— 手动执行 `linker64 sh -c 'echo ok'` 成功 |
| pstore / last_kmsg 能提供线索 | **无内容** —— `/sys/fs/pstore` 空、`/proc/last_kmsg` 不存在 |
| TWRP 里 `/system` 能直接看到我们刷的镜像 | **不能** —— TWRP 的 `/system` 是它自己的；真实 system 在 `mapper/system` → `/system/` 子目录 |
