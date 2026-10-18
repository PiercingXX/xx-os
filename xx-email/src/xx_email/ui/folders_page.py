"""Folders page: accounts plus every mailbox folder (thin view).

Replaces the old folder bottom-sheet dialog with a full page so it
fits the phone, screenshots cleanly and matches the design system:
one tap switches account or folder, rarely used account actions live
here instead of on the list. Folder contents still render through
hooks on worker threads — this page only shows state.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

from xx_email import mailbox as mailbox_mod
from xx_ui import widgets


def _account_subtitle(entry: dict) -> str:
    parts = []
    if entry.get("snoozed"):
        parts.append(f"{int(entry['snoozed']):d} snoozed")
    if entry.get("queued"):
        parts.append(f"{int(entry['queued']):d} queued")
    return ", ".join(parts) or "Signed in"


class FoldersPage(Gtk.Box):
    """Account switcher and folder list for one account."""

    def __init__(self, account: str, hooks, toast) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.account = account
        self._hooks = hooks
        self._toast = toast

        header = widgets.compact_header(
            lambda: hooks.go_back(), "Folders", account)
        self.append(header)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        body.add_css_class("xx-page-body")
        body.set_vexpand(True)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER,
                            Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        inner.set_vexpand(True)
        scrolled.set_child(inner)
        body.append(scrolled)
        self.append(body)

        inner.append(widgets.section_label("Accounts"))
        self._accounts_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=0)
        inner.append(self._accounts_box)

        inner.append(widgets.section_label("Folders"))
        folders = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        inner.append(folders)
        inbox_row = widgets.list_row(
            "Inbox", "Primary, Social, Promotions, Updates, Forums",
            on_activate=lambda: hooks.show_folder(None))
        folders.append(inbox_row)
        others = list(mailbox_mod.OTHER_FOLDERS)
        for index, folder in enumerate(others):
            row = widgets.list_row(
                folder.title, "",
                on_activate=lambda f=folder: hooks.show_folder(f),
                last=index == len(others) - 1)
            folders.append(row)

        hooks.load_state(self._on_state)

    # ------------------------------------------------------------------ state

    def _on_state(self, result) -> None:
        if isinstance(result, Exception):
            return
        _config, accounts = result
        child = self._accounts_box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._accounts_box.remove(child)
            child = next_child
        if not accounts:
            return
        for index, entry in enumerate(accounts):
            email = entry.get("email", "")
            marker = " (current)" if email == self.account else ""
            row = widgets.list_row(
                email + marker, _account_subtitle(entry),
                on_activate=lambda e=email: self._maybe_switch(e),
                last=index == len(accounts) - 1)
            self._accounts_box.append(row)

    def _maybe_switch(self, email: str) -> None:
        if email != self.account:
            self._hooks.switch_account(email)

def folders_navigation_page(page: FoldersPage) -> Adw.NavigationPage:
    navigation = Adw.NavigationPage(title="Folders")
    navigation.set_child(page)
    return navigation
