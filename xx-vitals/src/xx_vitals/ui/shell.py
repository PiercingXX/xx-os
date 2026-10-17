"""Application shell: xx_ui theme, four tabs, entry FAB, overflow menus.

The window is a plain Gtk.ApplicationWindow with no titlebar chrome; every
page carries its own PageHeader plus the shared BottomTabs. The design
system is installed once here via ``xx_ui.apply`` (live theme switches
restyle the open window with no restart). ``nav`` hooks below are the
headless navigation surface used by scripts/snap.py and the overflow probe.
"""

from __future__ import annotations

from types import SimpleNamespace

import xx_ui
from xx_ui import widgets

TABS = ("home", "journal", "browse", "profile")

TAB_LABELS = {"home": "Home", "journal": "Journal",
              "browse": "Browse", "profile": "Profile"}


def visible_tabs(config) -> list[str]:
    tabs = ["home"]
    if getattr(config, "show_journal_tab", True):
        tabs.append("journal")
    if getattr(config, "show_browse_tab", True):
        tabs.append("browse")
    if getattr(config, "show_profile_tab", True):
        tabs.append("profile")
    return tabs


def copy_to_clipboard(text: str) -> str | None:
    """Copy text; returns an error line or None on success."""
    from gi.repository import Gdk

    display = Gdk.Display.get_default()
    if display is None:
        return "No clipboard available right now."
    display.get_clipboard().set(text)
    return None


def show_result_dialog(window, title: str, text: str):
    """One-shot surface notice (import/export/copy outcomes)."""
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw

    from xx_vitals.ui import entry as entry_page

    dialog = Adw.Dialog(title=title)
    box = widgets.dialog_box(title)
    box.append(widgets.wrap_label(text, "xx-body"))
    close = widgets.primary_button("Close", dialog.close)
    box.append(close)
    entry_page.fit_dialog(dialog, box)
    dialog.present(window)
    return dialog


def open_overflow(anchor, items: list):
    """Surface menu: popover of 48 px rows. Returns the popover."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    popover = Gtk.Popover()
    box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    box.set_margin_top(4)
    box.set_margin_bottom(4)
    for text, callback in items:
        row = widgets.menu_row(text, callback)
        row.connect("clicked", lambda _b: popover.popdown())
        box.append(row)
    popover.set_child(box)
    popover.set_parent(anchor)
    popover.popup()
    return popover


def build_window(app, db, config, secrets) -> object:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from xx_vitals.ui import browse as browse_page
    from xx_vitals.ui import home as home_page
    from xx_vitals.ui import journal as journal_page
    from xx_vitals.ui import profile as profile_page

    xx_ui.apply(app)
    window = Gtk.ApplicationWindow(application=app)
    window.set_title("xx-vitals")
    window.set_default_size(360, 720)
    window.set_decorated(False)

    lives: dict[str, dict] = {}
    stack = Gtk.Stack()
    stack.set_hexpand(True)
    stack.set_vexpand(True)
    nav = SimpleNamespace()
    state = {"current": "home", "tabs": None}

    def refresh_all() -> None:
        for live in lives.values():
            refresh = live.get("refresh")
            if refresh is not None:
                refresh()

    def show(name: str) -> None:
        if name not in TABS:
            return
        if name not in visible_tabs(config):
            name = "home"
        state["current"] = name
        stack.set_visible_child_name(name)
        live = lives.get(name)
        if live is not None and live.get("refresh") is not None:
            live["refresh"]()
        _rebuild_tabs()

    def _rebuild_tabs() -> None:
        bar = widgets.bottom_tabs([
            ("", TAB_LABELS[tab], tab == state["current"],
             lambda tab=tab: show(tab))
            for tab in visible_tabs(config)
        ])
        if state["tabs"] is not None:
            root.remove(state["tabs"])
        state["tabs"] = bar
        root.append(bar)

    nav.show = show
    nav.refresh_all = refresh_all
    nav.refresh_tabs = _rebuild_tabs

    def _open_sheet(kind: str):
        from xx_vitals.ui import entry as entry_page

        return entry_page.open_sheet(window, db, config, kind,
                                     on_saved=refresh_all)

    nav.add_activity = lambda: _open_sheet("activity")
    nav.add_weight = lambda: _open_sheet("weight")

    def _import_here() -> None:
        profile_page.open_takeout_import(
            window, db,
            lambda text: show_result_dialog(window, "Import", text),
            on_done=refresh_all)

    nav.import_takeout = _import_here
    nav.open_menu = lambda name="home": lives[name]["menu"]()

    for tab in TABS:
        lives[tab] = {}
    stack.add_named(home_page.build_home_page(
        db, config, nav, lives["home"]), "home")
    stack.add_named(journal_page.build_journal_page(
        db, config, nav, lives["journal"]), "journal")
    lives["journal"]["window"] = window
    stack.add_named(browse_page.build_browse_page(
        db, config, nav, lives["browse"]), "browse")
    stack.add_named(profile_page.build_profile_page(
        db, config, secrets, nav, lives["profile"], window), "profile")
    profile_page.maybe_start_fallback(db, config, window)

    root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    root.append(stack)
    window.set_child(root)
    _rebuild_tabs()
    show("home")

    window._vitals_nav = {  # headless navigation for snap.py + probes
        "show": show,
        "refresh_all": refresh_all,
        "add_activity": nav.add_activity,
        "add_weight": nav.add_weight,
        "import_takeout": nav.import_takeout,
        "open_menu": nav.open_menu,
        "open_metric": lambda key="steps": lives["browse"].get(
            "open_metric", lambda _k: None)(key),
    }
    return window
