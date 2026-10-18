"""Device-rule contract for the mailbox stack (headless static checks).

The runtime traps in XXWM_RULES.md bit on a real phone while unit
tests stayed green, so this file pins the trap surface directly on
the sources: xx_ui owns the theme, no HeaderBar chrome, no
programmed focus (the OSK pops only on tap), themed list
backgrounds, ListView-backed long lists, ellipsized rows, and no
blocking calls on the main-loop side.
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "xx_email"
UI = SRC / "ui"

KNOWN_SIGNALS = {
    "activate",
    "bind",
    "changed",
    "clicked",
    "pressed",
    "response",
    "setup",
    "swipe",
}


def _sources():
    files = [SRC / "main.py", UI / "common.py", UI / "mailbox_page.py",
             UI / "folders_page.py", UI / "thread_page.py",
             UI / "search_page.py", UI / "compose_page.py",
             UI / "setup_page.py", UI / "settings_page.py",
             UI / "logs_page.py"]
    for path in files:
        assert path.exists(), path
    return {path: path.read_text(encoding="utf-8") for path in files}


def test_no_get_display_on_application():
    sources = _sources()
    for path, text in sources.items():
        assert ".get_display()" not in text, path
    assert "Gdk.Display.get_default()" in sources[SRC / "main.py"]


def test_xx_ui_owns_the_theme():
    main = (SRC / "main.py").read_text(encoding="utf-8")
    assert "xx_ui.apply(" in main
    assert "email.css" in main
    for path, text in _sources().items():
        assert "Adw.HeaderBar" not in text, path


def test_no_programmed_focus_at_startup():
    for path, text in _sources().items():
        if path.name == "compose_page.py":
            continue  # explicit-open exception takes focus on show
        # tap_to_focus only focuses on a user tap (OSK stays down).
        scrubbed = re.sub(r"def tap_to_focus.*?(?=\ndef |\Z)", "",
                          text, flags=re.S)
        assert "grab_focus" not in scrubbed, path
        assert "grab-focus" not in scrubbed, path


def test_phone_chrome_has_no_title_buttons():
    main = (SRC / "main.py").read_text(encoding="utf-8")
    assert "set_decorated(False)" in main
    for name in ("mailbox_page.py", "setup_page.py"):
        text = (UI / name).read_text(encoding="utf-8")
        assert "page_header" in text or \
            "header_with_menu" in text, name
    # Pushed pages (Settings too) carry the back chevron.
    for name in ("thread_page.py", "search_page.py", "compose_page.py",
                 "folders_page.py", "logs_page.py", "setup_page.py",
                 "settings_page.py"):
        text = (UI / name).read_text(encoding="utf-8")
        assert "compact_header" in text or \
            "xx-compact-header" in text, name


def test_long_lists_are_listview_backed():
    common = (UI / "common.py").read_text(encoding="utf-8")
    assert "Gtk.ListView" in common
    assert "SignalListItemFactory" in common
    assert "NoSelection" in common
    for name in ("mailbox_page.py", "thread_page.py", "search_page.py"):
        text = (UI / name).read_text(encoding="utf-8")
        assert "ListView" in text or "thread_list_view" in text, name


def test_rows_ellipsize_or_wrap():
    common = (UI / "common.py").read_text(encoding="utf-8")
    assert "set_ellipsize" in common
    assert "set_wrap" in common
    assert "list_row" in common


def test_search_entry_sits_at_the_bottom_unfocused():
    text = (UI / "search_page.py").read_text(encoding="utf-8")
    assert text.index("self.append(scrolled)") < \
        text.index("self.append(search)")
    assert "set_can_focus(False)" in text


def test_no_blocking_calls_on_ui_side():
    banned = ("subprocess", "time.sleep", "call_sync",
              "lookup_sync", "store_sync", "clear_sync", "bus_get_sync")
    for path, text in _sources().items():
        for marker in banned:
            assert marker not in text, (path, marker)


def test_only_known_signal_names_connected():
    connected = set()
    for path, text in _sources().items():
        for match in re.finditer(r"\.connect\(\"([^\"]+)\"", text):
            connected.add(match.group(1))
    unknown = connected - KNOWN_SIGNALS
    assert not unknown, unknown


def test_filters_and_bottom_nav_use_design_builders():
    mailbox = (UI / "mailbox_page.py").read_text(encoding="utf-8")
    assert "chip_bar" in mailbox
    assert "bottom_tabs" in mailbox
    # No inbox menu: Folders and Options are tabs, accounts live in
    # Folders and Settings once each.
    assert "header_with_menu" not in mailbox
    assert "view-refresh-symbolic" in mailbox
    assert "Adw.ViewStack" not in mailbox
    assert "Adw.ViewSwitcherBar" not in mailbox
    assert "Gtk.GestureSwipe" in (UI / "common.py").read_text(
        encoding="utf-8")
    assert "Gtk.GestureLongPress" in (UI / "common.py").read_text(
        encoding="utf-8")


def test_design_system_paints_list_backgrounds():
    import sys
    import types

    common = Path(__file__).resolve().parent.parent.parent / "common"
    before = set(sys.modules)
    if "xx_ui" not in sys.modules:
        pkg = types.ModuleType("xx_ui")
        pkg.__path__ = [str(common / "xx_ui")]
        sys.modules["xx_ui"] = pkg
    try:
        from xx_ui import css as css_mod
        from xx_ui import theme as theme_mod

        rendered = css_mod.render(theme_mod.load())
    finally:
        for name in [mod for mod in sys.modules
                     if mod not in before and mod != "xx_ui"]:
            del sys.modules[name]
    assert "listview" in rendered
    active = theme_mod.load()
    found = set(re.findall(r"#[0-9a-fA-F]{6}", rendered))
    allowed = {active.background, active.surface, active.surface_alt,
               active.border, active.foreground, active.muted,
               active.accent, theme_mod.DANGER_RED,
               theme_mod.ON_DANGER_FG}
    assert found and found <= allowed, found - allowed


def test_no_try_list_or_loading_lines():
    search = (UI / "search_page.py").read_text(encoding="utf-8")
    assert "OPERATOR_HINTS" not in search
    for name in ("folders_page.py", "settings_page.py"):
        text = (UI / name).read_text(encoding="utf-8")
        assert "Loading accounts" not in text, name
        assert "No accounts yet" not in text, name
    setup = (UI / "setup_page.py").read_text(encoding="utf-8")
    assert "Revoke Google access" not in setup


def test_lost_grant_says_it_once():
    mailbox = (UI / "mailbox_page.py").read_text(encoding="utf-8")
    assert 'if not outcome.get("ok", False) and not reauth:' in mailbox


def test_reply_row_stacks_only_when_narrow():
    thread = (UI / "thread_page.py").read_text(encoding="utf-8")
    assert "widgets.fit_row(" in thread
    assert "Gtk.FlowBox" not in thread
