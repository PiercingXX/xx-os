"""Pushed pages: Artist, Album, Playlist, a mood, all moods, charts.

Each opens inside the current tab with a compact back header, paints a
skeleton title at once (the name the tap already knew), loads on the
worker under a 2 px loading line, and keeps one muted sentence plus
Retry when the engine fails.

Minimal Mode: no hero art, the title in the display face, names as
plain words, ``[ play ]`` and friends, song lists without art, and the
release and related carousels as ``Title — Artist`` shelves. A page
repaints itself from what it already loaded when the mode flips.
"""

from __future__ import annotations

import random

from gi.repository import Gtk

from xx_music.engine import (CHARTS, ERROR_TEXT, MOODS, RADIO, STREAM,
                             AlbumPage, ArtistPage, ErrorKind, Item,
                             ItemKind, Mood, PlaylistPage, Shelf, Track)
from xx_music.tasks import Generation
from xx_music.ui import common, css, fmt, menus, minimal
from xx_ui import widgets

HERO_PX = 168
ARTIST_HERO_PX = 180
RELEASE_PX = 132
AVATAR_PX = 88


class Page:
    """Header, loading line, scrolling body, and the load/fail cycle."""

    caption = ""

    def __init__(self, ui, title: str) -> None:
        self.ui = ui
        self.title = title
        self.generation = Generation()
        self.expanded: set[str] = set()
        self.data = None
        self._painter = None
        self.loading = common.LoadingLine()
        self.body = common.vbox(0)
        self.body.set_margin_bottom(32)
        root = common.vbox(0)
        root.add_css_class("xx-mu")
        self.header_slot = common.vbox(0)
        self.header_slot.append(self.header())
        root.append(self.header_slot)
        root.append(self.loading)
        scroll = common.scroller(self.body)
        common.pull_to_refresh(scroll, lambda: self.reload())
        root.append(scroll)
        self.widget = root
        minimal.listen(self._minimal_changed)

    def header(self) -> Gtk.Widget:
        return common.header(self.ui.shell.pop, self.caption)

    def load(self, capability: str, fn, paint) -> None:
        self.loading.set_active(True)

        def done(result) -> None:
            self.loading.set_active(False)
            self.data, self._painter = result, paint
            self.repaint()

        self.ui.run(capability, fn, done, self.failed, self.generation)

    def repaint(self) -> None:
        if self._painter is None:
            return
        common.clear(self.body)
        self._painter(self.data)

    def _minimal_changed(self) -> None:
        if self.widget.get_root() is None:
            return  # popped: nothing to show it on
        common.clear(self.header_slot)
        self.header_slot.append(self.header())
        if self._painter is not None:
            self.repaint()

    def shelf(self, key: str, items: list[Item], on_menu=True) -> None:
        """Minimal Mode's stand-in for a carousel: ``Title — Artist``
        rows, five before "more…"."""
        ui = self.ui

        def toggle() -> None:
            self.expanded ^= {key}
            self.repaint()

        entries = [(minimal.item_text(item), lambda i=item:
                    ui.shell.open_item(i),
                    (lambda i=item: menus.item_menu(ui, i))
                    if on_menu else None) for item in items]
        self.gutter(minimal.shelf(entries, key in self.expanded, toggle))

    def failed(self, error) -> None:
        self.loading.set_active(False)
        self.data = getattr(error, "kind", ErrorKind.OTHER)
        self._painter = self._paint_failed
        self.repaint()

    def _paint_failed(self, kind) -> None:
        box = common.vbox(0)
        box.add_css_class("xx-page-body")
        box.append(common.label(self.title, "xx-mu-h1", lines=2, chars=16))
        box.append(common.empty_state(
            ERROR_TEXT.get(kind, ERROR_TEXT[ErrorKind.OTHER]),
            "Retry", self.reload))
        box.get_last_child().set_margin_top(32)
        self.body.append(box)

    def reload(self) -> None:
        pass

    def gutter(self, widget: Gtk.Widget) -> Gtk.Widget:
        widget.add_css_class("xx-page-body")
        self.body.append(widget)
        return widget

    def section(self, text: str, first: bool = False) -> None:
        self.gutter(common.section(text, first=first))

    def rows(self, tracks: list[Track], origin: dict,
             numbered: bool = False, limit: int | None = None) -> None:
        box = common.vbox(0)
        shown = tracks[:limit] if limit else tracks
        current = self.ui.player.track
        for index, track in enumerate(shown):
            box.append(common.track_row(
                self.ui, track,
                lambda t=track: self.ui.shell.play_song(t, tracks, origin),
                lambda t=track: menus.track_menu(self.ui, t),
                number=index + 1 if numbered else None,
                now=current is not None and current.id == track.id,
                offline=track.id in self.ui.downloaded,
                last=index == len(shown) - 1))
        self.gutter(box)

    def actions(self, buttons: list[Gtk.Widget]) -> None:
        if minimal.active():
            row = minimal.flow(buttons, spacing=8)
            row.set_margin_top(10)
            self.gutter(row)
            return
        row = Gtk.Box.new(Gtk.Orientation.VERTICAL if css.large_text()
                          else Gtk.Orientation.HORIZONTAL, 10)
        row.set_margin_top(18)
        for button in buttons:
            row.append(button)
        self.gutter(row)

    def hero(self, url: str, title: str, px: int, shape: str,
             lines: list[tuple[str, object]]) -> None:
        """Art, then the title in the display face, then muted lines;
        a line with a callback is a tappable name."""
        box = common.vbox(0)
        box.set_margin_top(8)
        bare = minimal.active()
        if not bare:
            art = common.art(self.ui, url, title, px, shape)
            art.set_halign(Gtk.Align.START)
            box.append(art)
        head = common.label(title, "xx-mu-h1", lines=3, chars=16)
        head.set_margin_top(4 if bare else 18)
        box.append(head)
        for text, callback in lines:
            if not text:
                continue
            if bare:
                if callback is not None:
                    line = minimal.text_row(text, callback, spoken=f"Open {text}")
                else:
                    line = minimal.text(text, minimal.Tone.QUIET, 13,
                                        chars=30)
                    line.add_css_class("xx-mu-num")
                    line.set_margin_top(4)
                box.append(line)
                continue
            if callback is not None:
                link = Gtk.Button()
                link.add_css_class("xx-mu-link")
                link.set_halign(Gtk.Align.START)
                link.set_child(common.label(text, "xx-body", chars=26))
                link.connect("clicked", lambda _b, cb=callback: cb())
                link.set_margin_top(6)
                common.name(link, f"Open {text}")
                box.append(link)
            else:
                line = common.label(text, "xx-secondary xx-mu-num", chars=30)
                line.set_margin_top(4)
                box.append(line)
        self.gutter(box)


