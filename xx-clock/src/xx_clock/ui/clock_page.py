"""Clock tab: hero time, date, digital/analog toggle, next-alarm card.

Mirrors ClockFragment: no page title (the time is the page), the hero
in the display face near the top, the full weekday date in muted text,
an outline style toggle, and the next armed alarm in a card that is
hidden when nothing is armed.
"""

from __future__ import annotations

import time as _time

APP_CSS_EXTRA = """
.xx-clock-hero { font-size: 72px; font-weight: 400; font-feature-settings: "tnum"; }
.xx-clock-analog { min-width: 220px; min-height: 220px; }
.xx-clock-date { font-size: 18px; }
"""


class ClockPage:
    def __init__(self, Gtk, widgets, settings, on_style_changed) -> None:
        self._Gtk = Gtk
        self._widgets = widgets
        self._settings = settings
        self._on_style_changed = on_style_changed
        self._hero = Gtk.Label(label="--:--")
        self._hero.add_css_class("xx-display-face")
        self._hero.add_css_class("xx-clock-hero")
        self._hero.set_xalign(0.5)
        self._analog = Gtk.DrawingArea()
        self._analog.add_css_class("xx-clock-analog")
        self._analog.set_draw_func(self._draw_analog)
        self._analog.set_halign(Gtk.Align.CENTER)
        self._date = widgets.label("", "xx-secondary", xalign=0.5)
        self._date.add_css_class("xx-clock-date")
        self._date.set_halign(Gtk.Align.CENTER)
        self._style_btn = widgets.outline_button("Analog", on_style_changed)
        self._style_btn.set_halign(Gtk.Align.CENTER)
        self._style_btn.set_margin_top(20)
        self._card = widgets.card()
        self._card.set_spacing(4)
        self._card.set_margin_top(36)
        self._card.append(widgets.caption("Next alarm"))
        # Wraps rather than ellipsizing "in 8 h 0 m" at large text.
        self._next = widgets.wrap_label("", "xx-title")
        self._card.append(self._next)
        self._card.set_visible(False)
        body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
        body.add_css_class("xx-page-body")
        body.set_margin_top(72)
        body.set_margin_bottom(24)
        body.append(self._hero)
        body.append(self._analog)
        body.append(self._date)
        body.append(self._style_btn)
        body.append(self._card)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(body)
        scrolled.set_vexpand(True)
        self.widget = scrolled
        self.apply_style()

    def apply_style(self) -> None:
        analog = self._settings.clock_style == "analog"
        self._hero.set_visible(not analog)
        self._analog.set_visible(analog)
        self._style_btn.set_label("Digital" if analog else "Analog")

    def tick(self) -> None:
        now = _time.localtime()
        self._hero.set_text(_time.strftime("%H:%M", now))
        self._date.set_text(_time.strftime("%A, %b %-d", now))
        self._analog.queue_draw()

    def refresh(self, state: dict) -> None:
        upcoming = (state or {}).get("next")
        if not upcoming:
            self._card.set_visible(False)
            return
        self._card.set_visible(True)
        parts = [upcoming.get("fire_text", ""), upcoming.get("label") or "",
                 f"in {upcoming.get('until_text', '')}"]
        self._next.set_text(" · ".join(p for p in parts if p))

    def _draw_analog(self, area, cairo, width: int, height: int) -> None:
        import math
        now = _time.localtime()
        cx, cy = width / 2, height / 2
        radius = min(width, height) / 2 - 8
        context = cairo
        try:
            color = area.get_color()
            context.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        except Exception:
            pass
        context.set_line_width(2)
        context.arc(cx, cy, radius, 0, 2 * math.pi)
        context.stroke()
        for hour in range(12):
            angle = hour * math.pi / 6
            context.move_to(cx + (radius - 10) * math.sin(angle),
                            cy - (radius - 10) * math.cos(angle))
            context.line_to(cx + radius * math.sin(angle), cy - radius * math.cos(angle))
            context.stroke()
        hands = [((now.tm_hour % 12 + now.tm_min / 60) * math.pi / 6, radius * 0.5, 4),
                 ((now.tm_min + now.tm_sec / 60) * math.pi / 30, radius * 0.8, 3),
                 (now.tm_sec * math.pi / 30, radius * 0.9, 1)]
        for angle, length, thick in hands:
            context.set_line_width(thick)
            context.move_to(cx, cy)
            context.line_to(cx + length * math.sin(angle), cy - length * math.cos(angle))
            context.stroke()
