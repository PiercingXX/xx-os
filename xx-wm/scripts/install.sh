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

parse_args() {
    while [ $# -gt 0 ]; do
        case $1 in
            --unattended) UNATTENDED=1 ;;
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
if command -v doas >/dev/null 2>&1; then
    SUDO=doas
elif command -v sudo >/dev/null 2>&1; then
    SUDO=sudo
else
    echo "Need doas or sudo." >&2
    exit 1
fi

# --- network check up front -------------------------------------------------
net_ok() {
    if command -v nmcli >/dev/null 2>&1; then
        [ "$(nmcli -t -f STATE g 2>/dev/null)" = "connected" ] && return 0
    fi
    ping -c 1 -W 3 1.1.1.1 >/dev/null 2>&1
}
if ! net_ok; then
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
if [ "$UNATTENDED" != 1 ] && ! command -v whiptail >/dev/null 2>&1; then
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
if [ "$SUDO" = sudo ]; then
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
        :
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

# The XX apps live next to xx-wm in the monorepo. xx-find is deliberately
# absent: Find My Device is built but must not be installed or run until the
# owner turns it on. One app failing to build never blocks the shell.
XX_APPS="xx-contacts xx-dialer txxt xx-calendar xx-calculator xx-email xx-vitals xx-share xx-captions xx-auto xx-radio xx-audiobook xx-note xx-weather xx-files xx-auth xx-clock"

install_apps() {
    for app in $XX_APPS; do
        echo "Building $app..."
        if (cd "$REPO_DIR/../$app" &&
            { meson setup build --prefix=/usr --reconfigure ||
              meson setup build --prefix=/usr; } &&
            $SUDO meson install -C build); then
            :
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
    echo "Building XX-WM..."
    cd "$REPO_DIR/launcher" || return 1
    meson setup build --prefix=/usr --reconfigure || meson setup build --prefix=/usr || return 1
    $SUDO meson install -C build || return 1
    install_apps
    install_xx_keyboard_deps
    install_xx_keyboard
    cd "$REPO_DIR" || return 1

    # Kept for the squeekboard runtime fallback only (see xx-wm.in).
    link_squeekboard_layouts
    check_layer_shell || true

    # piercing-dots phone profile (parallel task — tolerate its loud failure)
    sh "$REPO_DIR/scripts/bootstrap-dots.sh" || \
        echo "warn: piercing-dots phone profile not applied yet" >&2

    install_fonts
    install_logind_dropin
    select_phoc_scale || return 1
    enable_service
    setup_greeter
    add_input_group
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
