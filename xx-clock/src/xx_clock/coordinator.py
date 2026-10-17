"""Alarm/timer lifecycle (port of alarm/AlarmCoordinator.kt).

Single place that mutates runtime state. Policies, unchanged from Android:
newest-wins (a firing entry silences any current ringer), recurring alarms
pre-schedule their next occurrence at fire time, one-shots disable at fire,
snooze is tracked as snoozed-until state, and a missed ring always ends as
a notification — never silent-and-stuck.

All platform edges are injectable seams so tests drive the lifecycles with
an in-memory store and recorder hooks.
"""

from __future__ import annotations

import dataclasses
import time as _time
from collections.abc import Callable

from . import next_occurrence
from .models import STATE_FINISHED, STATE_IDLE, STATE_RUNNING, Alarm, TimerItem
from .prefs import TIMER_BOOT_GRACE_MS

TITLE_MISSED_ALARM = "Missed alarm"
TITLE_SILENCED_ALARM = "Alarm silenced"
TITLE_TIMER_FINISHED = "Timer finished"
TITLE_TIMER_FINISHED_AWAY = "A timer finished while you were away"


def _now_ms() -> int:
    return int(_time.time() * 1000)


@dataclasses.dataclass
class Hooks:
    ring_start: Callable[[int, bool], bool] = lambda entry_id, is_alarm: False
    ring_stop: Callable[[], None] = lambda: None
    show_alert: Callable[[str, int], None] = lambda kind, entry_id: None
    schedule_alarm: Callable[[Alarm, int], None] = lambda alarm, at_ms: None
    cancel_alarm: Callable[[int], None] = lambda entry_id: None
    schedule_timers: Callable[[], None] = lambda: None
    refreshed: Callable[[], None] = lambda: None
    missed: Callable[[int, str], None] = lambda entry_id, title: None


