"""Year-bounded listening stats and session history (M5).

Mirrors ui/stats/StatsScreen.kt: the year switcher (MIN_YEAR 2000 through
the current year), the last-30-days zero-filled series ending today,
monthly totals, top books/authors, and the paged listening history with
load-more and delete (a failed delete keeps the row and reports the
reason). Synchronous; screens use StatsState.submit_* from the GTK
thread.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date

from xx_audiobook import api as api_module

MIN_YEAR = 2000
HISTORY_PAGE_LIMIT = 20
SERIES_DAYS = 30


def current_year(today: date | None = None) -> int:
    return (today or date.today()).year


def clamp_year(year: int, now_year: int,
               first_year: int | None = None) -> tuple[int, bool, bool]:
    """Clamp to [MIN_YEAR..now]; returns (year, can_back, can_forward)."""
    floor = max(MIN_YEAR, first_year) if first_year else MIN_YEAR
    picked = min(now_year, max(floor, year))
    return (picked, picked > floor, picked < now_year)


def last_days(by_day: list, today: date,
              days: int = SERIES_DAYS) -> list:
    """The [days] days ending [today], oldest first, zero-filled."""
    seconds = {row.date: row.listened_s for row in by_day}
    out = []
    for back in range(days - 1, -1, -1):
        day = today.fromordinal(today.toordinal() - back)
        out.append((day.isoformat(), seconds.get(day.isoformat(), 0.0)))
    return out


def monthly_totals(by_month: list) -> list:
    """Twelve listened-seconds figures, January first, zero-filled."""
    seconds = {row.month: row.listened_s for row in by_month}
    return [seconds.get(month, 0.0) for month in range(1, 13)]


def format_listened(seconds: float) -> str:
    """'12 h 5 min' / '12 h' / '40 min' / '0 min' (never negative)."""
    total = max(0, int(seconds))
    hours, minutes = total // 3600, (total % 3600) // 60
    if hours > 0 and minutes > 0:
        return f"{hours} h {minutes} min"
    if hours > 0:
        return f"{hours} h"
    return f"{minutes} min"


def top_fraction(listened_s: float, best_s: float) -> float:
    if best_s <= 0:
        return 0.0
    return min(1.0, max(0.0, listened_s / best_s))


@dataclass
class StatsView:
    year: int = 0
    stats: object = None
    loading: bool = True
    error: str | None = None
    first_year: int | None = None
    current_year: int = 0

    @property
    def can_go_back(self) -> bool:
        floor = self.first_year if self.first_year else MIN_YEAR
        return self.year > max(MIN_YEAR, floor)

    @property
    def can_go_forward(self) -> bool:
        return self.year < self.current_year


@dataclass
class HistoryView:
    sessions: list = field(default_factory=list)
    total: int = 0
    page: int = 0
    loading: bool = True
    error: str | None = None

    @property
    def can_load_more(self) -> bool:
        return not self.loading and len(self.sessions) < self.total


def _message_of(error) -> str:
    return str(getattr(error, "message", error) or error)


class StatsState:
    """Small state API for the Stats screen (mirrors StatsViewModel)."""

    def __init__(self, api, now_year: int | None = None) -> None:
        self._api = api
        year = now_year if now_year is not None else current_year()
        self._lock = threading.Lock()
        self._view = StatsView(year=year, current_year=year)
        self._history = HistoryView()
        self._history_loading = False

    def view(self) -> StatsView:
        with self._lock:
            view = self._view
            return StatsView(
                year=view.year, stats=view.stats, loading=view.loading,
                error=view.error, first_year=view.first_year,
                current_year=view.current_year)

    def history(self) -> HistoryView:
        with self._lock:
            history = self._history
            return HistoryView(
                sessions=list(history.sessions), total=history.total,
                page=history.page, loading=history.loading,
                error=history.error)

    def select(self, year: int) -> tuple:
        with self._lock:
            picked, _back, _forward = clamp_year(
                year, self._view.current_year, self._view.first_year)
            kept = (self._view.stats if self._view.stats is not None
                    and self._view.stats.year == picked else None)
            self._view = StatsView(
                year=picked, stats=kept, loading=True, error=None,
                first_year=self._view.first_year,
                current_year=self._view.current_year)
        try:
            stats = self._api.stats(picked)
        except api_module.ApiError as exc:
            with self._lock:
                self._view = StatsView(
                    year=picked, stats=kept, loading=False,
                    error=_message_of(exc),
                    first_year=self._view.first_year,
                    current_year=self._view.current_year)
            return (None, exc)
        with self._lock:
            first = (stats.first_year if stats.first_year is not None
                     else self._view.first_year)
            self._view = StatsView(
                year=picked, stats=stats, loading=False, error=None,
                first_year=first, current_year=self._view.current_year)
        return (stats, None)

    def previous_year(self) -> tuple:
        with self._lock:
            year = self._view.year
        picked, _back, _forward = clamp_year(
            year - 1, self.view().current_year, self.view().first_year)
        if picked == year:
            return (self.view().stats, None)
        return self.select(year - 1)

    def next_year(self) -> tuple:
        with self._lock:
            year = self._view.year
        if year >= self.view().current_year:
            return (self.view().stats, None)
        return self.select(year + 1)

    def retry(self) -> tuple:
        with self._lock:
            year = self._view.year
        return self.select(year)

    def load_history(self, page: int) -> tuple:
        with self._lock:
            if self._history_loading:
                return (None, None)
            self._history_loading = True
            history = self._history
            self._history = HistoryView(
                sessions=list(history.sessions), total=history.total,
                page=history.page, loading=True, error=None)
        try:
            result = self._api.sessions(page, HISTORY_PAGE_LIMIT)
        except api_module.ApiError as exc:
            with self._lock:
                history = self._history
                self._history = HistoryView(
                    sessions=list(history.sessions), total=history.total,
                    page=history.page, loading=False,
                    error=_message_of(exc))
                self._history_loading = False
            return (None, exc)
        with self._lock:
            if page <= 1:
                merged = list(result.results)
            else:
                seen = {row.session_id
                        for row in self._history.sessions}
                merged = list(self._history.sessions) + [
                    row for row in result.results
                    if row.session_id not in seen]
            self._history = HistoryView(
                sessions=merged, total=result.total, page=page,
                loading=False, error=None)
            self._history_loading = False
        return (result, None)

    def load_first_page(self) -> tuple:
        return self.load_history(1)

    def load_more(self) -> tuple:
        history = self.history()
        if not history.can_load_more:
            return (None, None)
        return self.load_history(history.page + 1)

    def delete_session(self, session_id: str) -> tuple:
        try:
            self._api.delete_session(session_id)
        except api_module.ApiError as exc:
            with self._lock:
                history = self._history
                self._history = HistoryView(
                    sessions=list(history.sessions), total=history.total,
                    page=history.page, loading=False,
                    error=_message_of(exc))
            return (None, exc)
        with self._lock:
            history = self._history
            kept = [row for row in history.sessions
                    if row.session_id != session_id]
            self._history = HistoryView(
                sessions=kept, total=max(0, history.total - 1),
                page=history.page, loading=False, error=None)
            year = self._view.year
        self.select(year)
        return (session_id, None)

    def submit_select(self, year: int, on_done=None):
        return api_module.submit(lambda: self.select(year), on_done)

    def submit_load_more(self, on_done=None):
        return api_module.submit(self.load_more, on_done)

    def submit_delete(self, session_id: str, on_done=None):
        return api_module.submit(
            lambda: self.delete_session(session_id), on_done)
