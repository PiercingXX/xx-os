# Pixel 9 (`tokay`) bring-up plan for M3

Written 2026-11-12. Phone: Google Pixel 9, `tokay`, Tensor G4 (`zumapro`), board
family `caimito` (shared with the 9 Pro `caiman` and 9 Pro XL `komodo`). Milestone:
M3 in `docs/pixel-9-pro-design.md` section 9 ("Gate: Linux graphics on the Pixel").

**Status of this document.** Desk work only. No phone was connected or touched while
writing it. The scripts in `scripts/pixel/` were run on the desk (initramfs from real
Debian packages, boot images from a dummy kernel), never against a device. Claims are
marked as in the design doc: *sourced* (a primary page, source file or repository was
read), *reported* (someone else's notes or a search summary, not checked), *assumed*
(background knowledge or inference) or *decision* (proposed here; section 9 lists
what needs the owner's sign-off).

Goal: when the 1.8 V serial adapter arrives, the first boot is a checklist. In fact
most of the checklist does not need the adapter (section 7.1).

---

## 0. What changed since the design doc

The design doc says no Linux port of any Pixel 9 was found (section 1) and plans on
Google's 6.1 kernel with the Android bridge for the modem (sections 6, 7). Four
findings from this research change the picture:

1. **Mainline Linux already runs on Tensor G4.** postmarketOS (now Nura) lists the
   Pixel 9 `google-tokay` as booting with a 7.1-rc2 kernel: "Kernel compiles, boots,
   and simpledrm works", USB networking yes, touch and screen partial, edits from
   June 2026 (*sourced*, wiki page source and history). The kernel is a community tree,
   `Trijal08/kernel-mainline` branch `zumapro-google-caimito`, which carries
   `zumapro-tokay.dts`, a DSI display path through `drm/exynos`, a Panthor GPU node,
   touch over the AoC bus negotiator, the Exynos modem as a WWAN driver and AoC audio,
   with commits up to 2026-11-12 (*sourced*, tree read through GitHub). A branch of it
   by Marcus Ramberg (`marcus/caiman-dp`) enables DisplayPort out on the 9 Pro (*sourced*
   commit subjects; not run by anyone we can cite).
2. **The Pixel 9a, same SoC, is broadly working on mainline.** Omarchy Mobile's device
   table lists the 9a (`tegu`, zumapro) with display at 120 Hz, Panthor GPU rendering
   the desktop, Synaptics touch, USB NCM, Wi-Fi, Bluetooth, battery and charging,
   audio and sensors through the AoC, fingerprint, and calls and texts "testing"
   (*sourced*, `docs/DEVICES.md` and `device/tegu/NOTES.md`, 2026-10/11).
3. **Calls on Exynos modems work without Android.** A Pixel 6 port reports VoLTE calls,
   SMS and data on Verizon (via US Mobile) with a reverse-engineered modem stack
   (*sourced*, `TheNoswad/pixel6-mainline-nura` README); Omarchy Mobile reports calls,
   texts and data on a Verizon-network SIM on the Pixel 7 Pro, 2026-11-03 (*sourced*,
   device table). Not the 9's Exynos 5400 and not proven by us, but it moves the
   design's top risk.
4. **Google's own kernel source for this phone has stopped moving.** The AOSP manifest
   branch `android-gs-caimito-6.1-android16` was last changed 2025-06-10 and there is
   no later caimito branch (*sourced*, `git ls-remote` and the manifest). GrapheneOS
   keeps a combined Pixel 6.1 tree current through Android 17, last commit 2026-11-11
   (*sourced*).

**Proposed amendment (decision, needs sign-off):** run M3 on two kernels with one
userland and one set of boot tools. Boot first with the community mainline kernel
(others have USB and a picture on this exact phone), then with the Google 6.1 kernel
(GrapheneOS's tree) as the comparison and fallback. Whichever passes the M3 exit test
first carries M4. If mainline holds up, it also removes design risk 3 (the open GPU
driver on 6.1), because Panthor already runs on G4 there.

## 1. The device, as the source describes it

| Item | Value | Mark |
|---|---|---|
| Board id / rev (retail) | `0x60a06` / `0x010000` (`zumapro-tokay-mp.dts`) | sourced |
| Panel | 1080x2424; panel drivers `panel-gs-tk4a/b/c`, chosen by the bootloader through `exynos_drm.panel_name` | sourced (DT, modules list, driver) |
| Display engine | Samsung DECON/DPP/DSIM, out-of-tree `exynos-drm.ko`; a full atomic KMS driver with dumb buffers, PRIME and XRGB8888 | sourced (`exynos_drm_drv.c`) |
| DisplayPort | `drmdp@110F0000`, enabled for caimito; driver built with `CONFIG_DRM_SAMSUNG_DP=y` | sourced |
| Touch | Synaptics TCM over SPI (`syna_touch`), Google touch interface, touch bus negotiator in AoC-channel mode | sourced (DT, BUILD.bazel, defconfig) |
| USB | DWC3 at `0x11210000`, MAX77759-family TCPC with TCPM | sourced |
| Serial | `ttySAC0`, earlycon at `0x10870000`, enabled by `fastboot oem uart enable` | sourced (DT, Linaro scripts) |
| pstore | ramoops at `0xfd3ff000`, 4 MiB | sourced (`zumapro-rmem.dtsi`) |
| Splash | the bootloader leaves a command-mode DSI link streaming from `0xfac00000`, a8r8g8b8, 1080x2424 stride 4320 (commented-out simple-framebuffer in the mainline tokay DT) | sourced (mainline DT and its comments) |
| Boot partitions per slot | `boot`, `init_boot`, `vendor_boot`, `vendor_kernel_boot`, `dtbo` (+ `vbmeta*`, `pvmfw`, bootloader parts, `modem`) | sourced (BoardConfig; 9a getvar in Omarchy notes) |
| Sizes | boot, vendor_boot, vendor_kernel_boot 64 MiB; init_boot 8 MiB; dtbo 16 MiB | sourced (BoardConfig) |
| Per-phone partitions | `efs`, `efs_backup`, `modem_userdata`, `persist`, `devinfo`, `metadata` (+ `modem_a/b`) | sourced for the 9a, assumed the same on the 9 |

## 2. Kernel

### 2.1 Downstream: Google's 6.1 kernel

*Sources.* Two ways to get the same code (*sourced*):

| | Google AOSP manifest | GrapheneOS tree |
|---|---|---|
| Where | `repo init -u https://android.googlesource.com/kernel/manifest -b android-gs-caimito-6.1-android16` | `git clone https://gitlab.com/grapheneos/kernel_pixel_6.1.git -b 17 --recurse-submodules` |
| Last change | 2025-06-10 (Android 16 release) | 2026-11-11 (Android 17 QPRs, GKI updates) |
| Build | `./build_caimito.sh` | `KLEAF_REPO_MANIFEST=aosp_manifest.xml ./build_caimito.sh` |
| Pinning | manifest names branches; record with `repo manifest -r` | one superproject commit pins everything |

**Decision:** use the GrapheneOS tree by default. It is current, one commit pins it,
and the phone's other slot runs GrapheneOS, so its firmware matches. The AOSP manifest
stays as a cross-check. Pins are in `scripts/pixel/config.sh`.

*What the build is* (*sourced*, `private/devices/google/caimito/BUILD.bazel`, Google's
"Build Pixel kernels" page): Kleaf (Bazel) with hermetic prebuilt clang, Bazel and
JDK from the tree. `build_caimito.sh` runs
`tools/bazel run --config=stamp --config=caimito //private/devices/google/caimito:zumapro_caimito_dist`
and writes `out/caimito/dist`: the GKI `Image`, the Pixel modules, `dtb.img`,
`dtbo.img`, `vendor_kernel_boot.img` (DTB + first-stage modules), `vendor_dlkm.img`,
`system_dlkm.img`. The core kernel is `//common:kernel_aarch64`, by default **downloaded
and signed by Google** (`--use_prebuilt_gki=true` in the device bazelrc); the device
part is a "mixed build" against it. Host: x86-64 Linux; GrapheneOS asks 32 GiB RAM
for its builds (*sourced*); the desk has 61 GiB and 32 cores.

*Why the GKI must be rebuilt.* The options a Debian userland needs live in the core
kernel, not in modules, and GKI leaves them out (*sourced*, `gki_defconfig` on the
caimito branch): `CONFIG_FHANDLE` is explicitly not set, `CONFIG_DEVTMPFS` and
`CONFIG_VT` are absent, `CONFIG_STATIC_USERMODEHELPER=y` disables usermode helpers.
systemd lists `DEVTMPFS` and `FHANDLE` as required (*sourced*, systemd README). So:
`--kernel_package=@//aosp --use_prebuilt_gki=false`, plus our fragment through Kleaf's
`--defconfig_fragment` flag, which this branch's Kleaf documents (*sourced*,
`kleaf/docs/kernel_config.md`). Building everything from one tree also means no
module has to match Google's signed KMI. The prebuilt `fips140.ko` is signed for
Google's GKI and is switched off (*assumed* that nothing on our path needs it).

`scripts/pixel/build-kernel.sh grapheneos` does this and then reads the config back
out of the built `Image` (`extract-ikconfig`) and fails if any option of the fragment
is missing. Whether `--defconfig_fragment` reaches the GKI target as well as the
device target is *assumed*; that check is how we find out on the first build.

*The fragment* (`scripts/pixel/xxos-debian.config`): `DEVTMPFS`, `DEVTMPFS_MOUNT`,
`FHANDLE`, `AUTOFS_FS`, `TMPFS_POSIX_ACL/XATTR`, `SECCOMP(_FILTER)`, `USER_NS`,
`STATIC_USERMODEHELPER` off, `DRM_SIMPLEDRM`, `DRM_FBDEV_EMULATION`, `VT`,
`VT_CONSOLE`, `FRAMEBUFFER_CONSOLE`, `IKCONFIG_PROC`. Already in GKI and needed
(*sourced*): `CGROUPS`, `MEMCG`, `CGROUP_BPF`, `BPF_SYSCALL`, `NAMESPACES`, `EXT4`,
`F2FS`, `EROFS`, `BLK_DEV_LOOP`, `DM_CRYPT`, `OVERLAY_FS`, `FUSE_FS`, `INPUT_EVDEV`,
`INPUT_UINPUT`, `PSTORE_RAM`, `BOOT_CONFIG`, and the USB gadget functions
`USB_CONFIGFS_ACM`, `_NCM`, `_ECM`, `_SERIAL`, `_MASS_STORAGE`, `_F_FS`. Command line:
drop the vendor's `cgroup_disable=memory` (stock vendor_boot adds it, *sourced*
BoardConfig) so systemd gets the memory controller. SELinux stays built in; with no
policy loaded it denies nothing (*assumed*).

*Out-of-tree modules M3 needs* (all source in the manifest, *sourced* from
BUILD.bazel and the module lists):

| Need | Modules | Stage |
|---|---|---|
| SoC base (clocks, pinctrl, PMIC, IOMMU, power domains, ACPM, UFS) | `google-modules/soc/gs`: `clk_exynos_gs`, `pinctrl-exynos-gs`, `s2mpg14/15-*`, `samsung_iommu_v9`, `exynos-pd`, `gs_acpm`, `ufs-exynos-gs`, ... (~200) | first (vendor_kernel_boot); Google notes SoC modules must load first or the device crashes |
| Display | `exynos-drm`, `gs-drm-connector`, `gs-panel`, `panel-gs-tk4a/b/c`, `panel-samsung-*`, `drm_display_helper` | first |
| USB and Type-C | `dwc3-exynos-usb`, `xhci-exynos`, `phy-exynos-usbdrd-eusb-super`, `eusb_repeater`, `tcpci_max77759`, `google_tcpci_shim`, `usb_psy`, `bc_max77759` | first |
| AoC (touch arbitration, later audio and sensors) | `aoc_core`, `aoc_tbn_service_dev`, `aoc_usb_driver` | first |
| Touch | `touch_bus_negotiator`, `touch_offload`, `goog_touch_interface` (touch.common), `syna_touch` | negotiator first; the rest second stage (`vendor_dlkm`) |
| GPU (not needed for M3) | `mali_kbase`, `mali_pixel` (Arm's proprietary userspace only) | first |

Order matters: Android loads them from `modules.load` in list order (*sourced* Google
comment in BUILD.bazel). The bring-up init does the same, passing each module its
`module.param=value` tokens from the command line, because `insmod` does not read it
(`scripts/pixel/initramfs/init`).

*Firmware.* The Pixel kernel loads firmware from `/vendor/firmware`
(`firmware_class.path`, *sourced* stock bootargs): AoC firmware, touch firmware
(`synaptics.img`, used if the controller needs a reflash, *sourced* driver), Wi-Fi.
Google's licence forbids redistribution (design doc 7). For M3: copy the few files
needed from the owner's own factory image into the local initramfs work area
(`FIRMWARE_DIR`/`FIRMWARE_FILES` in `make-initramfs.sh`), never into git. Later, map
the phone's own `vendor` out of `super` read-only (`parse-android-dynparts` style
dm-linear tables, as postmarketOS initramfs does, *assumed*).

### 2.2 Mainline: the community Tensor G4 tree

- Tree: `https://github.com/Trijal08/kernel-mainline`, branch `zumapro-google-caimito`,
  pinned at `5d095886` (2026-11-12). It has `zumapro-tokay.dts`, `zumapro_defconfig`
  with `DRM_EXYNOS`, `DRM_PANTHOR`, `DRM_SIMPLEDRM`, DWC3, TCPM with the Maxim TCPC
  and the configfs gadget functions (*sourced*). Its chosen node notes "Bootloader
  expects bootargs specified otherwise it crashes" (*sourced*).
- Fork used by the postmarketOS page: `orgwithsomething/linux-zumapro` branch `tokay`
  (*sourced*). Reference for the 9a: `derekross/linux-omarchy-mobile` (mainline 7.3
  based, `zumapro-tegu.dts`, no tokay DT) (*sourced*).
- Build: plain `make ARCH=arm64 LLVM=1 zumapro_defconfig` + our fragment through
  `merge_config.sh`, `Image.gz dtbs modules` (`build-kernel.sh mainline`). No
  out-of-tree modules; the AoC, touch, modem and audio drivers are in the tree.
- Not from us: this tree is one or two people's work, largely generated with coding
  tools, and not upstream (*reported*; the Pixel 6 port says the same of its own code
  and that this is why none of it goes upstream, *sourced*). Track it, don't fork it,
  until M3 says which kernel we stand on.

## 3. Boot

### 3.1 Layout and how the bootloader assembles a boot (header v4)

| Partition | Holds | Ours, mainline | Ours, downstream |
|---|---|---|---|
| `boot_X` | kernel only (no ramdisk on init_boot devices), header v4, OS version and patch level | `Image.gz` | GKI `Image.lz4` |
| `init_boot_X` | the generic ramdisk, loaded last | erased (stand-in image as fallback) | erased (same) |
| `vendor_boot_X` | vendor ramdisk fragments, vendor cmdline, bootconfig, optional DTB | our initramfs, cmdline, **the DTB** | our initramfs, cmdline |
| `vendor_kernel_boot_X` | DTB + first-stage module ramdisk (Pixel 7 and later) | erased | Kleaf's, with our modules |
| `dtbo_X` | overlays chosen by board id/rev | tiny overlay carrying `0x60a06`/`0x10000` | Kleaf's |

*Sourced:* header versions 4 for boot and init_boot, `BOARD_RAMDISK_USE_LZ4`, A/B
lists and sizes (zumapro BoardConfig); "the generic ramdisk must come last", vendor
ramdisk order is the bootloader's choice, both lz4 (AOSP vendor_boot page); the
mainline Pixel approach of putting the DTB in vendor_boot and erasing
`vendor_kernel_boot`, `init_boot` and `dtbo` (Linaro `pixelscripts` Makefile, which
also has Pixel 10 targets); the 9a's five-image slot-B layout with a minimal board dtbo,
2048-byte vendor page size, and the base DTB chosen by `/soc_compatible` (Omarchy
`device/tegu`). *Assumed:* that the 9's bootloader accepts an erased `init_boot` next
to a populated `vendor_kernel_boot` (the downstream combination). If it refuses, flash
the stand-in `init_boot.img` that `make-boot-images.sh` also builds.

Our initramfs is one vendor ramdisk fragment. Nothing else in the assembled ramdisk
has an `/init`, so ours runs; the downstream module ramdisk only adds `/lib/modules`.

### 3.2 AVB footers

Unlocked, the bootloader boots images that fail verification, but a Pixel 9a went
straight back to fastboot (`bootfailure`) with images that had **no** AVB footer, and
booted the same payloads once they had hash footers (AOSP test key, rollback index 0,
OS version 16, patch 2025-09-05) (*sourced*, Omarchy tegu notes, 2026-10-28). So every
image we flash gets `avbtool add_hash_footer`, signed with a development key generated
in the work area. Header values default to 16.0.0 / 2025-09 (the 9a's working values);
set them from the build on the other slot with `unpack_bootimg` (*decision*).

### 3.3 A/B, retries and rollback

- Linux goes on the slot that does not hold GrapheneOS (`LINUX_SLOT`). Only `_X`
  suffixed partitions are ever written; never unsuffixed names, never
  `bootloader`, `radio`, `modem`, `efs*`, `persist`, `devinfo` (*decision*, the rule
  both Tensor ports keep, *sourced*).
- `fastboot --set-active=X` gives an unmarked slot **three** tries on the 9a's
  bootloader; each boot that is not marked successful uses one, including good ones;
  then the bootloader boots the other slot (*sourced*, 9a notes). A `fastboot flash`
  of the slot clears its successful mark (*sourced*). Marking it means writing one bit
  of `devinfo` (byte `0x35`, bit `0x02` on the 9a) (*sourced*); we do not do that in
  M3 (*decision*).
- **The fallback is safe only while `userdata` belongs to Android.** On the 9a, stock
  Android on slot A, falling back from a Linux slot whose root was on `userdata`,
  overwrote `userdata` before anyone could choose anything, twice (*sourced*). Our M3
  boots keep the root in RAM and the init marks every block device read-only, so a
  fallback lands in an intact GrapheneOS.
- Rollback by hand: Power + Volume Down into fastboot, `fastboot --set-active=<other>`,
  `fastboot reboot`. A hung kernel: hold Power + Volume Down about a minute (*sourced*
  Linaro README).
- **Slot firmware.** Bootloader components (`bl1`, `abl`, `bl31`, `tzsw`, `gsa`, ...)
  are slotted. The 9a's slot B carried older firmware than A, and Linux died on an
  `HCRX_EL2` access until the firmware was equalised (*sourced*). Before the first
  boot, check `version-bootloader` on both slots; if B is older, flash the **same**
  `bootloader.img` that A runs to B (`fastboot --slot=b flash bootloader ...`). Never
  an older one: Pixel bootloaders have anti-rollback (*sourced*, Pixel 6 port).

### 3.4 Testing without flashing

`fastboot boot` exists on Tensor Pixels: Linaro's `run` target does
`fastboot stage vendor_boot.img` then `fastboot boot boot.img` (*sourced*), the Pixel 6
port boots U-Boot with `fastboot boot` (*sourced*). Untested on the 9 (*assumed*). The
catch: a RAM boot still takes `vendor_kernel_boot`, `init_boot` and `dtbo` from the
**active** slot, so it is only clean once the Linux slot is active and prepared. Plan:
the first boot of each track is a flash of the Linux slot (section 7); later
iterations of kernel and initramfs use `stage` + `boot` with no write.

### 3.5 Unlocking, and the way back to stock

1. In GrapheneOS: Settings, About, tap Build number 7 times; Developer options, enable
   **OEM unlocking**. GrapheneOS's setup wizard turns this off by default (*sourced*).
2. `adb reboot bootloader` (or Power + Volume Down), `fastboot flashing unlock`,
   confirm on the phone. **This wipes userdata** (*sourced*). Fastboot 35.0.1 or newer
   (*sourced*, GrapheneOS).
3. Consequences: every boot shows the unlocked-bootloader warning; GrapheneOS still
   boots, unverified; attestation-based apps fail (*assumed*). Never relock with
   anything but a complete, unmodified OS image on the phone (*sourced*, Pixel 6 port).
4. Back to GrapheneOS: its factory zip, `flash-all.sh` (wipes), then for a relocked
   GrapheneOS `fastboot flashing lock`. Back to stock: `fastboot erase avb_custom_key`
   while unlocked, then Google's factory image `flash-all.sh` or flash.android.com
   (*sourced*). Download the factory image for the current build **before** day one
   and verify its SHA-256.

## 4. Minimal first-boot userland

### 4.1 The bring-up initramfs

`scripts/pixel/make-initramfs.sh` builds it from Debian 13 (trixie, the stable release
the design picks) arm64 packages, verified against the archive's signed `InRelease`
and unpacked without running package scripts or an emulator: static busybox, `modetest`
and `evtest` with their libraries, the xx-os `init`, `xxos-probe` and
`dp-hpd-bridge`. Size on the desk: 8.1 MiB lz4 (without modules or firmware). The init
(`scripts/pixel/initramfs/init`):

1. mounts proc, sys, a RAM `/run`, devtmpfs (or tmpfs + `mdev` on a kernel without
   it), configfs, pstore;
2. sets every block device read-only unless the command line names it as root;
3. saves what the bootloader handed over (`/proc/cmdline`, `/proc/bootconfig`, DT
   model, `chosen` and `reserved-memory` nodes, pstore from the last boot);
4. loads modules: `/lib/modules/modules.load` then our second-stage list in order
   (downstream), or a modalias coldplug (mainline);
5. pets a watchdog only if one is already running (stock runs `gs_watchdogd`,
   *sourced* directory in gs-common; whether one runs at handoff is *assumed*);
6. brings up the gadget and the debug channel (4.2);
7. stays in RAM, or switches root per `xxos.root=` (4.3).

### 4.2 The debug channel before serial: USB gadget ACM + NCM

configfs gadget, Linux Foundation multifunction id `1d6b:0104`, functions `acm.usb0`
and `ncm.usb0`, bound to the first UDC. The phone is `172.16.42.1`, `udhcpd` hands
the desk `172.16.42.2`; a shell on the desk's `/dev/ttyACM0` and on telnet. Telnet
is unauthenticated, acceptable on a point-to-point USB link for M3 only (*decision*).
If the UDC is not `configured` after 20 s the init forces the USB role to device once.

Known risk (*sourced*): on the 9a, Google's own 6.1 kernel with a RAM-only userland
and a static PID 1 "never brought up USB"; the cause was not found. Others' mainline
boots of the 9a and the 9 do bring up USB NCM (*sourced*). That is why mainline boots
first.

### 4.3 Where the root lives

| Stage | Root | Touches storage | Use |
|---|---|---|---|
| A | the initramfs itself (`xxos.root=ram`) | nothing | M3 probes |
| B | a Debian ext4 image streamed over NCM into RAM (`xxos.root=usb`; on the desk `nc 172.16.42.1 5555 < root.img`), loop-mounted, `switch_root` to systemd | nothing | first systemd, compositor test in software |
| C | `userdata` formatted ext4 (`xxos.root=PARTLABEL=userdata`) | wipes GrapheneOS's data; a fallback to slot A then wipes ours | M4 and later, owner's call (section 9) |

The phone has 12 GB RAM; a minimal Debian with river or sway and pixman is a few
hundred MB (*assumed*). Build the image on the desk with `mmdebstrap` (needs an
arm64 binfmt or `--variant=extract` plus a first-boot configure step).

### 4.4 What "a picture through normal Linux graphics" needs

Two levels, both count as Linux graphics for M3 only at level 2:

1. **Splash handoff (simpledrm).** The bootloader leaves the panel running from a
   framebuffer at `0xfac00000`. A `simple-framebuffer` node plus `CONFIG_DRM_SIMPLEDRM`
   gives a DRM device and, with `FRAMEBUFFER_CONSOLE`, the kernel log on the panel: a
   debug channel without serial or USB. Caveat: the 9 and 9a panels run in command
   mode, so writes show only when the DECON is triggered. Omarchy's 9a needed "ABL's
   simple framebuffer with a DECON trigger write" (*sourced*); the tokay mainline DT
   has the matching nodes commented out because enabling DECON0 resets the splash
   (*sourced*). Also whether the bootloader adds a splash `reserved-memory` node to
   the downstream DT: the downstream driver supports a handover region found through
   `memory-region` (*sourced*, `exynos_drm_fb.c`), but no source DT names one, so the
   bootloader probably adds it at boot (*assumed*); the init records
   `reserved-memory` so the first boot answers this.
2. **The real display driver (KMS).** Mainline: `drm/exynos` DECON + DSIM + the tk4
   panel driver, as the tokay DT already wires up (*sourced*). Downstream:
   `exynos-drm.ko` is a complete atomic KMS driver with dumb buffers, which is all a
   software-rendered wlroots compositor needs (*sourced* driver features; *assumed*
   that its Android-specific properties do not trip wlroots). Probe: `xxos-probe
   display` lists connectors with `modetest` and draws a pattern on the first
   connected one.

**Touch** needs the AoC up: the bus negotiator hands the SPI bus between the AoC and
the AP over an AoC service (`tbn,mode = 2`, *sourced* mainline DT and downstream
defconfig `TOUCHSCREEN_TBN_AOC_CHANNEL_MODE=y`), so AoC firmware must load. The 9a
also needed the touch rails sequenced through the PMIC before the controller answered
(*sourced*). Probe: `xxos-probe touch`.

**The monitor.** Two different mechanisms:
- Downstream: the DP driver ignores Type-C. Android's USB HAL copies `hpd`,
  `pin_assignment` and `orientation` from the Type-C DisplayPort alt mode in sysfs into
  the driver's `drm-displayport` sysfs group, and the driver drops notifications unless
  `exynos_drm.dp_enabled=1` (*sourced*, `UsbDpUtils.cpp` and `exynos_drm_dp.c`).
  `dp-hpd-bridge` does the HAL's job in 50 lines of shell. This answers the design
  doc's open question "whether the monitor appears as a connector without Android's USB
  service": it can, with that bridge (*sourced* mechanism, untested).
- Mainline: HPD is driven from the Type-C notification inside the kernel, DECON1 and
  DPP1 drive the second output, on the `marcus/caiman-dp` branch for the 9 Pro
  (*sourced* commit subjects). Needs a tokay DT change to enable it (*assumed* small:
  same SoC nodes, same caimito display wiring).
- Debugging while docked: the dock takes the only USB-C port, so the gadget link is
  gone. Start the bridge and a delayed `xxos-probe dp` from the shell, unplug the desk,
  plug the dock, look at the monitor (the test pattern is the proof), plug the desk
  back and read `/run/xxos/evidence`. Needs a dock that powers the phone (PD
  pass-through).

## 5. Prior art on Tensor phones

| Project | Devices | Kernel | Status (date) | What it teaches | Mark |
|---|---|---|---|---|---|
| postmarketOS / Nura `google-tokay` | Pixel 9 | community mainline 7.1-rc2 | boots, simpledrm, USB net, touch and battery partial-to-yes (2026-06) | this exact phone boots mainline with USB | sourced |
| `Trijal08/kernel-mainline` | 9, 9 Pro, 9 Pro XL, 9a, Fold | mainline, zumapro | very active (2026-11-12): AoC audio, modem, camera, BT, DP branch | the tree to start from | sourced |
| Omarchy Mobile | 7 Pro (reference), 9a, 6 Pro, 7, 7a, 8a, 8 Pro, Fold | mainline 7.3 (`linux-omarchy-mobile`) | 9a: display, Panthor, touch, Wi-Fi, BT, audio, sensors, fingerprint; calls testing. 7 Pro: calls on a Verizon-network SIM (2026-11) | slot-B install of five images, AVB footers, the fallback that wipes userdata, firmware equalisation, `devinfo` marking, NV backups | sourced |
| `pixel6-mainline-nura` | Pixel 6 | M8's gs101 mainline | daily-usable: calls (VoLTE), SMS, MMS, data on Verizon and T-Mobile MVNOs, GPU (Panfrost), Wi-Fi, BT, sensors; poor standby (2026-10) | Exynos modem calls without Android; per-phone partition backups | sourced |
| Linaro `pixelscripts` / upstream gs101 | Pixel 6 (+ Pixel 10 targets) | upstream Linux | gs101 upstream since 6.8; BusyBox console, serial via USB-Cereal at 1.8 V | `fastboot stage` + `boot`, `oem uart` setup, DTB in vendor_boot | sourced |
| postmarketOS `google-raven` | Pixel 6 Pro | `linux-postmarketos-gs101` | display via simplefb only, touch yes | simplefb is the usual first picture | sourced |
| Google, "Build Pixel kernels" | Pixel 6 to 9a | GKI 6.1, plus android-mainline and 6.12 dev kernels for Pixel 6 only | factory-supported | build and flash recipe | sourced |
| Ubuntu Touch / Halium | Pixel 6 | Halium | port never finished | the Halium route has nothing on Tensor | reported |
| Droidian, Mobian, FuriOS | none found on Tensor | | | | reported (search) |

What did not work, as recorded: Google's 6.1 kernel with a non-Android PID 1 brought no
USB on the 9a; unfooted images; old slot firmware (`HCRX_EL2`); Plymouth crashing
without a console; falling back to stock with a Linux `userdata` (*all sourced*, 9a
notes). The Pixel 6 port's standby draw is about 600 mA (*sourced*): suspend is still
an open problem on every Tensor port.

## 6. Host setup (desk)

- Tools present on the desk: `fastboot`, `adb`, `mkbootimg`, `avbtool`, `dtc`,
  `fdtoverlay`, `lz4`, `zstd`, `cpio`, `bsdtar`, `gpgv`, `readelf`, clang.
  Missing: `debian-archive-keyring` (for `make-initramfs.sh`), `repo` (only for the
  AOSP manifest), `shellcheck`, `mkdtimg` (comes with the downstream tree's prebuilts;
  without it the mainline plan erases `dtbo`).
- Work area: `~/xxos-pixel` (`XXOS_PIXEL_WORK`), outside the repo; 713 GB free on the
  working disk. Expect tens of GB for the downstream tree with its toolchains
  (*assumed*), under 2 GB for a shallow mainline tree.
- Scripts, in order, all desk-only:
  ```
  scripts/pixel/fetch-kernel.sh mainline        # and: grapheneos
  scripts/pixel/build-kernel.sh mainline        # and: grapheneos
  scripts/pixel/make-initramfs.sh mainline
  LINUX_SLOT=b scripts/pixel/make-boot-images.sh mainline
  # -> out/mainline/images/FLASH-PLAN.txt, read and typed by a person
  ```

## 7. First-boot plan

### 7.1 Does it need the adapter?

No step below needs serial unless a boot fails without bringing up USB or a picture.
A silent failure costs the Linux slot's tries and falls back to an untouched
GrapheneOS (3.3). So: Boots 1 to 3 can run before the USB-Cereal arrives; Boot 4
(downstream) is the one most likely to need it (4.2). Owner decides whether to start
before it arrives (section 9). When it arrives: 1.8 V switch, three USB-C cables so
the adapter is not replugged, flip the plug if there is no output, `fastboot oem uart
enable` and `fastboot oem uart config 3000000`, console at 3,000,000 baud (*sourced*,
Linaro).

### 7.2 Steps

Each step: what to do, go/no-go, rollback. "Fallback slot" = the GrapheneOS slot.

**Desk 1. Build (no phone). ~3 h, mostly waiting.**
Fetch and build both kernels, build both initramfs and image sets for the chosen
`LINUX_SLOT`. Download and verify the GrapheneOS factory zip for the installed release
and Google's factory image for the same Android version (firmware, stock `init_boot`
header values, recovery path). Inspect the stock `vendor_kernel_boot`: `unpack_bootimg`,
`fdtget` the base DTBs' `/soc_compatible`, and compare with the mainline tokay DTB.
*Go:* `build-kernel.sh` config checks pass; images and `FLASH-PLAN.txt` exist.
*No-go:* the downstream fragment does not reach GKI (fix the build first; mainline can
still go ahead). *Rollback:* none needed.

**Bench 0. Unlock and inventory. ~1 h.**
In GrapheneOS enable OEM unlocking; note the build. Bootloader: `fastboot getvar all`
saved to a file (product, slot flags, `version-bootloader`, `unlocked`). Unlock (wipes
GrapheneOS data; set GrapheneOS up again minimally, no accounts). Read
`version-bootloader` on the other slot (`--set-active`, `reboot-bootloader`, read,
switch back).
*Go:* unlocked, GrapheneOS boots on its slot, both slots' bootloader versions recorded.
*No-go:* OEM unlocking greyed out (carrier lock); stop. *Rollback:* none needed.

**Bench 1. Equalise slot firmware if needed. ~15 min.**
Only if the Linux slot's bootloader is older: flash GrapheneOS's `bootloader.img` (the
one the fallback slot runs) to the Linux slot. Never an older one.
*Go:* both slots report the same `version-bootloader`. *Rollback:* not applicable;
this is the step not to improvise.

**Boot 1. Mainline, RAM root, display by simplefb or DECON. ~1 to 2 h.**
Type the commands in `out/mainline/images/FLASH-PLAN.txt` (five partitions of the
Linux slot, `--set-active`, reboot).
*Go:* the desk sees `1d6b:0104`, `/dev/ttyACM0` and an NCM link; shell at
`172.16.42.1`. Then `xxos-probe display` (a pattern on the panel) and
`xxos-probe touch`. Collect evidence (`xxos-probe collect`).
*No-go:* back in fastboot (`bootfailure`): check footers and header values, try the
stand-in `init_boot`, then the Linaro layout (erase `dtbo`). Black screen and no USB:
wait for serial. *Rollback:* set the fallback slot active; GrapheneOS untouched.

**Boot 2. Mainline, Debian root in RAM (`xxos.root=usb`). ~2 h.**
Rebuild `vendor_boot` with `XXOS_ROOT=usb`, RAM-boot it (`fastboot stage` +
`fastboot boot`) if the bootloader allows, else flash `vendor_boot_X`. Stream the
Debian image; systemd comes up; start river (or sway) with the pixman renderer on the
panel.
*Go:* a compositor on the panel, touch moves the cursor or taps. *No-go:* systemd
fails: read the journal over NCM; compositor fails: test with `modetest` and
`weston --backend=drm --renderer=pixman` to separate driver from compositor problems.
*Rollback:* as Boot 1.

**Boot 3. Mainline, the monitor. ~2 h (more if the DP patches need porting).**
Merge or rebase the `marcus/caiman-dp` commits onto the pinned tree, add the tokay DT
nodes, rebuild. Dock with PD pass-through and a monitor; procedure in 4.4.
*Go:* the monitor shows the test pattern, `card*-DP-*` reads `connected`, and the
compositor puts an output on it. That plus Boots 1 and 2 is the M3 exit test.
*No-go:* evidence shows the alt mode entered but no link: hand to serial and the DP
branch's debug variant. *Rollback:* unplug; as Boot 1.

**Boot 4. Downstream 6.1 (GrapheneOS tree), RAM root. ~2 to 4 h.**
Same plan file for the `grapheneos` track (adds `vendor_kernel_boot_X` and Kleaf's
`dtbo`). Then the same probes, and `dp-hpd-bridge` for the monitor.
*Go:* USB channel up, `exynos-drm` draws on the panel, touch events, the monitor
connects through the bridge. *No-go:* no USB (the 9a result): wait for serial, then
read the boot from `ttySAC0`. *Rollback:* as Boot 1.

**Decide.** Write the M3 result into the design doc: which kernel passed which part
of the gate, with the evidence tarballs kept off the repo.

Before any write beyond the five Linux-slot images (e.g. formatting `userdata` for
Stage C), back up `efs`, `efs_backup`, `modem_userdata`, `persist`, `devinfo`,
`metadata`, `modem_a`, `modem_b` read-only over NCM (`dd` from the RAM shell) with
SHA-256 sums, kept off the phone and out of git; they hold the IMEI and radio
calibration that no factory image restores (*sourced*, both Tensor ports).

### 7.3 Time

| Block | Estimate |
|---|---|
| Desk 1 (fetch, two builds, images, factory images) | 3 h, about 1 h hands-on |
| Bench 0 + 1 | 1.5 h |
| Boots 1 to 3 (mainline to M3) | 1 to 2 days, if the DP port is small |
| Boot 4 (downstream) | half a day with USB; 1 to 2 days more if it needs serial |
| Total to the M3 decision | about one working week (*assumed*) |

### 7.4 Parts

| Part | Why | Status |
|---|---|---|
| USB-Cereal (or equivalent 1.8 V SBU UART breakout) | serial console for silent failures | on order |
| USB-C DisplayPort alt-mode adapter or dock **with PD pass-through** | the monitor test; must be DP alt mode, not DisplayLink (DisplayLink is a USB graphics device and proves nothing about DP) | to buy |
| Monitor with DP or HDMI input matching the adapter | | have (assumed) |
| 3 short USB-C cables | keep the Cereal's connectors from wear | to buy |
| USB-C PD charger, 45 W or more | powers dock and phone during the monitor test | have (assumed) |
| A USB 3 C-to-C or C-to-A data cable for the desk | the gadget link | have (assumed) |

## 8. Risks, updated

1. **A fallback to the Android slot wiping a Linux `userdata`.** Seen twice on the 9a.
   Mitigated in M3 by keeping the root in RAM; becomes real at Stage C. Needs a
   decision on marking the Linux slot successful (a `devinfo` write) before then.
2. **The downstream kernel not bringing up USB without Android's init.** One recorded
   failure on the 9a, cause unknown. Mitigated by booting mainline first and by serial.
3. **Touch depending on the AoC.** The bus negotiator talks to AoC firmware; firmware
   and driver versions must match. Partial on the 9 per postmarketOS; works on the 9a
   with power sequencing.
4. **DisplayPort on tokay unproven.** Mainline DP exists for the 9 Pro on a side
   branch; downstream needs the HAL bridge. Either way nobody has shown a monitor on a
   9 under Linux yet.
5. **Slot firmware mismatch and anti-rollback.** Old firmware on the Linux slot broke
   the 9a's boot; a downgrade could brick. Check versions, equalise upward only.
6. **The kernel base is a small community's tree.** Mainline G4 work is a few people,
   largely tool-generated and not upstream. Fork risk if they stop; licence-clean (GPL) but
   upstreaming our own fixes may be hard.
7. **Irreversible per-phone data** (`efs`, `persist`, `devinfo`): one mistyped
   partition name. Mitigated by suffixed names only, the read-only guard in the init,
   and backups before any wider write.
8. **Google's source drying up.** Confirmed for the AOSP kernel manifest (frozen
   2025-06). GrapheneOS's tree and mainline are the live sources.
