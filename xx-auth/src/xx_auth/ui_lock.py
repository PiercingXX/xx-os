"""Lock, first-run setup and app-lock settings pages. The lock model is new
on Linux (Android used the Keystore + device lock): with a keyring the
vault key is random and the password only gates the UI; without one the
password derives the key and the lock is mandatory."""

from __future__ import annotations


class LockController:
    def __init__(self, window) -> None:  # type: ignore[no-untyped-def]
        self.window = window
        self._mode = "unlock"

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self.refresh()

    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        raise NotImplementedError


def _error_line():  # type: ignore[no-untyped-def]
    import xx_ui.widgets as widgets

    line = widgets.wrap_label("")
    line.add_css_class("xx-danger-text")
    line.set_margin_top(12)
    line.set_visible(False)
    return line


def build_lock(window):  # type: ignore[no-untyped-def]
    import xx_ui.widgets as widgets

    Gtk = window._Gtk
    controller = LockController(window)

    header = widgets.page_header("xx-auth")
    body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    note = widgets.wrap_label("")
    note.set_margin_top(8)
    note.set_margin_bottom(16)
    body.append(note)
    password = widgets.text_entry("Password")
    password.set_visibility(False)
    password.set_margin_top(8)
    password.set_margin_bottom(12)
    password.connect("activate", lambda _e: do_unlock())
    body.append(password)
    confirm = widgets.text_entry("Confirm password")
    confirm.set_visibility(False)
    confirm.set_margin_bottom(12)
    confirm.connect("activate", lambda _e: do_unlock())
    body.append(confirm)
    action = widgets.primary_button("Unlock", lambda: do_unlock())
    action.get_first_child().set_max_width_chars(14)
    body.append(action)
    retry = widgets.text_button("Retry keyring", lambda: retry_keyring())
    retry.get_first_child().set_max_width_chars(14)
    retry.set_margin_top(8)
    body.append(retry)
    status = _error_line()
    body.append(status)

    def say(message: str) -> None:
        status.set_text(message)
        status.set_visible(bool(message))

    def render() -> None:
        setup = controller._mode == "setup"
        # Locked: the password field and Unlock say it; only setup
        # needs a line.
        note.set_text("No keyring answered. A password encrypts the vault."
                      if setup else "")
        note.set_visible(setup)
        password.set_placeholder_text("New password, 8+ characters" if setup else "Password")
        confirm.set_visible(setup)
        action.set_label("Protect vault" if setup else "Unlock")
        retry.set_visible(setup)
        say("")

    controller.refresh = render  # type: ignore[method-assign]

    def do_unlock() -> None:
        backend = window.backend
        if backend is None:
            return
        if controller._mode == "setup":
            first, second = password.get_text(), confirm.get_text()
            if len(first) < 8:
                say("Use at least 8 characters.")
                return
            if first != second:
                say("The passwords don't match.")
                return
            password.get_buffer().set_text("", 0)
            confirm.get_buffer().set_text("", 0)
            try:
                window.set_password(first)
            except ValueError as exc:
                say(str(exc) or "The vault couldn't be opened. Nothing was changed.")
            return
        if window.unlock(password.get_text()):
            password.get_buffer().set_text("", 0)
        else:
            say("Wrong password.")

    def retry_keyring() -> None:
        window._resolve_backend()

    page = widgets.page(header, body)
    return page, controller


class SettingsController:
    def __init__(self, window) -> None:  # type: ignore[no-untyped-def]
        self.window = window

    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        raise NotImplementedError


