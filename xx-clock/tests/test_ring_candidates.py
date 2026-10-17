"""Ring fallback order (translation of audio/RingCandidatesTest.kt)."""

from xx_clock import ring_candidates as module


def test_chosen_tone_first_defaults_follow():
    assert module.ring_candidates("chosen", "alarm-default",
                                  "notif-default") == ["chosen", "alarm-default", "notif-default"]


def test_no_chosen_tone_falls_to_defaults():
    assert module.ring_candidates(None, "alarm-default",
                                  "notif-default") == ["alarm-default", "notif-default"]


def test_chosen_tone_matching_default_not_attempted_twice():
    assert module.ring_candidates("same", "same", "other") == ["same", "other"]


def test_blank_and_null_entries_dropped():
    assert module.ring_candidates("  ", None, "") == []
    assert module.ring_candidates("", "ok", None) == ["ok"]


def test_everything_missing_yields_empty_list():
    assert module.ring_candidates(None, None, None) == []
