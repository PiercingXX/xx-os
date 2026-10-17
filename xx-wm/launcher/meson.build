project(
  'xx-wm',
  version: '0.1.1',
  meson_version: '>=0.60.0',
)

python = import('python').find_installation('python3')

# The shell themes through the shared design system: launcher modules
# import xx_ui (installed from common/ by scripts/install.sh). Fail setup
# early when it is missing instead of shipping a shell that crashes.
xx_ui_check = run_command(
  python, '-c', 'import xx_ui',
  check: false,
)
if xx_ui_check.returncode() != 0
  error('xx_ui not importable — install common/ first (meson setup + meson install in common/)')
endif

# No gtk4-layer-shell build probe here: this package only installs Python
# files, and a compile-time pkg-config probe would drag the whole GTK4
# development stack onto the device. The installer checks for the
# Gtk4LayerShell typelib at install time instead (scripts/install.sh).

prefix       = get_option('prefix')
bindir       = join_paths(prefix, get_option('bindir'))
libexecdir   = join_paths(prefix, get_option('libexecdir'))
datadir_abs  = join_paths(prefix, get_option('datadir'))

shell_subdir = 'xx-wm'
shell_datadir = join_paths(get_option('datadir'), shell_subdir)

# The shell is a flat set of modules, not an installable package: it uses
# plain imports (`from window import ...`) and finds sibling assets via
# Path(__file__).with_name(). So every .py must land in the SAME directory
# as style.css (shell_datadir) and run as `python3 <shell_datadir>/main.py`,
# which puts that directory on sys.path. Installing to site-packages breaks
# both the flat imports and the asset lookups.
install_data(
  [
    'src/action_sheet.py',
    'src/als_brightness.py',
    'src/backlight.py',
    'src/app_index.py',
    'src/app_item_actions.py',
    'src/app_names.py',
    'src/app_permissions.py',
    'src/app_size.py',
    'src/app_standby.py',
    'src/app_switcher.py',
    'src/back_gesture.py',
    'src/backup.py',
    'src/banner.py',
    'src/calendar_grid.py',
    'src/call_screen.py',
    'src/call_ui.py',
    'src/call_wiring.py',
    'src/captions.py',
    'src/capture.py',
    'src/clock_format.py',
    'src/capture_stitch.py',
    'src/config.py',
    'src/contacts.py',
    'src/default_layout.py',
    'src/desktop_theme.py',
    'src/dialer.py',
    'src/display_manager.py',
    'src/dnd.py',
    'src/drawer_page.py',
    'src/filebackup.py',
    'src/first_boot.py',
    'src/focus_mode.py',
    'src/font_theme.py',
    'src/gesture_bindings.py',
    'src/gesture_config.py',
    'src/helper_client.py',
    'src/home_launcher.py',
    'src/home_page.py',
    'src/hud.py',
    'src/ipc.py',
    'src/keyboard_layouts.py',
    'src/lock_lines.py',
    'src/lock_screen.py',
    'src/main.py',
    'src/media_controls.py',
    'src/modem_monitor.py',
    'src/night_light.py',
    'src/nope_mode.py',
    'src/notif_daemon.py',
    'src/notification_shade.py',
    'src/osk.py',
    'src/output_capture.py',
    'src/output_power.py',
    'src/polkit_agent.py',
    'src/power_menu.py',
    'src/power_profiles.py',
    'src/privacy_monitor.py',
    'src/quick_actions.py',
    'src/reorder_sheet.py',
    'src/settings_apps.py',
    'src/settings_battery_use.py',
    'src/settings_charging.py',
    'src/settings_connect.py',
    'src/settings_device.py',
    'src/settings_display.py',
    'src/settings_firewall.py',
    'src/settings_keyboard.py',
    'src/settings_launcher.py',
    'src/settings_lock.py',
    'src/settings_logs.py',
    'src/settings_medical.py',
    'src/settings_nope.py',
    'src/settings_page.py',
    'src/settings_permissions.py',
    'src/settings_screentime.py',
    'src/settings_security.py',
    'src/settings_services.py',
    'src/settings_smart_lock.py',
    'src/settings_standby.py',
    'src/settings_storage.py',
    'src/settings_ui.py',
    'src/settings_updates.py',
    'src/shell_log.py',
    'src/smart_lock.py',
    'src/sms.py',
    'src/sound.py',
    'src/status_bar.py',
    'src/system_settings.py',
    'src/system_status.py',
    'src/tailscale.py',
    'src/thumbnail_store.py',
    'src/toplevel_manager.py',
    'src/update_checker.py',
    'src/usage_stats.py',
    'src/wayland_bindings.py',
    'src/weather.py',
    'src/window.py',
    'src/xxdb.py',
    'src/xxdb_controller.py',
    'src/xxdb_daemon.py',
    'src/xxdb_input.py',
  ],
  install_dir: shell_datadir,
)

