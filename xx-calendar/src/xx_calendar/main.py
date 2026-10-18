"""Calendar app shell: xx_ui design system with Schedule/Day/Month pages.

Reads instances through the service's ``ListInstances`` operation running
in-process over the GUI's store handle (same code, identical results by
construction, no D-Bus round trip on the hot path). Every write goes
through the service — over D-Bus when the daemon owns the bus, otherwise
through the in-process handle, which is the single writer exactly when no
daemon runs. ``Changed`` (sender ``io.piercingxx.Calendar``) refreshes
pages after a 2 s debounce, like Android's ProviderObserver.
"""
from __future__ import annotations

import datetime as _datetime
import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

gi.require_version("Graphene", "1.0")

from gi.repository import (  # noqa: E402
    Adw, Gdk, Gio, GLib, GObject, Graphene, Gtk, Pango,
)

import xx_ui  # noqa: E402
from xx_ui import widgets  # noqa: E402
from xx_calendar import views  # noqa: E402
from xx_calendar.app_config import load_settings, save_settings  # noqa: E402
from xx_calendar.models import CalendarKey, SigilAssigner  # noqa: E402
from xx_calendar.month_grid import MonthGrid  # noqa: E402
from xx_calendar.service import (  # noqa: E402
    BUS_NAME,
    INTERFACE,
    OBJECT_PATH,
    CalendarService,
)
from xx_calendar.store import CalendarStore, default_db_path  # noqa: E402

APP_ID = "io.piercingxx.XXCalendar"

SWIPE_VELOCITY_PX_S = 400.0
CHANGED_DEBOUNCE_S = 2
RELOAD_MARGIN_DAYS = 14

RECURRENCE_CHOICES = views.REPEAT_CHOICES
REMINDER_CHOICES = views.REMINDER_CHOICES

#: Sub-pages a back gesture (Escape, Alt+Left) leaves.
SUB_VIEWS = ("detail", "editor", "import", "export", "search", "calendars")

FILTERS: tuple[str, ...] = ("ALL", "TIMED", "ALL-DAY")


def parse_launch_args(argv: list[str]) -> dict:
    """Split our flags from *argv*; unparseable input opens Schedule."""
    opts: dict = {"date": None, "today": False, "new_event": False,
                  "opens": []}
    rest = list(argv)
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg.startswith("--date="):
            opts["date"] = views.parse_date_arg(arg[len("--date="):])
            rest.pop(i)
        elif arg == "--date" and i + 1 < len(rest):
            opts["date"] = views.parse_date_arg(rest[i + 1])
            rest.pop(i)
            rest.pop(i)
        elif arg in ("--today", "-t"):
            opts["today"] = True
            rest.pop(i)
        elif arg in ("--new-event", "--new_event"):
            opts["new_event"] = True
            rest.pop(i)
        else:
            if not arg.startswith("-"):
                parsed = views.parse_app_uri(arg)
                if parsed is not None:
                    opts["date"] = parsed
                    rest.pop(i)
                    continue
                opts["opens"].append(arg)
                rest.pop(i)
                continue
            i += 1
    opts["rest"] = rest
    return opts


def local_zone():
    """System local zone as an IANA ZoneInfo (per-date DST), else fixed."""
    from zoneinfo import ZoneInfo

    names = [os.environ.get("TZ", "").lstrip(":").strip()]
    try:
        with open("/etc/timezone", encoding="utf-8") as handle:
            names.append(handle.readline().strip())
    except OSError:
        pass
    try:
        names.append(os.readlink("/etc/localtime").partition("zoneinfo/")[2])
    except OSError:
        pass
    for name in filter(None, names):
        try:
            return ZoneInfo(name)
        except Exception:
            continue
    return _datetime.datetime.now().astimezone().tzinfo or _datetime.timezone.utc


def parse_sheet_datetime(text: str, all_day: bool,
                         zone) -> int | None:
    """Parse create-sheet ``YYYY-MM-DD[ HH:MM]`` to UTC millis, else None."""
    cleaned = (text or "").strip()
    try:
        if all_day:
            day = _datetime.date.fromisoformat(cleaned)
            moment = _datetime.datetime(day.year, day.month, day.day,
                                        tzinfo=_datetime.timezone.utc)
        else:
            naive = _datetime.datetime.strptime(cleaned, "%Y-%m-%d %H:%M")
            moment = naive.replace(tzinfo=zone)
    except ValueError:
        return None
    try:
        return int(moment.timestamp() * 1000)
    except (OverflowError, OSError):
        return None


class DayHeaderItem(GObject.Object):
    label = GObject.Property(type=str, default="")


class EventItem(GObject.Object):
    uid = GObject.Property(type=str, default="")
    title = GObject.Property(type=str, default="")
    when = GObject.Property(type=str, default="")
    sigil = GObject.Property(type=str, default="·")
    dimmed = GObject.Property(type=bool, default=False)
    start = GObject.Property(type=GObject.TYPE_INT64, default=0)
    end = GObject.Property(type=GObject.TYPE_INT64, default=0)
    recurrence_id = GObject.Property(type=GObject.TYPE_INT64, default=0)


def agenda_list_model(days: list, dim_past: bool,
                      now_ms: int) -> Gio.ListStore:
    """Flat header/row model for a ListView; rows ellipsize downstream."""
    model = Gio.ListStore.new(GObject.Object)
    for day in days:
        header = DayHeaderItem()
        header.props.label = views.day_header(day.date)
        model.append(header)
        for row in list(day.all_day) + list(day.timed):
            item = EventItem()
            item.props.uid = row.uid
            item.props.title = row.title or views.UNTITLED
            item.props.start = int(row.start_ms)
            item.props.end = int(row.end_ms)
            item.props.recurrence_id = int(row.recurrence_id)
            item.props.sigil = views.sigil_for_tier(row.sigil_tier)
            if row.all_day:
                item.props.when = views.ALL_DAY_LABEL
            else:
                item.props.when = ""
            if row.end_ms > row.start_ms:
                past = row.end_ms < now_ms
            else:
                past = row.start_ms < now_ms
            item.props.dimmed = bool(dim_past and past)
            model.append(item)
    return model


class ChoiceChips:
    """One-of-N picker as a scrollable chip row (inverted selected chip,
    xx_ui ``.xx-chip``): every option visible and one tap away, styled
    and scaled by the theme, unlike a Gtk.DropDown's own popover list."""

    def __init__(self, labels: list[str], selected: int = 0,
                 on_change=None) -> None:
        self._buttons: list[Gtk.Button] = []
        self._selected = max(0, min(int(selected), len(labels) - 1))
        self._on_change = on_change
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for index, text in enumerate(labels):
            chip = Gtk.Button.new_with_label(text.upper())
            chip.add_css_class("xx-chip")
            chip.connect("clicked", lambda _b, i=index: self.set_selected(i))
            box.append(chip)
            self._buttons.append(chip)
        self.widget = Gtk.ScrolledWindow()
        self.widget.set_policy(Gtk.PolicyType.AUTOMATIC,
                               Gtk.PolicyType.NEVER)
        self.widget.set_child(box)
        self._paint()

    def _paint(self) -> None:
        for index, chip in enumerate(self._buttons):
            if index == self._selected:
                chip.add_css_class("selected")
            else:
                chip.remove_css_class("selected")

    def get_selected(self) -> int:
        return self._selected

    def set_selected(self, index: int) -> None:
        if not 0 <= index < len(self._buttons):
            return
        changed = index != self._selected
        self._selected = index
        self._paint()
        if changed and self._on_change is not None:
            self._on_change(index)

    def labels(self) -> list[str]:
        return [chip.get_label() or "" for chip in self._buttons]


