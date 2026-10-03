# 先读我 —— 2026 安卓16 · ColorOS 16 移植（Find X2 Pro）

> **新阶段唯一入口。** 换账号 / 换模型后先读这一个文件。
> 建立：2026-09-28
> 上一阶段（LineageOS GSI / 指纹调试 / 各种备份）**已整体归档到 `../2026old/`**，未删除任何文件。

---

## 〇、一句话现状

**目标：把 ColorOS 16（Android 16）从一加 8T 移植到 OPPO Find X2 Pro。**
**进展：方案定稿、补丁已落地、`system.img` 已重建并通过全量校验 —— 接上手机即可刷。**
**下一步：设备接上后跑 `03-脚本/flash_coloros16.sh --check`，再 `--flash`。**

| 里程碑 | 状态 |
|---|---|
| donor 基线确证（8T = ColorOS16 / sdk36 / EROFS） | ✅ |
| 致命内核门限定位（netbpfload @ `0xbdfc`） | ✅ |
| APEX 签名校验**不生效**的判定（4 条独立证据） | ✅ |
| netbpfload 补丁 + APEX 零位移重打包 | ✅ |
| **`system.img` 重建（烘入补丁 APEX）** | ✅ |
| 全量校验（fsck / 超级块 / 4474 条目 / 4422 xattr） | ✅ |
| vendor / odm 替换表 + 刷机脚本 | ✅ |
| **真机刷入** | ⏳ 设备未连接 |

---

## 一、目录结构

```
~/Documents/oppo/
├── 2026-安卓16/            ★★ 本阶段工作区（所有新产出只写这里）
│   ├── 先读我.md           ← 本文件
│   ├── 01-设备快照/        设备实机状态记录
│   ├── 02-移植素材/
│   │   ├── donor_a16/     从 8T A16 提取的关键二进制 + tethering.patched.apex
│   │   ├── x2pro_cos15/   ★ X2 Pro 自己的 vendor.img / odm.img（保留不刷）
│   │   ├── a16_patched/   ★★ 最终交付：重建后的 system.img（唯一需要自建的镜像）
│   │   └── netbpfload_补丁/ Magisk 兜底模块
│   ├── 03-脚本/           分析 / 补丁 / 重建 / 刷机脚本
│   ├── 04-日志/           执行日志
│   ├── 05-报告/           ★ 报告与清单
│   └── 06-应用备份/       已安装应用备份（41 个，5.9 GB）
├── 2026old/               ★ 上一阶段全部归档（12 项，440 MB）
├── donor_8t/              ★★ 8T 的 A16 OTA 整包 + 15 个分区镜像（28 GB）
├── platform-tools/        adb / fastboot / 回滚包 / TWRP / Magisk
├── FindX2Pro_ColorOS_15.0.2_.../   ColorOS 15 成功样例（参考基准）
├── port_8t/               8T vs X2 Pro 的 vendor/odm 差异分析
└── 先读我.md               上一阶段总入口（历史参考）
```

### 归档说明（`2026old/`）

`LineageOS-2026/`、`_udfps_work/`、`opppo16-2026/`、`pif/`、`指纹校准备份/`、
两个 `_backup_*`、`HERMES_*.md`、两个 shell 脚本、`进度报告_2026-09-22_4K与指纹修复.md`

> 其中 `_udfps_work/` 是上一阶段**指纹二进制补丁的完整工具链与结论**，
> 本阶段若涉及指纹仍然要用，需要时从 `2026old/` 取。

---

## 二、设备实机状态（2026-09-28 23:29 实测）

| 项 | 值 |
|---|---|
| 序列号 | `DEVICE_SERIAL`（adb 已连） |
| 型号 | `Kona for arm64` / device `kona` |
| 系统 | LineageOS 23.2 GSI，`23.2-20260524-GAPPS-EXT4-GSI` |
| Android | `16`，`sdk=36` |
| 内核 | `4.19.157-color597-os15pro-perf-gff3981b9a878`（Color597 2025-07-19 自编） |
| Root | **Magisk 30.7:MAGISK:R**（文档旧记 30.2，已升级） |
| fingerprint | `qti/kona/kona:12/RKQ1.211119.001/...`（被 vendor 覆盖） |
| `ro.board.api_level` | 30 |
| `ro.vendor.api_level` | **29** |
| `ro.llndk.api_level` | 202504（= Android 25Q2） |
| `init.svc.bpfloader` | stopped（A16 走 tethering APEX 的 netbpfload） |
| 指纹 | **当前不可用**（待处理） |
| 分区 | **A-only，无 slot** ⇒ 刷坏只能 fastboot / EDL |

---

## 三、★ 核心结论：致命内核门限（补丁点已确定）

8T A16 的 `netbpfload`（在 `/system/apex/com.android.tethering.apex` 内）含硬门限：

```
Android 25Q2 requires kernel 5.4.
```

本机内核 4.19.157 ⇒ 命中 ⇒ **`return 1` 退出**（反汇编确证）。

