"""Tests for the switcher's text-only rows and empty state.

The switcher is a Gtk.Window, so it cannot be instantiated in a headless
environment. The row and empty-state contracts are exercised through the
static headless seams the switcher itself renders from -- the same pattern
the wiring tests use for refresh/focus/close. A fake manager drives the app
list so the row names and the empty-state message are verified against real
manager state without a display.

Rows show the app's display name first (resolved from the app_id, never the
raw window title) with the window title as a second line only when it adds
information.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import app_switcher
from app_switcher import _EMPTY_STATE_TEXT, AppInfo
from toplevel_manager import Toplevel


class FakeManager:
    """Duck-typed stand-in for ToplevelManager used by the switcher."""

    def __init__(self, apps: list[Toplevel]) -> None:
        self._apps = apps
        self.available = True

    def list(self) -> list[Toplevel]:
        return list(self._apps)

    def on_change(self, callback) -> None:
        pass

    def activate(self, handle: object) -> None:
        pass

    def close(self, handle: object) -> None:
        pass


class FakeDesktopInfo:
    """Duck-typed stand-in for Gio.DesktopAppInfo."""

    def __init__(self, desktop_id: str = '', display: str = '',
                 name: str = '', wm_class: str = '') -> None:
        self._desktop_id = desktop_id
        self._display = display
        self._name = name
        self._wm_class = wm_class

    def get_id(self):
        return self._desktop_id

    def get_display_name(self):
        return self._display

    def get_name(self):
        return self._name

    def get_startup_wm_class(self):
        return self._wm_class


@pytest.fixture(autouse=True)
def _fresh_name_cache():
    app_switcher._APP_NAME_CACHE.clear()
    try:
        yield
    finally:
        app_switcher._APP_NAME_CACHE.clear()


def test_empty_state_shows_no_line() -> None:
    """No open apps shows nothing (owner: no empty-state lines)."""
    assert _EMPTY_STATE_TEXT == ''


def test_empty_state_used_when_no_apps() -> None:
    """With no apps the switcher shows the empty-state message, not a row."""
    from app_switcher import AppSwitcher

    apps = AppSwitcher._apps_from_manager(FakeManager([]))
    assert apps == []
    assert AppSwitcher._empty_state_label() == _EMPTY_STATE_TEXT


def test_empty_state_ignores_appless_manager() -> None:
    """A manager that reports nothing yields the empty state, never a crash."""
    from app_switcher import AppSwitcher

    apps = AppSwitcher._apps_from_manager(FakeManager([]))
    # The empty-state path is chosen precisely when there are no rows.
    assert len(apps) == 0
    assert AppSwitcher._empty_state_label() == _EMPTY_STATE_TEXT


def test_single_app_still_renders_a_row() -> None:
    """One open app produces exactly one row label, not the empty state."""
    from app_switcher import AppSwitcher

    apps = [AppInfo('org.a.App', 'Solo', handle='h1')]
    assert len(AppSwitcher._card_labels(apps)) == 1
    assert len(apps) > 0


def test_name_resolves_exact_desktop_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """`<app_id>.desktop` is tried first for the display name."""
    seen: list[str] = []

    def _lookup(desktop_id: str):
        seen.append(desktop_id)
        return FakeDesktopInfo(desktop_id, display='Firefox')

    monkeypatch.setattr(app_switcher, '_desktop_info_for', _lookup)
    assert app_switcher.resolve_app_name('org.mozilla.firefox') == 'Firefox'
    assert seen == ['org.mozilla.firefox.desktop']


def test_name_passes_through_desktop_suffixed_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def _lookup(desktop_id: str):
        seen.append(desktop_id)
        return FakeDesktopInfo(desktop_id, display='Files')

    monkeypatch.setattr(app_switcher, '_desktop_info_for', _lookup)
    assert app_switcher.resolve_app_name('org.gnome.Nautilus.desktop') == 'Files'
    assert seen == ['org.gnome.Nautilus.desktop']


def test_name_falls_back_to_last_component(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown apps still get a short first line: the id's last component
    (shared with the settings surfaces), never the raw app_id."""
    monkeypatch.setattr(app_switcher, '_desktop_info_for', lambda _id: None)
    monkeypatch.setattr(app_switcher, '_iter_desktop_infos', lambda: [])
    assert app_switcher.resolve_app_name('com.example.Missing') == 'Missing'
    assert app_switcher.resolve_app_name('io.piercingxx.TxxT') == 'TxxT'


