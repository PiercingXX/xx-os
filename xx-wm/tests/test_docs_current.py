"""The public docs describe the shell as it is.

The September/October build-out notes (docs/buildout.md,
docs/build-spec.md) are history, kept in git rather than the tree. The
launcher README names the compositors and keyboard the shell runs with,
and docs/config.md matches the code for the keys it documents.
"""
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def test_build_out_notes_left_the_tree():
    assert not (ROOT / 'docs' / 'buildout.md').exists()
    assert not (ROOT / 'docs' / 'build-spec.md').exists()


def test_launcher_readme_names_the_real_stack():
    text = _read('launcher/README.md')
    assert 'river' in text and 'phoc' in text
    assert 'xx-keyboard' in text
    assert 'starts squeekboard before the shell' not in text
    assert 'Workstream 17' not in text
    assert 'dr3k' not in text
    assert 'Hyprland when the gestures catch up' not in _read('README.md')


def test_config_md_keyboard_layouts_match_settings():
    import sys
    sys.path.insert(0, str(ROOT / 'launcher' / 'src'))
    from settings_keyboard import KEYBOARD_PRESETS, format_layout_key
    row = next(line for line in _read('docs/config.md').splitlines()
               if line.startswith('| `keyboard_layout`'))
    for _label, layout, variant in KEYBOARD_PRESETS:
        key = format_layout_key(layout, variant)
        assert '`%s`' % key in row, key


def test_config_md_medical_fields_match_the_sanitizer():
    import sys
    sys.path.insert(0, str(ROOT / 'launcher' / 'src'))
    import config
    row = next(line for line in _read('docs/config.md').splitlines()
               if line.startswith('| `medical_id`'))
    for key in config._MEDICAL_ID_STRING_KEYS:
        assert key in row, key
    assert 'Settings → Medical ID' in row
