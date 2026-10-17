"""Alarm and timer models (port of model/Alarm.kt + model/TimerItem.kt)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Alarm:
    id: int
    hour: int
    minute: int
    days_mask: int = 0
    label: str = ""
    enabled: bool = True
    vibrate: bool = True
    sound_uri: str | None = None

    @property
    def repeating(self) -> bool:
        return self.days_mask != 0


@dataclass
class TimerItem:
    id: int
    duration_ms: int
    state: str = "IDLE"
    ends_at_ms: int = 0
    remaining_ms: int = 0
    label: str = ""

    def __post_init__(self) -> None:
        if self.state == "IDLE" and self.remaining_ms == 0 and self.duration_ms:
            self.remaining_ms = self.duration_ms


STATE_IDLE = "IDLE"
STATE_RUNNING = "RUNNING"
STATE_PAUSED = "PAUSED"
STATE_FINISHED = "FINISHED"


def new_alarm(hour: int, minute: int, alarm_id: int) -> Alarm:
    return Alarm(id=alarm_id, hour=hour, minute=minute, days_mask=0,
                 label="", enabled=True, vibrate=True)


def new_timer(duration_ms: int, timer_id: int, label: str = "") -> TimerItem:
    return TimerItem(id=timer_id, duration_ms=duration_ms, state=STATE_IDLE,
                     ends_at_ms=0, remaining_ms=duration_ms, label=label)
