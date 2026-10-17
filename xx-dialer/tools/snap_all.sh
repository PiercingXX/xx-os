#!/bin/sh
# Render every xx-dialer screen in every theme and montage one labeled
# contact sheet per theme into OUTDIR.
#
# Usage: sh tools/snap_all.sh OUTDIR
# Env overrides: THEMES="amoled paper forest aura"
#                SCREENS="recents missed keypad people menu rules setup backup logs"
#
# Each run seeds a fresh temp HOME with tools/demo_seed.py, writes the
# theme into its xx-wm/config.json, then renders every screen with
# scripts/snap.py. Exits non-zero on any snap WARNING (phone-fit gate).
set -u
TOOLS=$(cd "$(dirname "$0")" && pwd)
APP=$(cd "$TOOLS/.." && pwd)
ROOT=$(cd "$APP/.." && pwd)
OUTDIR=${1:?usage: snap_all.sh OUTDIR}
THEMES=${THEMES:-"amoled paper forest aura"}
SCREENS=${SCREENS:-"recents missed keypad people menu rules rules_low setup backup logs"}

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

for theme in $THEMES; do
    SEED=$(mktemp -d)
    "$PY" "$TOOLS/demo_seed.py" "$SEED" >> "$LOG" 2>&1 \
        || { echo "seed failed for $theme" >&2; failed="$failed seed:$theme"; }
    mkdir -p "$SEED/.config/xx-wm"
    printf '{"theme":"%s"}\n' "$theme" > "$SEED/.config/xx-wm/config.json"
    for screen in $SCREENS; do
        out="$RAW/${theme}-${screen}.png"
        shotlog="$RAW/${theme}-${screen}.log"
        # rules_low scrolls a long page to its end: the extra delay lets
        # layout and the scroll settle before the snapshot fires.
        delay=1.0
        if [ "$screen" = "rules_low" ]; then
            delay=3.0
        fi
        HOME="$SEED" XDG_CONFIG_HOME="$SEED/.config" "$PY" \
            "$ROOT/scripts/snap.py" --out "$out" --delay "$delay" \
            --path "$APP/src" --path "$ROOT/common" \
            --script "$TOOLS/snap_nav/${screen}.py" \
            -- -m xx_dialer > "$shotlog" 2>&1
        rc=$?
        cat "$shotlog" >> "$LOG"
        if [ $rc -ne 0 ]; then
            echo "snap FAILED ($rc): $theme $screen (see $shotlog)" >&2
            failed="$failed snap:$theme:$screen"
        fi
        if grep -q "snap: WARNING" "$shotlog"; then
            echo "snap WARNING: $theme $screen (see $shotlog)" >&2
            failed="$failed fit:$theme:$screen"
        fi
    done
    rm -rf "$SEED"
    if command -v montage >/dev/null 2>&1; then
        # shellcheck disable=SC2086
        montage -label '%f' "$RAW/${theme}-"*.png -tile 2x5 \
            -geometry +6+6 -background '#888888' -fill '#ffffff' \
            -pointsize 20 "$OUTDIR/${theme}.png" >> "$LOG" 2>&1 \
            || { echo "montage failed: $theme" >&2; failed="$failed montage:$theme"; }
        echo "sheet: $OUTDIR/${theme}.png"
    else
        echo "montage missing: raws left in $RAW" >&2
        failed="$failed montage:missing"
    fi
done

if [ -n "$failed" ]; then
    echo "FAILURES:$failed" >&2
    exit 1
fi
echo "sheets in $OUTDIR"
