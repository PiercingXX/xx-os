#!/bin/sh
# XX-WM uninstaller: removes what scripts/install.sh and the meson installs
# put on this device and undoes the system changes install.sh made.
# Distro packages stay (other software may use them), and so do your
# settings and data (~/.config/xx-wm, ~/.local/share/xx-wm).
#
#   sh scripts/uninstall.sh [--dry-run] [--keep-apps]
#
# --keep-apps removes only the shell (xx-wm, its keyboard and session
# integration) and keeps the XX apps and the xx_ui library they need.
# Run it from the same checkout install.sh ran from: each project's
# build/meson-logs/install-log.txt lists exactly what meson installed;
# without it the list comes from a fresh meson configure of the source.
# GitHub.com/PiercingXX

set -u

REPO_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
DRY_RUN=0
KEEP_APPS=0

for arg in "$@"; do
    case $arg in
        --dry-run) DRY_RUN=1 ;;
        --keep-apps) KEEP_APPS=1 ;;
        -h|--help)
            echo "Usage: $0 [--dry-run] [--keep-apps]"
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            exit 1
            ;;
    esac
done

# XX_WM_UNINSTALL_ROOT: a staged root (DESTDIR-style) to clean instead of
# this system; it needs no privileges.
ROOT=${XX_WM_UNINSTALL_ROOT:-}
if [ -n "$ROOT" ] || [ "$(id -u)" = 0 ]; then
    SUDO=
elif command -v doas >/dev/null 2>&1; then
    SUDO=doas
elif command -v sudo >/dev/null 2>&1; then
    SUDO=sudo
else
    echo "Need doas or sudo." >&2
    exit 1
fi

STATE_DIR=${XX_APPS_STATE_DIR:-$ROOT/var/lib/xx-apps}
FONT_DIR=${XX_WM_FONT_DIR:-$HOME/.local/share/fonts}
XX_APPS=$(sed -n 's/^XX_APPS="\(.*\)"$/\1/p' "$REPO_DIR/scripts/install.sh")
FAILED=0

warn() {
    echo "warn: $*" >&2
    FAILED=1
}

run() {
    if [ "$DRY_RUN" = 1 ]; then
        echo "would run: $*"
        return 0
    fi
    "$@"
}

as_root() {
    if [ -n "$SUDO" ]; then
        run "$SUDO" "$@"
    else
        run "$@"
    fi
}

is_systemd() {
    [ -d "${XX_WM_SYSTEMD_RUN_DIR:-/run/systemd/system}" ]
}

has_openrc() {
    ! is_systemd && { [ -x /sbin/openrc-run ] || \
        command -v openrc-run >/dev/null 2>&1; }
}

stop_services() {
    if is_systemd; then
        run systemctl --user disable --now xx-wm 2>/dev/null || true
        as_root systemctl disable --now xx-wm-helper-boot.service \
            2>/dev/null || true
    elif has_openrc; then
        as_root rc-update del xx-wm default 2>/dev/null || true
        as_root rc-update del xx-wm-helper-boot boot 2>/dev/null || true
        if command -v tinydm-set-session >/dev/null 2>&1; then
            echo "note: tinydm still starts XX-WM; point it at another session with tinydm-set-session -f -s /usr/share/wayland-sessions/<session>.desktop"
        fi
    fi
}

# Every path meson installed for one project, one per line: files first
# in the log's order, directories meson created included.
installed_paths() {
    _log="$1/build/meson-logs/install-log.txt"
    if [ -f "$_log" ]; then
        grep -v '^#' "$_log"
        return 0
    fi
    command -v meson >/dev/null 2>&1 || return 1
    _tmp=$(mktemp -d) || return 1
    _st=1
    if meson setup "$_tmp/build" "$1" --prefix=/usr >/dev/null 2>&1; then
        meson introspect --installed "$_tmp/build" | python3 -c '
import json, sys
for path in json.load(sys.stdin).values():
    print(sys.argv[1] + path)
' "$ROOT"
        _st=$?
    fi
    rm -rf "$_tmp"
    return $_st
}

# Directories the suite owns are named after it; never remove anything
# else that only became empty.
_owned_dir() {
    case $1 in
        */xx-*|*/xx_*|*/txxt*) return 0 ;;
    esac
    return 1
}

