#!/bin/sh
# Seeded screenshots of every xx-clock screen in amoled + paper
# (graphite/aura spot checks), portrait 360x720:
#   xx-clock/tools/snap_all.sh OUTDIR
# Exits non-zero on any snap WARNING (phone-fit breach).
set -u
LANE=$(cd "$(dirname "$0")/../.." && pwd)
ROOT="$LANE/xx-clock"
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
    # $1=theme $2=name $3=scenario-env $4=nav-script-or-empty
    theme=$1 name=$2 extra_env=$3 nav=$4
    home="$WORK/home-$theme"
    out="$WORK/$theme-$name.png"
    err="$WORK/$theme-$name.err"
    attempt=0
    while [ "$attempt" -lt 3 ]; do
        attempt=$((attempt + 1))
        rm -f "$out"
        # shellcheck disable=SC2086
        if env HOME="$home" XDG_CONFIG_HOME="$home/.config" \
            XDG_DATA_HOME="$home/.local/share" XDG_STATE_HOME="$home/.local/state" \
            XX_CLOCK_LOCAL=1 XX_CLOCK_SEED=1 $extra_env \
            "$PY" "$LANE/scripts/snap.py" \
            --out "$out" --size 360x720 --delay 2.5 \
            --path "$ROOT/src" --path "$LANE/common" \
            ${nav:+--script "$nav"} \
            -- -m xx_clock 2>"$err"; then
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

mknav() {
    # $1=outfile $2=python-body
    printf '%s\n' "$2" > "$1"
}

snap_multi() {
    # $1=theme $2=name $3=extra-env $4=shot-target (ring|dialog)
    theme=$1 name=$2 extra_env=$3 target=$4
    home="$WORK/home-$theme"
    out="$WORK/$theme-$name.png"
    err="$WORK/$theme-$name.err"
    attempt=0
    while [ "$attempt" -lt 3 ]; do
        attempt=$((attempt + 1))
        rm -f "$out"
        # shellcheck disable=SC2086
        if env HOME="$home" XDG_CONFIG_HOME="$home/.config" \
            XDG_DATA_HOME="$home/.local/share" XDG_STATE_HOME="$home/.local/state" \
            XX_CLOCK_LOCAL=1 XX_CLOCK_SEED=1 SNAP_MULTI=1 \
            SHOT_TARGET="$target" SHOT_OUT="$out" $extra_env \
            "$PY" "$LANE/scripts/snap.py" \
            --out "$out" --size 360x720 --delay 2.5 \
            --path "$ROOT/src" --path "$LANE/common" \
            --script "$ROOT/tools/nav_surface.py" \
            -- -m xx_clock 2>"$err"; then
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

for theme in amoled paper graphite aura; do
    home="$WORK/home-$theme"
    mkdir -p "$home/.config/xx-wm" "$home/.local/share" "$home/.local/state"
    printf '{"theme": "%s"}' "$theme" > "$home/.config/xx-wm/config.json"
done

mknav "$WORK/nav-alarms.py" 'app._clock_window.show_tab("alarms")'
mknav "$WORK/nav-timers.py" 'app._clock_window.show_tab("timers")'
mknav "$WORK/nav-setup.py" 'app._clock_window.show_tab("setup")'

for theme in amoled paper; do
    snap_one "$theme" clock "" ""
    snap_one "$theme" alarms "" "$WORK/nav-alarms.py"
    snap_one "$theme" timers "" "$WORK/nav-timers.py"
    snap_one "$theme" setup "" "$WORK/nav-setup.py"
    snap_multi "$theme" dialog "" "dialog"
    snap_multi "$theme" ring-alarm "XX_CLOCK_RING=alarm" "ring"
    snap_multi "$theme" ring-timer "XX_CLOCK_RING=timer" "ring"
done

for theme in graphite aura; do
    snap_one "$theme" clock "" ""
    snap_one "$theme" alarms "" "$WORK/nav-alarms.py"
done

for theme in amoled paper; do
    # shellcheck disable=SC2086
    montage -label 'clock' "$WORK/$theme-clock.png" \
        -label 'alarms' "$WORK/$theme-alarms.png" \
        -label 'timers' "$WORK/$theme-timers.png" \
        -label 'setup' "$WORK/$theme-setup.png" \
        -label 'edit' "$WORK/$theme-dialog.png" \
        -label 'ring-alarm' "$WORK/$theme-ring-alarm.png" \
        -label 'ring-timer' "$WORK/$theme-ring-timer.png" \
        -tile 4x2 -geometry +4+4 -background black \
        -fill white -pointsize 28 "$OUTDIR/sheet-$theme.png" \
        || fail "montage failed for $theme"
    cp "$WORK/$theme-"*.png "$OUTDIR/"
done
cp "$WORK/graphite-"*.png "$WORK/aura-"*.png "$OUTDIR/"
rm -rf "$WORK"
trap - EXIT
echo "wrote $OUTDIR/sheet-{amoled,paper}.png"
