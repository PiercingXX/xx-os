"""Settings + Sync pages for xx-note.

Settings: notes folder (type a path and press Enter, or pick one), note
colors (Keep pastels or brand tones), sort order. Sync: one WebDAV switch;
its fields show only while it is on: server, user, remote folder and a
password that goes to libsecret (never config or logs) on Enter, Test or
Sync now. Test and Sync run on worker threads; one muted line reports
what came back. Nothing confirms a direct action.
"""

from __future__ import annotations

import threading

from xx_ui import widgets as xw

SORT_KEYS = ("updated", "created", "title")
SORT_LABELS = ("Edited", "Created", "Title")
COLOR_KEYS = ("keep", "tones")
COLOR_LABELS = ("Pastels", "Tones")


def compact_bar(Gtk, title: str):  # type: ignore[no-untyped-def]
    """Back chevron + caps title, the xx_ui compact header shape. Returns
    (bar, back_button) so the shell owns navigation."""
    bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    bar.add_css_class("xx-page-body")
    bar.add_css_class("xx-compact-header")
    back = xw.icon_button("go-previous-symbolic")
    back.set_can_focus(False)
    back.set_valign(Gtk.Align.CENTER)
    bar.append(back)
    label = xw.caption(title)
    label.set_hexpand(True)
    label.set_valign(Gtk.Align.CENTER)
    bar.append(label)
    return bar, back


def _field(Gtk, caption_text: str, child):  # type: ignore[no-untyped-def]
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    box.add_css_class("xx-note-field")
    box.append(xw.caption(caption_text))
    box.append(child)
    return box


def _entry(Gtk, text: str = "", placeholder: str = ""):  # type: ignore[no-untyped-def]
    entry = xw.deferred_entry(placeholder)
    entry.set_text(text)
    entry.set_hexpand(True)
    return entry


class _Segmented:
    """Joined caps choices (xx_ui segmented) whose selection can move."""

    def __init__(self, Gtk, labels, selected: int, on_pick) -> None:  # type: ignore[no-untyped-def]
        self.box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.box.add_css_class("xx-segmented")
        self.box.add_css_class("xx-note-seg")
        self.box.set_homogeneous(True)
        self._buttons = []
        self.selected = selected
        count = len(labels)
        for index, text in enumerate(labels):
            button = Gtk.Button.new_with_label(text.upper())
            button.set_can_focus(False)
            button.add_css_class(
                "seg-first" if index == 0
                else "seg-last" if index == count - 1 else "seg-mid"
            )
            button.connect("clicked", lambda _b, i=index: self._pick(i, on_pick))
            self.box.append(button)
            self._buttons.append(button)
        self._show()

    def _show(self) -> None:
        for index, button in enumerate(self._buttons):
            if index == self.selected:
                button.add_css_class("seg-selected")
            else:
                button.remove_css_class("seg-selected")

    def _pick(self, index: int, on_pick) -> None:  # type: ignore[no-untyped-def]
        if index == self.selected:
            return
        self.selected = index
        self._show()
        on_pick(index)


def _status_label(Gtk, Pango):  # type: ignore[no-untyped-def]
    label = Gtk.Label(label="")
    label.add_css_class("xx-note-status")
    label.set_xalign(0.0)
    label.set_wrap(True)
    label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    label.set_max_width_chars(24)
    label.set_visible(False)
    return label


def _say(label, text: str) -> None:  # type: ignore[no-untyped-def]
    label.set_text(text)
    label.set_visible(bool(text))


