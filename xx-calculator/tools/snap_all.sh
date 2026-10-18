#!/bin/sh
# Seeded screenshots of every xx-calculator state:
#   xx-calculator/tools/snap_all.sh OUTDIR
# Every portrait state (first launch, basic, result, error, science, a
# long expression, a long result, history, empty history, the overflow
# menu, licenses, a license text) at the FLX1 canvas 450x1000 and at 360x800, in
# amoled + paper; landscape 720x360 (basic/science); graphite + aura
# spot checks; fit checks at 320 px wide and at text-size-scale 1.5.
# Exits non-zero on any snap WARNING (phone-fit breach).
set -u
LANE=$(cd "$(dirname "$0")/../.." && pwd)
ROOT="$LANE/xx-calculator"
OUTDIR=${1:?usage: snap_all.sh OUTDIR}
mkdir -p "$OUTDIR"
PY=${PYTHON:-python3}
command -v "$PY" >/dev/null 2>&1 || PY=python3

THEMES="amoled paper"
LARGE=1.5
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

fail() {
    echo "snap_all: $1" >&2
    exit 1
}

snap_one() {
    # $1=theme $2=name $3=size $4=scenario $5=sheet $6=landscape $7=scale
    # $8=surface (menu|licenses|license, opened by tools/nav_surface.py)
    theme=$1 name=$2 size=$3 scenario=$4 sheet=$5 land=$6 scale=${7:-1.0}
    surface=${8:-}
    home="$WORK/home-$theme-$name"
    out="$WORK/$theme-$name.png"
    err="$WORK/$theme-$name.err"
    script=""
    [ -n "$surface" ] && script="$ROOT/tools/nav_surface.py"
    # The menu popover is its own surface: it is the shot; the window
    # capture behind it goes to a scratch file.
    shot=$out
    [ "$surface" = menu ] && shot="$WORK/$theme-$name-window.png"
    attempt=0
    while [ "$attempt" -lt 6 ]; do
        attempt=$((attempt + 1))
        rm -f "$out" "$shot"
        rm -rf "$home"
        mkdir -p "$home/.config/xx-wm" "$home/.local/share"
        printf '{"theme": "%s", "text_size_scale": %s}' "$theme" "$scale" \
            > "$home/.config/xx-wm/config.json"
        if HOME="$home" XDG_CONFIG_HOME="$home/.config" \
            XDG_DATA_HOME="$home/.local/share" \
            XX_CALC_SCENARIO="$scenario" XX_CALC_SHEET="$sheet" \
            XX_CALC_LANDSCAPE="$land" \
            CALC_SURFACE="$surface" SURFACE_OUT="$out" \
            "$PY" "$LANE/scripts/snap.py" \
            --out "$shot" --size "$size" --delay 1.2 \
            --path "$ROOT/src" --path "$LANE/common" \
            ${script:+--script "$script"} \
            -- -m xx_calculator 2>"$err"; then
            if grep -q 'snap: WARNING' "$err"; then
                cat "$err" >&2
                fail "$theme $name: phone-fit breach"
            fi
            if [ -s "$out" ]; then
                echo "shot $theme $name $size"
                return 0
            fi
        fi
        sleep 2
    done
    fail "snap failed: $theme $name ($(tail -n 3 "$err" 2>/dev/null))"
}

sheet_of() {
    # $1=sheet name, then label=file pairs
    name=$1
    shift
    args=""
    for pair in "$@"; do
        args="$args -label ${pair%%=*} ${pair#*=}"
    done
    # shellcheck disable=SC2086
    montage $args -tile 6x -geometry +4+4 -background black \
        -fill white -pointsize 28 "$OUTDIR/$name.png" \
        || fail "montage failed for $name"
}

# name:scenario:sheet:surface for every portrait state.
STATES="first:::
basic:basic::
result:result::
error:error::
science:science::
long:long::
big:big::
history:history:history:
empty:basic:history:
menu:result::menu
licenses:basic::licenses
license:basic::license"

