"""Home tab: dual ring, stat row, goal cards.

Pure math lives in core/home + core/metrics; this module only builds widgets.
Rings and dots are Cairo on Gtk.DrawingArea reading the live xx_ui theme and
redrawing on every theme switch, so paper (light) stays legible too.
"""

from __future__ import annotations

from datetime import date

from xx_vitals.core import home as home_model

RING_PX = 248
#: Room under a FAB page's last row: 56 px FAB + its 20 px margin.
FAB_CLEARANCE = 76
DOT_PX = 22


def day_summary(metric, units: str, steps_goal: int, effort_goal: int) -> dict:
    steps = metric.steps if metric is not None else None
    effort = metric.effort_points if metric is not None else None
    mask = metric.derived_mask if metric is not None else 0
    return {
        "has_data": home_model.has_data(metric) if metric is not None else False,
        "steps_fraction": _fraction(steps, steps_goal),
        "effort_fraction": _fraction(effort, effort_goal),
        "steps_text": _grouped(steps),
        "effort_text": _grouped(effort),
        "calories": home_model.format_calories(metric.calories_kcal if metric else None),
        "calories_prefix": home_model.derived_prefix(home_model.COLUMN_CALORIES, mask),
        "distance": home_model.format_distance(metric.distance_m if metric else None, units),
        "distance_label": home_model.distance_label(units),
        "distance_prefix": home_model.derived_prefix(home_model.COLUMN_DISTANCE, mask),
        "active_min": home_model.format_active_min(metric.active_min if metric else None),
    }


def _grouped(value) -> str:
    return f"{int(value):,}" if value is not None else "—"


def _fraction(actual, target: int) -> float:
    from xx_vitals.core import metrics

    if actual is None:
        return 0.0
    return metrics.ring_fraction(float(actual), float(target))


def collect_status(config, hardware_available: bool = False,
                   accelerometer_available: bool = False) -> dict:
    from xx_vitals.collect import status as collect_status

    described = collect_status.describe(
        bool(getattr(config, "collect_enabled", False)),
        hardware_available, accelerometer_available,
    )
    return {
        "title": described.title, "detail": described.detail,
        "enabled": described.enabled, "has_source": described.has_source,
    }


def _colors():
    import xx_ui

    active = xx_ui.theme.load()
    return active


def _rgba(hex_color: str):
    from gi.repository import Gdk

    rgba = Gdk.RGBA()
    rgba.parse(hex_color)
    return rgba


def _draw_rings(area, cairo, _width, _height, data) -> None:
    theme = _colors()
    cx = cy = RING_PX / 2
    track, steps_ink, effort_ink = (
        _rgba(theme.surface_alt), _rgba(theme.foreground), _rgba(theme.accent))
    cairo.set_line_width(14)
    cairo.set_line_cap(1)  # round caps
    for radius in (100.0, 78.0):
        cairo.set_source_rgba(*_channels(track))
        cairo.arc(cx, cy, radius, 0, 6.2832)
        cairo.stroke()
    for radius, fraction, ink in (
            (100.0, data["effort_fraction"], effort_ink),
            (78.0, data["steps_fraction"], steps_ink)):
        if fraction <= 0:
            continue
        cairo.set_source_rgba(*_channels(ink))
        cairo.arc(cx, cy, radius, -1.5708, -1.5708 + fraction * 6.2832)
        cairo.stroke()


def _channels(rgba) -> tuple:
    return (rgba.red, rgba.green, rgba.blue, rgba.alpha)


def _draw_dot(area, cairo, _width, _height, data) -> None:
    theme = _colors()
    mode, ink = data
    cx = cy = DOT_PX / 2
    if mode == "hit":
        paint = _rgba(theme.foreground)
        cairo.set_source_rgba(*_channels(paint))
        cairo.arc(cx, cy, 9.0, 0, 6.2832)
        cairo.fill()
    else:
        paint = _rgba(theme.muted if mode == "miss" else theme.border)
        cairo.set_source_rgba(*_channels(paint))
        cairo.set_line_width(2.0)
        cairo.arc(cx, cy, 8.0, 0, 6.2832)
        cairo.stroke()


def _draw_swatch(area, cairo, _width, _height, role: str) -> None:
    theme = _colors()
    paint = _rgba(theme.accent if role == "accent" else theme.foreground)
    cairo.set_source_rgba(*_channels(paint))
    cairo.arc(7.0, 7.0, 5.0, 0, 6.2832)
    cairo.fill()


