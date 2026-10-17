"""MPRIS2 interfaces for the player service. Property dictionaries are built
here from plain arguments so xx-auto and the shade see one consistent shape.
"""

from __future__ import annotations

import re

from gi.repository import GLib

from xx_music.engine import Track

MPRIS_PATH = "/org/mpris/MediaPlayer2"
ROOT_IFACE = "org.mpris.MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
PROPS_IFACE = "org.freedesktop.DBus.Properties"

MPRIS_XML = """<node>
  <interface name="org.mpris.MediaPlayer2">
    <method name="Raise"/>
    <method name="Quit"/>
    <property name="Identity" type="s" access="read"/>
    <property name="DesktopEntry" type="s" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/>
    <property name="SupportedMimeTypes" type="as" access="read"/>
  </interface>
  <interface name="org.mpris.MediaPlayer2.Player">
    <method name="Next"/>
    <method name="Previous"/>
    <method name="Pause"/>
    <method name="PlayPause"/>
    <method name="Play"/>
    <method name="Stop"/>
    <method name="Seek"><arg direction="in" type="x"/></method>
    <method name="SetPosition"><arg direction="in" type="o"/><arg direction="in" type="x"/></method>
    <method name="OpenUri"><arg direction="in" type="s"/></method>
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="LoopStatus" type="s" access="read"/>
    <property name="Rate" type="d" access="read"/>
    <property name="Shuffle" type="b" access="read"/>
    <property name="Metadata" type="a{sv}" access="read"/>
    <property name="Volume" type="d" access="read"/>
    <property name="Position" type="x" access="read"/>
    <property name="MinimumRate" type="d" access="read"/>
    <property name="MaximumRate" type="d" access="read"/>
    <property name="CanGoNext" type="b" access="read"/>
    <property name="CanGoPrevious" type="b" access="read"/>
    <property name="CanPlay" type="b" access="read"/>
    <property name="CanPause" type="b" access="read"/>
    <property name="CanSeek" type="b" access="read"/>
    <property name="CanControl" type="b" access="read"/>
    <signal name="Seeked"><arg type="x"/></signal>
  </interface>
  <interface name="org.freedesktop.DBus.Properties">
    <method name="Get"><arg direction="in" type="s"/><arg direction="in" type="s"/><arg direction="out" type="v"/></method>
    <method name="GetAll"><arg direction="in" type="s"/><arg direction="out" type="a{sv}"/></method>
    <method name="Set"><arg direction="in" type="s"/><arg direction="in" type="s"/><arg direction="in" type="v"/></method>
  </interface>
</node>"""

LOOP_FOR_REPEAT = {"none": "None", "all": "Playlist", "one": "Track"}
REPEAT_FOR_LOOP = {value: key for key, value in LOOP_FOR_REPEAT.items()}


def track_uid(player_object_path: str, track_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]", "_", track_id) or "track"
    return f"{player_object_path}/Track/{safe}"


def metadata_for(player_object_path: str, track: Track | None,
                 length_us: int, art_url: str) -> dict:
    meta: dict = {}
    if track is None:
        return meta
    meta["mpris:trackid"] = GLib.Variant(
        "o", track_uid(player_object_path, track.id))
    meta["xesam:title"] = GLib.Variant("s", track.title)
    meta["xesam:artist"] = GLib.Variant(
        "as", [a.name for a in track.artists if a.name])
    if track.album is not None and track.album.title:
        meta["xesam:album"] = GLib.Variant("s", track.album.title)
    if length_us > 0:
        meta["mpris:length"] = GLib.Variant("x", int(length_us))
    if art_url:
        meta["mpris:artUrl"] = GLib.Variant("s", art_url)
    return meta


def root_props(identity: str, desktop_entry: str) -> dict:
    return {
        "Identity": GLib.Variant("s", identity),
        "DesktopEntry": GLib.Variant("s", desktop_entry),
        "CanRaise": GLib.Variant("b", True),
        "CanQuit": GLib.Variant("b", True),
        "HasTrackList": GLib.Variant("b", False),
        "SupportedUriSchemes": GLib.Variant("as", ["http", "file"]),
        "SupportedMimeTypes": GLib.Variant(
            "as", ["audio/ogg", "audio/opus", "audio/webm",
                   "audio/x-m4a", "audio/mpeg"]),
    }


def player_props(status: str, repeat: str, shuffle: bool, volume: float,
                 position_us: int, metadata: dict, can_next: bool,
                 can_prev: bool, can_play: bool, can_pause: bool,
                 can_seek: bool) -> dict:
    return {
        "PlaybackStatus": GLib.Variant("s", status),
        "LoopStatus": GLib.Variant(
            "s", LOOP_FOR_REPEAT.get(repeat, "None")),
        "Rate": GLib.Variant("d", 1.0),
        "Shuffle": GLib.Variant("b", bool(shuffle)),
        "Metadata": GLib.Variant("a{sv}", metadata),
        "Volume": GLib.Variant("d", float(volume)),
        "Position": GLib.Variant("x", int(position_us)),
        "MinimumRate": GLib.Variant("d", 1.0),
        "MaximumRate": GLib.Variant("d", 1.0),
        "CanGoNext": GLib.Variant("b", bool(can_next)),
        "CanGoPrevious": GLib.Variant("b", bool(can_prev)),
        "CanPlay": GLib.Variant("b", bool(can_play)),
        "CanPause": GLib.Variant("b", bool(can_pause)),
        "CanSeek": GLib.Variant("b", bool(can_seek)),
        "CanControl": GLib.Variant("b", True),
    }


def changed_signal(interface: str, changed: dict) -> GLib.Variant:
    return GLib.Variant("(sa{sv}as)", (interface, changed, []))
