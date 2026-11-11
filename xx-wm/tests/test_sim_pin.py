"""SIM PIN / PUK prompt: modem lock watch, controller flow, D-Bus calls.

- modem_monitor keeps Modem.State/UnlockRequired/UnlockRetries from
  GetManagedObjects, follows StateChanged + Modem PropertiesChanged and
  reports a SimLockInfo on change (never for a modem that was never
  locked); a modem reaching REGISTERED re-lists its calls (catch-up).
- sim_pin.SimPinController shows one prompt, defers it while the shell
  is locked, validates before sending, SendPin/SendPuk async, shows
  attempts left and plain errors, never logs digits.
"""
import logging
import types
from types import SimpleNamespace

import pytest

import modem_monitor
import sim_pin
from test_modem_monitor_lifecycle import (
    MODEM, FakeBus, FakeParams, FakeReply, Watch)

_MODEM_IFACE = 'org.freedesktop.ModemManager1.Modem'
_VOICE_IFACE = 'org.freedesktop.ModemManager1.Modem.Voice'
SIM = '/org/freedesktop/ModemManager1/SIM/0'


# ------------------------------------------------------- monitor lock watch

def _monitor(monkeypatch, objects=None):
    bus = FakeBus()
    watch = Watch()
    pending = []
    locks = []

    def _idle(cb, *args):
        pending.append((cb, args))
        return len(pending)

    glib, gio = modem_monitor.GLib, modem_monitor.Gio
    monkeypatch.setattr(glib, 'idle_add', _idle)
    monkeypatch.setattr(gio, 'bus_get_sync', lambda *a, **k: bus)
    monkeypatch.setattr(gio, 'bus_watch_name', watch, raising=False)
    monkeypatch.setattr(gio, 'BusNameWatcherFlags',
                        types.SimpleNamespace(NONE=0), raising=False)
    monitor = modem_monitor.ModemMonitor(on_sim_lock=locks.append)

    def drain():
        while pending:
            cb, args = pending.pop(0)
            cb(*args)

    drain()
    bus.complete('GetManagedObjects', FakeReply((objects or {},)))
    drain()
    return monitor, bus, locks, drain


def _locked_props(lock=2, retries=3, state=2):
    return {'State': state, 'UnlockRequired': lock,
            'UnlockRetries': {lock: retries}, 'Sim': SIM}


def test_subscribes_modem_state_and_properties(monkeypatch):
    bus = FakeBus()
    subs = []
    bus.signal_subscribe = lambda *args: subs.append(args[:5]) or len(subs)
    monkeypatch.setattr(modem_monitor.GLib, 'idle_add', lambda *a: 1)
    monkeypatch.setattr(modem_monitor.Gio, 'bus_get_sync', lambda *a, **k: bus)
    monkeypatch.setattr(modem_monitor.Gio, 'bus_watch_name', Watch(),
                        raising=False)
    monkeypatch.setattr(modem_monitor.Gio, 'BusNameWatcherFlags',
                        types.SimpleNamespace(NONE=0), raising=False)
    monitor = modem_monitor.ModemMonitor(on_sim_lock=lambda _i: None)
    monitor._init_bus()
    assert ('org.freedesktop.ModemManager1', _MODEM_IFACE, 'StateChanged',
            None, None) in subs
    assert ('org.freedesktop.ModemManager1', 'org.freedesktop.DBus.Properties',
            'PropertiesChanged', None, _MODEM_IFACE) in subs


def test_locked_modem_at_enumerate_is_reported(monkeypatch):
    _m, _bus, locks, _d = _monitor(
        monkeypatch, {MODEM: {_MODEM_IFACE: _locked_props()}})
    assert len(locks) == 1
    info = locks[0]
    assert info.locked and info.unlock_required == 2
    assert info.sim_path == SIM and info.retries_left() == 3
    assert modem_monitor.current_sim_locks()[0].modem_path == MODEM


def test_unlocked_modem_is_never_reported(monkeypatch):
    monitor, _bus, locks, drain = _monitor(monkeypatch, {
        MODEM: {_MODEM_IFACE: {'State': 8, 'UnlockRequired': 1}}})
    monitor._on_modem_state_changed(
        None, None, MODEM, None, None, FakeParams((8, 11, 0)), None)
    drain()
    assert locks == []


