"""Broadway driver for the milestone 9 audit (not collected by pytest).

Opens every page against a seeded realistic store (including
Android-style millisecond ids and long unbroken strings) and prints
MIN/ALLOC markers per surface. Any unexpected exception exits
nonzero so the parent test fails instead of passing on empty
output. Minimum widths above 360 px mean horizontal overflow on the
phone canvas.
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

import xx_ui  # noqa: E402

Gtk.init()
opened = Gdk.Display.open(os.environ.get("BROADWAY_DISPLAY", ""))
assert opened is not None, "could not open the Broadway display"
Adw.init()
# Measure under the production stylesheet: the app always runs with
# xx_ui applied, and default Adwaita minima do not reflect it.
xx_ui.apply()

from xx_email import bodies as bodies_mod  # noqa: E402
from xx_email import config as config_mod  # noqa: E402
from xx_email import db as db_mod  # noqa: E402
from xx_email import log as log_mod  # noqa: E402
from xx_email import mailbox as mailbox_mod  # noqa: E402
from xx_email import setup as setup_mod  # noqa: E402
from xx_email import snooze as snooze_mod  # noqa: E402
from xx_email.ui import compose_page as compose_page_mod  # noqa: E402
from xx_email.ui import logs_page as logs_page_mod  # noqa: E402
from xx_email.ui import mailbox_page as mailbox_page_mod  # noqa: E402
from xx_email.ui import search_page as search_page_mod  # noqa: E402
from xx_email.ui import settings_page as settings_page_mod  # noqa: E402
from xx_email.ui import setup_page as setup_page_mod  # noqa: E402
from xx_email.ui import thread_page as thread_page_mod  # noqa: E402

ERRORS: list[str] = []

ACCOUNT = "user@dev.xxemail"
ACCOUNT2 = "second.account@example.com"
NOW_MS = 1_700_000_000_000
LONG_RUN = "x" * 200


def _hook_errors() -> None:
    def _collect(exc_type, exc, tb) -> None:
        ERRORS.append("".join(
            traceback.format_exception(exc_type, exc, tb)))

    sys.excepthook = _collect


def _push_visible(nav, page, label: str) -> None:
    nav.push(page)
    if not _wait_until(lambda: nav.get_visible_page() is page,
                       timeout_s=10.0):
        raise AssertionError(
            f"email settings: {label} page never shown")


def _open_db():
    setup_mod.ensure_app_dirs()
    return db_mod.open_db(config_mod.data_dir() / db_mod.DB_FILE_NAME)


def _seed() -> None:
    setup_mod.ensure_app_dirs()
    log_mod.init_default()
    log_mod.get().i("sync", f"signed in as {ACCOUNT}")
    conn = _open_db()
    try:
        for email in (ACCOUNT, ACCOUNT2):
            db_mod.upsert_account(conn, db_mod.Account(
                email=email, display_name="Dev User",
                history_id="100", last_sync_at=NOW_MS))
        threads = []
        messages = []
        for index in range(8):
            tid = f"t-long-{index:d}"
            threads.append(db_mod.Thread(
                account_email=ACCOUNT, id=tid,
                snippet=("Snippet with a very long unbroken run "
                         + LONG_RUN),
                subject=("Re: " + LONG_RUN + f" {index:d}"),
                from_address=("Very Long Sender Name That Never Ends "
                              "<very.long.sender@example.com>"),
                from_name="Very Long Sender Name That Never Ends",
                date=NOW_MS - index * 60_000, message_count=2,
                unread_count=1, has_attachments=(index % 2 == 0),
                starred=(index % 3 == 0), in_inbox=True,
                categories="", labels_csv="INBOX,UNREAD"))
            messages.append(db_mod.Message(
                account_email=ACCOUNT, id=f"m-{tid}",
                thread_id=tid, subject="Body subject",
                from_address="very.long.sender@example.com",
                to_csv=ACCOUNT, cc_csv="", date=NOW_MS,
                snippet="see inside", read=False, starred=False,
                has_attachments=False, labels_csv="INBOX,UNREAD",
                message_id_header=f"<{tid}@example.com>",
                body_plain=("Line one.\n" + LONG_RUN + "\nLine three "
                            "with plenty of words to wrap around."),
                body_fetched=True))
        db_mod.upsert_threads(conn, threads)
        db_mod.upsert_messages(conn, messages)
        db_mod.upsert_snooze_wake(conn, db_mod.SnoozeWake(
            account_email=ACCOUNT, thread_id="t-long-0",
            target_at=NOW_MS + 3_600_000, created_at=NOW_MS))
        config = config_mod.load()
        config.last_account = ACCOUNT
        config_mod.save(config)
    finally:
        conn.close()


def _toast(message, on_undo) -> None:
    _ = (message, on_undo)


class _MailboxHooks:
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
        done([ACCOUNT, ACCOUNT2])

    def refresh(self, done) -> None:
        done({"ok": True, "new_ids": []})

    def load_more(self, folder, done) -> None:
        _ = folder
        done(([], False))

    def failed_count(self, done) -> None:
        done(0)

    def perform(self, *args) -> None:
        done = args[-1]
        done(type("Undo", (), {"message": "Archived"})())

    def revert(self, undo, done) -> None:
        _ = undo
        done(True)

    def retry_failed(self) -> None:
        pass

    def open_thread(self, thread_id: str) -> None:
        _ = thread_id

    def open_compose(self) -> None:
        pass

    def open_search(self) -> None:
        pass

    def open_settings(self) -> None:
        pass

    def open_signin(self) -> None:
        pass

    def open_permissions(self) -> None:
        pass

    def switch_account(self, email: str) -> None:
        _ = email

    def add_account(self) -> None:
        pass

    def sign_out(self, email, revoke) -> None:
        _ = (email, revoke)


class _ThreadHooks:
    def load_thread(self, done) -> None:
        conn = _open_db()
        try:
            thread = db_mod.get_thread(conn, ACCOUNT, "t-long-1")
            messages = db_mod.messages_for_thread(conn, ACCOUNT,
                                                  "t-long-1")
            entries = [{
                "message_id": message.id, "html": message.body_html,
                "plain": message.body_plain, "attachments": []}
                for message in messages]
            done((thread, messages, entries))
        finally:
            conn.close()

    def mark_read(self) -> None:
        pass

    def perform(self, action, ids, done, starred: bool = False
                ) -> None:
        _ = (action, ids, starred)
        done({"undo": None})

    def thread_undone(self, undo) -> None:
        _ = undo

    def open_reply(self, mode, subject, quote) -> None:
        _ = (mode, subject, quote)

    def open_attachment(self, attachment) -> None:
        _ = attachment

    def go_back(self) -> None:
        pass


class _SearchHooks:
    def search(self, query: str, done) -> None:
        _ = query
        done(([], False, False))

    def open_thread(self, thread_id: str) -> None:
        _ = thread_id

    def go_back(self) -> None:
        pass


class _ComposeHooks:
    def save_draft(self, fields: dict) -> None:
        _ = fields

    def stage_upload(self, path: str, name: str, done) -> None:
        _ = (path, name)
        done(Exception("no uploads in audit"))

    def remove_staged(self, path) -> None:
        _ = path

    def fetch_offered(self, done) -> None:
        done([])

    def decline_offer(self) -> None:
        pass

    def send(self, fields: dict, staged: list, scheduled_at,
             done) -> None:
        _ = (fields, staged, scheduled_at)
        done((1, 10, False))

    def undo_send(self, entry_id: int, done) -> None:
        _ = entry_id
        done(True)

    def discard(self) -> None:
        pass

    def after_sent(self) -> None:
        pass


class _SetupHooks:
    def lookup_keyring(self, done) -> None:
        done(False)

    def start_sign_in(self, client_id, on_url, on_result) -> None:
        _ = (client_id, on_url, on_result)

    def cancel_sign_in(self) -> None:
        pass

    def account_ready(self, email: str) -> None:
        _ = email

    def open_setup_guide(self) -> None:
        pass

    def open_permissions(self) -> None:
        pass

    def go_back(self) -> None:
        pass


class _SettingsHooks:
    def load_state(self, done) -> None:
        conn = _open_db()
        try:
            entries = [{
                "email": item.email,
                "snoozed": snooze_mod.pending_wake_count(
                    conn, item.email),
                "queued": len(db_mod.open_outbox_for_account(
                    conn, item.email))}
                for item in db_mod.all_accounts(conn)]
            done((config_mod.load(), entries))
        finally:
            conn.close()

    def add_account(self) -> None:
        pass

    def remove_account(self, email: str) -> None:
        _ = email

    def revoke_account(self, email: str, done) -> None:
        done(f"No stored grant for {email}.")

    def open_permissions(self) -> None:
        pass

    def open_logs(self) -> None:
        pass

    def export_backup(self, path: str, done) -> None:
        done(path)

    def import_backup(self, path: str, done) -> None:
        done({"restored": {"wakes": 0, "pages": 0, "outbox": 0},
              "skipped": []})


class _LogsHooks:
    def load_logs(self, done) -> None:
        done(log_mod.get().share_text())

    def go_back(self) -> None:
        pass


def _measure(name: str, widget) -> None:
    try:
        sizes = widget.measure(Gtk.Orientation.HORIZONTAL, -1)
        minimum = sizes[0]
    except Exception as exc:  # noqa: BLE001
        print(f"MIN-ERROR {name}: {exc}")
        return
    try:
        alloc = widget.get_width()
    except Exception:  # noqa: BLE001
        alloc = -1
    print(f"MIN {name}: {int(minimum)}")
    print(f"ALLOC {name}: {int(alloc)}")


def _scenario_audit() -> None:
    _seed()
    window = Gtk.Window()
    window.set_default_size(320, 640)
    nav = Adw.NavigationView()
    window.set_child(nav)
    window.present()
    if not _wait_until(window.get_mapped, timeout_s=10.0):
        raise AssertionError("email settings: window never mapped")

    mailbox_page = mailbox_page_mod.MailboxPage(
        ACCOUNT, _MailboxHooks(), _toast)
    _push_visible(nav, Adw.NavigationPage(title="Mailbox",
                                          child=mailbox_page), "mailbox")
    mailbox_page.load()
    if not _wait_until(lambda: nav.get_visible_page() is not None,
                       timeout_s=10.0):
        raise AssertionError("email settings: mailbox never settled")
    _measure("mailbox", nav.get_visible_page())

    _push_visible(nav, thread_page_mod.thread_dialog_page(
        ACCOUNT, "t-long-1", _ThreadHooks(), _toast), "thread")
    _measure("thread", nav.get_visible_page())

    _push_visible(nav, search_page_mod.search_dialog_page(
        ACCOUNT, _SearchHooks(), _toast), "search")
    _measure("search", nav.get_visible_page())

    initial = {"to": ACCOUNT2, "cc": "", "bcc": "",
               "subject": "Re: " + LONG_RUN,
               "body": "Draft body " + LONG_RUN,
               "thread_id": "", "quote_id": "", "mode": "new",
               "in_reply_to": "", "references": "",
               "offer_count": 0, "show_cc_bcc": False}
    compose_page = compose_page_mod.ComposePage(
        ACCOUNT, initial, _ComposeHooks(), _toast)
    _push_visible(nav, compose_page_mod.compose_navigation_page(
        compose_page), "compose")
    _measure("compose", nav.get_visible_page())

    setup_page = setup_page_mod.SetupPage(
        _SetupHooks(), _toast, mode="add",
        client_id="x.apps.googleusercontent.com")
    _push_visible(nav, setup_page_mod.setup_navigation_page(setup_page),
                  "setup")
    _measure("setup", nav.get_visible_page())

    logs_page = logs_page_mod.LogsPage(_LogsHooks(), _toast)
    _push_visible(nav, logs_page_mod.logs_navigation_page(logs_page),
                  "logs")
    _measure("logs", nav.get_visible_page())

    settings_page = settings_page_mod.SettingsPage(
        _SettingsHooks(), _toast)
    _push_visible(nav, settings_page_mod.settings_navigation_page(
        settings_page), "settings")
    _measure("settings", nav.get_visible_page())

    bodies_text = bodies_mod.render_body(
        "<p>Hello</p>", "Hello fallback")
    assert "Hello" in bodies_text
    print("PAGES_OK: mailbox thread search compose setup logs "
          "settings")


def main() -> int:
    _hook_errors()
    try:
        mode = sys.argv[1] if len(sys.argv) > 1 else "audit"
        if mode != "audit":
            print(f"unknown mode {mode}")
            return 2
        _scenario_audit()
    except Exception:
        traceback.print_exc()
        return 1
    if ERRORS:
        for text in ERRORS:
            sys.stderr.write(text + "\n")
        return 1
    print("ERRORS: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
