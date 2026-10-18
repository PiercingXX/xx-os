#!/bin/sh
# Render every xx-captions screen (the settings window and every overlay
# state) and montage one labeled contact sheet per run into OUTDIR:
#   sheet-<theme>.png   each theme at SIZE (default 360x800)
#   sheet-fit320.png    every screen at 320 px wide (amoled)
#   sheet-scale15.png   every screen at 1.5x text on 360 px (amoled)
# Exits non-zero on any snap WARNING (a window wider or taller than its
# phone screen) or any failed render.
#
#   xx-captions/tools/snap_all.sh OUTDIR
#   SIZE=450x1000 THEMES="amoled paper forest" xx-captions/tools/snap_all.sh OUTDIR
#   FIT320="" SCALE15="" ... (screen lists; empty skips)
#
# The overflow menu is a Gtk.Popover (its own surface), which a window
# paint cannot capture headlessly; its rows are covered by the tests.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
APP=$(cd "$HERE/.." && pwd)
ROOT=$(cd "$APP/.." && pwd)
OUTDIR=${1:?usage: snap_all.sh OUTDIR}
THEMES=${THEMES-"amoled paper forest"}
SIZE=${SIZE:-360x800}
PY=${PYTHON:-/media/Working-Storage/GitHub/Phone-Projects/linux/xx-wm/.venv/bin/python}
[ -x "$PY" ] || PY=python3

# A screen is <window>-<state>. The first-run and download screens run
# on a bare HOME (no model, no transcripts); the rest on the demo seed.
SCREENS="settings settings-bottom settings-detail settings-vosk \
settings-downloading settings-first overlay-idle overlay-starting \
overlay-listening overlay-text overlay-error overlay-first \
overlay-downloading overlay-failed"
FIT320=${FIT320-$SCREENS}
SCALE15=${SCALE15-$SCREENS}

mkdir -p "$OUTDIR/logs"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM
failed=""

render_set() {
    # $1=sheet name $2=theme $3=text-size scale $4=size $5=screens
    sheet=$1 theme=$2 scale=$3 size=$4 screens=$5
    for screen in $screens; do
        SHOME="$WORK/home-$sheet-$screen"
        mkdir -p "$SHOME/.config/xx-wm"
        case "$screen" in
            *-first | overlay-downloading | overlay-failed) ;;
            *) "$PY" "$HERE/demo_seed.py" "$SHOME" --theme "$theme" \
                >/dev/null ;;
        esac
        printf '{"theme": "%s", "text_size_scale": %s}\n' "$theme" "$scale" \
            >"$SHOME/.config/xx-wm/config.json"
        case "$screen" in
            settings*) MODE=--settings ;;
            *) MODE=--overlay ;;
        esac
        NAV="$HERE/snaps/nav_$(printf '%s' "$screen" | tr '-' '_').py"
        SCRIPT_ARGS=""
        [ -f "$NAV" ] && SCRIPT_ARGS="--script $NAV"
        out="$WORK/$sheet-$screen.png"
        log="$WORK/$sheet-$screen.log"
        attempt=0
        while [ $attempt -lt 3 ]; do
            attempt=$((attempt + 1))
            rm -f "$out"
            # A private session bus per shot: the overlay's client never
            # reaches (or activates) a real captions service.
            # shellcheck disable=SC2086
            env HOME="$SHOME" XDG_CONFIG_HOME="$SHOME/.config" \
                XDG_DATA_HOME="$SHOME/.local/share" \
                XX_CAPTIONS_LAYER_PRELOADED=1 \
                dbus-run-session -- "$PY" "$ROOT/scripts/snap.py" \
                --out "$out" --size "$size" --delay 1.5 \
                --path "$APP/src" --path "$ROOT/common" \
                $SCRIPT_ARGS -- -m xx_captions $MODE >"$log" 2>&1
            if [ -s "$out" ] && grep -q "snap: wrote" "$log"; then
                break
            fi
            grep -q "snap: WARNING" "$log" && break
            sleep 1
        done
        cp "$log" "$OUTDIR/logs/$sheet-$screen.log"
        if grep -q "snap: WARNING" "$log"; then
            echo "FAIL: $sheet/$screen phone-fit WARNING"
            grep "snap: WARNING" "$log"
            failed="$failed $sheet/$screen(warning)"
            continue
        fi
        if [ ! -s "$out" ]; then
            echo "FAIL: $sheet/$screen snap failed"
            grep -E "snap: |Traceback|Error" "$log" | tail -n 5
            failed="$failed $sheet/$screen(snap)"
            continue
        fi
        echo "ok: $sheet/$screen"
    done
    set --
    for screen in $screens; do
        [ -s "$WORK/$sheet-$screen.png" ] \
            && set -- "$@" -label "$screen" "$WORK/$sheet-$screen.png"
    done
    if [ $# -gt 0 ]; then
        montage "$@" -tile 7x -geometry +4+4 -background black \
            -fill white -pointsize 24 "$OUTDIR/sheet-$sheet.png" \
            && echo "wrote $OUTDIR/sheet-$sheet.png"
    fi
}

for theme in $THEMES; do
    render_set "$theme" "$theme" 1 "$SIZE" "$SCREENS"
done
[ -n "$FIT320" ] && render_set fit320 amoled 1 320x720 "$FIT320"
[ -n "$SCALE15" ] && render_set scale15 amoled 1.5 360x720 "$SCALE15"

if [ -n "$failed" ]; then
    echo "snap_all: FAILED:$failed"
    exit 1
fi
echo "all sheets ok"
