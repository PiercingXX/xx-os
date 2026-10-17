#!/bin/sh
# Seeded screenshots of every xx-files screen in amoled + paper on the
# FLX1 canvas (450x1000), the main screens at 360x800, plus fit probes
# (320 px wide, text-size-scale 1.3) and graphite/aura spot checks:
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
    printf 'fake-jpeg %.0s' $(seq 1 700) > "$home/Pictures/Camera/sunset over the harbour.jpg"
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
    # $1=profile (theme, or "large") $2=name $3=size $4=scenario $5=extra-env
    profile=$1 name=$2 size=$3 scenario=$4 extra=$5
    home="$WORK/home-$profile"
    out="$WORK/$profile-$name.png"
    err="$WORK/$profile-$name.err"
    attempt=0
    while [ "$attempt" -lt 5 ]; do
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
                fail "$profile $name: phone-fit breach"
            fi
            if [ -s "$out" ]; then
                echo "shot $profile $name $size"
                return 0
            fi
        fi
        sleep 2
    done
    fail "snap failed: $profile $name ($(tail -n 3 "$err" 2>/dev/null))"
}

for profile in $THEMES graphite aura large; do
    home="$WORK/home-$profile"
    mkdir -p "$home/.config/xx-wm" "$home/.config/xx-files" \
        "$home/.local/share"
    if [ "$profile" = large ]; then
        printf '{"theme": "paper", "text_size_scale": 1.3}' \
            > "$home/.config/xx-wm/config.json"
    else
        printf '{"theme": "%s"}' "$profile" > "$home/.config/xx-wm/config.json"
    fi
    seed_home "$home"
    seed_trash "$home/.local/share" "$home"
done

# Every state on the FLX1 canvas (450x1000), the main ones at 360x800.
states="volumes files deep tree grid search multiselect actions properties new_folder rename delete_confirm view progress trash trash_actions trash_empty empty_folder blocked"
phone="volumes files tree actions view trash"

for theme in $THEMES; do
    for state in $states; do
        snap_one "$theme" "flx1-$state" "450x1000" "$state" ""
    done
    for state in $phone; do
        snap_one "$theme" "phone-$state" "360x800" "$state" ""
    done
done

# Fit probes: 320 px wide, large text (scale 1.3), two more presets.
# (snap_one assigns $name: sh has no locals, so loops use other names.)
for probe in files actions properties; do
    snap_one paper "narrow-$probe" "320x640" "$probe" ""
    snap_one large "big-$probe" "360x800" "$probe" ""
done
snap_one large "big-view" "360x800" "view" ""
snap_one graphite "flx1-files" "450x1000" "files" ""
snap_one aura "flx1-actions" "450x1000" "actions" ""

sheet() {
    # $1=out file $2=tile, then label:shot pairs
    out=$1 tile=$2
    shift 2
    args=""
    for pair in "$@"; do
        args="$args -label ${pair%%:*} $WORK/${pair#*:}.png"
    done
    # shellcheck disable=SC2086
    montage $args -tile "$tile" -geometry +4+4 -background black \
        -fill white -pointsize 28 "$OUTDIR/$out" \
        || fail "montage failed for $out"
}

for theme in $THEMES; do
    sheet "sheet-$theme-1.png" 5x1 \
        "volumes:$theme-flx1-volumes" "files:$theme-flx1-files" \
        "deep:$theme-flx1-deep" "tree:$theme-flx1-tree" \
        "grid:$theme-flx1-grid"
    sheet "sheet-$theme-2.png" 5x1 \
        "search:$theme-flx1-search" "select:$theme-flx1-multiselect" \
        "actions:$theme-flx1-actions" "properties:$theme-flx1-properties" \
        "view:$theme-flx1-view"
    sheet "sheet-$theme-3.png" 5x1 \
        "new-folder:$theme-flx1-new_folder" "rename:$theme-flx1-rename" \
        "delete:$theme-flx1-delete_confirm" "progress:$theme-flx1-progress" \
        "blocked:$theme-flx1-blocked"
    sheet "sheet-$theme-4.png" 4x1 \
        "trash:$theme-flx1-trash" "trash-row:$theme-flx1-trash_actions" \
        "trash-empty:$theme-flx1-trash_empty" \
        "empty:$theme-flx1-empty_folder"
    sheet "sheet-$theme-360.png" 6x1 \
        "volumes:$theme-phone-volumes" "files:$theme-phone-files" \
        "tree:$theme-phone-tree" "actions:$theme-phone-actions" \
        "view:$theme-phone-view" "trash:$theme-phone-trash"
    cp "$WORK/$theme-"*.png "$OUTDIR/"
done
sheet "sheet-fit.png" 5x2 \
    "320-files:paper-narrow-files" "320-actions:paper-narrow-actions" \
    "320-props:paper-narrow-properties" "graphite:graphite-flx1-files" \
    "aura:aura-flx1-actions" \
    "large-files:large-big-files" "large-actions:large-big-actions" \
    "large-props:large-big-properties" "large-view:large-big-view"
cp "$WORK/graphite-"*.png "$WORK/aura-"*.png "$WORK/large-"*.png "$OUTDIR/"
rm -rf "$WORK"
trap - EXIT
echo "wrote $OUTDIR/sheet-{amoled,paper}-{1,2,3,4,360}.png and sheet-fit.png"
