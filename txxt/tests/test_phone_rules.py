"""L4 phone-rule guards: static checks over the UI sources.

GTK behavior that headless tests cannot execute (focus, list
activation, title buttons, dialog arity, D-Bus reply types) is pinned
here as text guards so a regression fails loudly instead of popping
the on-screen keyboard on the device.
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "txxt"

UI_WITH_LISTS = ("conversations.py", "thread_page.py")
UI_WRITERS = ("main.py", "conversations.py", "thread_page.py", "compose.py",
              "new_conversation_page.py")
UI_PAGES = ("conversations.py", "thread_page.py", "blocking_page.py",
            "quarantine_page.py", "new_conversation_page.py",
            "settings_page.py")


def read(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


# ---- display / focus ----

def test_no_get_display_on_app_uses_default_display():
    src = read("main.py")
    assert "get_display()" not in src
    assert "Gdk.Display.get_default()" in src


def test_no_grab_focus_at_startup():
    assert "grab_focus" not in read("main.py")
    for name in UI_WITH_LISTS:
        current = ""
        for line in read(name).splitlines():
            match = re.match(r"\s*def (\w+)", line)
            if match:
                current = match.group(1)
            if "grab_focus" in line:
                assert "tap" in current or "click" in current, (name, current)


def test_entries_unfocusable_until_tapped():
    helper = read("conversations.py")
    for token in ("set_can_focus(False)", "PropagationPhase.CAPTURE",
                  "EventControllerFocus", "def tap_to_focus"):
        assert token in helper, token
    assert "set_focusable(False)" not in helper
    for name in UI_WITH_LISTS:
        src = read(name)
        assert "tap_to_focus" in src, name
        assert "set_can_focus(False)" in src or "tap_to_focus" in src, name


# ---- lists ----

def test_every_listview_single_click_no_selection():
    for name in UI_WITH_LISTS:
        src = read(name)
        assert src.count("Gtk.ListView.new") >= 1, name
        assert src.count("Gtk.ListView.new") == src.count(
            "set_single_click_activate(True)"), name
        assert "NoSelection.new" in src, name
    for name in UI_WITH_LISTS:
        assert "SingleSelection" not in read(name), name


def test_long_press_resolves_row_under_pointer():
    for name in ("conversations.py",):
        src = read(name)
        assert "Gtk.GestureLongPress.new" in src, name
        assert ".pick(" in src, name
        assert "get_parent()" in src, name


# ---- header bars / layout ----

def test_no_header_bar_chrome_anywhere():
    """The restyle dropped every Adw.HeaderBar for xx_ui page headers."""
    for name in UI_PAGES:
        assert "Adw.HeaderBar(" not in read(name), name
    assert "set_decorated(False)" in read("main.py")


def test_phone_layout_bounds():
    assert "set_size_request(320" in read("main.py")
    assert "360, 720" in read("main.py")
    for name in UI_PAGES:
        assert "from xx_ui import widgets" in read(name), name
    assert "widgets.bubble" in read("thread_page.py")
    assert "set_ellipsize" in read("thread_page.py")


def test_entries_unfocusable_on_new_page():
    assert "tap_to_focus" in read("new_conversation_page.py")


# ---- dialogs: parent first ----

def test_alert_dialogs_choose_parent_first():
    for name in ("conversations.py", "thread_page.py"):
        assert "Adw.AlertDialog.new" in read(name), name
    for path in sorted(SRC.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        assert re.search(r"\.choose\(\s*None", src) is None, path.name


# ---- D-Bus reply types: VariantType or None, never a string ----

def test_reply_types_are_variant_or_none():
    src = read("txxt_client.py")
    assert "VariantType.new(" in src
    assert "call_sync" not in src
    for path in sorted(SRC.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert re.search(r"reply_type\s*=\s*['\"]", text) is None, path.name


# ---- no polling: refresh off MessagesChanged ----

def test_no_polling_signal_drives_refresh():
    for name in ("main.py", "conversations.py", "thread_page.py",
                 "txxt_client.py", "new_conversation_page.py"):
        assert "timeout_add" not in read(name), name
    assert "signal_subscribe" in read("txxt_client.py")
    assert "MessagesChanged" in read("txxt_client.py")
    assert "MessagesChanged" in read("main.py")


# ---- Android-style ids need 64-bit properties ----

def test_android_ids_use_int64_properties():
    conv = read("conversations.py")
    assert "conversation_id = GObject.Property(type=int" not in conv
    assert "TYPE_INT64" in conv
    thread = read("thread_page.py")
    assert "message_id = GObject.Property(type=int" not in thread
    assert "TYPE_INT64" in thread


# ---- daemon-only message path ----

def test_ui_never_touches_modem_or_message_rows():
    banned = ("ModemManager", "mm_client", "persist_inbound",
              "persist_outgoing", "mark_sent", "create_and_send")
    for name in UI_WRITERS:
        src = read(name)
        for token in banned:
            assert token not in src, (name, token)


def test_send_and_mark_read_go_through_the_client():
    assert "self._client.send" in read("thread_page.py")
    assert "self._client.mark_read" in read("thread_page.py")
    assert "self._client.mark_read" in read("conversations.py")
    for name in ("main.py", "conversations.py", "thread_page.py"):
        assert "TxxtClient" in read(name) or "txxt_client" in read(name), name


# ---- theme: the app owns no theme reader or CSS ----

def test_no_local_theme_module():
    assert not (SRC / "theme.py").exists()
    for name in UI_PAGES + ("main.py",):
        assert "txxt_theme" not in read(name), name
        assert "css_for_theme" not in read(name), name


def test_shared_theme_applied_once_at_startup():
    # The shell theme is the single theme source: one xx_ui.apply at
    # startup (its config monitor live-reloads), no manual override.
    src = read("main.py")
    assert "xx_ui.apply(" in src
    assert "xx_ui.on_change(" not in src
    assert "xx_ui.install(" not in src
    assert "manual_theme" not in src


# ---- one way to do each thing ----

def test_new_message_has_one_to_field():
    src = read("new_conversation_page.py")
    assert src.count("text_entry(") == 1
    assert "search_bar(" not in src
    assert "empty_state(" not in src


def test_start_needs_a_number_not_a_name():
    from txxt.new_conversation_page import _dialable

    assert _dialable("+1 (614) 555-0101")
    assert _dialable("72975")
    assert not _dialable("")
    assert not _dialable("Mara")
    assert not _dialable("Mara 2")


def test_inbox_header_is_new_and_settings_no_menu():
    src = read("conversations.py")
    assert '("New", self._on_compose_clicked)' in src
    assert "overflow_cb" not in src
    assert "_on_overflow" not in src


def test_row_states_read_once():
    # The star rides the title and "muted" leads the snippet, so the
    # row never trails " · Starred" or " · Muted" as well.
    src = read("conversations.py")
    assert "view.snippet + view.marks" not in src
    assert "shrink_header_title(self._header)" in src


def test_a_photo_shows_without_its_placeholder_line():
    assert "COLLAPSED_PHOTO_PLACEHOLDER" in read("thread_page.py")


def test_a_block_that_worked_pops_nothing():
    src = read("conversations.py")
    assert '"Blocked"' not in src
    assert "Could not block" in src
