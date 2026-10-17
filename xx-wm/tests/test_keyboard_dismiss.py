"""Tests for the 20.1 tap-outside OSK dismissal.

The real Gtk event wiring -- a GestureClick on the shell stack that hides
squeekboard when a tap lands on a non-editable widget -- needs a live Wayland
session and is device-gated. The headless seam it drives is the D-Bus call
``_set_osk_visible(visible)``, which both ``_show_keyboard()`` and
``_hide_keyboard()`` route through. That seam is exercised here by stubbing the
gi bindings before importing window.py, then asserting the SetVisible payload
sent to squeekboard.
"""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


@pytest.fixture(scope='module')
def window():
    """Import window.py once against a stable set of fake gi bindings.

    window.py imports GTK at module level, so it cannot load in this headless
    environment. We install fake gi modules into sys.modules first; window's
    module-global Gtk/GLib/Gio then refer to these fakes for the whole module.
    """
    def require_version(namespace, version):
        # window.py probes Gtk4LayerShell and treats ValueError as 'absent';
        # the other GTK namespaces must import cleanly.
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
    # ShellWindow subclasses Adw.ApplicationWindow at class-definition time.
    adw.ApplicationWindow = type('ApplicationWindow', (), {})
    # _on_tap_outside checks `isinstance(widget, Gtk.Editable)`.
    gtk.Editable = type('Editable', (), {})
    gtk.PickFlags = types.SimpleNamespace(DEFAULT=0)

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
        window._TEST_GTK = gtk
        window._TEST_GIO = gio
        window._TEST_GLIB = glib
        yield window
    finally:
        # Restore the previous modules so other test files are unaffected.
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
        # Don't leak the fake-bound window module either (test_folders
        # idiom): a later `import window` must rebind against real gi.
        sys.modules.pop('window', None)


def _install_dbus(window):
    """Give the fake Gio/GLib the attributes _set_osk_visible needs, and
    return a recorder that captures the SetVisible payloads."""
    gio = window._TEST_GIO
    glib = window._TEST_GLIB
    calls = []

    class FakeBus:
        def call(self, *args):
            calls.append(args)

    gio.BusType = types.SimpleNamespace(SESSION='session')
    gio.DBusCallFlags = types.SimpleNamespace(NONE=0)
    gio.bus_get_sync = lambda *a, **k: FakeBus()

    def variant(sig, value):
        return types.SimpleNamespace(signature=sig, value=value)

    glib.Variant = variant
    return calls


def test_hide_keyboard_sends_setvisible_false(window):
    """_set_osk_visible(False) -- the seam _hide_keyboard() drives -- tells
    squeekboard to disappear."""
    calls = _install_dbus(window)
    window._set_osk_visible(False)

    assert len(calls) == 1
    name, path, iface, method, params = calls[0][:5]
    assert (name, path, iface, method) == (
        'sm.puri.OSK0', '/sm/puri/OSK0', 'sm.puri.OSK0', 'SetVisible')
    assert params.value == (False,)


def test_show_keyboard_sends_setvisible_true(window):
    """_set_osk_visible(True) -- the seam _show_keyboard() drives -- still
    raises squeekboard after the 20.1 refactor."""
    calls = _install_dbus(window)
    window._set_osk_visible(True)

    assert len(calls) == 1
    params = calls[0][4]
    assert params.value == (True,)


def _shell_for_tap(window, stack, hide_calls_via_dbus=True):
    """Bare ShellWindow stand-in with the tap-outside helpers bound."""
    shell = types.SimpleNamespace(
        stack=stack,
        apps_search=None,
        _hide_keyboard=(
            (lambda: window._set_osk_visible(False)) if hide_calls_via_dbus
            else lambda: None),
        _set_layer_keyboard_exclusive=lambda exclusive: None,
    )
    shell._widget_is_search_or_editable = (
        lambda widget: window.ShellWindow._widget_is_search_or_editable(
            shell, widget))
    return shell


