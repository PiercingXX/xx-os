"""XDG directories + notes-folder resolution for xx-note."""

from __future__ import annotations

import os
from pathlib import Path


def _home() -> Path:
    return Path(os.environ.get("HOME", str(Path.home())))


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", str(_home() / ".config")))


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", str(_home() / ".local" / "share")))


def app_config_dir() -> Path:
    return config_home() / "xx-note"


def default_notes_dir() -> Path:
    docs = os.environ.get("XDG_DOCUMENTS_DIR")
    if docs:
        return Path(docs) / "Notes"
    return _home() / "Documents" / "Notes"


def ensure_app_dirs() -> None:
    for directory in (app_config_dir(), data_home() / "xx-note"):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)


def ensure_notes_tree(notes_dir: Path) -> None:
    notes_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    (notes_dir / ".xxnote").mkdir(mode=0o700, parents=True, exist_ok=True)
    (notes_dir / ".xxnote" / "trash").mkdir(mode=0o700, parents=True, exist_ok=True)
    (notes_dir / "attachments").mkdir(mode=0o700, parents=True, exist_ok=True)
