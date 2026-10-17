"""Now Playing, its sheets, and the mini-player.

Mirrors ui/player/NowPlayingScreen.kt: the cover centered (capped so the
transport stays on screen), then left-aligned NOW PLAYING, the title and
author (both open the Book, as does the cover), the chapter, the
book-level SeekBar with the position and the time remaining under it,
time left in the book and the chapter at the current speed, the
transport (skip-seconds glyphs, a 72 px play / pause) and a row of five
sheet buttons — Chapters, Speed, Sleep, Bookmark, Play on… (Play on…
casts to a zone, ui/zones.py; it asks first). The skip glyphs follow
Setup's intervals live. The mini-player sits above the bottom bar on
every other tab. Everything reads ctx.player; commands go back through
it (worker thread -> player service).
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib, Gtk  # noqa: E402

from xx_audiobook import fmt  # noqa: E402
from xx_audiobook import prefs as prefs_module  # noqa: E402
from xx_audiobook import timeline as timeline_module  # noqa: E402
from xx_audiobook.ui import common  # noqa: E402
from xx_audiobook.ui.book import chapter_row  # noqa: E402
from xx_audiobook.ui.library import deferred  # noqa: E402
from xx_ui import widgets  # noqa: E402

SLEEP_PRESETS = (5, 10, 15, 30, 45, 60)
EMPTY_LINE = "Nothing playing. Pick a book in the Library."
COVER_PX = 220


def _key(child: Gtk.Widget, text: str, callback,
         cls: str = "xx-ab-key") -> Gtk.Button:
    button = Gtk.Button()
    button.add_css_class(cls)
    button.set_child(child)
    button.set_halign(Gtk.Align.CENTER)
    button.set_valign(Gtk.Align.CENTER)
    button.connect("clicked", lambda _b: callback())
    return common.name(button, text)


def _sheet_button(icon: str, caption: str, spoken: str,
                  callback) -> Gtk.Button:
    """SheetButton: an icon over a small label, no chrome."""
    button = Gtk.Button()
    button.add_css_class("xx-ab-sheetbtn")
    box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 4)
    box.set_valign(Gtk.Align.CENTER)
    image = widgets.icon_image(icon, 24)
    image.set_halign(Gtk.Align.CENTER)
    box.append(image)
    text = common.label(caption, "xx-ab-num", xalign=0.5, chars=8)
    box.append(text)
    button.set_child(box)
    button.connect("clicked", lambda _b: callback())
    return common.name(button, spoken)


def _skip_seconds() -> tuple:
    prefs = prefs_module.load()
    return prefs.skip_back_seconds, prefs.skip_seconds


def remaining_line(position_ms: int, duration_ms: int, speed: float,
                   chapter_end_ms: int | None) -> str:
    """'3:12:40 left in book · 12:05 left in chapter at 1.25×'."""
    if duration_ms <= 0:
        return ""
    parts = [fmt.format_ms(fmt.scaled_ms(duration_ms - position_ms, speed))
             + " left in book"]
    if chapter_end_ms is not None and chapter_end_ms > position_ms:
        parts.append(fmt.format_ms(fmt.scaled_ms(
            chapter_end_ms - position_ms, speed)) + " left in chapter")
    line = " · ".join(parts)
    if abs(speed - 1.0) > 0.01:
        line += " at " + fmt.speed_text(speed)
    return line


class NowPlayingPage:
    def __init__(self, ctx, open_book, window_getter) -> None:
        self.ctx = ctx
        self._open_book = open_book
        self._window = window_getter
        self._cover_for = None
        self._skips = _skip_seconds()
        self.stack = Gtk.Stack()
        empty = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        empty.add_css_class("xx-page")
        line = common.muted(EMPTY_LINE)
        line.set_margin_start(16)
        line.set_margin_end(16)
        line.set_vexpand(True)
        line.set_valign(Gtk.Align.CENTER)
        empty.append(line)
        self.stack.add_named(empty, "empty")
        self.stack.add_named(self._build(), "full")
        self.widget = self.stack
        ctx.player.connect(self.update)
        ctx.connect("settings-changed", self._settings_changed)
        ctx.connect("zones-changed", self._cells)
        self.update()

    def _settings_changed(self) -> None:
        """Setup changed the skip intervals: redraw both glyphs."""
        def _done(skips, error) -> None:
            if error is not None or skips == self._skips:
                return
            self._skips = skips
            back_s, forward_s = skips
            self.back_key.set_child(common.skip_glyph(back_s, False, 32))
            common.name(self.back_key, f"Back {back_s} seconds")
            self.forward_key.set_child(common.skip_glyph(forward_s, True, 32))
            common.name(self.forward_key, f"Forward {forward_s} seconds")

        self.ctx.run(_skip_seconds, _done)

    def _build(self) -> Gtk.Widget:
        body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
        body.set_margin_top(16)
        body.set_margin_bottom(16)

        self.cover_slot = Gtk.Box()
        self.cover_slot.set_halign(Gtk.Align.CENTER)
        cover_button = Gtk.Button()
        cover_button.add_css_class("xx-ab-tile")
        cover_button.set_child(self.cover_slot)
        cover_button.set_halign(Gtk.Align.CENTER)
        cover_button.connect("clicked", lambda _b: self._open())
        common.name(cover_button, "Open the book")
        body.append(cover_button)

        head = common.caps("Now playing")
        head.set_margin_top(4)
        body.append(head)
        texts = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self.title = common.label("", "xx-ab-np-title", lines=2, chars=18)
        self.author = common.label("", "xx-ab-b15 xx-ab-muted", chars=26)
        texts.append(self.title)
        texts.append(self.author)
        title_button = common.flat(texts, "Open the book", self._open)
        common.name(title_button, "Open the book")
        body.append(title_button)
        self.chapter = common.label("", "xx-ab-t14", lines=2, chars=28)
        body.append(self.chapter)
        self.note = common.label("", "xx-ab-b13 xx-ab-muted", lines=2,
                                 chars=34)
        body.append(self.note)

        seek = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self.scrubber = common.Scrubber()
        self.scrubber.connect("seek",
                              lambda _s, ms: self.ctx.player.seek_to(ms))
        seek.append(self.scrubber)
        times = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        self.elapsed = common.label("", "xx-ab-l12 xx-ab-num", chars=9)
        self.elapsed.set_hexpand(True)
        self.total = common.label("", "xx-ab-l12 xx-ab-muted xx-ab-num",
                                  xalign=1.0, chars=10)
        times.append(self.elapsed)
        times.append(self.total)
        seek.append(times)
        body.append(seek)
        self.left = common.label("", "xx-ab-l12 xx-ab-muted xx-ab-num",
                                 lines=2, chars=40)
        body.append(self.left)

        player = self.ctx.player
        back_s, forward_s = self._skips
        self.back_key = _key(common.skip_glyph(back_s, False, 32),
                             f"Back {back_s} seconds",
                             lambda: player.skip(-self._skips[0]))
        self.forward_key = _key(common.skip_glyph(forward_s, True, 32),
                                f"Forward {forward_s} seconds",
                                lambda: player.skip(self._skips[1]))
        transport = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        self.play_icon = widgets.icon_image("media-playback-start-symbolic",
                                            56)
        self.play = _key(self.play_icon, "Play", player.play_pause,
                         "xx-ab-play")
        for widget in (
                _key(widgets.icon_image("media-skip-backward-symbolic", 24),
                     "Previous chapter",
                     lambda: player.chapter_step(False)),
                self.back_key,
                self.play,
                self.forward_key,
                _key(widgets.icon_image("media-skip-forward-symbolic", 24),
                     "Next chapter", lambda: player.chapter_step(True))):
            widget.set_hexpand(True)
            transport.append(widget)
        body.append(transport)

        self.cells = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 4)
        self.cells.set_homogeneous(True)
        body.append(self.cells)
        self.error = common.label("", "xx-ab-b13 xx-ab-error", lines=2,
                                  chars=34)
        body.append(self.error)
        page = Gtk.ScrolledWindow()
        page.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        body.add_css_class("xx-page-body")
        page.set_child(body)
        return page

    def _open(self) -> None:
        if self.ctx.player.item_id:
            self._open_book(self.ctx.player.item_id, None)

    def update(self) -> None:
        player = self.ctx.player
        if not player.has_book:
            self.stack.set_visible_child_name("empty")
            return
        self.stack.set_visible_child_name("full")
        if self._cover_for != player.item_id:
            self._cover_for = player.item_id
            common.clear(self.cover_slot)
            self.cover_slot.append(common.cover(
                self.ctx, player.item_id, player.title, COVER_PX, True,
                "full"))
        self.title.set_text(player.title or "")
        self.author.set_text(player.author or "")
        self.author.set_visible(bool(player.author))
        current = player.chapter()
        self.chapter.set_text(current.title if current else "")
        self.chapter.set_visible(current is not None)
        note = ("Paused where you left off. Press play to continue."
                if player.restored else "Starting…" if player.busy else "")
        self.note.set_text(note)
        self.note.set_visible(bool(note))
        if not self.scrubber.dragging:
            self.scrubber.set_state(player.position_ms, player.duration_ms,
                                    [c.start_ms for c in player.chapters])
        shown = self.scrubber.shown_ms
        self.elapsed.set_text(fmt.format_ms(shown))
        self.total.set_text(
            "-" + fmt.format_ms(max(player.duration_ms - shown, 0)))
        self.left.set_text(remaining_line(
            player.position_ms, player.duration_ms, player.rate,
            player.chapter_end_ms() if player.chapters else None))
        self.left.set_visible(player.duration_ms > 0)
        self.play_icon.set_from_icon_name(
            "media-playback-pause-symbolic" if player.playing
            else "media-playback-start-symbolic")
        common.name(self.play, "Pause" if player.playing else "Play")
        self.error.set_text(player.error or "")
        self.error.set_visible(bool(player.error))
        self._cells()

    def _cells(self) -> None:
        player = self.ctx.player
        common.clear(self.cells)
        sleeping = player.sleep_remaining_ms > 0 or player.sleep_end_of_chapter
        speed = fmt.speed_text(player.rate)
        sleep = fmt.format_ms(player.sleep_remaining_ms) if sleeping \
            else "Sleep"
        for cell in (
                _sheet_button("view-list-bullet-symbolic", "Chapters",
                              "Chapters", self.open_chapters),
                _sheet_button("xx-ab-speed-symbolic", speed,
                              f"Speed {speed}", self.open_speed),
                _sheet_button("weather-clear-night-symbolic", sleep,
                              "Sleep timer, " + (
                                  ("stops in " + sleep) if sleeping
                                  else "off"), self.open_sleep),
                _sheet_button("bookmark-new-symbolic", "Bookmark",
                              "Add a bookmark", self.open_bookmark),
                self._play_on_cell()):
            self.cells.append(cell)

    def _play_on_cell(self) -> Gtk.Button:
        from xx_audiobook import zones as zones_module

        output = self.ctx.zones.snapshot().output
        zone = output if isinstance(output, zones_module.ZoneOutput) else None
        return _sheet_button(
            "xx-ab-cast-symbolic", "Play on…",
            f"Play on a zone, now on {zone.name}" if zone
            else "Play on a zone", self.open_play_on)

    def open_play_on(self) -> Gtk.Widget:
        from xx_audiobook.ui import zones as zones_ui

        return zones_ui.play_on_sheet(self.ctx, self._window())

    # -- sheets --------------------------------------------------------------------

    def _sheet(self, title: str) -> tuple:
        return common.sheet(title)

    def _present(self, dialog):
        return common.present(dialog, self._window())

    def open_chapters(self) -> Gtk.Widget:
        player = self.ctx.player
        dialog, box = self._sheet("Chapters")
        if not player.chapters:
            box.append(common.muted("This book has no chapters."))
            return self._present(dialog)
        rows = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        for index, chapter in enumerate(player.chapters):
            def _go(c=chapter) -> None:
                player.seek_to(c.start_ms)
                dialog.close()
            rows.append(chapter_row(
                chapter.title or f"Chapter {index + 1}", chapter.start_ms,
                max(chapter.end_ms - chapter.start_ms, 0),
                index == player.chapter_index, _go))
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(360)
        scroll.set_child(rows)
        box.append(scroll)
        target = max(player.chapter_index - 2, 0)

        def _scroll() -> bool:
            child = rows.get_first_child()
            for _ in range(target):
                child = child.get_next_sibling() if child else None
            if child is not None:
                ok, point = child.compute_point(rows, _point())
                if ok:
                    scroll.get_vadjustment().set_value(point.y)
            return False

        GLib.timeout_add(150, _scroll)
        return self._present(dialog)

    def open_speed(self) -> Gtk.Widget:
        """Speed: 'Remembered for this book.' and the steps as chips."""
        player = self.ctx.player
        dialog, box = self._sheet("Speed")
        box.append(common.para("Remembered for this book.",
                               "xx-ab-b11 xx-ab-muted"))
        flow = common.chip_flow([])
        box.append(flow)

        def _set(rate: float) -> None:
            player.set_rate(rate)
            _chips()

        def _chips() -> None:
            common.fill_flow(flow, [
                (fmt.speed_text(step), abs(step - player.rate) < 0.01,
                 lambda s=step: _set(s), f"Speed {fmt.speed_text(step)}")
                for step in timeline_module.SPEED_STEPS])

        _chips()
        return self._present(dialog)

    def open_sleep(self) -> Gtk.Widget:
        player = self.ctx.player
        dialog, box = self._sheet("Sleep timer")
        status = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        box.append(status)
        flow = common.chip_flow([])
        box.append(flow)
        custom = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
        minutes_entry = deferred(widgets.text_entry("Custom minutes"))
        minutes_entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
        minutes_entry.set_max_length(3)
        minutes_entry.set_hexpand(True)
        minutes_entry.update_property([Gtk.AccessibleProperty.LABEL],
                                      ["Custom minutes"])

        def _custom() -> None:
            text = "".join(ch for ch in minutes_entry.get_text()
                           if ch.isdigit())
            if text and int(text) > 0:
                minutes_entry.set_text("")
                _do(lambda: player.sleep_minutes(int(text)))

        minutes_entry.connect("activate", lambda _e: _custom())
        custom.append(minutes_entry)
        set_button = widgets.text_button("Set", _custom)
        common.fit(set_button, 6)
        custom.append(set_button)
        box.append(custom)

        def _refresh() -> None:
            common.clear(status)
            running = player.sleep_remaining_ms > 0 or \
                player.sleep_end_of_chapter
            if running:
                row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
                line = common.label(
                    ("Stops at the chapter end, in "
                     if player.sleep_end_of_chapter else "Stops in ")
                    + fmt.format_ms(player.sleep_remaining_ms),
                    "xx-ab-t14 xx-ab-num", lines=2, chars=18)
                line.set_hexpand(True)
                row.append(line)
                more = widgets.text_button("+5 min", lambda: _do(
                    lambda: player.sleep_minutes(
                        player.sleep_remaining_ms // 60000 + 5)))
                common.fit(more, 7)
                row.append(more)
                off = widgets.text_button("Off", lambda: _do(
                    player.cancel_sleep))
                common.fit(off, 4)
                row.append(off)
                status.append(row)
            else:
                status.append(common.para(
                    "Off. Fades out over the last 10 seconds.",
                    "xx-ab-b11 xx-ab-muted"))
            minutes = (round(player.sleep_remaining_ms / 60000)
                       if running and not player.sleep_end_of_chapter else 0)
            items = [(f"{m} min", minutes == m,
                      lambda m=m: _do(lambda: player.sleep_minutes(m)))
                     for m in SLEEP_PRESETS]
            items.append(("End of chapter", player.sleep_end_of_chapter,
                          lambda: _do(player.sleep_end_of_chapter_now)))
            common.fill_flow(flow, items)

        def _do(action) -> None:
            action()
            _refresh()

        _refresh()
        return self._present(dialog)

    def open_bookmark(self) -> Gtk.Widget:
        player = self.ctx.player
        ctx = self.ctx
        at_ms = player.position_ms
        dialog, box = self._sheet("Bookmark at " + fmt.format_ms(at_ms))
        entry = deferred(widgets.text_entry("Note (optional)"))
        entry.set_hexpand(True)
        entry.update_property([Gtk.AccessibleProperty.LABEL],
                              ["Note (optional)"])
        box.append(entry)
        listing = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        item_id = player.item_id

        def _add() -> None:
            note = entry.get_text()
            entry.set_text("")

            def _work():
                ctx.bookmarks.add(item_id, at_ms / 1000.0, note)
                return ctx.bookmarks.observe(item_id)

            ctx.run(_work, _show)

        add = widgets.primary_button("Add bookmark", _add)
        common.fit(add, 16)
        add.set_halign(Gtk.Align.START)
        box.append(add)
        box.append(listing)

        def _show(marks, error) -> None:
            common.clear(listing)
            if error is not None or not marks:
                return
            listing.append(common.label("This book", "xx-ab-t14", chars=20))
            for mark in marks:
                row = Gtk.Button()
                row.add_css_class("xx-ab-chapter")
                text = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
                where = fmt.format_ms(int(mark.position_s * 1000))
                text.append(common.label(where, "xx-ab-l13 xx-ab-num",
                                         chars=10))
                if mark.note:
                    text.append(common.label(mark.note, "xx-ab-b11", lines=2,
                                             chars=30))
                row.set_child(text)
                common.name(row, f"Go to bookmark at {where}"
                            + (f", {mark.note}" if mark.note else ""))
                row.connect("clicked", lambda _b, m=mark: (
                    player.seek_to(int(m.position_s * 1000)), dialog.close()))
                listing.append(row)

        ctx.run(lambda: ctx.bookmarks.observe(item_id), _show)
        return self._present(dialog)


def _point():
    gi.require_version("Graphene", "1.0")
    from gi.repository import Graphene

    return Graphene.Point().init(0, 0)


class MiniPlayer:
    """A hairline on top, the book's progress as a 3 px line, cover +
    title + time (tap opens Now Playing), skip back and play / pause."""

    def __init__(self, ctx, open_playing) -> None:
        self.ctx = ctx
        self._cover_for = None
        player = ctx.player
        back_s, _forward = _skip_seconds()
        self.widget = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self.widget.add_css_class("xx-ab-mini")
        self.bar = common.line(0.0)
        self.bar.update_property([Gtk.AccessibleProperty.LABEL], [""])
        self.widget.append(self.bar)
        row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        row.set_margin_start(12)
        row.set_margin_end(4)
        row.set_margin_top(4)
        row.set_margin_bottom(4)
        opener = Gtk.Button()
        opener.add_css_class("xx-ab-mini-open")
        opener.set_hexpand(True)
        inner = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        self.cover_slot = Gtk.Box()
        self.cover_slot.set_valign(Gtk.Align.CENTER)
        inner.append(self.cover_slot)
        texts = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        texts.set_valign(Gtk.Align.CENTER)
        texts.set_hexpand(True)
        self.title = common.label("", "xx-ab-t14", chars=18)
        self.sub = common.label("", "xx-ab-l10 xx-ab-muted xx-ab-num",
                                chars=24)
        texts.append(self.title)
        texts.append(self.sub)
        inner.append(texts)
        opener.set_child(inner)
        opener.connect("clicked", lambda _b: open_playing())
        self.opener = opener
        row.append(opener)
        self._back_s = back_s
        self.back_key = _key(common.skip_glyph(back_s, False, 28),
                             f"Back {back_s} seconds",
                             lambda: player.skip(-self._back_s))
        row.append(self.back_key)
        ctx.connect("settings-changed", self._settings_changed)
        self.play_icon = widgets.icon_image("media-playback-start-symbolic",
                                            24)
        self.play = _key(self.play_icon, "Play", player.play_pause)
        row.append(self.play)
        self.widget.append(row)
        player.connect(self.update)
        self.update()

    def _settings_changed(self) -> None:
        def _done(skips, error) -> None:
            if error is None and skips[0] != self._back_s:
                self._back_s = skips[0]
                self.back_key.set_child(
                    common.skip_glyph(self._back_s, False, 28))
                common.name(self.back_key, f"Back {self._back_s} seconds")

        self.ctx.run(_skip_seconds, _done)

    def update(self) -> None:
        player = self.ctx.player
        if not player.has_book:
            return
        if self._cover_for != player.item_id:
            self._cover_for = player.item_id
            common.clear(self.cover_slot)
            self.cover_slot.append(common.cover(
                self.ctx, player.item_id, player.title, 44, True))
        self.title.set_text(player.title)
        if player.error:
            sub = player.error
        elif player.busy:
            sub = "Starting…"
        else:
            sub = (fmt.format_ms(player.position_ms) + " / "
                   + fmt.format_ms(player.duration_ms))
        self.sub.set_text(sub)
        self.bar.set_fraction(player.position_ms / player.duration_ms
                              if player.duration_ms > 0 else 0.0)
        self.play_icon.set_from_icon_name(
            "media-playback-pause-symbolic" if player.playing
            else "media-playback-start-symbolic")
        common.name(self.play, "Pause" if player.playing else "Play")
        common.name(self.opener,
                    f"Now playing: {player.title}. Open the player.")
