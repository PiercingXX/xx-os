"""Clock tab: hero time, date, next-alarm card, digital/analog toggle."""

from __future__ import annotations

import time as _time

APP_CSS_EXTRA = """
.xx-clock-hero { font-size: 64px; font-weight: 300; font-feature-settings: "tnum"; }
.xx-clock-analog { min-width: 240px; min-height: 240px; }
.xx-clock-date { font-size: 16px; }
"""


class ClockPage:
    def __init__(self, Gtk, widgets, settings, on_style_changed) -> None:
        self._Gtk = Gtk
        self._widgets = widgets
        self._settings = settings
        self._on_style_changed = on_style_changed
        root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        root.append(widgets.page_header("Clock", "Local time"))
        self._hero = Gtk.Label(label="--:--")
        self._hero.add_css_class("xx-display-face")
        self._hero.add_css_class("xx-clock-hero")
        self._hero.set_xalign(0.5)
        self._analog = Gtk.DrawingArea()
        self._analog.add_css_class("xx-clock-analog")
        self._analog.set_draw_func(self._draw_analog)
        self._analog.set_halign(Gtk.Align.CENTER)
        self._date = widgets.label("", "xx-clock-date", xalign=0.5)
        self._date.set_halign(Gtk.Align.CENTER)
        self._card = widgets.card()
        self._card.set_margin_start(20)
        self._card.set_margin_end(20)
        self._card.set_margin_top(12)
        self._next = widgets.label("", "xx-secondary")
        self._card.append(self._next)
        self._style_btn = widgets.text_button("Analog", on_style_changed)
        self._style_btn.set_halign(Gtk.Align.CENTER)
        body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
        body.set_vexpand(True)
        body.set_valign(Gtk.Align.CENTER)
        body.append(self._hero)
        body.append(self._analog)
        body.append(self._date)
        body.append(self._card)
        body.append(self._style_btn)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(body)
        scrolled.set_vexpand(True)
        root.append(scrolled)
        self.widget = root
        self.apply_style()

    def apply_style(self) -> None:
        analog = self._settings.clock_style == "analog"
        self._hero.set_visible(not analog)
        self._analog.set_visible(analog)
        self._style_btn.set_label("Digital" if analog else "Analog")

    def tick(self) -> None:
        now = _time.localtime()
        self._hero.set_text(_time.strftime("%H:%M", now))
        self._date.set_text(_time.strftime("%a, %b %-d", now))
        self._analog.queue_draw()

    def refresh(self, state: dict) -> None:
        upcoming = (state or {}).get("next")
        if not upcoming:
            self._card.set_visible(False)
            return
        self._card.set_visible(True)
        label = upcoming.get("label") or "Alarm"
        self._next.set_text(f"{label} · {upcoming.get('fire_text', '')} ·"
                            f" in {upcoming.get('until_text', '')}")

    def _draw_analog(self, _area, cairo, width: int, height: int) -> None:
        import math
        now = _time.localtime()
        cx, cy = width / 2, height / 2
        radius = min(width, height) / 2 - 8
        context = cairo
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
