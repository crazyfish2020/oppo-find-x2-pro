# Find X2 Pro (PDEM30) 分区布局与恢复指南

> 实测时间：2026-10-02，设备正运行本移植版（ColorOS 16 / Android 16 / SDK 36）
> 数据来源：`/dev/block/by-name/`、`/dev/block/mapper/`、`/proc/partitions`、`/sys/block/dm-N/size`
> 换算：`/proc/partitions` 的 1 block = 1024 B

---

## 一、本 ROM 包只动这三个分区

| 分区 | 镜像文件 | 大小 | 刷入方式 |
|---|---|---|---|
| `boot` | `boot_permissive.img` | 100,663,296 | `fastboot flash boot` |
| `system` | `system_diag6.img` | 1,072,705,536 | `fastboot flash system`（**须在 fastbootd**） |
| `system_ext` | `system_ext_fix2.img` | 979,853,312 | `fastboot flash system_ext`（**须在 fastbootd**） |

`boot` 是物理分区；`system` / `system_ext` 是 **super 内的逻辑分区**，
只能在 **fastbootd**（`fastboot reboot fastboot` 后）里刷，普通 bootloader 会报
`Not enough space to write` 或直接找不到分区。

---

## 二、完整分区布局

### 2.1 物理分区（`/dev/block/by-name/`）

| 分区名 | 设备节点 | 大小 | 说明 |
|---|---|---|---|
| `sda` | — | 248,688,672 KB (≈237 GiB) | 主 LUN |
| `super` | `/dev/block/sda10` | 9,932,111,872 | ★ 动态分区容器 |
| `userdata` | `/dev/block/sda11` | 237,965,881,344 | 用户数据（f2fs，加密） |
| `metadata` | `/dev/block/sda7` | 16,777,216 | 加密元数据 |
| `cache` | `/dev/block/sda6` | 469,762,048 | 缓存 |
| `persist` | `/dev/block/sda2` | 33,554,432 | ★ 传感器校准等，**不可清** |
| `misc` | `/dev/block/sda3` | 1,048,576 | BCB |
| `vm-system` | `/dev/block/sda8` | 134,217,728 | 虚拟机系统 |
| `rawdump` | `/dev/block/sda9` | 134,217,728 | 崩溃转储 |
| `boot` | `/dev/block/sde11` | 100,663,296 | ★ 本包提供 |
| `recovery` | `/dev/block/sde26` | — | TWRP |
| `dtbo` | `/dev/block/sde19` | 25,165,824 | 设备树叠加 |
| `vbmeta` | `/dev/block/sde18` | — | ★ AVB 校验 |
| `vbmeta_system` | `/dev/block/sde16` | — | ★ AVB 校验（system/system_ext） |
| `vbmeta_vendor` | `/dev/block/sde17` | — | ★ AVB 校验（vendor/odm） |
| `modem` | `/dev/block/sde4` | 204,472,320 | ★ 基带固件 |
| `modemst1` | `/dev/block/sdf4` | — | ★ 基带校准 |
| `modemst2` | `/dev/block/sdf5` | — | ★ 基带校准 |
| `fsg` | `/dev/block/sdf6` | — | ★ 基带 |
| `fsc` | `/dev/block/sdf7` | — | 基带 |
| `oplus_sec` | `/dev/block/sde28` | — | OPPO 安全分区 |
| `preload` | `/dev/block/sde29` | — | 预装资源 |
| `vm-linux` | `/dev/block/sde23` | 33,554,432 | 虚拟机内核 |
| `vm-data` | `/dev/block/sde47` | — | 虚拟机数据 |
| `vm-keystore` | `/dev/block/sde46` | — | 虚拟机密钥 |
| `dsp` | `/dev/block/sde9` | 67,108,864 | DSP 固件 |

### 2.2 逻辑分区（super = sda10 内部，`/dev/block/mapper/`）

super 总容量 **9,932,111,872 B (9.25 GiB)**，内含 15 个逻辑分区，合计约 **4.82 GiB**，
其余约 4.4 GiB 为动态分区预留空间。

