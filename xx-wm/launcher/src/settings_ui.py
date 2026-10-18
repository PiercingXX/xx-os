"""Settings row grammar: the one way every settings section draws.

A settings subpage is a column of groups: a caps caption, a rounded
``surface`` card of rows split by hairlines, and at most one muted
footnote under the card. A row is a title, an optional muted subtitle
and one trailing control (a switch, a value + chevron, or a small
inline action). Live status goes in a row's subtitle; values that need
typing open a sheet (``form_sheet``) instead of sitting inline.

Sizes are settings-scoped (``css``): row titles 18 px, subtitles 14 px,
captions 12 px at 360 px wide (the Android xx-launcher settings sizes),
all multiplied by text_size_scale. The shared xx_ui base tokens that
every other surface uses stay untouched.
"""
from __future__ import annotations

# Settings-only type sizes (px at scale 1.0) and card geometry.
TITLE_PX = 18
SUBTITLE_PX = 14
CAPTION_PX = 12
HEADER_PX = 26
BODY_PX = 17
DIALOG_PX = 21
BAR_TITLE_PX = 18
INFO_PX = 14
_WRAP_CHARS = 18
_SHEET_FIELDS_UNSCROLLED = 3
_SHEET_MAX_HEIGHT = 340


def css(theme, scale: float) -> str:
    """Settings-scoped sheet for the window provider (pure, GTK-free)."""
    def px(base: float) -> str:
        return '%dpx' % round(base * scale)

    return """
.xx-settings .xx-title {{ font-size: {title}; }}
.xx-settings .xx-secondary {{ font-size: {sub}; }}
.xx-settings .xx-caption {{ font-size: {cap}; letter-spacing: 0.14em; }}
.xx-settings .xx-set-header {{ font-size: {header}; color: {fg}; }}
.xx-settings .xx-set-bar-title {{ font-size: {bar}; color: {fg}; }}
.xx-set-bar {{ min-height: 56px; padding: 4px 8px 0 4px; }}
.xx-set-body {{ padding: 4px 20px 32px 20px; }}
.xx-set-header {{ margin: 8px 0 8px 0; }}
.xx-set-caption {{ margin: 24px 16px 8px 16px; }}
.xx-set-card {{
    background-color: {surface};
    border-radius: 16px;
}}
.xx-set-card > .xx-set-row {{
    min-height: 40px;
    padding: 14px 16px;
    border-bottom: 1px solid {border};
    border-radius: 0;
}}
.xx-set-card > .xx-set-row:last-child {{ border-bottom: none; }}
.xx-set-card > button.xx-set-row:active,
.xx-set-card .xx-set-press:active {{ background-color: {surface_alt}; }}
.xx-set-card .xx-set-press {{ padding: 0; border-radius: 0; }}
.xx-set-card switch {{ background-color: {border}; }}
.xx-set-card switch:checked {{ background-color: {fg}; }}
.xx-set-card .xx-set-note {{ padding: 20px 16px; }}
.xx-set-card .xx-set-wide {{ padding: 16px; }}
.xx-set-footnote {{ margin: 10px 16px 0 16px; }}
.xx-set-primary {{ margin-top: 24px; }}
.xx-set-sheet {{ min-width: 280px; }}
.xx-set-sheet .xx-caption {{ margin-top: 8px; }}
.xx-set-sheet entry.xx-entry {{ background-color: {bg}; }}
.xx-settings .xx-dialog-title {{ font-size: {dialog}; }}
.xx-settings .xx-menu-row {{
    font-size: {body};
    font-weight: 400;
    min-height: 52px;
    padding: 0 8px;
}}
.xx-settings .xx-set-big {{ font-size: {header}; color: {fg}; }}
.xx-settings .xx-set-info {{ font-size: {info}; line-height: 1.25; }}
.xx-settings .xx-menu-check {{ font-size: {body}; color: {fg}; }}
.xx-settings .xx-widget-line {{ min-height: 88px; }}
.xx-settings .xx-widget-move {{ min-height: 44px; min-width: 48px; padding: 0 8px; }}
.xx-settings list.reorder-list {{ background-color: transparent; }}
.xx-settings list.reorder-list > row {{
    background-color: transparent;
    min-height: 52px;
    border-bottom: 1px solid {border};
}}
.xx-settings list.reorder-list > row:last-child {{ border-bottom: none; }}
.xx-settings list.reorder-list .app-name {{ font-size: {body}; }}
.xx-set-chart-bar {{ background-color: {border}; border-radius: 4px; }}
.xx-set-chart-bar.today {{ background-color: {fg}; }}
""".format(title=px(TITLE_PX), sub=px(SUBTITLE_PX), cap=px(CAPTION_PX),
           header=px(HEADER_PX), bar=px(BAR_TITLE_PX),
           body=px(BODY_PX), dialog=px(DIALOG_PX), info=px(INFO_PX),
           fg=theme.foreground, bg=theme.background, surface=theme.surface,
           surface_alt=theme.surface_alt, border=theme.border)


