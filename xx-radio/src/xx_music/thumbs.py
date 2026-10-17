"""Local thumbs up/down: ratings helpers and deterministic scoring.

The store itself lives in ``UiStore`` (``ratings.json``); this module is
the pure policy over it: who is disliked, how artists weigh, how tracks
and shelves order, when to suggest a kill, and how liked lists merge.
Everything here is pure and unit-testable: no I/O, no clock, no
randomness.
"""

from __future__ import annotations

from xx_music.blocked import normalize_name
from xx_music.engine import Item, ItemKind, Rating, Track

#: Downs on one artist that lower its weight and trigger one kill hint.
DOWN_THRESHOLD = 3

#: Scoring weights for the deterministic ordering pass.
LIKED_TRACK_BOOST = 10
LIKED_ARTIST_BOOST = 3
DOWN_ARTIST_PENALTY = 5

RatingsMap = dict[str, tuple[Rating, Track]]


def artist_key(artist_id: str = "", artist_name: str = "") -> str:
    if str(artist_id or ""):
        return f"id:{artist_id}"
    return f"name:{normalize_name(artist_name)}"


def suggestion_key(artist_id: str = "", artist_name: str = "") -> str:
    return artist_key(artist_id, artist_name)


def track_artist_keys(track: Track) -> list[str]:
    keys = []
    for artist in getattr(track, "artists", ()) or ():
        key = artist_key(getattr(artist, "id", "") or "",
                         getattr(artist, "name", "") or "")
        if key not in ("id:", "name:") and key not in keys:
            keys.append(key)
    return keys


def rating_of(ratings: RatingsMap | None, track_id: str) -> Rating:
    if not ratings:
        return Rating.NONE
    found = ratings.get(str(track_id or ""))
    return found[0] if found else Rating.NONE


def disliked_ids(ratings: RatingsMap | None) -> set[str]:
    if not ratings:
        return set()
    return {tid for tid, (rating, _t) in ratings.items()
            if rating == Rating.DISLIKE}


def liked_ids(ratings: RatingsMap | None) -> set[str]:
    if not ratings:
        return set()
    return {tid for tid, (rating, _t) in ratings.items()
            if rating == Rating.LIKE}


def liked_tracks(ratings: RatingsMap | None) -> list[Track]:
    if not ratings:
        return []
    return [track for rating, track in ratings.values()
            if rating == Rating.LIKE]


def disliked_tracks(ratings: RatingsMap | None) -> list[Track]:
    if not ratings:
        return []
    return [track for rating, track in ratings.values()
            if rating == Rating.DISLIKE]


def _artist_counts(ratings: RatingsMap | None,
                   want: Rating) -> dict[str, tuple[int, str, str]]:
    counts: dict[str, list] = {}
    if not ratings:
        return {}
    for rating, track in ratings.values():
        if rating != want:
            continue
        for artist in getattr(track, "artists", ()) or ():
            aid = str(getattr(artist, "id", "") or "")
            name = str(getattr(artist, "name", "") or "")
            if not aid and not normalize_name(name):
                continue
            key = artist_key(aid, name)
            slot = counts.get(key)
            if slot is None:
                counts[key] = [1, name or aid, aid]
            else:
                slot[0] += 1
                if not slot[1] and (name or aid):
                    slot[1] = name or aid
                if not slot[2] and aid:
                    slot[2] = aid
    return {k: (v[0], v[1], v[2]) for k, v in counts.items()}


def artist_down_counts(
        ratings: RatingsMap | None) -> dict[str, tuple[int, str, str]]:
    return _artist_counts(ratings, Rating.DISLIKE)


def artist_up_counts(
        ratings: RatingsMap | None) -> dict[str, tuple[int, str, str]]:
    return _artist_counts(ratings, Rating.LIKE)


def score_track(track: Track, ratings: RatingsMap | None = None,
                down_counts: dict | None = None,
                up_counts: dict | None = None) -> int:
    score = 0
    if ratings is not None and rating_of(ratings, track.id) == Rating.LIKE:
        score += LIKED_TRACK_BOOST
    downs = down_counts if down_counts is not None else (
        artist_down_counts(ratings) if ratings else {})
    ups = up_counts if up_counts is not None else (
        artist_up_counts(ratings) if ratings else {})
    for key in track_artist_keys(track):
        hit = downs.get(key)
        if hit:
            score -= DOWN_ARTIST_PENALTY * hit[0]
        hit = ups.get(key)
        if hit:
            score += LIKED_ARTIST_BOOST * hit[0]
    return score


