#!/bin/sh
# Seeded screenshots of every xx-calculator state:
#   xx-calculator/tools/snap_all.sh OUTDIR
# Every portrait state (basic/result/error/science/history) at the FLX1
# canvas 450x1000 and at 360x800, in amoled + paper; landscape 720x360
# (basic/science); graphite + aura spot checks; fit checks at 320 px
# wide and at text-size-scale 1.3. Exits non-zero on any snap WARNING
# (phone-fit breach).
set -u
LANE=$(cd "$(dirname "$0")/../.." && pwd)
ROOT="$LANE/xx-calculator"
OUTDIR=${1:?usage: snap_all.sh OUTDIR}
mkdir -p "$OUTDIR"
PY=${PYTHON:-python3}
command -v "$PY" >/dev/null 2>&1 || PY=python3

THEMES="amoled paper"
STATES="basic result error science history"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

fail() {
    echo "snap_all: $1" >&2
    exit 1
}

snap_one() {
    # $1=theme $2=name $3=size $4=scenario $5=sheet $6=landscape $7=scale
    theme=$1 name=$2 size=$3 scenario=$4 sheet=$5 land=$6 scale=${7:-1.0}
    home="$WORK/home-$theme-$name"
    mkdir -p "$home/.config/xx-wm" "$home/.local/share"
    printf '{"theme": "%s", "text_size_scale": %s}' "$theme" "$scale" \
        > "$home/.config/xx-wm/config.json"
    out="$WORK/$theme-$name.png"
    err="$WORK/$theme-$name.err"
    attempt=0
    while [ "$attempt" -lt 3 ]; do
        attempt=$((attempt + 1))
        rm -f "$out"
        if HOME="$home" XDG_CONFIG_HOME="$home/.config" \
            XDG_DATA_HOME="$home/.local/share" \
            XX_CALC_SCENARIO="$scenario" XX_CALC_SHEET="$sheet" \
            XX_CALC_LANDSCAPE="$land" \
            "$PY" "$LANE/scripts/snap.py" \
            --out "$out" --size "$size" --delay 1.2 \
            --path "$ROOT/src" --path "$LANE/common" \
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
        sleep 1
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
    montage $args -tile 5x -geometry +4+4 -background black \
        -fill white -pointsize 28 "$OUTDIR/$name.png" \
        || fail "montage failed for $name"
}

for theme in $THEMES; do
    for state in $STATES; do
        sheet=""
        [ "$state" = history ] && sheet=history
        snap_one "$theme" "flx1-$state" "450x1000" "$state" "$sheet" ""
        snap_one "$theme" "p360-$state" "360x800" "$state" "$sheet" ""
    done
    snap_one "$theme" "landscape-basic" "720x360" "basic" "" "1"
    snap_one "$theme" "landscape-science" "720x360" "science" "" "1"
done

# Theme spot checks beyond the two sheet themes.
snap_one "graphite" "spot-basic" "450x1000" "result" "" ""
snap_one "aura" "spot-science" "360x800" "science" "" ""

# Fit checks: 320 px wide, and large text (text-size-scale 1.3).
snap_one "paper" "fit320-basic" "320x640" "basic" "" ""
snap_one "amoled" "fit320-science" "320x640" "science" "" ""
snap_one "amoled" "large-basic" "450x1000" "result" "" "" "1.3"
snap_one "paper" "large-science" "360x800" "science" "" "" "1.3"
snap_one "amoled" "large-history" "360x800" "history" "history" "" "1.3"

for theme in $THEMES; do
    for size in flx1 p360; do
        sheet_of "sheet-$theme-$size" \
            "basic=$WORK/$theme-$size-basic.png" \
            "result=$WORK/$theme-$size-result.png" \
            "error=$WORK/$theme-$size-error.png" \
            "science=$WORK/$theme-$size-science.png" \
            "history=$WORK/$theme-$size-history.png"
    done
done
montage -label amoled-basic "$WORK/amoled-landscape-basic.png" \
    -label amoled-science "$WORK/amoled-landscape-science.png" \
    -label paper-basic "$WORK/paper-landscape-basic.png" \
    -label paper-science "$WORK/paper-landscape-science.png" \
    -tile 2x -geometry +4+4 -background black -fill white -pointsize 28 \
    "$OUTDIR/sheet-landscape.png" || fail "montage failed for landscape"
sheet_of "sheet-checks" \
    "graphite=$WORK/graphite-spot-basic.png" \
    "aura=$WORK/aura-spot-science.png" \
    "320-paper=$WORK/paper-fit320-basic.png" \
    "320-science=$WORK/amoled-fit320-science.png" \
    "large-450=$WORK/amoled-large-basic.png" \
    "large-360=$WORK/paper-large-science.png" \
    "large-hist=$WORK/amoled-large-history.png"
cp "$WORK/"*.png "$OUTDIR/"
rm -rf "$WORK"
trap - EXIT
echo "wrote $OUTDIR/sheet-{amoled,paper}-{flx1,p360}.png, sheet-landscape.png, sheet-checks.png"
