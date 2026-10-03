# ColorOS 16（Android 16）for OPPO Find X2 Pro

[![Android](https://img.shields.io/badge/Android-16%20%2F%20SDK%2036-3DDC84?logo=android&logoColor=white)](#)
[![ColorOS](https://img.shields.io/badge/ColorOS-16-1E7B3C)](#)
[![Device](https://img.shields.io/badge/Device-Find%20X2%20Pro%20(PDEM30)-blue)](#)
[![Kernel](https://img.shields.io/badge/Kernel-4.19.157-orange)](#)
[![Status](https://img.shields.io/badge/Status-可日常使用-success)](#)

> **把 ColorOS 16（Android 16）非官方移植到 OPPO Find X2 Pro。**
> 一台被官方停在 ColorOS 13 / Android 13 的 2020 年旗舰，现在跑 Android 16。

[English](README.md) · **简体中文**

---

## ⚠️ 免责声明 —— 请先读这一段

- **刷机风险自负。** 本机是 **A-only（无 A/B 槽）**，刷错分区可能**硬砖**，
  只能靠 EDL / fastboot 抢救。
- **务必先备份数据。** 刷机会清空 `/data`。
- **ColorOS 是 OPPO 的专有系统。** [Releases](../../releases) 里的系统镜像属于
  **非官方社区构建**，**仅供个人学习、研究与测试**，禁止商用再分发。
  如您是权利方并希望下架，请提 Issue。
- **不提供任何担保。** 你可能丢失数据、VoLTE、指纹，甚至变砖。
- **必须先解锁 bootloader**（解锁会清空全部数据）。

---

## 一、这是什么

这是一次**跨机型移植**：系统镜像基于社区制作的
**一加 8T（KB2000）ColorOS 16 移植包**，而 8T 与 Find X2 Pro 用的是**同一颗 SoC**
—— 高通骁龙 865（`kona` / SM8250）。

因为平台相同，只要打掉一批「内核版本门限」补丁，系统就能在 Find X2 Pro 上启动
（供体面向 4.19.325 内核，而 Find X2 Pro 是 **4.19.157**）。

| | |
|---|---|
| **目标机型** | OPPO Find X2 Pro —— `PDEM30` / `OP4A7A` |
| **SoC** | 高通骁龙 865（`kona` / `SM8250`） |
| **目标内核** | **4.19.157** |
| **供体机型** | 一加 8T —— `KB2000`（`kona`） |
| **供体内核** | 4.19.325 |
| **Android 版本** | 16（SDK 36） |
| **ColorOS 版本** | 16（25Q2） |
| **Root** | Magisk 30.7 |
| **刷入分区** | 仅 `boot`、`system`、`system_ext` |

> **不刷、保持原样：** `vendor`、`odm`、`my_stock`、`my_product`、`dtbo`、
> `vbmeta`、基带固件。这样能保住原厂相机 HAL、射频栈与硬件校准。

---

## 二、当前状态

已在真机 PDEM30 上作为**日常主力机**验证。

| 功能 | 状态 | 说明 |
|---|---|---|
| 开机 | ✅ | `sys.boot_completed=1`，release `16`，sdk `36` |
| Wi-Fi | ✅ | |
| 移动数据（5G/LTE） | ✅ | |
| VoLTE | ✅ | |
| 蓝牙 | ✅ | |
| 相机（拍照） | ✅ | 用 **Open Camera**（见下文） |
| 相机（录像） | ✅ | **必须安装本仓库的 Magisk 模块** |
| 传感器（加速度/陀螺/距离） | ✅ | |
| 指纹 | ❌ | `gaia init_fault=18` |
| SELinux | ⚠️ | 当前为 **Permissive**（移植所需） |
| 原厂相机 App | ❌ | 见[已知问题](#六已知问题) |
| 机型标识 | ⚠️ | 仍显示 `KB2000` / `OnePlus8T`（来自供体 `/my_product`） |

---

## 三、准备条件

**手机端**

- OPPO Find X2 Pro（`PDEM30`），**已解锁 bootloader**
- 第三方 Recovery（推荐 TWRP）或 fastboot 可用
- 电量 ≥ 50%

**电脑端**

- `adb` 与 `fastboot`（Android platform-tools）
- **macOS（Apple Silicon / Intel）**、**Linux** 或 **Windows**
- 约 4 GB 可用磁盘空间

> 自带的 `scripts/flash-m1.sh` 针对 **Apple Silicon macOS** 优化
> （自动识别 `/opt/homebrew/bin`，并规避了 macOS 上 `fastboot getvar` 会卡死的 bug）。
> Linux / Windows 请用 [5.3 节](#53-手动刷机linux--windows)的手动命令。

---

## 四、下载

所有文件都在 **[Releases](../../releases)** 页面。

| 文件 | 大小 | 用途 |
|---|---|---|
| `boot_permissive.img` | 96 MiB | boot 镜像（SELinux permissive） |
| `system_diag6.img` | 1.00 GiB | system 分区 |
| `system_ext_fix2.img` | 934 MiB | system_ext 分区 |
| `SHA256SUMS.txt` | — | 校验和 —— **刷前必验** |
| `aac-c2-preference-fix-v1.zip` | 2.5 KiB | Magisk 模块 —— **录像修复** |

校验下载：

```bash
shasum -a 256 -c SHA256SUMS.txt        # macOS / Linux
certutil -hashfile boot_permissive.img SHA256   # Windows
```

期望的校验和：

```
1a02f68ff04d581fa1e353a43a0e500a29ba43b4c7a0058c7229bd0e2ba27d11  boot_permissive.img
e3534c0d8dd269fb4226d90f2beb54eadddd9cc13ee00b95cb6e44b6952a6c76  system_diag6.img
0c990cc55410c1fb67695695aca8b023f6ea88e4b693ad3668bdc36d88459f1a  system_ext_fix2.img
```

---

## 五、刷机步骤

### 5.1 开始之前

1. **备份所有数据。** 刷机会清空 `/data`。
2. 确认 bootloader 已解锁（`fastboot flashing unlock`）。
3. 确认电脑能识别设备：`adb devices` 或 `fastboot devices`。

> ⚠️ **本机是 A-only（无 A/B 槽）。** 如果刷了坏 boot，只能靠 fastboot 或 EDL 抢救。
> 千万不要跳过校验和验证。

### 5.2 一键刷机（macOS，推荐）

```bash
# 1. 把三个 .img 放到脚本同目录（或改脚本里的 IMG 路径）
# 2. 试运行 —— 只检查环境并校验 sha256，不碰设备
./scripts/flash-m1.sh --check

# 3. 正式刷机
./scripts/flash-m1.sh
```

脚本会自动完成：

1. 定位 `adb` / `fastboot`
2. 用 `SHA256SUMS.txt` 校验三个镜像
3. 重启到 bootloader → 刷 `boot`
4. 重启到 **fastbootd** → 刷 `system` 与 `system_ext`
5. 重启进系统

**其它参数**

| 参数 | 作用 |
|---|---|
| `--check` | 只做环境检查与校验和验证 |
| `--status` | 打印设备当前所处模式 |
| `--reboot` | 重启到系统 |
| `--help` | 显示帮助 |

### 5.3 手动刷机（Linux / Windows）

```bash
# --- bootloader ---
fastboot flash boot        boot_permissive.img

# --- 切到 fastbootd（system/system_ext 是 super 里的逻辑分区） ---
fastboot reboot fastboot

# --- 系统分区 ---
fastboot flash system      system_diag6.img
fastboot flash system_ext  system_ext_fix2.img

# --- 重启 ---
fastboot reboot
```

> ⚠️ `system` 与 `system_ext` 位于 `super` 动态分区内，
> **只能在 `fastbootd` 模式下刷**，bootloader 模式刷不了。
> 如果 `fastboot flash system` 失败，先执行 `fastboot reboot fastboot` 再试。

### 5.4 首次开机

- 约 50 秒开机动画，约 55 秒后 `sys.boot_completed=1`。
- 首次开机可能更久（最长 5 分钟），请耐心等待。

验证：

```bash
adb shell getprop sys.boot_completed          # 期望 1
adb shell getprop ro.build.version.release    # 期望 16
adb shell getprop ro.build.version.sdk        # 期望 36
```

### 5.5 ★ 刷完必做：录像修复（千万别跳过）

**不装这个模块，录像会提示「无法保存视频」。**

安装 Magisk 模块 `aac-c2-preference-fix-v1.zip`：

1. 打开 **Magisk** 应用 → **模块** → **从本地安装**
2. 选择 `aac-c2-preference-fix-v1.zip`
3. **重启**

验证：

```bash
adb shell getprop vendor.audio.c2.preferred   # 期望 true
```

**它做了什么：** 只设置一个系统属性 `vendor.audio.c2.preferred=true`，
让高通 AV 增强库跳过创建自定义 OMX AAC 编码器
（`OMX.qcom.audio.encoder.aac`）—— 而这个组件 Find X2 Pro 的原厂 vendor
**从未声明过**。不设这个属性，`MediaCodec::CreateByComponentName` 会返回
`nullptr`，录像就报 `Failed to create audio encoder`。

它**不修改任何分区文件**，只设一个属性。

**临时应急**（不做模块，重启即失效）：

```bash
adb shell su -c 'setprop vendor.audio.c2.preferred true'
```

完整分析见 [`docs/root-cause/根因-13-…md`](docs/root-cause/)。

---

## 六、已知问题

| 问题 | 详情 | 规避方法 |
|---|---|---|
| **原厂一加相机不可用** | 供体 `/my_product` 强行以 `Camera 6` SAT 多摄模式初始化，与 Find X2 Pro 相机拓扑不匹配，HAL 返回 `-38 ENOSYS`。硬件与 HAL **完全正常**。 | 用 **Open Camera**（打开 `Camera 0`，完全正常） |
| 指纹 | `gaia init_fault=18` | 暂无 |
| 机型显示 `KB2000` | 来自供体 `/my_product/build.prop` | 仅影响观感 |
| SELinux 为 `Permissive` | 移植所需 | — |
| `reservedsize=128M` 未生效 | f2fs 占满整个分区 | 仅影响观感 |
| `/odm/bin/hw/subsys_daemon` 崩溃 | 位于 `libradioapis.so::QmiVsClient::getNecData` | 无功能影响 |

> **相机说明：** 原厂一加相机已移除。请从 F-Droid 安装
> [Open Camera](https://f-droid.org/packages/net.sourceforge.opencamera/)。
> 拍照、录像（配合上面的 Magisk 模块）、预览全部正常。

---

## 七、⚠️ 恢复出厂设置 —— 不按这个做必进死循环

**千万不要用 TWRP 的普通「Wipe / 双清」。**

TWRP 的双清**不会擦除 FBE 密钥目录** `/data/unencrypted`。
旧密钥与新 `/data` 不匹配，设备就会反复掉回 recovery
（`enablefilecrypto_failed` → `set_policy_failed:/data/cache`）。

**方式 A —— TWRP「Format Data」（推荐）**

1. 进入 TWRP
2. **Wipe** → **Format Data** → 输入 `yes`
3. **Reboot → Recovery**（重要，让 TWRP 重新识别新的 f2fs 结构）
4. **Reboot → System**

**方式 B —— ADB（10 秒）**

```bash
# 1. 重置 FBE 密钥与 misc 引导标志
adb shell 'mount -t f2fs /dev/block/sda11 /data 2>/dev/null; rm -rf /data/unencrypted /data/gsi; dd if=/dev/zero of=/dev/block/by-name/misc bs=4096 count=1; sync'

# 2. 清空 data 全部旧策略（保留 lost+found）
adb shell 'mount -t f2fs /dev/block/sda11 /data 2>/dev/null; cd /data && for d in *; do [ "$d" = "lost+found" ] || rm -rf "$d"; done; sync'

# 3. 重启
adb reboot
```

---

## 八、故障排查

| 现象 | 原因 / 解决 |
|---|---|
| 恢复出厂后反复回 TWRP | 见[第七章](#七️-恢复出厂设置--不按这个做必进死循环) |
| 开机卡第一屏 | `apexd-bootstrap` 被 SELinux 拦 —— boot 必须用本仓库的 permissive 版 |
| 无 Wi-Fi / 无移动数据 | `netd` / `netbpfload` 内核门限 —— 你刷的是原版 `system.img`，不是 `system_diag6.img` |
| 「无法保存视频」 | 装 Magisk 模块 —— 见 [5.5 节](#55--刷完必做录像修复千万别跳过) |
| 相机黑屏 | 用 Open Camera —— 见[已知问题](#六已知问题) |
| fastboot 刷 `system` 失败 | 你在 bootloader 而不是 fastbootd —— 执行 `fastboot reboot fastboot` |
| 电脑识别不到设备 | 换数据线/接口；macOS 上用 `--check` 诊断 |

更多细节：[`docs/troubleshooting.zh-CN.md`](docs/troubleshooting.zh-CN.md)

**常用调试命令**

```bash
# 让 MediaCodec 打印组件名（录像/编码排障）
adb shell su -c 'setprop debug.oplus.video.log.enable 5'
adb shell su -c 'setprop ctl.restart media'

# 查看可用编码器列表
adb shell dumpsys media.player | grep -A2 -i encoder

# init 内建命令的失败原因只在 dmesg，不在 logcat
adb shell dmesg | grep -i 'init: Command'
```

---

## 九、文档索引

| 文档 | 内容 |
|---|---|
| [`docs/flashing-guide.zh-CN.md`](docs/flashing-guide.zh-CN.md) | 完整刷机指南 + 注意事项 |
| [`docs/partition-layout.zh-CN.md`](docs/partition-layout.zh-CN.md) | 分区表与变砖恢复 |
| [`docs/troubleshooting.zh-CN.md`](docs/troubleshooting.zh-CN.md) | 完整排障运维手册 |
| [`docs/project-history.zh-CN.md`](docs/project-history.zh-CN.md) | 项目历程（早期 LineageOS GSI 路线） |
| [`docs/root-cause/`](docs/root-cause/) | 全部 13 个 bug 的根因分析 |

---

## 十、都修了什么（最有意思的部分）

这个移植一共修掉了 **13 个独立 bug**。挑几个重点：

| # | 现象 | 根因 |
|---|---|---|
| 1–3 | 开机死循环、无网络 | `libnetd_updatable.so` / `netbpfload` 要求内核 ≥ 5.4 / ≥ 4.19.236，而本机是 **4.19.157** → 二进制 NOP 修补 |
| 4 | init 拒绝 `exec_start` | `mkfs.erofs --replace` 丢了 SELinux xattr |
| 5–6 | APEX 签名缺失 | 重打包 APEX 丢了 APK Signature Scheme v2 签名块 → 补签 33 + 1 个 APEX |
| 7 | 开机卡 Phase 480 | `/data/system/locksettings.db` 属主为 `root:root 0600` → `chown system:system` |
| 9 | 相机黑屏 | 供体 `/my_product` 请求不兼容的 SAT 相机 → 换 Open Camera |
| 11 | 反复回 TWRP | TWRP 双清不重置 FBE 密钥 |
| **13** | **「无法保存视频」** | **`vendor.audio.c2.preferred` 未开启 → 请求了从未声明的 OMX AAC 编码器** |

完整分析（含反汇编证据、logcat 日志、字节偏移）都在
[`docs/root-cause/`](docs/root-cause/)。

---

## 十一、从源码构建

`scripts/build/` 里是产出镜像所用的全部脚本：

- `build_system_diag_v3.sh` —— 主 system 镜像构建（erofs + xattr + APEX 签名）
- `apex_v2_sign.py` —— 用 APK Signature Scheme v2 重签 APEX
- `patch_netbpfload.py` / `patch_libnetd_updatable.py` —— 内核版本门限修补
- `patch_boot_cmdline.py` —— 注入 `androidboot.selinux=permissive`
- `erofs_read.py` / `erofs_rebuild.py` —— erofs 工具

**所用工具链**

- Python 3.13 + `capstone` 5 + `pyelftools`
- `erofs-utils` 1.9.4（`mkfs.erofs`、`fsck.erofs`、`dump.erofs`）
- `apksig`（用于 APEX v2 签名）

你还需要供体基底镜像（一加 8T 的 ColorOS 16 移植包）——
见 [`docs/root-cause/交叉参考-大侠阿木8T移植项目.md`](docs/root-cause/)。

---

## 十二、致谢

- **[daxiaamu](https://github.com/daxiaamu)** —— 一加 8T 的 ColorOS 16/17 原始移植包，
  是本工作的基础。没有它就没有这一切。
- **Color597** —— Find X2 Pro 的 TWRP recovery
- **Magisk**（topjohnwu）
- OPPO / 一加 玩机社区

---

## 十三、许可

- **本仓库的脚本与文档**：**MIT** —— 见 [`LICENSE`](LICENSE)。
- **系统镜像**（`boot_permissive.img`、`system_diag6.img`、`system_ext_fix2.img`）：
  **不在 MIT 授权范围内。** 它们包含 OPPO / 高通的专有软件，
  **仅供个人非商业使用**。一切权利归各自所有者。

---

## 十四、参与贡献

欢迎提 Issue 和 Pull Request，尤其欢迎：

- 中文文档的英文翻译
- 指纹修复
- 恢复原厂相机

提交 bug 时请附上**机型、版本号与完整日志**。
