"""Downloads (M4): every book this phone keeps or is fetching
(ui/downloads/DownloadsScreen.kt).

One row per book with its cover: downloading -> progress + Cancel;
failed or cancelled -> the reason + Retry and Delete; on this phone ->
size + Delete (asks first; a book that is playing carries on by
streaming). A hairline banner says "Waiting for Wi-Fi." while queued
work is held back; the foot shows the storage the books use. Rows are
read from SQLite on a worker; while anything is moving the page re-reads
once a second (the download units write progress every 1 MB).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gi.repository import GLib, Gtk

from xx_audiobook import fmt
from xx_audiobook.ui import common
from xx_ui import widgets

EMPTY_LINE = ("No downloads yet. Download a book from its page to listen "
              "offline.")
ACTIVE = ("Queued", "Running")
STOPPED = ("Failed", "Cancelled")


@dataclass
class DownloadsView:
    """What the page shows, grouped (pure: built on the worker)."""

    active: list = field(default_factory=list)
    stopped: list = field(default_factory=list)
    done: list = field(default_factory=list)
    block: str | None = None
    used_bytes: int = 0
    free_bytes: int | None = None
    low: bool = False
    wifi_only: bool = True
    meta: dict = field(default_factory=dict)

    @property
    def moving(self) -> bool:
        return bool(self.active)

    @property
    def empty(self) -> bool:
        return not (self.active or self.stopped or self.done)

    def signature(self) -> tuple:
        """What the page paints; an unchanged poll repaints nothing."""
        rows = tuple((r.item_id, r.state, r.bytes_done, r.bytes_total,
                      r.error) for r in self.active + self.stopped
                     + self.done)
        return (rows, self.block, self.used_bytes,
                (self.free_bytes or 0) // 100_000_000, self.low,
                self.wifi_only, tuple(sorted(self.meta.items())))


def group(rows: list) -> tuple:
    """(active, stopped, done) keeping the newest-first order."""
    active = [r for r in rows if r.state in ACTIVE]
    stopped = [r for r in rows if r.state in STOPPED]
    done = [r for r in rows if r.state == "Done"]
    return active, stopped, done


def fraction(row) -> float:
    if row.bytes_total <= 0:
        return 0.0
    return min(max(row.bytes_done / row.bytes_total, 0.0), 1.0)


def status_line(row, block: str | None) -> str:
    """The muted line under a row's title."""
    if row.state == "Running":
        return (f"{fmt.percent(fraction(row))}%  ·  "
                f"{fmt.size_text(row.bytes_done)} of "
                f"{fmt.size_text(row.bytes_total)}")
    if row.state == "Queued":
        if row.error:
            return f"Waiting · {row.error}"
        return block or f"Waiting  ·  {fmt.size_text(row.bytes_total)}"
    if row.state == "Cancelled":
        return "Cancelled"
    if row.state == "Failed":
        return row.error or "Failed"
    return f"{fmt.size_text(row.bytes_total)}  ·  On this phone"


def storage_line(view: DownloadsView) -> str:
    used = f"Storage used: {fmt.size_text(view.used_bytes)}"
    if view.free_bytes is None:
        return used
    return f"{used}  ·  {fmt.size_text(view.free_bytes)} free"


