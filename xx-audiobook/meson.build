project(
  'xx-audiobook',
  version: '0.1.0',
  meson_version: '>=0.60.0',
)

python = import('python').find_installation('python3')

# The shared design system must be installed first (meson setup build &&
# meson install -C build in common/); the app imports xx_ui at runtime and
# never vendors a copy.
xx_ui_check = run_command(
  python, '-c', 'import xx_ui',
  check: false,
)
if xx_ui_check.returncode() != 0
  error('xx_ui python package not found: install common/ first')
endif

prefix = get_option('prefix')
bindir = join_paths(prefix, get_option('bindir'))
datadir = get_option('datadir')

app_id = 'io.piercingxx.XXAudiobook'
app_subdir = 'xx-audiobook'
app_datadir = join_paths(datadir, app_subdir)

# Python package: installed flat into the datadir like XX-WM does, so the
# launcher runs `python3 -m xx_audiobook`-style entry with sibling imports.
install_data(
  [
    'src/xx_audiobook/__init__.py',
    'src/xx_audiobook/__main__.py',
    'src/xx_audiobook/main.py',
    'src/xx_audiobook/paths.py',
    'src/xx_audiobook/prefs.py',
    'src/xx_audiobook/auth.py',
    'src/xx_audiobook/api.py',
    'src/xx_audiobook/store.py',
    'src/xx_audiobook/repos.py',
    'src/xx_audiobook/api_routes.py',
    'src/xx_audiobook/applog.py',
    'src/xx_audiobook/timeline.py',
    'src/xx_audiobook/stream_auth.py',
    'src/xx_audiobook/sleep.py',
    'src/xx_audiobook/sync.py',
    'src/xx_audiobook/restore.py',
    'src/xx_audiobook/player.py',
    'src/xx_audiobook/download.py',
    'src/xx_audiobook/fmt.py',
    'src/xx_audiobook/browse.py',
    'src/xx_audiobook/bookmarks.py',
    'src/xx_audiobook/reviews.py',
    'src/xx_audiobook/stats.py',
    'src/xx_audiobook/zones.py',
  ],
  install_dir: join_paths(app_datadir, 'xx_audiobook'),
)

# GTK pages (M3): Library, Book, Now Playing, pushed views, shell.
install_data(
  [
    'src/xx_audiobook/ui/__init__.py',
    'src/xx_audiobook/ui/book.py',
    'src/xx_audiobook/ui/browse.py',
    'src/xx_audiobook/ui/common.py',
    'src/xx_audiobook/ui/context.py',
    'src/xx_audiobook/ui/library.py',
    'src/xx_audiobook/ui/playing.py',
    'src/xx_audiobook/ui/shell.py',
    'src/xx_audiobook/ui/style.py',
  ],
  install_dir: join_paths(app_datadir, 'xx_audiobook', 'ui'),
)

# Player service subpackage (background daemon: own bus name + MPRIS).
install_data(
  [
    'src/xx_audiobook/service/__init__.py',
    'src/xx_audiobook/service/player_service.py',
  ],
  install_dir: join_paths(app_datadir, 'xx_audiobook', 'service'),
)

# Launcher -> $prefix/bin/xx-audiobook
launcher_conf = configuration_data()
launcher_conf.set('python', python.full_path())
launcher_conf.set('app_dir', join_paths(prefix, app_datadir))

configure_file(
  input: 'data/xx-audiobook.in',
  output: 'xx-audiobook',
  configuration: launcher_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

# App .desktop entry -> $prefix/share/applications/
desktop_conf = configuration_data()
desktop_conf.set('bindir', bindir)

configure_file(
  input: 'data/io.piercingxx.XXAudiobook.desktop.in',
  output: 'io.piercingxx.XXAudiobook.desktop',
  configuration: desktop_conf,
  install: true,
  install_dir: join_paths(datadir, 'applications'),
)

# Metainfo -> $prefix/share/metainfo/
install_data(
  'data/io.piercingxx.XXAudiobook.metainfo.xml',
  install_dir: join_paths(datadir, 'metainfo'),
)

# Symbolic icon -> $prefix/share/icons/hicolor/scalable/apps/
install_data(
  'data/icons/io.piercingxx.XXAudiobook.svg',
  install_dir: join_paths(datadir, 'icons', 'hicolor', 'scalable', 'apps'),
)

# D-Bus session activation: the .desktop is DBusActivatable, so the bus
# must know how to start the app. Without this file the first launch
# from the drawer fails to activate. Name= must equal the owned name.
dbus_conf = configuration_data()
dbus_conf.set('bindir', bindir)

configure_file(
  input: 'data/io.piercingxx.XXAudiobook.service.in',
  output: 'io.piercingxx.XXAudiobook.service',
  configuration: dbus_conf,
  install: true,
  install_dir: join_paths(datadir, 'dbus-1', 'services'),
)

# Player daemon launcher -> $prefix/bin/xx-audiobook-player
configure_file(
  input: 'data/xx-audiobook-player.in',
  output: 'xx-audiobook-player',
  configuration: launcher_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

# Player activation: the daemon owns io.piercingxx.XXAudiobook.Player
# (never the app id, or the GUI would exit as a secondary instance),
# so idle-exit never breaks clients. Name= must equal the owned name.
configure_file(
  input: 'data/io.piercingxx.XXAudiobook.Player.service.in',
  output: 'io.piercingxx.XXAudiobook.Player.service',
  configuration: dbus_conf,
  install: true,
  install_dir: join_paths(datadir, 'dbus-1', 'services'),
)

# Download worker launcher -> $prefix/bin/xx-audiobook-download
configure_file(
  input: 'data/xx-audiobook-download.in',
  output: 'xx-audiobook-download',
  configuration: launcher_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

# Background downloads: systemd user units (opt-in; enable explicitly,
# never auto-enabled at install). The per-book template is started by
# the app on enqueue; the sync timer sweeps Queued rows every 15 min.
systemd_conf = configuration_data()
systemd_conf.set('bindir', bindir)

configure_file(
  input: 'data/systemd/xx-audiobook-download@.service.in',
  output: 'xx-audiobook-download@.service',
  configuration: systemd_conf,
  install: true,
  install_dir: join_paths(datadir, 'systemd', 'user'),
)

configure_file(
  input: 'data/systemd/xx-audiobook-sync.service.in',
  output: 'xx-audiobook-sync.service',
  configuration: systemd_conf,
  install: true,
  install_dir: join_paths(datadir, 'systemd', 'user'),
)

install_data(
  'data/systemd/xx-audiobook-sync.timer',
  install_dir: join_paths(datadir, 'systemd', 'user'),
)
