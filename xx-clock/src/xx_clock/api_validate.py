"""D-Bus input validation (pure rules of alarm/AlarmClockApi.kt).

Android's AlarmClock intents do not exist on Linux; the same validation
rules guard the daemon's D-Bus methods instead, so malformed input is
rejected rather than stored.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import alarm_logic
from .models import STATE_FINISHED, STATE_IDLE, STATE_RUNNING, Alarm, TimerItem

SEARCH_TIME = "android.time"
SEARCH_NEXT = "android.next"
SEARCH_ALL = "android.all"
SEARCH_LABEL = "android.label"
RINGTONE_SILENT = "silent"
MAX_TIMER_SECONDS = 86_400
MIN_TIMER_SECONDS = 1


@dataclass
class SetAlarmRequest:
    hour: int
    minute: int
    days_mask: int
    label: str
    vibrate: bool
    sound_uri: str | None
    skip_ui: bool


@dataclass
class SetTimerRequest:
    duration_ms: int
    label: str
    skip_ui: bool


def set_alarm_request(hour: int | None, minute: int, weekdays: list[int],
                      label: str, vibrate: bool, ringtone: str | None,
                      skip_ui: bool) -> SetAlarmRequest | None:
    if hour is None or hour not in range(24) or minute not in range(60):
        return None
    sound = ringtone if ringtone and ringtone.strip() and ringtone != RINGTONE_SILENT else None
    mask = 0
    for day in weekdays:
        mask |= alarm_logic.bit_for_weekday(day)
    return SetAlarmRequest(hour=hour, minute=minute, days_mask=mask,
                           label=label.strip(), vibrate=vibrate,
                           sound_uri=sound, skip_ui=skip_ui)


def set_timer_request(length_seconds: int | None, label: str, skip_ui: bool) -> SetTimerRequest | None:
    if length_seconds is None or length_seconds not in range(MIN_TIMER_SECONDS, MAX_TIMER_SECONDS + 1):
        return None
    return SetTimerRequest(duration_ms=length_seconds * 1000, label=label.strip(), skip_ui=skip_ui)


def search_hour(hour: int | None, is_pm: bool | None) -> int | None:
    if hour is None:
        return None
    if is_pm is None or hour not in range(1, 13):
        return hour if hour in range(24) else None
    if is_pm:
        return 12 if hour == 12 else hour + 12
    return 0 if hour == 12 else hour


def identical_alarm(alarms: list[Alarm], request: SetAlarmRequest) -> Alarm | None:
    for alarm in alarms:
        if (alarm.hour == request.hour and alarm.minute == request.minute
                and alarm.days_mask == request.days_mask and alarm.label == request.label
                and alarm.vibrate == request.vibrate and alarm.sound_uri == request.sound_uri):
            return alarm
    return None


def reusable_timer(timers: list[TimerItem], request: SetTimerRequest) -> TimerItem | None:
    for timer in timers:
        if (timer.state == STATE_IDLE and timer.duration_ms == request.duration_ms
                and timer.label == request.label):
            return timer
    return None


def alarms_to_dismiss(alarms: list[Alarm], ringing_alarm_id: int | None,
                      next_armed_id: int | None, search_mode: str | None,
                      hour: int | None, minute: int | None,
                      label: str | None) -> list[Alarm]:
    enabled = [a for a in alarms if a.enabled]
    if search_mode == SEARCH_ALL:
        return enabled
    if search_mode == SEARCH_NEXT:
        target = ringing_alarm_id if ringing_alarm_id is not None else next_armed_id
        if target is None:
            return []
        return [a for a in alarms if a.id == target]
    if search_mode == SEARCH_LABEL:
        query = (label or "").strip()
        if not query:
            return []
        return [a for a in enabled if query.lower() in a.label.lower()]
    if search_mode == SEARCH_TIME:
        if hour is None or hour not in range(24):
            return []
        minute_value = minute if minute is not None else 0
        if minute_value not in range(60):
            return []
        return [a for a in enabled if a.hour == hour and a.minute == minute_value]
    if ringing_alarm_id is not None:
        return [a for a in alarms if a.id == ringing_alarm_id]
    if len(enabled) == 1:
        return enabled
    return []


def skip_upcoming_until_ms(alarm: Alarm, now_ms: int) -> int | None:
    if not alarm.repeating:
        return None
    from . import next_occurrence
    return next_occurrence.next_alarm_millis(alarm.hour, alarm.minute, alarm.days_mask, now_ms) + 1


def timers_to_dismiss(timers: list[TimerItem], ringing_timer_id: int | None,
                      label: str | None) -> list[TimerItem]:
    if ringing_timer_id is not None:
        return [t for t in timers if t.id == ringing_timer_id]
    query = (label or "").strip()
    if query:
        return [t for t in timers
                if query.lower() in t.label.lower()
                and t.state in (STATE_FINISHED, STATE_RUNNING)]
    return [t for t in timers if t.state == STATE_FINISHED]


def snooze_minutes(requested: int | None, default_minutes: int) -> int:
    value = default_minutes if requested is None else requested
    return max(1, min(120, value))
