"""The phone screen as a trackpad for the monitor (docked, river/xxwm).

Touches on the handset's black surface become pointer input on the
monitor through zwlr_virtual_pointer_v1 (river offers version 2):

- one finger moves the pointer (relative, with acceleration)
- tap = left click, two-finger tap = right click, three-finger tap =
  middle click
- two-finger drag scrolls (the content follows the fingers)
- tap, then touch again and move = drag with the left button held;
  tap twice = double click

``TouchpadGestures`` is the recognizer: pure, fed touch points with
millisecond times, it calls a sink (``move``, ``button``, ``scroll``,
``scroll_stop``). ``PointerSink`` keeps the pointer position inside the
monitor and sends absolute motion through ``VirtualPointer``, so the
pointer can never wander onto the dark handset.
"""
from __future__ import annotations

import logging
import math
import threading
from shell_log import log_exc

log = logging.getLogger('trackpad')

BTN_LEFT = 0x110
BTN_RIGHT = 0x111
BTN_MIDDLE = 0x112
AXIS_VERTICAL = 0
AXIS_HORIZONTAL = 1
AXIS_SOURCE_FINGER = 1

# Movement under this (logical px) is still a tap.
SLOP = 8.0
# A touch shorter than this that did not move is a tap.
TAP_MS = 200
# Multi-finger taps get a little longer: fingers rarely land together.
MULTI_TAP_MS = 300
# A touch this soon after a tap, near it, holds the button (drag, or
# the second click of a double click).
DRAG_WINDOW_MS = 300
DRAG_RADIUS = 40.0
# Pointer speed: BASE_GAIN at slow speed, rising with finger speed
# (px/ms) up to MAX_GAIN. A 360 px wide phone screen has to cover a
# monitor several times its width without the finger running out.
BASE_GAIN = 1.6
MAX_GAIN = 5.0
ACCEL_FROM = 0.25
ACCEL_SLOPE = 2.4
SCROLL_GAIN = 1.2


def gain(speed: float) -> float:
    """Pointer gain for a finger speed in px/ms."""
    if speed <= ACCEL_FROM:
        return BASE_GAIN
    return min(MAX_GAIN, BASE_GAIN * (1.0 + (speed - ACCEL_FROM) * ACCEL_SLOPE))


