"""Thin builders for the XX components: plain Gtk widgets with the
``xx-`` CSS classes applied. No subclasses, no framework. Uppercase is
applied in code (GTK CSS has no text-transform).

Width contract: the phone canvas is 360 px wide, so no helper may impose
a natural width above 320 px of content. Every label therefore carries a
``max-width-chars`` cap plus ellipsize (single line) or word-char wrap;
the caps only bound size negotiation. Labels allocated exactly their
natural width (button children, centered separator captions) ellipsize
only text longer than the cap: ellipsize misfires on fractional rounding
otherwise, swapping a whole fitting character for an ellipsis. Icons are
symbolic icon-theme images, colored by CSS, never Unicode glyphs.
"""

from __future__ import annotations

from dataclasses import dataclass
import os

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import Gtk, Pango

BAR_ICON_PX = 20
BUTTON_ICON_PX = 24

_STANDALONE_CHARS = 32
_ROW_CHARS = 12
_HEADER_TITLE_CHARS = 8
_BUTTON_CHARS = 8
_WIDE_BUTTON_CHARS = 16
_TAB_CHARS = 8
_CHIP_CHARS = 16
_META_CHARS = 12
_AVATAR_CHARS = 4
_KEY_DIGIT_CHARS = 4
_KEY_SUB_CHARS = 8
_ENTRY_CHARS = 20
_BIG_LABEL_CHARS = 12

_BIG_LABEL_CLASSES = frozenset(
    ('xx-display', 'xx-display-large', 'xx-hero'))


def _fit_single(line: Gtk.Label, chars: int) -> Gtk.Label:
    line.set_ellipsize(Pango.EllipsizeMode.END)
    line.set_max_width_chars(chars)
    return line


def _fit_wrap(line: Gtk.Label, chars: int = _STANDALONE_CHARS) -> Gtk.Label:
    line.set_wrap(True)
    line.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    line.set_max_width_chars(chars)
    line.set_ellipsize(Pango.EllipsizeMode.NONE)
    return line


def _fit_exact(text_label: Gtk.Label, text: str, chars: int) -> Gtk.Label:
    # For labels allocated exactly their natural width (button children,
    # captions centered between flexible separators): ellipsize fires on
    # fractional rounding even when the text fits, swapping a whole
    # character for an ellipsis. Only short text can fit exactly, so
    # ellipsize only the text that cannot fit; anything else clips a
    # fraction of a pixel invisibly instead.
    text_label.set_max_width_chars(chars)
    if len(text) > chars:
        text_label.set_ellipsize(Pango.EllipsizeMode.END)
    else:
        text_label.set_ellipsize(Pango.EllipsizeMode.NONE)
    return text_label


def fit_text(text_label: Gtk.Label, text: str, chars: int) -> Gtk.Label:
    """Set text with an ellipsis only when it cannot fit ``chars``.

    For labels whose text updates after build (hero numbers, empty
    states, trailing times): short text never misfires an ellipsis on
    fractional rounding; long text still truncates instead of widening
    the window.
    """
    text_label.set_text(text)
    return _fit_exact(text_label, text, chars)


def wrap_to_fit(widget: Gtk.Widget) -> Gtk.Widget:
    """Let a short label (or a button's label) wrap between words, centered,
    when its row is too narrow (320 px, large text), instead of widening
    the window. It stays on one line wherever it fits; a single word
    still never breaks. Give a letter-spaced (caps) label room to grow
    (hexpand in a filling row): held to exactly its natural width, it
    can wrap its last word. Returns ``widget``."""
    text_label = widget.get_child() if isinstance(widget, Gtk.Button) \
        else widget
    if not isinstance(text_label, Gtk.Label):
        return widget
    # A generous cap: letter-spacing is not in the per-char width, so a
    # tight cap would wrap a label that fits.
    _fit_wrap(text_label, _STANDALONE_CHARS)
    text_label.set_wrap_mode(Pango.WrapMode.WORD)
    text_label.set_natural_wrap_mode(Gtk.NaturalWrapMode.NONE)
    text_label.set_justify(Gtk.Justification.CENTER)
    return widget


def _fit_button_text(button: Gtk.Button,
                     chars: int = _BUTTON_CHARS) -> Gtk.Button:
    child = button.get_child()
    if isinstance(child, Gtk.Label):
        _fit_exact(child, child.get_text(), chars)
    return button


