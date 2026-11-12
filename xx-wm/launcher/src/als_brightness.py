from __future__ import annotations

import math
import queue
import subprocess
import threading
import weakref
from pathlib import Path
from typing import Callable

import backlight

from gi.repository import GLib

from shell_log import get_logger

_log = get_logger('brightness')

_LOGIN1 = 'org.freedesktop.login1'
_LOGIN1_SESSION = '/org/freedesktop/login1/session/auto'
_LOGIN1_SESSION_IFACE = 'org.freedesktop.login1.Session'

_failure_logged = False

# Display blank state, mirrored by display_manager: every live ALS
# instance pauses while the panel is dark.
_display_blanked = False
_INSTANCES: weakref.WeakSet = weakref.WeakSet()


def set_display_blanked(blanked: bool) -> None:
    """Pause (True) or resume (False) automatic brightness everywhere."""
    global _display_blanked
    blanked = bool(blanked)
    if blanked == _display_blanked:
        return
    _display_blanked = blanked
    for als in list(_INSTANCES):
        try:
            als._on_display_blanked(blanked)
        except Exception:
            _log.exception('ALS pause/resume failed')


def _note_failure(message: str, *args) -> None:
    """Log a backend failure once per process — the ALS poll and the shade
    slider would otherwise log the same EACCES every few seconds."""
    global _failure_logged
    if not _failure_logged:
        _failure_logged = True
        _log.warning(message, *args)

# Ambient light sensor via Linux IIO subsystem.
# Kernel path: /sys/bus/iio/devices/iio:deviceN/in_illuminance_input (lux)
# Verify the FP5's sensor node with: ls /sys/bus/iio/devices/
# Backlight: backlight.find() (backlight class, else an allow-listed panel LED)

_IIO_GLOB = '/sys/bus/iio/devices/iio:device*/in_illuminance_input'
_POLL_INTERVAL_MS = 2000
_CHANGE_THRESHOLD = 0.08  # apply only when the level moves by >= 8%
_SMOOTHING = 0.5          # EMA weight of a new (median) log-lux sample
_MEDIAN_WINDOW = 3        # a single odd sample never wins a median of 3
_RAMP_STEPS = 8
_RAMP_STEP_MS = 60

_LUX_CURVE: list[tuple[float, float]] = [
    (0.0,    0.02),
    (5.0,    0.05),
    (50.0,   0.15),
    (200.0,  0.35),
    (600.0,  0.60),
    (1500.0, 0.80),
    (5000.0, 1.00),
]


def _lux_to_ratio(lux: float) -> float:
    """Map ambient lux to a 0.0–1.0 brightness ratio using a log-ish curve."""
    lux = max(0.0, lux)
    for i in range(len(_LUX_CURVE) - 1):
        lx0, r0 = _LUX_CURVE[i]
        lx1, r1 = _LUX_CURVE[i + 1]
        if lux <= lx1:
            t = (lux - lx0) / (lx1 - lx0)
            return r0 + t * (r1 - r0)
    return _LUX_CURVE[-1][1]


def _backlight_path() -> Path | None:
    found = backlight.find()
    return found.path if found is not None else None


def _backlight_subsystem(path: Path | None) -> str:
    """login1 SetBrightness subsystem for a .../brightness node."""
    return 'leds' if path is not None and path.parent.parent.name == 'leds' \
        else 'backlight'


def _find_path(glob: str) -> Path | None:
    matches = list(Path('/').glob(glob.lstrip('/')))
    return matches[0] if matches else None


def _backlight_name(sysfs_path: Path | None = None) -> str | None:
    """Kernel backlight device name (e.g. intel_backlight) for login1."""
    path = sysfs_path if sysfs_path is not None else _backlight_path()
    return path.parent.name if path is not None else None


