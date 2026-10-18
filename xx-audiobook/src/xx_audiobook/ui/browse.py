"""Pushed views: Series / Author / Narrator and Collections / Collection
(ui/browse/BrowseScreen.kt, ui/collections/CollectionsScreen.kt).

Each opens under a TopBar carrying its name, then one muted kind line
("Series · 3 books"), uppercase list labels and LibraryRows. Series,
author and narrator answer from the server by id and fall back to the
cache by name; the cached answer paints first. Collections keep the
server's list order. Rows open the Book; a series row opens Series.
"""

from __future__ import annotations

from gi.repository import Gtk

from xx_audiobook import browse as browse_module
from xx_audiobook import fmt
from xx_audiobook.ui import common
from xx_audiobook.ui.context import error_line
from xx_ui import widgets


def _kind_line(text: str) -> Gtk.Label:
    line = common.label(text, "xx-ab-l12 xx-ab-muted", chars=36)
    line.set_margin_top(8)
    line.set_margin_bottom(4)
    return line


class BrowsePage:
    """Every book in one series (sequence order, '#n'), by one author,
    or read by one narrator (with their series)."""

    def __init__(self, ctx, kind: str, name: str, ref_id, nav) -> None:
        self.ctx, self.kind, self.name, self.ref_id = ctx, kind, name, ref_id
        self.nav = nav
        self.body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self.body.set_margin_bottom(24)
        header = common.header(nav.back, name)
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
        books = result.books
        kind = browse_module.KIND_TITLES[self.kind]
        body.append(_kind_line(
            kind + (f" · {fmt.books_count(len(books))}" if books else "")))
        line = error_line(result.error) if result.error else None
        if line and result.error is not None:
            body.append(common.muted(line))
        if result.series:
            body.append(common.caps_section("Series"))
            for series in result.series:
                text = (f"{series.name} · "
                        f"{fmt.books_count(series.book_count)}")
                link = common.label(text, "xx-ab-t14 xx-ab-link", lines=2,
                                    chars=30)
                row = common.tap_row(
                    link, f"Open series {text}",
                    lambda s=series: self.nav.browse(
                        browse_module.SERIES, s.name, s.id))
                body.append(row)
            body.append(common.caps_section("Books"))
        for index, book in enumerate(books):
            if self.kind == browse_module.SERIES:
                prefix = f"#{book.sequence}" if book.sequence else None
            else:
                prefix = book.item.series
            body.append(common.book_row(
                self.ctx, book.item, self.nav.open_item, prefix=prefix,
                last=index == len(books) - 1))


def _mosaic(ctx, row: browse_module.ListRow) -> Gtk.Widget:
    """A 56 px cover: the list's first book, or the book glyph."""
    ids = [i for i in (_item_of(url) for url in row.thumb_urls[:1]) if i]
    first = ids[0] if ids else ""
    return common.cover(ctx, first, row.name, 56, bool(first))


def _item_of(url: str) -> str | None:
    """'api/items/{id}/cover?...' -> id (thumb urls carry the item id)."""
    parts = (url or "").split("?")[0].strip("/").split("/")
    if "items" in parts:
        index = parts.index("items")
        if index + 1 < len(parts):
            return parts[index + 1]
    return None


def list_row(ctx, row, on_open, last: bool = False) -> Gtk.Button:
    """ListRow: cover, name, 'N books', the description on one line."""
    box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
    art = _mosaic(ctx, row)
    art.set_valign(Gtk.Align.CENTER)
    box.append(art)
    text = Gtk.Box.new(Gtk.Orientation.VERTICAL, 1)
    text.set_hexpand(True)
    text.set_valign(Gtk.Align.CENTER)
    text.append(common.label(row.name, "xx-ab-t16", lines=2, chars=20))
    text.append(common.label(fmt.books_count(row.book_count),
                             "xx-ab-l12 xx-ab-muted", chars=20))
    if row.description:
        text.append(common.label(row.description, "xx-ab-b11 xx-ab-muted",
                                 chars=28))
    box.append(text)
    return common.tap_row(box, f"Open {row.name}, "
                          f"{fmt.books_count(row.book_count)}",
                          on_open, last)


class CollectionsPage:
    """The estate's collections, then your playlists (read-only)."""

    def __init__(self, ctx, nav) -> None:
        self.ctx, self.nav = ctx, nav
        self.body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self.body.set_margin_bottom(24)
        header = common.header(nav.back, "Collections")
        self.widget = widgets.page(header, self.body)
        self.body.append(common.muted("Loading…"))
        ctx.run(ctx.collections.lists, self._loaded)

    def _loaded(self, result, error) -> None:
        body = self.body
        common.clear(body)
        if error is not None or result is None:
            body.append(common.muted(error_line(error) or ""))
            return
        collections, playlists, api_error = result
        if api_error is not None:
            body.append(common.muted(error_line(api_error)))
        for caption, rows in (("Collections", collections),
                              ("Your playlists", playlists)):
            if not rows:
                continue
            if caption != "Collections" or playlists:
                body.append(common.caps_section(caption))
            for index, row in enumerate(rows):
                body.append(list_row(
                    self.ctx, row, lambda r=row: self.nav.collection(r),
                    last=index == len(rows) - 1))


class CollectionPage:
    """One collection or playlist: its books in list order."""

    def __init__(self, ctx, row, nav) -> None:
        self.ctx, self.row, self.nav = ctx, row, nav
        self.body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self.body.set_margin_bottom(24)
        kind = "Playlist" if row.kind == "playlist" else "Collection"
        header = common.header(nav.back, row.name or kind)
        self.widget = widgets.page(header, self.body)
        self.kind = kind
        self._render([], None, loading=True)
        ctx.run(lambda: ctx.collections.detail(row.kind, row.id),
                lambda result, err: self._render(
                    *(result if err is None else ([], err))))

    def _render(self, books: list, error, loading: bool = False) -> None:
        body = self.body
        common.clear(body)
        count = self.row.book_count if loading or error else len(books)
        body.append(_kind_line(
            self.kind + ("" if loading or error is not None
                         else f" · {fmt.books_count(count)}")))
        for text in fmt.description_paragraphs(self.row.description or ""):
            body.append(common.para(text, "xx-ab-b13"))
        if error is not None:
            body.append(common.muted(error_line(error)))
        elif loading:
            body.append(common.muted("Loading…"))
        for index, item in enumerate(books):
            body.append(common.book_row(self.ctx, item, self.nav.open_item,
                                        last=index == len(books) - 1))
