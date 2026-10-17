"""Settings for xx-note (JSON in ``$XDG_CONFIG_HOME/xx-note/settings.json``).

WebDAV credentials are NEVER stored here — see ``secrets.py``
(libsecret). This file holds only non-secret preferences.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from xx_note import paths as _paths

COLOR_KEEP = "keep"
COLOR_TONES = "tones"
SORT_UPDATED = "updated"
SORT_CREATED = "created"
SORT_TITLE = "title"


@dataclass
class Settings:
    notes_dir: str = ""
    color_mode: str = COLOR_KEEP
    sort_order: str = SORT_UPDATED
    webdav_enabled: bool = False
    webdav_url: str = ""
    webdav_user: str = ""
    webdav_path: str = "Notes/"
    last_sync: str = ""

    def resolved_notes_dir(self) -> Path:
        if self.notes_dir.strip():
            return Path(self.notes_dir).expanduser()
        return _paths.default_notes_dir()


def settings_path() -> Path:
    return _paths.app_config_dir() / "settings.json"


def load(path: Path | None = None) -> Settings:
    target = path or settings_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    known = {f for f in Settings.__dataclass_fields__}
    return Settings(**{k: v for k, v in data.items() if k in known})


def save(settings: Settings, path: Path | None = None) -> None:
    target = path or settings_path()
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    tmp.replace(target)


def parse_settings(data: dict) -> Settings:
    known = {f for f in Settings.__dataclass_fields__}
    merged = Settings()
    base = asdict(merged)
    base.update({k: v for k, v in data.items() if k in known})
    return Settings(**base)
