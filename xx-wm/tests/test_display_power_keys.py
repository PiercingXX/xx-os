"""Power long-press contract + fingerprint node routing (headless).

The key-up handler must blank only when the press was short: after a fired
long-press (power menu shown) the release must do nothing, or the menu
appears and the screen immediately goes dark. These tests drive
DisplayManager._on_event directly with fake GLib (PyGObject is absent here);
idle_add invokes callbacks synchronously so on_* wiring is observable.
"""
import struct
import sys
import types

from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import pytest


class _SyncGLib:
    """Synchronous GLib seam: idle_add runs inline; timers are inert ids."""

    SOURCE_REMOVE = False
    SOURCE_CONTINUE = True

    @staticmethod
    def idle_add(fn, *a):
        fn(*a)
        return 0

    @staticmethod
    def timeout_add(_ms, _cb, *_a):
        return 1

    timeout_add_seconds = timeout_add

    @staticmethod
    def source_remove(_tid):
        return None


def _install_fake_glib() -> None:
    """Make `from gi.repository import GLib` resolve to a stub."""
    glib = types.ModuleType('gi.repository.GLib')
    glib.SOURCE_REMOVE = False
    glib.SOURCE_CONTINUE = True
    glib.timeout_add = lambda *a, **k: 1
    glib.timeout_add_seconds = lambda *a, **k: 1
    glib.source_remove = lambda *a, **k: None
    glib.idle_add = lambda fn, *a, **k: (fn(*a), 0)[1]

    gi_mod = types.ModuleType('gi')
    gi_rep = types.ModuleType('gi.repository')

    def _require_version(namespace, version):
        if namespace == 'Gtk4LayerShell':
            raise ValueError(f'{namespace} not available')
    gi_mod.require_version = _require_version
    sys.modules['gi'] = gi_mod
    sys.modules['gi.repository'] = gi_rep
    sys.modules['gi.repository.GLib'] = glib


_install_fake_glib()

import display_manager  # noqa: E402  (needs fake GLib installed first)


@pytest.fixture
def dm(monkeypatch):
    """Build DisplayManager with one fake input node (so the idle timer is
    enabled) but no reader threads, and stubbed display backends; _blank/
    _wake record invocations instead of touching brightnessctl/wlopm. The
    fingerprint node is pinned to event9."""
    # Which fake gi wins collection decides what display_manager bound as
    # GLib at import time, so pin the seam: idle_add synchronous, timers
    # inert — the tests drive _on_event/_on_power_long_press directly.
    monkeypatch.setattr(display_manager, 'GLib', _SyncGLib)
    monkeypatch.setattr(display_manager, '_all_event_devices',
                        lambda: ['/dev/input/eventX'])
    monkeypatch.setattr(display_manager, '_watch_evdev',
                        lambda *a, **k: None)
    monkeypatch.setattr(display_manager, '_detect_fp_node',
                        lambda: '/dev/input/event9')
    # The kernel agrees with the logical key state unless a test says not.
    monkeypatch.setattr(display_manager, '_key_is_down', lambda *_a: True,
                        raising=False)
    blanks: list[bool] = []
    wakes: list[bool] = []
    monkeypatch.setattr(display_manager.DisplayManager, '_blank',
                        lambda self: blanks.append(True))
    monkeypatch.setattr(display_manager.DisplayManager, '_wake',
                        lambda self: wakes.append(True))

    def _build(**kwargs):
        return display_manager.DisplayManager(**kwargs), blanks, wakes

    return _build


def _power(value: int):
    return ('/dev/input/eventX', display_manager.EV_KEY,
            display_manager.KEY_POWER, value)


def _voldown(value: int):
    return ('/dev/input/eventX', display_manager.EV_KEY,
            display_manager.KEY_VOLUMEDOWN, value)


