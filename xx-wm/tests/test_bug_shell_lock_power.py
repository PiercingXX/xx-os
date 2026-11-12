"""Lock / power findings from the other-shell reviews (reference/_findings).

1: lock swipe unlocked on velocity alone: a 10 px pocket flick at speed
   unlocked a no-PIN phone. Now travel >= 10% H, mostly vertical, and a
   speed requirement that falls to zero at half the height (Lomiri).
2: lockout resume trusted the wall-clock deadline: an RTC reset to 1970
   re-armed a lockout for decades. Now capped by the fail count and
   cleared (count kept) when the clock reads before the lockout start.
3: gesture verbs ran while the screen was blank but not yet locked.
4: PrepareForSleep locked without a logind delay inhibitor, so suspend
   could beat the lock's first frame (unlocked flash on resume).
5: Suspend was offered (and nothing blocked sleep) during a live call.
6: KEY_WAKEUP (143) from tap-to-wake panels never woke the display.

Headless: GTK widgets are never built; decision methods run unbound on
namespaces and the GLib seams are pinned per test.
"""
import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from lock_lines import (  # noqa: E402
    SWIPE_FLICK_SPEED,
    _lockout_secs,
    release_velocity,
    resume_lockout_secs,
    swipe_unlocks,
)

H = 720


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))


def _bind(ns, cls, *names):
    """Run the real methods against a namespace self (GObject classes
    refuse object.__new__ under real PyGObject)."""
    for name in names:
        setattr(ns, name, getattr(cls, name).__get__(ns))
    return ns


# ---- 1: swipe-to-unlock rule ---------------------------------------------

class TestSwipeUnlocks:
    def test_short_fast_flick_never_unlocks(self):
        # The old rule: any vy < -300 unlocked, however short.
        assert not swipe_unlocks(0, -10, -3000, H)
        assert not swipe_unlocks(0, -0.09 * H, -5000, H)

    def test_min_travel_with_flick_speed_unlocks(self):
        travel = 0.10 * H
        needed = SWIPE_FLICK_SPEED * (1 - travel / (0.5 * H))
        assert swipe_unlocks(0, -travel, -needed, H)
        assert not swipe_unlocks(0, -travel, -(needed - 1), H)

    def test_slow_long_drag_unlocks(self):
        assert swipe_unlocks(0, -0.5 * H, 0.0, H)
        assert swipe_unlocks(5, -0.6 * H, 0.0, H)

    def test_required_speed_falls_linearly(self):
        travel = 0.25 * H   # halfway to the no-speed point
        assert swipe_unlocks(0, -travel, -SWIPE_FLICK_SPEED / 2, H)
        assert not swipe_unlocks(0, -travel, -SWIPE_FLICK_SPEED / 2 + 5, H)

    def test_downward_or_sideways_never_unlocks(self):
        assert not swipe_unlocks(0, 0.6 * H, -2000, H)
        assert not swipe_unlocks(300, -0.2 * H, -2000, H)   # |dy| < 1.5|dx|
        assert not swipe_unlocks(100, -150, -2000, H)        # exactly 1.5x

    def test_downward_release_speed_counts_as_zero(self):
        assert not swipe_unlocks(0, -0.2 * H, 800, H)
        assert swipe_unlocks(0, -0.5 * H, 800, H)

    def test_unallocated_height_uses_fallback(self):
        assert not swipe_unlocks(0, -20, -3000, 0)
        assert swipe_unlocks(0, -400, 0.0, 0)


class TestReleaseVelocity:
    def test_uses_last_window_only(self):
        samples = [(0.0, 0.0), (0.5, -10.0), (0.95, -20.0), (1.0, -70.0)]
        # Window 0.1 s: from (0.95, -20) to (1.0, -70) -> -1000 px/s.
        assert release_velocity(samples) == pytest.approx(-1000.0)

    def test_held_finger_reads_zero(self):
        assert release_velocity([(0.0, 0.0)]) == 0.0
        assert release_velocity([(1.0, -50.0), (1.0, -50.0)]) == 0.0

    def test_stale_previous_sample_still_measured(self):
        assert release_velocity([(0.0, 0.0), (1.0, -100.0)]) == \
            pytest.approx(-100.0)


