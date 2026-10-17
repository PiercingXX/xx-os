"""The mini player above the bottom tabs (when a queue exists).

A 64 px row on ``surface``: 48 px art, title, artist (or the player's
problem sentence), play/pause and next, and a 2 px progress line along
the bottom edge. Tapping anywhere else opens Now Playing. Only the
progress line moves per tick.
"""

from __future__ import annotations

from gi.repository import GLib, Gtk

from xx_music.ui import common
from xx_ui import widgets


class MiniPlayer:
    def __init__(self, ui, open_playing) -> None:
        self.ui = ui
        self._art_for = None
        self._tick = 0
        player = ui.player
        self.widget = common.vbox(0)
        self.widget.add_css_class("xx-mu-mini")
        row = common.hbox(4)
        row.set_margin_start(12)
        row.set_margin_end(8)
        row.set_margin_top(8)
        row.set_margin_bottom(6)
        opener = Gtk.Button()
        opener.add_css_class("xx-mu-mini-open")
        opener.set_hexpand(True)
        inner = common.hbox(12)
        self.art_slot = common.hbox(0)
        inner.append(self.art_slot)
        texts = common.vbox(2)
        texts.set_valign(Gtk.Align.CENTER)
        texts.set_hexpand(True)
        self.title = common.label("", "xx-mu-mini-title", chars=16)
        self.sub = common.label("", "xx-mu-mini-sub", chars=20)
        texts.append(self.title)
        texts.append(self.sub)
        inner.append(texts)
        opener.set_child(inner)
        opener.connect("clicked", lambda _b: open_playing())
        self.opener = opener
        row.append(opener)
        self.play_icon = widgets.icon_image("media-playback-start-symbolic",
                                            24)
        self.play = Gtk.Button()
        self.play.add_css_class("xx-mu-key")
        self.play.set_child(self.play_icon)
        self.play.set_valign(Gtk.Align.CENTER)
        self.play.connect("clicked", lambda _b: player.play_pause())
        row.append(self.play)
        self.next = common.icon_button("media-skip-forward-symbolic",
                                       "Next song", player.next, 22)
        self.next.add_css_class("xx-mu-key")
        row.append(self.next)
        self.widget.append(row)
        self.bar = common.line(0.0, "Song progress")
        self.widget.append(self.bar)
        player.connect(self.update)
        self.widget.connect("map", lambda _w: self._arm())
        self.widget.connect("unmap", lambda _w: self._disarm())
        self.update()

    def _arm(self) -> None:
        if not self._tick:
            self._tick = GLib.timeout_add(1000, self._on_tick)

    def _disarm(self) -> None:
        if self._tick:
            GLib.source_remove(self._tick)
            self._tick = 0

    def _on_tick(self) -> bool:
        if self.ui.player.playing:
            self._progress()
        return True

    def _progress(self) -> None:
        """Only a visible change re-allocates the line."""
        fraction = round(self.ui.player.fraction, 3)
        if abs(self.bar.get_fraction() - fraction) >= 0.001:
            self.bar.set_fraction(fraction)

    def update(self) -> None:
        player = self.ui.player
        track = player.track
        self.widget.set_visible(track is not None)
        if track is None:
            return
        if self._art_for != track.id:
            self._art_for = track.id
            common.clear(self.art_slot)
            self.art_slot.append(common.art(
                self.ui, track.art_url, track.title, 48, "r4"))
        self.title.set_text(track.title)
        problem = player.problem
        if problem:
            self.sub.set_text(problem.get("text", ""))
            self.sub.add_css_class("xx-mu-error")
        else:
            self.sub.set_text(track.artist_line)
            self.sub.remove_css_class("xx-mu-error")
        self._progress()
        playing = player.playing
        self.play_icon.set_from_icon_name(
            "media-playback-pause-symbolic" if playing
            else "media-playback-start-symbolic")
        common.name(self.play, "Pause" if playing else "Play")
        common.name(self.opener,
                    f"Now playing: {track.title}. Open the player.")