class TestScreenshotConsumesPowerPress:
    def test_combo_volume_released_first_suppresses_blank(self, dm):
        shots: list[bool] = []
        mgr, blanks, wakes = dm(on_screenshot=lambda: shots.append(True))
        mgr._on_event(*_power(1))
        mgr._on_event(*_voldown(1))
        assert shots == [True]
        mgr._on_event(*_voldown(0))
        mgr._on_event(*_power(0))
        assert blanks == []
        assert wakes == []

    def test_combo_power_released_first_suppresses_blank(self, dm):
        shots: list[bool] = []
        mgr, blanks, wakes = dm(on_screenshot=lambda: shots.append(True))
        mgr._on_event(*_power(1))
        mgr._on_event(*_voldown(1))
        mgr._on_event(*_power(0))
        mgr._on_event(*_voldown(0))
        assert shots == [True]
        assert blanks == []
        assert wakes == []

    def test_next_press_after_combo_blanks_again(self, dm):
        mgr, blanks, _wakes = dm(on_screenshot=lambda: None)
        mgr._on_event(*_power(1))
        mgr._on_event(*_voldown(1))
        mgr._on_event(*_voldown(0))
        mgr._on_event(*_power(0))
        mgr._on_event(*_power(1))
        mgr._on_event(*_power(0))
        assert len(blanks) == 1

    def test_long_press_sets_consumed_and_clears_on_next_down(self, dm):
        mgr, blanks, _wakes = dm()
        assert mgr._power_press_consumed is False
        mgr._on_event(*_power(1))
        mgr._on_power_long_press()
        assert mgr._power_press_consumed is True
        mgr._on_event(*_power(0))
        assert blanks == []
        mgr._on_event(*_power(1))
        assert mgr._power_press_consumed is False

    def test_combo_while_blanked_wakes_before_screenshot(self, dm):
        shots: list[bool] = []
        mgr, blanks, wakes = dm(on_screenshot=lambda: shots.append(True))
        mgr._blanked = True
        mgr._on_event(*_power(1))
        mgr._on_event(*_voldown(1))
        assert wakes == [True]
        assert shots == [True]
        mgr._on_event(*_voldown(0))
        mgr._on_event(*_power(0))
        assert blanks == []


class TestLostVolumeDownRelease:
    """A volume-down release that never arrives (node re-enumerated, event
    dropped) must not disable the power key."""

    def test_stale_voldown_does_not_eat_short_press(self, dm, monkeypatch):
        shots: list[bool] = []
        mgr, blanks, _wakes = dm(on_screenshot=lambda: shots.append(True))
        mgr._on_event(*_voldown(1))   # the release is lost
        monkeypatch.setattr(display_manager, '_key_is_down', lambda *_a: False,
                            raising=False)
        mgr._on_event(*_power(1))
        mgr._on_event(*_power(0))
        assert shots == []
        assert len(blanks) == 1

    def test_unreadable_voldown_node_counts_as_released(self, dm, monkeypatch):
        mgr, blanks, _wakes = dm(on_screenshot=lambda: None)
        mgr._on_event(*_voldown(1))
        monkeypatch.setattr(display_manager, '_key_is_down', lambda *_a: None,
                            raising=False)
        mgr._on_event(*_power(1))
        mgr._on_event(*_power(0))
        assert len(blanks) == 1

    def test_short_press_toggles_even_with_voldown_flag_set(self, dm):
        # Key-up never consults volume-down: only a consumed press skips.
        mgr, blanks, _wakes = dm()
        mgr._on_event(*_power(1))
        mgr._voldown_held = True
        mgr._on_event(*_power(0))
        assert len(blanks) == 1

    def test_real_combo_with_voldown_first_still_screenshots(self, dm):
        shots: list[bool] = []
        mgr, blanks, _wakes = dm(on_screenshot=lambda: shots.append(True))
        mgr._on_event(*_voldown(1))
        mgr._on_event(*_power(1))
        mgr._on_event(*_power(0))
        mgr._on_event(*_voldown(0))
        assert shots == [True]
        assert blanks == []

    def test_key_is_down_on_a_non_evdev_file_is_none(self, tmp_path):
        node = tmp_path / 'event0'
        node.write_bytes(b'')
        assert display_manager._key_is_down(str(node), 114) is None
        assert display_manager._key_is_down(str(tmp_path / 'gone'), 114) is None


class TestLongPressContract:
    def test_fired_long_press_suppresses_key_up_blank(self, dm):
        menus: list[bool] = []
        mgr, blanks, wakes = dm(on_power_menu=lambda: menus.append(True))
        mgr._on_event(*_power(1))
        mgr._on_power_long_press()
        assert menus == [True]
        mgr._on_event(*_power(0))
        assert blanks == []
        assert wakes == []

    def test_cancelled_pending_behaves_as_short_press(self, dm):
        mgr, blanks, wakes = dm()
        mgr._on_event(*_power(1))
        mgr._on_event(*_power(0))
        assert len(blanks) == 1
        assert wakes == []

    def test_plain_key_up_without_press_unchanged(self, dm):
        mgr, blanks, wakes = dm()
        mgr._on_event(*_power(0))
        assert len(blanks) == 1
        assert wakes == []

    def test_next_press_after_long_press_blanks_again(self, dm):
        mgr, blanks, _wakes = dm()
        mgr._on_event(*_power(1))
        mgr._on_power_long_press()
        mgr._on_event(*_power(0))
        mgr._on_event(*_power(1))
        mgr._on_event(*_power(0))
        assert len(blanks) == 1


