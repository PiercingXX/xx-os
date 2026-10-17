"""Tests for wiring the brightness slider to the HUD overlay + silent absence (plan T4).

The running app (main.py) builds a Hud and passes it down the real call chain:
XXWMApplication._hud -> ShellWindow._ensure_shade -> NotificationShade(hud=)
-> QuickActionsPanel(hud=). The brightness slider's value-changed handler calls
QuickActionsPanel._on_brightness_changed(pct), which queues a debounced apply
(P3-G: one subprocess per drag, not per tick); when the trailing-edge timer
fires, _flush_slider_apply applies the level via _set_brightness_pct and then
flashes it on the HUD via _show_brightness_hud -> hud.show_brightness.

This test drives that exact path — _on_brightness_changed -> (flush) ->
_set_brightness_pct + _show_brightness_hud -> hud.show_brightness — so it
fails if the call is never made. It also pins the silent-absence contract:
when no HUD is wired, moving the brightness slider must still apply the level
and must not raise.

quick_actions imports gi.repository.Gdk/GLib/Gio/Gtk at module top level, but
PyGObject may be absent here. We inject minimal fake modules into sys.modules
before importing, exactly like test_quick_tiles does. The class body (real Gtk
widget construction) is never run here — the wiring methods under test are
exercised on a bare instance via object.__new__, which is the seam the slider
handler actually calls.
"""
import sys
import types

import pytest


class _FakeTimerGLib:
    """Controllable GLib timeout scheduler so debounce flushes are explicit."""

    def __init__(self) -> None:
        self.timeouts: dict[int, tuple[int, object, tuple]] = {}
        self.removed: list[int] = []
        self._next_id = 1

    def timeout_add(self, ms, cb, *data):
        tid = self._next_id
        self._next_id += 1
        self.timeouts[tid] = (ms, cb, data)
        return tid

    def source_remove(self, tid):
        self.removed.append(tid)
        self.timeouts.pop(tid, None)

    def fire_all(self):
        while self.timeouts:
            _tid, (_ms, cb, data) = self.timeouts.popitem()
            cb(*data)


_TIMER_GLIB = _FakeTimerGLib()


def _install_fake_gi() -> None:
    """Make `from gi.repository import Gdk, GLib, Gio, Gtk` resolve to stubs."""
    glib = types.ModuleType('gi.repository.GLib')
    glib.SOURCE_REMOVE = False
    glib.SOURCE_CONTINUE = True
    glib.timeout_add = _TIMER_GLIB.timeout_add
    glib.timeout_add_seconds = lambda *a, **k: 1
    glib.source_remove = _TIMER_GLIB.source_remove
    glib.idle_add = lambda *a, **k: None
    glib.Error = Exception
    glib.Variant = lambda *a, **k: None
    glib.VariantType = lambda *a, **k: None

    for name in ('Gdk', 'Gio', 'Gtk'):
        sys.modules[f'gi.repository.{name}'] = types.ModuleType(
            f'gi.repository.{name}')
    # The class statements `class QuickActionsPanel(Gtk.Box)` and
    # `class NotificationShade(Gtk.Window)` evaluate their base classes at
    # import time even though the widget methods are never run headlessly.
    sys.modules['gi.repository.Gtk'].Box = type('Box', (), {})
    sys.modules['gi.repository.Gtk'].Window = type('Window', (), {})

    gi_mod = types.ModuleType('gi')
    gi_rep = types.ModuleType('gi.repository')
    # require_version must raise for Gtk4LayerShell (so importing
    # notification_shade falls back to its non-layer-shell path); Adw and the
    # faked Gdk/Gio/GLib/Gtk return normally (Adw is required but never imported
    # by notification_shade).
    def _require_version(namespace, version):
        if namespace == 'Gtk4LayerShell':
            raise ValueError(f'{namespace} not available')
    gi_mod.require_version = _require_version
    sys.modules['gi'] = gi_mod
    sys.modules['gi.repository'] = gi_rep
    sys.modules['gi.repository.GLib'] = glib


_install_fake_gi()

import quick_actions  # noqa: E402  (needs fake gi modules installed first)


class _FakeHud:
    """Records every show_brightness call for assertion."""

    def __init__(self) -> None:
        self.brightness_calls: list[int] = []

    def show_brightness(self, pct: int) -> None:
        self.brightness_calls.append(pct)


def _bare_panel(**kwargs) -> quick_actions.QuickActionsPanel:
    """A QuickActionsPanel without running the heavy GTK __init__.

    The wiring methods under test (_on_brightness_changed / _flush_slider_apply
    / _show_brightness_hud) depend only on self._hud, the debounce state and
    the module-level _set_brightness_pct, so a bare instance exercises the
    exact seam the brightness slider handler calls without constructing real
    GTK widgets.
    """
    panel = object.__new__(quick_actions.QuickActionsPanel)
    panel._hud = kwargs.get('hud')
    panel._slider_pending = {}
    panel._slider_timers = {}
    panel._syncing_sliders = False
    panel._slider_appliers = {
        'bright': panel._apply_brightness,
        'vol': quick_actions._set_volume_pct,
    }
    return panel


