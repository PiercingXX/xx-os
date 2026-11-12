"""Screen lock through ext-session-lock-v1 (river, sway, any compositor
offering it except phoc).

With this protocol the compositor itself keeps every output locked until
the locking client says unlock: if the shell dies while locked, the
screens stay locked, and the restarted shell takes the lock over. phoc
keeps the layer-shell lock behind its own `-S` shield (lock_screen.py and
xx-wm-session).

gtk4-session-lock unmaps and destroys every window it was given when the
lock ends, so a lock window serves exactly one lock.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable

log = logging.getLogger('session_lock')

SESSION_LOCK = 'session-lock'
LAYER_SHELL = 'layer-shell'

_INTERNAL_CONNECTORS = ('DSI', 'eDP', 'LVDS')

_choice: tuple[str, str] | None = None


def _typelib():
    try:
        import gi
        gi.require_version('Gtk4SessionLock', '1.0')
        from gi.repository import Gtk4SessionLock
        return Gtk4SessionLock
    except (ImportError, ValueError):
        return None


def _wayland_query(display):
    """gdk_wayland_display_query_registry bound to ``display``. A display
    wrapped before GdkWayland was loaded is a bare GdkWaylandDisplay class
    without the method, so it is looked up in the typelib directly."""
    gtype = getattr(type(display), '__gtype__', None)
    if gtype is None or gtype.name != 'GdkWaylandDisplay':
        return None
    try:
        import gi
        gi.require_version('GdkWayland', '4.0')
        from gi.repository import GdkWayland  # noqa: F401
        from gi._gi import Repository
        info = Repository.get_default().find_by_name(
            'GdkWayland', 'WaylandDisplay')
        method = info.find_method('query_registry')
    except Exception:
        return None
    return lambda interface: method(display, interface)


def _has_global(display, interface: str) -> bool:
    query = getattr(display, 'query_registry', None) or _wayland_query(display)
    if query is None:
        return False
    try:
        return bool(query(interface))
    except Exception:
        return False


def _xxwm_present() -> bool:
    try:
        import xxwm_backend
        return xxwm_backend.present()
    except Exception:
        return False


def choose_backend(display=None, env=None, typelib=_typelib,
                   xxwm_present=_xxwm_present) -> tuple[str, str]:
    """(backend, reason): how this session locks. ext-session-lock when
    the compositor advertises it, is not phoc and the typelib can drive
    it; the layer-shell lock otherwise."""
    env = os.environ if env is None else env
    if display is None:
        try:
            from gi.repository import Gdk
            display = Gdk.Display.get_default()
        except Exception:
            display = None
    if display is None:
        return LAYER_SHELL, 'no display'
    river = xxwm_present()
    if not river and ('XX_WM_PHOC_SHELL_MODE' in env
                      or _has_global(display, 'phosh_private')):
        return LAYER_SHELL, 'phoc'
    if not _has_global(display, 'ext_session_lock_manager_v1'):
        return LAYER_SHELL, 'compositor has no ext_session_lock_manager_v1'
    module = typelib()
    if module is None:
        return LAYER_SHELL, 'Gtk4SessionLock typelib missing'
    try:
        supported = bool(module.is_supported())
    except Exception:
        supported = False
    if not supported:
        return LAYER_SHELL, 'gtk4-session-lock cannot bind the protocol'
    return SESSION_LOCK, 'xxwm on river' if river else 'ext-session-lock'


def backend() -> str:
    """The process-wide choice, decided and logged once."""
    global _choice
    if _choice is None:
        _choice = choose_backend()
        log.info('lock screen: %s (%s)', *_choice)
    return _choice[0]


def fall_back(reason: str) -> None:
    """Use the layer-shell lock from now on (the session lock failed at
    runtime), so the next lock still covers the screen."""
    global _choice
    _choice = (LAYER_SHELL, reason)
    log.warning('lock screen: %s (%s)', LAYER_SHELL, reason)


def _reset() -> None:
    """Test seam: decide again on the next backend() call."""
    global _choice
    _choice = None
    _engaged.clear()
    _ui_monitors.clear()


def handset_index(monitors: list) -> int | None:
    """Which monitor carries the lock UI: the first portrait one (the rule
    xxwm uses for the handset), else the first built-in panel, else the
    first. ``monitors`` are (connector, width, height) tuples."""
    if not monitors:
        return None
    for i, (_name, width, height) in enumerate(monitors):
        if height > width:
            return i
    for i, (name, _w, _h) in enumerate(monitors):
        if (name or '').startswith(_INTERNAL_CONNECTORS):
            return i
    return 0


def handset_monitor(display):
    """The GdkMonitor for the lock UI, or None."""
    try:
        model = display.get_monitors()
        monitors = [model.get_item(i) for i in range(model.get_n_items())]
    except Exception:
        return None
    shapes = []
    for monitor in monitors:
        try:
            geo = monitor.get_geometry()
            shapes.append((monitor.get_connector() or '', geo.width, geo.height))
        except Exception:
            shapes.append(('', 0, 0))
    index = handset_index(shapes)
    return None if index is None else monitors[index]


_engaged: set[int] = set()
# Docked (dock_controller): the lock UI goes where the picker says (the
# monitor), covers can be decorated (the handset cover is the trackpad),
# and listeners hear when a lock starts or ends.
_ui_monitor_picker: Callable[[], object] | None = None
_cover_hook: Callable[[object, object], None] | None = None
_state_listeners: list = []
_ui_monitors: dict[int, object] = {}


def set_ui_monitor_picker(picker: Callable[[], object] | None) -> None:
    """``picker()`` returns the GdkMonitor for the next lock's UI, or
    None for the handset rule."""
    global _ui_monitor_picker
    _ui_monitor_picker = picker


def set_cover_hook(hook: Callable[[object, object], None] | None) -> None:
    """``hook(cover_window, monitor)`` runs for every new cover."""
    global _cover_hook
    _cover_hook = hook


def add_state_listener(callback: Callable[[], None]) -> None:
    """``callback()`` runs when a lock is confirmed and when one ends."""
    if callback not in _state_listeners:
        _state_listeners.append(callback)


def ui_monitor():
    """The GdkMonitor holding the active lock's UI, or None."""
    for monitor in _ui_monitors.values():
        return monitor
    return None


