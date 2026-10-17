"""Minimal Mode: xx-radio's text-first rendering, after xx-launcher and TxxT.

Words instead of icons, tiles and cards. ``[ label ]`` is Minimal Mode's
only kind of button; a bare word acts where brackets would crowd (tabs,
top rows, filters). State is carried by weight and opacity of the
theme's own foreground, never by fills or a second color:

    Strong  foreground, bold   on / the one thing that matters
    Plain   foreground @ 90 %  ordinary text and actions
    Quiet   foreground @ 50 %  off / secondary
    (unavailable)      @ 25 %  can't be used right now

Album art is off exactly when Minimal Mode is on: no screen builds an
art tile and the art loader refuses every request, so no art leaves the
network while it is on (a bandwidth saver).

The state lives here, like ``css.large_text()``: screens ask
``active()`` while they build, and ``listen(callback)`` rebuilds them
when the setting flips (bound methods are held weakly, so a page that
was popped and dropped is not kept alive by its listener).

The top half is pure (tones, row text, shelf caps); the widget builders
import GTK lazily, so tests can check the vocabulary without a display.
"""

from __future__ import annotations

import weakref
from enum import Enum
from typing import Callable, Iterable

#: Rows a minimal shelf shows before "more…".
SHELF_ROWS = 5
MORE = "more…"
LESS = "less"
#: Row suffixes: opens a page, on this phone, still resolving.
OPENS = "▸"
ON_PHONE = "↓"
RESOLVING = "…"
DASH = " — "
#: The playing marker in front of a title.
PLAYING = "▸ "


class Tone(Enum):
    STRONG = "strong"
    PLAIN = "plain"
    QUIET = "quiet"


#: (opacity of the theme foreground, font weight) per tone.
TONE_STYLE: dict[Tone, tuple[float, int]] = {
    Tone.STRONG: (1.0, 700),
    Tone.PLAIN: (0.9, 400),
    Tone.QUIET: (0.5, 400),
}
UNAVAILABLE_STYLE = (0.25, 400)
#: CSS class per tone (css.py renders them from the live theme).
TONE_CLASS = {Tone.STRONG: "xx-mm-strong", Tone.PLAIN: "xx-mm-plain",
              Tone.QUIET: "xx-mm-quiet"}
UNAVAILABLE_CLASS = "xx-mm-off"
ALL_TONE_CLASSES = (*TONE_CLASS.values(), UNAVAILABLE_CLASS)
#: Font sizes the vocabulary uses (px at text size 1). Text scales with
#: the text size; button words only to 1.3x, so a row of them still fits.
SIZES = (12, 13, 14, 15, 17)


# -- pure vocabulary -------------------------------------------------------------

def tone_style(tone: Tone, enabled: bool = True) -> tuple[float, int]:
    """(opacity, weight): Strong is full and bold, Plain 90 %, Quiet
    50 %; anything unavailable is 25 % whatever its tone."""
    return TONE_STYLE[tone] if enabled else UNAVAILABLE_STYLE


def tone_class(tone: Tone, enabled: bool = True) -> str:
    return TONE_CLASS[tone] if enabled else UNAVAILABLE_CLASS


def toggled(on: bool) -> Tone:
    """The tone of a two-state control: on is Strong, off is Quiet."""
    return Tone.STRONG if on else Tone.QUIET


def bracket(label: str) -> str:
    return f"[ {label} ]"


def tab_word(title: str, open_: bool) -> str:
    """Tabs are words: the open one in caps, the rest lower case."""
    return title.upper() if open_ else title.lower()


def sentence_word(words: str) -> str:
    """A button label as Minimal Mode says it: lower-case first letter,
    names kept ("Open Lumen Drive" -> "open Lumen Drive")."""
    return words[:1].lower() + words[1:]


def on_off(on: bool) -> str:
    return "on" if on else "off"


def title_artist(title: str, artist: str = "", *, opens: bool = False,
                 downloaded: bool = False, resolving: bool = False) -> str:
    """One shelf row: ``Title — Artist``, then suffixes that carry
    state, as in the launcher drawer: ``▸`` opens a page, ``↓`` is on
    this phone, ``…`` is still resolving."""
    text = title.strip()
    artist = artist.strip()
    if artist and artist.casefold() != text.casefold():
        text = f"{text}{DASH}{artist}"
    for flag, mark in ((opens, OPENS), (downloaded, ON_PHONE),
                       (resolving, RESOLVING)):
        if flag:
            text = f"{text}  {mark}"
    return text


