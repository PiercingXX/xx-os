"""Timers tab: preset chips, custom minutes, live cards.

Mirrors TimersFragment: one scrolling row of preset chips, a minutes
field with Start, then a card per timer. No empty-state line: with no
timers the presets are the page.
"""

from __future__ import annotations

import time as _time

from .. import timer_math

PRESETS = (("1 min", 60_000), ("3 min", 180_000), ("5 min", 300_000),
           ("10 min", 600_000))
MIN_MINUTES = 1
MAX_MINUTES = 999

APP_CSS_EXTRA = """
.xx-clock-timer-time { font-size: 32px; font-weight: 400; font-feature-settings: "tnum"; }
.xx-clock-presets .xx-chip { padding: 0 4px; min-width: 0; }
.xx-clock-act { min-height: 40px; padding: 0 10px; }
.xx-clock-act.first { padding-left: 0; }
.xx-clock-acts > flowboxchild { padding: 0; }
"""


def parse_minutes(text: str) -> int | None:
    """The custom field's minutes, or None outside 1..999 (Android's
    MIN_MINUTES/MAX_MINUTES)."""
    try:
        minutes = int(text.strip())
    except (TypeError, ValueError):
        return None
    return minutes if MIN_MINUTES <= minutes <= MAX_MINUTES else None


class TimersPage:
    def __init__(self, Gtk, Pango, widgets, calls) -> None:
        self._Gtk = Gtk
        self._Pango = Pango
        self._widgets = widgets
        self._calls = calls
        self._state: dict = {}
        self._labels: dict = {}
        root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        # The four presets share the width; past what fits (320 px at
        # large text) the row scrolls, like Android's HorizontalScrollView.
        row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
        row.add_css_class("xx-clock-presets")
        row.set_homogeneous(True)
        for label, duration in PRESETS:
            chip = Gtk.Button()
            chip.add_css_class("xx-chip")
            text = widgets.label(label.upper(), "xx-chip-text")
            widgets.fit_text(text, label.upper(), 8)
            text.set_xalign(0.5)
            chip.set_child(text)
            chip.connect("clicked", lambda _b, d=duration: calls.start_timer(d, ""))
            row.append(chip)
        chips = Gtk.ScrolledWindow()
        chips.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        chips.set_child(row)
        chips.add_css_class("xx-page-body")
        chips.set_margin_top(16)
        root.append(chips)
        custom = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        custom.add_css_class("xx-page-body")
        custom.set_margin_top(12)
        custom.set_margin_bottom(8)
        self._custom = widgets.deferred_entry("Minutes")
        self._custom.set_input_purpose(Gtk.InputPurpose.DIGITS)
        self._custom.set_max_length(3)
        self._custom.set_hexpand(True)
        self._custom.connect("activate", lambda _e: self._start_custom())
        custom.append(self._custom)
        custom.append(widgets.primary_button("Start", self._start_custom))
        root.append(custom)
        self._list = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
        self._list.add_css_class("xx-page-body")
        self._list.set_margin_top(8)
        self._list.set_margin_bottom(16)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(self._list)
        scrolled.set_vexpand(True)
        root.append(scrolled)
        self.widget = root

    def _start_custom(self) -> None:
        minutes = parse_minutes(self._custom.get_text())
        if minutes is None:
            self._custom.add_css_class("error")
            return
        self._custom.remove_css_class("error")
        self._custom.set_text("")
        self._calls.start_timer(minutes * 60_000, "")

    def _clear(self) -> None:
        child = self._list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._list.remove(child)
            child = nxt

    def refresh(self, state: dict) -> None:
        self._state = state or {}
        self._labels = {}
        self._clear()
        for item in self._state.get("timers", []):
            self._list.append(self._card(item))

    def tick(self) -> None:
        labels = getattr(self, "_labels", {})
        if not labels:
            return
        now_ms = int(_time.time() * 1000)
        timers = {t["id"]: t for t in self._state.get("timers", [])}
        for key, label in list(labels.items()):
            try:
                item = timers[int(key.split("-", 1)[1])]
            except (KeyError, ValueError):
                continue
            try:
                label.set_text(timer_math.display(self._remaining(item, now_ms)))
            except Exception:
                pass

    def _remaining(self, item: dict, now_ms: int) -> int:
        from ..models import TimerItem
        timer = TimerItem(id=item["id"], duration_ms=item["duration_ms"],
                          state=item["state"], ends_at_ms=item["ends_at_ms"],
                          remaining_ms=item["remaining_ms"], label=item.get("label", ""))
        return timer_math.remaining_ms(timer, now_ms)

    def _action(self, text: str, callback, first: bool = False):
        """Chromeless caps action: text-first, muted, 40 px target."""
        Gtk = self._Gtk
        button = Gtk.Button()
        button.add_css_class("xx-btn")
        button.add_css_class("xx-clock-act")
        if first:
            button.add_css_class("first")
        # Exactly its natural width inside the button: a plain caption
        # ellipsizes on fractional rounding ("RESE…"), so fit it exactly.
        caption = self._widgets.caption(text)
        self._widgets.fit_text(caption, text.upper(), 12)
        button.set_child(caption)
        button.connect("clicked", lambda _b: callback())
        return button

    def _card(self, item: dict):
        """TimersFragment card: remaining time with the primary action
        (Pause/Resume) beside it, the label under it, then Reset, +1 min
        and Delete. A paused timer dims its time instead of a badge."""
        Gtk = self._Gtk
        timer_id = item["id"]
        state = item["state"]
        card = self._widgets.card()
        card.set_spacing(4)
        head = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        title = Gtk.Label(label=timer_math.display(self._remaining(item, int(_time.time() * 1000))))
        title.add_css_class("xx-display-face")
        title.add_css_class("xx-clock-timer-time")
        title.set_xalign(0.0)
        title.set_hexpand(True)
        if state == "PAUSED":
            title.add_css_class("xx-clock-off")
        self._labels[f"timer-{timer_id}"] = title
        head.append(title)
        primary = None
        if state == "RUNNING":
            primary = self._widgets.primary_button(
                "Pause", lambda: self._calls.pause_timer(timer_id))
        elif state == "PAUSED":
            primary = self._widgets.primary_button(
                "Resume", lambda: self._calls.resume_timer(timer_id))
        if primary is not None:
            primary.set_valign(Gtk.Align.CENTER)
            head.append(primary)
        card.append(head)
        if item.get("label"):
            card.append(self._widgets.label(item["label"], "xx-secondary"))
        actions = Gtk.FlowBox()
        actions.add_css_class("xx-clock-acts")
        actions.set_halign(Gtk.Align.START)
        actions.set_selection_mode(Gtk.SelectionMode.NONE)
        actions.set_homogeneous(False)
        actions.set_column_spacing(4)
        actions.set_row_spacing(0)
        actions.set_max_children_per_line(3)
        actions.set_min_children_per_line(1)
        actions.set_margin_top(4)
        buttons = []
        if state in ("RUNNING", "PAUSED"):
            buttons.append(("Reset", lambda: self._calls.reset_timer(timer_id)))
            buttons.append(("+1 min", lambda: self._calls.add_minute_timer(timer_id)))
        buttons.append(("Delete", lambda: self._calls.delete_timer(timer_id)))
        for index, (text, callback) in enumerate(buttons):
            actions.append(self._action(text, callback, first=index == 0))
        card.append(actions)
        return card
