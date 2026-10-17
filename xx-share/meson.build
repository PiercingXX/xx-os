project(
  'xx-share',
  version: '0.1.0',
  meson_version: '>=0.60.0',
)

python = import('python').find_installation('python3')

# The UI is built on the shared xx_ui design system: install common/
# first so `import xx_ui` resolves at runtime.
xx_ui_check = run_command(
  python, '-c', 'import xx_ui',
  check: false,
)
if xx_ui_check.returncode() != 0
  error('xx_ui not found for python. Install common/ first (common/meson.build).')
endif

prefix     = get_option('prefix')
bindir     = join_paths(prefix, get_option('bindir'))
libexecdir = join_paths(prefix, get_option('libexecdir'))

app_subdir = 'xx-share'
app_datadir = join_paths(get_option('datadir'), app_subdir)

# Flat modules like the shell: plain imports (`from window import ...`
# style) and no package. Every .py lands next to each other and runs as
# `python3 <app_datadir>/main.py`.
install_data(
  [
    'src/main.py',
    'src/protocol.py',
    'src/certs.py',
    'src/netif.py',
    'src/sanitize.py',
    'src/config.py',
    'src/server.py',
    'src/discovery.py',
    'src/client.py',
    'src/notify.py',
    'src/service.py',
    'src/ui.py',
    'src/share_bus.py',
    'src/history.py',
    'src/favorites.py',
    'src/transfers.py',
  ],
  install_dir: app_datadir,
)

launcher_conf = configuration_data()
launcher_conf.set('python', python.full_path())
launcher_conf.set('app_dir', join_paths(prefix, app_datadir))

configure_file(
  input: 'data/xx-share.in',
  output: 'xx-share',
  configuration: launcher_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

desktop_conf = configuration_data()
desktop_conf.set('bindir', bindir)

configure_file(
  input: 'data/io.piercingxx.XXShare.desktop.in',
  output: 'io.piercingxx.XXShare.desktop',
  configuration: desktop_conf,
  install: true,
  install_dir: join_paths(get_option('datadir'), 'applications'),
)

metainfo_conf = configuration_data()
metainfo_conf.set('app_dir', join_paths(prefix, app_datadir))

configure_file(
  input: 'data/io.piercingxx.XXShare.metainfo.xml.in',
  output: 'io.piercingxx.XXShare.metainfo.xml',
  configuration: metainfo_conf,
  install: true,
  install_dir: join_paths(get_option('datadir'), 'metainfo'),
)

# App icon -> hicolor theme (Icon=io.piercingxx.XXShare in the desktop).
# The file is the XX logomark until the owner's xx-share mark lands;
# swapping that one file picks up the real mark.
install_data(
  'data/icons/io.piercingxx.XXShare.svg',
  install_dir: join_paths(get_option('datadir'), 'icons', 'hicolor',
                          'scalable', 'apps'),
)

service_conf = configuration_data()
service_conf.set('bindir', bindir)

# User unit, NOT enabled at install: the app starts it only when the
# user enables "Visible to nearby devices".
configure_file(
  input: 'data/systemd/xx-share.service.in',
  output: 'xx-share.service',
  configuration: service_conf,
  install: true,
  install_dir: join_paths(get_option('datadir'), 'systemd', 'user'),
)
