"""New-message page: recipient picker over contacts search + typed number.

Ports the F3 surface (``ui/NewConversationActivity``): contact-name
search through the shared resolver plus a free number field. Starting a
thread only finds or creates the conversation row — messages still go
through the daemon, never around it.
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from xx_ui import widgets  # noqa: E402
from txxt.conversations import tap_to_focus  # noqa: E402


class NewConversationPage(Adw.NavigationPage):
    """Recipient picker that opens (or creates) a thread on selection."""

    def __init__(
        self, navigation: Adw.NavigationView, parent_window: Gtk.Window,
        store, resolver=None, on_open_thread=None,
    ) -> None:
        super().__init__()
        self._navigation = navigation
        self._window = parent_window
        self._store = store
        self._resolver = resolver
        self._on_open_thread = on_open_thread
        self._query_seq = 0
        self.set_title("New message")

        header = widgets.compact_header(
            self._on_back, "New message", "SMS and MMS",
        )
        body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 16)

        # One To field: typing a name lists matching contacts under it,
        # typing a number lets Start open that thread (no second search).
        to_group = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
        to_group.append(widgets.section_label("To"))
        self._number_entry = widgets.text_entry("Name or number")
        self._number_entry.set_hexpand(True)
        tap_to_focus(self._number_entry)
        self._number_entry.connect("changed", self._on_to_changed)
        self._number_entry.connect("activate",
                                   lambda _e: self._on_start_clicked())
        to_group.append(self._number_entry)
        self._start_button = widgets.primary_button(
            "Start conversation", self._on_start_clicked)
        self._start_button.set_sensitive(False)
        to_group.append(self._start_button)
        body.append(to_group)

        # Contact hits for what the To field holds; nothing at all
        # (no "no matches" line) when none match, as when a number is typed.
        self._rows = Gtk.ListBox()
        self._rows.set_selection_mode(Gtk.SelectionMode.NONE)
        self._rows.connect("row-activated", self._on_hit_activated)
        self._rows.set_visible(False)
        body.append(self._rows)

        self.set_child(widgets.page(header, body, None))

    # ------------------------------------------------------------ loading

    def set_resolver(self, resolver) -> None:
        self._resolver = resolver

    def _on_back(self) -> None:
        try:
            self._navigation.pop()
        except Exception:
            pass

    def _on_to_changed(self, entry) -> None:
        text = entry.get_text()
        self._start_button.set_sensitive(_dialable(text))
        self._on_query_changed(text)

    def _on_query_changed(self, text: str) -> None:
        self._query_seq += 1
        seq = self._query_seq
        resolver = self._resolver
        worker = threading.Thread(
            target=self._search_in_worker, args=(seq, text, resolver),
            daemon=True,
        )
        worker.start()

    def _search_in_worker(self, seq: int, query: str, resolver) -> None:
        try:
            text = (query or "").strip()
            hits = resolver.search(text, 20) if resolver is not None and text else []
        except Exception:
            hits = []
        GLib.idle_add(self._apply_hits, seq, query, hits)

    def _apply_hits(self, seq: int, query: str, hits) -> bool:
        if seq != self._query_seq:
            return False
        while True:
            row = self._rows.get_row_at_index(0)
            if row is None:
                break
            self._rows.remove(row)
        for hit in hits or []:
            try:
                from txxt import phone_numbers as _phones

                title = _phones.format_display_configured(
                    hit.display) or hit.display
                subtitle = _phones.format_display_configured(
                    hit.number) or hit.number
            except Exception:
                title, subtitle = hit.display, hit.number
            button = widgets.list_row(
                title, subtitle,
                leading=widgets.avatar(_initials(hit.display), 40),
                on_activate=self._on_hit_chosen(hit.number),
            )
            row = Gtk.ListBoxRow()
            row.set_child(button)
            row._txxt_number = hit.number
            self._rows.append(row)
        self._rows.set_visible(bool(hits))
        return False

    # ------------------------------------------------------------ actions

    def _on_hit_chosen(self, number: str):
        def _chosen() -> None:
            self._open_thread_for(number)

        return _chosen

    def _on_hit_activated(self, _box, row: Gtk.ListBoxRow) -> None:
        number = getattr(row, "_txxt_number", "") or ""
        if number.strip():
            self._open_thread_for(number)

    def _on_start_clicked(self) -> None:
        number = self._number_entry.get_text().strip()
        if not _dialable(number):
            return
        self._open_thread_for(number)

    def _open_thread_for(self, number: str) -> None:
        try:
            conversation_id = self._store.find_or_create_conversation(number)
        except Exception:
            return
        if self._on_open_thread is not None:
            try:
                self._on_open_thread(int(conversation_id))
            except Exception:
                pass


def _dialable(text: str) -> bool:
    """A typed number or short code (digits, maybe +, spaces, dashes,
    dots, parentheses): a name has to be picked from its contact row."""
    text = (text or "").strip()
    return (any(ch.isdigit() for ch in text)
            and all(ch.isdigit() or ch in "+-.() " for ch in text))


def _initials(display: str) -> str:
    words = [word for word in (display or "").split() if word[:1].isalpha()]
    return "".join(word[0] for word in words[:2]).upper() or "?"
