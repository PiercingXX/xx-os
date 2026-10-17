"""First-boot Keyboard step + Settings › Keyboard alignment.

The step sits before the PIN, defaults to QWERTY, writes
`keyboard_layout` and applies the hardware keymap through
settings_keyboard.apply_keyboard_layout (mocked here: no real
gsettings). Headless: wizard methods run against a namespace self, as in
test_first_boot_pin."""
import json
import re
import sys
import types
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / 'launcher' / 'src'
LAYOUTS = Path(__file__).parent.parent.parent / 'xx-keyboard' / 'src' / 'layouts'
sys.path.insert(0, str(SRC))


def _kb():
    # settings_page imports settings_keyboard at its foot; importing the
    # page first (as the shell does) keeps that cycle resolved.
    import settings_page  # noqa: F401
    import settings_keyboard
    return settings_keyboard


def _first_boot():
    # Imported lazily: sibling test modules stub sys.modules['gi'] during
    # collection; conftest restores the real bindings before tests run.
    import first_boot
    return first_boot


class _FakeCard:
    def __init__(self):
        self.css: set[str] = set()

    def add_css_class(self, name):
        self.css.add(name)

    def remove_css_class(self, name):
        self.css.discard(name)


class _FakeStack:
    def __init__(self):
        self.visible_child_name = None

    def set_visible_child_name(self, name):
        self.visible_child_name = name


class _FakeConfig:
    def __init__(self):
        self.writes: list[tuple[str, object]] = []
        self.data: dict = {}

    def set(self, key, value):
        self.writes.append((key, value))


@pytest.fixture
def applied(monkeypatch):
    """Record apply_keyboard_layout calls; any real gsettings spawn fails."""
    import subprocess

    settings_keyboard = _kb()
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(settings_keyboard, 'apply_keyboard_layout',
                        lambda layout, variant: calls.append((layout, variant)))

    def _no_spawn(*_a, **_k):
        raise AssertionError('real subprocess spawned in a unit test')
    monkeypatch.setattr(subprocess, 'Popen', _no_spawn)
    return calls


def _wizard():
    fb = _first_boot()
    cls = fb.FirstBootWizard
    wiz = types.SimpleNamespace(
        _keyboard_choice=fb._DEFAULT_KEYBOARD,
        _keyboard_applied=None,
        _keyboard_cards={key: _FakeCard() for key, _name in fb.KEYBOARD_CHOICES},
        _config=_FakeConfig(),
        _stack=_FakeStack(),
    )
    for name in ('_mark_keyboard_card', '_on_keyboard_card',
                 '_on_keyboard_next', '_apply_keyboard'):
        setattr(wiz, name, types.MethodType(getattr(cls, name), wiz))
    return wiz


# ------------------------------------------------------------ placement


class TestStepOrder:
    def _init_source(self):
        src = (SRC / 'first_boot.py').read_text(encoding='utf-8')
        return src.split('def __init__')[1].split('\n    def ')[0]

    def test_keyboard_step_is_added_before_pin(self):
        body = self._init_source()
        names = re.findall(r"add_named\(self\._build_\w+\(\), '(\w+)'\)", body)
        assert 'keyboard' in names
        assert names.index('keyboard') < names.index('pin')
        assert names.index('welcome') < names.index('keyboard')

    def test_welcome_leads_to_keyboard(self):
        src = (SRC / 'first_boot.py').read_text(encoding='utf-8')
        welcome = src.split('def _build_welcome')[1].split('\n    def ')[0]
        assert "set_visible_child_name('keyboard')" in welcome
        assert "set_visible_child_name('pin')" not in welcome

    def test_next_moves_on_to_pin(self, applied):
        wiz = _wizard()
        wiz._on_keyboard_next(None)
        assert wiz._stack.visible_child_name == 'pin'


# ------------------------------------------------------------ default


