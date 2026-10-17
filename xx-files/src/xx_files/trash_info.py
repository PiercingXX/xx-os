"""Freedesktop Trash helpers: parse .trashinfo sidecars.

The lane design uses the freedesktop Trash (``Gio.File.trash``,
``trash:///``) instead of Android's per-volume ``.xx-trash``. Restore
needs the original path recorded in the ``.trashinfo`` file, and a
taken name restores with a ``foo (2).ext`` suffix — the Android
``Trash.restoreDestination`` rule, kept.
"""
from __future__ import annotations

import configparser
import os
from dataclasses import dataclass

from .rename import next_available_name


@dataclass(frozen=True)
class TrashEntry:
    trash_path: str
    info_path: str | None
    display_name: str
    original_path: str | None
    deleted_ms: int | None
    is_dir: bool


def _decode_path(value: str) -> str:
    """Percent-decode a .trashinfo Path (UTF-8, never raises)."""
    out: list[str] = []
    buf = bytearray()
    i, n = 0, len(value)
    while i < n:
        if value[i] == "%" and i + 2 < n + 1:
            try:
                buf.append(int(value[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        if buf:
            out.append(bytes(buf).decode("utf-8", "replace"))
            buf = bytearray()
        out.append(value[i])
        i += 1
    if buf:
        out.append(bytes(buf).decode("utf-8", "replace"))
    return "".join(out)


def parse_trashinfo(text: str) -> tuple[str | None, str | None]:
    """Return (original_path, deletion_date) from a .trashinfo body."""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error:
        return None, None
    if not parser.has_section("Trash Info"):
        return None, None
    raw = None
    try:
        raw = parser.get("Trash Info", "Path", fallback=None)
    except configparser.Error:
        raw = None
    date = None
    try:
        date = parser.get("Trash Info", "DeletionDate", fallback=None)
    except configparser.Error:
        date = None
    path = _decode_path(raw) if raw else None
    return path, date


def restore_destination(original_path: str,
                        siblings: list[str]) -> str:
    parent = os.path.dirname(original_path)
    name = os.path.basename(original_path)
    if name not in set(siblings):
        return original_path
    return os.path.join(parent, next_available_name(name, siblings))


def trash_home() -> str:
    import pathlib

    data_home = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share")
    trash = os.path.join(data_home, "Trash")
    info = os.path.join(trash, "info")
    files = os.path.join(trash, "files")
    for path in (trash, info, files):
        pathlib.Path(path).mkdir(mode=0o700, parents=True, exist_ok=True)
    return trash


def list_entries(files_dir: str, info_dir: str) -> list[TrashEntry]:
    entries: list[TrashEntry] = []
    try:
        names = sorted(os.listdir(files_dir),
                       key=lambda n: n.casefold())
    except OSError:
        return []
    for name in names:
        trash_path = os.path.join(files_dir, name)
        info_path = os.path.join(info_dir, name + ".trashinfo")
        original: str | None = None
        if os.path.isfile(info_path):
            try:
                with open(info_path, encoding="utf-8",
                          errors="replace") as handle:
                    original, _deleted = parse_trashinfo(handle.read())
            except OSError:
                original = None
        entries.append(TrashEntry(
            trash_path=trash_path,
            info_path=info_path if os.path.isfile(info_path) else None,
            display_name=name,
            original_path=original,
            deleted_ms=None,
            is_dir=os.path.isdir(trash_path)
            and not os.path.islink(trash_path),
        ))
    return entries
