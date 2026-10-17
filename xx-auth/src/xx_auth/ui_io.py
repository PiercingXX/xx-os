"""Import/export page: real Aegis vault files through file pickers. Linux
mapping of Android `ui/ImportExportScreen.kt` (SAF → Gtk.FileDialog, async;
heavy crypto on worker threads)."""

from __future__ import annotations


def _widen_choose(button) -> None:  # type: ignore[no-untyped-def]
    child = button.get_first_child()
    if child is not None and hasattr(child, "set_max_width_chars"):
        child.set_max_width_chars(11)


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
    body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)

    body.append(widgets.section_label("Export"))
    password = widgets.text_entry("Export password (blank writes a plain file)")
    password.set_visibility(False)
    confirm = widgets.text_entry("Confirm password")
    confirm.set_visibility(False)
    body.append(password)
    body.append(confirm)
    body.append(widgets.primary_button("Export vault file", lambda: do_export()))

    body.append(widgets.section_label("Import"))
    import_password = widgets.text_entry("Vault file password (when encrypted)")
    import_password.set_visibility(False)
    import_password.set_visible(False)
    body.append(import_password)
    choose_btn = widgets.outline_button("Choose file", lambda: pick_import())
    _widen_choose(choose_btn)
    import_row = widgets.setting_row(
        "Import file", "Aegis vault / URI list",
        choose_btn, last=False,
    )
    body.append(import_row)
    found = widgets.caption("")
    found.set_visible(False)
    body.append(found)
    merge_row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    merge_row.set_halign(Gtk.Align.CENTER)
    add_btn = widgets.primary_button("Add to vault", lambda: do_apply(True))
    replace_btn = widgets.danger_button("Replace vault", lambda: do_apply(False))
    merge_row.append(add_btn)
    merge_row.append(replace_btn)
    merge_row.set_visible(False)
    body.append(merge_row)

    status = widgets.caption("")
    body.append(status)

    pending = {"raw": None, "password": None}

    def say(message: str) -> None:
        status.set_text(message)

    def backend_ready():  # type: ignore[no-untyped-def]
        backend = window.backend
        if backend is None or backend.cipher is None:
            say("The vault isn't unlocked yet.")
            return None
        return backend

    def do_export() -> None:
        backend = backend_ready()
        if backend is None:
            return
        first, second = password.get_text(), confirm.get_text()
        if first != second:
            say("The passwords don't match.")
            return
        if not first:
            if password.get_text() != "\u0000":
                say("Blank password writes a PLAIN file. Tap Export again to confirm.")
                password.get_buffer().set_text("\u0000", 1)
                return
            secret: str | None = None
        else:
            secret = first
        password.get_buffer().set_text("", 0)
        confirm.get_buffer().set_text("", 0)
        chosen = {"secret": secret}
        dialog = Gtk.FileDialog()
        dialog.set_title("Export vault")
        dialog.set_initial_name(accounts_mod.export_file_name())
        dialog.save(window.win, None, lambda d, r: on_export_chosen(d, r, chosen["secret"]))

    def on_export_chosen(dialog, result, secret) -> None:  # type: ignore[no-untyped-def]
        try:
            target = dialog.save_finish(result)
        except Exception:  # noqa: BLE001
            return
        path = target.get_file().get_path()
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
            window._GLib.idle_add(say, "Couldn't write that file.")
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
        path = chosen.get_file().get_path()
        if not path:
            return
        say("Reading file…")
        thread = threading.Thread(target=lambda: import_work(path), daemon=True)
        thread.start()

    def import_work(path: str) -> None:
        try:
            with open(path, "rb") as handle:
                raw = handle.read(MAX_IMPORT_BYTES + 1)
        except OSError:
            window._GLib.idle_add(say, "Couldn't read that file.")
            return
        window._GLib.idle_add(after_read, raw)

    def after_read(raw: bytes) -> bool:
        from xx_auth import aegis as aegis_mod

        backend = window.backend
        if backend is None or backend.cipher is None:
            say("The vault isn't unlocked yet.")
            return False
        secret = import_password.get_text() or None
        try:
            parsed = aegis_mod.parse_file(raw, secret)
        except ValueError as exc:
            if "password is required" in str(exc):
                import_password.set_visible(True)
                say("This file is encrypted. Enter its password above and pick it again.")
                return False
            say(accounts_mod.import_error_message(exc))
            return False
        pending["raw"] = raw
        pending["password"] = secret
        found.set_text(f"Found {len(parsed.accounts)} account{'s' if len(parsed.accounts) != 1 else ''}"
                       + (f", {parsed.skipped} skipped." if parsed.skipped else "."))
        found.set_visible(True)
        merge_row.set_visible(True)
        replace_btn.set_label("Replace vault")
        return False

    def do_apply(merge: bool) -> None:
        backend = backend_ready()
        if backend is None or pending["raw"] is None:
            return
        if not merge and replace_btn.get_label() != "Confirm replace":
            replace_btn.set_label("Confirm replace")
            say("Replace wipes the vault first. Tap again to confirm.")
            return
        replace_btn.set_label("Replace vault")
        thread = threading.Thread(
            target=lambda: apply_work(backend, pending["raw"], pending["password"], merge), daemon=True)
        thread.start()

    def apply_work(backend, raw: bytes, secret: str | None, merge: bool) -> None:  # type: ignore[no-untyped-def]
        try:
            imported, skipped = accounts_mod.import_vault_file(backend.store, raw, secret, backend.cipher, merge)
        except ValueError as exc:
            window._GLib.idle_add(say, accounts_mod.import_error_message(exc))
            return
        backend.store.save()
        pending["raw"] = None
        merge_row.set_visible(False)
        found.set_visible(False)
        import_password.get_buffer().set_text("", 0)
        skipped_text = f" Skipped {skipped}." if skipped else ""
        window._GLib.idle_add(say, f"Added {imported} account{'s' if imported != 1 else ''}." + skipped_text)
        window._GLib.idle_add(lambda: window.refresh() or False)

    page = widgets.page(header, body)
    return page, controller
