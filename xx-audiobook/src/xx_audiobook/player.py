"""Book playback engine: GStreamer playbin behind the book timeline (M2).

BookPlayer maps book-level transport (seek/skip/chapters/speed) onto one
audio backend track at a time; track transitions re-point the backend at
the next file URI. The bearer goes to the estate host only (StreamAuth);
downloaded tracks play from file:// with no header.

RealGstBackend is one playbin with a scaletempo audio filter for speed.
FakeBackend records calls for headless tests. PlayerState is the snapshot
the UI reads; PlayerClient drives the player service over D-Bus from a
worker thread. gi imports stay lazy so the engine imports without it.
"""

from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass

from xx_audiobook import stream_auth as stream_auth_module
from xx_audiobook import timeline as timeline_module

STATUS_STOPPED = "Stopped"
STATUS_PLAYING = "Playing"
STATUS_PAUSED = "Paused"

AUDIOSINK_ENV = "XX_AUDIOBOOK_AUDIOSINK"


def file_uri(path: str) -> str:
    return "file://" + urllib.parse.quote(path, safe="/")


class AudioBackend:
    """One-track audio sink; positions are intra-track milliseconds."""

    def set_uri(self, uri: str, headers: dict) -> None:
        raise NotImplementedError

    def play(self) -> None:
        raise NotImplementedError

    def pause(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def seek(self, offset_ms: int, rate: float) -> bool:
        raise NotImplementedError

    def set_rate(self, rate: float) -> bool:
        raise NotImplementedError

    def set_volume(self, volume: float) -> None:
        raise NotImplementedError

    def position_ms(self) -> int | None:
        raise NotImplementedError

    def duration_ms(self) -> int | None:
        raise NotImplementedError


class FakeBackend(AudioBackend):
    """In-memory backend for headless tests."""

    def __init__(self, durations: dict[str, int] | None = None) -> None:
        self.uris: list[str] = []
        self.headers: list[dict] = []
        self.calls: list[str] = []
        self.position = 0
        self.rate = 1.0
        self.volume = 1.0
        self.playing = False
        self.durations = dict(durations or {})
        self.seek_results: list[bool] = []
        self.on_eos = None
        self.on_error = None

    def set_uri(self, uri: str, headers: dict) -> None:
        self.uris.append(uri)
        self.headers.append(dict(headers))
        self.position = 0
        self.calls.append("set-uri")

    def play(self) -> None:
        self.playing = True
        self.calls.append("play")

    def pause(self) -> None:
        self.playing = False
        self.calls.append("pause")

    def stop(self) -> None:
        self.playing = False
        self.calls.append("stop")

    def seek(self, offset_ms: int, rate: float) -> bool:
        self.position = max(offset_ms, 0)
        self.rate = rate
        self.calls.append("seek")
        return True

    def set_rate(self, rate: float) -> bool:
        self.rate = rate
        self.calls.append("set-rate")
        return True

    def set_volume(self, volume: float) -> None:
        self.volume = volume
        self.calls.append("set-volume")

    def position_ms(self) -> int | None:
        return self.position

    def duration_ms(self) -> int | None:
        if not self.uris:
            return None
        return self.durations.get(self.uris[-1])

    def fire_eos(self) -> None:
        if self.on_eos is not None:
            self.on_eos()

    def fire_error(self, message: str) -> None:
        if self.on_error is not None:
            self.on_error(message)


class RealGstBackend(AudioBackend):
    """playbin + scaletempo; audio-sink override for tests/dumb outputs."""

    def __init__(self, audio_sink: str | None = None,
                 on_eos=None, on_error=None) -> None:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        self._gst = Gst
        playbin = Gst.ElementFactory.make("playbin", "xx-audiobook")
        if playbin is None:
            raise RuntimeError("no GStreamer playbin")
        tempo = Gst.ElementFactory.make("scaletempo", "xx-tempo")
        if tempo is not None:
            playbin.set_property("audio-filter", tempo)
        sink_name = audio_sink or os.environ.get(AUDIOSINK_ENV, "")
        if sink_name:
            sink = Gst.ElementFactory.make(sink_name, "xx-sink")
            if sink is not None:
                playbin.set_property("audio-sink", sink)
        self._playbin = playbin
        self._headers: dict = {}
        self._uri = ""
        self.on_eos = on_eos
        self.on_error = on_error
        playbin.connect("source-setup", self._on_source_setup)
        bus = playbin.get_bus()
        bus.add_signal_watch()
        bus.connect("message::eos", self._bus_eos)
        bus.connect("message::error", self._bus_error)

    def _on_source_setup(self, _playbin, source) -> None:
        if not self._headers:
            return
        try:
            if not source.has_property("extra-headers"):
                return
            struct = self._gst.Structure.new_empty("extra-headers")
            for key, value in self._headers.items():
                struct.set_value(key, value)
            source.set_property("extra-headers", struct)
        except Exception:
            pass

    def _bus_eos(self, _bus, _message) -> None:
        if self.on_eos is not None:
            self.on_eos()

    def _bus_error(self, _bus, message) -> None:
        detail = ""
        try:
            error, detail = message.parse_error()
            detail = f"{error}: {detail}"
        except Exception:
            pass
        if self.on_error is not None:
            self.on_error(detail or "playback error")

    def set_uri(self, uri: str, headers: dict) -> None:
        self._headers = dict(headers)
        self._uri = uri
        self._playbin.set_state(self._gst.State.NULL)
        self._playbin.set_property("uri", uri)

    def play(self) -> None:
        self._playbin.set_state(self._gst.State.PLAYING)

    def pause(self) -> None:
        self._playbin.set_state(self._gst.State.PAUSED)

    def stop(self) -> None:
        self._playbin.set_state(self._gst.State.NULL)

    def seek(self, offset_ms: int, rate: float) -> bool:
        Gst = self._gst
        flags = Gst.SeekFlags.FLUSH
        return bool(self._playbin.seek(
            rate, Gst.Format.TIME, flags, Gst.SeekType.SET,
            max(offset_ms, 0) * Gst.MSECOND, Gst.SeekType.NONE, -1))

    def set_rate(self, rate: float) -> bool:
        position = self.position_ms()
        if position is None:
            return False
        return self.seek(position, rate)

    def set_volume(self, volume: float) -> None:
        try:
            self._playbin.set_property(
                "volume", min(max(volume, 0.0), 1.0))
        except Exception:
            pass

    def _query_ms(self, want_position: bool) -> int | None:
        Gst = self._gst
        try:
            if want_position:
                ok, value = self._playbin.query_position(Gst.Format.TIME)
            else:
                ok, value = self._playbin.query_duration(Gst.Format.TIME)
        except Exception:
            return None
        if not ok or value is None or value < 0:
            return None
        return int(value // Gst.MSECOND)

    def position_ms(self) -> int | None:
        return self._query_ms(True)

    def duration_ms(self) -> int | None:
        return self._query_ms(False)


@dataclass
class PlayerState:
    item_id: str = ""
    title: str = ""
    author: str = ""
    cover_url: str = ""
    status: str = STATUS_STOPPED
    position_ms: int = 0
    duration_ms: int = 0
    rate: float = 1.0
    volume: float = 1.0
    chapter_index: int = -1
    chapter_count: int = 0
    track_index: int = 0
    track_count: int = 0
    has_book: bool = False
    can_go_next: bool = False
    can_go_previous: bool = False


class BookPlayer:
    """Book-level transport over one backend track at a time."""

    def __init__(self, backend: AudioBackend,
                 stream_auth=None,
                 now_ms=None,
                 skip_back_s: int = 30, skip_forward_s: int = 30,
                 auto_rewind_s: int = 2,
                 on_book_ended=None) -> None:
        self._backend = backend
        self._auth = (stream_auth if stream_auth is not None
                      else stream_auth_module.StreamAuth())
        self._now_ms = now_ms if now_ms is not None else _monotonic_ms
        self.skip_back_s = skip_back_s
        self.skip_forward_s = skip_forward_s
        self.auto_rewind_s = auto_rewind_s
        self.on_book_ended = on_book_ended
        backend.on_eos = self._on_track_ended
        self.loaded: timeline_module.LoadedBook | None = None
        self.status = STATUS_STOPPED
        self.rate = 1.0
        self.volume = 1.0
        self.track_index = 0
        self._position_ms = 0
        self._paused_at_ms = 0

    @property
    def has_book(self) -> bool:
        return self.loaded is not None

    def headers_for(self, uri: str) -> dict:
        return self._auth.headers_for(uri)

    def open_book(self, book: timeline_module.LoadedBook,
                  resume_ms: int, rate: float) -> bool:
        if not book.timeline.tracks:
            return False
        self._backend.stop()
        self.loaded = book
        self.rate = timeline_module.clamp_speed(rate)
        self.volume = 1.0
        self._backend.set_volume(1.0)
        self.status = STATUS_PAUSED
        self._paused_at_ms = 0
        point = book.timeline.locate(resume_ms)
        self._load_track(point.track_index, point.offset_ms)
        return True

    def play(self) -> bool:
        if self.loaded is None:
            return False
        if self.status == STATUS_PLAYING:
            return True
        back = timeline_module.auto_rewind_ms(
            self._paused_at_ms, self._now_ms(), self.auto_rewind_s)
        self._paused_at_ms = 0
        if back > 0:
            self.seek_global(self.position_global_ms() - back)
        self._backend.play()
        self.status = STATUS_PLAYING
        return True

    def pause(self) -> bool:
        if self.loaded is None or self.status != STATUS_PLAYING:
            return False
        self._backend.pause()
        self.status = STATUS_PAUSED
        self._paused_at_ms = self._now_ms()
        return True

    def stop(self) -> bool:
        if self.loaded is None:
            return False
        self._backend.stop()
        self.status = STATUS_STOPPED
        self._paused_at_ms = 0
        return True

    def position_global_ms(self) -> int:
        if self.loaded is None:
            return 0
        current = self._backend.position_ms()
        if current is not None:
            track = self.loaded.timeline.tracks[self.track_index]
            self._position_ms = track.start_ms + min(
                max(current, 0), track.duration_ms)
        return self._position_ms

    def duration_ms(self) -> int:
        if self.loaded is None:
            return 0
        return self.loaded.timeline.duration_ms

    def chapter_index(self) -> int:
        if self.loaded is None:
            return -1
        return self.loaded.timeline.chapter_at(self.position_global_ms())

    def seek_global(self, global_ms: int) -> int:
        book = self.loaded
        if book is None:
            return 0
        point = book.timeline.locate(global_ms)
        if point.track_index != self.track_index:
            self._load_track(point.track_index, point.offset_ms)
        else:
            self._backend.seek(point.offset_ms, self.rate)
            self._position_ms = book.timeline.global_of(
                point.track_index, point.offset_ms)
        return self._position_ms

    def skip_back(self) -> int:
        return self.seek_global(timeline_module.skip_to(
            self.loaded.timeline, self.position_global_ms(),
            -timeline_module.skip_ms(self.skip_back_s)))

    def skip_forward(self) -> int:
        return self.seek_global(timeline_module.skip_to(
            self.loaded.timeline, self.position_global_ms(),
            timeline_module.skip_ms(self.skip_forward_s)))

    def next_chapter(self) -> bool:
        if self.loaded is None:
            return False
        target = timeline_module.next_chapter(
            self.loaded.timeline, self.position_global_ms())
        if target is None:
            following = self.track_index + 1
            if self.loaded.timeline.chapters or following >= len(
                    self.loaded.timeline.tracks):
                return False
            target = self.loaded.timeline.tracks[following].start_ms
        self.seek_global(target)
        return True

    def previous_chapter(self) -> bool:
        if self.loaded is None:
            return False
        self.seek_global(timeline_module.previous_chapter(
            self.loaded.timeline, self.position_global_ms()))
        return True

    def set_rate(self, rate: float) -> float:
        self.rate = timeline_module.clamp_speed(rate)
        if self.loaded is not None:
            self._backend.set_rate(self.rate)
        return self.rate

    def set_volume(self, volume: float) -> float:
        self.volume = min(max(volume, 0.0), 1.0)
        self._backend.set_volume(self.volume)
        return self.volume

    def can_go_next(self) -> bool:
        if self.loaded is None:
            return False
        timeline = self.loaded.timeline
        if timeline.chapters:
            return (timeline_module.next_chapter(
                timeline, self.position_global_ms()) is not None)
        return self.track_index + 1 < len(timeline.tracks)

    def can_go_previous(self) -> bool:
        if self.loaded is None:
            return False
        if self.loaded.timeline.chapters:
            return True
        return self.track_index > 0 or self.position_global_ms() > 0

    def snapshot(self) -> PlayerState:
        book = self.loaded
        position = self.position_global_ms()
        if book is None:
            return PlayerState(status=self.status, rate=self.rate,
                               volume=self.volume)
        chapters = book.timeline.chapters
        return PlayerState(
            item_id=book.item_id, title=book.title, author=book.author,
            cover_url=book.cover_url or "", status=self.status,
            position_ms=position, duration_ms=self.duration_ms(),
            rate=self.rate, volume=self.volume,
            chapter_index=self.chapter_index(),
            chapter_count=len(chapters), track_index=self.track_index,
            track_count=len(book.timeline.tracks), has_book=True,
            can_go_next=self.can_go_next(),
            can_go_previous=self.can_go_previous())

    def _load_track(self, track_index: int, offset_ms: int) -> None:
        book = self.loaded
        track = book.timeline.tracks[track_index]
        self.track_index = track_index
        playing = self.status == STATUS_PLAYING
        self._backend.set_uri(track.uri, self.headers_for(track.uri))
        self._backend.pause()
        self._backend.seek(offset_ms, self.rate)
        if playing:
            self._backend.play()
        self._position_ms = book.timeline.global_of(track_index,
                                                    offset_ms)

    def _on_track_ended(self) -> None:
        book = self.loaded
        if book is None or self.status != STATUS_PLAYING:
            return
        following = self.track_index + 1
        if following < len(book.timeline.tracks):
            self._load_track(following, 0)
            return
        self.status = STATUS_STOPPED
        self._position_ms = book.timeline.duration_ms
        self._paused_at_ms = 0
        if self.on_book_ended is not None:
            try:
                self.on_book_ended(book.item_id)
            except Exception:
                pass


def _monotonic_ms() -> int:
    import time

    return int(time.monotonic() * 1000)


def _reply_type(signature: str):
    from gi.repository import GLib

    return GLib.VariantType.new(signature)


OPEN_TIMEOUT_MS = 90000
CALL_TIMEOUT_MS = 5000


class PlayerClient:
    """GUI-side client for the player service; every call blocks, so run
    them on a worker thread and post results with GLib.idle_add."""

    def __init__(self, connection=None) -> None:
        self._connection = connection

    def _conn(self):
        if self._connection is None:
            from gi.repository import Gio

            self._connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        return self._connection

    def _call(self, method: str, args=None, reply_sig: str | None = None,
              timeout_ms: int = CALL_TIMEOUT_MS, auto_start: bool = True):
        from gi.repository import Gio

        from xx_audiobook import __player_bus_name__ as bus_name
        from xx_audiobook.service.player_service import (
            OBJECT_PATH, PLAYER_INTERFACE)

        reply = self._conn().call_sync(
            bus_name, OBJECT_PATH, PLAYER_INTERFACE, method, args,
            _reply_type(reply_sig) if reply_sig else None,
            Gio.DBusCallFlags.NONE if auto_start
            else Gio.DBusCallFlags.NO_AUTO_START, timeout_ms, None)
        if reply is None:
            return None
        return reply.unpack()

    def _arg(self, signature: str, *values):
        from gi.repository import GLib

        return GLib.Variant(signature, values)

    def open(self, item_id: str) -> None:
        self._call("Open", self._arg("(s)", item_id),
                   timeout_ms=OPEN_TIMEOUT_MS)

    def play(self) -> None:
        self._call("Play")

    def pause(self) -> None:
        self._call("Pause")

    def play_pause(self) -> None:
        self._call("PlayPause")

    def stop(self) -> None:
        self._call("Stop")

    def seek_to_us(self, position_us: int) -> None:
        self._call("SeekTo", self._arg("(x)", int(position_us)))

    def seek_relative_us(self, offset_us: int) -> None:
        self._call("SeekRelative", self._arg("(x)", int(offset_us)))

    def skip_back(self) -> None:
        self._call("SkipBack")

    def skip_forward(self) -> None:
        self._call("SkipForward")

    def next_chapter(self) -> None:
        self._call("NextChapter")

    def previous_chapter(self) -> None:
        self._call("PreviousChapter")

    def set_rate(self, rate: float) -> None:
        self._call("SetRate", self._arg("(d)", float(rate)))

    def sleep(self, duration_ms: int) -> None:
        self._call("Sleep", self._arg("(x)", int(duration_ms)))

    def sleep_end_of_chapter(self) -> None:
        self._call("SleepEndOfChapter")

    def cancel_sleep(self) -> None:
        self._call("CancelSleep")

    def set_session(self, api_base: str, bearer: str) -> None:
        self._call("SetSession", self._arg("(ss)", api_base, bearer))

    def clear_session(self) -> None:
        self._call("ClearSession")

    def get_state(self, auto_start: bool = True) -> dict:
        """The service state; ``auto_start=False`` only asks a running
        service (the GUI's poll never starts the daemon)."""
        (boxed,) = self._call(
            "GetState", None, "(a{sv})", auto_start=auto_start) or ({},)
        return {key: _unbox(value) for key, value in boxed.items()}

    def check_conflict(self, item_id: str) -> dict:
        (boxed,) = self._call(
            "CheckConflict", self._arg("(s)", item_id), "(a{sv})",
            timeout_ms=OPEN_TIMEOUT_MS) or ({},)
        return {key: _unbox(value) for key, value in boxed.items()}

    def resolve_conflict(self, item_id: str, use_server: bool) -> None:
        self._call("ResolveConflict",
                   self._arg("(sb)", item_id, bool(use_server)),
                   timeout_ms=OPEN_TIMEOUT_MS)

    def subscribe(self, on_state) -> int:
        """Deliver StateChanged dicts to ``on_state`` in the calling
        thread's main context (subscribe from the GTK thread)."""
        from xx_audiobook import __player_bus_name__ as bus_name
        from xx_audiobook.service.player_service import (
            OBJECT_PATH, PLAYER_INTERFACE)
        from gi.repository import Gio

        def _signal(_conn, _sender, _path, _iface, _name, params) -> None:
            try:
                (boxed,) = params.unpack()
            except Exception:
                return
            on_state({key: _unbox(value) for key, value in boxed.items()})

        return self._conn().signal_subscribe(
            bus_name, PLAYER_INTERFACE, "StateChanged", OBJECT_PATH,
            None, Gio.DBusSignalFlags.NONE, _signal)


def _unbox(value):
    unpack = getattr(value, "unpack", None)
    if callable(unpack):
        try:
            return unpack()
        except Exception:
            return value
    return value
