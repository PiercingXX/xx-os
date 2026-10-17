"""D-Bus input validation (translation of alarm/AlarmClockApiTest.kt).

Android intents are OUT; the pure request rules live on guarding the
daemon's methods. Weekdays are ISO (Monday=0..Sunday=6) here.
"""

from xx_clock import alarm_logic as logic
from xx_clock import api_validate as api
from xx_clock import next_occurrence
from xx_clock.models import Alarm, TimerItem


def _alarm(alarm_id, hour=7, minute=0, mask=0, label="", enabled=True):
    return Alarm(id=alarm_id, hour=hour, minute=minute, days_mask=mask,
                 label=label, enabled=enabled, vibrate=True)


def test_set_alarm_rejects_missing_or_invalid_hour():
    assert api.set_alarm_request(None, 0, [], "", True, None, True) is None
    assert api.set_alarm_request(24, 0, [], "", True, None, True) is None
    assert api.set_alarm_request(7, 60, [], "", True, None, True) is None


def test_set_alarm_maps_weekdays_and_drops_silent_ringtone():
    request = api.set_alarm_request(6, 30, [logic.MONDAY, logic.FRIDAY],
                                    "  Gym  ", False, api.RINGTONE_SILENT, True)
    assert request is not None
    assert (request.hour, request.minute) == (6, 30)
    assert request.days_mask == (logic.bit_for_weekday(logic.MONDAY)
                                 | logic.bit_for_weekday(logic.FRIDAY))
    assert request.label == "Gym"
    assert request.vibrate is False
    assert request.sound_uri is None
    assert request.skip_ui is True


def test_set_alarm_keeps_a_real_sound():
    request = api.set_alarm_request(6, 30, [], "", True, "file:///tone.ogg", False)
    assert request is not None
    assert request.sound_uri == "file:///tone.ogg"


def test_set_timer_accepts_1s_through_24h_and_rejects_rest():
    assert api.set_timer_request(None, "", True) is None
    assert api.set_timer_request(0, "", True) is None
    assert api.set_timer_request(86_401, "", True) is None
    request = api.set_timer_request(90, "Eggs", True)
    assert request is not None
    assert request.duration_ms == 90_000
    assert request.label == "Eggs"


def test_search_hour_applies_am_pm_only_to_1_through_12():
    assert api.search_hour(7, True) == 19
    assert api.search_hour(7, False) == 7
    assert api.search_hour(12, True) == 12
    assert api.search_hour(12, False) == 0
    assert api.search_hour(19, True) == 19
    assert api.search_hour(7, None) == 7
    assert api.search_hour(None, True) is None


def test_dismiss_with_no_mode_dismisses_ringer_or_only_enabled():
    one, two = _alarm(1), _alarm(2, hour=8)
    assert [a.id for a in api.alarms_to_dismiss([one, two], 1, 2, None, None, None, None)] == [1]
    assert [a.id for a in api.alarms_to_dismiss([one], None, 1, None, None, None, None)] == [1]
    assert api.alarms_to_dismiss([one, two], None, 1, None, None, None, None) == []


def test_dismiss_next_prefers_ringer_then_next_armed():
    one, two = _alarm(1), _alarm(2)
    assert api.alarms_to_dismiss([one, two], 1, 2, api.SEARCH_NEXT, None, None, None)[0].id == 1
    assert api.alarms_to_dismiss([one, two], None, 2, api.SEARCH_NEXT, None, None, None)[0].id == 2


def test_dismiss_time_label_and_all():
    gym = _alarm(1, hour=6, minute=30, label="Gym")
    work = _alarm(2, hour=7, minute=0, label="Work")
    off = _alarm(3, hour=6, minute=30, label="Gym", enabled=False)
    assert [a.id for a in api.alarms_to_dismiss(
        [gym, work, off], None, None, api.SEARCH_TIME, 6, 30, None)] == [1]
    assert [a.id for a in api.alarms_to_dismiss(
        [gym, work, off], None, None, api.SEARCH_LABEL, None, None, "gym")] == [1]
    assert [a.id for a in api.alarms_to_dismiss(
        [gym, work, off], None, None, api.SEARCH_ALL, None, None, None)] == [1, 2]


def test_skip_upcoming_of_repeating_is_strictly_after_next_slot():
    weekly = _alarm(1, hour=7, minute=0, mask=logic.ALL_DAYS_MASK)
    now = 1_700_000_000_000
    following = next_occurrence.next_alarm_millis(7, 0, logic.ALL_DAYS_MASK, now)
    skip = api.skip_upcoming_until_ms(weekly, now)
    assert skip == following + 1
    assert next_occurrence.next_alarm_millis(7, 0, logic.ALL_DAYS_MASK, skip) > following
    assert api.skip_upcoming_until_ms(_alarm(2), now) is None


def test_timers_to_dismiss_are_ringer_matching_label_or_finished():
    idle = TimerItem(1, 60_000, "IDLE", 0, 60_000, "Eggs")
    done = TimerItem(2, 60_000, "FINISHED", 0, 0, "Eggs")
    running = TimerItem(3, 90_000, "RUNNING", 9, 0, "Pasta")
    assert [t.id for t in api.timers_to_dismiss([idle, done, running], 3, None)] == [3]
    assert [t.id for t in api.timers_to_dismiss([idle, done, running], None, None)] == [2]
    assert [t.id for t in api.timers_to_dismiss([idle, done, running], None, "egg")] == [2]


def test_snooze_minutes_default_and_clamp():
    assert api.snooze_minutes(None, 10) == 10
    assert api.snooze_minutes(0, 10) == 1
    assert api.snooze_minutes(999, 10) == 120
    assert api.snooze_minutes(15, 10) == 15


def test_identical_alarm_and_reusable_timer_match():
    request = api.set_alarm_request(6, 30, [logic.MONDAY], "Gym", True, None, True)
    assert request is not None
    same = Alarm(id=5, hour=6, minute=30,
                 days_mask=logic.bit_for_weekday(logic.MONDAY),
                 label="Gym", enabled=True, vibrate=True, sound_uri=None)
    assert api.identical_alarm([_alarm(1), same], request) is same
    timer_request = api.set_timer_request(60, "", True)
    assert timer_request is not None
    idle = TimerItem(7, 60_000, "IDLE", 0, 60_000, "")
    assert api.reusable_timer([idle], timer_request) is idle
