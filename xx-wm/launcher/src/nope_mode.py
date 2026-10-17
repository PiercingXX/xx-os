"""
Nope-Mode — Focus-mode blocking built into the shell (design.md "Focus Mode"
parity, ported from the Android Nope-Mode app in ref/Nope-Mode).

Chosen apps (desktop ids) go silent and won't open while Nope is active.
Active is derived, never accumulated: master switch AND (manual ForceOn,
a timed ForceOn, or any schedule), minus a bounded break. A missed or
duplicated wakeup cannot corrupt state because every query re-derives it.

Android mapping (behavior, not source):
- Override None / ForceOn / Break and the truth table come from the app's
  NopeController; the midnight-crossing schedule rule (day = start day)
  comes from its ScheduleEvaluator and is shared here with the DnD
  matcher (common/xx_phone), so one rule serves DnD, Focus and Nope.
- Leaving early is always bounded: Take a break offers 5 / 15 / 30
  minutes and auto-resumes. Minimum 30 minutes between breaks and 3
  breaks per window (Android defaults) stop chained breaks; friction
  settings lock while active.
- Silent means dropped, not held: the fallback listener cancels the
  notification outright. Focus holds and releases in a batch; Nope
  drops, and this module documents that difference.
- Already running: Android suspend hides the app and drops it from
  recents without killing it. The compositor has no suspend, so turning
  Nope on closes open toplevels of chosen apps instead (stronger, and
  honest about it). Reopening is blocked at the launch gate.
- Calls have no Nope allow-list of their own: starred contacts and
  repeat callers already ring through via DnD, which Nope never touches.
"""
from __future__ import annotations

import time as _time
from datetime import datetime, timedelta

from config import ShellConfig
from dnd import schedule_matches

BREAK_MINUTES = (5, 15, 30)
MAX_BREAK_MINUTES = 30
DEFAULT_MIN_INTERVAL_MINUTES = 30
DEFAULT_BUDGET_PER_WINDOW = 3

OVERRIDE_NONE = 'none'
OVERRIDE_FORCE = 'force'
OVERRIDE_BREAK = 'break'

_NOPE_BANNER_TEXT = 'Nope.'


def _norm(app_id: object) -> str:
    text = str(app_id or '')
    return (text[:-8] if text.endswith('.desktop') else text).casefold()


def _until_str(when_ts: float) -> str:
    try:
        return datetime.fromtimestamp(float(when_ts)).strftime('%H:%M')
    except (TypeError, ValueError, OverflowError, OSError):
        return ''


