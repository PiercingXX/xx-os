#!/bin/sh
# Seeded screenshots of every xx-note screen in amoled + paper
# (plus graphite/aura spot checks), portrait 360x720:
#   xx-note/tools/snap_all.sh OUTDIR
# Exits non-zero on any snap WARNING (phone-fit breach).
set -u
LANE=$(cd "$(dirname "$0")/../.." && pwd)
ROOT="$LANE/xx-note"
OUTDIR=${1:?usage: snap_all.sh OUTDIR}
mkdir -p "$OUTDIR"
PY=${PYTHON:-python3}
command -v "$PY" >/dev/null 2>&1 || PY=python3

THEMES="amoled paper"
SPOT="graphite aura"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

fail() {
    echo "snap_all: $1" >&2
    exit 1
}

snap_one() {
    # $1=theme $2=name $3=scenario $4=page
    theme=$1 name=$2 scenario=$3 page=$4
    home="$WORK/home-$theme-$name"
    out="$WORK/$theme-$name.png"
    err="$WORK/$theme-$name.err"
    nav="$WORK/nav-$theme-$name.py"
    printf 'app._note_window.show_page(%s)\n' "'$page'" > "$nav"
    attempt=0
    while [ "$attempt" -lt 3 ]; do
        attempt=$((attempt + 1))
        rm -f "$out"
        if HOME="$home" XDG_CONFIG_HOME="$home/.config" \
            XDG_DATA_HOME="$home/.local/share" \
            XX_NOTE_SCENARIO="$scenario" XX_NOTE_PAGE="$page" \
            "$PY" "$LANE/scripts/snap.py" \
            --out "$out" --size "360x720" --delay 1.2 \
            --path "$ROOT/src" --path "$LANE/common" \
            --script "$nav" \
            -- -m xx_note 2>"$err"; then
            if grep -q 'snap: WARNING' "$err"; then
                cat "$err" >&2
                fail "$theme $name: phone-fit breach"
            fi
            if [ -s "$out" ]; then
                echo "shot $theme $name"
                return 0
            fi
        fi
        sleep 1
    done
    fail "snap failed: $theme $name ($(tail -n 3 "$err" 2>/dev/null))"
}

for theme in $THEMES $SPOT; do
    for name in notes editor labels archive trash settings sync empty notes-spot editor-spot; do
        home="$WORK/home-$theme-$name"
        mkdir -p "$home/.config/xx-wm" "$home/Documents"
        printf '{"theme": "%s"}' "$theme" > "$home/.config/xx-wm/config.json"
    done
done

states="notes,full,notes
editor,full,editor
labels,full,labels
archive,full,archive
trash,full,trash
settings,full,settings
sync,full,sync
empty,empty,notes"

for theme in $THEMES; do
    for entry in $states; do
        name=${entry%%,*}; rest=${entry#*,}
        scenario=${rest%%,*}; page=${rest##*,}
        snap_one "$theme" "$name" "$scenario" "$page"
    done
done

for theme in $SPOT; do
    snap_one "$theme" "notes-spot" "full" "notes"
    snap_one "$theme" "editor-spot" "full" "editor"
done

needs_sheet=0
command -v montage >/dev/null 2>&1 || needs_sheet=1
if [ "$needs_sheet" -eq 0 ]; then
    for theme in $THEMES; do
        # shellcheck disable=SC2086
        montage -label 'notes' "$WORK/$theme-notes.png" \
            -label 'editor' "$WORK/$theme-editor.png" \
            -label 'labels' "$WORK/$theme-labels.png" \
            -label 'archive' "$WORK/$theme-archive.png" \
            -label 'trash' "$WORK/$theme-trash.png" \
            -label 'settings' "$WORK/$theme-settings.png" \
            -label 'sync' "$WORK/$theme-sync.png" \
            -label 'empty' "$WORK/$theme-empty.png" \
            -tile 4x2 -geometry +4+24 -background black \
            -fill white -pointsize 22 "$OUTDIR/sheet-$theme.png" \
            || fail "montage failed for $theme"
    done
    for theme in $SPOT; do
        montage -label 'notes' "$WORK/$theme-notes-spot.png" \
            -label 'editor' "$WORK/$theme-editor-spot.png" \
            -tile 2x1 -geometry +4+24 -background black \
            -fill white -pointsize 22 "$OUTDIR/sheet-$theme-spot.png" \
            || fail "montage failed for $theme"
    done
fi
for f in "$WORK/"*.png; do
    cp "$f" "$OUTDIR/"
done
echo "wrote $OUTDIR (sheets + singles)"
