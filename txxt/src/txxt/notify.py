"""Notification client: posture and redaction per Android policy.

Ports ``service/NotificationPolicy`` (sender-name-only content, never
bubbles) onto ``org.freedesktop.Notifications`` (session bus; the shell
daemon or any compliant daemon renders):

- Default body is sender-only: the summary carries the sender display
  name, the body stays empty. A per-contact content preview opts back
  into the message text (lock-screen posture ``content``).
- Every message notification carries a ``Reply`` action. Freedesktop
  notifications have no text entry, so invoking it opens the thread in
  the UI (``daemon.open_thread``) where the reply is composed and sent.
- Cards for a thread are closed (``CloseNotification``) once the thread
  is read, so the shade never keeps messages the user already saw.
- Sound only when the alert style is sound (``sound-name`` hint);
  silent otherwise (``suppress-sound`` hint). Vibrate is a daemon-level
  feedbackd concern, not a notification hint.
"""

from __future__ import annotations

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

NOTIF_BUS_NAME = "org.freedesktop.Notifications"
NOTIF_OBJECT_PATH = "/org/freedesktop/Notifications"
NOTIF_IFACE = "org.freedesktop.Notifications"

ACTION_REPLY = "reply"
ACTION_REPLY_LABEL = "Reply"
ACTION_OPEN = "open"
ACTION_OPEN_LABEL = "Open"

#: Shell hint carrying the action target (``"<bus> <path> <iface>"``).
#: The shell auto-starts that service and calls its
#: ``HandleNotificationAction(u id, s action_key)`` when the user taps
#: an action, so Reply/Open still work after the daemon idle-exits.
ACTION_TARGET_HINT = "x-xx-wm-action-target"


def action_target(bus_name: str, object_path: str, interface: str) -> str:
    """Render one ``x-xx-wm-action-target`` hint value."""
    return f"{bus_name} {object_path} {interface}"

CATEGORY_IM = "im.received"
SOUND_NEW_MESSAGE = "message-new-instant"


def notification_title(sender: str, display_name=lambda s: s) -> str:
    """Visible title: the sender's contact name, else the raw sender.

    Never blank — a resolver returning blank falls back to ``sender``
    (same never-silent rule as the Android policy).
    """
    try:
        resolved = display_name(sender)
    except Exception:
        resolved = sender
    return (resolved or "").strip() or sender


def redacted_body() -> str:
    """Default body: empty — the sender line is the whole notification."""
    return ""


def should_bubble() -> bool:
    """Bubbles/chat-heads stay excluded (product decision F9)."""
    return False


def actions_for_message() -> list[str]:
    """Flat action list: Reply (opens the thread) + Open."""
    return [ACTION_REPLY, ACTION_REPLY_LABEL, ACTION_OPEN, ACTION_OPEN_LABEL]


def hints_for_style(alert_style: str) -> dict:
    """Notification hints for one alert style (silent/vibrate/sound)."""
    if alert_style == "sound":
        return {"category": CATEGORY_IM, "sound-name": SOUND_NEW_MESSAGE}
    return {"category": CATEGORY_IM, "suppress-sound": True}


def notify_send(
    bus,
    summary: str,
    body: str = "",
    actions: list[str] | None = None,
    hints: dict | None = None,
    replace_id: int = 0,
    app_name: str = "TxxT",
    timeout_ms: int = 5_000,
) -> int | None:
    """Blocking ``Notify`` (worker threads only); new notification id or None."""
    hint_variant = {key: _hint_variant(value) for key, value in (hints or {}).items()}
    try:
        reply = bus.call_sync(
            NOTIF_BUS_NAME, NOTIF_OBJECT_PATH, NOTIF_IFACE, "Notify",
            GLib.Variant("(susssasa{sv}i)", (
                app_name, replace_id, "", summary, body,
                actions if actions is not None else [],
                hint_variant, -1,
            )),
            None, Gio.DBusCallFlags.NONE, timeout_ms, None,
        )
        return int(reply.unpack()[0])
    except Exception:
        return None


def close_notification(bus, notification_id: int, timeout_ms: int = 5_000) -> bool:
    """Blocking ``CloseNotification`` (worker threads only). False when the
    server refused or is gone; an id it already dropped is not an error
    per the spec, so a dismissed card closes quietly."""
    try:
        bus.call_sync(
            NOTIF_BUS_NAME, NOTIF_OBJECT_PATH, NOTIF_IFACE, "CloseNotification",
            GLib.Variant("(u)", (int(notification_id),)),
            None, Gio.DBusCallFlags.NONE, timeout_ms, None,
        )
    except Exception:
        return False
    return True


def _hint_variant(value: object) -> GLib.Variant:
    if isinstance(value, bool):
        return GLib.Variant("b", value)
    if isinstance(value, int):
        return GLib.Variant("i", value)
    return GLib.Variant("s", str(value))


def notify_message(
    bus,
    sender: str,
    display_name=lambda s: s,
    preview_body: str | None = None,
    alert_style: str = "sound",
    replace_id: int = 0,
    timeout_ms: int = 5_000,
    action_target: str | None = None,
) -> int | None:
    """Post one inbound-message notification: sender summary, sender-only
    body by default (``preview_body`` only for the content posture), Reply
    + Open actions, style hints.

    ``action_target`` (an :func:`action_target` value) tags the card so
    the shell routes action taps to ``HandleNotificationAction`` even
    when the daemon has idle-exited.
    """
    hints = hints_for_style(alert_style)
    if action_target:
        hints[ACTION_TARGET_HINT] = action_target
    return notify_send(
        bus,
        summary=notification_title(sender, display_name),
        body=preview_body if preview_body is not None else redacted_body(),
        actions=actions_for_message(),
        hints=hints,
        replace_id=replace_id,
        timeout_ms=timeout_ms,
    )


def parse_action_invoked(params) -> tuple[int, str] | None:
    """Unpack ``ActionInvoked(u id, s action_key)`` signal params."""
    try:
        unpacked = params.unpack()
    except Exception:
        return None
    if not isinstance(unpacked, (tuple, list)) or len(unpacked) < 2:
        return None
    try:
        return int(unpacked[0]), str(unpacked[1])
    except (TypeError, ValueError):
        return None


def subscribe_action_invoked(bus, handler) -> int | None:
    """Subscribe to ``ActionInvoked`` (7-arg handler shape)."""
    try:
        return bus.signal_subscribe(
            NOTIF_BUS_NAME, NOTIF_IFACE, "ActionInvoked", None,
            None, Gio.DBusSignalFlags.NONE, handler, None,
        )
    except Exception:
        return None
