"""Search: a boxed field, recent searches, suggestions, filtered results.

Empty field: recent searches as chips (tap to search again, Clear to
forget them). Typing: suggestions after a short pause (stale answers are
dropped by a Generation). Enter or a suggestion: results, with a filter
row (All, Songs, Albums, Artists, Playlists). Tapping a song plays it and
starts its radio; long-press (or More) opens Play next, Add to queue,
Start radio, Go to artist/album, Download, Like, Dislike.

Minimal Mode: the field without glyphs (``clear`` is a word), the
filters and recent searches as words, rows without art or icons.
"""

from __future__ import annotations

from gi.repository import GLib, Gtk

from xx_music.engine import (ERROR_TEXT, SEARCH, SUGGEST, ErrorKind, Item,
                             ItemKind, SearchResults)
from xx_music.tasks import Generation
from xx_music.ui import common, menus, minimal
from xx_ui import widgets

SUGGEST_DELAY_MS = 260
EMPTY_TEXT = "Find songs, albums, artists and playlists."
NOTHING_TEXT = "Nothing matches that. Try fewer words."
FILTERS = (("All", None), ("Songs", ItemKind.TRACK),
           ("Albums", ItemKind.ALBUM), ("Artists", ItemKind.ARTIST),
           ("Playlists", ItemKind.PLAYLIST))
ALL_LIMITS = {ItemKind.TRACK: 4, ItemKind.ALBUM: 3, ItemKind.ARTIST: 3,
              ItemKind.PLAYLIST: 3}


