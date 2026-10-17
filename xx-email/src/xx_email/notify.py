"""New-mail notifications for milestone 8 (stdlib + GI, no GTK).

One summary notification per account, like the Android Notifier's
per-account groups: a stable replaces_id per account so updates
replace instead of stacking, a sender-only body by default (never
subject or snippet), and actions Open / Archive / Mark read.

Delivery goes through the shell's org.freedesktop.Notifications
daemon — this module never owns that name, it only calls Notify on
it. Lock-screen posture comes from the shell config
(lock_screen_notifications: summary/count/off): summary shows the
sender names, count shows only the number, off sends nothing.

D-Bus replies unpack as tuples and reply_type is always a
GLib.VariantType (never a plain string); signal handlers take the
full (conn, sender, path, iface, signal, params, user_data) shape.
"""

from __future__ import annotations

import urllib.parse
import zlib

from xx_phone.schedules import schedule_matches

APP_ID = "io.piercingxx.XXEmail"
OPEN_URI_SCHEME = "io.piercingxx.XXEmail"

NOTIF_BUS_NAME = "org.freedesktop.Notifications"
NOTIF_OBJECT_PATH = "/org/freedesktop/Notifications"
NOTIF_INTERFACE = "org.freedesktop.Notifications"

ACTION_OPEN = "default"
ACTION_ARCHIVE = "archive"
ACTION_MARK_READ = "mark-read"

#: Shell hint carrying the action target (``"<bus> <path> <iface>"``).
#: The shell auto-starts that service and calls its
#: ``HandleNotificationAction(u id, s action_key)`` when the user taps
#: an action, so Archive / Mark read still work after the poller exits.
#: The bus name duplicates daemon.DAEMON_BUS_NAME (importing it here
#: would cycle); a static test pins them equal.
ACTION_TARGET_HINT = "x-xx-wm-action-target"
ACTION_BUS_NAME = "io.piercingxx.XXEmail.Daemon"
ACTION_OBJECT_PATH = "/io/piercingxx/XXEmail/Daemon"
ACTION_INTERFACE = "io.piercingxx.XXEmail.Daemon1"


def action_target_value() -> str:
    """Render the ``x-xx-wm-action-target`` hint for posted cards."""
    return f"{ACTION_BUS_NAME} {ACTION_OBJECT_PATH} {ACTION_INTERFACE}"


MAX_SENDERS_IN_BODY = 5


def notification_id_for_account(account: str) -> int:
    """Stable nonzero uint32 replaces_id for one account's summary.

    Deterministic (crc32) so the id survives reboots and updates
    replace the same notification instead of stacking. Distinct
    accounts map to distinct ids with overwhelming probability.
    """
    digest = zlib.crc32((account or "").strip().lower().encode())
    return 1 + (digest % 0x7FFFFFFF)


def lock_posture(shell_config: dict | None) -> str:
    """Shell lock-screen posture: summary, count, or off."""
    posture = (shell_config or {}).get("lock_screen_notifications",
                                       "summary")
    if posture in ("summary", "count", "off"):
        return posture
    return "summary"


def sender_display(from_name: str, from_address: str) -> str:
    """Sender-only display: header name, else the raw address."""
    if (from_name or "").strip():
        return from_name.strip()
    if (from_address or "").strip():
        return from_address.strip()
    return "?"


def build_notification(account: str, threads: list,
                       posture: str = "summary") -> dict | None:
    """Summary payload for one account's new mail, or None for off.

    threads are db.Thread rows (or duck-typed id/from_name/
    from_address). The body carries senders only — subjects and
    snippets never leave the store. count posture hides even the
    senders.
    """
    rows = [row for row in (threads or []) if row is not None]
    if not rows or posture == "off":
        return None
    count = len(rows)
    if count == 1:
        summary = "New mail"
    else:
        summary = f"{count:d} new messages"
    if posture == "count":
        body = f"{count:d} new message" + \
            ("" if count == 1 else "s")
    else:
        names = [sender_display(getattr(row, "from_name", ""),
                                getattr(row, "from_address", ""))
                 for row in rows[:MAX_SENDERS_IN_BODY]]
        names = [name for name in names if name]
        if len(rows) > MAX_SENDERS_IN_BODY:
            names.append(f"+{len(rows) - MAX_SENDERS_IN_BODY:d} more")
        body = ", ".join(names) or f"{count:d} new message" + \
            ("" if count == 1 else "s")
    latest = getattr(rows[0], "id", "")
    return {
        "account": account,
        "summary": summary,
        "body": body,
        "count": count,
        "thread_id": latest,
        "replaces_id": notification_id_for_account(account),
        "actions": [ACTION_OPEN, "Open",
                    ACTION_ARCHIVE, "Archive",
                    ACTION_MARK_READ, "Mark read"],
    }


