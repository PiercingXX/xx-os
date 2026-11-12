"""
In-process org.freedesktop.Notifications DBus service.

The freedesktop notification spec defines Notify as a METHOD call directed
at whichever process owns org.freedesktop.Notifications — not a broadcast
signal. Without a daemon owning that name, applications send their Notify
calls into the void. This daemon owns the name, receives method calls, and
routes them to a caller-supplied callback.

If another daemon (dunst, mako, fnott) is already running and owns the name,
we silently back off. The shade's fallback signal-subscription path remains
active in that case.

Capabilities advertised: body, body-markup, actions, persistence
"""
from __future__ import annotations

import inspect
import re
from typing import Callable

from gi.repository import GLib, Gio

_IFACE_NAME = 'org.freedesktop.Notifications'
_OBJ_PATH = '/org/freedesktop/Notifications'

# Hint carrying a D-Bus action target: "bus_name object_path interface".
# Invoking an action on such a notification emits ActionInvoked as usual
# and additionally calls <interface>.HandleNotificationAction(u id,
# s action_key) on the target with auto-start allowed, so a
# D-Bus-activatable service that already exited can start up and act.
_ACTION_TARGET_HINT = 'x-xx-wm-action-target'
_ACTION_METHOD = 'HandleNotificationAction'
_ACTION_TIMEOUT_MS = 5_000

_BUS_ELEM_RE = re.compile(r'[A-Za-z_-][A-Za-z0-9_-]*')
_UNIQUE_ELEM_RE = re.compile(r'[A-Za-z0-9_-]+')
_PATH_ELEM_RE = re.compile(r'[A-Za-z0-9_]+')


def _valid_bus_name(name: object) -> bool:
    if not isinstance(name, str) or not name or len(name) > 255:
        return False
    if name.startswith(':'):
        elems = name[1:].split('.')
        return bool(elems) and all(
            _UNIQUE_ELEM_RE.fullmatch(e) for e in elems)
    elems = name.split('.')
    return len(elems) >= 2 and all(
        _BUS_ELEM_RE.fullmatch(e) for e in elems)


def _valid_interface_name(name: object) -> bool:
    if not isinstance(name, str) or not name or len(name) > 255:
        return False
    elems = name.split('.')
    return len(elems) >= 2 and all(
        _BUS_ELEM_RE.fullmatch(e) for e in elems)


def _valid_object_path(path: object) -> bool:
    if not isinstance(path, str) or not path.startswith('/') \
            or len(path) > 1024:
        return False
    if path == '/':
        return True
    if '//' in path or path.endswith('/'):
        return False
    return all(_PATH_ELEM_RE.fullmatch(e) for e in path[1:].split('/'))


def parse_action_target(value: object) -> tuple[str, str, str] | None:
    """(bus_name, object_path, interface) from an action-target hint, or
    None when the hint is missing or malformed (never raises)."""
    try:
        if hasattr(value, 'unpack'):
            value = value.unpack()
    except Exception:
        return None
    if not isinstance(value, str):
        return None
    parts = value.split()
    if len(parts) != 3:
        return None
    bus_name, obj_path, iface = parts
    if not _valid_bus_name(bus_name):
        return None
    if not _valid_object_path(obj_path):
        return None
    if not _valid_interface_name(iface):
        return None
    return (bus_name, obj_path, iface)

_INTROSPECTION_XML = """
<node>
  <interface name='org.freedesktop.Notifications'>
    <method name='GetCapabilities'>
      <arg direction='out' name='caps' type='as'/>
    </method>
    <method name='Notify'>
      <arg direction='in'  name='app_name'        type='s'/>
      <arg direction='in'  name='replaces_id'      type='u'/>
      <arg direction='in'  name='app_icon'         type='s'/>
      <arg direction='in'  name='summary'          type='s'/>
      <arg direction='in'  name='body'             type='s'/>
      <arg direction='in'  name='actions'          type='as'/>
      <arg direction='in'  name='hints'            type='a{sv}'/>
      <arg direction='in'  name='expire_timeout'   type='i'/>
      <arg direction='out' name='id'               type='u'/>
    </method>
    <method name='CloseNotification'>
      <arg direction='in' name='id' type='u'/>
    </method>
    <method name='GetServerInformation'>
      <arg direction='out' name='name'         type='s'/>
      <arg direction='out' name='vendor'       type='s'/>
      <arg direction='out' name='version'      type='s'/>
      <arg direction='out' name='spec_version' type='s'/>
    </method>
    <signal name='NotificationClosed'>
      <arg name='id'     type='u'/>
      <arg name='reason' type='u'/>
    </signal>
    <signal name='ActionInvoked'>
      <arg name='id'         type='u'/>
      <arg name='action_key' type='s'/>
    </signal>
  </interface>
</node>
"""

