"""Setup and No access (ui/setup/SetupScreen.kt,
ui/login/NoAccessScreen.kt). Sign in itself lives in ui/signin.py: it
covers the whole window before the tabs exist.

Setup: the account and its origin (an admin tag for admins), Listening
stats, the server status rows (admins), playback prefs synced to the
account (skip back / forward 10/15/30, default speed, rewind on resume)
as FilterChip rows, Wi-Fi-only downloads and storage, the theme line (it
follows the shell: no picker here), Share logs (the last 100 KB,
redacted), Sign out (asks first) and About + licenses.

No access is the full-screen 403 not_granted state with Retry and Sign
out; it never signs out, wipes or retries by itself.
"""

from __future__ import annotations

import os

from gi.repository import Gdk, Gtk

from xx_audiobook import __version__, fmt
from xx_audiobook import api as api_module
from xx_audiobook import applog as applog_module
from xx_audiobook import paths as paths_module
from xx_audiobook import prefs as prefs_module
from xx_audiobook.ui import common
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
    return f"Signed in via {via}."


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
    """A FlowRow of FilterChips: (text, selected, spoken, callback)."""
    return common.chip_flow([(text, selected, callback, spoken)
                             for text, selected, spoken, callback in options])


def _choice(title: str) -> Gtk.Label:
    head = common.label(title, "xx-ab-b13", chars=28)
    head.set_margin_top(6)
    return head


