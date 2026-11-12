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
    CALL1, MODEM, FakeBus, FakeParams, FakeReply, _CALL_IFACE, _MM1,
    _VOICE_IFACE, _delete_call, _make_monitor, _ringing_props, _shown,
    _state_changed, modem_monitor,
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