class TestDefault:
    def test_qwerty_is_the_default_choice(self):
        fb = _first_boot()
        assert fb._DEFAULT_KEYBOARD == 'us'
        assert fb.KEYBOARD_CHOICES[0] == ('us', 'QWERTY')

    def test_untouched_default_is_written_on_next(self, applied):
        wiz = _wizard()
        wiz._mark_keyboard_card(wiz._keyboard_choice)
        assert wiz._keyboard_cards['us'].css == {'selected'}
        wiz._on_keyboard_next(None)
        assert wiz._config.writes == [('keyboard_layout', 'us')]
        assert applied == [('us', '')]

    def test_settings_default_is_qwerty(self):
        kb = _kb()
        assert kb.DEFAULT_KEY == 'us'
        assert kb.parse_layout_key('???') == ('us', '')


# ------------------------------------------------------------ selection


class TestSelection:
    def test_choice_writes_config_and_applies(self, applied):
        wiz = _wizard()
        wiz._on_keyboard_card('us+colemak')
        assert wiz._config.writes == [('keyboard_layout', 'us+colemak')]
        assert applied == [('us', 'colemak')]
        assert wiz._keyboard_cards['us+colemak'].css == {'selected'}
        assert wiz._keyboard_cards['us'].css == set()

    def test_piercing_num_writes_its_key_and_applies_us(self, applied):
        wiz = _wizard()
        wiz._on_keyboard_card('piercing-num')
        assert wiz._config.writes == [('keyboard_layout', 'piercing-num')]
        assert applied == [('us', '')]

    def test_repeat_choice_and_next_do_not_duplicate(self, applied):
        wiz = _wizard()
        wiz._on_keyboard_card('piercing-num')
        wiz._on_keyboard_card('piercing-num')
        wiz._on_keyboard_next(None)
        assert len(wiz._config.writes) == 1
        assert len(applied) == 1

    def test_changing_back_applies_again(self, applied):
        wiz = _wizard()
        wiz._on_keyboard_card('us+colemak')
        wiz._on_keyboard_card('us')
        assert [value for _key, value in wiz._config.writes] == [
            'us+colemak', 'us']
        assert applied == [('us', 'colemak'), ('us', '')]


# ------------------------------------------------------------ previews


@pytest.mark.parametrize('key', ['us', 'us+colemak', 'piercing-num'])
def test_preview_row_is_the_real_xx_keyboard_top_row(key):
    if not LAYOUTS.is_dir():
        pytest.skip('xx-keyboard sources not in this checkout')
    data = json.loads((LAYOUTS / f'{key}.json').read_text(encoding='utf-8'))
    row = data['levels'][0]['rows'][0]
    top = ''.join((k[0] if isinstance(k, list) else k) for k in row).upper()
    assert _first_boot()._KEYBOARD_TOP_ROWS[key] == top


def test_wizard_sheet_styles_cards_from_the_preset():
    import config
    fb = _first_boot()
    paper = config.THEME_PRESETS['paper']
    css = fb.theme_css(paper)
    assert '.kb-card.selected' in css
    assert '.kb-key' in css
    selected = css.split('.kb-card.selected:hover {')[1].split('}')[0]
    assert paper.foreground in selected


# ------------------------------------------------------------ xkb mapping


class TestXkbMapping:
    def test_piercing_num_maps_to_us(self):
        import keyboard_layouts
        assert keyboard_layouts.xkb_for('piercing-num') == 'us'

    @pytest.mark.parametrize('key', ['us', 'us+colemak', 'de', 'us+dvorak'])
    def test_xkb_layouts_pass_through(self, key):
        import keyboard_layouts
        assert keyboard_layouts.xkb_for(key) == key

    def test_choices_are_the_three_defaults(self):
        import keyboard_layouts
        assert keyboard_layouts.CHOICES == (
            ('us', 'QWERTY'), ('us+colemak', 'Colemak'),
            ('piercing-num', 'Piercing Num'))

    def test_settings_apply_never_hands_xkb_piercing_num(self, monkeypatch):
        import subprocess

        kb = _kb()
        spawned = []
        monkeypatch.setattr(subprocess, 'Popen',
                            lambda argv, **_k: spawned.append(argv))
        kb.apply_keyboard_layout('piercing-num', '')
        assert [argv[-1] for argv in spawned] == ["[('xkb', 'us')]"] * 2


