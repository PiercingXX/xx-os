"""Shared doubles for player tests: fake clock, file:// proxy, Ogg baking.

The fake proxy implements the radio_m2 StreamProxy contract surface the
player uses (url_for/is_cached) over generated Ogg/Opus files. Everything
is file:// so the conftest network guard stays green.
"""

from __future__ import annotations

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

Gst.init(None)

from pathlib import Path  # noqa: E402

from xx_music import engine as e  # noqa: E402


class FakeClock:
    def __init__(self) -> None:
        self.now = 5000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeProxy:
    """url_for(track_id, resolve) -> str; is_cached(track_id) -> bool."""

    def __init__(self, files: dict[str, Path]) -> None:
        self.files = dict(files)
        self.resolve_calls: list[str] = []
        self.upstream_calls: list[str] = []
        self.cached: set[str] = set(files)
        self.fail: dict[str, e.EngineError] = {}

    def url_for(self, track_id: str, resolve) -> str:
        self.resolve_calls.append(track_id)
        if track_id in self.fail:
            raise self.fail.pop(track_id)
        source = resolve()
        self.upstream_calls.append(track_id)
        path = self.files.get(track_id)
        if path is None:
            return source.uri
        return path.as_uri()

    def is_cached(self, track_id: str) -> bool:
        return track_id in self.cached


def bake_ogg(path: Path, seconds: float = 0.6, freq: int = 440) -> Path:
    """Render a short Opus-in-Ogg file with GStreamer (bounded wait)."""
    buffers = max(4, int(seconds * 44100 / 1024))
    pipe = Gst.parse_launch(
        f"audiotestsrc num-buffers={buffers} freq={freq} ! audioconvert "
        f"! audioresample ! opusenc ! oggmux ! filesink location={path}")
    bus = pipe.get_bus()
    try:
        pipe.set_state(Gst.State.PLAYING)
        message = bus.timed_pop_filtered(
            30 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
        assert message is not None, "ogg render timed out"
        assert message.type == Gst.MessageType.EOS, \
            f"ogg render failed: {message.type}"
    finally:
        pipe.set_state(Gst.State.NULL)
    assert path.is_file() and path.stat().st_size > 0
    return path


def make_track(track_id: str, title: str | None = None,
               artist: str = "Singer", artist_id: str = "ar-1",
               duration_s: float = 0.6) -> e.Track:
    return e.Track(id=track_id, title=title or track_id,
                   artists=(e.ArtistRef(artist, artist_id),),
                   album=e.AlbumRef("Album", "al-1"),
                   duration_s=duration_s)


def fakesink():
    return Gst.ElementFactory.make("fakesink", None)
