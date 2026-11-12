"""Docked mode (M2): the trackpad recognizer, the pointer sink, the dock
plan, the dock controller against fakes, per-output power, the lock
hooks and the Settings row."""
from __future__ import annotations

import shutil
import subprocess
import sys
import types
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import dock_controller as dc
import output_power
import session_lock
import settings_dock
import trackpad as tp
import wayland_bindings

SRC = Path(__file__).resolve().parent.parent / 'launcher' / 'src'
DATA = SRC.parent / 'data'


# -- trackpad recognizer ---------------------------------------------------

class Sink:
    def __init__(self) -> None:
        self.events: list = []

    def move(self, dx, dy, t):
        self.events.append(('move', dx, dy))

    def button(self, code, pressed, t):
        self.events.append(('button', code, pressed))

    def scroll(self, dx, dy, t):
        self.events.append(('scroll', dx, dy))

    def scroll_stop(self, t):
        self.events.append(('stop',))

    def kinds(self):
        return [e[0] for e in self.events]


@pytest.fixture
def pad():
    sink = Sink()
    return tp.TouchpadGestures(sink), sink


def test_tap_is_a_left_click(pad):
    g, sink = pad
    g.begin(1, 100, 100, 0)
    g.end(1, 101, 100, 80)
    assert sink.events == [('button', tp.BTN_LEFT, True),
                           ('button', tp.BTN_LEFT, False)]


def test_long_still_touch_is_no_click(pad):
    g, sink = pad
    g.begin(1, 100, 100, 0)
    g.end(1, 100, 100, tp.TAP_MS + 50)
    assert sink.events == []


def test_one_finger_moves_with_gain_after_slop(pad):
    g, sink = pad
    g.begin(1, 100, 100, 0)
    g.update(1, 104, 100, 10)        # inside the slop: nothing yet
    assert sink.events == []
    g.update(1, 120, 100, 100)       # slow: 20 px over 90 ms
    g.update(1, 130, 100, 200)
    g.end(1, 130, 100, 210)
    moves = [e for e in sink.events if e[0] == 'move']
    assert moves[0][1] == pytest.approx(20 * tp.BASE_GAIN)
    assert moves[1][1] == pytest.approx(10 * tp.BASE_GAIN)
    assert 'button' not in sink.kinds()


def test_fast_motion_accelerates():
    assert tp.gain(0.1) == tp.BASE_GAIN
    assert tp.gain(1.0) > tp.gain(0.5) > tp.BASE_GAIN
    assert tp.gain(100.0) == tp.MAX_GAIN


def test_two_finger_tap_is_a_right_click(pad):
    g, sink = pad
    g.begin(1, 100, 100, 0)
    g.begin(2, 160, 100, 20)
    g.end(1, 100, 100, 120)
    g.end(2, 160, 100, 140)
    assert sink.events == [('button', tp.BTN_RIGHT, True),
                           ('button', tp.BTN_RIGHT, False)]


def test_three_finger_tap_is_a_middle_click(pad):
    g, sink = pad
    for i, x in enumerate((100, 150, 200)):
        g.begin(i, x, 100, i * 10)
    for i, x in enumerate((100, 150, 200)):
        g.end(i, x, 100, 150 + i)
    assert sink.events == [('button', tp.BTN_MIDDLE, True),
                           ('button', tp.BTN_MIDDLE, False)]


def test_two_finger_drag_scrolls_with_the_fingers(pad):
    g, sink = pad
    g.begin(1, 100, 300, 0)
    g.begin(2, 160, 300, 5)
    for step in range(1, 6):
        y = 300 - step * 10          # fingers move up
        g.update(1, 100, y, 10 + step * 16)
        g.update(2, 160, y, 12 + step * 16)
    g.end(1, 100, 250, 200)
    g.end(2, 160, 250, 205)
    scrolls = [e for e in sink.events if e[0] == 'scroll']
    assert scrolls, sink.events
    # Natural scrolling: fingers up = content up = positive (down) axis.
    assert all(e[2] > 0 for e in scrolls)
    assert sum(e[2] for e in scrolls) == pytest.approx(50 * tp.SCROLL_GAIN)
    assert sink.events[-1] == ('stop',)
    assert 'button' not in sink.kinds() and 'move' not in sink.kinds()


