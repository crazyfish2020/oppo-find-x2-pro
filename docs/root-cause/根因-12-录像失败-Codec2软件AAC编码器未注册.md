# 根因 12 —— 相机录像「无法保存视频」：Codec2 软件 AAC 编码器未注册

> ## ⚠️ 本文结论已被**证伪**，请勿据此操作
> **正确结论见《根因-13-录像失败真因-属性vendor.audio.c2.preferred未开启.md》**（2026-10-04）。
>
> 证伪要点：
> 1. `c2.android.aac.encoder` **本来就已正确注册**（原版 `/vendor/etc/media_codecs_google_c2_audio.xml` 里就有，
>    并带 `<Alias name="OMX.google.aac.encoder"/>`）。v2 模块新增的 13 条 C2 声明**全部**被
>    `MediaCodecsXmlParser` 判为 `cannot add existing codec`，纯属重复。
> 2. 真根因不是「C2 未注册」，而是 `/system_ext/lib/libavenhancements.so` 因为
>    `vendor.audio.c2.preferred` 未开启，把**本机从未声明的** `OMX.qcom.audio.encoder.aac`
>    塞进了候选编码器列表，导致 `CreateByComponentName` 失败。
> 3. v2 模块覆盖的 `/vendor/etc/media_codecs.xml` **根本不会被解析**；
>    `media_codecs_oplus_c2.xml` 里的 `<Alias>` 对 `CreateByComponentName` 也无效。
> 4. 因此 **v2 模块无效，应弃用**；请改用 `aac-c2-preference-fix-v1.zip`（纯属性修复）。
>
> 以下原文仅作历史记录保留。

> 日期：2026-10-03
> 现象：Open Camera 录像时提示「无法保存视频」，`/sdcard/DCIM/OpenCamera/` 不产生任何 MP4。
> 状态：**根因已闭环，修复模块已生成**（`09-Magisk/modules/codec2-audio-fix-v2.zip`）。

---

## 一、现象与日志链

复现：`input tap 720 2871`（开始录像）→ 等 6 秒 → `input tap 720 2871`（停止）→ 无文件产生。

```
W/StagefrightRecorder: Intended audio encoding bit rate (156000) is too large and will be set to (96000)
E/OplusACodec: [OplusACodec:206] mSrOsieMMListCheckResult:0x0 mOsieVersion:1
E/OplusACodec: loadExtlib: failed to dlopen lib: libcodecextimpl.so
D/OMX_DumpInput: [OMX.qcom.video.encoder.avc_282]
I/OMXMaster: makeComponentInstance(OMX.qcom.video.encoder.avc) in android.hardwar process   ← 视频编码器创建成功
I/OMX-VENC: Component_init : OMX.qcom.video.encoder.avc : return = 0x0
I/ExtendedACodec: setupVideoEncoder()
E/OplusACodec: [OplusACodec:206] mSrOsieMMListCheckResult:0x0 mOsieVersion:1
E/OplusACodec: loadExtlib: failed to dlopen lib: libcodecextimpl.so
E/ACodec  : Unexpected nullptr for codec information          ← 真正的失败点
E/ACodec  : signalError(omxError 0x80001001, internalError -2147483648)
E/MediaCodec: Codec reported err 0x80001001/-2147479551, actionCode 0, while in state 1/INITIALIZING
W/MediaCodecSource: releaseEncoder return directly as mEncoder is NULL
E/StagefrightRecorder: Failed to create audio encoder
```

**关键观察：音频路径根本没有 `OMXMaster: makeComponentInstance(...)` 日志** ——
说明音频编码器**连组件创建都没走到**，失败发生在 `MediaCodecList` 查表阶段。
视频编码器（`OMX.qcom.video.encoder.avc`）完全正常，所以 `screenrecord` 能成功产出 MP4。

## 二、排除的红鲱鱼

| 候选原因 | 为什么不是 |
|---|---|
| `libcodecextimpl.so` 缺失 | 视频路径也报同样的两行 `OplusACodec` 日志，但视频编码正常。全盘 `find / -name "*codecext*"` 无结果，属 OPPO 扩展音频后处理的噪声日志。 |
| `MediaRecorder` 权限不足 | `RECORD_AUDIO` / `CAMERA` 均 `granted=true`。 |
| 视频编码器坏 | `screenrecord --time-limit 3` 成功产出 1,926,000 B MP4。 |
| `/data` 空间/挂载 | `/sdcard/DCIM/OpenCamera/` 可正常写 JPG。 |

## 三、反汇编定位（决定性）

`adb pull /system/lib64/libstagefright.so`，用 pyelftools + capstone 定位
字符串 `Unexpected nullptr for codec information`（`.rodata` @ `0x6257a`），
找到唯一引用点 `0xd265c`：

```
0xd265c -> _ZN7android6ACodec18UninitializedState19onAllocateComponentERKNS_2spINS_8AMessageEEE+0x2a4
```

⇒ 报错来自 **`ACodec::UninitializedState::onAllocateComponent()`**，
即 `mCodecInfo == nullptr` —— **框架的编解码器清单里找不到这个音频编码器**。

## 四、为什么清单里没有 AAC 编码器

### 4.1 框架加载哪些配置

`/system/lib64/libstagefright_xmlparser.so` 里的路径字符串：

```
/vendor/etc/media_codecs.xml              ← OMX 基础清单（硬编码）
/vendor/etc/media_codecs_performance.xml
ro.media.xml_variant.codecs               ← 变体选择属性
```

实测 `getprop ro.media.xml_variant.codecs` = **`_kona`**
⇒ 变体文件 `/vendor/etc/media_codecs_kona.xml`。

