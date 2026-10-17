"""Daemon Api over a temp store: mutations, validation, state shape."""

import json

import pytest

from xx_clock import prefs
from xx_clock.coordinator import Hooks
from xx_clock.daemon import Api, alarm_from_dict, alarm_to_dict
from xx_clock.store import Store

NOW = 1_700_000_000_000


@pytest.fixture()
def api(tmp_path):
    store = Store(path=tmp_path / "clock.db")
    seen = {"ring": [], "missed": [], "alerts": []}
    hooks = Hooks(
        ring_start=lambda eid, is_alarm: seen["ring"].append((eid, is_alarm)) or True,
        show_alert=lambda kind, eid: seen["alerts"].append((kind, eid)),
        schedule_alarm=lambda alarm, at_ms: store.set_scheduled_fire(alarm.id, at_ms),
        cancel_alarm=lambda eid: store.clear_scheduled_fire(eid),
        missed=lambda eid, title: seen["missed"].append((eid, title)))
    owned = Api(store, settings=prefs.Settings(), clock=lambda: NOW, hooks=hooks)
    yield owned, seen
    store.close()


def test_save_and_read_alarm_round_trip(api):
    owned, _seen = api
    alarm_id = owned.save_alarm({"hour": 7, "minute": 30, "days_mask": 31,
                                 "label": "Work", "enabled": True,
                                 "vibrate": True, "sound_uri": None})
    assert alarm_id > 0
    state = owned.get_state()
    assert len(state["alarms"]) == 1
    assert state["alarms"][0]["label"] == "Work"
    assert json.loads(json.dumps(state))


def test_save_rejects_invalid_time(api):
    owned, _seen = api
    assert owned.save_alarm({"hour": 25, "minute": 0}) == -1
    assert owned.save_alarm({"hour": 7}) == -1
    assert owned.get_state()["alarms"] == []


def test_save_unknown_id_rejected_but_update_accepted(api):
    owned, _seen = api
    assert owned.save_alarm({"id": 424242, "hour": 7, "minute": 0}) == -1
    alarm_id = owned.save_alarm({"hour": 7, "minute": 0})
    assert owned.save_alarm({"id": alarm_id, "hour": 8, "minute": 15}) == alarm_id
    assert owned.get_state()["alarms"][0]["hour"] == 8


def test_toggle_delete_and_dismiss(api):
    owned, seen = api
    alarm_id = owned.save_alarm({"hour": 7, "minute": 0})
    assert owned.toggle_alarm(alarm_id, False) is True
    assert owned.get_state()["alarms"][0]["enabled"] is False
    assert owned.toggle_alarm(987654, False) is False
    assert owned.delete_alarm(987654) is False
    assert owned.delete_alarm(alarm_id) is True
    assert owned.get_state()["alarms"] == []


def test_next_reports_soonest_ring_with_texts(api):
    owned, _seen = api
    alarm_id = owned.save_alarm({"hour": 7, "minute": 0, "label": "Gym"})
    upcoming = owned.get_state()["next"]
    assert upcoming is not None
    assert upcoming["id"] == alarm_id
    assert upcoming["fire_at_ms"] > NOW
    assert upcoming["fire_text"].strip()
    assert upcoming["until_text"].strip()


def test_timer_lifecycle_and_validation(api):
    owned, _seen = api
    assert owned.start_timer(0, "") == -1
    assert owned.start_timer(86_400_001, "") == -1
    timer_id = owned.start_timer(60_000, "tea")
    assert timer_id > 0
    assert owned.pause_timer(timer_id) is True
    assert owned.resume_timer(timer_id) is True
    assert owned.add_minute_timer(timer_id) is True
    assert owned.get_state()["timers"][0]["duration_ms"] == 60_000
    assert owned.pause_timer(987654) is False
    assert owned.reset_timer(timer_id) is True
    assert owned.get_state()["timers"][0]["state"] == "IDLE"
    assert owned.delete_timer(timer_id) is True
    assert owned.get_state()["timers"] == []


def test_snooze_uses_default_and_clamps(api):
    owned, seen = api
    alarm_id = owned.save_alarm({"hour": 7, "minute": 0})
    owned.coordinator.fire_alarm(alarm_id)
    assert owned.snooze_ringing(alarm_id, 0) is True
    assert owned._store.snoozed_until(alarm_id) == NOW + 1 * 60_000
    assert owned.dismiss_ringing(alarm_id) is True
    assert not owned._store.is_ringing(alarm_id)
    assert owned.snooze_ringing(987654, 10) is False


def test_state_reports_ringing_entry(api):
    owned, _seen = api
    alarm_id = owned.save_alarm({"hour": 7, "minute": 0, "label": "Wake"})
    owned.coordinator.fire_alarm(alarm_id)
    ringing = owned.get_state()["ringing"]
    assert ringing == {"id": alarm_id, "kind": "alarm", "label": "Wake"}


def test_alarm_dict_helpers_reject_garbage():
    assert alarm_from_dict({"hour": "x", "minute": 0}, 1) is None
    assert alarm_from_dict({"hour": 7, "minute": 0, "label": "x" * 500}, 1).label != ""
    alarm = alarm_from_dict({"hour": 7, "minute": 5}, 3)
    assert alarm is not None
    assert alarm_to_dict(alarm)["id"] == 3
