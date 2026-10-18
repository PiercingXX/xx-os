"""Pure view-model helpers for the L5 app shell (Revision 2 §5, §7 L5).

GUI-free on purpose: every name here is unit-tested headless, and the
month-cell summary helper is kept pure so L10 can share it with the
shell's shade calendar without importing GTK.

Ports: navigation routes + ``routeFor`` (MainActivity.kt), the
``--date``/URI open handling (``parseDeepLink`` + F9), the
``SwipeNavigate.direction`` rule, ``ScheduleWindowState``,
``MonthScreen.buildWeeks``/``leadingDays``/``monthPagerPage``,
the ``MON 24 AUG`` header language, ``HH:mm`` ranges, and the
dots/``+N`` month-cell language of the F7 month widget.
"""
from __future__ import annotations

import calendar as _calendar
import datetime as _datetime
from dataclasses import dataclass, field

ROUTE_SCHEDULE = "schedule"
ROUTE_DAY = "day"
ROUTE_WEEK = "week"
ROUTE_MONTH = "month"

ROUTES = (ROUTE_SCHEDULE, ROUTE_DAY, ROUTE_WEEK, ROUTE_MONTH)

IMPLEMENTED_VIEWS = (ROUTE_SCHEDULE, ROUTE_MONTH)

VIEWS: tuple[tuple[str, str], ...] = (
    ("SCHEDULE", "Schedule"),
    ("DAY", "Day"),
    ("WEEK", "Week"),
    ("MONTH", "Month"),
)

APP_URI_SCHEME = "io.piercingxx.XXCalendar"

SIGIL_UNMAPPED = "·"
SIGIL_GLYPHS = ("▌", "▏", "░", "▒")

MAX_CHIPS = 3

UNTITLED = "(untitled)"
ALL_DAY_LABEL = "all day"


def route_for(default_view: object) -> str:
    """Map a ``default_view`` setting to its navigation route (all four)."""
    mapping = {"SCHEDULE": ROUTE_SCHEDULE, "DAY": ROUTE_DAY,
               "WEEK": ROUTE_WEEK, "MONTH": ROUTE_MONTH}
    if isinstance(default_view, str) and default_view in mapping:
        return mapping[default_view]
    return ROUTE_SCHEDULE


def start_route(default_view: object) -> str:
    """Route the app actually opens: unimplemented Day/Week fall back."""
    wanted = route_for(default_view)
    if wanted in IMPLEMENTED_VIEWS:
        return wanted
    return ROUTE_SCHEDULE


def parse_date_arg(text: object) -> _datetime.date | None:
    """Strict ``YYYY-MM-DD`` parse; anything else yields None, never throws."""
    if not isinstance(text, str):
        return None
    try:
        parsed = _datetime.date.fromisoformat(text.strip())
    except ValueError:
        return None
    if parsed.isoformat() != text.strip():
        return None
    return parsed


def parse_app_uri(text: object) -> _datetime.date | None:
    """Handle ``io.piercingxx.XXCalendar://date/YYYY-MM-DD`` open URIs.

    Malformed input yields None so the app opens Schedule, never a crash.
    """
    if not isinstance(text, str):
        return None
    candidate = text.strip()
    prefix = APP_URI_SCHEME + "://"
    if not candidate.startswith(prefix):
        return None
    rest = candidate[len(prefix):]
    if rest.startswith("date/"):
        rest = rest[len("date/"):]
    elif rest.startswith("date"):
        rest = rest[len("date"):]
    rest = rest.strip("/")
    head = rest.split("?")[0].split("#")[0]
    if rest.startswith("?"):
        for part in rest[1:].split("&"):
            key, _, value = part.partition("=")
            if key == "date":
                head = value
                break
        else:
            return None
    return parse_date_arg(head)


def swipe_direction(dx: float, dy: float, threshold_px: float) -> int:
    """Port of ``SwipeNavigate.direction``: -1 previous, 0 none, +1 next."""
    if abs(dx) < threshold_px:
        return 0
    if abs(dx) <= abs(dy):
        return 0
    return 1 if dx < 0 else -1


