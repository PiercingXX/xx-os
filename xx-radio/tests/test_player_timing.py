"""The window's seek bar against the real pipeline, end to end.

A real PlayerService plays generated songs in real time (a clock-synced
fakesink) on a running GLib loop; every Changed snapshot it emits feeds
a PlayerModel exactly as the window's D-Bus backend does. Sampled every
20 ms, the model's position must match what the pipeline is playing,
through a start, a seek, a pause and resume, a skip and a gapless
hand-over.
"""

import json
import time

from gi.repository import GLib

from xx_music.fake_engine import FakeEngine
from xx_music.player.service import InlineExecutor, PlayerService
from xx_music.ui.player import PlayerModel
from xx_radio.main import SPEC

from player_fakes import FakeProxy, bake_ogg, fakesink, make_track

TOLERANCE_S = 0.06


class Bus:
    """Hands each Changed snapshot to the model, as DbusBackend does."""

    def __init__(self, model: PlayerModel) -> None:
        self.model = model

    def register_object(self, *_args):
        return 1

    def emit_signal(self, _dest, _path, _iface, signal, params):
        if signal == "Changed":
            self.model.apply_snapshot(json.loads(params.unpack()[0]))
        return True

    def call(self, *_args):
        return 0


def test_window_position_follows_the_pipeline(tmp_path):
    ids = ("tr-001", "tr-002", "tr-003")
    for track_id in ids:
        bake_ogg(tmp_path / f"{track_id}.ogg", seconds=2.0)
    sink = fakesink()
    sink.set_property("sync", True)
    service = PlayerService(
        SPEC, FakeEngine(media_dir=tmp_path),
        FakeProxy({t: tmp_path / f"{t}.ogg" for t in ids}),
        paths=SPEC.paths(), executor=InlineExecutor(), audio_sink=sink,
        own_name=lambda *_a: 1)
    model = PlayerModel()
    service.attach(Bus(model))
    service.start_timers()
    loop = GLib.MainLoop()
    began = time.monotonic()
    errors: list[tuple[float, str, float]] = []
    heard: list[str] = []

    def sample() -> bool:
        track = model.track
        loaded = service._loaded_id
        position = service.player.position_ns()
        if track is not None and track.id == loaded and position is not None:
            if not heard or heard[-1] != loaded:
                heard.append(loaded)
            errors.append((time.monotonic() - began, loaded,
                           model.position_s - position / 1e9))
        return True

    script = [
        (0.0, lambda: service.play([make_track(t, duration_s=2.0)
                                    for t in ids])),
        (0.5, lambda: service.seek_us(1_200_000)),
        (0.7, service.pause),
        (1.1, service.resume),
        (1.4, service.next),
        # tr-002 runs out and tr-003 follows gaplessly.
        (4.0, loop.quit),
    ]
    for at, action in script:
        GLib.timeout_add(int(at * 1000),
                         lambda action=action: (action(), False)[1])
    GLib.timeout_add(20, sample)
    try:
        loop.run()
    finally:
        service.request_stop()
    assert heard == list(ids)
    worst = max(errors, key=lambda e: abs(e[2]))
    assert abs(worst[2]) < TOLERANCE_S, worst
