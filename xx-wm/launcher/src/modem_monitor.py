"""
Watches ModemManager1 DBus for voice calls and incoming SMS, emitting
callbacks so the shell can show/hide CallUI and CallBar and post SMS
notifications.

ModemManager never publishes Call objects through the /org/freedesktop/
ModemManager1 ObjectManager — it only carries Modem objects there. Calls
are announced per modem by org.freedesktop.ModemManager1.Modem.Voice
signals CallAdded(o path) / CallDeleted(o path) on the modem object path,
so this monitor subscribes to those (plus Modem.Messaging Added for SMS)
and fetches each call's properties asynchronously. The remote Number
often arrives after the first ring, so every call also gets a
PropertiesChanged subscription to pick up late fields.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Callable

from gi.repository import GLib, Gio

_MM1 = 'org.freedesktop.ModemManager1'
_MM1_PATH = '/org/freedesktop/ModemManager1'
_MODEM_IFACE = 'org.freedesktop.ModemManager1.Modem'
_VOICE_IFACE = 'org.freedesktop.ModemManager1.Modem.Voice'
_MSG_IFACE = 'org.freedesktop.ModemManager1.Modem.Messaging'
_CALL_IFACE = 'org.freedesktop.ModemManager1.Call'
_SMS_IFACE = 'org.freedesktop.ModemManager1.Sms'
_OBJMGR_IFACE = 'org.freedesktop.DBus.ObjectManager'
_PROPS_IFACE = 'org.freedesktop.DBus.Properties'
_DBUS_IFACE = 'org.freedesktop.DBus'
_DBUS_PATH = '/org/freedesktop/DBus'

CALLS_BUS_NAME = 'org.gnome.Calls'
CHATTY_BUS_NAME = 'sm.puri.Chatty'
TXXT_BUS_NAME = 'io.piercingxx.TxxT.Daemon'

_DIR_INCOMING = 1
_DIR_OUTGOING = 2
_STATE_DIALING = 1
_STATE_RINGING_OUT = 2
_STATE_RINGING_IN = 3
_STATE_ACTIVE = 4
_STATE_HELD = 5
_STATE_WAITING = 6
_STATE_TERMINATED = 7
_LIVE_STATES = frozenset({
    _STATE_DIALING, _STATE_RINGING_OUT, _STATE_RINGING_IN,
    _STATE_ACTIVE, _STATE_HELD, _STATE_WAITING,
})

_CALL_TIMEOUT_MS = 5_000
_ENUM_TIMEOUT_MS = 8_000
_OWNER_PROBE_TIMEOUT_MS = 500
_IO_TIMEOUT_MS = 15_000

_last_monitor: ModemMonitor | None = None


@dataclass
class CallInfo:
    """One tracked call: path identity, remote party, direction/state codes
    matching the MM1 Call interface, and the monotonic timestamp of the
    first transition to ACTIVE (None until then)."""

    path: str
    number: str = ''
    direction: int = 0
    state: int = 0
    started_at: float | None = None


CallState = CallInfo


# MM1 Number property returns the remote party URI (tel:+1234567890)
def _strip_tel(number: str) -> str:
    return number.removeprefix('tel:').strip() or number


def _display_name(number: str) -> str:
    if not number:
        return ''
    try:
        from contacts import shared_book, display_number
        return shared_book().lookup_number(number) or display_number(number)
    except Exception:
        return number


def _err_message(err: Exception) -> str:
    return getattr(err, 'message', None) or str(err)


def active_call_path() -> str | None:
    """Object path of the preferred live call, if any (incoming ringing
    first, then active, then any other non-terminated call)."""
    if _last_monitor is None:
        return None
    return _last_monitor._active_call_path


CallResultCallback = Callable[[bool, str | None], None]


def accept_call(call_path: str, on_done: CallResultCallback) -> None:
    """
    Asynchronously Accept the call at ``call_path``.

    ``on_done(success, error)`` fires exactly once on the main loop when the
    MM1 method completes — or immediately on local failure (no monitor, no
    live bus connection). The D-Bus round trip rides Gio's async machinery,
    so a wedged ModemManager can never block the calling thread.
    """
    _call_method(call_path, 'Accept', on_done)


def hangup_call(call_path: str, on_done: CallResultCallback) -> None:
    """Asynchronously Hangup; same delivery contract as :func:`accept_call`."""
    _call_method(call_path, 'Hangup', on_done)


def _call_method(call_path: str, method: str, on_done: CallResultCallback) -> None:
    from shell_log import get_logger

    def _fail(message: str) -> None:
        get_logger('modem_monitor').warning('%s %s failed: %s', call_path, method, message)
        on_done(False, message)

    monitor = _last_monitor
    bus = monitor._bus if monitor is not None else None
    if bus is None:
        # No live system-bus connection (no monitor yet, or its bus init
        # failed): fail immediately instead of stalling the UI thread.
        _fail('system bus unavailable')
        return

    def _on_called(conn, result) -> None:
        try:
            conn.call_finish(result)
        except Exception as err:  # noqa: BLE001 - exactly-once contract:
            # any completion failure must reach the callback or the UI
            # pending guard sticks until the next call resets it.
            _fail(_err_message(err))
            return
        on_done(True, None)

    try:
        bus.call(
            _MM1, call_path, _CALL_IFACE, method, None, None,
            Gio.DBusCallFlags.NONE, _CALL_TIMEOUT_MS, None, _on_called,
        )
    except GLib.Error as err:
        # Dispatch itself failed (e.g. connection died between the check and
        # the call): still honour the exactly-once callback contract.
        _fail(getattr(err, 'message', None) or str(err))


def bus_name_has_owner(name: str, timeout_ms: int = _OWNER_PROBE_TIMEOUT_MS) -> bool:
    """True when some process owns ``name`` on the session bus.

    Used to defer to the device's real telephony apps (GNOME Calls for
    voice, Chatty or TxxT for SMS): when they own their bus name they
    handle the user-visible surface, so the shell must stay quiet.
    """
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        if bus is None:
            return False
        owner = bus.call_sync(
            'org.freedesktop.DBus', _DBUS_PATH, _DBUS_IFACE, 'GetNameOwner',
            GLib.Variant('(s)', (name,)), None,
            Gio.DBusCallFlags.NONE, timeout_ms, None,
        ).unpack()[0]
    except Exception:
        return False
    return bool(owner)


def _pick_modem_path(objects: dict, interface: str) -> str | None:
    """First modem object path carrying ``interface`` (Voice/Messaging),
    falling back to the first modem of any kind. None when no modem exists."""
    fallback: str | None = None
    for path in sorted(objects):
        ifaces = objects[path]
        if _MODEM_IFACE not in ifaces:
            continue
        if fallback is None:
            fallback = path
        if interface in ifaces:
            return path
    return fallback


def _sync_system_bus() -> Gio.DBusConnection | None:
    try:
        return Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    except Exception:
        return None


def resolve_modem_path(
    interface: str = _VOICE_IFACE, timeout_ms: int = _ENUM_TIMEOUT_MS,
) -> str | None:
    """Object path of the first modem carrying ``interface``.

    Reads the live monitor's cache when available, otherwise probes the
    system bus synchronously — call only from a worker thread, never the
    GTK main loop. Modems re-index after resets, so resolve per operation
    instead of caching the result.
    """
    monitor = _last_monitor
    if monitor is not None:
        cached = monitor.modem_path(interface)
        if cached is not None:
            return cached
    bus = _sync_system_bus()
    if bus is None:
        return None
    try:
        objects = bus.call_sync(
            _MM1, _MM1_PATH, _OBJMGR_IFACE, 'GetManagedObjects',
            None, None, Gio.DBusCallFlags.NONE, timeout_ms, None,
        ).unpack()[0]
    except Exception:
        return None
    try:
        return _pick_modem_path(dict(objects), interface)
    except Exception:
        return None


def voice_call_params(number: str, glib=None):
    """``(a{sv})`` params for Voice.CreateCall, built in one step.

    Never nest a prebuilt ``GLib.Variant('a{sv}', ...)`` inside the tuple:
    real PyGObject raises ``KeyError: 0`` for that, so pass a plain dict
    whose values are Variants.
    """
    glib = glib if glib is not None else GLib
    return glib.Variant('(a{sv})', ({'number': glib.Variant('s', number)},))


def sms_create_params(number: str, text: str, glib=None):
    """``(a{sv})`` params for Messaging.Create; same one-step rule."""
    glib = glib if glib is not None else GLib
    return glib.Variant('(a{sv})', ({
        'number': glib.Variant('s', number),
        'text': glib.Variant('s', text),
    },))


def voice_create_and_start(
    number: str, modem: str | None = None, timeout_ms: int = _IO_TIMEOUT_MS,
) -> tuple[bool, str]:
    """Create a voice call to ``number`` and Start it via D-Bus.

    Returns (True, call path) or (False, error detail). Blocking: call
    only from a worker thread.
    """
    from shell_log import get_logger
    bus = _sync_system_bus()
    if bus is None:
        return False, 'system bus unavailable'
    modem = modem or resolve_modem_path(_VOICE_IFACE)
    if modem is None:
        return False, 'no modem found'
    try:
        call_path = bus.call_sync(
            _MM1, modem, _VOICE_IFACE, 'CreateCall',
            voice_call_params(number), None,
            Gio.DBusCallFlags.NONE, timeout_ms, None,
        ).unpack()[0]
        bus.call_sync(
            _MM1, call_path, _CALL_IFACE, 'Start',
            None, None, Gio.DBusCallFlags.NONE, timeout_ms, None,
        )
    except Exception as err:
        detail = _err_message(err)
        get_logger('modem_monitor').warning('call to %s failed: %s', number, detail)
        return False, detail
    return True, str(call_path)


def messaging_create_and_send(
    number: str, text: str, modem: str | None = None,
    timeout_ms: int = _IO_TIMEOUT_MS,
) -> tuple[bool, str]:
    """Create an SMS and Send it via D-Bus (no mmcli output scraping, so
    multi-line text survives intact). Blocking: worker thread only."""
    from shell_log import get_logger
    bus = _sync_system_bus()
    if bus is None:
        return False, 'system bus unavailable'
    modem = modem or resolve_modem_path(_MSG_IFACE)
    if modem is None:
        return False, 'no modem found'
    try:
        sms_path = bus.call_sync(
            _MM1, modem, _MSG_IFACE, 'Create',
            sms_create_params(number, text), None,
            Gio.DBusCallFlags.NONE, timeout_ms, None,
        ).unpack()[0]
        bus.call_sync(
            _MM1, str(sms_path), _SMS_IFACE, 'Send',
            None, None, Gio.DBusCallFlags.NONE, timeout_ms, None,
        )
    except Exception as err:
        detail = _err_message(err)
        get_logger('modem_monitor').warning('SMS to %s failed: %s', number, detail)
        return False, detail
    return True, str(sms_path)


_SMS_SUBMIT_PDUS = frozenset({3, 4})
_SMS_STATE_RECEIVING = 2
_SMS_RECEIVE_POLLS = 15
_SMS_RECEIVE_POLL_MS = 2_000


def messaging_list(
    modem: str | None = None, timeout_ms: int = _ENUM_TIMEOUT_MS,
) -> list[dict] | None:
    """Full SMS store via Messaging.List + per-message GetAll.

    Returns the history list, or None on transport failure (so callers can
    fall back to mmcli scraping). Blocking: worker thread only.
    """
    bus = _sync_system_bus()
    if bus is None:
        return None
    modem = modem or resolve_modem_path(_MSG_IFACE)
    if modem is None:
        return None
    try:
        paths = bus.call_sync(
            _MM1, modem, _MSG_IFACE, 'List',
            None, None, Gio.DBusCallFlags.NONE, timeout_ms, None,
        ).unpack()[0]
        messages: list[dict] = []
        for sms_path in paths:
            props = dict(bus.call_sync(
                _MM1, str(sms_path), _PROPS_IFACE, 'GetAll',
                GLib.Variant('(s)', (_SMS_IFACE,)), None,
                Gio.DBusCallFlags.NONE, timeout_ms, None,
            ).unpack()[0])
            number = _strip_tel(str(props.get('Number', '')))
            text = str(props.get('Text', ''))
            if not number or not text:
                continue
            try:
                pdu = int(props.get('PduType', 0))
            except (TypeError, ValueError):
                pdu = 0
            messages.append({
                'number': number,
                'text': text,
                'outgoing': pdu in _SMS_SUBMIT_PDUS,
                'timestamp_iso': str(props.get('Timestamp', '')),
            })
    except Exception:
        return None
    return messages


class ModemMonitor:
    """
    Subscribe once; fires callbacks on the GLib main loop.

    Primary contract: ``on_calls_changed(calls)`` receives a snapshot list
    of every tracked call on each change; ``on_sms(number, text)`` fires
    for each received message. The legacy ``on_incoming(caller, number)`` /
    ``on_answered(caller, number)`` / ``on_ended()`` callbacks are kept as
    thin adapters: an incoming ringing call fires on_incoming, any call's
    first transition to active fires on_answered, and on_ended fires once
    when the last non-terminated call is gone. ``calls()`` exposes the
    current snapshot for the UI to render from.
    """

    def __init__(
        self,
        on_incoming: Callable[[str, str], None] | None = None,
        on_answered: Callable[[str, str], None] | None = None,
        on_ended: Callable[[], None] | None = None,
        *,
        on_calls_changed: Callable[[list[CallInfo]], None] | None = None,
        on_sms: Callable[[str, str], None] | None = None,
    ) -> None:
        self._on_incoming = on_incoming or (lambda _c, _n: None)
        self._on_answered = on_answered or (lambda _c, _n: None)
        self._on_ended = on_ended or (lambda: None)
        self._on_calls_changed = on_calls_changed
        self._on_sms = on_sms
        self._bus: Gio.DBusConnection | None = None
        self._calls: dict[str, CallInfo] = {}
        self._fetching: set[str] = set()
        self._modems: dict[str, set[str]] = {}
        self._call_modem: dict[str, str] = {}
        self._call_subs: dict[str, list[int]] = {}
        self._announced_incoming: set[str] = set()
        self._announced_active: set[str] = set()
        self._ended_announced = False
        self._active_call_path: str | None = None
        global _last_monitor
        _last_monitor = self
        GLib.idle_add(self._init_bus)

    # ------------------------------------------------------------ bus setup

    def _init_bus(self) -> bool:
        from shell_log import get_logger
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            self._bus.signal_subscribe(
                _MM1, _VOICE_IFACE, 'CallAdded', None,
                None, Gio.DBusSignalFlags.NONE,
                self._on_call_added, None,
            )
            self._bus.signal_subscribe(
                _MM1, _VOICE_IFACE, 'CallDeleted', None,
                None, Gio.DBusSignalFlags.NONE,
                self._on_call_deleted, None,
            )
            self._bus.signal_subscribe(
                _MM1, _MSG_IFACE, 'Added', None,
                None, Gio.DBusSignalFlags.NONE,
                self._on_messaging_added, None,
            )
            self._bus.signal_subscribe(
                _MM1, _OBJMGR_IFACE, 'InterfacesAdded', _MM1_PATH,
                None, Gio.DBusSignalFlags.NONE,
                self._on_interfaces_added, None,
            )
            self._bus.signal_subscribe(
                _MM1, _OBJMGR_IFACE, 'InterfacesRemoved', _MM1_PATH,
                None, Gio.DBusSignalFlags.NONE,
                self._on_interfaces_removed, None,
            )
            self._enumerate()
        except GLib.Error as err:
            get_logger('modem_monitor').warning('system bus unavailable: %s', err)
        return False

    # ------------------------------------------------------------- tracking

    def calls(self) -> list[CallInfo]:
        """Snapshot of every tracked call for the UI to render from."""
        return [replace(call) for call in self._calls.values()]

    def modem_path(self, interface: str = _VOICE_IFACE) -> str | None:
        """First known modem object path carrying ``interface``."""
        return _pick_modem_path(self._modems, interface)

    def _enumerate(self) -> None:
        if self._bus is None:
            return
        self._bus.call(
            _MM1, _MM1_PATH, _OBJMGR_IFACE, 'GetManagedObjects',
            None, None, Gio.DBusCallFlags.NONE, _ENUM_TIMEOUT_MS, None,
            self._on_managed_objects,
        )

    def _on_managed_objects(self, conn, result) -> None:
        from shell_log import get_logger
        try:
            objects = dict(conn.call_finish(result).unpack()[0])
        except Exception as err:
            get_logger('modem_monitor').warning(
                'GetManagedObjects failed: %s', _err_message(err))
            return
        for obj_path, interfaces in objects.items():
            if _MODEM_IFACE in interfaces:
                self._modems[obj_path] = set(interfaces)
        for modem_path, ifaces in self._modems.items():
            if _VOICE_IFACE in ifaces:
                self._list_calls(modem_path)

    def _list_calls(self, modem_path: str) -> None:
        if self._bus is None:
            return
        self._bus.call(
            _MM1, modem_path, _VOICE_IFACE, 'ListCalls',
            None, None, Gio.DBusCallFlags.NONE, _ENUM_TIMEOUT_MS, None,
            lambda conn, result: self._on_list_calls(modem_path, conn, result),
        )

    def _on_list_calls(self, modem_path: str, conn, result) -> None:
        from shell_log import get_logger
        try:
            paths = conn.call_finish(result).unpack()[0]
        except Exception as err:
            get_logger('modem_monitor').warning(
                'ListCalls on %s failed: %s', modem_path, _err_message(err))
            return
        for call_path in paths:
            self._adopt_call(str(call_path), modem_path)

    def _adopt_call(self, call_path: str, modem_path: str | None = None) -> None:
        if call_path in self._calls or call_path in self._fetching:
            return
        if self._bus is None:
            return
        self._fetching.add(call_path)
        if modem_path is not None:
            self._call_modem[call_path] = modem_path
        self._bus.call(
            _MM1, call_path, _PROPS_IFACE, 'GetAll',
            GLib.Variant('(s)', (_CALL_IFACE,)), None,
            Gio.DBusCallFlags.NONE, _CALL_TIMEOUT_MS, None,
            lambda conn, result: self._on_call_props(call_path, conn, result),
        )

    def _on_call_props(self, call_path: str, conn, result) -> None:
        from shell_log import get_logger
        try:
            props = dict(conn.call_finish(result).unpack()[0])
        except Exception as err:
            get_logger('modem_monitor').warning(
                'GetAll on %s failed: %s', call_path, _err_message(err))
            self._fetching.discard(call_path)
            return
        self._fetching.discard(call_path)
        try:
            direction = int(props.get('Direction', 0))
            state = int(props.get('State', 0))
        except (TypeError, ValueError):
            direction, state = 0, 0
        number = _strip_tel(str(props.get('Number', '')))
        if state == _STATE_TERMINATED:
            self._drop_call(call_path)
            return
        call = self._calls.get(call_path)
        if call is None:
            call = CallInfo(path=call_path)
            self._calls[call_path] = call
        call.direction = direction
        call.state = state
        call.number = number
        self._subscribe_call(call_path)
        self._ended_announced = False
        if direction == _DIR_INCOMING and state == _STATE_RINGING_IN:
            self._announce_incoming(call_path)
        if state == _STATE_ACTIVE:
            self._announce_active(call_path)
        self._refresh_active_path()
        self._emit_changed()

    def _subscribe_call(self, call_path: str) -> None:
        if call_path in self._call_subs or self._bus is None:
            return
        state_sub = self._bus.signal_subscribe(
            _MM1, _CALL_IFACE, 'StateChanged', call_path,
            None, Gio.DBusSignalFlags.NONE,
            self._on_call_state_changed, None,
        )
        props_sub = self._bus.signal_subscribe(
            _MM1, _PROPS_IFACE, 'PropertiesChanged', call_path,
            None, Gio.DBusSignalFlags.NONE,
            self._on_call_properties_changed, None,
        )
        self._call_subs[call_path] = [state_sub, props_sub]

    def _drop_subscription(self, call_path: str) -> None:
        sub_ids = self._call_subs.pop(call_path, [])
        if self._bus is not None:
            for sub_id in sub_ids:
                self._bus.signal_unsubscribe(sub_id)

    def _drop_call(self, call_path: str) -> None:
        tracked = self._calls.pop(call_path, None)
        self._fetching.discard(call_path)
        self._call_modem.pop(call_path, None)
        self._announced_incoming.discard(call_path)
        self._announced_active.discard(call_path)
        self._drop_subscription(call_path)
        if tracked is None:
            return
        self._refresh_active_path()
        self._emit_changed()
        if not self._calls and not self._ended_announced:
            self._ended_announced = True
            GLib.idle_add(self._on_ended)

    def _announce_incoming(self, call_path: str) -> None:
        if call_path in self._announced_incoming:
            return
        call = self._calls.get(call_path)
        if call is None:
            return
        self._announced_incoming.add(call_path)
        caller = _display_name(call.number)
        GLib.idle_add(self._on_incoming, caller, call.number)

    def _announce_active(self, call_path: str) -> None:
        if call_path in self._announced_active:
            return
        call = self._calls.get(call_path)
        if call is None:
            return
        self._announced_active.add(call_path)
        if call.started_at is None:
            call.started_at = time.monotonic()
        caller = _display_name(call.number)
        GLib.idle_add(self._on_answered, caller, call.number)

    def _refresh_active_path(self) -> None:
        live = [c for c in self._calls.values() if c.state in _LIVE_STATES]
        if not live:
            self._active_call_path = None
            return
        for call in live:
            if call.direction == _DIR_INCOMING and call.state == _STATE_RINGING_IN:
                self._active_call_path = call.path
                return
        for call in live:
            if call.state == _STATE_ACTIVE:
                self._active_call_path = call.path
                return
        self._active_call_path = live[0].path

    def _emit_changed(self) -> None:
        if self._on_calls_changed is None:
            return
        GLib.idle_add(self._on_calls_changed, self.calls())

    # -------------------------------------------------------------- signals

    def _on_call_added(
        self, _conn, _sender, modem_path, _iface, _sig, params, _ud,
    ) -> None:
        unpacked = params.unpack()
        call_path = unpacked[0] if isinstance(unpacked, (tuple, list)) else unpacked
        self._adopt_call(str(call_path), str(modem_path))

    def _on_call_deleted(
        self, _conn, _sender, _modem_path, _iface, _sig, params, _ud,
    ) -> None:
        unpacked = params.unpack()
        call_path = unpacked[0] if isinstance(unpacked, (tuple, list)) else unpacked
        self._drop_call(str(call_path))

    def _on_call_state_changed(
        self, _conn, _sender, call_path, _iface, _sig, params, _ud,
    ) -> None:
        _old, new_state, _reason = params.unpack()
        call = self._calls.get(call_path)
        if call is None:
            return
        try:
            new_state = int(new_state)
        except (TypeError, ValueError):
            return
        call.state = new_state
        if new_state == _STATE_ACTIVE:
            self._announce_active(call_path)
        elif new_state == _STATE_TERMINATED:
            self._drop_call(call_path)
            return
        elif (new_state == _STATE_RINGING_IN
                and call.direction == _DIR_INCOMING):
            # A waiting call that the network promotes to ringing never
            # passed through the initial fetch announcement: screen it
            # (consult) and ring it like any other incoming call.
            self._announce_incoming(call_path)
        self._refresh_active_path()
        self._emit_changed()

    def _on_call_properties_changed(
        self, _conn, _sender, call_path, _iface, _sig, params, _ud,
    ) -> None:
        unpacked = params.unpack()
        iface_name, changed, _invalidated = unpacked[0], unpacked[1], unpacked[2]
        if iface_name != _CALL_IFACE:
            return
        call = self._calls.get(call_path)
        if call is None:
            return
        try:
            changed = dict(changed)
        except (TypeError, ValueError):
            return
        if 'Number' not in changed:
            return
        number = _strip_tel(str(changed['Number']))
        if not number or number == call.number:
            return
        call.number = number
        if (call.direction == _DIR_INCOMING
                and call.state == _STATE_RINGING_IN
                and call_path in self._announced_incoming):
            self._announced_incoming.discard(call_path)
            self._announce_incoming(call_path)
        self._emit_changed()

    def _on_interfaces_added(
        self, _c, _sender, _path, _iface, _sig, params, _ud
    ) -> None:
        # Only Modem objects travel the ObjectManager; a re-probed modem
        # re-indexes, so re-attach and adopt whatever calls it already has.
        obj_path, interfaces = params.unpack()
        if _MODEM_IFACE not in interfaces:
            return
        self._modems[obj_path] = set(interfaces)
        if _VOICE_IFACE in interfaces:
            self._list_calls(obj_path)

    def _on_interfaces_removed(
        self, _c, _sender, _path, _iface, _sig, params, _ud
    ) -> None:
        obj_path, removed = params.unpack()
        if _MODEM_IFACE not in removed:
            return
        self._modems.pop(obj_path, None)
        for call_path in [p for p, m in self._call_modem.items() if m == obj_path]:
            self._drop_call(call_path)

    def _on_messaging_added(
        self, _conn, _sender, _modem_path, _iface, _sig, params, _ud,
    ) -> None:
        unpacked = params.unpack()
        sms_path, received = unpacked[0], unpacked[1]
        if not received or self._on_sms is None:
            return
        self._fetch_sms(str(sms_path), _SMS_RECEIVE_POLLS)

    def _fetch_sms(self, sms_path: str, polls_left: int) -> None:
        if self._bus is None:
            return
        self._bus.call(
            _MM1, sms_path, _PROPS_IFACE, 'GetAll',
            GLib.Variant('(s)', (_SMS_IFACE,)), None,
            Gio.DBusCallFlags.NONE, _CALL_TIMEOUT_MS, None,
            lambda conn, result: self._on_sms_props(sms_path, polls_left, conn, result),
        )

    def _on_sms_props(self, sms_path: str, polls_left: int, conn, result) -> None:
        from shell_log import get_logger
        try:
            props = dict(conn.call_finish(result).unpack()[0])
        except Exception as err:
            get_logger('modem_monitor').warning(
                'SMS GetAll failed: %s', _err_message(err))
            return
        # Added fires on the first part of a multi-part message; wait for
        # RECEIVED so the notification carries the whole text.
        if props.get('State') == _SMS_STATE_RECEIVING and polls_left > 0:
            GLib.timeout_add(
                _SMS_RECEIVE_POLL_MS,
                lambda: self._fetch_sms(sms_path, polls_left - 1) or False)
            return
        number = _strip_tel(str(props.get('Number', '')))
        text = str(props.get('Text', ''))
        if not number or not text:
            return
        GLib.idle_add(self._on_sms, number, text)