class NopeState:
    def __init__(self, config: ShellConfig) -> None:
        self._config = config

    @property
    def enabled(self) -> bool:
        return bool(self._config.data.get('nope_enabled', False))

    def set_enabled(self, enabled: bool) -> None:
        self._config.data['nope_enabled'] = bool(enabled)
        if not enabled:
            self._config.data['nope_override'] = OVERRIDE_NONE
            self._config.data['nope_override_until'] = 0.0
        self._config.save()

    def toggle(self, now: datetime | None = None,
               now_ts: float | None = None) -> bool:
        """Shade tap: flip ForceOn like the Android tile (derived toggle)."""
        now = now or datetime.now()
        now_ts = _time.time() if now_ts is None else now_ts
        if self.is_active(now, now_ts):
            if self.schedule_active(now):
                self.set_enabled(False)
            else:
                self._config.data['nope_enabled'] = True
                self.clear_override()
            return self.is_active(now, now_ts)
        self._config.data['nope_enabled'] = True
        self._config.data['nope_override'] = OVERRIDE_FORCE
        self._config.data['nope_override_until'] = 0.0
        self._config.save()
        return True

    @property
    def nope_apps(self) -> list[str]:
        val = self._config.data.get('nope_apps', [])
        if isinstance(val, list):
            return [str(a) for a in val]
        return []

    def set_app_blocked(self, app_id: str, blocked: bool) -> None:
        apps = [a for a in self.nope_apps if _norm(a) != _norm(app_id)]
        if blocked:
            apps.append(app_id)
        self._config.data['nope_apps'] = apps
        self._config.save()

    def is_nope_app(self, app_id: object) -> bool:
        if not app_id:
            return False
        wanted = _norm(app_id)
        return any(_norm(a) == wanted for a in self.nope_apps)

    @property
    def schedules(self) -> list[dict]:
        val = self._config.data.get('nope_schedules', [])
        return val if isinstance(val, list) else []

    def set_schedules(self, schedules: list[dict]) -> None:
        cleaned: list[dict] = []
        for sched in schedules:
            if not isinstance(sched, dict):
                continue
            days = sched.get('days')
            start = sched.get('start')
            end = sched.get('end')
            if not isinstance(days, list) or not isinstance(start, str) \
                    or not isinstance(end, str):
                continue
            cleaned.append({
                'days': [int(d) for d in days if isinstance(d, int)],
                'start': start,
                'end': end,
            })
        self._config.data['nope_schedules'] = cleaned
        self._config.save()

    @property
    def override(self) -> str:
        raw = self._config.data.get('nope_override', OVERRIDE_NONE)
        return raw if raw in (
            OVERRIDE_NONE, OVERRIDE_FORCE, OVERRIDE_BREAK) else OVERRIDE_NONE

    @property
    def override_until(self) -> float:
        try:
            return max(0.0, float(
                self._config.data.get('nope_override_until', 0.0)))
        except (TypeError, ValueError):
            return 0.0

    def set_force(self, until_ts: float | None = None) -> None:
        self._config.data['nope_enabled'] = True
        self._config.data['nope_override'] = OVERRIDE_FORCE
        self._config.data['nope_override_until'] = (
            0.0 if until_ts is None else max(0.0, float(until_ts)))
        self._config.save()

    def clear_override(self) -> None:
        self._config.data['nope_override'] = OVERRIDE_NONE
        self._config.data['nope_override_until'] = 0.0
        self._config.save()

    def on_break(self, now_ts: float | None = None) -> bool:
        now_ts = _time.time() if now_ts is None else now_ts
        if self.override != OVERRIDE_BREAK:
            return False
        until = self.override_until
        if until <= now_ts:
            return False
        if until - now_ts > MAX_BREAK_MINUTES * 60:
            return False
        return True

    def schedule_active(self, now: datetime | None = None) -> bool:
        return schedule_matches(self.schedules, now or datetime.now())

    def is_active(self, now: datetime | None = None,
                  now_ts: float | None = None) -> bool:
        """Derived state: master AND (force / timed force / schedule)."""
        now = now or datetime.now()
        now_ts = _time.time() if now_ts is None else now_ts
        if not self.enabled:
            return False
        if self.on_break(now_ts):
            return False
        if self.override == OVERRIDE_FORCE:
            until = self.override_until
            if until <= 0.0 or now_ts < until:
                return True
            return self.schedule_active(now)
        if self.override == OVERRIDE_BREAK:
            return self.schedule_active(now)
        return self.schedule_active(now)

    def is_blocked(self, app_id: object,
                   now: datetime | None = None,
                   now_ts: float | None = None) -> bool:
        if not app_id:
            return False
        return self.is_active(now, now_ts) and self.is_nope_app(app_id)

    def silences(self, desktop_entry: object,
                 app_name: object = '',
                 now: datetime | None = None,
                 now_ts: float | None = None) -> bool:
        """True when a notification from this app is dropped, not held."""
        return self.is_blocked(desktop_entry or app_name, now, now_ts)

    def notice_text(self, label: str,
                    now: datetime | None = None,
                    now_ts: float | None = None) -> str:
        """Brief Nope banner line naming the app and when the block lifts."""
        now = now or datetime.now()
        now_ts = _time.time() if now_ts is None else now_ts
        end = self.active_ends_at(now, now_ts)
        if end is not None:
            stamp = _until_str(end.timestamp())
            if stamp:
                return f'{_NOPE_BANNER_TEXT} {label} is blocked until {stamp}.'
        return f'{_NOPE_BANNER_TEXT} {label} is blocked.'

    def tile_detail(self, now: datetime | None = None,
                    now_ts: float | None = None) -> str | None:
        """Shade second line while on, e.g. 'On · until 17:00'."""
        now = now or datetime.now()
        now_ts = _time.time() if now_ts is None else now_ts
        if not self.is_active(now, now_ts):
            return None
        end = self.active_ends_at(now, now_ts)
        if end is None:
            return None
        stamp = _until_str(end.timestamp())
        return f'On · until {stamp}' if stamp else None

    def active_ends_at(self, now: datetime | None = None,
                       now_ts: float | None = None) -> datetime | None:
        """When the current active stretch ends, if it is bounded."""
        now = now or datetime.now()
        now_ts = _time.time() if now_ts is None else now_ts
        if not self.is_active(now, now_ts):
            return None
        if self.override == OVERRIDE_FORCE and self.override_until > 0.0:
            return datetime.fromtimestamp(self.override_until)
        return next_schedule_end(self.schedules, now)

    @property
    def break_log(self) -> list[float]:
        val = self._config.data.get('nope_break_log', [])
        if not isinstance(val, list):
            return []
        out: list[float] = []
        for item in val:
            try:
                out.append(float(item))
            except (TypeError, ValueError):
                continue
        return out

    @property
    def min_interval_minutes(self) -> int:
        try:
            return max(0, int(self._config.data.get(
                'nope_min_interval', DEFAULT_MIN_INTERVAL_MINUTES)))
        except (TypeError, ValueError):
            return DEFAULT_MIN_INTERVAL_MINUTES

    @property
    def budget_per_window(self) -> int:
        try:
            return max(0, int(self._config.data.get(
                'nope_budget', DEFAULT_BUDGET_PER_WINDOW)))
        except (TypeError, ValueError):
            return DEFAULT_BUDGET_PER_WINDOW

    def min_interval_elapsed(self, now_ts: float | None = None) -> bool:
        now_ts = _time.time() if now_ts is None else now_ts
        log = sorted(self.break_log)
        if not log:
            return True
        elapsed = now_ts - log[-1]
        return elapsed >= 0 and elapsed >= self.min_interval_minutes * 60

    def budget_remaining(self, now: datetime | None = None) -> int:
        now = now or datetime.now()
        start = window_start(self.schedules, now)
        if start is None:
            return self.budget_per_window
        floor = start.timestamp()
        used = sum(1 for item in self.break_log if item >= floor)
        return max(0, self.budget_per_window - used)

    def break_blocked_reason(self, now: datetime | None = None,
                             now_ts: float | None = None) -> str | None:
        now = now or datetime.now()
        now_ts = _time.time() if now_ts is None else now_ts
        if not self.is_active(now, now_ts):
            return 'Nope-Mode is not active — there is nothing to take a break from.'
        if self.budget_remaining(now) <= 0:
            return 'No breaks left in this window. The budget resets when the window ends.'
        if not self.min_interval_elapsed(now_ts):
            log = sorted(self.break_log)
            waited = (now_ts - log[-1]) / 60 if log else 0
            left = max(0, int(self.min_interval_minutes - waited))
            return f'Too soon since the last break — {left} min to wait.'
        return None

    def can_edit_friction(self, now: datetime | None = None,
                          now_ts: float | None = None) -> bool:
        return not self.is_active(now, now_ts)

    def start_break(self, minutes: int,
                    now: datetime | None = None,
                    now_ts: float | None = None) -> tuple[bool, str | None]:
        """Bounded leave-early: refuse with a reason when policy blocks it."""
        now = now or datetime.now()
        now_ts = _time.time() if now_ts is None else now_ts
        if minutes not in BREAK_MINUTES:
            return False, 'Pick 5, 15 or 30 minutes — breaks are always bounded.'
        reason = self.break_blocked_reason(now, now_ts)
        if reason is not None:
            return False, reason
        self._config.data['nope_override'] = OVERRIDE_BREAK
        self._config.data['nope_override_until'] = now_ts + minutes * 60
        log = self.break_log + [now_ts]
        self._config.data['nope_break_log'] = log[-24:]
        self._config.save()
        return True, None

    def end_break(self) -> None:
        """Clear a break early; changing your mind is not a bypass."""
        if self.override == OVERRIDE_BREAK:
            self.clear_override()


