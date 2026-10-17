"""L2 contacts list UI: search, sections, jump strip, row menu.

Phone-first 360x720 over ``Adw.NavigationView`` with the shared
``xx_ui`` design system: a ``PageHeader`` (title, count caption, New
action, overflow menu), an All/Starred ``ChipBar``, a virtualized
``Gtk.ListView`` of avatar rows with hairlines, an A-Z jump strip, and
a flat bottom search entry. Filters, import/export, the Me card and the
other preferences live in the overflow menu and the settings page; the
list itself shows the list. All store reads/writes run in worker
threads with results posted by ``GLib.idle_add``; the visible list is a
``Gtk.ListView`` over a ``Gio.ListStore`` so thousands of rows scroll.
External changes arrive through ``ChangeWatcher`` (the service's
``Changed`` signal plus a monitor on the database directory).

Import-safe without a display: constructing widgets needs a display, but
importing this module must not, so pytest never instantiates it.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")

import os  # noqa: E402
import threading  # noqa: E402

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from xx_ui import widgets  # noqa: E402
import xx_phone  # noqa: E402
from xx_contacts import avatar as avatar_module  # noqa: E402
from xx_contacts import privacy  # noqa: E402
from xx_contacts import vcard as vcard_module  # noqa: E402
from xx_contacts.list_model import (  # noqa: E402
    JUMP_MIN_TOUCH_PX,
    SEARCH_DEBOUNCE_MS,
    SEARCH_RESULT_LIMIT,
    ListFilter,
    ListRow,
    SearchDebouncer,
    SectionHeader,
    action_uri,
    build_sections,
    flatten_sections,
    jump_labels,
    plan_refresh,
    row_menu,
)
from xx_contacts.list_model import fetch_rows as fetch_list_rows  # noqa: E402
from xx_contacts.service import (  # noqa: E402
    BUS_NAME,
    INTERFACE,
    OBJECT_PATH,
)
from xx_contacts.store import (  # noqa: E402
    NotFoundError,
    default_exports_dir,
)

ACTION_LABELS = {
    "call": "Call",
    "message": "Message",
    "email": "Email",
    "star": "Add star",
    "unstar": "Remove star",
    "block": "Block",
    "unblock": "Unblock",
    "share": "Share vCard",
    "delete": "Delete",
}

REFRESH_DELAY_MS = 200


class RowItem(GObject.Object):
    """One ``Gio.ListStore`` entry: a section header or a contact row."""

    __gtype_name__ = "XXContactsRowItem"
    uid = GObject.Property(type=str, default="")
    title = GObject.Property(type=str, default="")
    detail = GObject.Property(type=str, default="")
    photo = GObject.Property(type=str, default="")
    starred = GObject.Property(type=bool, default=False)
    is_header = GObject.Property(type=bool, default=False)


LIST_PHOTO_PX = 32

#: Row text caps: 20/24 chars of the theme font fit the 320 px content
#: column next to a 40 px avatar without widening the 360 px canvas.
_NAME_CHARS = 20
_DETAIL_CHARS = 24


def resolve_row_uid(picked) -> str | None:
    """Return the contact uid for the widget under a pointer gesture.

    Selection, focus and pointer position are independent in GTK4, so a
    right-click or long-press must resolve its own target: walk up
    ``get_parent()`` from the picked widget to the row box, which carries
    the uid set at bind time. Empty or missing uids keep walking.
    """
    widget = picked
    while widget is not None:
        for attr in ("_xx_uid", "contact_uid", "xx_uid", "uid"):
            try:
                value = getattr(widget, attr, None)
            except Exception:
                value = None
            if isinstance(value, str) and value:
                return value
        try:
            data = widget.get_data("xx-uid")  # type: ignore[attr-defined]
        except Exception:
            data = None
        if isinstance(data, str) and data:
            return data
        get_parent = getattr(widget, "get_parent", None)
        if not callable(get_parent):
            return None
        try:
            widget = get_parent()
        except Exception:
            return None
    return None


def install_tap_to_focus(entry: Gtk.Widget) -> None:
    """Only let a text entry take focus from an explicit tap.

    The on-screen keyboard appears whenever a text widget gains focus,
    and GtkWindow picks an initial focus widget when it becomes active
    (after ``map``) while Adw.NavigationView restores focus on pop, so a
    ``map``-time clear-focus does not hold. ``set_focusable(False)``
    alone only stops the entry itself: a ``Gtk.SearchEntry`` holds an
    inner ``GtkText`` that still takes initial focus, so the entry
    starts with ``set_can_focus(False)`` (entry and descendants
    unfocusable); a CAPTURE-phase click re-enables the subtree and
    grabs focus, and an ``EventControllerFocus`` "leave" (which fires
    when focus leaves the entry or any child; ``notify::has-focus`` on
    the entry never fires because the inner ``GtkText`` holds focus)
    locks it again so neither launch nor page-pop can focus it.
    """
    entry.set_focusable(False)
    entry.set_can_focus(False)
    tap = Gtk.GestureClick.new()
    tap.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    tap.connect("pressed", _on_tap_to_focus_pressed, entry)
    entry.add_controller(tap)
    focus = Gtk.EventControllerFocus.new()
    focus.connect("leave", _on_tap_to_focus_left, entry)
    entry.add_controller(focus)


def _on_tap_to_focus_pressed(_gesture: Gtk.GestureClick, _n_press: int,
                             _x: float, _y: float,
                             entry: Gtk.Widget) -> None:
    entry.set_focusable(True)
    entry.set_can_focus(True)
    entry.grab_focus()


def _on_tap_to_focus_left(_controller: Gtk.EventControllerFocus,
                          entry: Gtk.Widget) -> None:
    try:
        entry.set_focusable(False)
    except Exception:
        pass
    try:
        entry.set_can_focus(False)
    except Exception:
        pass


def launch_uri(parent: Gtk.Window | None, uri: str) -> None:
    """Open a tel:/sms:/mailto:/file: URI in its default handler."""
    launcher = Gtk.UriLauncher.new(uri)
    launcher.launch(parent, None, _on_uri_launched)


def _on_uri_launched(launcher: Gtk.UriLauncher, result: Gio.AsyncResult
                     ) -> None:
    try:
        launcher.launch_finish(result)
    except GLib.Error:
        pass


def _monotonic_ms() -> int:
    return int(GLib.get_monotonic_time() // 1000)


class ChangeWatcher:
    """Nudge the list when another process writes the shared database.

    The ``--service`` process owns ``io.piercingxx.Contacts`` and emits
    ``Changed`` on writes; a directory monitor on the database covers the
    same writes even when nobody owns the name. Both paths funnel into
    one debounced callback.
    """

    def __init__(self, db_path: str, on_change) -> None:
        self._db_path = db_path
        self._on_change = on_change
        self._monitor: Gio.FileMonitor | None = None
        self._changed_id = 0
        self._connection: Gio.DBusConnection | None = None
        self._sub_id = 0

    def start(self) -> None:
        parent = Gio.File.new_for_path(
            os.path.dirname(os.path.abspath(self._db_path)))
        self._monitor = parent.monitor_directory(
            Gio.FileMonitorFlags.NONE, None)
        self._changed_id = self._monitor.connect("changed",
                                                 self._on_dir_changed)
        Gio.bus_get(Gio.BusType.SESSION, None, self._on_bus_ready)

    def stop(self) -> None:
        """Detach the directory monitor and the Changed subscription."""
        monitor, self._monitor = self._monitor, None
        changed_id, self._changed_id = self._changed_id, 0
        if monitor is not None and changed_id:
            try:
                monitor.disconnect(changed_id)
            except GLib.Error:
                pass
        connection, self._connection = self._connection, None
        sub_id, self._sub_id = self._sub_id, 0
        if connection is not None and sub_id:
            try:
                connection.signal_unsubscribe(sub_id)
            except GLib.Error:
                pass

    def _wanted(self, changed: Gio.File | None) -> bool:
        if changed is None:
            return False
        try:
            name = changed.get_basename()
        except GLib.Error:
            return False
        return name.startswith(os.path.basename(self._db_path))

    def _on_dir_changed(self, _monitor: Gio.FileMonitor,
                        changed: Gio.File, _other: Gio.File | None,
                        _event: Gio.FileMonitorEvent) -> None:
        if self._wanted(changed):
            self._on_change()

    def _on_bus_ready(self, _source: GObject.Object,
                      result: Gio.AsyncResult) -> None:
        try:
            connection = Gio.bus_get_finish(result)
        except GLib.Error:
            return
        self._connection = connection
        self._sub_id = connection.signal_subscribe(
            BUS_NAME, INTERFACE, "Changed", OBJECT_PATH, None,
            Gio.DBusSignalFlags.NONE, self._on_changed_signal)

    def _on_changed_signal(self, _connection: Gio.DBusConnection,
                           _sender: str | None, _path: str, _iface: str,
                           _signal: str, params: GLib.Variant) -> None:
        try:
            revision, uids = params.unpack()
        except Exception:
            return
        self._on_change(revision=int(revision), uids=list(uids))


class ContactsWindow(Adw.ApplicationWindow):
    """List/search window over the shared ``ContactStore``."""

    def __init__(self, app: Adw.Application, store,
                 config, config_path: str, db_path: str) -> None:
        super().__init__(application=app)
        self._store = store
        self._config = config
        self._config_path = config_path
        self._db_path = db_path
        self._model = Gio.ListStore.new(RowItem)
        self._rows: dict[str, ListRow] = {}
        self._known_revision = store.get_revision()
        self._query_seq = 0
        self._debouncer = SearchDebouncer(SEARCH_DEBOUNCE_MS)
        self._debounce_id = 0
        self._refresh_id = 0
        self._starred_only = False
        self._overflow: Gtk.Popover | None = None
        self._watcher = ChangeWatcher(db_path, self._on_external_changed)
        self._photos_dir = os.path.join(
            os.path.dirname(os.path.abspath(db_path)), "photos")
        self.set_title("xx-contacts")
        self.set_decorated(False)
        self.set_default_size(360, 720)

        navigation = Adw.NavigationView()
        self._navigation = navigation

        self._header = widgets.page_header(
            "Contacts", "0 contacts",
            actions=[("New", self._on_new_clicked)],
            overflow_cb=self._show_overflow)
        self._count_label = self._header.get_last_child()

        self._chips_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                   spacing=0)
        self._chips_slot.add_css_class("xx-page-body")
        self._rebuild_chips()

        middle = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        middle.set_hexpand(True)
        middle.set_vexpand(True)
        # NoSelection: activation (not selection) drives navigation, and
        # SingleSelection autoselects item 0 (a header) at launch. The
        # row menu resolves the row under the pointer, never the
        # selection, so it keeps working. Gtk.ListView has no
        # hover-select, so single-click-activate only turns a tap into an
        # activation instead of a selection; the pointer case stays sane.
        self._selection = Gtk.NoSelection.new(self._model)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_item_setup)
        factory.connect("bind", self._on_item_bind)
        factory.connect("unbind", self._on_item_unbind)
        self._list = Gtk.ListView.new(self._selection, factory)
        self._list.set_single_click_activate(True)
        self._list.set_hexpand(True)
        self._list.set_vexpand(True)
        self._list.set_margin_start(20)
        self._list.set_margin_end(20)
        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_policy(Gtk.PolicyType.NEVER,
                                  Gtk.PolicyType.AUTOMATIC)
        self._scrolled.set_hexpand(True)
        self._scrolled.set_vexpand(True)
        self._scrolled.set_child(self._list)
        middle.append(self._scrolled)

        self._empty_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                   spacing=0)
        self._empty_slot.add_css_class("xx-page-body")
        self._empty_slot.set_hexpand(True)
        self._empty_slot.set_vexpand(True)
        self._empty_slot.set_valign(Gtk.Align.CENTER)
        self._empty_slot.set_visible(False)
        middle.append(self._empty_slot)

        self._jump = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                             spacing=2)
        self._jump.set_valign(Gtk.Align.CENTER)
        middle.append(self._jump)

        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text("Search")
        self._search.set_hexpand(True)
        install_tap_to_focus(self._search)
        search_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                             spacing=8)
        search_bar.add_css_class("xx-searchbar")
        search_bar.add_css_class("xx-contact-search")
        search_bar.append(self._search)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.add_css_class("xx-page")
        root.append(self._header)
        root.append(self._chips_slot)
        root.append(middle)
        root.append(search_bar)
        list_page = Adw.NavigationPage(child=root, title="xx-contacts")
        navigation.push(list_page)
        self.set_content(navigation)
        self.set_size_request(320, 640)

        self._search.connect("search-changed", self._on_search_changed)
        self._list.connect("activate", self._on_row_activated)
        right_click = Gtk.GestureClick.new()
        right_click.set_button(3)
        right_click.connect("pressed", self._on_row_right_clicked)
        self._list.add_controller(right_click)
        long_press = Gtk.GestureLongPress.new()
        long_press.connect("pressed", self._on_row_long_pressed)
        self._list.add_controller(long_press)
        self.connect("map", self._on_map_clear_focus)
        self.connect("close-request", self._on_close_request)

        self._watcher.start()
        self._schedule_reload()

    # ------------------------------------------------------------- loading

    def _current_filter(self) -> ListFilter:
        return ListFilter(
            has_phone_only=bool(self._config.has_phone_only),
            starred_only=self._starred_only)

    def _set_starred_only(self, active: bool) -> None:
        self._starred_only = bool(active)
        self._rebuild_chips()
        self._schedule_reload()

    def _rebuild_chips(self) -> None:
        child = self._chips_slot.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._chips_slot.remove(child)
            child = following
        starred = self._starred_only
        self._chips_slot.append(widgets.chip_bar([
            ("All", not starred, lambda: self._set_starred_only(False)),
            ("Starred", starred, lambda: self._set_starred_only(True)),
        ]))

    def _schedule_reload(self) -> None:
        self._query_seq += 1
        seq = self._query_seq
        query = self._search.get_text()
        wanted = self._current_filter()
        worker = threading.Thread(target=self._load_in_worker,
                                  args=(seq, query, wanted),
                                  daemon=True)
        worker.start()

    def _load_in_worker(self, seq: int, query: str,
                        wanted: ListFilter) -> None:
        try:
            rows = fetch_list_rows(self._store, query, SEARCH_RESULT_LIMIT,
                                   wanted)
            revision = self._store.get_revision()
        except Exception:
            rows, revision = None, None
        GLib.idle_add(self._apply_rows, seq, rows, revision)

    def _fits_single(self, text_label: Gtk.Label, chars: int) -> None:
        text_label.set_ellipsize(Pango.EllipsizeMode.END)
        text_label.set_max_width_chars(chars)

    def _widen_action(self, empty: Gtk.Widget) -> None:
        box_child = empty.get_first_child()
        while box_child is not None:
            if isinstance(box_child, Gtk.Button):
                text_child = box_child.get_child()
                if isinstance(text_child, Gtk.Label):
                    text_child.set_max_width_chars(16)
            box_child = box_child.get_next_sibling()

    def _apply_rows(self, seq: int, rows: list[ListRow] | None,
                    revision: int | None) -> bool:
        if seq != self._query_seq or rows is None:
            return False
        self._known_revision = revision
        self._rows = {row.uid: row for row in rows}
        sections = build_sections(rows)
        flat = flatten_sections(sections)
        self._model.remove_all()
        for entry in flat:
            if isinstance(entry, SectionHeader):
                self._model.append(RowItem(title=entry.title,
                                           is_header=True))
            else:
                country = xx_phone.display_country(
                    self._config.default_region)
                shown = xx_phone.display(
                    entry.primary_number, country
                ) if entry.primary_number else ""
                detail = shown or entry.primary_email
                if entry.blocked and detail:
                    detail = f"{detail} · Blocked"
                elif entry.blocked:
                    detail = "Blocked"
                self._model.append(RowItem(uid=entry.uid,
                                           title=xx_phone.display(
                                               entry.display_name,
                                               country),
                                           detail=detail,
                                           photo=entry.photo_path,
                                           starred=entry.starred))
        self._rebuild_jump_strip(sections, flat)
        searching = bool(self._search.get_text().strip())
        if searching:
            empty = widgets.empty_state(
                "No matches. Try a different search.")
        else:
            empty = widgets.empty_state(
                "Your address book is empty.", "Import contacts",
                self._on_import_clicked)
        child = self._empty_slot.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._empty_slot.remove(child)
            child = following
        self._widen_action(empty)
        self._empty_slot.append(empty)
        has_rows = len(rows) > 0
        self._scrolled.set_visible(has_rows)
        self._empty_slot.set_visible(not has_rows)
        self._jump.set_visible(has_rows and len(sections) > 1)
        count = f"{len(rows)} CONTACTS" if len(rows) != 1 else "1 CONTACT"
        try:
            self._count_label.set_text(count)
        except Exception:
            pass
        return False

    def _rebuild_jump_strip(self, sections, flat) -> None:
        child = self._jump.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._jump.remove(child)
            child = following
        positions: dict[str, int] = {}
        for index, entry in enumerate(flat):
            if isinstance(entry, SectionHeader):
                positions.setdefault(entry.title, index)
        for label in jump_labels(sections):
            button = Gtk.Button(label=label)
            button.set_size_request(JUMP_MIN_TOUCH_PX, JUMP_MIN_TOUCH_PX)
            button.connect("clicked", self._on_jump_clicked,
                           positions.get(label, 0))
            self._jump.append(button)
        self._jump.set_visible(len(sections) > 1)

    # ------------------------------------------------------------ overflow

    def _show_overflow(self) -> None:
        popover = Gtk.Popover()
        self._overflow = popover
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        for title, target in (("Settings", "settings"),
                              ("Import/export", "import-export"),
                              ("My card", "me")):
            row = widgets.menu_row(
                title, lambda _t=target: self._open_overflow_target(
                    _t, popover))
            box.append(row)
        popover.set_child(box)
        popover.set_parent(self._header)
        popover.popup()

    def _open_overflow_target(self, target: str,
                              popover: Gtk.Popover) -> None:
        try:
            popover.popdown()
        except Exception:
            pass
        if target == "settings":
            self.open_settings()
        elif target == "import-export":
            self.open_import_export()
        elif target == "me":
            self.open_me_card()

    # ---------------------------------------------------------------- input

    def _on_search_changed(self, _entry: Gtk.SearchEntry) -> None:
        self._debouncer.edit(self._search.get_text(), _monotonic_ms())
        if self._debounce_id:
            GLib.source_remove(self._debounce_id)
        self._debounce_id = GLib.timeout_add(SEARCH_DEBOUNCE_MS,
                                             self._on_debounce)

    def _on_debounce(self) -> bool:
        self._debounce_id = 0
        if self._debouncer.due(_monotonic_ms()) is not None:
            self._schedule_reload()
        return False

    def _on_new_clicked(self) -> None:
        self.open_new_editor()

    def open_new_editor(self, prefill_phone: str = "") -> None:
        """Push a blank editor, optionally prefilling its first number.

        ``xx-contacts --new --tel NUMBER`` lands here (also on a second
        launch, forwarded to the running instance), so other apps get a
        review-before-save "Add to contacts" instead of a silent write.
        """
        from xx_contacts.editor_ui import ContactEditorPage

        page = ContactEditorPage(
            self._navigation, self, self._store, None, self._db_path,
            self._photos_dir, config=self._config,
            config_path=self._config_path,
            default_region=self._config.default_region,
            prefill_phone=prefill_phone,
            on_saved=lambda _uid: self._schedule_reload())
        self._navigation.push(page)

    def open_me_card(self) -> None:
        from xx_contacts.mecard_ui import MeCardPage

        page = MeCardPage(
            self._navigation, self, self._store, self._config,
            self._config_path, self._db_path, self._photos_dir,
            default_region=self._config.default_region)
        self._navigation.push(page)

    def _on_me_clicked(self, _button: Gtk.Button) -> None:
        self.open_me_card()

    def open_settings(self) -> None:
        from xx_contacts.settings_ui import SettingsPage

        page = SettingsPage(
            self._navigation, self, self._config, self._config_path,
            on_changed=self._schedule_reload)
        self._navigation.push(page)

    def _on_map_clear_focus(self, _window: Adw.ApplicationWindow) -> None:
        # The search entry must not take focus at launch: autofocus would
        # pop the on-screen keyboard on the phone. Tapping it later still
        # focuses it, since focus-on-click stays enabled.
        self.set_focus(None)

    def _on_jump_clicked(self, _button: Gtk.Button, position: int) -> None:
        self._list.scroll_to(position, Gtk.ListScrollFlags.NONE, None)

    def _on_row_activated(self, _view: Gtk.ListView,
                          position: int) -> None:
        item = self._model.get_item(position)
        if item is None or item.is_header:
            return
        if item.uid:
            self._open_detail(item.uid)

    def _uid_at_position(self, x: float, y: float) -> str | None:
        try:
            picked = self._list.pick(x, y, Gtk.PickFlags.DEFAULT)
        except Exception:
            return None
        return resolve_row_uid(picked)

    def _open_menu_at_position(self, x: float, y: float) -> None:
        uid = self._uid_at_position(x, y)
        if not uid:
            return
        row = self._rows.get(uid)
        if row is not None:
            self._show_row_menu(row)

    def _on_row_right_clicked(self, _gesture: Gtk.GestureClick,
                              _n_press: int, x: float, y: float) -> None:
        self._open_menu_at_position(x, y)

    def _on_row_long_pressed(self, _gesture: Gtk.GestureLongPress,
                             x: float, y: float) -> None:
        self._open_menu_at_position(x, y)

    def _open_detail(self, uid: str) -> None:
        from xx_contacts.detail_ui import ContactDetailPage

        page = ContactDetailPage(
            self._navigation, self, self._store, uid, self._db_path,
            config=self._config, config_path=self._config_path,
            photos_dir=self._photos_dir,
            default_region=self._config.default_region)
        self._navigation.push(page)

    def _on_close_request(self, _window: Adw.ApplicationWindow) -> bool:
        try:
            self._store.close()
        except Exception:
            pass
        return False

    def _on_external_changed(self, revision: int | None = None,
                             uids: list[str] | None = None) -> None:
        notice = (self._known_revision + 1) if revision is None else revision
        plan = plan_refresh(self._known_revision, notice, tuple(uids or ()),
                            tuple(self._rows))
        if not plan.refresh or self._refresh_id:
            return
        self._refresh_id = GLib.timeout_add(REFRESH_DELAY_MS,
                                            self._on_refresh_timeout)

    def _on_refresh_timeout(self) -> bool:
        self._refresh_id = 0
        self._schedule_reload()
        return False

    # -------------------------------------------------------------- factory

    def _on_item_setup(self, _factory: Gtk.SignalListItemFactory,
                       list_item: Gtk.ListItem) -> None:
        header = widgets.caption("")
        avatar_box, avatar_image, avatar_fallback = (
            avatar_module.build_avatar(LIST_PHOTO_PX))
        avatar_box.set_halign(Gtk.Align.START)
        avatar_box.set_hexpand(False)
        avatar_fallback.set_hexpand(False)
        avatar_image.set_hexpand(False)
        name = Gtk.Label()
        name.set_xalign(0.0)
        name.set_hexpand(True)
        name.add_css_class("xx-title")
        self._fits_single(name, _NAME_CHARS)
        detail = Gtk.Label()
        detail.set_xalign(0.0)
        detail.set_hexpand(True)
        detail.add_css_class("xx-secondary")
        self._fits_single(detail, _DETAIL_CHARS)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_hexpand(True)
        text.set_halign(Gtk.Align.FILL)
        text.append(name)
        text.append(detail)
        star = widgets.icon_image("starred-symbolic", 20)
        star.set_valign(Gtk.Align.CENTER)
        star.set_halign(Gtk.Align.END)
        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row_box.add_css_class("xx-row")
        row_box.set_valign(Gtk.Align.CENTER)
        row_box.set_halign(Gtk.Align.FILL)
        row_box.set_hexpand(True)
        row_box.append(avatar_box)
        row_box.append(text)
        row_box.append(star)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.set_hexpand(True)
        page.set_halign(Gtk.Align.FILL)
        page.append(header)
        page.append(row_box)
        list_item.header = header
        list_item.name = name
        list_item.detail = detail
        list_item.star = star
        list_item.avatar_image = avatar_image
        list_item.avatar_fallback = avatar_fallback
        list_item.row_box = row_box
        list_item.set_child(page)

    def _on_item_bind(self, _factory: Gtk.SignalListItemFactory,
                      list_item: Gtk.ListItem) -> None:
        item = list_item.get_item()
        is_header = bool(item.is_header)
        list_item.set_selectable(not is_header)
        list_item.set_focusable(not is_header)
        list_item.set_activatable(not is_header)
        list_item.header.set_visible(is_header)
        list_item.row_box.set_visible(not is_header)
        if is_header:
            list_item.header.set_text(item.title.upper())
            list_item.row_box.uid = ""
            list_item.row_box._xx_uid = ""
        else:
            list_item.name.set_text(item.title)
            list_item.detail.set_text(item.detail)
            list_item.detail.set_visible(bool(item.detail))
            list_item.star.set_visible(bool(item.starred))
            list_item.row_box.uid = item.uid
            list_item.row_box._xx_uid = item.uid
            found, position = self._model.find(item)
            n_items = self._model.get_n_items()
            if found and position == n_items - 1:
                list_item.row_box.add_css_class("last")
            else:
                list_item.row_box.remove_css_class("last")
            avatar_module.set_avatar(
                list_item.avatar_image, list_item.avatar_fallback,
                item.title, item.photo or None,
                bool(self._config.show_photos), LIST_PHOTO_PX)

    def _on_item_unbind(self, _factory: Gtk.SignalListItemFactory,
                        list_item: Gtk.ListItem) -> None:
        box = getattr(list_item, "row_box", None)
        if box is not None:
            box.uid = ""
            box._xx_uid = ""

    # ------------------------------------------------------------ row menu

    def _show_row_menu(self, row: ListRow) -> None:
        dialog = Gtk.Window(transient_for=self, modal=True,
                            title=row.display_name)
        dialog.set_decorated(False)
        dialog.set_default_size(300, -1)
        box = widgets.dialog_box(row.display_name)
        for action in row_menu(row):
            if action == "delete":
                button = widgets.menu_row(ACTION_LABELS[action])
                button.add_css_class("xx-danger-text")
            else:
                button = widgets.menu_row(ACTION_LABELS[action])
            button.connect("clicked", self._on_menu_action, action,
                           row.uid, dialog)
            box.append(button)
        cancel = widgets.menu_row("Cancel")
        cancel.connect("clicked", lambda _b: dialog.destroy())
        box.append(cancel)
        # The snap harness captures dialogs at phone size; pin the rows
        # to the top so they never spread across the extra height.
        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        box.append(spacer)
        dialog.set_child(box)
        dialog.present()

    def _on_menu_action(self, _button: Gtk.Button, action: str, uid: str,
                        dialog: Gtk.Window) -> None:
        dialog.destroy()
        row = self._rows.get(uid)
        if row is None:
            return
        uri = action_uri(action, row)
        if uri:
            launch_uri(self, uri)
        elif action in ("star", "unstar"):
            self._toggle_tag(uid, "starred", action == "star")
        elif action in ("block", "unblock"):
            self._toggle_tag(uid, "blocked", action == "block")
        elif action == "share":
            self._share_contact(uid)
        elif action == "delete":
            self._confirm_delete(row)

    def _toggle_tag(self, uid: str, tag: str, active: bool) -> None:
        worker = threading.Thread(target=self._toggle_tag_in_worker,
                                  args=(uid, tag, active), daemon=True)
        worker.start()

    def _toggle_tag_in_worker(self, uid: str, tag: str,
                              active: bool) -> None:
        try:
            from xx_contacts import service_client as service_client_module

            connection = service_client_module.gui_connection()
            if active:
                service_client_module.update_tags_sync(
                    connection, uid, add=(tag,))
            else:
                service_client_module.update_tags_sync(
                    connection, uid, remove=(tag,))
        except (NotFoundError, RuntimeError, GLib.Error):
            pass
        GLib.idle_add(self._reload_from_idle)

    def _reload_from_idle(self) -> bool:
        self._schedule_reload()
        return False

    def _share_contact(self, uid: str) -> None:
        worker = threading.Thread(target=self._share_in_worker, args=(uid,),
                                  daemon=True)
        worker.start()

    def _share_in_worker(self, uid: str) -> None:
        try:
            from xx_contacts import photos as photos_module

            record = self._store.get(uid)
            photo = photos_module.read_photo_bytes(record.photo_path or "")
            card = vcard_module.contact_to_vcard(record, photo_bytes=photo)
            exports = privacy.ensure_private_dir(
                default_exports_dir(os.path.expanduser("~")))
            path = os.path.join(exports, f"{uid}.vcf")
            privacy.secure_write_text(path, card)
        except (NotFoundError, OSError):
            path = ""
        GLib.idle_add(self._show_share_saved, path, uid)

    def _show_share_saved(self, path: str, uid: str) -> bool:
        row = self._rows.get(uid)
        name = row.display_name if row is not None else uid
        if not path:
            dialog = Adw.AlertDialog.new(
                "Share failed",
                f"Could not write a vCard for {name}.")
            dialog.add_response("ok", "OK")
            dialog.choose(self, None, self._on_dialog_dismissed)
            return False
        dialog = Adw.AlertDialog.new("vCard saved", path)
        dialog.add_response("close", "Close")
        dialog.add_response("open", "Open")
        dialog.set_default_response("close")
        dialog.set_close_response("close")
        dialog.choose(self, None,
                      lambda dlg, res: self._on_share_opened(dlg, res, path))
        return False

    def _on_share_opened(self, dialog: Adw.AlertDialog,
                         result: Gio.AsyncResult, path: str) -> None:
        try:
            response = dialog.choose_finish(result)
        except GLib.Error:
            return
        if response == "open":
            launch_uri(self, Gio.File.new_for_path(path).get_uri())

    def _confirm_delete(self, row: ListRow) -> None:
        dialog = Adw.AlertDialog.new(
            "Delete contact?",
            f"Delete {row.display_name}? This cannot be undone.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.choose(self, None,
                      lambda dlg, res: self._on_delete_chosen(dlg, res,
                                                             row.uid))

    def _on_delete_chosen(self, dialog: Adw.AlertDialog,
                          result: Gio.AsyncResult, uid: str) -> None:
        try:
            response = dialog.choose_finish(result)
        except GLib.Error:
            return
        if response != "delete":
            return
        worker = threading.Thread(target=self._delete_in_worker, args=(uid,),
                                  daemon=True)
        worker.start()

    def _delete_in_worker(self, uid: str) -> None:
        try:
            from xx_contacts import service_client as service_client_module

            connection = service_client_module.gui_connection()
            service_client_module.delete_sync(connection, uid)
        except (NotFoundError, RuntimeError, GLib.Error):
            pass
        GLib.idle_add(self._reload_from_idle)

    def _on_import_clicked(self, _button: Gtk.Button) -> None:
        self.open_import_export()

    def _on_import_export_clicked(self, _button: Gtk.Button) -> None:
        self.open_import_export()

    def open_import_export(self) -> None:
        from xx_contacts.import_export_ui import ImportExportPage

        page = ImportExportPage(
            self._navigation, self, self._store, self._config,
            self._config_path,
            default_region=self._config.default_region)
        self._navigation.push(page)

    def _open_import_export(self) -> None:
        self.open_import_export()

    def open_vcf_file(self, path: str) -> None:
        """Push the import page already reading a ``.vcf`` from Files.

        The page dry-runs its merge plan and asks to confirm, exactly as
        if the file had been picked inside the app: opening a vCard never
        writes before the user confirms.
        """
        from xx_contacts.import_export_ui import ImportExportPage

        page = ImportExportPage(
            self._navigation, self, self._store, self._config,
            self._config_path,
            default_region=self._config.default_region)
        self._navigation.push(page)
        page.import_file(path)

    def _on_dialog_dismissed(self, dialog: Adw.AlertDialog,
                             result: Gio.AsyncResult) -> None:
        try:
            dialog.choose_finish(result)
        except GLib.Error:
            pass
