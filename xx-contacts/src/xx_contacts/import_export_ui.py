"""L5 import/export page pushed on the list's ``Adw.NavigationView``.

Phone-first 360x720 (min 320x200): a scrolled stack with an import
group (vCard file, Android backup, one-time GNOME Contacts import),
an export group (whole address book through the portal file chooser),
and the optional one-way EDS publish switch (off by default). Every
write goes through the contacts service: the page dry-runs
``import_export.plan_import`` against records it reads directly,
shows the counts in a confirm dialog, and then performs exactly one
``Import`` call, so a 2,000-contact file costs one transaction and
one ``Changed`` signal.

Import-safe without a display: constructing the page needs a display,
but importing this module must not. The page holds no text entries
and never grabs focus, so opening it never pops the on-screen keyboard.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")

import os  # noqa: E402
import threading  # noqa: E402

from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from xx_ui import widgets  # noqa: E402
from xx_contacts import eds as eds_module  # noqa: E402
from xx_contacts import import_export as import_export_module  # noqa: E402
from xx_contacts import privacy  # noqa: E402
from xx_contacts.settings_ui import setting_row  # noqa: E402
from xx_contacts.store import NotFoundError  # noqa: E402

#: Largest vCard/groups file the pickers will read (a 2,000-contact
#: export is ~1 MB; this leaves wide headroom without risking RAM).
VCF_READ_LIMIT = 32 * 1024 * 1024


def vcard_filter_store() -> Gio.ListStore:
    """A ``Gtk.FileDialog`` filter list accepting ``.vcf`` files."""
    contacts = Gtk.FileFilter()
    contacts.set_name("vCard contacts")
    contacts.add_mime_type("text/vcard")
    contacts.add_pattern("*.vcf")
    contacts.add_pattern("*.vcard")
    store = Gio.ListStore.new(Gtk.FileFilter)
    store.append(contacts)
    return store


def json_filter_store() -> Gio.ListStore:
    groups = Gtk.FileFilter()
    groups.set_name("Groups JSON")
    groups.add_mime_type("application/json")
    groups.add_pattern("*.json")
    store = Gio.ListStore.new(Gtk.FileFilter)
    store.append(groups)
    return store


class ImportExportPage(Adw.NavigationPage):
    """Import, export and EDS mirror controls for the address book."""

    def __init__(self, navigation: Adw.NavigationView,
                 parent: Gtk.Window, store, config, config_path: str,
                 default_region: str = "US") -> None:
        super().__init__()
        self._navigation = navigation
        self._parent = parent
        self._store = store
        self._config = config
        self._config_path = config_path
        self._region = ((getattr(config, "default_region", None)
                         or default_region or "US").upper())
        self._alive = True
        self._pending_vcf = ""
        self._pending_backup: list = []
        self._pending_groups_applied = 0
        self._publisher = eds_module.EdsPublisher(
            eds_module.EBookClient, store.all_records,
            enabled=bool(getattr(config, "publish_to_eds", False)))

        header = widgets.compact_header(
            self._on_back_clicked, "Import/export")

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        body.add_css_class("xx-page-body")
        # Short bodies must pack at the top: a stretched viewport leaves
        # phantom gaps between the first children (seen on the Me card).
        body.set_valign(Gtk.Align.START)

        body.append(widgets.section_label("Import"))
        self._import_vcf_button = widgets.outline_button(
            "Import vCard file", self._on_pick_vcard)
        widgets.wrap_to_fit(self._import_vcf_button)
        body.append(self._import_vcf_button)
        self._import_backup_button = widgets.outline_button(
            "Import Android backup", self._on_pick_backup_vcf)
        widgets.wrap_to_fit(self._import_backup_button)
        body.append(self._import_backup_button)
        self._import_eds_button = widgets.outline_button(
            "Import from GNOME Contacts", self._on_import_eds)
        widgets.wrap_to_fit(self._import_eds_button)
        self._import_eds_button.set_sensitive(eds_module.is_available())
        body.append(self._import_eds_button)

        body.append(widgets.section_label("Export"))
        self._export_button = widgets.outline_button(
            "Export all contacts", self._on_export)
        widgets.wrap_to_fit(self._export_button)
        body.append(self._export_button)

        body.append(widgets.section_label("GNOME Contacts mirror"))
        self._publish_switch = widgets.make_switch(
            bool(getattr(config, "publish_to_eds", False)),
            lambda _active: self._on_publish_flipped())
        self._publish_switch.set_sensitive(eds_module.is_available())
        publish_row = setting_row(
            "Publish to GNOME Contacts",
            "One-way copy to GNOME Contacts",
            self._publish_switch, last=True)
        body.append(publish_row)

        self._status = widgets.label("", "xx-secondary")
        self._status.set_wrap(True)
        self._status.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        body.append(self._status)

        self.set_child(widgets.page(header, body))
        self.set_title("Import/export")
        self.connect("unrealize", self._on_unrealize)

    def _on_back_clicked(self) -> None:
        self._navigation.pop()

    def _on_publish_flipped(self) -> None:
        self._on_publish_toggled(self._publish_switch, None)

    # ------------------------------------------------------------------ pick

    def import_file(self, path: str) -> None:
        """Start the confirm flow for a ``.vcf`` opened from Files/CLI.

        Same worker, plan and confirm dialog as the in-app picker: the
        call only schedules the read in a worker thread.
        """
        if not self._alive or not path:
            return
        worker = threading.Thread(target=self._read_vcf_in_worker,
                                  args=(path, False), daemon=True)
        worker.start()

    def _on_pick_vcard(self) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title("Import vCard file")
        dialog.set_filters(vcard_filter_store())
        dialog.open(self._parent, None, self._on_vcard_chosen)

    def _on_vcard_chosen(self, dialog: Gtk.FileDialog,
                         result: Gio.AsyncResult) -> None:
        if not self._alive:
            return
        try:
            target = dialog.open_finish(result)
        except GLib.Error:
            return
        path = target.get_path() if target is not None else None
        if not path:
            return
        worker = threading.Thread(target=self._read_vcf_in_worker,
                                  args=(path, False), daemon=True)
        worker.start()

    def _on_pick_backup_vcf(self) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title("Import Android contacts.vcf")
        dialog.set_filters(vcard_filter_store())
        dialog.open(self._parent, None, self._on_backup_vcf_chosen)

    def _on_backup_vcf_chosen(self, dialog: Gtk.FileDialog,
                              result: Gio.AsyncResult) -> None:
        if not self._alive:
            return
        try:
            target = dialog.open_finish(result)
        except GLib.Error:
            return
        path = target.get_path() if target is not None else None
        if not path:
            return
        worker = threading.Thread(target=self._read_vcf_in_worker,
                                  args=(path, True), daemon=True)
        worker.start()

    def _read_vcf_in_worker(self, path: str, is_backup: bool) -> None:
        try:
            if os.path.getsize(path) > VCF_READ_LIMIT:
                raise OSError("file is too large")
            with open(path, encoding="utf-8") as handle:
                text = handle.read(VCF_READ_LIMIT + 1)
        except OSError:
            text = ""
        GLib.idle_add(self._plan_picked_file, text, is_backup)

    def _plan_picked_file(self, text: str, is_backup: bool) -> bool:
        if not self._alive:
            return False
        if not text:
            self._announce("Import failed",
                            "Could not read that file.")
            return False
        try:
            incoming = import_export_module.parse_text(text, self._region)
        except ValueError:
            incoming = []
        if not incoming:
            self._announce("Import failed",
                            "No vCard blocks found in that file.")
            return False
        if is_backup:
            self._pending_backup = incoming
            self._ask_backup_groups()
            return False
        try:
            plan = import_export_module.plan_import(
                self._store.all_records(), incoming, self._region)
        except (NotFoundError, OSError):
            self._announce("Import failed",
                            "Could not read the address book.")
            return False
        self._pending_vcf = text
        self._pending_groups_applied = 0
        self._confirm_import(plan)
        return False

    # -------------------------------------------------------- backup groups

    def _ask_backup_groups(self) -> None:
        dialog = Adw.AlertDialog.new(
            "Android backup",
            "contacts.vcf parsed. Add the matching groups.json "
            "memberships before importing?")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("without", "Import without groups")
        dialog.add_response("with", "Choose groups.json")
        dialog.set_default_response("without")
        dialog.set_close_response("cancel")
        dialog.choose(self._parent, None, self._on_backup_groups_answer)

    def _on_backup_groups_answer(self, dialog: Adw.AlertDialog,
                                 result: Gio.AsyncResult) -> None:
        if not self._alive:
            return
        try:
            response = dialog.choose_finish(result)
        except GLib.Error:
            return
        if response == "with":
            picker = Gtk.FileDialog()
            picker.set_title("Choose groups.json")
            picker.set_filters(json_filter_store())
            picker.open(self._parent, None, self._on_groups_chosen)
        elif response == "without":
            self._confirm_backup_now(0)

    def _on_groups_chosen(self, dialog: Gtk.FileDialog,
                          result: Gio.AsyncResult) -> None:
        if not self._alive:
            return
        try:
            target = dialog.open_finish(result)
        except GLib.Error:
            self._confirm_backup_now(0)
            return
        path = target.get_path() if target is not None else None
        if not path:
            self._confirm_backup_now(0)
            return
        worker = threading.Thread(target=self._read_groups_in_worker,
                                  args=(path,), daemon=True)
        worker.start()

    def _read_groups_in_worker(self, path: str) -> None:
        try:
            if os.path.getsize(path) > VCF_READ_LIMIT:
                raise OSError("file is too large")
            with open(path, encoding="utf-8") as handle:
                text = handle.read(VCF_READ_LIMIT + 1)
        except OSError:
            text = ""
        GLib.idle_add(self._fold_backup_groups, text)

    def _fold_backup_groups(self, text: str) -> bool:
        if not self._alive:
            return False
        applied = 0
        if text:
            try:
                applied, _unmapped = import_export_module.fold_groups_json(
                    self._pending_backup, text)
            except ValueError:
                self._announce("Import failed",
                                "groups.json is not valid.")
                return False
        self._confirm_backup_now(applied)
        return False

    def _confirm_backup_now(self, groups_applied: int) -> None:
        try:
            plan = import_export_module.plan_import(
                self._store.all_records(), self._pending_backup,
                self._region)
        except (NotFoundError, OSError):
            self._announce("Import failed",
                            "Could not read the address book.")
            return
        self._pending_groups_applied = groups_applied
        self._pending_vcf = ""
        self._confirm_import(plan)

    # ---------------------------------------------------------------- confirm

    def _confirm_import(self, plan) -> None:
        dialog = Adw.AlertDialog.new(
            "Import contacts?",
            import_export_module.summarize(
                plan, self._pending_groups_applied))
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("import", "Import")
        dialog.set_response_appearance(
            "import", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.choose(self._parent, None, self._on_import_answer)

    def _on_import_answer(self, dialog: Adw.AlertDialog,
                          result: Gio.AsyncResult) -> None:
        if not self._alive:
            return
        try:
            response = dialog.choose_finish(result)
        except GLib.Error:
            return
        if response != "import":
            return
        if self._pending_vcf:
            self._run_service_import(self._pending_vcf)
        else:
            self._run_backup_import()

    def _run_service_import(self, vcf_text: str) -> None:
        self._set_status("Importing…")
        worker = threading.Thread(target=self._import_in_worker,
                                  args=(vcf_text,), daemon=True)
        worker.start()

    def _run_backup_import(self) -> None:
        self._set_status("Importing…")
        backup = list(self._pending_backup)
        worker = threading.Thread(target=self._import_backup_in_worker,
                                  args=(backup,), daemon=True)
        worker.start()

    def _import_in_worker(self, vcf_text: str) -> None:
        outcome = self._call_service_import(vcf_text)
        GLib.idle_add(self._announce_import_outcome, outcome)

    def _import_backup_in_worker(self, backup: list) -> None:
        try:
            outcome = self._call_service_import(
                import_export_module.backup_to_text(backup))
        except (NotFoundError, ValueError, OSError) as exc:
            outcome = f"Import failed: {exc}"
        GLib.idle_add(self._announce_import_outcome, outcome)

    def _call_service_import(self, vcf_text: str) -> str:
        from xx_contacts import service_client as service_client_module

        try:
            connection = service_client_module.gui_connection()
            created, updated, skipped = service_client_module.import_sync(
                connection, vcf_text)
        except (NotFoundError, RuntimeError, ValueError,
                GLib.Error) as exc:
            return f"Import failed: {exc}"
        return (f"Imported {created} new, {updated} updated, "
                f"{skipped} skipped.")

    def _announce_import_outcome(self, outcome: str) -> bool:
        if not self._alive:
            return False
        self._pending_vcf = ""
        self._pending_backup = []
        self._set_status(outcome)
        return False

    # ------------------------------------------------------------------- eds

    def _on_import_eds(self) -> None:
        self._set_status("Reading GNOME Contacts…")
        worker = threading.Thread(target=self._import_eds_in_worker,
                                  daemon=True)
        worker.start()

    def _import_eds_in_worker(self) -> None:
        try:
            client = eds_module.EBookClient()
            incoming = eds_module.read_import_cards(client, self._region)
        except RuntimeError as exc:
            GLib.idle_add(self._announce, "Import failed", str(exc))
            return
        GLib.idle_add(self._plan_eds_cards, incoming)

    def _plan_eds_cards(self, incoming: list) -> bool:
        if not self._alive:
            return False
        if not incoming:
            self._announce("Nothing to import",
                            "GNOME Contacts holds no contacts.")
            return False
        self._pending_backup = incoming
        self._confirm_backup_now(0)
        return False

    def _on_publish_toggled(self, switch: Gtk.Switch, _pspec) -> None:
        active = switch.get_active()
        self._config.publish_to_eds = active
        try:
            self._config.save(self._config_path)
        except OSError:
            pass
        self._publisher.enabled = active
        if active:
            self._publisher.schedule()

    # ----------------------------------------------------------------- export

    def _on_export(self) -> None:
        dialog = Gtk.FileDialog()
        dialog.set_title("Export contacts")
        dialog.set_initial_name("contacts.vcf")
        dialog.set_filters(vcard_filter_store())
        dialog.save(self._parent, None, self._on_export_chosen)

    def _on_export_chosen(self, dialog: Gtk.FileDialog,
                          result: Gio.AsyncResult) -> None:
        if not self._alive:
            return
        try:
            target = dialog.save_finish(result)
        except GLib.Error:
            return
        if target is None:
            return
        path = target.get_path()
        if path:
            self._set_status("Exporting…")
            worker = threading.Thread(target=self._export_in_worker,
                                      args=(path,), daemon=True)
            worker.start()
        else:
            self._fallback_export()

    def _export_in_worker(self, path: str) -> None:
        try:
            records = self._store.all_records()
            text = import_export_module.export_text(records)
            privacy.secure_write_text(path, text)
            outcome = import_export_module.exported_line(len(records), path)
        except OSError as exc:
            outcome = f"Export failed: {exc}"
        GLib.idle_add(self._announce_export_outcome, outcome)

    def _fallback_export(self) -> None:
        from xx_contacts.store import default_exports_dir

        try:
            exports = privacy.ensure_private_dir(
                default_exports_dir(os.path.expanduser("~")))
            path = os.path.join(exports, "contacts.vcf")
            records = self._store.all_records()
            privacy.secure_write_text(
                path, import_export_module.export_text(records))
            self._set_status(
                import_export_module.exported_line(len(records), path))
        except OSError as exc:
            self._announce("Export failed", str(exc))

    def _announce_export_outcome(self, outcome: str) -> bool:
        if not self._alive:
            return False
        self._set_status(outcome)
        return False

    # ------------------------------------------------------------------- misc

    def _set_status(self, text: str) -> None:
        self._status.set_text(text)

    def _announce(self, heading: str, body: str) -> bool:
        if not self._alive:
            return False
        self._set_status(f"{heading}: {body}")
        dialog = Adw.AlertDialog.new(heading, body)
        dialog.add_response("ok", "OK")
        dialog.choose(self._parent, None, self._on_dialog_dismissed)
        return False

    def _on_dialog_dismissed(self, dialog: Adw.AlertDialog,
                             result: Gio.AsyncResult) -> None:
        try:
            dialog.choose_finish(result)
        except GLib.Error:
            pass

    def _on_unrealize(self, _page: Adw.NavigationPage) -> None:
        self._alive = False
