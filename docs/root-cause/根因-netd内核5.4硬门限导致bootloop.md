# 根因（第 4 号）：`libnetd_updatable.so` 内核 5.4 硬门限 → netd SIGABRT → boot loop

- **日期**：2026-10-02
- **机型**：OPPO Find X2 Pro（PDEM30 / kona / SM8250），内核 **4.19.157-color597**
- **系统**：ColorOS 16 移植（Android 16 / SDK 36，25Q2 基线），供体 OnePlus 8T
- **状态**：✅ 已修复并刷入验证（v6 镜像）。实测 netd 不再 SIGABRT —— 日志出现
  `Setting up TetherController hooks` / `Initializing RouteController` / `Initializing XfrmController` /
  `Enabling bandwidth control`，直接推进到 `system_server` 初始化。下一层阻塞（APEX 缺 v2 签名）
  见第 6 号根因。
- **现象**：`androidboot.selinux=permissive` 修好 SELinux 之后，A16 能起（`release=16 / sdk=36`、`/apex` 72 项、844 进程、SurfaceFlinger/zygote running），但**随即进入 boot loop**：刚显示一加 logo 就重启，反复循环，**双清无效**。

---

## 1. 一句话结论

**不是硬件故障，也不是双清能解决的问题。** 是我们移植进来的 Android 16 里
`/apex/com.android.tethering/lib64/libnetd_updatable.so` 带了一条
**「25Q2+ 平台要求内核 ≥ 5.4.0」的硬门限**，而本机内核是 4.19.157。
不满足时它直接 `abort()`；`netd` 又被 OPPO Phoenix 判定为 **bootup critical service**，
崩一次就整机重启 ⇒ zygote 反复重启 ⇒ `sys.boot_completed` 永远不置位 ⇒ 无限循环。

双清之所以无效，正因为问题在**系统镜像内部**，与 `/data` 无关。

---

## 2. 证据链（全部实测）

### 2.1 logcat（`04-日志/bootloop_20261002_003821/logcat_all.txt`）

```
7530  I netd         : netd starting
7530  I NetdUpdatable: libnetd_updatable_init: Initializing
7531  E NetdUpdatable: libnetd_updatable_init: Failed: (2147483647) 25Q2+ platform with kernel version < 5.4.0 is unsupported
7532  F libc         : Fatal signal 6 (SIGABRT), code -1 (SI_QUEUE) in tid 3081 (netd), pid 3081 (netd)
592   F DEBUG        :       #01 pc 000000000000a668  /apex/com.android.tethering/lib64/libnetd_updatable.so (libnetd_updatable_init.cfi+600) (BuildId: e72cefc5824c6db0f92effe3338cb6ac)
593   F DEBUG        :       #02 pc 000000000004a94c  /system/bin/netd (main.cfi+228) (BuildId: 13e845e98949fd244d187f063c987562)
```

`(2147483647)` = `0x7FFFFFFF`，正是代码里那个「错误 Status」的 code。
`libnetd_updatable_init.cfi+600` → 函数入口 `0xa410` + `600`(0x258) ≈ `0xa668`，
与反汇编中该函数末尾的 `bl abort` 完全对上。

### 2.2 dmesg

```
init: [PHOENIX] phx_is_bootup_critical_service: bootup critical service netd crashed
```

Phoenix 把 netd 当关键服务 ⇒ 崩溃触发整机重启。

### 2.3 tombstone

`/data/tombstones/` 里堆了 30+ 个 netd tombstone，全是同一地址 `pc=0xa668`。

### 2.4 其它旁证

- `getprop sys.boot_completed` 为空、`bootanim` 仍在 running、`uptime up 0 min`
  ⇒ 不是「内核 panic 式硬重启」，而是「卡在等 system 就绪 + 反复重启关键服务」。
- `init second_stage` 自身也 `SIGABRT`（`tid 767 (init)`），
  栈是 `Service::RunService → LOG(FATAL)` —— 这是 init 在跑子进程时被同一批
  LOG(FATAL) 拖下水（child 还没 exec，cmdline 仍是 init）。

---

## 3. 反汇编定位（libnetd_updatable.so，57,664 字节）

### 3.1 符号（从 `.gnu_debugdata` 解出）

```
0x00a410 size=0x00025c  libnetd_updatable_init.cfi
0x0045f4 size=0x001294  _ZN7android3net10BpfHandler4initEPKc   (= BpfHandler::init(const char*))
0x005df8 size=0x00009c  _ZN7android3bpfL22isAtLeastKernelVersionEjjj
0x005888 size=0x0000dc  _ZN7android3bpfL21uncachedKernelVersionEv
```

