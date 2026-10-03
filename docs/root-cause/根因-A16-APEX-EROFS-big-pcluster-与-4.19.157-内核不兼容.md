# 根因结论：Android 16 的 APEX 使用 EROFS big-pcluster，X2 Pro 的 4.19.157 内核挂不上

> ## ⚠️ 2026-10-01 订正：本文的"启动卡点"结论已作废
>
> 设备侧实测推翻了本文的两条核心推断：
> 1. **"卡在 first-stage init 或更早"是错的。** 物证 `persist.sys.boot.reason = reboot,boringssl-self-check-failed`
>    只可能由 init 在 `on init` 阶段按 `reboot_on_failure` 重启时写入 ⇒ init 确实跑到了 `on init`。
> 2. **"面包屑没写 ⇒ 没跑到"是假阴性。** 面包屑用 `/system/bin/sh` 执行，
>    而 `sh` 的 `PT_INTERP=/system/bin/linker64` → 软链到 `/apex/com.android.runtime/bin/linker64`，
>    `/apex` 为空时断链 ⇒ 面包屑**必然**失败，与 init 走到哪无关。
>
> 修正后的卡点：**`apexd-bootstrap` 没能把 APEX 挂到 `/apex`**，
> 由此导致全系统动态二进制不可执行。
> 详见同目录 `根因-修正-卡点已定位-apexd-bootstrap失败与17个apex软链.md`。
>
> 本文其余部分（内核配置对比、erofs 超级块、APEX/AVB 验证等）仍是有效的实测记录，**可以继续引用**。

> 结论时间：2026-09-29
> 现象：ColorOS 16（一加 8T 供体，A16 / SDK 36）刷入 OPPO Find X2 Pro（PDEM30 / kona）后，
> 开机约 **54 秒**掉回 bootloader（fastboot），全程 adb 从未出现。
> 结论：**内核能力缺口**，与 system/vendor/SELinux/AVB 均无关。

---

## 一、一句话结论

A16 的 **32 个 APEX** 内部 `apex_payload.img` 的 EROFS 超级块都带
`feature_incompat = 0x3`（= `LZ4_0PADDING | COMPR_CFGS/BIG_PCLUSTER`，即 16 KB 物理簇）。

X2 Pro 的 `4.19.157-color597` 内核里的 EROFS 驱动是 **4.19 原版**，不认识 `0x2` 位：

```
mount -t erofs → Invalid argument
```

于是：

```
apexd --bootstrap 挂载 bootstrap APEX 失败
  ⇒ /system/etc/init/apexd.rc:18
     reboot_on_failure reboot,bootloader,bootstrap-apexd-failed   ← 全系统唯一的 reboot,bootloader
  ⇒ init 重启到 bootloader ⇒ 表现为「约 54 秒后掉 fastboot」
```

---

## 二、证据链

### 2.1 两个内核的 EROFS 能力对比（决定性）

对内核镜像直接 `strings`：

| 字符串 | X2 Pro `4.19.157-color597` | 8T `4.19.325-KSU_NEXT` |
|---|---|---|
| `big pcluster head1/2 of compact indexes should be consistent for nid %llu` | **无** | **有** |
| `erofs_load_compr_cfgs` | **无** | **有** |
| `erofs_pcluster-%u` | **无** | **有** |
| `erofs_map_blocks_flatmode` / `erofs_raw_access_readpages` | 有 | 有 |

⇒ X2 Pro 内核 = 4.19 原版 EROFS；8T 内核（CAF 4.19.325，2025-12 构建）= 已回移 big-pcluster。

### 2.2 全部 A16 APEX payload 都是 `incompat=0x3`

遍历 `tree/system/apex/*.apex`，解出每个 zip 内 `apex_payload.img` 的 EROFS 超级块：

```
com.android.i18n.apex      incompat=0x3 blksz=4096B blocks=5192   [bootstrap]
com.android.runtime.apex   incompat=0x3 blksz=4096B blocks=1548   [bootstrap]
com.android.tzdata.apex    incompat=0x3 blksz=4096B blocks=150    [bootstrap]
com.android.art.apex       incompat=0x3 blksz=4096B blocks=7205   [bootstrap]
com.android.conscrypt.apex incompat=0x3 blksz=4096B blocks=1085   [bootstrap]
com.android.os.statsd.apex incompat=0x3 blksz=4096B blocks=474    [bootstrap]
…（33 个中 32 个如此；仅 com.android.apex.cts.shim.apex 是测试用的非 EROFS 载荷）
```

**对照**：外层主分区（`system` / `system_ext` / `product` / `my_*` / `vendor` / `odm`，
含 X2 Pro 自己的 `vendor`/`odm`）全部是 `incompat=0x1`。

⇒ **只有 APEX 的 payload 用了 pcluster，外层分区没有。**

### 2.3 设备内核实测（最直接）

TWRP 跑的就是同一个 `4.19.157` 内核，用 loop 设备直接挂载：

| 对象 | `incompat` | 结果 |
|---|---|---|
| `my_region.img`（对照） | `0x1` | ✅ `mount rc=0`，内容正常 |
| A16 `tzdata` 的 payload | `0x3` | ❌ `mount: Invalid argument`，`rc=1` |

### 2.4 `0x2` 位是「真·数据布局」而非纯声明

把 payload 超级块里的 `feature_incompat` 从 `0x3` 手工改成 `0x1` 后：

- 挂载**成功**（`rc=0`）
- 但**逐文件 md5 比对：19 个文件里 14 个内容错误**
  - 多数读成空（`d41d8cd98f00b204e9800998ecf8427e` = 空串的 md5）
  - `telephonylookup.xml` 甚至读出别人的数据（`72ba1585…` vs 正确 `c5fa80f8…`）

⇒ 旧驱动能「挂上」但解压全错。**改标志位这条路不通，payload 必须重建。**

### 2.5 为什么 ColorOS 15 能用、A16 不能

- AOSP 在 **2025 下半年**把 APEX payload 的 mkfs.erofs 参数默认改成带 16 KB pcluster。
- ColorOS 15（Color597 移植包）的 APEX 早于该变更 → `0x1` → 能挂。
- 8T 的 ColorOS 16 构建于 **2026-03** → 全部 `0x3` → 挂不上。
- 旁证：2026-09 那轮 **AOSP A16 GSI（`aosp_arm64-exp-BP2A.250605.031.A3`，2025-06 构建）
  在本机跑到了 `system_server` 常驻、`apex.all.ready=true`** —— 说明只要 payload 不带
  pcluster，这个 4.19.157 内核完全能跑 A16。

---

## 三、已排除的假设（避免重走）

| 假设 | 结论 | 依据 |
|---|---|---|
| SAR / system-as-root 布局不对 | ✗ 排除 | `tree/` 是标准 SAR 根；两个设备的 fstab 都是 `system /system erofs` |
| `my_colorospro` 被删导致 | ✗ 排除 | 该条目带 `nofail` |
| SELinux 策略不兼容 | ✗ 排除 | X2 Pro 与 8T 的 `/etc/selinux/` **逐字节相同**（`plat_pub_versioned.cil` md5 `15de3e03…`，`plat_sepolicy_vers.txt`=`30.0`） |
| AVB / dm-verity 校验失败 | ✗ 排除 | 设备上的 `vbmeta`/`vbmeta_system` 是原厂签名，**A15 用同一份能启动** ⇒ AVB 环境两次运行完全一致，不可能是差异源 |
| APEX 完整性 / AVB footer 丢失 | ✗ 已修 | 33 个 APEX 的 payload 都带合法 `AVBf` footer；重建树里的 `tethering.apex` 与 donor 逐字节一致（`usize=16384000 orig=16244736 vbmeta@16375808+2176`） |
| 外层镜像 EROFS 特性不兼容 | ✗ 排除 | 全部 `compat=0x7 incompat=0x1 blksz=4096`，与 X2 Pro 自己的 vendor 相同 |
| 还有别的二进制带内核版本门限 | ✗ 排除 | 全树 grep `requires kernel` / `Unsupported kernel version` **零命中**（门限只在 tethering APEX 的 `netbpfload` 内） |
| netbpfload 的 25Q2（内核≥5.4）门限 | △ 真存在但**不是**本次元凶 | ColorOS 16 在 8T 上跑的也是同一个 `netbpfload`（8T 内核同为 4.19.325）⇒ 它 `return 1` 对 ColorOS 是可承受的（ColorOS 的 netd 为老内核定制过）。详见 `2026old/LineageOS-2026/04-取证归档/根因-netbpfload-25Q2内核门限.md` |

---

## 四、修复方案：重建 32 个 APEX payload（去 pcluster）+ 自行重签

### 4.1 为什么可以自行重签（已查实）

`apexd` 二进制里确实有公钥比对逻辑，但**只有两条路径**会触发。把 `apexd` 的
源文件名和目录常量挖出来即可定位（`strings system/bin/apexd`）：

```
system/apex/apexd/apexd_brand_new_verifier.cpp   ← "brand-new APEX" 校验
system/apex/apexd/apexd_vendor_apex.cpp          ← vendor/odm APEX 校验

扫描目录 : /system/apex  /system_ext/apex  /product/apex  /vendor/apex  /odm/apex
白名单   : /{system,vendor,odm,product,system_ext}/etc/brand_new_apex
运行时   : /apex/apex-info-list.xml（每次开机重生成，不是持久密钥库）
```

两条触发路径：

| 路径 | 触发条件 | 我们的情况 |
|---|---|---|
| `apexd_vendor_apex.cpp`<br>`" : public key doesn't match pre-installed one"` | APEX 在 `/vendor/apex` 或 `/odm/apex`，比对 `/system` 里的同名包 | ✗ 我们的包在 `/system/apex` |
| `apexd_brand_new_verifier.cpp`<br>`"Brand-new APEX public key doesn't match existing active APEX"`<br>`"No pre-installed public key found for the brand-new APEX"` | `/data` 里的包**没有**预装同名包，须命中 `etc/brand_new_apex` 白名单 | ✗ 我们的包自己就是那个"预装同名包" |

补充实测：

- `/system/etc/security/` 实际只有 `cacerts/`、`fsverity/`、`otacerts.zip` —— **没有任何 apex 公钥目录**
- 每个 APEX 各带一把独立公钥（33 个 `apex_pubkey` 各 1032 字节，互不相同）
- 设备上 `/data/apex/active` **是空的** ⇒ 不存在"新包 vs 已记录公钥"的比对
- 上一轮移植记忆里也有 4 条独立证据表明 ColorOS 16 上 APEX 签名校验不生效

⇒ **预装 APEX 的信任根就是 AVB 保护的 `/system` 本身。替换 payload 签名 +
同步替换包内 `apex_pubkey`，`apexd` 会接受。**

### 4.2 流程（每个 APEX）

```
1. 从 .apex(zip) 取出 apex_payload.img
2. fsck.erofs --extract --xattrs  →  文件树（uid/gid/mode 由源镜像元数据提供，
                                     xattr 由树提供；已验证 security.selinux 能保住）
3. erofs_rebuild.py <源payload> <树> <新payload> --zopts "-zlz4hc,level=9"
   ⇒ incompat=0x1（不传 -C，即 4 KB 簇）
4. avbtool add_hashtree_footer（★ 不是 add_hash_footer，见下）
5. avbtool extract_public_key → 新 apex_pubkey（1032 字节）
6. 重打 zip：替换 apex_payload.img（stored + 4096 对齐）与 apex_pubkey，其余条目原样保留
7. 放回 tree/system/apex/，再用 erofs_rebuild.py 重建 system.img
```

#### ★ 第 4 步：必须 `add_hashtree_footer`，且参数有 4 个坑

实测 33 个原包的描述符**全部**是 `HASHTREE + PROPERTY(apex.key=<包名>)`，
`avbtool info_image` 输出（tzdata）：

```
Hashtree descriptor:
  Tree Offset: 614400   Tree Size: 12288
  Data Block Size: 4096   Hash Block Size: 4096
  Salt / Root Digest: (随机)      FEC size: 0
Prop: apex.key -> 'com.android.tzdata'
```

`apexd` 挂载时要拿 Hashtree 描述符里的 `salt` / `root_digest` / `tree_offset`
去建 **dm-verity** 设备。**用 `add_hash_footer` 只会产出 HASH 描述符、没有哈希树，
`apexd` 取不到 verity 数据，挂载必失败** —— 白刷一轮。

正确命令（4 个参数一个都不能少）：

```bash
avbtool add_hashtree_footer \
  --image <payload> \
  --partition_size 0 \            # 坑① 见下
  --hash_algorithm sha256 \
  --algorithm SHA256_RSA4096 \    # 沿用原包的算法 ID 2
  --key apex_key.pem \
  --rollback_index 0 \
  --do_not_generate_fec \         # 坑② 见下
  --prop apex.key:<包名>          # 坑③ 见下
```

| # | 坑 | 现象 / 原因 |
|---|---|---|
| ① | **用 `--partition_size 0`**（footer 紧贴镜像末尾），不要显式给尺寸 | 布局 = `[EROFS][hash tree 补齐4096][vbmeta 补齐4096][footer 所在4096块]`。原包 tzdata：`614400+12288+4096+4096 = 634880`，与本命令输出**字节级一致**。若显式给 `--partition_size`，avbtool 会用「保守估算的元数据上限」（`max_tree_size + MAX_VBMETA_SIZE 64KB + MAX_FOOTER_SIZE 4KB`，至少 69632 字节）卡尺寸，直接报 `Image size of X exceeds maximum image size of Y` |
| ② | **必须 `--do_not_generate_fec`** | avbtool 默认要生成 FEC，会去调外部 `fec` 程序，本机没有 ⇒ `FileNotFoundError: [Errno 2] ... 'fec'`。原包 `FEC size=0`，说明 apexer 也关了 |
| ③ | **prop 是 `apex.key=<包名>`** | 不是 `com.android.build.apex=true`。实测 33/33 都是 `apex.key` |
| ④ | `--partition_name` **不要给** | 原包 Hashtree 描述符里的 `Partition Name` 是**空**的 |

另外：`avbtool verify_image --key` 要的是 **PEM**，而 `extract_public_key` 输出的是
**AVB 二进制格式**（1032 字节 = `u32 key_bits + u32 n0inv + 512B 模数 + 512B rr`，
无显式指数，隐式 65537）。把二进制喂给 `verify_image` 会报
`Could not find private key of public key from ...`。

#### 顺带排除的担心：`apexd` 不校验 APEX 容器的 JAR 签名

在 `system/bin/apexd` 里 `strings` 搜不到任何 `META-INF` / `CERT.` / `.RSA` / `X509` 字样，
签名相关字符串只有 libavb 那几条（`SIGNATURE_MISMATCH`、
`Signature length does not match key length.` 等）。
⇒ 重签后 `META-INF/CERT.SF`、`CERT.RSA`、`MANIFEST.MF` 全部作废**无影响**。

**前提已验证**：
- xattr 可保（`tree/system/bin/init` → `u:object_r:init_exec:s0`）
- 工具链齐（openssl 3.6.3 / Python 3.13 / erofs-utils 1.9.4 / avbtool 1.3.0）
- 空间够（`qti_dynamic_partitions` 余量约 1.17 GB；payload 去 pcluster 后实测涨约 30–35%）
- fastboot 刷逻辑分区会**自动 `Resizing` 扩容**，不需要手工 resize

### 4.3 计划：一次全量重建 32 个，而不是先做 6 个 bootstrap

原计划是「先重建 6 个 bootstrap APEX，看失败模式是否从『掉 fastboot』变成
『正常重启循环』」。**该计划已被更强的离线验证取代**（见 §七）：

- 离线已把「挂载机制」证到底（真内核挂载 + 逐文件 md5 + xattr 全对），
  这比"6 个包能不能改变失败模式"更直接；
- 而且只做 6 个的话，万一仍然掉 fastboot，**无法区分**是"挂载仍失败"
  还是"apexd 拒绝了自签密钥"——信号是模糊的；全量重建后失败反而更干净；
- `apexd` 的公钥策略已在 §4.1 查实，不需要靠刷机来试。

⇒ 直接全量重建 32 个（`cts.shim` 非 EROFS，跳过），每个包做三层校验，再重建 system.img 刷一次。


---

## 五、回滚（保底）

`FindX2Pro_ColorOS_15.0.2_Pro_2025.1_20250823_By_Color597.zip`（7,606,536,347 B）已校验完整，
其 `dynamic_partitions_op_list` 以 `remove_all_groups` 开头并重建全部 16 个分区（含 `my_colorospro`），
**在 TWRP 里刷入即可完整回到可用的 ColorOS 15**，与本轮布局改动无关。

---

## 六、方法论教训

1. **`strings` 对比内核能力**是判断「内核是否支持某文件系统特性」的快速手段：
   内核里 `fs/erofs/super.c` 的特性名表 / 报错文案就是能力清单。
2. **mount 成功 ≠ 数据正确**。必须做逐文件 md5 比对，否则会把「静默读错」当成「修好了」。
3. **shell 32 位算术会溢出**：`$((sectors*512))` 在 toybox sh 里对 >4 GiB 的分区会溢出
   （`my_stock` 4,385,828,864 B 被读成 90,861,568 B），差点误判分区布局。
4. **`if cmd | sed …; then` 的退出码来自 `sed`**，永远为 0 —— 刷机脚本的成败判断会失效。
   本项目的 `flash_coloros16.sh` 有该缺陷，需改为 `${PIPESTATUS[0]}`。

---

## 七、离线验证结果（决定性）

修法不再停留在"理论上可行"，**已在本机 + 真机内核上把机制证到底**。

### 7.1 单包端到端验证（`com.android.tzdata`）

