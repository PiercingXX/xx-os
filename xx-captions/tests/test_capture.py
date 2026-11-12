from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
import time

import pytest

from xx_captions import audio, capture
from xx_captions.dbus_api import BUS_NAME


def _node(node_id: int, name: str, media_class: str,
          state: str = "running") -> dict:
    return {
        "id": node_id,
        "type": "PipeWire:Interface:Node",
        "info": {
            "state": state,
            "props": {"node.name": name, "media.class": media_class},
        },
    }


def _graph() -> list[dict]:
    return [
        _node(10, "alsa_output.platform-sound.HiFi__hw__0__sink",
              "Audio/Sink"),
        _node(20, "alsa_input.platform-sound.HiFi__hw__0__source",
              "Audio/Source"),
    ]


def test_stream_props_classify_capture_for_privacy_dot() -> None:
    assert capture.STREAM_PROPS == {
        "application.name": "captions",
        "application.id": BUS_NAME,
        "media.role": "Accessibility",
    }


def test_pw_record_argv_tags_stream_and_stays_s16() -> None:
    argv = capture.pw_record_argv_for("alsa_output.x")
    assert argv[:3] == ["pw-record", "--target", "alsa_output.x"]
    assert argv[-1] == "-"
    assert "--media-role" in argv and "Accessibility" in argv
    assert argv.count("-P") == 1
    props = json.loads(argv[argv.index("-P") + 1])
    assert props["stream.capture.sink"] is True
    for key, value in capture.STREAM_PROPS.items():
        assert props[key] == value
    assert "16000" in argv and "s16" in argv
    with pytest.raises(ValueError):
        capture.pw_record_argv_for("  ")


def test_pipewiresrc_props_tag_stream() -> None:
    props = capture.pipewiresrc_props_for("alsa_output.x")
    assert props["target-object"] == "alsa_output.x"
    assert props["stream.capture.sink"] is True
    assert props["application.name"] == "captions"
    assert props["application.id"] == BUS_NAME
    assert props["media.role"] == "Accessibility"


def test_preferred_backend_names_either_option() -> None:
    assert capture.preferred_backend() in ("gstreamer", "pw-record")


def test_pipe_capture_reads_sine_pipe_from_real_child() -> None:
    tone = capture.sine_s16(16000, 0.5)
    program = (
        "import sys; data = sys.stdin.buffer.read(); "
        "sys.stdout.buffer.write(data)"
    )
    got: list[capture.Chunk] = []
    ended = threading.Event()
    proc_input: bytes = tone

    real_popen = subprocess.Popen

    def echo_spawn(argv, **kwargs):
        assert argv[0] == sys.executable
        proc = real_popen(argv, stdin=subprocess.PIPE, **kwargs)
        assert proc.stdin is not None
        proc.stdin.write(proc_input)
        proc.stdin.close()
        return proc

    cap = capture.PipeCapture(
        [sys.executable, "-c", program], "playback",
        got.append, lambda: ended.set(), spawn=echo_spawn)
    cap.start()
    assert ended.wait(timeout=10.0)
    assert cap.wait(timeout=10.0)
    assert got
    assert all(chunk.label == "playback" for chunk in got)
    assert b"".join(chunk.raw for chunk in got) == tone
    cap.stop()


def test_pipe_capture_survives_missing_binary() -> None:
    cap = capture.PipeCapture(["no-such-pw-record-xyz"], "playback",
                              lambda chunk: None)
    with pytest.raises(capture.CaptureError):
        cap.start()


def test_fake_capture_emits_sine_and_stops() -> None:
    got: list[capture.Chunk] = []
    cap = capture.FakeCapture("playback", got.append, pace=False,
                              seconds=0.2)
    cap.start()
    assert cap.wait(timeout=10.0)
    assert got
    assert all(chunk.rate == 16000 and chunk.channels == 1 for chunk in got)
    cap.stop()
    cap.stop()


def test_fake_capture_rejects_double_start() -> None:
    cap = capture.FakeCapture("playback", lambda chunk: None, pace=True,
                              seconds=30.0)
    cap.start()
    try:
        with pytest.raises(capture.CaptureError):
            cap.start()
    finally:
        cap.stop()


def test_capture_set_starts_and_stops_together() -> None:
    first = capture.FakeCapture("downlink", lambda chunk: None, pace=False,
                                seconds=30.0)
    second = capture.FakeCapture("uplink", lambda chunk: None, pace=False,
                                 seconds=30.0)
    group = capture.CaptureSet([first, second])
    group.start()
    try:
        assert group.running
    finally:
        group.stop()
    assert not group.running


