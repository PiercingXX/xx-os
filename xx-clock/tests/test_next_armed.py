"""Next-armed selection (translation of repo/NextArmedTest.kt)."""

from xx_clock import alarm_logic as logic
from xx_clock.models import Alarm
from xx_clock.next_armed import next_armed

NOW = 1_000_000


def _alarm(alarm_id, enabled, mask=0):
    return Alarm(id=alarm_id, hour=7, minute=30, days_mask=mask, label="",
                 enabled=enabled, vibrate=True)


def _select(alarms, snooze=None, scheduled=None, now_ms=NOW):
    snooze = snooze or {}
    scheduled = scheduled or {}
    return next_armed(alarms, lambda aid: snooze.get(aid, 0),
                      lambda aid: scheduled.get(aid, 0), now_ms)


def test_snoozed_one_shot_armed_although_disabled():
    fired = _alarm(1, enabled=False)
    at = NOW + 10 * 60_000
    found = _select([fired], snooze={1: at})
    assert found is not None
    assert found[0].id == 1
    assert found[1] == at


def test_disabled_one_shot_without_snooze_not_armed():
    assert _select([_alarm(1, enabled=False)]) is None


def test_elapsed_snooze_does_not_arm():
    found = _select([_alarm(1, enabled=False)], snooze={1: NOW - 1},
                    scheduled={1: NOW - 2})
    assert found is None


def test_timestamp_keeps_later_of_snooze_and_scheduled():
    weekly = _alarm(2, enabled=True, mask=logic.ALL_DAYS_MASK)
    assert _select([weekly], snooze={2: NOW + 90_000},
                   scheduled={2: NOW + 30_000})[1] == NOW + 90_000
    assert _select([weekly], snooze={2: NOW + 30_000},
                   scheduled={2: NOW + 90_000})[1] == NOW + 90_000


def test_soonest_armed_wins_unarmed_skipped():
    snoozed = _alarm(1, enabled=False)
    weekly = _alarm(2, enabled=True, mask=logic.ALL_DAYS_MASK)
    muted = _alarm(3, enabled=False)
    found = _select([muted, snoozed, weekly],
                    snooze={1: NOW + 5 * 60_000},
                    scheduled={1: NOW + 5 * 60_000, 2: NOW + 3 * 60_000})
    assert found is not None
    assert found[0].id == 2
    assert found[1] == NOW + 3 * 60_000
