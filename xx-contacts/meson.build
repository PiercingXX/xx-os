project(
  'xx-contacts',
  version: '0.1.0',
  meson_version: '>=0.60.0',
)

python = import('python').find_installation('python3')

# The shared design system lives outside this app: common/meson.build
# installs the xx_ui python package, and this app imports it at runtime.
xx_ui_check = run_command(python, '-c', 'import xx_ui', check: false)
if xx_ui_check.returncode() != 0
  error('xx_ui python package is required: install common/ first, '
    + 'then configure this app')
endif

prefix = get_option('prefix')
bindir = join_paths(prefix, get_option('bindir'))
app_subdir = 'xx-contacts'
app_datadir = join_paths(get_option('datadir'), app_subdir)

install_subdir(
  'src/xx_contacts',
  install_dir: join_paths(get_option('datadir'), app_subdir),
)

launcher_conf = configuration_data()
launcher_conf.set('python', python.full_path())
launcher_conf.set('app_dir', join_paths(prefix, app_datadir))

configure_file(
  input: 'data/xx-contacts.in',
  output: 'xx-contacts',
  configuration: launcher_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

desktop_conf = configuration_data()
desktop_conf.set('bindir', bindir)

configure_file(
  input: 'data/io.piercingxx.XXContacts.desktop.in',
  output: 'io.piercingxx.XXContacts.desktop',
  configuration: desktop_conf,
  install: true,
  install_dir: join_paths(get_option('datadir'), 'applications'),
)

install_data(
  'data/io.piercingxx.XXContacts.metainfo.xml',
  install_dir: join_paths(get_option('datadir'), 'metainfo'),
)

# App icon -> hicolor theme (Icon=io.piercingxx.XXContacts in the desktop)
install_data(
  'data/icons/io.piercingxx.XXContacts.svg',
  install_dir: join_paths(get_option('datadir'), 'icons', 'hicolor',
                          'scalable', 'apps'),
)

# D-Bus activation for the contacts service: without this file the
# first idle exit (60 s) silently breaks every client, since nothing
# can re-start io.piercingxx.Contacts on demand. No SystemdService=
# line: no systemd unit ships, so systemd activation stays off and
# classic D-Bus activation owns the lifecycle.
dbus_service_conf = configuration_data()
dbus_service_conf.set('bindir', bindir)

configure_file(
  input: 'data/io.piercingxx.Contacts.service.in',
  output: 'io.piercingxx.Contacts.service',
  configuration: dbus_service_conf,
  install: true,
  install_dir: join_paths(get_option('datadir'), 'dbus-1', 'services'),
)