def _wide(button: Gtk.Button) -> Gtk.Button:
    if button.has_css_class("xx-mm-btn"):
        return button
    button.set_hexpand(True)
    return common.fit(button, 10)


def _play_buttons(ui, tracks: list[Track], origin: dict,
                  download: dict | None) -> list[Gtk.Widget]:
    def play(shuffle: bool) -> None:
        if not tracks:
            return
        if shuffle:
            ui.player.play(tracks, random.randrange(len(tracks)), origin)
            ui.player.set_shuffle(True)
        else:
            ui.player.play(tracks, 0, origin)

    if minimal.active():
        Tone = minimal.Tone
        words = [minimal.bracket_button("play", lambda: play(False),
                                        Tone.STRONG),
                 minimal.bracket_button("shuffle", lambda: play(True),
                                        Tone.QUIET)]
        if download is not None and ui.supports(STREAM):
            words.append(minimal.bracket_button(
                "download", lambda: (ui.player.download([download]),
                                     common.toast(ui, "Downloading")),
                Tone.QUIET))
        return words

    buttons = [_with_icon(_wide(minimal.primary(
        "Play", lambda: play(False))), "media-playback-start-symbolic")]
    shuffle = common.icon_button("media-playlist-shuffle-symbolic",
                                 "Shuffle", lambda: play(True))
    shuffle.add_css_class("xx-mu-thumb")
    buttons.append(shuffle)
    if download is not None and ui.supports(STREAM):
        get = common.icon_button(
            "folder-download-symbolic", "Download",
            lambda: (ui.player.download([download]),
                     common.toast(ui, "Downloading")))
        get.add_css_class("xx-mu-thumb")
        buttons.append(get)
    return buttons


def _with_icon(button: Gtk.Button, icon: str) -> Gtk.Button:
    if button.has_css_class("xx-mm-btn"):
        return button  # Minimal Mode: the word is enough
    text = button.get_label() or ""
    row = common.hbox(8)
    row.set_halign(Gtk.Align.CENTER)
    row.append(widgets.icon_image(icon, 18))
    word = common.label(text, "", chars=12)
    word.set_ellipsize(0)  # Pango NONE: short words, never a misfire
    row.append(word)
    button.set_child(row)
    common.name(button, text)
    return button


# -- album ------------------------------------------------------------------------

