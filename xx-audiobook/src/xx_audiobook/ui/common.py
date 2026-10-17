"""Shared widgets for the audiobook pages, thin over xx_ui.widgets.

Each one mirrors a Compose piece of the Android client: ScreenTitle,
TopBar, SectionTitle, MutedLine, CoverArt (square, a book glyph until the
art arrives), LibraryRow / BookTile, DownloadedMark, HairlineProgress,
FilterChip (brandChipColors), SkipGlyph, SeekBar (HairlineSlider), the
sheets and ConfirmDialog. Every icon-only control carries a label and a
48 px target; every label wraps or ellipsizes so 320 px still fits.
"""

from __future__ import annotations

import math

from gi.repository import GLib, GObject, Gtk, Pango

from xx_audiobook import fmt
from xx_ui import widgets

# Display roles take the brand display face (Space Mono over the house
# mono; a family the user picked stays in charge).
DISPLAY_ROLES = frozenset({"xx-ab-heading", "xx-ab-np-title",
                           "xx-ab-screen", "xx-ab-year"})

BOOK_ICON = "xx-ab-book-symbolic"


def label(text: str, cls: str = "xx-body", lines: int = 1,
          xalign: float = 0.0, chars: int = 24) -> Gtk.Label:
    """A label that never widens the window: ellipsized, or wrapped to
    ``lines`` then ellipsized."""
    widget = Gtk.Label(label=text)
    for name in cls.split():
        widget.add_css_class(name)
        if name in DISPLAY_ROLES:
            widget.add_css_class("xx-display-face")
    widget.set_xalign(xalign)
    widget.set_max_width_chars(chars)
    widget.set_ellipsize(Pango.EllipsizeMode.END)
    if lines > 1:
        widget.set_wrap(True)
        widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        widget.set_lines(lines)
    return widget


def para(text: str, cls: str = "xx-body") -> Gtk.Label:
    """Free text: wraps fully, never ellipsized."""
    widget = Gtk.Label(label=text)
    for name in cls.split():
        widget.add_css_class(name)
    widget.set_xalign(0.0)
    widget.set_wrap(True)
    widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    widget.set_max_width_chars(32)
    return widget


def muted(text: str) -> Gtk.Label:
    """MutedLine: the one line for empty, error and offline states."""
    line = para(text, "xx-ab-b13 xx-ab-muted")
    line.set_margin_top(12)
    line.set_margin_bottom(12)
    return line


def caps(text: str) -> Gtk.Label:
    """A small caps label (NOW PLAYING, tile captions)."""
    return label(text.upper(), "xx-ab-label", chars=28)


def name(widget: Gtk.Widget, text: str) -> Gtk.Widget:
    """Accessible label + tooltip for icon-only controls."""
    widget.set_tooltip_text(text)
    widget.update_property([Gtk.AccessibleProperty.LABEL], [text])
    return widget


def fit(button: Gtk.Button, chars: int) -> Gtk.Button:
    """A text button whose label ellipsizes instead of widening the
    window (large text sizes); its allocation always exceeds the natural
    width at 1x, so the ellipsis never misfires there."""
    child = button.get_child()
    child.set_max_width_chars(chars)
    child.set_ellipsize(Pango.EllipsizeMode.END)
    return button


def icon_button(icon: str, text: str, callback, px: int = 24) -> Gtk.Button:
    button = Gtk.Button()
    button.add_css_class("xx-btn")
    button.add_css_class("xx-btn-icon")
    button.set_child(widgets.icon_image(icon, px))
    button.connect("clicked", lambda _b: callback())
    button.set_size_request(48, 48)
    return name(button, text)


def clear(box: Gtk.Box) -> None:
    child = box.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        box.remove(child)
        child = following


def spacer(px: int) -> Gtk.Box:
    box = Gtk.Box()
    box.set_size_request(-1, px)
    return box


# -- chrome ----------------------------------------------------------------