for theme in $THEMES; do
    for spec in $STATES; do
        IFS=: read -r state scenario sheet surface <<EOF
$spec
EOF
        snap_one "$theme" "flx1-$state" "450x1000" "$scenario" "$sheet" "" 1.0 "$surface"
        snap_one "$theme" "p360-$state" "360x800" "$scenario" "$sheet" "" 1.0 "$surface"
    done
    snap_one "$theme" "landscape-basic" "720x360" "basic" "" "1"
    snap_one "$theme" "landscape-science" "720x360" "science" "" "1"
    snap_one "$theme" "landscape-big" "720x360" "big" "" "1"
done

# Theme spot checks beyond the two sheet themes.
snap_one "graphite" "spot-basic" "450x1000" "result" "" ""
snap_one "aura" "spot-science" "360x800" "science" "" ""
snap_one "forest" "spot-menu" "360x800" "result" "" "" 1.0 menu

# Fit checks: 320 px wide, and large text (text-size-scale 1.5).
snap_one "paper" "fit320-basic" "320x640" "basic" "" ""
snap_one "amoled" "fit320-science" "320x640" "science" "" ""
snap_one "amoled" "fit320-big" "320x640" "big" "" ""
snap_one "amoled" "large-basic" "450x1000" "result" "" "" "$LARGE"
snap_one "paper" "large-science" "360x800" "science" "" "" "$LARGE"
snap_one "amoled" "large-history" "360x800" "history" "history" "" "$LARGE"
snap_one "paper" "large320-long" "320x640" "long" "" "" "$LARGE"
snap_one "paper" "large320-big" "320x640" "big" "" "" "$LARGE"
snap_one "amoled" "large320-error" "320x640" "error" "" "" "$LARGE"
snap_one "amoled" "large320-licenses" "320x640" "basic" "" "" "$LARGE" licenses

for theme in $THEMES; do
    for size in flx1 p360; do
        pairs=""
        for spec in $STATES; do
            state=${spec%%:*}
            pairs="$pairs $state=$WORK/$theme-$size-$state.png"
        done
        # shellcheck disable=SC2086
        sheet_of "sheet-$theme-$size" $pairs
    done
done
montage -label amoled-basic "$WORK/amoled-landscape-basic.png" \
    -label amoled-science "$WORK/amoled-landscape-science.png" \
    -label amoled-big "$WORK/amoled-landscape-big.png" \
    -label paper-basic "$WORK/paper-landscape-basic.png" \
    -label paper-science "$WORK/paper-landscape-science.png" \
    -label paper-big "$WORK/paper-landscape-big.png" \
    -tile 3x -geometry +4+4 -background black -fill white -pointsize 28 \
    "$OUTDIR/sheet-landscape.png" || fail "montage failed for landscape"
sheet_of "sheet-checks" \
    "graphite=$WORK/graphite-spot-basic.png" \
    "aura=$WORK/aura-spot-science.png" \
    "forest-menu=$WORK/forest-spot-menu.png" \
    "320-paper=$WORK/paper-fit320-basic.png" \
    "320-science=$WORK/amoled-fit320-science.png" \
    "320-big=$WORK/amoled-fit320-big.png" \
    "large-450=$WORK/amoled-large-basic.png" \
    "large-360=$WORK/paper-large-science.png" \
    "large-hist=$WORK/amoled-large-history.png" \
    "large-320-long=$WORK/paper-large320-long.png" \
    "large-320-big=$WORK/paper-large320-big.png" \
    "large-320-error=$WORK/amoled-large320-error.png" \
    "large-320-licenses=$WORK/amoled-large320-licenses.png"
cp "$WORK/"*.png "$OUTDIR/"
rm -rf "$WORK"
trap - EXIT
echo "wrote $OUTDIR/sheet-{amoled,paper}-{flx1,p360}.png, sheet-landscape.png, sheet-checks.png"
