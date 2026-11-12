"""Lane vx_shell_core: single-source themes, hidden apps search-only, home alignment.

Headless: config/home_page/drawer_page import without a display; widget
construction is monkeypatched out, only the population seams run.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


class TestConfigSingleSource:
    def test_theme_names_are_xx_ui_objects(self):
        import config
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'common'))
        from xx_ui import theme
        assert config.ThemePreset is theme.ThemePreset
        assert config.THEME_PRESETS is theme.THEME_PRESETS
        assert config.FONT_FAMILIES is theme.FONT_FAMILIES
        assert config._derive_shades is theme._derive_shades
        assert len(config.THEME_PRESETS) == 8

    def test_custom_resolution_matches_xx_ui(self):
        import config
        from xx_ui import theme
        assert (config._derive_shades('#3a2a10')
                == theme._derive_shades('#3a2a10'))


class _FakeIndex:
    def __init__(self, app_ids):
        class _E:
            pass
        self.entries = []
        for app_id in app_ids:
            entry = _E()
            entry.app_id = app_id
            entry.name = app_id
            self.entries.append(entry)

    def search(self, query):
        return list(self.entries)


class _RecordingList:
    def __init__(self):
        self.inserted = []
        self.appended = []

    def insert(self, row, _pos):
        self.inserted.append(row)

    def append(self, row):
        self.appended.append(row)

    def set_valign(self, _align):
        pass


def _drawer_page(hidden, installed, monkeypatch):
    import drawer_page
    page = drawer_page.DrawerPage.__new__(drawer_page.DrawerPage)
    win = types.SimpleNamespace(
        config=types.SimpleNamespace(
            home_slots=[], app_labels={}, hidden_apps=list(hidden),
            pinned=[]),
        app_index=_FakeIndex(installed),
        apps_list=_RecordingList(),
        apps_search=types.SimpleNamespace(get_text=lambda: ''),
        app_count_label=types.SimpleNamespace(set_text=lambda _t: None),
        _sort_mode='az',
        _drawer_open_folder=None,
        _home_slot_app_ids=lambda: set(),
    )
    page._win = win
    # ShellWindow delegators resolve on win at call time, so the
    # monkeypatched page seams below take effect through them.
    win._drawer_folder_slots = lambda: page._drawer_folder_slots()
    win._make_drawer_folder_row = lambda s, i: page._make_drawer_folder_row(s, i)
    win._make_drawer_member_row = (
        lambda a, b, m: page._make_drawer_member_row(a, b, m))
    win._make_app_row = lambda e: page._make_app_row(e)
    win._make_settings_row = lambda: page._make_settings_row()
    win._install_time = lambda e: page._install_time(e)
    win._replace_rows = (
        lambda lb, entries, msg, maker=None: page._replace_rows(
            lb, entries, msg, maker))
    monkeypatch.setattr(page, '_replace_rows', lambda *a, **k: None)
    monkeypatch.setattr(page, '_make_settings_row',
                        lambda: types.SimpleNamespace(kind='settings'))
    return page, win


class TestHiddenAppsAreSearchOnly:
    """design.md: hidden apps are absent from the browse list and surface
    only in search; there is no "Hidden (n)" group."""

    def test_browse_list_has_no_hidden_rows_or_group(self, monkeypatch):
        page, win = _drawer_page(['h.desktop'], ['h.desktop', 'v.desktop'],
                                 monkeypatch)
        rows = []
        monkeypatch.setattr(page, '_make_app_row',
                            lambda e: rows.append(e.app_id) or ('row', e.app_id))
        page._populate_apps('')
        assert 'h.desktop' not in rows
        assert win.apps_list.inserted == []
        assert not hasattr(page, '_make_hidden_header')
        assert not hasattr(page, '_toggle_hidden')


class _FakeAligned:
    def __init__(self):
        self.xalign = None

    def set_xalign(self, value):
        self.xalign = value


class TestHomeAlignment:
    def _page(self, alignment):
        import home_page
        page = home_page.HomePage.__new__(home_page.HomePage)
        labels = {key: _FakeAligned()
                  for key in ('clock', 'date', 'weather', 'status')}
        win = types.SimpleNamespace(
            config=types.SimpleNamespace(home_alignment=alignment),
            clock_label=labels['clock'],
            date_label=labels['date'],
            weather_label=labels['weather'],
            status_strip=labels['status'],
        )
        page._win = win
        return page, labels

    def test_left_pref_left_aligns_widgets(self):
        page, labels = self._page('left')
        page._apply_widget_alignment()
        assert {label.xalign for label in labels.values()} == {0.0}

    def test_right_pref_right_aligns_widgets(self):
        page, labels = self._page('right')
        page._apply_widget_alignment()
        assert {label.xalign for label in labels.values()} == {1.0}

    def test_center_and_unknown_stay_centered(self):
        for alignment in ('center', 'diagonal'):
            page, labels = self._page(alignment)
            page._apply_widget_alignment()
            assert {label.xalign for label in labels.values()} == {0.5}


class TestNoConfirmationToasts:
    """Minimal rule: a direct action shows its result, not a toast."""

    def test_web_search_launches_without_a_status_line(self, monkeypatch):
        import drawer_page
        from gi.repository import Gio
        launched, statuses = [], []
        monkeypatch.setattr(Gio.AppInfo, 'launch_default_for_uri',
                            lambda uri, _ctx: launched.append(uri))
        page = drawer_page.DrawerPage.__new__(drawer_page.DrawerPage)
        page._win = types.SimpleNamespace(_show_status=statuses.append)
        page._open_web_search('river wm')
        assert launched and launched[0].endswith('river+wm')
        assert statuses == []

    def test_no_index_rebuilt_toast(self):
        src = Path(__file__).parent.parent / 'launcher' / 'src' / 'window.py'
        assert 'Application index rebuilt' not in src.read_text(
            encoding='utf-8')
