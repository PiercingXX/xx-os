"""SQLite persistence (port of data/ClockStore.kt).

Layout mirrors the Android store: definitions (alarms, timers) separate
from per-id runtime (scheduled_fire, snoozed_until, ringing) plus a
monotonic id counter over one shared alarm/timer namespace.
"""

from __future__ import annotations

import sqlite3
import threading
import time as _time

from . import ids, paths
from .models import Alarm, TimerItem

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alarms(
  id INTEGER PRIMARY KEY, hour INTEGER, minute INTEGER, days_mask INTEGER,
  label TEXT, enabled INTEGER, vibrate INTEGER, sound_uri TEXT);
CREATE TABLE IF NOT EXISTS timers(
  id INTEGER PRIMARY KEY, duration_ms INTEGER, state TEXT,
  ends_at_ms INTEGER, remaining_ms INTEGER, label TEXT);
CREATE TABLE IF NOT EXISTS runtime(
  id INTEGER PRIMARY KEY, scheduled_fire INTEGER NOT NULL DEFAULT 0,
  snoozed_until INTEGER NOT NULL DEFAULT 0, ringing INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value INTEGER);
"""


def _alarm_from_row(row: tuple) -> Alarm:
    return Alarm(id=int(row[0]), hour=int(row[1]), minute=int(row[2]),
                 days_mask=int(row[3]), label=str(row[4] or ""),
                 enabled=bool(row[5]), vibrate=bool(row[6]),
                 sound_uri=row[7] if row[7] else None)


def _timer_from_row(row: tuple) -> TimerItem:
    return TimerItem(id=int(row[0]), duration_ms=int(row[1]), state=str(row[2]),
                     ends_at_ms=int(row[3]), remaining_ms=int(row[4]),
                     label=str(row[5] or ""))


class Store:
    def __init__(self, path=None) -> None:
        paths.ensure_app_dirs()
        self._path = str(path or paths.db_path())
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self._path, check_same_thread=False)
        try:
            with self._lock:
                self._db.executescript(_SCHEMA)
        except sqlite3.DatabaseError:
            self._db.close()
            self._quarantine()
            self._db = sqlite3.connect(self._path, check_same_thread=False)
            with self._lock:
                self._db.executescript(_SCHEMA)
        self._ids = ids.MonotonicIdSequence(
            load=self._load_next_id,
            store=self._store_next_id,
            floor_of=self._id_floor,
        )

    def _quarantine(self) -> None:
        import time as _clock_time
        from pathlib import Path
        try:
            Path(self._path).rename(
                f"{self._path}.corrupt-{int(_clock_time.time())}")
        except Exception:
            pass

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def next_id(self) -> int:
        return self._ids.next()

    def _load_next_id(self) -> int:
        row = self._db.execute("SELECT value FROM meta WHERE key='next_id'").fetchone()
        return int(row[0]) if row else 0

    def _store_next_id(self, value: int) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('next_id', ?)", (value,))
            self._db.commit()

    def _id_floor(self) -> int:
        with self._lock:
            top_alarm = self._db.execute("SELECT MAX(id) FROM alarms").fetchone()[0] or 0
            top_timer = self._db.execute("SELECT MAX(id) FROM timers").fetchone()[0] or 0
        return max(int(top_alarm), int(top_timer), int(_time.time() * 1000))

    def alarms(self) -> list[Alarm]:
        with self._lock:
            rows = self._db.execute("SELECT id, hour, minute, days_mask, label, enabled, vibrate,"
                                    " sound_uri FROM alarms").fetchall()
        out: list[Alarm] = []
        for row in rows:
            try:
                alarm = _alarm_from_row(row)
                if alarm.hour not in range(24) or alarm.minute not in range(60):
                    continue
                out.append(alarm)
            except Exception:
                continue
        out.sort(key=lambda a: (a.hour, a.minute))
        return out

    def get_alarm(self, alarm_id: int) -> Alarm | None:
        return next((a for a in self.alarms() if a.id == alarm_id), None)

    def save_alarm(self, alarm: Alarm) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO alarms(id, hour, minute, days_mask, label, enabled,"
                " vibrate, sound_uri) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (alarm.id, alarm.hour, alarm.minute, alarm.days_mask, alarm.label,
                 int(alarm.enabled), int(alarm.vibrate), alarm.sound_uri))
            self._db.commit()

    def delete_alarm(self, alarm_id: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM alarms WHERE id=?", (alarm_id,))
            self._db.commit()
        self.clear_runtime(alarm_id)

    def timers(self) -> list[TimerItem]:
        with self._lock:
            rows = self._db.execute("SELECT id, duration_ms, state, ends_at_ms, remaining_ms,"
                                    " label FROM timers").fetchall()
        out: list[TimerItem] = []
        for row in rows:
            try:
                timer = _timer_from_row(row)
                if timer.state not in ("IDLE", "RUNNING", "PAUSED", "FINISHED"):
                    continue
                out.append(timer)
            except Exception:
                continue
        return out

    def get_timer(self, timer_id: int) -> TimerItem | None:
        return next((t for t in self.timers() if t.id == timer_id), None)

    def save_timer(self, timer: TimerItem) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO timers(id, duration_ms, state, ends_at_ms,"
                " remaining_ms, label) VALUES(?, ?, ?, ?, ?, ?)",
                (timer.id, timer.duration_ms, timer.state, timer.ends_at_ms,
                 timer.remaining_ms, timer.label))
            self._db.commit()

    def delete_timer(self, timer_id: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM timers WHERE id=?", (timer_id,))
            self._db.commit()
        self.clear_runtime(timer_id)

    def _runtime(self, entry_id: int) -> tuple[int, int, bool]:
        with self._lock:
            row = self._db.execute("SELECT scheduled_fire, snoozed_until, ringing FROM runtime"
                                   " WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            return (0, 0, False)
        return (int(row[0]), int(row[1]), bool(row[2]))

    def _mutate_runtime(self, entry_id: int, scheduled=None, snoozed=None, ringing=None) -> None:
        current = self._runtime(entry_id)
        scheduled = current[0] if scheduled is None else scheduled
        snoozed = current[1] if snoozed is None else snoozed
        ringing = current[2] if ringing is None else ringing
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO runtime(id, scheduled_fire, snoozed_until,"
                             " ringing) VALUES(?, ?, ?, ?)",
                             (entry_id, scheduled, snoozed, int(ringing)))
            self._db.commit()

    def scheduled_fire(self, entry_id: int) -> int:
        return self._runtime(entry_id)[0]

    def set_scheduled_fire(self, entry_id: int, at_ms: int) -> None:
        self._mutate_runtime(entry_id, scheduled=at_ms)

    def clear_scheduled_fire(self, entry_id: int) -> None:
        self._mutate_runtime(entry_id, scheduled=0)

    def snoozed_until(self, entry_id: int) -> int:
        return self._runtime(entry_id)[1]

    def set_snoozed_until(self, entry_id: int, until_ms: int) -> None:
        self._mutate_runtime(entry_id, snoozed=until_ms)

    def is_ringing(self, entry_id: int) -> bool:
        return self._runtime(entry_id)[2]

    def set_ringing(self, entry_id: int, ringing: bool) -> None:
        self._mutate_runtime(entry_id, ringing=ringing)

    def ringing_id(self) -> int | None:
        with self._lock:
            rows = self._db.execute("SELECT id FROM runtime WHERE ringing=1").fetchall()
        if not rows:
            return None
        return max(int(row[0]) for row in rows)

    def clear_runtime(self, entry_id: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM runtime WHERE id=?", (entry_id,))
            self._db.commit()
