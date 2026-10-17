"""GTK entry point: Adw.Application with the mailbox NavigationView stack.

Milestone 6: setup → mailbox → thread / search routes over the L1/L2
store + sync engine. The mailbox is a chip-filtered Gtk.ListView with
a folders page, swipe actions and multi-select; threads render
plain-text bodies; search fans out to the server or the offline FTS
index. Thin views only — formatting and mutations live in the pure
mailbox/bodies modules.

Device rules honored throughout: display CSS comes from
Gdk.Display.get_default (Application has no get_display); no text
entry is ever focused at startup (the OSK stays down until a tap); no
minimize/maximize buttons (page headers turn title buttons off); every
list background comes from the XX-WM theme (no default white
ListView). Store, network and Secret Service work runs on worker
threads with results posted via GLib.idle_add.
"""

import concurrent.futures
import json
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

import xx_ui

from xx_email import __app_id__
from xx_email import auth as auth_mod
from xx_email import bodies as bodies_mod
from xx_email import compose as compose_mod
from xx_email import config as config_mod
from xx_email import db as db_mod
from xx_email import gmail_client as gmail_mod
from xx_email import mailbox as mailbox_mod
from xx_email import notify as notify_mod
from xx_email import outbox as outbox_mod
from xx_email import setup as setup_mod
from xx_email import sync_service as sync_mod
from xx_email import token_store as token_mod
from xx_email.domain import MessageParts, SafePaths
from xx_email.ui import common as common_mod
from xx_email.ui import compose_page as compose_page_mod
from xx_email.ui import folders_page as folders_page_mod
from xx_email.ui import logs_page as logs_page_mod
from xx_email.ui import mailbox_page as mailbox_page_mod
from xx_email.ui import search_page as search_page_mod
from xx_email.ui import settings_page as settings_page_mod
from xx_email.ui import setup_page as setup_page_mod
from xx_email.ui import thread_page as thread_page_mod


def _db_path():
    dirs = setup_mod.ensure_app_dirs()
    return dirs["data"] / db_mod.DB_FILE_NAME


def _on_uri_launched(launcher, result) -> None:
    try:
        launcher.launch_finish(result)
    except Exception:
        pass


def _open_db():
    return db_mod.open_db(_db_path())


def _gmail_client(account: str):
    """Gmail client with a fresh token (worker thread only).

    Returns (client, token_worker); the caller closes the worker when
    done. Secret Service round-trips stay off the main loop.
    """
    worker = token_mod.TokenStoreWorker(
        token_mod.TokenStore(token_mod.SecretTokenBackend()))
    try:
        refresh = sync_mod.production_refresh(worker)
        seed = refresh(account, "")
    except Exception:
        worker.close()
        raise
    client = gmail_mod.GmailClient(
        get_token=lambda: seed,
        refresh_token=lambda rejected: refresh(account, rejected))
    return (client, worker)


def _revert_local(account: str, undo) -> None:
    """Roll back an optimistic local action after a server failure.

    Runs on the calling worker thread (never the main loop); the next
    delta sync still reconciles anything left diverged.
    """
    conn = _open_db()
    try:
        undo.revert(conn)
    finally:
        conn.close()


def _is_offline(exc: Exception) -> bool:
    """True for transport-level failures (no HTTP status came back)."""
    if isinstance(exc, gmail_mod.GmailTransportError):
        return exc.status is None
    return isinstance(exc, OSError)


def _run_server_ops(account: str, ops: list) -> None:
    client, worker = _gmail_client(account)
    try:
        for kind, thread_id, add, remove in ops:
            if kind == "modify":
                client.modify_thread(thread_id, add or None,
                                      remove or None)
            elif kind == "trash":
                client.trash_thread(thread_id)
            elif kind == "untrash":
                client.untrash_thread(thread_id)
    finally:
        worker.close()


def _engine_for(conn, account: str):
    worker = token_mod.TokenStoreWorker(
        token_mod.TokenStore(token_mod.SecretTokenBackend()))
    refresh = sync_mod.production_refresh(worker)
    try:
        stored = config_mod.load()
    except Exception:
        stored = None
    engine = sync_mod.SyncEngine(
        conn, gmail_mod.urllib_transport, tokens={},
        refresh=lambda email, rejected: refresh(email, rejected),
        files_dir=config_mod.data_dir(),
        send_and_archive=bool(stored is not None and
                              stored.send_and_archive))
    return (engine, worker)


class EmailApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=__app_id__,
            flags=Gio.ApplicationFlags.HANDLES_OPEN |
            Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self._email_css = Gtk.CssProvider()
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="xxemail-ui")
        self._nav: Adw.NavigationView | None = None
        self._overlay: Adw.ToastOverlay | None = None
        self._mailbox_page = None
        self._current_route: str | None = None
        self._current_account: str | None = None
        self._pending_undo = None
        self._pending_tap: tuple | None = None
        undo_action = Gio.SimpleAction.new("undo-mail", None)
        undo_action.connect("activate", lambda *_a: self._activate_undo())
        self.add_action(undo_action)
        tap_action = Gio.SimpleAction.new(
            "open-thread", GLib.VariantType.new("(ss)"))
        tap_action.connect("activate", self._on_open_thread_action)
        self.add_action(tap_action)

    # ------------------------------------------------------------------ app

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is None:
            window = Adw.ApplicationWindow(application=self)
            window.set_title("xx-email")
            window.set_default_size(360, 720)
            window.set_decorated(False)
            self._overlay = Adw.ToastOverlay()
            self._nav = Adw.NavigationView()
            self._overlay.set_child(self._nav)
            window.set_content(self._overlay)
            xx_ui.apply(self)
            self._apply_app_css()
            self._nav.push(self._loading_page())
            tap, self._pending_tap = self._pending_tap, None
            self._decide_start(tap=tap)
        else:
            tap, self._pending_tap = self._pending_tap, None
            if tap is not None and tap[0] and tap[1]:
                self.open_thread_target(tap[0], tap[1])
        window.present()

    def do_open(self, files, hint) -> None:
        _ = hint
        tap = None
        for item in files:
            try:
                uri = item.get_uri()
            except Exception:
                continue
            if not uri or "://" not in uri:
                continue
            account, thread = notify_mod.parse_open_thread_uri(uri)
            if thread:
                tap = (account, thread)
                break
        self._pending_tap = tap
        self.activate()

    def do_command_line(self, cmdline) -> int:
        try:
            argv = list(cmdline.get_arguments())
        except Exception:
            argv = []
        tap = self._parse_open_target(argv[1:])
        if tap is not None:
            self._pending_tap = tap
        self.activate()
        return 0

    @staticmethod
    def _parse_open_target(args: list) -> tuple | None:
        """--open-thread ID [--account A] or an open-thread URI."""
        return notify_mod.parse_open_target_args(args)

    def open_thread_target(self, account: str,
                           thread_id: str) -> None:
        """Navigate a notification tap: mailbox first, then the thread."""
        if self._nav is None or not account or not thread_id:
            self._pending_tap = (account, thread_id)
            return
        decision = mailbox_mod.notification_tap_action(
            self._current_route, self._current_account,
            account, thread_id)
        if decision[0] == "thread":
            self._push_thread(account, thread_id)
        elif decision[0] == "mailbox":
            self._push_mailbox(account)
            self._push_thread(account, thread_id)

    def do_shutdown(self) -> None:
        self._executor.shutdown(wait=False)
        Adw.Application.do_shutdown(self)

    def _loading_page(self) -> Adw.NavigationPage:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(48)
        spinner = Adw.Spinner()
        box.append(spinner)
        status = Gtk.Label(label="Opening mailbox…")
        status.set_wrap(True)
        box.append(status)
        page = Adw.NavigationPage(title="Loading")
        page.set_child(box)
        return page

    def _decide_start(self, tap=None) -> None:
        def _blocking():
            conn = _open_db()
            try:
                accounts = [a.email for a in db_mod.all_accounts(conn)]
            finally:
                conn.close()
            return (accounts, config_mod.load().last_account)

        def _done(result) -> None:
            if isinstance(result, Exception):
                accounts, last = ([], None)
            else:
                accounts, last = result
            tap_account, tap_thread = tap or (None, None)
            if tap_account not in accounts:
                tap_account, tap_thread = None, None
            route = mailbox_mod.start_route(accounts, last,
                                            tap_account)
            self._nav.pop()
            if route == "setup":
                self._push_setup()
                return
            account = route.split("/", 1)[1]
            self._push_mailbox(account, kick_sync=True)
            if tap_thread and tap_account == account:
                self._push_thread(account, tap_thread)

        common_mod.run_in_background(self._executor, _blocking, _done)

    # ----------------------------------------------------------------- pages

    def toast(self, message: str, on_undo=None) -> None:
        toast = Adw.Toast.new(message)
        if on_undo is not None:
            self._pending_undo = on_undo
            toast.set_button_label("Undo")
            toast.set_action_name("app.undo-mail")
        if self._overlay is not None:
            self._overlay.add_toast(toast)

    def _activate_undo(self) -> None:
        undo, self._pending_undo = self._pending_undo, None
        if undo is None:
            return
        try:
            undo()
        except Exception:
            self.toast("Undo failed", None)

    def _open_browser(self, url: str) -> bool:
        try:
            launcher = Gtk.UriLauncher.new(url)
        except Exception:
            self.toast("Couldn't open the browser.", None)
            return False
        try:
            window = self.props.active_window
            launcher.launch(window, None, _on_uri_launched)
        except Exception:
            self.toast("Couldn't open the browser.", None)
            return False
        return True

    def _open_setup_guide(self) -> None:
        here = Path(__file__).resolve()
        candidates = [here.parent.parent / "docs" / "oauth-setup.md",
                      here.parent.parent.parent / "docs" / "oauth-setup.md"]
        for candidate in candidates:
            if candidate.is_file():
                self._open_browser(candidate.as_uri())
                return
        self.toast("Setup guide: docs/oauth-setup.md", None)

    def _open_permissions(self) -> None:
        self._open_browser(setup_mod.GOOGLE_PERMISSIONS_URL)

    def _push_setup(self, mode: str = "first-run",
                    account: str | None = None) -> None:
        hooks = _SetupHooks(self, mode, account)
        page = setup_page_mod.SetupPage(
            hooks, self.toast, mode=mode, account=account,
            client_id=config_mod.load().client_id)
        self._current_route = "setup"
        self._current_account = account
        self._nav.push(setup_page_mod.setup_navigation_page(page))

    def _on_setup_account_ready(self, email: str, mode: str) -> None:
        def _blocking():
            conn = _open_db()
            engine, token_worker = _engine_for(conn, email)
            try:
                return engine.sync_account(email)
            finally:
                engine.close()
                token_worker.close()
                conn.close()

        def _done(result) -> None:
            visible = self._nav.get_visible_page()
            if visible is not None and visible.get_title() == "Setup":
                self._nav.pop()
            if isinstance(result, Exception):
                error = "Couldn't finish the first sync."
            else:
                error = result.error if not result.ok else None
            if mode == "reauth":
                if error is None:
                    self.toast("Signed in", None)
                    if self._mailbox_page is not None:
                        self._mailbox_page.set_reauth(False)
                else:
                    self.toast(error, None)
                if self._mailbox_page is not None:
                    self._mailbox_page.load()
                return
            self._push_mailbox(email)
            if error is not None:
                self.toast(error, None)

        common_mod.run_in_background(self._executor, _blocking, _done)

    def request_sign_out(self, email: str, revoke: bool) -> None:
        def _blocking():
            conn = _open_db()
            try:
                store = token_mod.TokenStore(
                    token_mod.SecretTokenBackend())
                if revoke:
                    outcome = setup_mod.sign_out(
                        conn, store, email,
                        data_dir=config_mod.data_dir(),
                        revoke=lambda cid, token:
                        setup_mod.revoke_grant(token))
                else:
                    outcome = setup_mod.sign_out(
                        conn, store, email,
                        data_dir=config_mod.data_dir(),
                        revoke=None)
                remaining = [item.email
                             for item in db_mod.all_accounts(conn)]
                current = config_mod.load()
                if current.last_account == email:
                    current.last_account = \
                        remaining[0] if remaining else ""
                    config_mod.save(current)
                return {"remaining": remaining,
                        "warnings": list(outcome["warnings"]),
                        "revoked": bool(outcome["revoked"])}
            finally:
                conn.close()

        def _done(result) -> None:
            if isinstance(result, Exception):
                self.toast("Sign out failed", None)
                return
            for warning in result["warnings"]:
                self.toast(warning, None)
            if not result["warnings"]:
                self.toast("Signed out", None)
            self._after_sign_out(email, result["remaining"])

        common_mod.run_in_background(self._executor, _blocking, _done)

    def _after_sign_out(self, email: str,
                        remaining: list[str]) -> None:
        self._nav.pop()
        if self._current_account == email:
            if remaining:
                self._push_mailbox(remaining[0])
            else:
                self._push_setup()
        elif self._mailbox_page is not None:
            self._mailbox_page.load()

    def _push_settings(self) -> None:
        hooks = _SettingsHooks(self)
        navigation = settings_page_mod.settings_navigation_page(
            settings_page_mod.SettingsPage(hooks, self.toast))
        self._nav.push(navigation)

    def _push_folders(self, account: str) -> None:
        hooks = _FoldersHooks(self, account)
        self._nav.push(folders_page_mod.folders_navigation_page(
            folders_page_mod.FoldersPage(account, hooks,
                                         self.toast)))

    def _push_logs(self) -> None:
        hooks = _LogsHooks(self)
        page = logs_page_mod.LogsPage(hooks, self.toast)
        self._nav.push(logs_page_mod.logs_navigation_page(page))

    def _push_mailbox(self, account: str, kick_sync: bool = False) -> None:
        hooks = _MailboxHooks(self, account)
        page = mailbox_page_mod.MailboxPage(
            account, hooks, self.toast,
            swipe_left=config_mod.load().swipe_left,
            swipe_right=config_mod.load().swipe_right)
        self._mailbox_page = page
        navigation = Adw.NavigationPage(title="Mailbox")
        navigation.set_child(page)
        self._current_route = mailbox_mod.route_mailbox(account)
        self._current_account = account
        config = config_mod.load()
        config.last_account = account
        config_mod.save(config)
        self._nav.push(navigation)
        page.load()
        if kick_sync:
            page.refresh()

    def _push_thread(self, account: str, thread_id: str) -> None:
        hooks = _ThreadHooks(self, account, thread_id)
        navigation = thread_page_mod.thread_dialog_page(
            account, thread_id, hooks, self.toast)
        self._current_route = mailbox_mod.route_thread(account,
                                                       thread_id)
        self._current_account = account
        self._nav.push(navigation)

    def _push_search(self, account: str) -> None:
        hooks = _SearchHooks(self, account)
        navigation = search_page_mod.search_dialog_page(
            account, hooks, self.toast)
        self._current_route = mailbox_mod.route_search(account)
        self._current_account = account
        self._nav.push(navigation)

    def _push_compose(self, account: str, thread_id: str = "",
                      quote_id: str = "", mode: str = "new",
                      initial_to: str = "",
                      initial_cc: str = "",
                      initial_subject: str = "") -> None:
        hooks = _ComposeHooks(self, account, thread_id, quote_id,
                              mode, initial_to, initial_cc,
                              initial_subject)
        loading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        status = Gtk.Label(label="Opening compose…")
        status.set_margin_top(48)
        loading.append(status)
        landing = Adw.NavigationPage(title="Compose")
        landing.set_child(loading)
        self._nav.push(landing)

        def _done(result) -> None:
            if self._nav.get_visible_page() is not landing:
                return
            self._nav.pop()
            if isinstance(result, Exception):
                self.toast("Couldn't open compose", None)
                return
            initial, autofocus = result
            page = compose_page_mod.ComposePage(
                account, initial, hooks, self.toast,
                autofocus_to=bool(autofocus))
            self._current_route = f"compose/{account}/{mode}"
            self._current_account = account
            self._nav.push(
                compose_page_mod.compose_navigation_page(page))

        hooks.load_initial(_done)

    def _push_placeholder(self, title: str, text: str) -> None:
        label = Gtk.Label(label=text)
        label.set_wrap(True)
        label.set_margin_top(48)
        label.set_margin_start(24)
        label.set_margin_end(24)
        page = Adw.NavigationPage(title=title)
        page.set_child(label)
        self._nav.push(page)

    def _on_open_thread_action(self, _action, parameter) -> None:
        account, thread_id = parameter.unpack()
        decision = mailbox_mod.notification_tap_action(
            self._current_route, self._current_account,
            account, thread_id)
        if decision[0] == "thread":
            self._push_thread(account, thread_id)
        elif decision[0] == "mailbox":
            self._push_mailbox(account)
            self._push_thread(account, thread_id)

    # ------------------------------------------------------------- app css

    def _apply_app_css(self) -> None:
        """Install the small app stylesheet over the xx_ui design CSS."""
        try:
            css_path = Path(__file__).with_name("email.css")
            self._email_css.load_from_data(
                css_path.read_bytes())
        except OSError:
            return
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display,
                self._email_css,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )


