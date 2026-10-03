# ColorOS 16 (Android 16) for OPPO Find X2 Pro

[![Android](https://img.shields.io/badge/Android-16%20%2F%20SDK%2036-3DDC84?logo=android&logoColor=white)](#)
[![ColorOS](https://img.shields.io/badge/ColorOS-16-1E7B3C)](#)
[![Device](https://img.shields.io/badge/Device-Find%20X2%20Pro%20(PDEM30)-blue)](#)
[![Kernel](https://img.shields.io/badge/Kernel-4.19.157-orange)](#)
[![Status](https://img.shields.io/badge/Status-Working%20Daily%20Driver-success)](#)

> **Unofficial port of ColorOS 16 (Android 16) to the OPPO Find X2 Pro.**
> A 2020 flagship that OPPO left behind on ColorOS 13 / Android 13 — now running Android 16.

**English** · [简体中文](README.zh-CN.md)

---

## ⚠️ DISCLAIMER — READ THIS FIRST

- **Flashing is entirely at your own risk.** A wrong partition can hard-brick a device with
  **no B slot** (this device is A-only). Recovery in that case requires EDL / fastboot.
- **Always back up your data.** Flashing wipes `/data`.
- **ColorOS is proprietary software owned by OPPO.** The system images provided in the
  [Releases](../../releases) section are **unofficial community builds** and are shared
  **strictly for personal, non-commercial research and testing**. Do not redistribute
  commercially. If you are a rights holder and want this removed, open an issue.
- **No warranty of any kind.** You may lose data, VoLTE, fingerprint, or the device itself.
- **Your bootloader must be unlocked.** Unlocking wipes all data.

---

## 1. What is this?

This is a **cross-flash port**: the system images are built from the community
**ColorOS 16 port for the OnePlus 8T (KB2000)**, which shares the exact same
SoC as the Find X2 Pro — **Qualcomm Snapdragon 865 (`kona` / SM8250)**.

Because both devices run the same platform, the system can boot on the Find X2 Pro
after a set of kernel-version gate patches (the donor targets a 4.19.325 kernel;
the Find X2 Pro ships **4.19.157**).

| | |
|---|---|
| **Target device** | OPPO Find X2 Pro — `PDEM30` / `OP4A7A` |
| **SoC** | Qualcomm Snapdragon 865 (`kona` / `SM8250`) |
| **Target kernel** | **4.19.157** |
| **Donor device** | OnePlus 8T — `KB2000` (`kona`) |
| **Donor kernel** | 4.19.325 |
| **Android version** | 16 (SDK 36) |
| **ColorOS version** | 16 (25Q2) |
| **Root** | Magisk 30.7 |
| **Partitions flashed** | `boot`, `system`, `system_ext` **only** |

> **Not flashed / left untouched:** `vendor`, `odm`, `my_stock`, `my_product`,
> `dtbo`, `vbmeta`, modem firmware. This keeps the device's original camera HAL,
> radio stack and hardware calibration intact.

---

## 2. Current status

Tested on a real PDEM30 running as a **daily driver**.

| Feature | Status | Notes |
|---|---|---|
| Boot | ✅ | `sys.boot_completed=1`, release `16`, sdk `36` |
| Wi-Fi | ✅ | |
| Mobile data (5G/LTE) | ✅ | |
| VoLTE | ✅ | |
| Bluetooth | ✅ | |
| Camera (photo) | ✅ | via **Open Camera** (see below) |
| Camera (video recording) | ✅ | **requires the Magisk module in this repo** |
| Sensors (accel/gyro/proximity) | ✅ | |
| Fingerprint | ❌ | `gaia init_fault=18` |
| SELinux | ⚠️ | currently **Permissive** (required for the port) |
| Factory camera app | ❌ | see [Known issues](#6-known-issues) |
| Model name | ⚠️ | still reports `KB2000` / `OnePlus8T` (from donor `/my_product`) |

---

## 3. Requirements

**On the phone**

- OPPO Find X2 Pro (`PDEM30`), **bootloader unlocked**
- A custom recovery (TWRP recommended) or fastboot access
- Battery ≥ 50 %

**On the computer**

- `adb` and `fastboot` (Android platform-tools)
- **macOS (Apple Silicon or Intel)**, **Linux**, or **Windows**
- ~4 GB free disk space

> The included `scripts/flash-m1.sh` is optimised for **Apple Silicon macOS**
> (auto-detects `/opt/homebrew/bin`, avoids the `fastboot getvar` hang that
> affects macOS). For Linux/Windows, use the manual fastboot commands in
> [section 5.3](#53-manual-flashing-linux--windows).

---

## 4. Download

Everything you need is in the **[Releases](../../releases)** page.

| File | Size | Purpose |
|---|---|---|
| `boot_permissive.img` | 96 MiB | Boot image (SELinux permissive) |
| `system_diag6.img` | 1.00 GiB | System partition |
| `system_ext_fix2.img` | 934 MiB | System_ext partition |
| `SHA256SUMS.txt` | — | Checksums — **verify before flashing** |
| `aac-c2-preference-fix-v1.zip` | 2.5 KiB | Magisk module — **video recording fix** |

Verify the download:

```bash
shasum -a 256 -c SHA256SUMS.txt        # macOS / Linux
certutil -hashfile boot_permissive.img SHA256   # Windows
```

Expected checksums:

```
1a02f68ff04d581fa1e353a43a0e500a29ba43b4c7a0058c7229bd0e2ba27d11  boot_permissive.img
e3534c0d8dd269fb4226d90f2beb54eadddd9cc13ee00b95cb6e44b6952a6c76  system_diag6.img
0c990cc55410c1fb67695695aca8b023f6ea88e4b693ad3668bdc36d88459f1a  system_ext_fix2.img
```

---

## 5. Installation

### 5.1 Before you start

1. **Back up everything.** This wipes `/data`.
2. Make sure the bootloader is unlocked (`fastboot flashing unlock`).
3. Confirm the device is detected: `adb devices` or `fastboot devices`.

> ⚠️ **This device is A-only (no A/B slots).** If you flash a broken `boot`,
> you must recover via fastboot or EDL. Do not skip the checksum verification.

### 5.2 One-click flashing (macOS, recommended)

```bash
# 1. Put the three .img files next to the script (or edit IMG path inside it)
# 2. Dry run — checks the environment and verifies sha256, touches nothing
./scripts/flash-m1.sh --check

# 3. Flash
./scripts/flash-m1.sh
```

The script will:

1. Locate `adb` / `fastboot` automatically
2. Verify all three images against `SHA256SUMS.txt`
3. Reboot to bootloader → flash `boot`
4. Reboot to **fastbootd** → flash `system` and `system_ext`
5. Reboot to system

**Other options**

| Flag | Action |
|---|---|
| `--check` | Environment + checksum verification only |
| `--status` | Print the device's current mode |
| `--reboot` | Reboot to system |
| `--help` | Show help |

### 5.3 Manual flashing (Linux / Windows)

```bash
# --- bootloader ---
fastboot flash boot        boot_permissive.img

# --- switch to fastbootd (system/system_ext are logical partitions in `super`) ---
fastboot reboot fastboot

# --- system partitions ---
fastboot flash system      system_diag6.img
fastboot flash system_ext  system_ext_fix2.img

# --- reboot ---
fastboot reboot
```

> ⚠️ `system` and `system_ext` live inside the `super` dynamic partition and
> **can only be flashed in `fastbootd` mode**, not in the bootloader.
> If `fastboot flash system` fails, run `fastboot reboot fastboot` and retry.

### 5.4 First boot

- Expect ~50 s of the boot animation, then `sys.boot_completed=1` at ~55 s.
- The first boot may take longer (up to 5 minutes). Be patient.

Verify:

```bash
adb shell getprop sys.boot_completed          # expect 1
adb shell getprop ro.build.version.release    # expect 16
adb shell getprop ro.build.version.sdk        # expect 36
```

### 5.5 ★ Post-install: the video-recording fix (DO NOT SKIP)

**Without this module, video recording fails with "Unable to save video".**

Install the Magisk module `aac-c2-preference-fix-v1.zip`:

1. Open the **Magisk** app → **Modules** → **Install from storage**
2. Select `aac-c2-preference-fix-v1.zip`
3. **Reboot**

Verify:

```bash
adb shell getprop vendor.audio.c2.preferred   # expect true
```

**What it does:** it sets a single system property, `vendor.audio.c2.preferred=true`,
which makes the Qualcomm AV-enhancement library skip the creation of a custom OMX
AAC encoder (`OMX.qcom.audio.encoder.aac`) that the Find X2 Pro's stock vendor
partition never declares. Without it, `MediaCodec::CreateByComponentName` returns
`nullptr` and the recorder reports `Failed to create audio encoder`.

It **does not modify any partition file** — it only sets a property.

**Temporary workaround** (no module, until next reboot):

```bash
adb shell su -c 'setprop vendor.audio.c2.preferred true'
```

Full analysis: [`docs/root-cause/根因-13-…md`](docs/root-cause/)

---

## 6. Known issues

| Issue | Detail | Workaround |
|---|---|---|
| **Stock OnePlus camera unusable** | The donor's `/my_product` forces the `Camera 6` SAT multi-camera usecase; the Find X2 Pro's camera topology doesn't match, so the HAL returns `-38 ENOSYS`. The hardware and HAL are **completely fine**. | Use **Open Camera** (opens `Camera 0`, works perfectly) |
| Fingerprint | `gaia init_fault=18` | none yet |
| Model name shows `KB2000` | Comes from the donor's `/my_product/build.prop` | cosmetic |
| SELinux is `Permissive` | Required for this port | — |
| `reservedsize=128M` not applied | f2fs occupies the whole partition | cosmetic |
| `/odm/bin/hw/subsys_daemon` crash | in `libradioapis.so::QmiVsClient::getNecData` | no functional impact |

> **Camera note:** the stock OnePlus camera app has been removed. Install
> [Open Camera](https://f-droid.org/packages/net.sourceforge.opencamera/) from F-Droid.
> Photo, video (with the Magisk fix) and preview all work.

---

## 7. ⚠️ Factory reset — READ THIS OR YOU WILL BOOTLOOP

**Never use TWRP's plain "Wipe / Factory Reset" on this port.**

TWRP's wipe does **not** erase the FBE key directory (`/data/unencrypted`).
The old key then mismatches the new `/data`, and the device bootloops back
into recovery (`enablefilecrypto_failed` → `set_policy_failed:/data/cache`).

**Option A — TWRP "Format Data" (recommended)**

1. Boot into TWRP
2. **Wipe** → **Format Data** → type `yes`
3. **Reboot → Recovery** (important — lets TWRP re-detect the new f2fs layout)
4. **Reboot → System**

**Option B — ADB (10 seconds)**

```bash
# 1. Reset FBE keys and the misc boot flag
adb shell 'mount -t f2fs /dev/block/sda11 /data 2>/dev/null; rm -rf /data/unencrypted /data/gsi; dd if=/dev/zero of=/dev/block/by-name/misc bs=4096 count=1; sync'

# 2. Wipe all old data policy (keep lost+found)
adb shell 'mount -t f2fs /dev/block/sda11 /data 2>/dev/null; cd /data && for d in *; do [ "$d" = "lost+found" ] || rm -rf "$d"; done; sync'

# 3. Reboot
adb reboot
```

---

## 8. Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| Bootloops into TWRP after factory reset | See [section 7](#7--factory-reset--read-this-or-you-will-bootloop) |
| Bootloop, stuck on first screen | `apexd-bootstrap` blocked by SELinux — the boot image must be the permissive one from this repo |
| No Wi-Fi / no mobile data | `netd` / `netbpfload` kernel gate — you flashed a stock `system.img` instead of `system_diag6.img` |
| "Unable to save video" | Install the Magisk module — see [section 5.5](#55--post-install-the-video-recording-fix-do-not-skip) |
| Camera app black screen | Use Open Camera — see [Known issues](#6-known-issues) |
| `system` flash fails in fastboot | You're in the bootloader, not fastbootd — run `fastboot reboot fastboot` |
| Device not detected | Try another USB cable/port; on macOS use `--check` to diagnose |

More detail (Chinese): [`docs/troubleshooting.zh-CN.md`](docs/troubleshooting.zh-CN.md)

**Useful debug commands**

```bash
# Make MediaCodec log component names (video/audio debugging)
adb shell su -c 'setprop debug.oplus.video.log.enable 5'
adb shell su -c 'setprop ctl.restart media'

# List available codecs
adb shell dumpsys media.player | grep -A2 -i encoder

# init builtin failures only appear in dmesg, not logcat
adb shell dmesg | grep -i 'init: Command'
```

---

## 9. Documentation

| Document | Content |
|---|---|
| [`docs/flashing-guide.zh-CN.md`](docs/flashing-guide.zh-CN.md) | Full flashing guide + precautions |
| [`docs/partition-layout.zh-CN.md`](docs/partition-layout.zh-CN.md) | Partition table & unbrick guide |
| [`docs/troubleshooting.zh-CN.md`](docs/troubleshooting.zh-CN.md) | Complete troubleshooting manual |
| [`docs/project-history.zh-CN.md`](docs/project-history.zh-CN.md) | Project history (earlier LineageOS GSI route) |
| [`docs/root-cause/`](docs/root-cause/) | Root-cause analysis of all 13 bugs fixed |

> Most docs are in Chinese. Pull requests with English translations are very welcome.

---

## 10. What was fixed (the interesting part)

This port required patching **13 distinct bugs**. Highlights:

| # | Symptom | Root cause |
|---|---|---|
| 1–3 | Bootloop, no network | `libnetd_updatable.so` / `netbpfload` require kernel ≥ 5.4 / ≥ 4.19.236; the device runs **4.19.157** → binary NOP patches |
| 4 | init refuses `exec_start` | `mkfs.erofs --replace` dropped SELinux xattrs |
| 5–6 | APEX signature missing | repacking APEX lost the APK Signature Scheme v2 block → re-signed 33 + 1 APEX |
| 7 | Bootloop at Phase 480 | `/data/system/locksettings.db` owned by `root:root 0600` → `chown system:system` |
| 9 | Camera black screen | donor `/my_product` requests an incompatible SAT camera → switched to Open Camera |
| 11 | Bootloop back to TWRP | TWRP wipe doesn't reset FBE keys |
| **13** | **"Unable to save video"** | **`vendor.audio.c2.preferred` unset → OMX AAC encoder requested but never declared** |

The full write-ups (with disassembly evidence, logcat traces, and byte offsets)
are in [`docs/root-cause/`](docs/root-cause/).

---

## 11. Building from source

The `scripts/build/` directory contains every script used to produce the images:

- `build_system_diag_v3.sh` — main system image build (erofs + xattr + APEX signing)
- `apex_v2_sign.py` — re-sign APEX with APK Signature Scheme v2
- `patch_netbpfload.py` / `patch_libnetd_updatable.py` — kernel version gate patches
- `patch_boot_cmdline.py` — inject `androidboot.selinux=permissive`
- `erofs_read.py` / `erofs_rebuild.py` — erofs tooling

**Toolchain used**

- Python 3.13 with `capstone` 5 + `pyelftools`
- `erofs-utils` 1.9.4 (`mkfs.erofs`, `fsck.erofs`, `dump.erofs`)
- `apksig` for APEX v2 signing

You will also need the donor base images (ColorOS 16 port for OnePlus 8T) —
see [`docs/root-cause/交叉参考-大侠阿木8T移植项目.md`](docs/root-cause/).

---

## 12. Credits

- **[daxiaamu](https://github.com/daxiaamu)** — the original ColorOS 16/17 port for the OnePlus 8T,
  which is the base of this work. Without it none of this would exist.
- **Color597** — TWRP recovery for the Find X2 Pro
- **Magisk** by topjohnwu
- The OPPO / OnePlus modding community

---

## 13. License

- **Scripts & documentation** in this repository: **MIT** — see [`LICENSE`](LICENSE).
- **System images** (`boot_permissive.img`, `system_diag6.img`, `system_ext_fix2.img`):
  **not covered by the MIT license.** They contain proprietary OPPO/Qualcomm software
  and are provided **for personal, non-commercial use only**. All rights belong to
  their respective owners.

---

## 14. Contributing

Issues and pull requests are welcome — especially:

- English translations of the Chinese docs
- Fingerprint fix
- Restoring the stock camera

Please include your **device model, build number, and full logs** in bug reports.