| 分区名 | mapper 节点 | 大小 | 归属 | 本包是否刷 |
|---|---|---|---|---|
| `system` | `/dev/block/dm-13` | 1,072,705,536 | 供体重建 | ★ **刷** |
| `system_ext` | `/dev/block/dm-2` | 979,853,312 | 供体重建 | ★ **刷** |
| `vendor` | `/dev/block/dm-0` | 409,079,808 | **目标机原厂** | 保留 |
| `odm` | `/dev/block/dm-4` | 1,166,114,816 | **目标机原厂** | 保留 |
| `product` | `/dev/block/dm-1` | 9,703,424 | 两者相同 | 保留 |
| `my_product` | `/dev/block/dm-7` | 1,442,086,912 | 目标机（含机型标识） | 保留 |
| `my_stock` | `/dev/block/dm-14` | 90,861,568 | **目标机原厂** | 保留 |
| `my_bigball` | `/dev/block/dm-5` | 4,096 | 目标机原厂 | 保留 |
| `my_heytap` | `/dev/block/dm-6` | 4,096 | 目标机原厂 | 保留 |
| `my_carrier` | `/dev/block/dm-9` | 4,096 | 目标机原厂 | 保留 |
| `my_company` | `/dev/block/dm-11` | 4,096 | 目标机原厂 | 保留 |
| `my_engineering` | `/dev/block/dm-3` | 12,288 | 目标机原厂 | 保留 |
| `my_preload` | `/dev/block/dm-10` | 8,192 | 目标机原厂 | 保留 |
| `my_region` | `/dev/block/dm-8` | 3,411,968 | 目标机原厂 | 保留 |
| `my_manifest` | `/dev/block/dm-12` | 638,976 | 目标机原厂 | 保留 |

> **★ 关键判断依据**：`vendor` / `odm` / `my_stock` 在目标机上的尺寸
> （409 MB / 1.17 GB / 91 MB）与供体一加 8T 的对应分区
> （962 MB / 234 MB / 4.39 GB）**完全不同** —— 证明它们是目标机原厂内容，
> 一旦刷入供体版本会丢失 Find X2 Pro 的硬件驱动与校准数据（相机、指纹、基带）。
> 这就是本移植方案「只重建 system / system_ext / boot」的根本原因。

---

## 三、刷机后的 `fastboot` 分区可见性

| 环境 | 可见分区 |
|---|---|
| bootloader（`fastboot`） | `boot` / `dtbo` / `vbmeta*` / `recovery` / `modem` 等**物理**分区 |
| fastbootd（`fastboot reboot fastboot`） | 上述 + **super 内的逻辑分区**（`system` / `system_ext` / `vendor` / `odm` / `my_*`） |

`flash-a16.sh` 已自动处理这个切换（步骤 2/3）。

---

## 四、恢复 / 回滚

### 4.1 只回滚 boot（最轻，失败率最低）

```sh
fastboot flash boot <你的原厂 boot.img>
# 或直接用项目里的脚本：
./03-脚本/restore_boot.sh
```

### 4.2 完全回滚到 ColorOS 15 / 官方

用 OPPO 官方线刷包（或已备好的 ColorOS 15 回滚包，7.6 GB，
在项目 `02-移植素材/platform-tools/` 附近）：
按官方 `flash-all` 流程走，或进 fastbootd 逐分区刷回 `system` / `system_ext` / `vendor` / `odm` / `my_*`。

> ⚠️ 回滚**不要** `fastboot erase userdata`，除非确定要清数据 ——
> 一旦清了，`/data/system/*.db` 会由 init 重新生成，
> 而 ColorOS 16 移植版对 `/data/system/locksettings.db` 的属主极其敏感
> （见 `根因-8-locksettings.db属主为root导致system_server自杀.md`）。

### 4.3 救砖

设备有 TWRP（`3.7.1_12-Compass-Color597-V2.1`）可进，adb 在 recovery 下即 root。
`./03-脚本/catch_boot.sh` 能抓「启动→重启」完整窗口日志。

---

## 五、如何自行备份这些分区（需要 root 或 TWRP）

设备运行时 `adb shell` 是 `shell` 用户（uid 2000），**读不了块设备**
（`/dev/block/sde*` 权限为 `brw------- root root`）。两条路：

**路线 A —— Magisk root**（手机上点「允许」授权 shell）
```sh
adb shell 'su -c "dd if=/dev/block/by-name/vendor of=/data/local/tmp/vendor.img"'
adb pull /data/local/tmp/vendor.img
```

**路线 B —— TWRP recovery**（adb 天然是 root）
```sh
adb reboot recovery
adb shell 'dd if=/dev/block/by-name/odm of=/tmp/odm.img'
adb pull /tmp/odm.img
```

**只要哈希不要整份镜像**（零磁盘占用，用于日后校验）：
```sh
adb shell 'su -c "sha256sum /dev/block/mapper/vendor /dev/block/mapper/odm"'
```

> 本发布包**不含** `vendor` / `odm` / `my_*` 的镜像，这是**有意为之**：
> 它们是设备原厂内容（约 3.2 GB），刷机时保留不动，
> 随包分发既无必要也违反「不覆盖目标机固件」的移植原则。
