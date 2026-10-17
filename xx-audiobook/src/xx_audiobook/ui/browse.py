"""Pushed views (M3): Series / Author / Narrator and Collections /
Collection (ui/browse/BrowseScreen.kt, ui/collections/).

Series, author and narrator answer from the server by id and fall back
to the cache by name; the cached answer paints first. Collections keep
the server's list order. Rows open the Book; a series row opens Series.
"""

from __future__ import annotations

from gi.repository import Gtk

from xx_audiobook import browse as browse_module
from xx_audiobook import fmt
from xx_audiobook.ui import common
from xx_audiobook.ui.context import error_line
from xx_ui import widgets


def _title_block(kind_text: str, title: str) -> Gtk.Box:
    box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 4)
    box.set_margin_top(4)
    box.append(common.caps(kind_text))
    box.append(common.label(title, "xx-ab-heading", lines=3, chars=20))
    return box


class BrowsePage:
    """Every book in one series (sequence order, '#n'), by one author,
    or read by one narrator (with their series)."""

    def __init__(self, ctx, kind: str, name: str, ref_id, nav) -> None:
        self.ctx, self.kind, self.name, self.ref_id = ctx, kind, name, ref_id
        self.nav = nav
        self.body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self.body.set_margin_bottom(24)
        header = common.header(
            nav.back, browse_module.KIND_TITLES[kind])
        self.widget = widgets.page(header, self.body)
        self.loading = True
        self._render(browse_module.BrowseResult())
        ctx.run(lambda: ctx.browse.local(kind, name),
                lambda books, err: self._render(
                    browse_module.BrowseResult(books or [])) if err is None
                else None)
        ctx.run(lambda: ctx.browse.load(kind, name, ref_id), self._loaded)

    def _loaded(self, result, error) -> None:
        self.loading = False
        if error is None:
            self._render(result)

    def _render(self, result) -> None:
        body = self.body
        common.clear(body)
        body.append(_title_block(
            browse_module.KIND_TITLES[self.kind], self.name))
        books = result.books
        if books:
            in_progress = sum(1 for b in books
                              if 0 < b.item.fraction < 1)
            finished = sum(1 for b in books if b.item.is_finished)
            facts = [fmt.books_count(len(books))]
            if in_progress:
                facts.append(f"{in_progress} in progress")
            if finished:
                facts.append(f"{finished} finished")
            meta = common.label("  ·  ".join(facts), "xx-ab-meta", chars=36)
            meta.set_margin_top(6)
            body.append(meta)
        line = error_line(result.error) if result.error else None
        if line and result.error is not None:
            body.append(common.muted(line))
        if not books and not self.loading:
            body.append(common.muted(
                f"No books on this phone for {self.name} yet."))
        if result.series:
            body.append(common.section("Series"))
            for index, series in enumerate(result.series):
                body.append(widgets.list_row(
                    series.name, fmt.books_count(series.book_count),
                    trailing=widgets.chevron(),
                    on_activate=lambda s=series: self.nav.browse(
                        browse_module.SERIES, s.name, s.id),
                    last=index == len(result.series) - 1))
            body.append(common.section("Books"))
        elif books:
            spacer = Gtk.Box()
            spacer.set_size_request(-1, 12)
            body.append(spacer)
        for index, book in enumerate(books):
            if self.kind == browse_module.SERIES:
                prefix = f"#{book.sequence}" if book.sequence else None
            else:
                prefix = book.item.series
            body.append(common.book_row(
                self.ctx, book.item, self.nav.open_item, prefix=prefix,
                last=index == len(books) - 1))


def _mosaic(ctx, row: browse_module.ListRow) -> Gtk.Widget:
    """A 56 px tile: the list's first covers 2x2, or its initials."""
    grid = Gtk.Grid()
    grid.set_row_spacing(2)
    grid.set_column_spacing(2)
    ids = [_item_of(url) for url in row.thumb_urls[:4]]
    ids = [i for i in ids if i]
    if len(ids) < 4:
        return common.cover(ctx, "", row.name, 56, False)
    for index, item_id in enumerate(ids):
        grid.attach(common.cover(ctx, item_id, "", 27, True),
                    index % 2, index // 2, 1, 1)
    return grid


def _item_of(url: str) -> str | None:
    """'api/items/{id}/cover?...' -> id (thumb urls carry the item id)."""
    parts = (url or "").split("?")[0].strip("/").split("/")
    if "items" in parts:
        index = parts.index("items")
        if index + 1 < len(parts):
            return parts[index + 1]
    return None


class CollectionsPage:
    """The estate's collections, then your playlists (read-only)."""

    def __init__(self, ctx, nav) -> None:
        self.ctx, self.nav = ctx, nav
        self.body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self.body.set_margin_bottom(24)
        header = common.header(nav.back, "Collections")
        self.widget = widgets.page(header, self.body)
        self.body.append(_title_block("Library", "Collections"))
        self.body.append(common.muted("Loading…"))
        ctx.run(ctx.collections.lists, self._loaded)

    def _loaded(self, result, error) -> None:
        body = self.body
        common.clear(body)
        body.append(_title_block("Library", "Collections"))
        if error is not None or result is None:
            body.append(common.muted(error_line(error) or ""))
            return
        collections, playlists, api_error = result
        if api_error is not None:
            body.append(common.muted(error_line(api_error)))
        if not collections and not playlists and api_error is None:
            body.append(common.muted("No collections or playlists yet."))
        for caption, rows in (("Collections", collections),
                              ("Your playlists", playlists)):
            if not rows:
                continue
            body.append(common.section(caption))
            for index, row in enumerate(rows):
                sub = fmt.books_count(row.book_count)
                if row.description:
                    sub += " · " + row.description
                body.append(widgets.list_row(
                    row.name, sub, leading=_mosaic(self.ctx, row),
                    trailing=widgets.chevron(),
                    on_activate=lambda r=row: self.nav.collection(r),
                    last=index == len(rows) - 1))


class CollectionPage:
    """One collection or playlist: its books in list order."""

    def __init__(self, ctx, row, nav) -> None:
        self.ctx, self.row, self.nav = ctx, row, nav
        self.body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self.body.set_margin_bottom(24)
        kind = "Playlist" if row.kind == "playlist" else "Collection"
        header = common.header(nav.back, kind)
        self.widget = widgets.page(header, self.body)
        self.kind = kind
        self._render([], None, loading=True)
        ctx.run(lambda: ctx.collections.detail(row.kind, row.id),
                lambda result, err: self._render(
                    *(result if err is None else ([], err))))

    def _render(self, books: list, error, loading: bool = False) -> None:
        body = self.body
        common.clear(body)
        body.append(_title_block(self.kind, self.row.name))
        if self.row.description:
            body.append(common.para(self.row.description, "xx-secondary"))
        if error is not None:
            body.append(common.muted(error_line(error)))
        elif loading:
            body.append(common.muted("Loading…"))
        elif not books:
            body.append(common.muted("This list is empty."))
        spacer = Gtk.Box()
        spacer.set_size_request(-1, 12)
        body.append(spacer)
        for index, item in enumerate(books):
            body.append(common.book_row(self.ctx, item, self.nav.open_item,
                                        last=index == len(books) - 1))