def test_lifting_one_of_two_fingers_does_not_jump(pad):
    g, sink = pad
    g.begin(1, 100, 300, 0)
    g.begin(2, 200, 300, 5)
    g.end(2, 200, 300, 400)
    g.update(1, 100, 290, 410)       # under the slop from the new center
    g.end(1, 100, 290, 420)
    assert sink.events == []


def test_tap_then_touch_and_move_drags(pad):
    g, sink = pad
    g.begin(1, 100, 100, 0)
    g.end(1, 100, 100, 60)
    sink.events.clear()
    g.begin(1, 102, 101, 200)         # within the drag window, near
    assert sink.events == [('button', tp.BTN_LEFT, True)]
    g.update(1, 140, 101, 260)
    g.update(1, 180, 101, 320)
    g.end(1, 180, 101, 330)
    assert sink.kinds()[0] == 'button' and sink.events[-1] == (
        'button', tp.BTN_LEFT, False)
    assert sink.kinds().count('move') == 2
    assert sink.kinds().count('button') == 2


def test_double_tap_is_a_double_click(pad):
    g, sink = pad
    g.begin(1, 100, 100, 0)
    g.end(1, 100, 100, 60)
    g.begin(1, 101, 100, 180)
    g.end(1, 101, 100, 240)
    presses = [e for e in sink.events if e[0] == 'button' and e[2]]
    releases = [e for e in sink.events if e[0] == 'button' and not e[2]]
    assert len(presses) == 2 and len(releases) == 2


def test_late_second_tap_is_a_plain_click(pad):
    g, sink = pad
    g.begin(1, 100, 100, 0)
    g.end(1, 100, 100, 60)
    sink.events.clear()
    g.begin(1, 100, 100, 60 + tp.DRAG_WINDOW_MS + 100)
    assert sink.events == []


def test_cancel_releases_and_never_clicks(pad):
    g, sink = pad
    g.begin(1, 100, 100, 0)
    g.end(1, 100, 100, 50)
    sink.events.clear()
    g.begin(1, 100, 100, 100)        # drag hold
    g.cancel(1, 120)
    assert sink.events == [('button', tp.BTN_LEFT, True),
                           ('button', tp.BTN_LEFT, False)]
    sink.events.clear()
    g.begin(2, 100, 100, 200)
    g.cancel(2, 220)
    assert sink.events == []


def test_unknown_sequences_are_ignored(pad):
    g, sink = pad
    g.update(9, 1, 1, 0)
    g.end(9, 1, 1, 0)
    g.cancel(9, 0)
    assert sink.events == []


# -- pointer sink ------------------------------------------------------------

class FakePointer:
    def __init__(self) -> None:
        self.calls: list = []

    def __getattr__(self, name):
        def record(*args):
            self.calls.append((name,) + args)
        return record


def test_layout_bounds():
    assert tp.layout_bounds([(0, 0, 360, 720), (360, 0, 1280, 720)]) == (
        0, 0, 1640, 720)
    assert tp.layout_bounds([]) == (0, 0, 1, 1)


def test_sink_keeps_the_pointer_on_the_monitor():
    pointer = FakePointer()
    flushed = []
    sink = tp.PointerSink(pointer, lambda: flushed.append(1))
    sink.set_area((360, 0, 1280, 720), (0, 0, 1640, 720))
    assert (sink.x, sink.y) == (1000.0, 360.0)
    sink.move(-5000, 0, 10)           # far left: stops at the monitor edge
    assert sink.x == 360
    name, t, x, y, w, h = pointer.calls[0]
    assert (name, x, y, w, h) == ('motion_absolute', 360, 360, 1640, 720)
    assert pointer.calls[1] == ('frame',)
    sink.move(0, 5000, 20)
    assert sink.y == 719
    assert flushed


def test_sink_click_lands_where_the_pointer_is():
    pointer = FakePointer()
    sink = tp.PointerSink(pointer)
    sink.set_area((360, 0, 1280, 720), (0, 0, 1640, 720))
    sink.button(tp.BTN_LEFT, True, 5)
    names = [c[0] for c in pointer.calls]
    assert names == ['motion_absolute', 'frame', 'button', 'frame']
    assert pointer.calls[2] == ('button', 5, tp.BTN_LEFT, 1)


