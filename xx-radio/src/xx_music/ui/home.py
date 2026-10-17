"""Home: mood chips, then shelves, painted from the disk cache first.

Shelves: the engine's first shelf (quick picks) as columns of song
rows, Listen again from this phone's history, the engine's other
shelves as tile carousels, On this phone, Top songs from the charts and
a Moods & genres card shelf. Empty shelves are hidden. The cached copy
paints at once; the fresh one replaces it when the worker answers, with
a 2 px loading line under the header meanwhile. Errors keep whatever is
on screen and add one muted line.

Minimal Mode: the chips are words and every shelf is ``Title — Artist``
rows, five of them before "more…", with no art anywhere.
"""

from __future__ import annotations

from gi.repository import Gtk

from xx_music.engine import (CHARTS, ERROR_TEXT, HOME, MOODS, EngineError,
                             ErrorKind, Item, ItemKind, Mood, Shelf, Track)
from xx_music.tasks import Generation
from xx_music.ui import common, css, menus, minimal
from xx_ui import widgets

QUICK_ROWS = 4
COLUMN_PX = 296
MOODS_SHELF = "Moods & genres"
CHARTS_PREFIX = "chart:"
EMPTY_TEXT = "Search for a song to start listening."
LOCAL_LISTEN_AGAIN = "Listen again"


def _track_items(tracks: list[Track]) -> tuple[Item, ...]:
    return tuple(Item(ItemKind.TRACK, t.id, t.title, t.artist_line,
                      t.art_url, t) for t in tracks)


def fetch_home(engine, region: str = "") -> list[Shelf]:
    """Everything Home shows from the engine, as shelves (worker)."""
    shelves = list(engine.home())
    if engine.supports(CHARTS):
        try:
            for shelf in engine.charts(region)[:1]:
                if shelf.items:
                    shelves.append(Shelf(CHARTS_PREFIX + shelf.title,
                                         shelf.items))
        except EngineError:
            pass
    if engine.supports(MOODS):
        try:
            moods = engine.moods()
        except EngineError:
            moods = []
        if moods:
            shelves.append(Shelf(MOODS_SHELF, tuple(
                Item(ItemKind.MOOD, m.id, m.title, m.group)
                for m in moods)))
    return shelves


