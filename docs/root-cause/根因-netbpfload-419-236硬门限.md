# 根因（最终）：netbpfload 的「4.19 内核必须 ≥ 4.19.236」硬门限 + SELinux 标签缺失

> 定位时间：2026-10-02
> 目标机：OPPO Find X2 Pro (PDEM30 / kona / SM8250)，内核 **4.19.157**
> 供体机：OnePlus 8T (KB2000 / kona)，内核 **4.19.325**
> 现象：刷入 ColorOS 16 (Android 16 / 25Q2 / SDK 36) 移植版后，**netd SIGABRT →
> OPPO Phoenix 判 bootup critical → 整机重启 → boot loop**

---

## 1. 结论（两句话）

移植版 netd 崩溃 **不是** 因为 `libnetd_updatable.so` 的 25Q2 门限（该门限早已被
NOP），而是因为 **`/sys/fs/bpf` 下一条 BPF 程序/映射都没有**，导致
`BpfHandler::init() → initPrograms() → checkProgramAccessible()` 失败，
`libnetd_updatable_init()` 拿到错误 Status 后 `abort()`。

而 `/sys/fs/bpf` 为空的原因有 **两个，必须同时修**：

| # | 根因 | 后果 | 状态 |
|---|---|---|---|
| **R1** | `/system/bin/netbpfload` 与 `/system/etc/init/wb_netbpfload.rc` 在镜像里**没有任何 `security.selinux` xattr** | init 拒绝 `exec_start wb_netbpfload`（permissive 下也拒），服务**从未运行** ⇒ BPF 未加载 | diag5 起已修 |
| **R2** | `netbpfload` 有硬门限 **「Android V+ requires 4.19 kernel to be 4.19.236+」** | 即使服务跑起来，内核 4.19.157 < 4.19.236 也会被拒后退出 ⇒ BPF 未加载 | 本次修复 |

---

## 2. 证据链

### 2.1 R2：反汇编 netbpfload 得到的三道门限

`netbpfload`（86,432 B，`/system/bin/netbpfload` 与
`/apex/com.android.tethering/bin/netbpfload` 是同一二进制）

```
0xbd9c: add  w9, w21, #0x50, lsl #12    ; 0x04090000 + 0x00500000 = 0x04590000 → 4.9.0
0xbdac: b.ls #0xc0b4                     → "Android S & T require kernel 4.9."      4.19 ✓
0xbdcc: add  w9, w21, #0xa0, lsl #12    ; + 0x00A00000 = 0x04120000 → 4.14.0
0xbdd8: b.ls #0xc0c8                     → "Android U requires kernel 4.14."        4.19 ✓
0xbde0: ldrb w8, [x8, #0x7c]            ; flag25Q2 @0x11407c
0xbde8: b.ne #0xbe00                     ; 非 25Q2 则跳过
0xbdf0: mov  w1, #4
0xbdf8: bl   #0xd8a0                     ; isAtLeastKernelVersion(5, 4, 0)
0xbdfc: tbz  w0, #0, #0xc0dc            → "Android 25Q2 requires kernel 5.4."      4.19 ✗ ★
0xbe14: bl   #0x110ec                    ; isLtsKernel()
0xbe1c: tbnz w0, #0, #0xbe38            → "Android V+ only supports LTS kernels."  待绕
0xbe44: bl   #0x118b8                    ; isKernelVersion(4, 19)
0xbe48: tbz  w0, #0, #0xbe90             ; 非 4.19 ⇒ 跳过
0xbe54: mov  w2, #0xec                   ; 236
0xbe58: bl   #0xd8a0                     ; isAtLeastKernelVersion(4, 19, 236)
0xbe5c: tbnz w0, #0, #0xbe90            → "Android V+ requires %d.%d kernel to be %d.%d.%d+."
                                          即 4.19.157 < 4.19.236 ⇒ 退出     ★★ 真凶
```

错误字符串（文件偏移，实测）：

