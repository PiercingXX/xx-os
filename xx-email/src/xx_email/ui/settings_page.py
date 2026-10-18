"""Settings page: every Android setting with a real effect here.

Ports ui/settings/SettingsScreen.kt as a NavigationView page of
SettingRows: accounts (add via the loopback OAuth flow, remove with
the snooze-restore warning, revoke), gestures, sending, sync,
notifications, appearance, privacy, logs, and JSON backup. Thin view
only — persistence and worker ops live behind hooks (main.py),
config saves are tiny atomic writes, and account/backup work runs
off the main loop. The phone theme comes from the shell (xx_ui), so
there is no per-app theme row.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, Gtk

from xx_email import config as config_mod
from xx_email import snooze as snooze_mod
from xx_ui import widgets

SWIPE_OPTIONS = ("ARCHIVE", "DELETE", "MARK_READ", "STAR", "SNOOZE")
UNDO_OPTIONS = ("5", "10", "20", "30")
SYNC_OPTIONS = ("15", "30", "60")


def swipe_label(action: str) -> str:
    return {"ARCHIVE": "Archive", "DELETE": "Delete",
            "MARK_READ": "Mark read", "STAR": "Star",
            "SNOOZE": "Snooze"}.get(action or "", action or "")


def account_subtitle(snoozed: int, queued: int) -> str:
    parts = []
    if snoozed:
        parts.append(f"{snoozed:d} snoozed")
    if queued:
        parts.append(f"{queued:d} queued")
    return ", ".join(parts) or "Signed in"


class SettingsPage(Gtk.Box):
    """Settings form; hooks own every worker-backed op."""

    def __init__(self, hooks, toast) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._hooks = hooks
        self._toast = toast
        self._stored = config_mod.load()
        self._ready = False

        self.append(widgets.compact_header(
            lambda: hooks.go_back(), "Settings"))

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER,
                            Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        inner.add_css_class("xx-page-body")
        inner.set_margin_bottom(20)
        scrolled.set_child(inner)
        self.append(scrolled)

        inner.append(widgets.section_label("Accounts"))
        self._accounts_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=0)
        inner.append(self._accounts_box)
        self._accounts_empty = widgets.label("", "xx-secondary")
        self._accounts_box.append(self._accounts_empty)
        inner.append(widgets.list_row(
            "Add account", "Sign in with Google in your browser",
            on_activate=lambda: self._hooks.add_account(), last=True))

        inner.append(widgets.section_label("Sending"))
        inner.append(self._option_row(
            "Undo window", "", self._stored.undo_seconds,
            UNDO_OPTIONS, lambda value: value + " s",
            lambda value: self._save_int("undo_seconds", value)))
        inner.append(self._switch_row(
            "Send and archive", "",
            bool(self._stored.send_and_archive), "send_and_archive",
            last=True))

        inner.append(widgets.section_label("Gestures"))
        inner.append(self._option_row(
            "Swipe right", "", self._stored.swipe_right,
            SWIPE_OPTIONS, swipe_label,
            lambda value: self._save_text("swipe_right", value)))
        inner.append(self._option_row(
            "Swipe left", "", self._stored.swipe_left,
            SWIPE_OPTIONS, swipe_label,
            lambda value: self._save_text("swipe_left", value),
            last=True))

        inner.append(widgets.section_label("Sync and notifications"))
        inner.append(self._option_row(
            "Sync interval", "", str(self._stored.sync_minutes),
            SYNC_OPTIONS, lambda value: value + " min",
            lambda value: self._save_int("sync_minutes", value)))
        inner.append(self._switch_row(
            "New-mail notifications", "",
            bool(self._stored.notifications_enabled),
            "notifications_enabled"))
        inner.append(self._switch_row(
            "Load remote images", "Off keeps tracking pixels dead",
            bool(self._stored.remote_images), "remote_images",
            last=True))

        inner.append(widgets.section_label("OAuth"))
        inner.append(widgets.caption("OAuth client ID"))
        client = widgets.text_entry("OAuth client ID")
        client.set_text(self._stored.client_id or "")
        client.connect("changed",
                       lambda row: self._save_text(
                           "client_id", row.get_text().strip()))
        inner.append(client)
        inner.append(widgets.field(
            "Last-used account", self._stored.last_account or "None yet"))

        inner.append(widgets.section_label("Privacy and data"))
        inner.append(widgets.list_row(
            "Revoke Google access", "Open your Google permissions",
            on_activate=lambda: self._hooks.open_permissions()))
        inner.append(widgets.list_row(
            "Logs", "On-device log, scrubbed before display",
            on_activate=lambda: self._hooks.open_logs()))
        inner.append(widgets.list_row(
            "Export backup",
            "Settings, snoozes and queued sends as JSON",
            on_activate=lambda: self._export_backup()))
        inner.append(widgets.list_row(
            "Import backup", "Validated before anything is written",
            on_activate=lambda: self._import_backup(), last=True))

        self._ready = True
        hooks.load_state(self._on_state)

    # -------------------------------------------------------------------- rows

    def _option_row(self, title: str, subtitle: str, current,
                    options: tuple, label_of, on_pick,
                    last: bool = False) -> Gtk.Box:
        shown = [label_of(option) for option in options]
        drop = Gtk.DropDown.new_from_strings(shown)
        try:
            selected = list(options).index(str(current))
        except ValueError:
            selected = 0
        drop.set_selected(selected)
        drop.set_valign(Gtk.Align.CENTER)
        drop.connect('notify::selected',
                     lambda combo, _p: self._on_option(
                         combo, options, on_pick))
        return widgets.setting_row(title, subtitle, drop, last=last)

    def _on_option(self, combo, options: tuple, on_pick) -> None:
        if not self._ready:
            return
        position = combo.get_selected()
        if 0 <= position < len(options):
            on_pick(options[position])

    def _switch_row(self, title: str, subtitle: str, active: bool,
                    key: str, last: bool = False) -> Gtk.Box:
        switch = widgets.make_switch(
            active, lambda on: self._save_flag(key, on))
        return widgets.setting_row(title, subtitle, switch, last=last)

    def _save_int(self, key: str, value: str) -> None:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return
        setattr(self._stored, key, number)
        self._persist()

    def _save_flag(self, key: str, value: bool) -> None:
        setattr(self._stored, key, bool(value))
        self._persist()

    def _save_text(self, key: str, value: str) -> None:
        if key in ("swipe_right", "swipe_left") and \
                value not in SWIPE_OPTIONS:
            return
        setattr(self._stored, key, value)
        self._persist()

    def _persist(self) -> None:
        try:
            config_mod.save(self._stored)
        except OSError:
            self._toast("Couldn't save settings", None)

    # ---------------------------------------------------------------- accounts

    def _on_state(self, result) -> None:
        if isinstance(result, Exception):
            self._accounts_empty.set_label("Accounts: unavailable")
            return
        _config, accounts = result
        child = self._accounts_box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._accounts_box.remove(child)
            child = next_child
        if not accounts:
            return
        for index, entry in enumerate(accounts):
            self._accounts_box.append(self._account_row(
                entry, last=index == len(accounts) - 1))

    def _account_row(self, entry: dict, last: bool = False) -> Gtk.Box:
        email = entry.get("email", "")
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        outer.add_css_class("xx-setting")
        if last:
            outer.add_css_class("last")
        outer.append(widgets.label(email, "xx-title"))
        outer.append(widgets.label(
            account_subtitle(int(entry.get("snoozed", 0)),
                             int(entry.get("queued", 0))),
            "xx-secondary"))
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                          spacing=4)
        buttons.set_halign(Gtk.Align.END)
        revoke = widgets.text_button("Revoke")
        revoke.set_tooltip_text("Revoke this account's Google grant")
        revoke.connect("clicked",
                       lambda _b, address=email:
                       self._confirm_revoke(address))
        buttons.append(revoke)
        remove = widgets.text_button("Remove")
        remove.connect("clicked",
                       lambda _b, address=email:
                       self._confirm_remove(address, entry))
        buttons.append(remove)
        outer.append(buttons)
        return outer

    def _confirm_remove(self, email: str, entry: dict) -> None:
        snoozed = int(entry.get("snoozed", 0))
        queued = int(entry.get("queued", 0))
        detail = (f"Remove {email} from this phone? Cached mail and "
                  "queued sends go with it; your Gmail data is "
                  "untouched.")
        if snoozed:
            detail += (f" {snoozed:d} snoozed "
                       f"{'thread is' if snoozed == 1 else 'threads are'} "
                       "moved back to the inbox on the server first.")
        if queued:
            detail += (f" {queued:d} queued "
                       f"{'send is' if queued == 1 else 'sends are'} "
                       "cancelled.")
        detail += " " + snooze_mod.UNINSTALL_WARNING
        dialog = Adw.AlertDialog()
        dialog.set_heading(f"Remove {email}?")
        dialog.set_body(detail)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance(
            "remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response",
                       lambda _d, answer: self._on_remove_answer(
                           email, answer))
        dialog.present(self.get_root())

    def _on_remove_answer(self, email: str, answer: str) -> None:
        if answer == "remove":
            self._hooks.remove_account(email)

    def _confirm_revoke(self, email: str) -> None:
        dialog = Adw.AlertDialog()
        dialog.set_heading(f"Revoke {email}?")
        dialog.set_body(
            "Google forgets this phone's access; the stored grant is "
            "deleted here too. Cached mail stays, and the next sync "
            "asks you to sign in again. Continue?")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("revoke", "Revoke")
        dialog.set_response_appearance(
            "revoke", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response",
                       lambda _d, answer: self._on_revoke_answer(
                           email, answer))
        dialog.present(self.get_root())

    def _on_revoke_answer(self, email: str, answer: str) -> None:
        if answer != "revoke":
            return
        self._toast("Revoking…", None)
        self._hooks.revoke_account(email, self._on_revoked)

    def _on_revoked(self, result) -> None:
        if isinstance(result, Exception):
            self._toast("Couldn't revoke access", None)
        else:
            self._toast(str(result) or "Access revoked", None)
        self._hooks.load_state(self._on_state)

    # ----------------------------------------------------------------- backup

    def _export_backup(self) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title("Save backup")
        dialog.set_initial_name("xx-email-backup.json")
        backup_filter = Gtk.FileFilter()
        backup_filter.set_name("JSON backup")
        backup_filter.add_suffix("json")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(backup_filter)
        dialog.set_filters(filters)
        try:
            dialog.save(self.get_root(), None, self._on_export_picked)
        except Exception:
            self._toast("Couldn't open the file picker", None)

    def _on_export_picked(self, dialog, result) -> None:
        try:
            target = dialog.save_finish(result)
        except Exception:
            return
        path = target.get_path() if isinstance(target, Gio.File) \
            else None
        if not path:
            self._toast("Couldn't use that file", None)
            return
        self._hooks.export_backup(path, self._on_exported)

    def _on_exported(self, result) -> None:
        if isinstance(result, Exception):
            self._toast(f"Backup failed: {result}", None)
        else:
            self._toast("Backup saved", None)

    def _import_backup(self) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title("Open backup")
        backup_filter = Gtk.FileFilter()
        backup_filter.set_name("JSON backup")
        backup_filter.add_suffix("json")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(backup_filter)
        dialog.set_filters(filters)
        try:
            dialog.open(self.get_root(), None, self._on_import_picked)
        except Exception:
            self._toast("Couldn't open the file picker", None)

    def _on_import_picked(self, dialog, result) -> None:
        try:
            target = dialog.open_finish(result)
        except Exception:
            return
        path = target.get_path() if isinstance(target, Gio.File) \
            else None
        if not path:
            self._toast("Couldn't use that file", None)
            return
        self._hooks.import_backup(path, self._on_imported)

    def _on_imported(self, result) -> None:
        if isinstance(result, Exception):
            self._toast(f"Import failed: {result}", None)
            return
        restored = (result or {}).get("restored", {})
        skipped = (result or {}).get("skipped", [])
        summary = (f"Restored {restored.get('wakes', 0):d} snoozes, "
                   f"{restored.get('pages', 0):d} cursors, "
                   f"{restored.get('outbox', 0):d} queued sends")
        if skipped:
            summary += f" ({len(skipped):d} skipped)"
        self._toast(summary, None)
        self._stored = config_mod.load()
        self._hooks.load_state(self._on_state)


def settings_navigation_page(page: SettingsPage) -> Adw.NavigationPage:
    navigation = Adw.NavigationPage(title="Settings")
    navigation.set_child(page)
    return navigation
