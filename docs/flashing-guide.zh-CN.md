# OPPO Find X2 Pro · ColorOS 16（Android 16）移植版 —— 刷机说明与注意事项

> **目标机**：OPPO Find X2 Pro（PDEM30 / kona / SM8250，内核 4.19.157）
> **刷入系统**：ColorOS 16 / Android 16（SDK 36 / 25Q2）移植版
> **供体**：OnePlus 8T（KB2000 / kona，第三方 ColorOS 16 移植包）
> **归档日期**：2026-10-04
> **当前状态**：✅ 设备已稳定运行，`sys.boot_completed=1` / `release=16` / `sdk=36`

---

## 0. 一分钟速览

- **只刷 3 个分区**：`boot` / `system` / `system_ext`。
- **完全不动**：`vendor` / `odm` / `my_stock` / `my_product` / `dtbo` / `vbmeta` / 基带固件。
- 刷完**必须**安装 Magisk 模块 `aac-c2-preference-fix-v1.zip`，否则**第三方相机录像无法保存**（详见第 5 章）。
- **恢复出厂设置时严禁只用 TWRP「双清」**，会反复掉回 recovery（详见第 6.1 节）。

---

## 1. 归档目录结构

```
A16-FindX2Pro-最终归档/
├── flash-m1.sh                        ← ★ 一键刷机脚本（Apple Silicon 定制）
├── 先读我.md                          ← 项目总说明（移植全过程）
├── 刷机说明与注意事项.md              ← 本文件
├── 分区布局与恢复指南.md              ← 分区表 / 变砖恢复
├── 移植问题与完整排障运维手册.md      ← 11+ 个根因与运维手册
├── 正式版刷机包/                      ← ★ 刷机就只用这里的文件
│   ├── images/
│   │   ├── boot_permissive.img
│   │   ├── system_diag6.img
│   │   └── system_ext_fix2.img
│   ├── magisk/
│   │   └── aac-c2-preference-fix-v1.zip   ← ★ 录像修复模块，必装
│   └── SHA256SUMS.txt
├── 过程文件/                          ← 中间产物、分析、脚本（刷机用不到）
│   ├── 分析/       根因-1..13.md、验收清单、证据截图/视频
│   ├── 脚本/       构建 / 补丁 / 取证脚本 + 发布包脚本快照
│   ├── 报告/       过程报告
│   ├── 移植素材/   donor_a16 供体文件、netbpfload 补丁、镜像指纹清单
│   ├── 发布文档/   发布时的 README / docs / evidence / flash-a16.sh
│   ├── Magisk工具/ Magisk v30.7、LSPosed、搞机助手、爱玩机工具箱、OpenCamera
│   └── 操作卡/     操作卡-当前状态 / 下一步 / v3取证
└── 应用备份/                          ← 40 个应用 APK 备份（换机/重装用）
    ├── apk/
    ├── 包名清单.txt
    └── 版本清单.txt
```

---

## 2. 刷机前准备

1. **解锁 bootloader**（一次性，会清空数据）。
2. 安装 platform-tools（`adb` / `fastboot`）。
   - Apple Silicon 用 Homebrew 安装：`brew install android-platform-tools`（路径 `/opt/homebrew/bin`）。
3. **备份数据**（刷机会清空 `/data`）。
4. 手机电量 ≥ 50%，用**原装或高质量数据线**连接电脑。

---

## 3. 一键刷机（推荐）

```bash
cd ~/Documents/oppo/A16-FindX2Pro-最终归档

# ① 只自检 + sha256 校验，不碰设备
./flash-m1.sh --check

# ② 校验通过后，正式刷机（全自动）
./flash-m1.sh
```

脚本会自动完成：`adb/fastboot` 定位 → 镜像 sha256 校验 → 重启到 bootloader →
刷 `boot` → 进入 fastbootd → 刷 `system` / `system_ext` → 重启进系统。

**其它参数**：

| 参数 | 作用 |
|---|---|
| `--check` | 只做环境自检与镜像校验 |
| `--status` | 只打印设备当前所处模式 |
| `--reboot` | 只重启到系统 |
| `--help` | 显示帮助 |

**手动刷机（备用）**：

```bash
fastboot flash boot       正式版刷机包/images/boot_permissive.img
fastboot reboot fastboot
fastboot flash system     正式版刷机包/images/system_diag6.img
fastboot flash system_ext 正式版刷机包/images/system_ext_fix2.img
fastboot reboot
```

**预期**：约 50 秒熄屏动画，约 55 秒后 `sys.boot_completed=1`。

验证：

