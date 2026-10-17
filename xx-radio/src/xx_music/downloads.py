"""Pinned downloads, the download queue and offline mode.

Everything here fills the loopback proxy's cache: a download is one full
GET through the proxy, which tees bytes to ``<track_id>.part`` and
renames it when complete. A dropped fetch leaves the ``.part`` behind
and the next GET resumes it with Range, so this module never deletes
partial files and never implements its own fetcher.

Policy, from the spec: Wi-Fi-only by default; 3 refusals within 15
minutes pause background downloads for 1 hour; at most 200 background
downloads a day; gone tracks are never retried; refused tracks wait
24 h; the playback gate pauses downloads while the player buffers and
for 30 s after any tap or song start, and spaces downloads 20 s apart
while music plays. After 3 consecutive network failures the queue
switches to offline mode and plays a mix from cached tracks.
"""

from __future__ import annotations

import collections
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from xx_music.engine import EngineError, ErrorKind, Track

MAX_DAILY_DOWNLOADS = 200
REFUSAL_WINDOW_S = 15 * 60
REFUSAL_THRESHOLD = 3
REFUSAL_COOLDOWN_S = 60 * 60
REFUSED_RETRY_S = 24 * 60 * 60
DAY_S = 24 * 60 * 60

PREFETCH_UNMETERED = 8
PREFETCH_METERED = 2

GATE_INTERACTION_QUIET_S = 30
GATE_PLAYING_GAP_S = 20

OFFLINE_FAILURE_THRESHOLD = 3
MIX_LIMIT = 24
MIX_EXCLUDE_HEARD_S = 48 * 60 * 60

GONE_KINDS = frozenset({ErrorKind.NOT_FOUND, ErrorKind.NOT_AVAILABLE})
REFUSAL_KINDS = frozenset({ErrorKind.RATE_LIMITED})
NETWORK_KINDS = frozenset({ErrorKind.OFFLINE, ErrorKind.TIMEOUT})
PAUSE_KINDS = frozenset({ErrorKind.HELPER_MISSING,
                         ErrorKind.SOURCE_CHANGED,
                         ErrorKind.AUTH_EXPIRED,
                         ErrorKind.AUTH_REQUIRED})


def classify_failure(kind: ErrorKind) -> str:
    """Map an engine error to queue handling.

    ``gone`` drops the track for good, ``refusal`` cools the queue
    down, ``network`` counts toward offline mode, ``pause`` stops the
    queue without walking it, ``transient`` retries later.
    """
    kind = ErrorKind(kind)
    if kind in GONE_KINDS:
        return "gone"
    if kind in REFUSAL_KINDS:
        return "refusal"
    if kind in NETWORK_KINDS:
        return "network"
    if kind in PAUSE_KINDS:
        return "pause"
    return "transient"