| # | 验证项 | 方法 | 结果 |
|---|---|---|---|
| 1 | 新 payload 能被 **4.19.157 内核**挂载 | 推到设备 `/tmp/tz_new.img` → `losetup` → `mount -t erofs -o ro` | ✅ `losetup rc=0`、**`mount rc=0`** |
| 2 | **内容字节级正确** | 挂载后 19 个文件逐个 `md5sum`，与本地解包树比对 | ✅ **19/19 全一致** |
| 3 | 关键反例文件 | `etc/tz/versioned/9/telephonylookup.xml` | ✅ `c5fa80f846307b24656f99b1425b47c2`（**改标志位那次得到的是错的 `72ba1585…`**） |
| 4 | SELinux 标签未丢 | `fsck.erofs --extract --path=/etc/tz/tzdata --xattrs` → `xattr -l` | ✅ 文件与目录都是 `u:object_r:system_zoneinfo_file:s0`，与**原 payload 完全相同** |
| 5 | AVB 自洽 | `avbtool verify_image` | ✅ `Successfully verified footer and SHA256_RSA4096 vbmeta struct` + `Successfully verified sha256 hashtree ... for image of 835584 bytes` |
| 6 | 与原包结构同构 | `avbtool info_image` 逐字段比对 | ✅ 除公钥/盐/摘要外**每一项都一致**（Image size 634880、VBMeta offset 626688+2176、Tree Offset 614400+12288、Data/Hash Block 4096、FEC 0、Flags 0、`Partition Name` 空、`Prop: apex.key -> 'com.android.tzdata'`） |
| 7 | `apex_pubkey` 格式一致 | 解析二进制 | ✅ 新旧都是 1032 字节 = `u32 key_bits + u32 n0inv + 512B 模数 + 512B rr` |

> 对照：**「改标志位」那次也 `mount rc=0`**，但 19 个文件里 14 个 md5 是错的
> （多数读成空串 md5 `d41d8cd9…`）。所以第 2 项才是真正的判据。

### 7.2 全量批量重建（32 个包，三层校验）

驱动：`03-脚本/apex_rebuild_all.py`（逐包调 `apex_rebuild.py`，再做 L2/L3）

| 层 | 校验内容 | 结果 |
|---|---|---|
| L1 | 包内自检：payload 首个条目 + STORED + 数据偏移 %4096==0 + `incompat==0x1` + 末尾 `AVBf` + `apex_pubkey` 已替换 + 条目数不变 | ✅ 32/32 |
| L2 | **元数据逐条目**：`walk_meta(原 payload)` vs `walk_meta(新 payload)`，比 (路径, 类型, mode, uid, gid, 非目录 size, 符号链接目标) 全集 | ✅ 32/32，**0 差异** |
| L3 | **内容 + xattr 逐文件**：新 payload 重新解包，与构建用的树比 (路径集合, 普通文件 md5, 符号链接目标, `security.selinux` 等 xattr) | ✅ 32/32，**0 差异** |

汇总：

```
通过 32 / 跳过 1（cts.shim 非 EROFS，本来就不受影响）/ 失败 0
payload 总量: 270,843,904 → 296,972,288 B  (+9.6%, +24.9 MB)
APEX  总量  : 276,082,688 → 301,895,623 B
```

**体积增长只有 +9.6%，远低于按 tzdata 外推的 30–35%** ——
因为 tzdata 是 XML（4 KB 簇下压缩率损失最大），而多数 APEX 是代码。
`super` 组余量约 1.17 GB，完全吃得下。

### 7.3 本轮新增/修正的工具

| 文件 | 作用 |
|---|---|
| `03-脚本/apex_avb_dump.py` | 解析 APEX 内 payload 末尾的 AVB vbmeta，打印描述符（**批量核对 33 个包结构**时用它发现了 `add_hash_footer` 用错） |
| `03-脚本/apex_rebuild.py` | 单包重建（已修正为 `add_hashtree_footer` + 三重自检） |
| `03-脚本/apex_rebuild_all.py` | 批量重建 + L2/L3 校验 + 安装 |
| `03-脚本/build_system_a16.sh` | 从树重建 system.img（含构建前自检 + 收尾超级块/fsck 自检） |
| `07-重建/_avb/avbtool.py` | avbtool 1.3.0，取自 `LineageOS/android_external_avb@lineage-23.2` |
| `07-重建/_avb/apex_key.pem` | 自备 RSA-4096 签名密钥 |
| `07-重建/_apexorig/` | 33 个**原始** APEX 的硬链接备份（零占空间） |

### 7.4 两个脚本坑（本机环境特有）

- **`shutil.rmtree` / `os.remove` 会被「批量删除保护」掐断**：本机有钩子，
  单轮删除超过 50 个文件就报 `SAFE_DELETE_BULK_CONFIRM_REQUIRED` 并**直接终止进程**。
  症状极具误导性：日志停在 `[5/6] 重打 zip…` 或 L3 解包处，看不出跟删除有关。
  **规避：全程不做删除** —— 旧工作区 `os.rename` 改名保留、输出先写 `.tmp` 再 `os.replace`、
  校验树每包独立目录不清理。
- **硬链接备份不能直接 `copy2` 覆盖**：`_apexorig/` 里的备份与树里的原包是同一 inode，
  `shutil.copy2(dst)` 是"打开目标并覆写"，会把备份一起改掉（备份就废了）。
  必须先写 `.new` 再 `os.replace` —— rename 换的是目录项，旧 inode 及其其它硬链接保持原样。

### 7.5 仍未验证的部分（只能靠刷机回答）

`apexd` 在真机上是否接受这套自签包 —— 这一条**无法离线证明**，
但它已由 §4.1 的代码级分析降到很低的概率。刷入后的判据：

| 观察到的现象 | 结论 |
|---|---|
| 正常开机进 UI | ✅ 成功 |
| 开机后**重启循环**（不再掉 fastboot） | ✅ bootstrap 已过 ⇒ 自签被接受，问题在别处（看 `reboot_reason`） |
| 仍然 ~54 秒**掉 fastboot** | ✗ 自签被拒 或 挂载仍失败 —— 下一步在 TWRP 里逐个 loop 挂载排 |

（按 §4.3 的分析，全量重建后"掉 fastboot"这个信号比只修 6 个包时更干净：
挂载机制已在 §7.1 证过，所以只剩"自签被拒"这一种解释。）

### 7.6 最终镜像 `system_a16final.img` 全项校验：26 项全过

`03-脚本/verify_system_a16.py` 对成品镜像做端到端反查（不是看构建日志，而是**从镜像里再读出来对**）。

| 组 | 检查内容 | 结果 |
|---|---|---|
| A | 超级块 `magic` / `compat=0x7` / **`incompat=0x1`** / `blkszbits=12` / 4096 对齐 | ✓ 5/5 |
| B | 元数据全集 vs 源镜像：无丢失条目；新增恰好 2 个；除 APEX size 与 `init.rc`(+4 B) 外零差异；32 个 APEX size 变大 | ✓ 4/4 |
| C | 两个新文件的权限/属主/大小 | ✓ 6/6 |
| D | 反提 `netbpfload`：md5 `1e0f9e37…` + `SELinux=bpfloader_exec` | ✓ 3/3 |
| D2 | 反提 `wb_netbpfload.rc`：`system_file`、服务指向 `/system/bin/netbpfload`、**无 `reboot_on_failure`** | ✓ 4/4 |
| E | 反提 `init.rc`：含 `exec_start wb_netbpfload`，已无裸 `exec_start bpfloader` | ✓ 3/3 |
| F | 镜像内 32 个新 APEX 与 `_apexout/` **逐字节一致** | ✓ 1/1 |

镜像：`02-移植素材/a16_patched/system_a16final.img`，**1,063,677,952 字节**（比 v1 大 25,153,536 B）。
完整日志：`04-日志/verify_a16final_20260929_195555.log`。

其中 B 组那条 `init.rc +4 B` 是**预期内**的：
`exec_start bpfloader`（20 字符）→ `exec_start wb_netbpfload`（24 字符），正好 +4 字节。
脚本里用 `EXPECT_SIZE_DELTA` 显式白名单化，而不是放宽成"允许任意差异"。

### 7.7 第三个环境坑：读文件要用 shell，不要信缓存

本机上 `Read` 类工具会**返回该文件的旧版本**（同一路径，内容与磁盘不一致，行数都不同），
而 `Edit` 是按磁盘实时内容匹配的 —— 于是出现"`Read` 看到的和 `Edit` 要匹配的对不上"。
排查这类不一致时，一律以 shell 取地面真相：

```bash
md5 -q 文件 && wc -l 文件 && grep -n "" 文件 | sed -n 'A,Bp'
```

另外 **BSD grep 不支持 `\|` 交替**，多模式匹配必须写 `grep -nE "a|b"`，
否则会静默返回空结果（看起来像"文件里没有这段"，实际是语法不认）。

## 八、当前状态与恢复步骤（刷机）

### 8.1 已经就绪的东西

| 项 | 状态 |
|---|---|
| `system_a16final.img` | ✅ 已构建并 26 项校验通过 |
| 32 个 APEX | ✅ 已重签为 `incompat=0x1` 并装进树 |
| `netbpfload` / `wb_netbpfload.rc` | ✅ 已注入（内核门限 NOP + 服务不带 `reboot_on_failure`） |
| `03-脚本/reflash_system.sh` | ✅ 默认镜像已改为 `system_a16final.img` |
| `03-脚本/flash_coloros16.sh` | ✅ 改用 `SYSIMG` 变量指向最终镜像（原先硬编码 v1，整包重刷会刷错） |
| `03-脚本/one_shot_flash.sh` | ✅ 新增（见下） |
| 回滚包 ColorOS 15 | ✅ `platform-tools/FindX2Pro_ColorOS_15.0.2_Pro_2025.1_20250823_By_Color597.zip` |

### 8.2 `one_shot_flash.sh` 的两道安全闸门

| 闸门 | 规则 | 退出码 |
|---|---|---|
| 1 | 只有设备处于 **TWRP / bootloader / fastbootd** 才动手；手机**正常开机**时一律不碰 | 2 |
| 2 | **只刷一次**：成功后写哨兵 `04-日志/.a16_system_flashed`，之后调用直接退出（防掉回 fastboot 后被反复重刷）；要重刷需手动删哨兵 | 4 |

其余退出码：`0` 已刷入并开始抓启动日志、`1` 出错、`3` 无设备。

### 8.3 恢复操作（一条命令）

手机进入 TWRP 或 bootloader 后：

```bash
bash ~/Documents/oppo/2026-安卓16/03-脚本/one_shot_flash.sh
```

脚本会自动：进 fastbootd → 刷 `system` → `fastboot reboot` → 抓启动时间线
（输出到 `04-日志/oneshot_<时间戳>/`，含 `timeline.txt`、`dmesg.txt`、`logcat_*.txt`）。

然后按 §7.5 的决策表判读结果。

### 8.4 本次中断原因

准备就绪后设备**不在 USB 上**（`adb devices` 与 `fastboot devices` 均为空；
`ioreg -p IOUSB` 只见 HUB 与网卡，无手机）—— 属物理连接问题，非软件。
镜像与脚本已全部就位，重连即可刷。

### 8.5 刷机与观测结果（★ 关键：不再掉 fastboot）

镜像**已成功刷入**，且**失败模式发生了本质变化**。

**刷入记录**

```
fastboot flash system system_a16final.img
  Sending sparse 'system' 1/4 (262140 KB) OKAY [ 6.394s]   Writing 'system' OKAY [0.998s]
  Sending sparse 'system' 2/4 (262140 KB) OKAY [ 6.325s]   Writing 'system' OKAY [0.874s]
  Sending sparse 'system' 3/4 (262140 KB) OKAY [ 6.450s]   Writing 'system' OKAY [0.745s]
  Sending sparse 'system' 4/4 (252328 KB) OKAY [ 6.104s]   Writing 'system' OKAY [0.639s]
  Finished. Total time: 31.702s        flash rc=0
```

4 段合计 `3×268431360 + 258383872 = 1,063,677,952` 字节，与镜像大小**完全相等** ⇒ 整镜像写入。

**时间线**

| 时刻 | 事件 |
|---|---|
| 19:56:30 | 刷入完成（rc=0） |
| 19:56:33 | `fastboot reboot`（从 fastbootd） |
| 19:56:54 | 设备以 **fastboot** 出现（+20 s）—— 这次是 **fastbootd→bootloader 的重启周期**，不是启动失败（前一次从 TWRP 真启动是 47~54 s 才掉） |
| 19:56:47 | 从 bootloader 再 `fastboot reboot`（正规启动） |
| 19:56:49 → 20:00:02 | **193 秒内既无 adb 也无 fastboot** |
| 20:00:42 → 20:10+ | USB 上稳定出现 `18D1:D00D`，**PID 从未变化、无重启循环** |

**USB 侧实测**（`ioreg -p IOUSB`，并用 pyusb 交叉确认）

```
idVendor  = 6353  = 0x18D1   ← Google/AOSP 通用 Android VID
idProduct = 53261 = 0xD00D   ← AOSP gadget 的「未配置 USB 功能」默认 PID
USB Product Name = "Android"
bcdUSB = 0x210   bcdDevice = 0x100   bMaxPacketSize0 = 64
bDeviceClass = 0（复合设备，类别在接口上）   bNumConfigurations = 1
iManufacturer = 1 / iProduct = 2 / iSerialNumber = 3
UsbDeviceSignature = <d1180dd0000000>
→ 该设备节点下【没有任何 IOUSBHostInterface 子节点】= 一个 USB 功能都没启用
```

**判读 —— ⚠️ 下面的原判读已被推翻，见紧随其后的「更正」**

| 原判读 | 依据 |
|---|---|
| ~~✅ `apexd --bootstrap` 没有失败~~ | ~~唯一的 `reboot,bootloader` 是 `apexd.rc:18`；10 分钟没掉 fastboot ⇒ bootstrap 未报错~~ |
| ~~⚠️ 但系统没走到框架层~~ | ~~10 分钟都是 `0xD00D` + 零接口~~ |

**★★ 更正（2026-09-29 20:22 实测，重要）**

`0x18D1:0xD00D` **就是这台手机 fastboot（bootloader）模式的 USB 描述符**，
不是什么"未配置 USB 功能的 gadget"。在 fastboot 里实测：

```
+-o Android@00140000  <IOUSBHostDevice, registered, matched>
   idVendor  = 6353  = 0x18D1
   idProduct = 53261 = 0xD00D
   USB Product Name = "Android"
   bcdDevice = 256   iSerialNumber = 3   bDeviceClass = 0   bcdUSB = 528
   UsbDeviceSignature = <d1180dd0 32336534 66653663 000000ff 4203>
                         └18d1:d00d┘ └"DEVICE_SERIAL" = 序列号┘
   接口子节点: +-o fastboot@0  <IOUSBHostInterface, registered, matched>
$ fastboot getvar product       → kona
$ fastboot getvar is-userspace  → no（是 bootloader，不是 fastbootd）
```

⇒ 那段"稳定 10 分钟"**其实是手机一直待在 bootloader 里**，
只是当时 fastboot 接口没绑上（ioreg 里确实没有 `IOUSBHostInterface` 子节点），
`fastboot devices` 因此看不见它 —— 于是被误读成"启动到某阶段后稳定"。

**因此必须修正为：**

| 结论 | 依据 |
|---|---|
| ❌ **撤回**「bootstrap 没失败 / 自签被接受」 | 目前**没有任何证据**说明重建后的 APEX 起作用。这次观测等于没观测 |
| ✅ 唯一站得住的一条 | **设备里刷的确实是正确的镜像**（§8.6 已在设备侧逐字段验证） |
| 📌 **教训** | `0xD00D` 在这台机器上是 **fastboot 的特征值**。判"在不在 fastboot"要用 `fastboot devices`，或看 `UsbDeviceSignature` 里的**序列号**与 `fastboot@0` **接口**，**别只看 PID** |

**宿主机侧已试尽的手段（都不足以定性，记录以免重复）**

- `system_profiler SPUSBDataType` —— 沙箱内**静默返回空**；`dangerouslyDisableSandbox` 后仍为空 ⇒ 本机不可用。
- `pyusb`（libusb 后端）—— 能读到设备描述符（`18d1:d00d`、`bcdDevice=0x100`），
  但**字符串描述符与配置描述符读取全部失败**（`Other error` / `no langid`）；
  停掉 `adb` 服务后重试仍失败 ⇒ libusb 在 macOS 上拿不到控制传输，**数不出接口数**。
- `ioreg` 只能给出设备级字段，**不暴露配置描述符的接口列表**。

**当时的下一步（已被 20:15 的实际进展取代）**

1. ~~看手机屏幕~~ → 实际是用户直接进 TWRP 并**格式化了 /data**。
2. ~~若卡住就格式化 /data 重试~~ → **已执行**（20:16 前完成）。
   镜像已刷好、`/data` 已清，接下来只需**真正启动一次并正确观测**（见 §8.8）。

### 8.6 刷入已在设备侧验证通过（2026-09-29 20:15）

在 TWRP 里直接读 `system` 分区，**逐字段与本地构建的镜像完全一致**：

```bash
$ adb shell blockdev --getsize64 /dev/block/mapper/system
1063677952                      # 与镜像大小完全相等 ⇒ fastbootd 已扩容并完整写入
$ adb shell "dd if=/dev/block/mapper/system bs=1 skip=1024 count=96 | od -A d -t x1"
e2 e1 f5 e0 | e4 6c e7 1a | 07 00 00 00 | 0c 00 | 24 00 | 7d 11 00 00 00 00 00 00 ...
```

| 字段 | 设备上读到的 | 期望 |
|---|---|---|
| `magic` | `0xE0F5E1E2` | ✅ |
| `feature_compat` | `0x7` | ✅ |
| **`feature_incompat`** | **`0x1`** | ✅ ← 4.19 可读 |
| `blkszbits` | `12` | ✅ |
| `inos` | `0x117d` = **4477** | ✅ |
| `blocks` | `0x03f667` = **259687** | ✅ |
| `u1` | `0xffff` = 65535 | ✅ |

⇒ **刷机这一步彻底排除**，失败原因不在"刷没刷对"。

