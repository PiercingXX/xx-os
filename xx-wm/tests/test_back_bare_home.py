"""Back with nothing shell-owned open goes to the focused app, only to it.

On bare home the back gesture used to send Escape and Android BACK to the
app hidden under the shell, and spawned the waydroid CLI on every back
swipe whether or not an Android app was focused.
"""
import subprocess
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


class _Manager:
    def __init__(self, app_id, available=True):
        self._app_id = app_id
        self.available = available

    def activated_app_id(self):
        return self._app_id


def _shell(over_apps, manager):
    return types.SimpleNamespace(
        _sheet_host=None, _home_launcher=None,
        _switcher=None, _shade=None, _dialer=None,
        stack=types.SimpleNamespace(get_visible_child_name=lambda: 'home'),
        _over_apps=over_apps, _toplevel_manager=manager,
    )


@pytest.fixture
def spawned(monkeypatch):
    calls: list = []
    monkeypatch.setattr(subprocess, 'Popen',
                        lambda argv, **_k: calls.append(argv[0]))
    return calls


def _back(shell):
    import window
    window.ShellWindow._handle_back(shell)


def test_bare_home_over_an_app_sends_nothing(spawned):
    _back(_shell(True, _Manager('org.gnome.Calculator')))
    assert spawned == []


def test_bare_home_with_no_app_focused_sends_nothing(spawned):
    _back(_shell(False, _Manager(None)))
    assert spawned == []


def test_focused_linux_app_gets_escape_only(spawned):
    _back(_shell(False, _Manager('org.gnome.Calculator')))
    assert spawned == ['wtype']


def test_focused_waydroid_app_gets_android_back_only(spawned):
    _back(_shell(False, _Manager('waydroid.com.android.settings')))
    assert spawned == ['waydroid']


def test_unknown_focus_falls_back_to_escape_never_waydroid(spawned):
    _back(_shell(False, None))
    _back(_shell(False, _Manager('x', available=False)))
    assert spawned == ['wtype', 'wtype']
