"""Stats: year bounds, zero-filled series, sessions paging and delete."""

from datetime import date

from xx_audiobook import api as api_module
from xx_audiobook import stats as stats_module
from xx_audiobook.api import AudiobookApi
from xx_audiobook.stats import StatsState


def _state(server):
    return StatsState(AudiobookApi(server.api_base, "tok"),
                      now_year=2026)


def test_clamp_year_bounds():
    assert stats_module.clamp_year(1999, 2026) == (2000, False, True)
    assert stats_module.clamp_year(2026, 2026) == (2026, True, False)
    assert stats_module.clamp_year(2030, 2026) == (2026, True, False)
    assert stats_module.clamp_year(2020, 2026) == (2020, True, True)
    assert stats_module.clamp_year(2020, 2026,
                                   first_year=2021) == (2021, False, True)


def test_last_days_zero_fills_ending_today():
    today = date(2026, 10, 17)
    by_day = [api_module.DayStat(date="2026-10-17", listened_s=60.0),
              api_module.DayStat(date="2026-10-15", listened_s=30.0)]
    series = stats_module.last_days(by_day, today)
    assert len(series) == 30
    assert series[0] == ("2026-09-18", 0.0)
    assert series[-1] == ("2026-10-17", 60.0)
    assert dict(series)["2026-10-16"] == 0.0
    assert dict(series)["2026-10-15"] == 30.0


def test_monthly_totals_zero_fill_twelve():
    by_month = [api_module.MonthStat(month=3, listened_s=90.0,
                                     sessions=1)]
    totals = stats_module.monthly_totals(by_month)
    assert len(totals) == 12
    assert totals[2] == 90.0
    assert totals[0] == 0.0
    assert sum(totals) == 90.0


def test_format_listened():
    assert stats_module.format_listened(0.0) == "0 min"
    assert stats_module.format_listened(40 * 60.0) == "40 min"
    assert stats_module.format_listened(12 * 3600 + 5 * 60) == "12 h 5 min"
    assert stats_module.format_listened(2 * 3600.0) == "2 h"
    assert stats_module.format_listened(-5.0) == "0 min"


def test_top_fraction():
    assert stats_module.top_fraction(50.0, 100.0) == 0.5
    assert stats_module.top_fraction(0.0, 0.0) == 0.0
    assert stats_module.top_fraction(200.0, 100.0) == 1.0


def test_select_loads_year_and_first_year(fake_server):
    state = _state(fake_server)
    stats, error = state.select(2026)
    assert error is None
    assert stats.total_listened_s == 7200.0
    assert stats.streak.current_days == 1
    assert stats.streak.longest_days == 2
    view = state.view()
    assert (view.year, view.first_year, view.error) == (2026, 2026, None)
    assert view.can_go_back is False
    assert view.can_go_forward is False


def test_year_navigation_bounds(fake_server):
    state = _state(fake_server)
    state.select(2026)
    assert state.view().first_year == 2026
    state.previous_year()
    assert state.view().year == 2026
    state.next_year()
    assert state.view().year == 2026
    stats, _error = state.select(1999)
    assert state.view().year == 2026
    assert stats is not None
    _stats, error = state.retry()
    assert error is None
    fresh = _state(fake_server)
    fresh.previous_year()
    assert fresh.view().year == 2025


def test_history_pages_and_delete(fake_server):
    state = _state(fake_server)
    page, error = state.load_first_page()
    assert error is None
    assert page.total == 1
    history = state.history()
    assert [s.session_id for s in history.sessions] == ["ps-1"]
    assert history.can_load_more is False
    assert state.load_more() == (None, None)

    gone, error = state.delete_session("ps-1")
    assert (gone, error) == ("ps-1", None)
    history = state.history()
    assert history.sessions == []
    assert history.total == 0


def test_failed_delete_keeps_row_and_reason(fake_server):
    state = _state(fake_server)
    state.load_first_page()
    fake_server.queue("DELETE", "/audiobook/api/me/sessions/ps-1",
                      500, '{"detail": "try again later"}')
    gone, error = state.delete_session("ps-1")
    assert gone is None
    assert error is not None
    history = state.history()
    assert [s.session_id for s in history.sessions] == ["ps-1"]
    assert history.error == "try again later"
