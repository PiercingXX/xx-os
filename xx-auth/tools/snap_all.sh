#!/bin/sh
# Seeded screenshots of every xx-auth screen in amoled + paper:
#   xx-auth/tools/snap_all.sh OUTDIR
# Exits non-zero on any snap WARNING (phone-fit breach).
set -u
LANE=$(cd "$(dirname "$0")/../.." && pwd)
ROOT="$LANE/xx-auth"
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

if command -v dbus-run-session >/dev/null 2>&1; then
    BUSWRAP="dbus-run-session --"
else
    BUSWRAP=""
fi

snap_one() {
    # $1=theme $2=name $3=size $4=scenario $5=page $6=nav $7=nokeyring $8=delay
    theme=$1 name=$2 size=$3 scenario=$4 page=$5 navcontent=$6 nokeyring=$7
    delay=${8:-1.5}
    home="$WORK/home-$theme-$name"
    mkdir -p "$home/.config/xx-wm"
    printf '{"theme": "%s"}' "$theme" > "$home/.config/xx-wm/config.json"
    out="$WORK/$theme-$name.png"
    err="$WORK/$theme-$name.err"
    nav="$WORK/nav-$theme-$name.py"
    printf '%s\n' "$navcontent" > "$nav"
    # No-op main-loop turns just before capture: headless WidgetPaintable
    # captures under Broadway flake empty without one (detail came back
    # empty 0/6 without it, all green with it). One per supported delay so
    # the turn lands ~100 ms before the shot; later ones never fire because
    # the capture exits first.
    printf 'from gi.repository import GLib as _GLib\n_GLib.timeout_add(1400, lambda: False)\n_GLib.timeout_add(1900, lambda: False)\n_GLib.timeout_add(2400, lambda: False)\n' >> "$nav"
    keyring_env=""
    if [ "$nokeyring" = "1" ]; then
        keyring_env="XX_AUTH_NO_KEYRING=1"
    fi
    attempt=0
    while [ "$attempt" -lt 5 ]; do
        attempt=$((attempt + 1))
        rm -f "$out"
        # Fresh home per attempt: a stale vault plus a fresh memory keyring
        # renders every row undecryptable.
        rm -rf "$home"
        mkdir -p "$home/.config/xx-wm"
        printf '{"theme": "%s"}' "$theme" > "$home/.config/xx-wm/config.json"
        # shellcheck disable=SC2086
        if env HOME="$home" XDG_CONFIG_HOME="$home/.config" XDG_DATA_HOME="$home/.local/share" \
            XX_AUTH_SCENARIO="$scenario" XX_AUTH_PAGE="$page" \
            $keyring_env \
            $BUSWRAP \
            "$PY" "$LANE/scripts/snap.py" \
            --out "$out" --size "$size" --delay "$delay" \
            --path "$ROOT/src" --path "$LANE/common" \
            --script "$nav" \
            -- -m xx_auth 2>"$err"; then
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

NAV_DETAIL='w = app._auth_window
rows = w.backend.store.get_all()
if rows:
    w.show_detail(rows[0].id)'
NAV_LOCK='w = app._auth_window
w.set_password("snap-lock-1")
w.backend.prefs.lock_enabled = True
from xx_auth import prefs as _pm
_pm.save(w.backend.prefs)
w.lock()'

for theme in $THEMES; do
    snap_one "$theme" "list" "360x720" "demo" "list" "" 1
    snap_one "$theme" "empty" "360x720" "empty" "list" "" 1
    snap_one "$theme" "add" "360x720" "demo" "add" "" 1
    snap_one "$theme" "detail" "360x720" "demo" "list" "$NAV_DETAIL" 1
    snap_one "$theme" "io" "360x720" "demo" "io" "" 1
    snap_one "$theme" "locksettings" "360x720" "demo" "locksettings" "" 1
    snap_one "$theme" "about" "360x720" "demo" "about" "" 1
    snap_one "$theme" "lock" "360x720" "demo" "list" "$NAV_LOCK" 1
    snap_one "$theme" "setup" "360x720" "demo" "list" "" 0 2.5
    snap_one "$theme" "narrow-list" "320x640" "demo" "list" "" 1 2.0
done

snap_one "graphite" "list" "360x720" "demo" "list" "" 1 2.0
snap_one "aura" "list" "360x720" "demo" "list" "" 1 2.0
snap_one "amoled" "tall-list" "600x1333" "demo" "list" "" 1 2.5

for theme in $THEMES; do
    # shellcheck disable=SC2086
    montage -label 'list' "$WORK/$theme-list.png" \
        -label 'empty' "$WORK/$theme-empty.png" \
        -label 'add' "$WORK/$theme-add.png" \
        -label 'detail' "$WORK/$theme-detail.png" \
        -label 'import-export' "$WORK/$theme-io.png" \
        -label 'lock-settings' "$WORK/$theme-locksettings.png" \
        -label 'about' "$WORK/$theme-about.png" \
        -label 'lock' "$WORK/$theme-lock.png" \
        -label 'setup' "$WORK/$theme-setup.png" \
        -tile 3x3 -geometry +4+4 -background black \
        -fill white -pointsize 28 "$OUTDIR/sheet-$theme.png" \
        || fail "montage failed for $theme"
    cp "$WORK/$theme-"*.png "$OUTDIR/"
done
cp "$WORK/graphite-list.png" "$WORK/aura-list.png" "$WORK/amoled-tall-list.png" "$OUTDIR/"
rm -rf "$WORK"
trap - EXIT
echo "wrote $OUTDIR/sheet-{amoled,paper}.png"