class FakeClock:
    """Injectable clock for gate timing tests; also called directly."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now_s = float(start)

    def __call__(self) -> float:
        return self.now_s

    def advance(self, seconds: float) -> float:
        self.now_s += float(seconds)
        return self.now_s


def plan_prefetch(queue_ids: list[str], current: int,
                  cached: set[str] | frozenset = frozenset(),
                  metered: bool = False,
                  exclude: set[str] | frozenset = frozenset()
                  ) -> list[str]:
    """Ids to prefetch: the next 8 (unmetered) or 2 (metered) songs.

    Skips the song that is playing, anything already cached, and
    ``exclude`` (gone or disliked songs). Nearest first; a song that
    leaves this window must have its fetch cancelled.
    """
    depth = PREFETCH_METERED if metered else PREFETCH_UNMETERED
    window = queue_ids[current + 1:current + 1 + depth]
    return [track_id for track_id in window
            if track_id not in cached and track_id not in exclude]


def build_offline_mix(entries: list[Any], heard: dict[str, float] | None,
                      seed_artist: str = "", limit: int = MIX_LIMIT,
                      now: float | None = None) -> list[Any]:
    """Up to ``limit`` cached entries playable with no link.

    Skips anything heard in the last 48 h, never repeats an artist
    within 3 places, and keeps the seed artist to at most 1 in 4.
    Least-recently-played first so stale tracks resurface.
    """
    if limit <= 0:
        return []
    at = time.time() if now is None else now
    heard = heard or {}

    def last_heard(entry: Any) -> float:
        hit = heard.get(entry.track_id)
        if hit is not None:
            return hit
        return getattr(entry, "last_played", 0.0) or 0.0

    def artist_of(entry: Any) -> str:
        return getattr(entry, "artist", "") or ""

    fresh = [entry for entry in entries
             if at - last_heard(entry) >= MIX_EXCLUDE_HEARD_S]
    fresh.sort(key=lambda entry: (last_heard(entry), entry.track_id))
    picked: list[Any] = []
    recent_artists: collections.deque[str] = collections.deque(maxlen=3)
    seed_window: collections.deque[bool] = collections.deque(maxlen=4)
    for entry in fresh:
        if len(picked) >= limit:
            break
        artist = artist_of(entry)
        if artist and artist in recent_artists:
            continue
        is_seed = bool(seed_artist) and artist == seed_artist
        if is_seed and sum(1 for flag in seed_window if flag) >= 1 \
                and len(seed_window) == 4:
            continue
        picked.append(entry)
        if artist:
            recent_artists.append(artist)
        seed_window.append(is_seed)
    return picked


class RefusalTracker:
    """1-hour cool-down after 3 refusals within 15 minutes."""

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.time
        self._hits: collections.deque[float] = collections.deque()
        self._cooldown_until = 0.0

    def record_refusal(self, at: float | None = None) -> None:
        moment = self._clock() if at is None else at
        cutoff = moment - REFUSAL_WINDOW_S
        while self._hits and self._hits[0] < cutoff:
            self._hits.popleft()
        self._hits.append(moment)
        if len(self._hits) >= REFUSAL_THRESHOLD:
            self._cooldown_until = moment + REFUSAL_COOLDOWN_S

    def in_cooldown(self, at: float | None = None) -> bool:
        return (self._clock() if at is None else at) < self._cooldown_until

    @property
    def cooldown_until(self) -> float:
        return self._cooldown_until


class DailyCap:
    """At most 200 background downloads per rolling 24 hours."""

    def __init__(self, limit: int = MAX_DAILY_DOWNLOADS,
                 clock: Callable[[], float] | None = None) -> None:
        self._limit = int(limit)
        self._clock = clock or time.time
        self._done: collections.deque[float] = collections.deque()

    def record_done(self, at: float | None = None) -> None:
        self._done.append(self._clock() if at is None else at)

    def count(self, at: float | None = None) -> int:
        moment = self._clock() if at is None else at
        cutoff = moment - DAY_S
        while self._done and self._done[0] < cutoff:
            self._done.popleft()
        return len(self._done)

    def allowed(self, at: float | None = None) -> bool:
        return self.count(at) < self._limit


class DownloadGate:
    """Playback-aware pacing for background downloads.

    Pauses while the player buffers and for 30 s after any tap or song
    start; while music plays, downloads stay 20 s apart; idle means
    full speed. Times come from the injected clock.
    """

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._last_interaction = float("-inf")
        self._last_finished = float("-inf")
        self._playing = False
        self._buffering = False

    def notify_interaction(self, at: float | None = None) -> None:
        with self._lock:
            self._last_interaction = self._clock() if at is None else at

    def notify_song_started(self, at: float | None = None) -> None:
        self.notify_interaction(at)

    def notify_playback(self, playing: bool, buffering: bool) -> None:
        with self._lock:
            self._playing = bool(playing)
            self._buffering = bool(buffering)

    def notify_finished(self, at: float | None = None) -> None:
        with self._lock:
            self._last_finished = self._clock() if at is None else at

    def block_reason(self, at: float | None = None) -> str | None:
        moment = self._clock() if at is None else at
        with self._lock:
            buffering = self._buffering
            playing = self._playing
            quiet_left = (self._last_interaction + GATE_INTERACTION_QUIET_S
                          - moment)
            gap_left = self._last_finished + GATE_PLAYING_GAP_S - moment
        if buffering:
            return "buffering"
        if quiet_left > 0:
            return "recent-tap"
        if playing and gap_left > 0:
            return "gap"
        return None

    def can_start(self, at: float | None = None) -> bool:
        return self.block_reason(at) is None


class PinBoard:
    """Which tracks stay pinned, by owner (a track, album or playlist)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owners: dict[str, set[str]] = {}

    @staticmethod
    def _owner(kind: str, item_id: str) -> str:
        return f"{kind}:{item_id}"

    def pin(self, owner: str, track_ids: list[str]) -> None:
        with self._lock:
            for track_id in track_ids:
                self._owners.setdefault(track_id, set()).add(owner)

    def unpin(self, owner: str) -> None:
        with self._lock:
            for track_id in [tid for tid, owners in self._owners.items()
                             if owner in owners]:
                owners = self._owners[track_id]
                owners.discard(owner)
                if not owners:
                    del self._owners[track_id]

    def pin_track(self, track_id: str) -> None:
        self.pin(self._owner("track", track_id), [track_id])

    def unpin_track(self, track_id: str) -> None:
        self.unpin(self._owner("track", track_id))

    def pin_album(self, album_id: str, track_ids: list[str]) -> None:
        self.pin(self._owner("album", album_id), track_ids)

    def unpin_album(self, album_id: str) -> None:
        self.unpin(self._owner("album", album_id))

    def pin_playlist(self, playlist_id: str,
                     track_ids: list[str]) -> None:
        self.pin(self._owner("playlist", playlist_id), track_ids)

    def unpin_playlist(self, playlist_id: str) -> None:
        self.unpin(self._owner("playlist", playlist_id))

    def is_pinned(self, track_id: str) -> bool:
        with self._lock:
            return track_id in self._owners

    def pinned_ids(self) -> set[str]:
        with self._lock:
            return set(self._owners)

    def owners_of(self, track_id: str) -> set[str]:
        with self._lock:
            return set(self._owners.get(track_id, ()))


