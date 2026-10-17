project(
  'xx-email',
  version: '0.1.0',
  meson_version: '>=0.60.0',
)

python = import('python').find_installation('python3')

# The shared design system ships from common/meson.build — install
# common/ first so the xx_ui package is importable here and staged.
xx_ui_check = run_command(
  python, '-c', 'import xx_ui',
  check: false,
)
if xx_ui_check.returncode() != 0
  error('xx_ui python package is required (install common/ first)')
endif

prefix = get_option('prefix')
bindir = join_paths(prefix, get_option('bindir'))

# Like XX-WM, the Python package installs into the datadir (not
# site-packages); the bindir launcher points Python at it.
pkg_datadir = join_paths(get_option('datadir'), 'xx-email')

install_subdir(
  'src/xx_email',
  install_dir: pkg_datadir,
)

# Launcher wrapper → $prefix/bin/xx-email
launcher_conf = configuration_data()
launcher_conf.set('python', python.full_path())
launcher_conf.set('pkgdir', join_paths(prefix, pkg_datadir))

configure_file(
  input: 'data/xx-email.in',
  output: 'xx-email',
  configuration: launcher_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

# App .desktop entry → $prefix/share/applications/
desktop_conf = configuration_data()
desktop_conf.set('bindir', bindir)

configure_file(
  input: 'data/io.piercingxx.XXEmail.desktop.in',
  output: 'io.piercingxx.XXEmail.desktop',
  configuration: desktop_conf,
  install: true,
  install_dir: join_paths(get_option('datadir'), 'applications'),
)

# AppStream metadata → $prefix/share/metainfo/
install_data(
  'data/io.piercingxx.XXEmail.metainfo.xml',
  install_dir: join_paths(get_option('datadir'), 'metainfo'),
)

# OAuth walkthrough the setup screen links to → $prefix/share/xx-email/docs/
install_data(
  'docs/oauth-setup.md',
  install_dir: join_paths(pkg_datadir, 'docs'),
)

# Background poll units → $prefix/lib/systemd/user/ (enable with
# systemctl --user enable --now xx-email-sync.timer). The timer fires
# every 15 minutes; longer sync_minutes and the shell battery saver
# are enforced by the daemon tick itself, so no unit edit is needed.
unit_conf = configuration_data()
unit_conf.set('bindir', bindir)

configure_file(
  input: 'data/systemd/xx-email-sync.service.in',
  output: 'xx-email-sync.service',
  configuration: unit_conf,
  install: true,
  install_dir: join_paths('lib', 'systemd', 'user'),
)

install_data(
  'data/systemd/xx-email-sync.timer',
  install_dir: join_paths('lib', 'systemd', 'user'),
)

# D-Bus activation for the UI → $prefix/share/dbus-1/services/
dbus_conf = configuration_data()
dbus_conf.set('bindir', bindir)

configure_file(
  input: 'data/dbus-1/io.piercingxx.XXEmail.service.in',
  output: 'io.piercingxx.XXEmail.service',
  configuration: dbus_conf,
  install: true,
  install_dir: join_paths(get_option('datadir'), 'dbus-1', 'services'),
)

# D-Bus activation for notification actions: the shell auto-starts
# this on HandleNotificationAction so Archive / Mark read work after
# the oneshot poller exits.
configure_file(
  input: 'data/dbus-1/io.piercingxx.XXEmail.Daemon.service.in',
  output: 'io.piercingxx.XXEmail.Daemon.service',
  configuration: dbus_conf,
  install: true,
  install_dir: join_paths(get_option('datadir'), 'dbus-1', 'services'),
)
