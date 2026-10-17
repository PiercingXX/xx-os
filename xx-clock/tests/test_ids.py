"""Monotonic ids (translation of data/UniqueIdGenerationTest.kt)."""

import pytest

from xx_clock import ids as ids_module
from xx_clock.models import new_alarm, new_timer


@pytest.fixture()
def sequence():
    slot = [0]
    now_ms = [1_750_000_000_000]
    seq = ids_module.MonotonicIdSequence(load=lambda: slot[0],
                                         store=lambda value: slot.__setitem__(0, value),
                                         floor_of=lambda: now_ms[0])
    return seq, slot, now_ms


def test_same_ms_creates_all_distinct(sequence):
    seq, _slot, _now = sequence
    minted = [seq.next() for _ in range(64)]
    assert len(set(minted)) == 64


def test_alarms_and_timers_share_one_namespace(sequence):
    seq, _slot, _now = sequence
    alarms = [new_alarm(6, 15, seq.next()).id for _ in range(16)]
    timers = [new_timer(90_000, seq.next()).id for _ in range(16)]
    assert len(set(alarms + timers)) == 32


def test_first_mint_clears_legacy_ids_and_wall_clock():
    slot = [0]
    legacy = 1_700_000_000_123
    now_ms = 1_750_000_000_000
    seq = ids_module.MonotonicIdSequence(load=lambda: slot[0],
                                         store=lambda value: slot.__setitem__(0, value),
                                         floor_of=lambda: max(legacy, now_ms))
    minted = seq.next()
    assert minted > legacy
    assert minted > now_ms


def test_sequence_resumes_above_previous_mints_after_restart():
    slot = [0]
    now_ms = 1_750_000_000_000

    def _make():
        return ids_module.MonotonicIdSequence(load=lambda: slot[0],
                                              store=lambda value: slot.__setitem__(0, value),
                                              floor_of=lambda: now_ms)
    first = [_make().next() for _ in range(4)]
    resumed = _make().next()
    assert all(resumed > value for value in first)
