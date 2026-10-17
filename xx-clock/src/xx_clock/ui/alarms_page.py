"""Alarms tab: sorted rows with switches, tap-to-edit, long-press delete."""

from __future__ import annotations

from .. import alarm_logic


def time_text(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


class AlarmsPage:
    def __init__(self, Gtk, Pango, widgets, service, settings, calls) -> None:
        self._Gtk = Gtk
        self._Pango = Pango
        self._widgets = widgets
        self._service = service
        self._calls = calls
        root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        root.append(widgets.page_header("Alarms", None))
        self._list = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self._list.set_margin_start(20)
        self._list.set_margin_end(20)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(self._list)
        scrolled.set_vexpand(True)
        root.append(scrolled)
        overlay = Gtk.Overlay()
        overlay.set_child(root)
        fab = widgets.fab(lambda: calls.open_editor(None))
        fab.set_margin_bottom(16)
        overlay.add_overlay(fab)
        self.widget = overlay

    def _clear(self) -> None:
        child = self._list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._list.remove(child)
            child = nxt

    def refresh(self, state: dict) -> None:
        self._clear()
        alarms = (state or {}).get("alarms", [])
        if not alarms:
            empty = self._widgets.empty_state("No alarms yet", "Add alarm",
                                              lambda: self._calls.open_editor(None))
            empty.set_margin_top(32)
            self._list.append(empty)
            return
        for index, item in enumerate(alarms):
            self._list.append(self._row(item, last=index == len(alarms) - 1))

    def _row(self, item: dict, last: bool):
        Gtk, Pango = self._Gtk, self._Pango
        row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        row.add_css_class("xx-row")
        if last:
            row.add_css_class("last")
        row.set_margin_top(4)
        row.set_margin_bottom(4)
        time_label = Gtk.Label(label=time_text(item["hour"], item["minute"]))
        time_label.add_css_class("xx-display-face")
        time_label.add_css_class("xx-title")
        texts = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
        texts.set_hexpand(True)
        summary = alarm_logic.summary(item.get("days_mask", 0))
        label_text = item.get("label") or ""
        sub = summary if not label_text else f"{label_text} · {summary}"
        primary = Gtk.Label(label=sub or summary)
        primary.set_xalign(0.0)
        primary.set_ellipsize(Pango.EllipsizeMode.END)
        primary.set_max_width_chars(22)
        texts.append(primary)
        if item.get("vibrate"):
            muted = Gtk.Label(label="Vibrate")
            muted.add_css_class("xx-secondary")
            muted.set_xalign(0.0)
            texts.append(muted)
        row.append(time_label)
        row.append(texts)
        switch = self._widgets.make_switch(
            item.get("enabled", True),
            lambda active, aid=item["id"]: self._calls.toggle_alarm(aid, active))
        switch.set_valign(Gtk.Align.CENTER)
        row.append(switch)

        def _edit(*_args) -> None:
            self._calls.open_editor(dict(item))

        def _pressed(_gesture, _n, x: float, y: float) -> None:
            hit = switch.get_allocation()
            if hit.x <= x <= hit.x + hit.width and hit.y <= y <= hit.y + hit.height:
                return
            _edit()

        click = Gtk.GestureClick.new()
        click.connect("pressed", _pressed)
        row.add_controller(click)
        longpress = Gtk.GestureLongPress.new()
        longpress.connect("pressed", lambda *_a: self._calls.delete_alarm(item["id"]))
        row.add_controller(longpress)
        box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        box.append(row)
        return box
