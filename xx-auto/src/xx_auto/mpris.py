"""MPRIS2 media access for the drive screen (replaces Media3).

Pure helpers (parse/sort) stay importable without gi; the Gio-backed
client runs on a worker thread and posts snapshots back with
GLib.idle_add, so the GTK loop never blocks.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MPRIS_PREFIX = "org.mpris.MediaPlayer2."
SUITE_DESKTOP_PREFIX = "io.piercingxx."

ACTIONS_INTERFACE = "io.piercingxx.XXAuto.Actions1"
ACTIONS_PATH = "/org/mpris/MediaPlayer2"


@dataclass
class CustomAction:
    action_id: str
    label: str
    icon_name: str = ""
    enabled: bool = True


@dataclass
class Track:
    bus_name: str
    identity: str = ""
    desktop_entry: str = ""
    title: str = ""
    artist: str = ""
    art_url: str = ""
    length_us: int = 0
    position_us: int = 0
    status: str = "Stopped"
    can_play: bool = False
    can_pause: bool = False
    can_next: bool = False
    can_prev: bool = False
    can_seek: bool = False
    actions: list[CustomAction] = field(default_factory=list)
    last_active_ms: int = 0

    @property
    def is_playing(self) -> bool:
        return self.status == "Playing"

    @property
    def has_item(self) -> bool:
        return bool(self.title or self.artist or self.length_us > 0)

    @property
    def short_name(self) -> str:
        name = self.bus_name
        if name.startswith(MPRIS_PREFIX):
            name = name[len(MPRIS_PREFIX):]
        return name


def display_name(track: Track) -> str:
    """Suite-aware player label: xx-audiobook style names stay readable."""
    if track.desktop_entry.startswith(SUITE_DESKTOP_PREFIX):
        tail = track.desktop_entry[len(SUITE_DESKTOP_PREFIX):]
        name = tail.replace("-", " ").strip()
        # Suite ids spell the brand prefix in caps ("XXRadio"): read it
        # the way the suite writes app names, "xx-radio".
        if len(name) > 2 and name[:2] == "XX" and name[2].isupper():
            name = "xx-" + name[2:].lower()
        return name or track.identity or track.short_name
    return track.identity or track.short_name


def is_suite(track: Track) -> bool:
    return track.desktop_entry.startswith(SUITE_DESKTOP_PREFIX)


def sort_players(tracks: list[Track]) -> list[Track]:
    """Suite players first, then the rest; stable by recency within each."""
    return sorted(tracks,
                  key=lambda t: (not is_suite(t), -(t.last_active_ms or 0)))


def _plain(value):
    unpack = getattr(value, "unpack", None)
    if callable(unpack):
        try:
            return unpack()
        except Exception:
            return value
    return value


def parse_metadata(metadata: dict) -> tuple[str, str, str, int]:
    """(title, artist, art_url, length_us) from an MPRIS Metadata dict."""
    metadata = {key: _plain(val) for key, val in (metadata or {}).items()}
    title = str(metadata.get("xesam:title", "") or "")

    def _first(value: object) -> str:
        if isinstance(value, (list, tuple)):
            return str(value[0]) if value else ""
        return str(value or "")

    artist = _first(metadata.get("xesam:artist",
                                 metadata.get("xesam:albumArtist", "")))
    art = str(metadata.get("mpris:artUrl", "") or "")
    try:
        length = int(metadata.get("mpris:length", 0) or 0)
    except (TypeError, ValueError):
        length = 0
    return title, artist, art, max(0, length)


def _reply_type(signature: str | None):
    if signature is None:
        return None
    from gi.repository import GLib

    return GLib.VariantType.new(signature)


def list_player_names(connection) -> list[str]:
    """MPRIS bus names on a session connection (worker-thread safe)."""
    from gi.repository import Gio

    reply = connection.call_sync(
        "org.freedesktop.DBus", "/org/freedesktop/DBus",
        "org.freedesktop.DBus", "ListNames", None,
        _reply_type("(as)"), Gio.DBusCallFlags.NONE, 5000, None)
    (names,) = reply.unpack()
    return sorted(n for n in names if n.startswith(MPRIS_PREFIX))


def read_track(connection, bus_name: str, timeout_ms: int = 5000) -> Track:
    """Snapshot one player; missing properties degrade to defaults."""
    from gi.repository import Gio

    track = Track(bus_name=bus_name)

    def _maybe_unbox(value):
        unpack = getattr(value, "unpack", None)
        if callable(unpack):
            try:
                return unpack()
            except Exception:
                return value
        return value

    def _get(iface: str, prop: str):
        from gi.repository import GLib

        try:
            reply = connection.call_sync(
                bus_name, "/org/mpris/MediaPlayer2",
                "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", (iface, prop)),
                _reply_type("(v)"), Gio.DBusCallFlags.NONE,
                timeout_ms, None)
            (boxed,) = reply.unpack()
            return _maybe_unbox(boxed)
        except Exception:
            return None

    def _get_all(iface: str) -> dict:
        from gi.repository import GLib

        try:
            reply = connection.call_sync(
                bus_name, "/org/mpris/MediaPlayer2",
                "org.freedesktop.DBus.Properties", "GetAll",
                GLib.Variant("(s)", (iface,)),
                _reply_type("(a{sv})"), Gio.DBusCallFlags.NONE,
                timeout_ms, None)
            (props,) = reply.unpack()
            out = {}
            for key, boxed in props.items():
                out[key] = _maybe_unbox(boxed)
            return out
        except Exception:
            return {}

    root = _get_all("org.mpris.MediaPlayer2")
    player = _get_all("org.mpris.MediaPlayer2.Player")
    track.identity = str(root.get("Identity", "") or "")
    track.desktop_entry = str(root.get("DesktopEntry", "") or "")
    track.can_play = bool(player.get("CanPlay", False))
    track.can_pause = bool(player.get("CanPause", False))
    track.can_next = bool(player.get("CanGoNext", False))
    track.can_prev = bool(player.get("CanGoPrevious", False))
    track.can_seek = bool(player.get("CanSeek", False))
    track.status = str(player.get("PlaybackStatus", "Stopped") or "Stopped")
    metadata = player.get("Metadata", {}) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    title, artist, art, length = parse_metadata(metadata)
    track.title, track.artist = title, artist
    track.art_url, track.length_us = art, length
    pos = _get("org.mpris.MediaPlayer2.Player", "Position")
    try:
        track.position_us = max(0, int(pos or 0))
    except (TypeError, ValueError):
        track.position_us = 0
    track.actions = read_actions(connection, bus_name, timeout_ms)
    return track


def read_actions(connection, bus_name: str,
                 timeout_ms: int = 5000) -> list[CustomAction]:
    """Optional Actions1 rows; [] when the player does not offer them."""
    from gi.repository import Gio


    try:
        reply = connection.call_sync(
            bus_name, ACTIONS_PATH, ACTIONS_INTERFACE, "ListActions", None,
            _reply_type("(a(sssb))"), Gio.DBusCallFlags.NONE,
            timeout_ms, None)
        (rows,) = reply.unpack()
    except Exception:
        return []
    actions = []
    for row in rows or []:
        try:
            action_id, label, icon, enabled = row
            actions.append(CustomAction(str(action_id), str(label),
                                        str(icon or ""), bool(enabled)))
        except (TypeError, ValueError):
            continue
    return actions


def send_player_method(connection, bus_name: str, method: str,
                       parameters=None, timeout_ms: int = 5000) -> None:
    """Fire a Player method; D-Bus errors surface to the caller."""
    from gi.repository import Gio

    connection.call_sync(
        bus_name, "/org/mpris/MediaPlayer2",
        "org.mpris.MediaPlayer2.Player", method, parameters,
        None, Gio.DBusCallFlags.NONE, timeout_ms, None)


def activate_action(connection, bus_name: str, action_id: str,
                    timeout_ms: int = 5000) -> None:
    """Fire an Actions1 custom button; errors surface to the caller."""
    from gi.repository import Gio

    from gi.repository import GLib

    connection.call_sync(
        bus_name, ACTIONS_PATH, ACTIONS_INTERFACE, "ActivateAction",
        GLib.Variant("(s)", (action_id,)),
        None, Gio.DBusCallFlags.NONE, timeout_ms, None)
