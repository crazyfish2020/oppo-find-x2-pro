# 第 6 号根因：重建 APEX 时丢失 APK Signature Scheme v2 签名块 → system_server 崩溃

> 时间：2026-10-02
> 现象：A16（ColorOS 16 / SDK 36）刷入 v5 后，一加 logo 一闪即重启，**双清无效**。
> 前序：第 3 号（SELinux 三重障碍）已由 permissive boot 绕过；第 4 号（netd 内核 5.4 硬门限）与
> 第 5 号（`--replace` 丢 xattr → netbpfload unlabeled）已在 v4/v5 修掉，v5 实测
> `I netd: libnetd_updatable_init success`、`bpf.progs_loaded=1`，netd 已完全正常。

---

## 1. 症状（v5 实测日志原话）

```
E System  : ************ Failure starting system services
E System  : java.lang.IllegalStateException: Failed to scan: /system/apex/com.android.adbd.apex
              at com.android.server.pm.InstallPackageHelper.scanApexPackages(InstallPackageHelper.java:4147)
E System  : Caused by: com.android.server.pm.PackageManagerException:
              No APK Signature Scheme v2 signature in package /system/apex/com.android.adbd.apex
E System  : Caused by: android.util.apk.SignatureNotFoundException:
              No APK Signing Block before ZIP Central Directory
              at android.util.apk.ApkSignatureSchemeV2Verifier.findSignature(ApkSignatureSchemeV2Verifier.java:201)
E AndroidRuntime: *** FATAL EXCEPTION IN SYSTEM PROCESS: main
```

dmesg 侧：

```
init: [PHOENIX] phx_is_bootup_critical_service: bootup critical service zygote crashed
critical svc 2014:system_server exit with 9 !
```

⇒ `zygote` / `system_server` 是 Phoenix 的 bootup-critical service，一崩就整机重启。

**双清无效的原因**：问题在 `/system` 镜像里的文件本身，不在 `/data`。清 data 不会碰到它。

---

## 2. 根因链

### 2.1 为什么要重建全部 33 个 APEX（这一步没错）

A16 的 APEX payload 用 `mkfs.erofs` 的 **big-pcluster（16 KB 物理簇）** 构建，
超级块 `feature_incompat = 0x3`。而 Find X2 Pro 的 **4.19.157-color597** 内核只认 `0x1`，
挂载直接返回 `-EINVAL`：

```
apexd --bootstrap 失败 ⇒ reboot,bootloader ⇒ 开机 54 秒掉 fastboot
```

所以 `apex_rebuild_all.py` 必须把 33 个 APEX 的 payload 全部重建成 `incompat=0x1`，
再用自备密钥重签 AVB 并替换包内 `apex_pubkey`。**这个前提无法回避。**

> 唯一例外：`com.android.apex.cts.shim.apex` 的 payload 本来就不是 EROFS，跳过。

### 2.2 错在哪：`apex_rebuild.py` 重打 zip 时把签名块整块丢了

`apex_rebuild.py` 第 5 步用 python `zipfile` 逐条重写 zip（保持条目顺序 / 压缩方式 /
STORED 条目 4096 对齐），**但没有重新插入 APK Signing Block**。

原包的 zip 尾部结构：

```
[ ZIP entries .................... ]
[ APK Signing Block ]   ← python zipfile 重写时整块丢失
[ ZIP Central Directory ]
[ EOCD ]
```

### 2.3 为什么 A16 才开始报

`PackageManagerService.scanApexPackages` 对每个 `/system/apex/*.apex` 调：

```java
ApkSignatureVerifier.verify(path, SIGNING_SCHEME_V2)   // APEX 的 minSignatureSchemeVersion = 2
```

拿不到签名块就直接抛 `SignatureNotFoundException`。
**这个校验从 v3 起就一直存在，只是 v3/v4 卡在更早的 SELinux 与 netd 阶段，
根本走不到 `system_server` 的 PackageManager 初始化。**

### 2.4 影响面量化（离线扫描，用 EOCD 定位法）

```
33 个 APEX 中：OK=1（cts.shim，未重建）  BAD=32（全部重建过的）
```

⇒ 不是单个包的问题，**32 个包全部缺签名块**。

---

