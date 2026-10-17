"""Playback rules: skip, sleep with fade, error policy, radio, offline mix.

Pure logic over injectable clocks so tests drive it with a fake clock.
The player service applies these decisions to the queue and playbin.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from xx_music.blocked import BlockedArtists, normalize_name
from xx_music.engine import (AlbumPage, ArtistPage, Engine, EngineError,
                             ErrorKind, Item, ItemKind, PlaylistPage,
                             SearchResults, Shelf, Track)

# Previous restarts the song past this position, else steps back.
RESTART_PREVIOUS_S = 3.0
# Radio extends itself when this many songs or fewer are left.
RADIO_EXTEND_REMAINING = 6
# Songs heard within this window never return to radios or mixes.
HEARD_EXCLUDE_S = 48 * 3600
# End-of-song sleep pauses when this much of the song is left.
END_OF_SONG_S = 1.5
# Sleep fades the volume out over these last seconds.
FADE_S = 10.0
# Network errors retry after these delays, then skip (keeping the song).
RETRY_DELAYS_S = (0.5, 1.0)
# Rate-limit retries wait retry_after_s clamped to this range.
RATE_RETRY_MIN_S = 15.0
RATE_RETRY_MAX_S = 600.0
# This many different songs failing to connect switches to the offline mix.
OFFLINE_TRIGGER_SONGS = 3
OFFLINE_MIX_LIMIT = 24
# Sleep choices offered: 15/30/45/60 minutes; 0 is off, -1 is end of song.
SLEEP_CHOICES_S = (15 * 60, 30 * 60, 45 * 60, 60 * 60)
SLEEP_OFF = 0
SLEEP_END_OF_SONG = -1
# Idle exit: stopped/empty with no window, then paused-with-queue.
IDLE_STOPPED_S = 60.0
IDLE_PAUSED_S = 30 * 60.0

Clock = Callable[[], float]


def should_restart_current(position_s: float, index: int) -> bool:
    return position_s > RESTART_PREVIOUS_S or index <= 0


def should_extend_radio(remaining: int) -> bool:
    return remaining <= RADIO_EXTEND_REMAINING


def filter_radio(tracks: list[Track], queued_ids: set[str],
                 heard_ids: set[str], disliked_ids: set[str],
                 blocked: BlockedArtists | None = None) -> list[Track]:
    """Drop queued, recently heard, disliked and blocked songs."""
    return [t for t in tracks if t.id not in queued_ids
            and t.id not in heard_ids and t.id not in disliked_ids
            and (blocked is None or not blocked.is_track_blocked(t))]


def _blocked_item(item: Item, blocked: BlockedArtists | None) -> bool:
    if blocked is None:
        return False
    if item.kind == ItemKind.TRACK:
        return item.track is not None and blocked.is_track_blocked(
            item.track)
    if item.kind == ItemKind.ARTIST:
        return blocked.is_blocked_artist(item.id, item.title)
    if item.kind == ItemKind.ALBUM:
        names = _subtitle_names(item.subtitle)
        for name in names:
            if blocked.is_blocked_name(name):
                return True
        return False
    return False


def _subtitle_names(subtitle: str) -> list[str]:
    names: list[str] = []
    for chunk in str(subtitle or "").split("·"):
        for part in chunk.split(","):
            name = normalize_name(part)
            if name and not name[:4].isdigit() and len(name) > 1:
                names.append(part)
    return names


def is_track_blocked(track: Track,
                     blocked: BlockedArtists | None) -> bool:
    return blocked is not None and blocked.is_track_blocked(track)


def _disliked_track_ids(ratings) -> set[str]:
    if not ratings:
        return set()
    try:
        from xx_music.thumbs import disliked_ids as _thumbs_disliked
    except Exception:  # noqa: BLE001 - thumbs never breaks the filter
        return set()
    try:
        return set(_thumbs_disliked(ratings))
    except Exception:  # noqa: BLE001 - a bad ratings map filters nothing
        return set()


def _is_disliked_track(track: Track, disliked: set[str]) -> bool:
    return bool(disliked) and track.id in disliked


def _is_disliked_item(item: Item, disliked: set[str]) -> bool:
    if not disliked:
        return False
    if item.kind == ItemKind.TRACK and item.track is not None:
        return item.track.id in disliked
    return False


def _order_with_thumbs_tracks(tracks: list[Track], ratings) -> list[Track]:
    if not ratings:
        return tracks
    try:
        from xx_music.thumbs import order_tracks as _order
    except Exception:  # noqa: BLE001 - ordering never breaks the filter
        return tracks
    try:
        return _order(tracks, ratings)
    except Exception:  # noqa: BLE001 - a bad ratings map keeps order
        return tracks


def _order_with_thumbs_items(items: list[Item], ratings) -> list[Item]:
    if not ratings:
        return items
    try:
        from xx_music.thumbs import order_items as _order_items
    except Exception:  # noqa: BLE001 - ordering never breaks the filter
        return items
    try:
        return _order_items(items, ratings)
    except Exception:  # noqa: BLE001 - a bad ratings map keeps order
        return items


def _order_with_thumbs_shelves(shelves: list[Shelf],
                               ratings) -> list[Shelf]:
    if not ratings:
        return shelves
    try:
        from xx_music.thumbs import order_shelves as _order_shelves
    except Exception:  # noqa: BLE001 - ordering never breaks the filter
        return shelves
    try:
        return _order_shelves(shelves, ratings)
    except Exception:  # noqa: BLE001 - a bad ratings map keeps order
        return shelves


def filter_tracks(tracks: list[Track] | tuple[Track, ...],
                  blocked: BlockedArtists | None,
                  ratings=None) -> list[Track]:
    disliked = _disliked_track_ids(ratings)
    if blocked is None and not disliked:
        return list(tracks)
    kept = [t for t in tracks
            if not _is_disliked_track(t, disliked)
            and (blocked is None or not blocked.is_track_blocked(t))]
    return _order_with_thumbs_tracks(kept, ratings)


def filter_items(items: list[Item] | tuple[Item, ...],
                 blocked: BlockedArtists | None,
                 ratings=None) -> list[Item]:
    disliked = _disliked_track_ids(ratings)
    if blocked is None and not disliked:
        return _order_with_thumbs_items(list(items), ratings)
    kept = [i for i in items
            if not _is_disliked_item(i, disliked)
            and not _blocked_item(i, blocked)]
    return _order_with_thumbs_items(kept, ratings)


def filter_shelves(shelves: list[Shelf],
                   blocked: BlockedArtists | None,
                   ratings=None) -> list[Shelf]:
    if blocked is None and not ratings:
        return list(shelves)
    out: list[Shelf] = []
    for shelf in shelves:
        items = tuple(filter_items(list(shelf.items), blocked, ratings))
        if items:
            out.append(Shelf(shelf.title, items))
    return _order_with_thumbs_shelves(out, ratings)


def filter_search(results: SearchResults,
                  blocked: BlockedArtists | None,
                  ratings=None, top_only: bool = False) -> SearchResults:
    if blocked is None and not ratings:
        return results
    disliked = _disliked_track_ids(ratings)
    if top_only and disliked:
        tracks = tuple(t for t in filter_tracks(
            list(results.tracks), blocked, None)
            if t.id not in disliked)
    else:
        tracks = tuple(filter_tracks(list(results.tracks), blocked))
    return SearchResults(
        tracks=tracks,
        albums=tuple(filter_items(list(results.albums), blocked)),
        artists=tuple(filter_items(list(results.artists), blocked)),
        playlists=tuple(filter_items(list(results.playlists), blocked)))


def filter_album_page(page: AlbumPage,
                      blocked: BlockedArtists | None) -> AlbumPage:
    if blocked is None:
        return page
    return AlbumPage(id=page.id, title=page.title, artists=page.artists,
                     year=page.year, kind_label=page.kind_label,
                     art_url=page.art_url,
                     tracks=tuple(filter_tracks(list(page.tracks), blocked)))


def filter_artist_page(page: ArtistPage,
                       blocked: BlockedArtists | None) -> ArtistPage:
    if blocked is None:
        return page
    return ArtistPage(
        id=page.id, name=page.name, art_url=page.art_url,
        top_tracks=tuple(filter_tracks(list(page.top_tracks), blocked)),
        albums=tuple(filter_items(list(page.albums), blocked)),
        singles=tuple(filter_items(list(page.singles), blocked)),
        related=tuple(filter_items(list(page.related), blocked)),
        radio_seed=page.radio_seed)


def filter_playlist_page(page: PlaylistPage,
                         blocked: BlockedArtists | None) -> PlaylistPage:
    if blocked is None:
        return page
    return PlaylistPage(
        id=page.id, title=page.title, author=page.author,
        art_url=page.art_url, track_count=page.track_count,
        tracks=tuple(filter_tracks(list(page.tracks), blocked)),
        editable=page.editable)


def filter_download_rows(rows: list,
                         blocked: BlockedArtists | None) -> list:
    if blocked is None:
        return list(rows)
    return [r for r in rows if not blocked.is_track_blocked(r.track)]


def fetch_radio_filtered(engine, seed_track_id: str,
                         blocked: BlockedArtists | None, limit: int,
                         exclude: set[str] | frozenset,
                         queued_ids: set[str] | None = None,
                         heard_ids: set[str] | None = None,
                         disliked_ids: set[str] | None = None,
                         ratings=None,
                         ) -> list[Track]:
    """Radio results with blocked artists skipped, refilled when short.

    Asks for more while the filtered batch is short and the engine
    still answers, so a queue never runs dry because of blocks.
    Disliked songs never return; liked artists rank first, down-heavy
    artists rank last (deterministic, stable for engine ties).
    """
    want = max(int(limit), 0)
    if want <= 0:
        return []
    queued = set(queued_ids or ())
    heard = set(heard_ids or ())
    disliked = set(disliked_ids or ()) | _disliked_track_ids(ratings)
    seen = set(exclude or ())
    collected: list[Track] = []
    collected_ids: set[str] = set()
    for _attempt in range(3):
        need = want - len(collected)
        batch = engine.radio(seed_track_id, limit=need + len(seen) + want,
                             exclude=frozenset(seen))
        if not batch:
            break
        for track in batch:
            seen.add(track.id)
            if track.id in collected_ids or track.id in queued \
                    or track.id in heard or track.id in disliked:
                continue
            if blocked is not None and blocked.is_track_blocked(track):
                continue
            collected.append(track)
            collected_ids.add(track.id)
            if len(collected) >= want:
                break
        if len(collected) >= want or len(batch) < need:
            break
    return _order_with_thumbs_tracks(collected[:want], ratings)


class FilteredEngine(Engine):
    """One filter layer over any engine: blocked artists never surface,
    disliked songs never return to suggestions, and thumbs reorder.

    Wraps catalog reads (home, search, artist/album/playlist pages,
    charts, library, mood shelves, radio) so screens need no changes.
    ``ratings_fn`` returns the local ratings map
    (``dict[track_id, (Rating, Track)]``) or None; it runs on the
    worker, so disk reads are fine.
    """

    def __init__(self, inner: Engine,
                 blocked: BlockedArtists | None,
                 ratings_fn=None) -> None:
        self._inner = inner
        self._blocked = blocked
        self._ratings_fn = ratings_fn
        self.key = inner.key
        self.name = inner.name
        self.capabilities = inner.capabilities

    def _ratings(self):
        if self._ratings_fn is None:
            return None
        try:
            return self._ratings_fn()
        except Exception:  # noqa: BLE001 - ratings never break catalog
            return None

    def supports(self, capability: str) -> bool:
        return self._inner.supports(capability)

    def status(self):
        return self._inner.status()

    def sign_in(self, credentials: dict[str, str]) -> str:
        return self._inner.sign_in(credentials)

    def sign_out(self) -> None:
        return self._inner.sign_out()

    def search(self, query: str, kind=None, limit: int = 20):
        ratings = self._ratings()
        raw = self._inner.search(query, kind, limit)
        if ratings:
            return filter_search(raw, self._blocked, ratings,
                                 top_only=kind is None)
        return filter_search(raw, self._blocked)

    def suggestions(self, query: str) -> list[str]:
        found = self._inner.suggestions(query)
        ratings = self._ratings()
        if not ratings:
            return found
        try:
            from xx_music.thumbs import filter_suggestion_strings
        except Exception:  # noqa: BLE001 - suggestions never break
            return found
        try:
            return filter_suggestion_strings(list(found), ratings)
        except Exception:  # noqa: BLE001 - a bad map keeps suggestions
            return found

    def home(self) -> list[Shelf]:
        return filter_shelves(self._inner.home(), self._blocked,
                              self._ratings())

    def moods(self):
        return self._inner.moods()

    def mood_shelves(self, mood_id: str) -> list[Shelf]:
        return filter_shelves(
            self._inner.mood_shelves(mood_id), self._blocked,
            self._ratings())

    def charts(self, region: str = "") -> list[Shelf]:
        return filter_shelves(
            self._inner.charts(region), self._blocked, self._ratings())

    def library(self, section) -> list[Item]:
        from xx_music.engine import LibrarySection as _Section

        ratings = self._ratings()
        if section == _Section.LIKED and ratings:
            try:
                from xx_music.thumbs import liked_tracks, merge_liked
            except Exception:  # noqa: BLE001 - merge never breaks
                return filter_items(
                    self._inner.library(section), self._blocked)
            try:
                remote = self._inner.library(section)
            except Exception:
                raise
            try:
                merged = merge_liked(liked_tracks(ratings), list(remote))
            except Exception:  # noqa: BLE001 - a bad map keeps remote
                return filter_items(remote, self._blocked)
            return filter_items(merged, self._blocked)
        return filter_items(self._inner.library(section), self._blocked,
                            ratings)

    def album(self, album_id: str) -> AlbumPage:
        return filter_album_page(
            self._inner.album(album_id), self._blocked)

    def artist(self, artist_id: str) -> ArtistPage:
        return filter_artist_page(
            self._inner.artist(artist_id), self._blocked)

    def playlist(self, playlist_id: str, limit: int = 200) -> PlaylistPage:
        return filter_playlist_page(
            self._inner.playlist(playlist_id, limit), self._blocked)

    def radio(self, seed_track_id: str, limit: int = 25,
              exclude=frozenset()) -> list[Track]:
        return fetch_radio_filtered(
            self._inner, seed_track_id, self._blocked, limit, set(exclude),
            ratings=self._ratings())

    def lyrics(self, track_id: str):
        return self._inner.lyrics(track_id)

    def rate(self, track_id: str, rating) -> None:
        return self._inner.rate(track_id, rating)

    def resolve(self, track_id: str, *, low_data: bool = False):
        return self._inner.resolve(track_id, low_data=low_data)

    def art_url(self, url: str, px: int) -> str:
        return self._inner.art_url(url, px)


def artist_key(track: Track) -> str:
    if track.artists:
        return track.artists[0].id or track.artists[0].name
    return f"track:{track.id}"


def build_offline_mix(tracks: list[Track], seed: Track | None,
                      heard_ids: set[str], limit: int = OFFLINE_MIX_LIMIT,
                      blocked: BlockedArtists | None = None,
                      ratings=None
                      ) -> list[Track]:
    """Up to ``limit`` on-phone songs: no heard songs, the seed artist at
    most 1 in 4, no artist again within 3 places."""
    seed_key = artist_key(seed) if seed is not None else ""
    disliked = _disliked_track_ids(ratings)
    ordered = _order_with_thumbs_tracks(list(tracks), ratings)
    max_seed = max(1, limit // 4)
    picked: list[Track] = []
    seed_count = 0
    recent: list[str] = []
    for track in ordered:
        if len(picked) >= limit:
            break
        if track.id in heard_ids:
            continue
        if track.id in disliked:
            continue
        if blocked is not None and blocked.is_track_blocked(track):
            continue
        key = artist_key(track)
        if seed_key and key == seed_key and seed_count >= max_seed:
            continue
        if key in recent[-3:]:
            continue
        picked.append(track)
        recent.append(key)
        if seed_key and key == seed_key:
            seed_count += 1
    return picked


def clamp_rate_retry(retry_after_s: float | None) -> float:
    try:
        delay = float(retry_after_s if retry_after_s is not None else 60.0)
    except (TypeError, ValueError):
        delay = 60.0
    return min(max(delay, RATE_RETRY_MIN_S), RATE_RETRY_MAX_S)


class SleepTimer:
    """Armed once; the service ticks it and fades/pauses accordingly."""

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or time.monotonic
        self.mode: int = SLEEP_OFF
        self.deadline: float = 0.0

    def arm(self, seconds: int) -> None:
        if seconds == SLEEP_OFF:
            self.mode = SLEEP_OFF
            self.deadline = 0.0
        elif seconds == SLEEP_END_OF_SONG:
            self.mode = SLEEP_END_OF_SONG
            self.deadline = 0.0
        elif seconds > 0:
            self.mode = seconds
            self.deadline = self._clock() + seconds
        else:
            raise ValueError(f"bad sleep value: {seconds}")

    @property
    def armed(self) -> bool:
        return self.mode != SLEEP_OFF

    def remaining(self) -> float:
        if self.mode == SLEEP_END_OF_SONG:
            return -1.0
        if not self.armed:
            return 0.0
        return max(0.0, self.deadline - self._clock())

    def expired(self) -> bool:
        return self.armed and self.mode != SLEEP_END_OF_SONG \
            and self._clock() >= self.deadline

    def fade_scale(self) -> float:
        """Volume multiplier: 1 until the last FADE_S seconds, then linear."""
        if self.mode == SLEEP_END_OF_SONG or not self.armed:
            return 1.0
        left = self.remaining()
        if left >= FADE_S:
            return 1.0
        return max(0.0, left / FADE_S)

    def end_of_song_pause(self, position_s: float | None,
                          duration_s: float | None) -> bool:
        return (self.mode == SLEEP_END_OF_SONG
                and position_s is not None and duration_s is not None
                and duration_s - position_s <= END_OF_SONG_S)


@dataclass
class ErrorAction:
    """One error-policy decision. ``action`` is drop-next, retry, skip,
    reresolve, pause, rate-retry or offline-mix; ``delay_s`` applies to
    the retry kinds."""
    action: str
    delay_s: float | None = None


class ErrorPolicy:
    """Maps engine failures to queue actions. Counters reset on the first
    song that plays; only the service knows the queue length, so a lone
    song turning skip into pause is applied there."""

    RETRYABLE = (ErrorKind.TIMEOUT, ErrorKind.OFFLINE, ErrorKind.OTHER)
    DROP = (ErrorKind.NOT_FOUND, ErrorKind.NOT_AVAILABLE)
    PAUSE = (ErrorKind.HELPER_MISSING, ErrorKind.SOURCE_CHANGED,
             ErrorKind.AUTH_EXPIRED, ErrorKind.AUTH_REQUIRED)

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or time.monotonic
        self.attempts: dict[str, int] = {}
        self.connect_failed_ids: list[str] = []
        self._offline_announced = False

    def note_playing(self) -> None:
        self.attempts.clear()
        self.connect_failed_ids.clear()
        self._offline_announced = False

    def note_offline_mix(self) -> None:
        self.connect_failed_ids.clear()
        self._offline_announced = False

    def _note_connect_failure(self, track_id: str) -> bool:
        """True when a third different song fails: switch to offline mix."""
        if track_id not in self.connect_failed_ids:
            self.connect_failed_ids.append(track_id)
        return len(self.connect_failed_ids) >= OFFLINE_TRIGGER_SONGS \
            and not self._offline_announced

    def decide(self, error: EngineError, track_id: str) -> ErrorAction:
        kind = error.kind
        if kind in self.DROP:
            return ErrorAction("drop-next")
        if kind == ErrorKind.RATE_LIMITED:
            self._note_connect_failure(track_id)
            return ErrorAction("rate-retry",
                               clamp_rate_retry(error.retry_after_s))
        if kind in self.PAUSE:
            return ErrorAction("pause")
        attempt = self.attempts.get(track_id, 0)
        if attempt < len(RETRY_DELAYS_S):
            self.attempts[track_id] = attempt + 1
            if self._note_connect_failure(track_id):
                self._offline_announced = True
                return ErrorAction("offline-mix")
            return ErrorAction("retry", RETRY_DELAYS_S[attempt])
        self.attempts.pop(track_id, None)
        if self._note_connect_failure(track_id):
            self._offline_announced = True
            return ErrorAction("offline-mix")
        return ErrorAction("skip")

    def decode_error(self, track_id: str) -> ErrorAction:
        return ErrorAction("drop-next")

    def expired_url(self, already_retried: bool) -> ErrorAction:
        """A 403/expiry re-resolves once and resumes, then skips."""
        return ErrorAction("skip" if already_retried else "reresolve")

    def skip_or_pause(self, queue_length: int) -> str:
        return "skip" if queue_length > 1 else "pause"


def idle_action(status: str, queue_length: int, idle_s: float,
                window_connected: bool) -> str | None:
    """None to stay, 'exit' now, or 'save-exit' after writing the queue."""
    if window_connected or status == "Playing":
        return None
    if queue_length == 0 or status == "Stopped":
        return "exit" if idle_s >= IDLE_STOPPED_S else None
    return "save-exit" if idle_s >= IDLE_PAUSED_S else None
