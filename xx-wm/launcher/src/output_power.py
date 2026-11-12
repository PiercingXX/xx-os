"""Real display power-off over zwlr_output_power_manager_v1 (version 1).

phoc 0.56 offers the output-power manager, which cuts panel/GPU power and
stops the compositor rendering — unlike brightnessctl (backlight only) or
the wlopm binary (not shipped on target devices). Protocol bindings are
generated at runtime by the installed pywayland's own scanner (see
wayland_bindings) into a version-keyed cache.

Generation, the compositor connect and the first registry round trip run
in a worker thread; only the fd watch and the ready listeners run on the
GTK main loop. Any failure (no pywayland, no bindings, no compositor, no
manager, a set_mode `failed` event, a dead connection) leaves
``available`` False so DisplayManager falls back to backlight-only
blanking instead of leaving the screen dark. A connection that dies after
it worked is opened again with backoff, and the last mode the shell asked
for is applied again, so outputs powered off before the loss do not stay
dark until a restart.
"""

from __future__ import annotations

import logging
import threading

import wayland_bindings

log = logging.getLogger(__name__)

XML_NAMES = wayland_bindings.OUTPUT_POWER_XML_NAMES
CLASS_NAMES = wayland_bindings.OUTPUT_POWER_CLASS_NAMES

MODE_OFF = 0
MODE_ON = 1

_RECONNECT_DELAYS = (1, 2, 5, 10, 30)
_RECONNECT_MAX = 20

try:
    from pywayland.client import Display

    _DISPLAY_AVAILABLE = True
except (ImportError, OSError):
    # OSError covers a pywayland install whose native libwayland is missing;
    # ImportError covers missing pywayland entirely.
    _DISPLAY_AVAILABLE = False
    Display = None  # type: ignore[assignment,misc]


def _post_to_main(callback: object, *args: object) -> None:
    """Run ``callback`` on the GTK main loop; call it directly when GLib is
    unavailable (headless seams and tests without a running loop)."""
    try:
        from gi.repository import GLib
    except Exception:
        callback(*args)  # type: ignore[operator]
        return
    GLib.idle_add(callback, *args)


def _call_later(seconds: int, callback: object) -> None:
    from gi.repository import GLib

    def _once() -> bool:
        callback()  # type: ignore[operator]
        return False

    GLib.timeout_add_seconds(seconds, _once)


