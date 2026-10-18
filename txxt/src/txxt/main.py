"""TxxT phone UI: conversation list + thread on an ``Adw.NavigationView``.

The UI reads the SQLite store for display and talks to the daemon
over ``io.piercingxx.TxxT1`` (``TxxtClient``: Send / MarkRead /
OpenThread / MessagesChanged) — never the modem stack, never message
rows behind the daemon. Refreshes ride the ``MessagesChanged``
signal; nothing polls.

Launches: ``txxt --open-thread ID``, the
``io.piercingxx.TxxT://thread/ID`` URI the daemon's notification
action fires, and ``sms:``/``smsto:``/``mms:``/``mmsto:`` links (the
.desktop ``Exec`` carries ``%U`` so the URI lands in argv; ``do_open``
covers bus-activated opens).

Phone rules: the shared ``xx_ui`` design system paints everything
(``xx_ui.apply`` once here; the shell theme is the single theme
source); the window is chromeless and no entry autofocuses — list
search and compose stay unfocusable until tapped.
"""

from __future__ import annotations

import os
from pathlib import Path

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')

from gi.repository import Adw, Gio, Gdk  # noqa: E402

import xx_ui  # noqa: E402
from txxt import APP_ID  # noqa: E402
from txxt import list_presenter as presenter  # noqa: E402
from txxt.conversations import ConversationListPage  # noqa: E402
from txxt.store import Store  # noqa: E402
from txxt.thread_page import ThreadPage, sweep_staged_photos  # noqa: E402
from txxt.txxt_client import TxxtClient  # noqa: E402


def data_dir() -> Path:
    root = os.environ.get('XDG_DATA_HOME') or str(Path.home() / '.local' / 'share')
    return Path(root) / 'txxt'


def ensure_data_dir() -> Path:
    """Create the XDG data dir (mode 0700) before SQLite opens in it."""
    target = data_dir()
    target.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(target, 0o700)
    except OSError:
        pass
    try:
        sweep_staged_photos()
    except Exception:
        pass
    return target


class TxxtWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, open_thread_id: int | None = None) -> None:
        super().__init__(application=app)
        self.set_title('TxxT')
        self.set_decorated(False)
        self.set_default_size(360, 720)
        # The suite floor: a phone in landscape (1000x450) is shorter
        # than any fixed portrait height.
        self.set_size_request(320, 200)
        xx_ui.apply(Gdk.Display.get_default())
        self._store = Store(str(ensure_data_dir() / 'txxt.db'))
        self._client: TxxtClient | None = None
        self._thread_pages: list[ThreadPage] = []

        from txxt.contacts import session_resolver  # noqa: E402
        self._resolver = session_resolver(None)

        self._navigation = Adw.NavigationView()
        self._navigation.connect('popped', self._on_page_popped)
        self._list_page = ConversationListPage(
            self._navigation, self, self._store, self._resolver,
            None, self.open_thread,
        )
        self._navigation.push(self._list_page)
        self.set_content(self._navigation)

        Gio.bus_get(Gio.BusType.SESSION, None, self._on_bus_ready)
        self._blocking_page = None
        self._quarantine_page = None
        self._compose_page = None
        if open_thread_id is not None:
            self.open_thread(open_thread_id)

    def _on_bus_ready(self, _source, result) -> None:
        try:
            connection = Gio.bus_get_finish(result)
        except Exception:
            return
        from txxt.contacts import GioContactsBackend, session_resolver  # noqa: E402
        self._resolver = session_resolver(connection)
        self._client = TxxtClient(connection)
        try:
            self._list_page.set_resolver(self._resolver)
        except Exception:
            pass
        self._list_page.set_client(self._client)
        for page in list(self._thread_pages):
            try:
                page.set_resolver(self._resolver)
            except Exception:
                pass
            page.set_client(self._client)
        for page in (self._blocking_page, self._quarantine_page, self._compose_page):
            if page is not None:
                try:
                    page.set_resolver(self._resolver)
                except Exception:
                    pass
        try:
            backend = GioContactsBackend(connection)
            backend.subscribe_changed(self._list_page.handle_contacts_changed)
            for page in list(self._thread_pages):
                try:
                    backend.subscribe_changed(page.handle_contacts_changed)
                except Exception:
                    pass
            for page in (self._blocking_page, self._quarantine_page):
                if page is not None:
                    try:
                        backend.subscribe_changed(page.handle_contacts_changed)
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            self._list_page.refresh()
        except Exception:
            pass
        self._client.subscribe_messages_changed(self._on_messages_changed)

    def _on_messages_changed(self, conversation_ids) -> None:
        self._list_page.refresh_for_signal(conversation_ids)
        for page in list(self._thread_pages):
            try:
                page.refresh_for_signal(conversation_ids)
            except Exception:
                pass

    def _on_page_popped(self, _navigation, _page) -> bool:
        self._thread_pages = [
            page for page in self._thread_pages if page.get_visible()
        ]
        return False

    def open_settings(self):
        from txxt.settings_page import SettingsPage  # noqa: E402

        page = SettingsPage(
            self._navigation, self, self._store,
            on_changed=self._on_settings_changed,
            on_open_blocking=lambda: self.open_blocking(),
            on_open_quarantine=lambda: self.open_quarantine(),
        )
        self._navigation.push(page)
        return page

    def _on_settings_changed(self) -> None:
        try:
            self._list_page.refresh()
        except Exception:
            pass

    def open_blocking(self) -> None:
        from txxt.blocking_page import BlockingPage  # noqa: E402

        if self._blocking_page is None or not self._blocking_page.get_visible():
            self._blocking_page = BlockingPage(
                self._navigation, self, self._store, self._resolver,
                on_changed=self._on_filter_pages_changed,
            )
            self._navigation.push(self._blocking_page)
        else:
            self._navigation.pop_to_page(self._blocking_page)

    def open_quarantine(self) -> None:
        from txxt.quarantine_page import QuarantinePage  # noqa: E402

        if self._quarantine_page is None or not self._quarantine_page.get_visible():
            self._quarantine_page = QuarantinePage(
                self._navigation, self, self._store, self._resolver,
                on_changed=self._on_filter_pages_changed,
            )
            self._navigation.push(self._quarantine_page)
        else:
            self._navigation.pop_to_page(self._quarantine_page)
        try:
            self._quarantine_page.refresh()
        except Exception:
            pass

    def open_new_conversation(self) -> None:
        from txxt.new_conversation_page import NewConversationPage  # noqa: E402

        if self._compose_page is None or not self._compose_page.get_visible():
            self._compose_page = NewConversationPage(
                self._navigation, self, self._store, self._resolver,
                self.open_thread,
            )
            self._navigation.push(self._compose_page)
        else:
            self._navigation.pop_to_page(self._compose_page)

    def _on_filter_pages_changed(self) -> None:
        try:
            self._list_page.refresh()
        except Exception:
            pass

    def open_thread(self, conversation_id: int):
        try:
            cid = int(conversation_id)
        except (TypeError, ValueError):
            return None
        if cid <= 0:
            return None
        for page in self._thread_pages:
            if page.conversation_id == cid and page.get_visible():
                self._navigation.pop_to_page(page)
                return page
        page = ThreadPage(
            self._navigation, self, self._store, self._resolver,
            self._client, cid,
        )
        self._thread_pages.append(page)
        self._navigation.push(page)
        return page

    def open_sms_target(self, target) -> None:
        """Open the thread for an ``sms:``-family link.

        The existing thread for that address (canonical-number match)
        opens, otherwise a new conversation starts pre-filled with the
        address and the link body. Threads are 1:1, so with several
        recipients the first one wins.
        """
        numbers = [str(item).strip() for item in target.recipients or ()]
        numbers = [item for item in numbers if item]
        if not numbers:
            return
        from txxt import phone_numbers as phone_module  # noqa: E402

        try:
            address = phone_module.canonical_dialable_configured(numbers[0])
        except Exception:
            address = numbers[0]
        if not address.strip():
            return
        try:
            conversation_id = int(
                self._store.find_or_create_conversation(address))
        except Exception:
            return
        page = self.open_thread(conversation_id)
        if page is not None and target.body:
            try:
                page.prefill_draft(target.body)
            except Exception:
                pass


class TxxtApplication(Adw.Application):
    def __init__(self, open_thread_id: int | None = None,
                 sms_target=None) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_OPEN,
        )
        self._pending_thread = open_thread_id
        self._pending_sms = sms_target

    def _deliver_pending(self, window) -> None:
        if self._pending_thread is not None:
            try:
                window.open_thread(self._pending_thread)
            except Exception:
                pass
            self._pending_thread = None
        if self._pending_sms is not None:
            target, self._pending_sms = self._pending_sms, None
            try:
                window.open_sms_target(target)
            except Exception:
                pass

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is None:
            window = TxxtWindow(self, self._pending_thread)
            self._pending_thread = None
        window.present()
        self._deliver_pending(window)

    def do_open(self, files, _hint: str) -> None:
        for item in files or []:
            try:
                uri = item.get_uri()
            except Exception:
                continue
            found = presenter.parse_thread_uri(uri)
            if found is not None:
                self._pending_thread = found
                continue
            sms = presenter.parse_sms_uri(uri)
            if sms is not None:
                self._pending_sms = sms
        window = self.props.active_window
        if window is None:
            window = TxxtWindow(self, self._pending_thread)
            self._pending_thread = None
            window.present()
        else:
            window.present()
        self._deliver_pending(window)


def main(argv: list[str] | None = None) -> None:
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if '--daemon' in args:
        from txxt.daemon import main as daemon_main

        raise SystemExit(daemon_main(args))
    pending = presenter.parse_open_thread_argv(args)
    pending_sms = presenter.parse_sms_target_argv(args)
    app = TxxtApplication(pending, pending_sms)
    app.run([])
