"""The window shell: core tabs, extra tabs from the AppSpec, bottom bar,
the mini player above it, health banners, and Now Playing sliding up
over everything.

Each tab owns a navigation stack: pages pushed from a tab (artist,
album, playlist, mood, charts, settings pages) open inside it, and
tapping the lit tab again pops back to its root. Apps add screens
through ``AppSpec.extra_tabs`` and ``xx_music.ui.context.Hooks``.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from xx_music.appspec import CORE_TAB_TITLES, AppSpec  # noqa: E402
from xx_music.engine import Engine, Item, ItemKind, Mood, Track  # noqa: E402
from xx_music.health import Banner, BannerKind  # noqa: E402
from xx_music.tasks import Worker  # noqa: E402
from xx_music.ui import common, css  # noqa: E402
from xx_music.ui.context import Hooks, UiContext  # noqa: E402
from xx_ui import widgets  # noqa: E402

FIRST_RUN_TEXT = {
    "home": "Search for a song to start listening.",
    "search": "Find songs, albums, artists and playlists.",
    "library": "Sign in to see your library. Downloads show here too.",
}

BANNER_BUTTONS = {
    "update-engine": "Update",
    "setup-helper": "Set up",
    "retry": "Retry",
    "sign-in": "Sign in",
}
OFFLINE_MIX_TEXT = "Offline · playing what's on this phone"


class TabContext:
    """What an extra tab's ``build`` receives."""

    def __init__(self, spec: AppSpec, engine: Engine, worker: Worker,
                 shell: Shell) -> None:
        self.spec = spec
        self.engine = engine
        self.worker = worker
        self.shell = shell
        self.ui = shell.ui


