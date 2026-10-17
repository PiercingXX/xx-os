"""Account detail page: live code, edit, reveal, move, delete. Linux
mapping of Android `ui/AccountDetailScreen.kt`."""

from __future__ import annotations

import time

COPIED_MS = 1500


def _widen(button, chars: int) -> None:
    child = button.get_first_child()
    if child is not None and hasattr(child, "set_max_width_chars"):
        child.set_max_width_chars(chars)


class DetailController:
    def __init__(self, window) -> None:  # type: ignore[no-untyped-def]
        self.window = window
        self.account_id: str | None = None

    def open(self, account_id: str) -> None:
        self.account_id = account_id
        self.reset()
        self.refresh()

    def on_show(self) -> None:
        self.refresh()

    def tick(self) -> None:
        self.refresh()

    def reset(self) -> None:
        pass

    def refresh(self) -> None:
        raise NotImplementedError


def meta_line(account) -> str:  # type: ignore[no-untyped-def]
    """`TOTP · SHA1 · 6 digits · 30 s`, as on Android."""
    # No-break spaces inside each part: a wrapped line breaks only
    # between parts, never "30 / s".
    nbsp = "\u00a0"
    parts = ["HOTP" if account.counter is not None else "TOTP", account.algorithm,
             f"{account.digits}{nbsp}digits"]
    if account.counter is not None:
        parts.append(f"counter{nbsp}{account.counter}")
    else:
        parts.append(f"{account.period}{nbsp}s")
    return " · ".join(parts)


