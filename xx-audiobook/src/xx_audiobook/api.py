"""DTOs and urllib HTTP client for the audiobook server contract.

Mirrors data/api/Dto.kt (epoch-seconds wire, relative track/cover urls) and
data/api/HttpAudiobookApi.kt (hand-rolled calls, no Retrofit): base
{origin}/audiobook/ with a trailing slash, ids stripped of '/', lists
decoding from a bare array or an object holding it, server urls resolved
keeping the /audiobook/ prefix, 10 s connect / 30 s read timeouts (120 s
for the manifest), no redirects.

Every call raises ApiError: Http for status >= 400 (with the body's
reason), Unreachable when the origin cannot be reached, Timeout for a slow
server (a subclass of Unreachable), NotSignedIn with no session.

Blocking: every method blocks on the network. Call from worker threads and
post results with GLib.idle_add (see submit()); never the GTK main loop.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

CONNECT_TIMEOUT_S = 10.0
READ_TIMEOUT_S = 30.0
MANIFEST_READ_TIMEOUT_S = 120.0
COVER_MAX_BYTES = 8 * 1024 * 1024

TIMEOUT_MESSAGE = ("The server is taking too long to answer."
                   " Try again in a moment.")
NOT_PLAYABLE_MESSAGE = "This book's audio file is damaged or incomplete."
NOT_GRANTED_MESSAGE = ("Your estate account doesn't have Audiobook access"
                       " yet. Ask the admin to grant it in xx-apps.")
FORBIDDEN_MESSAGE = "The server refused that for this account."
BAD_BODY_MESSAGE = "The server sent something the app cannot read."


class ApiError(Exception):
    """Base for every failure of an API call."""


class Http(ApiError):
    """HTTP status >= 400. reason is the body's reason (may be None)."""

    def __init__(self, code: int, reason: str | None, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.reason = reason
        self.message = message


class Unreachable(ApiError):
    """DNS / connect / TLS failure: usually Tailscale is off."""


class Timeout(Unreachable):
    """Connected, but the server did not answer in time."""


class NotSignedIn(ApiError):
    """No session: there is no API base to call."""

    def __init__(self) -> None:
        super().__init__("Not signed in.")


ApiError.Http = Http  # type: ignore[attr-defined]
ApiError.Unreachable = Unreachable  # type: ignore[attr-defined]
ApiError.Timeout = Timeout  # type: ignore[attr-defined]
ApiError.NotSignedIn = NotSignedIn  # type: ignore[attr-defined]


def seg(item_id: str) -> str:
    """Ids are opaque server hashes; a '/' would split the path."""
    return item_id.replace("/", "")


def decode_list(text: str, make_item, key: str = "results") -> list:
    """A bare array, or an object holding the array at key."""
    try:
        element = json.loads(text)
    except ValueError:
        raise Http(200, "bad_body", BAD_BODY_MESSAGE)
    if isinstance(element, list):
        array = element
    elif isinstance(element, dict):
        array = element.get(key, [])
    else:
        array = []
    try:
        return [make_item(row) for row in array]
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise Http(200, "bad_body", BAD_BODY_MESSAGE) from exc


def decode(text: str, make):
    try:
        return make(json.loads(text))
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise Http(200, "bad_body", BAD_BODY_MESSAGE) from exc


def resolve_url(api_base: str, url: str) -> str:
    """Absolute urls pass through; /audiobook/api/.. keeps the origin;
    /api/.. and bare-relative forms land under the API base."""
    if "://" in url:
        return url
    base = api_base if api_base.endswith("/") else api_base + "/"
    parsed = urllib.parse.urlsplit(base)
    base_path = parsed.path or "/"
    if url.startswith("/") and base_path != "/" and url.startswith(base_path):
        relative = url[len(base_path):]
    else:
        relative = url.lstrip("/")
    return urllib.parse.urljoin(base, relative)


def _omit_none(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if v is not None}


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_opt_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_opt_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_list(value) -> list:
    return list(value) if isinstance(value, list) else []


def _as_dict(value) -> dict:
    return dict(value) if isinstance(value, dict) else {}


@dataclass
class Me:
    user_id: str = ""
    username: str = ""
    display_name: str = ""
    is_admin: bool = False

    @classmethod
    def from_dict(cls, doc: dict) -> Me:
        return cls(
            user_id=str(doc.get("user_id", "") or ""),
            username=str(doc.get("username", "") or ""),
            display_name=str(doc.get("display_name", "") or ""),
            is_admin=bool(doc.get("is_admin", False)))


@dataclass
class SeriesRef:
    name: str = ""
    sequence: str = ""
    id: int | None = None

    @classmethod
    def from_dict(cls, doc: dict) -> SeriesRef:
        return cls(name=str(doc.get("name", "") or ""),
                   sequence=str(doc.get("sequence", "") or ""),
                   id=_as_opt_int(doc.get("id")))


@dataclass
class NamedRef:
    id: int = 0
    name: str = ""

    @classmethod
    def from_dict(cls, doc: dict) -> NamedRef:
        return cls(id=_as_int(doc.get("id")),
                   name=str(doc.get("name", "") or ""))


@dataclass
class MyReview:
    id: str = ""
    stars: int = 0
    body: str = ""
    created_at: float | None = None
    updated_at: float | None = None

    @classmethod
    def from_dict(cls, doc: dict) -> MyReview:
        return cls(id=str(doc.get("id", "") or ""),
                   stars=_as_int(doc.get("stars")),
                   body=str(doc.get("body", "") or ""),
                   created_at=_as_opt_float(doc.get("created_at")),
                   updated_at=_as_opt_float(doc.get("updated_at")))


@dataclass
class Rating:
    average: float | None = None
    count: int = 0
    mine: MyReview | None = None

    @classmethod
    def from_dict(cls, doc: dict) -> Rating:
        mine = doc.get("mine")
        return cls(average=_as_opt_float(doc.get("average")),
                   count=_as_int(doc.get("count")),
                   mine=MyReview.from_dict(mine)
                   if isinstance(mine, dict) else None)


@dataclass
class Progress:
    item_id: str = ""
    position_s: float = 0.0
    duration_s: float = 0.0
    speed: float = 1.0
    is_finished: bool = False
    started_at: float | None = None
    finished_at: float | None = None
    last_played_at: float | None = None
    updated_at: float | None = None
    applied: bool | None = None

    @classmethod
    def from_dict(cls, doc: dict) -> Progress:
        return cls(
            item_id=str(doc.get("item_id", "") or ""),
            position_s=_as_float(doc.get("position_s")),
            duration_s=_as_float(doc.get("duration_s")),
            speed=_as_float(doc.get("speed"), 1.0),
            is_finished=bool(doc.get("is_finished", False)),
            started_at=_as_opt_float(doc.get("started_at")),
            finished_at=_as_opt_float(doc.get("finished_at")),
            last_played_at=_as_opt_float(doc.get("last_played_at")),
            updated_at=_as_opt_float(doc.get("updated_at")),
            applied=(bool(doc["applied"]) if "applied" in doc
                     and doc["applied"] is not None else None))


@dataclass
class ItemSummary:
    id: str = ""
    title: str = ""
    subtitle: str = ""
    authors: list = field(default_factory=list)
    narrators: list = field(default_factory=list)
    series: list = field(default_factory=list)
    duration_s: float = 0.0
    status: str = "ok"
    added_at: float | None = None
    cover_url: str | None = None
    thumb_url: str | None = None
    progress: Progress | None = None
    rating: Rating = field(default_factory=Rating)
    author_refs: list = field(default_factory=list)
    narrator_refs: list = field(default_factory=list)

    @property
    def missing(self) -> bool:
        return self.status == "missing"

    @classmethod
    def from_dict(cls, doc: dict) -> ItemSummary:
        progress = doc.get("progress")
        rating = doc.get("rating")
        return cls(
            id=str(doc.get("id", "") or ""),
            title=str(doc.get("title", "") or ""),
            subtitle=str(doc.get("subtitle", "") or ""),
            authors=[str(a) for a in _as_list(doc.get("authors"))],
            narrators=[str(a) for a in _as_list(doc.get("narrators"))],
            series=[SeriesRef.from_dict(s) for s in _as_list(doc.get("series"))
                    if isinstance(s, dict)],
            duration_s=_as_float(doc.get("duration_s")),
            status=str(doc.get("status", "ok") or "ok"),
            added_at=_as_opt_float(doc.get("added_at")),
            cover_url=doc.get("cover_url"),
            thumb_url=doc.get("thumb_url"),
            progress=(Progress.from_dict(progress)
                      if isinstance(progress, dict) else None),
            rating=(Rating.from_dict(rating)
                    if isinstance(rating, dict) else Rating()),
            author_refs=[NamedRef.from_dict(r)
                         for r in _as_list(doc.get("author_refs"))
                         if isinstance(r, dict)],
            narrator_refs=[NamedRef.from_dict(r)
                           for r in _as_list(doc.get("narrator_refs"))
                           if isinstance(r, dict)])


@dataclass
class ItemsPage:
    total: int = 0
    page: int = 1
    limit: int = 50
    results: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, doc: dict) -> ItemsPage:
        return cls(
            total=_as_int(doc.get("total")), page=_as_int(doc.get("page"), 1),
            limit=_as_int(doc.get("limit"), 50),
            results=[ItemSummary.from_dict(r)
                     for r in _as_list(doc.get("results"))
                     if isinstance(r, dict)])