def label(text: str, cls: str, xalign: float = 0.0) -> Gtk.Label:
    widget = Gtk.Label(label=text)
    widget.add_css_class(cls)
    widget.set_xalign(xalign)
    if cls in _BIG_LABEL_CLASSES:
        _fit_single(widget, _BIG_LABEL_CHARS)
    else:
        _fit_single(widget, _STANDALONE_CHARS)
    return widget


def caption(text: str) -> Gtk.Label:
    return label(text.upper(), 'xx-caption')


def wrap_label(text: str, cls: str = 'xx-secondary',
               xalign: float = 0.0) -> Gtk.Label:
    """Multi-line label: word-char wrap, never ellipsized (an ellipsized
    wrapped label collapses to one truncated line)."""
    widget = Gtk.Label(label=text)
    widget.add_css_class(cls)
    widget.set_xalign(xalign)
    _fit_wrap(widget)
    return widget


def icon_image(name: str, px: int = BAR_ICON_PX) -> Gtk.Image:
    image = Gtk.Image.new_from_icon_name(name)
    image.set_pixel_size(px)
    return image


def _icon_button(name: str, callback=None,
                 px: int = BAR_ICON_PX) -> Gtk.Button:
    button = Gtk.Button()
    button.add_css_class('xx-btn')
    button.add_css_class('xx-btn-icon')
    button.set_child(icon_image(name, px))
    if callback is not None:
        button.connect('clicked', lambda _b: callback())
    return button


def page_header(title: str, caption_text: str | None = None,
                actions: list | None = None,
                overflow_cb=None,
                icon_name: str | None = None,
                icon_cb=None) -> Gtk.Box:
    """Big display title at the gutter, optional caption, trailing text
    actions (no chrome), an optional ``icon_name`` symbolic action and an
    optional overflow menu button (view-more-symbolic)."""
    root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    root.add_css_class('xx-page-body')
    top = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    top.add_css_class('xx-page-top')
    top.set_valign(Gtk.Align.CENTER)
    title_label = label(title, 'xx-display')
    _fit_single(title_label, _HEADER_TITLE_CHARS)
    title_label.set_hexpand(True)
    title_label.set_valign(Gtk.Align.CENTER)
    top.append(title_label)
    for action_label, callback in actions or []:
        button = Gtk.Button.new_with_label(action_label)
        button.add_css_class('xx-btn')
        button.add_css_class('xx-btn-text')
        button.set_valign(Gtk.Align.CENTER)
        _fit_button_text(button)
        if callback is not None:
            button.connect('clicked', lambda _b, cb=callback: cb())
        top.append(button)
    if icon_name is not None:
        trailing = _icon_button(icon_name, icon_cb)
        trailing.set_valign(Gtk.Align.CENTER)
        top.append(trailing)
    if overflow_cb is not None:
        more = _icon_button('view-more-symbolic', overflow_cb)
        more.set_valign(Gtk.Align.CENTER)
        top.append(more)
    root.append(top)
    if caption_text:
        sub = caption(caption_text)
        _fit_single(sub, _STANDALONE_CHARS)
        root.append(sub)
    return root


def compact_header(back_cb, title: str, subtitle: str = '',
                   icon_name: str = '', icon_cb=None,
                   second_icon_name: str = '',
                   second_icon_cb=None) -> Gtk.Box:
    """Sub-page header: 56 px bar, back chevron, caption-style title,
    up to two trailing symbolic icons. Everything vertically centered."""
    root = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    root.add_css_class('xx-page-body')
    root.add_css_class('xx-compact-header')
    back = _icon_button('go-previous-symbolic', back_cb)
    back.set_valign(Gtk.Align.CENTER)
    root.append(back)
    middle = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    middle.set_hexpand(True)
    middle.set_valign(Gtk.Align.CENTER)
    title_label = caption(title)
    _fit_single(title_label, _ROW_CHARS)
    middle.append(title_label)
    sub = label(subtitle, 'xx-secondary')
    _fit_single(sub, _ROW_CHARS)
    sub.set_visible(bool(subtitle))
    middle.append(sub)
    root.append(middle)
    if second_icon_name:
        second = _icon_button(second_icon_name, second_icon_cb)
        second.set_valign(Gtk.Align.CENTER)
        root.append(second)
    if icon_name:
        button = _icon_button(icon_name, icon_cb)
        button.set_valign(Gtk.Align.CENTER)
        root.append(button)
    return root


