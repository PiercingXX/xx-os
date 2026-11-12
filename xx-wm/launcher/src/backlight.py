"""Which sysfs node is the screen backlight.

Mainline phones expose /sys/class/backlight/<name>. Halium phones (the
FuriOS FLX1) have no backlight class; the panel is an LED-class node,
/sys/class/leds/lcd-backlight. brightnessctl without --device picks the
first device it finds (the FLX1's vibrator LED, a workstation's
input*::scrolllock), so nothing here ever runs it bare: no panel found
means no brightness write at all.

LED-class devices are allow-listed by name. The LED class also holds
keyboard lock and backlight LEDs, vibrators, torches, chargers and
radios; only a name that says "display backlight" is a panel.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import NamedTuple

SYSFS = Path('/sys/class')
# Tests and the snapshot rig point this at a fake (or missing) tree so a
# render on a workstation never writes the host's LEDs.
SYSFS_ENV = 'XX_WM_SYSFS_CLASS'

# lcd-backlight (FLX1 and other MediaTek Halium), lcd_backlight,
# panel-backlight, backlight, backlight0, ... Never function-style LED
# names (devicename::function, e.g. tpacpi::kbd_backlight).
_PANEL_LED = re.compile(
    r'(?:(?:lcd|panel|display|disp|dsi|mdss|oled|amoled)[-_]?)?backlight\d*')


class Backlight(NamedTuple):
    name: str        # brightnessctl --device / login1 SetBrightness name
    subsystem: str   # 'backlight' or 'leds' (login1 SetBrightness subsystem)
    path: Path       # .../brightness


def sysfs_class() -> Path:
    """/sys/class, or the tree XX_WM_SYSFS_CLASS names."""
    override = os.environ.get(SYSFS_ENV)
    return Path(override) if override else SYSFS


def is_panel_led(name: str) -> bool:
    """True for an LED-class device name that is a display backlight."""
    return _PANEL_LED.fullmatch(name.lower()) is not None


def find(root: Path | None = None) -> Backlight | None:
    """The screen backlight: a backlight-class device first, else an
    LED-class device whose name is on the panel allow-list."""
    root = sysfs_class() if root is None else root
    for subsystem in ('backlight', 'leds'):
        for node in sorted((root / subsystem).glob('*')):
            if subsystem == 'leds' and not is_panel_led(node.name):
                continue
            brightness = node / 'brightness'
            if brightness.exists():
                return Backlight(node.name, subsystem, brightness)
    return None


def brightnessctl_args(found: Backlight | None = None) -> list[str] | None:
    """['brightnessctl', '--device', name] for the screen, or None when
    there is no panel backlight (never a bare brightnessctl)."""
    found = find() if found is None else found
    if found is None:
        return None
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


def read_raw(found: Backlight | None = None) -> int | None:
    """Current raw brightness from sysfs (unprivileged read)."""
    found = find() if found is None else found
    if found is None:
        return None
    try:
        return int(found.path.read_text())
    except (OSError, ValueError):
        return None


# While docked with the phone screen dark (dock_controller), automatic
# brightness must not light the handset again.
_dark_hold = False


def hold_dark(held: bool) -> None:
    global _dark_hold
    _dark_hold = bool(held)


def dark_held() -> bool:
    return _dark_hold
