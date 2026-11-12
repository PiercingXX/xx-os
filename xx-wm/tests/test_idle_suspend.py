"""Idle inhibit (org.freedesktop.ScreenSaver, Caffeine) and automatic
suspend: pure decisions, the cookie table, the D-Bus method surface with
fake invocations/buses, the controller with fake timers, and the shell
wiring. No real bus, no logind, no main loop."""
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import auto_suspend  # noqa: E402
import idle_inhibit  # noqa: E402
import screensaver_service as ss  # noqa: E402


class FakeConfig:
    def __init__(self, **data):
        self.data = dict(data)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class FakeTimers:
    def __init__(self):
        self.next_id = 1
        self.live = {}

    def add(self, seconds, fn):
        sid = self.next_id
        self.next_id += 1
        self.live[sid] = (seconds, fn)
        return sid

    def remove(self, sid):
        self.live.pop(sid, None)

    def delays(self):
        return sorted(s for s, _fn in self.live.values())

    def fire(self, seconds=None):
        """Run (and drop) the one pending timer, optionally of a delay."""
        for sid, (secs, fn) in sorted(self.live.items()):
            if seconds is None or secs == seconds:
                del self.live[sid]
                return fn()
        raise AssertionError('no timer for %r in %r' % (seconds,
                                                         self.delays()))


class FakeDisplay:
    def __init__(self):
        self.reasons = set()
        self.wakes = 0

    def inhibit_idle(self, reason, active):
        (self.reasons.add if active else self.reasons.discard)(reason)

    def wake(self):
        self.wakes += 1


# ---------------------------------------------------------------- hub

class TestHub:
    def test_reasons_replay_on_attach(self):
        hub = idle_inhibit.IdleInhibitHub(timeout_add=lambda *a: 1,
                                          source_remove=lambda s: None)
        hub.set('screensaver', True)
        display = FakeDisplay()
        hub.attach(display)
        assert display.reasons == {'screensaver'}
        hub.set('screensaver', False)
        assert display.reasons == set()

    def test_listener_fires_only_on_change(self):
        hub = idle_inhibit.IdleInhibitHub()
        seen = []
        hub.add_listener(lambda: seen.append(1))
        hub.set('x', True)
        hub.set('x', True)
        hub.set('x', False)
        assert seen == [1, 1]

    def test_caffeine_timeout_by_timer_and_by_clock(self):
        timers = FakeTimers()
        now = [1000.0]
        hub = idle_inhibit.IdleInhibitHub(timeout_add=timers.add,
                                          source_remove=timers.remove,
                                          clock=lambda: now[0])
        display = FakeDisplay()
        hub.attach(display)
        hub.set_caffeine(True, 30)
        assert hub.caffeine_active() and display.reasons == {'caffeine'}
        assert timers.delays() == [1800]
        timers.fire()
        assert not hub.caffeine_active() and display.reasons == set()
        # A missed timer still expires on the next read.
        hub.set_caffeine(True, 1)
        timers.live.clear()
        now[0] += 61
        assert 'caffeine' not in hub.active_reasons()
        assert display.reasons == set()

    def test_caffeine_without_timeout_and_clear(self):
        timers = FakeTimers()
        hub = idle_inhibit.IdleInhibitHub(timeout_add=timers.add,
                                          source_remove=timers.remove)
        hub.set_caffeine(True, 0)
        assert timers.delays() == []
        assert hub.active_reasons() == {'caffeine'}
        hub.clear_caffeine('lock')
        assert hub.active_reasons() == set()
        hub.clear_caffeine('again')     # no-op when off

    def test_caffeine_minutes_config(self):
        assert idle_inhibit.caffeine_minutes(FakeConfig()) == 60
        assert idle_inhibit.caffeine_minutes(
            FakeConfig(caffeine_minutes=0)) == 0
        assert idle_inhibit.caffeine_minutes(
            FakeConfig(caffeine_minutes='junk')) == 60
        assert idle_inhibit.caffeine_minutes(
            FakeConfig(caffeine_minutes=99999)) == 1440
        assert idle_inhibit.caffeine_minutes(None) == 60

    def test_wake_forwards_to_display(self):
        hub = idle_inhibit.IdleInhibitHub()
        hub.wake()                      # no display: no crash
        display = FakeDisplay()
        hub.attach(display)
        hub.wake()
        assert display.wakes == 1


