project(
  'xx-auto',
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

app_id     = 'io.piercingxx.XXAuto'
app_subdir = 'xx-auto'
app_datadir = join_paths(datadir, app_subdir)

# The whole package (modules + icons): a hand-kept file list missed
# media_apps.py, so Radio/Audiobook read 'not installed' on a real phone.
install_subdir(
  join_paths('src', 'xx_auto'),
  install_dir: app_datadir,
  exclude_directories: ['__pycache__', 'icons/__pycache__'],
)

launcher_conf = configuration_data()
launcher_conf.set('python', python.full_path())
launcher_conf.set('app_dir', join_paths(prefix, app_datadir))

configure_file(
  input: 'data/xx-auto.in',
  output: 'xx-auto',
  configuration: launcher_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

desktop_conf = configuration_data()
desktop_conf.set('bindir', bindir)

configure_file(
  input: 'data/io.piercingxx.XXAuto.desktop.in',
  output: 'io.piercingxx.XXAuto.desktop',
  configuration: desktop_conf,
  install: true,
  install_dir: join_paths(datadir, 'applications'),
)

install_data(
  'data/io.piercingxx.XXAuto.metainfo.xml',
  install_dir: join_paths(datadir, 'metainfo'),
)

# App icon -> hicolor theme (Icon=io.piercingxx.XXAuto in the desktop).
# The file is the XX logomark until the owner's xx-auto mark lands;
# swapping that one file picks up the real mark.
install_data(
  'data/icons/io.piercingxx.XXAuto.svg',
  install_dir: join_paths(datadir, 'icons', 'hicolor', 'scalable', 'apps'),
)

systemd_conf = configuration_data()
systemd_conf.set('bindir', bindir)

configure_file(
  input: 'data/systemd/xx-auto-watch.service.in',
  output: 'xx-auto-watch.service',
  configuration: systemd_conf,
  install: true,
  install_dir: join_paths(datadir, 'systemd', 'user'),
)
