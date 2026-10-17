"""Desktop notifications (ring + missed). Headless-safe: Gio loads lazily.

Ringing posts urgency-critical with actions (Snooze/Dismiss for alarms,
Stop/+1 min for timers) so the ring is visible over the XX-WM lock screen.
The hints dict stays a plain dict inside one Variant construction — never a
nested prebuilt Variant — and reply_type is a real GLib.VariantType.
"""

from __future__ import annotations

from collections.abc import Callable

NOTIFY_BUS = "org.freedesktop.Notifications"
NOTIFY_PATH = "/org/freedesktop/Notifications"
NOTIFY_IFACE = "org.freedesktop.Notifications"

APP_NAME = "xx-clock"


def _gio():
    import gi
    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gio, GLib
    return Gio, GLib


def notify_params(summary: str, body: str, actions: list[str], critical: bool,
                  glib=None):
    if glib is None:
        _, glib = _gio()
    hints = {"urgency": glib.Variant("y", 2 if critical else 1)}
    return glib.Variant("(susssasa{sv}i)",
                        (APP_NAME, 0, "io.piercingxx.XXClock",
                         summary, body, actions, hints,
                         0 if critical else 10000))


class Notifier:
    def __init__(self, on_action: Callable[[str, int], None] | None = None) -> None:
        self._on_action = on_action or (lambda _action, _nid: None)
        self._conn = None
        self._sub = None

    def _bus(self):
        if self._conn is not None:
            return self._conn
        try:
            Gio, _ = _gio()
            self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception:
            return None
        return self._conn

    def _watch_actions(self) -> None:
        if self._sub is not None:
            return
        bus = self._bus()
        if bus is None:
            return

        def _invoked(_conn, _sender, _path, _iface, _signal, params, _data=None):
            try:
                nid, action = params.unpack()
            except Exception:
                return
            try:
                self._on_action(str(action), int(nid))
            except Exception:
                pass

        try:
            self._sub = bus.signal_subscribe(
                None, NOTIFY_IFACE, "ActionInvoked", NOTIFY_PATH, None,
                0, _invoked)
        except Exception:
            self._sub = None

    def post(self, summary: str, body: str = "", actions: list[str] | None = None,
             critical: bool = False) -> int | None:
        bus = self._bus()
        if bus is None:
            return None
        _, GLib = _gio()
        self._watch_actions()
        params = notify_params(summary, body, actions or [], critical)
        try:
            bus.call(NOTIFY_BUS, NOTIFY_PATH, NOTIFY_IFACE, "Notify", params,
                     GLib.VariantType.new("(u)"), 0, 5000, None,
                     lambda conn, res: _notify_done(conn, res))
        except Exception:
            return None
        return None

    def ring_alarm(self, label: str, fire_text: str) -> int | None:
        return self.post(label or "Alarm", fire_text,
                         ["snooze", "Snooze", "dismiss", "Dismiss"], critical=True)

    def ring_timer(self, label: str) -> int | None:
        return self.post(label or "Timer", "Timer finished",
                         ["stop", "Stop", "addminute", "+1 min"], critical=True)

    def missed(self, title: str, body: str = "") -> int | None:
        return self.post(title, body, [], critical=False)


def _notify_done(conn, res) -> dict:
    try:
        reply = conn.call_finish(res)
        return {"id": int(reply.unpack()[0])}
    except Exception:
        return {}