install_data(
  'src/style.css',
  install_dir: shell_datadir,
)

# Shade glyphs (symbolic, fill-only; regenerate with tools/shade_glyphs.py).
# quick_actions.ensure_shade_icons() adds this directory, next to the
# modules, to the icon-theme search path so Gtk.Image recolors them.
install_data(
  [
    'src/icons/xx-shade-airplane-symbolic.svg',
    'src/icons/xx-shade-auto-br-symbolic.svg',
    'src/icons/xx-shade-brightness-symbolic.svg',
    'src/icons/xx-shade-bt-symbolic.svg',
    'src/icons/xx-shade-captions-symbolic.svg',
    'src/icons/xx-shade-collapse-symbolic.svg',
    'src/icons/xx-shade-data-symbolic.svg',
    'src/icons/xx-shade-dnd-symbolic.svg',
    'src/icons/xx-shade-expand-symbolic.svg',
    'src/icons/xx-shade-focus-symbolic.svg',
    'src/icons/xx-shade-hotspot-symbolic.svg',
    'src/icons/xx-shade-location-symbolic.svg',
    'src/icons/xx-shade-night-symbolic.svg',
    'src/icons/xx-shade-nope-symbolic.svg',
    'src/icons/xx-shade-power-symbolic.svg',
    'src/icons/xx-shade-record-symbolic.svg',
    'src/icons/xx-shade-saver-symbolic.svg',
    'src/icons/xx-shade-settings-symbolic.svg',
    'src/icons/xx-shade-tailnet-symbolic.svg',
    'src/icons/xx-shade-torch-symbolic.svg',
    'src/icons/xx-shade-volume-symbolic.svg',
    'src/icons/xx-shade-wifi-symbolic.svg',
  ],
  install_dir: join_paths(shell_datadir, 'icons'),
)

install_data(
  'data/phoc.ini',
  install_dir: shell_datadir,
)

# Per-device phoc.ini scale fragments (20.5). install.sh picks one and copies
# it over phoc.ini at install time; shipping them lets the fragments travel
# with the package even when the repo is not present on the device.
install_data(
  'data/phoc/fairphone-5.ini',
  'data/phoc/furiphone-flx1.ini',
  'data/phoc/librem-5.ini',
  'data/phoc/tablet.ini',
  'data/phoc/qemu.ini',
  install_dir: join_paths(shell_datadir, 'phoc'),
)

# Wayland protocol XML (core + wlr extensions). Installed next to the
# shell modules; the pywayland client generates its bindings from these at
# runtime with the installed pywayland's own scanner (see
# src/wayland_bindings.py), cached per pywayland version and XML content.
# No generated modules are vendored or installed.
install_data(
  'data/wayland.xml',
  'data/wlr-foreign-toplevel-management-unstable-v1.xml',
  'data/wlr-output-power-management-unstable-v1.xml',
  'data/virtual-pointer-unstable-v1.xml',
  'data/wlr-screencopy-unstable-v1.xml',
  install_dir: shell_datadir,
)

install_data(
  'data/sounds/ringtone.ogg',
  'data/sounds/unknown.ogg',
  'data/sounds/notify.ogg',
  'data/sounds/NOTICE-original-sounds.txt',
  install_dir: join_paths(shell_datadir, 'sounds'),
)

