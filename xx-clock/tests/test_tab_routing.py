"""Tab routing (the portable half of ui/ClockSystemIntentsTest.kt).

Android manifest assertions are OUT (no manifest). What stays: a launch
request names exactly one of the three tabs, anything else keeps the
remembered tab.
"""

from xx_clock.main import VALID_TABS, tab_from_args


def test_tab_flags_select_their_tab():
    assert tab_from_args(["--tab=alarms"]) == "alarms"
    assert tab_from_args(["--tab=timers"]) == "timers"
    assert tab_from_args(["--tab=clock"]) == "clock"


def test_unknown_or_missing_tab_keeps_remembered_tab():
    assert tab_from_args([]) is None
    assert tab_from_args(["--tab=widget"]) is None
    assert tab_from_args(["--daemon"]) is None


def test_valid_tabs_match_window_tabs():
    assert set(VALID_TABS) >= {"clock", "alarms", "timers"}
