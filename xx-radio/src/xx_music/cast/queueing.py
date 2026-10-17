"""Small queue helpers for cast loads. Pure functions, fully tested."""

from __future__ import annotations

from typing import Any, Callable

LOAD_WINDOW = 8


def load_window(items: list, start: int, limit: int = LOAD_WINDOW,
                ready: Callable[[Any], bool] | None = None) -> list:
    """The start item plus the ready ones after it, at most ``limit``."""
    out: list = []
    for index in range(max(0, int(start)), len(items)):
        if len(out) >= max(1, int(limit)):
            break
        item = items[index]
        if ready is not None and not ready(item):
            continue
        out.append(item)
    return out


def fill_after(ids: list[str], loaded_from: str | None,
               playing_id: str | None) -> int:
    """Queue index appends continue after, -1 when unknown."""
    if loaded_from:
        try:
            return ids.index(loaded_from)
        except ValueError:
            pass
    if playing_id:
        try:
            return ids.index(playing_id)
        except ValueError:
            pass
    return -1


def should_reload(playing_id: str | None, start_id: str | None) -> bool:
    if not start_id:
        return False
    return playing_id != start_id


def local_index_after_cast(ids: list[str], fallback: int,
                           media_id: str | None) -> int:
    if media_id:
        try:
            return ids.index(media_id)
        except ValueError:
            pass
    return max(0, int(fallback))
