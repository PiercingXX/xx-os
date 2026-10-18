"""Shared GTK helpers for the mailbox stack (thin over mailbox.py).

Every widget here stays a thin view built from xx_ui.widgets:
formatting and mutations live in xx_email.mailbox / xx_email.bodies
(stdlib, pytest-covered). Blocking store/network work always runs on
the worker executor with results posted back via GLib.idle_add —
never on the GTK main loop.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")

from gi.repository import Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from xx_email import mailbox as mailbox_mod  # noqa: E402
from xx_ui import widgets  # noqa: E402


class FitRow(Gtk.Widget):
    """A row of buttons that stacks them, one per line, only where the
    page is narrower than the row (320 px at large text) instead of
    widening the window. ``build`` makes one fresh set of children; it
    runs twice, once per layout, so each layout owns its widgets."""

    __gtype_name__ = "XXEmailFitRow"

    def __init__(self, build, spacing: int = 8) -> None:
        super().__init__()
        self._row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                            spacing=spacing)
        self._column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                               spacing=spacing)
        for child in build():
            self._row.append(child)
        for child in build():
            self._column.append(child)
        self._row.set_parent(self)
        self._column.set_parent(self)
        self._column.set_child_visible(False)
        self._stacked = False
        self._sync_id = 0

    @property
    def stacked(self) -> bool:
        return self._stacked

    def _row_min(self) -> int:
        return self._row.measure(Gtk.Orientation.HORIZONTAL, -1)[0]

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            row_min, row_nat, _b1, _b2 = self._row.measure(orientation, -1)
            col_min, _col_nat, _b1, _b2 = self._column.measure(
                orientation, -1)
            return (min(row_min, col_min), max(row_nat, col_min), -1, -1)
        stacked = 0 <= for_size < self._row_min()
        child = self._column if stacked else self._row
        minimum, natural, _b1, _b2 = child.measure(orientation, for_size)
        return (minimum, natural, -1, -1)

    def do_size_allocate(self, width, height, baseline):
        stacked = width < self._row_min()
        active, idle = ((self._column, self._row) if stacked
                        else (self._row, self._column))
        active.allocate(width, height, -1, None)
        # The hidden layout gets a size it accepts; it is neither drawn
        # nor tappable (child-invisible), so no allocation warnings.
        idle_w = max(width, idle.measure(Gtk.Orientation.HORIZONTAL, -1)[0])
        idle_h = idle.measure(Gtk.Orientation.VERTICAL, idle_w)[0]
        idle.allocate(idle_w, idle_h, -1, None)
        if stacked != self._stacked:
            self._stacked = stacked
            if not self._sync_id:
                self._sync_id = GLib.idle_add(self._sync_visible)

    def _sync_visible(self) -> bool:
        self._sync_id = 0
        self._row.set_child_visible(not self._stacked)
        self._column.set_child_visible(self._stacked)
        self.queue_draw()
        return GLib.SOURCE_REMOVE

    def do_snapshot(self, snapshot):
        self.snapshot_child(
            self._column if self._stacked else self._row, snapshot)

    def do_dispose(self):
        if self._sync_id:
            GLib.source_remove(self._sync_id)
            self._sync_id = 0
        for child in (self._row, self._column):
            if child is not None and child.get_parent() is self:
                child.unparent()
        self._row = self._column = None


class ThreadItem(GObject.Object):
    """One ListView row's fields (ListStore needs GObject items)."""

    __gtype_name__ = "XXEmailThreadItem"

    thread_id = GObject.Property(type=str, default="")
    sender = GObject.Property(type=str, default="")
    subject = GObject.Property(type=str, default="")
    snippet = GObject.Property(type=str, default="")
    age = GObject.Property(type=str, default="")
    unread = GObject.Property(type=bool, default=False)
    starred = GObject.Property(type=bool, default=False)
    attached = GObject.Property(type=bool, default=False)
    selected = GObject.Property(type=bool, default=False)

    @classmethod
    def from_thread(cls, thread) -> "ThreadItem":
        item = cls()
        item.thread_id = thread.id
        name = (thread.from_name or "").strip()
        item.sender = name or thread.from_address
        item.subject = thread.subject or "(no subject)"
        item.snippet = thread.snippet or ""
        item.age = mailbox_mod.time_ago_now(thread.date)
        item.unread = thread.unread_count > 0
        item.starred = bool(thread.starred)
        item.attached = bool(thread.has_attachments)
        item.selected = False
        return item


