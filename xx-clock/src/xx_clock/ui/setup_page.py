"""Setup tab: reliability page (daemon, autostart, limits, ringing prefs)."""

from __future__ import annotations

LIMIT_TEXT = ("Alarms and timers ring while the phone is on, including with "
              "the screen off. They do not fire while the phone is suspended "
              "or powered off: unprivileged apps cannot set an RTC wakeup. "
              "Missed alarms appear as notifications on wake.")


class SetupPage:
    def __init__(self, Gtk, widgets, settings, calls) -> None:
        self._Gtk = Gtk
        self._widgets = widgets
        self._settings = settings
        self._calls = calls
        root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        root.append(widgets.page_header("Setup", "Reliable ringing"))
        body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        body.set_margin_start(20)
        body.set_margin_end(20)
        body.append(widgets.section_label("Service"))
        self._daemon_row = widgets.setting_row("Alarm service", "Checking…")
        body.append(self._daemon_row)
        self._autostart_row = widgets.setting_row("Start at login", "Checking…")
        body.append(self._autostart_row)
        body.append(widgets.section_label("Limits"))
        limit = widgets.wrap_label(LIMIT_TEXT, "xx-secondary")
        body.append(limit)
        body.append(widgets.section_label("Ringing"))
        snooze_box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        snooze_box.append(widgets.label("Snooze length", "xx-title"))
        spacer = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        spacer.set_hexpand(True)
        snooze_box.append(spacer)
        adj = Gtk.Adjustment.new(settings.snooze_minutes, 1, 120, 1, 5, 0)
        self._snooze = Gtk.SpinButton.new(adj, 1, 0)
        self._snooze.set_size_request(88, 48)
        self._snooze.connect("value-changed", lambda _s: calls.set_snooze(int(_s.get_value())))
        snooze_box.append(self._snooze)
        body.append(snooze_box)
        ramp = widgets.setting_row(
            "Gradual volume", "Fades in over a minute",
            trailing=widgets.make_switch(settings.gradual_volume, calls.set_gradual))
        body.append(ramp)
        test_row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        test_row.set_margin_top(8)
        test_row.append(widgets.outline_button("Test sound", calls.test_sound))
        body.append(test_row)
        body.append(widgets.section_label("About"))
        about = widgets.wrap_label(
            "xx-clock 0.1.0 · GPL-3.0 · offline, no accounts. "
            "Original code; no sounds bundled. Licenses: see the source tree.",
            "xx-secondary")
        body.append(about)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(body)
        scrolled.set_vexpand(True)
        root.append(scrolled)
        self.widget = root

    def refresh(self, daemon_state: dict) -> None:
        running = daemon_state.get("running")
        autostart = daemon_state.get("autostart")
        self._replace_subtitle(self._daemon_row,
                               "Running" if running else "Not running — the app still rings while open")
        self._replace_subtitle(self._autostart_row,
                               "Enabled" if autostart else "Not enabled — enable the user service")

    def _replace_subtitle(self, row, text: str) -> None:
        middle = row.get_first_child()
        if middle is None:
            return
        sub = middle.get_first_child()
        if sub is not None:
            sub = sub.get_next_sibling()
        if sub is not None:
            try:
                sub.set_text(text)
            except Exception:
                pass
