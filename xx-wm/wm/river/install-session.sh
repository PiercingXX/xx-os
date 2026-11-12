#!/bin/sh
# Install xxwm and the "XX-WM (river)" greeter session next to the phoc
# one, after `sh build.sh --install` and `cargo build --release` in wm/.
# The session needs wlr-randr for output scales (Debian: wlr-randr).
#   PREFIX     install prefix of river and xxwm (default: /usr/local)
#   XXWM_BIN   xxwm binary (default: ../target/release/xxwm)
set -eu
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
prefix=${PREFIX:-/usr/local}
xxwm=${XXWM_BIN:-$here/../target/release/xxwm}
sessions=${XX_WM_SESSIONS_DIR:-/usr/share/wayland-sessions}

if [ "$(id -u)" = 0 ]; then
    sudo=
elif command -v sudo >/dev/null 2>&1; then
    sudo=sudo
else
    sudo=doas
fi

[ -x "$xxwm" ] || { echo "no xxwm at $xxwm: cargo build --release in wm/" >&2; exit 1; }
[ -x "$prefix/bin/river" ] || { echo "no river in $prefix/bin: sh build.sh --install" >&2; exit 1; }

$sudo install -Dm755 "$xxwm" "$prefix/bin/xxwm"
$sudo install -Dm755 "$here/session/xx-wm-river-session" "$prefix/libexec/xx-wm-river-session"
$sudo install -Dm755 "$here/session/xx-wm-river-init" "$prefix/libexec/xx-wm-river-init"
sed "s|^Exec=.*|Exec=$prefix/libexec/xx-wm-river-session|" \
    "$here/session/xx-wm-river.desktop" >"${TMPDIR:-/tmp}/xx-wm-river.desktop.$$"
$sudo install -Dm644 "${TMPDIR:-/tmp}/xx-wm-river.desktop.$$" "$sessions/xx-wm-river.desktop"
rm -f "${TMPDIR:-/tmp}/xx-wm-river.desktop.$$"
command -v wlr-randr >/dev/null 2>&1 || \
    echo "warn: wlr-randr not found; outputs stay at scale 1 on river" >&2
echo "installed: pick \"XX-WM (river)\" in the greeter"
