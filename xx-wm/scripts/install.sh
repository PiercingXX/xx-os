#!/bin/sh
# XX-WM installer — whiptail TUI, cached sudo, network check up front.
# POSIX sh; runs on postmarketOS/Alpine (apk), Debian/Mobian (apt), and Arch (pacman).
# GitHub.com/PiercingXX

set -u

REPO_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)

# --- unattended mode (automation/CI) ----------------------------------------
# TUI stays the default; --unattended disables every prompt and whiptail.
UNATTENDED=0
DEVICE_ARG=""
AUTOLOGIN=0
NO_FONTS=0
REFRESH_ONLY=0

parse_args() {
    while [ $# -gt 0 ]; do
        case $1 in
            --unattended) UNATTENDED=1 ;;
            --refresh-only) REFRESH_ONLY=1 ;;
            --device)
                DEVICE_ARG=${2:-}
                [ -n "$DEVICE_ARG" ] || {
                    echo "error: --device needs a name" >&2
                    return 1
                }
                shift
                ;;
            --device=*) DEVICE_ARG=${1#--device=} ;;
            --autologin) AUTOLOGIN=1 ;;
            --no-fonts) NO_FONTS=1 ;;
            -h|--help)
                echo "Usage: $0 [--unattended [--device NAME] [--autologin] [--no-fonts]]"
                return 0
                ;;
            *)
                echo "Unknown argument: $1" >&2
                return 1
                ;;
        esac
        shift
    done
}
parse_args "$@" || exit 1

# --- privilege helper (postmarketOS ships doas) -----------------------------
# --refresh-only (xx-apps-update's post-install refresh) needs neither root
# nor the network: it only asks resident apps to restart on the session bus.
if [ "${REFRESH_ONLY:-0}" != 1 ]; then
    if command -v doas >/dev/null 2>&1; then
        SUDO=doas
    elif command -v sudo >/dev/null 2>&1; then
        SUDO=sudo
    else
        echo "Need doas or sudo." >&2
        exit 1
    fi
else
    SUDO=true
fi

# --- network check up front -------------------------------------------------
net_ok() {
    if command -v nmcli >/dev/null 2>&1; then
        [ "$(nmcli -t -f STATE g 2>/dev/null)" = "connected" ] && return 0
    fi
    ping -c 1 -W 3 1.1.1.1 >/dev/null 2>&1
}
if [ "${REFRESH_ONLY:-0}" != 1 ] && ! net_ok; then
    echo "Network connectivity is required to continue." >&2
    exit 1
fi

# --- package manager --------------------------------------------------------
if command -v apk >/dev/null 2>&1; then
    PKG=apk
elif command -v apt >/dev/null 2>&1; then
    PKG=apt
elif command -v pacman >/dev/null 2>&1; then
    PKG=pacman
else
    echo "Unsupported distro: need apk, apt, or pacman." >&2
    exit 1
fi

pkg_install() {
    # Never hard-fail the menu on one missing package
    if [ "$PKG" = apk ]; then
        $SUDO apk add "$@" || echo "warn: some packages failed: $*" >&2
    elif [ "$PKG" = pacman ]; then
        $SUDO pacman -S --needed --noconfirm "$@" || echo "warn: some packages failed: $*" >&2
    else
        $SUDO apt install -y "$@" || echo "warn: some packages failed: $*" >&2
    fi
}

# --- whiptail (TUI only; unattended never prompts) ---------------------------
if [ "${REFRESH_ONLY:-0}" != 1 ] && [ "$UNATTENDED" != 1 ] && ! command -v whiptail >/dev/null 2>&1; then
    echo "Installing whiptail..."
    if [ "$PKG" = apk ]; then
        pkg_install newt
    elif [ "$PKG" = pacman ]; then
        pkg_install libnewt
    else
        pkg_install whiptail
    fi
fi

# --- cached sudo (doas has no timestamp cache; skip there) ------------------
if [ "${REFRESH_ONLY:-0}" != 1 ] && [ "$SUDO" = sudo ]; then
    echo "Caching sudo credentials..."
    sudo -v
    ( while true; do sudo -n true; sleep 60; kill -0 "$$" || exit; done 2>/dev/null & )
fi

msg_box() {
    if [ "${UNATTENDED:-0}" = 1 ]; then
        printf '%s\n' "$1"
    else
        whiptail --msgbox "$1" 0 0 0
    fi
}

install_deps() {
    echo "Installing build and runtime dependencies..."
    if [ "$PKG" = apk ]; then
        pkg_install py3-gobject3 gtk4.0 libadwaita gtk4-layer-shell \
            meson ninja rsync git phoc lisgd wl-clipboard
        pkg_install py3-wayland
        pkg_install py3-phonenumbers openssl py3-cairo
        pkg_install geoclue
        pkg_install networkmanager
        pkg_install brightnessctl
        pkg_install wlsunset
    elif [ "$PKG" = pacman ]; then
        pkg_install python-gobject gtk4 libadwaita gtk4-layer-shell meson ninja \
            rsync git phoc python-pywayland wl-clipboard geoclue \
            networkmanager brightnessctl python-phonenumbers openssl python-cairo
        pkg_install wlsunset
        if command -v lisgd >/dev/null 2>&1; then
            echo "lisgd already present: $(command -v lisgd)"
        else
            echo "SKIP: lisgd is not in Arch repos. Build from https://git.sr.ht/~mil/lisgd (libevdev + libinput). This tablet already has /usr/bin/lisgd."
        fi
    else
        pkg_install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 \
            libgtk4-layer-shell0 gir1.2-gtk4layershell-1.0 meson ninja-build rsync git \
            phoc lisgd wl-clipboard
        pkg_install python3-pywayland
        pkg_install python3-phonenumbers openssl python3-gi-cairo
        pkg_install geoclue-2.0
        pkg_install network-manager
        pkg_install brightnessctl
        pkg_install wlsunset
    fi
    install_portals
    install_media_deps
    install_notes_deps
    install_auth_deps
}

install_notes_deps() {
    # Runtime stack for xx-note: libsecret holds the WebDAV password
    # (never in config). Name evidence: install_media_deps below uses
    # these exact names on all three distros.
    echo "Installing notes app dependencies..."
    if [ "$PKG" = apk ]; then
        pkg_install libsecret
    elif [ "$PKG" = pacman ]; then
        pkg_install libsecret
    else
        pkg_install gir1.2-secret-1
    fi
}

