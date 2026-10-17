"""GstPlayer against generated Ogg/Opus files with a fakesink."""

import threading

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib  # noqa: E402

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
    player = GstPlayer(audio_sink=fakesink(), on_eos=eos.set)
    try:
        player.load(first.as_uri())
        assert player.uri == first.as_uri()
        player.set_volume(0.5)
        player.play()
        player.seek_ns(100_000_000)
        player.set_next_uri(second.as_uri())
        player._finish_gap(player._playbin)
        assert player.uri == second.as_uri()
        player.play()
        assert _run_loop_until(eos)
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