def chevron(px: int = BAR_ICON_PX) -> Gtk.Image:
    """Muted trailing disclosure chevron for navigation rows."""
    return icon_image('go-next-symbolic', px)


def count_badge(text: str) -> Gtk.Label:
    """Small inverted unread-count pill for list-row trailing meta."""
    widget = Gtk.Label(label=text)
    widget.add_css_class('xx-count')
    widget.set_xalign(1.0)
    widget.set_valign(Gtk.Align.CENTER)
    _fit_exact(widget, text, _META_CHARS)
    return widget


def avatar(initials: str, size: int = 40, dashed: bool = False) -> Gtk.Label:
    disc = Gtk.Label(label=initials)
    disc.add_css_class('xx-avatar')
    if dashed:
        disc.add_css_class('dashed')
    disc.set_size_request(size, size)
    disc.set_xalign(0.5)
    disc.set_valign(Gtk.Align.CENTER)
    _fit_exact(disc, initials, _AVATAR_CHARS)
    return disc


def list_row(primary: str, secondary: str = '', meta: str = '',
             leading=None, trailing=None, on_activate=None,
             last: bool = False, danger_sub: bool = False,
             wrap_secondary: bool = False) -> Gtk.Button:
    """Activatable 64 px row with hairline (``last`` drops it).
    ``wrap_secondary`` lets the second line wrap (a forecast summary at
    large text) instead of ellipsizing."""
    row = Gtk.Button()
    row.add_css_class('xx-row')
    if last:
        row.add_css_class('last')
    box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
    box.set_valign(Gtk.Align.CENTER)
    if leading is not None:
        leading.set_valign(Gtk.Align.CENTER)
        box.append(leading)
    middle = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
    middle.set_hexpand(True)
    middle.set_valign(Gtk.Align.CENTER)
    primary_label = label(primary, 'xx-title')
    _fit_single(primary_label, _ROW_CHARS)
    middle.append(primary_label)
    if secondary:
        sub = label(secondary, 'xx-secondary')
        if wrap_secondary:
            _fit_wrap(sub)
        else:
            _fit_single(sub, _ROW_CHARS)
        if danger_sub:
            sub.add_css_class('xx-danger-text')
        middle.append(sub)
    box.append(middle)
    if meta:
        meta_label = label(meta, 'xx-time', xalign=1.0)
        _fit_single(meta_label, _META_CHARS)
        meta_label.set_valign(Gtk.Align.CENTER)
        box.append(meta_label)
    if trailing is not None:
        trailing.set_valign(Gtk.Align.CENTER)
        box.append(trailing)
    row.set_child(box)
    if on_activate is not None:
        row.connect('clicked', lambda _b: on_activate())
    return row


def chip_bar(items: list, spacing: int = 8) -> Gtk.ScrolledWindow:
    """Scrollable row of filter chips. ``items``: (label, selected, cb)."""
    box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, spacing)
    for text, selected, callback in items:
        chip = Gtk.Button.new_with_label(text.upper())
        chip.add_css_class('xx-chip')
        if selected:
            chip.add_css_class('selected')
        _fit_button_text(chip, _CHIP_CHARS)
        if callback is not None:
            chip.connect('clicked', lambda _b, cb=callback: cb())
        box.append(chip)
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
    scrolled.set_child(box)
    return scrolled


def segmented(items: list) -> Gtk.Box:
    """Joined caps chips. ``items``: (label, selected, cb)."""
    root = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    root.add_css_class('xx-segmented')
    count = len(items)
    for index, (text, selected, callback) in enumerate(items):
        button = Gtk.Button.new_with_label(text.upper())
        if index == 0:
            button.add_css_class('seg-first')
        elif index == count - 1:
            button.add_css_class('seg-last')
        else:
            button.add_css_class('seg-mid')
        if selected:
            button.add_css_class('seg-selected')
        wrap_to_fit(button)
        if callback is not None:
            button.connect('clicked', lambda _b, cb=callback: cb())
        button.set_hexpand(True)
        root.append(button)
    return root


def bottom_tabs(items: list) -> Gtk.Box:
    """4 equal tabs, indicator line on top of the active one."""
    root = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    root.add_css_class('xx-tabs')
    root.set_homogeneous(True)
    for _glyph, text, active, callback in items:
        tab = Gtk.Button.new_with_label(text.upper())
        tab.add_css_class('xx-tab')
        if active:
            tab.add_css_class('active')
        _fit_button_text(tab, _TAB_CHARS)
        if callback is not None:
            tab.connect('clicked', lambda _b, cb=callback: cb())
        root.append(tab)
    return root


