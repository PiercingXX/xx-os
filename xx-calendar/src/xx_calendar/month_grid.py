"""Cairo month grid: theme-token drawing that redraws on theme change."""
from __future__ import annotations

import datetime as _datetime

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

CELL_H = 48.0
DISC_R = 14.0
DOT_R = 2.2
#: Base numeral size (px) at text scale 1; the theme's scale multiplies it.
NUMERAL_PX = 13.0
#: Width of the leading ISO week-number column when it is on.
WEEK_COL_W = 28.0


def _hex_to_rgb(value: str) -> tuple[float, float, float]:
    text = (value or "#ffffff").strip().lstrip("#")
    if len(text) != 6:
        return (1.0, 1.0, 1.0)
    try:
        return (int(text[0:2], 16) / 255.0, int(text[2:4], 16) / 255.0,
                int(text[4:6], 16) / 255.0)
    except ValueError:
        return (1.0, 1.0, 1.0)


def iso_week(week: list) -> int:
    """ISO week number of a grid row: its Monday's week, so a row that
    starts on Sunday is not labelled with the week before."""
    for day, _in_month in week:
        if day.weekday() == 0:
            return day.isocalendar()[1]
    return week[0][0].isocalendar()[1]


class MonthGrid(Gtk.DrawingArea):
    """7-column month grid drawn with cairo from theme tokens."""

    def __init__(self, on_select=None) -> None:
        super().__init__()
        self._weeks: list = []
        self._counts: dict = {}
        self._today: _datetime.date | None = None
        self._selected: _datetime.date | None = None
        self._on_select = on_select
        self._theme = None
        self._week_numbers = False
        self.set_hexpand(True)
        self.set_vexpand(False)
        self.set_draw_func(self._draw)
        click = Gtk.GestureClick()
        click.connect("pressed", self._on_pressed)
        self.add_controller(click)
        try:
            import xx_ui

            self._theme = xx_ui.theme.load()
            self._unsub = xx_ui.on_change(self._on_theme)
        except Exception:
            self._theme = None
            self._unsub = None

    def _on_theme(self, theme) -> None:
        self._theme = theme
        self._resize()
        self.queue_draw()

    def _scale(self) -> float:
        try:
            return max(0.5, min(2.0, float(self._theme.scale)))
        except (AttributeError, TypeError, ValueError):
            return 1.0

    def _resize(self) -> None:
        rows = max(1, len(self._weeks))
        self.set_content_height(int(rows * CELL_H * max(1.0, self._scale())))

    def set_week_numbers(self, on: bool) -> None:
        """Show a muted ISO week-number column before the seven days."""
        self._week_numbers = bool(on)
        self.queue_draw()

    @property
    def week_numbers(self) -> bool:
        return self._week_numbers

    def _lead(self) -> float:
        return WEEK_COL_W * self._scale() if self._week_numbers else 0.0

    def set_data(self, weeks: list, counts: dict,
                 today: _datetime.date | None,
                 selected: _datetime.date | None) -> None:
        self._weeks = list(weeks)
        self._counts = dict(counts)
        self._today = today
        self._selected = selected
        self._resize()
        self.queue_draw()

    @property
    def weeks(self) -> list:
        return self._weeks

    def _colors(self) -> tuple:
        try:
            import xx_ui

            theme = self._theme or xx_ui.theme.load()
        except Exception:
            theme = None
        if theme is None:
            return ((1, 1, 1), (0.6, 0.6, 0.6), (0, 0, 0))
        return (_hex_to_rgb(theme.foreground), _hex_to_rgb(theme.muted),
                _hex_to_rgb(theme.background))

    def _on_pressed(self, _gesture, _n, x: float, y: float) -> None:
        if not self._weeks or self._on_select is None:
            return
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        rows = len(self._weeks)
        if width <= 0 or height <= 0 or rows <= 0:
            return
        lead = self._lead()
        if x < lead:
            return
        col = min(6, max(0, int((x - lead) / ((width - lead) / 7.0))))
        row = min(rows - 1, max(0, int(y / (height / rows))))
        try:
            day, _in = self._weeks[row][col]
        except IndexError:
            return
        self._on_select(day)

    def _draw(self, _area, ctx, width: int, height: int) -> None:
        if not self._weeks:
            return
        fg, muted, bg = self._colors()
        rows = len(self._weeks)
        scale = self._scale()
        lead = self._lead()
        cell_w = (width - lead) / 7.0
        cell_h = height / rows if rows else CELL_H
        # The ring never spills into the next cell (narrow screens, big
        # text, the week-number column).
        disc_r = min(DISC_R * max(1.0, scale), cell_w / 2.0 - 1.0)
        for row, week in enumerate(self._weeks):
            if lead and week:
                ctx.set_source_rgb(*muted)
                self._draw_numeral(ctx, str(iso_week(week)),
                                   lead / 2.0,
                                   row * cell_h + cell_h / 2.0 - 4.0,
                                   0.8)
            for col, (day, in_month) in enumerate(week):
                cx = lead + col * cell_w + cell_w / 2.0
                cy = row * cell_h + cell_h / 2.0 - 4.0
                is_today = self._today is not None and day == self._today
                is_sel = self._selected is not None and day == self._selected
                weekend = day.weekday() >= 5
                dim = (not in_month) or weekend
                if is_today:
                    ctx.set_source_rgb(*fg)
                    ctx.new_sub_path()
                    ctx.arc(cx, cy, disc_r, 0, 6.28318)
                    ctx.fill()
                    ctx.set_source_rgb(*bg)
                    self._draw_numeral(ctx, str(day.day), cx, cy)
                    ctx.set_source_rgb(*fg)
                else:
                    if is_sel:
                        ctx.set_source_rgb(*fg)
                        ctx.set_line_width(1.5)
                        # A fresh sub-path: without it the arc joins the
                        # last numeral's pen point with a stray line.
                        ctx.new_sub_path()
                        ctx.arc(cx, cy, disc_r, 0, 6.28318)
                        ctx.stroke()
                    ctx.set_source_rgb(*(muted if dim else fg))
                    self._draw_numeral(ctx, str(day.day), cx, cy)
                count = int(self._counts.get(day, 0))
                if count > 0:
                    dots = min(count, 3)
                    ctx.set_source_rgb(*(muted if (dim and not is_today) else fg))
                    total_w = (dots - 1) * 8.0
                    for i in range(dots):
                        dx = cx - total_w / 2.0 + i * 8.0
                        dy = cy + disc_r + 2.0
                        ctx.new_sub_path()
                        ctx.arc(dx, dy, DOT_R, 0, 6.28318)
                        ctx.fill()

    def _draw_numeral(self, ctx, text: str, cx: float, cy: float,
                      size: float = 1.0) -> None:
        layout = self.create_pango_layout(text)
        try:
            from gi.repository import Pango

            # The widget's own (theme) family at the scaled numeral size:
            # a bare size string would drop the family to system sans.
            base = self.get_pango_context().get_font_description()
            desc = base.copy() if base is not None \
                else Pango.FontDescription()
            desc.set_absolute_size(
                NUMERAL_PX * self._scale() * size * Pango.SCALE)
            layout.set_font_description(desc)
        except Exception:
            pass
        try:
            _ink, logical = layout.get_pixel_extents()
            tw, th = logical.width, logical.height
        except Exception:
            tw, th = (12, 14)
        ctx.move_to(cx - tw / 2.0, cy - th / 2.0)
        try:
            from gi.repository import PangoCairo

            PangoCairo.show_layout(ctx, layout)
        except Exception:
            ctx.show_text(text)
        ctx.new_path()
