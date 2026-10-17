#!/bin/sh
# Seeded screenshots of every xx-calculator state in amoled + paper:
#   xx-calculator/tools/snap_all.sh OUTDIR
# Portrait 360x720 (basic/result/error/science/history), landscape
# 720x360 (basic/science), plus phone-fit spots at 600x1333, 360x800
# and 320x640. Exits non-zero on any snap WARNING (phone-fit breach).
set -u
LANE=$(cd "$(dirname "$0")/../.." && pwd)
ROOT="$LANE/xx-calculator"
OUTDIR=${1:?usage: snap_all.sh OUTDIR}
mkdir -p "$OUTDIR"
PY=${PYTHON:-python3}
command -v "$PY" >/dev/null 2>&1 || PY=python3

THEMES="amoled paper"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

fail() {
    echo "snap_all: $1" >&2
    exit 1
}

snap_one() {
    # $1=theme $2=name $3=size $4=scenario $5=sheet $6=landscape
    theme=$1 name=$2 size=$3 scenario=$4 sheet=$5 land=$6
    home="$WORK/home-$theme"
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

for theme in $THEMES; do
    home="$WORK/home-$theme"
    mkdir -p "$home/.config/xx-wm" "$home/.local/share"
    printf '{"theme": "%s"}' "$theme" > "$home/.config/xx-wm/config.json"
done

for theme in $THEMES; do
    snap_one "$theme" "portrait-basic" "360x720" "basic" "" ""
    snap_one "$theme" "portrait-result" "360x720" "result" "" ""
    snap_one "$theme" "portrait-error" "360x720" "error" "" ""
    snap_one "$theme" "portrait-science" "360x720" "science" "" ""
    snap_one "$theme" "portrait-history" "360x720" "history" "history" ""
    snap_one "$theme" "landscape-basic" "720x360" "basic" "" "1"
    snap_one "$theme" "landscape-science" "720x360" "science" "" "1"
    snap_one "$theme" "flx1-basic" "600x1333" "basic" "" ""
    snap_one "$theme" "tall-basic" "360x800" "basic" "" ""
    snap_one "$theme" "small-basic" "320x640" "basic" "" ""
done

for theme in $THEMES; do
    # shellcheck disable=SC2086
    montage -label 'basic' "$WORK/$theme-portrait-basic.png" \
        -label 'result' "$WORK/$theme-portrait-result.png" \
        -label 'error' "$WORK/$theme-portrait-error.png" \
        -label 'science' "$WORK/$theme-portrait-science.png" \
        -label 'history' "$WORK/$theme-portrait-history.png" \
        -tile 3x2 -geometry +4+4 -background black \
        -fill white -pointsize 28 "$OUTDIR/sheet-$theme-portrait.png" \
        || fail "montage failed for $theme portrait"
    montage -label 'basic' "$WORK/$theme-landscape-basic.png" \
        -label 'science' "$WORK/$theme-landscape-science.png" \
        -label 'flx1' "$WORK/$theme-flx1-basic.png" \
        -label 'tall' "$WORK/$theme-tall-basic.png" \
        -label 'small' "$WORK/$theme-small-basic.png" \
        -tile 3x2 -geometry +4+4 -background black \
        -fill white -pointsize 28 "$OUTDIR/sheet-$theme-sizes.png" \
        || fail "montage failed for $theme sizes"
    cp "$WORK/$theme-"*.png "$OUTDIR/"
done
rm -rf "$WORK"
trap - EXIT
echo "wrote $OUTDIR/sheet-{amoled,paper}-{portrait,sizes}.png"
