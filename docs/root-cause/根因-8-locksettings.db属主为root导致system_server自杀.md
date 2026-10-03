# 第 8 号根因：`/data/system/locksettings.db` 属主为 root → system_server 自杀 → boot loop

> 时间：2026-10-02
> 现象：v7 刷入（`system_diag6.img` + `system_ext_fix2.img`）后，Android 16 的 system_server
> **已越过 APEX 扫描、进入包扫描**，但启动约 100 s 后整机重启。
> 前置：第 3 号（SELinux）已由 permissive boot 绕过；第 4/5/6/7 号已修。
> 结果：修掉本根因后 **Android 16 首次完整启动成功**（`sys.boot_completed=1`）。

---

## 1. 症状（v7 实测日志原话）

崩溃前**最后一条 system_server 主线程日志**：

```
E AndroidRuntime: *** FATAL EXCEPTION IN SYSTEM PROCESS: main
E AndroidRuntime: java.lang.RuntimeException: Failed to boot service
    com.android.server.locksettings.LockSettingsService$Lifecycle:
    onBootPhase threw an exception during phase 480
E AndroidRuntime:  at com.android.server.SystemServiceManager.startBootPhase(SystemServiceManager.java:331)
E AndroidRuntime:  at com.android.server.SystemServer.startOtherServices(SystemServer.java:3422)
E AndroidRuntime:  at com.android.server.SystemServer.run(SystemServer.java:1083)
E AndroidRuntime: Caused by: android.database.sqlite.SQLiteCantOpenDatabaseException:
    Cannot open database [unable to open database file (code 14 SQLITE_CANTOPEN): Permission denied]
    '/data/system/locksettings.db' with flags 0x10000001: File /data/system/locksettings.db is not readable
E AndroidRuntime:  at android.database.sqlite.SQLiteConnection.open(SQLiteConnection.java:290)
E AndroidRuntime:  at com.android.server.locksettings.LockSettingsStorage.readKeyValue(LockSettingsStorage.java:163)
E AndroidRuntime:  at com.android.server.locksettings.LockSettingsService.onBootPhase(LockSettingsService.java:426)
E Process : Quit itself,  Pid:2163  StackTrace:...uncaughtException...
E Process : Sending signal. PID: 2163 SIG: 9
```

前置 10 s 还有 Watchdog 预警（主线程被 SQLite 打开重试卡住）：

```
E Watchdog: **pre_watchdog happen **Blocked in handler on main thread (main) for 15s,
            Blocked in handler on display thread (android.display) for 15s
I Magisk  : ** zygote restarted        ← 随后 system_server 被杀 → zygote 重启 → 整机重启
```

### 1.1 抓日志的方法（★ 值得复用）

启动窗口只有 ~100 s，必须在窗口内把「全量 + 流式」都抓下来：

```bash
./03-脚本/catch_boot.sh 04-日志/v8run
# 内部：adb logcat -d -b all  (立即 dump 已有缓冲)
#       adb logcat -b all -v threadtime  (流式，覆盖窗口后半段)
#       adb shell dmesg / getprop  (快照)
#       每 5 s 记一次 boot_completed / bootanim，掉线即停
```

定位「主线程最后在干什么」的高效命令（比翻全量快得多）：

```bash
# 先找到 system_server 的 pid（例如 2163），再看它主线程的最后活动
awk '$3==2163 && $4==2163' logcat_stream.txt | tail -30
```

---

## 2. 根因

### 2.1 设备实测

```
$ adb shell stat /data/system/locksettings.db
  File: /data/system/locksettings.db
  Size: 0   regular empty file
Access: (0600/-rw-------)  Uid: ( 0/ root)  Gid: ( 0/ root)

$ adb shell ls -la /data/system/ | head
-rw-rw----  1 system system 139264  OplusCarrierId.db     ← 正常
-rw-rw----  1 system system  20480  OplusScoreCard.db     ← 正常
-rw-------  1 root   root        0  locksettings.db       ← ★ 异常
```

* **system_server 跑在 uid 1000(`system`)**，`locksettings.db` 却是 `root:root 0600`
  ⇒ `open()` 直接 EACCES ⇒ SQLite 抛 `SQLITE_CANTOPEN`。
* `/data/system` 下**其他所有** DB 都是 `system system`，**只有这一个**被 root 占了。
* 全盘复查：`find /data/system -user root` → **0 条**（清理后）；
  同分钟（11:21）也没有其他同批产物 ⇒ 是**孤立的一次误操作**。

### 2.2 为什么不是 SELinux

```
$ adb shell ls -laZ /data/system/locksettings.db
-rw------- 1 root root u:object_r:system_data_file:s0  0  locksettings.db
                                   ^^^ context 本来就是对的

$ dmesg | grep 'avc:'
... avc: denied { set } for property=... permissive=1
                                    ^^^^^^^^^^^^^ 全部 permissive=1
```

**所有 avc 都带 `permissive=1`**（permissive 下不拦），且文件 SELinux 标签本身正确
⇒ **纯 DAC（Unix 权限）问题**。

