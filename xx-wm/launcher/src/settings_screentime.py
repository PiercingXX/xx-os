"""Screen-time settings section: today + last 7 days, per-app detail,
unlock/notification counts, and optional per-app daily limits.

Data comes from usage_stats (foreground seconds while unlocked, unlocks,
notification counts). Limits are config-backed ({app_id: minutes}); when
one is reached the section fires a gentle banner (banner.py policy and
card) and the drawer greys the app until midnight. "Ignore for today"
dismisses both until the next day.
"""
from __future__ import annotations

import settings_page
import settings_ui as ui

import datetime

_SCREEN_TIME_NOTIF_ID = 999905

LIMIT_CHOICES: tuple = ((0, 'Off'), (15, '15 min'), (30, '30 min'),
                        (60, '1 hour'), (120, '2 hours'), (240, '4 hours'))

_WEEK_LETTERS = ('M', 'T', 'W', 'T', 'F', 'S', 'S')


def today_key(day=None) -> str:
    if day is None:
        day = datetime.date.today()
    if isinstance(day, datetime.datetime):
        day = day.date()
    if isinstance(day, str):
        return day
    return day.isoformat()


def fmt_duration(secs) -> str:
    """Short phone-sized duration: `45s`, `12m`, `1h 05m`."""
    try:
        total = max(0, int(secs))
    except (TypeError, ValueError):
        total = 0
    if total < 60:
        return f'{total}s'
    minutes, _secs = divmod(total, 60)
    if minutes < 60:
        return f'{minutes}m'
    hours, minutes = divmod(minutes, 60)
    return f'{hours}h {minutes:02d}m'


def limit_label_text(minutes: int) -> str:
    for value, label in LIMIT_CHOICES:
        if value == minutes:
            return label
    return f'{minutes}m'


def is_ignored(config, app_id: str, day: str) -> bool:
    try:
        ignored = config.app_limit_ignored
    except Exception:
        return False
    return isinstance(ignored, dict) and ignored.get(app_id) == day


def ignore_for_today(config, app_id: str, day: str | None = None) -> None:
    config.set_app_limit_ignored(app_id, today_key(day))


def reached_limits(config, totals: dict, day: str) -> dict:
    """{app_id: (secs, limit_secs)} for limits hit today, minus ignores."""
    try:
        limits = config.app_time_limits
    except Exception:
        return {}
    if not isinstance(limits, dict):
        return {}
    hit: dict = {}
    totals = totals if isinstance(totals, dict) else {}
    for app_id, minutes in limits.items():
        try:
            limit_secs = int(minutes) * 60
            secs = int(totals.get(app_id, 0))
        except (TypeError, ValueError):
            continue
        if limit_secs > 0 and secs >= limit_secs and not is_ignored(
                config, app_id, day):
            hit[app_id] = (secs, limit_secs)
    return hit


def screen_time_secs(win, app_id: str) -> int:
    """Today's foreground seconds for one app, cached per day on win so
    drawer rows never hit the tracker more than once per populate."""
    from settings_battery_use import usage_tracker_for
    day = today_key()
    cache = getattr(win, '_screen_time_cache', None)
    if not isinstance(cache, dict) or cache.get('day') != day:
        cache = {'day': day, 'totals': {}}
        tracker = usage_tracker_for(win)
        if tracker is not None:
            try:
                cache['totals'] = tracker.daily_totals(day) or {}
            except Exception:
                cache['totals'] = {}
        try:
            win._screen_time_cache = cache
        except Exception:
            pass
    try:
        return max(0, int(cache.get('totals', {}).get(app_id, 0)))
    except (TypeError, ValueError):
        return 0


def is_limited_today(win, app_id: str) -> bool:
    """True when the app hit its daily limit and was not ignored today:
    the drawer greys it until midnight."""
    try:
        limits = win.config.app_time_limits
    except Exception:
        return False
    if not isinstance(limits, dict) or app_id not in limits:
        return False
    try:
        limit_secs = int(limits[app_id]) * 60
    except (TypeError, ValueError):
        return False
    if limit_secs <= 0:
        return False
    if is_ignored(win.config, app_id, today_key()):
        return False
    return screen_time_secs(win, app_id) >= limit_secs