def test_sink_scroll_uses_finger_source_and_stops():
    pointer = FakePointer()
    sink = tp.PointerSink(pointer)
    sink.scroll(0, 12.0, 7)
    sink.scroll_stop(8)
    assert pointer.calls[0] == ('axis_source', tp.AXIS_SOURCE_FINGER)
    assert pointer.calls[1] == ('axis', 7, tp.AXIS_VERTICAL, 12.0)
    assert ('axis_stop', 8, tp.AXIS_VERTICAL) in pointer.calls
    assert pointer.calls[-1] == ('frame',)


def test_virtual_pointer_xml_has_every_request_the_sink_sends():
    tree = ET.parse(DATA / 'virtual-pointer-unstable-v1.xml')
    iface = next(i for i in tree.getroot().iter('interface')
                 if i.get('name') == 'zwlr_virtual_pointer_v1')
    requests = {r.get('name') for r in iface.iter('request')}
    for name in ('motion_absolute', 'button', 'axis', 'axis_source',
                 'axis_stop', 'frame', 'destroy'):
        assert name in requests


def test_virtual_pointer_without_bindings_reports_failure():
    vp = tp.VirtualPointer(bind_fn=lambda _x, _c: None)
    assert vp._connect() is False
    vp.close()


# -- outputs and plan ---------------------------------------------------------

DOCKED = [
    {'x': 0, 'y': 0, 'w': 360, 'h': 720, 'handset': True, 'workspace': 2,
     'focused': False, 'wl_output': 59},
    {'x': 360, 'y': 0, 'w': 1280, 'h': 720, 'handset': False,
     'workspace': 1, 'focused': True, 'wl_output': 60},
]
UNDOCKED = [DOCKED[0]]


def test_parse_outputs():
    layout = dc.parse_outputs(DOCKED)
    assert layout.docked
    assert layout.handset.wl_output == 59
    assert layout.monitor.rect == (360, 0, 1280, 720)
    assert not dc.parse_outputs(UNDOCKED).docked
    assert dc.parse_outputs('junk') == dc.UNDOCKED
    junk = [None, {'w': 0, 'h': 10}, {'x': 'a', 'w': 10, 'h': 10,
                                      'handset': 'yes', 'wl_output': True}]
    layout = dc.parse_outputs(junk)
    assert layout.handset is None
    assert layout.monitor.wl_output is None and layout.monitor.x == 0


def test_mode_of_defaults_to_trackpad():
    assert dc.mode_of(types.SimpleNamespace(data={})) == dc.TRACKPAD
    assert dc.mode_of(types.SimpleNamespace(
        data={dc.CONFIG_KEY: 'off'})) == dc.OFF
    assert dc.mode_of(types.SimpleNamespace(
        data={dc.CONFIG_KEY: 'sideways'})) == dc.TRACKPAD


def test_plans():
    assert dc.plan_for(dc.TRACKPAD, docked=False) == dc.IDLE
    trackpad = dc.plan_for(dc.TRACKPAD, docked=True)
    assert trackpad == dc.Plan(cover=True, trackpad=True, dark=True,
                               power_off=False, osk_off=True,
                               on_monitor=True)
    off = dc.plan_for(dc.OFF, docked=True)
    assert off.cover and off.power_off and off.dark and not off.trackpad
    second = dc.plan_for(dc.SECOND_SCREEN, docked=True)
    assert second == dc.Plan(False, False, False, False, False, True)


def test_handset_stays_lit_while_its_lock_ui_waits_for_the_pin():
    plan = dc.plan_for(dc.OFF, docked=True, lock_ui_on_handset=True)
    assert plan.cover and not plan.dark and not plan.power_off


def test_no_handset_output_means_nothing_to_cover():
    plan = dc.plan_for(dc.TRACKPAD, docked=True, has_handset=False)
    assert not plan.cover and plan.on_monitor


# -- the controller against fakes --------------------------------------------

class FakeConfig:
    def __init__(self, mode=None) -> None:
        self.data = {} if mode is None else {dc.CONFIG_KEY: mode}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class FakePower:
    def __init__(self) -> None:
        self.calls: list = []
        self.listeners: list = []

    def set_output(self, name, on):
        self.calls.append((name, on))
        return True

    def add_mode_listener(self, cb):
        self.listeners.append(cb)


class FakeCover:
    def __init__(self) -> None:
        self.visible = False

    def get_visible(self):
        return self.visible

    def present(self):
        self.visible = True

    def set_visible(self, v):
        self.visible = v