def test_retries_change_and_unlock_are_reported(monkeypatch):
    monitor, _bus, locks, drain = _monitor(
        monkeypatch, {MODEM: {_MODEM_IFACE: _locked_props()}})
    monitor._on_modem_properties_changed(
        None, None, MODEM, None, None,
        FakeParams((_MODEM_IFACE, {'UnlockRetries': {2: 2}}, [])), None)
    drain()
    assert locks[-1].retries_left() == 2
    # Same values again: no duplicate report.
    monitor._on_modem_properties_changed(
        None, None, MODEM, None, None,
        FakeParams((_MODEM_IFACE, {'UnlockRetries': {2: 2}}, [])), None)
    drain()
    assert len(locks) == 2
    # PIN exhausted → PUK.
    monitor._on_modem_properties_changed(
        None, None, MODEM, None, None,
        FakeParams((_MODEM_IFACE, {'UnlockRequired': 4,
                                   'UnlockRetries': {4: 10}}, [])), None)
    drain()
    assert locks[-1].unlock_required == 4 and locks[-1].retries_left() == 10
    monitor._on_modem_state_changed(
        None, None, MODEM, None, None, FakeParams((2, 3, 0)), None)
    drain()
    assert locks[-1].locked is False


def test_state_changed_to_locked_reports(monkeypatch):
    monitor, _bus, locks, drain = _monitor(monkeypatch, {
        MODEM: {_MODEM_IFACE: {'State': 1, 'UnlockRequired': 2,
                               'Sim': SIM}}})
    assert locks == []
    monitor._on_modem_state_changed(
        None, None, MODEM, None, None, FakeParams((1, 2, 0)), None)
    drain()
    assert locks and locks[-1].locked


def test_modem_removed_withdraws_prompt(monkeypatch):
    monitor, _bus, locks, drain = _monitor(
        monkeypatch, {MODEM: {_MODEM_IFACE: _locked_props()}})
    monitor._on_interfaces_removed(
        None, None, None, None, None,
        FakeParams((MODEM, [_MODEM_IFACE])), None)
    drain()
    assert locks[-1].locked is False and locks[-1].modem_path == MODEM


def test_registered_runs_call_catch_up(monkeypatch):
    monitor, bus, _locks, drain = _monitor(monkeypatch, {
        MODEM: {_MODEM_IFACE: {'State': 7}, _VOICE_IFACE: {}}})
    before = bus.methods().count('ListCalls')
    monitor._on_modem_state_changed(
        None, None, MODEM, None, None, FakeParams((7, 8, 0)), None)
    assert bus.methods().count('ListCalls') == before + 1
    # Moving between registered/connected states is not a new sweep.
    monitor._on_modem_state_changed(
        None, None, MODEM, None, None, FakeParams((8, 11, 0)), None)
    assert bus.methods().count('ListCalls') == before + 1


def test_voice_added_after_unlock_is_merged_and_listed(monkeypatch):
    monitor, bus, _locks, drain = _monitor(
        monkeypatch, {MODEM: {_MODEM_IFACE: _locked_props()}})
    assert monitor.modem_path(_VOICE_IFACE) == MODEM  # fallback only
    monitor._on_interfaces_added(
        None, None, None, None, None,
        FakeParams((MODEM, {_VOICE_IFACE: {}})), None)
    assert _MODEM_IFACE in monitor._modems[MODEM]
    assert _VOICE_IFACE in monitor._modems[MODEM]
    assert 'ListCalls' in bus.methods()
    monitor._on_interfaces_removed(
        None, None, None, None, None,
        FakeParams((MODEM, [_VOICE_IFACE])), None)
    assert monitor._modems[MODEM] == {_MODEM_IFACE}


# --------------------------------------------------------------- pure text

