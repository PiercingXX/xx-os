#!/bin/sh
# Render every xx-email screen in every theme and montage one labeled
# contact sheet per theme into OUTDIR, plus fit checks: every screen at
# 320 px wide (xx-email-fit320.png), at large text, text-size scale 1.5
# on 360 px (xx-email-scale15.png), and both together
# (xx-email-fit320x15.png).
#
#   xx-email/tools/snap_all.sh OUTDIR
#
# BEFORE=1 renders only the screens the pre-polish code can show
# (amoled still loops all four themes; pick per invocation).
# THEMES="amoled" BEFORE=1 .../snap_all.sh lane_shots/before
# Env: SIZE (per-theme sheet size, default 360x720), FIT320, SCALE15,
# FIT320X15 (screen lists; empty skips that check).
#
# Exits non-zero on any snap WARNING (a window wider or taller than its
# phone screen) or any failed render.
set -u
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
OUTDIR=${1:?usage: snap_all.sh OUTDIR}
THEMES=${THEMES:-"amoled paper forest aura"}
if [ "${BEFORE:-0}" = "1" ]; then
    SCREENS="setup inbox thread compose search"
else
    SCREENS="setup inbox thread compose folders search settings"
fi
SIZE=${SIZE:-360x720}
FIT320=${FIT320-$SCREENS}
SCALE15=${SCALE15-$SCREENS}
FIT320X15=${FIT320X15-$SCREENS}
mkdir -p "$OUTDIR"
PY=${PYTHON:-/media/Working-Storage/GitHub/Phone-Projects/linux/xx-wm/.venv/bin/python}
[ -x "$PY" ] || PY=python3
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
failed=""

render_set() {
    # $1=sheet name $2=theme $3=text-size scale $4=size $5=screens
    sheet=$1 theme=$2 scale=$3 size=$4 screens=$5
    args=""
    for screen in $screens; do
        hd="$WORK/home-$sheet-$screen"
        mkdir -p "$hd/run"
        if [ "$screen" = "setup" ]; then
            "$PY" "$ROOT/xx-email/tools/demo_seed.py" "$hd" --empty \
                >/dev/null || failed="$failed seed-$sheet-$screen"
        else
            "$PY" "$ROOT/xx-email/tools/demo_seed.py" "$hd" \
                >/dev/null || failed="$failed seed-$sheet-$screen"
        fi
        "$PY" -c "
import json
path = '$hd/.config/xx-wm/config.json'
with open(path, encoding='utf-8') as fh:
    data = json.load(fh)
data['theme'] = '$theme'
data['text_size_scale'] = $scale
with open(path, 'w', encoding='utf-8') as fh:
    json.dump(data, fh)
"
        shot="$WORK/$sheet-$screen.png"
        err="$WORK/snap-$sheet-$screen.err"
        # Success is a fresh PNG on disk: the runner can exit 0
        # while the app died at Gtk init (broadway boot race), so
        # retry on a missing shot, not on the exit status.
        rm -f "$shot"
        shot_ok=0
        for attempt in 1 2 3 4 5; do
            HOME="$hd" XDG_CONFIG_HOME="$hd/.config" \
                XDG_DATA_HOME="$hd/.local/share" \
                XDG_CACHE_HOME="$hd/.cache" \
                XDG_STATE_HOME="$hd/.local/state" \
                XDG_RUNTIME_DIR="$hd/run" \
                "$PY" "$ROOT/scripts/snap.py" \
                --out "$shot" --size "$size" --delay 4.0 \
                --path "$ROOT/common" --path "$ROOT/xx-email/src" \
                --script "$ROOT/xx-email/tools/snap_nav/$screen.py" \
                -- -m xx_email 2>"$err" || true
            if [ -s "$shot" ]; then
                shot_ok=1
                break
            fi
            echo "snap_all: retrying $sheet/$screen" \
                "attempt $attempt"
            sleep 2
        done
        [ "$shot_ok" = "1" ] || failed="$failed snap-$sheet-$screen"
        if grep -q 'snap: WARNING' "$err" 2>/dev/null; then
            echo "snap_all: $sheet/$screen does not fit $size"
            cat "$err"
            failed="$failed size-$sheet-$screen"
            [ -s "$shot" ] && cp "$shot" "$OUTDIR/failed-$sheet-$screen.png"
        fi
        [ -s "$shot" ] && args="$args -label $screen $shot"
    done
    # shellcheck disable=SC2086
    montage $args -tile 4x2 -geometry +4+4 -background black \
        -fill white -pointsize 24 "$OUTDIR/xx-email-$sheet.png" \
        || failed="$failed montage-$sheet"
    echo "wrote $OUTDIR/xx-email-$sheet.png"
}

for theme in $THEMES; do
    render_set "$theme" "$theme" 1 "$SIZE" "$SCREENS"
done
[ -n "$FIT320" ] && render_set fit320 amoled 1 320x720 "$FIT320"
[ -n "$SCALE15" ] && render_set scale15 amoled 1.5 360x720 "$SCALE15"
[ -n "$FIT320X15" ] && render_set fit320x15 amoled 1.5 320x720 "$FIT320X15"

if [ -n "$failed" ]; then
    echo "FAILED:$failed"
    exit 1
fi
