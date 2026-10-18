"""Settings › Nope-Mode section: master switch, blocked apps, schedules.

Mirrors the Android app's areas in one phone page: the master switch
(plus the reason line), the blocked-app picker (installed apps with
checkboxes), the schedule list, and Take a break (5 / 15 / 30 min with the
budget/interval frictions). Quiet Ringer stays out: calls already ring
through DnD's starred/repeat exceptions, which Nope never touches.
"""
from __future__ import annotations

import settings_page
import settings_ui as ui

BREAK_CHOICES = (5, 15, 30)


def status_text(state) -> str:
    """One-line reason under the master switch ('On · until 17:00')."""
    try:
        if state.on_break():
            stamp = state.tile_detail() or ''
            return 'On a break' + (f' · {stamp}' if stamp else '')
        if not state.is_active():
            return 'Off'
    except Exception:
        return ''
    try:
        detail = state.tile_detail()
    except Exception:
        detail = None
    return detail or 'On'


def schedule_summary(sched: dict) -> str:
    """'20:00 → 08:00 · every day' from a DnD-shaped schedule dict."""
    try:
        start = str(sched.get('start', ''))
        end = str(sched.get('end', ''))
        days = sched.get('days') or []
    except Exception:
        return 'Invalid schedule'
    if not start or not end:
        return 'Invalid schedule'
    try:
        days = sorted(int(d) for d in days)
    except (TypeError, ValueError):
        return f'{start} → {end}'
    if days == [0, 1, 2, 3, 4, 5, 6]:
        when = 'every day'
    elif days == [0, 1, 2, 3, 4]:
        when = 'weeknights'
    elif not days:
        when = 'no days'
    else:
        names = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
        when = ' '.join(names[d] for d in days if 0 <= d <= 6) or 'no days'
    wrap = ' · overnight' if end <= start else ''
    return f'{start} → {end} · {when}{wrap}'


def parse_days(raw: str) -> list[int] | None:
    """'0,1,2,3,4,5,6' (Monday-first) or '0-6' ranges, else None."""
    try:
        text = str(raw or '').strip()
    except Exception:
        return None
    if not text:
        return None
    days: set[int] = set()
    for part in text.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            try:
                first, last = part.split('-', 1)
                first_n, last_n = int(first), int(last)
            except (TypeError, ValueError):
                return None
            if not (0 <= first_n <= 6 and 0 <= last_n <= 6) \
                    or last_n < first_n:
                return None
            days.update(range(first_n, last_n + 1))
        else:
            try:
                num = int(part)
            except (TypeError, ValueError):
                return None
            if not 0 <= num <= 6:
                return None
            days.add(num)
    return sorted(days) if days else None


def _valid_hhmm(raw: str) -> bool:
    try:
        import re
        match = re.match(r'^(\d{1,2}):(\d{2})$', str(raw or '').strip())
    except Exception:
        return False
    if not match:
        return False
    return int(match.group(1)) <= 23 and int(match.group(2)) <= 59


def parse_schedule_form(values: dict) -> tuple[dict | None, str | None]:
    """Validate the Add-schedule sheet; (schedule, None) or (None, error)."""
    days = parse_days(values.get('days', ''))
    if days is None:
        return None, 'Days: 0-6 Monday-first, e.g. 0,1,2,3,4,5,6.'
    start = str(values.get('start', '') or '').strip()
    end = str(values.get('end', '') or '').strip()
    if not _valid_hhmm(start) or not _valid_hhmm(end):
        return None, 'Times: HH:MM, e.g. 20:00 and 08:00.'
    return {'days': days, 'start': start, 'end': end}, None


def app_choices(win) -> list[tuple[str, str, bool]]:
    """(app_id, display name, blocked) for every indexed app, A–Z."""
    try:
        entries = list(win.app_index.entries)
    except Exception:
        return []
    try:
        state = win.nope_state
    except Exception:
        state = None
    rows = []
    for entry in entries:
        app_id = getattr(entry, 'app_id', '') or ''
        if not app_id:
            continue
        try:
            name = win.config.label_for(app_id, getattr(entry, 'name', app_id))
        except Exception:
            name = getattr(entry, 'name', app_id)
        try:
            blocked = bool(state is not None and state.is_nope_app(app_id))
        except Exception:
            blocked = False
        rows.append((app_id, str(name), blocked))
    rows.sort(key=lambda row: row[1].casefold())
    return rows