def test_texts_and_validation():
    assert sim_pin.title_for(2) == 'SIM PIN required'
    assert sim_pin.title_for(4) == 'SIM PUK required'
    assert sim_pin.is_puk(4) and not sim_pin.is_puk(2)
    assert sim_pin.retries_text(None) == ''
    assert sim_pin.retries_text(3) == '3 attempts left'
    assert 'PUK after this' in sim_pin.retries_text(1, 2)
    assert 'unusable' in sim_pin.retries_text(1, 4)
    assert sim_pin.retries_text(0) == 'No attempts left'
    assert sim_pin.validate_pin('1234') is None
    assert sim_pin.validate_pin('123') and sim_pin.validate_pin('12a4')
    assert sim_pin.validate_puk('12345678', '1234', '1234') is None
    assert sim_pin.validate_puk('1234567', '1234', '1234')
    assert sim_pin.validate_puk('12345678', '1234', '4321') == \
        'The new PINs do not match.'


def test_plain_errors():
    class Wrong(Exception):
        message = ('GDBus.Error:org.freedesktop.ModemManager1.Error.'
                   'MobileEquipment.IncorrectPassword: Incorrect password')
    assert sim_pin.plain_error(Wrong(), 2) == 'Wrong PIN.'
    assert sim_pin.plain_error(Wrong(), 4) == 'Wrong PUK.'

    class Puk(Exception):
        message = 'GDBus.Error:org.freedesktop.ModemManager1.Error.MobileEquipment.SimPuk: x'
    assert 'PUK' in sim_pin.plain_error(Puk())


# ---------------------------------------------------------------- D-Bus

class CallBus:
    def __init__(self):
        self.calls = []

    def call(self, name, path, iface, method, params, reply_type, flags,
             timeout, cancellable, callback):
        self.calls.append((path, iface, method, params, callback))

    def call_finish(self, result):
        if isinstance(result, Exception):
            raise result
        return result


class V:
    def __init__(self, fmt, value):
        self.fmt, self.value = fmt, value


_GLIB = SimpleNamespace(Variant=V)
_GIO = SimpleNamespace(DBusCallFlags=SimpleNamespace(NONE=0))


def test_send_pin_and_puk_methods():
    bus = CallBus()
    done = []
    sim_pin.send_unlock(bus, SIM, 2, '1234', '', lambda *a: done.append(a),
                        _GLIB, _GIO)
    path, iface, method, params, cb = bus.calls[-1]
    assert (path, iface, method) == (SIM, 'org.freedesktop.ModemManager1.Sim',
                                     'SendPin')
    assert (params.fmt, params.value) == ('(s)', ('1234',))
    cb(bus, object())
    assert done == [(True, '')]
    sim_pin.send_unlock(bus, SIM, 4, '12345678', '0000',
                        lambda *a: done.append(a), _GLIB, _GIO)
    assert bus.calls[-1][2] == 'SendPuk'
    assert bus.calls[-1][3].value == ('12345678', '0000')


def test_send_failure_never_logs_digits(caplog):
    bus = CallBus()
    done = []
    sim_pin.send_unlock(bus, SIM, 2, '9876', '', lambda *a: done.append(a),
                        _GLIB, _GIO)

    class Wrong(Exception):
        message = 'org.freedesktop.ModemManager1.Error.MobileEquipment.IncorrectPassword: no'
    with caplog.at_level(logging.DEBUG):
        bus.calls[-1][4](bus, Wrong())
    assert done == [(False, 'Wrong PIN.')]
    assert '9876' not in caplog.text


def test_send_without_sim_or_bus_fails_soft():
    done = []
    sim_pin.send_unlock(None, SIM, 2, '1234', '', lambda *a: done.append(a))
    sim_pin.send_unlock(CallBus(), '', 2, '1234', '', lambda *a: done.append(a))
    assert [ok for ok, _t in done] == [False, False]


# ----------------------------------------------------------- controller

class FakeView:
    def __init__(self, info, on_submit, on_later):
        self.info, self.submit, self.later = info, on_submit, on_later
        self.notes, self.busy, self.closed = [], [], False
        self.updates = []

    def update(self, info):
        self.updates.append(info)

    def set_note(self, text):
        self.notes.append(text)

    def set_busy(self, busy):
        self.busy.append(busy)

    def close(self):
        self.closed = True


def _info(lock=2, retries=3, locked=True, modem=MODEM):
    return modem_monitor.SimLockInfo(
        modem_path=modem, sim_path=SIM, state=2 if locked else 8,
        unlock_required=lock, retries={lock: retries}, locked=locked)


