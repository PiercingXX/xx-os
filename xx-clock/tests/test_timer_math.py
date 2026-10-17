"""Timer math (translation of time/TimerMathTest.kt). All clocks injected."""

from xx_clock import timer_math as math
from xx_clock.models import TimerItem


def _timer(state, duration_ms=60_000, ends_at=0, remaining=None):
    return TimerItem(id=1, duration_ms=duration_ms, state=state,
                     ends_at_ms=ends_at,
                     remaining_ms=duration_ms if remaining is None else remaining,
                     label="")


def test_idle_returns_full_duration_regardless_of_now():
    timer = _timer("IDLE", duration_ms=90_000)
    assert math.remaining_ms(timer, 0) == 90_000
    assert math.remaining_ms(timer, 999_999_999) == 90_000


def test_running_with_future_deadline_returns_remainder():
    timer = _timer("RUNNING", duration_ms=60_000, ends_at=1_000_000, remaining=0)
    assert math.remaining_ms(timer, 999_000) == 1_000


def test_running_past_deadline_clamps_to_zero():
    timer = _timer("RUNNING", duration_ms=60_000, ends_at=1_000_000, remaining=0)
    assert math.remaining_ms(timer, 1_000_001) == 0


def test_paused_returns_frozen_remainder():
    timer = _timer("PAUSED", duration_ms=60_000, remaining=25_000)
    assert math.remaining_ms(timer, 5_000_000) == 25_000


def test_finished_returns_zero():
    timer = _timer("FINISHED", duration_ms=60_000, remaining=0)
    assert math.remaining_ms(timer, 5_000_000) == 0


def test_expiry_boundary_now_equals_deadline_is_expired():
    timer = _timer("RUNNING", ends_at=1_000_000, remaining=0)
    assert math.is_expired(timer, 1_000_000)
    assert not math.is_expired(timer, 999_999)


def test_expiry_only_counts_running():
    timer = _timer("PAUSED", ends_at=1_000_000, remaining=10_000)
    assert not math.is_expired(timer, 2_000_000)


def test_display_formatting():
    assert math.display(0) == "00:00"
    assert math.display(59_999) == "01:00"
    assert math.display(3_600_000) == "1:00:00"
    assert math.display(3_661_000) == "1:01:01"


def test_display_rounds_up_one_ms():
    assert math.display(1) == "00:01"


def test_display_never_negative():
    assert math.display(-5_000) == "00:00"