def search_bar(placeholder: str = 'Search', boxed: bool = False,
               on_changed=None) -> Gtk.Box:
    root = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    root.add_css_class('xx-searchbar-boxed' if boxed else 'xx-searchbar')
    if not boxed:
        glyph = icon_image('system-search-symbolic')
        glyph.set_valign(Gtk.Align.CENTER)
        root.append(glyph)
    entry = Gtk.Entry()
    entry.set_placeholder_text(placeholder)
    entry.set_max_width_chars(_ENTRY_CHARS)
    entry.set_hexpand(True)
    entry.set_valign(Gtk.Align.CENTER)
    if on_changed is not None:
        entry.connect('changed', lambda _e: on_changed(entry.get_text()))
    root.append(entry)
    return root


def _button(text: str, classes: list, callback=None,
            chars: int = _BUTTON_CHARS) -> Gtk.Button:
    button = Gtk.Button.new_with_label(text)
    for cls in classes:
        button.add_css_class(cls)
    _fit_button_text(button, chars)
    if callback is not None:
        button.connect('clicked', lambda _b: callback())
    return button


def _icon_text_button(icon_name: str, text: str, classes: list,
                      callback=None) -> Gtk.Button:
    button = Gtk.Button()
    for cls in classes:
        button.add_css_class(cls)
    row = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    row.set_halign(Gtk.Align.CENTER)
    row.set_valign(Gtk.Align.CENTER)
    row.append(icon_image(icon_name, BUTTON_ICON_PX))
    text_label = Gtk.Label(label=text)
    _fit_exact(text_label, text, _BUTTON_CHARS)
    row.append(text_label)
    button.set_child(row)
    if callback is not None:
        button.connect('clicked', lambda _b: callback())
    return button


def outline_button(text: str, callback=None) -> Gtk.Button:
    return _button(text, ['xx-btn', 'xx-btn-outline'], callback)


def primary_button(text: str, callback=None) -> Gtk.Button:
    return _button(text, ['xx-btn', 'xx-btn-primary'], callback)


def danger_button(text: str, callback=None) -> Gtk.Button:
    return _button(text, ['xx-btn', 'xx-btn-danger'], callback)


def text_button(text: str, callback=None) -> Gtk.Button:
    return _button(text, ['xx-btn', 'xx-btn-text'], callback)


def caps_button(text: str, callback=None) -> Gtk.Button:
    return _button(text.upper(), ['xx-btn-caps'], callback,
                   _WIDE_BUTTON_CHARS)


def icon_button(icon_name: str, callback=None) -> Gtk.Button:
    return _icon_button(icon_name, callback)


def call_button(text: str = 'Call', callback=None) -> Gtk.Button:
    return _icon_text_button('call-start-symbolic', text,
                             ['xx-btn', 'xx-btn-primary'], callback)


def fab(callback=None,
        icon_name: str = 'list-add-symbolic') -> Gtk.Button:
    button = _icon_button(icon_name, callback, BUTTON_ICON_PX)
    button.add_css_class('xx-fab')
    button.set_size_request(56, 56)
    button.set_halign(Gtk.Align.END)
    button.set_valign(Gtk.Align.END)
    button.set_margin_end(20)
    return button


def field(caption_text: str, value: str, placeholder: bool = False) -> Gtk.Box:
    root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    root.append(caption(caption_text))
    shown = label(value, 'xx-field-value')
    if placeholder:
        shown.add_css_class('placeholder')
    root.append(shown)
    return root


def section_label(text: str) -> Gtk.Label:
    widget = caption(text)
    widget.add_css_class('xx-section-label')
    return widget


def card() -> Gtk.Box:
    root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
    root.add_css_class('xx-card')
    return root


def setting_row(title: str, subtitle: str = '', trailing=None,
                last: bool = False) -> Gtk.Box:
    root = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 12)
    root.add_css_class('xx-setting')
    if last:
        root.add_css_class('last')
    middle = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
    middle.set_hexpand(True)
    middle.set_valign(Gtk.Align.CENTER)
    title_label = label(title, 'xx-title')
    _fit_single(title_label, _ROW_CHARS)
    middle.append(title_label)
    if subtitle:
        sub = label(subtitle, 'xx-secondary')
        _fit_single(sub, _ROW_CHARS)
        middle.append(sub)
    root.append(middle)
    if trailing is not None:
        trailing.set_valign(Gtk.Align.CENTER)
        root.append(trailing)
    return root


