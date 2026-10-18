"""Journal tab: activities grouped into local days, newest first.

Grouping and totals live in core/journal; rows are xx ListRows inside a
Gtk.ListView, day headers carry the day totals plus a mini effort ring.
Tapping a row opens a read-only detail dialog with the Android stat lines.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from xx_vitals.core import journal as journal_model

NO_DATA_BAND = "— no data —"


def _item_type():
    """ListView payload type: widgets are built in bind(), so a hidden
    page never pays for its rows in size negotiation."""
    from gi.repository import GObject

    class Item(GObject.Object):
        def __init__(self, kind: str, payload) -> None:
            super().__init__()
            self.kind = kind
            self.payload = payload

    return Item

#: (chip label, activity-type value): short labels fit 360 px unscrolled.
FILTERS = (("All", "all"), ("Walk", "walk"), ("Run", "run"),
           ("Bike", "cycle"), ("Misc", "other"))

MINI_PX = 30


def journal_rows(activities, zone, effort_target: int, dailies=()) -> list:
    return journal_model.group_by_day(activities, zone, effort_target, dailies)


def gap_band_text(groups, today) -> str | None:
    if not groups:
        return None
    newest = max(group.date for group in groups)
    gap = journal_model.no_data_gap(newest, today)
    if gap > 0:
        return f"{NO_DATA_BAND} ({gap}d)"
    return None


def journal_caption(days: int, total_steps: int) -> str | None:
    """Header caption: day and step counts, nothing for an empty journal."""
    if not days:
        return None
    caption = f"{days} day" if days == 1 else f"{days} days"
    if total_steps:
        caption += (" · 1 step" if total_steps == 1
                    else f" · {total_steps:,} steps")
    return caption


def _local_zone():
    return datetime.now().astimezone().tzinfo


def _day_title(day: date, today: date) -> str:
    if day == today:
        return "Today"
    if day == date.fromordinal(today.toordinal() - 1):
        return "Yesterday"
    return f"{day.strftime('%a, %b')} {day.day}"


def _clock_text(started_ms: int) -> str:
    return datetime.fromtimestamp(
        started_ms / 1000, tz=timezone.utc).astimezone().strftime("%H:%M")


def _span_text(started_ms: int, ended_ms: int) -> str:
    span = max(1, round((ended_ms - started_ms) / 60000))
    hours, minutes = divmod(span, 60)
    if hours:
        return f"{hours} h {minutes} min"
    return f"{minutes} min"


def _daypart(started_ms: int) -> str:
    hour = int(_clock_text(started_ms)[:2])
    if hour < 5:
        return "Late night"
    if hour < 12:
        return "Morning"
    if hour < 18:
        return "Afternoon"
    return "Evening"


def activity_title(activity) -> str:
    kind = (activity.type or "").strip().lower()
    if kind == journal_model.PHONE_STEPS_TYPE.lower():
        return "Phone steps"
    if kind == "walk":
        return f"{_daypart(activity.started_at)} walk"
    if kind == "run":
        return f"{_daypart(activity.started_at)} run"
    if kind == "cycle":
        return f"{_daypart(activity.started_at)} ride"
    return kind.capitalize() if kind else "Activity"


def activity_line(activity, units: str) -> str:
    from xx_vitals.core import home as home_model

    clock = _clock_text(activity.started_at)
    if (activity.type or "").lower() == journal_model.PHONE_STEPS_TYPE.lower():
        steps = f"{activity.steps:,} steps" if activity.steps else "—"
        pts = f"{activity.effort_points} pts" if activity.effort_points else ""
        return f"{steps} · {pts}".strip(" ·")
    parts = [clock]
    if activity.distance_m:
        parts.append(home_model.format_distance(activity.distance_m, units)
                     + " " + home_model.distance_label(units))
    if activity.effort_points:
        parts.append(f"{activity.effort_points} pts")
    return " · ".join(parts)


def _draw_mini(area, cairo, _width, _height, fraction: float) -> None:
    import xx_ui

    theme = xx_ui.theme.load()
    from gi.repository import Gdk

    def paint(hex_color: str):
        rgba = Gdk.RGBA()
        rgba.parse(hex_color)
        return (rgba.red, rgba.green, rgba.blue, rgba.alpha)

    cx = cy = MINI_PX / 2
    cairo.set_line_width(3.5)
    cairo.set_source_rgba(*paint(theme.surface_alt))
    cairo.arc(cx, cy, 11.0, 0, 6.2832)
    cairo.stroke()
    if fraction > 0:
        cairo.set_source_rgba(*paint(theme.foreground))
        cairo.arc(cx, cy, 11.0, -1.5708, -1.5708 + min(1.0, fraction) * 6.2832)
        cairo.stroke()


def build_journal_page(db, config, nav, live: dict | None = None) -> object:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gio, Gtk

    from xx_ui import widgets

    from xx_vitals.ui import shell as shell_page

    live = live if live is not None else {}
    state = {"filter": "all", "overflow": None, "store": None}
    header_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    chips_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    list_holder = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    list_holder.set_vexpand(True)

    def menu() -> object:
        items = shell_page.settings_items(config, nav)
        if not items:
            return None
        if state["overflow"] is not None:
            state["overflow"].popup()
            return state["overflow"]
        anchor = header_box.get_first_child()
        popover = shell_page.open_overflow(anchor, items)
        state["overflow"] = popover
        popover.connect("closed", lambda _p: state.update(overflow=None))
        return popover

    def rows_for_filter(groups):
        from xx_vitals.core.journal import DayGroup, ring_fraction

        wanted = state["filter"]
        if wanted == "all":
            return groups
        kept = []
        for group in groups:
            activities = tuple(
                activity for activity in group.activities
                if (activity.type or "").lower() == wanted)
            if activities:
                effort = sum(a.effort_points or 0 for a in activities)
                kept.append(DayGroup(
                    date=group.date, activities=activities,
                    total_steps=sum(a.steps or 0 for a in activities),
                    total_effort=effort,
                    ring_fraction=ring_fraction(effort, config.effort_goal)))
        return kept

    def refresh() -> None:
        try:
            activities = db.list_activities(limit=200)
        except (OSError, AttributeError):
            activities = []
        try:
            dailies = db.list_dailies(limit=60)
        except (OSError, AttributeError):
            dailies = []
        zone = _local_zone() or ZoneInfo("UTC")
        groups = rows_for_filter(journal_rows(
            activities, zone, config.effort_goal, dailies))
        total_steps = sum(a.steps or 0 for g in groups for a in g.activities)
        while True:
            first = header_box.get_first_child()
            if first is None:
                break
            header_box.remove(first)
        header_box.append(widgets.page_header(
            "Journal", journal_caption(len(groups), total_steps),
            overflow_cb=menu if shell_page.settings_items(config, nav)
            else None))
        while True:
            first = chips_box.get_first_child()
            if first is None:
                break
            chips_box.remove(first)
        chips_box.append(widgets.chip_bar([
            (label, value == state["filter"],
             lambda value=value: (state.update(filter=value), refresh()))
            for label, value in FILTERS
        ], spacing=4))
        while True:
            first = list_holder.get_first_child()
            if first is None:
                break
            list_holder.remove(first)
        if not groups:
            return  # an empty journal shows nothing; the FAB logs one
        item_type = _item_type()
        store = Gio.ListStore.new(item_type)
        for group in groups:
            store.append(item_type("day", group))
            for activity in group.activities:
                store.append(item_type("activity", activity))
        state["store"] = store
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda _f, item: item.set_child(
            Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)))
        factory.connect("bind", lambda _f, item: _bind_row(item))
        view = Gtk.ListView.new(Gtk.NoSelection.new(store), factory)
        view.set_single_click_activate(True)
        list_holder.append(view)

    def _bind_row(item) -> None:
        holder = item.get_child()
        previous = holder.get_first_child()
        while previous is not None:
            after = previous.get_next_sibling()
            holder.remove(previous)
            previous = after
        entry = item.get_item()
        if entry.kind == "day":
            holder.append(_day_header(entry.payload, config))
        else:
            holder.append(_activity_row(entry.payload, config, live))

    def open_first():
        try:
            activities = db.list_activities(limit=1)
        except (OSError, AttributeError):
            return None
        if not activities:
            return None
        return _open_detail(activities[0], config, live.get("window"))

    live["refresh"] = refresh
    live["menu"] = menu
    live["open_first"] = open_first
    live["window"] = None
    refresh()
    body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    body.add_css_class("xx-page-body")
    from xx_vitals.ui.home import FAB_CLEARANCE

    body.set_margin_bottom(FAB_CLEARANCE)  # the last row scrolls clear of the FAB
    body.append(chips_box)
    body.append(list_holder)
    return widgets.page(header_box, body, None, widgets.fab(nav.add_activity))


def _day_header(group, config):
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    import xx_ui
    from xx_ui import widgets

    row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    row.set_valign(Gtk.Align.CENTER)
    row.add_css_class("xx-vitals-day")
    text = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
    text.set_hexpand(True)
    text.set_valign(Gtk.Align.CENTER)
    text.append(widgets.caption(_day_title(group.date, date.today())))
    meta_bits = []
    if group.total_steps:
        meta_bits.append(f"{group.total_steps:,} steps")
    if group.total_effort:
        meta_bits.append(f"{group.total_effort} pts")
    if meta_bits:
        meta = widgets.wrap_label(" · ".join(meta_bits), "xx-secondary")
        text.append(meta)
    row.append(text)
    ring = Gtk.DrawingArea()
    ring.set_content_width(MINI_PX)
    ring.set_content_height(MINI_PX)
    ring.set_valign(Gtk.Align.CENTER)
    ring.set_draw_func(_draw_mini, group.ring_fraction)
    xx_ui.on_change(lambda _theme: ring.queue_draw())
    row.append(ring)
    return row


def _activity_row(activity, config, live):
    from xx_ui import widgets

    kind = (activity.type or "").strip().lower()
    if kind == journal_model.PHONE_STEPS_TYPE.lower():
        glyph = "S"
    elif kind == "walk":
        glyph = "W"
    elif kind == "run":
        glyph = "R"
    elif kind == "cycle":
        glyph = "C"
    else:
        glyph = (kind[:1].upper() if kind else "•")
    return widgets.list_row(
        activity_title(activity),
        activity_line(activity, config.units),
        leading=widgets.avatar(glyph, 40),
        on_activate=lambda: _open_detail(activity, config, live.get("window")))


def _open_detail(activity, config, window) -> None:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw

    from xx_ui import widgets

    dialog = Adw.Dialog(title=activity_title(activity))
    box = widgets.dialog_box(activity_title(activity))
    span = (_clock_text(activity.started_at) + " – "
            + _clock_text(activity.ended_at))
    box.append(widgets.caption(span))
    from xx_vitals.core import home as home_model

    units = config.units
    rows = [("Duration", _span_text(activity.started_at, activity.ended_at))]
    if activity.distance_m:
        rows.append(("Distance",
                     home_model.format_distance(activity.distance_m, units)
                     + " " + home_model.distance_label(units)))
    if activity.calories_kcal is not None:
        rows.append(("Energy expended",
                     f"{home_model.format_calories(activity.calories_kcal)} Cal"))
    if activity.active_min is not None:
        rows.append(("Move Minutes", str(activity.active_min)))
    if activity.steps:
        rows.append(("Steps", f"{activity.steps:,}"))
    if activity.hr_avg:
        rows.append(("Heart rate", f"{activity.hr_avg} bpm (average)"))
    for title, value in rows:
        box.append(widgets.field(title, value))
    close = widgets.primary_button("Close", dialog.close)
    box.append(close)
    from xx_vitals.ui import entry as entry_page

    entry_page.fit_dialog(dialog, box)
    if window is not None:
        dialog.present(window)
    return dialog
