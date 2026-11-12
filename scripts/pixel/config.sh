# shellcheck shell=sh disable=SC2034 # read by the scripts that source it
# Pinned inputs for the Pixel 9 (tokay) bring-up. Sourced by the other
# scripts in this directory; see docs/pixel-9-bringup.md for where each value
# comes from. Nothing here talks to a phone.

# Work area for sources, builds and images. Outside the repository on purpose:
# kernel trees are tens of GB and images may hold vendor firmware.
XXOS_PIXEL_WORK=${XXOS_PIXEL_WORK:-$HOME/xxos-pixel}

DEVICE=tokay
# Retail board values from zumapro-tokay-mp.dts (Google's caimito device tree).
BOARD_ID=0x60a06
BOARD_REV=0x10000
PANEL_SIZE=1080x2424

# Partition sizes from device/google/zumapro BoardConfig-common.mk.
BOOT_SIZE=$((64 * 1024 * 1024))
VENDOR_BOOT_SIZE=$((64 * 1024 * 1024))
VENDOR_KERNEL_BOOT_SIZE=$((64 * 1024 * 1024))
INIT_BOOT_SIZE=$((8 * 1024 * 1024))
DTBO_SIZE=$((16 * 1024 * 1024))

# Downstream track, default source: GrapheneOS's combined Pixel 6.1 tree,
# maintained through Android 17 (branch 17, commit of 2026-11-11).
GOS_KERNEL_URL=https://gitlab.com/grapheneos/kernel_pixel_6.1.git
GOS_KERNEL_BRANCH=17
GOS_KERNEL_REF=a0576fe5557bf1e735317a28fac3319f9d035306

# Downstream track, alternative source: Google's own kernel manifest. The
# caimito branch was last updated 2025-06-10 and has not moved since.
AOSP_MANIFEST_URL=https://android.googlesource.com/kernel/manifest
AOSP_MANIFEST_BRANCH=android-gs-caimito-6.1-android16
AOSP_MANIFEST_REF=cdef6011c8645d62360c08c557291cf54d930596

# Mainline track: the community Tensor G4 tree that carries a tokay device
# tree (zumapro-tokay.dts). Head of 2026-11-12.
MAINLINE_URL=https://github.com/Trijal08/kernel-mainline.git
MAINLINE_BRANCH=zumapro-google-caimito
MAINLINE_REF=5d0958868d97fcc2133ca31ad70ef5db9c19bf25
MAINLINE_DEFCONFIG=zumapro_defconfig
MAINLINE_DTB=exynos/google/zumapro-tokay.dtb

# Debian for the bring-up initramfs and the first root. Stable is trixie.
DEBIAN_MIRROR=${DEBIAN_MIRROR:-https://deb.debian.org/debian}
DEBIAN_SUITE=${DEBIAN_SUITE:-trixie}
DEBIAN_KEYRING=${DEBIAN_KEYRING:-/usr/share/keyrings/debian-archive-keyring.gpg}

# Boot image header values. Set them to the values of the build on the other
# slot (unpack_bootimg on its init_boot or boot image); unlocked, the
# bootloader does not enforce them, but a 9a refused images without AVB
# footers carrying them (see the doc, section 3.2).
OS_VERSION=${OS_VERSION:-16.0.0}
OS_PATCH_LEVEL=${OS_PATCH_LEVEL:-2025-09}
SECURITY_PATCH=${SECURITY_PATCH:-$OS_PATCH_LEVEL-05}

die() {
    echo "error: $*" >&2
    exit 1
}

need() {
    for tool in "$@"; do
        command -v "$tool" >/dev/null 2>&1 || die "missing host tool: $tool"
    done
}
