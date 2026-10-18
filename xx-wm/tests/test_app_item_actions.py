"""Tests for app_item_actions.py mutation helpers — no widgets shown."""
from pathlib import Path
from types import SimpleNamespace

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import app_item_actions
from config import ShellConfig

gi = pytest.importorskip('gi')
gi.require_version('Gtk', '4.0')

from app_index import AppEntry
from app_item_actions import AppItemActions


def _entry(app_id: str, name: str) -> AppEntry:
    return AppEntry(app_id=app_id, name=name, description='', executable='',
                    search_text=name.casefold(), desktop_app=None)


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    cfg = ShellConfig()
    cfg.set_home_slots([
        {'type': 'app', 'label': 'Notes', 'app_id': 'notes.desktop'},
        {'type': 'folder', 'label': 'Tools', 'folder': [
            {'label': 'Calc', 'app_id': 'calc.desktop'},
            {'label': 'Camera', 'app_id': 'camera.desktop'},
        ]},
    ])
    return cfg


@pytest.fixture
def actions(config):
    events = []
    act = AppItemActions(config, on_changed=lambda: events.append('changed'),
                         on_status=lambda msg: events.append(msg))
    act.events = events
    return act


class TestPasswordDialog:
    def test_wifi_title_is_a_password_field(self):
        assert AppItemActions._title_is_password('Password for HomeNet') is True

    def test_rename_title_is_not_a_password_field(self):
        assert AppItemActions._title_is_password('Rename') is False

    def test_entry_dialog_source_raises_osk(self):
        src = Path(__file__).parent.parent.joinpath(
            'launcher', 'src', 'app_item_actions.py').read_text(encoding='utf-8')
        assert 'on_keyboard' in src
        assert 'InputPurpose.PASSWORD' in src
        assert 'self._on_keyboard(True)' in src


class TestRenameInSlots:
    def test_updates_app_slot_and_folder_member(self, actions, config):
        actions._rename_in_slots('calc.desktop', 'Sums')
        members = config.home_slots[1]['folder']
        assert members[0]['label'] == 'Sums'

        actions._rename_in_slots('notes.desktop', 'Journal')
        assert config.home_slots[0]['label'] == 'Journal'

    def test_no_write_when_id_absent(self, actions, config):
        before = config.home_slots
        actions._rename_in_slots('missing.desktop', 'Nope')
        assert config.home_slots == before


class TestFolderMembership:
    def test_add_member(self, actions, config):
        actions._add_member(1, _entry('photos.desktop', 'Photos'), 'Photos')
        members = config.home_slots[1]['folder']
        assert members[-1] == {'label': 'Photos', 'app_id': 'photos.desktop'}
        assert 'changed' in actions.events

    def test_add_duplicate_is_noop(self, actions, config):
        actions._add_member(1, _entry('calc.desktop', 'Calc'), 'Calc')
        assert len(config.home_slots[1]['folder']) == 2
        assert 'changed' not in actions.events

    def test_move_member_down_and_clamp(self, actions, config):
        actions._move_member(1, 0, +1)
        members = config.home_slots[1]['folder']
        assert [m['label'] for m in members] == ['Camera', 'Calc']

        actions.events.clear()
        actions._move_member(1, 1, +1)
        assert [m['label'] for m in config.home_slots[1]['folder']] == ['Camera', 'Calc']
        assert 'changed' not in actions.events


class TestPinned:
    def test_pin_unpin_and_reorder(self, actions, config):
        a, b = _entry('a.desktop', 'A'), _entry('b.desktop', 'B')
        actions._set_pinned(a, True)
        actions._set_pinned(b, True)
        assert config.pinned == ['a.desktop', 'b.desktop']

        actions._move_pinned(b, -1)
        assert config.pinned == ['b.desktop', 'a.desktop']

        actions._set_pinned(a, False)
        assert config.pinned == ['b.desktop']


