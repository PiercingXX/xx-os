"""Primary layout contract: keyboard_layouts helper + session mapping.

The setup screen owns ``us`` / ``us+colemak`` / ``piercing-num``; the
hardware keymap must stay real xkb, so ``piercing-num`` maps to ``us``.
Tests never touch the network, accounts, or the real system: the session
snippet runs against a temp HOME with no gsettings calls.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / 'launcher' / 'src'
sys.path.insert(0, str(SRC))

SESSION = Path(__file__).parent.parent / 'launcher' / 'data' / 'xx-wm-session.in'


def test_choices_cover_the_three_setup_options():
    import keyboard_layouts as kl
    assert kl.CHOICES == (
        ('us', 'QWERTY'),
        ('us+colemak', 'Colemak'),
        ('piercing-num', 'Piercing Num'),
    )
    assert kl.DEFAULT_PRIMARY == 'us'


def test_xkb_for_maps_piercing_to_us():
    import keyboard_layouts as kl
    assert kl.xkb_for('piercing-num') == 'us'
    assert kl.xkb_for('us') == 'us'
    assert kl.xkb_for('us+colemak') == 'us+colemak'


def test_xkb_for_validates_and_falls_back():
    import keyboard_layouts as kl
    assert kl.xkb_for('  US  ') == 'us'
    assert kl.xkb_for('de') == 'de'
    assert kl.xkb_for('us+dvorak') == 'us+dvorak'
    assert kl.xkb_for('') == 'us'
    assert kl.xkb_for(None) == 'us'
    assert kl.xkb_for('garbage!!') == 'us'
    assert kl.xkb_for(42) == 'us'


def _session_snippet() -> str:
    text = SESSION.read_text(encoding='utf-8')
    start = text.index('_keyboard_layout=$(python3 -c "')
    end = text.index('" 2>/dev/null || echo', start)
    return text[start + len('_keyboard_layout=$(python3 -c "'):end]


def _run_snippet(snippet: str, home: Path) -> str:
    # The snippet reads $HOME literally; expand it to the temp dir.
    code = snippet.replace('$HOME', str(home))
    proc = subprocess.run(
        [sys.executable, '-c', code],
        capture_output=True, text=True, timeout=20,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.mark.parametrize(('config', 'expected'), [
    (None, 'us'),
    ({}, 'us'),
    ({'keyboard_layout': 'us'}, 'us'),
    ({'keyboard_layout': 'us+colemak'}, 'us+colemak'),
    ({'keyboard_layout': 'piercing-num'}, 'us'),
    ({'keyboard_layout': '  Piercing-Num '}, 'us'),
    ({'keyboard_layout': 'garbage!!'}, 'us'),
    ({'keyboard_layout': 42}, 'us'),
])
def test_session_mapping(tmp_path, config, expected):
    home = tmp_path / 'home'
    (home / '.config' / 'xx-wm').mkdir(parents=True)
    if config is not None:
        import json
        (home / '.config' / 'xx-wm' / 'config.json').write_text(
            json.dumps(config), encoding='utf-8')
    assert _run_snippet(_session_snippet(), home) == expected


def test_session_script_pins_real_xkb():
    text = SESSION.read_text(encoding='utf-8')
    assert 'keyboard_layout' in text
    assert "cfg.get('keyboard_layout', 'us')" in text
    assert "if layout == 'piercing-num':" in text
    assert re.search(r"layout = 'us'\nprint\(layout\)", text)
    assert 'us+colemak' not in text