def _wrap(label) -> None:
    from gi.repository import Pango

    label.set_wrap(True)
    label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    label.set_ellipsize(Pango.EllipsizeMode.NONE)
    label.set_max_width_chars(_WRAP_CHARS)


def _text_column(title: str, subtitle: str):
    from gi.repository import Gtk
    from xx_ui import widgets

    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    column.set_hexpand(True)
    column.set_valign(Gtk.Align.CENTER)
    title_label = widgets.label(title, 'xx-title')
    _wrap(title_label)
    sub = widgets.label(subtitle, 'xx-secondary')
    _wrap(sub)
    sub.set_visible(bool(subtitle))
    column.append(title_label)
    column.append(sub)
    return column, title_label, sub


def row(title: str, subtitle: str = '', trailing=None, on_activate=None):
    """One settings row. ``on_activate(row)`` makes it tappable; with a
    switch trailing, only the text side is tappable (split row) so the
    switch keeps its own gesture. ``row.title_label`` and
    ``row.subtitle_label`` stay addressable for live updates."""
    from gi.repository import Gtk

    column, title_label, sub = _text_column(title, subtitle)
    split = on_activate is not None and isinstance(trailing, Gtk.Switch)
    if on_activate is None or split:
        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        if split:
            press = Gtk.Button()
            press.add_css_class('xx-set-press')
            press.set_hexpand(True)
            press.set_child(column)
            press.connect('clicked', lambda _b: on_activate(root))
            root.append(press)
        else:
            root.append(column)
    else:
        root = Gtk.Button()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.append(column)
        root.set_child(box)
        root.connect('clicked', lambda _b: on_activate(root))
    root.add_css_class('xx-set-row')
    if trailing is not None:
        trailing.set_valign(Gtk.Align.CENTER)
        (root.get_child() if isinstance(root, Gtk.Button) else root).append(
            trailing)
    root.title_label = title_label
    root.subtitle_label = sub
    return root


def nav_row(title: str, subtitle: str = '', on_activate=None):
    """Value or secondary-action row: subtitle + trailing chevron."""
    from xx_ui import widgets

    return row(title, subtitle, widgets.chevron(), on_activate)


def switch_row(title: str, subtitle: str = '', active: bool = False,
               on_toggle=None, on_activate=None):
    """Row with a trailing switch; ``row.switch`` is the Gtk.Switch.
    ``on_toggle(active)`` fires on every flip (programmatic ones too:
    callers guard with their own updating flag, as before)."""
    from xx_ui import widgets

    switch = widgets.make_switch(active, on_toggle)
    root = row(title, subtitle, switch, on_activate)
    root.switch = switch
    return root


def action_row(title: str, label: str, callback, subtitle: str = ''):
    """Row with one small caps inline action (Check, Start, Remove…)."""
    from xx_ui import widgets

    button = widgets.caps_button(label)
    root = row(title, subtitle, button)
    button.connect('clicked', lambda _b: callback(root))
    root.action = button
    return root


def note_row(text: str):
    """Muted one-sentence row for an empty or unavailable list."""
    from xx_ui import widgets

    note = widgets.wrap_label(text)
    note.add_css_class('xx-set-row')
    note.add_css_class('xx-set-note')
    return note


def wide_row(child):
    """A card row holding a full-width control (slider, chart, grid)."""
    from gi.repository import Gtk

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.add_css_class('xx-set-row')
    box.add_css_class('xx-set-wide')
    box.append(child)
    return box


def set_subtitle(target, text: str) -> None:
    """Live status update for a row built here (hidden when empty)."""
    label = getattr(target, 'subtitle_label', None)
    if label is None:
        return
    try:
        label.set_text(text or '')
        label.set_visible(bool(text))
    except Exception:
        pass


def set_title(target, text: str) -> None:
    label = getattr(target, 'title_label', None)
    if label is not None:
        try:
            label.set_text(text)
        except Exception:
            pass


def card(rows=()):
    """Rounded surface card; rows are its direct children (hairlines
    come from CSS, none after the last)."""
    from gi.repository import Gtk

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    box.add_css_class('xx-set-card')
    try:
        box.set_overflow(Gtk.Overflow.HIDDEN)
    except Exception:
        pass
    for item in rows:
        box.append(item)
    return box


def fill(target, rows, hide_empty: bool = False) -> None:
    """Replace a card's rows; ``hide_empty`` hides the card (and the
    caption/footnote registered on it) when there is nothing to show."""
    child = target.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        target.remove(child)
        child = nxt
    for item in rows:
        target.append(item)
    if hide_empty:
        visible = bool(rows)
        for widget in [target] + list(getattr(target, 'companions', ())):
            try:
                widget.set_visible(visible)
            except Exception:
                pass


def caption(text: str):
    from xx_ui import widgets

    label = widgets.caption(text)
    label.add_css_class('xx-set-caption')
    return label


