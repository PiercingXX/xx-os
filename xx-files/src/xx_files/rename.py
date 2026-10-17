"""Rename validation and collision resolution.

Port of the Android ``Rename`` JVM seam (todo.md WS3 + P1): ``validate``
rejects empty names and path separators; the on-screen Rename confirms
overwrite or aborts instead of auto-uniquifying; paste / save-here /
trash-restore still uniquify via ``next_available_name``.
"""
from __future__ import annotations

import os


def validate(name: str) -> str | None:
    if not name.strip():
        return "Name cannot be empty"
    if "/" in name:
        return "Name cannot contain '/'"
    return None


def next_available_name(target_name: str, siblings: list[str]) -> str:
    taken = set(siblings)
    if target_name not in taken:
        return target_name
    dot = target_name.find(".")
    base = target_name.rsplit(".", 1)[0] if dot >= 0 else target_name
    ext = target_name[dot:] if dot > 0 else ""
    counter = 2
    while True:
        candidate = f"{base} ({counter}){ext}"
        if candidate not in taken:
            return candidate
        counter += 1


def resolve_target(original_path: str, new_name: str,
                   siblings: list[str]) -> str:
    parent = os.path.dirname(original_path)
    if new_name not in set(siblings):
        return os.path.join(parent, new_name)
    return os.path.join(parent, next_available_name(new_name, siblings))


def would_overwrite(original_name: str, new_name: str,
                    siblings: list[str]) -> bool:
    if new_name == original_name:
        return False
    return new_name in siblings