@dataclass
class Chapter:
    index: int = 0
    title: str = ""
    start_s: float = 0.0
    end_s: float = 0.0

    @classmethod
    def from_dict(cls, doc: dict) -> Chapter:
        return cls(index=_as_int(doc.get("index")),
                   title=str(doc.get("title", "") or ""),
                   start_s=_as_float(doc.get("start_s")),
                   end_s=_as_float(doc.get("end_s")))


@dataclass
class MediaFile:
    file_id: str = ""
    index: int = 0
    url: str = ""
    start_offset_s: float = 0.0
    duration_s: float = 0.0
    mime: str = ""
    size: int = 0
    sha256: str = ""

    @classmethod
    def from_dict(cls, doc: dict) -> MediaFile:
        sha = doc.get("sha256")
        return cls(file_id=str(doc.get("file_id", "") or ""),
                   index=_as_int(doc.get("index")),
                   url=str(doc.get("url", "") or ""),
                   start_offset_s=_as_float(doc.get("start_offset_s")),
                   duration_s=_as_float(doc.get("duration_s")),
                   mime=str(doc.get("mime", "") or ""),
                   size=_as_int(doc.get("size")),
                   sha256="" if sha is None else str(sha))


@dataclass
class ItemDetail:
    id: str = ""
    title: str = ""
    subtitle: str = ""
    authors: list = field(default_factory=list)
    narrators: list = field(default_factory=list)
    series: list = field(default_factory=list)
    duration_s: float = 0.0
    status: str = "ok"
    added_at: float | None = None
    cover_url: str | None = None
    thumb_url: str | None = None
    progress: Progress | None = None
    rating: Rating = field(default_factory=Rating)
    description: str = ""
    publisher: str = ""
    published_year: str = ""
    language: str = ""
    genres: list = field(default_factory=list)
    asin: str = ""
    isbn: str = ""
    explicit: bool = False
    abridged: bool = False
    size_bytes: int = 0
    file_count: int = 0
    updated_at: float | None = None
    chapters: list = field(default_factory=list)
    files: list = field(default_factory=list)
    author_refs: list = field(default_factory=list)
    narrator_refs: list = field(default_factory=list)
    playable: bool = True
    issues: list = field(default_factory=list)

    @property
    def missing(self) -> bool:
        return self.status == "missing"

    @classmethod
    def from_dict(cls, doc: dict) -> ItemDetail:
        progress = doc.get("progress")
        rating = doc.get("rating")
        return cls(
            id=str(doc.get("id", "") or ""),
            title=str(doc.get("title", "") or ""),
            subtitle=str(doc.get("subtitle", "") or ""),
            authors=[str(a) for a in _as_list(doc.get("authors"))],
            narrators=[str(a) for a in _as_list(doc.get("narrators"))],
            series=[SeriesRef.from_dict(s) for s in _as_list(doc.get("series"))
                    if isinstance(s, dict)],
            duration_s=_as_float(doc.get("duration_s")),
            status=str(doc.get("status", "ok") or "ok"),
            added_at=_as_opt_float(doc.get("added_at")),
            cover_url=doc.get("cover_url"),
            thumb_url=doc.get("thumb_url"),
            progress=(Progress.from_dict(progress)
                      if isinstance(progress, dict) else None),
            rating=(Rating.from_dict(rating)
                    if isinstance(rating, dict) else Rating()),
            description=str(doc.get("description", "") or ""),
            publisher=str(doc.get("publisher", "") or ""),
            published_year=str(doc.get("published_year", "") or ""),
            language=str(doc.get("language", "") or ""),
            genres=[str(g) for g in _as_list(doc.get("genres"))],
            asin=str(doc.get("asin", "") or ""),
            isbn=str(doc.get("isbn", "") or ""),
            explicit=bool(doc.get("explicit", False)),
            abridged=bool(doc.get("abridged", False)),
            size_bytes=_as_int(doc.get("size_bytes")),
            file_count=_as_int(doc.get("file_count")),
            updated_at=_as_opt_float(doc.get("updated_at")),
            chapters=[Chapter.from_dict(c)
                      for c in _as_list(doc.get("chapters"))
                      if isinstance(c, dict)],
            files=[MediaFile.from_dict(f) for f in _as_list(doc.get("files"))
                   if isinstance(f, dict)],
            author_refs=[NamedRef.from_dict(r)
                         for r in _as_list(doc.get("author_refs"))
                         if isinstance(r, dict)],
            narrator_refs=[NamedRef.from_dict(r)
                           for r in _as_list(doc.get("narrator_refs"))
                           if isinstance(r, dict)],
            playable=bool(doc.get("playable", True)),
            issues=[str(i) for i in _as_list(doc.get("issues"))])


@dataclass
class SeriesProgress:
    finished: int = 0
    in_progress: int = 0

    @classmethod
    def from_dict(cls, doc: dict) -> SeriesProgress:
        return cls(finished=_as_int(doc.get("finished")),
                   in_progress=_as_int(doc.get("in_progress")))


@dataclass
class SeriesCard:
    id: int = 0
    name: str = ""
    book_count: int = 0
    duration_s: float = 0.0
    thumb_urls: list = field(default_factory=list)
    added_at: float = 0.0
    progress: SeriesProgress = field(default_factory=SeriesProgress)

    @classmethod
    def from_dict(cls, doc: dict) -> SeriesCard:
        progress = doc.get("progress")
        return cls(
            id=_as_int(doc.get("id")), name=str(doc.get("name", "") or ""),
            book_count=_as_int(doc.get("book_count")),
            duration_s=_as_float(doc.get("duration_s")),
            thumb_urls=[str(u) for u in _as_list(doc.get("thumb_urls"))],
            added_at=_as_float(doc.get("added_at")),
            progress=(SeriesProgress.from_dict(progress)
                      if isinstance(progress, dict) else SeriesProgress()))


