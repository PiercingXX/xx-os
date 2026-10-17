"""Setup (M4/M5), Sign in and No access (ui/setup/SetupScreen.kt,
ui/login/SignInScreen.kt, ui/login/NoAccessScreen.kt).

Setup: the account and its origin (an ADMIN badge for admins), Listening
stats, the server status rows (admins), playback prefs synced to the
account (skip back / forward 10/15/30, default speed, rewind on resume),
Wi-Fi-only downloads and storage, the theme line (it follows the shell:
no picker here), Share logs (the last 100 KB, redacted), Sign out (asks
first) and About + licenses.

Sign in shows only when the hub probe and the stored token both failed:
estate address, username and password. The password lives in the entry
until the request is built, then the entry is cleared; it is never
stored or logged. No access is the full-screen 403 not_granted state
with Retry and Sign out.
"""

from __future__ import annotations

import os

from gi.repository import Gdk, Gtk

from xx_audiobook import __version__, fmt
from xx_audiobook import api as api_module
from xx_audiobook import applog as applog_module
from xx_audiobook import paths as paths_module
from xx_audiobook import prefs as prefs_module
from xx_audiobook.ui import common, style
from xx_audiobook.ui.library import deferred
from xx_ui import widgets

APP_NAME = "xx-audiobook"
REWIND_CHOICES = (0, 2, 5, 10)
SPEED_CHOICES = (0.75, 1.0, 1.25, 1.5, 2.0)
SUBSYSTEM_NAMES = {"ha": "Home Assistant", "db": "Database",
                   "scanner": "Library scan", "stream": "Streaming",
                   "cast": "Casting", "matcher": "Matching"}
GOOD_STATES = ("ok", "ready", "running", "up", "configured", "healthy")
SHARE_NAME = "xx-audiobook-logs.txt"


def license_entries() -> list:
    """The shared table minus what this app does not ship (phone
    numbers), plus GStreamer for playback."""
    entries = [e for e in widgets.standard_license_entries(APP_NAME)
               if e.name != "phonenumbers"]
    entries.insert(3, widgets.LicenseEntry(
        "GStreamer", "LGPL-2.1-or-later", "https://gstreamer.freedesktop.org/",
        ("gstreamer", "gst-plugins-base", "gst-plugins-good")))
    return entries


def subsystem_name(key: str) -> str:
    return SUBSYSTEM_NAMES.get(key, key.replace("_", " ").capitalize())


def source_line(account) -> str:
    if account is None:
        return ""
    via = {"hub": "the xx-apps hub", "login": "this phone",
           "stored": "a saved session"}.get(account.source, account.source)
    return f"Signed in via {via}. The password is never stored."


def host_of(origin: str) -> str:
    text = (origin or "").split("://", 1)[-1]
    return text.rstrip("/")


def write_share_file(text: str):
    """The redacted dump as a 0600 file in the cache folder."""
    path = paths_module.ensure_parent_for(paths_module.cache_dir()
                                          / SHARE_NAME)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def _home_short(path) -> str:
    home = os.path.expanduser("~")
    text = str(path)
    return "~" + text[len(home):] if text.startswith(home) else text


def _chips(options: list) -> Gtk.Widget:
    """Homogeneous 48 px chips: (text, selected, spoken, callback). At
    large text sizes they wrap three to a line and drop the unit."""
    large = style.large_text()
    if large:
        row = Gtk.FlowBox()
        row.set_selection_mode(Gtk.SelectionMode.NONE)
        row.set_homogeneous(True)
        row.set_column_spacing(6)
        row.set_row_spacing(6)
        row.set_min_children_per_line(3)
        row.set_max_children_per_line(3)
    else:
        row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 6)
        row.set_homogeneous(True)
    row.add_css_class("xx-ab-choice")
    row.set_margin_top(10)
    for text, selected, spoken, callback in options:
        if large:
            text = text.replace(" sec", "")
        chip = Gtk.Button.new_with_label(text.upper())
        chip.add_css_class("xx-chip")
        chip.set_size_request(-1, 48)
        chip.get_child().set_ellipsize(3)  # Pango END: never widens
        if selected:
            chip.add_css_class("selected")
        chip.connect("clicked", lambda _b, cb=callback: cb())
        common.name(chip, spoken + (", selected" if selected else ""))
        row.append(chip)
        if large:
            chip.get_parent().set_focusable(False)
    return row


