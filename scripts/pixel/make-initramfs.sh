#!/bin/sh
# Build the bring-up initramfs (busybox, modetest, evtest, the xx-os init)
# from signed Debian arm64 packages. No package scripts run and no emulator
# is needed: packages are verified against the archive's signed index and
# unpacked.
#
#   make-initramfs.sh grapheneos|aosp|mainline
#
# Optional environment:
#   EXTRA_PACKAGES   more Debian packages to unpack (space separated)
#   FIRMWARE_DIR     a local copy of the phone's /vendor/firmware ...
#   FIRMWARE_FILES   ... and the file globs from it to include. Firmware is
#                    Google's: it stays in the work area, never in git.
#
# Output: $XXOS_PIXEL_WORK/out/<track>/initramfs.cpio.lz4
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/config.sh"
need curl gpgv xz sha256sum bsdtar cpio lz4 readelf awk

track=${1:-grapheneos}
out=$XXOS_PIXEL_WORK/out/$track
cache=$XXOS_PIXEL_WORK/cache/debian-$DEBIAN_SUITE-arm64
stage=$out/initramfs.d
mkdir -p "$out" "$cache"

PACKAGES="busybox-static libc6 libdrm2 libdrm-common libdrm-tests evtest ${EXTRA_PACKAGES:-}"

# --- Signed index -----------------------------------------------------------
[ -f "$DEBIAN_KEYRING" ] || die "no Debian keyring at $DEBIAN_KEYRING (install debian-archive-keyring or set DEBIAN_KEYRING)"
dist=$DEBIAN_MIRROR/dists/$DEBIAN_SUITE
curl -fsSL -o "$cache/InRelease" "$dist/InRelease"
gpgv --keyring "$DEBIAN_KEYRING" "$cache/InRelease" 2>"$cache/gpgv.log" ||
    die "InRelease signature check failed (see $cache/gpgv.log)"
want=$(awk '/^SHA256:/ { s = 1; next } /^[A-Za-z]/ { s = 0 }
            s && $3 == "main/binary-arm64/Packages.xz" { print $1 }' "$cache/InRelease")
[ -n "$want" ] || die "Packages.xz not listed in InRelease"
curl -fsSL -o "$cache/Packages.xz" "$dist/main/binary-arm64/Packages.xz"
echo "$want  $cache/Packages.xz" | sha256sum -c --quiet - || die "Packages.xz hash mismatch"
xz -dc "$cache/Packages.xz" >"$cache/Packages"

# --- Packages -----------------------------------------------------------------
rm -rf "$stage"
mkdir -p "$stage/usr/bin" "$stage/usr/sbin" "$stage/usr/lib" "$stage/bin" "$stage/lib"
for pkg in $PACKAGES; do
    # shellcheck disable=SC2046 # two words: path and hash
    set -- $(awk -v p="$pkg" 'BEGIN { RS = ""; FS = "\n" }
        { name = ""; file = ""; sum = ""
          for (i = 1; i <= NF; i++) {
            if ($i ~ /^Package: /) name = substr($i, 10)
            if ($i ~ /^Filename: /) file = substr($i, 11)
            if ($i ~ /^SHA256: /) sum = substr($i, 9) }
          if (name == p) { print file, sum; exit } }' "$cache/Packages")
    [ $# -eq 2 ] || die "package $pkg not in $DEBIAN_SUITE arm64"
    deb=$cache/$(basename "$1")
    [ -f "$deb" ] || curl -fsSL -o "$deb" "$DEBIAN_MIRROR/$1"
    echo "$2  $deb" | sha256sum -c --quiet - || die "$deb hash mismatch"
    tmp=$out/unpack.$pkg
    rm -rf "$tmp" && mkdir -p "$tmp"
    bsdtar -xOf "$deb" 'data.tar.*' | bsdtar -xf - -C "$tmp"
    # Fold everything into /usr (merged-usr), whatever the package used.
    for d in bin sbin lib; do
        if [ -d "$tmp/$d" ] && [ ! -L "$tmp/$d" ]; then
            mkdir -p "$tmp/usr/$d"
            cp -a "$tmp/$d/." "$tmp/usr/$d/"
        fi
    done
    [ -d "$tmp/usr" ] && cp -a "$tmp/usr/." "$stage/usr/"
    [ -d "$tmp/etc" ] && mkdir -p "$stage/etc" && cp -a "$tmp/etc/." "$stage/etc/"
    rm -rf "$tmp"
    echo "unpacked $pkg"
done
rm -rf "$stage/usr/share/doc" "$stage/usr/share/man" "$stage/usr/share/locale" \
    "$stage/usr/share/lintian" "$stage/usr/share/bug"
# libdrm-tests also ships tests for other GPUs, which want their own libdrm
# flavours. Only the generic KMS tools are wanted.
rm -f "$stage"/usr/bin/amdgpu_* "$stage"/usr/bin/etnaviv_* "$stage"/usr/bin/tegra-*

# Real /lib and /bin with links into /usr, so a vendor ramdisk unpacked
# over this one can still add /lib/modules.
ln -sf /usr/lib/ld-linux-aarch64.so.1 "$stage/lib/ld-linux-aarch64.so.1"
ln -sf /usr/lib/aarch64-linux-gnu "$stage/lib/aarch64-linux-gnu"
bb=$stage/usr/bin/busybox
[ -f "$bb" ] || die "busybox-static did not provide usr/bin/busybox"
cp "$bb" "$stage/bin/busybox"
install -m 0755 "$HERE/initramfs/init" "$stage/init"
install -m 0755 "$HERE/initramfs/xxos-probe" "$stage/usr/bin/xxos-probe"
install -m 0755 "$HERE/initramfs/dp-hpd-bridge" "$stage/usr/bin/dp-hpd-bridge"

# --- Modules ------------------------------------------------------------------
case $track in
grapheneos | aosp)
    # First-stage modules ride in our vendor_kernel_boot. The rest (touch,
    # Wi-Fi, GPU, ...) normally live on vendor_dlkm; take them from dist.
    dist=$out/dist
    list=$dist/vendor_dlkm.modules.load
    if [ -f "$list" ]; then
        mkdir -p "$stage/usr/lib/modules/stage2"
        : >"$stage/usr/lib/modules/stage2/modules.load"
        while read -r ko; do
            f=$dist/$(basename "$ko")
            [ -f "$f" ] || { echo "warning: $ko not in dist" >&2; continue; }
            cp "$f" "$stage/usr/lib/modules/stage2/"
            basename "$ko" >>"$stage/usr/lib/modules/stage2/modules.load"
        done <"$list"
    else
        echo "warning: no $list; second-stage modules (touch) left out" >&2
    fi
    ;;
mainline)
    [ -d "$out/modules/lib/modules" ] || die "no mainline modules; run build-kernel.sh mainline"
    mkdir -p "$stage/usr/lib/modules"
    cp -a "$out/modules/lib/modules/." "$stage/usr/lib/modules/"
    find "$stage/usr/lib/modules" -name build -type l -delete
    ;;
*) die "unknown track '$track'" ;;
esac