# NotificationClosed reason codes per spec
_REASON_EXPIRED = 1
_REASON_DISMISSED = 2
_REASON_CLOSED = 3

# Notify ids are D-Bus uint32 (1..2^32-1). Shell-internal ids never come
# from this counter: the reserved block plus the SMS/call ranges the
# shell posts directly through the application path.
_MAX_ID = 0xFFFFFFFF
_RESERVED_IDS = frozenset({999901, 999902, 999903, 999904, 999905})
_RESERVED_FLOOR = 1_900_000_000

# Stored per-notification state is bounded: muted notifications are never
# closed, so without a cap the store (and its image-data bytes) grows
# for the whole session.
_MAX_STORED = 128


def _is_reserved(notif_id: int) -> bool:
    return notif_id in _RESERVED_IDS or notif_id >= _RESERVED_FLOOR


# Pixel payloads are never shown (text-first shade). Unpacking them turns
# every byte into a Python int on the main loop: 0.4 s for a 256x256 avatar.
_SKIPPED_HINTS = frozenset({'image-data', 'image_data', 'icon_data'})


def _notify_hints(hints_v) -> dict:
    """a{sv} hints decoded entry by entry, pixel payloads left packed."""
    hints: dict = {}
    for i in range(hints_v.n_children()):
        entry = hints_v.get_child_value(i)
        key = entry.get_child_value(0).get_string()
        if key in _SKIPPED_HINTS:
            continue
        try:
            hints[key] = entry.get_child_value(1).unpack()
        except Exception:
            continue
    return hints


def _notify_args(params) -> tuple:
    """Notify's eight in-args, reading each child on its own."""
    if not hasattr(params, 'get_child_value'):
        return tuple(params.unpack())
    n = params.n_children()
    parts = [params.get_child_value(i) for i in range(n)]
    out = [part.unpack() for part in parts[:6]]
    if n > 6:
        out.append(_notify_hints(parts[6]))
    out.extend(part.unpack() for part in parts[7:])
    return tuple(out)