def _controller(shell_locked=False):
    views, sends, timers = [], [], []
    state = {'locked': shell_locked}

    def factory(info, submit, later):
        view = FakeView(info, submit, later)
        views.append(view)
        return view

    ctl = sim_pin.SimPinController(
        view_factory=factory, bus_provider=lambda: 'BUS',
        shell_locked=lambda: state['locked'],
        timeout_add=lambda s, cb: timers.append(cb) or len(timers),
        sender=lambda *a: sends.append(a))
    return ctl, views, sends, timers, state


def test_prompt_shows_and_pin_is_sent():
    ctl, views, sends, _t, _s = _controller()
    ctl.handle(_info())
    assert len(views) == 1 and ctl.visible
    views[0].submit('12', '', '')
    assert views[0].notes[-1] == 'A PIN is 4 to 8 digits.'
    assert sends == []
    views[0].submit('1234', '', '')
    bus, sim, lock, code, new_pin, done = sends[0]
    assert (bus, sim, lock, code) == ('BUS', SIM, 2, '1234')
    assert views[0].busy == [True]
    views[0].submit('1234', '', '')  # in flight: ignored
    assert len(sends) == 1
    done(True, '')
    assert views[0].closed and not ctl.visible


def test_wrong_pin_shows_error_and_new_attempts():
    ctl, views, sends, _t, _s = _controller()
    ctl.handle(_info(retries=3))
    views[0].submit('1111', '', '')
    sends[0][-1](False, 'Wrong PIN.')
    assert views[0].notes[-1] == 'Wrong PIN.'
    assert not views[0].closed
    ctl.handle(_info(retries=2))
    assert views[0].updates[-1].retries_left() == 2
    assert len(views) == 1


def test_puk_flow_validates_new_pin():
    ctl, views, sends, _t, _s = _controller()
    ctl.handle(_info(lock=4, retries=10))
    views[0].submit('12345678', '1234', '9999')
    assert views[0].notes[-1] == 'The new PINs do not match.'
    views[0].submit('12345678', '1234', '1234')
    assert sends[0][2:5] == (4, '12345678', '1234')


def test_deferred_while_shell_locked():
    ctl, views, _sends, timers, state = _controller(shell_locked=True)
    ctl.handle(_info())
    assert views == [] and len(timers) == 1
    ctl.handle(_info(retries=2))
    assert len(timers) == 1  # one poll at a time
    assert timers[0]() is False  # still locked: re-armed
    assert views == [] and len(timers) == 2
    state['locked'] = False
    sim_pin_hook = ctl.on_shell_unlocked
    sim_pin_hook()
    assert len(views) == 1 and views[0].info.retries_left() == 2


def test_later_dismisses_until_lock_changes():
    ctl, views, _sends, _t, _s = _controller()
    ctl.handle(_info())
    views[0].later()
    assert views[0].closed and not ctl.visible
    ctl.handle(_info(retries=3))
    assert len(views) == 1
    ctl.handle(_info(lock=4, retries=10))
    assert len(views) == 2
    views[1].later()
    ctl.reopen()
    assert len(views) == 3


def test_unlocked_report_closes_prompt():
    ctl, views, _sends, _t, _s = _controller()
    ctl.handle(_info())
    ctl.handle(_info(locked=False))
    assert views[0].closed and not ctl.visible


def test_monitor_default_routes_to_sim_pin(monkeypatch):
    got = []
    monkeypatch.setattr(sim_pin, 'handle_lock', got.append)
    modem_monitor._default_sim_handler(_info())
    assert got and got[0].locked


@pytest.mark.parametrize('props,locked', [
    ({'State': 2, 'UnlockRequired': 2}, True),
    ({'State': 2, 'UnlockRequired': 0}, True),
    ({'State': 2, 'UnlockRequired': 1}, False),
    ({'State': 8, 'UnlockRequired': 2}, False),
    ({'State': -1, 'UnlockRequired': 4}, False),
    ({}, False),
])
def test_lock_rule(props, locked):
    assert modem_monitor.sim_lock_info(MODEM, props).locked is locked
