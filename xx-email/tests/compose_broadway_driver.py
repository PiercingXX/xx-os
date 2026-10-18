"""Broadway driver for the compose smoke test (not collected by pytest).

Runs one scenario against a real GTK display (the parent pytest
hands it GDK_BACKEND=broadway plus BROADWAY_DISPLAY) and prints
KEY: value markers on stdout. Any unexpected exception exits
nonzero so the parent test fails instead of passing on empty
output. Seeded data lives in the XDG store the parent prepared.
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "common"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from xx_testing.broadway_wait import pump_until as _wait_until  # noqa: E402

Gtk.init()
opened = Gdk.Display.open(os.environ.get("BROADWAY_DISPLAY", ""))
assert opened is not None, "could not open the Broadway display"
Adw.init()

from xx_email import bodies as bodies_mod  # noqa: E402
from xx_email import compose as compose_mod  # noqa: E402
from xx_email import db as db_mod  # noqa: E402
from xx_email import mailbox as mailbox_mod  # noqa: E402
from xx_email import setup as setup_mod  # noqa: E402
from xx_email.ui import compose_page as compose_page_mod  # noqa: E402
from xx_email.ui import mailbox_page as mailbox_page_mod  # noqa: E402
from xx_email.ui import search_page as search_page_mod  # noqa: E402
from xx_email.ui import thread_page as thread_page_mod  # noqa: E402

ERRORS: list[str] = []

ACCOUNT = "user@dev.xxemail"


def _hook_errors() -> None:
    def _collect(exc_type, exc, tb) -> None:
        ERRORS.append("".join(
            traceback.format_exception(exc_type, exc, tb)))

    sys.excepthook = _collect


def _open_db():
    setup_mod.ensure_app_dirs()
    from xx_email import config as config_mod
    return db_mod.open_db(config_mod.data_dir() / db_mod.DB_FILE_NAME)


class _StubHooks:
    """Synchronous worker stand-ins serving the seeded store."""

    def load_folder(self, folder, done) -> None:
        conn = _open_db()
        try:
            done((mailbox_mod.threads_for_folder(conn, ACCOUNT,
                                                 folder), False))
        finally:
            conn.close()

    def load_outbox(self, done) -> None:
        conn = _open_db()
        try:
            done((mailbox_mod.outbox_for_folder(conn, ACCOUNT),
                   False))
        finally:
            conn.close()

    def load_accounts(self, done) -> None:
        done([ACCOUNT])

    def refresh(self, done) -> None:
        done({"ok": True, "new_ids": []})

    def load_more(self, folder, done) -> None:
        _ = folder
        done(([], False))

    def failed_count(self, done) -> None:
        done(0)

    def perform(self, *args) -> None:
        done = args[-1]
        done(type("Undo", (), {"message": "Marked read"})())

    def revert(self, undo, done) -> None:
        _ = undo
        done(True)

    def load_thread(self, done) -> None:
        conn = _open_db()
        try:
            thread = db_mod.get_thread(conn, ACCOUNT, "t-seed-1")
            messages = db_mod.messages_for_thread(conn, ACCOUNT,
                                                  "t-seed-1")
            entries = [{
                "message_id": m.id, "html": m.body_html,
                "plain": m.body_plain,
                "attachments": bodies_mod.decode_attachments(
                    m.attachments_json)} for m in messages]
            done((thread, messages, entries))
        finally:
            conn.close()

    def search(self, query, done) -> None:
        _ = query
        done(([], False, False))

    def save_draft(self, fields) -> None:
        _ = fields

    def stage_upload(self, path, name, done) -> None:
        _ = (path, name)
        done(Exception("not in smoke"))

    def remove_staged(self, path) -> None:
        _ = path

    def fetch_offered(self, done) -> None:
        done([])

    def decline_offer(self) -> None:
        pass

    def send(self, fields, staged, scheduled_at, done) -> None:
        _ = (fields, staged, scheduled_at)
        done((1, 10, False))

    def undo_send(self, entry_id, done) -> None:
        _ = entry_id
        done(True)

    def discard(self) -> None:
        pass

    def after_sent(self) -> None:
        pass

    def mark_read(self) -> None:
        pass

    def open_reply(self, mode, subject, quote) -> None:
        _ = (mode, subject, quote)

    def open_attachment(self, attachment) -> None:
        _ = attachment

    def thread_undone(self, undo) -> None:
        _ = undo

    def go_back(self) -> None:
        pass

    def open_thread(self, thread_id) -> None:
        _ = thread_id

    def open_search(self) -> None:
        pass

    def open_settings(self) -> None:
        pass

    def open_signin(self) -> None:
        pass

    def open_compose(self) -> None:
        pass

    def open_permissions(self) -> None:
        pass

    def switch_account(self, email) -> None:
        _ = email

    def add_account(self) -> None:
        pass

    def sign_out(self, email, revoke) -> None:
        _ = (email, revoke)

    def retry_failed(self) -> None:
        pass


def _toast(message, on_undo) -> None:
    _ = (message, on_undo)


def _subtree_focused(widget: Gtk.Widget) -> bool:
    if widget.has_focus():
        return True
    child = widget.get_first_child()
    while child is not None:
        if _subtree_focused(child):
            return True
        child = child.get_next_sibling()
    return False


def _entry_focused(widget: Gtk.Widget) -> bool:
    """True when any text entry under widget holds keyboard focus.

    Buttons may take window focus — only entries pop the on-screen
    keyboard, so only entries count here.
    """
    if isinstance(widget, (Gtk.Entry, Gtk.TextView)) and (
            widget.has_focus() or _subtree_focused(widget)):
        return True
    child = widget.get_first_child()
    while child is not None:
        if _entry_focused(child):
            return True
        child = child.get_next_sibling()
    return False


def _focus_inside(window: Gtk.Window, widget: Gtk.Widget) -> bool:
    """True when the window's focus sits inside widget's subtree.

    GtkEntry holds an inner GtkText that owns keyboard focus, so the
    entry itself never reports has_focus — walk up from the focused
    widget instead.
    """
    node = window.get_focus()
    while node is not None:
        if node is widget:
            return True
        node = node.get_parent()
    return _subtree_focused(widget)


def _scenario_smoke() -> None:
    hooks = _StubHooks()
    conn = _open_db()
    try:
        row = db_mod.get_message(conn, ACCOUNT, "m-seed-1")
        assert row is not None, "seed message missing"
        original = compose_mod.OriginalMessage(
            id=row.id, thread_id=row.thread_id, subject=row.subject,
            from_address=row.from_address, to_csv=row.to_csv,
            cc_csv=row.cc_csv, date=row.date, snippet=row.snippet,
            body_plain=row.body_plain,
            message_id_header=row.message_id_header,
            has_attachments=row.has_attachments)
        reply = compose_mod.prefill_for_mode(ACCOUNT, "reply",
                                             original)
    finally:
        conn.close()

    def _show(page, title: str) -> Gtk.Window:
        window = Gtk.Window()
        window.set_title(title)
        window.set_default_size(360, 720)
        window.set_child(page)
        window.present()
        return window

    def _build_compose(initial: dict, autofocus: bool,
                       marker: str | None = None):
        page = compose_page_mod.ComposePage(
            ACCOUNT, initial, hooks, _toast,
            autofocus_to=autofocus)
        if marker is not None:
            print(f"{marker}:", page._to.get_text())
        return page

    reply_initial = {"to": reply.to, "cc": reply.cc, "bcc": "",
                     "subject": reply.subject, "body": reply.body,
                     "thread_id": reply.thread_id or "",
                     "quote_id": "m-seed-1", "mode": "reply",
                     "in_reply_to": reply.in_reply_to or "",
                     "references": reply.references or "",
                     "offer_count": 0, "show_cc_bcc": False}
    new_initial = {"to": "", "cc": "", "bcc": "", "subject": "",
                   "body": "", "thread_id": "", "quote_id": "",
                   "mode": "new", "in_reply_to": "",
                   "references": "", "offer_count": 0,
                   "show_cc_bcc": False}
    mailbox = mailbox_page_mod.MailboxPage(ACCOUNT, hooks, _toast)
    thread = thread_page_mod.ThreadPage(ACCOUNT, "t-seed-1",
                                        hooks, _toast)
    print("MAILBOX_TABS:", len(mailbox._tabs))
    print("REPLY_REFS:", "yes" if reply.references else "no")
    mailbox_window = _show(mailbox, "Mailbox")
    thread_window = _show(thread, "Thread")
    mailbox.load()
    if not _wait_until(mailbox_window.get_mapped, timeout_s=10.0):
        raise AssertionError("email compose: mailbox never mapped")
    mailbox._on_refreshed({"ok": True, "reauth": True, "new_ids": []})
    if not _wait_until(mailbox._reauth_banner.get_visible,
                       timeout_s=10.0):
        raise AssertionError("email compose: reauth banner never shown")
    print("REAUTH_BANNER:", mailbox._reauth_banner.get_visible())
    print("REAUTH_CAPTION_EMPTY:", mailbox._sync_line.get_text() == "")
    mailbox._on_refreshed({"ok": True, "reauth": False, "new_ids": []})
    if not _wait_until(lambda: not mailbox._reauth_banner.get_visible(),
                       timeout_s=10.0):
        raise AssertionError("email compose: reauth banner never cleared")
    search = search_page_mod.SearchPage(ACCOUNT, hooks, _toast)
    # No "Try" list: the search field and its placeholder are the prompt.
    print("HINT_ROWS:", 0 if not hasattr(search, "_hints") else -1)
    mailbox_window.destroy()
    thread_window.destroy()
    reply_page = _build_compose(reply_initial, False, "REPLY_TO")
    reply_window = _show(reply_page, "Reply")
    if not _wait_until(reply_window.get_mapped, timeout_s=10.0):
        raise AssertionError("email compose: reply never mapped")
    print("REPLY_ANY_FOCUSED:", _subtree_focused(reply_page))
    print("REPLY_ENTRY_FOCUSED:", _entry_focused(reply_page))
    reply_window.destroy()
    new_page = _build_compose(new_initial, True)
    new_window = _show(new_page, "Compose")
    if not _wait_until(new_window.get_mapped, timeout_s=10.0):
        raise AssertionError("email compose: compose never mapped")
    print("NEW_TO_FOCUSED:", _focus_inside(new_window,
                                           new_page._to))
    new_window.destroy()
    print("PAGES_OK: mailbox compose reply thread")


def main(argv: list[str]) -> int:
    _hook_errors()
    if argv != ["smoke"]:
        print(f"unknown scenario: {argv}")
        return 2
    _scenario_smoke()
    if ERRORS:
        sys.stderr.write(ERRORS[0][-2000:])
        return 1
    print("ERRORS: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
