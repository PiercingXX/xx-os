"""Drawer folder population (design.md "App drawer" row order + Folders).

The drawer's folder machinery (expansion state, folder/member rows,
swipe-right collapse) shipped in WS19-21 but ``folder_slots`` was never
populated, so no folder ever appeared. These tests pin the population
seam: ``_drawer_folder_slots`` filters home slots to folders with at
least one installed member, preserves original member indices (the
shared member menu indexes the raw config list), and keeps home-slot
order. Row insertion itself needs live GTK; the wiring is pinned by
source inspection.
"""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


@pytest.fixture(scope='module')
def window():
    """Import window.py once against fake gi bindings (see
    test_keyboard_dismiss.py for the full rationale)."""
    def require_version(namespace, version):
        if namespace == 'Gtk4LayerShell':
            raise ValueError('no layer shell')
        return None

    gi = types.ModuleType('gi')
    gi.require_version = require_version
    repo = types.ModuleType('gi.repository')
    gi.repository = repo

    def make(name):
        return types.ModuleType(f'gi.repository.{name}')

    adw, gdk, glib, gtk, pango, gio = (
        make('Adw'), make('Gdk'), make('GLib'), make('Gtk'),
        make('Pango'), make('Gio'),
    )
    adw.ApplicationWindow = type('ApplicationWindow', (), {})
    gtk.Editable = type('Editable', (), {})
    gtk.PickFlags = types.SimpleNamespace(DEFAULT=0)
    gtk.Align = types.SimpleNamespace(END=3, FILL=1)

    installed = {
        'gi': gi, 'gi.repository': repo,
        'gi.repository.Adw': adw, 'gi.repository.Gdk': gdk,
        'gi.repository.GLib': glib, 'gi.repository.Gtk': gtk,
        'gi.repository.Pango': pango, 'gi.repository.Gio': gio,
    }
    saved = {name: sys.modules.get(name) for name in installed}
    sys.modules.update(installed)
    try:
        import window
        # Don't leak our fake-bound module: later files must import
        # window.py against their own seams (see test_theme_hot_reload).
        sys.modules.pop('window', None)
        yield window
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


class _FakeIndex:
    def __init__(self, app_ids):
        class _E:
            pass
        self.entries = []
        for app_id in app_ids:
            e = _E()
            e.app_id = app_id
            e.name = app_id
            self.entries.append(e)

    def search(self, query):
        return list(self.entries)


class _FakeListBox:
    """Just enough ListBox for _populate_apps' row insertion calls."""

    def insert(self, *_a):
        return None

    def append(self, *_a):
        return None

    def set_valign(self, *_a):
        return None


def _bare(window, slots, installed_ids):
    w = object.__new__(window.ShellWindow)
    w.config = types.SimpleNamespace(home_slots=slots, app_labels={},
                                     hidden_apps=set(), pinned=[])
    w.app_index = _FakeIndex(installed_ids)
    return w


def test_folders_listed_in_home_order(window):
    w = _bare(window, [
        {'type': 'app', 'app_id': 'a'},
        {'type': 'folder', 'label': 'Tools', 'folder': [
            {'label': 'Calc', 'app_id': 'calc'}]},
        {'type': 'app', 'app_id': 'b'},
        {'type': 'folder', 'label': 'Comms', 'folder': [
            {'label': 'Phone', 'app_id': 'phone'}]},
    ], ['calc', 'phone', 'a', 'b'])
    assert [s['label'] for _i, s, _m in w._drawer_folder_slots()] == ['Tools', 'Comms']


def test_empty_folder_never_appears(window):
    w = _bare(window, [
        {'type': 'folder', 'label': 'Empty', 'folder': []},
        {'type': 'folder', 'label': 'Real', 'folder': [
            {'label': 'X', 'app_id': 'x'}]},
    ], ['x'])
    assert [s['label'] for _i, s, _m in w._drawer_folder_slots()] == ['Real']


def test_uninstalled_members_skipped_at_render(window):
    w = _bare(window, [
        {'type': 'folder', 'label': 'Mix', 'folder': [
            {'label': 'Ghost', 'app_id': 'not-installed'},
            {'label': 'Real', 'app_id': 'real'},
        ]},
    ], ['real'])
    folders = w._drawer_folder_slots()
    assert len(folders) == 1
    _idx, _slot, members = folders[0]
    # Ghost is skipped at render but Real keeps its original index (1),
    # because show_member_menu indexes the raw config list.
    assert members == [(1, {'label': 'Real', 'app_id': 'real'})]


def test_all_members_uninstalled_drops_folder(window):
    w = _bare(window, [
        {'type': 'folder', 'label': 'Gone', 'folder': [
            {'label': 'Ghost', 'app_id': 'not-installed'}]},
    ], [])
    assert w._drawer_folder_slots() == []


def test_stale_open_folder_closes_on_refresh(window, monkeypatch):
    """A folder that disappears while open must not leave a dangling
    expansion (design.md: collapses automatically if the folder goes)."""
    # Open folder 0, then its only member loses its installed app: the
    # folder no longer qualifies and the guard must close it.
    w = _bare(window, [
        {'type': 'folder', 'label': 'Gone', 'folder': [
            {'label': 'Ghost', 'app_id': 'not-installed'}]},
        {'type': 'app', 'app_id': 'x'},
    ], ['x'])
    w._drawer_open_folder = 0
    w.apps_search = types.SimpleNamespace(get_text=lambda: '')
    w._last_search_results = []
    w._sort_mode = 'az'
    w.apps_list = _FakeListBox()
    monkeypatch.setattr(w, '_replace_rows', lambda *a, **k: None)
    monkeypatch.setattr(w, '_make_app_row', lambda e: None)
    monkeypatch.setattr(w, '_make_settings_row', lambda: None)
    monkeypatch.setattr(w, '_make_drawer_folder_row',
                        lambda s, i: types.SimpleNamespace())
    w._populate_apps('')
    assert w._drawer_open_folder is None


def test_populate_wiring_uses_the_seam(window):
    """Row insertion needs live GTK; pin the wiring by source instead."""
    import inspect

    import drawer_page
    src = inspect.getsource(drawer_page.DrawerPage._populate_apps)
    assert 'win._drawer_folder_slots()' in src
    assert 'for idx, slot, members in folder_slots:' in src
    assert 'for m_idx, member in members:' in src