class MessageItem(GObject.Object):
    """One thread-view row's fields."""

    __gtype_name__ = "XXEmailMessageItem"

    message_id = GObject.Property(type=str, default="")
    sender = GObject.Property(type=str, default="")
    to_line = GObject.Property(type=str, default="")
    date = GObject.Property(type=str, default="")
    body = GObject.Property(type=str, default="")
    unread = GObject.Property(type=bool, default=False)


def initials_for(sender: str) -> tuple[str, bool]:
    """Avatar initials + dashed flag. Numbers/addresses get an empty disc."""
    words = [word for word in (sender or "").split() if word[:1].isalpha()]
    if not words:
        return ("", True)
    if len(words) == 1:
        return (words[0][:2].upper(), False)
    return ((words[0][:1] + words[1][:1]).upper(), False)


def secondary_for(item: ThreadItem) -> str:
    """One-line subject + snippet for the row's secondary label."""
    if item.snippet:
        return f"{item.subject} — {item.snippet}"
    return item.subject


def run_in_background(executor, blocking, done) -> None:
    """Run blocking() on the worker pool; done(result) via idle_add.

    Failures arrive as the exception object so pages can banner them.
    """
    future = executor.submit(blocking)

    def _finish(completed) -> None:
        try:
            result = completed.result()
        except Exception as exc:  # surface, never throw on the loop
            result = exc
        GLib.idle_add(done, result)

    future.add_done_callback(_finish)


def new_thread_store(items: list | None = None) -> Gio.ListStore:
    store = Gio.ListStore.new(ThreadItem)
    for thread in items or []:
        store.append(ThreadItem.from_thread(thread))
    return store


def replace_store(store: Gio.ListStore, threads: list) -> None:
    # One splice, not remove_all + appends: the ListView binds
    # eagerly on every emission, so incremental fills make every
    # mid-fill row look final (each keeps ``last``, killing every
    # hairline). A single emission binds once at final positions.
    store.splice(0, store.get_n_items(),
                 [ThreadItem.from_thread(thread)
                  for thread in threads])


def find_item(store: Gio.ListStore, thread_id: str):
    for position in range(store.get_n_items()):
        item = store.get_item(position)
        if item.thread_id == thread_id:
            return item
    return None


def overflow_menu(items: list[tuple[str, object]]) -> Gtk.MenuButton:
    """Header ⋯ button opening a surface popover (48 px rows, themed)."""
    menu = Gio.Menu()
    group = Gio.SimpleActionGroup()
    for index, (label_text, callback) in enumerate(items):
        name = f"item{index:d}"
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate",
                       lambda _a, _p, cb=callback: cb())
        group.add_action(action)
        menu.append(label_text, f"overflow.{name}")
    button = Gtk.MenuButton()
    button.set_icon_name("view-more-symbolic")
    button.set_menu_model(menu)
    button.insert_action_group("overflow", group)
    button.add_css_class("xx-btn")
    button.add_css_class("xx-btn-icon")
    return button


def header_with_menu(title: str, caption_text: str,
                     items: list[tuple[str, object]]) -> Gtk.Box:
    """PageHeader with a trailing ⋯ popover (list screens stay clean)."""
    header = widgets.page_header(title, caption_text)
    top = header.get_first_child()
    top.append(overflow_menu(items))
    return header


def tap_to_focus(widget) -> None:
    """Focus only on tap: unfocusable until a CAPTURE-phase click."""
    widget.set_can_focus(False)
    tap = Gtk.GestureClick()
    tap.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    tap.connect("pressed", lambda *_a: (
        widget.set_can_focus(True), widget.grab_focus()))
    widget.add_controller(tap)


