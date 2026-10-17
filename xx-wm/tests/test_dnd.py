"""Tests for dnd.py — schedules, repeat callers, starred matching."""
from datetime import datetime
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from config import ShellConfig
from dnd import DndState, normalize_number, numbers_match, schedule_matches


@pytest.fixture
def dnd(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    return DndState(ShellConfig())


# Monday 2026-07-20
MON_NOON = datetime(2026, 7, 20, 12, 0)
MON_2300 = datetime(2026, 7, 20, 23, 0)
TUE_0300 = datetime(2026, 7, 21, 3, 0)
TUE_0700 = datetime(2026, 7, 21, 7, 0)
SAT_2300 = datetime(2026, 7, 25, 23, 0)


class TestScheduleMatching:
    def test_simple_daytime_range(self):
        scheds = [{'days': [0, 1, 2, 3, 4], 'start': '09:00', 'end': '17:00'}]
        assert schedule_matches(scheds, MON_NOON)
        assert not schedule_matches(scheds, MON_2300)

    def test_overnight_range_crosses_midnight(self):
        # Monday 22:00 → 06:30: matches Mon late evening AND Tue early morning
        scheds = [{'days': [0], 'start': '22:00', 'end': '06:30'}]
        assert schedule_matches(scheds, MON_2300)
        assert schedule_matches(scheds, TUE_0300)
        assert not schedule_matches(scheds, TUE_0700)
        assert not schedule_matches(scheds, MON_NOON)

    def test_overnight_day_wrap_sunday_to_monday(self):
        scheds = [{'days': [6], 'start': '23:00', 'end': '05:00'}]
        assert schedule_matches(scheds, datetime(2026, 7, 26, 23, 30))  # Sunday night
        assert schedule_matches(scheds, datetime(2026, 7, 27, 4, 0))    # Monday early
        assert not schedule_matches(scheds, SAT_2300)

    def test_malformed_schedules_never_match(self):
        assert not schedule_matches([{'days': 'weekdays', 'start': '09:00', 'end': '17:00'}], MON_NOON)
        assert not schedule_matches([{'days': [0], 'start': '25:00', 'end': '17:00'}], MON_NOON)
        assert not schedule_matches([{'days': [0], 'start': None, 'end': '17:00'}], MON_NOON)
        assert not schedule_matches([], MON_NOON)


class TestMalformedEntriesSkippedAndLogged:
    def test_non_dict_entries_never_match_and_never_raise(self):
        bad = ['not a dict', None, 42, 3.5, ['days'],
               {'days': [0], 'start': '09:00'}]
        assert schedule_matches(bad, MON_NOON) is False

    def test_none_and_empty_never_match(self):
        assert schedule_matches(None, MON_NOON) is False
        assert schedule_matches([], MON_NOON) is False

    def test_top_level_non_list_never_raises(self):
        assert schedule_matches('soon', MON_NOON) is False
        assert schedule_matches(42, MON_NOON) is False
        assert schedule_matches({'days': [0], 'start': '09:00', 'end': '17:00'},
                                MON_NOON) is False

    def test_valid_entry_still_matches_alongside_garbage(self):
        scheds = ['garbage', None,
                  {'days': [0], 'start': '09:00', 'end': '17:00'}]
        assert schedule_matches(scheds, MON_NOON) is True
        assert schedule_matches(scheds, MON_2300) is False

    def test_malformed_entries_are_logged(self, caplog):
        with caplog.at_level('WARNING', logger='piercing.dnd'):
            assert schedule_matches(['nope', None,
                                     {'days': 'weekdays', 'start': '09:00',
                                      'end': '17:00'}], MON_NOON) is False
        assert any('malformed' in rec.message for rec in caplog.records)

    def test_top_level_garbage_is_logged(self, caplog):
        with caplog.at_level('WARNING', logger='piercing.dnd'):
            assert schedule_matches('soon', MON_NOON) is False
        assert any('malformed' in rec.message for rec in caplog.records)

    def test_is_active_with_malformed_config_never_crashes(self, dnd, caplog):
        dnd._config.data['dnd_schedules'] = [
            'nope', None, 42,
            {'days': [0], 'start': 'bad', 'end': 'also bad'},
        ]
        with caplog.at_level('WARNING', logger='piercing.dnd'):
            assert dnd.is_active(MON_NOON) is False
        assert any('malformed' in rec.message for rec in caplog.records)

    def test_matches_shared_module_on_valid_inputs(self):
        from xx_phone.schedules import schedule_matches as shared

        cases = [
            ([{'days': [0, 1, 2, 3, 4], 'start': '09:00', 'end': '17:00'}],
             MON_NOON),
            ([{'days': [0], 'start': '22:00', 'end': '06:30'}], MON_2300),
            ([{'days': [0], 'start': '22:00', 'end': '06:30'}], TUE_0300),
            ([{'days': [0], 'start': '22:00', 'end': '06:30'}], MON_NOON),
        ]
        for scheds, when in cases:
            assert schedule_matches(scheds, when) is shared(scheds, when)


class TestActive:
    def test_manual_toggle(self, dnd):
        assert not dnd.is_active(MON_NOON)
        dnd.set_enabled(True)
        assert dnd.is_active(MON_NOON)

    def test_schedule_driven(self, dnd):
        dnd._config.data['dnd_schedules'] = [{'days': [0], 'start': '22:00', 'end': '06:30'}]
        assert dnd.is_active(MON_2300)
        assert not dnd.is_active(MON_NOON)


class TestSetSchedules:
    def test_round_trips_and_drops_garbage(self, dnd):
        dnd.set_schedules([
            {'days': [0, 1], 'start': '22:00', 'end': '06:30'},
            {'days': 'nope', 'start': '09:00', 'end': '17:00'},
            'not a dict',
        ])
        assert dnd.schedules == [
            {'days': [0, 1], 'start': '22:00', 'end': '06:30'}]
        assert dnd.is_active(MON_2300)
        assert not dnd.is_active(MON_NOON)


class TestRepeatCaller:
    def test_second_call_within_window_is_exception(self, dnd):
        t0 = 1_000_000.0
        assert not dnd.is_exception('+15550102222', when=t0)
        dnd.note_call('+15550102222', when=t0)
        assert dnd.is_exception('+15550102222', when=t0 + 5 * 60)

    def test_call_outside_window_is_not(self, dnd):
        t0 = 1_000_000.0
        dnd.note_call('+15550102222', when=t0)
        assert not dnd.is_exception('+15550102222', when=t0 + 16 * 60)

    def test_number_formatting_variants_match(self, dnd):
        t0 = 1_000_000.0
        dnd.note_call('+1 (555) 010-2222', when=t0)
        assert dnd.is_exception('5550102222', when=t0 + 60)

    def test_different_number_is_not_exception(self, dnd):
        t0 = 1_000_000.0
        dnd.note_call('+15550102222', when=t0)
        assert not dnd.is_exception('+15550109999', when=t0 + 60)


class TestStarred:
    def test_starred_always_exception(self, dnd):
        dnd.set_starred('+1 555 010 2222', True)
        assert dnd.is_exception('5550102222', when=1.0)
        assert dnd.is_starred('(555) 010-2222')

    def test_unstar(self, dnd):
        dnd.set_starred('+15550102222', True)
        dnd.set_starred('555-010-2222', False)
        assert not dnd.is_starred('+15550102222')


class TestNormalization:
    def test_normalize(self):
        assert normalize_number('+1 (555) 010-2222') == '15550102222'

    def test_match_tail(self):
        assert numbers_match('+15550102222', '5550102222')
        assert not numbers_match('', '5550102222')