@dataclass
class SeriesBook:
    id: str = ""
    title: str = ""
    subtitle: str = ""
    authors: list = field(default_factory=list)
    narrators: list = field(default_factory=list)
    series: list = field(default_factory=list)
    duration_s: float = 0.0
    status: str = "ok"
    added_at: float | None = None
    cover_url: str | None = None
    thumb_url: str | None = None
    progress: Progress | None = None
    rating: Rating = field(default_factory=Rating)
    author_refs: list = field(default_factory=list)
    narrator_refs: list = field(default_factory=list)
    sequence: str = ""

    def summary(self) -> ItemSummary:
        return ItemSummary(
            id=self.id, title=self.title, subtitle=self.subtitle,
            authors=self.authors, narrators=self.narrators, series=self.series,
            duration_s=self.duration_s, status=self.status,
            added_at=self.added_at, cover_url=self.cover_url,
            thumb_url=self.thumb_url, progress=self.progress,
            rating=self.rating, author_refs=self.author_refs,
            narrator_refs=self.narrator_refs)

    @classmethod
    def from_dict(cls, doc: dict) -> SeriesBook:
        progress = doc.get("progress")
        rating = doc.get("rating")
        return cls(
            id=str(doc.get("id", "") or ""),
            title=str(doc.get("title", "") or ""),
            subtitle=str(doc.get("subtitle", "") or ""),
            authors=[str(a) for a in _as_list(doc.get("authors"))],
            narrators=[str(a) for a in _as_list(doc.get("narrators"))],
            series=[SeriesRef.from_dict(s) for s in _as_list(doc.get("series"))
                    if isinstance(s, dict)],
            duration_s=_as_float(doc.get("duration_s")),
            status=str(doc.get("status", "ok") or "ok"),
            added_at=_as_opt_float(doc.get("added_at")),
            cover_url=doc.get("cover_url"),
            thumb_url=doc.get("thumb_url"),
            progress=(Progress.from_dict(progress)
                      if isinstance(progress, dict) else None),
            rating=(Rating.from_dict(rating)
                    if isinstance(rating, dict) else Rating()),
            author_refs=[NamedRef.from_dict(r)
                         for r in _as_list(doc.get("author_refs"))
                         if isinstance(r, dict)],
            narrator_refs=[NamedRef.from_dict(r)
                           for r in _as_list(doc.get("narrator_refs"))
                           if isinstance(r, dict)],
            sequence=str(doc.get("sequence", "") or ""))


@dataclass
class SeriesDetail:
    id: int = 0
    name: str = ""
    book_count: int = 0
    duration_s: float = 0.0
    progress: SeriesProgress = field(default_factory=SeriesProgress)
    books: list = field(default_factory=list)

    def sequence_of(self, book: SeriesBook) -> str | None:
        if book.sequence.strip():
            return book.sequence
        for ref in book.series:
            if ref.id == self.id and ref.sequence.strip():
                return ref.sequence
        return None

    @classmethod
    def from_dict(cls, doc: dict) -> SeriesDetail:
        progress = doc.get("progress")
        return cls(
            id=_as_int(doc.get("id")), name=str(doc.get("name", "") or ""),
            book_count=_as_int(doc.get("book_count")),
            duration_s=_as_float(doc.get("duration_s")),
            progress=(SeriesProgress.from_dict(progress)
                      if isinstance(progress, dict) else SeriesProgress()),
            books=[SeriesBook.from_dict(b) for b in _as_list(doc.get("books"))
                   if isinstance(b, dict)])


@dataclass
class AuthorDetail:
    id: int = 0
    name: str = ""
    asin: str = ""
    description: str = ""
    image_url: str | None = None
    books: list = field(default_factory=list)
    series: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, doc: dict) -> AuthorDetail:
        return cls(
            id=_as_int(doc.get("id")), name=str(doc.get("name", "") or ""),
            asin=str(doc.get("asin", "") or ""),
            description=str(doc.get("description", "") or ""),
            image_url=doc.get("image_url"),
            books=[ItemSummary.from_dict(b)
                   for b in _as_list(doc.get("books"))
                   if isinstance(b, dict)],
            series=[SeriesCard.from_dict(s)
                    for s in _as_list(doc.get("series"))
                    if isinstance(s, dict)])


@dataclass
class NarratorDetail:
    id: int = 0
    name: str = ""
    book_count: int = 0
    books: list = field(default_factory=list)
    series: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, doc: dict) -> NarratorDetail:
        return cls(
            id=_as_int(doc.get("id")), name=str(doc.get("name", "") or ""),
            book_count=_as_int(doc.get("book_count")),
            books=[ItemSummary.from_dict(b)
                   for b in _as_list(doc.get("books"))
                   if isinstance(b, dict)],
            series=[SeriesCard.from_dict(s)
                    for s in _as_list(doc.get("series"))
                    if isinstance(s, dict)])


@dataclass
class HomeShelf:
    id: str = ""
    label: str = ""
    kind: str = "items"
    results: list = field(default_factory=list)

    def items(self) -> list:
        if self.kind != "items":
            return []
        out = []
        for row in self.results:
            if isinstance(row, dict):
                try:
                    out.append(ItemSummary.from_dict(row))
                except (ValueError, TypeError, KeyError):
                    continue
        return out

    @classmethod
    def from_dict(cls, doc: dict) -> HomeShelf:
        return cls(id=str(doc.get("id", "") or ""),
                   label=str(doc.get("label", "") or ""),
                   kind=str(doc.get("kind", "items") or "items"),
                   results=_as_list(doc.get("results")))


@dataclass
class Home:
    shelves: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, doc: dict) -> Home:
        return cls(shelves=[HomeShelf.from_dict(s)
                            for s in _as_list(doc.get("shelves"))
                            if isinstance(s, dict)])


@dataclass
class ListCard:
    id: str = ""
    name: str = ""
    description: str = ""
    book_count: int = 0
    thumb_urls: list = field(default_factory=list)
    updated_at: float = 0.0

    @classmethod
    def from_dict(cls, doc: dict) -> ListCard:
        return cls(
            id=str(doc.get("id", "") or ""),
            name=str(doc.get("name", "") or ""),
            description=str(doc.get("description", "") or ""),
            book_count=_as_int(doc.get("book_count")),
            thumb_urls=[str(u) for u in _as_list(doc.get("thumb_urls"))],
            updated_at=_as_float(doc.get("updated_at")))


@dataclass
class ListDetail:
    id: str = ""
    name: str = ""
    description: str = ""
    book_count: int = 0
    thumb_urls: list = field(default_factory=list)
    updated_at: float = 0.0
    books: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, doc: dict) -> ListDetail:
        return cls(
            id=str(doc.get("id", "") or ""),
            name=str(doc.get("name", "") or ""),
            description=str(doc.get("description", "") or ""),
            book_count=_as_int(doc.get("book_count")),
            thumb_urls=[str(u) for u in _as_list(doc.get("thumb_urls"))],
            updated_at=_as_float(doc.get("updated_at")),
            books=[ItemSummary.from_dict(b)
                   for b in _as_list(doc.get("books"))
                   if isinstance(b, dict)])