def make_thread_row() -> tuple:
    """Build one mailbox row's widgets (factory setup, once per row)."""
    leading = widgets.avatar("", 40, dashed=True)
    trail = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    trail.set_valign(Gtk.Align.CENTER)
    attach = widgets.icon_image("mail-attachment-symbolic")
    attach.add_css_class("email-dim")
    attach.set_visible(False)
    trail.append(attach)
    star = widgets.icon_image("starred-symbolic")
    star.set_visible(False)
    trail.append(star)
    dot_box = Gtk.Box()
    dot_box.add_css_class("email-dot")
    dot_box.set_size_request(8, 8)
    dot_box.set_valign(Gtk.Align.CENTER)
    dot_box.set_visible(False)
    trail.append(dot_box)
    row = widgets.list_row("x", "x", meta="x", leading=leading,
                           trailing=trail)
    middle = leading.get_next_sibling()
    title = middle.get_first_child()
    sub = title.get_next_sibling()
    meta = middle.get_next_sibling()
    row._email_refs = (leading, title, sub, meta, attach, star,
                       dot_box)
    return (row,) + row._email_refs


def bind_thread_row(row_widgets: tuple, item: ThreadItem) -> None:
    row, avatar, title, sub, meta, attach, star, dot = row_widgets
    initials, dashed = initials_for(item.sender)
    avatar.set_label(initials)
    if dashed:
        avatar.add_css_class("dashed")
    else:
        avatar.remove_css_class("dashed")
    title.set_label(item.sender)
    if item.unread:
        title.add_css_class("email-unread-title")
    else:
        title.remove_css_class("email-unread-title")
    sub.set_label(secondary_for(item))
    meta.set_label(item.age)
    attach.set_visible(item.attached)
    star.set_visible(item.starred)
    dot.set_visible(item.unread)
    if item.selected:
        row.add_css_class("selected")
    else:
        row.remove_css_class("selected")


def ellipsized(label: Gtk.Label) -> Gtk.Label:
    """Single-line label that shrinks instead of overflowing 320 px."""
    label.set_ellipsize(Pango.EllipsizeMode.END)
    label.set_wrap(False)
    label.set_lines(1)
    label.set_xalign(0.0)
    label.set_hexpand(True)
    return label


def wrapped(label: Gtk.Label) -> Gtk.Label:
    """Body label that wraps inside 360/320 px instead of overflowing."""
    label.set_wrap(True)
    label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    label.set_xalign(0.0)
    label.set_hexpand(True)
    return label


def thread_list_view(store: Gio.ListStore, on_open, on_toggle_select,
                     on_swipe, swipe_left: str,
                     swipe_right: str) -> Gtk.ListView:
    """Gtk.ListView for a thread list (long lists stay list-backed)."""
    factory = Gtk.SignalListItemFactory()

    def _setup(_factory, list_item) -> None:
        row_widgets = make_thread_row()
        list_item.set_child(row_widgets[0])

        click = Gtk.GestureClick()
        click.connect("pressed", lambda g, n, x, y:
                      _on_row_click(list_item, n))
        row_widgets[0].add_controller(click)

        if on_toggle_select is not None:
            hold = Gtk.GestureLongPress()
            hold.connect("pressed", lambda g, x, y:
                         _on_row_hold(list_item))
            row_widgets[0].add_controller(hold)

        if on_swipe is not None:
            swipe = Gtk.GestureSwipe()
            swipe.connect("swipe", lambda g, vx, vy:
                          _on_row_swipe(list_item, vx))
            row_widgets[0].add_controller(swipe)

    def _on_row_click(list_item, n_press: int) -> None:
        item = list_item.get_item()
        if item is not None:
            on_open(item.thread_id, n_press)

    def _on_row_hold(list_item) -> None:
        item = list_item.get_item()
        if item is not None:
            on_toggle_select(item.thread_id)

    def _on_row_swipe(list_item, velocity_x: float) -> None:
        item = list_item.get_item()
        if item is None:
            return
        action = swipe_right if velocity_x > 0 else swipe_left
        on_swipe(action, item.thread_id)

    def _bind(_factory, list_item) -> None:
        item = list_item.get_item()
        child = list_item.get_child()
        bind_thread_row((child,) + child._email_refs, item)
        if list_item.get_position() == store.get_n_items() - 1:
            child.add_css_class("last")
        else:
            child.remove_css_class("last")

    factory.connect("setup", _setup)
    factory.connect("bind", _bind)
    view = Gtk.ListView(model=Gtk.NoSelection.new(store), factory=factory)
    view.set_single_click_activate(True)
    return view

