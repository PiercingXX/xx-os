"""In-window bottom sheets: the shell's one surface for menus, pickers,
prompts and confirmations (the Linux counterpart of the Android
launcher's ActionSheet, which is a bottom sheet for every one of them).

Why not Gtk.Popover: the shell window is a gtk4-layer-shell surface, and
a GtkPopover there is a separate xdg_popup that takes a grab. The shell
flips its layer-shell keyboard mode all the time (a tap on any
non-editable widget drops it to NONE; opening a menu raises it to
EXCLUSIVE so entries can type). phoc answers a keyboard-interactivity
change on the parent by moving keyboard focus, which ends the popup grab,
and GTK then closes the popover about one frame after it maps. Settings
pickers open on button release, after the tap already set NONE, so every
one of them closed before it was seen. A sheet drawn inside the shell
window has no surface of its own and no grab, so focus and layer changes
cannot dismiss it.

A ``SheetHost`` wraps the window content in a Gtk.Overlay. ``present``
shows one sheet at a time over a dim scrim: a tap on the scrim, Escape,
or the back gesture closes it. The returned ``Sheet`` keeps the small
popover surface callers already use: ``popdown()``, ``connect('closed',
cb)``, ``get_visible()`` and ``get_mapped()``.
"""
from __future__ import annotations

from typing import Callable

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, GLib, Gtk

# The sheet never covers the top of the screen: the page under it stays
# readable, like the Android bottom sheet (~85% max height).
MAX_HEIGHT_FRACTION = 0.85
# Wide windows (tablets, the FLX1 at 600 px) keep a phone-width sheet.
MAX_WIDTH = 560
_REVEAL_MS = 160


def sheet_css(tokens: dict) -> str:
    """Theme rules for the sheet (pure; tokens are the shell sheet's)."""
    return """
.xx-sheet-scrim {{ background-color: rgba(0, 0, 0, 0.55); }}
.xx-sheet {{
    background-color: {surface};
    color: {foreground};
    border: 1px solid {border};
    border-bottom: none;
    border-radius: 28px 28px 0 0;
    padding: 0 20px 24px 20px;
}}
.xx-sheet-handle {{
    background-color: alpha({foreground}, 0.35);
    border-radius: 2px;
    min-width: 36px;
    min-height: 4px;
    margin: 12px 0 10px 0;
}}
.xx-sheet .xx-dialog {{
    background-color: transparent;
    border: none;
    border-radius: 0;
    padding: 0;
}}
.xx-sheet .xx-menu-row {{
    min-height: 52px;
}}
/* An entry on the surface-colored sheet sinks to the page color with a
   hairline (the Settings sheets' field look); surface on surface drew
   the Rename field as bare text. */
.xx-sheet entry.xx-entry {{
    background-color: {background};
    border: 1px solid {border};
    min-height: 46px;
}}
.xx-sheet entry.xx-entry:focus-within {{ border-color: {foreground}; }}
""".format(surface=tokens.get('surface', '#111111'),
           foreground=tokens.get('foreground', '#ffffff'),
           border=tokens.get('border', '#333333'),
           background=tokens.get('background', '#000000'))


class Sheet:
    """Handle for one shown sheet; mirrors the Gtk.Popover calls the
    shell made on its menus so every caller keeps working unchanged."""

    def __init__(self, host: 'SheetHost', layer: Gtk.Widget,
                 content: Gtk.Widget) -> None:
        self._host = host
        self.layer = layer
        self.content = content
        self._closed_cbs: list[Callable] = []
        self._open = True

    def connect(self, signal: str, callback: Callable, *args) -> int:
        if signal == 'closed':
            self._closed_cbs.append(lambda: callback(self, *args))
        return len(self._closed_cbs)

    def popdown(self) -> None:
        self._host.close(self)

    def get_visible(self) -> bool:
        return self._open

    def get_mapped(self) -> bool:
        return self._open and self.layer.get_mapped()

    def _fire_closed(self) -> None:
        self._open = False
        for callback in list(self._closed_cbs):
            try:
                callback()
            except Exception:
                from shell_log import get_logger
                get_logger('action_sheet').exception('sheet closed callback')


