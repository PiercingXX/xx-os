"""Receive prompt over org.freedesktop.Notifications.

Pure helpers (message text, action ids) are Gio-free and covered by
headless tests. The Gio sender uses async calls only; its callback
signatures match real Gio behavior: method replies unpack as a TUPLE of
out-args, and signal callbacks take no trailing user_data.
"""
from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)

NOTIF_IFACE = "org.freedesktop.Notifications"
NOTIF_PATH = "/org/freedesktop/Notifications"

ACTION_ACCEPT = "xx-share-accept"
ACTION_DECLINE = "xx-share-decline"


def prompt_text(
    alias: str, file_names: list[str], total_bytes: int
) -> tuple[str, str]:
    summary = f"Incoming files from {alias}"
    names = ", ".join(file_names[:3])
    if len(file_names) > 3:
        names += f" +{len(file_names) - 3} more"
    body = f"{names} ({format_size(total_bytes)})"
    return summary, body


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def prompt_actions() -> list[str]:
    return [ACTION_ACCEPT, "Accept", ACTION_DECLINE, "Decline"]


def action_for(notification_id: int, action_key: str) -> str | None:
    if action_key in (ACTION_ACCEPT, ACTION_DECLINE):
        return action_key
    return None


class ReceivePrompt:
    """One-shot Accept/Decline prompt; Gio is imported lazily so tests
    and the headless --service path without a session bus can still use
    the pure helpers above."""

    def __init__(
        self,
        summary: str,
        body: str,
        on_action: Callable[[str], None],
    ) -> None:
        self.summary = summary
        self.body = body
        self.on_action = on_action
        self.notification_id = 0
        self._connection = None
        self._signal_id = 0

    def show(self) -> bool:
        try:
            from gi.repository import Gio, GLib
        except ImportError:
            return False
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            return False
        self._connection = bus
        try:
            self._signal_id = bus.signal_subscribe(
                None, NOTIF_IFACE, "ActionInvoked", NOTIF_PATH, None,
                Gio.DBusSignalFlags.NONE, self._on_signal)
        except GLib.Error:
            return False
        parameters = GLib.Variant(
            "(susssasa{sv}i)",
            ("share", 0, "", self.summary, self.body,
             prompt_actions(), {}, 0),
        )
        try:
            bus.call(
                NOTIF_IFACE, NOTIF_PATH, NOTIF_IFACE, "Notify",
                parameters, GLib.VariantType.new("(u)"),
                Gio.DBusCallFlags.NONE, -1, None,
                self._on_reply)
        except GLib.Error:
            return False
        return True

    def close(self) -> None:
        connection, notif_id = self._connection, self.notification_id
        self.notification_id = 0
        self._unsubscribe()
        if connection is None or not notif_id:
            return
        try:
            from gi.repository import Gio, GLib
        except ImportError:
            return
        try:
            connection.call(  # type: ignore[union-attr]
                NOTIF_IFACE, NOTIF_PATH, NOTIF_IFACE, "CloseNotification",
                GLib.Variant("(u)", (int(notif_id),)), None,
                Gio.DBusCallFlags.NONE, -1, None, None)
        except GLib.Error as error:
            logger.warning("close notification failed: %s", error)

    def _unsubscribe(self) -> None:
        connection, signal_id = self._connection, self._signal_id
        self._connection, self._signal_id = None, 0
        if connection is None or not signal_id:
            return
        try:
            connection.signal_unsubscribe(signal_id)  # type: ignore[union-attr]
        except Exception as error:
            logger.warning("unsubscribe notification signal failed: %s", error)

    def _on_reply(self, connection: object, result: object) -> None:
        try:
            reply = connection.call_finish(result)  # type: ignore[union-attr]
            self.notification_id = int(reply.unpack()[0])
        except Exception as error:
            logger.warning("notification reply failed: %s", error)

    def _on_signal(
        self,
        connection: object,
        sender: object,
        path: object,
        iface: object,
        signal: object,
        params: object,
        user_data: object = None,
    ) -> None:
        try:
            notif_id, key = params.unpack()  # type: ignore[union-attr]
        except Exception as error:
            logger.warning("notification signal unpack failed: %s", error)
            return
        if int(notif_id) != self.notification_id:
            return
        action = action_for(int(notif_id), str(key))
        if action is not None:
            self.on_action(action)
