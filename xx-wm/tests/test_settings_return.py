"""Leaving Settings returns where it was opened from.

home -> drawer -> Settings -> back used to drop the shell to the BOTTOM
layer and show the app underneath instead of the drawer. Settings opened
over an app (the shade's Settings button) still drops back to that app.
"""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


@pytest.fixture(autouse=True)
def _transition_enum(monkeypatch):
    """Some suites leave a stub Gtk bound in window; the leave path only
    needs the transition enum."""
    import window
    if not hasattr(window.Gtk, 'StackTransitionType'):
        monkeypatch.setattr(
            window.Gtk, 'StackTransitionType',
            types.SimpleNamespace(SLIDE_RIGHT=0, SLIDE_LEFT=1), raising=False)


class _Stack:
    def __init__(self, page):
        self.page = page

    def get_visible_child_name(self):
        return self.page

    def set_visible_child_name(self, name):
        self.page = name

    def set_transition_type(self, _t):
        return None


class _Manager:
    def __init__(self, app_id):
        self.app_id = app_id

    def activated_app_id(self):
        return self.app_id


def _shell(page, over_apps, focused):
    events: list = []
    shell = types.SimpleNamespace(
        stack=_Stack(page),
        _over_apps=over_apps,
        _toplevel_manager=_Manager(focused),
        _raised_for_settings=False,
        _settings_stack=None,
        events=events,
    )
    shell._disarm_search_keyboard = lambda: None
    shell.present_over_apps = lambda: (events.append('top'),
                                       setattr(shell, '_over_apps', True))
    shell.drop_to_background = lambda: (events.append('drop'),
                                        setattr(shell, '_over_apps', False))
    return shell


def _round_trip(shell):
    import window
    window.ShellWindow._open_settings(shell)
    assert shell.stack.page == 'settings'
    window.ShellWindow._leave_settings(shell)


def test_drawer_over_an_app_returns_to_the_drawer():
    shell = _shell('apps', over_apps=True, focused='org.gnome.Calculator')
    _round_trip(shell)
    assert shell.stack.page == 'apps'
    assert 'drop' not in shell.events
    assert shell._over_apps is True


def test_home_over_an_app_returns_home_still_raised():
    shell = _shell('home', over_apps=True, focused='org.gnome.Calculator')
    _round_trip(shell)
    assert shell.stack.page == 'home'
    assert 'drop' not in shell.events


def test_drawer_with_no_app_open_returns_to_the_drawer():
    shell = _shell('apps', over_apps=False, focused=None)
    _round_trip(shell)
    assert shell.stack.page == 'apps'


def test_opened_over_an_app_drops_back_to_the_app():
    # The shade's Settings button over a focused app: the shell page under
    # the app is irrelevant; leaving shows the app again.
    shell = _shell('apps', over_apps=False, focused='org.gnome.Calculator')
    _round_trip(shell)
    assert shell.stack.page == 'home'
    assert shell.events[-1] == 'drop'
    assert shell._over_apps is False


def test_reopening_from_settings_keeps_the_first_origin():
    import window
    shell = _shell('apps', over_apps=True, focused='x')
    window.ShellWindow._open_settings(shell)
    shell._scroll_settings_to = lambda _s: None
    window.ShellWindow._open_settings(shell, 'wifi')
    window.ShellWindow._leave_settings(shell)
    assert shell.stack.page == 'apps'