## 3. 连带发现的第二个坑：32 个 APEX 丢失 SELinux 标签

`apex_rebuild_all.py` 产出的新 APEX 是 python 新建的文件，
**`security.selinux` 一条都没带**：

```
com.android.adbd.apex            0 xattr
com.android.apex.cts.shim.apex   1 xattr: security.selinux=u:object_r:system_file:s0   ← 只有它幸存
```

后果：permissive 下能跑（v5 实测 APEX 挂载成功），**enforcing 下 `/system/apex/*` 会变成
`u:object_r:unlabeled:s0`**，和 v3/v4 栽在 `netbpfload` 上的是同一类问题。

---

## 4. 修法

### 4.1 核心：用 AOSP 官方 apksig 库补 v2 签名块

新增 `03-脚本/tools/ApexSign.java` + `03-脚本/apex_v2_sign.py`。

**为什么用 apksig 而不是手写**

v2 签名块是一堆嵌套 length-prefix 编码 + stripping-protection attribute（`0xbeeff00d`），
手写极易错位。apksig（Maven `com.android.tools.build:apksig`）就是 `apksigner` 的内核。

已从 Google Maven 取回：`~/.workbuddy-ai/tools/apksig/apksig-8.7.3.jar`
（506,969 B，sha256 `c070ed1394629d74641aa0906f60b2ffa1ee77e6366a1f93437f59717b1aeb89`）

> 注意：Maven Central 上没有这个包（404），只有 Google Maven 有。

**反解出的 v2 块结构**（对照未重建的 cts.shim，用于确认 apksig 行为正确）：

```
uint64 blockSize = 0x0ff8 (4088)          → 总长 4096
uint64 pairLen   = 0x0740 (1856)
uint32 pairId    = 0x7109871a             ← APK Signature Scheme v2
uint32 signersLen= 0x0738 (1848)
  uint32 signerLen = 0x0734 (1844)
    uint32 signedDataLen = 0x04f8 (1272)
      uint32 digestsLen = 0x2c (44)
        uint32 entryLen=0x28 | uint32 algId=0x0103 | uint32 digestLen=0x20 | SHA256(32B)
      uint32 certsLen = 0x4b0 (1200) | uint32 certLen = 0x4ac | X.509 DER
      uint32 attrsLen = 12 | (entryLen=8 | id=0xbeeff00d | value=3)   ← stripping protection
    uint32 signaturesLen = 268 | 264 | 259 | 256   ← RSA-2048 签名
    uint32 pubKeyLen = 292 | SPKI DER
```

**算法 `0x0103` = RSASSA-PKCS1-v1_5 + SHA-256**（不是 PSS）。

**签名者的选择（刻意）**

证书用 `07-重建/_avb/apex_key.pem` 自签：

```bash
openssl req -x509 -new -key apex_key.pem -out apex_cert.pem -days 3650 \
  -subj "/CN=WorkBuddy A16 Port/O=Local/ST=Local/C=CN" -sha256
openssl pkcs12 -export -inkey apex_key.pem -in apex_cert.pem \
  -out apex.p12 -name apexkey -passout pass:android
```

**关键**：这把 key 就是包内 `apex_pubkey` 与 AVB 签名用的同一把。
所以「v2 证书公钥」与「apex_pubkey」天然一致，
无论上层是查 v2 自洽性还是查 apex_pubkey 都能过。

**只签 v2，不签 v1/v3**

* v1 需要 `META-INF/MANIFEST.MF`，APEX 不该有 ⇒ 必须关掉
  （顺带把原包残留的 3 个失效 v1 文件清掉了）
* v3 需要 signer lineage 结构；APEX 的 `minSignatureSchemeVersion` 就是 2，
  只签 v2 即可（`ApkSignatureVerifier` 会先试 v3，拿不到再落到 v2）

### 4.2 关键：签名必须不破坏 4096 对齐

APEX 硬要求 `apex_payload.img` 的 **data offset % 4096 == 0**（apexd 挂载时校验）。

apksig 只在 Central Directory 之前插入 Signing Block，**不动任何既有条目数据**，
所以对齐天然保持。实测：

```
签名前 adbd.apex:  apex_payload.img data@4096  (%4096=0)
签名后 adbd.apex:  apex_payload.img data@4096  (%4096=0)  ✓
```

