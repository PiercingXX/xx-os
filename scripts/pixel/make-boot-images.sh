#!/bin/sh
# Pack the Android v4 boot images for one Linux slot of the Pixel 9 (tokay)
# and write the fastboot commands that would flash them. It does not run
# them: flashing is a person at the bench with the checklist in
# docs/pixel-9-bringup.md section 7.
#
#   LINUX_SLOT=b make-boot-images.sh grapheneos|aosp|mainline
#
# Environment:
#   LINUX_SLOT   a or b, the slot that does NOT hold the working Android
#   XXOS_ROOT    ram (default), usb or PARTLABEL=userdata (see initramfs/init)
#   EXTRA_CMDLINE  appended to the kernel command line
#
# Output in $XXOS_PIXEL_WORK/out/<track>/images: boot.img, vendor_boot.img,
# init_boot.img (stand-in), dtbo.img and (downstream) vendor_kernel_boot.img,
# each with an AVB hash footer signed by a local development key, plus
# FLASH-PLAN.txt.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/config.sh"
need avbtool openssl dtc

track=${1:-grapheneos}
slot=${LINUX_SLOT:-}
case $slot in a | b) ;; *) die "set LINUX_SLOT=a or b (the slot without Android)" ;; esac
out=$XXOS_PIXEL_WORK/out/$track
img=$out/images
initramfs=$out/initramfs.cpio.lz4
[ -f "$initramfs" ] || die "no $initramfs; run make-initramfs.sh $track"
rm -rf "$img" && mkdir -p "$img"

# mkbootimg: the host's, else the copy in a downstream tree.
MKBOOTIMG=${MKBOOTIMG:-$(command -v mkbootimg || true)}
for t in grapheneos aosp; do
    [ -n "$MKBOOTIMG" ] && break
    f=$XXOS_PIXEL_WORK/src/$t/tools/mkbootimg/mkbootimg.py
    [ -f "$f" ] && MKBOOTIMG="python3 $f"
done
[ -n "$MKBOOTIMG" ] || die "no mkbootimg (install android-tools or fetch a downstream tree)"

cmdline="console=tty0 clk_ignore_unused pd_ignore_unused printk.devkmsg=on bootconfig"
cmdline="$cmdline xxos.root=${XXOS_ROOT:-ram}"

case $track in
grapheneos | aosp)
    dist=$out/dist
    kernel=$dist/Image.lz4
    # No cgroup_disable=memory (stock vendor_boot has it; systemd wants
    # the memory controller). DP notifications on, firmware where we put it.
    cmdline="$cmdline exynos_drm.dp_enabled=1 swiotlb=noforce"
    for f in "$kernel" "$dist/vendor_kernel_boot.img" "$dist/dtbo.img"; do
        [ -f "$f" ] || die "missing $f; run build-kernel.sh $track"
    done
    cp "$dist/vendor_kernel_boot.img" "$img/vendor_kernel_boot.img"
    cp "$dist/dtbo.img" "$img/dtbo.img"
    dtb_args=
    ;;
mainline)
    kernel=$out/Image.gz
    dtb=$out/$(basename "$MAINLINE_DTB")
    for f in "$kernel" "$dtb"; do
        [ -f "$f" ] || die "missing $f; run build-kernel.sh mainline"
    done
    dtb_args="--dtb $dtb"
    # A minimal overlay carrying tokay's board id, so the bootloader finds
    # a matching dtbo entry without applying Android's overlays to the
    # mainline tree (the approach the Pixel 9a mainline port uses).
    MKDTIMG=${MKDTIMG:-$(command -v mkdtimg || true)}
    for t in grapheneos aosp; do
        [ -n "$MKDTIMG" ] && break
        f=$XXOS_PIXEL_WORK/src/$t/prebuilts/kernel-build-tools/linux-x86/bin/mkdtimg
        [ -x "$f" ] && MKDTIMG=$f
    done
    if [ -n "$MKDTIMG" ]; then
        cat >"$img/board.dts" <<EOF
/dts-v1/;
/plugin/;
/ {
	board_id = <$BOARD_ID>;
	board_rev = <$BOARD_REV>;
	fragment@0 {
		target-path = "/";
		__overlay__ { xxos,board = "$DEVICE"; };
	};
};
EOF
        dtc -@ -I dts -O dtb -o "$img/board.dtbo" "$img/board.dts"
        "$MKDTIMG" create "$img/dtbo.img" --page_size=4096 \
            --id=/:board_id --rev=/:board_rev "$img/board.dtbo"
    else
        echo "warning: no mkdtimg; the plan erases dtbo instead" >&2
    fi
    ;;
*) die "unknown track '$track'" ;;
esac
cmdline="$cmdline ${EXTRA_CMDLINE:-}"

# --- Images -----------------------------------------------------------------
# shellcheck disable=SC2086 # MKBOOTIMG may be "python3 path"
$MKBOOTIMG --header_version 4 \
    --kernel "$kernel" \
    --os_version "$OS_VERSION" --os_patch_level "$OS_PATCH_LEVEL" \
    --output "$img/boot.img"
