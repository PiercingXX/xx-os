"""Full-screen ring card (Linux half of ui/AlarmAlertActivity.kt).

Shows over the XX-WM lock screen exactly the way the shell's call UI
does: a Gtk4LayerShell OVERLAY surface anchored on every edge. Always ink
— the fixed near-black ground mirrors AlarmAlertActivity's purpose-built
dark look (3 a.m. is not the moment for a Paper screen), not the suite
theme. Dismiss on alarms, Stop on timers, Snooze / +1 min beside them.
"""

from __future__ import annotations

INK_CSS = """
.xx-ring { background-color: #000000; color: rgba(255, 255, 255, 0.9); }
.xx-ring .xx-ring-time { color: #ffffff; font-size: 84px; font-weight: 400; }
.xx-ring .xx-ring-label { color: rgba(255, 255, 255, 0.5); font-size: 22px; }
.xx-ring button { border: 1px solid rgba(255, 255, 255, 0.25); border-radius: 12px;
  background: none; box-shadow: none; color: rgba(255, 255, 255, 0.9);
  min-height: 56px; padding: 0 12px; font-size: 17px; font-weight: 400; }
.xx-ring button:active { background-color: #18181b; }
.xx-ring button.xx-ring-primary { background-color: #ffffff; color: #000000;
  border-color: #ffffff; }
.xx-ring button.xx-ring-primary:active { background-color: #cccccc; }
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
        layered = False
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
                    layered = True
            except Exception:
                layered = False
        win.set_default_size(360, 720)
        if not layered:
            # No layer shell (another compositor): a borderless
            # full-screen window, never one with a title bar.
            win.set_decorated(False)
            win.fullscreen()
        # AlarmAlertActivity: the time huge in the display face, the label
        # under it, then the secondary action (outline) and the primary
        # one (filled Signal white) side by side at equal width.
        root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        root.set_valign(Gtk.Align.CENTER)
        root.set_margin_start(24)
        root.set_margin_end(24)
        self._time = Gtk.Label(label="")
        self._time.add_css_class("xx-ring-time")
        self._time.add_css_class("xx-display-face")
        self._time.set_xalign(0.5)
        root.append(self._time)
        self._title = Gtk.Label(label="")
        self._title.add_css_class("xx-ring-label")
        self._title.set_xalign(0.5)
        self._title.set_margin_top(16)
        self._title.set_wrap(True)
        self._title.set_max_width_chars(20)
        self._title.set_justify(Gtk.Justification.CENTER)
        root.append(self._title)
        self._buttons = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 24)
        self._buttons.set_homogeneous(True)
        self._buttons.set_margin_top(48)
        root.append(self._buttons)
        win.set_child(root)
        self.win = win

    def _clear_buttons(self) -> None:
        child = self._buttons.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._buttons.remove(child)
            child = nxt

    def _button(self, text: str, callback, primary: bool = False) -> None:
        btn = self._Gtk.Button.new_with_label(text)
        if primary:
            btn.add_css_class("xx-ring-primary")
        btn.set_hexpand(True)
        btn.connect("clicked", lambda _b: callback())
        self._buttons.append(btn)

    def show_alarm(self, entry_id: int, label: str, snooze_minutes: int) -> None:
        self._title.set_text(label or "Alarm")
        import time as _time
        self._time.set_text(_time.strftime("%H:%M"))
        self._clear_buttons()
        self._button("Snooze",
                     lambda: self._calls.snooze_ringing(entry_id, snooze_minutes))
        self._button("Dismiss", lambda: self._calls.dismiss_ringing(entry_id),
                     primary=True)
        self.win.present()

    def show_timer(self, entry_id: int, label: str) -> None:
        self._title.set_text(label or "Timer")
        self._time.set_text("00:00")
        self._clear_buttons()
        self._button("+1 min", lambda: self._calls.add_minute_timer(entry_id))
        self._button("Stop", lambda: self._calls.stop_timer_ringing(entry_id),
                     primary=True)
        self.win.present()

    def hide(self) -> None:
        try:
            self.win.hide()
        except Exception:
            pass
