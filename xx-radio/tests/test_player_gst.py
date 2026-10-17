"""GstPlayer against generated Ogg/Opus files with a fakesink."""

import threading
import time

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

from xx_music.player.gst import GstPlayer  # noqa: E402

from player_fakes import bake_ogg, fakesink  # noqa: E402


def _run_loop_until(event: threading.Event, timeout_s: float = 15.0) -> bool:
    loop = GLib.MainLoop()
    GLib.timeout_add(int(timeout_s * 1000), loop.quit)
    thread = threading.Thread(target=loop.run, daemon=True)
    thread.start()
    done = event.wait(timeout_s + 2.0)
    loop.quit()
    thread.join(timeout=10.0)
    return done


def test_fakesink_playback_reaches_eos(tmp_path):
    ogg = bake_ogg(tmp_path / "song.ogg")
    eos = threading.Event()
    finished = threading.Event()
    player = GstPlayer(audio_sink=fakesink(), on_eos=eos.set,
                       on_about_to_finish=finished.set)
    try:
        player.load(ogg.as_uri())
        player.play()
        assert _run_loop_until(eos)
        assert player.duration_ns() and player.duration_ns() > 0
        assert player.position_ns() is not None
    finally:
        player.shutdown()


def test_seek_volume_and_gapless_uri(tmp_path):
    first = bake_ogg(tmp_path / "a.ogg", freq=440)
    second = bake_ogg(tmp_path / "b.ogg", freq=660)
    eos = threading.Event()
    gapless: list[str] = []
    player = GstPlayer(audio_sink=fakesink(), on_eos=eos.set,
                       on_gapless=lambda: gapless.append(player.uri))
    try:
        player.load(first.as_uri())
        assert player.uri == first.as_uri()
        player.set_volume(0.5)
        player.play()
        player.seek_ns(100_000_000)
        player.set_next_uri(second.as_uri())
        player._finish_gap(player._playbin)
        # Handed over, not heard yet: the uri is the song playing out.
        assert player.uri == first.as_uri()
        player.play()
        assert _run_loop_until(eos)
        assert gapless == [second.as_uri()]
        assert player.uri == second.as_uri()
    finally:
        player.shutdown()


def _synced_sink():
    sink = fakesink()
    sink.set_property("sync", True)
    return sink


def test_gapless_next_song_reported_when_it_plays_out(tmp_path):
    """The queued song becomes current at its stream-start, when it is
    heard (not when the first one finished downloading, which for a file
    is at once), once, and starts at 0 (the query still answers the old
    song's end for a moment)."""
    first = bake_ogg(tmp_path / "a.ogg", seconds=0.5, freq=440)
    second = bake_ogg(tmp_path / "b.ogg", seconds=0.5, freq=660)
    eos = threading.Event()
    starts: list[tuple[str, int | None, float]] = []
    player = GstPlayer(audio_sink=_synced_sink(), on_eos=eos.set)
    player._on_gapless = lambda: starts.append(
        (player.uri, player.position_ns(), time.monotonic()))
    try:
        player.load(first.as_uri())
        player.set_next_uri(second.as_uri())
        began = time.monotonic()
        player.play()
        # The first song is read to its end before the loop runs: its
        # own stream-start must not count as the hand-over.
        time.sleep(0.1)
        assert _run_loop_until(eos)
        assert [uri for uri, _, _ in starts] == [second.as_uri()]
        _uri, position, at = starts[0]
        assert position is not None and position < 50_000_000
        assert at - began > 0.3
    finally:
        player.shutdown()


def test_seek_lands_exactly_and_hides_the_flush(tmp_path):
    """ACCURATE seeks land on the asked position (KEY_UNIT lands on the
    previous sync point, the source of a seek bar that is off); while the
    seek flushes the position is unknown, not the old one."""
    song = bake_ogg(tmp_path / "long.ogg", seconds=6.0)
    landed = threading.Event()
    player = GstPlayer(audio_sink=_synced_sink())
    seen: dict = {}

    def on_timing() -> None:
        if seen.get("asked") and not player._seeking \
                and "landed" not in seen:
            seen["landed"] = player.position_ns()
            landed.set()

    player._on_timing = on_timing
    try:
        player.load(song.as_uri())
        player.pause()
        assert player._playbin.get_state(10 * Gst.SECOND)[1] == \
            Gst.State.PAUSED
        assert player.seek_ns(3_300_000_000)
        seen["asked"] = True
        assert player.position_ns() is None
        assert not player.advancing()
        assert _run_loop_until(landed)
        assert abs(seen["landed"] - 3_300_000_000) < 20_000_000
    finally:
        player.shutdown()


def test_error_callback_reports_decode_failure(tmp_path):
    missing = tmp_path / "missing.ogg"
    errors: list[str] = []
    done = threading.Event()

    def on_error(message: str) -> None:
        errors.append(message)
        done.set()

    player = GstPlayer(audio_sink=fakesink(), on_error=on_error)
    try:
        player.load(missing.as_uri())
        player.play()
        assert _run_loop_until(done)
        assert errors
    finally:
        player.shutdown()
