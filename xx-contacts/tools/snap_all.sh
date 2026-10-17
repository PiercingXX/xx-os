#!/bin/sh
# Render EVERY xx-contacts screen in each theme and montage one labeled
# contact sheet per theme into OUTDIR. Exits non-zero on any snap WARNING
# (a window that does not fit 360x720) or any failed render.
#
#   xx-contacts/tools/snap_all.sh OUTDIR
#   THEMES="amoled paper forest aura" xx-contacts/tools/snap_all.sh OUTDIR
#   BEFORE=1 THEMES=amoled xx-contacts/tools/snap_all.sh lane_shots/before
#
# BEFORE=1 renders the current-code screen set (no settings/starred shots:
# those pages do not exist before the restyle).
set -u
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
OUTDIR=${1:?usage: snap_all.sh OUTDIR}
THEMES=${THEMES:-"amoled paper forest aura"}
BEFORE=${BEFORE:-0}

APP=xx-contacts
if [ "$BEFORE" = 1 ]; then
    SCREENS="list search detail editor-new editor-existing import-export me pick menu empty"
else
    SCREENS="list search starred detail editor-new editor-existing settings import-export me pick menu empty"
fi
# NOTE: the delete confirm and the import merge-plan confirm are
# Adw.AlertDialogs, which are not Gtk.Window toplevels and cannot be
# captured by scripts/snap.py headlessly; they are exercised by the
# dialog tests and described in the lane report instead.

mkdir -p "$OUTDIR" "$OUTDIR/logs"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM

failed=""
for theme in $THEMES; do
    for screen in $SCREENS; do
        SHOME="$WORK/home-$theme-$screen"
        mkdir -p "$SHOME"
        if [ "$screen" != "empty" ]; then
            "$ROOT/$APP/tools/demo_seed.py" "$SHOME" >/dev/null
        fi
        mkdir -p "$SHOME/.config/xx-wm"
        printf '{"theme": "%s"}\n' "$theme" >"$SHOME/.config/xx-wm/config.json"
        NAVNAME=$(printf '%s' "$screen" | tr '-' '_')
        case "$screen" in
            list | empty) NAV="" ;;
            *) NAV="$ROOT/$APP/tools/snaps/nav_${NAVNAME}.py" ;;
        esac
        # nav_pick.py and nav_delete.py/menu hide the main window and resize
        # the dialog; every other shot must already be 360x720.
        out="$WORK/$theme-$screen.png"
        log="$WORK/$theme-$screen.log"
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
                --out "$out" --delay 2.0 \
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
        cp "$log" "$OUTDIR/logs/$theme-$screen.log"
        if [ $rc -ne 0 ] || [ ! -s "$out" ]; then
            echo "FAIL: $theme/$screen snap failed"
            grep -E "snap: |Traceback|Error" "$log" | tail -n 5
            failed="$failed $theme/$screen(snap)"
            continue
        fi
        if grep -q "snap: WARNING" "$log"; then
            echo "FAIL: $theme/$screen phone-fit WARNING"
            grep "snap: WARNING" "$log"
            failed="$failed $theme/$screen(warning)"
            continue
        fi
        cp "$out" "$OUTDIR/$theme-$screen.png" \
            || { echo "FAIL: $theme/$screen copy"; failed="$failed $theme/$screen(copy)"; continue; }
        echo "ok: $theme/$screen"
    done
    if command -v montage >/dev/null 2>&1; then
        # shellcheck disable=SC2086
        montage -label '%f' -tile 4x -geometry +4+4 -background black \
            "$WORK/$theme-"*.png "$OUTDIR/sheet-$theme.png" \
            && echo "ok: sheet-$theme"
    else
        echo "SKIP: montage missing, no sheet-$theme"
        failed="$failed sheet-$theme(montage)"
    fi
done

if [ -n "$failed" ]; then
    echo "FAILED:$failed"
    exit 1
fi
echo "ALL SHEETS OK in $OUTDIR"
