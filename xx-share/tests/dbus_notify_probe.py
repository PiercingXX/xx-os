"""Real-bus probe: fake org.freedesktop.Notifications over a session bus.

Runs under ``dbus-run-session`` (no display needed). Registers a Gio
object with the real Notifications introspection XML, drives
``notify.ReceivePrompt`` against it, and proves:
- ``show()`` really issues a ``Notify`` call (string reply_type would
  raise TypeError here and ``show()`` would return False),
- an emitted ``ActionInvoked(id, accept-key)`` reaches ``on_action``,
- ``close()`` issues ``CloseNotification(id)``.

Exits 0 after printing PROBE-OK, 1 with PROBE-FAIL:<reason> otherwise.
Usage: ``dbus-run-session -- python3 dbus_notify_probe.py <src-dir>``.
"""
import sys
import time

SRC_DIR = sys.argv[1]
sys.path.insert(0, SRC_DIR)

from gi.repository import Gio, GLib  # noqa: E402

from notify import ACTION_ACCEPT, ACTION_DECLINE, ReceivePrompt  # noqa: E402

NOTIF_IFACE = "org.freedesktop.Notifications"
NOTIF_PATH = "/org/freedesktop/Notifications"

INTROSPECTION_XML = """<node>
  <interface name="org.freedesktop.Notifications">
    <method name="GetCapabilities">
      <arg name="capabilities" type="as" direction="out"/>
    </method>
    <method name="Notify">
      <arg name="app_name" type="s" direction="in"/>
      <arg name="replaces_id" type="u" direction="in"/>
      <arg name="app_icon" type="s" direction="in"/>
      <arg name="summary" type="s" direction="in"/>
      <arg name="body" type="s" direction="in"/>
      <arg name="actions" type="as" direction="in"/>
      <arg name="hints" type="a{sv}" direction="in"/>
      <arg name="expire_timeout" type="i" direction="in"/>
      <arg name="id" type="u" direction="out"/>
    </method>
    <method name="CloseNotification">
      <arg name="id" type="u" direction="in"/>
    </method>
    <signal name="NotificationClosed">
      <arg name="id" type="u"/>
      <arg name="reason" type="u"/>
    </signal>
    <signal name="ActionInvoked">
      <arg name="id" type="u"/>
      <arg name="action_key" type="s"/>
    </signal>
  </interface>
</node>
"""

NOTIFY_ID = 42

state = {
    "acquired": False,
    "notify": None,
    "closed": None,
    "actions": [],
    "prompt": None,
    "phase": "acquire",
    "deadline": time.monotonic() + 10,
    "code": 1,
}

bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
iface = node.lookup_interface(NOTIF_IFACE)


def fail(reason: str) -> bool:
    print(f"PROBE-FAIL:{reason}", flush=True)
    state["code"] = 1
    loop.quit()
    return False


def on_method(_conn, _sender, _path, _iface_name, method, params,
              invocation) -> None:
    if method == "GetCapabilities":
        invocation.return_value(GLib.Variant("(as)", (["actions"],)))
    elif method == "Notify":
        state["notify"] = params.unpack()
        invocation.return_value(GLib.Variant("(u)", (NOTIFY_ID,)))
    elif method == "CloseNotification":
        (notif_id,) = params.unpack()
        state["closed"] = int(notif_id)
        invocation.return_value(None)
    else:
        invocation.return_error_literal(
            Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD,
            f"no such method: {method}")


def on_acquired(_conn, _name) -> None:
    state["acquired"] = True


bus.register_object(NOTIF_PATH, iface, on_method, None, None)
Gio.bus_own_name(Gio.BusType.SESSION, NOTIF_IFACE,
                 Gio.BusNameOwnerFlags.NONE, None, on_acquired, None)

loop = GLib.MainLoop()


def step() -> bool:
    now = time.monotonic()
    if now > state["deadline"]:
        return fail(f"timeout in phase {state['phase']}")
    phase = state["phase"]
    if phase == "acquire":
        if state["acquired"]:
            prompt = ReceivePrompt("Incoming files from phone",
                                   "a.jpg (1.0 KB)",
                                   state["actions"].append)
            if not prompt.show():
                return fail("show() returned False")
            state["prompt"] = prompt
            state["phase"] = "notify"
            state["deadline"] = now + 10
    elif phase == "notify":
        if state["notify"] is not None:
            (app, _replaces, _icon, summary, body, actions,
             _hints, _expire) = state["notify"]
            if app != "share":
                return fail(f"wrong app_name: {app!r}")
            if summary != "Incoming files from phone":
                return fail(f"wrong summary: {summary!r}")
            if ACTION_ACCEPT not in actions or ACTION_DECLINE not in actions:
                return fail(f"wrong actions: {actions!r}")
            print("PROBE-NOTIFY-OK", flush=True)
            state["phase"] = "reply"
            state["deadline"] = now + 10
    elif phase == "reply":
        prompt = state["prompt"]
        if prompt is not None and prompt.notification_id == NOTIFY_ID:
            bus.emit_signal(None, NOTIF_PATH, NOTIF_IFACE, "ActionInvoked",
                            GLib.Variant("(us)", (NOTIFY_ID, ACTION_ACCEPT)))
            state["phase"] = "action"
            state["deadline"] = now + 10
    elif phase == "action":
        if state["actions"] == [ACTION_ACCEPT]:
            print("PROBE-ACTION-OK", flush=True)
            state["prompt"].close()
            state["phase"] = "close"
            state["deadline"] = now + 10
    elif phase == "close":
        if state["closed"] == NOTIFY_ID:
            print("PROBE-CLOSE-OK", flush=True)
            print("PROBE-OK", flush=True)
            state["code"] = 0
            loop.quit()
            return False
    return True


GLib.timeout_add(20, step)
loop.run()
sys.exit(state["code"])
