"""Book (M3): cover and metadata (authors, narrators, series open their
views), the damaged state, Play / Resume, Download, Mark finished,
Ratings & reviews (ui/reviews.py), description, chapters (first 5, then
all), bookmarks and the "Where were you?" prompt (ui/detail/BookScreen.kt).
Download queues the book (M4) and then tracks it: "Downloading 42%",
"Downloaded" (both open Downloads), "Retry download".

Offline-first: the cached detail renders at once, the server's answer
replaces it when it arrives; with neither, the library row's title and
cover stand in under one muted line.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk  # noqa: E402

from xx_audiobook import browse as browse_module  # noqa: E402
from xx_audiobook import fmt  # noqa: E402
from xx_audiobook.ui import common, style  # noqa: E402
from xx_audiobook.ui.context import error_line  # noqa: E402
from xx_audiobook.ui.reviews import ReviewsBlock  # noqa: E402
from xx_ui import widgets  # noqa: E402

CHAPTERS_COLLAPSED = 5


class BookPage:
    def __init__(self, ctx, item_id: str, peek, nav) -> None:
        self.ctx = ctx
        self.item_id = item_id
        self.peek = peek
        self.nav = nav
        self.detail = None
        self.progress = None
        self.marks: list = []
        self.status: str | None = None
        self.loading = True
        self.all_chapters = False
        self.download_state: dict | None = None
        self.body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self.body.set_margin_bottom(24)
        self.bar = common.header(nav.back, "Book")
        self.widget = widgets.page(self.bar, self.body)
        self.reviews = ReviewsBlock(ctx, item_id, self.play,
                                    self._finished_marked,
                                    lambda: self.widget)
        self._render()
        self._load()
        self._playing_chapter = -1
        self._seen = None
        self._unlisten = None
        self._events = (("downloads-changed", self._reread),
                        ("rating-changed", self._rating_changed),
                        ("account-changed", self.reviews.resolve_me))
        self.widget.connect("map", self._mapped)
        self.widget.connect("unmap", self._unmapped)

    def _mapped(self, _widget) -> None:
        if self._unlisten is None:
            self._unlisten = self.ctx.player.connect(self._player_changed)
            for event, callback in self._events:
                self.ctx.connect(event, callback)
        self._player_changed()

    def _unmapped(self, _widget) -> None:
        if self._unlisten is not None:
            self._unlisten()
            self._unlisten = None
            for event, callback in self._events:
                self.ctx.disconnect(event, callback)

    def _download_status(self) -> dict | None:
        downloads = self.ctx.downloads
        return downloads.status(self.item_id) if downloads else None

    # -- data ---------------------------------------------------------------------

    def _load(self) -> None:
        ctx, item_id = self.ctx, self.item_id

        def _cached():
            return (ctx.library.cached_detail(item_id),
                    ctx.progress.get(item_id),
                    ctx.bookmarks.observe(item_id),
                    self._download_status())

        def _fresh():
            detail, error = ctx.library.refresh_detail(item_id)
            _rows, sync_error = ctx.bookmarks.refresh(item_id)
            return (detail, error or None, sync_error,
                    ctx.progress.get(item_id),
                    ctx.bookmarks.observe(item_id))

        def _got_cache(result, error) -> None:
            if error is None:
                (cached, self.progress, self.marks,
                 self.download_state) = result
                self.detail = self.detail or cached
                self._render()
            ctx.run(_fresh, _got_fresh)

        def _got_fresh(result, error) -> None:
            self.loading = False
            if error is None:
                detail, api_error, _sync, self.progress, self.marks = result
                self.detail = detail or self.detail
                if detail is None and self.detail is None:
                    self.status = error_line(api_error)
            self._render()

        ctx.run(_cached, _got_cache)

    def _reread(self) -> None:
        ctx, item_id = self.ctx, self.item_id

        def _read():
            return (ctx.progress.get(item_id), ctx.bookmarks.observe(item_id),
                    self._download_status())

        def _done(result, error) -> None:
            if error is None:
                self.progress, self.marks, self.download_state = result
                self._render()

        ctx.run(_read, _done)

    def _rating_changed(self, item_id: str) -> None:
        """A review landed: the cached detail carries the new star line."""
        if item_id != self.item_id:
            return

        def _done(detail, error) -> None:
            if error is None and detail is not None:
                self.detail = detail
                self._render()

        self.ctx.run(lambda: self.ctx.library.cached_detail(self.item_id),
                     _done)

    def _finished_marked(self) -> None:
        self.ctx.emit("library-changed")
        self._reread()

    def _player_changed(self) -> None:
        player = self.ctx.player
        mine = player.item_id == self.item_id
        index = player.chapter_index if mine and not player.restored else -1
        key = (index, mine and player.playing, mine and player.busy)
        if key != self._seen and self.detail is not None:
            self._seen = key
            self._playing_chapter = index
            self._render()

    # -- derived ----------------------------------------------------------------

    def _position_s(self) -> float:
        if self.progress is not None:
            return self.progress.position_s
        if self.detail is not None and self.detail.progress is not None:
            return self.detail.progress.position_s
        return 0.0

    def _finished(self) -> bool:
        if self.progress is not None:
            return self.progress.is_finished
        detail = self.detail
        return bool(detail and detail.progress and detail.progress.is_finished)

    # -- render -------------------------------------------------------------------

    def _render(self) -> None:
        body = self.body
        common.clear(body)
        detail = self.detail
        if detail is None:
            title = getattr(self.peek, "title", "") or "Book"
            self.bar.title.set_text(title)
            body.append(self._head(title, getattr(self.peek, "authors", ""),
                                   bool(getattr(self.peek, "thumb_url", None))))
            body.append(common.muted(
                self.status or ("Loading…" if self.loading
                                else "This book is not on this phone.")))
            return
        self.bar.title.set_text(detail.title or "Book")
        body.append(self._header(detail))
        if self.status:
            body.append(common.muted(self.status))
        if not detail.playable:
            body.append(common.muted(fmt.DAMAGED_LINE))
        body.append(self._actions(detail))
        self.reviews.set_eligibility(self._position_s() > 0
                                     or self._finished())
        body.append(self.reviews.widget)
        paragraphs = fmt.description_paragraphs(detail.description)
        if paragraphs:
            body.append(common.section("About"))
            for text in paragraphs[:6]:
                para = common.para(text, "xx-ab-b13")
                para.set_margin_bottom(8)
                body.append(para)
        self._chapters(body, detail)
        self._bookmarks(body)

    def _head(self, title: str, authors: str, has_cover: bool) -> Gtk.Box:
        """Cover (120 px) beside the title block; stacked at large text."""
        row = Gtk.Box.new(Gtk.Orientation.VERTICAL if style.large_text()
                          else Gtk.Orientation.HORIZONTAL, 16)
        row.set_margin_top(16)
        art = common.cover(self.ctx, self.item_id, title, 120, has_cover,
                           "full")
        art.set_valign(Gtk.Align.START)
        art.set_halign(Gtk.Align.START)
        row.append(art)
        self.text = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
        self.text.set_hexpand(True)
        self.text.append(common.label(title, "xx-ab-heading", lines=4,
                                      chars=12))
        if authors:
            self.text.append(common.label(authors, "xx-ab-t14", chars=16))
        row.append(self.text)
        return row

    def _link(self, text: str, spoken: str, callback,
              cls: str = "xx-ab-b11", chars: int = 30) -> Gtk.Button:
        """LinkLine: underlined, a 48 px row, read as a link."""
        button = Gtk.Button()
        button.add_css_class("xx-ab-shelf-head")
        line = common.label(text, cls + " xx-ab-link", chars=chars)
        button.set_child(line)
        button.set_halign(Gtk.Align.START)
        common.name(button, spoken)
        button.connect("clicked", lambda _b: callback())
        return button

    def _header(self, detail) -> Gtk.Box:
        root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        row = self._head(detail.title, "", bool(detail.cover_url
                                                 or detail.thumb_url))
        text = self.text
        if detail.subtitle:
            text.append(common.label(detail.subtitle, "xx-ab-t14",
                                     lines=2, chars=16))
        refs = {r.name: r.id for r in detail.author_refs}
        for author in detail.authors:
            text.append(self._link(
                author, f"Books by {author}",
                lambda a=author: self.nav.browse(
                    browse_module.AUTHOR, a, refs.get(a)), "xx-ab-t14"))
        if detail.narrators:
            text.append(common.label("Read by", "xx-ab-l10 xx-ab-muted",
                                     chars=12))
            nrefs = {r.name: r.id for r in detail.narrator_refs}
            for narrator in detail.narrators:
                text.append(self._link(
                    narrator, f"Books read by {narrator}",
                    lambda n=narrator: self.nav.browse(
                        browse_module.NARRATOR, n, nrefs.get(n))))
        for series in detail.series:
            shown = (f"{series.name} #{series.sequence}"
                     if series.sequence.strip() else series.name)
            text.append(self._link(
                shown, f"Series {shown}",
                lambda s=series: self.nav.browse(
                    browse_module.SERIES, s.name, s.id)))
        if detail.duration_s > 0:
            text.append(common.label(fmt.format_duration(detail.duration_s),
                                     "xx-ab-b11 xx-ab-muted", chars=16))
        if detail.missing:
            text.append(common.label("Missing on the server",
                                     "xx-ab-l12 xx-ab-error", chars=22))
        root.append(row)
        position = self._position_s()
        if detail.duration_s > 0 and position > 0 and not self._finished():
            bar = common.line(position / detail.duration_s)
            bar.set_margin_top(12)
            root.append(bar)
        return root

    def _actions(self, detail) -> Gtk.Box:
        box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
        box.set_margin_top(12)
        position = self._position_s()
        finished = self._finished()
        player = self.ctx.player
        current = player.item_id == self.item_id and not player.restored
        if player.busy and player.item_id == self.item_id:
            text = "Starting…"
        elif current and player.playing:
            text = "Playing"
        elif finished:
            text = "Play again"
        elif position > 0:
            text = "Resume at " + fmt.format_ms(int(position * 1000))
        else:
            text = "Play"
        play = widgets.primary_button(text, self.play)
        common.fit(play, 24)
        play.set_sensitive(detail.playable and not detail.missing)
        box.append(play)
        row = Gtk.Box.new(Gtk.Orientation.VERTICAL if style.large_text()
                          else Gtk.Orientation.HORIZONTAL, 8)
        row.set_homogeneous(True)
        download = widgets.outline_button(self._download_text(),
                                          self.download)
        download.set_sensitive(detail.playable and not detail.missing)
        common.fit(download, 15)
        mark = widgets.outline_button(
            "Mark unfinished" if finished else "Mark finished",
            lambda: self.set_finished(not finished))
        common.fit(mark, 15)
        row.append(download)
        row.append(mark)
        box.append(row)
        return box

    def _chapters(self, body: Gtk.Box, detail) -> None:
        chapters = sorted(detail.chapters, key=lambda c: c.start_s)
        if not chapters:
            return
        body.append(common.section(f"Chapters ({len(chapters)})"))
        playing = (chapters[self._playing_chapter]
                   if 0 <= self._playing_chapter < len(chapters) else None)
        if self.all_chapters:
            shown = chapters
        elif playing is not None and \
                chapters.index(playing) >= CHAPTERS_COLLAPSED:
            shown = chapters[:CHAPTERS_COLLAPSED - 1] + [playing]
        else:
            shown = chapters[:CHAPTERS_COLLAPSED]
        for chapter in shown:
            body.append(chapter_row(
                chapter.title or f"Chapter {chapter.index + 1}",
                int(chapter.start_s * 1000),
                int(max(chapter.end_s - chapter.start_s, 0) * 1000),
                chapter is playing,
                lambda c=chapter: self.play_from(int(c.start_s * 1000))))
        if len(chapters) > CHAPTERS_COLLAPSED:
            more = widgets.text_button(
                "Fewer chapters" if self.all_chapters
                else f"All {len(chapters)} chapters", self._toggle_chapters)
            common.fit(more, 20)
            more.set_halign(Gtk.Align.START)
            body.append(more)

    def _toggle_chapters(self) -> None:
        self.all_chapters = not self.all_chapters
        self._render()

    def _bookmarks(self, body: Gtk.Box) -> None:
        body.append(common.section("Bookmarks"))
        if not self.marks:
            body.append(common.muted("No bookmarks. Add one from Now Playing."))
            return
        for index, mark in enumerate(self.marks):
            body.append(bookmark_row(
                mark, lambda m=mark: self.play_from(int(m.position_s * 1000)),
                lambda m=mark: self.edit_mark(m),
                lambda m=mark: self.delete_mark(m),
                last=index == len(self.marks) - 1))

    # -- actions ---------------------------------------------------------------

    def play(self) -> None:
        player = self.ctx.player
        if player.item_id == self.item_id and not player.restored:
            if not player.playing:
                player.play_pause()
            self.nav.now_playing()
            return
        self._with_conflict_check(lambda: self._start(None))

    def play_from(self, at_ms: int) -> None:
        player = self.ctx.player
        if player.item_id == self.item_id and not player.restored:
            player.seek_to(at_ms)
            if not player.playing:
                player.play_pause()
            self.nav.now_playing()
            return
        self._start(at_ms)

    def _start(self, at_ms) -> None:
        title = self.detail.title if self.detail else ""
        self.ctx.player.open_and_play(self.item_id, at_ms, title=title)
        self.nav.now_playing()

    def _with_conflict_check(self, proceed) -> None:
        client = self.ctx.client
        if client is None or not self.ctx.signed_in:
            proceed()
            return
        session = self.ctx.bearer()

        def _check():
            if session is not None:
                client.set_session(*session)
            return client.check_conflict(self.item_id)

        def _checked(conflict, error) -> None:
            if error is not None or not conflict or \
                    not conflict.get("needs_prompt"):
                proceed()
                return
            self.ask_conflict(float(conflict.get("local_s", 0.0)),
                              float(conflict.get("server_s", 0.0)), proceed)

        self.ctx.run(_check, _checked)

    def ask_conflict(self, local_s: float, server_s: float, proceed) -> None:
        """The > 60 s prompt (CheckConflict): which position wins."""
        here = fmt.format_ms(int(local_s * 1000))
        there = fmt.format_ms(int(server_s * 1000))
        client = self.ctx.client
        item_id = self.item_id

        def _answer(response: str) -> None:
            use_server = response == "server"

            def _resolve():
                if client is not None:
                    client.resolve_conflict(item_id, use_server)

            self.ctx.run(_resolve, lambda _r, _e: proceed())

        return common.confirm(
            self.widget, "Where were you?",
            f"This phone is at {here}; another device reached {there}.",
            [("phone", "This phone", "outline"),
             ("server", "Other device", "primary")], _answer)

    def _download_text(self) -> str:
        status = self.download_state or {}
        state = status.get("state")
        if state == "Running":
            total = status.get("bytes_total") or 0
            done = status.get("bytes_done") or 0
            return (f"Downloading {fmt.percent(done / total)}%"
                    if total else "Downloading")
        if state == "Queued":
            return "Queued"
        if state == "Done":
            return "Downloaded"
        if state in ("Failed", "Cancelled"):
            return "Retry download"
        return "Download"

    def download(self) -> None:
        state = (self.download_state or {}).get("state")
        if state in ("Running", "Queued", "Done"):
            self.nav.downloads()
            return
        if state in ("Failed", "Cancelled"):
            self.ctx.download_action("retry", self.item_id)
            return

        def _done(line) -> None:
            self.status = line
            self._reread()

        title = self.detail.title if self.detail else ""
        self.status = "Queueing…"
        self._render()
        self.ctx.request_download(self.item_id, title, _done)

    def set_finished(self, finished: bool) -> None:
        ctx, item_id = self.ctx, self.item_id

        def _work():
            ctx.progress.set_finished(item_id, finished)
            ctx.progress.flush()

        def _done(_result, _error) -> None:
            ctx.emit("library-changed")
            self._reread()

        ctx.run(_work, _done)

    def edit_mark(self, mark):
        entry = widgets.text_entry("Note")
        entry.set_text(mark.note)

        def _answer(response: str) -> None:
            if response != "save":
                return
            note = entry.get_text()
            self.ctx.run(lambda: self._edit_and_sync(mark, note),
                         lambda _r, _e: self._reread())

        return common.confirm(
            self.widget, "Bookmark note",
            f"At {fmt.format_ms(int(mark.position_s * 1000))}.",
            [("cancel", "Cancel", "outline"), ("save", "Save", "primary")],
            _answer, extra=entry)

    def _edit_and_sync(self, mark, note: str) -> None:
        self.ctx.bookmarks.edit(mark, note)

    def delete_mark(self, mark):
        def _answer(response: str) -> None:
            if response != "delete":
                return

            def _work():
                self.ctx.bookmarks.delete(mark)

            self.ctx.run(_work, lambda _r, _e: self._reread())

        where = fmt.format_ms(int(mark.position_s * 1000))
        return common.confirm(
            self.widget, "Delete this bookmark?",
            f"The bookmark at {where} is removed on every device.",
            [("keep", "Keep", "outline"), ("delete", "Delete", "danger")],
            _answer)


def chapter_row(title: str, start_ms: int, length_ms: int, now: bool,
                on_tap) -> Gtk.Button:
    """A chapter: title, start and length; the playing one carries a
    2 px foreground bar on its left edge and the title weight."""
    row = Gtk.Button()
    row.add_css_class("xx-ab-chapter")
    if now:
        row.add_css_class("now")
    box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    name = common.label(title, "xx-ab-t14" if now else "xx-ab-b13",
                        lines=2, chars=20)
    name.set_hexpand(True)
    box.append(name)
    times = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    times.set_valign(Gtk.Align.CENTER)
    times.append(common.label(fmt.format_ms(start_ms),
                              "xx-ab-l12 xx-ab-muted xx-ab-num", xalign=1.0,
                              chars=8))
    if length_ms > 0:
        times.append(common.label(fmt.format_duration(length_ms / 1000),
                                  "xx-ab-l10 xx-ab-muted xx-ab-num",
                                  xalign=1.0, chars=10))
    box.append(times)
    row.set_child(box)
    spoken = f"Play chapter {title}"
    if length_ms > 0:
        spoken += ", " + fmt.format_duration(length_ms / 1000)
    row.update_property([Gtk.AccessibleProperty.LABEL], [
        spoken + (", playing now" if now else "")])
    row.connect("clicked", lambda _b: on_tap())
    return row


def bookmark_row(mark, on_play, on_edit, on_delete,
                 last: bool = False) -> Gtk.Box:
    """Time + note (+ 'Not synced yet'), with edit and delete targets."""
    row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    jump = Gtk.Button()
    jump.add_css_class("xx-ab-shelf-head")
    jump.set_hexpand(True)
    text = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    text.set_valign(Gtk.Align.CENTER)
    where = fmt.format_ms(int(mark.position_s * 1000))
    text.append(common.label(where, "xx-ab-l13 xx-ab-num", chars=10))
    if mark.note:
        text.append(common.label(mark.note, "xx-ab-b11 xx-ab-muted",
                                 lines=2, chars=24))
    if mark.pending is not None:
        text.append(common.label("Not synced yet", "xx-ab-l10 xx-ab-muted",
                                 chars=16))
    jump.set_child(text)
    common.name(jump, f"Play from bookmark at {where}")
    jump.connect("clicked", lambda _b: on_play())
    row.append(jump)
    row.append(common.icon_button("document-edit-symbolic",
                                  "Edit bookmark note", on_edit))
    row.append(common.icon_button("user-trash-symbolic", "Delete bookmark",
                                  on_delete))
    return row
