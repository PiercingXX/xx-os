"""The window's view of the player: one model, two backends.

``PlayerModel`` holds what the screens show (the service snapshot, the
queue, the ratings this window knows) and turns taps into commands. The
screens never talk D-Bus themselves.

- ``DbusBackend`` drives the real player service: Player1 for queue
  work, MPRIS2 for transport, signals for updates. Every call is async
  and returns on the main loop; a missing bus just leaves the model
  empty (no mini player).
- ``LocalBackend`` applies the same commands in process over the real
  ``Queue``: tests, demo renders, and a window without a service.

Positions tick locally between snapshots (the service only announces
changes), so the scrubber moves without a D-Bus round trip per second.
"""

from __future__ import annotations

import json
import time
from typing import Callable

from xx_music.engine import Rating, Track
from xx_music.queue import REPEAT_ALL, REPEAT_MODES, REPEAT_NONE, Queue

PLAYING = "Playing"
PAUSED = "Paused"
STOPPED = "Stopped"

SLEEP_OFF = 0
SLEEP_END_OF_SONG = -1

MPRIS_PATH = "/org/mpris/MediaPlayer2"
MPRIS_PLAYER = "org.mpris.MediaPlayer2.Player"


class PlayerModel:
    def __init__(self, backend=None, clock: Callable[[], float] | None = None
                 ) -> None:
        self._clock = clock or time.monotonic
        self._listeners: list[Callable[[], None]] = []
        self._queue_listeners: list[Callable[[], None]] = []
        self.snapshot: dict = {}
        self.queue: list[Track] = []
        self.queue_version = -1
        self.ratings: dict[str, Rating] = {}
        self._stamp = self._clock()
        self.backend = backend
        if backend is not None:
            backend.attach(self)

    # -- listeners -----------------------------------------------------------
    def connect(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(callback)
        return lambda: self._drop(self._listeners, callback)

    def connect_queue(self, callback: Callable[[], None]
                      ) -> Callable[[], None]:
        self._queue_listeners.append(callback)
        return lambda: self._drop(self._queue_listeners, callback)

    @staticmethod
    def _drop(where: list, callback) -> None:
        try:
            where.remove(callback)
        except ValueError:
            pass

    def _emit(self) -> None:
        for callback in list(self._listeners):
            callback()

    def _emit_queue(self) -> None:
        for callback in list(self._queue_listeners):
            callback()

    # -- state fed by the backend -------------------------------------------
    def apply_snapshot(self, snapshot: dict) -> None:
        self.snapshot = dict(snapshot or {})
        self._stamp = self._clock()
        if self.snapshot.get("version", -1) != self.queue_version \
                and self.backend is not None:
            self.backend.fetch_queue()
        self._emit()

    def apply_queue(self, version: int, tracks: list[Track],
                    index: int) -> None:
        self.queue = list(tracks)
        self.queue_version = version
        if self.snapshot:
            self.snapshot["index"] = index
            self.snapshot["length"] = len(tracks)
        self._emit_queue()

    # -- reading -------------------------------------------------------------
    @property
    def track(self) -> Track | None:
        raw = self.snapshot.get("track")
        if not raw:
            return None
        try:
            return Track.from_dict(raw)
        except (KeyError, TypeError, ValueError):
            return None

    @property
    def has_queue(self) -> bool:
        return self.track is not None

    @property
    def status(self) -> str:
        return str(self.snapshot.get("status") or STOPPED)

    @property
    def playing(self) -> bool:
        return self.status == PLAYING

    @property
    def index(self) -> int:
        return int(self.snapshot.get("index", -1))

    @property
    def duration_s(self) -> float:
        value = float(self.snapshot.get("duration_s") or 0.0)
        if value <= 0 and self.track and self.track.duration_s:
            value = float(self.track.duration_s)
        return value

    @property
    def position_s(self) -> float:
        base = float(self.snapshot.get("position_s") or 0.0)
        if self.playing:
            base += max(0.0, self._clock() - self._stamp)
        duration = self.duration_s
        return min(base, duration) if duration > 0 else base

    @property
    def fraction(self) -> float:
        duration = self.duration_s
        return min(max(self.position_s / duration, 0.0), 1.0) \
            if duration > 0 else 0.0

    @property
    def shuffle(self) -> bool:
        return bool(self.snapshot.get("shuffle"))

    @property
    def repeat(self) -> str:
        mode = str(self.snapshot.get("repeat") or REPEAT_NONE)
        return mode if mode in REPEAT_MODES else REPEAT_NONE

    @property
    def sleep(self) -> dict | None:
        value = self.snapshot.get("sleep")
        return value if isinstance(value, dict) else None

    @property
    def sleep_remaining_s(self) -> float | None:
        sleep = self.sleep
        if not sleep or sleep.get("mode") == SLEEP_END_OF_SONG:
            return None
        remaining = float(sleep.get("remaining_s") or 0.0)
        if self.playing:
            remaining -= max(0.0, self._clock() - self._stamp)
        return max(0.0, remaining)

    @property
    def sleep_end_of_song(self) -> bool:
        sleep = self.sleep
        return bool(sleep) and sleep.get("mode") == SLEEP_END_OF_SONG

    @property
    def problem(self) -> dict | None:
        value = self.snapshot.get("problem")
        return value if isinstance(value, dict) else None

    @property
    def origin(self) -> dict:
        value = self.snapshot.get("origin")
        return value if isinstance(value, dict) else {}

    @property
    def offline(self) -> bool:
        return bool(self.snapshot.get("offline"))

    @property
    def cast(self) -> dict:
        value = self.snapshot.get("cast")
        return dict(value) if isinstance(value, dict) else {}

    @property
    def cast_name(self) -> str:
        return str(self.cast.get("connectedName") or "")

    @property
    def casting(self) -> bool:
        return bool(self.cast.get("connectedId"))

    def refresh_cast(self) -> None:
        self._send("refresh_cast")

    def cast_to(self, device_id: str) -> None:
        if device_id:
            self._send("cast_to", str(device_id))

    def stop_casting(self) -> None:
        self._send("stop_casting")

    def set_volume(self, level: float) -> None:
        self._send("set_volume", min(1.0, max(0.0, float(level))))

    def rating(self, track_id: str) -> Rating:
        return self.ratings.get(track_id, Rating.NONE)

    def up_next(self) -> list[tuple[int, Track]]:
        start = self.index + 1 if self.index >= 0 else 0
        return list(enumerate(self.queue))[start:]

    # -- commands ------------------------------------------------------------
    def _send(self, name: str, *args) -> None:
        if self.backend is not None:
            getattr(self.backend, name)(*args)

    def play(self, tracks: list[Track], start: int = 0,
             origin: dict | None = None) -> None:
        if tracks:
            self._send("play", list(tracks), int(start), dict(origin or {}))

    def start_radio(self, track: Track) -> None:
        self._send("start_radio", track)

    def enqueue(self, tracks: list[Track], play_next: bool = False) -> None:
        if tracks:
            self._send("enqueue", list(tracks), bool(play_next))

    def play_at(self, index: int) -> None:
        self._send("play_at", int(index))

    def remove(self, index: int) -> None:
        self._send("remove", int(index))

    def move(self, source: int, dest: int) -> None:
        self._send("move", int(source), int(dest))

    def play_pause(self) -> None:
        self._send("play_pause")

    def next(self) -> None:
        self._send("next")

    def previous(self) -> None:
        self._send("previous")

    def seek(self, seconds: float) -> None:
        self.snapshot["position_s"] = max(0.0, float(seconds))
        self._stamp = self._clock()
        self._send("seek", max(0.0, float(seconds)))
        self._emit()

    def toggle_shuffle(self) -> None:
        self._send("set_shuffle", not self.shuffle)

    def set_shuffle(self, on: bool) -> None:
        self._send("set_shuffle", bool(on))

    def cycle_repeat(self) -> None:
        order = (REPEAT_NONE, REPEAT_ALL, "one")
        mode = order[(order.index(self.repeat) + 1) % len(order)]
        self._send("set_repeat", mode)

    def set_sleep(self, seconds: int) -> None:
        self._send("set_sleep", int(seconds))

    def rate(self, track_id: str, rating: Rating) -> None:
        current = self.rating(track_id)
        value = Rating.NONE if current == rating else rating
        self.ratings[track_id] = value
        self._send("rate", track_id, value.value)
        self._emit()

    def download(self, items: list[dict]) -> None:
        if items:
            self._send("download", list(items))

    def remove_download(self, item_id: str) -> None:
        if item_id:
            self._send("remove_download", item_id)


class LocalBackend:
    """Applies commands in process over the real Queue (no audio).

    Used by tests, by the demo renders and when no player service is
    reachable. It answers synchronously; the model emits at once.
    """

    def __init__(self, queue: Queue | None = None, status: str = PAUSED,
                 position_s: float = 0.0,
                 radio: Callable[[Track], list[Track]] | None = None) -> None:
        self.queue = queue or Queue()
        self.status = status if self.queue.tracks else STOPPED
        self.position_s = position_s
        self.sleep: dict | None = None
        self.problem: dict | None = None
        self.offline = False
        self.downloads: list[dict] = []
        self.removed: list[str] = []
        self.calls: list[str] = []
        self._radio = radio
        self.model: PlayerModel | None = None

    def attach(self, model: PlayerModel) -> None:
        self.model = model
        self._push(structural=True)

    def snapshot(self) -> dict:
        track = self.queue.current()
        return {
            "status": self.status,
            "track": track.to_dict() if track else None,
            "position_s": self.position_s,
            "duration_s": float(track.duration_s or 0) if track else 0.0,
            "index": self.queue.index,
            "length": len(self.queue.tracks),
            "version": self.queue.version,
            "shuffle": self.queue.shuffle,
            "repeat": self.queue.repeat,
            "sleep": self.sleep,
            "problem": self.problem,
            "origin": self.queue.origin,
            "offline": self.offline,
            "cast": {"available": False, "searching": False,
                     "devices": [], "connectedId": None,
                     "connectedName": "", "playing": False,
                     "positionMs": 0, "durationMs": 0, "busy": False,
                     "error": None, "volume": 1.0, "muted": False,
                     "mediaId": None},
        }

    def fetch_queue(self) -> None:
        if self.model is not None:
            self.model.apply_queue(self.queue.version,
                                   list(self.queue.tracks), self.queue.index)

    def _push(self, structural: bool = False) -> None:
        if self.model is None:
            return
        if structural:
            self.fetch_queue()
        self.model.apply_snapshot(self.snapshot())

    # commands
    def play(self, tracks, start, origin) -> None:
        self.calls.append("play")
        self.queue.replace(tracks, start, origin)
        self.status, self.position_s = PLAYING, 0.0
        self._push(True)

    def start_radio(self, track) -> None:
        self.calls.append("start_radio")
        more = self._radio(track) if self._radio else []
        self.queue.replace([track, *more], 0,
                           {"type": "radio", "seed": track.id,
                            "title": track.title})
        self.status, self.position_s = PLAYING, 0.0
        self._push(True)

    def enqueue(self, tracks, play_next) -> None:
        self.calls.append("enqueue")
        self.queue.enqueue(tracks, play_next)
        if self.status == STOPPED and self.queue.tracks:
            self.status = PAUSED
        self._push(True)

    def play_at(self, index) -> None:
        self.calls.append("play_at")
        self.queue.play_at(index)
        self.queue.version += 1
        self.status, self.position_s = PLAYING, 0.0
        self._push(True)

    def remove(self, index) -> None:
        self.calls.append("remove")
        self.queue.remove(index)
        if not self.queue.tracks:
            self.status = STOPPED
        self._push(True)

    def move(self, source, dest) -> None:
        self.calls.append("move")
        self.queue.move(source, dest)
        self._push(True)

    def play_pause(self) -> None:
        self.calls.append("play_pause")
        if self.queue.tracks:
            self.status = PAUSED if self.status == PLAYING else PLAYING
        self._push()

    def next(self) -> None:
        self.calls.append("next")
        index = self.queue.next_index()
        if index is None:
            self.status = STOPPED
        else:
            self.queue.index = index
            self.queue.version += 1
            self.position_s = 0.0
        self._push(True)

    def previous(self) -> None:
        self.calls.append("previous")
        index = self.queue.previous_index(self.position_s)
        if index is not None and index != self.queue.index:
            self.queue.index = index
            self.queue.version += 1
        self.position_s = 0.0
        self._push(True)

    def seek(self, seconds) -> None:
        self.calls.append("seek")
        self.position_s = seconds
        self._push()

    def set_shuffle(self, on) -> None:
        self.calls.append("set_shuffle")
        self.queue.set_shuffle(on)
        self._push(True)

    def set_repeat(self, mode) -> None:
        self.calls.append("set_repeat")
        self.queue.set_repeat(mode)
        self._push()

    def set_sleep(self, seconds) -> None:
        self.calls.append("set_sleep")
        if seconds == SLEEP_OFF:
            self.sleep = None
        elif seconds == SLEEP_END_OF_SONG:
            self.sleep = {"mode": SLEEP_END_OF_SONG, "remaining_s": None}
        else:
            self.sleep = {"mode": int(seconds), "remaining_s": float(seconds)}
        self._push()

    def rate(self, track_id, rating) -> None:
        self.calls.append("rate")
        if rating == Rating.DISLIKE.value:
            dropped = self.queue.remove_id(track_id)
            if dropped:
                if not self.queue.tracks:
                    self.status = STOPPED
                elif self.status == STOPPED and self.queue.tracks:
                    self.status = PAUSED
                self.position_s = 0.0
                self._push(True)

    def download(self, items) -> None:
        self.calls.append("download")
        self.downloads.extend(items)

    def remove_download(self, item_id) -> None:
        self.calls.append("remove_download")
        self.removed.append(item_id)

    def refresh_cast(self) -> None:
        self.calls.append("refresh_cast")

    def cast_to(self, device_id) -> None:
        self.calls.append("cast_to")

    def stop_casting(self) -> None:
        self.calls.append("stop_casting")

    def set_volume(self, level) -> None:
        self.calls.append("set_volume")


class DbusBackend:
    """The real player service over the session bus (all calls async)."""

    def __init__(self, spec) -> None:
        self.spec = spec
        self.model: PlayerModel | None = None
        self.client = None
        self._conn = None
        self._subs: list[int] = []

    def attach(self, model: PlayerModel) -> None:
        from gi.repository import Gio

        self.model = model
        Gio.bus_get(Gio.BusType.SESSION, None, self._on_bus)

    def _on_bus(self, _source, result) -> None:
        from gi.repository import Gio

        from xx_music.player.client import PlayerClient

        try:
            self._conn = Gio.bus_get_finish(result)
        except Exception:  # noqa: BLE001 - no session bus: stay empty
            return
        self.client = PlayerClient(self._conn, self.spec)
        for member in ("Changed", "QueueChanged", "Problem",
                       "CastChanged"):
            self._subs.append(self._conn.signal_subscribe(
                self.spec.player_bus_name, self.spec.player_interface,
                member, self.spec.player_object_path, None,
                Gio.DBusSignalFlags.NONE, self._on_signal))
        self._probe()

    def _probe(self) -> None:
        """Snapshot only if the service runs: never auto-start it just
        because the window opened (an empty service would idle-exit)."""
        from gi.repository import Gio, GLib

        self._conn.call(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "NameHasOwner",
            GLib.Variant("(s)", (self.spec.player_bus_name,)),
            GLib.VariantType.new("(b)"), Gio.DBusCallFlags.NONE, 3000,
            None, self._on_owner)

    def _on_owner(self, connection, result) -> None:
        try:
            (owned,) = connection.call_finish(result).unpack()
        except Exception:  # noqa: BLE001 - doubt means not running
            owned = False
        if owned:
            self.refresh()

    def refresh(self) -> None:
        if self.client is None:
            return
        self.client.snapshot(self._on_snapshot)

    def _on_snapshot(self, payload, error) -> None:
        if error is not None or payload is None or self.model is None:
            return
        try:
            self.model.apply_snapshot(json.loads(payload))
        except ValueError:
            pass

    def fetch_queue(self) -> None:
        if self.client is None:
            return

        def done(value, error) -> None:
            if error is None and value is not None and self.model:
                version, tracks, index = value
                self.model.apply_queue(version, tracks, index)

        self.client.queue(done)

    def _on_signal(self, _conn, _sender, _path, _iface, member,
                   params) -> None:
        if self.model is None:
            return
        if member == "Changed":
            (payload,) = params.unpack()
            try:
                self.model.apply_snapshot(json.loads(payload))
            except ValueError:
                pass
        elif member == "QueueChanged":
            self.fetch_queue()
        elif member == "Problem":
            self.refresh()
        elif member == "CastChanged":
            self.refresh()

    def _mpris(self, method: str, params=None) -> None:
        from gi.repository import Gio

        if self._conn is None:
            return
        self._conn.call(
            self.spec.mpris_bus_name, MPRIS_PATH, MPRIS_PLAYER, method,
            params, None, Gio.DBusCallFlags.NONE, 10000, None, None)

    # Player1 (auto-starts the service through D-Bus activation)
    def play(self, tracks, start, origin) -> None:
        if self.client:
            self.client.play(tracks, start, origin)

    def start_radio(self, track) -> None:
        if self.client:
            self.client.start_radio(track)

    def enqueue(self, tracks, play_next) -> None:
        if self.client:
            self.client.enqueue(tracks, play_next)

    def play_at(self, index) -> None:
        if self.client:
            self.client.play_at(index)

    def remove(self, index) -> None:
        if self.client:
            self.client.remove(index)

    def move(self, source, dest) -> None:
        if self.client:
            self.client.move(source, dest)

    def set_shuffle(self, on) -> None:
        if self.client:
            self.client.set_shuffle(on)

    def set_repeat(self, mode) -> None:
        if self.client:
            self.client.set_repeat(mode)

    def set_sleep(self, seconds) -> None:
        if self.client:
            self.client.set_sleep(seconds)

    def rate(self, track_id, rating) -> None:
        if self.client:
            self.client.rate(track_id, rating)

    def download(self, items) -> None:
        if self.client:
            self.client.download(items)

    def remove_download(self, item_id) -> None:
        if self.client:
            self.client.remove_download(item_id)

    def refresh_cast(self) -> None:
        if self.client:
            self.client.list_cast_devices(self._on_cast_devices)

    def _on_cast_devices(self, payload, error) -> None:
        if error is None and payload is not None and self.model:
            self.refresh()

    def cast_to(self, device_id) -> None:
        if self.client:
            self.client.cast_to(device_id,
                                lambda *_a: self.refresh())

    def stop_casting(self) -> None:
        if self.client:
            self.client.stop_casting(lambda *_a: self.refresh())

    def set_volume(self, level) -> None:
        from gi.repository import Gio, GLib

        if self._conn is None:
            return
        self._conn.call(
            self.spec.mpris_bus_name, MPRIS_PATH,
            "org.freedesktop.DBus.Properties", "Set",
            GLib.Variant("(ssv)", (MPRIS_PLAYER, "Volume",
                                    GLib.Variant("d", float(level)))),
            None, Gio.DBusCallFlags.NONE, 10000, None, None)

    # MPRIS2 transport
    def play_pause(self) -> None:
        self._mpris("PlayPause")

    def next(self) -> None:
        self._mpris("Next")

    def previous(self) -> None:
        self._mpris("Previous")

    def seek(self, seconds) -> None:
        from gi.repository import GLib

        from xx_music.player.mpris import track_uid

        track = self.model.track if self.model else None
        if track is None:
            return
        self._mpris("SetPosition", GLib.Variant(
            "(ox)", (track_uid(self.spec.player_object_path, track.id),
                     int(seconds * 1e6))))