- ✅ 已确认**不是警告**，是致命退出
- ✅ 已确认 **netd 侧无同类门限**
- ✅ 已确认**无属性后门**（无 `ro.bpf.kver_override`）
- ✅ 已确认**检查在 BPF 加载之前**（`createSysFsBpfSubDir`@`0xc290` / `loadProg`@`0xc528` 都在门限出口 `0xc070` 之后）
  ⇒ **改返回值是掩盖症状，必须改分支**

**★ 最终补丁（单点）**

```asm
0xbdf8: bl   isAtLeastKernelVersion(5, 4, 0)   ; w0=5, w1=4, w2=wzr
0xbdfc: tbz  w0, #0, #0xc0dc                   ; ★ 内核 < 5.4 → 跳致命路径
```

| 偏移 | 原字节 | 新字节 | 说明 |
|---|---|---|---|
| `0xbdfc` | `00 17 00 36` | `1f 20 03 d5` | `tbz w0,#0,#0xc0dc` → `nop` |

其余 7 段 LTS 点版本检查全部**非致命**（只置 `w19=1`），另三条 `cmp+b.ls` 门限 4.19.157 均满足。

### ⚠️ 严重度：不是"没网络"，是"无限重启"

`netbpfload.rc` 里写着：

```
service mdnsd_netbpfload /apex/com.android.tethering/bin/netbpfload
    reboot_on_failure reboot,netbpfload-failed        ← ★
```

门限命中 → 返回 1 → **init 直接重启设备** → bootloop。
**所以这个补丁是必需项，不是优化项。**

### ★ 落地前提已澄清：不需要重签（4 条独立证据）

原本担心"APEX 有签名，改 payload 要重签"。**实测判定：ColorOS 16 上 APEX 校验不生效。**

| # | 证据 | 方法 |
|---|---|---|
| 1 | 全部 7 个分区里 `avbpubkey` 出现 **0 次** | 全镜像 grep |
| 2 | `/system/etc/security/apex`（可信公钥目录）**不存在** | `dump.erofs` |
| 3 | APEX **无 `AVBf` 尾标**（未做 AVB 签名） | 尾部字节搜索 |
| 4 | `apexd` 里 `"trusted"`=0 次、`"security/apex"`=0 次 | `strings` |

⇒ 走 legacy 路径，**改 payload 后重新打包仍可激活**。这是整个方案的地基。

### 落地方式（已实施）

**主方案**：把打过补丁的 tethering APEX **烘进重建的 `system.img`**
（`02-移植素材/a16_patched/system.img`，`compat=0x7 incompat=0x1 blocks=253546`）。

**兜底方案**：`02-移植素材/netbpfload_补丁/Magisk模块_netbpfload内核门限修复/`
（`post-fs-data` 里 bind-mount 覆盖，**必须 `remount,bind,suid`**）。
时序安全：`netd` 属 `class main`，在 `post-fs-data` **之后**启动，不会抢跑。

详细报告：`05-报告/报告-移植方案与刷机流程.md`（★ 主交付）
上一轮报告：`05-报告/报告-移植可行性第一轮-内核门限.md`

---

## 四、Donor 基线

| 项 | 值 |
|---|---|
| 设备 | OnePlus 8T（KB2000 CH） |
| 平台 | **kona / SM8250 —— 与 X2 Pro 同平台** ★ |
| 版本 | `KB2000_16.0.5.701(CN01)` |
| **sdk** | **36 ⇒ Android 16** ✅ |
| 分区格式 | 15 个分区**全部 EROFS** |
| 整包 | `donor_8t/ota_full_8t.zip`（6.68 GiB，完整性已校验） |
| 已解包 | `donor_8t/extracted/*.img` |

---

## 五、铁律（本阶段新增）

- **删大文件前必须验证「副本是否真的完整」**：同名解包目录体积远小于源包时，往往只解了部分分区。
  （本轮因此避免误删 7.6 GiB 的回滚保命包）
- **分片下载残留的判据**：所有分片字节数之和 == 目标文件字节数（差 0），且目标文件通过完整性校验。
- **APEX 不是普通文件**：A16 起 BPF 加载器在 tethering APEX 里，不在 `/system/bin`。
- **查内核门限不要只看 `libbpf_android.so`**：全局门限在 `netbpfload`，前者只有逐 map 的非致命检查。
- **反汇编前先确认 vaddr == file offset**：本二进制 `.text addr=off=0x6000` 成立，
  但换二进制必须重新核对节表，不能沿用。
- macOS 回收站命令在 `/usr/bin/trash`。
- **★ `/tmp` 在本机不可靠**：系统会**中途清理** `/tmp`（实测一次构建期间文件被清掉，
  导致 mkfs 收尾写超级块校验和失败，产出的镜像 `compat=0x6` 而非 `0x7`）。
  **规矩：所有构建/中间产物一律写到项目目录，不用 `/tmp`。**
- **沙箱视图与提权视图 `/tmp` 不互通**：同一批文件，有的命令看得见、有的看不见，
  还会出现"文件明明在、`cp` 却报 No such file"的假警报。
  **判断产物是否存在，以项目目录下的 `ls` + 实际读字节为准，不要相信单条报错。**
