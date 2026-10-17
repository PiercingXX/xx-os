#!/bin/sh
# XX-WM — default app set (todo.md Workstream 17).
# Called from install.sh: apps.sh <pkg:apk|apt|pacman> <sudo:doas|sudo>
# Every item is guarded — one missing package never hard-fails the menu.
# GitHub.com/PiercingXX

set -u

PKG=${1:-}
SUDO=${2:-}
if [ -z "$PKG" ] || [ -z "$SUDO" ]; then
    if command -v apk >/dev/null 2>&1; then
        PKG=apk
    elif command -v apt >/dev/null 2>&1; then
        PKG=apt
    elif command -v pacman >/dev/null 2>&1; then
        PKG=pacman
    else
        PKG=apt
    fi
    if command -v doas >/dev/null 2>&1; then SUDO=doas; else SUDO=sudo; fi
fi

APPDIR="$HOME/.local/share/applications"
mkdir -p "$APPDIR"

pkg_install() {
    if [ "$PKG" = apk ]; then
        $SUDO apk add "$@" || echo "warn: install failed: $*" >&2
    elif [ "$PKG" = pacman ]; then
        $SUDO pacman -S --needed --noconfirm "$@" || echo "warn: install failed: $*" >&2
    else
        $SUDO apt install -y "$@" || echo "warn: install failed: $*" >&2
    fi
}

find_browser() {
    for b in waterfox firefox firefox-esr; do
        if command -v "$b" >/dev/null 2>&1; then
            echo "$b"
            return 0
        fi
    done
    return 1
}

ask() {
    # ask "<prompt>" "<default>"
    if command -v whiptail >/dev/null 2>&1; then
        whiptail --inputbox "$1" 0 0 "$2" 3>&1 1>&2 2>&3
    else
        printf '%s [%s]: ' "$1" "$2" >&2
        read -r _ans
        echo "${_ans:-$2}"
    fi
}

pwa_desktop() {
    # pwa_desktop <id> <name> <url>
    _browser=$(find_browser) || { echo "warn: no browser for $2 PWA" >&2; return 1; }
    # Encode the URL for a double-quoted Exec argument (Desktop Entry Spec):
    # %% for field codes, then escape " ` $ \ with a backslash. Every
    # backslash is doubled once more because the KeyFile layer consumes one
    # level of \\ escapes before the Exec line is parsed; the same doubling
    # keeps the unquoted heredoc below from expanding $ and ` as shell syntax.
    _url=$(printf '%s' "$3" | sed \
        -e 's/%/%%/g' \
        -e 's/\\/\\\\\\\\/g' \
        -e 's/"/\\\\"/g' \
        -e 's/`/\\\\`/g' \
        -e 's/\$/\\\\$/g')
    cat > "$APPDIR/$1.desktop" << EOF
[Desktop Entry]
Type=Application
Name=$2
Exec=$_browser --new-window "$_url"
Terminal=false
Categories=Network;
EOF
    echo "$2 PWA entry written."
}

# --- 17.1 Browser -----------------------------------------------------------
# Phones (apk/apt): Firefox ESR + mobile-config. Tablet (pacman/x86_64):
# Waterfox if a configured repo package exists, else extra's firefox.
# Never pull AUR via yay.
echo "Installing browser..."
BROWSER_DESKTOP=
if [ "$PKG" = pacman ]; then
    if pacman -Si waterfox >/dev/null 2>&1; then
        pkg_install waterfox
        BROWSER_DESKTOP=waterfox.desktop
    elif pacman -Si waterfox-bin >/dev/null 2>&1; then
        pkg_install waterfox-bin
        BROWSER_DESKTOP=waterfox-bin.desktop
    else
        pkg_install firefox
        BROWSER_DESKTOP=firefox.desktop
    fi
else
    pkg_install firefox-esr mobile-config-firefox
    BROWSER_DESKTOP=firefox-esr.desktop
fi
if [ -n "$BROWSER_DESKTOP" ] && command -v xdg-settings >/dev/null 2>&1; then
    xdg-settings set default-web-browser "$BROWSER_DESKTOP" 2>/dev/null || true
fi

# --- 17.2 Calculator --------------------------------------------------------
pkg_install gnome-calculator

