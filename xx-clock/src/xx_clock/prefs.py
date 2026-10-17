"""Behavior constants (port of Prefs.kt) plus user settings JSON."""

from __future__ import annotations

import json
from dataclasses import dataclass

from . import paths

SNOOZE_MINUTES_DEFAULT = 10
AUTO_SILENCE_MINUTES = 10
GRADUAL_VOLUME_DEFAULT = True
TIMER_BOOT_GRACE_MS = 15 * 60 * 1000
RING_WAKELOCK_MS = 12 * 60 * 1000
VOLUME_RAMP_MS = 60_000


@dataclass
class Settings:
    snooze_minutes: int = SNOOZE_MINUTES_DEFAULT
    gradual_volume: bool = GRADUAL_VOLUME_DEFAULT
    clock_style: str = "digital"
    last_tab: str = "clock"


def load() -> Settings:
    try:
        raw = paths.settings_path().read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return Settings()
    if not isinstance(data, dict):
        return Settings()
    settings = Settings()
    try:
        settings.snooze_minutes = max(1, min(120, int(data.get("snooze_minutes", settings.snooze_minutes))))
    except (TypeError, ValueError):
        pass
    if isinstance(data.get("gradual_volume"), bool):
        settings.gradual_volume = data["gradual_volume"]
    if data.get("clock_style") in ("digital", "analog"):
        settings.clock_style = data["clock_style"]
    if data.get("last_tab") in ("clock", "alarms", "timers", "setup"):
        settings.last_tab = data["last_tab"]
    return settings


def save(settings: Settings) -> None:
    paths.ensure_app_dirs()
    payload = {
        "snooze_minutes": settings.snooze_minutes,
        "gradual_volume": settings.gradual_volume,
        "clock_style": settings.clock_style,
        "last_tab": settings.last_tab,
    }
    tmp = paths.settings_path().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(paths.settings_path())