> ★ 判据：**permissive 模式下还报 `Permission denied`，一定是 DAC，不是 SELinux。**
> 一条 `grep 'avc:' dmesg | tail` 看有没有 `permissive=1` 就能立刻排除 SELinux。

### 2.3 什么时候被写坏的

文件 mtime 落在**主机侧 TWRP/root shell 作业的那个时间点**（`2026-10-01 11:21` UTC）。
`/data/system` 目录本身是 `system system drwxrwxr-x`，所以**任何 root shell 的
`touch` / `> file` 重定向都会产出一个 root 属主的新文件**，而正常启动流程
（system_server / vold / init 的 `on post-fs-data`）从不会创建它。

---

## 3. 修复

设备进 TWRP（TWRP 的 adb 是 `uid=0(root)`，`/data` 已挂载 rw）：

```sh
adb reboot recovery
# 等 state=recovery

# 1) 先取证，再改（保留原始状态）
adb shell 'ls -laZ /data/system/locksettings.db'

# 2) 修属主（0 字节空文件，无数据可丢）
adb shell 'chown system:system /data/system/locksettings.db && chmod 600 /data/system/locksettings.db'

# 3) 复查
adb shell 'ls -laZ /data/system/locksettings.db'
# -rw------- 1 system system u:object_r:system_data_file:s0 0 ... locksettings.db

# 4) 全盘扫同类问题（关键！）
adb shell 'find /data/system -user root'          # 期望 0 条
adb shell 'find /data/system -maxdepth 1 -user root -type d'
```

> `/data/misc` 下会有 ~69 条 root 属主条目（`vold` / `keystore` / `hardware` / `pwm` …），
> 那是**正常设计**，不要动。

改完 `adb reboot`，实测 **50 s 后 `bootanim=stopped`、55 s `sys.boot_completed=1`**。

---

## 4. 结果（v8 实测）

```
5 s   bootanim=running
50 s  bootanim=stopped
55 s  boot_completed=1
240 s boot_completed=1     ← 连续稳定 4 分钟，无重启

$ adb shell getprop ro.build.version.release   → 16
$ adb shell getprop ro.build.version.sdk       → 36
$ adb shell dumpsys activity activities | grep topResumedActivity
  topResumedActivity=ActivityRecord{... com.oplus.account/
      com.platform.account.sign.register.activity.AccountRegisterMainActivity t2}
```

* 屏幕完整渲染（状态栏 / WiFi 图标 / 电量 100 / 导航栏），截图见
  `04-日志/v8run/screenshot_boot16.png`
* **WiFi 已连**：SSID `1001`，IP `192.168.0.106`，11ac，175 Mbps
* **SIM/基带已识别**：`中国联通`、`gsm.network.type = NR_SA`（5G SA）、`gsm.sim.state=LOADED,ABSENT`
* 关键服务全 running：`zygote` / `zygote_secondary` / `surfaceflinger` / `audioserver` /
  `cameraserver` / `netd` / `vold`
* `logcat -b crash` 里 `FATAL EXCEPTION IN SYSTEM PROCESS` 计数 = **0**

### 4.1 遗留（不阻塞启动）

| 项 | 现象 | 备注 |
|---|---|---|
| 机型标识 | `ro.product.model=KB2000` / `device=OnePlus8T` | 仍是供体 8T 的值，应改为 `PDEM30` / `Find X2 Pro` |
| SELinux | `androidboot.selinux=permissive` | 待回 enforcing（需给 apexd 域补策略） |
| ODM 崩溃 | `/odm/bin/hw/subsys_daemon` 在 `libradioapis.so::QmiVsClient::getNecData` `__memcpy_chk_fail` | ODM 与 A16 radio APIs 不匹配，影响部分射频特性；modem 本身已起来 |
| init 子进程 abort | `/system/bin/init second_stage` fork 在 `Service::RunService` abort ×2 | 某个 service exec 失败；非致命（主 init 存活） |

---

## 5. 教训

1. **「permissive 下还 Permission denied」⇒ 一定是 DAC**。
   先 `grep 'avc:' dmesg`，看到 `permissive=1` 就立刻把 SELinux 从嫌疑名单划掉，
   直奔 `ls -laZ` 看属主/权限。
2. **`/data/system/*.db` 必须 `system:system`**，因为 system_server 是 uid 1000。
   凡是**在 TWRP/root shell 里碰过 `/data`** 的会话，收尾都要跑一遍
   `find /data/system -user root`。
3. **取证要抓「崩溃前最后一条主线程日志」**。整机 boot loop 时，
   看 `awk '$3==<system_server pid> && $4==$3' logcat_stream.txt | tail`
   比通读日志快一个数量级。
4. **启动窗口短（~100 s）时，必须「先 dump 缓冲、再流式」双管齐下**。
   只 dump 会丢后半段，只流式会丢前半段。见 `03-脚本/catch_boot.sh`。