class NotificationDaemon:
    """
    Owns org.freedesktop.Notifications on the session bus.
    Fires on_notify(id, app_name, summary, body, desktop_entry, hints) for each
    Notify call — hints carries the raw a{sv} dict (category, urgency,
    suppress-sound) so DnD and sound policy can be applied downstream.
    Callbacks accepting a seventh positional argument additionally receive
    the action list as [(key, label), ...] pairs decoded from the flat
    Notify actions array. Fires on_close(id) when the client that was
    issued the id closes it, or when a notification expires.
    """

    def __init__(
        self,
        on_notify: Callable[..., None],
        on_close: Callable[[int], None] | None = None,
    ) -> None:
        self._on_notify = on_notify
        self._on_close = on_close or (lambda _id: None)
        self._bus: Gio.DBusConnection | None = None
        self._reg_id: int = 0
        self._name_id: int = 0
        self._next_id: int = 1
        self._owns_name = False
        self._expire_timers: dict[int, int] = {}
        self._notif_hints: dict[int, dict] = {}
        self._issued_ids: dict[int, None] = {}
        self._takes_actions: bool | None = None
        GLib.idle_add(self._start)

    def _start(self) -> bool:
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            node_info = Gio.DBusNodeInfo.new_for_xml(_INTROSPECTION_XML)
            iface_info = node_info.lookup_interface(_IFACE_NAME)
            self._reg_id = self._bus.register_object(
                _OBJ_PATH,
                iface_info,
                self._on_method_call,
                None,
                None,
            )
            self._name_id = Gio.bus_own_name_on_connection(
                self._bus,
                _IFACE_NAME,
                (Gio.BusNameOwnerFlags.REPLACE
                 | Gio.BusNameOwnerFlags.ALLOW_REPLACEMENT),
                self._on_name_acquired,
                self._on_name_lost,
            )
        except GLib.Error:
            pass
        return False

    def _on_name_acquired(self, _conn: Gio.DBusConnection, name: str) -> None:
        self._owns_name = True
        from shell_log import get_logger
        get_logger('notif_daemon').info('acquired DBus name %s', name)

    def _on_name_lost(self, _conn: Gio.DBusConnection | None, name: str) -> None:
        # Another daemon already owns the name — back off silently.
        self._owns_name = False
        from shell_log import get_logger
        get_logger('notif_daemon').debug('could not own %s — another daemon is running', name)

    def owns_name(self) -> bool:
        """True once this process has acquired org.freedesktop.Notifications.

        Lets the shade treat our daemon as authoritative without a D-Bus
        round trip, even before the first GetNameOwner probe could settle.
        """
        return self._owns_name

    def unique_name(self) -> str | None:
        """This connection's unique name (':1.x'), for owner comparison."""
        if self._bus is None:
            return None
        try:
            return self._bus.get_unique_name()
        except Exception:
            return None

    def _on_method_call(
        self,
        conn: Gio.DBusConnection,
        sender: str,
        _path: str,
        _iface: str,
        method: str,
        params: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        try:
            if method == 'GetCapabilities':
                invocation.return_value(
                    GLib.Variant(
                        '(as)',
                        (['body', 'body-markup', 'actions', 'persistence'],),
                    )
                )
            elif method == 'GetServerInformation':
                invocation.return_value(
                    GLib.Variant('(ssss)', ('PiercingXX', 'PiercingXX', '1.0', '1.2'))
                )
            elif method == 'Notify':
                notif_id = self._handle_notify(params, sender)
                invocation.return_value(GLib.Variant('(u)', (notif_id,)))
            elif method == 'CloseNotification':
                notif_id = int(params.get_child_value(0).get_uint32())
                if self.client_may_close(notif_id, sender):
                    self._close_notification(conn, notif_id, _REASON_CLOSED)
                invocation.return_value(None)
            else:
                invocation.return_error_literal(
                    Gio.dbus_error_quark(),
                    Gio.DBusError.UNKNOWN_METHOD,
                    f'Unknown method: {method}',
                )
        except Exception as exc:
            invocation.return_error_literal(
                Gio.dbus_error_quark(),
                Gio.DBusError.FAILED,
                str(exc),
            )

    def _takes_actions_arg(self) -> bool:
        """Whether on_notify accepts the seventh (actions) argument.

        Probed once via signature so six-argument callbacks keep working.
        """
        if self._takes_actions is None:
            try:
                params = list(
                    inspect.signature(self._on_notify).parameters.values())
                if any(p.kind is inspect.Parameter.VAR_POSITIONAL
                       for p in params):
                    self._takes_actions = True
                else:
                    positional = sum(
                        1 for p in params
                        if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                      inspect.Parameter.POSITIONAL_OR_KEYWORD)
                    )
                    self._takes_actions = positional >= 7
            except (TypeError, ValueError):
                self._takes_actions = False
        return self._takes_actions

    def _issued_ids_dict(self) -> dict:
        """Ids this daemon handed out and not yet closed.

        Lazily created so older test seams that build the daemon without
        __init__ keep working.
        """
        issued = getattr(self, '_issued_ids', None)
        if issued is None:
            issued = self._issued_ids = {}
        return issued

    def _fresh_id(self, issued: dict) -> int:
        """Next free id: within 1..2^32-1, skipping live and reserved ids."""
        candidate = self._next_id
        while True:
            if candidate < 1 or candidate > _MAX_ID:
                candidate = 1
            if candidate not in issued and not _is_reserved(candidate):
                break
            candidate += 1
        self._next_id = candidate + 1
        if self._next_id > _MAX_ID:
            self._next_id = 1
        return candidate

    def client_may_close(self, notif_id: int, sender: str | None) -> bool:
        """Only the client that was issued an id may close it: never an id
        this daemon did not hand out, and never a shell-reserved row."""
        if _is_reserved(notif_id):
            return False
        issued = self._issued_ids_dict()
        return notif_id in issued and issued[notif_id] == sender

    def _handle_notify(self, params: GLib.Variant,
                       sender: str | None = None) -> int:
        parts = _notify_args(params)
        app_name = str(parts[0])
        replaces_id = int(parts[1]) if parts[1] else 0
        summary = str(parts[3])
        body = str(parts[4])
        raw_actions = parts[5] if len(parts) > 5 else []
        hints: dict = parts[6] if len(parts) > 6 else {}
        expire_timeout = int(parts[7]) if len(parts) > 7 else -1

        desktop_entry = str(hints.get('desktop-entry', ''))

        try:
            items = list(raw_actions)
        except TypeError:
            items = []
        actions = [
            (str(items[i]), str(items[i + 1]))
            for i in range(0, len(items) - 1, 2)
        ]

        issued = self._issued_ids_dict()
        if replaces_id and issued.get(replaces_id, False) == sender:
            notif_id = replaces_id
        else:
            # Client input never advances the counter: a forged replaces_id
            # used to push _next_id past 2^32 (every later Notify then
            # failed building its uint32 reply) or into shell-reserved ids.
            notif_id = self._fresh_id(issued)
        issued[notif_id] = sender
        while len(issued) > _MAX_STORED:
            issued.pop(next(iter(issued)))

        # Cancel previous expire timer if replacing
        if notif_id in self._expire_timers:
            GLib.source_remove(self._expire_timers.pop(notif_id))

        clean_hints = hints if isinstance(hints, dict) else {}
        stored = dict(clean_hints)
        stored.pop('image-data', None)
        stored.pop('image_data', None)
        self._notif_hints[notif_id] = stored
        while len(self._notif_hints) > _MAX_STORED:
            self._notif_hints.pop(next(iter(self._notif_hints)))

        if self._takes_actions_arg():
            GLib.idle_add(self._on_notify, notif_id, app_name, summary, body,
                          desktop_entry, clean_hints, actions)
        else:
            GLib.idle_add(self._on_notify, notif_id, app_name, summary, body,
                          desktop_entry, clean_hints)

        if expire_timeout > 0:
            timer_id = GLib.timeout_add(
                expire_timeout,
                lambda nid=notif_id: self._expire_notification(nid),
            )
            self._expire_timers[notif_id] = timer_id

        return notif_id

    def _expire_notification(self, notif_id: int) -> bool:
        # The app hears reason 1 and forgets the id, so the row and its
        # actions go too: a kept row would invoke actions on a dead id.
        self._expire_timers.pop(notif_id, None)
        if not self._bus:
            return False
        self._close_notification(self._bus, notif_id, _REASON_EXPIRED)
        return False

    def _close_notification(
        self, conn: Gio.DBusConnection, notif_id: int, reason: int
    ) -> None:
        timer = self._expire_timers.pop(notif_id, None)
        if timer is not None:
            GLib.source_remove(timer)
        self._notif_hints.pop(notif_id, None)
        self._issued_ids_dict().pop(notif_id, None)
        GLib.idle_add(self._on_close, notif_id)
        try:
            conn.emit_signal(
                None,
                _OBJ_PATH,
                _IFACE_NAME,
                'NotificationClosed',
                GLib.Variant('(uu)', (notif_id, reason)),
            )
        except GLib.Error:
            pass

    def close_from_shell(self, notif_id: int) -> None:
        """Called by the shade when the user dismisses a notification."""
        if self._bus:
            self._close_notification(self._bus, notif_id, _REASON_DISMISSED)

    def close_for_mute(self, notif_id: int) -> None:
        """Called when an app is muted: its live notification goes away
        as if the app itself closed it (NotificationClosed reason 3),
        not as a user dismissal (reason 2)."""
        if self._bus:
            self._close_notification(self._bus, notif_id, _REASON_CLOSED)

    def invoke_action(self, notif_id: int, action_key: str) -> None:
        """Called by the shade when the user taps a notification action."""
        if self._bus:
            try:
                self._bus.emit_signal(
                    None,
                    _OBJ_PATH,
                    _IFACE_NAME,
                    'ActionInvoked',
                    GLib.Variant('(us)', (notif_id, action_key)),
                )
            except GLib.Error:
                pass
        self._forward_action_to_target(notif_id, action_key)

    def _forward_action_to_target(self, notif_id: int,
                                  action_key: str) -> None:
        """Fire-and-forget HandleNotificationAction on the hint target.

        Lets a D-Bus-activatable service that posted actions and already
        exited start back up when the user taps one: the call allows
        auto-start, times out after 5 s, and only logs at DEBUG on
        failure. A missing or malformed hint is silently ignored.
        """
        from shell_log import get_logger
        try:
            target = parse_action_target(
                self._notif_hints.get(notif_id, {}).get(_ACTION_TARGET_HINT))
        except Exception:
            return
        if target is None or self._bus is None:
            return
        bus_name, obj_path, iface = target
        try:
            params = GLib.Variant('(us)', (int(notif_id), str(action_key)))
        except Exception:
            return
        try:
            self._bus.call(
                bus_name, obj_path, iface, _ACTION_METHOD, params, None,
                Gio.DBusCallFlags.NONE, _ACTION_TIMEOUT_MS, None,
                self._on_action_forwarded,
            )
        except Exception as err:  # noqa: BLE001 - fire-and-forget forward:
            # the ActionInvoked signal above already went out.
            get_logger('notif_daemon').debug(
                'action target call failed: %s', err)

    def _on_action_forwarded(self, conn, result) -> None:
        try:
            conn.call_finish(result)
        except Exception as err:  # noqa: BLE001 - fire-and-forget forward
            from shell_log import get_logger
            get_logger('notif_daemon').debug(
                'action target call failed: %s', err)

    def stop(self) -> None:
        if self._name_id:
            Gio.bus_unown_name(self._name_id)
        if self._reg_id and self._bus:
            self._bus.unregister_object(self._reg_id)