class AlbumView(Page):
    caption = "Album"

    def __init__(self, ui, album_id: str, title: str = "") -> None:
        super().__init__(ui, title or "Album")
        self.album_id = album_id
        self.hero("", self.title, HERO_PX, "r8", [])
        self.reload()

    def header(self):
        return common.header(self.ui.shell.pop, self.caption, "",
                             "view-more-symbolic", "More options",
                             self._menu)

    def _menu(self) -> None:
        menus.item_menu(self.ui, Item(ItemKind.ALBUM, self.album_id,
                                      self.title))

    def reload(self) -> None:
        self.load("album", lambda: self.ui.engine.album(self.album_id),
                  self.paint)

    def paint(self, page: AlbumPage) -> None:
        self.title = page.title
        artist = next((a for a in page.artists if a.id), None)
        names = ", ".join(a.name for a in page.artists if a.name)
        self.hero(page.art_url, page.title, HERO_PX, "r8", [
            (names, (lambda: self.ui.shell.open_artist(artist.id,
                                                       artist.name))
             if artist else None),
            (fmt.joined(fmt.album_meta(page), fmt.total_time(page.tracks)),
             None)])
        origin = {"type": "album", "id": page.id, "title": page.title}
        self.actions(_play_buttons(
            self.ui, list(page.tracks), origin,
            menus.download_payload("album", page.id, page.title)))
        self.section("Songs")
        self.rows(list(page.tracks), origin, numbered=True)


# -- playlist ----------------------------------------------------------------------

class PlaylistView(Page):
    caption = "Playlist"

    def __init__(self, ui, playlist_id: str, title: str = "") -> None:
        super().__init__(ui, title or "Playlist")
        self.playlist_id = playlist_id
        self.hero("", self.title, HERO_PX, "r8", [])
        self.reload()

    def header(self):
        return common.header(self.ui.shell.pop, self.caption, "",
                             "view-more-symbolic", "More options",
                             self._menu)

    def _menu(self) -> None:
        menus.item_menu(self.ui, Item(ItemKind.PLAYLIST, self.playlist_id,
                                      self.title))

    def reload(self) -> None:
        self.load("playlist",
                  lambda: self.ui.engine.playlist(self.playlist_id),
                  self.paint)

    def paint(self, page: PlaylistPage) -> None:
        self.title = page.title
        self.hero(page.art_url, page.title, HERO_PX, "r8", [
            (page.author, None),
            (fmt.joined(fmt.songs(page.track_count
                                  if page.track_count is not None
                                  else len(page.tracks)),
                        fmt.total_time(page.tracks)), None)])
        origin = {"type": "playlist", "id": page.id, "title": page.title}
        self.actions(_play_buttons(
            self.ui, list(page.tracks), origin,
            menus.download_payload("playlist", page.id, page.title)))
        self.section("Songs")
        self.rows(list(page.tracks), origin)


# -- artist ------------------------------------------------------------------------

class ArtistView(Page):
    caption = "Artist"

    def __init__(self, ui, artist_id: str, name: str = "") -> None:
        super().__init__(ui, name or "Artist")
        self.artist_id = artist_id
        self.hero("", self.title, ARTIST_HERO_PX, "r16", [])
        self.reload()

    def header(self):
        return common.header(self.ui.shell.pop, self.caption, "",
                             "view-more-symbolic", "More options",
                             self._menu)

    def _menu(self) -> None:
        menus.item_menu(self.ui, Item(ItemKind.ARTIST, self.artist_id,
                                      self.title))

    def reload(self) -> None:
        self.load("artist", lambda: self.ui.engine.artist(self.artist_id),
                  self.paint)

    def paint(self, page: ArtistPage) -> None:
        ui = self.ui
        self.title = page.name
        self.hero(page.art_url, page.name, ARTIST_HERO_PX, "r16", [])
        origin = {"type": "artist", "id": page.id, "title": page.name}
        top = list(page.top_tracks)
        buttons = [_with_icon(_wide(minimal.primary(
            "Play", lambda: ui.player.play(top, 0, origin))),
            "media-playback-start-symbolic")]
        if ui.supports(RADIO) and (page.radio_seed or top):
            buttons.append(_with_icon(_wide(minimal.outline(
                "Radio", lambda: _start_artist_radio(ui, page))),
                "media-playlist-shuffle-symbolic"))
        if top:
            self.actions(buttons)
            self.section("Top songs")
            self.rows(top, origin, limit=5)
        for title, items in (("Albums", page.albums),
                             ("Singles", page.singles)):
            if items and minimal.active():
                self.section(title)
                self.shelf(title, list(items))
            elif items:
                self.section(title)
                self.body.append(common.carousel([
                    common.tile(ui, item, lambda i=item: ui.shell.open_item(i),
                                lambda i=item: menus.item_menu(ui, i),
                                px=RELEASE_PX)
                    for item in items]))
        if page.related and minimal.active():
            self.section("Fans also like")
            self.shelf("related", list(page.related), on_menu=False)
        elif page.related:
            self.section("Fans also like")
            self.body.append(common.carousel([
                common.tile(ui, item, lambda i=item: ui.shell.open_item(i),
                            px=AVATAR_PX, sub="")
                for item in page.related], spacing=16))


