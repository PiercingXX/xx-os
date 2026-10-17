#!/bin/sh
# Seeded + empty screenshots of every M3 screen in four themes, plus
# 320 px and text-scale-2 fit checks:
#   xx-audiobook/tools/snap_all.sh OUTDIR [TARGET ...]
# Seeds a temp HOME per theme (tools/demo_seed.py), renders each target
# with scripts/snap.py through tools/snap_app.py (no network, no
# keyring, no player service), and montages one sheet per theme.
# Exits non-zero on any snap WARNING (phone-fit breach).
set -u
LANE=$(cd "$(dirname "$0")/../.." && pwd)
ROOT="$LANE/xx-audiobook"
OUTDIR=${1:?usage: snap_all.sh OUTDIR [TARGET ...]}
shift
mkdir -p "$OUTDIR"
PY=${PYTHON:-python3}
THEMES=${THEMES:-"amoled paper graphite aura"}
TARGETS=${*:-"library library-grid library-search book book-chapters
book-damaged conflict playing playing-restored sheet-chapters sheet-speed
sheet-sleep sheet-bookmark series author narrator collections collection"}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

fail() {
    echo "snap_all: $1" >&2
    exit 1
}

snap_one() {
    # $1=home $2=out $3=target $4=scenario $5=size
    err="$2.err"
    attempt=0
    while [ "$attempt" -lt 3 ]; do
        attempt=$((attempt + 1))
        rm -f "$2"
        if env HOME="$1" XDG_CONFIG_HOME="$1/.config" \
            DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent-snap-bus \
            SNAP_TARGET="$3" SNAP_SCENARIO="$4" \
            "$PY" "$LANE/scripts/snap.py" --out "$2" --size "$5" \
            --delay 1.4 --path "$ROOT/src" --path "$LANE/common" \
            --script "$ROOT/tools/snap_nav.py" \
            -- "$ROOT/tools/snap_app.py" 2>"$err"; then
            if grep -q 'snap: WARNING' "$err"; then
                cat "$err" >&2
                fail "$3 $5: phone-fit breach"
            fi
            [ -s "$2" ] && return 0
        fi
        sleep 1
    done
    fail "snap failed: $3 $5 ($(tail -n 3 "$err"))"
}

home_for() {
    # $1=theme $2=scenario $3=text scale
    home="$WORK/home-$1-$2-$3"
    if [ ! -d "$home" ]; then
        mkdir -p "$home/.config/xx-wm"
        printf '{"theme": "%s", "text_size_scale": %s}' "$1" "$3" \
            > "$home/.config/xx-wm/config.json"
        if [ "$2" = seeded ]; then
            "$PY" "$ROOT/tools/demo_seed.py" "$home" >/dev/null \
                || fail "seed failed"
        fi
    fi
    echo "$home"
}

for theme in $THEMES; do
    labels=""
    for target in $TARGETS; do
        out="$WORK/$theme-$target.png"
        snap_one "$(home_for "$theme" seeded 1)" "$out" "$target" seeded \
            360x720
        labels="$labels -label $target $out"
    done
    for target in library playing-restored; do
        out="$WORK/$theme-empty-$target.png"
        snap_one "$(home_for "$theme" empty 1)" "$out" "$target" empty \
            360x720
        labels="$labels -label empty-$target $out"
    done
    # shellcheck disable=SC2086
    montage $labels -tile 6x -geometry 360x720+6+6 -background '#202020' \
        -fill white -pointsize 22 "$OUTDIR/sheet-$theme.png" \
        || fail "montage failed for $theme"
    cp "$WORK/$theme-"*.png "$OUTDIR/"
done

# Fit checks: 320 px wide, and text-size-scale 2 at 360 px.
labels=""
for target in library book playing sheet-speed series; do
    out="$WORK/fit320-$target.png"
    snap_one "$(home_for amoled seeded 1)" "$out" "$target" seeded 320x720
    labels="$labels -label 320-$target $out"
done
for target in library book playing; do
    out="$WORK/scale2-$target.png"
    snap_one "$(home_for amoled seeded 2)" "$out" "$target" seeded 360x720
    labels="$labels -label scale2-$target $out"
done
# shellcheck disable=SC2086
montage $labels -tile 8x -geometry +6+6 -background '#202020' \
    -fill white -pointsize 22 "$OUTDIR/sheet-fit.png" \
    || fail "montage failed for fit"
cp "$WORK/fit320-"*.png "$WORK/scale2-"*.png "$OUTDIR/"
echo "wrote $OUTDIR/sheet-{$(echo "$THEMES" | tr ' ' ,)}.png and sheet-fit.png"
