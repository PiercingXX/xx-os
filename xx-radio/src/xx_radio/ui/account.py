"""Settings › Account, the browser-header sign-in and the OAuth sign-in.

Sign-in is optional: search, radio and lyrics work without it. The
recommended way is a paste of a desktop browser's request headers (or
its Copy as cURL); OAuth with the person's own client is offered as the
advanced way and gives library access only. Every call runs on the
worker through ``xx_radio.account.AccountManager``; the paste is
cleared from the screen once used and never echoed back or logged.
"""

from __future__ import annotations

from gi.repository import Gdk, Gtk

from xx_music.ui import common
from xx_music.ui.settings import nav_row, section
from xx_radio.account import METHOD_OAUTH, AccountState
from xx_radio.youtube import auth
from xx_ui import widgets

STEPS = (
    "On a computer, open music.youtube.com in a browser and sign in. "
    "A spare account is the careful choice.",
    "Open the developer tools (F12), then Network. Click around the page "
    "and pick a request named browse or next.",
    "Copy its request headers, or Copy as cURL, and paste everything "
    "below.",
)
PASTE_HINT = "Paste the headers or the cURL command here"


def sign_in_error(error: BaseException) -> str:
    """One plain sentence per failure; the detail stays out of sight."""
    if isinstance(error, auth.KeyringUnavailable):
        return "Signing in needs a keyring, and none is running."
    if isinstance(error, auth.CredentialsExpired):
        return "That sign-in has expired. Copy fresh headers."
    if isinstance(error, auth.CredentialsError):
        return "That paste has no sign-in cookie. Copy the request " \
               "headers again."
    if isinstance(error, auth.ValidationError):
        return "YouTube didn't accept that sign-in. Copy fresh headers."
    if isinstance(error, auth.OAuthDenied):
        return "The sign-in was declined."
    if isinstance(error, auth.BadOAuthClient):
        return "Google didn't accept that client id and secret."
    return "Couldn't sign in. Check the connection and try again."


def _method_line(state: AccountState) -> str:
    if state.method == METHOD_OAUTH:
        return "Your own OAuth client: library and likes; songs stream " \
               "without the account"
    return "Signed in with browser headers"


class AccountSection:
    """The Account block at the top of Settings."""

    def __init__(self, ui, manager) -> None:
        self.ui = ui
        self.manager = manager
        self.state: AccountState | None = None
        self.widget = common.vbox(0)
        self.widget.append(section("Account"))
        self.slot = common.vbox(0)
        self.widget.append(self.slot)
        self.slot.append(widgets.setting_row("Account", "Checking…",
                                             last=True))
        ui.on_account(self._from_ui)
        self.refresh()

    def refresh(self) -> None:
        self.ui.io(self.manager.state, self.show,
                   lambda _e: self.show(AccountState(False)))

    def _from_ui(self) -> None:
        if self.state is not None and \
                self.state.signed_in != self.ui.signed_in:
            self.refresh()

    def show(self, state: AccountState) -> None:
        self.state = state
        self.ui.set_account(state.signed_in, state.account_name)
        slot = self.slot
        common.clear(slot)
        if not state.signed_in:
            slot.append(common.para(
                "Not signed in. Search, radio and lyrics work without an "
                "account; signing in adds your library and likes.",
                "xx-secondary"))
            button = widgets.primary_button("Sign in", self.open_sign_in)
            common.fit(button, 12)
            button.set_halign(Gtk.Align.START)
            button.set_margin_top(12)
            button.set_margin_bottom(4)
            slot.append(button)
            return
        rows = [(state.account_name or "Signed in", _method_line(state))]
        if state.brand_user:
            rows.append(("Brand account", state.brand_user))
        for title, sub in rows:
            row = widgets.setting_row(title, sub)
            _wrap(row)
            slot.append(row)
        out = widgets.outline_button("Sign out", self.confirm_sign_out)
        common.fit(out, 12)
        out.set_halign(Gtk.Align.START)
        out.set_margin_top(12)
        slot.append(out)

    def open_sign_in(self) -> None:
        page = SignInPage(self.ui, self.manager, self.show)
        self.ui.shell.push(page.widget, "Sign in")

    def confirm_sign_out(self) -> None:
        def answer(response: str) -> None:
            if response != "out":
                return
            self.ui.io(self.manager.sign_out, self.show,
                       lambda _e: common.toast(self.ui,
                                               "Couldn't sign out."))

        common.confirm(
            self.ui.shell.window, "Sign out?",
            "Your library and likes leave this phone. Downloads stay.",
            [("keep", "Cancel", "outline"), ("out", "Sign out", "danger")],
            answer)


def _wrap(row: Gtk.Widget) -> None:
    middle = row.get_first_child()
    child = middle.get_first_child() if middle else None
    while child is not None:
        if isinstance(child, Gtk.Label) and child.has_css_class(
                "xx-secondary"):
            child.set_wrap(True)
            child.set_lines(3)
            child.set_max_width_chars(28)
        child = child.get_next_sibling()


