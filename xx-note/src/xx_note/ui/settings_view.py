"""Settings + Sync pages for xx-note.

Settings: notes folder (entry + system picker), color mode, sort order,
and a note that the theme follows the suite. Sync: WebDAV enable switch,
host/user/path, password into libsecret (never config or logs), Test
connection and Sync now on worker threads, legible status line.
"""

from __future__ import annotations

import threading


def _label(Gtk, Pango, text, cls="xx-note-preview", chars=28):  # type: ignore[no-untyped-def]
    label = Gtk.Label(label=text)
    label.add_css_class(cls)
    label.set_xalign(0.0)
    label.set_wrap(True)
    label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    label.set_max_width_chars(chars)
    return label


def _tap_to_focus(Gtk, entry):  # type: ignore[no-untyped-def]
    """Entries never take focus unless tapped (no OSK pop on page open)."""
    entry.set_can_focus(False)
    press = Gtk.GestureClick.new()
    press.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    press.connect(
        "pressed",
        lambda *_args, target=entry: (target.set_can_focus(True), target.grab_focus()),
    )
    entry.add_controller(press)


def _row(Gtk, Pango, title, subtitle=""):  # type: ignore[no-untyped-def]
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    box.set_margin_start(20)
    box.set_margin_end(20)
    box.set_margin_top(8)
    box.append(_label(Gtk, Pango, title, "xx-note-row-title", 24))
    if subtitle:
        box.append(_label(Gtk, Pango, subtitle, "xx-note-caption", 30))
    return box


class SettingsPage:
    def __init__(self, Gtk, Pango, GLib, settings, on_settings_changed=None):  # type: ignore[no-untyped-def]
        self._Gtk = Gtk
        self._on_changed = on_settings_changed
        self._settings = settings
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.box.set_margin_bottom(120)

        self.box.append(_row(Gtk, Pango, "Notes folder",
                             "Plain Markdown files live here."))
        self.folder_entry = Gtk.Entry()
        _tap_to_focus(Gtk, self.folder_entry)
        self.folder_entry.set_text(settings.resolved_notes_dir().as_posix())
        self.folder_entry.set_margin_start(20)
        self.folder_entry.set_margin_end(20)
        self.folder_entry.set_max_width_chars(24)
        self.box.append(self.folder_entry)
        pick = Gtk.Button.new_with_label("Choose folder")
        pick.set_margin_start(20)
        pick.set_can_focus(False)
        pick.connect("clicked", self._pick_folder)
        self.box.append(pick)
        apply_btn = Gtk.Button.new_with_label("Use this folder")
        apply_btn.set_margin_start(20)
        apply_btn.set_can_focus(False)
        apply_btn.connect("clicked", self._apply_folder)
        self.box.append(apply_btn)

        self.box.append(_row(Gtk, Pango, "Note colors",
                             "Keep names round-trip in the file either way."))
        self.color_keep = Gtk.CheckButton.new_with_label("Keep pastels")
        self.color_tones = Gtk.CheckButton.new_with_label("Brand tones")
        self.color_tones.set_group(self.color_keep)
        self.color_keep.set_margin_start(20)
        self.color_tones.set_margin_start(20)
        self.color_keep.set_can_focus(False)
        self.color_tones.set_can_focus(False)
        if settings.color_mode == "tones":
            self.color_tones.set_active(True)
        else:
            self.color_keep.set_active(True)
        self.color_keep.connect("toggled", self._color_changed)
        self.box.append(self.color_keep)
        self.box.append(self.color_tones)

        self.box.append(_row(Gtk, Pango, "Sort order", ""))
        self.sort_combo = Gtk.DropDown.new_from_strings(
            ["Recently updated", "Recently created", "Title"]
        )
        self.sort_combo.set_margin_start(20)
        self.sort_combo.set_can_focus(False)
        self.sort_combo.set_selected(
            {"updated": 0, "created": 1, "title": 2}.get(settings.sort_order, 0)
        )
        self.sort_combo.connect("notify::selected", self._sort_changed)
        self.box.append(self.sort_combo)

        self.box.append(_row(
            Gtk, Pango, "Theme",
            "Follows the suite theme live. Change it in xx-wm settings."))
        self.status = _label(Gtk, Pango, "", "xx-note-caption", 30)
        self.status.set_margin_start(20)
        self.box.append(self.status)

    def _pick_folder(self, _button: object) -> None:
        try:
            dialog = self._Gtk.FileDialog()
            dialog.set_title("Notes folder")
            dialog.select_folder(
                None, None, lambda dlg, res: self._picked(dlg, res)
            )
        except Exception as exc:
            self.status.set_text(f"Picker unavailable: {exc}")

    def _picked(self, dialog: object, res: object) -> None:
        try:
            folder = dialog.select_folder_finish(res)
            path = folder.get_path()
        except Exception:
            return
        if path:
            self.folder_entry.set_text(path)

    def _apply_folder(self, _button: object) -> None:
        from xx_note import paths as paths_mod
        from xx_note import prefs as prefs_mod

        target = self.folder_entry.get_text().strip()
        if not target:
            self.status.set_text("Folder is empty.")
            return
        from pathlib import Path

        try:
            paths_mod.ensure_notes_tree(Path(target).expanduser())
        except OSError as exc:
            self.status.set_text(f"Cannot use that folder: {exc}")
            return
        self._settings.notes_dir = target
        prefs_mod.save(self._settings)
        self.status.set_text("Folder saved.")
        if self._on_changed is not None:
            self._on_changed()

    def _color_changed(self, _button: object) -> None:
        from xx_note import prefs as prefs_mod

        self._settings.color_mode = (
            "tones" if self.color_tones.get_active() else "keep"
        )
        prefs_mod.save(self._settings)
        if self._on_changed is not None:
            self._on_changed()

    def _sort_changed(self, _combo: object, _param: object) -> None:
        from xx_note import prefs as prefs_mod

        self._settings.sort_order = ("updated", "created", "title")[
            self.sort_combo.get_selected()
        ]
        prefs_mod.save(self._settings)
        if self._on_changed is not None:
            self._on_changed()