class SearchPage:
    def __init__(self, ui) -> None:
        self.ui = ui
        self.query = ""
        self.kind: ItemKind | None = None
        self.results: SearchResults | None = None
        self.recents: list[str] = []
        self.suggestions: list[str] = []
        self.mode = "empty"
        self.note = ""
        self._suggest_gen = Generation()
        self._search_gen = Generation()
        self._timer = 0
        self._quiet = False

        bar = widgets.search_bar("Songs, albums, artists", boxed=True)
        bar.set_margin_start(20)
        bar.set_margin_end(20)
        bar.set_margin_top(4)
        bar.set_margin_bottom(8)
        self.bar = bar
        self.entry = bar.get_last_child()
        self.clear_word = minimal.word_button(
            "clear", lambda: self.set_text(""), spoken="Clear the search")
        self.clear_word.set_visible(False)
        bar.append(self.clear_word)
        self.entry.set_can_focus(False)
        tap = Gtk.GestureClick.new()
        tap.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        tap.connect("pressed", lambda *_a: self._focus())
        self.entry.add_controller(tap)
        self._style_bar()
        self.entry.connect("changed", self._changed)
        self.entry.connect("activate", lambda _e: self.submit(
            self.entry.get_text()))
        self.entry.connect("icon-press", self._icon)
        self.entry.update_property([Gtk.AccessibleProperty.LABEL],
                                   ["Search"])

        self.loading = common.LoadingLine()
        self.filters = common.vbox(0)
        self.body = common.vbox(0)
        self.body.set_margin_bottom(28)
        root = common.vbox(0)
        root.append(widgets.page_header("Search"))
        root.append(bar)
        root.append(self.loading)
        root.append(self.filters)
        root.append(common.scroller(self.body))
        self.widget = root
        ui.io(ui.store.recents, self._got_recents, lambda _e: None)
        minimal.listen(self._minimal_changed)

    def _style_bar(self) -> None:
        """The search glyph, or (Minimal Mode) none."""
        self.entry.set_icon_from_icon_name(
            Gtk.EntryIconPosition.PRIMARY,
            None if minimal.active() else "system-search-symbolic")
        self._sync_clear_icon()

    def _minimal_changed(self) -> None:
        self._style_bar()
        self._paint()

    # -- entry ---------------------------------------------------------------------
    def _focus(self) -> None:
        self.entry.set_can_focus(True)
        self.entry.grab_focus()

    def _icon(self, _entry, position) -> None:
        if position == Gtk.EntryIconPosition.SECONDARY:
            self.set_text("")

    def set_text(self, text: str) -> None:
        self._quiet = True
        self.entry.set_text(text)
        self._quiet = False
        self._sync_clear_icon()
        if not text:
            self.mode = "empty"
            self.results = None
            self._paint()

    def _sync_clear_icon(self) -> None:
        typed = bool(self.entry.get_text())
        words = minimal.active()
        self.entry.set_icon_from_icon_name(
            Gtk.EntryIconPosition.SECONDARY,
            "edit-clear-symbolic" if typed and not words else None)
        self.clear_word.set_visible(typed and words)

    def _changed(self, _entry) -> None:
        self._sync_clear_icon()
        if self._quiet:
            return
        text = self.entry.get_text()
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        if not text.strip():
            self.mode = "empty"
            self.results = None
            self._suggest_gen.next()
            self._paint()
            return
        self.mode = "typing"
        if not self.ui.supports(SUGGEST):
            self._paint()
            return
        self._timer = GLib.timeout_add(SUGGEST_DELAY_MS, self._suggest)

    def _suggest(self) -> bool:
        self._timer = 0
        text = self.entry.get_text()
        self.ui.run(SUGGEST, lambda: self.ui.engine.suggestions(text),
                    self._got_suggestions, lambda _e: None,
                    self._suggest_gen)
        return False

    def _got_suggestions(self, found: list[str]) -> None:
        if self.mode != "typing":
            return
        self.suggestions = list(found)[:8]
        self._paint()

    def _got_recents(self, recents: list[str]) -> None:
        self.recents = recents
        if self.mode == "empty":
            self._paint()

    # -- searching ---------------------------------------------------------------------
    def submit(self, text: str) -> None:
        query = " ".join(text.split())
        if not query or not self.ui.supports(SEARCH):
            return
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        self._suggest_gen.next()
        if self.entry.get_text() != query:
            self.set_text(query)
        self.query = query
        self.mode = "results"
        self.note = ""
        self.ui.io(lambda: self.ui.store.add_recent(query),
                   self._got_recents_quiet, lambda _e: None)
        self._run()

    def _got_recents_quiet(self, recents: list[str]) -> None:
        self.recents = recents

    def _run(self) -> None:
        query, kind = self.query, self.kind
        self.loading.set_active(True)
        self._paint_filters()
        self.ui.run(SEARCH, lambda: self.ui.engine.search(
            query, kind, 20 if kind else 8), self._got_results,
            self._failed, self._search_gen)

    def set_kind(self, kind: ItemKind | None) -> None:
        if kind == self.kind:
            return
        self.kind = kind
        if self.mode == "results":
            self._run()

    def _got_results(self, results: SearchResults) -> None:
        self.loading.set_active(False)
        self.results = results
        self._paint()

    def _failed(self, error) -> None:
        self.loading.set_active(False)
        kind = getattr(error, "kind", ErrorKind.OTHER)
        self.note = ERROR_TEXT.get(kind, ERROR_TEXT[ErrorKind.OTHER])
        self._paint()

    def reload(self) -> None:
        if self.mode == "results":
            self._run()

    # -- paint ---------------------------------------------------------------------------
    def _paint_filters(self) -> None:
        common.clear(self.filters)
        if self.mode != "results":
            return
        bar = common.chips([(text, kind == self.kind,
                             lambda k=kind: self.set_kind(k))
                            for text, kind in FILTERS])
        bar.set_margin_bottom(6)
        self.filters.append(bar)

    def _paint(self) -> None:
        self._paint_filters()
        body = self.body
        common.clear(body)
        if self.mode == "empty":
            self._paint_recents()
        elif self.mode == "typing":
            self._paint_suggestions()
        else:
            self._paint_results()

    def _gutter(self, widget: Gtk.Widget) -> Gtk.Widget:
        widget.add_css_class("xx-page-body")
        self.body.append(widget)
        return widget

    def _paint_recents(self) -> None:
        if not self.recents:
            line = common.muted(EMPTY_TEXT)
            line.set_margin_top(24)
            self._gutter(line)
            return
        self._gutter(common.section("Recent searches", "Clear",
                                    self._clear_recents, first=True))
        self._gutter(common.flow_chips(
            [(q, False, lambda q=q: self.submit(q)) for q in self.recents],
            per_line=3))

    def _clear_recents(self) -> None:
        self.recents = []
        self.ui.io(self.ui.store.clear_recents)
        self._paint()

    def _paint_suggestions(self) -> None:
        text = self.entry.get_text().strip()
        options = [text] + [s for s in self.suggestions
                            if s.casefold() != text.casefold()]
        box = common.vbox(0)
        for index, suggestion in enumerate(options):
            box.append(self._suggestion_row(suggestion, index == 0,
                                            index == len(options) - 1))
        self._gutter(box)

    def _suggestion_row(self, text: str, typed: bool,
                        last: bool) -> Gtk.Widget:
        if minimal.active():
            return minimal.text_row(
                text, lambda: self.submit(text),
                tone=minimal.Tone.PLAIN if typed else minimal.Tone.QUIET,
                spoken=f"Search for {text}")
        row = Gtk.Button()
        row.add_css_class("xx-mu-row")
        if last:
            row.add_css_class("last")
        line = common.hbox(14)
        glyph = widgets.icon_image("system-search-symbolic", 18)
        glyph.add_css_class("xx-mu-faint")
        line.append(glyph)
        label = common.label(text, "xx-title" if typed else "xx-body",
                             chars=22)
        label.set_hexpand(True)
        line.append(label)
        row.set_child(line)
        row.connect("clicked", lambda _b: self.submit(text))
        common.name(row, f"Search for {text}")
        return row

    def _paint_results(self) -> None:
        if self.note:
            note = common.muted(self.note)
            self._gutter(note)
        results = self.results
        if results is None:
            return
        if results.empty:
            if not self.note:
                self._gutter(common.muted(NOTHING_TEXT))
            return
        if self.kind is not None:
            self._paint_kind(results, self.kind, limit=None, head=False)
            return
        first = True
        for kind in (ItemKind.TRACK, ItemKind.ARTIST, ItemKind.ALBUM,
                     ItemKind.PLAYLIST):
            if self._paint_kind(results, kind, ALL_LIMITS[kind],
                                head=True, first=first):
                first = False

    def _paint_kind(self, results: SearchResults, kind: ItemKind,
                    limit: int | None, head: bool,
                    first: bool = False) -> bool:
        ui = self.ui
        title = dict((k, t) for t, k in FILTERS)[kind]
        if kind == ItemKind.TRACK:
            tracks = list(results.tracks)
            if not tracks:
                return False
            if head:
                more = len(tracks) > (limit or 0)
                self._gutter(common.section(
                    title, "See all" if more else "",
                    lambda: self.set_kind(ItemKind.TRACK), first=first))
            shown = tracks[:limit] if limit else tracks
            box = common.vbox(0)
            for index, track in enumerate(shown):
                box.append(common.track_row(
                    ui, track, lambda t=track: ui.shell.play_song(t),
                    lambda t=track: menus.track_menu(ui, t),
                    offline=track.id in ui.downloaded, lead="Song",
                    last=index == len(shown) - 1))
            self._gutter(box)
            return True
        items: list[Item] = list({
            ItemKind.ALBUM: results.albums, ItemKind.ARTIST: results.artists,
            ItemKind.PLAYLIST: results.playlists}[kind])
        if not items:
            return False
        if head:
            more = len(items) > (limit or 0)
            self._gutter(common.section(
                title, "See all" if more else "",
                lambda k=kind: self.set_kind(k), first=first))
        shown = items[:limit] if limit else items
        box = common.vbox(0)
        for index, item in enumerate(shown):
            box.append(common.item_row(
                ui, item, lambda i=item: ui.shell.open_item(i),
                lambda i=item: menus.item_menu(ui, i),
                last=index == len(shown) - 1))
        self._gutter(box)
        return True