def _numbered(index: int, text: str) -> Gtk.Widget:
    row = common.hbox(14)
    row.set_margin_top(10)
    num = Gtk.Label(label=str(index))
    num.add_css_class("xx-mu-stepnum")
    num.add_css_class("now")
    num.set_size_request(28, 28)
    num.set_valign(Gtk.Align.START)
    row.append(num)
    line = common.para(text, "xx-body", 28)
    line.set_hexpand(True)
    row.append(line)
    return row


class PasteBox:
    """A multi-line paste field on ``surface``; focus only on tap."""

    def __init__(self) -> None:
        self.view = Gtk.TextView()
        self.view.add_css_class("xx-mu-paste")
        self.view.set_wrap_mode(Gtk.WrapMode.CHAR)
        self.view.set_accepts_tab(False)
        self.view.set_can_focus(False)
        self.view.set_monospace(True)
        self.view.update_property([Gtk.AccessibleProperty.LABEL],
                                  ["Request headers"])
        tap = Gtk.GestureClick.new()
        tap.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        tap.connect("pressed", lambda *_a: self._focus())
        self.view.add_controller(tap)
        self.hint = common.label(PASTE_HINT, "xx-secondary", lines=2,
                                 chars=26)
        self.hint.set_can_target(False)
        self.hint.set_valign(Gtk.Align.START)
        overlay = Gtk.Overlay()
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(112)
        scroll.set_max_content_height(160)
        scroll.set_child(self.view)
        overlay.set_child(scroll)
        overlay.add_overlay(self.hint)
        self.widget = common.vbox(0)
        self.widget.add_css_class("xx-mu-paste-box")
        self.widget.append(overlay)
        self.view.get_buffer().connect("changed", lambda _b: self._sync())

    def _focus(self) -> None:
        self.view.set_can_focus(True)
        self.view.grab_focus()

    def _sync(self) -> None:
        self.hint.set_visible(not self.text())

    def text(self) -> str:
        buffer = self.view.get_buffer()
        return buffer.get_text(buffer.get_start_iter(),
                               buffer.get_end_iter(), False)

    def set_text(self, text: str) -> None:
        self.view.get_buffer().set_text(text)

    def summary(self, text: str) -> None:
        """Show that something was pasted without echoing it."""
        lines = len(text.splitlines()) or 1
        self.hint.set_text(f"Pasted {lines} lines. Tap Sign in.")


class SignInPage:
    def __init__(self, ui, manager, on_signed_in) -> None:
        self.ui = ui
        self.manager = manager
        self.on_signed_in = on_signed_in
        self._pasted = ""
        self.loading = common.LoadingLine()
        body = common.vbox(0)
        body.add_css_class("xx-page-body")
        body.set_margin_bottom(32)
        head = common.label("Sign in with your browser", "xx-mu-h2",
                            lines=2, chars=16)
        head.set_margin_top(8)
        body.append(head)
        intro = common.para(
            "XX-Radio never sees your password. It borrows the sign-in of "
            "a browser session you copy here.", "xx-secondary")
        intro.set_margin_top(8)
        body.append(intro)
        for index, text in enumerate(STEPS, 1):
            body.append(_numbered(index, text))
        self.paste = PasteBox()
        self.paste.widget.set_margin_top(18)
        body.append(self.paste.widget)
        buttons = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 10)
        buttons.set_homogeneous(True)
        buttons.set_margin_top(12)
        paste = widgets.outline_button("Paste", self.paste_clipboard)
        self.go = widgets.primary_button("Sign in", self.sign_in)
        for button in (paste, self.go):
            common.fit(button, 10)
            buttons.append(button)
        body.append(buttons)
        self.result = common.para("", "xx-secondary")
        self.result.set_margin_top(10)
        self.result.set_visible(False)
        body.append(self.result)
        body.append(section("Advanced"))
        body.append(nav_row(
            "Use your own OAuth client",
            "Library and likes only. Needs a client from Google Cloud.",
            self.open_oauth, last=True))
        root = common.vbox(0)
        root.add_css_class("xx-mu")
        root.append(common.header(ui.shell.pop, "Sign in"))
        root.append(self.loading)
        root.append(common.scroller(body))
        self.widget = root
        root.xx_page = self

    def paste_clipboard(self) -> None:
        clipboard = Gdk.Display.get_default().get_clipboard()

        def done(source, result) -> None:
            try:
                text = source.read_text_finish(result) or ""
            except Exception:  # noqa: BLE001 - empty or not text
                text = ""
            if not text.strip():
                self._say("The clipboard is empty.", error=True)
                return
            self.paste.set_text(text)
            self._say("")

        clipboard.read_text_async(None, done)

    def _say(self, text: str, error: bool = False) -> None:
        self.result.set_text(text)
        self.result.set_visible(bool(text))
        if error:
            self.result.add_css_class("xx-mu-error")
        else:
            self.result.remove_css_class("xx-mu-error")

    def sign_in(self) -> None:
        text = self.paste.text()
        if not text.strip():
            self._say("Paste the request headers first.", error=True)
            return
        self.paste.set_text("")
        self.paste.summary(text)
        self.go.set_sensitive(False)
        self.loading.set_active(True)
        self._say("Checking with YouTube…")
        self.ui.io(lambda: self.manager.sign_in_with_headers(text),
                   self._done, self._failed)

    def _done(self, state: AccountState) -> None:
        self.loading.set_active(False)
        self.go.set_sensitive(True)
        self.on_signed_in(state)
        self.ui.shell.pop()
        common.toast(self.ui, f"Signed in as {state.account_name}")

    def _failed(self, error) -> None:
        self.loading.set_active(False)
        self.go.set_sensitive(True)
        self.paste.hint.set_text(PASTE_HINT)
        self._say(sign_in_error(error), error=True)

    def open_oauth(self) -> None:
        page = OAuthPage(self.ui, self.manager, self.on_signed_in)
        self.ui.shell.push(page.widget, "OAuth")