### 8.7 若仍然卡住：诊断路线（按代价从低到高）

**① TWRP 里查「init 走到哪了」—— 零成本，信息量最大。**
`/data` 与 `/metadata` 的**文件与时间戳就是进度条**：

| 看到什么 | 说明走到哪 |
|---|---|
| `/data` 仍然空 | 卡在 `on fs` 之前/之中（最可能是 `apexd --bootstrap`） |
| `/data/property/persistent_properties` 存在 | `on post-fs-data` 跑过 |
| `/data/apex/active/` 有内容 | `apexd` 激活过 APEX |
| `/data/system/packages.xml` 存在 | 框架起来了 |
| `/metadata/bootstat` 的 mtime 更新 | 走到了 `boot_completed` |

（实测：我们 19:56 那次尝试后 `/metadata/bootstat` mtime 停在 `05:26`，
说明**连 bootstat 都没写** ⇒ 那次确实没走到框架。）

**② 内核日志这条路是断的。**
`pstore` 是空的（`/sys/fs/pstore/` 里没有 `console-ramoops`）⇒ `last_kmsg` 拿不到。

**③ 终极手段：造一个「早期就开 adb」的诊断镜像**（要重建 + 重刷，约 2 分钟）
往 `/system/etc/init/` 加一个 rc，在 `on post-fs` 里强制
`setprop sys.usb.configfs 1` + `setprop sys.usb.config adb`。
这样即使后面卡住，**adb 也已经能连上**，可以直接 `dmesg` / `logcat` 看到卡在哪。

> 必须在 `on post-fs` 而不是 `on early-init` —— `/dev/usb-ffs/adb` 这个 FunctionFS
> 是 `init.rc` 的 `on init` 阶段挂的；且 adbd 依赖 `/system` 已挂载。

**④ TWRP 侧的环境坑**：toybox **没有 `hexdump`**（用 `od -A d -t x1`）；
`pstore` 空；`/metadata` 是 ext4 且**未被格式化**（只有 `/data` 被格式化）。

### 8.8 ★★ 首要怀疑换人：**内核补丁级别**（4.19.157 vs 4.19.325）

前面 §4 的 EROFS/APEX 分析**在技术上是成立的**（离线已证挂载 + 重签），
但**故障根本没走到那一步**：实测 `/data` 连 `on post-fs-data` 都没到（§8.5）。
把一个个"便宜的假设"逐个实测排除之后，剩下的最强嫌疑是**内核本身太旧**。

#### 8.8.1 当前刷的 `boot` 的真实身份

从设备 dump 出 `boot` 后直接读内核版本串：

```
Linux version 4.19.157-color597-os15pro-perf-gff3981b9a878 (color597@color597)
#72 SMP PREEMPT Sat Jul 19 00:19:32 CST 2025
```

`os15pro` = **Color597 那套 ColorOS 15 移植的内核**。
而 **8T 跑 Android 16 用的是 `4.19.325-KSU_NEXT`** ——
**同样 4.19 大版本，补丁级别差 168 个**。

⇒ 这解释了全部症状：

| 症状 | 解释 |
|---|---|
| 失败极早（`/data` 完全没被碰） | 内核/早期 init 阶段就死了，还没到 `on fs`/`apexd` |
| `boot` cmdline 里有 `reboot=panic_warm` | 是 **kernel panic**，panic 会热重启回 bootloader |
| 同样 4.19 的 8T 却跑得起来 | 8T 的 4.19.**325** 有 A16 需要的回移，4.19.**157** 没有 |

#### 8.8.2 本轮**实测排除**的假设（都做了实验，别再重走）

| 假设 | 实测方法与结果 | 结论 |
|---|---|---|
| SELinux 挡的 | `fastboot boot` 加 `androidboot.selinux=permissive androidboot.debuggable=1` → 仍 27 s 掉回 | ❌ |
| `apexd` 拿包内 `apex_pubkey` 校 payload 的 AVB 签名 | 重建后的包里 `apex_pubkey` **已经是我们的公钥**（md5 `960ea922…`；原始为 `b3e1a9f3…`/`3b089985…`） | ❌ |
| `system` 被 dm-verity 强制校验 | `vbmeta` Flags=**2**、`vbmeta_system` Flags=**3**（3 = HASHTREE_DISABLED｜VERIFICATION_DISABLED）⇒ 验证已关闭 | ❌ |
| vendor API level 太低（`init` 里确有 `"Unexpected vendor api level for "` 字符串） | **8T 的 vendor 也是 `ro.board.api_level=30`**、`ro.vendor.build.version.sdk=30` —— 两台都是 kona、都首发 Android 11 | ❌ |
| `/system/bootstrap-apex` 里留着旧 APEX（硬链接被 `os.replace` 换掉） | 它是个**空目录**（运行时的挂载点），`system/bootstrap-apex` 不存在 | ❌ |
| fstab 把 `system` 声明错了 | fstab 里 `system` **同时声明 ext4 与 erofs** 两行，`wait,avb=vbmeta_system,logical,first_stage_mount,avb_keys=/avb/{q,r,s}-gsi.avbpubkey` | ❌ |

#### 8.8.3 已建立的可复用事实与工具

| 事实 / 工具 | 说明 |
|---|---|
| **刷入内容正确** | `/dev/block/mapper/system` = 1,063,677,952 B；超级块 `magic=E0F5E1E2 compat=0x7 incompat=0x1 blkszbits=12 inos=4477 blocks=259687 u1=0xffff` 逐字段一致 |
| **`fastboot boot <img>` ★ 实测不可用（已更正）** | **★ 2026-09-29 20:40 更正：本条此前记为"已验证可用的安全通道"，是错的。** 实测：原厂设备 `boot.img`、原厂 8T `boot.img`、TWRP、以及内核/ramdisk/dtb/`extra_cmdline` 各改 1 字节的 4 个变体，**全部**返回 `Booting FAILED (remote: 'Failed to load/authenticate boot image: Load Error')`，且**瞬间**返回（设备根本不离开 fastboot）。连 TWRP 都拒 ⇒ 这是**生产版 ABL 关闭了 `fastboot boot` 命令**，不是镜像内容问题。**结论：本机不存在"纯内存、零风险"的实验通道，任何实验都必须真实刷写分区。** |
| **boot 头 v2 的 `extra_cmdline`** | `cmdline[512]@偏移 64`、**`extra_cmdline[1024]@偏移 608`**；直接往 608 写字符串即可追加内核 cmdline，**不用重新打包** |
| **观测判据** | 成功 = USB PID 从 `0xD00D` 变成 `0x4EE1/0x4EE2`，或 adb 出现；失败 = 设备消失后又在 fastboot 出现 |
| **`0xD00D` 的陷阱** | 它是**本机 fastboot 模式的 PID**，不是"未配置功能的 gadget"。判 fastboot 要看 `fastboot devices` 或 `UsbDeviceSignature` 里的序列号 + `fastboot@0` 接口 |
| **设备侧无日志** | `pstore` 空、`logdump`/`misc`/`devinfo` 全零、`logfs` 是空 FAT12、无 `/proc/last_kmsg` ⇒ 拿不到 panic 现场 |
| 新增脚本 | `03-脚本/watch_boot.py`（`--boot-img` 走安全通道、`--reboot` 走正常启动） |
| 本地已有 | 8T 的 `vendor.img`(962 MB)、`odm.img`(234 MB)、`payload-dumper-go`、7.17 GB 的 OTA（内含 `payload.bin`）⇒ 可抽 8T 的 `boot`/`vendor_dlkm`/`system_dlkm` |

#### 8.8.4 下一步可选路线（未执行，待定）

| 路线 | 做法 | 风险 | 预期 |
|---|---|---|---|
| **A. 换 8T 的内核** | 用 `payload-dumper-go` 从 8T OTA 抽 `boot`（4.19.325）→ **`fastboot boot` 试**（零风险）；要长期用则还得配 8T 的 `vendor_dlkm`（模块与内核版本强绑定） | 低（先试不刷） | 若系统能过 ⇒ 坐实是内核；但要处理 DTB/panel 差异 |
| **B. 串口控制台** | cmdline 已有 `console=ttyMSM0,115200n8 earlycon=msm_geni_serial,0xa90000`，接 UART 就能看到 panic 全文 | 需硬件 | 一锤定音 |
| **C. 回滚** | ColorOS 15（可用）或 LineageOS 16 GSI（A16 可用，指纹不可用） | 低 | 恢复可用状态 |

> **方法论教训（重要）**：本项目一度把"APEX EROFS 不兼容"当成唯一根因，
> 并围绕它做了大量（技术上正确的）工作。但**故障从未走到那一步** ——
> 判"故障发生在哪一阶段"必须靠**设备侧证据**（`/data` 的时间戳进度条），
> 不能靠"我们改了哪里，所以就是哪里"。本轮正是靠"`/data` 一个文件都没被写"
> 才把方向从 APEX 拉回到内核。


---

### 8.9 第二轮排查（2026-09-29 20:38–20:55）：通道被封 + 一条决定性证据

#### 8.9.1 ★ 重大更正：`fastboot boot` 在这台设备的 ABL 上**不可用**

上一轮把 `fastboot boot` 记成"零风险的内存实验通道"。本轮做了完整的对照矩阵，结论相反：

| 被测镜像 | 内容改动 | `fastboot boot` 结果 |
|---|---|---|
| `04-日志/dump_202707/boot.img` | 无（原厂，设备自己的） | `Booting FAILED (remote: 'Failed to load/authenticate boot image: Load Error')` |
| `06-校验/8t_boot.img` | 无（8T 原厂） | 同上 |
| `TWRP-13-Compass-Color597-V2.0.img` | 无（以前确实能跑起来） | 同上 |
| `t_cmdline1.img` | `extra_cmdline` 改 **1 字节**（偏移 700） | 同上 |
| `t_kernel1.img` | kernel 改 **1 字节**（偏移 9096） | 同上 |
| `t_ramdisk1.img` | ramdisk 改 **1 字节**（偏移 60459960） | 同上 |
| `t_dtb1.img` | dtb 改 **1 字节**（偏移 61561784） | 同上 |
| `hybrid_a16rd.img` | 换成 A16 ramdisk | 同上 |

**全部瞬间失败，设备始终停在 fastboot、连一次复位都没有。**

关键推理：设备**能**启动它 `boot` 分区里的内容（会跑到 ~25 s 才失败），而那个内容**不是 OPPO 官方签名**的
（是 Color597 的移植版 + Magisk），所以**启动校验必然是关闭的**。
校验都关了还一律 `Load Error` ⇒ **只能是 `fastboot boot` 这条命令本身被生产版 ABL 关掉了**。

> **后果（对本项目影响极大）**：没有"只加载不刷写"的安全通道。**每一次实验都要真实刷分区**，
> 试错成本从"秒级零风险"变成"分钟级 + 需要回滚备份"。

#### 8.9.2 ★ 决定性证据：设备是用 **ColorOS 15 的 ramdisk** 去启动 A16 的 system

Magisk 打补丁时会把**原始 `init`** 压缩存进 ramdisk 的 `backup/init.xz`。把它解出来做哈希比对：

| 来源 | 大小 | sha256(前 16) |
|---|---|---|
| **设备 `boot` 分区里 Magisk 备份出的原始 init** | 2,259,784 | **`031b03db44bd951a`** |
| `06-校验/rd_cos15/init`（ColorOS 15 移植版） | 2,259,784 | **`031b03db44bd951a`** ← **完全一致** |
| `06-校验/rd_ix/init` | 2,259,784 | `031b03db44bd951a` ← 完全一致 |
| `/tmp/rd8t/init`（8T / A16 原厂 ramdisk） | 2,259,624 | `2f4405f0d58d7306` ← **不同** |

⇒ **设备上刷着的 `boot` 就是 ColorOS 15 移植版的 boot**（含 A15 的 `init` + Magisk），
拿它去引导 A16 的 `system`。这就是此前推测的"最强假设"，现在**有了字节级证据**。

**但影响面比想象中小**，这是本轮另一个重要发现：

| 二进制 | 大小 | 含 `Unexpected vendor api level` |
|---|---|---|
| A16 system 的 `/system/bin/init` | 3,528,608 | ✅ 含 |
| 8T(A16) ramdisk 的 `init` | 2,259,624 | ❌ 不含 |
| 设备(A15) ramdisk 的 `init` | 2,259,784 | ❌ 不含 |

两者是**不同的二进制**。Android 11+ 的流程是：
**ramdisk 里的 first-stage `init` 只负责挂载分区，然后 `execv("/system/bin/init", {"selinux_setup"})`**
—— **第二阶段用的 init 是 A16 system 里的那个**。
所以 ramdisk init 版本的影响面**仅限 first-stage 挂载**（用哪份 fstab、挂哪些分区），
而不是"整个 init 都是 A15 的"。这削弱了（但不排除）ramdisk 假设。

#### 8.9.3 本轮新增的排除项（全部有证据）

| 假设 | 证据 | 结论 |
|---|---|---|
| **vendor API level 太低**（A16 init 里有 `Unexpected vendor api level for `） | 查 AOSP 源码：该串在 `system/core/init/property_service.cpp::property_initialize_ro_vendor_api_level()`，只是 **`LOG(ERROR)`**，**不是 FATAL**；触发后把 `ro.vendor.api_level` 重置为 `__ANDROID_VENDOR_API_MAX__` 继续启动 | ❌ 排除（且 8T 的 `ro.board.api_level` 同样是 30） |
| 已刷分区的 **EROFS 超级块** `incompat=0x3` | 逐个读超级块（偏移 1024，字段 @80）：`system/system_ext/product/odm/vendor/my_*` **全部 `0x1`**（只有散落的 `apex_payload.img` 是 `0x3`，非已刷镜像） | ❌ |
| **vendor sepolicy 版本** 不被 A16 支持 | 两边 `/vendor/etc/selinux/plat_sepolicy_vers.txt` **都是 `30.0`**；A16 system 的 `/system/etc/selinux/mapping/` **有 `30.0.cil`(126,395 B) 与 `30.0.compat.cil`** | ❌ |
| 重建时**丢了 bootstrap APEX** | 重建树 `/system/apex/` 与 donor 原版 `donor_8t/extracted/system.img:/system/apex` **逐名一致，都是 33 个**（此前查 `/apex` 是空的运行时挂载点，属路径笔误） | ❌ |
| `system_ext/apex/com.android.compos.apex` 的 `0x3` 导致 bootstrap 失败 | `com.android.compos` **不在** A16 `apexd` 引用的包名集合里（apexd 里只出现 adb/apex/i18n/os.statsd/runtime/sdkext/tzdata/virt/vndk.v 等）⇒ 非 bootstrap 项 | ❌（但仍是真实缺陷，见下） |
| `boot` 头的 `id[32]@576` 是 AOSP 式 SHA1，ABL 会校验 | 实测设备原厂 `boot.img` 的 `id` = `4bfecf1a…`，AOSP 公式算得 `438ea701…`，**对不上**；8T 也一样对不上 ⇒ 是 OEM 自定义摘要，不是 AOSP SHA1 | 排除"可用重算绕过"的思路 |

#### 8.9.4 仍未定位（真正的卡点）

- 故障点在 **`on post-fs-data` 之前**（`/data` 里只有 TWRP 格式化时建的 `media/`、`recovery/`，时间戳对不上系统启动）。
- 设备侧**没有任何日志通道**：`pstore`/`console-ramoops` 为空、`logfs` 是空 FAT12、`logdump`/`misc`/`devinfo` 全零、无 UART。
- 唯一带 `reboot_on_failure reboot,bootloader` 的仍是 `apexd.rc` 的 `apexd-bootstrap` 服务
  （该服务**没有 `on` 触发**，是 init 在代码里启动的）。

#### 8.9.5 待定路线（需人工决策）

| 路线 | 做法 | 成本 / 风险 | 预期 |
|---|---|---|---|
| **A. 诊断版镜像（推荐）** | 重建 `system`：(1) 去掉 `apexd.rc` 里 `apexd-bootstrap` 的 `reboot_on_failure`，让设备**别重启**、停在现场；(2) 在 `on early-init`/`on init` 用 configfs 强制拉起 adb（`sys.usb.configfs=1` + `sys.usb.config=adb`）。刷入后读 `logcat`/`dmesg` | 需刷 `system`（有 `system_a16final.img` 与备份可回滚） | **唯一能拿到真因的路** |
| **B. 顺手修已知缺陷** | 重建 `system_ext`，把 `com.android.compos.apex` 的 payload 从 `0x3` 压到 `0x1` | 1 次刷写 | 若它确实是某个必经挂载点，可能直接好 |
| **C. 换 8T 的 ramdisk（A16 init）** | 把 hybrid boot 刷进 `boot`（X2 Pro kernel + X2 Pro dtb + **A16 ramdisk**） | 1 次刷写，`boot` 有完整备份 | 直接检验 ramdisk 假设；但 A16 fstab 引用的 `op2`/`my_reserve` 在 X2 Pro 上不存在，可能引入新变量 |
| **D. 回滚** | TWRP 刷 `FindX2Pro_ColorOS_15.0.2_Pro_…zip` + 清 `/data` | 低 | 恢复可用状态（用户已预授权） |

> **方法论教训（第二次，比上次更硬）**：
> 上一轮的教训是"别用'我们改了哪里'推断故障点，要靠设备侧证据"；
> 这一轮的教训是 **"先验证实验通道本身是否成立"**。
> 整整一轮的"零风险 `fastboot boot` 实验"设计，建立在一个**从未被真正验证过**的前提上
> （把"用户进过 TWRP"误当成"`fastboot boot` 启动过 TWRP"）。
> 通道一旦不成立，所有基于它的结论都要撤回。
> **任何实验通道，必须先跑一遍"已知会成功"的对照组。**

---

## 8.10 第三轮排查（2026-09-29 21:45–22:05）：根因字节级确证 + 三个"疑似真凶"被逐个排除

