if not get_option('tests')
  subdir_done()
endif

test_env = environment()
test_env.set('G_DEBUG', 'gc-friendly,fatal-warnings')
test_env.set('MALLOC_CHECK_', '2')
test_env.set('NO_AT_BRIDGE', '1')
test_env.set('GSETTINGS_BACKEND', 'memory')
test_env.set('GSETTINGS_SCHEMA_DIR', meson.project_build_root() / 'data')

# For -Db_sanitize=address
test_env.set(
  'LSAN_OPTIONS',
  'suppressions=@0@/data/leak-suppress.txt'.format(meson.project_source_root()),
)
# as our libraries lack frame pointers since otherwise back traces are incomplete
test_env.set('ASAN_OPTIONS', 'fast_unwind_on_malloc=0')
# Useful when running locally and glib hits an assertion:
#test_env.set('ASAN_OPTIONS', 'fast_unwind_on_malloc=0:disable_coredump=0:unmap_shadow_on_exit=1:abort_on_error=1')

tests = [
  'completer',
  'completer-base',
  'completer-hunspell',
  'input-surface',
  'load-layouts',
  'osk-widget',
  'completers',
  'emoji-db',
  'size-manager',
]
foreach name : tests
  name = 'test-@0@'.format(name)
  test = executable(name, '@0@.c'.format(name), pie: true, dependencies: libpos_dep)
  test('@0@'.format(name), test, depends: compile_schemas, env: test_env)
endforeach

# xx-keyboard: theme-reader unit test. Headless by design (no Gtk init),
# so it passes in plain `meson test` with no display.
test(
  'test-xx-theme',
  executable('test-xx-theme', 'test-xx-theme.c', pie: true, dependencies: libpos_dep),
  depends: compile_schemas,
  env: test_env,
)

# xx-keyboard: older-phoc protocol policy (ext vs wlr data control,
# required globals). Pure helpers, no display needed.
test(
  'test-data-control-fallback',
  executable('test-data-control-fallback', 'test-data-control-fallback.c', pie: true, dependencies: libpos_dep),
  depends: compile_schemas,
  env: test_env,
)

# xx-keyboard: layout-file contract (valid JSON, Piercing letters exact).
xx_layouts_tester = find_program('test-xx-layouts.py')
test(
  'test-xx-layouts',
  xx_layouts_tester,
  args: ['--layouts=' + meson.current_source_dir() / '..' / 'src' / 'layouts'],
)

# xx-keyboard: SIGTERM lifecycle. Runs the real binary against a fake
# headless compositor plus a private bus, swipes once, then stops it
# with SIGTERM like systemd does. Needs no display; skips cleanly
# without dbus-daemon. No fatal-warnings: the fake compositor
# legitimately trips benign toolkit criticals (seat, resize).
lifecycle_env = environment()
lifecycle_env.set('NO_AT_BRIDGE', '1')
lifecycle_env.set('GSETTINGS_BACKEND', 'memory')
lifecycle_env.set(
  'LSAN_OPTIONS',
  'suppressions=@0@/data/leak-suppress.txt'.format(meson.project_source_root()),
)
lifecycle_env.set('ASAN_OPTIONS', 'fast_unwind_on_malloc=0')
lifecycle_env.set('MALLOC_CHECK_', '2')
test(
  'test-kb-lifecycle',
  find_program('test-kb-lifecycle.py'),
  args: [
    '--binary=' + meson.current_build_dir() / '..' / 'src' / 'xx-keyboard',
    '--schema-dir=' + meson.project_build_root() / 'data',
    '--dict-dir=' + meson.project_source_root() / 'glide' / 'dicts',
  ],
  depends: [compile_schemas, xx_keyboard_bin],
  env: lifecycle_env,
  timeout: 150,
)

# xx-keyboard: older-phoc compat. Same lifecycle against a fake
# compositor offering only zwlr data control (FuriOS FLX1) or none at
# all; startup must not bail with "Failed to find all Wayland globals".
foreach dc : ['wlr', 'none']
  test(
    'test-kb-lifecycle-@0@'.format(dc),
    find_program('test-kb-lifecycle.py'),
    args: [
      '--binary=' + meson.current_build_dir() / '..' / 'src' / 'xx-keyboard',
      '--schema-dir=' + meson.project_build_root() / 'data',
      '--dict-dir=' + meson.project_source_root() / 'glide' / 'dicts',
      '--data-control=' + dc,
    ],
    depends: [compile_schemas, xx_keyboard_bin],
    env: lifecycle_env,
    timeout: 150,
  )
endforeach

if presage_dep.found()
  # Create a dummy db for testing
  presage_test_db = custom_target(
    'presage-test-db',
    input: meson.current_source_dir() / 'data' / 'input.txt',
    output: 'database_en.db',
    command: ['mk-presage-db', '@INPUT@', '@OUTPUT@'],
  )

  name = 'test-completer-presage'
  test(
    name,
    executable(
      name,
      f'@name@.c',
      pie: true,
      c_args: '-DPOS_PRESAGE_TEST_DB_DIR="@0@"'.format(meson.current_build_dir()),
      dependencies: libpos_dep,
    ),
    depends: [compile_schemas, presage_test_db],
    env: test_env,
  )
endif

# Glide helpers: pure unit tests, no display needed. The decoder tests
# use the dictionaries from the source tree.
glide_test_dict = '"@0@"'.format(meson.project_source_root() / 'glide' / 'dicts' / 'main_en-US.dict')
test(
  'test-glide',
  executable(
    'test-glide',
    'test-glide.c',
    pie: true,
    c_args: '-DXXGLIDE_TEST_DICT=@0@'.format(glide_test_dict),
    dependencies: libpos_dep,
  ),
  depends: compile_schemas,
  env: test_env,
)

# Glide widget capture: needs a display (Broadway works), skips cleanly
# without one so `meson test` stays green headless.
test(
  'test-glide-widget',
  executable(
    'test-glide-widget',
    'test-glide-widget.c',
    pie: true,
    c_args: '-DXXGLIDE_TEST_DICT=@0@'.format(glide_test_dict),
    dependencies: libpos_dep,
  ),
  depends: compile_schemas,
  env: test_env,
)

if uim_dep.found()
  name = 'test-completer-uim'
  test(
    name,
    executable(name, f'@name@.c', pie: true, dependencies: libpos_dep),
    depends: [compile_schemas],
    env: test_env,
  )
endif
