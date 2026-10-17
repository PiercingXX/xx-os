"""Relative-time text (port of the until() half of util/Fmt.kt)."""

from xx_clock import fmt


def test_until_minutes_only():
    assert fmt.until(0, 7 * 60_000) == "7 m"


def test_until_hours_and_minutes():
    assert fmt.until(0, (7 * 3600 + 53 * 60) * 1000) == "7 h 53 m"


def test_until_days_hours_minutes():
    assert fmt.until(1_000_000, 1_000_000 + (2 * 86400 + 7 * 3600 + 53 * 60) * 1000) == "2 d 7 h 53 m"


def test_until_past_clamps_to_zero():
    assert fmt.until(5_000, 1_000) == "0 m"


def test_until_exact_hour():
    assert fmt.until(0, 3_600_000) == "1 h 0 m"


def test_format_time_returns_short_local_time():
    import time as _time
    text = fmt.format_time(int(_time.time() * 1000))
    assert text.strip()
    assert ":" in text
