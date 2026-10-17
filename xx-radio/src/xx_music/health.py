"""Engine health: per-capability error counters that raise one banner.

Each process owns a :class:`HealthMonitor` and feeds it every engine
outcome. Three ``SOURCE_CHANGED`` (or ``HELPER_MISSING``) failures for one
capability within ten minutes raise the update banner; the first success
for that capability clears it again. ``OFFLINE`` and sign-in failures
(``AUTH_REQUIRED``/``AUTH_EXPIRED``) raise their own banner until any
success proves the connection or sign-in works again.

Source-agnostic on purpose: the source name (the service's public name
in this app) is a constructor argument, so no service name appears here.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

from xx_music.engine import ErrorKind

#: Failures counted per capability for the update banner.
COUNTED = frozenset({ErrorKind.SOURCE_CHANGED, ErrorKind.HELPER_MISSING})
#: Three counted failures within this window trip the banner.
TRIP_AFTER = 3
WINDOW_S = 10 * 60
#: Recent errors kept for diagnostics (PORT_SPEC section 4.1: last 50).
RECENT_KEPT = 50


class BannerKind(StrEnum):
    SOURCE_CHANGED = "source-changed"
    HELPER_MISSING = "helper-missing"
    OFFLINE = "offline"
    SIGNED_OUT = "signed-out"


#: The one action the banner button routes to, per kind.
BANNER_ACTIONS: dict[BannerKind, str] = {
    BannerKind.SOURCE_CHANGED: "update-engine",
    BannerKind.HELPER_MISSING: "setup-helper",
    BannerKind.OFFLINE: "retry",
    BannerKind.SIGNED_OUT: "sign-in",
}


@dataclass(frozen=True)
class Banner:
    """One banner state: what to show and which action it routes to."""

    kind: BannerKind
    text: str
    action: str


@dataclass(frozen=True)
class ErrorEvent:
    """One engine failure, for the diagnostics block."""

    when: str  # UTC ISO timestamp, for direct use in diagnostics text
    capability: str
    kind: str


class HealthMonitor:
    """Aggregate engine errors per capability into one banner state."""

    def __init__(self, source_name: str = "The music service",
                 clock=time.monotonic) -> None:
        self._source_name = source_name
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, list[tuple[float, ErrorKind]]] = {}
        self._offline = False
        self._signed_out = False
        self._recent: list[ErrorEvent] = []

    def note_error(self, capability: str, kind: ErrorKind) -> None:
        kind = ErrorKind(kind)
        now = self._clock()
        stamped = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            if kind in COUNTED:
                self._failures.setdefault(capability, []).append((now, kind))
            if kind == ErrorKind.OFFLINE:
                self._offline = True
            if kind in (ErrorKind.AUTH_REQUIRED, ErrorKind.AUTH_EXPIRED):
                self._signed_out = True
            self._recent.append(ErrorEvent(stamped, capability, kind.value))
            del self._recent[:-RECENT_KEPT]

    def note_success(self, capability: str | None = None) -> None:
        with self._lock:
            if capability is None:
                self._failures.clear()
            else:
                self._failures.pop(capability, None)
            self._offline = False
            self._signed_out = False

    def _recent_failures(self, capability: str,
                         now: float) -> list[tuple[float, ErrorKind]]:
        recent = [(when, kind) for when, kind in
                  self._failures.get(capability, []) if now - when < WINDOW_S]
        if recent:
            self._failures[capability] = recent
        else:
            self._failures.pop(capability, None)
        return recent

    @property
    def banner(self) -> Banner | None:
        with self._lock:
            now = self._clock()
            offline = self._offline
            signed_out = self._signed_out
            tripped: list[tuple[float, ErrorKind]] = []
            for capability in list(self._failures):
                recent = self._recent_failures(capability, now)
                if len(recent) >= TRIP_AFTER:
                    tripped.append(max(recent))
            if offline:
                return self._make(BannerKind.OFFLINE)
            if signed_out:
                return self._make(BannerKind.SIGNED_OUT)
            if tripped:
                latest = max(tripped)
                if latest[1] == ErrorKind.HELPER_MISSING:
                    return self._make(BannerKind.HELPER_MISSING)
                return self._make(BannerKind.SOURCE_CHANGED)
            return None

    def _make(self, kind: BannerKind) -> Banner:
        if kind == BannerKind.SOURCE_CHANGED:
            text = (f"{self._source_name} changed something "
                    f"\u2014 update the playback engine")
        elif kind == BannerKind.HELPER_MISSING:
            text = "Playback helper missing"
        elif kind == BannerKind.OFFLINE:
            text = "Offline"
        else:
            text = "Signed out"
        return Banner(kind, text, BANNER_ACTIONS[kind])

    def recent_errors(self, limit: int = 20) -> tuple[ErrorEvent, ...]:
        with self._lock:
            return tuple(self._recent[-limit:] if limit > 0 else ())