class ScheduleWindow:
    """Visible agenda window in whole days; port of ScheduleWindowState."""

    EXTEND_MONTHS = 2

    def __init__(self, today: _datetime.date | None = None) -> None:
        anchor = today or _datetime.date.today()
        first = anchor.replace(day=1)
        start = _add_months(first, -1)
        end = _month_end(_add_months(first, 1))
        self.start_day = start.toordinal()
        self.end_day = end.toordinal()
        self.revision = 0
        self.focus_date: _datetime.date | None = None

    @property
    def start_date(self) -> _datetime.date:
        return _datetime.date.fromordinal(self.start_day)

    @property
    def end_date(self) -> _datetime.date:
        return _datetime.date.fromordinal(self.end_day)

    def on_current_month(self, today: _datetime.date | None = None) -> bool:
        now = today or _datetime.date.today()
        anchor = (now.year, now.month)
        return ((self.start_date.year, self.start_date.month) <= anchor
                <= (self.end_date.year, self.end_date.month))

    def extend_backward(self) -> None:
        self.start_day = _add_months(self.start_date,
                                     -self.EXTEND_MONTHS).toordinal()

    def extend_forward(self) -> None:
        self.end_day = _month_end(_add_months(
            _datetime.date.fromordinal(self.end_day).replace(day=1),
            self.EXTEND_MONTHS)).toordinal()

    def jump_to(self, day: _datetime.date) -> None:
        first = day.replace(day=1)
        self.start_day = _add_months(first, -1).toordinal()
        self.end_day = _month_end(_add_months(first, 1)).toordinal()
        self.focus_date = day

    def focus_on(self, day: _datetime.date) -> None:
        if day < self.start_date or day > self.end_date:
            self.jump_to(day)
        else:
            self.focus_date = day

    def picker_month(self, today: _datetime.date | None = None
                     ) -> tuple[int, int]:
        anchor = self.focus_date or today or _datetime.date.today()
        return (anchor.year, anchor.month)

    def force_refresh(self) -> None:
        self.revision += 1


def _add_months(day: _datetime.date, delta: int) -> _datetime.date:
    index = day.year * 12 + (day.month - 1) + delta
    year, month = index // 12, index % 12 + 1
    last = _calendar.monthrange(year, month)[1]
    return _datetime.date(year, month, min(day.day, last))


def _month_end(first: _datetime.date) -> _datetime.date:
    return _datetime.date(first.year, first.month,
                          _calendar.monthrange(first.year, first.month)[1])


def month_pager_page(year: int, month: int, base_year: int, base_month: int,
                     page_count: int = 2401) -> int:
    """Pager index for a month relative to *base*; clamped into range."""
    index = (year * 12 + month) - (base_year * 12 + base_month)
    return max(0, min(page_count - 1, index))


def leading_days(year: int, month: int, first_weekday: int = 0) -> int:
    """Leading pad cells before the 1st, Monday-first by default (0=Mon)."""
    first = _datetime.date(year, month, 1).weekday()
    return (first - first_weekday) % 7


def build_weeks(year: int, month: int, first_weekday: int = 0
                ) -> list[list[tuple[_datetime.date, bool]]]:
    """Whole-month weeks of ``(date, in_month)``; out-month cells are real."""
    lead = leading_days(year, month, first_weekday)
    start = _datetime.date(year, month, 1) - _datetime.timedelta(days=lead)
    length = _calendar.monthrange(year, month)[1]
    count = (lead + length + 6) // 7
    weeks = []
    for week in range(count):
        row = []
        for day in range(7):
            date = start + _datetime.timedelta(days=week * 7 + day)
            row.append((date, date.year == year and date.month == month))
        weeks.append(row)
    return weeks


def weekday_labels(first_weekday: int = 0) -> list[str]:
    """``MO..SU`` column headers, start-day-of-week aware."""
    names = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
    return [names[(first_weekday + i) % 7] for i in range(7)]