class SettingsPage:
    def __init__(self, Gtk, Pango, GLib, settings, on_settings_changed=None):  # type: ignore[no-untyped-def]
        self._Gtk = Gtk
        self._on_changed = on_settings_changed
        self._settings = settings
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.header, self.back_button = compact_bar(Gtk, "Settings")
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        body.add_css_class("xx-page-body")
        body.set_margin_bottom(24)

        folder_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.folder_entry = _entry(Gtk, settings.resolved_notes_dir().as_posix())
        self.folder_entry.connect("activate", self._apply_folder)
        folder_row.append(self.folder_entry)
        pick = xw.icon_button("folder-open-symbolic", None)
        pick.set_can_focus(False)
        pick.set_valign(Gtk.Align.CENTER)
        pick.set_tooltip_text("Choose folder")
        pick.connect("clicked", self._pick_folder)
        folder_row.append(pick)
        body.append(_field(Gtk, "Notes folder", folder_row))

        self.colors = _Segmented(
            Gtk, COLOR_LABELS,
            1 if settings.color_mode == "tones" else 0,
            self._color_changed,
        )
        body.append(_field(Gtk, "Note colors", self.colors.box))

        self.sort = _Segmented(
            Gtk, SORT_LABELS,
            {"updated": 0, "created": 1, "title": 2}.get(settings.sort_order, 0),
            self._sort_changed,
        )
        body.append(_field(Gtk, "Sort", self.sort.box))

        self.status = _status_label(Gtk, Pango)
        body.append(self.status)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(body)
        self.box.append(self.header)
        self.box.append(scroll)

    def _pick_folder(self, _button: object) -> None:
        try:
            dialog = self._Gtk.FileDialog()
            dialog.set_title("Notes folder")
            dialog.select_folder(
                None, None, lambda dlg, res: self._picked(dlg, res)
            )
        except Exception as exc:
            _say(self.status, f"Picker unavailable: {exc}")

    def _picked(self, dialog: object, res: object) -> None:
        try:
            folder = dialog.select_folder_finish(res)
            path = folder.get_path()
        except Exception:
            return
        if path:
            self.folder_entry.set_text(path)
            self._apply_folder(None)

    def _apply_folder(self, _widget: object) -> None:
        from pathlib import Path

        from xx_note import paths as paths_mod
        from xx_note import prefs as prefs_mod

        target = self.folder_entry.get_text().strip()
        if not target:
            _say(self.status, "Folder is empty.")
            return
        try:
            paths_mod.ensure_notes_tree(Path(target).expanduser())
        except OSError as exc:
            _say(self.status, f"Cannot use that folder: {exc}")
            return
        _say(self.status, "")
        self._settings.notes_dir = target
        prefs_mod.save(self._settings)
        if self._on_changed is not None:
            self._on_changed()

    def _color_changed(self, index: int) -> None:
        from xx_note import prefs as prefs_mod

        self._settings.color_mode = COLOR_KEYS[index]
        prefs_mod.save(self._settings)
        if self._on_changed is not None:
            self._on_changed()

    def _sort_changed(self, index: int) -> None:
        from xx_note import prefs as prefs_mod

        self._settings.sort_order = SORT_KEYS[index]
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
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.header, self.back_button = compact_bar(Gtk, "Sync")
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        body.add_css_class("xx-page-body")
        body.set_margin_bottom(24)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.add_css_class("xx-setting")
        row.add_css_class("last")
        middle = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        middle.set_hexpand(True)
        middle.set_valign(Gtk.Align.CENTER)
        middle.append(xw.label("WebDAV", "xx-title"))
        self.state_label = xw.label(self._state_text(), "xx-secondary")
        middle.append(self.state_label)
        row.append(middle)
        self.enable = xw.make_switch(settings.webdav_enabled)
        self.enable.set_can_focus(False)
        self.enable.connect("notify::active", self._enabled_changed)
        row.append(self.enable)
        body.append(row)

        self.fields = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.url_entry = _entry(Gtk, settings.webdav_url,
                                "https://host/remote.php/dav")
        self.fields.append(_field(Gtk, "Server", self.url_entry))
        self.user_entry = _entry(Gtk, settings.webdav_user)
        self.fields.append(_field(Gtk, "User", self.user_entry))
        self.path_entry = _entry(Gtk, settings.webdav_path, "Notes/")
        self.fields.append(_field(Gtk, "Remote folder", self.path_entry))
        self.pass_entry = _entry(Gtk)
        self.pass_entry.set_visibility(False)
        self.pass_entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        self.pass_entry.connect("activate", self._save_password)
        self.fields.append(_field(Gtk, "Password", self.pass_entry))

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions.add_css_class("xx-note-actions")
        actions.set_homogeneous(True)
        self.test_button = xw.outline_button("Test", lambda: self.test_connection())
        self.test_button.set_can_focus(False)
        actions.append(self.test_button)
        self.sync_button = xw.primary_button("Sync now", lambda: self.sync_now())
        self.sync_button.set_can_focus(False)
        actions.append(self.sync_button)
        self.fields.append(actions)
        self.fields.set_visible(settings.webdav_enabled)
        body.append(self.fields)

        self.status = _status_label(Gtk, Pango)
        body.append(self.status)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(body)
        self.box.append(self.header)
        self.box.append(scroll)

    def _state_text(self) -> str:
        if not self._settings.webdav_enabled:
            return "Off"
        if not self._settings.last_sync:
            return "Never synced"
        return f"Last sync {self._settings.last_sync}"

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
        self.fields.set_visible(self._settings.webdav_enabled)
        self.state_label.set_text(self._state_text())
        _say(self.status, "")

    def _typed_password(self) -> str:
        """Take the typed password off the screen; the worker stores it."""
        password = self.pass_entry.get_text()
        if password:
            self.pass_entry.set_text("")
            self.pass_entry.set_placeholder_text("Saved in keyring")
        return password

    def _store(self, password: str) -> None:
        """Worker side: save a freshly typed password in the keyring."""
        host = self._host_of()
        if password and host is not None:
            self._secrets.set_password(host, self._settings.webdav_user, password)

    def _save_password(self, _entry: object) -> None:
        self._persist_fields()
        if self._host_of() is None:
            _say(self.status, "Set a valid server URL first.")
            return
        password = self._typed_password()
        if not password:
            return
        thread = threading.Thread(target=self._store, args=(password,), daemon=True)
        thread.start()

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
        password = self._typed_password()
        _say(self.status, "Testing...")
        self.test_button.set_sensitive(False)

        def work() -> None:
            try:
                self._store(password)
                ok = self._client().check()
                self._GLib.idle_add(self._tested, ok, "")
            except Exception as exc:
                self._GLib.idle_add(self._tested, False, str(exc))

        thread = threading.Thread(target=work, daemon=True)
        thread.start()

    def _tested(self, ok: bool, problem: str) -> bool:
        self.test_button.set_sensitive(True)
        _say(self.status, "Connected." if ok else f"Failed: {problem}")
        return False

    def sync_now(self, on_done=None) -> None:  # type: ignore[no-untyped-def]
        self._persist_fields()
        if not self._settings.webdav_enabled:
            _say(self.status, "Turn sync on first.")
            return
        password = self._typed_password()
        _say(self.status, "Syncing...")
        self.sync_button.set_sensitive(False)

        def work() -> None:
            try:
                from xx_note import prefs as prefs_mod
                from xx_note import sync_engine as engine_mod

                self._store(password)
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
        self.state_label.set_text(self._state_text())
        _say(self.status, summary)
        if on_done is not None:
            on_done()
        return False
