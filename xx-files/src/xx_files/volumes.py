"""Storage roots: home folders plus removable media.

Port of the Android ``Volume``/``VolumeLabels``/``Volumes``/``BackNav``
JVM seams. The primary root is always labelled **Home**; removable
media passes the OS label through. ``containing`` picks the longest
matching root so a path inside removable media resolves to that
volume, never to Home.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class Volume:
    label: str
    root_path: str
    is_primary: bool
    is_removable: bool


def label(description: str, is_primary: bool) -> str:
    return "Home" if is_primary else description


def _trim(path: str) -> str:
    return path.rstrip("/") or "/"


def is_volume_root(path: str, roots: list[str]) -> bool:
    trimmed = _trim(path)
    return any(_trim(r) == trimmed for r in roots)


def mounted(volumes: list[Volume]) -> list[Volume]:
    return [v for v in volumes if v.root_path]


def containing(path: str, volumes: list[Volume]) -> Volume | None:
    trimmed = _trim(path)
    best: Volume | None = None
    for vol in mounted(volumes):
        root = _trim(vol.root_path)
        if trimmed == root or trimmed.startswith(root + "/"):
            if best is None or len(root) > len(_trim(best.root_path)):
                best = vol
    return best


class Dest(Enum):
    FINISH = "finish"
    VOLUME_LIST = "volume_list"
    PARENT = "parent"


def back_dest(volumes_mode: bool, trash_mode: bool,
              current_path: str, volumes: list[Volume]) -> Dest:
    if volumes_mode or trash_mode:
        return Dest.VOLUME_LIST if not volumes_mode else Dest.FINISH
    volume = containing(current_path, volumes)
    if volume is None:
        return Dest.PARENT
    if is_volume_root(current_path, [volume.root_path]):
        return Dest.VOLUME_LIST
    return Dest.PARENT
