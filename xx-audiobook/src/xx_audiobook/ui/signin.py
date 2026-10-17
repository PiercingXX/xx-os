"""Sign in and the auth gate's first frame (ui/login/SignInScreen.kt,
ui/App.kt).

The window opens on a neutral Checking frame while the context resolves
auth on a worker (hub probe, then the stored token). Only when both fail
does Sign in cover the whole window: estate address, username and
password, Sign in, and "I signed in to xx-apps — try again" for when the
hub has come up since. The password lives in its entry until the login
request is built, then the entry is cleared; it is never stored or
logged. The tabs appear only after a sign-in succeeds.
"""

from __future__ import annotations

from gi.repository import Gtk

from xx_audiobook.ui import common
from xx_audiobook.ui.library import deferred
from xx_ui import widgets

HEADING = "Audiobook · estate account"
INTRO = "Use your estate account — the one xx-apps signs in with."
RETRY_HUB = "I signed in to xx-apps — try again"
CHECKING = "Checking your estate sign-in"


def checking_view() -> Gtk.Widget:
    """The brief neutral frame while auth resolves: the ground and one
    small spinner, never a flash of Library."""
    box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    box.add_css_class("xx-ab-checking")
    box.set_vexpand(True)
    from gi.repository import Adw

    if hasattr(Adw, "Spinner"):
        spinner = Adw.Spinner()
    else:  # libadwaita < 1.6
        spinner = Gtk.Spinner()
        spinner.start()
    spinner.set_size_request(24, 24)
    spinner.set_halign(Gtk.Align.CENTER)
    spinner.set_valign(Gtk.Align.CENTER)
    spinner.set_vexpand(True)
    spinner.update_property([Gtk.AccessibleProperty.LABEL], [CHECKING])
    box.append(spinner)
    return box


class SignInView:
    """Full-window Sign in. ``ctx.sign_in`` / ``ctx.retry_hub`` run on a
    worker and emit session-changed on success; the shell then swaps in
    the tabs."""

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.error: str | None = None
        self.busy = False
        self._entries: dict = {}
        self.body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)
        self.body.add_css_class("xx-ab-signin")
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(self.body)
        self.widget = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self.widget.add_css_class("xx-page")
        self.widget.append(scroll)
        self.render()

    def _entry(self, key: str, caption: str, placeholder: str,
               purpose) -> Gtk.Entry:
        entry = self._entries.get(key)
        if entry is None:
            entry = deferred(widgets.text_entry(placeholder))
            entry.set_input_purpose(purpose)
            entry.update_property([Gtk.AccessibleProperty.LABEL], [caption])
            entry.set_hexpand(True)
            entry.connect("activate", lambda _e: self.sign_in())
            if key == "password":
                entry.set_visibility(False)
            self._entries[key] = entry
        else:
            parent = entry.get_parent()
            if parent is not None:
                parent.remove(entry)
        return entry

    def render(self) -> None:
        body = self.body
        common.clear(body)
        heading = common.label(HEADING, "xx-ab-screen", lines=3, chars=16)
        heading.update_property([Gtk.AccessibleProperty.LABEL], [HEADING])
        body.append(heading)
        body.append(common.para(INTRO, "xx-ab-b13 xx-ab-muted"))
        fields = (("origin", "Estate address", "https://estate.example",
                   Gtk.InputPurpose.URL),
                  ("username", "Username", "", Gtk.InputPurpose.FREE_FORM),
                  ("password", "Password", "", Gtk.InputPurpose.PASSWORD))
        for key, caption, placeholder, purpose in fields:
            field = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
            field.append(common.label(caption, "xx-ab-field", chars=24))
            entry = self._entry(key, caption, placeholder, purpose)
            if key == "password":
                row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 4)
                row.append(entry)
                shown = entry.get_visibility()
                row.append(common.icon_button(
                    "view-conceal-symbolic" if shown
                    else "view-reveal-symbolic",
                    "Hide password" if shown else "Show password",
                    self._toggle_password))
                field.append(row)
            else:
                field.append(entry)
            body.append(field)
        message = self.error or (self.ctx.session.sign_out_message
                                 if self.ctx.session is not None else None)
        if message:
            body.append(common.para(message, "xx-ab-error"))
        go = widgets.primary_button(
            "Signing in…" if self.busy else "Sign in", self.sign_in)
        go.set_sensitive(not self.busy)
        common.fit(go, 20)
        go.set_margin_top(4)
        body.append(go)
        retry = widgets.text_button(RETRY_HUB, self.retry_hub)
        retry.set_sensitive(not self.busy)
        common.fit(retry, 34)
        retry.get_child().set_wrap(True)
        retry.get_child().set_lines(2)
        retry.get_child().set_justify(Gtk.Justification.CENTER)
        body.append(retry)

    # -- actions ------------------------------------------------------------

    def _toggle_password(self) -> None:
        entry = self._entries.get("password")
        if entry is not None:
            entry.set_visibility(not entry.get_visibility())
            self.render()

    def sign_in(self) -> None:
        if self.busy:
            return
        origin = self._entries["origin"].get_text().strip()
        username = self._entries["username"].get_text().strip()
        password = self._entries["password"].get_text()
        # The password leaves the widget now: it exists only in the
        # login request body from here on.
        self._entries["password"].set_text("")
        if origin and (not username or not password):
            self.error = "Enter your estate username and password."
            self.render()
            return
        self.busy, self.error = True, None
        self.render()

        def _done(message) -> None:
            self.busy = False
            self.error = message
            self.render()

        self.ctx.sign_in(origin, username, password, _done)

    def retry_hub(self) -> None:
        self.busy, self.error = True, None
        self.render()

        def _done(message) -> None:
            self.busy = False
            self.error = message
            self.render()

        self.ctx.retry_hub(_done)
