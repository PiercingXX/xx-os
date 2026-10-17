"""The window: the auth gate, then five bottom tabs with one navigation
stack per tab and the mini-player above the bar (ui/App.kt,
ui/navigation/AppNavHost.kt).

The gate (App.kt): a neutral Checking frame while the context resolves
auth on a worker (hub probe, then the stored token); Sign in over the
whole window when both fail; the tabs only once someone is signed in. A
403 not_granted covers the window with No access (Retry / Sign out)
until access comes back. A sign-out or a revoked session returns to
Sign in.

Pushed views (Book, Series, Author, Narrator, Collections, Collection,
Stats, licenses) open inside the tab you are on, which stays lit;
reselecting a tab pops to its root. The bottom bar is icon + label with
a 1 px line along the active tab's top edge; from text size 1.5x the
labels drop and the icons carry the tabs (each still reads its name).
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
from xx_audiobook.ui.setup import (  # noqa: E402
    APP_NAME, NoAccessView, SetupPage)
from xx_audiobook.ui.signin import SignInView, checking_view  # noqa: E402
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
TAB_ICONS = {"library": "xx-ab-library-symbolic",
             "playing": "xx-ab-play-circle-symbolic",
             "downloads": "xx-ab-download-symbolic",
             "zones": "xx-ab-speaker-symbolic",
             "setup": "xx-ab-settings-symbolic"}
GATES = ("checking", "signin", "app", "no-access")


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
        from xx_audiobook.ui.setup import license_entries

        nav = self._shell.current_nav()
        nav.push(widgets.licenses_page(nav, APP_NAME, license_entries()))


def tab_button(tab: str, text: str, spoken: str, on_select) -> Gtk.Button:
    """One bottom-bar tab: the icon over its label; read as its screen."""
    button = Gtk.Button()
    button.add_css_class("xx-ab-tab")
    box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 4)
    box.set_halign(Gtk.Align.CENTER)
    box.set_valign(Gtk.Align.CENTER)
    box.append(widgets.icon_image(TAB_ICONS[tab], 24))
    caption = Gtk.Label(label=text)
    caption.set_ellipsize(0)
    box.append(caption)
    button.set_child(box)
    button.caption = caption
    button.connect("clicked", lambda _b: on_select(tab))
    common.name(button, spoken)
    return button


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
        self.tabs.add_css_class("xx-ab-tabs")
        self.tabs.set_homogeneous(True)
        self.tab_buttons = {}
        for tab, label, spoken in TABS:
            button = tab_button(tab, label, spoken, self.show)
            self.tab_buttons[tab] = button
            self.tabs.append(button)
        self._labels()
        app = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        app.append(self.stack)
        app.append(self.mini.widget)
        app.append(self.tabs)
        self.sign_in = SignInView(ctx)
        self.no_access = NoAccessView(ctx, lambda: self.window)
        self.root = Gtk.Stack()
        self.root.add_named(checking_view(), "checking")
        self.root.add_named(self.sign_in.widget, "signin")
        self.root.add_named(app, "app")
        self.root.add_named(self.no_access.widget, "no-access")
        self.root.set_visible_child_name("checking")
        ctx.player.connect(self._sync_mini)
        ctx.connect("auth-resolved", self._gate)
        ctx.connect("access-changed", self._gate)
        ctx.connect("session-changed", self._session_changed)
        self.show("library")

    @property
    def gate(self) -> str:
        return self.root.get_visible_child_name()

    def _labels(self, _theme=None) -> None:
        """Icons only from text size 1.5x (labels would clip)."""
        for button in self.tab_buttons.values():
            button.caption.set_visible(not style.large_text())

    def _session_changed(self) -> None:
        if self.ctx.signed_in:
            self.show("library")
        else:
            self.sign_in.render()
        self.library.refresh()
        self._gate()

    def _gate(self) -> None:
        """Checking until auth resolves; then Sign in, No access (403
        not_granted) or the tabs."""
        ctx = self.ctx
        if not ctx.auth_resolved:
            name = "checking"
        elif not ctx.signed_in:
            name = "signin"
        elif ctx.access_denied:
            self.no_access.render()
            name = "no-access"
        else:
            name = "app"
        if name != self.gate:
            self.root.set_visible_child_name(name)

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
                button.set_state_flags(Gtk.StateFlags.SELECTED, False)
            else:
                button.remove_css_class("active")
                button.unset_state_flags(Gtk.StateFlags.SELECTED)
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
    window.add_css_class("xx-ab")
    shell = Shell(window, ctx)
    xx_ui.on_change(shell._labels)
    window.set_content(shell.root)
    window._ab = shell
    ctx.start()
    return window