def _labelled(caption_text: str, child: Gtk.Widget) -> Gtk.Box:
    """Caption over a control: what an entry or picker holds stays named
    once it is filled in."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.append(widgets.caption(caption_text))
    box.append(child)
    return box


def _two_lines(row: Gtk.Widget) -> Gtk.Widget:
    """Let a setting row's title and subtitle wrap to two lines each, so
    large text on a narrow phone reads "Dim past events", not "Dim pa…"."""
    for widget in _descendants(row):
        if isinstance(widget, Gtk.Label) and (
                widget.has_css_class("xx-title")
                or widget.has_css_class("xx-secondary")):
            widget.set_wrap(True)
            widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            widget.set_lines(2)
            widget.set_ellipsize(Pango.EllipsizeMode.END)
    return row


def _descendants(widget: Gtk.Widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _descendants(child)
        child = child.get_next_sibling()


def _button_pair(left: Gtk.Widget, right: Gtk.Widget) -> Gtk.Box:
    """Full-width equal pair (Cancel/Save, Edit/Copy), gutter to gutter."""
    pair = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    for button in (left, right):
        button.set_hexpand(True)
        pair.append(button)
    return pair


class CalendarApp(Adw.Application):
    def __init__(self, launch: dict | None = None,
                 db_path: str | None = None) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_OPEN,
        )
        self._launch = launch or {"date": None, "today": False,
                                  "new_event": False, "opens": []}
        home = os.path.expanduser("~")
        self._db_path = db_path or default_db_path(home)
        self._window = None

    def do_activate(self) -> None:
        self._ensure_window()
        assert self._window is not None
        self._window.apply_launch(self._launch, initial=True)
        self._window.present()

    def do_open(self, files, _hint) -> None:
        self._ensure_window()
        assert self._window is not None
        for info in files:
            uri = info.get_uri() if hasattr(info, "get_uri") else None
            if uri and views.parse_app_uri(uri) is not None:
                self._window.jump_to(views.parse_app_uri(uri))
            elif uri and uri.lower().endswith(".ics"):
                self._window.import_ics_uri(uri)
        self._window.present()

    def _ensure_window(self) -> None:
        if self._window is None:
            self._window = CalendarWindow(self, self._db_path)


class CalendarWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, db_path: str) -> None:
        super().__init__(application=app)
        self.set_title("xx-calendar")
        self.set_default_size(360, 720)
        # Adw windows floor at 360 px wide by default: a 320 px
        # screen would cut the app off.
        self.set_size_request(320, 200)
        try:
            self.set_decorated(False)
        except Exception:
            pass
        self._store = CalendarStore(db_path)
        self._service = CalendarService(self._store, start_worker=False)
        self._settings = load_settings()
        self._zone = local_zone()
        self._sigils: dict = {}
        self._schedule = views.ScheduleWindow()
        self._month_year = self._schedule.picker_month()
        self._month_selected = None
        self._day_date = _datetime.date.today()
        self._view = views.start_route(self._settings.get("default_view"))
        if self._view not in ("schedule", "month"):
            self._view = "schedule"
        self._filter = "ALL"
        self._tab_view = self._view
        # The schedule scrolls to this day once its rows exist: today on
        # first open, the jumped-to or swiped-to day after.
        self._scroll_target: _datetime.date | None = _datetime.date.today()
        self._day_markers: dict = {}
        self._editor_back = "schedule"
        self._extended_back = False
        self._search_text = ""
        self._load_generation = 0
        self._reload_pending = False
        self._bus_connection = None
        self._last_dialog = None
        self._last_instances: list[dict] = []
        self._month_counts: dict = {}
        xx_ui.apply(self)
        self._load_app_css()
        self._unsub = xx_ui.on_change(self._on_theme_changed)
        self._build_chrome()
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self._subscribe_changed()
        GLib.timeout_add_seconds(60, self._on_minute_tick)

    def _load_app_css(self) -> None:
        try:
            from pathlib import Path

            css_path = Path(__file__).with_name("calendar.css")
            text = css_path.read_text(encoding="utf-8")
            display = Gdk.Display.get_default()
            if display is None:
                return
            provider = Gtk.CssProvider()
            provider.load_from_data(text.encode("utf-8"))
            Gtk.StyleContext.add_provider_for_display(
                display, provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except Exception:
            pass

    def _on_theme_changed(self, _theme) -> None:
        try:
            grid = getattr(self, "_month_grid", None)
            if grid is not None:
                grid.queue_draw()
        except Exception:
            pass
        return None

    # ---------------------------------------------------------------- chrome

    def _build_chrome(self) -> None:
        self._header_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self._stack.set_vexpand(True)
        self._stack.set_hexpand(True)
        self._schedule_model = Gio.ListStore.new(GObject.Object)
        self._schedule_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._schedule_rows.add_css_class("xx-page-body")
        schedule_scroll = Gtk.ScrolledWindow()
        schedule_scroll.set_policy(Gtk.PolicyType.NEVER,
                                   Gtk.PolicyType.AUTOMATIC)
        schedule_scroll.set_vexpand(True)
        schedule_scroll.set_child(self._schedule_rows)
        schedule_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._schedule_chips_slot = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL)
        self._schedule_chips_slot.add_css_class("xx-page-body")
        self._schedule_chips_slot.add_css_class("xxc-chips")
        schedule_page.append(self._schedule_chips_slot)
        schedule_page.append(schedule_scroll)
        swipe = Gtk.GestureSwipe()
        swipe.connect("swipe", self._on_schedule_swipe)
        schedule_scroll.add_controller(swipe)
        schedule_scroll.connect("edge-reached", self._on_schedule_edge)
        self._schedule_scroll = schedule_scroll
        self._stack.add_named(schedule_page, "schedule")
        self._day_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._day_rows.add_css_class("xx-page-body")
        day_scroll = Gtk.ScrolledWindow()
        day_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        day_scroll.set_vexpand(True)
        day_scroll.set_child(self._day_rows)
        day_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        day_page.append(day_scroll)
        day_swipe = Gtk.GestureSwipe()
        day_swipe.connect("swipe", self._on_day_swipe)
        day_scroll.add_controller(day_swipe)
        self._stack.add_named(day_page, "day")
        month_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        month_page.add_css_class("xx-page-body")
        self._month_dow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._month_dow.add_css_class("xxc-dow-row")
        month_page.append(self._month_dow)
        self._month_grid = MonthGrid(on_select=self._on_month_day)
        self._month_grid.add_css_class("xxc-month-grid")
        month_page.append(self._month_grid)
        grid_swipe = Gtk.GestureSwipe()
        grid_swipe.connect("swipe", self._on_month_swipe)
        self._month_grid.add_controller(grid_swipe)
        self._peek_label_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._peek_label_slot.add_css_class("xxc-peek-label")
        month_page.append(self._peek_label_slot)
        self._peek_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        peek_scroll = Gtk.ScrolledWindow()
        peek_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        peek_scroll.set_vexpand(True)
        peek_scroll.set_child(self._peek_rows)
        month_page.append(peek_scroll)
        self._stack.add_named(month_page, "month")
        self._cal_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._cal_rows.add_css_class("xx-page-body")
        cal_scroll = Gtk.ScrolledWindow()
        cal_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        cal_scroll.set_vexpand(True)
        cal_scroll.set_child(self._cal_rows)
        cal_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        cal_page.append(cal_scroll)
        self._stack.add_named(cal_page, "calendars")
        search_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        search_page.add_css_class("xx-page-body")
        self._search_entry = widgets.text_entry("Search events")
        self._defocusable(self._search_entry)
        self._search_entry.connect("changed", self._on_search_changed)
        search_page.append(self._search_entry)
        self._search_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        search_scroll = Gtk.ScrolledWindow()
        search_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        search_scroll.set_vexpand(True)
        search_scroll.set_child(self._search_rows)
        search_page.append(search_scroll)
        self._stack.add_named(search_page, "search")
        self._settings_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._settings_rows.add_css_class("xx-page-body")
        settings_scroll = Gtk.ScrolledWindow()
        settings_scroll.set_policy(Gtk.PolicyType.NEVER,
                                   Gtk.PolicyType.AUTOMATIC)
        settings_scroll.set_vexpand(True)
        settings_scroll.set_child(self._settings_rows)
        settings_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        settings_page.append(settings_scroll)
        self._stack.add_named(settings_page, "settings")
        for name in ("detail", "editor", "import", "export"):
            rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            rows.add_css_class("xx-page-body")
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_vexpand(True)
            scroll.set_child(rows)
            holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            holder.append(scroll)
            self._stack.add_named(holder, name)
            setattr(self, f"_{name}_rows", rows)
        self._back_view = "schedule"
        self._detail_uid: str | None = None
        self._detail_rid = 0
        self._build_settings_rows()
        self._tabs_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._fab = widgets.fab(
            callback=lambda: self.open_create_sheet(self._fab_day()))
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self._header_slot)
        content.append(self._stack)
        content.set_vexpand(True)
        overlay = Gtk.Overlay()
        overlay.set_child(content)
        overlay.add_overlay(self._fab)
        self._fab.set_halign(Gtk.Align.END)
        self._fab.set_valign(Gtk.Align.END)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(overlay)
        overlay.set_vexpand(True)
        root.append(self._tabs_slot)
        self._toasts = Adw.ToastOverlay()
        self._toasts.set_child(root)
        self.set_content(self._toasts)
        self.show_view(self._view)

    def _defocusable(self, entry: Gtk.Entry) -> None:
        try:
            entry.set_can_focus(False)
        except Exception:
            return
        try:
            gesture = Gtk.GestureClick()
            gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            gesture.connect("pressed", self._on_entry_capture, entry)
            entry.add_controller(gesture)
        except Exception:
            pass

    def _on_entry_capture(self, _gesture, _n, _x, _y, entry) -> None:
        try:
            entry.set_can_focus(True)
            entry.grab_focus()
        except Exception:
            pass

    # ---------------------------------------------------------------- headers

    def _clear_header(self) -> None:
        child = self._header_slot.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._header_slot.remove(child)
            child = nxt

    def _refresh_header(self) -> None:
        self._clear_header()
        search = {"icon_name": "system-search-symbolic",
                  "icon_cb": lambda: self.show_view("search")}
        today = _datetime.date.today()
        if self._view == "schedule":
            focus = self._schedule.focus_date or today
            header = widgets.page_header(
                "Agenda", views.month_label(focus.year, focus.month),
                actions=self._today_action(focus != today),
                shrink_title=True, **search)
        elif self._view == "month":
            year, month = self._month_year
            header = widgets.page_header(
                views.month_title(year, month),
                actions=self._today_action(
                    (year, month) != (today.year, today.month)),
                shrink_title=True, **search)
        elif self._view == "day":
            header = widgets.page_header(
                views.day_title(self._day_date),
                actions=self._today_action(self._day_date != today),
                shrink_title=True, **search)
        elif self._view == "calendars":
            header = widgets.compact_header(
                lambda: self.show_view("settings"), "Calendars")
        elif self._view == "search":
            header = widgets.compact_header(self.go_back, "Search")
        elif self._view == "detail":
            header = widgets.compact_header(self.go_back, "Event")
        elif self._view == "editor":
            header = widgets.compact_header(
                self.go_back, getattr(self, "_editor_title", "New event"))
        elif self._view == "import":
            header = widgets.compact_header(self.go_back, "Import")
        elif self._view == "export":
            header = widgets.compact_header(self.go_back, "Export")
        else:
            header = widgets.page_header(
                "Setup", shrink_title=True)
        self._header_slot.append(header)
        self._refresh_tabs()

    def _today_action(self, away: bool) -> list:
        """``Today`` sits in the header only while the page is elsewhere."""
        return [("Today", self.go_today)] if away else []

    def go_today(self) -> None:
        today = _datetime.date.today()
        if self._view == "month":
            self._month_year = (today.year, today.month)
            self._month_selected = today
            self._day_date = today
            self._refresh_header()
            self.reload()
            return
        self.jump_to(today)

    def _go_back(self) -> None:
        target = getattr(self, "_back_view", "schedule")
        if target not in ("schedule", "day", "month", "calendars", "search",
                          "settings"):
            target = "schedule"
        self.show_view(target)

    def go_back(self) -> bool:
        """One step back, as a phone back gesture means it: a sub-page
        returns where it was opened from (the editor to the event it
        edits); a tab page stays put. True when it moved."""
        if self._view == "editor" and self._editor_back == "detail":
            self._view = "detail"
            try:
                self._stack.set_visible_child_name("detail")
            except Exception:
                pass
            self._refresh_header()
            return True
        if self._view == "search":
            self.show_view(self._tab_view)
            return True
        if self._view == "calendars":
            self.show_view("settings")
            return True
        if self._view in SUB_VIEWS:
            self._go_back()
            return True
        return False

    def _on_key(self, _controller, keyval, _code, state) -> bool:
        alt = bool(state & Gdk.ModifierType.ALT_MASK)
        if keyval in (Gdk.KEY_Escape, Gdk.KEY_Back) or (
                alt and keyval == Gdk.KEY_Left):
            return self.go_back()
        return False

    def _refresh_tabs(self) -> None:
        child = self._tabs_slot.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._tabs_slot.remove(child)
            child = nxt
        active = self._tab_view
        # Six letters at most: four tabs must fit 360 px at 1.5x text.
        # Each page title is its tab's name.
        tabs = widgets.bottom_tabs([
            ("", "Agenda", active == "schedule",
             lambda: self.show_view("schedule")),
            ("", "Day", active == "day",
             lambda: self.show_view("day")),
            ("", "Month", active == "month",
             lambda: self.show_view("month")),
            ("", "Setup", active == "settings",
             lambda: self.show_view("settings")),
        ])
        self._tabs_slot.append(tabs)
        try:
            self._fab.set_visible(self._view in ("schedule", "day", "month"))
        except Exception:
            pass

    def _fab_day(self):
        """The day a new event starts on: the day on screen."""
        if self._view == "day":
            return self._day_date
        if self._view == "month":
            return self._month_selected
        return self._schedule.focus_date

    def _refresh_chips(self) -> None:
        child = self._schedule_chips_slot.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._schedule_chips_slot.remove(child)
            child = nxt
        if self._view != "schedule":
            return
        bar = widgets.chip_bar([
            ("All", self._filter == "ALL",
             lambda: self._set_filter("ALL")),
            ("Timed", self._filter == "TIMED",
             lambda: self._set_filter("TIMED")),
            ("All-day", self._filter == "ALL-DAY",
             lambda: self._set_filter("ALL-DAY")),
        ])
        self._schedule_chips_slot.append(bar)

    def _set_filter(self, value: str) -> None:
        self._filter = value
        self._refresh_chips()
        self.reload()

    # ------------------------------------------------------------ navigation

    def show_view(self, tag: str) -> None:
        if tag not in ("schedule", "day", "month", "calendars", "search",
                       "settings", "detail", "editor", "import", "export"):
            tag = "schedule"
        if tag in ("schedule", "day", "month", "calendars", "search",
                   "settings"):
            self._back_view = tag
        if tag in ("schedule", "day", "month", "settings"):
            self._tab_view = tag
        if tag == "schedule" and self._view != "schedule" \
                and self._scroll_target is None:
            self._scroll_target = (self._schedule.focus_date
                                   or _datetime.date.today())
        if tag == "month" and self._month_selected is None:
            today = _datetime.date.today()
            if self._month_year == (today.year, today.month):
                self._month_selected = today
        self._view = tag
        try:
            self._stack.set_visible_child_name(tag)
        except Exception:
            pass
        if tag == "settings":
            self._render_settings()
        if tag == "calendars":
            self._render_calendars()
        self._refresh_header()
        self._refresh_chips()
        self.reload()

    def jump_to(self, day) -> None:
        if day is None:
            return
        self._schedule.jump_to(day)
        self._scroll_target = day
        self._month_year = (day.year, day.month)
        self._month_selected = day
        self._day_date = day
        if self._view not in ("schedule", "day", "month"):
            self.show_view("schedule")
        else:
            self._refresh_header()
            self.reload()

    def apply_launch(self, launch: dict, initial: bool = False) -> None:
        day = launch.get("date")
        if launch.get("today"):
            day = _datetime.date.today()
        if day is not None:
            self.jump_to(day)
        if launch.get("new_event") and initial:
            self.open_create_sheet(day)
        for arg in launch.get("opens", []):
            self.import_ics_uri(arg)

    def _on_search_changed(self, entry) -> None:
        try:
            self._search_text = entry.get_text()
        except Exception:
            self._search_text = ""
        if self._view == "search":
            self.reload()

    def _on_month_day(self, day) -> None:
        if self._month_selected == day:
            self._day_date = day
            self.show_view("day")
        else:
            self._month_selected = day
            self._day_date = day
            self.reload()

    # -------------------------------------------------------------- loading

    def _visible_calendar_ids(self) -> list[str]:
        try:
            return [c.id for c in self._store.list_calendars()
                    if c.is_visible]
        except Exception:
            return []

    def _calendar_names(self) -> dict[str, str]:
        try:
            return {c.id: (c.display_name or c.id)
                    for c in self._store.list_calendars()}
        except Exception:
            return {}

    def _assign_sigils(self) -> None:
        try:
            calendars = self._store.list_calendars()
        except Exception:
            return
        assignment = SigilAssigner.assign(
            self._sigils,
            [CalendarKey(c.id, c.account_name or "") for c in calendars])
        self._sigils = dict(assignment.assignments)

    def reload(self) -> None:
        self._load_generation += 1
        generation = self._load_generation
        zone = self._zone
        if self._view == "day":
            start_ms = views.local_midnight_ms(self._day_date, zone)
            end_ms = views.local_midnight_ms(
                self._day_date + _datetime.timedelta(days=1), zone)
        elif self._view == "month":
            year, month = self._month_year
            start_ms, end_ms = views.month_window_ms(year, month, zone)
        elif self._view in ("search",):
            start_ms, end_ms = views.schedule_window_ms(self._schedule, zone)
        elif self._view in ("schedule",):
            start_ms, end_ms = views.schedule_window_ms(self._schedule, zone)
        else:
            return
        ids = self._visible_calendar_ids()
        worker = threading.Thread(
            target=self._load_in_background,
            args=(generation, start_ms, end_ms, ids),
            name="calendar-load", daemon=True)
        worker.start()

    def _load_in_background(self, generation: int, start_ms: int,
                            end_ms: int, ids: list[str]) -> None:
        try:
            instances = self._service.list_instances(start_ms, end_ms, ids)
            plain = [{key: _variant_value(value)
                      for key, value in item.items()}
                     for item in instances]
        except Exception:
            plain = []
        GLib.idle_add(self._apply_loaded, generation, plain)

    def _apply_loaded(self, generation: int, instances: list[dict]) -> bool:
        if generation != self._load_generation:
            return False
        self._assign_sigils()
        tiers = {key.calendar_id: _tier_index(tier)
                 for key, tier in self._sigils.items()}
        for item in instances:
            item["sigil_tier"] = tiers.get(item.get("calendar_id", ""), 0)
        now_ms = int(_datetime.datetime.now(
            _datetime.timezone.utc).timestamp() * 1000)
        dim_past = bool(self._settings.get("dim_past", True))
        zone = self._zone
        self._last_instances = instances
        if self._view == "schedule":
            days = views.group_agenda(instances, zone,
                                      self._schedule.start_date,
                                      self._schedule.end_date)
            if self._filter == "TIMED":
                days = [views.AgendaDay(date=d.date, all_day=[],
                                        timed=list(d.timed)) for d in days]
                days = [d for d in days if d.timed]
            elif self._filter == "ALL-DAY":
                days = [views.AgendaDay(date=d.date,
                                        all_day=list(d.all_day), timed=[])
                        for d in days]
                days = [d for d in days if d.all_day]
            model = agenda_list_model(days, dim_past, now_ms)
            self._schedule_model.remove_all()
            for i in range(model.get_n_items()):
                self._schedule_model.append(model.get_item(i))
            keep = self._scroll_anchor()
            self._day_markers = self._render_rows(
                self._schedule_rows, days, dim_past, now_ms)
            if self._scroll_target is not None:
                GLib.idle_add(self._scroll_schedule_to_focus)
            elif keep is not None:
                GLib.idle_add(self._restore_scroll_anchor, keep)
        elif self._view == "day":
            days = views.group_agenda(instances, zone, self._day_date,
                                      self._day_date)
            model = agenda_list_model(days, dim_past, now_ms)
            self._schedule_model.remove_all()
            for i in range(model.get_n_items()):
                self._schedule_model.append(model.get_item(i))
            # The page title already names the day: no separator under it.
            self._render_rows(self._day_rows, days, dim_past, now_ms,
                              headers=False)
        elif self._view == "search":
            query = (self._search_text or "").strip().lower()
            days = views.group_agenda(instances, zone,
                                      self._schedule.start_date,
                                      self._schedule.end_date)
            if query:
                kept = []
                for day in days:
                    all_day = [r for r in day.all_day
                               if query in (r.title or "").lower()]
                    timed = [r for r in day.timed
                             if query in (r.title or "").lower()]
                    if all_day or timed:
                        kept.append(views.AgendaDay(date=day.date,
                                                   all_day=all_day,
                                                   timed=timed))
                days = kept
            else:
                days = []
            self._render_rows(self._search_rows, days, dim_past, now_ms)
        else:
            self._render_month(instances, now_ms, dim_past)
        self._refresh_header()
        return False

    def _scroll_anchor(self) -> tuple[str, float] | None:
        """Where the schedule is scrolled, so a reload keeps the view
        still: measured from the end after rows were prepended above (an
        extend backward), from the top otherwise."""
        try:
            adj = self._schedule_scroll.get_vadjustment()
        except Exception:
            return None
        back, self._extended_back = self._extended_back, False
        if back:
            return ("end", adj.get_upper() - adj.get_value())
        return ("top", adj.get_value())

    def _restore_scroll_anchor(self, anchor: tuple[str, float]) -> bool:
        try:
            adj = self._schedule_scroll.get_vadjustment()
            edge, offset = anchor
            value = adj.get_upper() - offset if edge == "end" else offset
            adj.set_value(max(0.0, value))
        except Exception:
            pass
        return False

    def _scroll_schedule_to_focus(self) -> bool:
        """Scroll the schedule so the target day (or the next day with
        events) sits at the top: the app opens on today, not on the first
        day of last month."""
        target = self._scroll_target
        if target is None:
            return False
        later = sorted(day for day in self._day_markers if day >= target)
        marker = self._day_markers.get(later[0]) if later else None
        if marker is None and self._day_markers:
            marker = self._day_markers[max(self._day_markers)]
        try:
            adj = self._schedule_scroll.get_vadjustment()
        except Exception:
            return False
        if marker is None:
            self._scroll_target = None
            adj.set_value(0.0)
            return False
        ok, point = marker.compute_point(
            self._schedule_rows, _graphene_point(0.0, 0.0))
        laid_out = (ok and self._schedule_rows.get_height() > 0
                    and adj.get_upper() >= self._schedule_rows.get_height()
                    and (point.y > 0.0 or marker is
                         self._schedule_rows.get_first_child()))
        self._scroll_tries = getattr(self, "_scroll_tries", 0) + 1
        if not laid_out and self._scroll_tries < 50:
            # Not laid out yet: try again on a later turn.
            GLib.timeout_add(40, self._scroll_schedule_to_focus)
            return False
        self._scroll_tries = 0
        self._scroll_target = None
        adj.set_value(min(point.y, max(0.0, adj.get_upper()
                                       - adj.get_page_size())))
        return False

    def _clear_rows(self, box) -> None:
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt

    def _event_secondary(self, row, names: dict) -> tuple[str, str]:
        cal = names.get(row.calendar_id, row.calendar_id)
        if row.all_day:
            secondary = f"ALL DAY · {cal}" if cal else "ALL DAY"
            meta = ""
        else:
            span = views.time_range(int(row.start_ms), int(row.end_ms),
                                    self._zone)
            secondary = f"{span} · {cal}" if cal else span
            meta = ""
        return (secondary, meta)

    def _event_row(self, row, names: dict, last: bool, dim_past: bool,
                   now_ms: int) -> Gtk.Widget:
        """Text-first event row: title over time and calendar."""
        secondary, meta = self._event_secondary(row, names)
        uid, rid = row.uid, int(row.recurrence_id)
        item = widgets.list_row(
            row.title or views.UNTITLED, secondary, meta=meta, last=last,
            on_activate=lambda u=uid, r=rid: self.open_detail(u, r))
        if dim_past:
            if row.end_ms > row.start_ms:
                past = row.end_ms < now_ms
            else:
                past = row.start_ms < now_ms
            if past:
                item.set_opacity(0.55)
        return item

    def _render_rows(self, box, days: list, dim_past: bool, now_ms: int,
                     headers: bool = True) -> dict:
        """Day separators and event rows; an empty list shows nothing.
        Returns each day's first widget, for scrolling to a day."""
        self._clear_rows(box)
        markers: dict = {}
        names = self._calendar_names() if days else {}
        for day in days:
            rows = list(day.all_day) + list(day.timed)
            for index, row in enumerate(rows):
                if index == 0 and headers:
                    separator = widgets.day_separator(
                        views.day_header(day.date))
                    box.append(separator)
                    markers[day.date] = separator
                item = self._event_row(row, names, index == len(rows) - 1,
                                       dim_past, now_ms)
                box.append(item)
                markers.setdefault(day.date, item)
        return markers

    def _render_month(self, instances: list[dict], now_ms: int,
                      dim_past: bool) -> None:
        year, month = self._month_year
        settings = self._settings
        first_weekday = _weekday_index(
            str(settings.get("start_day_of_week", "MONDAY")))
        weeks = views.build_weeks(year, month, first_weekday)
        start = weeks[0][0][0]
        last = weeks[-1][-1][0] + _datetime.timedelta(days=1)
        by_date = views.events_by_date(instances, self._zone, start, last)
        counts = {day: len(rows) for day, rows in by_date.items()}
        self._month_counts = counts
        week_numbers = bool(settings.get("week_numbers", False))
        self._month_grid.set_week_numbers(week_numbers)
        self._clear_rows(self._month_dow)
        if week_numbers:
            lead = Gtk.Box()
            lead.set_size_request(int(self._month_grid._lead()), -1)
            self._month_dow.append(lead)
        for label in views.weekday_labels(first_weekday):
            cell = widgets.caption(label)
            cell.set_hexpand(True)
            cell.set_xalign(0.5)
            self._month_dow.append(cell)
        self._month_grid.set_data(weeks, counts, _datetime.date.today(),
                                  self._month_selected)
        self._clear_rows(self._peek_label_slot)
        self._clear_rows(self._peek_rows)
        selected = self._month_selected
        if selected is None or not (start <= selected < last):
            return
        self._peek_label_slot.append(
            widgets.caption(views.day_header(selected)))
        rows = list(by_date.get(selected, []))
        names = self._calendar_names()
        for i, row in enumerate(rows):
            self._peek_rows.append(self._event_row(
                row, names, i == len(rows) - 1, dim_past, now_ms))

    def _render_calendars(self) -> None:
        self._clear_rows(self._cal_rows)
        try:
            calendars = self._store.list_calendars()
        except Exception:
            calendars = []
        for i, cal in enumerate(calendars):
            switch = widgets.make_switch(bool(cal.is_visible),
                                         lambda on, c=cal:
                                         self._toggle_calendar(c, on))
            row = widgets.list_row(
                cal.display_name or cal.id, cal.account_name or "",
                meta="READ-ONLY" if not cal.is_writable else "",
                trailing=switch, last=i == len(calendars) - 1)
            self._cal_rows.append(row)

    def _toggle_calendar(self, cal, visible: bool) -> None:
        try:
            from xx_calendar.store import CalendarRecord

            updated = CalendarRecord(
                id=cal.id, display_name=cal.display_name,
                account_name=cal.account_name, color=cal.color,
                is_visible=bool(visible), is_writable=cal.is_writable,
                sigil_tier=cal.sigil_tier, revision=cal.revision)
            self._store.update_calendar(updated)
        except Exception as exc:
            self._toasts.add_toast(Adw.Toast.new(f"Could not save: {exc}"))
            return
        self.reload()

    def _build_settings_rows(self) -> None:
        return None

    def _render_settings(self) -> None:
        self._clear_rows(self._settings_rows)
        get = self._settings.get
        rows = self._settings_rows
        rows.append(widgets.section_label("VIEW"))
        rows.append(_two_lines(widgets.setting_row(
            "Dim past events", "Fade ended events",
            widgets.make_switch(bool(get("dim_past", True)),
                                lambda on: self._save_setting("dim_past",
                                                             bool(on))))))
        rows.append(_two_lines(widgets.setting_row(
            "Week numbers", "In the month grid",
            widgets.make_switch(bool(get("week_numbers", False)),
                                lambda on: self._save_setting("week_numbers",
                                                             bool(on))))))
        sunday = str(get("start_day_of_week", "MONDAY")).upper() == "SUNDAY"
        rows.append(_two_lines(widgets.setting_row(
            "Week starts", "",
            widgets.segmented([
                ("Mon", not sunday,
                 lambda: self._set_week_start("MONDAY")),
                ("Sun", sunday,
                 lambda: self._set_week_start("SUNDAY")),
            ]), last=True)))
        rows.append(widgets.section_label("CALENDARS"))
        try:
            calendars = self._store.list_calendars()
        except Exception:
            calendars = []
        rows.append(_two_lines(widgets.list_row(
            "Calendars",
            ", ".join(c.display_name or c.id for c in calendars),
            last=True, on_activate=lambda: self.show_view("calendars"))))
        rows.append(widgets.section_label("DATA"))
        rows.append(_button_pair(
            widgets.outline_button("Import",
                                   callback=self._open_import_picker),
            widgets.outline_button("Export",
                                   callback=self.open_export_dialog)))

    def _set_week_start(self, value: str) -> None:
        if str(self._settings.get("start_day_of_week", "")).upper() == value:
            return
        self._save_setting("start_day_of_week", value)
        self._render_settings()

    def _save_setting(self, key: str, value) -> None:
        self._settings[key] = value
        try:
            save_settings(self._settings)
        except Exception as exc:
            self._toasts.add_toast(Adw.Toast.new(f"Could not save: {exc}"))
            return
        self.reload()

    # ------------------------------------------------------------ gestures

    def _on_schedule_edge(self, _scroller, pos) -> None:
        if pos == Gtk.PositionType.BOTTOM:
            self._schedule.extend_forward()
        elif pos == Gtk.PositionType.TOP:
            self._schedule.extend_backward()
            self._extended_back = True
        else:
            return
        self.reload()

    def _on_schedule_swipe(self, _gesture, velocity_x, velocity_y) -> None:
        direction = views.swipe_direction(velocity_x, velocity_y,
                                          SWIPE_VELOCITY_PX_S)
        if direction == 0:
            return
        self.shift_focus(direction)

    def _on_day_swipe(self, _gesture, velocity_x, velocity_y) -> None:
        direction = views.swipe_direction(velocity_x, velocity_y,
                                          SWIPE_VELOCITY_PX_S)
        if direction == 0:
            return
        self._day_date = self._day_date + _datetime.timedelta(days=direction)
        self._refresh_header()
        self.reload()

    def _ensure_focus_window(self) -> None:
        focus = self._schedule.focus_date
        if focus is None:
            return
        near_start = focus - self._schedule.start_date
        near_end = self._schedule.end_date - focus
        if near_start.days < RELOAD_MARGIN_DAYS:
            self._schedule.extend_backward()
        if near_end.days < RELOAD_MARGIN_DAYS:
            self._schedule.extend_forward()

    def shift_focus(self, days: int) -> None:
        anchor = self._schedule.focus_date or _datetime.date.today()
        self._schedule.focus_on(anchor + _datetime.timedelta(days=days))
        self._scroll_target = self._schedule.focus_date
        self._ensure_focus_window()
        self.reload()

    def _on_month_swipe(self, _gesture, velocity_x, velocity_y) -> None:
        direction = views.swipe_direction(velocity_x, velocity_y,
                                          SWIPE_VELOCITY_PX_S)
        if direction == 0:
            return
        year, month = self._month_year
        first = _datetime.date(year, month, 1)
        if direction > 0:
            nxt = first + _datetime.timedelta(days=32)
        else:
            nxt = first - _datetime.timedelta(days=1)
        self._month_year = (nxt.year, nxt.month)
        today = _datetime.date.today()
        self._month_selected = today if (today.year, today.month) == \
            self._month_year else None
        self._refresh_header()
        self.reload()

    # -------------------------------------------------------------- changed

    def _subscribe_changed(self) -> None:
        try:
            Gio.bus_get(Gio.BusType.SESSION, None, self._on_bus_ready)
        except Exception:
            self._bus_connection = None

    def _on_bus_ready(self, _source, result) -> None:
        try:
            connection = Gio.bus_get_finish(result)
        except Exception:
            return
        self._bus_connection = connection
        try:
            connection.signal_subscribe(
                BUS_NAME, INTERFACE, "Changed", OBJECT_PATH, None,
                Gio.DBusSignalFlags.NONE, self._on_changed_signal)
        except Exception:
            pass

    def _on_changed_signal(self, _conn, _sender, _path, _iface, _signal,
                           params) -> None:
        try:
            _revision, _uids = params.unpack()
        except Exception:
            _revision, _uids = (0, [])
        del _revision, _uids
        self._schedule.force_refresh()
        if not self._reload_pending:
            self._reload_pending = True
            GLib.timeout_add_seconds(CHANGED_DEBOUNCE_S,
                                     self._on_changed_debounced)

    def _on_changed_debounced(self) -> bool:
        self._reload_pending = False
        self.reload()
        return False

    def _on_minute_tick(self) -> bool:
        self.reload()
        return True

    # ---------------------------------------------------------- detail/editor

    def writable_calendars(self) -> list:
        try:
            return [c for c in self._store.list_calendars() if c.is_writable]
        except Exception:
            return []

    def open_detail(self, uid: str, recurrence_id: int = 0) -> None:
        try:
            from xx_calendar import ics as ics_module

            ics_text = self._service.get(uid)
            events = ics_module.parse_events(ics_text)
            event = events[0] if events else None
        except Exception as exc:
            self._toasts.add_toast(Adw.Toast.new(f"Could not load: {exc}"))
            return
        if event is None:
            self._toasts.add_toast(Adw.Toast.new("Could not load event"))
            return
        title = event.summary() or views.UNTITLED
        box = self._detail_rows
        self._clear_rows(box)
        headline = Gtk.Label(label=title)
        headline.add_css_class("xx-display-large")
        headline.set_xalign(0.0)
        headline.set_wrap(True)
        headline.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        headline.set_max_width_chars(32)
        box.append(headline)
        when = self._when_text(event, int(recurrence_id))
        if when:
            box.append(_wrapped_field("WHEN", when))
        try:
            names = self._calendar_names()
            record = self._store.get_event(uid)
            box.append(_wrapped_field(
                "CALENDAR", names.get(record.calendar_id,
                                      record.calendar_id)))
        except Exception:
            pass
        location = event.location() or ""
        if location:
            box.append(_wrapped_field("LOCATION", location))
        description = event.description() or ""
        if description:
            box.append(_wrapped_field("DESCRIPTION", description))
        rule = event.rrule() or ""
        if rule:
            box.append(widgets.field("REPEAT", views.repeat_label(rule)))
        reminders = event.reminder_minutes()
        if reminders:
            box.append(widgets.field(
                "REMINDER", views.reminder_label(reminders[0])))
        self._detail_uid = uid
        self._detail_rid = int(recurrence_id)
        box.append(_button_pair(
            widgets.outline_button(
                "Edit", callback=lambda: self.open_editor(uid=uid)),
            widgets.outline_button(
                "Copy",
                callback=lambda: self.open_editor(uid=uid, duplicate=True))))
        scope = {"value": "this" if rule else "all"}
        if rule:
            box.append(widgets.segmented([
                ("This", True, lambda: scope.update(value="this")),
                ("Following", False,
                 lambda: scope.update(value="this-and-following")),
                ("All", False, lambda: scope.update(value="all")),
            ]))
        box.append(widgets.danger_button(
            "Delete",
            callback=lambda: self._delete_event(
                uid, scope["value"], recurrence_id)))
        self._last_dialog = box
        self.show_view("detail")

    def _when_text(self, event, recurrence_id: int = 0) -> str:
        """The tapped occurrence's time in words, not the series start."""
        try:
            start = event.dtstart()
            if start is None:
                return ""
            end = event.dtend()
            start_ms = int(start[0])
            end_ms = int(end[0]) if end is not None else None
            if recurrence_id and recurrence_id != start_ms:
                if end_ms is not None:
                    end_ms = recurrence_id + (end_ms - start_ms)
                start_ms = recurrence_id
            zone = _datetime.timezone.utc if start[1] else self._zone
            moment = _datetime.datetime.fromtimestamp(
                start_ms / 1000, _datetime.timezone.utc).astimezone(zone)
            stop = None
            if end_ms is not None:
                stop = _datetime.datetime.fromtimestamp(
                    end_ms / 1000, _datetime.timezone.utc).astimezone(zone)
            return views.when_label(moment, stop, bool(start[1]))
        except Exception:
            return ""

    def _delete_event(self, uid: str, scope: str, recurrence_id: int) -> None:
        try:
            self._service.delete(uid, scope, int(recurrence_id))
        except Exception as exc:
            self._toasts.add_toast(Adw.Toast.new(f"Could not delete: {exc}"))
            return
        self._schedule.force_refresh()
        self._go_back()

    def open_create_sheet(self, day=None) -> None:
        self.open_editor(day=day)

    def open_editor(self, day=None, uid: str | None = None,
                    duplicate: bool = False) -> None:
        calendars = self.writable_calendars()
        if not calendars:
            self._toasts.add_toast(
                Adw.Toast.new("No writable calendar yet"))
            return
        from xx_calendar import ics as ics_module

        existing = None
        if uid is not None:
            try:
                text = self._service.get(uid)
                parsed = ics_module.parse_events(text)
                existing = parsed[0] if parsed else None
            except Exception:
                existing = None
        editing = existing is not None and not duplicate
        self._editor_title = "Edit event" if editing else "New event"
        self._editor_back = "detail" if self._view == "detail" else "list"
        content = self._editor_rows
        self._clear_rows(content)
        title_entry = widgets.text_entry("Title")
        self._defocusable(title_entry)
        if existing is not None and (existing.summary() or ""):
            title_entry.set_text(existing.summary() or "")
        content.append(title_entry)
        all_day_switch = widgets.make_switch(False)
        content.append(widgets.setting_row("All day", "", all_day_switch,
                                           last=True))
        if existing is not None and existing.dtstart() is not None:
            if existing.dtstart()[1]:
                all_day_switch.set_active(True)
        start_entry = widgets.text_entry("YYYY-MM-DD HH:MM")
        self._defocusable(start_entry)
        end_entry = widgets.text_entry("YYYY-MM-DD HH:MM")
        self._defocusable(end_entry)
        duration_min = self._settings.get("default_duration_min", 30)
        try:
            duration_min = int(duration_min)
        except (TypeError, ValueError):
            duration_min = 30
        anchor = day or _datetime.date.today()
        if existing is not None:
            try:
                prefill = self._prefill_range(existing)
            except Exception:
                prefill = ("", "")
        else:
            prefill = views.default_range(anchor, _datetime.datetime.now(),
                                          duration_min)
        if prefill[0]:
            start_entry.set_text(prefill[0])
        if prefill[1]:
            end_entry.set_text(prefill[1])
        content.append(_labelled("STARTS", start_entry))
        content.append(_labelled("ENDS", end_entry))
        selected_cal = 0
        if existing is not None:
            try:
                record = self._store.get_event(uid or "")
                for i, cal in enumerate(calendars):
                    if cal.id == record.calendar_id:
                        selected_cal = i
                        break
            except Exception:
                pass
        picker = ChoiceChips([c.display_name or c.id for c in calendars],
                             selected_cal)
        if len(calendars) > 1:
            content.append(_labelled("CALENDAR", picker.widget))
        raw_rule = existing.rrule() if existing is not None else ""
        repeat_choices = views.repeat_choices(raw_rule or "")
        repeat_index = views.repeat_index(raw_rule or "", repeat_choices)
        repeat_picker = ChoiceChips(
            [label for label, _value in repeat_choices],
            repeat_index if repeat_index is not None else 0)
        content.append(_labelled("REPEAT", repeat_picker.widget))
        default_rem = self._settings.get("default_notification_min")
        try:
            default_rem = int(default_rem)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            default_rem = 10
        sel_rem = 0
        wanted = default_rem
        if existing is not None:
            got = existing.reminder_minutes()
            wanted = got[0] if got else None
        for i, (_label, value) in enumerate(REMINDER_CHOICES):
            if value == wanted:
                sel_rem = i
                break
        reminder_picker = ChoiceChips(
            [label for label, _value in REMINDER_CHOICES], sel_rem)
        content.append(_labelled("REMINDER", reminder_picker.widget))
        location_entry = widgets.text_entry("Location")
        self._defocusable(location_entry)
        if existing is not None and existing.location():
            location_entry.set_text(existing.location() or "")
        content.append(location_entry)
        desc_entry = widgets.text_entry("Description")
        self._defocusable(desc_entry)
        if existing is not None and existing.description():
            desc_entry.set_text(existing.description() or "")
        content.append(desc_entry)
        scope_picker = None
        if editing and existing.rrule():
            scope_picker = ChoiceChips(["This", "Following", "All"], 0)
            content.append(_labelled("CHANGE", scope_picker.widget))
        error_label = widgets.label("", "xx-danger-text")
        error_label.set_wrap(True)
        content.append(error_label)
        save = widgets.primary_button(
            "Save",
            callback=lambda: self._save_editor(
                content, title_entry, all_day_switch, start_entry, end_entry,
                picker, calendars, repeat_picker, reminder_picker,
                location_entry, desc_entry, scope_picker, error_label,
                {"anchor": anchor, "duration_min": duration_min,
                 "uid": None if duplicate else uid,
                 "repeat_choices": repeat_choices}))
        content.append(_button_pair(
            widgets.text_button("Cancel", callback=self.go_back), save))
        self._last_dialog = content
        self.show_view("editor")

        def on_all_day(*_args) -> None:
            on = all_day_switch.get_active()
            for entry, hour in ((start_entry, 9), (end_entry, 10)):
                entry.set_placeholder_text(
                    "YYYY-MM-DD" if on else "YYYY-MM-DD HH:MM")
                entry.set_text(views.retime_text(entry.get_text(), on, hour))

        all_day_switch.connect("notify::active", on_all_day)
        for entry in (start_entry, end_entry):
            entry.set_placeholder_text(
                "YYYY-MM-DD" if all_day_switch.get_active()
                else "YYYY-MM-DD HH:MM")

    def _prefill_range(self, event) -> tuple[str, str]:
        start = event.dtstart()
        if start is None:
            return ("", "")
        moment = _datetime.datetime.fromtimestamp(
            start[0] / 1000, _datetime.timezone.utc).astimezone(self._zone)
        if start[1]:
            first = moment.date().isoformat()
            end = event.dtend()
            if end is not None:
                stop = _datetime.datetime.fromtimestamp(
                    end[0] / 1000, _datetime.timezone.utc).astimezone(
                        self._zone).date().isoformat()
            else:
                stop = first
            return (first, stop)
        first = moment.strftime("%Y-%m-%d %H:%M")
        end = event.dtend()
        if end is not None:
            stop = _datetime.datetime.fromtimestamp(
                end[0] / 1000, _datetime.timezone.utc).astimezone(
                    self._zone).strftime("%Y-%m-%d %H:%M")
        else:
            stop = ""
        return (first, stop)

    def _save_editor(self, host, title_entry, all_day_switch, start_entry,
                     end_entry, picker, calendars, repeat_picker,
                     reminder_picker, location_entry, desc_entry,
                     scope_picker, error_label, state) -> None:
        title = title_entry.get_text()
        if not title.strip():
            error_label.set_text("Give the event a title.")
            return
        all_day = all_day_switch.get_active()
        start_ms = parse_sheet_datetime(start_entry.get_text(), all_day,
                                        self._zone)
        if start_ms is None:
            error_label.set_text(
                "Start is not a date" + ("" if all_day else " and time")
                + ".")
            return
        raw_end = end_entry.get_text().strip()
        if raw_end:
            end_ms = parse_sheet_datetime(raw_end, all_day, self._zone)
            if end_ms is None:
                error_label.set_text("End is not a date"
                                     + ("" if all_day else " and time")
                                     + ".")
                return
        else:
            if all_day:
                end_ms = start_ms + 86400000
            else:
                end_ms = start_ms + max(1, state["duration_min"]) * 60000
        if end_ms <= start_ms:
            error_label.set_text("End must be after start.")
            return
        index = picker.get_selected()
        calendar_id = calendars[index].id if 0 <= index < len(calendars) \
            else calendars[0].id
        choices = state.get("repeat_choices") or RECURRENCE_CHOICES
        rrule = choices[repeat_picker.get_selected()][1] or None
        rem_value = REMINDER_CHOICES[reminder_picker.get_selected()][1]
        location = location_entry.get_text().strip() or None
        description = desc_entry.get_text().strip() or None
        ics_text = views.build_create_ics(
            title, all_day, start_ms, end_ms, reminder_minutes=rem_value,
            rrule=rrule, location=location, description=description)
        uid = state.get("uid")
        if uid is None:
            self._write_create(calendar_id, ics_text, host, error_label)
            return
        scope = "all"
        if scope_picker is not None:
            picked = scope_picker.get_selected()
            scope = ["this", "this-and-following", "all"][min(2, picked)]
            try:
                from xx_calendar import ics as ics_module

                old = ics_module.parse_events(self._service.get(uid))[0]
                if old.rrule() and scope == "all":
                    pass
            except Exception:
                pass
            if scope == "this":
                try:
                    from xx_calendar import ics as ics_module

                    target = None
                    for item in self._last_instances:
                        if item.get("uid") == uid:
                            target = int(item.get("start", 0))
                            break
                    _ = target
                except Exception:
                    pass
        try:
            if scope == "all":
                self._service.update(uid, ics_text, "all", 0)
            else:
                # The occurrence the user opened, not the first one loaded.
                rid = self._detail_rid if self._detail_uid == uid else 0
                for item in self._last_instances if not rid else []:
                    if item.get("uid") == uid:
                        rid = int(item.get("start", 0))
                        break
                self._service.update(uid, ics_text, scope, rid)
        except Exception as exc:
            error_label.set_text(f"Could not save: {exc}")
            return
        GLib.idle_add(self._on_create_done, host, True, "")

    def _write_create(self, calendar_id: str, ics_text: str, host,
                      error_label) -> None:
        if self._bus_connection is not None:
            try:
                self._bus_connection.call(
                    BUS_NAME, OBJECT_PATH, INTERFACE, "Create",
                    GLib.Variant("(ss)", (calendar_id, ics_text)),
                    GLib.VariantType.new("(s)"),
                    Gio.DBusCallFlags.NONE, -1, None,
                    self._on_create_replied, host)
                return
            except Exception:
                pass
        worker = threading.Thread(
            target=self._create_locally,
            args=(calendar_id, ics_text, host), daemon=True)
        worker.start()

    def _on_create_replied(self, connection, result, host) -> None:
        try:
            reply = connection.call_finish(result)
            _uid = reply.unpack()[0]
        except Exception as exc:
            self._show_sheet_error(host, f"Could not save: {exc}")
            return
        GLib.idle_add(self._on_create_done, host, True, "")

    def _create_locally(self, calendar_id: str, ics_text: str,
                        host) -> None:
        try:
            self._service.create(calendar_id, ics_text)
        except Exception as exc:
            GLib.idle_add(self._show_sheet_error, host,
                          f"Could not save: {exc}")
            return
        GLib.idle_add(self._on_create_done, host, True, "")

    def _show_sheet_error(self, host, message: str) -> bool:
        error = _find_error_label(host)
        if error is not None:
            try:
                error.set_text(message)
            except Exception:
                try:
                    error.set_label(message)
                except Exception:
                    pass
        else:
            self._toasts.add_toast(Adw.Toast.new(message))
        return False

    def _on_create_done(self, _host, _ok: bool, _message: str = "") -> bool:
        """A finished write returns to the list (or the edited event's
        page) quietly: the new or changed rows are the confirmation."""
        self._schedule.force_refresh()
        if self._view == "editor" and self._editor_back == "detail" \
                and self._detail_uid:
            self.open_detail(self._detail_uid, self._detail_rid)
            if self._view == "detail":
                return False
        self._go_back()
        return False

    # ------------------------------------------------------------ ics import

    def _open_import_picker(self) -> None:
        picker = Gtk.FileDialog()
        try:
            picker.set_title("Import .ics file")
        except Exception:
            pass
        try:
            picker.open(self, None, self._on_import_picked)
        except Exception as exc:
            self._toasts.add_toast(Adw.Toast.new(f"Could not open: {exc}"))

    def _on_import_picked(self, dialog, result) -> None:
        try:
            chosen = dialog.open_finish(result)
            uri = chosen.get_uri() if hasattr(chosen, "get_uri") else None
        except Exception as exc:
            self._toasts.add_toast(Adw.Toast.new(f"Could not open: {exc}"))
            return
        if uri:
            self.import_ics_uri(uri)

    def import_ics_uri(self, uri: str) -> None:
        path = _uri_to_path(uri)
        if path is None or not path.lower().endswith(".ics"):
            return
        calendars = self.writable_calendars()
        if not calendars:
            self._toasts.add_toast(
                Adw.Toast.new("No writable calendar yet"))
            return
        content = self._import_rows
        self._clear_rows(content)
        content.append(_wrapped_field("FILE", os.path.basename(path)))
        picker = ChoiceChips([c.display_name or c.id for c in calendars])
        content.append(_labelled("INTO", picker.widget))
        error_label = widgets.label("", "xx-danger-text")
        error_label.set_wrap(True)
        content.append(error_label)
        content.append(_button_pair(
            widgets.text_button("Cancel", callback=self.go_back),
            widgets.primary_button(
                "Import",
                callback=lambda: self._start_import(
                    picker.get_selected(), calendars, path, content,
                    error_label))))
        self._last_dialog = content
        self.show_view("import")

    def _start_import(self, index: int, calendars: list, path: str, host,
                      error_label) -> None:
        calendar_id = calendars[index].id \
            if 0 <= index < len(calendars) else calendars[0].id
        worker = threading.Thread(
            target=self._import_locally,
            args=(calendar_id, path, host), daemon=True)
        worker.start()

    def _import_locally(self, calendar_id: str, path: str,
                        host) -> None:
        from xx_calendar.service import read_bounded_text

        try:
            text = read_bounded_text(path)
            self._service.import_ics(calendar_id, text)
        except Exception as exc:
            GLib.idle_add(self._show_sheet_error, host,
                          f"Could not import: {exc}")
            return
        GLib.idle_add(self._on_create_done, host, True, "")

    def open_export_dialog(self) -> None:
        try:
            calendars = self._store.list_calendars()
        except Exception:
            calendars = []
        content = self._export_rows
        self._clear_rows(content)
        picker = ChoiceChips(["All"] + [c.display_name or c.id
                                        for c in calendars])
        content.append(_labelled("CALENDARS", picker.widget))
        error_label = widgets.label("", "xx-danger-text")
        error_label.set_wrap(True)
        content.append(error_label)
        content.append(_button_pair(
            widgets.text_button("Cancel", callback=self.go_back),
            widgets.primary_button(
                "Export",
                callback=lambda: self._start_export(
                    picker.get_selected(), calendars, content,
                    error_label))))
        self._last_dialog = content
        self.show_view("export")

    def _start_export(self, index: int, calendars: list, host,
                      error_label) -> None:
        if index <= 0:
            ids = None
        else:
            target = calendars[index - 1] if 0 <= index - 1 < len(calendars) \
                else None
            ids = [target.id] if target is not None else None
        try:
            text = self._service.export_ics(ids)
        except Exception as exc:
            error_label.set_text(f"Could not export: {exc}")
            return
        saver = Gtk.FileDialog()
        try:
            saver.set_initial_name("calendar.ics")
        except Exception:
            pass
        saver.save(self, None,
                   lambda d, r: self._on_export_picked(d, r, text, host,
                                                       error_label))

    def _on_export_picked(self, dialog, result, text: str, parent,
                          error_label) -> None:
        try:
            chosen = dialog.save_finish(result)  # type: ignore[arg-type]
            path = chosen.get_path() if hasattr(chosen, "get_path") else None
        except GLib.Error as exc:
            # Dismissing the save dialog is not an error to report.
            if exc.matches(Gtk.DialogError.quark(),
                           Gtk.DialogError.DISMISSED) or \
                    exc.matches(Gtk.DialogError.quark(),
                                Gtk.DialogError.CANCELLED):
                return
            error_label.set_text(f"Could not export: {exc.message}")
            return
        except Exception as exc:
            error_label.set_text(f"Could not export: {exc}")
            return
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        except Exception as exc:
            error_label.set_text(f"Could not export: {exc}")
            return
        GLib.idle_add(self._on_create_done, parent, True, "")


