#!/bin/sh
# Seeded screenshots of every xx-note screen in amoled + paper at the
# FLX1 canvas (450x1000) and a 360x800 phone, plus graphite (450x1000)
# and aura (360x800) spot checks and phone-fit probes (320x640, and
# text size 1.5 at 360x800):
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
SIZES="450x1000 360x800"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# A private session bus per shot: the app is single-instance, so a running
# xx-note (or a parallel shot) would swallow the launch otherwise.
if command -v dbus-run-session >/dev/null 2>&1; then
    BUSWRAP="dbus-run-session --"
else
    BUSWRAP=""
fi

fail() {
    echo "snap_all: $1" >&2
    exit 1
}

SYNC_ON='{"webdav_enabled": true, "webdav_url": "https://cloud.example.com/remote.php/dav", "webdav_user": "me", "last_sync": "09:38"}'

# name|scenario|page|nav python (w = the NoteWindow)|xx-note settings json
states="notes|full|notes||
editor|full|editor||
read|full|editor|w.editor_page.toggle_mode()|
select|full|notes|w._on_long_press(None, 0, 0, w._notes[0].id); w._on_long_press(None, 0, 0, w._notes[2].id)|
search|full|notes|w.toggle_search(); w.search.set_text('the')|
filter|full|notes|w._filter_label('errands')|
labels|full|labels||
archive|full|archive||
trash|full|trash||
settings|full|settings||
sync|full|sync||$SYNC_ON
sync-off|full|sync||
error|full|settings|p = w.settings_page; p.folder_entry.set_text('/proc/xx-note'); p._apply_folder(None)|
empty|empty|notes||"

snap_one() {
    # $1=theme $2=name $3=size $4=state-line $5=extra config json
    theme=$1 name=$2 size=$3 line=$4 extra=${5:-}
    scenario=$(printf '%s' "$line" | cut -d'|' -f2)
    page=$(printf '%s' "$line" | cut -d'|' -f3)
    navline=$(printf '%s' "$line" | cut -d'|' -f4)
    prefs=$(printf '%s' "$line" | cut -d'|' -f5)
    home="$WORK/home-$theme-$name-$size"
    out="$WORK/$theme-$name-$size.png"
    err="$WORK/$theme-$name-$size.err"
    nav="$WORK/nav-$theme-$name-$size.py"
    {
        printf 'w = app._note_window\n'
        printf 'w.show_page(%s)\n' "'$page'"
        [ -n "$navline" ] && printf '%s\n' "$navline"
    } > "$nav"
    attempt=0
    while [ "$attempt" -lt 8 ]; do
        attempt=$((attempt + 1))
        rm -f "$out"
        rm -rf "$home"
        mkdir -p "$home/.config/xx-wm" "$home/.config/xx-note" "$home/Documents"
        printf '{"theme": "%s"%s}' "$theme" "$extra" > "$home/.config/xx-wm/config.json"
        [ -n "$prefs" ] && printf '%s' "$prefs" > "$home/.config/xx-note/settings.json"
        if HOME="$home" XDG_CONFIG_HOME="$home/.config" \
            XDG_DATA_HOME="$home/.local/share" \
            XX_NOTE_SCENARIO="$scenario" XX_NOTE_PAGE="$page" \
            $BUSWRAP "$PY" "$LANE/scripts/snap.py" \
            --out "$out" --size "$size" --delay 1.2 \
            --path "$ROOT/src" --path "$LANE/common" \
            --script "$nav" \
            -- -m xx_note 2>"$err"; then
            if grep -q 'snap: WARNING' "$err"; then
                cat "$err" >&2
                fail "$theme $name $size: phone-fit breach"
            fi
            if [ -s "$out" ]; then
                echo "shot $theme $name $size"
                return 0
            fi
        fi
        # Parallel snaps can draw the same Broadway display: retry.
        sleep "$attempt"
    done
    fail "snap failed: $theme $name $size ($(tail -n 3 "$err" 2>/dev/null))"
}

state() {
    printf '%s\n' "$states" | grep "^$1|"
}

names=$(printf '%s\n' "$states" | cut -d'|' -f1)

for theme in $THEMES; do
    for size in $SIZES; do
        for name in $names; do
            snap_one "$theme" "$name" "$size" "$(state "$name")"
        done
    done
done

# Theme spot checks beyond the two sheet themes.
SPOTS="notes editor settings sync"
for name in $SPOTS; do
    snap_one graphite "$name" "450x1000" "$(state "$name")"
    snap_one aura "$name" "360x800" "$(state "$name")"
done

# Phone-fit probes: narrowest canvas and large text.
FITS="notes editor read select search labels archive trash settings sync"
for name in $FITS; do
    snap_one "paper" "$name" "320x640" "$(state "$name")"
done
for name in $FITS; do
    snap_one "amoled" "$name-large" "360x800" "$(state "$name")" \
        ', "text_size_scale": 1.5'
done

if command -v montage >/dev/null 2>&1; then
    for theme in $THEMES; do
        for size in $SIZES; do
            labels=""
            for name in $names; do
                labels="$labels -label $name $WORK/$theme-$name-$size.png"
            done
            # shellcheck disable=SC2086
            montage $labels -tile 8x2 -geometry +4+24 -background black \
                -fill white -pointsize 22 "$OUTDIR/sheet-$theme-$size.png" \
                || fail "montage failed for $theme $size"
        done
    done
    labels=""
    for name in $SPOTS; do
        labels="$labels -label graphite-$name $WORK/graphite-$name-450x1000.png"
    done
    for name in $SPOTS; do
        labels="$labels -label aura-$name $WORK/aura-$name-360x800.png"
    done
    # shellcheck disable=SC2086
    montage $labels -tile 4x2 -geometry +4+24 -background black \
        -fill white -pointsize 22 "$OUTDIR/sheet-spots.png" \
        || fail "montage failed for spots"
    labels=""
    for name in $FITS; do
        labels="$labels -label 320-$name $WORK/paper-$name-320x640.png"
    done
    for name in $FITS; do
        labels="$labels -label large-$name $WORK/amoled-$name-large-360x800.png"
    done
    # shellcheck disable=SC2086
    montage $labels -tile 10x2 -geometry +4+24 -background black \
        -fill white -pointsize 22 "$OUTDIR/sheet-fit.png" \
        || fail "montage failed for fit"
fi
for f in "$WORK/"*.png; do
    cp "$f" "$OUTDIR/"
done
echo "wrote $OUTDIR (sheets + singles)"