class Shell:
    def __init__(self, spec: AppSpec, engine: Engine,
                 worker: Worker | None = None, ui: UiContext | None = None,
                 window: Gtk.Window | None = None) -> None:
        from xx_music.ui.home import HomePage
        from xx_music.ui.library import LibraryPage
        from xx_music.ui.miniplayer import MiniPlayer
        from xx_music.ui.nowplaying import NowPlaying
        from xx_music.ui.search import SearchPage
        from xx_music.ui.settings import SettingsPage

        self.spec = spec
        self.engine = engine
        self.ui = ui or UiContext(spec, engine, worker)
        self.ui.shell = self
        self.worker = self.ui.worker
        self.window = window
        self.current = ""
        self.titles: dict[str, str] = {}
        self.navs: dict[str, Adw.NavigationView] = {}
        self.pages: dict[str, object] = {}
        self.stack = Gtk.Stack()
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)

        context = TabContext(spec, engine, self.worker, self)
        extras = {tab.key: tab for tab in spec.extra_tabs}
        builders = {"home": HomePage, "search": SearchPage,
                    "library": LibraryPage, "settings": SettingsPage}
        for key in spec.tab_keys():
            if key in extras:
                self.titles[key] = extras[key].title
                widget = extras[key].build(context)
            else:
                self.titles[key] = CORE_TAB_TITLES[key]
                page = builders[key](self.ui)
                self.pages[key] = page
                widget = page.widget
            nav = Adw.NavigationView()
            nav.add(Adw.NavigationPage.new_with_tag(
                widget, self.titles[key], key))
            self.navs[key] = nav
            self.stack.add_named(nav, key)

        self.banner_slot = Gtk.Revealer()
        self.banner_slot.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._banner_key: tuple | None = None

        self.mini = MiniPlayer(self.ui, self.open_now_playing)
        self.tabs = common.hbox(0)
        self.tabs.add_css_class("xx-tabs")
        self.tabs.set_homogeneous(True)
        self.tab_buttons: dict[str, Gtk.Button] = {}
        for key in spec.tab_keys():
            title = self.titles[key]
            button = Gtk.Button.new_with_label(title.upper())
            button.add_css_class("xx-tab")
            common.fit(button, 9)
            button.connect("clicked", lambda _b, k=key: self.show(k))
            common.name(button, title)
            self.tab_buttons[key] = button
            self.tabs.append(button)

        main = common.vbox(0)
        main.add_css_class("xx-mu")
        main.add_css_class("xx-page")
        main.append(self.banner_slot)
        main.append(self.stack)
        main.append(self.mini.widget)
        main.append(self.tabs)

        self.now_playing = NowPlaying(self.ui, self.close_now_playing)
        self.np_slot = Gtk.Revealer()
        self.np_slot.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_UP)
        self.np_slot.set_transition_duration(220)
        self.np_slot.set_child(self.now_playing.widget)
        self.np_slot.set_valign(Gtk.Align.FILL)
        self.np_slot.set_visible(False)
        self.np_slot.connect("notify::child-revealed", self._np_settled)

        self.toast_label = common.label("", "xx-body", chars=30)
        self.toast_label.set_hexpand(True)
        toast_box = common.hbox(12)
        toast_box.add_css_class("xx-mu-banner")
        toast_box.add_css_class("loud")
        toast_box.append(self.toast_label)
        self.toast_action = Gtk.Button.new_with_label("")
        self.toast_action.add_css_class("xx-mu-banner-act")
        self.toast_action.set_valign(Gtk.Align.CENTER)
        self.toast_action.set_visible(False)
        toast_box.append(self.toast_action)
        self.toast_slot = Gtk.Revealer()
        self.toast_slot.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_UP)
        self.toast_slot.set_child(toast_box)
        self.toast_slot.set_valign(Gtk.Align.END)
        self.toast_slot.set_can_target(False)
        self.toast_slot.set_margin_bottom(57)
        self._toast_id = 0
        self._toast_cb = None

        self.overlay = Gtk.Overlay()
        self.overlay.set_child(main)
        self.overlay.add_overlay(self.np_slot)
        self.overlay.add_overlay(self.toast_slot)
        self.root = self.overlay
        self.first_run_widget = None

        self.ui.on_health(self.sync_banner)
        self.ui.player.connect(self._on_player)
        self._last_track = ""
        self.show("home")

    # -- tabs and navigation ------------------------------------------------------
    def show(self, key: str) -> None:
        if key == self.current and key in self.navs:
            self.navs[key].pop_to_tag(key)
        self.current = key
        self.stack.set_visible_child_name(key)
        for name, button in self.tab_buttons.items():
            if name == key:
                button.add_css_class("active")
            else:
                button.remove_css_class("active")
        page = self.pages.get(key)
        if page is not None and hasattr(page, "shown"):
            page.shown()

    def current_nav(self) -> Adw.NavigationView:
        return self.navs[self.current]

    def push(self, widget: Gtk.Widget, title: str) -> None:
        self.close_now_playing()
        self.current_nav().push(Adw.NavigationPage.new(widget, title))

    def pop(self) -> None:
        self.current_nav().pop()

    def open_item(self, item: Item) -> None:
        from xx_music.ui import pages

        if item.kind == ItemKind.TRACK and item.track is not None:
            self.play_song(item.track)
            return
        if item.kind == ItemKind.ALBUM:
            pages.open_album(self.ui, item.id, item.title)
        elif item.kind == ItemKind.ARTIST:
            pages.open_artist(self.ui, item.id, item.title)
        elif item.kind == ItemKind.PLAYLIST:
            pages.open_playlist(self.ui, item.id, item.title)
        elif item.kind == ItemKind.MOOD:
            pages.open_mood(self.ui, Mood(item.id, item.title))

    def open_artist(self, artist_id: str, name: str = "") -> None:
        from xx_music.ui import pages

        pages.open_artist(self.ui, artist_id, name)

    def open_album(self, album_id: str, title: str = "") -> None:
        from xx_music.ui import pages

        pages.open_album(self.ui, album_id, title)

    def play_song(self, track: Track, tracks: list[Track] | None = None,
                  origin: dict | None = None) -> None:
        """Tap on a song: within a list it plays the list from there;
        alone it plays the song and starts its radio."""
        if tracks:
            start = next((i for i, t in enumerate(tracks)
                          if t.id == track.id), 0)
            self.ui.player.play(tracks, start, origin or {})
        elif self.ui.supports("radio"):
            self.ui.player.start_radio(track)
        else:
            self.ui.player.play([track], 0, origin or {})

    # -- now playing ----------------------------------------------------------------
    def open_now_playing(self, view: str = "art") -> None:
        if not self.ui.player.has_queue:
            return
        self.now_playing.set_view(view)
        self.np_slot.set_visible(True)
        self.np_slot.set_reveal_child(True)
        self.now_playing.shown()

    def close_now_playing(self) -> None:
        if self.np_slot.get_reveal_child():
            self.np_slot.set_reveal_child(False)

    def _np_settled(self, slot, _pspec) -> None:
        if not slot.get_reveal_child() and not slot.get_child_revealed():
            slot.set_visible(False)

    @property
    def now_playing_open(self) -> bool:
        return self.np_slot.get_reveal_child()

    def _on_player(self) -> None:
        player = self.ui.player
        if not player.has_queue and self.now_playing_open:
            self.close_now_playing()
        track = player.track
        if track is not None and player.playing and \
                track.id != self._last_track:
            self._last_track = track.id
            self.ui.io(lambda t=track: self.ui.store.note_played(t))
        self.sync_banner()

    # -- banners and toasts -------------------------------------------------------------
    def current_banner(self) -> Banner | None:
        banner = self.ui.health.banner
        if banner is None and self.ui.player.offline:
            return Banner(BannerKind.OFFLINE, OFFLINE_MIX_TEXT, "retry")
        return banner

    def sync_banner(self) -> None:
        banner = self.current_banner()
        key = (banner.kind, banner.text) if banner else None
        if key == self._banner_key:
            return
        self._banner_key = key
        if banner is None:
            self.banner_slot.set_reveal_child(False)
            return
        self.banner_slot.set_child(self._banner_widget(banner))
        self.banner_slot.set_reveal_child(True)

    def _banner_widget(self, banner: Banner) -> Gtk.Widget:
        loud = banner.kind in (BannerKind.SOURCE_CHANGED,
                               BannerKind.HELPER_MISSING)
        row = common.hbox(12)
        row.add_css_class("xx-mu-banner")
        row.add_css_class("loud" if loud else "calm")
        icon = {BannerKind.OFFLINE: "network-offline-symbolic",
                BannerKind.SIGNED_OUT: "system-users-symbolic"}.get(
                    banner.kind, "dialog-warning-symbolic")
        glyph = widgets.icon_image(icon, 16)
        glyph.set_valign(Gtk.Align.CENTER)
        row.append(glyph)
        text = common.label(banner.text, "xx-mu-banner-text", lines=2,
                            chars=22)
        text.set_hexpand(True)
        text.set_valign(Gtk.Align.CENTER)
        row.append(text)
        action = BANNER_BUTTONS.get(banner.action)
        if action and (banner.action in self.ui.hooks.actions
                       or banner.action == "retry"):
            button = Gtk.Button.new_with_label(action.upper())
            button.add_css_class("xx-mu-banner-act")
            button.set_valign(Gtk.Align.CENTER)
            common.fit(button, 10)
            button.connect("clicked",
                           lambda _b, a=banner.action: self.ui.action(a))
            row.append(button)
        return row

    def toast(self, text: str, action_label: str = "",
              action=None) -> None:
        from gi.repository import GLib

        self.toast_label.set_text(text)
        if self._toast_id:
            GLib.source_remove(self._toast_id)
            self._toast_id = 0
        try:
            self.toast_action.disconnect_by_func(self._toast_go)
        except Exception:  # noqa: BLE001 - no handler yet
            pass
        self._toast_cb = action
        if action_label and action is not None:
            self.toast_action.set_label(action_label.upper())
            common.fit(self.toast_action, 10)
            self.toast_action.set_visible(True)
            self.toast_action.connect("clicked", self._toast_go)
        else:
            self.toast_action.set_visible(False)
        self.toast_slot.set_reveal_child(True)

        def _hide() -> bool:
            self._toast_id = 0
            self.toast_slot.set_reveal_child(False)
            return False

        self._toast_id = GLib.timeout_add(4000 if action is not None
                                          else 2600, _hide)

    def _toast_go(self, _button) -> None:
        from gi.repository import GLib

        callback, self._toast_cb = self._toast_cb, None
        self.toast_slot.set_reveal_child(False)
        if self._toast_id:
            GLib.source_remove(self._toast_id)
            self._toast_id = 0
        if callback is not None:
            callback()

    def retry(self) -> None:
        self.ui.health.note_success()
        self.sync_banner()
        for page in self.pages.values():
            if hasattr(page, "reload"):
                page.reload()

    # -- first run --------------------------------------------------------------------------
    def show_first_run(self, widget: Gtk.Widget) -> None:
        self.first_run_widget = widget
        self.overlay.add_overlay(widget)

    def hide_first_run(self) -> None:
        if self.first_run_widget is not None:
            self.overlay.remove_overlay(self.first_run_widget)
            self.first_run_widget = None
        offer_welcome(self)


