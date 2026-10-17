"""Lane lp_home: home slots, gestures and back behaviour.

Tests-first contract for the eight items: slot change-app menu + replace,
Add to Home menus, double-tap/long-press dispatch, home_to_recents, back
order, empty-folder status, rename triad, uninstall purge. Bare
SimpleNamespace doubles keep everything headless; widget construction is
never needed.
"""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

SRC = Path(__file__).parent.parent / 'launcher' / 'src'

gi = pytest.importorskip('gi')
gi.require_version('Gtk', '4.0')

from app_index import AppEntry  # noqa: E402
from app_item_actions import AppItemActions  # noqa: E402
from config import ShellConfig  # noqa: E402
import gesture_config  # noqa: E402
from gesture_config import GestureConfig  # noqa: E402
from home_page import HomePage  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_modules():
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        sys.modules.pop(name, None)


@pytest.fixture
def home_dir(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    return tmp_path / 'home'


def _entry(app_id='notes.desktop', name='Notes'):
    return AppEntry(app_id=app_id, name=name, description='',
                    executable='', search_text=name.casefold(),
                    desktop_app=None)


def _home_win(config, **over):
    """Bare HomePage window double: real config, stubbed surfaces."""
    statuses = []
    win = types.SimpleNamespace(
        config=config,
        gesture_config=types.SimpleNamespace(get=lambda _k: 'none'),
        stack=types.SimpleNamespace(
            set_visible_child_name=lambda _n: None),
        apps_search=types.SimpleNamespace(get_text=lambda: ''),
        _home_launcher=types.SimpleNamespace(refresh=lambda *a, **k: None),
        _pick_slot_mode=False,
        _pick_slot_target=None,
        _populate_apps=lambda _q='': None,
        _refresh_after_item_action=lambda: None,
        _show_status=lambda msg: statuses.append(msg),
        _dispatch_gesture_action=lambda _a: None,
        item_actions=types.SimpleNamespace(
            _show_action_menu=lambda *a, **k: None,
            _show_entry_dialog=lambda *a, **k: None,
            _rename_in_slots=lambda _a, _l: None,
        ),
    )
    win.statuses = statuses
    for key, value in over.items():
        setattr(win, key, value)
    return win


# -- item 3b: long_press_home validation --------------------------------------

class TestLongPressHomeAction:
    @pytest.fixture(autouse=True)
    def _isolate_gestures(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gesture_config, '_config_path',
                            lambda: tmp_path / 'gestures.json')

    def test_edit_is_valid_for_long_press_home(self):
        assert gesture_config._is_valid_value('long_press_home', 'edit')

    def test_long_press_home_defaults_to_edit(self):
        assert GestureConfig().get('long_press_home') == 'edit'

    def test_edit_persists(self):
        gc = GestureConfig()
        gc.set('long_press_home', 'edit')
        assert GestureConfig().get('long_press_home') == 'edit'

    def test_double_tap_home_defaults_off_like_android(self):
        # Android "Double Tap to Lock" defaults off.
        assert GestureConfig().get('double_tap_home') == 'none'


# -- item 3: home background tap / long-press dispatch -------------------------

class _Claim:
    def __init__(self):
        self.state = None

    def set_state(self, state):
        self.state = state


class TestHomeBackgroundGestures:
    def test_double_tap_dispatches_configured_action(self, home_dir):
        config = ShellConfig()
        seen = []
        win = _home_win(
            config,
            gesture_config=types.SimpleNamespace(
                get=lambda k: 'lock_screen' if k == 'double_tap_home' else 'none'),
            _dispatch_gesture_action=lambda a: seen.append(a),
        )
        HomePage(win)._on_home_background_tap(2)
        assert seen == ['lock_screen']

    def test_single_tap_does_nothing(self, home_dir):
        config = ShellConfig()
        seen = []
        win = _home_win(config,
                        _dispatch_gesture_action=lambda a: seen.append(a))
        HomePage(win)._on_home_background_tap(1)
        assert seen == []

    def test_long_press_edit_enters_edit_mode(self, home_dir):
        config = ShellConfig()
        edited = []
        win = _home_win(
            config,
            gesture_config=types.SimpleNamespace(
                get=lambda k: 'edit' if k == 'long_press_home' else 'none'),
        )
        win._home_launcher = types.SimpleNamespace(
            edit_mode=False,
            set_edit_mode=lambda enabled: edited.append(enabled))
        HomePage(win)._on_home_long_press(_Claim(), 0.0, 0.0)
        assert edited == [True]

    def test_long_press_edit_skipped_when_already_editing(self, home_dir):
        config = ShellConfig()
        edited = []
        win = _home_win(
            config,
            gesture_config=types.SimpleNamespace(
                get=lambda k: 'edit' if k == 'long_press_home' else 'none'),
        )
        win._home_launcher = types.SimpleNamespace(
            edit_mode=True,
            set_edit_mode=lambda enabled: edited.append(enabled))
        HomePage(win)._on_home_long_press(_Claim(), 0.0, 0.0)
        assert edited == []

    def test_long_press_settings_opens_settings(self, home_dir):
        config = ShellConfig()
        seen = []
        win = _home_win(
            config,
            gesture_config=types.SimpleNamespace(
                get=lambda k: 'settings' if k == 'long_press_home' else 'none'),
            _dispatch_gesture_action=lambda a: seen.append(a),
        )
        HomePage(win)._on_home_long_press(_Claim(), 0.0, 0.0)
        assert seen == ['settings']

    def test_long_press_none_does_nothing(self, home_dir):
        config = ShellConfig()
        seen = []
        edited = []
        win = _home_win(
            config,
            gesture_config=types.SimpleNamespace(get=lambda _k: 'none'),
            _dispatch_gesture_action=lambda a: seen.append(a),
        )
        win._home_launcher = types.SimpleNamespace(
            edit_mode=False,
            set_edit_mode=lambda enabled: edited.append(enabled))
        HomePage(win)._on_home_long_press(_Claim(), 0.0, 0.0)
        assert seen == [] and edited == []

    def test_home_panes_wire_background_tap(self):
        import inspect
        import home_page
        src = inspect.getsource(home_page.HomePage._build_home_page)
        assert '_on_home_background_tap' in src
        assert src.count('_on_home_background_tap') >= 2


# -- owned config key: home_to_recents -----------------------------------------

class TestHomeToRecentsKey:
    def test_default_is_false(self, home_dir):
        assert ShellConfig().home_to_recents is False

    def test_setter_round_trips(self, home_dir):
        config = ShellConfig()
        config.set_home_to_recents(True)
        assert ShellConfig().home_to_recents is True
        config.set_home_to_recents(False)
        assert ShellConfig().home_to_recents is False

    def test_backup_round_trip(self, home_dir):
        from backup import export_backup, restore_backup, validate_backup
        config = ShellConfig()
        config.set_home_to_recents(True)
        payload = export_backup(config)
        assert payload['home_to_recents'] is True
        assert validate_backup(payload) == (True, None)
        config.set_home_to_recents(False)
        assert restore_backup(config, payload) is True
        assert config.home_to_recents is True

    def test_documented(self):
        docs = (SRC.parent.parent / 'docs' / 'config.md').read_text(
            encoding='utf-8')
        assert 'home_to_recents' in docs


# -- item 4: home_to_recents ----------------------------------------------------

def _recents_shell(config, page='home', switcher_visible=False,
                   shade_visible=False, recents=False):
    calls = []
    shell = types.SimpleNamespace(
        config=config,
        stack=types.SimpleNamespace(get_visible_child_name=lambda: page,
                                    set_visible_child_name=lambda _n: None),
        _switcher=types.SimpleNamespace(
            get_visible=lambda: switcher_visible,
            hide_switcher=lambda: calls.append('hide-switcher')),
        _shade=types.SimpleNamespace(
            get_visible=lambda: shade_visible,
            hide_shade=lambda: calls.append('hide-shade')),
        _dialer=None,
        present_over_apps=lambda: calls.append('present'),
        _show_switcher=lambda: calls.append('switcher'),
    )
    shell.calls = calls
    shell.recents = recents
    return shell


class TestHomeToRecents:
    def test_home_on_home_opens_switcher(self, home_dir):
        import window
        config = ShellConfig()
        config.set_home_to_recents(True)
        shell = _recents_shell(config)
        window.ShellWindow.go_home(shell)
        assert shell.calls == ['switcher']

    def test_home_on_home_default_goes_home(self, home_dir):
        import window
        config = ShellConfig()
        shell = _recents_shell(config)
        window.ShellWindow.go_home(shell)
        assert 'switcher' not in shell.calls
        assert shell.calls[-1] == 'present'

    def test_overlay_visible_keeps_old_path(self, home_dir):
        import window
        config = ShellConfig()
        config.set_home_to_recents(True)
        shell = _recents_shell(config, switcher_visible=True)
        window.ShellWindow.go_home(shell)
        assert shell.calls[0] == 'hide-switcher'
        assert 'switcher' not in [c for c in shell.calls[1:]
                                  if c == 'switcher']

    def test_away_from_home_goes_home(self, home_dir):
        import window
        config = ShellConfig()
        config.set_home_to_recents(True)
        shell = _recents_shell(config, page='apps')
        window.ShellWindow.go_home(shell)
        assert 'switcher' not in shell.calls


# -- item 5: back on home -------------------------------------------------------

def _back_shell(**over):
    calls = []
    launcher = types.SimpleNamespace(
        close_folder=lambda: False, edit_mode=False,
        set_edit_mode=lambda enabled: calls.append(('edit', enabled)))
    shell = types.SimpleNamespace(
        _switcher=None, _shade=None, _dialer=None,
        _home_launcher=launcher,
        stack=types.SimpleNamespace(
            get_visible_child_name=lambda: 'home',
            set_visible_child_name=lambda _n: calls.append(('page', _n)),
            set_transition_type=lambda _t: None),
        calls=calls,
    )
    for key, value in over.items():
        setattr(shell, key, value)
    return shell


class TestBackOnHome:
    def test_open_folder_collapses_first(self, home_dir):
        import window
        shell = _back_shell()
        shell._switcher = types.SimpleNamespace(
            get_visible=lambda: True,
            hide_switcher=lambda: shell.calls.append('hide-switcher'))
        shell._home_launcher.close_folder = lambda: True
        window.ShellWindow._handle_back(shell)
        assert shell.calls == []

    def test_edit_mode_leaves_before_old_path(self, home_dir):
        import window
        shell = _back_shell()
        shell._home_launcher.edit_mode = True
        window.ShellWindow._handle_back(shell)
        assert shell.calls == [('edit', False)]

    def test_old_path_intact_without_home_state(self, home_dir):
        import window
        shell = _back_shell()
        shell._switcher = types.SimpleNamespace(
            get_visible=lambda: True,
            hide_switcher=lambda: shell.calls.append('hide-switcher'))
        window.ShellWindow._handle_back(shell)
        assert shell.calls == ['hide-switcher']

    def test_back_source_order(self):
        body = (SRC / 'window.py').read_text(encoding='utf-8')
        chunk = body.split('def _handle_back')[1].split(
            'def _build_widget_block')[0]
        assert chunk.find('close_folder') < chunk.find('edit_mode')
        assert chunk.find('edit_mode') < chunk.find('hide_switcher')


# -- item 6: empty folder tap ---------------------------------------------------

class TestEmptyFolderTap:
    def _launcher(self):
        import home_launcher
        inst = home_launcher.HomeLauncher.__new__(home_launcher.HomeLauncher)
        statuses = []
        inst._open_folder_index = None
        inst._build = lambda *a, **k: statuses.append('built')
        inst._on_status = statuses.append
        inst.statuses = statuses
        return inst

    def test_empty_folder_reports_status(self):
        import home_launcher
        inst = self._launcher()
        home_launcher.HomeLauncher._toggle_folder(
            inst, 0, {'type': 'folder', 'label': 'Empty', 'folder': []})
        assert 'Folder is empty' in inst.statuses
        assert inst._open_folder_index is None

    def test_nonempty_folder_opens(self):
        import home_launcher
        inst = self._launcher()
        home_launcher.HomeLauncher._toggle_folder(
            inst, 0, {'type': 'folder', 'label': 'Tools',
                      'folder': [{'label': 'Calc'}]})
        assert inst._open_folder_index == 0

    def test_status_wired_from_home_page(self):
        import inspect
        import home_page
        src = inspect.getsource(home_page.HomePage._build_home_page)
        assert '_show_status' in src


# -- item 1: slot menu + replace -------------------------------------------------

class TestSlotMenu:
    def _win(self, config, slots):
        captured = {}
        win = _home_win(config)
        win.item_actions = types.SimpleNamespace(
            _show_action_menu=lambda a, t, acts, **_k: captured.update(
                anchor=a, title=t, actions=acts),
            _show_entry_dialog=lambda *a, **k: captured.update(
                entry=(a, k)),
            _show_member_info=lambda *a: captured.update(info=a),
            _show_popover=lambda *a: captured.update(popover=a),
            _rename_in_slots=lambda app_id, label: captured.update(
                renamed=(app_id, label)),
        )
        win.captured = captured
        config.set_home_slots(slots)
        return win

    def test_app_slot_name_opens_three_item_menu(self, home_dir):
        config = ShellConfig()
        win = self._win(config, [
            {'type': 'app', 'label': 'Notes', 'app_id': 'notes.desktop'},
        ])
        HomePage(win)._rename_slot(object(), 0)
        labels = [label for label, _cb in win.captured['actions']]
        assert labels == ['Rename', 'Change app', 'App info']

    def test_folder_slot_renames_directly(self, home_dir):
        config = ShellConfig()
        win = self._win(config, [
            {'type': 'folder', 'label': 'Tools', 'folder': []},
        ])
        HomePage(win)._rename_slot(object(), 0)
        assert 'actions' not in win.captured
        assert 'entry' in win.captured

    def test_change_app_enters_replace_pick_mode(self, home_dir):
        config = ShellConfig()
        win = self._win(config, [
            {'type': 'app', 'label': 'Notes', 'app_id': 'notes.desktop'},
            {'type': 'app', 'label': 'Calc', 'app_id': 'calc.desktop'},
        ])
        HomePage(win)._rename_slot(object(), 1)
        change = dict(win.captured['actions'])['Change app']
        shown = []
        win.stack.set_visible_child_name = shown.append
        change()
        assert win._pick_slot_mode is True
        assert win._pick_slot_target == 1
        assert shown == ['apps']

    def test_replace_keeps_position(self, home_dir):
        config = ShellConfig()
        win = self._win(config, [
            {'type': 'app', 'label': 'Notes', 'app_id': 'notes.desktop'},
            {'type': 'app', 'label': 'Calc', 'app_id': 'calc.desktop'},
        ])
        refreshed = []
        win._home_launcher = types.SimpleNamespace(
            refresh=lambda *a, **k: refreshed.append(True))
        populated = []
        win._populate_apps = lambda _q='': populated.append(True)
        shown = []
        win.stack.set_visible_child_name = shown.append
        HomePage(win)._replace_app_slot(0, _entry('photos.desktop', 'Photos'))
        slots = config.home_slots
        assert [s.get('app_id') for s in slots] == [
            'photos.desktop', 'calc.desktop']
        assert slots[0]['label'] == 'Photos'
        assert shown == ['home']

    def test_drawer_pick_replaces_target(self, home_dir):
        import drawer_page
        config = ShellConfig()
        config.set_home_slots([
            {'type': 'app', 'label': 'Notes', 'app_id': 'notes.desktop'},
        ])
        added = []
        win = types.SimpleNamespace(
            config=config, _pick_slot_mode=True, _pick_slot_target=0,
            _add_app_slot=lambda e: added.append(('add', e.app_id)),
        )
        win._replace_app_slot = lambda i, e: added.append(('replace', i,
                                                           e.app_id))
        drawer_page.DrawerPage(win)._launch_entry(
            _entry('photos.desktop', 'Photos'))
        assert added == [('replace', 0, 'photos.desktop')]
        assert win._pick_slot_mode is False
        assert win._pick_slot_target is None

    def test_drawer_pick_without_target_adds(self, home_dir):
        import drawer_page
        config = ShellConfig()
        added = []
        win = types.SimpleNamespace(
            config=config, _pick_slot_mode=True, _pick_slot_target=None,
            _add_app_slot=lambda e: added.append(e.app_id),
        )
        drawer_page.DrawerPage(win)._launch_entry(
            _entry('photos.desktop', 'Photos'))
        assert added == ['photos.desktop']

    def test_leaving_drawer_clears_replace_target(self):
        body = (SRC / 'window.py').read_text(encoding='utf-8')
        chunk = body.split('def _on_stack_page_changed')[1].split(
            'def _gesture_binding_text')[0]
        assert '_pick_slot_target = None' in chunk


# -- item 2: Add to Home ----------------------------------------------------------

class TestAddToHome:
    def _actions(self, config):
        events = []
        act = AppItemActions(
            config, on_changed=lambda: events.append('changed'),
            on_status=lambda msg: events.append(msg))
        act.events = events
        return act

    def test_appends_next_slot(self, home_dir):
        config = ShellConfig()
        config.set_home_slots([
            {'type': 'app', 'label': 'Notes', 'app_id': 'notes.desktop'},
        ])
        act = self._actions(config)
        act._add_to_home('photos.desktop', 'Photos')
        slots = config.home_slots
        assert [s.get('app_id') for s in slots] == [
            'notes.desktop', 'photos.desktop']
        assert 'changed' in act.events

    def test_duplicate_reports_already_on_home(self, home_dir):
        config = ShellConfig()
        config.set_home_slots([
            {'type': 'app', 'label': 'Notes', 'app_id': 'notes.desktop'},
            {'type': 'folder', 'label': 'Tools', 'folder': [
                {'label': 'Calc', 'app_id': 'calc.desktop'}]},
        ])
        act = self._actions(config)
        act._add_to_home('notes.desktop', 'Notes')
        act._add_to_home('calc.desktop', 'Calc')
        assert config.home_slots and len(config.home_slots) == 2
        assert sum('Already on home' in e for e in act.events) == 2

    def test_full_reports_home_is_full(self, home_dir):
        config = ShellConfig()
        config.set_home_slots([
            {'type': 'app', 'label': f'A{i}', 'app_id': f'a{i}.desktop'}
            for i in range(8)
        ])
        act = self._actions(config)
        act._add_to_home('photos.desktop', 'Photos')
        assert len(config.home_slots) == 8
        assert any('Home is full' in e for e in act.events)

    def test_drawer_menu_has_add_to_home(self, home_dir):
        config = ShellConfig()
        act = self._actions(config)
        captured = {}
        act._show_action_menu = lambda a, t, acts, **_k: captured.update(
            actions=acts)
        act.show_app_menu(object(), _entry('photos.desktop', 'Photos'))
        assert 'Add to Home' in [label for label, _cb in captured['actions']]

    def test_member_menu_has_add_to_home(self, home_dir):
        config = ShellConfig()
        config.set_home_slots([
            {'type': 'folder', 'label': 'Tools', 'folder': [
                {'label': 'Calc', 'app_id': 'calc.desktop'}]},
        ])
        act = self._actions(config)
        captured = {}
        act._show_action_menu = lambda a, t, acts, **_k: captured.update(
            actions=acts)
        act.show_member_menu(object(), 0, 0)
        assert 'Add to Home' in [label for label, _cb in captured['actions']]

    def test_member_add_to_home_reports_already_on_home(self, home_dir):
        config = ShellConfig()
        config.set_home_slots([
            {'type': 'folder', 'label': 'Tools', 'folder': [
                {'label': 'Calc', 'app_id': 'calc.desktop'}]},
        ])
        act = self._actions(config)
        captured = {}
        act._show_action_menu = lambda a, t, acts, **_k: captured.update(
            actions=acts)
        act.show_member_menu(object(), 0, 0)
        add = dict(captured['actions'])['Add to Home']
        add()
        assert len(config.home_slots) == 1
        assert any('Already on home' in e for e in act.events)


# -- item 7: rename triad ----------------------------------------------------------

class TestRenameTriad:
    def _actions(self, config):
        events = []
        act = AppItemActions(
            config, on_changed=lambda: events.append('changed'),
            on_status=lambda msg: events.append(msg))
        act.events = events
        return act

    def test_empty_drawer_rename_resets_to_real_name(self, home_dir):
        config = ShellConfig()
        config.set_home_slots([
            {'type': 'app', 'label': 'Journal', 'app_id': 'notes.desktop'},
            {'type': 'folder', 'label': 'Tools', 'folder': [
                {'label': 'Journal', 'app_id': 'notes.desktop'}]},
        ])
        config.set_app_label('notes.desktop', 'Journal')
        act = self._actions(config)
        captured = {}
        act._show_entry_dialog = lambda *a, **k: captured.update(
            commit=k.get('on_commit') or a[4])
        act._show_rename(object(), _entry('notes.desktop', 'Notes'))
        captured['commit']('')
        assert 'notes.desktop' not in config.app_labels
        assert config.home_slots[0]['label'] == 'Notes'
        assert config.home_slots[1]['folder'][0]['label'] == 'Notes'

    def _slot_win(self, config, slots):
        menu = {}
        dialogs = []
        propagated = []
        win = _home_win(config)
        win.item_actions = types.SimpleNamespace(
            _show_action_menu=lambda a, t, acts, **_k: menu.update(actions=acts),
            _show_entry_dialog=lambda a, t, initial, label, commit, **k:
                dialogs.append(commit),
            _rename_in_slots=lambda app_id, label: propagated.append(
                (app_id, label)),
        )
        win.menu = menu
        win.dialogs = dialogs
        win.propagated = propagated
        win.app_index = types.SimpleNamespace(
            entries=[_entry('notes.desktop', 'Notes')])
        config.set_home_slots(slots)
        return win

    def _rename_commit(self, win, idx):
        HomePage(win)._rename_slot(object(), idx)
        rename = dict(win.menu['actions'])['Rename']
        rename()
        return win.dialogs[-1]

    def test_home_edit_rename_propagates_everywhere(self, home_dir):
        config = ShellConfig()
        win = self._slot_win(config, [
            {'type': 'app', 'label': 'Notes', 'app_id': 'notes.desktop'},
            {'type': 'folder', 'label': 'Tools', 'folder': [
                {'label': 'Notes', 'app_id': 'notes.desktop'}]},
        ])
        self._rename_commit(win, 0)('Journal')
        assert config.home_slots[0]['label'] == 'Journal'
        assert ('notes.desktop', 'Journal') in win.propagated
        assert config.app_labels.get('notes.desktop') == 'Journal'

    def test_home_edit_empty_rename_resets(self, home_dir):
        config = ShellConfig()
        win = self._slot_win(config, [
            {'type': 'app', 'label': 'Journal', 'app_id': 'notes.desktop'},
        ])
        config.set_app_label('notes.desktop', 'Journal')
        self._rename_commit(win, 0)('')
        assert 'notes.desktop' not in config.app_labels
        assert config.home_slots[0]['label'] == 'Notes'

    def test_empty_member_rename_restores_real_name(self, home_dir):
        config = ShellConfig()
        config.set_home_slots([
            {'type': 'folder', 'label': 'Tools', 'folder': [
                {'label': 'Sums', 'app_id': 'calc.desktop'}]},
        ])
        config.set_app_label('calc.desktop', 'Sums')
        events = []
        act = AppItemActions(
            config, on_changed=lambda: events.append('changed'),
            on_status=lambda msg: events.append(msg),
            real_name_fn=lambda app_id: 'Calculator'
            if app_id == 'calc.desktop' else None)
        captured = {}
        act._show_entry_dialog = lambda *a, **k: captured.update(
            commit=k.get('on_commit') or a[4])
        act._show_member_rename(object(), 0, 0)
        captured['commit']('')
        assert 'calc.desktop' not in config.app_labels
        assert config.home_slots[0]['folder'][0]['label'] == 'Calculator'

    def test_index_refresh_updates_uncustomised_labels(self, home_dir):
        import window
        config = ShellConfig()
        config.set_home_slots([
            {'type': 'app', 'label': 'Old Calc', 'app_id': 'calc.desktop'},
            {'type': 'app', 'label': 'Mine', 'app_id': 'notes.desktop'},
            {'type': 'app', 'label': 'Run', 'cmd': ['run-thing']},
            {'type': 'folder', 'label': 'Tools', 'folder': [
                {'label': 'Old Calc', 'app_id': 'calc.desktop'}]},
        ])
        config.set_app_label('notes.desktop', 'Mine')
        shell = types.SimpleNamespace(
            config=config,
            app_index=types.SimpleNamespace(
                entries=[_entry('calc.desktop', 'Calculator'),
                         _entry('notes.desktop', 'Notes')]),
        )
        window.ShellWindow._sync_home_labels_from_index(shell)
        slots = config.home_slots
        assert slots[0]['label'] == 'Calculator'
        assert slots[1]['label'] == 'Mine'
        assert slots[2]['label'] == 'Run'
        assert slots[3]['folder'][0]['label'] == 'Calculator'

    def test_entry_dialog_supports_empty_commit(self, home_dir, monkeypatch):
        import app_item_actions as module
        from types import SimpleNamespace

        class _Box:
            def __init__(self, *a, **k):
                self.children = []

            def add_css_class(self, _n):
                pass

            def append(self, w):
                self.children.append(w)

        class _Label:
            def __init__(self, *a, **k):
                pass

            def add_css_class(self, _n):
                pass

            def set_ellipsize(self, _v):
                pass

            def set_max_width_chars(self, _v):
                pass

        class _Entry:
            def __init__(self, *a, **k):
                self.text = ''
                self.handlers = {}

            def add_css_class(self, _n):
                pass

            def set_text(self, value):
                self.text = value

            def get_text(self):
                return self.text

            def select_region(self, *_a):
                pass

            def set_visibility(self, _v):
                pass

            def set_input_purpose(self, _v):
                pass

            def set_max_width_chars(self, _v):
                pass

            def connect(self, signal, callback):
                self.handlers[signal] = callback

        class _Button:
            def __init__(self, *a, **k):
                self.handlers = {}

            def add_css_class(self, _n):
                pass

            def get_child(self):
                return SimpleNamespace(
                    set_xalign=lambda _v: None, set_wrap=lambda _v: None,
                    set_wrap_mode=lambda _v: None,
                    set_max_width_chars=lambda _v: None)

            def connect(self, signal, callback):
                self.handlers[signal] = callback

        monkeypatch.setattr(module, 'Gtk', SimpleNamespace(
            Box=_Box, Label=_Label, Entry=_Entry, Button=_Button,
            Orientation=SimpleNamespace(HORIZONTAL=0, VERTICAL=1),
            InputPurpose=SimpleNamespace(PASSWORD='pw')))
        monkeypatch.setattr(module, 'Pango', SimpleNamespace(
            WrapMode=SimpleNamespace(WORD_CHAR='wc'),
            EllipsizeMode=SimpleNamespace(END='end')))
        config = ShellConfig()
        act = self._actions(config)
        committed = []
        _box, entry, commit = act._entry_dialog_content(
            'Rename', 'Notes', 'Rename', committed.append,
            allow_empty=True)
        entry.set_text('   ')
        commit()
        assert committed == ['']


# -- item 8: uninstall purge -------------------------------------------------------

def _purge_shell(config, **over):
    shell = types.SimpleNamespace(config=config)
    for key, value in over.items():
        setattr(shell, key, value)
    return shell


class TestUninstallPurge:
    def _seeded(self, config):
        config.set_home_slots([
            {'type': 'app', 'label': 'Gone', 'app_id': 'gone.desktop'},
            {'type': 'app', 'label': 'Stay', 'app_id': 'stay.desktop'},
            {'type': 'app', 'label': 'Run', 'cmd': ['run-thing']},
            {'type': 'app', 'label': 'Droid',
             'app_id': 'com.example.droid'},
            {'type': 'folder', 'label': 'Tools', 'folder': [
                {'label': 'Gone', 'app_id': 'gone.desktop'},
                {'label': 'Phone'},
                {'label': 'Run', 'cmd': ['run-thing']},
            ]},
        ])
        config.set_pinned(['gone.desktop', 'stay.desktop'])
        config.set_hidden_apps(['gone.desktop', 'stay.desktop'])
        config.set_app_label('gone.desktop', 'Gone')
        config.set_app_label('stay.desktop', 'Stay')

    def test_purge_drops_uninstalled_everywhere(self, home_dir):
        import window
        config = ShellConfig()
        self._seeded(config)
        window.ShellWindow._purge_uninstalled_apps(
            _purge_shell(config), {'gone.desktop'})
        slots = config.home_slots
        assert [s.get('app_id') for s in slots if s.get('type') == 'app'] == [
            'stay.desktop', None, 'com.example.droid']
        members = next(s['folder'] for s in slots
                       if s.get('type') == 'folder')
        assert [m.get('label') for m in members] == ['Phone', 'Run']
        assert config.pinned == ['stay.desktop']
        assert config.hidden_apps == ['stay.desktop']
        assert 'gone.desktop' not in config.app_labels
        assert config.app_labels.get('stay.desktop') == 'Stay'

    def test_reload_purges_only_real_uninstalls(self, home_dir):
        import window
        config = ShellConfig()
        self._seeded(config)
        calls = []
        index = types.SimpleNamespace(
            entries=[_entry('gone.desktop', 'Gone'),
                     _entry('stay.desktop', 'Stay')],
        )

        def _refresh():
            index.entries = [_entry('stay.desktop', 'Stay')]
            return index.entries

        index.refresh = _refresh
        shell = _purge_shell(
            config, app_index=index, _app_refresh_timer=None,
            _home_launcher=types.SimpleNamespace(
                refresh=lambda *a, **k: None),
            apps_search=types.SimpleNamespace(get_text=lambda: ''),
            _populate_apps=lambda _q='': calls.append('populated'))
        window.ShellWindow._reload_app_index(shell)
        assert 'gone.desktop' not in config.pinned
        assert calls == ['populated']

    def test_reload_empty_index_purges_nothing(self, home_dir):
        import window
        config = ShellConfig()
        self._seeded(config)
        index = types.SimpleNamespace(
            entries=[_entry('gone.desktop', 'Gone')],
        )
        # Simulate the transient scan: refresh reports nothing installed.
        def _empty():
            index.entries = []
            return index.entries

        index.refresh = _empty
        shell = _purge_shell(
            config, app_index=index, _app_refresh_timer=None,
            _home_launcher=types.SimpleNamespace(
                refresh=lambda *a, **k: None),
            apps_search=types.SimpleNamespace(get_text=lambda: ''),
            _populate_apps=lambda _q='': None)
        window.ShellWindow._reload_app_index(shell)
        assert config.pinned == ['gone.desktop', 'stay.desktop']
        assert len(config.home_slots) == 5


