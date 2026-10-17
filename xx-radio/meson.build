project(
  'xx-radio',
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

app_id = 'io.piercingxx.XXRadio'
app_subdir = 'xx-radio'
app_datadir = join_paths(datadir, app_subdir)

# Python packages: installed flat into the datadir like XX-WM does. Two
# packages side by side: xx_music (the reusable, source-agnostic core) and
# xx_radio (this app: its AppSpec and the YouTube Music engine).
foreach pkg : ['xx_music', 'xx_radio']
  install_subdir(
    join_paths('src', pkg),
    install_dir: app_datadir,
    exclude_directories: ['__pycache__'],
  )
endforeach

# Launcher -> $prefix/bin/xx-radio
launcher_conf = configuration_data()
launcher_conf.set('python', python.full_path())
launcher_conf.set('app_dir', join_paths(prefix, app_datadir))

configure_file(
  input: 'data/xx-radio.in',
  output: 'xx-radio',
  configuration: launcher_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

# App .desktop entry -> $prefix/share/applications/
desktop_conf = configuration_data()
desktop_conf.set('bindir', bindir)

configure_file(
  input: 'data/io.piercingxx.XXRadio.desktop.in',
  output: 'io.piercingxx.XXRadio.desktop',
  configuration: desktop_conf,
  install: true,
  install_dir: join_paths(datadir, 'applications'),
)

# Metainfo -> $prefix/share/metainfo/
install_data(
  'data/io.piercingxx.XXRadio.metainfo.xml',
  install_dir: join_paths(datadir, 'metainfo'),
)

# Symbolic icon -> $prefix/share/icons/hicolor/scalable/apps/
install_data(
  'data/icons/io.piercingxx.XXRadio.svg',
  install_dir: join_paths(datadir, 'icons', 'hicolor', 'scalable', 'apps'),
)

# D-Bus session activation: the .desktop is DBusActivatable, so the bus
# must know how to start the app. Name= must equal the owned name.
# The player service owns its own name (never the app id, or the GUI
# would exit as a secondary instance) and idle-exits, so it ships its
# own activation file or the first idle exit breaks every client.
dbus_conf = configuration_data()
dbus_conf.set('bindir', bindir)

configure_file(
  input: 'data/io.piercingxx.XXRadio.service.in',
  output: 'io.piercingxx.XXRadio.service',
  configuration: dbus_conf,
  install: true,
  install_dir: join_paths(datadir, 'dbus-1', 'services'),
)

configure_file(
  input: 'data/io.piercingxx.XXRadio.Player.service.in',
  output: 'io.piercingxx.XXRadio.Player.service',
  configuration: dbus_conf,
  install: true,
  install_dir: join_paths(datadir, 'dbus-1', 'services'),
)
