"""Library: the account's library when signed in, this phone's otherwise.

Signed in: Playlists, Songs, Albums, Artists, Liked, History and On this
phone. Signed out: one line and Sign in, then Liked and History kept on
this phone, and On this phone. On this phone lists downloads by state:
downloading (a blinking square and a 2 px progress line), waiting,
failed (with Retry) and done, under a storage meter.

Minimal Mode: the sections are words, the meter a hairline under one
line of figures, rows without art, ``[ retry ]`` and ``[ cancel ]``.
"""

from __future__ import annotations

from gi.repository import Gtk

from xx_music.engine import (ERROR_TEXT, LIBRARY, ErrorKind, Item,
                             LibrarySection, Track)
from xx_music.tasks import Generation
from xx_music.ui import common, css, fmt, menus, minimal
from xx_music.ui.context import ACTIVE, DONE, FAILED, QUEUED, DownloadRow
from xx_ui import widgets

PHONE = "phone"
DISLIKED = "disliked"
SIGNED_IN_SECTIONS = (
    (LibrarySection.PLAYLISTS.value, "Playlists"),
    (LibrarySection.SONGS.value, "Songs"),
    (LibrarySection.ALBUMS.value, "Albums"),
    (LibrarySection.ARTISTS.value, "Artists"),
    (LibrarySection.LIKED.value, "Liked"),
    (LibrarySection.HISTORY.value, "History"),
    (DISLIKED, "Thumbs down"),
    (PHONE, "On this phone"),
)
SIGNED_OUT_SECTIONS = (
    (PHONE, "On this phone"),
    (LibrarySection.LIKED.value, "Liked"),
    (LibrarySection.HISTORY.value, "History"),
    (DISLIKED, "Thumbs down"),
)
EMPTY = {
    PHONE: "Nothing on this phone yet. Download songs, albums and "
           "playlists from their menus.",
    LibrarySection.LIKED.value: "Songs you like show here.",
    DISLIKED: "Songs you thumb down hide from suggestions. "
              "Remove one here to see it again.",
    LibrarySection.HISTORY.value: "Songs you play show here.",
    LibrarySection.PLAYLISTS.value: "No playlists in your library yet.",
    LibrarySection.SONGS.value: "No songs in your library yet.",
    LibrarySection.ALBUMS.value: "No albums in your library yet.",
    LibrarySection.ARTISTS.value: "No artists in your library yet.",
}
STATE_TITLES = {ACTIVE: "Downloading", QUEUED: "Waiting", FAILED: "Failed",
                DONE: "Downloaded"}


