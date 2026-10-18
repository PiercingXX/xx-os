"""Recents page: call log with grouping, chips and verdict lines.

Consumes the pure shapes in ``recents.py`` over the warm SQLite store.
Lists follow the phone rules: ``Gtk.ListView`` with
``set_single_click_activate(True)`` over a ``Gtk.NoSelection`` model
(single click calls back), long-press menus via one ``Gtk.GestureLongPress``
that resolves the row under the pointer (``pick`` + walk up), never the
selection. Call rows carry Android-style millisecond ids, so the model
item keeps them in int64 properties (``GObject.TYPE_INT64``).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, Gio, GObject, Gtk, Pango  # noqa: E402

from xx_ui import widgets  # noqa: E402
import xx_phone  # noqa: E402
from xx_dialer import contacts_client, policy, recents  # noqa: E402
from xx_dialer.store import (  # noqa: E402
    CALL_TYPE_INCOMING,
    CALL_TYPE_MISSED,
    CALL_TYPE_OUTGOING,
    Store,
)


@dataclass
class RecentsDeps:
    db_file: Path
    home: Path | None = None
    voice: object = None
    contacts: object = None
    launch_sms: object = None
    copy_text: object = None
    notice: object = None
    now_ms: object = None
    navigate: object = None


GLYPH_ICONS = {
    recents.GLYPH_RING: "call-incoming-symbolic",
    recents.GLYPH_SILENCED: "notifications-disabled-symbolic",
    recents.GLYPH_BLOCKED: "call-stop-symbolic",
    recents.GLYPH_NONE: "phone-symbolic",
}

OVERFLOW_PAGES = (("Setup", "setup"), ("Import & export", "backup"),
                  ("Logs", "logs"))


def _default_launch_sms(uri: str) -> None:
    Gio.AppInfo.launch_default_for_uri(uri, None)


def _default_copy_text(text: str) -> None:
    display = Gdk.Display.get_default()
    if display is None:
        raise RuntimeError("no display for clipboard")
    display.get_clipboard().set(text)


class RecentItem(GObject.Object):
    __gtype_name__ = "XxRecentItem"

    kind = GObject.Property(type=str, default="item")
    row_id = GObject.Property(type=GObject.TYPE_INT64, default=0)
    date = GObject.Property(type=GObject.TYPE_INT64, default=0)
    title = GObject.Property(type=str, default="")
    number = GObject.Property(type=str, default="")
    when = GObject.Property(type=str, default="")
    chip = GObject.Property(type=str, default="")
    meta = GObject.Property(type=str, default="")
    verdict = GObject.Property(type=str, default="")
    count = GObject.Property(type=int, default=0)
    expanded = GObject.Property(type=bool, default=False)
    identity = GObject.Property(type=str, default="")


class RecentsController:
    """Store-backed Recents state plus every row action.

    The view is thin: it renders ``items`` and forwards gestures here.
    All side effects run through the injected deps, so headless tests
    drive this with fakes and a real seeded store.
    """

    FILTERS = (recents.FILTER_ALL, recents.FILTER_MISSED,
               recents.FILTER_SILENCED, recents.FILTER_BLOCKED)

    def __init__(self, deps: RecentsDeps) -> None:
        self._deps = deps
        self.filter_name = recents.FILTER_ALL
        self.deeplink: str | None = None
        self.grouping_on = True
        self.expanded: set[str] = set()
        self.merged: list[recents.MergedCall] = []
        self.starred: list[tuple[str, str, str]] = []
        self.unread = 0
        self._by_key: dict[int, recents.MergedCall] = {}

    # ---- loading -------------------------------------------------------

    def _now_ms(self) -> int:
        if self._deps.now_ms is not None:
            return int(self._deps.now_ms())
        return int(time.time() * 1000)

    def _region(self) -> str:
        try:
            from xx_dialer import config as app_config

            return policy.region_of(app_config.load(self._deps.home))
        except Exception:
            return "US"

    def load(self) -> None:
        with Store(self._deps.db_file) as store:
            try:
                from xx_dialer import config as app_config

                self.grouping_on = bool(app_config.load(
                    self._deps.home).get("group_recents", True))
            except Exception:
                self.grouping_on = True
            call_rows = store.recent_calls()
            screen_rows = store.last_screened(400)
            mirror = store.all_mirror()
            viewed_at = store.get_recents_viewed()
        region = self._region()
        names = {row.e164: row.display_name for row in mirror
                 if row.display_name}
        outgoing: dict[str, list[int]] = {}
        platform = []
        for row in call_rows:
            e164 = policy.canonical_number(row.number or "", region)
            missed = row.call_type == CALL_TYPE_MISSED \
                or (row.call_type == CALL_TYPE_INCOMING
                    and (row.duration or 0) <= 0)
            platform.append(recents.PlatformCall(
                key=row.row_id, time_ms=row.date, raw=row.number,
                e164=e164, cached_name=row.cached_name, missed=missed,
                outgoing=row.call_type == CALL_TYPE_OUTGOING,
                duration_s=row.duration or 0))
            if row.call_type == CALL_TYPE_OUTGOING and e164:
                outgoing.setdefault(e164, []).append(row.date)
        logs = [recents.ScreenEntry(
            row_id=row.row_id, at=row.at, e164=row.e164,
            verdict=row.verdict, reason=row.reason, mode=row.mode,
            answered=row.answered) for row in screen_rows]
        answered_by_screen = {entry.row_id: entry.answered
                              for entry in logs}
        self.merged = recents.merge(platform, logs)
        for call in self.merged:
            if call.display_name is None and call.e164:
                call.display_name = names.get(call.e164)
            if (call.screen_id is not None
                    and answered_by_screen.get(call.screen_id)
                    and not call.outgoing):
                call.missed = False
            if call.e164 and call.e164 in outgoing:
                weekday = recents.recent_outgoing_weekday(
                    outgoing[call.e164], call.time_ms)
                if weekday and call.reason.startswith("you called them"):
                    call.reason = "you called them %s" % weekday
        self._by_key = {call.key: call for call in self.merged}
        self.starred = recents.starred_strip(mirror)
        self.unread = recents.unread_missed(self.merged, viewed_at)

    def visible_calls(self) -> list[recents.MergedCall]:
        return [call for call in self.merged
                if recents.passes(self.filter_name, call)
                and (self.deeplink is None
                     or call.identity == self.deeplink)]

    def items(self) -> list[RecentItem]:
        now = self._now_ms()
        region = self._region()
        out: list[RecentItem] = []
        for grouped in recents.group(self.visible_calls(),
                                     self.grouping_on, self.expanded):
            if isinstance(grouped, recents.Group):
                first = grouped.calls[0]
                item = RecentItem()
                item.kind = "header"
                item.identity = grouped.identity
                item.title = recents.display_name_of(first, region)
                item.number = xx_phone.display(
                    grouped.identity,
                    xx_phone.display_country(region))
                item.count = len(grouped.calls)
                item.expanded = grouped.expanded
                item.date = first.time_ms
                out.append(item)
                if grouped.expanded:
                    for call in grouped.calls:
                        out.append(self._row_item(call, now, region))
            else:
                out.append(self._row_item(grouped.call, now, region))
        return out

    def _row_item(self, call: recents.MergedCall,
                  now_ms: int, region: str | None = None) -> RecentItem:
        if region is None:
            region = self._region()
        item = RecentItem()
        item.kind = "item"
        item.row_id = call.key
        item.date = call.time_ms
        item.title = recents.display_name_of(call, region)
        item.number = xx_phone.display(
            call.e164 or call.raw,
            xx_phone.display_country(region))
        item.when = recents.format_when(call.time_ms, now_ms,
                                        self._local_tz())
        item.chip = recents.direction_chip(call)
        duration = recents.duration_text(call.duration_s, call.missed)
        item.meta = duration
        item.verdict = recents.verdict_line(call.glyph, call.reason,
                                            call.observed)
        if call.identity:
            item.identity = call.identity
        return item

    @staticmethod
    def _local_tz():
        from datetime import datetime

        return datetime.now().astimezone().tzinfo

    def find(self, key: int) -> recents.MergedCall | None:
        return self._by_key.get(key)

    # ---- viewing --------------------------------------------------------

    def mark_viewed(self) -> None:
        if recents.should_clear(True, self.filter_name
                                == recents.FILTER_MISSED):
            try:
                with Store(self._deps.db_file) as store:
                    store.set_recents_viewed(self._now_ms())
            except Exception:
                pass
            self.unread = 0

    def toggle_group(self, identity: str) -> None:
        if identity in self.expanded:
            self.expanded.remove(identity)
        else:
            self.expanded.add(identity)

    # ---- row actions ------------------------------------------------------

    def _notice(self, text: str) -> None:
        if self._deps.notice is not None:
            try:
                self._deps.notice(text)
            except Exception:
                pass

    @staticmethod
    def _dialable(call: recents.MergedCall) -> str | None:
        if call.e164:
            return call.e164
        if call.raw and call.raw.strip():
            return call.raw.strip()
        return None

    def _uid_for(self, call: recents.MergedCall) -> str | None:
        if not call.e164:
            return None
        try:
            with Store(self._deps.db_file) as store:
                row = store.find_mirror(call.e164)
        except Exception:
            return None
        return row.lookup_key if row is not None else None

    def call_back(self, call: recents.MergedCall) -> None:
        number = self._dialable(call)
        if not number:
            self._notice("This entry has no number to call.")
            return
        self.place(number)

    def place(self, number: str) -> None:
        voice = self._deps.voice
        if voice is None:
            self._notice("No modem is available to place the call.")
            return

        def _done(ok: bool, detail) -> None:
            if not ok:
                self._notice("Could not place the call: %s"
                             % (detail or "modem error"))
                return
            self.mark_viewed()

        try:
            voice.place(number, _done)
        except Exception as err:
            self._notice("Could not place the call: %s" % (err,))

    def message(self, call: recents.MergedCall) -> None:
        number = self._dialable(call)
        if not number:
            self._notice("This entry has no number to message.")
            return
        launcher = self._deps.launch_sms or _default_launch_sms
        try:
            launcher("sms:%s" % number)
        except Exception:
            self._notice("No messaging app is available.")

    def copy_number(self, call: recents.MergedCall) -> None:
        number = self._dialable(call)
        if not number:
            return
        copier = self._deps.copy_text or _default_copy_text
        try:
            copier(number)
        except Exception:
            self._notice("Could not copy the number.")
            return

    def add_contact(self, call: recents.MergedCall) -> None:
        number = self._dialable(call)
        if not number:
            self._notice("This entry has no number to save.")
            return
        self.add_number_to_contacts(number, call.display_name)

    def add_number_to_contacts(self, number: str,
                               name: str | None = None) -> None:
        client = self._deps.contacts
        if client is None:
            self._notice("Contacts app is not installed.")
            return
        vcard = contacts_client.new_contact_vcard(number, name)

        def _done(_uid: str) -> None:
            self.load()

        def _failed(_err) -> None:
            self._notice("Could not save the contact.")

        try:
            client.create_async(vcard, _done, _failed)
        except Exception:
            self._notice("Could not save the contact.")

    def block(self, call: recents.MergedCall) -> None:
        number = self._dialable(call)
        if not number:
            return
        uid = self._uid_for(call)
        client = self._deps.contacts
        if uid is not None and client is not None:
            try:
                client.set_blocked(uid, True)
            except Exception:
                pass
        else:
            try:
                with Store(self._deps.db_file) as store:
                    store.add_blocked(policy.canonical_number(
                        number, self._region()) or number)
            except Exception:
                pass
        self.load()

    def ring_next_time(self, call: recents.MergedCall) -> None:
        uid = self._uid_for(call)
        client = self._deps.contacts
        if uid is None or client is None:
            self._notice("No contact for this number — "
                         "save it to contacts first.")
            return
        try:
            client.set_star(uid, True)
        except Exception:
            self._notice("Could not star the contact.")
            return
        self._notice("Starred — the next call will ring.")
        self.load()

    def add_to_business(self, call: recents.MergedCall) -> None:
        uid = self._uid_for(call)
        client = self._deps.contacts
        if uid is None or client is None:
            self._notice("No contact for this number — "
                         "save it to contacts first.")
            return
        try:
            client.set_business(uid, True)
        except Exception:
            self._notice("Could not update the contact.")
            return
        self._notice("Added to Business.")
        self.load()

    def delete(self, call: recents.MergedCall) -> bool:
        try:
            with Store(self._deps.db_file) as store:
                removed = store.delete_call(call.key)
        except Exception:
            removed = False
        if removed:
            self._notice("Deleted.")
            self.load()
        else:
            self._notice("Could not delete this entry.")
        return removed


def _menu_buttons(call: recents.MergedCall,
                  controller: RecentsController) -> list[tuple[str, object]]:
    number = RecentsController._dialable(call)
    entries: list[tuple[str, object]] = []
    if number is not None:
        entries.extend([
            ("Call back", controller.call_back),
            ("Message", controller.message),
            ("Add to contacts", controller.add_contact),
            ("Copy number", controller.copy_number),
            ("Block", controller.block),
        ])
    if call.glyph == recents.GLYPH_SILENCED and call.e164:
        entries.extend([
            ("Ring next time", controller.ring_next_time),
            ("Add to Business", controller.add_to_business),
        ])
    entries.append(("Delete", controller.delete))
    return entries


class RecentsView:
    """The Recents page widget plus its refresh entry points."""

    def __init__(self, controller: RecentsController) -> None:
        self.controller = controller
        self._store = Gio.ListStore(item_type=RecentItem)
        self._suppress_activate = False
        navigate = getattr(controller._deps, "navigate", None)
        self._navigate = navigate if callable(navigate) else None
        self._root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                             spacing=0)
        self._build()
        self.refresh()

    @property
    def widget(self) -> Gtk.Widget:
        return self._root

    def _pad(self, widget: Gtk.Widget) -> Gtk.Widget:
        widget.add_css_class("xx-page-body")
        return widget

    def _build(self) -> None:
        self._header = widgets.page_header(
            "Recents",
            overflow_cb=self._open_overflow
            if self._navigate is not None else None)
        self._caption = widgets.caption("")
        self._header.append(self._caption)
        self._root.append(self._header)

        self._chips_wrap = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._pad(self._chips_wrap)
        chips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                        spacing=8)
        chips.add_css_class("xx-dialer-chips")
        chips_scroll = Gtk.ScrolledWindow()
        chips_scroll.set_policy(Gtk.PolicyType.AUTOMATIC,
                                Gtk.PolicyType.NEVER)
        chips_scroll.set_min_content_width(0)
        chips_scroll.set_child(chips)
        self._chips_wrap.append(chips_scroll)
        self._chips_box = chips
        self._chip_buttons: list[tuple[str, Gtk.Button]] = []
        self._root.append(self._chips_wrap)

        self._star_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                 spacing=8)
        star_scroll = Gtk.ScrolledWindow()
        star_scroll.set_policy(Gtk.PolicyType.AUTOMATIC,
                               Gtk.PolicyType.NEVER)
        star_scroll.set_min_content_width(0)
        star_scroll.set_child(self._star_bar)
        self._star_wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                  spacing=2)
        self._pad(self._star_wrap)
        self._star_wrap.append(widgets.caption("Starred"))
        self._star_wrap.append(star_scroll)
        self._root.append(self._star_wrap)

        self._deep_note = Gtk.Button(label="")
        self._deep_note.add_css_class("xx-btn")
        self._deep_note.add_css_class("xx-btn-text")
        deep_label = self._deep_note.get_child()
        if isinstance(deep_label, Gtk.Label):
            deep_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._deep_note.connect("clicked", self._on_clear_deep)
        self._pad(self._deep_note)
        self._root.append(self._deep_note)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_setup)
        factory.connect("bind", self._on_bind)
        selection = Gtk.NoSelection(model=self._store)
        self._list = Gtk.ListView(model=selection, factory=factory)
        self._list.set_single_click_activate(True)
        self._list.connect("activate", self._on_activate)
        long_press = Gtk.GestureLongPress.new()
        long_press.connect("pressed", self._on_long_press)
        self._list.add_controller(long_press)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER,
                          Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(self._list)
        self._root.append(scroll)

        # No calls (or none in a filter) shows an empty list, no line.
        self._empty = widgets.label("", "xx-empty", xalign=0.5)
        self._empty.set_halign(Gtk.Align.CENTER)
        self._empty.set_valign(Gtk.Align.CENTER)
        self._empty.set_vexpand(True)
        self._root.append(self._empty)

    def _overflow_anchor(self) -> Gtk.Widget:
        top = self._header.get_first_child()
        if top is not None:
            last = top.get_last_child()
            if last is not None:
                return last
        return self._header

    def _open_overflow(self) -> None:
        if self._navigate is None:
            return
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        popover = Gtk.Popover()
        popover.set_parent(self._overflow_anchor())
        point = Gdk.Rectangle()
        point.x, point.y, point.width, point.height = 0, 0, 1, 1
        popover.set_pointing_to(point)
        popover.set_child(box)
        for title, name in OVERFLOW_PAGES:
            button = widgets.menu_row(title)
            button.connect(
                "clicked", self._on_overflow_go, popover, name)
            box.append(button)
        popover.popup()

    def _on_overflow_go(self, _button: Gtk.Button,
                        popover: Gtk.Popover, name: str) -> None:
        popover.popdown()
        if self._navigate is not None:
            self._navigate(name)

    # ---- factory ----------------------------------------------------------

    @staticmethod
    def _row_label(text: str, cls: str, xalign: float = 0.0,
                   cap: int | None = None) -> Gtk.Label:
        label = Gtk.Label(label="", xalign=xalign)
        label.add_css_class(cls)
        if cap is None:
            label.set_text(text)
            label.set_ellipsize(Pango.EllipsizeMode.END)
        else:
            widgets.fit_text(label, text, cap)
        return label

    def _on_setup(self, _factory, list_item) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                       spacing=0)
        root.add_css_class("xx-row")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                        spacing=2)
        inner.set_margin_start(20)
        inner.set_margin_end(20)
        inner.set_valign(Gtk.Align.CENTER)
        root.set_valign(Gtk.Align.CENTER)
        root.append(inner)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                         spacing=12)
        header.set_valign(Gtk.Align.CENTER)
        arrow = widgets.icon_image("go-next-symbolic")
        arrow.set_valign(Gtk.Align.CENTER)
        title = self._row_label("", "xx-title")
        title.set_hexpand(True)
        title.set_valign(Gtk.Align.CENTER)
        count = self._row_label("", "xx-time", xalign=1.0, cap=8)
        count.set_valign(Gtk.Align.CENTER)
        header.append(arrow)
        header.append(title)
        header.append(count)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                      spacing=12)
        row.set_valign(Gtk.Align.CENTER)
        glyph = widgets.icon_image("phone-symbolic")
        glyph.set_valign(Gtk.Align.CENTER)
        middle = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                         spacing=2)
        middle.set_hexpand(True)
        middle.set_valign(Gtk.Align.CENTER)
        name = self._row_label("", "xx-title")
        sub = self._row_label("", "xx-secondary")
        verdict = widgets.wrap_label("")
        middle.append(name)
        middle.append(sub)
        middle.append(verdict)
        when = self._row_label("", "xx-time", xalign=1.0, cap=12)
        when.set_valign(Gtk.Align.START)
        row.append(glyph)
        row.append(middle)
        row.append(when)
        inner.append(header)
        inner.append(row)
        list_item.set_child(root)
        list_item.xx_parts = (root, header, arrow, title, count,
                              row, glyph, name, when, sub, verdict)

    def _row_icon(self, call) -> tuple[str, bool]:
        if call.outgoing:
            return ("call-outgoing-symbolic", False)
        if call.missed:
            return ("call-missed-symbolic", True)
        if call.glyph == recents.GLYPH_BLOCKED:
            return (GLYPH_ICONS[call.glyph], True)
        return (GLYPH_ICONS.get(call.glyph,
                                GLYPH_ICONS[recents.GLYPH_NONE]), False)

    def _on_bind(self, _factory, list_item) -> None:
        item = list_item.get_item()
        parts = list_item.xx_parts
        (root, header, arrow, title, count,
         row, glyph, name, when, sub, verdict) = parts
        total = self._store.get_n_items()
        try:
            last = list_item.get_position() == total - 1
        except Exception:
            last = False
        if last:
            root.add_css_class("last")
        else:
            root.remove_css_class("last")
        is_header = item.kind == "header"
        header.set_visible(is_header)
        row.set_visible(not is_header)
        verdict.set_visible(not is_header
                            and bool(item.verdict))
        if is_header:
            root.add_css_class("xx-dialer-group")
        else:
            root.remove_css_class("xx-dialer-group")
        if is_header:
            arrow.set_from_icon_name(
                "go-down-symbolic" if item.expanded
                else "go-next-symbolic")
            title.set_text(item.title or item.number)
            widgets.fit_text(count, "×%d" % item.count, 8)
            root.recent_key = "header:%s" % item.identity
            return
        call = self.controller.find(int(item.row_id))
        icon_name, danger = ("phone-symbolic", False)
        if call is not None:
            icon_name, danger = self._row_icon(call)
        glyph.set_from_icon_name(icon_name)
        for widget in (sub, verdict, glyph):
            if danger:
                widget.add_css_class("xx-danger-text")
            else:
                widget.remove_css_class("xx-danger-text")
        name.set_text(item.title)
        widgets.fit_text(when, item.when, 12)
        chip_line = item.chip
        if item.meta:
            chip_line = "%s · %s" % (item.chip, item.meta)
        sub.set_text(chip_line)
        verdict.set_text(item.verdict)
        root.recent_key = "row:%d" % item.row_id

    # ---- gestures -----------------------------------------------------------

    def _on_activate(self, _view, position: int) -> None:
        if self._suppress_activate:
            self._suppress_activate = False
            return
        item = self._store.get_item(position)
        if item is None:
            return
        if item.kind == "header":
            self.controller.toggle_group(item.identity)
            self.refresh()
            return
        call = self.controller.find(int(item.row_id))
        if call is not None:
            self.controller.call_back(call)
            self.refresh()

    def _on_long_press(self, gesture, x: float, y: float) -> None:
        widget = gesture.get_widget()
        target = widget.pick(x, y, Gtk.PickFlags.DEFAULT)
        while target is not None and target is not widget:
            key = getattr(target, "recent_key", None)
            if key is not None:
                break
            target = target.get_parent()
        else:
            target = None
        if target is None:
            return
        self._suppress_activate = True
        self._open_menu(str(key), target)

    def _resolve_key(self, key: str) -> recents.MergedCall | None:
        if key.startswith("row:"):
            try:
                return self.controller.find(int(key[4:]))
            except ValueError:
                return None
        return None

    def _open_menu(self, key: str, anchor: Gtk.Widget) -> None:
        call = self._resolve_key(key)
        if call is None:
            if key.startswith("header:"):
                self.controller.toggle_group(key[len("header:"):])
                self.refresh()
            return
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                      spacing=0)
        popover = Gtk.Popover()
        popover.set_parent(anchor)
        point = Gdk.Rectangle()
        point.x, point.y, point.width, point.height = 0, 0, 1, 1
        popover.set_pointing_to(point)
        popover.set_child(box)
        for label, action in _menu_buttons(call, self.controller):
            button = widgets.menu_row(label)

            def _clicked(_btn, step=action) -> None:
                popover.popdown()
                step(call)
                self.refresh()

            button.connect("clicked", _clicked)
            box.append(button)
        popover.popup()
        return popover

    def _on_filter(self, name: str) -> None:
        self.controller.filter_name = name
        if name == recents.FILTER_MISSED:
            self.controller.mark_viewed()
        self.refresh()

    def _on_clear_deep(self, _button) -> None:
        self.controller.deeplink = None
        self.refresh()

    def _on_star(self, _button, e164: str) -> None:
        self.controller.deeplink = e164
        self.refresh()

    # ---- refresh --------------------------------------------------------------

    def refresh(self) -> None:
        self.controller.load()
        while self._store.get_n_items():
            self._store.remove(0)
        for item in self.controller.items():
            self._store.append(item)
        self._render_chrome()

    def _on_filter_clicked(self, _button: Gtk.Button, name: str) -> None:
        self._on_filter(name)

    def _render_chrome(self) -> None:
        current = self.controller.filter_name
        while True:
            child = self._chips_box.get_first_child()
            if child is None:
                break
            self._chips_box.remove(child)
        self._chip_buttons = []
        for name in RecentsController.FILTERS:
            button = Gtk.Button.new_with_label(name.upper())
            button.add_css_class("xx-chip")
            if name == current:
                button.add_css_class("selected")
            button.connect("clicked", self._on_filter_clicked, name)
            self._chips_box.append(button)
            self._chip_buttons.append((name, button))
        while True:
            child = self._star_bar.get_first_child()
            if child is None:
                break
            self._star_bar.remove(child)
        starred = self.controller.starred
        self._star_wrap.set_visible(bool(starred))
        for uid, name, e164 in starred:
            from xx_dialer import t9

            region = self.controller._region()
            shown = name or xx_phone.display(
                e164, xx_phone.display_country(region))
            button = Gtk.Button()
            cell = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                           spacing=8)
            cell.set_valign(Gtk.Align.CENTER)
            cell.append(widgets.avatar(
                t9.initials(name or e164), 32))
            text = self._row_label(shown, "xx-title")
            cell.append(text)
            button.set_child(cell)
            button.set_tooltip_text(shown)
            button.connect("clicked", self._on_star, e164)
            self._star_bar.append(button)
        unread = self.controller.unread
        if unread:
            self._caption.set_text(
                recents.missed_title(unread).upper())
        else:
            total = len(self.controller.merged)
            self._caption.set_text(
                "%d CALL%s" % (total, "" if total == 1 else "S"))
        deeplink = self.controller.deeplink
        self._deep_note.set_visible(deeplink is not None)
        if deeplink is not None:
            self._deep_note.set_label(
                "Showing calls with %s — show all"
                % xx_phone.display(
                    deeplink, xx_phone.display_country(
                        self.controller._region())))
        self._empty.set_visible(self._store.get_n_items() == 0)


def build_recents(controller: RecentsController) -> RecentsView:
    return RecentsView(controller)
