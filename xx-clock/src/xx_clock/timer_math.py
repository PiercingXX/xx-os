"""Pure timer math (port of time/TimerMath.kt)."""

from __future__ import annotations

from .models import STATE_FINISHED, STATE_PAUSED, STATE_RUNNING, TimerItem


def remaining_ms(timer: TimerItem, now_ms: int) -> int:
    if timer.state == STATE_RUNNING:
        return max(0, timer.ends_at_ms - now_ms)
    if timer.state in (STATE_PAUSED, STATE_FINISHED):
        return max(0, timer.remaining_ms)
    return timer.duration_ms


def is_expired(timer: TimerItem, now_ms: int) -> bool:
    return timer.state == STATE_RUNNING and now_ms >= timer.ends_at_ms


def display(ms: int) -> str:
    total_seconds = (max(0, ms) + 999) // 1000
    hours, rest = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"
