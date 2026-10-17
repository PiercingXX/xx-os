"""Directory order: directories first, then files, each group by name.

Port of the Android ``Listing`` JVM seam (todo.md WS2): the on-screen
order is ``sort(children)`` — never the raw ``listFiles()`` order.
``Entry`` keeps this module Gio-free so the pytest port runs headless.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Entry:
    name: str
    path: str
    is_dir: bool
    size: int = 0
    modified: float = 0.0


def _key(entry: Entry) -> str:
    return entry.name.casefold()


def sort(children: list[Entry]) -> list[Entry]:
    dirs = sorted((e for e in children if e.is_dir), key=_key)
    files = sorted((e for e in children if not e.is_dir), key=_key)
    return dirs + files


def sort_by(children: list[Entry], mode: str) -> list[Entry]:
    """Sorted listing for the sort control; every mode stays dirs-first."""
    if mode == "size":
        dirs = sorted((e for e in children if e.is_dir), key=_key)
        files = sorted((e for e in children if not e.is_dir),
                       key=lambda e: (-e.size, _key(e)))
        return dirs + files
    if mode == "date":
        dirs = sorted((e for e in children if e.is_dir), key=_key)
        files = sorted((e for e in children if not e.is_dir),
                       key=lambda e: (-e.modified, _key(e)))
        return dirs + files
    return sort(children)