class FakeMonitor:
    def __init__(self, x, y, w, h) -> None:
        self.geo = types.SimpleNamespace(x=x, y=y, width=w, height=h)

    def get_geometry(self):
        return self.geo


class FakeDisplay:
    def __init__(self) -> None:
        self.monitors = [FakeMonitor(0, 0, 360, 720),
                         FakeMonitor(360, 0, 1280, 720)]

    def get_monitors(self):
        items = self.monitors
        return types.SimpleNamespace(get_n_items=lambda: len(items),
                                     get_item=lambda i: items[i])


@pytest.fixture
def ctl(monkeypatch, tmp_path):
    covers: list = []

    def make_cover(_on_touch):
        cover = FakeCover()
        covers.append(cover)
        return cover

    monkeypatch.setattr(dc, '_make_cover_window', make_cover)
    darks: list = []
    monkeypatch.setattr(dc.DockController, '_set_dark',
                        lambda self, dark, force=False: darks.append(
                            (dark, force)))
    monkeypatch.setattr(dc.DockController, '_ensure_pointer',
                        lambda self: None)
    osk = {'enabled': True}
    monkeypatch.setattr(dc, '_osk_enabled', lambda: osk['enabled'])

    def set_osk(enabled):
        osk['enabled'] = enabled
        return True

    monkeypatch.setattr(dc, '_set_osk_enabled', set_osk)
    power = FakePower()
    controller = dc.DockController(FakeConfig(), output_power=power,
                                   display=FakeDisplay(),
                                   state_dir=tmp_path)
    controller.covers, controller.darks = covers, darks
    controller.osk, controller.power = osk, power
    return controller


def test_dock_covers_darkens_and_turns_the_keyboard_off(ctl, tmp_path):
    ctl.on_outputs(DOCKED)
    assert ctl.covers and ctl.covers[0].visible
    assert ctl.darks == [(True, False)]
    assert ctl.osk['enabled'] is False
    assert (tmp_path / 'dock-osk-off').exists()
    assert ctl.power.calls == []
    assert ctl.surface_monitor() is ctl._gdk_display().monitors[1]


def test_undock_undoes_everything(ctl, tmp_path):
    ctl.on_outputs(DOCKED)
    ctl.on_outputs(UNDOCKED)
    assert not ctl.covers[0].visible
    assert ctl.darks == [(True, False), (False, False)]
    assert ctl.osk['enabled'] is True
    assert not (tmp_path / 'dock-osk-off').exists()
    assert ctl.surface_monitor() is None


def test_off_mode_powers_the_handset_output_off_and_back(ctl):
    ctl._config.set(dc.CONFIG_KEY, dc.OFF)
    ctl.on_outputs(DOCKED)
    assert ctl.power.calls == [(59, False)]
    assert ctl.covers[0].visible
    ctl.on_outputs(UNDOCKED)
    assert ctl.power.calls == [(59, False), (59, True)]


def test_second_screen_leaves_the_phone_alone(ctl):
    ctl._config.set(dc.CONFIG_KEY, dc.SECOND_SCREEN)
    ctl.on_outputs(DOCKED)
    assert ctl.covers == [] and ctl.darks == [] and ctl.power.calls == []
    assert ctl.osk['enabled'] is True
    assert ctl.surface_monitor() is not None


def test_changing_the_mode_while_docked_applies_at_once(ctl):
    ctl.on_outputs(DOCKED)
    ctl.set_mode(dc.SECOND_SCREEN)
    assert not ctl.covers[0].visible
    assert ctl.darks[-1] == (False, False)
    assert ctl._config.data[dc.CONFIG_KEY] == dc.SECOND_SCREEN
    ctl.set_mode(dc.OFF)
    assert ctl.covers[0].visible and ctl.power.calls == [(59, False)]
    ctl.set_mode('bogus')
    assert ctl._config.data[dc.CONFIG_KEY] == dc.OFF


def test_wake_darkens_the_handset_again(ctl):
    ctl.on_outputs(DOCKED)
    ctl.on_blank_changed(False)
    assert ctl.darks[-1] == (True, True)
    ctl.on_outputs(UNDOCKED)
    count = len(ctl.darks)
    ctl.on_blank_changed(False)
    assert len(ctl.darks) == count


