"""Timers tab: preset chips, custom minutes, live cards."""

from __future__ import annotations

import time as _time

from .. import timer_math

PRESETS = (("1 min", 60_000), ("3 min", 180_000), ("5 min", 300_000),
           ("10 min", 600_000))


class TimersPage:
    def __init__(self, Gtk, Pango, widgets, calls) -> None:
        self._Gtk = Gtk
        self._Pango = Pango
        self._widgets = widgets
        self._calls = calls
        self._state: dict = {}
        root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        root.append(widgets.page_header("Timers", None))
        chips = Gtk.Grid()
        chips.set_column_homogeneous(True)
        chips.set_column_spacing(8)
        chips.set_row_spacing(8)
        chips.set_margin_start(20)
        chips.set_margin_end(20)
        chips.set_margin_bottom(8)
        for index, (label, duration) in enumerate(PRESETS):
            btn = widgets.outline_button(label,
                                         lambda _b=None, d=duration: calls.start_timer(d, ""))
            btn.set_hexpand(True)
            chips.attach(btn, index % 2, index // 2, 1, 1)
        root.append(chips)
        custom = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
        custom.set_margin_start(20)
        custom.set_margin_end(20)
        custom.set_margin_bottom(8)
        adj = Gtk.Adjustment.new(5, 1, 24 * 60, 1, 5, 0)
        self._custom = Gtk.SpinButton.new(adj, 1, 0)
        self._custom.set_size_request(96, 48)
        custom.append(self._custom)
        custom.append(widgets.primary_button(
            "Start", lambda: calls.start_timer(int(self._custom.get_value()) * 60_000, "")))
        root.append(custom)
        self._list = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
        self._list.set_margin_start(20)
        self._list.set_margin_end(20)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(self._list)
        scrolled.set_vexpand(True)
        root.append(scrolled)
        self.widget = root

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
        timers = self._state.get("timers", [])
        if not timers:
            empty = self._widgets.empty_state("No timers running")
            empty.set_margin_top(24)
            self._list.append(empty)
            return
        for item in timers:
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

    def _card(self, item: dict):
        Gtk = self._Gtk
        card = self._widgets.card()
        now_ms = int(_time.time() * 1000)
        remaining = self._remaining(item, now_ms)
        title = Gtk.Label(label=timer_math.display(remaining))
        title.add_css_class("xx-display-face")
        title.add_css_class("xx-title")
        title.set_xalign(0.0)
        self._labels[f"timer-{item['id']}"] = title
        card.append(title)
        if item.get("label"):
            sub = self._widgets.label(item["label"], "xx-secondary")
            card.append(sub)
        row = Gtk.FlowBox()
        row.set_selection_mode(Gtk.SelectionMode.NONE)
        row.set_column_spacing(8)
        row.set_row_spacing(8)
        row.set_max_children_per_line(4)
        row.set_min_children_per_line(1)
        state = item["state"]
        if state == "RUNNING":
            row.append(self._widgets.outline_button(
                "Pause", lambda: self._calls.pause_timer(item["id"])))
        elif state == "PAUSED":
            row.append(self._widgets.outline_button(
                "Resume", lambda: self._calls.resume_timer(item["id"])))
        if state in ("RUNNING", "PAUSED"):
            row.append(self._widgets.outline_button(
                "Reset", lambda: self._calls.reset_timer(item["id"])))
            row.append(self._widgets.outline_button(
                "+1 min", lambda: self._calls.add_minute_timer(item["id"])))
        row.append(self._widgets.text_button(
            "Delete", lambda: self._calls.delete_timer(item["id"])))
        card.append(row)
        return card