def _block(title: str, sub: str = "") -> Gtk.Box:
    box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
    box.add_css_class("xx-ab-cell-box")
    box.append(common.label(title, "xx-title", chars=24))
    if sub:
        box.append(common.label(sub, "xx-secondary", lines=2, chars=30))
    return box


class SetupPage:
    def __init__(self, ctx, nav, window_getter) -> None:
        self.ctx = ctx
        self.nav = nav
        self._window = window_getter
        self.prefs = prefs_module.Prefs()
        self.storage = None
        self.wifi_only = True
        self.status = None
        self.status_error: str | None = None
        self.status_loading = False
        self.sync_line: str | None = None
        self.share_line: str | None = None
        self.sign_in_error: str | None = None
        self.busy = False
        self.loaded = False
        self.body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self.body.set_margin_bottom(28)
        self.widget = widgets.page(common.page_top("Setup"), self.body)
        self._entries: dict = {}
        self._render()
        for event in ("ready", "session-changed", "account-changed",
                      "settings-changed", "downloads-changed"):
            ctx.connect(event, self.reload)
        self.widget.connect("map", lambda _w: self.reload())

    # -- data ---------------------------------------------------------------

    def reload(self) -> None:
        ctx = self.ctx
        if not ctx.started:
            return

        def _read():
            downloads = ctx.downloads
            storage = downloads.storage_state() if downloads else None
            wifi = downloads.wifi_only() if downloads else True
            return prefs_module.load(), storage, wifi

        def _done(result, error) -> None:
            if error is None and result is not None:
                self.prefs, self.storage, self.wifi_only = result
                self.loaded = True
                self._render()
                if ctx.signed_in and ctx.is_admin and self.status is None \
                        and not self.status_loading:
                    self.refresh_status()

        ctx.run(_read, _done)

    def refresh_status(self) -> None:
        self.status_loading = True
        self._render()

        def _done(status, error) -> None:
            self.status_loading = False
            if error is None:
                self.status, self.status_error = status, None
            else:
                from xx_audiobook.ui.context import error_line

                self.status_error = error_line(error)
            self._render()

        self.ctx.run(self.ctx.api.status, _done)

    # -- render -------------------------------------------------------------

    def _render(self) -> None:
        body = self.body
        common.clear(body)
        ctx = self.ctx
        if not ctx.started:
            body.append(common.muted("Checking your sign-in…"))
            return
        if not ctx.signed_in:
            self._sign_in(body)
            return
        self._account(body)
        body.append(common.section("Listening"))
        body.append(widgets.list_row(
            "Listening stats", "Time, streaks and recent sessions",
            trailing=widgets.chevron(), on_activate=self.nav.stats,
            last=True))
        if ctx.is_admin:
            self._server(body)
        self._playback(body)
        self._downloads(body)
        body.append(common.section("Theme"))
        body.append(widgets.setting_row(
            self._theme_name(), "Follows the shell theme (XX-WM)",
            last=True))
        body.append(common.section("Support"))
        share = widgets.list_row(
            "Share logs", "Last 100 KB, redacted",
            trailing=common.glyph("edit-copy-symbolic", 18),
            on_activate=self.share_logs, last=True)
        common.name(share, "Share logs: copy the last 100 KB, redacted")
        body.append(share)
        if self.share_line:
            note = common.para(self.share_line, "xx-ab-meta")
            note.set_margin_top(4)
            body.append(note)
        out = widgets.outline_button("Sign out", self.ask_sign_out)
        out.get_child().add_css_class("xx-danger-text")
        common.fit(out, 20)
        out.set_margin_top(28)
        body.append(out)
        body.append(widgets.about_content(
            APP_NAME, __version__, license_entries(), self.nav.licenses))

    def _theme_name(self) -> str:
        try:
            import xx_ui

            key = xx_ui.load().key
        except Exception:
            return "AMOLED"
        try:
            from xx_ui.theme import THEME_PRESETS

            return THEME_PRESETS[key].name
        except (KeyError, ImportError):
            return key.capitalize()

    def _account(self, body: Gtk.Box) -> None:
        ctx = self.ctx
        account = ctx.account()
        me = ctx.me
        name = ((me.display_name if me else "")
                or (account.display_name if account else "")
                or (me.username if me else "")
                or (account.username if account else "") or "Signed in")
        user = (me.username if me and me.username else
                account.username if account else "")
        origin = host_of(account.origin) if account else ""
        body.append(common.section("Account", first=True))
        row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 14)
        row.add_css_class("xx-setting")
        row.add_css_class("last")
        row.append(widgets.avatar(fmt.initials(name), 44))
        text = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
        text.set_hexpand(True)
        text.set_valign(Gtk.Align.CENTER)
        top = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
        top.append(common.label(name, "xx-title", lines=2, chars=14))
        if ctx.is_admin:
            top.append(common.badge("Admin"))
        text.append(top)
        sub = "  ·  ".join(p for p in (user, origin) if p)
        if sub:
            text.append(common.label(sub, "xx-secondary", chars=24))
        row.append(text)
        body.append(row)
        line = common.para(source_line(account), "xx-ab-meta")
        if account is not None:
            body.append(line)

    def _server(self, body: Gtk.Box) -> None:
        body.append(common.section_with(
            "Server status", "view-refresh-symbolic", "Refresh status",
            self.refresh_status))
        status = self.status
        if status is None:
            body.append(common.muted(
                self.status_error or ("Checking the server…"
                                      if self.status_loading else "")))
            return
        books = f"{status.items:,} books" if status.items is not None else ""
        if status.items_missing:
            books += f" · {status.items_missing} missing"
        head_line = " ".join(p for p in (status.service or "Server",
                                         status.version) if p)
        rows = [(True, head_line, books)]
        for root in status.media_roots:
            count = (f" · {root.item_count:,} books"
                     if root.item_count is not None else "")
            rows.append((root.present, "Media folder",
                         root.path + (count if root.present
                                      else " · missing")))
        for key, sub in sorted(status.subsystems.items()):
            good = sub.state.lower() in GOOD_STATES
            more = " — ".join(p for p in (sub.detail, sub.hint) if p)
            rows.append((good, f"{subsystem_name(key)}: "
                         f"{sub.state.replace('_', ' ')}", more))
        for index, (good, title, detail) in enumerate(rows):
            line = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
            line.add_css_class("xx-setting")
            if index == len(rows) - 1:
                line.add_css_class("last")
            line.append(common.dot(good, "Working" if good
                                   else "Not working"))
            text = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
            text.set_hexpand(True)
            text.set_valign(Gtk.Align.CENTER)
            text.append(common.label(title, "xx-title", lines=2, chars=24))
            if detail:
                text.append(common.label(detail, "xx-secondary", lines=2,
                                         chars=28))
            line.append(text)
            body.append(line)
        if self.status_error:
            body.append(common.muted(self.status_error))

    def _playback(self, body: Gtk.Box) -> None:
        prefs = self.prefs
        body.append(common.section("Playback"))
        back = _block("Skip back", "The back button jumps this far.")
        back.append(_chips([
            (f"{s} sec", prefs.skip_back_seconds == s,
             f"Skip back {s} seconds",
             lambda s=s: self.save(skip_back_seconds=s))
            for s in prefs_module.SKIP_CHOICES]))
        body.append(back)
        forward = _block("Skip forward")
        forward.append(_chips([
            (f"{s} sec", prefs.skip_seconds == s,
             f"Skip forward {s} seconds",
             lambda s=s: self.save(skip_seconds=s))
            for s in prefs_module.SKIP_CHOICES]))
        body.append(forward)
        speed = _block("Default speed", "Books you set keep their own.")
        speed.append(_chips([
            (fmt.speed_text(v), abs(prefs.default_speed - v) < 0.01,
             f"Default speed {fmt.speed_text(v)}",
             lambda v=v: self.save(default_speed=v))
            for v in SPEED_CHOICES]))
        body.append(speed)
        rewind = _block("Rewind when resuming",
                        "After a pause of 10 s or more.")
        rewind.append(_chips([
            ("Off" if s == 0 else f"{s} sec",
             prefs.auto_rewind_seconds == s,
             "No rewind" if s == 0 else f"Rewind {s} seconds",
             lambda s=s: self.save(auto_rewind_seconds=s))
            for s in REWIND_CHOICES]))
        body.append(rewind)
        synced = self.sync_line or (
            "Saved on this phone; syncs with the web player when online."
            if prefs.settings_dirty else "Shared with the web player.")
        note = common.para(synced, "xx-ab-meta")
        note.set_margin_top(4)
        body.append(note)

    def _downloads(self, body: Gtk.Box) -> None:
        body.append(common.section("Downloads"))
        switch = widgets.make_switch(self.wifi_only,
                                     self.set_wifi_only)
        switch.update_property([Gtk.AccessibleProperty.LABEL],
                               ["Download on Wi-Fi only"])
        body.append(widgets.setting_row(
            "Wi-Fi only", "Never download on mobile data", trailing=switch))
        storage = self.storage or {}
        used = fmt.size_text(storage.get("used_bytes", 0))
        free = storage.get("free_bytes")
        sub = f"{used} of books" + (f" · {fmt.size_text(free)} free"
                                    if free is not None else "")
        if storage.get("low"):
            sub += " · low"
        body.append(widgets.list_row(
            "Storage", sub, trailing=widgets.chevron(),
            on_activate=self.nav.downloads, last=True))

    def _sign_in(self, body: Gtk.Box) -> None:
        account_message = (self.ctx.session.sign_out_message
                           if self.ctx.session is not None else None)
        where = common.caps("Audiobook · estate account")
        where.set_margin_top(8)
        body.append(where)
        heading = common.label("Sign in", "xx-ab-heading", chars=16)
        heading.set_margin_top(4)
        body.append(heading)
        intro = common.para("Use your estate account: the one xx-apps "
                            "signs in with. Signed in to xx-apps already? "
                            "Try it again below.", "xx-secondary")
        intro.set_margin_top(6)
        body.append(intro)
        if account_message:
            body.append(common.muted(account_message))
        fields = (("origin", "Estate address", "https://estate.example",
                   Gtk.InputPurpose.URL),
                  ("username", "Username", "", Gtk.InputPurpose.FREE_FORM),
                  ("password", "Password", "", Gtk.InputPurpose.PASSWORD))
        for key, caption, placeholder, purpose in fields:
            head = common.caps(caption)
            head.set_margin_top(18)
            head.set_margin_bottom(6)
            body.append(head)
            entry = self._entries.get(key)
            if entry is None:
                entry = deferred(widgets.text_entry(placeholder))
                entry.set_input_purpose(purpose)
                entry.update_property([Gtk.AccessibleProperty.LABEL],
                                      [caption])
                entry.set_hexpand(True)
                entry.connect("activate", lambda _e: self.sign_in())
                if key == "password":
                    entry.set_visibility(False)
                self._entries[key] = entry
            else:
                parent = entry.get_parent()
                if parent is not None:
                    parent.remove(entry)
            if key == "password":
                row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 4)
                row.append(entry)
                shown = entry.get_visibility()
                row.append(common.icon_button(
                    "view-conceal-symbolic" if shown
                    else "view-reveal-symbolic",
                    "Hide password" if shown else "Show password",
                    self._toggle_password))
                body.append(row)
            else:
                body.append(entry)
        if self.sign_in_error:
            error = common.para(self.sign_in_error, "xx-secondary")
            error.add_css_class("xx-danger-text")
            error.set_margin_top(12)
            body.append(error)
        go = widgets.primary_button(
            "Signing in…" if self.busy else "Sign in", self.sign_in)
        go.set_sensitive(not self.busy)
        common.fit(go, 20)
        go.set_margin_top(24)
        body.append(go)
        retry = widgets.outline_button("Try xx-apps again", self.retry_hub)
        common.name(retry, "I signed in to xx-apps — try again")
        retry.set_sensitive(not self.busy)
        common.fit(retry, 26)
        retry.set_margin_top(10)
        body.append(retry)
        foot = common.para("The password goes to the estate once and is "
                           "never stored on this phone.", "xx-ab-meta")
        foot.set_margin_top(16)
        body.append(foot)

    # -- actions ------------------------------------------------------------

    def _toggle_password(self) -> None:
        entry = self._entries.get("password")
        if entry is not None:
            entry.set_visibility(not entry.get_visibility())
            self._render()

    def sign_in(self) -> None:
        if self.busy:
            return
        origin = self._entries["origin"].get_text().strip()
        username = self._entries["username"].get_text().strip()
        password = self._entries["password"].get_text()
        # The password leaves the widget now: it exists only in the
        # login request body from here on.
        self._entries["password"].set_text("")
        self.busy, self.sign_in_error = True, None
        self._render()

        def _done(message) -> None:
            self.busy = False
            self.sign_in_error = message
            self._render()

        self.ctx.sign_in(origin, username, password, _done)

    def retry_hub(self) -> None:
        self.busy, self.sign_in_error = True, None
        self._render()

        def _done(message) -> None:
            self.busy = False
            self.sign_in_error = message
            self._render()

        self.ctx.retry_hub(_done)

    def save(self, **changes) -> None:
        for key, value in changes.items():
            setattr(self.prefs, key, value)
        self.sync_line = "Saved on this phone. Syncing…"
        self._render()

        def _done(synced: bool) -> None:
            self.sync_line = None if synced else (
                "Saved on this phone; syncs with the web player when "
                "online.")
            self.reload()

        self.ctx.save_settings(_done, **changes)

    def set_wifi_only(self, value: bool) -> None:
        downloads = self.ctx.downloads
        if downloads is None:
            return
        self.wifi_only = value
        self.ctx.run(lambda: downloads.set_wifi_only(value),
                     lambda _r, _e: self.ctx.emit("downloads-changed"))

    def share_logs(self) -> None:
        def _work():
            text = applog_module.share_tail()
            return text, write_share_file(text)

        def _done(result, error) -> None:
            if error is not None or result is None:
                self.share_line = "Could not write the logs."
            else:
                text, path = result
                display = Gdk.Display.get_default()
                if display is not None:
                    display.get_clipboard().set_content(
                        Gdk.ContentProvider.new_for_bytes(
                            "text/plain;charset=utf-8",
                            _bytes(text)))
                self.share_line = (
                    f"Copied {fmt.size_text(len(text.encode()))} of "
                    f"redacted logs. Also saved to {_home_short(path)}.")
            self._render()

        self.ctx.run(_work, _done)

    def ask_sign_out(self):
        return ask_sign_out(self.ctx, self._window())