def item_text(item, downloaded: bool = False) -> str:
    """``title_artist`` for an engine Item: songs get their artists,
    albums and playlists their subtitle (without a repeated kind word)
    and ``▸``; artists and moods are just their name and ``▸``."""
    from xx_music.engine import ItemKind
    from xx_music.ui import fmt

    if item.kind == ItemKind.TRACK:
        artist = item.track.artist_line if item.track is not None \
            else item.subtitle
        return title_artist(item.title, artist, downloaded=downloaded)
    if item.kind in (ItemKind.ARTIST, ItemKind.MOOD):
        return title_artist(item.title, opens=True)
    sub = item.subtitle
    if sub.strip().lower() == fmt.kind_word(item.kind).lower():
        sub = ""
    return title_artist(item.title, sub, opens=True)


def playing_title(title: str, playing: bool) -> str:
    """``▸ title`` on the row that is playing."""
    return f"{PLAYING}{title}" if playing else title


def shelf_cut(items: list, expanded: bool,
              rows: int = SHELF_ROWS) -> tuple[list, str]:
    """(the rows shown, the toggle word: "more…", "less" or "")."""
    items = list(items)
    if len(items) <= rows:
        return items, ""
    if expanded:
        return items, LESS
    return items[:rows], MORE


# -- state ----------------------------------------------------------------------------

_state = {"on": False}
_listeners: list = []


def active() -> bool:
    """True while Minimal Mode is on (screens ask while they build)."""
    return _state["on"]


def listen(callback: Callable[[], None]) -> None:
    """``callback()`` after every flip. Bound methods are held weakly."""
    if hasattr(callback, "__self__") and hasattr(callback, "__func__"):
        _listeners.append(weakref.WeakMethod(callback))
    else:
        _listeners.append(lambda cb=callback: cb)


def set_active(on: bool) -> bool:
    """Flip the mode; listeners rebuild their screens. Returns whether
    anything changed."""
    on = bool(on)
    if on == _state["on"]:
        return False
    _state["on"] = on
    for ref in list(_listeners):
        callback = ref()
        if callback is not None:
            callback()
    _listeners[:] = [r for r in _listeners if r() is not None]
    return True


def reset() -> None:
    """Off, no listeners (tests)."""
    _state["on"] = False
    _listeners.clear()


def listener_count() -> int:
    return sum(1 for ref in _listeners if ref() is not None)


# -- widgets -----------------------------------------------------------------------------

def _gtk():
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    return Gtk


def set_tone(widget, tone: Tone, enabled: bool = True) -> None:
    """Swap a widget's tone class (a button's label follows)."""
    Gtk = _gtk()
    targets = [widget]
    if isinstance(widget, Gtk.Button) and isinstance(widget.get_child(),
                                                     Gtk.Label):
        targets.append(widget.get_child())
    for target in targets:
        for cls in ALL_TONE_CLASSES:
            target.remove_css_class(cls)
        target.add_css_class(tone_class(tone, enabled))


def text(words: str, tone: Tone = Tone.PLAIN, size: int = 15,
         lines: int = 1, chars: int = 24, xalign: float = 0.0,
         wrap: bool = False):
    """A label in a tone. One line ellipsizes; ``wrap`` wraps fully."""
    Gtk = _gtk()
    from gi.repository import Pango

    label = Gtk.Label(label=words)
    label.add_css_class("xx-mm-text")
    label.add_css_class(f"xx-mm-s{size}")
    label.add_css_class(tone_class(tone))
    label.set_xalign(xalign)
    label.set_max_width_chars(chars)
    if wrap:
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    else:
        label.set_ellipsize(Pango.EllipsizeMode.END)
        if lines > 1:
            label.set_wrap(True)
            label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            label.set_lines(lines)
    return label