install_media_deps() {
    # Runtime stack for the media apps (xx-radio, xx-audiobook). One app
    # failing to build never blocks the shell: pkg_install only warns.
    # Name evidence: xx-radio PORT_SPEC.md 2.1 (apk search on the pmOS
    # v26.06 VM, Alpine aports, Arch pacman -Si, Debian madison) and
    # xx-radio/docs/M0-findings.md (measured on FuriOS forky).
    echo "Installing media app dependencies..."
    if [ "$PKG" = apk ]; then
        # GStreamer (playbin; Opus/WebM needs good+base, AAC needs libav)
        # plus its GI use; libsecret holds the sign-in tokens.
        pkg_install gstreamer gst-plugins-base gst-plugins-good \
            gst-plugins-bad gst-libav py3-gst libsecret
        # xx-radio base: distro ytmusicapi + yt-dlp (the app's engine venv
        # is the fast path when they lag); nodejs runs the JS challenges
        # and the token helper. git (helper clone) is in the base deps.
        # Alpine's nodejs ships without npm (checked on pmOS v26.06); the
        # token-helper setup needs it.
        pkg_install py3-ytmusicapi yt-dlp nodejs npm
        # xx-radio cast discovery: the avahi daemon first (D-Bus
        # org.freedesktop.Avahi), zeroconf as the fallback.
        pkg_install avahi py3-zeroconf
    elif [ "$PKG" = pacman ]; then
        pkg_install gstreamer gst-plugins-base gst-plugins-good \
            gst-plugins-bad gst-libav gst-python libsecret
        pkg_install python-ytmusicapi yt-dlp nodejs npm
        # xx-radio cast discovery (avahi verified: avahi-daemon and
        # avahi-browse ship in this package on Arch).
        pkg_install avahi python-zeroconf
    else
        # FuriOS is Debian forky: no curl/pip/python3-venv by default, so
        # the engine venv is a first-run concern, not installed here.
        # Core libgstreamer follows as a dependency of the plugin packages.
        pkg_install gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
            gstreamer1.0-plugins-bad gstreamer1.0-libav \
            gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
            python3-gst-1.0 gir1.2-secret-1
        # trixie's nodejs is 20 (below the floor of 22); forky has 24.
        # python3-ytmusicapi is on its own line: not in trixie, and one
        # missing name must not fail yt-dlp/nodejs/npm with it.
        pkg_install yt-dlp nodejs npm
        pkg_install python3-ytmusicapi
        # xx-radio cast discovery: the avahi daemon first (D-Bus
        # org.freedesktop.Avahi), zeroconf as the fallback.
        pkg_install avahi-daemon python3-zeroconf
    fi
    # libadwaita and gtk4-layer-shell come from the base deps above; neither
    # media app uses Gtk4LayerShell or the GstAudio/GstPbutils typelibs
    # (only Gst 1.0), so no extra gir is installed for them.
}

install_auth_deps() {
    # Runtime stack for xx-auth (offline authenticator): AES-256-GCM via the
    # cryptography bindings, the Secret Service keyring, and zbarimg for
    # QR-image import. Names follow the exact prefix convention of the base
    # and media deps above (py3-/python-/python3-); re-verify on device when
    # the phone image changes.
    echo "Installing authenticator dependencies..."
    if [ "$PKG" = apk ]; then
        pkg_install py3-cryptography libsecret zbar
    elif [ "$PKG" = pacman ]; then
        pkg_install python-cryptography libsecret zbar
    else
        pkg_install python3-cryptography gir1.2-secret-1 zbar-tools
    fi
}

install_portals() {
    # File pickers (Gtk.FileDialog), screenshot/screencast and Settings
    # portals need the broker plus a GTK frontend and the wlroots backend
    # phoc speaks. Install only when the broker itself is missing.
    if command -v xdg-desktop-portal >/dev/null 2>&1; then
        return 0
    fi
    if [ "$PKG" = apk ]; then
        pkg_install xdg-desktop-portal xdg-desktop-portal-gtk \
            xdg-desktop-portal-wlr
    elif [ "$PKG" = pacman ]; then
        pkg_install xdg-desktop-portal xdg-desktop-portal-gtk \
            xdg-desktop-portal-wlr
    else
        pkg_install xdg-desktop-portal xdg-desktop-portal-gtk \
            xdg-desktop-portal-wlr
    fi
}

install_xx_keyboard_deps() {
    # Build deps for xx-keyboard (Stevia fork, C/GTK3). squeekboard is no
    # longer installed; it stays only a runtime fallback in xx-wm.in.
    echo "Installing xx-keyboard build dependencies..."
    if [ "$PKG" = apk ]; then
        pkg_install gtk+3.0-dev libhandy1-dev gmobile-dev feedbackd-dev \
            gnome-desktop-dev json-glib-dev libxkbcommon-dev dconf-dev \
            hunspell-dev hunspell-en gsettings-desktop-schemas-dev \
            wayland-dev wayland-protocols systemd-dev g++ gettext
    elif [ "$PKG" = pacman ]; then
        pkg_install gtk3 libhandy gmobile feedbackd gnome-desktop json-glib \
            libxkbcommon dconf hunspell hunspell-en_us \
            gsettings-desktop-schemas wayland-protocols gcc gettext
    else
        pkg_install libgtk-3-dev libhandy-1-dev libgmobile-dev \
            libfeedback-dev libgnome-desktop-3-dev libjson-glib-dev \
            libxkbcommon-dev libdconf-dev libhunspell-dev hunspell-en-us \
            gsettings-desktop-schemas-dev libsystemd-dev wayland-protocols g++ \
            gettext
    fi
    # gettext provides msgfmt for the translations (FuriOS ships without it);
    # g++ builds the glide (swipe) decoder; hunspell-en* is the word list
    # the completion bar and its fallback language need.
    # hunspell above is not optional: meson errors with
    # 'No usable completer found' when neither hunspell nor presage is
    # present, and presage is not packaged on every target distro.
}

install_xx_keyboard() {
    echo "Building xx-keyboard..."
    if (cd "$REPO_DIR/../xx-keyboard" &&
        { meson setup build --prefix=/usr --reconfigure ||
          meson setup build --prefix=/usr; } &&
        $SUDO meson install -C build); then
        record_installed "xx-keyboard" || true
    else
        echo "warn: xx-keyboard did not install; the session falls back to squeekboard when present" >&2
    fi
}

