"""PipeWire capture: process plumbing around the pure plan in audio.py.

Live captions capture the default sink's monitor; call transcription
captures the voice-call sink monitor (downlink) plus the default
source (uplink). Every stream is capture-only and carries honest
properties (application.name/id, media.role=Accessibility) so the
shell privacy monitor classifies it as mic use.

Only this module spawns capture processes. Readers run on their own
threads and push raw s16 bytes at the service; the service feeds its
engine from exactly one worker thread. Nothing here touches the GTK
main loop; pactl/pw-dump/pw-record run on worker threads only.
"""
from __future__ import annotations

import json
import math
import struct
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from xx_captions import audio
from xx_captions.dbus_api import BUS_NAME

APP_NAME = "captions"
APP_ID = BUS_NAME
MEDIA_ROLE = "Accessibility"

STREAM_PROPS = {
    "application.name": APP_NAME,
    "application.id": APP_ID,
    "media.role": MEDIA_ROLE,
}

CHUNK_MS = 100


class CaptureError(RuntimeError):
    pass


def pw_record_argv_for(target: str,
                       rate: int = audio.ENGINE_RATE_HZ) -> list[str]:
    """pw-record argv for one sink monitor, tagged for the privacy monitor.

    The monitor selector stays merged in the single `-P` properties
    object: a second `-P`/`--properties` flag would replace it and the
    capture would no longer read the sink's monitor.
    """
    base = audio.pw_record_argv(target, rate, STREAM_PROPS)
    head, tail = base[:-1], base[-1:]
    assert tail == ["-"]
    return head + ["--media-role", MEDIA_ROLE] + tail


def pipewiresrc_props_for(target: str,
                          rate: int = audio.ENGINE_RATE_HZ) -> dict:
    """GStreamer pipewiresrc properties for one node, tagged likewise."""
    props = dict(audio.pipewiresrc_caps(target, rate))
    props["application.name"] = APP_NAME
    props["application.id"] = APP_ID
    props["media.role"] = MEDIA_ROLE
    return props


def has_gstreamer() -> bool:
    """True when GStreamer pipewiresrc capture is available in-process."""
    try:
        import gi  # noqa: F401

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst  # noqa: F401

        return Gst.registry_get().find_plugin("pipewire") is not None
    except Exception:
        return False


def preferred_backend() -> str:
    """'gstreamer' when pipewiresrc exists, else the pw-record child."""
    return "gstreamer" if has_gstreamer() else "pw-record"


