# 根因 13 —— 录像「无法保存视频」的**真正**根因：`vendor.audio.c2.preferred` 未开启

> 结论日期：2026-10-04 00:55
> 状态：✅ **已修复并重启后验证通过**（MP4 含 vide/avc1 + soun/mp4a 双轨）
> ★ 本文**取代**《根因-12-录像失败-Codec2软件AAC编码器未注册.md》（该文结论是错的）

---

## 一、一句话结论

`/system_ext/lib/libavenhancements.so`（高通 AV Enhancements，移植自 OnePlus 8T 的 ColorOS 16）
在构造「自定义音频编码器候选列表」时读属性 **`vendor.audio.c2.preferred`**：

* 为 `true` → 打印 `CCodec preferred, skip creation of custom OMX audio encoders`，**返回空列表**
  → 系统按 mime 自动选到 `c2.android.aac.encoder` → **录像正常**；
* 为 `false`/未设置（本机默认）→ 调用 `AVConfigHelper::useHwAACEncoder()`（读 `vendor.audio.hw.aac.encoder`，本机为 `true`）
  → 把 **`OMX.qcom.audio.encoder.aac`** 放进候选列表 → 创建失败 → 录像无音轨、文件无法保存。

**修复 = 设 `vendor.audio.c2.preferred=true`**（Magisk 模块 `aac-c2-preference-fix-v1.zip`，纯属性修复，不动任何分区文件）。

---

## 二、失败链（logcat 实证）

```
D/MediaCodec : CreateByComponentName: name OMX.qcom.audio.encoder.aac
D/OplusACodec: OplusACodec [149]
D/OplusACodec: ACodec() ro.oplus.audio.effect.type = dolby ...
D/OplusACodec: getBufferChannel: New
D/MediaCodec : [0x...] init: CCodec 0x..., CCodecBufferChannel 0x...
D/MediaCodec : [0x...] setState: 1
E/ACodec     : Unexpected nullptr for codec information          ← 关键
E/ACodec     : signalError(omxError 0x80001001, internalError -2147483648)
E/MediaCodec : Codec reported err 0x80001001/-2147479551, actionCode 0, while in state 1/INITIALIZING
W/MediaCodecSource: releaseEncoder return directly as mEncoder is NULL
E/StagefrightRecorder: Failed to create audio encoder
```

* 音频路径**完全没有** `OMXMaster: makeComponentInstance(...)` ⇒ 失败发生在 **MediaCodecList 查表阶段**，没走到 OMX 实例化。
* 对比视频编码器（同一个 `MediaCodec::init` 路径）：
  `CreateByComponentName: OMX.qcom.video.encoder.avc` → `OMXMaster: makeComponentInstance(OMX.qcom.video.encoder.avc)` ✅

---

## 三、为什么之前所有尝试都失败（重要的踩坑记录）

### 3.1 `media_codecs_oplus_c2.xml` 加 `<Alias>` 无效
AOSP `MediaCodecList::findCodecByName()`（libstagefright32.so `0x11fdbd`，600 B）**确实会遍历 `getAliases()`**
（`blx getAliases` + `AString::operator==`），但……

**`findCodecByName` 在整个 libstagefright32.so 里从来没有被 BL/BLX 调用过**
（`blx_scan.py` 半字暴力扫描：`hits: 0`）。
`MediaCodec::CreateByComponentName`（`0x111a7c`）走的是注入进 `MediaCodec` 构造函数的
`std::function<int(AString const&, MediaCodecInfo**)>` 工厂，**不是** `findCodecByName`。

⇒ **别名方案在本机无效**（实测：加了别名后 dumpsys 能看到 alias 列表，但 CreateByComponentName 依然 `Unexpected nullptr`）。

### 3.2 往 `media_codecs.xml` / `media_codecs_oplus_c2.xml` 里加 OMX 声明无效
解析日志（`MediaCodecsXmlParser`）证明设备上真正被加载的 XML 是：

