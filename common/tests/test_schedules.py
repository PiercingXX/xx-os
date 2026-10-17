"""DnD/Focus schedule matcher: day windows, overnight wrap, malformed input."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from xx_phone import schedules  # noqa: E402


def at(weekday_monday0, hour, minute=0):
    base = datetime(2026, 10, 12 + weekday_monday0, hour, minute)
    assert base.weekday() == weekday_monday0
    return base


def test_day_window_matches_inside():
    sched = [{"days": [0], "start": "09:00", "end": "17:00"}]
    assert schedules.schedule_matches(sched, at(0, 10)) is True
    assert schedules.schedule_matches(sched, at(0, 8, 59)) is False
    assert schedules.schedule_matches(sched, at(0, 17)) is False
    assert schedules.schedule_matches(sched, at(1, 10)) is False


def test_overnight_wraps_onto_next_day():
    sched = [{"days": [0], "start": "22:00", "end": "06:30"}]
    assert schedules.schedule_matches(sched, at(0, 23)) is True
    assert schedules.schedule_matches(sched, at(1, 6)) is True
    assert schedules.schedule_matches(sched, at(1, 7)) is False
    assert schedules.schedule_matches(sched, at(0, 21)) is False


def test_malformed_entries_never_match():
    bad = ["soon", None, 42, {}, {"days": "soon", "start": "09:00",
                                  "end": "17:00"},
           {"days": [0], "start": "99:99", "end": "17:00"},
           {"days": [0], "start": "09:00"}]
    assert schedules.schedule_matches(bad, at(0, 10)) is False
    assert schedules.schedule_matches(None, at(0, 10)) is False
    assert schedules.schedule_matches([], at(0, 10)) is False


def test_parse_hhmm_rejects_garbage():
    assert schedules.parse_hhmm("09:30") == 570
    assert schedules.parse_hhmm("9:05") == 545
    assert schedules.parse_hhmm("24:00") is None
    assert schedules.parse_hhmm("09:60") is None
    assert schedules.parse_hhmm("nope") is None
    assert schedules.parse_hhmm(None) is None
    assert schedules.parse_hhmm(930) is None