def test_call_plan_resolves_fresh_each_call_never_cached() -> None:
    sinks = ["sink-a", "sink-b"]

    def fake_sink() -> str:
        return sinks.pop(0)

    def fake_graph() -> list[dict]:
        return [
            _node(1, "sink-a", "Audio/Sink"),
            _node(3, "sink-b", "Audio/Sink"),
            _node(5, "mic", "Audio/Source"),
        ]

    resolver = capture.CallPlanResolver(
        get_sink=fake_sink, get_source=lambda: "mic",
        get_graph=fake_graph)
    first = resolver.resolve()
    second = resolver.resolve()
    assert first["downlink"].node == "sink-a"
    assert second["downlink"].node == "sink-b"
    assert first["uplink"].node == "mic"


def test_call_plan_refuses_half_a_call() -> None:
    graph = [_node(1, "sink-a", "Audio/Sink")]
    resolver = capture.CallPlanResolver(
        get_sink=lambda: "sink-a", get_source=lambda: "",
        get_graph=lambda: graph)
    assert resolver.resolve() == {}
    resolver2 = capture.CallPlanResolver(
        get_sink=lambda: "missing", get_source=lambda: "mic",
        get_graph=lambda: graph)
    assert resolver2.resolve() == {}


def test_call_plan_prefers_pactl_source_but_survives_without() -> None:
    resolver = capture.CallPlanResolver(
        get_sink=lambda: "alsa_output.platform-sound.HiFi__hw__0__sink",
        get_source=lambda: "",
        get_graph=_graph)
    plan = resolver.resolve()
    assert plan["uplink"].node == "alsa_input.platform-sound.HiFi__hw__0__source"


def test_default_sink_never_numeric_and_empty_on_failure() -> None:
    assert capture.default_sink_from_pactl(
        run=lambda argv: "  42\n") == "42"
    assert capture.default_sink_from_pactl(
        run=lambda argv: "alsa_output.x\n") == "alsa_output.x"

    def boom(argv):
        raise OSError("no pactl")

    assert capture.default_sink_from_pactl(run=boom) == ""
    assert capture.default_source_from_pactl(run=boom) == ""
    assert capture.graph_from_pw_dump(run=boom) == []


def test_route_tracker_reports_each_switch_once() -> None:
    plans = [
        {"playback": audio.CaptureTarget("sink-a", "system")},
        {"playback": audio.CaptureTarget("sink-a", "system")},
        {"playback": audio.CaptureTarget("sink-b", "system")},
    ]
    tracker = capture.RouteTracker(lambda: plans.pop(0))
    _, changed = tracker.check()
    assert changed is False
    _, changed = tracker.check()
    assert changed is False
    _, changed = tracker.check()
    assert changed is True


def test_mix_mono_averages_and_truncates() -> None:
    assert capture.mix_mono([1.0, 3.0], [3.0, 1.0]) == [2.0, 2.0]
    assert capture.mix_mono([1.0, 1.0, 1.0], [3.0]) == [2.0]


def test_sine_is_valid_s16_mono() -> None:
    raw = capture.sine_s16(16000, 0.1, channels=1)
    assert len(raw) == 1600 * 2
    stream = io.BytesIO(raw)
    assert len(stream.read()) == 3200


def test_chunk_reader_decodes_pipe_bytes() -> None:
    from xx_captions import resample

    raw = capture.sine_s16(48000, 0.2, channels=2)
    samples = resample.pcm16_to_mono_float32_16k(raw, 48000, 2)
    assert len(samples) == 3200
    assert max(abs(v) for v in samples) > 0.5


def test_pipe_capture_reader_thread_ends_on_eof() -> None:
    stream = io.BytesIO(capture.sine_s16(16000, 0.3))
    ended = threading.Event()

    class _Proc:
        stdout = stream

        def terminate(self) -> None:
            pass

        def wait(self, timeout: float = 5.0) -> int:
            return 0

    got: list[capture.Chunk] = []
    cap = capture.PipeCapture(["pw-record"], "playback", got.append,
                              lambda: ended.set(),
                              spawn=lambda argv, **kw: _Proc())
    start = time.monotonic()
    cap.start()
    assert ended.wait(timeout=10.0)
    assert time.monotonic() - start < 10.0
    assert b"".join(chunk.raw for chunk in got) == stream.getvalue()