# --- 17.3 Tailscale ---------------------------------------------------------
# Package per distro, its service under the running init, then make this
# user the Tailscale operator so the shade tile toggles it without root.
# Sign-in happens from the tile (it opens the login page).
ts_systemd() { [ -d /run/systemd/system ]; }
ts_curl() { command -v curl >/dev/null 2>&1 || pkg_install curl ca-certificates; }

ts_apt_repo() {
    # Debian/Ubuntu and derivatives (FuriOS, Mobian, PureOS) through
    # Tailscale's own repo: Debian itself does not package tailscale.
    ts_curl
    . /etc/os-release
    _ts_key=$(mktemp)
    for _ts_dist in ${ID:-} ${ID_LIKE:-}; do
        case "$_ts_dist" in debian|ubuntu) ;; *) continue ;; esac
        _ts_code=${VERSION_CODENAME:-}
        if [ "$_ts_dist" = debian ]; then
            # Derivatives (FuriOS) leave VERSION_CODENAME empty; Debian's
            # own codename is in DEBIAN_CODENAME or /etc/debian_version
            # ("forky/sid" on testing).
            [ -n "${DEBIAN_CODENAME:-}" ] && _ts_code=$DEBIAN_CODENAME
            [ -n "$_ts_code" ] || _ts_code=$(cut -d/ -f1 /etc/debian_version 2>/dev/null)
            case "$_ts_code" in *[!a-z]*|'') _ts_code= ;; esac
        fi
        [ -n "$_ts_code" ] || continue
        _ts_base="https://pkgs.tailscale.com/stable/$_ts_dist/$_ts_code"
        curl -fsSL "$_ts_base.noarmor.gpg" >"$_ts_key" 2>/dev/null || continue
        $SUDO install -m 644 "$_ts_key" /usr/share/keyrings/tailscale-archive-keyring.gpg
        curl -fsSL "$_ts_base.tailscale-keyring.list" | $SUDO tee /etc/apt/sources.list.d/tailscale.list >/dev/null
        $SUDO apt-get update -qq && $SUDO apt-get install -y tailscale && { rm -f "$_ts_key"; return 0; }
    done
    rm -f "$_ts_key"
    return 1
}

ts_static() {
    # Last resort: Tailscale's static build, newest version from its index.
    case "$(uname -m)" in
        aarch64|arm64) _ts_arch=arm64 ;;
        armv7*|armv6*) _ts_arch=arm ;;
        x86_64) _ts_arch=amd64 ;;
        *) echo "warn: no Tailscale build for $(uname -m)" >&2; return 1 ;;
    esac
    ts_curl
    _ts_ver=$(curl -fsSL 'https://pkgs.tailscale.com/stable/?mode=json' |
        sed -n 's/.*"TarballsVersion": *"\([^"]*\)".*/\1/p')
    [ -n "$_ts_ver" ] || { echo "warn: Tailscale version lookup failed" >&2; return 1; }
    _ts_dir="tailscale_${_ts_ver}_${_ts_arch}"
    _ts_tmp=$(mktemp -d)
    curl -fsSL "https://pkgs.tailscale.com/stable/${_ts_dir}.tgz" | tar xz -C "$_ts_tmp" || {
        echo "warn: Tailscale download failed" >&2; rm -rf "$_ts_tmp"; return 1; }
    $SUDO install -m 755 "$_ts_tmp/$_ts_dir/tailscale" "$_ts_tmp/$_ts_dir/tailscaled" /usr/local/bin/
    if ts_systemd; then
        sed 's|/usr/sbin/tailscaled|/usr/local/bin/tailscaled|g' \
            "$_ts_tmp/$_ts_dir/systemd/tailscaled.service" |
            $SUDO tee /etc/systemd/system/tailscaled.service >/dev/null
        $SUDO install -m 644 "$_ts_tmp/$_ts_dir/systemd/tailscaled.defaults" /etc/default/tailscaled
        $SUDO systemctl daemon-reload
    fi
    rm -rf "$_ts_tmp"
}

