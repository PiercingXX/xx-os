"""Mailbox page: chip-filtered inbox over the L1/L2 store (thin view).

Ports MailboxScreen/MailboxViewModel over xx_email.mailbox (pure) and
the L1/L2 store: the five inbox tabs are a ChipBar filter over one
Gtk.ListView, folders/accounts live on the Folders page and the
header ⋯ menu, rows swipe and long-press into multi-select, and undo
toasts confirm every action. Store/network work enters through hooks
and always runs off the main loop; results land here for display.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

import time
from datetime import datetime

from gi.repository import Adw, Gtk

from xx_email import mailbox as mailbox_mod
from xx_email.domain import MailboxFolder, SnoozePresets
from xx_email.ui import common as common_mod
from xx_ui import widgets


class MailboxPage(Gtk.Box):
    """Chip-filtered inbox for one account."""

    def __init__(self, account: str, hooks, toast, swipe_left: str = "ARCHIVE",
                 swipe_right: str = "DELETE") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.account = account
        self._hooks = hooks
        self._toast = toast
        self._swipe_left = swipe_left
        self._swipe_right = swipe_right
        self._tab = mailbox_mod.tabs()[0]
        self._tabs = list(mailbox_mod.tabs())
        self._folder: MailboxFolder | None = None
        self._selection = mailbox_mod.Selection()

        # One refresh glyph, no menu: Folders and Options are tabs, and
        # accounts (switch, add, sign out, revoke) live in Folders and
        # Settings once each.
        self.append(widgets.page_header(
            "Inbox", account, icon_name="view-refresh-symbolic",
            icon_cb=lambda: self.refresh()))

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        body.set_vexpand(True)
        chips_pad = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        chips_pad.add_css_class("xx-page-body")
        body.append(chips_pad)
        self._chip_wrap = chips_pad
        self._paint_chips()

        banners = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._reauth_banner = self._banner(
            banners, "Sign in again to keep syncing",
            "Sign in", lambda _b: hooks.open_signin())
        self._failed_banner = self._banner(
            banners, "", "Retry", lambda _b: hooks.retry_failed())
        body.append(banners)

        self._sync_line = widgets.caption("")
        sync_pad = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sync_pad.add_css_class("xx-page-body")
        sync_pad.append(self._sync_line)
        body.append(sync_pad)

        self._store = common_mod.new_thread_store()
        view = common_mod.thread_list_view(
            self._store, self._row_activated, self._toggle_select,
            self._on_swipe, self._swipe_left, self._swipe_right)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER,
                            Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        scrolled.set_child(view)
        area = Gtk.Overlay()
        area.set_vexpand(True)
        area.set_child(scrolled)
        fab = widgets.fab(callback=lambda: hooks.open_compose(),
                          icon_name="list-add-symbolic")
        fab.set_margin_bottom(20)
        area.add_overlay(fab)
        body.append(area)

        self._selection_bar = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._selection_bar.add_css_class("xx-page-body")
        self._selection_bar.set_visible(False)
        self._selection_label = widgets.caption("0 selected")
        self._selection_bar.append(self._selection_label)
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_max_children_per_line(3)
        for label, action in (("Archive", "ARCHIVE"),
                              ("Delete", "DELETE"),
                              ("Unread", "MARK_UNREAD"),
                              ("Star", "STAR"),
                              ("Snooze", "SNOOZE"),
                              ("Clear", None)):
            button = widgets.outline_button(label)
            if action is None:
                button.connect("clicked",
                               lambda _b: self._clear_selection())
            else:
                button.connect(
                    "clicked",
                    lambda _b, a=action: self._on_bulk(a))
            flow.append(button)
        self._selection_bar.append(flow)
        body.append(self._selection_bar)

        more_pad = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        more_pad.add_css_class("xx-page-body")
        self._load_more_button = widgets.outline_button("Load more")
        self._load_more_button.connect(
            "clicked", lambda _b: self._on_load_more())
        self._load_more_button.set_visible(False)
        more_pad.append(self._load_more_button)
        body.append(more_pad)

        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        tabs = widgets.bottom_tabs([
            ("", "Inbox", True, None),
            ("", "Folders", False, lambda: hooks.open_folders()),
            ("", "Search", False, lambda: hooks.open_search()),
            ("", "Options", False,
             lambda: hooks.open_settings()),
        ])
        tabs.add_css_class("email-tabs")
        bottom.append(tabs)
        self.append(body)
        self.append(bottom)

    # ------------------------------------------------------------------ lists

    def _paint_chips(self) -> None:
        child = self._chip_wrap.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._chip_wrap.remove(child)
            child = next_child
        self._chip_wrap.append(widgets.chip_bar([
            (tab.title, tab == self._tab,
             lambda t=tab: self._select_tab(t))
            for tab in self._tabs
        ]))

    def _select_tab(self, tab) -> None:
        self._tab = tab
        self._selection.clear()
        self._paint_chips()
        self._paint_selection()
        self._reload_tab(tab)

    def _row_activated(self, thread_id: str, _n_press: int = 1) -> None:
        if len(self._selection):
            self._toggle_select(thread_id)
        else:
            self._hooks.open_thread(thread_id)

    def _toggle_select(self, thread_id: str) -> None:
        self._selection.toggle(thread_id)
        self._paint_selection()

    def _clear_selection(self) -> None:
        self._selection.clear()
        self._paint_selection()

    def _paint_selection(self) -> None:
        count = len(self._selection)
        self._selection_bar.set_visible(count > 0)
        self._selection_label.set_label(
            _caption(f"{count:d} selected"))
        for position in range(self._store.get_n_items()):
            item = self._store.get_item(position)
            item.selected = item.thread_id in self._selection

    # ------------------------------------------------------------------ data

    def load(self) -> None:
        self._reload_tab(self._tab)
        self._hooks.failed_count(self.set_failed)

    def _reload_tab(self, tab: MailboxFolder) -> None:
        store = self._store

        def _done(result) -> None:
            if isinstance(result, Exception):
                self._toast(f"Couldn't load {tab.title}", None)
                return
            threads, _more = result
            common_mod.replace_store(store, threads)

        self._hooks.load_folder(tab, _done)

    def select_folder(self, folder: MailboxFolder | None) -> None:
        self._folder = folder
        self._selection.clear()
        self._paint_selection()
        self._load_more_button.set_visible(False)
        if folder is None:
            self.load()
            return

        def _done(result) -> None:
            if isinstance(result, Exception):
                self._toast(f"Couldn't load {folder.title}", None)
                return
            if folder == MailboxFolder.OUTBOX:
                self._done_outbox(result)
                return
            threads, more = result
            common_mod.replace_store(self._store, threads)
            self._load_more_button.set_visible(bool(more))
            self._load_more_button.set_label("Load more")

        if folder == MailboxFolder.OUTBOX:
            self._hooks.load_outbox(self._done_outbox)
        else:
            self._hooks.load_folder(folder, _done)

    def _done_outbox(self, result) -> None:
        if isinstance(result, Exception):
            self._toast("Couldn't load Outbox", None)
            return
        entries, _more = result
        store = self._store
        store.splice(0, store.get_n_items(),
                     [common_mod.ThreadItem.from_thread(
                         _outbox_row(entry)) for entry in entries])
        self._load_more_button.set_visible(False)

    def _on_load_more(self) -> None:
        if self._folder is None:
            return
        self._load_more_button.set_label("Loading…")

        def _done(result) -> None:
            if isinstance(result, Exception):
                self._load_more_button.set_label("Load more")
                self._toast("Couldn't load more", None)
                return
            threads, more = result
            fresh = [common_mod.ThreadItem.from_thread(thread)
                     for thread in threads]
            self._store.splice(self._store.get_n_items(), 0, fresh)
            self._load_more_button.set_visible(bool(more))
            self._load_more_button.set_label("Load more")

        self._hooks.load_more(self._folder, _done)

    def refresh(self) -> None:
        self._sync_line.set_label(
            _caption(mailbox_mod.sync_status_line(syncing=True)))
        self._hooks.refresh(self._on_refreshed)

    def _on_refreshed(self, outcome) -> None:
        if isinstance(outcome, Exception):
            self._sync_line.set_label(
                mailbox_mod.sync_status_line(
                    {"ok": False, "error": "couldn't reach it"}).upper())
            self._toast("Sync failed", None)
            return
        reauth = bool(outcome.get("reauth", False))
        # A lost grant shows the sign-in banner; a toast would say it twice.
        if not outcome.get("ok", False) and not reauth:
            self._toast(outcome.get("error") or "Sync failed", None)
        self.set_reauth(reauth)
        # The reauth banner card carries the sign-in message; the caption
        # under it stays empty so it never reads twice.
        if reauth:
            self._sync_line.set_label("")
        else:
            self._sync_line.set_label(
                _caption(mailbox_mod.sync_status_line(outcome)))
        self.load()

    def set_reauth(self, needed: bool) -> None:
        self._reauth_banner.set_visible(needed)

    def set_failed(self, result) -> None:
        count = 0 if isinstance(result, Exception) else int(result or 0)
        self._failed_banner.set_visible(count > 0)
        if count > 0:
            self._failed_label.set_label(
                f"{count:d} send{'' if count == 1 else 's'} failed")

    # ---------------------------------------------------------------- actions

    def _on_swipe(self, action: str, thread_id: str) -> None:
        if action == "SNOOZE":
            self._open_snooze([thread_id])
        else:
            self._perform(action, [thread_id])

    def _on_bulk(self, action: str) -> None:
        ids = self._selection.ids()
        if not ids:
            return
        if action == "SNOOZE":
            self._open_snooze(ids)
        else:
            self._perform(action, ids)

    def _perform(self, action: str, ids: list[str], wake_at=None,
                 label_id=None) -> None:
        def _done(result) -> None:
            if isinstance(result, Exception):
                self._toast("Action failed — you stay put", None)
                self._reload_current()
                return
            self._toast(result.message,
                         lambda: self._revert(result))
            self._selection.clear()
            self._paint_selection()
            self._reload_current()

        self._hooks.perform(action, ids, wake_at, label_id, _done)

    def _revert(self, undo) -> None:
        def _done(result) -> None:
            if isinstance(result, Exception):
                self._toast("Undo failed", None)
                return
            self._reload_current()

        self._hooks.revert(undo, _done)

    def _reload_current(self) -> None:
        if self._folder is None:
            self._reload_tab(self._tab)
        else:
            folder = self._folder
            self.select_folder(None)
            self.select_folder(folder)

    # ---------------------------------------------------------------- sheets

    def _banner(self, parent, label_text: str, action_label: str,
                on_action):
        # The message reads whole on its own line(s) with the action under
        # it: side by side, large text squeezed it to a word per line.
        box = widgets.card()
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        label = common_mod.wrapped(Gtk.Label(label=label_text))
        label.add_css_class("xx-title")
        label.set_xalign(0.0)
        row.append(label)
        action = widgets.wrap_to_fit(widgets.outline_button(action_label))
        action.set_halign(Gtk.Align.START)
        action.connect("clicked", on_action)
        row.append(action)
        box.append(row)
        pad = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pad.add_css_class("xx-page-body")
        pad.append(box)
        pad.set_visible(False)
        parent.append(pad)
        if action_label == "Retry":
            self._failed_label = label
        return pad

    def _open_snooze(self, ids: list[str]) -> None:
        now = datetime.now().astimezone()
        presets = (
            ("Later today", SnoozePresets.later_today(now)),
            ("Tomorrow morning", SnoozePresets.tomorrow_morning(now)),
            ("Weekend", SnoozePresets.weekend(now)),
            ("Next week", SnoozePresets.next_week(now)),
        )
        dialog = Adw.Dialog()
        dialog.set_title("Snooze until…")
        content = widgets.dialog_box("Snooze until…")
        dialog.set_child(content)
        for label_text, moment in presets:
            button = widgets.menu_row(label_text)
            wake_at = int(moment.timestamp() * 1000)

            def _pick(_b, undo_ids=list(ids), at=wake_at) -> None:
                dialog.close()
                self._perform("SNOOZE", undo_ids, wake_at=at)

            button.connect("clicked", _pick)
            content.append(button)
        cancel = widgets.menu_row("Cancel")
        cancel.connect("clicked", lambda _b: dialog.close())
        content.append(cancel)
        dialog.present(self.get_root())


def _caption(text: str) -> str:
    """Caption labels are uppercased in code (no CSS text-transform)."""
    return text.upper()


def _outbox_row(entry):
    """Project an outbox row onto a thread-shaped display row (pure)."""
    return mailbox_mod.outbox_display_row(entry,
                                          int(time.time() * 1000))