def test_tap_on_editable_keeps_keyboard(window):
    """A tap that lands on an editable widget (the drawer search entry) must
    not dismiss the OSK."""
    editable = window._TEST_GTK.Editable
    calls = _install_dbus(window)

    class FakeStack:
        def pick(self, x, y, flags):
            # The search entry is an editable widget.
            return editable()

    shell = _shell_for_tap(window, FakeStack())
    window.ShellWindow._on_tap_outside(shell, None, 1, 0, 0)
    assert calls == []


def test_tap_on_child_of_editable_keeps_keyboard(window):
    """A tap on SearchEntry chrome (icon, padding) still counts as the entry."""
    editable = window._TEST_GTK.Editable
    parent = editable()
    child = types.SimpleNamespace(get_parent=lambda: parent)
    calls = _install_dbus(window)

    class FakeStack:
        def pick(self, x, y, flags):
            return child

    shell = _shell_for_tap(window, FakeStack())
    window.ShellWindow._on_tap_outside(shell, None, 1, 0, 0)
    assert calls == []


def test_tap_on_search_entry_identity_keeps_keyboard(window):
    """A tap on SearchEntry chrome that is not Gtk.Editable still counts."""
    search = object()
    icon = types.SimpleNamespace(get_parent=lambda: search)
    calls = _install_dbus(window)

    class FakeStack:
        def pick(self, x, y, flags):
            return icon

    shell = _shell_for_tap(window, FakeStack())
    shell.apps_search = search
    window.ShellWindow._on_tap_outside(shell, None, 1, 0, 0)
    assert calls == []


def test_shell_window_requests_keyboard_on_demand():
    src = Path(__file__).parent.parent.joinpath(
        'launcher', 'src', 'window.py').read_text(encoding='utf-8')
    assert 'KeyboardMode.EXCLUSIVE' in src
    assert 'KeyboardMode.NONE' in src
    assert "notify::has-focus" in src
    assert 'EventControllerFocus' in src
    assert 'contains_focus' in src


def test_search_focus_shows_when_inner_text_has_focus(window):
    """GTK4 SearchEntry.has_focus() is often False; contains_focus() is not."""
    calls = _install_dbus(window)
    entry = types.SimpleNamespace(
        has_focus=lambda: False, contains_focus=lambda: True)
    armed = []
    shell = types.SimpleNamespace(
        _arm_search_keyboard=lambda: (
            armed.append(True), window._set_osk_visible(True)),
        _disarm_search_keyboard=lambda: armed.append(False),
    )
    window.ShellWindow._on_search_focus(shell, entry)
    assert armed == [True]
    assert calls[0][4].value == (True,)


def test_search_focus_false_does_not_hide(window):
    """A False has-focus notify must not drop the OSK; tap-outside does that."""
    calls = _install_dbus(window)
    entry = types.SimpleNamespace(
        has_focus=lambda: False, contains_focus=lambda: False)
    shell = types.SimpleNamespace(
        _arm_search_keyboard=lambda: window._set_osk_visible(True),
        _disarm_search_keyboard=lambda: window._set_osk_visible(False),
    )
    window.ShellWindow._on_search_focus(shell, entry)
    assert calls == []


def test_tap_outside_editable_hides_keyboard(window):
    """A tap that lands on a non-editable widget hides the OSK (20.1)."""
    calls = _install_dbus(window)

    class FakeStack:
        def pick(self, x, y, flags):
            # Home launcher / app list / settings are not editable.
            return object()

    shell = _shell_for_tap(window, FakeStack())
    window.ShellWindow._on_tap_outside(shell, None, 1, 0, 0)
    import time as _time
    _deadline = _time.monotonic() + 5
    while not calls and _time.monotonic() < _deadline:
        _time.sleep(0.01)
    assert len(calls) == 1
    name, path, iface, method, params = calls[0][:5]
    assert (name, path, iface, method) == (
        'sm.puri.OSK0', '/sm/puri/OSK0', 'sm.puri.OSK0', 'SetVisible')
    assert params.value == (False,)