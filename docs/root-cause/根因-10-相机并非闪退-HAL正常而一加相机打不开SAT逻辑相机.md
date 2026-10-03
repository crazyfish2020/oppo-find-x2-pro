# 根因 10 —— 相机并非「闪退」：HAL 完全正常，是一加相机打不开 SAT 逻辑相机

> 记录时间：2026-10-02
> 状态：**根因已确认**；已采纳方案 A（保留 OpenCamera 兜底），设备继续运行 Android 16
> **本文修正** `根因-9-相机闪退-一加相机与目标机配置不匹配.md` 的结论（根因 9 的方向不准确）

---

## 一、症状与关键辨析

* 现象：点开相机 → **无预览 / 黑屏**，用户描述为「闪退」。
* **关键事实：相机进程从未崩溃**。
  * 无 `FATAL EXCEPTION`，无 tombstone，无 ANR。
  * `am start -W` 返回 `Status: ok`，`TotalTime: 904ms`（冷启动）/ `250ms`（热启动）。
  * `topResumedActivity` 一度就是 `com.oneplus.camera/.OPCameraActivity`，进程一直存活（`S` 状态）。

> ★★ 根因 9 把这条当成主因是**误判**：
> ```
> E SizeUtils: java.lang.NumberFormatException: Invalid Size: ""
> E SizeUtils:   at com.oneplus.camera.capturemode.SlowMotionCaptureMode.<clinit>(SlowMotionCaptureMode.kt:62)
> ```
> 它只是**被 App 自己捕获**的 `E` 级日志（慢动作模式的尺寸列表读空），
> **不导致进程退出**，与「打不开」无因果关系。

---

## 二、真正的失败点：HAL 层 `configureStreams` 返回 -38

一加相机完整失败链路（`adb logcat`）：

```
Camera2ClientBase: Camera 6: Opened. Client: com.oneplus.camera (PID 16382, UID 10100)
CameraDeviceClient: CameraDeviceClient 6: Opened
CameraDeviceClient: createStream : stream size is 1440 x 1080
CameraDeviceClient: createStream : stream size is 4160 x 3120
CameraDeviceClient: createStream : stream size is 720 x 540

CamX     : camxhal3.cpp:1066 configure_streams() Number of streams: 3
CHIUSECASE: WUTONG.cpp:658   initPackageName() get vendor tags com.oplus.caller.package.name error!!
CHIUSECASE: WUTONG.cpp:729   CheckECSSupport() m_isSupportECS is 0
CHIUSECASE: WUTONG.cpp:1049  GetOplusCameraType() logicalCameraId 6, cameraType = 12
CHIUSECASE: usecase ID:6
CHIUSECASE: [MultiCamera] usecase name = UsecaseSAT, numPipelines = 15 numTargets = 15
CHIUSECASE: chxusecasemc.cpp:4838 CreateMultiCameraResource() Failed to alloc memory for Multi Camera Resources (Streams)
CHIUSECASE: chxusecasemc.cpp:5568 Initialize() Failed to Create Multi Camera Resource
CHIUSECASE: chxusecasemc.cpp:157  Create() Failed to create multicamera usecase: 8
CHIUSECASE: opluschxextensionmodule.cpp:1577 OnInitializeOverrideSession() For cameraId = 6 CreateUsecaseObject failed

Camera3-Device: Camera 6: configureStreamsLocked: Unable to configure streams with HAL: Function not implemented (-38)
CameraDeviceClient: endConfigure: Camera 6: Error configuring streams: Function not implemented (-38)
```

* `-38` = `ENOSYS`（Function not implemented）。
* 打开摄像头**成功**，配置流**失败** ⇒ 相机 App 拿不到任何 buffer ⇒ 界面只能黑屏。
* 注意 `dmesg` 中**没有 ION / dmabuf / OOM 报错**，`MemAvailable` 约 7 GB
  ⇒ 这里的 `Failed to alloc memory` 是 **HAL 内部的逻辑资源分配失败**，不是物理内存不足。

---

## 三、★ 对照实验（决定性）

用同一个 HAL、同一时刻，换一个「通用相机 App」做 A/B 对照：

| 相机 App | 打开的相机 | usecase | 结果 |
|---|---|---|---|
| **一加相机** `com.oneplus.camera` | **Camera 6**（`cameraType = 12`，SAT 多摄逻辑相机） | `UsecaseSAT`（15 pipeline / 15 target） | ❌ **-38** |
| **OpenCamera** `net.sourceforge.opencamera` | **Camera 0**（`cameraType = 0`，主摄） | `AdvancedCameraUsecase`（2 streams） | ✅ **成功** |

OpenCamera 日志（无任何 -38，`grep -c "Function not implemented"` = **0**）：