`.gnu_debugdata` 是 LZMA 压缩的 mini-debug ELF，解出来即可拿到完整 `.symtab`。

### 3.2 门限算法

内核版本用 `KERNEL_VERSION` 风格编码：`major<<24 | minor<<16 | sub<<8`。

```
4920: mov  w20, #0x408ffff        ; w20 = 4.9.0 - 1（基址）

493c: add  w9, w20, #0x50, lsl #12   ; 0x040DFFFF = 4.14.0 - 1
4940: ldr  w8, [x8, #0x78]           ; w8 = 实际内核版本（全局 0xd078）
4944: cmp  w8, w9
4948: b.ls 0x5348                    ; 门限① → "U+ platform with kernel version < 4.14.0 is unsupported"

4b8c: add  w9, w20, #0xa0, lsl #12   ; 0x0412FFFF = 4.19.0 - 1
4b90: ldr  w8, [x8, #0x78]
4b94: cmp  w8, w9
4b98: b.ls 0x535c                    ; 门限② → "V+ platform with kernel version < 4.19.0 is unsupported"

4bbc: add  w9, w20, #0xfb0, lsl #12  ; 0x0503FFFF = 5.4.0 - 1
4bc0: cmp  w8, w9
4bc4: b.ls 0x5438                    ; 门限③ → "25Q2+ platform with kernel version < 5.4.0 is unsupported"
```

### 3.3 本机内核逐条比对

本机 4.19.157 → `0x04139D00`

| 门限 | 阈值 | 比较 | 结果 |
|---|---|---|---|
| ① 4.14.0 | `0x040DFFFF` | `0x04139D00 > 0x040DFFFF` | ✅ 不跳 |
| ② 4.19.0 | `0x0412FFFF` | `0x04139D00 > 0x0412FFFF` | ✅ 不跳 |
| ③ 5.4.0  | `0x0503FFFF` | `0x04139D00 ≤ 0x0503FFFF` | ❌ **命中 → SIGABRT** |

### 3.4 报错外壳

`libnetd_updatable_init` 本身不做判断，它只是：
1. 调 `BpfHandler::init(cg2_path)`（`0x45f4`）；
2. 若返回的 Status `code != 0` → `LOG(FATAL) "libnetd_updatable_init: Failed: (%d) %s"` → `abort()`。

```
a510: ldr  w8, [sp, #0x40]     ; Status.code
a514: cbnz w8, 0xa5c4          ; != 0 → 走 FATAL
...
a638: ldr  w1, [sp, #0x40]     ; code（= 0x7FFFFFFF）
a658: bl   operator<<(ostream&, const string&)   ; Status.msg
a668: bl   abort@plt
```

`BpfHandler::init` 命中门限时构造的正是 `{code = 0x7FFFFFFF, msg = "25Q2+ ... < 5.4.0 ..."}`：

```
5450: mov  w8, #0x7fffffff     ; = 2147483647
5458: str  w8, [x24]           ; Status.code
```

---

## 4. 修法

### 4.1 采用：NOP 掉三条门限跳转

文件偏移 == 虚拟地址（`.text` 从 `0x4000` 起，且 `addr == off`），只改 4 字节指令，
**不增删任何字节** ⇒ 文件大小 / 段表 / 重定位全部不变。

| 偏移 | 原指令 | 改为 |
|---|---|---|
| `0x4948` | `54005009` `b.ls 0x5348` | `d503201f` `nop` |
| `0x4b98` | `54003e29` `b.ls 0x535c` | `d503201f` `nop` |
| `0x4bc4` | `540043a9` `b.ls 0x5438` | `d503201f` `nop` |

- ①② 本来就不跳，改 NOP 行为等价（不再依赖版本探测结果，更稳）。
- ③ 改 NOP 后顺序执行到 `0x4bc8`（正常的 `open(cg2_path, O_DIRECTORY|O_CLOEXEC)` 流程）。

> ⚠ 这是「绕过」不是「修复」：25Q2 的 netd 在 4.19 内核上确实缺一些 BPF 能力，
> 后续 `initMaps` / `attachProgramToCgroup` 仍可能失败 —— 但那些失败会走
> **正常错误返回路径**（netd 打日志、返回错误码），**不会 abort 拖垮整机**。
> 本项目此前已对 `netbpfload` 做过同性质处理（`07-重建/新增文件/wb_netbpfload.rc`）。

