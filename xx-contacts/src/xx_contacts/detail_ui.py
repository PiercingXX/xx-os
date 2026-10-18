"""L3 contact detail page pushed on the list's ``Adw.NavigationView``.

Phone-first 360x720: a compact back header with an edit action, a big
avatar and display name, status chips, Call/Message actions, captioned
fields from ``detail``, tag switches that write through the store
(list + detail refresh off the ``Changed`` watcher), portal vCard
share, and delete with confirmation. Call history stays hidden until
xx-dialer exposes it (see ``detail``'s seam).

Import-safe without a display: constructing the page needs a display, but
importing this module must not. The page holds no text entries and never
grabs focus, so opening it never pops the on-screen keyboard.
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
from xx_contacts import privacy  # noqa: E402
from xx_contacts import vcard as vcard_module  # noqa: E402
from xx_contacts.list_ui import ChangeWatcher, launch_uri  # noqa: E402
from xx_contacts.store import NotFoundError, default_exports_dir  # noqa: E402

DETAIL_PHOTO_PX = 88

ACTION_LABELS = {
    "call": "Call",
    "message": "Message",
    "email": "Email",
    "map": "Map",
    "open": "Open",
}

_TAG_SUBTITLES = {
    "starred": "Always rings",
    "business": "Work contact",
    "blocked": "Declines calls",
}

_TAG_TITLES = {
    "starred": "Starred",
    "business": "Business",
    "blocked": "Blocked",
}


def wrap_value(value: Gtk.Widget) -> None:
    """A field value (an email, a long number) wraps at large text and
    320 px instead of ellipsizing; it never widens the window."""
    if isinstance(value, Gtk.Label):
        value.set_wrap(True)
        value.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        value.set_ellipsize(Pango.EllipsizeMode.NONE)


class ContactDetailPage(Adw.NavigationPage):
    """Detail for one contact ``uid``; pops itself after delete."""

    def __init__(self, navigation: Adw.NavigationView,
                 parent: Gtk.Window, store, uid: str, db_path: str,
                 on_deleted=None, default_region: str = "US", config=None,
                 config_path: str = "", photos_dir: str = "") -> None:
        super().__init__()
        self._navigation = navigation
        self._parent = parent
        self._store = store
        self._uid = uid
        self._db_path = db_path
        self._on_deleted = on_deleted
        self._config = config
        self._config_path = config_path
        self._photos_dir = photos_dir
        self._region = ((getattr(config, "default_region", None)
                         or default_region or "US").upper())
        self._alive = True
        self._switches: dict[str, Gtk.Switch] = {}
        self._updating_switches = False
        self._watcher = ChangeWatcher(db_path, self._on_external_changed)

        # One way to edit: the Edit button by Share vCard (as on Android).
        header = widgets.compact_header(
            self._on_back_clicked, "Details", "")

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.add_css_class("xx-page-body")
        # Short bodies must pack at the top: a stretched viewport leaves
        # phantom gaps between the first children (seen on the Me card).
        body.set_valign(Gtk.Align.START)

        avatar_box, avatar_image, avatar_fallback = (
            avatar_module.build_avatar(DETAIL_PHOTO_PX))
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

        self._chips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                              spacing=8)
        body.append(self._chips)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self._call_button = widgets.call_button("Call",
                                                self._on_call_clicked)
        self._call_button.set_hexpand(True)
        self._call_button.set_sensitive(False)
        actions.append(self._call_button)
        self._message_button = widgets.outline_button(
            "Message", self._on_message_clicked)
        widgets.wrap_to_fit(self._message_button)
        self._message_button.set_hexpand(True)
        self._message_button.set_sensitive(False)
        actions.append(self._message_button)
        body.append(actions)

        self._fields = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                               spacing=20)
        body.append(self._fields)

        body.append(widgets.section_label("Tags"))
        self._toggles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                spacing=0)
        body.append(self._toggles)

        body.append(widgets.section_label("Me card"))
        me_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        me_text = widgets.label("Your own card", "xx-title")
        me_text.set_hexpand(True)
        me_text.set_valign(Gtk.Align.CENTER)
        me_row.append(me_text)
        self._me_button = widgets.text_button("Set as Me",
                                              self._on_me_clicked)
        me_row.append(self._me_button)
        body.append(me_row)

        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        bottom.add_css_class("xx-page-body")
        bottom.set_margin_bottom(12)
        share_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                            spacing=12)
        share_row.set_hexpand(True)
        share_row.set_homogeneous(True)
        self._share_button = widgets.outline_button(
            "Share vCard", self._on_share_clicked)
        widgets.wrap_to_fit(self._share_button)
        self._share_button.set_hexpand(True)
        share_row.append(self._share_button)
        self._edit_button = widgets.outline_button(
            "Edit", self._on_edit_clicked)
        widgets.wrap_to_fit(self._edit_button)
        self._edit_button.set_hexpand(True)
        share_row.append(self._edit_button)
        bottom.append(share_row)
        self._delete_button = widgets.text_button(
            "Delete", self._on_delete_clicked)
        self._delete_button.add_css_class("xx-danger-text")
        self._delete_button.set_halign(Gtk.Align.CENTER)
        bottom.append(self._delete_button)

        self.set_child(widgets.page(header, body, bottom))
        self.set_title("Details")
        self.connect("unrealize", self._on_unrealize)
        self._watcher.start()
        self._schedule_load()

    # ----------------------------------------------------------------- load

    def _schedule_load(self) -> None:
        worker = threading.Thread(target=self._load_in_worker, daemon=True)
        worker.start()

    def _load_in_worker(self) -> None:
        try:
            record = self._store.get(self._uid)
        except NotFoundError:
            record = None
        GLib.idle_add(self._apply_record, record)

    def _apply_record(self, record) -> bool:
        if not self._alive:
            return False
        if record is None:
            self._close_missing()
            return False
        title = detail_model.detail_title(record)
        shown = xx_phone.display(title, xx_phone.display_country(
            self._region))
        self.set_title(shown)
        self._name.set_text(shown)
        avatar_module.set_avatar(
            self._avatar_image, self._avatar_fallback, title,
            record.photo_path or None,
            bool(getattr(self._config, "show_photos", False)),
            DETAIL_PHOTO_PX)
        self._render_chips(record)
        self._render_fields(record)
        self._render_toggles(record)
        self._refresh_me_button()
        self._refresh_call_buttons(record)
        return False

    def _render_chips(self, record) -> None:
        child = self._chips.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._chips.remove(child)
            child = following
        tags: list[tuple[str, bool]] = []
        if getattr(record, "starred", False):
            tags.append(("Starred", True))
        for group in (getattr(record, "groups", None) or [])[:2]:
            tags.append((str(group), False))
        if not tags:
            self._chips.set_visible(False)
            return
        self._chips.set_visible(True)
        for text, selected in tags:
            chip = Gtk.Label(label=text.upper())
            chip.add_css_class("xx-chip")
            if selected:
                chip.add_css_class("selected")
            self._chips.append(chip)

    def _refresh_me_button(self) -> None:
        if self._config is None:
            self._me_button.set_sensitive(False)
            return
        mine = mecard_module.is_me(self._config, self._uid)
        self._me_button.set_label("Me ✓" if mine else "Set as Me")

    def _on_me_clicked(self) -> None:
        if self._config is None:
            return
        if mecard_module.is_me(self._config, self._uid):
            mecard_module.clear_me_uid(self._config, self._config_path)
        else:
            mecard_module.set_me_uid(self._config, self._config_path,
                                     self._uid)
        self._refresh_me_button()

    def _refresh_call_buttons(self, record) -> None:
        call_uri, message_uri = "", ""
        for section in detail_model.build_detail_sections(record,
                                                          self._region):
            for item in section.fields:
                for action in item.actions:
                    if action.kind == "call" and not call_uri:
                        call_uri = action.uri
                    if action.kind == "message" and not message_uri:
                        message_uri = action.uri
        self._call_uri = call_uri
        self._message_uri = message_uri
        self._call_button.set_sensitive(bool(call_uri))
        self._message_button.set_sensitive(bool(message_uri))

    def _on_call_clicked(self) -> None:
        if getattr(self, "_call_uri", ""):
            launch_uri(self._parent, self._call_uri)

    def _on_message_clicked(self) -> None:
        if getattr(self, "_message_uri", ""):
            launch_uri(self._parent, self._message_uri)

    def _on_back_clicked(self) -> None:
        self._navigation.pop()

    def _on_edit_clicked(self, _button: Gtk.Button | None = None) -> None:
        from xx_contacts.editor_ui import ContactEditorPage

        page = ContactEditorPage(
            self._navigation, self._parent, self._store, self._uid,
            self._db_path, self._photos_dir, config=self._config,
            config_path=self._config_path, default_region=self._region)
        self._navigation.push(page)

    # -------------------------------------------------------------- toggles

    def _render_toggles(self, record) -> None:
        child = self._toggles.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._toggles.remove(child)
            child = following
        self._switches = {}
        self._updating_switches = True
        try:
            keys = ("starred", "business", "blocked")
            for index, key in enumerate(keys):
                switch = widgets.make_switch(
                    bool(getattr(record, key)),
                    lambda active, _k=key: self._on_toggle_flipped_by_key(
                        _k, active))
                self._toggles.append(widgets.setting_row(
                    _TAG_TITLES[key], _TAG_SUBTITLES[key], switch,
                    last=index == len(keys) - 1))
                self._switches[key] = switch
        finally:
            self._updating_switches = False

    def _on_toggle_flipped_by_key(self, key: str, active: bool) -> None:
        if self._updating_switches or not self._alive:
            return
        worker = threading.Thread(target=self._toggle_in_worker,
                                  args=(key, bool(active),),
                                  daemon=True)
        worker.start()

    def _toggle_in_worker(self, key: str, active: bool) -> None:
        try:
            from xx_contacts import service_client as service_client_module

            connection = service_client_module.gui_connection()
            if active:
                service_client_module.update_tags_sync(
                    connection, self._uid, add=(key,))
            else:
                service_client_module.update_tags_sync(
                    connection, self._uid, remove=(key,))
        except (NotFoundError, RuntimeError, GLib.Error):
            pass
        GLib.idle_add(self._reload_from_idle)

    def _reload_from_idle(self) -> bool:
        if self._alive:
            self._schedule_load()
        return False

    def _on_external_changed(self, revision: int | None = None,
                             uids: list[str] | None = None) -> None:
        if not self._alive:
            return
        if uids is not None and self._uid not in uids:
            return
        GLib.idle_add(self._reload_from_idle)

    # ------------------------------------------------------------- sections

    def _render_fields(self, record) -> None:
        child = self._fields.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._fields.remove(child)
            child = following
        for section in detail_model.build_detail_sections(record,
                                                          self._region):
            for item in section.fields:
                caption = item.label or section.title
                # Call/Message stay on the big action buttons above; only
                # the actions with no button of their own get trailing
                # icon buttons on the field's own row.
                rest = [action for action in item.actions
                        if action.kind not in ("call", "message")]
                icons = [a for a in rest if a.kind in ("email", "map")]
                if icons:
                    box = Gtk.Box(
                        orientation=Gtk.Orientation.VERTICAL, spacing=6)
                    box.append(widgets.caption(caption))
                    row = Gtk.Box(
                        orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
                    value = widgets.label(item.display, "xx-field-value")
                    wrap_value(value)
                    value.set_hexpand(True)
                    value.set_valign(Gtk.Align.CENTER)
                    row.append(value)
                    for action in icons:
                        name = ("mail-send-symbolic"
                                if action.kind == "email"
                                else "mark-location-symbolic")
                        button = widgets.icon_button(
                            name,
                            lambda _a=action: self._on_field_action(
                                _a.uri) if _a.uri else None)
                        button.set_valign(Gtk.Align.CENTER)
                        row.append(button)
                    box.append(row)
                    self._fields.append(box)
                    leftover = [a for a in rest if a not in icons]
                    if leftover:
                        links = Gtk.Box(
                            orientation=Gtk.Orientation.HORIZONTAL,
                            spacing=0)
                        for action in leftover:
                            text = ACTION_LABELS.get(action.kind,
                                                    action.kind)
                            button = widgets.text_button(
                                text,
                                lambda _a=action:
                                self._on_field_action(_a.uri)
                                if _a.uri else None)
                            links.append(button)
                        self._fields.append(links)
                else:
                    field_box = widgets.field(caption, item.display)
                    wrap_value(field_box.get_last_child())
                    field_box.set_spacing(6)
                    self._fields.append(field_box)
                    if rest:
                        row = Gtk.Box(
                            orientation=Gtk.Orientation.HORIZONTAL,
                            spacing=0)
                        for action in rest:
                            text = ACTION_LABELS.get(action.kind,
                                                    action.kind)
                            button = widgets.text_button(
                                text,
                                lambda _a=action:
                                self._on_field_action(_a.uri)
                                if _a.uri else None)
                            row.append(button)
                        self._fields.append(row)
        if detail_model.CALL_HISTORY_AVAILABLE:
            history = detail_model.get_call_history(self._uid)
            if history:
                self._fields.append(widgets.section_label("Recent calls"))
                for line in history:
                    value = widgets.label(str(line), "xx-body")
                    value.set_wrap(True)
                    value.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                    self._fields.append(value)

    def _on_field_action(self, uri: str) -> None:
        if uri:
            launch_uri(self._parent, uri)

    # ---------------------------------------------------------------- share

    def _on_share_clicked(self, _button: Gtk.Button | None = None) -> None:
        worker = threading.Thread(target=self._share_in_worker, daemon=True)
        worker.start()

    def _share_in_worker(self) -> None:
        try:
            record = self._store.get(self._uid)
            photo = photos_module.read_photo_bytes(record.photo_path or "")
            card = vcard_module.contact_to_vcard(record, photo_bytes=photo)
            name = detail_model.detail_title(record)
        except NotFoundError:
            card, name = "", ""
        GLib.idle_add(self._portal_save, card, name)

    def _portal_save(self, card: str, name: str) -> bool:
        if not self._alive:
            return False
        if not card:
            self._message("Share failed",
                          "Could not build a vCard for this contact.")
            return False
        dialog = Gtk.FileDialog()
        dialog.set_initial_name(f"{name or 'contact'}.vcf")
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
            path = os.path.join(exports, f"{self._uid}.vcf")
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
                      lambda dlg, res: self._on_saved_opened(dlg, res, path))
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

    # --------------------------------------------------------------- delete

    def _on_delete_clicked(self, _button: Gtk.Button | None = None) -> None:
        dialog = Adw.AlertDialog.new(
            "Delete contact?",
            f"Delete {self._name.get_text()}? This cannot be undone.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.choose(self._parent, None, self._on_delete_chosen)

    def _on_delete_chosen(self, dialog: Adw.AlertDialog,
                          result: Gio.AsyncResult) -> None:
        try:
            response = dialog.choose_finish(result)
        except GLib.Error:
            return
        if response != "delete":
            return
        worker = threading.Thread(target=self._delete_in_worker, daemon=True)
        worker.start()

    def _delete_in_worker(self) -> None:
        try:
            from xx_contacts import service_client as service_client_module

            connection = service_client_module.gui_connection()
            service_client_module.delete_sync(connection, self._uid)
            missing = False
        except NotFoundError:
            missing = True
        except (RuntimeError, GLib.Error):
            return
        GLib.idle_add(self._after_delete, missing)

    def _after_delete(self, missing: bool) -> bool:
        if not self._alive:
            return False
        if missing:
            self._close_missing()
            return False
        self._alive = False
        try:
            self._watcher.stop()
        except Exception:
            pass
        if self._on_deleted is not None:
            self._on_deleted()
        else:
            self._navigation.pop()
        return False

    # ------------------------------------------------------------------ misc

    def _close_missing(self) -> None:
        self._message("Contact gone",
                      "This contact no longer exists.")
        self._alive = False
        try:
            self._watcher.stop()
        except Exception:
            pass
        if self._on_deleted is not None:
            self._on_deleted()
        else:
            self._navigation.pop()

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