9. **Voice on the Exynos 5400 and Verizon** (design risk 1): still the M5 gate, but
   prior art on Exynos 5123 (Pixel 6) and the Pixel 7 Pro now shows calls without an
   Android container. This argues for re-evaluating design section 6 (modem through a
   Halium container) after M3.

## 9. Decisions for the owner

1. **Accept the two-kernel M3, mainline first** (section 0). It amends design section 7
   ("Google's 6.1 Pixel kernel") and, if mainline passes, sections 5 and 6.
2. **Start Boots 1 to 3 before the serial adapter arrives**, or wait. Recommended:
   start; the failure mode is a fallback to an untouched GrapheneOS.
3. **Which slot is Linux** (the one GrapheneOS is not on) and whether GrapheneOS stays
   on the other slot through M3. Recommended: yes, it is the fallback and the firmware
   source.
4. **When the root moves to `userdata`** (Stage C): it costs the GrapheneOS data and
   needs the slot-marking decision. Recommended: not before M4.
5. **Whether to mark the Linux slot successful by writing `devinfo`**, with a tool and
   procedure modelled on Omarchy's (dry run, one-byte diff, read-back). Recommended:
   decide at M4, not in M3.
6. **Buy:** a DP alt-mode adapter or dock with PD pass-through (not DisplayLink) and
   three short USB-C cables. Install on the desk: `debian-archive-keyring`,
   `shellcheck`, and `repo` if the AOSP manifest is wanted.