download_nerd_font_fallback() {
    # Distro packages don't always carry the Nerd Font variant (the shell's
    # default). When no JetBrainsMono Nerd Font is visible, fetch the
    # upstream release zip into the user font dir. Best effort: never fatal.
    if command -v fc-list >/dev/null 2>&1 && \
       fc-list 2>/dev/null | grep -qi "JetBrainsMono Nerd Font"; then
        return 0
    fi
    if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
        echo "warn: no curl/wget — skipping Nerd Font download" >&2
        return 0
    fi
    if ! command -v unzip >/dev/null 2>&1; then
        echo "warn: no unzip — skipping Nerd Font download" >&2
        return 0
    fi
    _zip_url=${XX_WM_NERD_FONT_URL:-https://github.com/ryanoasis/nerd-fonts/releases/download/v3.2.1/JetBrainsMono.zip}
    _fontdir=${XX_WM_FONT_DIR:-$HOME/.local/share/fonts}
    mkdir -p "$_fontdir" || return 0
    _tmpzip=$(mktemp /tmp/xx-wm-fonts.XXXXXX.zip) || return 0
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL -o "$_tmpzip" "$_zip_url" || {
            rm -f "$_tmpzip"
            echo "warn: Nerd Font download failed" >&2
            return 0
        }
    else
        wget -q -O "$_tmpzip" "$_zip_url" || {
            rm -f "$_tmpzip"
            echo "warn: Nerd Font download failed" >&2
            return 0
        }
    fi
    unzip -o -j "$_tmpzip" -d "$_fontdir" >/dev/null 2>&1 || \
        echo "warn: Nerd Font unzip failed" >&2
    rm -f "$_tmpzip"
    return 0
}

install_fonts() {
    if [ "${NO_FONTS:-0}" = 1 ]; then
        echo "Skipping fonts (--no-fonts)."
        return 0
    fi
    echo "Installing fonts (optional — never fails the install)..."
    if [ "$PKG" = pacman ]; then
        pkg_install ttf-jetbrains-mono-nerd
        pkg_install ttf-jetbrains-mono
        pkg_install ttf-space-mono-nerd
    elif [ "$PKG" = apk ]; then
        pkg_install font-jetbrains-mono-nerd
        pkg_install font-jetbrains-mono
        pkg_install font-space-mono
    else
        pkg_install fonts-jetbrains-mono
        echo "warn: no Space Mono distro package — skipping (not installing a stand-in)" >&2
    fi
    download_nerd_font_fallback || true
    $SUDO fc-cache -f >/dev/null 2>&1 || true
    return 0
}

check_layer_shell() {
    # meson intentionally has no gtk4-layer-shell dependency (it would pull
    # the whole GTK4 dev stack just to install Python files); the runtime
    # requirement is checked here via the typelib the shell actually loads.
    if python3 -c "import gi; gi.require_version('Gtk4LayerShell', '1.0')" \
        >/dev/null 2>&1; then
        echo "Gtk4LayerShell typelib present."
        return 0
    fi
    echo "warn: Gtk4LayerShell typelib not found — install gtk4-layer-shell for your distro (layer surfaces fall back to plain windows without it)" >&2
    return 1
}

install_logind_dropin() {
    src=/etc/systemd/logind.conf.d/10-xx-wm-power.conf
    if [ ! -f "$src" ]; then
        src="$REPO_DIR/launcher/data/logind/10-xx-wm-power.conf"
    fi
    if [ ! -f "$src" ]; then
        return 0
    fi
    if [ -d /etc/elogind ] || command -v elogind >/dev/null 2>&1; then
        $SUDO mkdir -p /etc/elogind/logind.conf.d || true
        $SUDO cp "$src" /etc/elogind/logind.conf.d/10-xx-wm-power.conf || \
            echo "warn: could not install elogind power-key drop-in" >&2
    fi
    return 0
}

link_squeekboard_layouts() {
    # squeekboard only reads the user path — symlink the installed layouts.
    # Shared by every install path (Install, Update, unattended), not just TUI.
    mkdir -p "$HOME/.local/share/squeekboard" || return 0
    ln -sfn /usr/share/xx-wm/squeekboard \
        "$HOME/.local/share/squeekboard/keyboards"
}

# The XX apps live next to xx-wm in the monorepo. xx-find (Find My Device)
# installs like the rest but runs nothing: its meson install never enables,
# starts or autostarts the agent; it stays off until the owner enables and
# pairs it in Settings. One app failing to build never blocks the shell.
# --- installed app versions (app_updates) --------------------------------------
# xx-apps-update --check compares this file against the published apps.json:
# /var/lib/xx-apps/installed.json maps app -> {version, commit, common}.
# XX_APPS_STATE_DIR overrides the directory (tests); XX_APPS_COMMIT overrides
# the recorded commit (tarballs carry no git history). Best effort throughout:
# recording never fails the install, and the write is atomic (tmp file plus
# rename in the same directory).
_xx_state_dir() {
    if [ -n "${XX_APPS_STATE_DIR:-}" ]; then
        printf '%s\n' "$XX_APPS_STATE_DIR"
    else
        printf '%s\n' /var/lib/xx-apps
    fi
}

_xx_merge_installed() {
    _m_app=$1 _m_current=$2 _m_stage=$3 _m_src=$4
    _m_commit=${XX_APPS_COMMIT:-$(git -C "$REPO_DIR" rev-parse HEAD 2>/dev/null)}
    XX_MERGE_APP="$_m_app" XX_MERGE_CURRENT="$_m_current" \
    XX_MERGE_STAGE="$_m_stage" XX_MERGE_SRC="$_m_src" \
    XX_MERGE_COMMIT="$_m_commit" python3 - <<'PYEOF' || return 1
import json, os, re
app = os.environ['XX_MERGE_APP']
src = os.environ['XX_MERGE_SRC']
def ver(path):
    try:
        with open(path, encoding='utf-8') as handle:
            text = handle.read()
    except OSError:
        return None
    match = re.search(r"version\s*:\s*['\"]([^'\"]+)['\"]", text)
    return match.group(1) if match else None
if app == 'xx-wm':
    meson = os.path.join(src, 'launcher', 'meson.build')
else:
    meson = os.path.join(src, 'meson.build')
common_meson = os.path.join(os.path.dirname(src), 'common', 'meson.build')
if app == 'common':
    common_meson = meson
entry = {'version': ver(meson), 'commit': os.environ.get('XX_MERGE_COMMIT') or None,
         'common': ver(common_meson)}
current = os.environ['XX_MERGE_CURRENT']
try:
    with open(current, encoding='utf-8') as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        data = {}
except (OSError, ValueError):
    data = {}
data[app] = entry
stage = os.environ['XX_MERGE_STAGE']
with open(stage, 'w', encoding='utf-8') as handle:
    json.dump(data, handle, indent=2, sort_keys=True)
    handle.write('\n')
PYEOF
}

record_installed() {
    _r_app=${1:-}
    [ -n "$_r_app" ] || return 0
    command -v python3 >/dev/null 2>&1 || return 0
    _r_dir=$(_xx_state_dir)
    _r_src=$REPO_DIR/../$_r_app
    [ "$_r_app" = "xx-wm" ] && _r_src=$REPO_DIR
    if (mkdir -p "$_r_dir" 2>/dev/null) && [ -w "$_r_dir" ]; then
        _r_stage=$_r_dir/.installed.json.tmp.$$
        if _xx_merge_installed "$_r_app" "$_r_dir/installed.json" \
                "$_r_stage" "$_r_src"; then
            mv -f "$_r_stage" "$_r_dir/installed.json" 2>/dev/null || \
                rm -f "$_r_stage"
        else
            rm -f "$_r_stage"
        fi
    else
        _r_stage=$(mktemp "${TMPDIR:-/tmp}/xx-apps-installed.XXXXXX") || return 0
        if _xx_merge_installed "$_r_app" "$_r_dir/installed.json" \
                "$_r_stage" "$_r_src"; then
            $SUDO sh -c 'mkdir -p "$0" && cat > "$0/installed.json.tmp" && mv "$0/installed.json.tmp" "$0/installed.json"' \
                "$_r_dir" < "$_r_stage" 2>/dev/null || true
        fi
        rm -f "$_r_stage"
    fi
    return 0
}

XX_APPS="xx-contacts xx-dialer txxt xx-calendar xx-calculator xx-email xx-vitals xx-share xx-captions xx-auto xx-radio xx-audiobook xx-note xx-weather xx-files xx-auth xx-clock xx-find"

install_apps() {
    for app in $XX_APPS; do
        echo "Building $app..."
        if (cd "$REPO_DIR/../$app" &&
            { meson setup build --prefix=/usr --reconfigure ||
              meson setup build --prefix=/usr; } &&
            $SUDO meson install -C build); then
            record_installed "$app" || true
        else
            echo "warn: $app did not install; the shell continues without it" >&2
        fi
    done
}

build_install() {
    echo "Building shared xx_ui design system..."
    cd "$REPO_DIR/../common" || return 1
    meson setup build --prefix=/usr --reconfigure || meson setup build --prefix=/usr || return 1
    $SUDO meson install -C build || return 1
    record_installed "common" || true
    echo "Building XX-WM..."
    cd "$REPO_DIR/launcher" || return 1
    meson setup build --prefix=/usr --reconfigure || meson setup build --prefix=/usr || return 1
    $SUDO meson install -C build || return 1
    record_installed "xx-wm" || true
    install_apps
    install_xx_keyboard_deps
    install_xx_keyboard
    cd "$REPO_DIR" || return 1

    # Kept for the squeekboard runtime fallback only (see xx-wm.in).
    link_squeekboard_layouts
    check_layer_shell || true

    # Owner's update-system.sh (opt out: XX_WM_NO_UPDATE_SCRIPT=1).
    # Succeeds silently on repeat runs; only a real failure warns.
    sh "$REPO_DIR/scripts/bootstrap-dots.sh" || \
        echo "warn: owner update script not installed (Settings falls back to the built-in flow)" >&2

    install_fonts
    install_logind_dropin
    select_phoc_scale || return 1
    enable_service
    setup_greeter
    add_input_group
    _build_status=$?
    refresh_icon_caches
    # Resident suite processes still run the old code until they restart;
    # refresh them now so the new code shows without a reboot. Best
    # effort: never fail the install when there is no session to refresh.
    refresh_running_apps || true
    return $_build_status
}

# GTK trusts hicolor's icon-theme.cache over the directory, so icons
# installed after the cache was last built show as "?" (owner saw every
# app as a question mark on the FLX1 and Librem). Rebuild it, and the
# desktop database, after every install. Best effort.
refresh_icon_caches() {
    for _cache_tool in gtk-update-icon-cache gtk4-update-icon-cache; do
        if command -v "$_cache_tool" >/dev/null 2>&1; then
            $SUDO "$_cache_tool" -q -t -f /usr/share/icons/hicolor 2>/dev/null && break
        fi
    done
    if command -v update-desktop-database >/dev/null 2>&1; then
        $SUDO update-desktop-database -q /usr/share/applications 2>/dev/null || true
    fi
    return 0
}

# --- per-device phoc.ini scale (20.5) --------------------------------------
# The tablet's panel reports as DSI-1 (wanting scale 1.5) and collides with
# the FP5's DSI-1 (scale 2.5), so a single phoc.ini cannot serve both. Prompt
# for the device and copy its fragment over the meson-installed default.
# A cancelled prompt must not silently keep the FP5 default (DSI-1 scale
# 2.5): detect_phoc_device prefers tablet on 1200x1920 DSI-1 modes, and an
# empty detection requires an explicit selection.
detect_phoc_device() {
    _drm=${XX_WM_SYSFS_DRM:-/sys/class/drm}
    for _modes in "$_drm"/card*-Virtual-1/modes; do
        [ -f "$_modes" ] || continue
        echo "qemu"
        return 0
    done
    for _modes in "$_drm"/card*-DSI-1/modes; do
        [ -f "$_modes" ] || continue
        if grep -q "1200x1920" "$_modes" 2>/dev/null || \
           grep -q "1920x1200" "$_modes" 2>/dev/null; then
            echo "tablet"
            return 0
        fi
        if grep -q "2340x1080" "$_modes" 2>/dev/null || \
           grep -q "1080x2340" "$_modes" 2>/dev/null; then
            echo "fairphone-5"
            return 0
        fi
        if grep -q "720x1440" "$_modes" 2>/dev/null || \
           grep -q "1440x720" "$_modes" 2>/dev/null; then
            echo "librem-5"
            return 0
        fi
    done
    for _modes in "$_drm"/card*-HWCOMPOSER-1/modes; do
        [ -f "$_modes" ] || continue
        echo "furiphone-flx1"
        return 0
    done
    echo ""
    return 0
}

_ask_device() {
    whiptail --backtitle "GitHub.com/PiercingXX" --title "Device" \
        --menu "Which device is this? (sets phoc.ini scale)" 0 0 0 \
        "fairphone-5"    "FP5 — DSI-1, scale 2.5" \
        "furiphone-flx1" "FLX1 — HWCOMPOSER-1, scale 1.6" \
        "librem-5"       "Librem 5 — DSI-1, scale 2" \
        "tablet"         "x86 tablet — DSI-1, scale 1.5" \
        "qemu"           "QEMU/UTM VM — Virtual-1, scale 1.5" \
        3>&1 1>&2 2>&3
}

select_phoc_scale() {
    # detected names a DRM-modes match (1200x1920 DSI-1 → tablet); empty
    # means no recognizable panel, so a cancelled prompt must loop.
    detected=$(detect_phoc_device)
    dev=""
    if [ "${UNATTENDED:-0}" = 1 ]; then
        if [ -n "${DEVICE_ARG:-}" ]; then
            dev="$DEVICE_ARG"
        elif [ -n "$detected" ]; then
            dev="$detected"
            echo "using detected device: $dev"
        else
            echo "error: no device detected and no --device given (phoc.ini scale needs one)" >&2
            return 1
        fi
    elif [ ! -t 0 ]; then
        if [ -n "$detected" ]; then
            dev="$detected"
            echo "using detected device: $dev"
        else
            echo "A device is required so phoc.ini scale is not the Fairphone 5 default (need a TTY or DRM sysfs)." >&2
            return 1
        fi
    else
        dev=$(_ask_device) || dev=""
        if [ -z "$dev" ]; then
            if [ -n "$detected" ]; then
                dev="$detected"
                echo "using detected device: $dev"
            else
                msg_box "A device is required so phoc.ini scale is not the Fairphone 5 default."
                while [ -z "$dev" ]; do
                    dev=$(_ask_device) || dev=""
                done
            fi
        fi
    fi
    # The fragment ships in the repo tree (launcher/data/phoc/$dev.ini);
    # post-install the helper falls back to /usr/share/xx-wm/phoc/$dev.ini.
    _install_phoc_fragment "$dev"
}

_install_phoc_fragment() {
    _dev=$1
    _frag="$REPO_DIR/launcher/data/phoc/${_dev}.ini"
    if [ ! -f "$_frag" ]; then
        _frag="/usr/share/xx-wm/phoc/${_dev}.ini"
    fi
    if [ ! -f "$_frag" ]; then
        echo "warn: no phoc.ini fragment for '$_dev' — keeping default" >&2
        return 1
    fi
    $SUDO cp "$_frag" "${XX_WM_PHOC_DEST:-/usr/share/xx-wm/phoc.ini}" || {
        echo "warn: could not install phoc.ini fragment for '$_dev'" >&2
        if [ "${UNATTENDED:-0}" = 1 ]; then
            return 1
        fi
        return 0
    }
}

# --- input group (20.3) -----------------------------------------------------
# lisgd (touchscreen) and DisplayManager (power/volume keys) read evdev
# directly; without membership in the `input` group they silently do nothing.
# usermod -aG appends and is idempotent; we skip the call entirely when the
# user is already a member so the "re-login" note only appears when it matters.
add_input_group() {
    user=$(id -un)
    if id -nG "$user" 2>/dev/null | grep -qw input; then
        return 0
    fi
    $SUDO usermod -aG input "$user" 2>/dev/null || \
        $SUDO addgroup "$user" input 2>/dev/null || {
            echo "warn: could not add '$user' to the input group" >&2
            return 1
        }
    echo "Added '$user' to the input group — re-login (or reboot) for it to take effect."
}

# --- post-install refresh (install_refresh) ---------------------------------
# Suite apps keep resident processes that survive an install with the OLD
# code: --gapplication-service windows (D-Bus activated, resident after
# the window closes), the radio/audiobook player services, systemd user
# units, and the shell itself. Reopening an app reattaches to the old
# process, so without this a fresh install shows old code until a reboot.
# Best effort throughout: a refresh step never fails the install, never
# touches processes owned by another user, and never uses pkill -f (a
# substring pattern could match this script's own command line). PIDs
# come from exact argv matches in /proc instead. XX_REFRESH_PROC and
# XX_REFRESH_USER_BASE are test hooks (fake /proc, fake runtime base).
_rra_proc_root() {
    if [ -n "${XX_REFRESH_PROC:-}" ]; then
        printf '%s\n' "$XX_REFRESH_PROC"
    else
        printf '%s\n' /proc
    fi
}

_rra_argv_has() {
    printf '%s\n' "$2" | grep -qxF -e "$1" 2>/dev/null
}

_rra_argv_has_path() {
    if printf '%s\n' "$2" | grep -qxF -e "$1" 2>/dev/null; then
        return 0
    fi
    printf '%s\n' "$2" | grep -qx -e ".*/$1" 2>/dev/null
}

_rra_session_bus() {
    _s_uid=$(id -u 2>/dev/null) || return 1
    _s_base=${XX_REFRESH_USER_BASE:-/run/user}
    _s_dir=${XDG_RUNTIME_DIR:-$_s_base/$_s_uid}
    if [ ! -S "$_s_dir/bus" ]; then
        _s_dir=$_s_base/$_s_uid
    fi
    if [ ! -S "$_s_dir/bus" ]; then
        return 1
    fi
    XDG_RUNTIME_DIR=$_s_dir
    DBUS_SESSION_BUS_ADDRESS=unix:path=$_s_dir/bus
    export XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS
    return 0
}

_rra_window_pids() {
    _w_mod=$1
    _w_launch=$2
    _w_proc=$(_rra_proc_root)
    _w_me=$$
    for _w_d in "$_w_proc"/[0-9]*; do
        [ -d "$_w_d" ] || continue
        _w_pid=${_w_d##*/}
        case $_w_pid in
            ''|*[!0-9]*) continue ;;
        esac
        if [ "$_w_pid" = "$_w_me" ]; then
            continue
        fi
        if ! [ -O "$_w_d" ] 2>/dev/null; then
            continue
        fi
        [ -r "$_w_d/cmdline" ] || continue
        _w_args=$(tr '\0' '\n' <"$_w_d/cmdline" 2>/dev/null) || continue
        if _rra_argv_has --gapplication-service "$_w_args" && { _rra_argv_has "$_w_mod" "$_w_args" || _rra_argv_has_path "$_w_launch" "$_w_args"; }; then
            echo "$_w_pid"
        fi
    done
    return 0
}

_rra_player_pids() {
    _pp_mod=$1
    _pp_flag=$2
    _pp_launch=$3
    _pp_proc=$(_rra_proc_root)
    _pp_me=$$
    for _pp_d in "$_pp_proc"/[0-9]*; do
        [ -d "$_pp_d" ] || continue
        _pp_pid=${_pp_d##*/}
        case $_pp_pid in
            ''|*[!0-9]*) continue ;;
        esac
        if [ "$_pp_pid" = "$_pp_me" ]; then
            continue
        fi
        if ! [ -O "$_pp_d" ] 2>/dev/null; then
            continue
        fi
        [ -r "$_pp_d/cmdline" ] || continue
        _pp_args=$(tr '\0' '\n' <"$_pp_d/cmdline" 2>/dev/null) || continue
        if [ -n "$_pp_flag" ]; then
            if _rra_argv_has "$_pp_flag" "$_pp_args" && { _rra_argv_has "$_pp_mod" "$_pp_args" || _rra_argv_has_path "$_pp_launch" "$_pp_args"; }; then
                echo "$_pp_pid"
            fi
        else
            if _rra_argv_has "$_pp_mod" "$_pp_args" || _rra_argv_has_path "$_pp_launch" "$_pp_args"; then
                echo "$_pp_pid"
            fi
        fi
    done
    return 0
}

_rra_shell_pids() {
    _h_proc=$(_rra_proc_root)
    _h_me=$$
    for _h_d in "$_h_proc"/[0-9]*; do
        [ -d "$_h_d" ] || continue
        _h_pid=${_h_d##*/}
        case $_h_pid in
            ''|*[!0-9]*) continue ;;
        esac
        if [ "$_h_pid" = "$_h_me" ]; then
            continue
        fi
        if ! [ -O "$_h_d" ] 2>/dev/null; then
            continue
        fi
        [ -r "$_h_d/cmdline" ] || continue
        _h_args=$(tr '\0' '\n' <"$_h_d/cmdline" 2>/dev/null) || continue
        _h_first=$(printf '%s\n' "$_h_args" | sed -n '1p')
        case $_h_first in
            python3|python3.*|*/python3|*/python3.*) ;;
            *) continue ;;
        esac
        if _rra_argv_has_path xx-wm/main.py "$_h_args"; then
            echo "$_h_pid"
        fi
    done
    return 0
}

