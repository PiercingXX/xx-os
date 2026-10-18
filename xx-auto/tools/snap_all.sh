#!/bin/sh
# Seeded screenshots of every xx-auto screen, one labeled sheet per run:
#   xx-auto/tools/snap_all.sh OUTDIR
#   THEMES="amoled paper forest" SIZES="450x1000 360x800" ... OUTDIR
#
# Per theme and size: every screen in portrait, plus landscape (720x360).
# Fit runs: every screen at 320 px wide (sheet-fit320) and at large text,
# text-size scale 1.5 on 360x800 (sheet-scale15). amoled runs with
# always_ink on (forced Ink); every other theme with it off (live suite
# theme). Exits non-zero on any snap WARNING (phone-fit breach) or any
# failed render.
set -u
LANE=$(cd "$(dirname "$0")/../.." && pwd)
ROOT="$LANE/xx-auto"
OUTDIR=${1:?usage: snap_all.sh OUTDIR}
THEMES=${THEMES-"amoled paper forest"}
SIZES=${SIZES-"450x1000 360x800"}
LANDSCAPE=${LANDSCAPE-"720x360"}
FIT320=${FIT320-1}
SCALE15=${SCALE15-1}
mkdir -p "$OUTDIR"
PY=${PYTHON:-/media/Working-Storage/GitHub/Phone-Projects/linux/xx-wm/.venv/bin/python}
[ -x "$PY" ] || PY=python3

# name,scenario,sheet,nav  (nav: extra python run before the sheet opens)
SCREENS="first_launch,nothing_installed,drive,
nothing_playing,nothing_playing,drive,
playing,audiobook,drive,
radio,radio,drive,
paused,paused,drive,
players,quick_pick,players,
favourites,favourites,favourites,
favourites_empty,settings,favourites,w._render_favourites([], '')
contacts_down,settings,favourites,w._render_favourites([], 'down')
car,car,car,
car_empty,settings,car,w._car_devices = []; w._render_car_sheet()
settings,settings,settings,
licenses,settings,licenses,"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM
failed=""

snap_one() {
    # $1=run $2=home $3=name $4=size $5=scenario $6=sheet $7=nav $8=landscape
    run=$1 home=$2 name=$3 size=$4 scenario=$5 sheet=$6 extra=$7 land=$8
    out="$WORK/$run/$name.png"
    err="$WORK/$run/$name.err"
    nav="$WORK/$run/nav-$name.py"
    {
        echo "w = app._auto_window"
        [ -n "$extra" ] && echo "$extra"
        [ "$sheet" != "drive" ] && echo "w.open_sheet('$sheet')"
    } > "$nav"
    attempt=0
    while [ "$attempt" -lt 3 ]; do
        attempt=$((attempt + 1))
        rm -f "$out"
        # Own session bus per shot: the app is single-instance, so a
        # parallel run (or a real xx-auto) would swallow the launch, and
        # no real player on the user bus leaks into a seeded screen.
        HOME="$home" XDG_CONFIG_HOME="$home/.config" \
            XX_AUTO_SCENARIO="$scenario" XX_AUTO_LANDSCAPE="$land" \
            GDK_DEBUG=no-portals \
            dbus-run-session -- "$PY" "$LANE/scripts/snap.py" \
            --out "$out" --size "$size" --delay 1.2 \
            --path "$ROOT/src" --path "$LANE/common" \
            --script "$nav" -- -m xx_auto >/dev/null 2>"$err" || true
        if grep -q 'snap: WARNING' "$err"; then
            echo "FAIL: $run/$name phone-fit WARNING"
            grep 'snap: WARNING' "$err"
            failed="$failed $run/$name(warning)"
            return 1
        fi
        if [ -s "$out" ]; then
            return 0
        fi
        sleep 1
    done
    echo "FAIL: $run/$name snap failed: $(tail -n 3 "$err" 2>/dev/null)"
    failed="$failed $run/$name(snap)"
    return 1
}

render_run() {
    # $1=run name $2=theme $3=text scale $4=size $5=landscape (1 or "")
    run=$1 theme=$2 scale=$3 size=$4 land=$5
    home="$WORK/home-$run"
    mkdir -p "$WORK/$run" "$home/.config/xx-wm" "$home/.config/xx-auto"
    printf '{"theme": "%s", "text_size_scale": %s}' "$theme" "$scale" \
        > "$home/.config/xx-wm/config.json"
    if [ "$theme" = "amoled" ]; then
        printf '{"always_ink": true}' > "$home/.config/xx-auto/settings.json"
    else
        printf '{"always_ink": false}' > "$home/.config/xx-auto/settings.json"
    fi
    labels=""
    printf '%s\n' "$SCREENS" | while IFS=, read -r name scenario sheet extra; do
        [ -n "$name" ] || continue
        snap_one "$run" "$home" "$name" "$size" "$scenario" "$sheet" \
            "$extra" "$land" || echo "$run/$name" >> "$WORK/failed"
    done
    for f in "$WORK/$run/"*.png; do
        [ -s "$f" ] || continue
        labels="$labels -label $(basename "$f" .png) $f"
    done
    mkdir -p "$OUTDIR"
    # shellcheck disable=SC2086
    montage $labels -tile 7x -geometry +4+4 -background black \
        -fill white -pointsize 22 "$OUTDIR/sheet-$run.png" \
        && echo "wrote $OUTDIR/sheet-$run.png" \
        || echo "$run(montage)" >> "$WORK/failed"
}

for theme in $THEMES; do
    for size in $SIZES; do
        render_run "$theme-$size" "$theme" 1 "$size" ""
    done
    [ -n "$LANDSCAPE" ] && render_run "$theme-landscape" "$theme" 1 \
        "$LANDSCAPE" 1
done
[ "$FIT320" = 1 ] && render_run fit320 amoled 1 320x720 ""
[ "$SCALE15" = 1 ] && render_run scale15 amoled 1.5 360x800 ""

if [ -s "$WORK/failed" ]; then
    echo "snap_all: failed:" $(cat "$WORK/failed")
    exit 1
fi
echo "all sheets ok"
