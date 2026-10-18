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
# Busy squares hold still: headless frames are scarce, and a blink that
# lands after the last one leaves the window unpainted at capture time.
_common.BusySquare._blink = lambda self: True


def later(ms, fn):
    GLib.timeout_add(ms, lambda: (fn(), False)[1])  # noqa: F821


def skip(get_box, count, wait=600):
    """Show a page's lower part: hide its first ``count`` children once
    the page has painted. A late scroll never lands headless (each one
    re-allocates after the last frame), so the renders cut instead."""
    def hide():
        child = get_box().get_first_child()
        hidden = 0
        while child is not None and hidden < count:
            if child.get_visible():
                child.set_visible(False)
                hidden += 1
            child = child.get_next_sibling()

    later(wait, hide)


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
    skip(lambda: shell.pages["home"].body, 5)
elif TARGET == "home-end":
    skip(lambda: shell.pages["home"].body, 9)
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
    _artist = pages.open_artist(ui, "ar-lumen", "Lumen Drive")
    skip(lambda: _artist.body, 4)
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
    skip(lambda: library.body, 5)
elif TARGET == "settings":
    shell.show("settings")
elif TARGET == "kill-confirm":
    from xx_music.ui.kill import request_kill

    later(300, lambda: request_kill(ui, "ar-north", "North Hollow"))
elif TARGET == "kill-toast":
    later(300, lambda: ui.kill_artist("ar-north", "North Hollow"))
elif TARGET == "killed-artists":
    ui.blocked.block("ar-north", "North Hollow")
    ui.blocked.block("", "Copper Tide")
    shell.show("settings")
    later(200, settings.open_blocked)
elif TARGET in ("cast-sheet", "casting", "casting-sheet"):
    # A stand-in speaker list (the local backend has no Cast devices).
    _speakers = [{"id": "sp-1", "name": "Living Room", "model": "Nest Audio"},
                 {"id": "sp-2", "name": "Kitchen", "model": ""}]
    _real_snapshot = ui.player.backend.snapshot

    def _snapshot():
        snap = _real_snapshot()
        cast = dict(snap["cast"], available=True, devices=_speakers)
        if TARGET != "cast-sheet":
            cast.update(connectedId="sp-1", connectedName="Living Room",
                        playing=True, volume=0.4)
        snap["cast"] = cast
        return snap

    ui.player.backend.snapshot = _snapshot
    ui.player.backend._push()
    shell.open_now_playing()
    if TARGET != "casting":
        from xx_music.ui import cast as _cast_ui

        later(200, lambda: _cast_ui.open_cast_sheet(ui))
elif TARGET == "minimal-on":
    # Live: the regular window, then Minimal Mode switched on in Settings.
    shell.show("settings")
    later(300, lambda: settings.set_minimal(True))
elif TARGET == "minimal-off":
    shell.show("settings")
    later(300, lambda: settings.set_minimal(False))
elif TARGET == "minimal-on-home":
    later(300, lambda: settings.set_minimal(True))
elif TARGET == "settings-lower":
    shell.show("settings")
    skip(lambda: settings.body, 3)
elif TARGET == "settings-end":
    shell.show("settings")
    skip(lambda: settings.body, 4)
elif TARGET == "settings-about":
    # The About block alone: every box above the last one is hidden.
    shell.show("settings")

    def _above_about():
        count, child = 0, settings.body.get_first_child()
        while child is not None and child.get_next_sibling() is not None:
            count += 1
            child = child.get_next_sibling()
        return count

    later(500, lambda: skip(lambda: settings.body, _above_about(), 0))
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
            skip(lambda: sign.body, 5, 100)
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
elif TARGET == "welcome":
    from xx_radio.ui import account

    later(200, lambda: account.show_welcome(ui, ui.hooks.open_sign_in))
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
elif TARGET.startswith("firstrun") and TARGET != "firstrun":
    def _start():
        screen = shell.first_run_widget
        if screen is None:
            return True  # the first-run check is still on the worker
        screen.xx_screen.start()
        return False

    GLib.timeout_add(50, _start)  # noqa: F821
