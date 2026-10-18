"""L4 Me card page pushed on the list's ``Adw.NavigationView``.

Renders the designated Me contact (avatar, name, numbers, share as
vCard) or an empty state explaining how to designate one. Designation
itself happens here (remove) and on the detail page (set); sharing
embeds the normalized photo bytes as a vCard 4.0 data-URI PHOTO line.

Import-safe without a display: constructing the page needs a display,
but importing this module must not. The page holds no text entries and
never grabs focus, so opening it never pops the on-screen keyboard.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")

import os  # noqa: E402
import threading  # noqa: E402

from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from xx_ui import widgets  # noqa: E402
import xx_phone  # noqa: E402
from xx_contacts import avatar as avatar_module  # noqa: E402
from xx_contacts import detail as detail_model  # noqa: E402
from xx_contacts import mecard as mecard_module  # noqa: E402
from xx_contacts import photos as photos_module  # noqa: E402
from xx_contacts import vcard as vcard_module  # noqa: E402
from xx_contacts.detail_ui import wrap_value  # noqa: E402
from xx_contacts.list_ui import ChangeWatcher, launch_uri  # noqa: E402
from xx_contacts.store import default_exports_dir  # noqa: E402
from xx_contacts import privacy  # noqa: E402

ME_PHOTO_PX = 64


class MeCardPage(Adw.NavigationPage):
    """The user's own card, or the designate-Me empty state."""

    def __init__(self, navigation: Adw.NavigationView,
                 parent: Gtk.Window, store, config, config_path: str,
                 db_path: str, photos_dir: str,
                 default_region: str = "US", on_edit=None) -> None:
        super().__init__()
        self._navigation = navigation
        self._parent = parent
        self._store = store
        self._config = config
        self._config_path = config_path
        self._db_path = db_path
        self._photos_dir = photos_dir
        self._region = ((getattr(config, "default_region", None)
                         or default_region or "US").upper())
        self._on_edit = on_edit
        self._alive = True
        self._me_uid = ""
        self._watcher = ChangeWatcher(db_path, self._on_external_changed)

        header = widgets.compact_header(
            self._on_back_clicked, "Me card")

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        body.add_css_class("xx-page-body")
        # Short bodies must pack at the top: a stretched viewport leaves
        # phantom gaps between the first children.
        body.set_valign(Gtk.Align.START)

        avatar_box, avatar_image, avatar_fallback = (
            avatar_module.build_avatar(ME_PHOTO_PX))
        body.append(avatar_box)
        self._avatar_box = avatar_box
        self._avatar_image = avatar_image
        self._avatar_fallback = avatar_fallback
        self._name = widgets.label("", "xx-display-large")
        self._name.set_ellipsize(Pango.EllipsizeMode.NONE)
        self._name.set_wrap(True)
        self._name.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._name.set_max_width_chars(16)
        body.append(self._name)

        # The same rhythm as a contact's Details: 20 between fields, 6
        # between a caption and its value.
        self._sections = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                 spacing=20)
        body.append(self._sections)

        self._empty = widgets.label("", "xx-secondary")
        self._empty.set_wrap(True)
        self._empty.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        body.append(self._empty)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self._share_button = widgets.outline_button(
            "Share vCard", self._on_share_clicked)
        widgets.wrap_to_fit(self._share_button)
        self._share_button.set_hexpand(True)
        actions.append(self._share_button)
        self._edit_button = widgets.outline_button(
            "Edit", self._on_edit_clicked)
        widgets.wrap_to_fit(self._edit_button)
        self._edit_button.set_hexpand(True)
        actions.append(self._edit_button)
        body.append(actions)
        self._remove_button = widgets.text_button(
            "Remove Me", self._on_remove_clicked)
        remove_label = self._remove_button.get_child()
        if isinstance(remove_label, Gtk.Label):
            remove_label.set_max_width_chars(16)
        self._remove_button.add_css_class("xx-danger-text")
        self._remove_button.set_halign(Gtk.Align.CENTER)
        body.append(self._remove_button)

        self.set_child(widgets.page(header, body))
        self.set_title("Me")
        self.connect("unrealize", self._on_unrealize)
        self._watcher.start()
        self._schedule_load()

    def _on_back_clicked(self) -> None:
        self._navigation.pop()

    # ------------------------------------------------------------------- load

    def _schedule_load(self) -> None:
        worker = threading.Thread(target=self._load_in_worker, daemon=True)
        worker.start()

    def _load_in_worker(self) -> None:
        record = mecard_module.resolve_me(self._store, self._config)
        GLib.idle_add(self._apply_record, record)

    def _apply_record(self, record) -> bool:
        if not self._alive:
            return False
        if record is None:
            self._me_uid = ""
            self._name.set_text("No Me card yet")
            avatar_module.set_avatar(
                self._avatar_image, self._avatar_fallback, "", None,
                False, ME_PHOTO_PX)
            self._empty.set_text("Open a contact and choose Set as Me.")
            self._empty.set_visible(True)
            self._share_button.set_sensitive(False)
            self._edit_button.set_sensitive(False)
            self._remove_button.set_sensitive(False)
            child = self._sections.get_first_child()
            while child is not None:
                following = child.get_next_sibling()
                self._sections.remove(child)
                child = following
            return False
        self._me_uid = record.uid
        title = detail_model.detail_title(record)
        shown = xx_phone.display(title, xx_phone.display_country(
            self._region))
        self._name.set_text(shown)
        self._empty.set_visible(False)
        avatar_module.set_avatar(
            self._avatar_image, self._avatar_fallback, title,
            record.photo_path or None,
            bool(getattr(self._config, "show_photos", False)),
            ME_PHOTO_PX)
        child = self._sections.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._sections.remove(child)
            child = following
        for section in detail_model.build_detail_sections(record,
                                                          self._region):
            for item in section.fields:
                caption = item.label or section.title
                field_box = widgets.field(caption, item.display)
                field_box.set_spacing(6)
                wrap_value(field_box.get_last_child())
                self._sections.append(field_box)
        self._share_button.set_sensitive(True)
        self._edit_button.set_sensitive(True)
        self._remove_button.set_sensitive(True)
        return False

    def _on_external_changed(self, revision: int | None = None,
                             uids: list[str] | None = None) -> None:
        if not self._alive:
            return
        if uids is not None and self._me_uid and self._me_uid not in uids:
            return
        GLib.idle_add(self._reload_from_idle)

    def _reload_from_idle(self) -> bool:
        if self._alive:
            self._schedule_load()
        return False

    # ----------------------------------------------------------------- actions

    def _on_edit_clicked(self) -> None:
        if not self._me_uid:
            return
        from xx_contacts.editor_ui import ContactEditorPage

        page = ContactEditorPage(
            self._navigation, self._parent, self._store, self._me_uid,
            self._db_path, self._photos_dir, config=self._config,
            config_path=self._config_path, default_region=self._region)
        self._navigation.push(page)
        if self._on_edit is not None:
            try:
                self._on_edit(self._me_uid)
            except Exception:
                pass

    def _on_remove_clicked(self) -> None:
        dialog = Adw.AlertDialog.new(
            "Remove Me card?",
            "This contact stays in your address book; it just won't be "
            "your Me card anymore.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance(
            "remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.choose(self._parent, None, self._on_remove_chosen)

    def _on_remove_chosen(self, dialog: Adw.AlertDialog,
                          result: Gio.AsyncResult) -> None:
        try:
            response = dialog.choose_finish(result)
        except GLib.Error:
            return
        if response != "remove":
            return
        mecard_module.clear_me_uid(self._config, self._config_path)
        self._schedule_load()

    def _on_share_clicked(self) -> None:
        worker = threading.Thread(target=self._share_in_worker, daemon=True)
        worker.start()

    def _share_in_worker(self) -> None:
        record = mecard_module.resolve_me(self._store, self._config)
        if record is None:
            GLib.idle_add(self._announce_saved, "")
            return
        photo = photos_module.read_photo_bytes(record.photo_path or "")
        card = vcard_module.contact_to_vcard(record, photo_bytes=photo)
        GLib.idle_add(self._portal_save, card,
                      detail_model.detail_title(record))

    def _portal_save(self, card: str, name: str) -> bool:
        if not self._alive:
            return False
        if not card:
            self._message("Share failed",
                          "There is no Me card to share yet.")
            return False
        dialog = Gtk.FileDialog()
        dialog.set_initial_name(f"{name or 'me'}.vcf")
        dialog.save(self._parent, None, self._on_save_finished, card)
        return False

    def _on_save_finished(self, dialog: Gtk.FileDialog,
                          result: Gio.AsyncResult, card: str) -> None:
        try:
            target = dialog.save_finish(result)
        except GLib.Error:
            return
        path = target.get_path() if target is not None else None
        if path:
            worker = threading.Thread(target=self._write_saved_vcard,
                                      args=(path, card,), daemon=True)
            worker.start()
        else:
            self._fallback_save(card)

    def _write_saved_vcard(self, path: str, card: str) -> None:
        try:
            privacy.secure_write_text(path, card)
        except OSError:
            path = ""
        GLib.idle_add(self._announce_saved, path)

    def _fallback_save(self, card: str) -> None:
        try:
            exports = privacy.ensure_private_dir(
                default_exports_dir(os.path.expanduser("~")))
            path = os.path.join(exports, "me.vcf")
            privacy.secure_write_text(path, card)
        except OSError:
            path = ""
        self._announce_saved(path)

    def _announce_saved(self, path: str) -> bool:
        if not self._alive:
            return False
        if not path:
            self._message("Share failed", "Could not write the vCard file.")
            return False
        dialog = Adw.AlertDialog.new("vCard saved", path)
        dialog.add_response("close", "Close")
        dialog.add_response("open", "Open")
        dialog.set_default_response("close")
        dialog.set_close_response("close")
        dialog.choose(self._parent, None,
                      lambda dlg, res: self._on_saved_opened(dlg, res,
                                                             path))
        return False

    def _on_saved_opened(self, dialog: Adw.AlertDialog,
                         result: Gio.AsyncResult, path: str) -> None:
        try:
            response = dialog.choose_finish(result)
        except GLib.Error:
            return
        if response == "open":
            launch_uri(self._parent,
                       Gio.File.new_for_path(path).get_uri())

    # -------------------------------------------------------------------- misc

    def _message(self, heading: str, body: str) -> None:
        dialog = Adw.AlertDialog.new(heading, body)
        dialog.add_response("ok", "OK")
        dialog.choose(self._parent, None, self._on_dialog_dismissed)

    def _on_dialog_dismissed(self, dialog: Adw.AlertDialog,
                             result: Gio.AsyncResult) -> None:
        try:
            dialog.choose_finish(result)
        except GLib.Error:
            pass

    def _on_unrealize(self, _page: Adw.NavigationPage) -> None:
        self._alive = False
        try:
            self._watcher.stop()
        except Exception:
            pass
