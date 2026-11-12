"""One shared ticker for the shell's periodic housekeeping.

Recurring checks (the home and lock clocks, the status line, Focus
release, screen-time limits, the wireless-debugging idle check) are jobs
here instead of GLib timers of their own. One source wakes for whichever
job is due next; minute jobs land just after the minute boundary, so a
clock showing HH:MM wakes once a minute instead of every second. While
the display is blank the ticker sleeps, apart from jobs registered with
``while_blank``. Waking runs every job that came due while dark, so the
clock and status line are current on the first lit frame.

A job callback follows GLib's convention: returning ``False`` drops the
job, anything else keeps it.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Callable

_log = logging.getLogger(__name__)

# Lands minute jobs just past the boundary: a timer that fires a few ms
# early would otherwise redraw the old minute.
_ALIGN_SLACK_S = 0.05
# Unaligned jobs due this soon run with the current wakeup instead of
# waking the CPU again a moment later.
_COALESCE_S = 2.0


@dataclass
class _Job:
    fn: Callable[[], object]
    period: float
    align: bool
    while_blank: bool
    due: float = 0.0


class Ticker:
    """``timeout_add(ms, fn)`` / ``source_remove(id)`` and ``clock``
    (wall seconds) are injectable so tests run without a main loop."""

    def __init__(self, timeout_add: Callable | None = None,
                 source_remove: Callable | None = None,
                 clock: Callable[[], float] | None = None) -> None:
        self._timeout_add = timeout_add
        self._source_remove = source_remove
        self._clock = clock or time.time
        self._jobs: dict[int, _Job] = {}
        self._next_id = 0
        self._src = None
        self._blanked = False
        self._firing = False

    # -- jobs -------------------------------------------------------------

    def add(self, fn: Callable[[], object], period_s: float = 60.0, *,
            align_minute: bool = False, while_blank: bool = False) -> int:
        """Run ``fn`` every ``period_s`` seconds (on the minute boundary
        with ``align_minute``); returns a handle for remove()."""
        self._next_id += 1
        job = _Job(fn=fn, period=max(1.0, float(period_s)),
                   align=bool(align_minute), while_blank=bool(while_blank))
        job.due = self._next_due(job, self._clock())
        self._jobs[self._next_id] = job
        self._reschedule()
        return self._next_id

    def remove(self, handle: int | None) -> None:
        if handle is None:
            return
        if self._jobs.pop(handle, None) is not None:
            self._reschedule()

    def has(self, handle: int | None) -> bool:
        return handle in self._jobs

    def __len__(self) -> int:
        return len(self._jobs)

    # -- display ----------------------------------------------------------

    @property
    def blanked(self) -> bool:
        return self._blanked

    def set_blanked(self, blanked: bool) -> None:
        """DisplayManager blank/wake: sleep while dark, catch up on wake."""
        blanked = bool(blanked)
        if blanked == self._blanked:
            return
        self._blanked = blanked
        if blanked:
            self._reschedule()
        else:
            self._fire()

    # -- scheduling -------------------------------------------------------

    def _next_due(self, job: _Job, now: float) -> float:
        if job.align:
            return (math.floor(now / job.period) + 1) * job.period \
                + _ALIGN_SLACK_S
        return now + job.period

    def _runnable(self, job: _Job) -> bool:
        return job.while_blank or not self._blanked

    def _reschedule(self) -> None:
        if self._firing:
            return
        self._cancel()
        due = [job.due for job in self._jobs.values() if self._runnable(job)]
        if not due:
            return
        delay_ms = max(0, int(math.ceil((min(due) - self._clock()) * 1000)))
        add = self._timeout_add
        if add is None:
            try:
                from gi.repository import GLib
                add = GLib.timeout_add
            except Exception:
                return
        try:
            self._src = add(delay_ms, self._on_timeout)
        except Exception as exc:
            _log.warning('ticker timer unavailable: %s', exc)
            self._src = None

    def _cancel(self) -> None:
        src, self._src = self._src, None
        if src is None:
            return
        remove = self._source_remove
        if remove is None:
            try:
                from gi.repository import GLib
                remove = GLib.source_remove
            except Exception:
                return
        try:
            remove(src)
        except Exception:
            pass

    def _on_timeout(self) -> bool:
        self._src = None
        self._fire()
        return False

    def _fire(self) -> None:
        self._cancel()
        self._firing = True
        try:
            now = self._clock()
            for handle, job in list(self._jobs.items()):
                if handle not in self._jobs or not self._runnable(job):
                    continue
                if job.due > now + (0 if job.align else _COALESCE_S):
                    continue
                job.due = self._next_due(job, now)
                try:
                    keep = job.fn()
                except Exception:
                    _log.exception('ticker job failed')
                    keep = True
                if keep is False:
                    self._jobs.pop(handle, None)
        finally:
            self._firing = False
        self._reschedule()


_TICKER: Ticker | None = None


def get_ticker() -> Ticker:
    global _TICKER
    if _TICKER is None:
        _TICKER = Ticker()
    return _TICKER