def page_top(title: str, actions: list = ()) -> Gtk.Box:
    """ScreenTitle in a row with labelled 48 px icon actions,
    (icon, spoken, callback): a tab root's header."""
    top = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    top.add_css_class("xx-ab-top")
    heading = label(title, "xx-ab-screen", chars=12)
    heading.set_hexpand(True)
    heading.set_valign(Gtk.Align.CENTER)
    heading.update_property([Gtk.AccessibleProperty.LABEL], [title])
    top.append(heading)
    for icon, spoken, callback in actions:
        button = icon_button(icon, spoken, callback)
        button.set_valign(Gtk.Align.CENTER)
        top.append(button)
    return top


def header(back, title: str) -> Gtk.Box:
    """TopBar: a pushed screen's 56 px bar, Back and the title on one
    line, a hairline along the bottom. ``bar.title`` can be retitled."""
    bar = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    bar.add_css_class("xx-ab-bar")
    back_button = icon_button("go-previous-symbolic", "Back", back)
    back_button.set_valign(Gtk.Align.CENTER)
    bar.append(back_button)
    heading = label(title, "xx-ab-t16", chars=22)
    heading.set_hexpand(True)
    heading.set_valign(Gtk.Align.CENTER)
    bar.append(heading)
    bar.title = heading
    return bar


def section(text: str, first: bool = False) -> Gtk.Label:
    """SectionTitle: title case, 14 px, muted, a heading."""
    head = label(text, "xx-ab-section", lines=2, chars=30)
    if first:
        head.add_css_class("first")
    return head


def caps_section(text: str) -> Gtk.Label:
    """The uppercase list label of Series / Collections (labelMedium)."""
    return label(text.upper(), "xx-ab-caps", chars=30)


def section_with(text: str, icon: str, spoken: str, callback,
                 first: bool = False) -> Gtk.Box:
    """A section title with one labelled 48 px action at its end."""
    row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    head = section(text, first)
    head.set_hexpand(True)
    head.set_valign(Gtk.Align.CENTER)
    row.append(head)
    action = icon_button(icon, spoken, callback)
    action.set_valign(Gtk.Align.CENTER)
    row.append(action)
    return row


def chip(text: str, selected: bool, callback,
         spoken: str | None = None) -> Gtk.Button:
    """FilterChip: hairline square idle, inverted when selected; the
    label stays as written (no caps)."""
    button = Gtk.Button.new_with_label(text)
    button.add_css_class("xx-ab-chip")
    if selected:
        button.add_css_class("selected")
    button.get_child().set_ellipsize(Pango.EllipsizeMode.END)
    button.get_child().set_max_width_chars(18)
    button.connect("clicked", lambda _b: callback())
    if spoken is not None:
        button.update_property([Gtk.AccessibleProperty.LABEL],
                               [spoken + (", selected" if selected else "")])
    return button


def chip_strip(items: list) -> Gtk.ScrolledWindow:
    """A sideways-scrolling row of chips: (text, selected, callback)."""
    box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 6)
    for text, selected, callback in items:
        button = chip(text, selected, callback)
        # The strip scrolls sideways: chips keep their whole label.
        button.get_child().set_ellipsize(Pango.EllipsizeMode.NONE)
        box.append(button)
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
    scroller.set_child(box)
    return scroller


def chip_flow(items: list) -> Gtk.FlowBox:
    """FlowRow of chips that wraps: (text, selected, callback[, spoken])."""
    flow = Gtk.FlowBox()
    flow.set_selection_mode(Gtk.SelectionMode.NONE)
    flow.set_homogeneous(False)
    flow.set_column_spacing(6)
    flow.set_row_spacing(6)
    flow.set_min_children_per_line(1)
    flow.set_max_children_per_line(12)
    flow.set_margin_top(6)
    flow.set_margin_bottom(6)
    fill_flow(flow, items)
    return flow


def fill_flow(flow: Gtk.FlowBox, items: list) -> None:
    child = flow.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        flow.remove(child)
        child = following
    for item in items:
        text, selected, callback = item[:3]
        spoken = item[3] if len(item) > 3 else None
        button = chip(text, selected, callback, spoken)
        button.set_valign(Gtk.Align.CENTER)
        flow.append(button)
        button.get_parent().set_focusable(False)


