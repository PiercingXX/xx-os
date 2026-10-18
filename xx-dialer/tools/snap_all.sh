#!/bin/sh
# Render every xx-dialer screen in every theme and montage one labeled
# contact sheet per theme into OUTDIR, plus fit checks: every screen at
# 320 px wide (fit320.png) and at large text, text-size scale 1.5 on
# 360 px (scale15.png).
#
# Usage: sh tools/snap_all.sh OUTDIR
# Env overrides: THEMES="amoled paper forest aura"
#                SCREENS="recents missed keypad people menu rules setup backup logs"
#                FIT320="..." SCALE15="..." (screen lists; empty skips)
#
# Each run seeds a fresh temp HOME with tools/demo_seed.py, writes the
# theme and text scale into its xx-wm/config.json, then renders every
# screen with scripts/snap.py. Exits non-zero on any snap WARNING (a
# window wider or taller than its phone screen: the phone-fit gate).
set -u
TOOLS=$(cd "$(dirname "$0")" && pwd)
APP=$(cd "$TOOLS/.." && pwd)
ROOT=$(cd "$APP/.." && pwd)
OUTDIR=${1:?usage: snap_all.sh OUTDIR}
THEMES=${THEMES-"amoled paper forest aura"}
SCREENS=${SCREENS:-"recents missed keypad people menu rules rules_low setup backup logs"}
FIT320=${FIT320-$SCREENS}
SCALE15=${SCALE15-$SCREENS}

if [ -n "${PYTHON:-}" ]; then
    PY="$PYTHON"
elif [ -x "$ROOT/.venv/bin/python" ]; then
    PY="$ROOT/.venv/bin/python"
else
    PY="python3"
fi

mkdir -p "$OUTDIR"
RAW="$OUTDIR/raw"
mkdir -p "$RAW"
LOG="$OUTDIR/snap.log"
: > "$LOG"
failed=""

render_set() {
    # $1=sheet name $2=theme $3=text-size scale $4=size $5=screens
    sheet=$1 theme=$2 scale=$3 size=$4 screens=$5
    SEED=$(mktemp -d)
    "$PY" "$TOOLS/demo_seed.py" "$SEED" >> "$LOG" 2>&1 \
        || { echo "seed failed for $sheet" >&2; failed="$failed seed:$sheet"; }
    mkdir -p "$SEED/.config/xx-wm"
    printf '{"theme":"%s","text_size_scale":%s}\n' "$theme" "$scale" \
        > "$SEED/.config/xx-wm/config.json"
    for screen in $screens; do
        out="$RAW/${sheet}-${screen}.png"
        shotlog="$RAW/${sheet}-${screen}.log"
        # rules_low scrolls a long page to its end: the extra delay lets
        # layout and the scroll settle before the snapshot fires.
        delay=1.0
        if [ "$screen" = "rules_low" ]; then
            delay=3.0
        fi
        HOME="$SEED" XDG_CONFIG_HOME="$SEED/.config" "$PY" \
            "$ROOT/scripts/snap.py" --out "$out" --size "$size" \
            --delay "$delay" --path "$APP/src" --path "$ROOT/common" \
            --script "$TOOLS/snap_nav/${screen}.py" \
            -- -m xx_dialer > "$shotlog" 2>&1
        rc=$?
        cat "$shotlog" >> "$LOG"
        if [ $rc -ne 0 ]; then
            echo "snap FAILED ($rc): $sheet $screen (see $shotlog)" >&2
            failed="$failed snap:$sheet:$screen"
        fi
        if grep -q "snap: WARNING" "$shotlog"; then
            echo "snap WARNING: $sheet $screen (see $shotlog)" >&2
            failed="$failed fit:$sheet:$screen"
        fi
    done
    rm -rf "$SEED"
    if command -v montage >/dev/null 2>&1; then
        # shellcheck disable=SC2086
        montage -label '%f' "$RAW/${sheet}-"*.png -tile 5x \
            -geometry +6+6 -background '#888888' -fill '#ffffff' \
            -pointsize 20 "$OUTDIR/${sheet}.png" >> "$LOG" 2>&1 \
            || { echo "montage failed: $sheet" >&2; failed="$failed montage:$sheet"; }
        echo "sheet: $OUTDIR/${sheet}.png"
    else
        echo "montage missing: raws left in $RAW" >&2
        failed="$failed montage:missing"
    fi
}

for theme in $THEMES; do
    render_set "$theme" "$theme" 1 360x720 "$SCREENS"
done
[ -n "$FIT320" ] && render_set fit320 amoled 1 320x720 "$FIT320"
[ -n "$SCALE15" ] && render_set scale15 amoled 1.5 360x720 "$SCALE15"

if [ -n "$failed" ]; then
    echo "FAILURES:$failed" >&2
    exit 1
fi
echo "sheets in $OUTDIR"
