"""ModemMonitor voice/SMS tracking over real ModemManager signals (W1).

ModemManager never publishes Call objects through the /org/freedesktop/
ModemManager1 ObjectManager — it only carries Modem objects there — so the
previous suite, which injected InterfacesAdded carrying the Call interface,
tested a flow that can never fire on hardware. Those tests are replaced
here with the real flows: Voice.CallAdded/CallDeleted per modem, async
Properties.GetAll per call, StateChanged + PropertiesChanged tracking
(Number often arrives after the first ring), initial adoption via
GetManagedObjects + Voice.ListCalls, and Modem.Messaging Added for SMS.

Fakes are always reached through modem_monitor.GLib/Gio: sibling test
modules also install fake gi packages at collection time, so sys.modules
contents are not stable but the module's own bound references are.
"""
import logging
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

    def _require_version(namespace, version):
        if namespace == 'Gtk4LayerShell':
            raise ValueError(f'{namespace} not available')
    gi_mod.require_version = _require_version
    sys.modules['gi'] = gi_mod
    sys.modules['gi.repository'] = gi_rep
    sys.modules['gi.repository.GLib'] = glib
    sys.modules['gi.repository.Gio'] = gio


_install_fake_gi()

import modem_monitor  # noqa: E402  (needs fake gi modules installed first)

_MODEM_IFACE = 'org.freedesktop.ModemManager1.Modem'
_VOICE_IFACE = 'org.freedesktop.ModemManager1.Modem.Voice'
_MSG_IFACE = 'org.freedesktop.ModemManager1.Modem.Messaging'
_CALL_IFACE = 'org.freedesktop.ModemManager1.Call'
_SMS_IFACE = 'org.freedesktop.ModemManager1.Sms'
_MM1 = 'org.freedesktop.ModemManager1'

MODEM = '/org/freedesktop/ModemManager1/Modem/0'
CALL1 = '/org/freedesktop/ModemManager1/Call/1'
CALL2 = '/org/freedesktop/ModemManager1/Call/2'


class FakeParams:
    def __init__(self, value):
        self._value = value

    def unpack(self):
        return self._value


class FakeReply:
    # Mirrors GLib: every reply is a tuple of out-args, so a single a{..}
    # out-arg (GetAll, GetManagedObjects) unpacks to a 1-tuple, not a dict.
    def __init__(self, value):
        self._value = value

    def unpack(self):
        if isinstance(self._value, dict):
            return (self._value,)
        return self._value


class FakeBus:
    def __init__(self):
        self.subs = []
        self.unsubs = []
        self.calls = []  # async dispatches: (bus, path, iface, method)
        self.pending = []  # async completion callbacks awaiting transport
        self._finish = []  # queued call_finish replies (values or Exception)
        self.sync_calls = []
        self.sync_replies = []
        self._next = 1

    def signal_subscribe(self, sender, iface, signal, path, *_rest):
        self.subs.append({'sender': sender, 'iface': iface,
                          'signal': signal, 'path': path})
        sid = self._next
        self._next += 1
        return sid

    def signal_unsubscribe(self, sid):
        self.unsubs.append(sid)

    def call(self, bus_name, path, iface, method, params=None, reply_type=None,
             flags=None, timeout=-1, cancellable=None, callback=None,
             *user_data):
        """Async Gio.DBusConnection.call with Gio's real positional layout:
        any user_data given is delivered to the callback as a third
        argument, exactly as PyGObject does (a stray None breaks 2-arg
        callbacks on a real bus)."""
        self.calls.append((bus_name, path, iface, method))
        self.pending.append((callback, user_data))

    def call_finish(self, _result):
        reply = self._finish.pop(0)
        if isinstance(reply, Exception):
            raise modem_monitor.GLib.Error(str(reply))
        return reply

    def call_sync(self, *args):
        self.sync_calls.append(args)
        reply = self.sync_replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def complete_next(self, reply) -> None:
        """Deliver the oldest outstanding async reply."""
        self._finish.append(reply)
        cb, user_data = self.pending.pop(0)
        cb(self, object(), *user_data)


