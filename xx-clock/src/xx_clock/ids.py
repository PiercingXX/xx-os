"""Monotonic id sequence (port of MonotonicIdSequence in data/ClockStore.kt).

Alarms and timers draw from ONE shared namespace so ids never collide
across kinds and never regress across process death. The floor re-checks
stored ids plus the wall clock so legacy timestamp ids are never reissued.
"""

from __future__ import annotations

import threading
from collections.abc import Callable


class MonotonicIdSequence:
    def __init__(self, load: Callable[[], int], store: Callable[[int], None],
                 floor_of: Callable[[], int]) -> None:
        self._load = load
        self._store = store
        self._floor_of = floor_of
        self._lock = threading.Lock()

    def next(self) -> int:
        with self._lock:
            value = max(self._load(), self._floor_of()) + 1
            self._store(value)
            return value
