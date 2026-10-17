"""Now Playing, sliding up over the shell; Lyrics and Up next inside it;
the sleep sheet.

Top: collapse, then the Sleep, Lyrics and Queue actions (inverted while
on; Sleep reads the time left while armed). The art view: a big square
cover, the title in the display face, the artists as tappable names, the
album. The lyrics and queue views swap the cover for a compact row and
their list. Always at the bottom: the scrubber with tabular times, the
transport (shuffle, previous, a round inverted play, next, repeat), and
Dislike · Playing from · Like when the engine can rate.

Minimal Mode (``xx_music.ui.minimal``): no cover anywhere. The top row is
words (``close``, then ``sleep``, ``lyrics``, ``queue``, Strong while on);
the title is the stage, in Space Mono at 26 px, with the artists as plain
words (no underline) under it; swipe it left or right to skip, up for
the queue, long-press for the song's menu. A hairline seek (a 1 px line
and a dot) replaces the scrubber, and the controls are brackets:
``[ like ] [ down ]``, ``[ prev ] [ pause ] [ next ]``,
``[ shuffle ] [ repeat ]``. The whole screen rebuilds when it flips.
"""

from __future__ import annotations

from gi.repository import GLib, GObject, Gtk, Pango

from xx_music.engine import LYRICS, Lyrics, Rating
from xx_music.tasks import Generation
from xx_music.ui import cast as cast_ui
from xx_music.ui import common, css, fmt, menus, minimal
from xx_music.ui.player import SLEEP_END_OF_SONG, SLEEP_OFF
from xx_music.ui.queue_page import QueueView
from xx_ui import widgets

SLEEP_MINUTES = (15, 30, 45, 60)
COVER_LIMIT = 284
TICK_MS = 500
LYRICS_HOLD_S = 3.0
PROBLEM_ACTIONS = {
    "helper-missing": ("setup-helper", "Set up"),
    "source-changed": ("update-engine", "Update"),
    "auth-expired": ("sign-in", "Sign in"),
    "auth-required": ("sign-in", "Sign in"),
}


class Scrubber(Gtk.DrawingArea):
    """Seek bar: 2 px trough, the played part bright, a round thumb;
    drag or tap to seek, arrow keys step 10 s."""

    __gtype_name__ = "XXMusicScrubber"
    __gsignals__ = {"seek": (GObject.SignalFlags.RUN_FIRST, None, (float,))}

    def __init__(self, hairline: bool = False) -> None:
        super().__init__(accessible_role=Gtk.AccessibleRole.SLIDER)
        self.position = 0.0
        self.duration = 0.0
        self.hairline = hairline
        self._drag: float | None = None
        self._start_x = 0.0
        self.set_content_height(28 if hairline else 32)
        self.set_hexpand(True)
        self.set_focusable(True)
        self.set_draw_func(self._draw)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Position"])
        drag = Gtk.GestureDrag.new()
        drag.connect("drag-begin", self._begin)
        drag.connect("drag-update", self._update)
        drag.connect("drag-end", self._end)
        self.add_controller(drag)
        keys = Gtk.EventControllerKey.new()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

    @property
    def dragging(self) -> bool:
        return self._drag is not None

    def set_state(self, position: float, duration: float) -> None:
        self.position, self.duration = position, duration
        self.update_property(
            [Gtk.AccessibleProperty.VALUE_MIN,
             Gtk.AccessibleProperty.VALUE_MAX,
             Gtk.AccessibleProperty.VALUE_NOW,
             Gtk.AccessibleProperty.VALUE_TEXT],
            [0.0, float(max(duration, 1.0)), float(position),
             fmt.clock(position)])
        self.queue_draw()

    def _at(self, x: float) -> float:
        width = max(self.get_width() - 12, 1)
        return min(max((x - 6) / width, 0.0), 1.0) * self.duration

    def _begin(self, _gesture, x, _y) -> None:
        if self.duration <= 0:
            return
        self._start_x = x
        self._drag = self._at(x)
        self.queue_draw()

    def _update(self, _gesture, dx, _dy) -> None:
        if self._drag is not None:
            self._drag = self._at(self._start_x + dx)
            self.queue_draw()

    def _end(self, _gesture, dx, _dy) -> None:
        if self._drag is None:
            return
        target = self._at(self._start_x + dx)
        self._drag = None
        self.position = target
        self.emit("seek", target)
        self.queue_draw()

    def _key(self, _ctrl, keyval, _code, _state) -> bool:
        from gi.repository import Gdk

        step = {Gdk.KEY_Left: -10.0, Gdk.KEY_Right: 10.0}.get(keyval)
        if step is None or self.duration <= 0:
            return False
        self.emit("seek", min(max(self.position + step, 0.0),
                              self.duration))
        return True

    def _draw(self, widget, cr, width, height) -> None:
        import math

        color = widget.get_color()
        rgb = (color.red, color.green, color.blue)
        mid = height / 2
        shown = self._drag if self._drag is not None else self.position
        frac = shown / self.duration if self.duration > 0 else 0.0
        frac = min(max(frac, 0.0), 1.0)
        if self.hairline:
            minimal.draw_hairline(cr, width, height, frac, rgb,
                                  self._drag is not None)
            return
        left, span = 6, max(width - 12, 1)
        cr.set_source_rgba(*rgb, 0.22)
        cr.rectangle(left, mid - 1, span, 2)
        cr.fill()
        cr.set_source_rgba(*rgb, 1.0)
        cr.rectangle(left, mid - 1, span * frac, 2)
        cr.fill()
        radius = 7 if self._drag is not None else 5
        cr.arc(left + span * frac, mid, radius, 0, 2 * math.pi)
        cr.fill()


