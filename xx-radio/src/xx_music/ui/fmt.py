"""Plain text helpers for the music screens. Pure: no GTK, no I/O."""

from __future__ import annotations

from xx_music.engine import AlbumPage, Item, ItemKind, PlaylistPage, Track


def duration(seconds: float | None) -> str:
    """``3:07`` or ``1:02:03``; empty when unknown."""
    if seconds is None or seconds < 0:
        return ""
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def clock(seconds: float | None) -> str:
    """Like :func:`duration` but ``0:00`` when unknown (scrubber times)."""
    return duration(seconds) or "0:00"


def initials(title: str, limit: int = 2) -> str:
    """Up to two capital letters for a placeholder tile."""
    words = [w for w in "".join(
        ch if ch.isalnum() else " " for ch in title or "").split() if w]
    skip = {"the", "a", "an", "of", "and"}
    picked = [w for w in words if w.lower() not in skip] or words
    letters = "".join(w[0] for w in picked[:limit]).upper()
    return letters or "?"


def songs(count: int | None) -> str:
    if count is None:
        return ""
    return "1 song" if count == 1 else f"{count} songs"


def size(num_bytes: int | None) -> str:
    """Human size, one decimal from GB down to MB."""
    if not num_bytes or num_bytes <= 0:
        return "0 MB"
    gb = num_bytes / 2**30
    if gb >= 1:
        return f"{gb:.1f} GB"
    mb = num_bytes / 2**20
    return f"{max(mb, 0.1):.0f} MB" if mb >= 1 else "1 MB"


def joined(*parts: str) -> str:
    return " · ".join(p for p in parts if p)


def kind_word(kind: ItemKind) -> str:
    return {ItemKind.TRACK: "Song", ItemKind.ALBUM: "Album",
            ItemKind.ARTIST: "Artist", ItemKind.PLAYLIST: "Playlist",
            ItemKind.MOOD: "Mood"}.get(kind, "")


def track_line(track: Track, *extra: str) -> str:
    """Secondary line for a song row: artists, then extras."""
    return joined(track.artist_line, *extra)


def album_meta(page: AlbumPage) -> str:
    return joined(page.kind_label, page.year, songs(len(page.tracks)))


def playlist_meta(page: PlaylistPage) -> str:
    count = page.track_count if page.track_count is not None \
        else len(page.tracks)
    return joined("Playlist", page.author, songs(count))


def total_time(tracks) -> str:
    seconds = sum(t.duration_s or 0 for t in tracks)
    if seconds <= 0:
        return ""
    minutes = int(seconds // 60)
    if minutes >= 60:
        return f"{minutes // 60} h {minutes % 60} min"
    return f"{minutes} min"


def item_line(item: Item) -> str:
    """Muted line under a result row: the kind word, then the subtitle
    unless it only repeats the kind."""
    word = kind_word(item.kind)
    sub = item.subtitle
    if sub.strip().lower() == word.lower():
        sub = ""
    return joined(word, sub)


def origin_text(origin: dict | None, current: Track | None = None) -> str:
    """'Playing from' caption text for a queue origin dict."""
    if not origin:
        return ""
    title = str(origin.get("title") or "")
    kind = str(origin.get("type") or "")
    if kind == "radio":
        seed = title or (current.title if current else "")
        return f"Radio · {seed}" if seed else "Radio"
    if kind == "offline":
        return "On this phone"
    if title:
        return title
    return {"album": "Album", "playlist": "Playlist",
            "artist": "Artist", "search": "Search"}.get(kind, "")


def sleep_text(remaining_s: float | None) -> str:
    if remaining_s is None:
        return ""
    total = max(0, int(remaining_s))
    return f"{total // 60}:{total % 60:02d}"