@dataclass
class QueuedTrack:
    track_id: str
    engine: str = ""
    title: str = ""
    artist: str = ""
    state: str = "queued"
    not_before: float = 0.0
    attempts: int = 0


class OfflineTracker:
    """Offline mode after 3 consecutive network failures.

    One success clears the streak and leaves offline mode; the UI may
    also enter or leave it directly.
    """

    def __init__(self,
                 threshold: int = OFFLINE_FAILURE_THRESHOLD) -> None:
        self._threshold = max(1, int(threshold))
        self._lock = threading.Lock()
        self._consecutive = 0
        self._offline = False

    @property
    def offline(self) -> bool:
        with self._lock:
            return self._offline

    @property
    def consecutive_failures(self) -> int:
        with self._lock:
            return self._consecutive

    def record_success(self) -> None:
        with self._lock:
            self._consecutive = 0
            self._offline = False

    def record_network_failure(self) -> bool:
        with self._lock:
            self._consecutive += 1
            if self._consecutive >= self._threshold:
                self._offline = True
            return self._offline

    def enter(self) -> None:
        with self._lock:
            self._offline = True

    def exit(self) -> None:
        with self._lock:
            self._offline = False
            self._consecutive = 0


class DownloadQueue:
    """FIFO of tracks to fill into the proxy cache.

    Wi-Fi-only by default: on a metered link new downloads wait unless
    the switch is off. Gone tracks are dropped for good, refused ones
    wait 24 h, network failures count toward offline mode, and states
    that pause playback leave the queue untouched.
    """

    def __init__(self, clock: Callable[[], float] | None = None,
                 wifi_only: bool = True) -> None:
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._order: collections.deque[str] = collections.deque()
        self._jobs: dict[str, QueuedTrack] = {}
        self._wifi_only = bool(wifi_only)
        self.pins = PinBoard()
        self.gate = DownloadGate(self._clock)
        self.refusals = RefusalTracker(self._clock)
        self.daily = DailyCap(clock=self._clock)
        self.offline = OfflineTracker()

    @property
    def wifi_only(self) -> bool:
        return self._wifi_only

    def set_wifi_only(self, wifi_only: bool) -> None:
        self._wifi_only = bool(wifi_only)

    def enqueue(self, tracks: list[Track | str],
                engine: str = "") -> int:
        """Queue tracks nearest-first; returns how many are now pending."""
        added = 0
        with self._lock:
            for track in tracks:
                if isinstance(track, Track):
                    track_id, title = track.id, track.title
                    artist = track.artist_line
                else:
                    track_id, title, artist = str(track), "", ""
                job = self._jobs.get(track_id)
                if job is None:
                    job = QueuedTrack(track_id=track_id, engine=engine,
                                      title=title or track_id, artist=artist)
                    self._jobs[track_id] = job
                    self._order.append(track_id)
                if job.state in ("gone", "done"):
                    job.state = "queued"
                    job.not_before = 0.0
                    if track_id not in self._order:
                        self._order.append(track_id)
                if job.state == "queued":
                    added += 1
        return added

    def drop(self, track_id: str) -> None:
        with self._lock:
            self._jobs.pop(track_id, None)
            try:
                self._order.remove(track_id)
            except ValueError:
                pass

    def pending(self) -> list[QueuedTrack]:
        with self._lock:
            return [self._jobs[tid] for tid in self._order
                    if self._jobs[tid].state == "queued"]

    def check_gates(self, *, online: bool, metered: bool,
                    at: float | None = None) -> str:
        """Why a download may start now: ready, offline, cooldown,
        daily-cap, metered, a gate reason, or empty. Never mutates."""
        moment = self._clock() if at is None else at
        if self.offline.offline or not online:
            return "offline"
        if self.refusals.in_cooldown(moment):
            return "cooldown"
        if not self.daily.allowed(moment):
            return "daily-cap"
        if metered and self._wifi_only:
            return "metered"
        blocked = self.gate.block_reason(moment)
        if blocked is not None:
            return blocked
        with self._lock:
            if any(job.state == "active" for job in self._jobs.values()):
                return "empty"
            if any(job.state == "queued" and job.not_before <= moment
                   for job in self._jobs.values()):
                return "ready"
        return "empty"

    def next_ready(self, *, online: bool, metered: bool,
                   at: float | None = None) -> tuple[str | None, str]:
        """Next downloadable id, or (None, reason) from check_gates."""
        moment = self._clock() if at is None else at
        reason = self.check_gates(online=online, metered=metered, at=moment)
        if reason != "ready":
            return None, reason
        with self._lock:
            for track_id in list(self._order):
                job = self._jobs.get(track_id)
                if job is None or job.state in ("gone", "done"):
                    continue
                if job.state == "queued" and job.not_before <= moment:
                    job.state = "active"
                    job.attempts += 1
                    return track_id, "ready"
            return None, "empty"

    def mark_done(self, track_id: str,
                  at: float | None = None) -> None:
        moment = self._clock() if at is None else at
        with self._lock:
            job = self._jobs.get(track_id)
            if job is not None:
                job.state = "done"
                try:
                    self._order.remove(track_id)
                except ValueError:
                    pass
        self.daily.record_done(moment)
        self.gate.notify_finished(moment)
        self.offline.record_success()

    def mark_failed(self, track_id: str, kind: ErrorKind,
                    at: float | None = None) -> str:
        """Fold an engine failure into the queue; returns its handling."""
        moment = self._clock() if at is None else at
        handling = classify_failure(kind)
        with self._lock:
            job = self._jobs.get(track_id)
            if job is not None and job.state == "active":
                job.state = "queued"
            if job is not None and handling == "gone":
                job.state = "gone"
                try:
                    self._order.remove(track_id)
                except ValueError:
                    pass
            if job is not None and handling == "refusal":
                job.state = "queued"
                job.not_before = moment + REFUSED_RETRY_S
        if handling == "refusal":
            self.refusals.record_refusal(moment)
        elif handling == "network":
            self.offline.record_network_failure()
        elif handling in ("gone", "transient"):
            pass
        return handling