def score_item(item: Item, ratings: RatingsMap | None = None,
               down_counts: dict | None = None,
               up_counts: dict | None = None) -> int:
    if item.kind == ItemKind.TRACK and item.track is not None:
        return score_track(item.track, ratings, down_counts, up_counts)
    if item.kind == ItemKind.ARTIST:
        key = artist_key(item.id, item.title)
        score = 0
        downs = down_counts if down_counts is not None else (
            artist_down_counts(ratings) if ratings else {})
        ups = up_counts if up_counts is not None else (
            artist_up_counts(ratings) if ratings else {})
        hit = downs.get(key)
        if hit:
            score -= DOWN_ARTIST_PENALTY * hit[0]
        hit = ups.get(key)
        if hit:
            score += LIKED_ARTIST_BOOST * hit[0]
        return score
    return 0


def order_tracks(tracks: list[Track] | tuple[Track, ...],
                 ratings: RatingsMap | None) -> list[Track]:
    if not ratings:
        return list(tracks)
    downs = artist_down_counts(ratings)
    ups = artist_up_counts(ratings)
    scored = [(score_track(t, ratings, downs, ups), i, t)
              for i, t in enumerate(tracks)]
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [t for _s, _i, t in scored]


def order_items(items: list[Item] | tuple[Item, ...],
                ratings: RatingsMap | None) -> list[Item]:
    if not ratings:
        return list(items)
    downs = artist_down_counts(ratings)
    ups = artist_up_counts(ratings)
    scored = [(score_item(i, ratings, downs, ups), n, i)
              for n, i in enumerate(items)]
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [i for _s, _n, i in scored]


def order_shelves(shelves: list, ratings: RatingsMap | None) -> list:
    if not ratings:
        return list(shelves)
    downs = artist_down_counts(ratings)
    ups = artist_up_counts(ratings)
    rows = []
    for index, shelf in enumerate(shelves):
        items = list(getattr(shelf, "items", ()))
        best = 0
        for item in items:
            score = score_item(item, ratings, downs, ups)
            if score > best:
                best = score
        rows.append((-best, index, shelf))
    rows.sort()
    return [shelf for _b, _i, shelf in rows]


def filter_suggestion_strings(suggestions: list[str],
                              ratings: RatingsMap | None) -> list[str]:
    if not ratings:
        return list(suggestions)
    dead = {t.title.casefold() for t in disliked_tracks(ratings) if t.title}
    dead |= {a.casefold() for _r, t in ratings.values()
             if _r == Rating.DISLIKE for a in
             [x.name for x in (getattr(t, "artists", ()) or ())] if a}
    return [s for s in suggestions if s.casefold() not in dead]


def kill_candidate(ratings: RatingsMap | None, blocked=None,
                   already: set[str] | frozenset | None = None
                   ) -> tuple[str, str] | None:
    already = set(already or ())
    counts = artist_down_counts(ratings)
    ranked = sorted(counts.items(),
                    key=lambda kv: (-kv[1][0], kv[1][1].casefold(), kv[0]))
    for key, (count, name, aid) in ranked:
        if count < DOWN_THRESHOLD:
            break
        if key in already:
            continue
        if blocked is not None:
            try:
                if blocked.is_blocked_artist(aid, name):
                    continue
            except Exception:  # noqa: BLE001 - a broken block list skips
                pass
        return (aid, name)
    return None


def merge_liked(local_tracks: list[Track],
                remote_items: list[Item]) -> list[Item]:
    seen: set[str] = set()
    out: list[Item] = []
    for track in local_tracks or ():
        if track.id and track.id not in seen:
            seen.add(track.id)
            out.append(Item(ItemKind.TRACK, track.id, track.title,
                            track.artist_line, track.art_url, track))
    for item in remote_items or ():
        key = item.track.id if item.track is not None else item.id
        if key and key not in seen:
            seen.add(key)
            out.append(item)
        elif not key:
            out.append(item)
    return out