install_tailscale() {
    if ! command -v tailscale >/dev/null 2>&1; then
        case "$PKG" in
            apk)
                if ts_systemd; then pkg_install tailscale tailscale-systemd
                else pkg_install tailscale tailscale-openrc; fi ;;
            pacman) pkg_install tailscale ;;
            apt) ts_apt_repo ;;
        esac
    fi
    command -v tailscale >/dev/null 2>&1 || ts_static || return 1
    if ts_systemd; then
        $SUDO systemctl enable --now tailscaled
    elif command -v rc-service >/dev/null 2>&1; then
        $SUDO rc-update add tailscale default
        $SUDO rc-service tailscale start
    fi
    # tailscaled needs a moment before it accepts prefs
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        tailscale status --json 2>/dev/null | grep -q BackendState && break
        sleep 1
    done
    if $SUDO tailscale set --operator="$(id -un)"; then
        echo "Tailscale ready — sign in from the Tailscale tile in the shade."
    else
        echo "warn: tailscaled is not answering; start it, then run: $SUDO tailscale set --operator=$(id -un)" >&2
    fi
}

install_tailscale || echo "warn: Tailscale was not installed" >&2

# --- 17.4 Skippy (mobile PWA over Tailscale) --------------------------------
SKIPPY_HOST=$(ask "Skippy host (Tailscale IP or name)" "skippy")
if [ -n "$SKIPPY_HOST" ]; then
    pwa_desktop skippy "Skippy" "http://${SKIPPY_HOST}:8282/mobile/" || true
fi

# --- 17.5 Audiobookshelf (PWA path first — no Waydroid dependency) ----------
ABS_URL=$(ask "Audiobookshelf server URL (blank to skip)" "")
if [ -n "$ABS_URL" ]; then
    pwa_desktop audiobookshelf "Audiobook" "$ABS_URL" || true
fi

# --- 17.6 Waydroid (install only — init/microG is device work) --------------
# 3 GiB = 3145728 kB. The smoke tablet is 1.8 GiB; do not install there.
_mem_kb=0
if [ -r /proc/meminfo ]; then
    _mem_kb=$(awk '/^MemTotal:/ {print $2; exit}' /proc/meminfo 2>/dev/null || true)
fi
case $_mem_kb in
    ''|*[!0-9]*) _mem_kb=0 ;;
esac
if [ "$_mem_kb" -gt 0 ] && [ "$_mem_kb" -lt 3145728 ]; then
    echo "SKIP: Waydroid needs ≥3 GiB RAM (MemTotal=${_mem_kb} kB)"
elif command -v waydroid >/dev/null 2>&1; then
    echo "Waydroid already installed."
elif [ "$PKG" = apk ] && $SUDO apk add waydroid 2>/dev/null; then
    echo "Waydroid installed via apk."
elif [ "$PKG" = apt ]; then
    if ! $SUDO apt install -y waydroid 2>/dev/null; then
        pkg_install curl ca-certificates
        curl -s https://repo.waydro.id | $SUDO bash || echo "warn: waydro.id repo setup failed" >&2
        pkg_install waydroid
    fi
else
    echo "warn: Waydroid unavailable on this distro" >&2
fi
echo "Waydroid init + microG image + Android apps are device work — see devices/ notes."

# --- 17.7 Sharing backends (wm_services) --------------------------------------
# Verified names: kdeconnect/syncthing on apk+apt+pacman; adbd on apt
# only (`adbd`, older name `android-tools-adbd`); apk/pacman
# android-tools ships no adbd daemon, so the ADB row hides there.
# openssh + nftables are the SSH/firewall transports (nftables is the
# firewall backend everywhere; ufw is only a fallback). All guarded.
echo "Installing sharing backends..."
case "$PKG" in
    apk) pkg_install openssh nftables kdeconnect syncthing ;;
    pacman) pkg_install openssh nftables kdeconnect syncthing ;;
    *) pkg_install openssh-server nftables kdeconnect syncthing ;;
esac
if [ "$PKG" = apt ]; then
    pkg_install adbd || pkg_install android-tools-adbd || \
        echo "warn: no adbd package — wireless debugging (ADB) hides" >&2
else
    echo "SKIP: no adbd daemon package on $PKG — wireless debugging (ADB) hides"
fi

# --- 17.8 Utilities baseline ------------------------------------------------
pkg_install neovim ripgrep wl-clipboard
if [ "$PKG" = apk ]; then
    pkg_install yazi || echo "warn: yazi not in repos — install via cargo later" >&2
else
    pkg_install yazi || echo "warn: yazi not in repos — install via cargo later" >&2
fi

echo "Phone app set done."
