"""Alarms tab: sorted rows with switches, tap-to-edit, long-press delete."""

from __future__ import annotations

from .. import alarm_logic


def time_text(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def tap_zone(x: float, y: float, hit) -> str:
    """Where a tap at row (x, y) lands, given the switch's allocation
    ``hit`` (x, y, width, height): "switch" (the shared switch's 48 px
    target handles it) or "row" (open the editor)."""
    if (hit.x <= x <= hit.x + hit.width
            and hit.y <= y <= hit.y + hit.height):
        return "switch"
    return "row"


class AlarmsPage:
    def __init__(self, Gtk, Pango, widgets, service, settings, calls) -> None:
        self._Gtk = Gtk
        self._Pango = Pango
        self._widgets = widgets
        self._service = service
        self._calls = calls
        # No page title, like AlarmsFragment: the tab names the page.
        root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self._list = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self._list.add_css_class("xx-page-body")
        self._list.set_margin_top(16)
        # Room under the last row so the FAB never covers its switch.
        self._list.set_margin_bottom(88)
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
        # Empty list shows nothing: the FAB is the one way to add.
        self._clear()
        alarms = (state or {}).get("alarms", [])
        for index, item in enumerate(alarms):
            self._list.append(self._row(item, last=index == len(alarms) - 1))

    def _row(self, item: dict, last: bool):
        """AlarmsAdapter row: big time over the label and repeat summary,
        the enable switch at the end. Off alarms dim."""
        Gtk, Pango = self._Gtk, self._Pango
        row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        row.add_css_class("xx-row")
        if last:
            row.add_css_class("last")
        texts = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
        texts.set_hexpand(True)
        texts.set_valign(Gtk.Align.CENTER)
        if not item.get("enabled", True):
            texts.add_css_class("xx-clock-off")
        time_label = Gtk.Label(label=time_text(item["hour"], item["minute"]))
        time_label.add_css_class("xx-display-face")
        time_label.add_css_class("xx-clock-alarm-time")
        time_label.set_xalign(0.0)
        texts.append(time_label)
        summary = alarm_logic.summary(item.get("days_mask", 0))
        label_text = item.get("label") or ""
        sub = Gtk.Label(label=f"{label_text} · {summary}" if label_text else summary)
        sub.add_css_class("xx-secondary")
        sub.set_xalign(0.0)
        sub.set_ellipsize(Pango.EllipsizeMode.END)
        sub.set_max_width_chars(22)
        texts.append(sub)
        row.append(texts)
        switch = self._widgets.make_switch(
            item.get("enabled", True),
            lambda active, aid=item["id"]: self._calls.toggle_alarm(aid, active))
        switch.set_valign(Gtk.Align.CENTER)
        row.append(switch)

        def _edit(*_args) -> None:
            self._calls.open_editor(dict(item))

        # A tap acts on release; a long press deletes and swallows that
        # release, so holding a row never opens the editor too.
        held = {"down": False}

        def _pressed(*_args) -> None:
            held["down"] = False

        def _released(_gesture, _n, x: float, y: float) -> None:
            if held["down"]:
                return
            if tap_zone(x, y, switch.get_allocation()) == "switch":
                return
            _edit()

        def _held(*_args) -> None:
            held["down"] = True
            self._calls.delete_alarm(item["id"])

        click = Gtk.GestureClick.new()
        click.connect("pressed", _pressed)
        click.connect("released", _released)
        row.add_controller(click)
        longpress = Gtk.GestureLongPress.new()
        longpress.connect("pressed", _held)
        row.add_controller(longpress)
        return row
