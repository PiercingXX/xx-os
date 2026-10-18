#!/bin/sh
# Render the shell (home, drawer, lock, first boot, menus) for every theme.
#   xx-wm/tools/snap_shell.sh OUTDIR [theme ...]
# Default themes: amoled paper forest aura burgundy.
# Needs gtk4-broadwayd + ImageMagick montage. HOME/XDG_* point at seeded
# temp dirs so the real config is untouched. Every render must be exactly
# the snap size (360x720; SNAP_SIZE=450x1000 renders the FLX1) with zero
# `snap: WARNING`s or the run fails. SNAP_TEXT_SCALE=1.5 seeds large text.
set -u
REPO=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
OUTDIR=${1:?usage: snap_shell.sh OUTDIR [theme ...]}
SIZE=${SNAP_SIZE:-360x720}
SW=${SIZE%x*} SH=${SIZE#*x}
TEXT_SCALE=${SNAP_TEXT_SCALE:-1.0}
shift || true
if [ "$#" -gt 0 ]; then
    THEMES="$*"
else
    THEMES="amoled paper forest aura burgundy"
fi
mkdir -p "$OUTDIR"
WORK=$(mktemp -d)
if [ "${KEEP_WORK:-0}" = 1 ]; then
    trap 'echo "work kept: $WORK"' EXIT INT TERM
else
    trap 'rm -rf "$WORK"' EXIT INT TERM
fi
export PATH="$HOME/.local/bin:$PATH"
# Private session bus per shot: the shell is a single-instance app, so a
# shell already on the user bus (or a parallel run) would swallow the launch.
BUSWRAP=""
command -v dbus-run-session >/dev/null 2>&1 && BUSWRAP="dbus-run-session --"

APPS="Phone:phone Messages:messages Contacts:contacts Calendar:calendar Camera:camera Maps:maps Notes:notes Clocks:clocks Files:files Settings:settings Weather:weather Vitals:vitals Email:email Share:share Calculator:calculator Photos:photos Music:music Browser:browser Terminal:terminal Software:software"

seed_home() {
    # $1 = home dir, $2 = theme, $3 = with_config (1 = shell run, 0 = wizard run)
    _home=$1 _theme=$2 _cfg=$3
    mkdir -p "$_home/.config/xx-wm" "$_home/.local/share/applications" "$_home/.cache"
    for pair in $APPS; do
        _name=${pair%%:*} _id=${pair##*:}
        printf '[Desktop Entry]\nName=%s\nExec=true\nType=Application\n' "$_name" \
            > "$_home/.local/share/applications/${_id}.desktop"
    done
    # Installed days ago, like a phone's apps: no "new" dot on every row.
    touch -d '3 days ago' "$_home/.local/share/applications/"*.desktop
    if [ "$_cfg" = 1 ]; then
        # pin_hash is the legacy bare-sha256 of "123456": a set PIN keeps
        # the lock screen on its keypad instead of auto-unlocking.
        cat > "$_home/.config/xx-wm/config.json" <<EOF
{"theme": "$_theme", "text_size_scale": $TEXT_SCALE,
 "pin_hash": "8d969eef6ecad3c29a3a629280e686cf0c3f5d5a86aff3ca12020c923adc6c92",
 "home_slots": [
  {"type": "folder", "label": "Comms", "app_id": null, "cmd": null,
   "folder": [{"label": "Phone", "app_id": "phone.desktop"},
              {"label": "Messages", "app_id": "messages.desktop"},
              {"label": "Contacts", "app_id": "contacts.desktop"}]},
  {"type": "app", "label": "Notes", "app_id": "notes.desktop", "cmd": null, "folder": null},
  {"type": "folder", "label": "Tools", "app_id": null, "cmd": null,
   "folder": [{"label": "Camera", "app_id": "camera.desktop"},
              {"label": "Maps", "app_id": "maps.desktop"},
              {"label": "Clocks", "app_id": "clocks.desktop"}]},
  {"type": "app", "label": "Calendar", "app_id": "calendar.desktop", "cmd": null, "folder": null},
  {"type": "app", "label": "Weather", "app_id": "weather.desktop", "cmd": null, "folder": null}
]}
EOF
    else
        # Wizard run: setup not complete, only the text size seeded.
        printf '{"setup_complete": false, "text_size_scale": %s}\n' \
            "$TEXT_SCALE" > "$_home/.config/xx-wm/config.json"
    fi
}

nav() {
    # $1 = name, body arrives on stdin (heredoc)
    _name=$1
    sed "s/360, 720/$SW, $SH/g" > "$WORK/nav-$_name.py"
}

nav home <<'EOF'
win.unfullscreen()
win.set_default_size(360, 720)
win.set_size_request(360, 720)
win.go_home()
EOF
nav folder <<'EOF'
win.unfullscreen()
win.set_default_size(360, 720)
win.set_size_request(360, 720)
win.go_home()
from gi.repository import Gtk
slots = win.config.home_slots
win._home_launcher._toggle_folder(0, slots[0])
# Headless only: Revealer slide animations never paint under the
# snapshotter, so reveal the open drop-down without a transition.
child = win._home_launcher.get_first_child()
while child is not None:
    if isinstance(child, Gtk.Revealer):
        child.set_transition_type(Gtk.RevealerTransitionType.NONE)
        child.set_reveal_child(True)
    child = child.get_next_sibling()
EOF
nav drawer <<'EOF'
win.unfullscreen()
win.set_default_size(360, 720)
win.set_size_request(360, 720)
win.stack.set_visible_child_name('apps')
EOF
nav drawer_search <<'EOF'
win.unfullscreen()
win.set_default_size(360, 720)
win.set_size_request(360, 720)
win.stack.set_visible_child_name('apps')
win.apps_search.set_text('ca')
EOF
nav lock <<'EOF'
win.unfullscreen()
win.set_default_size(360, 720)
win.set_size_request(360, 720)
from gi.repository import Gtk
win._show_lock_screen()
lock = win._lock_screen
lock.unfullscreen()
lock.set_default_size(360, 720)
lock.set_size_request(360, 720)
# Headless only: instant reveal, the slide animation never paints here.
lock._pin_revealer.set_transition_type(Gtk.RevealerTransitionType.NONE)
lock._swipe_up()
# Headless only: the keypad sizes from the output's geometry, and
# Broadway's virtual monitor is desktop-sized; size it for the phone.
import lock_screen as _ls
_w, _h = (360, 720)
lock._apply_keypad_size(_ls.keypad_key_size(
    _w - _ls._KEYPAD_SIDE_MARGINS,
    _h - _ls._COLLAPSED_CLOCK_RESERVE - _ls._KEYPAD_HEIGHT_MARGIN))
win.set_visible(False)
EOF
nav menu <<'EOF'
win.unfullscreen()
win.set_default_size(360, 720)
win.set_size_request(360, 720)
win.stack.set_visible_child_name('apps')
from gi.repository import Gtk
# The real long-press menu and rename prompt, as the in-window sheets
# the phone shows (no hand-built stand-ins).
Gtk.Settings.get_default().set_property('gtk-enable-animations', False)
entry = next(e for e in win.app_index.entries if e.app_id == 'camera.desktop')
win.item_actions.show_app_menu(win.apps_list, entry)
EOF
nav rename <<'EOF'
win.unfullscreen()
win.set_default_size(360, 720)
win.set_size_request(360, 720)
win.stack.set_visible_child_name('apps')
from gi.repository import Gtk
Gtk.Settings.get_default().set_property('gtk-enable-animations', False)
entry = next(e for e in win.app_index.entries if e.app_id == 'camera.desktop')
win.item_actions._show_rename(win.apps_list, entry)
EOF

# Wizard nav scripts are parametrized by theme at run time (first window
# is the wizard; preview the theme, then jump to the page).
WIZ_PAGES="welcome pin pin_confirm theme status_bar timezone tour_swipe_up tour_swipe_down tour_swipe_side info_shade info_focus info_files"
wiz_nav() {
    # $1 = theme, $2 = page
    _t=$1 _p=$2
    f="$WORK/nav-wiz-$_t-$_p.py"
    {
        echo "win.unfullscreen()"
        echo "win.set_default_size($SW, $SH)"
        echo "win.set_size_request($SW, $SH)"
        echo "win._on_theme_row(None, '$_t')"
        echo "win._stack.set_visible_child_name('$_p')"
        if [ "$_p" = pin ]; then
            echo "win._pin_buf = '123'"
            printf 'win._pin_dots.set_text(%s)\n' "'●●●'"
        fi
        if [ "$_p" = pin_confirm ]; then
            echo "win._confirm_buf = '12345'"
            printf 'win._confirm_dots.set_text(%s)\n' "'●●●●●'"
        fi
    } > "$f"
}

SHELL_SHOTS="home folder drawer drawer_search lock menu rename"
run_one() {
    # $1 = home, $2 = out png, $3 = nav file
    _home=$1 _out=$2 _nav=$3
    _err="$WORK/snap.err"
    # The headless rig rarely tears the very first frame (render_texture
    # returns None); retry a few times before calling the run failed.
    _try=0
    while [ "$_try" -lt 5 ]; do
        _try=$((_try + 1))
        HOME="$_home" XDG_CONFIG_HOME="$_home/.config" \
            XDG_DATA_HOME="$_home/.local/share" \
            XDG_CACHE_HOME="$_home/.cache" \
            XDG_DATA_DIRS="$_home/.local/share" \
            $BUSWRAP python3 "$REPO/scripts/snap.py" --out "$_out" --delay 1.0 --size "$SIZE" \
            --path "$REPO/common" --path "$REPO/xx-wm/launcher/src" \
            --script "$_nav" -- "$REPO/xx-wm/launcher/src/main.py" 2>"$_err" \
            && [ -s "$_out" ] && break
        echo "snap retry $_try: $_out"
        sleep 1
    done
    if [ ! -f "$_out" ]; then
        echo "snap failed: $_out"
        tail -n 20 "$_err"
        return 1
    fi
    if grep -q 'snap: WARNING' "$_err"; then
        echo "snap size mismatch: $_out"
        grep 'snap: WARNING' "$_err"
        return 1
    fi
}

for theme in $THEMES; do
    echo "=== $theme"
    SHOME="$WORK/home-$theme"
    seed_home "$SHOME" "$theme" 1
    for shot in $SHELL_SHOTS; do
        run_one "$SHOME" "$WORK/$theme-$shot.png" "$WORK/nav-$shot.py" || exit 1
    done
    WHOME="$WORK/wizhome-$theme"
    seed_home "$WHOME" "$theme" 0
    for page in $WIZ_PAGES; do
        wiz_nav "$theme" "$page"
        run_one "$WHOME" "$WORK/$theme-firstboot-$page.png" \
            "$WORK/nav-wiz-$theme-$page.py" || exit 1
    done
    # One labeled sheet per theme.
    # shellcheck disable=SC2086
    montage $(
        for shot in $SHELL_SHOTS; do echo "-label $shot $WORK/$theme-$shot.png"; done
        for page in $WIZ_PAGES; do echo "-label firstboot-$page $WORK/$theme-firstboot-$page.png"; done
    ) -tile 3x -geometry "$SIZE+4+4" -background black \
        -fill white -pointsize 20 "$OUTDIR/sheet-$theme.png" \
        || { echo "montage failed: $theme"; exit 1; }
    echo "wrote $OUTDIR/sheet-$theme.png"
done