def flat(child: Gtk.Widget, spoken: str, callback) -> Gtk.Button:
    """A chromeless tappable area (48 px min) around ``child``."""
    button = Gtk.Button()
    button.add_css_class("xx-ab-flat")
    button.set_child(child)
    button.update_property([Gtk.AccessibleProperty.LABEL], [spoken])
    button.connect("clicked", lambda _b: callback())
    return button


def line(fraction: float) -> Gtk.ProgressBar:
    """HairlineProgress: 3 px, the played part in the foreground over a
    25% well; no caps, no gap, no end dot."""
    bar = Gtk.ProgressBar()
    bar.add_css_class("xx-ab-line")
    bar.set_fraction(min(max(fraction, 0.0), 1.0))
    bar.set_hexpand(True)
    bar.update_property([Gtk.AccessibleProperty.LABEL],
                        [f"{fmt.percent(fraction)}% listened"])
    return bar


def offline_tag() -> Gtk.Box:
    """DownloadedMark: a hairline tag, the done glyph and 'Offline'."""
    tag = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 2)
    tag.add_css_class("xx-ab-tag")
    tag.set_valign(Gtk.Align.CENTER)
    tag.set_halign(Gtk.Align.START)
    tag.append(widgets.icon_image("xx-ab-download-symbolic", 12))
    text = Gtk.Label(label="Offline")
    text.set_ellipsize(Pango.EllipsizeMode.END)
    tag.append(text)
    tag.update_property([Gtk.AccessibleProperty.LABEL], ["Downloaded"])
    return tag


def rating_line(average, count: int) -> str:
    """RatingBadge: '★ 4.0 (1)', or nothing when no one rated it."""
    text = fmt.rating_text(average, count)
    return f"{text} ({count})" if text else ""


def rating_spoken(average, count: int) -> str:
    plural = "" if count == 1 else "s"
    return f"Rated {average:.1f} out of 5 from {count} review{plural}"


# -- covers -------------------------------------------------------------------


class _Square(Gtk.Widget):
    """Square box: fixed ``px``, or as wide as allocated (height follows)."""

    __gtype_name__ = "XXAudiobookSquare"

    def __init__(self, child: Gtk.Widget, px: int = 0) -> None:
        super().__init__()
        self._px = px
        self._child = child
        child.set_parent(self)
        self.set_overflow(Gtk.Overflow.HIDDEN)

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        if self._px:
            return (self._px, self._px, -1, -1)
        if orientation == Gtk.Orientation.HORIZONTAL:
            return (64, 160, -1, -1)
        side = for_size if for_size > 0 else 160
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

    __gtype_name__ = "XXAudiobookClamp"

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


def cover(ctx, item_id: str, title: str, px: int = 0,
          has_cover: bool = True, size: str = "thumb") -> Gtk.Widget:
    """CoverArt: a square on the tile color with a hairline; the book
    glyph until the art arrives (or for good when there is none)."""
    holder = Gtk.Overlay()
    holder.add_css_class("xx-ab-cover")
    glyph_px = 20 if 0 < px <= 48 else (40 if px >= 180 else 24)
    mark = widgets.icon_image(BOOK_ICON, glyph_px)
    mark.set_halign(Gtk.Align.CENTER)
    mark.set_valign(Gtk.Align.CENTER)
    holder.set_child(mark)
    square = _Square(holder, px)
    square.update_property([Gtk.AccessibleProperty.LABEL],
                           [f"Cover of {title}"])
    if not has_cover or not item_id:
        return square
    picture = Gtk.Picture()
    picture.set_content_fit(Gtk.ContentFit.COVER)
    picture.set_can_shrink(True)
    picture.set_visible(False)
    holder.add_overlay(picture)

    def _deliver(texture) -> None:
        picture.set_paintable(texture)
        picture.set_visible(True)
        mark.set_visible(False)

    ctx.covers.request(item_id, size, _deliver)
    return square


# -- book rows and tiles ----------------------------------------------------------