def notify_limit(win, app_id: str, label: str, secs: int,
                 limit_secs: int) -> bool:
    """Gentle banner for a reached limit. Returns True when a banner was
    requested. `win._screen_time_banner` overrides dispatch in tests."""
    override = getattr(win, '_screen_time_banner', None)
    if callable(override):
        try:
            override(app_id, label, secs, limit_secs)
        except Exception:
            return False
        return True
    try:
        app = win.get_application()
    except Exception:
        return False
    locked = True
    try:
        locked_fn = getattr(app, '_banner_locked', None)
        locked = bool(locked_fn()) if callable(locked_fn) else True
    except Exception:
        locked = True
    if locked:
        return False
    try:
        banner = app._ensure_banner()
        banner.show_notification(
            _SCREEN_TIME_NOTIF_ID, 'Screen time',
            f'{label} reached its daily limit',
            f'{fmt_duration(secs)} of {fmt_duration(limit_secs)} · '
            'Ignore for today in Settings → Screen time.',
            'xx-wm', [], None)
    except Exception:
        return False
    return True


_CHART_PX = 64


def week_chart(week) -> object:
    """Seven bars, today's in the foreground color, with day letters."""
    from gi.repository import Gtk
    from xx_ui import widgets

    peak = max((secs for _day, secs in week), default=0)
    chart = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    chart.set_homogeneous(True)
    for index, (day, secs) in enumerate(week):
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        well = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        well.set_size_request(-1, _CHART_PX)
        bar = Gtk.Box()
        bar.add_css_class('xx-set-chart-bar')
        if index == len(week) - 1:
            bar.add_css_class('today')
        height = round(_CHART_PX * secs / peak) if peak > 0 else 0
        bar.set_size_request(-1, max(3, height))
        bar.set_valign(Gtk.Align.END)
        bar.set_vexpand(True)
        well.append(bar)
        try:
            letter = day.strftime('%a')[:1]
        except Exception:
            letter = _WEEK_LETTERS[index % 7]
        name = widgets.label(letter, 'xx-caption', xalign=0.5)
        column.append(well)
        column.append(name)
        chart.append(column)
    return chart


