"""Blocked artists: the local "kill artist" store.

One JSON file next to the other local data (history, ratings). Each
entry keeps the engine's stable artist id when one is known, the
display name, and when it was blocked. Two artists can share a name,
so matching is by id when the candidate has one, and by normalised
name only when it has none.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


def normalize_name(name: str) -> str:
    return " ".join(str(name or "").split()).casefold()


@dataclass(frozen=True)
class BlockedArtist:
    id: str = ""
    name: str = ""
    blocked_at: float = 0.0

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name,
                "blocked_at": self.blocked_at}

    @classmethod
    def from_dict(cls, raw: dict) -> BlockedArtist:
        return cls(id=str(raw.get("id") or ""),
                   name=str(raw.get("name") or ""),
                   blocked_at=float(raw.get("blocked_at") or 0.0))


def _entry_key(entry: BlockedArtist) -> tuple:
    if entry.id:
        return ("id", entry.id)
    return ("name", normalize_name(entry.name))


class BlockedArtists:
    """Thread-safe blocked-artist set with atomic JSON persistence."""

    def __init__(self, path: Path | str,
                 clock: Callable[[], float] | None = None) -> None:
        self._path = Path(path)
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._by_id: dict[str, BlockedArtist] = {}
        self._by_name: dict[str, BlockedArtist] = {}
        self._load_locked()

    def _load_locked(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, list):
            return
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                entry = BlockedArtist.from_dict(item)
            except (TypeError, ValueError):
                continue
            if not entry.id and not normalize_name(entry.name):
                continue
            self._remember(entry)

    def _remember(self, entry: BlockedArtist) -> None:
        if entry.id:
            self._by_id[entry.id] = entry
        else:
            key = normalize_name(entry.name)
            if key:
                self._by_name[key] = entry

    def _save_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self._path.parent, 0o700)
        except OSError:
            pass
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        data = [e.to_dict() for e in self.list_locked()]
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, self._path)

    def list_locked(self) -> list[BlockedArtist]:
        found = [*self._by_id.values(), *self._by_name.values()]
        found.sort(key=lambda e: (-(e.blocked_at or 0.0),
                                  normalize_name(e.name), e.id))
        return found

    def list(self) -> list[BlockedArtist]:
        with self._lock:
            return self.list_locked()

    def ids(self) -> set[str]:
        with self._lock:
            return set(self._by_id)

    def names(self) -> set[str]:
        with self._lock:
            return set(self._by_name)

    def is_blocked_id(self, artist_id: str) -> bool:
        if not artist_id:
            return False
        with self._lock:
            return artist_id in self._by_id

    def is_blocked_name(self, name: str) -> bool:
        key = normalize_name(name)
        if not key:
            return False
        with self._lock:
            return key in self._by_name

    def is_blocked_artist(self, artist_id: str = "",
                          artist_name: str = "") -> bool:
        if artist_id:
            return self.is_blocked_id(artist_id)
        return self.is_blocked_name(artist_name)

    def is_track_blocked(self, track) -> bool:
        artists = getattr(track, "artists", ()) or ()
        for artist in artists:
            aid = getattr(artist, "id", "") or ""
            name = getattr(artist, "name", "") or ""
            if aid:
                if self.is_blocked_id(aid):
                    return True
            elif normalize_name(name) and self.is_blocked_name(name):
                return True
        return False

    def block(self, artist_id: str = "",
              artist_name: str = "") -> BlockedArtist | None:
        artist_id = str(artist_id or "")
        artist_name = str(artist_name or "")
        if not artist_id and not normalize_name(artist_name):
            return None
        with self._lock:
            if artist_id:
                existing = self._by_id.get(artist_id)
                if existing is not None:
                    return existing
            else:
                existing = self._by_name.get(normalize_name(artist_name))
                if existing is not None:
                    return existing
            entry = BlockedArtist(id=artist_id, name=artist_name,
                                  blocked_at=self._clock())
            self._remember(entry)
            self._save_locked()
            return entry

    def unblock(self, artist_id: str = "",
                artist_name: str = "") -> bool:
        artist_id = str(artist_id or "")
        key = normalize_name(artist_name)
        with self._lock:
            if artist_id:
                if artist_id not in self._by_id:
                    return False
                del self._by_id[artist_id]
                self._save_locked()
                return True
            if key and key in self._by_name:
                del self._by_name[key]
                self._save_locked()
                return True
            return False

    def clear(self) -> None:
        with self._lock:
            self._by_id.clear()
            self._by_name.clear()
            self._save_locked()

    def refresh(self) -> None:
        with self._lock:
            self._by_id.clear()
            self._by_name.clear()
            self._load_locked()


def purge_queue(queue, is_blocked) -> tuple[int, bool]:
    """Drop blocked tracks from a Queue in place.

    Returns (removed_count, current_was_blocked). When the current
    track was blocked the index lands on the next kept song (or the
    closest one when it was last); the caller restarts playback there.
    """
    tracks = list(getattr(queue, "tracks", ()))
    index = int(getattr(queue, "index", -1))
    current = None
    if 0 <= index < len(tracks):
        current = tracks[index]
    kept = [t for t in tracks if not is_blocked(t)]
    removed = len(tracks) - len(kept)
    if not removed:
        return (0, False)
    current_blocked = current is not None and bool(is_blocked(current))
    if not kept:
        queue.tracks = []
        queue.index = -1
    elif not current_blocked and current is not None:
        queue.tracks = kept
        queue.index = next((i for i, t in enumerate(kept)
                            if t.id == current.id), 0)
    else:
        queue.tracks = kept
        following = None
        for pos in range(index + 1, len(tracks)):
            if not is_blocked(tracks[pos]):
                following = tracks[pos]
                break
        if following is not None:
            queue.index = next((i for i, t in enumerate(kept)
                                if t.id == following.id), 0)
        else:
            queue.index = min(max(index, 0), len(kept) - 1)
    try:
        queue.version = int(getattr(queue, "version", 0)) + 1
    except (TypeError, ValueError):
        pass
    return (removed, current_blocked)


def confirm_text(artist_name: str) -> tuple[str, str]:
    name = " ".join(str(artist_name or "").split()) or "this artist"
    return (f"Kill {name}?",
            "Their songs leave your queue and won't be suggested again.")


def killed_text(artist_name: str) -> str:
    name = " ".join(str(artist_name or "").split()) or "Artist"
    return f"Killed {name}"


def blocked_summary(count: int) -> str:
    if count <= 0:
        return "None"
    return "1 artist" if count == 1 else f"{count} artists"
