#!/bin/sh
# Render EVERY xx-contacts screen in each theme and montage one labeled
# contact sheet per theme into OUTDIR, plus fit checks: every screen at
# 320 px wide (sheet-fit320.png) and at large text, text-size scale 1.5
# on 360 px (sheet-scale15.png). Exits non-zero on any snap WARNING (a
# window wider or taller than its phone screen) or any failed render.
#
#   xx-contacts/tools/snap_all.sh OUTDIR
#   THEMES="amoled paper forest aura" xx-contacts/tools/snap_all.sh OUTDIR
#   BEFORE=1 THEMES=amoled xx-contacts/tools/snap_all.sh lane_shots/before
#   FIT320="list detail" SCALE15="" ... (screen lists; empty skips)
#
# BEFORE=1 renders the current-code screen set (no settings/starred shots:
# those pages do not exist before the restyle).
set -u
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
OUTDIR=${1:?usage: snap_all.sh OUTDIR}
THEMES=${THEMES-"amoled paper forest aura"}
BEFORE=${BEFORE:-0}

APP=xx-contacts
if [ "$BEFORE" = 1 ]; then
    SCREENS="list search detail editor-new editor-existing import-export me pick menu empty"
else
    SCREENS="list search starred detail editor-new editor-existing settings import-export me pick menu empty"
fi
FIT320=${FIT320-$SCREENS}
SCALE15=${SCALE15-$SCREENS}
# NOTE: the delete confirm and the import merge-plan confirm are
# Adw.AlertDialogs, which are not Gtk.Window toplevels and cannot be
# captured by scripts/snap.py headlessly; they are exercised by the
# dialog tests and described in the lane report instead.

mkdir -p "$OUTDIR" "$OUTDIR/logs"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM

failed=""
render_set() {
    # $1=sheet name $2=theme $3=text-size scale $4=size $5=screens
    sheet=$1 theme=$2 scale=$3 size=$4 screens=$5
    for screen in $screens; do
        SHOME="$WORK/home-$sheet-$screen"
        mkdir -p "$SHOME"
        if [ "$screen" != "empty" ]; then
            "$ROOT/$APP/tools/demo_seed.py" "$SHOME" >/dev/null
        fi
        mkdir -p "$SHOME/.config/xx-wm"
        printf '{"theme": "%s", "text_size_scale": %s}\n' "$theme" "$scale" \
            >"$SHOME/.config/xx-wm/config.json"
        NAVNAME=$(printf '%s' "$screen" | tr '-' '_')
        case "$screen" in
            list | empty) NAV="" ;;
            *) NAV="$ROOT/$APP/tools/snaps/nav_${NAVNAME}.py" ;;
        esac
        # nav_pick.py and nav_delete.py/menu hide the main window and resize
        # the dialog; every other shot must already be the phone size.
        out="$WORK/$sheet-$screen.png"
        log="$WORK/$sheet-$screen.log"
        if [ -n "$NAV" ]; then
            SCRIPT_ARGS="--script $NAV"
        else
            SCRIPT_ARGS=""
        fi
        attempt=0
        rc=1
        while [ $attempt -lt 2 ]; do
            attempt=$((attempt + 1))
            # shellcheck disable=SC2086
            if env HOME="$SHOME" XDG_CONFIG_HOME="$SHOME/.config" \
                XDG_DATA_HOME="$SHOME/.local/share" SEED_HOME="$SHOME" \
                "${PYTHON:-$ROOT/.venv/bin/python}" "$ROOT/scripts/snap.py" \
                --out "$out" --size "$size" --delay 2.0 \
                --path "$ROOT/$APP/src" --path "$ROOT/common" \
                $SCRIPT_ARGS -- -m xx_contacts >"$log" 2>&1; then
                rc=0
            else
                rc=$?
            fi
            if [ $rc -eq 0 ] && [ -s "$out" ] \
                && ! grep -q "snap: WARNING" "$log" \
                && grep -q "snap: wrote" "$log"; then
                break
            fi
            rc=1
            sleep 1
        done
        cp "$log" "$OUTDIR/logs/$sheet-$screen.log"
        if grep -q "snap: WARNING" "$log"; then
            echo "FAIL: $sheet/$screen phone-fit WARNING"
            grep "snap: WARNING" "$log"
            failed="$failed $sheet/$screen(warning)"
            [ -s "$out" ] && cp "$out" "$OUTDIR/failed-$sheet-$screen.png"
            continue
        fi
        if [ $rc -ne 0 ] || [ ! -s "$out" ]; then
            echo "FAIL: $sheet/$screen snap failed"
            grep -E "snap: |Traceback|Error" "$log" | tail -n 5
            failed="$failed $sheet/$screen(snap)"
            continue
        fi
        cp "$out" "$OUTDIR/$sheet-$screen.png" \
            || { echo "FAIL: $sheet/$screen copy"; failed="$failed $sheet/$screen(copy)"; continue; }
        echo "ok: $sheet/$screen"
    done
    if command -v montage >/dev/null 2>&1; then
        # shellcheck disable=SC2086
        montage -label '%f' -tile 6x -geometry +4+4 -background black \
            "$WORK/$sheet-"*.png "$OUTDIR/sheet-$sheet.png" \
            && echo "ok: sheet-$sheet"
    else
        echo "SKIP: montage missing, no sheet-$sheet"
        failed="$failed sheet-$sheet(montage)"
    fi
}

for theme in $THEMES; do
    render_set "$theme" "$theme" 1 360x720 "$SCREENS"
done
[ -n "$FIT320" ] && render_set fit320 amoled 1 320x720 "$FIT320"
[ -n "$SCALE15" ] && render_set scale15 amoled 1.5 360x720 "$SCALE15"

if [ -n "$failed" ]; then
    echo "FAILED:$failed"
    exit 1
fi
echo "ALL SHEETS OK in $OUTDIR"
