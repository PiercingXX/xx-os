"""Shared widgets for the music screens, thin over xx_ui.widgets.

Art tiles (monochrome initials until the real art lands), song rows,
shelf tiles, the 2 px lines (progress and the sweeping loading line),
blinking busy squares, tags, bottom sheets and their one presentation
seam. Every icon-only control carries a spoken label and a 48 px target;
every label ellipsizes or wraps, so a 320 px window still fits.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib, Gtk, Pango  # noqa: E402

from xx_music.engine import Item, ItemKind, Track  # noqa: E402
from xx_music.ui import css, fmt, minimal  # noqa: E402
from xx_ui import widgets  # noqa: E402

#: Display roles take the brand display face (Space Mono over the house
#: mono; a family the person picked stays in charge).
DISPLAY_ROLES = frozenset({"xx-mu-h1", "xx-mu-h2", "xx-mu-np-title",
                           "xx-mm-np-title",
                           "xx-mu-readout", "xx-mu-code"})

TILE_PX = 120
ROW_ART_PX = 48


# -- text ---------------------------------------------------------------------

def label(text: str, cls: str = "xx-body", lines: int = 1,
          xalign: float = 0.0, chars: int = 24) -> Gtk.Label:
    """A label that never widens the window: ellipsized, or wrapped to
    ``lines`` then ellipsized."""
    widget = Gtk.Label(label=text)
    for name_ in cls.split():
        widget.add_css_class(name_)
        if name_ in DISPLAY_ROLES:
            widget.add_css_class("xx-display-face")
    widget.set_xalign(xalign)
    widget.set_max_width_chars(chars)
    widget.set_ellipsize(Pango.EllipsizeMode.END)
    if lines > 1:
        widget.set_wrap(True)
        widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        widget.set_lines(lines)
    return widget


def para(text: str, cls: str = "xx-body", chars: int = 32) -> Gtk.Label:
    """Free text: wraps fully, never ellipsized."""
    widget = Gtk.Label(label=text)
    for name_ in cls.split():
        widget.add_css_class(name_)
        if name_ in DISPLAY_ROLES:
            widget.add_css_class("xx-display-face")
    widget.set_xalign(0.0)
    widget.set_wrap(True)
    widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    widget.set_max_width_chars(chars)
    return widget


def muted(text: str) -> Gtk.Label:
    """The one muted line for empty, error and offline states."""
    line_ = para(text, "xx-secondary")
    line_.set_margin_top(14)
    line_.set_margin_bottom(14)
    return line_


def caps(text: str, chars: int = 28) -> Gtk.Label:
    return label(text.upper(), "xx-mu-label", chars=chars)


def name(widget: Gtk.Widget, text: str, tip: bool = False) -> Gtk.Widget:
    """Accessible label (and, with ``tip``, a tooltip)."""
    if tip and widget.get_tooltip_text() != text:
        widget.set_tooltip_text(text)
    widget.update_property([Gtk.AccessibleProperty.LABEL], [text])
    return widget


def fit(button: Gtk.Button, chars: int) -> Gtk.Button:
    """A text button that ellipsizes instead of widening the window."""
    child = button.get_child()
    if button.has_css_class("xx-mm-btn"):
        return button  # minimal words size themselves
    if isinstance(child, Gtk.Label):
        # Short words never ellipsize (letter-spaced caps would misfire);
        # only text longer than the budget can.
        child.set_max_width_chars(chars)
        child.set_ellipsize(Pango.EllipsizeMode.END
                            if len(child.get_text()) > chars
                            else Pango.EllipsizeMode.NONE)
    return button


def icon_button(icon: str, text: str, callback, px: int = 20
                ) -> Gtk.Button:
    button = Gtk.Button()
    button.add_css_class("xx-btn")
    button.add_css_class("xx-btn-icon")
    button.set_child(widgets.icon_image(icon, px))
    button.set_size_request(48, 48)
    button.set_valign(Gtk.Align.CENTER)
    if callback is not None:
        button.connect("clicked", lambda _b: callback())
    return name(button, text, tip=True)


def header(back, title: str, subtitle: str = "",
           icon: str = "", icon_text: str = "", icon_cb=None) -> Gtk.Box:
    """xx_ui's compact sub-page bar with labelled 48 px targets; in
    Minimal Mode, words: ``back``, the caption, the trailing action."""
    if minimal.active():
        return minimal_header(back, title, subtitle,
                              (icon_text or title) if icon else "", icon_cb)
    bar = widgets.compact_header(back, title, subtitle, icon, icon_cb)
    back_button = bar.get_first_child()
    back_button.set_size_request(48, 48)
    name(back_button, "Back")
    if icon:
        trailing = bar.get_last_child()
        trailing.set_size_request(48, 48)
        name(trailing, icon_text or title)
    return bar


def minimal_header(back, title: str, subtitle: str = "",
                   action: str = "", action_cb=None) -> Gtk.Box:
    bar = hbox(4)
    bar.add_css_class("xx-page-body")
    bar.add_css_class("xx-mm-header")
    bar.set_margin_top(4)
    bar.set_margin_bottom(4)
    back_word = minimal.word_button("back", back, minimal.Tone.QUIET,
                                    spoken="Back")
    back_word.add_css_class("xx-mm-flush")
    bar.append(back_word)
    texts = vbox(0)
    texts.set_hexpand(True)
    texts.set_valign(Gtk.Align.CENTER)
    caption = minimal.text(title.upper(), minimal.Tone.QUIET, 12, chars=18)
    caption.add_css_class("xx-mm-caps")
    texts.append(caption)
    if subtitle:
        texts.append(minimal.text(subtitle, minimal.Tone.QUIET, 12,
                                  chars=24))
    bar.append(texts)
    if action and action_cb is not None:
        words = {"more options": "more"}.get(action.lower(), action.lower())
        if len(words) > 8:
            words = words.split()[0]
        trailing = minimal.word_button(words, action_cb, minimal.Tone.QUIET,
                                       spoken=action)
        trailing.add_css_class("xx-mm-flush-end")
        bar.append(trailing)
    return bar


def clear(box: Gtk.Widget) -> None:
    child = box.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        box.remove(child)
        child = following


def later(ms: int, callback) -> int:
    return GLib.timeout_add(ms, lambda: (callback(), False)[1])


def wake_for_position(player, steps: int, callback) -> int:
    """One wake just after the playing position next shows a change (a
    new second, or the next pixel of a bar ``steps`` wide), at most 30
    times a second; no wake (0) while it stands still."""
    wait = player.until_next_step(steps)
    if wait is None:
        return 0
    return later(max(int(wait * 1000) + 15, 33), callback)


def vbox(spacing: int = 0) -> Gtk.Box:
    return Gtk.Box.new(Gtk.Orientation.VERTICAL, spacing)


def hbox(spacing: int = 0) -> Gtk.Box:
    return Gtk.Box.new(Gtk.Orientation.HORIZONTAL, spacing)


def scroller(child: Gtk.Widget) -> Gtk.ScrolledWindow:
    """A vertical page scroller (never horizontal)."""
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_hexpand(True)
    scroll.set_vexpand(True)
    scroll.set_child(child)
    return scroll


PULL_REFRESH_GAP_S = 2.0


def pull_to_refresh(scroll: Gtk.ScrolledWindow, callback) -> None:
    """Pull down past the top of ``scroll`` to call ``callback`` (owner:
    "swipe down on xx-radio to refresh"). GTK reports the overscroll as
    edge-overshot at the TOP edge; repeats within a short gap (one long
    pull, a wheel held at the top) count once."""
    import time

    last = [0.0]

    def _overshot(_scroll, pos) -> None:
        if pos != Gtk.PositionType.TOP:
            return
        now = time.monotonic()
        if now - last[0] < PULL_REFRESH_GAP_S:
            return
        last[0] = now
        callback()

    scroll.connect("edge-overshot", _overshot)


def hscroll(child: Gtk.Widget) -> Gtk.ScrolledWindow:
    """A horizontal carousel that bleeds to the window edges."""
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
    scroll.add_css_class("xx-mu-hscroll")
    scroll.set_hexpand(True)
    scroll.set_propagate_natural_height(True)
    child.set_margin_start(20)
    child.set_margin_end(20)
    scroll.set_child(child)
    return scroll


# -- lines, busy squares, tags ---------------------------------------------------

def line(fraction: float, spoken: str = "") -> Gtk.ProgressBar:
    """2 px hairline progress: the played part in the theme foreground."""
    bar = Gtk.ProgressBar()
    bar.add_css_class("xx-mu-line")
    bar.set_fraction(min(max(fraction, 0.0), 1.0))
    bar.set_hexpand(True)
    if spoken:
        bar.update_property([Gtk.AccessibleProperty.LABEL], [spoken])
    return bar


def meter(fraction: float, spoken: str = "") -> Gtk.ProgressBar:
    """4 px storage meter."""
    bar = Gtk.ProgressBar()
    bar.add_css_class("xx-mu-meter")
    bar.set_fraction(min(max(fraction, 0.0), 1.0))
    bar.set_hexpand(True)
    if spoken:
        bar.update_property([Gtk.AccessibleProperty.LABEL], [spoken])
    return bar


class LoadingLine(Gtk.DrawingArea):
    """The 2 px sweeping line under a header instead of a spinner.

    Hidden when idle; while active, a 30 % segment travels left to right.
    """

    __gtype_name__ = "XXMusicLoadingLine"

    def __init__(self) -> None:
        super().__init__()
        self.set_content_height(2)
        self.set_hexpand(True)
        self.set_can_target(False)
        self._phase = 0.18
        self._tick = 0
        self._active = False
        self.set_opacity(0.0)
        self.set_draw_func(self._draw)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Loading"])

    def set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        self.set_opacity(1.0 if active else 0.0)
        if active and not self._tick:
            self._tick = self.add_tick_callback(self._on_tick)
        elif not active and self._tick:
            self.remove_tick_callback(self._tick)
            self._tick = 0

    @property
    def active(self) -> bool:
        return self._active

    def _on_tick(self, _widget, clock) -> bool:
        self._phase = (clock.get_frame_time() / 1_400_000.0) % 1.0
        self.queue_draw()
        return True

    def _draw(self, widget, cr, width, height) -> None:
        color = widget.get_color()
        cr.set_source_rgba(color.red, color.green, color.blue, 0.12)
        cr.rectangle(0, 0, width, height)
        cr.fill()
        span = width * 0.3
        x = -span + (width + span) * self._phase
        cr.set_source_rgba(color.red, color.green, color.blue, 1.0)
        cr.rectangle(max(x, 0), 0, min(span, width - max(x, 0)), height)
        cr.fill()


class BusySquare(Gtk.DrawingArea):
    """An 8 px square that blinks while its row is busy. It only
    repaints (never restyles or relayouts) on each blink."""

    __gtype_name__ = "XXMusicBusySquare"

    def __init__(self) -> None:
        super().__init__()
        self.set_content_width(8)
        self.set_content_height(8)
        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)
        self.set_can_target(False)
        self.lit = True
        self._timer = 0
        self.set_draw_func(self._draw)
        self.connect("map", self._map)
        self.connect("unmap", self._unmap)
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Working"])

    def _blink(self) -> bool:
        self.lit = not self.lit
        self.queue_draw()
        return True

    def _map(self, _w) -> None:
        if not self._timer:
            self._timer = GLib.timeout_add(520, self._blink)

    def _unmap(self, _w) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _draw(self, widget, cr, width, height) -> None:
        color = widget.get_color()
        cr.set_source_rgba(color.red, color.green, color.blue,
                           1.0 if self.lit else 0.2)
        cr.rectangle(0, 0, width, height)
        cr.fill()


def busy_square() -> Gtk.Widget:
    return BusySquare()


def tag(text: str, icon: str = "", solid: bool = False) -> Gtk.Box:
    box = hbox(3)
    box.add_css_class("xx-mu-tag")
    if solid:
        box.add_css_class("solid")
    box.set_valign(Gtk.Align.CENTER)
    box.set_halign(Gtk.Align.START)
    if icon:
        box.append(widgets.icon_image(icon, 10))
    text_label = Gtk.Label(label=text.upper())
    # Letter-spaced caps misfire an ellipsis on a word that fits
    # ("OFFLIN…"): only text past the budget may ellipsize.
    text_label.set_ellipsize(Pango.EllipsizeMode.END if len(text) > 14
                             else Pango.EllipsizeMode.NONE)
    text_label.set_max_width_chars(14)
    box.append(text_label)
    return box


def offline_tag() -> Gtk.Box:
    # "Offline" fits a 120 px tile whole ("ON THIS PH…" did not); the
    # spoken label keeps the full meaning.
    box = tag("Offline", "folder-download-symbolic")
    box.update_property([Gtk.AccessibleProperty.LABEL], ["On this phone"])
    return box


def explicit_tag() -> Gtk.Box:
    box = tag("E", solid=True)
    box.update_property([Gtk.AccessibleProperty.LABEL], ["Explicit"])
    return box


# -- art --------------------------------------------------------------------------

class Square(Gtk.Widget):
    """Square box: fixed ``px``, or as wide as allocated (height follows)."""

    __gtype_name__ = "XXMusicSquare"

    def __init__(self, child: Gtk.Widget, px: int = 0,
                 limit: int = 0) -> None:
        super().__init__()
        self._px = px
        self._limit = limit
        self._child = child
        child.set_parent(self)
        self.set_overflow(Gtk.Overflow.HIDDEN)

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        if self._px:
            return (self._px, self._px, -1, -1)
        if orientation == Gtk.Orientation.HORIZONTAL:
            return (96, self._limit or 320, -1, -1)
        side = for_size if for_size > 0 else (self._limit or 320)
        if self._limit:
            side = min(side, self._limit)
        return (side, side, -1, -1)

    def do_size_allocate(self, width, height, baseline):
        self._child.allocate(width, height, baseline, None)

    def do_snapshot(self, snapshot):
        self.snapshot_child(self._child, snapshot)

    def do_dispose(self):
        if self._child is not None:
            self._child.unparent()
            self._child = None


class Clamp(Gtk.Widget):
    """Exactly ``width`` wide; height follows the child at that width."""

    __gtype_name__ = "XXMusicClamp"

    def __init__(self, child: Gtk.Widget, width: int) -> None:
        super().__init__()
        self._width = width
        self._child = child
        child.set_parent(self)

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return (self._width, self._width, -1, -1)
        low, high, _b1, _b2 = self._child.measure(orientation, self._width)
        return (low, high, -1, -1)

    def do_size_allocate(self, width, height, baseline):
        self._child.allocate(width, height, baseline, None)

    def do_snapshot(self, snapshot):
        self.snapshot_child(self._child, snapshot)

    def do_dispose(self):
        if self._child is not None:
            self._child.unparent()
            self._child = None


def art(ui, url: str, title: str, px: int = 0, shape: str = "r4",
        limit: int = 0) -> Gtk.Widget:
    """Square art: an initials tile in the house glyph language (light
    initials over a short 2 px rule, hairline frame) until the real art
    arrives, if any."""
    holder = Gtk.Overlay()
    holder.add_css_class("xx-mu-art")
    holder.add_css_class(shape)
    size = px or limit or 240
    tier = "s" if size <= 64 else "m" if size <= 140 else \
        "l" if size <= 220 else "xl"
    glyph = vbox(max(size // 14, 3))
    glyph.set_halign(Gtk.Align.CENTER)
    glyph.set_valign(Gtk.Align.CENTER)
    initials = Gtk.Label(label=fmt.initials(title))
    initials.add_css_class("xx-mu-initials")
    initials.add_css_class(tier)
    initials.add_css_class("xx-display-face")
    glyph.append(initials)
    if size > 40:
        rule = Gtk.Box()
        rule.add_css_class("xx-mu-rule")
        rule.set_size_request(
            {"s": 10, "m": 18, "l": 30, "xl": 40}[tier], 2)
        rule.set_halign(Gtk.Align.CENTER)
        glyph.append(rule)
    holder.set_child(glyph)
    square = Square(holder, px, limit)
    square.update_property([Gtk.AccessibleProperty.LABEL],
                           [f"Art for {title}"])
    if not url or ui is None or ui.art is None or minimal.active():
        return square
    picture = Gtk.Picture()
    picture.set_content_fit(Gtk.ContentFit.COVER)
    picture.set_can_shrink(True)
    picture.set_visible(False)
    holder.add_overlay(picture)

    def _deliver(texture) -> None:
        picture.set_paintable(texture)
        picture.set_visible(True)
        glyph.set_visible(False)

    ui.art.request(url, max(size, 64) * 2, _deliver)
    return square


def item_art(ui, item: Item, px: int) -> Gtk.Widget:
    shape = "round" if item.kind == ItemKind.ARTIST else (
        "r4" if px <= 64 else "r8")
    return art(ui, item.art_url, item.title, px, shape)


# -- long press -----------------------------------------------------------------

def on_long_press(widget: Gtk.Widget, callback) -> None:
    """Long-press (and right click) opens ``callback``; the click that
    ends a long press does not also activate the widget."""
    if callback is None:
        return
    press = Gtk.GestureLongPress.new()
    press.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

    def _pressed(gesture, _x, _y) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        callback()

    press.connect("pressed", _pressed)
    widget.add_controller(press)
    right = Gtk.GestureClick.new()
    right.set_button(3)
    right.connect("pressed", lambda *_a: callback())
    widget.add_controller(right)


# -- rows and tiles ---------------------------------------------------------------

def beside(row: Gtk.Button, line: Gtk.Box, trailing: Gtk.Widget
           ) -> Gtk.Overlay:
    """``row`` with its trailing target (More, Retry, Cancel) laid over
    its end, never inside it. A GTK 4 button takes its click in the
    capture phase, so a button nested in a row button never fires: the
    row claims every tap that ends on it (touch and mouse alike) and
    plays the song instead. ``line`` (the row's content) keeps room the
    width of the target, so the row looks the same."""
    room = Gtk.Box()
    widths = Gtk.SizeGroup.new(Gtk.SizeGroupMode.HORIZONTAL)
    widths.add_widget(room)
    widths.add_widget(trailing)
    line.append(room)
    trailing.set_halign(Gtk.Align.END)
    trailing.set_valign(Gtk.Align.CENTER)
    holder = Gtk.Overlay()
    holder.set_child(row)
    holder.add_overlay(trailing)
    return holder


def track_row(ui, track: Track, on_play, on_menu=None, *,
              number: int | None = None, now: bool = False,
              extra: str = "", offline: bool = False, last: bool = False,
              show_art: bool = True, meta: str | None = None,
              lead: str = "") -> Gtk.Widget:
    """A song row: art (or its number), title, artists, duration, a
    labelled More button; long-press opens the same menu. Minimal Mode:
    title and artists in words, ``▸`` on the playing one, no art or
    icons (long-press opens the menu)."""
    if minimal.active():
        title_text = track.title if number is None or now \
            else f"{number}. {track.title}"
        sub_text = fmt.joined(lead, fmt.track_line(track, extra))
        if offline:
            sub_text = f"{sub_text}  {minimal.ON_PHONE}".strip()
        if track.explicit:
            sub_text = fmt.joined("E", sub_text)
        trailing = meta if meta is not None else fmt.duration(
            track.duration_s)
        return minimal.list_row(
            title_text, sub_text, on_play, on_menu,
            now=now, meta=trailing,
            spoken=f"{track.title}, {track.artist_line}".strip(", "))
    row = Gtk.Button()
    row.add_css_class("xx-mu-row")
    if last:
        row.add_css_class("last")
    if now:
        row.add_css_class("now")
    box = hbox(14)
    if number is not None:
        slot = Gtk.CenterBox()
        slot.set_size_request(24, -1)
        if now and ui is not None and ui.player.playing:
            slot.set_center_widget(busy_square())
        else:
            num = label(str(number), "xx-mu-rownum xx-mu-num", xalign=0.5,
                        chars=3)
            num.set_ellipsize(Pango.EllipsizeMode.NONE)
            if now:
                num.add_css_class("xx-mu-strong")
            slot.set_center_widget(num)
        box.append(slot)
    elif show_art:
        holder = Gtk.Overlay()
        holder.set_child(art(ui, track.art_url, track.title, ROW_ART_PX))
        box.append(holder)
    text = vbox(2)
    text.set_hexpand(True)
    text.set_valign(Gtk.Align.CENTER)
    title = label(track.title, "xx-title", chars=18)
    text.append(title)
    sub_line = hbox(6)
    if track.explicit:
        sub_line.append(explicit_tag())
    if offline:
        sub_line.append(widgets.icon_image("folder-download-symbolic", 12))
    sub = label(fmt.joined(lead, fmt.track_line(track, extra)),
                "xx-secondary", chars=22)
    sub.set_hexpand(True)
    sub_line.append(sub)
    text.append(sub_line)
    box.append(text)
    trailing = meta if meta is not None else fmt.duration(track.duration_s)
    if trailing and not css.large_text():
        when = label(trailing, "xx-time xx-mu-num", xalign=1.0, chars=7)
        when.set_ellipsize(Pango.EllipsizeMode.NONE)
        when.set_valign(Gtk.Align.CENTER)
        box.append(when)
    row.set_child(box)
    name(row, f"{track.title}, {track.artist_line}".strip(", "))
    row.connect("clicked", lambda _b: on_play())
    on_long_press(row, on_menu)
    if on_menu is None:
        return row
    return beside(row, box, icon_button(
        "view-more-symbolic", f"More options for {track.title}", on_menu))


def item_row(ui, item: Item, on_open, on_menu=None, *, last: bool = False,
             line_text: str | None = None) -> Gtk.Widget:
    """An album, artist or playlist row (artists get a round avatar)."""
    if minimal.active():
        sub = line_text if line_text is not None else fmt.item_line(item)
        return minimal.list_row(
            minimal.title_artist(item.title, opens=True), sub, on_open,
            on_menu, spoken=f"{item.title}, {sub}".strip(", "))
    row = Gtk.Button()
    row.add_css_class("xx-mu-row")
    if last:
        row.add_css_class("last")
    box = hbox(14)
    box.append(item_art(ui, item, ROW_ART_PX))
    text = vbox(2)
    text.set_hexpand(True)
    text.set_valign(Gtk.Align.CENTER)
    text.append(label(item.title, "xx-title", chars=20))
    sub = line_text if line_text is not None else fmt.item_line(item)
    if sub:
        text.append(label(sub, "xx-secondary", chars=24))
    box.append(text)
    if on_menu is None:
        box.append(widgets.chevron())
    row.set_child(box)
    name(row, f"{item.title}, {sub}".strip(", "))
    row.connect("clicked", lambda _b: on_open())
    on_long_press(row, on_menu)
    if on_menu is None:
        return row
    return beside(row, box, icon_button(
        "view-more-symbolic", f"More options for {item.title}", on_menu))


def tile(ui, item: Item, on_open, on_menu=None, px: int = TILE_PX,
         offline: bool = False, sub: str | None = None) -> Gtk.Button:
    """Shelf tile: square art, two-line title, one muted line."""
    button = Gtk.Button()
    button.add_css_class("xx-mu-tile")
    box = vbox(0)
    box.append(item_art(ui, item, px))
    chars = max(px // 9, 10)
    title = label(item.title, "xx-mu-tile-title", lines=2, chars=chars)
    title.set_margin_top(8)
    title.set_yalign(0.0)
    if item.kind == ItemKind.ARTIST:
        title.set_xalign(0.5)
        title.set_justify(Gtk.Justification.CENTER)
    box.append(title)
    line_text = sub if sub is not None else (
        item.subtitle if item.subtitle.lower() != "artist" else "")
    if line_text:
        subtitle = label(line_text, "xx-mu-tile-sub", chars=chars)
        subtitle.set_margin_top(2)
        if item.kind == ItemKind.ARTIST:
            subtitle.set_xalign(0.5)
        box.append(subtitle)
    if offline:
        badge = offline_tag()
        badge.set_margin_top(6)
        box.append(badge)
    button.set_child(Clamp(box, px))
    name(button, item.title)
    button.connect("clicked", lambda _b: on_open())
    on_long_press(button, on_menu)
    return button


def section(text: str, action: str = "", callback=None,
            first: bool = False) -> Gtk.Box:
    """A caps section label with an optional trailing text action."""
    if minimal.active():
        head = minimal.section(text, action, callback)
        if first:
            head.add_css_class("first")
        return head
    head = hbox(0)
    head.add_css_class("xx-mu-section")
    head.add_css_class("xx-mu-section-head")
    if first:
        head.add_css_class("first")
    caption = caps(text, chars=26)
    caption.set_hexpand(True)
    caption.set_valign(Gtk.Align.CENTER)
    head.append(caption)
    if action and callback is not None:
        more = Gtk.Button.new_with_label(action.upper())
        more.add_css_class("xx-mu-act")
        more.set_valign(Gtk.Align.CENTER)
        fit(more, 10)
        more.connect("clicked", lambda _b: callback())
        head.append(more)
    return head


def carousel(children: list[Gtk.Widget], spacing: int = 12
             ) -> Gtk.ScrolledWindow:
    row = hbox(spacing)
    row.set_valign(Gtk.Align.START)
    for child in children:
        child.set_valign(Gtk.Align.START)
        row.append(child)
    return hscroll(row)


def chips(items: list, scroll: bool = True) -> Gtk.Widget:
    """A row of caps chips. ``items``: (label, selected, callback).
    Minimal Mode: a scrolling row of words, the selected one Strong."""
    if minimal.active():
        row = hbox(0)
        for text, selected, callback in items:
            row.append(minimal.word_button(
                text.lower(), callback, minimal.toggled(selected)))
        scroll = hscroll(row)
        row.set_margin_start(12)
        row.set_margin_end(12)
        scroll.add_css_class("xx-mm-words")
        return scroll
    bar = widgets.chip_bar(items)
    bar.add_css_class("xx-mu-chips")
    bar.add_css_class("xx-mu-hscroll")
    bar.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
    inner = bar.get_child()
    if isinstance(inner, Gtk.Viewport):
        inner = inner.get_child()
    inner.set_margin_start(20)
    inner.set_margin_end(20)
    child = inner.get_first_child()
    while child is not None:
        child.set_size_request(-1, 36)
        child.get_child().set_ellipsize(Pango.EllipsizeMode.NONE)
        child = child.get_next_sibling()
    return bar


def flow_chips(items: list, per_line: int = 3) -> Gtk.FlowBox:
    """Wrapping chips (recent searches, sleep minutes)."""
    if minimal.active():
        words = minimal.flow([minimal.word_button(
            text, callback, minimal.toggled(selected) if selected
            else minimal.Tone.PLAIN, chars=24)
            for text, selected, callback in items], spacing=0)
        return words
    flow = Gtk.FlowBox()
    flow.set_selection_mode(Gtk.SelectionMode.NONE)
    flow.set_column_spacing(8)
    flow.set_row_spacing(8)
    flow.set_max_children_per_line(per_line)
    flow.set_min_children_per_line(1)
    flow.set_homogeneous(False)
    for text, selected, callback in items:
        chip = Gtk.Button.new_with_label(text)
        chip.add_css_class("xx-chip")
        chip.set_size_request(-1, 40)
        chip.set_halign(Gtk.Align.START)
        chip.get_child().set_max_width_chars(18)
        chip.get_child().set_ellipsize(
            Pango.EllipsizeMode.END if len(text) > 18
            else Pango.EllipsizeMode.NONE)
        if selected:
            chip.add_css_class("selected")
        chip.connect("clicked", lambda _b, cb=callback: cb())
        flow.append(chip)
        chip.get_parent().set_focusable(False)
    return flow


def grid_chips(items: list, per_line: int = 4) -> Gtk.Grid:
    """Equal-width chips in a fixed grid (sleep minutes). Minimal Mode:
    brackets, the selected one Strong."""
    if minimal.active():
        brackets = minimal.flow([minimal.bracket_button(
            text.lower(), callback, minimal.toggled(selected))
            for text, selected, callback in items], spacing=8)
        return brackets
    grid = Gtk.Grid()
    grid.set_column_spacing(8)
    grid.set_row_spacing(8)
    grid.set_column_homogeneous(True)
    for index, (text, selected, callback) in enumerate(items):
        chip = Gtk.Button.new_with_label(text.upper())
        chip.add_css_class("xx-chip")
        chip.set_size_request(-1, 48)
        chip.get_child().set_ellipsize(Pango.EllipsizeMode.END)
        if selected:
            chip.add_css_class("selected")
        chip.connect("clicked", lambda _b, cb=callback: cb())
        grid.attach(chip, index % per_line, index // per_line, 1, 1)
    return grid


# -- sheets ------------------------------------------------------------------------

def sheet(title: str, subtitle: str = "") -> tuple:
    """An Adw.Dialog (a bottom sheet on the phone) with a padded box,
    the title and a labelled close target. Returns (dialog, box)."""
    gi.require_version("Adw", "1")
    from gi.repository import Adw

    dialog = Adw.Dialog()
    dialog.add_css_class("xx-mu-dialog")
    dialog.set_title(title)
    dialog.set_content_width(360)
    box = vbox(6)
    box.add_css_class("xx-mu-sheet")
    head = hbox(0)
    texts = vbox(0)
    texts.set_hexpand(True)
    texts.set_valign(Gtk.Align.CENTER)
    texts.append(label(title, "xx-dialog-title", chars=20))
    if subtitle:
        texts.append(label(subtitle, "xx-secondary", chars=24))
    head.append(texts)
    head.append(_close(dialog))
    box.append(head)
    dialog.set_child(box)
    return dialog, box


def _close(dialog) -> Gtk.Widget:
    """A sheet's close target: the word in Minimal Mode, else the icon."""
    if minimal.active():
        word = minimal.word_button("close", dialog.close, spoken="Close")
        word.add_css_class("xx-mm-flush-end")
        return word
    return icon_button("window-close-symbolic", "Close", dialog.close)


#: Widest window (logical px) that still presents sheets as a phone does.
PHONE_MAX_WIDTH = 600


def sheet_width(parent: Gtk.Widget | None) -> int:
    """The bottom-sheet width for ``parent``'s window: the whole window
    width at phone widths (Android's ModalBottomSheet spans the screen),
    0 where the window is wider and the sheet may float."""
    root = parent.get_root() if parent is not None else None
    width = root.get_width() if root is not None else 0
    if width <= 0 and isinstance(root, Gtk.Window):
        width = root.get_default_size()[0]
    return width if 0 < width <= PHONE_MAX_WIDTH else 0


def dock(dialog, parent: Gtk.Widget) -> None:
    """At phone widths, present ``dialog`` as a full-width bottom sheet
    over a dimmed app; libadwaita's AUTO mode floats a 360 px dialog as
    a card on the 450 px FLX1."""
    width = sheet_width(parent)
    if not width:
        return
    gi.require_version("Adw", "1")
    from gi.repository import Adw

    mode = getattr(Adw, "DialogPresentationMode", None)
    if mode is not None and hasattr(dialog, "set_presentation_mode"):
        dialog.set_presentation_mode(mode.BOTTOM_SHEET)
    dialog.set_content_width(width)


def present(dialog, parent: Gtk.Widget):
    """Every sheet opens through here (one seam the renders replace)."""
    dock(dialog, parent)
    dialog.present(parent)
    return dialog


def menu_row(icon: str, text: str, callback,
             proper: bool = False) -> Gtk.Button:
    """A 48 px sheet row. ``proper`` text is a name (a speaker's): Minimal
    Mode keeps its capital ("Living Room", not "living Room")."""
    if minimal.active():
        return minimal.text_row(
            text if proper else minimal.sentence_word(text), callback)
    button = Gtk.Button()
    button.add_css_class("xx-mu-menu")
    row = hbox(16)
    row.append(widgets.icon_image(icon, 20))
    text_label = label(text, "xx-body", chars=32)
    # The row's whole width: "This phone · playing here" never loses
    # its last word to a natural-width allocation.
    text_label.set_hexpand(True)
    row.append(text_label)
    button.set_child(row)
    button.connect("clicked", lambda _b: callback())
    return button


def menu_sheet(ui, title: str, subtitle: str, art_widget: Gtk.Widget,
               entries: list) -> tuple:
    """A long-press menu: the thing's art and name, then 48 px rows.
    ``entries``: (icon, text, callback) or None for a hairline. Minimal
    Mode: no art and no icons, the entries as words."""
    gi.require_version("Adw", "1")
    from gi.repository import Adw

    dialog = Adw.Dialog()
    dialog.add_css_class("xx-mu-dialog")
    dialog.set_title(title)
    dialog.set_content_width(360)
    box = vbox(0)
    box.add_css_class("xx-mu-sheet")
    head = hbox(14)
    head.set_margin_bottom(10)
    if not minimal.active():
        art_widget.set_valign(Gtk.Align.CENTER)
        head.append(art_widget)
    texts = vbox(2)
    texts.set_hexpand(True)
    texts.set_valign(Gtk.Align.CENTER)
    texts.append(label(title, "xx-title", chars=20))
    if subtitle:
        texts.append(label(subtitle, "xx-secondary", chars=24))
    head.append(texts)
    head.append(_close(dialog))
    box.append(head)
    rule = Gtk.Separator.new(Gtk.Orientation.HORIZONTAL)
    rule.set_margin_bottom(4)
    box.append(rule)
    for entry in entries:
        if entry is None:
            sep = Gtk.Separator.new(Gtk.Orientation.HORIZONTAL)
            sep.set_margin_top(4)
            sep.set_margin_bottom(4)
            box.append(sep)
            continue
        icon, text, callback = entry

        def _go(cb=callback) -> None:
            dialog.close()
            cb()

        box.append(menu_row(icon, text, _go))
    dialog.set_child(box)
    return dialog, box


def confirm(parent: Gtk.Widget, heading: str, body: str, responses: list,
            on_response):
    """A house-style confirm sheet. ``responses``: (id, label, kind),
    kind 'outline' | 'primary' | 'danger'; the first one is the dismiss
    answer (also on close)."""
    dialog, box = sheet(heading)
    if body:
        box.append(para(body, "xx-secondary"))
    row = Gtk.Box.new(Gtk.Orientation.VERTICAL if css.large_text()
                      else Gtk.Orientation.HORIZONTAL, 10)
    row.set_homogeneous(True)
    row.set_margin_top(14)
    answered: list = []

    def _answer(response_id: str) -> None:
        if answered:
            return
        answered.append(response_id)
        dialog.close()
        on_response(response_id)

    if minimal.active():
        row.set_homogeneous(False)
    for response_id, text, kind in responses:
        button = minimal.button(text, lambda r=response_id: _answer(r),
                                kind)
        fit(button, 14)
        row.append(button)
    box.append(row)
    dialog.connect("closed", lambda _d: _answer(responses[0][0]))
    return present(dialog, parent)


def empty_state(text: str, action: str = "", callback=None) -> Gtk.Widget:
    """The one-sentence empty state; in Minimal Mode, a Quiet line and
    a bracketed action."""
    if not minimal.active():
        return widgets.empty_state(text, action or None, callback)
    box = vbox(4)
    box.append(minimal.text(text, minimal.Tone.QUIET, 15, wrap=True,
                            chars=28))
    if action:
        act = minimal.bracket_button(action.lower(), callback,
                                     minimal.Tone.STRONG)
        act.set_halign(Gtk.Align.START)
        box.append(act)
    return box


def copied(widget: Gtk.Widget, match: str = "", ms: int = 2000) -> None:
    """The in-place receipt for a copy: the first label under ``widget``
    (or the one reading ``match``) says "Copied" for two seconds, then
    its own words come back. No toast for a direct action."""
    found = None
    stack = [widget]
    while stack and found is None:
        node = stack.pop(0)
        if isinstance(node, Gtk.Label) and (not match
                                             or node.get_text() == match):
            found = node
            break
        child = node.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    if found is None:
        return
    original = getattr(found, "xx_copied_from", None) or found.get_text()
    found.xx_copied_from = original
    found.set_text("Copied")

    def restore() -> bool:
        found.set_text(original)
        found.xx_copied_from = None
        return False

    GLib.timeout_add(ms, restore)


def retry_button(callback) -> Gtk.Widget:
    """The one action under an error line: Retry (``[ retry ]`` in
    Minimal Mode), start-aligned like the line above it."""
    if minimal.active():
        button = minimal.bracket_button("retry", callback,
                                        minimal.Tone.STRONG)
    else:
        button = widgets.outline_button("Retry", callback)
    button.set_halign(Gtk.Align.START)
    return button


def toast(ui, text: str, action_label: str = "",
          action=None) -> None:
    """A one-line note at the bottom of the window (the shell owns it)."""
    if ui is not None and ui.shell is not None:
        ui.shell.toast(text, action_label, action)
