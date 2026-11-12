"""Drawer cost per keystroke and per app-monitor burst: entries carry their
folded search keys and .desktop mtime from the index refresh, so search
folds only the query and a populate stats nothing; a rescan that changes
nothing rebuilds nothing."""
import sys
import time
import types

import pytest


@pytest.fixture(autouse=True)
def _no_window_leak():
    """window.py imported here binds real GTK; suites that import it
    against fake bindings must not inherit this module."""
    saved = {n: m for n, m in sys.modules.items() if n == 'window'}
    yield
    sys.modules.pop('window', None)
    sys.modules.update(saved)


@pytest.fixture
def desktop_infos(tmp_path):
    from gi.repository import Gio
    infos = []
    for name in ('Café', 'Calculator', 'E-Ink Reader', 'Notes', 'Maps'):
        path = tmp_path / ('%s.desktop' % name.replace(' ', '').lower())
        path.write_text('[Desktop Entry]\nType=Application\nName=%s\n'
                        'Exec=true\n' % name, encoding='utf-8')
        infos.append(Gio.DesktopAppInfo.new_from_filename(str(path)))
    return infos


@pytest.fixture
def index(desktop_infos, monkeypatch):
    import app_index
    monkeypatch.setattr(app_index.Gio.AppInfo, 'get_all',
                        lambda: list(desktop_infos))
    idx = app_index.AppIndex()
    idx.refresh()
    return idx


class TestSearch:
    def test_keystroke_folds_only_the_query(self, index, monkeypatch):
        import app_index
        calls = []
        real = app_index.fold_for_search
        monkeypatch.setattr(app_index, 'fold_for_search',
                            lambda text: calls.append(text) or real(text))
        hits = index.search('cafe')
        assert [e.name for e in hits] == ['Café']
        assert calls == ['cafe']
        assert [e.name for e in index.search('eink')] == ['E-Ink Reader']

    def test_entries_built_elsewhere_still_search(self):
        import app_index
        entry = app_index.AppEntry(
            app_id='x.desktop', name='Café', description='',
            executable='x', search_text='café x', desktop_app=None)
        idx = app_index.AppIndex()
        idx.entries = [entry]
        assert idx.search('cafe') == [entry]


class TestNoStatPerRow:
    def test_refresh_records_the_mtime(self, index):
        assert all(isinstance(e.mtime, float) and e.mtime > 0
                   for e in index.entries)

    def test_new_badge_reads_the_recorded_mtime(self, index, monkeypatch):
        import drawer_page
        from pathlib import Path

        def _no_stat(*_a, **_k):
            raise AssertionError('stat during populate')
        monkeypatch.setattr(Path, 'stat', _no_stat)
        entry = index.entries[0]
        assert drawer_page.app_is_new(entry, now=time.time()) is True
        assert drawer_page.DrawerPage._install_time(entry) == entry.mtime


class TestRescan:
    def test_unchanged_rescan_rebuilds_nothing(self, index, monkeypatch):
        import window
        monkeypatch.setattr(window, '_sync_home_labels', lambda *a: None)
        populated, homes = [], []
        shell = types.SimpleNamespace(
            app_index=index, _app_refresh_timer=1,
            config=types.SimpleNamespace(),
            _home_launcher=types.SimpleNamespace(
                refresh=lambda *a, **k: homes.append(1)),
            apps_search=types.SimpleNamespace(get_text=lambda: ''),
            _populate_apps=lambda _q='': populated.append(1))
        window.ShellWindow._reload_app_index(shell)
        assert populated == [] and homes == []

    def test_changed_rescan_rebuilds(self, index, desktop_infos,
                                     monkeypatch, tmp_path):
        import app_index
        import window
        from gi.repository import Gio
        path = tmp_path / 'new.desktop'
        path.write_text('[Desktop Entry]\nType=Application\nName=New\n'
                        'Exec=true\n', encoding='utf-8')
        more = list(desktop_infos) + [
            Gio.DesktopAppInfo.new_from_filename(str(path))]
        monkeypatch.setattr(app_index.Gio.AppInfo, 'get_all', lambda: more)
        monkeypatch.setattr(window, '_sync_home_labels', lambda *a: None)
        populated = []
        shell = types.SimpleNamespace(
            app_index=index, _app_refresh_timer=1,
            config=types.SimpleNamespace(),
            _home_launcher=types.SimpleNamespace(refresh=lambda *a, **k: None),
            apps_search=types.SimpleNamespace(get_text=lambda: ''),
            _populate_apps=lambda _q='': populated.append(1))
        window.ShellWindow._reload_app_index(shell)
        assert populated == [1]

    def test_monitor_burst_waits_for_it_to_settle(self):
        import window
        assert window._APP_RESCAN_DELAY_MS >= 1000