class TouchpadGestures:
    """Touch sequences in, pointer actions out. Times are milliseconds;
    coordinates are surface-local logical px. ``sink`` offers
    ``move(dx, dy, t)``, ``button(code, pressed, t)``,
    ``scroll(dx, dy, t)`` and ``scroll_stop(t)``."""

    def __init__(self, sink) -> None:
        self._sink = sink
        self._points: dict = {}
        self._reset_gesture()
        self._last_tap: tuple[int, float, float] | None = None

    def _reset_gesture(self) -> None:
        self._max_fingers = 0
        self._start_t = 0
        self._moved = False
        self._scrolling = False
        self._held = False
        self._anchor: tuple[float, float] | None = None
        self._last_t = 0
        self._centroid: tuple[float, float] | None = None
        self._origin: tuple[float, float] | None = None

    @property
    def fingers(self) -> int:
        return len(self._points)

    def begin(self, seq, x: float, y: float, t: int) -> None:
        if not self._points:
            self._reset_gesture()
            self._start_t = t
            self._anchor = (x, y)
            last = self._last_tap
            if (last is not None and t - last[0] <= DRAG_WINDOW_MS
                    and math.hypot(x - last[1], y - last[2]) <= DRAG_RADIUS):
                self._held = True
                self._last_tap = None
                self._sink.button(BTN_LEFT, True, t)
        elif self._held and not self._moved:
            # A second finger during a tap-hold: it was a two-finger
            # gesture after all, not a drag.
            self._held = False
            self._sink.button(BTN_LEFT, False, t)
        self._points[seq] = (x, y)
        self._max_fingers = max(self._max_fingers, len(self._points))
        self._last_t = t
        self._centroid = self._center()
        if not self._scrolling:
            self._origin = self._centroid

    def update(self, seq, x: float, y: float, t: int) -> None:
        old = self._points.get(seq)
        if old is None or old == (x, y):
            return
        self._points[seq] = (x, y)
        if self._max_fingers == 1:
            self._one_finger(old, (x, y), t)
        elif len(self._points) >= 2:
            self._two_fingers(t)
        self._last_t = t

    def end(self, seq, x: float, y: float, t: int) -> None:
        if seq not in self._points:
            return
        self.update(seq, x, y, t)
        self._points.pop(seq, None)
        if self._points:
            # Lifting one finger of several: the centroid jumps, so the
            # next update must not read it as movement.
            self._centroid = self._center()
            if not self._scrolling:
                self._origin = self._centroid
            return
        self._finish(t)

    def cancel(self, seq, t: int) -> None:
        """The compositor took the touch away: release, never click."""
        if self._points.pop(seq, None) is None or self._points:
            return
        if self._held:
            self._sink.button(BTN_LEFT, False, t)
        if self._scrolling:
            self._sink.scroll_stop(t)
        self._last_tap = None
        self._reset_gesture()

    # -- internals -----------------------------------------------------

    def _center(self) -> tuple[float, float] | None:
        if not self._points:
            return None
        xs = [p[0] for p in self._points.values()]
        ys = [p[1] for p in self._points.values()]
        return (sum(xs) / len(xs), sum(ys) / len(ys))

    def _one_finger(self, old, new, t: int) -> None:
        if not self._moved:
            ax, ay = self._anchor or old
            if math.hypot(new[0] - ax, new[1] - ay) < SLOP:
                return
            self._moved = True
            old = (ax, ay)
        dx, dy = new[0] - old[0], new[1] - old[1]
        dt = max(1, t - self._last_t)
        g = gain(math.hypot(dx, dy) / dt)
        self._sink.move(dx * g, dy * g, t)

    def _two_fingers(self, t: int) -> None:
        center = self._center()
        if center is None:
            return
        if not self._scrolling:
            ox, oy = self._origin or center
            if math.hypot(center[0] - ox, center[1] - oy) < SLOP:
                return
            self._scrolling = True
            self._moved = True
            self._centroid = (ox, oy)
        prev = self._centroid or center
        self._centroid = center
        dx, dy = center[0] - prev[0], center[1] - prev[1]
        # Natural scrolling: the content follows the fingers.
        self._sink.scroll(-dx * SCROLL_GAIN, -dy * SCROLL_GAIN, t)

    def _finish(self, t: int) -> None:
        duration = t - self._start_t
        fingers = self._max_fingers
        if self._held:
            self._sink.button(BTN_LEFT, False, t)
            self._reset_gesture()
            return
        if self._scrolling:
            self._sink.scroll_stop(t)
        elif not self._moved:
            if fingers == 1 and duration <= TAP_MS:
                self._click(BTN_LEFT, t)
                self._last_tap = (t, *(self._anchor or (0.0, 0.0)))
                self._reset_gesture()
                return
            if fingers in (2, 3) and duration <= MULTI_TAP_MS:
                self._click(BTN_RIGHT if fingers == 2 else BTN_MIDDLE, t)
        self._last_tap = None
        self._reset_gesture()

    def _click(self, code: int, t: int) -> None:
        self._sink.button(code, True, t)
        self._sink.button(code, False, t + 1)


def layout_bounds(rects) -> tuple[int, int, int, int]:
    """(x, y, w, h) of the box around every output rect (x, y, w, h)."""
    rects = [r for r in rects if r[2] > 0 and r[3] > 0]
    if not rects:
        return (0, 0, 1, 1)
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[0] + r[2] for r in rects)
    y1 = max(r[1] + r[3] for r in rects)
    return (x0, y0, x1 - x0, y1 - y0)