remove_project() {
    _name=$1 _src=$2
    if [ ! -d "$_src" ]; then
        echo "note: $_name: no source tree at $_src (not installed from this checkout)"
        return 0
    fi
    _list=$(mktemp) || return 0
    _dirs=$(mktemp) || { rm -f "$_list"; return 0; }
    if ! installed_paths "$_src" > "$_list" || [ ! -s "$_list" ]; then
        echo "note: $_name: no install log and meson could not list its files; nothing removed"
        rm -f "$_list" "$_dirs"
        return 0
    fi
    echo "Removing $_name..."
    while IFS= read -r _p; do
        [ -n "$_p" ] || continue
        if [ -d "$_p" ] && [ ! -L "$_p" ]; then
            printf '%s\n' "$_p" >> "$_dirs"
            continue
        fi
        if [ -e "$_p" ] || [ -L "$_p" ]; then
            as_root rm -f -- "$_p" || warn "could not remove $_p"
        fi
        _d=${_p%/*}
        while [ -n "$_d" ] && _owned_dir "$_d"; do
            printf '%s\n' "$_d" >> "$_dirs"
            _d=${_d%/*}
        done
    done < "$_list"
    LC_ALL=C sort -r -u "$_dirs" | while IFS= read -r _d; do
        [ -d "$_d" ] || continue
        if _owned_dir "$_d" && [ -d "$_d/__pycache__" ]; then
            as_root rm -rf -- "$_d/__pycache__"
        fi
        if [ -z "$(ls -A "$_d" 2>/dev/null)" ]; then
            as_root rmdir -- "$_d" 2>/dev/null || true
        fi
    done
    rm -f "$_list" "$_dirs"
}

remove_shell_extras() {
    # install.sh copies the power-key drop-in for elogind and, on Halium,
    # the portal override; meson installs the systemd drop-in. All go,
    # then logind needs a restart or reboot.
    for _f in "$ROOT/etc/elogind/logind.conf.d/10-xx-wm-power.conf" \
              "$ROOT/etc/systemd/logind.conf.d/10-xx-wm-power.conf" \
              "$ROOT/etc/xdg/xdg-desktop-portal/xx-wm-portals.conf" \
              "$ROOT/etc/conf.d/xx-wm"; do
        if [ -e "$_f" ]; then
            as_root rm -f -- "$_f" || warn "could not remove $_f"
        fi
    done
    # deploy.sh and the phoc.ini fragment write into the datadir too.
    if [ -d "$ROOT/usr/share/xx-wm" ]; then
        as_root rm -rf -- "$ROOT/usr/share/xx-wm" || \
            warn "could not remove $ROOT/usr/share/xx-wm"
    fi
}

_greetd_configs() {
    printf '%s\n' "$ROOT/etc/greetd/config.toml" "$ROOT/etc/phrog/greetd-config.toml"
    if [ -z "$ROOT" ] && command -v systemctl >/dev/null 2>&1; then
        systemctl cat greetd 2>/dev/null | \
            grep -o -e '-c [^[:space:]]*' -e '--config[ =][^[:space:]]*' | \
            sed 's/^-c //; s/^--config[ =]//'
    fi
}

restore_backups() {
    _greetd_configs | sort -u | while IFS= read -r _cfg; do
        _bak="$_cfg.xx-wm-backup"
        [ -f "$_bak" ] || continue
        if grep -q 'xx-wm-session' "$_cfg" 2>/dev/null; then
            as_root mv -f -- "$_bak" "$_cfg" && \
                echo "Restored $_cfg (XX-WM autologin removed)."
        else
            echo "note: $_cfg no longer starts XX-WM; left as is (the pre-install copy is $_bak)"
        fi
    done
    _osk="$ROOT/usr/share/gnome-shell/osk-layouts/us.json"
    if [ -f "$_osk.xx-wm-backup" ]; then
        as_root mv -f -- "$_osk.xx-wm-backup" "$_osk" && \
            echo "Restored the stock GDM on-screen keyboard."
    fi
}

remove_user_files() {
    _link="$HOME/.local/share/squeekboard/keyboards"
    if [ -L "$_link" ] && [ "$(readlink "$_link")" = /usr/share/xx-wm/squeekboard ]; then
        run rm -f -- "$_link"
    fi
    if [ -f "$FONT_DIR/.xx-wm-fonts" ]; then
        while IFS= read -r _font; do
            case $_font in
                ''|*/*) continue ;;
            esac
            if [ -f "$FONT_DIR/$_font" ]; then
                run rm -f -- "$FONT_DIR/$_font"
            fi
        done < "$FONT_DIR/.xx-wm-fonts"
        run rm -f -- "$FONT_DIR/.xx-wm-fonts"
        if [ -z "$ROOT" ] && command -v fc-cache >/dev/null 2>&1; then
            run fc-cache -f >/dev/null 2>&1 || true
        fi
    fi
    # bootstrap-dots.sh's pinned clone (never the user's ~/piercing-dots).
    if [ -d "$HOME/.cache/piercing-dots" ]; then
        run rm -rf -- "$HOME/.cache/piercing-dots"
    fi
}