class _Visible:
    def __init__(self):
        self.visible = True

    def set_visible(self, value):
        self.visible = bool(value)


class _Revealer:
    def __init__(self):
        self.revealed = False

    def set_reveal_child(self, value):
        self.revealed = bool(value)


@pytest.fixture
def lock_mod(monkeypatch):
    import lock_screen
    clock = {'us': 10_000_000}
    monkeypatch.setattr(lock_screen, 'GLib', types.SimpleNamespace(
        get_monotonic_time=lambda: clock['us'],
        SOURCE_REMOVE=False,
        timeout_add=lambda *_a: 7,
        source_remove=lambda *_a: None))
    lock_screen._test_clock = clock
    return lock_screen


def _drag_lock(lock_mod, height=H):
    swiped = []
    ns = types.SimpleNamespace(
        _drag_samples=[],
        get_height=lambda: height,
        _swipe_up=lambda: swiped.append(True))
    for name in ('_note_drag_sample', '_on_drag_begin', '_on_drag_update',
                 '_on_drag_end'):
        setattr(ns, name, getattr(lock_mod.LockScreen, name).__get__(ns))
    return ns, swiped


def _drag(lock_mod, ns, points, step_us=20_000):
    clock = lock_mod._test_clock
    ns._on_drag_begin(None, 100, 600)
    for dx, dy in points[:-1]:
        clock['us'] += step_us
        ns._on_drag_update(None, dx, dy)
    clock['us'] += step_us
    ns._on_drag_end(None, *points[-1])


class TestLockDragWiring:
    def test_pocket_flick_ignored(self, lock_mod):
        ns, swiped = _drag_lock(lock_mod)
        _drag(lock_mod, ns, [(0, -5), (0, -10)])   # 500 px/s, 10 px
        assert swiped == []

    def test_real_flick_unlocks(self, lock_mod):
        ns, swiped = _drag_lock(lock_mod)
        _drag(lock_mod, ns, [(0, -30), (0, -60), (0, -100)])
        assert swiped == [True]
        assert ns._drag_samples == []

    def test_slow_drag_past_half_unlocks(self, lock_mod):
        ns, swiped = _drag_lock(lock_mod)
        points = [(0, -40 * i) for i in range(1, 10)] + [(0, -360)] * 3
        _drag(lock_mod, ns, points, step_us=200_000)
        assert swiped == [True]

    def test_build_uses_drag_not_bare_swipe(self):
        src = (Path(__file__).parent.parent / 'launcher' / 'src' /
               'lock_screen.py').read_text(encoding='utf-8')
        assert 'Gtk.GestureDrag.new()' in src
        assert 'Gtk.GestureSwipe.new()' not in src


# ---- 2: lockout resume vs clock jumps ------------------------------------

NOW = 1_800_000_000.0


class TestResumeLockoutSecs:
    def test_running_lockout_resumes_remaining(self):
        assert resume_lockout_secs(5, NOW + 20, NOW - 10, NOW) == 20

    def test_capped_by_fail_count(self):
        # Clock reset to 1970 after the lockout was persisted in 2027,
        # legacy state (no start): decades remaining, capped to 30 s.
        assert resume_lockout_secs(5, NOW, 0.0, 5_000.0) == _lockout_secs(5)
        assert resume_lockout_secs(50, NOW + 1e9, NOW, NOW) == 300

    def test_clock_before_start_clears(self):
        assert resume_lockout_secs(6, NOW + 60, NOW, 5_000.0) is None

    def test_expired_or_no_fails_rearms_nothing(self):
        assert resume_lockout_secs(5, NOW - 1, NOW - 31, NOW) == 0
        assert resume_lockout_secs(0, NOW + 60, 0.0, NOW) == 0


