"""ModemManager SMS + mmsd-tng MMS clients (worker-thread only).

Ports the Android send/receive pipeline (``service/SendPipeline``,
``service/SmsReceiver``, ``service/RebootReconcile``) onto D-Bus:

- ModemManager ``org.freedesktop.ModemManager1.Modem.Messaging`` on the
  **system** bus: ``Create`` + ``Sms.Send`` for outbound, the
  ``Added(o path, b received)`` signal plus ``List``/``GetAll`` for
  inbound, ``Delete`` after the row is committed to SQLite. The modem
  path is resolved from the ObjectManager on every operation (modems
  re-index after resets; the index is never hardcoded).
- mmsd-tng (``org.ofono.mms``) on the **session** bus (review note 1)
  for MMS, behind :class:`MmsAdapter`.

Threading contract (XXWM_RULES.md): nothing here touches GTK. The
``*_sync`` helpers block and run on worker threads only; the
``*_async`` starters use ``Gio.DBusConnection.call`` (2-arg callbacks,
never a trailing ``user_data``) so they are safe to arm from the main
loop. Signal handlers keep the exact 7-arg ``signal_subscribe`` shape
and are subscribed with an explicit trailing ``None``.

Every reply is unpacked as a tuple of out-args (``reply.unpack()[0]``),
never as a bare value. ``Create`` only ever sends ``number`` + ``text``
— delivery/read reports are never requested (``SendPolicy`` parity).

mmsd-tng shapes (session bus ``org.ofono.mms``) follow the documented
mmsd-tng API (``doc/service-api.txt``, ``doc/message-api.txt``,
``src/service.c``): ``Service.GetMessages`` -> ``a(oa{sv})``,
``Service.MessageAdded(o path, a{sv} properties)`` on the per-modem
service object (default ``/org/ofono/mms/modemmanager``),
``Service.SendMessage(as recipients, v smil, a(sss) attachments)``
-> ``o`` with staged text/image files, ``Message`` properties via
``GetAll`` (``Attachments`` are ``(id, type, file, offset, length)``)
plus ``Delete``. Pinned by tests against a fake bus with those shapes;
on-device proof against the real mmsd-tng must confirm the per-modem
service path.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

MM_BUS_NAME = "org.freedesktop.ModemManager1"
MM_ROOT_PATH = "/org/freedesktop/ModemManager1"
OBJMGR_IFACE = "org.freedesktop.DBus.ObjectManager"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
MODEM_IFACE = "org.freedesktop.ModemManager1.Modem"
MESSAGING_IFACE = "org.freedesktop.ModemManager1.Modem.Messaging"
SMS_IFACE = "org.freedesktop.ModemManager1.Sms"

MMS_BUS_NAME = "org.ofono.mms"
MMS_ROOT_PATH = "/"
#: mmsd-tng service object carrying ``org.ofono.mms.Service``. Observed
#: in the wild as ``/org/ofono/mms/modemmanager`` (chatty/mmsd bring-up
#: posts) and as ``/org/ofono/mms/<imsi>`` (ofono message-app posts);
#: the per-modem suffix varies, so callers may override it.
MMS_SERVICE_PATH = "/org/ofono/mms/modemmanager"
MMS_SERVICE_IFACE = "org.ofono.mms.Service"
MMS_MESSAGE_IFACE = "org.ofono.mms.Message"

#: ``Sms.State`` values (MM1 Sms interface).
STATE_RECEIVING = 2
STATE_RECEIVED = 3

#: ``Sms.PduType`` deliver type: a modem-store row carrying this came in.
PDU_TYPE_DELIVER = 1
#: PduTypes a modem-store row carries when it went out (submit + reports).
PDU_TYPES_OUTGOING = frozenset({2, 3, 4, 5})

ENUM_TIMEOUT_MS = 8_000
IO_TIMEOUT_MS = 15_000

#: Polls before an ``Added`` announcement with a stuck RECEIVING state
#: gives up (the row stays in the modem store for the next reconcile).
MAX_RECEIVE_POLLS = 10

#: A multipart SMS still RECEIVING a week after its timestamp will never
#: get its missing parts, and stuck rows fill the modem/SIM store until
#: new SMS silently stop arriving (Chatty ``RECIEVE_TIMEOUT_SECONDS``,
#: chatty-mm-account.c). The startup reconcile imports what text it has
#: and deletes it after this long.
RECEIVING_TIMEOUT_S = 7 * 24 * 60 * 60
#: The same limit for a row with no usable timestamp, counted from when
#: txxt first saw it (Chatty's 3-day ``..._UNKNOWN_RECEIVE_TIME``).
RECEIVING_TIMEOUT_UNKNOWN_S = 3 * 24 * 60 * 60
#: First re-poll delay; doubles per attempt up to :data:`MAX_POLL_DELAY_S`.
POLL_BASE_DELAY_S = 1.0
MAX_POLL_DELAY_S = 8.0


def timestamp_seconds(stamp: str) -> float | None:
    """MM ``Timestamp`` (ISO-8601) as epoch seconds, None when unusable."""
    text = (stamp or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def receiving_expired(stamp: str, now_s: float, first_seen_s: float) -> bool:
    """Whether a row stuck in RECEIVING should be given up on.

    Port of Chatty's ``mm_account_check_state``: a week after the SMS
    timestamp, or three days after txxt first saw a row without one. A
    timestamp later than now means this clock is wrong (a dead RTC reads
    1970), so the week counts from first sight instead of never ending.
    """
    sent = timestamp_seconds(stamp)
    if sent is None:
        return now_s - first_seen_s >= RECEIVING_TIMEOUT_UNKNOWN_S
    if now_s >= sent + RECEIVING_TIMEOUT_S:
        return True
    if now_s > sent:
        return False
    return now_s - first_seen_s >= RECEIVING_TIMEOUT_S


def receive_backoff(attempt: int) -> float:
    """Delay before re-poll ``attempt`` (0-based) while State is RECEIVING."""
    return min(POLL_BASE_DELAY_S * (2.0 ** attempt), MAX_POLL_DELAY_S)


def strip_tel(number: str) -> str:
    """MM1 Number properties arrive as ``tel:`` URIs; the store keeps digits."""
    text = (number or "").strip()
    return text.removeprefix("tel:").strip() or text


def direction_from_pdu(pdu_type: int) -> str:
    """Store direction for a modem-store row: deliver (or unknown) reads
    inbound, submit/report rows read outbound."""
    try:
        pdu = int(pdu_type)
    except (TypeError, ValueError):
        return "INCOMING"
    return "OUTGOING" if pdu in PDU_TYPES_OUTGOING else "INCOMING"


def pick_modem_path(objects: dict, interface: str = MESSAGING_IFACE) -> str | None:
    """First modem object path carrying ``interface`` (sorted for
    determinism), else the first modem of any kind, else None.

    Pure port of the shell ``modem_monitor._pick_modem_path``: modems
    re-index after resets, so callers resolve per operation.
    """
    fallback: str | None = None
    for path in sorted(objects):
        ifaces = objects[path]
        if MODEM_IFACE not in ifaces:
            continue
        if fallback is None:
            fallback = path
        if interface in ifaces:
            return path
    return fallback


def get_managed_objects(bus, timeout_ms: int = ENUM_TIMEOUT_MS) -> dict | None:
    """ObjectManager snapshot, or None on transport failure."""
    try:
        reply = bus.call_sync(
            MM_BUS_NAME, MM_ROOT_PATH, OBJMGR_IFACE, "GetManagedObjects",
            None, None, Gio.DBusCallFlags.NONE, timeout_ms, None,
        )
        return dict(reply.unpack()[0])
    except Exception:
        return None


def resolve_modem_path(
    bus, interface: str = MESSAGING_IFACE, timeout_ms: int = ENUM_TIMEOUT_MS
) -> str | None:
    """Live modem path for ``interface``; None when the bus or modem is gone."""
    if bus is None:
        return None
    objects = get_managed_objects(bus, timeout_ms)
    if not objects:
        return None
    try:
        return pick_modem_path(objects, interface)
    except Exception:
        return None


def system_bus() -> Gio.DBusConnection | None:
    """Worker-thread system-bus connection, or None when unavailable."""
    try:
        return Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    except Exception:
        return None


def session_bus() -> Gio.DBusConnection | None:
    """Session-bus connection (mmsd-tng, notifications); None when gone."""
    try:
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except Exception:
        return None


@dataclass(frozen=True)
class SmsRecord:
    """One modem-store row with its object path."""

    path: str
    number: str
    text: str
    timestamp: str = ""
    pdu_type: int = 0
    state: int = STATE_RECEIVED
    outgoing: bool = False


def record_from_props(path: str, props: dict) -> SmsRecord | None:
    """Build an :class:`SmsRecord` from a ``GetAll`` property map.

    Returns None when the row carries no usable number or text (same skip
    rule as the shell monitor): half-written modem rows never reach the
    archive.
    """
    number = strip_tel(str(props.get("Number", "")))
    text = str(props.get("Text", ""))
    if not number or not text:
        return None
    try:
        pdu = int(props.get("PduType", 0))
    except (TypeError, ValueError):
        pdu = 0
    try:
        state = int(props.get("State", STATE_RECEIVED))
    except (TypeError, ValueError):
        state = STATE_RECEIVED
    return SmsRecord(
        path=str(path),
        number=number,
        text=text,
        timestamp=str(props.get("Timestamp", "")),
        pdu_type=pdu,
        state=state,
        outgoing=direction_from_pdu(pdu) == "OUTGOING",
    )


def create_params(number: str, text: str) -> GLib.Variant:
    """``Create`` in-args built in one step: nesting a prebuilt ``a{sv}``
    Variant inside ``(a{sv})`` raises ``KeyError`` on real PyGObject, so
    the dict assembles inline."""
    return GLib.Variant("(a{sv})", ({
        "number": GLib.Variant("s", number),
        "text": GLib.Variant("s", text),
    },))


def create_sms(
    bus, modem_path: str, number: str, text: str, timeout_ms: int = IO_TIMEOUT_MS
) -> str:
    """``Messaging.Create`` with number+text only; returns the SMS path."""
    reply = bus.call_sync(
        MM_BUS_NAME, modem_path, MESSAGING_IFACE, "Create",
        create_params(number, text), None,
        Gio.DBusCallFlags.NONE, timeout_ms, None,
    )
    return str(reply.unpack()[0])


def send_sms_path(bus, sms_path: str, timeout_ms: int = IO_TIMEOUT_MS) -> None:
    """``Sms.Send`` on an already-created SMS object."""
    bus.call_sync(
        MM_BUS_NAME, sms_path, SMS_IFACE, "Send",
        None, None, Gio.DBusCallFlags.NONE, timeout_ms, None,
    )


def create_and_send(
    bus, modem_path: str, number: str, text: str, timeout_ms: int = IO_TIMEOUT_MS
) -> tuple[bool, str]:
    """Create an SMS and Send it; (True, sms path) or (False, detail)."""
    try:
        sms_path = create_sms(bus, modem_path, number, text, timeout_ms)
        send_sms_path(bus, sms_path, timeout_ms)
    except Exception as err:
        return False, getattr(err, "message", None) or str(err)
    return True, sms_path


def list_sms_paths(
    bus, modem_path: str, timeout_ms: int = ENUM_TIMEOUT_MS
) -> list[str] | None:
    """``Messaging.List`` object paths, or None on transport failure."""
    try:
        reply = bus.call_sync(
            MM_BUS_NAME, modem_path, MESSAGING_IFACE, "List",
            None, None, Gio.DBusCallFlags.NONE, timeout_ms, None,
        )
        return [str(path) for path in reply.unpack()[0]]
    except Exception:
        return None


def get_sms_properties(
    bus, sms_path: str, timeout_ms: int = IO_TIMEOUT_MS
) -> dict | None:
    """``GetAll`` on the Sms interface, or None on transport failure."""
    try:
        reply = bus.call_sync(
            MM_BUS_NAME, sms_path, PROPS_IFACE, "GetAll",
            GLib.Variant("(s)", (SMS_IFACE,)), None,
            Gio.DBusCallFlags.NONE, timeout_ms, None,
        )
        return dict(reply.unpack()[0])
    except Exception:
        return None


def delete_sms(
    bus, modem_path: str, sms_path: str, timeout_ms: int = IO_TIMEOUT_MS
) -> bool:
    """``Messaging.Delete`` one modem-store row; False on failure."""
    try:
        bus.call_sync(
            MM_BUS_NAME, modem_path, MESSAGING_IFACE, "Delete",
            GLib.Variant("(o)", (sms_path,)), None,
            Gio.DBusCallFlags.NONE, timeout_ms, None,
        )
    except Exception:
        return False
    return True


def delete_sms_after_import(
    bus, modem_path: str, sms_path: str, store, message_id: int,
    timeout_ms: int = IO_TIMEOUT_MS,
) -> bool:
    """Crash-safe delete: flag the SQLite row first, delete on the modem,
    clear the flag only on success. A crash between commit and delete
    restarts with the row still flagged; the reconcile match then treats
    the modem copy as already imported and re-deletes it."""
    store.mark_pending_delete(message_id)
    if not delete_sms(bus, modem_path, sms_path, timeout_ms):
        return False
    store.clear_pending_delete(message_id)
    return True


def list_store(
    bus, modem_path: str, timeout_ms: int = ENUM_TIMEOUT_MS
) -> tuple[list[SmsRecord], list[tuple[str, str]]] | None:
    """Full modem store: ``(records, blank_receiving)``.

    ``records`` as :func:`list_records`; ``blank_receiving`` holds
    ``(path, timestamp)`` for RECEIVING rows with no number or text yet,
    which never become a record but still take a store slot (the
    startup reconcile deletes them once they are stuck for good).
    """
    paths = list_sms_paths(bus, modem_path, timeout_ms)
    if paths is None:
        return None
    records = []
    blanks = []
    for sms_path in paths:
        props = get_sms_properties(bus, sms_path, timeout_ms)
        if props is None:
            continue
        record = record_from_props(sms_path, props)
        if record is not None:
            records.append(record)
            continue
        try:
            state = int(props.get("State", STATE_RECEIVED))
        except (TypeError, ValueError):
            state = STATE_RECEIVED
        if state == STATE_RECEIVING:
            blanks.append((str(sms_path), str(props.get("Timestamp", ""))))
    return records, blanks


def list_records(
    bus, modem_path: str, timeout_ms: int = ENUM_TIMEOUT_MS
) -> list[SmsRecord] | None:
    """Full modem store as records (``List`` + per-row ``GetAll``).

    None on transport failure; half-written rows are skipped row by row
    so one corrupt object never fails the whole reconcile.
    """
    listed = list_store(bus, modem_path, timeout_ms)
    return None if listed is None else listed[0]


def wait_received(poll_props, sleep, max_polls: int = MAX_RECEIVE_POLLS) -> dict | None:
    """Poll ``poll_props()`` until State leaves RECEIVING.

    ``Added`` fires on the first part of a multipart message, so the text
    is only whole once State reaches RECEIVED. Returns the first property
    map whose State is not RECEIVING, or None when the polls run out or no
    map ever arrives. ``sleep`` is injected (real sleeps live on worker
    threads only; tests pass a no-op).
    """
    for attempt in range(max_polls + 1):
        try:
            props = poll_props()
        except Exception:
            props = None
        if props is not None:
            try:
                state = int(props.get("State", STATE_RECEIVED))
            except (TypeError, ValueError):
                state = STATE_RECEIVED
            if state != STATE_RECEIVING:
                return props
        if attempt < max_polls:
            sleep(receive_backoff(attempt))
    return None


def parse_added_params(params) -> tuple[str | None, bool]:
    """Unpack an ``Added(o path, b received)`` signal body."""
    try:
        unpacked = params.unpack()
    except Exception:
        return None, False
    if not isinstance(unpacked, (tuple, list)) or len(unpacked) < 2:
        return None, False
    return str(unpacked[0]), bool(unpacked[1])


def send_sms_async(bus, modem_path: str, number: str, text: str, on_done) -> None:
    """Arm ``Create`` + ``Send`` from any thread; ``on_done(ok, detail)``
    fires exactly once on the main loop. Callbacks take 2 args (no
    trailing ``user_data`` per XXWM_RULES.md)."""

    def _on_created(conn, result) -> None:
        try:
            sms_path = str(conn.call_finish(result).unpack()[0])
        except Exception as err:
            on_done(False, getattr(err, "message", None) or str(err))
            return

        def _on_sent(conn2, result2) -> None:
            try:
                conn2.call_finish(result2)
            except Exception as err2:
                on_done(False, getattr(err2, "message", None) or str(err2))
                return
            on_done(True, sms_path)

        try:
            conn.call(
                MM_BUS_NAME, sms_path, SMS_IFACE, "Send",
                None, None, Gio.DBusCallFlags.NONE, IO_TIMEOUT_MS, None, _on_sent,
            )
        except Exception as err:
            on_done(False, getattr(err, "message", None) or str(err))

    try:
        bus.call(
            MM_BUS_NAME, modem_path, MESSAGING_IFACE, "Create",
            create_params(number, text), None,
            Gio.DBusCallFlags.NONE, IO_TIMEOUT_MS, None, _on_created,
        )
    except Exception as err:
        on_done(False, getattr(err, "message", None) or str(err))


def subscribe_messaging_added(bus, handler) -> int | None:
    """Subscribe to ``Messaging.Added``; the handler keeps the exact 7-arg
    ``signal_subscribe`` shape. Returns the subscription id or None."""
    try:
        return bus.signal_subscribe(
            MM_BUS_NAME, MESSAGING_IFACE, "Added", None,
            None, Gio.DBusSignalFlags.NONE, handler, None,
        )
    except Exception:
        return None


def worker_send(number: str, text: str, timeout_ms: int = IO_TIMEOUT_MS) -> tuple[bool, str]:
    """Blocking create+send resolving the modem per call (worker threads)."""
    bus = system_bus()
    if bus is None:
        return False, "system bus unavailable"
    modem = resolve_modem_path(bus)
    if modem is None:
        return False, "no modem found"
    return create_and_send(bus, modem, number, text, timeout_ms)


class MmsAdapter:
    """mmsd-tng client on the session bus, behind one seam.

    Real shapes (session bus ``org.ofono.mms``, per mmsd-tng
    ``doc/service-api.txt``, ``doc/message-api.txt`` and ``src/service.c``):
    ``Service.GetMessages`` -> ``a(oa{sv})`` (path + properties structs);
    ``Service.MessageAdded(o path, a{sv} properties)``;
    ``Service.SendMessage(as recipients, v smil, a(sss) attachments)``
    -> ``o`` where each attachment is ``(content-id, content-type,
    file-path)`` and the SMIL body rides as the middle variant;
    ``Message.GetAll`` -> ``a{sv}`` with documented ``Attachments`` of
    ``(id, content-type, file, offset, length)``; ``Message.Delete``.
    Service object defaults to :data:`MMS_SERVICE_PATH` (per-modem
    variants exist, hence the override). ``bus`` is any object with
    ``call_sync`` returning tuple-``unpack()`` replies plus
    ``signal_subscribe`` — the real Gio session connection in
    production, a fake in tests.
    """

    def __init__(
        self, bus, timeout_ms: int = IO_TIMEOUT_MS,
        service_path: str = MMS_SERVICE_PATH,
    ) -> None:
        self._bus = bus
        self._timeout_ms = timeout_ms
        self._service_path = service_path or MMS_SERVICE_PATH

    def available(self) -> bool:
        """True when the mmsd-tng service answers ``GetMessages``."""
        return self.list_messages() is not None

    def list_messages(self) -> list[str] | None:
        """Message object paths, or None on transport failure.

        Accepts the documented ``a(oa{sv})`` structs (path first) and,
        for older fakes, a bare ``ao`` list.
        """
        try:
            reply = self._bus.call_sync(
                MMS_BUS_NAME, self._service_path, MMS_SERVICE_IFACE, "GetMessages",
                None, None, Gio.DBusCallFlags.NONE, self._timeout_ms, None,
            )
            paths = []
            for entry in reply.unpack()[0] or []:
                if isinstance(entry, (tuple, list)) and entry:
                    paths.append(str(entry[0]))
                else:
                    paths.append(str(entry))
            return paths
        except Exception:
            return None

    def get_message(self, path: str) -> dict | None:
        try:
            reply = self._bus.call_sync(
                MMS_BUS_NAME, path, PROPS_IFACE, "GetAll",
                GLib.Variant("(s)", (MMS_MESSAGE_IFACE,)), None,
                Gio.DBusCallFlags.NONE, self._timeout_ms, None,
            )
            return dict(reply.unpack()[0])
        except Exception:
            return None

    def send_message(
        self, recipients: list[str], smil: str = "",
        attachments: list[tuple[str, str, str]] | None = None,
    ) -> tuple[bool, str]:
        """``Service.SendMessage``; (True, message path) or (False, detail).

        The documented ``(as, v, a(sss))`` shape: recipients, the SMIL
        body as the middle variant, then ``(content-id, content-type,
        file-path)`` triples the caller staged (text file plus images —
        never ``(recipients, text, files)``).
        """
        try:
            staged = [
                (str(name), str(content_type), str(path))
                for name, content_type, path in (attachments or [])
            ]
            payload = GLib.Variant("(asva(sss))", (
                [str(r) for r in recipients],
                GLib.Variant("s", str(smil or "")),
                staged,
            ))
            reply = self._bus.call_sync(
                MMS_BUS_NAME, self._service_path, MMS_SERVICE_IFACE, "SendMessage",
                payload, None,
                Gio.DBusCallFlags.NONE, self._timeout_ms, None,
            )
            return True, str(reply.unpack()[0])
        except Exception as err:
            return False, getattr(err, "message", None) or str(err)

    def mark_read(self, path: str) -> bool:
        try:
            self._bus.call_sync(
                MMS_BUS_NAME, path, MMS_MESSAGE_IFACE, "MarkRead",
                None, None, Gio.DBusCallFlags.NONE, self._timeout_ms, None,
            )
        except Exception:
            return False
        return True

    def delete_message(self, path: str) -> bool:
        try:
            self._bus.call_sync(
                MMS_BUS_NAME, path, MMS_MESSAGE_IFACE, "Delete",
                None, None, Gio.DBusCallFlags.NONE, self._timeout_ms, None,
            )
        except Exception:
            return False
        return True

    def subscribe_added(self, handler) -> int | None:
        """Subscribe to ``Service.MessageAdded`` (7-arg handler shape)."""
        try:
            return self._bus.signal_subscribe(
                MMS_BUS_NAME, MMS_SERVICE_IFACE, "MessageAdded", None,
                None, Gio.DBusSignalFlags.NONE, handler, None,
            )
        except Exception:
            return None


def wait_received_props(
    bus, sms_path: str, sleep=time.sleep,
    max_polls: int = MAX_RECEIVE_POLLS, timeout_ms: int = IO_TIMEOUT_MS,
) -> dict | None:
    """Worker-thread ``GetAll`` polling until State leaves RECEIVING."""
    return wait_received(
        lambda: get_sms_properties(bus, sms_path, timeout_ms),
        sleep, max_polls,
    )