# PiercingXX Colemak layouts for squeekboard (vendored from
# furi-phone-colemak-keyboard). install.sh symlinks this tree to
# ~/.local/share/squeekboard/keyboards/ — squeekboard only reads the user
# path. Subdirs are input-purpose variants squeekboard picks natively.
install_data(
  'data/squeekboard/us+colemak.yaml',
  'data/squeekboard/us+colemak_wide.yaml',
  install_dir: join_paths(shell_datadir, 'squeekboard'),
)
# squeekboard falls back to us.yaml when gsettings reports layout "us"
# (mru-sources often stays us even when sources is us+colemak).
install_data(
  'data/squeekboard/us+colemak.yaml',
  rename: 'us.yaml',
  install_dir: join_paths(shell_datadir, 'squeekboard'),
)
install_data(
  'data/squeekboard/us+colemak_wide.yaml',
  rename: 'us_wide.yaml',
  install_dir: join_paths(shell_datadir, 'squeekboard'),
)
install_data(
  'data/squeekboard/terminal/us+colemak.yaml',
  'data/squeekboard/terminal/us+colemak_wide.yaml',
  install_dir: join_paths(shell_datadir, 'squeekboard', 'terminal'),
)
install_data(
  'data/squeekboard/email/us+colemak.yaml',
  'data/squeekboard/email/us+colemak_wide.yaml',
  install_dir: join_paths(shell_datadir, 'squeekboard', 'email'),
)
install_data(
  'data/squeekboard/url/us+colemak.yaml',
  'data/squeekboard/url/us+colemak_wide.yaml',
  install_dir: join_paths(shell_datadir, 'squeekboard', 'url'),
)

# PiercingXX Colemak layout adapted to GNOME Shell's OSK JSON format (24).
# GDM's greeter OSK picks layouts from /usr/share/gnome-shell/osk-layouts/
# by name; install.sh offers a guarded step to overwrite us.json there
# (with backup). Shipping it lets that step work without the repo present.
install_data(
  'data/gnome-osk/us.json',
  install_dir: join_paths(shell_datadir, 'gnome-osk'),
)

# Shell Python launcher (runs main.py) → $prefix/bin/xx-wm
launcher_conf = configuration_data()
launcher_conf.set('python', python.full_path())
launcher_conf.set('shell_dir', join_paths(prefix, shell_datadir))
launcher_conf.set('bindir', bindir)

