"""Screen-time / usage statistics: data collection layer (no UI).

Counts foreground seconds per app_id while the screen is on and unlocked,
plus unlocks per day and notifications per app (counts only, no content).
The future UI reads back through the query API below.

Wiring (see main.py): the tracker subscribes to a ToplevelManager via
``on_activated_changed``, chains DisplayManager's ``on_blank_changed``
without replacing the existing subscriber, observes lock transitions via
``set_locked``, and counts notifications via ``note_notification``.

Writes are throttled for eMMC wear: state changes only mark days dirty,
and files flush at most every ``FLUSH_INTERVAL_SECS`` (5 minutes) plus on
blank and on lock. Storage is ``~/.local/share/xx-wm/usage/YYYY-MM-DD.json``
(atomic replace, 0600), pruned to ``RETENTION_DAYS`` (35) days. The fsyncs
run on one writer thread, never on the GTK loop.

Durations come from the monotonic clock and only their placement on the
calendar from the wall clock: phones whose RTC boots years behind jump
forward when NTP lands, and that jump is not screen time. One span is
capped at ``MAX_SPAN_SECS``.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import queue
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

FLUSH_INTERVAL_SECS = 300
RETENTION_DAYS = 35
MAX_SPAN_SECS = 12 * 3600


def default_data_dir() -> Path:
    return Path.home() / '.local' / 'share' / 'xx-wm' / 'usage'


def normalize_date(value: datetime.date | datetime.datetime | str) -> datetime.date:
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value))


def _day_of(ts: float) -> datetime.date:
    return datetime.datetime.fromtimestamp(ts).date()


def _next_midnight_ts(ts: float) -> float:
    day_start = datetime.datetime.fromtimestamp(ts).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return (day_start + datetime.timedelta(days=1)).timestamp()


class _DayData:
    def __init__(self) -> None:
        self.apps: dict[str, int] = {}
        self.unlocks = 0
        self.notifications: dict[str, int] = {}

    @classmethod
    def from_dict(cls, raw: object) -> _DayData:
        day = cls()
        if not isinstance(raw, dict):
            return day
        apps = raw.get('apps')
        if isinstance(apps, dict):
            for key, value in apps.items():
                try:
                    day.apps[str(key)] = max(0, int(value))  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    continue
        try:
            day.unlocks = max(0, int(raw.get('unlocks', 0)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            pass
        notifs = raw.get('notifications')
        if isinstance(notifs, dict):
            for key, value in notifs.items():
                try:
                    day.notifications[str(key)] = max(0, int(value))  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    continue
        return day

    def to_dict(self) -> dict:
        return {
            'apps': dict(self.apps),
            'unlocks': self.unlocks,
            'notifications': dict(self.notifications),
        }


class UsageTracker:
    """Foreground-time, unlock and notification counters with day-file storage."""

    def __init__(
        self,
        enabled: bool = True,
        data_dir: str | Path | None = None,
        now=None,
        flush_interval: float = FLUSH_INTERVAL_SECS,
        retention_days: int = RETENTION_DAYS,
        manager=None,
        monotonic=None,
        background_writes: bool = True,
    ) -> None:
        self.enabled = bool(enabled)
        self._data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
        self._now = now if now is not None else time.time
        if monotonic is None:
            monotonic = now if now is not None else time.monotonic
        self._mono = monotonic
        self._background = bool(background_writes)
        self._queue: queue.Queue | None = None
        self._segment_mono: float | None = None
        self._flush_interval = max(0.0, float(flush_interval))
        self._retention_days = max(1, int(retention_days))
        self._current_app: str | None = None
        self._blanked = False
        self._locked = False
        self._days: dict[datetime.date, _DayData] = {}
        self._dirty: set[datetime.date] = set()
        # Anchor the throttle at construction so event-driven flushes never
        # write per event: the first periodic write happens a full interval
        # after startup. Blank/lock bypass via force=True.
        self._last_flush: float = self._now()
        if manager is not None:
            self.attach_manager(manager)
        if self.enabled:
            self._submit([], self._now())

    def attach_manager(self, manager) -> None:
        """Subscribe to a ToplevelManager's activation changes."""
        manager.on_activated_changed(self.on_activated_changed)
        try:
            current = manager.active_app_id
        except Exception:  # noqa: BLE001 - older managers lack the property
            current = None
        if current:
            self._current_app = str(current)
            self._open_segment(self._now())

    def _countable(self) -> bool:
        return (
            self.enabled
            and not self._blanked
            and not self._locked
            and self._current_app is not None
        )

    def _at(self, now: float | None) -> float:
        return now if now is not None else self._now()

    def _mono_at(self, now: float | None) -> float:
        # An explicit stamp (tests, replays) stands for both clocks.
        return now if now is not None else self._mono()

    # -- event inputs ----------------------------------------------------

    def on_activated_changed(self, app_id: str | None, now: float | None = None) -> None:
        """Foreground app changed; None means no app toplevel is active."""
        ts = self._at(now)
        if not self.enabled:
            self._current_app = str(app_id) if app_id else None
            self._segment_mono = None
            return
        self._close_segment(ts, now)
        self._current_app = str(app_id) if app_id else None
        self._open_segment(ts, now)
        self._maybe_flush(ts)

    def on_blank_changed(self, blanked: bool, now: float | None = None) -> None:
        """Display blank state changed. Chain from DisplayManager."""
        ts = self._at(now)
        blanked = bool(blanked)
        self._blanked = blanked
        if not self.enabled:
            return
        self._close_segment(ts, now)
        self._open_segment(ts, now)
        if blanked:
            self.flush(now=ts, force=True)
        else:
            self._maybe_flush(ts)

    def set_locked(self, locked: bool, now: float | None = None) -> None:
        """Lock state changed. Unlocks count on locked -> unlocked."""
        ts = self._at(now)
        locked = bool(locked)
        was = self._locked
        self._locked = locked
        if not self.enabled:
            return
        self._close_segment(ts, now)
        if locked and not was:
            self._open_segment(ts, now)
            self.flush(now=ts, force=True)
        elif was and not locked:
            day = _day_of(ts)
            self._record(day).unlocks += 1
            self._dirty.add(day)
            self._open_segment(ts, now)
            self._maybe_flush(ts)
        else:
            self._open_segment(ts, now)

    def note_notification(self, app_id: str, now: float | None = None) -> None:
        """Count one delivered notification for app_id (id only, no content)."""
        if not self.enabled or not app_id:
            return
        ts = self._at(now)
        day = _day_of(ts)
        record = self._record(day)
        record.notifications[str(app_id)] = record.notifications.get(str(app_id), 0) + 1
        self._dirty.add(day)
        self._maybe_flush(ts)

    # -- accounting ------------------------------------------------------

    def _close_segment(self, ts: float, now: float | None = None) -> None:
        start = self._segment_mono
        app = self._current_app
        self._segment_mono = None
        if start is None or app is None:
            return
        secs = min(self._mono_at(now) - start, MAX_SPAN_SECS)
        if secs <= 0:
            return
        self._add_span(app, ts - secs, ts)

    def _open_segment(self, ts: float, now: float | None = None) -> None:
        if self._countable() and self._segment_mono is None:
            self._segment_mono = self._mono_at(now)

    def _add_span(self, app_id: str, start: float, end: float) -> None:
        cursor = start
        while cursor < end:
            day = _day_of(cursor)
            chunk_end = min(end, _next_midnight_ts(cursor))
            if chunk_end <= cursor:
                chunk_end = end
            secs = int(round(chunk_end - cursor))
            if secs > 0:
                record = self._record(day)
                record.apps[app_id] = record.apps.get(app_id, 0) + secs
                self._dirty.add(day)
            cursor = chunk_end

    def _record(self, day: datetime.date) -> _DayData:
        record = self._days.get(day)
        if record is None:
            record = self._load_day(day)
            self._days[day] = record
        return record

    # -- queries (the future UI reads these) ------------------------------

    def daily_totals(
        self, day: datetime.date | datetime.datetime | str,
        now: float | None = None,
    ) -> dict[str, int]:
        """Foreground seconds per app_id for one local day."""
        if not self.enabled:
            return {}
        ts = self._at(now)
        self._close_segment(ts, now)
        self._open_segment(ts, now)
        return dict(self._record(normalize_date(day)).apps)

    def week(
        self, day: datetime.date | datetime.datetime | str,
        now: float | None = None,
    ) -> list[tuple[datetime.date, int]]:
        """Seven (date, total_seconds) pairs ending on day, inclusive."""
        end = normalize_date(day)
        if not self.enabled:
            return [(end - datetime.timedelta(days=i), 0) for i in range(6, -1, -1)]
        ts = self._at(now)
        self._close_segment(ts, now)
        self._open_segment(ts, now)
        out: list[tuple[datetime.date, int]] = []
        for i in range(6, -1, -1):
            current = end - datetime.timedelta(days=i)
            out.append((current, sum(self._record(current).apps.values())))
        return out

    def unlocks(
        self, day: datetime.date | datetime.datetime | str,
        now: float | None = None,
    ) -> int:
        if not self.enabled:
            return 0
        ts = self._at(now)
        self._close_segment(ts, now)
        self._open_segment(ts, now)
        return self._record(normalize_date(day)).unlocks

    def notifications(
        self, day: datetime.date | datetime.datetime | str,
        now: float | None = None,
    ) -> dict[str, int]:
        """Delivered-notification counts per app_id for one local day."""
        if not self.enabled:
            return {}
        ts = self._at(now)
        self._close_segment(ts, now)
        self._open_segment(ts, now)
        return dict(self._record(normalize_date(day)).notifications)

    def flush(self, now: float | None = None, force: bool = False) -> bool:
        """Write dirty days. Throttled unless force (blank/lock paths)."""
        if not self.enabled:
            return False
        ts = self._at(now)
        self._close_segment(ts, now)
        self._open_segment(ts, now)
        if not self._dirty:
            return False
        if not force and abs(ts - self._last_flush) < self._flush_interval:
            return False
        writes = []
        for day in sorted(self._dirty):
            record = self._days.get(day)
            if record is not None:
                writes.append((day, record.to_dict()))
        self._dirty.clear()
        self._submit(writes, ts)
        self._last_flush = ts
        return True

    def _submit(self, writes: list, ts: float) -> None:
        if not self._background:
            self._write_and_prune(writes, ts)
            return
        if self._queue is None:
            self._queue = queue.Queue()
            threading.Thread(target=self._writer, name='usage-stats-writer',
                             daemon=True).start()
        self._queue.put((writes, ts))

    def _writer(self) -> None:
        q = self._queue
        while True:
            writes, ts = q.get()
            try:
                self._write_and_prune(writes, ts)
            except Exception as exc:  # noqa: BLE001 - keep the writer alive
                log.warning('usage stats write failed: %s', exc)
            finally:
                q.task_done()

    def wait_writes(self) -> None:
        """Block until queued writes landed (shutdown, tests)."""
        if self._queue is not None:
            self._queue.join()

    def _write_and_prune(self, writes: list, ts: float) -> None:
        for day, payload in writes:
            self._write_day(day, payload)
        try:
            self._prune(ts)
        except Exception as exc:  # noqa: BLE001 - pruning is best-effort
            log.warning('usage stats prune failed: %s', exc)

    def _maybe_flush(self, ts: float) -> None:
        if self._dirty:
            self.flush(now=ts)

    # -- storage ---------------------------------------------------------

    def _file_for(self, day: datetime.date) -> Path:
        return self._data_dir / (day.isoformat() + '.json')

    def _load_day(self, day: datetime.date) -> _DayData:
        try:
            raw = json.loads(self._file_for(day).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return _DayData()
        return _DayData.from_dict(raw)

    def _write_day(self, day: datetime.date, record: dict) -> None:
        path = self._file_for(day)
        payload = json.dumps(record, indent=2, sort_keys=True) + '\n'
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_name(path.name + '.tmp')
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                    fh.write(payload)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp_path, path)
            finally:
                tmp_path.unlink(missing_ok=True)
            os.chmod(path, 0o600)
        except OSError as exc:
            log.warning('usage stats write failed for %s: %s', day, exc)

    def _prune(self, ts: float) -> None:
        today = _day_of(ts)
        try:
            names = [p.name for p in self._data_dir.iterdir()]
        except OSError:
            return
        for name in names:
            if not name.endswith('.json') or name.endswith('.tmp'):
                continue
            try:
                day = datetime.date.fromisoformat(name[:-5])
            except ValueError:
                continue
            if (today - day).days >= self._retention_days:
                try:
                    (self._data_dir / name).unlink(missing_ok=True)
                except OSError as exc:
                    log.warning('usage stats prune failed for %s: %s', name, exc)