class LibraryPage:
    def __init__(self, ui) -> None:
        self.ui = ui
        self.section = ""
        self._picked = False
        self.generation = Generation()
        self.loading = common.LoadingLine()
        self.top = common.vbox(0)
        self.body = common.vbox(0)
        self.body.set_margin_bottom(28)
        root = common.vbox(0)
        root.append(widgets.page_header("Library"))
        root.append(self.loading)
        root.append(self.top)
        scroll = common.scroller(self.body)
        common.pull_to_refresh(scroll, self.reload)
        root.append(scroll)
        self.widget = root
        self._repaint = None
        ui.on_account(self._account_changed)
        ui.on_downloads(self._downloads_changed)
        minimal.listen(self._minimal_changed)
        self._account_changed()

    def _minimal_changed(self) -> None:
        """Repaint what is on screen from what was loaded."""
        self._paint_top()
        if self.section == PHONE:
            self._paint_phone()
        elif self._repaint is not None:
            self._repaint()

    # -- sections ------------------------------------------------------------------
    def sections(self) -> tuple:
        if self.ui.signed_in and self.ui.supports(LIBRARY):
            return SIGNED_IN_SECTIONS
        return SIGNED_OUT_SECTIONS

    def _account_changed(self) -> None:
        keys = [k for k, _t in self.sections()]
        if self.section not in keys or not self._picked:
            self.section = keys[0]
        self.show_section(self.section)

    def pick(self, key: str) -> None:
        self._picked = True
        self.show_section(key)

    def _downloads_changed(self) -> None:
        if self.section == PHONE:
            self._paint_phone()

    def shown(self) -> None:
        if self.section == PHONE:
            self.ui.refresh_downloads()
        elif self.section in (LibrarySection.LIKED.value,
                              LibrarySection.HISTORY.value, DISLIKED):
            self.show_section(self.section)

    def reload(self) -> None:
        self.show_section(self.section)

    def show_section(self, key: str) -> None:
        self.section = key
        self._repaint = None
        self._paint_top()
        common.clear(self.body)
        self.loading.set_active(False)
        self.generation.next()
        if key == PHONE:
            self._paint_phone()
            return
        if key == DISLIKED:
            self.ui.io(self.ui.store.disliked,
                       lambda tracks: self._paint_tracks(
                           tracks, key), lambda _e: None)
            return
        if not self.ui.signed_in or not self.ui.supports(LIBRARY):
            store = self.ui.store
            fetch = store.liked if key == LibrarySection.LIKED.value \
                else store.history
            self.ui.io(fetch, lambda tracks: self._paint_tracks(
                tracks, key), lambda _e: None)
            return
        self.loading.set_active(True)
        section = LibrarySection(key)
        self.ui.run(LIBRARY, lambda: self.ui.engine.library(section),
                    self._got_items, self._failed, self.generation)

    def _paint_top(self) -> None:
        common.clear(self.top)
        ui = self.ui
        if not ui.signed_in and ui.supports(LIBRARY):
            box = common.vbox(10)
            box.add_css_class("xx-page-body")
            box.set_margin_bottom(8)
            box.append(common.para(
                f"Sign in to see your {ui.engine.name} library.",
                "xx-secondary"))
            if "sign-in" in ui.hooks.actions:
                button = minimal.outline(
                    "Sign in", lambda: ui.action("sign-in"))
                common.fit(button, 12)
                button.set_halign(Gtk.Align.START)
                box.append(button)
            self.top.append(box)
        self.top.append(common.chips([
            (title, key == self.section,
             lambda k=key: self.pick(k))
            for key, title in self.sections()]))

    # -- signed-in sections ------------------------------------------------------------
    def _got_items(self, items: list[Item]) -> None:
        self.loading.set_active(False)
        self._repaint = lambda: self._got_items(items)
        common.clear(self.body)
        if not items:
            self._gutter(common.muted(EMPTY[self.section]))
            return
        tracks = [i.track for i in items if i.track is not None]
        if len(tracks) == len(items):
            self._paint_tracks(tracks, self.section)
            return
        box = common.vbox(0)
        for index, item in enumerate(items):
            box.append(common.item_row(
                self.ui, item, lambda i=item: self.ui.shell.open_item(i),
                lambda i=item: menus.item_menu(self.ui, i),
                last=index == len(items) - 1))
        self._gutter(box)

    def _failed(self, error) -> None:
        self.loading.set_active(False)
        kind = getattr(error, "kind", ErrorKind.OTHER)
        common.clear(self.body)
        self._gutter(common.empty_state(
            ERROR_TEXT.get(kind, ERROR_TEXT[ErrorKind.OTHER]), "Retry",
            self.reload)).set_margin_top(24)

    def _paint_tracks(self, tracks: list[Track], key: str) -> None:
        if key != self.section:
            return
        self._repaint = lambda: self._paint_tracks(tracks, key)
        common.clear(self.body)
        if not tracks:
            self._gutter(common.muted(EMPTY[key]))
            return
        title = dict([*SIGNED_IN_SECTIONS, *SIGNED_OUT_SECTIONS])[key]
        origin = {"type": "library", "title": title}
        head = common.section(fmt.songs(len(tracks)), "Play",
                              lambda: self.ui.player.play(tracks, 0, origin),
                              first=True)
        self._gutter(head)
        box = common.vbox(0)
        for index, track in enumerate(tracks):
            box.append(common.track_row(
                self.ui, track,
                lambda t=track: self.ui.shell.play_song(t, tracks, origin),
                lambda t=track: menus.track_menu(self.ui, t),
                offline=track.id in self.ui.downloaded,
                last=index == len(tracks) - 1))
        self._gutter(box)

    # -- on this phone ------------------------------------------------------------------
    def _paint_phone(self) -> None:
        if self.section != PHONE:
            return
        common.clear(self.body)
        rows: list[DownloadRow] = list(self.ui.downloads)
        done = [r for r in rows if r.state == DONE]
        used = sum(r.size_bytes or 0 for r in done)
        self._gutter(self._storage(len(done), used))
        if not rows:
            self._gutter(common.muted(EMPTY[PHONE]))
            return
        for state in (ACTIVE, QUEUED, FAILED, DONE):
            group = [r for r in rows if r.state == state]
            if not group:
                continue
            action = ("Play", lambda: self._play_done(done)) \
                if state == DONE else ("", None)
            self._gutter(common.section(
                f"{STATE_TITLES[state]} · {len(group)}", action[0],
                action[1]))
            box = common.vbox(0)
            for index, row in enumerate(group):
                box.append(self._download_row(row, done,
                                              index == len(group) - 1))
            self._gutter(box)

    def _storage(self, count: int, used: int) -> Gtk.Widget:
        try:
            cap_gb = int(self.ui.prefs.get("storage_cap_gb") or 2)
        except (ValueError, TypeError):
            cap_gb = 2
        cap = cap_gb * 2**30
        if minimal.active():
            return storage_lines(used, cap_gb, self._phone_line(count))
        box = common.vbox(8)
        box.add_css_class("xx-mu-block")
        box.set_margin_top(8)
        top = Gtk.Box.new(Gtk.Orientation.VERTICAL if css.large_text()
                          else Gtk.Orientation.HORIZONTAL, 8)
        figure = common.label(fmt.size(used), "xx-mu-h2 xx-mu-num", chars=10)
        figure.set_hexpand(True)
        figure.set_ellipsize(0)
        top.append(figure)
        of = common.label(f"of {cap_gb} GB", "xx-secondary xx-mu-num",
                          xalign=1.0, chars=10)
        of.set_valign(Gtk.Align.END)
        of.set_ellipsize(0)
        top.append(of)
        box.append(top)
        box.append(common.meter(used / cap if cap else 0.0,
                                f"{fmt.size(used)} of {cap_gb} GB used"))
        line = common.label(self._phone_line(count), "xx-mu-meta",
                            lines=2, chars=34)
        box.append(line)
        return box

    def _phone_line(self, count: int) -> str:
        active = sum(1 for r in self.ui.downloads if r.state == ACTIVE)
        waiting = sum(1 for r in self.ui.downloads if r.state == QUEUED)
        parts = [fmt.songs(count) + " on this phone"]
        if active:
            parts.append(f"downloading {active}")
        if waiting:
            parts.append(f"{waiting} waiting for Wi-Fi")
        return " · ".join(parts)

    def _play_done(self, done: list[DownloadRow]) -> None:
        tracks = [r.track for r in done]
        self.ui.player.play(tracks, 0, {"type": "offline",
                                        "title": "On this phone"})

    def _download_row(self, row: DownloadRow, done: list[DownloadRow],
                      last: bool) -> Gtk.Widget:
        ui = self.ui
        track = row.track
        if minimal.active():
            return self._minimal_download_row(row, done)
        button = Gtk.Button()
        button.add_css_class("xx-mu-row")
        if last:
            button.add_css_class("last")
        line = common.hbox(14)
        holder = Gtk.Overlay()
        holder.set_child(common.art(ui, track.art_url, track.title, 48))
        line.append(holder)
        text = common.vbox(3)
        text.set_hexpand(True)
        text.set_valign(Gtk.Align.CENTER)
        text.append(common.label(track.title, "xx-title", chars=18))
        if row.state == ACTIVE:
            status = common.hbox(8)
            status.append(common.busy_square())
            bar = common.line(row.progress,
                              f"{int(row.progress * 100)} percent")
            bar.set_valign(Gtk.Align.CENTER)
            status.append(bar)
            pct = common.label(f"{int(row.progress * 100)}%",
                               "xx-mu-meta xx-mu-num", chars=5)
            status.append(pct)
            text.append(status)
        else:
            note = row.note or {
                QUEUED: "Waiting",
                FAILED: "Couldn't download",
                DONE: fmt.joined(track.artist_line,
                                 fmt.size(row.size_bytes)
                                 if row.size_bytes else ""),
            }[row.state]
            sub = common.label(note, "xx-secondary", chars=24)
            if row.state == FAILED:
                sub.add_css_class("xx-mu-error")
            text.append(sub)
        line.append(text)
        if row.state == FAILED:
            trailing = common.icon_button(
                "view-refresh-symbolic", f"Retry {track.title}",
                lambda: (ui.player.download([menus.download_payload(
                    "track", track.id, track.title, track)]),
                    common.toast(ui, "Trying again")))
        elif row.state in (ACTIVE, QUEUED):
            trailing = common.icon_button(
                "window-close-symbolic", f"Cancel {track.title}",
                lambda: ui.player.remove_download(track.id))
        else:
            trailing = common.icon_button(
                "view-more-symbolic", f"More options for {track.title}",
                lambda: menus.track_menu(ui, track))
        button.set_child(line)
        common.name(button, track.title)
        if row.state == DONE:
            tracks = [r.track for r in done]
            button.connect("clicked", lambda _b: ui.shell.play_song(
                track, tracks, {"type": "offline",
                                "title": "On this phone"}))
            common.on_long_press(button, lambda: menus.track_menu(ui, track))
        return common.beside(button, line, trailing)

    def _retry(self, track: Track) -> None:
        self.ui.player.download([menus.download_payload(
            "track", track.id, track.title, track)])
        common.toast(self.ui, "Trying again")

    def _minimal_download_row(self, row: DownloadRow,
                              done: list[DownloadRow]) -> Gtk.Widget:
        ui = self.ui
        track = row.track
        if row.state == DONE:
            tracks = [r.track for r in done]
            return minimal.list_row(
                track.title, fmt.joined(track.artist_line,
                                        fmt.size(row.size_bytes)
                                        if row.size_bytes else ""),
                lambda: ui.shell.play_song(track, tracks, {
                    "type": "offline", "title": "On this phone"}),
                lambda: menus.track_menu(ui, track))
        note = row.note or {
            ACTIVE: f"downloading · {int(row.progress * 100)}%",
            QUEUED: "waiting",
            FAILED: "couldn't download"}[row.state]
        line = common.hbox(8)
        texts = common.vbox(1)
        texts.set_hexpand(True)
        texts.set_valign(Gtk.Align.CENTER)
        texts.append(minimal.text(track.title, minimal.Tone.PLAIN, 15,
                                  chars=20))
        texts.append(minimal.text(note, minimal.Tone.QUIET, 13, chars=24))
        if row.state == ACTIVE:
            bar = minimal.hairline(row.progress)
            bar.set_margin_top(4)
            texts.append(bar)
        line.append(texts)
        if row.state == FAILED:
            line.append(minimal.bracket_button(
                "retry", lambda: self._retry(track), minimal.Tone.STRONG,
                size=13, spoken=f"Retry {track.title}"))
        else:
            line.append(minimal.bracket_button(
                "cancel", lambda: ui.player.remove_download(track.id),
                minimal.Tone.QUIET, size=13,
                spoken=f"Cancel {track.title}"))
        line.set_margin_top(6)
        line.set_margin_bottom(6)
        return line

    def _gutter(self, widget: Gtk.Widget) -> Gtk.Widget:
        holder = common.vbox(0)
        holder.add_css_class("xx-page-body")
        holder.append(widget)
        self.body.append(holder)
        return widget



def storage_lines(used: int, cap_gb: int, line: str) -> Gtk.Widget:
    """Minimal Mode's storage meter: the figures, a hairline, a Quiet
    line. No block, no fill."""
    box = common.vbox(6)
    box.set_margin_top(8)
    box.set_margin_bottom(4)
    figures = minimal.text(f"{fmt.size(used)} of {cap_gb} GB",
                           minimal.Tone.PLAIN, 15, chars=24)
    figures.add_css_class("xx-mu-num")
    box.append(figures)
    cap = cap_gb * 2**30
    bar = minimal.hairline(used / cap if cap else 0.0)
    bar.update_property([Gtk.AccessibleProperty.LABEL],
                        [f"{fmt.size(used)} of {cap_gb} GB used"])
    box.append(bar)
    box.append(minimal.text(line, minimal.Tone.QUIET, 13, lines=2,
                            chars=34))
    return box
