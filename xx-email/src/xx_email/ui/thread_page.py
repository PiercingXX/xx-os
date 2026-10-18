"""Thread page: messages in date order with plain-text bodies.

Ports ThreadScreen/ThreadViewModel over xx_email.bodies: marks the
thread read on open, renders decoded text/plain (else converted HTML)
in wrapping 15 px labels, shows attachment chips, and exposes reply /
reply-all / forward plus a ⋯ menu for star/archive/delete/unread/
snooze. Failures banner in place — the user stays put.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, Gtk

from xx_email import bodies as bodies_mod
from xx_email import contacts as contacts_mod
from xx_email import mailbox as mailbox_mod
from xx_email.ui import common as common_mod
from xx_ui import widgets


class ThreadPage(Gtk.Box):
    """One conversation for one account."""

    def __init__(self, account: str, thread_id: str, hooks, toast) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.account = account
        self.thread_id = thread_id
        self._hooks = hooks
        self._toast = toast
        self._starred = False

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.add_css_class("xx-page-body")
        bar.add_css_class("xx-compact-header")
        back = widgets.icon_button("go-previous-symbolic",
                                   lambda: hooks.go_back())
        back.set_valign(Gtk.Align.CENTER)
        bar.append(back)
        middle = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        middle.set_hexpand(True)
        middle.set_valign(Gtk.Align.CENTER)
        self._subject = widgets.caption("")
        middle.append(self._subject)
        self._thread_sub = widgets.label("", "xx-secondary")
        middle.append(self._thread_sub)
        bar.append(middle)
        self._star_button = widgets.icon_button(
            "starred-symbolic", lambda: self._toggle_star())
        self._star_button.set_valign(Gtk.Align.CENTER)
        bar.append(self._star_button)
        overflow = common_mod.overflow_menu([
            ("Archive", lambda: self._on_action("ARCHIVE")),
            ("Delete", lambda: self._on_action("DELETE")),
            ("Mark unread", lambda: self._on_action("MARK_UNREAD")),
            ("Snooze", lambda: self._on_action("SNOOZE")),
        ])
        overflow.set_valign(Gtk.Align.CENTER)
        bar.append(overflow)
        self.append(bar)

        self._store = Gio.ListStore.new(common_mod.MessageItem)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_setup)
        factory.connect("bind", self._on_bind)
        view = Gtk.ListView(model=Gtk.NoSelection.new(self._store),
                            factory=factory)
        view.set_single_click_activate(True)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER,
                            Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        scrolled.set_child(view)
        self.append(scrolled)

        # One line of three where they fit; at 320 px and large text
        # Forward drops to a second line instead of widening the window.
        reply_row = Gtk.FlowBox()
        reply_row.set_selection_mode(Gtk.SelectionMode.NONE)
        reply_row.set_max_children_per_line(3)
        reply_row.set_min_children_per_line(1)
        reply_row.set_column_spacing(8)
        reply_row.set_row_spacing(8)
        reply_row.add_css_class("xx-page-body")
        reply_row.set_margin_top(12)
        reply_row.set_margin_bottom(20)
        for label, mode in (("Reply", "reply"),
                            ("Reply all", "reply_all"),
                            ("Forward", "forward")):
            button = widgets.outline_button(label)
            button.set_hexpand(True)
            button.connect("clicked",
                           lambda _b, m=mode: self._on_reply(m))
            reply_row.append(button)
            child = button.get_parent()
            if child is not None:
                child.set_focusable(False)
        self.append(reply_row)

        self._messages: list = []
        self._expanded: set[str] = set()
        self._images_allowed: set[str] = set()
        self._full: dict = {}
        self._hooks.load_thread(self._on_loaded)

    # ------------------------------------------------------------------ rows

    def _on_setup(self, _factory, list_item) -> None:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.set_margin_start(20)
        card.set_margin_end(20)
        card.set_margin_top(12)
        card.set_margin_bottom(12)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        head.set_valign(Gtk.Align.CENTER)
        avatar = widgets.avatar("", 40, dashed=True)
        head.append(avatar)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        names.set_hexpand(True)
        names.set_valign(Gtk.Align.CENTER)
        sender = widgets.label("", "xx-title")
        names.append(sender)
        date = widgets.label("", "xx-time")
        names.append(date)
        head.append(names)
        tap = Gtk.GestureClick()
        tap.connect("pressed", lambda _g, _n, _x, _y:
                    self._toggle_expand(list_item))
        head.add_controller(tap)
        to_line = widgets.label("", "xx-secondary")
        body = common_mod.wrapped(Gtk.Label())
        body.add_css_class("xx-body")
        body.set_selectable(True)
        chips = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.append(head)
        card.append(to_line)
        card.append(body)
        card.append(chips)
        list_item.set_child(card)

    def _on_bind(self, _factory, list_item) -> None:
        item = list_item.get_item()
        card = list_item.get_child()
        head = card.get_first_child()
        avatar = head.get_first_child()
        names = avatar.get_next_sibling()
        sender = names.get_first_child()
        date = sender.get_next_sibling()
        to_line = head.get_next_sibling()
        body = to_line.get_next_sibling()
        chips = body.get_next_sibling()
        sender.set_label(item.sender)
        date.set_label(item.date)
        to_line.set_label(item.to_line)
        body.set_label(item.body)
        initials, dashed = common_mod.initials_for(item.sender)
        avatar.set_label(initials)
        if dashed:
            avatar.add_css_class("dashed")
        else:
            avatar.remove_css_class("dashed")
        while True:
            chip = chips.get_first_child()
            if chip is None:
                break
            chips.remove(chip)
        if item.message_id in self._expanded and \
                bodies_mod.has_remote_images(
                    self._html_for(item.message_id)) and \
                item.message_id not in self._images_allowed:
            load = widgets.outline_button("Load images")
            load.connect("clicked", lambda _b, mid=item.message_id:
                         self._allow_images(mid))
            chips.append(load)
        for attachment in self._attachments_for(item.message_id):
            chip = widgets.outline_button(
                f"{attachment.filename} ({attachment.size // 1024:d} KB)")
            chip.connect("clicked", lambda _b, a=attachment:
                         self._hooks.open_attachment(a))
            chips.append(chip)

    def _toggle_expand(self, list_item) -> None:
        item = list_item.get_item()
        if item is None:
            return
        self._expanded = mailbox_mod.toggle_expanded(
            self._expanded, item.message_id)
        self._rerender(item.message_id)

    def _allow_images(self, message_id: str) -> None:
        self._images_allowed.add(message_id)
        self._rerender(message_id)

    def _rerender(self, message_id: str) -> None:
        for index, message in enumerate(self._messages):
            if message.id != message_id:
                continue
            item = self._store.get_item(index)
            if item is not None:
                self._render_item(item, message)
        count = self._store.get_n_items()
        if count:
            self._store.items_changed(0, count, count)

    # ------------------------------------------------------------------ data

    def _render_item(self, item, message) -> None:
        """Body text for one row: full when expanded, snippet when shut."""
        entry = self._full.get(message.id, {})
        allow = message.id in self._images_allowed
        if message.id in self._expanded:
            item.body = bodies_mod.render_body(
                entry.get("html"), entry.get("plain"),
                message.snippet, allow_remote_images=allow)
        else:
            item.body = message.snippet or "Tap the header to expand"

    def _html_for(self, message_id: str):
        return self._full.get(message_id, {}).get("html")

    def _on_loaded(self, result) -> None:
        if isinstance(result, Exception):
            self._toast("Couldn't open thread", None)
            return
        thread, messages, full = result
        self._subject.set_label(
            (thread.subject or "(no subject)").upper())
        count = len(list(messages))
        self._thread_sub.set_label(
            f"{count:d} MESSAGE" + ("" if count == 1 else "S"))
        self._starred = bool(thread.starred)
        self._paint_star()
        self._messages = list(messages)
        self._full = {entry["message_id"]: entry for entry in full}
        self._expanded = mailbox_mod.default_expanded_ids(
            self._messages)
        self._store.remove_all()
        for message in self._messages:
            item = common_mod.MessageItem()
            item.message_id = message.id
            item.sender = contacts_mod.resolve_sender_display(
                message.from_address, None)
            item.to_line = f"to {message.to_csv}" if message.to_csv else ""
            item.date = mailbox_mod.time_ago_now(message.date)
            item.unread = not message.read
            self._render_item(item, message)
            self._store.append(item)
        self._enrich_senders()
        self._hooks.mark_read()

    def _enrich_senders(self) -> None:
        """Replace raw senders with xx-contacts names when available.

        Async and off the lookup path: each distinct address resolves
        through the exact contacts email lookup → EDS → raw header,
        then its rows rebind.
        """
        seen: set[str] = set()
        for message in self._messages:
            address = (message.from_address or "").strip()
            if not address or address in seen:
                continue
            seen.add(address)

            def _done(name, at=address) -> None:
                if not name or not name.strip():
                    return
                display = contacts_mod.resolve_sender_display(at, name)
                changed = False
                for index in range(self._store.get_n_items()):
                    item = self._store.get_item(index)
                    if item.sender != display and \
                            self._messages[index].from_address == at:
                        item.sender = display
                        changed = True
                if changed:
                    count = self._store.get_n_items()
                    self._store.items_changed(0, count, count)

            contacts_mod.lookup_email_async(address, _done)

    def _attachments_for(self, message_id: str) -> list:
        entry = self._full.get(message_id, {})
        return list(entry.get("attachments", []))

    def _paint_star(self) -> None:
        self._star_button.set_child(widgets.icon_image(
            "starred-symbolic" if self._starred
            else "non-starred-symbolic"))

    def _toggle_star(self) -> None:
        self._hooks.perform("STAR", [self.thread_id], self._on_done,
                            starred=not self._starred)

    def _on_action(self, action: str) -> None:
        if action == "SNOOZE":
            self._toast("Snooze from the mailbox list", None)
            return
        self._hooks.perform(action, [self.thread_id], self._on_done)

    def _on_done(self, result) -> None:
        if isinstance(result, Exception):
            self._toast("Action failed — you stay put", None)
            return
        undo = result.get("undo")
        if undo is not None:
            self._hooks.thread_undone(undo)
            self._hooks.go_back()
        else:
            self._hooks.go_back()

    def _on_reply(self, mode: str) -> None:
        quote = self._messages[-1].id if self._messages else ""
        subject = self._subject.get_label()
        self._hooks.open_reply(mode, subject, quote)


def thread_dialog_page(account: str, thread_id: str, hooks,
                       toast) -> Adw.NavigationPage:
    page = ThreadPage(account, thread_id, hooks, toast)
    navigation = Adw.NavigationPage(title="Thread")
    navigation.set_child(page)
    return navigation
