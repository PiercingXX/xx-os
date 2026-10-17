"""Next-armed selection (pure core of AlarmRepository.nextArmed).

An alarm is armed while enabled OR a snooze is still pending: a one-shot
that disabled itself at fire time must keep counting once snoozed.
"""

from __future__ import annotations

from collections.abc import Callable

from .models import Alarm


def next_armed(alarms: list[Alarm], snoozed_until: Callable[[int], int],
               scheduled_fire: Callable[[int], int], now_ms: int) -> tuple[Alarm, int] | None:
    best: tuple[Alarm, int] | None = None
    for alarm in alarms:
        snoozed = snoozed_until(alarm.id)
        if not alarm.enabled and snoozed <= now_ms:
            continue
        at = max(snoozed, scheduled_fire(alarm.id))
        if at <= now_ms:
            continue
        if best is None or at < best[1]:
            best = (alarm, at)
    return best