# ---------------------------------------------------- inhibitor table

class TestInhibitorTable:
    def test_cookies_unique_nonzero_and_sender_scoped(self):
        table = ss.InhibitorTable()
        a = table.add(':1.5', 'mpv', 'video')
        b = table.add(':1.6', 'firefox', 'video')
        assert a and b and a != b and table.active
        assert not table.remove(a, ':1.6')      # someone else's cookie
        assert table.remove(a, ':1.5')
        assert not table.remove(a, ':1.5')      # already gone
        assert [e.cookie for e in table.entries()] == [b]

    def test_drop_sender(self):
        table = ss.InhibitorTable()
        table.add(':1.5', 'a', '')
        table.add(':1.5', 'b', '')
        keep = table.add(':1.9', 'c', '')
        assert len(table.drop_sender(':1.5')) == 2
        assert [e.cookie for e in table.entries()] == [keep]
        assert not table.has_sender(':1.5')

    def test_cookie_wraps_and_skips_live(self):
        table = ss.InhibitorTable()
        table._next = 0xFFFFFFFF
        top = table.add(':1.1', 'a', '')
        assert top == 0xFFFFFFFF
        assert table.add(':1.1', 'b', '') == 1


# ------------------------------------------------- screensaver service

class FakeInvocation:
    def __init__(self):
        self.value = 'unset'
        self.error = None

    def return_value(self, value):
        self.value = value

    def return_dbus_error(self, name, message):
        self.error = (name, message)


class FakeBus:
    def __init__(self):
        self.signals = []
        self.unregistered = []

    def emit_signal(self, dest, path, iface, name, params):
        self.signals.append((path, iface, name, params.unpack()))

    def unregister_object(self, reg_id):
        self.unregistered.append(reg_id)


def _service(blanked=False):
    changes = []
    activity = []
    state = {'blanked': blanked}
    svc = ss.ScreenSaverService(
        on_inhibit_changed=changes.append,
        is_blanked=lambda: state['blanked'],
        on_user_activity=lambda: activity.append(1),
        clock=lambda: 100.0)
    return svc, changes, activity, state


def _call(svc, method, sender=':1.7', args=None):
    from gi.repository import GLib
    params = GLib.Variant(*args) if args else GLib.Variant('()', ())
    inv = FakeInvocation()
    svc._on_method_call(None, sender, ss.OBJECT_PATHS[0], ss.INTERFACE,
                        method, params, inv)
    return inv