本机实际加载的 OMX 清单 = `media_codecs.xml` + `media_codecs_kona.xml`，
两者的 `Include` 链为：

```
media_codecs.xml        → media_codecs_google_audio.xml / _telephony / _video
media_codecs_kona.xml   → media_codecs_google_audio.xml / media_codecs_vendor_audio.xml / ...
```

**没有任何一个 Include 指向 Codec2 的音频配置。**
（`/odm/etc/media_codecs_c2.xml` 等 ODM 文件因为 `/vendor/etc/media_codecs.xml` 不引用它们，
且框架的 ODM 顶层文件 `/odm/etc/media_codecs.xml` 在本机**不存在**，所以整条 ODM 链没被加载。）

### 4.2 Codec2 编解码器怎么注册

`adb pull /system/lib64/libsfplugin_ccodec.so`（`Codec2InfoBuilder`）里的关键字符串：

```
搜索目录: /product/etc, system_ext/etc, /vendor/etc, odm/etc, /system/etc,
          /apex/com.android.media.swcodec/etc, /apex/com.android.media/etc/formatshaper
文件名:   media_codecs_oplus_c2.xml   ← OPPO 自有命名（A16 框架期待的名字）
          media_codecs_performance_c2.xml
          media_codecs_c2_dolby_audio.xml
          media_codecs_dolby_vision.xml
          media_codecs_shaping.xml
          media_codecs_performance.xml
报错串:   component '%s' not found in xml
          alias '%s' not found in xml; use an XML <Alias> tag for this
```

**Codec2 组件必须由 XML 显式声明才会被注册**（`component not found in xml`）。

本机 `/vendor/etc/` 下 **`media_codecs_oplus_c2.xml` 与 `media_codecs_c2.xml` 都不存在**；
ODM 里只有旧命名的 `/odm/etc/media_codecs_c2.xml`（内容仅 Include 了
`media_codecs_c2_oplus_audio.xml`，只声明 `c2.oplus.ozoaudio.*`，**不含 AAC**）。

### 4.3 组件其实存在，只是没被登记

```
/apex/com.android.media.swcodec/lib64/libcodec2_soft_aacenc.so          ← 存在
/apex/com.android.media.swcodec/etc/media_codecs.xml 第 270 行:
    <MediaCodec name="c2.android.aac.encoder" type="audio/mp4a-latm">
        <Alias name="OMX.google.aac.encoder" />
```

软件编解码器仓库里**有** `c2.android.aac.encoder`，
但框架侧 `MediaCodecList` 没登记它，于是 `MediaRecorder` 找不到可用的 AAC 编码器。

### 4.4 旧式 OMX 软件编码器为什么也救不了场

```
/vendor/lib/   libstagefright_soft_aacenc.so  ← 只有 32 位
/vendor/lib64/ （无 libstagefright_soft_aacenc.so）
```
`/vendor/etc/media_codecs_google_audio.xml` 里声明的 `OMX.google.aac.encoder`
是传统 SoftOMX 组件，64 位路径下没有对应实现库，同样无法实例化。

## 五、结论

> **录像失败 = 系统没有任何一个可用的 AAC 音频编码器。**
> Codec2 软件编码器（`c2.android.aac.encoder`）存在但未在 XML 中声明，
> 传统 OMX 软件编码器（`OMX.google.aac.encoder`）缺少 64 位实现库。

顺带结论：**AAC / MP3 / Opus / Vorbis / FLAC / AMR 等音频解码大概率同样受损**
（都依赖同一批缺失的软件实现），修复后可一并恢复。

## 六、修复方案

Magisk 模块 `codec2-audio-fix-v2.zip`（systemless overlay，仅覆盖 `/vendor/etc/` 下 3 个文件）：

| # | 文件 | 动作 |
|---|---|---|
| A | `/vendor/etc/media_codecs_oplus_c2.xml` | **新增**，声明 `c2.android.aac/amr/mp3/opus/vorbis/flac/raw/g711` 解码器与 AAC/AMR/FLAC/Opus 编码器，并带 `<Alias>`；AAC 编码器加 `rank="512"` |
| B | `/vendor/etc/media_codecs_google_audio.xml` | 移除失效的 `OMX.google.aac.encoder` 条目，避免被优先选中 |
| C | `/vendor/etc/media_codecs.xml` | 追加 `<Include href="media_codecs_google_c2_audio.xml" />`（冗余路径） |

卸载模块重启即可完全回退。

## 七、复现与验证命令

```bash
# 复现
adb logcat -c
adb shell 'am start -n net.sourceforge.opencamera/.MainActivity'
sleep 5; adb shell 'input tap 720 2871'; sleep 6; adb shell 'input tap 720 2871'
adb shell 'ls -la /sdcard/DCIM/OpenCamera/ | tail -3'      # 修复后应出现 .mp4
adb logcat -d | grep -E "Failed to create audio encoder|c2.android.aac.encoder"
```

## 八、工具与方法备忘

- 反汇编定位报错语义：`/tmp/find_str_ref.py`（pyelftools + capstone 找 ADRP+ADD 引用）
- 符号解析：`/tmp/resolve_sym.py`（dynsym 就近匹配，确认落在
  `ACodec::UninitializedState::onAllocateComponent`）
- 静态初始化字符串表提取：`/tmp/list_c2names.py`（确认 `media_codecs_oplus_c2.xml` 确实在名单里）
- ★ 教训：`grep -l` 搜 `aac` 只能找到 google 的音频配置，
  **Codec2 的注册清单要用 `libsfplugin_ccodec.so` 的字符串去挖**，光看 `/vendor/etc` 会漏。
