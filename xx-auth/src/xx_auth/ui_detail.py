"""Account detail page: live code, edit, reveal, move, delete. Linux
mapping of Android `ui/AccountDetailScreen.kt`."""

from __future__ import annotations

import time


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
        self.refresh()

    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        raise NotImplementedError


def build(window):  # type: ignore[no-untyped-def]
    import xx_ui.widgets as widgets
    from xx_auth import accounts as accounts_mod
    from xx_auth.main import group_code

    Gtk = window._Gtk
    controller = DetailController(window)

    header = widgets.compact_header(lambda: window.show("list"), "Account", "")
    body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)

    code = Gtk.Label(label="")
    code.add_css_class("xx-auth-code")
    code.add_css_class("xx-display-face")
    code.set_xalign(0.5)
    code.set_max_width_chars(10)
    code.set_ellipsize(window._Pango.EllipsizeMode.END)
    body.append(code)

    buttons = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    buttons.set_hexpand(True)
    buttons.set_homogeneous(True)
    copy_btn = widgets.primary_button("Copy code", lambda: do_copy())
    next_btn = widgets.outline_button("Next code", lambda: do_next())
    for btn in (copy_btn, next_btn):
        btn.set_hexpand(True)
        _widen(btn, 14)
    buttons.append(copy_btn)
    buttons.append(next_btn)
    body.append(buttons)

    issuer = widgets.text_entry("Issuer")
    label = widgets.text_entry("Account name")
    group = widgets.text_entry("Group (optional)")
    body.append(issuer)
    body.append(label)
    body.append(group)
    body.append(widgets.primary_button("Save changes", lambda: do_save()))

    reveal_btn = widgets.outline_button("Reveal secret", lambda: do_reveal())
    secret_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
    secret_text = widgets.caption("")
    copy_secret = widgets.text_button("Copy secret", lambda: do_copy_secret())
    copy_uri = widgets.text_button("Copy transfer link", lambda: do_copy_uri())
    secret_box.append(secret_text)
    secret_box.append(copy_secret)
    secret_box.append(copy_uri)
    secret_box.set_visible(False)
    body.append(reveal_btn)
    body.append(secret_box)

    move_row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    move_row.set_hexpand(True)
    move_row.set_homogeneous(True)
    up_btn = widgets.outline_button("Move up", lambda: do_move(-1))
    down_btn = widgets.outline_button("Move down", lambda: do_move(1))
    for btn in (up_btn, down_btn):
        btn.set_hexpand(True)
        _widen(btn, 14)
    move_row.append(up_btn)
    move_row.append(down_btn)
    body.append(move_row)

    delete_btn = widgets.danger_button("Delete", lambda: do_delete())
    body.append(delete_btn)
    status = widgets.caption("")
    body.append(status)

    revealed = {"secret": ""}

    def say(message: str) -> None:
        status.set_text(message)

    def current():  # type: ignore[no-untyped-def]
        backend = window.backend
        if backend is None or backend.cipher is None or controller.account_id is None:
            return None, None
        return backend, backend.store.get_by_id(controller.account_id)

    def render() -> None:
        backend, account = current()
        if backend is None or account is None:
            code.set_text("")
            say("That account is gone.")
            return
        card = accounts_mod.account_card(
            backend.store, account.id, int(time.time() * 1000), backend.cipher)
        if card is None:
            return
        code.set_text(group_code(card.code))
        next_btn.set_visible(card.is_hotp)
        if issuer.get_text() != account.issuer and not issuer.has_focus():
            issuer.get_buffer().set_text(account.issuer, len(account.issuer))
        if label.get_text() != account.label and not label.has_focus():
            label.get_buffer().set_text(account.label, len(account.label))
        if group.get_text() != account.group and not group.has_focus():
            group.get_buffer().set_text(account.group, len(account.group))

    controller.refresh = render  # type: ignore[method-assign]

    def do_copy() -> None:
        backend, account = current()
        if backend is None or account is None:
            return
        text = code.get_text().replace(" ", "")
        if text and set(text) != {"-"}:
            window.copy_code(text)
            say("Copied.")

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
            say("Next code copied.")
        render()

    def do_save() -> None:
        backend, account = current()
        if backend is None or account is None:
            return
        try:
            accounts_mod.edit_account(
                backend.store, account.id, issuer.get_text(), label.get_text(), group.get_text())
        except ValueError as exc:
            say(str(exc))
            return
        backend.store.save()
        say("Saved.")
        window.refresh()

    def do_reveal() -> None:
        backend, account = current()
        if backend is None or account is None:
            return
        if secret_box.get_visible():
            secret_box.set_visible(False)
            revealed["secret"] = ""
            reveal_btn.set_label("Reveal secret")
            secret_text.set_text("")
            return
        secret = accounts_mod.reveal_secret(backend.store, account.id, backend.cipher)
        if secret is None:
            say("Can't decrypt on this phone. Re-add it.")
            return
        revealed["secret"] = secret
        secret_text.set_text(secret)
        secret_box.set_visible(True)
        reveal_btn.set_label("Hide secret")

    def do_copy_secret() -> None:
        if revealed["secret"]:
            window.copy_code(revealed["secret"])
            say("Secret copied.")

    def do_copy_uri() -> None:
        backend, account = current()
        if backend is None or account is None:
            return
        uri = accounts_mod.transfer_uri(backend.store, account.id, backend.cipher)
        if uri is not None:
            window.copy_code(uri)
            say("Transfer link copied.")

    def do_move(delta: int) -> None:
        backend, account = current()
        if backend is None or account is None:
            return
        ordered = backend.store.get_all()
        index = next((i for i, row in enumerate(ordered) if row.id == account.id), -1)
        if accounts_mod.reorder_account(backend.store, account.id, index + delta) is None:
            return
        backend.store.save()
        say("Moved.")
        window.refresh()

    def do_delete() -> None:
        if delete_btn.get_label() != "Confirm delete":
            delete_btn.set_label("Confirm delete")
            say("Tap again to delete this account.")
            return
        _backend, account = current()
        if _backend is None or account is None:
            return
        accounts_mod.delete_account(_backend.store, account.id)
        _backend.store.save()
        delete_btn.set_label("Delete")
        window.show("list")
        window.refresh()

    page = widgets.page(header, body)
    return page, controller
