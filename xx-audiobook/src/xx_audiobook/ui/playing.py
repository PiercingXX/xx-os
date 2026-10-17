"""Now Playing, its sheets, and the mini-player (M3).

Mirrors ui/player/NowPlayingScreen.kt: cover and title (both open the
Book), the chapter, a book-level scrubber with chapter ticks, time left
in the book and the chapter at the current speed, transport with
skip-seconds glyphs, and the Chapters / Speed / Sleep / Bookmark /
Play on… sheets (Play on… casts to a zone, ui/zones.py; it asks first).
The skip glyphs follow Setup's intervals live. The mini-player sits above
the bottom bar on
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

SLEEP_PRESETS = (5, 10, 15, 30, 45, 60, 90)
EMPTY_LINE = "Nothing playing. Pick a book in the Library."


def _key(child: Gtk.Widget, text: str, callback,
         cls: str = "xx-ab-key") -> Gtk.Button:
    button = Gtk.Button()
    button.add_css_class(cls)
    button.set_child(child)
    button.set_halign(Gtk.Align.CENTER)
    button.set_valign(Gtk.Align.CENTER)
    button.connect("clicked", lambda _b: callback())
    return common.name(button, text)


def _cell(top: Gtk.Widget, caption: str, spoken: str, callback,
          on: bool = False) -> Gtk.Button:
    button = Gtk.Button()
    button.add_css_class("xx-ab-cell")
    if on:
        button.add_css_class("on")
    box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 4)
    box.set_valign(Gtk.Align.CENTER)
    top.set_halign(Gtk.Align.CENTER)
    box.append(top)
    text = common.label(caption.upper(), "xx-ab-label xx-ab-cell-label",
                        xalign=0.5, chars=9)
    box.append(text)
    button.set_child(box)
    button.connect("clicked", lambda _b: callback())
    return common.name(button, spoken)


def _skip_seconds() -> tuple:
    prefs = prefs_module.load()
    return prefs.skip_back_seconds, prefs.skip_seconds


class NowPlayingPage:
    def __init__(self, ctx, open_book, window_getter) -> None:
        self.ctx = ctx
        self._open_book = open_book
        self._window = window_getter
        self._cover_for = None
        self._skips = _skip_seconds()
        self.stack = Gtk.Stack()
        empty = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        empty.append(widgets.page_header("Playing"))
        line = common.muted(EMPTY_LINE)
        line.add_css_class("xx-page-body")
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
            self.back_key.set_child(common.skip_glyph(back_s, False, 34))
            common.name(self.back_key, f"Back {back_s} seconds")
            self.forward_key.set_child(common.skip_glyph(forward_s, True, 34))
            common.name(self.forward_key, f"Forward {forward_s} seconds")

        self.ctx.run(_skip_seconds, _done)

    def _build(self) -> Gtk.Widget:
        body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        body.set_margin_top(16)
        body.set_margin_bottom(20)
        top = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        head = common.caps("Now playing")
        head.set_hexpand(True)
        top.append(head)
        self.where = common.label("", "xx-ab-label xx-ab-num", xalign=1.0,
                                  chars=14)
        self.where.set_ellipsize(0)  # short; never misfire an ellipsis
        top.append(self.where)
        body.append(top)

        self.cover_slot = Gtk.Box()
        self.cover_slot.set_halign(Gtk.Align.CENTER)
        self.cover_slot.set_margin_top(16)
        cover_button = Gtk.Button()
        cover_button.add_css_class("xx-ab-tile")
        cover_button.set_child(self.cover_slot)
        cover_button.set_halign(Gtk.Align.CENTER)
        cover_button.connect("clicked", lambda _b: self._open())
        common.name(cover_button, "Open the book")
        body.append(cover_button)

        title_button = Gtk.Button()
        title_button.add_css_class("xx-ab-tile")
        title_button.set_margin_top(20)
        texts = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
        self.title = common.label("", "xx-ab-np-title", lines=2, chars=18)
        self.author = common.label("", "xx-secondary", chars=26)
        texts.append(self.title)
        texts.append(self.author)
        title_button.set_child(texts)
        title_button.connect("clicked", lambda _b: self._open())
        common.name(title_button, "Open the book")
        body.append(title_button)
        self.chapter = common.label("", "xx-body", lines=2, chars=28)
        self.chapter.set_margin_top(10)
        body.append(self.chapter)
        self.note = common.label("", "xx-ab-meta", lines=2, chars=34)
        self.note.set_margin_top(4)
        body.append(self.note)

        self.scrubber = common.Scrubber()
        self.scrubber.set_margin_top(14)
        self.scrubber.connect("seek", lambda _s, ms: self.ctx.player.seek_to(ms))
        body.append(self.scrubber)
        times = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        self.elapsed = common.label("", "xx-time xx-ab-num", chars=9)
        self.elapsed.set_hexpand(True)
        self.total = common.label("", "xx-time xx-ab-num", xalign=1.0,
                                  chars=9)
        times.append(self.elapsed)
        times.append(self.total)
        body.append(times)
        self.left = common.label("", "xx-ab-meta xx-ab-num", chars=40)
        self.left.set_margin_top(8)
        body.append(self.left)

        player = self.ctx.player
        back_s, forward_s = self._skips
        self.back_key = _key(common.skip_glyph(back_s, False, 34),
                             f"Back {back_s} seconds",
                             lambda: player.skip(-self._skips[0]))
        self.forward_key = _key(common.skip_glyph(forward_s, True, 34),
                                f"Forward {forward_s} seconds",
                                lambda: player.skip(self._skips[1]))
        transport = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        transport.set_margin_top(18)
        self.play_icon = widgets.icon_image("media-playback-start-symbolic",
                                            32)
        self.play = _key(self.play_icon, "Play", player.play_pause,
                         "xx-ab-play")
        for widget in (
                _key(widgets.icon_image("media-skip-backward-symbolic", 22),
                     "Previous chapter",
                     lambda: player.chapter_step(False)),
                self.back_key,
                self.play,
                self.forward_key,
                _key(widgets.icon_image("media-skip-forward-symbolic", 22),
                     "Next chapter", lambda: player.chapter_step(True))):
            widget.set_hexpand(widget is not self.play)
            transport.append(widget)
        body.append(transport)

        self.cells = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 6)
        self.cells.set_homogeneous(True)
        self.cells.set_margin_top(20)
        body.append(self.cells)
        self.error = common.label("", "xx-secondary", lines=2, chars=34)
        self.error.set_margin_top(12)
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
                self.ctx, player.item_id, player.title, 212, True, "full"))
        self.title.set_text(player.title or "")
        self.author.set_text(player.author or "")
        self.author.set_visible(bool(player.author))
        current = player.chapter()
        self.chapter.set_text(current.title if current else "")
        self.chapter.set_visible(current is not None)
        count = len(player.chapters)
        self.where.set_text(f"{player.chapter_index + 1} / {count}"
                            if current is not None and count else "")
        note = ("Paused where you left off." if player.restored
                else "Starting…" if player.busy else "")
        self.note.set_text(note)
        self.note.set_visible(bool(note))
        if not self.scrubber.dragging:
            self.scrubber.set_state(player.position_ms, player.duration_ms,
                                    [c.start_ms for c in player.chapters])
        self.elapsed.set_text(fmt.format_ms(player.position_ms))
        self.total.set_text(fmt.format_ms(player.duration_ms))
        self.left.set_text(fmt.time_left_line(
            player.position_ms, player.duration_ms, player.rate,
            player.chapter_end_ms()))
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
        speed = common.label(fmt.speed_text(player.rate),
                             "xx-title xx-ab-num", xalign=0.5, chars=6)
        sleep_top = (common.label(fmt.format_ms(player.sleep_remaining_ms),
                                  "xx-title xx-ab-num", xalign=0.5, chars=6)
                     if sleeping else
                     widgets.icon_image("weather-clear-night-symbolic", 20))
        for cell in (
                _cell(widgets.icon_image("view-list-ordered-symbolic", 20),
                      "Chapters", "Chapters", self.open_chapters),
                _cell(speed, "Speed",
                      f"Speed {fmt.speed_text(player.rate)}",
                      self.open_speed, abs(player.rate - 1.0) > 0.01),
                _cell(sleep_top, "Sleep",
                      "Sleep timer, " + ("on" if sleeping else "off"),
                      self.open_sleep, sleeping),
                _cell(widgets.icon_image("bookmark-new-symbolic", 20),
                      "Bookmark", "Add a bookmark", self.open_bookmark),
                self._play_on_cell()):
            self.cells.append(cell)

    def _play_on_cell(self) -> Gtk.Button:
        from xx_audiobook import zones as zones_module

        output = self.ctx.zones.snapshot().output
        zone = output if isinstance(output, zones_module.ZoneOutput) else None
        return _cell(widgets.icon_image("audio-speakers-symbolic", 20),
                     "Play on", f"Play on a zone, now on {zone.name}"
                     if zone else "Play on a zone", self.open_play_on,
                     zone is not None)

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
        player = self.ctx.player
        dialog, box = self._sheet("Speed")
        readout = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
        readout.set_margin_top(4)
        value = common.label(fmt.speed_text(player.rate),
                             "xx-ab-readout xx-ab-num", xalign=0.5, chars=6)
        value.set_hexpand(True)

        def _set(rate: float) -> None:
            player.set_rate(rate)
            value.set_text(fmt.speed_text(player.rate))
            _chips()

        readout.append(common.icon_button(
            "list-remove-symbolic", "Slower",
            lambda: _set(timeline_module.step_speed(player.rate, False))))
        readout.append(value)
        readout.append(common.icon_button(
            "list-add-symbolic", "Faster",
            lambda: _set(timeline_module.step_speed(player.rate, True))))
        box.append(readout)
        hint = common.caps("Remembered for this book")
        hint.set_xalign(0.5)
        box.append(hint)
        flow = _flow()
        box.append(flow)

        def _chips() -> None:
            _fill(flow, [(fmt.speed_text(step),
                          abs(step - player.rate) < 0.01,
                          lambda s=step: _set(s))
                         for step in timeline_module.SPEED_STEPS])

        _chips()
        return self._present(dialog)

    def open_sleep(self) -> Gtk.Widget:
        player = self.ctx.player
        dialog, box = self._sheet("Sleep timer")
        status = Gtk.Box.new(Gtk.Orientation.VERTICAL, 4)
        box.append(status)
        box.append(common.caps("Minutes"))
        flow = _flow()
        flow.set_margin_top(0)
        box.append(flow)
        chapter_end = _flow(1)
        chapter_end.set_margin_top(0)
        box.append(chapter_end)

        def _refresh() -> None:
            common.clear(status)
            running = player.sleep_remaining_ms > 0 or \
                player.sleep_end_of_chapter
            if running:
                status.append(common.caps(
                    "Stops at the chapter end, in"
                    if player.sleep_end_of_chapter else "Stops in"))
                status.append(common.label(
                    fmt.format_ms(player.sleep_remaining_ms),
                    "xx-ab-readout xx-ab-num", chars=8))
                actions = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 10)
                actions.set_homogeneous(True)
                actions.set_margin_bottom(8)
                actions.append(common.fit(widgets.outline_button(
                    "+5 min", lambda: _do(lambda: player.sleep_minutes(
                        player.sleep_remaining_ms // 60000 + 5))), 8))
                actions.append(common.fit(widgets.outline_button(
                    "Off", lambda: _do(player.cancel_sleep)), 8))
                status.append(actions)
            else:
                line = common.para("Off. Fades out over the last 10 seconds.",
                                   "xx-secondary")
                line.set_margin_bottom(8)
                status.append(line)
            minutes = (round(player.sleep_remaining_ms / 60000)
                       if running and not player.sleep_end_of_chapter else 0)
            _fill(flow, [(str(m), minutes == m,
                          lambda m=m: _do(lambda: player.sleep_minutes(m)))
                         for m in SLEEP_PRESETS])
            _fill(chapter_end, [("End of chapter",
                                 player.sleep_end_of_chapter,
                                 lambda: _do(player.sleep_end_of_chapter_now))])

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
        box.append(add)
        box.append(listing)

        def _show(marks, error) -> None:
            common.clear(listing)
            if error is not None or not marks:
                return
            listing.append(common.section("This book"))
            for mark in marks:
                row = Gtk.Button()
                row.add_css_class("xx-ab-chapter")
                text = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
                where = fmt.format_ms(int(mark.position_s * 1000))
                text.append(common.label(where, "xx-body xx-ab-num", chars=10))
                if mark.note:
                    text.append(common.label(mark.note, "xx-ab-meta",
                                             chars=30))
                row.set_child(text)
                common.name(row, f"Go to bookmark at {where}")
                row.connect("clicked", lambda _b, m=mark: (
                    player.seek_to(int(m.position_s * 1000)), dialog.close()))
                listing.append(row)

        ctx.run(lambda: ctx.bookmarks.observe(item_id), _show)
        return self._present(dialog)


def _point():
    gi.require_version("Graphene", "1.0")
    from gi.repository import Graphene

    return Graphene.Point().init(0, 0)


def _flow(per_line: int = 4) -> Gtk.FlowBox:
    flow = Gtk.FlowBox()
    flow.set_selection_mode(Gtk.SelectionMode.NONE)
    flow.set_homogeneous(True)
    flow.set_column_spacing(8)
    flow.set_row_spacing(8)
    flow.set_max_children_per_line(per_line)
    flow.set_min_children_per_line(per_line)
    flow.set_margin_top(8)
    return flow


def _fill(flow: Gtk.FlowBox, items: list) -> None:
    child = flow.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        flow.remove(child)
        child = following
    for text, selected, callback in items:
        chip = Gtk.Button.new_with_label(text.upper())
        chip.add_css_class("xx-chip")
        chip.set_size_request(-1, 48)
        chip.get_child().set_ellipsize(3)  # Pango END: never widens
        if selected:
            chip.add_css_class("selected")
        chip.connect("clicked", lambda _b, cb=callback: cb())
        flow.append(chip)
        chip.get_parent().set_focusable(False)


class MiniPlayer:
    """Hairline, the book's progress as a 2 px line, cover + title +
    time (tap opens Now Playing), skip back and play / pause."""

    def __init__(self, ctx, open_playing) -> None:
        self.ctx = ctx
        self._cover_for = None
        player = ctx.player
        back_s, _forward = _skip_seconds()
        self.widget = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self.widget.add_css_class("xx-ab-mini")
        self.bar = common.line(0.0)
        self.widget.append(self.bar)
        row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 4)
        row.set_margin_start(20)
        row.set_margin_end(12)
        row.set_margin_top(6)
        row.set_margin_bottom(6)
        opener = Gtk.Button()
        opener.add_css_class("xx-ab-mini-open")
        opener.set_hexpand(True)
        inner = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        self.cover_slot = Gtk.Box()
        inner.append(self.cover_slot)
        texts = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
        texts.set_valign(Gtk.Align.CENTER)
        texts.set_hexpand(True)
        self.title = common.label("", "xx-body", chars=18)
        self.sub = common.label("", "xx-ab-meta xx-ab-num", chars=22)
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