_rra_mpris_status() {
    _m_bus=$1
    _m_out=
    if command -v gdbus >/dev/null 2>&1; then
        _m_out=$(gdbus call --session --dest "$_m_bus" --object-path /org/mpris/MediaPlayer2 --method org.freedesktop.DBus.Properties.Get org.mpris.MediaPlayer2.Player PlaybackStatus 2>/dev/null) || _m_out=
    fi
    if [ -z "$_m_out" ] && command -v busctl >/dev/null 2>&1; then
        _m_out=$(busctl --user get-property "$_m_bus" /org/mpris/MediaPlayer2 org.mpris.MediaPlayer2.Player PlaybackStatus 2>/dev/null) || _m_out=
    fi
    printf '%s\n' "$_m_out"
    return 0
}

_rra_quit_window() {
    _q_bus=$1
    _q_pid=$2
    _q_path=/$(printf '%s' "$_q_bus" | tr '.' '/')
    if command -v gdbus >/dev/null 2>&1; then
        if gdbus call --session --dest "$_q_bus" --object-path "$_q_path" --method org.gtk.Actions.Activate quit '[]' '{}' >/dev/null 2>&1; then
            return 0
        fi
    fi
    if [ -n "$_q_pid" ]; then
        env kill -TERM "$_q_pid" 2>/dev/null || true
    fi
    return 0
}

