project(
  'txxt',
  version: '0.1.0',
  meson_version: '>=0.60.0',
)

python = import('python').find_installation('python3')

# The UI runs on the shared xx_ui design system: install common/ first so
# the xx_ui package is importable, here and on the device.
xx_ui_check = run_command(python, '-c', 'import xx_ui', check: false)
if xx_ui_check.returncode() != 0
  error('txxt requires the xx_ui package: configure and install common/ first')
endif

prefix = get_option('prefix')
datadir = join_paths(prefix, get_option('datadir'))
bindir = join_paths(prefix, get_option('bindir'))
pkgdir = join_paths(get_option('datadir'), 'txxt')

install_data(
  [
    'src/txxt/__init__.py',
    'src/txxt/__main__.py',
    'src/txxt/main.py',
    'src/txxt/settings.py',
    'src/txxt/backup.py',
    'src/txxt/blocking.py',
    'src/txxt/phone_numbers.py',
    'src/txxt/conversations_core.py',
    'src/txxt/store.py',
    'src/txxt/mm_client.py',
    'src/txxt/daemon.py',
    'src/txxt/notify.py',
    'src/txxt/contacts.py',
    'src/txxt/contacts_eds.py',
    'src/txxt/compose.py',
    'src/txxt/conversations.py',
    'src/txxt/known_senders.py',
    'src/txxt/list_presenter.py',
    'src/txxt/thread_page.py',
    'src/txxt/txxt_client.py',
    'src/txxt/media.py',
    'src/txxt/receive_policy.py',
    'src/txxt/settings_page.py',
    'src/txxt/blocking_page.py',
    'src/txxt/quarantine_page.py',
    'src/txxt/new_conversation_page.py',
  ],
  install_dir: pkgdir,
)

launcher_conf = configuration_data()
launcher_conf.set('pkgdir_parent', datadir)

configure_file(
  input: 'data/txxt.in',
  output: 'txxt',
  configuration: launcher_conf,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

install_data(
  'data/io.piercingxx.TxxT.desktop',
  install_dir: join_paths(get_option('datadir'), 'applications'),
)

install_data(
  'data/io.piercingxx.TxxT.metainfo.xml',
  install_dir: join_paths(get_option('datadir'), 'metainfo'),
)

install_data(
  'data/icons/io.piercingxx.TxxT.svg',
  install_dir: join_paths(get_option('datadir'), 'icons', 'hicolor',
                          'scalable', 'apps'),
)

# D-Bus activation for the GUI: the desktop entry is DBusActivatable,
# so the bus must know how to start it. Without this file launchers
# that activate over D-Bus fail silently (only the Daemon service
# existed before). Name= must equal the desktop app id, never the
# daemon name, or the GUI would exit as a secondary instance.
gui_service_conf = configuration_data()
gui_service_conf.set('bindir', bindir)

configure_file(
  input: 'data/io.piercingxx.TxxT.service.in',
  output: 'io.piercingxx.TxxT.service',
  configuration: gui_service_conf,
  install: true,
  install_dir: join_paths(datadir, 'dbus-1', 'services'),
)

install_data(
  'data/txxt-daemon.service',
  install_dir: join_paths(get_option('datadir'), 'txxt'),
)

install_data(
  'data/io.piercingxx.TxxT.Daemon.service',
  install_dir: join_paths(datadir, 'dbus-1', 'services'),
)

# Absolute: sessions only read /etc/xdg/autostart (plus ~/.config/autostart),
# never a prefix-relative etc dir.
install_data(
  'data/io.piercingxx.TxxT.Daemon.desktop',
  install_dir: '/etc/xdg/autostart',
)