class _MailboxHooks:
    """Worker-backed mailbox callbacks (main loop never blocks)."""

    def __init__(self, app: EmailApplication, account: str) -> None:
        self._app = app
        self._account = account

    def _run(self, blocking, done) -> None:
        common_mod.run_in_background(self._app._executor, blocking, done)

    def load_folder(self, folder, done) -> None:
        account = self._account

        def _blocking():
            conn = _open_db()
            try:
                threads = mailbox_mod.threads_for_folder(conn, account,
                                                         folder)
                if folder == mailbox_mod.MailboxFolder.DRAFTS:
                    threads = list(threads) + _local_draft_rows(
                        account)
                key = mailbox_mod.folder_page_key(folder)
                more = False
                if key is not None:
                    page = db_mod.get_folder_page(conn, account, key)
                    more = page is not None and \
                        page.next_page_token is not None
                return (threads, more)
            finally:
                conn.close()

        self._run(_blocking, done)

    def load_outbox(self, done) -> None:
        account = self._account

        def _blocking():
            conn = _open_db()
            try:
                return (mailbox_mod.outbox_for_folder(conn, account),
                        False)
            finally:
                conn.close()

        self._run(_blocking, done)

    def load_accounts(self, done) -> None:
        def _blocking():
            conn = _open_db()
            try:
                return [a.email for a in db_mod.all_accounts(conn)]
            finally:
                conn.close()

        self._run(_blocking, done)

    def refresh(self, done) -> None:
        account = self._account

        def _blocking():
            conn = _open_db()
            engine, token_worker = _engine_for(conn, account)
            try:
                outcome = engine.sync_account(account)
                return {"ok": outcome.ok, "error": outcome.error,
                        "reauth": outcome.reauth,
                        "offline": outcome.offline,
                        "new_ids": list(outcome.new_ids)}
            finally:
                engine.close()
                token_worker.close()
                conn.close()

        self._run(_blocking, done)

    def perform(self, action, ids, wake_at, label_id, done) -> None:
        account = self._account

        def _blocking():
            conn = _open_db()
            try:
                undo = mailbox_mod.apply_local_action(
                    conn, account, action, list(ids), wake_at=wake_at,
                    label_id=label_id)
            finally:
                conn.close()
            try:
                _run_server_ops(account, undo.server_ops)
            except Exception:
                _revert_local(account, undo)
                raise
            return undo

        self._run(_blocking, done)

    def revert(self, undo, done) -> None:
        def _blocking():
            conn = _open_db()
            try:
                undo.revert(conn)
                return True
            finally:
                conn.close()

        self._run(_blocking, done)

    def load_more(self, folder, done) -> None:
        account = self._account

        def _blocking():
            conn = _open_db()
            try:
                key = mailbox_mod.folder_page_key(folder)
                if key is None:
                    return ([], False)
                saved = db_mod.get_folder_page(conn, account, key)
                token = saved.next_page_token if saved else None
                if token is None:
                    return ([], False)
                labels, spam_trash = mailbox_mod.folder_list_query(folder)
                engine, token_worker = _engine_for(conn, account)
                try:
                    rows, next_token = engine.fetch_folder_page(
                        account, key, labels, spam_trash, token)
                finally:
                    engine.close()
                    token_worker.close()
                return (rows, next_token is not None)
            finally:
                conn.close()

        self._run(_blocking, done)

    def failed_count(self, done) -> None:
        account = self._account

        def _blocking():
            conn = _open_db()
            try:
                return len(db_mod.failed_outbox_for_account(conn,
                                                            account))
            finally:
                conn.close()

        self._run(_blocking, done)

    def retry_failed(self) -> None:
        account = self._account
        app = self._app

        def _blocking():
            conn = _open_db()
            try:
                rows = db_mod.failed_outbox_for_account(conn, account)
                for row in rows:
                    db_mod.set_outbox_state(conn, row.id, "QUEUED",
                                            error=None)
                return len(rows)
            finally:
                conn.close()

        def _done(result) -> None:
            if isinstance(result, Exception) or not result:
                return
            app.toast(f"Retrying {result:d} failed send"
                      f"{'' if result == 1 else 's'}…", None)

        self._run(_blocking, _done)

    def open_thread(self, thread_id: str) -> None:
        if thread_id.startswith("local-draft:"):
            _open_local_draft(self._app, self._account, thread_id)
            return
        self._app._push_thread(self._account, thread_id)

    def open_compose(self) -> None:
        self._app._push_compose(self._account)

    def open_search(self) -> None:
        self._app._push_search(self._account)

    def open_settings(self) -> None:
        self._app._push_settings()

    def open_folders(self) -> None:
        self._app._push_folders(self._account)

    def open_signin(self) -> None:
        self._app._push_setup(mode="reauth", account=self._account)

    def open_permissions(self) -> None:
        self._app._open_permissions()

    def switch_account(self, email: str) -> None:
        self._app._nav.pop()
        self._app._push_mailbox(email)

    def add_account(self) -> None:
        self._app._push_setup(mode="add")

    def sign_out(self, email: str, revoke: bool) -> None:
        self._app.request_sign_out(email, revoke)