### 8.10.1 ★ 决定性铁证：内核 EROFS 函数符号对比

不再依赖"补丁级别差 168"这种间接推断，直接比两个内核里 EROFS 驱动的**函数符号**：

| 符号 | 设备 4.19.157 | 8T 4.19.325 |
|---|---|---|
| **`erofs_load_compr_cfgs`** | **无** | **★ 有** |
| `z_erofs_load_lz4_config` | 无 | ★ 有 |
| `erofs_superblock_csum_verify` | 无 | ★ 有 |
| `z_erofs_extent_lookback` | 无 | ★ 有 |
| `erofs_pcluster-%u`（big pcluster） | 无 | ★ 有 |
| `z_erofs_vle_normalaccess_readpage`（旧命名） | ★ 有 | 无 |
| EROFS 超级块魔数 `0xE0F5E1E2` 常量 | 无（代码里以位运算比较，未落常量） | 无 |

`erofs_load_compr_cfgs` 正是解析 **COMPR_CFGS**（`incompat` 位 `0x2`）压缩配置表的函数。
设备内核里**根本没有这个函数** ⇒ 它在物理上无法解析 `incompat=0x3` 的 EROFS。

**这不是推测，是字节级事实。**

### 8.10.2 8T 供体实测：分区级 `0x1`、APEX 载荷 `0x3`

| 对象 | 分区镜像 superblock | 分区内 APEX 载荷 |
|---|---|---|
| 8T A16 供体 `system/system_ext/vendor/my_*` | **全部 `incompat=0x1`** | **全部 `0x3`** |
| X2 Pro COS15 | `0x1` | `0x1` |

⇒ 结论：**分区镜像本来就是 4.19.157 能读的，根本不需要重建**；
真正读不了的只有**分区内部那 32 个 APEX 载荷**。
所以"重建 32 个 APEX 载荷 `0x3 → 0x1`"是对症的、且是唯一可行的修法。
（8T 自己那套 A16 之所以能跑，是因为它的 4.19.325 内核 backport 了现代 EROFS。）

### 8.10.3 重建后的 32 个 APEX：AVB/dm-verity 全量校验通过

补上了之前**从未做过**的一项验证：apexd 是靠 Hashtree 描述符建 dm-verity 来挂载 APEX 的，
而 `fsck.erofs` **只校验 EROFS 文件系统本身、完全不看哈希树** —— 那是个盲区。

用权威 `avbtool info_image` 取参数 + **独立重算 dm-verity 哈希树根摘要**：

| 包 | image_size | tree_size 自洽 | 重算 root_digest vs 描述符 |
|---|---|---|---|
| 原始（对照组，8T 靠它开机） | 16244736 | ✓ 32 块 × 4096 = 131072 | **★ 一致** |
| 重建 | 17760256 | ✓ 35 块 × 4096 = 143360 | **★ 一致** |

**全量 32 个包：32 通过 / 0 失败。**

至此重建工作在三个独立维度上均被证明健全：
EROFS 文件系统（`fsck` 干净）、文件清单（与原包逐条一致）、AVB 哈希树（根摘要重算一致）。
⇒ **APEX 重建不是失败原因。**

### 8.10.4 ★ 修正两处旧结论（都是错的）

**(1) "系统里唯一带 `reboot_on_failure reboot,bootloader` 的只有 `apexd.rc`" —— 错。**
全树扫描实际有 **7 处** `reboot_on_failure`：

```
system/etc/init/apexd.rc:8        reboot,apexd-failed
system/etc/init/apexd.rc:18       reboot,bootloader,bootstrap-apexd-failed   <- 唯一回 bootloader 的
system/etc/init/netbpfload.rc:70  reboot,netbpfload-missing
system/etc/init/hw/init.rc:1002   reboot,boringssl-self-check-failed
system/etc/init/hw/init.rc:1009   reboot,boringssl-self-check-failed
system/etc/init/hw/init.rc:1016   reboot,boringssl-self-check-failed
system/etc/init/hw/init.rc:1023   reboot,boringssl-self-check-failed
```

其中 `boringssl_self_test32/64` 是在 **`on init`** 由 `init.boringssl.zygote64_32.rc` 拉起的
（`on init && property:ro.product.cpu.abilist32=*` → `exec_start boringssl_self_test32`），
**比 bpfloader（late-init）更早**，是排在 `apexd-bootstrap` 之后的第二个嫌疑犯。

**(2) `03-脚本/apex_avb_dump.py` 的 HASHTREE 解析器是坏的。**
它把 `hash_algorithm` 当"u32 长度 + 字符串"，而真 `avbtool.py:1432` 里
`hash_algorithm` 是**定长字段**，长度字段（`partition_name_len`/`salt_len`/`root_digest_len`）
在其**之后**。从第一个字段起整体错位，打印出 `image_size=69770609852153856` 这类垃圾值。
**上一轮"AVB 已验证"的结论是拿这个坏解析器做的，属于假阳性。**
已重写为「委托权威 `avbtool info_image` + 独立重算哈希树根摘要」，并新增 `tree_size` 自洽检查。

### 8.10.5 级联关系：`bpfloader` 那条路是症状，不是根因

`/system/etc/init/netbpfload.rc` 里是**占位版**：
`service bpfloader /system/bin/false` + `updatable` + `reboot_on_failure reboot,netbpfload-missing`，
文件自己注释写着 "most of the below settings are irrelevant **unless the apex is missing**"。

A16 上真正生效的是 tethering APEX 内的 `etc/netbpfload.35rc`：
`service bpfloader /apex/com.android.tethering/bin/netbpfload` + `override` + `reboot,bpfloader-failed`
（APEX 的 `overrides` 机制会用它覆盖 `/system/etc/` 里的同名文件）。

⇒ 只有 **APEX 没挂上** 时，才会跑到 `/system/bin/false` 然后 `reboot,netbpfload-missing`。
所以「堵住 bpfloader」只堵住了一个**下游症状出口**；上游（APEX 挂不上）才是根因。

### 8.10.6 本轮新建的诊断版镜像

`03-脚本/make_diag.py`（生成补丁）+ `03-脚本/build_system_diag.sh`（重建）
→ 产出 `02-移植素材/a16_patched/system_diag.img`（1,063,690,240 字节，超级块正常、`fsck` 干净、5 个补丁文件逐字节验证一致）

做了 6 件事：

1. **注释掉全部 7 处 `reboot_on_failure`** ⇒ 失败也不再重启，设备停在现场
2. **8 个关键 trigger 各插一次面包屑** `exec_start wb_diag_<stage>`
   （`early-init` / `postapexd` / `init` / `late-init` / `early-fs` / `post-fs` / `post-fs-data` / `boot`）
   ⇒ 形成 `trace.txt`，能精确知道 init 走到哪一步死的
3. **给 `apexd` 与 `apexd-bootstrap` 加 `stdio_to_kmsg`**
   ⇒ 这条极关键：apexd 在 bootstrap 阶段跑时 logd 还没起来，liblog 回退到 stderr，
     而 init 默认把服务 stdio 接到 `/dev/null`。不加这条，**apexd 的报错全部丢失，我们一直在盲飞**
4. **倾倒内容**写到 `/metadata/wbdiag/`（ext4、`first_stage_mount`、双清不受影响、TWRP 可直接读）：
   `/proc/cmdline`、`/proc/version`、`/proc/filesystems`、`/sys/module/erofs`、`getprop`、
   `/proc/mounts`、`/system/apex` 与 `/apex` 列表、`/dev/block/mapper`、`/dev/block/by-name`、
   过滤后的 `dmesg`（含 init 与 apexd 的报错）、全量 `dmesg`
5. **`setenforce 0`**（放在 `on early-init` 开头）⇒ 若这样就能起来，根因就是 SELinux 策略
6. **`ro.adb.secure=0`** ⇒ 万一 adb 起来，不需要授权弹窗

### 8.10.7 当前阻塞

设备**不在 USB 上**（`fastboot devices` / `adb devices` / `ioreg` 三处均为空；
只剩无关的 USB 集线器与无线接收器）。上一轮把 `/tmp/hybrid_A16init_x2fstab.img`
（X2 Pro 内核 + X2 Pro dtb + A16 ramdisk init + X2 Pro fstab）刷进 `boot` 后，
设备就从 USB 上彻底消失了 —— 那个混合 ramdisk 在**第一阶段 init** 就挂了。

⇒ **恢复顺序必须是先 `boot` 后 `system`**：先刷回已验证可用的
`04-日志/dump_202707/boot.img`（sha256 `fc60a85e05b6d953`）把 USB 救回来，
再刷诊断版 `system`。已封装成 `03-脚本/flash_diag.sh` 一条命令。

> **方法论教训（第三次）**：**"验证"这件事本身必须被验证。**
> 这一轮里，"APEX 重建已验证通过"这个结论，是建立在一个字段布局写错的解析器上的。
> 凡是自己手写的二进制解析器，**必须先用一个已知正确的样本做对照组**——
> 就像本轮用原始包验证哈希树算法那样。
> 对照组不过，解析器的输出一律视为无效。

---

## 8.11 第四轮（2026-09-29 22:05–22:30）：`bootstrap-apex` 线索关闭 + 揪出 `system_ext` 丢 xattr 的真 bug

### 8.11.1 `bootstrap-apex` 是空目录 —— 线索关闭

上一轮末尾发现 `system.img` 根目录里除了 `apex` 还有 `bootstrap-apex`，
一度怀疑它是"第二份 bootstrap APEX 副本、仍是 `0x3`"，能直接解释
`reboot,bootloader,bootstrap-apex-failed` 这个症状。

实测（`dump.erofs --ls --path=/bootstrap-apex`）：

```
donor system.img      : Links: 2  →  只有 . 和 ..
diag  system_diag.img : Links: 2  →  只有 . 和 ..
```

**两侧都是空目录**（`Links: 2` 意味着里面没有任何子目录）。
它只是个占位挂载点，供体 8T 自己也靠它正常开机。
⇒ **此线索关闭，`bootstrap-apex` 与本次失败无关。**

同时把"到底哪些分区有 apex 目录"这件事彻底扫清了：

```
system.img       ROOT: ... apex ... bootstrap-apex ... system_ext ... product ... vendor ...
system_ext.img   ROOT: ... apex ... opex ...
其余 12 个分区   都没有 apex 目录（product/vendor/odm/my_* 的根里都没有）
```

`system.img` 根里那些 `system/ product/ vendor/ odm/ my_*` 是 system-as-root 的
挂载点/软链，真实内容来自各自独立分区。所以**全设备只有两个 apex 源目录**：

| 镜像 | apex 目录 | 内容 | 状态 |
|---|---|---|---|
| `system.img` | `/apex` | 33 个 APEX | 32 个已重建为 `0x1`（第 33 个是非 EROFS 的 cts.shim，apexd 会跳过） |
| `system_ext.img` | `/apex` | `com.android.compos.apex` + `com.android.vndk.v30.apex` | compos 已重建为 `0x1`；vndk.v30 的 payload 是 **ext4**（`magic=0x260`），不受 COMPR_CFGS 影响 |

### 8.11.2 真 bug：`system_ext` 重建后**全部 SELinux 标签丢失**

`build_system_ext_fix.sh` 第一版跑完，日志里有一行很容易被忽略的细节：

```
tar 写出: .../system_ext_fix.img.tar (1677916160 字节), xattr 记录 0 条
```

**`xattr 记录 0 条`。** 顺着查下去：

| 文件 | 原镜像 | 重建后 |
|---|---|---|
| `system_ext.img:/apex/com.android.compos.apex` | `Xattr size: 16` | **`Xattr size: 0`** ← 标签没了 |
| `system.img:/system/bin/init`（对照，走 system 树） | `48` | `48` ✓ |

再直接数一遍两个解包树：

```
07-重建/tree_system_ext   有 xattr 的文件数：0        ← 全丢
07-重建/tree              抽样 2000 个文件：1970 个有 xattr   ← 正常
```

**根因**：`tree_system_ext` 当初是用 `fsck.erofs --extract`（**漏了 `--xattrs`**）
临时解出来的，没有脚本、没有留痕。`erofs_rebuild.py` 的 xattr 只从目录树文件上读，
树里没有 ⇒ 写出的镜像里 **3821 个条目（418 目录 + 3397 文件 + 6 符号链接）全部无标签**。

**连带症状**：超级块 `compat` 从 `0x7` 掉到 `0x3`。
`compat` 位 `0x4 = XATTR_FILTER`，是 `mkfs.erofs` 检测到有 xattr 才会开的。
所以 `compat=0x3` **不是独立问题，而是 xattr 丢失的指纹**。
（`compat` 属于"可安全忽略"位，单看它不影响挂载 —— 但它是个好用的体检指标。）

**影响面评估**：上一轮的全量刷机用的是**未重建的供体 `system_ext.img`**（带 xattr，
只是 compos 是 `0x3`），所以这个 bug **不会解释之前的失败**；
但如果照原样把重建版刷进去，会新增一个「system_ext 全体文件无 SELinux 标签」的故障。

**修复**：
1. `build_system_ext_fix.sh` 增加**步骤 0**：`fsck.erofs --extract=<tree> --xattrs <src>`，
   并断言 xattr 覆盖率 > 0，否则直接中止。
2. 收尾校验新增：xattr 抽查 + `compat` 必须回到 `0x7`，不满足则 `exit 1`。
3. `erofs_rebuild.py` 增加通用防护：若 `xattr 记录 == 0` 而源镜像根目录带 xattr，
   直接报错退出（`exit 2`）并提示正确解包命令。
4. 清空旧树不能用 `rm -rf`（3000+ 文件会触发宿主批量删除防护钩子），
   改成**旧树改名让位** `mv tree_system_ext tree_system_ext.old.<ts>`。

### 8.11.3 顺手修掉一个方法论缺陷：`dump.erofs` 的 `Xattr size` 不能当等值判据

对比 APEX payload 时发现：**同一个 SELinux 标签，原包报 `Xattr size: 16`，
重建包报 `48`**。原因是 xattr 可以内联存在 inode 里、也可以放共享 xattr 区，
两种编码的字节数不同。**数值不等 ≠ 标签不同。**

于是新建 `03-脚本/verify_xattr.py`，改用可靠判据：

```
fsck.erofs --extract=<tmp> --path=<p> --xattrs <img>
→ ctypes listxattr/getxattr 读出真实 (name, value) 集合 → 逐条比对
```

用它重验：

- **`system_diag.img` vs 供体 `system.img`**：抽样 24 个文件（覆盖
  `/system/bin`、`/system/etc/init/hw`、`/system/framework`、`/system/priv-app`）
  → **24/24 一致**（`system_file` / `init_exec` / `system_linker_exec` / `system_suspend_exec` 等标签逐条相符）
- **32 个重建 APEX payload**：逐个与原包比对 `/`、`/bin`、`/etc`、`/lib64` 下的文件
  → 见 8.11.4

> **方法论教训（第四次）**：**别拿"看起来像度量"的数字当判据。**
> `Xattr size` 只是个存储尺寸，跟"标签对不对"没有一一对应关系。
> 判断语义一致性，必须把语义内容解出来比 —— 就像这里解出 `security.selinux` 的实际值。

### 8.11.4 本轮产出与状态

| 产物 | 说明 |
|---|---|
| `02-移植素材/a16_patched/system_ext_fix.img` | 重建版 `system_ext`（compos → `0x1`，**带 xattr**，`compat=0x7`） |
| `03-脚本/verify_xattr.py` | 新的可靠 xattr 校验器（真实内容比对） |
| `03-脚本/build_system_ext_fix.sh` | 加步骤 0（带 xattr 解包）+ xattr/compat 断言 + 旧树改名让位 |
| `03-脚本/erofs_rebuild.py` | 加「xattr 全丢则报错」通用防护 |
| `07-重建/tree_system_ext.old.<ts>` | 旧的**无 xattr** 树，确认无用后可删 |

**当前阻塞仍然是设备不在 USB 上。** 拿到设备后的动作不变：
`03-脚本/flash_diag.sh`（先恢复 `boot` → 刷诊断版 `system` → 刷修好的 `system_ext`）。


---

## 8.12 第四轮补充：能不能刷 8T 的 TWRP / recovery？（结论：不能，已字节级证伪）

起因：用户问"不能刷 8t 的类似 TWRP.img？"。

### 8.12.1 8T 到底有没有 recovery 分区？—— 有

`payload_extract.py <ota_full_8t.zip> list` 显示 8T 的 `payload.bin`
（`OTA_TARGET_VERSION=KB2000_16.0.5.701(CN01)`，version 2，manifest 267072 字节）
共 **45 个分区**，其中**确实含 `recovery`**（50 个操作，压缩 35,794,591 字节）。
⇒ **8T 是 A-only（有独立 recovery 分区），和 X2 Pro 一样。**

已抽出：`06-校验/8t_recovery.img` = **104,857,600 字节**（kernel 47,511,568 / ramdisk 19,885,699）。

### 8.12.2 但刷过去**完全没用** —— 它的内核和 X2 Pro 的一样老

对 8T recovery 的内核做符号检查：

| 符号 | 8T recovery (4.19.157-perf+) | X2 Pro boot (4.19.157-color597-os15pro-perf) |
|---|---|---|
| `erofs_load_compr_cfgs` | **★没有** | **★没有** |
| `z_erofs_load_lz4_config` | **★没有** | **★没有** |
| `erofs_superblock_csum_verify` | **★没有** | **★没有** |
| `erofs_build_cache_strategy` | 有 | 有 |

8T recovery 的内核版本串：
```
Linux version 4.19.157-perf+ (root@dg02-pool06-kvm76) (clang version 10.0.7 for Android NDK, GNU ld ...)
```

⇒ **8T 的 recovery 内核同样挂不上 `incompat=0x3` 的 A16 APEX。刷到 X2 Pro 上一点忙都帮不上。**

### 8.12.3 而且它不是 TWRP