@dataclass(frozen=True)
class OfflineSnapshot:
    offline: bool = False
    online: bool = True
    metered: bool = False
    pending: int = 0
    downloaded_today: int = 0
    can_download: bool = True
    can_download_reason: str = "ready"
    prefetch_depth: int = PREFETCH_UNMETERED
    text: str = ""


class OfflineState:
    """The one object the UI asks about offline life."""

    def __init__(self, queue: DownloadQueue,
                 metered_fn: Callable[[], bool] | None = None,
                 online_fn: Callable[[], bool] | None = None,
                 prefetch_fn: Callable[[], int] | None = None) -> None:
        self._queue = queue
        self._metered_fn = metered_fn or (lambda: False)
        self._online_fn = online_fn or (lambda: True)
        self._prefetch_fn = prefetch_fn

    def snapshot(self, at: float | None = None) -> OfflineSnapshot:
        online = bool(self._online_fn())
        metered = bool(self._metered_fn())
        reason = self._queue.check_gates(online=online, metered=metered,
                                         at=at)
        pending = len(self._queue.pending())
        today = self._queue.daily.count(at)
        depth = (self._prefetch_fn() if self._prefetch_fn is not None
                 else (PREFETCH_METERED if metered else PREFETCH_UNMETERED))
        offline = self._queue.offline.offline or not online
        return OfflineSnapshot(
            offline=offline, online=online, metered=metered,
            pending=pending, downloaded_today=today,
            can_download=reason == "ready",
            can_download_reason=reason, prefetch_depth=depth,
            text=self._text(offline, pending, reason))

    @staticmethod
    def _text(offline: bool, pending: int, reason: str) -> str:
        if offline:
            return "You're offline. Downloaded music still plays."
        if reason == "metered":
            return (f"{pending} waiting for Wi-Fi."
                    if pending else "On mobile data. Downloads wait.")
        if reason == "cooldown":
            return "Downloads paused for a while. Trying again soon."
        if reason == "daily-cap":
            return "Today's download limit is reached. More tomorrow."
        if reason in ("buffering", "recent-tap", "gap"):
            return "Downloads resume when the music settles."
        if pending:
            return f"Downloading {pending}."
        return "Everything on this phone is up to date."


