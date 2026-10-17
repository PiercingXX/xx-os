"""Shared widgets for the audiobook pages (M3), thin over xx_ui.widgets.

Covers (square, monochrome initials placeholder), book rows and tiles,
offline tags, hairline progress, the skip-seconds glyph and the
book-level scrubber. Every icon-only control carries a label and a
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
                           "xx-ab-readout"})


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
    widget.add_css_class(cls)
    widget.set_xalign(0.0)
    widget.set_wrap(True)
    widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    widget.set_max_width_chars(32)
    return widget


def muted(text: str) -> Gtk.Label:
    """The one muted line for empty, error and offline states."""
    line = para(text, "xx-secondary")
    line.set_margin_top(12)
    line.set_margin_bottom(12)
    return line


def caps(text: str) -> Gtk.Label:
    return label(text.upper(), "xx-ab-label", chars=28)


def name(widget: Gtk.Widget, text: str) -> Gtk.Widget:
    """Accessible label + tooltip for icon-only controls."""
    widget.set_tooltip_text(text)
    widget.update_property([Gtk.AccessibleProperty.LABEL], [text])
    return widget


def header(back, title: str) -> Gtk.Box:
    """xx_ui's compact sub-page bar with a labelled 48 px back target."""
    bar = widgets.compact_header(back, title)
    back_button = bar.get_first_child()
    back_button.set_size_request(48, 48)
    name(back_button, "Back")
    return bar


def fit(button: Gtk.Button, chars: int) -> Gtk.Button:
    """A text button whose label ellipsizes instead of widening the
    window (large text sizes); its allocation always exceeds the natural
    width at 1x, so the ellipsis never misfires there."""
    child = button.get_child()
    child.set_max_width_chars(chars)
    child.set_ellipsize(Pango.EllipsizeMode.END)
    return button


def icon_button(icon: str, text: str, callback) -> Gtk.Button:
    button = widgets.icon_button(icon, callback)
    button.set_size_request(48, 48)
    return name(button, text)


def clear(box: Gtk.Box) -> None:
    child = box.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        box.remove(child)
        child = following


def line(fraction: float) -> Gtk.ProgressBar:
    """2 px hairline progress: played part in the theme foreground."""
    bar = Gtk.ProgressBar()
    bar.add_css_class("xx-ab-line")
    bar.set_fraction(min(max(fraction, 0.0), 1.0))
    bar.set_hexpand(True)
    bar.update_property([Gtk.AccessibleProperty.LABEL],
                        [f"{fmt.percent(fraction)}% listened"])
    return bar