class ScreenTimeSection(settings_page._Section):
    """Settings section: week chart, per-app times, limits."""

    key = 'screentime'
    title = 'Screen time'

    def __init__(self) -> None:
        self._today_label = None
        self._counts_label = None
        self._week_box = None
        self._apps_box = None
        self._selected: str | None = None

    def build(self, win) -> list:
        from gi.repository import Gtk
        from xx_ui import widgets

        summary = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._today_label = widgets.label('—', 'xx-set-big')
        self._counts_label = widgets.wrap_label('')
        summary.append(self._today_label)
        summary.append(self._counts_label)
        self._week_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        summary.append(self._week_box)
        self._week_box.set_margin_top(16)
        top = ui.group('Screen time today', [ui.wide_row(summary)])
        apps = ui.group('App time and limits',
                        note='Tap an app for its daily limit or its week.')
        self._apps_box = apps[1]
        return top + apps

    def refresh(self, win) -> None:
        from settings_page import _run_bg
        from settings_battery_use import usage_tracker_for
        tracker = usage_tracker_for(win)
        _run_bg(lambda: self._gather(tracker),
                lambda res: self._render(win, res))

    # ------------------------------------------------------------ behavior

    def _gather(self, tracker):
        """Worker thread: (today_totals, week, unlocks, notif_total)."""
        import datetime as _dt
        today = _dt.date.today()
        totals: dict = {}
        week: list = []
        unlocks = 0
        notifs = 0
        if tracker is not None:
            try:
                totals = tracker.daily_totals(today) or {}
                week = tracker.week(today) or []
                unlocks = tracker.unlocks(today) or 0
                per_app = tracker.notifications(today) or {}
                notifs = sum(per_app.values())
            except Exception:
                pass
        if not week:
            week = [(today - _dt.timedelta(days=i), 0)
                    for i in range(6, -1, -1)]
        return (totals, week, unlocks, notifs)

    def _render(self, win, res) -> None:
        try:
            totals, week, unlocks, notifs = res
        except (TypeError, ValueError):
            return
        day = today_key()
        if self._today_label is not None:
            total = sum(totals.values()) if totals else 0
            self._today_label.set_text(fmt_duration(total))
        self._render_week(week)
        if self._counts_label is not None:
            self._counts_label.set_text(
                f'{unlocks} unlocks · {notifs} notifications')
        try:
            win._screen_time_cache = {'day': day, 'totals': dict(totals)}
        except Exception:
            pass
        self._check_limits(win, totals, day)
        self._render_apps(win, totals, day)

    def _render_week(self, week) -> None:
        if self._week_box is not None:
            ui.fill(self._week_box, [week_chart(week)])

    def _check_limits(self, win, totals: dict, day: str) -> None:
        try:
            hit = reached_limits(win.config, totals, day)
        except Exception:
            return
        if not hit:
            return
        try:
            notified = win._screen_time_notified
            if not isinstance(notified, set):
                notified = set()
        except Exception:
            notified = set()
        for app_id, (secs, limit_secs) in hit.items():
            marker = (app_id, day)
            if marker in notified:
                continue
            label = self._display_name(win, app_id)
            if notify_limit(win, app_id, label, secs, limit_secs):
                notified.add(marker)
        try:
            win._screen_time_notified = notified
        except Exception:
            pass

    def _render_apps(self, win, totals: dict, day: str) -> None:
        if self._apps_box is None:
            return
        if self._selected is not None:
            self._render_breakdown(win, totals)
            return
        try:
            limits = win.config.app_time_limits
        except Exception:
            limits = {}
        over = reached_limits(win.config, totals, day)
        rows = []
        for app_id, secs in sorted(totals.items(), key=lambda kv: -kv[1]):
            current = 0
            if isinstance(limits, dict):
                try:
                    current = int(limits.get(app_id, 0))
                except (TypeError, ValueError):
                    current = 0
            detail = '%s today' % fmt_duration(secs)
            if current:
                detail += ' · limit %s' % limit_label_text(current)
            if app_id in over:
                detail += ' · over'
            rows.append(ui.nav_row(
                self._display_name(win, app_id), detail,
                lambda r, a=app_id, c=current: self._app_menu(
                    win, r, a, c, a in over)))
        ui.fill(self._apps_box, rows, hide_empty=True)

    def _app_menu(self, win, anchor, app_id: str, current: int,
                  is_over: bool) -> None:
        choices = [('Limit: %s%s' % (label, ' ✓' if minutes == current
                                     else ''),
                    lambda m=minutes: self._set_limit(win, app_id, m))
                   for minutes, label in LIMIT_CHOICES]
        if is_over:
            choices.append(('Ignore for today',
                            lambda: self._ignore(win, app_id)))
        choices.append(('Last 7 days', lambda: self._select(win, app_id)))
        win.item_actions._show_action_menu(
            anchor, self._display_name(win, app_id), choices)

    def _set_limit(self, win, app_id: str, minutes: int) -> None:
        try:
            win.config.set_app_time_limit(app_id, minutes)
        except Exception:
            return
        self.refresh(win)

    def _render_breakdown(self, win, totals: dict) -> None:
        from settings_battery_use import usage_tracker_for
        tracker = usage_tracker_for(win)
        per_day: list = []
        if tracker is not None:
            import datetime as _dt
            today = _dt.date.today()
            for back in range(6, -1, -1):
                day = today - _dt.timedelta(days=back)
                try:
                    secs = (tracker.daily_totals(day) or {}).get(
                        self._selected, 0)
                except Exception:
                    secs = 0
                per_day.append((day, secs))
        rows = [ui.row(self._display_name(win, self._selected),
                       'Last 7 days')]
        for day, secs in per_day:
            try:
                stamp = day.strftime('%a %b %d')
            except Exception:
                stamp = str(day)
            rows.append(ui.row(stamp, fmt_duration(secs)))
        rows.append(ui.nav_row('All apps', 'Back to today',
                               lambda _r: self._select(win, None)))
        ui.fill(self._apps_box, rows)

    def _select(self, win, app_id: str | None) -> None:
        self._selected = app_id
        self.refresh(win)

    def _ignore(self, win, app_id: str) -> None:
        try:
            ignore_for_today(win.config, app_id)
        except Exception:
            return
        repopulate = getattr(win, '_populate_apps', None)
        if callable(repopulate):
            try:
                repopulate('')
            except Exception:
                pass
        else:
            refresh_parent = getattr(win, '_refresh_after_item_action', None)
            if callable(refresh_parent):
                try:
                    refresh_parent()
                except Exception:
                    pass
        self.refresh(win)

    def _display_name(self, win, app_id: str) -> str:
        try:
            from settings_battery_use import friendly_app_name
        except ImportError:
            return app_id
        return friendly_app_name(win, app_id)
