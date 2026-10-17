"""Shared Do Not Disturb / Focus schedule matcher.

One copy of the shell's days + start/end matcher (days are
Monday-first 0-6, overnight ranges wrap with the day check on the
start day). Malformed entries never match. Pure stdlib: safe to import
from daemons with no GTK main loop running.
"""

from __future__ import annotations

import re
from datetime import datetime


def parse_hhmm(value: object) -> int | None:
    """Minutes since midnight for "HH:MM", else None."""
    if not isinstance(value, str):
        return None
    match = re.match(r"^(\d{1,2}):(\d{2})$", value)
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    if hours > 23 or minutes > 59:
        return None
    return hours * 60 + minutes


def schedule_matches(schedules, now: datetime) -> bool:
    """True when any schedule covers `now`.

    Overnight ranges (e.g. 22:00-06:30) wrap: the evening part belongs
    to the schedule's day, the morning part to the following day.
    """
    for sched in schedules or []:
        if not isinstance(sched, dict):
            continue
        days = sched.get("days")
        start = parse_hhmm(sched.get("start"))
        end = parse_hhmm(sched.get("end"))
        if not isinstance(days, list) or start is None or end is None:
            continue
        minutes = now.hour * 60 + now.minute
        weekday = now.weekday()
        if start <= end:
            if weekday in days and start <= minutes < end:
                return True
        else:
            if weekday in days and minutes >= start:
                return True
            if (weekday - 1) % 7 in days and minutes < end:
                return True
    return False
