#!/bin/sh
# Seeded screenshots of every xx-weather screen in amoled + paper,
# plus graphite/aura spot checks and phone-fit probes (320 px, 600x1333,
# large text):
#   xx-weather/tools/snap_all.sh OUTDIR
# Exits non-zero on any snap WARNING (phone-fit breach).
set -u
LANE=$(cd "$(dirname "$0")/../.." && pwd)
ROOT="$LANE/xx-weather"
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

snap_one() {
    # $1=theme $2=name $3=size $4=scenario $5=page $6=extra-config
    theme=$1 name=$2 size=$3 scenario=$4 page=$5 extra=${6:-}
    home="$WORK/home-$theme-$name"
    mkdir -p "$home/.config/xx-wm"
    printf '{"theme": "%s"%s}' "$theme" "$extra" > "$home/.config/xx-wm/config.json"
    out="$WORK/$theme-$name.png"
    err="$WORK/$theme-$name.err"
    attempt=0
    while [ "$attempt" -lt 3 ]; do
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

# scenario,page per shot (states cover every screen and every data state)
states="welcome,welcome,forecast
loading,loading,forecast
current,current,forecast
error,error,forecast
places,places,places
settings,current,settings
alerts,alerts,settings
licenses,current,licenses
day,day,forecast"

for theme in $THEMES; do
    for entry in $states; do
        name=${entry%%,*}; rest=${entry#*,}
        scenario=${rest%%,*}; page=${rest##*,}
        snap_one "$theme" "$name" "360x720" "$scenario" "$page"
    done
done

# Phone-fit probes: narrowest canvas, FLX1 canvas, large text.
snap_one "paper" "narrow-current" "320x640" "current" "forecast"
snap_one "paper" "flx1-current" "600x1333" "current" "forecast"
snap_one "paper" "large-current" "360x720" "current" "forecast" \
    ', "text_size_scale": 1.3'

# Theme spot checks beyond the two sheet themes.
snap_one "graphite" "spot-current" "360x720" "current" "forecast"
snap_one "aura" "spot-settings" "360x720" "current" "settings"

for theme in $THEMES; do
    # shellcheck disable=SC2086
    montage -label 'welcome' "$WORK/$theme-welcome.png" \
        -label 'loading' "$WORK/$theme-loading.png" \
        -label 'current' "$WORK/$theme-current.png" \
        -label 'error' "$WORK/$theme-error.png" \
        -label 'places' "$WORK/$theme-places.png" \
        -label 'settings' "$WORK/$theme-settings.png" \
        -label 'alerts' "$WORK/$theme-alerts.png" \
        -label 'licenses' "$WORK/$theme-licenses.png" \
        -label 'day' "$WORK/$theme-day.png" \
        -tile 3x3 -geometry +4+4 -background black \
        -fill white -pointsize 28 "$OUTDIR/sheet-$theme.png" \
        || fail "montage failed for $theme"
    cp "$WORK/$theme-"*.png "$OUTDIR/"
done
cp "$WORK/paper-narrow-current.png" "$WORK/paper-flx1-current.png" \
    "$WORK/paper-large-current.png" "$WORK/graphite-spot-current.png" \
    "$WORK/aura-spot-settings.png" "$OUTDIR/"
rm -rf "$WORK"
trap - EXIT
echo "wrote $OUTDIR/sheet-{amoled,paper}.png"
