"""L4 static guards: the new pages must keep the XXWM runtime traps shut.

These read the sources (never construct widgets, so they run headless)
and pin the phone-app rules the editor/Me/photos UI has to follow:
tap-to-focus entries, no keyboard pop on open, touch date pickers,
portal file dialogs, human type labels, one-write saves, the int64
rule, and valid GTK signal names.
"""

from pathlib import Path

from xx_contacts import editor  # noqa: E402
from xx_contacts.service import emit_changed  # noqa: E402

SRC = Path(__file__).parent.parent / "src" / "xx_contacts"


def _read(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


# ---- UX pass: one way to each thing, no clutter ----

def test_detail_edits_from_one_button_only():
    src = _read("detail_ui.py")
    assert "document-edit-symbolic" not in src
    assert '"Edit", self._on_edit_clicked' in src
    assert "★" not in src


def test_list_more_button_opens_settings_directly():
    src = _read("list_ui.py")
    assert "Gtk.Popover()" not in src
    assert "self.open_settings()" in src


def test_editor_shows_no_empty_preview_or_add_glyphs():
    src = _read("editor_ui.py")
    assert "(empty)" not in src
    assert '"+ Add' not in src
    assert "self._photo_remove.set_visible(has_photo)" in src


def test_editor_entries_take_focus_only_on_tap():
    src = _read("editor_ui.py")
    assert "install_tap_to_focus" in src
    assert src.count("install_tap_to_focus(") >= 4
    assert "from xx_contacts.list_ui import install_tap_to_focus" in src
    assert _read("list_ui.py").count("set_can_focus(False)") >= 1
    assert 'connect("map"' in src
    assert "set_focus(None)" in src
    assert "grab_focus" not in src


def test_editor_dates_use_touch_calendar_not_free_text():
    src = _read("editor_ui.py")
    assert "Gtk.Calendar" in src
    assert "Gtk.Popover" in src
    assert "day-selected" in src
    lowered = src.lower()
    assert "birthday_entry" not in lowered
    assert "anniversary_entry" not in lowered
    assert "birthdayentry" not in lowered


def test_editor_photo_uses_portal_dialog_with_image_filters():
    src = _read("editor_ui.py")
    assert "Gtk.FileDialog" in src
    assert "Gtk.FileFilter" in src
    assert 'add_mime_type("image/*")' in src
    assert ".open(self._parent, None, self._on_photo_chosen)" in src
    assert "user_data" not in src


def test_editor_type_picker_uses_human_labels():
    options = dict(editor.type_options())
    assert options["CELL"] == "Mobile"
    src = _read("editor_ui.py")
    assert "type_options()" in src
    assert "Gtk.DropDown" in src


def test_editor_save_is_one_service_call_with_no_gui_emit():
    src = _read("editor.py")
    assert "store.create" in src and "store.update" in src
    ui = _read("editor_ui.py")
    assert "save_new" not in ui and "save_edit" not in ui
    assert "emit_changed" not in ui
    assert "service_client" in ui
    assert "form_to_vcard" in ui
    assert "create_sync" in ui and "update_sync" in ui
    assert "Discard changes?" in ui
    assert "is_dirty" in ui


def test_gui_writes_all_go_through_the_service():
    for name in ("list_ui.py", "detail_ui.py", "editor_ui.py"):
        src = _read(name)
        assert "service_client" in src, name
    assert "emit_changed" not in _read("list_ui.py")
    assert "emit_changed" not in _read("detail_ui.py")
    assert "emit_changed" not in _read("editor_ui.py")
    for name, writes in (("list_ui.py", ("update_tags_sync",
                                         "delete_sync")),
                         ("detail_ui.py", ("update_tags_sync",
                                           "delete_sync"))):
        src = _read(name)
        for symbol in writes:
            assert symbol in src, f"{name}: {symbol}"
    assert "update_tags" in _read("list_ui.py")


def test_me_page_holds_no_entries_or_focus_grab():
    src = _read("mecard_ui.py")
    assert "Gtk.Entry" not in src
    assert "SearchEntry" not in src
    assert "grab_focus" not in src
    assert "HeaderBar" not in src
    assert "compact_header(" in src
    assert "Share vCard" in src
    assert "Set as Me" in src or "Remove Me" in src


def test_list_has_new_and_me_buttons_and_photos_toggle():
    src = _read("list_ui.py")
    assert "ContactEditorPage" in src
    assert "MeCardPage" in src
    assert "LIST_PHOTO_PX = 32" in src
    assert "show_photos" in src
    assert "Gtk.HeaderBar" not in src


def test_detail_has_edit_me_and_photo_header():
    src = _read("detail_ui.py")
    assert "ContactEditorPage" in src
    assert "Set as Me" in src
    assert "DETAIL_PHOTO_PX = 88" in src
    assert "Gtk.Entry" not in src
    assert "SearchEntry" not in src
    assert "grab_focus" not in src


def test_no_gobject_int_properties_in_new_ui():
    for name in ("editor_ui.py", "mecard_ui.py", "avatar.py", "list_ui.py",
                 "detail_ui.py"):
        assert "GObject.Property(type=int)" not in _read(name), name


def _gi_signal_names() -> set[str]:
    import warnings

    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GObject, Gtk

    names: set[str] = set()
    warnings.simplefilter("ignore")
    for module in (Gtk, Gdk, Adw, Gio):
        for attr in dir(module):
            obj = getattr(module, attr, None)
            gtype = getattr(obj, "__gtype__", None)
            if gtype is None:
                continue
            try:
                GObject.type_class_ref(gtype)
                names.update(GObject.signal_list_names(gtype))
            except Exception:
                continue
    assert "day-selected" in names
    assert Gtk.Calendar is not None
    return names | {"notify"}


def test_new_ui_uses_real_gtk_signals_only():
    import re

    known = _gi_signal_names()
    unknown = []
    for name in ("editor_ui.py", "mecard_ui.py", "avatar.py", "list_ui.py",
                 "detail_ui.py"):
        for signal in re.findall(r"connect\(\"([^\"]+)\"", _read(name)):
            base = signal.split("::")[0]
            if base not in known:
                unknown.append(f"{name}: {signal}")
    assert unknown == []


def test_emit_changed_reports_tuple_changed_signal():
    class FakeConnection:
        def __init__(self):
            self.signals = []

        def emit_signal(self, destination, path, interface, signal,
                        parameters):
            self.signals.append((destination, path, interface, signal,
                                 parameters))
            return True

    assert emit_changed(None, 1, ["uid"]) is False
    connection = FakeConnection()
    assert emit_changed(connection, 9, ["uid-1"]) is True
    (_dest, path, iface, signal, params) = connection.signals[0]
    assert (path, iface, signal) == ("/io/piercingxx/Contacts",
                                     "io.piercingxx.Contacts1", "Changed")
    assert params.unpack() == (9, ["uid-1"])