解出 recovery ramdisk（gzip → cpio，55,791,360 字节，708 个条目）：
顶层是 `init.recovery.qcom.rc`、`plat_file_contexts`、`apex/`、`my_*` 等标准 recovery 布局，
**搜 `twrp` 命中 0 条** ⇒ 是 **OPPO 官方 recovery**，功能只有 "Apply update from ADB / wipe"，
远不如手上这个 `TWRP-13-Compass-Color597-V2.0.img`。

**另外风险**：8T recovery 里的 dtb 是 8T 的（1080p 面板），X2 Pro 是 1440p 面板
⇒ 刷过去很可能黑屏。

### 8.12.4 顺带纠正一个可能的误解：带 4.19.325 的是 `boot`，不是 `recovery`

| 镜像 | 内核版本串 | 有 `erofs_load_compr_cfgs`？ |
|---|---|---|
| 8T `boot`（OTA，= 本地 `06-校验/8t_boot.img`，sha256 前16位 `2df89224299c7ba3`） | `4.19.325-KSU_NEXT-v20251208 (bruce@bruce-PC)` | **有** |
| 8T `recovery` | `4.19.157-perf+ (root@dg02-pool06-kvm76)` | 没有 |
| X2 Pro `boot`（`04-日志/dump_202707/boot.img`） | `4.19.157-color597-os15pro-perf-gff3981b9a878` | 没有 |

注意：8T 的 `boot` 是**社区 KSU 内核**（"bruce@bruce-PC"，2025-12-08 编），
不是 OPPO 官方编译的 —— 说明这个 OTA 包被人换过 boot。

### 8.12.5 新事实：两机的 vendor 内核模块 vermagic **完全相同**

```
8T    vendor /lib/modules/audio_adsp_loader.ko : vermagic=4.19.157-perf+ SMP preempt mod_unload modversions aarch64
X2Pro vendor /lib/modules/audio_adsp_loader.ko : vermagic=4.19.157-perf+ SMP preempt mod_unload modversions aarch64
```

两机的 vendor 模块都是 `4.19.157-perf+`，说明**同源（OPPO 4.19.157 树）**，模块可互换。

而两个 boot 内核各自期望的 vermagic 是：

```
8T    boot : 4.19.325-KSU_NEXT-v20251208 SMP preempt mod_unload modversions aarch64
X2Pro boot : 4.19.157-color597-os15pro-perf-gff3981b9a878 SMP preempt mod_unload modversions aarch64
```

⇒ 把 8T 的 4.19.325 内核刷到 X2 Pro，其期望 vermagic 与 X2 Pro vendor 模块的
`4.19.157-perf+` 对不上，**内核模块会加载失败**（显示/触控/WiFi 依赖的模块全废）；
再加上 dtb 是 8T 的 ⇒ 黑屏。

> ⚠ **本节结论已在 §8.13.1 被推翻（保留原文以便追溯）。**
> 实测：**两台设备的内核 vermagic 都不等于自己模块的 vermagic，却都正常开机**
> ⇒ 这条链路上 vermagic 校验并未实际拦截，**不能**用它否掉换内核。
> 真正让整包 8T boot 起不来的是 **dtb 不匹配**（8T 的 dtb 描述 8T 的板子/面板）。
> 正确做法见 §8.13：**换内核、保留 X2 Pro 的 dtb**。

**实测佐证**：`04-日志/boot_8tA16boot/` 里，`fastboot boot 8t_boot.img` 返回
`Booting OKAY`，但 **0.1 秒后设备就重新枚举回 fastboot**，观测 8 分钟内反复掉回 bootloader。
⇒ **8T 的 boot 在 X2 Pro 上根本起不来。**

### 8.12.6 结论

| 方案 | 可行性 | 原因 |
|---|---|---|
| 刷 8T 的 **recovery** | ✗ | 内核同为 4.19.157 且缺 `erofs_load_compr_cfgs`，对挂 A16 APEX 零帮助；非 TWRP；dtb 不匹配有黑屏风险 |
| 刷 8T 的 **boot**（4.19.325） | ✗（整包）／**待验证（换核）** | **整包**带 8T 的 dtb → 黑屏，实测掉回 bootloader。但"vermagic 不匹配"这条理由**已在 §8.13.1 撤回**；改为"只换内核、保留 X2 Pro dtb"的混合 boot（§8.13）**尚未验证，是当前要做的实验** |
| 刷 8T 的 **TWRP** | ✗ | 不存在 —— OTA 里只有官方 recovery，没有第三方 TWRP |
| **重建 APEX 让 4.19.157 能挂** | ✓ | **唯一可行，且已全部完成**（32 个 + compos，含 xattr 修复） |

⇒ **维持现路线**。设备回到 bootloader 后跑 `03-脚本/flash_diag.sh` 即可。

---

## 8.13 换内核路线：能不能把 8T 的 4.19.325 内核刷到 X2 Pro？

起因：用户追问 ——
> "X2 Pro 的 4.19.157-color597 内核，就不能刷 4.19.325-KSU_NEXT-v20251208 内核？
>  我记得 rec 是可以刷的"

这一问戳中了我 §8.12.5 里的一个**过度断言**，先纠正。

### 8.13.1 ★ 纠正：vermagic 不匹配**并不能**否掉换内核

§8.12.5 我写过"内核 vermagic 与 vendor 模块的 `4.19.157-perf+` 对不上 ⇒ 模块会加载失败"。
这个推论**站不住**，因为**两台设备本来就在 vermagic 不匹配的状态下正常开机**：

| 设备 | 内核版本串（boot 里） | vendor 模块 vermagic | 实际状态 |
|---|---|---|---|
| 8T | `4.19.325-KSU_NEXT-v20251208` | `4.19.157-perf+` | **正常开机** |
| X2 Pro | `4.19.157-color597-os15pro-perf-gff3981b9a878` | `4.19.157-perf+` | **正常开机** |

⇒ 两台机器的 **内核 vermagic 都不等于自己模块的 vermagic**，却都能开。
说明这条设备链路上 vermagic 校验**没有实际拦截**（Android 的 libmodprobe 在这些 vendor
模块上是放行的，或被强制加载）。
**结论：vermagic 不匹配不能作为"换内核必然失败"的理由。** 我之前的判断过强，撤回。

### 8.13.2 真正让 8T 内核起不来的，是 **dtb**（不是 vermagic）

`boot.img`（header v2）是**四段独立**结构：

```
[header 4096] [kernel] [ramdisk] [second?] [dtb] [AVB vbmeta + AVBf footer]
                 ↑ 偏移 = page_size      ↑ 各自独立，由 ABL 分段读取后交给内核
```

两个 boot 的头部参数**完全一致**：

| 项 | X2 Pro boot | 8T boot |
|---|---|---|
| `kernel_addr` | `0x8000` | `0x8000` |
| `ramdisk_addr` | `0x1000000` | `0x1000000` |
| `tags_addr` | `0x100` | `0x100` |
| `page_size` / `header_version` | `4096` / `2` | `4096` / `2` |
| kernel 段形态 | 未压缩裸 arm64 `Image` | 未压缩裸 arm64 `Image` |

两段的 `kernel` 段里**都不含 FDT magic** ⇒ dtb 是**独立的一段**，
由 ABL 读出来、按自己的方式传给内核。**所以换内核根本不需要动 dtb 和 ramdisk。**

而 §8.12.5 的实测 `fastboot boot 8t_boot.img` 之所以掉回 bootloader，
是因为那是**整包 8T boot**，带的 dtb 是 8T 的：

| boot | dtb 段 | 内容 |
|---|---|---|
| X2 Pro | 522,926 字节 | **1 个 FDT**（OPPO kona，含 `oplus,dtsi_no`；无 `Find X2`/`PDEM30` 字样） |
| 8T | 13,102,651 字节 | **约 25 个 FDT** 的多机型 blob（8T 是通用包） |

⇒ **8T 的 dtb 描述的是 8T 的板子/1080p 面板，喂给 X2 Pro 自然黑屏/起不来。**

### 8.13.3 于是做了「换核不换板」的混合 boot

新增 `03-脚本/build_hybrid_kernel.py`：从 A 拿 kernel 段、从 B 拿 ramdisk 段、从 B 拿 dtb 段，
拼成一个新的 boot.img（保持骨架的**文件大小与 AVB 页脚结构不变**，只改写头部偏移 8/16/1648）。
CLI：

```
build_hybrid_kernel.py <内核来源 boot> <骨架 boot> <输出> \
    [--ramdisk-from <boot.img|base|kernel-src|file:PATH>] [--dtb-from ...]
```

产出两个混合 boot（**段级 sha256 前 16 位**证明"只换了内核"）：

| 镜像 | kernel | ramdisk | dtb |
|---|---|---|---|
| X2 Pro 原 boot | `69cbcaac` | `b11e4042` | `b6ec57c6` |
| **混合 A** `hybrid_8tkernel.img` | **`3e043603`**（8T 4.19.325） | `b11e4042`（X2 Pro） | `b6ec57c6`（X2 Pro） |
| **混合 B** `hybrid_8tkernel_a16rd.img` | **`3e043603`**（8T 4.19.325） | `4d13fe66`（8T/A16） | `b6ec57c6`（X2 Pro） |
| 8T 原 boot | `3e043603` | `4d13fe66` | `bb180cd9` |

- 混合 A = 8T 内核 + **X2 Pro 自己的 ramdisk** + X2 Pro dtb ⇒ **变量最少**，最干净地检验"内核"这一个假设。
- 混合 B = 8T 内核 + A16 的 ramdisk + X2 Pro dtb ⇒ 顺带试 A16 的 first-stage init。

两个都是 **100,663,296 字节**（与 X2 Pro 原 boot 同尺寸，页脚结构未破坏）：
`hybrid_8tkernel.img` sha256 前16位 `e44757ddde11e479`；
`hybrid_8tkernel_a16rd.img` sha256 前16位 `ffb30d0446bce903`。

预检实测：

```
混合 A 内核 : Linux version 4.19.325-KSU_NEXT-v20251208 (bruce@bruce-PC) ... #1 SMP PREEMPT Mon Dec 8 13:44:23 CST 2025
             含 erofs_load_compr_cfgs   : True      ← X2 Pro 内核这里是 False
             含 z_erofs_load_lz4_config : True      ← X2 Pro 内核这里是 False
```

### 8.13.4 ★ 刷机顺序必须改：实验内核要放**最后**（新踩的坑）

`system` / `system_ext` 是**逻辑分区**，只能在 **fastbootd**（用户空间 fastboot）里刷；
而 **fastbootd 是由"能启动的 boot ramdisk"提供的**。
原来的 `flash_diag.sh` 把 `--kernel` 的实验内核放在**步骤 2**，
于是会变成：刷实验内核 → 想进 fastbootd → **实验内核起不来 ⇒ fastbootd 没了 ⇒ system 也刷不进去**，
卡死在半路（而且 boot 已经是被怀疑的那份）。

**已改**：实验内核挪到**步骤 6（最后一步）**，顺序变成

```
① 先刷回已知可用 boot（把 fastbootd 能力拿回来）
② 进 fastbootd  →  ③ 刷诊断 system  →  ④ 刷修复版 system_ext
⑤ 回 bootloader  →  ⑥ 刷实验内核（boot 分区只能在 bootloader 里刷）
⑦ 重启
```

好处：**无论实验内核成不成，`system`/`system_ext` 都已经就位**，
失败时只要"不加 `--kernel` 再跑一次"就能干净回退。

`wait_and_flash.sh` 也加了参数透传，守候到设备后可直接带 `--kernel` 自动执行：

```
./03-脚本/wait_and_flash.sh 28800 --kernel 02-移植素材/a16_patched/hybrid_8tkernel.img
```

### 8.13.5 这次的判读规则（一次刷机就能定性）

| 现象 | 结论 |
|---|---|
| 起来并进系统（adb 可见，`/apex/com.android.tethering` 已挂） | **根因确证 = 内核缺 `erofs_load_compr_cfgs`**；后续只需把内核这条线补齐 |
| 起不来、但 USB 有反应（掉回 fastboot/bootloader） | 根因**不止** EROFS 压缩配置；进 TWRP 读 `/metadata/wbdiag/` |
| 完全无 USB（连节点都没有） | 失败点在**内核/第一阶段 init**之前，读不到 dump；换混合 B 再试一次 |

⇒ 无论哪种结果，都比"盲猜"前进一大步。**这一步必须做。**

---

## 8.14 诊断镜像 `system_diag.img` 的**独立审计**（不信构建日志，直接拆开看）

设备不在，正好把要刷的东西**从镜像里反抽出来逐条核对**。
方法：`fsck.erofs --extract=<文件> --path=<镜像内路径> system_diag.img`，然后直接读。

> ⚠ 注意：`--extract` 的取值是**输出文件路径**，不是目录。
> 传目录名会得到一个"什么都没抽出来"的假象（本项目踩过）。

### 8.14.1 `reboot_on_failure` —— 7 条全部中和，**零条生效** ★最关键

```
netbpfload.rc:70   # wb_diag: 已注释 —— reboot_on_failure reboot,netbpfload-missing
hw/init.rc:1011    # wb_diag: 已注释 —— reboot_on_failure reboot,boringssl-self-check-failed
hw/init.rc:1018    # （同上）
hw/init.rc:1025    # （同上）
hw/init.rc:1032    # （同上）
apexd.rc:10        # wb_diag: 已注释 —— reboot_on_failure reboot,apexd-failed
apexd.rc:22        # wb_diag: 已注释 —— reboot_on_failure reboot,bootloader,bootstrap-apexd-failed
```

用 `grep -rn '^\s*reboot_on_failure'`（只匹配**未注释**的）扫描整个 `/system/etc/init`（78 个 `.rc`）
⇒ **命中 0 条**。
其中 `apexd.rc:22` 的 `reboot,bootloader,bootstrap-apexd-failed`
**就是**之前"开机约 50 秒后掉回 bootloader"的元凶，现已失效。

### 8.14.2 面包屑 —— 8 个 `exec_start` 与 8 个服务**一一对应**

| `init.rc` 里的触发点 | `wb_diag.rc` 里的服务 | 对应阶段 |
|---|---|---|
| `wb_diag_earlyinit` | ✓ | `on early-init` |
| `wb_diag_postapexd` | ✓ | apexd 之后 |
| `wb_diag_init` | ✓ | `on init` |
| `wb_diag_lateinit` | ✓ | `on late-init` |
| `wb_diag_earlyfs` | ✓ | `on early-fs` |
| `wb_diag_postfs` | ✓ | `on post-fs` |
| `wb_diag_postfsdata` | ✓ | `on post-fs-data` |
| `wb_diag_boot` | ✓ | `on boot` |

两个集合的差集**都为空** ⇒ 接线完整，没有"起了个不存在的服务"或"漏了个阶段"。

### 8.14.3 `stdio_to_kmsg` —— 已加到两个 apexd 服务

`apexd.rc` 里 `service apexd` 与 `service apexd-bootstrap` **各自**都有 `stdio_to_kmsg`：

```
service apexd /system/bin/apexd
    # wb_diag: 让 apexd 的 stderr 进内核日志（否则错误全丢）
    stdio_to_kmsg
    ...

service apexd-bootstrap /system/bin/apexd --bootstrap
    # wb_diag: 让 apexd 的 stderr 进内核日志（否则错误全丢）
    stdio_to_kmsg
    ...
```

⇒ apexd 的报错会进 `/dev/kmsg`，即使 logd 没起来也能在 dmesg 里看到。
（另外 `boringssl` 那 4 个服务也顺带加了。）

### 8.14.4 倾倒脚本 —— 存在、可执行、目标正确

`/system/etc/wb_diag.sh`：**2878 字节，权限 `0755`**，首行 `#!/system/bin/sh`。
输出优先级 `/metadata/wbdiag` → `/data/wbdiag` → `/dev/kmsg`（/metadata 是
`wait,check,formattable,first_stage_mount`，双清不受影响，TWRP 可直接读）。

每阶段产三个文件：`trace.txt`（追加一行面包屑）、`dmesg_<stage>.txt`、
`report_<stage>.txt`。`report_*.txt` 里抓的东西**正好对准本次问题**：

```
--- /proc/version   (内核版本，验证 4.19.157 vs 4.19.325)  ← 这次实验的关键判据
--- /proc/filesystems (erofs 是否注册)
--- /sys/module/erofs/version
--- /sys/fs/selinux/enforce
--- ls -la /apex  /system/apex  /dev/block/mapper
--- dmesg 里筛 apex|avb|dm-verity|selinux|init:|vold|fatal|panic|boring
```

### 8.14.5 `ro.adb.secure=0` —— 确认已改（在 `/system/build.prop`）

```
223: ro.secure=1
225: ro.adb.secure=0      ← 改了：adb 不需要授权弹窗
227: ro.debuggable=0
229: ro.force.debuggable=0
```

### 8.14.6 ★ 更正：`setenforce 0` **没做，而且做不了**

之前计划里写了"setenforce 0"。实测：

1. 整个 `/system/etc/init` 树里 `grep -rn setenforce` ⇒ **0 条**，根本没实现。
2. 更要紧的是：**在这台机器上它本来就做不到**。
   Android 的 `init` 里是否允许 permissive 是**编译期常量** `ALLOW_PERMISSIVE_SELINUX`：

   ```cpp
   bool IsEnforcing() {
       if (ALLOW_PERMISSIVE_SELINUX)  return StatusFromCmdline() == SELINUX_ENFORCING;
       else                           return true;      // ← user 构建走这条，cmdline 被无视
   }
   ```

   只有 userdebug/eng 构建才把它编成 1。本机 `build.prop` 是
   `ro.debuggable=0`、cmdline 里 `buildvariant=user` ⇒ **user 构建** ⇒
   `androidboot.selinux=permissive` 会被**直接忽略**。
   所以"往 cmdline 塞 permissive"这条路在本机**不通**。

