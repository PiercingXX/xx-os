"""The offline audio cache on top of the loopback proxy's files.

The proxy tees every played track into ``cache_dir`` as ``<track_id>``
(complete) or ``<track_id>.part`` (partial); this module owns the policy
around those files: one storage cap (2 GB default) with a free-space
floor (always leave 1 GB), LRU eviction that never evicts pinned tracks,
and a metadata index so the cache stays browsable without a link.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Container

DEFAULT_CAP_BYTES = 2 * 2**30
MIN_FREE_BYTES = 1 * 2**30
VALID_CAP_CHOICES_GB = (1, 2, 5, 10, 20)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cached_tracks (
    track_id TEXT PRIMARY KEY,
    engine TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    artist TEXT NOT NULL DEFAULT '',
    album TEXT NOT NULL DEFAULT '',
    duration_s REAL,
    size_bytes INTEGER,
    mime TEXT NOT NULL DEFAULT '',
    pinned INTEGER NOT NULL DEFAULT 0,
    last_played REAL NOT NULL DEFAULT 0,
    cached_at REAL NOT NULL DEFAULT 0
)
"""


@dataclass
class CacheEntry:
    track_id: str
    engine: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""
    duration_s: float | None = None
    size_bytes: int | None = None
    mime: str = ""
    pinned: bool = False
    last_played: float = 0.0
    cached_at: float = 0.0