class TestHidden:
    def test_hide_and_show(self, actions, config):
        e = _entry('x.desktop', 'X')
        actions._set_hidden(e, True)
        assert 'x.desktop' in config.hidden_apps
        actions._set_hidden(e, False)
        assert 'x.desktop' not in config.hidden_apps


class TestMuteNormalization:
    def test_menu_written_mute_matches_desktop_entry_hint(self, config):
        config.set_app_muted('org.gnome.Calculator.desktop', 9e12)
        assert config.is_app_muted('org.gnome.Calculator')
        assert config.is_app_muted('org.gnome.Calculator.desktop')
        assert not config.is_app_muted('org.gnome.Clocks')


class _FakeLabel:
    def __init__(self, label='', xalign=0, wrap=False):
        self.text = label
        self.calls = {}

    def add_css_class(self, _name):
        pass

    def set_xalign(self, value):
        self.calls['xalign'] = value

    def set_wrap(self, value):
        self.calls['wrap'] = value

    def set_wrap_mode(self, value):
        self.calls['wrap_mode'] = value

    def set_max_width_chars(self, value):
        self.calls['max_width_chars'] = value

    def set_ellipsize(self, value):
        self.calls['ellipsize'] = value


class _FakeButton:
    def __init__(self, label=''):
        self.label = label
        self.child = _FakeLabel(label)
        self.handlers = {}
        self.calls = {}

    def add_css_class(self, name):
        self.calls.setdefault('css_classes', []).append(name)

    def get_child(self):
        return self.child

    def connect(self, signal, callback):
        self.handlers[signal] = callback


class _FakeBox:
    def __init__(self, orientation=None, spacing=0):
        self.children = []
        self.calls = {}

    def add_css_class(self, name):
        self.calls.setdefault('css_classes', []).append(name)

    def append(self, widget):
        self.children.append(widget)


class _FakeEntry:
    def __init__(self):
        self.text = ''
        self.calls = {}
        self.handlers = {}

    def add_css_class(self, name):
        self.calls.setdefault('css_classes', []).append(name)

    def set_text(self, value):
        self.text = value

    def get_text(self):
        return self.text

    def select_region(self, start, end):
        self.calls['select_region'] = (start, end)

    def set_visibility(self, value):
        self.calls['visibility'] = value

    def set_input_purpose(self, value):
        self.calls['input_purpose'] = value

    def set_max_width_chars(self, value):
        self.calls['max_width_chars'] = value

    def connect(self, signal, callback):
        self.handlers[signal] = callback

    def grab_focus(self):
        self.calls['grab_focus'] = True


class _FakePopover:
    def __init__(self):
        self.popped_down = False

    def popdown(self):
        self.popped_down = True


@pytest.fixture
def faked_toolkit(monkeypatch):
    """Recorder Gtk/Pango: constructing real widgets segfaults headless."""
    gtk = SimpleNamespace(
        Box=_FakeBox, Label=_FakeLabel, Button=_FakeButton,
        Entry=_FakeEntry,
        Orientation=SimpleNamespace(HORIZONTAL=0, VERTICAL=1),
        InputPurpose=SimpleNamespace(PASSWORD='password'),
    )
    pango = SimpleNamespace(
        WrapMode=SimpleNamespace(WORD='word', WORD_CHAR='word-char'),
        EllipsizeMode=SimpleNamespace(END='end'),
    )
    monkeypatch.setattr(app_item_actions, 'Gtk', gtk)
    monkeypatch.setattr(app_item_actions, 'Pango', pango)
    return gtk, pango


def _show_dialog(actions, password, entered):
    captured = {}
    popover = _FakePopover()

    def _capture(_anchor, content):
        captured['box'] = content
        return popover

    actions._show_popover = _capture
    committed = []
    actions._show_entry_dialog(object(), 'Password for HomeNet', '', 'Connect',
                               committed.append, password=password)
    box = captured['box']
    entry = next(c for c in box.children if isinstance(c, _FakeEntry))
    entry.text = entered
    return box, entry, popover, committed