**替代方案（已具备）**：不绕过 SELinux，改为**观察**它 ——
`wb_diag.sh` 的 `report_*.txt` 会 grep dmesg 里的 `selinux`/`avc`，
SELinux 拒绝会原样落在 dump 里。诊断够用，只是不能"放行"。

### 8.14.7 顺带把 boot 头部真实偏移记下来（之前记错过）

`boot_img_hdr_v2` 实际布局（X2 Pro `boot.img` 实测）：

| 字段 | 偏移 | 实测值 |
|---|---|---|
| `magic` | 0 | `ANDROID!` |
| `kernel_size` | 8 | **60,450,832** |
| `kernel_addr` | 12 | `0x8000` |
| `ramdisk_size` | 16 | 1,097,960 |
| `ramdisk_addr` | 20 | `0x1000000` |
| `second_size` | 24 | 0 |
| `tags_addr` | 32 | `0x100` |
| `page_size` | 36 | 4096 |
| `header_version` | 40 | 2 |
| **`os_version`** | **44** | `0x18000181` |
| `name[16]` | 48 | 空 |
| **`cmdline[512]`** | **64** | 见下 |
| `id[8]` | 576 | — |
| `extra_cmdline[1024]` | 608 | 空 |
| `header_size` | 1644 | 1660 |
| `dtb_size` | 1648 | 522,926 |
| `dtb_addr` | 1652 | `0x1f00000` |

> 之前我把 `cmdline` 记成偏移 44，那是 **`os_version`**，读出来是乱码。
> **正确是 64。**

本机 cmdline 原文（在 header 里，512 字节够长）：

```
console=ttyMSM0,115200n8 earlycon=msm_geni_serial,0xa90000 androidboot.hardware=qcom
androidboot.console=ttyMSM0 androidboot.memcg=1 lpm_levels.sleep_disabled=1
video=vfb:640x400,bpp=32,memsize=3072000 msm_rtb.filter=0x237 service_locator.enable=1
androidboot.usbcontroller=a600000.dwc3 swiotlb=2048 loop.max_part=7
cgroup.memory=nokmem,nosocket reboot=panic_warm kpti=off buildvariant=user
```

注意 `buildvariant=user` —— 这就是 8.14.6 里"permissive 不通"的直接证据。
另外这里**没有任何 `selinux`/`enforcing` 字样**。

### 8.14.8 审计结论

| 项 | 结论 |
|---|---|
| 7 条 `reboot_on_failure` | ✓ 全部中和，0 条生效 |
| 8 个面包屑阶段 | ✓ 与 init.rc 触发点一一对应 |
| `stdio_to_kmsg` | ✓ apexd / apexd-bootstrap 都有 |
| `/system/etc/wb_diag.sh` | ✓ 存在、0755、目标 `/metadata/wbdiag` |
| `ro.adb.secure=0` | ✓ `/system/build.prop:225` |
| `setenforce 0` | ✗ **未实现，且 user 构建下不可行**（已更正，改用 dmesg 观察） |
| `system` / `system_ext` 超级块 | ✓ `compat=0x7 incompat=0x1 blkszbits=12`，fsck 干净 |
| xattr 抽查 | ✓ system 2/2、system_ext 4/4 |

⇒ **`system_diag.img` + `system_ext_fix.img` 可以放心刷。**

---

## 8.15 ★★ 重大发现：`fastboot wait-for-device` 不存在 ⇒ 刷机流程**静默空转**

### 8.15.1 现象

设备回到 bootloader 后，A/B 实验脚本第一次真跑，**23 秒就"跑完"三个阶段**，
每个阶段的日志都写着"预检通过 → 步骤 1/7 进入 bootloader → ✓ 已在 bootloader"，
然后立刻：

```
fastboot: usage: unknown command wait-for-device
✗ 等待 bootloader 超时
[22:43:40] flash_diag.sh 退出码 1
```

**一行都没刷。** 三个阶段全是空转，脚本却照样输出了
"⇒ 换内核**不能**解决问题 ⇒ 根因不在 EROFS 压缩配置"这种**结论**。
—— 这是最危险的失败模式：**看起来跑完了，其实什么都没做**。

### 8.15.2 根因

```
$ platform-tools/fastboot --version
fastboot version 36.0.1-13811061
$ platform-tools/fastboot wait-for-device
fastboot: usage: unknown command wait-for-device
```

**`wait-for-device` 是 `adb` 的子命令，`fastboot` 没有。**
（`fastboot` 侧的正确做法是轮询 `fastboot devices`。）

### 8.15.3 影响面：4 处，横跨 3 个脚本

| 文件 | 行 | 状态 |
|---|---|---|
| `flash_diag.sh` | 244 | **主刷机路径**，本次踩中 |
| `reflash_system.sh` | 48 | 同类 |
| `flash_coloros16.sh` | 142 / 225 | 同类（进 fastboot / 进 recovery） |

⇒ **所有走这几条路径的"刷机"，都会在进入 bootloader 后一行不刷地退出。**
这解释了为什么之前若干次"刷了但没变化"——**根本没刷进去**。

### 8.15.4 修复

统一改成轮询（`fastboot devices` 轮询，最多 80 秒）：

```bash
# ★ fastboot 36.0.1 没有 wait-for-device 子命令（那是 adb 的），改用轮询
i=0
until "$FB" devices 2>/dev/null | grep -q .; do
  sleep 2; i=$((i+1)); [ $i -gt 40 ] && { err "等待 bootloader 超时"; exit 1; }
done
```

`flash_coloros16.sh` 里那两处在函数内，用 `return 1` 而不是 `exit 1`。
4 处已全部替换，`grep -rn wait-for-device` 现在只剩注释。

### 8.15.5 连带修复：A/B 脚本不能再"假装有结论"

原来 `run_flash` 的失败只打一行提示就继续，最后照样输出结论。
**已改成硬失败**：任一阶段的刷机流程非零退出 ⇒ **立刻 `exit 4` 并说明"观察结果无意义"**，
不再输出"换内核不能解决问题"这种会误导人的结论。

```bash
if ! run_flash "--kernel $STAGE1" "阶段1"; then
  log "✗ 阶段1 的刷机流程**没有成功执行**（非零退出）⇒ 观察结果无意义，中止。"
  exit 4
fi
```

### 8.15.6 教训（写进技能了）

1. **"跑完"≠"做到了"。** 自动化脚本必须校验**动作是否真的发生**，
   而不是只看自己有没有走完流程。本次的判据本该是：
   `fastboot flash` 输出里出现 `Sending 'boot'` / `OKAY`，或刷完比对分区哈希。
2. **不要把"未知子命令"当软错误。** `fastboot` 对不认识的子命令打印 usage 到 stderr、
   返回非零——而它被 `||` 或 `2>&1 | tee` 一包，很容易被忽略。
3. **实验脚本的结论必须是"有前置条件"的。** 没刷成就不许下结论。

> 顺带：这也是**为什么必须真跑一次**。这套脚本已经"验证"过多轮（语法、预检、
> 干跑无设备路径全部通过），但**唯一没验过的就是"设备在场时能不能真刷"**——
> 而恰恰是这一步坏了。**语法检查通过 ≠ 能干活。**

---

## 8.16 ★ 第一次**真正刷进去**的实验结果（阶段1：8T 内核 + X2 Pro ramdisk）

修掉 §8.15 的 `wait-for-device` 之后，22:44:56 实验真正跑起来了。

### 8.16.1 刷机过程（这次是真的）

| 步骤 | 结果 |
|---|---|
| 1 进入 bootloader | 设备当时在 **adb** 模式（不是 fastboot）⇒ `adb reboot bootloader` ⇒ ✓ |
| 2 刷回已知可用 boot | `Sending 'boot' (98304 KB) OKAY` / `Writing 'boot' OKAY` ✓ |
| 3 进 fastbootd | `Rebooting into fastboot OKAY` → `< waiting for any device >` → ✓ |
| 4 刷诊断版 system | 4 个 sparse 分块，全部 OKAY，共 **32.2 s** ✓ |
| 5 刷修复版 system_ext | 自动扩容 **+339,968 字节**，4 个分块 OKAY，共 **30.6 s** ✓ |
| 6 刷实验内核 | 回 bootloader 后 `flash boot hybrid_8tkernel.img` ✓（sha256 `e44757ddde11e479`） |
| 7 重启 | `Rebooting OKAY` ✓ |

> `Invalid sparse file format at header magic` 是**无害**提示：
> fastboot 先按 raw 试、失败后自动改用 sparse 分块发送，**最终是成功的**
> （后面 `Sending sparse 'system' 1/4 … OKAY` 就是证据）。别被这行吓到。

### 8.16.2 观察结果：**完全无 USB**

```
[22:46:37] 等待 150 秒观察启动结果…
[22:49:18] 阶段1 观察结果: none
```

150 秒内 **fastboot 和 adb 都没有，USB 节点也不存在**。

### 8.16.3 判读

按 §8.13.5 的规则，这是**第三种**情形：

| 现象 | 含义 |
|---|---|
| 进系统 | 根因 = 内核缺 `erofs_load_compr_cfgs` |
| 起不来但 USB 有反应 | 根因不止 EROFS |
| **完全无 USB（本次）** | **失败在内核 / 第一阶段 init 之前**，连 USB 都没枚举起来 |

⇒ **`8T 内核 + X2 Pro ramdisk` 这个组合连 USB 都没起来。**
注意这**不能**直接推出"换内核路线失败"——因为变量不止一个：
X2 Pro 的 boot ramdisk 里可能带着**为 4.19.157 编译的 first-stage 模块**，
喂给 4.19.325 内核时在 init 之前就崩了。
**所以必须接着试阶段2（换用 A16 的 ramdisk）。**

### 8.16.4 附带确认

- `flash_diag.sh` 的 7 步新顺序**按设计工作**：先刷回已知可用 boot 拿回 fastbootd，
  再刷 system/system_ext，**最后**才刷实验内核 ⇒ 无论实验内核成不成，
  `system`/`system_ext` 都已就位（本次已确认两者都刷入成功）。
- 诊断版 `system_diag.img` + 修复版 `system_ext_fix.img` **已实际落在设备上**。
- 设备重启后 USB 全无 ⇒ **读不到** `/metadata/wbdiag/`（需要进 TWRP 才能读）。

---

## 8.17 阶段2 结果（8T 内核 + A16 ramdisk）—— **同样完全无 USB**

22:58:14 刷入实验内核 `hybrid_8tkernel_a16rd.img`（sha256 `ffb30d0446bce903`），
22:58:22 重启，观察 150 秒 → **fastboot / adb / USB 节点全无**。

### 关键对比

| 组合 | kernel | ramdisk | dtb | 结果 |
|---|---|---|---|---|
| 阶段1 | 8T 4.19.325 | X2 Pro | X2 Pro | **完全无 USB** |
| 阶段2 | 8T 4.19.325 | A16 (8T) | X2 Pro | **完全无 USB** |
| X2Pro 原 boot | 4.19.157-color597 | X2 Pro | X2 Pro | 正常开机 |

⇒ **变量不是 ramdisk，是内核本身。** 8T 的 4.19.325-KSU_NEXT 内核在 X2 Pro 上
**根本不启动**，与 ramdisk 无关。

### 结论：换内核路线**关闭**

8T 内核是社区 KSU 内核（bruce@bruce-PC），为 OnePlus 8T 编译。
即使保留 X2 Pro 的 dtb，它仍然包含大量 8T 专用的驱动/配置，
在 X2 Pro 的板子上连 first-stage init 之前都过不了。
**不可行。**

---

## 8.18 ★ 供体全分区 EROFS 扫描 —— **全部 `incompat=0x1`，与 4.19.157 兼容**

```
my_bigball.img      0.0 MB   EROFS compat=0x7 incompat=0x1 (4.19.157兼容)
my_carrier.img      0.0 MB   EROFS compat=0x7 incompat=0x1 (4.19.157兼容)
my_company.img      0.0 MB   EROFS compat=0x7 incompat=0x1 (4.19.157兼容)
my_engineering.img  0.0 MB   EROFS compat=0x7 incompat=0x1 (4.19.157兼容)
my_heytap.img       0.0 MB   EROFS compat=0x7 incompat=0x1 (4.19.157兼容)
my_manifest.img     0.6 MB   EROFS compat=0x7 incompat=0x1 (4.19.157兼容)
my_preload.img      0.0 MB   EROFS compat=0x7 incompat=0x1 (4.19.157兼容)
my_product.img   1375.3 MB   EROFS compat=0x7 incompat=0x1 (4.19.157兼容)
my_region.img       3.3 MB   EROFS compat=0x7 incompat=0x1 (4.19.157兼容)
my_stock.img     4182.7 MB   EROFS compat=0x7 incompat=0x1 (4.19.157兼容)
odm.img           222.8 MB   EROFS compat=0x7 incompat=0x1 (4.19.157兼容)
product.img         9.3 MB   EROFS compat=0x7 incompat=0x1 (4.19.157兼容)
system.img        990.4 MB   EROFS compat=0x7 incompat=0x1 (4.19.157兼容)
system_ext.img    934.1 MB   EROFS compat=0x7 incompat=0x1 (4.19.157兼容)
vendor.img        917.4 MB   EROFS compat=0x7 incompat=0x1 (4.19.157兼容)
```

**结论**：供体 8T 的 **全部 15 个分区都是 EROFS `incompat=0x1`**。
只有 `system.img` 和 `system_ext.img` **内部**的 APEX payload 曾经是 `incompat=0x3`
（已经被我们重建为 `0x1`）。

⇒ **EROFS 兼容性不是问题。** X2 Pro 的 4.19.157 内核可以挂载供体的所有分区。
真正的问题是：**A16 的 8T system 能不能在 X2 Pro 的硬件上跑起来？**

---

## 8.19 下一步：控制实验（X2 Pro 内核 + 已重建的 8T system）

当前设备状态：
- boot = `hybrid_8tkernel_a16rd.img`（8T 内核，不启动）
- system = `system_diag.img`（已重建 APEX，诊断版）
- system_ext = `system_ext_fix.img`（已重建 compos，修复版）

控制实验：**刷回 X2 Pro 的已知可用 boot（4.19.157 内核），保留已刷入的 system/system_ext，
重启，看能不能进系统。**

- 若能进 ⇒ 重建 APEX 这条路**已经打通**，只需把诊断版改回正式版。
- 若不能进 ⇒ 根因**不在 EROFS**，需要进一步排查（Vendor HAL / SELinux / 缺失服务等）。

这是当前最关键的实验。

---

## 8.20 控制实验已执行：X2 Pro 原核 + 重建版 A16 system/system_ext

23:22–23:24 自动刷入流程完成：
- `boot`: X2 Pro 已知可用 boot (4.19.157 内核, sha256 fc60a85e05b6d953)
- `system`: `system_diag.img` (全 APEX 重建为 0x1, 7 条 reboot_on_failure 注释, wbdiag 倾倒)
- `system_ext`: `system_ext_fix.img` (compos 重建为 0x1, xattr 完整)
23:32 从 TWRP 执行 `adb reboot` 正式重启进系统。
目前设备正在启动流程中。

---

## 8.21 阶段 3 现场取证：pstore 通道失效 + rawdump 金矿（2026-09-30）

### 8.21.1 空间清理（用户诉求：磁盘不足）

删除前 `oppo` 目录 42.8 GB → 删除后 29 GB，**净释放约 13.8 GB**。可用空间 7.3 GB → 15 GB。

| 删除项 | 大小 | 冗余依据 |
|---|---|---|
| `donor_8t/ota_full_8t.zip` | 6.7 GB | 内含单个 `payload.bin`，15 个分区已全部解压至 `donor_8t/extracted/` |
| `07-重建/_apexwork` | 2.1 GB | 各 APEX 的 `payload_orig/check/signed/from_new_apex.img` 中间件，成品在 `_apexout/` |
| `07-重建/tree_system_ext` | 1.6 GB | 解包树，成品 `system_ext_fix.img` 已产出 |
| `x2pro_cos15/{odm,vendor}.new.dat` | 1.5 GB | 是 `.new.dat.br` 的解压中间物，`.br` 与 `.img` 均保留 |
| `02-移植素材/a16_patched/system_a16final.img` | 1.0 GB | 已被 `system_diag.img` 取代 |
| `07-重建/_l3verify` + `_verify` | 0.8 GB | 三级验证副本，与 `_apexout/` 重复 |
| `_apexorig` + `hybrid_8tkernel*.img` | 0.5 GB | 原始 APEX 可从供体 system.img 再抽；换内核路线已证伪 |

**保留（关键底线）**：`platform-tools/FindX2Pro_ColorOS_15.0.2_Pro_*.zip`（7.0 GB，ColorOS 15 回滚唯一素材）、
`06-应用备份`（5.7 GB，用户明确要求）、`donor_8t/extracted/`（供体 15 分区）、`07-重建/tree`（system 解包树）、`05-分析`。

> ⚠ 环境注记：macOS Data 卷 99% 占用，`Docker.raw`（稀疏 460 GB，实占 5.5 GB）在运行且持续增长，
> 是空间波动的次要来源。删除后 `df` 的 Avail 会因 purgeable/SpoIndex 回收而短暂抖动，非删除失败。

### 8.21.2 ★ pstore / ramoops 取证通道在本机**完全失效**

ramoops 参数（`/sys/module/ramoops/parameters/`）：

```
mem_address=2952790016 (0xB0000000)  mem_size=4194304 (4 MB)
console_size=262144   pmsg_size=2097152   record_size=262144   ftrace_size=262144   ecc=0
内核 CONFIG_PSTORE=y / PSTORE_CONSOLE=y / PSTORE_PMSG=y / PSTORE_RAM=y
```

**校准实验**：从 TWRP 执行 `echo c > /proc/sysrq-trigger` 制造**真实内核 panic**。

结果：`/sys/fs/pstore/` 中**依然没有 `console-ramoops-0`**，连原有 `pmsg-ramoops-0` 也消失。