def offline_tag() -> Gtk.Box:
    """'On this phone': a hairline tag with the downloaded glyph."""
    tag = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 3)
    tag.add_css_class("xx-ab-tag")
    tag.set_valign(Gtk.Align.CENTER)
    tag.set_halign(Gtk.Align.START)
    tag.append(widgets.icon_image("folder-download-symbolic", 10))
    text = Gtk.Label(label="OFFLINE")
    text.set_ellipsize(Pango.EllipsizeMode.END)
    tag.append(text)
    tag.update_property([Gtk.AccessibleProperty.LABEL], ["Downloaded"])
    return tag


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
    """Square cover: initials placeholder until the art arrives (if any).

    The placeholder is the house glyph language: light initials over a
    short 2 px rule on a surface tile with a hairline."""
    holder = Gtk.Overlay()
    holder.add_css_class("xx-ab-cover")
    glyph = Gtk.Box.new(Gtk.Orientation.VERTICAL, max(px // 14, 4))
    glyph.set_halign(Gtk.Align.CENTER)
    glyph.set_valign(Gtk.Align.CENTER)
    tier = "s" if 0 < px <= 64 else ("l" if px >= 160 else "m")
    initials = Gtk.Label(label=fmt.initials(title))
    initials.add_css_class("xx-ab-initials")
    initials.add_css_class(tier)
    glyph.append(initials)
    rule = Gtk.Box()
    rule.add_css_class("xx-ab-rule")
    rule.set_size_request({"s": 12, "m": 22, "l": 36}[tier], 2)
    rule.set_halign(Gtk.Align.CENTER)
    glyph.append(rule)
    holder.set_child(glyph)
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
        glyph.set_visible(False)

    ctx.covers.request(item_id, size, _deliver)
    return square


# -- book rows and tiles ----------------------------------------------------------


def _meta_row(item, downloaded: bool) -> Gtk.Box | None:
    meta = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    parts = []
    rating = fmt.rating_text(item.rating_average, item.rating_count)
    if rating:
        parts.append(label(rating, "xx-ab-meta xx-ab-num", chars=8))
    if item.is_finished:
        parts.append(label("Finished", "xx-ab-meta", chars=10))
    if item.missing:
        parts.append(label("Missing on server", "xx-ab-meta xx-danger-text",
                           chars=18))
    if downloaded:
        parts.append(offline_tag())
    if not parts:
        return None
    for part in parts:
        meta.append(part)
    return meta


def book_row(ctx, item, on_open, prefix: str | None = None,
             last: bool = False) -> Gtk.Button:
    """Library row: cover, title, author, rating/finished/offline, progress."""
    row = Gtk.Button()
    row.add_css_class("xx-ab-row")
    if last:
        row.add_css_class("last")
    box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 14)
    art = cover(ctx, item.id, item.title, 56, bool(item.thumb_url))
    art.set_valign(Gtk.Align.START)
    box.append(art)
    text = Gtk.Box.new(Gtk.Orientation.VERTICAL, 3)
    text.set_hexpand(True)
    text.set_valign(Gtk.Align.CENTER)
    if prefix:
        text.append(caps(prefix))
    text.append(label(item.title, "xx-title", lines=2, chars=20))
    if item.authors:
        text.append(label(item.authors, "xx-secondary", chars=22))
    meta = _meta_row(item, item.id in ctx.downloaded)
    if meta is not None:
        meta.set_margin_top(2)
        text.append(meta)
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
    """Grid / shelf tile: cover, hairline progress, two-line title, author."""
    tile = Gtk.Button()
    tile.add_css_class("xx-ab-tile")
    box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    box.append(cover(ctx, item.id, item.title, px, bool(item.thumb_url)))
    bar = line(item.fraction if not item.is_finished else 0.0)
    bar.set_margin_top(6)
    bar.set_opacity(1.0 if 0 < item.fraction < 1 else 0.0)
    box.append(bar)
    chars = max(px // 9, 10) if px else 14
    title = label(item.title, "xx-ab-meta xx-ab-strong", lines=2,
                  chars=chars)
    title.set_margin_top(6)
    box.append(title)
    if item.authors:
        box.append(label(item.authors, "xx-ab-meta", chars=chars))
    if item.id in ctx.downloaded:
        tag = offline_tag()
        tag.set_margin_top(4)
        box.append(tag)
    tile.set_child(Clamp(box, px) if px else box)
    tile.update_property([Gtk.AccessibleProperty.LABEL], [item.title])
    tile.connect("clicked", lambda _b: on_open(item))
    return tile


def section(text: str, first: bool = False) -> Gtk.Label:
    """A caps section label; ``first`` sits right under a tab's title."""
    head = widgets.section_label(text)
    if first:
        head.add_css_class("xx-ab-first")
    else:
        head.set_margin_top(20)
    return head


def section_with(text: str, icon: str, spoken: str, callback,
                 first: bool = False) -> Gtk.Box:
    """A section caption with one labelled 48 px action at its end."""
    row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    row.add_css_class("xx-ab-head")
    if first:
        row.set_margin_top(0)
    head = caps(text)
    head.set_hexpand(True)
    head.set_valign(Gtk.Align.CENTER)
    row.append(head)
    action = icon_button(icon, spoken, callback)
    action.set_valign(Gtk.Align.CENTER)
    row.append(action)
    return row


# -- skip glyph ----------------------------------------------------------------


def skip_glyph(seconds: int, forward: bool, px: int = 32) -> Gtk.DrawingArea:
    """A circular arrow with the skip seconds inside (any interval):
    round-capped strokes in the theme foreground, no fill."""
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
        cr.set_line_width(max(side * 0.055, 1.4))
        cr.set_line_cap(1)  # round
        cr.set_line_join(1)
        start, end = math.radians(-62), math.radians(242)
        cr.arc(cx, cy, r, start, end)
        cr.stroke()
        # A filled head at the arc's end, along the clockwise turn.
        ex, ey = cx + r * math.cos(end), cy + r * math.sin(end)
        tx, ty = -math.sin(end), math.cos(end)
        nx, ny = math.cos(end), math.sin(end)
        head = side * 0.19
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
        font.set_absolute_size(side * 0.30 * Pango.SCALE)
        layout.set_font_description(font)
        tw, th = layout.get_pixel_size()
        cr.move_to(cx - tw / 2, cy - th / 2 + side * 0.02)
        from gi.repository import PangoCairo

        PangoCairo.show_layout(cr, layout)

    area.set_draw_func(_draw)
    return area


# -- scrubber -------------------------------------------------------------------


class Scrubber(Gtk.DrawingArea):
    """Book-level seek bar: 2 px trough, the played part bright, chapter
    ticks, a slim playhead; drag or tap to seek, arrows step 30 s."""

    __gtype_name__ = "XXAudiobookScrubber"
    __gsignals__ = {"seek": (GObject.SignalFlags.RUN_FIRST, None, (int,))}

    def __init__(self) -> None:
        super().__init__(accessible_role=Gtk.AccessibleRole.SLIDER)
        self.position_ms = 0
        self.duration_ms = 0
        self.ticks: list = []
        self._drag_ms: int | None = None
        self.set_content_height(36)
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
             fmt.format_ms(position_ms)])
        self.queue_draw()

    @property
    def dragging(self) -> bool:
        return self._drag_ms is not None

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
        rgb = (color.red, color.green, color.blue)
        mid = height / 2
        shown = self._drag_ms if self._drag_ms is not None \
            else self.position_ms
        frac = (shown / self.duration_ms) if self.duration_ms > 0 else 0.0
        frac = min(max(frac, 0.0), 1.0)
        cr.set_source_rgba(*rgb, 0.22)
        cr.rectangle(0, mid - 1, width, 2)
        cr.fill()
        cr.set_source_rgba(*rgb, 0.45)
        for tick in self.ticks:
            if self.duration_ms > 0 and 0 < tick < self.duration_ms:
                x = round(width * tick / self.duration_ms)
                cr.rectangle(x, mid + 4, 1, 4)
        cr.fill()
        cr.set_source_rgba(*rgb, 1.0)
        cr.rectangle(0, mid - 1, width * frac, 2)
        cr.fill()
        head = 3 if self._drag_ms is not None else 2
        tall = 20 if self._drag_ms is not None else 14
        x = min(max(width * frac - head / 2, 0), width - head)
        cr.rectangle(x, mid - tall / 2, head, tall)
        cr.fill()