def _drawing(px: int, draw, data=None):
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    import xx_ui

    area = Gtk.DrawingArea()
    area.set_content_width(px)
    area.set_content_height(px)
    area.set_draw_func(draw, data)
    area.set_halign(Gtk.Align.CENTER)
    xx_ui.on_change(lambda _theme: area.queue_draw())
    return area


def build_home_page(db, config, nav, live: dict | None = None) -> object:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from xx_ui import widgets

    from xx_vitals.ui import shell as shell_page

    live = live if live is not None else {}
    body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)
    state: dict = {"overflow": None}
    header_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)

    def menu() -> object:
        if state["overflow"] is not None:
            state["overflow"].popup()
            return state["overflow"]
        anchor = header_box.get_first_child()
        popover = shell_page.open_overflow(anchor, home_menu_items(config, nav))
        state["overflow"] = popover
        popover.connect("closed", lambda _p: state.update(overflow=None))
        return popover

    def refresh() -> None:
        child = body.get_first_child()
        while child is not None:
            after = child.get_next_sibling()
            body.remove(child)
            child = after
        _fill_body(body, db, config, nav)
        while True:
            first = header_box.get_first_child()
            if first is None:
                break
            header_box.remove(first)
        header_box.append(widgets.page_header(
            "Vitals", _today_caption(), overflow_cb=menu))

    live["refresh"] = refresh
    live["menu"] = menu
    refresh()
    body.add_css_class("xx-page-body")
    body.set_margin_bottom(FAB_CLEARANCE)  # the last card scrolls clear of the FAB
    return widgets.page(header_box, body, None, widgets.fab(nav.add_activity))


def home_menu_items(config, nav) -> list:
    """Home's overflow: Log weight (the FAB logs an activity), plus
    Settings only while the Profile tab is hidden (otherwise the tab is
    the one way there)."""
    from xx_vitals.ui import shell as shell_page

    return [("Log weight", nav.add_weight)] + shell_page.settings_items(
        config, nav)


def _today_caption() -> str:
    today = date.today()
    return f"{today.strftime('%A, %B')} {today.day}"


def _fill_body(body, db, config, nav) -> None:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from xx_ui import widgets

    try:
        dailies = db.list_dailies(limit=30)
    except (OSError, AttributeError):
        dailies = []
    today = date.today()
    latest = None
    for row in dailies:
        try:
            if date.fromisoformat(row.date[:10]) <= today and (
                    latest is None or row.date > latest.date):
                latest = row
        except (ValueError, AttributeError):
            continue
    summary = day_summary(latest, config.units, config.steps_goal,
                          config.effort_goal)

    ring_wrap = Gtk.Overlay()
    ring_wrap.set_halign(Gtk.Align.CENTER)
    ring_wrap.set_child(_drawing(RING_PX, _draw_rings, summary))
    center = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    center.set_halign(Gtk.Align.CENTER)
    center.set_valign(Gtk.Align.CENTER)
    effort_label = widgets.label(summary["effort_text"], "xx-display-large")
    effort_label.add_css_class("xx-accent-text")
    effort_label.set_xalign(0.5)
    steps_label = widgets.label(summary["steps_text"], "xx-title")
    steps_label.set_xalign(0.5)
    center.append(effort_label)
    center.append(steps_label)
    ring_wrap.add_overlay(center)
    body.append(ring_wrap)
    body.append(_legend_row())
    body.append(_stat_row(summary))

    if not getattr(config, "collect_enabled", False):
        body.append(_collect_off_card(config, nav))

    body.append(_goals_card(dailies, today, config, nav))
    body.append(_weekly_card(dailies, today, config, nav))
    weight_card = _weight_card(db, config, nav)
    if weight_card is not None:
        body.append(weight_card)


