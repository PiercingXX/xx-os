# Glide (swipe) decoder static library on the vendored LatinIME sources.
# No GTK/glib here; plain C API in xxglide.h. Pulled into the Stevia build
# with subdir('glide'), guarded by the 'glide' option.

# The rest of Stevia is C; the decoder needs C++ only when glide is on.
add_languages('cpp', required : true)

# include/jni.h is AOSP's VM-neutral JNI header (Apache-2.0, from the NDK
# sysroot): the decoder only needs the JNI types, never a JDK.
xxglide_inc = include_directories('latinime', 'include')

# Same selection as tools/glide-replay's Makefile: every .cpp under latinime/.
latinime_srcs = run_command(
  'find', meson.current_source_dir() / 'latinime', '-name', '*.cpp',
  check : true,
).stdout().strip().split('\n')

xxglide_lib = static_library(
  'xxglide',
  'xxglide.cpp',
  latinime_srcs,
  include_directories : xxglide_inc,
  c_args : '-DHOST_TOOL',
  cpp_args : ['-DHOST_TOOL', '-Wno-unused-parameter', '-Wno-unused-but-set-variable'],
  override_options : 'cpp_std=c++17',
  install : false,
)

xxglide_dep = declare_dependency(
  link_with : xxglide_lib,
  include_directories : include_directories('.'),
)

# The UI lane finds the dictionary at <datadir>/xx-keyboard/dicts/.
install_data(
  'dicts/main_en-US.dict',
  'dicts/main_en-GB.dict',
  install_dir : get_option('datadir') / 'xx-keyboard' / 'dicts',
)

if get_option('tests')
  glide_test_defs = [
    '-DXXGLIDE_TEST_DICT="' + meson.current_source_dir() / 'dicts/main_en-US.dict"',
    '-DXXGLIDE_TEST_CORPUS="' + meson.current_source_dir() / 'test/corpus.jsonl"',
    '-DXXGLIDE_TEST_WORDS="' + meson.current_source_dir() / 'test/words.txt"',
  ]
  # Corpus is 19 real tester glides plus 150 words x QWERTY/Colemak
  # synthetic swipes from gen_corpus.py (widget-real key boxes, humanlike
  # speed/noise). Reference rate is top-1 229/319, top-4 299/319; the
  # misses are near-misses at rank 2-4, also missed by the reference
  # decoder. The floor pins regressions, not the exact rate.
  glide_corpus_defs = glide_test_defs + [
    '-DXXGLIDE_CORPUS_TOP1_FLOOR=225',
    '-DXXGLIDE_CORPUS_TOP4_FLOOR=295',
  ]
  glide_decode_test = executable(
    'test-glide-decode',
    'test/test_glide_decode.cpp',
    dependencies : xxglide_dep,
    include_directories : xxglide_inc,
    c_args : glide_test_defs,
    cpp_args : glide_test_defs + ['-DHOST_TOOL'],
    override_options : 'cpp_std=c++17',
  )
  test('test-glide-decode', glide_decode_test, timeout : 300)
  glide_corpus_test = executable(
    'test-glide-corpus',
    'test/test_glide_corpus.cpp',
    dependencies : xxglide_dep,
    include_directories : xxglide_inc,
    c_args : glide_corpus_defs,
    cpp_args : glide_corpus_defs + ['-DHOST_TOOL'],
    override_options : 'cpp_std=c++17',
  )
  test('test-glide-corpus', glide_corpus_test, timeout : 300)
  # The corpus files are generated output: fail if a hand edit drifts
  # from what gen_corpus.py (seeded, no network) would produce.
  test(
    'test-glide-corpus-sync',
    find_program('python3'),
    args : [meson.current_source_dir() / 'test' / 'gen_corpus.py', '--check'],
    timeout : 120,
  )
endif
