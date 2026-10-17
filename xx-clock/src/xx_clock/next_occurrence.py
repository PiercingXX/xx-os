"""Pure recurrence math (port of time/NextOccurrence.kt).

Alarms are (hour, minute, days_mask); the next occurrence is computed fresh
against wall-clock time, so DST transitions resolve through zoneinfo. A
nonexistent local time (spring-forward gap) falls forward to the next valid
instant instead of raising.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def _valid_local(midnight: datetime, hour: int, minute: int, zone: ZoneInfo) -> datetime:
    naive = midnight.replace(hour=hour, minute=minute, second=0, microsecond=0)
    candidate = naive.replace(tzinfo=zone)
    seen = candidate.astimezone(zone)
    if (seen.hour, seen.minute) != (hour, minute):
        gap_end = naive + timedelta(hours=2)
        return gap_end.replace(tzinfo=zone)
    return candidate


def next_alarm(now: datetime, hour: int, minute: int, days_mask: int) -> datetime:
    if hour not in range(24):
        raise ValueError(f"hour out of range: {hour}")
    if minute not in range(60):
        raise ValueError(f"minute out of range: {minute}")
    zone = now.tzinfo
    if zone is None:
        zone = ZoneInfo("UTC")
    day = now.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    for _ in range(8):
        candidate = _valid_local(day.replace(tzinfo=None), hour, minute, zone)
        day_bit = 1 << candidate.weekday()
        day_matches = days_mask == 0 or (days_mask & day_bit) != 0
        if candidate >= now.astimezone(zone) and day_matches:
            return candidate
        day = day + timedelta(days=1)
    raise RuntimeError(
        f"Unreachable: no occurrence within 8 days (hour={hour} minute={minute} days_mask={days_mask})")


def next_alarm_millis(hour: int, minute: int, days_mask: int, now_ms: int | None = None,
                      tzname: str | None = None) -> int:
    import time as _time
    if now_ms is None:
        now_ms = int(_time.time() * 1000)
    zone = ZoneInfo(tzname) if tzname else datetime.now().astimezone().tzinfo
    now = datetime.fromtimestamp(now_ms / 1000, tz=zone)
    return int(next_alarm(now, hour, minute, days_mask).timestamp() * 1000)
