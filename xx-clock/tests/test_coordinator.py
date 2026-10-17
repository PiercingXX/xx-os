"""Lifecycle rules (translation of alarm/AlarmCoordinatorTest.kt).

The coordinator drives an in-memory-behaving store with recorder hooks —
the same seams the Android tests use — so every policy below holds on the
device too.
"""

import dataclasses

import pytest

from xx_clock import alarm_logic as logic
from xx_clock.coordinator import Coordinator, Hooks
from xx_clock.models import Alarm
from xx_clock.store import Store

NOW = 1_700_000_000_000


class Script:
    def __init__(self, store, now=NOW):
        self.now = now
        self.ring_calls: list = []
        self.stops = 0
        self.alerts: list = []
        self.scheduled: list = []
        self.cancelled: list = []
        self.timers_scheduled = 0
        self.missed: list = []
        self.refreshed = 0
        self.ring_ok = True
        hooks = Hooks(
            ring_start=self._ring_start, ring_stop=self._ring_stop,
            show_alert=lambda kind, eid: self.alerts.append((kind, eid)),
            schedule_alarm=self._schedule_alarm,
            cancel_alarm=lambda eid: self.cancelled.append(eid),
            schedule_timers=self._schedule_timers,
            refreshed=self._refreshed,
            missed=lambda eid, title: self.missed.append((eid, title)))
        self.coordinator = Coordinator(store, clock=lambda: self.now, hooks=hooks)

    def _ring_start(self, entry_id, is_alarm):
        self.ring_calls.append((entry_id, is_alarm))
        return self.ring_ok

    def _ring_stop(self):
        self.stops += 1

    def _schedule_alarm(self, alarm, at_ms):
        self.scheduled.append((alarm.id, at_ms))

    def _schedule_timers(self):
        self.timers_scheduled += 1

    def _refreshed(self):
        self.refreshed += 1


@pytest.fixture()
def env(tmp_path):
    store = Store(path=tmp_path / "clock.db")
    script = Script(store)
    yield store, script
    store.close()


def _alarm(store, hour=7, minute=0, mask=0, enabled=True):
    alarm = Alarm(id=store.next_id(), hour=hour, minute=minute, days_mask=mask,
                  label="", enabled=enabled, vibrate=True)
    store.save_alarm(alarm)
    return alarm


def test_one_shot_disables_itself_at_fire(env):
    store, script = env
    alarm = _alarm(store)
    script.coordinator.fire_alarm(alarm.id)
    assert store.get_alarm(alarm.id).enabled is False
    assert store.is_ringing(alarm.id)
    assert script.alerts == [("alarm", alarm.id)]


def test_snoozed_one_shot_still_armed_although_disabled(env):
    store, script = env
    alarm = _alarm(store)
    script.coordinator.fire_alarm(alarm.id)
    script.coordinator.snooze(alarm.id, 10)
    assert store.get_alarm(alarm.id).enabled is False
    assert store.snoozed_until(alarm.id) == NOW + 10 * 60_000
    assert script.scheduled[-1] == (alarm.id, NOW + 10 * 60_000)


def test_recurring_snooze_leaves_definition_armed_and_books_wakeup(env):
    store, script = env
    alarm = _alarm(store, mask=logic.ALL_DAYS_MASK)
    script.coordinator.fire_alarm(alarm.id)
    assert store.get_alarm(alarm.id).enabled is True
    script.coordinator.snooze(alarm.id, 5)
    assert store.snoozed_until(alarm.id) == NOW + 5 * 60_000


def test_firing_at_exact_wall_instant_rearms_next_occurrence(env):
    store, script = env
    alarm = _alarm(store, hour=7, minute=0, mask=logic.ALL_DAYS_MASK)
    from xx_clock import next_occurrence
    expected = next_occurrence.next_alarm_millis(7, 0, logic.ALL_DAYS_MASK, NOW + 1)
    script.coordinator.fire_alarm(alarm.id)
    assert (alarm.id, expected) in script.scheduled


def test_firing_alarm_takes_over_current_ringer(env):
    store, script = env
    first = _alarm(store, hour=6)
    second = _alarm(store, hour=7)
    script.coordinator.fire_alarm(first.id)
    assert store.is_ringing(first.id)
    script.coordinator.fire_alarm(second.id)
    assert not store.is_ringing(first.id)
    assert store.is_ringing(second.id)
    assert script.alerts[-1] == ("alarm", second.id)


def test_failed_ring_leaves_one_shot_on_and_posts_missed(env):
    store, script = env
    script.ring_ok = False
    alarm = _alarm(store)
    script.coordinator.fire_alarm(alarm.id)
    assert store.get_alarm(alarm.id).enabled is True
    assert not store.is_ringing(alarm.id)
    assert script.missed and script.missed[0][0] == alarm.id