class SerialWorker:
    """One lazily started daemon thread running jobs in submission order.

    Backlight, wlopm and pactl calls block (a process exec, a logind round
    trip), so they never run on the GTK loop; one thread keeps them in
    order, so a blank's 'set 0' can't overtake an earlier ALS step or be
    overtaken by a later wake."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._jobs: queue.SimpleQueue = queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def submit(self, job: Callable[[], object]) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run, name=self._name, daemon=True)
                self._thread.start()
        self._jobs.put(job)

    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            try:
                job()
            except Exception:
                _log.exception('%s job failed', self._name)


_BACKLIGHT_IO = SerialWorker('backlight-io')


def run_backlight_io(job: Callable[[], object]) -> None:
    """Run a blocking backlight job off the main loop, in order with every
    other backlight write (the display manager's blank/wake included)."""
    _BACKLIGHT_IO.submit(job)


def _brightnessctl_set(value: int, device: str | None = None) -> bool:
    if device is None:
        # Bare brightnessctl picks the first device it finds, which on the
        # FLX1 is the vibrator LED, not the panel.
        return False
    try:
        cmd = ['brightnessctl', '--device', device]
        result = subprocess.run(
            cmd + ['set', str(max(0, int(value)))],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False


def login1_set_brightness(name: str, value: int, bus=None,
                          subsystem: str = 'backlight') -> bool:
    """Set an absolute backlight value through logind's Session interface
    (works unprivileged via the session object, elogind included)."""
    try:
        from gi.repository import Gio, GLib
        conn = bus if bus is not None else Gio.bus_get_sync(
            Gio.BusType.SYSTEM, None)
        conn.call_sync(
            _LOGIN1, _LOGIN1_SESSION, _LOGIN1_SESSION_IFACE, 'SetBrightness',
            GLib.Variant('(ssu)', (subsystem, name, max(0, int(value)))),
            None, Gio.DBusCallFlags.NONE, 5000, None,
        )
        return True
    except Exception:
        return False


def _sysfs_set_brightness(value: int, sysfs_path: Path | None = None) -> bool:
    """Last resort: direct sysfs write, which fails with EACCES for the
    unprivileged shell user unless udev rules grant access."""
    path = sysfs_path if sysfs_path is not None else _backlight_path()
    if path is None:
        return False
    try:
        path.write_text(str(max(0, int(value))))
        return True
    except (OSError, ValueError):
        return False


def apply_backlight(value: int, device: str | None = None,
                    sysfs_path: Path | None = None) -> bool:
    """Best-effort absolute backlight write: brightnessctl first, logind's
    Session.SetBrightness next, sysfs only as a last resort. Failures are
    logged once; returns True when any backend applied the value."""
    value = max(0, int(value))
    if device is None:
        device = _backlight_name(sysfs_path)
    if _brightnessctl_set(value, device):
        return True
    if device is not None and login1_set_brightness(
            device, value,
            subsystem=_backlight_subsystem(sysfs_path or _backlight_path())):
        return True
    if _sysfs_set_brightness(value, sysfs_path):
        return True
    _note_failure('backlight write failed (brightnessctl, logind, sysfs); '
                  'value %d dropped', value)
    return False


def apply_backlight_pct(pct: int) -> bool:
    """Percent-based wrapper for the shade slider, queued on the backlight
    worker (it falls through brightnessctl and a logind round trip)."""
    pct = max(0, min(100, int(pct)))
    run_backlight_io(lambda: apply_backlight_pct_sync(pct))
    return True


def apply_backlight_pct_sync(pct: int) -> bool:
    """Blocking body of apply_backlight_pct (worker thread only): resolves
    the absolute scale from sysfs max_brightness (reads are unprivileged)."""
    pct = max(0, min(100, int(pct)))
    path = _backlight_path()
    maximum: int | None = None
    if path is not None:
        try:
            maximum = int((path.parent / 'max_brightness').read_text().strip())
        except (OSError, ValueError):
            maximum = None
    if maximum:
        return apply_backlight(max(1, round(pct * maximum / 100)),
                               device=path.parent.name if path else None,
                               sysfs_path=path)
    name = _backlight_name(path)
    if _brightnessctl_set(pct, name):
        return True
    if name is not None and login1_set_brightness(
            name, pct, subsystem=_backlight_subsystem(path)):
        return True
    _note_failure('backlight write failed (brightnessctl, logind); '
                  'no readable max_brightness for %d%%', pct)
    return False


class ALSBrightness:
    """
    Reads ambient light sensor via IIO sysfs and adjusts display brightness.
    Enable/disable with `start()` / `stop()`.
    Falls back silently if sensor or backlight sysfs node is absent.

    Paused while the display is blanked (set_display_blanked): a write
    then would light a panel that is meant to be dark. Readings are
    filtered (median of the last three, then an EMA in log-lux) and a new
    level applies as a short ramp, so a hand passing over the sensor never
    flickers the screen.
    """

    def __init__(self) -> None:
        self._sensor_path: Path | None = _find_path(_IIO_GLOB)
        self._backlight_path: Path | None = _backlight_path()
        self._max_brightness: int = self._read_max()
        self._last_ratio: float = -1.0
        self._timer_id: int | None = None
        self._enabled: bool = False
        self._log_lux: float | None = None
        self._samples: list[float] = []
        self._ramp_src: int | None = None
        self._ramp_steps: list[int] = []
        self._current: int | None = None
        self._write_lock = threading.Lock()
        self._write_value_pending: int | None = None
        _INSTANCES.add(self)

    def _read_max(self) -> int:
        if self._backlight_path is None:
            return 255
        max_path = self._backlight_path.parent / 'max_brightness'
        try:
            value = int(max_path.read_text().strip())
        except (OSError, ValueError):
            return 255
        # Some backlights report 0 at boot; a 0..0 range means the node is not
        # ready, so fall back to a sane default rather than writing nonsense.
        return value if value > 0 else 255

    def available(self) -> bool:
        return self._sensor_path is not None and self._backlight_path is not None

    def start(self) -> None:
        if self._enabled:
            return
        self._enabled = True
        self._resume()

    def stop(self) -> None:
        self._enabled = False
        self._pause()

    def _resume(self) -> None:
        """Start polling from a fresh reading (the first one applies at
        once; the level may have changed while paused)."""
        if not self._enabled or _display_blanked:
            return
        self._log_lux = None
        self._samples = []
        self._last_ratio = -1.0
        self._current = None
        self._poll()
        if self._timer_id is None and self._enabled:
            self._timer_id = GLib.timeout_add(_POLL_INTERVAL_MS, self._poll)

    def _pause(self) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        self._cancel_ramp()

    def _on_display_blanked(self, blanked: bool) -> None:
        if blanked:
            self._pause()
        else:
            self._resume()

    def _poll(self) -> bool:
        if not self._enabled or _display_blanked:
            self._timer_id = None
            return False
        lux = self._read_lux()
        if lux is None:
            return True
        sample = math.log1p(max(0.0, lux))
        if not self._samples:
            self._samples = [sample] * _MEDIAN_WINDOW
        else:
            self._samples = self._samples[1:] + [sample]
        median = sorted(self._samples)[_MEDIAN_WINDOW // 2]
        self._log_lux = median if self._log_lux is None else (
            self._log_lux + _SMOOTHING * (median - self._log_lux))
        ratio = _lux_to_ratio(math.expm1(self._log_lux))
        if abs(ratio - self._last_ratio) < _CHANGE_THRESHOLD:
            return True
        self._last_ratio = ratio
        self._ramp_to(max(1, round(ratio * self._max_brightness)))
        return True

    def _read_lux(self) -> float | None:
        if self._sensor_path is None:
            return None
        try:
            return float(self._sensor_path.read_text().strip())
        except (OSError, ValueError):
            return None

    def _read_current(self) -> int | None:
        if self._current is not None:
            return self._current
        if self._backlight_path is None:
            return None
        try:
            return int(self._backlight_path.read_text().strip())
        except (OSError, ValueError):
            return None

    def _ramp_to(self, target: int) -> None:
        self._cancel_ramp()
        start = self._read_current()
        if start is None or start == target:
            self._ramp_steps = [target]
        else:
            n = _RAMP_STEPS
            self._ramp_steps = [round(start + (target - start) * i / n)
                                for i in range(1, n + 1)]
        self._ramp_tick()
        if self._ramp_steps:
            self._ramp_src = GLib.timeout_add(_RAMP_STEP_MS, self._ramp_tick)

    def _ramp_tick(self) -> bool:
        if not self._ramp_steps or _display_blanked:
            self._ramp_steps = []
            self._ramp_src = None
            return False
        value = self._ramp_steps.pop(0)
        self._current = value
        self._queue_write(value)
        if not self._ramp_steps:
            self._ramp_src = None
            return False
        return True

    def _cancel_ramp(self) -> None:
        if self._ramp_src is not None:
            GLib.source_remove(self._ramp_src)
            self._ramp_src = None
        self._ramp_steps = []

    def _queue_write(self, value: int) -> None:
        """Coalescing write: one job in flight on the backlight worker,
        which writes the newest value, so a slow brightnessctl never
        builds a backlog of stale ramp steps."""
        with self._write_lock:
            queued = self._write_value_pending is not None
            self._write_value_pending = value
        if not queued:
            run_backlight_io(self._flush_write)

    def _flush_write(self) -> None:
        with self._write_lock:
            value, self._write_value_pending = self._write_value_pending, None
        # Checked again on the worker: a blank queued just before this job
        # already switched the panel off, and it must stay off; so must a
        # phone screen docked dark (dock_controller).
        if value is None or _display_blanked or backlight.dark_held():
            return
        self._write_value(value)

    def _write_value(self, value: int) -> None:
        apply_backlight(value, sysfs_path=self._backlight_path)

    def _apply(self, ratio: float) -> None:
        """Blocking single write of a ratio (backlight worker only)."""
        self._write_value(max(1, round(ratio * self._max_brightness)))

    @property
    def enabled(self) -> bool:
        return self._enabled