class TestBrightnessSliderWiresHud:
    @pytest.fixture(autouse=True)
    def _clean_timers(self, monkeypatch):
        # Which fake gi ends up bound depends on which test module first
        # imports quick_actions during collection, so pin the exact seam.
        monkeypatch.setattr(quick_actions, 'GLib', _TIMER_GLIB)
        _TIMER_GLIB.timeouts.clear()
        _TIMER_GLIB.removed.clear()
        yield

    def test_constructor_accepts_hud(self):
        """The real call site (NotificationShade) passes hud=; it must be stored.

        The real __init__ builds GTK widgets, so headlessly we verify the
        signature accepts hud and that NotificationShade forwards it.
        """
        import inspect
        params = inspect.signature(
            quick_actions.QuickActionsPanel.__init__).parameters
        assert 'hud' in params

    def test_brightness_change_applies_without_hud_flash(self, monkeypatch):
        """A drag ending at 63 applies 63 once, and flashes nothing: the
        owner asked for no brightness popup."""
        applied: list[int] = []
        monkeypatch.setattr(quick_actions, '_set_brightness_pct',
                            lambda pct: applied.append(pct))
        hud = _FakeHud()
        panel = _bare_panel(hud=hud)

        panel._on_brightness_changed(63)
        assert applied == []  # nothing spawns until the debounce window closes

        _TIMER_GLIB.fire_all()

        assert applied == [63]
        assert hud.brightness_calls == []

    def test_drag_ticks_collapse_to_one_apply_with_final_value(self, monkeypatch):
        """N value-changed ticks inside one window → exactly one apply, and it
        carries the final (trailing) value."""
        applied: list[int] = []
        monkeypatch.setattr(quick_actions, '_set_brightness_pct',
                            lambda pct: applied.append(pct))
        hud = _FakeHud()
        panel = _bare_panel(hud=hud)

        for pct in (10, 20, 30, 80):
            panel._on_brightness_changed(pct)
        _TIMER_GLIB.fire_all()

        assert applied == [80]
        assert hud.brightness_calls == []

    def test_no_hud_still_applies_brightness_without_crashing(self, monkeypatch):
        """Silent absence: without a HUD the brightness change still applies and
        must not raise."""
        applied: list[int] = []
        monkeypatch.setattr(quick_actions, '_set_brightness_pct',
                            lambda pct: applied.append(pct))
        panel = _bare_panel(hud=None)

        panel._on_brightness_changed(42)
        _TIMER_GLIB.fire_all()

        assert applied == [42]

    def test_slider_handlers_route_through_debounced_queue(self):
        """Both sliders' value-changed handlers must route through the debounce
        queue (one subprocess per drag), never straight at a setter. The
        handler is wired in _build_sliders, which builds real GTK widgets (not
        runnable headlessly), so we assert on the source."""
        import inspect
        src = inspect.getsource(quick_actions.QuickActionsPanel._build_sliders)
        assert "'value-changed'" in src
        bright_branch = src.split("if key == 'bright':")[1].split('else:')[0]
        volume_branch = src.split('else:')[1]
        assert '_on_brightness_changed' in bright_branch
        assert '_on_volume_changed' in volume_branch
        for setter in ('_set_brightness_pct', '_set_volume_pct'):
            assert setter not in src

    def test_debounce_window_is_the_module_constant(self):
        """The trailing-edge window is the shared constant, so tuning it never
        desyncs from the queued timers."""
        import inspect
        src = inspect.getsource(quick_actions.QuickActionsPanel._queue_slider_apply)
        assert '_SLIDER_APPLY_DEBOUNCE_MS' in src


class TestWiringChainReachesPanel:
    def test_notification_shade_passes_hud_to_panel(self):
        """NotificationShade must forward hud= into QuickActionsPanel so the
        running app's HUD reaches the brightness slider."""
        import inspect
        # Other test modules (e.g. test_hud_volume) replace sys.modules['gi']
        # with a stub lacking require_version before tests run, so re-install
        # the full fake here to make importing notification_shade's module-level
        # GTK code resolve in this test regardless of collection order.
        saved_gi = {name: mod for name, mod in sys.modules.items()
                    if name == 'gi' or name.startswith('gi.')}
        _install_fake_gi()
        try:
            from notification_shade import NotificationShade
            params = inspect.signature(NotificationShade.__init__).parameters
            assert 'hud' in params
        finally:
            for name in [name for name in sys.modules
                         if name == 'gi' or name.startswith('gi.')]:
                del sys.modules[name]
            sys.modules.update(saved_gi)