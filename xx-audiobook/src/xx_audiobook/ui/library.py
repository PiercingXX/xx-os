"""Library (M3): search, sort + show chips, list/grid (remembered), the
Continue listening / Continue the series / Listen again / Discover
shelves, load-more paging and offline marks (ui/library/LibraryScreen.kt).

The cache is the UI: every render reads SQLite on a worker; refreshes
write the server's pages into it, then re-read. Shelves hide while
searching or filtering; one muted line covers empty / error / offline.
"""

from __future__ import annotations

from gi.repository import GLib, Gtk

from xx_audiobook import browse as browse_module
from xx_audiobook import store as store_module
from xx_audiobook.ui import common
from xx_audiobook.ui.context import error_line
from xx_ui import widgets

PAGE = 50
SORTS = (("Title", store_module.Sort.TITLE),
         ("Author", store_module.Sort.AUTHOR),
         ("Added", store_module.Sort.ADDED),
         ("Recent", store_module.Sort.LAST_PLAYED),
         ("Rating", store_module.Sort.RATING))
FILTERS = (("In progress", store_module.Filter.IN_PROGRESS),
           ("Finished", store_module.Filter.FINISHED),
           ("Not started", store_module.Filter.NOT_STARTED))
DOWNLOADED = "downloaded"


def deferred(entry: Gtk.Entry) -> Gtk.Entry:
    """No startup focus (no keyboard pop); the first tap focuses."""
    entry.set_can_focus(False)
    tap = Gtk.GestureClick.new()
    tap.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

    def _enable(*_args) -> None:
        entry.set_can_focus(True)
        entry.grab_focus()

    tap.connect("pressed", _enable)
    entry.add_controller(tap)
    return entry


