"""Lane bug_shell telephony findings: 6, 12 and lower-severity L1.

6: a waiting call that becomes the ringing call must be screened (consult)
   and must ring — _on_call_state_changed ignored WAITING->RINGING_IN.
12: an ordinary incoming call must be written to the call log exactly once
    (_track_snapshot and _on_call_ended both reported it).
L1: emergency dial from the lock screen must dial the normalized number,
    not the raw string that merely matched as an emergency number.
"""
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


def _install_fake_gi():
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


@pytest.fixture(autouse=True)
def _isolate_modules():
    """Leave no imported shell module behind: sibling files import window
    and friends against their own gi seams (test_folders idiom)."""
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        sys.modules.pop(name, None)

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
        self.subs = []
        self.pending = []
        self._finish = []
        self._next = 1

    def signal_subscribe(self, sender, iface, signal, path, *_rest):
        self.subs.append({'sender': sender, 'iface': iface,
                          'signal': signal, 'path': path})
        sid = self._next
        self._next += 1
        return sid

    def signal_unsubscribe(self, sid):
        pass

    def call(self, bus_name, path, iface, method, params=None,
             reply_type=None, flags=None, timeout=-1, cancellable=None,
             callback=None, *user_data):
        self.pending.append((callback, user_data))

    def call_finish(self, _result):
        reply = self._finish.pop(0)
        if isinstance(reply, Exception):
            raise modem_monitor.GLib.Error(str(reply))
        return reply

    def complete_next(self, reply) -> None:
        self._finish.append(reply)
        cb, user_data = self.pending.pop(0)
        cb(self, object(), *user_data)


def _make_monitor(monkeypatch):
    bus = FakeBus()
    pending: list = []
    fired = {'incoming': [], 'answered': [], 'ended': 0, 'changed': []}

    def _idle(cb, *args):
        pending.append((cb, args))
        return len(pending)

    monkeypatch.setattr(modem_monitor.GLib, 'idle_add', _idle)
    monkeypatch.setattr(modem_monitor.Gio, 'bus_get_sync',
                        lambda *a, **k: bus)
    monitor = modem_monitor.ModemMonitor(
        on_incoming=lambda c, n: fired['incoming'].append((c, n)),
        on_answered=lambda c, n: fired['answered'].append((c, n)),
        on_ended=lambda: fired.__setitem__('ended', fired['ended'] + 1),
        on_calls_changed=lambda calls: fired['changed'].append(list(calls)),
    )

    def _drain():
        while pending:
            cb, args = pending.pop(0)
            cb(*args)

    _drain()
    bus.complete_next(FakeReply({}))
    _drain()
    return monitor, bus, fired, _drain


def _add_call(monitor, bus, drain, path, props, modem=MODEM):
    monitor._on_call_added(
        None, None, modem, None, None, FakeParams((path,)), None)
    bus.complete_next(FakeReply(dict(props)))
    drain()


def _props(number='tel:+15550001111', direction=1, state=3):
    return {'Direction': direction, 'State': state, 'Number': number}


def _shown(number: str) -> str:
    """Expected caller text for an unsaved number: the xx_phone rule."""
    import xx_phone
    return xx_phone.display(number, xx_phone.user_country("", ""))


class TestWaitingBecomesRinging:
    def test_waiting_to_ringing_is_announced(self, monkeypatch) -> None:
        monitor, bus, fired, drain = _make_monitor(monkeypatch)
        _add_call(monitor, bus, drain, CALL_A,
                  _props(direction=1, state=4))  # active call A
        _add_call(monitor, bus, drain, CALL_B,
                  _props(number='tel:+15550002222', direction=1, state=6))
        assert fired['incoming'] == []
        # A hangs up; B moves WAITING -> RINGING_IN.
        monitor._on_call_state_changed(
            None, None, CALL_B, None, None,
            FakeParams((6, 3, 0)), None)
        drain()
        assert fired['incoming'] == [(_shown('+15550002222'), '+15550002222')], \
            'a waiting call that starts ringing must be screened and ring'


class TestCallLogDedup:
    def _win(self, monkeypatch):
        import call_screen
        noted: list = []
        monkeypatch.setattr(call_screen, 'note_call',
                            lambda *a: noted.append(a))
        win = SimpleNamespace(
            _screen_pending={'number': '+15550001111',
                             'started_ms': 1000,
                             'answered': False},
            _screen_tracked={},
            _screen_done=set(),
            _call_ui=SimpleNamespace(end_call=lambda: None),
            _call_bar=SimpleNamespace(hide_bar=lambda: None),
        )
        from call_wiring import CallWiring
        return CallWiring(win), noted

    def _call(self, number='+15550001111', direction=1, state=4):
        return SimpleNamespace(path=CALL_A, number=number,
                               direction=direction, state=state)

    def test_ordinary_call_logged_once(self, monkeypatch) -> None:
        import time
        monkeypatch.setattr(time, 'time', lambda: 2.0)
        wiring, noted = self._win(monkeypatch)
        wiring._track_snapshot([self._call()])
        wiring._track_snapshot([])
        wiring._on_call_ended()
        assert len(noted) == 1, f'call logged {len(noted)} times: {noted}'


class TestEmergencyDialNormalized:
    def test_place_emergency_call_dials_digits(self, monkeypatch) -> None:
        # place_emergency_call binds voice_create_and_start lazily from
        # modem_monitor, so patch the target it actually calls.
        import modem_monitor as _mm
        dialed: list = []
        monkeypatch.setattr(
            _mm, 'voice_create_and_start',
            lambda number, *a, **k: dialed.append(number) or (True, 'p'))
        # Fresh import against the real bindings: sibling modules may have
        # bound dialer to collection-time gi stubs (test_emergency idiom).
        saved = sys.modules.pop('dialer', None)
        try:
            import dialer
            # Never the host's NetworkManager / ModemManager.
            monkeypatch.setattr(dialer, 'ensure_radio_on', lambda *a, **k: False)
            monkeypatch.setattr(dialer, 'modem_sim_missing',
                                lambda *a, **k: False)
            ok, _ = dialer.place_emergency_call('*1*1*2#')
        finally:
            sys.modules.pop('dialer', None)
            if saved is not None:
                sys.modules['dialer'] = saved
        assert ok is True
        assert dialed == ['112'], f'dialed raw string: {dialed}'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
