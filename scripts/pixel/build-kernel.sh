#!/bin/sh
# Build a kernel for the Pixel 9 (tokay) bring-up from a tree fetched by
# fetch-kernel.sh, with xxos-debian.config applied, and check the result.
#
#   build-kernel.sh grapheneos|aosp   Kleaf (Bazel) build of the caimito
#                                     kernel: GKI built from source, Pixel
#                                     modules, dtb, dtbo, vendor_kernel_boot
#   build-kernel.sh mainline          make build: Image, tokay dtb, modules
#
# Output: $XXOS_PIXEL_WORK/out/<track>/. Never touches a phone.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/config.sh"

track=${1:-grapheneos}
src=$XXOS_PIXEL_WORK/src/$track
out=$XXOS_PIXEL_WORK/out/$track
fragment=$HERE/xxos-debian.config
[ -d "$src" ] || die "no source at $src; run fetch-kernel.sh $track first"
mkdir -p "$out"

# Every CONFIG_X=y line of the fragment must be =y in the built config, and
# every "is not set" line must not be =y.
check_config() {
    cfg=$1 bad=0
    grep -E '^CONFIG_[A-Z0-9_]+=y$' "$fragment" | while read -r line; do
        grep -qx "$line" "$cfg" || { echo "missing: $line" >&2; exit 1; }
    done || bad=1
    grep -E '^# CONFIG_[A-Z0-9_]+ is not set$' "$fragment" |
        sed 's/^# \(CONFIG_[A-Z0-9_]*\) is not set$/\1/' | while read -r sym; do
        ! grep -qx "$sym=y" "$cfg" || { echo "still set: $sym" >&2; exit 1; }
    done || bad=1
    [ "$bad" = 0 ] || die "$cfg does not carry xxos-debian.config"
    echo "config check passed: $cfg"
}

build_downstream() {
    need python3
    dev=$src/private/devices/google/caimito
    [ -f "$dev/BUILD.bazel" ] || die "no caimito device directory in $src"
    # Kleaf takes a fragment from the command line only as an exported
    # source-tree target. Copy it next to the caimito build and export it.
    cp "$fragment" "$dev/xxos_debian_defconfig"
    grep -q 'xxos_debian_defconfig' "$dev/BUILD.bazel" ||
        printf '\nexports_files(["xxos_debian_defconfig"])\n' >>"$dev/BUILD.bazel"

    # GrapheneOS's tree is one git repository, not a repo checkout; Kleaf
    # finds its project list through this variable.
    if [ -f "$src/aosp_manifest.xml" ]; then
        KLEAF_REPO_MANIFEST=aosp_manifest.xml
        export KLEAF_REPO_MANIFEST
    fi
    cd "$src"
    # The device bazelrc defaults to a downloaded, signed GKI. The core
    # kernel needs our options, so build it from source instead; the
    # prebuilt fips140.ko is signed for Google's GKI and cannot load.
    tools/bazel run \
        --config=stamp \
        --config=caimito \
        --kernel_package=@//aosp \
        --use_prebuilt_gki=false \
        --use_signed_prebuilts=false \
        --download_prebuilt_gki_fips140=false \
        --defconfig_fragment=//private/devices/google/caimito:xxos_debian_defconfig \
        //private/devices/google/caimito:zumapro_caimito_dist

    dist=$src/out/caimito/dist
    [ -d "$dist" ] || die "no dist directory at $dist"
    rm -rf "$out/dist"
    cp -a "$dist" "$out/dist"
    # The core kernel's own config, read out of the image (IKCONFIG=y),
    # is the one that matters for devtmpfs and friends.
    sh "$src/aosp/scripts/extract-ikconfig" "$out/dist/Image" >"$out/gki.config"
    check_config "$out/gki.config"
    [ -f "$out/dist/.config" ] && check_config "$out/dist/.config"
    for f in Image.lz4 dtb.img dtbo.img vendor_kernel_boot.img; do
        [ -f "$out/dist/$f" ] || echo "warning: $f not in dist" >&2
    done
}

build_mainline() {
    need make clang ld.lld
    bld=$out/build
    mk() { make -C "$src" O="$bld" ARCH=arm64 LLVM=1 -j"$(nproc)" "$@"; }
    mk "$MAINLINE_DEFCONFIG"
    "$src/scripts/kconfig/merge_config.sh" -m -O "$bld" "$bld/.config" "$fragment"
    mk olddefconfig
    check_config "$bld/.config"
    mk Image.gz dtbs modules
    rm -rf "$out/modules"
    mk modules_install INSTALL_MOD_PATH="$out/modules" INSTALL_MOD_STRIP=1
    cp "$bld/arch/arm64/boot/Image.gz" "$out/Image.gz"
    cp "$bld/arch/arm64/boot/dts/$MAINLINE_DTB" "$out/$(basename "$MAINLINE_DTB")"
    cp "$bld/.config" "$out/kernel.config"
}

case $track in
grapheneos | aosp) build_downstream ;;
mainline) build_mainline ;;
*) die "unknown track '$track' (grapheneos, aosp or mainline)" ;;
esac

echo "kernel ready: $out"
