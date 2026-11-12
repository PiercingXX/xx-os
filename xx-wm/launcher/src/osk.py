"""On-screen keyboard helper for layer-shell surfaces.

phoc layer-shell v3 has no ON_DEMAND. While an editable is focused the
surface uses EXCLUSIVE and the keyboard's SetVisible(True); otherwise
NONE and hide. SetVisible is always async — never call_sync on the UI
thread.

xx-keyboard and squeekboard (and upstream phosh-osk-stevia) all serve
sm.puri.OSK0. The shell finds which one is running (/proc) and which
process owns the name (NameOwnerChanged + GetConnectionUnixProcessID,
all async), and sends SetVisible only when the owner is the running
keyboard: never to a name nobody owns (that only logged ServiceUnknown
on the FLX1, where xx-keyboard ran without the name), never with
D-Bus auto-start (it could launch squeekboard next to xx-keyboard), and
never to another keyboard that happens to hold the name.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

OSK_NAME = 'sm.puri.OSK0'
OSK_PATH = '/sm/puri/OSK0'
OSK_IFACE = 'sm.puri.OSK0'

_DBUS_NAME = 'org.freedesktop.DBus'
_DBUS_PATH = '/org/freedesktop/DBus'
_DBUS_IFACE = 'org.freedesktop.DBus'

# Preference order when more than one runs. /proc/<pid>/comm keeps 15
# characters, so 'phosh-osk-stevia' reads 'phosh-osk-stevi'.
KEYBOARDS = ('xx-keyboard', 'phosh-osk-stevia', 'squeekboard')


def _keyboard_for_comm(comm: str) -> str | None:
    comm = comm.strip()
    for name in KEYBOARDS:
        if comm and name[:15] == comm:
            return name
    return None


def running_keyboard(proc: Path = Path('/proc'),
                     uid: int | None = None) -> str | None:
    """The on-screen keyboard running for this user, by /proc comm
    (xx-keyboard first), or None."""
    uid = os.getuid() if uid is None else uid
    found: set[str] = set()
    try:
        entries = list(proc.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            name = _keyboard_for_comm((entry / 'comm').read_text())
            if name is None or entry.stat().st_uid != uid:
                continue
        except OSError:
            continue
        found.add(name)
    for name in KEYBOARDS:
        if name in found:
            return name
    return None


def _comm_of(pid: int, proc: Path = Path('/proc')) -> str | None:
    try:
        return _keyboard_for_comm((proc / str(pid) / 'comm').read_text())
    except OSError:
        return None


def _log():
    from shell_log import get_logger
    return get_logger('osk')


class KeyboardLink:
    """Async tracker of who serves sm.puri.OSK0, and the SetVisible sender.

    The first set_visible() subscribes to NameOwnerChanged and asks for the
    current owner; a request made before the answer is kept (newest wins)
    and sent once the owner is known to be the running keyboard.
    """

    def __init__(self, bus: object, proc: Path = Path('/proc')) -> None:
        self._bus = bus
        self._proc = proc
        self._started = False
        self._resolved = False
        self._owner: str | None = None
        self._owner_keyboard: str | None = None
        self._running: str | None = None
        self._pending: bool | None = None
        self._noted: set[str] = set()

    def set_visible(self, visible: bool) -> None:
        self._pending = bool(visible)
        if not self._started:
            self._start()
            return
        if self._resolved:
            self._flush()

    # -- owner tracking ---------------------------------------------------
    def _start(self) -> None:
        from gi.repository import Gio, GLib
        self._started = True
        self._bus.signal_subscribe(
            _DBUS_NAME, _DBUS_IFACE, 'NameOwnerChanged', _DBUS_PATH,
            OSK_NAME, Gio.DBusSignalFlags.NONE, self._on_owner_changed, None)
        self._bus.call(
            _DBUS_NAME, _DBUS_PATH, _DBUS_IFACE, 'GetNameOwner',
            GLib.Variant('(s)', (OSK_NAME,)), GLib.VariantType.new('(s)'),
            Gio.DBusCallFlags.NONE, 1000, None, self._on_get_owner)

    def _on_get_owner(self, bus, result) -> None:
        try:
            owner = bus.call_finish(result).unpack()[0]
        except Exception:
            owner = ''   # NameHasNoOwner
        self._set_owner(owner)

    def _on_owner_changed(self, _conn, _sender, _path, _iface, _signal,
                          params, _user_data) -> None:
        try:
            name, _old, new = params.unpack()
        except Exception:
            return
        if name == OSK_NAME:
            self._set_owner(new)

    def _set_owner(self, owner: str) -> None:
        self._owner = owner or None
        self._owner_keyboard = None
        self._running = running_keyboard(self._proc)
        if self._owner is None:
            self._resolved = True
            self._flush()
            return
        self._resolved = False
        from gi.repository import Gio, GLib
        self._bus.call(
            _DBUS_NAME, _DBUS_PATH, _DBUS_IFACE, 'GetConnectionUnixProcessID',
            GLib.Variant('(s)', (self._owner,)), GLib.VariantType.new('(u)'),
            Gio.DBusCallFlags.NONE, 1000, None,
            lambda bus, result, owner=self._owner:
            self._on_owner_pid(bus, result, owner))

    def _on_owner_pid(self, bus, result, owner: str) -> None:
        if owner != self._owner:
            return   # the name moved on while we asked
        try:
            pid = bus.call_finish(result).unpack()[0]
            self._owner_keyboard = _comm_of(pid, self._proc)
        except Exception:
            self._owner_keyboard = None
        self._resolved = True
        self._flush()

    # -- sending ----------------------------------------------------------
    def target(self) -> str | None:
        """Bus name to send SetVisible to, or None when no running
        keyboard serves sm.puri.OSK0."""
        if self._owner is None:
            return None
        if (self._running is not None and self._owner_keyboard is not None
                and self._owner_keyboard != self._running):
            return None
        return OSK_NAME

    def _flush(self) -> None:
        visible, self._pending = self._pending, None
        if visible is None:
            return
        if self.target() is None:
            self._note_unreachable()
            return
        from gi.repository import Gio, GLib
        self._bus.call(
            OSK_NAME, OSK_PATH, OSK_IFACE, 'SetVisible',
            GLib.Variant('(b)', (visible,)), None,
            Gio.DBusCallFlags.NO_AUTO_START, 2000, None, self._on_set_done)

    def _on_set_done(self, bus, result) -> None:
        try:
            bus.call_finish(result)
        except Exception as error:
            self._note_once(f'set:{self._owner}',
                            'SetVisible on %s failed: %s', OSK_NAME, error)

    def _note_unreachable(self) -> None:
        if self._owner is None:
            self._note_once(
                f'none:{self._running}',
                '%s runs without the %s name; it shows on text input only',
                self._running or 'no on-screen keyboard', OSK_NAME)
        else:
            self._note_once(
                f'other:{self._owner}',
                '%s is held by %s, not the running %s; not asking it',
                OSK_NAME, self._owner_keyboard, self._running)

    def _note_once(self, key: str, message: str, *args) -> None:
        if key in self._noted:
            return
        self._noted.add(key)
        _log().info(message, *args)


_LINK: KeyboardLink | None = None


def _link() -> KeyboardLink:
    global _LINK
    if _LINK is None:
        from gi.repository import Gio
        _LINK = KeyboardLink(Gio.bus_get_sync(Gio.BusType.SESSION, None))
    return _LINK


def set_visible(visible: bool) -> None:
    try:
        _link().set_visible(visible)
    except Exception as error:
        _log().warning('set-OSK-visible(%s) failed: %s', visible, error)


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
