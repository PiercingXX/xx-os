"""Background player service: own bus name, MPRIS2, playbin (M2).

Owns ``io.piercingxx.XXAudiobook.Player`` (Player1 UI surface) and
``org.mpris.MediaPlayer2.xx_audiobook`` (MPRIS2 + the optional
``io.piercingxx.XXAuto.Actions1`` custom actions) at
``/org/mpris/MediaPlayer2``. Never the GUI app id, so the window always
starts while this runs. Headless: no Gtk/Adw imports here.

Writes go through M1's ProgressRepository; libsecret stays out — the GUI
hands the bearer to SetSession (memory only, never logged) and clears it
with ClearSession. Blocking API calls run on one worker thread; the
playbin stays on the main thread. Idle-exits after IDLE_EXIT_SECONDS
with no book (the activation file makes that safe).

Player1 UI contract (methods on /io/piercingxx/XXAudiobook/Player):
Open(item), Play, Pause, PlayPause, Stop, SeekTo(us), SeekRelative(us),
SkipBack, SkipForward, NextChapter, PreviousChapter, SetRate, SetVolume,
Sleep(ms), SleepEndOfChapter, CancelSleep, SetSession(api_base, bearer),
ClearSession, GetState() -> a{sv}, CheckConflict(item) -> a{sv},
ResolveConflict(item, use_server). Signals: StateChanged(a{sv}) on real
changes, BookmarkRequested(item, position_us) for the bookmark action.
"""

from __future__ import annotations

import os
import queue
import threading
import time

from xx_audiobook import (
    __mpris_bus_name__ as MPRIS_BUS,
    __player_bus_name__ as PLAYER_BUS,
)
from xx_audiobook import api as api_module
from xx_audiobook import player as player_module
from xx_audiobook import prefs as prefs_module
from xx_audiobook import repos as repos_module
from xx_audiobook import sleep as sleep_module
from xx_audiobook import store as store_module
from xx_audiobook import stream_auth as stream_auth_module
from xx_audiobook import sync as sync_module
from xx_audiobook import timeline as timeline_module

OBJECT_PATH = "/io/piercingxx/XXAudiobook/Player"
PLAYER_INTERFACE = "io.piercingxx.XXAudiobook.Player1"
MPRIS_PATH = "/org/mpris/MediaPlayer2"
MPRIS_ROOT = "org.mpris.MediaPlayer2"
MPRIS_PLAYER = "org.mpris.MediaPlayer2.Player"
PROPS_INTERFACE = "org.freedesktop.DBus.Properties"
ACTIONS_INTERFACE = "io.piercingxx.XXAuto.Actions1"
DESKTOP_ENTRY = "io.piercingxx.XXAudiobook"
IDENTITY = "xx-audiobook"

ERROR = PLAYER_INTERFACE + ".Error"

IDLE_EXIT_SECONDS = 60.0
IDLE_ENV_VAR = "XX_AUDIOBOOK_PLAYER_IDLE_TIMEOUT"

ACTION_SKIP_BACK = "skip-back"
ACTION_BOOKMARK = "bookmark"
ACTION_SKIP_FORWARD = "skip-forward"

MPRIS_XML = """\
<node>
  <interface name="org.mpris.MediaPlayer2">
    <method name="Raise"/>
    <method name="Quit"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="Identity" type="s" access="read"/>
    <property name="DesktopEntry" type="s" access="read"/>
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
    <method name="Seek"><arg direction="in" type="x" name="Offset"/></method>
    <method name="SetPosition"><arg direction="in" type="o" name="TrackId"/>
      <arg direction="in" type="x" name="Position"/></method>
    <method name="OpenUri"><arg direction="in" type="s" name="Uri"/></method>
    <signal name="Seeked"><arg type="x" name="Position"/></signal>
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
  </interface>
  <interface name="org.freedesktop.DBus.Properties">
    <method name="Get"><arg direction="in" type="s" name="interface"/>
      <arg direction="in" type="s" name="property"/>
      <arg direction="out" type="v" name="value"/></method>
    <method name="Set"><arg direction="in" type="s" name="interface"/>
      <arg direction="in" type="s" name="property"/>
      <arg direction="in" type="v" name="value"/></method>
    <method name="GetAll"><arg direction="in" type="s" name="interface"/>
      <arg direction="out" type="a{sv}" name="properties"/></method>
  </interface>
  <interface name="io.piercingxx.XXAuto.Actions1">
    <method name="ListActions">
      <arg direction="out" type="a(sssb)" name="actions"/></method>
    <method name="ActivateAction">
      <arg direction="in" type="s" name="action_id"/></method>
  </interface>
</node>
"""

PLAYER_XML = """\
<node>
  <interface name="io.piercingxx.XXAudiobook.Player1">
    <method name="Open"><arg direction="in" type="s" name="item_id"/></method>
    <method name="Play"/>
    <method name="Pause"/>
    <method name="PlayPause"/>
    <method name="Stop"/>
    <method name="SeekTo"><arg direction="in" type="x" name="position_us"/></method>
    <method name="SeekRelative"><arg direction="in" type="x" name="offset_us"/></method>
    <method name="SkipBack"/>
    <method name="SkipForward"/>
    <method name="NextChapter"/>
    <method name="PreviousChapter"/>
    <method name="SetRate"><arg direction="in" type="d" name="rate"/></method>
    <method name="SetVolume"><arg direction="in" type="d" name="volume"/></method>
    <method name="Sleep"><arg direction="in" type="x" name="duration_ms"/></method>
    <method name="SleepEndOfChapter"/>
    <method name="CancelSleep"/>
    <method name="SetSession"><arg direction="in" type="s" name="api_base"/>
      <arg direction="in" type="s" name="bearer"/></method>
    <method name="ClearSession"/>
    <method name="GetState"><arg direction="out" type="a{sv}" name="state"/></method>
    <method name="CheckConflict"><arg direction="in" type="s" name="item_id"/>
      <arg direction="out" type="a{sv}" name="conflict"/></method>
    <method name="ResolveConflict"><arg direction="in" type="s" name="item_id"/>
      <arg direction="in" type="b" name="use_server"/></method>
    <signal name="StateChanged"><arg type="a{sv}" name="state"/></signal>
    <signal name="BookmarkRequested"><arg type="s" name="item_id"/>
      <arg type="x" name="position_us"/></signal>
  </interface>
  <interface name="org.freedesktop.DBus.Properties">
    <method name="Get"><arg direction="in" type="s" name="interface"/>
      <arg direction="in" type="s" name="property"/>
      <arg direction="out" type="v" name="value"/></method>
    <method name="Set"><arg direction="in" type="s" name="interface"/>
      <arg direction="in" type="s" name="property"/>
      <arg direction="in" type="v" name="value"/></method>
    <method name="GetAll"><arg direction="in" type="s" name="interface"/>
      <arg direction="out" type="a{sv}" name="properties"/></method>
  </interface>
</node>
"""


