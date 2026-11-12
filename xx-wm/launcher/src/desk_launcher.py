"""Docked launcher: tapping Super (or Alt+Tab) opens the shell's own app
search as a centered prompt on the monitor that has focus.

It is the drawer search, not a second one: the same matches
(drawer_page.search_results: hidden apps, renames, folder members), the
same Launcher Settings and `!` web rows, the same single-result
auto-launch, and the same launch path with its Nope and Focus gates.

Keys: type to search; Enter opens the selected row (the first one until
the selection moves); Down/Up, Tab/Shift+Tab and Ctrl+J/Ctrl+K move the
selection while typing. Once it has moved down, the list has the keys:
j/k move, l opens, h goes back to typing. Escape, a second Super tap or a
click outside closes the prompt.

Headless-safe: without GTK only the pure helpers exist.
"""
from __future__ import annotations

from drawer_page import settings_row_visible, should_auto_launch, web_search_row_query

_HAS_GTK = True
try:
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Gdk', '4.0')
    gi.require_version('Gtk4LayerShell', '1.0')
    from gi.repository import Gdk, GLib, Gtk, Pango
    from gi.repository import Gtk4LayerShell as LayerShell
except (ImportError, ValueError):
    _HAS_GTK = False

PANEL_WIDTH = 560
VISIBLE_ROWS = 8
ROW_HEIGHT = 40

SETTINGS_LABEL = 'Launcher Settings'


def launcher_rows(results: list, raw: str) -> list[tuple[str, object]]:
    """Rows for a typed query, in drawer order: app matches, then the
    Launcher Settings row when it matches, then the web row (a `!` query,
    or nothing matched). Nothing until something is typed."""
    text = raw or ''
    if not text.strip():
        return []
    rows: list[tuple[str, object]] = [('app', entry) for entry in results]
    if settings_row_visible(text) and not text.strip().startswith('!'):
        rows.append(('settings', None))
    web = web_search_row_query(text, len(results))
    if web:
        rows.append(('web', web))
    return rows


def row_label(row: tuple[str, object], label_for=None) -> str:
    kind, value = row
    if kind == 'app':
        name = getattr(value, 'name', '')
        if callable(label_for):
            return label_for(getattr(value, 'app_id', ''), name)
        return name
    if kind == 'settings':
        return SETTINGS_LABEL
    return f'Web: {value}'


def step(index: int, delta: int, count: int) -> int:
    """Move a selection, clamped to the rows (no wrap)."""
    if count <= 0:
        return 0
    return max(0, min(count - 1, index + delta))


# Key → (delta, list-mode only). Ctrl+J/K work while typing; plain j/k
# only once the list has the keys, so they still type otherwise.
def key_move(keyname: str, ctrl: bool, shift: bool, list_mode: bool) -> int | None:
    name = (keyname or '').casefold()
    if name in ('down',) or (name == 'tab' and not shift):
        return 1
    if name in ('up', 'iso_left_tab') or (name == 'tab' and shift):
        return -1
    if ctrl and name == 'j':
        return 1
    if ctrl and name == 'k':
        return -1
    if list_mode and not ctrl and name == 'j':
        return 1
    if list_mode and not ctrl and name == 'k':
        return -1
    return None


def theme_css(preset) -> str:
    bg = preset.background
    fg = preset.foreground
    return f"""
.xx-desklauncher-panel {{
    background: {bg};
    border: 1px solid alpha({fg}, 0.16);
    border-radius: 8px;
    padding: 6px 0 6px 0;
}}
entry.xx-desklauncher-entry,
entry.xx-desklauncher-entry:focus,
entry.xx-desklauncher-entry:focus-within {{
    background: none;
    border: none;
    box-shadow: none;
    outline: none;
    min-height: 44px;
    padding: 0 20px;
    font-size: 16px;
    color: alpha({fg}, 0.90);
    caret-color: alpha({fg}, 0.90);
}}
entry.xx-desklauncher-entry > text > placeholder {{
    color: {preset.muted};
}}
.xx-desklauncher-rule {{
    min-height: 1px;
    margin: 6px 0 6px 0;
    background: alpha({fg}, 0.16);
}}
list.xx-desklauncher-list,
list.xx-desklauncher-list > row {{
    background: none;
    padding: 0;
}}
.xx-desklauncher-row {{
    min-height: {ROW_HEIGHT}px;
    margin: 0 6px;
    padding: 0 14px;
    border-radius: 6px;
}}
.xx-desklauncher-row.selected {{
    background: {fg};
}}
.xx-desklauncher-name {{
    font-size: 14px;
    color: alpha({fg}, 0.90);
}}
.xx-desklauncher-row.selected .xx-desklauncher-name {{
    color: {bg};
}}
.xx-desklauncher-row.paused {{
    opacity: 0.45;
}}
"""


