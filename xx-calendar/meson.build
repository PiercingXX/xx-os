project(
  'xx-calendar',
  version: '0.1.0',
  meson_version: '>=0.60.0',
)

python = import('python').find_installation('python3')

# Shared design system: common/meson.build installs the xx_ui python
# package first, then this app imports it at runtime (never vendored).
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

app_id     = 'io.piercingxx.XXCalendar'
app_subdir = 'xx-calendar'
app_datadir = join_paths(datadir, app_subdir)

# The Python package installs to the datadir like XX-WM does: the launcher
# runs `python3 <datadir>/xx_calendar/main.py` with that dir on the path,
# so the checkout layout (src/xx_calendar/*.py) and the installed layout
# stay identical.
install_data(
  [
    'src/xx_calendar/__init__.py',
    'src/xx_calendar/__main__.py',
    'src/xx_calendar/main.py',
    'src/xx_calendar/month_grid.py',
    'src/xx_calendar/calendar.css',
    'src/xx_calendar/app_config.py',
    'src/xx_calendar/models.py',
    'src/xx_calendar/store.py',
    'src/xx_calendar/ics.py',
    'src/xx_calendar/recurrence.py',
    'src/xx_calendar/service.py',
    'src/xx_calendar/backup.py',
    'src/xx_calendar/eds_import.py',
    'src/xx_calendar/views.py',
  ],
  install_dir: join_paths(app_datadir, 'xx_calendar'),
)

# Launcher in bindir -> prefix/bin/xx-calendar
launcher_conf = configuration_data()
launcher_conf.set('python', python.full_path())
launcher_conf.set('app_dir', join_paths(prefix, app_datadir))

configure_file(
  input: 'data/xx-calendar.in',
  output: 'xx-calendar',
  configuration: launcher_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

# App .desktop entry -> prefix/share/applications/
desktop_conf = configuration_data()
desktop_conf.set('bindir', bindir)

configure_file(
  input: 'data/io.piercingxx.XXCalendar.desktop.in',
  output: 'io.piercingxx.XXCalendar.desktop',
  configuration: desktop_conf,
  install: true,
  install_dir: join_paths(datadir, 'applications'),
)

# AppStream metainfo -> prefix/share/metainfo/
install_data(
  'data/io.piercingxx.XXCalendar.metainfo.xml',
  install_dir: join_paths(datadir, 'metainfo'),
)

# App icon -> hicolor theme (Icon=io.piercingxx.XXCalendar in the desktop)
install_data(
  'data/icons/io.piercingxx.XXCalendar.svg',
  install_dir: join_paths(datadir, 'icons', 'hicolor', 'scalable', 'apps'),
)

# D-Bus activation for the GUI: the desktop entry is DBusActivatable,
# so the bus must know how to start it (only the Calendar store
# service existed before, and launchers failed silently). Name= must
# equal the desktop app id, never the store service name, or the GUI
# would exit as a secondary instance.
configure_file(
  input: 'data/io.piercingxx.XXCalendar.service.in',
  output: 'io.piercingxx.XXCalendar.service',
  configuration: desktop_conf,
  install: true,
  install_dir: join_paths(datadir, 'dbus-1', 'services'),
)

# Session activation file -> prefix/share/dbus-1/services/
# Lets the session bus start `xx-calendar --service` on first use and
# re-activate it after an idle exit. Name= must equal the owned name.
service_conf = configuration_data()
service_conf.set('bindir', bindir)

configure_file(
  input: 'data/io.piercingxx.Calendar.service.in',
  output: 'io.piercingxx.Calendar.service',
  configuration: service_conf,
  install: true,
  install_dir: join_paths(datadir, 'dbus-1', 'services'),
)