def footnote(text: str = ''):
    """Muted help line under a card; hidden while empty."""
    from xx_ui import widgets

    note = widgets.wrap_label(text)
    note.add_css_class('xx-set-footnote')
    note.set_visible(bool(text))
    return note


def say(note, text: str) -> None:
    """Set a footnote's text (hidden when empty)."""
    if note is None:
        return
    try:
        note.set_text(text or '')
        note.set_visible(bool(text))
    except Exception:
        pass


def group(caption_text: str, rows=(), note: str | None = None) -> list:
    """[caption, card(, footnote)] for a section's build() list. The
    card is ``widgets[1]``; it remembers its caption and footnote as
    ``companions`` so ``fill(..., hide_empty=True)`` hides them too."""
    head = caption(caption_text)
    body = card(rows)
    built = [head, body]
    if note is not None:
        built.append(footnote(note))
    body.companions = [head] + built[2:]
    return built


def primary(text: str, callback):
    """The page's one filled action, under its card."""
    from xx_ui import widgets

    button = widgets.primary_button(text, callback)
    button.add_css_class('xx-set-primary')
    return button


# ---- sheets ---------------------------------------------------------------

def _keyboard(win, show: bool) -> None:
    actions = getattr(win, 'item_actions', None)
    callback = getattr(actions, '_on_keyboard', None)
    if callable(callback):
        try:
            callback(show)
        except Exception:
            pass


def form_sheet(win, anchor, title: str, fields, commit_label: str,
               on_commit, note: str = '', extra=None):
    """A dialog sheet: caps-captioned fields and one primary action.

    ``fields``: (key, caption, value, kind) with kind 'text',
    'password', 'pin' or 'number'. ``on_commit(values)`` returns an error
    string to keep the sheet open with that message, or None to close.
    ``extra`` (a widget) sits between the fields and the action.
    Returns (box, entries) so callers and tests can drive it.
    """
    from gi.repository import Gtk
    from xx_ui import widgets

    box = widgets.dialog_box(title)
    box.add_css_class('xx-set-sheet')
    column = box
    if len(fields) > _SHEET_FIELDS_UNSCROLLED:
        # Many fields scroll above a primary action that stays in reach.
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_propagate_natural_height(True)
        scroller.set_max_content_height(_SHEET_MAX_HEIGHT)
        scroller.set_child(column)
        box.append(scroller)
    entries: dict = {}
    order = []
    for key, label_text, value, kind in fields:
        column.append(widgets.caption(label_text))
        entry = widgets.deferred_entry('')
        entry.set_text(str(value or ''))
        if kind in ('password', 'pin'):
            entry.set_visibility(False)
            entry.set_input_purpose(Gtk.InputPurpose.PIN if kind == 'pin'
                                    else Gtk.InputPurpose.PASSWORD)
        elif kind == 'number':
            entry.set_input_purpose(Gtk.InputPurpose.NUMBER)
        getattr(win, '_attach_osk', lambda _w: None)(entry)
        entries[key] = entry
        order.append(entry)
        column.append(entry)
    if extra is not None:
        box.append(extra)
    status = widgets.wrap_label(note)
    status.set_visible(bool(note))
    box.append(status)
    shown: dict = {}

    def _commit(*_args) -> None:
        values = {key: entry.get_text() for key, entry in entries.items()}
        error = on_commit(values)
        if error:
            status.set_text(error)
            status.set_visible(True)
            return
        popover = shown.pop('popover', None)
        if popover is not None:
            popover.popdown()

    def _next(nxt) -> None:
        nxt.set_can_focus(True)
        nxt.grab_focus()

    for index, entry in enumerate(order):
        if index + 1 < len(order):
            entry.connect('activate',
                          lambda _e, nxt=order[index + 1]: _next(nxt))
        else:
            entry.connect('activate', _commit)
    box.append(widgets.primary_button(commit_label, _commit))
    actions = getattr(win, 'item_actions', None)
    if actions is not None and anchor is not None:
        popover = actions._show_popover(anchor, box)
        shown['popover'] = popover
        if hasattr(popover, 'connect'):
            popover.connect('closed', lambda _p: _keyboard(win, False))
    return box, entries


def info_sheet(win, anchor, title: str, text: str):
    """A read-only sheet: title, wrapped text and one OK (Android's
    AlertDialog with a message and an OK button)."""
    from xx_ui import widgets

    box = widgets.dialog_box(title)
    box.add_css_class('xx-set-sheet')
    body = widgets.wrap_label(text, 'xx-title')
    body.add_css_class('xx-set-info')
    box.append(body)
    shown: dict = {}

    def _ok() -> None:
        popover = shown.pop('popover', None)
        if popover is not None:
            popover.popdown()

    box.append(widgets.primary_button('OK', _ok))
    actions = getattr(win, 'item_actions', None)
    if actions is not None and anchor is not None:
        shown['popover'] = actions._show_popover(anchor, box)
    return box
