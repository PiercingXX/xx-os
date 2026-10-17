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
    """30 thin bars on a baseline hairline; the best day's level is a
    faint hairline with its value; today is the last bar."""

    __gtype_name__ = "XXAudiobookDayBars"

    def __init__(self, series: list) -> None:
        super().__init__()
        self.series = series
        self.set_content_height(round(116 * max(style.scale, 1.0)))
        self.set_hexpand(True)
        self.add_css_class("xx-ab-chart")
        self.set_draw_func(self._draw)
        total = sum(v for _d, v in series)
        best = max((v for _d, v in series), default=0.0)
        self.update_property([Gtk.AccessibleProperty.LABEL], [
            f"Last 30 days: {stats_module.format_listened(total)} in all, "
            f"best day {stats_module.format_listened(best)}"])

    def _draw(self, widget, cr, width, height) -> None:
        color = widget.get_color()
        rgb = (color.red, color.green, color.blue)
        values = [v for _d, v in self.series]
        count = max(len(values), 1)
        top_pad = 18
        chart_h = height - top_pad - 2
        gap = 3
        bar_w = max((width - gap * (count - 1)) / count, 1.0)
        heights = bar_heights(values, chart_h)
        best = max(values) if values else 0.0
        # Baseline.
        cr.set_source_rgba(*rgb, 0.28)
        cr.rectangle(0, height - 1, width, 1)
        cr.fill()
        if best > 0:
            # The best day's level: a faint hairline and its value.
            cr.set_source_rgba(*rgb, 0.16)
            cr.rectangle(0, top_pad, width, 1)
            cr.fill()
            layout = widget.create_pango_layout(fmt.short_hours(best))
            cr.set_source_rgba(*rgb, 0.6)
            cr.move_to(0, 0)
            from gi.repository import PangoCairo

            PangoCairo.show_layout(cr, layout)
        for index, bar in enumerate(heights):
            x = index * (bar_w + gap)
            if bar <= 0:
                cr.set_source_rgba(*rgb, 0.22)
                cr.rectangle(x, height - 3, bar_w, 2)
            else:
                last = index == len(heights) - 1
                cr.set_source_rgba(*rgb, 1.0 if last else 0.78)
                cr.rectangle(x, height - 1 - bar, bar_w, bar)
            cr.fill()


