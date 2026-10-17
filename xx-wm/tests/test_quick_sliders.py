"""Slider debounce + per-expand getter batch (P3-G).

Dragging a shade slider used to spawn one brightnessctl/pactl process per
value-changed tick (dozens/sec on a weak tablet), and every expand() re-probed
brightness and volume synchronously per slider. Now:

- value-changed events land in a trailing-edge GLib timeout
  (_SLIDER_APPLY_DEBOUNCE_MS) so a whole drag ends in ONE subprocess apply
  carrying the final value;
- expand() reads brightness+volume in ONE getter batch (_read_slider_values)
  and feeds both sliders from it; a sync guard keeps the set_value round-trip
  from queueing a redundant apply;
- external command semantics are untouched: brightnessctl set <p>% /
  pactl set-sink-volume @DEFAULT_SINK@ <p>% with the exact argv.

quick_actions imports gi.repository.Gdk/GLib/Gio/Gtk at module top level, but
PyGObject may be absent here; minimal fake modules are injected before import,
exactly like test_quick_tiles does. Widget construction is never run — the
debounce and expand logic is exercised on a bare QuickActionsPanel built with
object.__new__, with a controllable GLib timeout scheduler pinned onto the
module.
"""
import subprocess
import sys
import types

import pytest


def _install_fake_gi() -> None:
    """Make `from gi.repository import Gdk, GLib, Gio, Gtk` resolve to stubs."""
    glib = types.ModuleType('gi.repository.GLib')
    glib.SOURCE_REMOVE = False
    glib.SOURCE_CONTINUE = True
    glib.timeout_add = lambda *a, **k: 1
    glib.timeout_add_seconds = lambda *a, **k: 1
    glib.source_remove = lambda *a, **k: None
    glib.idle_add = lambda *a, **k: None
    glib.Error = Exception
    glib.Variant = lambda *a, **k: None
    glib.VariantType = lambda *a, **k: None

    gio = types.ModuleType('gi.repository.Gio')
    gio.DBusCallFlags = types.SimpleNamespace(NONE=0)
    gio.DBusSignalFlags = types.SimpleNamespace(NONE=0)

    for name in ('Gdk', 'Gtk'):
        sys.modules[f'gi.repository.{name}'] = types.ModuleType(
            f'gi.repository.{name}')
    # The class statement `class QuickActionsPanel(Gtk.Box)` evaluates its
    # base class at import time even though widgets are never constructed.
    sys.modules['gi.repository.Gtk'].Box = type('Box', (), {})

    gi_mod = types.ModuleType('gi')
    gi_rep = types.ModuleType('gi.repository')

    def _require_version(namespace, version):
        if namespace == 'Gtk4LayerShell':
            raise ValueError(f'{namespace} not available')
    gi_mod.require_version = _require_version
    sys.modules['gi'] = gi_mod
    sys.modules['gi.repository'] = gi_rep
    sys.modules['gi.repository.GLib'] = glib
    sys.modules['gi.repository.Gio'] = gio


_install_fake_gi()

import quick_actions  # noqa: E402  (needs fake gi modules installed first)


class _TimerGLib:
    """Controllable GLib timeout scheduler: flushes happen only when a test
    fires the due callbacks, so debounce windows are deterministic."""

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

    @property
    def pending_intervals(self):
        return [ms for ms, _cb, _data in self.timeouts.values()]


class _FakeSlider:
    """Records its value and re-emits connected value-changed handlers on
    set_value, mirroring how Gtk.Scale notifies observers."""

    def __init__(self):
        self.value = 0.0
        self.sensitive = True
        self._handlers: dict[str, list] = {}

    def connect(self, signal, cb):
        self._handlers.setdefault(signal, []).append(cb)

    def set_value(self, value):
        self.value = value
        for cb in self._handlers.get('value-changed', []):
            cb(self)

    def get_value(self):
        return self.value

    def set_sensitive(self, value):
        self.sensitive = value


class _FakeVisibility:
    def __init__(self):
        self.visible = None

    def set_visible(self, value):
        self.visible = value


class _FakeHud:
    def __init__(self):
        self.brightness_calls: list[int] = []

    def show_brightness(self, pct):
        self.brightness_calls.append(pct)


@pytest.fixture()
def timer_glib(monkeypatch):
    glib = _TimerGLib()
    monkeypatch.setattr(quick_actions, 'GLib', glib)
    return glib


@pytest.fixture(autouse=True)
def _inline_workers(monkeypatch):
    """Run the W1 worker seam inline: expand() reads sliders off-thread in
    production, so these synchronous assertions drive the worker directly.
    Real-thread interleaving is covered in test_w1_notifications."""
    monkeypatch.setattr(quick_actions, '_spawn_worker_thread',
                        lambda target: target())


