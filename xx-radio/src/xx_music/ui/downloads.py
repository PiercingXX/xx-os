"""Where On this phone comes from by default: the cache index, read-only.

The player service is the single writer of ``music.db``; the window
opens it read-only and lists the pinned (downloaded) songs. Songs being
downloaded or that failed come from the service later; until it
publishes them, only finished downloads show.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from xx_music.engine import AlbumRef, ArtistRef, Track
from xx_music.ui.context import DONE, DownloadRow


def read_done(db_path: Path | str) -> list[DownloadRow]:
    """Pinned songs from the cache index, newest first (worker)."""
    path = Path(db_path)
    if not path.is_file():
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True,
                               timeout=2)
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(
            "SELECT track_id, title, artist, album, duration_s, size_bytes"
            " FROM cached_tracks WHERE pinned=1"
            " ORDER BY cached_at DESC, track_id").fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    found = []
    for track_id, title, artist, album, duration_s, size_bytes in rows:
        track = Track(
            id=str(track_id), title=str(title or track_id),
            artists=(ArtistRef(str(artist)),) if artist else (),
            album=AlbumRef(str(album)) if album else None,
            duration_s=float(duration_s) if duration_s else None)
        found.append(DownloadRow(track, DONE, 1.0, "",
                                 int(size_bytes) if size_bytes else None))
    return found


def default_rows(ui) -> list[DownloadRow]:
    return read_done(ui.paths.db_path())