@dataclass
class ListeningSession:
    session_id: str = ""
    item_id: str = ""
    title: str = ""
    device: str = ""
    start_position_s: float = 0.0
    position_s: float = 0.0
    listened_s: float = 0.0
    speed: float = 1.0
    started_at: float = 0.0
    updated_at: float = 0.0
    closed_at: float | None = None

    @classmethod
    def from_dict(cls, doc: dict) -> ListeningSession:
        return cls(
            session_id=str(doc.get("session_id", "") or ""),
            item_id=str(doc.get("item_id", "") or ""),
            title=str(doc.get("title", "") or ""),
            device=str(doc.get("device", "") or ""),
            start_position_s=_as_float(doc.get("start_position_s")),
            position_s=_as_float(doc.get("position_s")),
            listened_s=_as_float(doc.get("listened_s")),
            speed=_as_float(doc.get("speed"), 1.0),
            started_at=_as_float(doc.get("started_at")),
            updated_at=_as_float(doc.get("updated_at")),
            closed_at=_as_opt_float(doc.get("closed_at")))


@dataclass
class ListeningSessionPage:
    total: int = 0
    page: int = 1
    limit: int = 50
    results: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, doc: dict) -> ListeningSessionPage:
        return cls(
            total=_as_int(doc.get("total")), page=_as_int(doc.get("page"), 1),
            limit=_as_int(doc.get("limit"), 50),
            results=[ListeningSession.from_dict(r)
                     for r in _as_list(doc.get("results"))
                     if isinstance(r, dict)])


@dataclass
class UserSettings:
    skip_back_s: int = 15
    skip_forward_s: int = 30
    default_speed: float = 1.0
    auto_rewind_s: int = 2
    sleep_default_min: int | None = None
    sleep_end_of_chapter: bool = False
    theme_preset: str = "default"
    library_view: str = "grid"
    updated_at: float | None = None
    applied: bool | None = None

    @classmethod
    def from_dict(cls, doc: dict) -> UserSettings:
        return cls(
            skip_back_s=_as_int(doc.get("skip_back_s"), 15),
            skip_forward_s=_as_int(doc.get("skip_forward_s"), 30),
            default_speed=_as_float(doc.get("default_speed"), 1.0),
            auto_rewind_s=_as_int(doc.get("auto_rewind_s"), 2),
            sleep_default_min=_as_opt_int(doc.get("sleep_default_min")),
            sleep_end_of_chapter=bool(doc.get("sleep_end_of_chapter", False)),
            theme_preset=str(doc.get("theme_preset", "default")
                             or "default"),
            library_view=str(doc.get("library_view", "grid") or "grid"),
            updated_at=_as_opt_float(doc.get("updated_at")),
            applied=(bool(doc["applied"]) if "applied" in doc
                     and doc["applied"] is not None else None))


@dataclass
class MonthStat:
    month: int = 0
    listened_s: float = 0.0
    sessions: int = 0

    @classmethod
    def from_dict(cls, doc: dict) -> MonthStat:
        return cls(month=_as_int(doc.get("month")),
                   listened_s=_as_float(doc.get("listened_s")),
                   sessions=_as_int(doc.get("sessions")))


@dataclass
class TopItem:
    item_id: str = ""
    title: str = ""
    listened_s: float = 0.0
    thumb_url: str | None = None

    @classmethod
    def from_dict(cls, doc: dict) -> TopItem:
        return cls(item_id=str(doc.get("item_id", "") or ""),
                   title=str(doc.get("title", "") or ""),
                   listened_s=_as_float(doc.get("listened_s")),
                   thumb_url=doc.get("thumb_url"))


@dataclass
class Streak:
    current_days: int = 0
    longest_days: int = 0

    @classmethod
    def from_dict(cls, doc: dict) -> Streak:
        return cls(current_days=_as_int(doc.get("current_days")),
                   longest_days=_as_int(doc.get("longest_days")))


@dataclass
class DayStat:
    date: str = ""
    listened_s: float = 0.0

    @classmethod
    def from_dict(cls, doc: dict) -> DayStat:
        return cls(date=str(doc.get("date", "") or ""),
                   listened_s=_as_float(doc.get("listened_s")))


@dataclass
class TopAuthor:
    id: int = 0
    name: str = ""
    listened_s: float = 0.0

    @classmethod
    def from_dict(cls, doc: dict) -> TopAuthor:
        return cls(id=_as_int(doc.get("id")),
                   name=str(doc.get("name", "") or ""),
                   listened_s=_as_float(doc.get("listened_s")))


@dataclass
class Stats:
    year: int = 0
    total_listened_s: float = 0.0
    sessions: int = 0
    items_played: int = 0
    items_finished: int = 0
    days_listened: int = 0
    by_month: list = field(default_factory=list)
    top_items: list = field(default_factory=list)
    streak: Streak = field(default_factory=Streak)
    first_year: int | None = None
    by_day: list = field(default_factory=list)
    top_authors: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, doc: dict) -> Stats:
        streak = doc.get("streak")
        return cls(
            year=_as_int(doc.get("year")),
            total_listened_s=_as_float(doc.get("total_listened_s")),
            sessions=_as_int(doc.get("sessions")),
            items_played=_as_int(doc.get("items_played")),
            items_finished=_as_int(doc.get("items_finished")),
            days_listened=_as_int(doc.get("days_listened")),
            by_month=[MonthStat.from_dict(m)
                      for m in _as_list(doc.get("by_month"))
                      if isinstance(m, dict)],
            top_items=[TopItem.from_dict(t)
                       for t in _as_list(doc.get("top_items"))
                       if isinstance(t, dict)],
            streak=(Streak.from_dict(streak)
                    if isinstance(streak, dict) else Streak()),
            first_year=_as_opt_int(doc.get("first_year")),
            by_day=[DayStat.from_dict(d) for d in _as_list(doc.get("by_day"))
                    if isinstance(d, dict)],
            top_authors=[TopAuthor.from_dict(a)
                         for a in _as_list(doc.get("top_authors"))
                         if isinstance(a, dict)])


@dataclass
class ManifestFile:
    file_id: str = ""
    index: int = 0
    filename: str = ""
    size: int = 0
    sha256: str = ""
    mime: str = ""
    duration_s: float = 0.0
    start_offset_s: float = 0.0
    url: str = ""

    @classmethod
    def from_dict(cls, doc: dict) -> ManifestFile:
        sha = doc.get("sha256")
        return cls(file_id=str(doc.get("file_id", "") or ""),
                   index=_as_int(doc.get("index")),
                   filename=str(doc.get("filename", "") or ""),
                   size=_as_int(doc.get("size")),
                   sha256="" if sha is None else str(sha),
                   mime=str(doc.get("mime", "") or ""),
                   duration_s=_as_float(doc.get("duration_s")),
                   start_offset_s=_as_float(doc.get("start_offset_s")),
                   url=str(doc.get("url", "") or ""))


@dataclass
class Manifest:
    item_id: str = ""
    total_size: int = 0
    files: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, doc: dict) -> Manifest:
        return cls(
            item_id=str(doc.get("item_id", "") or ""),
            total_size=_as_int(doc.get("total_size")),
            files=[ManifestFile.from_dict(f)
                   for f in _as_list(doc.get("files"))
                   if isinstance(f, dict)])