def week_number(day: _datetime.date) -> int:
    """ISO week number for the week-number gutter."""
    return day.isocalendar()[1]


def month_label(year: int, month: int) -> str:
    """``AUGUST 2026``: the chrome's uppercase month language."""
    return f"{_calendar.month_name[month].upper()} {year:d}"


def month_title(year: int, month: int) -> str:
    """``August 2026``: the month page title (display type, not caps)."""
    return f"{_calendar.month_name[month]} {year:d}"


def day_title(day: _datetime.date) -> str:
    """``Sat 15 Aug``: the day page title."""
    return f"{day.strftime('%a')} {day.day:d} {day.strftime('%b')}"


#: Editor repeat choices: (label, RRULE). Weekly recurs on the start's
#: own weekday (no BYDAY), as RFC 5545 reads a bare FREQ=WEEKLY.
REPEAT_CHOICES: tuple[tuple[str, str], ...] = (
    ("Never", ""),
    ("Daily", "FREQ=DAILY"),
    ("Weekly", "FREQ=WEEKLY"),
    ("Weekdays", "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"),
    ("Monthly", "FREQ=MONTHLY"),
    ("Yearly", "FREQ=YEARLY"),
)

#: Editor reminder choices: (label, minutes before; None for none).
REMINDER_CHOICES: tuple[tuple[str, int | None], ...] = (
    ("None", None),
    ("5 min", 5),
    ("10 min", 10),
    ("30 min", 30),
    ("1 hour", 60),
    ("2 hours", 120),
    ("1 day", 1440),
)


