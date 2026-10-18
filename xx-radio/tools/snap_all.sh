#!/bin/sh
# Every xx-radio screen and state in four themes, plus 320 px and
# text-size-2 fit checks:
#   xx-radio/tools/snap_all.sh OUTDIR [LABEL ...]
# Seeds a temp HOME per theme (tools/demo_seed.py), renders each target
# with scripts/snap.py through tools/snap_app.py (the demo engine, a
# local player, no network, no keyring, no player service), and
# montages one sheet per theme. Exits non-zero on any snap WARNING
# (phone-fit breach). JOBS sets the parallel renders (default 3).
# MINIMAL=both (default) also renders every screen in Minimal Mode as
# mm-LABEL (sheet-minimal-THEME.png, sheet-minimal-fit.png), plus the
# live switch (mm-toggle-on, mm-toggle-off); MINIMAL=only skips the
# regular screens, MINIMAL=none the minimal ones. A LABEL argument may
# name either form.
set -u
LANE=$(cd "$(dirname "$0")/../.." && pwd)
ROOT="$LANE/xx-radio"
OUTDIR=${1:?usage: snap_all.sh OUTDIR [LABEL ...]}
shift
ONLY=${*:-}
mkdir -p "$OUTDIR"
OUTDIR=$(cd "$OUTDIR" && pwd)
PY=${PYTHON:-python3}
THEMES=${THEMES:-"amoled paper graphite aura"}
JOBS=${JOBS:-3}
# SIZE: the window for the theme renders (default 360x720; the phones
# are 450x1000 and 360x720 logical).
SIZE=${SIZE:-360x720}
MINIMAL=${MINIMAL:-both}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# label target scenario [ENV=VALUE ...]
SCREENS="home home seeded
home-lower home-lower seeded
home-end home-end seeded
home-empty home empty
search search seeded
search-typing search-typing seeded
search-results search-results seeded
search-songs search-songs seeded
search-menu search-menu seeded
artist artist seeded
artist-lower artist-lower seeded
album album seeded
playlist playlist seeded
mood mood seeded
moods moods seeded
charts charts seeded
item-menu item-menu seeded
playing playing seeded
playing-paused playing seeded SNAP_PAUSED=1
playing-problem playing-problem seeded
lyrics lyrics seeded
queue queue seeded
sheet-sleep sheet-sleep seeded
sheet-sleep-on sheet-sleep-on seeded
library library seeded
library-liked library-songs seeded
library-phone library-phone seeded
library-phone-lower library-phone-lower seeded
library-signedout library signed-out
library-empty library empty
settings settings seeded
settings-signedout settings signed-out
settings-lower settings-lower seeded
settings-end settings-end seeded
settings-about settings-about seeded
sheet-cap sheet-cap seeded
engine-ready engine seeded
engine-token engine seeded SNAP_ENGINE=token-missing
engine-updating engine-updating seeded
engine-failed engine seeded SNAP_ENGINE=failed
signin signin signed-out
signin-error signin-error signed-out SNAP_SIGNIN=fail
oauth oauth signed-out
log log seeded
licenses licenses seeded
firstrun firstrun firstrun
welcome welcome empty
firstrun-running firstrun-go firstrun SNAP_FIRSTRUN=running
firstrun-failed firstrun-go firstrun SNAP_FIRSTRUN=failed
firstrun-done firstrun-go firstrun SNAP_FIRSTRUN=done
banner-changed banner-changed seeded
banner-helper banner-helper seeded
banner-offline banner-offline seeded
banner-signedout banner-signedout seeded
banner-mix banner-mix seeded
kill-confirm kill-confirm seeded
kill-toast kill-toast seeded
killed-artists killed-artists seeded
cast-sheet cast-sheet seeded
casting casting seeded
casting-sheet casting-sheet seeded"
# Minimal Mode only: the live switch from Settings, both ways.
MM_EXTRA="mm-toggle-on minimal-on seeded
mm-toggle-off minimal-off seeded SNAP_MINIMAL=1
mm-toggle-home minimal-on-home seeded"
FIT320="home search-results artist album playing lyrics queue library-phone
settings engine-updating signin firstrun-failed sheet-sleep-on search-menu
welcome kill-confirm killed-artists casting"
SCALE2="home search-results album playing queue library-phone settings
engine-ready signin firstrun welcome"

home_for() {
    # $1=theme $2=scenario $3=text scale $4=variant: Minimal Mode saves
    # its setting in HOME, so minimal renders get their own HOME and each
    # live switch a private one (parallel jobs never see each other's).
    kind=seeded
    case $2 in empty|firstrun) kind=empty ;; esac
    home="$WORK/home-$1-$kind-$3-$4"
    if [ ! -d "$home" ]; then
        mkdir -p "$home/.config/xx-wm"
        printf '{"theme": "%s", "text_size_scale": %s}' "$1" "$3" \
            > "$home/.config/xx-wm/config.json"
        if [ "$kind" = seeded ]; then
            "$PY" "$ROOT/tools/demo_seed.py" "$home" >/dev/null \
                || { echo "snap_all: seed failed" >&2; exit 1; }
        fi
    fi
    echo "$home"
}