class HomePage:
    def __init__(self, ui) -> None:
        self.ui = ui
        self.generation = Generation()
        self.shelves: list[Shelf] = []
        self.history: list[Track] = []
        self.painted = False
        self.note = ""
        self.expanded: set[str] = set()
        self.loading = common.LoadingLine()
        self.body = common.vbox(0)
        self.body.set_margin_bottom(28)
        root = common.vbox(0)
        root.append(widgets.page_header("Home"))
        root.append(self.loading)
        self.scroll = common.scroller(self.body)
        root.append(self.scroll)
        self.widget = root
        ui.on_downloads(self._repaint)
        minimal.listen(self._repaint)
        self.reload(from_cache=True)

    # -- data -------------------------------------------------------------------
    def reload(self, from_cache: bool = False) -> None:
        ui = self.ui
        if from_cache and not self.painted:
            def cached():
                shelves, _saved = ui.store.load_home()
                return shelves, ui.store.history()

            ui.io(cached, self._cached, lambda _e: None)
        else:
            ui.io(ui.store.history, self._history, lambda _e: None)
        if not ui.supports(HOME):
            self._paint()
            return
        self.loading.set_active(True)
        region = ""
        ui.run(HOME, lambda: fetch_home(ui.engine, region), self._fresh,
               self._failed, self.generation)

    def shown(self) -> None:
        if self.painted:
            self.ui.io(self.ui.store.history, self._history,
                       lambda _e: None)

    def _cached(self, result) -> None:
        shelves, history = result
        self.history = history
        if shelves and not self.shelves:
            self.shelves = shelves
        self._paint()

    def _history(self, history) -> None:
        if [t.id for t in history] != [t.id for t in self.history]:
            self.history = history
            self._paint()

    def _fresh(self, shelves: list[Shelf]) -> None:
        self.loading.set_active(False)
        self.shelves = shelves
        self.note = ""
        self.ui.io(lambda: self.ui.store.save_home(shelves))
        self._paint()

    def _failed(self, error) -> None:
        self.loading.set_active(False)
        kind = getattr(error, "kind", ErrorKind.OTHER)
        self.note = ERROR_TEXT.get(kind, ERROR_TEXT[ErrorKind.OTHER])
        self._paint()

    def _repaint(self) -> None:
        if self.painted:
            self._paint()

    # -- paint --------------------------------------------------------------------
    def _paint(self) -> None:
        self.painted = True
        body = self.body
        common.clear(body)
        moods = next((s for s in self.shelves if s.title == MOODS_SHELF),
                     None)
        chips = self._chips(moods)
        if chips is not None:
            chips.set_margin_top(4)
            body.append(chips)
        if self.note:
            note = common.label(self.note, "xx-mu-inline-note", lines=3,
                                chars=30)
            holder = common.vbox(0)
            holder.add_css_class("xx-page-body")
            holder.append(note)
            body.append(holder)
        ordered = self._ordered()
        if not ordered:
            empty = common.empty_state(
                self.note or EMPTY_TEXT,
                "Search" if not self.note else "Retry",
                (lambda: self.ui.shell.show("search")) if not self.note
                else self.reload)
            empty.add_css_class("xx-page-body")
            empty.set_margin_top(48)
            body.append(empty)
            return
        for index, (title, shelf) in enumerate(ordered):
            head = common.section(title, first=index == 0)
            head.add_css_class("xx-page-body")
            body.append(head)
            body.append(self._shelf_widget(shelf))

    def _ordered(self) -> list[tuple[str, Shelf]]:
        engine_shelves = [s for s in self.shelves if s.items
                          and s.title != MOODS_SHELF
                          and not s.title.startswith(CHARTS_PREFIX)]
        charts = [s for s in self.shelves if s.items
                  and s.title.startswith(CHARTS_PREFIX)]
        moods = [s for s in self.shelves if s.items
                 and s.title == MOODS_SHELF]
        out: list[tuple[str, Shelf]] = []
        if engine_shelves:
            out.append((engine_shelves[0].title, engine_shelves[0]))
        if self.history:
            out.append((LOCAL_LISTEN_AGAIN, Shelf(
                LOCAL_LISTEN_AGAIN, _track_items(self.history[:12]))))
        for shelf in engine_shelves[1:]:
            out.append((shelf.title, shelf))
        local = [row.track for row in self.ui.downloads
                 if row.state == "done"]
        if local:
            out.append(("On this phone", Shelf(
                "On this phone", _track_items(local[:12]))))
        for shelf in charts:
            out.append((shelf.title[len(CHARTS_PREFIX):], shelf))
        for shelf in moods:
            out.append((shelf.title, shelf))
        return out

    def _chips(self, moods: Shelf | None):
        ui = self.ui
        from xx_music.ui import pages

        items = []
        if ui.supports(MOODS):
            items.append(("Moods & genres", False,
                          lambda: pages.open_moods(ui)))
        if ui.supports(CHARTS):
            items.append(("Charts", False, lambda: pages.open_charts(ui)))
        for item in (moods.items if moods else ())[:6]:
            items.append((item.title, False,
                          lambda i=item: pages.open_mood(
                              ui, Mood(i.id, i.title, i.subtitle))))
        return common.chips(items) if items else None

    def _shelf_widget(self, shelf: Shelf) -> Gtk.Widget:
        items = list(shelf.items)
        if minimal.active():
            return self._minimal_shelf(shelf.title, items)
        if all(i.kind == ItemKind.MOOD for i in items):
            return self._mood_cards(items)
        tracks = [i.track for i in items if i.track is not None]
        if len(tracks) == len(items) and len(items) > 2 \
                and shelf.title != LOCAL_LISTEN_AGAIN \
                and not css.large_text():
            return self._song_columns(tracks, shelf.title)
        tiles = []
        for item in items:
            offline = item.kind == ItemKind.TRACK and \
                item.id in self.ui.downloaded
            tiles.append(common.tile(
                self.ui, item, lambda i=item: self._open(i, items),
                lambda i=item: self._menu(i), offline=offline))
        return common.carousel(tiles)

    def _minimal_shelf(self, title: str, items: list[Item]) -> Gtk.Widget:
        """``Title — Artist`` rows, five before "more…"."""
        from xx_music.ui import pages

        def opener(item: Item):
            if item.kind == ItemKind.MOOD:
                return lambda: pages.open_mood(
                    self.ui, Mood(item.id, item.title, item.subtitle))
            return lambda: self._open(item, items)

        def toggle() -> None:
            self.expanded ^= {title}
            self._paint()

        entries = [(minimal.item_text(
            item, item.kind == ItemKind.TRACK
            and item.id in self.ui.downloaded), opener(item),
            None if item.kind == ItemKind.MOOD
            else (lambda i=item: self._menu(i))) for item in items]
        box = minimal.shelf(entries, title in self.expanded, toggle)
        box.add_css_class("xx-page-body")
        return box

    def _song_columns(self, tracks: list[Track], title: str) -> Gtk.Widget:
        columns = []
        for start in range(0, len(tracks), QUICK_ROWS):
            column = common.vbox(0)
            chunk = tracks[start:start + QUICK_ROWS]
            for offset, track in enumerate(chunk):
                column.append(common.track_row(
                    self.ui, track,
                    lambda t=track: self.ui.shell.play_song(t),
                    lambda t=track: menus.track_menu(self.ui, t),
                    offline=track.id in self.ui.downloaded,
                    last=offset == len(chunk) - 1, meta=""))
            columns.append(common.Clamp(column, COLUMN_PX))
        return common.carousel(columns, spacing=20)

    def _mood_cards(self, items: list[Item]) -> Gtk.Widget:
        from xx_music.ui import pages

        rows = (common.hbox(10), common.hbox(10))
        for index, item in enumerate(items):
            card = Gtk.Button()
            card.add_css_class("xx-mu-mood")
            inner = common.vbox(6)
            inner.append(common.caps(item.subtitle or "Mood", chars=12))
            inner.append(common.label(item.title, "xx-mu-h2", chars=9))
            card.set_child(common.Clamp(inner, 112))
            card.connect("clicked", lambda _b, i=item: pages.open_mood(
                self.ui, Mood(i.id, i.title, i.subtitle)))
            common.name(card, item.title)
            rows[index % 2].append(card)
        grid = common.vbox(10)
        grid.append(rows[0])
        if rows[1].get_first_child() is not None:
            grid.append(rows[1])
        return common.hscroll(grid)

    def _open(self, item: Item, siblings: list[Item]) -> None:
        if item.kind == ItemKind.TRACK and item.track is not None:
            self.ui.shell.play_song(item.track)
        else:
            self.ui.shell.open_item(item)

    def _menu(self, item: Item) -> None:
        if item.kind == ItemKind.TRACK and item.track is not None:
            menus.track_menu(self.ui, item.track)
        else:
            menus.item_menu(self.ui, item)


