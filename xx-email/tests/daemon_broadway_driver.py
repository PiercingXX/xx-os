"""Broadway driver for the milestone 8 smoke test (not collected).

Runs one scenario against a real GTK display (the parent pytest
hands it GDK_BACKEND=broadway plus BROADWAY_DISPLAY) and prints KEY:
value markers on stdout. Any unexpected exception exits nonzero so
the parent test fails instead of passing on empty output.

Scenario: the background service tick runs first (fake engine + a
recording bus, real seeded store), then the app opens the notified
thread through the real open-thread URI path, and the real mailbox +
thread pages pump without exceptions.
"""

import os
import sys
import traceback
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "common"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from xx_testing.broadway_wait import pump_until as _wait_until  # noqa: E402

Gtk.init()
opened = Gdk.Display.open(os.environ.get("BROADWAY_DISPLAY", ""))
assert opened is not None, "could not open the Broadway display"
Adw.init()

from xx_email import daemon as daemon_mod  # noqa: E402
from xx_email import db as db_mod  # noqa: E402
from xx_email import mailbox as mailbox_mod  # noqa: E402
from xx_email import notify as notify_mod  # noqa: E402
from xx_email import setup as setup_mod  # noqa: E402
from xx_email.sync_service import SyncOutcome  # noqa: E402
from xx_email.ui import mailbox_page as mailbox_page_mod  # noqa: E402
from xx_email.ui import thread_page as thread_page_mod  # noqa: E402

ERRORS: list[str] = []

ACCOUNT = "user@dev.xxemail"
THREAD = "t-open"


def _hook_errors() -> None:
    def _collect(exc_type, exc, tb) -> None:
        ERRORS.append("".join(
            traceback.format_exception(exc_type, exc, tb)))

    sys.excepthook = _collect


def _mapped(window) -> bool:
    try:
        return bool(window.get_mapped())
    except Exception:
        return False


def _open_db():
    setup_mod.ensure_app_dirs()
    from xx_email import config as config_mod
    return db_mod.open_db(config_mod.data_dir() / db_mod.DB_FILE_NAME)


class _FakeResult:
    def __init__(self, server_id: int) -> None:
        self._server_id = server_id

    def unpack(self):
        return (self._server_id,)


class _FakeBus:
    def __init__(self) -> None:
        self.notifies: list[dict] = []

    def call(self, bus, path, iface, method, params, reply_type,
             flags, timeout, cancellable, callback) -> None:
        summary = params.unpack()[3] if method == "Notify" else ""
        self.notifies.append({"method": method, "summary": summary,
                              "reply_type": reply_type})
        if callback is not None:
            callback(self, _FakeResult(41))

    def call_finish(self, result):
        return result


class _FakeEngine:
    def sync_account(self, account: str):
        return SyncOutcome(email=account, ok=True,
                           new_ids=[THREAD], checkpoint="9")


class _FakeClient:
    def __init__(self) -> None:
        self.modified: list = []

    def modify_thread(self, thread_id, add=None, remove=None):
        self.modified.append((thread_id, add, remove))
        return {}


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
        done(type("Undo", (), {"message": "Archived"})())

    def revert(self, undo, done) -> None:
        _ = undo
        done(True)

    def load_thread(self, done) -> None:
        conn = _open_db()
        try:
            thread = db_mod.get_thread(conn, ACCOUNT, THREAD)
            messages = db_mod.messages_for_thread(conn, ACCOUNT,
                                                  THREAD)
            entries = [{
                "message_id": message.id, "html": message.body_html,
                "plain": message.body_plain, "attachments": []}
                for message in messages]
            done((thread, messages, entries))
        finally:
            conn.close()

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

    def open_thread(self, thread_id: str) -> None:
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

    def switch_account(self, email: str) -> None:
        _ = email

    def add_account(self) -> None:
        pass

    def sign_out(self, email, revoke) -> None:
        _ = (email, revoke)

    def retry_failed(self) -> None:
        pass


def _toast(message, on_undo) -> None:
    _ = (message, on_undo)


def _scenario_smoke() -> None:
    conn = _open_db()
    try:
        assert db_mod.get_thread(conn, ACCOUNT, THREAD) is not None, \
            "seed thread missing"
    finally:
        conn.close()

    bus = _FakeBus()
    conn = _open_db()
    try:
        result = daemon_mod.run_tick(
            conn, lambda _account: _FakeEngine(), bus,
            lambda _account: _FakeClient(), now_ms=1700000000000,
            app_config=SimpleNamespace(notifications_enabled=True),
            shell_config={})
    finally:
        conn.close()
    assert result["notified"] == {ACCOUNT: 41}, result["notified"]
    payload = result["payloads"][ACCOUNT]
    assert payload["thread_id"] == THREAD
    assert len(bus.notifies) == 1
    print("TICK_NOTIFIED:", result["notified"][ACCOUNT])

    uri = notify_mod.open_thread_uri(ACCOUNT, THREAD)
    parsed = notify_mod.parse_open_thread_uri(uri)
    assert parsed == (ACCOUNT, THREAD), parsed
    print("URI_OK:", uri)

    from xx_email import main as main_mod

    assert main_mod.EmailApplication._parse_open_target(
        ["--open-thread", THREAD,
         "--account", ACCOUNT]) == (ACCOUNT, THREAD)
    assert main_mod.EmailApplication._parse_open_target(
        [uri]) == (ACCOUNT, THREAD)
    app = main_mod.EmailApplication()
    flags = app.get_flags()
    assert flags & Gio.ApplicationFlags.HANDLES_OPEN, flags
    print("HANDLES_OPEN: yes")
    app._executor.shutdown(wait=False)

    hooks = _StubHooks()
    mailbox = mailbox_page_mod.MailboxPage(ACCOUNT, hooks, _toast)
    thread = thread_page_mod.ThreadPage(ACCOUNT, THREAD, hooks,
                                        _toast)

    def _show(page, title: str) -> Gtk.Window:
        window = Gtk.Window()
        window.set_title(title)
        window.set_default_size(360, 720)
        window.set_child(page)
        window.present()
        return window

    mailbox_window = _show(mailbox, "Mailbox")
    thread_window = _show(thread, "Thread")
    mailbox.load()
    if not _wait_until(lambda: _mapped(mailbox_window)
                       and _mapped(thread_window), timeout_s=10.0):
        raise AssertionError("email daemon: pages never mapped")
    mailbox_window.destroy()
    thread_window.destroy()
    print("PAGES_OK: mailbox thread")


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