def _run_lines(argv: list[str]) -> str:
    proc = subprocess.run(argv, capture_output=True, text=True,
                          timeout=10)
    if proc.returncode != 0:
        raise CaptureError(f"{' '.join(argv)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def default_sink_from_pactl(run=None) -> str:
    """Current default sink name; '' when pactl cannot tell us.

    Never a numeric id: under pipewire-pulse ids shift between boots.
    Worker threads only: this blocks on a child process.
    """
    run = run or _run_lines
    try:
        return run(["pactl", "get-default-sink"]).strip()
    except Exception:
        return ""


def default_source_from_pactl(run=None) -> str:
    """Current default source name; '' when pactl cannot tell us."""
    run = run or _run_lines
    try:
        return run(["pactl", "get-default-source"]).strip()
    except Exception:
        return ""


def graph_from_pw_dump(run=None) -> list[dict]:
    """Live PipeWire graph via pw-dump; [] when unreachable."""
    run = run or _run_lines
    try:
        parsed = json.loads(run(["pw-dump"]))
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def _running_named_source(nodes: list[dict], name: str) -> str | None:
    node = audio.find_node_by_name(nodes, name) if name else None
    if node is None:
        return None
    info = node.get("info")
    if not isinstance(info, dict):
        return None
    if str(info.get("state", "")).lower() != "running":
        return None
    return name


def resolve_playback(default_sink: str,
                     nodes: list[dict]) -> dict[str, audio.CaptureTarget]:
    """Monitor of the given sink; {} when the graph cannot supply it."""
    return audio.resolve_capture(nodes, default_sink, in_call=False)


class CallPlanResolver:
    """Fresh per-call downlink+uplink resolution, never cached.

    callaudiod switches profiles underneath us, so every call
    re-reads the current default sink (for the downlink monitor) and
    the current default source (for the uplink mic). A stored plan
    from a previous call is never reused.
    """

    def __init__(self, get_sink=None, get_source=None,
                 get_graph=None) -> None:
        self._get_sink = get_sink or default_sink_from_pactl
        self._get_source = get_source or default_source_from_pactl
        self._get_graph = get_graph or graph_from_pw_dump

    def resolve(self) -> dict[str, audio.CaptureTarget]:
        """Current call plan; {} when either side is missing.

        Refuses half a call: no downlink or no uplink means no session.
        """
        sink = self._get_sink()
        pactl_source = self._get_source()
        nodes = self._get_graph()
        downlink = audio.find_monitor_for_sink(nodes, sink)
        uplink = _running_named_source(nodes, pactl_source)
        if uplink is None:
            uplink = audio.default_source(nodes)
        if not downlink or not uplink:
            return {}
        return {
            "downlink": audio.CaptureTarget(downlink, "far end"),
            "uplink": audio.CaptureTarget(uplink, "mic"),
        }


def plan_signature(plan: dict[str, audio.CaptureTarget]) -> tuple:
    """Comparable shape of a plan: sorted (role, node) pairs."""
    return tuple(sorted((role, target.node)
                        for role, target in plan.items()))


class RouteTracker:
    """Notices mid-call audio-route changes (speaker/Bluetooth switches).

    Poll the current plan through resolve(); changed() is True once per
    switch so the service can restart capture and note the gap in the
    transcript instead of misattributing voices to the wrong side.
    """

    def __init__(self, resolve: Callable[[], dict]) -> None:
        self._resolve = resolve
        self._last: tuple | None = None

    def check(self) -> tuple[dict, bool]:
        """(plan, changed-since-last-check). First call never reports."""
        plan = self._resolve()
        signature = plan_signature(plan)
        if self._last is None:
            self._last = signature
            return plan, False
        changed = signature != self._last
        self._last = signature
        return plan, changed


def sine_s16(rate: int, seconds: float, freq: float = 440.0,
             channels: int = 1, amplitude: int = 30000) -> bytes:
    """Raw s16 test tone; the fake source behind capture tests."""
    frames = int(rate * seconds)
    out = bytearray()
    for i in range(frames):
        value = int(amplitude * math.sin(2 * math.pi * freq * i / rate))
        out += struct.pack("<h", value) * channels
    return bytes(out)


def mix_mono(first: list[float], second: list[float]) -> list[float]:
    """Sample-wise mean of two mono chunks (call downlink+uplink)."""
    length = min(len(first), len(second))
    return [(first[i] + second[i]) / 2.0 for i in range(length)]


@dataclass
class Chunk:
    raw: bytes
    rate: int
    channels: int
    label: str


class PipeCapture:
    """One pw-record child streaming raw s16 to a reader thread.

    on_bytes receives Chunk values; on_end fires once at EOF or error.
    The reader thread never blocks the caller: start() returns once the
    child is spawned, stop() is idempotent.
    """

    def __init__(self, argv: list[str], label: str,
                 on_bytes: Callable[[Chunk], None],
                 on_end: Callable[[], None] | None = None,
                 rate: int = audio.ENGINE_RATE_HZ, channels: int = 1,
                 chunk_ms: int = CHUNK_MS,
                 spawn=None) -> None:
        if not argv:
            raise CaptureError("capture needs a subprocess argv")
        self._argv = list(argv)
        self._label = label
        self._on_bytes = on_bytes
        self._on_end = on_end
        self._rate = rate
        self._channels = channels
        self._chunk_ms = chunk_ms
        self._spawn = spawn or subprocess.Popen
        self._lock = threading.Lock()
        self._proc = None
        self._thread: threading.Thread | None = None
        self._stopped = False

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def label(self) -> str:
        return self._label

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                raise CaptureError("capture already running")
            try:
                self._proc = self._spawn(
                    self._argv, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL)
            except OSError as exc:
                raise CaptureError(
                    f"cannot start {' '.join(self._argv)}: {exc}") from exc
            self._stopped = False
            self._thread = threading.Thread(target=self._read,
                                            daemon=True)
            self._thread.start()

    def _read(self) -> None:
        try:
            stream = self._proc.stdout
            frames = self._rate * self._chunk_ms // 1000
            want = frames * self._channels * 2
            while True:
                with self._lock:
                    if self._stopped:
                        return
                try:
                    data = stream.read(want) if stream else b""
                except (OSError, ValueError):
                    return
                if not data:
                    return
                try:
                    self._on_bytes(Chunk(data, self._rate,
                                         self._channels, self._label))
                except Exception:
                    return
        finally:
            with self._lock:
                self._thread = None
            if self._on_end is not None:
                try:
                    self._on_end()
                except Exception:
                    pass

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            proc = self._proc
            self._thread = None
            self._proc = None
            self._stopped = True
        if proc is not None:
            try:
                proc.terminate()
            except (OSError, AttributeError):
                pass
            try:
                proc.wait(timeout=5.0)
            except Exception:
                try:
                    proc.kill()
                except (OSError, AttributeError):
                    pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)

    def wait(self, timeout: float = 5.0) -> bool:
        with self._lock:
            thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=timeout)
        return not thread.is_alive()


