"""ModemMonitor lifecycle findings from the phone-stack reviews.

- Call waiting: a second incoming call arrives WAITING and never passes
  RINGING_IN while the first is up, so it skipped the dialer's
  CheckNumber screening (GNOME Calls screens every incoming call). A
  block verdict hangs up only that call, never Voice.HangupAll.
- ModemManager restarts: the monitor enumerated once and kept dead
  object paths (ghost call screen). It now watches the bus name (Cutie's
  NameOwnerChanged handling): vanish drops everything through the normal
  ended path, appear re-enumerates.
- Ended calls: TERMINATED calls are removed with Voice.DeleteCall (also
  leftovers found at enumerate) and the MMCallStateReason survives as a
  short text (GNOME Calls calls-mm-origin.c / calls-mm-call.c).

Fakes are reached through modem_monitor.GLib/Gio (sibling modules
install their own fake gi packages at collection time).
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


def _install_fake_gi() -> None:
    glib = types.ModuleType('gi.repository.GLib')
    glib.SOURCE_REMOVE = False
    glib.SOURCE_CONTINUE = True
    glib.Error = type('Error', (Exception,), {})
    glib.idle_add = lambda *a, **k: 1
    glib.timeout_add = lambda *a, **k: 1
    glib.timeout_add_seconds = lambda *a, **k: 1
    glib.source_remove = lambda *a, **k: None
    glib.Variant = lambda *a, **k: None

    gio = types.ModuleType('gi.repository.Gio')
    gio.BusType = types.SimpleNamespace(SYSTEM=1, SESSION=2)
    gio.DBusSignalFlags = types.SimpleNamespace(NONE=0)
    gio.DBusCallFlags = types.SimpleNamespace(NONE=0)
    gio.bus_get_sync = lambda *a, **k: None

    gi_mod = types.ModuleType('gi')
    gi_rep = types.ModuleType('gi.repository')
    gi_mod.require_version = lambda *a, **k: None
    sys.modules['gi'] = gi_mod
    sys.modules['gi.repository'] = gi_rep
    sys.modules['gi.repository.GLib'] = glib
    sys.modules['gi.repository.Gio'] = gio


_install_fake_gi()

import modem_monitor  # noqa: E402

_MODEM_IFACE = 'org.freedesktop.ModemManager1.Modem'
_VOICE_IFACE = 'org.freedesktop.ModemManager1.Modem.Voice'
_CALL_IFACE = 'org.freedesktop.ModemManager1.Call'
_MM1 = 'org.freedesktop.ModemManager1'

MODEM = '/org/freedesktop/ModemManager1/Modem/0'
CALL_A = '/org/freedesktop/ModemManager1/Call/1'
CALL_B = '/org/freedesktop/ModemManager1/Call/2'


class FakeParams:
    def __init__(self, value):
        self._value = value

    def unpack(self):
        return self._value


class FakeReply:
    def __init__(self, value):
        self._value = value

    def unpack(self):
        if isinstance(self._value, dict):
            return (self._value,)
        return self._value


class FakeBus:
    def __init__(self):
        self.calls = []
        self.pending = []
        self._finish = []
        self._next = 1

    def signal_subscribe(self, *_args):
        sid = self._next
        self._next += 1
        return sid

    def signal_unsubscribe(self, _sid):
        pass

    def call(self, bus_name, path, iface, method, params=None,
             reply_type=None, flags=None, timeout=-1, cancellable=None,
             callback=None, *user_data):
        self.calls.append((bus_name, path, iface, method))
        self.pending.append((method, callback, user_data))

    def call_finish(self, _result):
        reply = self._finish.pop(0)
        if isinstance(reply, Exception):
            raise modem_monitor.GLib.Error(str(reply))
        return reply

    def complete(self, method, reply) -> None:
        """Deliver the oldest outstanding reply for ``method``."""
        for index, (name, cb, user_data) in enumerate(self.pending):
            if name == method:
                del self.pending[index]
                self._finish.append(reply)
                cb(self, object(), *user_data)
                return
        raise AssertionError(f'no pending {method}')

    def methods(self):
        return [c[3] for c in self.calls]


class Watch:
    def __init__(self):
        self.args = None

    def __call__(self, bus_type, name, flags, appeared, vanished):
        self.args = (bus_type, name, appeared, vanished)
        return 42

    def appear(self, owner=':1.7'):
        self.args[2](None, _MM1, owner)

    def vanish(self):
        self.args[3](None, _MM1)


def _make_monitor(monkeypatch):
    bus = FakeBus()
    watch = Watch()
    pending: list = []
    fired = {'incoming': [], 'ended': 0, 'changed': [],
             'consults': [], 'hangups': []}

    def _idle(cb, *args):
        pending.append((cb, args))
        return len(pending)

    glib, gio = modem_monitor.GLib, modem_monitor.Gio
    monkeypatch.setattr(glib, 'idle_add', _idle)
    monkeypatch.setattr(gio, 'bus_get_sync', lambda *a, **k: bus)
    monkeypatch.setattr(gio, 'bus_watch_name', watch, raising=False)
    monkeypatch.setattr(gio, 'BusNameWatcherFlags',
                        types.SimpleNamespace(NONE=0), raising=False)
    monkeypatch.setattr(
        modem_monitor, '_consult_check_number',
        lambda number, cb: fired['consults'].append((number, cb)))
    monkeypatch.setattr(
        modem_monitor, 'hangup_call',
        lambda path, cb: fired['hangups'].append(path))
    monitor = modem_monitor.ModemMonitor(
        on_incoming=lambda c, n: fired['incoming'].append(n),
        on_ended=lambda: fired.__setitem__('ended', fired['ended'] + 1),
        on_calls_changed=lambda calls: fired['changed'].append(list(calls)),
    )

    def drain():
        while pending:
            cb, args = pending.pop(0)
            cb(*args)

    drain()
    bus.complete('GetManagedObjects', FakeReply(({},)))
    drain()
    return monitor, bus, watch, fired, drain


def _add_call(monitor, bus, drain, path, number, state, direction=1,
              reason=0):
    monitor._on_call_added(
        None, None, MODEM, None, None, FakeParams((path,)), None)
    bus.complete('GetAll', FakeReply({
        'Direction': direction, 'State': state, 'Number': number,
        'StateReason': reason}))
    drain()


def _state(monitor, drain, path, old, new, reason=0):
    monitor._on_call_state_changed(
        None, None, path, None, None, FakeParams((old, new, reason)), None)
    drain()


ACTIVE = modem_monitor._STATE_ACTIVE
WAITING = modem_monitor._STATE_WAITING
RINGING_IN = modem_monitor._STATE_RINGING_IN
TERMINATED = modem_monitor._STATE_TERMINATED


class TestWaitingCallScreening:
    def _two_calls(self, monkeypatch, number='tel:+15550002222'):
        monitor, bus, _w, fired, drain = _make_monitor(monkeypatch)
        _add_call(monitor, bus, drain, CALL_A, 'tel:+15550001111', ACTIVE)
        _add_call(monitor, bus, drain, CALL_B, number, WAITING)
        return monitor, bus, fired, drain

    def test_waiting_call_is_consulted(self, monkeypatch):
        _m, _b, fired, _d = self._two_calls(monkeypatch)
        assert [n for n, _cb in fired['consults']] == ['+15550002222']
        # The waiting banner is the UI: no incoming surface over call A.
        assert fired['incoming'] == []

    def test_block_hangs_up_only_the_waiting_call(self, monkeypatch):
        _m, bus, fired, _d = self._two_calls(monkeypatch)
        _number, on_verdict = fired['consults'][0]
        on_verdict('block', 'blocklist')
        assert fired['hangups'] == [CALL_B]
        assert 'HangupAll' not in bus.methods()

    def test_ring_and_silence_leave_the_call_alone(self, monkeypatch):
        _m, _b, fired, _d = self._two_calls(monkeypatch)
        _number, on_verdict = fired['consults'][0]
        on_verdict('ring', '')
        on_verdict('silence', 'unknown')
        assert fired['hangups'] == []

    def test_late_block_after_hold_and_answer_is_ignored(self, monkeypatch):
        monitor, _b, fired, drain = self._two_calls(monkeypatch)
        _state(monitor, drain, CALL_A, ACTIVE, modem_monitor._STATE_HELD)
        _state(monitor, drain, CALL_B, WAITING, ACTIVE)
        _number, on_verdict = fired['consults'][0]
        on_verdict('block', 'blocklist')
        assert fired['hangups'] == []

    def test_promoted_to_ringing_is_screened_by_the_ringing_path(
            self, monkeypatch):
        monitor, _b, fired, drain = self._two_calls(monkeypatch)
        _state(monitor, drain, CALL_B, WAITING, RINGING_IN)
        assert fired['incoming'] == ['+15550002222']
        _number, on_verdict = fired['consults'][0]
        on_verdict('block', 'blocklist')
        assert fired['hangups'] == []

    def test_late_number_rescreens_and_drops_the_withheld_verdict(
            self, monkeypatch):
        monitor, _b, fired, drain = self._two_calls(monkeypatch, number='')
        monitor._on_call_properties_changed(
            None, None, CALL_B, None, None,
            FakeParams((_CALL_IFACE, {'Number': 'tel:+15550003333'}, [])),
            None)
        drain()
        assert [n for n, _cb in fired['consults']] == ['', '+15550003333']
        fired['consults'][0][1]('block', 'withheld')
        assert fired['hangups'] == []
        fired['consults'][1][1]('block', 'blocklist')
        assert fired['hangups'] == [CALL_B]

    def test_waiting_via_state_change_is_consulted_once(self, monkeypatch):
        monitor, bus, _w, fired, drain = _make_monitor(monkeypatch)
        _add_call(monitor, bus, drain, CALL_A, 'tel:+15550001111', ACTIVE)
        _add_call(monitor, bus, drain, CALL_B, 'tel:+15550002222', 0)
        _state(monitor, drain, CALL_B, 0, WAITING)
        _state(monitor, drain, CALL_B, WAITING, WAITING)
        assert [n for n, _cb in fired['consults']] == ['+15550002222']

    def test_ringing_call_is_left_to_the_shell_consult(self, monkeypatch):
        monitor, bus, _w, fired, drain = _make_monitor(monkeypatch)
        _add_call(monitor, bus, drain, CALL_A, 'tel:+15550001111',
                  RINGING_IN)
        assert fired['consults'] == []
        assert fired['incoming'] == ['+15550001111']


class TestModemManagerRestart:
    def test_watches_the_system_bus_name(self, monkeypatch):
        _m, _b, watch, _f, _d = _make_monitor(monkeypatch)
        assert watch.args[:2] == (modem_monitor.Gio.BusType.SYSTEM, _MM1)

    def test_first_appear_does_not_re_enumerate(self, monkeypatch):
        _m, bus, watch, _f, _d = _make_monitor(monkeypatch)
        watch.appear()
        assert bus.methods().count('GetManagedObjects') == 1

    def test_vanish_ends_calls_and_appear_re_enumerates(self, monkeypatch):
        monitor, bus, watch, fired, drain = _make_monitor(monkeypatch)
        watch.appear()
        monitor._modems[MODEM] = {_MODEM_IFACE, _VOICE_IFACE}
        _add_call(monitor, bus, drain, CALL_A, 'tel:+15550001111', ACTIVE)
        watch.vanish()
        drain()
        assert monitor.calls() == []
        assert monitor._modems == {}
        assert monitor._call_subs == {}
        assert modem_monitor.active_call_path() is None
        assert fired['ended'] == 1
        assert fired['changed'][-1] == []
        watch.appear(':1.99')
        assert bus.methods().count('GetManagedObjects') == 2
        bus.complete('GetManagedObjects', FakeReply((
            {MODEM: {_MODEM_IFACE: {}, _VOICE_IFACE: {}}},)))
        assert bus.methods()[-1] == 'ListCalls'
        assert monitor.modem_path() == MODEM

    def test_new_owner_without_vanish_also_resets(self, monkeypatch):
        monitor, bus, watch, fired, drain = _make_monitor(monkeypatch)
        watch.appear(':1.7')
        _add_call(monitor, bus, drain, CALL_A, 'tel:+15550001111', ACTIVE)
        watch.appear(':1.8')
        drain()
        assert monitor.calls() == []
        assert fired['ended'] == 1
        assert bus.methods().count('GetManagedObjects') == 2

    def test_fetch_in_flight_at_vanish_is_not_resurrected(self, monkeypatch):
        monitor, bus, watch, fired, drain = _make_monitor(monkeypatch)
        watch.appear()
        monitor._on_call_added(
            None, None, MODEM, None, None, FakeParams((CALL_A,)), None)
        watch.vanish()
        bus.complete('GetAll', FakeReply({
            'Direction': 1, 'State': RINGING_IN, 'Number': 'tel:+1555'}))
        drain()
        assert monitor.calls() == []
        assert fired['incoming'] == []

    def test_no_watch_api_still_enumerates(self, monkeypatch):
        bus = FakeBus()
        glib, gio = modem_monitor.GLib, modem_monitor.Gio
        pending: list = []
        monkeypatch.setattr(glib, 'idle_add',
                            lambda cb, *a: pending.append((cb, a)))
        monkeypatch.setattr(gio, 'bus_get_sync', lambda *a, **k: bus)

        def _boom(*_a):
            raise RuntimeError('no watcher')

        monkeypatch.setattr(gio, 'bus_watch_name', _boom, raising=False)
        modem_monitor.ModemMonitor()
        while pending:
            cb, args = pending.pop(0)
            cb(*args)
        assert bus.methods() == ['GetManagedObjects']


class TestEndedCalls:
    def test_terminated_call_is_deleted_with_its_reason(self, monkeypatch):
        monitor, bus, _w, fired, drain = _make_monitor(monkeypatch)
        _add_call(monitor, bus, drain, CALL_A, 'tel:+15550001111',
                  modem_monitor._STATE_RINGING_OUT, direction=2)
        _state(monitor, drain, CALL_A, modem_monitor._STATE_RINGING_OUT,
               TERMINATED, reason=5)
        assert (_MM1, MODEM, _VOICE_IFACE, 'DeleteCall') in bus.calls
        ended = modem_monitor.last_ended_call()
        assert ended is not None and ended.path == CALL_A
        assert ended.end_reason == 'Busy'
        assert ended.number == '+15550001111'
        assert fired['ended'] == 1
        # A failed DeleteCall (already deleted elsewhere) is harmless.
        bus.complete('DeleteCall', RuntimeError('No such call'))

    def test_leftover_terminated_call_at_enumerate_is_deleted(
            self, monkeypatch):
        monitor, bus, _w, fired, drain = _make_monitor(monkeypatch)
        monitor._enumerate()
        bus.complete('GetManagedObjects', FakeReply((
            {MODEM: {_MODEM_IFACE: {}, _VOICE_IFACE: {}}},)))
        bus.complete('ListCalls', FakeReply(([CALL_A],)))
        bus.complete('GetAll', FakeReply({
            'Direction': 1, 'State': TERMINATED, 'Number': 'tel:+1555',
            'StateReason': 4}))
        drain()
        assert (_MM1, MODEM, _VOICE_IFACE, 'DeleteCall') in bus.calls
        assert monitor.calls() == []
        assert fired['ended'] == 0

    def test_reason_texts(self):
        text = modem_monitor.end_reason_text
        assert text(5) == 'Busy'
        assert text(6) == 'Network error'
        assert text(7) == 'Audio setup failed'
        assert text(4) == 'Call ended'
        assert text(99) == 'Call ended'
        assert text('junk') == 'Call ended'
