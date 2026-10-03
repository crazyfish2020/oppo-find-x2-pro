# A16 移植启动失败 —— 根因修正（本次设备侧实测版）

> **本文取代** `根因-A16-APEX-EROFS-big-pcluster-与-4.19.157-内核不兼容.md` 里的启动卡点结论。
> 那一版说的"卡在 first-stage init 或更早"**作废**，理由见下。
> 生成时间：2026-10-01，设备为 PDEM30，正处于 TWRP。

---

## 一、先把"假阴性"这件事说清楚（这是本次最大的坑）

之前的判断依据是"`/metadata/wbdiag/` 目录不存在 ⇒ 面包屑一条都没写 ⇒ init 连 `on early-init` 都没跑到"。

**这个推理是错的，因为面包屑的构造方式本身就不可能成功。**

实测（从 `07-重建/tree` 里逐个读 ELF 的 `PT_INTERP`）：

| 二进制 | PT_INTERP | `/apex` 未挂时能否 exec |
|---|---|---|
| `/system/bin/init` | `/system/bin/bootstrap/linker64` | ✅ 能 |
| `/system/bin/apexd` | `/system/bin/bootstrap/linker64` | ✅ 能 |
| `/system/bin/servicemanager` | `/system/bin/bootstrap/linker64` | ✅ 能 |
| `/system/bin/sh` | `/system/bin/linker64` | ❌ **不能** |
| `/system/bin/toybox`（`true`/`false`/`sh` 都是它） | `/system/bin/linker64` | ❌ **不能** |
| `/system/bin/boringssl_self_test64` | `/system/bin/linker64` | ❌ **不能** |
| `/system/bin/logd` | `/system/bin/linker64` | ❌ **不能** |

而 `/system/bin/linker64` 本身是个软链：

```
/system/bin/linker64 -> /apex/com.android.runtime/bin/linker64
```

**⇒ `/apex` 没挂上时，`/system/bin/linker64` 是断的，所有 `PT_INTERP=/system/bin/linker64` 的程序全部 exec 不了（ENOENT）。**

而我们 v1/v2 的面包屑是这么做的：

```ini
on early-init
    exec_start wb_diag_earlyinit      # 服务体：exec /system/bin/sh /system/etc/wb_diag.sh earlyinit 0
```

用的是 **`sh`** —— 恰好就是那个 exec 不了的。

**⇒ 面包屑必然静默失败，`/metadata/wbdiag` 不存在是【假阴性】，不能用来推断 init 走到了哪。**

同时这也解释了另外两件事：
- `/metadata` 里 09-30 / 10-01 的启动**什么都没写** —— 不是因为没跑到，而是因为写它的工具（sh）根本起不来。
- v2 里把 `boringssl_self_test64` 换成 `/system/bin/true` 想"绕开自检" —— **没用**，因为 `true` 是 `toybox` 的软链，`toybox` 也是 `PT_INTERP=/system/bin/linker64`，一样起不来。

---

## 二、真正的失败链（现在是有证据的，不是猜的）

```
1. second-stage init 起来（init 自身用 bootstrap linker，能跑）
2. on early-init 跑到最后一条：exec_start apexd-bootstrap
3. apexd-bootstrap 挂 bootstrap APEX 失败  →  /apex 保持空
4. on init 触发 → exec_start boringssl_self_test64
      /system/bin/boringssl_self_test64  PT_INTERP=/system/bin/linker64  →  断链  →  exec 失败
5. 该服务的 reboot_on_failure 生效：reboot_on_failure reboot,boringssl-self-check-failed
6. init 立刻 reboot → 重启 → 再来一遍
```

**第 5 步的物证**（设备侧读到，且是持久属性）：

```
persist.sys.boot.reason = reboot,boringssl-self-check-failed
```

`boringssl-self-check-failed` 这个字符串**只有** init 在按 `reboot_on_failure` 重启时才会写进 `persist.sys.boot.reason`。
它同时证明了：**init 确实跑到了 `on init`**（所以"卡在 first-stage"的说法彻底不成立）。

定义出处（本地 tree 里可查）：

```
system/etc/init/hw/init.rc:1008   service boringssl_self_test64 /system/bin/boringssl_self_test64
                                      reboot_on_failure reboot,boringssl-self-check-failed
system/etc/init/hw/init.boringssl.zygote64_32.rc
                                  on init && property:ro.product.cpu.abilist64=*
                                      exec_start boringssl_self_test64
```

