"""L6 contact picker page: pick-mode list the D-Bus ``Pick`` serves.

Phone-first 360x720 (min 320x640): a title per kind, a tap-to-focus
search filter, a single-click ``Gtk.ListView`` over the snapshot rows
from ``picker`` (one tap picks), and a Cancel button that reports
``("", "")``. The page never writes: picks and cancels funnel into the
``on_pick`` callback, and the service replies to its held invocation
from there. Closing the window counts as a cancel.

Import-safe without a display: constructing the page needs a display,
but importing this module must not.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")

from gi.repository import Adw, Gio, GObject, Gtk, Pango  # noqa: E402

from xx_ui import widgets  # noqa: E402
from xx_contacts import picker as picker_module  # noqa: E402
from xx_contacts.list_ui import install_tap_to_focus  # noqa: E402
from xx_contacts.names import initials as name_initials  # noqa: E402

TITLES = {
    "phone": "Pick a phone number",
    "email": "Pick an email address",
    "contact": "Pick a contact",
}

EMPTY_TEXT = {
    "phone": "No contacts with phone numbers",
    "email": "No contacts with email addresses",
    "contact": "No contacts",
}


class PickItem(GObject.Object):
    """One ``Gio.ListStore`` entry backing a picker row."""

    __gtype_name__ = "XXContactsPickItem"
    uid = GObject.Property(type=str, default="")
    title = GObject.Property(type=str, default="")
    detail = GObject.Property(type=str, default="")
    value = GObject.Property(type=str, default="")


class ContactPickerPage(Adw.NavigationPage):
    """Pick-mode page over snapshot ``rows``; one tap picks."""

    def __init__(self, navigation: Adw.NavigationView, kind: str,
                 rows: list, on_pick, on_cancel=None) -> None:
        super().__init__()
        self._navigation = navigation
        self._kind = picker_module.normalize_kind(kind)
        self._rows = list(rows or ())
        self._on_pick = on_pick
        self._on_cancel = on_cancel if on_cancel is not None else (
            lambda: on_pick("", ""))
        self._settled = False
        self._model = Gio.ListStore.new(PickItem)

        captions = {"phone": "Phone number", "email": "Email address",
                    "contact": "Contact"}
        header = widgets.page_header(
            "Pick", captions[self._kind],
            actions=[("Cancel", self._on_cancel_clicked)])
        top = header.get_first_child()
        self._cancel_button = top.get_last_child()

        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text("Search")
        self._search.set_hexpand(True)
        install_tap_to_focus(self._search)
        search_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                             spacing=8)
        search_bar.add_css_class("xx-searchbar")
        search_bar.add_css_class("xx-contact-search")
        search_bar.append(self._search)

        self._selection = Gtk.NoSelection.new(self._model)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_item_setup)
        factory.connect("bind", self._on_item_bind)
        self._list = Gtk.ListView.new(self._selection, factory)
        self._list.set_single_click_activate(True)
        self._list.set_hexpand(True)
        self._list.set_vexpand(True)
        self._list.set_margin_start(20)
        self._list.set_margin_end(20)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER,
                            Gtk.PolicyType.AUTOMATIC)
        scrolled.set_hexpand(True)
        scrolled.set_vexpand(True)
        scrolled.set_child(self._list)

        middle = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        middle.add_css_class("xx-page-body")
        middle.set_hexpand(True)
        middle.set_vexpand(True)
        middle.append(scrolled)
        self._empty = widgets.empty_state(EMPTY_TEXT[self._kind])
        self._empty.set_valign(Gtk.Align.CENTER)
        self._empty.set_visible(False)
        middle.append(self._empty)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.add_css_class("xx-page")
        root.append(header)
        root.append(middle)
        root.append(search_bar)
        self.set_child(root)
        self.set_title(TITLES[self._kind])
        self._search.connect("search-changed", self._on_search_changed)
        self._list.connect("activate", self._on_row_activated)
        self.connect("map", self._on_map_clear_focus)
        self._apply_rows(self._rows)

    # ------------------------------------------------------------------ rows

    def _apply_rows(self, rows: list) -> None:
        self._model.remove_all()
        for row in rows:
            item = PickItem()
            item.uid = row.uid
            item.title = row.title
            item.detail = row.detail
            item.value = row.value
            self._model.append(item)
        self._empty.set_visible(len(rows) == 0)

    def _on_search_changed(self, _entry: Gtk.SearchEntry) -> None:
        self._apply_rows(picker_module.filter_pick_rows(
            self._rows, self._search.get_text()))

    def _on_row_activated(self, _view: Gtk.ListView, position: int) -> None:
        item = self._model.get_item(position)
        if item is None or self._settled:
            return
        self._settled = True
        self._on_pick(item.uid, item.value)

    def _on_cancel_clicked(self, _button: Gtk.Button | None = None) -> None:
        if self._settled:
            return
        self._settled = True
        self._on_cancel()

    def _on_map_clear_focus(self, _page: Adw.NavigationPage) -> None:
        window = self.get_root()
        if window is not None:
            try:
                window.set_focus(None)
            except Exception:
                pass

    # ---------------------------------------------------------------- factory

    def _on_item_setup(self, _factory: Gtk.SignalListItemFactory,
                       list_item: Gtk.ListItem) -> None:
        disc = widgets.avatar("", 40)
        name = Gtk.Label()
        name.set_xalign(0.0)
        name.set_hexpand(True)
        name.add_css_class("xx-title")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(20)
        detail = Gtk.Label()
        detail.set_xalign(0.0)
        detail.set_hexpand(True)
        detail.add_css_class("xx-secondary")
        detail.set_ellipsize(Pango.EllipsizeMode.END)
        detail.set_max_width_chars(24)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_hexpand(True)
        text.append(name)
        text.append(detail)
        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row_box.add_css_class("xx-row")
        row_box.set_valign(Gtk.Align.CENTER)
        row_box.append(disc)
        row_box.append(text)
        list_item.disc = disc
        list_item.name = name
        list_item.detail = detail
        list_item.row_box = row_box
        list_item.set_child(row_box)

    def _on_item_bind(self, _factory: Gtk.SignalListItemFactory,
                      list_item: Gtk.ListItem) -> None:
        item = list_item.get_item()
        list_item.set_selectable(True)
        list_item.set_focusable(True)
        list_item.set_activatable(True)
        list_item.disc.set_text(name_initials(item.title) or "?")
        list_item.name.set_text(item.title)
        list_item.detail.set_text(item.detail)
        list_item.detail.set_visible(bool(item.detail))
        found, position = self._model.find(item)
        n_items = self._model.get_n_items()
        if found and position == n_items - 1:
            list_item.row_box.add_css_class("last")
        else:
            list_item.row_box.remove_css_class("last")


def present_pick_dialog(kind: str, rows: list, on_done) -> bool:
    """Show the picker in its own window; False when there is no display.

    In-process helper for tests and Broadway drivers: the window is a
    bare ``Gtk.Window`` with no application. Production ``Pick`` never
    calls this from the service — the service launches
    ``xx-contacts --pick`` and the GUI reports back over
    ``PickReply``. Closing the window reports a cancel through
    ``on_done``.
    """
    from gi.repository import Gdk

    normalized = picker_module.normalize_kind(kind)
    try:
        if Gdk.Display.get_default() is None:
            return False
        window = Gtk.Window(title=TITLES[normalized])
    except Exception:
        return False
    window.set_decorated(False)
    window.set_default_size(360, 720)
    window.set_size_request(320, 640)
    navigation = Adw.NavigationView()
    settled: list[bool] = []

    def _settle(uid: str, value: str) -> None:
        if settled:
            return
        settled.append(True)
        try:
            on_done(str(uid), str(value))
        finally:
            window.destroy()

    def _on_close(_window: Gtk.Window) -> bool:
        _settle("", "")
        return False

    page = ContactPickerPage(navigation, normalized, rows, _settle,
                             lambda: _settle("", ""))
    navigation.push(page)
    window.set_child(navigation)
    window.connect("close-request", _on_close)
    try:
        window.present()
    except Exception:
        if not settled:
            return False
    return True
