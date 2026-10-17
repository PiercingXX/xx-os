"""Which sysfs node is the screen backlight.

Mainline phones expose /sys/class/backlight/<name>. Halium phones (the
FuriOS FLX1) have no backlight class; the panel is an LED-class node,
/sys/class/leds/lcd-backlight. brightnessctl without --device picks the
first device it finds, which on the FLX1 is the *vibrator* LED, so every
caller must name the device this module resolves.
"""
from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

SYSFS = Path('/sys/class')


class Backlight(NamedTuple):
    name: str        # brightnessctl --device / login1 SetBrightness name
    subsystem: str   # 'backlight' or 'leds' (login1 SetBrightness subsystem)
    path: Path       # .../brightness


def find(root: Path = SYSFS) -> Backlight | None:
    """The screen backlight: a backlight-class device first, else an
    LED-class device whose name contains 'backlight'."""
    for subsystem, pattern in (('backlight', '*'), ('leds', '*backlight*')):
        for node in sorted((root / subsystem).glob(pattern)):
            brightness = node / 'brightness'
            if brightness.exists():
                return Backlight(node.name, subsystem, brightness)
    return None


def brightnessctl_args(found: Backlight | None = None) -> list[str]:
    """['brightnessctl', '--device', name] for the screen, or the bare
    command when nothing was found (brightnessctl then guesses)."""
    found = find() if found is None else found
    if found is None:
        return ['brightnessctl']
    return ['brightnessctl', '--device', found.name]


def read_pct(found: Backlight | None = None) -> int | None:
    """Current brightness percent from sysfs (unprivileged read)."""
    found = find() if found is None else found
    if found is None:
        return None
    try:
        current = int(found.path.read_text())
        maximum = int((found.path.parent / 'max_brightness').read_text())
    except (OSError, ValueError):
        return None
    return int(current * 100 / maximum) if maximum else None
