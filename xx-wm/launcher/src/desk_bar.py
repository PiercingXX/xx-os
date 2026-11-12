"""Docked top bar: a thin strip at the top of every monitor that is not
the handset, while xxwm runs the session (river).

The monitor shows this bar and nothing else. Left to right: the
workspaces in use (the one this monitor shows inverted; a click shows
another one here), the title of the window in front on this monitor,
then call state, the notification count, the handset status bar's state
glyphs and battery, and the date and time.

Everything comes from sources the shell already has: xxwm's `subscribe
state` stream for outputs, workspaces and titles; the shell window's 1 s
clock tick, 60 s status poll, privacy monitor, call snapshots and shade
list for the rest (desk_session.py fans them out). Nothing here polls.

Headless-safe: without GTK only the pure helpers exist.
"""
from __future__ import annotations

from datetime import datetime

from status_bar import (
    ICON_NAMES, battery_low, battery_text, right_glyphs, signal_bars,
)

_HAS_GTK = True
try:
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Gtk4LayerShell', '1.0')
    from gi.repository import Gtk, Pango
    from gi.repository import Gtk4LayerShell as LayerShell
except (ImportError, ValueError):
    _HAS_GTK = False

BAR_HEIGHT = 28
WORKSPACES = 10

CALL_TEXT = {
    'incoming': 'Incoming call',
    'outgoing': 'Calling',
    'active': 'On call',
}


# ---- state ------------------------------------------------------------

def output_key(output: dict) -> str:
    """How bars are keyed: the connector name, else the position."""
    name = output.get('name')
    if isinstance(name, str) and name:
        return name
    return f"{output.get('x', 0)},{output.get('y', 0)}"


def desk_outputs(state: dict | None) -> list[dict]:
    """The outputs that get a bar: every one that is not the handset."""
    outputs = (state or {}).get('outputs') or []
    return [o for o in outputs
            if isinstance(o, dict) and not o.get('handset')
            and int(o.get('w') or 0) > 0 and int(o.get('h') or 0) > 0]


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def workspace_items(state: dict | None, output: dict) -> list[tuple[int, bool]]:
    """(workspace, shown here) for the bar on ``output``: the workspaces
    that hold windows plus the one this output shows, in key order
    (1..9, then 10 for the 0 key). A workspace another output is
    showing (the handset's own) belongs to that output's bar, not here."""
    current = _int(output.get('workspace'))
    used = {_int(w.get('workspace')) for w in (state or {}).get('windows') or []
            if isinstance(w, dict)}
    elsewhere = {_int(o.get('workspace')) for o in (state or {}).get('outputs') or []
                 if isinstance(o, dict) and o is not output
                 and output_key(o) != output_key(output)}
    used -= elsewhere
    used.add(current)
    return [(n, n == current) for n in range(1, WORKSPACES + 1) if n in used]


def output_title(output: dict) -> str:
    title = output.get('title')
    return title.strip() if isinstance(title, str) else ''


def match_monitor(monitors: list[tuple], output: dict) -> int | None:
    """Index into ``monitors`` [(connector, x, y, w, h)] for an xxwm
    output: by connector name, else by exact geometry."""
    name = output.get('name')
    if isinstance(name, str) and name:
        for i, monitor in enumerate(monitors):
            if monitor[0] == name:
                return i
    rect = tuple(_int(output.get(k)) for k in ('x', 'y', 'w', 'h'))
    for i, monitor in enumerate(monitors):
        if tuple(_int(v) for v in monitor[1:5]) == rect:
            return i
    return None


def clock_parts(now: datetime, clock: str) -> tuple[str, str]:
    """(`Thu, Nov 12`, ``clock``): the home date format, then the time
    as the shell's clock tick formats it."""
    return now.strftime('%a, %b %d').replace(' 0', ' '), clock


def notification_text(count: int) -> str:
    """The count, or '' when there is nothing to show."""
    count = _int(count)
    return str(count) if count > 0 else ''