class _SetupHooks:
    """Worker-backed setup callbacks (main loop never blocks).

    The loopback listener binds, the browser opens on the main
    thread, and the wait plus the exchange plus the keyring and
    sqlite writes all run on the worker pool.
    """

    def __init__(self, app: EmailApplication, mode: str,
                 account: str | None = None) -> None:
        self._app = app
        self._mode = mode
        self._account = account
        self._dismissed = False
        self._flow_guard = threading.Lock()
        self._flow = None

    def _remember_flow(self, flow) -> None:
        with self._flow_guard:
            self._flow = flow

    def _release_flow(self):
        with self._flow_guard:
            flow, self._flow = self._flow, None
        return flow

    def _token_store(self):
        return token_mod.TokenStore(
            token_mod.SecretTokenBackend())

    def lookup_keyring(self, done) -> None:
        def _blocking():
            worker = token_mod.TokenStoreWorker(self._token_store())
            try:
                return bool(worker.unreadable)
            finally:
                worker.close()

        common_mod.run_in_background(self._app._executor, _blocking,
                                     done)

    def start_sign_in(self, client_id, on_url, on_result) -> None:
        app = self._app

        def _blocking_begin():
            candidate = (client_id or "").strip()
            if not auth_mod.is_valid_client_id(candidate):
                raise auth_mod.AuthFlowError(
                    "That client ID does not look like a Google "
                    "client ID.")
            setup_mod.ensure_app_dirs()
            worker = token_mod.TokenStoreWorker(self._token_store())
            try:
                if worker.unreadable:
                    raise setup_mod.KeyringLocked(
                        setup_mod.KEYRING_LOCKED_MESSAGE)
            finally:
                worker.close()
            flow = auth_mod.LoopbackFlow(candidate)
            self._remember_flow(flow)
            current = config_mod.load()
            current.client_id = candidate
            config_mod.save(current)
            return (candidate, flow.authorization_url)

        def _begun(result) -> None:
            if isinstance(result, Exception):
                self._release_flow()
                on_result(result)
                return
            candidate, url = result
            if not app._open_browser(url):
                self.cancel_sign_in()
                on_result(auth_mod.AuthFlowError(
                    "Couldn't open the browser — paste this address "
                    "into one manually: " + url))
                return
            on_url(url)
            self._wait_and_finish(candidate, on_result)

        common_mod.run_in_background(app._executor, _blocking_begin,
                                     _begun)

    def _wait_and_finish(self, candidate, on_result) -> None:
        app = self._app

        def _blocking_finish():
            flow = self._release_flow()
            if flow is None:
                raise auth_mod.AuthFlowError("Sign-in was cancelled.")
            code = flow.wait_for_code(
                timeout=setup_mod.SIGN_IN_TIMEOUT)
            conn = _open_db()
            try:
                return setup_mod.finish_sign_in(
                    candidate, code, flow.redirect_uri,
                    flow.verifier, token_transport=None,
                    store=self._token_store(), conn=conn)
            finally:
                conn.close()

        common_mod.run_in_background(app._executor, _blocking_finish,
                                     on_result)

    def cancel_sign_in(self) -> None:
        def _blocking():
            flow = self._release_flow()
            if flow is not None:
                flow.stop()

        self._app._executor.submit(_blocking)

    def account_ready(self, email: str) -> None:
        if self._dismissed:
            self._app.toast("Signed in", None)
            return
        self._app._on_setup_account_ready(email, self._mode)

    def open_setup_guide(self) -> None:
        self._app._open_setup_guide()

    def open_permissions(self) -> None:
        self._app._open_permissions()

    def go_back(self) -> None:
        self._dismissed = True
        self._app._nav.pop()


