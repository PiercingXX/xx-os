"""Settings.json backup unit: export/parse round-trip, never half-applied."""

from __future__ import annotations

from .settings_store import DEFAULTS, Settings, parse_settings

APP_TAG = "auto"
# Backups written before the apps dropped the xx- prefix still restore.
LEGACY_APP_TAGS = ("xx-auto",)
FORMAT_VERSION = 1


def export_backup(settings: Settings) -> dict:
    """Serialize settings to a backup payload."""
    return {"app": APP_TAG, "version": FORMAT_VERSION,
            "settings": settings.to_dict()}


def parse_backup(payload: object) -> Settings | None:
    """Parse a backup payload; None when it is not ours or corrupt."""
    if not isinstance(payload, dict):
        return None
    if payload.get("app") not in (APP_TAG, *LEGACY_APP_TAGS) or payload.get("version") != 1:
        return None
    settings = payload.get("settings")
    if not isinstance(settings, dict):
        return None
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in settings.items() if k in DEFAULTS})
    try:
        return parse_settings(merged)
    except Exception:
        return None