# shellcheck disable=SC2086 # MKBOOTIMG and dtb_args split on purpose
$MKBOOTIMG --header_version 4 --pagesize 2048 \
    $dtb_args \
    --vendor_cmdline "$cmdline" \
    --ramdisk_type platform --ramdisk_name xxos \
    --vendor_ramdisk_fragment "$initramfs" \
    --vendor_boot "$img/vendor_boot.img"
# A stand-in init_boot with an almost empty ramdisk, for the case where the
# bootloader refuses a slot whose init_boot is erased (the plan's fallback).
mkdir -p "$img/init_boot.d/xxos"
(cd "$img/init_boot.d" && find . | cpio -o -H newc -R 0:0 --quiet) | lz4 -l -9 -q >"$img/init_boot.cpio.lz4"
rm -rf "$img/init_boot.d"
# shellcheck disable=SC2086 # MKBOOTIMG may be "python3 path"
$MKBOOTIMG --header_version 4 \
    --ramdisk "$img/init_boot.cpio.lz4" \
    --os_version "$OS_VERSION" --os_patch_level "$OS_PATCH_LEVEL" \
    --output "$img/init_boot.img"

# --- AVB hash footers with a local development key -------------------------
# A Pixel 9a refused unfooted images and booted footed ones (doc 3.2). The
# key is generated here and stays in the work area; an unlocked bootloader
# does not check whose it is.
key=$XXOS_PIXEL_WORK/keys/avb-dev-rsa2048.pem
if [ ! -f "$key" ]; then
    mkdir -p "$(dirname "$key")"
    (umask 077 && openssl genrsa -out "$key" 2048 2>/dev/null)
fi
footer() { # footer IMAGE PARTITION SIZE [extra avbtool args]
    f=$1 part=$2 size=$3
    shift 3
    [ "$(wc -c <"$f")" -lt "$size" ] || die "$f does not fit $part ($size bytes)"
    avbtool add_hash_footer --image "$f" --partition_name "$part" \
        --partition_size "$size" --key "$key" --algorithm SHA256_RSA2048 \
        --rollback_index 0 "$@"
}
footer "$img/boot.img" boot "$BOOT_SIZE" \
    --prop "com.android.build.boot.os_version:${OS_VERSION%%.*}" \
    --prop "com.android.build.boot.security_patch:$SECURITY_PATCH"
footer "$img/vendor_boot.img" vendor_boot "$VENDOR_BOOT_SIZE"
footer "$img/init_boot.img" init_boot "$INIT_BOOT_SIZE" \
    --prop "com.android.build.init_boot.os_version:${OS_VERSION%%.*}" \
    --prop "com.android.build.init_boot.security_patch:$SECURITY_PATCH"
if [ -f "$img/vendor_kernel_boot.img" ]; then
    avbtool erase_footer --image "$img/vendor_kernel_boot.img" 2>/dev/null || true
    footer "$img/vendor_kernel_boot.img" vendor_kernel_boot "$VENDOR_KERNEL_BOOT_SIZE"
fi
if [ -f "$img/dtbo.img" ]; then
    avbtool erase_footer --image "$img/dtbo.img" 2>/dev/null || true
    footer "$img/dtbo.img" dtbo "$DTBO_SIZE"
fi

# --- The plan, as text -----------------------------------------------------------
plan=$img/FLASH-PLAN.txt
{
    echo "# xx-os Pixel 9 ($DEVICE), track $track, Linux on slot $slot."
    echo "# Generated $(date -u +%Y-%m-%dT%H:%MZ). Read docs/pixel-9-bringup.md 7 first."
    echo "# Phone in bootloader fastboot (power + volume down), not fastbootd."
    echo
    echo "fastboot getvar product            # must say $DEVICE"
    echo "fastboot getvar current-slot       # must NOT be $slot (that slot is Android)"
    echo "fastboot getvar unlocked           # must say yes"
    echo
    echo "fastboot flash boot_$slot $img/boot.img"
    echo "fastboot flash vendor_boot_$slot $img/vendor_boot.img"
    if [ -f "$img/vendor_kernel_boot.img" ]; then
        echo "fastboot flash vendor_kernel_boot_$slot $img/vendor_kernel_boot.img"
    else
        echo "fastboot erase vendor_kernel_boot_$slot   # the DTB comes from vendor_boot"
    fi
    echo "fastboot erase init_boot_$slot            # our ramdisk is in vendor_boot"
    echo "#   if the bootloader then refuses the slot: fastboot flash init_boot_$slot $img/init_boot.img"
    if [ -f "$img/dtbo.img" ]; then
        echo "fastboot flash dtbo_$slot $img/dtbo.img"
    else
        echo "fastboot erase dtbo_$slot"
    fi
    echo "fastboot --set-active=$slot"
    echo "fastboot reboot"
    echo
    echo "# Rollback: power + volume down into fastboot, then"
    echo "#   fastboot --set-active=$( [ "$slot" = a ] && echo b || echo a ) && fastboot reboot"
} >"$plan"

ls -l "$img"
echo "plan: $plan (not executed)"