class _SettingsHooks:
    """Worker-backed settings callbacks (main loop never blocks).

    Config rows persist synchronously in the page (tiny atomic
    file writes); everything touching sqlite, the keyring, the
    network, or backup files runs here on the worker pool.
    """

    def __init__(self, app: EmailApplication) -> None:
        self._app = app

    def _run(self, blocking, done) -> None:
        common_mod.run_in_background(self._app._executor, blocking,
                                     done)

    def load_state(self, done) -> None:
        def _blocking():
            conn = _open_db()
            try:
                from xx_email import snooze as snooze_mod

                entries = []
                for item in db_mod.all_accounts(conn):
                    entries.append({
                        "email": item.email,
                        "snoozed": snooze_mod.pending_wake_count(
                            conn, item.email),
                        "queued": len(db_mod.open_outbox_for_account(
                            conn, item.email))})
                return (config_mod.load(), entries)
            finally:
                conn.close()

        self._run(_blocking, done)

    def add_account(self) -> None:
        self._app._push_setup(mode="add")

    def remove_account(self, email: str) -> None:
        self._app._nav.pop()
        self._app.request_sign_out(email, revoke=False)

    def revoke_account(self, email: str, done) -> None:
        def _blocking():
            worker = token_mod.TokenStoreWorker(token_mod.TokenStore(
                token_mod.SecretTokenBackend()))
            try:
                record = worker.load(email)
                if record is None or not record.refresh_token:
                    return f"No stored grant for {email}."
                confirmed = setup_mod.revoke_grant(
                    record.refresh_token)
                try:
                    worker.remove(email)
                except Exception:
                    pass
                if confirmed:
                    return (f"Access revoked for {email} — sign in "
                            "again to keep syncing.")
                return (f"Google did not confirm revocation for "
                        f"{email} — the local grant is removed; "
                        "revoke it in your Google permissions if "
                        "needed.")
            finally:
                worker.close()

        self._run(_blocking, done)

    def open_permissions(self) -> None:
        self._app._open_permissions()

    def open_logs(self) -> None:
        self._app._push_logs()

    def export_backup(self, path: str, done) -> None:
        def _blocking():
            from xx_email import backup as backup_mod

            conn = _open_db()
            try:
                payload = backup_mod.export_backup(
                    conn, config_mod.load())
            finally:
                conn.close()
            return str(backup_mod.write_export_file(path, payload))

        self._run(_blocking, done)

    def import_backup(self, path: str, done) -> None:
        def _blocking():
            from xx_email import backup as backup_mod

            payload = backup_mod.read_export_file(path)
            conn = _open_db()
            try:
                return backup_mod.restore_backup(
                    conn, payload,
                    data_dir=config_mod.data_dir())
            finally:
                conn.close()

        self._run(_blocking, done)


class _FoldersHooks:
    """Worker-backed folders callbacks (main loop never blocks)."""

    def __init__(self, app: EmailApplication, account: str) -> None:
        self._app = app
        self._account = account

    def _run(self, blocking, done) -> None:
        common_mod.run_in_background(self._app._executor, blocking,
                                     done)

    def load_state(self, done) -> None:
        def _blocking():
            conn = _open_db()
            try:
                from xx_email import snooze as snooze_mod

                entries = []
                for item in db_mod.all_accounts(conn):
                    entries.append({
                        "email": item.email,
                        "snoozed": snooze_mod.pending_wake_count(
                            conn, item.email),
                        "queued": len(db_mod.open_outbox_for_account(
                            conn, item.email))})
                return (config_mod.load(), entries)
            finally:
                conn.close()

        self._run(_blocking, done)

    def show_folder(self, folder) -> None:
        app = self._app
        app._nav.pop()
        page = app._mailbox_page
        if page is None or page.account != self._account:
            app._push_mailbox(self._account)
            page = app._mailbox_page
        if page is not None:
            page.select_folder(folder)

    def switch_account(self, email: str) -> None:
        app = self._app
        app._nav.pop()
        app._nav.pop()
        app._push_mailbox(email)

    def add_account(self) -> None:
        self._app._push_setup(mode="add")

    def sign_out(self, email: str, revoke: bool) -> None:
        self._app._nav.pop()
        self._app.request_sign_out(email, revoke)

    def open_permissions(self) -> None:
        self._app._open_permissions()

    def go_back(self) -> None:
        self._app._nav.pop()