# ------------------------------------------------------------ settings


class TestSettingsPicker:
    def test_presets_lead_with_the_three_choices(self):
        import keyboard_layouts
        kb = _kb()
        keys = [kb.format_layout_key(layout, variant)
                for _label, layout, variant in kb.KEYBOARD_PRESETS]
        assert keys[:3] == [key for key, _name in keyboard_layouts.CHOICES]
        assert len(keys) == len(set(keys))

    def test_parse_accepts_piercing_num(self):
        kb = _kb()
        assert kb.parse_layout_key('piercing-num') == ('piercing-num', '')
        assert kb.parse_layout_key(' Piercing-Num ') == ('piercing-num', '')
        assert kb.current_label('piercing-num') == 'Piercing Num'

    def _section(self, monkeypatch):
        kb = _kb()
        subtitles = []
        monkeypatch.setattr(kb.ui, 'set_subtitle',
                            lambda _row, text: subtitles.append(text))
        monkeypatch.setattr(kb.ui, 'say', lambda _status, _text: None)
        menus = []
        win = types.SimpleNamespace(
            config=types.SimpleNamespace(data={}),
            item_actions=types.SimpleNamespace(
                _show_action_menu=lambda anchor, title, actions,
                subtitle=None: menus.append((title, actions, subtitle))))
        win.config.set = lambda key, value: win.config.data.update(
            {key: value})
        section = kb.KeyboardSection()
        section._row = object()
        return kb, section, win, subtitles, menus

    def test_menu_offers_all_three_with_osk_only_note(self, monkeypatch):
        kb, section, win, _subtitles, menus = self._section(monkeypatch)
        section._show_presets(win, section._row)
        (title, actions, subtitle), = menus
        labels = [label for label, _cb in actions]
        assert labels[:3] == ['QWERTY', 'US Colemak', 'Piercing Num']
        assert 'on-screen only' in subtitle

    def test_picking_piercing_num_writes_key_applies_us(self, monkeypatch,
                                                        applied):
        kb, section, win, subtitles, menus = self._section(monkeypatch)
        section._show_presets(win, section._row)
        (_title, actions, _subtitle), = menus
        dict(actions)['Piercing Num']()
        assert win.config.data['keyboard_layout'] == 'piercing-num'
        assert applied == [('us', '')]
        assert subtitles[-1] == 'Piercing Num · on-screen only'

    def test_unset_key_shows_qwerty(self, monkeypatch):
        kb, section, win, subtitles, _menus = self._section(monkeypatch)
        section._render(win)
        assert subtitles == ['QWERTY']


def test_setup_pending_marker(tmp_path):
    # A reboot between the Keyboard step and Finish must rerun the wizard
    # (or the PIN would never be set); configs older than the marker are done.
    import json
    from first_boot import setup_pending
    cfg = tmp_path / 'config.json'
    assert setup_pending(cfg) is True
    cfg.write_text(json.dumps({'keyboard_layout': 'us', 'setup_complete': False}))
    assert setup_pending(cfg) is True
    cfg.write_text(json.dumps({'keyboard_layout': 'us', 'setup_complete': True}))
    assert setup_pending(cfg) is False
    cfg.write_text(json.dumps({'theme': 'amoled'}))
    assert setup_pending(cfg) is False


def test_keyboard_step_marks_setup_unfinished():
    wiz = _wizard()
    wiz._apply_keyboard('us')
    assert wiz._config.data['setup_complete'] is False
