"""Browse tab: search over six categories with honest "not tracked" rows.

Category data lives in core/browse; series math in core/detail. Category and
metric rows render through Gtk.ListView; charts are Cairo bars on
Gtk.DrawingArea reading the live xx_ui theme (no new dependencies).
"""

from __future__ import annotations

from xx_vitals.core import browse as browse_model
from xx_vitals.core import detail as detail_model

CHART_W = 272
CHART_H = 150

ZOOMS = ("Day", "Week", "Month", "Year")

METRIC_LETTERS = {
    "steps": "S", "distance": "D", "active_min": "A", "effort": "E",
    "weight": "W", "bmi": "B",
}

CATEGORY_LETTERS = {
    "activity": "A", "body": "B", "hr": "H", "nutrition": "N",
    "sleep": "S", "cycle": "C",
}


def visible_rows(query: str) -> list:
    return browse_model.search_rows(query)


def detail_series(dailies, zoom_name: str, metric: str, zone=None):
    zoom = detail_model.Zoom(zoom_name)
    extractor, accumulates = _metric_extractor(metric, zone)
    return detail_model.aggregate(dailies, zoom, extractor, accumulates=accumulates)


def _metric_extractor(metric: str, zone=None):
    if metric == "steps":
        return lambda row: float(row.steps) if row.steps is not None else None, True
    if metric == "distance":
        return lambda row: row.distance_m, True
    if metric == "active_min":
        return lambda row: float(row.active_min) if row.active_min is not None else None, True
    if metric == "effort":
        return lambda row: float(row.effort_points) if row.effort_points is not None else None, True
    if metric == "weight":
        return lambda row: row.weight_avg_kg, False
    if metric == "bmi":
        return _bmi_extractor(), False
    return lambda row: None, True


def _bmi_extractor():
    def extract(row):
        height = getattr(row, "height_metres", None)
        weight = row.weight_avg_kg
        if height is None or not height or weight is None:
            return None
        return weight / (height * height)

    return extract


def weight_rows(samples, zone):
    return detail_model.daily_weight_projection(samples, zone)


def _item_type():
    """ListView payload type: widgets are built in bind(), so a hidden
    page never pays for its rows in size negotiation."""
    from gi.repository import GObject

    class Item(GObject.Object):
        def __init__(self, payload) -> None:
            super().__init__()
            self.payload = payload

    return Item


def metric_label(metric: str) -> str:
    try:
        return browse_model.METRIC_LABELS[browse_model.MetricKey(metric)]
    except (ValueError, KeyError):
        return metric.capitalize()


def latest_value_text(metric: str, dailies, weights, config) -> str:
    from xx_vitals.core import format as format_model
    from xx_vitals.core import home as home_model

    if metric == "weight":
        if not weights:
            return "—"
        return format_model.format_weight(weights[0].weight_kg, config.units)
    if metric == "bmi":
        value = _bmi_latest(dailies, weights, config)
        return f"{value:.1f}" if value is not None else "—"
    if not dailies:
        return "—"
    for row in dailies:
        if metric == "steps" and row.steps is not None:
            return f"{row.steps:,}"
        if metric == "distance" and row.distance_m is not None:
            return (home_model.format_distance(row.distance_m, config.units)
                    + " " + home_model.distance_label(config.units))
        if metric == "active_min" and row.active_min is not None:
            return str(row.active_min)
        if metric == "effort" and row.effort_points is not None:
            return str(row.effort_points)
    return "—"


def _bmi_latest(dailies, weights, config):
    if not config.height_metres or not weights:
        return None
    return weights[0].weight_kg / (config.height_metres ** 2)


def _draw_chart(area, cairo, _width, _height, payload) -> None:
    import xx_ui

    theme = xx_ui.theme.load()
    from gi.repository import Gdk

    def paint(hex_color: str):
        rgba = Gdk.RGBA()
        rgba.parse(hex_color)
        return (rgba.red, rgba.green, rgba.blue, rgba.alpha)

    series, _accumulates = payload
    values = [b.value for b in series.buckets]
    pad = 8.0
    base = CHART_H - 22.0
    top = 10.0
    span = max(1e-9, (series.axis_max - series.axis_min) or 1.0)
    n = max(1, len(values))
    slot = (CHART_W - pad * 2) / n
    bar_w = max(2.0, min(14.0, slot - 4.0))
    for index, value in enumerate(values):
        if value is None:
            continue
        height = 4.0 + (value - series.axis_min) / span * (base - top - 4.0)
        x = pad + index * slot + (slot - bar_w) / 2
        cairo.set_source_rgba(*paint(theme.foreground))
        cairo.rectangle(x, base - height, bar_w, height)
        cairo.fill()
    cairo.set_source_rgba(*paint(theme.border))
    cairo.set_line_width(1.0)
    cairo.move_to(pad, base + 0.5)
    cairo.line_to(CHART_W - pad, base + 0.5)
    cairo.stroke()


