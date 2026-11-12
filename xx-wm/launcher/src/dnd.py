"""
Do Not Disturb state — Pixel model (design.md "Do Not Disturb").

One toggle silences notification sounds and banners; notifications still
collect in the shade. Always-through exceptions: alarms, repeat callers
(same number twice within 15 minutes), starred contacts. Optional schedules
(days + start/end, overnight ranges supported). Config-backed so the
notification daemon and call UI can consult it.

The schedule matcher is the shared xx_phone rule (common/xx_phone),
also used by Focus Mode and xx-email. Malformed entries are skipped
with a warning, never raise. Numbers compare through the shared
xx_phone.numbers_match rule (whole number, never the last 7 digits),
so a starred 614-555-0142 does not let 312-555-0142 ring through.
"""
from __future__ import annotations

import re
import time as _time
from datetime import datetime, timedelta

from config import ShellConfig
from shell_log import get_logger, log_exc

try:
    from xx_phone import numbers_match as _shared_numbers_match
    from xx_phone.schedules import parse_hhmm
    from xx_phone.schedules import schedule_matches as _shared_schedule_matches
except ImportError:
    import sys
    from pathlib import Path

    _common_dir = Path(__file__).resolve().parents[3] / 'common'
    if _common_dir.is_dir() and str(_common_dir) not in sys.path:
        sys.path.insert(0, str(_common_dir))
    from xx_phone import numbers_match as _shared_numbers_match
    from xx_phone.schedules import parse_hhmm
    from xx_phone.schedules import schedule_matches as _shared_schedule_matches

_log = get_logger('dnd')

REPEAT_CALLER_WINDOW = 15 * 60

# Only an alarm category passes DnD and Focus. Critical urgency alone does
# not: any app can ask for it.
ALARM_CATEGORY = 'alarm'


def is_alarm_notification(hints: object) -> bool:
    """True for a notification whose category is 'alarm' (or 'alarm.*')."""
    if not isinstance(hints, dict):
        return False
    category = hints.get('category', '')
    if not isinstance(category, str):
        return False
    category = category.strip().casefold()
    return (category == ALARM_CATEGORY
            or category.startswith(ALARM_CATEGORY + '.'))


def normalize_number(number: str) -> str:
    """Digits only (a leading '+' is dropped); for display/keys, not matching."""
    return re.sub(r'[^\d]', '', number or '')


def numbers_match(a: str, b: str) -> bool:
    """Same number? The shared xx_phone rule: '+1 (614) 555-0142' and
    '6145550142' match; '3125550142' and a bare '5550142' do not."""
    try:
        return bool(_shared_numbers_match(a, b))
    except Exception:
        na, nb = normalize_number(a), normalize_number(b)
        return bool(na) and na == nb


def schedule_matches(schedules, now: datetime) -> bool:
    """True if any schedule covers `now` (shared xx_phone rule).

    Malformed entries are skipped with a warning, never raise, so a
    hand-edited config cannot crash the shell.
    """
    if schedules is None:
        return False
    if isinstance(schedules, (str, bytes, dict)):
        _log.warning('skipping malformed dnd schedules: %r', schedules)
        return False
    try:
        entries = list(schedules or [])
    except TypeError:
        _log.warning('skipping malformed dnd schedules: %r', schedules)
        return False
    for sched in entries:
        if not isinstance(sched, dict):
            _log.warning('skipping malformed dnd schedule: %r', sched)
            continue
        days = sched.get('days')
        start = parse_hhmm(sched.get('start'))
        end = parse_hhmm(sched.get('end'))
        if not isinstance(days, list) or start is None or end is None:
            _log.warning('skipping malformed dnd schedule: %r', sched)
    try:
        return bool(_shared_schedule_matches(schedules, now))
    except Exception:
        _log.warning('skipping malformed dnd schedules: %r', schedules)
        return False