class _LockoutConfig:
    def __init__(self, fails, until, start=None):
        self.state = {'fails': fails, 'until': until}
        if start is not None:
            self.state['start'] = start
        self.writes = []

    def get_lockout_state(self):
        return self.state['fails'], self.state['until']

    def get_lockout_start(self):
        return self.state.get('start', 0.0)

    def set_lockout_state(self, fails, until, start=None):
        self.writes.append((fails, until, start))
        self.state = {'fails': fails, 'until': until}
        if start is not None:
            self.state['start'] = start


def _resume(lock_mod, monkeypatch, config, now):
    monkeypatch.setattr(lock_mod.time, 'time', lambda: now)
    armed = []
    ns = types.SimpleNamespace(
        _config=config, _fail_count=0,
        _start_lockout=armed.append)
    lock_mod.LockScreen._resume_lockout_state(ns)
    return ns, armed


class TestResumeLockoutState:
    def test_rtc_reset_legacy_state_is_capped(self, lock_mod, monkeypatch):
        config = _LockoutConfig(5, NOW + 30)
        ns, armed = _resume(lock_mod, monkeypatch, config, 86_400.0)
        assert ns._fail_count == 5
        assert armed == [_lockout_secs(5)]

    def test_clock_before_start_clears_but_keeps_count(
            self, lock_mod, monkeypatch):
        config = _LockoutConfig(6, NOW + 60, start=NOW)
        ns, armed = _resume(lock_mod, monkeypatch, config, 86_400.0)
        assert armed == []
        assert ns._fail_count == 6
        assert config.writes == [(6, 0.0, None)]

    def test_genuine_lockout_resumes(self, lock_mod, monkeypatch):
        config = _LockoutConfig(5, NOW + 12, start=NOW - 18)
        _ns, armed = _resume(lock_mod, monkeypatch, config, NOW)
        assert armed == [12]

    def test_persist_records_start(self, lock_mod, monkeypatch):
        monkeypatch.setattr(lock_mod.time, 'time', lambda: NOW)
        config = _LockoutConfig(0, 0.0)
        ns = types.SimpleNamespace(_config=config, _fail_count=5)
        lock_mod.LockScreen._persist_lockout_state(ns, NOW + 30)
        lock_mod.LockScreen._persist_lockout_state(ns, 0.0)
        assert config.writes == [(5, NOW + 30, NOW), (5, 0.0, None)]

    def test_config_start_round_trip_and_back_compat(self, isolated_home):
        from config import ShellConfig
        config = ShellConfig()
        config.set_lockout_state(5, NOW + 30, start=NOW)
        fresh = ShellConfig()
        assert fresh.get_lockout_state() == (5, NOW + 30)
        assert fresh.get_lockout_start() == NOW
        config.set_lockout_state(5, NOW + 30)       # legacy writer
        assert ShellConfig().get_lockout_start() == 0.0
        config.data['lock_screen_lockout'] = {'fails': 5, 'until': 1.0,
                                              'start': 'junk'}
        assert config.get_lockout_start() == 0.0


# ---- 3: gestures while blanked-but-unlocked ------------------------------

def _ipc_app(blanked, locked=False):
    import main
    calls = []
    shell = types.SimpleNamespace(
        is_locked=lambda: locked,
        _show_lock_screen=lambda: calls.append('lock'),
        _show_shade=lambda: calls.append('shade'),
        _show_switcher=lambda: calls.append('switcher'),
        _show_keyboard=lambda: calls.append('keyboard'),
        _handle_back=lambda: calls.append('back'),
        go_home=lambda: calls.append('home'),
        switch_adjacent_app=lambda a: calls.append(('adjacent', a)),
        _switcher=None, _shade=None,
        gesture_config=None)
    app = types.SimpleNamespace(
        _shell=shell,
        _display_mgr=types.SimpleNamespace(blanked=blanked),
        _LOCKED_SILENCED=main.XXWMApplication._LOCKED_SILENCED)
    _bind(app, main.XXWMApplication, '_on_ipc_command',
          '_ipc_silenced_while_locked', '_display_blanked',
          '_on_bottom_gesture')
    return main, app, calls


@pytest.fixture
def sync_main_glib(monkeypatch):
    import main
    monkeypatch.setattr(main, 'GLib', types.SimpleNamespace(
        idle_add=lambda fn, *a: (fn(*a), 0)[1],
        SOURCE_REMOVE=False))
    return main