def _notify_state() -> None:
    for callback in list(_state_listeners):
        try:
            callback()
        except Exception as exc:  # noqa: BLE001 - a listener never breaks the lock
            log.warning('lock state listener failed: %s', exc)


def engaged() -> bool:
    """True while this process holds an ext-session-lock: the compositor
    shows nothing but lock surfaces, so a window mapped now is never
    seen."""
    return bool(_engaged)


class LockHosted:
    """Mixin for a shell Gtk.Window that must stay reachable over the
    lock (the call UI, the power menu). Over a layer-shell lock it maps
    as its own overlay surface, as always. While an ext-session-lock is
    up only lock surfaces show, so its content moves into the lock
    surface of the host from ``set_lock_host`` and back to this window
    when the window hides or the lock ends; the window itself stays
    unmapped meanwhile.

    The host offers ``can_host_surface()``,
    ``host_surface(widget, on_released) -> bool`` and
    ``release_surface(widget)``; ``on_released`` runs when the lock ends
    with the widget still hosted, after the host dropped it."""

    _lock_host_fn: Callable[[], object] | None = None
    _lock_hosted = None
    _lock_content = None

    def set_lock_host(self, host_fn: Callable[[], object] | None) -> None:
        self._lock_host_fn = host_fn

    def lock_hosted(self) -> bool:
        return self._lock_hosted is not None

    def _lock_host_target(self):
        fn = self._lock_host_fn
        if fn is None:
            return None
        try:
            host = fn()
            if host is not None and host.can_host_surface():
                return host
        except Exception as exc:
            log.warning('lock host unavailable: %s', exc)
        return None

    def _enter_lock(self) -> bool:
        if self._lock_hosted is not None:
            try:
                if self._lock_hosted.can_host_surface():
                    return True
            except Exception:
                pass
            self._leave_lock()
        host = self._lock_host_target()
        if host is None:
            return False
        content = self.get_child()
        if content is None:
            return False
        if super().get_visible():
            super().set_visible(False)
        self.set_child(None)
        try:
            hosted = bool(host.host_surface(content, self._on_lock_released))
        except Exception as exc:
            log.warning('lock host refused a surface: %s', exc)
            hosted = False
        if not hosted:
            self.set_child(content)
            return False
        content.set_sensitive(super().get_sensitive())
        self._lock_hosted = host
        self._lock_content = content
        return True

    def _restore_lock_content(self) -> None:
        content, self._lock_content = self._lock_content, None
        if content is None:
            return
        content.set_sensitive(True)
        if content.get_parent() is None:
            self.set_child(content)

    def _leave_lock(self) -> None:
        host, self._lock_hosted = self._lock_hosted, None
        if host is None:
            return
        try:
            host.release_surface(self._lock_content)
        except Exception as exc:
            log.warning('lock host release failed: %s', exc)
        self._restore_lock_content()

    def _on_lock_released(self) -> None:
        """The lock ended while this surface sat in it: it stays shown,
        now as its own surface."""
        if self._lock_hosted is None:
            return
        self._lock_hosted = None
        self._restore_lock_content()
        super().present()

    def present(self) -> None:
        if not self._enter_lock():
            super().present()

    def set_visible(self, visible: bool) -> None:
        if not visible:
            self._leave_lock()
            super().set_visible(False)
        elif not self._enter_lock():
            super().set_visible(True)

    def hide(self) -> None:
        self._leave_lock()
        super().hide()

    def get_visible(self) -> bool:
        return self._lock_hosted is not None or super().get_visible()

    def set_sensitive(self, sensitive: bool) -> None:
        super().set_sensitive(sensitive)
        if self._lock_hosted is not None and self._lock_content is not None:
            self._lock_content.set_sensitive(sensitive)