def make_switch(active: bool = False, callback=None) -> Gtk.Switch:
    """Themed switch. The shared ``switch`` rule pads the drawn pill with a
    transparent border, so the tap target is 48 px tall and wide while
    the pill stays its usual size."""
    switch = Gtk.Switch()
    switch.add_css_class('xx-switch')
    switch.set_active(active)
    switch.set_valign(Gtk.Align.CENTER)
    if callback is not None:
        switch.connect('notify::active',
                       lambda _s, _p: callback(switch.get_active()))
    return switch


def empty_state(text: str, action_label: str | None = None,
                callback=None) -> Gtk.Box:
    root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)
    prompt = label(text, 'xx-empty', xalign=0.5)
    _fit_wrap(prompt)
    prompt.set_halign(Gtk.Align.CENTER)
    root.append(prompt)
    if action_label:
        action = outline_button(action_label, callback)
        # Full-width: a centered button is allocated its minimum, which
        # ellipsizes its own label.
        action.set_hexpand(True)
        root.append(action)
    return root


def keypad_key(digit: str, sub: str = '', callback=None) -> Gtk.Button:
    key = Gtk.Button()
    key.add_css_class('xx-key')
    box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    box.set_valign(Gtk.Align.CENTER)
    digit_label = label(digit, 'xx-key-digit', xalign=0.5)
    _fit_exact(digit_label, digit, _KEY_DIGIT_CHARS)
    box.append(digit_label)
    if sub:
        sub_label = label(sub.upper(), 'xx-key-sub', xalign=0.5)
        _fit_exact(sub_label, sub, _KEY_SUB_CHARS)
        box.append(sub_label)
    key.set_child(box)
    if callback is not None:
        key.connect('clicked', lambda _b: callback(digit))
    return key


def call_action(text: str, active: bool = False, callback=None) -> Gtk.Button:
    classes = ['xx-callaction']
    if active:
        classes.append('active')
    return _button(text.upper(), classes, callback)


def end_call_button(callback=None) -> Gtk.Button:
    return _icon_text_button('call-start-symbolic', 'End call',
                             ['xx-endcall'], callback)


# Status tiles sit 4-up in a homogeneous grid: 10 narrow chars keep four
# columns inside 336 px of content (an SSID longer than that ellipsizes).
_TILE_LABEL_CHARS = 10
_TILE_STATE_CHARS = 10


def status_tile(label_text: str, state: str = '',
                active: bool = False, on_toggle=None) -> Gtk.ToggleButton:
    """Two-line caps toggle tile, inverted while on.

    ``label_text`` is uppercased in code; ``state`` (SSID, On/Off) keeps
    its own casing and hides its line while empty. ``on_toggle`` gets
    the button on every user toggle. The ``active`` class always tracks
    the switch state so the inverted fill never goes stale.
    """
    button = Gtk.ToggleButton()
    button.add_css_class('xx-tile')
    box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 2)
    box.set_halign(Gtk.Align.CENTER)
    box.set_valign(Gtk.Align.CENTER)
    shown = label_text.upper()
    name = Gtk.Label(label=shown)
    name.add_css_class('xx-tile-label')
    _fit_exact(name, shown, _TILE_LABEL_CHARS)
    box.append(name)
    sub = Gtk.Label(label=state)
    sub.add_css_class('xx-tile-state')
    _fit_exact(sub, state, _TILE_STATE_CHARS)
    sub.set_visible(bool(state))
    box.append(sub)
    button.set_child(box)
    # State first, listeners after: the initial value never fires toggled.
    if active:
        button.set_active(True)
        button.add_css_class('active')

    def _sync(*_args) -> None:
        if button.get_active():
            button.add_css_class('active')
        else:
            button.remove_css_class('active')

    button.connect('toggled', _sync)
    if on_toggle is not None:
        button.connect('toggled', lambda _b: on_toggle(button))
    return button


def status_tile_state(button: Gtk.ToggleButton) -> Gtk.Label:
    """The state line of a ``status_tile`` (for live text updates)."""
    return button.get_child().get_last_child()