class PointerSink:
    """Gesture sink that keeps its own pointer position on ``target``
    (the monitor rect, global logical px) and sends absolute motion over
    the whole output layout ``bounds``. ``pointer`` offers
    ``motion_absolute(t, x, y, w, h)``, ``button(t, code, state)``,
    ``axis_source``, ``axis(t, axis, value)``, ``axis_stop(t, axis)``,
    ``frame()``; ``flush`` pushes them to the compositor."""

    def __init__(self, pointer, flush=None) -> None:
        self._pointer = pointer
        self._flush = flush or (lambda: None)
        self._target = (0, 0, 1, 1)
        self._bounds = (0, 0, 1, 1)
        self.x = 0.0
        self.y = 0.0

    def set_area(self, target, bounds) -> None:
        """Pointer area ``target`` within ``bounds``; the pointer starts
        in the middle of the area (where xxwm warps it on dock)."""
        moved = tuple(target) != self._target
        self._target = tuple(target)
        self._bounds = tuple(bounds)
        if moved:
            self.x = target[0] + target[2] / 2.0
            self.y = target[1] + target[3] / 2.0

    def _clamp(self) -> None:
        x, y, w, h = self._target
        self.x = min(max(self.x, x), x + w - 1)
        self.y = min(max(self.y, y), y + h - 1)

    def _send_position(self, t: int) -> None:
        bx, by, bw, bh = self._bounds
        self._pointer.motion_absolute(
            t & 0xFFFFFFFF, max(0, int(round(self.x - bx))),
            max(0, int(round(self.y - by))), max(1, bw), max(1, bh))

    def move(self, dx: float, dy: float, t: int) -> None:
        self.x += dx
        self.y += dy
        self._clamp()
        self._send_position(t)
        self._pointer.frame()
        self._flush()

    def button(self, code: int, pressed: bool, t: int) -> None:
        # Absolute first: another pointer (a mouse) may have moved it.
        self._send_position(t)
        self._pointer.frame()
        self._pointer.button(t & 0xFFFFFFFF, code, 1 if pressed else 0)
        self._pointer.frame()
        self._flush()

    def scroll(self, dx: float, dy: float, t: int) -> None:
        self._pointer.axis_source(AXIS_SOURCE_FINGER)
        if dy:
            self._pointer.axis(t & 0xFFFFFFFF, AXIS_VERTICAL, float(dy))
        if dx:
            self._pointer.axis(t & 0xFFFFFFFF, AXIS_HORIZONTAL, float(dx))
        self._pointer.frame()
        self._flush()

    def scroll_stop(self, t: int) -> None:
        self._pointer.axis_source(AXIS_SOURCE_FINGER)
        self._pointer.axis_stop(t & 0xFFFFFFFF, AXIS_VERTICAL)
        self._pointer.axis_stop(t & 0xFFFFFFFF, AXIS_HORIZONTAL)
        self._pointer.frame()
        self._flush()


