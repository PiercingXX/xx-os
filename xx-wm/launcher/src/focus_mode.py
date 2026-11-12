"""
Focus Mode — Pixel model (design.md "Focus Mode").

The user picks distracting apps (`focus_apps`); while focus is active those
apps render dimmed with a paused note, launching them is blocked, and their
notifications are held silently, released in one batch when focus ends.
"Take a break" suspends focus for 5/10/15 minutes and auto-resumes.
Schedules reuse the DnD matcher. Focus and DnD are independent toggles.
"""
from __future__ import annotations

import time as _time
from datetime import datetime

from app_names import fallback_app_name
from config import ShellConfig, same_app_id
from dnd import schedule_matches

BREAK_MINUTES = (5, 10, 15)


def _norm(app_id: str) -> str:
    return app_id[:-8] if app_id.endswith('.desktop') else app_id


class FocusState:
    def __init__(self, config: ShellConfig) -> None:
        self._config = config

    @property
    def enabled(self) -> bool:
        return bool(self._config.data.get('focus_enabled', False))

    def set_enabled(self, enabled: bool) -> None:
        self._config.data['focus_enabled'] = bool(enabled)
        if not enabled:
            self._config.data['focus_break_until'] = 0.0
        self._config.save()

    @property
    def focus_apps(self) -> list[str]:
        val = self._config.data.get('focus_apps', [])
        if isinstance(val, list):
            return [str(a) for a in val]
        return []

    def set_app_focused(self, app_id: str, focused: bool) -> None:
        apps = [a for a in self.focus_apps if _norm(a) != _norm(app_id)]
        if focused:
            apps.append(app_id)
        self._config.data['focus_apps'] = apps
        self._config.save()

    def is_focus_app(self, app_id: str) -> bool:
        wanted = _norm(app_id)
        return any(_norm(a) == wanted for a in self.focus_apps)

    @property
    def schedules(self) -> list[dict]:
        val = self._config.data.get('focus_schedules', [])
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
        self._config.data['focus_schedules'] = cleaned
        self._config.save()

    @property
    def break_until(self) -> float:
        try:
            return float(self._config.data.get('focus_break_until', 0.0))
        except (TypeError, ValueError):
            return 0.0

    def on_break(self, now_ts: float | None = None) -> bool:
        now_ts = _time.time() if now_ts is None else now_ts
        return self.break_until > now_ts

    def start_break(self, minutes: int, now_ts: float | None = None) -> None:
        now_ts = _time.time() if now_ts is None else now_ts
        self._config.data['focus_break_until'] = now_ts + minutes * 60
        self._config.save()

    def is_active(self, now: datetime | None = None,
                  now_ts: float | None = None) -> bool:
        """Focus in force: toggled on or scheduled, and not on a break."""
        if self.on_break(now_ts):
            return False
        if self.enabled:
            return True
        return schedule_matches(self.schedules, now or datetime.now())

    def is_paused_app(self, app_id: str | None,
                      now: datetime | None = None,
                      now_ts: float | None = None) -> bool:
        if not app_id:
            return False
        return self.is_active(now, now_ts) and self.is_focus_app(app_id)

    def sender_is_focus_app(self, desktop_entry: str | None,
                            app_name: str | None,
                            name_of=None) -> bool:
        """Whether a notification sender is one of the focus apps.

        The desktop-entry hint and the app name are both tried as ids.
        A sender without a desktop entry also matches by name: the app's
        display name (``name_of(app_id)``) or its id's last component.
        """
        wanted = str(app_name or '').strip().casefold()
        for app in self.focus_apps:
            if same_app_id(app, desktop_entry) or same_app_id(app, app_name):
                return True
            if desktop_entry or not wanted:
                continue
            if fallback_app_name(app).casefold() == wanted:
                return True
            if name_of is not None:
                try:
                    if str(name_of(app)).strip().casefold() == wanted:
                        return True
                except Exception:
                    continue
        return False

    def is_paused_sender(self, desktop_entry: str | None,
                         app_name: str | None, name_of=None,
                         now: datetime | None = None,
                         now_ts: float | None = None) -> bool:
        """Notification gate: focus in force and the sender is paused."""
        if not (desktop_entry or app_name):
            return False
        return (self.is_active(now, now_ts)
                and self.sender_is_focus_app(desktop_entry, app_name, name_of))


class HeldNotifications:
    """Queue for notifications held while their app is paused; released in
    arrival order when focus ends or a break starts. Entries start with
    the notification id: a replacement takes the old entry's place and a
    withdrawn notification leaves the queue."""

    def __init__(self) -> None:
        self._held: list[tuple] = []

    def hold(self, *notification: object) -> None:
        if notification:
            self.withdraw(notification[0])
        self._held.append(tuple(notification))

    def withdraw(self, notif_id: object) -> bool:
        """Drop a held notification the app closed; True if one went."""
        before = len(self._held)
        self._held = [h for h in self._held if not h or h[0] != notif_id]
        return len(self._held) != before

    def release_all(self) -> list[tuple]:
        held, self._held = self._held, []
        return held

    def __len__(self) -> int:
        return len(self._held)