def _chart(series, accumulates: bool):
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    import xx_ui

    area = Gtk.DrawingArea()
    area.set_content_width(CHART_W)
    area.set_content_height(CHART_H)
    area.set_draw_func(_draw_chart, (series, accumulates))
    xx_ui.on_change(lambda _theme: area.queue_draw())
    return area


def build_browse_page(db, config, nav, live: dict | None = None) -> object:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gio, Gtk

    from xx_ui import widgets

    from xx_vitals.ui import shell as shell_page

    live = live if live is not None else {}
    state = {"query": "", "zoom": "Week", "stack": None,
             "level": "cats", "metric": "steps", "overflow": None}
    header_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    search_row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    search_row.append(widgets.icon_image("system-search-symbolic"))
    search_entry = widgets.deferred_entry("Search metrics")
    search_entry.set_hexpand(True)
    search_entry.connect("changed",
                         lambda _e: (state.update(query=search_entry.get_text()),
                                     refresh()))
    search_row.append(search_entry)
    inner = Gtk.Stack()
    state["stack"] = inner
    cats_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    metrics_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    detail_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)
    inner.add_named(cats_box, "cats")
    inner.add_named(metrics_box, "metrics")
    inner.add_named(detail_box, "detail")

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

    def _header(back_cb, title: str, subtitle: str = ""):
        header = widgets.compact_header(back_cb, title, subtitle)
        # The browse body already carries the gutter: no second one.
        header.remove_css_class("xx-page-body")
        return header

    def to_cats() -> None:
        state["level"] = "cats"
        inner.set_visible_child_name("cats")

    def back() -> bool:
        """Detail -> the metric list it came from -> categories."""
        if state["level"] == "detail" and state.get("from_metrics"):
            state["level"] = "metrics"
            inner.set_visible_child_name("metrics")
            return True
        if state["level"] in ("detail", "metrics"):
            to_cats()
            return True
        return False

    def open_category(category) -> None:
        if not category.tracked:
            return  # a "not tracked" row is information, not a door
        if len(category.metrics) == 1:
            open_metric(category.metrics[0].value)
            return
        _fill_box(metrics_box, _header(to_cats, category.label))
        for index, key in enumerate(category.metrics):
            metrics_box.append(widgets.list_row(
                browse_model.METRIC_LABELS[key],
                latest_value_text(key.value, _dailies(), _weights(), config),
                leading=widgets.avatar(METRIC_LETTERS.get(key.value, "•"), 40),
                on_activate=lambda key=key: open_metric(key.value, True),
                last=index == len(category.metrics) - 1))
        state["level"] = "metrics"
        inner.set_visible_child_name("metrics")

    def open_metric(metric: str, from_metrics: bool = False) -> None:
        state["metric"] = metric
        state["from_metrics"] = from_metrics
        _show_detail()
        state["level"] = "detail"
        inner.set_visible_child_name("detail")

    def _show_detail() -> None:
        metric = state["metric"]
        _fill_box(detail_box, _header(
            lambda: back(), metric_label(metric), _range_caption()))
        series, action = _series_for(metric)
        if series is None:
            # No data: nothing to read or zoom, at most the one action
            # that adds some.
            if action is not None:
                button = widgets.outline_button(*action)
                button.set_hexpand(True)
                detail_box.append(button)
            return
        detail_box.append(widgets.segmented([
            (zoom, zoom == state["zoom"],
             lambda zoom=zoom: (state.update(zoom=zoom), _show_detail()))
            for zoom in ZOOMS
        ]))
        detail_box.append(_chart(series, _accumulates(metric)))
        detail_box.append(_axis_labels(series))
        for title, value in _summary_fields(metric, series):
            detail_box.append(widgets.field(title, value))

    def _range_caption() -> str:
        try:
            rows = db.list_dailies(limit=365)
        except (OSError, AttributeError):
            rows = []
        if not rows:
            return ""
        return range_caption(rows[-1].date, rows[0].date)

    def _series_for(metric: str):
        try:
            dailies = db.list_dailies(limit=365)
        except (OSError, AttributeError):
            dailies = []
        try:
            samples = db.list_weights(limit=365)
        except (OSError, AttributeError):
            samples = []
        if metric == "weight":
            rows = weight_rows(samples, None)
            if not any(row.weight_avg_kg is not None for row in rows):
                return None, ("Log weight", nav.add_weight)
            height = getattr(config, "height_metres", 0.0) or 0.0
            for row in rows:
                row.height_metres = height
            return detail_series(rows, state["zoom"].lower(), "weight"), None
        if metric == "bmi":
            if not getattr(config, "height_metres", 0.0):
                return None, ("Set height", lambda: nav.show("profile"))
            rows = weight_rows(samples, None)
            height = config.height_metres
            for row in rows:
                row.height_metres = height
            if not any(_bmi_extractor()(row) is not None for row in rows):
                return None, ("Log weight", nav.add_weight)
            return detail_series(rows, state["zoom"].lower(), "bmi"), None
        extract, _acc = _metric_extractor(metric)
        if not any(extract(row) is not None for row in dailies):
            return None, None
        return detail_series(dailies, state["zoom"].lower(), metric), None

    def _dailies():
        try:
            return db.list_dailies(limit=60)
        except (OSError, AttributeError):
            return []

    def _weights():
        try:
            return db.list_weights(limit=60)
        except (OSError, AttributeError):
            return []

    def refresh() -> None:
        while True:
            first = header_box.get_first_child()
            if first is None:
                break
            header_box.remove(first)
        header_box.append(widgets.page_header(
            "Browse", None,
            overflow_cb=menu if shell_page.settings_items(config, nav)
            else None))
        _fill_box(cats_box, None)
        state["level"] = "cats"
        inner.set_visible_child_name("cats")
        rows = visible_rows(state["query"])
        if not rows:
            return  # no matches: the list is simply empty
        item_type = _item_type()
        store = Gio.ListStore.new(item_type)
        for category in rows:
            store.append(item_type(category))
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda _f, item: item.set_child(
            Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)))
        factory.connect("bind", lambda _f, item: _bind_row(item))
        view = Gtk.ListView.new(Gtk.NoSelection.new(store), factory)
        view.set_single_click_activate(True)
        cats_box.append(view)
        inner.set_visible_child_name("cats")

    def _bind_row(item) -> None:
        holder = item.get_child()
        previous = holder.get_first_child()
        while previous is not None:
            after = previous.get_next_sibling()
            holder.remove(previous)
            previous = after
        holder.append(_category_row(item.get_item().payload, open_category))

    live["refresh"] = refresh
    live["menu"] = menu
    live["open_metric"] = open_metric
    live["back"] = back
    live["open_category"] = lambda category_id: open_category(next(
        c for c in browse_model.search_rows("") if c.id == category_id))
    refresh()
    body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    body.add_css_class("xx-page-body")
    body.append(search_row)
    body.append(inner)
    return widgets.page(header_box, body, None)