def test_a_mode_on_event_darkens_the_handset_again(ctl):
    ctl.on_outputs(DOCKED)
    ctl._on_power_mode(True)
    assert ctl.darks[-1] == (True, True)
    ctl._on_power_mode(False)
    assert len(ctl.darks) == 2


def test_stop_puts_the_phone_back(ctl, monkeypatch):
    monkeypatch.setattr(dc, '_current', ctl)
    ctl._config.set(dc.CONFIG_KEY, dc.OFF)
    ctl.on_outputs(DOCKED)
    ctl.stop()
    assert ctl.power.calls[-1] == (59, True)
    assert not ctl.covers[0].visible and ctl.osk['enabled'] is True
    assert dc.current() is None


def test_a_stale_keyboard_marker_turns_the_keyboard_back_on(ctl, tmp_path):
    (tmp_path / 'dock-osk-off').write_text('1')
    ctl.osk['enabled'] = False
    ctl._restore_stale_osk()
    assert ctl.osk['enabled'] is True
    assert not (tmp_path / 'dock-osk-off').exists()


def test_keyboard_left_off_by_the_user_stays_off(ctl, tmp_path):
    ctl.osk['enabled'] = False
    ctl.on_outputs(DOCKED)
    assert not (tmp_path / 'dock-osk-off').exists()
    ctl.on_outputs(UNDOCKED)
    assert ctl.osk['enabled'] is False


def test_lock_ui_on_the_handset_keeps_it_lit_until_unlock(ctl, monkeypatch):
    handset = ctl._gdk_display().monitors[0]
    locked = {'on': True}
    ctl._is_locked = lambda: locked['on']
    monkeypatch.setattr(session_lock, 'ui_monitor', lambda: handset)
    ctl._config.set(dc.CONFIG_KEY, dc.OFF)
    ctl.on_outputs(DOCKED)
    assert ctl.darks == [] and ctl.power.calls == []
    locked['on'] = False
    ctl.refresh()
    assert ctl.darks == [(True, False)] and ctl.power.calls == [(59, False)]


class Follower:
    def __init__(self) -> None:
        self.placed: list = []
        self.visible = False
        self.presented = 0

    def follow_dock(self, monitor):
        self.placed.append(monitor)
        self.visible = False          # a remap the compositor may close

    def get_visible(self):
        return self.visible

    def present(self):
        self.presented += 1
        self.visible = True


def test_surfaces_follow_the_dock(ctl, monkeypatch):
    monkeypatch.setattr(dc, '_followers', __import__('weakref').WeakSet())
    monkeypatch.setattr(dc, '_current', ctl)
    surface = Follower()
    dc.sync_surface(surface)
    assert surface.placed == [None]
    surface.visible = True
    ctl.on_outputs(DOCKED)
    monitor = ctl._gdk_display().monitors[1]
    assert surface.placed == [None, monitor]
    assert surface.visible and surface.presented == 1
    dc.sync_surface(surface)          # no change: no remap
    assert surface.placed == [None, monitor]
    ctl.on_outputs(UNDOCKED)
    assert surface.placed[-1] is None


def test_place_layer_surface_uses_card_anchors_on_the_monitor():
    calls: list = []
    edge = types.SimpleNamespace(TOP='T', BOTTOM='B', LEFT='L', RIGHT='R')
    shell = types.SimpleNamespace(
        Edge=edge,
        is_layer_window=lambda w: True,
        set_anchor=lambda w, e, on: calls.append(('anchor', e, on)),
        set_margin=lambda w, e, m: calls.append(('margin', e, m)),
        set_exclusive_zone=lambda w, z: calls.append(('zone', z)),
        set_monitor=lambda w, m: calls.append(('monitor', m)))
    sizes: list = []
    window = types.SimpleNamespace(
        get_default_size=lambda: (0, 0),
        set_default_size=lambda w, h: sizes.append((w, h)))
    dc.place_layer_surface(window, 'MON', ('top', 'left', 'right'),
                           ('top', 'right'), desk_size=(400, -1),
                           phone_margin=8, desk_exclusive=0,
                           layer_shell=shell)
    assert ('anchor', 'L', False) in calls and ('anchor', 'R', True) in calls
    assert ('margin', 'T', 16) in calls and ('margin', 'L', 0) in calls
    assert ('zone', 0) in calls and calls[-1] == ('monitor', 'MON')
    assert sizes == [(400, -1)]
    calls.clear()
    dc.place_layer_surface(window, None, ('top', 'left', 'right'),
                           ('top', 'right'), desk_size=(400, -1),
                           phone_margin=8, layer_shell=shell)
    assert ('anchor', 'L', True) in calls and ('margin', 'L', 8) in calls
    assert calls[-1] == ('monitor', None)
    assert sizes == [(400, -1), (0, 0)]