def scheduled_session_end(schedules, now: datetime) -> float:
    """Epoch at which the scheduled session covering `now` ends (windows
    that touch or overlap count as one session); 0.0 when none covers.

    Turning DnD or Focus off inside a schedule skips the rest of that
    session; the next one starts as usual."""
    if not schedule_matches(schedules, now):
        return 0.0
    from nope_mode import next_schedule_end
    at = now
    for _ in range(8):
        end = next_schedule_end(schedules, at)
        if end is None or end <= at:
            break
        at = end
        if not schedule_matches(schedules, at):
            return at.timestamp()
    if at > now:
        return at.timestamp()
    return (now + timedelta(days=1)).timestamp()


def schedule_skipped(skip_until: object, now: datetime) -> bool:
    try:
        return now.timestamp() < float(skip_until or 0.0)
    except (TypeError, ValueError, OverflowError, OSError):
        return False


class DndState:
    """Config-backed DnD state plus repeat-caller tracking (in-memory)."""

    # Called after the on/off state or schedule changes (the status bar
    # flags redraw at once instead of on the next minute tick).
    on_change = None

    def _changed(self) -> None:
        callback = self.on_change
        if callback is not None:
            try:
                callback()
            except Exception:
                log_exc('dnd._changed: callback')

    def __init__(self, config: ShellConfig) -> None:
        self._config = config
        self._recent_calls: list[tuple[str, float]] = []

    @property
    def enabled(self) -> bool:
        return bool(self._config.data.get('dnd_enabled', False))

    def set_enabled(self, enabled: bool, now: datetime | None = None) -> None:
        """Off also ends a scheduled session in progress; on clears that."""
        self._config.data['dnd_enabled'] = bool(enabled)
        self._config.data['dnd_schedule_off_until'] = (
            0.0 if enabled else scheduled_session_end(
                self.schedules, now or datetime.now()))
        self._config.save()
        self._changed()

    @property
    def schedules(self) -> list[dict]:
        val = self._config.data.get('dnd_schedules', [])
        return val if isinstance(val, list) else []

    def set_schedules(self, schedules: list[dict]) -> None:
        cleaned: list[dict] = []
        for sched in schedules:
            if not isinstance(sched, dict):
                continue
            days = sched.get('days')
            start = sched.get('start')
            end = sched.get('end')
            if not isinstance(days, list) or not isinstance(start, str) or not isinstance(end, str):
                continue
            cleaned.append({
                'days': [int(d) for d in days if isinstance(d, int)],
                'start': start,
                'end': end,
            })
        self._config.data['dnd_schedules'] = cleaned
        self._config.save()
        self._changed()

    @property
    def starred_numbers(self) -> list[str]:
        val = self._config.data.get('dnd_starred_numbers', [])
        if isinstance(val, list):
            return [str(n) for n in val]
        return []

    def set_starred(self, number: str, starred: bool) -> None:
        current = [n for n in self.starred_numbers if not numbers_match(n, number)]
        if starred:
            current.append(number)
        self._config.data['dnd_starred_numbers'] = current
        self._config.save()

    def is_starred(self, number: str) -> bool:
        return any(numbers_match(n, number) for n in self.starred_numbers)

    def is_active(self, now: datetime | None = None) -> bool:
        if self.enabled:
            return True
        now = now or datetime.now()
        if schedule_skipped(
                self._config.data.get('dnd_schedule_off_until'), now):
            return False
        return schedule_matches(self.schedules, now)

    # -- call exceptions ---------------------------------------------------

    def note_call(self, number: str, when: float | None = None) -> None:
        when = _time.monotonic() if when is None else when
        cutoff = when - REPEAT_CALLER_WINDOW
        self._recent_calls = [(n, t) for n, t in self._recent_calls if t > cutoff]
        # Kept as dialled ('+' and all) so the shared matcher can tell
        # a country code from a national number.
        self._recent_calls.append(((number or '').strip(), when))

    def is_exception(self, number: str, when: float | None = None) -> bool:
        """True if this call should ring through DnD: starred contact, or the
        same number already called within the last 15 minutes."""
        when = _time.monotonic() if when is None else when
        if self.is_starred(number):
            return True
        cutoff = when - REPEAT_CALLER_WINDOW
        return any(
            t > cutoff and n and numbers_match(n, number)
            for n, t in self._recent_calls
        )
