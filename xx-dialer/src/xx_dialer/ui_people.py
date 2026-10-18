"""People page: the contacts cache sectioned for the engine.

Sections come from the warm SQLite mirror (kept by
``ContactsClient`` from xx-contacts, or edited locally when that
service is absent): Starred, Business, Blocked, then Everyone. Every
contact appears exactly once; a blocked contact sits in Blocked even
when also starred, because Block beats Star in the engine. Tapping a
row opens a sheet with Call, Message, and the tier switches — tag
changes go through the ``ContactsClient`` (atomic ``UpdateTags``
deltas when the service is present, recorded pending edits when
absent); a number
with no contact behind it blocks into the local block table.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, Gio, GObject, Gtk, Pango  # noqa: E402

from xx_ui import widgets  # noqa: E402
import xx_phone  # noqa: E402
from xx_dialer import policy, t9  # noqa: E402
from xx_dialer.store import BLOCKED_GROUP, BIZ_TIER, Store  # noqa: E402


def _outline(text: str, callback=None):
    """An outline button whose label wraps between words (large text,
    320 px) instead of ellipsizing; it never widens the window."""
    return widgets.wrap_to_fit(widgets.outline_button(text, callback))


@dataclass
class PeopleDeps:
    db_file: Path
    home: Path | None = None
    voice: object = None
    contacts: object = None
    launch_sms: object = None
    notice: object = None


@dataclass
class Person:
    uid: str
    display_name: str
    numbers: list[str] = field(default_factory=list)
    starred: bool = False
    business: bool = False
    blocked: bool = False

    @property
    def primary(self) -> str:
        return self.numbers[0] if self.numbers else ""


@dataclass
class PeopleSnapshot:
    sections: list[tuple[str, list[Person]]] = field(default_factory=list)
    contacts_present: bool = False
    empty_reason: str = ""


def section_people(store: Store) -> list[tuple[str, list[Person]]]:
    """Mirror + tiers + stars into Starred/Business/Blocked/Everyone."""
    tiers: dict[str, set[str]] = {}
    for key, tier, _added in store.all_tiers():
        tiers.setdefault(key, set()).add(tier)
    stars = set(store.all_starred())
    by_uid: dict[str, Person] = {}
    for row in store.all_mirror():
        person = by_uid.get(row.lookup_key)
        if person is None:
            person = Person(uid=row.lookup_key,
                            display_name=row.display_name)
            by_uid[row.lookup_key] = person
        if row.e164 not in person.numbers:
            person.numbers.append(row.e164)
        if not person.display_name and row.display_name:
            person.display_name = row.display_name
        person.starred = person.starred or row.starred \
            or row.lookup_key in stars
    for uid, person in by_uid.items():
        names = tiers.get(uid, set())
        person.business = BIZ_TIER in names
        person.blocked = BLOCKED_GROUP in names
        person.numbers.sort()
    ordered = sorted(by_uid.values(),
                     key=lambda item: (item.display_name or
                                       item.primary).lower())
    starred = [item for item in ordered if item.starred
               and not item.blocked]
    business = [item for item in ordered if item.business
                and not item.starred and not item.blocked]
    blocked = [item for item in ordered if item.blocked]
    everyone = [item for item in ordered if not item.starred
                and not item.business and not item.blocked]
    return [("Starred", starred), ("Business", business),
            ("Blocked", blocked), ("Everyone", everyone)]


def person_marks(person: Person, section: str = "") -> str:
    """Trailing marks for a People row. The mark its section already
    names (a star under Starred, Blocked under Blocked) is left off."""
    marks = ""
    if person.starred and section != "Starred":
        marks += "* "
    if person.business and section != "Business":
        marks += "Biz "
    if person.blocked and section != "Blocked":
        marks += "Blocked "
    return marks.strip()


class PeopleController:
    """Store-backed People state plus the sheet actions."""

    def __init__(self, deps: PeopleDeps) -> None:
        self._deps = deps
        self._snapshot = PeopleSnapshot()

    def _notice(self, text: str) -> None:
        if self._deps.notice is not None:
            try:
                self._deps.notice(text)
            except Exception:
                pass

    def load(self) -> PeopleSnapshot:
        with Store(self._deps.db_file) as store:
            sections = section_people(store)
            empty = all(not people for _title, people in sections)
        client = self._deps.contacts
        present = bool(client is not None and getattr(
            client, "is_present", False))
        if empty and not present:
            reason = ("Contacts are unavailable: the contacts app is not "
                      "installed or not running.")
        else:
            # An empty address book shows nothing; only a cause is news.
            reason = ""
        self._snapshot = PeopleSnapshot(
            sections=sections, contacts_present=present,
            empty_reason=reason)
        return self._snapshot

    def find(self, uid: str) -> Person | None:
        for _title, people in self._snapshot.sections:
            for person in people:
                if person.uid == uid:
                    return person
        return None

    def _client(self):
        return self._deps.contacts

    def _region(self) -> str:
        try:
            from xx_dialer import config as app_config

            return policy.region_of(app_config.load(self._deps.home))
        except Exception:
            return "US"

    def set_star(self, person: Person, starred: bool) -> None:
        client = self._client()
        try:
            if client is not None:
                client.set_star(person.uid, starred)
            else:
                with Store(self._deps.db_file) as store:
                    if starred:
                        store.add_star(person.uid)
                    else:
                        store.remove_star(person.uid)
        except Exception:
            self._notice("Could not update the star.")
            return
        self.load()

    def set_business(self, person: Person, business: bool) -> None:
        client = self._client()
        try:
            if client is not None:
                client.set_business(person.uid, business)
            else:
                with Store(self._deps.db_file) as store:
                    if business:
                        store.upsert_tier(person.uid, BIZ_TIER)
                    else:
                        store.remove_tier(person.uid, BIZ_TIER)
        except Exception:
            self._notice("Could not update Business.")
            return
        self.load()

    def set_blocked(self, person: Person, blocked: bool) -> None:
        client = self._client()
        try:
            if client is not None:
                client.set_blocked(person.uid, blocked)
            else:
                with Store(self._deps.db_file) as store:
                    if blocked:
                        store.upsert_tier(person.uid, BLOCKED_GROUP)
                        for number in person.numbers:
                            store.add_blocked(number)
                    else:
                        store.remove_tier(person.uid, BLOCKED_GROUP)
                        for number in person.numbers:
                            store.remove_blocked(number)
        except Exception:
            self._notice("Could not update the block.")
            return
        # The row moving in or out of Blocked is the answer: no toast.
        self.load()

    def block_number(self, number: str) -> None:
        """Block a number with no contact behind it: local block table."""
        normalized = policy.canonical_number(number, self._region()) \
            or number.strip()
        if not normalized:
            return
        client = self._client()
        uid = None
        try:
            with Store(self._deps.db_file) as store:
                row = store.find_mirror(normalized)
                uid = row.lookup_key if row is not None else None
        except Exception:
            uid = None
        if uid is not None and client is not None:
            try:
                client.set_blocked(uid, True)
                self.load()
                return
            except Exception:
                pass
        try:
            with Store(self._deps.db_file) as store:
                store.add_blocked(normalized)
        except Exception:
            pass
        self.load()

    def call(self, person: Person, number: str | None = None) -> None:
        target = number or person.primary
        if not target:
            self._notice("This contact has no number to call.")
            return
        voice = self._deps.voice
        if voice is None:
            self._notice("No modem is available to place the call.")
            return

        def _done(ok: bool, detail) -> None:
            if not ok:
                self._notice("Could not place the call: %s"
                             % (detail or "modem error"))

        try:
            voice.place(target, _done)
        except Exception as err:
            self._notice("Could not place the call: %s" % (err,))

    def message(self, person: Person, number: str | None = None) -> None:
        target = number or person.primary
        if not target:
            self._notice("This contact has no number to message.")
            return
        launcher = self._deps.launch_sms
        try:
            if launcher is not None:
                launcher("sms:%s" % target)
            else:
                Gio.AppInfo.launch_default_for_uri("sms:%s" % target,
                                                   None)
        except Exception:
            self._notice("No messaging app is available.")


class PeopleView:
    """The People page widget."""

    def __init__(self, controller: PeopleController) -> None:
        self.controller = controller
        self._root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                             spacing=0)
        self._build()
        self.refresh()

    @property
    def widget(self) -> Gtk.Widget:
        return self._root

    def _build(self) -> None:
        self._header = widgets.page_header("People")
        self._caption = widgets.caption("")
        self._header.append(self._caption)
        self._root.append(self._header)
        self._empty = widgets.label("", "xx-empty", xalign=0.5)
        self._empty.set_halign(Gtk.Align.CENTER)
        self._empty.set_valign(Gtk.Align.CENTER)
        self._empty.set_vexpand(True)
        self._root.append(self._empty)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_setup)
        factory.connect("bind", self._on_bind)
        self._store = Gio.ListStore(item_type=PeopleItem)
        selection = Gtk.NoSelection(model=self._store)
        self._list = Gtk.ListView(model=selection, factory=factory)
        self._list.set_single_click_activate(True)
        self._list.connect("activate", self._on_activate)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER,
                          Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(self._list)
        self._root.append(scroll)

    def _region(self) -> str:
        try:
            return self.controller._region()
        except Exception:
            return "US"

    def refresh(self) -> None:
        snapshot = self.controller.load()
        while self._store.get_n_items():
            self._store.remove(0)
        total = 0
        region = self._region()
        for title, people in snapshot.sections:
            if not people:
                continue
            header = PeopleItem()
            header.kind = "header"
            header.title = "%s (%d)" % (title, len(people))
            self._store.append(header)
            for person in people:
                item = PeopleItem()
                item.kind = "item"
                item.uid = person.uid
                item.title = person.display_name or xx_phone.display(
                    person.primary, xx_phone.display_country(region))
                item.number = xx_phone.display(
                    person.primary, xx_phone.display_country(region))
                item.meta = person_marks(person, title)
                self._store.append(item)
                total += 1
        self._caption.set_text("%d CONTACT%s"
                               % (total, "" if total == 1 else "S"))
        self._empty.set_visible(self._store.get_n_items() == 0
                                and bool(snapshot.empty_reason))
        if self._empty.get_visible():
            widgets.fit_text(self._empty, snapshot.empty_reason, 32)

    # ---- factory ----------------------------------------------------------

    def _on_setup(self, _factory, list_item) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                       spacing=0)
        root.add_css_class("xx-row")
        section = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                          spacing=0)
        section.set_valign(Gtk.Align.CENTER)
        section_title = Gtk.Label(label="", xalign=0.0)
        section_title.add_css_class("xx-caption")
        section_title.add_css_class("xx-section-label")
        section_title.set_hexpand(True)
        section_title.set_ellipsize(Pango.EllipsizeMode.END)
        section.append(section_title)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                      spacing=12)
        row.set_valign(Gtk.Align.CENTER)
        disc = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                       spacing=0)
        disc.set_valign(Gtk.Align.CENTER)
        name = Gtk.Label(label="", xalign=0.0, hexpand=True)
        name.add_css_class("xx-title")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_hexpand(True)
        text.set_valign(Gtk.Align.CENTER)
        sub = Gtk.Label(label="", xalign=0.0)
        sub.add_css_class("xx-secondary")
        sub.set_ellipsize(Pango.EllipsizeMode.END)
        text.append(name)
        text.append(sub)
        meta = Gtk.Label(label="", xalign=1.0)
        meta.add_css_class("xx-time")
        meta.set_ellipsize(Pango.EllipsizeMode.END)
        row.append(disc)
        row.append(text)
        row.append(meta)
        gutter = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                         spacing=0)
        gutter.set_margin_start(20)
        gutter.set_margin_end(20)
        gutter.append(section)
        gutter.append(row)
        root.append(gutter)
        list_item.set_child(root)
        list_item.xx_parts = (root, section, section_title,
                              row, disc, name, sub, meta)

    def _on_bind(self, _factory, list_item) -> None:
        item = list_item.get_item()
        (root, section, section_title,
         row, disc, name, sub, meta) = list_item.xx_parts
        total = self._store.get_n_items()
        try:
            last = list_item.get_position() == total - 1
        except Exception:
            last = False
        if item.kind == "header":
            root.remove_css_class("xx-row")
            root.remove_css_class("last")
            section.set_visible(True)
            row.set_visible(False)
            section_title.set_text(item.title.upper())
            list_item.get_child().person_uid = None
            return
        root.add_css_class("xx-row")
        if last:
            root.add_css_class("last")
        else:
            root.remove_css_class("last")
        section.set_visible(False)
        row.set_visible(True)
        while True:
            child = disc.get_first_child()
            if child is None:
                break
            disc.remove(child)
        disc.append(widgets.avatar(t9.initials(item.title), 40))
        name.set_text(item.title)
        sub.set_text(item.number)
        widgets.fit_text(meta, item.meta, 12)
        list_item.get_child().person_uid = item.uid

    def _on_activate(self, _view, position: int) -> None:
        item = self._store.get_item(position)
        if item is None or item.kind != "item":
            return
        person = self.controller.find(item.uid)
        if person is None:
            return
        anchor = self._list
        self._open_sheet(person, anchor)

    def _open_sheet(self, person: Person, anchor: Gtk.Widget) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                       spacing=12)
        head.set_valign(Gtk.Align.CENTER)
        region = self._region()
        head.append(widgets.avatar(
            t9.initials(person.display_name or person.primary), 64))
        title = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title.set_hexpand(True)
        title.set_valign(Gtk.Align.CENTER)
        name = Gtk.Label(
            label=person.display_name or xx_phone.display(
                person.primary, xx_phone.display_country(region)),
            xalign=0.0)
        name.add_css_class("xx-title")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        numbers = Gtk.Label(
            label="\n".join(xx_phone.display(
                number, xx_phone.display_country(region))
                for number in person.numbers)
            or "No numbers",
            xalign=0.0)
        numbers.add_css_class("xx-secondary")
        numbers.set_ellipsize(Pango.EllipsizeMode.END)
        title.append(name)
        title.append(numbers)
        head.append(title)
        box.append(head)
        star = widgets.make_switch(person.starred)
        business = widgets.make_switch(person.business)
        blocked = widgets.make_switch(person.blocked)
        for switch, label in ((star, "Starred"), (business, "Business"),
                              (blocked, "Blocked")):
            box.append(widgets.setting_row(label, trailing=switch))

        star.connect("notify::active", self._on_sheet_star, person)
        business.connect("notify::active", self._on_sheet_business,
                         person)
        blocked.connect("notify::active", self._on_sheet_blocked,
                        person)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                          spacing=8)
        call = _outline("Call")
        call.set_hexpand(True)
        call.connect("clicked",
                     lambda _btn: self.controller.call(person))
        message = _outline("Message")
        message.set_hexpand(True)
        message.connect("clicked",
                        lambda _btn: self.controller.message(person))
        actions.append(call)
        actions.append(message)
        box.append(actions)
        popover = Gtk.Popover()
        popover.set_parent(anchor)
        point = Gdk.Rectangle()
        point.x, point.y, point.width, point.height = 0, 0, 1, 1
        popover.set_pointing_to(point)
        popover.set_child(box)
        popover.popup()
        return popover

    def _on_sheet_star(self, switch: Gtk.Switch, _spec,
                       person: Person) -> None:
        self.controller.set_star(person, switch.get_active())
        self.refresh()

    def _on_sheet_business(self, switch: Gtk.Switch, _spec,
                           person: Person) -> None:
        self.controller.set_business(person, switch.get_active())
        self.refresh()

    def _on_sheet_blocked(self, switch: Gtk.Switch, _spec,
                          person: Person) -> None:
        self.controller.set_blocked(person, switch.get_active())
        self.refresh()


class PeopleItem(GObject.Object):
    __gtype_name__ = "XxPeopleItem"

    kind = GObject.Property(type=str, default="item")
    uid = GObject.Property(type=str, default="")
    title = GObject.Property(type=str, default="")
    number = GObject.Property(type=str, default="")
    meta = GObject.Property(type=str, default="")


def build_people(deps: PeopleDeps) -> PeopleView:
    return PeopleView(PeopleController(deps))