⇒ **结论：本机 ramoops 内存区未被正确保留（pmsg 内容为随机内存垃圾即佐证），
`console-ramoops` 在任何情况下都不会生成。**
**必须废弃"无 console-ramoops ⇒ 无内核 panic"这一推论** —— 它不成立。

### 8.21.3 ★★ 金矿：`rawdump` 分区存有完整内核日志

`rawdump` = 134217728 B (128 MB)，Qualcomm Minidump 分区。头部 magic `Raw_Dmp!`，
后接 144 条 `md_*.BIN` 目录项（`md_KELF_HEADER.BIN` / `md_KWDOGDATA.BIN` / `md_c*_context.BIN` …）。

**在 dump 数据区内发现带完整 `<N>[timestamp]` 前缀的内核日志**，共 15441 行，分两段：

| 段 | 偏移 | 行数 | 内核 | 内容 |
|---|---|---|---|---|
| 段0 | `0x3922ec4` | 15934 | `4.19.157-perf+`（**原厂**） | 运行 **2633 秒**；`init: First stage mount skipped (recovery mode)` ⇒ 这是 **TWRP 恢复模式**启动日志 |
| 段1 | `0x3e11120` | 2700 | `4.19.157-color597-os15pro`（**color597**） | 0→2.014 s，被 `[op_kernel_log] kernel_log_wb_int` 截断 |

**`apexd` 在整份 rawdump 中出现 0 次** ⇒ rawdump 内**不存在任何一次 A16 系统启动日志**，
即 A16 启动从未走到第二阶段的 `apexd`。

### 8.21.4 ★★★ 关键发现：内核 `VFS: Unable to mount root fs` panic

在 `0x3cccbe6` 发现 OPPO `save_dump_reason_to_smem` 记录：

```
Kernel panic - not syncing: VFS: Unable to mount root fs on unknown-block(0,0)
save_dump_reason_to_smem: dump_reason : VFS: Unable to mount root fs on unknown-block(0,0)
        reason_len=50  function caused panic : mount_block_root  name_len=16
CPU: 3 PID: 1 Comm: swapper/0  Tainted: G S  W  4.19.157-color597-os15pro-perf-gff3981b9a878 #72
Hardware name: Qualcomm Technologies, Inc. kona MTP (DT)
Call trace:
 panic+0x168/0x2dc
 mount_block_root+0x190/0x1d8
 handle_initrd+0x74/0x264        ← 走了「传统 initrd」路径
 initrd_load+0xac/0xb4           ← 内核未能解包 initramfs
 prepare_namespace+0x4c/0x1c0
 kernel_init_freeable+0x12c/0x144
 kernel_init+0x14/0x2b0
```

**语义**：`initramfs`（boot.img 的 ramdisk）**没有被内核识别/解包**，
于是 `initrd_start` 保持有效，内核回退到老的 `initrd_load()` → `handle_initrd()` → `mount_block_root()`，
在 `unknown-block(0,0)` 上挂根文件系统失败 → `panic()`。

**这是纯内核态、早于任何用户空间的失败**，完全符合"卡 OPPO 第一屏 + 无 USB 枚举"的现象。

### 8.21.5 镜像指纹核对（排除"刷错东西"）

| 分区 | 设备实测 | 对应本地镜像 |
|---|---|---|
| `boot` 头 4 KB | `2d01dc5628ea8d0fe47363630ef9f226` | = `01-设备快照/刷机前_20260929/boot.img` ✅ |
| `system` 头 4 KB | `aba7347c543341e371e560fe11e5e434` | = `02-移植素材/a16_patched/system_diag.img` ✅ |
| `system` 全尺寸 | 1063690240 B | = `system_diag.img` ✅ |
| `system_ext` | 979853312 B | = `system_ext_fix.img` ✅ |
| 其余 13 个动态分区 | 尺寸逐一匹配 | = `donor_8t/extracted/*.img` ✅ |

**全部刷写正确，无"刷错文件"问题。**

**boot.img 内核**：`4.19.157-color597-os15pro-perf-gff3981b9a878 (color597@color597) clang 11.0.2`
—— 即 **color597 的 ColorOS 15 Pro 定制内核**。段结构 `kernel=60450832  ramdisk=1097960`（gzip cpio）。
`04-日志/dump_202707/{boot,boot_panic0,boot_perm}.img` 三者与该 boot.img **结构完全一致**。

### 8.21.6 APEX 签名公钥差异（已排查，非根因）

| APEX | AVB pubkey SHA1 |
|---|---|
| 原版 `com.android.runtime.apex`（供体 8T） | `d5b7226d2893434282927add44fab971b405586c` |
| 重建版（avbtool 1.3.0 重签） | `8318b54da8d6df18433202438a01ade66f1ff172` |

`apexd` 二进制含 `apex_pubkey` / `public key doesn't match the pre-installed one` 等串，
**但供体 8T 的 `/system/etc/security/` 下并不存在 `apex_pubkey` 文件**（设备上同样不存在），
`GetPreinstalledPublicKeys()` 返回空 ⇒ 预置公钥校验被跳过。**故公钥差异不构成拒绝理由，排除。**

### 8.21.7 当前待办

1. **取回 A16 启动的最新内核日志**：rawdump 是环形区，需在 A16 启动失败后**立即**再拉一次 rawdump 并 diff，
   才能确认 `VFS: Unable to mount root fs` 是否就是本次 A16 启动的失败点（而非历史 hybrid 实验残留）。
2. 若确认：修复方向为 **让内核能解包 ramdisk** —— 检查 `CONFIG_RD_GZIP`、ramdisk 段偏移/大小、
   `header_version` 与 ABL 的加载约定是否自洽。
3. 若否：则需另建取证通道（`oplusreserve1` 8 MB 裸分区面包屑，或 `misc` 分区尾部）。

---

## 8.22 阶段 4：把 rawdump 彻底拆解 —— **内核命令行 = 启动身份指纹**（2026-09-30 下午）

上一节的"待办 1"已在本节完成。结论是**推翻了一半、坐实了一半**，并且挖出两个真实代码缺陷。

### 8.22.1 新方法：用 `Kernel command line` 给每次启动做身份指纹

rawdump 的日志区是**环形缓冲 + 多股日志流交错**（minidump 裸格式 `(cpu)[pid:comm]` 与
pstore 带时间戳格式 `<N>[ts] (cpu)[pid:comm]` 混在一起），**按字节偏移排序 ≠ 按时间排序**。
所以"哪一段是最后一次启动"不能靠偏移猜，必须找**不可伪造的指纹**。

`Kernel command line:` 就是最好的指纹 —— 它是内核启动时原样打印的，直接反映
**ABL 从 boot.img 头部 cmdline + extra_cmdline 拼出来的字符串**，即"刷进去的是哪张 boot.img"。

扫描结果（共 4 处）：

| 偏移 | 内核 | cmdline 关键片段 | 判定 |
|---|---|---|---|
| `0x02be962f` | stock 4.19.157-perf+ | `buildvariant=eng` + `oplus_ftm_mode=ftmrecovery` + `androidboot.mode=recovery` | **TWRP** |
| `0x03924dd3` | stock 4.19.157-perf+ | 同上 | **TWRP** |
| `0x03b26d5b` | stock 4.19.157-perf+ | 同上 | **TWRP** |
| `0x03e13078` | **color597-os15pro** | **`reboot=panic_warm kpti=off buildvariant=user`** + **`androidboot.mode=kernel`** | **正常开机** |

### 8.22.2 ★ 决定性比对：失败启动的 cmdline 与 X2 Pro 移植 boot **逐字节一致**

`04-日志/dump_202707/boot.img` 头部的 cmdline 是：

```
... cgroup.memory=nokmem,nosocket reboot=panic_warm kpti=off buildvariant=user
```

而 `0x03e13078` 那次启动打印的 cmdline 同样含 **`reboot=panic_warm kpti=off buildvariant=user`**
（TWRP 那三次则是 `androidboot.selinux=permissive buildvariant=eng`，完全不同）。

⇒ **失败启动用的是 X2 Pro 移植 boot.img 的头部（含内核 color597）**，且是
**`androidboot.mode=kernel` = 正常开机进 Android**，不是 recovery。

### 8.22.3 ★★ 但 initrd 大小对不上 ⇒ 刷进去的不是"原版"移植 boot

`Freeing initrd memory` 的换算公式**已用 TWRP 镜像校准**（见下表）：

| 镜像 | 头部 `ramdisk_size` | 日志中的值 |
|---|---|---|
| `TWRP-13-Compass-Color597-V2.0.img` | 25400552 | （实际跑的是 v2.1，日志 24812K） |
| `04-日志/dump_202707/boot.img`（移植 boot） | **1097960** | 应为 **1072K** |
| `06-校验/8t_boot.img`（KSU_NEXT） | **1522509** | 应为 **1484K**（`1522509 & ~0xFFF = 1519616`） |

失败启动的日志写的是 **`Freeing initrd memory: 1484K`**，
⇒ 它加载的 **不是**移植 boot 的 1072K ramdisk，而是**约 1.45–1.52 MB 的 ramdisk**
—— **与 8T/A16 的 `ramdisk_size = 1522509` 精确吻合**。

**综合 8.22.2 + 8.22.3：刷进去的是一张「X2 Pro 头部+内核（color597）+ A16(8T) ramdisk」的混合镜像**
—— 正是 `build_hybrid_boot.py` 的产物形态（`hybrid_a16rd.img`）。

### 8.22.4 ★★★ 为什么混合镜像会 panic：内核**根本没尝试解压**

对照两条时间线（`BOOTPROF` 佐证）：

| 启动 | `Trying to unpack rootfs image as initramfs...` | 下一条 | Δ | `populate_rootfs` 耗时 |
|---|---|---|---|---|
| TWRP（成功） | `0.593885` | `Freeing initrd memory: 24812K` @`0.889315` | **295 ms** | `295.553386ms`（日志明写） |
| **失败启动** | `0.441126` | **`rootfs image is not initramfs (no cpio magic)`** @`0.441533` | **0.4 ms** | — |

**0.4 ms 就报错退出。** 1.5 MB gzip 的解压不可能只花 0.4 ms（对比成功那次 295 ms）。
⇒ 内核在 `decompress_method()` 阶段**没有识别出任何压缩魔数**，
于是把数据当**裸 cpio** 解析，第一组字节不是 `070701` ⇒ 立刻 `no cpio magic`。

**即：内核拿到的 initrd 内容，连 gzip 魔数都不是。**

随后完整走了老式 initrd 降级链路（已从 rawdump 逐条确认）：

```
Trying to unpack rootfs image as initramfs...
rootfs image is not initramfs (no cpio magic); looks like an initrd
Freeing initrd memory: 1484K
RAMDISK: gzip image found at block 0        ← rd_load_image() 把 /initrd.image 灌进 /dev/ram0
Warning: unable to open an initial console.
Kernel panic - not syncing: VFS: Unable to mount root fs on unknown-block(0,0)
  mount_block_root+0x190/0x1d8
  handle_initrd+0x74/0x264
  initrd_load+0xac/0xb4
  prepare_namespace+0x4c/0x1c0
```

**对照内核 `kernel_init_freeable()` 源码即可判读**：

```c
if (sys_access("/init", 0) != 0) {   // initramfs 没解出来 ⇒ /init 不存在
        ramdisk_execute_command = NULL;
        prepare_namespace();          // ← 老 initrd 路径 → panic
}
...
free_initmem();                       // ← 成功路径
```

- 成功启动：`unable to open an initial console` → `Freeing unused kernel memory: 4096K` → `KERNEL_INIT_DONE` ✅
- 失败启动：`unable to open an initial console` → **`RAMDISK: gzip image found at block 0`** → panic ❌

**这是一条纯内核态、早于任何用户空间的失败，与"卡 OPPO 第一屏 + 全程零 USB 枚举"完全自洽。**

### 8.22.4b ★★★ 修正与补强：从内核里**抠出 `.config`**，地雷现形

上面的"内核没尝试解压"只是现象。真正的判据来自内核内嵌配置 ——
三个内核都带 `CONFIG_IKCONFIG=y`，用 `IKCFG_ST` 锚点 + gzip 解出**完整 180 KB `.config`**：

| CONFIG | **color597 内核** | KSU_NEXT 4.19.325 | stock 4.19.157-perf+ |
|---|---|---|---|
| `CONFIG_RD_GZIP` | **y** | y | y |
| `CONFIG_RD_BZIP2` | **y** | y | y |
| `CONFIG_RD_LZMA` | **y** | y | y |
| **`CONFIG_RD_XZ`** | **n** | n | n |
| **`CONFIG_RD_LZO`** | **n** | n | n |
| **`CONFIG_RD_LZ4`** | **n** | n | n |
| `CONFIG_BLK_DEV_INITRD` / `BLK_DEV_RAM` | y / y | y / y | y / y |
| `CONFIG_BLK_DEV_RAM_COUNT` / `SIZE` | 16 / 8192 | 16 / 8192 | 16 / 8192 |
| `CONFIG_DECOMPRESS_GZIP / LZ4 / LZMA` | y / y / y | y / y / y | y / y / y |

**两条硬结论：**

1. **`CONFIG_RD_GZIP=y` ⇒ 我的"内核不支持 gzip initramfs"假设被证伪。**
   反证很硬：TWRP v2.0 的 ramdisk 我实测是**完整合法 gzip**（解压出 66 509 056 字节 cpio，`gzip -t` 通过），
   而 stock 内核那次 `populate_rootfs` 用了 **295.55 ms 且无任何报错** ⇒ **gzip initramfs 解压链路是通的**。

2. **★ 真地雷：`CONFIG_RD_LZ4 = n`（同时 `XZ` / `LZO` 也是 n）。**
   内核只认 **gzip / bzip2 / lzma** 三种 initramfs 压缩。
   **若 ramdisk 是 LZ4（或 XZ/LZO）压缩的，内核 `decompress_method()` 认不出魔数
   ⇒ 当裸 cpio 解析 ⇒ 首字节非 `070701` ⇒ 立刻 `no cpio magic`（0.4 ms，完全吻合）。**

   **⇒ 这条必须写进移植检查清单：任何来源的 ramdisk，打包前必须确认是 gzip/bzip2/lzma 之一。**

   同时注意 `CONFIG_DECOMPRESS_LZ4=y` **不等于** `CONFIG_RD_LZ4=y` ——
   前者只服务"压缩内核镜像"路径，**对 initramfs 无效**。这个坑很容易踩。

**另注**：`no cpio magic`（来自 `unpack_to_rootfs`）与 `RAMDISK: gzip image found at block 0`
（来自 `identify_ramdisk_image` → `decompress_method`）在**同一次启动**里同时出现，
说明这两处对"是不是压缩流"的判定路径**并不等价** —— 前者只在数据被当成 cpio 直解时才会给出该错。
排障时**不要**用"日志里出现了 gzip 字样"就断定 ramdisk 格式没问题。


### 8.22.5 时序修正：这条 panic **可能不是**最近一次 A16 尝试

`op_kernel_log` 记录的 `kernel boot count`：

| 启动 | boot count | log number | is_monitoring |
|---|---|---|---|
| **panic 那次** | **2518** | `[6/8]` | `121` |
| TWRP 那次 | **2761** | `[1/8]` | `0` |

boot count **2761 > 2518**，而 TWRP 是"最近"才进去的 ⇒ **panic（2518）早于 TWRP（2761）**。

**推论**：这条 `VFS` panic 很可能是 **9/29 晚 20:30–22:50 的 hybrid 实验残留**；
而 9/29 23:24 那次 `flash_diag.sh`（刷了已知可用 boot + system_diag + system_ext_fix）
之后的启动**是纯卡死（没 panic）⇒ 没写新 dump ⇒ 环形区里仍是旧 panic**。

**⇒ 因此 §8.21.7 待办 1 的答案：必须"清零基线 + 复现 + 立即再拉"才能定性，不能凭现有 dump 下结论。**
新的 `reflash_a16.sh --baseline / --all` 已把这一步固化成流程。

### 8.22.6 附带挖出的两个真实代码缺陷（已修）

`build_hybrid_boot.py` / `build_hybrid_kernel.py` 的段偏移计算**漏掉了 `recovery_dtbo` 段**。
boot v1/v2 头的段顺序是：

```
[header] [kernel] [ramdisk] [second] [recovery_dtbo] [dtb]
```

- X2 Pro 移植 boot：`recovery_dtbo_size = 0` ⇒ 侥幸没出错（这就是之前一直没暴露的原因）
- **TWRP-13-Compass-Color597-V2.0.img：`recovery_dtbo_size = 11729256`** ⇒
  按老算法 `dtb@0x4519000` 读到 `d7 b7 ab 1e`（垃圾）；正规算法 `dtb@0x5049000` 读到 `d0 0d fe ed`（✅FDT）

**已修复**：两处 `parse_hdr()` 补上 `recovery_dtbo_size/offset`，body 组装补上 `second`/`recovery_dtbo`
两段原样保留，`seg()` 增加 `second`/`recovery_dtbo`；并在检测到骨架含 recovery_dtbo 时打印告警。
修复后用 TWRP 镜像回归验证，两个脚本均能正确定位 FDT。

### 8.22.7 新增工具：`03-脚本/reflash_a16.sh`

把"干净重刷 + 取证"固化成一条命令，内置事故复盘的 4 条硬约束：

1. **每次重刷第一步就刷回已知可用 boot**（sha256 `fc60a85e05b6d953…`，脚本会校验）
2. **取证先立基线**：`--baseline` 拉 rawdump 记 md5；`--rawdump` 拉第二次并 diff，
   直接给出"有没有产生新 dump"的判定（区分 **panic** 与 **纯卡死**）
3. 不依赖 `fastboot wait-for-device`（本机 fastboot 36.0.2 无此子命令），一律轮询
4. 判断 fastboot 成败一律取 `PIPESTATUS[0]`