@dataclass
class Track:
    file_id: str = ""
    url: str = ""
    start_offset_s: float = 0.0
    duration_s: float = 0.0
    mime: str = ""
    size: int = 0

    @classmethod
    def from_dict(cls, doc: dict) -> Track:
        return cls(file_id=str(doc.get("file_id", "") or ""),
                   url=str(doc.get("url", "") or ""),
                   start_offset_s=_as_float(doc.get("start_offset_s")),
                   duration_s=_as_float(doc.get("duration_s")),
                   mime=str(doc.get("mime", "") or ""),
                   size=_as_int(doc.get("size")))


@dataclass
class PlayResponse:
    session_id: str = ""
    item_id: str = ""
    resume_position_s: float = 0.0
    duration_s: float = 0.0
    speed: float = 1.0
    tracks: list = field(default_factory=list)
    chapters: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, doc: dict) -> PlayResponse:
        return cls(
            session_id=str(doc.get("session_id", "") or ""),
            item_id=str(doc.get("item_id", "") or ""),
            resume_position_s=_as_float(doc.get("resume_position_s")),
            duration_s=_as_float(doc.get("duration_s")),
            speed=_as_float(doc.get("speed"), 1.0),
            tracks=[Track.from_dict(t) for t in _as_list(doc.get("tracks"))
                    if isinstance(t, dict)],
            chapters=[Chapter.from_dict(c)
                      for c in _as_list(doc.get("chapters"))
                      if isinstance(c, dict)])


@dataclass
class PlaySession:
    session_id: str = ""
    item_id: str = ""
    position_s: float = 0.0
    listened_s: float = 0.0
    speed: float = 1.0
    closed_at: float | None = None
    progress_applied: bool = True
    progress: Progress | None = None

    @classmethod
    def from_dict(cls, doc: dict) -> PlaySession:
        progress = doc.get("progress")
        return cls(
            session_id=str(doc.get("session_id", "") or ""),
            item_id=str(doc.get("item_id", "") or ""),
            position_s=_as_float(doc.get("position_s")),
            listened_s=_as_float(doc.get("listened_s")),
            speed=_as_float(doc.get("speed"), 1.0),
            closed_at=_as_opt_float(doc.get("closed_at")),
            progress_applied=bool(doc.get("progress_applied", True)),
            progress=(Progress.from_dict(progress)
                      if isinstance(progress, dict) else None))


@dataclass
class Bookmark:
    id: str = ""
    item_id: str = ""
    position_s: float = 0.0
    note: str = ""
    created_at: float | None = None
    updated_at: float | None = None

    @classmethod
    def from_dict(cls, doc: dict) -> Bookmark:
        return cls(id=str(doc.get("id", "") or ""),
                   item_id=str(doc.get("item_id", "") or ""),
                   position_s=_as_float(doc.get("position_s")),
                   note=str(doc.get("note", "") or ""),
                   created_at=_as_opt_float(doc.get("created_at")),
                   updated_at=_as_opt_float(doc.get("updated_at")))


@dataclass
class ReviewUser:
    id: str = ""
    display_name: str = ""

    @classmethod
    def from_dict(cls, doc: dict) -> ReviewUser:
        return cls(id=str(doc.get("id", "") or ""),
                   display_name=str(doc.get("display_name", "") or ""))


@dataclass
class Review:
    id: str = ""
    item_id: str = ""
    user: ReviewUser = field(default_factory=ReviewUser)
    stars: int = 0
    body: str = ""
    created_at: float | None = None
    updated_at: float | None = None

    @classmethod
    def from_dict(cls, doc: dict) -> Review:
        user = doc.get("user")
        return cls(
            id=str(doc.get("id", "") or ""),
            item_id=str(doc.get("item_id", "") or ""),
            user=(ReviewUser.from_dict(user)
                  if isinstance(user, dict) else ReviewUser()),
            stars=_as_int(doc.get("stars")),
            body=str(doc.get("body", "") or ""),
            created_at=_as_opt_float(doc.get("created_at")),
            updated_at=_as_opt_float(doc.get("updated_at")))


@dataclass
class ReviewsPage:
    average: float | None = None
    count: int = 0
    page: int = 1
    limit: int = 20
    results: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, doc: dict) -> ReviewsPage:
        return cls(
            average=_as_opt_float(doc.get("average")),
            count=_as_int(doc.get("count")),
            page=_as_int(doc.get("page"), 1),
            limit=_as_int(doc.get("limit"), 20),
            results=[Review.from_dict(r) for r in _as_list(doc.get("results"))
                     if isinstance(r, dict)])


@dataclass
class Location:
    id: str = ""
    name: str = ""
    configured: bool = True

    @classmethod
    def from_dict(cls, doc: dict) -> Location:
        return cls(id=str(doc.get("id", "") or ""),
                   name=str(doc.get("name", "") or ""),
                   configured=bool(doc.get("configured", True)))


@dataclass
class CastTarget:
    entity_id: str = ""
    name: str = ""
    state: str = ""
    volume_level: float | None = None
    media_title: str | None = None
    media_position_s: float | None = None

    @classmethod
    def from_dict(cls, doc: dict) -> CastTarget:
        return cls(
            entity_id=str(doc.get("entity_id", "") or ""),
            name=str(doc.get("name", "") or ""),
            state=str(doc.get("state", "") or ""),
            volume_level=_as_opt_float(doc.get("volume_level")),
            media_title=doc.get("media_title"),
            media_position_s=_as_opt_float(doc.get("media_position_s")))


@dataclass
class CastPlayResult:
    location_id: str = ""
    entity_id: str = ""
    item_id: str = ""
    file_id: str = ""
    position_s: float = 0.0
    offset_s: float = 0.0
    expires_at: int = 0
    queued: int = 0
    seeked: bool = False

    @classmethod
    def from_dict(cls, doc: dict) -> CastPlayResult:
        return cls(
            location_id=str(doc.get("location_id", "") or ""),
            entity_id=str(doc.get("entity_id", "") or ""),
            item_id=str(doc.get("item_id", "") or ""),
            file_id=str(doc.get("file_id", "") or ""),
            position_s=_as_float(doc.get("position_s")),
            offset_s=_as_float(doc.get("offset_s")),
            expires_at=_as_int(doc.get("expires_at")),
            queued=_as_int(doc.get("queued")),
            seeked=bool(doc.get("seeked", False)))


@dataclass
class CastTransportResult:
    ok: bool = True
    action: str = ""
    entity_id: str = ""

    @classmethod
    def from_dict(cls, doc: dict) -> CastTransportResult:
        return cls(ok=bool(doc.get("ok", True)),
                   action=str(doc.get("action", "") or ""),
                   entity_id=str(doc.get("entity_id", "") or ""))


@dataclass
class StatusRoot:
    path: str = ""
    present: bool = False
    item_count: int | None = None

    @classmethod
    def from_dict(cls, doc: dict) -> StatusRoot:
        return cls(path=str(doc.get("path", "") or ""),
                   present=bool(doc.get("present", False)),
                   item_count=_as_opt_int(doc.get("item_count")))


@dataclass
class Subsystem:
    state: str = ""
    detail: str = ""
    hint: str = ""

    @classmethod
    def from_dict(cls, doc: dict) -> Subsystem:
        return cls(state=str(doc.get("state", "") or ""),
                   detail=str(doc.get("detail", "") or ""),
                   hint=str(doc.get("hint", "") or ""))