class _LogsHooks:
    """Worker-backed log callbacks."""

    def __init__(self, app: EmailApplication) -> None:
        self._app = app

    def load_logs(self, done) -> None:
        def _blocking():
            from xx_email import log as log_mod

            log_mod.init_default()
            return log_mod.get().share_text()

        common_mod.run_in_background(self._app._executor, _blocking,
                                     done)

    def go_back(self) -> None:
        self._app._nav.pop()


class _ThreadHooks:
    """Worker-backed thread callbacks."""

    def __init__(self, app: EmailApplication, account: str,
                 thread_id: str) -> None:
        self._app = app
        self._account = account
        self._thread_id = thread_id

    def _run(self, blocking, done) -> None:
        common_mod.run_in_background(self._app._executor, blocking, done)

    def load_thread(self, done) -> None:
        account = self._account
        thread_id = self._thread_id

        def _blocking():
            conn = _open_db()
            try:
                thread = db_mod.get_thread(conn, account, thread_id)
                if thread is None:
                    raise LookupError(f"No thread {thread_id}")
                messages = db_mod.messages_for_thread(conn, account,
                                                      thread_id)
                try:
                    client, worker = _gmail_client(account)
                except Exception:
                    client, worker = None, None
                try:
                    if client is None:
                        entries = [_cached_entry(message)
                                   for message in messages]
                    else:
                        entries = [_full_entry(conn, account, client,
                                               message)
                                   for message in messages]
                finally:
                    if worker is not None:
                        worker.close()
                return (thread, messages, entries)
            finally:
                conn.close()

        self._run(_blocking, done)

    def mark_read(self) -> None:
        account = self._account
        thread_id = self._thread_id

        def _blocking():
            conn = _open_db()
            try:
                undo = mailbox_mod.apply_local_action(
                    conn, account, "MARK_READ", [thread_id])
            finally:
                conn.close()
            try:
                _run_server_ops(account, undo.server_ops)
            except Exception:
                pass
            return True

        self._run(_blocking, lambda _r: None)

    def perform(self, action, ids, done, starred: bool = False) -> None:
        account = self._account

        def _blocking():
            conn = _open_db()
            try:
                undo = mailbox_mod.apply_local_action(
                    conn, account, action, list(ids))
            finally:
                conn.close()
            try:
                _run_server_ops(account, undo.server_ops)
            except Exception:
                _revert_local(account, undo)
                raise
            return {"undo": undo}

        self._run(_blocking, done)

    def thread_undone(self, undo) -> None:
        self._app._pending_undo = None
        self._app.toast(undo.message,
                         lambda: self._revert_undo(undo))

    def _revert_undo(self, undo) -> None:
        def _blocking():
            conn = _open_db()
            try:
                undo.revert(conn)
                return True
            finally:
                conn.close()

        self._run(_blocking, lambda _r: self._app._nav.pop())

    def open_reply(self, mode: str, subject: str, quote: str) -> None:
        _ = subject
        self._app._push_compose(self._account, self._thread_id,
                                quote, mode)

    def open_attachment(self, attachment) -> None:
        account = self._account
        app = self._app

        def _blocking():
            client, worker = _gmail_client(account)
            try:
                payload = client.get_attachment(attachment.message_id,
                                                attachment.attachment_id)
                data = bodies_mod.decode_base64url_bytes(
                    payload.data or "")
                if data is None:
                    raise ValueError("Attachment had no data")
                return _save_attachment(attachment.filename, data)
            finally:
                worker.close()

        def _done(result) -> None:
            if isinstance(result, Exception):
                app.toast("Couldn't download attachment", None)
                return
            try:
                Gio.AppInfo.launch_default_for_uri(result, None)
            except Exception:
                app.toast(f"Saved to {result}", None)

        self._run(_blocking, _done)

    def go_back(self) -> None:
        self._app._nav.pop()


class _SearchHooks:
    """Worker-backed search callbacks."""

    def __init__(self, app: EmailApplication, account: str) -> None:
        self._app = app
        self._account = account

    def search(self, query: str, done) -> None:
        """Operator queries hit the server; transport failures fall back.

        Returns (threads, is_server, offline): when the server cannot
        be reached, downloaded FTS rows show with the offline note
        instead of a bare failure.
        """
        account = self._account

        def _blocking():
            conn = _open_db()
            try:
                if mailbox_mod.should_search_server(query):
                    try:
                        return (_server_search(conn, account, query),
                                True, False)
                    except Exception as exc:
                        if not _is_offline(exc):
                            raise
                        local = mailbox_mod.search_local_threads(
                            conn, account, query)
                        return (local, False, True)
                local = mailbox_mod.search_local_threads(conn, account,
                                                         query)
                if local:
                    return (local, False, False)
                try:
                    return (_server_search(conn, account, query),
                            True, False)
                except Exception as exc:
                    if not _is_offline(exc):
                        raise
                    return (local, False, True)
            finally:
                conn.close()

        common_mod.run_in_background(self._app._executor, _blocking,
                                     done)

    def open_thread(self, thread_id: str) -> None:
        self._app._push_thread(self._account, thread_id)

    def go_back(self) -> None:
        self._app._nav.pop()


