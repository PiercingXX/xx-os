#!/bin/sh
# Fetch a pinned kernel source tree for the Pixel 9 (tokay) bring-up.
#
#   fetch-kernel.sh grapheneos   downstream 6.1, GrapheneOS's tree (default)
#   fetch-kernel.sh aosp         downstream 6.1, Google's repo manifest
#   fetch-kernel.sh mainline     community mainline tree with a tokay DT
#
# Sources land in $XXOS_PIXEL_WORK/src/<track>. Re-running checks the pin
# instead of fetching again. Network only; never touches a phone.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/config.sh"

track=${1:-grapheneos}
dest=$XXOS_PIXEL_WORK/src/$track
mkdir -p "$XXOS_PIXEL_WORK/src"

# Clone one commit of a git repository, shallow, and check it is the pin.
fetch_pinned_git() {
    url=$1 ref=$2 dir=$3
    if [ ! -d "$dir/.git" ]; then
        git init -q "$dir"
        git -C "$dir" remote add origin "$url"
    fi
    git -C "$dir" fetch --depth=1 origin "$ref"
    git -C "$dir" -c advice.detachedHead=false checkout -q FETCH_HEAD
    got=$(git -C "$dir" rev-parse HEAD)
    [ "$got" = "$ref" ] || die "$dir is at $got, expected $ref"
}

case $track in
grapheneos)
    need git
    fetch_pinned_git "$GOS_KERNEL_URL" "$GOS_KERNEL_REF" "$dest"
    # Submodules (common kernel, Google modules, prebuilt toolchains) are
    # pinned by the superproject commit.
    git -C "$dest" submodule update --init --recursive --depth=1 --jobs=8
    ;;
aosp)
    need git repo
    mkdir -p "$dest"
    cd "$dest"
    if [ ! -d .repo ]; then
        repo init --depth=1 -u "$AOSP_MANIFEST_URL" -b "$AOSP_MANIFEST_BRANCH"
    fi
    got=$(git -C .repo/manifests rev-parse HEAD)
    if [ "$got" != "$AOSP_MANIFEST_REF" ]; then
        echo "warning: manifest moved: $got (pinned $AOSP_MANIFEST_REF)" >&2
    fi
    repo sync -c --no-tags --no-clone-bundle -j8
    # The manifest names branches, not commits. Record what was synced so
    # the build can be repeated exactly.
    repo manifest -r -o "$XXOS_PIXEL_WORK/src/aosp-pinned-manifest.xml"
    echo "pinned manifest: $XXOS_PIXEL_WORK/src/aosp-pinned-manifest.xml"
    ;;
mainline)
    need git
    fetch_pinned_git "$MAINLINE_URL" "$MAINLINE_REF" "$dest"
    ;;
*)
    die "unknown track '$track' (grapheneos, aosp or mainline)"
    ;;
esac

echo "source ready: $dest"