| 消费者 | 进程 | 加载的 XML |
|---|---|---|
| OMX 服务 | `vendor.media.omx`（进程名 `media.codec`） | **`/vendor/etc/media_codecs_kona.xml`** + `media_codecs_google_audio.xml` / `media_codecs_vendor_audio.xml` / `media_codecs_google_telephony.xml` / `media_codecs_google_video.xml` + `media_codecs_performance_kona.xml` + `/odm/etc/media_codecs_vendor_oplus.xml` → ffmpeg / dolby / `media_codecs_odm.xml` |
| mediaserver（Codec2） | `mediaserver` | `/apex/com.android.media.swcodec/etc/media_codecs.xml`、`/odm/etc/media_codecs_c2.xml`、`/odm/etc/media_codecs_c2_oplus_audio.xml`、`vendor/etc/media_codecs_oplus_c2.xml`、**`/vendor/etc/media_codecs_kona_vendor.xml`**、`media_codecs_performance_kona_vendor.xml` |

* **`/vendor/etc/media_codecs.xml` 根本不会被解析**（往里加东西 100% 无效）。
* 文件名由 `getVendorXmlPath()` 用 `ro.media.xml_variant.codecs=_kona` 推导：
  OMX 侧 → `media_codecs_kona.xml`；Codec2 侧 → `media_codecs_kona_vendor.xml`（注意两者**不同名**）。
* 实测：往 `media_codecs_kona.xml` 里补 `OMX.qcom.audio.encoder.aac` 后，重启 `vendor.media.omx`，
  `dumpsys media.player` 里**确实出现了**该 Encoder 条目 —— **但 `CreateByComponentName` 依旧 `Unexpected nullptr`**。
  ⇒ 列表里有 ≠ 能创建，问题不在 XML。

### 3.3 组件本身其实是存在的
* `/vendor/lib/libOmxCore.so` + `/vendor/lib64/libOmxCore.so` 的注册表里**有** `OMX.qcom.audio.encoder.aac`；
* 实现库 `/vendor/lib{,64}/libOmxAacEnc.so`（78,544 B）也在。
* ⇒ 只是**从未被任何 media_codecs XML 声明**（原厂 Find X2 Pro 的 vendor 就是如此）。

### 3.4 字符串到底在哪
`grep -rl 'qcom.audio.encoder' /system/lib* /system_ext/lib* /vendor/lib* /odm/lib* /product/lib*` →
```
/system_ext/lib/libavenhancements.so        ← 真凶
/system_ext/lib64/libavenhancements.so
/vendor/lib{,64}/libOmxAacEnc.so, libOmxCore.so, libmm-omxcore.so, ...
```
**`libmediaplayerservice32.so` / `libstagefright32.so` 里根本没有这个字符串** —— 之前的推测是错的。

---

## 四、反汇编实证（`libavenhancements32.so`，Thumb-2）

```
0x00038a76  add r1,pc   ; "mime"
0x00038a78  blx AMessage::findString("mime", &AString)
0x00038a7e  beq.w #0x38e00                  ; 找不到 mime -> assert
0x00038a86  add r0,pc   ; "vendor.audio.c2.preferred"
0x00038a88  blx property_get_bool           ; 默认 false
0x00038a8c  cbz r0, #0x38aa0                ; ★ false -> 走 useHwAACEncoder 分支
...
0x00038a14  blx __android_log_print         ; "ExtendedUtils"
0x00038e1c  add r2,pc   ; "CCodec preferred, skip creation of custom OMX audio encoders"
0x00038e22  b   #0x38a9c                    ; 直接返回（空候选表）

; ---- c2.preferred == false 时 ----
0x00038aa0  blx AVConfigHelper::getInstance()
0x00038acc  blx AVConfigHelper::useHwAACEncoder()   ; 读 vendor.audio.hw.aac.encoder
...
0x00038d50  add r1,pc   ; "OMX.qcom.audio.encoder.aac"   ★ 放进 Vector<AString> 候选表
0x00038d6e  blx VectorImpl::push
```

字符串 VA 与引用点（`brute_pcrel.py` 半字暴力扫描）：

