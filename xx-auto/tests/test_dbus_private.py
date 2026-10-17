"""MPRIS + xx-contacts over a private bus with fake players.

Fakes mirror the real Gio traps: replies unpack to TUPLES of out-args,
reply_type is always a real GLib.VariantType, and the fake services use
the exact MPRIS / xx-contacts bus names, paths and interfaces.
"""
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    __import__("shutil").which("dbus-daemon") is None,
    reason="dbus-daemon is missing")

SRC = str(Path(__file__).parent.parent / "src")

FAKE_BUS = textwrap.dedent("""\
    import sys
    sys.path.insert(0, "__SRC__")
    import gi
    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gio, GLib

    MPRIS_XML = '''<node>
      <interface name="org.mpris.MediaPlayer2">
        <property name="Identity" type="s" access="read"/>
        <property name="DesktopEntry" type="s" access="read"/>
      </interface>
      <interface name="org.mpris.MediaPlayer2.Player">
        <method name="Play"/><method name="Pause"/><method name="PlayPause"/>
        <method name="Next"/><method name="Previous"/>
        <property name="PlaybackStatus" type="s" access="read"/>
        <property name="Metadata" type="a{sv}" access="read"/>
        <property name="Position" type="x" access="read"/>
        <property name="CanPlay" type="b" access="read"/>
        <property name="CanPause" type="b" access="read"/>
        <property name="CanGoNext" type="b" access="read"/>
        <property name="CanGoPrevious" type="b" access="read"/>
        <property name="CanSeek" type="b" access="read"/>
      </interface>
      <interface name="io.piercingxx.XXAuto.Actions1">
        <method name="ListActions"><arg direction="out" type="a(sssb)"/></method>
        <method name="ActivateAction"><arg direction="in" type="s"/></method>
      </interface>
    </node>'''

    CONTACTS_XML = '''<node>
      <interface name="io.piercingxx.Contacts1">
        <method name="List"><arg direction="in" type="t"/>
          <arg direction="out" type="t"/><arg direction="out" type="as"/>
          <arg direction="out" type="as"/></method>
        <method name="Get"><arg direction="in" type="s"/>
          <arg direction="out" type="s"/></method>
      </interface>
    </node>'''

    VCARDS = {
        "uid-alex": "BEGIN:VCARD\\nFN:Alex Morgan\\nTEL;TYPE=CELL:+1 555 0142\\nX-STARRED:1\\nEND:VCARD",
        "uid-bo": "BEGIN:VCARD\\nFN:Bo\\nTEL;TYPE=HOME:+1 555 9999\\nEND:VCARD",
    }
    CALLS = []

    PROPS_XML = ('<node><interface name="org.freedesktop.DBus.Properties">'
                   '<method name="Get"><arg direction="in" type="s"/>'
                   '<arg direction="in" type="s"/>'
                   '<arg direction="out" type="v"/></method>'
                   '<method name="GetAll"><arg direction="in" type="s"/>'
                   '<arg direction="out" type="a{sv}"/></method>'
                   '</interface></node>')

    class Player:
        def __init__(self, conn, name, identity, desktop, status, title, artist, actions):
            self.status, self.title, self.artist = status, title, artist
            self.identity, self.desktop, self.actions = identity, desktop, actions
            node = Gio.DBusNodeInfo.new_for_xml(MPRIS_XML)
            props = Gio.DBusNodeInfo.new_for_xml(PROPS_XML)
            for iface in list(node.interfaces) + list(props.interfaces):
                conn.register_object("/org/mpris/MediaPlayer2", iface, self.on_call)
            Gio.bus_own_name_on_connection(conn, name, Gio.BusNameOwnerFlags.NONE, None, None)

        def on_call(self, conn, sender, path, iface, method, params, invocation):
            if iface == "org.freedesktop.DBus.Properties" and method == "GetAll":
                (name,) = params.unpack()
                if name == "org.mpris.MediaPlayer2":
                    invocation.return_value(GLib.Variant("(a{sv})", ({"Identity": GLib.Variant("s", self.identity),
                        "DesktopEntry": GLib.Variant("s", self.desktop)},)))
                    return
                meta = {"xesam:title": GLib.Variant("s", self.title),
                        "xesam:artist": GLib.Variant("as", [self.artist])} if self.title else {}
                invocation.return_value(GLib.Variant("(a{sv})", ({"PlaybackStatus": GLib.Variant("s", self.status),
                    "Metadata": GLib.Variant("a{sv}", meta),
                    "CanPlay": GLib.Variant("b", True), "CanPause": GLib.Variant("b", True),
                    "CanGoNext": GLib.Variant("b", True), "CanGoPrevious": GLib.Variant("b", True),
                    "CanSeek": GLib.Variant("b", False)},)))
                return
            if iface == "org.freedesktop.DBus.Properties" and method == "Get":
                (_iface, prop) = params.unpack()
                if prop == "Position":
                    invocation.return_value(GLib.Variant("(v)", (GLib.Variant("x", 1000),)))
                else:
                    invocation.return_value(GLib.Variant("(v)", (GLib.Variant("s", ""),)))
                return
            if iface == "io.piercingxx.XXAuto.Actions1" and method == "ListActions":
                invocation.return_value(GLib.Variant("(a(sssb))", (self.actions,)))
                return
            if method in ("Play", "Pause", "PlayPause", "Next", "Previous", "Raise"):
                CALLS.append(method)
                if method == "PlayPause":
                    self.status = "Paused" if self.status == "Playing" else "Playing"
                invocation.return_value(None)
                return
            if method == "ActivateAction":
                CALLS.append(params.unpack()[0])
                invocation.return_value(None)
                return
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)

    class Contacts:
        def __init__(self, conn):
            node = Gio.DBusNodeInfo.new_for_xml(CONTACTS_XML)
            for iface in node.interfaces:
                conn.register_object("/io/piercingxx/Contacts", iface, self.on_call)
            Gio.bus_own_name_on_connection(conn, "io.piercingxx.Contacts",
                                           Gio.BusNameOwnerFlags.NONE, None, None)

        def on_call(self, conn, sender, path, iface, method, params, invocation):
            if method == "List":
                invocation.return_value(GLib.Variant("(tasas)", (7, list(VCARDS), [])))
                return
            (uid,) = params.unpack()
            invocation.return_value(GLib.Variant("(s)", (VCARDS.get(uid, ""),)))

    import os
    def _new_conn():
        return Gio.DBusConnection.new_for_address_sync(
            os.environ["DBUS_SESSION_BUS_ADDRESS"],
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
    conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    Player(conn, "org.mpris.MediaPlayer2.xx_audiobook", "XX-Audiobook",
           "io.piercingxx.XXAudiobook", "Playing", "Chapter 7", "The Book",
           [("skip-back", "Back 30s", "", True),
            ("bookmark", "Bookmark", "", True),
            ("skip-forward", "Forward 30s", "", True)])
    Player(_new_conn(), "org.mpris.MediaPlayer2.xx_radio", "XX-Radio",
           "io.piercingxx.XXRadio", "Paused", "Slow Morning Light",
           "Harbor FM",
           [("like", "Like", "xx-auto-thumb-up-symbolic", True),
            ("dislike", "Dislike", "xx-auto-thumb-down-symbolic", True)])
    Player(_new_conn(), "org.mpris.MediaPlayer2.plain", "Plain",
           "org.example.Plain", "Paused", "Song", "Singer", [])
    Contacts(conn)
    print("READY", flush=True)
    GLib.MainLoop().run()
    """)