# Glyph tiles sit 4-up across 360 px: 8 caps chars is the label budget
# (AIRPLANE, LOCATION); a longer state line (SSID) ellipsizes.
_GLYPH_TILE_LABEL_CHARS = 8
_GLYPH_TILE_STATE_CHARS = 10
GLYPH_TILE_ICON_PX = 20


def glyph_tile(icon_name: str, label_text: str, state: str = '',
               active: bool = False, on_toggle=None) -> Gtk.ToggleButton:
    """Quick-settings tile: symbolic glyph top-left, caps label and a
    state line bottom-left, inverted (foreground block) while on.

    Two extra looks ride on CSS classes the caller toggles: ``working``
    (dashed border while a toggle finishes off-thread) and ``attention``
    (a dot top-right, state line at text weight: the tile wants the
    user, e.g. a sign-in). ``active`` tracks the switch like
    ``status_tile``; ``glyph_tile_state`` reaches the state line.
    """
    button = Gtk.ToggleButton()
    button.add_css_class('xx-gtile')
    # The state block's vexpand pins it to the bottom edge; stop it from
    # propagating so a tall window never stretches the tile rows.
    button.set_vexpand(False)
    body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    top = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 0)
    top.set_valign(Gtk.Align.START)
    glyph = icon_image(icon_name, GLYPH_TILE_ICON_PX)
    glyph.add_css_class('xx-gtile-glyph')
    glyph.set_halign(Gtk.Align.START)
    glyph.set_hexpand(True)
    top.append(glyph)
    dot = Gtk.Box()
    dot.add_css_class('xx-gtile-dot')
    dot.set_valign(Gtk.Align.START)
    dot.set_halign(Gtk.Align.END)
    top.append(dot)
    body.append(top)
    text = Gtk.Box.new(Gtk.Orientation.VERTICAL, 3)
    text.set_vexpand(True)
    text.set_valign(Gtk.Align.END)
    shown = label_text.upper()
    name = Gtk.Label(label=shown, xalign=0.0)
    name.add_css_class('xx-gtile-label')
    _fit_single(name, _GLYPH_TILE_LABEL_CHARS)
    text.append(name)
    sub = Gtk.Label(label=state, xalign=0.0)
    sub.add_css_class('xx-gtile-state')
    _fit_single(sub, _GLYPH_TILE_STATE_CHARS)
    text.append(sub)
    body.append(text)
    button.set_child(body)
    if active:
        button.set_active(True)
        button.add_css_class('active')

    def _sync(*_args) -> None:
        if button.get_active():
            button.add_css_class('active')
        else:
            button.remove_css_class('active')

    button.connect('toggled', _sync)
    if on_toggle is not None:
        button.connect('toggled', lambda _b: on_toggle(button))
    return button


def glyph_tile_state(button: Gtk.ToggleButton) -> Gtk.Label:
    """The state line of a ``glyph_tile`` (for live text updates)."""
    return button.get_child().get_last_child().get_last_child()


def bubble(text: str, outgoing: bool = True) -> Gtk.Label:
    widget = label(text, 'xx-bubble-out' if outgoing else 'xx-bubble-in')
    _fit_wrap(widget)
    if outgoing:
        widget.set_halign(Gtk.Align.END)
    return widget


def day_separator(text: str) -> Gtk.Box:
    root = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    root.add_css_class('xx-day-sep-box')
    root.set_halign(Gtk.Align.FILL)
    root.set_valign(Gtk.Align.CENTER)
    before = Gtk.Separator.new(Gtk.Orientation.HORIZONTAL)
    before.set_hexpand(True)
    before.set_valign(Gtk.Align.CENTER)
    root.append(before)
    tag = caption(text)
    tag.set_xalign(0.5)
    tag.set_valign(Gtk.Align.CENTER)
    _fit_exact(tag, tag.get_text(), _STANDALONE_CHARS)
    root.append(tag)
    after = Gtk.Separator.new(Gtk.Orientation.HORIZONTAL)
    after.set_hexpand(True)
    after.set_valign(Gtk.Align.CENTER)
    root.append(after)
    return root


def dialog_box(title: str) -> Gtk.Box:
    root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)
    root.add_css_class('xx-dialog')
    title_label = label(title, 'xx-dialog-title')
    _fit_single(title_label, _ROW_CHARS)
    root.append(title_label)
    return root