### 4.2 已放弃的备选

- **改 `[0xd078]` 全局内核版本为 5.4.x**：能让所有版本判断「自然通过」，
  但下游会走「现代内核」分支，可能假设 5.4 才有的能力 ⇒ 风险不可控。
- **让 `setenforce 0` 生效**：与本题无关（SELinux 问题已由 boot cmdline 的
  `androidboot.selinux=permissive` 解决）。

---

## 5. 执行链路（本次新增/修改的工具）

```
03-脚本/patch_netd_gate.sh         ★ 新建：NOP 三条门限（带原字节自检 + 幂等 + 回读校验）
03-脚本/apex_rebuild.py            ★ 修改：新增 --hook（解包后/重建前改树）与 --name；
                                        prop「值」改为与原包逐项硬校验
03-脚本/build_system_diag_v3.sh    复用：把 07-重建/tree 打成 system 镜像
```

重建命令：

```bash
python3 apex_rebuild.py \
  07-重建/_apexorig/com.android.tethering.apex.orig \
  07-重建/新增文件/com.android.tethering.apex.new \
  --key 07-重建/_avb/apex_key.pem \
  --hook 03-脚本/patch_netd_gate.sh \
  --name com.android.tethering \
  --work 07-重建/_apexwork/tethering
```

重建后自检（全部通过）：

- `incompat=0x1`（4K 簇，本机内核可挂）
- `verify_image: ok`（footer + vbmeta 签名 + sha256 hashtree）
- 描述符 `HASHTREE`，prop `apex.key=com.android.tethering`（与原包一致）
- `apex_pubkey` 已换成自备密钥（1032 字节，与原包等长）
- 包内 `lib64/libnetd_updatable.so` 三处均为 `nop` ✓
- 包内 57/57 文件保留 `security.selinux` xattr（`.so` = `u:object_r:system_lib_file:s0`）

### ★ 本次踩到并修掉的一个真 bug

`apex_rebuild.py` 原本用**输入文件名**推包名：输入叫 `com.android.tethering.apex.orig`
时 `basename[:-5]` 走不到，结果包名被推成 `com.android.tethering.apex.orig`，
`apex.key` prop 就成了错的包名 —— **apexd 会直接拒包**。
现已改为：`--name` > 文件名里 `.apex` 之前的部分 > 整名，
并加硬校验「新 prop 的值必须与原包逐项一致」。

---

## 6. 与既有根因的关系

| # | 根因 | 状态 |
|---|---|---|
| 1 | APEX payload 用 big-pcluster（`incompat=0x3`），4.19 内核挂不上 | ✅ 已修（`apex_rebuild_all.py` 全量重建为 `0x1`） |
| 2 | `/system/bin/linker64` 是软链到 `/apex/...`，apexd 起不来时全盘 exec 失败 | ✅ 已修（15 个条目实体化） |
| 3 | SELinux 三重障碍阻断 `apexd-bootstrap`；`setenforce 0` 因 `buildvariant=user` 被编译期禁用 | ✅ 已修（boot cmdline 加 `androidboot.selinux=permissive`） |
| **4** | **`libnetd_updatable.so` 内核 5.4 硬门限 → netd SIGABRT → boot loop** | **本次修复，待刷机验证** |

---

## 7. 下一步

1. 刷入 `system_diag6.img`（v6 = 33 个 APEX 全量 v2 签名 + netd 三条门限 NOP）。
2. 观察是否越过 netd 这一步：`sys.boot_completed` 是否置位、开机动画是否停。
3. 若仍有其它服务 SIGABRT，按同样方法（logcat 找 `Fatal signal 6` → tombstone 定位
   `.so+偏移` → 反汇编找门限）逐个处理。
4. 最终目标：回到 **enforcing** 启动（方案 A：给 `apexd-bootstrap` service 加
   `seclabel u:r:shell:s0`；方案 B：追加 SELinux 策略规则）。
5. 次要：改机型显示（当前 `OnePlus8T`/`KB2000` 是供体值）。

## 8. 保底回滚

- 去掉 permissive：`03-脚本/restore_boot.sh`
- 刷回 ColorOS 15：`02-移植素材/platform-tools/FindX2Pro_ColorOS_15.0.2_Pro_*.zip`（7.6 GB）
- APEX 原包备份：`07-重建/_apexorig/com.android.tethering.apex.orig`
  （sha256 `2f022b68fabdadd183afd5e2709bb71f76c1648747d5ce60877398e1a973a03d`）
