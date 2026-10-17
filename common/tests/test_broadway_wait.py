"""Tests for the shared Broadway wait-until helper."""

import threading
import time

from xx_testing import broadway_wait as wait_mod


def test_poll_until_true_immediately():
    assert wait_mod.poll_until(lambda: True, timeout_s=1.0) is True


def test_poll_until_waits_for_late_condition():
    state = {"ready": False}

    def _flip():
        time.sleep(0.2)
        state["ready"] = True

    thread = threading.Thread(target=_flip)
    thread.start()
    try:
        assert wait_mod.poll_until(lambda: state["ready"],
                                   timeout_s=5.0) is True
    finally:
        thread.join()


def test_poll_until_timeout_returns_false():
    assert wait_mod.poll_until(lambda: False, timeout_s=0.2,
                               interval_s=0.05) is False


def test_poll_until_predicate_errors_count_as_not_ready():
    calls = {"count": 0}

    def _flaky():
        calls["count"] += 1
        if calls["count"] < 3:
            raise RuntimeError("not yet")
        return True

    assert wait_mod.poll_until(_flaky, timeout_s=5.0) is True


def test_wait_until_raises_with_message_on_timeout():
    try:
        wait_mod.wait_until(lambda: False, timeout_s=0.2,
                            interval_s=0.05,
                            message="broadway socket never appeared")
    except AssertionError as exc:
        assert "broadway socket never appeared" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("wait_until did not raise")


def test_pump_until_true_without_display():
    assert wait_mod.pump_until(lambda: True, timeout_s=2.0,
                               settle_s=0.0) is True


def test_pump_until_timeout_returns_false():
    assert wait_mod.pump_until(lambda: False, timeout_s=0.3,
                               settle_s=0.0) is False


def test_iterate_until_true_without_display():
    assert wait_mod.iterate_until(lambda: True, timeout_s=2.0) is True


def test_iterate_until_timeout_returns_false():
    assert wait_mod.iterate_until(lambda: False, timeout_s=0.3) is False
