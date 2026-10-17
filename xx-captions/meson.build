project(
  'xx-captions',
  version: '0.1.0',
  meson_version: '>=0.60.0',
)

python = import('python').find_installation('python3')

# The overlay and settings windows run on the shared design system:
# configure and install common/ first so `import xx_ui` resolves.
xx_ui_check = run_command(python, '-c', 'import xx_ui', check: false)
if xx_ui_check.returncode() != 0
  error('xx_ui is not installed; configure and install common/ first')
endif

prefix = get_option('prefix')
bindir = join_paths(prefix, get_option('bindir'))

python.install_sources(
  'src/xx_captions/__init__.py',
  'src/xx_captions/__main__.py',
  'src/xx_captions/audio.py',
  'src/xx_captions/capture.py',
  'src/xx_captions/client.py',
  'src/xx_captions/config.py',
  'src/xx_captions/dbus_api.py',
  'src/xx_captions/engine.py',
  'src/xx_captions/models.py',
  'src/xx_captions/overlay.py',
  'src/xx_captions/overlay_state.py',
  'src/xx_captions/resample.py',
  'src/xx_captions/service.py',
  'src/xx_captions/settings.py',
  'src/xx_captions/store.py',
  subdir: 'xx_captions',
)

install_data(
  'src/xx_captions/captions.css',
  install_dir: join_paths(python.get_install_dir(), 'xx_captions'),
)

python.install_sources(
  'src/xx_captions/engines/__init__.py',
  'src/xx_captions/engines/sherpa.py',
  'src/xx_captions/engines/vosk.py',
  subdir: 'xx_captions/engines',
)

launcher_conf = configuration_data()
launcher_conf.set('python', python.full_path())

configure_file(
  input: 'data/xx-captions.in',
  output: 'xx-captions',
  configuration: launcher_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

configure_file(
  input: 'data/io.piercingxx.Captions1.service.in',
  output: 'io.piercingxx.Captions1.service',
  configuration: launcher_conf,
  install: true,
  install_dir: join_paths(get_option('datadir'), 'dbus-1', 'services'),
)

# Settings window desktop entry (DBusActivatable=false: the settings
# and overlay own io.piercingxx.CaptionsSettings / io.piercingxx.Captions
# as plain Gtk.Application ids, launched via Exec, never bus-activated).
desktop_conf = configuration_data()
desktop_conf.set('bindir', bindir)

configure_file(
  input: 'data/io.piercingxx.CaptionsSettings.desktop.in',
  output: 'io.piercingxx.CaptionsSettings.desktop',
  configuration: desktop_conf,
  install: true,
  install_dir: join_paths(get_option('datadir'), 'applications'),
)

install_data(
  'data/io.piercingxx.CaptionsSettings.metainfo.xml',
  install_dir: join_paths(get_option('datadir'), 'metainfo'),
)

# App icon -> hicolor theme. The file is the XX logomark until the
# owner's xx-captions mark lands; swapping that one file picks up
# the real mark.
install_data(
  'data/icons/io.piercingxx.CaptionsSettings.svg',
  install_dir: join_paths(get_option('datadir'), 'icons', 'hicolor',
                          'scalable', 'apps'),
)