## 10. Sources

- Google, Build Pixel kernels: https://source.android.com/docs/setup/build/building-pixel-kernels
- Google, vendor boot partitions: https://source.android.com/docs/core/architecture/partitions/vendor-boot-partitions
- Kernel manifest, caimito branch: https://android.googlesource.com/kernel/manifest/+/refs/heads/android-gs-caimito-6.1-android16
- Caimito device kernel (BUILD.bazel, DTs, module lists): https://android.googlesource.com/kernel/devices/google/caimito/ and `.../zumapro/`, `.../zuma/`
- GKI defconfig on that branch: https://android.googlesource.com/kernel/common/+/refs/heads/android-gs-caimito-6.1-android16/arch/arm64/configs/gki_defconfig
- Kleaf kernel config docs: https://android.googlesource.com/kernel/build/+/refs/heads/android-gs-caimito-6.1-android16/kleaf/docs/kernel_config.md
- Samsung display driver (`exynos_drm_drv.c`, `exynos_drm_dp.c`, `exynos_drm_fb.c`): https://android.googlesource.com/kernel/google-modules/display/samsung/
- Pixel USB HAL, DisplayPort (`UsbDpUtils.cpp`): https://android.googlesource.com/platform/hardware/google/pixel/+/refs/heads/main/usb/
- zumapro BoardConfig: https://android.googlesource.com/device/google/zumapro/+/refs/heads/android15-security-release/BoardConfig-common.mk
- GrapheneOS build and CLI install: https://grapheneos.org/build , https://grapheneos.org/install/cli , https://gitlab.com/grapheneos/kernel_pixel_6.1
- postmarketOS / Nura wiki, Pixel 9: https://wiki.postmarketos.org/wiki/Google_Pixel_9_(google-tokay) ; Tensor G1, G4 and Pixel 6 Pro pages on the same wiki
- Mainline G4 tree: https://github.com/Trijal08/kernel-mainline (branch `zumapro-google-caimito`); DP: https://github.com/marcusramberg/linux-mainline (branch `marcus/caiman-dp`); tokay fork: https://github.com/orgwithsomething/linux-zumapro
- Omarchy Mobile: https://github.com/derekross/omarchy-mobile (`docs/DEVICES.md`, `device/tegu/NOTES.md`, `device/tegu/device.conf`); kernel https://github.com/derekross/linux-omarchy-mobile
- Pixel 6 mainline port: https://github.com/TheNoswad/pixel6-mainline-nura (README, INSTALL.md)
- Linaro pixelscripts: https://gitlab.com/LinaroLtd/googlelt/pixelscripts (README, Makefile)
- systemd kernel requirements: https://github.com/systemd/systemd/blob/main/README
- Pixel 6 upstream talk (W. McVicker, 2025): https://www.youtube.com/watch?v=ctF8cFCQOPo