def _collect_off_card(config, nav):
    """Step counting is off: the state and the one action that fixes it."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from xx_ui import widgets

    def turn_on() -> None:
        from xx_vitals.ui import profile as profile_page

        profile_page.set_collect_enabled(config, True)
        nav.refresh_all()

    card = widgets.card()
    row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    line = widgets.wrap_label(collect_status(config)["title"], "xx-title")
    line.set_hexpand(True)
    line.set_valign(Gtk.Align.CENTER)
    row.append(line)
    button = widgets.outline_button("Turn on", turn_on)
    button.set_valign(Gtk.Align.CENTER)
    row.append(button)
    card.append(row)
    return card


def _legend_row():
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk, Pango

    from xx_ui import widgets

    row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    row.set_homogeneous(True)
    for text, role in (("Effort", "accent"), ("Steps", "foreground")):
        cell = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 6)
        cell.set_halign(Gtk.Align.CENTER)
        cell.set_valign(Gtk.Align.CENTER)
        swatch = _drawing(14, _draw_swatch, role)
        cell.append(swatch)
        tag = widgets.caption(text)
        # Allocated exactly its natural width: an ellipsis would misfire
        # on letter-spacing rounding, a sub-pixel clip stays invisible.
        tag.set_ellipsize(Pango.EllipsizeMode.NONE)
        cell.append(tag)
        row.append(cell)
    return row


def _stat_row(summary: dict):
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk, Pango

    from xx_ui import widgets

    row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    row.set_homogeneous(True)
    cells = [
        (f"{summary['calories_prefix']}{summary['calories']}", "Cal"),
        (f"{summary['distance_prefix']}{summary['distance']}",
         summary["distance_label"]),
        (summary["active_min"], "Active Min"),
    ]
    for value, unit in cells:
        cell = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
        value_label = widgets.label(value, "xx-title")
        value_label.set_xalign(0.5)
        unit_label = widgets.caption(unit)
        unit_label.set_xalign(0.5)
        unit_label.set_justify(Gtk.Justification.CENTER)
        unit_label.set_wrap(True)
        unit_label.set_ellipsize(Pango.EllipsizeMode.NONE)
        cell.append(value_label)
        cell.append(unit_label)
        row.append(cell)
    return row


def _card_title(text: str, meta: str, on_tap):
    """A card's tappable title row: wrapping title, meta at the end."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk, Pango

    from xx_ui import widgets

    row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    title_label = widgets.wrap_label(text, "xx-title")
    title_label.set_hexpand(True)
    title_label.set_valign(Gtk.Align.CENTER)
    row.append(title_label)
    if meta:
        meta_label = widgets.label(meta, "xx-title")
        meta_label.set_ellipsize(Pango.EllipsizeMode.NONE)
        meta_label.set_valign(Gtk.Align.CENTER)
        row.append(meta_label)
    button = Gtk.Button()
    button.add_css_class("xx-btn")
    button.set_child(row)
    button.connect("clicked", lambda _b: on_tap())
    return button


def _goals_card(dailies, today: date, config, nav):
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from xx_ui import widgets

    slots = home_model.last_seven_days(dailies, today)
    hits = sum(
        1 for slot in slots
        if slot.metric is not None
        and (slot.metric.effort_points or 0) >= config.effort_goal)
    card = widgets.card()
    card.append(_card_title("Your daily goals", f"{hits}/7",
                            lambda: nav.show("profile")))
    card.append(widgets.caption("Last 7 days"))
    dots = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 4)
    dots.set_homogeneous(True)
    dots.set_hexpand(True)
    for slot in slots:
        cell = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
        if slot.metric is None:
            mode = "gap"
        elif (slot.metric.effort_points or 0) >= config.effort_goal:
            mode = "hit"
        else:
            mode = "miss"
        cell.append(_drawing(DOT_PX, _draw_dot, (mode, None)))
        letter = widgets.caption(home_model.weekday_letter(slot.date))
        letter.set_xalign(0.5)
        cell.append(letter)
        dots.append(cell)
    card.append(dots)
    return card


def _weekly_card(dailies, today: date, config, nav):
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from xx_ui import widgets

    card = widgets.card()
    card.append(_card_title("Your weekly target", "",
                            lambda: nav.show("profile")))
    card.append(widgets.caption(home_model.iso_week_range_label(today)))
    current = home_model.current_iso_week_effort(dailies, today)
    card.append(widgets.label(
        home_model.weekly_progress_line(current, config.weekly_effort_goal),
        "xx-display"))
    bar = Gtk.ProgressBar()
    goal = max(1, config.weekly_effort_goal)
    bar.set_fraction(min(1.0, current / goal))
    bar.set_show_text(False)
    card.append(bar)
    return card


def _weight_card(db, config, nav):
    from xx_ui import widgets

    try:
        samples = db.list_weights(limit=1)
    except (OSError, AttributeError):
        return None
    if not samples:
        return None
    from xx_vitals.core import format as format_model

    card = widgets.card()
    card.append(widgets.caption("Latest weigh-in"))
    card.append(widgets.label(
        format_model.format_weight(samples[0].weight_kg, config.units),
        "xx-display"))
    return card
