"""pywayland client for wlr-foreign-toplevel-management-unstable-v1.

Lists, activates and closes toplevel windows on a wlroots compositor (phoc).
The pywayland interaction is isolated behind a backend so the manager's pure
logic (app filtering, closed-handle pruning, ordering, dispatch) is testable
without a compositor or pywayland installed. Protocol bindings are generated
at runtime by the installed pywayland's own scanner (see wayland_bindings)
into a version-keyed cache, so one vendored generation cannot break the
switcher on a different pywayland. If pywayland, the bindings or the
compositor connection are unavailable the manager degrades to an empty state
instead of crashing, so the switcher can still show "No open apps".
"""

from __future__ import annotations

import logging
import os
import queue
import select
import struct
import threading
from dataclasses import dataclass

import wayland_bindings

log = logging.getLogger(__name__)

STATE_MAXIMIZED = 0
STATE_MINIMIZED = 1
STATE_ACTIVATED = 2
STATE_FULLSCREEN = 3


def decode_state_array(state: object) -> tuple[int, ...]:
    """Decode a foreign-toplevel ``state`` event payload to state ids.

    The protocol declares the argument as an array of uint32, which
    pywayland delivers as bytes in little-endian order. Lists and tuples
    of ints (test fakes, future bindings) are accepted as-is; anything
    else decodes to empty. A trailing partial word is ignored.
    """
    if state is None:
        return ()
    if isinstance(state, (bytes, bytearray, memoryview)):
        data = bytes(state)
        words = len(data) // 4
        if words == 0:
            return ()
        return tuple(struct.unpack('<' + 'I' * words, data[:words * 4]))
    if isinstance(state, (list, tuple)):
        try:
            return tuple(int(item) for item in state)
        except (TypeError, ValueError):
            return ()
    return ()

# The shell's own surfaces must never appear in the switcher, nor the
# on-screen keyboard (it is a regular toplevel while typing). xx-keyboard
# ships as io.piercingxx.XXKeyboard (casefolded: xxkeyboard, no hyphen),
# so both spellings are filtered; squeekboard stays for the fallback.
_OWN_APP_ID = 'io.piercingxx.XXWM'
_FILTERED_SUBSTRINGS = ('squeekboard', 'xx-keyboard', 'xxkeyboard')

# Bind v1 only. phoc 0.56 still sends handle event 7 (`parent`, protocol v2+);
# bindings generated from older output only know events 0–6, and pywayland
# then raises "has no event 7" and drops the rest of the burst — empty recents.
_FOREIGN_TOPLEVEL_BIND_VERSION = 1
_PUMP_INTERVAL_MS = 50

try:
    from pywayland.client import Display

    _DISPLAY_AVAILABLE = True
except (ImportError, OSError):
    # OSError covers a pywayland install whose native libwayland is missing;
    # ImportError covers missing pywayland entirely.
    _DISPLAY_AVAILABLE = False
    Display = None  # type: ignore[assignment,misc]


def _resolve_import_time_classes() -> tuple[object, object, object]:
    """Best-effort synchronous binding resolution for import time.

    Prefers bindings generated on a previous run (matching the installed
    pywayland by construction), then a hand-installed pywayland.protocol
    module. Never generates and never raises: generation happens in a worker
    thread via DeferredWaylandBackend.
    """
    cached = wayland_bindings.load_cached_bindings()
    if cached is not None:
        return (
            cached['WlSeat'],
            cached['ZwlrForeignToplevelManagerV1'],
            cached['ZwlrForeignToplevelHandleV1'],
        )
    try:
        from pywayland.protocol.wayland import WlSeat
        from pywayland.protocol.wlr_foreign_toplevel_management_unstable_v1 import (
            ZwlrForeignToplevelHandleV1,
            ZwlrForeignToplevelManagerV1,
        )
    except (ImportError, OSError, TypeError):
        # Fall back to an environment where the generated wlr module was
        # installed into pywayland.protocol itself (e.g. hand-run scanner).
        # TypeError: pywayland 0.4.18 Global is not subscriptable, and
        # Python 3.14 rejects Global[Iface] from either source.
        return (None, None, None)
    return (WlSeat, ZwlrForeignToplevelManagerV1, ZwlrForeignToplevelHandleV1)


