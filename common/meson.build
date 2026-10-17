project(
  'xx-ui',
  version: '0.1.0',
  meson_version: '>=0.60.0',
)

python = import('python').find_installation('python3')

python.install_sources(
  'xx_ui/__init__.py',
  'xx_ui/theme.py',
  'xx_ui/css.py',
  'xx_ui/widgets.py',
  'xx_ui/style.css',
  subdir: 'xx_ui',
)

python.install_sources(
  'xx_phone/__init__.py',
  'xx_phone/schedules.py',
  'xx_phone/weather.py',
  subdir: 'xx_phone',
)
