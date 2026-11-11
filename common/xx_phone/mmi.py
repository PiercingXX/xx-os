"""MMI/USSD codes: one detector and one async USSD session for every dialer.

Typing ``*100#`` and pressing Call must not place a voice call. Both the
XX-WM shell dialer and the xx-dialer app classify what was typed with
:func:`classify` before dialing:

- ``'imei'``: ``*#06#`` shows the modem's IMEI locally (no network
  traffic), read from ``Modem3gpp.Imei`` or ``Modem.EquipmentIdentifier``
  (:func:`fetch_imei`).
- ``'ussd'``: anything USSD-shaped goes to ModemManager's
  ``Modem3gpp.Ussd.Initiate`` through :class:`UssdSession`. That includes
  supplementary-service MMI codes such as ``**21*number#`` (call
  forwarding): like GNOME Calls, they are not interpreted locally but sent
  to the network via Initiate, which handles them on most carriers.
- ``'call'``: everything else is a voice call (``#31#number`` included).

Detection and session handling are ported from GNOME Calls
(``src/calls-util.c`` ``calls_number_is_ussd``;
``plugins/provider/mm/calls-mm-origin.c`` USSD initiate/respond/cancel and
the raw ``PropertiesChanged`` handling, because the normal property signal
is inconsistent; NetworkRequest/NetworkNotification are deduped), GPL-3.0+.
The ``*#06#`` IMEI shortcut follows plasma-dialer's dialpad (GPL-2.0+).

Every D-Bus call here is async (``bus.call`` with a callback) so nothing
blocks the GTK main loop; the bus and the ``GLib`` module are injected so
the state machine runs under a fake bus in tests. Pure module otherwise:
it never imports gi itself.
"""
from __future__ import annotations

import re
from typing import Callable

MM1 = 'org.freedesktop.ModemManager1'
MM1_PATH = '/org/freedesktop/ModemManager1'
MODEM_IFACE = 'org.freedesktop.ModemManager1.Modem'
MODEM3GPP_IFACE = 'org.freedesktop.ModemManager1.Modem.Modem3gpp'
USSD_IFACE = 'org.freedesktop.ModemManager1.Modem.Modem3gpp.Ussd'
PROPS_IFACE = 'org.freedesktop.DBus.Properties'
OBJMGR_IFACE = 'org.freedesktop.DBus.ObjectManager'

IMEI_CODE = '*#06#'

#: MMModem3gppUssdSessionState
STATE_UNKNOWN = 0
STATE_IDLE = 1
STATE_ACTIVE = 2
STATE_USER_RESPONSE = 3

_ENUM_TIMEOUT_MS = 8_000
_PROP_TIMEOUT_MS = 5_000
#: The network may take a while to answer a USSD request.
_USSD_TIMEOUT_MS = 60_000

_DBUS_ERROR_PREFIX = re.compile(r'^(GDBus\.Error:)?[\w.\-]+\.Error[\w.\-]*:\s*')


# --------------------------------------------------------------- detector

def is_ussd(number: str | None) -> bool:
    """True for USSD/MMI-shaped codes: start with ``*``, ``#``, ``**``,
    ``##`` or ``*#``, then only digits and ``*``, finished by a single
    ``#``. Port of GNOME Calls' ``calls_number_is_ussd``."""
    if not isinstance(number, str) or not number:
        return False
    if number[0] not in '*#':
        return False
    rest = number[1:]
    if rest.startswith('#'):
        rest = rest[1:]
    index = 0
    while index < len(rest) and (rest[index].isdigit() and rest[index].isascii()
                                 or rest[index] == '*'):
        index += 1
    return rest[index:] == '#'


def normalize_code(raw: str | None) -> str:
    """The dial string with whitespace removed (keypads and pasted text
    may carry spaces); nothing else changes."""
    if not isinstance(raw, str):
        return ''
    return re.sub(r'\s+', '', raw)


def classify(raw: str | None) -> str:
    """``'imei'``, ``'ussd'`` or ``'call'`` for what the user typed."""
    code = normalize_code(raw)
    if code == IMEI_CODE:
        return 'imei'
    if is_ussd(code):
        return 'ussd'
    return 'call'


# ---------------------------------------------------------------- helpers