class FakeCapture:
    """In-process sine generator behind the same start/stop contract.

    Test hook (and PipeWire-less smoke): emits sine_s16 chunks on its
    own thread until stop() or the second budget runs out.
    """

    def __init__(self, label: str, on_bytes: Callable[[Chunk], None],
                 on_end: Callable[[], None] | None = None,
                 rate: int = audio.ENGINE_RATE_HZ, chunk_ms: int = CHUNK_MS,
                 freq: float = 440.0, seconds: float | None = None,
                 pace: bool = True) -> None:
        self._label = label
        self._on_bytes = on_bytes
        self._on_end = on_end
        self._rate = rate
        self._chunk_ms = chunk_ms
        self._freq = freq
        self._seconds = seconds
        self._pace = pace
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stopped = False

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                raise CaptureError("capture already running")
            self._stopped = False
            self._thread = threading.Thread(target=self._emit,
                                            daemon=True)
            self._thread.start()

    def _emit(self) -> None:
        try:
            tone = sine_s16(self._rate, self._chunk_ms / 1000.0,
                            self._freq)
            start = time.monotonic()
            while True:
                with self._lock:
                    if self._stopped:
                        return
                if (self._seconds is not None
                        and time.monotonic() - start >= self._seconds):
                    return
                try:
                    self._on_bytes(Chunk(bytes(tone), self._rate, 1,
                                         self._label))
                except Exception:
                    return
                if self._pace:
                    time.sleep(self._chunk_ms / 1000.0)
        finally:
            with self._lock:
                self._thread = None
            if self._on_end is not None:
                try:
                    self._on_end()
                except Exception:
                    pass

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            self._thread = None
            self._stopped = True
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)

    def wait(self, timeout: float = 5.0) -> bool:
        with self._lock:
            thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=timeout)
        return not thread.is_alive()


class CaptureSet:
    """One start/stop handle over a session's reader(s)."""

    def __init__(self, captures: list) -> None:
        self._captures = list(captures)

    @property
    def running(self) -> bool:
        return any(cap.running for cap in self._captures)

    def start(self) -> None:
        started: list = []
        try:
            for cap in self._captures:
                cap.start()
                started.append(cap)
        except Exception:
            for cap in started:
                try:
                    cap.stop()
                except Exception:
                    pass
            raise

    def stop(self) -> None:
        for cap in self._captures:
            try:
                cap.stop()
            except Exception:
                pass


__all__ = [
    "APP_ID",
    "APP_NAME",
    "CHUNK_MS",
    "MEDIA_ROLE",
    "STREAM_PROPS",
    "CallPlanResolver",
    "CaptureError",
    "CaptureSet",
    "Chunk",
    "FakeCapture",
    "PipeCapture",
    "RouteTracker",
    "default_sink_from_pactl",
    "default_source_from_pactl",
    "graph_from_pw_dump",
    "has_gstreamer",
    "mix_mono",
    "pipewiresrc_props_for",
    "plan_signature",
    "preferred_backend",
    "pw_record_argv_for",
    "resolve_playback",
    "sine_s16",
]