@dataclass
class Status:
    version: str = ""
    service: str = ""
    items: int | None = None
    items_missing: int | None = None
    pending_matches: int | None = None
    media_roots: list = field(default_factory=list)
    subsystems: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, doc: dict) -> Status:
        raw = _as_dict(doc.get("subsystems"))
        return cls(
            version=str(doc.get("version", "") or ""),
            service=str(doc.get("service", "") or ""),
            items=_as_opt_int(doc.get("items")),
            items_missing=_as_opt_int(doc.get("items_missing")),
            pending_matches=_as_opt_int(doc.get("pending_matches")),
            media_roots=[StatusRoot.from_dict(r)
                         for r in _as_list(doc.get("media_roots"))
                         if isinstance(r, dict)],
            subsystems={k: Subsystem.from_dict(v) for k, v in raw.items()
                        if isinstance(v, dict)})


@dataclass
class SearchResults:
    books: list = field(default_factory=list)
    series: list = field(default_factory=list)
    authors: list = field(default_factory=list)
    narrators: list = field(default_factory=list)
    genres: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, doc: dict) -> SearchResults:
        return cls(
            books=[ItemSummary.from_dict(b)
                   for b in _as_list(doc.get("books"))
                   if isinstance(b, dict)],
            series=_as_list(doc.get("series")),
            authors=_as_list(doc.get("authors")),
            narrators=_as_list(doc.get("narrators")),
            genres=[str(g) for g in _as_list(doc.get("genres"))])


@dataclass
class PlayRequest:
    device: str = ""
    speed: float | None = None

    def to_dict(self) -> dict:
        return _omit_none({"device": self.device, "speed": self.speed})


@dataclass
class SyncRequest:
    position_s: float = 0.0
    listened_s: float = 0.0
    speed: float | None = None
    updated_at: float | None = None

    def to_dict(self) -> dict:
        return _omit_none({"position_s": self.position_s,
                           "listened_s": self.listened_s,
                           "speed": self.speed,
                           "updated_at": self.updated_at})


@dataclass
class CloseRequest:
    position_s: float | None = None
    listened_s: float | None = None
    speed: float | None = None
    updated_at: float | None = None

    def to_dict(self) -> dict:
        return _omit_none({"position_s": self.position_s,
                           "listened_s": self.listened_s,
                           "speed": self.speed,
                           "updated_at": self.updated_at})


@dataclass
class ProgressPut:
    position_s: float = 0.0
    duration_s: float | None = None
    speed: float | None = None
    is_finished: bool | None = None
    updated_at: float | None = None

    def to_dict(self) -> dict:
        return _omit_none({"position_s": self.position_s,
                           "duration_s": self.duration_s,
                           "speed": self.speed,
                           "is_finished": self.is_finished,
                           "updated_at": self.updated_at})


@dataclass
class BookmarkCreate:
    item_id: str = ""
    position_s: float = 0.0
    note: str = ""

    def to_dict(self) -> dict:
        return {"item_id": self.item_id, "position_s": self.position_s,
                "note": self.note}


@dataclass
class BookmarkPatch:
    position_s: float | None = None
    note: str | None = None

    def to_dict(self) -> dict:
        return _omit_none({"position_s": self.position_s,
                           "note": self.note})


@dataclass
class ReviewPut:
    stars: int = 0
    body: str = ""

    def to_dict(self) -> dict:
        return {"stars": self.stars, "body": self.body}


@dataclass
class CastPlay:
    entity_id: str = ""
    item_id: str = ""
    location_id: str | None = None
    chapter_index: int | None = None
    position_s: float | None = None

    def to_dict(self) -> dict:
        return _omit_none({"entity_id": self.entity_id,
                           "item_id": self.item_id,
                           "location_id": self.location_id,
                           "chapter_index": self.chapter_index,
                           "position_s": self.position_s})


@dataclass
class CastTransport:
    entity_id: str = ""
    action: str = ""
    location_id: str | None = None
    value: float | None = None

    def to_dict(self) -> dict:
        return _omit_none({"entity_id": self.entity_id,
                           "action": self.action,
                           "location_id": self.location_id,
                           "value": self.value})


@dataclass
class UserSettingsPut:
    skip_back_s: int | None = None
    skip_forward_s: int | None = None
    default_speed: float | None = None
    auto_rewind_s: int | None = None
    updated_at: float | None = None

    def to_dict(self) -> dict:
        return _omit_none({"skip_back_s": self.skip_back_s,
                           "skip_forward_s": self.skip_forward_s,
                           "default_speed": self.default_speed,
                           "auto_rewind_s": self.auto_rewind_s,
                           "updated_at": self.updated_at})


@dataclass
class ListCreate:
    name: str = ""
    description: str | None = None

    def to_dict(self) -> dict:
        return _omit_none({"name": self.name,
                           "description": self.description})


@dataclass
class ListPatch:
    name: str | None = None
    description: str | None = None

    def to_dict(self) -> dict:
        return _omit_none({"name": self.name,
                           "description": self.description})


@dataclass
class ItemsQuery:
    page: int = 1
    limit: int = 50
    q: str | None = None
    sort: str | None = None
    descending: bool = False
    filter: str | None = None

    def to_params(self) -> dict:
        params = {"page": str(self.page), "limit": str(self.limit)}
        if self.q and self.q.strip():
            params["q"] = self.q
        if self.sort:
            params["sort"] = self.sort
            params["order"] = "desc" if self.descending else "asc"
        if self.filter:
            params["filter"] = self.filter
        return params


def message_of(code: int, text: str) -> str:
    try:
        obj = json.loads(text)
    except ValueError:
        obj = None
    if isinstance(obj, dict):
        detail = obj.get("detail")
        if isinstance(detail, str) and detail:
            return detail
        error = obj.get("error")
        if isinstance(error, dict):
            nested = error.get("message")
            if isinstance(nested, str) and nested:
                return nested
    return f"The server answered {code}."


def message_for(code: int, reason: str | None, text: str) -> str:
    if code == 409 and reason == "not_playable":
        return NOT_PLAYABLE_MESSAGE
    if code == 403 and reason == "not_granted":
        return NOT_GRANTED_MESSAGE
    return message_of(code, text)


def reason_of(text: str) -> str | None:
    from xx_audiobook import auth as auth_module
    return auth_module.parse_reason(text)


def error_for(exc: Exception) -> ApiError:
    from xx_audiobook import auth as auth_module
    if isinstance(exc, ApiError):
        return exc
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return Timeout(TIMEOUT_MESSAGE)
    return Unreachable(auth_module.UNREACHABLE_MESSAGE)


Transport = Callable[[str, str, dict, bytes | None, float],
                     tuple[int, str]]


def _default_transport(method: str, url: str, headers: dict,
                       body: bytes | None, timeout: float,
                       ) -> tuple[int, str]:
    request = urllib.request.Request(url, data=body, headers=headers,
                                     method=method)
    try:
        with urllib.request.urlopen(request,
                                    timeout=timeout) as response:
            if response.url != url:
                raise Http(302, None, "Redirects are not followed.")
            try:
                raw = response.read()
            except (http.client.HTTPException, OSError) as exc:
                raise Http(200, "bad_body", BAD_BODY_MESSAGE) from exc
            return response.status, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            payload = exc.read().decode("utf-8", "replace")
        except OSError:
            payload = ""
        return exc.code, payload
    except (urllib.error.URLError, OSError, TimeoutError,
            socket.timeout) as exc:
        raise error_for(exc)


