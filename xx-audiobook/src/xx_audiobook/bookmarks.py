"""Server-synced bookmarks with a Room cache and a queue (M5).

Mirrors data/repo/BookmarkRepository.kt: every change lands in Room
first (visible at once, survives offline) marked pending until the
server accepts it; sync_pending() replays the queue and refresh()
replaces the synced rows while keeping unsent edits. A 404 drops the
row (the server no longer has it, or will never take it); other errors
stay pending. Synchronous; screens use BookmarksState.submit_* from the
GTK thread.
"""

from __future__ import annotations

import threading
import time
import uuid

from xx_audiobook import api as api_module
from xx_audiobook import repos as repos_module
from xx_audiobook import store as store_module

LOCAL_PREFIX = "local-"
CREATE = "create"
EDIT = "edit"
DELETE = "delete"


def _iso_now(now_ms: int) -> str:
    return repos_module.from_ms(now_ms)


def _entity_to_store(row: api_module.Bookmark):
    return store_module.Bookmark(
        id=row.id, item_id=row.item_id,
        position_s=row.position_s, note=row.note,
        created_at=(repos_module.from_seconds(row.created_at)
                    if row.created_at is not None else None),
        updated_at=(repos_module.from_seconds(row.updated_at)
                    if row.updated_at is not None else None),
        pending=None)


class BookmarkRepository:
    """Room-first bookmarks; the pending queue survives offline."""

    def __init__(self, api, db: store_module.Database,
                 now_ms=None) -> None:
        self._api = api
        self._db = db
        self._now_ms = now_ms if now_ms is not None else (
            lambda: int(time.time() * 1000))
        self._lock = threading.Lock()

    def observe(self, item_id: str) -> list:
        return self._db.bookmarks().for_item(item_id)

    def pending_count(self) -> int:
        return len(self._db.bookmarks().pending())

    def refresh(self, item_id: str) -> tuple:
        with self._lock:
            self.sync_pending_locked()
        try:
            rows = self._api.bookmarks(item_id)
        except api_module.ApiError as exc:
            return (None, exc)
        self._db.bookmarks().replace_synced(
            item_id, [_entity_to_store(r) for r in rows])
        return (self.observe(item_id), None)

    def add(self, item_id: str, position_s: float, note: str):
        now = self._now_ms()
        local = store_module.Bookmark(
            id=LOCAL_PREFIX + str(uuid.uuid4()), item_id=item_id,
            position_s=max(0.0, position_s), note=(note or "").strip(),
            created_at=_iso_now(now), updated_at=_iso_now(now),
            pending=CREATE)
        self._db.bookmarks().upsert([local])
        with self._lock:
            pushed = self._push_create(local)
        return pushed if pushed is not None else local

    def edit(self, bookmark: store_module.Bookmark, note: str,
             position_s: float | None = None) -> None:
        pending = CREATE if bookmark.pending == CREATE else EDIT
        row = store_module.Bookmark(
            id=bookmark.id, item_id=bookmark.item_id,
            position_s=max(0.0, (bookmark.position_s if position_s is None
                                 else position_s)),
            note=(note or "").strip(), created_at=bookmark.created_at,
            updated_at=_iso_now(self._now_ms()), pending=pending)
        self._db.bookmarks().upsert([row])
        with self._lock:
            if pending == CREATE:
                self._push_create(row)
            else:
                self._push_edit(row)

    def delete(self, bookmark: store_module.Bookmark) -> None:
        if bookmark.pending == CREATE:
            self._db.bookmarks().delete(bookmark.id)
            return
        row = store_module.Bookmark(
            id=bookmark.id, item_id=bookmark.item_id,
            position_s=bookmark.position_s, note=bookmark.note,
            created_at=bookmark.created_at,
            updated_at=bookmark.updated_at, pending=DELETE)
        self._db.bookmarks().upsert([row])
        with self._lock:
            self._push_delete(row)

    def sync_pending(self) -> int:
        with self._lock:
            return self.sync_pending_locked()

    def sync_pending_locked(self) -> int:
        for row in self._db.bookmarks().pending():
            if row.pending == CREATE:
                self._push_create(row)
            elif row.pending == EDIT:
                self._push_edit(row)
            elif row.pending == DELETE:
                self._push_delete(row)
        return len(self._db.bookmarks().pending())

    def _push_create(self, row: store_module.Bookmark):
        try:
            server = self._api.add_bookmark(api_module.BookmarkCreate(
                item_id=row.item_id, position_s=row.position_s,
                note=row.note))
        except api_module.Http as exc:
            if exc.code == 404:
                self._db.bookmarks().delete(row.id)
            return None
        except api_module.ApiError:
            return None
        self._db.bookmarks().delete(row.id)
        stored = _entity_to_store(server)
        self._db.bookmarks().upsert([stored])
        return stored

    def _push_edit(self, row: store_module.Bookmark):
        try:
            server = self._api.edit_bookmark(
                row.id, api_module.BookmarkPatch(
                    position_s=row.position_s, note=row.note))
        except api_module.Http as exc:
            if exc.code == 404:
                self._db.bookmarks().delete(row.id)
            return None
        except api_module.ApiError:
            return None
        self._db.bookmarks().upsert([_entity_to_store(server)])
        return server

    def _push_delete(self, row: store_module.Bookmark):
        try:
            self._api.delete_bookmark(row.id)
        except api_module.Http as exc:
            if exc.code == 404:
                self._db.bookmarks().delete(row.id)
            return None
        except api_module.ApiError:
            return None
        self._db.bookmarks().delete(row.id)
        return row


class BookmarksState:
    """Small state API for the bookmark sheet (mirrors the M4 pattern).

    Screens read observe() (any thread) and call the plain methods on a
    worker thread or the submit_* variants from the GTK thread (results
    arrive via GLib.idle_add).
    """

    def __init__(self, api, db: store_module.Database,
                 now_ms=None) -> None:
        self._repo = BookmarkRepository(api, db, now_ms=now_ms)

    @property
    def repo(self) -> BookmarkRepository:
        return self._repo

    def observe(self, item_id: str) -> list:
        return self._repo.observe(item_id)

    def pending_count(self) -> int:
        return self._repo.pending_count()

    def refresh(self, item_id: str) -> tuple:
        return self._repo.refresh(item_id)

    def add(self, item_id: str, position_s: float, note: str):
        return self._repo.add(item_id, position_s, note)

    def edit(self, bookmark: store_module.Bookmark, note: str,
             position_s: float | None = None) -> None:
        self._repo.edit(bookmark, note, position_s)

    def delete(self, bookmark: store_module.Bookmark) -> None:
        self._repo.delete(bookmark)

    def sync_pending(self) -> int:
        return self._repo.sync_pending()

    def submit_refresh(self, item_id: str, on_done=None):
        return api_module.submit(
            lambda: self.refresh(item_id), on_done)

    def submit_add(self, item_id: str, position_s: float, note: str,
                   on_done=None):
        return api_module.submit(
            lambda: (self.add(item_id, position_s, note), None), on_done)

    def submit_sync(self, on_done=None):
        return api_module.submit(
            lambda: (self.sync_pending(), None), on_done)
