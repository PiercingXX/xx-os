"""Listening stats (M5, ui/stats/StatsScreen.kt), pushed from Setup.

A year switcher (2000 or the first year with listening, through this
year), time listened, books finished, days, the current and longest
streak; the last 30 days as bars (this year only, zero-filled, ending
today); monthly hairline bars; top books and authors; then recent
sessions, twenty a page, each deletable (asks first; a failed delete
keeps the row and says why).

Charts follow the brand: monochrome theme foreground, 1-2 px hairlines,
flat fills, no gradients, no color coding, every figure also in text.
"""

from __future__ import annotations

from datetime import date

from gi.repository import Gtk

from xx_audiobook import browse as browse_module
from xx_audiobook import fmt
from xx_audiobook import stats as stats_module
from xx_audiobook.ui import common, style
from xx_ui import widgets

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
          "Oct", "Nov", "Dec")


def bar_heights(values: list, height: float, floor: float = 2.0) -> list:
    """Bar heights for the 30-day chart: proportional to the best day;
    days with listening never shrink below ``floor`` px; empty days 0."""
    best = max(values) if values else 0.0
    if best <= 0:
        return [0.0 for _ in values]
    return [0.0 if v <= 0 else max(floor, height * v / best) for v in values]


def days_label(count: int) -> str:
    return "1 day" if count == 1 else f"{count} days"


class DayBars(Gtk.DrawingArea):
    """DayBars: thirty thin columns on a hairline baseline, foreground
    fill, today on the right."""

    __gtype_name__ = "XXAudiobookDayBars"

    def __init__(self, series: list) -> None:
        super().__init__()
        self.series = series
        self.set_content_height(round(64 * max(style.scale, 1.0)))
        self.set_hexpand(True)
        self.add_css_class("xx-ab-chart")
        self.set_draw_func(self._draw)
        total = sum(v for _d, v in series)
        listened = sum(1 for _d, v in series if v > 0)
        self.update_property([Gtk.AccessibleProperty.LABEL], [
            f"Last 30 days: listened on {listened}, "
            f"{stats_module.format_listened(total)} in all"])

    def _draw(self, widget, cr, width, height) -> None:
        color = widget.get_color()
        rgb = (color.red, color.green, color.blue)
        values = [v for _d, v in self.series]
        count = max(len(values), 1)
        gap = 2
        bar_w = max((width - gap * (count - 1)) / count, 1.0)
        heights = bar_heights(values, height - 1)
        cr.set_source_rgba(*rgb, 1.0)
        for index, bar in enumerate(heights):
            if bar > 0:
                cr.rectangle(index * (bar_w + gap), height - 1 - bar,
                             bar_w, bar)
        cr.fill()
        cr.set_source_rgba(*rgb, 0.1)
        cr.rectangle(0, height - 1, width, 1)
        cr.fill()


class MonthBars(Gtk.DrawingArea):
    """MonthBars: twelve columns on a hairline, scaled to the busiest
    month (the initials sit in a row of labels below)."""

    __gtype_name__ = "XXAudiobookMonthBars"

    def __init__(self, totals: list) -> None:
        super().__init__()
        self.totals = totals
        self.set_content_height(round(96 * max(style.scale, 1.0)))
        self.set_hexpand(True)
        self.add_css_class("xx-ab-chart")
        self.set_draw_func(self._draw)
        self.update_property([Gtk.AccessibleProperty.LABEL], [
            "By month: " + ", ".join(
                f"{MONTHS[i]} {stats_module.format_listened(s)}"
                for i, s in enumerate(totals) if s > 0)])

    def _draw(self, widget, cr, width, height) -> None:
        color = widget.get_color()
        rgb = (color.red, color.green, color.blue)
        best = max(self.totals) if self.totals else 0.0
        gap = 4
        col = (width - gap * 11) / 12
        cr.set_source_rgba(*rgb, 1.0)
        for index, seconds in enumerate(self.totals):
            if best > 0 and seconds > 0:
                bar = max((height - 1) * seconds / best, 2.0)
                cr.rectangle(index * (col + gap), height - 1 - bar, col, bar)
        cr.fill()
        cr.set_source_rgba(*rgb, 0.1)
        for index in range(12):
            cr.rectangle(index * (col + gap), height - 1, col, 1)
        cr.fill()