- **EROFS 目录块尾部用 `\0` 填充**：内核用 `strnlen` 截断，自写解析器必须
  `name.split(b'\x00',1)[0]`，否则会把 `lsattr\0\0\0` 当文件名（本轮 24 个条目中招）。
- **EROFS xattr 内联体大小** = `icount ? 8 + 4*icount : 0`（不是 `4+4*icount`），
  且 `icount & 0x8000` 表示共享/EA_INITED，此时为 0。
- **重建镜像必须校验 `feature_compat`**：`incompat=0x1` 用 `-zlz4hc,level=9`；
  `incompat=0x3`（big pcluster / 16KB 簇）必须加 `-C16384`。搞混会导致内核不认。

---

## 六、待办

1. ✅ **已释放磁盘**：删除 64 个冗余分片 + 归档区 A15 补丁，**4.7 GiB → 15 GiB**
   （依据：分片拼接 SHA256 == zip SHA256；保命包已复校完好。详见 `04-日志/清理执行日志_20260928.md`）
2. ✅ **已确认** netbpfload 检查点在 BPF 加载之前
3. ✅ **netbpfload 补丁 + APEX 重打包跑通**（零位移，APEX 总大小不变）
4. ✅ **`system.img` 已重建并全量校验通过**（4474 条目仅 1 个文件不同 = 补丁 APEX）
5. ✅ **产出 vendor/odm 替换表 + 刷机脚本** `03-脚本/flash_coloros16.sh`
6. ⏳ **下一步：接上手机 → `--check` → `--flash`**（设备当前未连接）
7. ⏳ 指纹修复（可与移植并行，或先做；参考 `2026old/_udfps_work/`）

### 刷机前最后检查清单

- [ ] 手机已解锁 BL、电量 > 60%
- [ ] `flash_coloros16.sh --check` 全绿
- [ ] 确认能进 fastboot / fastbootd（A-only 无 B 槽，这是唯一退路）
- [ ] 回滚包在手：`platform-tools/FindX2Pro_ColorOS_15.0.2_...zip`（7.6 GB）

---

## 六·补、⚠️ 环境异常记录

用 `python3 - <<'PY'` heredoc 执行删除脚本时出现执行路径歧义：
脚本自身报告断言失败、未输出删除日志，但事后发现文件已被删（回收站为空）。
**推断为沙箱环境与提权环境各执行了一次。**

**规矩**：删除类脚本**先写 `.py` 文件再执行**，不要用 heredoc；
断言失败后**必须重新实测**（`df` / `ls`），不能只信任脚本输出。

---

## 七、关键路径速查

```
adb / fastboot      ~/Documents/oppo/platform-tools/adb | fastboot
root 调用           adb shell '/debug_ramdisk/su -c "…"'
erofs 工具          ~/.workbuddy-ai/tools/erofs/erofs-utils/1.9.4/bin/{dump,fsck,mkfs}.erofs
capstone venv       ~/.workbuddy-ai/binaries/python/envs/default/bin/python
e2fsprogs           /opt/homebrew/opt/e2fsprogs/sbin/
回滚包（勿删）       platform-tools/FindX2Pro_ColorOS_15.0.2_...zip
TWRP                platform-tools/TWRP-13-Compass-Color597-V2.0.img
Magisk              platform-tools/Magisk-30.2.zip
```

### 本阶段自建脚本（`03-脚本/`）

```
sdat2img.py          transfer.list + new.dat → 分区镜像（★ v4 语义：区间是【目标】区间）
analyze_transfer.py  分析 transfer.list 结构
probe_semantics.py   判定 transfer 语义（数据驱动，不靠猜）
br_decompress.py     流式 brotli 解压
erofs_read.py        纯 Python EROFS 只读解析器（probe|ls|cat|tree|size）
erofs_rebuild.py     ★ EROFS 重建（保真 uid/gid/mode/xattr，--replace 换文件）
erofs_compare.py     两个 EROFS 镜像的元数据逐条目对比
tree_compare.py      两棵解包树的 SHA256 + 符号链接 + 权限对比
apex_repack.py       ★ APEX 零位移替换 payload（原地改数据区 + 修正两处 CRC32）
patch_netbpfload.py  netbpfload 补丁
flash_coloros16.sh   ★ 刷机 / 回滚
backup_apps.sh       应用备份
```

### ★ 交付镜像

```
02-移植素材/a16_patched/system.img
    1,038,524,416 字节  compat=0x7 incompat=0x1 blkszbits=12 blocks=253546 inos=4475
    镜像内 tethering APEX md5 = a9b4f5fc50b28d9feae5710c008349e9（补丁版）
    镜像内 /bin/netbpfload md5 = 1e0f9e37e83c1f32de5bb700f84ca299
```

> ⚠️ `super.img.gz`（7.7 GB 整机备份）在移动硬盘
> `/Volumes/电影/其他/数码/oppo/备份_PDEM30_20260918/`，不在本机。需要时先接硬盘。