class LibraryPage:
    def __init__(self, ctx, open_book, open_collections) -> None:
        self.ctx = ctx
        self._open_book = open_book
        self.q = ""
        self.sort = store_module.Sort.TITLE
        self.descending = False
        self.show = None
        self.limit = PAGE
        self.has_more = False
        self.status: str | None = None
        self.folded = set(browse_module.FOLDED_BY_DEFAULT)
        self._search_id = 0
        self._loading_more = False

        top = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        top.add_css_class("xx-page-body")
        top.add_css_class("xx-page-top")
        title = widgets.label("Library", "xx-display")
        title.add_css_class("xx-display-face")
        title.set_hexpand(True)
        top.append(title)
        top.append(common.icon_button("folder-music-symbolic", "Collections",
                                      open_collections))
        self.view_button = common.icon_button(
            "view-grid-symbolic", "Show as grid", self.toggle_grid)
        top.append(self.view_button)

        search = widgets.search_bar("Title, author, series",
                                    on_changed=self._typed)
        self.entry = deferred(search.get_last_child())
        self.entry.update_property([Gtk.AccessibleProperty.LABEL],
                                   ["Search the library"])
        self.sort_row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        self.show_row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        header = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        for part in (top, search, self.sort_row, self.show_row):
            header.append(part)

        self.body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self.body.set_margin_bottom(16)
        self.widget = widgets.page(header, self.body)
        self.scroller = self.widget.get_last_child()
        self.scroller.connect("edge-reached", self._edge)
        self._chips()
        ctx.connect("ready", self.refresh)
        ctx.connect("library-changed", self.reload)

    # -- state -----------------------------------------------------------------

    @property
    def filtered(self) -> bool:
        return bool(self.q.strip()) or self.show is not None

    def query(self, limit: int | None = None,
              offset: int = 0) -> store_module.LibraryQuery:
        ids = (sorted(self.ctx.downloaded) if self.show == DOWNLOADED
               else None)
        return store_module.LibraryQuery(
            q=self.q.strip() or None, sort=self.sort,
            descending=self.descending,
            filter=None if self.show in (None, DOWNLOADED) else self.show,
            limit=limit or self.limit, offset=offset, ids=ids)

    def _chip_row(self, row: Gtk.Box, caption: str, items: list) -> None:
        common.clear(row)
        row.add_css_class("xx-page-body")
        row.add_css_class("xx-ab-chiprow")
        head = common.caps(caption)
        head.set_size_request(44, -1)
        row.append(head)
        bar = widgets.chip_bar(items, spacing=6)
        bar.set_hexpand(True)
        row.append(bar)

    def _chips(self) -> None:
        arrow = " ↓" if self.descending else " ↑"
        self._chip_row(self.sort_row, "Sort", [
            (text + (arrow if sort == self.sort else ""), sort == self.sort,
             lambda s=sort: self.set_sort(s)) for text, sort in SORTS])
        shows = [(text, value) for text, value in FILTERS]
        shows.append(("Downloaded", DOWNLOADED))
        self._chip_row(self.show_row, "Show", [
            (text, value == self.show, lambda v=value: self.set_show(v))
            for text, value in shows])

    def set_sort(self, sort) -> None:
        if sort == self.sort:
            self.descending = not self.descending
        else:
            self.sort = sort
            self.descending = sort in (store_module.Sort.ADDED,
                                       store_module.Sort.LAST_PLAYED,
                                       store_module.Sort.RATING)
        self._changed()

    def set_show(self, value) -> None:
        self.show = None if value == self.show else value
        self._changed()

    def _changed(self) -> None:
        self.limit = PAGE
        self._chips()
        self.refresh()

    def _typed(self, text: str) -> None:
        self.q = text
        if self._search_id:
            GLib.source_remove(self._search_id)
        self._search_id = GLib.timeout_add(300, self._search_now)

    def _search_now(self) -> bool:
        self._search_id = 0
        self.limit = PAGE
        self.refresh()
        return False

    def toggle_grid(self) -> None:
        grid = not self.ctx.ui_prefs.get("library_grid", False)
        self.ctx.save_ui_prefs(library_grid=grid)
        self.reload()

    # -- data ------------------------------------------------------------------

    def refresh(self) -> None:
        """Re-read the cache now, then ask the server for page 1."""
        if self.ctx.db is None:
            return
        self.reload()
        query = self.query(limit=PAGE)
        on_shelves = not self.filtered
        ctx = self.ctx

        def _work():
            ok, has_more, error = (True, False, None)
            if self.show != DOWNLOADED:
                ok, has_more, error = ctx.library.refresh(query)
            if on_shelves and error is None:
                ctx.library.refresh_continue()
                ctx.home.refresh()
            return (has_more, error)

        self.ctx.run(_work, self._refreshed)

    def _refreshed(self, result, error) -> None:
        if error is None and result is not None:
            self.has_more, error = result
        self.status = error_line(error)
        self.reload()

    def load_more(self) -> None:
        if self._loading_more or not self.has_more_rows():
            return
        self._loading_more = True
        query = self.query(limit=PAGE, offset=self.limit)

        def _work():
            if self.ctx.library is None or self.show == DOWNLOADED:
                return (False, None)
            ok, has_more, error = self.ctx.library.refresh(query)
            return (has_more, error)

        def _done(result, _error) -> None:
            self._loading_more = False
            if result is not None:
                self.has_more = result[0]
            self.limit += PAGE
            self.reload()

        self.ctx.run(_work, _done)

    def has_more_rows(self) -> bool:
        return self.has_more or self._last_count >= self.limit

    _last_count = 0

    def _edge(self, _scroller, position) -> None:
        if position == Gtk.PositionType.BOTTOM:
            self.load_more()

    def reload(self) -> None:
        if self.ctx.db is None:
            return
        query = self.query()
        shelves = not self.filtered
        ctx = self.ctx

        def _read():
            rows = ctx.library.observe(query)
            shelf_rows = []
            if shelves:
                cont = ctx.library.observe_continue()
                if cont:
                    shelf_rows.append(browse_module.Shelf(
                        "continue", browse_module.SHELF_LABELS["continue"],
                        cont))
                shelf_rows.extend(s for s in ctx.home.shelves() if s.items)
            return rows, shelf_rows

        self.ctx.run(_read, self._render)

    # -- render ------------------------------------------------------------------

    def _render(self, result, error) -> None:
        if error is not None or result is None:
            return
        rows, shelves = result
        self._last_count = len(rows)
        grid = self.ctx.ui_prefs.get("library_grid", False)
        common.name(self.view_button,
                    "Show as list" if grid else "Show as grid")
        self.view_button.get_child().set_from_icon_name(
            "view-list-bullet-symbolic" if grid else "view-grid-symbolic")
        body = self.body
        common.clear(body)
        if self.status:
            body.append(common.muted(self.status))
        if not rows and not self.status:
            body.append(common.muted(
                "No downloaded books. Download one from its page."
                if self.show == DOWNLOADED else
                "No books match." if self.filtered else
                "No books yet."))
        for shelf in shelves:
            self._shelf(body, shelf)
        if shelves and rows:
            body.append(common.section("All books"))
        if grid:
            self._grid(body, rows)
        else:
            for index, item in enumerate(rows):
                body.append(common.book_row(
                    self.ctx, item, self._open_book,
                    last=index == len(rows) - 1))
        if rows and self.has_more_rows():
            more = common.fit(widgets.outline_button(
                "More books", self.load_more), 16)
            more.set_margin_top(16)
            body.append(more)

    def _shelf(self, body: Gtk.Box, shelf) -> None:
        open_now = shelf.id not in self.folded
        head = Gtk.Button()
        head.add_css_class("xx-ab-shelf-head")
        row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
        text = common.caps(shelf.label)
        text.set_hexpand(True)
        row.append(text)
        row.append(widgets.icon_image(
            "pan-up-symbolic" if open_now else "pan-down-symbolic", 16))
        head.set_child(row)
        head.set_margin_top(8)
        common.name(head, f"{shelf.label}, {'shown' if open_now else 'folded'}")
        head.connect("clicked", lambda _b: self._fold(shelf.id))
        body.append(head)
        if not open_now:
            return
        strip = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        px = 116 if shelf.id == "continue" else 96
        for item in shelf.items:
            strip.append(common.book_tile(self.ctx, item, self._open_book,
                                          px=px))
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroller.set_propagate_natural_height(True)
        scroller.set_child(strip)
        body.append(scroller)

    def _fold(self, shelf_id: str) -> None:
        self.folded ^= {shelf_id}
        self.reload()

    def _grid(self, body: Gtk.Box, rows: list) -> None:
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_homogeneous(True)
        flow.set_min_children_per_line(2)
        flow.set_max_children_per_line(2)
        flow.set_column_spacing(14)
        flow.set_row_spacing(20)
        flow.set_margin_top(8)
        for item in rows:
            flow.append(common.book_tile(self.ctx, item, self._open_book))
        child = flow.get_first_child()
        while child is not None:
            child.set_focusable(False)
            child = child.get_next_sibling()
        body.append(flow)
