"""Navigation driver for scripts/snap.py (``win`` and ``app`` in scope).

SNAP_TARGET picks the surface. Headless Broadway never ticks the frame
clock, so an Adw bottom sheet cannot animate open: the one presentation
seam (xx_music.ui.common.present) is swapped for a dock that shows the
same sheet content where the bottom sheet docks it, over a scrim.
"""

import os

from xx_music.engine import ErrorKind, Item, ItemKind, Mood
from xx_music.ui import common as _common
from xx_music.ui import menus, pages

TARGET = os.environ.get("SNAP_TARGET", "home")
Gtk.Settings.get_default().set_property(  # noqa: F821
    "gtk-enable-animations", False)
shell = win.xx_shell  # noqa: F821
ui = shell.ui
_content = win.get_content()  # noqa: F821
win.set_content(None)  # noqa: F821
_overlay = Gtk.Overlay()  # noqa: F821
_overlay.set_child(_content)
win.set_content(_overlay)  # noqa: F821
_css = Gtk.CssProvider()  # noqa: F821
_css.load_from_data(b"""
.snap-scrim { background-color: alpha(black, 0.55); }
.snap-dock { background-color: @card_bg_color;
  border-top: 1px solid @borders; border-radius: 16px 16px 0 0;
  padding-top: 14px; }
""")
Gtk.StyleContext.add_provider_for_display(  # noqa: F821
    win.get_display(), _css, 900)  # noqa: F821


def _dock(dialog, parent):
    child = dialog.get_child()
    dialog.set_child(None)
    scrim = Gtk.Box()  # noqa: F821
    scrim.add_css_class("snap-scrim")
    child.add_css_class("snap-dock")
    child.set_valign(Gtk.Align.END)  # noqa: F821
    _overlay.add_overlay(scrim)
    _overlay.add_overlay(child)
    return dialog


_common.present = _dock


def later(ms, fn):
    GLib.timeout_add(ms, lambda: (fn(), False)[1])  # noqa: F821


def scroll_to(get_scroller, fraction):
    """Pages fill in late; scroll once their height has settled (each
    set_value re-allocates, so never set the same value twice)."""
    state = {"upper": -1.0, "tries": 10, "seen": 0}

    def step():
        adj = get_scroller().get_vadjustment()
        upper = adj.get_upper()
        state["tries"] -= 1
        settled = upper > adj.get_page_size() + 1 and upper == state["upper"]
        state["seen"] = state["seen"] + 1 if settled else 0
        if state["seen"] >= 2 or state["tries"] <= 0:
            target = (upper - adj.get_page_size()) * fraction
            if abs(adj.get_value() - target) > 0.5:
                adj.set_value(target)
            return False
        state["upper"] = upper
        return True

    GLib.timeout_add(120, step)  # noqa: F821


def find_scroller(widget):
    """The first vertical ScrolledWindow inside the visible page."""
    stack = [widget]
    while stack:
        node = stack.pop(0)
        if isinstance(node, Gtk.ScrolledWindow) and \
                node.get_vscrollbar_policy() != Gtk.PolicyType.NEVER:  # noqa: F821
            return node
        child = node.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return None


def top_page():
    return shell.current_nav().get_visible_page().get_child()


def track(tid):
    import demo_seed

    return demo_seed.track(tid)


search = shell.pages["search"]
library = shell.pages["library"]
settings = shell.pages["settings"]

if TARGET == "home":
    pass
elif TARGET == "home-lower":
    scroll_to(lambda: shell.pages["home"].scroll, 0.62)
elif TARGET == "home-end":
    scroll_to(lambda: shell.pages["home"].scroll, 1.0)
elif TARGET == "search":
    shell.show("search")
elif TARGET == "search-typing":
    shell.show("search")
    search.entry.set_text("no")
elif TARGET == "search-results":
    shell.show("search")
    search.submit("night")
elif TARGET == "search-songs":
    shell.show("search")
    search.submit("o")
    later(300, lambda: search.set_kind(ItemKind.TRACK))
elif TARGET == "search-menu":
    shell.show("search")
    search.submit("night")
    later(400, lambda: menus.track_menu(ui, track("tr-003")))
elif TARGET == "artist":
    shell.show("search")
    pages.open_artist(ui, "ar-lumen", "Lumen Drive")
elif TARGET == "artist-lower":
    shell.show("search")
    pages.open_artist(ui, "ar-lumen", "Lumen Drive")
    scroll_to(lambda: find_scroller(top_page()), 1.0)
elif TARGET == "album":
    pages.open_album(ui, "al-tide", "Tidewater")
