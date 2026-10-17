"""Full-screen ring card (Linux half of ui/AlarmAlertActivity.kt).

Shows over the XX-WM lock screen exactly the way the shell's call UI
does: a Gtk4LayerShell OVERLAY surface anchored on every edge. Always ink
— the fixed near-black ground mirrors AlarmAlertActivity's purpose-built
dark look (3 a.m. is not the moment for a Paper screen), not the suite
theme. Dismiss on alarms, Stop on timers, Snooze / +1 min beside them.
"""

from __future__ import annotations

INK_CSS = """
.xx-ring { background-color: #000000; color: #ffffff; }
.xx-ring .xx-ring-title { color: #ffffff; font-size: 30px; }
.xx-ring .xx-ring-time { color: #ffffff; font-size: 64px; font-weight: 300; }
.xx-ring .xx-ring-sub { color: #b0b0b0; font-size: 15px; }
.xx-ring button { border: 1px solid #ffffff; border-radius: 12px;
  color: #ffffff; min-height: 56px; padding: 0 20px; font-size: 17px; }
.xx-ring button:active { background-color: #222222; }
"""


class RingCard:
    def __init__(self, Gtk, Gdk, widgets, calls) -> None:
        self._Gtk = Gtk
        self._calls = calls
        try:
            import gi
            gi.require_version("Gtk4LayerShell", "1.0")
            from gi.repository import Gtk4LayerShell as LayerShell
        except Exception:
            LayerShell = None
        self._LayerShell = LayerShell
        win = Gtk.Window(title="xx-clock")
        win.add_css_class("xx-ring")
        provider = Gtk.CssProvider()
        provider.load_from_data(INK_CSS.encode("utf-8"))
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        if LayerShell is not None:
            try:
                if LayerShell.is_supported():
                    LayerShell.init_for_window(win)
                    LayerShell.set_layer(win, LayerShell.Layer.OVERLAY)
                    for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM,
                                 LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
                        LayerShell.set_anchor(win, edge, True)
                    LayerShell.set_exclusive_zone(win, -1)
                    LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.NONE)
                    win.set_default_size(360, 720)
            except Exception:
                win.set_default_size(360, 720)
                win.fullscreen()
        else:
            win.set_default_size(360, 720)
            win.fullscreen()
        root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)
        root.set_valign(Gtk.Align.CENTER)
        root.set_margin_start(32)
        root.set_margin_end(32)
        self._kind = Gtk.Label(label="")
        self._kind.add_css_class("xx-ring-sub")
        self._kind.set_xalign(0.5)
        root.append(self._kind)
        self._title = Gtk.Label(label="")
        self._title.add_css_class("xx-ring-title")
        self._title.set_xalign(0.5)
        self._title.set_wrap(True)
        root.append(self._title)
        self._time = Gtk.Label(label="")
        self._time.add_css_class("xx-ring-time")
        self._time.add_css_class("xx-display-face")
        self._time.set_xalign(0.5)
        root.append(self._time)
        self._buttons = Gtk.FlowBox()
        self._buttons.set_selection_mode(Gtk.SelectionMode.NONE)
        self._buttons.set_column_spacing(12)
        self._buttons.set_row_spacing(12)
        self._buttons.set_max_children_per_line(2)
        self._buttons.set_min_children_per_line(1)
        self._buttons.set_halign(Gtk.Align.CENTER)
        root.append(self._buttons)
        win.set_child(root)
        self.win = win

    def _clear_buttons(self) -> None:
        child = self._buttons.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._buttons.remove(child)
            child = nxt

    def _button(self, text: str, callback) -> None:
        btn = self._Gtk.Button.new_with_label(text)
        btn.connect("clicked", lambda _b: callback())
        self._buttons.append(btn)

    def show_alarm(self, entry_id: int, label: str, snooze_minutes: int) -> None:
        self._kind.set_text("ALARM")
        self._title.set_text(label or "Alarm")
        import time as _time
        self._time.set_text(_time.strftime("%H:%M"))
        self._clear_buttons()
        self._button(f"Snooze {snooze_minutes} min",
                     lambda: self._calls.snooze_ringing(entry_id, snooze_minutes))
        self._button("Dismiss", lambda: self._calls.dismiss_ringing(entry_id))
        self.win.present()

    def show_timer(self, entry_id: int, label: str) -> None:
        self._kind.set_text("TIMER")
        self._title.set_text(label or "Timer")
        self._time.set_text("00:00")
        self._clear_buttons()
        self._button("Stop", lambda: self._calls.stop_timer_ringing(entry_id))
        self._button("+1 min", lambda: self._calls.add_minute_timer(entry_id))
        self.win.present()

    def hide(self) -> None:
        try:
            self.win.hide()
        except Exception:
            pass
