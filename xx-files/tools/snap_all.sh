#!/bin/sh
# Seeded screenshots of every xx-files screen in amoled + paper at
# 360x720, plus graphite/aura spot checks and a 320 px fit check:
#   xx-files/tools/snap_all.sh OUTDIR
# Exits non-zero on any snap WARNING (phone-fit breach).
set -u
LANE=$(cd "$(dirname "$0")/../.." && pwd)
ROOT="$LANE/xx-files"
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

seed_home() {
    # $1 = HOME dir. Fixed demo tree so every shot is deterministic.
    home=$1
    mkdir -p "$home/Documents/Empty folder" "$home/Downloads" \
        "$home/Pictures/Camera" "$home/Music"
    printf 'hello world %.0s' $(seq 1 40) > "$home/Documents/notes.txt"
    printf '%%PDF-1.4 fake %.0s' $(seq 1 40) > "$home/Documents/report.pdf"
    printf 'fake-jpeg %.0s' $(seq 1 800) > "$home/Pictures/photo.jpg"
    printf 'fake-mp4 %.0s' $(seq 1 800) > "$home/Pictures/clip.mp4"
    printf 'fake-jpeg %.0s' $(seq 1 600) > "$home/Pictures/Camera/shot.jpg"
    printf 'fake-mp3 %.0s' $(seq 1 400) > "$home/Music/song.mp3"
    printf 'binary %.0s' $(seq 1 100) > "$home/Downloads/setup.bin"
}

seed_trash() {
    # $1 = XDG_DATA_HOME dir. Two trashed files with .trashinfo sidecars.
    data=$1
    mkdir -p "$data/Trash/files" "$data/Trash/info"
    printf 'old draft' > "$data/Trash/files/draft.txt"
    printf '[Trash Info]\nPath=%s/Documents/draft.txt\nDeletionDate=2026-10-01T10:00:00\n' \
        "$2" > "$data/Trash/files/../info/draft.txt.trashinfo"
    printf 'trashed bytes' > "$data/Trash/files/old-photo.jpg"
    printf '[Trash Info]\nPath=%s/Pictures/old-photo.jpg\nDeletionDate=2026-10-02T10:00:00\n' \
        "$2" > "$data/Trash/files/../info/old-photo.jpg.trashinfo"
}

snap_one() {
    # $1=theme $2=name $3=size $4=scenario $5=extra-env
    theme=$1 name=$2 size=$3 scenario=$4 extra=$5
    home="$WORK/home-$theme"
    out="$WORK/$theme-$name.png"
    err="$WORK/$theme-$name.err"
    attempt=0
    while [ "$attempt" -lt 3 ]; do
        attempt=$((attempt + 1))
        rm -f "$out"
        if HOME="$home" XDG_CONFIG_HOME="$home/.config" \
            XDG_DATA_HOME="$home/.local/share" \
            XX_FILES_HOME="$home" \
            XX_FILES_SCENARIO="$scenario" $extra \
            "$PY" "$LANE/scripts/snap.py" \
            --out "$out" --size "$size" --delay 1.5 \
            --path "$ROOT/src" --path "$LANE/common" \
            -- -m xx_files 2>"$err"; then
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

for theme in $THEMES graphite aura; do
    home="$WORK/home-$theme"
    mkdir -p "$home/.config/xx-wm" "$home/.config/xx-files" \
        "$home/.local/share"
    printf '{"theme": "%s"}' "$theme" > "$home/.config/xx-wm/config.json"
    seed_home "$home"
    seed_trash "$home/.local/share" "$home"
done

states="volumes files tree grid search multiselect properties new_folder rename delete_confirm actions sort_menu trash trash_empty empty_folder blocked progress"

for theme in $THEMES; do
    for name in $states; do
        snap_one "$theme" "portrait-$name" "360x720" "$name" ""
    done
    snap_one "$theme" "small-files" "320x640" "files" ""
    snap_one "$theme" "small-trash" "320x640" "trash" ""
done

for theme in graphite aura; do
    snap_one "$theme" "portrait-files" "360x720" "files" ""
done

for theme in $THEMES; do
    # shellcheck disable=SC2086
    montage -label 'volumes' "$WORK/$theme-portrait-volumes.png" \
        -label 'files' "$WORK/$theme-portrait-files.png" \
        -label 'tree' "$WORK/$theme-portrait-tree.png" \
        -label 'grid' "$WORK/$theme-portrait-grid.png" \
        -label 'search' "$WORK/$theme-portrait-search.png" \
        -label 'multiselect' "$WORK/$theme-portrait-multiselect.png" \
        -label 'properties' "$WORK/$theme-portrait-properties.png" \
        -label 'new-folder' "$WORK/$theme-portrait-new_folder.png" \
        -tile 4x2 -geometry +4+4 -background black \
        -fill white -pointsize 28 "$OUTDIR/sheet-$theme-1.png" \
        || fail "montage failed for $theme sheet 1"
    montage -label 'rename' "$WORK/$theme-portrait-rename.png" \
        -label 'delete' "$WORK/$theme-portrait-delete_confirm.png" \
        -label 'actions' "$WORK/$theme-portrait-actions.png" \
        -label 'sort' "$WORK/$theme-portrait-sort_menu.png" \
        -label 'trash' "$WORK/$theme-portrait-trash.png" \
        -label 'trash-empty' "$WORK/$theme-portrait-trash_empty.png" \
        -label 'empty' "$WORK/$theme-portrait-empty_folder.png" \
        -label 'blocked' "$WORK/$theme-portrait-blocked.png" \
        -tile 4x2 -geometry +4+4 -background black \
        -fill white -pointsize 28 "$OUTDIR/sheet-$theme-2.png" \
        || fail "montage failed for $theme sheet 2"
    montage -label 'progress' "$WORK/$theme-portrait-progress.png" \
        -label '320-files' "$WORK/$theme-small-files.png" \
        -label '320-trash' "$WORK/$theme-small-trash.png" \
        -tile 3x1 -geometry +4+4 -background black \
        -fill white -pointsize 28 "$OUTDIR/sheet-$theme-3.png" \
        || fail "montage failed for $theme sheet 3"
    cp "$WORK/$theme-"*.png "$OUTDIR/"
done
cp "$WORK/graphite-"*.png "$WORK/aura-"*.png "$OUTDIR/"
rm -rf "$WORK"
trap - EXIT
echo "wrote $OUTDIR/sheet-{amoled,paper}-{1,2,3}.png"
