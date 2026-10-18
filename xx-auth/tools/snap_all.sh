#!/bin/sh
# Seeded screenshots of every xx-auth screen in amoled + paper at the FLX1
# canvas (450x1000) and a 360x800 phone, plus graphite/aura spot checks and
# phone-fit probes (320 px wide, large text at 1.5):
#   xx-auth/tools/snap_all.sh OUTDIR
# Writes sheet-{amoled,paper}-{450,360}{,-flows}.png and sheet-fit{,-2}.png.
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
    # $1=theme $2=name $3=size $4=scenario $5=page $6=nav $7=nokeyring
    # $8=delay $9=extra config json (leading comma)
    theme=$1 name=$2 size=$3 scenario=$4 page=$5 navcontent=$6 nokeyring=$7
    delay=${8:-1.5}
    extra=${9:-}
    home="$WORK/home-$theme-$name"
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
        printf '{"theme": "%s"%s}' "$theme" "$extra" > "$home/.config/xx-wm/config.json"
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
# The seeded vault is keyring-backed: the app lock is a UI gate on top,
# set like Settings > App lock does (setup_password refuses a vault with
# rows, by design).
NAV_LOCK='w = app._auth_window
from xx_auth import accounts as _am
from xx_auth import keystore as _ks
_am.change_password(w.backend.store, w.backend.prefs, _ks.MemoryKeyring(), "", "snap-lock-1")
w.backend.prefs.lock_enabled = True
from xx_auth import prefs as _pm
_pm.save(w.backend.prefs)
w.lock()'

HELPERS="exec(open('$ROOT/tools/snap_surface.py').read())"
NAV_SEARCH='w = app._auth_window
w._pages["list"].toggle_search()'
NAV_MANUAL='w = app._auth_window
page = w.stack.get_child_by_name("add")
def _find(x, label):
    if isinstance(x, Gtk.Button) and x.get_label() == label:
        return x
    c = x.get_first_child()
    while c is not None:
        r = _find(c, label)
        if r is not None:
            return r
        c = c.get_next_sibling()
    return None
_find(page, "Enter details").emit("clicked")'
NAV_DETAIL_BOTTOM="$NAV_DETAIL
$HELPERS
page = w.stack.get_child_by_name('detail')
def _find(x, label):
    if isinstance(x, Gtk.Button) and x.get_label() == label:
        return x
    c = x.get_first_child()
    while c is not None:
        r = _find(c, label)
        if r is not None:
            return r
        c = c.get_next_sibling()
    return None
_find(page, 'Reveal secret').emit('clicked')
scroll_to_bottom('detail')"
NAV_IO_TYPED='w = app._auth_window
page = w.stack.get_child_by_name("io")
def _entries(x, out):
    if isinstance(x, Gtk.Entry):
        out.append(x)
    c = x.get_first_child()
    while c is not None:
        _entries(c, out)
        c = c.get_next_sibling()
    return out
_entries(page, [])[0].set_text("snap-export-1")'

# The overflow menu is its own surface: painted to menu-<theme><suffix>.png.
nav_menu() {
    printf '%s\nw = app._auth_window\nw._pages["list"].show_menu()\nGLib.timeout_add(300, lambda: snap_widget(w._pages["list"].menu_popover, "%s"))\n' \
        "$HELPERS" "$1"
}

# Every screen at one size: $1=theme $2=size $3=suffix
snap_screens() {
    snap_one "$1" "list$3" "$2" "demo" "list" "" 1
    snap_one "$1" "empty$3" "$2" "empty" "list" "" 1
    snap_one "$1" "add$3" "$2" "demo" "add" "" 1
    snap_one "$1" "detail$3" "$2" "demo" "list" "$NAV_DETAIL" 1
    snap_one "$1" "io$3" "$2" "demo" "io" "" 1
    snap_one "$1" "locksettings$3" "$2" "demo" "locksettings" "" 1
    snap_one "$1" "about$3" "$2" "demo" "about" "" 1
    snap_one "$1" "lock$3" "$2" "demo" "list" "$NAV_LOCK" 1
    snap_one "$1" "setup$3" "$2" "demo" "list" "" 0 2.5
    snap_one "$1" "search$3" "$2" "demo" "list" "$NAV_SEARCH" 1
    snap_one "$1" "menuwin$3" "$2" "demo" "list" "$(nav_menu "$WORK/$1-menu$3.png")" 1
    snap_one "$1" "manual$3" "$2" "demo" "add" "$NAV_MANUAL" 1
    snap_one "$1" "detailbottom$3" "$2" "demo" "list" "$NAV_DETAIL_BOTTOM" 1
    snap_one "$1" "iotyped$3" "$2" "demo" "io" "$NAV_IO_TYPED" 1
}

