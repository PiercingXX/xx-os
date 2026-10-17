"""Breadcrumb split/navigate: absolute path <-> labelled segments.

Port of the Android ``Breadcrumbs`` JVM seam (todo.md F3). The root
label is the *current root's* label — never a hardcoded "Internal" on
every root — so a removable-media root reads as its own volume.
"""
from __future__ import annotations

import os


def _segments(path: str, root: str) -> list[str]:
    root = root.rstrip("/") or "/"
    trimmed = path.rstrip("/") or "/"
    relative = trimmed[len(root):] if trimmed.startswith(root) else trimmed
    return [s for s in relative.split("/") if s]


def split(path: str, root: str, root_label: str = "Home") -> list[str]:
    return [root_label] + _segments(path, root)


def segment_path(path: str, root: str, index: int) -> str:
    root = root.rstrip("/") or "/"
    if index <= 0:
        return root
    segs = _segments(path, root)
    return os.path.join(root, *segs[:index])


def navigate_to(path: str, root: str, index: int) -> str:
    segs = _segments(path, root)
    if index < 0 or index > len(segs):
        return path
    return segment_path(path, root, index)