class _ComposeHooks:
    """Worker-backed compose callbacks (main loop never blocks).

    Prefill, MIME composition, payload files and Gmail uploads all
    run on the app's worker pool; the page only renders fields and
    chips. Drafts autosave to the data dir and restore when the same
    compose identity reopens.
    """

    def __init__(self, app: EmailApplication, account: str,
                 thread_id: str = "", quote_id: str = "",
                 mode: str = "new", initial_to: str = "",
                 initial_cc: str = "",
                 initial_subject: str = "") -> None:
        self._app = app
        self._account = account
        self._thread_id = thread_id or ""
        self._quote_id = quote_id or ""
        self._mode = mode or "new"
        self._initial_to = initial_to or ""
        self._initial_cc = initial_cc or ""
        self._initial_subject = initial_subject or ""
        self._prefill = None
        self._offered: list = []
        self._uploads: list[str] = []

    def _run(self, blocking, done) -> None:
        common_mod.run_in_background(self._app._executor, blocking, done)

    def _uploads_dir(self):
        cache = setup_mod.ensure_app_dirs()["cache"] / "uploads"
        cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        return cache

    def load_initial(self, done) -> None:
        account = self._account
        thread_id = self._thread_id
        quote_id = self._quote_id
        mode = self._mode

        def _blocking():
            conn = _open_db()
            try:
                row = None
                if quote_id:
                    row = db_mod.get_message(conn, account, quote_id)
                if row is None:
                    original = compose_mod.OriginalMessage()
                else:
                    original = compose_mod.OriginalMessage(
                        id=row.id, thread_id=row.thread_id,
                        subject=row.subject,
                        from_address=row.from_address,
                        to_csv=row.to_csv, cc_csv=row.cc_csv,
                        date=row.date, snippet=row.snippet,
                        body_plain=row.body_plain,
                        message_id_header=row.message_id_header,
                        has_attachments=row.has_attachments)
                prefill = compose_mod.prefill_for_mode(
                    account, mode, original,
                    initial_to=self._initial_to,
                    initial_cc=self._initial_cc,
                    initial_subject=self._initial_subject,
                    thread_id_arg=thread_id)
                self._prefill = prefill
                offered = []
                if prefill.offer_attachments and row is not None:
                    offered = bodies_mod.decode_attachments(
                        row.attachments_json)
                self._offered = list(offered)
                fields = {"to": prefill.to, "cc": prefill.cc,
                          "bcc": "", "subject": prefill.subject,
                          "body": prefill.body}
                saved = compose_mod.load_draft(
                    config_mod.data_dir(), account, thread_id,
                    quote_id, mode)
                if saved is not None and any(
                        (saved.get(key) or "") for key in fields):
                    for key in fields:
                        fields[key] = saved.get(key, "")
                initial = dict(
                    fields, thread_id=prefill.thread_id or "",
                    quote_id=quote_id, mode=mode,
                    in_reply_to=prefill.in_reply_to or "",
                    references=prefill.references or "",
                    offer_count=len(offered),
                    show_cc_bcc=bool(fields["cc"] or fields["bcc"]))
                return (initial, not fields["to"].strip())
            finally:
                conn.close()

        self._run(_blocking, done)

    def save_draft(self, fields: dict) -> None:
        account = self._account
        thread_id = self._thread_id
        quote_id = self._quote_id
        mode = self._mode
        snapshot = dict(fields)

        def _blocking():
            compose_mod.save_draft(config_mod.data_dir(), account,
                                   snapshot, thread_id, quote_id,
                                   mode)

        self._app._executor.submit(_blocking)

    def stage_upload(self, path: str, display_name: str,
                     done) -> None:
        def _blocking():
            stored, name, size = compose_mod.store_upload(
                self._uploads_dir(), display_name, path)
            self._uploads.append(str(stored))
            return {"path": str(stored), "name": name,
                    "mime": compose_mod.guess_mime(name),
                    "size": size}

        self._run(_blocking, done)

    def remove_staged(self, path) -> None:
        if path in self._uploads:
            self._uploads.remove(path)
        try:
            Path(str(path)).unlink(missing_ok=True)
        except OSError:
            pass

    def fetch_offered(self, done) -> None:
        account = self._account
        offered = list(self._offered)

        def _blocking():
            staged = []
            if not offered:
                return staged
            client, worker = _gmail_client(account)
            try:
                for meta in offered:
                    payload = client.get_attachment(
                        meta.message_id, meta.attachment_id)
                    data = bodies_mod.decode_base64url_bytes(
                        payload.data or "")
                    if data is None:
                        raise ValueError(
                            "Attachment had no data")
                    mime = meta.mime_type or compose_mod.guess_mime(
                        meta.filename)
                    safe = SafePaths.child_name(meta.filename) or \
                        "attachment"
                    stored = compose_mod.copy_staged_to_uploads(
                        self._uploads_dir(),
                        compose_mod.StagedAttachment(
                            name=safe, mime_type=mime,
                            data=data))
                    self._uploads.append(str(stored))
                    staged.append({
                        "path": str(stored),
                        "name": safe,
                        "mime": mime,
                        "size": len(data)})
            finally:
                worker.close()
            self._offered = []
            return staged

        self._run(_blocking, done)

    def decline_offer(self) -> None:
        self._offered = []

    def send(self, fields: dict, staged: list,
             scheduled_at, done) -> None:
        account = self._account
        prefill = self._prefill
        thread_id = prefill.thread_id if prefill is not None else None
        in_reply_to = prefill.in_reply_to \
            if prefill is not None else None
        references = prefill.references \
            if prefill is not None else None

        def _blocking():
            stored = config_mod.load()
            attachments = []
            for item in staged or []:
                data = Path(str(item["path"])).read_bytes()
                attachments.append(compose_mod.StagedAttachment(
                    name=item.get("name") or "attachment",
                    mime_type=item.get("mime") or
                    compose_mod.guess_mime(item.get("name") or ""),
                    data=data))
            conn = _open_db()
            try:
                entry_id = outbox_mod.enqueue_fields(
                    conn, config_mod.data_dir(), account,
                    fields.get("to", ""), fields.get("cc", ""),
                    fields.get("bcc", ""), fields.get("subject", ""),
                    fields.get("body", ""), thread_id=thread_id,
                    in_reply_to=in_reply_to, references=references,
                    attachments=attachments,
                    scheduled_at=scheduled_at,
                    undo_seconds=stored.undo_seconds)
            finally:
                conn.close()
            compose_mod.clear_draft(config_mod.data_dir(), account,
                                    self._thread_id, self._quote_id,
                                    self._mode)
            for item in staged or []:
                try:
                    Path(str(item["path"])).unlink(missing_ok=True)
                except OSError:
                    pass
            self._uploads = []
            return (entry_id, stored.undo_seconds,
                    scheduled_at is not None)

        self._run(_blocking, done)

    def undo_send(self, entry_id: int, done) -> None:
        def _blocking():
            conn = _open_db()
            try:
                return outbox_mod.cancel_queued_send(
                    conn, config_mod.data_dir(), entry_id)
            finally:
                conn.close()

        self._run(_blocking, done)

    def discard(self) -> None:
        account = self._account
        thread_id = self._thread_id
        quote_id = self._quote_id
        mode = self._mode
        uploads = list(self._uploads)
        self._uploads = []

        def _blocking():
            for path in uploads:
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass
            compose_mod.clear_draft(config_mod.data_dir(), account,
                                    thread_id, quote_id, mode)
            return True

        self._run(_blocking, lambda _r: self._app._nav.pop())

    def after_sent(self) -> None:
        self._app._nav.pop()