class TestPasswordEntryDialog:
    def test_password_mode_hides_and_preserves_spaces(
            self, actions, faked_toolkit):
        _gtk, _pango = faked_toolkit
        _box, entry, popover, committed = _show_dialog(
            actions, True, '  p@ss w0rd  ')
        assert entry.calls['visibility'] is False
        assert entry.calls['input_purpose'] == 'password'
        assert 'select_region' not in entry.calls
        entry.handlers['activate']()
        assert popover.popped_down
        assert committed == ['  p@ss w0rd  ']

    def test_default_mode_strips_and_selects(self, actions, faked_toolkit):
        _box, entry, popover, committed = _show_dialog(actions, False, '  Calc  ')
        assert 'visibility' not in entry.calls
        assert 'input_purpose' not in entry.calls
        assert entry.calls['select_region'] == (0, -1)
        entry.handlers['activate']()
        assert committed == ['Calc']

    def test_password_mode_uses_password_purpose_constant(
            self, actions, faked_toolkit):
        _gtk, _pango = faked_toolkit
        _box, entry, _popover, _committed = _show_dialog(
            actions, True, 'x')
        assert entry.calls['input_purpose'] == _gtk.InputPurpose.PASSWORD


class TestPopoverWidthCaps:
    def test_action_items_wrap_word_char_at_28(
            self, actions, faked_toolkit):
        _gtk, pango = faked_toolkit
        box = actions._action_list(
            [('android.com.google.android.apps.youtube.music', lambda: None)],
            title='A very long menu title that exceeds the cap width')
        heading, btn = box.children
        assert heading.calls['ellipsize'] == pango.EllipsizeMode.END
        assert heading.calls['max_width_chars'] == 28
        child = btn.child
        assert child.calls['xalign'] == 0
        assert child.calls['wrap'] is True
        assert child.calls['wrap_mode'] == pango.WrapMode.WORD_CHAR
        assert child.calls['max_width_chars'] == 28

    def test_entry_dialog_caps(self, actions, faked_toolkit):
        _gtk, pango = faked_toolkit
        box, entry, _popover, _committed = _show_dialog(actions, False, 'x')
        heading = box.children[0]
        assert heading.calls['ellipsize'] == pango.EllipsizeMode.END
        assert heading.calls['max_width_chars'] == 28
        assert entry.calls['max_width_chars'] == 28


class TestSharedDialogStyles:
    def test_menu_rows_carry_menu_row_class(self, actions, faked_toolkit):
        box = actions._action_list([('Rename', lambda: None)], title='Camera')
        btn = box.children[-1]
        assert 'xx-menu-row' in btn.calls['css_classes']

    def test_entry_dialog_uses_shared_entry_and_primary_classes(
            self, actions, faked_toolkit):
        box, entry, _popover, _committed = _show_dialog(actions, False, 'x')
        assert 'xx-dialog' in box.calls['css_classes']
        assert 'xx-entry' in entry.calls['css_classes']
        commit = box.children[-1]
        assert 'xx-btn-primary' in commit.calls['css_classes']


class _Sub:
    def __init__(self, text: str) -> None:
        self._text = text

    def get_text(self) -> str:
        return self._text


class TestCurrentChoiceIndex:
    """A value row's menu marks the value its subtitle shows."""

    _CHOICES = [('App switcher', None), ('Home', None), ('None', None)]

    def test_subtitle_value_is_checked(self):
        row = SimpleNamespace(subtitle_label=_Sub('Home'))
        assert app_item_actions.current_choice_index(row, self._CHOICES) == 1

    def test_action_menus_stay_unchecked(self):
        # A drawer row (no subtitle) or a subtitle no choice names.
        assert app_item_actions.current_choice_index(
            object(), self._CHOICES) is None
        row = SimpleNamespace(subtitle_label=_Sub('On, starts at boot'))
        assert app_item_actions.current_choice_index(
            row, self._CHOICES) is None
        empty = SimpleNamespace(subtitle_label=_Sub(''))
        assert app_item_actions.current_choice_index(
            empty, self._CHOICES) is None
