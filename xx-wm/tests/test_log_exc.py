"""shell_log.log_exc: the one logger for broad handlers that used to pass."""
import logging

import pytest

import shell_log


@pytest.fixture(autouse=True)
def _fresh():
    shell_log._logged_exc.clear()
    yield
    shell_log._logged_exc.clear()


def _fail(where, exc):
    try:
        raise exc
    except Exception:
        shell_log.log_exc(where)


def test_first_failure_is_a_warning_with_traceback(caplog):
    caplog.set_level(logging.DEBUG, logger='piercing.exc')
    _fail('ringer start', RuntimeError('no feedbackd'))
    (record,) = caplog.records
    assert record.levelno == logging.WARNING
    assert record.getMessage() == (
        "ringer start failed: RuntimeError('no feedbackd')")
    assert record.exc_info[0] is RuntimeError


def test_repeats_drop_to_debug(caplog):
    caplog.set_level(logging.DEBUG, logger='piercing.exc')
    for _ in range(3):
        _fail('banner', ValueError('x'))
    assert [r.levelno for r in caplog.records] == [
        logging.WARNING, logging.DEBUG, logging.DEBUG]


def test_new_place_or_type_warns_again(caplog):
    caplog.set_level(logging.DEBUG, logger='piercing.exc')
    _fail('banner', ValueError('x'))
    _fail('banner', OSError('y'))
    _fail('thaw', ValueError('x'))
    assert [r.levelno for r in caplog.records] == [logging.WARNING] * 3


def test_at_info_level_repeats_are_silent(caplog):
    caplog.set_level(logging.INFO, logger='piercing.exc')
    for _ in range(5):
        _fail('tile', KeyError('k'))
    assert len(caplog.records) == 1