def build_settings(window):  # type: ignore[no-untyped-def]
    import xx_ui.widgets as widgets
    from xx_auth import prefs as prefs_mod

    Gtk = window._Gtk
    controller = SettingsController(window)

    header = widgets.compact_header(lambda: window.show("list"), "App lock")
    body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    syncing = {"on": False}

    lock_switch = widgets.make_switch(False, lambda on: toggle_lock(on))
    body.append(widgets.setting_row("App lock", "Ask for the password", lock_switch))
    idle = widgets.text_entry("120")
    idle.set_width_chars(5)
    idle.set_max_width_chars(5)
    idle.set_input_purpose(Gtk.InputPurpose.DIGITS)
    idle.connect("activate", lambda _e: save_idle())
    focus = Gtk.EventControllerFocus.new()
    focus.connect("leave", lambda _c: save_idle())
    idle.add_controller(focus)
    body.append(widgets.setting_row("Idle relock", "Seconds", idle))
    keyring_switch = widgets.make_switch(False, lambda on: toggle_keyring(on))
    body.append(widgets.setting_row("Use keyring", "Applies on next start", keyring_switch, last=True))

    password_label = widgets.section_label("Password")
    password_label.set_margin_top(28)
    password_label.set_margin_bottom(8)
    body.append(password_label)
    current = widgets.text_entry("Current password")
    current.set_visibility(False)
    fresh = widgets.text_entry("New password, 8+ characters")
    fresh.set_visibility(False)
    repeat = widgets.text_entry("Confirm new password")
    repeat.set_visibility(False)
    for entry in (current, fresh, repeat):
        entry.set_margin_bottom(12)
        body.append(entry)
    change_btn = widgets.primary_button("Change password", lambda: change_password())
    change_btn.get_first_child().set_max_width_chars(15)
    body.append(change_btn)
    status = _error_line()
    body.append(status)

    def say(message: str) -> None:
        """Errors only: switches and fields show their own state."""
        status.set_text(message)
        status.set_visible(bool(message))

    def render() -> None:
        backend = window.backend
        if backend is None:
            return
        syncing["on"] = True
        try:
            has_password = bool(backend.prefs.lock_hash)
            lock_switch.set_active(backend.prefs.lock_enabled and has_password)
            keyring_switch.set_active(not backend.prefs.keyring_disabled)
            seconds = str(backend.prefs.idle_seconds)
            if not idle.has_focus():
                idle.get_buffer().set_text(seconds, -1)
            current.set_visible(has_password)
            change_btn.set_label("Change password" if has_password else "Set password")
        finally:
            syncing["on"] = False
        say("")

    controller.refresh = render  # type: ignore[method-assign]

    def toggle_lock(on: bool) -> None:
        backend = window.backend
        if backend is None or syncing["on"]:
            return
        if on and not backend.prefs.lock_hash:
            say("Set a password below first.")
            lock_switch.set_active(False)
            return
        backend.prefs.lock_enabled = on
        prefs_mod.save(backend.prefs)
        say("")

    def save_idle() -> None:
        backend = window.backend
        if backend is None or syncing["on"]:
            return
        text = idle.get_text().strip()
        if text == str(backend.prefs.idle_seconds):
            return
        try:
            seconds = int(text)
        except ValueError:
            say("Idle seconds must be a whole number.")
            return
        if seconds < 15:
            say("Idle relock needs at least 15 seconds.")
            return
        backend.prefs.idle_seconds = seconds
        prefs_mod.save(backend.prefs)
        say("")

    def toggle_keyring(on: bool) -> None:
        backend = window.backend
        if backend is None or syncing["on"]:
            return
        import os as _os

        from xx_auth import accounts as _accounts_mod
        from xx_auth import keystore as _keystore_mod
        from xx_auth.keystore import MemoryKeyring as _MemoryKeyring

        if backend.cipher is None:
            say("Unlock the vault first.")
            return
        keyring = _MemoryKeyring() if _os.environ.get("XX_AUTH_NO_KEYRING") else _keystore_mod.SecretServiceKeyring()
        if on == (not backend.prefs.keyring_disabled):
            return
        if not on and (len(fresh.get_text()) < 8 or fresh.get_text() != repeat.get_text()):
            say("Enter the new password below first (8+ characters, twice).")
            return
        try:
            if on:
                backend.cipher = _accounts_mod.switch_key_source(
                    backend.store, backend.prefs, keyring, backend.cipher, None, True)
            else:
                backend.cipher = _accounts_mod.switch_key_source(
                    backend.store, backend.prefs, keyring, backend.cipher,
                    fresh.get_text(), False)
        except ValueError as exc:
            say(str(exc) or "Couldn't move the vault. Nothing changed.")
            return
        for entry in (current, fresh, repeat):
            entry.get_buffer().set_text("", 0)
        render()

    def change_password() -> None:
        backend = window.backend
        if backend is None:
            return
        import os as _os

        from xx_auth import accounts as accounts_mod
        from xx_auth import keystore as keystore_mod
        from xx_auth.keystore import MemoryKeyring as _MemoryKeyring

        fresh_text, repeat_text = fresh.get_text(), repeat.get_text()
        if len(fresh_text) < 8 or fresh_text != repeat_text:
            say("The new passwords don't match or are too short.")
            return
        keyring = _MemoryKeyring() if _os.environ.get("XX_AUTH_NO_KEYRING") else keystore_mod.SecretServiceKeyring()
        try:
            new_cipher = accounts_mod.change_password(
                backend.store, backend.prefs, keyring, current.get_text(), fresh_text)
        except ValueError as exc:
            backend.store.load()
            say(str(exc) or "Couldn't re-encrypt the vault. Nothing changed.")
            return
        if new_cipher is not None:
            backend.cipher = new_cipher
            source = backend.store.header.key_source if backend.store.header else ""
            backend.password_mode = source != "keyring"
        for entry in (current, fresh, repeat):
            entry.get_buffer().set_text("", 0)
        render()

    page = widgets.page(header, body)
    return page, controller
