"""The window: five bottom tabs, one navigation stack per tab, the
mini-player above the bar (ui/navigation/AppNavHost.kt).

Pushed views (Book, Series, Author, Narrator, Collections, Collection)
open inside the tab you are on, which stays lit; reselecting a tab pops
to its root. Downloads, Zones and Setup are later milestones and show
one muted line.
"""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from xx_audiobook.ui import common, style  # noqa: E402
from xx_audiobook.ui.book import BookPage  # noqa: E402
from xx_audiobook.ui.browse import (  # noqa: E402
    BrowsePage, CollectionPage, CollectionsPage)
from xx_audiobook.ui.library import LibraryPage  # noqa: E402
from xx_audiobook.ui.playing import MiniPlayer, NowPlayingPage  # noqa: E402
from xx_ui import widgets  # noqa: E402

TABS: tuple = (
    ("library", "Library", "Library"),
    ("playing", "Playing", "Now Playing"),
    ("downloads", "Downloads", "Downloads"),
    ("zones", "Zones", "Zones"),
    ("setup", "Setup", "Setup"),
)
PLACEHOLDERS = {
    "downloads": "No downloaded books yet.",
    "zones": "No speaker zones yet.",
    "setup": "Sign in to set up audiobooks.",
}


class Nav:
    """What pages may ask of navigation (pushes stay in the current tab)."""

    def __init__(self, shell: Shell) -> None:
        self._shell = shell

    def back(self) -> None:
        self._shell.current_nav().pop()

    def open_item(self, item) -> None:
        self.open_book(item.id, item)

    def open_book(self, item_id: str, peek=None) -> None:
        page = BookPage(self._shell.ctx, item_id, peek, self)
        self._shell.push(page, "Book")

    def browse(self, kind: str, name: str, ref_id) -> None:
        page = BrowsePage(self._shell.ctx, kind, name, ref_id, self)
        self._shell.push(page, name)

    def collections(self) -> None:
        self._shell.push(CollectionsPage(self._shell.ctx, self),
                         "Collections")

    def collection(self, row) -> None:
        self._shell.push(CollectionPage(self._shell.ctx, row, self),
                         row.name)

    def now_playing(self) -> None:
        self._shell.show("playing")


class Shell:
    def __init__(self, window: Gtk.Window, ctx) -> None:
        self.window = window
        self.ctx = ctx
        self.nav = Nav(self)
        self.current = "library"
        self.navs: dict = {}
        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.set_hexpand(True)
        self.library = LibraryPage(ctx, self.nav.open_item,
                                   self.nav.collections)
        self.playing = NowPlayingPage(ctx, self.nav.open_book,
                                      lambda: self.window)
        roots = {"library": self.library.widget,
                 "playing": self.playing.widget}
        for tab, label, _spoken in TABS:
            root = roots.get(tab) or widgets.page(
                widgets.page_header(label),
                common.muted(PLACEHOLDERS[tab]))
            nav = Adw.NavigationView()
            nav.add(Adw.NavigationPage.new_with_tag(root, label, tab))
            self.navs[tab] = nav
            self.stack.add_named(nav, tab)
        self.mini = MiniPlayer(ctx, lambda: self.show("playing"))
        self.tabs = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        self.tabs.add_css_class("xx-tabs")
        self.tabs.set_homogeneous(True)
        self.tab_buttons = {}
        for tab, label, spoken in TABS:
            button = Gtk.Button.new_with_label(label.upper())
            button.add_css_class("xx-tab")
            button.get_child().set_ellipsize(3)  # Pango END
            button.connect("clicked", lambda _b, t=tab: self.show(t))
            common.name(button, spoken)
            self.tab_buttons[tab] = button
            self.tabs.append(button)
        root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        root.add_css_class("xx-ab")
        root.append(self.stack)
        root.append(self.mini.widget)
        root.append(self.tabs)
        self.root = root
        ctx.player.connect(self._sync_mini)
        self.show("library")

    def current_nav(self) -> Adw.NavigationView:
        return self.navs[self.current]

    def push(self, page, title: str) -> None:
        self.top_page = page
        self.current_nav().push(Adw.NavigationPage.new(page.widget, title))

    def show(self, tab: str) -> None:
        if tab == self.current:
            self.navs[tab].pop_to_tag(tab)
        self.current = tab
        self.stack.set_visible_child_name(tab)
        for name, button in self.tab_buttons.items():
            if name == tab:
                button.add_css_class("active")
            else:
                button.remove_css_class("active")
        self._sync_mini()

    def _sync_mini(self) -> None:
        self.mini.widget.set_visible(
            self.ctx.player.has_book and self.current != "playing")


def build(app, ctx) -> Adw.ApplicationWindow:
    import xx_ui

    xx_ui.apply(app)
    style.install()
    window = Adw.ApplicationWindow(application=app)
    window.set_title("XX-Audiobook")
    window.set_default_size(360, 720)
    window.set_decorated(False)
    shell = Shell(window, ctx)
    window.set_content(shell.root)
    window._ab = shell
    ctx.start()
    return window