def _button(words: str, callback, tone: Tone, enabled: bool, size: int,
            spoken: str, cls: str, chars: int = 0):
    Gtk = _gtk()
    from gi.repository import Pango

    button = Gtk.Button()
    button.add_css_class("xx-mm-btn")
    button.add_css_class(cls)
    label = Gtk.Label(label=words)
    label.add_css_class(f"xx-mm-b{size}")
    # Long labels may ellipsize at the window edge (natural width stays
    # the whole label); short words never do.
    label.set_ellipsize(Pango.EllipsizeMode.END if chars
                        and len(words) > chars
                        else Pango.EllipsizeMode.NONE)
    button.set_child(label)
    set_tone(button, tone, enabled)
    button.set_sensitive(enabled)
    button.set_valign(Gtk.Align.CENTER)
    if callback is not None:
        button.connect("clicked", lambda _b: callback())
    button.update_property([Gtk.AccessibleProperty.LABEL],
                           [spoken or words.strip("[] ")])
    return button


def bracket_button(label: str, callback, tone: Tone = Tone.PLAIN,
                   enabled: bool = True, size: int = 15, spoken: str = "",
                   chars: int = 0):
    """``[ label ]``, Minimal Mode's only kind of button."""
    return _button(bracket(label), callback, tone, enabled, size,
                   spoken or label, "xx-mm-bracket", chars)


def word_button(label: str, callback, tone: Tone = Tone.QUIET,
                size: int = 14, spoken: str = "", chars: int = 0):
    """A bare word that acts, for top rows where brackets would crowd."""
    return _button(label, callback, tone, True, size, spoken or label,
                   "xx-mm-word", chars)


def flow(widgets_: Iterable, center: bool = False, spacing: int = 4):
    """Buttons in a row that wraps instead of widening the window (320 px,
    large text)."""
    Gtk = _gtk()
    box = Gtk.FlowBox()
    box.add_css_class("xx-mm-flow")
    box.set_selection_mode(Gtk.SelectionMode.NONE)
    box.set_homogeneous(False)
    box.set_column_spacing(spacing)
    box.set_row_spacing(0)
    box.set_min_children_per_line(1)
    children = list(widgets_)
    box.set_max_children_per_line(max(len(children), 1))
    # Never stretched: a stretched line spreads its words apart.
    box.set_halign(Gtk.Align.CENTER if center else Gtk.Align.START)
    for child in children:
        box.append(child)
        holder = child.get_parent()
        holder.set_focusable(False)
    return box


def words_row(items: list, size: int = 14):
    """A wrapping row of words. ``items``: (label, on, callback); the
    one that is on is Strong, the rest Quiet."""
    return flow([word_button(label.lower(), callback, toggled(on), size)
                 for label, on, callback in items], spacing=0)


def _press(widget, on_menu) -> None:
    from xx_music.ui import common

    common.on_long_press(widget, on_menu)


def text_row(words: str, on_click, on_menu=None, tone: Tone = Tone.PLAIN,
             size: int = 15, spoken: str = "", chars: int = 30):
    """One tappable line (a shelf row, a suggestion); long-press opens
    ``on_menu``."""
    Gtk = _gtk()
    row = Gtk.Button()
    row.add_css_class("xx-mm-row")
    row.set_child(text(words, tone, size, chars=chars))
    if on_click is not None:
        row.connect("clicked", lambda _b: on_click())
    _press(row, on_menu)
    row.update_property([Gtk.AccessibleProperty.LABEL], [spoken or words])
    return row


def list_row(title: str, sub: str, on_click, on_menu=None, *,
             now: bool = False, meta: str = "", spoken: str = ""):
    """A list row without art or icons: the title (``▸ title`` and
    Strong while it plays), a Quiet second line, a Quiet trailing meta."""
    Gtk = _gtk()
    from xx_music.ui import common, css

    row = Gtk.Button()
    row.add_css_class("xx-mm-row")
    line = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
    texts = Gtk.Box.new(Gtk.Orientation.VERTICAL, 1)
    texts.set_hexpand(True)
    texts.set_valign(Gtk.Align.CENTER)
    texts.append(text(playing_title(title, now),
                      Tone.STRONG if now else Tone.PLAIN, 15, chars=22))
    if sub:
        texts.append(text(sub, Tone.QUIET, 13, chars=28))
    line.append(texts)
    if meta and not css.large_text():
        when = text(meta, Tone.QUIET, 13, chars=7, xalign=1.0)
        when.add_css_class("xx-mu-num")
        when.set_valign(Gtk.Align.CENTER)
        line.append(when)
    row.set_child(line)
    if on_click is not None:
        row.connect("clicked", lambda _b: on_click())
    common.on_long_press(row, on_menu)
    row.update_property([Gtk.AccessibleProperty.LABEL],
                        [spoken or ", ".join(p for p in (title, sub) if p)])
    return row


