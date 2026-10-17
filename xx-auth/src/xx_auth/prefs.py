"""App preferences. Linux mapping of Android `VaultSettings` plus the lock
settings this lane requires: single JSON row with defaults when never set."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "xx-auth"


@dataclass
class Prefs:
    master_key_version: int = 1
    export_schema_version: int = 1
    lock_enabled: bool = False
    lock_hash: str = ""
    lock_salt: str = ""
    lock_kdf_salt: str = ""
    idle_seconds: int = 120
    keyring_disabled: bool = False
    group_filter: str = ""


_DEFAULTS = asdict(Prefs())


def load(path: Path | None = None) -> Prefs:
    prefs_path = path or config_dir() / "settings.json"
    data = dict(_DEFAULTS)
    if prefs_path.is_file():
        try:
            raw = json.loads(prefs_path.read_text(encoding="utf-8"))
        except ValueError:
            raw = {}
        if isinstance(raw, dict):
            for key in _DEFAULTS:
                if key in raw:
                    data[key] = raw[key]
    prefs = Prefs()
    for key, value in data.items():
        if key in _DEFAULTS:
            setattr(prefs, key, value)
    return prefs


def save(prefs: Prefs, path: Path | None = None) -> None:
    prefs_path = path or config_dir() / "settings.json"
    prefs_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(prefs_path.parent, 0o700)
    except OSError:
        pass
    tmp = prefs_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(prefs)), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, prefs_path)