def theme_css(preset) -> str:
    """Bar chrome on the brand ramp: page color strip, text at 90% of the
    foreground, muted secondary text, emphasis inverted (the workspace
    shown here, a live call) like the shade's play key and today."""
    bg = preset.background
    fg = preset.foreground
    from config import WARNING_ORANGE
    return f"""
window.xx-deskbar {{
    background: {bg};
}}
.xx-deskbar-root {{
    background: {bg};
    color: alpha({fg}, 0.90);
    padding: 0 10px;
}}
.xx-deskbar-ws {{
    min-width: 22px;
    min-height: 20px;
    border-radius: 4px;
    font-size: 12px;
    font-feature-settings: "tnum";
    color: {preset.muted};
}}
.xx-deskbar-ws.current {{
    background: {fg};
    color: {bg};
}}
.xx-deskbar-title {{
    font-size: 13px;
    color: alpha({fg}, 0.90);
    margin-left: 14px;
}}
.xx-deskbar-call {{
    font-size: 11px;
    letter-spacing: 0.06em;
    padding: 1px 8px;
    border-radius: 4px;
    background: {fg};
    color: {bg};
}}
.xx-deskbar-count {{
    font-size: 11px;
    font-feature-settings: "tnum";
    min-width: 16px;
    padding: 0 3px;
    border-radius: 4px;
    border: 1px solid alpha({fg}, 0.40);
    color: alpha({fg}, 0.90);
}}
.xx-deskbar-battery {{
    font-size: 12px;
    font-feature-settings: "tnum";
    color: alpha({fg}, 0.90);
}}
.xx-deskbar-battery.low {{
    color: {WARNING_ORANGE};
}}
.xx-deskbar-glyph {{
    color: alpha({fg}, 0.80);
    -gtk-icon-size: 14px;
}}
.xx-deskbar-privacy {{
    font-size: 9px;
    color: {preset.accent};
}}
.xx-deskbar-date {{
    font-size: 12px;
    color: {preset.muted};
}}
.xx-deskbar-time {{
    font-size: 13px;
    font-weight: 500;
    font-feature-settings: "tnum";
    color: alpha({fg}, 0.90);
}}
"""