def _make_monitor(bus, monkeypatch, drain_startup=True):
    """Monitor wired to a FakeBus with a drainable idle queue.

    Returns (monitor, fired, pending, drain); fired collects callback
    deliveries, pending is the live idle list, drain runs it. By default
    the startup GetManagedObjects round trip is completed empty so tests
    start from a clean enumerated state; enumeration tests opt out to
    drive that handshake themselves.
    """
    glib, gio = modem_monitor.GLib, modem_monitor.Gio
    pending: list = []
    fired = {'incoming': [], 'answered': [], 'ended': 0,
             'changed': [], 'sms': []}

    def _idle(cb, *args):
        pending.append((cb, args))
        return len(pending)

    monkeypatch.setattr(glib, 'idle_add', _idle)
    monkeypatch.setattr(gio, 'bus_get_sync', lambda *a, **k: bus)
    monitor = modem_monitor.ModemMonitor(
        on_incoming=lambda c, n: fired['incoming'].append((c, n)),
        on_answered=lambda c, n: fired['answered'].append((c, n)),
        on_ended=lambda: fired.__setitem__('ended', fired['ended'] + 1),
        on_calls_changed=lambda calls: fired['changed'].append(list(calls)),
        on_sms=lambda n, t: fired['sms'].append((n, t)),
    )

    def _drain():
        while pending:
            cb, args = pending.pop(0)
            cb(*args)

    _drain()  # runs _init_bus: subscribes + queues GetManagedObjects
    if drain_startup:
        bus.complete_next(FakeReply({}))
        _drain()
    return monitor, fired, pending, _drain


def _ringing_props(number='tel:+15550001111', direction=1, state=3):
    return {'Direction': direction, 'State': state, 'Number': number}


def _shown(number: str) -> str:
    """Expected caller text for an unsaved number: the xx_phone rule."""
    import xx_phone
    return xx_phone.display(number, xx_phone.user_country("", ""))


def _add_call(monitor, bus, drain, call_path, props, modem=MODEM):
    """Drive Voice.CallAdded through the async GetAll fetch."""
    monitor._on_call_added(
        None, None, modem, None, None, FakeParams((call_path,)), None)
    assert bus.calls[-1][1:] == (call_path, modem_monitor._PROPS_IFACE, 'GetAll')
    bus.complete_next(FakeReply(dict(props)))
    drain()


def _delete_call(monitor, bus, drain, call_path):
    monitor._on_call_deleted(
        None, None, MODEM, None, None, FakeParams((call_path,)), None)
    drain()


def _state_changed(monitor, drain, call_path, new_state):
    monitor._on_call_state_changed(
        None, None, call_path, None, None, FakeParams((3, new_state, 0)), None)
    drain()


def _number_changed(monitor, drain, call_path, number):
    monitor._on_call_properties_changed(
        None, None, call_path, None, None,
        FakeParams((_CALL_IFACE, {'Number': number}, [])), None)
    drain()


