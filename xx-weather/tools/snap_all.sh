#!/bin/sh
# Seeded screenshots of every xx-weather screen in amoled + paper at the
# FLX1 canvas (450x1000) and a 360x800 phone, plus fit probes (320 px
# wide, large text at 1.5) and graphite/aura spot checks:
#   xx-weather/tools/snap_all.sh OUTDIR
# Writes sheet-{amoled,paper}-{450x1000,360x800}.png and sheet-fit.png.
# Exits non-zero on any snap WARNING (phone-fit breach).
set -u
LANE=$(cd "$(dirname "$0")/../.." && pwd)
ROOT="$LANE/xx-weather"
OUTDIR=${1:?usage: snap_all.sh OUTDIR}
mkdir -p "$OUTDIR"
PY=${PYTHON:-python3}
command -v "$PY" >/dev/null 2>&1 || PY=python3

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

fail() {
    echo "snap_all: $1" >&2
    exit 1
}

snap_one() {
    # $1=theme $2=name $3=size $4=scenario $5=page $6=extra-config
    theme=$1 name=$2 size=$3 scenario=$4 page=$5 extra=${6:-}
    home="$WORK/home-$theme-$name"
    mkdir -p "$home/.config/xx-wm"
    printf '{"theme": "%s"%s}' "$theme" "$extra" > "$home/.config/xx-wm/config.json"
    out="$WORK/$theme-$name.png"
    err="$WORK/$theme-$name.err"
    attempt=0
    while [ "$attempt" -lt 5 ]; do
        attempt=$((attempt + 1))
        rm -f "$out"
        if HOME="$home" XDG_CONFIG_HOME="$home/.config" \
            XDG_DATA_HOME="$home/.local/share" \
            XDG_CACHE_HOME="$home/.cache" \
            XX_WEATHER_SCENARIO="$scenario" XX_WEATHER_PAGE="$page" \
            "$PY" "$LANE/scripts/snap.py" \
            --out "$out" --size "$size" --delay 1.2 \
            --path "$ROOT/src" --path "$LANE/common" \
            -- -m xx_weather 2>"$err"; then
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

# name,scenario,page per shot (every screen and every data state)
STATES="welcome,welcome,forecast
add,welcome,places
loading,loading,forecast
current,current,forecast
error,error,forecast
day,day,forecast
places,places,places
settings,current,settings
alerts,alerts,settings
licenses,current,licenses"
SIZES="450x1000 360x800"

group() {
    # $1=theme $2=size: every state, one after another
    for entry in $STATES; do
        name=${entry%%,*}; rest=${entry#*,}
        scenario=${rest%%,*}; page=${rest##*,}
        snap_one "$1" "$2-$name" "$2" "$scenario" "$page"
    done
}

LARGE=', "text_size_scale": 1.5'

fits() {
    # Narrowest canvas, large text, and two more presets.
    snap_one paper fit-narrow-current 320x640 current forecast
    snap_one paper fit-narrow-alerts 320x640 alerts settings
    snap_one paper fit-narrow-places 320x640 places places
    snap_one paper fit-narrow-day 320x640 day forecast
    snap_one paper fit-large-current 360x800 current forecast "$LARGE"
    snap_one amoled fit-large-alerts 360x800 alerts settings "$LARGE"
    snap_one amoled fit-large-places 360x800 places places "$LARGE"
    snap_one amoled fit-large-day 360x800 day forecast "$LARGE"
    snap_one paper fit-large-licenses 360x800 current licenses "$LARGE"
    snap_one amoled fit-large-narrow 320x640 alerts settings "$LARGE"
    snap_one graphite fit-spot-current 450x1000 current forecast
    snap_one aura fit-spot-places 450x1000 places places
    snap_one aura fit-spot-celsius 360x800 alerts-c settings
}

# One at a time: Broadway display numbers (and their ports) collide
# when renders run in parallel.
for theme in amoled paper; do
    for size in $SIZES; do
        group "$theme" "$size"
    done
done
fits

labels() {
    # $1=theme $2=size
    for entry in $STATES; do
        name=${entry%%,*}
        printf -- "-label %s %s " "$name" "$WORK/$1-$2-$name.png"
    done
}

for theme in amoled paper; do
    for size in $SIZES; do
        # shellcheck disable=SC2046
        montage $(labels "$theme" "$size") \
            -tile 5x2 -geometry +4+4 -background black \
            -fill white -pointsize 28 "$OUTDIR/sheet-$theme-$size.png" \
            || fail "montage failed for $theme $size"
    done
done
montage -label 'narrow' "$WORK/paper-fit-narrow-current.png" \
    -label 'narrow alerts' "$WORK/paper-fit-narrow-alerts.png" \
    -label 'narrow places' "$WORK/paper-fit-narrow-places.png" \
    -label 'narrow day' "$WORK/paper-fit-narrow-day.png" \
    -label 'large' "$WORK/paper-fit-large-current.png" \
    -label 'large alerts' "$WORK/amoled-fit-large-alerts.png" \
    -label 'large places' "$WORK/amoled-fit-large-places.png" \
    -label 'large day' "$WORK/amoled-fit-large-day.png" \
    -label 'large licenses' "$WORK/paper-fit-large-licenses.png" \
    -label 'large 320' "$WORK/amoled-fit-large-narrow.png" \
    -label 'graphite' "$WORK/graphite-fit-spot-current.png" \
    -label 'aura' "$WORK/aura-fit-spot-places.png" \
    -label 'aura celsius' "$WORK/aura-fit-spot-celsius.png" \
    -tile 7x2 -geometry +4+4 -background black \
    -fill white -pointsize 28 "$OUTDIR/sheet-fit.png" \
    || fail "montage failed for fit"
cp "$WORK/"*.png "$OUTDIR/"
rm -rf "$WORK"
trap - EXIT
echo "wrote $OUTDIR/sheet-{amoled,paper}-{450x1000,360x800}.png and sheet-fit.png"