def _start_artist_radio(ui, page: ArtistPage) -> None:
    seed = next((t for t in page.top_tracks if t.id == page.radio_seed),
                None) or (page.top_tracks[0] if page.top_tracks else None)
    if seed is not None:
        ui.player.start_radio(seed)


def artist_radio(ui, artist_id: str) -> None:
    ui.run("artist", lambda: ui.engine.artist(artist_id),
           lambda page: _start_artist_radio(ui, page),
           lambda e: common.toast(ui, getattr(e, "user_text", "")))


# -- moods and charts --------------------------------------------------------------

class ShelvesView(Page):
    """A titled page of shelves (a mood's playlists, the charts)."""

    def __init__(self, ui, title: str, caption: str, capability: str,
                 fetch) -> None:
        self.caption = caption
        super().__init__(ui, title)
        self.capability = capability
        self.fetch = fetch
        self._title_block()
        self.reload()

    def _title_block(self) -> None:
        head = common.label(self.title, "xx-mu-h1", lines=2, chars=16)
        head.set_margin_top(8)
        self.gutter(head)

    def reload(self) -> None:
        self.load(self.capability, self.fetch, self.paint)

    def paint(self, shelves: list[Shelf]) -> None:
        ui = self.ui
        self._title_block()
        shown = [s for s in shelves if s.items]
        if not shown:
            self.gutter(common.muted("Nothing here right now."))
            return
        for shelf in shown:
            self.section(shelf.title)
            items = list(shelf.items)
            tracks = [i.track for i in items if i.track is not None]
            if len(tracks) == len(items):
                self.rows(tracks, {"type": "chart", "title": shelf.title},
                          numbered=self.capability == CHARTS, limit=10)
                continue
            if minimal.active():
                self.shelf(shelf.title, items)
                continue
            self.body.append(common.carousel([
                common.tile(ui, item, lambda i=item: ui.shell.open_item(i),
                            lambda i=item: menus.item_menu(ui, i),
                            px=AVATAR_PX if item.kind == ItemKind.ARTIST
                            else common.TILE_PX)
                for item in items]))


class MoodsView(Page):
    caption = "Moods & genres"

    def __init__(self, ui) -> None:
        super().__init__(ui, "Moods & genres")
        self.reload()

    def reload(self) -> None:
        self.load(MOODS, self.ui.engine.moods, self.paint)

    def paint(self, moods: list[Mood]) -> None:
        groups: dict[str, list[Mood]] = {}
        for mood in moods:
            groups.setdefault(mood.group or "Moods", []).append(mood)
        for index, (group, members) in enumerate(groups.items()):
            self.section(group, first=index == 0)
            self.gutter(common.flow_chips([
                (m.title, False, lambda m=m: open_mood(self.ui, m))
                for m in members], per_line=3))


# -- openers -----------------------------------------------------------------------

def open_album(ui, album_id: str, title: str = "") -> AlbumView:
    view = AlbumView(ui, album_id, title)
    ui.shell.push(view.widget, title or "Album")
    return view


def open_playlist(ui, playlist_id: str, title: str = "") -> PlaylistView:
    view = PlaylistView(ui, playlist_id, title)
    ui.shell.push(view.widget, title or "Playlist")
    return view


def open_artist(ui, artist_id: str, name: str = "") -> ArtistView:
    view = ArtistView(ui, artist_id, name)
    ui.shell.push(view.widget, name or "Artist")
    return view


def open_mood(ui, mood: Mood) -> None:
    view = ShelvesView(ui, mood.title, mood.group or "Mood", MOODS,
                       lambda: ui.engine.mood_shelves(mood.id))
    ui.shell.push(view.widget, mood.title)


def open_moods(ui) -> None:
    ui.shell.push(MoodsView(ui).widget, "Moods & genres")


def open_charts(ui) -> None:
    region = ""
    view = ShelvesView(ui, "Charts", "Charts", CHARTS,
                       lambda: ui.engine.charts(region))
    ui.shell.push(view.widget, "Charts")


def play_collection(ui, item: Item, shuffle: bool = False,
                    enqueue: bool = False) -> None:
    """Play, shuffle or queue an album or playlist from its menu."""
    def fetch():
        if item.kind == ItemKind.ALBUM:
            return list(ui.engine.album(item.id).tracks)
        return list(ui.engine.playlist(item.id).tracks)

    def done(tracks: list[Track]) -> None:
        if not tracks:
            return
        origin = {"type": item.kind.value, "id": item.id,
                  "title": item.title}
        if enqueue:
            ui.player.enqueue(tracks, False)
            common.toast(ui, "Added to the queue")
        elif shuffle:
            ui.player.play(tracks, random.randrange(len(tracks)), origin)
            ui.player.set_shuffle(True)
        else:
            ui.player.play(tracks, 0, origin)

    ui.run(item.kind.value, fetch, done,
           lambda e: common.toast(ui, getattr(e, "user_text", "")))