class StatsPage:
    def __init__(self, ctx, nav, window_getter) -> None:
        self.ctx = ctx
        self.nav = nav
        self._window = window_getter
        self.state = stats_module.StatsState(ctx.api)
        self.today = date.today()
        self.body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self.body.set_margin_bottom(24)
        header = common.header(nav.back, "Listening stats")
        self.widget = widgets.page(header, self.body)
        self._render()
        self.select(self.state.view().year)
        self._history(self.state.load_first_page)

    # -- data ---------------------------------------------------------------

    def select(self, year: int) -> None:
        self.ctx.run(lambda: self.state.select(year),
                     lambda _r, _e: self._render())
        self._render()

    def _history(self, call) -> None:
        self.ctx.run(call, lambda _r, _e: self._render())
        self._render()

    def load_more(self) -> None:
        self._history(self.state.load_more)

    # -- render -------------------------------------------------------------

    def _render(self) -> None:
        body = self.body
        common.clear(body)
        view = self.state.view()
        body.append(self._switcher(view))
        stats = view.stats
        if stats is None:
            body.append(common.muted(view.error or "Loading…"))
        else:
            if view.error:
                body.append(common.muted(view.error))
            self._figures(body, stats)
            if view.year == self.today.year:
                self._days(body, stats)
            self._months(body, stats)
            self._tops(body, stats)
        self._sessions(body)

    def _switcher(self, view) -> Gtk.Box:
        """YearSwitcher: '‹ 2025' · 2026 · '2027 ›'."""
        row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        row.set_margin_top(4)
        back = widgets.text_button(f"‹ {view.year - 1}",
                                   lambda: self.select(view.year - 1))
        common.fit(back, 6)
        common.name(back, "Previous year")
        back.set_sensitive(view.can_go_back)
        forward = widgets.text_button(f"{view.year + 1} ›",
                                      lambda: self.select(view.year + 1))
        common.fit(forward, 6)
        common.name(forward, "Next year")
        forward.set_sensitive(view.can_go_forward)
        middle = common.label(str(view.year), "xx-ab-year xx-ab-num",
                              xalign=0.5, chars=6)
        middle.set_hexpand(True)
        middle.update_property([Gtk.AccessibleProperty.LABEL],
                               [f"Year {view.year}"])
        row.append(back)
        row.append(middle)
        row.append(forward)
        return row

    def _figures(self, body: Gtk.Box, stats) -> None:
        """Tiles: Listened / Finished / Days, then Streak / Longest."""
        rows = [(("Listened",
                  stats_module.format_listened(stats.total_listened_s)),
                 ("Finished", str(stats.items_finished)),
                 ("Days", str(stats.days_listened)))]
        if stats.streak.longest_days > 0:
            rows.append((("Streak", days_label(stats.streak.current_days)),
                         ("Longest", days_label(stats.streak.longest_days))))
        for index, tiles in enumerate(rows):
            grid = Gtk.Box.new(Gtk.Orientation.VERTICAL if style.large_text()
                               else Gtk.Orientation.HORIZONTAL, 8)
            # The first row sizes by content: "459 h 16 min" keeps one
            # line beside two short counts instead of wrapping its unit.
            grid.set_homogeneous(index > 0 or style.large_text())
            grid.set_margin_top(8)
            for caption, value in tiles:
                tile = common.figure(value, caption)
                tile.set_hexpand(True)
                grid.append(tile)
            body.append(grid)

    def _days(self, body: Gtk.Box, stats) -> None:
        series = stats_module.last_days(stats.by_day, self.today)
        body.append(common.section("Last 30 days"))
        body.append(DayBars(series))
        axis = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        first = date.fromisoformat(series[0][0])
        start = common.label(f"{first.strftime('%b')} {first.day}",
                             "xx-ab-l10 xx-ab-muted", chars=8)
        start.set_hexpand(True)
        axis.append(start)
        today = common.label("Today", "xx-ab-l10 xx-ab-muted",
                             xalign=1.0, chars=8)
        today.set_ellipsize(0)  # short; never misfire an ellipsis
        axis.append(today)
        body.append(axis)

    def _months(self, body: Gtk.Box, stats) -> None:
        totals = stats_module.monthly_totals(stats.by_month)
        body.append(common.section("By month"))
        body.append(MonthBars(totals))
        names = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 4)
        names.set_homogeneous(True)
        for month in MONTHS:
            names.append(common.label(month[0], "xx-ab-l10 xx-ab-muted",
                                      xalign=0.5, chars=1))
        body.append(names)

    def _tops(self, body: Gtk.Box, stats) -> None:
        if stats.top_items:
            body.append(common.section("Top books"))
            best = stats.top_items[0].listened_s
            for index, top in enumerate(stats.top_items[:5]):
                body.append(self._top_row(
                    f"{index + 1}.", top.title or "Untitled", top.listened_s,
                    best, lambda t=top: self.nav.open_book(t.item_id)))
        if stats.top_authors:
            body.append(common.section("Top authors"))
            best = stats.top_authors[0].listened_s
            for top in stats.top_authors[:5]:
                body.append(self._top_row(
                    "", top.name or "Unknown", top.listened_s, best,
                    lambda t=top: self.nav.browse(
                        browse_module.AUTHOR, t.name, t.id or None)))

    def _top_row(self, rank: str, title: str, seconds: float, best: float,
                 on_open) -> Gtk.Button:
        text = Gtk.Box.new(Gtk.Orientation.VERTICAL, 4)
        text.set_margin_top(8)
        text.set_margin_bottom(8)
        head = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
        if rank:
            head.append(common.label(rank, "xx-ab-l12 xx-ab-muted", chars=3))
        name = common.label(title, "xx-ab-t14", chars=20)
        name.set_hexpand(True)
        head.append(name)
        listened = stats_module.format_listened(seconds)
        short = common.label(listened, "xx-ab-l12 xx-ab-num", xalign=1.0,
                             chars=12)
        short.set_ellipsize(0)  # short; never misfire an ellipsis
        head.append(short)
        text.append(head)
        text.append(common.bar(stats_module.top_fraction(seconds, best),
                               f"{title}: {listened}"))
        return common.flat(text, f"{title}, {listened}", on_open)

    def _sessions(self, body: Gtk.Box) -> None:
        history = self.state.history()
        if not (history.sessions or history.error or history.loading):
            return  # no sessions: no heading, no line
        body.append(common.section("Recent sessions"))
        if history.error:
            body.append(common.muted(history.error))
        if not history.sessions:
            if history.loading and not history.error:
                body.append(common.muted("Loading…"))
            return
        for index, session in enumerate(history.sessions):
            body.append(self._session_row(
                session, index == len(history.sessions) - 1))
        if history.can_load_more or history.loading:
            more = widgets.text_button(
                "Loading…" if history.loading else
                f"More sessions ({len(history.sessions)} of "
                f"{history.total})", self.load_more)
            more.set_sensitive(not history.loading)
            common.fit(more, 26)
            more.set_halign(Gtk.Align.START)
            body.append(more)

    def _session_row(self, session, last: bool) -> Gtk.Box:
        row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
        row.add_css_class("xx-ab-session")
        text = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        text.set_hexpand(True)
        text.set_valign(Gtk.Align.CENTER)
        text.append(common.label(session.title or "Untitled", "xx-ab-t14",
                                 chars=24))
        when = " ".join(p for p in (fmt.day_text(session.started_at),
                                    fmt.clock_text(session.started_at)) if p)
        meta = " · ".join(p for p in (
            when, stats_module.format_listened(session.listened_s),
            (session.device or "").strip()) if p)
        text.append(common.label(meta, "xx-ab-l12 xx-ab-muted xx-ab-num",
                                 lines=2, chars=34))
        row.append(text)
        delete = common.icon_button(
            "user-trash-symbolic",
            f"Delete the session of {session.title or 'this book'}, {when}",
            lambda: self.ask_delete(session))
        delete.set_valign(Gtk.Align.CENTER)
        row.append(delete)
        return row

    def ask_delete(self, session):
        def _answer(response: str) -> None:
            if response == "delete":
                self.ctx.run(
                    lambda: self.state.delete_session(session.session_id),
                    lambda _r, _e: self._render())

        listened = stats_module.format_listened(session.listened_s)
        return common.confirm(
            self._window(), "Delete this session?",
            f"{listened} of {session.title or 'this book'} leaves your "
            "stats and history on every device.",
            [("keep", "Keep", "outline"), ("delete", "Delete", "danger")],
            _answer)
