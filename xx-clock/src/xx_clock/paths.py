"""XDG locations for xx-clock. Created mode 0700 on first run."""

from __future__ import annotations

import os
from pathlib import Path


def _home() -> Path:
    return Path(os.environ.get("HOME") or str(Path.home()))


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(_home() / ".local" / "share")
    return Path(base) / "xx-clock"


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(_home() / ".config")
    return Path(base) / "xx-clock"


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(_home() / ".local" / "state")
    return Path(base) / "xx-clock"


def db_path() -> Path:
    return data_dir() / "clock.db"


def settings_path() -> Path:
    return config_dir() / "settings.json"


def ensure_app_dirs() -> None:
    for directory in (data_dir(), config_dir(), state_dir()):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
