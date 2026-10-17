"""Playback rules: skip, sleep with fade, error policy, radio, offline mix.

Pure logic over injectable clocks so tests drive it with a fake clock.
The player service applies these decisions to the queue and playbin.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from xx_music.engine import EngineError, ErrorKind, Track

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
                 heard_ids: set[str], disliked_ids: set[str]) -> list[Track]:
    """Drop queued, recently heard and disliked songs from radio results."""
    return [t for t in tracks if t.id not in queued_ids
            and t.id not in heard_ids and t.id not in disliked_ids]


def artist_key(track: Track) -> str:
    if track.artists:
        return track.artists[0].id or track.artists[0].name
    return f"track:{track.id}"


def build_offline_mix(tracks: list[Track], seed: Track | None,
                      heard_ids: set[str], limit: int = OFFLINE_MIX_LIMIT
                      ) -> list[Track]:
    """Up to ``limit`` on-phone songs: no heard songs, the seed artist at
    most 1 in 4, no artist again within 3 places."""
    seed_key = artist_key(seed) if seed is not None else ""
    max_seed = max(1, limit // 4)
    picked: list[Track] = []
    seed_count = 0
    recent: list[str] = []
    for track in tracks:
        if len(picked) >= limit:
            break
        if track.id in heard_ids:
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
