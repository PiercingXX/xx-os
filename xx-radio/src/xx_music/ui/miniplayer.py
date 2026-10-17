"""The mini player above the bottom tabs (when a queue exists).

A 64 px row on ``surface``: 48 px art, title, artist (or the player's
problem sentence), play/pause and next, and a 2 px progress line along
the bottom edge. Tapping anywhere else opens Now Playing. Only the
progress line moves per tick.

Minimal Mode: no art and no fill, a 1 px progress hairline on top, one
line ``Title — Artist`` and ``[ pause ]`` / ``[ play ]``.
"""

from __future__ import annotations

from gi.repository import GLib, Gtk

from xx_music.ui import common, minimal
from xx_ui import widgets


class MiniPlayer:
    def __init__(self, ui, open_playing) -> None:
        self.ui = ui
        self.open_playing = open_playing
        self._art_for = None
        self._tick = 0
        self.widget = common.vbox(0)
        self._build()
        ui.player.connect(self.update)
        self.widget.connect("map", lambda _w: self._arm())
        self.widget.connect("unmap", lambda _w: self._disarm())
        minimal.listen(self._rebuild)
        self.update()

    def _rebuild(self) -> None:
        common.clear(self.widget)
        self._art_for = None
        self._build()
        self.update()

    def _build(self) -> None:
        if minimal.active():
            self._build_minimal()
        else:
            self._build_regular()

    def _build_minimal(self) -> None:
        player = self.ui.player
        self.minimal = True
        self.widget.remove_css_class("xx-mu-mini")
        self.widget.add_css_class("xx-mm-mini")
        self.bar = minimal.hairline(0.0)
        self.bar.update_property([Gtk.AccessibleProperty.LABEL],
                                 ["Song progress"])
        self.widget.append(self.bar)
        row = common.hbox(4)
        row.set_margin_start(12)
        row.set_margin_end(10)
        opener = Gtk.Button()
        opener.add_css_class("xx-mm-row")
        opener.set_hexpand(True)
        self.line = minimal.text("", minimal.Tone.PLAIN, 14, chars=24)
        opener.set_child(self.line)
        opener.connect("clicked", lambda _b: self.open_playing())
        self.opener = opener
        row.append(opener)
        self.play = minimal.bracket_button("play", player.play_pause,
                                           minimal.Tone.STRONG, size=14)
        row.append(self.play)
        self.widget.append(row)

    def _build_regular(self) -> None:
        player = self.ui.player
        open_playing = self.open_playing
        self.minimal = False
        self.widget.remove_css_class("xx-mm-mini")
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
        if self.minimal:
            self._update_minimal(track)
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

    def _update_minimal(self, track) -> None:
        player = self.ui.player
        problem = player.problem
        if problem:
            self.line.set_text(problem.get("text", ""))
            minimal.set_tone(self.line, minimal.Tone.QUIET)
        else:
            self.line.set_text(minimal.title_artist(track.title,
                                                    track.artist_line))
            minimal.set_tone(self.line, minimal.Tone.PLAIN)
        self._progress()
        word = "pause" if player.playing else "play"
        self.play.get_child().set_text(minimal.bracket(word))
        common.name(self.play, word.capitalize())
        common.name(self.opener,
                    f"Now playing: {track.title}. Open the player.")
