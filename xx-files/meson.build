project(
  'xx-files',
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

app_id     = 'io.piercingxx.XXFiles'
app_subdir = 'xx-files'
app_datadir = join_paths(datadir, app_subdir)

# The whole package: a hand-kept file list once missed a module on a
# sibling, so the package directory installs as one unit.
install_subdir(
  join_paths('src', 'xx_files'),
  install_dir: app_datadir,
  exclude_directories: ['__pycache__'],
)

launcher_conf = configuration_data()
launcher_conf.set('python', python.full_path())
launcher_conf.set('app_dir', join_paths(prefix, app_datadir))

configure_file(
  input: 'data/xx-files.in',
  output: 'xx-files',
  configuration: launcher_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

desktop_conf = configuration_data()
desktop_conf.set('bindir', bindir)

configure_file(
  input: 'data/io.piercingxx.XXFiles.desktop.in',
  output: 'io.piercingxx.XXFiles.desktop',
  configuration: desktop_conf,
  install: true,
  install_dir: join_paths(datadir, 'applications'),
)

install_data(
  'data/io.piercingxx.XXFiles.metainfo.xml',
  install_dir: join_paths(datadir, 'metainfo'),
)

# App icon -> hicolor theme (Icon=io.piercingxx.XXFiles in the desktop).
# A stand-in folder mark until the owner's xx-files mark lands;
# swapping that one file picks up the real mark.
install_data(
  'data/icons/io.piercingxx.XXFiles.svg',
  install_dir: join_paths(datadir, 'icons', 'hicolor', 'scalable', 'apps'),
)

# D-Bus session activation: the .desktop is DBusActivatable, so the bus
# must know how to start the app. Without this file the first launch
# from the drawer fails to activate.
dbus_conf = configuration_data()
dbus_conf.set('bindir', bindir)

configure_file(
  input: 'data/io.piercingxx.XXFiles.service.in',
  output: 'io.piercingxx.XXFiles.service',
  configuration: dbus_conf,
  install: true,
  install_dir: join_paths(datadir, 'dbus-1', 'services'),
)