def build_window(app, spec: AppSpec, engine: Engine | None = None,
                 hooks: Hooks | None = None, player=None,
                 ui_factory=None):
    """The app window at phone size, themed by xx_ui with live switching.

    ``hooks`` adds the app's own screens; ``player`` is a PlayerModel
    (default: the player service over D-Bus); ``ui_factory(spec,
    engine)`` may build the whole UiContext instead (renders, tests).
    """
    import xx_ui

    from xx_music.ui.art import ArtLoader
    from xx_music.ui.player import DbusBackend, PlayerModel

    xx_ui.apply(app)
    css.install()
    window = Adw.ApplicationWindow(application=app)
    window.set_title(spec.name)
    window.set_default_size(360, 720)
    window.set_decorated(False)
    engine = engine if engine is not None else spec.make_engine()
    if ui_factory is not None:
        ui = ui_factory(spec, engine)
    else:
        ui = UiContext(
            spec, engine, Worker(),
            player or PlayerModel(DbusBackend(spec)),
            ArtLoader(spec.paths().art_cache_dir(), engine.art_url),
            hooks=hooks)
    shell = Shell(spec, engine, ui=ui, window=window)
    window.set_content(shell.root)
    window.xx_shell = shell
    start(shell)

    def _destroyed(_w) -> None:
        shell.worker.shutdown()
        if shell.ui.art is not None:
            shell.ui.art.shutdown()

    window.connect("destroy", _destroyed)
    return window


def start(shell: Shell) -> None:
    """Account state, downloads, and the first-run check (all off-loop)."""
    ui = shell.ui

    def _status(status) -> None:
        ui.set_account(bool(status.signed_in), status.account_name)

    ui.run("status", ui.engine.status, _status, lambda _e: None)

    def _prefs(prefs: dict) -> None:
        ui.prefs = prefs
        ui.refresh_downloads()

    ui.io(ui.store.prefs, _prefs, lambda _e: ui.refresh_downloads())
    first = ui.hooks.first_run
    if first is None:
        offer_welcome(shell)
        return

    def _needed(needed: bool) -> None:
        if needed:
            shell.show_first_run(first.build(ui, shell.hide_first_run))
        else:
            offer_welcome(shell)

    ui.io(lambda: bool(first.needed(ui)), _needed,
          lambda _e: offer_welcome(shell))


def offer_welcome(shell: Shell) -> None:
    """The app's one-time welcome sheet, once the first run is over."""
    ui = shell.ui
    welcome = ui.hooks.welcome
    if welcome is None:
        return
    ui.io(lambda: bool(welcome.needed(ui)),
          lambda needed: welcome.show(ui) if needed else None,
          lambda _e: None)
