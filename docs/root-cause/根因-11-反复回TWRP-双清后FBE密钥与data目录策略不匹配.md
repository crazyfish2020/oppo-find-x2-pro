# 根因 11 —— 反复回 TWRP：双清后 FBE 密钥与 `/data` 目录策略不匹配

> 记录时间：2026-10-02
> 状态：**根因已确认，已修复，验证通过**
> 触发场景：用户在已成功的 A16 上做「恢复出厂设置」→ 设备自动进 recovery → 手动格式化双清
> 相关：`根因-8-locksettings.db属主为root导致system_server自杀.md`（同为 `/data` 层问题）

---

## 一、症状

用户操作链：**恢复出厂设置 → 自动进 recovery（TWRP）→ 格式化双清 → 之后每次开机都落回 TWRP**。

* 设备能进 TWRP，但 `reboot system` 后又回到 TWRP。
* 不是 bootloop（没有反复重启），而是**每次都被「引导」进 recovery**。

---

## 二、定位手段：TWRP 的 `/tmp/recovery.log` 头部

TWRP 会在 `/tmp/recovery.log` 开头记录**它是被什么原因启动的**：

```
boot command: boot-recovery
Android Rescue Party trigger!
The reported problem is:
 '--reason=enablefilecrypto_failed'
```

**第一次失败**：`--reason=enablefilecrypto_failed`

---

## 三、★ 把「为什么进 recovery」从猜测变成铁证

在真 `/system/bin/init`（3,528,608 B）里定位字符串：

```bash
adb pull /mnt/system/system/bin/init ./sys_init.bin      # /mnt/system/system 才是真 /system
strings -a -t x sys_init.bin > str.txt
grep -n 'enablefilecrypto' str.txt                        # → 3410:24452
sed -n "3365,3435p" str.txt
```

字符串表相邻项揭示了代码分支：

```
24426 mark_post_data
24435 mkdir
2443b copy_update_engine_log
24452 enablefilecrypto                                                    ← 失败点
24463 fs_mgr_mount_all suggested recovery, so wiping data via recovery with prompt.   ← 紧邻下一条！
```

⇒ 对应 AOSP `init.cpp` 分支：
**`fs_mgr_mount_all()` 判定「需要 recovery 擦除 `/data`」→ 写 BCB `boot-recovery --reason=...` → 重启 → ABL 进 TWRP。**

**「为什么进 recovery」至此不再是猜测。**

---

## 四、根因链（两环）

### 第 1 环 —— `enablefilecrypto_failed`

「恢复出厂设置」会重置 `/data`，但**没有重置 FBE 密钥材料**；
旧密钥与新的空 `/data` 对不上，`on post-fs-data` 里的 `installkey /data` 失败。

`init.rc` 中相关片段（`/mnt/system/system/etc/init/hw/init.rc`）：

```
on post-fs-data
    write /metadata/wbdiag/48_on_post_fs_data 1
    exec - system system -- /system/bin/vdc checkpoint prepareCheckpoint
    chown system system /data
    chmod 0771 /data
    restorecon /data
    # Make sure we have the device encryption key.
    installkey /data                                  ← 第 1 环失败点（FBE 密钥装载）
    mkdir /data/bootchart 0755 shell shell encryption=Require
    ...
    mkdir /data/misc 01771 system misc encryption=Require
    mkdir /data/property 0700 root root encryption=Require
    mkdir /data/cache 0770 system cache encryption=Require      ← 第 2 环失败点
    ...
```

### 第 2 环 —— `set_policy_failed:/data/cache`

第一次修复后重启，又回 TWRP。拉第二次的 `/tmp/recovery.log`：

```
'--reason=set_policy_failed:/data/cache'
```

原因：**旧密钥时代创建的目录（`/data/cache` 等）带着旧的 FBE 策略**，
与新建的密钥不匹配 ⇒ `mkdir ... encryption=Require` 设策略失败。
（同时观察到 `/data/unencrypted/key/*` 已被成功重建，`/data` 从 7 个目录变成 14 个 —— 说明第 1 环确实修好了。）

---

## 五、★ 修复动作（两步，均已验证有效）

```bash
# 第 1 步：重置 FBE 密钥材料（让 Android 重新生成密钥）
adb shell '
  mount -t f2fs /dev/block/sda11 /data
  rm -rf /data/unencrypted /data/gsi
  mount -t ext4 /dev/block/sda7 /mnt/meta
  rm -f  /mnt/meta/bootstat/persist.sys.boot.reason
  rm -rf /mnt/meta/gsi/dsu /mnt/meta/gsi/phh
  dd if=/dev/zero of=/dev/block/by-name/misc bs=4096 count=1
  sync'

# 第 2 步：彻底清空 /data 内容（保留 lost+found），让系统用新密钥重建全部目录
adb shell '
  mount -t f2fs /dev/block/sda11 /data
  cd /data
  for d in *; do [ "$d" = "lost+found" ] || rm -rf "$d"; done
  sync'

adb shell reboot
```

> 备份（修复前已做）：`/tmp/opx/backup/ue.tar`（`/data/unencrypted`，25,088 B）、
> `/tmp/opx/backup/meta.tar`（`bootstat` + `wbdiag` + `gsi/phh`，5,336,064 B）。

### 验证结果

```
sys.boot_completed = 1
ro.build.version.release = 16
ro.build.version.sdk = 36
crypto.state = encrypted / file
init.svc.vold / zygote / surfaceflinger / keystore2 = running
```

**Android 16 成功启动，不再回 TWRP。**

---

## 六、辅助定位手段（可复用）

1. **自建面包屑**：`init.rc` 里写 `/metadata/wbdiag/NN_xxx 1`。
   本次「`48_on_post_fs_data` 有更新、`50_on_boot` 无更新」⇒ 卡在 post-fs-data，节省大量时间。
2. **FBE 密钥材料完好性检查**（本次修复前）：
   | 文件 | 大小 | 非零字节 |
   |---|---|---|
   | `secdiscardable` | 16384 | 16336 |
   | `keymaster_key_blob` | 463 | 161 |
   | `encrypted_key` | 92 | 92 |
   | `version` | 1 | 1 |
3. **`/data` 是 `latemount`**（`/vendor/etc/fstab.qcom`）：
   `fileencryption=ice,wrappedkey,quota,reservedsize=128M,checkpoint=fs,inlinecrypt`

---

## 七、教训

1. **「双清」≠「重置加密密钥」**。
   跨版本/跨机型刷机时，`/data/unencrypted`（密钥材料）与 `/data/*` 的 FBE 策略必须**成对处理**，
   只清其一就会得到 `enablefilecrypto_failed` 或 `set_policy_failed`。
2. **TWRP 的 `/tmp/recovery.log` 第一屏就是答案**。
   `--reason=` 直接告诉你「谁、为什么把你送进 recovery」，比翻 dmesg 快得多。
3. **`strings` 找「失败点字符串 + 紧邻下一条」可以还原代码分支语义**，
   在无源码、无符号的厂商 `init` 上尤其有效。
4. **`reservedsize=128M` 未生效**（f2fs `block_count × 4096` = 分区大小，fs 占满整个分区），
   与 fstab 的 `checkpoint=fs` 不配套 —— 属**遗留隐患**，可能影响后续 OTA/checkpoint，
   本次未处理（见待办）。
