# Changelog

## [v1.0] — 2026-10-02

First public release. ColorOS 16 (Android 16 / SDK 36 / 25Q2) running on the
OPPO Find X2 Pro (PDEM30).

### Images
- `boot_permissive.img` — boot image with `androidboot.selinux=permissive`
- `system_diag6.img` — system partition
- `system_ext_fix2.img` — system_ext partition

### Fixed
- **Boot** — SELinux was blocking `apexd-bootstrap` (user build disables
  `setenforce` at compile time). Fixed via boot cmdline.
- **netd** — `libnetd_updatable.so` required kernel ≥ 5.4 (device has 4.19.157).
  Patched three version gates (`0x4948`, `0x4b98`, `0x4bc4`).
- **netbpfload** — required 4.19 kernel ≥ 4.19.236 (device has 4.19.157).
  Patched three gates (`0xbdfc`, `0xbe1c`, `0xbe5c`).
- **SELinux labels** — `mkfs.erofs --replace` dropped xattrs. Build now uses
  `--replace "/target=content#template"` to preserve labels.
- **APEX signature** — repacking lost the APK Signature Scheme v2 block.
  Re-signed 33 APEX in `/system/apex` plus `com.android.compos.apex`.
- **Bootloop at Phase 480** — `/data/system/locksettings.db` was owned by
  `root:root 0600`; `system_server` (uid 1000) could not read it. Fixed with
  `chown system:system`.
- **Camera** — the donor's `/my_product` forces an incompatible SAT
  multi-camera usecase (HAL returns `-38 ENOSYS`). Switched to Open Camera,
  which uses the main camera (`Camera 0`) correctly.
- **Bootloop back to TWRP after factory reset** — TWRP's wipe does not reset
  FBE keys. Documented the correct procedure.
- **★ Video recording "Unable to save video"** — `libavenhancements.so` read
  `vendor.audio.c2.preferred`; when unset it pushed `OMX.qcom.audio.encoder.aac`
  into the candidate list, but that component is never declared by the stock
  vendor partition, so `MediaCodec::CreateByComponentName` returned `nullptr`.
  Fixed with the Magisk module `aac-c2-preference-fix-v1.zip`, which only sets
  `vendor.audio.c2.preferred=true`.

### Known issues at release
- Fingerprint not working (`gaia init_fault=18`)
- SELinux is Permissive
- Model name still reports `KB2000` / `OnePlus8T`
- `reservedsize=128M` not applied
- `/odm/bin/hw/subsys_daemon` crashes in `libradioapis.so`