def test_state_stream_lines():
    """xxwm's `subscribe state`: outputs come in state objects; shell
    verbs, bare window lists and junk are not outputs."""
    line = '{"outputs":[{"name":"HEADLESS-2","x":360}],"windows":[]}\n'
    assert dc._parse_line(line) == [{'name': 'HEADLESS-2', 'x': 360}]
    assert dc._parse_line('{"shell":"launcher"}') is None
    assert dc._parse_line('[{"id":"a"}]') is None
    assert dc._parse_line('{"outputs":"no"}') is None
    assert dc._parse_line('\n') is None
    assert dc._parse_line('garbage') is None


class NamedMonitor(FakeMonitor):
    def __init__(self, name, *geo) -> None:
        super().__init__(*geo)
        self.name = name

    def get_connector(self):
        return self.name


def test_monitors_match_by_connector_name_first():
    display = FakeDisplay()
    moved = NamedMonitor('HDMI-A-1', 999, 0, 1280, 720)   # GDK not moved yet
    display.monitors = [display.monitors[0], moved]
    out = dc.Output(360, 0, 1280, 720, False, 60, 'HDMI-A-1')
    assert dc.gdk_monitor_for(display, out) is moved
    unnamed = dc.Output(0, 0, 360, 720, True, 59)
    assert dc.gdk_monitor_for(display, unnamed) is display.monitors[0]
    assert dc.gdk_monitor_for(display, dc.Output(
        5, 5, 5, 5, False, None, 'DP-9')) is None


# -- per-output power ---------------------------------------------------------

@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setattr(wayland_bindings, 'ensure_protocol_bindings',
                        lambda _names, _classes: None)
    b = output_power.OutputPower(display=types.SimpleNamespace(
        flush=lambda: None))
    b._connected = True
    b._manager = object()
    return b


class Power:
    def __init__(self) -> None:
        self.modes: list = []

    def set_mode(self, mode):
        self.modes.append(mode)


def test_a_held_output_is_skipped_by_blank_and_wake(backend):
    phone, monitor = Power(), Power()
    backend._powers = {59: phone, 60: monitor}
    assert backend.set_output(59, False) is True
    assert phone.modes == [output_power.MODE_OFF]
    assert backend.set_all(True) is True
    assert phone.modes == [output_power.MODE_OFF]
    assert monitor.modes == [output_power.MODE_ON]
    assert backend.set_output(59, True) is True
    assert backend.held_off() == frozenset()
    backend.set_all(False)
    assert phone.modes[-1] == output_power.MODE_OFF


def test_mode_events_of_a_held_output_are_not_a_blank(backend):
    phone, monitor = Power(), Power()
    backend._powers = {59: phone, 60: monitor}
    seen: list = []
    backend.add_mode_listener(seen.append)
    backend.set_output(59, False)
    backend._on_mode(phone, output_power.MODE_OFF)
    assert seen == []
    # Unplugged in the dark "off" mode: the monitor goes off before the
    # dock powers the phone on again. Not a blank (it would lock).
    backend._on_mode(monitor, output_power.MODE_OFF)
    assert seen == []
    backend.set_output(59, True)
    backend._on_mode(phone, output_power.MODE_ON)
    assert seen == [True]


def test_a_replayed_off_while_powering_back_on_is_no_blank(backend):
    """On undock river replays the phone output's old "off" after the
    dock already asked for "on": seen on headless river, it locked."""
    phone = Power()
    backend._powers = {59: phone}
    seen: list = []
    backend.add_mode_listener(seen.append)
    backend.set_output(59, False)
    backend._on_mode(phone, output_power.MODE_OFF)
    backend.set_output(59, True)
    backend._on_mode(phone, output_power.MODE_OFF)
    assert seen == []
    backend._on_mode(phone, output_power.MODE_ON)
    backend._on_mode(phone, output_power.MODE_OFF)   # a real blank later
    assert seen == [True, False]


