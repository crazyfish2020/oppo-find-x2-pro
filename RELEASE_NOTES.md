# Release v1.0 — ColorOS 16 (Android 16) for OPPO Find X2 Pro

Unofficial port of **ColorOS 16 / Android 16 (SDK 36 / 25Q2)** to the
**OPPO Find X2 Pro (PDEM30)**, built on the community ColorOS 16 port for the
**OnePlus 8T (KB2000)** — both are Qualcomm Snapdragon 865 (`kona` / SM8250).

**Status: working daily driver.** Boot, Wi-Fi, 5G/LTE, VoLTE, Bluetooth,
sensors, camera (photo + video) all verified on real hardware.

---

## ⚠️ Read before flashing

- **Your bootloader must be unlocked.** Flashing wipes `/data`.
- This device is **A-only** (no A/B slots). A bad `boot` needs fastboot/EDL to recover.
- **Verify checksums** before flashing.
- System images contain proprietary OPPO/Qualcomm software and are shared
  **for personal, non-commercial use only**.

---

## 📦 Downloads

| File | Size | Required |
|---|---|---|
| `boot_permissive.img` | 96 MiB | ✅ |
| `system_diag6.img` | 1.00 GiB | ✅ |
| `system_ext_fix2.img` | 934 MiB | ✅ |
| `SHA256SUMS.txt` | 255 B | ✅ (verify!) |
| `aac-c2-preference-fix-v1.zip` | 2.5 KiB | ✅ **Magisk module — install after flashing** |

### Checksums

```
1a02f68ff04d581fa1e353a43a0e500a29ba43b4c7a0058c7229bd0e2ba27d11  boot_permissive.img
e3534c0d8dd269fb4226d90f2beb54eadddd9cc13ee00b95cb6e44b6952a6c76  system_diag6.img
0c990cc55410c1fb67695695aca8b023f6ea88e4b693ad3668bdc36d88459f1a  system_ext_fix2.img
```

```bash
shasum -a 256 -c SHA256SUMS.txt
```

---

## 🚀 Quick start

```bash
# 1. Verify
shasum -a 256 -c SHA256SUMS.txt

# 2. Dry run
./scripts/flash-m1.sh --check

# 3. Flash
./scripts/flash-m1.sh
```

Manual flashing (Linux / Windows):

```bash
fastboot flash boot        boot_permissive.img
fastboot reboot fastboot
fastboot flash system      system_diag6.img
fastboot flash system_ext  system_ext_fix2.img
fastboot reboot
```

**Then install the Magisk module** `aac-c2-preference-fix-v1.zip`
(Magisk → Modules → Install from storage → reboot).
Without it, **video recording fails with "Unable to save video"**.

Full guide: [README.md](../blob/main/README.md)

---

## ✅ What works

Boot · Wi-Fi · 5G/LTE · VoLTE · Bluetooth · Sensors · Camera photo · Camera video
(with the Magisk module) · **Fingerprint** · Magisk root

## ❌ Known issues

- **SELinux is Permissive** — required for the port
- **Stock OnePlus camera unusable** — use
  [Open Camera](https://f-droid.org/packages/net.sourceforge.opencamera/)
  (opens `Camera 0`; the hardware and HAL are fine)
- Model name still reports `KB2000` / `OnePlus8T` (from donor `/my_product`)

---

## 🔧 Notable fixes in this release

- **Boot** — SELinux was blocking `apexd-bootstrap`
- **Network** — `libnetd_updatable.so` / `netbpfload` required kernel ≥ 5.4 /
  ≥ 4.19.236; the device runs **4.19.157** → binary gate patches
- **APEX signing** — repacking dropped the APK Signature Scheme v2 block;
  33 + 1 APEX re-signed
- **Bootloop at Phase 480** — `locksettings.db` owned by `root:root 0600`
- **Bootloop back to TWRP after factory reset** — TWRP wipe doesn't reset FBE keys
- **★ "Unable to save video"** — `vendor.audio.c2.preferred` unset caused the
  system to request `OMX.qcom.audio.encoder.aac`, a component the stock vendor
  partition never declares

Root-cause analysis (with disassembly evidence and logcat traces) for all 13 bugs:
[`docs/root-cause/`](../tree/main/docs/root-cause)

---

## 🙏 Credits

- **[daxiaamu](https://github.com/daxiaamu)** — original ColorOS 16/17 port for the OnePlus 8T
- **Color597** — TWRP recovery for the Find X2 Pro
- **Magisk** by topjohnwu

---

**Flashing is at your own risk. No warranty of any kind.**