def test_resetting_non_ringing_timer_keeps_alarm_ringing(env):
    store, script = env
    alarm = _alarm(store)
    script.coordinator.fire_alarm(alarm.id)
    timer = script.coordinator.start_timer(60_000, "")
    script.coordinator.stop_timer(timer.id)
    assert store.is_ringing(alarm.id)
    assert script.stops == 0


def test_resetting_ringing_timer_stops_ring_and_goes_idle(env):
    store, script = env
    timer = script.coordinator.start_timer(60_000, "")
    script.now = timer.ends_at_ms
    script.coordinator.fire_timer(timer.id)
    assert store.is_ringing(timer.id)
    script.coordinator.stop_timer(timer.id)
    assert not store.is_ringing(timer.id)
    assert store.get_timer(timer.id).state == "IDLE"
    assert script.stops == 1


def test_timer_expiry_within_grace_still_rings_on_reconcile(env):
    store, script = env
    timer = script.coordinator.start_timer(60_000, "")
    script.now = timer.ends_at_ms + 5 * 60_000
    script.coordinator.reconcile(force=True, recover_ringing=True)
    assert store.is_ringing(timer.id)


def test_timer_expired_long_ago_becomes_missed_not_ringing(env):
    store, script = env
    timer = script.coordinator.start_timer(60_000, "")
    script.now = timer.ends_at_ms + 60 * 60_000
    script.coordinator.reconcile(force=True, recover_ringing=True)
    assert not store.is_ringing(timer.id)
    assert store.get_timer(timer.id).state == "FINISHED"
    assert script.missed and script.missed[0][0] == timer.id


def test_boot_reconcile_re_registers_enabled_and_snoozed(env):
    store, script = env
    weekly = _alarm(store, mask=logic.ALL_DAYS_MASK)
    one_shot = _alarm(store, hour=8, enabled=False)
    store.set_snoozed_until(one_shot.id, NOW + 60_000)
    script.coordinator.reconcile(force=True, recover_ringing=True)
    assert {aid for aid, _at in script.scheduled} == {weekly.id, one_shot.id}


def test_mid_ring_death_becomes_missed_on_reconcile(env):
    store, script = env
    alarm = _alarm(store)
    store.set_ringing(alarm.id, True)
    script.coordinator.reconcile(force=True, recover_ringing=True)
    assert not store.is_ringing(alarm.id)
    assert script.missed and script.missed[0][0] == alarm.id


def test_live_ringer_not_mistaken_for_missed(env):
    store, script = env
    alarm = _alarm(store)
    store.set_ringing(alarm.id, True)
    script.coordinator.reconcile(force=False, recover_ringing=False)
    assert store.is_ringing(alarm.id)
    assert script.missed == []


def test_garbage_row_cannot_abort_boot_reconcile(env):
    store, script = env
    good = _alarm(store, mask=logic.ALL_DAYS_MASK)
    with store._lock:
        store._db.execute("INSERT INTO alarms(id, hour, minute, days_mask, label, enabled,"
                          " vibrate, sound_uri) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                          (good.id + 5000, 25, 0, 0, "garbage", 1, 1, None))
        store._db.commit()
    script.coordinator.reconcile(force=True, recover_ringing=True)
    assert any(aid == good.id for aid, _at in script.scheduled)


def test_start_or_reuse_reuses_identical_idle_timer(env):
    store, script = env
    first = script.coordinator.start_timer(60_000, "tea")
    script.coordinator.stop_timer(first.id)
    second = script.coordinator.start_or_reuse_timer(60_000, "tea")
    assert second.id == first.id
    assert store.get_timer(first.id).state == "RUNNING"


def test_add_minute_while_running_extends_deadline(env):
    store, script = env
    timer = script.coordinator.start_timer(60_000, "")
    before = store.get_timer(timer.id).ends_at_ms
    script.coordinator.add_minute(timer.id)
    assert store.get_timer(timer.id).ends_at_ms == before + 60_000


def test_add_minute_to_ringing_restarts_one_minute(env):
    store, script = env
    timer = script.coordinator.start_timer(60_000, "")
    script.coordinator.fire_timer(timer.id)
    script.coordinator.add_minute_to_ringing_timer(timer.id)
    restarted = store.get_timer(timer.id)
    assert restarted.state == "RUNNING"
    assert restarted.ends_at_ms == NOW + 60_000
    assert not store.is_ringing(timer.id)


def test_save_after_toggle_off_stays_disabled(env):
    store, script = env
    alarm = _alarm(store)
    script.coordinator.toggle_alarm(alarm.id, False)
    edited = dataclasses.replace(store.get_alarm(alarm.id), label="Work")
    script.coordinator.save_alarm(edited)
    assert store.get_alarm(alarm.id).enabled is False
    assert store.get_alarm(alarm.id).label == "Work"


def test_delete_ringing_alarm_silences_first(env):
    store, script = env
    alarm = _alarm(store)
    script.coordinator.fire_alarm(alarm.id)
    script.coordinator.delete_alarm(alarm.id)
    assert script.stops == 1
    assert store.get_alarm(alarm.id) is None
