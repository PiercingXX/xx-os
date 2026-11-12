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

ModemManager itself can restart (crash, package upgrade, modem reset): the
monitor watches its system-bus name, forgets every modem and call when it
vanishes (ending any call surface through the normal ended path, so no
ghost call screen stays up) and re-enumerates when it comes back. Calls
that reach TERMINATED are removed from ModemManager with Voice.DeleteCall
(GNOME Calls does the same), and the terminate reason is kept as a short
text on the call (``CallInfo.end_reason``, :func:`last_ended_call`).

Modem state: every modem's ``Modem.State`` / ``UnlockRequired`` /
``UnlockRetries`` are kept from GetManagedObjects and followed through
``Modem.StateChanged`` and the Modem ``PropertiesChanged``. A SIM that
needs a PIN/PUK (State LOCKED) is reported as a :class:`SimLockInfo` to
``on_sim_lock`` (default: the shell's ``sim_pin`` prompt); the same report
with ``locked=False`` withdraws it. When a modem reaches REGISTERED the
monitor re-lists its calls (catch-up for a call that rang while it was
still registering — sxmo's modem hook does the same sweep).
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
_SIM_IFACE = 'org.freedesktop.ModemManager1.Sim'
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

#: MMModemState values the monitor acts on.
_MODEM_STATE_FAILED = -1
_MODEM_STATE_LOCKED = 2
_MODEM_STATE_REGISTERED = 8
#: MMModemLock NONE: no PIN/PUK needed.
_LOCK_NONE = 1
_LIVE_STATES = frozenset({
    _STATE_DIALING, _STATE_RINGING_OUT, _STATE_RINGING_IN,
    _STATE_ACTIVE, _STATE_HELD, _STATE_WAITING,
})

#: MMCallStateReason to the short text shown/logged when a call ends
#: (GNOME Calls' STATE_REASON_MAP, shortened for a phone surface).
_END_REASONS = {
    0: 'Call ended',
    1: 'Call ended',
    2: 'Call ended',
    3: 'Call ended',
    4: 'Call ended',
    5: 'Busy',
    6: 'Network error',
    7: 'Audio setup failed',
    8: 'Call transferred',
    9: 'Call deflected',
}


def end_reason_text(reason) -> str:
    """Short text for an MMCallStateReason code (``5`` reads 'Busy');
    unknown codes read 'Call ended'."""
    try:
        return _END_REASONS.get(int(reason), 'Call ended')
    except (TypeError, ValueError):
        return 'Call ended'

_CALL_TIMEOUT_MS = 5_000
_ENUM_TIMEOUT_MS = 8_000
_IO_TIMEOUT_MS = 15_000
#: Backoff between GetAll attempts on a new call; one entry per retry.
_FETCH_RETRY_MS = (250, 500, 1_000, 2_000)
#: How long an incoming call with no number waits for caller ID before
#: it is announced (and screened) as withheld.
_CALLER_ID_WAIT_MS = 1_500

_last_monitor: ModemMonitor | None = None

#: Session-bus apps that take over the call or SMS surface when they run.
_TELEPHONY_APP_NAMES = (CALLS_BUS_NAME, CHATTY_BUS_NAME, TXXT_BUS_NAME)
_name_watches: dict[str, int] = {}
_name_owned: dict[str, bool] = {}
_activatable: set[str] = set()


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
    state_reason: int = 0
    end_reason: str = ''
    ended_at: float | None = None


CallState = CallInfo


@dataclass
class SimLockInfo:
    """Lock state of one modem's SIM: ``unlock_required`` is the
    MMModemLock code (2 SIM PIN, 4 SIM PUK, ...), ``retries`` the
    UnlockRetries map (lock code → attempts left), ``sim_path`` the
    modem's Sim object for SendPin/SendPuk."""

    modem_path: str
    sim_path: str = ''
    state: int = 0
    unlock_required: int = 0
    retries: dict | None = None
    locked: bool = False

    def retries_left(self) -> int | None:
        """Attempts left for the current lock, None when unknown."""
        try:
            value = (self.retries or {}).get(self.unlock_required)
            return None if value is None else int(value)
        except (TypeError, ValueError, AttributeError):
            return None


def sim_lock_info(modem_path: str, props: dict) -> SimLockInfo:
    """SimLockInfo from Modem interface properties. Locked means State
    LOCKED with a lock other than NONE (phosh-wwan-mm's rule); a modem
    still probing (lock UNKNOWN) only counts once it reports LOCKED."""
    props = props or {}

    def _int(key, default=0):
        try:
            return int(props.get(key, default))
        except (TypeError, ValueError):
            return default

    state = _int('State')
    lock = _int('UnlockRequired')
    retries: dict = {}
    try:
        for key, value in dict(props.get('UnlockRetries') or {}).items():
            retries[int(key)] = int(value)
    except (TypeError, ValueError, AttributeError):
        retries = {}
    sim = str(props.get('Sim') or '')
    if sim == '/':
        sim = ''
    locked = state == _MODEM_STATE_LOCKED and lock != _LOCK_NONE
    return SimLockInfo(modem_path=modem_path, sim_path=sim, state=state,
                       unlock_required=lock, retries=retries, locked=locked)


def _modem_iface_props(interfaces) -> dict:
    """The Modem interface's properties from an ObjectManager entry
    (``{iface: {prop: value}}``); {} for any other shape."""
    if not isinstance(interfaces, dict):
        return {}
    props = interfaces.get(_MODEM_IFACE)
    return props if isinstance(props, dict) else {}


def _default_sim_handler(info: SimLockInfo) -> None:
    """Route a lock report to the shell's SIM PIN prompt (sim_pin.py);
    a missing or broken prompt never takes the call monitor down."""
    try:
        import sim_pin
        sim_pin.handle_lock(info)
    except Exception as err:  # noqa: BLE001 - optional surface
        from shell_log import get_logger
        get_logger('modem_monitor').warning('SIM prompt unavailable: %s', err)


def current_sim_locks() -> list[SimLockInfo]:
    """Every modem whose SIM currently needs a PIN/PUK (for a settings
    entry that re-opens a dismissed prompt)."""
    monitor = _last_monitor
    if monitor is None:
        return []
    return [info for info in (
        sim_lock_info(path, props)
        for path, props in sorted(monitor._modem_props.items()))
        if info.locked]


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


def last_ended_call() -> CallInfo | None:
    """The most recently terminated call (``end_reason`` filled in, e.g.
    'Busy'), or None. For the call-ended path: the legacy ``on_ended``
    callback carries no arguments."""
    if _last_monitor is None:
        return None
    ended = _last_monitor._last_ended
    return replace(ended) if ended is not None else None


def watch_call_end(call_path: str, on_end: Callable[[str], None]) -> bool:
    """Call ``on_end(text)`` once on the main loop when the call at
    ``call_path`` ends: ``text`` is the short reason for an abnormal end
    ('Busy', 'Network error') and '' for an ordinary one. For the
    "Dialing…" line of whoever placed the call. False when no monitor
    runs, so nothing would ever report."""
    monitor = _last_monitor
    if monitor is None:
        return False
    monitor._watch_end(str(call_path), on_end)
    return True


def _end_text(call: CallInfo | None) -> str:
    if call is None or call.state_reason in (0, 1, 2, 3, 4):
        return ''
    return call.end_reason or end_reason_text(call.state_reason)


def active_call_path() -> str | None:
    """Object path of the preferred live call, if any (incoming ringing
    first, then active, then any other non-terminated call)."""
    if _last_monitor is None:
        return None
    return _last_monitor._active_call_path


CallResultCallback = Callable[[bool, str | None], None]


def _consult_check_number(number: str, on_verdict) -> None:
    """Dialer CheckNumber consult (call_screen), fail-open to ring."""
    from call_screen import consult_check_number
    consult_check_number(number, on_verdict)


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


def bus_name_has_owner(name: str, timeout_ms: int | None = None) -> bool:
    """True when some process owns ``name`` on the session bus.

    Used to defer to the device's real telephony apps (GNOME Calls for
    voice, Chatty or TxxT for SMS): when they own their bus name they
    handle the user-visible surface, so the shell must stay quiet. Read
    from a Gio.bus_watch_name watcher (:func:`watch_telephony_apps`), so
    it never blocks; ``timeout_ms`` is accepted for old callers and
    ignored. A name nobody watched yet starts a watch and reads False.
    """
    _watch_session_name(name)
    return bool(_name_owned.get(name, False))


def bus_name_activatable(name: str) -> bool:
    """True when the session bus can start ``name`` on demand (a D-Bus
    service file is installed), whether or not it runs now."""
    return name in _activatable


def watch_telephony_apps() -> None:
    """Follow GNOME Calls, Chatty and TxxT on the session bus (one
    watcher each) and load the activatable names. Idempotent."""
    for name in _TELEPHONY_APP_NAMES:
        _watch_session_name(name)
    _refresh_activatable()


def _watch_session_name(name: str) -> None:
    if name in _name_watches:
        return
    try:
        _name_watches[name] = Gio.bus_watch_name(
            Gio.BusType.SESSION, name, Gio.BusNameWatcherFlags.NONE,
            _on_session_name_appeared, _on_session_name_vanished)
    except Exception as err:  # noqa: BLE001 - reads as not running
        _name_watches[name] = 0
        from shell_log import get_logger
        get_logger('modem_monitor').warning(
            'cannot watch %s: %s', name, _err_message(err))


def _on_session_name_appeared(_conn, name, _owner) -> None:
    _name_owned[str(name)] = True
    _refresh_activatable()


def _on_session_name_vanished(_conn, name) -> None:
    _name_owned[str(name)] = False
    _refresh_activatable()


def _refresh_activatable() -> None:
    """ListActivatableNames on the session bus, async; the cached set
    stays as it was when the bus cannot be reached."""
    def _on_reply(conn, result) -> None:
        try:
            names = conn.call_finish(result).unpack()[0]
        except Exception:  # noqa: BLE001 - keep the last good answer
            return
        _activatable.clear()
        _activatable.update(str(n) for n in names
                            if str(n) in _TELEPHONY_APP_NAMES)

    def _on_bus(_source, result) -> None:
        try:
            conn = Gio.bus_get_finish(result)
            conn.call(
                'org.freedesktop.DBus', _DBUS_PATH, _DBUS_IFACE,
                'ListActivatableNames', None, None,
                Gio.DBusCallFlags.NONE, _CALL_TIMEOUT_MS, None, _on_reply)
        except Exception:  # noqa: BLE001 - optional
            return

    try:
        Gio.bus_get(Gio.BusType.SESSION, None, _on_bus)
    except Exception:  # noqa: BLE001 - optional
        pass


def start_bus_name(name: str) -> None:
    """Ask the session bus to start an activatable service now
    (StartServiceByName, async, errors ignored)."""
    def _on_reply(conn, result) -> None:
        try:
            conn.call_finish(result)
        except Exception as err:  # noqa: BLE001 - logged only
            from shell_log import get_logger
            get_logger('modem_monitor').warning(
                'cannot start %s: %s', name, _err_message(err))

    def _on_bus(_source, result) -> None:
        try:
            conn = Gio.bus_get_finish(result)
            conn.call(
                'org.freedesktop.DBus', _DBUS_PATH, _DBUS_IFACE,
                'StartServiceByName', GLib.Variant('(su)', (name, 0)), None,
                Gio.DBusCallFlags.NONE, _IO_TIMEOUT_MS, None, _on_reply)
        except Exception:  # noqa: BLE001 - optional
            return

    try:
        Gio.bus_get(Gio.BusType.SESSION, None, _on_bus)
    except Exception:  # noqa: BLE001 - optional
        pass


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
    call_path = None
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
        if call_path is not None:
            _delete_created_call(bus, modem, str(call_path), timeout_ms)
        return False, detail
    return True, str(call_path)


def _delete_created_call(bus, modem: str, call_path: str,
                         timeout_ms: int) -> None:
    """Voice.DeleteCall for a call whose Start failed: it would sit in
    state UNKNOWN until ModemManager restarts. Worker thread only."""
    try:
        bus.call_sync(
            _MM1, modem, _VOICE_IFACE, 'DeleteCall',
            GLib.Variant('(o)', (call_path,)), None,
            Gio.DBusCallFlags.NONE, timeout_ms, None,
        )
    except Exception:  # noqa: BLE001 - already gone is fine
        pass


_SMS_STATE_RECEIVING = 2
_SMS_RECEIVE_POLLS = 15
_SMS_RECEIVE_POLL_MS = 2_000
#: Messages the shell posted itself, remembered to drop a copy that
#: ModemManager announces again (modem re-enable before the delete).
_SMS_SEEN_MAX = 128


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
        on_sim_lock: Callable[[SimLockInfo], None] | None = None,
    ) -> None:
        self._on_incoming = on_incoming or (lambda _c, _n: None)
        self._on_answered = on_answered or (lambda _c, _n: None)
        self._on_ended = on_ended or (lambda: None)
        self._on_calls_changed = on_calls_changed
        self._on_sms = on_sms
        self._on_sim_lock = on_sim_lock or _default_sim_handler
        self._modem_props: dict[str, dict] = {}
        self._lock_seen: dict[str, tuple] = {}
        self._bus: Gio.DBusConnection | None = None
        self._calls: dict[str, CallInfo] = {}
        self._fetching: set[str] = set()
        self._fetch_attempts: dict[str, int] = {}
        self._modems: dict[str, set[str]] = {}
        self._call_modem: dict[str, str] = {}
        self._call_subs: dict[str, list[int]] = {}
        self._announced_incoming: set[str] = set()
        self._announced_active: set[str] = set()
        # True while no live call is up (nothing for on_ended to report).
        self._ended_announced = True
        self._active_call_path: str | None = None
        self._screened_waiting: set[str] = set()
        self._number_wait: dict[str, int] = {}
        self._number_waited: set[str] = set()
        self._last_ended: CallInfo | None = None
        self._sms_seen: list[tuple] = []
        self._end_watchers: dict[str, list] = {}
        self._mm_watch: int | None = None
        self._mm_owner: str | None = None
        self._mm_vanished = False
        self._subs: list[int] = []
        self._closed = False
        global _last_monitor
        _last_monitor = self
        GLib.idle_add(self._init_bus)

    def close(self) -> None:
        """Stop following ModemManager: every signal subscription and the
        bus-name watch are dropped and no callback fires after this. Calls
        stay as they are on the modem for the next monitor to adopt."""
        global _last_monitor
        self._closed = True
        def quiet(*_a) -> None:
            return None

        self._on_incoming = self._on_answered = quiet
        self._on_ended = self._on_sim_lock = quiet
        self._on_calls_changed = None
        self._on_sms = None
        for call_path in list(self._number_wait):
            self._cancel_number_wait(call_path)
        for call_path in list(self._call_subs):
            self._drop_subscription(call_path)
        if self._bus is not None:
            for sub_id in self._subs:
                self._bus.signal_unsubscribe(sub_id)
        self._subs = []
        self._bus = None
        if self._mm_watch is not None:
            try:
                Gio.bus_unwatch_name(self._mm_watch)
            except Exception:  # noqa: BLE001 - already gone
                pass
            self._mm_watch = None
        self._end_watchers.clear()
        if _last_monitor is self:
            _last_monitor = None

    def _watch_end(self, call_path: str, on_end) -> None:
        ended = self._last_ended
        if (call_path not in self._calls and call_path not in self._fetching
                and ended is not None and ended.path == call_path):
            GLib.idle_add(on_end, _end_text(ended))
            return
        self._end_watchers.setdefault(call_path, []).append(on_end)

    # ------------------------------------------------------------ bus setup

    def _init_bus(self) -> bool:
        from shell_log import get_logger
        if self._closed:
            return False
        watch_telephony_apps()
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            for iface, signal, path, arg0, handler in (
                    (_VOICE_IFACE, 'CallAdded', None, None,
                     self._on_call_added),
                    (_VOICE_IFACE, 'CallDeleted', None, None,
                     self._on_call_deleted),
                    (_MSG_IFACE, 'Added', None, None,
                     self._on_messaging_added),
                    (_MODEM_IFACE, 'StateChanged', None, None,
                     self._on_modem_state_changed),
                    (_PROPS_IFACE, 'PropertiesChanged', None, _MODEM_IFACE,
                     self._on_modem_properties_changed),
                    (_OBJMGR_IFACE, 'InterfacesAdded', _MM1_PATH, None,
                     self._on_interfaces_added),
                    (_OBJMGR_IFACE, 'InterfacesRemoved', _MM1_PATH, None,
                     self._on_interfaces_removed)):
                self._subs.append(self._bus.signal_subscribe(
                    _MM1, iface, signal, path, arg0,
                    Gio.DBusSignalFlags.NONE, handler, None,
                ))
            self._enumerate()
        except GLib.Error as err:
            get_logger('modem_monitor').warning('system bus unavailable: %s', err)
            return False
        self._watch_modem_manager()
        return False

    def _watch_modem_manager(self) -> None:
        """Follow ModemManager's bus name: a restart drops every object
        path we hold, so vanish forgets them and appear re-enumerates."""
        if self._mm_watch is not None:
            return
        try:
            self._mm_watch = Gio.bus_watch_name(
                Gio.BusType.SYSTEM, _MM1, Gio.BusNameWatcherFlags.NONE,
                self._on_mm_appeared, self._on_mm_vanished)
        except Exception as err:  # noqa: BLE001 - optional robustness only
            from shell_log import get_logger
            get_logger('modem_monitor').warning(
                'cannot watch %s: %s', _MM1, _err_message(err))

    def _on_mm_appeared(self, _conn, _name, owner) -> None:
        previous, self._mm_owner = self._mm_owner, owner
        if previous is not None and previous != owner:
            # A new instance without a vanish in between (fast restart):
            # every path the old one handed out is dead.
            self._forget_modem_manager()
            self._mm_vanished = True
        if self._mm_vanished:
            self._mm_vanished = False
            self._enumerate()

    def _on_mm_vanished(self, _conn, _name) -> None:
        self._mm_owner = None
        self._mm_vanished = True
        self._forget_modem_manager()

    def _forget_modem_manager(self) -> None:
        """Drop every modem and call: each call leaves through _drop_call,
        so the snapshot empties and on_ended fires once (call UI closes)."""
        from shell_log import get_logger
        if self._calls or self._modems:
            get_logger('modem_monitor').warning(
                'ModemManager went away; dropping %d call(s), %d modem(s)',
                len(self._calls), len(self._modems))
        self._modems.clear()
        for modem_path in list(self._modem_props):
            self._forget_modem_state(modem_path)
        for call_path in list(self._calls) + list(self._fetching):
            self._drop_call(call_path)

    # ------------------------------------------------------------- tracking

    def calls(self) -> list[CallInfo]:
        """Snapshot of every live call for the UI to render from. A call
        still in state UNKNOWN (created, never started: a failed Start
        leaves one) is tracked but not live, so it never shows a surface
        or holds the phone awake."""
        return [replace(call) for call in self._calls.values()
                if call.state in _LIVE_STATES]

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
                self._update_modem_props(
                    obj_path, _modem_iface_props(interfaces), replace_all=True)
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
        self._fetch_attempts[call_path] = 0
        if modem_path is not None:
            self._call_modem[call_path] = modem_path
        # Watch the call before asking for its state: a change that lands
        # between the GetAll reply and a later subscription would be lost.
        self._subscribe_call(call_path)
        self._fetch_call(call_path)

    def _fetch_call(self, call_path: str) -> bool:
        if call_path not in self._fetching or self._bus is None:
            return False
        self._bus.call(
            _MM1, call_path, _PROPS_IFACE, 'GetAll',
            GLib.Variant('(s)', (_CALL_IFACE,)), None,
            Gio.DBusCallFlags.NONE, _CALL_TIMEOUT_MS, None,
            lambda conn, result: self._on_call_props(call_path, conn, result),
        )
        return False

    def _on_call_props(self, call_path: str, conn, result) -> None:
        from shell_log import get_logger
        try:
            props = dict(conn.call_finish(result).unpack()[0])
        except Exception as err:
            if call_path not in self._fetching:
                return
            attempt = self._fetch_attempts.get(call_path, 0)
            if attempt < len(_FETCH_RETRY_MS):
                delay = _FETCH_RETRY_MS[attempt]
                self._fetch_attempts[call_path] = attempt + 1
                get_logger('modem_monitor').warning(
                    'GetAll on %s failed (%s); retrying in %d ms',
                    call_path, _err_message(err), delay)
                GLib.timeout_add(delay, self._fetch_call, call_path)
                return
            get_logger('modem_monitor').warning(
                'GetAll on %s failed: %s; giving up',
                call_path, _err_message(err))
            self._drop_call(call_path)
            return
        if call_path not in self._fetching and call_path not in self._calls:
            # Dropped while the fetch was in flight (CallDeleted, or
            # ModemManager went away): never resurrect a dead path.
            return
        self._fetching.discard(call_path)
        self._fetch_attempts.pop(call_path, None)
        try:
            direction = int(props.get('Direction', 0))
            state = int(props.get('State', 0))
        except (TypeError, ValueError):
            direction, state = 0, 0
        number = _strip_tel(str(props.get('Number', '')))
        try:
            reason = int(props.get('StateReason', 0))
        except (TypeError, ValueError):
            reason = 0
        if state == _STATE_TERMINATED:
            # Ended before we looked, or a leftover found at enumerate:
            # ModemManager keeps it until someone deletes it.
            tracked = self._calls.get(call_path)
            if tracked is not None:
                tracked.state_reason = reason
            self._delete_call(call_path)
            self._drop_call(call_path, terminated=True)
            return
        call = self._calls.get(call_path)
        if call is None:
            call = CallInfo(path=call_path)
            self._calls[call_path] = call
        call.direction = direction
        call.state = state
        call.number = number
        call.state_reason = reason
        self._subscribe_call(call_path)
        if state in _LIVE_STATES:
            self._ended_announced = False
        if direction == _DIR_INCOMING and state == _STATE_RINGING_IN:
            self._announce_incoming(call_path)
        elif direction == _DIR_INCOMING and state == _STATE_WAITING:
            self._screen_waiting(call_path)
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

    def _drop_call(self, call_path: str, terminated: bool = False) -> None:
        tracked = self._calls.pop(call_path, None)
        self._fetching.discard(call_path)
        self._fetch_attempts.pop(call_path, None)
        self._call_modem.pop(call_path, None)
        self._announced_incoming.discard(call_path)
        self._announced_active.discard(call_path)
        self._screened_waiting.discard(call_path)
        self._cancel_number_wait(call_path)
        self._number_waited.discard(call_path)
        self._drop_subscription(call_path)
        if terminated and tracked is not None:
            tracked.state = _STATE_TERMINATED
            tracked.end_reason = end_reason_text(tracked.state_reason)
            tracked.ended_at = time.monotonic()
        for on_end in self._end_watchers.pop(call_path, []):
            GLib.idle_add(on_end, _end_text(tracked) if terminated else '')
        if tracked is None:
            return
        if terminated:
            self._last_ended = tracked
            if tracked.state_reason not in (0, 1, 2, 3, 4):
                from shell_log import get_logger
                get_logger('modem_monitor').info(
                    'call %s ended: %s', call_path, tracked.end_reason)
        self._refresh_active_path()
        self._emit_changed()
        if not self.calls() and not self._ended_announced:
            self._ended_announced = True
            GLib.idle_add(self._on_ended)

    def _delete_call(self, call_path: str) -> None:
        """Voice.DeleteCall for a TERMINATED call (fire-and-forget: another
        client may already have deleted it, and errors change nothing)."""
        modem_path = self._call_modem.get(call_path)
        if modem_path is None or self._bus is None:
            return

        def _on_deleted(conn, result) -> None:
            try:
                conn.call_finish(result)
            except Exception:  # noqa: BLE001 - already gone is fine
                pass

        try:
            self._bus.call(
                _MM1, modem_path, _VOICE_IFACE, 'DeleteCall',
                GLib.Variant('(o)', (call_path,)), None,
                Gio.DBusCallFlags.NONE, _CALL_TIMEOUT_MS, None, _on_deleted,
            )
        except Exception:  # noqa: BLE001 - best effort cleanup
            pass

    def _screen_waiting(self, call_path: str) -> None:
        """Consult the dialer policy (CheckNumber) for a second incoming
        call that arrives WAITING. It never passes RINGING_IN while the
        first call is up, so the ringing path would never screen it. The
        waiting banner stays the UI; a block verdict hangs up only this
        call (Call.Hangup on its own path, never Voice.HangupAll), and a
        verdict that lands after the call was answered, promoted to
        ringing (screened there) or renumbered is ignored."""
        if (call_path in self._screened_waiting
                or call_path in self._announced_incoming):
            return
        call = self._calls.get(call_path)
        if call is None:
            return
        if self._await_number(call):
            return
        self._screened_waiting.add(call_path)
        number = call.number

        def _on_verdict(verdict: str, reason: str) -> None:
            current = self._calls.get(call_path)
            if (current is None or current.state != _STATE_WAITING
                    or current.number != number or verdict != 'block'):
                return
            from shell_log import get_logger
            get_logger('modem_monitor').info(
                'blocked waiting call from %s (%s)', number or 'withheld', reason)
            hangup_call(call_path, lambda _ok, _err: None)

        try:
            _consult_check_number(number, _on_verdict)
        except Exception:  # noqa: BLE001 - screening fails open (rings)
            pass

    def _await_number(self, call: CallInfo) -> bool:
        """True while ``call`` has no number yet and still waits for
        caller ID (up to _CALLER_ID_WAIT_MS): screening on an empty number
        would treat a late caller ID as a withheld caller."""
        if call.number or call.path in self._number_waited:
            return False
        if call.path not in self._number_wait:
            self._number_wait[call.path] = GLib.timeout_add(
                _CALLER_ID_WAIT_MS, self._on_number_wait_over, call.path)
        return True

    def _cancel_number_wait(self, call_path: str) -> None:
        source = self._number_wait.pop(call_path, None)
        if source is not None:
            try:
                GLib.source_remove(source)
            except Exception:  # noqa: BLE001 - already fired
                pass

    def _on_number_wait_over(self, call_path: str) -> bool:
        if self._number_wait.pop(call_path, None) is None:
            return False
        self._number_waited.add(call_path)
        self._screen_incoming(call_path)
        return False

    def _screen_incoming(self, call_path: str) -> None:
        """Announce a ringing call or screen a waiting one, whichever
        state the incoming call is in now."""
        call = self._calls.get(call_path)
        if call is None or call.direction != _DIR_INCOMING:
            return
        if call.state == _STATE_RINGING_IN:
            self._announce_incoming(call_path)
        elif call.state == _STATE_WAITING:
            self._screen_waiting(call_path)

    def _announce_incoming(self, call_path: str) -> None:
        if call_path in self._announced_incoming:
            return
        call = self._calls.get(call_path)
        if call is None:
            return
        if self._await_number(call):
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
        _old, new_state, reason = params.unpack()
        try:
            new_state = int(new_state)
        except (TypeError, ValueError):
            return
        call = self._calls.get(call_path)
        if call is None:
            if (call_path in self._fetching
                    and new_state == _STATE_TERMINATED):
                # Ended before its first GetAll came back: the reply in
                # flight is older than this and must not ring.
                self._delete_call(call_path)
                self._drop_call(call_path, terminated=True)
            return
        try:
            call.state_reason = int(reason)
        except (TypeError, ValueError):
            pass
        call.state = new_state
        if new_state in _LIVE_STATES:
            self._ended_announced = False
        if new_state == _STATE_ACTIVE:
            self._announce_active(call_path)
        elif new_state == _STATE_TERMINATED:
            self._delete_call(call_path)
            self._drop_call(call_path, terminated=True)
            return
        elif (new_state == _STATE_RINGING_IN
                and call.direction == _DIR_INCOMING):
            # A waiting call that the network promotes to ringing never
            # passed through the initial fetch announcement: screen it
            # (consult) and ring it like any other incoming call.
            self._announce_incoming(call_path)
        elif (new_state == _STATE_WAITING
                and call.direction == _DIR_INCOMING):
            self._screen_waiting(call_path)
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
        if call_path in self._number_wait:
            self._cancel_number_wait(call_path)
            self._number_waited.add(call_path)
            self._screen_incoming(call_path)
        elif (call.direction == _DIR_INCOMING
                and call.state == _STATE_RINGING_IN
                and call_path in self._announced_incoming):
            self._announced_incoming.discard(call_path)
            self._announce_incoming(call_path)
        elif (call.direction == _DIR_INCOMING
                and call.state == _STATE_WAITING
                and call_path in self._screened_waiting):
            # Screened while withheld; the real number decides now.
            self._screened_waiting.discard(call_path)
            self._screen_waiting(call_path)
        self._emit_changed()

    def _on_interfaces_added(
        self, _c, _sender, _path, _iface, _sig, params, _ud
    ) -> None:
        # Only Modem objects travel the ObjectManager; a re-probed modem
        # re-indexes, so re-attach and adopt whatever calls it already has.
        obj_path, interfaces = params.unpack()
        known = self._modems.get(obj_path)
        if known is None and _MODEM_IFACE not in interfaces:
            return
        # A modem that finishes initialising after a SIM unlock gains
        # Voice/Messaging/Ussd on the SAME path: merge, never replace.
        had_voice = known is not None and _VOICE_IFACE in known
        self._modems[obj_path] = (known or set()) | set(interfaces)
        if _MODEM_IFACE in interfaces:
            self._update_modem_props(
                obj_path, _modem_iface_props(interfaces), replace_all=True)
        if _VOICE_IFACE in interfaces and not had_voice:
            self._list_calls(obj_path)

    def _on_interfaces_removed(
        self, _c, _sender, _path, _iface, _sig, params, _ud
    ) -> None:
        obj_path, removed = params.unpack()
        if _MODEM_IFACE not in removed:
            known = self._modems.get(obj_path)
            if known is not None:
                known.difference_update(removed)
            return
        self._modems.pop(obj_path, None)
        self._forget_modem_state(obj_path)
        for call_path in [p for p, m in self._call_modem.items() if m == obj_path]:
            self._drop_call(call_path)

    # ---------------------------------------------------------- modem state

    def _update_modem_props(self, modem_path: str, props,
                            replace_all: bool = False) -> None:
        try:
            fresh = dict(props or {})
        except (TypeError, ValueError):
            fresh = {}
        if replace_all or modem_path not in self._modem_props:
            self._modem_props[modem_path] = fresh
        else:
            self._modem_props[modem_path].update(fresh)
        self._evaluate_lock(modem_path)

    def _forget_modem_state(self, modem_path: str) -> None:
        """A modem went away: withdraw its SIM prompt if one was up."""
        self._modem_props.pop(modem_path, None)
        seen = self._lock_seen.pop(modem_path, None)
        if seen is not None and seen[0]:
            GLib.idle_add(self._on_sim_lock,
                          SimLockInfo(modem_path=modem_path))

    def _evaluate_lock(self, modem_path: str) -> None:
        """Report the modem's lock state when it changed (locked, lock
        kind or attempts left). A modem that was never locked is not
        reported: no prompt to withdraw."""
        info = sim_lock_info(modem_path, self._modem_props.get(modem_path, {}))
        key = (info.locked, info.unlock_required if info.locked else 0,
               info.retries_left() if info.locked else None,
               info.sim_path if info.locked else '')
        previous = self._lock_seen.get(modem_path)
        if previous == key:
            return
        self._lock_seen[modem_path] = key
        if not info.locked and (previous is None or not previous[0]):
            return
        from shell_log import get_logger
        get_logger('modem_monitor').info(
            'modem %s SIM %s (lock %d)', modem_path,
            'locked' if info.locked else 'unlocked', info.unlock_required)
        GLib.idle_add(self._on_sim_lock, info)

    def _on_modem_state_changed(
        self, _conn, _sender, modem_path, _iface, _sig, params, _ud,
    ) -> None:
        try:
            old, new, _reason = params.unpack()
            old, new = int(old), int(new)
        except (TypeError, ValueError):
            return
        modem_path = str(modem_path)
        if modem_path not in self._modem_props and modem_path not in self._modems:
            return
        self._update_modem_props(modem_path, {'State': new})
        if (new >= _MODEM_STATE_REGISTERED > old
                and _VOICE_IFACE in self._modems.get(modem_path, set())):
            # Registered (again): sweep for calls that arrived while the
            # modem was still coming up (sxmo_hook_modem.sh catch-up).
            self._list_calls(modem_path)

    def _on_modem_properties_changed(
        self, _conn, _sender, modem_path, _iface, _sig, params, _ud,
    ) -> None:
        try:
            unpacked = params.unpack()
            iface_name, changed = unpacked[0], dict(unpacked[1])
        except (TypeError, ValueError, IndexError):
            return
        if iface_name != _MODEM_IFACE:
            return
        modem_path = str(modem_path)
        if modem_path not in self._modems:
            return
        relevant = {key: changed[key] for key in
                    ('State', 'UnlockRequired', 'UnlockRetries', 'Sim')
                    if key in changed}
        if relevant:
            self._update_modem_props(modem_path, relevant)

    def _on_messaging_added(
        self, _conn, _sender, modem_path, _iface, _sig, params, _ud,
    ) -> None:
        unpacked = params.unpack()
        sms_path, received = unpacked[0], unpacked[1]
        if not received or self._on_sms is None:
            return
        self._fetch_sms(str(sms_path), _SMS_RECEIVE_POLLS,
                        str(modem_path) if modem_path else None)

    def _fetch_sms(self, sms_path: str, polls_left: int,
                   modem_path: str | None = None) -> None:
        if self._bus is None:
            return
        self._bus.call(
            _MM1, sms_path, _PROPS_IFACE, 'GetAll',
            GLib.Variant('(s)', (_SMS_IFACE,)), None,
            Gio.DBusCallFlags.NONE, _CALL_TIMEOUT_MS, None,
            lambda conn, result: self._on_sms_props(
                sms_path, polls_left, conn, result, modem_path),
        )

    def _on_sms_props(self, sms_path: str, polls_left: int, conn, result,
                      modem_path: str | None = None) -> None:
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
                lambda: self._fetch_sms(
                    sms_path, polls_left - 1, modem_path) or False)
            return
        number = _strip_tel(str(props.get('Number', '')))
        text = str(props.get('Text', ''))
        if not number or not text:
            return
        key = (number, text, str(props.get('Timestamp', '')))
        GLib.idle_add(self._deliver_sms, sms_path, modem_path, key)

    def _deliver_sms(self, sms_path: str, modem_path: str | None,
                     key: tuple) -> bool:
        """Hand one received SMS to ``on_sms``. When the shell posted it
        itself (the handler returns True: no TxxT or Chatty owns SMS),
        nothing else will ever remove it, so it is deleted from the modem
        store; a copy of a message already posted is only deleted."""
        if key in self._sms_seen:
            self._delete_sms(sms_path, modem_path)
            return False
        number, text = key[0], key[1]
        if self._on_sms(number, text) is True:
            self._sms_seen.append(key)
            del self._sms_seen[:-_SMS_SEEN_MAX]
            self._delete_sms(sms_path, modem_path)
        return False

    def _delete_sms(self, sms_path: str, modem_path: str | None) -> None:
        modem_path = modem_path or self.modem_path(_MSG_IFACE)
        if modem_path is None or self._bus is None:
            return

        def _on_deleted(conn, result) -> None:
            try:
                conn.call_finish(result)
            except Exception as err:  # noqa: BLE001 - logged only
                from shell_log import get_logger
                get_logger('modem_monitor').warning(
                    'SMS delete failed: %s', _err_message(err))

        try:
            self._bus.call(
                _MM1, modem_path, _MSG_IFACE, 'Delete',
                GLib.Variant('(o)', (sms_path,)), None,
                Gio.DBusCallFlags.NONE, _CALL_TIMEOUT_MS, None, _on_deleted,
            )
        except Exception:  # noqa: BLE001 - best effort cleanup
            pass