子命令：`--status` / `--baseline` / `--flash` / `--observe [秒]` / `--rawdump` / `--all`。
`--rawdump` 会自动统计 `Kernel panic`、`no cpio magic`、`RAMDISK: gzip`、`KERNEL_INIT_DONE`、
`apexd`、`Freeing initrd memory`、`Linux version`、`boot count` 等关键标记。

### 8.22.8 本节待办

**修正后的定性（重要）**：结合 §8.22.5 的 boot count 时序 + §8.22.2 的内核/cmdline + §8.22.3 的 ramdisk 尺寸，
这条 `VFS` panic 的**最可能来源是 9/29 晚 20:39 前后的 `hybrid_a16rd.img` 实验**
（`build_hybrid_boot.py` 产物 = X2 Pro 头部+color597 内核 + A16(8T) ramdisk），
**而不是 23:24 那次 A16 正式尝试**。23:24 那次（已知可用 boot + system_diag + system_ext_fix）
是**纯卡死、没有 panic ⇒ 没有写新 dump**，所以环形区里留的仍是旧 panic。

**⇒ 因此现有 rawdump 不足以定性 A16 的失败模式，必须做"基线 diff"的干净复现。**

1. **把设备送回 TWRP** → `./03-脚本/reflash_a16.sh --baseline`（立基线，记 rawdump md5）
2. `./03-脚本/reflash_a16.sh --flash`（刷回已知可用 boot + system_diag + system_ext_fix）
3. `./03-脚本/reflash_a16.sh --observe 180` + `--rawdump`
   → **md5 变了** = 有新 panic，按新日志定位；**md5 没变** = 纯卡死，转攻一阶段 init
4. 顺带在 TWRP 里取内核配置基线：`zcat /proc/config.gz | grep -E 'CONFIG_RD_|CONFIG_BLK_DEV_RAM'`
   （设备内核 `CONFIG_IKCONFIG_PROC` 若为 y 则可用；本机已能离线抠 `.config`，见 §8.22.4b）
5. **ramdisk 格式红线**：任何要刷进 boot 的 ramdisk，必须是 **gzip / bzip2 / lzma** 之一
   （`CONFIG_RD_LZ4/XZ/LZO = n`，用 LZ4 会瞬间 `no cpio magic` 并 panic）
6. 若确认是纯卡死：转攻一阶段 init（A16 的 `init` + `first_stage_mount` + EROFS 挂载）

---

## 8.23 阶段 5：拿到「启动原因」铁证 —— 卡点已从 apexd 前移到 boringssl

> 时间：2026-09-30。**本节推翻 §8.22.8 的"不足以定性"结论**：不需要新 rawdump，
> 设备自己把"上次为什么重启"写在了 `/metadata/bootstat/` 里。

### 8.23.1 ★ 铁证：`persist.sys.boot.reason`

`04-日志/twrp_dump_20260929_234355/metadata_bootstat/persist.sys.boot.reason`：

```
reboot,boringssl-self-check-failed
```

**对照实验**：`04-日志/twrp_diag_201530/bootreason.txt`（20:15，刷机前）**为空**（仅一个换行）
⇒ 该 reason 产生于 **20:15 之后**，不是历史遗留。

### 8.23.2 为什么这条证据把卡点整体前移

`boringssl_self_test*` 是 **second-stage init 在 `on init` 才拉起的服务**：

```
# /system/etc/init/hw/init.boringssl.zygote64_32.rc
on init && property:ro.product.cpu.abilist32=*
    exec_start boringssl_self_test32
on init && property:ro.product.cpu.abilist64=*
    exec_start boringssl_self_test64
on property:apexd.status=ready && property:ro.product.cpu.abilist32=*
    exec_start boringssl_self_test_apex32
on property:apexd.status=ready && property:ro.product.cpu.abilist64=*
    exec_start boringssl_self_test_apex64
```

⇒ 能走到 boringssl ⇒ **first-stage 挂载 + `execv("/system/bin/init")` + `apexd-bootstrap` 全部成功**。
**APEX 重建（`0x3 → 0x1`）确实生效了。**

特别地，`_apex32/64` 那两条的触发条件是 **`property:apexd.status=ready`** ——
这是 apexd **完整跑完**才会置的属性，是"APEX 挂载成功"的**独立旁证**。

### 8.23.3 与「诊断版已注释 7 处 `reboot_on_failure`」的矛盾及解释

`system_diag.img` 经**字节级**反抽（`fsck.erofs --extract`，工具见 §8.23.6）：

| 文件 | 未注释 `reboot_on_failure` |
|---|---|
| `/system/etc/init/hw/init.rc` | **0 条**（4 处 boringssl 全注释 @1011/1018/1025/1032）|
| `/system/etc/init/apexd.rc` | **0 条**（2 处全注释）|
| `/system/etc/init/netbpfload.rc` | **0 条** |
| `wb_diag` 面包屑 | **20 处** |

⇒ 诊断版**不可能**产生 `boringssl-self-check-failed`。
⇒ **该 reason 只能来自诊断版之前的那次启动**：时间窗内最匹配的是
**21:43 的 `hybridA16init` 实验**（A16 ramdisk init + X2 Pro fstab + **修复版 system**，
当时 boringssl 的 `reboot_on_failure` **尚未注释**）。
⇒ 旁证：`hybridA16init_2143/timeline.txt` 记 "121 秒无 USB" —— `boringssl_self_test` 在 `on init`
跑，**早于 adbd 起来**，所以"走到了但无 USB"完全自洽。

### 8.23.4 卡点重排（更新 §8.9.4 / §8.22.8）

```
[已越过] first-stage 挂载 system/vendor/... (erofs incompat=0x1)
[已越过] execv("/system/bin/init")  → A16 second-stage init
[已越过] apexd-bootstrap            ← APEX 0x3→0x1 修复生效
                                       （★2026-10-01 订正：原写"apexd.status=ready 已置"，
                                         该旁证不成立，理由见 §8.24.3）
[★卡点]  on init → boringssl_self_test32/64
         → 失败 → reboot,boringssl-self-check-failed
[未知]   再往后（adbd 起来之前还有别的关卡）
```

`boringssl_self_test64` 二进制仅 **14 368 字节**，`strings` 里**没有** `reboot` / `powerctl`
⇒ 重启由 init 的 `reboot_on_failure` 触发，**不是二进制自己干的**（所以注释掉就该生效）。

### 8.23.5 为什么 23:24 刷诊断版后仍「无 USB」

诊断版注释了 boringssl 的 `reboot_on_failure` ⇒ **失败不再重启**，但**不等于会成功**。
设备此后应"停在现场"，而 `adbd` 尚未起 ⇒ **无 USB 符合预期，不是新故障**。
**下一个真正的卡点仍未知**，必须靠 `/metadata/wbdiag/` 定位。

### 8.23.6 本次核实到的可复用事实

- **erofs 工具链路径**：`~/.workbuddy-ai/tools/erofs/erofs-utils/1.9.4/bin/{fsck,dump,mkfs}.erofs`
  （`build_system_diag.sh` 第 128-129 行硬编码，`which` 找不到）
- **读镜像内单个文件**：`fsck.erofs --extract=<输出文件> --path=<镜像内路径> <img>`
  （`--extract` 取**文件路径**，传目录会得到"什么都没抽出来"的假象）
- **APEX payload 的 EROFS 超级块在偏移 1024**，`feature_incompat` 在 sb 内 `+80`；
  实测 `07-重建/tree/system/apex/` 与 `_apexout/` **32 个全 `0x1`**，`donor_a16/` 是 `0x3`
- 外层分区镜像（system/system_ext/vendor/odm/product/my_*）**全部 `incompat=0x1`**，
  **没有 `big_pcluster`（0x4）** —— 文档标题里的 "big-pcluster" 实际是 `COMPR_CFGS`（0x2）

### 8.23.7 待办

1. 设备送回 TWRP → **优先**：`mount /dev/block/by-name/metadata /metadata && cat /metadata/wbdiag/trace.txt`
   —— 直接给出 init 走到哪个 trigger 死的（诊断版已埋 8 个面包屑）
2. 若 `/metadata/wbdiag/` 为空 ⇒ second-stage 都没起来（更早失败），转查 first-stage
3. 备选加固：把 4 个 boringssl 服务的**命令**直接换成 `/system/bin/true`（彻底绕开自检），
   而不是只注释 `reboot_on_failure` —— 排除"失败引发后续连锁"的可能

---

## 8.24 无设备状态下的攻坚（2026-10-01 凌晨）

设备不在 USB 总线上（`adb devices` 空、`fastboot devices` 空、`ioreg` 匹配 0），
只能做离线。本轮拿到 **三条新认识**，并订正了 §8.23 的一处旁证。

### 8.24.1 ★★ `/metadata` 是 `first_stage_mount` ⇒ 面包屑可能还在机器里

两台机器的 fstab 逐行对比（`06-校验/fstab/`）：

```
# cos15_fstab.qcom (X2 Pro) :97
/dev/block/by-name/metadata  /metadata  ext4  noatime,nosuid,nodev,discard  wait,check,formattable,first_stage_mount
# 8t_vendor_fstab.qcom      :48
/dev/block/by-name/metadata  /metadata  ext4  noatime,nosuid,nodev,discard  wait,check,formattable,first_stage_mount
```

**两台机器都是 `first_stage_mount`** ⇒ `/metadata` 在 **first-stage init** 就挂上了，
**早于** second-stage init 的 `on early-init` ⇒ 诊断版的**第一个面包屑**（`wb_diag_earlyinit`）
就已经能往 `/metadata/wbdiag/` 写。

**推论（本轮最重要的行动结论）**：
2026-09-29 23:18 刷入的 `system_diag.img` 写的面包屑
**很可能仍然躺在手机里，一行没动**。
`04-日志/twrp_dump_20260929_234355/` 只抓了 `metadata_bootstat/` 和 `pstore/` 两个路径，
**从未检查 `/metadata/wbdiag/`** —— 这是纯粹的"没去看"，不是"没有"。

⇒ **必须先做零成本取证，再考虑任何重刷。**（`./03-脚本/reflash_a16.sh --forensics`）

> 唯一可能已经毁掉这份证据的操作：用户在 23:18 之后做过 **双清/格式化 data**
> （`metadata` 会被 `formattable` 连带重建）。若如此，`/metadata/wbdiag/` 会是空的，
> 那就直接转 §8.24.4 的加固版 v2。

### 8.24.2 `misc` 分区可以当"不依赖任何文件系统"的标记通道

`01-设备快照/刷机前_20260929/fastboot_getvar_刷机后.txt` 实测：

```
partition-size:misc:0x100000        = 1 MB
partition-size:metadata:0x1000000   = 16 MB
partition-size:oplusreserve1:0x800000   = 8 MB   (type=raw)
partition-size:oplusreserve2:0x8000000  = 128 MB (type=raw)
partition-size:oplusreserve3:0x4000000  = 64 MB  (type=raw)
partition-size:oplusreserve4:0x2000000  = 32 MB  (type=raw)
partition-size:oplusreserve5:0x4000000  = 64 MB  (type=raw)
partition-size:rawdump:0x8000000        = 128 MB
```

Android 在 `misc` 里只写最前面 **2 KB** 的 `bootloader_message`（BCB：
`command[32] + status[32] + recovery[768] + stage[32] + reserved[1184]`）。

⇒ **`misc` 偏移 512 KB 处完全空闲**，可以当标记槽：

```sh
# 写：槽号 = 阶段序号，槽宽 64 字节
printf '%-15s' "$STAGE" | dd of=/dev/block/by-name/misc bs=64 seek=$((8192 + IDX)) conv=notrunc
# 读（TWRP 里）：
dd if=/dev/block/by-name/misc bs=64 skip=8192 count=16 | tr -d '\000'
```

**为什么不写 `oplusreserve1`**：名字带 "reserve" 不代表不用，
OPPO 的 `oplusreserve*` 常被 bootloader 用于厂商自有数据（校验/日志/NV），
写坏了可能影响开机；而 `misc` 的用途是公开且边界明确的。

### 8.24.3 ★ 订正 §8.23：`apexd.status=ready` 这条旁证不成立

§8.23.2 用「`boringssl_self_test_apex32/64` 的触发条件是 `property:apexd.status=ready`」
来论证"apexd 完整跑完"。**这条推理有漏洞**：

`/system/etc/init/hw/init.boringssl.zygote64_32.rc` 全文：

```
on init && property:ro.product.cpu.abilist32=*
    exec_start boringssl_self_test32           ← 只要 on init 就会跑
on init && property:ro.product.cpu.abilist64=*
    exec_start boringssl_self_test64
on property:apexd.status=ready && property:ro.product.cpu.abilist32=*
    exec_start boringssl_self_test_apex32      ← 需要 apexd.status=ready
on property:apexd.status=ready && property:ro.product.cpu.abilist64=*
    exec_start boringssl_self_test_apex64
```

**前两个只需 `on init`，不需要 `apexd.status=ready`。**
而 `persist.sys.boot.reason` 只写 `reboot,boringssl-self-check-failed`，
**不区分是哪一个**。所以：

- ✅ **仍然成立**：`on init` 能跑到 ⇒ `on early-init` 已完成
  （`exec_start apexd-bootstrap` 与 `perform_apex_config --bootstrap` 都在 `on early-init` 段内，
  已由 `/tmp/diag/init.rc` 第 140–155 行的上下文逐行确认）
- ❌ **不再成立**："`apexd.status=ready` 已置 ⇒ apexd 完整跑完"
  —— 重启可能来自 `on init` 的那一对，**根本没走到 apex 那一对**

**订正后的表述**：
> `boringssl-self-check-failed` ⇒ `on init` 已到达 ⇒ `on early-init` 未失败。
> 若 `exec_start` 对 oneshot 服务是同步等待（AOSP init.rc 该处注释如此暗示），
> 则等价于 **apexd-bootstrap 未返回非零** —— 即 **APEX 重建（0x3→0x1）确实起了作用**。
> 但"apexd 完整跑完（`apexd.status=ready`）"**没有证据**。

加固版 v2 用 `on property:apexd.status=ready` 挂了一个独立面包屑（`apexready`），
下一次实验就能把这条彻底钉死。

### 8.24.4 加固版 v2（`system_diag2.img`）做了什么

在 v1（注释 7 处 `reboot_on_failure` + 8 个面包屑 + `stdio_to_kmsg` + `ro.adb.secure=0`）之上：

| # | 改动 | 为什么 |
|---|---|---|
| 1 | **4 个 `service boringssl_self_test*` 的命令 → `/system/bin/true`** | 只注释 `reboot_on_failure` 只是"失败不重启"，服务**仍然会失败**；把命令本身换掉才是真正绕开 |
| 2 | **`wb_diag.sh` 增加 boringssl 探针**（`postapexd`/`apexready`/`lateinit` 三个阶段各跑一次真身，`timeout 8`，记 `ls -laZ` + rc + stderr 到 `/metadata/wbdiag/boringssl_<stage>.txt`） | 绕开之后要知道"**为什么**会失败"：rc 是多少、文件在不在、SELinux 标签对不对 |
| 3 | **`misc` 偏移 512 KB 写阶段标记**（槽号=阶段序号） | 不依赖任何文件系统（见 §8.24.2） |
| 4 | **新增 `apexready` 阶段**（`on property:apexd.status=ready` 触发） | 给"apexd 完整跑完"一个独立、可证伪的证据（见 §8.24.3） |

阶段序号（`misc` 槽号）：

```
1 earlyinit   2 postapexd   3 init   4 lateinit
5 earlyfs     6 postfs      7 postfsdata   8 boot   9 apexready
```

### 8.24.5 本轮新增/修改的工具

- **`03-脚本/reflash_a16.sh` 新增 `--forensics`** ★
  在 TWRP 里一条命令把该看的全拉了：`/metadata/wbdiag/*`、`/metadata/bootstat/*`、
  `/data/wbdiag/*`、`/sys/fs/pstore/*`、`/proc/last_kmsg`、`misc` 的 BCB、`/dev/block/by-name/`、
  rawdump + 标记分析。全部**只读**，不改设备。
- **`03-脚本/reflash_a16.sh` 新增 `--markers`**：读 `misc` 偏移 512 KB 的 9 个标记槽。
- **`03-脚本/make_diag.py`**：① 修 `OUT` 目录不存在就崩的 bug（加 `os.makedirs`）；
  ② 新增 boringssl 中和 + 探针 + `misc` 标记；③ 新增 `apexready` 阶段。
- **`03-脚本/build_system_diag.sh`**：① `SRC`/`OUT` 支持环境变量覆盖
  （原始 `system.img` 已被删，增量重建改用 `system_diag.img` 作元数据基底 —— 等价，
  因为被替换的 8 个文件全部由 `--add` 覆盖）；
  ② 新增步骤 5/5：从**成品镜像反抽** `init.rc`，断言
  `boringssl→true == 4`、`仍指向真身 == 0`、`面包屑 == 8`、`未注释 reboot_on_failure == 0`。

### 8.24.6 待办（按优先级）

1. **★ 设备一接上，先跑 `./03-脚本/reflash_a16.sh --forensics`（零成本，不刷机）**
   - `f_trace.txt` 非空 ⇒ 直接读出 init 死在哪个 trigger
   - `/metadata/wbdiag/` 不存在或为空 ⇒ 二阶段 init 没跑到 `on early-init` ⇒ 转查 first-stage
   - 若确认已被双清 ⇒ 直接进第 2 步
2. 刷 `system_diag2.img`（加固版 v2）→ 再跑一次 `--forensics` + `--markers`
3. 若 v2 仍无 USB 且 `misc` 标记为空 ⇒ **失败在 first-stage**（比二阶段更早），
   下一轮改从 first-stage 的 `init`/`first_stage_mount` 下手（例如把 A16 ramdisk 的 first-stage init 换成 X2 Pro 的）
4. 保底：`platform-tools/FindX2Pro_ColorOS_15.0.2_Pro_2025.1_20250823_By_Color597.zip`（7.0 GB）完好，随时可回滚 A15