def shelf(entries: list, expanded: bool, on_toggle,
          rows: int = SHELF_ROWS):
    """Launcher-drawer shelf: a few one-line rows, the rest behind
    "more…". ``entries``: (text, on_click, on_menu)."""
    Gtk = _gtk()
    box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    box.add_css_class("xx-mm-shelf")
    shown, toggle = shelf_cut(entries, expanded, rows)
    for words, on_click, on_menu in shown:
        box.append(text_row(words, on_click, on_menu))
    if toggle:
        more = word_button(toggle, on_toggle, Tone.QUIET, 13)
        more.set_halign(Gtk.Align.START)
        box.append(more)
    return box


def section(words: str, action: str = "", callback=None):
    """A caps section label with an optional trailing word."""
    Gtk = _gtk()
    head = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    head.add_css_class("xx-mm-section")
    caption = text(words.upper(), Tone.QUIET, 12, chars=26)
    caption.add_css_class("xx-mm-caps")
    caption.set_hexpand(True)
    caption.set_valign(Gtk.Align.CENTER)
    head.append(caption)
    if action and callback is not None:
        head.append(word_button(action.lower(), callback, Tone.QUIET, 13))
    return head


def hairline(fraction: float | None = None):
    """A 1 px line in the 10 % foreground; with ``fraction``, the played
    part in Plain (the mini player's progress)."""
    Gtk = _gtk()
    bar = Gtk.ProgressBar()
    bar.add_css_class("xx-mm-hairline")
    bar.set_hexpand(True)
    bar.set_fraction(min(max(fraction or 0.0, 0.0), 1.0))
    bar.set_can_target(False)
    return bar


def button(words: str, callback=None, kind: str = "outline"):
    """The app's text buttons, minimal-aware: ``[ words ]`` while Minimal
    Mode is on (primary and danger are Strong, outline Plain, text
    Quiet), the house button otherwise."""
    from xx_ui import widgets

    if active():
        tone = {"primary": Tone.STRONG, "danger": Tone.STRONG,
                "text": Tone.QUIET}.get(kind, Tone.PLAIN)
        made = bracket_button(sentence_word(words), callback, tone,
                              chars=12, spoken=words)
        made.set_halign(_gtk().Align.START)
        return made
    maker = {"primary": widgets.primary_button,
             "danger": widgets.danger_button,
             "text": widgets.text_button}.get(kind, widgets.outline_button)
    return maker(words, callback)


def primary(words: str, callback=None):
    return button(words, callback, "primary")


def outline(words: str, callback=None):
    return button(words, callback, "outline")


def danger(words: str, callback=None):
    return button(words, callback, "danger")


def quiet(words: str, callback=None):
    return button(words, callback, "text")


def draw_hairline(cr, width: float, height: float, fraction: float,
                  rgb: tuple, dragging: bool = False) -> None:
    """The hairline seek: a 1 px line (10 %), the played part (90 %), a
    3 px dot at the head (full foreground)."""
    import math

    mid = round(height / 2) + 0.5
    left, span = 4.0, max(width - 8.0, 1.0)
    head = left + span * min(max(fraction, 0.0), 1.0)
    cr.set_line_width(1.0)
    cr.set_source_rgba(*rgb, 0.10)
    cr.move_to(left, mid)
    cr.line_to(left + span, mid)
    cr.stroke()
    cr.set_source_rgba(*rgb, 0.90)
    cr.move_to(left, mid)
    cr.line_to(head, mid)
    cr.stroke()
    cr.set_source_rgba(*rgb, 1.0)
    cr.arc(head, mid, 4.5 if dragging else 3.0, 0, 2 * math.pi)
    cr.fill()
