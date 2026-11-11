"""org.freedesktop.ScreenSaver on the session bus.

phoc forwards every Wayland zwp_idle_inhibit_v1 (a video player, a
browser playing fullscreen video) to whoever owns this name
(phoc idle-inhibit.c), and Flatpak/X11-era apps call it directly. Before
the shell owned it nobody answered, so video blanked after
``display_blank_timeout``. Each held cookie keeps the shell's named idle
inhibitor ``screensaver`` (idle_inhibit.IdleInhibitHub ->
DisplayManager.inhibit_idle) up; cookies die with their sender's unique
bus name, so a crashed player never pins the screen on.

Exported at both object paths GNOME and KDE use (``/org/freedesktop/
ScreenSaver`` and ``/ScreenSaver``):

- ``Inhibit(s app, s reason) -> u cookie``
- ``UnInhibit(u cookie)`` (only the sender that took the cookie)
- ``GetActive() -> b`` (true while the display is blanked)
- ``GetActiveTime() -> u`` (seconds blanked, 0 while lit)
- ``SimulateUserActivity()`` (wakes a blank display / resets idle)
- signal ``ActiveChanged(b)``

The name is requested without queueing or replacement: under a host
session that already owns it (GNOME, Plasma, dev runs over Phosh) the
shell logs once and leaves it alone. Everything is asynchronous.
"""
from __future__ import annotations

import time
from typing import Callable, NamedTuple

from shell_log import get_logger

_log = get_logger('screensaver')

BUS_NAME = 'org.freedesktop.ScreenSaver'
INTERFACE = 'org.freedesktop.ScreenSaver'
OBJECT_PATHS = ('/org/freedesktop/ScreenSaver', '/ScreenSaver')

INTROSPECTION_XML = """\
<node>
  <interface name="org.freedesktop.ScreenSaver">
    <method name="Inhibit">
      <arg type="s" name="application_name" direction="in"/>
      <arg type="s" name="reason_for_inhibit" direction="in"/>
      <arg type="u" name="cookie" direction="out"/>
    </method>
    <method name="UnInhibit">
      <arg type="u" name="cookie" direction="in"/>
    </method>
    <method name="GetActive">
      <arg type="b" name="active" direction="out"/>
    </method>
    <method name="GetActiveTime">
      <arg type="u" name="seconds" direction="out"/>
    </method>
    <method name="SimulateUserActivity"/>
    <signal name="ActiveChanged">
      <arg type="b" name="active"/>
    </signal>
  </interface>
</node>
"""


class Inhibitor(NamedTuple):
    cookie: int
    sender: str
    app: str
    reason: str


class InhibitorTable:
    """Cookie bookkeeping, widget- and bus-free (tests drive it directly)."""

    def __init__(self) -> None:
        self._by_cookie: dict[int, Inhibitor] = {}
        self._next = 1

    def add(self, sender: str, app: str, reason: str) -> int:
        # Cookies are nonzero u32s, never reused while live.
        while True:
            cookie = self._next
            self._next = self._next + 1 if self._next < 0xFFFFFFFF else 1
            if cookie not in self._by_cookie:
                break
        self._by_cookie[cookie] = Inhibitor(
            cookie, str(sender or ''), str(app or ''), str(reason or ''))
        return cookie

    def remove(self, cookie: int, sender: str) -> bool:
        """Drop one cookie; only its own sender may (False otherwise)."""
        entry = self._by_cookie.get(int(cookie))
        if entry is None or entry.sender != str(sender or ''):
            return False
        del self._by_cookie[entry.cookie]
        return True

    def drop_sender(self, sender: str) -> list[Inhibitor]:
        """Forget every cookie a vanished bus name held."""
        gone = [e for e in self._by_cookie.values() if e.sender == sender]
        for entry in gone:
            del self._by_cookie[entry.cookie]
        return gone

    def has_sender(self, sender: str) -> bool:
        return any(e.sender == sender for e in self._by_cookie.values())

    @property
    def active(self) -> bool:
        return bool(self._by_cookie)

    def entries(self) -> list[Inhibitor]:
        return sorted(self._by_cookie.values())