### 4.3 顺带补回 SELinux 标签

`apex_v2_sign.py` 在替换文件前把原 xattr 复制到新文件，
并兜底补写 `security.selinux = u:object_r:system_file:s0`。

---

## 5. 验证

### 5.1 用 apksig 自己的 ApkVerifier 交叉验证

`03-脚本/tools/ApexVerify.java`（用平台侧同一套校验逻辑）：

```
file            : adbd.out  (6980444 B)
verified        : true
  v1 scheme     : false
  v2 scheme     : true          ← ★
  v3 scheme     : false
errors          : 0
warnings        : 0
v2 signer #0 certs=1  subject=C=CN, ST=Local, O=Local, CN=WorkBuddy A16 Port
```

### 5.2 条目内容零改动

```
条目                        压缩       大小    内容一致?
apex_payload.img           0     6688768   ✓
assets/NOTICE.html.gz      0      265698   ✓
resources.arsc             0          40   ✓
AndroidManifest.xml        8        1080   ✓
apex_build_info.pb         8        1467   ✓
apex_manifest.pb           8         218   ✓
apex_pubkey                8        1032   ✓
```

（`META-INF/CERT.SF`、`META-INF/CERT.RSA`、`META-INF/MANIFEST.MF` 三个失效的 v1 残留被移除。）

### 5.3 全量结果

```
tree/system/apex 33 个包：
  签名块   33/33 OK
  xattr    33/33 OK
  payload data offset %4096 == 0  ✓
```

### 5.4 新增构建期防线

`build_system_diag_v3.sh` 反抽断言新增：

* **F 段** 扩充：`com.android.scheduling.apex` / `com.android.adbd.apex` 的
  `security.selinux` 必须是 `u:object_r:system_file:s0`
* **G 段（新）**：从镜像反抽真实 APEX，检查
  ① 有 `APK Sig Block 42` 魔数 ② payload data offset 是 4096 倍数

任一条不过 ⇒ `exit 1` ⇒ 不允许刷这个镜像。

---

## 6. 复现命令

```bash
cd ~/Documents/oppo/2026-安卓16/03-脚本

# 扫描哪些包缺签名块
/usr/bin/python3 apex_v2_sign.py --dry-run

# 补签（幂等，已签的跳过）
/usr/bin/python3 apex_v2_sign.py

# 重建 system 镜像（含 F/G 段断言）
./build_system_diag_v3.sh ~/Documents/oppo/2026-安卓16/02-移植素材/a16_patched/system_diag6.img
```

---

## 7. 教训

1. **「重打容器」必须盘点容器自身的元数据**。zip 不只有条目，还有
   Signing Block / CD / EOCD；EROFS 不只有内容，还有 xattr。
   第 5 号根因丢的是 EROFS xattr，第 6 号丢的是 zip Signing Block，**同一类错误犯了两次**。
2. **每一层校验都要有断言**。v3~v5 之所以反复卡在不同的「下一关」，
   就是因为前面几关没有自动断言，只能靠刷机后看日志发现。
3. **不要从文件名外推语义**（`apex_rebuild.py` 曾把 `xxx.apex.orig` 推成包名）。
4. **同一个密钥贯穿所有签名层**（AVB / apex_pubkey / v2 证书），
   可以一次性堵死「信任锚不匹配」这一类问题。

---

## 8. 第 7 号根因：`/system_ext/apex/` 也被扫，同样缺 v2 签名

> 时间：2026-10-02
> 现象：刷入 v6（`system_diag6.img` = 33 个 APEX 全量补签）后，system_server 仍 FATAL。

### 8.1 日志原话

```
E System  : java.lang.IllegalStateException: Failed to scan: /system_ext/apex/com.android.compos.apex
E System  : Caused by: android.util.apk.SignatureNotFoundException:
              No APK Signing Block before ZIP Central Directory
E Zygote  : System zygote died with fatal exception
```

**注意报错路径是 `/system_ext/apex/`，不是 `/system/apex/`。**

### 8.2 为什么上一轮没发现

`scanApexPackages` 扫的是**所有分区的 `/apex/` 目录**，不是只有 `/system/apex`：

