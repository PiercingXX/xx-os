"""Add-account page: image-file scan, paste, manual entry. Linux mapping of
Android `ui/AddAccountScreen.kt`; all three land in the same vault path."""

from __future__ import annotations


def _widen_scan(button) -> None:  # type: ignore[no-untyped-def]
    child = button.get_first_child()
    if child is not None and hasattr(child, "set_max_width_chars"):
        child.set_max_width_chars(11)


class AddController:
    def __init__(self, window) -> None:  # type: ignore[no-untyped-def]
        self.window = window

    def on_show(self) -> None:
        pass


def _status_label(window):  # type: ignore[no-untyped-def]
    import xx_ui.widgets as widgets

    return widgets.caption("")


def build(window):  # type: ignore[no-untyped-def]
    import threading

    import xx_ui.widgets as widgets
    from xx_auth import accounts as accounts_mod
    from xx_auth import qr as qr_mod

    Gtk = window._Gtk
    controller = AddController(window)

    header = widgets.compact_header(lambda: window.show("list"), "Add account", "Scan, paste or type")
    body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)
    status = _status_label(window)

    def say(message: str) -> None:
        status.set_text(message)

    scan_btn = widgets.outline_button("Choose file", lambda: pick_image())
    _widen_scan(scan_btn)
    scan_row = widgets.setting_row(
        "Scan an image", qr_mod.backend_help() or "QR image file",
        scan_btn, last=False,
    )
    body.append(scan_row)

    paste = widgets.deferred_entry("Paste otpauth:// URI, transfer link or secret")
    body.append(widgets.section_label("Paste"))
    body.append(paste)
    body.append(widgets.primary_button("Add pasted code", lambda: do_paste()))

    body.append(widgets.section_label("Type it in"))
    issuer = widgets.text_entry("Issuer (optional)")
    account = widgets.text_entry("Account name")
    secret = widgets.text_entry("Secret (Base32)")
    body.append(issuer)
    body.append(account)
    body.append(secret)

    kind = {"hotp": False}
    algo = {"value": "SHA1"}
    digits = {"value": 6}
    type_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    algo_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    digits_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    body.append(type_box)
    body.append(algo_box)
    body.append(digits_box)

    def rebuild_options() -> None:
        for box, items in (
            (type_box, [("TOTP", not kind["hotp"]), ("HOTP", kind["hotp"])]),
            (algo_box, [(name, algo["value"] == name) for name in ("SHA1", "SHA256", "SHA512")]),
            (digits_box, [(str(n), digits["value"] == n) for n in (6, 7, 8)]),
        ):
            child = box.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                box.remove(child)
                child = nxt
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

    rebuild_options()
    period = widgets.text_entry("Period seconds (TOTP)")
    period.get_buffer().set_text("30", 2)
    counter = widgets.text_entry("Counter (HOTP)")
    counter.get_buffer().set_text("0", 1)
    body.append(period)
    body.append(counter)
    body.append(widgets.primary_button("Save account", lambda: do_manual()))
    body.append(status)

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
            result = accounts_mod.add_from_text(backend.store, paste.get_text(), backend.cipher)
        except ValueError as exc:
            say(accounts_mod.add_error_message(exc))
            return
        backend.store.save()
        paste.get_buffer().set_text("", 0)
        say(result.summary())
        window.refresh()

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
            created = accounts_mod.add_manual(
                backend.store, issuer.get_text(), account.get_text(), secret.get_text(),
                kind["hotp"], algo["value"], digits["value"], period_value, counter_value,
                backend.cipher,
            )
        except ValueError as exc:
            say(accounts_mod.add_error_message(exc))
            return
        backend.store.save()
        secret.get_buffer().set_text("", 0)
        say(f"Added {created.issuer or created.label}.")
        window.refresh()

    def pick_image() -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title("Scan a QR image")
        dialog.open(window.win, None, on_image_chosen)

    def on_image_chosen(dialog, result) -> None:  # type: ignore[no-untyped-def]
        try:
            chosen = dialog.open_finish(result)
        except Exception:  # noqa: BLE001
            return
        path = chosen.get_file().get_path()
        if not path:
            return
        say("Reading QR…")
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
        backend = window.backend
        if backend is None or backend.cipher is None:
            say("The vault isn't unlocked yet.")
            return False
        try:
            result = accounts_mod.add_from_text(backend.store, payload, backend.cipher)
        except ValueError as exc:
            say(accounts_mod.add_error_message(exc))
            return False
        backend.store.save()
        say(result.summary())
        window.refresh()
        return False

    page = widgets.page(header, body)
    return page, controller