def resolve_idle_timeout(explicit: float | None = None) -> float:
    if explicit is not None:
        return float(explicit)
    raw = os.environ.get(IDLE_ENV_VAR, "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return IDLE_EXIT_SECONDS


def _monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


def _wall_ms() -> int:
    return int(time.time() * 1000)


class ServiceError(Exception):
    pass


class _SleepTarget(sleep_module.SleepTarget):
    def __init__(self, service: PlayerService) -> None:
        self._service = service

    def set_volume(self, volume: float) -> None:
        self._service.engine.set_volume(volume)

    def pause(self) -> None:
        self._service.pause_for_sleep()

    def position_ms(self) -> int:
        return self._service.engine.position_global_ms()

    def chapter_end_ms(self) -> int:
        book = self._service.engine.loaded
        if book is None:
            return 0
        return book.timeline.chapter_end_at(
            self._service.engine.position_global_ms())

    def speed(self) -> float:
        return self._service.engine.rate

    def is_playing(self) -> bool:
        return (self._service.engine.status
                == player_module.STATUS_PLAYING)


class _SyncEnv(sync_module.SyncEnv):
    def __init__(self, service: PlayerService) -> None:
        self._service = service

    def item_id(self) -> str | None:
        book = self._service.engine.loaded
        return book.item_id if book is not None else None

    def is_playing(self) -> bool:
        return (self._service.engine.status
                == player_module.STATUS_PLAYING)

    def position_ms(self) -> int:
        return self._service.engine.position_global_ms()

    def chapter_index(self) -> int:
        return self._service.engine.chapter_index()

    def screen_on(self) -> bool:
        return self._service.screen_on

    def power_save(self) -> bool:
        return self._service.power_save


class PlayerService:
    """Owns the engine, sleep timer, sync timing and both D-Bus trees."""

    def __init__(self, db, api, backend,
                 clock=None, device_label: str = "linux",
                 local_file=None, start_worker: bool = True) -> None:
        self._db = db
        self._clock = clock if clock is not None else _monotonic_ms
        self._device_label = device_label
        self.local_file = local_file
        self._api_base: str | None = None
        self._bearer: str | None = None
        if api is None:
            api = api_module.AudiobookApi(
                base_url=lambda: self._api_base,
                token=lambda: self._bearer)
        self._api = api
        self._progress = repos_module.ProgressRepository(
            self._api, db, now_ms=_wall_ms)
        self._stream_auth = stream_auth_module.StreamAuth()
        self.engine = player_module.BookPlayer(
            backend, self._stream_auth, now_ms=self._clock,
            on_book_ended=self._book_ended_from_backend)
        self._sleep = sleep_module.SleepTimer(
            _SleepTarget(self), now_ms=self._clock)
        self._scheduler = sync_module.SyncScheduler(
            _SyncEnv(self), clock=self._clock)
        self._sync = sync_module.ProgressSync(
            self._api, self._progress,
            set_last_played=self._set_last_played,
            request_flush=self._request_flush,
            device_label=device_label,
            on_session_changed=self._session_remapped)
        self.screen_on = True
        self.power_save = False
        self._need_play_event = True
        self._last_power_probe_ms = 0
        self._last_call_ms = self._clock()
        self._last_published: dict = {}
        self._last_mpris: tuple = ()
        self._flush_queued = False
        self._connection = None
        self._loop = None
        self._idle_timeout = resolve_idle_timeout()
        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        if start_worker:
            self._worker = threading.Thread(
                target=self._worker_loop, daemon=True,
                name="xx-audiobook-player")
            self._worker.start()
        self._apply_prefs()

    # -- session (in-memory bearer, never logged) ---------------------------

    def set_session(self, api_base: str, bearer: str) -> None:
        if not (api_base or "").strip() or not (bearer or "").strip():
            raise ServiceError("signed out")
        self._api_base = api_base.strip()
        self._bearer = bearer.strip()
        self._stream_auth.set(self._api_base, self._bearer)

    def clear_session(self) -> None:
        self._touch()
        try:
            self.stop()
        except Exception:
            pass
        try:
            self._sleep.cancel()
        except Exception:
            pass
        # The stop-write above is queued ahead of this, so the final
        # position posts with the old bearer before it is dropped.
        self._submit(self._clear_session_state)
        self._scheduler.reset()

    def _clear_session_state(self) -> None:
        self._api_base = None
        self._bearer = None
        self._stream_auth.clear()

    # -- prefs ---------------------------------------------------------------

    def _apply_prefs(self) -> None:
        try:
            prefs = prefs_module.load()
        except Exception:
            return
        self.engine.skip_back_s = prefs.skip_back_seconds
        self.engine.skip_forward_s = prefs.skip_seconds
        self.engine.auto_rewind_s = prefs.auto_rewind_seconds

    # -- opening books -------------------------------------------------------

    def resolve_track_uri(self, item_id: str, file_id: str,
                          url: str) -> str:
        if self.local_file is not None:
            try:
                local = self.local_file(item_id, file_id)
            except Exception:
                local = None
            if local:
                return player_module.file_uri(str(local))
        try:
            resolved = self._api.resolve(url)
        except Exception:
            resolved = None
        return resolved or url

    def open_book_sync(self, item_id: str) -> tuple:
        """Worker-thread open: returns (book, resume_ms, rate)."""
        self._apply_prefs()
        try:
            play = self._api.play(
                item_id, api_module.PlayRequest(device=self._device_label))
        except api_module.ApiError as exc:
            if isinstance(exc, api_module.Http) and exc.code == 401:
                raise ServiceError("not signed in") from exc
            offline = self._offline_book(item_id)
            if offline is None:
                raise ServiceError(str(exc) or "cannot load book") from exc
            return offline
        timeline = timeline_module.BookTimeline.from_play(
            play, lambda fid, url: self.resolve_track_uri(
                play.item_id or item_id, fid, url))
        detail = None
        try:
            library = repos_module.LibraryRepository(self._api, self._db)
            target = play.item_id or item_id
            detail = library.cached_detail(target)
            if detail is None:
                detail, _error = library.refresh_detail(target)
        except Exception:
            detail = None
        if not timeline.chapters and detail is not None:
            timeline = timeline_module.BookTimeline(
                timeline.tracks, timeline_module.chapters_of(
                    detail.chapters))
        target_id = play.item_id or item_id
        if play.resume_position_s > 0:
            try:
                self._progress.pull(target_id)
            except api_module.ApiError:
                pass
        prefs = prefs_module.load()
        resume = timeline_module.resume_ms(
            self._progress.get(target_id), prefs.last_item_id,
            prefs.last_position_s, target_id, timeline.duration_ms)
        rate = timeline_module.initial_speed(
            self._progress.speed_for(target_id), prefs.default_speed)
        book = timeline_module.LoadedBook(
            item_id=target_id,
            title=getattr(detail, "title", "") or "",
            author=", ".join(getattr(detail, "authors", None) or []),
            cover_url=getattr(detail, "cover_url", None),
            session_id=play.session_id or None, timeline=timeline)
        self._set_last_played(target_id, resume / 1000.0)
        return (book, resume, rate)

    def _offline_book(self, item_id: str) -> tuple | None:
        try:
            library = repos_module.LibraryRepository(self._api, self._db)
            detail = library.cached_detail(item_id)
        except Exception:
            return None
        if detail is None or self.local_file is None:
            return None
        files = sorted(getattr(detail, "files", None) or [],
                       key=lambda f: f.index)
        if not files:
            return None
        tracks = []
        start = 0
        for item in files:
            try:
                local = self.local_file(item_id, item.file_id)
            except Exception:
                local = None
            if not local:
                return None
            duration = timeline_module.seconds_to_ms(item.duration_s)
            tracks.append(timeline_module.Track(
                item.file_id, player_module.file_uri(str(local)),
                start, duration, item.mime))
            start += duration
        timeline = timeline_module.BookTimeline(
            tracks, timeline_module.chapters_of(detail.chapters))
        prefs = prefs_module.load()
        resume = timeline_module.resume_ms(
            self._progress.get(item_id), prefs.last_item_id,
            prefs.last_position_s, item_id, timeline.duration_ms)
        rate = timeline_module.initial_speed(
            self._progress.speed_for(item_id), prefs.default_speed)
        return (timeline_module.LoadedBook(
            item_id=item_id, title=detail.title or "",
            author=", ".join(detail.authors or []),
            cover_url=None, session_id=None, timeline=timeline),
            resume, rate)

    def finish_open(self, opened: tuple) -> None:
        book, resume_ms, rate = opened
        self.engine.open_book(book, resume_ms, rate)
        self._scheduler.reset()
        self._need_play_event = True
        self._publish_state(force=True)

    # -- transport (main thread) ----------------------------------------------

    def play(self) -> bool:
        if not self.engine.play():
            return False
        if self._need_play_event:
            self._need_play_event = False
            book = self.engine.loaded
            if book is not None:
                item_id = book.item_id
                position = self.engine.position_global_ms() / 1000.0
                rate = self.engine.rate
                self._submit(self._safe_log_event, item_id, "play",
                             position, rate)
        self._publish_state()
        return True

    def _safe_log_event(self, item_id: str, event: str,
                        position_s: float, speed: float) -> None:
        try:
            self._progress.log_event(item_id, event, position_s, speed)
        except Exception:
            pass

    def pause(self) -> bool:
        if not self.engine.pause():
            return False
        self._need_play_event = True
        self._write_now(self._scheduler.on_pause())
        self._publish_state(force=True)
        return True

    def pause_for_sleep(self) -> None:
        if self.engine.pause():
            self._write_now(self._scheduler.on_pause())
        self._publish_state(force=True)

    def play_pause(self) -> bool:
        if self.engine.status == player_module.STATUS_PLAYING:
            return self.pause()
        return self.play()

    def stop(self) -> bool:
        book = self.engine.loaded
        if book is None or not self.engine.stop():
            return False
        tick = self._scheduler.on_stop()
        if tick is not None:
            self._submit(self._stop_write_then_close, tick,
                         book.item_id, book.session_id,
                         self.engine.duration_ms(), self.engine.rate)
        else:
            self._sync.mark_closed(book.item_id, book.session_id)
        self._need_play_event = True
        self._publish_state(force=True)
        return True

    def _stop_write_then_close(self, tick, item_id: str,
                               session_id: str | None,
                               duration_ms: int, speed: float) -> None:
        try:
            self._sync.write(tick, session_id, duration_ms, speed)
        finally:
            try:
                self._sync.mark_closed(item_id, session_id)
            except Exception:
                pass

    def seek_to_ms(self, global_ms: int) -> int:
        position = self.engine.seek_global(global_ms)
        self._emit_seeked(position)
        self._write_now(self._scheduler.on_seek())
        self._publish_state()
        return position

    def seek_relative_ms(self, delta_ms: int) -> int:
        return self.seek_to_ms(
            self.engine.position_global_ms() + delta_ms)

    def skip_back(self) -> int:
        position = self.engine.skip_back()
        self._emit_seeked(position)
        self._write_now(self._scheduler.on_seek())
        self._publish_state()
        return position

    def skip_forward(self) -> int:
        position = self.engine.skip_forward()
        self._emit_seeked(position)
        self._write_now(self._scheduler.on_seek())
        self._publish_state()
        return position

    def next_chapter(self) -> bool:
        if not self.engine.next_chapter():
            return False
        self._emit_seeked(self.engine.position_global_ms())
        self._write_now(self._scheduler.on_seek())
        self._publish_state()
        return True

    def previous_chapter(self) -> bool:
        if not self.engine.previous_chapter():
            return False
        self._emit_seeked(self.engine.position_global_ms())
        self._write_now(self._scheduler.on_seek())
        self._publish_state()
        return True

    def set_rate(self, rate: float) -> float:
        applied = self.engine.set_rate(rate)
        book = self.engine.loaded
        if book is not None:
            item_id, speed = book.item_id, applied
            self._submit(lambda: self._safe_set_speed(item_id, speed))
        self._publish_state()
        return applied

    def _safe_set_speed(self, item_id: str, speed: float) -> None:
        try:
            self._progress.set_speed(item_id, speed)
        except Exception:
            pass

    def set_volume(self, volume: float) -> float:
        applied = self.engine.set_volume(volume)
        self._publish_state()
        return applied

    # -- sleep (main thread) ---------------------------------------------------

    def sleep_ms(self, duration_ms: int) -> None:
        if self.engine.loaded is None:
            raise ServiceError("nothing loaded")
        try:
            self._sleep.start(int(duration_ms))
        except ValueError as exc:
            raise ServiceError(str(exc)) from exc
        self._publish_state()

    def sleep_end_of_chapter(self) -> None:
        if self.engine.loaded is None:
            raise ServiceError("nothing loaded")
        self._sleep.start_end_of_chapter()
        self._publish_state()

    def cancel_sleep(self) -> None:
        self._sleep.cancel()
        self._publish_state()

    def _sleep_tick(self) -> None:
        self._sleep.tick()
        self._publish_state()

    # -- sync writes -------------------------------------------------------------

    def _snapshot_sync(self) -> tuple:
        book = self.engine.loaded
        if book is None:
            return (None, 0, self.engine.rate)
        return (book.session_id, self.engine.duration_ms(),
                self.engine.rate)

    def _write_now(self, tick) -> None:
        if tick is None:
            return
        session_id, duration_ms, rate = self._snapshot_sync()
        book = self.engine.loaded
        if book is None or tick.item_id != book.item_id:
            return
        self._submit(self._sync.write, tick, session_id,
                     duration_ms, rate)

    def _second_tick(self) -> None:
        tick = self._scheduler.tick()
        if tick is not None:
            self._write_now(tick)
        self._maybe_probe_power()
        self._publish_state()

    def _set_last_played(self, item_id: str, position_s: float) -> None:
        try:
            prefs = prefs_module.load()
            prefs.last_item_id = item_id
            prefs.last_position_s = position_s
            prefs_module.save(prefs)
        except Exception:
            pass

    def _request_flush(self) -> None:
        if self._flush_queued:
            return
        self._flush_queued = True

        def _flush() -> None:
            self._flush_queued = False
            try:
                self._progress.flush()
            except Exception:
                pass

        self._submit(_flush)

    def _session_remapped(self, item_id: str, old: str,
                          new: str | None) -> None:
        from gi.repository import GLib

        GLib.idle_add(self._apply_remap, item_id, old, new)

    def _apply_remap(self, item_id: str, old: str,
                     new: str | None) -> bool:
        book = self.engine.loaded
        if book is not None and book.item_id == item_id and (
                book.session_id == old):
            book.session_id = new
        return False

    def _book_ended_from_backend(self, item_id: str) -> None:
        from gi.repository import GLib

        GLib.idle_add(self._book_ended_idle, item_id)

    def _book_ended_idle(self, item_id: str) -> bool:
        book = self.engine.loaded
        if book is None or book.item_id != item_id:
            return False
        self._write_now(self._scheduler.on_stop())
        session_id = book.session_id
        self._submit(lambda: self._finish_book(item_id, session_id))
        self._publish_state(force=True)
        return False

    def _finish_book(self, item_id: str, session_id: str | None) -> None:
        try:
            self._progress.set_finished(item_id, True)
        except Exception:
            pass
        try:
            self._sync.mark_closed(item_id, session_id)
        except Exception:
            pass
        self._request_flush()

    def _maybe_probe_power(self) -> None:
        now = self._clock()
        if now - self._last_power_probe_ms < 30000:
            return
        self._last_power_probe_ms = now
        self._submit(self._probe_power)

    def _probe_power(self) -> None:
        try:
            from gi.repository import Gio, GLib

            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            reply = bus.call_sync(
                "org.freedesktop.UPower", "/org/freedesktop/UPower",
                PROPS_INTERFACE, "Get",
                GLib.Variant("(ss)", ("org.freedesktop.UPower",
                                      "OnBattery")),
                GLib.VariantType.new("(v)"),
                Gio.DBusCallFlags.NONE, 500, None)
            (boxed,) = reply.unpack()
            self.power_save = bool(boxed.unpack())
        except Exception:
            self.power_save = False

    # -- state snapshots ---------------------------------------------------------

    def state_dict(self) -> dict:
        snap = self.engine.snapshot()
        sleeping = self._sleep.state
        if isinstance(sleeping, sleep_module.SleepCounting):
            remaining = sleeping.remaining_ms
            end_of_chapter = sleeping.end_of_chapter
        else:
            remaining = 0
            end_of_chapter = False
        return {
            "item_id": snap.item_id, "title": snap.title,
            "author": snap.author, "cover_url": snap.cover_url,
            "status": snap.status, "position_us":
            snap.position_ms * 1000,
            "duration_us": snap.duration_ms * 1000,
            "rate": snap.rate, "volume": snap.volume,
            "chapter_index": snap.chapter_index,
            "chapter_count": snap.chapter_count,
            "track_index": snap.track_index,
            "track_count": snap.track_count,
            "has_book": snap.has_book,
            "can_go_next": snap.can_go_next,
            "can_go_previous": snap.can_go_previous,
            "sleep_remaining_ms": remaining,
            "sleep_end_of_chapter": end_of_chapter,
        }

    def track_id(self) -> str:
        return (f"/io/piercingxx/XXAudiobook/Track/"
                f"{self.engine.track_index}")

    def metadata_dict(self) -> dict:
        from gi.repository import GLib

        snap = self.engine.snapshot()
        meta = {
            "mpris:trackid": GLib.Variant("o", self.track_id()),
            "mpris:length": GLib.Variant(
                "x", max(snap.duration_ms, 0) * 1000),
            "xesam:title": GLib.Variant("s", snap.title),
            "xesam:artist": GLib.Variant("as", [snap.author]
                                         if snap.author else []),
            "xesam:album": GLib.Variant("s", snap.title),
        }
        if snap.cover_url:
            meta["mpris:artUrl"] = GLib.Variant("s", snap.cover_url)
        return meta

    def mpris_root_props(self) -> dict:
        from gi.repository import GLib

        mimes: set[str] = set()
        book = self.engine.loaded
        if book is not None:
            for track in book.timeline.tracks:
                if track.mime:
                    mimes.add(track.mime)
        return {
            "CanQuit": GLib.Variant("b", True),
            "CanRaise": GLib.Variant("b", False),
            "HasTrackList": GLib.Variant("b", False),
            "Identity": GLib.Variant("s", IDENTITY),
            "DesktopEntry": GLib.Variant("s", DESKTOP_ENTRY),
            "SupportedUriSchemes": GLib.Variant(
                "as", ["file", "http", "https"]),
            "SupportedMimeTypes": GLib.Variant(
                "as", sorted(mimes)),
        }

    def mpris_player_props(self) -> dict:
        from gi.repository import GLib

        snap = self.engine.snapshot()
        return {
            "PlaybackStatus": GLib.Variant("s", snap.status),
            "LoopStatus": GLib.Variant("s", "None"),
            "Rate": GLib.Variant("d", float(snap.rate)),
            "Shuffle": GLib.Variant("b", False),
            "Metadata": GLib.Variant("a{sv}", self.metadata_dict()),
            "Volume": GLib.Variant("d", float(snap.volume)),
            "Position": GLib.Variant(
                "x", max(snap.position_ms, 0) * 1000),
            "MinimumRate": GLib.Variant(
                "d", float(timeline_module.MIN_SPEED)),
            "MaximumRate": GLib.Variant(
                "d", float(timeline_module.MAX_SPEED)),
            "CanGoNext": GLib.Variant("b", snap.can_go_next),
            "CanGoPrevious": GLib.Variant("b", snap.can_go_previous),
            "CanPlay": GLib.Variant("b", snap.has_book),
            "CanPause": GLib.Variant(
                "b", snap.has_book and snap.status in (
                    player_module.STATUS_PLAYING,
                    player_module.STATUS_PAUSED)),
            "CanSeek": GLib.Variant(
                "b", snap.has_book and snap.duration_ms > 0),
            "CanControl": GLib.Variant("b", True),
        }

    def action_rows(self) -> list:
        snap = self.engine.snapshot()
        return [
            (ACTION_SKIP_BACK,
             f"Back {self.engine.skip_back_s}s", "", True),
            (ACTION_BOOKMARK, "Bookmark", "", snap.has_book),
            (ACTION_SKIP_FORWARD,
             f"Forward {self.engine.skip_forward_s}s", "", True),
        ]

    def _state_variant(self, state: dict):
        from gi.repository import GLib

        return GLib.Variant("(a{sv})", ({
            "item_id": GLib.Variant("s", state["item_id"]),
            "title": GLib.Variant("s", state["title"]),
            "author": GLib.Variant("s", state["author"]),
            "cover_url": GLib.Variant("s", state["cover_url"]),
            "status": GLib.Variant("s", state["status"]),
            "position_us": GLib.Variant("x", int(state["position_us"])),
            "duration_us": GLib.Variant("x", int(state["duration_us"])),
            "rate": GLib.Variant("d", float(state["rate"])),
            "volume": GLib.Variant("d", float(state["volume"])),
            "chapter_index": GLib.Variant(
                "i", int(state["chapter_index"])),
            "chapter_count": GLib.Variant(
                "i", int(state["chapter_count"])),
            "track_index": GLib.Variant("i", int(state["track_index"])),
            "track_count": GLib.Variant("i", int(state["track_count"])),
            "has_book": GLib.Variant("b", bool(state["has_book"])),
            "can_go_next": GLib.Variant("b", bool(state["can_go_next"])),
            "can_go_previous": GLib.Variant(
                "b", bool(state["can_go_previous"])),
            "sleep_remaining_ms": GLib.Variant(
                "x", int(state["sleep_remaining_ms"])),
            "sleep_end_of_chapter": GLib.Variant(
                "b", bool(state["sleep_end_of_chapter"])),
        },))

    # -- signal emission -------------------------------------------------------

    def _emit_seeked(self, position_ms: int) -> None:
        if self._connection is None:
            return
        try:
            from gi.repository import GLib

            self._connection.emit_signal(
                None, MPRIS_PATH, MPRIS_PLAYER, "Seeked",
                GLib.Variant("(x)", (int(position_ms * 1000),)))
        except Exception:
            pass

    def _emit_state_changed(self, state: dict) -> None:
        if self._connection is None:
            return
        try:
            self._connection.emit_signal(
                None, OBJECT_PATH, PLAYER_INTERFACE, "StateChanged",
                self._state_variant(state))
        except Exception:
            pass

    def _emit_mpris_changed(self) -> None:
        if self._connection is None:
            return
        try:
            from gi.repository import GLib

            self._connection.emit_signal(
                None, MPRIS_PATH, PROPS_INTERFACE,
                "PropertiesChanged",
                GLib.Variant("(sa{sv}as)", (
                    MPRIS_PLAYER, self.mpris_player_props(), [])))
        except Exception:
            pass

    def _emit_bookmark_request(self) -> None:
        if self._connection is None:
            return
        book = self.engine.loaded
        if book is None:
            return
        try:
            from gi.repository import GLib

            self._connection.emit_signal(
                None, OBJECT_PATH, PLAYER_INTERFACE,
                "BookmarkRequested",
                GLib.Variant("(sx)", (
                    book.item_id,
                    int(self.engine.position_global_ms() * 1000))))
        except Exception:
            pass

    def _publish_key(self, state: dict) -> dict:
        key = {k: v for k, v in state.items()
               if k not in ("position_us", "sleep_remaining_ms")}
        key["sleep_active"] = bool(
            state.get("sleep_remaining_ms", 0) > 0
            or state.get("sleep_end_of_chapter", False))
        return key

    def _mpris_key(self) -> tuple:
        snap = self.engine.snapshot()
        return (snap.status, self.track_id(), snap.duration_ms,
                snap.title, snap.author, snap.cover_url, snap.rate,
                snap.volume, snap.can_go_next, snap.can_go_previous,
                snap.has_book)

    def _publish_state(self, force: bool = False) -> None:
        state = self.state_dict()
        key = self._publish_key(state)
        if force or key != self._last_published:
            self._last_published = key
            self._emit_state_changed(state)
        mpris_key = self._mpris_key()
        if force or mpris_key != self._last_mpris:
            self._last_mpris = mpris_key
            self._emit_mpris_changed()

    # -- D-Bus dispatch --------------------------------------------------------

    def _dispatch_player(self, method: str, args: tuple):
        if method == "Open":
            (item_id,) = args
            self._open_async(item_id, None)
            return None
        if method == "Play":
            self.play()
        elif method == "Pause":
            self.pause()
        elif method == "PlayPause":
            self.play_pause()
        elif method == "Stop":
            self.stop()
        elif method == "SeekTo":
            (position_us,) = args
            self.seek_to_ms(int(position_us) // 1000)
        elif method == "SeekRelative":
            (offset_us,) = args
            self.seek_relative_ms(int(offset_us) // 1000)
        elif method == "SkipBack":
            self.skip_back()
        elif method == "SkipForward":
            self.skip_forward()
        elif method == "NextChapter":
            self.next_chapter()
        elif method == "PreviousChapter":
            self.previous_chapter()
        elif method == "SetRate":
            (rate,) = args
            self.set_rate(float(rate))
        elif method == "SetVolume":
            (volume,) = args
            self.set_volume(float(volume))
        elif method == "Sleep":
            (duration_ms,) = args
            self.sleep_ms(int(duration_ms))
        elif method == "SleepEndOfChapter":
            self.sleep_end_of_chapter()
        elif method == "CancelSleep":
            self.cancel_sleep()
        elif method == "SetSession":
            api_base, bearer = args
            self.set_session(str(api_base), str(bearer))
        elif method == "ClearSession":
            self.clear_session()
        elif method == "GetState":
            return self._state_variant(self.state_dict())
        elif method == "CheckConflict":
            (item_id,) = args
            return self._conflict_variant_sync(item_id)
        elif method == "ResolveConflict":
            item_id, use_server = args
            self._resolve_async(item_id, bool(use_server), None)
            return None
        else:
            raise ServiceError(f"unknown method: {method}")
        return None

    def _conflict_variant_sync(self, item_id: str):
        from gi.repository import GLib

        conflict = self._progress.compare_on_open(item_id)
        if conflict is None:
            local = self._progress.get(item_id)
            doc = {"needs_prompt": GLib.Variant("b", False),
                   "local_s": GLib.Variant(
                       "d", float(local.position_s if local else 0.0)),
                   "server_s": GLib.Variant("d", 0.0),
                   "duration_s": GLib.Variant(
                       "d", float(local.duration_s or 0.0)
                       if local and local.duration_s else 0.0)}
        else:
            doc = {"needs_prompt": GLib.Variant("b", True),
                   "local_s": GLib.Variant("d", float(conflict.local_s)),
                   "server_s": GLib.Variant("d", float(conflict.server_s)),
                   "duration_s": GLib.Variant(
                       "d", float(conflict.duration_s or 0.0))}
        return GLib.Variant("(a{sv})", (doc,))

    def _dispatch_mpris(self, interface: str, method: str, args: tuple):
        if interface == MPRIS_ROOT:
            if method == "Raise":
                return None
            if method == "Quit":
                self.stop()
                self._request_quit()
                return None
            raise ServiceError(f"unknown method: {method}")
        if interface == MPRIS_PLAYER:
            if method == "Play":
                self.play()
            elif method == "Pause":
                self.pause()
            elif method == "PlayPause":
                self.play_pause()
            elif method == "Stop":
                self.stop()
            elif method == "Next":
                self.next_chapter()
            elif method == "Previous":
                self.previous_chapter()
            elif method == "Seek":
                (offset_us,) = args
                self.seek_relative_ms(int(offset_us) // 1000)
            elif method == "SetPosition":
                track_id, position_us = args
                if str(track_id) != self.track_id():
                    return None
                self.seek_to_ms(int(position_us) // 1000)
            elif method == "OpenUri":
                (uri,) = args
                item = _book_item_of(str(uri))
                if item is None:
                    raise ServiceError("only book: URIs open here")
                self._open_async(item, None)
            else:
                raise ServiceError(f"unknown method: {method}")
            return None
        raise ServiceError(f"unknown interface: {interface}")

    def _dispatch_actions(self, method: str, args: tuple):
        from gi.repository import GLib

        if method == "ListActions":
            return GLib.Variant(
                "(a(sssb))", (self.action_rows(),))
        if method == "ActivateAction":
            (action_id,) = args
            if action_id == ACTION_SKIP_BACK:
                self.skip_back()
            elif action_id == ACTION_SKIP_FORWARD:
                self.skip_forward()
            elif action_id == ACTION_BOOKMARK:
                self._emit_bookmark_request()
            else:
                raise ServiceError(f"unknown action: {action_id}")
            return None
        raise ServiceError(f"unknown method: {method}")

    def _dispatch_props(self, method: str, args: tuple):
        from gi.repository import GLib

        if method == "GetAll":
            (interface,) = args
            try:
                props = self._props_for(interface)
            except KeyError:
                raise ServiceError("unknown interface") from None
            return GLib.Variant("(a{sv})", (props,))
        if method == "Get":
            interface, prop = args
            try:
                value = self._props_for(interface)[prop]
            except KeyError:
                raise ServiceError("unknown property") from None
            return GLib.Variant("(v)", (value,))
        if method == "Set":
            interface, prop, value = args
            self._set_prop(interface, prop, value)
            return None
        raise ServiceError(f"unknown method: {method}")

    def _props_for(self, interface: str) -> dict:
        if interface == MPRIS_ROOT:
            return self.mpris_root_props()
        if interface == MPRIS_PLAYER:
            return self.mpris_player_props()
        if interface == PLAYER_INTERFACE:
            return self._player_props()
        raise KeyError(interface)

    def _player_props(self) -> dict:
        from gi.repository import GLib

        state = self.state_dict()
        return {
            "ItemId": GLib.Variant("s", state["item_id"]),
            "Title": GLib.Variant("s", state["title"]),
            "Author": GLib.Variant("s", state["author"]),
            "Status": GLib.Variant("s", state["status"]),
            "PositionUs": GLib.Variant("x", int(state["position_us"])),
            "DurationUs": GLib.Variant("x", int(state["duration_us"])),
            "Rate": GLib.Variant("d", float(state["rate"])),
            "HasBook": GLib.Variant("b", bool(state["has_book"])),
            "SleepRemainingMs": GLib.Variant(
                "x", int(state["sleep_remaining_ms"])),
        }

    def _set_prop(self, interface: str, prop: str, value) -> None:
        plain = value.unpack() if hasattr(value, "unpack") else value
        if interface == MPRIS_PLAYER and prop == "Volume":
            self.set_volume(float(plain))
        elif interface == MPRIS_PLAYER and prop == "Rate":
            self.set_rate(float(plain))
        else:
            raise ServiceError("property is read-only")

    # -- async (worker-thread) calls -------------------------------------------

    def _submit(self, func, *args) -> None:
        self._queue.put((func, args))

    def _worker_loop(self) -> None:
        while True:
            task = self._queue.get()
            try:
                if task is None:
                    return
                func, args = task
                func(*args)
            except Exception:
                pass
            finally:
                self._queue.task_done()

    def _open_async(self, item_id: str, invocation) -> None:
        def _work() -> None:
            from gi.repository import GLib

            try:
                opened = self.open_book_sync(item_id)
            except ServiceError as exc:
                if invocation is not None:
                    GLib.idle_add(invocation.return_dbus_error,
                                  ERROR, str(exc) or "cannot load book")
                return
            except Exception as exc:
                if invocation is not None:
                    GLib.idle_add(invocation.return_dbus_error,
                                  ERROR, f"cannot load book: {exc}")
                return
            if invocation is not None:
                GLib.idle_add(self._open_done, opened, invocation)
            else:
                GLib.idle_add(self._open_done_no_reply, opened)

        self._submit(_work)

    def _open_done(self, opened: tuple, invocation) -> bool:
        self.finish_open(opened)
        self._emit_seeked(self.engine.position_global_ms())
        invocation.return_value(None)
        return False

    def _open_done_no_reply(self, opened: tuple) -> bool:
        self.finish_open(opened)
        self._emit_seeked(self.engine.position_global_ms())
        return False

    def _conflict_async(self, item_id: str, invocation) -> None:
        def _work() -> None:
            from gi.repository import GLib

            try:
                variant = self._conflict_variant_sync(item_id)
            except Exception as exc:
                GLib.idle_add(invocation.return_dbus_error,
                              ERROR, f"conflict check failed: {exc}")
                return
            GLib.idle_add(invocation.return_value, variant)

        self._submit(_work)

    def _resolve_async(self, item_id: str, use_server: bool,
                       invocation) -> None:
        def _work() -> None:
            from gi.repository import GLib

            try:
                conflict = self._progress.compare_on_open(item_id)
                if conflict is not None:
                    self._progress.resolve(conflict, use_server)
            except Exception as exc:
                if invocation is not None:
                    GLib.idle_add(invocation.return_dbus_error,
                                  ERROR, f"resolve failed: {exc}")
                return
            if invocation is not None:
                GLib.idle_add(invocation.return_value, None)

        self._submit(_work)

    def _request_quit(self) -> None:
        from gi.repository import GLib

        if self._loop is not None:
            GLib.idle_add(self._loop.quit)

    # -- Gio plumbing ------------------------------------------------------------

    def handle_mpris_call(self, _connection, _sender, _path,
                          interface_name, method_name, parameters,
                          invocation) -> None:
        self._touch()
        try:
            args = parameters.unpack() if parameters is not None else ()
        except Exception as exc:
            invocation.return_dbus_error(ERROR, f"bad args: {exc}")
            return
        try:
            if interface_name == PROPS_INTERFACE:
                variant = self._dispatch_props(method_name, args)
            elif interface_name == ACTIONS_INTERFACE:
                variant = self._dispatch_actions(method_name, args)
            else:
                variant = self._dispatch_mpris(
                    interface_name, method_name, args)
        except ServiceError as exc:
            invocation.return_dbus_error(ERROR, str(exc))
            return
        except Exception as exc:
            invocation.return_dbus_error(ERROR, f"failed: {exc}")
            return
        from gi.repository import GLib

        if variant is None:
            invocation.return_value(GLib.Variant("()", ()))
        else:
            invocation.return_value(variant)

    def handle_player_call(self, _connection, _sender, _path,
                           interface_name, method_name, parameters,
                           invocation) -> None:
        self._touch()
        try:
            args = parameters.unpack() if parameters is not None else ()
        except Exception as exc:
            invocation.return_dbus_error(ERROR, f"bad args: {exc}")
            return
        if interface_name == PROPS_INTERFACE:
            try:
                variant = self._dispatch_props(method_name, args)
            except ServiceError as exc:
                invocation.return_dbus_error(ERROR, str(exc))
                return
            from gi.repository import GLib

            if variant is None:
                invocation.return_value(GLib.Variant("()", ()))
            else:
                invocation.return_value(variant)
            return
        if method_name in ("Open", "CheckConflict", "ResolveConflict"):
            if method_name == "Open":
                (item_id,) = args
                self._open_async(str(item_id), invocation)
            elif method_name == "CheckConflict":
                (item_id,) = args
                self._conflict_async(str(item_id), invocation)
            else:
                item_id, use_server = args
                self._resolve_async(str(item_id), bool(use_server),
                                    invocation)
            return
        try:
            variant = self._dispatch_player(method_name, args)
        except ServiceError as exc:
            invocation.return_dbus_error(ERROR, str(exc))
            return
        except Exception as exc:
            invocation.return_dbus_error(ERROR, f"failed: {exc}")
            return
        from gi.repository import GLib

        if variant is None:
            invocation.return_value(GLib.Variant("()", ()))
        else:
            invocation.return_value(variant)

    def register_on(self, connection) -> list:
        from gi.repository import Gio

        ids = []
        mpris_node = Gio.DBusNodeInfo.new_for_xml(MPRIS_XML)
        for iface in mpris_node.interfaces:
            ids.append(connection.register_object(
                MPRIS_PATH, iface, self.handle_mpris_call))
        player_node = Gio.DBusNodeInfo.new_for_xml(PLAYER_XML)
        for iface in player_node.interfaces:
            ids.append(connection.register_object(
                OBJECT_PATH, iface, self.handle_player_call))
        self._connection = connection
        return ids

    def idle_seconds(self) -> float:
        return (self._clock() - self._last_call_ms) / 1000.0

    def _touch(self) -> None:
        self._last_call_ms = self._clock()

    def shutdown(self) -> None:
        self._queue.put(None)
        if self._loop is not None:
            self._loop.quit()

    def run(self, idle_timeout: float | None = None) -> int:
        from gi.repository import Gio, GLib

        self._idle_timeout = resolve_idle_timeout(idle_timeout)
        loop = GLib.MainLoop()
        self._loop = loop
        state: dict = {}

        def on_bus_acquired(connection, _name: str) -> None:
            self.register_on(connection)

        def on_name_acquired(_connection, _name: str) -> None:
            state["owned"] = True

        def on_name_lost(_connection, _name: str) -> None:
            loop.quit()

        Gio.bus_own_name(Gio.BusType.SESSION, PLAYER_BUS,
                         Gio.BusNameOwnerFlags.NONE,
                         on_bus_acquired, on_name_acquired, on_name_lost)
        Gio.bus_own_name(Gio.BusType.SESSION, MPRIS_BUS,
                         Gio.BusNameOwnerFlags.NONE,
                         lambda _c, _n: None, lambda _c, _n: None,
                         lambda _c, _n: None)
        GLib.timeout_add(250, self._tick_sleep)
        GLib.timeout_add(1000, self._tick_second)
        try:
            loop.run()
        finally:
            self._loop = None
            self._queue.put(None)
        return 0

    def _tick_sleep(self) -> bool:
        try:
            self._sleep_tick()
        except Exception:
            pass
        return True

    def _tick_second(self) -> bool:
        try:
            self._second_tick()
        except Exception:
            pass
        try:
            if (self.engine.loaded is None
                    and self.idle_seconds() >= self._idle_timeout):
                if self._loop is not None:
                    self._loop.quit()
                return False
        except Exception:
            pass
        return True


def _book_item_of(uri: str) -> str | None:
    if uri.startswith("book:"):
        item = uri[len("book:"):]
        return item or None
    return None


def build_service(db_path=None, api_base: str | None = None,
                  bearer: str | None = None,
                  backend=None, idle_timeout: float | None = None,
                  local_file=None) -> PlayerService:
    from xx_audiobook import paths as paths_module

    paths_module.ensure_app_dirs()
    db = store_module.Database(db_path) if db_path else (
        store_module.Database())
    if backend is None:
        backend = player_module.RealGstBackend()
    if local_file is None:
        try:
            from xx_audiobook import download as download_module

            local_file = download_module.DownloadRepository(
                None, db, download_module.BookFiles()).local_file
        except Exception:
            local_file = None
    service = PlayerService(db, None, backend, local_file=local_file)
    if api_base and bearer:
        service.set_session(api_base, bearer)
    if idle_timeout is not None:
        service._idle_timeout = float(idle_timeout)
    return service


def run_service(db_path=None, api_base: str | None = None,
                bearer: str | None = None,
                backend=None, idle_timeout: float | None = None,
                local_file=None) -> int:
    service = build_service(db_path, api_base, bearer, backend,
                            idle_timeout, local_file)
    return service.run(idle_timeout)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="xx-audiobook-player")
    parser.add_argument("--db", default=None)
    parser.add_argument("--idle-timeout", type=float, default=None)
    parser.add_argument("--audio-sink", default=None)
    args = parser.parse_args(argv if argv is not None else [])
    backend = player_module.RealGstBackend(
        audio_sink=args.audio_sink or None)
    return run_service(db_path=args.db, backend=backend,
                       idle_timeout=resolve_idle_timeout(
                           args.idle_timeout))