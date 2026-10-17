"""Stable schedule keys (translation of scheduler/SchedulerRequestCodeTest.kt)."""

from xx_clock import request_codes as codes

IDS = [1_750_000_000_123,
       1_750_000_000_123 + (1 << 32),
       1_750_000_000_123 + (2 << 32),
       1_750_000_000_123 + (3 << 32),
       5_000_000_000_000,
       5_000_000_000_001]
KINDS = [codes.REQ_KIND_ALARM, codes.REQ_KIND_TIMER, codes.REQ_KIND_SHOW]


def test_spread_members_truncate_identically_sanity():
    assert len({entry & 0xFFFFFFFF for entry in IDS[:4]}) == 1


def test_truncation_twins_get_distinct_codes_per_kind():
    for kind in KINDS:
        seen = {codes.request_code(kind, entry) for entry in IDS}
        assert len(seen) == len(IDS), f"kind {kind} must distinguish ids 2^32 apart"


def test_same_kind_and_id_always_same_code():
    for entry in IDS:
        for kind in KINDS:
            assert codes.request_code(kind, entry) == codes.request_code(kind, entry)


def test_kinds_never_share_a_code_for_identical_ids():
    for entry in IDS:
        seen = {codes.request_code(kind, entry) for kind in KINDS}
        assert len(seen) == 3


def test_codes_stay_non_negative():
    for entry in IDS:
        for kind in KINDS:
            assert codes.request_code(kind, entry) >= 0
