#!/bin/sh
# Render EVERY xx-calendar screen and montage one labeled contact sheet per
# set into OUTDIR: each theme at the FLX1 canvas (450x1000) and a 360x800
# phone, plus fit checks: every screen 320 px wide (sheet-fit320.png) and
# at large text, text-size scale 1.5 on 360x800 (sheet-scale15.png).
# Exits non-zero on any snap WARNING (a window wider or taller than its
# phone screen) or any failed render.
#
#   xx-calendar/tools/snap_all.sh OUTDIR
#   THEMES="amoled paper forest" SIZES="450x1000 360x800" ... OUTDIR
#   FIT320="schedule month" SCALE15="" ... (screen lists; empty skips)
#   JOBS=3 renders that many sets at once (each shot has its own display).
#
# Each shot runs on its own session bus (the app is single-instance).
# Navigation scripts live in tools/snaps/nav_<screen>.py.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
LANE=$(cd "$ROOT/.." && pwd)
OUTDIR=${1:?usage: snap_all.sh OUTDIR}
THEMES=${THEMES-"amoled paper forest"}
SIZES=${SIZES-"450x1000 360x800"}
JOBS=${JOBS:-3}
PY=${PYTHON:-/media/Working-Storage/GitHub/Phone-Projects/linux/xx-wm/.venv/bin/python}
[ -x "$PY" ] || PY=python3

SCREENS="schedule month month-picked month-weeks day detail detail-repeat
detail-allday editor-new
editor-edit editor-repeat editor-error calendars search search-blank
settings import export empty empty-month"
SCREENS=$(echo $SCREENS)
FIT320=${FIT320-$SCREENS}
SCALE15=${SCALE15-$SCREENS}

mkdir -p "$OUTDIR/logs"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM
SAMPLE_ICS="$WORK/sample.ics"
printf 'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//X//EN\r\nBEGIN:VEVENT\r\nUID:sample-1\r\nDTSTAMP:20260101T000000Z\r\nDTSTART:20260820T100000Z\r\nDTEND:20260820T110000Z\r\nSUMMARY:Sample import\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n' > "$SAMPLE_ICS"

render_set() {
    # $1=sheet $2=theme $3=text-size scale $4=size $5=screens
    sheet=$1 theme=$2 scale=$3 size=$4 screens=$5
    status="$WORK/status-$sheet"
    : > "$status"
    for screen in $screens; do
        SHOME="$WORK/home-$sheet-$screen"
        mkdir -p "$SHOME/.config/xx-wm" "$SHOME/.local/share/xx-calendar"
        case "$screen" in
            empty | empty-month) ;;
            *) PYTHONPATH="$ROOT/src" "$PY" "$ROOT/tools/demo_seed.py" \
                "$SHOME" >/dev/null 2>&1 ;;
        esac
        printf '{"theme": "%s", "text_size_scale": %s}\n' "$theme" "$scale" \
            > "$SHOME/.config/xx-wm/config.json"
        nav="$ROOT/tools/snaps/nav_$(printf '%s' "$screen" | tr '-' '_').py"
        out="$WORK/$sheet-$screen.png"
        log="$WORK/$sheet-$screen.log"
        attempt=0
        ok=0
        while [ $attempt -lt 3 ]; do
            attempt=$((attempt + 1))
            rm -f "$out"
            # Own session bus per shot: the app is single-instance, so a
            # parallel shot (or a calendar on the user bus) would swallow
            # the launch and this one would exit drawing nothing.
            dbus-run-session -- env GDK_DEBUG=no-portals HOME="$SHOME" \
                XDG_CONFIG_HOME="$SHOME/.config" \
                XDG_DATA_HOME="$SHOME/.local/share" \
                SNAP_SAMPLE_ICS="$SAMPLE_ICS" \
                "$PY" "$LANE/scripts/snap.py" \
                --out "$out" --size "$size" --delay 1.5 \
                --path "$ROOT/src" --path "$LANE/common" \
                --script "$nav" -- -m xx_calendar >"$log" 2>&1
            if grep -q "snap: WARNING" "$log"; then
                break
            fi
            if [ -s "$out" ] && grep -q "snap: wrote" "$log"; then
                ok=1
                break
            fi
            sleep 1
        done
        cp "$log" "$OUTDIR/logs/$sheet-$screen.log"
        if grep -q "snap: WARNING" "$log"; then
            echo "FAIL: $sheet/$screen phone-fit WARNING"
            grep "snap: WARNING" "$log"
            echo "$screen" >> "$status"
            [ -s "$out" ] && cp "$out" "$OUTDIR/failed-$sheet-$screen.png"
            continue
        fi
        if [ $ok -ne 1 ]; then
            echo "FAIL: $sheet/$screen snap failed"
            grep -E "snap: |Traceback|Error" "$log" | tail -n 5
            echo "$screen" >> "$status"
            continue
        fi
        echo "ok: $sheet/$screen"
    done
    set --
    for screen in $screens; do
        [ -s "$WORK/$sheet-$screen.png" ] || continue
        set -- "$@" -label "$screen" "$WORK/$sheet-$screen.png"
    done
    if [ $# -gt 0 ]; then
        montage "$@" -tile 7x -geometry +4+4 -background black \
            -fill white -pointsize 22 "$OUTDIR/sheet-$sheet.png" \
            && echo "ok: sheet-$sheet" \
            || echo "montage" >> "$status"
    fi
}

running=0
SETS=""
launch() {
    render_set "$@" &
    SETS="$SETS $1"
    running=$((running + 1))
    if [ "$running" -ge "$JOBS" ]; then
        wait
        running=0
    fi
}
for theme in $THEMES; do
    for size in $SIZES; do
        launch "$theme-${size%%x*}" "$theme" 1 "$size" "$SCREENS"
    done
done
[ -n "$FIT320" ] && launch fit320 amoled 1 320x720 "$FIT320"
[ -n "$SCALE15" ] && launch scale15 amoled 1.5 360x800 "$SCALE15"
wait

failed=""
for sheet in $SETS; do
    if [ -s "$WORK/status-$sheet" ]; then
        failed="$failed $sheet($(tr '\n' ' ' < "$WORK/status-$sheet"))"
    fi
done
if [ -n "$failed" ]; then
    echo "FAILED:$failed"
    exit 1
fi
echo "ALL SHEETS OK in $OUTDIR"