def _key(icon: str, text: str, callback, px: int,
         cls: str = "xx-mu-key") -> Gtk.Button:
    button = Gtk.Button()
    button.add_css_class(cls)
    image = widgets.icon_image(icon, px)
    button.set_child(image)
    button.set_valign(Gtk.Align.CENTER)
    button.set_halign(Gtk.Align.CENTER)
    button.connect("clicked", lambda _b: callback())
    button.image = image
    return common.name(button, text)


def _act(icon: str, text: str, callback) -> Gtk.Button:
    """A caps text action (Sleep, Lyrics, Queue); the glyph stands in
    for the word at large text sizes."""
    button = Gtk.Button()
    button.add_css_class("xx-mu-act")
    row = common.hbox(0)
    image = widgets.icon_image(icon, 16)
    row.append(image)
    text_label = Gtk.Label(label=text.upper())
    text_label.set_ellipsize(Pango.EllipsizeMode.NONE)
    row.append(text_label)
    button.set_child(row)
    button.image = image
    button.text_label = text_label
    button.connect("clicked", lambda _b: callback())
    common.name(button, text)
    return button


class NowPlaying:
    def __init__(self, ui, close) -> None:
        self.ui = ui
        self.close = close
        self.view = "art"
        self._art_for = None
        self._compact_for = None
        self._lyrics_for = None
        self._lyrics: Lyrics | None = None
        self._lyric_rows: list[tuple[int | None, Gtk.Widget]] = []
        self._lyric_now = -1
        self._manual_until = 0.0
        self._tick = 0
        self._tick_key = None
        self._gen = Generation()
        self.words = False
        self.queue = QueueView(ui)
        self._queue_box = None

        root = common.vbox(0)
        root.add_css_class("xx-mu-np")
        root.add_css_class("xx-mu")
        self.widget = root
        self._build()
        ui.player.connect(self.update)
        minimal.listen(self._rebuild)
        self.update()

    def _build(self) -> None:
        """The screen, regular or minimal (``self.words``)."""
        self.words = minimal.active()
        root = self.widget
        root.append(self._top_bar())
        self.cast_line = cast_ui.playing_line(self.ui)
        self.cast_line.set_margin_start(16)
        self.cast_line.set_margin_end(16)
        root.append(self.cast_line)
        self.cast_vol = cast_ui.volume_row(self.ui)
        self.cast_vol.set_margin_start(16)
        self.cast_vol.set_margin_end(16)
        root.append(self.cast_vol)
        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(120)
        self.stack.add_named(self._art_view(), "art")
        self.stack.add_named(self._lyrics_view(), "lyrics")
        if self._queue_box is not None:
            self._queue_box.remove(self.queue.widget)
        queue_box = common.vbox(0)
        self.queue_head = common.vbox(0)
        queue_box.append(self.queue_head)
        queue_box.append(self.queue.widget)
        self._queue_box = queue_box
        self.stack.add_named(queue_box, "queue")
        root.append(self.stack)
        root.append(self._controls())

    def _rebuild(self) -> None:
        """Minimal Mode flipped: build the screen again, same view."""
        common.clear(self.widget)
        self._art_for = None
        self._compact_for = None
        self._lyrics_for = None
        self._build()
        self.set_view(self.view)

    # -- building ------------------------------------------------------------------
    def _top_bar(self) -> Gtk.Widget:
        if self.words:
            return self._word_bar()
        bar = common.hbox(6)
        bar.set_margin_start(8)
        bar.set_margin_end(14)
        bar.set_margin_top(8)
        bar.set_margin_bottom(4)
        collapse = common.icon_button("go-down-symbolic", "Close the player",
                                      self.close, 22)
        bar.append(collapse)
        self.cast_btn = cast_ui.cast_button(self.ui, 18)
        self.cast_btn.set_valign(Gtk.Align.CENTER)
        bar.append(self.cast_btn)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        bar.append(spacer)
        self.sleep_act = _act("weather-clear-night-symbolic", "Sleep",
                              self.open_sleep)
        self.lyrics_act = _act("format-justify-left-symbolic", "Lyrics",
                               lambda: self.toggle("lyrics"))
        self.queue_act = _act("view-list-symbolic", "Queue",
                              lambda: self.toggle("queue"))
        for act in (self.sleep_act, self.lyrics_act, self.queue_act):
            act.set_valign(Gtk.Align.CENTER)
            bar.append(act)
        return bar

    def _word_bar(self) -> Gtk.Widget:
        """``close`` … ``cast`` ``sleep`` ``lyrics`` ``queue``: words,
        Strong while on."""
        Tone = minimal.Tone
        bar = common.hbox(0)
        bar.set_margin_start(24)
        bar.set_margin_end(18)
        bar.set_margin_top(4)
        bar.set_margin_bottom(4)
        close = minimal.word_button("close", self.close, Tone.QUIET,
                                    spoken="Close the player")
        close.add_css_class("xx-mm-flush")
        bar.append(close)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        bar.append(spacer)
        self.sleep_act = minimal.word_button("sleep", self.open_sleep,
                                             Tone.QUIET)
        self.lyrics_act = minimal.word_button(
            "lyrics", lambda: self.toggle("lyrics"), Tone.QUIET)
        self.queue_act = minimal.word_button(
            "queue", lambda: self.toggle("queue"), Tone.QUIET)
        self.cast_btn = cast_ui.cast_button(self.ui)
        for word in (self.cast_btn, self.sleep_act, self.lyrics_act,
                     self.queue_act):
            word.add_css_class("xx-mm-tight")
        bar.append(minimal.flow([self.cast_btn, self.sleep_act,
                                 self.lyrics_act, self.queue_act],
                                spacing=0))
        bar.get_last_child().set_halign(Gtk.Align.END)
        return bar

    def _lit(self, act: Gtk.Button, on: bool) -> None:
        if self.words:
            minimal.set_tone(act, minimal.toggled(on))
        elif on:
            act.add_css_class("on")
        else:
            act.remove_css_class("on")

    def _minimal_art_view(self) -> Gtk.Widget:
        """No cover: the title is the stage. Swipe left/right to skip,
        up for the queue; long-press for the song's menu."""
        body = common.vbox(0)
        body.set_valign(Gtk.Align.CENTER)
        body.set_margin_start(28)
        body.set_margin_end(28)
        self.cover_slot = common.vbox(0)
        self.title = common.label("", "xx-mm-np-title", lines=4, chars=14)
        self.title.set_wrap(True)
        body.append(self.title)
        self.artists = common.hbox(0)
        self.artists.set_margin_top(8)
        body.append(self.artists)
        self.album = minimal.text("", minimal.Tone.QUIET, 13, chars=30)
        self.album.set_margin_top(2)
        body.append(self.album)
        # Long-press the title for the song's menu (an artist's name for
        # Kill artist); swipe anywhere on the stage.
        common.on_long_press(self.title, self.open_menu)
        swipe = Gtk.GestureSwipe.new()
        swipe.connect("swipe", self._swiped)
        body.add_controller(swipe)
        holder = common.vbox(0)
        holder.set_vexpand(True)
        holder.append(body)
        body.set_vexpand(True)
        return common.scroller(holder)

    def _swiped(self, _gesture, vx: float, vy: float) -> None:
        player = self.ui.player
        if abs(vx) > abs(vy) and abs(vx) > 400:
            (player.previous if vx > 0 else player.next)()
        elif vy < -400:
            self.toggle("queue")

    def _art_view(self) -> Gtk.Widget:
        if self.words:
            return self._minimal_art_view()
        body = common.vbox(0)
        body.set_valign(Gtk.Align.CENTER)
        body.set_margin_start(28)
        body.set_margin_end(28)
        self.cover_slot = common.vbox(0)
        self.cover_slot.set_margin_top(8)
        body.append(self.cover_slot)
        texts = common.vbox(4)
        texts.set_margin_top(20)
        self.title = common.label("", "xx-mu-np-title", lines=2, chars=16)
        texts.append(self.title)
        self.artists = common.hbox(2)
        texts.append(self.artists)
        self.album = common.label("", "xx-mu-meta", chars=30)
        texts.append(self.album)
        body.append(texts)
        # Swipe the cover/stage to change songs, as in Minimal Mode:
        # left = next, right = previous, up = queue.
        swipe = Gtk.GestureSwipe.new()
        swipe.connect("swipe", self._swiped)
        body.add_controller(swipe)
        scroll = common.scroller(body)
        return scroll

    def _lyrics_view(self) -> Gtk.Widget:
        box = common.vbox(0)
        self.lyrics_head = common.vbox(0)
        box.append(self.lyrics_head)
        self.lyrics_body = common.vbox(0)
        self.lyrics_body.add_css_class("xx-page-body")
        self.lyrics_body.set_margin_top(8)
        self.lyrics_body.set_margin_bottom(40)
        self.lyrics_scroll = common.scroller(self.lyrics_body)
        adj = self.lyrics_scroll.get_vadjustment()
        drag = Gtk.GestureDrag.new()
        drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        drag.connect("drag-begin", lambda *_a: self._manual())
        self.lyrics_scroll.add_controller(drag)
        wheel = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.connect("scroll", lambda *_a: (self._manual(), False)[1])
        self.lyrics_scroll.add_controller(wheel)
        self._lyrics_adj = adj
        box.append(self.lyrics_scroll)
        return box

    def _minimal_controls(self) -> Gtk.Widget:
        """Hairline seek, then brackets: thumbs, transport, modes."""
        Tone = minimal.Tone
        player = self.ui.player
        box = common.vbox(0)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_bottom(12)
        self.problem = common.hbox(10)
        self.problem.set_margin_start(6)
        self.problem.set_margin_end(6)
        self.problem.set_margin_bottom(8)
        box.append(self.problem)
        self.scrubber = Scrubber(hairline=True)
        self.scrubber.connect("seek", lambda _s, at: player.seek(at))
        box.append(self.scrubber)
        times = common.hbox(0)
        times.set_margin_start(4)
        times.set_margin_end(4)
        self.elapsed = minimal.text("0:00", Tone.QUIET, 12, chars=8)
        self.elapsed.add_css_class("xx-mu-num")
        self.elapsed.set_hexpand(True)
        self.total = minimal.text("0:00", Tone.QUIET, 12, chars=8,
                                  xalign=1.0)
        self.total.add_css_class("xx-mu-num")
        times.append(self.elapsed)
        times.append(self.total)
        box.append(times)
        self.like = minimal.bracket_button(
            "like", lambda: self._rate(Rating.LIKE), Tone.QUIET, size=13)
        self.dislike = minimal.bracket_button(
            "down", lambda: self._rate(Rating.DISLIKE), Tone.QUIET, size=13,
            spoken="Dislike")
        self.thumbs = minimal.flow([self.like, self.dislike], center=True,
                                   spacing=24)
        self.thumbs.set_margin_top(6)
        box.append(self.thumbs)
        # One line at 320 px and at 2x text: smaller words when large.
        large = css.large_text()
        self.prev = minimal.bracket_button("prev", player.previous,
                                           size=13 if large else 15,
                                           spoken="Previous song")
        self.play = minimal.bracket_button("play", player.play_pause,
                                           Tone.STRONG,
                                           size=15 if large else 17)
        self.next = minimal.bracket_button("next", player.next,
                                           size=13 if large else 15,
                                           spoken="Next song")
        box.append(minimal.flow([self.prev, self.play, self.next],
                                center=True, spacing=4))
        self.shuffle = minimal.bracket_button(
            "shuffle", player.toggle_shuffle, Tone.QUIET, size=13)
        self.repeat = minimal.bracket_button(
            "repeat", player.cycle_repeat, Tone.QUIET, size=13)
        box.append(minimal.flow([self.shuffle, self.repeat], center=True,
                                spacing=16))
        self.from_caption = minimal.text("", Tone.QUIET, 12, chars=8)
        self.from_caption.set_visible(False)
        self.from_text = minimal.text("", Tone.QUIET, 12, xalign=0.5,
                                      chars=30)
        self.from_text.set_margin_top(4)
        box.append(self.from_text)
        return box

    def _controls(self) -> Gtk.Widget:
        if self.words:
            return self._minimal_controls()
        player = self.ui.player
        box = common.vbox(0)
        box.set_margin_start(22)
        box.set_margin_end(22)
        box.set_margin_bottom(14)
        self.problem = common.hbox(10)
        self.problem.set_margin_start(6)
        self.problem.set_margin_end(6)
        self.problem.set_margin_bottom(8)
        box.append(self.problem)
        self.scrubber = Scrubber()
        self.scrubber.connect("seek", lambda _s, at: player.seek(at))
        box.append(self.scrubber)
        times = common.hbox(0)
        times.set_margin_start(6)
        times.set_margin_end(6)
        self.elapsed = common.label("0:00", "xx-time xx-mu-num", chars=8)
        self.elapsed.set_hexpand(True)
        self.total = common.label("0:00", "xx-time xx-mu-num", xalign=1.0,
                                  chars=8)
        times.append(self.elapsed)
        times.append(self.total)
        box.append(times)

        transport = common.hbox(0)
        transport.set_margin_top(10)
        self.shuffle = _key("media-playlist-shuffle-symbolic", "Shuffle",
                            player.toggle_shuffle, 22)
        self.prev = _key("media-skip-backward-symbolic", "Previous song",
                         player.previous, 30)
        self.play_icon = widgets.icon_image("media-playback-start-symbolic",
                                            28)
        self.play = Gtk.Button()
        self.play.add_css_class("xx-mu-play")
        self.play.set_child(self.play_icon)
        self.play.set_halign(Gtk.Align.CENTER)
        self.play.set_valign(Gtk.Align.CENTER)
        self.play.connect("clicked", lambda _b: player.play_pause())
        self.next = _key("media-skip-forward-symbolic", "Next song",
                         player.next, 30)
        self.repeat = _key("media-playlist-repeat-symbolic", "Repeat",
                           player.cycle_repeat, 22)
        for widget in (self.shuffle, self.prev, self.play, self.next,
                       self.repeat):
            widget.set_hexpand(True)
            transport.append(widget)
        box.append(transport)

        self.footer = common.hbox(4)
        self.footer.set_margin_top(12)
        self.dislike = _key("xx-music-thumb-down-symbolic", "Dislike",
                            lambda: self._rate(Rating.DISLIKE), 18,
                            "xx-mu-thumb")
        self.like = _key("xx-music-thumb-up-symbolic", "Like",
                         lambda: self._rate(Rating.LIKE), 18, "xx-mu-thumb")
        middle = common.vbox(2)
        middle.set_hexpand(True)
        middle.set_valign(Gtk.Align.CENTER)
        self.from_caption = common.label("PLAYING FROM", "xx-mu-label",
                                         xalign=0.5, chars=18)
        self.from_text = common.label("", "xx-mu-meta xx-mu-strong",
                                      xalign=0.5, chars=24)
        middle.append(self.from_caption)
        middle.append(self.from_text)
        self.footer.append(self.dislike)
        self.footer.append(middle)
        self.footer.append(self.like)
        box.append(self.footer)
        return box

    # -- views ------------------------------------------------------------------------
    def set_view(self, view: str) -> None:
        self.view = view if view in ("art", "lyrics", "queue") else "art"
        self.stack.set_visible_child_name(self.view)
        for act, name in ((self.lyrics_act, "lyrics"),
                          (self.queue_act, "queue")):
            self._lit(act, self.view == name)
        if self.view == "lyrics":
            self._load_lyrics()
        elif self.view == "queue":
            self.queue.update()
        self._compact_for = None
        self.update()

    def toggle(self, view: str) -> None:
        self.set_view("art" if self.view == view else view)

    def shown(self) -> None:
        if not self._tick:
            self._tick = GLib.timeout_add(TICK_MS, self._on_tick)
        self.update()

    def _on_tick(self) -> bool:
        if not self.widget.get_mapped():
            self._tick = 0
            return False
        player = self.ui.player
        key = (round(player.position_s), player.sleep_remaining_s)
        if player.playing and key != self._tick_key:
            self._tick_key = key
            self._times()
            self._sleep_label()
            if self.view == "lyrics":
                self._follow_lyrics()
        return True

    # -- updating --------------------------------------------------------------------
    def update(self) -> None:
        player = self.ui.player
        cast = player.cast
        cast_ui.sync_line(self.cast_line, cast)
        self.cast_vol.sync(cast)
        cast_ui.set_casting(self.cast_btn, player.casting)
        track = player.track
        if track is None:
            return
        if self._art_for != track.id:
            self._art_for = track.id
            common.clear(self.cover_slot)
            if not self.words:
                self.cover_slot.append(common.art(
                    self.ui, track.art_url, track.title, 0, "r16",
                    limit=COVER_LIMIT if not css.large_text() else 200))
            if self.view == "lyrics":
                self._load_lyrics()
        if self._compact_for != (track.id, self.view):
            self._compact_for = (track.id, self.view)
            head = self.lyrics_head if self.view == "lyrics" \
                else self.queue_head
            common.clear(head)
            if self.view != "art":
                head.append(self._compact(track))
        self.title.set_text(track.title)
        common.clear(self.artists)
        if self.words:
            self._minimal_artists(track)
        for index, artist in enumerate(() if self.words
                                       else track.artists):
            if index:
                self.artists.append(common.label(",", "xx-secondary",
                                                 chars=1))
            if artist.id:
                link = Gtk.Button()
                link.add_css_class("xx-mu-link")
                link.set_child(common.label(artist.name, "xx-secondary",
                                            chars=18))
                link.connect("clicked", lambda _b, a=artist: (
                    self.close(), self.ui.shell.open_artist(a.id, a.name)))
                common.name(link, f"Open {artist.name}")
                from xx_music.ui.kill import request_kill

                common.on_long_press(
                    link, lambda a=artist: request_kill(
                        self.ui, a.id, a.name))
                self.artists.append(link)
            else:
                name_label = common.label(artist.name, "xx-secondary",
                                          chars=18)
                self.artists.append(name_label)
                if artist.name.strip():
                    from xx_music.ui.kill import request_kill

                    common.on_long_press(
                        name_label, lambda a=artist: request_kill(
                            self.ui, "", a.name))
        album = track.album.title if track.album else ""
        self.album.set_text(album)
        self.album.set_visible(bool(album) and not player.problem)
        self._problem(player.problem)
        self._times()
        self._transport()
        self._sleep_label()
        self._footer(track)

    def _minimal_artists(self, track) -> None:
        """Plain words, no underline: tap opens the artist, long-press
        offers Kill artist."""
        from xx_music.ui.kill import request_kill

        names = [a for a in track.artists if a.name]
        line = []
        for index, artist in enumerate(names):
            if artist.id:
                word = minimal.word_button(
                    artist.name, lambda a=artist: (
                        self.close(), self.ui.shell.open_artist(a.id,
                                                                a.name)),
                    minimal.Tone.QUIET, 15, f"Open {artist.name}", chars=18)
                if index == 0:
                    word.add_css_class("xx-mm-flush")
            else:
                word = minimal.text(artist.name, minimal.Tone.QUIET, 15,
                                    chars=18)
            common.on_long_press(word, lambda a=artist: request_kill(
                self.ui, a.id or "", a.name))
            line.append(word)
            if index != len(names) - 1:
                line.append(minimal.text("·", minimal.Tone.QUIET, 15,
                                         chars=1))
        if line:
            self.artists.append(minimal.flow(line, spacing=0))

    def _compact(self, track) -> Gtk.Widget:
        row = common.hbox(14)
        row.add_css_class("xx-page-body")
        row.set_margin_top(4)
        row.set_margin_bottom(8)
        if not self.words:
            row.append(common.art(self.ui, track.art_url, track.title, 72,
                                  "r8"))
        texts = common.vbox(2)
        texts.set_valign(Gtk.Align.CENTER)
        texts.set_hexpand(True)
        texts.append(common.label(track.title, "xx-mu-h2", lines=2,
                                  chars=16))
        texts.append(common.label(track.artist_line, "xx-secondary",
                                  chars=22))
        row.append(texts)
        return row

    def _problem(self, problem: dict | None) -> None:
        common.clear(self.problem)
        self.problem.set_visible(bool(problem))
        if not problem:
            return
        text = common.label(problem.get("text", ""), "xx-mu-meta xx-mu-error",
                            lines=3, chars=26)
        text.set_hexpand(True)
        self.problem.append(text)
        action = PROBLEM_ACTIONS.get(str(problem.get("kind", "")))
        if action and action[0] in self.ui.hooks.actions:
            button = minimal.outline(
                action[1], lambda a=action[0]: (self.close(),
                                                self.ui.action(a)))
            common.fit(button, 8)
            button.set_valign(Gtk.Align.CENTER)
            self.problem.append(button)

    def _times(self) -> None:
        player = self.ui.player
        position, duration = player.position_s, player.duration_s
        if (position, duration) != (self.scrubber.position,
                                    self.scrubber.duration) \
                and not self.scrubber.dragging:
            self.scrubber.set_state(position, duration)
        self.elapsed.set_text(fmt.clock(position))
        self.total.set_text(fmt.clock(duration))

    def _minimal_transport(self) -> None:
        player = self.ui.player
        word = "pause" if player.playing else "play"
        self.play.get_child().set_text(minimal.bracket(word))
        common.name(self.play, word.capitalize())
        minimal.set_tone(self.shuffle, minimal.toggled(player.shuffle))
        common.name(self.shuffle, "Shuffle on" if player.shuffle
                    else "Shuffle off")
        mode = player.repeat
        self.repeat.get_child().set_text(minimal.bracket(
            "repeat 1" if mode == "one" else "repeat"))
        minimal.set_tone(self.repeat, minimal.toggled(mode != "none"))
        common.name(self.repeat, {"none": "Repeat off", "all": "Repeat all",
                                  "one": "Repeat one"}[mode])

    def _transport(self) -> None:
        if self.words:
            self._minimal_transport()
            return
        player = self.ui.player
        playing = player.playing
        self.play_icon.set_from_icon_name(
            "media-playback-pause-symbolic" if playing
            else "media-playback-start-symbolic")
        common.name(self.play, "Pause" if playing else "Play")
        if player.shuffle:
            self.shuffle.remove_css_class("dim")
        else:
            self.shuffle.add_css_class("dim")
        common.name(self.shuffle, "Shuffle on" if player.shuffle
                    else "Shuffle off")
        mode = player.repeat
        self.repeat.image.set_from_icon_name(
            "media-playlist-repeat-song-symbolic" if mode == "one"
            else "media-playlist-repeat-symbolic")
        if mode == "none":
            self.repeat.add_css_class("dim")
        else:
            self.repeat.remove_css_class("dim")
        common.name(self.repeat, {"none": "Repeat off", "all": "Repeat all",
                                  "one": "Repeat one"}[mode])

    def _sleep_label(self) -> None:
        player = self.ui.player
        armed = player.sleep is not None
        if player.sleep_end_of_song:
            text = "End"
        elif armed:
            text = fmt.sleep_text(player.sleep_remaining_s)
        else:
            text = "Sleep"
        if self.words:
            self.sleep_act.get_child().set_text(
                "sleep" if not armed else
                f"sleep {'end' if text == 'End' else text}")
        else:
            self.sleep_act.text_label.set_text(text.upper())
        self._lit(self.sleep_act, armed)
        common.name(self.sleep_act, "Sleep timer, " + (
            f"{text} left" if armed and text != "End"
            else "end of this song" if armed else "off"))
        large = css.large_text()
        for act in () if self.words else (self.sleep_act, self.lyrics_act,
                                           self.queue_act):
            words = not large or (act is self.sleep_act and armed)
            act.text_label.set_visible(words)
            act.image.set_visible(not words)
        self.lyrics_act.set_visible(self.ui.supports(LYRICS))

    def _footer(self, track) -> None:
        player = self.ui.player
        self.like.set_visible(True)
        self.dislike.set_visible(True)
        rating = player.rating(track.id)
        if self.words:
            # Local thumbs always work (signed out, any engine).
            self.thumbs.set_visible(True)
        for button, value, word in ((self.like, Rating.LIKE, "Like"),
                                    (self.dislike, Rating.DISLIKE,
                                     "Dislike")):
            on = rating == value
            self._lit(button, on)
            common.name(button, f"Remove {word.lower()}" if on else word)
        origin = fmt.origin_text(player.origin, track)
        self.from_text.set_text(f"from {origin}" if self.words and origin
                                else origin)
        self.from_caption.set_visible(bool(origin) and not self.words)
        self.from_text.set_visible(bool(origin))

    def _rate(self, rating: Rating) -> None:
        track = self.ui.player.track
        if track is not None:
            self.ui.rate(track, rating)

    # -- lyrics -----------------------------------------------------------------------
    def _load_lyrics(self) -> None:
        track = self.ui.player.track
        if track is None or self._lyrics_for == track.id:
            return
        self._lyrics_for = track.id
        self._lyrics = None
        self._paint_lyrics(loading=True)
        self.ui.run(LYRICS, lambda: self.ui.engine.lyrics(track.id),
                    self._got_lyrics, lambda _e: self._got_lyrics(None),
                    self._gen)

    def _got_lyrics(self, lyrics: Lyrics | None) -> None:
        self._lyrics = lyrics
        self._paint_lyrics()

    def _paint_lyrics(self, loading: bool = False) -> None:
        body = self.lyrics_body
        common.clear(body)
        self._lyric_rows = []
        self._lyric_now = -1
        if loading:
            body.append(common.muted("Looking for lyrics…"))
            return
        lyrics = self._lyrics
        if lyrics is None or not lyrics.lines:
            body.append(common.muted("No lyrics for this song."))
            return
        synced = lyrics.synced
        for line in lyrics.lines:
            text = common.para(line.text or "♪", "xx-mu-lyric-text", 26)
            if synced and line.start_ms is not None:
                button = Gtk.Button()
                button.add_css_class("xx-mu-lyric")
                button.set_child(text)
                at = line.start_ms / 1000.0
                button.connect("clicked", lambda _b, s=at:
                               self.ui.player.seek(s))
                common.name(button, f"{line.text} (play from here)")
                body.append(button)
            else:
                text.set_margin_top(6)
                text.set_margin_bottom(6)
                body.append(text)
            self._lyric_rows.append((line.start_ms if synced else None, text))
        if lyrics.source:
            body.append(common.caps(lyrics.source))
            body.get_last_child().set_margin_top(20)
        self._follow_lyrics(force=True)

    def _manual(self) -> None:
        self._manual_until = GLib.get_monotonic_time() / 1e6 + LYRICS_HOLD_S

    def _follow_lyrics(self, force: bool = False) -> None:
        if not self._lyric_rows:
            return
        position_ms = self.ui.player.position_s * 1000
        timed = self._lyric_rows[0][0] is not None
        if timed:
            now = -1
            for index, (start, _w) in enumerate(self._lyric_rows):
                if start is not None and start <= position_ms:
                    now = index
        else:
            frac = self.ui.player.fraction
            now = min(int(frac * len(self._lyric_rows)),
                      len(self._lyric_rows) - 1)
        if now == self._lyric_now and not force:
            return
        self._lyric_now = now
        for index, (_s, widget) in enumerate(self._lyric_rows):
            widget.remove_css_class("now")
            widget.remove_css_class("past")
            if index == now:
                widget.add_css_class("now")
            elif index < now:
                widget.add_css_class("past")
        if GLib.get_monotonic_time() / 1e6 < self._manual_until:
            return
        if 0 <= now:
            target = self._lyric_rows[now][1]
            common.later(60, lambda: self._center(target))

    def _center(self, widget: Gtk.Widget) -> None:
        import gi

        gi.require_version("Graphene", "1.0")
        from gi.repository import Graphene

        ok, point = widget.compute_point(self.lyrics_body,
                                         Graphene.Point().init(0, 0))
        if not ok:
            return
        page = self._lyrics_adj.get_page_size()
        self._lyrics_adj.set_value(max(0.0, point.y - page * 0.35))

    # -- sleep sheet --------------------------------------------------------------------
    def open_sleep(self):
        player = self.ui.player
        dialog, box = common.sheet("Sleep timer")
        status = common.vbox(4)
        box.append(status)
        box.append(common.caps("Minutes"))
        grid_slot = common.vbox(0)
        box.append(grid_slot)
        end_slot = common.vbox(0)
        end_slot.set_margin_top(8)
        box.append(end_slot)

        def refresh() -> None:
            common.clear(status)
            common.clear(grid_slot)
            common.clear(end_slot)
            armed = player.sleep is not None
            if armed:
                status.append(common.caps(
                    "Pauses at the end of this song"
                    if player.sleep_end_of_song else "Pauses in"))
                if not player.sleep_end_of_song:
                    status.append(common.label(
                        fmt.sleep_text(player.sleep_remaining_s),
                        "xx-mu-readout xx-mu-num", chars=8))
                off = minimal.outline("Turn off",
                                             lambda: do(SLEEP_OFF))
                common.fit(off, 12)
                off.set_margin_top(8)
                off.set_margin_bottom(10)
                status.append(off)
            else:
                line = common.para(
                    "Off. The music fades out over the last 10 seconds.",
                    "xx-secondary")
                line.set_margin_bottom(10)
                status.append(line)
            mode = (player.sleep or {}).get("mode", SLEEP_OFF)
            grid_slot.append(common.grid_chips([
                (f"{m}", mode == m * 60, lambda m=m: do(m * 60))
                for m in SLEEP_MINUTES], per_line=4))
            end_slot.append(common.grid_chips([
                ("End of this song", player.sleep_end_of_song,
                 lambda: do(SLEEP_END_OF_SONG))], per_line=1))

        def do(seconds: int) -> None:
            player.set_sleep(seconds)
            common.later(120, refresh)

        refresh()
        return common.present(dialog, self.ui.shell.window)

    def open_menu(self):
        track = self.ui.player.track
        if track is not None:
            return menus.track_menu(self.ui, track)
        return None