def notify_actions_flat(payload: dict) -> list[str]:
    """Flat key/label action array for the Notify call."""
    return list(payload.get("actions", []))


def open_thread_uri(account: str, thread_id: str) -> str:
    """URI the Open action carries: scheme://thread?account=&thread=."""
    query = urllib.parse.urlencode({"account": account or "",
                                    "thread": thread_id or ""})
    return f"{OPEN_URI_SCHEME}://thread?{query}"


def parse_open_thread_uri(uri: str) -> tuple[str | None, str | None]:
    """Parse an open-thread URI or --open-thread target.

    Accepts scheme://thread?account=A&thread=T and scheme://thread/A/T
    plus a bare thread id (account None). Returns (account, thread).
    """
    text = (uri or "").strip()
    if not text:
        return (None, None)
    if "://" in text:
        scheme, _, rest = text.partition("://")
        if scheme != OPEN_URI_SCHEME:
            return (None, None)
        path, _, query = rest.partition("?")
        params = urllib.parse.parse_qs(query)
        account = (params.get("account", [None])[0] or "").strip() \
            or None
        thread = (params.get("thread", [None])[0] or "").strip() \
            or None
        if account is not None or thread is not None:
            return (account, thread)
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 3 and parts[0] == "thread":
            return (parts[1] or None, parts[2] or None)
        if len(parts) == 1:
            return (None, parts[0] or None)
        return (None, None)
    return (None, text or None)


def parse_open_target_args(args: list) -> tuple | None:
    """--open-thread ID [--account A] or an open-thread URI target.

    Pure argv parsing shared by the GUI (do_command_line) and tests:
    returns (account, thread) or None when no open target is present.
    """
    tap = None
    account = None
    index = 0
    items = list(args or [])
    while index < len(items):
        token = items[index]
        if token == "--open-thread" and index + 1 < len(items):
            thread = (items[index + 1] or "").strip() or None
            tap = (account, thread) if thread else tap
            index += 2
            continue
        if token == "--account" and index + 1 < len(items):
            account = (items[index + 1] or "").strip() or None
            if tap is not None and tap[0] is None:
                tap = (account, tap[1])
            index += 2
            continue
        if token.startswith("--account="):
            account = token.split("=", 1)[1].strip() or None
            if tap is not None and tap[0] is None:
                tap = (account, tap[1])
            index += 1
            continue
        if "://" in token:
            parsed = parse_open_thread_uri(token)
            if parsed[1]:
                tap = parsed
            index += 1
            continue
        index += 1
    return tap


def notification_hints():
    """The single hints dict every Notify call carries.

    Values are GLib.Variant (never nested prebuilt a{sv} Variants —
    the caller passes this plain dict into one Variant constructor).
    The action-target hint routes Archive / Mark read taps to the
    activatable actions service after the poller exits.
    """
    from gi.repository import GLib

    return {"desktop-entry": GLib.Variant("s", APP_ID),
            "category": GLib.Variant("s", "email.arrived"),
            "urgency": GLib.Variant("y", 1),
            ACTION_TARGET_HINT: GLib.Variant("s", action_target_value())}


def _notify_variant(summary: str, body: str, actions: list[str],
                    replaces_id: int, expire_ms: int = 15000):
    """Build the (susssasa{sv}i) Notify parameter tuple."""
    from gi.repository import GLib

    return GLib.Variant(
        "(susssasa{sv}i)",
        (APP_ID, replaces_id, "", summary, body, actions,
         notification_hints(), expire_ms))


