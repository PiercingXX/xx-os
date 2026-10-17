"""GStreamer playbin wrapper. Main-loop safe: only non-blocking queries and
property sets here; bus messages arrive on the GLib loop via add_signal_watch.

Timing: ``on_timing`` fires (main loop) whenever what the position query
means changes (the pipeline starts or stops running, a seek lands, the
stream's real duration arrives), and ``on_gapless`` when the song handed
over with :meth:`set_next_uri` actually starts playing out. Positions are
never guessed while a seek is in flight.
"""

from __future__ import annotations

import time
from typing import Callable

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

Gst.init(None)

AUDIO_ONLY_FLAGS = 0x02
# After a gapless hand-over the position query answers with the old
# song's end until the new song's first buffer (a few ms); never longer
# than this, so a late look at the bus cannot pin the new song at 0.
HANDOVER_S = 0.5


class GstPlayer:
    def __init__(self, audio_sink=None,
                 on_eos: Callable[[], None] | None = None,
                 on_error: Callable[[str], None] | None = None,
                 on_about_to_finish: Callable[[], None] | None = None,
                 on_timing: Callable[[], None] | None = None,
                 on_gapless: Callable[[], None] | None = None,
                 ) -> None:
        playbin = Gst.ElementFactory.make("playbin", None)
        if playbin is None:
            raise RuntimeError("GStreamer playbin is missing")
        self._playbin = playbin
        self._playbin.set_property("flags", AUDIO_ONLY_FLAGS)
        sink = audio_sink
        if sink is None:
            sink = Gst.ElementFactory.make("autoaudiosink", None)
        if sink is not None:
            self._playbin.set_property("audio-sink", sink)
        self._uri = ""
        self._next_uri = ""
        # Set on the streaming thread when the next song takes over the
        # input; the song becomes current when its stream-start reaches
        # the sink (it is heard then, not when it starts downloading).
        # The loaded song's own stream-start comes first: a cached song
        # can be read to its end before that message is even dispatched.
        self._gap_uri = ""
        self._own_start = False
        # (old song's last position, when): the query still answers it
        # for a moment after the next song's stream-start, while the new
        # song is at 0.
        self._handover: tuple[int, float] | None = None
        self._seeking = False
        self._on_eos = on_eos
        self._on_error = on_error
        self._on_about_to_finish = on_about_to_finish
        self._on_timing = on_timing
        self._on_gapless = on_gapless
        self._bus = self._playbin.get_bus()
        self._bus.add_signal_watch()
        self._bus.connect("message", self._on_message)
        self._playbin.connect("about-to-finish", self._finish_gap)

    # -- transport ----------------------------------------------------------
    @property
    def uri(self) -> str:
        return self._uri

    def load(self, uri: str) -> None:
        """Start ``uri`` from the beginning (the same one again too)."""
        self._playbin.set_state(Gst.State.READY)
        self._playbin.set_property("uri", uri)
        self._uri = uri
        self._next_uri = ""
        self._gap_uri = ""
        self._handover = None
        self._seeking = False
        self._own_start = True

    def play(self) -> None:
        self._playbin.set_state(Gst.State.PLAYING)

    def pause(self) -> None:
        self._playbin.set_state(Gst.State.PAUSED)

    def stop(self) -> None:
        self._playbin.set_state(Gst.State.NULL)
        self._uri = ""
        self._next_uri = ""
        self._gap_uri = ""
        self._handover = None
        self._seeking = False

    def seek_ns(self, position_ns: int) -> bool:
        """Seek to exactly ``position_ns`` (ACCURATE: a KEY_UNIT seek lands
        on the container's previous sync point, seconds early on WebM)."""
        self._seeking = bool(self._playbin.seek_simple(
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
            max(0, position_ns)))
        return self._seeking

    def set_next_uri(self, uri: str) -> None:
        self._next_uri = uri or ""

    def set_volume(self, level: float) -> None:
        self._playbin.set_property(
            "volume", min(1.0, max(0.0, float(level))))

    # -- queries (non-blocking) ----------------------------------------------
    def position_ns(self) -> int | None:
        """The position being heard; None while unknown (nothing loaded,
        or a seek still flushing towards its target)."""
        if self._seeking:
            return None
        ok, value = self._playbin.query_position(Gst.Format.TIME)
        if not ok:
            return None
        if self._handover is not None:
            last, at = self._handover
            if value >= last and time.monotonic() - at < HANDOVER_S:
                return 0
            self._handover = None
        return int(value)

    def duration_ns(self) -> int | None:
        ok, value = self._playbin.query_duration(Gst.Format.TIME)
        return int(value) if ok else None

    def is_playing(self) -> bool:
        _, state, _ = self._playbin.get_state(0)
        return state == Gst.State.PLAYING

    def advancing(self) -> bool:
        """The position is moving right now: PLAYING with nothing pending
        (not prerolling, seeking or on the way to PAUSED)."""
        _, state, pending = self._playbin.get_state(0)
        return (not self._seeking and state == Gst.State.PLAYING
                and pending == Gst.State.VOID_PENDING)

    def shutdown(self) -> None:
        try:
            self._bus.remove_signal_watch()
        finally:
            self._playbin.set_state(Gst.State.NULL)

    # -- bus ------------------------------------------------------------------
    def _finish_gap(self, _playbin) -> None:
        # Streaming thread: hand the next song to playbin, nothing more.
        if self._next_uri:
            self._playbin.set_property("uri", self._next_uri)
            self._gap_uri = self._next_uri
            self._next_uri = ""
        if self._on_about_to_finish is not None:
            self._on_about_to_finish()

    def _timing(self) -> None:
        if self._on_timing is not None:
            self._on_timing()

    def _on_message(self, _bus, message) -> None:
        kind = message.type
        if kind == Gst.MessageType.EOS:
            if self._on_eos is not None:
                self._on_eos()
        elif kind == Gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            if self._on_error is not None:
                self._on_error(str(error))
        elif kind == Gst.MessageType.STREAM_START:
            if self._own_start:
                self._own_start = False
            elif self._gap_uri:
                self._uri, self._gap_uri = self._gap_uri, ""
                ok, last = self._playbin.query_position(Gst.Format.TIME)
                self._handover = (int(last), time.monotonic()) if ok \
                    else None
                if self._on_gapless is not None:
                    self._on_gapless()
            self._timing()
        elif kind == Gst.MessageType.ASYNC_DONE:
            self._seeking = False
            self._timing()
        elif kind == Gst.MessageType.DURATION_CHANGED:
            self._timing()
        elif kind == Gst.MessageType.STATE_CHANGED \
                and message.src == self._playbin:
            self._timing()
