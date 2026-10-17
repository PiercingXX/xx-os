#!/bin/sh
# Seeded screenshots of every xx-clock screen:
#   xx-clock/tools/snap_all.sh OUTDIR
# amoled + paper at the FLX1 canvas (450x1000) and 360x800, empty lists,
# graphite/aura spot checks, and phone-fit probes at 320 px wide and at
# large text (text_size_scale 1.3). Exits non-zero on any snap WARNING
# (phone-fit breach).
set -u
LANE=$(cd "$(dirname "$0")/../.." && pwd)
ROOT="$LANE/xx-clock"
OUTDIR=${1:?usage: snap_all.sh OUTDIR}
mkdir -p "$OUTDIR"
PY=${PYTHON:-python3}
command -v "$PY" >/dev/null 2>&1 || PY=python3
# Private session bus per shot: xx-clock is a single-instance
# GApplication, so a clock already running on the user's bus (or a
# parallel shot) would swallow the launch and exit 0 without a window.
BUSWRAP=""
command -v dbus-run-session >/dev/null 2>&1 && BUSWRAP="dbus-run-session --"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

fail() {
    echo "snap_all: $1" >&2
    exit 1
}

snap_one() {
    # $1=theme $2=name $3=size $4=tab-or-surface $5=seed(1|0) $6=extra-env
    # $7=extra-config. Surfaces (dialog, ring-alarm, ring-timer) paint
    # through tools/nav_surface.py; tabs paint the main window.
    theme=$1 name=$2 size=$3 target=$4 seed=$5 extra_env=${6:-} extra=${7:-}
    home="$WORK/home-$theme-$name"
    out="$WORK/$theme-$name.png"
    err="$WORK/$theme-$name.err"
    multi="" script="" tab=$target shot=""
    case $target in
        dialog) multi=1 script="$ROOT/tools/nav_surface.py" tab=alarms shot=dialog ;;
        ring-*) multi=1 script="$ROOT/tools/nav_surface.py" tab=clock shot=ring
                extra_env="$extra_env XX_CLOCK_RING=${target#ring-}" ;;
    esac
    attempt=0
    while [ "$attempt" -lt 3 ]; do
        attempt=$((attempt + 1))
        rm -f "$out"
        rm -rf "$home"
        mkdir -p "$home/.config/xx-wm" "$home/.local/share" "$home/.local/state"
        printf '{"theme": "%s"%s}' "$theme" "$extra" > "$home/.config/xx-wm/config.json"
        seed_env=""
        [ "$seed" = 1 ] && seed_env="XX_CLOCK_SEED=1"
        # shellcheck disable=SC2086
        if $BUSWRAP env HOME="$home" XDG_CONFIG_HOME="$home/.config" \
            XDG_DATA_HOME="$home/.local/share" XDG_STATE_HOME="$home/.local/state" \
            XX_CLOCK_LOCAL=1 XX_CLOCK_TAB="$tab" $seed_env \
            ${multi:+SNAP_MULTI=1 SHOT_TARGET=$shot SHOT_OUT=$out} $extra_env \
            "$PY" "$LANE/scripts/snap.py" \
            --out "$out" --size "$size" --delay 2.5 \
            --path "$ROOT/src" --path "$LANE/common" \
            ${script:+--script "$script"} \
            -- -m xx_clock 2>"$err"; then
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

SCREENS="clock alarms timers setup dialog ring-alarm ring-timer"

for theme in amoled paper; do
    for screen in $SCREENS; do
        snap_one "$theme" "flx1-$screen" 450x1000 "$screen" 1
        snap_one "$theme" "tall-$screen" 360x800 "$screen" 1
    done
    snap_one "$theme" "flx1-alarms-empty" 450x1000 alarms 0
    snap_one "$theme" "flx1-timers-empty" 450x1000 timers 0
done

# Theme spot checks beyond the two sheet themes.
snap_one graphite "flx1-clock" 450x1000 clock 1
snap_one aura "flx1-alarms" 450x1000 alarms 1

# Phone-fit probes: narrowest canvas, and large text.
for screen in $SCREENS; do
    snap_one paper "narrow-$screen" 320x640 "$screen" 1
    snap_one paper "large-$screen" 360x800 "$screen" 1 "" \
        ', "text_size_scale": 1.3'
done

sheet() {
    # $1=output $2=tile, rest: label=file pairs
    sheet_out=$1 tile=$2
    shift 2
    args=""
    for pair in "$@"; do
        args="$args -label ${pair%%=*} $WORK/${pair#*=}.png"
    done
    # shellcheck disable=SC2086
    montage $args -tile "$tile" -geometry +4+4 -background black \
        -fill white -pointsize 28 "$OUTDIR/$sheet_out" \
        || fail "montage failed for $sheet_out"
}

for theme in amoled paper; do
    for prefix in flx1 tall; do
        sheet "sheet-$theme-$prefix.png" 4x2 \
            "clock=$theme-$prefix-clock" "alarms=$theme-$prefix-alarms" \
            "timers=$theme-$prefix-timers" "setup=$theme-$prefix-setup" \
            "edit=$theme-$prefix-dialog" "ring-alarm=$theme-$prefix-ring-alarm" \
            "ring-timer=$theme-$prefix-ring-timer"
    done
done
sheet "sheet-empty-spots.png" 3x2 \
    "amoled-alarms-empty=amoled-flx1-alarms-empty" \
    "amoled-timers-empty=amoled-flx1-timers-empty" \
    "paper-alarms-empty=paper-flx1-alarms-empty" \
    "paper-timers-empty=paper-flx1-timers-empty" \
    "graphite-clock=graphite-flx1-clock" "aura-alarms=aura-flx1-alarms"
for prefix in narrow large; do
    sheet "sheet-fit-$prefix.png" 4x2 \
        "clock=paper-$prefix-clock" "alarms=paper-$prefix-alarms" \
        "timers=paper-$prefix-timers" "setup=paper-$prefix-setup" \
        "edit=paper-$prefix-dialog" "ring-alarm=paper-$prefix-ring-alarm" \
        "ring-timer=paper-$prefix-ring-timer"
done
cp "$WORK/"*.png "$OUTDIR/"
rm -rf "$WORK"
trap - EXIT
echo "wrote $OUTDIR/sheet-*.png"