def menu_row(text: str, callback=None) -> Gtk.Button:
    return _button(text, ['xx-menu-row'], callback, _WIDE_BUTTON_CHARS)


def text_entry(placeholder: str = '') -> Gtk.Entry:
    entry = Gtk.Entry()
    entry.add_css_class('xx-entry')
    entry.set_placeholder_text(placeholder)
    entry.set_max_width_chars(_ENTRY_CHARS)
    return entry


def deferred_entry(placeholder: str = '') -> Gtk.Entry:
    """Entry that stays unfocusable until tapped.

    A window focuses its first focusable widget on activation, which pops
    the on-screen keyboard on a phone. The CAPTURE-phase tap enables and
    focuses the entry for the same tap, so typing still starts at once.
    """
    entry = text_entry(placeholder)
    entry.set_can_focus(False)
    tap = Gtk.GestureClick.new()
    tap.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

    def _enable(*_args) -> None:
        entry.set_can_focus(True)
        entry.grab_focus()

    tap.connect('pressed', _enable)
    entry.add_controller(tap)
    return entry


def page(header, body, bottom=None, fab_widget=None) -> Gtk.Box:
    """Assemble a phone page: fixed header, scrollable body, fixed bottom
    bar. The body scrolls vertically (never horizontally); ``fab_widget``
    overlays the scroll area so it sits above ``bottom`` and never
    overlaps it."""
    root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    root.add_css_class('xx-page')
    root.append(header)
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scrolled.set_hexpand(True)
    scrolled.set_vexpand(True)
    body.set_hexpand(True)
    body.add_css_class('xx-page-body')  # the 20 px gutter is the page's job
    scrolled.set_child(body)
    if fab_widget is not None:
        fab_widget.set_margin_bottom(20)
        area = Gtk.Overlay()
        area.set_vexpand(True)
        area.set_child(scrolled)
        area.add_overlay(fab_widget)
        root.append(area)
    else:
        root.append(scrolled)
    if bottom is not None:
        root.append(bottom)
    return root


# ---- about & open-source licenses ----------------------------------------

#: Where installed distributions keep per-package license texts (Arch,
#: pmOS/Alpine); absent elsewhere, where the SPDX id + URL shows instead.
LICENSES_ROOT = '/usr/share/licenses'


@dataclass
class LicenseEntry:
    """One row on the licenses page: the component, its SPDX id, the
    project URL, and the system license directories to try, in order."""
    name: str
    spdx: str
    url: str
    packages: tuple = ()


def standard_license_entries(app_name: str) -> list:
    """The app's own license plus the third-party components every XX
    app uses at runtime: GTK/libadwaita/PyGObject, the ``phonenumbers``
    display engine, the xx_ui fonts and icons. One data table per app;
    each app passes its own name."""
    return [
        LicenseEntry(app_name, 'GPL-3.0-or-later',
                     'See LICENSE in the source tree'),
        LicenseEntry('GTK 4', 'LGPL-2.1-or-later',
                     'https://www.gtk.org/',
                     ('gtk4', 'gtk4-layer-shell')),
        LicenseEntry('libadwaita', 'LGPL-2.1-or-later',
                     'https://gnome.pages.gitlab.gnome.org/libadwaita/',
                     ('libadwaita',)),
        LicenseEntry('PyGObject', 'LGPL-2.1-or-later',
                     'https://pygobject.gnome.org/',
                     ('python-gobject',)),
        LicenseEntry('phonenumbers', 'Apache-2.0',
                     'https://github.com/daviddrysdale/python-phonenumbers',
                     ('python-phonenumbers', 'py3-phonenumbers')),
        LicenseEntry('JetBrains Mono / Space Mono', 'OFL-1.1',
                     'https://www.jetbrains.com/lp/mono/',
                     ('ttf-jetbrains-mono', 'ttf-space-mono',
                      'adwaita-fonts')),
        LicenseEntry('Nerd Fonts glyphs', 'MIT',
                     'https://www.nerdfonts.com/',
                     ('nerd-fonts', 'ttf-nerd-fonts-symbols')),
        LicenseEntry('Adwaita symbolic icons', 'CC-BY-SA-3.0',
                     'https://gitlab.gnome.org/GNOME/adwaita-icon-theme',
                     ('adwaita-icon-theme',)),
    ]


