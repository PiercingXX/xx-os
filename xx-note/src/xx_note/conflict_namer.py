"""Conflict-fork naming (port of ``sync/ConflictNamer.kt``).

``<slug>_<device>_<MMM-dd-HHmm-yyyy>_EditConflict_<n>.md`` — imitates
Synology Drive's own convention so forks look native in Drive's web UI.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from xx_note import slug as _slug
from xx_note import ulid as _ulid

MAX_NAME_BYTES = 255
SEPARATOR_BYTES = 1
MD_SUFFIX = ".md"
_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def _stamp(now: datetime) -> str:
    now = now.astimezone(timezone.utc)
    return (
        f"{_MONTHS[now.month - 1]}-{now.day:02d}-"
        f"{now.hour:02d}{now.minute:02d}-{now.year}"
    )


def _title_part(original_path: str) -> str:
    name = original_path.rsplit("/", 1)[-1]
    if name.lower().endswith(MD_SUFFIX):
        stem = name[: -len(MD_SUFFIX)]
    else:
        stem = name
    dash = stem.find("-")
    if dash == _ulid.LENGTH and _ulid.is_valid(stem[:dash]):
        return stem[dash + 1 :]
    return stem


class ConflictNamer:
    def __init__(
        self,
        device_name: str,
        clock: Callable[[], datetime] | None = None,
        name_exists: Callable[[str], bool] | None = None,
    ) -> None:
        self._device = device_name
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._exists = name_exists or (lambda _name: False)

    def fork_name(self, original_path: str) -> str:
        device = _slug.of(self._device)
        stamp = _stamp(self._clock())
        number = 1
        while True:
            tail = f"{stamp}_EditConflict_{number}.md"
            budget = (
                MAX_NAME_BYTES
                - len(tail.encode("utf-8"))
                - SEPARATOR_BYTES * 2
                - len(device.encode("utf-8"))
            )
            part = _slug.of(_title_part(original_path), max(1, budget))
            candidate = f"{part}_{device}_{tail}"
            if not self._exists(candidate):
                return candidate
            number += 1