# --- Firmware (local only) ----------------------------------------------------
if [ -n "${FIRMWARE_DIR:-}" ]; then
    mkdir -p "$stage/usr/lib/firmware"
    for glob in ${FIRMWARE_FILES:-}; do
        for f in "$FIRMWARE_DIR"/$glob; do
            [ -e "$f" ] && cp -a "$f" "$stage/usr/lib/firmware/"
        done
    done
fi

# --- Every binary finds its libraries ------------------------------------------
missing=$(find "$stage/usr/bin" "$stage/usr/sbin" -type f -perm -u+x 2>/dev/null |
    while read -r f; do
        readelf -d "$f" 2>/dev/null | sed -n 's/.*Shared library: \[\(.*\)\]/\1/p'
    done | sort -u | while read -r lib; do
        [ -e "$stage/usr/lib/aarch64-linux-gnu/$lib" ] || [ -e "$stage/usr/lib/$lib" ] ||
            echo "$lib"
    done)
[ -z "$missing" ] || die "libraries missing (add their packages to EXTRA_PACKAGES): $missing"

# Busybox applets the init uses, checked when an arm64 emulator is around.
for q in qemu-aarch64-static qemu-aarch64; do
    command -v "$q" >/dev/null 2>&1 || continue
    applets=$("$q" "$bb" --list)
    for a in blockdev insmod modprobe mdev nc setsid switch_root telnetd udhcpd watchdog timeout; do
        echo "$applets" | grep -qx "$a" || echo "warning: busybox lacks $a" >&2
    done
    break
done

# --- Archive --------------------------------------------------------------------
img=$out/initramfs.cpio.lz4
(cd "$stage" && find . | LC_ALL=C sort | cpio -o -H newc -R 0:0 --quiet) | lz4 -l -9 -q >"$img"
size=$(wc -c <"$img")
echo "initramfs: $img ($((size / 1024)) KiB)"
[ "$size" -lt $((48 * 1024 * 1024)) ] ||
    echo "warning: over 48 MiB; vendor_boot is 64 MiB including the DTB" >&2