WlSeat, ZwlrForeignToplevelManagerV1, ZwlrForeignToplevelHandleV1 = (
    _resolve_import_time_classes()
)

_PYWAYLAND_AVAILABLE = (
    _DISPLAY_AVAILABLE
    and WlSeat is not None
    and ZwlrForeignToplevelManagerV1 is not None
    and ZwlrForeignToplevelHandleV1 is not None
)


def _post_to_main(callback: object, *args: object) -> None:
    """Run ``callback`` on the GTK main loop; call it directly when GLib is
    unavailable (headless seams and tests without a running loop)."""
    try:
        from gi.repository import GLib
    except Exception:
        callback(*args)  # type: ignore[operator]
        return
    GLib.idle_add(callback, *args)


@dataclass(frozen=True)
class Toplevel:
    """A foreign toplevel as surfaced to the switcher.

    ``handle`` is the opaque protocol object identity; it is stable for the
    lifetime of the window and is what activate()/close() are addressed by.
    """

    app_id: str
    title: str
    handle: object
    activated: bool = False


def _is_filtered(app_id: str) -> bool:
    if app_id == _OWN_APP_ID:
        return True
    lowered = app_id.casefold()
    return any(marker in lowered for marker in _FILTERED_SUBSTRINGS)


class WaylandToplevelBackend:
    """pywayland client driving wlr-foreign-toplevel-management-unstable-v1.

    A dedicated thread owns this Display so GTK's libwayland connection is
    not starved. The wakeup pipe lets activate/close run without waiting
    for a compositor event. FakeDisplay tests stay on the GLib pump, which
    also delivers the initial registry burst. The backend only mutates the
    manager's registry via the ``_upsert``/``_remove`` hooks and reports
    focus via the single state callback; it never reads state back.

    ``seat_cls``/``manager_cls`` default to the import-time bindings; passing
    them explicitly (runtime-generated bindings, or fakes in tests) selects
    the protocol classes for this connection.
    """

    def __init__(
        self,
        seat_cls: object | None = None,
        manager_cls: object | None = None,
        display: object | None = None,
    ) -> None:
        if display is None:
            if Display is None:
                raise RuntimeError('pywayland is not available')
            display = Display()
        if seat_cls is None:
            seat_cls = WlSeat
        if manager_cls is None:
            manager_cls = ZwlrForeignToplevelManagerV1
        if seat_cls is None or manager_cls is None:
            raise RuntimeError('wayland protocol bindings are not available')
        self._display = display
        self._seat_cls = seat_cls
        self._manager_cls = manager_cls
        self._registry: object | None = None
        self._manager: object | None = None
        self._seat: object | None = None
        self._handles: dict[object, object] = {}
        self._info: dict[object, list[str]] = {}
        self._manager_cb = None
        self._state_cb = None
        self._fd_source = None
        self._thread: threading.Thread | None = None
        self._closing = False
        self._requests: queue.Queue = queue.Queue()
        self._wakeup_r: int | None = None
        self._wakeup_w: int | None = None
        self._connect()

    def _connect(self) -> None:
        try:
            self._display.connect()
        except Exception as exc:
            raise RuntimeError(f'cannot connect to Wayland display: {exc}') from exc
        # pywayland resolves new_id event args through the live WlRegistry
        # proxy, so it must stay referenced for the connection's lifetime.
        self._registry = self._display.get_registry()
        self._registry.dispatcher['global'] = self._on_registry_global
        self._registry.dispatcher['global_remove'] = self._on_registry_global_remove
        # Do not start the wayland thread here: the initial registry burst
        # would upsert into a still-None manager callback and recents stays
        # empty. connect() starts it after the callback is bound.
        if self._is_real_display():
            self._wakeup_r, self._wakeup_w = os.pipe()
            os.set_blocking(self._wakeup_r, False)
            self._flush_display()
        else:
            self._arm_pump()
            self._pump()

    def _is_real_display(self) -> bool:
        cls = type(self._display)
        return cls.__name__ == 'Display' and 'pywayland' in (cls.__module__ or '')

    def _wayland_fd(self) -> int | None:
        get_fd = getattr(self._display, 'get_fd', None)
        if not callable(get_fd):
            return None
        try:
            fd = get_fd()
        except Exception:
            return None
        return fd if isinstance(fd, int) and fd >= 0 else None

    def _flush_display(self) -> None:
        try:
            self._display.flush()
        except Exception:
            pass

    def _dispatch_error_is_fatal(self, exc: BaseException) -> bool:
        if self._closing:
            return True
        msg = str(exc)
        if 'destroyed' in msg.lower():
            return True
        if '11' in msg or 'EAGAIN' in msg.upper():
            return False
        if 'has no event' in msg:
            log.warning('wayland event skipped: %s', exc)
            return False
        log.error('wayland thread dispatch failed: %s', exc)
        return True

    def _dispatch_pending(self) -> bool:
        try:
            self._display.dispatch(block=False)
        except Exception as exc:  # noqa: BLE001
            return not self._dispatch_error_is_fatal(exc)
        return True

    def _read_and_dispatch(self) -> bool:
        # pywayland dispatch(block=False) is wl_display_dispatch_pending and
        # does not read the display fd. Calling only that after select left
        # recents at "No open apps" (available=False). Display.read() is
        # prepare_read + read_events; then pending dispatch delivers the
        # registry/toplevel events. No Display.read → blocking dispatch.
        reader = getattr(self._display, 'read', None)
        try:
            if callable(reader):
                reader()
            else:
                self._display.dispatch(block=True)
        except Exception as exc:  # noqa: BLE001
            if self._dispatch_error_is_fatal(exc):
                return False
            return True
        return self._dispatch_pending()

    def _thread_loop(self) -> None:
        self._flush_display()
        wl_fd = self._wayland_fd()
        while not self._closing:
            self._drain_requests()
            self._flush_display()
            if not self._dispatch_pending():
                return
            fds = []
            if wl_fd is not None:
                fds.append(wl_fd)
            if self._wakeup_r is not None:
                fds.append(self._wakeup_r)
            if not fds:
                if not self._read_and_dispatch():
                    return
                continue
            try:
                ready, _, _ = select.select(fds, [], [], 0.25)
            except Exception as exc:  # noqa: BLE001
                log.error('wayland thread select failed: %s', exc)
                return
            if self._wakeup_r is not None and self._wakeup_r in ready:
                try:
                    os.read(self._wakeup_r, 64)
                except BlockingIOError:
                    pass
                self._drain_requests()
                self._flush_display()
            if wl_fd is not None and wl_fd in ready:
                if not self._read_and_dispatch():
                    return
                self._flush_display()

    def _arm_pump(self) -> None:
        import gi
        gi.require_version('Gtk', '4.0')
        from gi.repository import GLib

        self._fd_source = GLib.timeout_add(_PUMP_INTERVAL_MS, self._pump)

    def _pump(self) -> bool:
        # Flush first: dispatch(block=False) raises EAGAIN on an empty
        # queue, and a combined try would skip flush — the get_registry
        # / bind requests never leave the client (empty recents).
        try:
            self._display.flush()
        except Exception:
            pass
        try:
            self._display.dispatch(block=False)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if '11' in msg or 'EAGAIN' in msg.upper():
                pass
            elif 'has no event' in msg:
                log.warning('wayland event skipped: %s', exc)
            else:
                log.error('wayland dispatch failed: %s', exc)
                return False
        try:
            self._display.flush()
        except Exception:
            pass
        return True

    # -- protocol glue -----------------------------------------------------

    def _on_registry_global(self, registry, name: int, interface: str, version: int) -> None:
        # pywayland dispatches wl_registry.global as (registry, name,
        # interface, version) with no serial. An extra parameter here used
        # to make every global raise TypeError and the manager never bound
        # (seen on a real phoc session).
        if interface == 'wl_seat':
            self._seat = registry.bind(name, self._seat_cls, 1)
        elif interface == 'zwlr_foreign_toplevel_manager_v1':
            self._manager = registry.bind(
                name, self._manager_cls, _FOREIGN_TOPLEVEL_BIND_VERSION)
            self._manager.dispatcher['toplevel'] = self._on_toplevel
            self._manager.dispatcher['finished'] = self._on_finished

    def _on_registry_global_remove(self, _registry, _name: int) -> None:
        pass

    def _on_toplevel(self, manager, toplevel) -> None:
        log.info('foreign toplevel created %s', id(toplevel))
        self._handles[id(toplevel)] = toplevel
        toplevel.dispatcher['title'] = self._on_title
        toplevel.dispatcher['app_id'] = self._on_app_id
        toplevel.dispatcher['state'] = self._on_state
        toplevel.dispatcher['output_enter'] = self._on_output_enter
        toplevel.dispatcher['output_leave'] = self._on_output_leave
        toplevel.dispatcher['done'] = self._on_done
        toplevel.dispatcher['closed'] = self._on_closed
        toplevel.dispatcher['parent'] = self._on_parent
        self._upsert(id(toplevel), '', '')

    def _on_title(self, toplevel, title: str) -> None:
        info = self._info.setdefault(id(toplevel), ['', ''])
        info[1] = title
        self._upsert(id(toplevel), info[0], info[1])

    def _on_app_id(self, toplevel, app_id: str) -> None:
        info = self._info.setdefault(id(toplevel), ['', ''])
        info[0] = app_id
        self._upsert(id(toplevel), info[0], info[1])

    def _on_state(self, toplevel, state) -> None:
        try:
            activated = STATE_ACTIVATED in decode_state_array(state)
        except Exception:  # noqa: BLE001 - a bad state array must not break dispatch
            activated = False
        if self._state_cb is not None:
            self._state_cb(id(toplevel), activated)

    def _on_parent(self, toplevel, _parent) -> None:
        pass

    def _on_output_enter(self, toplevel, _output) -> None:
        pass

    def _on_output_leave(self, toplevel, _output) -> None:
        pass

    def _on_done(self, toplevel) -> None:
        pass

    def _on_closed(self, toplevel) -> None:
        log.info('foreign toplevel closed %s', id(toplevel))
        self._remove(id(toplevel))
        self._handles.pop(id(toplevel), None)
        self._info.pop(id(toplevel), None)

    def _on_finished(self, _manager) -> None:
        # Compositor dropped the protocol; clear everything.
        log.info('foreign-toplevel manager finished')
        self._manager = None
        for handle in list(self._handles):
            self._remove(handle)
        self._handles.clear()
        self._info.clear()

    # -- manager hooks -----------------------------------------------------

    def _call_manager_cb(self, handle: object, app_id: object, title: object) -> None:
        cb = self._manager_cb
        if cb is None:
            return
        # Invoke directly: ToplevelManager is thread-safe and marshals
        # GTK refresh itself. idle_add here dropped the initial burst
        # when the GTK source did not run before the first show.
        cb(handle, app_id, title)

    def _upsert(self, handle: object, app_id: str, title: str) -> None:
        self._call_manager_cb(handle, app_id, title)

    def _remove(self, handle: object) -> None:
        self._call_manager_cb(handle, None, None)

    # -- public API (backend contract) -------------------------------------

    def available(self) -> bool:
        return self._manager is not None and not self._closing

    def connect(self, callback) -> None:
        self._manager_cb = callback
        self._replay_handles()
        # Start the wayland thread only after the callback is bound so the
        # initial registry/toplevel burst is not upserted into None.
        if self._is_real_display() and self._thread is None:
            self._thread = threading.Thread(
                target=self._thread_loop, daemon=True, name='xx-wm-toplevel')
            self._thread.start()

    def connect_focus(self, callback) -> None:
        """Legacy alias for connect_state: a single activation feed."""
        self._state_cb = callback

    def _replay_handles(self) -> None:
        for handle in list(self._handles):
            info = self._info.get(handle, ['', ''])
            self._call_manager_cb(handle, info[0], info[1])

    def connect_state(self, callback) -> None:
        """Register ``callback(handle, activated)`` for state events."""
        self._state_cb = callback

    def _drain_requests(self) -> None:
        while True:
            try:
                op, handle = self._requests.get_nowait()
            except queue.Empty:
                return
            self._apply_request(op, handle)

    def _apply_request(self, op: str, handle: object) -> None:
        toplevel = self._handles.get(handle)
        if toplevel is None:
            log.warning('toplevel %s %r: handle gone', op, handle)
            return
        try:
            if op == 'activate':
                if self._seat is None:
                    log.warning('cannot activate %r: no wl_seat bound', handle)
                    return
                toplevel.activate(self._seat)
            elif op == 'close':
                toplevel.close()
            self._display.flush()
        except Exception as exc:  # noqa: BLE001
            log.warning('toplevel %s %r failed: %s', op, handle, exc)

    def _wakeup(self) -> None:
        if self._wakeup_w is None:
            self._drain_requests()
            return
        try:
            os.write(self._wakeup_w, b'\0')
        except OSError:
            pass

    def activate(self, handle: object) -> None:
        self._requests.put(('activate', handle))
        self._wakeup()

    def close(self, handle: object) -> None:
        self._requests.put(('close', handle))
        self._wakeup()

    def shutdown(self) -> None:
        self._closing = True
        self._wakeup()
        try:
            disconnect = getattr(self._display, 'disconnect', None)
            if callable(disconnect):
                disconnect()
        except Exception:
            pass
        for attr in ('_wakeup_r', '_wakeup_w'):
            fd = getattr(self, attr)
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                setattr(self, attr, None)