def _bytes(text: str):
    from gi.repository import GLib

    return GLib.Bytes.new(text.encode("utf-8"))


def ask_sign_out(ctx, parent):
    def _answer(response: str) -> None:
        if response == "out":
            ctx.sign_out()

    return common.confirm(
        parent, "Sign out?",
        "Playback stops. Downloads and your place in each book stay on "
        "this phone; unfinished downloads resume when you sign in again.",
        [("stay", "Stay", "outline"), ("out", "Sign out", "danger")],
        _answer)


class NoAccessView:
    """403 not_granted, full screen: Retry asks again, Sign out asks
    first. Never signs out or wipes by itself."""

    def __init__(self, ctx, window_getter) -> None:
        self.ctx = ctx
        self._window = window_getter
        self.busy = False
        self.line: str | None = None
        self.body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self.body.add_css_class("xx-ab-noaccess")
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(self.body)
        self.widget = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self.widget.add_css_class("xx-page")
        self.widget.add_css_class("xx-ab")
        self.widget.append(scroll)
        self.render()
        ctx.connect("account-changed", self.render)

    def render(self) -> None:
        body = self.body
        common.clear(body)
        body.append(common.caps("xx-audiobook"))
        mark = common.glyph("action-unavailable-symbolic", 40)
        mark.set_halign(Gtk.Align.START)
        mark.set_margin_top(32)
        body.append(mark)
        heading = common.label("No Audiobook access", "xx-ab-heading", lines=2,
                               chars=16)
        heading.set_margin_top(16)
        body.append(heading)
        text = common.para(api_module.NOT_GRANTED_MESSAGE, "xx-secondary")
        text.set_margin_top(8)
        body.append(text)
        account = self.ctx.account()
        if account is not None:
            who = account.display_name or account.username or "you"
            line = common.para(
                f"Signed in as {who} · {host_of(account.origin)}.",
                "xx-ab-meta")
            line.set_margin_top(16)
            body.append(line)
        if self.line:
            body.append(common.muted(self.line))
        retry = widgets.primary_button(
            "Checking…" if self.busy else "Retry", self.retry)
        retry.set_sensitive(not self.busy)
        common.fit(retry, 20)
        retry.set_margin_top(32)
        body.append(retry)
        out = widgets.outline_button("Sign out", lambda: ask_sign_out(
            self.ctx, self._window()))
        common.fit(out, 20)
        out.set_margin_top(10)
        body.append(out)

    def retry(self) -> None:
        self.busy, self.line = True, None
        self.render()

        def _done(error) -> None:
            self.busy = False
            if error is not None and self.ctx.access_denied:
                self.line = "Still no access. Ask the admin, then retry."
            self.render()

        self.ctx.recheck_access(_done)
