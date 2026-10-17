"""Recurrence math (translation of time/NextOccurrenceTest.kt)."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from xx_clock import alarm_logic as logic
from xx_clock.next_occurrence import next_alarm, next_alarm_millis

UTC = ZoneInfo("UTC")
NEW_YORK = ZoneInfo("America/New_York")


def _at(year, month, day, hour, minute, zone=UTC):
    return datetime(year, month, day, hour, minute, tzinfo=zone)


def test_one_shot_future_today_returns_today():
    now = _at(2024, 5, 6, 6, 0)
    assert next_alarm(now, 7, 30, 0) == _at(2024, 5, 6, 7, 30)


def test_one_shot_passed_today_returns_tomorrow():
    now = _at(2024, 5, 6, 8, 0)
    assert next_alarm(now, 7, 30, 0) == _at(2024, 5, 7, 7, 30)


def test_weekly_mask_skips_non_matching_days():
    monday = _at(2024, 5, 6, 6, 0)
    assert monday.weekday() == 0
    mask = logic.bit_for_weekday(2)
    assert next_alarm(monday, 7, 30, mask) == _at(2024, 5, 8, 7, 30)


def test_weekly_mask_skips_today_when_slot_passed():
    monday_late = _at(2024, 5, 6, 8, 0)
    mask = logic.bit_for_weekday(0)
    assert next_alarm(monday_late, 7, 30, mask) == _at(2024, 5, 13, 7, 30)


def test_week_boundary_wrap_sunday_from_saturday():
    saturday = _at(2024, 5, 11, 20, 0)
    mask = logic.bit_for_weekday(6)
    assert next_alarm(saturday, 7, 30, mask) == _at(2024, 5, 12, 7, 30)


def test_every_day_mask_finds_tomorrow_when_passed():
    now = _at(2024, 5, 6, 8, 0)
    assert next_alarm(now, 7, 30, logic.ALL_DAYS_MASK) == _at(2024, 5, 7, 7, 30)


def test_candidate_exactly_equal_to_now_is_accepted():
    now = _at(2024, 5, 6, 7, 30)
    assert next_alarm(now, 7, 30, 0) == now


def test_result_never_before_now_across_samples():
    base = _at(2024, 5, 6, 12, 0)
    masks = (0, logic.WEEKDAYS_MASK, logic.WEEKENDS_MASK, logic.ALL_DAYS_MASK,
             logic.bit_for_weekday(4))
    for hours in range(0, 48, 3):
        now = base + timedelta(hours=hours, minutes=17)
        for hour in (0, 7, 12, 23):
            for mask in masks:
                assert next_alarm(now, hour, 30, mask) >= now


def test_dst_spring_forward_gap_resolves_without_exception():
    now = datetime(2024, 3, 10, 1, 30, tzinfo=NEW_YORK)
    resolved = next_alarm(now, 2, 30, 0)
    assert resolved >= now


def test_dst_fall_back_ambiguous_resolves_without_exception():
    now = datetime(2024, 11, 3, 0, 30, tzinfo=NEW_YORK)
    resolved = next_alarm(now, 1, 30, 0)
    assert resolved >= now


def test_invalid_hour_or_minute_rejected():
    now = _at(2024, 5, 6, 6, 0)
    with pytest.raises(ValueError):
        next_alarm(now, 24, 0, 0)
    with pytest.raises(ValueError):
        next_alarm(now, 7, 60, 0)


def test_millis_variant_matches_datetime_variant():
    now_ms = int(_at(2024, 5, 6, 8, 0).timestamp() * 1000)
    expected = int(_at(2024, 5, 7, 7, 30).timestamp() * 1000)
    assert next_alarm_millis(7, 30, 0, now_ms, "UTC") == expected