```
Camera2ClientBase: Camera 0: Opened. Client: net.sourceforge.opencamera (PID 17252, UID 10350)
CameraDeviceClient: createStream : stream size is 1920 x 1440
CameraDeviceClient: createStream : stream size is 4000 x 3000
CamX     : camxhal3.cpp:1066 configure_streams() Number of streams: 2
CHIUSECASE: WUTONG.cpp:1049 GetOplusCameraType() logicalCameraId 0, cameraType = 0
CHIUSECASE: usecase ID:3
CHIUSECASE: AdvancedCameraUsecase::Initialize usecaseId:1 num_streams:2
```

**实测预览画面正常**（截图存证）：
`05-分析/证据-相机/OpenCamera实测预览-2026-10-02.png`
（界面显示 `ID:0`、存储 `214.7GB`、曝光 `ISO 6400 1/30s`，画面为实时取景内容）

### ⇒ 结论

**Find X2 Pro 的相机硬件、CamX HAL、cameraserver、ION/dmabuf 链路 100% 正常。**
故障范围被精确缩小到：**一加相机选择了 SAT 逻辑相机（Camera 6），而该 usecase 在本机初始化失败。**

---

## 四、★ 深层原因：`/my_product` 是供体 8T 的分区

```
$ getprop ro.oplus.system.camera.name
com.oplus.camera                       ← 系统期望的相机包（来自目标机 odm）

$ getprop oplus.camera.packname
                                       ← 空！没有任何组件回填它

$ grep ro.product /my_product/build.prop
ro.product.brand=OnePlus
ro.product.device=OnePlus8T            ← my_product 是 8T 的！

$ pm list packages | grep -i camera
package:com.oneplus.camera             ← 实际存在的只有一加相机
package:com.oneplus.camera.service
package:com.oneplus.camera.pictureprocessing
package:com.oplus.engineercamera
（没有 com.oplus.camera）
```

`persist.camera.privapp.list` 同时列出两者，进一步说明是**混合状态**：

```
[persist.camera.privapp.list]: [com.oneplus.camera,com.oppo.camera,com.oplus.engineercamera,com.oplus.camera]
```

**死结结构**：

1. **目标机 odm**（未刷，保留原厂）声明「本机相机是 `com.oplus.camera`」；
2. **供体 my_product**（8T 移植包）提供的却是 **一加相机**；
3. 一加相机带着 **8T 的相机拓扑认知**去开 Find X2 Pro 的 **SAT 多摄逻辑相机（Camera 6）**，
   两者拓扑不同（8T 与 Find X2 Pro 的摄像头数量/类型不一致）⇒ SAT usecase 资源分配失败 ⇒ -38。

**这同时解释了为什么重刷同一套镜像无效**：`my_product` 不在这套包的刷写范围内
（本包只动 `system` / `system_ext` / `boot`），一加相机原样保留。

---

## 五、修复方案与采纳结果

| 方案 | 做法 | 代价 | 采纳 |
|---|---|---|---|
| **A. OpenCamera 兜底** | 已安装 `net.sourceforge.opencamera`（`OpenCamera_96.apk`，4,973,923 B） | 非原厂，无人像/夜景算法 | ✅ **用户选定** |
| B. 装原厂 OPPO 相机 | 提取 `com.oplus.camera` 装上，与 `ro.oplus.system.camera.name` 吻合 | 需先找到 ColorOS 15 官方包（**当前未找到**）；A15 的 APK 跑 A16 有兼容风险 | — |
| C. 刷回 ColorOS 15 官方 | 相机/指纹/NFC 全恢复原厂 | 系统降级到 Android 15，放弃 A16 | — |

安装命令（已完成）：

```bash
adb install -r ~/Documents/oppo/2026-安卓16/09-Magisk/apk/OpenCamera_96.apk
```

---

## 六、教训

1. **「闪退」必须先分清「进程崩溃」还是「功能不可用」**。
   本例进程完好、Activity 也起得来，是**下层 HAL 拒绝配置流**导致黑屏 ——
   盯着 App 的 `NumberFormatException` 查了一轮是白费的。
2. **`grep -c` 做 A/B 对照比读代码更有效**。
   「换一个通用 App 试同一个 HAL」一步就把故障范围从「整个相机栈」缩到「一个 App」。
3. **`getprop` 里的「期望值」与「实际存在的包」要交叉验证**。
   `ro.oplus.system.camera.name` 指向一个**不存在的包**，是移植包的典型悬空引用。
4. **移植时 `my_product` 的角色容易被忽略**。
   它承载大量厂商 App 与配置，一旦来自供体，就会出现
   「odm 说 A、my_product 给 B」的混合状态，且**重刷 system 无法修复**。
5. **`-38 / ENOSYS` 不一定是「接口不存在」**，在 CamX 里常是
   「上层传下来的配置组合，HAL 内部初始化失败后向上抛的通用错误码」，
   真正的错误要看 HAL 自己的 `CHIUSECASE` 日志。