| 偏移 | 内容 |
|---|---|
| `0x2687` | `Android S & T require kernel 4.9.` |
| `0x3921` | `Android U requires kernel 4.14.` |
| `0x2f76` | `Android 25Q2 requires kernel 5.4.` |
| `0x301c` | `Android V+ only supports LTS kernels.` |
| `0x2c07` | **`Android V+ requires %d.%d kernel to be %d.%d.%d+.`** |
| `0x3bb2` | `Unsupported kernel version (%07x).` |
| `0x29ea` | `Failed to set bpf.progs_loaded property to 1.` |

**供体与目标机的决定性差异**：

| 机器 | 内核 | `isAtLeastKernelVersion(4,19,236)` |
|---|---|---|
| OnePlus 8T（供体） | 4.19.325 | ✅ 通过 ⇒ BPF 正常加载 ⇒ netd 正常 |
| Find X2 Pro（目标） | 4.19.157 | ❌ 命中 ⇒ netbpfload 退出 ⇒ /sys/fs/bpf 空 ⇒ netd abort |

### 2.2 R1：镜像 xattr 实测

```
$ fsck.erofs --extract=<tmp> --path=/<path> --xattrs system_diag3.img
$ xattr -p security.selinux <tmp>
```

`system_diag3.img`（2026-10-01 16:20 构建，标签断言 10-02 01:05 才加入脚本 ⇒ 早于补丁）：

| 路径 | 实测标签 |
|---|---|
| `system/bin/netbpfload` | **（无 xattr）** ✗ |
| `system/etc/init/wb_netbpfload.rc` | **（无 xattr）** ✗ |
| `system/bin/bpfloader` | `u:object_r:bpfloader_exec:s0` ✓ |
| `system/bin/netd` | `u:object_r:netd_exec:s0` ✓ |
| `system/etc/init/hw/init.rc` | `u:object_r:system_file:s0` ✓ |

`system_diag5.img`（10-02 01:06，晚于标签修复）：全部 5 项 ✓ 正确。

### 2.3 netd 侧的完整调用链（源码级）

```
netd main
 └─ CgroupGetControllerPath(".cgroup2", &path)        netd VA 0x4a920~0x4a934
 └─ libnetd_updatable_init(path)                      netd VA 0x4a94c  ★
     └─ BpfHandler::init(cg2_path)                    BpfHandler.cpp:254
         ├─ if (!isAtLeast25Q2) waitForBpf();         25Q2=true ⇒ 【整体跳过】
         ├─ RETURN_IF_NOT_OK(initPrograms(cg2_path))  ← 失败点
         │   ├─ 版本门限 4.9/4.14/4.19/5.4            （5.4 已被 NOP）
         │   ├─ open("/sys/fs/cgroup")                ✓ 通过
         │   └─ checkProgramAccessible(               ✗ /sys/fs/bpf 为空
         │        "/sys/fs/bpf/netd_shared/prog_netd_skfilter_allowlist_xtbpf")
         │        → "Failed to get program from {}"
         └─ RETURN_IF_NOT_OK(initMaps())
     └─ !isOk(ret) ⇒ LOG(ERROR) << ": Failed: (" << code << ") " << msg;
                     abort();                        libnetd_updatable.so VA 0xa668
```

`libnetd_updatable.so` 反汇编确认 tombstone PC 归属：

```
0xa61c: add x1, x1, #0x46e     ; 0x246e = "libnetd_updatable_init"（22 字节）
0xa62c: add x1, x1, #0x9de     ; 0x19de = ": Failed: ("
0xa644: add x1, x1, #0x108     ; 0x2108 = ") "
0xa664: str w20, [x19]
0xa668: bl  #0xace8            ; ← abort()   tombstone 报 "libnetd_updatable_init.cfi+600"
```

