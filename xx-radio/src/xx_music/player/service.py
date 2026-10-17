"""The headless player service: queue, GStreamer, MPRIS2, Actions1, Player1.

One GLib main loop for D-Bus and the GStreamer bus watch. Engine calls and
proxy fetches run on a worker pool; results come back via idle_add. D-Bus
handlers only touch state and reply; anything slow is delegated. Playback
URIs always come from the stream proxy's url_for, never from the engine.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gio, GLib  # noqa: E402

from xx_music import policy as P  # noqa: E402
from xx_music.appspec import AppSpec  # noqa: E402
from xx_music.engine import (ERROR_TEXT, RADIO, Engine, EngineError,  # noqa: E402
                             ErrorKind, Rating, Track)
from xx_music.paths import AppPaths  # noqa: E402
from xx_music.player import actions as actions_mod  # noqa: E402
from xx_music.player import api as api_mod  # noqa: E402
from xx_music.player import mpris as mpris_mod  # noqa: E402
from xx_music.player.gst import GstPlayer  # noqa: E402
from xx_music.queue import Queue  # noqa: E402

log = logging.getLogger("xx-music-player")

STATUS_STOPPED = "Stopped"
STATUS_PLAYING = "Playing"
STATUS_PAUSED = "Paused"

TICK_MS = 500
IDLE_CHECK_MS = 5000
CHANGED_COOLDOWN_MS = 250
RADIO_LIMIT = 25
UNKNOWN_METHOD = "org.freedesktop.DBus.Error.UnknownMethod"


def _glib_idle(fn: Callable, *args) -> None:
    GLib.idle_add(lambda: (fn(*args), False)[1])


def _glib_schedule(delay_ms: int, fn: Callable) -> int:
    return GLib.timeout_add(delay_ms, lambda: (fn(), False)[1])


class InlineExecutor:
    def submit(self, fn: Callable, *args) -> None:
        fn(*args)


class PlayerService:
    def __init__(self, spec: AppSpec, engine: Engine, proxy,
                 paths: AppPaths | None = None,
                 clock: Callable[[], float] | None = None,
                 idle_add: Callable | None = None,
                 schedule: Callable | None = None,
                 executor=None, audio_sink=None,
                 audio_sink_name: str = "autoaudiosink",
                 leveling_enabled: bool = False,
                 own_name=None) -> None:
        self.spec = spec
        self.engine = engine
        self.proxy = proxy
        self._own_name = own_name or Gio.bus_own_name_on_connection
        self.paths = paths or spec.paths()
        self.paths.ensure_app_dirs()
        self._clock = clock or time.monotonic
        self._idle_add = idle_add or _glib_idle
        self._schedule = schedule or _glib_schedule
        self._executor = executor or ThreadPoolExecutor(
            max_workers=3, thread_name_prefix="xx-player")
        self._leveling = leveling_enabled
        self._volume = 1.0
        from xx_music.blocked import BlockedArtists, purge_queue

        self.blocked = BlockedArtists(
            self.paths.state_dir() / "blocked-artists.json")
        self.queue = Queue.load(self.paths.queue_path())
        purge_queue(self.queue, self.blocked.is_track_blocked)
        self.status = STATUS_PAUSED if self.queue.tracks else STATUS_STOPPED
        self._sleep = P.SleepTimer(self._clock)
        self._errors = P.ErrorPolicy(self._clock)
        self._problem: dict | None = None
        self._offline = False
        self._heard: dict[str, float] = {}
        self._disliked: set[str] = set()
        self._downloads_version = 0
        self._generation = 0
        self._prefetched: dict[str, str] = {}
        self._reresolved: set[str] = set()
        self._extending = False
        self._position_us = 0
        self._duration_us = 0
        self._last_activity = self._clock()
        self._last_save_key: tuple | None = None
        self._window_connected = False
        self._changed_dirty = False
        self._changed_cooldown = False
        self._conn: Gio.DBusConnection | None = None
        self._loop: GLib.MainLoop | None = None
        self._owner_ids: list[int] = []
        sink = audio_sink
        if sink is None and audio_sink_name != "autoaudiosink":
            from gi.repository import Gst
            sink = Gst.ElementFactory.make(audio_sink_name, None)
        self.player = GstPlayer(
            audio_sink=sink,
            on_eos=lambda: self._idle_add(self._on_eos),
            on_error=lambda msg: self._idle_add(self._on_gst_error, msg),
            on_about_to_finish=self._on_about_to_finish,
        )
        self._save_if_needed(force=True)

    # -- queue controls (main loop) -------------------------------------------
    def _touch(self) -> None:
        self._last_activity = self._clock()

    def play(self, tracks: list[Track], start: int = 0,
             origin: dict | None = None) -> None:
        self.queue.replace(tracks, start, origin)
        self._problem = None
        self._offline = False
        self.status = STATUS_PLAYING if tracks else STATUS_STOPPED
        self._touch()
        self._save_if_needed(force=True)
        self._announce(structural=True)
        self._play_index(self.queue.index)

    def start_radio(self, track: Track) -> None:
        self.play([track], 0, {"type": "radio", "seed": track.id})

    def enqueue(self, tracks: list[Track], play_next: bool = False) -> int:
        added = self.queue.enqueue(tracks, play_next)
        if added:
            self._touch()
            self._save_if_needed()
            self._announce(structural=True)
            if self.status == STATUS_STOPPED:
                self.status = STATUS_PLAYING
                self._play_index(self.queue.index)
        return added

    def play_at(self, index: int) -> None:
        self.queue.play_at(index)
        self.status = STATUS_PLAYING
        self._touch()
        self._save_if_needed(force=True)
        self._announce(structural=True)
        self._play_index(index)

    def remove(self, index: int) -> None:
        current = self.queue.current()
        self.queue.remove(index)
        if not self.queue.tracks:
            self._stop_playback()
        elif current is not None and index == self.queue.index \
                and self.queue.current() is not current:
            self._play_index(self.queue.index)
        self._touch()
        self._save_if_needed(force=True)
        self._announce(structural=True)

    def move(self, source: int, dest: int) -> None:
        self.queue.move(source, dest)
        self._touch()
        self._save_if_needed()
        self._announce(structural=True)

    def set_shuffle(self, on: bool) -> None:
        self.queue.set_shuffle(on)
        self._touch()
        self._save_if_needed()
        self._announce(structural=True)

    def set_repeat(self, mode: str) -> None:
        self.queue.set_repeat(mode)
        self._touch()
        self._save_if_needed()
        self._announce(structural=True)

    def set_sleep(self, seconds: int) -> None:
        self._sleep.arm(seconds)
        self._touch()
        self._announce()

    def rate(self, track_id: str, rating: str) -> None:
        try:
            value = Rating(rating)
        except ValueError:
            raise ValueError(f"bad rating: {rating}") from None
        self._executor.submit(self._rate_work, track_id, value)

    def download(self, items_json: str) -> None:
        items = json.loads(items_json)
        if not isinstance(items, list):
            raise ValueError("download payload must be a JSON list")
        self._downloads_version += 1
        self._emit_downloads_changed()

    def remove_download(self, item_id: str) -> None:
        if not item_id:
            raise ValueError("download id must not be empty")
        self._downloads_version += 1
        self._emit_downloads_changed()

    # -- transport (MPRIS drives these) -----------------------------------------
    def pause(self) -> None:
        if self.status == STATUS_PLAYING:
            self.status = STATUS_PAUSED
            self.player.pause()
            self._touch()
            self._save_if_needed(force=True)
            self._announce()

    def resume(self) -> None:
        if self.queue.tracks and self.status != STATUS_PLAYING:
            self.status = STATUS_PLAYING
            self.player.play()
            self._touch()
            self._announce()

    def stop(self) -> None:
        self._stop_playback()

    def play_pause(self) -> None:
        if self.status == STATUS_PLAYING:
            self.pause()
        else:
            self.resume()

    def next(self) -> None:
        index = self.queue.next_index()
        if index is None:
            self._stop_playback()
            return
        self.queue.index = index
        self.queue.version += 1
        if self.status == STATUS_STOPPED:
            self.status = STATUS_PLAYING
        self._touch()
        self._save_if_needed(force=True)
        self._announce(structural=True)
        self._play_index(index)

    def previous(self) -> None:
        position_s = self._position_us / 1e6
        index = self.queue.previous_index(position_s)
        if index is None:
            return
        if index == self.queue.index:
            self.seek_us(0)
            return
        self.queue.index = index
        self.queue.version += 1
        if self.status == STATUS_STOPPED:
            self.status = STATUS_PLAYING
        self._touch()
        self._save_if_needed(force=True)
        self._announce(structural=True)
        self._play_index(index)

    def seek_us(self, position_us: int) -> None:
        self.player.seek_ns(max(0, int(position_us)) * 1000)
        self._position_us = max(0, int(position_us))
        self._emit_seeked(self._position_us)
        self._announce()

    # -- snapshots ---------------------------------------------------------------
    def heard_ids(self) -> set[str]:
        now = self._clock()
        return {tid for tid, at in self._heard.items()
                if now - at < P.HEARD_EXCLUDE_S}

    def minimal_mode(self) -> bool:
        """The window's Minimal Mode (``config.json``): no art at all,
        the system's media controls included."""
        try:
            raw = json.loads(self.paths.config_path().read_text("utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(raw, dict) and bool(raw.get("minimal_mode"))

    def art_url_for(self, track: Track | None) -> str:
        if track is None or self.minimal_mode():
            return ""
        if track.art_url.startswith("file://"):
            return track.art_url
        cached = self.paths.art_cache_dir() / track.id
        if cached.is_file():
            return cached.as_uri()
        return ""

    def snapshot_dict(self) -> dict:
        track = self.queue.current()
        sleep = None
        if self._sleep.armed:
            sleep = {"mode": self._sleep.mode,
                     "remaining_s": self._sleep.remaining()}
        return {
            "status": self.status,
            "track": track.to_dict() if track else None,
            "position_s": self._position_us / 1e6,
            "duration_s": self._duration_us / 1e6,
            "index": self.queue.index,
            "length": len(self.queue.tracks),
            "version": self.queue.version,
            "shuffle": self.queue.shuffle,
            "repeat": self.queue.repeat,
            "sleep": sleep,
            "problem": self._problem,
            "origin": self.queue.origin,
            "offline": self._offline,
        }

    def snapshot_json(self) -> str:
        return json.dumps(self.snapshot_dict())

    def queue_state(self) -> tuple[int, str, int]:
        return (self.queue.version,
                api_mod.tracks_to_json(self.queue.tracks), self.queue.index)

    # -- playback engine ----------------------------------------------------------
    def _stop_playback(self) -> None:
        self._generation += 1
        self.status = STATUS_STOPPED
        self.player.stop()
        self._position_us = 0
        self._touch()
        self._save_if_needed(force=True)
        self._announce()

    def _play_index(self, index: int) -> None:
        track = self.queue.current()
        if track is None or index < 0:
            if self.status != STATUS_STOPPED:
                self._stop_playback()
            return
        self._generation += 1
        generation = self._generation
        uri = self._prefetched.pop(track.id, "")
        if uri:
            self._uri_ready(generation, index, track.id, uri, None)
        else:
            self._executor.submit(self._resolve_work, generation, index,
                                  track.id)

    def _resolve_work(self, generation: int, index: int, track_id: str
                      ) -> None:
        captured: dict = {}

        def resolve():
            source = self.engine.resolve(track_id)
            captured["gain_db"] = source.gain_db
            return source

        try:
            uri = self.proxy.url_for(track_id, resolve)
        except EngineError as exc:
            self._idle_add(self._resolve_failed, generation, index,
                           track_id, exc)
        except Exception as exc:  # noqa: BLE001 - engine bugs pause, not crash
            log.warning("resolve %s failed: %s", track_id, exc)
            self._idle_add(self._resolve_failed, generation, index,
                           track_id, EngineError(ErrorKind.OTHER, str(exc)))
        else:
            self._idle_add(self._uri_ready, generation, index, track_id,
                           uri, captured.get("gain_db"))

    def _uri_ready(self, generation: int, index: int, track_id: str,
                   uri: str, gain_db: float | None) -> None:
        if generation != self._generation:
            return
        track = self.queue.current()
        if track is None or track.id != track_id \
                or index != self.queue.index:
            return
        if not uri:
            self._resolve_failed(
                generation, index, track_id,
                EngineError(ErrorKind.NOT_AVAILABLE, track_id))
            return
        self.player.load(uri)
        if self._leveling and gain_db:
            try:
                self.player.set_volume(10.0 ** (float(gain_db) / 20.0))
            except (TypeError, ValueError):
                pass
        if self.status == STATUS_PLAYING:
            self.player.play()
        else:
            self.player.pause()
        self._heard[track_id] = self._clock()
        self._errors.note_playing()
        self._problem = None
        self._reresolved.discard(track_id)
        self._apply_fade_volume()
        self._prefetch_next()
        self._extend_radio_if_needed()
        self._touch()
        self._save_if_needed()
        self._announce()

    def _resolve_failed(self, generation: int, index: int, track_id: str,
                        error: EngineError) -> None:
        if generation != self._generation:
            return
        track = self.queue.current()
        if track is None or track.id != track_id:
            return
        action = self._errors.decide(error, track_id)
        self._apply_error_action(action, index, track, error)

    def _apply_error_action(self, action: P.ErrorAction, index: int,
                            track: Track, error: EngineError) -> None:
        kind = action.action
        if kind == "retry":
            self._schedule(int((action.delay_s or 0.5) * 1000),
                           lambda: self._retry_current(index, track.id))
        elif kind == "rate-retry":
            self._schedule(int((action.delay_s or 60.0) * 1000),
                           lambda: self._retry_current(index, track.id))
        elif kind == "reresolve":
            self._reresolved.add(track.id)
            self._play_index(index)
        elif kind == "drop-next":
            self.queue.remove(index)
            self._save_if_needed(force=True)
            if not self.queue.tracks:
                self._set_problem(error.kind, error.user_text)
                self._stop_playback()
            else:
                self.queue.index = min(index, len(self.queue.tracks) - 1)
                self._announce(structural=True)
                self._play_index(self.queue.index)
        elif kind == "skip":
            if P.ErrorPolicy().skip_or_pause(
                    len(self.queue.tracks)) == "pause":
                self._set_problem(error.kind, error.user_text)
                self.pause()
            else:
                self.next()
        elif kind == "offline-mix":
            self._switch_to_offline_mix()
        else:
            self._set_problem(error.kind, error.user_text)
            self.pause()

    def _retry_current(self, index: int, track_id: str) -> None:
        track = self.queue.current()
        if track is not None and track.id == track_id \
                and index == self.queue.index \
                and self.status == STATUS_PLAYING:
            self._play_index(index)

    def _on_eos(self) -> None:
        if self.status == STATUS_STOPPED:
            return
        index = self.queue.next_index()
        if index is None:
            self._stop_playback()
            return
        self.queue.index = index
        self.queue.version += 1
        self._save_if_needed(force=True)
        self._announce(structural=True)
        self._play_index(index)

    def _on_gst_error(self, message: str) -> None:
        track = self.queue.current()
        if track is None or self.status == STATUS_STOPPED:
            return
        log.warning("playback error on %s: %s", track.id, message)
        self._apply_error_action(
            self._errors.decode_error(track.id), self.queue.index, track,
            EngineError(ErrorKind.OTHER, message))

    def _on_about_to_finish(self) -> None:
        index = self.queue.next_index()
        if index is None:
            return
        track = self.queue.tracks[index]
        uri = self._prefetched.get(track.id, "")
        if uri:
            self.player.set_next_uri(uri)

    def _prefetch_next(self) -> None:
        index = self.queue.next_index()
        if index is None:
            return
        track = self.queue.tracks[index]
        if track.id in self._prefetched:
            return
        self._executor.submit(self._prefetch_work, self._generation,
                              track.id)

    def _prefetch_work(self, generation: int, track_id: str) -> None:
        try:
            uri = self.proxy.url_for(
                track_id, lambda: self.engine.resolve(track_id))
        except Exception as exc:  # noqa: BLE001 - prefetch never interrupts
            log.debug("prefetch %s failed: %s", track_id, exc)
            return
        self._idle_add(self._prefetch_ready, generation, track_id, uri)

    def _prefetch_ready(self, generation: int, track_id: str, uri: str
                        ) -> None:
        if generation == self._generation and uri:
            self._prefetched[track_id] = uri
            self._on_about_to_finish()

    def _extend_radio_if_needed(self) -> None:
        origin = self.queue.origin
        if origin.get("type") != "radio" or not origin.get("seed"):
            return
        if not P.should_extend_radio(self.queue.remaining()):
            return
        if self._extending:
            return
        if RADIO not in self.engine.capabilities:
            return
        self._extending = True
        self._executor.submit(self._radio_work, self._generation,
                              str(origin["seed"]))

    def _radio_work(self, generation: int, seed: str) -> None:
        try:
            self.blocked.refresh()
            kept = P.fetch_radio_filtered(
                self.engine, seed, self.blocked, RADIO_LIMIT,
                self.queue.ids() | self.heard_ids(),
                self.queue.ids(), self.heard_ids(), self._disliked)
        except Exception as exc:  # noqa: BLE001 - radio stays finite then
            log.debug("radio extension failed: %s", exc)
            self._idle_add(self._radio_done, generation, [])
        else:
            self._idle_add(self._radio_done, generation, kept)

    def _radio_done(self, generation: int, tracks: list[Track]) -> None:
        self._extending = False
        if generation != self._generation or not tracks:
            return
        self.queue.enqueue(tracks)
        self._save_if_needed()
        self._announce(structural=True)

    def _rate_work(self, track_id: str, rating: Rating) -> None:
        try:
            self.engine.rate(track_id, rating)
        except Exception as exc:  # noqa: BLE001 - rating failure is a note
            log.debug("rate %s failed: %s", track_id, exc)
            return
        self._idle_add(self._rate_done, track_id, rating)

    def _rate_done(self, track_id: str, rating: Rating) -> None:
        if rating == Rating.DISLIKE:
            self._disliked.add(track_id)
            dropped = self.queue.remove_id(track_id)
            if dropped:
                self._save_if_needed(force=True)
                self._announce(structural=True)
                if self.queue.current() is None:
                    self._stop_playback()
        elif rating == Rating.LIKE:
            self._disliked.discard(track_id)
        else:
            self._disliked.discard(track_id)
        self._emit_properties_changed(mpris_mod.PLAYER_IFACE, {})
        self._announce()

    def _switch_to_offline_mix(self) -> None:
        self._errors.note_offline_mix()
        cached = [t for t in self.queue.tracks
                  if self._is_usable_offline(t.id)]
        if not cached:
            self._set_problem(ErrorKind.OFFLINE,
                              ERROR_TEXT[ErrorKind.OFFLINE])
            self.pause()
            return
        current = self.queue.current()
        keep = ([current] if current and self._is_usable_offline(current.id)
                else []) + [t for t in cached
                             if current is None or t.id != current.id]
        self.queue.tracks = keep[:P.OFFLINE_MIX_LIMIT + 1]
        self.queue.index = 0 if self.queue.tracks else -1
        self.queue.version += 1
        self._offline = True
        self._save_if_needed(force=True)
        self._announce(structural=True)
        self._play_index(self.queue.index)

    def _is_usable_offline(self, track_id: str) -> bool:
        try:
            return bool(self.proxy.is_cached(track_id))
        except Exception:  # noqa: BLE001 - cache doubt means online behavior
            return False

    def _set_problem(self, kind: ErrorKind, text: str) -> None:
        self._problem = {"kind": str(kind.value), "text": text}
        if self._conn is not None:
            self._conn.emit_signal(
                None, self.spec.player_object_path,
                self.spec.player_interface, "Problem",
                GLib.Variant("(ss)", (str(kind.value), text)))
        self._announce()

    def _apply_fade_volume(self) -> None:
        self.player.set_volume(self._volume * self._sleep.fade_scale())

    # -- tick: position, sleep, persistence (main loop, non-blocking) ------------
    def _tick(self) -> None:
        position = self.player.position_ns()
        duration = self.player.duration_ns()
        if position is not None:
            self._position_us = position // 1000
        if duration is not None and duration > 0:
            self._duration_us = duration // 1000
        elif self.queue.current() and self.queue.current().duration_s:
            self._duration_us = int(
                self.queue.current().duration_s * 1e6)
        if self.status == STATUS_PLAYING:
            if self._sleep.expired():
                self._sleep.arm(P.SLEEP_OFF)
                self.pause()
            elif self._sleep.end_of_song_pause(
                    self._position_us / 1e6, self._duration_us / 1e6):
                self._sleep.arm(P.SLEEP_OFF)
                self.pause()
            elif self._sleep.armed:
                self._apply_fade_volume()
        self._save_if_needed()
        self._schedule(TICK_MS, self._tick)

    def _save_if_needed(self, force: bool = False) -> None:
        key = (self.queue.persistence_key(), self.status,
               int(self._position_us // 5_000_000))
        if force or key != self._last_save_key:
            self._last_save_key = key
            try:
                self.queue.save(self.paths.queue_path())
            except OSError as exc:
                log.warning("queue save failed: %s", exc)

    # -- Changed coalescing (at most 4 per second) ---------------------------------
    def _announce(self, structural: bool = False) -> None:
        if structural:
            self._emit_queue_changed()
        self._changed_dirty = True
        if self._changed_cooldown:
            return
        self._emit_changed_now()
        self._changed_cooldown = True
        self._schedule(CHANGED_COOLDOWN_MS, self._changed_cooldown_done)

    def _changed_cooldown_done(self) -> None:
        self._changed_cooldown = False
        if self._changed_dirty:
            self._emit_changed_now()

    def _emit_changed_now(self) -> None:
        self._changed_dirty = False
        if self._conn is None:
            return
        self._conn.emit_signal(
            None, self.spec.player_object_path, self.spec.player_interface,
            "Changed", GLib.Variant("(s)", (self.snapshot_json(),)))
        self._emit_properties_changed(mpris_mod.PLAYER_IFACE, {})
        self._emit_properties_changed(mpris_mod.ROOT_IFACE, {})

    def _emit_queue_changed(self) -> None:
        if self._conn is None:
            return
        self._conn.emit_signal(
            None, self.spec.player_object_path, self.spec.player_interface,
            "QueueChanged", GLib.Variant("(u)", (self.queue.version,)))

    def _emit_downloads_changed(self) -> None:
        if self._conn is None:
            return
        self._conn.emit_signal(
            None, self.spec.player_object_path, self.spec.player_interface,
            "DownloadsChanged",
            GLib.Variant("(u)", (self._downloads_version,)))

    def _emit_seeked(self, position_us: int) -> None:
        if self._conn is None:
            return
        self._conn.emit_signal(
            None, mpris_mod.MPRIS_PATH, mpris_mod.PLAYER_IFACE, "Seeked",
            GLib.Variant("(x)", (int(position_us),)))

    def _emit_properties_changed(self, interface: str, changed: dict) -> None:
        if self._conn is None:
            return
        if interface == mpris_mod.PLAYER_IFACE and not changed:
            changed = self._player_props()
        elif interface == mpris_mod.ROOT_IFACE and not changed:
            changed = mpris_mod.root_props(
                self.spec.name, self.spec.desktop_entry)
        self._conn.emit_signal(
            None, mpris_mod.MPRIS_PATH, mpris_mod.PROPS_IFACE,
            "PropertiesChanged", mpris_mod.changed_signal(
                interface, changed))

    # -- MPRIS property view ----------------------------------------------------------
    def _player_props(self) -> dict:
        track = self.queue.current()
        position = self.player.position_ns()
        if position is not None:
            self._position_us = position // 1000
        duration = self.player.duration_ns()
        if duration is not None and duration > 0:
            self._duration_us = duration // 1000
        elif track is not None and track.duration_s:
            self._duration_us = int(track.duration_s * 1e6)
        else:
            self._duration_us = 0
        return mpris_mod.player_props(
            self.status, self.queue.repeat, self.queue.shuffle,
            self._volume, self._position_us,
            mpris_mod.metadata_for(
                self.spec.player_object_path, track, self._duration_us,
                self.art_url_for(track)),
            can_next=self.queue.next_index() is not None,
            can_prev=self.queue.index > 0 or self.queue.repeat == "all",
            can_play=bool(self.queue.tracks),
            can_pause=self.status == STATUS_PLAYING,
            can_seek=self._duration_us > 0)

    # -- D-Bus: Player1 --------------------------------------------------------------
    def _reply(self, invocation, variant) -> None:
        try:
            invocation.return_value(variant)
        except Exception as exc:  # noqa: BLE001 - a gone caller is not fatal
            log.debug("reply failed: %s", exc)

    def _reject(self, invocation, message: str) -> None:
        try:
            invocation.return_dbus_error(api_mod.INVALID_ARGS, message)
        except Exception as exc:  # noqa: BLE001 - a gone caller is not fatal
            log.debug("error reply failed: %s", exc)

    def _on_player1_call(self, _connection, _sender, _path, _iface,
                         method, params, invocation) -> None:
        try:
            args = params.unpack() if params is not None else ()
        except Exception as exc:
            self._reject(invocation, f"cannot unpack args: {exc}")
            return
        try:
            if method == "Play":
                tracks_json, start, origin_json = args
                self.play(api_mod.tracks_from_json(tracks_json), start,
                          api_mod.origin_from_json(origin_json))
            elif method == "StartRadio":
                (payload,) = args
                self.start_radio(api_mod.track_from_json(payload))
            elif method == "Enqueue":
                tracks_json, play_next = args
                self.enqueue(api_mod.tracks_from_json(tracks_json),
                             bool(play_next))
            elif method == "PlayAt":
                (index,) = args
                self.play_at(int(index))
            elif method == "Remove":
                (index,) = args
                self.remove(int(index))
            elif method == "Move":
                source, dest = args
                self.move(int(source), int(dest))
            elif method == "SetShuffle":
                (on,) = args
                self.set_shuffle(bool(on))
            elif method == "SetRepeat":
                (mode,) = args
                self.set_repeat(str(mode))
            elif method == "SetSleep":
                (seconds,) = args
                self.set_sleep(int(seconds))
            elif method == "Rate":
                track_id, rating = args
                self.rate(str(track_id), str(rating))
            elif method == "Download":
                (items_json,) = args
                self.download(str(items_json))
            elif method == "RemoveDownload":
                (item_id,) = args
                self.remove_download(str(item_id))
            elif method == "Snapshot":
                self._reply(invocation,
                            GLib.Variant("(s)", (self.snapshot_json(),)))
                return
            elif method == "Queue":
                version, payload, index = self.queue_state()
                self._reply(invocation, GLib.Variant(
                    "(usi)", (version, payload, index)))
                return
            else:
                try:
                    invocation.return_dbus_error(UNKNOWN_METHOD, method)
                except Exception:  # noqa: BLE001 - gone caller
                    pass
                return
        except (ValueError, IndexError, KeyError, TypeError) as exc:
            self._reject(invocation, f"bad arguments for {method}: {exc}")
            return
        self._reply(invocation, None)

    # -- D-Bus: MPRIS2 ------------------------------------------------------------------
    def _on_mpris_call(self, _connection, _sender, _path, iface,
                       method, params, invocation) -> None:
        try:
            args = params.unpack() if params is not None else ()
        except Exception as exc:
            self._reject(invocation, f"cannot unpack args: {exc}")
            return
        if iface == mpris_mod.PROPS_IFACE:
            self._handle_props(method, args, invocation)
            return
        if iface == mpris_mod.ROOT_IFACE:
            if method == "Raise":
                self._raise_window()
            elif method == "Quit":
                self.request_stop()
            else:
                self._unknown(invocation, method)
                return
            self._reply(invocation, None)
            return
        if iface == mpris_mod.PLAYER_IFACE:
            if not self._handle_player_method(method, args, invocation):
                self._unknown(invocation, method)
            return
        self._unknown(invocation, method)

    def _unknown(self, invocation, method: str) -> None:
        try:
            invocation.return_dbus_error(UNKNOWN_METHOD, method)
        except Exception:  # noqa: BLE001 - gone caller
            pass

    def _handle_player_method(self, method: str, args: tuple, invocation
                              ) -> bool:
        if method == "Next":
            self.next()
        elif method == "Previous":
            self.previous()
        elif method == "Pause":
            self.pause()
        elif method == "PlayPause":
            self.play_pause()
        elif method == "Play":
            self.resume()
        elif method == "Stop":
            self.stop()
        elif method == "Seek":
            (offset,) = args
            self.seek_us(self._position_us + int(offset))
        elif method == "SetPosition":
            _track_uid, position = args
            self.seek_us(int(position))
        elif method == "OpenUri":
            (uri,) = args
            if not self._open_uri(str(uri)):
                self._reject(invocation, f"no queued song at {uri}")
                return True
        else:
            return False
        self._reply(invocation, None)
        return True

    def _open_uri(self, uri: str) -> bool:
        for index, track in enumerate(self.queue.tracks):
            if self._prefetched.get(track.id) == uri:
                self.play_at(index)
                return True
        if self.queue.current() and self.player.uri == uri:
            self.resume()
            return True
        return False

    def _handle_props(self, method: str, args: tuple, invocation) -> None:
        if method == "GetAll":
            (iface,) = args
            props = self._props_for(iface)
            if props is None:
                self._unknown(invocation, iface)
                return
            self._reply(invocation, GLib.Variant("(a{sv})", (props,)))
        elif method == "Get":
            iface, name = args
            props = self._props_for(iface)
            if props is None or name not in props:
                self._unknown(invocation, str(name))
                return
            self._reply(invocation,
                        GLib.Variant("(v)", (props[name],)))
        elif method == "Set":
            iface, name, value = args
            if not self._set_prop(iface, str(name), value):
                self._unknown(invocation, str(name))
                return
            self._reply(invocation, None)
        else:
            self._unknown(invocation, method)

    def _props_for(self, iface: str) -> dict | None:
        if iface == mpris_mod.ROOT_IFACE:
            return mpris_mod.root_props(
                self.spec.name, self.spec.desktop_entry)
        if iface == mpris_mod.PLAYER_IFACE:
            return self._player_props()
        return None

    def _set_prop(self, iface: str, name: str, value) -> bool:
        if iface != mpris_mod.PLAYER_IFACE:
            return False
        try:
            plain = value.unpack() if hasattr(value, "unpack") else value
        except Exception:
            return False
        if name == "Volume":
            self._volume = min(1.0, max(0.0, float(plain)))
            self._apply_fade_volume()
            self._announce()
            return True
        if name == "Shuffle":
            self.set_shuffle(bool(plain))
            return True
        if name == "LoopStatus":
            repeat = mpris_mod.REPEAT_FOR_LOOP.get(str(plain))
            if repeat is None:
                return False
            self.set_repeat(repeat)
            return True
        return False

    def _raise_window(self) -> None:
        if self._conn is None:
            return
        self._conn.call(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "StartServiceByName",
            GLib.Variant("(su)", (self.spec.app_id, 0)),
            GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE,
            5000, None, None)

    # -- D-Bus: Actions1 ------------------------------------------------------------------
    def _on_actions_call(self, _connection, _sender, _path, _iface,
                         method, params, invocation) -> None:
        try:
            args = params.unpack() if params is not None else ()
        except Exception as exc:
            self._reject(invocation, f"cannot unpack args: {exc}")
            return
        if method == "ListActions":
            rows = actions_mod.action_rows(
                self.spec, self.engine, self.queue.current())
            self._reply(invocation,
                        GLib.Variant("(a(sssb))", (rows,)))
        elif method == "ActivateAction":
            (action_id,) = args
            self._activate_action(str(action_id), invocation)
        else:
            self._unknown(invocation, method)

    def _activate_action(self, action_id: str, invocation) -> None:
        found = next((a for a in self.spec.player_actions
                      if a.id == action_id), None)
        track = self.queue.current()
        if found is None or not found.enabled(self.engine, track):
            self._reject(invocation, f"no action {action_id} here")
            return
        self._reply(invocation, None)
        self._executor.submit(self._action_work, found, track)

    def _action_work(self, action, track: Track | None) -> None:
        try:
            action.handler(self.engine, track)
        except Exception as exc:  # noqa: BLE001 - a failing thumb is a note
            log.debug("player action failed: %s", exc)
            return
        self._idle_add(self._emit_properties_changed,
                       mpris_mod.PLAYER_IFACE, {})
        self._idle_add(self._announce)

    # -- bus lifetime --------------------------------------------------------------------------
    def attach(self, connection: Gio.DBusConnection) -> None:
        self._conn = connection
        mpris_node = Gio.DBusNodeInfo.new_for_xml(mpris_mod.MPRIS_XML)
        for iface in mpris_node.interfaces:
            connection.register_object(
                mpris_mod.MPRIS_PATH, iface, self._on_mpris_call)
        actions_node = Gio.DBusNodeInfo.new_for_xml(actions_mod.ACTIONS_XML)
        for iface in actions_node.interfaces:
            connection.register_object(
                actions_mod.ACTIONS_PATH, iface, self._on_actions_call)
        player_node = Gio.DBusNodeInfo.new_for_xml(
            api_mod.player_xml(self.spec))
        for iface in player_node.interfaces:
            connection.register_object(
                self.spec.player_object_path, iface, self._on_player1_call)
        for name in (self.spec.player_bus_name, self.spec.mpris_bus_name):
            self._owner_ids.append(
                self._own_name(
                    connection, name, Gio.BusNameOwnerFlags.NONE,
                    None, None))

    def start_timers(self) -> None:
        self._schedule(TICK_MS, self._tick)
        self._schedule(IDLE_CHECK_MS, self._idle_poll)

    def check_idle(self, window_connected: bool, now: float | None = None
                   ) -> str | None:
        return P.idle_action(self.status, len(self.queue.tracks),
                             now - self._last_activity
                             if now is not None else
                             self._clock() - self._last_activity,
                             window_connected)

    def _idle_poll(self) -> None:
        if self._conn is None:
            self._schedule(IDLE_CHECK_MS, self._idle_poll)
            return
        self._conn.call(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "NameHasOwner",
            GLib.Variant("(s)", (self.spec.app_id,)),
            GLib.VariantType.new("(b)"), Gio.DBusCallFlags.NONE,
            2000, None, self._on_owner_result)

    def _on_owner_result(self, connection, result) -> None:
        try:
            reply = connection.call_finish(result)
            (owned,) = reply.unpack()
        except Exception:  # noqa: BLE001 - doubt means the window is gone
            owned = False
        self._window_connected = bool(owned)
        action = self.check_idle(bool(owned))
        if action is not None:
            self._save_if_needed(force=True)
            self.request_stop()
        else:
            self._schedule(IDLE_CHECK_MS, self._idle_poll)

    def run(self) -> int:
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.attach(connection)
        self.start_timers()
        self._loop = GLib.MainLoop()
        self._loop.run()
        return 0

    def request_stop(self) -> None:
        self._save_if_needed(force=True)
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except Exception:  # noqa: BLE001 - stopping must not raise
            pass
        try:
            self.player.shutdown()
        except Exception:  # noqa: BLE001 - stopping must not raise
            pass
        if self._loop is not None:
            self._loop.quit()


def build_proxy(spec: AppSpec, cache_dir=None):
    """The loopback range proxy (radio_m2's StreamProxy, exact contract)."""
    from xx_music.streamproxy import StreamProxy
    proxy = StreamProxy(cache_dir or spec.paths().cache_dir() / "audio")
    proxy.start()
    return proxy


def main(spec: AppSpec) -> int:
    engine = spec.make_engine("player")
    try:
        proxy = build_proxy(spec)
    except ImportError:
        raise SystemExit(
            "stream proxy is not installed yet; it lands with radio_m2"
        ) from None
    service = PlayerService(spec, engine, proxy)
    try:
        return service.run()
    finally:
        try:
            proxy.stop()
        except Exception:  # noqa: BLE001 - stopping must not raise
            pass