def test_a_monitor_going_dark_is_no_blank_of_the_phone(backend):
    """Undocking disables the monitor (mode off) while the phone is lit:
    that once blanked and locked the phone on every undock."""
    phone, monitor = Power(), Power()
    backend._powers = {59: phone, 60: monitor}
    seen: list = []
    backend.add_mode_listener(seen.append)
    backend._on_mode(phone, output_power.MODE_ON)
    backend._on_mode(monitor, output_power.MODE_ON)
    backend._on_mode(monitor, output_power.MODE_OFF)
    assert seen == [True, True]
    backend._on_mode(phone, output_power.MODE_OFF)    # a real blank
    assert seen == [True, True, False]
    backend._on_mode(phone, output_power.MODE_ON)
    assert seen[-1] is True
    backend._on_registry_global_remove(None, 60)
    backend._on_mode(phone, output_power.MODE_OFF)
    assert seen[-1] is False


def test_set_output_of_an_unknown_output_fails_and_holds_nothing(backend):
    assert backend.set_output(99, True) is False
    assert backend.held_off() == frozenset()


class DestroyablePower(Power):
    destroyed = False

    def destroy(self):
        self.destroyed = True


def test_failed_from_an_unplugged_monitor_is_no_refusal(backend, monkeypatch):
    """wlroots sends `failed` for an output that goes away, just before
    its global is removed: unplugging the monitor must not turn output
    power off for the rest of the session."""
    pending: list = []
    monkeypatch.setattr(output_power, '_post_later',
                        lambda cb, *args: pending.append((cb, args)))
    phone, monitor = Power(), DestroyablePower()
    backend._outputs = {59: object(), 60: object()}
    backend._powers = {59: phone, 60: monitor}
    failed: list = []
    backend.add_fail_listener(lambda: failed.append(1))
    backend._on_failed(monitor)
    backend._on_registry_global_remove(None, 60)
    for cb, args in pending:
        cb(*args)
    assert backend.available is True and failed == []
    assert monitor.destroyed and 60 not in backend._powers
    assert backend.set_all(False) is True


def test_failed_after_the_monitor_global_went_is_no_refusal(backend):
    phone, monitor = Power(), DestroyablePower()
    backend._outputs = {59: object(), 60: object()}
    backend._powers = {59: phone, 60: monitor}
    seen: list = []
    backend.add_mode_listener(seen.append)
    backend._on_registry_global_remove(None, 60)
    # Released at once: river reuses the output under a new global, and
    # a second control object for it would get `failed`.
    assert monitor.destroyed
    # Events already on the wire for the gone output change nothing.
    backend._on_mode(monitor, output_power.MODE_OFF)
    backend._on_failed(monitor)
    assert backend.available is True and seen == []


def test_failed_for_a_present_output_is_a_refusal(backend, monkeypatch):
    pending: list = []
    monkeypatch.setattr(output_power, '_post_later',
                        lambda cb, *args: pending.append((cb, args)))
    phone = DestroyablePower()
    backend._outputs = {59: object()}
    backend._powers = {59: phone}
    backend._on_failed(phone)
    assert backend.available is True
    for cb, args in pending:
        cb(*args)
    assert backend.available is False


# -- lock hooks -------------------------------------------------------------

class FakeInstance:
    def __init__(self) -> None:
        self.assigned: list = []

    def assign_window_to_monitor(self, window, monitor):
        self.assigned.append((window, monitor))


@pytest.fixture
def lock_hooks():
    yield
    session_lock.set_ui_monitor_picker(None)
    session_lock.set_cover_hook(None)
    session_lock._state_listeners.clear()
    session_lock._reset()


def test_lock_ui_goes_where_the_dock_says(lock_hooks):
    lock = session_lock.SessionLock(lambda: 'ui', lambda: 'cover',
                                    module=object(), display=object())
    lock.active = True
    session_lock.set_ui_monitor_picker(lambda: 'monitor')
    covers: list = []
    session_lock.set_cover_hook(lambda c, m: covers.append((c, m)))
    instance = FakeInstance()
    lock._on_monitor(instance, 'handset')
    lock._on_monitor(instance, 'monitor')
    assert instance.assigned == [('cover', 'handset'), ('ui', 'monitor')]
    assert covers == [('cover', 'handset')]
    assert session_lock.ui_monitor() == 'monitor'


