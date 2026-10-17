"""Accounts list page: live codes, search, group chips, tap-to-copy,
long-press for detail. Linux mapping of Android `ui/AccountListScreen.kt`."""

from __future__ import annotations

import time


class ListController:
    def __init__(self, window) -> None:  # type: ignore[no-untyped-def]
        self.window = window
        self.query = ""
        self.group = ""
        self._cards = []
        self._suppress_click = False

    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        backend = self.window.backend
        if backend is None or backend.cipher is None:
            return
        from xx_auth import accounts as accounts_mod

        now = int(time.time() * 1000)
        cards = accounts_mod.account_cards(backend.store, now, backend.cipher)
        cards = accounts_mod.filter_cards(cards, self.query)
        if self.group:
            cards = [c for c in cards if c.group == self.group]
        self._cards = cards
        self._render(cards)

    def _render(self, cards) -> None:  # type: ignore[no-untyped-def]
        raise NotImplementedError


def _code_label(window, code: str):  # type: ignore[no-untyped-def]
    Gtk = window._Gtk
    from xx_auth.main import group_code

    label = Gtk.Label(label=group_code(code))
    label.add_css_class("xx-auth-code")
    label.add_css_class("xx-display-face")
    label.set_xalign(0.0)
    label.set_max_width_chars(10)
    label.set_ellipsize(window._Pango.EllipsizeMode.END)
    return label


def _widen(button, chars: int) -> None:
    child = button.get_first_child()
    if child is not None and hasattr(child, "set_max_width_chars"):
        child.set_max_width_chars(chars)


def _row(window, card, last: bool):  # type: ignore[no-untyped-def]
    from xx_auth import accounts as accounts_mod

    Gtk = window._Gtk
    import xx_ui.widgets as widgets

    row = Gtk.Button()
    row.add_css_class("xx-row")
    row.add_css_class("xx-auth-row")
    if last:
        row.add_css_class("last")
    outer = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
    outer.set_hexpand(True)
    head = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    issuer = widgets.label(card.issuer or card.label, "xx-caption")
    issuer.set_hexpand(True)
    head.append(issuer)
    if card.is_hotp:
        meta = widgets.label(f"counter {card.counter}", "xx-time", xalign=1.0)
        head.append(meta)
    else:
        remaining = card.remaining_millis or 0
        seconds = -(-remaining // 1000)
        meta = widgets.label(f"{seconds}s", "xx-time", xalign=1.0)
        head.append(meta)
    outer.append(head)
    if card.issuer:
        name = widgets.label(card.label, "xx-title")
        outer.append(name)
    outer.append(_code_label(window, card.code))
    if card.is_hotp:
        foot = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
        foot.set_hexpand(True)
        nxt = widgets.text_button("Next code", lambda: _advance(window, card.id))
        nxt.set_halign(Gtk.Align.END)
        nxt.set_hexpand(True)
        _widen(nxt, 10)
        foot.append(nxt)
        outer.append(foot)
    else:
        bar = Gtk.ProgressBar()
        bar.set_hexpand(True)
        bar.set_fraction(max(0.0, min(1.0, card.progress_fraction or 0.0)))
        if (card.remaining_millis or 0) <= accounts_mod.WARN_MILLIS and not card.unreadable:
            bar.add_css_class("error")
        outer.append(bar)
    if card.unreadable:
        note = widgets.label("Can't decrypt on this phone. Re-add it.", "xx-secondary")
        outer.append(note)
    row.set_child(outer)

    controller = window._pages.get("list")

    def on_click(_button) -> None:
        if controller is not None and getattr(controller, "_suppress_click", False):
            controller._suppress_click = False
            return
        if not card.unreadable:
            window.copy_code(card.code)
            window.refresh_status(f"Copied {card.issuer or card.label}.")

    row.connect("clicked", on_click)
    press = Gtk.GestureLongPress.new()
    press.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

    def on_press(_gesture, _x, _y) -> None:
        if controller is not None:
            controller._suppress_click = True
        window.show_detail(card.id)

    press.connect("pressed", on_press)
    row.add_controller(press)
    return row


def _advance(window, account_id: str) -> None:  # type: ignore[no-untyped-def]
    backend = window.backend
    if backend is None or backend.cipher is None:
        return
    updated = backend.store.advance_counter(account_id)
    if updated is not None:
        backend.store.save()
        window.copy_code(_live_code(window, updated.id))
        window.refresh_status("Next code copied.")
    window.refresh()


def _live_code(window, account_id: str) -> str:  # type: ignore[no-untyped-def]
    from xx_auth import accounts as accounts_mod

    backend = window.backend
    card = accounts_mod.account_card(backend.store, account_id, int(time.time() * 1000), backend.cipher)
    return card.code if card else ""


def build(window):  # type: ignore[no-untyped-def]
    import xx_ui.widgets as widgets

    Gtk = window._Gtk
    controller = ListController(window)

    search = widgets.deferred_entry("Search")
    search.connect("changed", lambda _e: (setattr(controller, "query", search.get_text()), controller.refresh()))

    chips_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    rows = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    empty = widgets.empty_state("No codes yet. Add your first account.", "Add account", lambda: window.show("add"))

    def render(cards) -> None:  # type: ignore[no-untyped-def]
        child = rows.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            rows.remove(child)
            child = nxt
        backend = window.backend
        groups = sorted({a.group for a in backend.store.get_all() if a.group}) if backend else []
        chip_child = chips_box.get_first_child()
        while chip_child is not None:
            nxt = chip_child.get_next_sibling()
            chips_box.remove(chip_child)
            chip_child = nxt
        if groups:
            items = [("All", controller.group == "", lambda: _pick_group(window, ""))]
            for name in groups:
                items.append((name, controller.group == name,
                              lambda n=name: _pick_group(window, n)))
            chips_box.append(widgets.chip_bar(items))
            chips_box.set_visible(True)
        else:
            chips_box.set_visible(False)
        if not cards and not controller.query and not controller.group:
            empty.set_visible(True)
        else:
            empty.set_visible(False)
            if not cards:
                note = widgets.label("No matches.", "xx-secondary")
                rows.append(note)
            for index, card in enumerate(cards):
                rows.append(_row(window, card, index == len(cards) - 1))
        count = len(cards)
        window.set_list_caption(f"{count} account{'s' if count != 1 else ''}")

    controller._render = render

    header = widgets.page_header("xx-auth", actions=[("Add", lambda: window.show("add"))])
    caption = widgets.caption("")
    header.append(caption)
    window.set_list_caption = caption.set_text
    window.refresh_status = lambda _m: None

    body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
    body.append(search)
    body.append(chips_box)
    body.append(rows)
    body.append(empty)
    clearance = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    clearance.set_size_request(-1, 76)
    body.append(clearance)

    bottom = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    bottom.set_halign(Gtk.Align.CENTER)
    bottom.set_spacing(4)
    vault_btn = widgets.text_button("Import/export", lambda: window.show("io"))
    _widen(vault_btn, 13)
    bottom.append(vault_btn)
    bottom.append(widgets.text_button("Lock", lambda: window.show("locksettings")))
    bottom.append(widgets.text_button("About", lambda: window.show("about")))
    status = widgets.caption("")
    bottom.append(status)

    def show_status(message: str) -> None:
        status.set_text(message)

    window.refresh_status = show_status
    page = widgets.page(header, body, bottom, widgets.fab(lambda: window.show("add")))
    return page, controller


def _pick_group(window, name: str) -> None:  # type: ignore[no-untyped-def]
    controller = window._pages.get("list")
    if controller is not None:
        controller.group = name
        controller.refresh()