def _marks(item, downloaded: bool, with_flags: bool = True) -> Gtk.Box | None:
    """★ / Finished / Missing / Offline, wrapping like a FlowRow."""
    parts = []
    rating = rating_line(item.rating_average, item.rating_count)
    if rating:
        mark = label(rating, "xx-ab-meta xx-ab-num", chars=12)
        mark.update_property([Gtk.AccessibleProperty.LABEL], [
            rating_spoken(item.rating_average, item.rating_count)])
        parts.append(mark)
    if with_flags and item.is_finished:
        parts.append(label("Finished", "xx-ab-meta", chars=10))
    if with_flags and item.missing:
        parts.append(label("Missing on server", "xx-ab-l12 xx-ab-error",
                           chars=18))
    if downloaded:
        parts.append(offline_tag())
    if not parts:
        return None
    flow = Gtk.FlowBox()
    flow.set_selection_mode(Gtk.SelectionMode.NONE)
    flow.set_column_spacing(8)
    flow.set_row_spacing(2)
    flow.set_max_children_per_line(4)
    flow.set_can_target(False)
    for part in parts:
        part.set_valign(Gtk.Align.CENTER)
        flow.append(part)
        part.get_parent().set_focusable(False)
    return flow


def book_row(ctx, item, on_open, prefix: str | None = None,
             last: bool = False) -> Gtk.Button:
    """LibraryRow: 56 px cover, title, author, ★ / finished / offline,
    then the 3 px progress line."""
    row = Gtk.Button()
    row.add_css_class("xx-ab-row")
    if last:
        row.add_css_class("last")
    box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
    art = cover(ctx, item.id, item.title, 56, bool(item.thumb_url))
    art.set_valign(Gtk.Align.CENTER)
    box.append(art)
    text = Gtk.Box.new(Gtk.Orientation.VERTICAL, 1)
    text.set_hexpand(True)
    text.set_valign(Gtk.Align.CENTER)
    if prefix:
        text.append(label(prefix, "xx-ab-l10 xx-ab-muted", chars=24))
    text.append(label(item.title, "xx-ab-t16", lines=2, chars=20))
    if item.authors:
        text.append(label(item.authors, "xx-ab-b13 xx-ab-muted", chars=24))
    marks = _marks(item, item.id in ctx.downloaded)
    if marks is not None:
        marks.set_margin_top(2)
        text.append(marks)
    if 0 < item.fraction < 1 and not item.is_finished:
        bar = line(item.fraction)
        bar.set_margin_top(6)
        text.append(bar)
    box.append(text)
    row.set_child(box)
    row.update_property([Gtk.AccessibleProperty.LABEL],
                        [f"{item.title}, {item.authors}".strip(", ")])
    row.connect("clicked", lambda _b: on_open(item))
    return row