`isAtLeast25Q2` 的判据：`libnetd_updatable.so` 字符串表含
`ro.build.version.codename`（文件偏移 0x1c3a，被 VA `0x4100: add x8, x8, #0xc3a` 引用）
与 `android_get_device_api_level`；平台版本戳写在
`/system/etc/init/netbpfload.rc` 首行：
`# 2025 2 36 0 0 # 25q2 sdk/api level 36.0 - Android 16 Baklava QPR0`。
**25Q2 ⇒ `isAtLeast25Q2 = true`** ⇒ `waitForBpf()` 被跳过（与 tombstone
`Process uptime: 0s` 相符：netd 没有等待，直接进 initPrograms 就 abort）。

历史 logcat 直接印证（`patch_netd_gate.sh` 记录，当时 `.so` 未打补丁）：

```
I NetdUpdatable: libnetd_updatable_init: Initializing
E NetdUpdatable: libnetd_updatable_init: Failed: (2147483647) 25Q2+ platform with kernel version < 5.4.0 is unsupported
F libc    : Fatal signal 6 (SIGABRT), code -1 (SI_QUEUE) in tid 3426 (netd)
  #01 /apex/com.android.tethering/lib64/libnetd_updatable.so (libnetd_updatable_init.cfi+600)
  #02 /system/bin/netd (main.cfi+228)
```

### 2.4 netbpfload 的完整行为（字符串表）

```
/apex/com.android.tethering/etc/bpf/netd_shared/     ← 加载后 pin 到 /sys/fs/bpf/netd_shared/
/apex/com.android.tethering/etc/bpf/net_shared/
/apex/com.android.tethering/etc/bpf/netd_readonly/
/apex/com.android.tethering/etc/bpf/net_private/
/apex/com.android.tethering/etc/bpf/tethering/
/system/bin/bpfloader                                 ← 末尾 exec 平台 bpfloader（Rust, BpfLoader-rs）
/system/etc/init/netbpfload.rc
/system/etc/init/bpfloader.rc
Platform has *both* bpfloader & netbpfload init scripts.   （本机只有 netbpfload.rc，不触发 ✓）
skipping map %s which requires kernel version 0x%x >= 0x%x （版本不满足的对象会被【静默跳过】）
Failed to set bpf.progs_loaded property to 1.              （SELinux 拒绝时才会出现）
```

平台 `bpfloader`（62,248 B，Rust `system/bpf/loader/bpfloader.rs`）**没有**任何内核版本门限，
只加载 `/system/etc/bpf/` 并 pin 到 `/sys/fs/bpf/` ✓。

### 2.5 供体 8T 的 BPF 对象本身支持 4.19

`/apex/com.android.tethering/etc/bpf/netd_shared/netd.o` 段表实测：

```
cgroupskb/ingress/stats$5_10_25q2     cgroupskb/ingress/stats$5_4_25q2
cgroupskb/ingress/stats$5_10_u        cgroupskb/ingress/stats$4_19
cgroupskb/ingress/stats$4_9           skfilter/{allowlist,denylist,egress,ingress}/xtbpf
bind4/inet4_bind  bind6/inet6_bind  connect4/inet4_connect  connect6/inet6_connect
getsockopt/prog   setsockopt/prog   recvmsg{4,6}/udp{4,6}_recvmsg  sendmsg{4,6}/udp{4,6}_sendmsg
bpfloader_min_ver  bpfloader_max_ver  btf_min_bpfloader_ver  btf_user_min_bpfloader_ver
```

**⇒ 存在 `$4_19` / `$4_9` 变体，BPF 对象本身完全支持 4.19 内核。**
被拦住的只是 `netbpfload` 的**保守版本门限**，不是内核能力不足。

---

## 3. 修复

### 3.1 `netbpfload` 三处补丁（`03-脚本/patch_netbpfload.py`）

| 偏移 | 原指令 | 新指令 | 绕过 |
|---|---|---|---|
| `0xbdfc` | `tbz w0,#0,#0xc0dc` `00 17 00 36` | `nop` `1f 20 03 d5` | `Android 25Q2 requires kernel 5.4.` |
| `0xbe1c` | `tbnz w0,#0,#0xbe38` `e0 00 00 37` | `b #0xbe38` `07 00 00 14` | `Android V+ only supports LTS kernels.` |
| `0xbe5c` | `tbnz w0,#0,#0xbe90` `a0 01 00 37` | `b #0xbe90` `0d 00 00 14` | `Android V+ requires 4.19 kernel to be 4.19.236+.` |