class TestGesturesWhileBlanked:
    GESTURES = ['gesture.home', 'gesture.switcher', 'gesture.shade',
                'gesture.keyboard', 'gesture.back']

    def test_pure_rule(self, sync_main_glib):
        main = sync_main_glib
        for verb in self.GESTURES:
            assert main.gesture_silenced_while_blanked(verb, True)
            assert not main.gesture_silenced_while_blanked(verb, False)
        assert not main.gesture_silenced_while_blanked('lock', True)
        assert not main.gesture_silenced_while_blanked('shade.hide', True)

    @pytest.mark.parametrize('verb', GESTURES)
    def test_gesture_dropped_while_blanked(self, sync_main_glib, verb):
        _main, app, calls = _ipc_app(blanked=True)
        app._on_ipc_command(verb)
        assert calls == []

    def test_gesture_runs_when_lit(self, sync_main_glib):
        _main, app, calls = _ipc_app(blanked=False)
        app._on_ipc_command('gesture.switcher')
        assert calls == ['switcher']

    def test_lock_still_runs_while_blanked(self, sync_main_glib):
        _main, app, calls = _ipc_app(blanked=True)
        app._on_ipc_command('lock')
        assert calls == ['lock']

    def test_bottom_strip_sideways_dropped_while_blanked(
            self, sync_main_glib):
        from bottom_gesture import NEXT
        _main, app, calls = _ipc_app(blanked=True)
        app._on_bottom_gesture(NEXT)
        assert calls == []
        app._display_mgr.blanked = False
        app._on_bottom_gesture(NEXT)
        assert calls == [('adjacent', NEXT)]

    def test_no_display_manager_reads_lit(self, sync_main_glib):
        _main, app, _calls = _ipc_app(blanked=False)
        del app._display_mgr
        assert app._display_blanked() is False


# ---- logind inhibitor (shared by 4 and 5) --------------------------------

class _FdList:
    def __init__(self, fd):
        self.fd = fd

    def get(self, index):
        assert index == 0
        return self.fd


class _FakeLogind:
    """Records Inhibit calls; reply() completes the oldest in flight with
    a real pipe fd so close-on-release is observable."""

    def __init__(self):
        self.calls = []
        self.pending = []
        self.fds = []

    def call_with_unix_fd_list(self, name, path, iface, method, params,
                               reply_type, flags, timeout, fd_list,
                               cancellable, callback, user_data):
        self.calls.append((iface, method, params))
        self.pending.append(callback)

    def reply(self):
        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        self.fds.append(read_fd)
        callback = self.pending.pop(0)
        callback(self, read_fd, None)
        return read_fd

    def call_with_unix_fd_list_finish(self, result):
        return types.SimpleNamespace(unpack=lambda: (0,)), _FdList(result)


def _fd_open(fd):
    try:
        os.fstat(fd)
        return True
    except OSError:
        return False


def _inhibitor(what='sleep', why='why', mode='delay'):
    import display_manager
    bus = _FakeLogind()
    inh = display_manager.LogindInhibitor(what, why, mode, bus=bus)
    inh._call_args = lambda: ((what, 'XX-WM', why, mode), '(h)', 0)
    return inh, bus


class TestLogindInhibitor:
    def test_acquire_calls_inhibit_and_holds_fd(self):
        inh, bus = _inhibitor(mode='delay')
        inh.acquire()
        inh.acquire()   # in flight: no second call
        assert bus.calls == [('org.freedesktop.login1.Manager', 'Inhibit',
                              ('sleep', 'XX-WM', 'why', 'delay'))]
        fd = bus.reply()
        assert inh.held and _fd_open(fd)
        inh.release()
        assert not inh.held and not _fd_open(fd)
        inh.release()   # idempotent

    def test_release_while_in_flight_drops_late_fd(self):
        inh, bus = _inhibitor()
        inh.acquire()
        inh.release()
        fd = bus.reply()
        assert not inh.held and not _fd_open(fd)

    def test_no_bus_fails_soft(self):
        import display_manager
        inh = display_manager.LogindInhibitor('sleep', 'x', 'delay')

        def _boom():
            raise RuntimeError('no system bus')
        inh._get_bus = _boom
        inh.acquire()
        assert not inh.held
        inh.release()

    def test_refused_call_fails_soft(self):
        inh, bus = _inhibitor()

        def _refuse(_result):
            raise RuntimeError('access denied')
        bus.call_with_unix_fd_list_finish = _refuse
        inh.acquire()
        bus.pending.pop(0)(bus, None, None)
        assert not inh.held
        inh.acquire()   # may retry later
        assert len(bus.calls) == 2