def _start_fake(address):
    import os

    env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=address,
               PYTHONPATH=SRC)
    code = FAKE_BUS.replace("__SRC__", SRC)
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    line = proc.stdout.readline().strip()
    if line != "READY":
        err = proc.stderr.read()[-2000:]
        proc.terminate()
        raise AssertionError(f"fake bus failed to start: {line!r} {err!r}")
    return proc


def test_mpris_list_sort_read_and_actions(private_bus):
    import os

    os.environ["DBUS_SESSION_BUS_ADDRESS"] = private_bus
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    from xx_auto import mpris as mpris_mod

    fake = _start_fake(private_bus)
    try:
        conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        names = mpris_mod.list_player_names(conn)
        radio_bus = "org.mpris.MediaPlayer2.xx_radio"
        book_bus = "org.mpris.MediaPlayer2.xx_audiobook"
        assert radio_bus in names
        assert book_bus in names
        tracks = [mpris_mod.read_track(conn, name) for name in names]
        by_bus = {track.bus_name: track for track in tracks}
        ordered = mpris_mod.sort_players(tracks)
        assert [track.desktop_entry for track in ordered] == [
            "io.piercingxx.XXAudiobook", "io.piercingxx.XXRadio",
            "org.example.Plain"]
        assert by_bus[book_bus].title == "Chapter 7"
        assert [action.action_id
                for action in by_bus[book_bus].actions] == [
            "skip-back", "bookmark", "skip-forward"]
        assert [action.action_id
                for action in by_bus[radio_bus].actions] == [
            "like", "dislike"]
        assert by_bus[radio_bus].actions[0].icon_name == \
            "xx-auto-thumb-up-symbolic"
        assert by_bus[radio_bus].actions[1].icon_name == \
            "xx-auto-thumb-down-symbolic"
        from xx_auto.drive_tiles import Tile, tile_for

        assert tile_for(by_bus[radio_bus].desktop_entry,
                        by_bus[radio_bus].identity) is Tile.RADIO
        assert tile_for(by_bus[book_bus].desktop_entry,
                        by_bus[book_bus].identity) is Tile.AUDIOBOOK
        assert mpris_mod.read_actions(conn, names[0]) == []
        mpris_mod.send_player_method(conn, book_bus, "PlayPause")
        again = mpris_mod.read_track(conn, book_bus)
        assert again.status == "Paused"
        mpris_mod.activate_action(conn, radio_bus, "like")
    finally:
        fake.terminate()
        fake.wait(timeout=10)


def test_contacts_starred_only(private_bus):
    import os

    os.environ["DBUS_SESSION_BUS_ADDRESS"] = private_bus
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    from xx_auto import contacts_client as cc

    fake = _start_fake(private_bus)
    try:
        conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        favourites = cc.list_starred_sync(conn)
        assert [fav.name for fav in favourites] == ["Alex Morgan"]
        assert favourites[0].number == "+1 555 0142"
    finally:
        fake.terminate()
        fake.wait(timeout=10)
