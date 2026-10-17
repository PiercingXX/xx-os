"""What the window remembers between runs: small JSON files.

- the Home shelves, so Home paints from disk before the network answers;
- recent searches (newest first, 12 kept);
- songs this phone played (Listen again, History when signed out);
- local likes and dislikes (Liked when signed out);
- the window's settings (``config.json``: storage limit, Wi-Fi only...).

Reads and writes block on disk, so screens call them on the worker.
Writes go to a temporary file first, then an atomic rename.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from xx_music.engine import Item, ItemKind, Rating, Shelf, Track
from xx_music.paths import AppPaths

RECENTS_KEPT = 12
HISTORY_KEPT = 200

#: Window settings and their defaults (keys shared with the service).
PREF_DEFAULTS: dict[str, Any] = {
    "storage_cap_gb": 2,
    "wifi_only": True,
    "keep_liked_offline": False,
    "data_saver": True,
    "loudness_leveling": False,
    "charts_region": "",
    "first_run_done": False,
    "welcome_done": False,
}


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def item_to_dict(item: Item) -> dict:
    raw = asdict(item)
    raw["kind"] = item.kind.value
    raw["track"] = item.track.to_dict() if item.track else None
    return raw


def item_from_dict(raw: dict) -> Item:
    track = raw.get("track")
    return Item(kind=ItemKind(raw["kind"]), id=str(raw["id"]),
                title=str(raw.get("title") or ""),
                subtitle=str(raw.get("subtitle") or ""),
                art_url=str(raw.get("art_url") or ""),
                track=Track.from_dict(track) if track else None)


def shelves_to_json(shelves: list[Shelf]) -> list:
    return [{"title": s.title, "items": [item_to_dict(i) for i in s.items]}
            for s in shelves]


def shelves_from_json(raw) -> list[Shelf]:
    shelves = []
    for shelf in raw if isinstance(raw, list) else []:
        try:
            shelves.append(Shelf(str(shelf["title"]), tuple(
                item_from_dict(i) for i in shelf.get("items") or ())))
        except (KeyError, TypeError, ValueError):
            continue
    return shelves


class UiStore:
    def __init__(self, paths: AppPaths, engine_key: str = "") -> None:
        self._paths = paths
        self._key = engine_key or "engine"
        self._lock = threading.Lock()

    # files
    def _state(self, name: str) -> Path:
        return self._paths.state_dir() / name

    def home_path(self) -> Path:
        return self._paths.cache_dir() / f"home-{self._key}.json"

    # -- home cache ------------------------------------------------------------
    def load_home(self) -> tuple[list[Shelf], float]:
        raw = _read(self.home_path(), {})
        if not isinstance(raw, dict):
            return [], 0.0
        return shelves_from_json(raw.get("shelves")), float(
            raw.get("saved_at") or 0.0)

    def save_home(self, shelves: list[Shelf]) -> None:
        _write(self.home_path(), {"saved_at": time.time(),
                                  "shelves": shelves_to_json(shelves)})

    # -- recent searches ---------------------------------------------------------
    def recents(self) -> list[str]:
        raw = _read(self._state("recent-searches.json"), [])
        return [str(q) for q in raw if isinstance(q, str)][:RECENTS_KEPT] \
            if isinstance(raw, list) else []

    def add_recent(self, query: str) -> list[str]:
        query = " ".join(query.split())
        if not query:
            return self.recents()
        with self._lock:
            kept = [q for q in self.recents()
                    if q.casefold() != query.casefold()]
            kept = [query, *kept][:RECENTS_KEPT]
            _write(self._state("recent-searches.json"), kept)
        return kept

    def clear_recents(self) -> None:
        with self._lock:
            _write(self._state("recent-searches.json"), [])

    # -- local history -------------------------------------------------------------
    def history(self) -> list[Track]:
        raw = _read(self._state("history.json"), [])
        tracks = []
        for entry in raw if isinstance(raw, list) else []:
            try:
                tracks.append(Track.from_dict(entry["track"]))
            except (KeyError, TypeError, ValueError):
                continue
        return tracks

    def note_played(self, track: Track) -> None:
        with self._lock:
            raw = _read(self._state("history.json"), [])
            raw = [e for e in raw if isinstance(e, dict)
                   and (e.get("track") or {}).get("id") != track.id]
            raw.insert(0, {"at": time.time(), "track": track.to_dict()})
            _write(self._state("history.json"), raw[:HISTORY_KEPT])

    # -- local ratings ---------------------------------------------------------------
    def ratings(self) -> dict[str, tuple[Rating, Track]]:
        raw = _read(self._state("ratings.json"), {})
        found = {}
        for track_id, entry in (raw.items() if isinstance(raw, dict) else ()):
            try:
                found[track_id] = (Rating(entry["rating"]),
                                   Track.from_dict(entry["track"]))
            except (KeyError, TypeError, ValueError):
                continue
        return found

    def set_rating(self, track: Track, rating: Rating) -> None:
        with self._lock:
            raw = _read(self._state("ratings.json"), {})
            if not isinstance(raw, dict):
                raw = {}
            if rating == Rating.NONE:
                raw.pop(track.id, None)
            else:
                raw[track.id] = {"rating": rating.value,
                                 "track": track.to_dict(),
                                 "at": time.time()}
            _write(self._state("ratings.json"), raw)

    def liked(self) -> list[Track]:
        rated = self.ratings()
        return [track for rating, track in rated.values()
                if rating == Rating.LIKE]

    # -- settings ----------------------------------------------------------------------
    def prefs(self) -> dict[str, Any]:
        raw = _read(self._paths.config_path(), {})
        merged = dict(PREF_DEFAULTS)
        if isinstance(raw, dict):
            merged.update({k: v for k, v in raw.items() if k in merged})
        return merged

    def set_pref(self, key: str, value: Any) -> dict[str, Any]:
        if key not in PREF_DEFAULTS:
            raise KeyError(key)
        with self._lock:
            raw = _read(self._paths.config_path(), {})
            if not isinstance(raw, dict):
                raw = {}
            raw[key] = value
            _write(self._paths.config_path(), raw)
        return self.prefs()
