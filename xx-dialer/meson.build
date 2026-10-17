project(
  'xx-dialer',
  version: '0.1.0',
  meson_version: '>=0.60.0',
)

python = import('python').find_installation('python3')

# Shared design system: common/meson.build installs the xx_ui python
# package (install common/ first). Fail early when it is missing.
xx_ui_check = run_command(
  python, '-c', 'import xx_ui',
  check: false,
)
if xx_ui_check.returncode() != 0
  error('xx_ui python package is required: install common/ first (see README.md)')
endif

prefix = get_option('prefix')
bindir = join_paths(prefix, get_option('bindir'))
datadir = get_option('datadir')

app_subdir = 'xx-dialer'
app_datadir = join_paths(datadir, app_subdir)

# The app is a plain Python package: it is installed to the datadir like
# XX-WM does (not site-packages), and the bindir launcher sets PYTHONPATH
# so `python3 -m xx_dialer` resolves from the installed tree. Explicit
# file list (XX-WM style) so bytecode caches never ship.
install_data(
  [
    'src/xx_dialer/__init__.py',
    'src/xx_dialer/__main__.py',
    'src/xx_dialer/applog.py',
    'src/xx_dialer/main.py',
    'src/xx_dialer/policy.py',
    'src/xx_dialer/dialer.css',
    'src/xx_dialer/config.py',
    'src/xx_dialer/store.py',
    'src/xx_dialer/codecs.py',
    'src/xx_dialer/importers.py',
    'src/xx_dialer/contacts_client.py',
    'src/xx_dialer/t9.py',
    'src/xx_dialer/masks.py',
    'src/xx_dialer/modem.py',
    'src/xx_dialer/facts.py',
    'src/xx_dialer/service.py',
    'src/xx_dialer/recents.py',
    'src/xx_dialer/ringer.py',
    'src/xx_dialer/ui_recents.py',
    'src/xx_dialer/ui_keypad.py',
    'src/xx_dialer/rules.py',
    'src/xx_dialer/setup.py',
    'src/xx_dialer/ui_backup.py',
    'src/xx_dialer/ui_logs.py',
    'src/xx_dialer/ui_rules.py',
    'src/xx_dialer/ui_people.py',
    'src/xx_dialer/ui_setup.py',
  ],
  install_dir: join_paths(app_datadir, 'xx_dialer'),
)

# Launcher (runs the installed package) -> $prefix/bin/xx-dialer
launcher_conf = configuration_data()
launcher_conf.set('python', python.full_path())
launcher_conf.set('appdir', join_paths(prefix, app_datadir))

configure_file(
  input: 'data/xx-dialer.in',
  output: 'xx-dialer',
  configuration: launcher_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

# App .desktop entry (app id io.piercingxx.XXDialer) -> applications dir
desktop_conf = configuration_data()
desktop_conf.set('bindir', bindir)

configure_file(
  input: 'data/io.piercingxx.XXDialer.desktop.in',
  output: 'io.piercingxx.XXDialer.desktop',
  configuration: desktop_conf,
  install: true,
  install_dir: join_paths(datadir, 'applications'),
)

# AppStream metainfo -> metainfo dir
install_data(
  'data/io.piercingxx.XXDialer.metainfo.xml',
  install_dir: join_paths(datadir, 'metainfo'),
)

# App icon -> hicolor theme (Icon=io.piercingxx.XXDialer in the desktop)
install_data(
  'data/icons/io.piercingxx.XXDialer.svg',
  install_dir: join_paths(datadir, 'icons', 'hicolor', 'scalable', 'apps'),
)

# D-Bus activation for the GUI: the desktop entry is DBusActivatable,
# so the bus must know how to start it (only the Policy service
# existed before, and launchers failed silently). Name= must equal
# the desktop app id, never the policy name, or the GUI would exit
# as a secondary instance.
configure_file(
  input: 'data/io.piercingxx.XXDialer.service.in',
  output: 'io.piercingxx.XXDialer.service',
  configuration: desktop_conf,
  install: true,
  install_dir: join_paths(datadir, 'dbus-1', 'services'),
)

# D-Bus activation for the policy service -> session bus services dir
# (its own bus name: the GTK app id io.piercingxx.XXDialer must stay
# free for the GUI's Gtk.Application registration)
configure_file(
  input: 'data/io.piercingxx.XXDialer.Policy.service.in',
  output: 'io.piercingxx.XXDialer.Policy.service',
  configuration: desktop_conf,
  install: true,
  install_dir: join_paths(datadir, 'dbus-1', 'services'),
)