class NopeSection(settings_page._Section):
    """Settings section: Nope-Mode toggle, apps, schedules, breaks."""

    key = 'nope'
    title = 'Nope-Mode'

    def __init__(self) -> None:
        self._status_card = None
        self._apps_card = None
        self._sched_card = None
        self._updating = False

    def build(self, win) -> list:
        top = ui.group('Nope-Mode', note='Chosen apps go silent and won\'t '
                       'open. Calls still ring through DnD as usual.')
        self._status_card = top[1]
        apps = ui.group('Blocked apps', note='Pick the apps that go silent.')
        self._apps_card = apps[1]
        sched = ui.group('Schedules', note='Days are Monday-first: 0 is '
                         'Monday, 6 is Sunday. Overnight ranges wrap.')
        self._sched_card = sched[1]
        self.refresh(win)
        return top + apps + sched

    def refresh(self, win) -> None:
        self._render_status(win)
        self._render_apps(win)
        self._render_schedules(win)

    def _state(self, win):
        return getattr(win, 'nope_state', None)

    def _render_status(self, win) -> None:
        if self._status_card is None:
            return
        state = self._state(win)
        active = False
        try:
            active = bool(state is not None and state.enabled)
        except Exception:
            pass
        master = ui.switch_row(
            'Nope-Mode', status_text(state) if state is not None else '',
            active, on_toggle=lambda on: self._set_enabled(win, on))
        rows = [master]
        if state is not None:
            try:
                reason = state.break_blocked_reason()
            except Exception:
                reason = None
            detail = ''
            if reason is not None:
                try:
                    remaining = state.budget_remaining()
                    detail = (f"{remaining} break"
                              f"{'' if remaining == 1 else 's'} left · "
                              f"{reason}")
                except Exception:
                    detail = reason
            for minutes in BREAK_CHOICES:
                rows.append(ui.action_row(
                    f'Take a break ({minutes} min)', 'Start',
                    lambda _r, m=minutes: self._start_break(win, m),
                    subtitle=detail))
        ui.fill(self._status_card, rows)

    def _render_apps(self, win) -> None:
        if self._apps_card is None:
            return
        choices = app_choices(win)
        if not choices:
            ui.fill(self._apps_card, [ui.note_row('No apps indexed yet.')])
            return
        rows = []
        for app_id, name, blocked in choices:
            rows.append(ui.switch_row(
                name, 'Blocked' if blocked else '', blocked,
                on_toggle=lambda on, a=app_id: self._set_blocked(win, a, on)))
        ui.fill(self._apps_card, rows)

    def _render_schedules(self, win) -> None:
        if self._sched_card is None:
            return
        state = self._state(win)
        try:
            schedules = list(state.schedules) if state is not None else []
        except Exception:
            schedules = []
        rows = []
        for index, sched in enumerate(schedules):
            rows.append(ui.action_row(
                schedule_summary(sched), 'Remove',
                lambda _r, i=index: self._remove_schedule(win, i)))
        rows.append(ui.nav_row('Add schedule', 'Days + start/end',
                               lambda r: self._add_schedule(win, r)))
        ui.fill(self._sched_card, rows)

    def _set_enabled(self, win, on: bool) -> None:
        if self._updating:
            return
        state = self._state(win)
        if state is None:
            return
        try:
            if on:
                state.set_enabled(True)
                state.set_force()
                win._on_nope_turned_on()
            else:
                state.set_enabled(False)
        except Exception:
            pass
        self.refresh(win)

    def _set_blocked(self, win, app_id: str, blocked: bool) -> None:
        state = self._state(win)
        if state is None:
            return
        try:
            state.set_app_blocked(app_id, blocked)
        except Exception:
            pass
        self._render_apps(win)

    def _remove_schedule(self, win, index: int) -> None:
        state = self._state(win)
        if state is None:
            return
        try:
            schedules = list(state.schedules)
            del schedules[index]
            state.set_schedules(schedules)
        except (IndexError, TypeError, ValueError):
            pass
        except Exception:
            pass
        self.refresh(win)

    def _add_schedule(self, win, anchor) -> None:
        def _commit(values: dict) -> str | None:
            sched, error = parse_schedule_form(values)
            if error is not None:
                return error
            state = self._state(win)
            if state is None:
                return 'Nope-Mode is unavailable.'
            try:
                state.set_schedules(list(state.schedules) + [sched])
            except Exception:
                return 'Could not save the schedule.'
            self.refresh(win)
            return None

        ui.form_sheet(
            win, anchor, 'Add schedule',
            [('days', 'DAYS (0=Monday … 6=Sunday)', '0,1,2,3,4,5,6', 'text'),
             ('start', 'START (HH:MM)', '20:00', 'text'),
             ('end', 'END (HH:MM)', '08:00', 'text')],
            'Add', _commit,
            note='Overnight ranges like 20:00 → 08:00 wrap past midnight.')

    def _start_break(self, win, minutes: int) -> None:
        state = self._state(win)
        if state is None:
            return
        try:
            ok, reason = state.start_break(minutes)
        except Exception:
            return
        if not ok:
            try:
                win._show_status(str(reason))
            except Exception:
                pass
        self.refresh(win)
