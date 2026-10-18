"""Navigation driver for scripts/snap.py (``win`` and ``app`` in scope).

SNAP_TARGET picks the surface. Headless Broadway never ticks the frame
clock, so an Adw bottom sheet cannot animate open; the harness swaps the
one presentation seam (ui.common.present) for a dock that shows the same
sheet content where the bottom sheet docks it, over a scrim.
"""

import os

from xx_audiobook.ui import common as _common

TARGET = os.environ.get("SNAP_TARGET", "library")
# Headless frame clocks barely tick: land every transition at its end.
Gtk.Settings.get_default().set_property(  # noqa: F821
    "gtk-enable-animations", False)
shell = win._ab  # noqa: F821
_content = win.get_content()  # noqa: F821
win.set_content(None)  # noqa: F821
_overlay = Gtk.Overlay()  # noqa: F821
_overlay.set_child(_content)
win.set_content(_overlay)  # noqa: F821
_css = Gtk.CssProvider()  # noqa: F821
_css.load_from_data(b"""
.snap-scrim { background-color: alpha(black, 0.55); }
.snap-dock { background-color: @card_bg_color; border-top: 1px solid @borders;
  border-radius: 3px 3px 0 0; padding-top: 18px; }
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
ctx = shell.ctx
nav = shell.nav
import demo_seed  # noqa: E402

PLAYING = {
    "has_book": True, "item_id": "ph", "title": "Project Hail Mary",
    "author": "Andy Weir", "status": "Playing",
    "position_us": int(16.2 * 3600 * 0.42 * 1e6),
    "duration_us": int(16.2 * 3600 * 1e6), "rate": 1.25,
    "sleep_remaining_ms": 0, "sleep_end_of_chapter": False,
}


def play(**extra) -> None:
    ctx.player.apply_service(dict(PLAYING, **extra))


def book(item_id: str) -> None:
    nav.open_book(item_id)


def scroll_later(page_widget, value: float) -> None:
    """Pages re-render when fresh data lands; keep asking for a while."""
    tries = [4]

    def _step() -> bool:
        scroller = page_widget().get_last_child()
        adj = scroller.get_vadjustment()
        adj.set_value(value)
        tries[0] -= 1
        return tries[0] > 0

    GLib.timeout_add(150, _step)  # noqa: F821


def lower(cls, keep) -> None:
    """Show a long page from a marker down: a late scroll never lands
    headless, so every render hides the body children before the first
    one ``keep(page, child)`` accepts."""
    render = cls._render

    def _lower(self) -> None:
        render(self)
        child = self.body.get_first_child()
        while child is not None and not keep(self, child):
            child.set_visible(False)
            child = child.get_next_sibling()

    cls._render = _lower


def caption_is(*texts):
    def _keep(_page, child) -> bool:
        return isinstance(child, Gtk.Label) and any(  # noqa: F821
            child.get_text().startswith(t) for t in texts)
    return _keep


def when(ready, action, every: int = 120, tries: int = 40) -> None:
    """Run ``action`` once ``ready()`` holds (data lands on workers)."""
    left = [tries]

    def _poll() -> bool:
        left[0] -= 1
        if ready():
            action()
            return False
        return left[0] > 0

    GLib.timeout_add(every, _poll)  # noqa: F821


def _m45(target: str) -> None:
    from xx_audiobook import download as download_module

    if target == "downloads-wifi":
        def _metered() -> None:
            ctx.downloads.network.on_facts(download_module.Facts(
                internet=True, validated=True, not_metered=False))
            ctx.emit("downloads-changed")

        when(lambda: ctx.downloads is not None, _metered)
        shell.show("downloads")
    elif target == "download-delete":
        play()
        shell.show("downloads")
        page = shell.downloads
        when(lambda: page.view.done,
             lambda: page.ask_delete(page.view.done[0]))
    elif target in ("setup-playback", "setup-bottom"):
        from xx_audiobook.ui.setup import SetupPage

        lower(SetupPage, caption_is(
            "Playback" if target == "setup-playback" else "Downloads"))
        shell.show("setup")
    elif target == "signout":
        shell.show("setup")
        when(lambda: shell.setup.loaded, shell.setup.ask_sign_out)
    elif target == "licenses":
        shell.show("setup")
        when(lambda: ctx.started, nav.licenses)
    elif target == "noaccess":
        def _deny() -> None:
            ctx.session.on_access(False)
            ctx.check_session()

        when(lambda: ctx.started and ctx.signed_in, _deny)
    elif target in ("stats", "stats-lower", "stats-sessions",
                    "stats-delete"):
        from xx_audiobook.ui.stats import StatsPage

        if target == "stats-lower":
            lower(StatsPage, caption_is("By month"))
        elif target in ("stats-sessions", "stats-delete"):
            lower(StatsPage, caption_is("Recent sessions"))
        shell.show("setup")
        when(lambda: ctx.started, nav.stats)
        if target == "stats-delete":
            when(lambda: getattr(shell, "top_page", None) is not None
                 and shell.top_page.state.history().sessions,
                 lambda: shell.top_page.ask_delete(
                     shell.top_page.state.history().sessions[0]))
    elif target in ("zones-cast", "zones-active", "playon-sheet"):
        play()
        shell.show("playing" if target == "playon-sheet" else "zones")

        def _ready() -> bool:
            snap = ctx.zones.snapshot()
            return bool(snap.selected and snap.targets.get(snap.selected))

        def _go() -> None:
            snap = ctx.zones.snapshot()
            kitchen = snap.targets[snap.selected][0]
            if target == "zones-cast":
                from xx_audiobook.ui.zones import play_on

                play_on(ctx, kitchen)
            elif target == "zones-active":
                ctx.zones.play_on(kitchen, ctx.player.item_id,
                                  ctx.player.position_ms / 1000)
                ctx.zones.load_targets(snap.selected)
                ctx.player.status = "Paused"
                ctx.player.changed()
                ctx.emit("zones-changed")
            else:
                shell.playing.open_play_on()

        when(_ready, _go)
    elif target in ("book-reviews", "review-mine", "review-gate",
                    "review-editor", "review-delete"):
        from xx_audiobook.ui.book import BookPage

        def _reviews_first(page, child) -> bool:
            return child is page.reviews.widget

        lower(BookPage, _reviews_first)
        item = {"review-mine": "lw", "review-gate": "du"}.get(target, "ph")
        book(item)

        def _block_ready() -> bool:
            page = getattr(shell, "top_page", None)
            return (page is not None and page.reviews.state is not None
                    and page.reviews.state.ui().page is not None)

        def _act() -> None:
            block = shell.top_page.reviews
            if target == "review-editor":
                block._start_with_stars(4)
                block.text.get_buffer().set_text(
                    "[Draft] A placeholder comment.")
            elif target == "review-delete":
                block.ask_delete(block.state.ui().others[0])

        if target in ("review-editor", "review-delete"):
            when(_block_ready, _act)


if TARGET == "library-grid":
    shell.library.toggle_grid()
    shell.library.folded |= {"continue", "continue_series"}
    shell.library.reload()
    scroll_later(lambda: shell.library.widget, 250)
elif TARGET == "library-search":
    shell.library.entry.set_text("chambers")
elif TARGET == "library-filter":
    shell.library.set_show("downloaded")
elif TARGET == "book":
    play()
    book("ph")
elif TARGET == "book-damaged":
    book("hy")
elif TARGET == "book-chapters":
    # The lower half of the Book (chapters + bookmarks): a late scroll
    # never lands headless, so each render starts at the chapters.
    from xx_audiobook.ui.book import BookPage

    _render = BookPage._render

    def _lower(self) -> None:
        _render(self)
        child = self.body.get_first_child()
        while child is not None and not (
                isinstance(child, Gtk.Label)  # noqa: F821
                and child.get_text().startswith("Chapters")):
            child.set_visible(False)
            child = child.get_next_sibling()

    BookPage._render = _lower
    play()
    book("ph")
elif TARGET == "conflict":
    book("cc")
    GLib.timeout_add(500, lambda: (  # noqa: F821
        shell.top_page.ask_conflict(1520.0, 6125.0, lambda: None),
        False)[1])
elif TARGET == "playing":
    play()
    shell.show("playing")
elif TARGET == "playing-restored":
    shell.show("playing")
elif TARGET.startswith("sheet-"):
    play(sleep_remaining_ms=1500000 if TARGET == "sheet-sleep" else 0)
    shell.show("playing")
    opener = {"sheet-chapters": shell.playing.open_chapters,
              "sheet-speed": shell.playing.open_speed,
              "sheet-sleep": shell.playing.open_sleep,
              "sheet-bookmark": shell.playing.open_bookmark}[TARGET]
    GLib.timeout_add(300, lambda: (opener(), False)[1])  # noqa: F821
elif TARGET == "series":
    nav.browse("series", "Wayfarers", 7)
elif TARGET == "author":
    nav.browse("author", "Becky Chambers",
               demo_seed.AUTHORS["Becky Chambers"])
elif TARGET == "narrator":
    nav.browse("narrator", "Kevin R. Free",
               demo_seed.NARRATORS["Kevin R. Free"])
elif TARGET == "collections":
    nav.collections()
elif TARGET == "collection":
    from xx_audiobook.browse import ListRow

    nav.collection(ListRow("col-hugo", "Hugo winners", "collection",
                           "Best Novel, read aloud.", 5))
elif TARGET in ("downloads", "zones", "setup", "signin"):
    shell.show("setup" if TARGET == "signin" else TARGET)
    seeded = os.environ.get("SNAP_SCENARIO", "seeded") != "empty"
    if TARGET == "zones" and seeded:
        play()
    if TARGET == "signin" and ctx.session.session is not None:
        # A signed-in HOME: sign out (keeps the books) -> Sign in.
        when(lambda: ctx.started and ctx.signed_in, ctx.sign_out)
elif TARGET == "checking":
    # The gate's first frame, held: auth never resolves here.
    shell._gate = lambda: None
    shell.root.set_visible_child_name("checking")
else:
    _m45(TARGET)