def test_lock_state_listeners_hear_lock_and_unlock(lock_hooks):
    seen: list = []
    session_lock.add_state_listener(lambda: seen.append(1))
    session_lock.add_state_listener(lambda: 1 / 0)
    lock = session_lock.SessionLock(lambda: 'ui', lambda: 'cover',
                                    module=object(), display=object())
    lock.active = True
    lock._instance = types.SimpleNamespace(unlock=lambda: None)
    session_lock._ui_monitors[id(lock)] = 'handset'
    lock._on_locked()
    lock._end(failed=False)
    assert seen == [1, 1]
    assert session_lock.ui_monitor() is None


# -- settings row -------------------------------------------------------------

def test_no_dock_row_without_xxwm():
    section = settings_dock.DockSection(available=lambda: False)
    assert section.build(types.SimpleNamespace(config=FakeConfig())) == []


def test_choose_mode_saves_and_applies(monkeypatch):
    applied: list = []
    fake = types.SimpleNamespace(set_mode=applied.append)
    monkeypatch.setattr(settings_dock, 'current', lambda: fake)
    win = types.SimpleNamespace(config=FakeConfig())
    settings_dock.choose_mode(win, dc.OFF)
    assert applied == [dc.OFF]
    monkeypatch.setattr(settings_dock, 'current', lambda: None)
    settings_dock.choose_mode(win, dc.SECOND_SCREEN)
    settings_dock.choose_mode(win, 'nope')
    assert win.config.data[dc.CONFIG_KEY] == dc.SECOND_SCREEN


def test_new_modules_install():
    meson = (SRC.parent / 'meson.build').read_text(encoding='utf-8')
    for name in ('dock_controller.py', 'trackpad.py', 'settings_dock.py'):
        assert f"'src/{name}'" in meson


def test_config_key_is_documented_with_its_default():
    import config
    assert config.DEFAULT_CONFIG[dc.CONFIG_KEY] == dc.DEFAULT_MODE
    doc = (SRC.parent.parent / 'docs' / 'config.md').read_text()
    assert f'`{dc.CONFIG_KEY}`' in doc


# -- real GTK -------------------------------------------------------------------

def test_cover_window_builds_under_real_gtk(tmp_path):
    """The handset cover (and its touch controller) builds and maps on a
    real GTK display without errors."""
    if shutil.which('gtk4-broadwayd') is None:
        pytest.skip('needs gtk4-broadwayd')
    pytest.importorskip('gi')
    common = Path(__file__).resolve().parents[2] / 'common'
    script = tmp_path / 'cover.py'
    script.write_text(
        'import glob, os, sys, time\n'
        'src_dir, run_dir, common = sys.argv[1], sys.argv[2], sys.argv[3]\n'
        "os.environ['GDK_BACKEND'] = 'broadway'\n"
        "os.environ['XDG_RUNTIME_DIR'] = run_dir\n"
        'sys.path.insert(0, common)\n'
        'from xx_testing import broadway_display\n'
        'daemon, display = broadway_display.start(run_dir=run_dir)\n'
        "os.environ['BROADWAY_DISPLAY'] = display\n"
        'for _i in range(200):\n'
        "    if glob.glob(os.path.join(run_dir, 'broadway*.socket')):\n"
        '        break\n'
        '    time.sleep(0.05)\n'
        'try:\n'
        '    import gi\n'
        "    gi.require_version('Gtk', '4.0')\n"
        '    from gi.repository import GLib, Gtk\n'
        '    sys.path.insert(0, src_dir)\n'
        '    import dock_controller\n'
        '    errors = []\n'
        '    sys.excepthook = lambda *a: errors.append(a)\n'
        '    seen = []\n'
        '    win = dock_controller._make_cover_window(\n'
        '        lambda *a: seen.append(a))\n'
        '    win.present()\n'
        '    loop = GLib.MainLoop()\n'
        '    GLib.timeout_add(600, loop.quit)\n'
        '    loop.run()\n'
        "    print('MAPPED', win.get_mapped(), 'ERRORS', len(errors), errors)\n"
        'finally:\n'
        '    daemon.terminate()\n')
    result = subprocess.run(
        [sys.executable, str(script), str(SRC), str(tmp_path), str(common)],
        capture_output=True, text=True, timeout=60, check=False)
    assert 'MAPPED True ERRORS 0' in result.stdout, (
        result.stdout + result.stderr)