def send_notification(connection, payload: dict, done=None,
                      expire_ms: int = 15000) -> None:
    """Async Notify for one account summary; done(server_id) on return.

    connection is the session-bus Gio.DBusConnection. The callback
    takes (conn, result) with no trailing user_data, and the reply
    unpacks as a one-tuple of out-args.
    """
    from gi.repository import Gio, GLib

    params = _notify_variant(payload["summary"], payload["body"],
                             notify_actions_flat(payload),
                             int(payload.get("replaces_id", 0)),
                             expire_ms)

    def _reply(conn, result) -> None:
        try:
            server_id = conn.call_finish(result).unpack()[0]
        except Exception:
            server_id = 0
        if done is not None:
            done(server_id)

    connection.call(
        NOTIF_BUS_NAME, NOTIF_OBJECT_PATH, NOTIF_INTERFACE, "Notify",
        params, GLib.VariantType.new("(u)"),
        Gio.DBusCallFlags.NONE, -1, None, _reply)


def app_muted(shell_config: dict | None, now_ts: float) -> bool:
    """True while the shell's muted_apps still mutes this app."""
    muted = (shell_config or {}).get("muted_apps", {})
    if not isinstance(muted, dict):
        return False
    try:
        until = float(muted.get(APP_ID, 0.0))
    except (TypeError, ValueError):
        return False
    return until > now_ts


def dnd_active(shell_config: dict | None, now) -> bool:
    """Shell Do Not Disturb in force (toggle or schedule).

    Mail has no starred/repeat-caller exception, so active DnD
    holds every new-mail notification.
    """
    config = shell_config or {}
    if bool(config.get("dnd_enabled", False)):
        return True
    schedules = config.get("dnd_schedules", [])
    return schedule_matches(schedules, now) if isinstance(
        schedules, list) else False


def _focus_norm(app_id: str) -> str:
    return app_id[:-8] if app_id.endswith(".desktop") else app_id


def focus_holds_app(shell_config: dict | None, now,
                    now_ts: float) -> bool:
    """True while shell Focus Mode pauses this app.

    Focus holds paused apps' notifications until focus ends; a
    "take a break" window suspends the hold.
    """
    config = shell_config or {}
    try:
        on_break = float(config.get("focus_break_until", 0.0)) > now_ts
    except (TypeError, ValueError):
        on_break = False
    if on_break:
        return False
    active = bool(config.get("focus_enabled", False))
    if not active:
        schedules = config.get("focus_schedules", [])
        active = schedule_matches(schedules, now) if isinstance(
            schedules, list) else False
    if not active:
        return False
    apps = config.get("focus_apps", [])
    if not isinstance(apps, list):
        return False
    wanted = _focus_norm(APP_ID)
    return any(_focus_norm(str(item)) == wanted for item in apps)


def notification_allowed(shell_config: dict | None, now=None) -> bool:
    """Whether a new-mail notification may be sent right now.

    Delivery normally goes through the shell's daemon, which applies
    this same policy — the service checks first so a foreign daemon
    (or a direct bus call) cannot bypass a mute, DnD, or focus hold.
    """
    from datetime import datetime

    moment = now if now is not None else datetime.now().astimezone()
    try:
        now_ts = moment.timestamp()
    except Exception:
        return True
    if app_muted(shell_config, now_ts):
        return False
    try:
        if dnd_active(shell_config, moment):
            return False
        if focus_holds_app(shell_config, moment, now_ts):
            return False
    except Exception:
        return True
    return True


def withdraw_notification(connection, replaces_id: int) -> None:
    """Close one account summary (account removed, nothing new)."""
    from gi.repository import Gio, GLib

    connection.call(
        NOTIF_BUS_NAME, NOTIF_OBJECT_PATH, NOTIF_INTERFACE,
        "CloseNotification",
        GLib.Variant("(u)", (int(replaces_id),)),
        None, Gio.DBusCallFlags.NONE, -1, None, None)
