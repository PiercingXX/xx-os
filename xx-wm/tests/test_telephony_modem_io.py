"""D-Bus modem I/O: voice call placement (W1 telephony).

Dialing goes through Voice.CreateCall + Call.Start directly, no mmcli
output scraping. Texting belongs to TxxT.

Fakes are reached through modem_monitor.GLib/Gio attributes, which the
tests script explicitly: sibling modules install their own fake gi
packages at collection time.
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

_MM1 = 'org.freedesktop.ModemManager1'
_VOICE_IFACE = 'org.freedesktop.ModemManager1.Modem.Voice'
_MSG_IFACE = 'org.freedesktop.ModemManager1.Modem.Messaging'
_CALL_IFACE = 'org.freedesktop.ModemManager1.Call'
_SMS_IFACE = 'org.freedesktop.ModemManager1.Sms'

MODEM = '/org/freedesktop/ModemManager1/Modem/0'
CALL1 = '/org/freedesktop/ModemManager1/Call/1'
SMS1 = '/org/freedesktop/ModemManager1/SMS/1'
SMS2 = '/org/freedesktop/ModemManager1/SMS/2'


class FakeVariant:
    def __init__(self, fmt, value=None):
        self.fmt = fmt
        self.value = value

    def unpack(self):
        return self.value


class FakeReply:
    # Mirrors GLib: every reply is a tuple of out-args, so a single a{..}
    # out-arg (GetAll, GetManagedObjects) unpacks to a 1-tuple, not a dict.
    def __init__(self, value):
        self._value = value

    def unpack(self):
        if isinstance(self._value, dict):
            return (self._value,)
        return self._value


class FakeSyncBus:
    def __init__(self, replies):
        self.sync_calls = []
        self.sync_replies = list(replies)

    def call_sync(self, *args):
        self.sync_calls.append(args)
        reply = self.sync_replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _script_bus(monkeypatch, replies):
    bus = FakeSyncBus(replies)
    monkeypatch.setattr(modem_monitor.GLib, 'Variant', FakeVariant)
    monkeypatch.setattr(modem_monitor.Gio, 'bus_get_sync',
                        lambda *a, **k: bus)
    return bus


def _props_arg(call_args):
    """Unwrap the (a{sv}) params variant into a plain {name: value} dict.

    Real PyGObject rejects a prebuilt a{sv} Variant nested in the tuple
    (KeyError: 0), so production builds the params in one step: the tuple
    holds a plain dict of Variants.
    """
    params = call_args[4]
    assert params.fmt == '(a{sv})'
    (props,) = params.value
    assert isinstance(props, dict)
    return {k: v.value for k, v in props.items()}


class TestVoiceCreateAndStart:
    def test_create_then_start_with_number_prop(self, monkeypatch):
        bus = _script_bus(monkeypatch, [FakeReply((CALL1,)), FakeReply(())])
        ok, path = modem_monitor.voice_create_and_start(
            '+15550001111', modem=MODEM)
        assert (ok, path) == (True, CALL1)
        assert bus.sync_calls[0][:4] == (_MM1, MODEM, _VOICE_IFACE, 'CreateCall')
        assert _props_arg(bus.sync_calls[0]) == {'number': '+15550001111'}
        assert bus.sync_calls[1][:4] == (_MM1, CALL1, _CALL_IFACE, 'Start')

    def test_resolves_modem_when_not_given(self, monkeypatch):
        bus = _script_bus(monkeypatch, [FakeReply((CALL1,)), FakeReply(())])
        monkeypatch.setattr(modem_monitor, 'resolve_modem_path',
                            lambda *a, **k: MODEM)
        ok, _ = modem_monitor.voice_create_and_start('+15550001111')
        assert ok is True
        assert bus.sync_calls[0][1] == MODEM

    def test_no_bus_reports_unavailable(self, monkeypatch):
        monkeypatch.setattr(modem_monitor.Gio, 'bus_get_sync',
                            lambda *a, **k: None)
        assert modem_monitor.voice_create_and_start('+1', modem=MODEM) == \
            (False, 'system bus unavailable')

    def test_no_modem_reports_not_found(self, monkeypatch):
        _script_bus(monkeypatch, [])
        monkeypatch.setattr(modem_monitor, 'resolve_modem_path',
                            lambda *a, **k: None)
        assert modem_monitor.voice_create_and_start('+1') == \
            (False, 'no modem found')

    def test_create_failure_reports_detail(self, monkeypatch):
        bus = _script_bus(monkeypatch, [Exception('rejected by modem')])
        ok, detail = modem_monitor.voice_create_and_start('+1', modem=MODEM)
        assert ok is False
        assert detail == 'rejected by modem'
        assert len(bus.sync_calls) == 1
