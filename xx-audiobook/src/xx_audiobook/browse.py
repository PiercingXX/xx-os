"""Home shelves, browse views and collections (M3).

Mirrors data/repo/HomeRepository.kt, ui/browse/BrowseViewModel.kt
(ServerBrowseSource + LocalBrowseSource) and ui/collections (bookmarks
live in bookmarks.py). Server answers are written through the
item cache and read back from it, so rows carry live progress; offline
(or without a server id) the cache is searched by name. Synchronous:
callers run every method on a worker thread.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from xx_audiobook import api as api_module
from xx_audiobook import repos as repos_module
from xx_audiobook import store as store_module

HOME_SHELVES = ("continue_series", "listen_again", "discover")
SHELF_LABELS = {
    "continue": "Continue listening",
    "continue_series": "Continue the series",
    "listen_again": "Listen again",
    "discover": "Discover",
}
FOLDED_BY_DEFAULT = frozenset({"listen_again", "discover"})

SERIES = "series"
AUTHOR = "author"
NARRATOR = "narrator"
KIND_TITLES = {SERIES: "Series", AUTHOR: "Author", NARRATOR: "Narrator"}
LOCAL_LIMIT = 200


@dataclass
class Shelf:
    id: str
    label: str
    items: list = field(default_factory=list)


@dataclass
class BrowseBook:
    item: repos_module.LibraryItem
    sequence: str | None = None


@dataclass
class BrowseSeries:
    id: int
    name: str
    book_count: int = 0


@dataclass
class BrowseResult:
    books: list = field(default_factory=list)
    series: list = field(default_factory=list)
    error: Exception | None = None


@dataclass
class ListRow:
    id: str
    name: str
    kind: str
    description: str = ""
    book_count: int = 0
    thumb_urls: list = field(default_factory=list)


def _in_order(library, ids: list) -> list:
    rows = {row.id: row for row in library.observe_ids(ids)} if ids else {}
    return [rows[i] for i in ids if i in rows]


def pick_home_shelves(home: api_module.Home) -> list:
    """The server shelves the Library shows, in HOME_SHELVES order."""
    by_id = {shelf.id: shelf for shelf in home.shelves}
    picked = []
    for shelf_id in HOME_SHELVES:
        shelf = by_id.get(shelf_id)
        if shelf is not None and shelf.items():
            picked.append(shelf)
    return picked


class HomeRepository:
    """`GET api/me/home` shelves, kept in memory (online only)."""

    def __init__(self, api, library) -> None:
        self._api = api
        self._library = library
        self._ids: list = []

    def refresh(self) -> Exception | None:
        try:
            home = self._api.home()
        except api_module.ApiError as exc:
            return exc
        ids = []
        for shelf in pick_home_shelves(home):
            summaries = shelf.items()
            self._library.cache_summaries(summaries)
            ids.append((shelf.id, [s.id for s in summaries]))
        self._ids = ids
        return None

    def shelves(self) -> list:
        """Shelves read back from the cache, so progress is live."""
        return [Shelf(shelf_id, SHELF_LABELS[shelf_id],
                      _in_order(self._library, item_ids))
                for shelf_id, item_ids in self._ids]

    def clear(self) -> None:
        self._ids = []


def _sequence_key(book: BrowseBook) -> tuple:
    try:
        number = float(book.sequence) if book.sequence else float("inf")
    except ValueError:
        number = float("inf")
    return (number, book.item.title.lower())


def select_local(kind: str, name: str, rows: list) -> list:
    """Cache rows matching a series / author / narrator name (pure)."""
    wanted = name.strip().lower()
    if kind == SERIES:
        books = []
        for item in rows:
            series = (item.series or "").strip()
            if series.lower() == wanted:
                books.append(BrowseBook(item))
            elif series.lower().startswith(wanted + " #"):
                books.append(BrowseBook(item, series[len(wanted) + 2:]))
        return sorted(books, key=_sequence_key)
    matched = []
    for item in rows:
        names = item.authors if kind == AUTHOR else item.narrators
        if any(part.strip().lower() == wanted
               for part in (names or "").split(",")):
            matched.append(item)
    matched.sort(key=lambda i: ((i.series or "￿").lower(),
                                i.title.lower()))
    return [BrowseBook(item) for item in matched]


class BrowseRepository:
    """Series / Author / Narrator: server by id, else the cache by name."""

    def __init__(self, api, library) -> None:
        self._api = api
        self._library = library

    def local(self, kind: str, name: str) -> list:
        rows = self._library.observe(store_module.LibraryQuery(
            q=name, limit=LOCAL_LIMIT))
        return select_local(kind, name, rows)

    def load(self, kind: str, name: str, ref_id: int | None
             ) -> BrowseResult:
        if ref_id is None:
            return BrowseResult(self.local(kind, name))
        try:
            if kind == SERIES:
                detail = self._api.series(ref_id)
                self._library.cache_summaries(
                    [b.summary() for b in detail.books])
                order = [(b.id, detail.sequence_of(b)) for b in detail.books]
                series = []
            else:
                detail = (self._api.author(ref_id) if kind == AUTHOR
                          else self._api.narrator(ref_id))
                self._library.cache_summaries(detail.books)
                order = [(b.id, None) for b in detail.books]
                series = [BrowseSeries(s.id, s.name, s.book_count)
                          for s in detail.series]
        except api_module.ApiError as exc:
            return BrowseResult(self.local(kind, name), [], exc)
        rows = {row.id: row for row in self._library.observe_ids(
            [item_id for item_id, _seq in order])}
        books = [BrowseBook(rows[item_id], seq)
                 for item_id, seq in order if item_id in rows]
        return BrowseResult(books, series)


class CollectionsRepository:
    """The estate's collections and your playlists, list order kept."""

    def __init__(self, api, library) -> None:
        self._api = api
        self._library = library
        self._lists: tuple | None = None

    def lists(self) -> tuple:
        """(collections, playlists, error); the last answer when offline."""
        try:
            collections = [ListRow(c.id, c.name, "collection",
                                   c.description, c.book_count,
                                   c.thumb_urls)
                           for c in self._api.collections()]
            playlists = [ListRow(p.id, p.name, "playlist", p.description,
                                 p.book_count, p.thumb_urls)
                         for p in self._api.playlists()]
        except api_module.ApiError as exc:
            if self._lists is not None:
                return (*self._lists, exc)
            return ([], [], exc)
        self._lists = (collections, playlists)
        return (collections, playlists, None)

    def detail(self, kind: str, list_id: str) -> tuple:
        """(books in list order, error)."""
        try:
            detail = (self._api.collection(list_id) if kind == "collection"
                      else self._api.playlist(list_id))
        except api_module.ApiError as exc:
            return ([], exc)
        self._library.cache_summaries(detail.books)
        return (_in_order(self._library, [b.id for b in detail.books]),
                None)