class Coordinator:
    def __init__(self, store, clock: Callable[[], int] = _now_ms,
                 hooks: Hooks | None = None) -> None:
        self._store = store
        self._clock = clock
        self._hooks = hooks or Hooks()

    def fire_alarm(self, alarm_id: int) -> None:
        store, hooks = self._store, self._hooks
        alarm = store.get_alarm(alarm_id)
        if alarm is None:
            return
        self._silence_other_ringer(alarm_id)
        following = next_occurrence.next_alarm_millis(
            alarm.hour, alarm.minute, alarm.days_mask, self._clock() + 1)
        if alarm.repeating:
            hooks.schedule_alarm(alarm, following)
        store.set_snoozed_until(alarm_id, 0)
        store.set_ringing(alarm_id, True)
        if not hooks.ring_start(alarm_id, True):
            self.ring_start_failed(alarm_id, True)
            if alarm.repeating:
                hooks.schedule_alarm(alarm, following)
            else:
                store.save_alarm(dataclasses.replace(alarm, enabled=True))
                hooks.schedule_alarm(dataclasses.replace(alarm, enabled=True), following)
        else:
            hooks.show_alert("alarm", alarm_id)
            if not alarm.repeating:
                store.save_alarm(dataclasses.replace(alarm, enabled=False))
        hooks.refreshed()

    def _silence_other_ringer(self, except_id: int) -> None:
        current = self._store.ringing_id()
        if current is not None and current != except_id:
            self.auto_silence(current, self._store.get_alarm(current) is not None,
                              post_notification=False)

    def snooze(self, alarm_id: int, minutes: int) -> None:
        store, hooks = self._store, self._hooks
        alarm = store.get_alarm(alarm_id)
        if alarm is None:
            hooks.ring_stop()
            return
        hooks.ring_stop()
        until = self._clock() + minutes * 60_000
        store.set_ringing(alarm_id, False)
        store.set_snoozed_until(alarm_id, until)
        hooks.schedule_alarm(alarm, until)
        hooks.refreshed()

    def dismiss(self, alarm_id: int) -> None:
        self._hooks.ring_stop()
        self._store.set_ringing(alarm_id, False)
        self._hooks.refreshed()

    def auto_silence(self, entry_id: int, is_alarm: bool, post_notification: bool = True) -> None:
        store, hooks = self._store, self._hooks
        hooks.ring_stop()
        store.set_ringing(entry_id, False)
        if is_alarm:
            store.clear_runtime(entry_id)
            if post_notification:
                hooks.missed(entry_id, TITLE_SILENCED_ALARM)
        else:
            self._stop_timer_internal(entry_id)
        hooks.refreshed()

    def ring_start_failed(self, entry_id: int, is_alarm: bool) -> None:
        store, hooks = self._store, self._hooks
        store.set_ringing(entry_id, False)
        if is_alarm:
            store.clear_runtime(entry_id)
            hooks.missed(entry_id, TITLE_MISSED_ALARM)
        else:
            self._stop_timer_internal(entry_id)
            hooks.missed(entry_id, TITLE_TIMER_FINISHED)
        hooks.refreshed()

    def fire_timer(self, timer_id: int, try_ring: bool = True) -> None:
        store, hooks = self._store, self._hooks
        timer = store.get_timer(timer_id)
        if timer is None:
            return
        self._silence_other_ringer(timer_id)
        store.save_timer(dataclasses.replace(timer, state=STATE_FINISHED,
                                             remaining_ms=0, ends_at_ms=0))
        store.set_ringing(timer_id, True)
        started = try_ring and hooks.ring_start(timer_id, False)
        if not started:
            store.set_ringing(timer_id, False)
            hooks.missed(timer_id, TITLE_TIMER_FINISHED)
        else:
            hooks.show_alert("timer", timer_id)
        hooks.schedule_timers()
        hooks.refreshed()

    def stop_timer(self, timer_id: int) -> None:
        if self._store.is_ringing(timer_id):
            self._hooks.ring_stop()
        self._stop_timer_internal(timer_id)

    def _stop_timer_internal(self, timer_id: int) -> None:
        store = self._store
        store.set_ringing(timer_id, False)
        timer = store.get_timer(timer_id)
        if timer is None:
            return
        store.save_timer(dataclasses.replace(timer, state=STATE_IDLE,
                                             remaining_ms=timer.duration_ms, ends_at_ms=0))
        self._hooks.schedule_timers()
        self._hooks.refreshed()

    def add_minute_to_ringing_timer(self, timer_id: int) -> None:
        store, hooks = self._store, self._hooks
        hooks.ring_stop()
        store.set_ringing(timer_id, False)
        timer = store.get_timer(timer_id)
        if timer is None:
            return
        store.save_timer(dataclasses.replace(timer, state=STATE_RUNNING,
                                             ends_at_ms=self._clock() + 60_000, remaining_ms=0))
        hooks.schedule_timers()
        hooks.refreshed()

    def start_or_reuse_timer(self, duration_ms: int, label: str = "") -> TimerItem:
        store, hooks = self._store, self._hooks
        for timer in store.timers():
            if (timer.state == STATE_IDLE and timer.duration_ms == duration_ms
                    and timer.label == label):
                running = dataclasses.replace(timer, state=STATE_RUNNING,
                                              ends_at_ms=self._clock() + duration_ms,
                                              remaining_ms=0)
                store.save_timer(running)
                hooks.schedule_timers()
                hooks.refreshed()
                return running
        return self.start_timer(duration_ms, label)

    def start_timer(self, duration_ms: int, label: str = "") -> TimerItem:
        store, hooks = self._store, self._hooks
        timer = TimerItem(id=store.next_id(), duration_ms=duration_ms, state=STATE_RUNNING,
                          ends_at_ms=self._clock() + duration_ms, remaining_ms=0, label=label)
        store.save_timer(timer)
        hooks.schedule_timers()
        hooks.refreshed()
        return timer

    def pause_timer(self, timer_id: int) -> None:
        from . import timer_math
        store = self._store
        timer = store.get_timer(timer_id)
        if timer is None or timer.state != STATE_RUNNING:
            return
        remaining = timer_math.remaining_ms(timer, self._clock())
        store.save_timer(dataclasses.replace(timer, state="PAUSED",
                                             remaining_ms=remaining, ends_at_ms=0))
        self._hooks.schedule_timers()
        self._hooks.refreshed()

    def resume_timer(self, timer_id: int) -> None:
        store = self._store
        timer = store.get_timer(timer_id)
        if timer is None or timer.state != "PAUSED":
            return
        store.save_timer(dataclasses.replace(timer, state=STATE_RUNNING,
                                             ends_at_ms=self._clock() + timer.remaining_ms,
                                             remaining_ms=0))
        self._hooks.schedule_timers()
        self._hooks.refreshed()

    def add_minute(self, timer_id: int) -> None:
        store = self._store
        timer = store.get_timer(timer_id)
        if timer is None:
            return
        if timer.state == STATE_RUNNING:
            store.save_timer(dataclasses.replace(timer, ends_at_ms=timer.ends_at_ms + 60_000))
            self._hooks.schedule_timers()
            self._hooks.refreshed()
        elif timer.state == "PAUSED":
            store.save_timer(dataclasses.replace(timer, remaining_ms=timer.remaining_ms + 60_000))
            self._hooks.refreshed()

    def delete_timer(self, timer_id: int) -> None:
        if self._store.is_ringing(timer_id):
            self.stop_timer(timer_id)
        self._store.delete_timer(timer_id)
        self._hooks.schedule_timers()
        self._hooks.refreshed()

    def save_alarm(self, alarm: Alarm) -> None:
        store, hooks = self._store, self._hooks
        if store.is_ringing(alarm.id):
            hooks.ring_stop()
            store.set_ringing(alarm.id, False)
        store.save_alarm(alarm)
        store.clear_runtime(alarm.id)
        if alarm.enabled:
            hooks.schedule_alarm(
                alarm, next_occurrence.next_alarm_millis(
                    alarm.hour, alarm.minute, alarm.days_mask, self._clock()))
        else:
            hooks.cancel_alarm(alarm.id)
        hooks.refreshed()

    def delete_alarm(self, alarm_id: int) -> None:
        if self._store.is_ringing(alarm_id):
            self.dismiss(alarm_id)
        self._hooks.cancel_alarm(alarm_id)
        self._store.delete_alarm(alarm_id)
        self._hooks.refreshed()

    def toggle_alarm(self, alarm_id: int, enabled: bool) -> None:
        alarm = self._store.get_alarm(alarm_id)
        if alarm is None:
            return
        self.save_alarm(dataclasses.replace(alarm, enabled=enabled))

    def reconcile(self, force: bool = False, recover_ringing: bool = True) -> None:
        store, hooks = self._store, self._hooks
        now = self._clock()
        for alarm in store.alarms():
            try:
                if store.is_ringing(alarm.id) and recover_ringing:
                    store.set_ringing(alarm.id, False)
                    store.clear_runtime(alarm.id)
                    if alarm.repeating:
                        following = next_occurrence.next_alarm_millis(
                            alarm.hour, alarm.minute, alarm.days_mask, now)
                        hooks.schedule_alarm(alarm, following)
                    hooks.missed(alarm.id, TITLE_MISSED_ALARM)
                elif store.is_ringing(alarm.id):
                    pass
                elif not alarm.enabled:
                    snoozed = store.snoozed_until(alarm.id)
                    if snoozed > now:
                        if force or store.scheduled_fire(alarm.id) != snoozed:
                            hooks.schedule_alarm(alarm, snoozed)
                    else:
                        if 0 < snoozed <= now:
                            store.set_snoozed_until(alarm.id, 0)
                            hooks.missed(alarm.id, TITLE_MISSED_ALARM)
                        hooks.cancel_alarm(alarm.id)
                else:
                    snoozed = store.snoozed_until(alarm.id)
                    if snoozed > now:
                        expected = snoozed
                    else:
                        expected = next_occurrence.next_alarm_millis(
                            alarm.hour, alarm.minute, alarm.days_mask, now)
                    if force or store.scheduled_fire(alarm.id) != expected:
                        hooks.schedule_alarm(alarm, expected)
            except Exception:
                continue
        for timer in store.timers():
            if store.is_ringing(timer.id):
                if recover_ringing:
                    store.set_ringing(timer.id, False)
                    store.save_timer(dataclasses.replace(timer, state=STATE_IDLE,
                                                         remaining_ms=timer.duration_ms,
                                                         ends_at_ms=0))
                    hooks.missed(timer.id, TITLE_TIMER_FINISHED_AWAY)
                continue
            if timer.state == STATE_RUNNING and timer.ends_at_ms <= now:
                overdue = now - timer.ends_at_ms
                if overdue <= TIMER_BOOT_GRACE_MS:
                    self.fire_timer(timer.id, try_ring=True)
                else:
                    store.save_timer(dataclasses.replace(timer, state=STATE_FINISHED,
                                                         remaining_ms=0, ends_at_ms=0))
                    hooks.missed(timer.id, TITLE_TIMER_FINISHED_AWAY)
        hooks.schedule_timers()
        hooks.refreshed()