# ---- 4: lock before suspend ----------------------------------------------

class _Delay:
    def __init__(self):
        self.events = []

    def acquire(self):
        self.events.append('acquire')

    def release(self):
        self.events.append('release')


class _FrameClock:
    def __init__(self):
        self.handlers = {}

    def connect(self, signal, cb):
        self.handlers[signal] = cb
        return 41

    def disconnect(self, _hid):
        self.handlers.clear()

    def paint(self):
        cb = self.handlers.get('after-paint')
        if cb is not None:
            cb(self)


@pytest.fixture
def sleep_app(monkeypatch):
    import main
    rec = {'idle': [], 'timeouts': []}
    monkeypatch.setattr(main, 'GLib', types.SimpleNamespace(
        idle_add=lambda fn, *a: rec['idle'].append(fn) or 1,
        timeout_add=lambda ms, fn, *a: rec['timeouts'].append((ms, fn)) or 2,
        SOURCE_REMOVE=False))
    events = []
    clock = _FrameClock()
    lock = types.SimpleNamespace(
        get_frame_clock=lambda: clock,
        queue_draw=lambda: events.append('queue_draw'))
    state = {'locked': False}

    def _show():
        events.append('show_lock')
        state['locked'] = True
    shell = types.SimpleNamespace(
        is_locked=lambda: state['locked'], _show_lock_screen=_show,
        _lock_screen=lock)
    delay = _Delay()
    delay.events = events
    app = types.SimpleNamespace(_shell=shell, _sleep_delay=delay)
    _bind(app, main.XXWMApplication, '_on_sleep', '_lock_for_sleep',
          '_release_sleep_delay')
    return main, app, rec, events, clock, state


class TestLockBeforeSuspend:
    def test_release_waits_for_lock_paint(self, sleep_app):
        _main, app, rec, events, clock, _state = sleep_app
        app._on_sleep(True)
        assert events == []                 # lock runs on the main loop
        rec['idle'].pop(0)()
        assert events == ['show_lock', 'queue_draw']
        clock.paint()
        assert events == ['show_lock', 'queue_draw', 'release']
        assert clock.handlers == {}

    def test_safety_timeout_releases_when_no_paint(self, sleep_app):
        _main, app, rec, events, _clock, _state = sleep_app
        app._on_sleep(True)
        rec['idle'].pop(0)()
        (ms, fn), = rec['timeouts']
        assert 0 < ms <= 2000
        fn()
        assert events[-1] == 'release'

    def test_already_locked_releases_at_once(self, sleep_app):
        _main, app, rec, events, _clock, state = sleep_app
        state['locked'] = True
        app._on_sleep(True)
        rec['idle'].pop(0)()
        assert events == ['show_lock', 'release']

    def test_resume_retakes_delay(self, sleep_app, monkeypatch):
        import settings_display
        monkeypatch.setattr(settings_display, 'refresh_night_scheduler',
                            lambda _why: None)
        _main, app, _rec, events, _clock, _state = sleep_app
        app._on_sleep(False)
        assert events == ['acquire']

    def test_no_shell_releases_immediately(self, sleep_app):
        _main, app, rec, events, _clock, _state = sleep_app
        app._shell = None
        app._on_sleep(True)
        assert rec['idle'] == [] and events == ['release']

    def test_no_logind_is_harmless(self, sleep_app):
        _main, app, rec, events, _clock, _state = sleep_app
        app._sleep_delay = None
        app._on_sleep(True)
        rec['idle'].pop(0)()
        assert events == ['show_lock', 'queue_draw']
        rec['timeouts'][0][1]()

    def test_setup_takes_delay_inhibitor(self):
        src = (Path(__file__).parent.parent / 'launcher' / 'src' /
               'main.py').read_text(encoding='utf-8')
        assert "'sleep', 'Lock screen before suspend', 'delay'" in src
        assert 'self._sleep_delay = _setup_logind(self._on_sleep)' in src