class TestScreenSaverService:
    def test_object_paths_and_interface(self):
        assert ss.OBJECT_PATHS == ('/org/freedesktop/ScreenSaver',
                                   '/ScreenSaver')
        assert ss.BUS_NAME == 'org.freedesktop.ScreenSaver'
        for name in ('Inhibit', 'UnInhibit', 'GetActive',
                     'SimulateUserActivity', 'ActiveChanged'):
            assert name in ss.INTROSPECTION_XML

    def test_introspection_parses(self):
        from gi.repository import Gio
        node = Gio.DBusNodeInfo.new_for_xml(ss.INTROSPECTION_XML)
        assert node.lookup_interface(ss.INTERFACE) is not None

    def test_inhibit_uninhibit_over_dbus_methods(self):
        svc, changes, _act, _st = _service()
        inv = _call(svc, 'Inhibit', args=('(ss)', ('mpv', 'Playing video')))
        (cookie,) = inv.value.unpack()
        assert cookie > 0 and changes == [True]
        inv = _call(svc, 'Inhibit', args=('(ss)', ('mpv', 'second')))
        (cookie2,) = inv.value.unpack()
        assert changes == [True]                 # still one held state
        _call(svc, 'UnInhibit', args=('(u)', (cookie,)))
        assert changes == [True]
        # Another sender can't release it.
        _call(svc, 'UnInhibit', sender=':1.99', args=('(u)', (cookie2,)))
        assert changes == [True]
        inv = _call(svc, 'UnInhibit', args=('(u)', (cookie2,)))
        assert inv.value is None and changes == [True, False]

    def test_vanished_sender_drops_its_cookies(self):
        svc, changes, _act, _st = _service()
        svc.inhibit(':1.7', 'firefox', 'video')
        svc.inhibit(':1.8', 'phoc', 'Inhibiting idle session')
        svc.sender_vanished(':1.7')
        assert changes == [True]
        svc.sender_vanished(':1.8')
        assert changes == [True, False]
        svc.sender_vanished(':1.8')              # idempotent

    def test_get_active_follows_blank_and_active_time(self):
        svc, _c, _a, state = _service()
        inv = _call(svc, 'GetActive')
        assert inv.value.unpack() == (False,)
        state['blanked'] = True
        svc.set_active(True)
        assert _call(svc, 'GetActive').value.unpack() == (True,)
        svc._clock = lambda: 130.0
        assert _call(svc, 'GetActiveTime').value.unpack() == (30,)

    def test_simulate_user_activity(self):
        svc, _c, activity, _st = _service()
        inv = _call(svc, 'SimulateUserActivity')
        assert inv.value is None and activity == [1]

    def test_unknown_method_errors(self):
        svc, _c, _a, _st = _service()
        inv = _call(svc, 'Lock')
        assert inv.error[0].endswith('UnknownMethod')

    def test_active_changed_emitted_on_both_paths_only_when_owner(self):
        svc, _c, _a, _st = _service()
        bus = FakeBus()
        svc._bus = bus
        svc.set_active(True)                     # not owner yet: silent
        assert bus.signals == []
        svc.set_active(False)
        svc._owns_name = True
        svc.set_active(True)
        svc.set_active(True)                     # no change: no signal
        assert [(p, n, a) for p, _i, n, a in bus.signals] == [
            ('/org/freedesktop/ScreenSaver', 'ActiveChanged', (True,)),
            ('/ScreenSaver', 'ActiveChanged', (True,))]

    def test_name_already_owned_backs_off(self):
        svc, changes, _a, _st = _service()
        bus = FakeBus()
        svc._bus = bus
        svc._reg_ids = [11, 12]
        svc.inhibit(':1.7', 'x', '')
        svc._on_name_lost(bus, ss.BUS_NAME)
        assert bus.unregistered == [11, 12]
        assert not svc.owns_name and not svc.table.active
        assert changes == [True, False]

    def test_start_without_bus_is_soft(self, monkeypatch):
        svc, _c, _a, _st = _service()
        svc._on_bus_ready(None, object())        # bad result: logged only
        assert not svc.owns_name


# ----------------------------------------------------- pure decisions

class TestDecisions:
    def test_suspend_after_defaults_off(self):
        assert auto_suspend.DEFAULT_SUSPEND_AFTER == 0
        assert auto_suspend.suspend_after_secs(FakeConfig()) == 0
        assert auto_suspend.suspend_after_secs(
            FakeConfig(suspend_after=300)) == 300
        assert auto_suspend.suspend_after_secs(
            FakeConfig(suspend_after=-5)) == 0
        assert auto_suspend.suspend_after_secs(
            FakeConfig(suspend_after='x')) == 0
        assert auto_suspend.suspend_after_secs(None) == 0

    def test_labels(self):
        assert [auto_suspend.suspend_after_label(s)
                for s in (0, 60, 300, 900, 120, 45)] == [
            'Off', '1 min', '5 min', '15 min', '2 min', '45 s']

    def test_backoff_caps_at_45(self):
        seq, prev = [], None
        for _ in range(6):
            prev = auto_suspend.next_backoff(prev)
            seq.append(prev)
        assert seq == [10, 20, 40, 45, 45, 45]

    def test_parse_clock_state(self):
        state = {'alarms': [], 'ringing': None,
                 'next': {'id': 3, 'fire_at_ms': 9_000_000},
                 'timers': [{'state': 'RUNNING', 'ends_at_ms': 5_000_000},
                            {'state': 'PAUSED', 'ends_at_ms': 1}]}
        info = auto_suspend.parse_clock_state(json.dumps(state))
        assert info == auto_suspend.AlarmInfo(True, 5_000_000, False)
        assert auto_suspend.parse_clock_state(json.dumps(
            {'next': None, 'timers': [], 'ringing': None})) \
            == auto_suspend.NO_ALARMS
        assert auto_suspend.parse_clock_state(json.dumps(
            {'ringing': {'id': 1}})).ringing
        assert not auto_suspend.parse_clock_state('garbage').known

    def test_alarm_blocker(self):
        now = 1_000_000
        info = auto_suspend.AlarmInfo
        f = auto_suspend.alarm_blocker
        assert f(auto_suspend.NO_ALARMS, now, False) is None
        assert f(auto_suspend.ALARMS_UNKNOWN, now, True) \
            == 'alarm state unknown'
        assert f(info(True, None, True), now, True) == 'alarm ringing'
        assert f(info(True, now + 60_000, None), now, True) == 'alarm soon'
        far = info(True, now + 8 * 3600_000, False)
        assert f(far, now, False) == 'alarm armed (no RTC wake)'
        assert f(far, now, True) is None

    def test_blockers(self):
        ctx = auto_suspend.SuspendContext
        assert auto_suspend.blockers(ctx(blanked=True)) == []
        assert auto_suspend.blockers(ctx(blanked=False)) == ['display on']
        got = auto_suspend.blockers(ctx(
            blanked=True, call_live=True,
            inhibitors=frozenset({'caffeine', 'screensaver'}),
            media_playing=True, charging=True, flag_file=True,
            debug_clients=1, alarm=auto_suspend.AlarmInfo(True, None, True)))
        assert got == ['call', 'inhibited: caffeine, screensaver',
                       'media playing', 'charging', 'no-suspend flag',
                       'wireless debugging session', 'alarm ringing']
        assert auto_suspend.blockers(ctx(
            blanked=True, charging=True, allow_on_charger=True)) == []

    def test_flag_file(self, tmp_path):
        env = {'XDG_RUNTIME_DIR': str(tmp_path)}
        assert not auto_suspend.flag_present(env)
        (tmp_path / 'xx-wm').mkdir()
        (tmp_path / 'xx-wm' / 'no-suspend').write_text('')
        assert auto_suspend.flag_present(env)
        assert not auto_suspend.flag_present({})