def test_fallback_matches_settings_surfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The switcher and the battery/screen-time surfaces share one
    fallback rule, so an unknown id reads the same everywhere."""
    import app_names

    monkeypatch.setattr(app_switcher, '_desktop_info_for', lambda _id: None)
    monkeypatch.setattr(app_switcher, '_iter_desktop_infos', lambda: [])
    for app_id in ('io.piercingxx.TxxT', 'org.example.App.desktop',
                   'com.example.Missing'):
        assert app_switcher.resolve_app_name(app_id) == \
            app_names.fallback_app_name(app_id)


def test_name_matches_startup_wm_class_case_insensitively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_switcher, '_desktop_info_for', lambda _id: None)
    monkeypatch.setattr(
        app_switcher, '_iter_desktop_infos',
        lambda: [FakeDesktopInfo('org.mozilla.firefox.desktop',
                                 display='Firefox Web Browser',
                                 wm_class='Firefox')],
    )
    assert app_switcher.resolve_app_name('firefox') == 'Firefox Web Browser'


def test_name_matches_desktop_basename_case_insensitively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_switcher, '_desktop_info_for', lambda _id: None)
    monkeypatch.setattr(
        app_switcher, '_iter_desktop_infos',
        lambda: [FakeDesktopInfo('Org.Gnome.Nautilus.desktop', display='Files')],
    )
    assert app_switcher.resolve_app_name('org.gnome.nautilus') == 'Files'


def test_name_prefers_display_name_over_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        app_switcher, '_desktop_info_for',
        lambda _id: FakeDesktopInfo(_id, display='', name='Fallback Name'),
    )
    assert app_switcher.resolve_app_name('org.a.App') == 'Fallback Name'


def test_name_resolution_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _lookup(desktop_id: str):
        calls.append(desktop_id)
        return FakeDesktopInfo(desktop_id, display='Cached')

    monkeypatch.setattr(app_switcher, '_desktop_info_for', _lookup)
    assert app_switcher.resolve_app_name('org.a.App') == 'Cached'
    assert app_switcher.resolve_app_name('org.a.App') == 'Cached'
    assert calls == ['org.a.App.desktop']


def test_rows_show_names_not_titles(monkeypatch: pytest.MonkeyPatch) -> None:
    """Row first lines are resolved app names, never raw window titles."""
    from app_switcher import AppSwitcher

    monkeypatch.setattr(app_switcher, 'resolve_app_name',
                        lambda app_id: {'org.a.App': 'Alpha App',
                                        'org.b.App': 'Beta App'}[app_id])
    apps = [
        AppInfo('org.a.App', 'Some Window Title', handle='h1'),
        AppInfo('org.b.App', 'Another Title', handle='h2'),
    ]
    assert AppSwitcher._card_labels(apps) == ['Alpha App', 'Beta App']


def test_subtitle_shows_title_when_it_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app_switcher import AppSwitcher

    monkeypatch.setattr(app_switcher, 'resolve_app_name', lambda _id: 'Alpha App')
    app = AppInfo('org.a.App', 'Inbox — Mail', handle='h1')
    assert AppSwitcher._card_name(app) == 'Alpha App'
    assert AppSwitcher._card_subtitle(app) == 'Inbox — Mail'


def test_subtitle_empty_when_title_matches_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app_switcher import AppSwitcher

    monkeypatch.setattr(app_switcher, 'resolve_app_name', lambda _id: 'Alpha App')
    assert AppSwitcher._card_subtitle(AppInfo('org.a.App', 'Alpha App')) == ''


def test_subtitle_empty_when_title_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app_switcher import AppSwitcher

    monkeypatch.setattr(app_switcher, 'resolve_app_name', lambda _id: 'Alpha App')
    assert AppSwitcher._card_subtitle(AppInfo('org.a.App', '')) == ''
    assert AppSwitcher._card_subtitle(AppInfo('org.a.App', '   ')) == ''


def test_card_labels_from_manager_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """Row labels are derived from the manager's list in order."""
    from app_switcher import AppSwitcher

    monkeypatch.setattr(app_switcher, 'resolve_app_name',
                        lambda app_id: {'org.a.App': 'Alpha App',
                                        'org.b.App': 'Beta App'}[app_id])
    fake = FakeManager([
        Toplevel('org.a.App', 'Alpha', 'h1'),
        Toplevel('org.b.App', 'Beta', 'h2'),
    ])
    apps = AppSwitcher._apps_from_manager(fake)
    assert AppSwitcher._card_labels(apps) == ['Alpha App', 'Beta App']


def test_card_labels_preserve_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rows keep the manager's insertion order, so the switcher is stable."""
    from app_switcher import AppSwitcher

    monkeypatch.setattr(app_switcher, 'resolve_app_name', lambda app_id: app_id)
    apps = [
        AppInfo('org.a.App', 'First', handle='h1'),
        AppInfo('org.b.App', 'Second', handle='h2'),
        AppInfo('org.c.App', 'Third', handle='h3'),
    ]
    assert AppSwitcher._card_labels(apps) == ['org.a.App', 'org.b.App', 'org.c.App']


def test_card_pixel_size_is_pixel_fraction() -> None:
    from app_switcher import AppSwitcher

    assert AppSwitcher._card_pixel_size(800, 1280) == (656, 870)
    assert AppSwitcher._card_pixel_size(0, 0) == (280, 420)


def test_single_app_still_renders_a_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One open app produces exactly one card label, not the empty state."""
    from app_switcher import AppSwitcher

    monkeypatch.setattr(app_switcher, 'resolve_app_name', lambda _id: 'Solo')
    apps = [AppInfo('org.a.App', 'Solo', handle='h1')]
    assert AppSwitcher._card_labels(apps) == ['Solo']
    # A non-empty list must not be mistaken for the empty state.
    assert len(apps) > 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