def fill_via_proxy(proxy: Any, track_id: str,
                   resolve: Callable[[], Any],
                   timeout_s: float = 60.0) -> int:
    """Pull one whole track through the proxy into its cache.

    The proxy tees the bytes and resumes any ``.part`` left by a drop,
    so a failure here just raises and the next call carries on. Only
    the track id ever reaches the error; headers and addresses stay
    out of it.
    """
    try:
        url = proxy.url_for(track_id, resolve)
    except (OSError, RuntimeError) as exc:
        raise EngineError(ErrorKind.OFFLINE, track_id) from exc
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request,
                                    timeout=timeout_s) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise EngineError(ErrorKind.OFFLINE, track_id) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise EngineError(ErrorKind.OFFLINE, track_id) from exc
    except Exception as exc:
        raise EngineError(ErrorKind.OTHER, track_id) from exc
    return len(body)


__all__ = ["MAX_DAILY_DOWNLOADS", "REFUSAL_WINDOW_S", "REFUSAL_THRESHOLD",
           "REFUSAL_COOLDOWN_S", "REFUSED_RETRY_S", "PREFETCH_UNMETERED",
           "PREFETCH_METERED", "GATE_INTERACTION_QUIET_S",
           "GATE_PLAYING_GAP_S", "OFFLINE_FAILURE_THRESHOLD", "MIX_LIMIT",
           "MIX_EXCLUDE_HEARD_S", "classify_failure", "FakeClock",
           "plan_prefetch", "build_offline_mix", "RefusalTracker",
           "DailyCap", "DownloadGate", "PinBoard", "QueuedTrack",
           "OfflineTracker", "DownloadQueue", "OfflineSnapshot",
           "OfflineState", "fill_via_proxy"]