class OutputPower:
    """pywayland client driving zwlr_output_power_manager_v1.

    Binds the manager and every wl_output, then exposes
    ``set_all(on)`` to power all outputs off/on. ``available`` is True
    only once the main-loop connect has bound the manager; a `failed`
    event or a dead connection flips it back to False permanently.

    The compositor keeps output-power mode across shell restarts, so a
    fresh client must never assume a default: DisplayManager registers
    a ready listener (force outputs on once the manager binds) and mode
    / fail listeners (mirror the compositor-reported state, including
    changes made by someone else).
    """

    def __init__(self, display: object | None = None,
                 display_factory: object | None = None) -> None:
        self._lock = threading.Lock()
        self._display = display
        # Only a connection this object opens can be opened again.
        if display_factory is None and display is None and Display is not None:
            display_factory = Display
        self._display_factory = display_factory
        self._bindings: dict | None = None
        self._bindings_seen: dict | None = None
        self._wanted: bool | None = None
        self._reconnects = 0
        self._registry: object | None = None
        self._output_cls: object | None = None
        self._manager_cls: object | None = None
        self._manager: object | None = None
        self._outputs: dict[int, object] = {}
        self._powers: dict[int, object] = {}
        self._connected = False
        self._failed = False
        self._fd_source = None
        # Ready fires once on the next successful bind (never for binds
        # that already happened — the registrant reconciles those itself,
        # so a late registration cannot force outputs on twice). Mode
        # listeners get True/False per compositor `mode` event; fail
        # listeners fire once per `failed` event (the refused change never
        # happened, so the mirrored state stays as it was).
        self._ready_listeners: list[object] = []
        self._mode_listeners: list[object] = []
        self._fail_listeners: list[object] = []
        thread = threading.Thread(
            target=self._prepare, name='xx-wm-output-power', daemon=True,
        )
        thread.start()

    def add_ready_listener(self, callback: object) -> None:
        """Run ``callback()`` once the manager binds (next bind only)."""
        with self._lock:
            self._ready_listeners.append(callback)

    def add_mode_listener(self, callback: object) -> None:
        """Run ``callback(on: bool)`` on every compositor `mode` event."""
        with self._lock:
            self._mode_listeners.append(callback)

    def add_fail_listener(self, callback: object) -> None:
        """Run ``callback()`` on every compositor `failed` event."""
        with self._lock:
            self._fail_listeners.append(callback)

    def _fire_ready(self) -> None:
        with self._lock:
            listeners = list(self._ready_listeners)
            self._ready_listeners = []
        for callback in listeners:
            try:
                callback()  # type: ignore[operator]
            except Exception as exc:  # noqa: BLE001 - one bad listener
                log.warning('output power ready listener failed: %s', exc)

    def _fire_mode(self, on: bool) -> None:
        with self._lock:
            listeners = list(self._mode_listeners)
        for callback in listeners:
            try:
                callback(on)  # type: ignore[operator]
            except Exception as exc:  # noqa: BLE001 - mirror must survive
                log.warning('output power mode listener failed: %s', exc)

    def _fire_failed(self) -> None:
        with self._lock:
            listeners = list(self._fail_listeners)
        for callback in listeners:
            try:
                callback()  # type: ignore[operator]
            except Exception as exc:  # noqa: BLE001 - mirror must survive
                log.warning('output power fail listener failed: %s', exc)

    @property
    def available(self) -> bool:
        with self._lock:
            return (
                self._connected
                and not self._failed
                and self._manager is not None
            )

    def set_all(self, on: bool) -> bool:
        """Power every bound output off (``on=False``) or on. Returns True
        only when every output accepted the mode; False (never raises)
        tells DisplayManager to fall back to backlight-only blanking."""
        mode = MODE_ON if on else MODE_OFF
        with self._lock:
            self._wanted = bool(on)
            usable = (
                self._connected and not self._failed
                and self._manager is not None
            )
            powers = list(self._powers.values()) if usable else []
        if not usable or not powers:
            return False
        try:
            for power in powers:
                power.set_mode(mode)  # type: ignore[attr-defined]
            self._display.flush()  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 - fall back, never trap dark
            log.error('output power set_mode failed: %s', exc)
            self._mark_dead()
            return False
        return True

    def _mark_dead(self) -> None:
        with self._lock:
            was = self._connected
            self._connected = False
            source, self._fd_source = self._fd_source, None
        if source is not None:
            try:
                from gi.repository import GLib
                GLib.source_remove(source)
            except Exception:  # noqa: BLE001 - already gone
                pass
        if was:
            self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        if self._display_factory is None or self._bindings is None:
            return
        if self._reconnects >= _RECONNECT_MAX:
            log.error('output power: giving up on the compositor connection')
            return
        delay = _RECONNECT_DELAYS[min(self._reconnects, len(_RECONNECT_DELAYS) - 1)]
        self._reconnects += 1
        try:
            _call_later(delay, self._reconnect)
        except Exception as exc:  # noqa: BLE001 - no loop, no reconnect
            log.warning('output power reconnect not scheduled: %s', exc)

    def _reconnect(self) -> None:
        old = self._display
        with self._lock:
            self._connected = False
            self._manager = None
            self._outputs = {}
            self._powers = {}
            self._display = None
        try:
            closer = getattr(old, 'disconnect', None)
            if callable(closer):
                closer()
        except Exception:  # noqa: BLE001 - the socket is already dead
            pass
        threading.Thread(target=self._bind_and_post, args=(self._bindings,),
                         name='xx-wm-output-power', daemon=True).start()

    def _prepare(self) -> None:
        if self._display is None and not _DISPLAY_AVAILABLE:
            return
        try:
            bindings = wayland_bindings.ensure_protocol_bindings(
                XML_NAMES, CLASS_NAMES)
        except Exception as exc:  # noqa: BLE001 - degrade on any failure
            log.info('output power bindings failed: %s', exc)
            return
        if bindings is None:
            return
        self._bind_and_post(bindings)

    def _bind_and_post(self, bindings: dict[str, type]) -> None:
        """Worker thread: connect and round-trip, then finish on the GTK
        loop. A failed reconnect tries again later."""
        if self._bind(bindings):
            _post_to_main(self._finish_connect)
        elif self._bindings is not None:
            _post_to_main(self._schedule_reconnect)

    def _connect_main(self, bindings: dict[str, type]) -> None:
        """Bind and finish in one go (tests and synchronous callers)."""
        if self._bind(bindings):
            self._finish_connect()

    def _bind(self, bindings: dict[str, type]) -> bool:
        try:
            if self._display is None:
                factory = self._display_factory
                if factory is None:
                    raise RuntimeError('pywayland is not available')
                self._display = factory()  # type: ignore[operator]
            self._output_cls = bindings['WlOutput']
            self._manager_cls = bindings['ZwlrOutputPowerManagerV1']
            self._bindings_seen = bindings
            self._display.connect()  # type: ignore[union-attr]
            # pywayland resolves new_id event args through the live
            # WlRegistry proxy, so it must stay referenced for the
            # connection's lifetime.
            self._registry = self._display.get_registry()  # type: ignore[union-attr]
            self._registry.dispatcher['global'] = self._on_registry_global  # type: ignore[union-attr]
            self._registry.dispatcher['global_remove'] = (  # type: ignore[union-attr]
                self._on_registry_global_remove
            )
            self._display.dispatch(block=True)  # type: ignore[union-attr]
            self._display.flush()  # type: ignore[union-attr]
            if self._manager is None:
                raise RuntimeError(
                    'compositor does not offer zwlr_output_power_manager_v1')
        except Exception as exc:  # noqa: BLE001 - degrade to backlight blanking
            log.info('output power unavailable: %s', exc)
            return False
        return True

    def _finish_connect(self) -> bool:
        """GTK loop: watch the fd and go live; after a reconnect apply the
        mode the shell last asked for; then tell the ready listeners."""
        try:
            self._arm_fd_watch()
        except Exception as exc:  # noqa: BLE001 - degrade to backlight blanking
            log.info('output power unavailable: %s', exc)
            return False
        reconnected = self._bindings is not None
        self._bindings = self._bindings_seen
        with self._lock:
            self._connected = True
            wanted = self._wanted
        if reconnected:
            log.info('output power reconnected')
            self._reconnects = 0
            if wanted is not None:
                self.set_all(wanted)
        self._fire_ready()
        return False

    # -- GLib fd-watch -----------------------------------------------------

    def _arm_fd_watch(self) -> None:
        import gi
        gi.require_version('Gtk', '4.0')
        from gi.repository import GLib

        fd = self._display.get_fd()  # type: ignore[union-attr]
        # PyGObject has no unix_fd_add; newer releases move fd_add_full to
        # GLibUnix and deprecate the GLib alias.
        try:
            from gi.repository import GLibUnix
            fd_add_full = GLibUnix.fd_add_full
        except (ImportError, AttributeError):
            fd_add_full = GLib.unix_fd_add_full
        self._fd_source = fd_add_full(
            GLib.PRIORITY_DEFAULT, fd,
            GLib.IOCondition.IN | GLib.IOCondition.HUP, self._on_fd_ready,
        )

    def _on_fd_ready(self, _fd: int, _cond: int) -> bool:
        from gi.repository import GLib

        if _cond & GLib.IOCondition.HUP:
            # Compositor closed the socket; stop watching instead of spinning
            # on a dead fd forever.
            log.error('output power connection closed (HUP)')
            self._fd_source = None
            self._mark_dead()
            return False
        try:
            # The fd is readable, so a reading dispatch returns at once;
            # dispatch(block=False) only drains already-queued events, never
            # reads the socket, and the still-readable fd spins the loop.
            self._display.dispatch(block=True)  # type: ignore[union-attr]
            self._display.flush()  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 - compositor went away
            log.error('output power dispatch failed: %s', exc)
            self._fd_source = None
            self._mark_dead()
            return False  # dispatch is unrecoverable; stop the fd-watch
        return True  # keep watching

    # -- protocol glue -----------------------------------------------------

    def _on_registry_global(
        self, registry, name: int, interface: str, version: int,
    ) -> None:
        if interface == 'wl_output':
            self._outputs[name] = registry.bind(name, self._output_cls, 1)
            self._ensure_power(name)
        elif interface == 'zwlr_output_power_manager_v1':
            self._manager = registry.bind(
                name, self._manager_cls, min(1, version))
            for pending in list(self._outputs):
                self._ensure_power(pending)

    def _on_registry_global_remove(self, _registry, name: int) -> None:
        self._powers.pop(name, None)
        self._outputs.pop(name, None)

    def _ensure_power(self, name: int) -> None:
        if self._manager is None or name in self._powers:
            return
        output = self._outputs.get(name)
        if output is None:
            return
        power = self._manager.get_output_power(output)  # type: ignore[attr-defined]
        power.dispatcher['mode'] = self._on_mode
        power.dispatcher['failed'] = self._on_failed
        self._powers[name] = power

    def _on_mode(self, _power, _mode: int) -> None:
        self._fire_mode(bool(_mode == MODE_ON))

    def _on_failed(self, power) -> None:
        log.error('output power set_mode failed by compositor; '
                  'falling back to backlight blanking')
        with self._lock:
            self._failed = True
        self._fire_failed()
        try:
            power.destroy()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - object already invalid
            pass
