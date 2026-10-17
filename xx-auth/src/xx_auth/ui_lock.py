"""Lock, first-run setup and lock-settings pages. The lock model is new on
Linux (Android used the Keystore + device lock): with a keyring the vault
key is random and the password only gates the UI; without one the password
derives the key and the lock is mandatory."""

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


def build_lock(window):  # type: ignore[no-untyped-def]
    import xx_ui.widgets as widgets

    Gtk = window._Gtk
    controller = LockController(window)

    header = widgets.page_header("xx-auth")
    body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)
    title = widgets.label("", "xx-title")
    body.append(title)
    caption = widgets.caption("")
    body.append(caption)
    password = widgets.text_entry("App password")
    password.set_visibility(False)
    password.connect("activate", lambda _e: do_unlock())
    body.append(password)
    confirm = widgets.text_entry("Confirm password")
    confirm.set_visibility(False)
    body.append(confirm)
    action = widgets.primary_button("Unlock", lambda: do_unlock())
    body.append(action)
    retry = widgets.text_button("Retry keyring", lambda: retry_keyring())
    body.append(retry)
    status = widgets.caption("")
    body.append(status)

    def say(message: str) -> None:
        status.set_text(message)

    def render() -> None:
        if controller._mode == "setup":
            title.set_text("Protect your codes")
            caption.set_text("No keyring answered, so an app password encrypts the vault.")
            password.set_visible(True)
            confirm.set_visible(True)
            action.set_label("Protect vault")
            retry.set_visible(True)
        else:
            title.set_text("xx-auth is locked")
            caption.set_text("Enter the app password to see your codes.")
            password.set_visible(True)
            confirm.set_visible(False)
            action.set_label("Unlock")
            retry.set_visible(False)
        status.set_text("")

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
            window.set_password(first)
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

    header = widgets.compact_header(lambda: window.show("list"), "Lock", "App lock and idle relock")
    body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)
    syncing = {"on": False}

    lock_switch = widgets.make_switch(False, lambda on: toggle_lock(on))
    body.append(widgets.setting_row("App lock", "Ask for the password on start", lock_switch))
    idle = widgets.text_entry("Idle relock (seconds)")
    body.append(idle)
    body.append(widgets.primary_button("Save idle timeout", lambda: save_idle()))
    keyring_switch = widgets.make_switch(False, lambda on: toggle_keyring(on))
    body.append(widgets.setting_row("Use keyring", "Device keyring", keyring_switch))

    body.append(widgets.section_label("Change password"))
    current = widgets.text_entry("Current password")
    current.set_visibility(False)
    fresh = widgets.text_entry("New password (8+ characters)")
    fresh.set_visibility(False)
    repeat = widgets.text_entry("Confirm new password")
    repeat.set_visibility(False)
    body.append(current)
    body.append(fresh)
    body.append(repeat)
    body.append(widgets.primary_button("Change password", lambda: change_password()))
    status = widgets.caption("")
    body.append(status)

    def say(message: str) -> None:
        status.set_text(message)

    def render() -> None:
        backend = window.backend
        if backend is None:
            return
        syncing["on"] = True
        try:
            has_password = bool(backend.prefs.lock_hash)
            lock_switch.set_active(backend.prefs.lock_enabled and has_password)
            keyring_switch.set_active(not backend.prefs.keyring_disabled)
            idle.get_buffer().set_text(str(backend.prefs.idle_seconds), len(str(backend.prefs.idle_seconds)))
        finally:
            syncing["on"] = False

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
        say("App lock on." if on else "App lock off.")

    def save_idle() -> None:
        backend = window.backend
        if backend is None:
            return
        try:
            seconds = int(idle.get_text().strip())
        except ValueError:
            say("Idle seconds must be a whole number.")
            return
        if seconds < 15:
            say("Idle relock needs at least 15 seconds.")
            return
        backend.prefs.idle_seconds = seconds
        prefs_mod.save(backend.prefs)
        say("Idle relock saved.")

    def toggle_keyring(on: bool) -> None:
        backend = window.backend
        if backend is None or syncing["on"]:
            return
        backend.prefs.keyring_disabled = not on
        prefs_mod.save(backend.prefs)
        say("Keyring use saved. It applies on next start.")

    def change_password() -> None:
        backend = window.backend
        if backend is None:
            return
        import binascii

        from xx_auth import keystore as keystore_mod

        fresh_text, repeat_text = fresh.get_text(), repeat.get_text()
        if len(fresh_text) < 8 or fresh_text != repeat_text:
            say("The new passwords don't match or are too short.")
            return
        if backend.prefs.lock_hash:
            try:
                salt = binascii.unhexlify(backend.prefs.lock_salt)
            except (binascii.Error, ValueError):
                say("The stored lock is damaged. Reinstall to reset it.")
                return
            if keystore_mod.password_verifier(current.get_text(), salt) != backend.prefs.lock_hash:
                say("Wrong current password.")
                return
        salt = keystore_mod.new_salt()
        backend.prefs.lock_salt = binascii.hexlify(salt).decode("ascii")
        backend.prefs.lock_hash = keystore_mod.password_verifier(fresh_text, salt)
        if backend.password_mode:
            kdf_salt = keystore_mod.new_salt()
            backend.prefs.lock_kdf_salt = binascii.hexlify(kdf_salt).decode("ascii")
            backend.cipher = keystore_mod.SecretCipher(keystore_mod.derive_key(fresh_text, kdf_salt))
        prefs_mod.save(backend.prefs)
        for entry in (current, fresh, repeat):
            entry.get_buffer().set_text("", 0)
        say("Password changed.")

    page = widgets.page(header, body)
    return page, controller
