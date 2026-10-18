"""Search page: operator hints plus offline FTS (thin view).

Ports SearchScreen: operator queries (anything with a colon) go to
the server as Gmail ``q``; plain terms hit the local FTS index first
and fall back to the server when empty. Results render without swipe
chrome. The entry sits at the bottom in thumb reach and never takes
focus on its own — the keyboard opens only on tap.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

from xx_email import mailbox as mailbox_mod
from xx_email.ui import common as common_mod
from xx_ui import widgets


class SearchPage(Gtk.Box):
    """Operator + offline search for one account."""

    def __init__(self, account: str, hooks, toast) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.account = account
        self._hooks = hooks
        self._toast = toast
        self._searched = False

        self.append(widgets.compact_header(
            lambda: hooks.go_back(), "Search", account))

        self._store = common_mod.new_thread_store()
        view = common_mod.thread_list_view(
            self._store, lambda tid, _n: hooks.open_thread(tid),
            None, None, "", "")
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER,
                            Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        scrolled.set_child(view)
        self.append(scrolled)

        self._status = widgets.caption("")
        status_pad = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        status_pad.add_css_class("xx-page-body")
        status_pad.append(self._status)
        self.append(status_pad)

        search = widgets.search_bar(
            "Search mail (from:, has:…)")
        self._entry = search.get_first_child().get_next_sibling()
        self._entry.set_can_focus(False)
        focus_tap = Gtk.GestureClick()
        focus_tap.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        focus_tap.connect("pressed",
                          lambda *_a: self._entry.set_can_focus(True))
        self._entry.add_controller(focus_tap)
        self._entry.connect("activate", lambda _e: self._run(None))
        self.append(search)

    def _run(self, preset: str | None) -> None:
        query = preset if preset is not None else \
            self._entry.get_text().strip()
        if not query:
            return
        if preset is not None:
            self._entry.set_text(preset)
        self._status.set_label("SEARCHING…")

        def _done(result) -> None:
            if isinstance(result, Exception):
                self._status.set_label("")
                self._toast("Search failed", None)
                return
            threads, server, offline = result
            self._searched = True
            common_mod.replace_store(self._store, threads)
            self._status.set_label(
                mailbox_mod.search_status(bool(server), bool(offline),
                                          len(threads)).upper())

        self._hooks.search(query, _done)


def search_dialog_page(account: str, hooks, toast) -> Adw.NavigationPage:
    page = SearchPage(account, hooks, toast)
    navigation = Adw.NavigationPage(title="Search")
    navigation.set_child(page)
    return navigation