if _HAS_GTK:

    from status_bar import _draw_bars

    def see_through(window) -> None:
        """A transparent window behind an overlay's own panel. The shell's
        theme paints every window the page color, so this sits one step
        above it (as hud.py does)."""
        css = Gtk.CssProvider()
        css.load_from_data(b'window { background: transparent; }')
        window.get_style_context().add_provider(
            css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 10)

    class DeskBarView(Gtk.Box):
        """The strip's content. A plain widget so headless renders can
        compose it without a layer surface."""

        def __init__(self, on_workspace=None) -> None:
            super().__init__()
            self.add_css_class('xx-deskbar-root')
            self.set_hexpand(True)
            self._on_workspace = on_workspace
            self._workspaces: list[tuple[int, bool]] = []
            self._snapshot: dict = {}
            self._dnd = self._focus = self._privacy = False
            self._cell_bars = 0

            left = Gtk.Box(spacing=0)
            left.set_valign(Gtk.Align.CENTER)
            self._ws_box = Gtk.Box(spacing=2)
            left.append(self._ws_box)
            self.title_label = Gtk.Label(xalign=0)
            self.title_label.add_css_class('xx-deskbar-title')
            self.title_label.set_ellipsize(Pango.EllipsizeMode.END)
            self.title_label.set_hexpand(True)
            self.title_label.set_visible(False)
            left.append(self.title_label)
            left.set_hexpand(True)
            self.append(left)

            right = Gtk.Box(spacing=14)
            right.set_valign(Gtk.Align.CENTER)
            right.set_halign(Gtk.Align.END)
            self.append(right)

            self._call = Gtk.Label()
            self._call.add_css_class('xx-deskbar-call')
            self._call.set_visible(False)
            right.append(self._call)
            self._count = Gtk.Label()
            self._count.add_css_class('xx-deskbar-count')
            self._count.set_visible(False)
            right.append(self._count)

            glyphs = Gtk.Box(spacing=7)
            glyphs.set_valign(Gtk.Align.CENTER)
            right.append(glyphs)
            self._privacy_dot = Gtk.Label(label='●')
            self._privacy_dot.add_css_class('xx-deskbar-privacy')
            icons = {}
            for key in ('focus', 'dnd', 'wifi'):
                icon = Gtk.Image.new_from_icon_name(ICON_NAMES[key])
                icon.add_css_class('xx-deskbar-glyph')
                icon.set_pixel_size(14)
                icons[key] = icon
            self._cell = Gtk.DrawingArea()
            self._cell.add_css_class('xx-deskbar-glyph')
            self._cell.set_content_width(13)
            self._cell.set_content_height(11)
            self._cell.set_valign(Gtk.Align.CENTER)
            self._cell.set_draw_func(_draw_bars, self)
            self._battery = Gtk.Label()
            self._battery.add_css_class('xx-deskbar-battery')
            self._glyphs = {
                'privacy': self._privacy_dot, 'focus': icons['focus'],
                'dnd': icons['dnd'], 'wifi': icons['wifi'],
                'cell': self._cell, 'battery': self._battery,
            }
            for key in ('privacy', 'focus', 'dnd', 'wifi', 'cell', 'battery'):
                widget = self._glyphs[key]
                widget.set_visible(False)
                glyphs.append(widget)

            clock = Gtk.Box(spacing=8)
            clock.set_valign(Gtk.Align.CENTER)
            self.date_label = Gtk.Label()
            self.date_label.add_css_class('xx-deskbar-date')
            self.time_label = Gtk.Label()
            self.time_label.add_css_class('xx-deskbar-time')
            clock.append(self.date_label)
            clock.append(self.time_label)
            right.append(clock)

        # -- xxwm ----------------------------------------------------------
        def set_workspaces(self, items: list[tuple[int, bool]]) -> None:
            items = list(items)
            if items == self._workspaces:
                return
            self._workspaces = items
            child = self._ws_box.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                self._ws_box.remove(child)
                child = nxt
            for number, current in items:
                # A label with a click, not a GtkButton: the stock button
                # theme would paint over the inverted current block.
                label = Gtk.Label(label=str(number))
                label.add_css_class('xx-deskbar-ws')
                label.set_valign(Gtk.Align.CENTER)
                if current:
                    label.add_css_class('current')
                click = Gtk.GestureClick()
                click.connect('released', self._on_ws_clicked, number)
                label.add_controller(click)
                self._ws_box.append(label)

        def _on_ws_clicked(self, _gesture, _n, _x, _y, number: int) -> None:
            if callable(self._on_workspace):
                self._on_workspace(number)

        def set_title(self, title: str) -> None:
            title = title or ''
            if self.title_label.get_text() != title:
                self.title_label.set_text(title)
            self.title_label.set_visible(bool(title))

        # -- shell sources ---------------------------------------------------
        def set_clock(self, date_text: str, time_text: str) -> None:
            if self.date_label.get_text() != date_text:
                self.date_label.set_text(date_text)
            if self.time_label.get_text() != time_text:
                self.time_label.set_text(time_text)

        def set_call(self, page: str | None) -> None:
            text = CALL_TEXT.get(page or '', '')
            self._call.set_text(text)
            self._call.set_visible(bool(text))

        def set_notifications(self, count: int) -> None:
            text = notification_text(count)
            self._count.set_text(text)
            self._count.set_visible(bool(text))

        def set_flags(self, dnd: bool, focus: bool) -> None:
            if (bool(dnd), bool(focus)) != (self._dnd, self._focus):
                self._dnd, self._focus = bool(dnd), bool(focus)
                self._render()

        def set_privacy(self, active: bool) -> None:
            if bool(active) != self._privacy:
                self._privacy = bool(active)
                self._render()

        def set_status(self, snapshot: dict | None) -> None:
            self._snapshot = dict(snapshot or {})
            self._render()

        def _render(self) -> None:
            shown = set(right_glyphs(self._snapshot, self._dnd,
                                     self._focus, self._privacy))
            for key, widget in self._glyphs.items():
                widget.set_visible(key in shown)
            self._battery.set_text(battery_text(self._snapshot))
            if battery_low(self._snapshot):
                self._battery.add_css_class('low')
            else:
                self._battery.remove_css_class('low')
            self._cell_bars = signal_bars(self._snapshot.get('signal'))
            self._cell.queue_draw()

    class DeskBar(Gtk.Window):
        """The layer surface for one monitor: Layer.TOP, anchored to the
        top edge, an exclusive zone of its height so xxwm tiles below it,
        never keyboard focus."""

        def __init__(self, monitor, on_workspace=None) -> None:
            super().__init__()
            self.set_decorated(False)
            self.add_css_class('xx-deskbar')
            LayerShell.init_for_window(self)
            LayerShell.set_namespace(self, 'xx-wm-deskbar')
            LayerShell.set_layer(self, LayerShell.Layer.TOP)
            LayerShell.set_monitor(self, monitor)
            for edge in (LayerShell.Edge.TOP, LayerShell.Edge.LEFT,
                         LayerShell.Edge.RIGHT):
                LayerShell.set_anchor(self, edge, True)
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
            LayerShell.set_exclusive_zone(self, BAR_HEIGHT)
            self.set_default_size(-1, BAR_HEIGHT)
            self.set_size_request(-1, BAR_HEIGHT)
            self.monitor = monitor
            self.view = DeskBarView(on_workspace=on_workspace)
            self.set_child(self.view)