> ⚠️ **方向易错**：G4/G5 的原指令是「条件成立就跳到**正常**路径」，
> 所以必须改成**无条件 `b <正常路径>`**；若照 G3 那样改成 `nop`，
> 反而会掉进报错分支（语义正好相反）。

源 md5 `553aefcf58b59c43e6aa23df1a32aa50` → 产物 md5 **`9a6bdd2ccc56cb2011b0e9c8f315b14f`**（9 字节差异）。

### 3.2 SELinux 标签（`build_system_diag_v3.sh` 第 241-249 行）

`--replace` / `--add` 的 xattr 取自「**树内模板路径**」，用 `#` 指定：

```
--replace "/system/bin/netbpfload=$NEW/netbpfload#system/bin/bpfloader"
          ↑ 目标文件                  ↑ 内容            ↑ 标签模板 = bpfloader_exec
--replace "/system/etc/init/wb_netbpfload.rc=$NEW/wb_netbpfload.rc#system/etc/init/hw/init.rc"
```

脚本第 415-470 行会**反抽真实 xattr** 并断言，不通过直接 `exit 1`（"不要刷这个镜像"）。

### 3.3 保留 `wb_netbpfload` 而非还原 `bpfloader`

APEX 的 `netbpfload.35rc` 带 `override`，会把 `service bpfloader` 指向
**未打补丁**的 `/apex/com.android.tethering/bin/netbpfload`。所以 `init.rc` 保留

```
on load-bpf-programs
    exec_start wb_netbpfload        # → /system/bin/netbpfload（已打补丁）
on bpf-progs-loaded
    start netd
```

`exec_start` 是**同步**的，因此 `wb_netbpfload` 跑完才 `start netd` ——
与 AOSP「netd 在 bpfloader 之后启动」的时序一致 ✓。

---

## 4. 验证清单（刷机后）

```bash
# 1) 标签
adb shell ls -lZ /system/bin/netbpfload      # 期望 u:object_r:bpfloader_exec:s0
# 2) 服务是否真的跑过
adb shell getprop bpf.progs_loaded           # 期望 1
adb shell getprop init.svc.wb_netbpfload     # 期望 stopped（oneshot 跑完即停）
# 3) BPF 是否 pin 上
adb shell ls /sys/fs/bpf/netd_shared/        # 期望见到 prog_netd_* / map_netd_*
# 4) netd 是否还崩
adb shell getprop init.svc.netd              # 期望 running
adb logcat -b crash | grep -i netd           # 期望空
# 5) 离线取证（/metadata 挂载后）
cat /metadata/wbdiag/probe_bpf.log
```

镜像内已新增探针：`on bpf-progs-loaded` → `exec_start wb_probe_bpf`
（`/system/bin/sh /system/etc/wb_diag.sh bpf`），会把
`getprop` / `/sys/fs/bpf` 递归列表 / `dmesg | grep bpf` / `logcat -t 200`
全部写进 `/metadata/wbdiag/probe_bpf.log`。

---

## 5. 尚未排除的次要风险

1. **`isLtsKernel()`（0x110ec）** 在 4.19.157 上返回什么未实测。已用
   `b #0xbe38` 无条件绕过其报错退出（该函数全局仅此一处调用）。
2. **`netbpfload` 内部对单个 BPF 对象的 `min_kernel_ver` 判定**：
   `skipping map %s which requires kernel version ...` —— 若某对象的
   `min_kernel_ver > 0x04139D00`，会被静默跳过。`netd.o` 有 `$4_19` 变体，
   预计不触发；探针日志会直接暴露。
3. **`netd` 后续在 `tagSocket/untagSocket` 等路径** 若依赖特定 BPF map，
   可能仍报错（但不至于 SIGABRT）。