def _bare_panel(hud=None) -> quick_actions.QuickActionsPanel:
    panel = object.__new__(quick_actions.QuickActionsPanel)
    panel._hud = hud
    panel._slider_pending = {}
    panel._slider_timers = {}
    panel._syncing_sliders = False
    panel._slider_appliers = {
        'bright': panel._apply_brightness,
        'vol': quick_actions._set_volume_pct,
    }
    return panel


def _panel_for_expand(bright, vol, hud=None):
    panel = _bare_panel(hud=hud)
    panel.tier2_grid = _FakeVisibility()
    panel.sep = _FakeVisibility()
    panel.sliders_box = _FakeVisibility()
    panel.sliders_box.visible = True
    panel._bright_slider = bright
    panel._vol_slider = vol
    return panel


# --- trailing-edge debounce ---------------------------------------------------

class TestSliderDebounce:
    def test_drag_ticks_collapse_to_one_brightness_apply(self, timer_glib,
                                                         monkeypatch):
        applied: list[int] = []
        monkeypatch.setattr(quick_actions, '_set_brightness_pct',
                            lambda pct: applied.append(pct))
        hud = _FakeHud()
        panel = _bare_panel(hud=hud)

        for pct in (5, 15, 25, 70, 90):
            panel._on_brightness_changed(pct)
        assert applied == []  # nothing spawns while the drag is in flight

        timer_glib.fire_all()

        assert applied == [90]
        assert hud.brightness_calls == []  # no brightness popup

    def test_drag_ticks_collapse_to_one_volume_apply(self, timer_glib,
                                                     monkeypatch):
        applied: list[int] = []
        monkeypatch.setattr(quick_actions, '_set_volume_pct',
                            lambda pct: applied.append(pct))
        panel = _bare_panel()

        for pct in (10, 20, 30, 45):
            panel._on_volume_changed(pct)
        assert applied == []

        timer_glib.fire_all()

        assert applied == [45]

    def test_ticks_reschedule_one_timer_instead_of_stacking(self, timer_glib):
        panel = _bare_panel()

        for pct in (10, 20, 30):
            panel._on_brightness_changed(pct)

        assert len(timer_glib.timeouts) == 1
        assert timer_glib.removed == [1, 2]
        assert timer_glib.pending_intervals == [
            quick_actions._SLIDER_APPLY_DEBOUNCE_MS]

    def test_flush_is_idempotent_after_firing(self, timer_glib, monkeypatch):
        applied: list[int] = []
        monkeypatch.setattr(quick_actions, '_set_brightness_pct',
                            lambda pct: applied.append(pct))
        panel = _bare_panel()

        panel._on_brightness_changed(50)
        timer_glib.fire_all()
        timer_glib.fire_all()

        assert applied == [50]

    def test_flush_returns_source_remove(self, timer_glib):
        panel = _bare_panel()
        panel._on_brightness_changed(50)
        assert all(cb(*data) is False
                   for _ms, cb, data in timer_glib.timeouts.values())

    def test_sync_guard_blocks_queuing(self, timer_glib):
        panel = _bare_panel()
        panel._syncing_sliders = True

        panel._on_brightness_changed(50)

        assert timer_glib.timeouts == {}
        assert panel._slider_pending == {}


# --- external command semantics survive the debounce --------------------------

class TestCommandSemanticsPreserved:
    def test_debounced_brightness_apply_uses_exact_argv(self, timer_glib,
                                                        monkeypatch):
        calls = []
        monkeypatch.setattr(subprocess, 'Popen',
                            lambda cmd, **k: calls.append(cmd))
        panel = _bare_panel(hud=_FakeHud())

        panel._on_brightness_changed(72)
        timer_glib.fire_all()

        assert calls == [['brightnessctl', 'set', '72%']]

    def test_debounced_volume_apply_uses_exact_argv(self, timer_glib,
                                                    monkeypatch):
        calls = []
        monkeypatch.setattr(subprocess, 'Popen',
                            lambda cmd, **k: calls.append(cmd))
        panel = _bare_panel()

        panel._on_volume_changed(40)
        timer_glib.fire_all()

        assert calls == [['pactl', 'set-sink-volume', '@DEFAULT_SINK@',
                          '40%']]

    def test_missing_binaries_stay_silent_through_the_debounce(
            self, timer_glib, monkeypatch):
        def _boom(cmd, **k):
            raise FileNotFoundError(cmd[0])
        monkeypatch.setattr(subprocess, 'Popen', _boom)
        panel = _bare_panel(hud=_FakeHud())

        panel._on_brightness_changed(60)
        panel._on_volume_changed(30)
        timer_glib.fire_all()


# --- one getter batch per expand ----------------------------------------------