def range_caption(first_iso: str, last_iso: str) -> str:
    """Short data range for the detail header: "Sep 18 – Oct 17", with
    years only when the range crosses one."""
    from datetime import date

    try:
        first = date.fromisoformat(first_iso[:10])
        last = date.fromisoformat(last_iso[:10])
    except ValueError:
        return f"{first_iso} – {last_iso}"

    def short(day) -> str:
        return f"{day.strftime('%b')} {day.day}"

    if first.year != last.year:
        return f"{short(first)} {first.year} – {short(last)} {last.year}"
    return f"{short(first)} – {short(last)}"


def _fill_box(box, header) -> None:
    while True:
        first = box.get_first_child()
        if first is None:
            break
        box.remove(first)
    if header is not None:
        box.append(header)


def _category_row(category, open_category):
    from xx_ui import widgets

    if category.tracked:
        names = [browse_model.METRIC_LABELS[key] for key in category.metrics]
        if len(names) > 2:
            secondary = f"{names[0]} · {names[1]} · +{len(names) - 2}"
        else:
            secondary = " · ".join(names)
        meta = ""
    else:
        secondary = browse_model.NOT_TRACKED_TAG.capitalize()
        meta = ""
    letter = CATEGORY_LETTERS.get(category.id, category.label[:1].upper())
    row = widgets.list_row(
        category.label, secondary, meta=meta,
        leading=widgets.avatar(letter, 40),
        on_activate=(lambda: open_category(category))
        if category.tracked else None)
    # A "not tracked" row reads as a fact: no press state, no dead end.
    row.set_can_target(category.tracked)
    return row


def _accumulates(metric: str) -> bool:
    return metric not in ("weight", "bmi")


def _axis_labels(series) -> object:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk, Pango

    from xx_ui import widgets

    row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    labels = [b.label for b in series.buckets if b.value is not None]
    first = widgets.caption(labels[0] if labels else "—")
    first.set_hexpand(True)
    row.append(first)
    values = [b.value for b in series.buckets if b.value is not None]
    peak = widgets.caption(f"Max {_compact(max(values))}" if values else "—")
    # Allocated exactly its natural width: never misfire on rounding.
    peak.set_ellipsize(Pango.EllipsizeMode.NONE)
    row.append(peak)
    return row


def _compact(value) -> str:
    if value is None:
        return "—"
    if value >= 1000:
        return f"{value:,.0f}"
    if value == int(value):
        return str(int(value))
    return f"{value:.1f}"


def _summary_fields(metric: str, series) -> list:
    values = [b.value for b in series.buckets if b.value is not None]
    if not values:
        return []
    if _accumulates(metric):
        total = sum(values)
        best = max(values)
        best_label = next(
            b.label for b in series.buckets if b.value == best)
        return [("Total", _compact(total)),
                ("Daily average", _compact(total / len(values))),
                ("Best", f"{_compact(best)} · {best_label}")]
    latest = values[-1]
    return [("Latest", _compact(latest)),
            ("Average", _compact(sum(values) / len(values))),
            ("Change", _signed(latest - values[0]))]


def _signed(value: float) -> str:
    text = _compact(abs(value))
    if value > 0:
        return f"+{text}"
    if value < 0:
        return f"−{text}"
    return text
