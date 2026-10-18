"""Setup tab: reliability page (daemon, autostart, limits, ringing prefs)."""

from __future__ import annotations

from .. import __version__ as _version

# Wake-from-suspend is impossible unprivileged (PORT_SPEC §4); one line
# says what that means for the user.
LIMIT_TEXT = "Silent when suspended or off"


def daemon_subtitles(daemon_state: dict) -> tuple[str, str]:
    """(alarm service, start at login) row subtitles."""
    running = "Running" if daemon_state.get("running") else "Not running"
    login = "On" if daemon_state.get("autostart") else "Off"
    return running, login


def wrap_labels(root) -> None:
    """Make every label under ``root`` wrap between words instead of
    ellipsizing."""
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk, Pango
    stack = [root]
    while stack:
        widget = stack.pop()
        if isinstance(widget, Gtk.Label):
            widget.set_ellipsize(Pango.EllipsizeMode.NONE)
            widget.set_wrap(True)
            widget.set_wrap_mode(Pango.WrapMode.WORD)
        child = widget.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()


class SetupPage:
    def __init__(self, Gtk, widgets, settings, calls) -> None:
        self._Gtk = Gtk
        self._widgets = widgets
        self._settings = settings
        self._calls = calls
        body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        body.add_css_class("xx-page-body")
        body.set_margin_top(8)
        body.set_margin_bottom(24)
        body.append(widgets.section_label("Service"))
        self._daemon_row = widgets.setting_row("Alarm service", "Checking")
        body.append(self._daemon_row)
        self._autostart_row = widgets.setting_row("Start at login", "Checking")
        body.append(self._autostart_row)
        body.append(self._wrapped_row("Limits", LIMIT_TEXT))
        body.append(widgets.section_label("Ringing"))
        adj = Gtk.Adjustment.new(settings.snooze_minutes, 1, 120, 1, 5, 0)
        self._snooze = Gtk.SpinButton.new(adj, 1, 0)
        # Theme-sized digits (a bare SpinButton keeps the unscaled base
        # font) and 48 px -/+ targets.
        self._snooze.add_css_class("xx-clock-snooze")
        self._snooze.add_css_class("xx-title")
        self._snooze.set_width_chars(3)
        self._snooze.connect("value-changed", lambda _s: calls.set_snooze(int(_s.get_value())))
        body.append(widgets.setting_row("Snooze", "Minutes", trailing=self._snooze))
        body.append(widgets.setting_row(
            "Gradual volume", "Fades in over a minute",
            trailing=widgets.make_switch(settings.gradual_volume, calls.set_gradual)))
        body.append(widgets.list_row("Test sound", "Plays for 3 seconds",
                                     on_activate=calls.test_sound, last=True))
        body.append(widgets.section_label("About"))
        body.append(widgets.setting_row("xx-clock", f"Version {_version} · GPL-3.0"))
        body.append(widgets.list_row("Open-source licenses", "",
                                     on_activate=calls.show_licenses, last=True))
        # Every line on this page is short: at 320 px and large text it
        # wraps onto a second line rather than losing its end to an
        # ellipsis ("Open-source licen…").
        wrap_labels(body)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(body)
        scrolled.set_vexpand(True)
        self.widget = scrolled

    def _wrapped_row(self, title: str, subtitle: str):
        """A setting row whose subtitle wraps instead of ellipsizing: the
        Limits line is the one sentence on this page that must be read
        whole, at 320 px and at large text too."""
        row = self._widgets.setting_row(title, last=True)
        row.get_first_child().append(self._widgets.wrap_label(subtitle))
        return row

    def refresh(self, daemon_state: dict) -> None:
        running, login = daemon_subtitles(daemon_state)
        self._replace_subtitle(self._daemon_row, running)
        self._replace_subtitle(self._autostart_row, login)

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