class TokenSource:
    """Bearer [REDACTED] 401/403 handling, mirroring AuthInterceptor.TokenSource."""

    def current(self) -> tuple[str, str] | None:
        """The (token, api_base) to send, or None when signed out."""
        return None

    def renew(self, stale_token: str) -> tuple[str, str] | None:
        """Blocking renew after an expired 401; None when it cannot."""
        return None

    def on_rejected(self, action: str, reason: str | None) -> None:
        """A 401 we cannot recover from; action is renew or wipe."""

    def on_access(self, granted: bool) -> None:
        """False after a 403 not_granted; True again after any 2xx."""


class _NullTokens(TokenSource):
    pass


class AudiobookApi:
    """Urllib implementation of the server contract (HttpAudiobookApi)."""

    def __init__(self, base_url=None, token=None,
                 transport: Transport | None = None,
                 tokens: TokenSource | None = None) -> None:
        self._base_url = base_url
        self._token = token
        self._transport = (transport if transport is not None
                           else _default_transport)
        self._tokens = tokens if tokens is not None else _NullTokens()
        self._renew_lock = threading.Lock()

    def _base(self) -> str | None:
        base = (self._base_url() if callable(self._base_url)
                else self._base_url)
        if not base or not base.strip():
            return None
        return base if base.endswith("/") else base + "/"

    def _bearer_for(self, url: str) -> str | None:
        from xx_audiobook import auth as auth_module
        current = self._tokens.current()
        if current is not None:
            token, api_base = current
            if auth_module.same_origin(url, api_base):
                return token
            return None
        token = self._token() if callable(self._token) else self._token
        if not token:
            return None
        base = self._base()
        if base is not None and auth_module.same_origin(url, base):
            return token
        return None

    def resolve(self, url: str) -> str | None:
        base = self._base()
        if base is None:
            return None
        return resolve_url(base, url)

    def cover_url(self, item_id: str, size: str = "thumb") -> str | None:
        base = self._base()
        if base is None:
            return None
        return (base + "api/items/" + seg(item_id) + "/cover?size="
                + urllib.parse.quote(size))

    def file_url(self, item_id: str, file_id: str) -> str | None:
        base = self._base()
        if base is None:
            return None
        return (base + "api/items/" + seg(item_id) + "/files/"
                + seg(file_id))

    def cover_bytes(self, item_id: str, size: str = "thumb") -> bytes:
        """Cover image bytes (GET, origin-keyed bearer, no redirects)."""
        url = self.cover_url(item_id, size)
        if url is None:
            raise NotSignedIn()
        headers = {"Accept": "image/*"}
        bearer = self._bearer_for(url)
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request,
                                        timeout=READ_TIMEOUT_S) as response:
                if response.url != url:
                    raise Http(302, None, "Redirects are not followed.")
                return response.read(COVER_MAX_BYTES)
        except urllib.error.HTTPError as exc:
            raise Http(exc.code, None, f"cover: HTTP {exc.code}") from exc
        except ApiError:
            raise
        except (urllib.error.URLError, OSError, TimeoutError,
                socket.timeout) as exc:
            raise error_for(exc)

    def _send(self, method: str, path: str, body=None,
              query: dict | None = None,
              timeout: float = READ_TIMEOUT_S) -> str:
        base = self._base()
        if base is None:
            raise NotSignedIn()
        url = base + path
        if query:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
        payload = (json.dumps(body).encode("utf-8")
                   if body is not None else None)
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        bearer = self._bearer_for(url)
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        try:
            code, text = self._transport(method, url, headers, payload,
                                         timeout)
        except ApiError:
            raise
        except Exception as exc:
            raise error_for(exc)
        if 200 <= code < 300:
            self._tokens.on_access(True)
            return text
        reason = reason_of(text)
        if code == 403 and reason == "not_granted":
            self._tokens.on_access(False)
            raise Http(code, reason, message_for(code, reason, text))
        if code == 401:
            return self._handle_401(method, url, payload, query, text,
                                    reason, timeout)
        raise Http(code, reason, message_for(code, reason, text))

    def _handle_401(self, method: str, url: str, payload: bytes | None,
                    query: dict | None, text: str, reason: str | None,
                    timeout: float) -> str:
        from xx_audiobook import auth as auth_module
        action = auth_module.action_for_reason(reason)
        if action == auth_module.AuthAction.WIPE:
            self._tokens.on_rejected("wipe", reason)
            raise Http(401, reason, message_for(401, reason, text))
        if action != auth_module.AuthAction.RENEW:
            raise Http(401, reason, message_for(401, reason, text))
        current = self._tokens.current()
        stale = current[0] if current else (
            self._token() if callable(self._token) else self._token)
        with self._renew_lock:
            fresh = self._tokens.current()
            if (fresh is not None and current is not None
                    and fresh[0] != current[0]):
                renewed = fresh
            else:
                renewed = (self._tokens.renew(stale or "")
                           if stale else None)
            if renewed is None:
                self._tokens.on_rejected("renew", reason)
                raise Http(401, reason, message_for(401, reason, text))
            new_token, _ = renewed
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        headers["Authorization"] = "Bearer " + new_token
        try:
            code, retry_text = self._transport(method, url, headers, payload,
                                               timeout)
        except ApiError:
            raise
        except Exception as exc:
            raise error_for(exc)
        if 200 <= code < 300:
            self._tokens.on_access(True)
            return retry_text
        retry_reason = reason_of(retry_text)
        raise Http(code, retry_reason,
                   message_for(code, retry_reason, retry_text))

    def _get(self, path: str, make, query: dict | None = None):
        return decode(self._send("GET", path, None, query), make)

    def _get_list(self, path: str, make_item, query: dict | None = None,
                  key: str = "results"):
        return decode_list(self._send("GET", path, None, query), make_item,
                           key)

    def _post(self, path: str, body, make):
        return decode(self._send("POST", path, body), make)

    def _request(self, method: str, path: str, body, make):
        return decode(self._send(method, path, body), make)

    def me(self) -> Me:
        return self._get("api/me", Me.from_dict)

    def items(self, query: ItemsQuery | None = None) -> ItemsPage:
        params = (query or ItemsQuery()).to_params()
        return self._get("api/items", ItemsPage.from_dict, params)

    def item(self, item_id: str) -> ItemDetail:
        return self._get(f"api/items/{seg(item_id)}", ItemDetail.from_dict)

    def continue_listening(self) -> list:
        return self._get_list("api/me/continue", ItemSummary.from_dict)

    def manifest(self, item_id: str) -> Manifest:
        text = self._send("GET", f"api/items/{seg(item_id)}/manifest", None,
                          None, MANIFEST_READ_TIMEOUT_S)
        return decode(text, Manifest.from_dict)

    def play(self, item_id: str,
             request: PlayRequest | None = None) -> PlayResponse:
        body = (request or PlayRequest()).to_dict()
        return self._post(f"api/items/{seg(item_id)}/play", body,
                          PlayResponse.from_dict)

    def sync(self, session_id: str, body: SyncRequest) -> PlaySession:
        return self._post(f"api/sessions/{seg(session_id)}/sync",
                          body.to_dict(), PlaySession.from_dict)

    def close_session(self, session_id: str,
                      body: CloseRequest | None = None) -> None:
        self._send("POST", f"api/sessions/{seg(session_id)}/close",
                   (body or CloseRequest()).to_dict())

    def all_progress(self) -> list:
        return self._get_list("api/me/progress", Progress.from_dict)

    def progress(self, item_id: str) -> Progress | None:
        try:
            return self._get(f"api/me/progress/{seg(item_id)}",
                             Progress.from_dict)
        except Http as exc:
            if exc.code == 404:
                return None
            raise

    def put_progress(self, item_id: str, body: ProgressPut) -> Progress:
        return self._request("PUT", f"api/me/progress/{seg(item_id)}",
                             body.to_dict(), Progress.from_dict)

    def finish(self, item_id: str) -> Progress:
        return self._post(f"api/me/progress/{seg(item_id)}/finish", {},
                          Progress.from_dict)

    def unfinish(self, item_id: str) -> Progress:
        return self._post(f"api/me/progress/{seg(item_id)}/unfinish", {},
                          Progress.from_dict)

    def batch_finish(self, item_ids: list, finished: bool = True) -> dict:
        text = self._send("POST", "api/me/progress/batch",
                          {"item_ids": list(item_ids),
                           "finished": finished})
        return decode(text, dict)

    def bookmarks(self, item_id: str | None = None) -> list:
        query = {"item_id": item_id} if item_id else None
        return self._get_list("api/me/bookmarks", Bookmark.from_dict, query)

    def add_bookmark(self, body: BookmarkCreate) -> Bookmark:
        return self._post("api/me/bookmarks", body.to_dict(),
                          Bookmark.from_dict)

    def edit_bookmark(self, bookmark_id: str,
                      body: BookmarkPatch) -> Bookmark:
        return self._request("PATCH",
                             f"api/me/bookmarks/{seg(bookmark_id)}",
                             body.to_dict(), Bookmark.from_dict)

    def delete_bookmark(self, bookmark_id: str) -> None:
        self._send("DELETE", f"api/me/bookmarks/{seg(bookmark_id)}", None)

    def bookmark(self, bookmark_id: str) -> Bookmark:
        return self._get(f"api/me/bookmarks/{seg(bookmark_id)}",
                         Bookmark.from_dict)

    def reviews(self, item_id: str, page: int = 1) -> ReviewsPage:
        return self._get(f"api/items/{seg(item_id)}/reviews",
                         ReviewsPage.from_dict, {"page": str(page)})

    def put_review(self, item_id: str, body: ReviewPut) -> Review:
        return self._request("PUT", f"api/items/{seg(item_id)}/review",
                             body.to_dict(), Review.from_dict)

    def delete_my_review(self, item_id: str) -> None:
        self._send("DELETE", f"api/items/{seg(item_id)}/review", None)

    def admin_delete_review(self, review_id: str) -> None:
        self._send("DELETE", f"api/admin/reviews/{seg(review_id)}", None)

    def recent_reviews(self) -> list:
        return self._get_list("api/reviews/recent", Review.from_dict)

    def locations(self) -> list:
        return self._get_list("api/locations", Location.from_dict)

    def cast_targets(self, location_id: str) -> list:
        return decode_list(
            self._send("GET", "api/cast/targets", None,
                       {"location_id": location_id}),
            CastTarget.from_dict, "targets")

    def cast_play(self, body: CastPlay) -> CastPlayResult:
        return self._post("api/cast/play", body.to_dict(),
                          CastPlayResult.from_dict)

    def cast_transport(self, body: CastTransport) -> CastTransportResult:
        return self._post("api/cast/transport", body.to_dict(),
                          CastTransportResult.from_dict)

    def series(self, series_id: int) -> SeriesDetail:
        return self._get(f"api/series/{int(series_id)}",
                         SeriesDetail.from_dict)

    def series_list(self) -> list:
        return self._get_list("api/series", SeriesCard.from_dict)

    def author(self, author_id: int) -> AuthorDetail:
        return self._get(f"api/authors/{int(author_id)}",
                         AuthorDetail.from_dict)

    def authors_list(self) -> list:
        return self._get_list("api/authors", NamedRef.from_dict)

    def narrator(self, narrator_id: int) -> NarratorDetail:
        return self._get(f"api/narrators/{int(narrator_id)}",
                         NarratorDetail.from_dict)

    def narrators_list(self) -> list:
        return self._get_list("api/narrators", NamedRef.from_dict)

    def home(self) -> Home:
        return self._get("api/me/home", Home.from_dict)

    def sessions(self, page: int = 1, limit: int = 50
                 ) -> ListeningSessionPage:
        return self._get("api/me/sessions", ListeningSessionPage.from_dict,
                         {"page": str(page), "limit": str(limit)})

    def delete_session(self, session_id: str) -> None:
        self._send("DELETE", f"api/me/sessions/{seg(session_id)}", None)

    def settings(self) -> UserSettings:
        return self._get("api/me/settings", UserSettings.from_dict)

    def put_settings(self, body: UserSettingsPut) -> UserSettings:
        return self._request("PUT", "api/me/settings", body.to_dict(),
                             UserSettings.from_dict)

    def collections(self) -> list:
        return self._get_list("api/collections", ListCard.from_dict)

    def collection(self, collection_id: str) -> ListDetail:
        return self._get(f"api/collections/{seg(collection_id)}",
                         ListDetail.from_dict)

    def playlists(self) -> list:
        return self._get_list("api/me/playlists", ListCard.from_dict)

    def playlist(self, playlist_id: str) -> ListDetail:
        return self._get(f"api/me/playlists/{seg(playlist_id)}",
                         ListDetail.from_dict)

    def create_playlist(self, body: ListCreate) -> ListCard:
        return self._post("api/me/playlists", body.to_dict(),
                          ListCard.from_dict)

    def update_playlist(self, playlist_id: str,
                        body: ListPatch) -> ListCard:
        return self._request("PATCH",
                             f"api/me/playlists/{seg(playlist_id)}",
                             body.to_dict(), ListCard.from_dict)

    def delete_playlist(self, playlist_id: str) -> None:
        self._send("DELETE", f"api/me/playlists/{seg(playlist_id)}", None)

    def add_playlist_items(self, playlist_id: str,
                           item_ids: list) -> ListDetail:
        return self._post(f"api/me/playlists/{seg(playlist_id)}/items",
                          {"item_ids": list(item_ids)},
                          ListDetail.from_dict)

    def set_playlist_items(self, playlist_id: str,
                           item_ids: list) -> ListDetail:
        return self._request("PUT",
                             f"api/me/playlists/{seg(playlist_id)}/items",
                             {"item_ids": list(item_ids)},
                             ListDetail.from_dict)

    def remove_playlist_item(self, playlist_id: str, item_id: str) -> None:
        self._send("DELETE",
                   f"api/me/playlists/{seg(playlist_id)}"
                   f"/items/{seg(item_id)}", None)

    def search(self, q: str, limit: int = 8) -> SearchResults:
        return self._get("api/search", SearchResults.from_dict,
                         {"q": q, "limit": str(limit)})

    def facets(self) -> dict:
        return decode(self._send("GET", "api/library/facets", None), dict)

    def stats(self, year: int | None = None) -> Stats:
        query = {"year": str(year)} if year is not None else None
        return self._get("api/me/stats", Stats.from_dict, query)

    def status(self) -> Status:
        return self._get("api/status", Status.from_dict)


def submit(fn, on_done=None):
    """Run blocking fn() on a worker thread; on_done(result, error) runs
    via GLib.idle_add when GLib is available, else on the worker thread."""
    def run() -> None:
        try:
            result: object = fn()
            error = None
        except Exception as exc:  # noqa: BLE001 - delivered to on_done
            result, error = None, exc
        if on_done is None:
            return
        try:
            from gi.repository import GLib
            GLib.idle_add(lambda: (on_done(result, error), False)[1])
        except ImportError:
            on_done(result, error)

    thread = threading.Thread(target=run, name="xx-audiobook-io",
                              daemon=True)
    thread.start()
    return thread