# -- sheets and confirms -----------------------------------------------------


def sheet(title: str) -> tuple:
    """An Adw.Dialog (a bottom sheet on the phone) holding a padded box
    with the title and a labelled close target. Returns (dialog, box)."""
    import gi

    gi.require_version("Adw", "1")
    from gi.repository import Adw

    dialog = Adw.Dialog()
    dialog.add_css_class("xx-ab-dialog")
    dialog.set_title(title)
    dialog.set_content_width(360)
    box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
    box.add_css_class("xx-ab-sheet")
    head = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    heading = label(title, "xx-dialog-title", chars=20)
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
    """A house-style confirm sheet. ``responses``: (id, label, kind) with
    kind 'outline' | 'primary' | 'danger'; the first one is the dismiss
    answer (also on close)."""
    dialog, box = sheet(heading)
    if body:
        box.append(para(body, "xx-secondary"))
    if extra is not None:
        extra.set_margin_top(4)
        box.append(extra)
    from xx_audiobook.ui import style

    row = Gtk.Box.new(Gtk.Orientation.VERTICAL if style.large_text()
                      else Gtk.Orientation.HORIZONTAL, 10)
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


# -- M4/M5 building blocks -----------------------------------------------------


def page_top(title: str, actions: list = ()) -> Gtk.Box:
    """A tab root's header: the display title at the gutter and labelled
    48 px icon actions, (icon, spoken, callback), like the Library."""
    top = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    top.add_css_class("xx-page-body")
    top.add_css_class("xx-page-top")
    heading = widgets.label(title, "xx-display")
    heading.add_css_class("xx-display-face")
    heading.set_hexpand(True)
    heading.set_ellipsize(Pango.EllipsizeMode.END)
    heading.set_max_width_chars(12)
    top.append(heading)
    for icon, spoken, callback in actions:
        top.append(icon_button(icon, spoken, callback))
    return top


def glyph(icon: str, px: int = 20, on: bool = False) -> Gtk.Image:
    image = widgets.icon_image(icon, px)
    image.add_css_class("xx-ab-glyph")
    if on:
        image.add_css_class("on")
    return image


def bar(fraction: float, spoken: str) -> Gtk.ProgressBar:
    """The 2 px hairline as a chart bar (its own spoken value)."""
    widget = line(fraction)
    widget.update_property([Gtk.AccessibleProperty.LABEL], [spoken])
    return widget


def stars(value: float, px: int = 14) -> Gtk.Box:
    """Five read-only stars, filled up to the rounded value."""
    box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 2)
    box.add_css_class("xx-ab-stars")
    whole = int(round(min(max(value or 0.0, 0.0), 5.0)))
    for index in range(5):
        star = widgets.icon_image(
            "starred-symbolic" if index < whole else "non-starred-symbolic",
            px)
        if index < whole:
            star.add_css_class("on")
        box.append(star)
    box.set_valign(Gtk.Align.CENTER)
    box.update_property([Gtk.AccessibleProperty.LABEL],
                        [fmt.stars_text(value)])
    return box


def dot(on: bool, spoken: str) -> Gtk.Label:
    """● working / ○ not: a status mark in text, never color alone."""
    mark = Gtk.Label(label="●" if on else "○")
    mark.add_css_class("xx-ab-dot")
    if on:
        mark.add_css_class("on")
    mark.set_valign(Gtk.Align.CENTER)
    mark.update_property([Gtk.AccessibleProperty.LABEL], [spoken])
    return mark


def badge(text: str) -> Gtk.Label:
    tag = Gtk.Label(label=text.upper())
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
    """A big light number over a caps caption (stats tiles)."""
    box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
    box.append(label(value, "xx-ab-figure xx-ab-num", chars=8))
    box.append(caps(caption))
    box.update_property([Gtk.AccessibleProperty.LABEL],
                        [f"{caption}: {value}"])
    return box


def spacer(px: int) -> Gtk.Box:
    box = Gtk.Box()
    box.set_size_request(-1, px)
    return box