**⇒ 卡点收敛为一句话：`apexd-bootstrap` 没能把 APEX 挂到 `/apex`。**

之后的一切（没有 USB、没有内核日志、没有 zygote）都是这一件事的后果：`/apex` 空 ⇒ 全系统动态二进制都起不来。

---

## 三、已排除的候选（都有实测证据，不要再回头查）

| 候选 | 排除依据 |
|---|---|
| 卡在 first-stage init 或更早 | `persist.sys.boot.reason=reboot,boringssl-self-check-failed` 证明 init 跑到了 `on init` |
| 面包屑没写是因为 `/metadata` 没挂 | `/metadata` 是 `first_stage_mount`，且 `/metadata/gsi`、`/metadata/bootstat` 都在 |
| boot/vbmeta/dtbo 被刷坏 | 与刷机前快照逐字节相同（`fc60a85e…` / `755099cb…` / `7d247e39…`） |
| 内核不对 | 设备 boot 与 zip 原版 boot 的 kernel 完全相同：`sha256=69cbcaac633116816d7ae6afc7159e05`，`4.19.157-color597-os15pro-perf` |
| ramdisk 不对 | 差异只有 Magisk（多 87 KB）；`fstab.qcom` 与 zip 原版逐字节相同 |
| erofs 格式不兼容 | 所有 erofs 分区（含能启动的 COS15）超级块参数完全一致：`feature_incompat=0x1`、`blkszbits=12` |
| 内核缺 erofs | 三个内核全 `CONFIG_EROFS_FS=y + EROFS_FS_ZIP=y + LZ4/LZ4HC` |
| APEX 镜像本身坏了 | TWRP 里 `losetup` + `mount -t erofs` 成功；`avbtool verify_image` 通过 |
| `vbmeta_system` 校验拦截 | `Flags: 3`（hashtree + verification 都禁用） |
| `stdio_to_kmsg` 是非法选项 | A16 的 `init` 二进制里就有这个字符串，`prng_seeder.rc` 也在用 |
| `misc` 的 BCB 能控制启动目标 | ABL 日志里**根本没有读 misc 的痕迹**；恢复/快速启动由 `KeyPress` + `BootReason` + OPPO `Phoenix` 读 `opporeserve1` 决定 |

---

## 四、修复方案（v3）

### 4.1 关键数字：全 `/system` 只有 **17 个**软链指向 `/apex/`

```
system/bin/linker64              -> /apex/com.android.runtime/bin/linker64
system/bin/linker                -> /apex/com.android.runtime/bin/linker
system/bin/linker_asan64         -> /apex/com.android.runtime/bin/linker64
system/bin/linker_asan           -> /apex/com.android.runtime/bin/linker
system/bin/linker_hwasan64       -> /apex/com.android.runtime/bin/linker64
system/lib64/libc.so             -> /apex/com.android.runtime/lib64/bionic/libc.so
system/lib64/libm.so             -> /apex/com.android.runtime/lib64/bionic/libm.so
system/lib64/libdl.so            -> /apex/com.android.runtime/lib64/bionic/libdl.so
system/lib64/libdl_android.so    -> /apex/com.android.runtime/lib64/bionic/libdl_android.so
system/lib64/hwasan/libc.so      -> /apex/com.android.runtime/lib64/bionic/hwasan/libc.so
system/lib64/libclang_rt.hwasan-aarch64-android.so -> .../libclang_rt.hwasan-aarch64-android.so
system/lib/libc.so               -> /apex/com.android.runtime/lib/bionic/libc.so
system/lib/libm.so               -> /apex/com.android.runtime/lib/bionic/libm.so
system/lib/libdl.so              -> /apex/com.android.runtime/lib/bionic/libdl.so
system/lib/libdl_android.so      -> /apex/com.android.runtime/lib/bionic/libdl_android.so
system/usr/icu                   -> /apex/com.android.i18n/etc/icu
system/bin/ethtool               -> /apex/com.android.tethering/bin/ethtool
```

**15 个来自 `com.android.runtime`（bootstrap APEX），1 个来自 i18n，1 个来自 tethering。**

### 4.2 v3 要做三件事

**① 把这 15 个软链"实体化"** —— 从 `com.android.runtime.apex` 的内层 `apex_payload.img` 里把
`bin/linker64`、`bin/linker`、`lib64/bionic/libc.so`、`libm.so`、`libdl.so`、`libdl_android.so`
直接解出来，**作为实体文件**放进 `/system/bin/`、`/system/lib64/`。