# ---- 5: no suspend during a call -----------------------------------------

class _Row:
    def __init__(self):
        self.visible = True

    def set_visible(self, value):
        self.visible = bool(value)


class TestSuspendDuringCall:
    def test_rows_drop_suspend_while_call_live(self):
        import power_menu
        assert power_menu.visible_power_rows('yes', call_live=True) == [
            'lock', 'reboot', 'poweroff', 'cancel']
        assert power_menu.visible_power_rows(None, call_live=True) == [
            'lock', 'reboot', 'poweroff', 'cancel']
        assert 'suspend' in power_menu.visible_power_rows('yes')

    def test_menu_row_follows_call_state(self):
        import power_menu
        live = {'on': True}
        menu = types.SimpleNamespace(_suspend_row=_Row(),
                                     _call_live=lambda: live['on'])
        power_menu.PowerMenu._on_can_suspend(menu, 'yes')
        assert menu._suspend_row.visible is False
        live['on'] = False
        power_menu.PowerMenu._on_can_suspend(menu, menu._can_suspend)
        assert menu._suspend_row.visible is True

    def test_broken_probe_never_breaks_menu(self):
        import power_menu

        def _boom():
            raise RuntimeError
        menu = types.SimpleNamespace(_suspend_row=_Row(), _call_live=_boom)
        power_menu.PowerMenu._on_can_suspend(menu, 'yes')
        assert menu._suspend_row.visible is True

    def test_execute_refuses_suspend_during_call(self, monkeypatch):
        import power_menu
        fired = []
        monkeypatch.setattr(power_menu, 'power_action_async',
                            lambda cmd, on_done=None: fired.append(cmd))
        menu = types.SimpleNamespace(
            _call_live=lambda: True, _dismiss=lambda: None,
            _on_power_result=None)
        power_menu.PowerMenu._execute(menu, 'suspend')
        power_menu.PowerMenu._execute(menu, 'reboot')
        assert fired == ['reboot']

    def test_failed_power_action_logs_without_a_stock_dialog(self, caplog):
        import logging
        import power_menu
        src = Path(power_menu.__file__).read_text(encoding='utf-8')
        assert 'MessageDialog' not in src
        with caplog.at_level(logging.WARNING, logger=power_menu._log.name):
            power_menu.PowerMenu._on_power_result(
                types.SimpleNamespace(), False, 'Interactive auth needed')
        assert 'Interactive auth needed' in caplog.text

    def test_main_wires_call_probe(self):
        src = (Path(__file__).parent.parent / 'launcher' / 'src' /
               'main.py').read_text(encoding='utf-8')
        assert 'call_live=self._shell._call_live' in src


@pytest.fixture
def dm_bare(monkeypatch):
    """A DisplayManager shell without __init__ (no evdev, no backends)."""
    import display_manager
    made = []

    class _Inh:
        def __init__(self, what, why, mode, bus=None):
            self.args = (what, why, mode)
            self.events = []
            made.append(self)

        def acquire(self):
            self.events.append('acquire')

        def release(self):
            self.events.append('release')

    monkeypatch.setattr(display_manager, 'LogindInhibitor', _Inh)
    mgr = object.__new__(display_manager.DisplayManager)
    return display_manager, mgr, made