def _server_search(conn, account: str, query: str) -> list:
    lowered = query.lower()
    client, worker = _gmail_client(account)
    try:
        page = client.list_threads(
            query=query, max_results=25,
            include_spam_trash="in:trash" in lowered
            or "in:spam" in lowered)
        ids = [ref.id for ref in page.threads]
        engine, engine_worker = _engine_for(conn, account)
        try:
            engine.hydrate(account, ids)
        finally:
            engine.close()
            engine_worker.close()
        return [row for row in
                (db_mod.get_thread(conn, account, tid) for tid in ids)
                if row is not None]
    finally:
        worker.close()


def _cached_entry(message: db_mod.Message) -> dict:
    """Thread entry from the downloaded cache (offline, no grant)."""
    return {"message_id": message.id, "html": message.body_html,
            "plain": message.body_plain,
            "attachments": bodies_mod.decode_attachments(
                message.attachments_json)}


def _full_entry(conn, account: str, client, message: db_mod.Message
                ) -> dict:
    if message.body_fetched:
        return {"message_id": message.id, "html": message.body_html,
                "plain": message.body_plain,
                "attachments": bodies_mod.decode_attachments(
                    message.attachments_json)}
    full = client.get_message(message.id, fmt="full")
    payload = full.payload
    if payload is None:
        return {"message_id": message.id, "html": message.body_html,
                "plain": message.body_plain, "attachments": []}
    plain_part = MessageParts.find_body(payload, "text/plain")
    html_part = MessageParts.find_body(payload, "text/html")
    plain = bodies_mod.decode_base64url(
        plain_part.body.data if plain_part is not None
        and plain_part.body is not None else None)
    html_text = bodies_mod.decode_base64url(
        html_part.body.data if html_part is not None
        and html_part.body is not None else None)
    found = MessageParts.attachments(message.id, payload)
    metas = [bodies_mod.AttachmentMeta(
        message_id=part.message_id, attachment_id=part.attachment_id,
        filename=part.filename, mime_type=part.mime_type,
        size=part.size) for part in found]
    db_mod.update_message_bodies(
        conn, account, message.id, html_text, plain,
        json.dumps([{"messageId": meta.message_id,
                     "attachmentId": meta.attachment_id,
                     "filename": meta.filename,
                     "mimeType": meta.mime_type,
                     "size": meta.size} for meta in metas]))
    return {"message_id": message.id, "html": html_text,
            "plain": plain, "attachments": metas}


def _local_draft_rows(account: str) -> list:
    """Autosaved local drafts as thread-shaped rows for Drafts."""

    class _Row:
        pass

    rows = []
    try:
        drafts = compose_mod.list_drafts(config_mod.data_dir(),
                                         account)
    except Exception:
        return rows
    for draft in drafts:
        row = _Row()
        row.id = ("local-draft:" + draft.get("thread_id", "") + ":" +
                  draft.get("quote_id", "") + ":" +
                  draft.get("mode", "new"))
        subject = draft.get("subject", "") or "(no subject)"
        row.subject = subject
        to = draft.get("to", "")
        row.snippet = f"Draft to {to}" if to else "Draft"
        row.from_address = account
        row.from_name = ""
        row.date = draft.get("updated_at", 0)
        row.unread_count = 0
        row.starred = False
        row.has_attachments = False
        rows.append(row)
    return rows


def _open_local_draft(app: EmailApplication, account: str,
                      thread_id: str) -> None:
    """Reopen an autosaved draft in compose (Drafts folder entry)."""
    try:
        _, thread, quote, mode = thread_id.split(":", 3)
    except ValueError:
        return
    app._push_compose(account, thread, quote, mode or "new")


def _save_attachment(filename: str, data: bytes) -> str:
    from xx_email.domain import SafePaths

    cache = setup_mod.ensure_app_dirs()["cache"] / "attachments"
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = SafePaths.child_name_or(filename, "attachment", "attachment")
    target = cache / name
    if not SafePaths.is_inside(cache, target):
        raise ValueError("Attachment name escapes its directory")
    target.write_bytes(bytes(data))
    return target.as_uri()


def main(argv=None) -> int:
    args = list(sys.argv if argv is None else argv)
    if "--daemon-tick" in args[1:]:
        from xx_email import daemon as daemon_mod

        return daemon_mod.run_daemon_tick()
    if "--actions-service" in args[1:]:
        from xx_email import actions as actions_mod

        return actions_mod.run()
    app = EmailApplication()
    return app.run(args)