class VirtualPointer:
    """A long-lived zwlr_virtual_pointer_v1. ``connect_async(done)``
    generates the bindings and connects in a worker thread, then calls
    ``done(ok)`` on the GTK main loop; requests are then sent from the
    main loop (they never block: a flush only writes). The fd watch
    reads protocol errors and the compositor hanging up."""

    def __init__(self, bind_fn=None, display_cls=None) -> None:
        self._bind_fn = bind_fn
        self._display_cls = display_cls
        self._display = None
        self._registry = None
        self._pointer = None
        self._source = None

    @property
    def pointer(self):
        return self._pointer

    def flush(self) -> None:
        display = self._display
        if display is None:
            return
        try:
            display.flush()
        except Exception as exc:  # noqa: BLE001 - compositor went away
            log.warning('virtual pointer flush failed: %s', exc)
            self.close()

    def connect_async(self, done) -> None:
        def work() -> None:
            ok = self._connect()
            try:
                from gi.repository import GLib
                GLib.idle_add(lambda: (self._connected(ok, done), False)[1])
            except Exception:
                self._connected(ok, done)

        threading.Thread(target=work, daemon=True,
                         name='xx-wm-trackpad').start()

    def _connected(self, ok: bool, done) -> None:
        if ok:
            self._arm_fd_watch()
        done(ok)

    def _connect(self) -> bool:
        try:
            import wayland_bindings
            bind_fn = self._bind_fn or wayland_bindings.ensure_protocol_bindings
            bindings = bind_fn(wayland_bindings.VIRTUAL_POINTER_XML_NAMES,
                               wayland_bindings.VIRTUAL_POINTER_CLASS_NAMES)
            if not bindings:
                return False
            display_cls = self._display_cls
            if display_cls is None:
                from pywayland.client import Display
                display_cls = Display
            display = display_cls()
            display.connect()
        except Exception as exc:  # noqa: BLE001 - no trackpad, never a crash
            log.info('virtual pointer unavailable: %s', exc)
            return False
        # pywayland resolves new_id event args through the live registry
        # proxy: keep it referenced for the connection's lifetime.
        self._registry = display.get_registry()
        state: dict = {}

        def on_global(reg, name: int, interface: str, version: int) -> None:
            if interface == 'wl_seat' and 'seat' not in state:
                state['seat'] = reg.bind(name, bindings['WlSeat'], 1)
            elif interface == 'zwlr_virtual_pointer_manager_v1':
                state['manager'] = reg.bind(
                    name, bindings['ZwlrVirtualPointerManagerV1'],
                    min(2, version))

        self._registry.dispatcher['global'] = on_global
        try:
            display.roundtrip()
            seat, manager = state.get('seat'), state.get('manager')
            if seat is None or manager is None:
                log.info('compositor offers no virtual pointer')
                display.disconnect()
                self._registry = None
                return False
            self._pointer = manager.create_virtual_pointer(seat)
            display.flush()
        except Exception as exc:  # noqa: BLE001
            log.info('virtual pointer setup failed: %s', exc)
            self._registry = None
            return False
        self._display = display
        return True

    def _arm_fd_watch(self) -> None:
        try:
            from gi.repository import GLib
            try:
                from gi.repository import GLibUnix
                fd_add_full = GLibUnix.fd_add_full
            except (ImportError, AttributeError):
                fd_add_full = GLib.unix_fd_add_full
            self._source = fd_add_full(
                GLib.PRIORITY_DEFAULT, self._display.get_fd(),
                GLib.IOCondition.IN | GLib.IOCondition.HUP, self._on_fd)
        except Exception as exc:  # noqa: BLE001
            log.info('virtual pointer fd watch failed: %s', exc)

    def _on_fd(self, _fd, cond) -> bool:
        from gi.repository import GLib
        if cond & GLib.IOCondition.HUP:
            log.warning('virtual pointer: compositor hung up')
            self._source = None
            self.close()
            return False
        try:
            # A readable fd: a reading dispatch returns at once.
            self._display.dispatch(block=True)
        except Exception as exc:  # noqa: BLE001
            log.warning('virtual pointer dispatch failed: %s', exc)
            self._source = None
            self.close()
            return False
        return True

    def close(self) -> None:
        source, self._source = self._source, None
        if source is not None:
            try:
                from gi.repository import GLib
                GLib.source_remove(source)
            except Exception:
                log_exc('trackpad.close: GLib.source_remove')
        pointer, self._pointer = self._pointer, None
        display, self._display = self._display, None
        self._registry = None
        if pointer is not None:
            try:
                pointer.destroy()
            except Exception:
                pass
        if display is not None:
            try:
                display.flush()
                display.disconnect()
            except Exception:
                log_exc('trackpad.close: display.flush')
