"""On-phone log ring (port of the local half of log/AppLog.kt).

A bounded in-memory ring plus a file under the state dir. Nothing is ever
uploaded — the dump provider from Android is deliberately not ported.
"""

from __future__ import annotations

import collections
import threading
import time as _time

from . import paths

RING_SIZE = 200

_lock = threading.Lock()
_ring: collections.deque = collections.deque(maxlen=RING_SIZE)


def log(tag: str, message: str) -> None:
    line = f"{_time.strftime('%H:%M:%S')} {tag} {message}"
    with _lock:
        _ring.append(line)
    try:
        paths.ensure_app_dirs()
        with open(paths.state_dir() / "xx-clock.log", "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        pass


def read_recent(limit: int = 100) -> list[str]:
    with _lock:
        return list(_ring)[-limit:]


def reset() -> None:
    with _lock:
        _ring.clear()