class DownloadsPage:
    def __init__(self, ctx, open_book, window_getter) -> None:
        self.ctx = ctx
        self._open_book = open_book
        self._window = window_getter
        self.view = DownloadsView()
        self.loaded = False
        self._poll = 0
        self._reading = False
        self.body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        self.body.set_margin_bottom(24)
        self.widget = widgets.page(common.page_top("Downloads"), self.body)
        self._render()
        ctx.connect("ready", self.reload)
        ctx.connect("downloads-changed", self.reload)
        self.widget.connect("map", lambda _w: self._mapped())
        self.widget.connect("unmap", lambda _w: self._stop_poll())

    # -- data ---------------------------------------------------------------

    def reload(self) -> None:
        ctx = self.ctx
        downloads = ctx.downloads
        if downloads is None or self._reading:
            return
        self._reading = True

        def _read():
            rows = downloads.list()
            active, stopped, done = group(rows)
            storage = downloads.storage_state()
            meta = {}
            for row in rows:
                detail = ctx.library.cached_detail(row.item_id)
                if detail is not None:
                    meta[row.item_id] = (
                        ", ".join(detail.authors),
                        bool(detail.cover_url or detail.thumb_url))
            return DownloadsView(
                active=active, stopped=stopped, done=done,
                block=downloads.block_reason(),
                used_bytes=storage["used_bytes"],
                free_bytes=storage["free_bytes"], low=storage["low"],
                wifi_only=downloads.wifi_only(), meta=meta)

        def _done(view, error) -> None:
            self._reading = False
            if error is None and view is not None:
                changed = (not self.loaded
                           or view.signature() != self.view.signature())
                self.view = view
                self.loaded = True
                if changed:
                    self._render()
                self._sync_poll()

        ctx.run(_read, _done)

    def _mapped(self) -> None:
        self.reload()

    def _sync_poll(self) -> None:
        if self.view.moving and self.widget.get_mapped():
            if not self._poll:
                self._poll = GLib.timeout_add_seconds(1, self._tick)
        else:
            self._stop_poll()

    def _tick(self) -> bool:
        self.reload()
        return True

    def _stop_poll(self) -> None:
        if self._poll:
            GLib.source_remove(self._poll)
            self._poll = 0

    # -- render -------------------------------------------------------------

    def _render(self) -> None:
        body, view = self.body, self.view
        common.clear(body)
        if not self.loaded:
            body.append(common.muted("Loading…"))
            return
        if view.block and (view.active or view.stopped):
            body.append(self._banner(view.block))
        if view.low:
            body.append(common.muted(
                "Storage is low. Free some space before downloading more."))
        if view.empty:
            body.append(common.muted(EMPTY_LINE))
        first = not (view.block and (view.active or view.stopped))
        for caption, rows in (("Active", view.active),
                              ("On this phone", view.done),
                              ("Failed or cancelled", view.stopped)):
            if not rows:
                continue
            body.append(common.section(f"{caption} · {len(rows)}", first))
            first = False
            for index, row in enumerate(rows):
                body.append(self._row(row, last=index == len(rows) - 1))
        body.append(common.section("Storage"))
        foot = common.para(storage_line(view), "xx-secondary")
        body.append(foot)
        if view.wifi_only:
            note = common.para("Downloads use Wi-Fi only. Change it in "
                               "Setup.", "xx-ab-meta")
            note.set_margin_top(6)
            body.append(note)

    def _banner(self, text: str) -> Gtk.Box:
        banner = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
        banner.add_css_class("xx-ab-banner")
        banner.set_margin_top(8)
        icon = ("network-wireless-offline-symbolic"
                if "Wi-Fi" in text else "network-offline-symbolic")
        banner.append(common.glyph(icon, 18))
        line = common.label(text, "xx-body", lines=2, chars=26)
        line.set_hexpand(True)
        banner.append(line)
        banner.update_property([Gtk.AccessibleProperty.LABEL], [text])
        return banner

    def _row(self, row, last: bool) -> Gtk.Box:
        authors, has_cover = self.view.meta.get(row.item_id, ("", True))
        outer = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
        outer.add_css_class("xx-setting")
        if last:
            outer.add_css_class("last")
        opener = Gtk.Button()
        opener.add_css_class("xx-ab-shelf-head")
        opener.set_hexpand(True)
        box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 14)
        art = common.cover(self.ctx, row.item_id, row.title, 56, has_cover)
        art.set_valign(Gtk.Align.START)
        box.append(art)
        text = Gtk.Box.new(Gtk.Orientation.VERTICAL, 3)
        text.set_hexpand(True)
        text.set_valign(Gtk.Align.CENTER)
        text.append(common.label(row.title or row.item_id, "xx-title",
                                 lines=2, chars=16))
        if authors:
            text.append(common.label(authors, "xx-secondary", chars=18))
        status = status_line(row, self.view.block)
        line = common.label(status, "xx-ab-meta xx-ab-num", lines=2,
                            chars=22)
        if row.state == "Failed":
            line.add_css_class("xx-danger-text")
        text.append(line)
        if row.state == "Running":
            progress = common.bar(
                fraction(row),
                f"{fmt.percent(fraction(row))}% downloaded")
            progress.set_margin_top(6)
            text.append(progress)
        box.append(text)
        opener.set_child(box)
        common.name(opener, f"{row.title}. {status}")
        opener.connect("clicked", lambda _b: self._open_book(row.item_id))
        outer.append(opener)
        actions = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        actions.set_valign(Gtk.Align.CENTER)
        if row.state in ACTIVE:
            actions.append(common.icon_button(
                "process-stop-symbolic", f"Cancel download of {row.title}",
                lambda: self.ctx.download_action("cancel", row.item_id)))
        if row.state in STOPPED:
            actions.append(common.icon_button(
                "view-refresh-symbolic", f"Retry download of {row.title}",
                lambda: self.ctx.download_action("retry", row.item_id)))
        if row.state not in ACTIVE:
            actions.append(common.icon_button(
                "user-trash-symbolic", f"Delete download of {row.title}",
                lambda: self.ask_delete(row)))
        outer.append(actions)
        return outer

    # -- actions ------------------------------------------------------------

    def ask_delete(self, row):
        player = self.ctx.player
        playing = player.item_id == row.item_id and not player.restored
        title = row.title or "This book"
        body = (f"{title} ({fmt.size_text(row.bytes_total)}) leaves this "
                "phone" + ("; it keeps playing, streamed from the estate."
                           if playing else
                           "; it streams from the estate again."))

        def _answer(response: str) -> None:
            if response == "delete":
                self.ctx.download_action("delete", row.item_id)

        return common.confirm(
            self._window(), "Delete the download?", body,
            [("keep", "Keep", "outline"), ("delete", "Delete", "danger")],
            _answer)