| 分区 | `/apex` 是否存在 | 内容 |
|---|---|---|
| `system` | 有（实为 `/system/apex`，OPPO 布局下顶层 `/apex` 为空） | 33 个 APEX（v6 已全部补签） |
| `system_ext` | **有** | `com.android.compos.apex`、`com.android.vndk.v30.apex` |
| `product` | 无 | — |
| `vendor` | 无 | — |
| `odm` | 无 | — |

`build_system_ext_fix.sh` 早先用 `apex_rebuild.py` 重打过 `com.android.compos.apex`
（为了绕开 payload `incompat=0x3`），**同样丢了 Signing Block**，但当时没人想到
system_ext 也会被扫。设备实测：`com.android.compos.apex` 2452352 B，`cd_off` 前 16 字节
是随机数据而非 `APK Sig Block 42`。

> `com.android.vndk.v30.apex`（114728960 B）payload 是 ext4（magic `0x260`）、
> **没有重打过**，签名块与 xattr 都在，不需要处理。

### 8.3 修复流程

```bash
cd ~/Documents/oppo/2026-安卓16
EROFS=~/.workbuddy-ai/tools/erofs/erofs-utils/1.9.4/bin

# 1. 解包 system_ext（★ 必须 --xattrs，否则 3800+ 文件丢 SELinux 标签）
"$EROFS/fsck.erofs" --extract=07-重建/tree_system_ext --xattrs \
    02-移植素材/a16_patched/system_ext_fix.img

# 2. 补 v2 签名（幂等；已签的 vndk 会跳过）
/usr/bin/python3 03-脚本/apex_v2_sign.py --dir 07-重建/tree_system_ext/apex
#    -> OK com.android.compos.apex  已补 v2 签名（sigblock=4096, xattr=1, payload@4096）

# 3. 重建镜像（★ 不要用 --stream：mkfs 用 -T 0 会先建 2 TiB 稀疏文件，
#    且长时间任务必须走工具后台，nohup 会被会话回收）
/usr/bin/python3 03-脚本/erofs_rebuild.py \
    02-移植素材/a16_patched/system_ext_fix.img \
    07-重建/tree_system_ext \
    02-移植素材/a16_patched/system_ext_fix2.img \
    --zopts "-zlz4hc,level=9"
#    -> 979853312 字节，sha256 0c990cc55410c1fb67695695aca8b023f6ea88e4b693ad3668bdc36d88459f1a

# 4. 反抽验证
"$EROFS/fsck.erofs" --extract=/tmp/verify_compos.apex \
    --path=/apex/com.android.compos.apex 02-移植素材/a16_patched/system_ext_fix2.img
JAVA=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home/bin/java
"$JAVA" -cp ~/.workbuddy-ai/tools/apksig/apksig-8.7.3.jar:03-脚本/tools \
    ApexVerify /tmp/verify_compos.apex
#    -> verified: true / v2 scheme: true / errors: 0

# 5. 刷入（reflash_a16.sh 的 SYS_EXT 已指向 system_ext_fix2.img）
WB_YES=1 ./03-脚本/reflash_a16.sh --flash
```

### 8.4 结果

刷入后 `ro.build.version.release=16` / `sdk=36`，
system_server **越过了 APEX 扫描**，日志推进到包扫描阶段：

```
I PackagePartitionsExtImpl: custom partititions are overlaid;skip parse custom images
W OverlayConfig: Invalid partition_order.xml, partition_order.xml has 6 partitions,
                 which is different from SYSTEM_PARTITIONS
```

`Failed to scan` / `SignatureNotFoundException` 全部消失。

### 8.5 教训

1. **「哪些目录会被扫」必须实地枚举，不能靠推断**。`scanApexPackages` 覆盖
   system / system_ext / product / vendor / odm 的 `/apex/`。
   以后任何「重建 APEX」的动作，都要问一句：**这个包在哪个分区？那个分区的 `/apex` 会不会被扫？**
2. **验证要在设备上做，不要只在主机上做**。`adb exec-out cat` 拉回设备文件、
   本地查 `cd_off-16 == "APK Sig Block 42"`，是零成本确认「镜像真的刷进去了」的手段。
3. **构建期断言要按分区遍历**：把「所有挂载点的 `/apex/*.apex` 都有 Signing Block」
   做成一条硬断言，而不是只查 `/system/apex`。