class TestExpandGetterBatch:
    def test_expand_reads_each_source_once_regardless_of_slider_count(
            self, timer_glib, monkeypatch):
        bright_reads, vol_reads = [], []

        def _fake_bright():
            bright_reads.append(1)
            return 33

        def _fake_vol():
            vol_reads.append(1)
            return 66

        monkeypatch.setattr(quick_actions, '_get_brightness_pct', _fake_bright)
        monkeypatch.setattr(quick_actions, '_get_volume_pct', _fake_vol)
        panel = _panel_for_expand(_FakeSlider(), _FakeSlider())

        panel.expand(True)

        assert len(bright_reads) == 1
        assert len(vol_reads) == 1
        assert panel._bright_slider.value == 33
        assert panel._vol_slider.value == 66

    def test_expand_never_spawns_an_apply_from_set_value_round_trip(
            self, timer_glib, monkeypatch):
        calls = []
        monkeypatch.setattr(subprocess, 'Popen',
                            lambda cmd, **k: calls.append(cmd))
        monkeypatch.setattr(quick_actions, '_get_brightness_pct', lambda: 33)
        monkeypatch.setattr(quick_actions, '_get_volume_pct', lambda: 66)
        bright, vol = _FakeSlider(), _FakeSlider()
        panel = _panel_for_expand(bright, vol)

        panel.expand(True)
        timer_glib.fire_all()

        assert calls == []

    def test_collapse_probes_nothing(self, timer_glib, monkeypatch):
        probes = []
        monkeypatch.setattr(quick_actions, '_get_brightness_pct',
                            lambda: probes.append('b'))
        monkeypatch.setattr(quick_actions, '_get_volume_pct',
                            lambda: probes.append('v'))
        panel = _panel_for_expand(_FakeSlider(), _FakeSlider())

        panel.expand(False)

        assert probes == []
        assert panel.tier2_grid.visible is False
        assert panel.sliders_box.visible is True

    def test_expand_shows_tier2_and_keeps_sliders(self, timer_glib, monkeypatch):
        monkeypatch.setattr(quick_actions, '_get_brightness_pct', lambda: 50)
        monkeypatch.setattr(quick_actions, '_get_volume_pct', lambda: 50)
        panel = _panel_for_expand(_FakeSlider(), _FakeSlider())

        panel.expand(True)

        assert panel.tier2_grid.visible is True
        assert panel.sep.visible is True
        assert panel.sliders_box.visible is True

    def test_sliders_stay_visible_when_collapsed(self, timer_glib, monkeypatch):
        monkeypatch.setattr(quick_actions, '_get_brightness_pct', lambda: 50)
        monkeypatch.setattr(quick_actions, '_get_volume_pct', lambda: 50)
        panel = _panel_for_expand(_FakeSlider(), _FakeSlider())
        panel.expand(True)
        panel.expand(False)
        assert panel.sliders_box.visible is True
        assert panel.tier2_grid.visible is False

    def test_expand_does_not_gate_sliders(self):
        import inspect
        src = inspect.getsource(quick_actions.QuickActionsPanel.expand)
        assert 'sliders_box.set_visible' not in src

    def test_expand_batch_routes_through_read_slider_values(self):
        import inspect
        src = inspect.getsource(
            quick_actions.QuickActionsPanel._sync_sliders_to_live_state)
        assert '_read_slider_values()' in src
        assert '_get_brightness_pct' not in src
        assert '_get_volume_pct' not in src


# --- sysfs brightness read (no brightnessctl spawn on expand) -----------------

class TestSysfsBrightnessRead:
    def _tree(self, root, nodes):
        for name, files in nodes.items():
            node = root / name
            node.mkdir(parents=True, exist_ok=True)
            for filename, content in files.items():
                (node / filename).write_text(content)

    def test_reads_percent_from_first_node(self, tmp_path):
        self._tree(tmp_path, {'intel_backlight': {
            'brightness': '40\n', 'max_brightness': '200\n'}})
        assert quick_actions._read_sysfs_brightness_pct(tmp_path) == 20

    def test_zero_brightness_is_zero_not_default(self, tmp_path):
        self._tree(tmp_path, {'intel_backlight': {
            'brightness': '0\n', 'max_brightness': '200\n'}})
        assert quick_actions._read_sysfs_brightness_pct(tmp_path) == 0

    def test_missing_nodes_give_none(self, tmp_path):
        assert quick_actions._read_sysfs_brightness_pct(tmp_path) is None

    def test_getter_falls_back_to_default(self, monkeypatch):
        monkeypatch.setattr(quick_actions, '_read_sysfs_brightness_pct',
                            lambda: None)
        assert quick_actions._get_brightness_pct() == \
            quick_actions._SLIDER_DEFAULT_PCT

    def test_getter_keeps_zero(self, monkeypatch):
        monkeypatch.setattr(quick_actions, '_read_sysfs_brightness_pct',
                            lambda: 0)
        assert quick_actions._get_brightness_pct() == 0

    def test_getter_spawns_no_process(self):
        import inspect
        src = inspect.getsource(quick_actions._get_brightness_pct)
        assert 'subprocess' not in src
        assert 'check_output' not in src
