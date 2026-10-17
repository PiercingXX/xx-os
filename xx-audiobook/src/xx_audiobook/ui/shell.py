"""The window: five bottom tabs, one navigation stack per tab, the
mini-player above the bar (ui/navigation/AppNavHost.kt).

Pushed views (Book, Series, Author, Narrator, Collections, Collection,
Stats, licenses) open inside the tab you are on, which stays lit;
reselecting a tab pops to its root. A 403 not_granted covers the whole
window with No access (Retry / Sign out) until access comes back.
"""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from xx_audiobook.ui import common, style  # noqa: E402
from xx_audiobook.ui.book import BookPage  # noqa: E402
from xx_audiobook.ui.browse import (  # noqa: E402
    BrowsePage, CollectionPage, CollectionsPage)
from xx_audiobook.ui.downloads import DownloadsPage  # noqa: E402
from xx_audiobook.ui.library import LibraryPage  # noqa: E402
from xx_audiobook.ui.playing import MiniPlayer, NowPlayingPage  # noqa: E402
from xx_audiobook.ui.setup import NoAccessView, SetupPage  # noqa: E402
from xx_audiobook.ui.stats import StatsPage  # noqa: E402
from xx_audiobook.ui.zones import ZonesPage  # noqa: E402
from xx_ui import widgets  # noqa: E402

TABS: tuple = (
    ("library", "Library", "Library"),
    ("playing", "Playing", "Now Playing"),
    ("downloads", "Downloads", "Downloads"),
    ("zones", "Zones", "Zones"),
    ("setup", "Setup", "Setup"),
)


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

    def downloads(self) -> None:
        self._shell.show("downloads")

    def stats(self) -> None:
        shell = self._shell
        shell.push(StatsPage(shell.ctx, self, lambda: shell.window),
                   "Listening stats")

    def licenses(self) -> None:
        from xx_audiobook.ui.setup import APP_NAME, license_entries

        nav = self._shell.current_nav()
        nav.push(widgets.licenses_page(nav, APP_NAME, license_entries()))


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
        self.downloads = DownloadsPage(ctx, self.nav.open_book,
                                       lambda: self.window)
        self.zones = ZonesPage(ctx, lambda: self.window)
        self.setup = SetupPage(ctx, self.nav, lambda: self.window)
        roots = {"library": self.library.widget,
                 "playing": self.playing.widget,
                 "downloads": self.downloads.widget,
                 "zones": self.zones.widget,
                 "setup": self.setup.widget}
        for tab, label, _spoken in TABS:
            root = roots[tab]
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
        self.no_access = NoAccessView(ctx, lambda: self.window)
        self.root = Gtk.Stack()
        self.root.add_named(root, "app")
        self.root.add_named(self.no_access.widget, "no-access")
        ctx.player.connect(self._sync_mini)
        ctx.connect("access-changed", self._sync_access)
        ctx.connect("session-changed", self.library.refresh)
        self.show("library")

    def _sync_access(self) -> None:
        """403 not_granted: the whole window is No access until Retry
        (or a sign-in) brings access back."""
        denied = self.ctx.access_denied and self.ctx.signed_in
        if denied:
            self.no_access.render()
        self.root.set_visible_child_name("no-access" if denied else "app")

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
    window.set_title("xx-audiobook")
    window.set_default_size(360, 720)
    window.set_decorated(False)
    shell = Shell(window, ctx)
    window.set_content(shell.root)
    window._ab = shell
    ctx.start()
    return window