sheet() {
    # $1=theme $2=suffix
    montage -label 'list' "$WORK/$1-list$2.png" \
        -label 'empty' "$WORK/$1-empty$2.png" \
        -label 'add' "$WORK/$1-add$2.png" \
        -label 'detail' "$WORK/$1-detail$2.png" \
        -label 'import-export' "$WORK/$1-io$2.png" \
        -label 'lock-settings' "$WORK/$1-locksettings$2.png" \
        -label 'about' "$WORK/$1-about$2.png" \
        -label 'lock' "$WORK/$1-lock$2.png" \
        -label 'setup' "$WORK/$1-setup$2.png" \
        -tile 3x3 -geometry +4+4 -background black \
        -fill white -pointsize 28 "$OUTDIR/sheet-$1$2.png" \
        || fail "montage failed for $1$2"
    montage -label 'search' "$WORK/$1-search$2.png" \
        -label 'menu' "$WORK/$1-menu$2.png" \
        -label 'manual entry' "$WORK/$1-manual$2.png" \
        -label 'detail, revealed' "$WORK/$1-detailbottom$2.png" \
        -label 'export password' "$WORK/$1-iotyped$2.png" \
        -tile 5x1 -geometry +4+4 -background black \
        -fill white -pointsize 28 "$OUTDIR/sheet-$1$2-flows.png" \
        || fail "montage failed for $1$2 flows"
}

for theme in $THEMES; do
    snap_screens "$theme" "450x1000" "-450"
    snap_screens "$theme" "360x800" "-360"
done

# Theme spot checks beyond the two sheet themes.
snap_one "graphite" "list-450" "450x1000" "demo" "list" "" 1 2.0
snap_one "aura" "detail-450" "450x1000" "demo" "list" "$NAV_DETAIL" 1 2.0

# Phone-fit probes: 320 px wide, and large text (scale 1.5) at 360x800.
LARGE=', "text_size_scale": 1.5'
snap_one "amoled" "fit320-list" "320x640" "demo" "list" "" 1 2.0
snap_one "amoled" "fit320-add" "320x640" "demo" "add" "" 1 2.0
snap_one "amoled" "fit320-detail" "320x640" "demo" "list" "$NAV_DETAIL" 1 2.0
snap_one "paper" "large-list" "360x800" "demo" "list" "" 1 2.0 "$LARGE"
snap_one "paper" "large-add" "360x800" "demo" "add" "" 1 2.0 "$LARGE"
snap_one "paper" "large-detail" "360x800" "demo" "list" "$NAV_DETAIL" 1 2.0 "$LARGE"
snap_one "amoled" "fit320-io" "320x640" "demo" "io" "$NAV_IO_TYPED" 1 2.0
snap_one "amoled" "fit320-locksettings" "320x640" "demo" "locksettings" "" 1 2.0
snap_one "amoled" "fit320-setup" "320x640" "demo" "list" "" 0 2.5
snap_one "paper" "large-io" "360x800" "demo" "io" "$NAV_IO_TYPED" 1 2.0 "$LARGE"
snap_one "paper" "large-locksettings" "360x800" "demo" "locksettings" "" 1 2.0 "$LARGE"
snap_one "paper" "large-detailbottom" "360x800" "demo" "list" "$NAV_DETAIL_BOTTOM" 1 2.0 "$LARGE"
snap_one "paper" "large-manual" "360x800" "demo" "add" "$NAV_MANUAL" 1 2.0 "$LARGE"
snap_one "paper" "large-lock" "360x800" "demo" "list" "$NAV_LOCK" 1 2.0 "$LARGE"
snap_one "paper" "large-about" "360x800" "demo" "about" "" 1 2.0 "$LARGE"

for theme in $THEMES; do
    sheet "$theme" "-450"
    sheet "$theme" "-360"
    cp "$WORK/$theme-"*.png "$OUTDIR/"
done
montage -label 'graphite list' "$WORK/graphite-list-450.png" \
    -label 'aura detail' "$WORK/aura-detail-450.png" \
    -label '320 list' "$WORK/amoled-fit320-list.png" \
    -label '320 add' "$WORK/amoled-fit320-add.png" \
    -label '320 detail' "$WORK/amoled-fit320-detail.png" \
    -label 'large list' "$WORK/paper-large-list.png" \
    -label 'large add' "$WORK/paper-large-add.png" \
    -label 'large detail' "$WORK/paper-large-detail.png" \
    -tile 4x2 -geometry +4+4 -background black \
    -fill white -pointsize 28 "$OUTDIR/sheet-fit.png" \
    || fail "montage failed for fit sheet"
montage -label '320 export' "$WORK/amoled-fit320-io.png" \
    -label '320 app lock' "$WORK/amoled-fit320-locksettings.png" \
    -label '320 setup' "$WORK/amoled-fit320-setup.png" \
    -label 'large export' "$WORK/paper-large-io.png" \
    -label 'large app lock' "$WORK/paper-large-locksettings.png" \
    -label 'large revealed' "$WORK/paper-large-detailbottom.png" \
    -label 'large manual' "$WORK/paper-large-manual.png" \
    -label 'large lock' "$WORK/paper-large-lock.png" \
    -label 'large about' "$WORK/paper-large-about.png" \
    -tile 5x2 -geometry +4+4 -background black \
    -fill white -pointsize 28 "$OUTDIR/sheet-fit-2.png" \
    || fail "montage failed for fit sheet 2"
cp "$WORK/graphite-"*.png "$WORK/aura-"*.png "$OUTDIR/"
cp "$WORK/"*fit320*.png "$WORK/"*large*.png "$OUTDIR/"
rm -rf "$WORK"
trap - EXIT
echo "wrote $OUTDIR/sheet-{amoled,paper}-{450,360}{,-flows}.png $OUTDIR/sheet-fit{,-2}.png"