def plain_error(err: object) -> str:
    """A D-Bus error as a plain sentence for the user: the GDBus error
    name prefix is dropped, the message kept."""
    message = getattr(err, 'message', None) or str(err or '')
    message = _DBUS_ERROR_PREFIX.sub('', message.strip())
    message = message.strip() or 'The modem reported an error.'
    return message[0].upper() + message[1:]


def _unpack_first(reply):
    value = reply.unpack()
    if isinstance(value, (tuple, list)) and len(value) == 1:
        return value[0]
    return value


def _unwrap(value):
    """A property value: real GLib unpacks fully, but tolerate a
    ``GLib.Variant`` left over from ``Get`` (``(v)`` replies)."""
    unpack = getattr(value, 'unpack', None)
    if callable(unpack):
        try:
            return unpack()
        except Exception:
            return value
    return value


def pick_modem(objects: dict, interface: str = USSD_IFACE) -> str | None:
    """First modem object path (sorted) exposing ``interface``; None when
    no modem has it. Unlike the voice resolver this never falls back to a
    modem without the interface: Initiate on it would only fail."""
    for path in sorted(objects or {}):
        ifaces = objects[path] or {}
        if MODEM_IFACE in ifaces and interface in ifaces:
            return path
    return None


def imeis_from_objects(objects: dict) -> list[str]:
    """IMEI of every modem in a GetManagedObjects reply: Modem3gpp.Imei,
    else Modem.EquipmentIdentifier. Deduped, modem path order."""
    found: list[str] = []
    for path in sorted(objects or {}):
        ifaces = objects[path] or {}
        if MODEM_IFACE not in ifaces:
            continue
        gpp = ifaces.get(MODEM3GPP_IFACE) or {}
        modem = ifaces.get(MODEM_IFACE) or {}
        value = str(_unwrap(gpp.get('Imei', '')) or '').strip()
        if not value:
            value = str(_unwrap(modem.get('EquipmentIdentifier', '')) or '').strip()
        if value and value not in found:
            found.append(value)
    return found


def imei_text(imeis: list[str]) -> str:
    """Body text for the ``*#06#`` sheet."""
    if not imeis:
        return 'No IMEI available: no modem was found.'
    if len(imeis) == 1:
        return imeis[0]
    return '\n'.join('IMEI %d: %s' % (i + 1, v) for i, v in enumerate(imeis))


def _flags(gio):
    if gio is None:
        return 0
    try:
        return gio.DBusCallFlags.NONE
    except AttributeError:
        return 0


def _signal_flags(gio):
    if gio is None:
        return 0
    try:
        return gio.DBusSignalFlags.NONE
    except AttributeError:
        return 0


def fetch_imei(bus, on_done: Callable[[list[str], str], None],
               gio=None) -> None:
    """Read every modem's IMEI with one async GetManagedObjects;
    ``on_done(imeis, error)`` fires exactly once."""
    if bus is None:
        on_done([], 'No modem found: the system bus is unavailable.')
        return

    def _done(conn, result) -> None:
        try:
            objects = dict(_unpack_first(conn.call_finish(result)))
        except Exception as err:  # noqa: BLE001 - shown as text
            on_done([], plain_error(err))
            return
        imeis = imeis_from_objects(objects)
        on_done(imeis, '' if imeis else 'No IMEI available: no modem was found.')

    try:
        bus.call(MM1, MM1_PATH, OBJMGR_IFACE, 'GetManagedObjects', None, None,
                 _flags(gio), _ENUM_TIMEOUT_MS, None, _done)
    except Exception as err:  # noqa: BLE001
        on_done([], plain_error(err))


# ---------------------------------------------------------------- session

