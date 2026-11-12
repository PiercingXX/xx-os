"""Night light: the custom schedule follows DST in the system zone."""
import datetime
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import night_light  # noqa: E402

_BERLIN = '/usr/share/zoneinfo/Europe/Berlin'


@pytest.fixture
def berlin(monkeypatch):
    if not os.path.exists(_BERLIN):
        pytest.skip('tzdata not installed')
    monkeypatch.delenv('TZ', raising=False)
    return night_light._system_tz(_BERLIN)


def test_system_zone_keeps_its_dst_rules(berlin):
    winter = datetime.datetime(2026, 1, 15, 12, tzinfo=berlin)
    summer = datetime.datetime(2026, 7, 15, 12, tzinfo=berlin)
    assert winter.utcoffset() != summer.utcoffset()


def test_custom_edge_after_the_spring_change_is_at_local_time(berlin):
    # Saturday noon before the 2026-03-29 change; 22:00 on Sunday is CEST.
    now = datetime.datetime(2026, 3, 28, 12, tzinfo=berlin).timestamp()
    edges, constant = night_light.collect_edges(
        now, 'custom', None, None, 22 * 60, 6 * 60, berlin)
    assert constant is None
    sunday_on = [when for when, on in edges if on and when > now + 86400]
    local = datetime.datetime.fromtimestamp(sunday_on[0], tz=berlin)
    assert (local.hour, local.minute) == (22, 0)


def test_tz_env_wins(monkeypatch):
    if not os.path.exists(_BERLIN):
        pytest.skip('tzdata not installed')
    monkeypatch.setenv('TZ', 'Europe/Berlin')
    tz = night_light._system_tz('/nonexistent')
    assert str(tz) == 'Europe/Berlin'
