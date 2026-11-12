#!/bin/sh
# Build the river compositor xx-os carries: the pinned upstream commit with
# every patch in patches/ applied in name order.
#
#   sh build.sh              fetch, verify, patch and build into $RIVER_BUILD_DIR/stage
#   sh build.sh --install    the same, then copy the staged tree to / (sudo)
#
# Needs Zig 0.16 (ZIG=/path/to/zig, else zig on PATH), git, pkg-config and
# the development files of wlroots 0.20, wayland, wayland-protocols,
# xkbcommon, libinput, libevdev and pixman. Zig fetches river's Zig
# dependencies itself and checks each against the hash in build.zig.zon.
#   RIVER_BUILD_DIR  work directory (default: ./build next to this script)
#   PREFIX           install prefix (default: /usr/local)
#   RIVER_OPTIMIZE   Zig optimize mode (default: ReleaseSafe)
set -eu

RIVER_URL=https://codeberg.org/river/river.git
RIVER_TAG=v0.5.0
RIVER_COMMIT=cc4a291b2b9375fe3d5fe5a64b6f510b463f888b
ZIG_SERIES=0.16.

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
work=${RIVER_BUILD_DIR:-$here/build}
prefix=${PREFIX:-/usr/local}
optimize=${RIVER_OPTIMIZE:-ReleaseSafe}
zig=${ZIG:-zig}

install=0
for arg in "$@"; do
    case $arg in
        --install) install=1 ;;
        -h|--help)
            sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "unknown argument: $arg" >&2
            exit 1
            ;;
    esac
done

zig_version=$("$zig" version 2>/dev/null) || {
    echo "zig not found (set ZIG=/path/to/zig); river $RIVER_TAG needs Zig ${ZIG_SERIES}x" >&2
    exit 1
}
case $zig_version in
    "$ZIG_SERIES"*) ;;
    *)
        echo "Zig $zig_version found; river $RIVER_TAG needs Zig ${ZIG_SERIES}x" >&2
        exit 1
        ;;
esac
pkg-config --exists wlroots-0.20 || {
    echo "wlroots 0.20 development files not found (Debian: libwlroots-0.20-dev)" >&2
    exit 1
}

src=$work/river
stage=$work/stage
mkdir -p "$work"
rm -rf "$src" "$stage"
git init -q "$src"
git -C "$src" fetch -q --depth 1 "$RIVER_URL" "$RIVER_COMMIT"
git -C "$src" -c advice.detachedHead=false checkout -q FETCH_HEAD
head=$(git -C "$src" rev-parse HEAD)
if [ "$head" != "$RIVER_COMMIT" ]; then
    echo "river checkout is $head, expected $RIVER_COMMIT ($RIVER_TAG)" >&2
    exit 1
fi
echo "river $RIVER_TAG at $RIVER_COMMIT"

for patch in "$here"/patches/*.patch; do
    [ -e "$patch" ] || continue
    echo "applying $(basename "$patch")"
    git -C "$src" apply --whitespace=nowarn "$patch"
done

echo "building with Zig $zig_version ($optimize), prefix $prefix"
(
    cd "$src"
    DESTDIR=$stage "$zig" build \
        -Doptimize="$optimize" \
        -Dpie \
        -Dman-pages=false \
        --prefix "$prefix" \
        --search-prefix /usr \
        install
)
"$stage$prefix/bin/river" -version
echo "staged in $stage"

if [ "$install" = 1 ]; then
    if [ "$(id -u)" = 0 ]; then
        sudo=
    elif command -v sudo >/dev/null 2>&1; then
        sudo=sudo
    else
        sudo=doas
    fi
    $sudo cp -R "$stage/." /
    echo "installed to $prefix"
fi