class TestVoiceSubscriptions:
    def test_init_subscribes_voice_and_messaging_signals(self, monkeypatch):
        bus = FakeBus()
        _make_monitor(bus, monkeypatch)
        got = {(s['iface'], s['signal'], s['path']) for s in bus.subs}
        assert (_VOICE_IFACE, 'CallAdded', None) in got
        assert (_VOICE_IFACE, 'CallDeleted', None) in got
        assert (_MSG_IFACE, 'Added', None) in got

    def test_voice_subscriptions_accept_any_path(self, monkeypatch):
        bus = FakeBus()
        _make_monitor(bus, monkeypatch)
        for sub in bus.subs:
            if sub['signal'] in ('CallAdded', 'CallDeleted', 'Added'):
                assert sub['sender'] == _MM1
                assert sub['path'] is None

    def test_call_adoption_subscribes_state_and_props(self, monkeypatch):
        bus = FakeBus()
        monitor, _, _, drain = _make_monitor(bus, monkeypatch)
        _add_call(monitor, bus, drain, CALL1, _ringing_props())
        watched = {(s['iface'], s['signal'], s['path']) for s in bus.subs}
        assert (_CALL_IFACE, 'StateChanged', CALL1) in watched
        assert (modem_monitor._PROPS_IFACE, 'PropertiesChanged', CALL1) in watched

    def test_terminated_call_unsubscribes(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        _add_call(monitor, bus, drain, CALL1, _ringing_props())
        assert len(bus.unsubs) == 0
        _state_changed(monitor, drain, CALL1, modem_monitor._STATE_TERMINATED)
        assert len(bus.unsubs) == 2
        assert CALL1 not in monitor._call_subs
        assert fired['ended'] == 1

    def test_call_deleted_unsubscribes(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        _add_call(monitor, bus, drain, CALL1, _ringing_props())
        sub_ids = set(range(1, bus._next))
        _delete_call(monitor, bus, drain, CALL1)
        assert len(bus.unsubs) == 2
        assert set(bus.unsubs) <= sub_ids
        assert fired['ended'] == 1


class TestIncomingFlow:
    def test_call_added_ringing_fires_incoming_with_stripped_number(
            self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        _add_call(monitor, bus, drain, CALL1, _ringing_props())
        assert fired['incoming'] == [(_shown('+15550001111'), '+15550001111')]
        assert fired['answered'] == []
        assert fired['ended'] == 0
        assert len(fired['changed']) == 1
        assert fired['changed'][0][0].number == '+15550001111'

    def test_incoming_uses_contact_name(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        monkeypatch.setattr(modem_monitor, '_display_name',
                            lambda n: 'Mom' if n == '+15550001111' else n)
        _add_call(monitor, bus, drain, CALL1, _ringing_props())
        assert fired['incoming'] == [('Mom', '+15550001111')]

    def test_late_number_arrives_via_properties_changed(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        _add_call(monitor, bus, drain, CALL1, _ringing_props(number=''))
        assert fired['incoming'] == [('', '')]
        _number_changed(monitor, drain, CALL1, 'tel:+15550001111')
        assert fired['incoming'][-1] == (
            _shown('+15550001111'), '+15550001111')
        assert monitor.calls()[0].number == '+15550001111'

    def test_active_transition_fires_answered_once_with_started_at(
            self, monkeypatch):
        import time as _time
        before = _time.monotonic()
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        _add_call(monitor, bus, drain, CALL1, _ringing_props())
        assert monitor.calls()[0].started_at is None
        _state_changed(monitor, drain, CALL1, modem_monitor._STATE_ACTIVE)
        assert fired['answered'] == [(_shown('+15550001111'), '+15550001111')]
        started = monitor.calls()[0].started_at
        assert started is not None and started >= before
        _state_changed(monitor, drain, CALL1, modem_monitor._STATE_HELD)
        _state_changed(monitor, drain, CALL1, modem_monitor._STATE_ACTIVE)
        assert len(fired['answered']) == 1
        assert monitor.calls()[0].started_at == started

    def test_adopted_active_call_fires_answered(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        _add_call(monitor, bus, drain, CALL1,
                  _ringing_props(state=modem_monitor._STATE_ACTIVE))
        assert fired['incoming'] == []
        assert len(fired['answered']) == 1
        assert monitor.calls()[0].started_at is not None

    def test_terminated_at_fetch_is_dropped_silently(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        _add_call(monitor, bus, drain, CALL1,
                  _ringing_props(state=modem_monitor._STATE_TERMINATED))
        assert monitor.calls() == []
        assert fired['incoming'] == []
        assert fired['ended'] == 0

    def test_getall_failure_leaves_no_trace(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        monitor._on_call_added(
            None, None, MODEM, None, None, FakeParams((CALL1,)), None)
        bus.complete_next(Exception('No such object'))
        drain()
        assert monitor.calls() == []
        assert fired['incoming'] == [] and fired['ended'] == 0


class TestInitialEnumeration:
    def test_existing_calls_adopted_from_list_calls(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch, drain_startup=False)
        assert bus.calls[-1][2:] == (modem_monitor._OBJMGR_IFACE,
                                     'GetManagedObjects')
        objects = {MODEM: {_MODEM_IFACE: {}, _VOICE_IFACE: {},
                            'org.freedesktop.DBus.Properties': {}}}
        bus.complete_next(FakeReply(objects))
        drain()
        assert bus.calls[-1][1:] == (
            MODEM, _VOICE_IFACE, 'ListCalls')
        bus.complete_next(FakeReply(([CALL1],)))
        drain()
        bus.complete_next(FakeReply(_ringing_props()))
        drain()
        assert fired['incoming'] == [(_shown('+15550001111'), '+15550001111')]
        assert modem_monitor.active_call_path() == CALL1

    def test_modem_added_triggers_list_calls(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch, drain_startup=False)
        bus.complete_next(FakeReply({}))
        drain()
        assert fired['incoming'] == []
        monitor._on_interfaces_added(
            None, None, None, None, None,
            FakeParams((MODEM, {_MODEM_IFACE: {}, _VOICE_IFACE: {}})), None)
        assert bus.calls[-1][1:] == (MODEM, _VOICE_IFACE, 'ListCalls')
        bus.complete_next(FakeReply(([CALL1],)))
        drain()
        bus.complete_next(FakeReply(_ringing_props()))
        drain()
        assert len(fired['incoming']) == 1

    def test_modem_removed_drops_its_calls(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        _add_call(monitor, bus, drain, CALL1, _ringing_props())
        monitor._on_interfaces_removed(
            None, None, None, None, None,
            FakeParams((MODEM, [_MODEM_IFACE])), None)
        drain()
        assert monitor.calls() == []
        assert fired['ended'] == 1

    def test_modems_tracked_by_path(self, monkeypatch):
        bus = FakeBus()
        monitor, _, _, drain = _make_monitor(bus, monkeypatch)
        assert monitor.modem_path() is None
        monitor._on_interfaces_added(
            None, None, None, None, None,
            FakeParams((MODEM, {_MODEM_IFACE: {}, _VOICE_IFACE: {}})), None)
        drain()
        assert monitor.modem_path() == MODEM
        assert monitor.modem_path(_MSG_IFACE) == MODEM


class TestConcurrentCalls:
    def test_waiting_call_does_not_overwrite_first(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        _add_call(monitor, bus, drain, CALL1,
                  _ringing_props(state=modem_monitor._STATE_ACTIVE))
        _add_call(monitor, bus, drain, CALL2,
                  _ringing_props(number='tel:+15550002222',
                                 state=modem_monitor._STATE_WAITING))
        assert modem_monitor.active_call_path() == CALL1
        assert {c.path for c in monitor.calls()} == {CALL1, CALL2}
        assert fired['ended'] == 0

    def test_waiting_call_ending_does_not_end_first(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        _add_call(monitor, bus, drain, CALL1,
                  _ringing_props(state=modem_monitor._STATE_ACTIVE))
        _add_call(monitor, bus, drain, CALL2,
                  _ringing_props(number='tel:+15550002222',
                                 state=modem_monitor._STATE_WAITING))
        _state_changed(monitor, drain, CALL2, modem_monitor._STATE_TERMINATED)
        assert fired['ended'] == 0
        assert [c.path for c in monitor.calls()] == [CALL1]
        _state_changed(monitor, drain, CALL1, modem_monitor._STATE_TERMINATED)
        assert fired['ended'] == 1

    def test_ended_fires_once_for_last_call(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        _add_call(monitor, bus, drain, CALL1, _ringing_props())
        _delete_call(monitor, bus, drain, CALL1)
        assert fired['ended'] == 1
        _delete_call(monitor, bus, drain, CALL1)
        assert fired['ended'] == 1

    def test_new_call_after_ended_rings_again(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        _add_call(monitor, bus, drain, CALL1, _ringing_props())
        _delete_call(monitor, bus, drain, CALL1)
        assert fired['ended'] == 1
        _add_call(monitor, bus, drain, CALL2, _ringing_props())
        assert len(fired['incoming']) == 2
        _delete_call(monitor, bus, drain, CALL2)
        assert fired['ended'] == 2


class TestOutgoingCalls:
    def test_outgoing_dialing_surfaces_without_ringing(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        _add_call(monitor, bus, drain, CALL1,
                  _ringing_props(direction=2,
                                 state=modem_monitor._STATE_DIALING))
        assert fired['incoming'] == [] and fired['answered'] == []
        assert len(fired['changed']) == 1
        call = fired['changed'][0][0]
        assert (call.direction, call.state) == (2, modem_monitor._STATE_DIALING)
        assert modem_monitor.active_call_path() == CALL1

    def test_outgoing_answered_on_active(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        _add_call(monitor, bus, drain, CALL1,
                  _ringing_props(direction=2,
                                 state=modem_monitor._STATE_RINGING_OUT))
        assert fired['answered'] == []
        _state_changed(monitor, drain, CALL1, modem_monitor._STATE_ACTIVE)
        assert len(fired['answered']) == 1
        assert monitor.calls()[0].started_at is not None


class TestIncomingSms:
    def test_received_message_fires_on_sms(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        sms_path = '/org/freedesktop/ModemManager1/SMS/7'
        monitor._on_messaging_added(
            None, None, MODEM, None, None, FakeParams((sms_path, True)), None)
        assert bus.calls[-1][1:] == (sms_path, modem_monitor._PROPS_IFACE,
                                     'GetAll')
        bus.complete_next(FakeReply({'Number': 'tel:+15550001111',
                                     'Text': 'hello\nsecond line'}))
        drain()
        assert fired['sms'] == [('+15550001111', 'hello\nsecond line')]

    def test_unreceived_message_ignored(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        n_calls = len(bus.calls)
        monitor._on_messaging_added(
            None, None, MODEM, None, None,
            FakeParams(('/org/freedesktop/ModemManager1/SMS/8', False)), None)
        drain()
        assert len(bus.calls) == n_calls
        assert fired['sms'] == []

    def test_multipart_waits_for_received_state(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        timers = []
        monkeypatch.setattr(modem_monitor.GLib, 'timeout_add',
                            lambda ms, cb, *a: timers.append(cb) or 1)
        sms_path = '/org/freedesktop/ModemManager1/SMS/10'
        monitor._on_messaging_added(
            None, None, MODEM, None, None, FakeParams((sms_path, True)), None)
        bus.complete_next(FakeReply({
            'Number': '+15550001111', 'Text': 'part one',
            'State': modem_monitor._SMS_STATE_RECEIVING}))
        drain()
        assert fired['sms'] == [] and len(timers) == 1
        timers.pop()()
        bus.complete_next(FakeReply({
            'Number': '+15550001111', 'Text': 'part one part two', 'State': 3}))
        drain()
        assert fired['sms'] == [('+15550001111', 'part one part two')]

    def test_empty_sms_details_ignored(self, monkeypatch):
        bus = FakeBus()
        monitor, fired, _, drain = _make_monitor(bus, monkeypatch)
        monitor._on_messaging_added(
            None, None, MODEM, None, None,
            FakeParams(('/org/freedesktop/ModemManager1/SMS/9', True)), None)
        bus.complete_next(FakeReply({'Number': '', 'Text': ''}))
        drain()
        assert fired['sms'] == []


class TestModemResolution:
    def test_prefers_voice_modem(self):
        other = '/org/freedesktop/ModemManager1/Modem/1'
        objects = {
            other: {_MODEM_IFACE: {}, _MSG_IFACE: {}},
            MODEM: {_MODEM_IFACE: {}, _VOICE_IFACE: {}},
        }
        assert modem_monitor._pick_modem_path(objects, _VOICE_IFACE) == MODEM
        assert modem_monitor._pick_modem_path(objects, _MSG_IFACE) == other

    def test_falls_back_to_any_modem(self):
        objects = {MODEM: {_MODEM_IFACE: {}}}
        assert modem_monitor._pick_modem_path(objects, _VOICE_IFACE) == MODEM

    def test_none_without_modems(self):
        assert modem_monitor._pick_modem_path({}, _VOICE_IFACE) is None
        objects = {'/org/freedesktop/ModemManager1/Call/1': {_CALL_IFACE: {}}}
        assert modem_monitor._pick_modem_path(objects, _VOICE_IFACE) is None

    def test_resolve_uses_monitor_cache(self, monkeypatch):
        bus = FakeBus()
        monitor, _, _, _drain = _make_monitor(bus, monkeypatch)
        monitor._modems[MODEM] = {_MODEM_IFACE, _VOICE_IFACE}
        monkeypatch.setattr(modem_monitor.Gio, 'bus_get_sync',
                            lambda *a, **k: (_ for _ in ()).throw(
                                AssertionError('no bus probe expected')))
        assert modem_monitor.resolve_modem_path() == MODEM

    def test_resolve_probes_bus_without_monitor(self, monkeypatch):
        bus = FakeBus()
        bus.sync_replies.append(FakeReply(
            {MODEM: {_MODEM_IFACE: {}, _VOICE_IFACE: {}}}))
        monkeypatch.setattr(modem_monitor, '_last_monitor', None)
        monkeypatch.setattr(modem_monitor.Gio, 'bus_get_sync',
                            lambda *a, **k: bus)
        assert modem_monitor.resolve_modem_path() == MODEM
        assert bus.sync_calls[-1][2:4] == (modem_monitor._OBJMGR_IFACE,
                                          'GetManagedObjects')

    def test_resolve_none_when_bus_down(self, monkeypatch):
        monkeypatch.setattr(modem_monitor, '_last_monitor', None)
        monkeypatch.setattr(modem_monitor.Gio, 'bus_get_sync',
                            lambda *a, **k: None)
        assert modem_monitor.resolve_modem_path() is None


class TestSessionBusOwnership:
    @staticmethod
    def _session_bus(monkeypatch):
        """The shared Gio fake comes from whichever sibling module imported
        first and may lack SESSION; script what this suite needs."""
        import types as _types
        monkeypatch.setattr(modem_monitor.Gio, 'BusType',
                            _types.SimpleNamespace(SYSTEM=1, SESSION=2))

    def test_owned_name_reports_true(self, monkeypatch):
        self._session_bus(monkeypatch)
        bus = FakeBus()
        bus.sync_replies.append(FakeReply((':1.42',)))
        monkeypatch.setattr(modem_monitor.Gio, 'bus_get_sync',
                            lambda *a, **k: bus)
        assert modem_monitor.bus_name_has_owner('org.gnome.Calls') is True
        assert bus.sync_calls[0][3] == 'GetNameOwner'

    def test_unowned_name_reports_false(self, monkeypatch):
        self._session_bus(monkeypatch)
        bus = FakeBus()
        bus.sync_replies.append(Exception(
            'org.freedesktop.DBus.Error.NameHasNoOwner'))
        monkeypatch.setattr(modem_monitor.Gio, 'bus_get_sync',
                            lambda *a, **k: bus)
        assert modem_monitor.bus_name_has_owner('sm.puri.Chatty') is False

    def test_missing_bus_reports_false(self, monkeypatch):
        self._session_bus(monkeypatch)
        monkeypatch.setattr(modem_monitor.Gio, 'bus_get_sync',
                            lambda *a, **k: None)
        assert modem_monitor.bus_name_has_owner('org.gnome.Calls') is False


class TestCallInfo:
    def test_started_at_unset_until_active(self, monkeypatch):
        bus = FakeBus()
        monitor, _, _, drain = _make_monitor(bus, monkeypatch)
        _add_call(monitor, bus, drain, CALL1, _ringing_props())
        (call,) = monitor.calls()
        assert isinstance(call, modem_monitor.CallInfo)
        assert (call.path, call.number, call.direction, call.state) == \
            (CALL1, '+15550001111', 1, 3)
        assert call.started_at is None

    def test_calls_returns_copies(self, monkeypatch):
        bus = FakeBus()
        monitor, _, _, drain = _make_monitor(bus, monkeypatch)
        _add_call(monitor, bus, drain, CALL1, _ringing_props())
        monitor.calls()[0].number = 'mutated'
        assert monitor.calls()[0].number == '+15550001111'


class TestAcceptHangupDBus:
    """Async accept/hangup: dispatch records the MM1 method, the completion
    callback delivers (success, error) exactly once, and local failures
    (no monitor / no live bus) deliver immediately without touching D-Bus."""

    def test_accept_dispatches_async_and_delivers_success(self, monkeypatch):
        bus = FakeBus()
        _make_monitor(bus, monkeypatch)
        delivered = []
        modem_monitor.accept_call(
            '/org/freedesktop/ModemManager1/Call/7',
            lambda ok, err: delivered.append((ok, err)),
        )
        assert delivered == []  # nothing until the transport answers
        assert bus.calls[-1] == (
            _MM1, '/org/freedesktop/ModemManager1/Call/7', _CALL_IFACE, 'Accept',
        )
        bus.complete_next(FakeReply(()))
        assert delivered == [(True, None)]

    def test_hangup_dispatches_async_and_delivers_success(self, monkeypatch):
        bus = FakeBus()
        _make_monitor(bus, monkeypatch)
        delivered = []
        modem_monitor.hangup_call(
            '/org/freedesktop/ModemManager1/Call/9',
            lambda ok, err: delivered.append((ok, err)),
        )
        assert delivered == []
        assert bus.calls[-1] == (
            _MM1, '/org/freedesktop/ModemManager1/Call/9', _CALL_IFACE, 'Hangup',
        )
        bus.complete_next(FakeReply(()))
        assert delivered == [(True, None)]

    def test_no_monitor_delivers_immediate_failure(self, monkeypatch, caplog):
        monkeypatch.setattr(modem_monitor, '_last_monitor', None)
        delivered = []
        with caplog.at_level(logging.WARNING):
            modem_monitor.accept_call(
                '/org/freedesktop/ModemManager1/Call/1',
                lambda ok, err: delivered.append((ok, err)),
            )
            modem_monitor.hangup_call(
                '/org/freedesktop/ModemManager1/Call/1',
                lambda ok, err: delivered.append((ok, err)),
            )
        assert delivered == [(False, 'system bus unavailable'),
                             (False, 'system bus unavailable')]
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 2

    def test_failed_bus_init_delivers_immediate_failure(self, monkeypatch):
        glib, gio = modem_monitor.GLib, modem_monitor.Gio

        def _fail(*a, **k):
            raise glib.Error('system bus not reachable')

        monkeypatch.setattr(glib, 'idle_add', lambda cb, *a: cb(*a) or 1)
        monkeypatch.setattr(gio, 'bus_get_sync', _fail)
        modem_monitor.ModemMonitor(lambda c, n: None, lambda c, n: None,
                                   lambda: None)
        delivered = []
        modem_monitor.hangup_call('/org/freedesktop/ModemManager1/Call/2',
                                  lambda ok, err: delivered.append((ok, err)))
        assert delivered == [(False, 'system bus unavailable')]

    def test_transport_error_surfaces_message(self, monkeypatch, caplog):
        bus = FakeBus()
        _make_monitor(bus, monkeypatch)
        delivered = []
        with caplog.at_level(logging.WARNING):
            modem_monitor.hangup_call(
                '/org/freedesktop/ModemManager1/Call/404',
                lambda ok, err: delivered.append((ok, err)),
            )
            bus.complete_next(Exception('No such object'))
        assert delivered == [(False, 'No such object')]
        assert any('Hangup' in r.getMessage() and 'No such object' in r.getMessage()
                   for r in caplog.records)

    def test_each_request_delivers_exactly_once(self, monkeypatch):
        bus = FakeBus()
        _make_monitor(bus, monkeypatch)
        delivered = []

        def _cb(ok, err):
            delivered.append((ok, err))

        modem_monitor.accept_call('/org/freedesktop/ModemManager1/Call/10', _cb)
        modem_monitor.hangup_call('/org/freedesktop/ModemManager1/Call/11', _cb)
        bus.complete_next(Exception('first failed'))
        bus.complete_next(FakeReply(()))
        assert delivered == [(False, 'first failed'), (True, None)]

    def test_sync_dispatch_failure_still_delivers_callback(self, monkeypatch):
        bus = FakeBus()

        def _dead_transport(*a, **k):
            raise modem_monitor.GLib.Error('connection is closed')

        bus.call = _dead_transport
        _make_monitor(bus, monkeypatch, drain_startup=False)
        delivered = []
        modem_monitor.accept_call('/org/freedesktop/ModemManager1/Call/12',
                                  lambda ok, err: delivered.append((ok, err)))
        assert delivered == [(False, 'connection is closed')]
        assert bus.pending == []


class TestBusInit:
    def test_bus_init_failure_logged_once(self, monkeypatch, caplog):
        glib, gio = modem_monitor.GLib, modem_monitor.Gio

        def _fail(*a, **k):
            raise glib.Error('system bus not reachable')

        monkeypatch.setattr(glib, 'idle_add', lambda cb, *a: cb(*a) or 1)
        monkeypatch.setattr(gio, 'bus_get_sync', _fail)
        with caplog.at_level(logging.WARNING):
            modem_monitor.ModemMonitor(lambda c, n: None, lambda c, n: None,
                                       lambda: None)
        bus_warnings = [r for r in caplog.records
                        if r.levelno == logging.WARNING and 'bus' in r.getMessage()]
        assert len(bus_warnings) == 1


class TestActiveCallPath:
    def test_tracks_preferred_live_call(self, monkeypatch):
        bus = FakeBus()
        monitor, _, _, drain = _make_monitor(bus, monkeypatch)
        assert modem_monitor.active_call_path() is None
        _add_call(monitor, bus, drain, CALL1, _ringing_props())
        assert modem_monitor.active_call_path() == CALL1
        _delete_call(monitor, bus, drain, CALL1)
        assert modem_monitor.active_call_path() is None

    def test_none_without_monitor(self, monkeypatch):
        monkeypatch.setattr(modem_monitor, '_last_monitor', None)
        assert modem_monitor.active_call_path() is None


class TestDisplayName:
    """Unknown numbers read through xx_phone; saved names win."""

    def test_unknown_number_formatted_through_xx_phone(self, monkeypatch):
        import types as _types
        import contacts
        import xx_phone
        monkeypatch.setattr(
            contacts, 'shared_book',
            lambda config=None: _types.SimpleNamespace(lookup_number=lambda n: None))
        number = '+16145550177'
        assert modem_monitor._display_name(number) == xx_phone.display(
            number, xx_phone.user_country("", ""))

    def test_saved_name_wins_over_number(self, monkeypatch):
        import types as _types
        import contacts
        monkeypatch.setattr(
            contacts, 'shared_book',
            lambda config=None: _types.SimpleNamespace(lookup_number=lambda n: 'Mom'))
        assert modem_monitor._display_name('+16145550177') == 'Mom'

    def test_empty_number_stays_empty(self):
        assert modem_monitor._display_name('') == ''
