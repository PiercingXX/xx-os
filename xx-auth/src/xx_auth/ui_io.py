"""Import/export page: real Aegis vault files through file pickers. Linux
mapping of Android `ui/ImportExportScreen.kt` (SAF → Gtk.FileDialog, async;
heavy crypto on worker threads)."""

from __future__ import annotations


def _widen(button, chars: int) -> None:  # type: ignore[no-untyped-def]
    child = button.get_first_child()
    if child is not None and hasattr(child, "set_max_width_chars"):
        child.set_max_width_chars(chars)


class IoController:
    def __init__(self, window) -> None:  # type: ignore[no-untyped-def]
        self.window = window

    def on_show(self) -> None:
        pass


def build(window):  # type: ignore[no-untyped-def]
    import threading

    import xx_ui.widgets as widgets
    from xx_auth import accounts as accounts_mod
    from xx_auth.aegis import MAX_IMPORT_BYTES

    Gtk = window._Gtk
    controller = IoController(window)

    header = widgets.compact_header(lambda: window.show("list"), "Import / export", "Aegis vault files")
    body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)

    def section(title: str, first: bool = False) -> None:
        label = widgets.section_label(title)
        label.set_margin_top(8 if first else 28)
        label.set_margin_bottom(8)
        body.append(label)

    def labeled(caption_text: str, widget) -> None:  # type: ignore[no-untyped-def]
        cap = widgets.caption(caption_text)
        cap.set_margin_bottom(6)
        body.append(cap)
        widget.set_margin_bottom(12)
        body.append(widget)

    section("Export", first=True)
    password = widgets.text_entry("Optional")
    password.set_visibility(False)
    confirm = widgets.text_entry("")
    confirm.set_visibility(False)
    labeled("Password", password)
    # Confirm appears once a password is typed: a plain export needs none.
    confirm_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    confirm_cap = widgets.caption("Confirm")
    confirm_cap.set_margin_bottom(6)
    confirm_box.append(confirm_cap)
    confirm.set_margin_bottom(12)
    confirm_box.append(confirm)
    confirm_box.set_visible(False)
    body.append(confirm_box)
    export_btn = widgets.primary_button("Export vault file", lambda: do_export())
    _widen(export_btn, 17)
    body.append(export_btn)

    section("Import")
    import_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    import_box.set_visible(False)
    import_password = widgets.text_entry("File password")
    import_password.set_visibility(False)
    import_password.set_margin_bottom(12)
    import_box.append(import_password)
    body.append(import_box)
    # One full-width button, like Export above: a row with a trailing
    # button squeezes both at large text.
    choose_btn = widgets.outline_button("Choose vault file", lambda: pick_import())
    _widen(choose_btn, 18)
    body.append(choose_btn)
    found = widgets.label("", "xx-title")
    found.set_margin_top(16)
    found.set_visible(False)
    body.append(found)
    merge_row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    merge_row.set_homogeneous(True)
    merge_row.set_margin_top(12)
    add_btn = widgets.primary_button("Add", lambda: do_apply(True))
    replace_btn = widgets.outline_button("Replace", lambda: do_apply(False))
    replace_btn.add_css_class("xx-auth-danger")
    for btn in (add_btn, replace_btn):
        btn.set_hexpand(True)
        _widen(btn, 15)
        merge_row.append(btn)
    merge_row.set_visible(False)
    body.append(merge_row)

    status = widgets.wrap_label("")
    status.set_margin_top(16)
    status.set_visible(False)
    body.append(status)
    clearance = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    clearance.set_size_request(-1, 32)
    body.append(clearance)

    pending = {"raw": None, "password": None}
    plain = {"armed": False}

    def say(message: str, error: bool = False) -> bool:
        status.set_text(message)
        status.set_visible(bool(message))
        if error:
            status.add_css_class("xx-danger-text")
        else:
            status.remove_css_class("xx-danger-text")
        return False

    def fail(message: str) -> bool:
        return say(message, True)

    def disarm_plain(*_args) -> None:  # type: ignore[no-untyped-def]
        if plain["armed"]:
            plain["armed"] = False
            export_btn.set_label("Export vault file")

    def sync_confirm(*_args) -> None:  # type: ignore[no-untyped-def]
        typed = bool(password.get_text())
        confirm_box.set_visible(typed)
        if not typed and confirm.get_text():
            confirm.get_buffer().set_text("", 0)

    password.connect("changed", disarm_plain)
    password.connect("changed", sync_confirm)
    confirm.connect("changed", disarm_plain)

    def backend_ready():  # type: ignore[no-untyped-def]
        backend = window.backend
        if backend is None or backend.cipher is None:
            fail("The vault isn't unlocked yet.")
            return None
        return backend

    def do_export() -> None:
        backend = backend_ready()
        if backend is None:
            return
        first, second = password.get_text(), confirm.get_text()
        if first != second:
            fail("The passwords don't match.")
            return
        if not first and not plain["armed"]:
            # A plain file holds every secret: the button asks once more.
            plain["armed"] = True
            export_btn.set_label("Export plain file")
            say("")
            return
        secret: str | None = first or None
        disarm_plain()
        password.get_buffer().set_text("", 0)
        confirm.get_buffer().set_text("", 0)
        say("")
        dialog = Gtk.FileDialog()
        dialog.set_title("Export vault")
        dialog.set_initial_name(accounts_mod.export_file_name())
        dialog.save(window.win, None, lambda d, r: on_export_chosen(d, r, secret))

    def on_export_chosen(dialog, result, secret) -> None:  # type: ignore[no-untyped-def]
        try:
            target = dialog.save_finish(result)
        except Exception:  # noqa: BLE001
            return
        path = target.get_path() if target is not None else None
        if not path:
            return
        thread = threading.Thread(target=lambda: export_work(path, secret), daemon=True)
        thread.start()

    def export_work(path: str, secret: str | None) -> None:
        backend = window.backend
        if backend is None or backend.cipher is None:
            return
        try:
            text = accounts_mod.export_vault_text(backend.store, secret, backend.cipher)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
            count = backend.store.count()
        except OSError:
            window._GLib.idle_add(fail, "Couldn't write that file.")
            return
        window._GLib.idle_add(say, f"Exported {count} account{'s' if count != 1 else ''}.")

    def pick_import() -> None:
        if backend_ready() is None:
            return
        dialog = Gtk.FileDialog()
        dialog.set_title("Import a vault file")
        dialog.open(window.win, None, on_import_chosen)

    def on_import_chosen(dialog, result) -> None:  # type: ignore[no-untyped-def]
        try:
            chosen = dialog.open_finish(result)
        except Exception:  # noqa: BLE001
            return
        path = chosen.get_path() if chosen is not None else None
        if not path:
            return
        say("")
        thread = threading.Thread(target=lambda: import_work(path), daemon=True)
        thread.start()

    def import_work(path: str) -> None:
        try:
            with open(path, "rb") as handle:
                raw = handle.read(MAX_IMPORT_BYTES + 1)
        except OSError:
            window._GLib.idle_add(fail, "Couldn't read that file.")
            return
        window._GLib.idle_add(after_read, raw)

    def after_read(raw: bytes) -> bool:
        from xx_auth import aegis as aegis_mod

        backend = window.backend
        if backend is None or backend.cipher is None:
            return fail("The vault isn't unlocked yet.")
        secret = import_password.get_text() or None
        try:
            parsed = aegis_mod.parse_file(raw, secret)
        except ValueError as exc:
            if "password is required" in str(exc):
                import_box.set_visible(True)
                return fail("Encrypted. Enter its password and choose it again.")
            return fail(accounts_mod.import_error_message(exc))
        pending["raw"] = raw
        pending["password"] = secret
        count = len(parsed.accounts)
        found.set_text(f"{count} account{'s' if count != 1 else ''}"
                       + (f", {parsed.skipped} skipped" if parsed.skipped else ""))
        found.set_visible(True)
        merge_row.set_visible(True)
        replace_btn.set_label("Replace")
        return False

    def do_apply(merge: bool) -> None:
        backend = backend_ready()
        if backend is None or pending["raw"] is None:
            return
        if not merge and replace_btn.get_label() != "Confirm replace":
            # Replace wipes the vault first: the button asks once more.
            replace_btn.set_label("Confirm replace")
            return
        replace_btn.set_label("Replace")
        thread = threading.Thread(
            target=lambda: apply_work(backend, pending["raw"], pending["password"], merge), daemon=True)
        thread.start()

    def finish_import(imported: int, skipped: int) -> bool:
        pending["raw"] = None
        merge_row.set_visible(False)
        found.set_visible(False)
        import_box.set_visible(False)
        import_password.get_buffer().set_text("", 0)
        skipped_text = f" Skipped {skipped}." if skipped else ""
        say(f"Added {imported} account{'s' if imported != 1 else ''}." + skipped_text)
        window.refresh()
        return False

    def apply_work(backend, raw: bytes, secret: str | None, merge: bool) -> None:  # type: ignore[no-untyped-def]
        try:
            imported, skipped = accounts_mod.import_vault_file(backend.store, raw, secret, backend.cipher, merge)
        except ValueError as exc:
            window._GLib.idle_add(fail, accounts_mod.import_error_message(exc))
            return
        backend.store.save()
        window._GLib.idle_add(finish_import, imported, skipped)

    page = widgets.page(header, body)
    return page, controller
