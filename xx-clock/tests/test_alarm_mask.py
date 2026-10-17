"""Days-mask rules (translation of model/AlarmMaskTest.kt).

Android java.util.Calendar constants map to ISO weekdays here
(Monday=0..Sunday=6); the bit layout (Mon=bit0..Sun=bit6) and every
summary string are identical.
"""

import pytest

from xx_clock import alarm_logic as logic
from xx_clock.models import Alarm

ALL = [logic.MONDAY, logic.TUESDAY, logic.WEDNESDAY, logic.THURSDAY,
       logic.FRIDAY, logic.SATURDAY, logic.SUNDAY]


def test_bits_run_mon_to_sun():
    assert [logic.bit_for_weekday(day) for day in ALL] == [1 << bit for bit in range(7)]


def test_bits_are_seven_distinct_powers_of_two():
    bits = [logic.bit_for_weekday(day) for day in ALL]
    assert len(set(bits)) == 7
    assert all(bit != 0 and bit & (bit - 1) == 0 for bit in bits)


def test_unknown_day_maps_to_zero():
    assert logic.bit_for_weekday(7) == 0
    assert logic.bit_for_weekday(-1) == 0


def test_round_trip_day_to_bit_and_back():
    for day in ALL:
        assert logic.calendar_days(logic.bit_for_weekday(day)) == [day]


def test_calendar_days_orders_monday_first():
    assert logic.calendar_days(logic.ALL_DAYS_MASK) == ALL
    assert logic.calendar_days(logic.WEEKDAYS_MASK) == ALL[:5]
    assert logic.calendar_days(logic.WEEKENDS_MASK) == ALL[5:]


def test_summary_for_empty_mask_is_once():
    assert logic.summary(0) == "Once"


def test_summary_for_weekdays_weekends_every_day():
    assert logic.summary(logic.WEEKDAYS_MASK) == "Weekdays"
    assert logic.summary(logic.WEEKENDS_MASK) == "Weekends"
    assert logic.summary(logic.ALL_DAYS_MASK) == "Every day"


def test_summary_for_custom_selection_lists_short_names_monday_first():
    mask = logic.bit_for_weekday(logic.WEDNESDAY) | logic.bit_for_weekday(logic.MONDAY)
    assert logic.summary(mask) == "Mon, Wed"
    assert logic.summary(logic.bit_for_weekday(logic.TUESDAY)) == "Tue"
    other = logic.bit_for_weekday(logic.SUNDAY) | logic.bit_for_weekday(logic.FRIDAY)
    assert logic.summary(other) == "Fri, Sun"


def test_repeating_is_true_exactly_when_mask_non_zero():
    assert not Alarm(id=1, hour=7, minute=0, days_mask=0).repeating
    assert Alarm(id=2, hour=7, minute=30,
                 days_mask=logic.bit_for_weekday(logic.MONDAY)).repeating


@pytest.mark.parametrize("hour,minute", [(0, 0), (7, 30), (23, 59)])
def test_mask_independent_of_time(hour, minute):
    assert logic.summary(0) == "Once"
    alarm = Alarm(id=9, hour=hour, minute=minute, days_mask=logic.ALL_DAYS_MASK)
    assert alarm.repeating