class DeferredWaylandBackend:
    """Backend contract over bindings generated in a worker thread.

    Construction returns immediately: binding generation (a scanner
    subprocess) and the compositor connect run off the GTK main loop in a
    daemon thread. ``available()`` is False until the real backend is up;
    callbacks registered via ``connect``/``on_ready`` are then wired and the
    manager is notified on the main loop. Any failure degrades silently to
    permanently unavailable — the switcher keeps its empty state.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._callback = None
        self._state_callback = None
        self._real: WaylandToplevelBackend | None = None
        self._ready_callbacks: list[object] = []
        self._shutdown_requested = False
        thread = threading.Thread(
            target=self._prepare, name='xx-wm-wayland-bindings', daemon=True,
        )
        thread.start()

    def shutdown(self) -> None:
        """Drop the live connection, or tear down the backend still being
        built. Without this every resync leaked a Wayland client, a reader
        thread and a pipe pair."""
        with self._lock:
            self._shutdown_requested = True
            real = self._real
        if real is not None:
            try:
                shutdown = getattr(real, 'shutdown', None)
                if callable(shutdown):
                    shutdown()
            except Exception:
                pass

    def available(self) -> bool:
        with self._lock:
            real = self._real
        return real is not None and real.available()

    def connect(self, callback) -> None:
        with self._lock:
            self._callback = callback
            real = self._real
        if real is not None:
            real.connect(callback)

    def connect_state(self, callback) -> None:
        with self._lock:
            self._state_callback = callback
            real = self._real
        if real is not None:
            real.connect_state(callback)

    def connect_focus(self, callback) -> None:
        """Legacy alias for connect_state: a single activation feed."""
        self.connect_state(callback)

    def on_ready(self, callback) -> None:
        """Invoke ``callback`` on the main loop once the real backend is up."""
        with self._lock:
            if self._real is None:
                self._ready_callbacks.append(callback)
                return
        _post_to_main(callback)

    def activate(self, handle: object) -> None:
        with self._lock:
            real = self._real
        if real is not None and real.available():
            real.activate(handle)

    def close(self, handle: object) -> None:
        with self._lock:
            real = self._real
        if real is not None and real.available():
            real.close(handle)

    def _prepare(self) -> None:
        try:
            bindings = wayland_bindings.ensure_bindings()
        except Exception as exc:  # noqa: BLE001 - degrade on any failure
            log.info('wayland bindings failed: %s', exc)
            return
        if bindings is None:
            return
        try:
            real = WaylandToplevelBackend(
                seat_cls=bindings['WlSeat'],
                manager_cls=bindings['ZwlrForeignToplevelManagerV1'],
            )
        except Exception as exc:  # noqa: BLE001 - graceful absence on any failure
            log.info('failed to start toplevel backend: %s', exc)
            return
        with self._lock:
            aborted = self._shutdown_requested
            if not aborted:
                self._real = real
                if self._callback is not None:
                    real.connect(self._callback)
                if self._state_callback is not None:
                    connect_state = getattr(real, 'connect_state', None)
                    if callable(connect_state):
                        connect_state(self._state_callback)
                ready = list(self._ready_callbacks)
                self._ready_callbacks.clear()
            else:
                ready = []
        if aborted:
            try:
                real.shutdown()
            except Exception:
                pass
            return
        for callback in ready:
            _post_to_main(callback)


class ToplevelManager:
    """Tracks foreign toplevels and dispatches activate/close.

    The backend is the only component that touches pywayland. It must expose:

        available() -> bool
        connect(callback) -> None   # callback(handle, app_id, title) on change,
                                    # callback(handle, None, None) on removal
        activate(handle) -> None
        close(handle) -> None

    Backends that report window state additionally expose
    ``connect_state(callback)`` with ``callback(handle, activated)``; the
    legacy ``connect_focus`` name wires the same single feed. The manager
    tolerates backends with neither (no activation tracking).

    A backend that is still preparing (DeferredWaylandBackend) additionally
    exposes ``on_ready(callback)``; the manager connects immediately and is
    notified on the main loop once the backend is up, so the switcher
    refreshes without polling.

    The manager keeps the authoritative registry and applies app filtering
    and closed-handle pruning on top of whatever the backend reports.
    """

    def __init__(self, backend: object | None = None) -> None:
        if backend is None:
            backend = _make_default_backend()
        self._backend = backend
        self._registry: dict[object, Toplevel] = {}
        self._callbacks: list[object] = []
        self._lock = threading.Lock()
        self._activated: object | None = None
        self._activated_callbacks: list[object] = []
        self._current_active: str | None = None
        self._generation = 0
        if backend is not None:
            # Bind before the first globals arrive: the backend is
            # non-blocking, so available() may still be False at construct.
            backend.connect(self._on_backend_event)
            self._connect_tracking(backend)
            on_ready = getattr(backend, 'on_ready', None)
            if callable(on_ready):
                on_ready(self._on_bindings_ready)

    def _connect_tracking(self, backend: object) -> None:
        """Wire the single activation feed: ``connect_state`` where the
        backend offers it, else the legacy ``connect_focus`` name."""
        connect_state = getattr(backend, 'connect_state', None)
        if callable(connect_state):
            connect_state(self._on_backend_state)
            return
        connect_focus = getattr(backend, 'connect_focus', None)
        if callable(connect_focus):
            connect_focus(self._on_backend_state)

    def _on_bindings_ready(self) -> None:
        self._notify()

    @property
    def available(self) -> bool:
        """True when a working protocol backend could be established."""
        return self._backend is not None and self._backend.available()

    def list(self) -> list[Toplevel]:
        """Current toplevels, shell and keyboard surfaces filtered out."""
        with self._lock:
            return [t for t in self._registry.values() if not _is_filtered(t.app_id)]

    def activated_app_id(self) -> str | None:
        """app_id of the focused foreign toplevel, or None."""
        with self._lock:
            return self._current_active

    def activate(self, handle: object) -> None:
        if self._backend is not None and self._backend.available():
            self._backend.activate(handle)

    def close(self, handle: object) -> None:
        if self._backend is not None and self._backend.available():
            self._backend.close(handle)

    def resync(self, backend: object | None = None) -> None:
        """Drop the current protocol client and bind again.

        A live connection can stay available() True after it stops receiving
        new toplevels (phoc sends the initial snapshot, then silence). Recents
        then shows "No open apps" while windows are on screen. Re-binding
        pulls the current list, the same way a fresh standalone client does.
        """
        old = self._backend
        with self._lock:
            self._registry.clear()
            self._activated = None
            # A re-bind replays every open window under fresh handles. The
            # shell must not mistake that replay for newly mapped windows.
            self._generation += 1
        if backend is None:
            backend = _make_default_backend()
        self._backend = backend
        if backend is not None:
            backend.connect(self._on_backend_event)
            self._connect_tracking(backend)
        closer = getattr(old, 'shutdown', None)
        if callable(closer) and old is not backend:
            closer()
        self._notify()
        self._recompute_active()

    def on_change(self, callback: object) -> None:
        """Register a zero-arg callable invoked whenever the registry changes."""
        self._callbacks.append(callback)

    def on_activated_changed(self, callback: object) -> None:
        """Register ``callback(app_id | None)`` for foreground changes.

        The argument is the activated app toplevel's app_id, or None when
        no app toplevel is activated (e.g. the shell itself has focus).
        Filtered surfaces (the shell, the keyboard) never report as active.
        """
        self._activated_callbacks.append(callback)

    @property
    def active_app_id(self) -> str | None:
        """Currently activated app toplevel's app_id, or None."""
        return self._current_active

    @property
    def generation(self) -> int:
        """Re-bind count. Changes on every resync so the shell can tell a
        replay of already-known windows apart from newly mapped ones."""
        return self._generation

    def _marshal_to_main(self, callback: object, *args: object) -> None:
        """Run ``callback`` on the GTK main thread.

        Directly when already there; otherwise via idle_add. The old
        ``GLib.main_depth() == 0`` probe meant "no loop running", but the
        depth is counted per thread, so on the wayland thread it always
        read 0 and GTK widgets were touched off-thread. Off-thread callers
        with no GLib (or an already-owned context) still deliver directly
        instead of losing the change.
        """
        if threading.current_thread() is threading.main_thread():
            callback(*args)  # type: ignore[operator]
            return
        try:
            from gi.repository import GLib
        except Exception:
            callback(*args)  # type: ignore[operator]
            return
        try:
            if GLib.MainContext.default().is_owner():
                callback(*args)  # type: ignore[operator]
                return
        except Exception:
            pass
        GLib.idle_add(self._run_once, callback, args)

    @staticmethod
    def _run_once(callback: object, args: object) -> bool:
        callback(*args)  # type: ignore[operator]
        return False

    def _notify(self) -> None:
        self._marshal_to_main(self._emit_change)

    def _emit_change(self) -> None:
        for cb in self._callbacks:
            try:
                cb()
            except Exception as exc:  # noqa: BLE001 - one bad callback must not break the registry
                log.error('toplevel change callback failed: %s', exc)

    def _notify_activated(self, app_id: str | None) -> None:
        self._marshal_to_main(self._emit_activated, app_id)

    def _emit_activated(self, app_id: str | None) -> None:
        for cb in self._activated_callbacks:
            try:
                cb(app_id)
            except Exception as exc:  # noqa: BLE001 - one bad callback must not break the registry
                log.error('toplevel activated callback failed: %s', exc)

    def _recompute_active(self) -> None:
        with self._lock:
            topo = self._registry.get(self._activated)
            best = None
            if topo is not None and topo.app_id and not _is_filtered(topo.app_id):
                best = topo.app_id
        if best != self._current_active:
            self._current_active = best
            self._notify_activated(best)

    def _note_focus(self, handle: object, activated: object) -> None:
        activated = bool(activated)
        with self._lock:
            if activated:
                self._activated = handle
            elif self._activated == handle:
                self._activated = None
            for known, topo in self._registry.items():
                want = known == self._activated
                if topo.activated != want:
                    self._registry[known] = Toplevel(
                        app_id=topo.app_id, title=topo.title,
                        handle=topo.handle, activated=want,
                    )
        self._recompute_active()

    def _on_backend_state(self, handle: object, activated: object) -> None:
        self._note_focus(handle, activated)

    def _on_backend_event(self, handle: object, app_id: object, title: object) -> None:
        with self._lock:
            if app_id is None:
                self._registry.pop(handle, None)
                if self._activated == handle:
                    self._activated = None
            else:
                self._registry[handle] = Toplevel(
                    app_id=app_id, title=title or '', handle=handle,
                    activated=handle == self._activated,
                )
        self._notify()
        self._recompute_active()


def _make_default_backend() -> object | None:
    """Build the binding backend, or None when pywayland is unavailable.

    Generation and connect run in a worker thread inside
    DeferredWaylandBackend, so this never blocks the GTK main loop.

    Under river the xxwm window manager owns window switching, so its
    control socket wins over the Wayland protocol client when present.
    """
    try:
        import xxwm_backend
        if xxwm_backend.present():
            log.info('toplevel manager: using the xxwm window manager')
            return xxwm_backend.XxwmBackend()
    except Exception as exc:  # noqa: BLE001 - fall back to the protocol client
        log.warning('xxwm backend unavailable: %s', exc)
    if not _DISPLAY_AVAILABLE:
        log.info('pywayland unavailable, toplevel manager disabled')
        return None
    return DeferredWaylandBackend()
