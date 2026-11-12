"""ModemMonitor call lifecycle fixes (review 2026-11-11, P1.2).

A failed GetAll on a new call retries with backoff, StateChanged is
watched before that fetch, calls ModemManager reports in state UNKNOWN
(created, never started) are not live, a failed Start deletes its call,
and screening waits for caller ID before acting on an empty number.

Reuses the FakeBus / idle-queue harness of test_modem_monitor_calls.py
(same fake gi, same module-bound references).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_modem_monitor_calls import (  # noqa: E402
    CALL1, CALL2, MODEM, FakeBus, FakeParams, FakeReply, _CALL_IFACE, _MM1,
    _VOICE_IFACE, _add_call, _delete_call, _make_monitor, _number_changed,
    _ringing_props, _shown, _state_changed, modem_monitor,
)


def _timers(monkeypatch):
    timers = []
    monkeypatch.setattr(modem_monitor.GLib, 'timeout_add',
                        lambda ms, cb, *a: timers.append((ms, cb, a)) or len(timers))
    monkeypatch.setattr(modem_monitor.GLib, 'source_remove',
                        lambda *_a: None)
    return timers


def _add_pending(monitor, call_path=CALL1):
    monitor._on_call_added(
        None, None, MODEM, None, None, FakeParams((call_path,)), None)


class TestCallFetchRetry:
    def test_failed_fetch_retries_and_rings(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        timers = _timers(monkeypatch)
        _add_pending(monitor)
        bus.complete_next(Exception('Timeout was reached'))
        drain()
        assert fired['incoming'] == []
        assert len(timers) == 1
        ms, cb, args = timers.pop()
        assert ms > 0
        cb(*args)
        assert bus.calls[-1][1:] == (CALL1, modem_monitor._PROPS_IFACE, 'GetAll')
        bus.complete_next(FakeReply(_ringing_props()))
        drain()
        assert fired['incoming'] == [(_shown('+15550001111'), '+15550001111')]

    def test_backoff_grows_then_gives_up(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        timers = _timers(monkeypatch)
        _add_pending(monitor)
        delays = []
        for _ in range(10):
            bus.complete_next(Exception('Timeout was reached'))
            drain()
            if not timers:
                break
            ms, cb, args = timers.pop()
            delays.append(ms)
            cb(*args)
        assert 2 <= len(delays) < 10
        assert delays == sorted(delays) and delays[-1] > delays[0]
        assert CALL1 not in monitor._fetching
        assert CALL1 not in monitor._call_subs
        assert monitor.calls() == [] and fired['ended'] == 0

    def test_retry_after_call_deleted_is_dropped(self, monkeypatch):
        bus = FakeBus()
        monitor, _, _, drain = _make_monitor(bus, monkeypatch)
        timers = _timers(monkeypatch)
        _add_pending(monitor)
        bus.complete_next(Exception('Timeout was reached'))
        drain()
        _delete_call(monitor, bus, drain, CALL1)
        n_calls = len(bus.calls)
        _ms, cb, args = timers.pop()
        cb(*args)
        assert len(bus.calls) == n_calls
        assert monitor.calls() == []

    def test_state_changed_subscribed_before_fetch(self, monkeypatch):
        bus = FakeBus()
        monitor, _, _, _drain = _make_monitor(bus, monkeypatch)
        _add_pending(monitor)
        assert bus.pending, 'GetAll still in flight'
        watched = {(s['iface'], s['signal'], s['path']) for s in bus.subs}
        assert (_CALL_IFACE, 'StateChanged', CALL1) in watched

    def test_terminated_while_fetching_never_rings(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        _add_pending(monitor)
        _state_changed(monitor, drain, CALL1, modem_monitor._STATE_TERMINATED)
        assert (_MM1, MODEM, _VOICE_IFACE, 'DeleteCall') in bus.calls
        # The GetAll reply left ModemManager before the hang-up reached us.
        bus.complete_next(FakeReply(_ringing_props()))
        drain()
        assert fired['incoming'] == []
        assert monitor.calls() == []


class TestUnknownStateCalls:
    """A call in MMCallState UNKNOWN (0) was created but never started
    (a failed Start leaves one behind): it is not a live call, so it
    neither shows a surface nor blocks auto-lock nor holds back on_ended."""

    def test_unknown_state_call_not_in_snapshot(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        _add_call(monitor, bus, drain, CALL1,
                  _ringing_props(direction=2, state=0))
        assert monitor.calls() == []
        assert all(snapshot == [] for snapshot in fired['changed'])
        assert modem_monitor.active_call_path() is None

    def test_unknown_call_shows_once_started(self, monkeypatch):
        bus = FakeBus()
        monitor, _, _, drain = _make_monitor(bus, monkeypatch)
        _add_call(monitor, bus, drain, CALL1,
                  _ringing_props(direction=2, state=0))
        _state_changed(monitor, drain, CALL1, modem_monitor._STATE_DIALING)
        assert [c.path for c in monitor.calls()] == [CALL1]

    def test_phantom_does_not_hold_back_ended(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        _add_call(monitor, bus, drain, CALL1,
                  _ringing_props(direction=2, state=0))
        _add_call(monitor, bus, drain, CALL2, _ringing_props())
        _state_changed(monitor, drain, CALL2, modem_monitor._STATE_TERMINATED)
        assert fired['ended'] == 1
        assert monitor.calls() == []


class TestScreeningWaitsForCallerId:
    """Caller ID often lands after the first ring. The ringing announce
    (which runs the shell's screening consult) and the waiting-call
    consult hold an empty number until it arrives or 1.5 s pass, so a
    "block hidden callers" rule never hangs up a caller whose number was
    merely late."""

    def test_ringing_waits_for_late_number(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        timers = _timers(monkeypatch)
        _add_call(monitor, bus, drain, CALL1, _ringing_props(number=''))
        assert fired['incoming'] == []
        assert [ms for ms, _cb, _a in timers] == [1500]
        # The silent incoming page still renders from the snapshot.
        assert [c.path for c in monitor.calls()] == [CALL1]
        _number_changed(monitor, drain, CALL1, 'tel:+15550001111')
        assert fired['incoming'] == [(_shown('+15550001111'), '+15550001111')]
        # A timer that fires anyway announces nothing twice.
        _ms, cb, args = timers.pop()
        cb(*args)
        drain()
        assert len(fired['incoming']) == 1

    def test_withheld_caller_rings_after_the_wait(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        timers = _timers(monkeypatch)
        _add_call(monitor, bus, drain, CALL1, _ringing_props(number=''))
        _ms, cb, args = timers.pop()
        cb(*args)
        drain()
        assert fired['incoming'] == [('', '')]

    def test_wait_dropped_with_the_call(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        timers = _timers(monkeypatch)
        _add_call(monitor, bus, drain, CALL1, _ringing_props(number=''))
        _delete_call(monitor, bus, drain, CALL1)
        _ms, cb, args = timers.pop()
        cb(*args)
        drain()
        assert fired['incoming'] == []

    def test_waiting_call_consult_waits_for_number(self, monkeypatch):
        bus = FakeBus()
        monitor, _fired, _, drain = _make_monitor(bus, monkeypatch)
        timers = _timers(monkeypatch)
        consults = []
        monkeypatch.setattr(modem_monitor, '_consult_check_number',
                            lambda number, cb: consults.append(number))
        _add_call(monitor, bus, drain, CALL1,
                  _ringing_props(state=modem_monitor._STATE_ACTIVE))
        _add_call(monitor, bus, drain, CALL2,
                  _ringing_props(number='',
                                 state=modem_monitor._STATE_WAITING))
        assert consults == []
        _number_changed(monitor, drain, CALL2, 'tel:+15550002222')
        assert consults == ['+15550002222']
        assert len(timers) == 1


SMS_A = '/org/freedesktop/ModemManager1/SMS/3'
SMS_B = '/org/freedesktop/ModemManager1/SMS/4'
_MSG_IFACE = 'org.freedesktop.ModemManager1.Modem.Messaging'


def _sms_added(monitor, bus, drain, sms_path, props):
    while bus.pending:  # an earlier Delete still awaiting its reply
        bus.complete_next(FakeReply(()))
    monitor._on_messaging_added(
        None, None, MODEM, None, None, FakeParams((sms_path, True)), None)
    bus.complete_next(FakeReply(dict(props)))
    drain()


_SMS_PROPS = {'Number': 'tel:+15550001111', 'Text': 'hello',
              'Timestamp': '2026-11-12T08:00:00+00'}


class TestSmsFallbackStore:
    """When the shell itself posted the SMS banner (no TxxT or Chatty),
    the message is deleted from the modem (storage would fill), and a
    copy ModemManager announces again (modem re-enable) is not notified
    a second time."""

    def test_shell_posted_message_deleted_from_modem(self, monkeypatch):
        bus = FakeBus()
        monitor, _fired, _, drain = _make_monitor(bus, monkeypatch)
        posted = []
        monitor._on_sms = lambda n, t: posted.append((n, t)) or True
        _sms_added(monitor, bus, drain, SMS_A, _SMS_PROPS)
        assert posted == [('+15550001111', 'hello')]
        assert bus.calls[-1] == (_MM1, MODEM, _MSG_IFACE, 'Delete')

    def test_message_left_for_its_app_is_kept(self, monkeypatch):
        bus = FakeBus()
        monitor, _fired, _, drain = _make_monitor(bus, monkeypatch)
        monitor._on_sms = lambda n, t: False
        _sms_added(monitor, bus, drain, SMS_A, _SMS_PROPS)
        assert 'Delete' not in [c[3] for c in bus.calls]

    def test_reannounced_copy_is_not_notified_twice(self, monkeypatch):
        bus = FakeBus()
        monitor, _fired, _, drain = _make_monitor(bus, monkeypatch)
        posted = []
        monitor._on_sms = lambda n, t: posted.append((n, t)) or True
        _sms_added(monitor, bus, drain, SMS_A, _SMS_PROPS)
        _sms_added(monitor, bus, drain, SMS_B, _SMS_PROPS)
        assert len(posted) == 1
        deletes = [c for c in bus.calls if c[3] == 'Delete']
        assert len(deletes) == 2

    def test_same_text_at_another_time_is_a_new_message(self, monkeypatch):
        bus = FakeBus()
        monitor, _fired, _, drain = _make_monitor(bus, monkeypatch)
        posted = []
        monitor._on_sms = lambda n, t: posted.append((n, t)) or True
        _sms_added(monitor, bus, drain, SMS_A, _SMS_PROPS)
        _sms_added(monitor, bus, drain, SMS_B,
                   dict(_SMS_PROPS, Timestamp='2026-11-12T09:00:00+00'))
        assert len(posted) == 2


class TestFailedStartDeletesCall:
    def test_start_failure_deletes_created_call(self, monkeypatch):
        bus = FakeBus()
        bus.sync_replies.extend([
            FakeReply((CALL1,)), Exception('No network'), FakeReply(())])
        monkeypatch.setattr(modem_monitor.Gio, 'bus_get_sync',
                            lambda *a, **k: bus)
        ok, detail = modem_monitor.voice_create_and_start(
            '+15550001111', modem=MODEM)
        assert (ok, detail) == (False, 'No network')
        assert [c[3] for c in bus.sync_calls] == [
            'CreateCall', 'Start', 'DeleteCall']
        assert bus.sync_calls[2][1:3] == (MODEM, _VOICE_IFACE)

    def test_create_failure_deletes_nothing(self, monkeypatch):
        bus = FakeBus()
        bus.sync_replies.append(Exception('rejected'))
        monkeypatch.setattr(modem_monitor.Gio, 'bus_get_sync',
                            lambda *a, **k: bus)
        assert modem_monitor.voice_create_and_start(
            '+1', modem=MODEM) == (False, 'rejected')
        assert [c[3] for c in bus.sync_calls] == ['CreateCall']
