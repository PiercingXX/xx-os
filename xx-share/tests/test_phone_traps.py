"""Phone-trap guards for the share window: static source checks.

These mirror XXWM_RULES.md's runtime traps so a regression fails here
instead of on the device. No GTK instantiation, so they run headless.
"""
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
UI = (SRC / "ui.py").read_text(encoding="utf-8")


def test_no_application_get_display():
    for line in UI.splitlines():
        if "get_display()" in line:
            assert "Display.get_default()" in line, line


def test_theming_owned_by_xx_ui():
    assert "xx_ui.apply" in UI
    assert "add_provider_for_display" not in UI
    assert "Gdk.Display.get_default()" not in UI


def test_no_local_theme_css():
    for token in ("def theme_css", "effective_theme", "THEME_PRESETS",
                  "ThemePreset"):
        assert token not in UI, token
    assert "from xx_ui import widgets" in UI


def test_no_entry_autofocus_at_startup():
    for guilty in ("alias.grab_focus", "pin.grab_focus",
                   "send_pin.grab_focus", "entry.grab_focus"):
        assert guilty not in UI
    assert "self.send_button.grab_focus()" in UI


def test_no_titlebar_chrome():
    for guilty in ("HeaderBar", "ToolbarView", "ActionRow",
                   "show_start_title_buttons", "show_end_title_buttons"):
        assert guilty not in UI, guilty
    assert "set_decorated(False)" in UI


def test_lists_use_xx_ui_builders():
    for token in ("widgets.page_header", "widgets.list_row",
                  "widgets.bottom_tabs", "widgets.setting_row"):
        assert token in UI, token
    for gone in ("Gtk.ListView", "Gtk.SignalListItemFactory",
                 "Gtk.SingleSelection", "Gtk.StringList", "Gtk.ListBox"):
        assert gone not in UI, gone


def test_file_picker_uses_modern_dialog():
    assert "Gtk.FileDialog" in UI
    assert "open_multiple" in UI
    assert "open_multiple_finish" in UI
    assert "FileChooserDialog" not in UI


def test_share_window_scope_present():
    for token in ("Cancel", "cancel_send", "_on_clear_history",
                  "history_box", "_toggle_favorite", "Auto-accept",
                  "Keep visible", "Receiver PIN", "PIN_NEEDED",
                  "TransferDone", "IncomingRequest"):
        assert token in UI, token


def test_minimal_no_clutter():
    # An empty list shows nothing; one entry point per action (no
    # overflow menu duplicating Clear/Settings); no PIN line in the
    # receive prompt (the PIN is checked before the prompt appears).
    for gone in ("widgets.empty_state", "overflow_cb", "Gtk.Popover",
                 "Nothing received", "No favorites", "No devices",
                 "No files", "Your PIN", "files chosen", "Empty = no PIN",
                 "compact_header"):
        assert gone not in UI, gone


def test_service_wires_bus_signals_and_history():
    service = (SRC / "service.py").read_text(encoding="utf-8")
    for token in ("HistoryStore", "TransferManager", "IncomingRequest",
                  "TransferProgress", "TransferDone", "start_send",
                  "cancel_send", "visibility_note"):
        assert token in service, token


def test_desktop_entry_and_unit():
    root = SRC.parent
    desktop = (root / "data" / "io.piercingxx.XXShare.desktop.in").read_text()
    assert "Send with xx-share" in desktop
    assert "--send" in desktop
    unit = (root / "data" / "systemd" / "xx-share.service.in").read_text()
    assert "--service" in unit
    main = (SRC / "main.py").read_text(encoding="utf-8")
    assert "BusServer" in main
    assert "MainLoop" in main
    assert "BusBackend" in main
