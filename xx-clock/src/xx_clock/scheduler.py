"""GLib wake-up scheduling (Linux half of scheduler/ExactScheduler.kt).

Android registers each ring with AlarmManager (`setAlarmClock` for alarms,
`setExactAndAllowWhileIdle` for the soonest timer) and degrades one alarm
to an inexact window when exact access is revoked. On Linux the daemon owns
its GLib main loop, so "exact" is a timeout at the deadline and there is no
permission to revoke — the degraded path has no trigger and is not ported.
What stays: per-entry sources keyed by the stable request-code packing, and
the reconcile rule that a cold start re-registers everything (persisted
bookkeeping can look current while the sources are gone).
"""

from __future__ import annotations

from collections.abc import Callable

from . import request_codes
from .models import STATE_RUNNING, Alarm, TimerItem


class Scheduler:
    def __init__(self, glib, clock: Callable[[], int]) -> None:
        self._glib = glib
        self._clock = clock
        self._sources: dict[int, int] = {}

    def _arm(self, kind: int, entry_id: int, at_ms: int, fire: Callable[[], None]) -> None:
        key = request_codes.request_code(kind, entry_id)
        self.disarm(kind, entry_id)
        delay = max(0, at_ms - self._clock())

        def _once() -> bool:
            self._sources.pop(key, None)
            fire()
            return False

        self._sources[key] = self._glib.timeout_add(delay, _once)

    def disarm(self, kind: int, entry_id: int) -> None:
        key = request_codes.request_code(kind, entry_id)
        source = self._sources.pop(key, None)
        if source is not None:
            try:
                self._glib.source_remove(source)
            except Exception:
                pass

    def schedule_alarm(self, alarm: Alarm, at_ms: int, fire: Callable[[int], None]) -> None:
        self._arm(request_codes.REQ_KIND_ALARM, alarm.id, at_ms,
                  lambda: fire(alarm.id))

    def cancel_alarm(self, alarm_id: int) -> None:
        self.disarm(request_codes.REQ_KIND_ALARM, alarm_id)

    def schedule_timers(self, timers: list[TimerItem], fire: Callable[[int], None]) -> None:
        wanted = set()
        for timer in timers:
            if timer.state != STATE_RUNNING:
                continue
            key = request_codes.request_code(request_codes.REQ_KIND_TIMER, timer.id)
            wanted.add(key)
            if key not in self._sources:
                self._arm(request_codes.REQ_KIND_TIMER, timer.id, timer.ends_at_ms,
                          lambda tid=timer.id: fire(tid))
        for key in [k for k in self._sources
                    if (k >> 29) == request_codes.REQ_KIND_TIMER and k not in wanted]:
            source = self._sources.pop(key)
            try:
                self._glib.source_remove(source)
            except Exception:
                pass

    def pending(self) -> int:
        return len(self._sources)
