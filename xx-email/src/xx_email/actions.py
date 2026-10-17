"""Notification-action endpoint for posted mail cards (no GTK).

The poller posts one summary card per account and exits; each card
carries the ``x-xx-wm-action-target`` hint naming this service, so the
shell auto-starts it and calls ``HandleNotificationAction(u id,
s action_key)`` when the user taps Open / Archive / Mark read. Only
ids the poller recorded in the ``notif_actions`` table act —
anything else is a silent no-op, so a replayed or foreign id can
never archive an unrelated thread.

Method handlers never block the main loop: work runs on a worker
thread and replies via ``GLib.idle_add``. The process idle-exits
shortly after starting; every tap re-activates it from the table.
"""

from __future__ import annotations

import threading

from xx_email import db as db_mod
from xx_email import notify as notify_mod

OBJECT_PATH = "/io/piercingxx/XXEmail/Daemon"
INTERFACE = "io.piercingxx.XXEmail.Daemon1"

ERROR_INVALID_ARGUMENT = INTERFACE + ".InvalidArgument"

#: Linger after (re)start: rapid double taps land in one process.
IDLE_EXIT_SECONDS = 30

INTROSPECTION_XML = """\
<node>
  <interface name="io.piercingxx.XXEmail.Daemon1">
    <method name="HandleNotificationAction">
      <arg direction="in" type="u" name="id"/>
      <arg direction="in" type="s" name="action_key"/>
    </method>
  </interface>
</node>
"""


def _default_spawn(fn) -> None:
    thread = threading.Thread(target=fn, daemon=True)
    thread.start()


def _default_idle(fn, *args) -> None:
    from gi.repository import GLib

    GLib.idle_add(fn, *args)


class ActionsService:
    """Serve ``HandleNotificationAction`` from the posted-id table."""

    def __init__(self, db_opener, client_for, opener, handler=None,
                 spawn=None, idle=None) -> None:
        self._db_opener = db_opener
        self._client_for = client_for
        self._opener = opener
        if handler is None:
            from xx_email.daemon import ActionHandler

            handler = ActionHandler()
        self._handler = handler
        self._spawn = spawn or _default_spawn
        self._idle = idle or _default_idle

    def handle_notification_action(self, server_id: object,
                                   action_key: object) -> bool:
        """Act on one tap; False when the id was never posted.

        Unknown ids and unknown keys do nothing. Never raises.
        """
        try:
            notif_id = int(server_id)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
        if notif_id <= 0:
            return False
        key = str(action_key or "")
        if key not in (notify_mod.ACTION_OPEN, "open",
                       notify_mod.ACTION_ARCHIVE,
                       notify_mod.ACTION_MARK_READ):
            return False
        try:
            handle = self._db_opener()
        except Exception:
            return False
        try:
            found = db_mod.posted_notif_for(handle, notif_id)
            if found is None:
                return False
            account, thread_id = found
            if key in (notify_mod.ACTION_OPEN, "open"):
                self._opener(notify_mod.open_thread_uri(account,
                                                        thread_id))
                return True
            client = self._client_for(account)
            try:
                return bool(self._handler.handle(handle, client,
                                                 account, key,
                                                 thread_id))
            finally:
                closer = getattr(client, "close", None)
                if callable(closer):
                    try:
                        closer()
                    except Exception:
                        pass
        except Exception:
            return False
        finally:
            try:
                handle.close()
            except Exception:
                pass

    def handle_method_call(self, connection, sender, object_path,
                           interface_name, method_name, parameters,
                           invocation) -> None:
        """Gio dispatch: unpack plus worker submit only, so the main
        loop never blocks; the worker replies via ``idle_add``."""
        try:
            args = parameters.unpack() if parameters is not None else ()
        except Exception as exc:
            invocation.return_dbus_error(
                ERROR_INVALID_ARGUMENT, "cannot unpack args: %s" % (exc,))
            return
        if method_name != "HandleNotificationAction":
            invocation.return_dbus_error(
                ERROR_INVALID_ARGUMENT,
                "unknown method: %s" % (method_name,))
            return
        try:
            notif_id, action_key = args
            notif_id = int(notif_id)
        except (TypeError, ValueError):
            invocation.return_dbus_error(
                ERROR_INVALID_ARGUMENT,
                "HandleNotificationAction takes (us)")
            return

        def _work() -> None:
            try:
                self.handle_notification_action(notif_id,
                                                str(action_key))
            except Exception:
                pass
            self._idle(self._reply_empty, invocation)

        self._spawn(_work)

    @staticmethod
    def _reply_empty(invocation) -> None:
        from gi.repository import GLib

        invocation.return_value(GLib.Variant("()", ()))

    def register_on(self, connection) -> int:
        from gi.repository import Gio

        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        iface = node.interfaces[0]
        return connection.register_object(OBJECT_PATH, iface,
                                          self.handle_method_call)


def production_wiring():
    """Live seams: app-dirs db, per-account Gmail client, URI opener."""
    from xx_email import daemon as daemon_mod
    from xx_email import setup as setup_mod

    def _db_opener():
        return db_mod.open_db(
            setup_mod.ensure_app_dirs()["data"] / db_mod.DB_FILE_NAME)

    def _opener(uri: str) -> None:
        from gi.repository import Gio

        Gio.AppInfo.launch_default_for_uri(uri, None)

    return (_db_opener, daemon_mod.production_client_for, _opener)


def run() -> int:
    """Own the daemon name, serve one action burst, idle-exit."""
    from gi.repository import Gio, GLib

    from xx_email.daemon import DAEMON_BUS_NAME

    assert DAEMON_BUS_NAME != notify_mod.APP_ID, \
        "actions service must not own the GUI application_id"
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except Exception:
        return 1
    if bus is None:
        return 1
    service = ActionsService(*production_wiring())
    try:
        service.register_on(bus)
    except Exception:
        return 1
    Gio.bus_own_name(Gio.BusType.SESSION, DAEMON_BUS_NAME,
                     Gio.BusNameOwnerFlags.NONE, None, None, None)
    loop = GLib.MainLoop()
    GLib.timeout_add_seconds(IDLE_EXIT_SECONDS, loop.quit)
    try:
        loop.run()
    except Exception:
        pass
    return 0
