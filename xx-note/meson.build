project(
  'xx-note',
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

app_id     = 'io.piercingxx.XXNote'
app_subdir = 'xx-note'
app_datadir = join_paths(datadir, app_subdir)

install_subdir(
  join_paths('src', 'xx_note'),
  install_dir: app_datadir,
  exclude_directories: ['__pycache__', 'ui/__pycache__'],
)

launcher_conf = configuration_data()
launcher_conf.set('python', python.full_path())
launcher_conf.set('app_dir', join_paths(prefix, app_datadir))

configure_file(
  input: 'data/xx-note.in',
  output: 'xx-note',
  configuration: launcher_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

desktop_conf = configuration_data()
desktop_conf.set('bindir', bindir)

configure_file(
  input: 'data/io.piercingxx.XXNote.desktop.in',
  output: 'io.piercingxx.XXNote.desktop',
  configuration: desktop_conf,
  install: true,
  install_dir: join_paths(datadir, 'applications'),
)

install_data(
  'data/io.piercingxx.XXNote.metainfo.xml',
  install_dir: join_paths(datadir, 'metainfo'),
)

install_data(
  'data/icons/io.piercingxx.XXNote.svg',
  install_dir: join_paths(datadir, 'icons', 'hicolor', 'scalable', 'apps'),
)