```bash
adb shell getprop sys.boot_completed      # 期望 1
adb shell getprop ro.build.version.release  # 期望 16
adb shell getprop ro.build.version.sdk      # 期望 36
```

---

## 4. 镜像清单与校验

| 文件 | 大小 (B) | sha256 |
|---|---|---|
| `boot_permissive.img` | 100,663,296 | `1a02f68ff04d581fa1e353a43a0e500a29ba43b4c7a0058c7229bd0e2ba27d11` |
| `system_diag6.img` | 1,072,705,536 | `e3534c0d8dd269fb4226d90f2beb54eadddd9cc13ee00b95cb6e44b6952a6c76` |
| `system_ext_fix2.img` | 979,853,312 | `0c990cc55410c1fb67695695aca8b023f6ea88e4b693ad3668bdc36d88459f1a` |

刷机前务必 `./flash-m1.sh --check` 让脚本自动校验；**校验不通过禁止刷入**。

---

## 5. 本版修复的 Bug

### 5.1 ★ 录像「无法保存视频」（根因 13，2026-10-04 修复）

**现象**：第三方相机（Open Camera）拍照正常，一按录像就提示"无法保存视频"，文件 0 字节。

**真根因**（反汇编实证）：

`/system_ext/lib/libavenhancements.so`（高通 AV Enhancements，随 8T 的 ColorOS 16 移植而来）
在构造音频编码器候选表时读属性 `vendor.audio.c2.preferred`：

- 属性为 `true` → 打印 `CCodec preferred, skip creation of custom OMX audio encoders`，返回空表
  → 系统按 mime 选到 `c2.android.aac.encoder` ✅
- 属性未设置（本机默认）→ 走 `AVConfigHelper::useHwAACEncoder()`（读 `vendor.audio.hw.aac.encoder` = `true`）
  → 把 **`OMX.qcom.audio.encoder.aac`** 塞进候选表 → 该组件**原厂 Find X2 Pro 从未在任何
  media_codecs XML 里声明过** → `CreateByComponentName` 返回 nullptr
  → `ACodec: Unexpected nullptr for codec information` → `Failed to create audio encoder`。

**修复方式**（纯属性，不改任何分区文件）：

安装 Magisk 模块 `正式版刷机包/magisk/aac-c2-preference-fix-v1.zip`，它只做一件事：
把 `vendor.audio.c2.preferred` 设为 `true`（`system.prop` + `post-fs-data.sh` 双保险）。

**安装步骤**：

1. 打开 Magisk 应用 → 「模块」→「从本地安装」→ 选择 `aac-c2-preference-fix-v1.zip`。
2. 重启手机。
3. 验证：

   ```bash
   adb shell getprop vendor.audio.c2.preferred     # 期望 true
   ```

**验证结果**：修复后录制 `VID_20261004_005417.mp4`（19,978,518 B），
MP4 原子解析为 `vide/avc1`（H.264 视频轨）+ `soun/mp4a/esds`（AAC 音频轨）**双轨正常**。

> 若临时应急（不想做模块），可直接：
> `adb shell su -c 'setprop vendor.audio.c2.preferred true'`
> 该属性**每次调用时实时读取**，无需重启任何服务，录像立即生效。

### 5.2 历史根因（完整版见 `过程文件/分析/`）

| # | 故障现象 | 真根因 | 状态 |
|---|---|---|---|
| 1 | 开机卡一屏，`apexd-bootstrap` 不起 | SELinux 三重拦截（user build 禁 `setenforce`） | ✅ boot cmdline 加 permissive |
| 2 | `netd` 启动即崩，网络全死 | `libnetd_updatable.so` 要求内核 ≥ 5.4 | ✅ NOP 三处门限 |
| 3 | `netbpfload` 启动即挂，WiFi/数据全无 | 要求 4.19 内核 ≥ 4.19.236（本机 4.19.157） | ✅ 修补三处 |
| 4 | init 拒绝 `exec_start` | `mkfs.erofs --replace` 丢 xattr | ✅ 构建时补 SELinux 标签 |
| 5 | `/system/apex/` 报签名缺失 | 重打 APEX 丢 v2 签名块 | ✅ 补签 33 个 APEX |
| 6 | `compos.apex` 仍缺签名 | `/system_ext/apex/` 也是独立 APEX | ✅ 补签后重打包 |
| 7 | boot loop 卡 Phase 480 | `locksettings.db` 属主 root:root 0600 | ✅ TWRP 里 `chown system:system` |
| 9 | 原厂一加相机打不开、黑屏 | 供体 `my_product` 调用 SAT 多摄逻辑相机，HAL 报 -38 | ✅ 换 Open Camera |
| 11 | 恢复出厂后反复回 TWRP | 双清未重置 FBE 密钥 | ✅ 见第 6.1 节 |
| 13 | **录像无法保存** | `vendor.audio.c2.preferred` 未开启 | ✅ 见第 5.1 节 |

