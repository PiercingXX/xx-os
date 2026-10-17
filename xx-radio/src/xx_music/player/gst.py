"""GStreamer playbin wrapper. Main-loop safe: only non-blocking queries and
property sets here; bus messages arrive on the GLib loop via add_signal_watch.
"""

from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

Gst.init(None)

AUDIO_ONLY_FLAGS = 0x02


class GstPlayer:
    def __init__(self, audio_sink=None,
                 on_eos: Callable[[], None] | None = None,
                 on_error: Callable[[str], None] | None = None,
                 on_about_to_finish: Callable[[], None] | None = None,
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
        self._on_eos = on_eos
        self._on_error = on_error
        self._on_about_to_finish = on_about_to_finish
        self._bus = self._playbin.get_bus()
        self._bus.add_signal_watch()
        self._bus.connect("message", self._on_message)
        self._playbin.connect("about-to-finish", self._finish_gap)

    # -- transport ----------------------------------------------------------
    @property
    def uri(self) -> str:
        return self._uri

    def load(self, uri: str) -> None:
        if uri != self._uri:
            self._playbin.set_state(Gst.State.READY)
            self._playbin.set_property("uri", uri)
            self._uri = uri

    def play(self) -> None:
        self._playbin.set_state(Gst.State.PLAYING)

    def pause(self) -> None:
        self._playbin.set_state(Gst.State.PAUSED)

    def stop(self) -> None:
        self._playbin.set_state(Gst.State.NULL)
        self._uri = ""
        self._next_uri = ""

    def seek_ns(self, position_ns: int) -> None:
        self._playbin.seek_simple(
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
            max(0, position_ns))

    def set_next_uri(self, uri: str) -> None:
        self._next_uri = uri or ""

    def set_volume(self, level: float) -> None:
        self._playbin.set_property(
            "volume", min(1.0, max(0.0, float(level))))

    # -- queries (non-blocking) ----------------------------------------------
    def position_ns(self) -> int | None:
        ok, value = self._playbin.query_position(Gst.Format.TIME)
        return int(value) if ok else None

    def duration_ns(self) -> int | None:
        ok, value = self._playbin.query_duration(Gst.Format.TIME)
        return int(value) if ok else None

    def is_playing(self) -> bool:
        _, state, _ = self._playbin.get_state(0)
        return state == Gst.State.PLAYING

    def shutdown(self) -> None:
        try:
            self._bus.remove_signal_watch()
        finally:
            self._playbin.set_state(Gst.State.NULL)

    # -- bus ------------------------------------------------------------------
    def _finish_gap(self, _playbin) -> None:
        if self._next_uri:
            self._playbin.set_property("uri", self._next_uri)
            self._uri = self._next_uri
            self._next_uri = ""
        if self._on_about_to_finish is not None:
            self._on_about_to_finish()

    def _on_message(self, _bus, message) -> None:
        kind = message.type
        if kind == Gst.MessageType.EOS:
            if self._on_eos is not None:
                self._on_eos()
        elif kind == Gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            if self._on_error is not None:
                self._on_error(str(error))