def _full(button: Gtk.Button, chars: int = 24) -> Gtk.Button:
    common.fit(button, chars)
    button.set_hexpand(True)
    return button


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
        self.loaded = False
        self.body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 4)
        self.body.set_margin_bottom(24)
        self.widget = widgets.page(common.page_top("Setup"), self.body)
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
            from xx_audiobook.ui.context import NOT_SIGNED_IN_LINE

            body.append(common.muted(NOT_SIGNED_IN_LINE))
            return
        self._account(body)
        stats = _full(widgets.outline_button("Listening stats",
                                             self.nav.stats))
        stats.set_margin_top(8)
        body.append(stats)
        if ctx.is_admin:
            self._server(body)
        self._playback(body)
        self._downloads(body)
        body.append(common.section("Theme"))
        body.append(common.para(
            f"{self._theme_name()} — follows the shell (XX-WM).",
            "xx-ab-b11 xx-ab-muted"))
        body.append(common.section("Support"))
        share = _full(widgets.outline_button("Share logs", self.share_logs))
        common.name(share, "Share logs: copy the last 100 KB, redacted")
        body.append(share)
        if self.share_line:
            body.append(common.para(self.share_line,
                                    "xx-ab-b11 xx-ab-muted"))
        body.append(_full(widgets.outline_button("Sign out",
                                                 self.ask_sign_out)))
        self._about(body)

    def _about(self, body: Gtk.Box) -> None:
        body.append(common.section("About"))
        mark = Gtk.Label()
        mark.set_markup("<u>XX</u> audiobook")
        mark.add_css_class("xx-ab-year")
        mark.add_css_class("xx-display-face")
        mark.set_xalign(0.0)
        mark.update_property([Gtk.AccessibleProperty.LABEL], [APP_NAME])
        body.append(mark)
        body.append(common.label(f"Version {__version__}",
                                 "xx-ab-b11 xx-ab-muted", chars=28))
        entries = license_entries()
        spdx = entries[0].spdx if entries else ""
        body.append(common.label(f"License {spdx}", "xx-ab-l10 xx-ab-muted",
                                 chars=28))
        licenses = widgets.text_button("Open-source licenses",
                                       self.nav.licenses)
        common.fit(licenses, 24)
        licenses.add_css_class("xx-ab-flush")  # on the text's left edge
        licenses.set_halign(Gtk.Align.START)
        body.append(licenses)

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
        body.append(common.section("Account", first=True))
        top = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
        top.append(common.label(name, "xx-ab-t16", lines=2, chars=20))
        if ctx.is_admin:
            top.append(common.badge("admin"))
        body.append(top)
        if account is not None:
            body.append(common.label(account.origin, "xx-ab-b11 xx-ab-muted",
                                     chars=34))
            body.append(common.para(source_line(account),
                                    "xx-ab-l10 xx-ab-muted"))

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
        books = fmt.books_count(status.items) if status.items is not None else ""
        if status.items_missing:
            books += f" · {status.items_missing} missing"
        head_line = " ".join(p for p in (status.service or "Server",
                                         status.version) if p)
        body.append(common.para(" · ".join(p for p in (head_line, books)
                                           if p), "xx-ab-b13"))
        rows = []
        for root in status.media_roots:
            count = (f" · {fmt.books_count(root.item_count)}"
                     if root.item_count is not None else "")
            rows.append((root.present, "Media folder",
                         root.path + (count if root.present
                                      else " · missing")))
        for key, sub in sorted(status.subsystems.items()):
            good = sub.state.lower() in GOOD_STATES
            more = " — ".join(p for p in (sub.detail, sub.hint) if p)
            rows.append((good, f"{subsystem_name(key)}: "
                         f"{sub.state.replace('_', ' ')}", more))
        for good, title, detail in rows:
            line = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
            line.set_margin_top(4)
            line.append(common.dot(good, "Working" if good
                                   else "Not working"))
            text = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
            text.set_hexpand(True)
            text.append(common.label(title, "xx-ab-b13", lines=2, chars=28))
            if detail:
                text.append(common.label(detail, "xx-ab-b11 xx-ab-muted",
                                         lines=2, chars=34))
            line.append(text)
            body.append(line)
        if self.status_error:
            body.append(common.muted(self.status_error))

    def _playback(self, body: Gtk.Box) -> None:
        prefs = self.prefs
        body.append(common.section("Playback"))
        body.append(_choice("Skip back"))
        body.append(_chips([
            (f"{s} s", prefs.skip_back_seconds == s,
             f"Skip back {s} seconds",
             lambda s=s: self.save(skip_back_seconds=s))
            for s in prefs_module.SKIP_CHOICES]))
        body.append(_choice("Skip forward"))
        body.append(_chips([
            (f"{s} s", prefs.skip_seconds == s,
             f"Skip forward {s} seconds",
             lambda s=s: self.save(skip_seconds=s))
            for s in prefs_module.SKIP_CHOICES]))
        body.append(_choice("Default speed"))
        body.append(_chips([
            (f"{v:.2f}×", abs(prefs.default_speed - v) < 0.01,
             f"Default speed {fmt.speed_text(v)}",
             lambda v=v: self.save(default_speed=v))
            for v in SPEED_CHOICES]))
        body.append(_choice("Rewind when resuming"))
        body.append(_chips([
            ("Off" if s == 0 else f"{s} s",
             prefs.auto_rewind_seconds == s,
             "No rewind" if s == 0 else f"Rewind {s} seconds",
             lambda s=s: self.save(auto_rewind_seconds=s))
            for s in REWIND_CHOICES]))
        synced = self.sync_line or (
            "Saved on this phone; syncs with the web player when online."
            if prefs.settings_dirty else "Shared with the web player.")
        body.append(common.para(synced, "xx-ab-l10 xx-ab-muted"))

    def _downloads(self, body: Gtk.Box) -> None:
        body.append(common.section("Downloads"))
        check = Gtk.CheckButton()
        check.set_active(self.wifi_only)
        check.set_valign(Gtk.Align.CENTER)
        check.set_can_target(False)
        check.set_focusable(False)
        row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
        text = common.label("Download on Wi-Fi only", "xx-ab-b15", chars=24)
        text.set_hexpand(True)
        row.append(text)
        row.append(check)
        toggle = common.flat(row, "Download on Wi-Fi only",
                             lambda: self.set_wifi_only(not self.wifi_only))
        toggle.update_property([Gtk.AccessibleProperty.LABEL], [
            "Download on Wi-Fi only, " + ("on" if self.wifi_only
                                          else "off")])
        body.append(toggle)
        storage = self.storage or {}
        used = fmt.size_text(storage.get("used_bytes", 0))
        free = storage.get("free_bytes")
        line = f"Storage used: {used}" + (f" · {fmt.size_text(free)} free"
                                          if free is not None else "")
        if storage.get("low"):
            line += " · low"
        body.append(common.para(line, "xx-ab-b11 xx-ab-muted"))

    # -- actions ------------------------------------------------------------

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
        self._render()
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
        [("stay", "Cancel", "outline"), ("out", "Sign out", "danger")],
        _answer)


class NoAccessView:
    """403 not_granted, full screen: Retry asks again, Sign out asks
    first. Never signs out or wipes by itself."""

    def __init__(self, ctx, window_getter) -> None:
        self.ctx = ctx
        self._window = window_getter
        self.busy = False
        self.line: str | None = None
        self.body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)
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
        heading = common.label("No Audiobook access", "xx-ab-screen",
                               lines=2, chars=16)
        body.append(heading)
        body.append(common.para(api_module.NOT_GRANTED_MESSAGE, "xx-ab-b15"))
        account = self.ctx.account()
        if account is not None:
            who = account.display_name or account.username or "you"
            body.append(common.para(f"Signed in as {who}.",
                                    "xx-ab-b11 xx-ab-muted"))
        if self.line:
            body.append(common.para(self.line, "xx-ab-b13 xx-ab-muted"))
        retry = widgets.primary_button(
            "Checking…" if self.busy else "Retry", self.retry)
        retry.set_sensitive(not self.busy)
        common.fit(retry, 20)
        body.append(retry)
        out = widgets.outline_button("Sign out", lambda: ask_sign_out(
            self.ctx, self._window()))
        out.set_sensitive(not self.busy)
        common.fit(out, 20)
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
