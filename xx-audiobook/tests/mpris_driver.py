"""MPRIS + Player1 driver for test_mpris.py (runs under dbus-run-session).

Starts the REAL PlayerService (FakeBackend, in-memory DB, offline) on the
private session bus, then drives it as a Gio client exactly like xx-auto
does: GetAll root/player, transport, Seek/SetPosition, Seeked and
PropertiesChanged signals, Actions1, Player1 state/sleep, and the
signed-out Open error. Prints PASS or raises.
"""

import os
import sys
import tempfile
import threading
import time

HOME = tempfile.mkdtemp(prefix="xx-audiobook-mpris-")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = os.path.join(HOME, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(HOME, "data")

sys.path.insert(0, os.environ["SRC_DIR"])

import gi  # noqa: E402

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from xx_audiobook import __app_id__ as APP_ID  # noqa: E402
from xx_audiobook.player import FakeBackend  # noqa: E402
from xx_audiobook.service.player_service import (  # noqa: E402
    ACTIONS_INTERFACE,
    MPRIS_BUS,
    MPRIS_PATH,
    MPRIS_PLAYER,
    MPRIS_ROOT,
    OBJECT_PATH,
    PLAYER_BUS,
    PLAYER_INTERFACE,
    PROPS_INTERFACE,
    PlayerService,
)
from xx_audiobook.store import Database  # noqa: E402
from xx_audiobook.timeline import (  # noqa: E402
    BookTimeline,
    Chapter,
    LoadedBook,
    Track,
)

TIMEOUT_S = 5.0


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def reply_type(signature):
    return GLib.VariantType.new(signature)


class Client:
    def __init__(self, connection):
        self.connection = connection

    def call(self, bus, path, iface, method, args=None,
             reply_sig=None):
        return self.connection.call_sync(
            bus, path, iface, method, args,
            reply_type(reply_sig) if reply_sig else None,
            Gio.DBusCallFlags.NONE, 5000, None)

    def get_all(self, bus, path, iface):
        reply = self.call(bus, path, PROPS_INTERFACE, "GetAll",
                          GLib.Variant("(s)", (iface,)), "(a{sv})")
        (props,) = reply.unpack()
        return props

    def get(self, bus, path, iface, prop):
        reply = self.call(bus, path, PROPS_INTERFACE, "Get",
                          GLib.Variant("(ss)", (iface, prop)), "(v)")
        (boxed,) = reply.unpack()
        return boxed


def wait_for(predicate, message):
    deadline = time.monotonic() + TIMEOUT_S
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(message)


def main():
    db = Database.in_memory()
    service = PlayerService(db, None, FakeBackend())
    book = LoadedBook(
        "it-9", "Driver Book", "Driver Author",
        None, None,
        BookTimeline(
            [Track("a", "file:///d/a.m4b", 0, 60000, "audio/mp4"),
             Track("b", "file:///d/b.m4b", 60000, 60000, "audio/mp4")],
            [Chapter(0, "One", 0, 60000),
             Chapter(1, "Two", 60000, 120000)]))
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    service.register_on(connection)
    Gio.bus_own_name_on_connection(connection, PLAYER_BUS,
                                   Gio.BusNameOwnerFlags.NONE, None, None)
    Gio.bus_own_name_on_connection(connection, MPRIS_BUS,
                                   Gio.BusNameOwnerFlags.NONE, None, None)

    events = {"seeked": [], "props": [], "bookmark": []}

    def on_signal(*args):
        params = args[5] if len(args) > 5 else None
        signal = args[4] if len(args) > 4 else ""
        if signal == "Seeked":
            events["seeked"].append(params.unpack())
        elif signal == "PropertiesChanged":
            events["props"].append(params.unpack())
        elif signal == "BookmarkRequested":
            events["bookmark"].append(params.unpack())

    connection.signal_subscribe(None, PROPS_INTERFACE, "PropertiesChanged",
                                MPRIS_PATH, None,
                                Gio.DBusSignalFlags.NONE, on_signal)
    connection.signal_subscribe(None, MPRIS_PLAYER, "Seeked",
                                MPRIS_PATH, None,
                                Gio.DBusSignalFlags.NONE, on_signal)
    connection.signal_subscribe(None, PLAYER_INTERFACE,
                                "BookmarkRequested", OBJECT_PATH, None,
                                Gio.DBusSignalFlags.NONE, on_signal)

    loop = GLib.MainLoop()
    failures = []

    def work():
        try:
            run_checks(connection, service, book, events)
        except Exception as exc:  # noqa: BLE001
            import traceback

            failures.append("".join(
                traceback.format_exception(exc)))
        finally:
            GLib.idle_add(loop.quit)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    loop.run()
    thread.join(timeout=10)
    if failures:
        print("FAIL")
        print(failures[0])
        return 1
    print("PASS")
    return 0


def run_checks(connection, service, book, events):
    client = Client(connection)

    def names():
        reply = connection.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "ListNames", None,
            reply_type("(as)"), Gio.DBusCallFlags.NONE, 5000, None)
        (listed,) = reply.unpack()
        return set(listed)

    wait_for(lambda: MPRIS_BUS in names(), "MPRIS name not owned")
    check(PLAYER_BUS in names(), "player name not owned")
    check(APP_ID not in names(), "service must not own the GUI app id")

    service.finish_open((book, 0, 1.0))

    root = client.get_all(MPRIS_BUS, MPRIS_PATH, MPRIS_ROOT)
    check(root["Identity"] == "xx-audiobook", "Identity")
    check(root["DesktopEntry"] == "io.piercingxx.XXAudiobook",
          "DesktopEntry")

    player = client.get_all(MPRIS_BUS, MPRIS_PATH, MPRIS_PLAYER)
    check(player["PlaybackStatus"] == "Paused", "starts paused")
    check(player["CanGoNext"] is True, "CanGoNext maps to chapters")
    check(player["CanGoPrevious"] is True, "CanGoPrevious maps")
    check(player["CanSeek"] is True, "CanSeek")
    check(player["Rate"] == 1.0, "Rate")
    meta = dict(player["Metadata"])
    check(meta["xesam:title"] == "Driver Book", "metadata title")
    check(meta["xesam:artist"] == ["Driver Author"], "metadata artist")
    check(meta["mpris:length"] == 120000 * 1000, "metadata length")

    state = client.call(PLAYER_BUS, OBJECT_PATH, PLAYER_INTERFACE,
                        "GetState", None, "(a{sv})")
    (boxed,) = state.unpack()
    check(boxed["has_book"] is True, "GetState has_book")
    check(boxed["duration_us"] == 120000 * 1000, "GetState duration")

    client.call(MPRIS_BUS, MPRIS_PATH, MPRIS_PLAYER, "Play")
    wait_for(lambda: client.get(
        MPRIS_BUS, MPRIS_PATH, MPRIS_PLAYER,
        "PlaybackStatus") == "Playing", "Play status")
    check(len(events["props"]) >= 1, "PropertiesChanged on Play")

    client.call(MPRIS_BUS, MPRIS_PATH, MPRIS_PLAYER, "Seek",
                GLib.Variant("(x)", (30000000,)))
    wait_for(lambda: client.get(
        MPRIS_BUS, MPRIS_PATH, MPRIS_PLAYER,
        "Position") == 30000000, "Seek position")
    wait_for(lambda: events["seeked"], "Seeked after Seek")
    check(events["seeked"][-1] == (30000000,), "Seeked value")

    track0 = "/io/piercingxx/XXAudiobook/Track/0"
    client.call(MPRIS_BUS, MPRIS_PATH, MPRIS_PLAYER, "SetPosition",
                GLib.Variant("(ox)", (track0, 10000000,)))
    wait_for(lambda: client.get(
        MPRIS_BUS, MPRIS_PATH, MPRIS_PLAYER,
        "Position") == 10000000, "SetPosition position")
    client.call(MPRIS_BUS, MPRIS_PATH, MPRIS_PLAYER, "SetPosition",
                GLib.Variant("(ox)", ("/wrong/9", 60000000,)))
    time.sleep(0.3)
    check(client.get(MPRIS_BUS, MPRIS_PATH, MPRIS_PLAYER, "Position")
          == 10000000, "unknown track id is ignored")

    client.call(MPRIS_BUS, MPRIS_PATH, MPRIS_PLAYER, "Next")
    wait_for(lambda: client.get(
        MPRIS_BUS, MPRIS_PATH, MPRIS_PLAYER,
        "Position") == 60000000, "Next goes to chapter Two")
    client.call(MPRIS_BUS, MPRIS_PATH, MPRIS_PLAYER, "Previous")
    wait_for(lambda: client.get(
        MPRIS_BUS, MPRIS_PATH, MPRIS_PLAYER,
        "Position") == 0, "Previous restarts chapter one")

    reply = client.call(MPRIS_BUS, MPRIS_PATH, ACTIONS_INTERFACE,
                        "ListActions", None, "(a(sssb))")
    (rows,) = reply.unpack()
    check([row[0] for row in rows] == [
        "skip-back", "bookmark", "skip-forward"], "action rows")
    client.call(MPRIS_BUS, MPRIS_PATH, ACTIONS_INTERFACE,
                "ActivateAction", GLib.Variant("(s)", ("skip-forward",)))
    wait_for(lambda: client.get(
        MPRIS_BUS, MPRIS_PATH, MPRIS_PLAYER,
        "Position") == 30000000, "skip-forward action")
    client.call(MPRIS_BUS, MPRIS_PATH, ACTIONS_INTERFACE,
                "ActivateAction", GLib.Variant("(s)", ("bookmark",)))
    wait_for(lambda: events["bookmark"], "BookmarkRequested signal")
    check(events["bookmark"][-1][0] == "it-9", "bookmark item")

    client.call(PLAYER_BUS, OBJECT_PATH, PLAYER_INTERFACE, "Sleep",
                GLib.Variant("(x)", (60000,)))
    updated = client.call(PLAYER_BUS, OBJECT_PATH, PLAYER_INTERFACE,
                          "GetState", None, "(a{sv})")
    (boxed,) = updated.unpack()
    check(boxed["sleep_remaining_ms"] == 60000, "sleep state")
    client.call(PLAYER_BUS, OBJECT_PATH, PLAYER_INTERFACE,
                "CancelSleep")

    try:
        client.call(PLAYER_BUS, OBJECT_PATH, PLAYER_INTERFACE, "Open",
                    GLib.Variant("(s)", ("it-9",)))
    except GLib.Error:
        pass
    else:
        raise AssertionError("signed-out Open must fail")

    client.call(MPRIS_BUS, MPRIS_PATH, MPRIS_PLAYER, "Pause")
    wait_for(lambda: client.get(
        MPRIS_BUS, MPRIS_PATH, MPRIS_PLAYER,
        "PlaybackStatus") == "Paused", "Pause status")


if __name__ == "__main__":
    raise SystemExit(main())