---

## 6. 注意事项 / 避坑（★ 必读）

### 6.1 ★★ 恢复出厂设置后「反复回 TWRP」怎么避免

**血泪教训**：切勿在系统设置里点"清除所有数据"后，直接在 TWRP 里做简单的「Wipe / 双清」！
TWRP 的双清**不会擦除 FBE 密钥目录** `/data/unencrypted`，旧密钥/旧目录策略与新 `/data`
不匹配，开机就会死循环（第一环报 `enablefilecrypto_failed`，第二环报
`set_policy_failed:/data/cache`）而反复掉回 TWRP。

**正确做法（二选一）**：

**方式 A：TWRP 界面「Format Data」（推荐）**

1. 手机进入 TWRP。
2. 【Wipe】→ 右下角【Format Data】→ 输入 `yes` 确认。
3. 格式化完成后**必须再点【Reboot】→【Recovery】**，让 TWRP 重新识别全新 f2fs 结构。
4. 再次进入 TWRP 后，【Reboot】→【System】正常开机。

**方式 B：Mac 终端 ADB 一键彻底重置**

```bash
# 1. 重置 FBE 密钥与 misc 引导标志
adb shell 'mount -t f2fs /dev/block/sda11 /data 2>/dev/null; rm -rf /data/unencrypted /data/gsi; dd if=/dev/zero of=/dev/block/by-name/misc bs=4096 count=1; sync'

# 2. 清空 data 目录全部旧策略（保留 lost+found）
adb shell 'mount -t f2fs /dev/block/sda11 /data 2>/dev/null; cd /data && for d in *; do [ "$d" = "lost+found" ] || rm -rf "$d"; done; sync'

# 3. 重启进系统
adb reboot
```

### 6.2 相机

- 硬件模组、Qualcomm CamX HAL、cameraserver、ION 共享内存**全部正常**。
- 原厂一加相机之所以打不开，是因为供体 8T 的 `/my_product` 分区强行以 `Camera 6`（SAT 多摄，
  15 pipeline）初始化，与 Find X2 Pro 的相机拓扑不匹配，HAL 返回 `-38 ENOSYS`。
- **已卸载**原厂一加相机及其伴随服务，**保留 Open Camera**（打开主摄 `Camera 0`，实测秒开、
  实时取景、拍照录像全部正常）。

### 6.3 已知问题（不阻塞使用）

| 项 | 说明 |
|---|---|
| 机型标识 | 仍显示 `KB2000 / OnePlus8T`（来自供体 `/my_product/build.prop`） |
| SELinux | 当前为 `Permissive`（移植必需），未回 enforcing |
| `/odm/bin/hw/subsys_daemon` | 在 `libradioapis.so::QmiVsClient::getNecData` 处崩溃（不影响通话/数据） |
| 指纹 | `gaia init_fault=18`（不可用） |
| `reservedsize=128M` | 未生效（f2fs 占满整个分区，与 `checkpoint=fs` 不配套） |

### 6.4 Root 使用

- 已安装 **Magisk v30.7**，`magiskd` 实时运行。
- 授权方式：解锁屏幕 → 打开 Magisk / 爱玩机工具箱 → 弹窗点【允许】。
- **shell 语法**：用 `su -c '命令'`，**不是** `su 0 命令`（后者会被 SIGTERM）。

### 6.5 调试技巧（排障用）

```bash
# 让 MediaCodec 打印组件名（录像/编码排障）
adb shell su -c 'setprop debug.oplus.video.log.enable 5'
adb shell su -c 'setprop ctl.restart media'

# 查看编码器列表
adb shell dumpsys media.player | grep -A2 -i encoder

# init 内建命令的失败原因只在 dmesg，不在 logcat
adb shell dmesg | grep -i 'init: Command'
```

---

## 7. 兜底与回滚

- **回滚 boot**：`过程文件/脚本/restore_boot.sh`（若曾备份原 boot）。
- **回滚到 ColorOS 15 官方**：需 ColorOS 15 官方全量包（当前**未**在本机找到，需另行下载）。
- **分区布局 / 变砖恢复**：见 `分区布局与恢复指南.md`。
- 更完整的排障手册：见 `移植问题与完整排障运维手册.md`。
