"""Weekly recurrence mask (port of the Alarm companion in model/Alarm.kt).

Bit layout: bit0 = Monday ... bit6 = Sunday. Weekdays use ISO numbering
(Monday=0 .. Sunday=6); days_mask == 0 is a one-shot alarm.
"""

from __future__ import annotations

MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY, SATURDAY, SUNDAY = range(7)

_ORDERED_DAYS = (MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY, SATURDAY, SUNDAY)
_SHORT_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def bit_for_weekday(weekday: int) -> int:
    if weekday not in _ORDERED_DAYS:
        return 0
    return 1 << weekday


def calendar_days(mask: int) -> list[int]:
    return [day for day in _ORDERED_DAYS if mask & bit_for_weekday(day)]


WEEKDAYS_MASK = sum(bit_for_weekday(d) for d in (MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY))
WEEKENDS_MASK = bit_for_weekday(SATURDAY) | bit_for_weekday(SUNDAY)
ALL_DAYS_MASK = WEEKDAYS_MASK | WEEKENDS_MASK


def short_day_name(weekday: int) -> str:
    return _SHORT_NAMES[weekday]


def summary(mask: int) -> str:
    if mask == 0:
        return "Once"
    if mask == WEEKDAYS_MASK:
        return "Weekdays"
    if mask == WEEKENDS_MASK:
        return "Weekends"
    if mask == ALL_DAYS_MASK:
        return "Every day"
    return ", ".join(short_day_name(day) for day in calendar_days(mask))