class SessionLock:
    """One ext-session-lock: the window from ``make_ui`` (the lock UI)
    on the handset monitor, a plain cover from ``make_cover`` on every
    other monitor, including monitors plugged in while locked. Both make
    a new, unrealized window per call; the library destroys them when the
    lock ends. ``on_ended(failed)`` runs before that."""

    def __init__(self, make_ui: Callable[[], object],
                 make_cover: Callable[[], object],
                 on_ended: Callable[[bool], None] | None = None,
                 module=None, display=None) -> None:
        self._make_ui = make_ui
        self._make_cover = make_cover
        self._on_ended = on_ended
        self._module = module if module is not None else _typelib()
        self._display = display
        self._instance = None
        self._ui_placed = False
        self._covers: list = []
        self.active = False
        self.locked = False
        self._unlock_pending = False

    def lock(self) -> bool:
        """Start the lock; False when it could not even be requested."""
        if self.active:
            return True
        if self._module is None:
            return False
        instance = self._module.Instance.new()
        self._instance = instance
        instance.connect('monitor', self._on_monitor)
        instance.connect('locked', self._on_locked)
        instance.connect('failed', self._on_failed)
        instance.connect('unlocked', self._on_unlocked)
        self.active = True
        _engaged.add(id(self))
        if not instance.lock():
            log.error('session lock refused immediately')
            self._end(failed=True)
            return False
        return True

    def unlock(self) -> None:
        """End the lock. Before the compositor confirmed it, the unlock
        waits for that confirmation (the library would ignore it)."""
        if self._instance is None or not self.active:
            return
        if self.locked:
            self._instance.unlock()
        else:
            self._unlock_pending = True

    def _target_monitor(self):
        picker = _ui_monitor_picker
        if picker is not None:
            try:
                picked = picker()
            except Exception:
                picked = None
            if picked is not None:
                return picked
        display = self._display
        if display is None:
            from gi.repository import Gdk
            display = Gdk.Display.get_default()
        return handset_monitor(display) if display is not None else None

    def _on_monitor(self, instance, monitor) -> None:
        if not self.active:
            return
        if not self._ui_placed:
            target = self._target_monitor()
            if target is None or target == monitor:
                self._ui_placed = True
                _ui_monitors[id(self)] = monitor
                instance.assign_window_to_monitor(self._make_ui(), monitor)
                return
        cover = self._make_cover()
        hook = _cover_hook
        if hook is not None:
            try:
                hook(cover, monitor)
            except Exception as exc:  # noqa: BLE001
                log.warning('lock cover hook failed: %s', exc)
        self._covers.append(cover)
        instance.assign_window_to_monitor(cover, monitor)

    def _on_locked(self, *_args) -> None:
        self.locked = True
        log.info('session locked')
        if self._unlock_pending:
            self._unlock_pending = False
            self._instance.unlock()
        _notify_state()

    def _on_failed(self, *_args) -> None:
        log.error('session lock failed (another client holds the lock?)')
        self._end(failed=True)

    def _on_unlocked(self, *_args) -> None:
        log.info('session unlocked')
        self._end(failed=False)

    def _end(self, failed: bool) -> None:
        if not self.active:
            return
        self.active = False
        self.locked = False
        _engaged.discard(id(self))
        _ui_monitors.pop(id(self), None)
        self._covers.clear()
        callback, self._on_ended = self._on_ended, None
        if callback is not None:
            callback(failed)
        _notify_state()