elif TARGET == "playlist":
    pages.open_playlist(ui, "pl-drive", "Night Drive")
elif TARGET == "mood":
    pages.open_mood(ui, Mood("mo-chill", "Chill", "Moods"))
elif TARGET == "moods":
    pages.open_moods(ui)
elif TARGET == "charts":
    pages.open_charts(ui)
elif TARGET == "item-menu":
    menus.item_menu(ui, Item(ItemKind.ALBUM, "al-tide", "Tidewater",
                             "2024 · Lumen Drive"))
elif TARGET == "playing":
    shell.open_now_playing()
elif TARGET == "lyrics":
    shell.open_now_playing("lyrics")
elif TARGET == "queue":
    shell.open_now_playing("queue")
elif TARGET == "sheet-sleep":
    shell.open_now_playing()
    later(200, shell.now_playing.open_sleep)
elif TARGET == "sheet-sleep-on":
    ui.player.set_sleep(30 * 60)
    shell.open_now_playing()
    later(200, shell.now_playing.open_sleep)
elif TARGET == "playing-problem":
    ui.player.backend.problem = {
        "kind": "helper-missing",
        "text": "Playback needs a helper that isn't set up. "
                "See Settings, Playback engine."}
    ui.player.backend.status = "Paused"
    ui.player.backend._push()
    shell.open_now_playing()
elif TARGET == "library":
    shell.show("library")
elif TARGET == "library-songs":
    shell.show("library")
    library.pick("liked")
elif TARGET == "library-phone":
    shell.show("library")
    library.pick("phone")
elif TARGET == "library-phone-lower":
    shell.show("library")
    library.pick("phone")
    scroll_to(lambda: library.body.get_parent().get_parent(), 1.0)
elif TARGET == "settings":
    shell.show("settings")
elif TARGET == "settings-lower":
    shell.show("settings")
    scroll_to(lambda: settings.body.get_parent().get_parent(), 0.55)
elif TARGET == "settings-end":
    shell.show("settings")
    scroll_to(lambda: settings.body.get_parent().get_parent(), 1.0)
elif TARGET == "sheet-cap":
    shell.show("settings")
    later(300, settings.open_cap)
elif TARGET.startswith("engine"):
    shell.show("settings")
    from xx_radio.ui import playback_engine

    def _engine():
        page = playback_engine.EnginePage(ui, ui.hooks.engine_control)
        shell.push(page.widget, "Playback engine")
        if TARGET == "engine-updating":
            later(200, page.update)

    later(100, _engine)
elif TARGET in ("signin", "signin-error"):
    ui.action("sign-in")
    if TARGET == "signin-error":
        def _fail():
            page = top_page()
            sign = page.xx_page
            sign.paste.set_text("cookie: (pasted)")
            sign.sign_in()
            scroll_to(lambda: find_scroller(top_page()), 1.0)
        later(300, _fail)
elif TARGET == "oauth":
    ui.action("sign-in")

    def _oauth():
        page = top_page().xx_page
        page.open_oauth()

        def _code():
            oauth = top_page().xx_page
            oauth.client_id.set_text("1234-demo.apps.googleusercontent.com")
            oauth.client_secret.set_text("demo-secret")
            oauth.start()

        later(200, _code)

    later(200, _oauth)
elif TARGET == "log":
    shell.show("settings")
    settings.open_log()
elif TARGET == "licenses":
    shell.show("settings")
    later(100, lambda: settings.open_licenses(
        ui.hooks.licenses()))
elif TARGET == "banner-changed":
    for _ in range(3):
        ui.health.note_error("search", ErrorKind.SOURCE_CHANGED)
    shell.sync_banner()
elif TARGET == "banner-helper":
    for _ in range(3):
        ui.health.note_error("stream", ErrorKind.HELPER_MISSING)
    shell.sync_banner()
elif TARGET == "banner-offline":
    ui.health.note_error("home", ErrorKind.OFFLINE)
    shell.sync_banner()
    later(400, lambda: shell.pages["home"]._failed(
        type("E", (), {"kind": ErrorKind.OFFLINE})()))
elif TARGET == "banner-signedout":
    ui.health.note_error("library", ErrorKind.AUTH_EXPIRED)
    shell.sync_banner()
elif TARGET == "banner-mix":
    ui.player.backend.offline = True
    ui.player.backend._push()
elif TARGET.startswith("firstrun"):
    def _start():
        screen = shell.first_run_widget
        if screen is not None and TARGET != "firstrun":
            screen.xx_screen.start()
    later(500, _start)
