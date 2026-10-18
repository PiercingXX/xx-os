#!/bin/sh
# Seeded screenshots of every xx-vitals screen, one labeled contact sheet
# per run, into OUTDIR:
#
#   xx-vitals/tools/snap_all.sh OUTDIR
#   THEMES="amoled paper" SIZES=450x1000 xx-vitals/tools/snap_all.sh OUTDIR
#   TARGETS="home profile" FIT320="" SCALE15="" ... (empty skips a run)
#
# Runs: every theme in THEMES at every size in SIZES (sheet-THEME-WxH),
# plus the fit checks: amoled at 320 px wide (sheet-fit320) and amoled at
# text-size scale 1.5 on 360x800 (sheet-scale15). Each run seeds its own
# HOME; the empty-* targets start from a bare HOME (first launch). Exits
# non-zero on any snap WARNING (a window that does not fit its phone
# screen) or any failed render.
set -u
LANE=$(cd "$(dirname "$0")/../.." && pwd)
OUTDIR=${1:?usage: snap_all.sh OUTDIR}
PY=${PYTHON:-/media/Working-Storage/GitHub/Phone-Projects/linux/xx-wm/.venv/bin/python}
[ -x "$PY" ] || PY=python3
THEMES=${THEMES-"amoled paper forest"}
SIZES=${SIZES-"450x1000 360x800"}
ALL="home journal browse detail metrics search-miss profile
profile-2 profile-3 profile-4 profile-5 add-activity add-weight weight-error
activity menu-home empty-home
empty-journal empty-detail empty-weight empty-profile"
TARGETS=${TARGETS-$ALL}
FIT320=${FIT320-$TARGETS}
SCALE15=${SCALE15-$TARGETS}
DIALOGS=" add-activity add-weight weight-error activity "
mkdir -p "$OUTDIR/logs"
WORK=$(mktemp -d "$OUTDIR/.work.XXXXXX")
trap 'rm -rf "$WORK"' EXIT INT TERM
# Each shot gets its own session bus: the app is single-instance, so a
# parallel run (or a real xx-vitals on the user bus) would swallow it.
BUSWRAP=""
command -v dbus-run-session >/dev/null 2>&1 && BUSWRAP="dbus-run-session --"

snap_one() {
    # $1=sheet $2=index $3=target $4=home $5=size
    sheet=$1 index=$2 target=$3 home=$4 size=$5
    nav=${target#empty-}
    page=""
    case "$nav" in
        menu-*) page=${nav#menu-}; nav=menu ;;
    esac
    out="$WORK/$sheet/$index-$target.png"
    log="$WORK/$sheet/$index-$target.log"
    delay=1.2
    extra=""
    case "$DIALOGS" in
        *" $nav "*) delay=2.2; extra="DIALOG_OUT=$out" ;;
    esac
    [ "$nav" = menu ] && { delay=2.2; extra="DIALOG_OUT=$out"; }
    snapout=$out
    [ -n "$extra" ] && snapout="$WORK/$sheet/dummy-$index.out"
    attempt=0
    while [ "$attempt" -lt 3 ]; do
        attempt=$((attempt + 1))
        rm -f "$out"
        # shellcheck disable=SC2086
        env HOME="$home" XDG_CONFIG_HOME="$home/.config" \
            XDG_DATA_HOME="$home/.local/share" \
            XDG_STATE_HOME="$home/.local/state" \
            XDG_CACHE_HOME="$home/.cache" \
            SNAP_TARGET="$nav" SNAP_MENU_PAGE="${page:-home}" $extra \
            $BUSWRAP "$PY" "$LANE/scripts/snap.py" \
            --out "$snapout" \
            --size "$size" --delay "$delay" \
            --path "$LANE/xx-vitals/src" --path "$LANE/common" \
            --script "$LANE/xx-vitals/tools/snap_nav.py" \
            -- -m xx_vitals >"$log" 2>&1
        if grep -aq 'snap: WARNING' "$log"; then
            # Keep the shot on the sheet: the overflow is what to look at.
            cp "$log" "$OUTDIR/logs/$sheet-$target.log"
            echo "FAIL: $sheet/$target $(grep -a 'snap: WARNING' "$log")"
            echo "$sheet/$target" >>"$WORK/failed"
            return 1
        fi
        [ -s "$out" ] && return 0
        sleep 1
    done
    cp "$log" "$OUTDIR/logs/$sheet-$target.log"
    echo "FAIL: $sheet/$target snap failed ($(tail -n 2 "$log"))"
    echo "$sheet/$target" >>"$WORK/failed"
    return 1
}

render_set() {
    # $1=sheet $2=theme $3=text-size scale $4=size $5=targets
    sheet=$1 theme=$2 scale=$3 size=$4 targets=$5
    mkdir -p "$WORK/$sheet"
    for kind in seeded empty; do
        home="$WORK/$sheet-$kind"
        mkdir -p "$home/.config/xx-wm"
        printf '{"theme": "%s", "text_size_scale": %s}' "$theme" "$scale" \
            >"$home/.config/xx-wm/config.json"
    done
    env HOME="$WORK/$sheet-seeded" XDG_CONFIG_HOME="$WORK/$sheet-seeded/.config" \
        XDG_DATA_HOME="$WORK/$sheet-seeded/.local/share" \
        XDG_STATE_HOME="$WORK/$sheet-seeded/.local/state" \
        XDG_CACHE_HOME="$WORK/$sheet-seeded/.cache" \
        "$PY" "$LANE/xx-vitals/tools/demo_seed.py" "$WORK/$sheet-seeded" \
        >/dev/null || { echo "FAIL: $sheet seed"; echo "$sheet/seed" >>"$WORK/failed"; return; }
    index=10
    for target in $targets; do
        index=$((index + 1))
        case "$target" in
            empty-*) home="$WORK/$sheet-empty" ;;
            *) home="$WORK/$sheet-seeded" ;;
        esac
        snap_one "$sheet" "$index" "$target" "$home" "$size"
    done
    rm -f "$WORK/$sheet"/dummy-*.out
    # shellcheck disable=SC2046
    montage -label '%t' $(ls "$WORK/$sheet"/*.png) -tile 7x \
        -geometry +4+4 -background black -fill white -pointsize 22 \
        "$OUTDIR/sheet-$sheet.png" \
        && echo "wrote $OUTDIR/sheet-$sheet.png" \
        || { echo "FAIL: $sheet montage"; echo "$sheet/montage" >>"$WORK/failed"; }
}

# Up to JOBS runs at once, each in a subshell without the cleanup trap
# (a finished run must not delete the other runs' work dir).
JOBS=${JOBS:-4}
running=0
spawn() {
    ( trap - EXIT INT TERM; render_set "$@" ) &
    running=$((running + 1))
    if [ "$running" -ge "$JOBS" ]; then
        wait
        running=0
    fi
}

for theme in $THEMES; do
    for size in $SIZES; do
        spawn "$theme-$size" "$theme" 1 "$size" "$TARGETS"
    done
done
[ -n "$FIT320" ] && spawn fit320 amoled 1 320x720 "$FIT320"
[ -n "$SCALE15" ] && spawn scale15 amoled 1.5 360x800 "$SCALE15"
wait
if [ -s "$WORK/failed" ]; then
    echo "snap_all: failed: $(tr '\n' ' ' <"$WORK/failed")"
    exit 1
fi
echo "all sheets ok"