def _minutes(value: object) -> int | None:
    try:
        from xx_phone.schedules import parse_hhmm
    except ImportError:
        import sys
        from pathlib import Path
        common = Path(__file__).resolve().parents[3] / 'common'
        if common.is_dir() and str(common) not in sys.path:
            sys.path.insert(0, str(common))
        from xx_phone.schedules import parse_hhmm
    try:
        return parse_hhmm(value)
    except Exception:
        return None


def window_start(schedules: list[dict], now: datetime) -> datetime | None:
    """Start of the schedule window containing `now`, if any."""
    minute = now.hour * 60 + now.minute
    weekday = now.weekday()
    for sched in schedules or []:
        if not isinstance(sched, dict):
            continue
        days = sched.get('days')
        start = _minutes(sched.get('start'))
        end = _minutes(sched.get('end'))
        if not isinstance(days, list) or start is None or end is None:
            continue
        if start <= end:
            if weekday in days and start <= minute < end:
                return now.replace(
                    hour=start // 60, minute=start % 60, second=0,
                    microsecond=0)
        elif minute >= start and weekday in days:
            return now.replace(
                hour=start // 60, minute=start % 60, second=0,
                microsecond=0)
        elif minute < end and (weekday - 1) % 7 in days:
            day = now - timedelta(days=1)
            return day.replace(
                hour=start // 60, minute=start % 60, second=0,
                microsecond=0)
    return None