> 效果：`/system/bin/linker64` 变成真实的静态链接器 ⇒
> **所有 `PT_INTERP=/system/bin/linker64` 的程序（`sh`、`toybox`、`adbd`、`logd`、`servicemanager`…）在 `/apex` 为空时也能跑。**
> 这一步不是为了"让系统跑起来"（框架还需要 ART APEX），
> **而是为了把 adbd 拉起来 ⇒ 拿到 USB ⇒ 拿到 adb shell ⇒ 才能交互式地查 `apexd-bootstrap` 到底为什么失败。**

**② 面包屑改成 init 内建命令** —— 不再用 `sh`，改成：

```ini
on early-init
    mkdir /metadata/wbdiag 0777 root root
    write /metadata/wbdiag/10_earlyinit 1
    ...
    write /metadata/wbdiag/18_pre_apexdbootstrap 1
    exec_start apexd-bootstrap
    write /metadata/wbdiag/20_post_apexdbootstrap 1

on property:init.svc.apexd-bootstrap=stopped
    write /metadata/wbdiag/21_apexdbootstrap_stopped 1
on property:apexd.status=ready
    write /metadata/wbdiag/22_apexd_status_ready 1
```

`write` / `mkdir` 是 **init 自己的内建命令，不 exec 任何东西**，`/apex` 挂没挂都能写。
这才是"零依赖面包屑"。

**③ 抓 apexd-bootstrap 的真实报错** —— ①做完之后 `sh` 能跑了，加一个服务：

```ini
service wb_apexd_probe /system/bin/sh /system/etc/wb_diag_apexd.sh
    class core
    oneshot
    disabled
    stdio_to_kmsg
```
脚本里 `apexd --bootstrap 2>&1 | tee /metadata/wbdiag/apexd_bootstrap.log`，
**这样失败原因就直接落在 `/metadata` 里**，不用再猜。

### 4.3 全局关掉 `reboot_on_failure`

除了 `apexd.rc`，还要把 `init.rc` 里 boringssl 那 4 条、以及其它 `.rc` 里所有
`reboot_on_failure` 全部注释掉，**让设备失败时不要立刻重启跑掉**，
否则永远看不到"失败之后会发生什么"。

---

## 五、回滚底线（不变）

- 回滚包 `platform-tools/FindX2Pro_ColorOS_15.0.2_Pro_2025.1_20250823_By_Color597.zip` 完好，`unzip -t` CRC 通过。
- 设备当前 `boot` 分区的镜像本地有完整备份：`04-日志/dump_202707/boot.img`，
  `sha256=fc60a85e05b6d9532470d59ebfa7705467214e2934e3ca9bb47b940442f1bbea`。
- **TWRP 装在 `recovery` 分区**（分区 134,217,728 B = 128 MiB，与 `TWRP-13-Compass-Color597-V2.0.img` 字节数完全相同），
  ABL 支持 recovery 启动（现在跑的 TWRP 的 cmdline 里就有 `androidboot.mode=recovery`）
  ⇒ **随时能用"重启到 recovery"回到 TWRP，这条退路是可靠的。**

---

## 六、附：本次顺带确认的设备事实

- 分区：`boot=96 MiB`(sde11)、`recovery=128 MiB`、`dtbo=24 MiB`、`vbmeta=64 KiB`、`vbmeta_system=64 KiB`、
  `misc=1 MiB`、`super=9.25 GiB`(sda10)、`data`=sda11(221 GiB)、`cache`=sda6(448 MiB)。
  **`vendor_boot` 分区不存在** —— 这正好对上 ABL 日志里的 `Invalid vendor_boot partition. Skipping`。
- `/apex` 是 `tmpfs` 且 **noexec**；`/tmp`、`/dev` 可执行。
- TWRP 的 `/sbin/sh` 是软链 → `/system/bin/sh`，**它其实是 ROM 的 `/system/bin/sh`**，
  所以一旦 `/system` 被盖住或被换成 A16 的（软链断裂），adbd 就再也 exec 不出 shell ——
  本次设备卡死就是这么来的，重启一次即可恢复。
- `/proc/bootprof` 是 OPPO 自己的启动阶段探针（init.rc 里每阶段都有 `write /proc/bootprof "INIT:xxx"`），
  但它**每次启动都重置**，且本次 rawdump 区域不含它 ⇒ 拿不到上一次失败启动的阶段表。
  以后可以在失败后**先不要重启**，直接读 `/proc/bootprof`。
