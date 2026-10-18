"""Lane lp_folders: folder management parity and drag-to-reorder.

Headless behavior seams for every item (folder menu, counts, name
validation, last-member cleanup, delete confirms, member Hide/Show +
Sort A-Z, reorder model + order appliers), plus a Broadway smoke that
builds the real reorder sheet at phone size and drives a drop through
the same path as Gtk.DropTarget.
"""
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

gi = pytest.importorskip('gi')
gi.require_version('Gtk', '4.0')

from app_index import AppEntry  # noqa: E402
from app_item_actions import AppItemActions, validate_folder_name  # noqa: E402
from config import ShellConfig  # noqa: E402

import drawer_page  # noqa: E402
import reorder_sheet  # noqa: E402


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
            {'label': 'Camera', 'app_id': 'camera.desktop'},
            {'label': 'Calc', 'app_id': 'calc.desktop'},
        ]},
        {'type': 'folder', 'label': 'Comms', 'folder': [
            {'label': 'Phone', 'app_id': 'phone.desktop'},
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


def _capture_menus(actions):
    """Record (title, [(label, callback)]) for every action menu shown."""
    menus = []

    def _capture(_anchor, title, action_list, **_kw):
        menus.append((title, list(action_list)))

    actions._show_action_menu = _capture
    return menus


# -- item 3: folder names -----------------------------------------------

class TestFolderNameValidation:
    def test_blank_rejected(self):
        assert validate_folder_name([], '   ') is not None

    def test_duplicate_case_insensitive(self, config):
        slots = config.home_slots
        assert validate_folder_name(slots, 'tools') is not None
        assert validate_folder_name(slots, 'TOOLS') is not None

    def test_self_rename_same_case_allowed(self, config):
        assert validate_folder_name(config.home_slots, 'TOOLS', exclude=1) is None

    def test_other_folder_rename_to_duplicate_rejected(self, config):
        assert validate_folder_name(
            config.home_slots, 'tools', exclude=2) is not None

    def test_valid_name_accepted(self, config):
        assert validate_folder_name(config.home_slots, 'Utils') is None

    def test_rename_blank_keeps_old_name(self, actions, config):
        assert actions.rename_folder(1, '   ') is False
        assert config.home_slots[1]['label'] == 'Tools'
        assert actions.events and 'changed' not in actions.events

    def test_rename_duplicate_keeps_old_name(self, actions, config):
        assert actions.rename_folder(1, 'comms') is False
        assert config.home_slots[1]['label'] == 'Tools'

    def test_rename_valid_persists(self, actions, config):
        assert actions.rename_folder(1, 'Utils') is True
        assert config.home_slots[1]['label'] == 'Utils'
        assert 'changed' in actions.events


# -- item 1: long-press menu on drawer folder rows -----------------------

class TestFolderMenu:
    def test_menu_lists_folder_actions(self, actions):
        menus = _capture_menus(actions)
        actions.show_folder_menu(object(), 1)
        assert len(menus) == 1
        _title, items = menus[0]
        assert [label for label, _cb in items] == [
            'Rename', 'Move up', 'Move down',
            'Sort A–Z', 'Rearrange…', 'Delete']

    def test_menu_ignores_non_folders(self, actions):
        menus = _capture_menus(actions)
        actions.show_folder_menu(object(), 0)
        actions.show_folder_menu(object(), 99)
        assert menus == []

    def test_delete_asks_first_with_drawer_confirm(self, actions, config):
        menus = _capture_menus(actions)
        actions.show_folder_menu(object(), 1)
        delete = dict(menus[0][1])['Delete']
        delete()
        assert len(menus) == 2
        title, confirm_items = menus[1]
        assert 'Delete folder' in title and 'Tools' in title
        assert 'Its apps return to the drawer' in title
        labels = [label for label, _cb in confirm_items]
        assert labels == ['Delete', 'Cancel']
        dict(confirm_items)['Cancel']()
        assert len(config.home_slots) == 3
        dict(confirm_items)['Delete']()
        assert [s['label'] for s in config.home_slots] == ['Notes', 'Comms']
        assert 'changed' in actions.events

    def test_move_folder_swaps_slots(self, actions, config):
        assert actions.move_folder(1, +1) is True
        assert [s['label'] for s in config.home_slots] == [
            'Notes', 'Comms', 'Tools']

    def test_move_folder_clamps_at_ends(self, actions, config):
        assert actions.move_folder(2, +1) is False
        assert [s['label'] for s in config.home_slots][2] == 'Comms'
        assert actions.move_folder(0, +1) is False
        assert 'changed' not in actions.events

    def test_sort_folder_members_alphabetical(self, actions, config):
        assert actions.sort_folder_members(1) is True
        members = config.home_slots[1]['folder']
        assert [m['label'] for m in members] == ['Calc', 'Camera']

    def test_folder_reorder_needs_two_members(self, actions):
        menus = _capture_menus(actions)
        actions.show_folder_menu(object(), 2)
        by_label = dict(menus[0][1])
        by_label['Rearrange…']()
        assert any('two' in msg for msg in actions.events)


# -- item 2: member counts ------------------------------------------------

class TestFolderCounts:
    def test_title_carries_android_style_count(self):
        assert drawer_page.drawer_folder_title('Tools', 3) == 'Tools (3)'

    def test_row_maker_counts_installed_members(self):
        import inspect
        src = inspect.getsource(drawer_page.DrawerPage._make_drawer_folder_row)
        assert 'drawer_folder_title' in src

    def test_folder_row_long_press_opens_folder_menu(self):
        import inspect
        src = inspect.getsource(drawer_page.DrawerPage._make_drawer_folder_row)
        assert 'show_folder_menu' in src


# -- item 4: last member out deletes the folder ---------------------------

class TestLastMemberCleanup:
    def test_removing_last_member_clears_slot(self, actions, config):
        actions._remove_member(2, 0)
        assert [s['label'] for s in config.home_slots] == ['Notes', 'Tools']
        assert 'changed' in actions.events

    def test_removing_one_of_many_keeps_folder(self, actions, config):
        actions._remove_member(1, 0)
        assert config.home_slots[1]['label'] == 'Tools'
        assert len(config.home_slots[1]['folder']) == 1


# -- item 6+7: member menu Hide/Show + Sort A–Z ----------------------------

class TestMemberMenu:
    def _labels(self, actions, slot=1, member=0):
        menus = _capture_menus(actions)
        actions.show_member_menu(object(), slot, member)
        assert len(menus) == 1
        return menus[0][1]

    def test_member_menu_lists_hide_sort_rearrange(self, actions):
        labels = [label for label, _cb in self._labels(actions)]
        assert 'Hide' in labels
        assert 'Sort A–Z' in labels
        assert 'Rearrange…' in labels
        assert 'Remove from folder' in labels

    def test_member_hide_and_show(self, actions, config):
        items = dict(self._labels(actions))
        items['Hide']()
        assert 'camera.desktop' in config.hidden_apps
        items = dict(self._labels(actions))
        assert 'Show' in items
        items['Show']()
        assert 'camera.desktop' not in config.hidden_apps

    def test_member_sort_orders_members(self, actions, config):
        items = dict(self._labels(actions))
        items['Sort A–Z']()
        assert [m['label'] for m in config.home_slots[1]['folder']] == [
            'Calc', 'Camera']

    def test_member_rearrange_needs_two_members(self, actions):
        menus = _capture_menus(actions)
        actions.show_member_menu(object(), 2, 0)
        dict(menus[0][1])['Rearrange…']()
        assert any('two' in msg for msg in actions.events)


# -- item 8: reorder model + order appliers -------------------------------

class TestReorderModel:
    def test_drag_move_repositions(self):
        model = reorder_sheet.ReorderModel(
            [('a', 'A'), ('b', 'B'), ('c', 'C'), ('d', 'D')])
        assert model.move(0, 2) is True
        assert model.ids() == ['b', 'c', 'a', 'd']

    def test_drag_move_to_end(self):
        model = reorder_sheet.ReorderModel([('a', 'A'), ('b', 'B')])
        assert model.move(0, 1) is True
        assert model.ids() == ['b', 'a']

    def test_move_rejects_bad_indices(self):
        model = reorder_sheet.ReorderModel([('a', 'A')])
        assert model.move(0, 5) is False
        assert model.move(3, 0) is False
        assert model.ids() == ['a']

    def test_swap_up_down(self):
        model = reorder_sheet.ReorderModel([('a', 'A'), ('b', 'B')])
        assert model.swap(1, -1) is True
        assert model.ids() == ['b', 'a']
        assert model.swap(1, +1) is False

    def test_set_order_applies_permutation(self):
        model = reorder_sheet.ReorderModel([('a', 'A'), ('b', 'B')])
        assert model.set_order(['b', 'a']) is True
        assert model.ids() == ['b', 'a']

    def test_set_order_rejects_partial(self):
        model = reorder_sheet.ReorderModel([('a', 'A'), ('b', 'B')])
        assert model.set_order(['b']) is False
        assert model.ids() == ['a', 'b']


class TestApplyOrder:
    def test_apply_member_order_persists_silently(self, actions, config):
        actions.apply_member_order(1, ['1', '0'])
        assert [m['label'] for m in config.home_slots[1]['folder']] == [
            'Calc', 'Camera']
        assert 'changed' not in actions.events

    def test_apply_slot_order_permutes_home(self, actions, config):
        actions.apply_slot_order(['2', '0', '1'])
        assert [s['label'] for s in config.home_slots] == [
            'Comms', 'Notes', 'Tools']

    def test_apply_slot_order_rejects_partial(self, actions, config):
        before = [s['label'] for s in config.home_slots]
        actions.apply_slot_order(['1', '0'])
        assert [s['label'] for s in config.home_slots] == before

    def test_apply_pinned_order(self, actions, config):
        actions._set_pinned(_entry('a.desktop', 'A'), True)
        actions._set_pinned(_entry('b.desktop', 'B'), True)
        actions.events.clear()
        actions.apply_pinned_order(['b.desktop', 'a.desktop'])
        assert config.pinned == ['b.desktop', 'a.desktop']
        assert 'changed' not in actions.events

    def test_pinned_menu_gains_rearrange(self, actions, config):
        actions._set_pinned(_entry('a.desktop', 'A'), True)
        actions._set_pinned(_entry('b.desktop', 'B'), True)
        menus = _capture_menus(actions)
        actions.show_app_menu(object(), _entry('a.desktop', 'A'))
        labels = [label for label, _cb in menus[0][1]]
        assert 'Rearrange pinned…' in labels


# -- item 5: home edit-mode delete asks first ------------------------------

class TestHomeSlotRemoval:
    def _page(self, actions, config, statuses):
        import home_page

        win = SimpleNamespace(
            config=config,
            item_actions=actions,
            _home_launcher=SimpleNamespace(refresh=lambda **_k: None),
            _populate_apps=lambda _q: None,
            _show_status=statuses.append,
            apps_search=SimpleNamespace(get_text=lambda: ''),
        )
        page = home_page.HomePage.__new__(home_page.HomePage)
        page._win = win
        return page

    def test_folder_remove_with_widget_confirms_first(self, actions, config):
        menus = _capture_menus(actions)
        statuses = []
        page = self._page(actions, config, statuses)
        page._remove_slot(1, widget=object())
        assert len(config.home_slots) == 3
        assert len(menus) == 1
        title, items = menus[0]
        assert 'Delete folder "Tools"?' in title
        assert 'Its apps return to the drawer' in title
        dict(items)['Delete']()
        assert [s['label'] for s in config.home_slots] == ['Notes', 'Comms']

    def test_folder_remove_without_widget_deletes_directly(self, actions, config):
        page = self._page(actions, config, [])
        page._remove_slot(1)
        assert [s['label'] for s in config.home_slots] == ['Notes', 'Comms']

    def test_app_slot_removes_without_confirm(self, actions, config):
        menus = _capture_menus(actions)
        page = self._page(actions, config, [])
        page._remove_slot(0, widget=object())
        assert menus == []
        assert [s['label'] for s in config.home_slots] == ['Tools', 'Comms']

    def test_create_folder_rejects_blank_and_duplicate(self, actions, config):
        statuses = []
        page = self._page(actions, config, statuses)
        page._create_folder_slot('   ')
        page._create_folder_slot('tools')
        assert len(config.home_slots) == 3
        assert len(statuses) == 2

    def test_edit_rearrange_action_opens_home_sheet(self, actions, config, monkeypatch):
        seen = []
        monkeypatch.setattr(
            actions, 'show_home_reorder', lambda anchor: seen.append(anchor))
        page = self._page(actions, config, [])
        page._on_edit_action('rearrange', widget=object())
        assert len(seen) == 1

    def test_edit_footer_lists_rearrange_for_two_slots(self):
        import home_launcher

        full = home_launcher.HomeLauncher._edit_footer_actions(3)
        assert ('rearrange', 'Rearrange…') in full
        single = home_launcher.HomeLauncher._edit_footer_actions(1)
        assert all(action != 'rearrange' for action, _label in single)


# -- Broadway: real sheet at phone size -----------------------------------

_LP_BROADWAY_SMOKE = '''\\
"""lp_folders Broadway smoke: real reorder sheet, drop saves order."""
import glob
import os
import subprocess
import sys
import time

src_dir, run_dir = sys.argv[1], sys.argv[2]
os.environ['GDK_BACKEND'] = 'broadway'
home = os.path.join(run_dir, 'home')
os.makedirs(home, exist_ok=True)
os.environ['HOME'] = home
os.environ['XDG_CONFIG_HOME'] = os.path.join(home, '.config')
os.environ['XDG_CACHE_HOME'] = os.path.join(home, '.cache')
os.environ['XDG_DATA_HOME'] = os.path.join(home, '.local', 'share')
os.environ['XDG_RUNTIME_DIR'] = run_dir
# A probed free display (a fixed number collided with parallel runs).
_common = src_dir
while _common != os.path.dirname(_common) and not os.path.isdir(
        os.path.join(_common, 'common', 'xx_testing')):
    _common = os.path.dirname(_common)
sys.path.insert(0, os.path.join(_common, 'common'))
from xx_testing import broadway_display
daemon, display = broadway_display.start(run_dir=run_dir)
os.environ['BROADWAY_DISPLAY'] = display
try:
    for _i in range(200):
        if glob.glob(os.path.join(run_dir, 'broadway*.socket')):
            break
        time.sleep(0.05)
    else:
        print('SMOKE FAIL: no broadway socket')
        sys.exit(1)
    sys.path.insert(0, src_dir)
    candidate = src_dir
    for _i in range(4):
        candidate = os.path.dirname(candidate)
        xx_ui_dir = os.path.join(candidate, 'common')
        if os.path.isdir(os.path.join(xx_ui_dir, 'xx_ui')):
            sys.path.insert(0, xx_ui_dir)
            break
    import gi
    gi.require_version('Gtk', '4.0')
    from gi.repository import Gtk, GLib

    import reorder_sheet

    failures = []
    sys.excepthook = lambda *exc: failures.append(repr(exc[1]))
    saved = []

    win = Gtk.Window()
    win.set_default_size(360, 720)
    box, view = reorder_sheet.build_reorder_box(
        'Rearrange Tools',
        [('calc', 'Calc'), ('camera', 'Camera'), ('phone', 'Phone')],
        on_order_changed=saved.append,
        on_done=lambda: None)
    win.set_child(box)
    win.present()

    def _walk(widget):
        yield widget
        child = widget.get_first_child()
        while child is not None:
            yield from _walk(child)
            child = child.get_next_sibling()

    def step_checks():
        try:
            rows = [w for w in _walk(box)
                    if isinstance(w, Gtk.ListBoxRow)]
            assert len(rows) == 3, len(rows)
            handles = [w.get_text() for w in _walk(box)
                       if isinstance(w, Gtk.Label)
                       and w.get_text() == reorder_sheet.DRAG_HANDLE_GLYPH]
            assert len(handles) == 3, len(handles)
            minimum, _natural = box.measure(
                Gtk.Orientation.HORIZONTAL, -1)[:2]
            print(f'SHEET: min width {minimum}')
            assert minimum <= 360, minimum
            assert view.model.ids() == ['calc', 'camera', 'phone']
            assert view.move_row('calc', 'phone') is True
            assert view.model.ids() == ['camera', 'phone', 'calc']
            assert saved and saved[-1] == ['camera', 'phone', 'calc'], saved
            shown = [r.get_child().get_first_child().get_text()
                     for r in rows]
            assert view.row_ids() == ['camera', 'phone', 'calc'], shown
            view.replace([('camera', 'Camera'), ('phone', 'Phone')])
            assert view.model.ids() == ['camera', 'phone']
            assert len([w for w in _walk(box)
                        if isinstance(w, Gtk.ListBoxRow)]) == 2
            print(f"WINDOW: allocated {win.get_width()}x{win.get_height()}")
        except Exception as exc:
            failures.append(repr(exc))
        finally:
            loop.quit()
        return False

    def guard(step):
        def run():
            try:
                return step()
            except Exception as exc:
                failures.append(repr(exc))
                loop.quit()
                return False
        return run

    loop = GLib.MainLoop()
    GLib.timeout_add(1500, guard(step_checks))
    GLib.timeout_add(12000, guard(lambda: (
        failures.append('timeout'), loop.quit(), False)[2]))
    loop.run()
    assert not failures, failures
    print('LP_FOLDERS SMOKE OK')
finally:
    daemon.terminate()
'''


class TestBroadwayReorderSheet:
    def test_real_sheet_renders_and_drop_saves(self, tmp_path):
        import shutil
        if shutil.which('gtk4-broadwayd') is None:
            pytest.skip('needs gtk4-broadwayd')
        pytest.importorskip('gi')
        run_dir = tmp_path / 'broadway'
        run_dir.mkdir()
        script = tmp_path / 'lp_folders_broadway_smoke.py'
        script.write_text(_LP_BROADWAY_SMOKE)
        src = Path(__file__).parent.parent / 'launcher' / 'src'
        proc = subprocess.run(
            [sys.executable, str(script), str(src), str(run_dir)],
            capture_output=True, text=True, timeout=180)
        assert 'LP_FOLDERS SMOKE OK' in proc.stdout, (
            proc.returncode, proc.stdout[-3000:], proc.stderr[-3000:])