class SheetHost:
    """Owns the overlay layer the shell's sheets draw in."""

    def __init__(self, child: Gtk.Widget) -> None:
        self.overlay = Gtk.Overlay()
        self.overlay.set_child(child)
        self._sheet: Sheet | None = None
        keys = Gtk.EventControllerKey.new()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect('key-pressed', self._on_key)
        self.overlay.add_controller(keys)

    # -- queries -------------------------------------------------------

    def is_open(self) -> bool:
        return self._sheet is not None

    @property
    def current(self) -> Sheet | None:
        return self._sheet

    # -- show / close --------------------------------------------------

    def present(self, content: Gtk.Widget, settings_scope: bool = False) -> Sheet:
        """Show ``content`` in a bottom sheet, replacing any open one."""
        if self._sheet is not None:
            self.close(self._sheet)
        layer = Gtk.Overlay()
        layer.add_css_class('xx-sheet-layer')
        if settings_scope:
            # Settings rows size their type under .xx-settings; a sheet
            # opened from them keeps the same sizes.
            layer.add_css_class('xx-settings')
        scrim = Gtk.Box()
        scrim.add_css_class('xx-sheet-scrim')
        scrim.set_hexpand(True)
        scrim.set_vexpand(True)
        tap = Gtk.GestureClick.new()
        tap.set_touch_only(False)
        layer.set_child(scrim)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        column.add_css_class('xx-sheet')
        column.set_valign(Gtk.Align.END)
        column.set_halign(Gtk.Align.FILL)
        column.set_hexpand(True)
        handle = Gtk.Box()
        handle.add_css_class('xx-sheet-handle')
        handle.set_halign(Gtk.Align.CENTER)
        column.append(handle)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_propagate_natural_height(True)
        scroller.set_max_content_height(self._max_content_height())
        scroller.set_child(content)
        column.append(scroller)

        clamp = _width_clamp(column)
        revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
            transition_duration=_REVEAL_MS)
        revealer.set_valign(Gtk.Align.END)
        revealer.set_child(clamp)
        layer.add_overlay(revealer)

        sheet = Sheet(self, layer, content)
        sheet.revealer = revealer
        sheet.scroller = scroller
        tap.connect('released',
                    lambda *_a: self.close(sheet) if sheet is self._sheet else None)
        scrim.add_controller(tap)
        self._sheet = sheet
        self.overlay.add_overlay(layer)
        revealer.set_reveal_child(True)
        return sheet

    def close(self, sheet: Sheet | None = None) -> bool:
        """Close ``sheet`` (default: the open one). True when one closed."""
        target = sheet if sheet is not None else self._sheet
        if target is None or target is not self._sheet:
            return False
        self._sheet = None
        try:
            self.overlay.remove_overlay(target.layer)
        except Exception:
            pass
        target._fire_closed()
        return True

    def close_all(self) -> bool:
        return self.close(None)

    # -- internals -----------------------------------------------------

    def _max_content_height(self) -> int:
        height = self.overlay.get_height()
        if height <= 0:
            root = self.overlay.get_root()
            height = root.get_height() if root is not None else 0
        if height <= 0:
            height = 720
        # Handle + padding sit outside the scroller.
        return max(160, int(height * MAX_HEIGHT_FRACTION) - 50)

    def _on_key(self, _ctl, keyval: int, _code: int, _state) -> bool:
        if self._sheet is not None and keyval == Gdk.KEY_Escape:
            GLib.idle_add(lambda: self.close() and False)
            return True
        return False


def _width_clamp(child: Gtk.Widget) -> Gtk.Widget:
    """Cap the sheet at MAX_WIDTH on wide windows, full width on phones."""
    try:
        from gi.repository import Adw
        clamp = Adw.Clamp(maximum_size=MAX_WIDTH, tightening_threshold=MAX_WIDTH)
        clamp.set_child(child)
        return clamp
    except Exception:
        return child


def host_for(widget) -> SheetHost | None:
    """The SheetHost of the window ``widget`` lives in, if it has one."""
    try:
        root = widget.get_root()
    except Exception:
        return None
    host = getattr(root, '_sheet_host', None)
    return host if isinstance(host, SheetHost) else None


def in_settings(widget) -> bool:
    """True when ``widget`` sits under the settings page (.xx-settings)."""
    node = widget
    while node is not None:
        try:
            if node.has_css_class('xx-settings'):
                return True
            node = node.get_parent()
        except Exception:
            return False
    return False