class SyncPage:
    def __init__(self, Gtk, Pango, GLib, settings, secrets, vault_getter):  # type: ignore[no-untyped-def]
        self._Gtk = Gtk
        self._GLib = GLib
        self._settings = settings
        self._secrets = secrets
        self._vault_getter = vault_getter
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.box.set_margin_bottom(120)

        self.box.append(_row(Gtk, Pango, "WebDAV sync",
                             "Optional. Off by default. Nextcloud and others."))
        self.enable = Gtk.Switch()
        self.enable.set_margin_start(20)
        self.enable.set_can_focus(False)
        self.enable.set_active(settings.webdav_enabled)
        self.enable.connect("notify::active", self._enabled_changed)
        self.box.append(self.enable)

        self.box.append(_row(Gtk, Pango, "Server", "https://host/remote.php/dav"))
        self.url_entry = Gtk.Entry()
        _tap_to_focus(Gtk, self.url_entry)
        self.url_entry.set_text(settings.webdav_url)
        self.url_entry.set_margin_start(20)
        self.url_entry.set_margin_end(20)
        self.url_entry.set_max_width_chars(24)
        self.box.append(self.url_entry)

        self.box.append(_row(Gtk, Pango, "User", ""))
        self.user_entry = Gtk.Entry()
        _tap_to_focus(Gtk, self.user_entry)
        self.user_entry.set_text(settings.webdav_user)
        self.user_entry.set_margin_start(20)
        self.user_entry.set_margin_end(20)
        self.box.append(self.user_entry)

        self.box.append(_row(Gtk, Pango, "Remote folder", ""))
        self.path_entry = Gtk.Entry()
        _tap_to_focus(Gtk, self.path_entry)
        self.path_entry.set_text(settings.webdav_path)
        self.path_entry.set_margin_start(20)
        self.path_entry.set_margin_end(20)
        self.box.append(self.path_entry)

        self.box.append(_row(Gtk, Pango, "Password",
                             "Kept in the keyring, never in settings."))
        self.pass_entry = Gtk.Entry()
        _tap_to_focus(Gtk, self.pass_entry)
        self.pass_entry.set_visibility(False)
        self.pass_entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        self.pass_entry.set_margin_start(20)
        self.pass_entry.set_margin_end(20)
        self.box.append(self.pass_entry)
        save_btn = Gtk.Button.new_with_label("Save password")
        save_btn.set_margin_start(20)
        save_btn.set_can_focus(False)
        save_btn.connect("clicked", self._save_password)
        self.box.append(save_btn)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions.set_margin_start(20)
        actions.set_margin_top(8)
        self.test_button = Gtk.Button.new_with_label("Test connection")
        self.test_button.set_can_focus(False)
        self.test_button.connect("clicked", lambda _b: self.test_connection())
        actions.append(self.test_button)
        self.sync_button = Gtk.Button.new_with_label("Sync now")
        self.sync_button.set_can_focus(False)
        self.sync_button.connect("clicked", lambda _b: self.sync_now())
        actions.append(self.sync_button)
        self.box.append(actions)

        self.status = _label(Gtk, Pango, self._status_text(),
                             "xx-note-caption", 30)
        self.status.set_margin_start(20)
        self.box.append(self.status)

    def _status_text(self) -> str:
        if not self._settings.webdav_enabled:
            return "Sync is off. Notes stay on this phone."
        last = self._settings.last_sync or "never synced"
        return f"Last sync: {last}"

    def _persist_fields(self) -> None:
        from xx_note import prefs as prefs_mod

        self._settings.webdav_url = self.url_entry.get_text().strip()
        self._settings.webdav_user = self.user_entry.get_text().strip()
        self._settings.webdav_path = self.path_entry.get_text().strip() or "Notes/"
        prefs_mod.save(self._settings)

    def _enabled_changed(self, _switch: object, _param: object) -> None:
        from xx_note import prefs as prefs_mod

        self._settings.webdav_enabled = self.enable.get_active()
        prefs_mod.save(self._settings)
        self.status.set_text(self._status_text())

    def _save_password(self, _button: object) -> None:
        self._persist_fields()
        password = self.pass_entry.get_text()
        if not password:
            self.status.set_text("Type the password first.")
            return
        host = self._host_of()
        if host is None:
            self.status.set_text("Set a valid server URL first.")
            return

        def work() -> None:
            self._secrets.set_password(
                host, self._settings.webdav_user, password
            )
            self._GLib.idle_add(self._note_saved)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()

    def _note_saved(self) -> bool:
        self.pass_entry.set_text("")
        self.status.set_text("Password saved in the keyring.")
        return False

    def _host_of(self) -> str | None:
        from urllib import parse as _parse

        try:
            host = _parse.urlparse(self._settings.webdav_url).hostname
        except ValueError:
            host = None
        return host

    def _client(self):  # type: ignore[no-untyped-def]
        from xx_note import webdav as webdav_mod

        host = self._host_of()
        if host is None:
            raise webdav_mod.WebDavError("bad server URL")
        password = self._secrets.get_password(host, self._settings.webdav_user)
        if not password:
            raise webdav_mod.WebDavError("no password saved yet")
        return webdav_mod.WebDavClient(
            self._settings.webdav_url, self._settings.webdav_user, password
        )

    def test_connection(self) -> None:
        self._persist_fields()
        self.status.set_text("Testing...")
        self.test_button.set_sensitive(False)

        def work() -> None:
            try:
                ok = self._client().check()
                self._GLib.idle_add(self._tested, ok, "")
            except Exception as exc:
                self._GLib.idle_add(self._tested, False, str(exc))

        thread = threading.Thread(target=work, daemon=True)
        thread.start()

    def _tested(self, ok: bool, problem: str) -> bool:
        self.test_button.set_sensitive(True)
        self.status.set_text("Connected." if ok else f"Failed: {problem}")
        return False

    def sync_now(self, on_done=None) -> None:  # type: ignore[no-untyped-def]
        self._persist_fields()
        if not self._settings.webdav_enabled:
            self.status.set_text("Turn sync on first.")
            return
        self.status.set_text("Syncing...")
        self.sync_button.set_sensitive(False)

        def work() -> None:
            try:
                from xx_note import prefs as prefs_mod
                from xx_note import sync_engine as engine_mod

                vault = self._vault_getter()
                result = engine_mod.reconcile(
                    vault, self._client(),
                    self._settings.webdav_path.strip() or "Notes/",
                )
                if result.last_sync:
                    self._settings.last_sync = result.last_sync
                    prefs_mod.save(self._settings)
                summary = (
                    f"Up {result.pushed}, down {result.pulled}, "
                    f"merged {result.merged}, forks {result.forked}."
                )
                if result.errors:
                    summary += f" {len(result.errors)} notes need attention."
                self._GLib.idle_add(self._synced, summary, on_done)
            except Exception as exc:
                self._GLib.idle_add(self._synced, f"Failed: {exc}", on_done)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()

    def _synced(self, summary: str, on_done) -> bool:  # type: ignore[no-untyped-def]
        self.sync_button.set_sensitive(True)
        self.status.set_text(summary)
        if on_done is not None:
            on_done()
        return False