class OAuthPage:
    """Advanced: ytmusicapi's device flow with the person's own client."""

    def __init__(self, ui, manager, on_signed_in) -> None:
        self.ui = ui
        self.manager = manager
        self.on_signed_in = on_signed_in
        self.loading = common.LoadingLine()
        body = common.vbox(0)
        body.add_css_class("xx-page-body")
        body.set_margin_bottom(32)
        head = common.label("Your own OAuth client", "xx-mu-h2", lines=2,
                            chars=16)
        head.set_margin_top(8)
        body.append(head)
        intro = common.para(
            "Create an OAuth client of type TVs and Limited Input in Google "
            "Cloud with the YouTube Data API on, then enter it here. This "
            "gives your library and likes; songs still stream without the "
            "account.", "xx-secondary")
        intro.set_margin_top(8)
        body.append(intro)
        self.client_id = widgets.deferred_entry("Client ID")
        self.client_secret = widgets.deferred_entry("Client secret")
        self.client_secret.set_visibility(False)
        for caption, entry in (("Client ID", self.client_id),
                               ("Client secret", self.client_secret)):
            label = common.caps(caption)
            label.set_margin_top(16)
            label.set_margin_bottom(6)
            body.append(label)
            entry.set_hexpand(True)
            entry.update_property([Gtk.AccessibleProperty.LABEL], [caption])
            body.append(entry)
        self.go = widgets.primary_button("Get a code", self.start)
        common.fit(self.go, 14)
        self.go.set_margin_top(16)
        body.append(self.go)
        self.code_box = common.vbox(6)
        self.code_box.set_margin_top(20)
        body.append(self.code_box)
        root = common.vbox(0)
        root.add_css_class("xx-mu")
        root.append(common.header(ui.shell.pop, "OAuth"))
        root.append(self.loading)
        root.append(common.scroller(body))
        self.widget = root
        root.xx_page = self

    def start(self) -> None:
        client_id = self.client_id.get_text().strip()
        secret = self.client_secret.get_text().strip()
        if not client_id or not secret:
            self._note("Enter your client id and secret first.", True)
            return
        self.go.set_sensitive(False)
        self.loading.set_active(True)

        def work():
            self.manager.remember_oauth_client(client_id, secret)
            return self.manager.start_oauth(client_id, secret)

        self.ui.io(work, lambda code: self.show_code(code, client_id,
                                                       secret),
                   self._failed)

    def show_code(self, code, client_id: str, secret: str) -> None:
        self.go.set_visible(False)
        common.clear(self.code_box)
        block = common.vbox(6)
        block.add_css_class("xx-mu-block")
        block.append(common.caps("Enter this code"))
        block.append(common.label(code.user_code, "xx-mu-code xx-mu-num",
                                  chars=14))
        url = code.verification_url.replace("https://", "")
        block.append(common.para(f"at {url} on any device signed in to "
                                 "the account.", "xx-secondary"))
        waiting = common.hbox(10)
        waiting.set_margin_top(6)
        waiting.append(common.busy_square())
        waiting.append(common.label("Waiting for you to approve…",
                                    "xx-mu-meta", chars=30))
        block.append(waiting)
        self.code_box.append(block)
        self.ui.io(lambda: self.manager.finish_oauth(client_id, secret,
                                                     code),
                   self._done, self._failed)

    def _done(self, state: AccountState) -> None:
        self.loading.set_active(False)
        self.on_signed_in(state)
        self.ui.shell.current_nav().pop_to_tag("settings")
        common.toast(self.ui, "Signed in")

    def _failed(self, error) -> None:
        self.loading.set_active(False)
        self.go.set_sensitive(True)
        self.go.set_visible(True)
        common.clear(self.code_box)
        self._note(sign_in_error(error), True)

    def _note(self, text: str, error: bool = False) -> None:
        common.clear(self.code_box)
        line = common.para(text, "xx-secondary")
        if error:
            line.add_css_class("xx-mu-error")
        self.code_box.append(line)
