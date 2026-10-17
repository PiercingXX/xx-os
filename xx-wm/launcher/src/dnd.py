"""
Do Not Disturb state — Pixel model (design.md "Do Not Disturb").

One toggle silences notification sounds and banners; notifications still
collect in the shade. Always-through exceptions: alarms, repeat callers
(same number twice within 15 minutes), starred contacts. Optional schedules
(days + start/end, overnight ranges supported). Config-backed so the
notification daemon and call UI can consult it.

The schedule matcher is the shared xx_phone rule (common/xx_phone),
also used by Focus Mode and xx-email. Malformed entries are skipped
with a warning, never raise.
"""
from __future__ import annotations

import re
import time as _time
from datetime import datetime

from config import ShellConfig
from shell_log import get_logger

try:
    from xx_phone.schedules import parse_hhmm
    from xx_phone.schedules import schedule_matches as _shared_schedule_matches
except ImportError:
    import sys
    from pathlib import Path

    _common_dir = Path(__file__).resolve().parents[3] / 'common'
    if _common_dir.is_dir() and str(_common_dir) not in sys.path:
        sys.path.insert(0, str(_common_dir))
    from xx_phone.schedules import parse_hhmm
    from xx_phone.schedules import schedule_matches as _shared_schedule_matches

_log = get_logger('dnd')

REPEAT_CALLER_WINDOW = 15 * 60


def normalize_number(number: str) -> str:
    """Digits only; '+1 (555) 010-2222' and '5550102222' compare equal by tail."""
    return re.sub(r'[^\d]', '', number or '')


def numbers_match(a: str, b: str) -> bool:
    na, nb = normalize_number(a), normalize_number(b)
    if not na or not nb:
        return False
    tail = min(7, len(na), len(nb))
    return na[-tail:] == nb[-tail:]


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


class DndState:
    """Config-backed DnD state plus repeat-caller tracking (in-memory)."""

    def __init__(self, config: ShellConfig) -> None:
        self._config = config
        self._recent_calls: list[tuple[str, float]] = []

    @property
    def enabled(self) -> bool:
        return bool(self._config.data.get('dnd_enabled', False))

    def set_enabled(self, enabled: bool) -> None:
        self._config.data['dnd_enabled'] = bool(enabled)
        self._config.save()

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
        return schedule_matches(self.schedules, now or datetime.now())

    # -- call exceptions ---------------------------------------------------

    def note_call(self, number: str, when: float | None = None) -> None:
        when = _time.time() if when is None else when
        cutoff = when - REPEAT_CALLER_WINDOW
        self._recent_calls = [(n, t) for n, t in self._recent_calls if t > cutoff]
        self._recent_calls.append((normalize_number(number), when))

    def is_exception(self, number: str, when: float | None = None) -> bool:
        """True if this call should ring through DnD: starred contact, or the
        same number already called within the last 15 minutes."""
        when = _time.time() if when is None else when
        if self.is_starred(number):
            return True
        cutoff = when - REPEAT_CALLER_WINDOW
        wanted = normalize_number(number)
        return any(
            t > cutoff and n and numbers_match(n, wanted)
            for n, t in self._recent_calls
        )
