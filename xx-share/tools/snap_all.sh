#!/bin/sh
# Render every xx-share screen and montage one labeled contact sheet per
# theme and phone size into OUTDIR, plus fit runs: every screen at 320 px
# wide (sheet-fit320.png) and at large text, text-size scale 1.5 on
# 360 px (sheet-scale15.png). Exits non-zero on any snap WARNING (a
# window wider or taller than its phone screen) or any failed render.
#
#   xx-share/tools/snap_all.sh OUTDIR
#   THEMES="amoled paper forest" SIZES="450x1000 360x800" ... OUTDIR
#   SCREENS="share receive" FIT320="" SCALE15="" ... (empty skips a run)
#
# Python: $PYTHON, else ./.venv, else xx-wm/.venv, else python3 (mirrors
# scripts/check-all.sh). scripts/snap.py runs each shot on a private
# Broadway runtime dir, so parallel lanes do not steal displays.
set -u
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
OUTDIR=${1:?usage: snap_all.sh OUTDIR}
mkdir -p "$OUTDIR" "$OUTDIR/logs"
pick_python() {
    for py in "${PYTHON:-}" "$ROOT/.venv/bin/python" "$ROOT/xx-wm/.venv/bin/python"; do
        [ -n "$py" ] && [ -x "$py" ] && { echo "$py"; return; }
    done
    echo python3
}
PY=$(pick_python)
THEMES=${THEMES-"amoled paper forest"}
SIZES=${SIZES-"450x1000 360x800"}
# first / history-empty / settings-empty run on an unseeded HOME (first
# launch: no files chosen, no peers, nothing received, no favorites).
SCREENS=${SCREENS-"share first sending sent error pin history history-empty settings settings-empty receive"}
FIT320=${FIT320-$SCREENS}
SCALE15=${SCALE15-$SCREENS}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM
failed=""

render_set() {
    # $1=sheet name $2=theme $3=text-size scale $4=size $5=screens
    sheet=$1 theme=$2 scale=$3 size=$4 screens=$5
    for screen in $screens; do
        SHOME="$WORK/home-$sheet-$screen"
        rm -rf "$SHOME"
        mkdir -p "$SHOME/.config/xx-wm"
        set --
        case "$screen" in
            first | history-empty | settings-empty) ;;
            *)
                "$PY" "$ROOT/xx-share/tools/demo_seed.py" "$SHOME" >/dev/null \
                    || { failed="$failed $sheet/$screen(seed)"; continue; }
                set -- --send "$SHOME/demo-files/IMG_20240613_091522.jpg" \
                    "$SHOME/demo-files/concert-clip.mp4"
                ;;
        esac
        printf '{"theme": "%s", "text_size_scale": %s}\n' "$theme" "$scale" \
            >"$SHOME/.config/xx-wm/config.json"
        out="$WORK/$sheet-$screen.png"
        log="$WORK/$sheet-$screen.log"
        ok=""
        attempt=0
        while [ "$attempt" -lt 5 ] && [ -z "$ok" ]; do
            attempt=$((attempt + 1))
            rm -f "$out"
            HOME="$SHOME" XDG_CONFIG_HOME="$SHOME/.config" \
                XDG_DATA_HOME="$SHOME/.local/share" SCREEN="$screen" \
                "$PY" "$ROOT/scripts/snap.py" --out "$out" --size "$size" \
                --delay 1.5 --path "$ROOT/xx-share/src" --path "$ROOT/common" \
                --script "$ROOT/xx-share/tools/snap_screen.py" \
                -- "$ROOT/xx-share/src/main.py" "$@" >"$log" 2>&1
            # snap.py can exit 0 with no window while the Broadway daemon
            # is still starting, so a missing PNG retries like a failure.
            if [ -s "$out" ] && grep -q 'snap: wrote' "$log"; then
                ok=1
            else
                sleep 2
            fi
        done
        cp "$log" "$OUTDIR/logs/$sheet-$screen.log"
        if grep -q 'snap: WARNING' "$log"; then
            echo "FAIL: $sheet/$screen does not fit $size"
            grep 'snap: WARNING' "$log"
            failed="$failed $sheet/$screen(warning)"
            [ -s "$out" ] && cp "$out" "$OUTDIR/failed-$sheet-$screen.png"
            continue
        fi
        if [ -z "$ok" ]; then
            echo "FAIL: $sheet/$screen snap failed"
            grep -E 'snap: |Traceback|Error' "$log" | tail -n 5
            failed="$failed $sheet/$screen(snap)"
            continue
        fi
        echo "ok: $sheet/$screen"
    done
    args=""
    for screen in $screens; do
        [ -s "$WORK/$sheet-$screen.png" ] \
            && args="$args -label $screen $WORK/$sheet-$screen.png"
    done
    [ -n "$args" ] || return
    # shellcheck disable=SC2086
    montage $args -tile 6x -geometry +4+4 -background black \
        -fill white -pointsize 24 "$OUTDIR/sheet-$sheet.png" \
        && echo "ok: sheet-$sheet" \
        || failed="$failed sheet-$sheet(montage)"
}

for theme in $THEMES; do
    for size in $SIZES; do
        render_set "$theme-$size" "$theme" 1 "$size" "$SCREENS"
    done
done
[ -n "$FIT320" ] && render_set fit320 amoled 1 320x720 "$FIT320"
[ -n "$SCALE15" ] && render_set scale15 amoled 1.5 360x800 "$SCALE15"

if [ -n "$failed" ]; then
    echo "snap_all FAILED:$failed"
    exit 1
fi
echo "ALL SHEETS OK in $OUTDIR"