class TestCallSleepBlock:
    def test_block_inhibitor_follows_reasons(self, dm_bare):
        _dm, mgr, made = dm_bare
        mgr.inhibit_sleep('call', False)
        assert made == []
        mgr.inhibit_sleep('call', True)
        mgr.inhibit_sleep('call', True)
        (inh,) = made
        assert inh.args == ('sleep', 'Call in progress', 'block')
        assert inh.events == ['acquire', 'acquire']   # idempotent acquire
        mgr.inhibit_sleep('call', False)
        assert inh.events[-1] == 'release'

    def test_call_wiring_holds_block_for_live_call(self, dm_bare):
        from call_wiring import CallWiring
        from modem_monitor import _STATE_TERMINATED
        _dm, mgr, made = dm_bare
        mgr._inhibitors = set()
        win = types.SimpleNamespace(
            _display_mgr=mgr, _call_was_live=False, _blanked=False,
            is_locked=lambda: False)
        live = types.SimpleNamespace(state=_STATE_TERMINATED + 1)
        ended = types.SimpleNamespace(state=_STATE_TERMINATED)
        CallWiring(win)._sync_call_inhibit([live])
        assert made[0].events == ['acquire']
        CallWiring(win)._sync_call_inhibit([ended])
        assert made[0].events == ['acquire', 'release']

    def test_old_display_manager_without_sleep_hook(self):
        from call_wiring import CallWiring
        held = []
        win = types.SimpleNamespace(
            _display_mgr=types.SimpleNamespace(
                inhibit_idle=lambda r, a: held.append((r, a))),
            _call_was_live=False, _blanked=False, is_locked=lambda: False)
        CallWiring(win)._sync_call_inhibit([])
        assert held == [('call', False)]


# ---- 6: KEY_WAKEUP --------------------------------------------------------

class _SyncGLib:
    SOURCE_REMOVE = False

    @staticmethod
    def idle_add(fn, *a):
        fn(*a)
        return 0

    @staticmethod
    def timeout_add(*_a):
        return 1

    timeout_add_seconds = timeout_add

    @staticmethod
    def source_remove(_tid):
        return None


@pytest.fixture
def wake_dm(monkeypatch):
    import display_manager
    monkeypatch.setattr(display_manager, 'GLib', _SyncGLib)
    monkeypatch.setattr(display_manager, '_all_event_devices',
                        lambda: ['/dev/input/eventX'])
    monkeypatch.setattr(display_manager, '_watch_evdev', lambda *a, **k: None)
    monkeypatch.setattr(display_manager, '_detect_fp_node',
                        lambda: '/dev/input/event9')
    wakes, idles = [], []
    monkeypatch.setattr(display_manager.DisplayManager, '_wake',
                        lambda self: wakes.append(True))
    mgr = display_manager.DisplayManager()
    monkeypatch.setattr(mgr, 'reset_idle', lambda: idles.append(True))
    return display_manager, mgr, wakes, idles


class TestKeyWakeup:
    def test_wakeup_key_wakes_blanked_display(self, wake_dm):
        dm, mgr, wakes, _idles = wake_dm
        mgr._blanked = True
        mgr._on_event('/dev/input/event2', dm.EV_KEY, dm.KEY_WAKEUP, 1)
        assert wakes == [True]

    def test_wakeup_key_release_is_noop(self, wake_dm):
        dm, mgr, wakes, idles = wake_dm
        mgr._blanked = True
        mgr._on_event('/dev/input/event2', dm.EV_KEY, dm.KEY_WAKEUP, 0)
        assert wakes == [] and idles == []

    def test_wakeup_key_while_lit_only_resets_idle(self, wake_dm):
        dm, mgr, wakes, idles = wake_dm
        mgr._on_event('/dev/input/event2', dm.EV_KEY, dm.KEY_WAKEUP, 1)
        assert wakes == [] and idles == [True]

    def test_reader_filter_posts_wakeup_presses(self, wake_dm):
        import struct
        dm = wake_dm[0]
        data = b''.join(
            struct.pack(dm._EV_FMT, 0, 0, dm.EV_KEY, dm.KEY_WAKEUP, v)
            for v in (1, 0))
        wanted, _ = dm.filter_evdev_data(
            data, '/dev/input/event2', '/dev/input/event9', 0.0)
        assert [w[2:] for w in wanted] == [(dm.KEY_WAKEUP, 1)]
        assert dm.KEY_WAKEUP == 143