_rra_quit_player() {
    _qp_bus=$1
    _qp_pid=$2
    if command -v gdbus >/dev/null 2>&1; then
        if gdbus call --session --dest "$_qp_bus" --object-path /org/mpris/MediaPlayer2 --method org.mpris.MediaPlayer2.Quit >/dev/null 2>&1; then
            return 0
        fi
    fi
    if [ -n "$_qp_pid" ]; then
        env kill -TERM "$_qp_pid" 2>/dev/null || true
    fi
    return 0
}

refresh_running_apps() {
    _r_windows="io.piercingxx.XXDialer:xx_dialer:xx-dialer io.piercingxx.XXCalendar:xx_calendar:xx-calendar io.piercingxx.XXVitals:xx_vitals:xx-vitals io.piercingxx.XXRadio:xx_radio:xx-radio io.piercingxx.XXAudiobook:xx_audiobook:xx-audiobook io.piercingxx.XXFiles:xx_files:xx-files io.piercingxx.XXClock:xx_clock:xx-clock io.piercingxx.TxxT:txxt:txxt"
    _r_players="org.mpris.MediaPlayer2.xx_radio:xx_radio:--player-service:xx-radio org.mpris.MediaPlayer2.xx_audiobook:xx_audiobook.service.player_service::xx-audiobook-player"
    _r_units="xx-clock-daemon.service xx-share.service xx-auto-watch.service xx-vitals-collect.service"
    _r_refreshed=0
    _r_deferred=0
    if ! _rra_session_bus; then
        echo "refresh: no user session bus found — leaving running apps until the next login."
        return 0
    fi
    for _r_spec in $_r_windows; do
        _r_bus=${_r_spec%%:*}
        _r_tmp=${_r_spec#*:}
        _r_mod=${_r_tmp%%:*}
        _r_launch=${_r_tmp#*:}
        _r_pids=$(_rra_window_pids "$_r_mod" "$_r_launch")
        if [ -z "$_r_pids" ]; then
            continue
        fi
        for _r_pid in $_r_pids; do
            _rra_quit_window "$_r_bus" "$_r_pid"
            echo "refresh: asked $_r_launch ($_r_bus) to quit; it starts with the new code on the next launch."
            _r_refreshed=$((_r_refreshed + 1))
        done
    done
    for _r_spec in $_r_players; do
        _r_rest=$_r_spec
        _r_mbus=${_r_rest%%:*}
        _r_rest=${_r_rest#*:}
        _r_mod=${_r_rest%%:*}
        _r_rest=${_r_rest#*:}
        _r_flag=${_r_rest%%:*}
        _r_launch=${_r_rest#*:}
        _r_name=${_r_mbus#org.mpris.MediaPlayer2.}
        _r_status=$(_rra_mpris_status "$_r_mbus")
        case $_r_status in
            *Playing*)
                echo "refresh: $_r_name player is Playing — leaving it; it picks up the new code after playback stops or at the next login."
                _r_deferred=$((_r_deferred + 1))
                continue
                ;;
        esac
        _r_pids=$(_rra_player_pids "$_r_mod" "$_r_flag" "$_r_launch")
        if [ -z "$_r_status" ] && [ -z "$_r_pids" ]; then
            continue
        fi
        if [ -z "$_r_status" ]; then
            echo "refresh: $_r_name player status is unknown — leaving it; it picks up the new code after playback stops or at the next login."
            _r_deferred=$((_r_deferred + 1))
            continue
        fi
        if [ -z "$_r_pids" ]; then
            _rra_quit_player "$_r_mbus" ""
            echo "refresh: quit idle $_r_name player; it restarts with the new code on the next playback."
            _r_refreshed=$((_r_refreshed + 1))
            continue
        fi
        for _r_pid in $_r_pids; do
            _rra_quit_player "$_r_mbus" "$_r_pid"
            echo "refresh: quit idle $_r_name player (pid $_r_pid); it restarts with the new code on the next playback."
            _r_refreshed=$((_r_refreshed + 1))
        done
    done
    if command -v systemctl >/dev/null 2>&1 && systemctl --user list-units --no-legend >/dev/null 2>&1; then
        for _r_unit in $_r_units; do
            # try-restart only touches a running unit, and the clock
            # daemon reloads its schedule from disk on start
            # (ClockDaemon reconciles on startup), so no alarm is lost.
            if systemctl --user try-restart "$_r_unit" 2>/dev/null; then
                echo "refresh: try-restarted $_r_unit (no-op when it was not running)."
                _r_refreshed=$((_r_refreshed + 1))
            else
                echo "refresh: could not try-restart $_r_unit — leaving it until later."
                _r_deferred=$((_r_deferred + 1))
            fi
        done
    else
        echo "refresh: no systemd user manager — skipping user units until the next login."
    fi
    _r_shell=$(_rra_shell_pids)
    _r_count=0
    _r_only=
    for _r_p in $_r_shell; do
        _r_count=$((_r_count + 1))
        _r_only=$_r_p
    done
    if [ "$_r_count" = 1 ]; then
        # SIGUSR1 respawns only the python child; the /usr/bin/xx-wm
        # wrapper, phoc and the session stay up (see xx-wm.in).
        if env kill -USR1 "$_r_only" 2>/dev/null; then
            echo "refresh: asked the shell (pid $_r_only) to respawn with the new code; apps and phoc stay up."
            _r_refreshed=$((_r_refreshed + 1))
        else
            echo "refresh: could not signal the shell — leaving it until the next login."
            _r_deferred=$((_r_deferred + 1))
        fi
    elif [ "$_r_count" = 0 ]; then
        echo "refresh: shell is not running — nothing to respawn."
    else
        echo "refresh: several shell processes found — leaving them until the next login."
        _r_deferred=$((_r_deferred + 1))
    fi
    echo "refresh: done — $_r_refreshed refreshed, $_r_deferred left until later."
    return 0
}

is_systemd() {
    # Never infer the init system from the package manager: postmarketOS
    # v26.06 Phosh images install via apk yet run systemd, where rc-update
    # does not exist. The runtime marker is /run/systemd/system, which only
    # exists when systemd is running.
    [ -d "${XX_WM_SYSTEMD_RUN_DIR:-/run/systemd/system}" ]
}

has_openrc() {
    # OpenRC counts only when systemd is NOT running and openrc-run exists.
    ! is_systemd && { [ -x /sbin/openrc-run ] || \
        command -v openrc-run >/dev/null 2>&1; }
}

write_openrc_conf() {
    # The OpenRC service runs as $XX_WM_USER: record the installing user
    # so the session starts as them instead of the literal default 'user'.
    printf 'XX_WM_USER=%s\n' "$(id -un)" | \
        $SUDO tee /etc/conf.d/xx-wm >/dev/null || \
        echo "warn: could not write /etc/conf.d/xx-wm" >&2
}

enable_service() {
    if is_systemd; then
        systemctl --user daemon-reload 2>/dev/null || true
        # Re-apply the saved charge limit and network blocks at boot.
        $SUDO systemctl enable xx-wm-helper-boot.service 2>/dev/null || \
            echo "warn: enable xx-wm-helper-boot.service failed" >&2
        if [ -f /usr/share/wayland-sessions/xx-wm.desktop ]; then
            # Idempotent: a previous install.sh always-enabled the unit.
            # --now stops a running duplicate if we are somehow inside a session.
            systemctl --user disable --now xx-wm 2>/dev/null || true
            echo "wayland-session installed; systemd --user xx-wm disabled (would double-start under GDM)."
            echo "Opt-in only from a session that is NOT already xx-wm-session:"
            echo "  systemctl --user start xx-wm"
            echo "Do not 'enable --now' under GDM: WantedBy=graphical-session.target would start a second Python shell."
        else
            systemctl --user enable xx-wm 2>/dev/null || \
                echo "warn: enable the service after first login: systemctl --user enable xx-wm" >&2
        fi
    elif has_openrc; then
        # OpenRC hosts (older pmOS images): no systemd unit to fight with.
        write_openrc_conf
        $SUDO rc-update add xx-wm default 2>/dev/null || \
            echo "warn: rc-update add xx-wm default failed" >&2
        $SUDO rc-update add xx-wm-helper-boot boot 2>/dev/null || \
            echo "warn: rc-update add xx-wm-helper-boot boot failed" >&2
    else
        echo "warn: no supported init detected (neither systemd nor OpenRC) — start xx-wm-session from your display manager" >&2
    fi
}

# --- GDM Colemak OSK (24) ---------------------------------------------------
# The GDM greeter runs GNOME Shell's own on-screen keyboard, which reads JSON
# layouts from /usr/share/gnome-shell/osk-layouts/ — squeekboard layouts do
# not apply there, so the login screen types QWERTY unless the stock US
# layout is overwritten. Only offered when GDM is actually wired up as the
# display manager — enabled, or at least shipped and selected by the distro
# config; a bare binary with nothing pointing at it makes the entry inert.
# pmOS/FuriOS phones don't run GDM anyway. Backs up the original once;
# restoring is a plain cp.
gdm_detected() {
    if ! command -v systemctl >/dev/null 2>&1; then
        # No systemd (postmarketOS/OpenRC): keep the old binary-only probe.
        command -v gdm >/dev/null 2>&1 || command -v gdm3 >/dev/null 2>&1
        return
    fi
    if ! command -v gdm >/dev/null 2>&1 && ! command -v gdm3 >/dev/null 2>&1; then
        return 1
    fi
    if systemctl -q is-enabled gdm 2>/dev/null || systemctl -q is-enabled gdm3 2>/dev/null; then
        return 0
    fi
    if systemctl cat gdm >/dev/null 2>&1 || systemctl cat gdm3 >/dev/null 2>&1; then
        return 0
    fi
    grep -q gdm /etc/X11/default-display-manager 2>/dev/null && return 0
    readlink /etc/systemd/system/display-manager.service 2>/dev/null | grep -q gdm
}

install_gdm_osk() {
    src=/usr/share/xx-wm/gnome-osk/us.json
    dst=/usr/share/gnome-shell/osk-layouts/us.json
    bak="$dst.xx-wm-backup"
    if [ ! -f "$src" ]; then
        echo "warn: $src missing — run Install or Update first" >&2
        return 1
    fi
    if ! whiptail --backtitle "GitHub.com/PiercingXX" \
        --title "GDM Colemak keyboard" --yesno \
        "This OVERWRITES the stock GNOME Shell US on-screen keyboard with the Colemak layout.\n\nThe original is backed up once to:\n  $bak\n\nRestore QWERTY later with:\n  sudo cp $bak $dst\n\nContinue?" 0 0; then
        return 0
    fi
    if [ ! -f "$bak" ]; then
        $SUDO cp "$dst" "$bak" || {
            echo "warn: could not back up $dst" >&2
            return 1
        }
    fi
    $SUDO cp "$src" "$dst" || {
        echo "warn: could not install the Colemak GDM OSK layout" >&2
        return 1
    }
    msg_box "GDM now types Colemak on the login screen.\nRestore QWERTY with:\n  sudo cp $bak $dst"
}

# --- greetd / phrog (postmarketOS v26.06 Phosh) ------------------------------
# pmOS Phosh images greet with greetd + phrog; the greeter's session list
# comes from /usr/share/wayland-sessions, so the meson-installed
# xx-wm.desktop entry already appears there. Autologin writes an
# [initial_session] (session command + installing user) into the greetd
# config file actually in use — the one named by greetd.service's ExecStart
# -c/--config on pmOS, e.g. /etc/phrog/greetd-config.toml — backed up first,
# and only ever with an explicit opt-in (--autologin, or the TUI prompt).
greetd_detected() {
    command -v greetd >/dev/null 2>&1 || \
    [ -f /etc/phrog/greetd-config.toml ] || \
    [ -f /etc/greetd/config.toml ]
}

greetd_config_path() {
    _svc=$(systemctl cat greetd 2>/dev/null || true)
    _cfg=$(printf '%s\n' "$_svc" | grep -o -- '-c [^[:space:]]*' 2>/dev/null | \
        head -n 1 | cut -d ' ' -f 2)
    if [ -z "$_cfg" ]; then
        _cfg=$(printf '%s\n' "$_svc" | \
            grep -o -- '--config[ =][^[:space:]]*' 2>/dev/null | \
            head -n 1 | sed 's/^--config[ =]//')
    fi
    if [ -z "$_cfg" ]; then
        if [ -f /etc/phrog/greetd-config.toml ]; then
            _cfg=/etc/phrog/greetd-config.toml
        else
            _cfg=/etc/greetd/config.toml
        fi
    fi
    printf '%s\n' "$_cfg"
}

setup_greetd_autologin() {
    _cfg=$(greetd_config_path)
    _user=$(id -un)
    _cmd=${XX_WM_SESSION_BIN:-/usr/libexec/xx-wm-session}
    _bak="$_cfg.xx-wm-backup"
    if [ ! -f "$_cfg" ]; then
        echo "warn: greetd config $_cfg not found — skipping autologin" >&2
        return 1
    fi
    if grep -q '^\[initial_session\]' "$_cfg" 2>/dev/null; then
        echo "greetd config already has [initial_session] — leaving as-is."
        return 0
    fi
    if [ ! -f "$_bak" ]; then
        $SUDO cp "$_cfg" "$_bak" || {
            echo "warn: could not back up $_cfg" >&2
            return 1
        }
    fi
    {
        printf '\n[initial_session]\n'
        printf 'command = "%s"\n' "$_cmd"
        printf 'user = "%s"\n' "$_user"
    } | $SUDO tee -a "$_cfg" >/dev/null || {
        echo "warn: could not write autologin to $_cfg" >&2
        return 1
    }
    echo "greetd autologin enabled for '$_user' (backup: $_bak)."
}

setup_greeter() {
    if greetd_detected; then
        echo "greetd/phrog detected: pick XX-WM in the greeter's Session menu at next login."
        if [ "${AUTOLOGIN:-0}" = 1 ]; then
            setup_greetd_autologin || true
        elif [ "${UNATTENDED:-0}" != 1 ] && [ -t 0 ]; then
            if whiptail --backtitle "GitHub.com/PiercingXX" \
                --title "greetd autologin" --yesno \
                "Log straight into XX-WM (writes [initial_session] to the greetd config)? The original is backed up first." 0 0; then
                setup_greetd_autologin || true
            fi
        fi
    else
        echo "At next login, select the XX-WM session from your display manager."
    fi
    return 0
}

do_install() {
    install_deps
    if build_install; then
        msg_box "XX-WM installed. Select the XX-WM session at next login (not PiercingXX), or reboot."
    else
        msg_box "Install hit an error — check the terminal output."
    fi
}

do_unattended() {
    # Automation/CI: no prompts, no dialogs. A failed required step exits
    # non-zero; a summary closes the run either way.
    install_deps
    build_install || {
        echo "error: unattended install failed — see output above" >&2
        return 1
    }
    echo "--- XX-WM install summary ---"
    echo "mode: unattended"
    if [ -n "${DEVICE_ARG:-}" ]; then
        echo "device: $DEVICE_ARG"
    else
        echo "device: $(detect_phoc_device) (auto-detected)"
    fi
    if [ "${NO_FONTS:-0}" = 1 ]; then
        echo "fonts: skipped"
    else
        echo "fonts: installed"
    fi
    if [ "${AUTOLOGIN:-0}" = 1 ]; then
        echo "autologin: requested"
    else
        echo "autologin: off"
    fi
    if greetd_detected; then
        echo "greeter: greetd/phrog — pick XX-WM in the Session menu"
    else
        echo "greeter: pick XX-WM in your display manager"
    fi
    return 0
}

do_update() {
    echo "Updating repo..."
    git -C "$REPO_DIR" pull --ff-only || echo "warn: git pull failed" >&2
    if build_install; then
        msg_box "XX-WM updated."
    else
        msg_box "Update hit an error — check the terminal output."
    fi
}

menu() {
    set -- \
        "Install"             "Install XX-WM (deps, build, service)" \
        "Update"              "Pull latest and reinstall" \
        "Install phone apps"  "Browser, calculator, Tailscale, Skippy, Waydroid..."
    if gdm_detected; then
        set -- "$@" \
            "GDM Colemak OSK"    "Colemak on the GDM login screen (overwrites stock US)"
    fi
    set -- "$@" \
        "Reboot"              "Reboot the system" \
        "Exit"                "Exit the installer"
    whiptail --backtitle "GitHub.com/PiercingXX" --title "XX-WM" \
        --menu "Run options in order:" 0 0 0 "$@" 3>&1 1>&2 2>&3
}

if [ "${REFRESH_ONLY:-0}" = 1 ]; then
    refresh_running_apps
    exit $?
fi

if [ "$UNATTENDED" = 1 ]; then
    do_unattended
    exit $?
fi

while true; do
    clear
    choice=$(menu) || exit 0
    case $choice in
        "Install")            do_install ;;
        "Update")             do_update ;;
        "Install phone apps") sh "$REPO_DIR/scripts/apps.sh" "$PKG" "$SUDO" || true ;;
        "GDM Colemak OSK")    install_gdm_osk ;;
        "Reboot")             $SUDO reboot ;;
        "Exit")               exit 0 ;;
        *)                    exit 0 ;;
    esac
done