def build(window):  # type: ignore[no-untyped-def]
    import xx_ui.widgets as widgets
    from xx_auth import accounts as accounts_mod
    from xx_auth.main import group_code
    from xx_auth.ui_list import Ring, code_label, ring_state

    Gtk = window._Gtk
    GLib = window._GLib
    controller = DetailController(window)

    header = widgets.compact_header(lambda: window.show("list"), "Account", "")
    body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)

    def section(title: str) -> None:
        label = widgets.section_label(title)
        label.set_margin_top(28)
        label.set_margin_bottom(8)
        body.append(label)

    def labeled(caption_text: str, widget) -> None:  # type: ignore[no-untyped-def]
        cap = widgets.caption(caption_text)
        cap.set_margin_bottom(6)
        body.append(cap)
        widget.set_margin_bottom(12)
        body.append(widget)

    # Identity and the live code.
    title = widgets.label("", "xx-title")
    title.set_margin_top(8)
    body.append(title)
    subtitle = widgets.label("", "xx-secondary")
    body.append(subtitle)
    code_row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
    code_row.set_margin_top(8)
    code = code_label(window, hero=True)
    code.set_hexpand(True)
    code_row.append(code)
    ring = Ring(Gtk)
    code_row.append(ring.widget)
    body.append(code_row)
    meta = widgets.wrap_label("", "xx-secondary")
    meta.set_margin_top(4)
    body.append(meta)

    buttons = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    buttons.set_homogeneous(True)
    buttons.set_margin_top(16)
    copy_btn = widgets.primary_button("Copy code", lambda: do_copy())
    next_btn = widgets.outline_button("Next code", lambda: do_next())
    for btn in (copy_btn, next_btn):
        btn.set_hexpand(True)
        _widen(btn, 14)
    buttons.append(copy_btn)
    buttons.append(next_btn)
    body.append(buttons)

    section("Identity")
    issuer = widgets.text_entry("e.g. GitHub")
    label = widgets.text_entry("e.g. alice@example.com")
    group = widgets.text_entry("Optional")
    labeled("Issuer", issuer)
    labeled("Account", label)
    labeled("Group", group)
    save_btn = widgets.primary_button("Save changes", lambda: do_save())
    _widen(save_btn, 14)
    # Shown only while an edit is pending: no disabled block on the page.
    save_btn.set_sensitive(False)
    save_btn.set_visible(False)
    body.append(save_btn)

    section("Secret")
    secret_text = Gtk.Label(label="")
    secret_text.add_css_class("xx-auth-secret")
    secret_text.add_css_class("xx-display-face")
    secret_text.set_xalign(0.0)
    secret_text.set_wrap(True)
    secret_text.set_wrap_mode(window._Pango.WrapMode.CHAR)
    secret_text.set_max_width_chars(24)
    secret_text.set_selectable(True)
    secret_text.set_visible(False)
    secret_text.set_margin_bottom(8)
    body.append(secret_text)
    secret_actions = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    secret_actions.set_homogeneous(True)
    secret_actions.set_margin_bottom(8)
    copy_secret = widgets.outline_button("Copy secret", lambda: do_copy_secret())
    copy_uri = widgets.outline_button("Copy link", lambda: do_copy_uri())
    for btn in (copy_secret, copy_uri):
        btn.set_hexpand(True)
        _widen(btn, 12)
        secret_actions.append(btn)
    secret_actions.set_visible(False)
    body.append(secret_actions)
    reveal_btn = widgets.outline_button("Reveal secret", lambda: do_reveal())
    _widen(reveal_btn, 14)
    body.append(reveal_btn)

    section("Position")
    position = widgets.label("", "xx-secondary")
    position.set_margin_bottom(10)
    body.append(position)
    move_row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    move_row.set_homogeneous(True)
    up_btn = widgets.outline_button("Move up", lambda: do_move(-1))
    down_btn = widgets.outline_button("Move down", lambda: do_move(1))
    for btn in (up_btn, down_btn):
        btn.set_hexpand(True)
        _widen(btn, 12)
    move_row.append(up_btn)
    move_row.append(down_btn)
    body.append(move_row)

    section("Remove")
    warn = widgets.wrap_label("Turn off two-factor on the service first.")
    warn.set_margin_bottom(10)
    body.append(warn)
    delete_btn = widgets.outline_button("Delete account", lambda: do_delete())
    delete_btn.add_css_class("xx-auth-danger")
    _widen(delete_btn, 16)
    body.append(delete_btn)

    status = widgets.wrap_label("")
    status.add_css_class("xx-danger-text")
    status.set_margin_top(12)
    status.set_visible(False)
    body.append(status)
    clearance = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    clearance.set_size_request(-1, 32)
    body.append(clearance)

    revealed = {"secret": ""}
    copied = {"id": 0}
    syncing = {"on": False}

    def say(message: str) -> None:
        """Errors only: a successful action shows itself."""
        status.set_text(message)
        status.set_visible(bool(message))

    def current():  # type: ignore[no-untyped-def]
        backend = window.backend
        if backend is None or backend.cipher is None or controller.account_id is None:
            return None, None
        return backend, backend.store.get_by_id(controller.account_id)

    def flash(button, text: str, rest: str) -> None:  # type: ignore[no-untyped-def]
        if copied["id"]:
            GLib.source_remove(copied["id"])
        button.set_label(text)

        def restore() -> bool:
            copied["id"] = 0
            button.set_label(rest)
            return False

        copied["id"] = GLib.timeout_add(COPIED_MS, restore)

    def set_dirty(flag: bool) -> None:
        save_btn.set_sensitive(flag)
        save_btn.set_visible(flag)

    def dirty(account) -> bool:  # type: ignore[no-untyped-def]
        return (issuer.get_text() != account.issuer or label.get_text() != account.label
                or group.get_text() != account.group)

    def render() -> None:
        backend, account = current()
        if backend is None or account is None:
            code.set_text("")
            return
        card = accounts_mod.account_card(
            backend.store, account.id, int(time.time() * 1000), backend.cipher)
        if card is None:
            return
        title.set_text(account.issuer or account.label)
        subtitle.set_text(account.label if account.issuer else "")
        subtitle.set_visible(bool(account.issuer))
        code.set_text(group_code(card.code))
        if card.unreadable:
            code.add_css_class("unreadable")
        else:
            code.remove_css_class("unreadable")
        ring.widget.set_visible(not card.is_hotp)
        if not card.is_hotp:
            ring.update(*ring_state(card))
        meta.set_text(meta_line(account))
        next_btn.set_visible(card.is_hotp)
        copy_btn.set_sensitive(not card.unreadable)
        # Fill the fields from the vault unless the operator is editing.
        if not save_btn.get_sensitive():
            syncing["on"] = True
            try:
                for entry, value in ((issuer, account.issuer), (label, account.label),
                                     (group, account.group)):
                    if entry.get_text() != value and not entry.has_focus():
                        entry.get_buffer().set_text(value, -1)
            finally:
                syncing["on"] = False
        set_dirty(dirty(account))
        ordered = backend.store.get_all()
        index = next((i for i, row in enumerate(ordered) if row.id == account.id), 0)
        position.set_text(f"{index + 1} of {len(ordered)}")
        up_btn.set_sensitive(index > 0)
        down_btn.set_sensitive(index < len(ordered) - 1)

    def reset() -> None:
        syncing["on"] = True
        try:
            for entry in (issuer, label, group):
                entry.get_buffer().set_text("", 0)
        finally:
            syncing["on"] = False
        set_dirty(False)
        hide_secret()
        delete_btn.set_label("Delete account")
        say("")

    def on_edit(_entry) -> None:  # type: ignore[no-untyped-def]
        if syncing["on"]:
            return
        _backend, account = current()
        if account is not None:
            set_dirty(dirty(account))

    for entry in (issuer, label, group):
        entry.connect("changed", on_edit)

    controller.refresh = render  # type: ignore[method-assign]
    controller.reset = reset  # type: ignore[method-assign]

    def do_copy() -> None:
        backend, account = current()
        if backend is None or account is None:
            return
        text = code.get_text().replace(" ", "")
        if text and set(text) != {"-"}:
            window.copy_code(text)
            flash(copy_btn, "Copied", "Copy code")

    def do_next() -> None:
        backend, account = current()
        if backend is None or account is None:
            return
        updated = backend.store.advance_counter(account.id)
        if updated is None:
            return
        backend.store.save()
        card = accounts_mod.account_card(
            backend.store, account.id, int(time.time() * 1000), backend.cipher)
        if card is not None:
            window.copy_code(card.code)
            flash(copy_btn, "Copied", "Copy code")
        render()
        window.refresh()

    def do_save() -> None:
        backend, account = current()
        if backend is None or account is None:
            return
        try:
            accounts_mod.edit_account(
                backend.store, account.id, issuer.get_text(), label.get_text(), group.get_text())
        except ValueError as exc:
            say(accounts_mod.add_error_message(exc))
            return
        backend.store.save()
        say("")
        set_dirty(False)
        window.refresh()

    def hide_secret() -> None:
        revealed["secret"] = ""
        secret_text.set_text("")
        secret_text.set_visible(False)
        secret_actions.set_visible(False)
        reveal_btn.set_label("Reveal secret")

    def do_reveal() -> None:
        backend, account = current()
        if backend is None or account is None:
            return
        if revealed["secret"]:
            hide_secret()
            return
        secret = accounts_mod.reveal_secret(backend.store, account.id, backend.cipher)
        if secret is None:
            say("Can't decrypt on this phone. Re-add it.")
            return
        revealed["secret"] = secret
        secret_text.set_text(" ".join(secret[i:i + 4] for i in range(0, len(secret), 4)))
        secret_text.set_visible(True)
        secret_actions.set_visible(True)
        reveal_btn.set_label("Hide secret")

    def do_copy_secret() -> None:
        if revealed["secret"]:
            window.copy_code(revealed["secret"])
            flash(copy_secret, "Copied", "Copy secret")

    def do_copy_uri() -> None:
        backend, account = current()
        if backend is None or account is None:
            return
        uri = accounts_mod.transfer_uri(backend.store, account.id, backend.cipher)
        if uri is not None:
            window.copy_code(uri)
            flash(copy_uri, "Copied", "Copy link")

    def do_move(delta: int) -> None:
        backend, account = current()
        if backend is None or account is None:
            return
        ordered = backend.store.get_all()
        index = next((i for i, row in enumerate(ordered) if row.id == account.id), -1)
        if accounts_mod.reorder_account(backend.store, account.id, index + delta) is None:
            return
        backend.store.save()
        render()
        window.refresh()

    def do_delete() -> None:
        # Two taps, inline: the button itself asks.
        if delete_btn.get_label() != "Confirm delete":
            delete_btn.set_label("Confirm delete")
            return
        _backend, account = current()
        if _backend is None or account is None:
            return
        accounts_mod.delete_account(_backend.store, account.id)
        _backend.store.save()
        delete_btn.set_label("Delete account")
        window.show("list")
        window.refresh()

    page = widgets.page(header, body)
    return page, controller