def _rule_parts(rule: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for chunk in (rule or "").strip().upper().split(";"):
        key, _sep, value = chunk.partition("=")
        if key and value:
            parts[key] = value
    return parts


def repeat_choices(rule: str = "") -> list[tuple[str, str]]:
    """The editor's repeat list. A rule none of the presets says exactly
    (``BYDAY=MO,WE``, an ``UNTIL``) gets its own ``Custom`` entry, so
    saving an untouched event never rewrites its rule."""
    choices = list(REPEAT_CHOICES)
    if rule and repeat_index(rule, choices) is None:
        choices.append(("Custom", rule.strip()))
    return choices


def repeat_index(rule: str, choices=REPEAT_CHOICES) -> int | None:
    """Index of the choice whose rule equals ``rule`` (part order and
    case ignored); ``0`` (Never) for no rule; None when nothing matches."""
    if not (rule or "").strip():
        return 0
    wanted = _rule_parts(rule)
    for index, (_label, value) in enumerate(choices):
        if value and _rule_parts(value) == wanted:
            return index
    return None


def repeat_label(rule: str) -> str:
    """``Weekdays``, ``Weekly``…; ``Custom`` for anything else."""
    index = repeat_index(rule)
    if index is None:
        return "Custom"
    return REPEAT_CHOICES[index][0]


def reminder_label(minutes: int) -> str:
    """``10 min before``, ``1 hour before``, ``1 day before``."""
    total = max(0, int(minutes))
    if total and total % 1440 == 0:
        days = total // 1440
        amount = f"{days:d} day" + ("" if days == 1 else "s")
    elif total and total % 60 == 0:
        hours = total // 60
        amount = f"{hours:d} hour" + ("" if hours == 1 else "s")
    else:
        amount = f"{total:d} min"
    return f"{amount} before"


def when_label(start: _datetime.datetime, end: _datetime.datetime | None,
               all_day: bool) -> str:
    """Detail page WHEN: ``Tue 4 Aug 2026 · 06:30 – 07:30``, an all-day
    span ``Wed 5 – Fri 7 Aug 2026`` (``end`` exclusive), or one day."""
    def _day(moment) -> str:
        return f"{moment.strftime('%a')} {moment.day:d} {moment.strftime('%b')}"

    if all_day:
        first = start.date() if isinstance(start, _datetime.datetime) \
            else start
        last = first
        if end is not None:
            stop = end.date() if isinstance(end, _datetime.datetime) else end
            last = max(first, stop - _datetime.timedelta(days=1))
        if last == first:
            return f"{_day(first)} {first.year:d}"
        if (last.year, last.month) == (first.year, first.month):
            head = f"{first.strftime('%a')} {first.day:d}"
        else:
            head = _day(first)
        return f"{head} – {_day(last)} {last.year:d}"
    text = f"{_day(start)} {start.year:d} · {start.strftime('%H:%M')}"
    if end is None or end <= start:
        return text
    if end.date() == start.date():
        return f"{text} – {end.strftime('%H:%M')}"
    return f"{text} – {_day(end)} {end.strftime('%H:%M')}"


def default_range(day: _datetime.date, now: _datetime.datetime,
                  duration_min: int = 30) -> tuple[str, str]:
    """A new event's prefilled ``YYYY-MM-DD HH:MM`` start and end: the
    next whole hour today, 09:00 on any other day."""
    if day == now.date():
        start = now.replace(minute=0, second=0, microsecond=0) \
            + _datetime.timedelta(hours=1)
        if start.date() != day:
            start = _datetime.datetime(day.year, day.month, day.day, 23, 0)
    else:
        start = _datetime.datetime(day.year, day.month, day.day, 9, 0)
    end = start + _datetime.timedelta(minutes=max(1, int(duration_min)))
    return (start.strftime("%Y-%m-%d %H:%M"), end.strftime("%Y-%m-%d %H:%M"))


def retime_text(text: str, all_day: bool, hour: int = 9) -> str:
    """Editor All day switch: drop the time from ``YYYY-MM-DD HH:MM`` when
    it turns on, add ``hour``:00 to a bare date when it turns off. Text
    that is neither is left as typed."""
    cleaned = (text or "").strip()
    if all_day:
        try:
            moment = _datetime.datetime.strptime(cleaned, "%Y-%m-%d %H:%M")
        except ValueError:
            return text
        return moment.date().isoformat()
    try:
        day = _datetime.date.fromisoformat(cleaned)
    except ValueError:
        return text
    return f"{day.isoformat()} {int(hour):02d}:00"


def day_header(day: _datetime.date) -> str:
    """``MON 24 AUG``: the agenda/peek day header language."""
    return (f"{day.strftime('%a').upper()} {day.day:d} "
            f"{day.strftime('%b').upper()}")


def time_range(start_ms: int, end_ms: int, zone: object = None) -> str:
    """``09:00 – 09:15``, or a bare ``19:30`` with no extent."""
    if zone is None:
        zone = _datetime.timezone.utc
    start = _datetime.datetime.fromtimestamp(
        start_ms / 1000, _datetime.timezone.utc).astimezone(zone)
    if end_ms <= start_ms:
        return start.strftime("%H:%M")
    end = _datetime.datetime.fromtimestamp(
        end_ms / 1000, _datetime.timezone.utc).astimezone(zone)
    return f"{start.strftime('%H:%M')} – {end.strftime('%H:%M')}"


def month_cell_summary(count: int, cap: int = MAX_CHIPS) -> str:
    """Dots/``+N`` cell language shared with the shell's shade calendar.

    Pure by design: L10 feeds the shade grid from this without importing
    GTK. ``0`` renders nothing, ``1..cap`` render dots, beyond that the
    dots stay capped and the remainder shows as ``+N`` (F7 ``+N`` style).
    """
    try:
        total = int(count)
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    dots = "·" * min(total, cap)
    if total <= cap:
        return dots
    return f"{dots} +{total - cap:d}"


def sigil_for_tier(tier: object) -> str:
    """Glyph for a 0-based sigil tier; unknown tiers render unmapped."""
    try:
        index = int(tier)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return SIGIL_UNMAPPED
    if 0 <= index < len(SIGIL_GLYPHS):
        return SIGIL_GLYPHS[index]
    return SIGIL_UNMAPPED


@dataclass
class AgendaRow:
    uid: str = ""
    calendar_id: str = ""
    title: str = ""
    start_ms: int = 0
    end_ms: int = 0
    all_day: bool = False
    recurrence_id: int = 0
    status: str = ""
    sigil_tier: int = 0


@dataclass
class AgendaDay:
    date: _datetime.date = field(
        default_factory=_datetime.date.today)
    all_day: list[AgendaRow] = field(default_factory=list)
    timed: list[AgendaRow] = field(default_factory=list)


def query_instances(store, start_ms: int, end_ms: int,
                    calendar_ids: list[str] | None = None) -> list[dict]:
    """Read expanded occurrences straight from SQLite + the L1 expander.

    This is the GUI read path: it runs the same store window query and
    the same ``recurrence.expand_vevent`` + title logic as the service's
    ``ListInstances``, and ``test_views`` pins the two outputs identical.
    Reads stay in-process (no D-Bus round trip on the hot path); every
    write still goes through the service, which owns ``Changed``.
    """
    from xx_calendar import ics as ics_module
    from xx_calendar import recurrence as recurrence_module

    start_ms, end_ms = int(start_ms), int(end_ms)
    if end_ms <= start_ms:
        raise ValueError("window end must be after window start")
    wanted = list(calendar_ids) if calendar_ids is not None else None
    rows = store.events_in_window(start_ms, end_ms, wanted)
    parents = [r for r in rows if r.recurrence_id == 0]
    overrides: dict[str, list] = {}
    for row in rows:
        if row.recurrence_id != 0:
            overrides.setdefault(row.uid, []).append(row)
    out: list[dict] = []
    for parent in parents:
        events = ics_module.parse_events(parent.ics)
        if not events:
            continue
        event = events[0]
        override_events = []
        for row in overrides.get(parent.uid, []):
            parsed = ics_module.parse_events(row.ics)
            if parsed:
                override_events.append(parsed[0])
        summaries = {parent.uid: event.summary() or ""}
        for override in override_events:
            rid = override.recurrence_id()
            if rid is not None and override.summary():
                summaries[rid[0]] = override.summary()
        for instance in recurrence_module.expand_vevent(
                event, start_ms, end_ms, overrides=override_events):
            title = summaries.get(instance.recurrence_id or "",
                                  summaries[parent.uid])
            rid = (instance.recurrence_id
                   if instance.recurrence_id is not None
                   else instance.start_utc)
            out.append({
                "uid": parent.uid,
                "calendar_id": parent.calendar_id,
                "start": int(instance.start_utc),
                "end": int(instance.end_utc),
                "all_day": bool(instance.all_day),
                "recurrence_id": int(rid),
                "is_override": bool(instance.is_override),
                "status": instance.status or "",
                "title": title or "",
            })
    out.sort(key=lambda item: (item["start"], item["uid"]))
    return out


def group_agenda(instances: list[dict], zone,
                 start_date: _datetime.date | None = None,
                 end_date: _datetime.date | None = None) -> list[AgendaDay]:
    """Bucket plain instance dicts into days; empty days are dropped."""
    from xx_calendar.models import AgendaGrouping, InstanceSpan

    window = None
    if start_date is not None and end_date is not None:
        window = (start_date, end_date)
    spans = [InstanceSpan(uid=item["uid"], start_millis=int(item["start"]),
                          end_millis=int(item["end"]),
                          all_day=bool(item["all_day"]))
             for item in instances]
    by_key = {(item["uid"], int(item["start"])): item for item in instances}
    days = []
    for bucket in AgendaGrouping.group(spans, zone, window=window):
        rows = []
        for span in list(bucket.all_day) + list(bucket.timed):
            item = by_key.get((span.uid, span.start_millis))
            if item is None:
                continue
            rows.append(AgendaRow(
                uid=item["uid"], calendar_id=item.get("calendar_id", ""),
                title=item.get("title", "") or UNTITLED,
                start_ms=int(item["start"]), end_ms=int(item["end"]),
                all_day=bool(item["all_day"]),
                recurrence_id=int(item.get("recurrence_id", 0)),
                status=item.get("status", ""),
                sigil_tier=int(item.get("sigil_tier", 0))))
        all_day = [r for r in rows if r.all_day]
        timed = [r for r in rows if not r.all_day]
        if rows:
            days.append(AgendaDay(date=bucket.date, all_day=all_day,
                                  timed=timed))
    return days


def events_by_date(instances: list[dict], zone,
                   start_date: _datetime.date,
                   end_date: _datetime.date) -> dict:
    """Map each grid date to its rows (all-day first), for month cells."""
    grouped: dict = {}
    for day in group_agenda(instances, zone, start_date, end_date):
        grouped[day.date] = day.all_day + day.timed
    return grouped


def local_midnight_ms(day: _datetime.date, zone) -> int:
    """Local midnight as UTC millis (window edges, DST-correct)."""
    moment = _datetime.datetime(day.year, day.month, day.day, tzinfo=zone)
    return int(moment.timestamp() * 1000)


def schedule_window_ms(window: ScheduleWindow, zone) -> tuple[int, int]:
    """``[start, end)`` millis covering the whole agenda window."""
    start = local_midnight_ms(window.start_date, zone)
    end = local_midnight_ms(window.end_date + _datetime.timedelta(days=1),
                            zone)
    return (start, end)


def month_window_ms(year: int, month: int, zone,
                    margin_days: int = 7) -> tuple[int, int]:
    """``[start, end)`` millis covering a month grid plus its margin."""
    first = _datetime.date(year, month, 1)
    start_day = first - _datetime.timedelta(days=margin_days)
    last_day = _datetime.date(
        year, month, _calendar.monthrange(year, month)[1])
    end_day = last_day + _datetime.timedelta(days=margin_days + 1)
    return (local_midnight_ms(start_day, zone),
            local_midnight_ms(end_day, zone))


def build_create_ics(title: str, all_day: bool, start_ms: int, end_ms: int,
                     reminder_minutes: int | None = None,
                     rrule: str | None = None,
                     location: str | None = None,
                     description: str | None = None) -> str:
    """Build the minimal VEVENT text the create sheet writes via Create."""
    from xx_calendar import ics as ics_module
    from xx_calendar.ics import ICalProperty, VEvent

    event = VEvent()
    event.props.append(ICalProperty(name="UID", value=ics_module.make_uid(),
                                    modified=True))
    stamp = _datetime.datetime.now(
        _datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    event.props.append(ICalProperty(name="DTSTAMP", value=stamp,
                                    modified=True))
    if all_day:
        day = _datetime.datetime.fromtimestamp(
            start_ms / 1000, _datetime.timezone.utc).date()
        event.props.append(ICalProperty(name="DTSTART",
                                        params=[("VALUE", ["DATE"])],
                                        value=day.strftime("%Y%m%d"),
                                        modified=True))
        end_day = _datetime.datetime.fromtimestamp(
            end_ms / 1000, _datetime.timezone.utc).date()
        if end_day <= day:
            end_day = day + _datetime.timedelta(days=1)
        event.props.append(ICalProperty(name="DTEND",
                                        params=[("VALUE", ["DATE"])],
                                        value=end_day.strftime("%Y%m%d"),
                                        modified=True))
    else:
        event.props.append(ICalProperty(
            name="DTSTART",
            value=_datetime.datetime.fromtimestamp(
                start_ms / 1000,
                _datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            modified=True))
        if end_ms > start_ms:
            event.props.append(ICalProperty(
                name="DTEND",
                value=_datetime.datetime.fromtimestamp(
                    end_ms / 1000,
                    _datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                modified=True))
    cleaned = (title or "").strip()
    event.set_text("SUMMARY", cleaned if cleaned else UNTITLED)
    if location:
        event.set_text("LOCATION", location.strip())
    if description:
        event.set_text("DESCRIPTION", description.strip())
    if rrule:
        event.props.append(ICalProperty(name="RRULE", value=rrule.strip(),
                                        modified=True))
    if reminder_minutes is not None:
        try:
            minutes = int(reminder_minutes)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            minutes = 0
        if minutes > 0:
            event.alarms.append(ics_module.alarm_for_minutes(minutes))
    return event.serialize()