def _wrapped_field(caption_text: str, value: str) -> Gtk.Box:
    """``widgets.field`` whose value wraps (a long location, a file name)
    instead of running off a 320 px screen."""
    box = widgets.field(caption_text, value)
    shown = box.get_last_child()
    if isinstance(shown, Gtk.Label):
        shown.set_wrap(True)
        shown.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        shown.set_ellipsize(Pango.EllipsizeMode.NONE)
        shown.set_max_width_chars(-1)
        shown.set_width_chars(-1)
        shown.set_xalign(0.0)
    return box

def _graphene_point(x: float, y: float):
    point = Graphene.Point()
    point.init(x, y)
    return point


def _tier_index(tier) -> int:
    try:
        from xx_calendar.models import SigilTier

        return list(SigilTier).index(tier)
    except (ValueError, TypeError):
        return 0


def _weekday_index(name: str) -> int:
    order = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY",
             "SATURDAY", "SUNDAY"]
    try:
        return order.index(name.upper())
    except ValueError:
        return 0


def _variant_value(variant):
    text = variant.get_type_string()
    if text == "b":
        return variant.get_boolean()
    if text == "x":
        return variant.get_int64()
    if text == "i":
        return variant.get_int32()
    if text == "s":
        return variant.get_string()
    try:
        return variant.unpack()
    except Exception:
        return None


def _find_error_label(widget):
    if widget is None:
        return None
    if isinstance(widget, Gtk.Label) and (
            widget.has_css_class("xx-danger-text") or
            widget.has_css_class("error")):
        return widget
    child = widget.get_first_child() if hasattr(widget, "get_first_child") \
        else None
    while child is not None:
        found = _find_error_label(child)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def _uri_to_path(uri: str) -> str | None:
    try:
        info = Gio.File.new_for_uri(uri)
        path = info.get_path()
        if path:
            return path
    except Exception:
        pass
    if os.path.isfile(uri):
        return uri
    return None


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else list(__import__("sys").argv)
    program = args[0] if args else "xx-calendar"
    launch = parse_launch_args(args[1:])
    app = CalendarApp(launch=launch)
    return app.run([program] + launch["rest"])


if __name__ == "__main__":
    raise SystemExit(main())

