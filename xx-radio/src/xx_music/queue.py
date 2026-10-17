"""The ordered playback queue: tracks, position, shuffle/repeat, persistence.

Shuffle physically reorders the list with the current track anchored, and
is reset by every new queue. Repeat one repeats the current track, repeat
all wraps at the ends, otherwise running past an end stops. The queue
itself never plays anything; the player service drives it.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from xx_music.engine import Track
from xx_music.policy import RESTART_PREVIOUS_S

REPEAT_NONE = "none"
REPEAT_ALL = "all"
REPEAT_ONE = "one"
REPEAT_MODES = (REPEAT_NONE, REPEAT_ALL, REPEAT_ONE)


class Queue:
    def __init__(self) -> None:
        self.tracks: list[Track] = []
        self.index: int = -1
        self.shuffle: bool = False
        self.repeat: str = REPEAT_NONE
        self.origin: dict = {}
        self.version: int = 0

    # -- properties -------------------------------------------------------
    @property
    def empty(self) -> bool:
        return not self.tracks

    def current(self) -> Track | None:
        if 0 <= self.index < len(self.tracks):
            return self.tracks[self.index]
        return None

    def remaining(self) -> int:
        """Songs after the current one."""
        if self.index < 0:
            return len(self.tracks)
        return max(0, len(self.tracks) - self.index - 1)

    def ids(self) -> set[str]:
        return {track.id for track in self.tracks}

    # -- mutation ---------------------------------------------------------
    def replace(self, tracks: list[Track], start: int = 0,
                origin: dict | None = None) -> None:
        self.tracks = list(tracks)
        self.shuffle = False
        self.origin = dict(origin or {})
        self.index = min(max(0, start), len(self.tracks) - 1) \
            if self.tracks else -1
        self.version += 1

    def enqueue(self, tracks: list[Track], play_next: bool = False) -> int:
        """Append tracks, skipping ids already queued. Returns added count."""
        fresh = [t for t in tracks if t.id not in self.ids()]
        if not fresh:
            return 0
        if play_next and 0 <= self.index < len(self.tracks):
            head = self.tracks[:self.index + 1]
            tail = self.tracks[self.index + 1:]
            self.tracks = [*head, *fresh, *tail]
        else:
            self.tracks.extend(fresh)
            if self.index < 0:
                self.index = 0
        self.version += 1
        return len(fresh)

    def play_at(self, index: int) -> Track:
        if not 0 <= index < len(self.tracks):
            raise IndexError(f"queue index {index} out of range")
        self.index = index
        self.version += 1
        return self.tracks[index]

    def remove(self, index: int) -> Track:
        removed = self.tracks.pop(index)
        if index < self.index:
            self.index -= 1
        elif index == self.index:
            if self.index >= len(self.tracks):
                self.index = len(self.tracks) - 1
        if not self.tracks:
            self.index = -1
        self.version += 1
        return removed

    def remove_id(self, track_id: str) -> int:
        """Drop every queued copy of one id (a dislike leaves the queue)."""
        kept = [t for t in self.tracks if t.id != track_id]
        dropped = len(self.tracks) - len(kept)
        if dropped:
            current_id = self.current().id if self.current() else None
            self.tracks = kept
            if not kept:
                self.index = -1
            elif current_id is not None and current_id != track_id:
                self.index = next(
                    i for i, t in enumerate(kept) if t.id == current_id)
            elif current_id == track_id:
                self.index = min(self.index, len(kept) - 1)
            self.version += 1
        return dropped

    def move(self, source: int, dest: int) -> None:
        current = self.current()
        track = self.tracks.pop(source)
        self.tracks.insert(dest, track)
        if current is not None:
            self.index = self.tracks.index(current)
        self.version += 1

    def set_shuffle(self, on: bool) -> None:
        on = bool(on)
        if on == self.shuffle:
            return
        self.shuffle = on
        if on and len(self.tracks) > 1:
            current = self.current()
            rest = [t for t in self.tracks if t is not current]
            random.shuffle(rest)
            if current is None:
                self.tracks = rest
                self.index = 0 if rest else -1
            else:
                self.tracks = [*rest[:self.index], current,
                               *rest[self.index:]]
                self.index = self.tracks.index(current)
        self.version += 1

    def set_repeat(self, mode: str) -> None:
        if mode not in REPEAT_MODES:
            raise ValueError(f"repeat must be one of {REPEAT_MODES}")
        if mode != self.repeat:
            self.repeat = mode
            self.version += 1

    # -- sequencing ---------------------------------------------------------
    def next_index(self) -> int | None:
        """Where playback goes after the current song, None to stop."""
        if not self.tracks or self.index < 0:
            return None
        if self.repeat == REPEAT_ONE:
            return self.index
        if self.index + 1 < len(self.tracks):
            return self.index + 1
        if self.repeat == REPEAT_ALL and self.tracks:
            return 0
        return None

    def previous_index(self, position_s: float) -> int | None:
        """Previous restarts the song past 3 s, else steps back."""
        if not self.tracks or self.index < 0:
            return None
        if position_s > RESTART_PREVIOUS_S or self.index == 0:
            return self.index
        return self.index - 1

    # -- persistence --------------------------------------------------------
    def persistence_key(self) -> tuple:
        current = self.current()
        return (self.version, self.index,
                current.id if current else None, len(self.tracks))

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "index": self.index,
            "shuffle": self.shuffle,
            "repeat": self.repeat,
            "origin": self.origin,
            "tracks": [t.to_dict() for t in self.tracks],
        }

    @classmethod
    def from_dict(cls, raw: dict) -> Queue:
        queue = cls()
        queue.tracks = [Track.from_dict(t) for t in raw.get("tracks") or ()]
        queue.index = int(raw.get("index", -1))
        if not queue.tracks:
            queue.index = -1
        else:
            queue.index = min(max(0, queue.index), len(queue.tracks) - 1)
        queue.shuffle = bool(raw.get("shuffle"))
        repeat = str(raw.get("repeat") or REPEAT_NONE)
        queue.repeat = repeat if repeat in REPEAT_MODES else REPEAT_NONE
        origin = raw.get("origin")
        queue.origin = dict(origin) if isinstance(origin, dict) else {}
        queue.version = int(raw.get("version", 0))
        return queue

    def save(self, path: Path | str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict()), encoding="utf-8")
        tmp.rename(target)

    @classmethod
    def load(cls, path: Path | str) -> Queue:
        try:
            return cls.from_dict(
                json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return cls()
