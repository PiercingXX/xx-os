"""Add-account page: image-file scan, paste, manual entry. Linux mapping of
Android `ui/AddAccountScreen.kt`; all three land in the same vault path,
and a successful add returns to the list, where the new row is the
receipt."""

from __future__ import annotations


def _widen(button, chars: int) -> None:  # type: ignore[no-untyped-def]
    child = button.get_first_child()
    if child is not None and hasattr(child, "set_max_width_chars"):
        child.set_max_width_chars(chars)


class AddController:
    def __init__(self, window) -> None:  # type: ignore[no-untyped-def]
        self.window = window

    def on_show(self) -> None:
        pass


def build(window):  # type: ignore[no-untyped-def]
    import threading

    import xx_ui.widgets as widgets
    from xx_auth import accounts as accounts_mod
    from xx_auth import qr as qr_mod

    Gtk = window._Gtk
    controller = AddController(window)

    header = widgets.compact_header(lambda: window.show("list"), "Add account")
    body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)

    def section(title: str, first: bool = False) -> None:
        label = widgets.section_label(title)
        label.set_margin_top(8 if first else 28)
        label.set_margin_bottom(8)
        body.append(label)

    def labeled(box, caption_text: str, widget) -> None:  # type: ignore[no-untyped-def]
        cap = widgets.caption(caption_text)
        cap.set_margin_bottom(6)
        box.append(cap)
        widget.set_margin_bottom(12)
        box.append(widget)

    status = widgets.wrap_label("")
    status.add_css_class("xx-danger-text")
    status.set_visible(False)

    def say(message: str) -> None:
        """Errors and progress only: success returns to the list."""
        status.set_text(message)
        status.set_visible(bool(message))

    section("Scan", first=True)
    scan_btn = widgets.outline_button("Choose file", lambda: pick_image())
    _widen(scan_btn, 11)
    body.append(widgets.setting_row(
        "QR image", qr_mod.backend_help() or "PNG or JPEG", scan_btn, last=True))

    section("Paste")
    paste = widgets.deferred_entry("otpauth:// or secret")
    paste.set_margin_bottom(12)
    paste.connect("activate", lambda _e: do_paste())
    body.append(paste)
    paste_btn = widgets.primary_button("Add from paste", lambda: do_paste())
    _widen(paste_btn, 14)
    body.append(paste_btn)

    section("Manual entry")
    open_manual = widgets.outline_button("Enter details by hand", lambda: show_manual())
    _widen(open_manual, 21)
    body.append(open_manual)
    manual = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    manual.set_visible(False)
    body.append(manual)

    issuer = widgets.text_entry("e.g. GitHub")
    account = widgets.text_entry("e.g. alice@example.com")
    secret = widgets.text_entry("Base32")
    labeled(manual, "Issuer", issuer)
    labeled(manual, "Account", account)
    labeled(manual, "Secret", secret)

    kind = {"hotp": False}
    algo = {"value": "SHA1"}
    digits = {"value": 6}
    type_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    algo_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    digits_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    labeled(manual, "Type", type_box)
    labeled(manual, "Algorithm", algo_box)
    labeled(manual, "Digits", digits_box)
    period = widgets.text_entry("30")
    period.get_buffer().set_text("30", 2)
    counter = widgets.text_entry("0")
    counter.get_buffer().set_text("0", 1)
    period_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    counter_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    labeled(period_box, "Period, seconds", period)
    labeled(counter_box, "Counter", counter)
    manual.append(period_box)
    manual.append(counter_box)
    save_btn = widgets.primary_button("Add account", lambda: do_manual())
    _widen(save_btn, 14)
    manual.append(save_btn)

    status.set_margin_top(12)
    body.append(status)
    clearance = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    clearance.set_size_request(-1, 32)
    body.append(clearance)

    def clear(box) -> None:  # type: ignore[no-untyped-def]
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt

    def rebuild_options() -> None:
        for box in (type_box, algo_box, digits_box):
            clear(box)
        type_box.append(widgets.segmented([
            ("TOTP", not kind["hotp"], lambda: (kind.update(hotp=False), rebuild_options())),
            ("HOTP", kind["hotp"], lambda: (kind.update(hotp=True), rebuild_options())),
        ]))
        algo_box.append(widgets.chip_bar([
            (name, algo["value"] == name, lambda n=name: (algo.update(value=n), rebuild_options()))
            for name in ("SHA1", "SHA256", "SHA512")
        ]))
        digits_box.append(widgets.chip_bar([
            (str(n), digits["value"] == n, lambda n=n: (digits.update(value=n), rebuild_options()))
            for n in (6, 7, 8)
        ]))
        # Only the field the chosen type uses.
        period_box.set_visible(not kind["hotp"])
        counter_box.set_visible(kind["hotp"])

    rebuild_options()

    def show_manual() -> None:
        open_manual.set_visible(False)
        manual.set_visible(True)

    def added() -> None:
        say("")
        window.refresh()
        window.show("list")

    def backend_ready():  # type: ignore[no-untyped-def]
        backend = window.backend
        if backend is None or backend.cipher is None:
            say("The vault isn't unlocked yet.")
            return None
        return backend

    def do_paste() -> None:
        backend = backend_ready()
        if backend is None:
            return
        try:
            accounts_mod.add_from_text(backend.store, paste.get_text(), backend.cipher)
        except ValueError as exc:
            say(accounts_mod.add_error_message(exc))
            return
        backend.store.save()
        paste.get_buffer().set_text("", 0)
        added()

    def do_manual() -> None:
        backend = backend_ready()
        if backend is None:
            return
        try:
            period_value = int(period.get_text().strip() or "30")
            counter_value = int(counter.get_text().strip() or "0")
        except ValueError:
            say("The period and counter must be whole numbers.")
            return
        try:
            accounts_mod.add_manual(
                backend.store, issuer.get_text(), account.get_text(), secret.get_text(),
                kind["hotp"], algo["value"], digits["value"], period_value, counter_value,
                backend.cipher,
            )
        except ValueError as exc:
            say(accounts_mod.add_error_message(exc))
            return
        backend.store.save()
        for entry in (issuer, account, secret):
            entry.get_buffer().set_text("", 0)
        added()

    def pick_image() -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title("Scan a QR image")
        dialog.open(window.win, None, on_image_chosen)

    def on_image_chosen(dialog, result) -> None:  # type: ignore[no-untyped-def]
        try:
            chosen = dialog.open_finish(result)
        except Exception:  # noqa: BLE001
            return
        path = chosen.get_path()
        if not path:
            return
        say("")
        thread = threading.Thread(target=lambda: scan_work(path), daemon=True)
        thread.start()

    def scan_work(path: str) -> None:
        try:
            payloads = qr_mod.decode_image_file(path)
        except ValueError as exc:
            window._GLib.idle_add(say, str(exc))
            return
        payload = accounts_mod.scan_payloads(payloads)
        if payload is None:
            window._GLib.idle_add(say, "That QR code isn't a 2FA code.")
            return
        window._GLib.idle_add(add_scanned_payload, payload)

    def add_scanned_payload(payload: str) -> bool:
        backend = backend_ready()
        if backend is None:
            return False
        try:
            accounts_mod.add_from_text(backend.store, payload, backend.cipher)
        except ValueError as exc:
            say(accounts_mod.add_error_message(exc))
            return False
        backend.store.save()
        added()
        return False

    page = widgets.page(header, body)
    return page, controller
