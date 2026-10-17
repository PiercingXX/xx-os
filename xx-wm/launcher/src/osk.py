"""On-screen keyboard helper for layer-shell surfaces.

phoc layer-shell v3 has no ON_DEMAND. While an editable is focused the
surface uses EXCLUSIVE and the keyboard's SetVisible(True); otherwise
NONE and hide. SetVisible is always async — never call_sync on the UI
thread. Works with xx-keyboard or squeekboard (same sm.puri.OSK0 name).
"""
from __future__ import annotations

from collections.abc import Callable


def set_visible(visible: bool) -> None:
    from gi.repository import Gio, GLib
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        payload = (
            'sm.puri.OSK0', '/sm/puri/OSK0', 'sm.puri.OSK0', 'SetVisible',
            GLib.Variant('(b)', (visible,)), None,
            Gio.DBusCallFlags.NONE,
        )
        bus.call(*payload, 2000, None, None, None)
    except Exception as error:
        from shell_log import get_logger
        get_logger('osk').warning(
            'set-OSK-visible(%s) over D-Bus failed: %s', visible, error)


def set_layer_keyboard(window: object, exclusive: bool) -> None:
    try:
        from gi.repository import Gtk4LayerShell as LayerShell
    except (ImportError, ValueError):
        return
    is_supported = getattr(LayerShell, 'is_supported', None)
    if callable(is_supported) and not is_supported():
        return
    mode = (LayerShell.KeyboardMode.EXCLUSIVE if exclusive
            else LayerShell.KeyboardMode.NONE)
    try:
        LayerShell.set_keyboard_mode(window, mode)
    except Exception:
        pass


def attach(widget: object, on_show: Callable[[], None],
           on_hide: Callable[[], None] | None = None) -> None:
    from gi.repository import Gtk
    focus_cls = getattr(Gtk, 'EventControllerFocus', None)
    if focus_cls is not None:
        focus = focus_cls.new()
        focus.connect('enter', lambda *_: on_show())
        if on_hide is not None:
            focus.connect('leave', lambda *_: on_hide())
        widget.add_controller(focus)
    tap_cls = getattr(Gtk, 'GestureClick', None)
    if tap_cls is None:
        return
    tap = tap_cls.new()
    tap.set_touch_only(False)
    tap.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

    def _pressed(gesture: object, *_args: object) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        widget.grab_focus()
        on_show()

    tap.connect('pressed', _pressed)
    widget.add_controller(tap)
