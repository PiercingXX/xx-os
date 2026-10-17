from __future__ import annotations

import subprocess
from pathlib import Path

import backlight

from gi.repository import GLib

from shell_log import get_logger

_log = get_logger('brightness')

_LOGIN1 = 'org.freedesktop.login1'
_LOGIN1_SESSION = '/org/freedesktop/login1/session/auto'
_LOGIN1_SESSION_IFACE = 'org.freedesktop.login1.Session'

_failure_logged = False


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
# Backlight: backlight.find() (backlight class, else a *backlight LED)

_IIO_GLOB = '/sys/bus/iio/devices/iio:device*/in_illuminance_input'
_POLL_INTERVAL_MS = 5000
_CHANGE_THRESHOLD = 0.12  # only apply if brightness changes by ≥12%

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


def _brightnessctl_set(value: int, device: str | None = None) -> bool:
    try:
        cmd = ['brightnessctl']
        if device is not None:
            cmd += ['--device', device]
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
    """Percent-based wrapper for the shade slider: resolves the absolute
    scale from sysfs max_brightness (reads are unprivileged)."""
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
    """

    def __init__(self) -> None:
        self._sensor_path: Path | None = _find_path(_IIO_GLOB)
        self._backlight_path: Path | None = _backlight_path()
        self._max_brightness: int = self._read_max()
        self._last_ratio: float = -1.0
        self._timer_id: int | None = None
        self._enabled: bool = False

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
        self._poll()
        if self._timer_id is None:
            self._timer_id = GLib.timeout_add(_POLL_INTERVAL_MS, self._poll)

    def stop(self) -> None:
        self._enabled = False
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def _poll(self) -> bool:
        if not self._enabled:
            return False
        lux = self._read_lux()
        if lux is None:
            return True

        ratio = _lux_to_ratio(lux)
        if abs(ratio - self._last_ratio) >= _CHANGE_THRESHOLD:
            self._apply(ratio)
            self._last_ratio = ratio
        return True

    def _read_lux(self) -> float | None:
        if self._sensor_path is None:
            return None
        try:
            return float(self._sensor_path.read_text().strip())
        except (OSError, ValueError):
            return None

    def _apply(self, ratio: float) -> None:
        value = max(1, round(ratio * self._max_brightness))
        apply_backlight(value, sysfs_path=self._backlight_path)

    @property
    def enabled(self) -> bool:
        return self._enabled
