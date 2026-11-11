"""ModemManager Voice client on the SYSTEM bus (place/answer/hangup/hold).

The SHELL owns the in-call UI, the ringer and the incoming surface
(``ref/xx-wm/launcher/src/call_ui.py`` + ``modem_monitor.py``); this
module only places calls, drives the voice-call primitives the app's
own surfaces need, and tracks call state. It deliberately owns no
ringer and no incoming surface.

Every operation resolves the modem from the ObjectManager on each use
— modems re-index after resets, so a cached path or a hardcoded index
breaks. The tracked modems and calls follow ModemManager's bus name: a
restart drops them all (an empty snapshot) and re-enumerates. All bus calls follow the shell's async Gio discipline (tuple
replies via ``reply.unpack()[0]``, two-arg callbacks, seven-arg signal
handlers, ``reply_type`` always ``None``).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace

import xx_phone

MM1 = "org.freedesktop.ModemManager1"
MM1_PATH = "/org/freedesktop/ModemManager1"
MODEM_IFACE = "org.freedesktop.ModemManager1.Modem"
VOICE_IFACE = "org.freedesktop.ModemManager1.Modem.Voice"
CALL_IFACE = "org.freedesktop.ModemManager1.Call"
OBJMGR_IFACE = "org.freedesktop.DBus.ObjectManager"
PROPS_IFACE = "org.freedesktop.DBus.Properties"

CALL_STATE_UNKNOWN = 0
CALL_STATE_DIALING = 1
CALL_STATE_RINGING_OUT = 2
CALL_STATE_RINGING_IN = 3
CALL_STATE_ACTIVE = 4
CALL_STATE_HELD = 5
CALL_STATE_WAITING = 6
CALL_STATE_TERMINATED = 7

LIVE_STATES = frozenset({
    CALL_STATE_DIALING, CALL_STATE_RINGING_OUT, CALL_STATE_RINGING_IN,
    CALL_STATE_ACTIVE, CALL_STATE_HELD, CALL_STATE_WAITING,
})

CALL_DIRECTION_UNKNOWN = 0
CALL_DIRECTION_INCOMING = 1
CALL_DIRECTION_OUTGOING = 2

STATE_REASON_UNKNOWN = 0
STATE_REASON_OUTGOING_STARTED = 1
STATE_REASON_INCOMING_NEW = 2
STATE_REASON_ACCEPTED = 3
STATE_REASON_TERMINATED = 4
STATE_REASON_REFUSED_OR_BUSY = 5
STATE_REASON_ERROR = 6
STATE_REASON_AUDIO_SETUP_FAILED = 7
STATE_REASON_TRANSFERRED = 8
STATE_REASON_DEFLECTED = 9

_CALL_TIMEOUT_MS = 5_000
_ENUM_TIMEOUT_MS = 8_000
_IO_TIMEOUT_MS = 15_000


@dataclass
class CallInfo:
    """One tracked call: path identity plus the MM1 Call property codes."""

    path: str
    number: str = ""
    direction: int = CALL_DIRECTION_UNKNOWN
    state: int = CALL_STATE_UNKNOWN
    started_at: float | None = None


def _strip_tel(number: str) -> str:
    return number.removeprefix("tel:").strip() or number


def _err_message(err: Exception) -> str:
    return getattr(err, "message", None) or str(err)


def pick_modem_path(objects: dict, interface: str = VOICE_IFACE) -> str | None:
    """First modem path carrying ``interface``, else the first modem, else None."""
    fallback: str | None = None
    for path in sorted(objects):
        if MODEM_IFACE not in objects[path]:
            continue
        if fallback is None:
            fallback = path
        if interface in objects[path]:
            return path
    return fallback


def resolve_modem_path(interface: str = VOICE_IFACE,
                       timeout_ms: int = _ENUM_TIMEOUT_MS,
                       bus=None) -> str | None:
    """Object path of the first modem carrying ``interface``.

    Blocking: call only from a worker thread, never the GTK main loop.
    """
    if bus is None:
        try:
            from gi.repository import Gio
        except ImportError:
            return None
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except Exception:
            return None
    if bus is None:
        return None
    try:
        from gi.repository import Gio as _gio
    except ImportError:
        _gio = None
    flags = _gio.DBusCallFlags.NONE if _gio is not None else 0
    try:
        objects = bus.call_sync(
            MM1, MM1_PATH, OBJMGR_IFACE, "GetManagedObjects",
            None, None, flags, timeout_ms, None,
        ).unpack()[0]
    except Exception:
        return None
    try:
        return pick_modem_path(dict(objects), interface)
    except Exception:
        return None


def _default_spawn(fn) -> None:
    thread = threading.Thread(target=fn, daemon=True)
    thread.start()


def _default_idle(fn, *args) -> None:
    from gi.repository import GLib

    GLib.idle_add(fn, *args)


class VoiceClient:
    """Async MM1 Voice/Call driver with injected bus and loop seams.

    ``on_calls`` receives a snapshot list on each change; ``on_state``
    fires per ``StateChanged`` as ``(path, old, new, reason)``. Result
    callbacks are exactly-once ``(success, detail)`` tuples posted back
    through ``idle`` so the GTK loop never blocks.
    """

    def __init__(self, on_calls=None, on_state=None, bus=None, gio=None,
                 glib=None, spawn=None, idle=None) -> None:
        self._on_calls = on_calls or (lambda _calls: None)
        self._on_state = on_state or (lambda _p, _o, _n, _r: None)
        self._bus = bus
        self._gio = gio
        self._glib = glib
        self._spawn = spawn or _default_spawn
        self._idle = idle or _default_idle
        self._calls: dict[str, CallInfo] = {}
        self._fetching: set[str] = set()
        self._modems: dict[str, set[str]] = {}
        self._call_modem: dict[str, str] = {}
        self._subs: dict[str, list] = {}
        self._voice_subs: dict[str, list] = {}
        self._mm_watch = None
        self._mm_owner: str | None = None
        self._mm_vanished = False

    # ---- lifecycle ---------------------------------------------------

    def start(self, bus=None, gio=None, glib=None) -> bool:
        if bus is None:
            bus = self._bus
        if gio is None:
            gio = self._gio
        if glib is None:
            glib = self._glib
        if gio is None or glib is None:
            try:
                from gi.repository import Gio as _gio, GLib as _glib
            except ImportError:
                return False
            gio = _gio if gio is None else gio
            glib = _glib if glib is None else glib
        if bus is None:
            try:
                bus = gio.bus_get_sync(gio.BusType.SYSTEM, None)
            except Exception:
                return False
        if bus is None:
            return False
        self._bus = bus
        self._gio = gio
        self._glib = glib
        self._enumerate()
        self._watch_modem_manager()
        return True

    def _watch_modem_manager(self) -> None:
        """Follow ModemManager's system-bus name (Cutie's NameOwnerChanged
        rule): every path the old instance handed out dies with it."""
        if self._mm_watch is not None or self._gio is None:
            return
        try:
            self._mm_watch = self._gio.bus_watch_name(
                self._gio.BusType.SYSTEM, MM1,
                self._gio.BusNameWatcherFlags.NONE,
                self._on_mm_appeared, self._on_mm_vanished)
        except Exception:
            self._mm_watch = None

    def _on_mm_appeared(self, _conn, _name, owner) -> None:
        previous, self._mm_owner = self._mm_owner, owner
        if previous is not None and previous != owner:
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
        """Drop every modem, call and per-path subscription."""
        bus = self._bus
        if bus is not None:
            for sub_ids in self._voice_subs.values():
                for sub_id in sub_ids:
                    try:
                        bus.signal_unsubscribe(sub_id)
                    except Exception:
                        pass
        self._voice_subs = {}
        self._modems = {}
        had_calls = bool(self._calls)
        for call_path in list(self._calls) + list(self._fetching):
            if bus is not None:
                for sub_id in self._subs.pop(call_path, []):
                    try:
                        bus.signal_unsubscribe(sub_id)
                    except Exception:
                        pass
            self._calls.pop(call_path, None)
        self._fetching = set()
        self._call_modem = {}
        if had_calls:
            self._emit_changed()

    def stop(self) -> None:
        if self._mm_watch is not None and self._gio is not None:
            try:
                self._gio.bus_unwatch_name(self._mm_watch)
            except Exception:
                pass
        self._mm_watch = None
        bus = self._bus
        if bus is not None:
            for sub_ids in list(self._subs.values()) \
                    + list(self._voice_subs.values()):
                for sub_id in sub_ids:
                    try:
                        bus.signal_unsubscribe(sub_id)
                    except Exception:
                        pass
        self._subs = {}
        self._voice_subs = {}
        self._calls = {}
        self._fetching = set()

    def calls(self) -> list[CallInfo]:
        return [replace(call) for call in self._calls.values()]

    # ---- placement ----------------------------------------------------

    def place(self, number: str, on_done) -> None:
        """CreateCall + Start on the freshly resolved modem (worker thread)."""
        digits = (number or "").strip()
        if not digits:
            self._idle(on_done, False, "empty number")
            return

        def _work() -> None:
            modem = self._resolve_voice_modem()
            if modem is None:
                self._idle(on_done, False, "no modem found")
                return
            ok, detail = self._create_and_start(modem, digits)
            self._idle(on_done, ok, detail)

        self._spawn(_work)

    def _resolve_voice_modem(self) -> str | None:
        cached = self.modem_path()
        if cached is not None:
            return cached
        return resolve_modem_path(VOICE_IFACE, _ENUM_TIMEOUT_MS,
                                  self._system_bus())

    def _system_bus(self):
        if self._bus is not None:
            return self._bus
        try:
            from gi.repository import Gio as _gio

            return _gio.bus_get_sync(_gio.BusType.SYSTEM, None)
        except Exception:
            return None

    def _create_and_start(self, modem: str,
                          number: str) -> tuple[bool, str]:
        bus = self._system_bus()
        if bus is None:
            return False, "system bus unavailable"
        flags = self._call_flags()
        try:
            props = self._glib.Variant(
                "a{sv}", {"number": self._glib.Variant("s", number)})
            call_path = bus.call_sync(
                MM1, modem, VOICE_IFACE, "CreateCall",
                self._glib.Variant("(a{sv})", (props,)), None,
                flags, _IO_TIMEOUT_MS, None,
            ).unpack()[0]
            bus.call_sync(
                MM1, str(call_path), CALL_IFACE, "Start",
                None, None, flags, _IO_TIMEOUT_MS, None,
            )
        except Exception as err:
            return False, _err_message(err)
        return True, str(call_path)

    # ---- in-call primitives (async, exactly-once callbacks) ------------

    def accept(self, call_path: str, on_done) -> None:
        self._call_method(call_path, "Accept", None, on_done)

    def hangup(self, call_path: str, on_done) -> None:
        self._call_method(call_path, "Hangup", None, on_done)

    def hold(self, call_path: str, on_done) -> None:
        self._call_method(call_path, "Hold", None, on_done)

    def unhold(self, call_path: str, on_done) -> None:
        self._call_method(call_path, "Unhold", None, on_done)

    def send_dtmf(self, call_path: str, digit: str, on_done) -> None:
        # The shared xx_phone rule (also the shell's in-call keypad).
        tone = xx_phone.dtmf_tone(digit)
        if tone is None:
            self._idle(on_done, False, "invalid DTMF digit: %r" % (digit,))
            return
        params = self._glib.Variant("(s)", (tone,))
        self._call_method(call_path, "SendDtmf", params, on_done)

    def hangup_all(self, on_done) -> None:
        """Hang up every live tracked call; reports (True, hung count)."""
        live = [c.path for c in self._calls.values()
                if c.state in LIVE_STATES]
        if not live:
            self._idle(on_done, True, 0)
            return
        pending = list(live)
        failures: list[str] = []

        def _one(path: str) -> None:
            def _done(ok: bool, detail) -> None:
                if not ok:
                    failures.append(str(detail))
                pending.remove(path)
                if not pending:
                    if failures:
                        self._idle(on_done, False, "; ".join(failures))
                    else:
                        self._idle(on_done, True, len(live))
            self.hangup(path, _done)

        for path in live:
            _one(path)

    def hold_and_accept(self, waiting_path: str, on_done) -> None:
        """Hold every active call, then Accept the waiting one.

        MM1 exposes no single hold-and-accept primitive on most modems,
        so this composes the Call.Hold + Call.Accept pair (the swap the
        shell's call-waiting banner offers).
        """
        active = [c.path for c in self._calls.values()
                  if c.state == CALL_STATE_ACTIVE]

        def _accept(_ok: bool, _detail) -> None:
            self.accept(waiting_path, on_done)

        if not active:
            _accept(True, None)
            return
        pending = list(active)

        def _held(path: str, ok: bool, _detail) -> None:
            pending.remove(path)
            if not pending:
                _accept(ok, _detail)

        for path in active:
            self.hold(path, lambda ok, d, p=path: _held(p, ok, d))

    def _call_method(self, call_path: str, method: str, params,
                     on_done) -> None:
        bus = self._bus
        if bus is None:
            self._idle(on_done, False, "system bus unavailable")
            return

        def _on_called(conn, result) -> None:
            try:
                conn.call_finish(result)
            except Exception as err:
                self._idle(on_done, False, _err_message(err))
                return
            self._idle(on_done, True, None)

        try:
            bus.call(
                MM1, call_path, CALL_IFACE, method, params, None,
                self._call_flags(), _CALL_TIMEOUT_MS, None, _on_called,
            )
        except Exception as err:
            self._idle(on_done, False, _err_message(err))

    def _call_flags(self):
        if self._gio is not None:
            return self._gio.DBusCallFlags.NONE
        try:
            from gi.repository import Gio as _gio

            return _gio.DBusCallFlags.NONE
        except ImportError:
            return 0

    # ---- tracking -----------------------------------------------------

    def modem_path(self, interface: str = VOICE_IFACE) -> str | None:
        return pick_modem_path(self._modems, interface)

    def _enumerate(self) -> None:
        if self._bus is None:
            return
        self._bus.call(
            MM1, MM1_PATH, OBJMGR_IFACE, "GetManagedObjects",
            None, None, self._call_flags(), _ENUM_TIMEOUT_MS, None,
            self._on_managed_objects,
        )

    def _on_managed_objects(self, conn, result) -> None:
        try:
            objects = dict(conn.call_finish(result).unpack()[0])
        except Exception:
            return
        for obj_path, interfaces in objects.items():
            if MODEM_IFACE in interfaces:
                self._modems[obj_path] = set(interfaces)
                if VOICE_IFACE in interfaces:
                    self._subscribe_voice(obj_path)
                    self._list_calls(obj_path)

    def _subscribe_voice(self, modem_path: str) -> None:
        if modem_path in self._voice_subs or self._bus is None:
            return
        flags = self._signal_flags()
        added = self._bus.signal_subscribe(
            MM1, VOICE_IFACE, "CallAdded", modem_path,
            None, flags, self._on_call_added, None,
        )
        deleted = self._bus.signal_subscribe(
            MM1, VOICE_IFACE, "CallDeleted", modem_path,
            None, flags, self._on_call_deleted, None,
        )
        self._voice_subs[modem_path] = [added, deleted]

    def _signal_flags(self):
        if self._gio is not None:
            return self._gio.DBusSignalFlags.NONE
        try:
            from gi.repository import Gio as _gio

            return _gio.DBusSignalFlags.NONE
        except ImportError:
            return 0

    def _list_calls(self, modem_path: str) -> None:
        if self._bus is None:
            return
        self._bus.call(
            MM1, modem_path, VOICE_IFACE, "ListCalls",
            None, None, self._call_flags(), _ENUM_TIMEOUT_MS, None,
            lambda conn, result: self._on_list_calls(
                modem_path, conn, result),
        )

    def _on_list_calls(self, modem_path: str, conn, result) -> None:
        try:
            paths = conn.call_finish(result).unpack()[0]
        except Exception:
            return
        for call_path in paths:
            self._adopt_call(str(call_path), modem_path)

    def _adopt_call(self, call_path: str,
                    modem_path: str | None = None) -> None:
        if call_path in self._calls or call_path in self._fetching:
            return
        if self._bus is None:
            return
        self._fetching.add(call_path)
        if modem_path is not None:
            self._call_modem[call_path] = modem_path
        self._bus.call(
            MM1, call_path, PROPS_IFACE, "GetAll",
            self._glib.Variant("(s)", (CALL_IFACE,)), None,
            self._call_flags(), _CALL_TIMEOUT_MS, None,
            lambda conn, result: self._on_call_props(
                call_path, conn, result),
        )

    def _on_call_props(self, call_path: str, conn, result) -> None:
        try:
            props = dict(conn.call_finish(result).unpack()[0])
        except Exception:
            self._fetching.discard(call_path)
            return
        if call_path not in self._fetching and call_path not in self._calls:
            return  # dropped while in flight (deleted, or MM went away)
        self._fetching.discard(call_path)
        try:
            direction = int(props.get("Direction", 0))
            state = int(props.get("State", 0))
        except (TypeError, ValueError):
            direction, state = 0, 0
        if state == CALL_STATE_TERMINATED:
            self._drop_call(call_path)
            return
        call = self._calls.get(call_path)
        if call is None:
            call = CallInfo(path=call_path)
            self._calls[call_path] = call
        call.direction = direction
        call.state = state
        call.number = _strip_tel(str(props.get("Number", "")))
        if state == CALL_STATE_ACTIVE and call.started_at is None:
            call.started_at = time.monotonic()
        self._subscribe_call(call_path)
        self._emit_changed()

    def _subscribe_call(self, call_path: str) -> None:
        if call_path in self._subs or self._bus is None:
            return
        sub = self._bus.signal_subscribe(
            MM1, CALL_IFACE, "StateChanged", call_path,
            None, self._signal_flags(),
            self._on_call_state_changed, None,
        )
        self._subs[call_path] = [sub]

    def _drop_call(self, call_path: str) -> None:
        if self._bus is not None:
            for sub_id in self._subs.pop(call_path, []):
                try:
                    self._bus.signal_unsubscribe(sub_id)
                except Exception:
                    pass
        self._call_modem.pop(call_path, None)
        self._fetching.discard(call_path)
        if self._calls.pop(call_path, None) is None:
            return
        self._emit_changed()

    def _emit_changed(self) -> None:
        self._idle(self._on_calls, self.calls())

    # ---- signals (seven-arg Gio handlers) ------------------------------

    def _on_call_added(self, _conn, _sender, modem_path, _iface, _sig,
                       params, _user_data) -> None:
        unpacked = params.unpack()
        call_path = unpacked[0] \
            if isinstance(unpacked, (tuple, list)) else unpacked
        self._adopt_call(str(call_path), str(modem_path))

    def _on_call_deleted(self, _conn, _sender, _modem_path, _iface, _sig,
                         params, _user_data) -> None:
        unpacked = params.unpack()
        call_path = unpacked[0] \
            if isinstance(unpacked, (tuple, list)) else unpacked
        self._drop_call(str(call_path))

    def _on_call_state_changed(self, _conn, _sender, call_path, _iface,
                               _sig, params, _user_data) -> None:
        old_state, new_state, reason = params.unpack()
        call = self._calls.get(call_path)
        if call is None:
            return
        try:
            new_state = int(new_state)
            old_state = int(old_state)
            reason = int(reason)
        except (TypeError, ValueError):
            return
        call.state = new_state
        if new_state == CALL_STATE_ACTIVE and call.started_at is None:
            call.started_at = time.monotonic()
        try:
            self._idle(self._on_state, call_path, old_state, new_state,
                       reason)
        except Exception:
            pass
        if new_state == CALL_STATE_TERMINATED:
            self._drop_call(call_path)
            return
        self._emit_changed()