| 字符串 | VA | 引用 |
|---|---|---|
| `CCodec preferred, skip creation of custom OMX audio encoders` | 0x276c9 | `add@0x38e1c` |
| `CCodec preferred, skip creation of custom OMX audio components` | 0x220f5 | `add@0x380b8` |
| `OMX.qcom.audio.encoder.aac` | 0x246d0 | `add@0x38d50` |
| `vendor.audio.c2.preferred` | 0x289a2 | `add@0x37cdc`, `add@0x38a86` |
| `vendor.audio.hw.aac.encoder` | 0x1f65d | `add@0x31bf4`（`AVConfigHelper::useHwAACEncoder`） |

---

## 五、修复与验证

### 5.1 即时验证（`property_get_bool` 每次调用都读，无需重启）
```bash
adb shell su -c 'setprop vendor.audio.c2.preferred true'
# 然后录像
```
结果：
```
D/MediaCodec : CreateByComponentName: name c2.android.aac.encoder     ← 直接按 mime 选到 C2
I/CCodec     : Created component [c2.android.aac.encoder] for [c2.android.aac.encoder]
D/MPEG4Writer: Received total/0-length (272/0) buffers and encoded 271 frames. - Audio
D/MPEG4Writer: MOOV atom was written to the file
D/MPEG4Writer: final fsync() takes 65 ms, file size 14851770
```
**报错计数 = 0**（`Failed to create audio encoder` / `Unexpected nullptr` 全消失）。

### 5.2 持久化
Magisk 模块 **`aac-c2-preference-fix-v1.zip`**（`09-Magisk/modules/`，同时已放 `/sdcard/Download/`）：
```
module.prop
system.prop          → vendor.audio.c2.preferred=true      （Magisk post-fs-data 用 resetprop 应用）
post-fs-data.sh      → resetprop -n vendor.audio.c2.preferred true   （双保险）
README.txt
```
**不新增/不覆盖/不删除任何分区文件。**

安装：`adb shell su -c 'magisk --install-module /data/local/tmp/aac-c2-preference-fix-v1.zip'` → 重启。

### 5.3 重启后复验（2026-10-04 00:54）
```
getprop vendor.audio.c2.preferred → true            ← 模块自动生效
mount | grep media_codecs_kona.xml → （空）          ← 临时 bind mount 已消失
/vendor/etc/media_codecs_kona.xml  → 0 处新增        ← 已恢复原版
/vendor/etc/media_codecs.xml       → 0 处新增        ← 已恢复原版
/vendor/etc/media_codecs_oplus_c2.xml → No such file ← v2 模块 overlay 已消失
```
录像结果 `VID_20261004_005417.mp4`（19,978,518 B），拉回本地解析 MP4 原子：
```
moov / mvhd / trak ×2 / mdia ×2 / mdat 全部存在
trak#1: vide + avc1        （H.264 视频轨）
trak#2: soun + mp4a + esds （AAC 音频轨）
```
**✅ 双轨齐全，修复彻底生效。**

---

## 六、附带澄清的其他红鲱鱼

1. `/vendor` 是 **erofs 只读**，无法 `mount -o rw,remount`；但 `/vendor/etc` 被 Magisk 以 **tmpfs** 覆盖，
   且其中的文件是**逐个从 `/dev/block/sda11`（=data，即 Magisk 模块目录）bind mount** 过来的。
2. `mount --bind` 做的临时覆盖在 `su` shell 与 mediaserver 之间**可见性正常**（`/proc/<pid>/mountinfo` 可证）。
3. `dumpsys media.player` 的编解码器列表**不会触发新的 XML 解析**（`logcat -c` + dumpsys 后无 `MediaCodecsXmlParser` 输出）
   ⇒ 列表确实来自 mediaserver 自己的 `MediaCodecList` 单例。
4. `media_codecs_kona.xml` 与 `media_codecs_kona_vendor.xml` 内容几乎相同（27,952 B），
   但**消费者不同**（OMX 服务 / Codec2），改错文件等于没改。
5. 失败时**没有** `OMXMaster: makeComponentInstance` 日志 ⇒ 不要再去查 OMX 实现库或 OMX 服务。