def next_schedule_end(schedules: list[dict],
                      now: datetime) -> datetime | None:
    """Next end boundary of a covering window, if the state is bounded."""
    minute = now.hour * 60 + now.minute
    weekday = now.weekday()
    best: datetime | None = None
    for sched in schedules or []:
        if not isinstance(sched, dict):
            continue
        days = sched.get('days')
        start = _minutes(sched.get('start'))
        end = _minutes(sched.get('end'))
        if not isinstance(days, list) or start is None or end is None:
            continue
        covering = (
            (start <= end and weekday in days and start <= minute < end)
            or (start > end and weekday in days and minute >= start)
            or (start > end and (weekday - 1) % 7 in days and minute < end))
        if not covering:
            continue
        if start > end and minute < end:
            day = now
        elif start > end and minute >= start:
            day = now + timedelta(days=1)
        else:
            day = now
        end_at = day.replace(
            hour=end // 60, minute=end % 60, second=0, microsecond=0)
        if end_at > now and (best is None or end_at < best):
            best = end_at
    return best


def blocks_window(win: object, app_id: object, label: str = '') -> bool:
    """Launch gate for pages holding only a window ref: True when the
    launch must not happen (and the Nope notice is already shown).

    A window without Nope state (old test doubles) never blocks, so the
    gate cannot break callers that predate it.
    """
    state = getattr(win, 'nope_state', None)
    if state is None or not app_id:
        return False
    try:
        blocked = bool(state.is_blocked(app_id))
    except Exception:
        return False
    if blocked:
        try:
            win._show_nope_notice(label or str(app_id))
        except Exception:
            pass
    return blocked


def close_blocked_windows(manager: object, state: NopeState) -> list[str]:
    """Close open toplevels of chosen apps; returns the closed app ids.

    Runs when Nope turns on. Never raises and never touches real apps in
    tests: callers pass the live manager on device, tests pass a fake.
    """
    try:
        windows = list(manager.list())
    except Exception:
        return []
    closed: list[str] = []
    for topo in windows:
        app_id = getattr(topo, 'app_id', None)
        try:
            if not state.is_nope_app(app_id):
                continue
        except Exception:
            continue
        try:
            manager.close(getattr(topo, 'handle', topo))
        except Exception:
            continue
        closed.append(str(app_id))
    return closed