line_for() {
    # mm-LABEL is LABEL in Minimal Mode (saved in settings before start)
    printf '%s\n' "$MM_EXTRA" | while read -r label rest; do
        [ "$label" = "$1" ] && { echo "$rest"; return; }
    done
    base=${1#mm-}
    printf '%s\n' "$SCREENS" | while read -r label rest; do
        if [ "$label" = "$base" ]; then
            if [ "$base" = "$1" ]; then echo "$rest"
            else echo "$rest SNAP_MINIMAL=1"; fi
            return
        fi
    done
}

# One job per line: out home size target scenario env...
jobs="$WORK/jobs"
: > "$jobs"
add_job() {
    # $1=out $2=theme $3=scale $4=size $5=label
    set -- "$1" "$2" "$3" "$4" "$5" $(line_for "$5")
    out=$1 theme=$2 scale=$3 size=$4 label=$5
    target=$6 scenario=$7
    shift 7
    case $label in
        mm-toggle-*) variant=$label ;;
        mm-*) variant=mm ;;
        *) variant=regular ;;
    esac
    home=$(home_for "$theme" "$scenario" "$scale" "$variant")
    # no trailing blank: xargs -L would join the next line
    echo "$out $home $size $target $scenario${*:+ $*}" >> "$jobs"
}

wanted() {
    [ -z "$ONLY" ] && return 0
    for item in $ONLY; do [ "$item" = "$1" ] && return 0; done
    return 1
}

regular=$(printf '%s\n' "$SCREENS" | cut -d' ' -f1)
mm_labels=""
for label in $regular; do mm_labels="$mm_labels mm-$label"; done
mm_labels="$mm_labels $(printf '%s\n' "$MM_EXTRA" | cut -d' ' -f1)"
fit_labels=$FIT320
scale_labels=$SCALE2
case $MINIMAL in
    none) labels_all=$regular ;;
    only) labels_all=$mm_labels; fit_labels=""; scale_labels="" ;;
    *) labels_all="$regular $mm_labels" ;;
esac
if [ "$MINIMAL" != none ]; then
    for label in $FIT320 mm-toggle-on; do
        case $label in mm-*) ;; *) label=mm-$label ;; esac
        fit_labels="$fit_labels $label"
    done
    for label in $SCALE2; do scale_labels="$scale_labels mm-$label"; done
fi
for theme in $THEMES; do
    for label in $labels_all; do
        wanted "$label" && add_job "$WORK/$theme-$label.png" "$theme" 1 \
            "$SIZE" "$label"
    done
done
for label in $fit_labels; do
    wanted "$label" && add_job "$WORK/fit320-$label.png" amoled 1 320x720 \
        "$label"
done
for label in $scale_labels; do
    wanted "$label" && add_job "$WORK/scale2-$label.png" amoled 2 360x720 \
        "$label"
done

cat > "$WORK/one.sh" <<EOF
#!/bin/sh
out=\$1 home=\$2 size=\$3 target=\$4 scenario=\$5
shift 5
attempt=0
while [ "\$attempt" -lt 10 ]; do
    attempt=\$((attempt + 1))
    rm -f "\$out"
    env HOME="\$home" XDG_CONFIG_HOME="\$home/.config" \\
        XDG_DATA_HOME="\$home/.local/share" XDG_CACHE_HOME="\$home/.cache" \\
        XDG_STATE_HOME="\$home/.local/state" \\
        DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent-snap-bus \\
        SNAP_TARGET="\$target" SNAP_SCENARIO="\$scenario" "\$@" \\
        "$PY" "$LANE/scripts/snap.py" --out "\$out" --size "\$size" \\
        --delay 1.5 --path "$ROOT/src" --path "$LANE/common" \\
        --path "$ROOT/tools" --script "$ROOT/tools/snap_nav.py" \\
        -- "$ROOT/tools/snap_app.py" > "\$out.err" 2>&1
    if grep -q 'snap: WARNING' "\$out.err"; then
        echo "WARN \$out" >> "$WORK/warnings"
        grep 'snap: WARNING' "\$out.err" >&2
        exit 1
    fi
    [ -s "\$out" ] && exit 0
    sleep 1
done
echo "FAIL \$out" >> "$WORK/failures"
tail -n 5 "\$out.err" >&2
exit 1
EOF
xargs -P "$JOBS" -L 1 sh "$WORK/one.sh" < "$jobs"

if [ -s "$WORK/warnings" ] || [ -s "$WORK/failures" ]; then
    cat "$WORK/warnings" "$WORK/failures" >&2 2>/dev/null
    echo "snap_all: failed" >&2
    exit 1
fi

# sheet OUT STRIP FILE... : one montage of the renders that exist,
# each captioned with its name less the STRIP prefix
sheet() {
    out=$1 strip=$2
    shift 2
    labels=""
    for file in "$@"; do
        [ -s "$file" ] || continue
        name=$(basename "$file" .png)
        labels="$labels -label ${name#"$strip"} $file"
    done
    [ -n "$labels" ] || return 0
    # shellcheck disable=SC2086
    montage $labels -tile 9x -geometry "$SIZE"+6+6 -background '#202020' \
        -fill white -pointsize 22 "$OUTDIR/$out" \
        || { echo "snap_all: montage failed" >&2; exit 1; }
    echo "$OUTDIR/$out"
}

for theme in $THEMES; do
    files=""
    for label in $regular; do files="$files $WORK/$theme-$label.png"; done
    # shellcheck disable=SC2086
    sheet "sheet-$theme.png" "$theme-" $files
    files=""
    for label in $mm_labels; do files="$files $WORK/$theme-$label.png"; done
    # shellcheck disable=SC2086
    sheet "sheet-minimal-$theme.png" "$theme-" $files
    cp "$WORK/$theme-"*.png "$OUTDIR/" 2>/dev/null
done
# shellcheck disable=SC2046
sheet sheet-fit.png "" $(ls "$WORK"/fit320-*.png "$WORK"/scale2-*.png \
    2>/dev/null | grep -v -- '-mm-')
# shellcheck disable=SC2046
sheet sheet-minimal-fit.png "" $(ls "$WORK"/fit320-mm-*.png \
    "$WORK"/scale2-mm-*.png 2>/dev/null)
cp "$WORK"/fit320-*.png "$WORK"/scale2-*.png "$OUTDIR/" 2>/dev/null
exit 0
