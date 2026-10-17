project(
  'xx-vitals',
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

app_id = 'io.piercingxx.XXVitals'
app_subdir = 'xx-vitals'
app_datadir = join_paths(datadir, app_subdir)

# Python package: installed flat into the datadir like XX-WM does, so the
# launcher runs `python3 <datadir>/main.py`-style entry with sibling imports.
install_data(
  [
    'src/xx_vitals/__init__.py',
    'src/xx_vitals/__main__.py',
    'src/xx_vitals/main.py',
    'src/xx_vitals/paths.py',
    'src/xx_vitals/prefs.py',
    'src/xx_vitals/sync_wire.py',
    'src/xx_vitals/units.py',
  ],
  install_dir: join_paths(app_datadir, 'xx_vitals'),
)

install_data(
  [
    'src/xx_vitals/core/__init__.py',
    'src/xx_vitals/core/applog.py',
    'src/xx_vitals/core/browse.py',
    'src/xx_vitals/core/bucketing.py',
    'src/xx_vitals/core/detail.py',
    'src/xx_vitals/core/format.py',
    'src/xx_vitals/core/home.py',
    'src/xx_vitals/core/journal.py',
    'src/xx_vitals/core/metrics.py',
    'src/xx_vitals/core/profile_edit.py',
    'src/xx_vitals/core/takeout.py',
  ],
  install_dir: join_paths(app_datadir, 'xx_vitals', 'core'),
)

install_data(
  [
    'src/xx_vitals/store/__init__.py',
    'src/xx_vitals/store/db.py',
    'src/xx_vitals/store/entries.py',
    'src/xx_vitals/store/prefs.py',
  ],
  install_dir: join_paths(app_datadir, 'xx_vitals', 'store'),
)

install_data(
  [
    'src/xx_vitals/sync/__init__.py',
    'src/xx_vitals/sync/api.py',
    'src/xx_vitals/sync/auth.py',
    'src/xx_vitals/sync/engine.py',
    'src/xx_vitals/sync/http.py',
    'src/xx_vitals/sync/http_api.py',
    'src/xx_vitals/sync/status.py',
    'src/xx_vitals/sync/worker.py',
  ],
  install_dir: join_paths(app_datadir, 'xx_vitals', 'sync'),
)

install_data(
  [
    'src/xx_vitals/collect/__init__.py',
    'src/xx_vitals/collect/burst.py',
    'src/xx_vitals/collect/inhibit.py',
    'src/xx_vitals/collect/resident.py',
    'src/xx_vitals/collect/sampler.py',
    'src/xx_vitals/collect/sensors.py',
    'src/xx_vitals/collect/service.py',
    'src/xx_vitals/collect/status.py',
  ],
  install_dir: join_paths(app_datadir, 'xx_vitals', 'collect'),
)

install_data(
  [
    'src/xx_vitals/ui/__init__.py',
    'src/xx_vitals/ui/browse.py',
    'src/xx_vitals/ui/entry.py',
    'src/xx_vitals/ui/home.py',
    'src/xx_vitals/ui/journal.py',
    'src/xx_vitals/ui/profile.py',
    'src/xx_vitals/ui/shell.py',
  ],
  install_dir: join_paths(app_datadir, 'xx_vitals', 'ui'),
)

# Launchers -> $prefix/bin/xx-vitals and xx-vitals-collect
launcher_conf = configuration_data()
launcher_conf.set('python', python.full_path())
launcher_conf.set('app_dir', join_paths(prefix, app_datadir))

configure_file(
  input: 'data/xx-vitals.in',
  output: 'xx-vitals',
  configuration: launcher_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

configure_file(
  input: 'data/xx-vitals-collect.in',
  output: 'xx-vitals-collect',
  configuration: launcher_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

# App .desktop entry -> $prefix/share/applications/
desktop_conf = configuration_data()
desktop_conf.set('bindir', bindir)

configure_file(
  input: 'data/io.piercingxx.XXVitals.desktop.in',
  output: 'io.piercingxx.XXVitals.desktop',
  configuration: desktop_conf,
  install: true,
  install_dir: join_paths(datadir, 'applications'),
)

# Metainfo -> $prefix/share/metainfo/
install_data(
  'data/io.piercingxx.XXVitals.metainfo.xml',
  install_dir: join_paths(datadir, 'metainfo'),
)

# App icon -> hicolor theme (Icon=io.piercingxx.XXVitals in the desktop)
install_data(
  'data/icons/io.piercingxx.XXVitals.svg',
  install_dir: join_paths(datadir, 'icons', 'hicolor', 'scalable', 'apps'),
)

# D-Bus session activation: the .desktop is DBusActivatable, so the bus
# must know how to start the app. Without this file the first launch
# from the drawer fails to activate.
dbus_conf = configuration_data()
dbus_conf.set('bindir', bindir)

configure_file(
  input: 'data/io.piercingxx.XXVitals.service.in',
  output: 'io.piercingxx.XXVitals.service',
  configuration: dbus_conf,
  install: true,
  install_dir: join_paths(datadir, 'dbus-1', 'services'),
)

# Background collection: systemd user service + timers (opt-in; enable
# explicitly, never auto-enabled at install).
systemd_conf = configuration_data()
systemd_conf.set('bindir', bindir)

configure_file(
  input: 'data/systemd/xx-vitals-collect.service.in',
  output: 'xx-vitals-collect.service',
  configuration: systemd_conf,
  install: true,
  install_dir: join_paths(datadir, 'systemd', 'user'),
)

configure_file(
  input: 'data/systemd/xx-vitals-collect-hw.service.in',
  output: 'xx-vitals-collect-hw.service',
  configuration: systemd_conf,
  install: true,
  install_dir: join_paths(datadir, 'systemd', 'user'),
)

configure_file(
  input: 'data/systemd/xx-vitals-sync.service.in',
  output: 'xx-vitals-sync.service',
  configuration: systemd_conf,
  install: true,
  install_dir: join_paths(datadir, 'systemd', 'user'),
)

install_data(
  'data/systemd/xx-vitals-collect.timer',
  'data/systemd/xx-vitals-sync.timer',
  install_dir: join_paths(datadir, 'systemd', 'user'),
)
