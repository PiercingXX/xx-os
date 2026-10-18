"""Conversation list page: rows, list search, long-press actions.

The UI reads the SQLite store for display, sends through the daemon's
``io.piercingxx.TxxT1`` API (``TxxtClient``), and refreshes off the
``MessagesChanged`` signal — it never touches the modem stack and never
creates message rows behind the daemon. Conversation-flag edits
(pin/mute/archive/star/delete, mark unread) have no daemon method, so
the page applies them to its own store copy and re-reads; the
daemon's retry queue keys on message ids, where a removed row is a
harmless no-op.

Phone rules: ``Gtk.ListView`` with single-click activate over
``Gtk.NoSelection`` (an autoselecting selection model would pick row 0);
long-press resolves the row under the pointer (pick + walk up), never
the selection; no header-bar chrome (``xx_ui`` page header); the search
entry stays unfocusable until tapped so the on-screen keyboard never
pops on its own. Import-safe without a display: building the page
needs one, importing this module must not.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
gi.require_version("Gdk", "4.0")

import threading  # noqa: E402
import time  # noqa: E402

from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

from txxt import blocking as blocking_mod  # noqa: E402
from xx_ui import widgets  # noqa: E402
from txxt import list_presenter as presenter  # noqa: E402

FILTERS = ("all", "unread", "starred")


class ConversationItem(GObject.Object):
    """One ``Gio.ListStore`` entry for the conversation list."""

    __gtype_name__ = "TxxtConversationItem"
    conversation_id = GObject.Property(type=GObject.TYPE_INT64, default=0)
    title = GObject.Property(type=str, default="")
    snippet = GObject.Property(type=str, default="")
    stamp = GObject.Property(type=str, default="")
    unread = GObject.Property(type=int, default=0)
    address = GObject.Property(type=str, default="")
    marks = GObject.Property(type=str, default="")


def resolve_conversation_id(picked) -> int | None:
    """Conversation id for the widget under a pointer gesture.

    Selection, focus and pointer position are independent in GTK4, so
    a long-press must resolve its own target: walk up ``get_parent()``
    from the picked widget to the row box, which carries the id set at
    bind time.
    """
    widget = picked
    while widget is not None:
        for attr in ("_txxt_cid", "conversation_id", "txxt_cid", "cid"):
            try:
                value = getattr(widget, attr, None)
            except Exception:
                value = None
            if isinstance(value, int) and value > 0:
                return value
            if isinstance(value, bool):
                continue
        get_parent = getattr(widget, "get_parent", None)
        if not callable(get_parent):
            return None
        try:
            widget = get_parent()
        except Exception:
            return None
    return None


def tap_to_focus(entry: Gtk.Editable) -> None:
    """Focusable only on an explicit tap (never at startup or on pop).

    The entry starts unfocusable so window activation and
    ``Adw.NavigationView`` focus restore cannot grab it (which would
    pop the on-screen keyboard). A CAPTURE-phase click arms it and
    grabs focus; leaving focus disarms it again.
    """
    entry.set_can_focus(False)
    tap = Gtk.GestureClick.new()
    tap.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

    def _on_tapped(_gesture, _n_press, _x, _y) -> None:
        entry.set_can_focus(True)
        entry.grab_focus()

    tap.connect("pressed", _on_tapped)
    entry.add_controller(tap)
    focus = Gtk.EventControllerFocus.new()

    def _on_leave(_controller) -> None:
        entry.set_can_focus(False)

    focus.connect("leave", _on_leave)
    entry.add_controller(focus)


def avatar_for(title: str) -> Gtk.Label:
    """Avatar disc for a row title: initials, or a dashed disc for numbers."""
    if any(char.isalpha() for char in title):
        words = [word for word in title.split() if word[:1].isalpha()]
        initials = "".join(word[0] for word in words[:2]).upper() or "?"
        return widgets.avatar(initials, 40)
    return widgets.avatar("", 40, dashed=True)


def launch_uri(parent: Gtk.Window | None, uri: str) -> None:
    """Open a tel:/sms: URI with whichever handler owns it."""
    launcher = Gtk.UriLauncher.new(uri)
    launcher.launch(parent, None, _on_uri_launched)


def _on_uri_launched(
    launcher: Gtk.UriLauncher, result: Gio.AsyncResult,
) -> None:
    try:
        launcher.launch_finish(result)
    except GLib.Error:
        pass


class ConversationListPage(Adw.NavigationPage):
    """Root page: searchable conversation list over the store."""

    def __init__(
        self, navigation: Adw.NavigationView, parent_window: Gtk.Window,
        store, resolver=None, client=None, on_open_thread=None,
    ) -> None:
        super().__init__()
        self._navigation = navigation
        self._window = parent_window
        self._store = store
        self._resolver = resolver
        self._client = client
        self._on_open_thread = on_open_thread
        self._views: list = []
        self._query_seq = 0
        self._filter = "all"
        try:
            from txxt.contacts import DisplayNameCache

            self._names = DisplayNameCache(resolver)
        except Exception:
            self._names = None

        self._header = widgets.page_header(
            "Inbox", "Threads",
            icon_name="list-add-symbolic",
            icon_cb=self._on_compose_clicked,
            overflow_cb=self._on_overflow_clicked,
        )
        self._caption = self._header.get_last_child()

        chips_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self._chips_wrap = chips_box
        self._rebuild_chips()
        held = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self._quarantine_banner = Gtk.Button()
        self._quarantine_banner.add_css_class("xx-card")
        banner_row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        banner_row.set_valign(Gtk.Align.CENTER)
        shield = widgets.icon_image("dialog-warning-symbolic")
        shield.set_valign(Gtk.Align.CENTER)
        banner_row.append(shield)
        self._banner_label = widgets.label("Held for review", "xx-title")
        self._banner_label.set_hexpand(True)
        self._banner_label.set_valign(Gtk.Align.CENTER)
        banner_row.append(self._banner_label)
        chevron = widgets.icon_image("go-next-symbolic")
        chevron.set_valign(Gtk.Align.CENTER)
        banner_row.append(chevron)
        self._quarantine_banner.set_child(banner_row)
        self._quarantine_banner.set_visible(False)
        self._quarantine_banner.connect("clicked", self._on_quarantine_clicked)
        held.append(self._quarantine_banner)

        self._model = Gio.ListStore.new(ConversationItem)
        self._selection = Gtk.NoSelection.new(self._model)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_item_setup)
        factory.connect("bind", self._on_item_bind)
        self._list = Gtk.ListView.new(self._selection, factory)
        self._list.set_single_click_activate(True)
        self._list.set_vexpand(True)
        right_click = Gtk.GestureClick.new()
        right_click.set_button(3)
        right_click.connect("pressed", self._on_row_right_clicked)
        self._list.add_controller(right_click)
        long_press = Gtk.GestureLongPress.new()
        long_press.connect("pressed", self._on_row_long_pressed)
        self._list.add_controller(long_press)

        body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        body.append(chips_box)
        body.append(held)
        rows_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        rows_box.append(self._list)
        body.append(rows_box)
        self._empty_wrap = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self._empty = widgets.empty_state("No conversations yet.")
        self._empty.set_vexpand(True)
        self._empty.set_visible(False)
        self._empty_wrap.append(self._empty)
        body.append(self._empty_wrap)

        search = widgets.search_bar(
            "Search messages", on_changed=self._on_query_changed,
        )
        self._search = _first_entry(search)
        tap_to_focus(self._search)
        self.set_child(widgets.page(self._header, body, search))
        self.set_title("Inbox")

        self._list.connect("activate", self._on_row_activated)

        self.refresh()

    # ------------------------------------------------------------ loading

    def set_client(self, client) -> None:
        self._client = client

    def set_resolver(self, resolver) -> None:
        self._resolver = resolver
        try:
            self._names.set_resolver(resolver)
        except Exception:
            pass

    def refresh(self) -> None:
        """Re-read the store on a worker; results post back via idle."""
        self._query_seq += 1
        seq = self._query_seq
        query = self._search.get_text()
        worker = threading.Thread(
            target=self._load_in_worker, args=(seq, query), daemon=True,
        )
        worker.start()

    def refresh_for_signal(self, conversation_ids) -> None:
        _ = conversation_ids
        self.refresh()

    def handle_contacts_changed(
        self, _conn, _sender, _path, _iface, _signal, params, _user_data,
    ) -> None:
        try:
            self._names.handle_contacts_changed(
                _conn, _sender, _path, _iface, _signal, params, _user_data,
            )
        except Exception:
            pass
        GLib.idle_add(self._refresh_from_idle)

    def _refresh_from_idle(self) -> bool:
        try:
            self.refresh()
        except Exception:
            pass
        return False

    def _display_name(self, number: str) -> str:
        try:
            return self._names.fast(number)
        except Exception:
            pass
        if self._resolver is not None:
            try:
                return self._resolver.display_name(number)
            except Exception:
                pass
        try:
            from txxt import phone_numbers as _phones

            return _phones.format_display_configured(number) or number
        except Exception:
            return number

    def _blocking_display_name(self, number: str) -> str:
        try:
            return self._names.resolve_blocking(number)
        except Exception:
            pass
        return self._display_name(number)

    def _load_in_worker(self, seq: int, query: str) -> None:
        try:
            rows = self._store.get_all_conversations()
            by_thread = {
                row.id: self._store.get_messages_for_conversation(row.id)
                for row in rows
            }
            now = int(time.time() * 1000)
            views = presenter.present_conversations(
                rows, by_thread.get, self._display_name, now,
            )
            held = sum(1 for row in rows if row.is_quarantined)
        except Exception:
            views = None
            held = 0
        GLib.idle_add(self._apply_views, seq, views, query, held)
        if views is None:
            return
        try:
            numbers: list[str] = []
            seen: set[str] = set()
            for view in views:
                address = getattr(view, "address", "") or ""
                if address.strip() and address not in seen:
                    seen.add(address)
                    numbers.append(address)
        except Exception:
            numbers = []
        if not numbers or self._resolver is None:
            return
        try:
            for number in numbers:
                self._names.resolve_blocking(number)
        except Exception:
            return
        try:
            rows = self._store.get_all_conversations()
            by_thread = {
                row.id: self._store.get_messages_for_conversation(row.id)
                for row in rows
            }
            now = int(time.time() * 1000)
            resolved = presenter.present_conversations(
                rows, by_thread.get, self._display_name, now,
            )
            held_again = sum(1 for row in rows if row.is_quarantined)
        except Exception:
            return
        GLib.idle_add(self._apply_views, seq, resolved, query, held_again)

    def _apply_views(self, seq: int, views, query: str, held: int = 0) -> bool:
        if seq != self._query_seq:
            return False
        if views is None:
            return False
        self._views = views
        self._apply_filter(query)
        try:
            count = int(held)
        except (TypeError, ValueError):
            count = 0
        self._banner_label.set_text(f"{count} held for review")
        self._quarantine_banner.set_visible(count > 0)
        return False

    # ------------------------------------------------------------ chrome

    def _rebuild_chips(self) -> None:
        kid = self._chips_wrap.get_first_child()
        while kid is not None:
            following = kid.get_next_sibling()
            self._chips_wrap.remove(kid)
            kid = following
        self._chips_wrap.append(widgets.chip_bar([
            (key.upper(), key == self._filter, self._on_chip_tapped(key))
            for key in FILTERS
        ]))

    def _on_chip_tapped(self, key: str):
        def _tapped() -> None:
            self._filter = key
            self._rebuild_chips()
            try:
                self._apply_filter(self._search.get_text())
            except Exception:
                pass

        return _tapped

    def _on_compose_clicked(self) -> None:
        try:
            self._window.open_new_conversation()
        except Exception:
            pass

    def _on_overflow_clicked(self) -> None:
        dialog = Adw.AlertDialog.new("Inbox", None)
        dialog.add_response("settings", "Settings")
        dialog.add_response("blocking", "Blocking")
        dialog.add_response("cancel", "Cancel")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.choose(
            self._window, None, self._on_overflow_chosen,
        )

    def _on_overflow_chosen(self, dialog, result) -> None:
        try:
            response = dialog.choose_finish(result)
        except GLib.Error:
            return
        if response == "settings":
            try:
                self._window.open_settings()
            except Exception:
                pass
        elif response == "blocking":
            try:
                self._window.open_blocking()
            except Exception:
                pass

    def _on_quarantine_clicked(self, _button) -> None:
        try:
            self._window.open_quarantine()
        except Exception:
            pass

    def _apply_filter(self, query: str) -> None:
        shown = presenter.filter_rows(self._views, query)
        if self._filter == "unread":
            shown = [view for view in shown if view.unread_count > 0]
        elif self._filter == "starred":
            shown = [view for view in shown if view.starred]
        self._model.remove_all()
        for view in shown:
            title = view.title + (" ★" if view.starred else "")
            self._model.append(ConversationItem(
                conversation_id=view.conversation_id,
                title=title,
                snippet=view.snippet + view.marks,
                stamp=presenter.format_time(
                    view.timestamp_millis, int(time.time() * 1000),
                ),
                unread=view.unread_count,
                address=view.address,
            ))
        total = len(self._views)
        try:
            self._caption.set_text(
                presenter.count_label(total, "thread").upper())
        except Exception:
            pass
        searching = bool((query or "").strip()) or self._filter != "all"
        self._empty.get_first_child().set_text(
            "No matches for that search." if searching
            else "No conversations yet.",
        )
        has_rows = len(shown) > 0
        self._list.set_visible(has_rows)
        self._empty.set_visible(not has_rows)

    # -------------------------------------------------------------- factory

    def _on_item_setup(self, _factory, list_item: Gtk.ListItem) -> None:
        leading = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        leading.set_valign(Gtk.Align.CENTER)
        middle = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
        middle.set_hexpand(True)
        middle.set_valign(Gtk.Align.CENTER)
        title = widgets.label("", "xx-title")
        middle.append(title)
        snippet = widgets.label("", "xx-secondary")
        middle.append(snippet)
        trailing = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
        trailing.set_valign(Gtk.Align.CENTER)
        stamp = widgets.label("", "xx-time", xalign=1.0)
        trailing.append(stamp)
        badge = widgets.count_badge("")
        badge.set_halign(Gtk.Align.END)
        trailing.append(badge)
        row_box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        row_box.add_css_class("xx-row")
        row_box.set_valign(Gtk.Align.CENTER)
        row_box.append(leading)
        row_box.append(middle)
        row_box.append(trailing)
        list_item.leading = leading
        list_item.title = title
        list_item.snippet = snippet
        list_item.stamp = stamp
        list_item.badge = badge
        list_item.row_box = row_box
        list_item.set_child(row_box)

    def _on_item_bind(self, _factory, list_item: Gtk.ListItem) -> None:
        item = list_item.get_item()
        list_item.set_activatable(True)
        list_item.title.set_text(item.title)
        list_item.snippet.set_text(item.snippet)
        list_item.stamp.set_text(item.stamp)
        if item.unread > 0:
            list_item.badge.set_text(str(item.unread))
            list_item.badge.set_visible(True)
        else:
            list_item.badge.set_visible(False)
        leading = list_item.leading
        kid = leading.get_first_child()
        while kid is not None:
            following = kid.get_next_sibling()
            leading.remove(kid)
            kid = following
        leading.append(avatar_for(item.title))
        list_item.row_box._txxt_cid = int(item.conversation_id)
        try:
            total = self._model.get_n_items()
            last = list_item.get_position() == total - 1
        except Exception:
            last = False
        if last:
            list_item.row_box.add_css_class("last")
        else:
            list_item.row_box.remove_css_class("last")

    # ---------------------------------------------------------------- input

    def _on_query_changed(self, text: str) -> None:
        self._apply_filter(text)

    def _on_row_activated(self, _view: Gtk.ListView, position: int) -> None:
        item = self._model.get_item(position)
        if item is None or int(item.conversation_id) <= 0:
            return
        if self._on_open_thread is not None:
            self._on_open_thread(int(item.conversation_id))

    def _cid_at_position(self, x: float, y: float) -> int | None:
        try:
            picked = self._list.pick(x, y, Gtk.PickFlags.DEFAULT)
        except Exception:
            return None
        return resolve_conversation_id(picked)

    def _view_for(self, conversation_id: int):
        for view in self._views:
            if view.conversation_id == conversation_id:
                return view
        return None

    def _on_row_right_clicked(self, _gesture, _n_press, x, y) -> None:
        cid = self._cid_at_position(x, y)
        if cid:
            self._show_row_menu(cid)

    def _on_row_long_pressed(self, _gesture, x, y) -> None:
        cid = self._cid_at_position(x, y)
        if cid:
            self._show_row_menu(cid)

    # ------------------------------------------------------------ row menu

    def _show_row_menu(self, conversation_id: int) -> None:
        view = self._view_for(conversation_id)
        title = view.title if view is not None else "Conversation"
        address = view.address if view is not None else ""
        try:
            from txxt import phone_numbers as _phones

            shown = _phones.format_display_configured(address)
        except Exception:
            shown = ""
        dialog = Adw.AlertDialog.new(
            title, shown if shown and shown != title else None)
        dialog.add_response("call", "Call")
        dialog.add_response("copy", "Copy number")
        if view is not None and view.unread_count > 0:
            dialog.add_response("read", "Mark read")
        else:
            dialog.add_response("unread", "Mark unread")
        dialog.add_response("pin", "Unpin" if self._is_pinned(conversation_id) else "Pin")
        dialog.add_response("mute", "Unmute" if self._is_muted(conversation_id) else "Mute")
        dialog.add_response("archive", "Archive")
        if view is not None and "Starred" in (view.marks or ""):
            dialog.add_response("unstar", "Remove star")
        else:
            dialog.add_response("star", "Add star")
        dialog.add_response("block", "Block")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.add_response("cancel", "Cancel")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.choose(
            self._window, None,
            lambda dlg, res: self._on_menu_chosen(
                dlg, res, conversation_id, address),
        )

    def _on_menu_chosen(self, dialog, result, conversation_id, address) -> None:
        try:
            response = dialog.choose_finish(result)
        except GLib.Error:
            return
        if response == "cancel":
            return
        if response == "call" and address:
            launch_uri(self._window, f"tel:{address}")
        elif response == "copy" and address:
            self.get_clipboard().set(address)
        elif response == "read":
            self._mark_read(conversation_id)
        elif response == "unread":
            self._run_store(lambda: self._store.mark_conversation_unread(conversation_id))
        elif response == "pin":
            self._run_store(lambda: self._store.toggle_pin(conversation_id))
        elif response == "mute":
            self._toggle_mute(conversation_id)
        elif response == "archive":
            self._run_store(lambda: self._set_archived(conversation_id, True))
        elif response in ("star", "unstar"):
            self._run_store(
                lambda: self._set_starred(address, response == "star"))
        elif response == "block":
            self._block_sender(address)
        elif response == "delete":
            self._confirm_delete(conversation_id)

    def _is_pinned(self, conversation_id: int) -> bool:
        view = self._view_for(conversation_id)
        return bool(view is not None and view.pinned)

    def _is_muted(self, conversation_id: int) -> bool:
        view = self._view_for(conversation_id)
        return bool(view is not None and view.muted)

    def _set_archived(self, conversation_id: int, archived: bool) -> None:
        row = self._store.get_conversation(conversation_id)
        if row is None:
            return
        flags = row.flags()
        flags = flags.archive() if archived else flags.unarchive()
        self._store.set_flags(conversation_id, flags)

    def _set_starred(self, address: str, starred: bool) -> None:
        if not address:
            return
        if starred:
            self._store.star(address)
        else:
            self._store.unstar(address)

    def _toggle_mute(self, conversation_id: int) -> None:
        row = self._store.get_conversation(conversation_id)
        if row is None:
            return
        flags = row.flags()
        flags = flags.unmute() if flags.is_muted else flags.mute()
        self._store.set_flags(conversation_id, flags)

    def _run_store(self, fn) -> None:
        worker = threading.Thread(target=self._store_in_worker, args=(fn,), daemon=True)
        worker.start()

    def _store_in_worker(self, fn) -> None:
        try:
            fn()
        except Exception:
            pass
        GLib.idle_add(self._after_store_write)

    def _after_store_write(self) -> bool:
        self.refresh()
        return False

    def _mark_read(self, conversation_id: int) -> None:
        if self._client is None:
            self._run_store(
                lambda: self._store.mark_conversation_read(conversation_id))
            return

        def _done(ok, _error) -> None:
            self.refresh()

        try:
            self._client.mark_read(conversation_id, _done)
        except Exception:
            self._run_store(
                lambda: self._store.mark_conversation_read(conversation_id))

    def _block_sender(self, address: str) -> None:
        if not address:
            return
        worker = threading.Thread(
            target=self._block_in_worker, args=(address,), daemon=True)
        worker.start()

    def _block_in_worker(self, address: str) -> None:
        try:
            self._store.blocklist_add("sender", address)
            done = True
        except Exception:
            done = False
        try:
            blocking_mod.live_apply_from_store(self._store)
        except Exception:
            pass
        if self._resolver is not None:
            try:
                self._resolver.set_blocked(address, True)
            except Exception:
                pass
        GLib.idle_add(self._announce_block, address, done)

    def _announce_block(self, address: str, done: bool) -> bool:
        try:
            from txxt import phone_numbers as _phones

            shown = _phones.format_display_configured(address) or address
        except Exception:
            shown = address
        heading = "Blocked" if done else "Could not block"
        body = (
            f"New messages from {shown} are blocked."
            if done else f"No contact holds {shown}; nothing changed."
        )
        dialog = Adw.AlertDialog.new(heading, body)
        dialog.add_response("ok", "OK")
        dialog.set_default_response("ok")
        dialog.set_close_response("ok")
        dialog.choose(self._window, None, self._on_dialog_dismissed)
        if done:
            self.refresh()
        return False

    def _confirm_delete(self, conversation_id: int) -> None:
        dialog = Adw.AlertDialog.new(
            "Delete conversation?",
            "Delete this thread and every message in it? This cannot be undone.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.choose(
            self._window, None,
            lambda dlg, res: self._on_delete_chosen(dlg, res, conversation_id),
        )

    def _on_delete_chosen(self, dialog, result, conversation_id: int) -> None:
        try:
            response = dialog.choose_finish(result)
        except GLib.Error:
            return
        if response != "delete":
            return
        self._run_store(lambda: self._store.delete_conversation(conversation_id))

    def _on_dialog_dismissed(self, dialog: Adw.AlertDialog, result) -> None:
        try:
            dialog.choose_finish(result)
        except GLib.Error:
            pass


def _first_entry(box: Gtk.Box) -> Gtk.Entry:
    """The text entry inside an ``xx_ui`` search bar."""
    kid = box.get_first_child()
    while kid is not None:
        if isinstance(kid, Gtk.Entry):
            return kid
        kid = kid.get_next_sibling()
    raise AssertionError("search bar has no entry")
