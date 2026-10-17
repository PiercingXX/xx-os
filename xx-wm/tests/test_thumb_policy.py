"""Thumbnail capture rule (wm_apptheme lane).

A screencopy every 2.5 s for the focused app burned battery on the phone.
Thumbnails are only read by the app switcher, so captures happen on demand:
once shortly after a fresh app launch, and right before the switcher opens
(the focused app, about to leave the foreground). Captures for the same app
within a minute are skipped; the stored thumb is still fresh. Both
per-capture log lines are debug, never info.
"""
import inspect
import logging
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

WINDOW_PY = Path(__file__).parent.parent / 'launcher' / 'src' / 'window.py'

_clock = [1000.0]


def _install_fake_gi() -> None:
    glib = types.ModuleType('gi.repository.GLib')
    glib.SOURCE_REMOVE = False
    glib.SOURCE_CONTINUE = True
    glib.Error = type('Error', (Exception,), {})
    glib.idle_add = lambda *a, **k: 1
    glib.timeout_add = lambda *a, **k: 1
    glib.timeout_add_seconds = lambda *a, **k: 1
    glib.source_remove = lambda *a, **k: None
    glib.get_monotonic_time = lambda: int(_clock[0] * 1_000_000)

    gdk = types.ModuleType('gi.repository.Gdk')
    gdk.Display = types.SimpleNamespace(get_default=lambda: None)

    gio = types.ModuleType('gi.repository.Gio')

    gtk = types.ModuleType('gi.repository.Gtk')
    gtk.Window = type('Window', (), {})
    gtk.Box = type('Box', (), {})
    gtk.Editable = type('Editable', (), {})
    gtk.PickFlags = types.SimpleNamespace(DEFAULT=0)

    adw = types.ModuleType('gi.repository.Adw')
    adw.ApplicationWindow = type('ApplicationWindow', (), {})
    adw.StyleManager = types.SimpleNamespace(
        get_default=lambda: types.SimpleNamespace(set_color_scheme=lambda s: None))
    adw.ColorScheme = types.SimpleNamespace(FORCE_DARK=1, FORCE_LIGHT=2)

    pango = types.ModuleType('gi.repository.Pango')

    def _require_version(namespace, version):
        if namespace == 'Gtk4LayerShell':
            raise ValueError(f'{namespace} not available')

    gi_mod = types.ModuleType('gi')
    gi_rep = types.ModuleType('gi.repository')
    gi_mod.require_version = _require_version
    gi_mod.repository = gi_rep
    sys.modules['gi'] = gi_mod
    sys.modules['gi.repository'] = gi_rep
    for name, mod in (('GLib', glib), ('Gdk', gdk), ('Gio', gio),
                      ('Gtk', gtk), ('Adw', adw), ('Pango', pango)):
        sys.modules[f'gi.repository.{name}'] = mod


_install_fake_gi()

import window  # noqa: E402  (needs fake gi modules installed first)

sys.modules.pop('window', None)


class FakeManager:
    def __init__(self, app_id) -> None:
        self._app_id = app_id

    def activated_app_id(self):
        return self._app_id


class FakeCapture:
    def __init__(self) -> None:
        self.callbacks: list[object] = []
        self.calls = 0

    def capture(self, callback) -> bool:
        self.calls += 1
        self.callbacks.append(callback)
        return True


class FakeStore:
    def __init__(self) -> None:
        self.puts: dict[str, object] = {}

    def put(self, app_id, thumb) -> None:
        self.puts[app_id] = thumb


def _stub_window(app_id='org.gnome.TextEditor'):
    win = object.__new__(window.ShellWindow)
    win._blanked = False
    win._call_live = lambda: False
    win._layer_is_top = False
    win._switcher = None
    win._shade = None
    win._lock_screen = None
    win._power_menu = None
    win._toplevel_manager = FakeManager(app_id)
    win._thumbs = FakeStore()
    win._output_capture = FakeCapture()
    win._thumb_captured_at = {}
    return win


def _maybe(win):
    window.ShellWindow._maybe_capture_thumb(win)


def test_no_periodic_thumb_timer():
    """The 2.5 s polling tick is gone from the shell."""
    src = WINDOW_PY.read_text(encoding='utf-8')
    assert '_tick_thumb_capture' not in src
    assert 'timeout_add(2500' not in src


def test_switcher_opens_capture_on_demand():
    """_show_switcher captures the focused app while still visible."""
    src = inspect.getsource(window.ShellWindow._show_switcher)
    assert '_maybe_capture_thumb' in src


def test_fresh_launch_still_captures_once():
    """drop_to_background keeps its single delayed capture for new apps."""
    src = inspect.getsource(window.ShellWindow.drop_to_background)
    assert '_capture_thumb_once' in src


def test_per_capture_logs_are_debug_not_info():
    import re
    src = WINDOW_PY.read_text(encoding='utf-8')
    levels = {which: level for level, which in
              re.findall(r"\.(info|debug)\(\s*'(capturing thumb|thumb stored)", src)}
    assert levels.get('capturing thumb') == 'debug', levels
    assert levels.get('thumb stored') == 'debug', levels


def test_focused_app_is_captured():
    win = _stub_window()
    _maybe(win)
    assert win._output_capture.calls == 1
    assert win._thumb_captured_at.get('org.gnome.TextEditor') == 1000.0


def test_repeat_capture_throttled_within_a_minute():
    win = _stub_window()
    _maybe(win)
    _clock[0] += 30.0
    _maybe(win)
    assert win._output_capture.calls == 1
    _clock[0] += 31.0
    _maybe(win)
    assert win._output_capture.calls == 2


def test_other_app_captures_despite_throttle():
    win = _stub_window()
    _maybe(win)
    win._toplevel_manager = FakeManager('org.mozilla.firefox')
    _maybe(win)
    assert win._output_capture.calls == 2


def test_no_app_id_no_capture():
    win = _stub_window(app_id=None)
    _maybe(win)
    assert win._output_capture.calls == 0


def test_overlay_on_top_blocks_capture():
    win = _stub_window()
    win._blanked = True
    _maybe(win)
    assert win._output_capture.calls == 0


def test_capture_callback_stores_thumb(caplog):
    win = _stub_window()
    with caplog.at_level(logging.DEBUG, logger='piercing.window'):
        _maybe(win)
        thumb = types.SimpleNamespace(width=108, height=240)
        win._output_capture.callbacks[0](thumb)
    assert win._thumbs.puts['org.gnome.TextEditor'] is thumb
    noisy = [r for r in caplog.records
             if r.levelno >= logging.INFO and 'thumb' in r.getMessage()]
    assert noisy == []
    assert any('capturing thumb for org.gnome.TextEditor' in r.getMessage()
               for r in caplog.records)