class CacheIndex:
    """SQLite metadata for cached tracks; the files stay on disk."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._lock = threading.Lock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path),
                                     check_same_thread=False)
        with self._lock:
            self._conn.execute(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def upsert(self, entry: CacheEntry) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO cached_tracks "
                "(track_id, engine, title, artist, album, duration_s,"
                " size_bytes, mime, pinned, last_played, cached_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(track_id) DO UPDATE SET"
                " engine=excluded.engine, title=excluded.title,"
                " artist=excluded.artist, album=excluded.album,"
                " duration_s=excluded.duration_s,"
                " size_bytes=excluded.size_bytes, mime=excluded.mime,"
                " pinned=excluded.pinned, last_played=excluded.last_played,"
                " cached_at=excluded.cached_at",
                (entry.track_id, entry.engine, entry.title, entry.artist,
                 entry.album, entry.duration_s, entry.size_bytes,
                 entry.mime, int(entry.pinned), entry.last_played or 0.0,
                 entry.cached_at or 0.0))
            self._conn.commit()

    def remove(self, track_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM cached_tracks WHERE track_id=?",
                               (track_id,))
            self._conn.commit()

    def get(self, track_id: str) -> CacheEntry | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT track_id, engine, title, artist, album, duration_s,"
                " size_bytes, mime, pinned, last_played, cached_at"
                " FROM cached_tracks WHERE track_id=?",
                (track_id,)).fetchone()
        return _row_to_entry(row) if row else None

    def list_all(self, order: str = "recent") -> list[CacheEntry]:
        by = ("last_played DESC, track_id" if order == "recent"
              else "last_played ASC, track_id")
        with self._lock:
            rows = self._conn.execute(
                "SELECT track_id, engine, title, artist, album, duration_s,"
                f" size_bytes, mime, pinned, last_played, cached_at"
                f" FROM cached_tracks ORDER BY {by}").fetchall()
        return [_row_to_entry(row) for row in rows]

    def find(self, query: str, limit: int = 50) -> list[CacheEntry]:
        like = f"%{query.strip().lower()}%"
        with self._lock:
            rows = self._conn.execute(
                "SELECT track_id, engine, title, artist, album, duration_s,"
                " size_bytes, mime, pinned, last_played, cached_at"
                " FROM cached_tracks WHERE lower(title) LIKE ?"
                " OR lower(artist) LIKE ? OR lower(album) LIKE ?"
                " ORDER BY last_played DESC, track_id LIMIT ?",
                (like, like, like, max(1, limit))).fetchall()
        return [_row_to_entry(row) for row in rows]

    def set_pinned(self, track_id: str, pinned: bool) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE cached_tracks SET pinned=? WHERE track_id=?",
                (int(pinned), track_id))
            self._conn.commit()

    def mark_played(self, track_id: str, at: float) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE cached_tracks SET last_played=? WHERE track_id=?",
                (at, track_id))
            self._conn.commit()

    def totals(self) -> tuple[int, int]:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(size_bytes),0)"
                " FROM cached_tracks").fetchone()
        return (int(row[0]), int(row[1]))


def _row_to_entry(row: tuple) -> CacheEntry:
    (track_id, engine, title, artist, album, duration_s, size_bytes,
     mime, pinned, last_played, cached_at) = row
    return CacheEntry(track_id=track_id, engine=engine or "",
                      title=title or "", artist=artist or "",
                      album=album or "",
                      duration_s=(float(duration_s)
                                  if duration_s is not None else None),
                      size_bytes=(int(size_bytes)
                                  if size_bytes is not None else None),
                      mime=mime or "", pinned=bool(pinned),
                      last_played=float(last_played or 0.0),
                      cached_at=float(cached_at or 0.0))


def plan_eviction(entries: list[CacheEntry], need_bytes: int,
                  protected: Container[str] | None = None) -> list[str]:
    """Ids to drop so ``need_bytes`` are freed, unpinned LRU first.

    Pinned tracks and ``protected`` ones (the current song plus the
    prefetch window) are never picked. ``need_bytes`` <= 0 picks
    nothing. Sizes unknown (None) count as 0.
    """
    if need_bytes <= 0:
        return []
    skip = protected or frozenset()
    candidates = sorted(
        (entry for entry in entries
         if not entry.pinned and entry.track_id not in skip),
        key=lambda entry: (entry.last_played or 0.0, entry.track_id))
    picked: list[str] = []
    freed = 0
    for entry in candidates:
        picked.append(entry.track_id)
        freed += entry.size_bytes or 0
        if freed >= need_bytes:
            break
    return picked if freed >= need_bytes else []


class CacheManager:
    """Cap, floor and eviction around one proxy cache directory."""

    def __init__(self, cache_dir: Path | str, index: CacheIndex, *,
                 cap_bytes: int = DEFAULT_CAP_BYTES,
                 free_bytes_fn: Callable[[], int] | None = None,
                 clock: Callable[[], float] | None = None) -> None:
        if int(cap_bytes) <= 0:
            raise ValueError("cap_bytes must be positive")
        self._cache_dir = Path(cache_dir)
        self._index = index
        self._cap_bytes = int(cap_bytes)
        self._free_bytes_fn = free_bytes_fn
        self._clock = clock or time.time
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self._cache_dir, 0o700)
        except OSError:
            pass

    @property
    def cap_bytes(self) -> int:
        return self._cap_bytes

    def set_cap_bytes(self, cap_bytes: int) -> None:
        if int(cap_bytes) <= 0:
            raise ValueError("cap_bytes must be positive")
        self._cap_bytes = int(cap_bytes)

    def set_cap_gb(self, cap_gb: int) -> None:
        if int(cap_gb) not in VALID_CAP_CHOICES_GB:
            raise ValueError(f"cap must be one of {VALID_CAP_CHOICES_GB}")
        self.set_cap_bytes(int(cap_gb) * 2**30)

    def complete_path(self, track_id: str) -> Path:
        return self._cache_dir / track_id

    def part_path(self, track_id: str) -> Path:
        return self._cache_dir / (track_id + ".part")

    def is_cached(self, track_id: str) -> bool:
        return self.complete_path(track_id).is_file()

    def usage_bytes(self) -> int:
        """Disk actually used by complete and partial files."""
        total = 0
        try:
            with os.scandir(self._cache_dir) as entries:
                for entry in entries:
                    if entry.is_file():
                        try:
                            total += entry.stat().st_size
                        except OSError:
                            pass
        except OSError:
            pass
        return total

    def free_bytes(self) -> int:
        if self._free_bytes_fn is not None:
            try:
                return max(0, int(self._free_bytes_fn()))
            except Exception:
                return 0
        try:
            return max(0, shutil.disk_usage(self._cache_dir).free)
        except OSError:
            return 0

    def effective_cap(self) -> int:
        """min(cap, used + free - 1 GB): the floor always wins."""
        used = self.usage_bytes()
        return max(0, min(self._cap_bytes,
                          used + self.free_bytes() - MIN_FREE_BYTES))

    def register(self, entry: CacheEntry) -> CacheEntry:
        """Record a track, sizing it from disk when it is complete."""
        path = self.complete_path(entry.track_id)
        try:
            size = path.stat().st_size if path.is_file() else None
        except OSError:
            size = None
        stored = CacheEntry(
            track_id=entry.track_id, engine=entry.engine,
            title=entry.title or entry.track_id,
            artist=entry.artist, album=entry.album,
            duration_s=entry.duration_s,
            size_bytes=size if size is not None else entry.size_bytes,
            mime=entry.mime, pinned=entry.pinned,
            last_played=entry.last_played,
            cached_at=entry.cached_at or self._clock())
        self._index.upsert(stored)
        return stored

    def note_played(self, track_id: str) -> None:
        self._index.mark_played(track_id, self._clock())

    def set_pinned(self, track_id: str, pinned: bool) -> None:
        self._index.set_pinned(track_id, pinned)

    def sync_from_disk(self) -> None:
        """Drop index rows whose files are gone; fix sizes that moved."""
        on_disk: dict[str, int] = {}
        try:
            with os.scandir(self._cache_dir) as entries:
                for entry in entries:
                    name = entry.name
                    if entry.is_file() and not name.endswith(".part") \
                            and not name.startswith("."):
                        try:
                            on_disk[name] = entry.stat().st_size
                        except OSError:
                            pass
        except OSError:
            pass
        for known in self._index.list_all(order="oldest"):
            if known.track_id not in on_disk:
                self._index.remove(known.track_id)
            elif known.size_bytes != on_disk[known.track_id]:
                known.size_bytes = on_disk[known.track_id]
                self._index.upsert(known)

    def plan(self, need_bytes: int = 0,
             protected: Container[str] | None = None) -> list[str]:
        """Ids to evict: over-cap overflow plus ``need_bytes`` room."""
        over = self.usage_bytes() - self.effective_cap()
        need = max(0, over) + max(0, need_bytes)
        if need <= 0:
            return []
        entries = self._sized_entries()
        return plan_eviction(entries, need, protected)

    def evict(self, protected: Container[str] | None = None,
              need_bytes: int = 0) -> list[str]:
        """Delete planned files and their index rows; returns evicted ids."""
        evicted: list[str] = []
        for track_id in self.plan(need_bytes, protected):
            try:
                self.complete_path(track_id).unlink(missing_ok=True)
            except OSError:
                continue
            try:
                self.part_path(track_id).unlink(missing_ok=True)
            except OSError:
                pass
            self._index.remove(track_id)
            evicted.append(track_id)
        return evicted

    def _sized_entries(self) -> list[CacheEntry]:
        entries = self._index.list_all(order="oldest")
        for entry in entries:
            if entry.size_bytes is None:
                try:
                    path = self.complete_path(entry.track_id)
                    if path.is_file():
                        entry.size_bytes = path.stat().st_size
                except OSError:
                    pass
        return entries
