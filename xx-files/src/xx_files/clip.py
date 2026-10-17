"""Clipboard model: staged copy/cut plus paste-name resolution.

Port of the Android ``Clip`` JVM seam. ``unique_name`` resolves a
paste conflict as ``foo (2).txt`` — never a silent overwrite — and
``can_paste`` gates the Paste action on a staged item plus a writable
cwd.
"""
from __future__ import annotations

from dataclasses import dataclass

from .rename import next_available_name


@dataclass(frozen=True)
class Item:
    path: str
    is_cut: bool


def can_paste(clip: Item | None, cwd_writable: bool) -> bool:
    return clip is not None and cwd_writable


def unique_name(existing: list[str], name: str) -> str:
    if name not in set(existing):
        return name
    return next_available_name(name, existing)