remove_input_group() {
    _user=$(id -un)
    _marker="$STATE_DIR/input-group-added"
    if [ -f "$_marker" ] && grep -qx "$_user" "$_marker"; then
        if [ -n "$ROOT" ]; then
            echo "would take '$_user' out of the input group"
        else
            as_root gpasswd -d "$_user" input >/dev/null 2>&1 || \
                as_root deluser "$_user" input >/dev/null 2>&1 || \
                warn "could not take '$_user' out of the input group"
        fi
    elif id -nG "$_user" 2>/dev/null | grep -qw input; then
        echo "note: '$_user' stays in the input group (install.sh did not record adding it)"
    fi
}

forget_installed() {
    [ -d "$STATE_DIR" ] || return 0
    if [ "$KEEP_APPS" = 0 ]; then
        as_root rm -rf -- "$STATE_DIR"
        return 0
    fi
    as_root rm -f -- "$STATE_DIR/input-group-added"
    _json="$STATE_DIR/installed.json"
    [ -f "$_json" ] || return 0
    _tmp=$(mktemp) || return 0
    if python3 - "$_json" > "$_tmp" <<'PYEOF'
import json, sys
with open(sys.argv[1], encoding='utf-8') as handle:
    data = json.load(handle)
for name in ('xx-wm', 'xx-keyboard'):
    data.pop(name, None)
json.dump(data, sys.stdout, indent=2, sort_keys=True)
sys.stdout.write('\n')
PYEOF
    then
        as_root cp -- "$_tmp" "$_json"
    fi
    rm -f "$_tmp"
}

refresh_caches() {
    if [ -d "$ROOT/usr/share/glib-2.0/schemas" ] && \
       command -v glib-compile-schemas >/dev/null 2>&1; then
        as_root glib-compile-schemas "$ROOT/usr/share/glib-2.0/schemas" \
            >/dev/null 2>&1 || true
    fi
    for _tool in gtk-update-icon-cache gtk4-update-icon-cache; do
        if [ -d "$ROOT/usr/share/icons/hicolor" ] && \
           command -v "$_tool" >/dev/null 2>&1; then
            as_root "$_tool" -q -t -f "$ROOT/usr/share/icons/hicolor" \
                2>/dev/null && break
        fi
    done
    if [ -d "$ROOT/usr/share/applications" ] && \
       command -v update-desktop-database >/dev/null 2>&1; then
        as_root update-desktop-database -q "$ROOT/usr/share/applications" \
            2>/dev/null || true
    fi
}

stop_services
restore_backups
remove_project xx-wm "$REPO_DIR/launcher"
remove_shell_extras
remove_project xx-keyboard "$REPO_DIR/../xx-keyboard"
if [ "$KEEP_APPS" = 0 ]; then
    for _app in $XX_APPS; do
        remove_project "$_app" "$REPO_DIR/../$_app"
    done
    remove_project common "$REPO_DIR/../common"
fi
remove_user_files
remove_input_group
forget_installed
refresh_caches

echo "XX-WM removed. Reboot (or restart logind) so the power key goes back to the system default."
if [ "$FAILED" = 1 ]; then
    echo "Some steps failed; see the warnings above." >&2
    exit 1
fi
exit 0