class UssdSession:
    """One USSD conversation with the network, driven asynchronously.

    ``start(code)`` resolves a modem with the Ussd interface (unless one
    was given), cancels any session the modem still has open (GNOME Calls
    does the same: Initiate fails while ACTIVE/USER_RESPONSE), then calls
    Initiate. The reply and every NetworkRequest/NetworkNotification
    property update land in :attr:`messages`, deduped per user action (the
    Initiate/Respond reply and the property update carry the same text).
    :attr:`state` follows the modem's session State; when it is
    USER_RESPONSE the UI offers a reply field (``respond(text)``).
    ``cancel()`` ends the network session; ``close()`` only detaches.
    ``on_change(session)`` fires after every visible change.
    """

    def __init__(self, bus, glib, gio=None, modem_path: str | None = None,
                 on_change: Callable[['UssdSession'], None] | None = None,
                 timeout_ms: int = _USSD_TIMEOUT_MS) -> None:
        self._bus = bus
        self._glib = glib
        self._gio = gio
        self.modem_path = modem_path
        self._on_change = on_change or (lambda _s: None)
        self._timeout_ms = timeout_ms
        self.messages: list[str] = []
        self.state = STATE_UNKNOWN
        self.error = ''
        self.busy = False
        self.closed = False
        self.code = ''
        self._seen: set[str] = set()
        self._sub_id = None

    # -- public -------------------------------------------------------

    @property
    def awaiting_reply(self) -> bool:
        """True when the network asked a question and nothing is in flight."""
        return (not self.closed and not self.busy
                and self.state == STATE_USER_RESPONSE)

    def start(self, code: str) -> None:
        self.code = normalize_code(code)
        if not self.code:
            self._fail('Nothing to send.')
            return
        if self._bus is None:
            self._fail('No modem found: the system bus is unavailable.')
            return
        self._begin_action()
        if self.modem_path:
            self._attach_and_initiate()
            return
        self._call(MM1_PATH, OBJMGR_IFACE, 'GetManagedObjects', None,
                   self._on_objects, _ENUM_TIMEOUT_MS)

    def respond(self, text: str) -> None:
        text = (text or '').strip()
        if self.closed or self.busy or not text:
            return
        self._begin_action()
        self._call(self.modem_path, USSD_IFACE, 'Respond',
                   self._glib.Variant('(s)', (text,)), self._on_reply,
                   self._timeout_ms)

    def cancel(self) -> None:
        """End the network session (when one may be open) and detach."""
        if self.closed:
            return
        if self.modem_path and self._bus is not None and (
                self.busy or self.state in (STATE_ACTIVE, STATE_USER_RESPONSE)):
            self._call(self.modem_path, USSD_IFACE, 'Cancel', None,
                       lambda _c, _r: None, _PROP_TIMEOUT_MS, finish=True)
        self.close()

    def close(self) -> None:
        """Detach from the modem; late replies are ignored."""
        if self.closed:
            return
        self.closed = True
        self.busy = False
        if self._sub_id is not None and self._bus is not None:
            try:
                self._bus.signal_unsubscribe(self._sub_id)
            except Exception:
                pass
        self._sub_id = None

    # -- internals ----------------------------------------------------

    def _begin_action(self) -> None:
        self.busy = True
        self.error = ''
        self._seen = set()
        self._changed()

    def _changed(self) -> None:
        try:
            self._on_change(self)
        except Exception:
            pass

    def _fail(self, text: str) -> None:
        self.busy = False
        self.error = text
        self._changed()

    def _add_message(self, text) -> bool:
        text = str(_unwrap(text) or '').strip()
        if not text or text in self._seen:
            return False
        self._seen.add(text)
        self.messages.append(text)
        return True

    def _call(self, path, iface, method, params, callback, timeout,
              finish: bool = False) -> None:
        def _done(conn, result) -> None:
            if finish:
                try:
                    conn.call_finish(result)
                except Exception:
                    pass
                return
            callback(conn, result)

        try:
            self._bus.call(MM1, path, iface, method, params, None,
                           _flags(self._gio), timeout, None, _done)
        except Exception as err:  # noqa: BLE001 - dispatch failed
            if not finish:
                self._fail(plain_error(err))

    def _on_objects(self, conn, result) -> None:
        if self.closed:
            return
        try:
            objects = dict(_unpack_first(conn.call_finish(result)))
        except Exception as err:  # noqa: BLE001
            self._fail(plain_error(err))
            return
        path = pick_modem(objects)
        if path is None:
            has_modem = any(MODEM_IFACE in (i or {}) for i in objects.values())
            self._fail('This modem cannot send USSD codes right now.'
                       if has_modem else 'No modem found.')
            return
        self.modem_path = path
        self._attach_and_initiate()

    def _attach_and_initiate(self) -> None:
        if self._sub_id is None:
            try:
                self._sub_id = self._bus.signal_subscribe(
                    MM1, PROPS_IFACE, 'PropertiesChanged', self.modem_path,
                    USSD_IFACE, _signal_flags(self._gio),
                    self._on_properties_changed, None)
            except Exception:
                self._sub_id = None
        self._call(self.modem_path, PROPS_IFACE, 'Get',
                   self._glib.Variant('(ss)', (USSD_IFACE, 'State')),
                   self._on_initial_state, _PROP_TIMEOUT_MS)

    def _on_initial_state(self, conn, result) -> None:
        if self.closed:
            return
        try:
            state = int(_unwrap(_unpack_first(conn.call_finish(result))))
        except Exception:
            state = STATE_UNKNOWN
        if state in (STATE_ACTIVE, STATE_USER_RESPONSE):
            # A stale session (ours or another client's) blocks Initiate.
            self._call(self.modem_path, USSD_IFACE, 'Cancel', None,
                       self._on_stale_cancelled, _PROP_TIMEOUT_MS)
            return
        self._initiate()

    def _on_stale_cancelled(self, conn, result) -> None:
        if self.closed:
            return
        try:
            conn.call_finish(result)
        except Exception:
            pass  # Initiate reports the real problem if any
        self._initiate()

    def _initiate(self) -> None:
        self.state = STATE_ACTIVE
        self._call(self.modem_path, USSD_IFACE, 'Initiate',
                   self._glib.Variant('(s)', (self.code,)), self._on_reply,
                   self._timeout_ms)

    def _on_reply(self, conn, result) -> None:
        if self.closed:
            return
        try:
            reply = _unpack_first(conn.call_finish(result))
        except Exception as err:  # noqa: BLE001
            self.state = STATE_IDLE
            self._fail(plain_error(err))
            return
        self.busy = False
        self._add_message(reply)
        self._changed()
        # The State update may trail the reply (plasma-dialer notes the
        # order is undefined): read it now so the reply field shows.
        self._call(self.modem_path, PROPS_IFACE, 'Get',
                   self._glib.Variant('(ss)', (USSD_IFACE, 'State')),
                   self._on_state_read, _PROP_TIMEOUT_MS)

    def _on_state_read(self, conn, result) -> None:
        if self.closed:
            return
        try:
            state = int(_unwrap(_unpack_first(conn.call_finish(result))))
        except Exception:
            return
        if state != self.state:
            self.state = state
            self._changed()

    def _on_network_request(self, conn, result) -> None:
        if self.closed:
            return
        try:
            text = _unpack_first(conn.call_finish(result))
        except Exception:
            return
        if self._add_message(text):
            self._changed()

    def _on_properties_changed(self, _conn, _sender, _path, _iface, _sig,
                               params, _ud) -> None:
        if self.closed:
            return
        try:
            values = params.unpack()
            iface, changed = values[0], dict(values[1])
        except Exception:
            return
        if iface != USSD_IFACE:
            return
        dirty = False
        if 'State' in changed:
            try:
                state = int(_unwrap(changed['State']))
            except (TypeError, ValueError):
                state = self.state
            if state != self.state:
                self.state = state
                dirty = True
        if 'NetworkRequest' in changed:
            dirty = self._add_message(changed['NetworkRequest']) or dirty
        elif self.state == STATE_USER_RESPONSE and 'State' in changed:
            # NetworkRequest is not reliably signalled (GNOME Calls' XXX):
            # read it when the session starts waiting for the user.
            self._call(self.modem_path, PROPS_IFACE, 'Get',
                       self._glib.Variant('(ss)', (USSD_IFACE, 'NetworkRequest')),
                       self._on_network_request, _PROP_TIMEOUT_MS)
        if (self.state != STATE_USER_RESPONSE
                and 'NetworkNotification' in changed):
            dirty = self._add_message(changed['NetworkNotification']) or dirty
        if dirty:
            self._changed()


def state_text(session: UssdSession) -> str:
    """One status line for the sheet under the message."""
    if session.error:
        return ''
    if session.busy:
        return 'Waiting for the network…'
    if session.state == STATE_USER_RESPONSE:
        return 'The network is waiting for your reply.'
    if session.messages:
        return 'Session ended.' if session.state == STATE_IDLE else ''
    return ''
