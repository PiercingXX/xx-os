"""Accounts list page: live codes with a countdown ring, search, group
chips, tap-to-copy, long-press for detail. Linux mapping of Android
`ui/AccountListScreen.kt`.

Rows are built once per change in the account set and then updated in
place every second (code, ring, counter), so a tap or long-press never
lands on a row that was swapped out under the finger."""

from __future__ import annotations

import math
import time

COPIED_MS = 1500


class ListController:
    def __init__(self, window) -> None:  # type: ignore[no-untyped-def]
        self.window = window
        self.query = ""
        self.group = ""
        self.searching = False
        self._cards = []
        self._suppress_click = False

    def on_show(self) -> None:
        self.refresh()

    def tick(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        backend = self.window.backend
        if backend is None or backend.cipher is None:
            return
        from xx_auth import accounts as accounts_mod

        now = int(time.time() * 1000)
        cards = accounts_mod.account_cards(backend.store, now, backend.cipher)
        total = len(cards)
        cards = accounts_mod.filter_cards(cards, self.query if self.searching else "")
        if self.group:
            cards = [c for c in cards if c.group == self.group]
        self._cards = cards
        self._render(cards, total)

    def _render(self, cards, total: int) -> None:  # type: ignore[no-untyped-def]
        raise NotImplementedError


class Ring:
    """The Android countdown ring: a 40 px arc that drains with the
    period, the seconds left in its middle, danger-colored for the last
    five seconds. Drawn, not laid out, so a tick never relayouts."""

    SIZE = 40

    def __init__(self, Gtk) -> None:  # type: ignore[no-untyped-def]
        self.fraction = 0.0
        self.seconds = 0
        area = Gtk.DrawingArea()
        area.add_css_class("xx-auth-ring")
        area.set_content_width(self.SIZE)
        area.set_content_height(self.SIZE)
        area.set_valign(Gtk.Align.CENTER)
        area.set_draw_func(self._draw)
        self.widget = area

    def update(self, fraction: float, seconds: int, warn: bool) -> None:
        fraction = max(0.0, min(1.0, fraction))
        if warn:
            self.widget.add_css_class("warn")
        else:
            self.widget.remove_css_class("warn")
        if (fraction, seconds) != (self.fraction, self.seconds):
            self.fraction, self.seconds = fraction, seconds
            self.widget.queue_draw()

    def _draw(self, area, cr, width: int, height: int) -> None:  # type: ignore[no-untyped-def]
        import gi

        gi.require_version("PangoCairo", "1.0")
        from gi.repository import PangoCairo

        color = area.get_color()
        stroke = 3.0
        cx, cy = width / 2, height / 2
        radius = min(width, height) / 2 - stroke / 2 - 1
        cr.set_line_width(stroke)
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha * 0.125)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()
        if self.fraction > 0:
            cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
            start = -math.pi / 2
            cr.arc(cx, cy, radius, start, start + 2 * math.pi * self.fraction)
            cr.stroke()
        layout = area.create_pango_layout(str(self.seconds))
        text_w, text_h = layout.get_pixel_size()
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        cr.move_to(cx - text_w / 2, cy - text_h / 2)
        PangoCairo.show_layout(cr, layout)


