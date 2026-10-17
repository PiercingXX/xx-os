"""Suite media apps the Radio/Audiobook tiles open.

The public radio app is xx-radio and the audiobook app is xx-audiobook;
each is detected by desktop id and launched by desktop file. Matching of
live players to tiles stays generic (drive_tiles.tile_for), so another
app built on the shared music core works the same way with no code
change here.
"""

from __future__ import annotations

RADIO_DESKTOP_ID = "io.piercingxx.XXRadio"
RADIO_DESKTOP_FILE = RADIO_DESKTOP_ID + ".desktop"
RADIO_MPRIS_BUS = "org.mpris.MediaPlayer2.xx_radio"

AUDIOBOOK_DESKTOP_ID = "io.piercingxx.XXAudiobook"
AUDIOBOOK_DESKTOP_FILE = AUDIOBOOK_DESKTOP_ID + ".desktop"
AUDIOBOOK_MPRIS_BUS = "org.mpris.MediaPlayer2.xx_audiobook"

THUMB_UP_ICON = "xx-auto-thumb-up-symbolic"
THUMB_DOWN_ICON = "xx-auto-thumb-down-symbolic"


def _lookup(desktop, desktop_file: str):
    """DesktopAppInfo or None. Newer PyGObject raises TypeError for a
    missing id instead of returning None."""
    try:
        return desktop.new(desktop_file)
    except Exception:
        return None


def is_installed(desktop_file: str, desktop=None) -> bool:
    """True when the desktop entry for an app exists."""
    try:
        if desktop is None:
            import gi

            gi.require_version("Gio", "2.0")
            from gi.repository import Gio

            desktop = Gio.DesktopAppInfo
        return _lookup(desktop, desktop_file) is not None
    except Exception:
        return False


def installed(desktop=None) -> dict:
    """{"radio": bool, "audiobook": bool} for the drive tiles."""
    return {"radio": is_installed(RADIO_DESKTOP_FILE, desktop),
            "audiobook": is_installed(AUDIOBOOK_DESKTOP_FILE, desktop)}


def launch(desktop_file: str, desktop=None) -> bool:
    """Open the app for a desktop file; False when nothing is installed."""
    try:
        if desktop is None:
            import gi

            gi.require_version("Gio", "2.0")
            from gi.repository import Gio

            desktop = Gio.DesktopAppInfo
        info = _lookup(desktop, desktop_file)
        if info is None:
            return False
        return bool(info.launch([], None))
    except Exception:
        return False


def launch_radio(desktop=None) -> bool:
    """Open xx-radio; False when it is not installed."""
    return launch(RADIO_DESKTOP_FILE, desktop)


def launch_audiobook(desktop=None) -> bool:
    """Open xx-audiobook; False when it is not installed."""
    return launch(AUDIOBOOK_DESKTOP_FILE, desktop)
