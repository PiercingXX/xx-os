"""Setup-time preferences stored in ~/.config/xx-audiobook/config.json.

Mirrors the Android DataStore app_prefs keys (data/prefs/AppPrefs.kt) with
identical defaults. Speed is clamped 0.5-3.0, auto-rewind 0-30 s. The debug
API-base override is env-only (XX_AUDIOBOOK_API_BASE), never stored.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

from xx_audiobook import paths as paths_module

SKIP_SECONDS_DEFAULT = 30
SKIP_BACK_SECONDS_DEFAULT = 30
DEFAULT_SPEED_DEFAULT = 1.0
SPEED_MIN = 0.5
SPEED_MAX = 3.0
AUTO_REWIND_SECONDS_DEFAULT = 2
AUTO_REWIND_MIN = 0
AUTO_REWIND_MAX = 30
SKIP_CHOICES = (10, 15, 30)
SPEED_STEPS = (0.5, 0.75, 1.0, 1.1, 1.2, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)
DEBUG_API_BASE_ENV = "XX_AUDIOBOOK_API_BASE"


@dataclass
class Prefs:
    skip_seconds: int = SKIP_SECONDS_DEFAULT
    skip_back_seconds: int = SKIP_BACK_SECONDS_DEFAULT
    default_speed: float = DEFAULT_SPEED_DEFAULT
    auto_rewind_seconds: int = AUTO_REWIND_SECONDS_DEFAULT
    download_wifi_only: bool = True
    last_item_id: str | None = None
    last_position_s: float = 0.0
    settings_updated_at: float = 0.0
    settings_dirty: bool = False
    owner_user_id: str | None = None


def clamp_prefs(prefs: Prefs) -> Prefs:
    prefs.default_speed = min(SPEED_MAX, max(SPEED_MIN, prefs.default_speed))
    prefs.auto_rewind_seconds = min(
        AUTO_REWIND_MAX, max(AUTO_REWIND_MIN, prefs.auto_rewind_seconds))
    return prefs


def load() -> Prefs:
    try:
        raw = json.loads(paths_module.config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Prefs()
    if not isinstance(raw, dict):
        return Prefs()
    prefs = Prefs()
    for key, value in raw.items():
        if hasattr(prefs, key):
            setattr(prefs, key, value)
    return clamp_prefs(prefs)


def save(prefs: Prefs) -> None:
    path = paths_module.ensure_parent_for(paths_module.config_path())
    path.write_text(json.dumps(asdict(clamp_prefs(prefs)), indent=2),
                    encoding="utf-8")


def debug_api_base() -> str | None:
    override = os.environ.get(DEBUG_API_BASE_ENV, "").strip()
    return override or None


class PrefsStore:
    """Mutable prefs view for SettingsSync (mirrors AppPrefs G12)."""

    def current(self) -> Prefs:
        return load()

    def mark_settings_edited(self, at_s: float) -> None:
        prefs = load()
        prefs.settings_updated_at = at_s
        prefs.settings_dirty = True
        save(prefs)

    def mark_settings_synced(self, at_s: float) -> None:
        prefs = load()
        if prefs.settings_updated_at == at_s:
            prefs.settings_dirty = False
            save(prefs)

    def apply_remote_settings(self, skip_back: int, skip_forward: int,
                              speed: float, auto_rewind: int,
                              at_s: float) -> None:
        prefs = load()
        prefs.skip_back_seconds = skip_back
        prefs.skip_seconds = skip_forward
        prefs.default_speed = speed
        prefs.auto_rewind_seconds = auto_rewind
        prefs.settings_updated_at = at_s
        prefs.settings_dirty = False
        save(prefs)

    def set_owner_user_id(self, user_id: str) -> None:
        prefs = load()
        prefs.owner_user_id = user_id
        save(prefs)


def ui_prefs_path():
    return paths_module.config_dir() / "ui.json"


def load_ui() -> dict:
    """Local-only view prefs (UiPrefs.kt): {'library_grid': bool}."""
    try:
        raw = json.loads(ui_prefs_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return {"library_grid": bool(raw.get("library_grid", False))}


def save_ui(values: dict) -> None:
    path = paths_module.ensure_parent_for(ui_prefs_path())
    path.write_text(json.dumps(load_ui() | dict(values), indent=2),
                    encoding="utf-8")