def ring_state(card) -> tuple[float, int, bool]:  # type: ignore[no-untyped-def]
    """(fraction, seconds, warn) for a TOTP card's ring."""
    from xx_auth import accounts as accounts_mod

    remaining = card.remaining_millis or 0
    seconds = -(-remaining // 1000)
    warn = remaining <= accounts_mod.WARN_MILLIS and not card.unreadable
    return (card.progress_fraction or 0.0), int(seconds), warn


def code_label(window, hero: bool = False):  # type: ignore[no-untyped-def]
    Gtk = window._Gtk
    label = Gtk.Label(label="")
    label.add_css_class("xx-auth-code")
    label.add_css_class("xx-display-face")
    if hero:
        label.add_css_class("hero")
    label.set_xalign(0.0)
    label.set_max_width_chars(10)
    label.set_ellipsize(window._Pango.EllipsizeMode.END)
    return label


class _Row:
    """One account row and the widgets a tick updates in place."""

    def __init__(self, window, controller, card, last: bool) -> None:  # type: ignore[no-untyped-def]
        import xx_ui.widgets as widgets

        Gtk = window._Gtk
        self.window = window
        self.card = card
        self._restore_id = 0
        row = Gtk.Button()
        row.add_css_class("xx-row")
        row.add_css_class("xx-auth-row")
        if last:
            row.add_css_class("last")
        box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        column = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
        column.set_hexpand(True)
        column.set_valign(Gtk.Align.CENTER)
        column.append(widgets.label(card.issuer or card.label, "xx-title"))
        self.sub_text = card.label if card.issuer else ""
        self.sub = widgets.label(self.sub_text, "xx-secondary")
        self.sub.set_visible(bool(self.sub_text))
        column.append(self.sub)
        self.code = code_label(window)
        self.code.set_margin_top(6)
        if card.unreadable:
            self.code.add_css_class("unreadable")
        column.append(self.code)
        if card.unreadable:
            note = widgets.wrap_label("Can't decrypt on this phone. Re-add it.", "xx-secondary")
            note.add_css_class("xx-danger-text")
            column.append(note)
        box.append(column)
        self.ring = None
        self.counter = None
        if card.is_hotp:
            hotp = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
            hotp.set_valign(Gtk.Align.CENTER)
            self.counter = Gtk.Label(label="")
            self.counter.add_css_class("xx-auth-counter")
            hotp.append(self.counter)
            hotp.append(widgets.icon_button("view-refresh-symbolic", lambda: self._next()))
            box.append(hotp)
        else:
            self.ring = Ring(Gtk)
            box.append(self.ring.widget)
        row.set_child(box)
        self.widget = row
        self.update(card)

        def on_click(_button) -> None:
            if controller._suppress_click:
                controller._suppress_click = False
                return
            if self.card.unreadable:
                window.show_detail(self.card.id)
                return
            window.copy_code(self.card.code)
            self.flash_copied()

        row.connect("clicked", on_click)
        press = Gtk.GestureLongPress.new()
        press.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        def on_press(_gesture, _x, _y) -> None:
            controller._suppress_click = True
            window.show_detail(self.card.id)

        press.connect("pressed", on_press)
        row.add_controller(press)
        secondary = Gtk.GestureClick.new()
        secondary.set_button(3)
        secondary.connect("pressed", lambda *_a: window.show_detail(self.card.id))
        row.add_controller(secondary)

    def update(self, card) -> None:  # type: ignore[no-untyped-def]
        from xx_auth.main import group_code

        self.card = card
        self.code.set_text(group_code(card.code))
        if self.ring is not None:
            self.ring.update(*ring_state(card))
        if self.counter is not None:
            self.counter.set_text(f"#{card.counter}")

    def flash_copied(self) -> None:
        """The in-row copy receipt: the account line reads Copied for a
        moment. No toast, no HUD."""
        GLib = self.window._GLib
        if self._restore_id:
            GLib.source_remove(self._restore_id)
        self.sub.set_text("Copied")
        self.sub.set_visible(True)

        def restore() -> bool:
            self._restore_id = 0
            self.sub.set_text(self.sub_text)
            self.sub.set_visible(bool(self.sub_text))
            return False

        self._restore_id = GLib.timeout_add(COPIED_MS, restore)

    def _next(self) -> None:
        if _advance(self.window, self.card.id):
            self.flash_copied()


def _advance(window, account_id: str) -> bool:  # type: ignore[no-untyped-def]
    backend = window.backend
    if backend is None or backend.cipher is None:
        return False
    updated = backend.store.advance_counter(account_id)
    if updated is None:
        return False
    backend.store.save()
    window.copy_code(_live_code(window, updated.id))
    window.refresh()
    return True


def _live_code(window, account_id: str) -> str:  # type: ignore[no-untyped-def]
    from xx_auth import accounts as accounts_mod

    backend = window.backend
    card = accounts_mod.account_card(backend.store, account_id, int(time.time() * 1000), backend.cipher)
    return card.code if card else ""


def _header_button(header, icon_name: str):  # type: ignore[no-untyped-def]
    """The page_header icon button showing ``icon_name``."""
    child = header.get_first_child().get_first_child()
    while child is not None:
        image = child.get_child() if hasattr(child, "get_child") else None
        if image is not None and getattr(image, "get_icon_name", lambda: None)() == icon_name:
            return child
        child = child.get_next_sibling()
    return None


def _structure(cards) -> tuple:  # type: ignore[no-untyped-def]
    return tuple((c.id, c.issuer, c.label, c.is_hotp, c.unreadable) for c in cards)


def build(window):  # type: ignore[no-untyped-def]
    import xx_ui.widgets as widgets

    Gtk = window._Gtk
    controller = ListController(window)
    state = {"structure": None, "chips": None, "rows": {}}

    search = widgets.deferred_entry("Search issuer or account")
    search.set_visible(False)
    search.set_margin_bottom(8)
    search.connect("changed", lambda _e: (setattr(controller, "query", search.get_text()), controller.refresh()))

    chips_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    chips_box.set_margin_bottom(4)
    rows = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)

    def toggle_search() -> None:
        controller.searching = not controller.searching
        search.set_visible(controller.searching)
        if controller.searching:
            search.set_can_focus(True)
            search.grab_focus()
        else:
            search.get_buffer().set_text("", 0)
            controller.query = ""
        controller.refresh()

    def show_menu() -> None:
        popover = Gtk.Popover()
        box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        for title, target in (("Import / export", "io"),
                              ("App lock", "locksettings"),
                              ("About", "about")):
            def go(_t=target) -> None:
                popover.popdown()
                window.show(_t)

            box.append(widgets.menu_row(title, go))
        popover.set_child(box)
        popover.set_parent(more_button or header)
        popover.popup()

    header = widgets.page_header(
        "xx-auth", icon_name="system-search-symbolic", icon_cb=toggle_search,
        overflow_cb=show_menu)
    search_button = _header_button(header, "system-search-symbolic")
    more_button = _header_button(header, "view-more-symbolic")

    def render(cards, total: int) -> None:  # type: ignore[no-untyped-def]
        backend = window.backend
        groups = tuple(sorted({a.group for a in backend.store.get_all() if a.group})) if backend else ()
        chips_key = (groups, controller.group)
        if chips_key != state["chips"]:
            state["chips"] = chips_key
            child = chips_box.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                chips_box.remove(child)
                child = nxt
            if groups:
                items = [("All", controller.group == "", lambda: _pick_group(window, ""))]
                for name in groups:
                    items.append((name, controller.group == name,
                                  lambda n=name: _pick_group(window, n)))
                chips_box.append(widgets.chip_bar(items))
            chips_box.set_visible(bool(groups))
        # An empty vault shows nothing but the add button.
        if search_button is not None:
            search_button.set_visible(total > 0 or controller.searching)
        structure = _structure(cards)
        if structure == state["structure"]:
            for card in cards:
                row = state["rows"].get(card.id)
                if row is not None:
                    row.update(card)
            return
        state["structure"] = structure
        child = rows.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            rows.remove(child)
            child = nxt
        state["rows"] = {}
        for index, card in enumerate(cards):
            row = _Row(window, controller, card, index == len(cards) - 1)
            state["rows"][card.id] = row
            rows.append(row.widget)

    controller._render = render

    body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    body.append(search)
    body.append(chips_box)
    body.append(rows)
    clearance = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    clearance.set_size_request(-1, 96)
    body.append(clearance)

    page = widgets.page(header, body, None, widgets.fab(lambda: window.show("add")))
    return page, controller


def _pick_group(window, name: str) -> None:  # type: ignore[no-untyped-def]
    controller = window._pages.get("list")
    if controller is not None:
        controller.group = name
        controller.refresh()