class TestFingerprintNodeRouting:
    def test_key_on_detected_node_triggers_auth(self, dm):
        auths: list[bool] = []
        mgr, _blanks, _wakes = dm(on_fingerprint=lambda: auths.append(True))
        mgr._on_event('/dev/input/event9', display_manager.EV_KEY, 96, 1)
        assert auths == [True]

    def test_key_on_other_node_does_not_trigger_auth(self, dm):
        auths: list[bool] = []
        mgr, _blanks, _wakes = dm(on_fingerprint=lambda: auths.append(True))
        mgr._on_event('/dev/input/event3', display_manager.EV_KEY, 96, 1)
        assert auths == []

    def test_key_on_detected_node_wakes_blanked_screen(self, dm):
        mgr, blanks, wakes = dm()
        mgr._blanked = True
        mgr._on_event('/dev/input/event9', display_manager.EV_KEY, 96, 1)
        assert wakes == [True]
        assert blanks == []


def _ev_bytes(ev_type: int, code: int, value: int) -> bytes:
    return struct.pack(display_manager._EV_FMT, 0, 0, ev_type, code, value)


@pytest.fixture
def idle_calls(monkeypatch, dm):
    """Record GLib.timeout_add_seconds calls. Depends on dm so the pinned
    GLib seam exists before this patches its scheduler."""
    calls: list[int] = []
    monkeypatch.setattr(
        display_manager.GLib, 'timeout_add_seconds',
        lambda secs, _cb: (calls.append(secs), 99)[1],
    )
    return calls


class TestReaderThreadFilter:
    """filter_evdev_data is the pure in-thread gate: only actionable events
    may be posted to the GTK loop, so scroll/drag floods and streaming
    sensors never queue callbacks there."""

    def test_power_and_volume_keys_post(self):
        data = b''.join([
            _ev_bytes(display_manager.EV_KEY, display_manager.KEY_POWER, 1),
            _ev_bytes(display_manager.EV_KEY, display_manager.KEY_POWER, 0),
            _ev_bytes(display_manager.EV_KEY, display_manager.KEY_VOLUMEUP, 1),
            _ev_bytes(display_manager.EV_KEY, display_manager.KEY_VOLUMEDOWN, 0),
        ])
        wanted, last = display_manager.filter_evdev_data(
            data, '/dev/input/event0', '/dev/input/event9', 1000.0)
        assert [w[1:] for w in wanted] == [
            (display_manager.EV_KEY, display_manager.KEY_POWER, 1),
            (display_manager.EV_KEY, display_manager.KEY_POWER, 0),
            (display_manager.EV_KEY, display_manager.KEY_VOLUMEUP, 1),
            (display_manager.EV_KEY, display_manager.KEY_VOLUMEDOWN, 0),
        ]
        assert all(w[0] == '/dev/input/event0' for w in wanted)
        assert last is None

    def test_flood_events_dropped(self):
        data = b''.join([
            _ev_bytes(0, 0, 0),    # EV_SYN
            _ev_bytes(4, 4, 1),    # EV_MSC
            _ev_bytes(2, 0, 5),    # EV_REL motion
            _ev_bytes(display_manager.EV_ABS, 0, 120),   # ABS_X touch-move
            _ev_bytes(display_manager.EV_ABS, 53, 200),  # ABS_MT_POSITION_X
        ])
        wanted, last = display_manager.filter_evdev_data(
            data, '/dev/input/event1', '/dev/input/event9', 1000.0)
        assert wanted == []
        assert last is None

    def test_fingerprint_node_posts_presses_only(self):
        press = _ev_bytes(display_manager.EV_KEY, 96, 1)
        release = _ev_bytes(display_manager.EV_KEY, 96, 0)
        wanted, _ = display_manager.filter_evdev_data(
            press, '/dev/input/event9', '/dev/input/event9', 1000.0)
        assert len(wanted) == 1
        wanted, _ = display_manager.filter_evdev_data(
            release, '/dev/input/event9', '/dev/input/event9', 1000.0)
        assert wanted == []
        wanted, _ = display_manager.filter_evdev_data(
            press, '/dev/input/event3', '/dev/input/event9', 1000.0)
        assert wanted == []

    def test_touch_down_throttled_to_one_per_window(self):
        down = _ev_bytes(
            display_manager.EV_ABS,
            display_manager.ABS_MT_TRACKING_ID, 5)
        wanted, last = display_manager.filter_evdev_data(
            down, '/dev/input/event1', '/dev/input/event9', 1000.0)
        assert len(wanted) == 1
        assert last == 1000.0
        wanted, last = display_manager.filter_evdev_data(
            down, '/dev/input/event1', '/dev/input/event9', 1100.0, last)
        assert wanted == []
        assert last == 1000.0
        wanted, last = display_manager.filter_evdev_data(
            down, '/dev/input/event1', '/dev/input/event9', 1250.0, last)
        assert len(wanted) == 1
        assert last == 1250.0

    def test_touch_lift_never_posts(self):
        lift = _ev_bytes(
            display_manager.EV_ABS,
            display_manager.ABS_MT_TRACKING_ID, -1)
        wanted, _ = display_manager.filter_evdev_data(
            lift, '/dev/input/event1', '/dev/input/event9', 1000.0)
        assert wanted == []

    def test_trailing_partial_bytes_ignored(self):
        data = (_ev_bytes(display_manager.EV_KEY,
                          display_manager.KEY_POWER, 1) + b'\x00\x01')
        wanted, _ = display_manager.filter_evdev_data(
            data, '/dev/input/event0', '/dev/input/event9', 1000.0)
        assert len(wanted) == 1

    def test_each_device_throttles_independently(self):
        down = _ev_bytes(
            display_manager.EV_ABS,
            display_manager.ABS_MT_TRACKING_ID, 5)
        for dev in ('/dev/input/event1', '/dev/input/event2'):
            wanted, _ = display_manager.filter_evdev_data(
                down, dev, '/dev/input/event9', 1000.0, None)
            assert len(wanted) == 1