def license_text(entry: LicenseEntry,
                 licenses_root: str = LICENSES_ROOT) -> str:
    """The license text for ``entry``: the system copy when present,
    else the SPDX id and the project URL."""
    candidates = [os.path.join(licenses_root, 'spdx', entry.spdx + '.txt')]
    for package in entry.packages:
        try:
            names = sorted(os.listdir(os.path.join(licenses_root,
                                                   package)))
        except OSError:
            continue
        for name in names:
            if name.lower() == 'copyright':
                continue
            candidates.append(os.path.join(licenses_root, package, name))
    for path in candidates:
        try:
            with open(path, encoding='utf-8', errors='replace') as handle:
                text = handle.read().strip()
        except OSError:
            continue
        if text:
            return text
    return '%s\n%s' % (entry.spdx, entry.url)


def _adw():
    """libadwaita, loaded only by the licenses pages that need it: importing
    it initializes Adw and restyles every widget in apps that dropped it."""
    gi.require_version('Adw', '1')
    from gi.repository import Adw
    return Adw


def _license_text_page(entry: LicenseEntry,
                       licenses_root: str = LICENSES_ROOT):
    """A navigation page showing one license text (scrollable)."""
    content = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_hexpand(True)
    scroll.set_vexpand(True)
    view = Gtk.TextView()
    view.set_editable(False)
    view.set_cursor_visible(False)
    view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    view.get_buffer().set_text(license_text(entry, licenses_root))
    scroll.set_child(view)
    content.append(scroll)
    sheet = _adw().NavigationPage.new(content, entry.name)
    return sheet


def licenses_page(navigation, app_name: str,
                  entries: list,
                  licenses_root: str = LICENSES_ROOT):
    """An 'Open-source licenses' page: one row per component showing
    its SPDX id; tapping a row pushes its license text. No trailing
    chevrons: the rows read as tappable on their own."""
    body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
    for entry in entries:
        row = list_row(entry.name, entry.spdx,
                       on_activate=(lambda item=entry: navigation.push(
                           _license_text_page(item, licenses_root))))
        body.append(row)
    header = compact_header(navigation.pop, 'Open-source licenses',
                            app_name)
    sheet = _adw().NavigationPage.new(page(header, body),
                                   'Open-source licenses')
    return sheet


def about_content(app_name: str, version: str, entries: list,
                  on_licenses, section: bool = True) -> Gtk.Box:
    """An 'About' section body: the app name and version, the app
    license line, and an 'Open-source licenses' row. ``section=False``
    drops the 'About' label, for a page whose header already says it."""
    root = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
    if section:
        root.append(section_label('About'))
    root.append(setting_row(app_name, 'Version %s' % version))
    spdx = entries[0].spdx if entries else ''
    root.append(setting_row('License', spdx))
    # No trailing chevron: the row reads as tappable on its own, and the
    # suite carries no decorative glyphs.
    root.append(list_row('Open-source licenses',
                         '%d components' % len(entries),
                         on_activate=on_licenses, last=True))
    return root


def show_licenses_dialog(parent: Gtk.Window, app_name: str,
                         entries: list,
                         licenses_root: str = LICENSES_ROOT) -> None:
    """The licenses page as a dialog (for pages without a
    NavigationView): rows open each license text in place."""
    dialog = Gtk.Dialog(title='Open-source licenses - %s' % app_name,
                        transient_for=parent, modal=True)
    dialog.add_css_class('xx-dialog')
    area = dialog.get_content_area()
    area.set_spacing(8)
    stack = Gtk.Stack()
    stack.set_vexpand(True)
    area.append(stack)
    listing = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_vexpand(True)
    scroll.set_child(listing)
    stack.add_named(scroll, 'listing')
    for entry in entries:
        sheet = _license_text_page(entry, licenses_root)
        stack.add_named(sheet.get_child(), entry.name)
        listing.append(list_row(
            entry.name, entry.spdx,
            on_activate=(lambda name=entry.name:
                         stack.set_visible_child_name(name))))
    back = Gtk.Button.new_with_label('Back')
    back.connect('clicked',
                 lambda _b: stack.set_visible_child_name('listing'))
    close = Gtk.Button.new_with_label('Close')
    close.connect('clicked', lambda _b: dialog.close())
    actions = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
    actions.set_halign(Gtk.Align.END)
    actions.append(back)
    actions.append(close)
    area.append(actions)
    dialog.set_default_size(340, 600)
    dialog.present()
