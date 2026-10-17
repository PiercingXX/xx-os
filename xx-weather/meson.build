project(
  'xx-weather',
  version: '0.1.0',
  meson_version: '>=0.60.0',
)

python = import('python').find_installation('python3')

xx_ui_check = run_command(
  python, ['-c', 'import xx_ui'],
  check: false,
)
if xx_ui_check.returncode() != 0
  error('xx_ui python package not found: install common/ first')
endif

prefix     = get_option('prefix')
bindir     = join_paths(prefix, get_option('bindir'))
datadir    = get_option('datadir')

app_id     = 'io.piercingxx.XXWeather'
app_subdir = 'xx-weather'
app_datadir = join_paths(datadir, app_subdir)

# The whole package: a hand-kept file list once missed a module on a
# sibling app, so install the directory instead.
install_subdir(
  join_paths('src', 'xx_weather'),
  install_dir: app_datadir,
  exclude_directories: ['__pycache__'],
)

launcher_conf = configuration_data()
launcher_conf.set('python', python.full_path())
launcher_conf.set('app_dir', join_paths(prefix, app_datadir))

configure_file(
  input: 'data/xx-weather.in',
  output: 'xx-weather',
  configuration: launcher_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

desktop_conf = configuration_data()
desktop_conf.set('bindir', bindir)

configure_file(
  input: 'data/io.piercingxx.XXWeather.desktop.in',
  output: 'io.piercingxx.XXWeather.desktop',
  configuration: desktop_conf,
  install: true,
  install_dir: join_paths(datadir, 'applications'),
)

install_data(
  'data/io.piercingxx.XXWeather.metainfo.xml',
  install_dir: join_paths(datadir, 'metainfo'),
)

# App icon -> hicolor theme (Icon=io.piercingxx.XXWeather in the desktop).
# The file is the Android sun mark until the owner's xx-weather mark lands;
# swapping that one file picks up the real mark.
install_data(
  'data/icons/io.piercingxx.XXWeather.svg',
  install_dir: join_paths(datadir, 'icons', 'hicolor', 'scalable', 'apps'),
)

# Background refresh: systemd user service + timer (opt-in; enable
# explicitly, never auto-enabled at install).
systemd_conf = configuration_data()
systemd_conf.set('bindir', bindir)

configure_file(
  input: 'data/systemd/xx-weather-refresh.service.in',
  output: 'xx-weather-refresh.service',
  configuration: systemd_conf,
  install: true,
  install_dir: join_paths(datadir, 'systemd', 'user'),
)

install_data(
  'data/systemd/xx-weather-refresh.timer',
  install_dir: join_paths(datadir, 'systemd', 'user'),
)