class StatsPage:
    def __init__(self, ctx, nav, window_getter) -> None:
        self.ctx = ctx
        self.nav = nav
        self._window = window_getter
        self.state = stats_module.StatsState(ctx.api)
        self.today = date.today()
        self.body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self.body.set_margin_bottom(28)
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
        row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        row.set_margin_top(4)
        back = common.icon_button(
            "go-previous-symbolic", f"Year {view.year - 1}",
            lambda: self.select(view.year - 1))
        back.set_sensitive(view.can_go_back)
        forward = common.icon_button(
            "go-next-symbolic", f"Year {view.year + 1}",
            lambda: self.select(view.year + 1))
        forward.set_sensitive(view.can_go_forward)
        middle = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        middle.set_hexpand(True)
        middle.set_valign(Gtk.Align.CENTER)
        caption = common.caps("Year")
        caption.set_xalign(0.5)
        middle.append(caption)
        middle.append(common.label(str(view.year), "xx-ab-heading xx-ab-num",
                                   xalign=0.5, chars=6))
        row.append(back)
        row.append(middle)
        row.append(forward)
        return row

    def _figures(self, body: Gtk.Box, stats) -> None:
        hero = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
        hero.set_margin_top(20)
        hero.append(common.caps("Time listened"))
        listened = stats_module.format_listened(stats.total_listened_s)
        hero.append(common.label(listened, "xx-ab-hero xx-ab-num", lines=2,
                                 chars=14))
        sessions = (f"{stats.sessions} sessions  ·  "
                    f"{fmt.books_count(stats.items_played)} played")
        hero.append(common.label(sessions, "xx-ab-meta xx-ab-num", lines=2,
                                 chars=34))
        hero.update_property([Gtk.AccessibleProperty.LABEL],
                             [f"Time listened: {listened}. {sessions}"])
        body.append(hero)
        tiles = (("Books finished", str(stats.items_finished)),
                 ("Days listened", str(stats.days_listened)),
                 ("Current streak", days_label(stats.streak.current_days)),
                 ("Longest streak", days_label(stats.streak.longest_days)))
        grid = Gtk.Grid()
        grid.set_column_homogeneous(True)
        grid.set_column_spacing(16)
        grid.set_margin_top(16)
        per_row = 1 if style.large_text() else 2
        for index, (caption, value) in enumerate(tiles):
            cell = common.figure(value, caption)
            cell.add_css_class("xx-ab-cell-box")
            grid.attach(cell, index % per_row, index // per_row, 1, 1)
        body.append(grid)

    def _days(self, body: Gtk.Box, stats) -> None:
        series = stats_module.last_days(stats.by_day, self.today)
        body.append(common.section("Last 30 days"))
        body.append(DayBars(series))
        axis = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        axis.set_margin_top(6)
        first = date.fromisoformat(series[0][0])
        start = common.label(f"{first.strftime('%b')} {first.day}",
                             "xx-ab-meta", chars=8)
        start.set_hexpand(True)
        axis.append(start)
        axis.append(common.label("Today", "xx-ab-meta", xalign=1.0, chars=8))
        body.append(axis)

    def _months(self, body: Gtk.Box, stats) -> None:
        totals = stats_module.monthly_totals(stats.by_month)
        best = max(totals) if totals else 0.0
        body.append(common.section("By month"))
        last_month = (self.today.month
                      if stats.year == self.today.year else 12)
        for index, seconds in enumerate(totals[:last_month]):
            row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
            row.set_margin_top(5)
            row.set_margin_bottom(5)
            name = common.label(MONTHS[index].upper(), "xx-ab-label",
                                chars=4)
            name.set_size_request(36, -1)
            row.append(name)
            spoken = (f"{MONTHS[index]}: "
                      f"{stats_module.format_listened(seconds)}")
            line = common.bar(stats_module.top_fraction(seconds, best),
                              spoken)
            line.set_valign(Gtk.Align.CENTER)
            row.append(line)
            value = common.label(fmt.short_hours(seconds),
                                 "xx-ab-meta xx-ab-num", xalign=1.0, chars=5)
            value.set_size_request(44, -1)
            row.append(value)
            body.append(row)

    def _tops(self, body: Gtk.Box, stats) -> None:
        if stats.top_items:
            body.append(common.section("Top books"))
            best = stats.top_items[0].listened_s
            for index, top in enumerate(stats.top_items[:5]):
                body.append(self._top_row(
                    top.title, top.listened_s, best,
                    common.cover(self.ctx, top.item_id, top.title, 44,
                                 bool(top.thumb_url)),
                    lambda t=top: self.nav.open_book(t.item_id),
                    index == min(len(stats.top_items), 5) - 1))
        if stats.top_authors:
            body.append(common.section("Top authors"))
            best = stats.top_authors[0].listened_s
            for index, top in enumerate(stats.top_authors[:5]):
                body.append(self._top_row(
                    top.name, top.listened_s, best, None,
                    lambda t=top: self.nav.browse(
                        browse_module.AUTHOR, t.name, t.id or None),
                    index == min(len(stats.top_authors), 5) - 1))

    def _top_row(self, title: str, seconds: float, best: float, art,
                 on_open, last: bool) -> Gtk.Button:
        box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 14)
        if art is not None:
            art.set_valign(Gtk.Align.CENTER)
            box.append(art)
        text = Gtk.Box.new(Gtk.Orientation.VERTICAL, 6)
        text.set_hexpand(True)
        text.set_valign(Gtk.Align.CENTER)
        head = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
        name = common.label(title, "xx-body", chars=18)
        name.set_hexpand(True)
        head.append(name)
        listened = stats_module.format_listened(seconds)
        short = common.label(fmt.short_hours(seconds), "xx-ab-meta xx-ab-num",
                             xalign=1.0, chars=7)
        short.set_ellipsize(0)  # short; never misfire an ellipsis
        head.append(short)
        text.append(head)
        text.append(common.bar(stats_module.top_fraction(seconds, best),
                               f"{title}: {listened}"))
        box.append(text)
        return common.tap_row(box, f"{title}, {listened}", on_open, last)

    def _sessions(self, body: Gtk.Box) -> None:
        history = self.state.history()
        body.append(common.section(
            f"Recent sessions · {history.total}" if history.total
            else "Recent sessions"))
        if history.error:
            line = common.muted(history.error)
            line.add_css_class("xx-danger-text")
            body.append(line)
        if not history.sessions:
            body.append(common.muted(
                "Loading…" if history.loading and not history.error
                else "No listening sessions yet."))
            return
        for index, session in enumerate(history.sessions):
            body.append(self._session_row(
                session, index == len(history.sessions) - 1))
        if history.can_load_more or history.loading:
            more = widgets.outline_button(
                "Loading…" if history.loading else "Load more",
                self.load_more)
            more.set_sensitive(not history.loading)
            common.fit(more, 16)
            more.set_margin_top(12)
            body.append(more)

    def _session_row(self, session, last: bool) -> Gtk.Box:
        row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
        row.add_css_class("xx-ab-session")
        if last:
            row.add_css_class("last")
        text = Gtk.Box.new(Gtk.Orientation.VERTICAL, 3)
        text.set_hexpand(True)
        text.set_valign(Gtk.Align.CENTER)
        text.append(common.label(session.title or "Untitled", "xx-body",
                                 lines=2, chars=22))
        when = " ".join(p for p in (fmt.day_text(session.started_at),
                                    fmt.clock_text(session.started_at)) if p)
        meta = "  ·  ".join(p for p in (
            when, stats_module.format_listened(session.listened_s)) if p)
        text.append(common.label(meta, "xx-ab-meta xx-ab-num", chars=30))
        span = (f"{fmt.format_ms(int(session.start_position_s * 1000))} → "
                f"{fmt.format_ms(int(session.position_s * 1000))}")
        if session.device:
            span += f"  ·  {session.device}"
        text.append(common.label(span, "xx-tiny xx-ab-num", chars=34))
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