configure_file(
  input: 'data/xx-wm.in',
  output: 'xx-wm',
  configuration: launcher_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

# IPC client (lisgd bindings, wake hooks) → $prefix/bin/xx-wm-ipc
configure_file(
  input: 'data/xx-wm-ipc.in',
  output: 'xx-wm-ipc',
  configuration: launcher_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

# App .desktop entry → $prefix/share/applications/
desktop_conf = configuration_data()
desktop_conf.set('bindir', bindir)

configure_file(
  input: 'data/io.piercingxx.XXWM.desktop.in',
  output: 'io.piercingxx.XXWM.desktop',
  configuration: desktop_conf,
  install: true,
  install_dir: join_paths(get_option('datadir'), 'applications'),
)

# Shell icon -> hicolor theme (Icon=io.piercingxx.XXWM in the desktop).
install_data(
  'data/icons/io.piercingxx.XXWM.svg',
  install_dir: join_paths(get_option('datadir'), 'icons', 'hicolor',
                          'scalable', 'apps'),
)

# Session launcher (phoc wrapper) → $prefix/libexec/xx-wm-session
# Wayland session entry → $prefix/share/wayland-sessions/xx-wm.desktop
# Display managers only scan *.desktop here — a .session suffix is ignored.
session_conf = configuration_data()
session_conf.set('bindir', bindir)
session_conf.set('libexecdir', libexecdir)
session_conf.set('datadir', datadir_abs)

configure_file(
  input: 'data/xx-wm-session.in',
  output: 'xx-wm-session',
  configuration: session_conf,
  install: true,
  install_dir: get_option('libexecdir'),
  install_mode: 'rwxr-xr-x',
)

configure_file(
  input: 'data/xx-wm.desktop.in',
  output: 'xx-wm.desktop',
  configuration: session_conf,
  install: true,
  install_dir: join_paths(get_option('datadir'), 'wayland-sessions'),
)

# systemd user service
configure_file(
  input: 'data/systemd/xx-wm.service',
  output: 'xx-wm.service',
  configuration: session_conf,
  install: true,
  install_dir: join_paths(get_option('datadir'), 'systemd', 'user'),
)

# Session target, started by the in-session wrapper once phoc is up: pulls
# graphical-session.target in (BindsTo), which xdg-desktop-portal.service
# needs (Requisite=...). Static file, no substitution.
install_data(
  'data/systemd/xx-wm-session.target',
  install_dir: join_paths(get_option('datadir'), 'systemd', 'user'),
)

# Portal preferences, picked by the lowercased XDG_CURRENT_DESKTOP
# (XX-WM → xx-wm-portals.conf, see portals.conf(5)). GTK everywhere except
# screenshot/screencast, where phoc needs the wlroots backend.
install_data(
  'data/xx-wm-portals.conf',
  install_dir: join_paths(get_option('datadir'), 'xdg-desktop-portal'),
)

# OpenRC init script (for OpenRC-based images; postmarketOS default images
# are OpenRC). configure_file so command= uses the meson libexecdir (Arch
# often ships /usr/lib, not /usr/libexec). The build output must NOT be
# plain 'xx-wm': the launcher wrapper above already produces build/xx-wm and
# the later file would win, shipping the init script as /usr/bin/xx-wm.
# Configure to xx-wm.openrc, then install it renamed to init.d/xx-wm.
openrc_cfg = configure_file(
  input: 'data/openrc/xx-wm',
  output: 'xx-wm.openrc',
  configuration: session_conf,
)
install_data(
  openrc_cfg,
  rename: 'xx-wm',
  install_dir: join_paths(get_option('sysconfdir'), 'init.d'),
  install_mode: 'rwxr-xr-x',
)

# logind drop-in: let the shell own the power key instead of instant-poweroff
install_data(
  'data/logind/10-xx-wm-power.conf',
  install_dir: join_paths(get_option('sysconfdir'), 'systemd', 'logind.conf.d'),
)

# Privileged helper (root D-Bus system service io.piercingxx.XXWM.Helper1).
# The daemon itself: helper script with the python path baked in.
helper_conf = configuration_data()
helper_conf.set('python', python.full_path())
helper_conf.set('libexecdir', libexecdir)

configure_file(
  input: 'helper/xx_wm_helper.py',
  output: 'xx-wm-helper',
  configuration: helper_conf,
  install: true,
  install_dir: get_option('libexecdir'),
  install_mode: 'rwxr-xr-x',
)

# D-Bus activation (.service) + bus policy (.conf): the bus launches the
# helper on demand as root; per-method authorization stays polkit's job.
configure_file(
  input: 'data/dbus-1/system-services/io.piercingxx.XXWM.Helper1.service',
  output: 'io.piercingxx.XXWM.Helper1.service',
  configuration: helper_conf,
  install: true,
  install_dir: join_paths(get_option('datadir'), 'dbus-1', 'system-services'),
)

install_data(
  'data/dbus-1/system.d/io.piercingxx.XXWM.Helper1.conf',
  install_dir: join_paths(get_option('datadir'), 'dbus-1', 'system.d'),
)

install_data(
  'data/polkit-1/actions/io.piercingxx.XXWM.Helper.policy',
  install_dir: join_paths(get_option('datadir'), 'polkit-1', 'actions'),
)

# Boot re-apply of the persisted charge limit and nft blocks (oneshot,
# `--apply-saved`). Output names must stay unique: meson maps every
# configure_file output into the same build directory, so two outputs
# sharing a basename collide even with different inputs/install dirs.
# System units are only searched under lib/ (unlike user units, which
# systemd also reads from share/): ask systemd, fall back to prefix/lib.
systemd_dep = dependency('systemd', required: false)
if systemd_dep.found()
  system_unit_dir = systemd_dep.get_variable(pkgconfig: 'systemd_system_unit_dir',
    pkgconfig_define: ['prefix', get_option('prefix')])
else
  system_unit_dir = join_paths(get_option('prefix'), 'lib', 'systemd', 'system')
endif

configure_file(
  input: 'data/systemd/xx-wm-helper-boot.service',
  output: 'xx-wm-helper-boot.service',
  configuration: helper_conf,
  install: true,
  install_dir: system_unit_dir,
)

configure_file(
  input: 'data/openrc/xx-wm-helper-boot',
  output: 'xx-wm-helper-boot',
  configuration: helper_conf,
  install: true,
  install_dir: join_paths(get_option('sysconfdir'), 'init.d'),
  install_mode: 'rwxr-xr-x',
)