def _touch_down():
    return ('/dev/input/event1', display_manager.EV_ABS,
            display_manager.ABS_MT_TRACKING_ID, 7)


class TestTouchNeverWakesBlanked:
    """Pocket safety: only the power key and the fingerprint node wake a
    blanked screen. Touch while lit only pushes the idle timer out."""

    def test_touch_while_blanked_is_ignored(self, dm, idle_calls):
        mgr, blanks, wakes = dm()
        mgr._blanked = True
        idle_calls.clear()
        mgr._on_event(*_touch_down())
        assert wakes == []
        assert blanks == []
        assert idle_calls == []

    def test_touch_while_lit_resets_idle(self, dm, idle_calls):
        mgr, blanks, wakes = dm()
        idle_calls.clear()
        mgr._on_event(*_touch_down())
        assert wakes == []
        assert blanks == []
        assert idle_calls == [display_manager._IDLE_SECS]


class TestBlankTimeout:
    def test_default_is_sixty_seconds(self, dm, idle_calls):
        dm()
        assert idle_calls == [60]

    def test_custom_timeout_applies(self, dm, idle_calls):
        dm(get_blank_timeout=lambda: 30)
        assert idle_calls == [30]

    def test_zero_never_schedules(self, dm, idle_calls):
        mgr, _blanks, _wakes = dm(get_blank_timeout=lambda: 0)
        assert idle_calls == []
        assert mgr._idle_src is None

    def test_invalid_value_falls_back_to_default(self, dm, idle_calls):
        dm(get_blank_timeout=lambda: 'junk')
        assert idle_calls == [display_manager._IDLE_SECS]

    def test_config_property_clamped_like_auto_lock(self):
        from config import DEFAULT_CONFIG, ShellConfig
        cfg = ShellConfig.__new__(ShellConfig)
        cfg.data = dict(DEFAULT_CONFIG)
        assert cfg.display_blank_timeout == 60
        cfg.data = dict(DEFAULT_CONFIG, display_blank_timeout=30)
        assert cfg.display_blank_timeout == 30
        cfg.data = dict(DEFAULT_CONFIG, display_blank_timeout=0)
        assert cfg.display_blank_timeout == 0
        cfg.data = dict(DEFAULT_CONFIG, display_blank_timeout=-5)
        assert cfg.display_blank_timeout == 0
        cfg.data = dict(DEFAULT_CONFIG, display_blank_timeout='junk')
        assert cfg.display_blank_timeout == 60


class TestInhibitIdle:
    def test_inhibited_idle_never_blanks(self, dm):
        mgr, blanks, _wakes = dm()
        mgr.inhibit_idle('call', True)
        mgr._on_idle()
        assert blanks == []

    def test_released_inhibitor_blanks_again(self, dm):
        mgr, blanks, _wakes = dm()
        mgr.inhibit_idle('call', True)
        mgr.inhibit_idle('call', False)
        mgr._on_idle()
        assert blanks == [True]

    def test_all_reasons_must_release(self, dm):
        mgr, blanks, _wakes = dm()
        mgr.inhibit_idle('call', True)
        mgr.inhibit_idle('nav', True)
        mgr.inhibit_idle('call', False)
        mgr._on_idle()
        assert blanks == []
        mgr.inhibit_idle('nav', False)
        mgr._on_idle()
        assert blanks == [True]


class TestWakeApi:
    def test_wake_while_blanked_wakes(self, dm):
        mgr, blanks, wakes = dm()
        mgr._blanked = True
        mgr.wake()
        assert wakes == [True]
        assert blanks == []

    def test_wake_while_lit_resets_idle(self, dm, idle_calls):
        mgr, blanks, wakes = dm()
        idle_calls.clear()
        mgr.wake()
        assert wakes == []
        assert idle_calls == [display_manager._IDLE_SECS]