# --------------------------------------------------------- controller

def _controller(**over):
    timers = FakeTimers()
    rec = {'suspends': [], 'clock_calls': 0, 'modem': 0, 'rtc': []}
    state = {'blanked': True, 'alarm': auto_suspend.NO_ALARMS,
             'call': False, 'reasons': set(), 'media': False,
             'charging': False, 'flag': False, 'clients': 0}

    def query_clock(cb):
        rec['clock_calls'] += 1
        cb(state['alarm'])

    def suspend(cb):
        rec['suspends'].append(cb)

    kwargs = dict(
        config=FakeConfig(suspend_after=60),
        is_blanked=lambda: state['blanked'],
        call_live=lambda: state['call'],
        inhibit_reasons=lambda: state['reasons'],
        media_playing=lambda: state['media'],
        charging=lambda: state['charging'],
        debug_clients=lambda: state['clients'],
        flag_present=lambda: state['flag'],
        query_clock=query_clock,
        suspend=suspend,
        modem_check=lambda: rec.__setitem__('modem', rec['modem'] + 1),
        timeout_add=timers.add,
        source_remove=timers.remove,
        wall_ms=lambda: 1_000_000,
    )
    kwargs.update(over)
    ctl = auto_suspend.AutoSuspendController(**kwargs)
    return ctl, timers, rec, state


