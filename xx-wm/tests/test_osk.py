"""Every Gtk.Entry / SearchEntry the shell draws is OSK-attached or exempt.

PIN keypads are buttons, not entries. A new Entry without osk.attach /
_attach_osk / on_keyboard / EventControllerFocus in the following ~35
lines is a bug (3b.2).
"""
import re
from pathlib import Path

SRC = Path(__file__).parent.parent / 'launcher' / 'src'
ENTRY_RE = re.compile(r'\bGtk\.(Search)?Entry\s*\(')
MARKERS = (
    'osk.attach',
    '_attach_osk',
    'EventControllerFocus',
    'on_keyboard',
    'attach(',
)


def test_every_gtk_entry_is_attached_or_exempt():
    found: list[str] = []
    missing: list[str] = []
    for path in sorted(SRC.glob('*.py')):
        lines = path.read_text(encoding='utf-8').splitlines()
        for i, line in enumerate(lines):
            if not ENTRY_RE.search(line) or line.lstrip().startswith('#'):
                continue
            window = '\n'.join(lines[i:min(len(lines), i + 35)])
            loc = f'{path.name}:{i + 1}'
            found.append(loc)
            if not any(marker in window for marker in MARKERS):
                missing.append(f'{loc}: {line.strip()}')
    assert found, 'expected Gtk.Entry / SearchEntry construction sites'
    assert not missing, 'unattached entries:\n' + '\n'.join(missing)


def test_helper_owns_setvisible_and_attach():
    text = (SRC / 'osk.py').read_text(encoding='utf-8')
    assert 'def set_visible' in text
    assert 'def set_layer_keyboard' in text
    assert 'def attach' in text
    assert 'bus.call_sync' not in text
    assert 'bus.call(' in text
    assert 'KeyboardMode.EXCLUSIVE' in text
    assert 'KeyboardMode.NONE' in text


def test_wizard_and_sms_and_dialogs_use_the_helper():
    first_boot = (SRC / 'first_boot.py').read_text(encoding='utf-8')
    sms = (SRC / 'sms.py').read_text(encoding='utf-8')
    actions = (SRC / 'app_item_actions.py').read_text(encoding='utf-8')
    window = (SRC / 'window.py').read_text(encoding='utf-8')
    assert 'from osk import attach' in first_boot
    assert 'KeyboardMode.NONE' in first_boot
    assert 'from osk import attach' in sms
    assert 'from osk import attach' in actions
    assert 'from osk import attach' in window
    assert 'def _attach_osk' in window