if _HAS_GTK:

    from desk_bar import see_through

    class DeskLauncher(Gtk.Window):
        """Transparent overlay over one monitor holding the centered
        prompt. ``shell`` is the ShellWindow (config, app index, gates)."""

        def __init__(self, shell) -> None:
            super().__init__()
            self._shell = shell
            self._rows: list[tuple[str, object]] = []
            self._selected = 0
            self._list_mode = False
            self.set_decorated(False)
            self.add_css_class('xx-desklauncher')
            see_through(self)
            LayerShell.init_for_window(self)
            LayerShell.set_namespace(self, 'xx-wm-launcher')
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM,
                         LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
                LayerShell.set_anchor(self, edge, True)
            LayerShell.set_exclusive_zone(self, -1)
            LayerShell.set_keyboard_mode(
                self, LayerShell.KeyboardMode.EXCLUSIVE)

            self._panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self._panel.add_css_class('xx-desklauncher-panel')
            self._panel.set_halign(Gtk.Align.CENTER)
            self._panel.set_valign(Gtk.Align.START)
            self._panel.set_size_request(PANEL_WIDTH, -1)

            self.entry = Gtk.Entry()
            self.entry.add_css_class('xx-desklauncher-entry')
            self.entry.set_placeholder_text('Search')
            self.entry.connect('changed', self._on_changed)
            self.entry.connect('activate', lambda _e: self._activate())
            self._panel.append(self.entry)

            self._rule = Gtk.Box()
            self._rule.add_css_class('xx-desklauncher-rule')
            self._rule.set_visible(False)
            self._panel.append(self._rule)

            self._list = Gtk.ListBox()
            self._list.add_css_class('xx-desklauncher-list')
            self._list.set_selection_mode(Gtk.SelectionMode.NONE)
            self._list.set_can_focus(False)
            self._scroller = Gtk.ScrolledWindow()
            self._scroller.set_policy(Gtk.PolicyType.NEVER,
                                      Gtk.PolicyType.AUTOMATIC)
            self._scroller.set_propagate_natural_height(True)
            self._scroller.set_max_content_height(ROW_HEIGHT * VISIBLE_ROWS)
            self._scroller.set_child(self._list)
            self._scroller.set_visible(False)
            self._panel.append(self._scroller)
            self.set_child(self._panel)

            keys = Gtk.EventControllerKey()
            keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            keys.connect('key-pressed', self._on_key)
            self.add_controller(keys)
            click = Gtk.GestureClick()
            click.connect('released', self._on_click)
            self.add_controller(click)

        # -- showing -----------------------------------------------------
        def show_on(self, monitor) -> None:
            geometry = monitor.get_geometry() if monitor is not None else None
            if geometry is not None:
                # Upper third, like a command prompt; never wider than
                # the screen (the handset is 360 px).
                self._panel.set_size_request(
                    min(PANEL_WIDTH, max(200, geometry.width - 32)), -1)
                self._panel.set_margin_top(max(16, geometry.height // 5))
            if monitor is not None:
                LayerShell.set_monitor(self, monitor)
            self.entry.set_text('')
            self._list_mode = False
            self._refresh('')
            self.present()
            self.entry.grab_focus()

        def close_prompt(self) -> None:
            self.set_visible(False)
            self.entry.set_text('')

        # -- rows ----------------------------------------------------------
        def _on_changed(self, entry) -> None:
            text = entry.get_text()
            self._list_mode = False
            self._refresh(text)
            results = [r[1] for r in self._rows if r[0] == 'app']
            if should_auto_launch(text, len(results),
                                  settings_row_visible(text),
                                  bool(self._shell.config.search_auto_launch)):
                self._launch(('app', results[0]))

        def _refresh(self, text: str) -> None:
            from drawer_page import search_results
            shell = self._shell
            results = search_results(shell, text) if text.strip() else []
            self._rows = launcher_rows(results, text)
            self._selected = 0
            child = self._list.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                self._list.remove(child)
                child = nxt
            paused = getattr(getattr(shell, 'focus_state', None),
                             'is_paused_app', None)
            for row in self._rows:
                box = Gtk.Box()
                box.add_css_class('xx-desklauncher-row')
                if (row[0] == 'app' and callable(paused)
                        and paused(row[1].app_id)):
                    box.add_css_class('paused')
                label = Gtk.Label(
                    label=row_label(row, shell.config.label_for), xalign=0)
                label.add_css_class('xx-desklauncher-name')
                label.set_ellipsize(Pango.EllipsizeMode.END)
                label.set_hexpand(True)
                box.append(label)
                click = Gtk.GestureClick()
                click.connect('released',
                              lambda _g, _n, _x, _y, r=row: self._launch(r))
                box.add_controller(click)
                item = Gtk.ListBoxRow(selectable=False, activatable=False)
                item.set_can_focus(False)
                item.set_child(box)
                self._list.append(item)
            has_rows = bool(self._rows)
            self._rule.set_visible(has_rows)
            self._scroller.set_visible(has_rows)
            self._paint_selection()

        def _paint_selection(self) -> None:
            for i, item in enumerate(self._items()):
                box = item.get_child()
                if i == self._selected:
                    box.add_css_class('selected')
                else:
                    box.remove_css_class('selected')
            GLib.idle_add(self._scroll_to_selected)

        def _items(self) -> list:
            items = []
            child = self._list.get_first_child()
            while child is not None:
                items.append(child)
                child = child.get_next_sibling()
            return items

        def _scroll_to_selected(self) -> bool:
            adj = self._scroller.get_vadjustment()
            top = self._selected * ROW_HEIGHT
            bottom = top + ROW_HEIGHT
            if top < adj.get_value():
                adj.set_value(top)
            elif bottom > adj.get_value() + adj.get_page_size():
                adj.set_value(bottom - adj.get_page_size())
            return False

        # -- keys ------------------------------------------------------------
        def _on_key(self, _ctrl, keyval, _code, state) -> bool:
            name = Gdk.keyval_name(keyval) or ''
            ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
            shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
            if keyval == Gdk.KEY_Escape:
                self.close_prompt()
                return True
            if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                self._activate()
                return True
            if self._list_mode and not ctrl and name.casefold() == 'l':
                self._activate()
                return True
            if self._list_mode and not ctrl and name.casefold() == 'h':
                self._list_mode = False
                return True
            delta = key_move(name, ctrl, shift, self._list_mode)
            if delta is not None:
                if not self._rows:
                    return True
                if delta < 0 and self._selected == 0:
                    self._list_mode = False
                else:
                    self._list_mode = True
                self._selected = step(self._selected, delta, len(self._rows))
                self._paint_selection()
                return True
            if self._list_mode:
                # Any other key types again.
                self._list_mode = False
            return False

        def _on_click(self, _gesture, _n, x, y) -> None:
            ok, rect = self._panel.compute_bounds(self)
            if ok and rect.contains_point(_point(x, y)):
                return
            self.close_prompt()

        # -- launching ---------------------------------------------------------
        def _activate(self) -> None:
            if not self._rows:
                return
            self._launch(self._rows[min(self._selected, len(self._rows) - 1)])

        def _launch(self, row: tuple[str, object]) -> None:
            from drawer_page import _open_settings, launch_app, open_web_search
            shell = self._shell
            kind, value = row
            self.close_prompt()
            if kind == 'app':
                launch_app(shell, value)
            elif kind == 'settings':
                _open_settings(shell)
            elif kind == 'web':
                open_web_search(shell, value)

    def _point(x: float, y: float):
        from gi.repository import Graphene
        p = Graphene.Point()
        p.init(x, y)
        return p