class ScreenSaverService:
    """Owns org.freedesktop.ScreenSaver and mirrors cookies onto
    ``on_inhibit_changed(bool)``.

    ``is_blanked()`` answers GetActive; ``on_user_activity()`` serves
    SimulateUserActivity. ``bus`` may be injected (tests pass a fake with
    register_object/emit_signal); otherwise start() fetches the session
    bus asynchronously.
    """

    def __init__(self, on_inhibit_changed: Callable[[bool], None],
                 is_blanked: Callable[[], bool] | None = None,
                 on_user_activity: Callable[[], None] | None = None,
                 clock: Callable[[], float] | None = None) -> None:
        self.table = InhibitorTable()
        self._on_inhibit_changed = on_inhibit_changed
        self._is_blanked = is_blanked or (lambda: False)
        self._on_user_activity = on_user_activity or (lambda: None)
        self._clock = clock or time.monotonic
        self._bus = None
        self._reg_ids: list[int] = []
        self._name_id = 0
        self._watches: dict[str, int] = {}
        self._owns_name = False
        self._active = False
        self._active_since: float | None = None
        self._was_inhibited = False

    @property
    def owns_name(self) -> bool:
        return self._owns_name

    # -- lifecycle ------------------------------------------------------

    def start(self, bus: object | None = None) -> None:
        if bus is not None:
            self._on_bus(bus)
            return
        try:
            from gi.repository import Gio
            Gio.bus_get(Gio.BusType.SESSION, None, self._on_bus_ready)
        except Exception as exc:
            _log.warning('screensaver: no session bus: %s', exc)

    def _on_bus_ready(self, _source, result) -> None:
        try:
            from gi.repository import Gio
            bus = Gio.bus_get_finish(result)
        except Exception as exc:
            _log.warning('screensaver: no session bus: %s', exc)
            return
        self._on_bus(bus)

    def _on_bus(self, bus: object) -> None:
        self._bus = bus
        try:
            from gi.repository import Gio
            iface = Gio.DBusNodeInfo.new_for_xml(
                INTROSPECTION_XML).lookup_interface(INTERFACE)
            for path in OBJECT_PATHS:
                self._reg_ids.append(bus.register_object(
                    path, iface, self._on_method_call, None, None))
            # No REPLACE, no ALLOW_REPLACEMENT, no queue: a host session's
            # screensaver keeps its name and we simply stay out.
            self._name_id = Gio.bus_own_name_on_connection(
                bus, BUS_NAME, Gio.BusNameOwnerFlags.DO_NOT_QUEUE,
                self._on_name_acquired, self._on_name_lost)
        except Exception as exc:
            _log.warning('screensaver: export failed: %s', exc)
            self._unregister()

    def _on_name_acquired(self, _conn, name: str) -> None:
        self._owns_name = True
        _log.info('acquired %s (idle inhibit for apps and phoc)', name)

    def _on_name_lost(self, _conn, name: str) -> None:
        was_owner = self._owns_name
        self._owns_name = False
        if was_owner:
            _log.warning('lost %s; idle inhibitors dropped', name)
        else:
            _log.info('%s already owned (host session) — not taking it',
                      name)
        self._unregister()
        for sender in list(self._watches):
            self._unwatch(sender)
        self.table = InhibitorTable()
        self._sync_inhibit()

    def _unregister(self) -> None:
        bus = self._bus
        ids, self._reg_ids = self._reg_ids, []
        for reg_id in ids:
            try:
                bus.unregister_object(reg_id)
            except Exception:
                pass

    def stop(self) -> None:
        if self._name_id:
            try:
                from gi.repository import Gio
                Gio.bus_unown_name(self._name_id)
            except Exception:
                pass
            self._name_id = 0
        self._unregister()

    # -- D-Bus ------------------------------------------------------------

    def _on_method_call(self, _conn, sender, _path, _iface, method,
                        params, invocation) -> None:
        try:
            from gi.repository import GLib
            if method == 'Inhibit':
                app, reason = params.unpack()
                cookie = self.inhibit(sender, app, reason)
                invocation.return_value(GLib.Variant('(u)', (cookie,)))
            elif method == 'UnInhibit':
                (cookie,) = params.unpack()
                self.uninhibit(sender, cookie)
                invocation.return_value(None)
            elif method == 'GetActive':
                invocation.return_value(
                    GLib.Variant('(b)', (self.get_active(),)))
            elif method == 'GetActiveTime':
                invocation.return_value(
                    GLib.Variant('(u)', (self.get_active_time(),)))
            elif method == 'SimulateUserActivity':
                self.simulate_user_activity()
                invocation.return_value(None)
            else:
                invocation.return_dbus_error(
                    'org.freedesktop.DBus.Error.UnknownMethod', method)
        except Exception as exc:
            _log.warning('screensaver %s failed: %s', method, exc)
            try:
                invocation.return_dbus_error(
                    'org.freedesktop.DBus.Error.Failed', str(exc))
            except Exception:
                pass

    # -- behaviour (callable without a bus) --------------------------------

    def inhibit(self, sender: str, app: str, reason: str) -> int:
        cookie = self.table.add(sender, app, reason)
        _log.info('inhibit #%d by %s (%s): %s', cookie, app or '?',
                  sender, reason or '-')
        self._watch(sender)
        self._sync_inhibit()
        return cookie

    def uninhibit(self, sender: str, cookie: int) -> bool:
        removed = self.table.remove(cookie, sender)
        if removed:
            _log.info('uninhibit #%d by %s', cookie, sender)
            if not self.table.has_sender(sender):
                self._unwatch(sender)
            self._sync_inhibit()
        return removed

    def sender_vanished(self, sender: str) -> None:
        gone = self.table.drop_sender(sender)
        self._unwatch(sender)
        if gone:
            _log.info('%s vanished: dropped %d inhibitor(s)', sender,
                      len(gone))
            self._sync_inhibit()

    def get_active(self) -> bool:
        try:
            return bool(self._is_blanked())
        except Exception:
            return False

    def get_active_time(self) -> int:
        if not self.get_active() or self._active_since is None:
            return 0
        return max(0, int(self._clock() - self._active_since))

    def simulate_user_activity(self) -> None:
        try:
            self._on_user_activity()
        except Exception as exc:
            _log.warning('SimulateUserActivity failed: %s', exc)

    def set_active(self, active: bool) -> None:
        """Blank-state fan-out (main.py): emits ActiveChanged on change."""
        active = bool(active)
        if active == self._active:
            return
        self._active = active
        self._active_since = self._clock() if active else None
        if not self._owns_name or self._bus is None:
            return
        try:
            from gi.repository import GLib
            for path in OBJECT_PATHS:
                self._bus.emit_signal(None, path, INTERFACE, 'ActiveChanged',
                                      GLib.Variant('(b)', (active,)))
        except Exception as exc:
            _log.warning('ActiveChanged emit failed: %s', exc)

    # -- internals ------------------------------------------------------------

    def _sync_inhibit(self) -> None:
        now = self.table.active
        if now == self._was_inhibited:
            return
        self._was_inhibited = now
        try:
            self._on_inhibit_changed(now)
        except Exception as exc:
            _log.warning('idle inhibit update failed: %s', exc)

    def _watch(self, sender: str) -> None:
        if not sender or sender in self._watches or self._bus is None:
            return
        try:
            from gi.repository import Gio
            self._watches[sender] = Gio.bus_watch_name_on_connection(
                self._bus, sender, Gio.BusNameWatcherFlags.NONE, None,
                lambda _c, name: self.sender_vanished(name))
        except Exception as exc:
            _log.warning('cannot watch %s: %s', sender, exc)

    def _unwatch(self, sender: str) -> None:
        watch_id = self._watches.pop(sender, 0)
        if not watch_id:
            return
        try:
            from gi.repository import Gio
            Gio.bus_unwatch_name(watch_id)
        except Exception:
            pass