def book_tile(ctx, item, on_open, px: int = 0) -> Gtk.Button:
    """BookTile: cover, progress (only while in progress), two-line
    title, author, ★ and the Offline mark."""
    tile = Gtk.Button()
    tile.add_css_class("xx-ab-tile")
    box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    box.append(cover(ctx, item.id, item.title, px, bool(item.thumb_url)))
    if 0 < item.fraction < 1 and not item.is_finished:
        bar = line(item.fraction)
        bar.set_margin_top(4)
        box.append(bar)
    chars = max(px // 8, 10) if px else 16
    title = label(item.title, "xx-ab-l13", lines=2, chars=chars)
    title.set_margin_top(4)
    box.append(title)
    if item.authors:
        box.append(label(item.authors, "xx-ab-l10 xx-ab-muted", chars=chars))
    marks = _marks(item, item.id in ctx.downloaded, with_flags=False)
    if marks is not None:
        marks.set_margin_top(2)
        box.append(marks)
    tile.set_child(Clamp(box, px) if px else box)
    tile.update_property([Gtk.AccessibleProperty.LABEL], [item.title])
    tile.connect("clicked", lambda _b: on_open(item))
    return tile


# -- skip glyph ----------------------------------------------------------------


def skip_glyph(seconds: int, forward: bool, px: int = 32) -> Gtk.DrawingArea:
    """SkipGlyph: a circular arrow with the skip seconds inside (any
    interval), drawn in the theme foreground."""
    area = Gtk.DrawingArea()
    area.set_content_width(px)
    area.set_content_height(px)
    area.set_can_target(False)

    def _draw(widget, cr, width, height) -> None:
        color = widget.get_color()
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        side = min(width, height)
        cx, cy, r = width / 2, height / 2, side * 0.40
        cr.save()
        if not forward:
            cr.translate(width, 0)
            cr.scale(-1, 1)
        cr.set_line_width(max(side * 0.075, 1.6))
        cr.set_line_cap(0)  # butt: square ends
        start, end = math.radians(-62), math.radians(242)
        cr.arc(cx, cy, r, start, end)
        cr.stroke()
        # A filled head at the arc's end, along the clockwise turn.
        ex, ey = cx + r * math.cos(end), cy + r * math.sin(end)
        tx, ty = -math.sin(end), math.cos(end)
        nx, ny = math.cos(end), math.sin(end)
        head = side * 0.2
        cr.move_to(ex + tx * head * 0.9, ey + ty * head * 0.9)
        cr.line_to(ex - tx * head * 0.3 + nx * head * 0.62,
                   ey - ty * head * 0.3 + ny * head * 0.62)
        cr.line_to(ex - tx * head * 0.3 - nx * head * 0.62,
                   ey - ty * head * 0.3 - ny * head * 0.62)
        cr.close_path()
        cr.fill()
        cr.restore()
        layout = widget.create_pango_layout(str(seconds))
        font = Pango.FontDescription.from_string(
            widget.get_pango_context().get_font_description().to_string())
        font.set_absolute_size(side * 0.28 * Pango.SCALE)
        layout.set_font_description(font)
        tw, th = layout.get_pixel_size()
        cr.move_to(cx - tw / 2, cy - th / 2 + side * 0.03)
        from gi.repository import PangoCairo

        PangoCairo.show_layout(cr, layout)

    area.set_draw_func(_draw)
    return area


# -- scrubber -------------------------------------------------------------------


def draw_slider(cr, rgb, bg, width: float, height: float,
                fraction: float, dragging: bool = False) -> None:
    """HairlineSlider: a 1 px 25% hairline for the whole range, the
    played part as a 3 px bar, a 14 px square thumb with a ground rim."""
    mid = height / 2
    frac = min(max(fraction, 0.0), 1.0)
    thumb = 16 if dragging else 14
    cr.set_source_rgba(*rgb, 0.25)
    cr.rectangle(0, round(mid) - 0.5, width, 1)
    cr.fill()
    cr.set_source_rgba(*rgb, 1.0)
    cr.rectangle(0, mid - 1.5, width * frac, 3)
    cr.fill()
    x = min(max(width * frac - thumb / 2, 0), width - thumb)
    cr.set_source_rgba(*bg, 1.0)
    cr.rectangle(x - 1, mid - thumb / 2 - 1, thumb + 2, thumb + 2)
    cr.fill()
    cr.set_source_rgba(*rgb, 1.0)
    cr.rectangle(x, mid - thumb / 2, thumb, thumb)
    cr.fill()


def ground_rgb(widget: Gtk.Widget) -> tuple:
    """The window ground behind a slider thumb (the thumb's rim), from
    the theme style.py last applied (no file read here)."""
    from xx_audiobook.ui import style

    try:
        hexcode = style.ground.lstrip("#")
        return tuple(int(hexcode[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except (ValueError, IndexError):
        return (0.0, 0.0, 0.0)


class Scrubber(Gtk.DrawingArea):
    """SeekBar: book-level position on a HairlineSlider; drag or tap to
    seek, arrows step 30 s. ``ticks`` (chapter starts) are kept for the
    spoken value only; Android draws none."""

    __gtype_name__ = "XXAudiobookScrubber"
    __gsignals__ = {"seek": (GObject.SignalFlags.RUN_FIRST, None, (int,))}

    def __init__(self) -> None:
        super().__init__(accessible_role=Gtk.AccessibleRole.SLIDER)
        self.position_ms = 0
        self.duration_ms = 0
        self.ticks: list = []
        self._drag_ms: int | None = None
        self._ground = ground_rgb(self)
        self.set_content_height(32)
        self.set_hexpand(True)
        self.set_focusable(True)
        self.set_draw_func(self._draw)
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             ["Position in book"])
        drag = Gtk.GestureDrag.new()
        drag.connect("drag-begin", self._begin)
        drag.connect("drag-update", self._update)
        drag.connect("drag-end", self._end)
        self.add_controller(drag)
        keys = Gtk.EventControllerKey.new()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self._start_x = 0.0

    def set_state(self, position_ms: int, duration_ms: int,
                  ticks: list) -> None:
        self.position_ms, self.duration_ms = position_ms, duration_ms
        self.ticks = ticks
        self.update_property(
            [Gtk.AccessibleProperty.VALUE_MIN,
             Gtk.AccessibleProperty.VALUE_MAX,
             Gtk.AccessibleProperty.VALUE_NOW,
             Gtk.AccessibleProperty.VALUE_TEXT],
            [0.0, float(max(duration_ms, 1)), float(position_ms),
             f"{fmt.format_ms(position_ms)} of "
             f"{fmt.format_ms(duration_ms)}"])
        self.queue_draw()

    @property
    def dragging(self) -> bool:
        return self._drag_ms is not None

    @property
    def shown_ms(self) -> int:
        return self._drag_ms if self._drag_ms is not None \
            else self.position_ms

    def _ms_at(self, x: float) -> int:
        width = max(self.get_width(), 1)
        return int(min(max(x / width, 0.0), 1.0) * self.duration_ms)

    def _begin(self, _gesture, x, _y) -> None:
        if self.duration_ms <= 0:
            return
        self._start_x = x
        self._drag_ms = self._ms_at(x)
        self.queue_draw()

    def _update(self, _gesture, dx, _dy) -> None:
        if self._drag_ms is not None:
            self._drag_ms = self._ms_at(self._start_x + dx)
            self.queue_draw()

    def _end(self, _gesture, dx, _dy) -> None:
        if self._drag_ms is None:
            return
        target = self._ms_at(self._start_x + dx)
        self._drag_ms = None
        self.position_ms = target
        self.emit("seek", target)
        self.queue_draw()

    def _key(self, _ctrl, keyval, _code, _state) -> bool:
        from gi.repository import Gdk

        step = {Gdk.KEY_Left: -30000, Gdk.KEY_Right: 30000}.get(keyval)
        if step is None or self.duration_ms <= 0:
            return False
        self.emit("seek", min(max(self.position_ms + step, 0),
                              self.duration_ms))
        return True

    def _draw(self, widget, cr, width, height) -> None:
        color = widget.get_color()
        frac = (self.shown_ms / self.duration_ms
                if self.duration_ms > 0 else 0.0)
        draw_slider(cr, (color.red, color.green, color.blue), self._ground,
                    width, height, frac, self.dragging)


# -- sheets and confirms -----------------------------------------------------


def sheet(title: str) -> tuple:
    """ModalBottomSheet: an Adw.Dialog (a bottom sheet on the phone)
    holding a padded box with the title and a labelled close target.
    Returns (dialog, box)."""
    import gi

    gi.require_version("Adw", "1")
    from gi.repository import Adw

    dialog = Adw.Dialog()
    dialog.add_css_class("xx-ab-dialog")
    dialog.set_title(title)
    dialog.set_content_width(360)
    box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
    box.add_css_class("xx-ab-sheet")
    box.add_css_class("xx-ab")
    head = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    heading = label(title, "xx-ab-t16", chars=20)
    heading.set_hexpand(True)
    head.append(heading)
    head.append(icon_button("window-close-symbolic", "Close", dialog.close))
    box.append(head)
    dialog.set_child(box)
    return dialog, box


def present(dialog, parent: Gtk.Widget):
    """Every sheet and confirm opens through here (one seam)."""
    dialog.present(parent)
    return dialog


def confirm(parent: Gtk.Widget, heading: str, body: str, responses: list,
            on_response, extra: Gtk.Widget | None = None):
    """ConfirmDialog in the house sheet. ``responses``: (id, label, kind)
    with kind 'outline' | 'primary' | 'danger'; the first one is the
    dismiss answer (also on close)."""
    dialog, box = sheet(heading)
    if body:
        box.append(para(body, "xx-ab-b13"))
    if extra is not None:
        extra.set_margin_top(4)
        box.append(extra)
    from xx_audiobook.ui import style

    row = Gtk.Box.new(Gtk.Orientation.VERTICAL if style.large_text()
                      else Gtk.Orientation.HORIZONTAL, 8)
    row.set_homogeneous(True)
    row.set_margin_top(12)
    answered = []

    def _answer(response_id: str) -> None:
        if answered:
            return
        answered.append(response_id)
        dialog.close()
        on_response(response_id)

    for response_id, text, kind in responses:
        maker = {"primary": widgets.primary_button,
                 "danger": widgets.danger_button}.get(
                     kind, widgets.outline_button)
        button = maker(text, lambda r=response_id: _answer(r))
        fit(button, 14)
        row.append(button)
    box.append(row)
    dialog.connect("closed", lambda _d: _answer(responses[0][0]))
    return present(dialog, parent)


def later(ms: int, callback) -> None:
    GLib.timeout_add(ms, lambda: (callback(), False)[1])


# -- small building blocks -----------------------------------------------------


def glyph(icon: str, px: int = 20, on: bool = False) -> Gtk.Image:
    image = widgets.icon_image(icon, px)
    image.add_css_class("xx-ab-glyph")
    if on:
        image.add_css_class("on")
    return image


def bar(fraction: float, spoken: str) -> Gtk.ProgressBar:
    """The 3 px hairline as a chart bar (its own spoken value)."""
    widget = line(fraction)
    widget.update_property([Gtk.AccessibleProperty.LABEL], [spoken])
    return widget


def stars(value: float) -> Gtk.Label:
    """Read-only ★★★★☆ in the foreground, read as 'n of 5 stars'."""
    whole = int(round(min(max(value or 0.0, 0.0), 5.0)))
    mark = Gtk.Label(label="★" * whole + "☆" * (5 - whole))
    mark.add_css_class("xx-ab-stars")
    mark.set_valign(Gtk.Align.CENTER)
    mark.update_property([Gtk.AccessibleProperty.LABEL],
                         [fmt.stars_text(value)])
    return mark


def dot(on: bool, spoken: str) -> Gtk.Label:
    """● working / ○ not: a status mark in text, never color alone."""
    mark = Gtk.Label(label="●" if on else "○")
    mark.add_css_class("xx-ab-dot")
    if on:
        mark.add_css_class("on")
    mark.set_valign(Gtk.Align.START)
    mark.update_property([Gtk.AccessibleProperty.LABEL], [spoken])
    return mark


def badge(text: str) -> Gtk.Label:
    """Tag: a hairline square box, the label in the muted stop."""
    tag = Gtk.Label(label=text)
    tag.add_css_class("xx-ab-badge")
    tag.set_valign(Gtk.Align.CENTER)
    tag.set_halign(Gtk.Align.START)
    return tag


def tap_row(child: Gtk.Widget, spoken: str, callback,
            last: bool = False) -> Gtk.Button:
    """A full-width hairline row button around ``child``."""
    row = Gtk.Button()
    row.add_css_class("xx-ab-row")
    if last:
        row.add_css_class("last")
    row.set_child(child)
    row.update_property([Gtk.AccessibleProperty.LABEL], [spoken])
    row.connect("clicked", lambda _b: callback())
    return row


def figure(value: str, caption: str) -> Gtk.Box:
    """Stats Tile: a hairline square, a caps caption over the value."""
    box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
    box.add_css_class("xx-ab-tile-box")
    box.append(label(caption.upper(), "xx-ab-label", chars=10))
    box.append(label(value, "xx-ab-t16 xx-ab-num", lines=2, chars=10))
    box.update_property([Gtk.AccessibleProperty.LABEL],
                        [f"{caption}: {value}"])
    return box