class TestController:
    def test_off_by_default_never_arms(self):
        ctl, timers, rec, _st = _controller(config=FakeConfig())
        ctl.note_blank(True)
        assert timers.delays() == [] and rec['suspends'] == []

    def test_blank_arms_then_suspends(self):
        ctl, timers, rec, _st = _controller()
        ctl.note_blank(True)
        assert timers.delays() == [60]
        timers.fire(60)
        assert rec['clock_calls'] == 1 and len(rec['suspends']) == 1
        assert ctl.in_flight
        rec['suspends'][0](True, '')
        assert timers.delays() == []

    def test_wake_cancels_and_stale_replies_are_dropped(self):
        pending = []
        ctl, timers, rec, state = _controller(
            query_clock=lambda cb: pending.append(cb))
        ctl.note_blank(True)
        timers.fire(60)
        state['blanked'] = False
        ctl.note_blank(False)
        pending[0](auto_suspend.NO_ALARMS)       # late clock reply
        assert rec['suspends'] == [] and timers.delays() == []

    def test_never_suspends_when_lit_even_if_timer_fires(self):
        ctl, timers, rec, state = _controller()
        ctl.note_blank(True)
        state['blanked'] = False                 # missed wake report
        timers.fire(60)
        assert rec['suspends'] == []

    @pytest.mark.parametrize('key,value', [
        ('call', True), ('reasons', {'screensaver'}), ('media', True),
        ('charging', True), ('flag', True), ('clients', 1),
        ('alarm', auto_suspend.AlarmInfo(True, 1_000_000 + 3600_000,
                                         False)),
        ('alarm', auto_suspend.ALARMS_UNKNOWN),
    ])
    def test_blocked_backs_off_then_proceeds(self, key, value):
        ctl, timers, rec, state = _controller()
        clear = state[key]
        state[key] = value
        ctl.note_blank(True)
        timers.fire(60)
        assert rec['suspends'] == [] and timers.delays() == [10]
        timers.fire(10)
        assert timers.delays() == [20]
        timers.fire(20)
        timers.fire(40)
        assert timers.delays() == [45]
        state[key] = clear
        timers.fire(45)
        assert len(rec['suspends']) == 1

    def test_charging_allowed_by_setting(self):
        ctl, timers, rec, state = _controller(
            config=FakeConfig(suspend_after=60, suspend_when_charging=True))
        state['charging'] = True
        ctl.note_blank(True)
        timers.fire(60)
        assert len(rec['suspends']) == 1

    def test_refused_suspend_retries_after_cap(self):
        ctl, timers, rec, _st = _controller()
        ctl.note_blank(True)
        timers.fire(60)
        rec['suspends'][0](False, 'Operation inhibited')
        assert not ctl.in_flight and timers.delays() == [45]
        timers.fire(45)
        assert len(rec['suspends']) == 2

    @pytest.mark.parametrize('value', ['no', 'na'])
    def test_logind_that_cannot_suspend_arms_nothing(self, value):
        """FuriOS sets AllowSuspend=no: no RTC wake, no Suspend call, no
        retry loop; the next blank asks logind again."""
        asked = []
        ctl, timers, rec, state = _controller(
            can_suspend=lambda cb: (asked.append(1), cb(value)),
            rtc_wake=lambda epoch, cb: rec['rtc'].append(epoch))
        state['alarm'] = auto_suspend.AlarmInfo(
            True, 1_000_000 + 3600_000, False)
        ctl.note_blank(True)
        timers.fire(60)
        assert asked == [1] and rec['clock_calls'] == 0
        assert rec['suspends'] == [] and rec['rtc'] == []
        assert timers.delays() == []

    @pytest.mark.parametrize('value', ['yes', 'challenge', None])
    def test_logind_that_can_suspend_goes_ahead(self, value):
        ctl, timers, rec, _st = _controller(
            can_suspend=lambda cb: cb(value))
        ctl.note_blank(True)
        timers.fire(60)
        assert len(rec['suspends']) == 1

    def test_resume_while_blank_waits_grace_and_checks_modem(self):
        ctl, timers, rec, _st = _controller()
        ctl.note_blank(True)
        timers.fire(60)
        rec['suspends'][0](True, '')
        ctl.note_resumed()
        assert sorted(timers.delays()) == [auto_suspend.MODEM_CHECK_DELAY_S,
                                           auto_suspend.RESUME_GRACE_S]
        timers.fire(auto_suspend.MODEM_CHECK_DELAY_S)
        assert rec['modem'] == 1
        timers.fire(auto_suspend.RESUME_GRACE_S)
        assert len(rec['suspends']) == 2

    def test_resume_while_lit_does_not_rearm(self):
        ctl, timers, _rec, state = _controller()
        state['blanked'] = False
        ctl.note_resumed()
        assert timers.delays() == [auto_suspend.MODEM_CHECK_DELAY_S]

    def test_rtc_wake_lets_armed_alarm_through(self):
        wakes, clears = [], []
        ctl, timers, rec, state = _controller(
            rtc_wake=lambda at, cb: wakes.append((at, cb)),
            rtc_clear=lambda: clears.append(1))
        state['alarm'] = auto_suspend.AlarmInfo(True, 1_000_000 + 3600_000,
                                                False)
        ctl.note_blank(True)
        timers.fire(60)
        # Suspend waits for the helper's answer.
        assert rec['suspends'] == [] and ctl.in_flight
        assert [at for at, _cb in wakes] == [
            (1_000_000 + 3600_000) // 1000 - auto_suspend.RTC_WAKE_LEAD_S]
        wakes[0][1](True)
        assert len(rec['suspends']) == 1
        rec['suspends'][0](True, '')
        ctl.note_resumed()
        assert clears == [1]
        ctl.note_resumed()                       # cleared once only
        assert clears == [1]

    def test_rtc_wake_failure_falls_back_to_blocking(self):
        ctl, timers, rec, state = _controller(
            rtc_wake=lambda at, cb: cb(False))
        state['alarm'] = auto_suspend.AlarmInfo(True, 1_000_000 + 3600_000,
                                                False)
        ctl.note_blank(True)
        timers.fire(60)
        assert rec['suspends'] == [] and timers.delays() == [10]
        assert not ctl.in_flight
        assert ctl._last_blockers == ['alarm armed (RTC wake failed)']

    def test_rtc_wake_raising_falls_back(self):
        def boom(at, cb):
            raise RuntimeError('no bus')
        ctl, timers, rec, state = _controller(rtc_wake=boom)
        state['alarm'] = auto_suspend.AlarmInfo(True, 1_000_000 + 3600_000,
                                                False)
        ctl.note_blank(True)
        timers.fire(60)
        assert rec['suspends'] == [] and timers.delays() == [10]

    def test_rtc_wake_not_needed_without_alarm(self):
        wakes = []
        ctl, timers, rec, _st = _controller(
            rtc_wake=lambda at, cb: wakes.append(at))
        ctl.note_blank(True)
        timers.fire(60)
        assert wakes == [] and len(rec['suspends']) == 1

    def test_alarm_soon_blocks_even_with_rtc(self):
        wakes = []
        ctl, timers, rec, state = _controller(
            rtc_wake=lambda at, cb: wakes.append(at))
        state['alarm'] = auto_suspend.AlarmInfo(True, 1_000_000 + 90_000,
                                                False)
        ctl.note_blank(True)
        timers.fire(60)
        assert wakes == [] and rec['suspends'] == []

    def test_wake_during_arming_clears_and_never_suspends(self):
        wakes, clears = [], []
        ctl, timers, rec, state = _controller(
            rtc_wake=lambda at, cb: wakes.append(cb),
            rtc_clear=lambda: clears.append(1))
        state['alarm'] = auto_suspend.AlarmInfo(True, 1_000_000 + 3600_000,
                                                False)
        ctl.note_blank(True)
        timers.fire(60)
        state['blanked'] = False
        ctl.note_blank(False)
        wakes[0](True)                           # late helper reply
        assert rec['suspends'] == [] and clears == [1]

    def test_refused_suspend_clears_rtc(self):
        clears = []
        ctl, timers, rec, state = _controller(
            rtc_wake=lambda at, cb: cb(True),
            rtc_clear=lambda: clears.append(1))
        state['alarm'] = auto_suspend.AlarmInfo(True, 1_000_000 + 3600_000,
                                                False)
        ctl.note_blank(True)
        timers.fire(60)
        rec['suspends'][0](False, 'Operation inhibited')
        assert clears == [1] and timers.delays() == [45]

    def test_rtc_wake_time_capped(self):
        now = 1_000_000_000
        far = now + 5 * 86400_000
        assert auto_suspend.rtc_wake_time(far, now) \
            == now // 1000 + auto_suspend.RTC_WAKE_MAX_AHEAD_S
        assert auto_suspend.rtc_wake_time(now + 3600_000, now) \
            == now // 1000 + 3600 - auto_suspend.RTC_WAKE_LEAD_S

    def test_probe_exceptions_fail_soft(self):
        def boom():
            raise RuntimeError('x')
        ctl, timers, rec, _st = _controller(call_live=boom,
                                            media_playing=boom)
        ctl.note_blank(True)
        timers.fire(60)
        assert len(rec['suspends']) == 1


class FakeHelperClient:
    HELPER_NOT_INSTALLED = 'helper not installed'

    def __init__(self, result=(True, None)):
        self.result = result
        self.calls = []

    def set_wake_alarm(self, epoch, cb):
        self.calls.append(('set', epoch))
        cb(*self.result)

    def clear_wake_alarm(self, cb):
        self.calls.append(('clear',))
        cb(True, None)


class TestHelperRtcWake:
    def test_arm_and_clear_go_through_helper(self):
        client = FakeHelperClient()
        rtc = auto_suspend.HelperRtcWake(client)
        got = []
        rtc.arm(1_800_000_000, got.append)
        rtc.clear()
        assert got == [True]
        assert client.calls == [('set', 1_800_000_000), ('clear',)]

    def test_not_installed_is_remembered(self):
        client = FakeHelperClient((False, 'helper not installed'))
        rtc = auto_suspend.HelperRtcWake(client)
        got = []
        rtc.arm(1, got.append)
        rtc.arm(2, got.append)
        rtc.clear()
        assert got == [False, False] and rtc.unavailable
        assert client.calls == [('set', 1)]

    def test_other_errors_retry(self):
        client = FakeHelperClient((False, 'RTC wake alarm already set'))
        rtc = auto_suspend.HelperRtcWake(client)
        got = []
        rtc.arm(1, got.append)
        rtc.arm(2, got.append)
        assert got == [False, False] and not rtc.unavailable
        assert len(client.calls) == 2

# ------------------------------------------------------------ the tile

class TestCaffeineTile:
    @pytest.fixture(autouse=True)
    def fresh_hub(self, monkeypatch):
        monkeypatch.setattr(idle_inhibit, '_hub', idle_inhibit.IdleInhibitHub(
            timeout_add=lambda *a: 1, source_remove=lambda s: None))

    def test_tile_listed_and_routed(self):
        from test_captions_tile import quick_actions
        tile = next(t for t in quick_actions._TILES if t.key == 'caffeine')
        assert tile.tier == 2 and tile.label == 'Caffeine'
        assert len(tile.label) <= 8
        assert quick_actions.tile_settings_section('caffeine') == 'standby'
        import settings_page
        assert 'standby' in settings_page._SETTINGS_SECTION_KEYS

    def test_state_round_trip_uses_config_minutes(self):
        from test_captions_tile import quick_actions
        assert quick_actions._get_caffeine_state() is False
        quick_actions._set_caffeine_state(True,
                                          FakeConfig(caffeine_minutes=0))
        hub = idle_inhibit.get_hub()
        assert quick_actions._get_caffeine_state() is True
        assert hub._caffeine_deadline is None
        quick_actions._set_caffeine_state(False)
        assert hub.active_reasons() == set()

    def test_panel_binds_live_config(self):
        from test_captions_tile import quick_actions
        panel = object.__new__(quick_actions.QuickActionsPanel)
        panel._hw_gate_cache = {k: False for k in (
            'tailscale', 'auto_br', 'torch', 'record', 'captions',
            'hotspot', 'night', 'location')}
        panel._dnd = panel._focus = panel._nope = None
        panel._als = panel._location = SimpleNamespace(
            available=lambda: False)
        panel._config = FakeConfig(caffeine_minutes=30)
        tile = next(t for t in panel._tiles() if t.key == 'caffeine')
        tile.set_state(True)
        hub = idle_inhibit.get_hub()
        assert hub.caffeine_active() and hub._caffeine_deadline is not None

    def test_power_menu_lock_clears_caffeine(self, monkeypatch):
        import power_menu
        import smart_lock
        monkeypatch.setattr(smart_lock, '_force_pin_until_next_unlock',
                            smart_lock._force_pin_until_next_unlock)
        hub = idle_inhibit.get_hub()
        hub.set_caffeine(True, 0)
        locked = []
        menu = SimpleNamespace(_dismiss=lambda: None,
                               _on_lock=lambda: locked.append(1))
        power_menu.PowerMenu._lock(menu)
        assert locked == [1] and not hub.caffeine_active()


# ------------------------------------------------------ settings rows

class TestSettingsRows:
    def test_choices_and_labels(self):
        import settings_page  # noqa: F401  (import-order cycle)
        import settings_standby
        assert [s for s, _l in settings_standby.SUSPEND_CHOICES] \
            == [0, 60, 300, 900]
        assert settings_standby.SUSPEND_CHOICES \
            == auto_suspend.SUSPEND_AFTER_CHOICES
        assert [settings_standby.caffeine_label(m)
                for m in settings_standby.CAFFEINE_CHOICES] == [
            'After 30 min', 'After 1 hour', 'After 2 hours',
            'When turned off']
        win = SimpleNamespace(config=FakeConfig(suspend_after=300))
        assert settings_standby.suspend_after(win) == 300
        assert settings_standby.caffeine_minutes(win) == 60

    def test_set_suspend_writes_config(self, monkeypatch):
        import settings_page  # noqa: F401
        import settings_standby
        section = settings_standby.StandbySection()
        rendered = []
        monkeypatch.setattr(section, '_render_suspend',
                            lambda win: rendered.append(1))
        win = SimpleNamespace(config=FakeConfig())
        section._set_suspend(win, 900)
        section._set_caffeine(win, 0)
        assert win.config.data == {'suspend_after': 900,
                                   'caffeine_minutes': 0}
        assert rendered == [1, 1]


# --------------------------------------------------------- shell wiring

def _bind(obj, cls, *names):
    for name in names:
        setattr(obj, name, types.MethodType(getattr(cls, name), obj))


class TestShellWiring:
    @pytest.fixture(autouse=True)
    def fresh_hub(self, monkeypatch):
        monkeypatch.setattr(idle_inhibit, '_hub', idle_inhibit.IdleInhibitHub(
            timeout_add=lambda *a: 1, source_remove=lambda s: None))

    def test_blank_hooks_fan_out(self):
        import main
        calls = []
        app = SimpleNamespace(
            _screensaver=SimpleNamespace(
                set_active=lambda b: calls.append(('ss', b))),
            _auto_suspend=SimpleNamespace(
                note_blank=lambda b: calls.append(('as', b))))
        _bind(app, main.XXWMApplication, '_idle_blank_hooks')
        hub = idle_inhibit.get_hub()
        hub.set_caffeine(True, 0)
        app._idle_blank_hooks(False)
        assert hub.caffeine_active()
        app._idle_blank_hooks(True)
        assert not hub.caffeine_active()
        assert calls == [('ss', False), ('as', False), ('ss', True),
                         ('as', True)]

    def test_blank_hooks_without_services(self):
        import main
        app = SimpleNamespace(_screensaver=None, _auto_suspend=None)
        _bind(app, main.XXWMApplication, '_idle_blank_hooks')
        app._idle_blank_hooks(True)

    def test_probes(self):
        import main
        hub = idle_inhibit.get_hub()
        hub.set('screensaver', True)
        app = SimpleNamespace(
            _display_mgr=SimpleNamespace(idle_inhibitors=frozenset({'call'})),
            _mpris=SimpleNamespace(players=lambda: [
                SimpleNamespace(status='Paused'),
                SimpleNamespace(status='Playing')]),
            _standby=SimpleNamespace(_battery=(50.0, True)),
            _xxdb=SimpleNamespace(active_clients=2))
        _bind(app, main.XXWMApplication, '_idle_reasons', '_media_playing',
              '_on_charger', '_xxdb_clients')
        assert app._idle_reasons() == {'screensaver', 'call'}
        assert app._media_playing() and app._on_charger()
        assert app._xxdb_clients() == 2
        bare = SimpleNamespace(_display_mgr=None, _mpris=None, _standby=None,
                               _xxdb=None)
        _bind(bare, main.XXWMApplication, '_media_playing', '_on_charger',
              '_xxdb_clients')
        assert not bare._media_playing() and not bare._on_charger()
        assert bare._xxdb_clients() == 0

    def test_resume_reaches_auto_suspend(self, monkeypatch):
        import main
        resumed = []
        monkeypatch.setitem(sys.modules, 'settings_display', SimpleNamespace(
            refresh_night_scheduler=lambda why: None))
        app = SimpleNamespace(
            _shell=None, _sleep_delay=None,
            _auto_suspend=SimpleNamespace(
                note_resumed=lambda: resumed.append(1)))
        _bind(app, main.XXWMApplication, '_on_sleep')
        app._on_sleep(False)
        assert resumed == [1]

    def test_display_manager_exposes_inhibitors(self):
        import display_manager
        mgr = object.__new__(display_manager.DisplayManager)
        mgr._inhibitors = set()
        mgr.inhibit_idle('screensaver', True)
        assert mgr.idle_inhibitors == frozenset({'screensaver'})
