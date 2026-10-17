"""Persisted settings: show_hidden, sort mode, view mode.

JSON file under ``$XDG_CONFIG_HOME/xx-files/settings.json`` (sibling
convention). Every XDG directory is created mode 0700 before use so a
first run on a clean device never crashes opening the store.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass
class Settings:
    show_hidden: bool = False
    sort: str = "name"
    view: str = "list"


def config_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config")
    path = os.path.join(base, "xx-files")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def settings_path() -> str:
    return os.path.join(config_dir(), "settings.json")


def load(path: str | None = None) -> Settings:
    target = path or settings_path()
    try:
        with open(target, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return Settings()
    if not isinstance(data, dict):
        return Settings()
    settings = Settings()
    if isinstance(data.get("show_hidden"), bool):
        settings.show_hidden = data["show_hidden"]
    if data.get("sort") in ("name", "size", "date"):
        settings.sort = data["sort"]
    if data.get("view") in ("list", "tree", "grid"):
        settings.view = data["view"]
    return settings


def save(settings: Settings, path: str | None = None) -> None:
    target = path or settings_path()
    os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump({"show_hidden": settings.show_hidden,
                   "sort": settings.sort,
                   "view": settings.view}, handle)
    os.replace(tmp, target)
