"""Fresh-interpreter driver for the Minimal Mode live test.

Run like broadway_probe_app.py (GDK_BACKEND=broadway against a live
gtk4-broadwayd, see test_minimal_mode.py). Builds the REAL window over
the fake engine, with art urls on everything and an art loader whose
fetch only records (no network), then:

1. regular: visits the screens and expects art requests;
2. switches Minimal Mode on from Settings and expects every screen
   rebuilt in words (tabs, mini player, Now Playing, Home rows, a page
   pushed before the switch) and not one art request while it is on;
3. switches it off and expects the regular screens and art back;
4. builds a second window with the setting saved and expects it minimal
   from the first paint, with no art request at all.

Prints FAIL: lines and exits non-zero on any failed expectation.
"""

from __future__ import annotations

import dataclasses
import gc
import sys
import threading

ART = "https://art.invalid/cover.jpg"


def _arted(value):
    """``value`` with an art url on every Item, Track and page in it."""
    if isinstance(value, (list, tuple)):
        return type(value)(_arted(v) for v in value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        changes = {}
        for field in dataclasses.fields(value):
            current = getattr(value, field.name)
            if field.name == "art_url" and not current:
                changes[field.name] = ART
            elif isinstance(current, (list, tuple)) or (
                    dataclasses.is_dataclass(current)
                    and not isinstance(current, type)):
                changes[field.name] = _arted(current)
        return dataclasses.replace(value, **changes) if changes else value
    return value


def main() -> int:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk

    from xx_music.fake_engine import FakeEngine
    from xx_music.queue import Queue
    from xx_music.tasks import Worker
    from xx_music.ui import menus, minimal, pages
    from xx_music.ui.art import ArtLoader
    from xx_music.ui.context import UiContext
    from xx_music.ui.player import PLAYING, LocalBackend, PlayerModel
    from xx_radio.main import SPEC
    from xx_radio.ui.window import build_window

    if Gtk.init_check() is not True or Gdk.Display.get_default() is None:
        print("FAIL: no Broadway display")
        return 1

    class ArtEngine(FakeEngine):
        def __getattribute__(self, name):
            attr = super().__getattribute__(name)
            if name in ("home", "search", "album", "artist", "playlist",
                        "library", "charts", "mood_shelves"):
                return lambda *a, **k: _arted(attr(*a, **k))
            return attr

    class CountingArt(ArtLoader):
        def __init__(self) -> None:
            self.fetched: list[str] = []
            super().__init__(None, fetch=self._fetch,
                             post=lambda cb: GLib.idle_add(
                                 lambda: (cb(), False)[1]))
            self.asked = 0

        def _fetch(self, url: str) -> bytes:
            self.fetched.append(url)
            return b""

        def request(self, url, px, deliver) -> None:
            self.asked += 1
            super().request(url, px, deliver)

    errors: list = []
    sys.excepthook = lambda _t, value, _tb: errors.append(repr(value))
    threading.excepthook = lambda args: errors.append(repr(args.exc_value))
    failures: list[str] = []

    def expect(ok: bool, what: str) -> None:
        if not ok:
            failures.append(what)
            print(f"FAIL: {what}")

    def pump(ms: int) -> None:
        loop = GLib.MainLoop()
        GLib.timeout_add(ms, lambda: (loop.quit(), False)[1])
        loop.run()

    def texts(widget) -> list[str]:
        found = []
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Label) and child.get_mapped():
                found.append(child.get_text())
            found.extend(texts(child))
            child = child.get_next_sibling()
        return found

    def count(widget, type_name: str) -> int:
        total = 0
        child = widget.get_first_child()
        while child is not None:
            if type(child).__name__ == type_name:
                total += 1
            total += count(child, type_name)
            child = child.get_next_sibling()
        return total

    app = Adw.Application(application_id=SPEC.app_id + ".MinimalProbe",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    if app.register(None) is not True:
        print("FAIL: app registration failed")
        return 1

    def window_with(art: CountingArt):
        engine = ArtEngine()
        queue = Queue()
        queue.replace([_arted(t) for t in engine.search("o").tracks], 0)
        player = PlayerModel(LocalBackend(queue, PLAYING, 42.0),
                             clock=lambda: 0.0)
        holder = {}

        def factory(spec, eng, hooks):
            holder["ui"] = UiContext(spec, eng, Worker(), player, art,
                                     hooks=hooks)
            return holder["ui"]

        window = build_window(app, SPEC, engine, player=player,
                              ui_factory=factory, welcome=False)
        window.present()
        return window, window.xx_shell

    def tour(shell) -> None:
        """Every screen that can show art."""
        ui = shell.ui
        for key in ("home", "search", "library", "settings"):
            shell.show(key)
            pump(150)
        shell.show("search")
        shell.pages["search"].submit("o")
        pump(300)
        shell.show("home")
        pages.open_album(ui, "al-tide", "Tidewater")
        pump(300)
        pages.open_artist(ui, "ar-lumen", "Lumen Drive")
        pump(300)
        pages.open_playlist(ui, "pl-drive", "Night Drive")
        pump(300)
        shell.show("home")
        shell.show("home")
        for view in ("art", "lyrics", "queue"):
            shell.open_now_playing(view)
            pump(250)
        shell.close_now_playing()
        menus.track_menu(ui, ui.player.track)
        pump(200)

    # 1. regular -------------------------------------------------------------
    art = CountingArt()
    window, shell = window_with(art)
    ui = shell.ui
    pump(600)
    tour(shell)
    expect(art.asked > 0, "regular screens asked for no art")
    expect(not minimal.active(), "Minimal Mode on by default")
    expect(shell.tab_buttons["home"].get_label() == "HOME",
           "regular tabs are not caps")

    # a page pushed before the switch must rebuild too
    shell.show("search")
    album = pages.open_album(ui, "al-tide", "Tidewater")
    pump(300)

    # 2. on, live, from Settings --------------------------------------------
    shell.show("settings")
    pump(200)
    settings = shell.pages["settings"]
    asked = art.asked
    settings.set_minimal(True)
    pump(400)
    expect(minimal.active(), "Settings did not switch Minimal Mode on")
    expect(ui.store.prefs().get("minimal_mode") is True,
           "Minimal Mode was not saved")
    words = {k: b.get_child().get_text()
             for k, b in shell.tab_buttons.items()}
    expect(words.get("settings") == "SETTINGS"
           and words.get("home") == "home",
           f"tabs are not words: {words}")
    expect(shell.mini.play.get_child().get_text() in ("[ pause ]",
                                                      "[ play ]"),
           "mini player has no bracket play")
    expect(shell.now_playing.words, "Now Playing did not rebuild")
    expect(shell.now_playing.cover_slot.get_first_child() is None,
           "Now Playing kept a cover")
    shell.show("search")
    pump(150)
    header = texts(album.header_slot)
    expect("back" in header, f"pushed page header not rebuilt: {header}")
    shell.show("home")
    pump(200)
    home = texts(shell.pages["home"].body)
    expect(any(" — " in t for t in home),
           "Home shelves are not Title — Artist rows")
    tour(shell)
    expect(count(window, "XXMusicSquare") == 0,
           "an art tile is on screen in Minimal Mode")
    expect(art.asked == asked,
           f"{art.asked - asked} art requests while Minimal Mode is on")
    fetched = len(art.fetched)
    art.request(ART + "?direct", 96, lambda _t: None)
    pump(100)
    expect(len(art.fetched) == fetched and art.refused >= 1,
           "the art loader fetched while Minimal Mode is on")
    gc.collect()
    listeners = minimal.listener_count()
    for _ in range(3):
        ui.set_minimal(False)
        pump(50)
        ui.set_minimal(True)
        pump(50)
    gc.collect()
    expect(minimal.listener_count() <= listeners,
           f"listeners grew: {listeners} -> {minimal.listener_count()}")

    # 3. off again -------------------------------------------------------------
    shell.show("settings")
    pump(100)
    asked = art.asked
    settings.set_minimal(False)
    pump(400)
    expect(not minimal.active(), "Minimal Mode did not switch off")
    expect(ui.store.prefs().get("minimal_mode") is False,
           "switching off was not saved")
    expect(shell.tab_buttons["home"].get_label() == "HOME",
           "tabs did not come back")
    expect(not shell.now_playing.words, "Now Playing stayed minimal")
    tour(shell)
    expect(art.asked > asked, "art did not come back")
    window.destroy()
    pump(100)

    # 4. saved: minimal from the first paint ---------------------------------
    ui.store.set_pref("minimal_mode", True)
    art2 = CountingArt()
    window2, shell2 = window_with(art2)
    pump(600)
    expect(minimal.active(), "the saved setting was not applied at start")
    expect(not art2.enabled, "the art loader is on at a minimal start")
    tour(shell2)
    expect(art2.asked == 0 and not art2.fetched,
           f"{art2.asked} art requests in a minimal start")
    window2.destroy()
    pump(100)

    for message in errors:
        print(f"ERROR:{message}")
    print("DONE")
    return 1 if failures or errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
