"""Home and lock-screen clock formatting with a 12/24-hour preference.

`clock_24h` True forces `%H:%M`, False forces `%-I:%M` with no AM/PM (like
the Android launcher clock), and None follows `org.gnome.desktop.interface
clock-format` — guarded so a missing schema keeps today's 24-hour clock.
Pure apart from the guarded Gio read, which is a local dconf lookup safe
for the main thread.
"""
from __future__ import annotations

from datetime import datetime

_CLOCK_SCHEMA = 'org.gnome.desktop.interface'
_CLOCK_KEY = 'clock-format'


# The GSettings object is built once: a Gio.Settings per clock redraw
# re-resolved the schema and opened the dconf backend every time. Its
# reads stay live (dconf is memory-mapped). False = schema missing.
_settings: object | None = None


def _clock_settings() -> object | None:
    global _settings
    if _settings is None:
        _settings = False
        from gi.repository import Gio
        source = Gio.SettingsSchemaSource.get_default()
        if source is None:
            return None
        schema = source.lookup(_CLOCK_SCHEMA, True)
        if schema is None or not schema.has_key(_CLOCK_KEY):
            return None
        _settings = Gio.Settings.new_full(schema, None)
    return _settings or None


def gnome_24h() -> bool | None:
    """GNOME clock-format as True/False, or None when the schema or key
    is missing (guarded: never raises)."""
    try:
        settings = _clock_settings()
        if settings is None:
            return None
        return settings.get_string(_CLOCK_KEY) == '24h'
    except Exception:
        return None


def _live_gnome_24h() -> bool | None:
    try:
        return gnome_24h()
    except Exception:
        return None


def resolve_24h(config: object, gnome_24h: bool | None = None) -> bool:
    """Effective hour mode: the explicit `clock_24h` pref wins, otherwise
    the GNOME setting, otherwise 24-hour (today's behaviour). `gnome_24h`
    is an injectable seam for the live Gio lookup; pass None to query."""
    try:
        explicit = getattr(config, 'clock_24h', None)
    except Exception:
        explicit = None
    if explicit is True:
        return True
    if explicit is False:
        return False
    system = gnome_24h if gnome_24h is not None else _live_gnome_24h()
    if system is None:
        return True
    return bool(system)


def format_clock(now: datetime, use_24h: bool) -> str:
    """`09:05` in 24-hour mode, `9:05` with no AM/PM in 12-hour mode."""
    if use_24h:
        return now.strftime('%H:%M')
    return now.strftime('%-I:%M')
