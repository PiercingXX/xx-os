"""Bottom-edge system gesture, handled by the shell (Android gesture nav).

lisgd classes a swipe only by its length as a fixed third of the screen
(S < 1/3, M 1/3-2/3, L > 2/3) and cannot see a pause, so on the FLX1
every bottom swipe the owner made was a short one (home) and the switcher
never opened. This module replaces lisgd's bottom-edge bindings with the
Android behaviour:

  quick upward flick                         -> home     (swipe_up_short)
  swipe up past PAUSE_MIN_FRAC and hold still
  for PAUSE_MS, or release slowly past
  SLOW_RELEASE_FRAC                          -> switcher (swipe_up_long)
  sideways swipe along the bottom edge       -> previous / next open app

The verbs come from gestures.json through gesture_bindings.resolve_verb,
the same mapping lisgd's bindings use, so a rebound slot behaves the same
whichever path fires it. While the strip is up the shell regenerates
lisgd's bindings without the bottom (B) ones, so nothing fires twice.

The surface is an invisible OVERLAY layer strip STRIP_HEIGHT logical px
tall along the bottom edge. A touch that starts on it stays with it after
the finger leaves the strip (Wayland implicit grab; status_bar.py's pull
relies on the same), so the whole swipe is seen. OVERLAY because phoc
routes input to the overlay layer above a fullscreen view (TOP is not
drawn or hit over fullscreen), which keeps the gesture in fullscreen apps
the way Android does. Exclusive zone -1: apps are never resized, and the
strip stays on the screen's bottom edge even while the on-screen keyboard
reserves space (with zone 0 phoc would lift it above the keyboard, over
the app's input bar).

A tap on the strip is not swallowed: the strip clears its input region
and replays the tap as a pointer click at the same spot through
zwlr_virtual_pointer_v1 (worker thread), so it reaches the app's bottom
bar or the keyboard underneath; then the input region comes back.

Visibility follows the status bar (status_bar.decide_visible): hidden
under the lock screen and the first-boot / welcome wizard.

Headless-safe: without PyGObject only the pure helpers exist; without
layer-shell support create_bottom_gesture() returns None and lisgd keeps
its bottom bindings.
"""
from __future__ import annotations

import logging
import threading
from typing import NamedTuple

_log = logging.getLogger('piercing.bottom_gesture')

_HAS_GTK = True
try:
    import gi
except ImportError:
    _HAS_GTK = False

_HAS_LAYER = False
if _HAS_GTK:
    try:
        gi.require_version('Gtk4LayerShell', '1.0')
        _HAS_LAYER = True
    except (ValueError, ImportError):
        _HAS_LAYER = False
    try:
        gi.require_version('Gtk', '4.0')
        from gi.repository import GLib, Gtk
    except (ValueError, ImportError):
        _HAS_GTK = False
    if _HAS_GTK and _HAS_LAYER:
        try:
            from gi.repository import Gtk4LayerShell as LayerShell
        except ImportError:
            _HAS_LAYER = False

# ---- geometry & thresholds -------------------------------------------

# Strip height in logical px: about 2.7 mm on the FLX1 (1000 px / ~150 mm).
# Thin, so an app's bottom bar stays reachable above it.
STRIP_HEIGHT = 18

# Every threshold is a fraction of the output's logical height, so the
# gesture keeps its physical size across scales. FLX1 (1000 px tall,
# ~6.7 px/mm) values in brackets.
HOME_MIN_FRAC = 0.05       # shortest upward swipe that counts      [50 px, 7.5 mm]
PAUSE_MIN_FRAC = 0.12      # travel before a pause opens recents    [120 px, 18 mm]
SLOW_RELEASE_FRAC = 0.25   # slow release this far up opens recents [250 px, 37 mm]
SIDE_MIN_FRAC = 0.10       # sideways travel for previous / next    [100 px, 15 mm]
SLOP_FRAC = 0.015          # "still" / tap wobble                   [15 px, 2.2 mm]
# Upward release speed (output heights per second) at or above which a
# release is a flick, i.e. home whatever its length.          [900 px/s]
FLICK_SPEED_FRAC = 0.9

PAUSE_MS = 250             # finger nearly still this long -> recents
VELOCITY_WINDOW_MS = 80    # release speed is measured over the last 80 ms
POLL_MS = 50               # pause check cadence while a finger is down

# Strip outcomes. HOME / SWITCHER map through gestures.json; PREVIOUS /
# NEXT switch apps; TAP is replayed to whatever is under the strip.
HOME = 'home'
SWITCHER = 'switcher'
PREVIOUS = 'previous'
NEXT = 'next'
TAP = 'tap'

# Outcome -> gestures.json slot whose action it runs.
ACTION_SLOTS = {
    HOME: 'swipe_up_short',
    SWITCHER: 'swipe_up_long',
}


class Thresholds(NamedTuple):
    home_min: float
    pause_min: float
    slow_release: float
    side_min: float
    slop: float
    flick_speed: float  # px per ms


def thresholds(height: float) -> Thresholds:
    """Pixel thresholds for an output ``height`` logical px tall."""
    h = max(1.0, float(height))
    return Thresholds(
        home_min=h * HOME_MIN_FRAC,
        pause_min=h * PAUSE_MIN_FRAC,
        slow_release=h * SLOW_RELEASE_FRAC,
        side_min=h * SIDE_MIN_FRAC,
        slop=h * SLOP_FRAC,
        flick_speed=h * FLICK_SPEED_FRAC / 1000.0,
    )


# ---- classifier (pure) ------------------------------------------------
# A drag is a list of samples (t_ms, dx, dy): offsets from the touch-down
# point, dy negative upward (GTK drag offsets), first sample (t0, 0, 0).

def _up(sample) -> float:
    return -float(sample[2])


def _sample_at(samples, t_ms: float):
    """Latest sample at or before ``t_ms`` (the first one if none is)."""
    found = samples[0]
    for sample in samples:
        if sample[0] <= t_ms:
            found = sample
        else:
            break
    return found


def release_speed(samples, t_end: float | None = None) -> float:
    """Upward speed (px/ms) over the last VELOCITY_WINDOW_MS of the drag."""
    if not samples:
        return 0.0
    last = samples[-1]
    t_end = last[0] if t_end is None else t_end
    ref = _sample_at(samples, t_end - VELOCITY_WINDOW_MS)
    if ref is last:
        # Only one sample in reach: measure from the one before it.
        index = samples.index(last)
        ref = samples[index - 1] if index > 0 else last
    span = max(1.0, float(t_end) - float(ref[0]))
    return (_up(last) - _up(ref)) / span


def _travel(samples) -> float:
    return max((max(abs(s[1]), abs(s[2])) for s in samples), default=0.0)


def is_pause(samples, now_ms: float, height: float) -> bool:
    """True once the finger has swiped up past PAUSE_MIN_FRAC (upward
    dominant) and then held nearly still for PAUSE_MS."""
    if not samples:
        return False
    th = thresholds(height)
    last = samples[-1]
    if _up(last) < th.pause_min or abs(last[1]) > _up(last):
        return False
    start = now_ms - PAUSE_MS
    if start < samples[0][0]:
        return False
    # The position at the window's start and every move inside it must
    # stay within the slop of where the finger is now.
    window = [_sample_at(samples, start)] + [
        s for s in samples if start < s[0] <= now_ms]
    return all(abs(s[1] - last[1]) <= th.slop and abs(s[2] - last[2]) <= th.slop
               for s in window)


def classify_release(samples, height: float,
                     t_end: float | None = None) -> str | None:
    """Outcome of a drag released at the last sample: HOME, SWITCHER,
    PREVIOUS, NEXT, TAP, or None (too short / ambiguous)."""
    if not samples:
        return None
    th = thresholds(height)
    if _travel(samples) <= th.slop:
        return TAP
    last = samples[-1]
    dx, up = float(last[1]), _up(last)
    if abs(dx) >= th.side_min and abs(dx) > 2.0 * max(0.0, up):
        # Finger moved right: the app to the left in recents.
        return PREVIOUS if dx > 0 else NEXT
    if up < th.home_min or abs(dx) > up:
        return None
    if release_speed(samples, t_end) >= th.flick_speed:
        return HOME
    if up >= th.slow_release:
        return SWITCHER
    return HOME


class BottomSwipe:
    """One drag on the strip, timestamped in ms. ``poll`` reports the
    live pause (SWITCHER, once); after that the release is ignored."""

    def __init__(self, height: float, t_ms: float) -> None:
        self.height = float(height)
        self.samples: list[tuple[float, float, float]] = [(float(t_ms), 0.0, 0.0)]
        self.fired: str | None = None

    def update(self, t_ms: float, dx: float, dy: float) -> None:
        self.samples.append((float(t_ms), float(dx), float(dy)))

    def poll(self, t_ms: float) -> str | None:
        if self.fired is None and is_pause(self.samples, t_ms, self.height):
            self.fired = SWITCHER
            return SWITCHER
        return None

    def release(self, t_ms: float, dx: float, dy: float) -> str | None:
        self.update(t_ms, dx, dy)
        if self.fired is not None:
            return None
        return classify_release(self.samples, self.height, t_ms)


# ---- wiring helpers (pure) ---------------------------------------------

def verb_for(action: str, gesture_config) -> str | None:
    """IPC verb a HOME / SWITCHER outcome fires: the gestures.json slot
    resolved exactly as lisgd's bindings resolve it."""
    slot = ACTION_SLOTS.get(action)
    if slot is None:
        return None
    from gesture_bindings import resolve_verb
    return resolve_verb(gesture_config, slot)


def adjacent_toplevel(toplevels, action: str):
    """The open app PREVIOUS / NEXT switches to: the neighbour of the
    activated one in recents order (left / right card). No wrap-around,
    and nothing when no app is activated."""
    items = list(toplevels or ())
    index = next((i for i, t in enumerate(items)
                  if getattr(t, 'activated', False)), None)
    if index is None:
        return None
    step = -1 if action == PREVIOUS else 1 if action == NEXT else 0
    target = index + step
    if step == 0 or not 0 <= target < len(items):
        return None
    return items[target]


def decide_visible(locked: bool, wizard_visible: bool) -> bool:
    """Same rule as the status bar: off under the lock screen and the
    first-boot / welcome wizard, on over home and apps."""
    from status_bar import decide_visible as bar_rule
    return bar_rule(locked, wizard_visible)


def configure_layer(window, height: int, layer_shell) -> None:
    """OVERLAY strip anchored BOTTOM + LEFT + RIGHT, exclusive zone -1
    (never resizes apps, stays on the edge under the keyboard), no
    keyboard focus."""
    layer_shell.init_for_window(window)
    set_namespace = getattr(layer_shell, 'set_namespace', None)
    if callable(set_namespace):
        set_namespace(window, 'xx-wm-bottom-gesture')
    layer_shell.set_layer(window, layer_shell.Layer.OVERLAY)
    layer_shell.set_anchor(window, layer_shell.Edge.BOTTOM, True)
    layer_shell.set_anchor(window, layer_shell.Edge.LEFT, True)
    layer_shell.set_anchor(window, layer_shell.Edge.RIGHT, True)
    layer_shell.set_anchor(window, layer_shell.Edge.TOP, False)
    layer_shell.set_keyboard_mode(window, layer_shell.KeyboardMode.NONE)
    layer_shell.set_exclusive_zone(window, -1)
    window.set_default_size(-1, int(height))
    window.set_size_request(-1, int(height))


def output_size(display) -> tuple[int, int] | None:
    """(width, height) logical px of the first monitor, or None."""
    try:
        monitors = display.get_monitors()
        monitor = monitors.get_item(0) if monitors.get_n_items() else None
        if monitor is None:
            return None
        geometry = monitor.get_geometry()
    except Exception:
        return None
    if geometry.width <= 0 or geometry.height <= 0:
        return None
    return int(geometry.width), int(geometry.height)


def replay_tap(x: float, y: float, width: int, height: int) -> bool:
    """Click at output-logical (x, y) through a one-shot virtual pointer.
    Blocks on the Wayland connection: worker thread only."""
    from capture import VirtualScroller
    pointer = VirtualScroller()
    if not pointer.connect():
        return False
    try:
        return pointer.click(x, y, width, height)
    finally:
        pointer.close()


# Not fully transparent: GTK draws nothing for an all-transparent
# window, never commits a buffer, and the strip never maps. One 255th of
# black is below what the eye (or an 8-bit panel) shows.
CSS = b"""
window.xx-bottom-gesture {
    background: rgba(0, 0, 0, 0.004);
}
"""

# Restore the input region this long after the replayed click went out:
# the click must reach phoc while the strip is still see-through.
_RESTORE_MS = 120
# Wait for the cleared input region to be committed before clicking.
_CLEAR_SETTLE_MS = 40


if _HAS_GTK:

    def _now_ms() -> float:
        return GLib.get_monotonic_time() / 1000.0

    class BottomGestureStrip(Gtk.Window):
        """The invisible layer-shell strip; ``on_action(outcome)`` gets
        HOME / SWITCHER / PREVIOUS / NEXT on the main loop."""

        def __init__(self, on_action=None, replay=replay_tap) -> None:
            super().__init__()
            self._on_action = on_action
            self._replay = replay
            self._swipe: BottomSwipe | None = None
            self._origin = (0.0, 0.0)
            self._poll_src = 0
            self._size = (0, 0)
            # Resizable on purpose: a non-resizable window is sized to its
            # natural width (1 px here) and phoc never shows the strip.
            self.set_decorated(False)
            self.add_css_class('xx-bottom-gesture')
            provider = Gtk.CssProvider()
            provider.load_from_data(CSS)
            # Above the shell's theme sheet (APPLICATION + 1), whose
            # `window { background-color }` would paint the strip solid.
            Gtk.StyleContext.add_provider_for_display(
                self.get_display(), provider,
                Gtk.STYLE_PROVIDER_PRIORITY_USER)
            configure_layer(self, STRIP_HEIGHT, LayerShell)
            # An empty child has no natural width and GTK then never
            # computes a size for the surface (nothing maps): ask for 1 px.
            body = Gtk.Box()
            body.set_size_request(1, STRIP_HEIGHT)
            self.set_child(body)

            drag = Gtk.GestureDrag.new()
            drag.set_touch_only(False)
            drag.connect('drag-begin', self._on_drag_begin)
            drag.connect('drag-update', self._on_drag_update)
            drag.connect('drag-end', self._on_drag_end)
            self.add_controller(drag)

        # -- visibility ------------------------------------------------
        def sync_visibility(self, locked: bool, wizard_visible: bool) -> None:
            visible = decide_visible(locked, wizard_visible)
            if visible == bool(self.get_visible()):
                return
            if visible:
                self.present()
            else:
                self._cancel()
                self.set_visible(False)

        def raise_strip(self) -> None:
            """Map again so the strip is the topmost OVERLAY surface
            (phoc stacks a layer by map order): called after another
            overlay (recents) maps over the bottom edge."""
            if not self.get_visible():
                return
            self._cancel()
            self.set_visible(False)
            self.present()

        # -- gesture ---------------------------------------------------
        def _on_drag_begin(self, _gesture, x, y) -> None:
            size = output_size(self.get_display()) or (
                self.get_width(), 1000)
            self._size = size
            self._origin = (float(x), float(y))
            self._swipe = BottomSwipe(size[1], _now_ms())
            if not self._poll_src:
                self._poll_src = GLib.timeout_add(POLL_MS, self._on_poll)

        def _on_drag_update(self, _gesture, dx, dy) -> None:
            if self._swipe is not None:
                self._swipe.update(_now_ms(), dx, dy)

        def _on_poll(self) -> bool:
            swipe = self._swipe
            if swipe is None:
                self._poll_src = 0
                return GLib.SOURCE_REMOVE
            if swipe.poll(_now_ms()) is not None:
                self._fire(SWITCHER)
                self._poll_src = 0
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        def _on_drag_end(self, _gesture, dx, dy) -> None:
            swipe = self._swipe
            self._cancel()
            if swipe is None:
                return
            outcome = swipe.release(_now_ms(), dx, dy)
            _log.info('strip drag %+.0f,%+.0f -> %s', dx, dy, outcome)
            if outcome == TAP:
                self._pass_tap()
            elif outcome is not None:
                self._fire(outcome)

        def _cancel(self) -> None:
            self._swipe = None
            if self._poll_src:
                GLib.source_remove(self._poll_src)
                self._poll_src = 0

        def _fire(self, outcome: str) -> None:
            if callable(self._on_action):
                self._on_action(outcome)

        # -- tap pass-through ------------------------------------------
        def _pass_tap(self) -> None:
            width, height = self._size
            if width <= 0 or height <= 0:
                return
            x = self._origin[0]
            y = height - self.get_height() + self._origin[1]
            if not self._set_passthrough(True):
                return
            GLib.timeout_add(_CLEAR_SETTLE_MS, self._start_replay,
                             x, y, width, height)

        def _start_replay(self, x, y, width, height) -> bool:
            def _work() -> None:
                try:
                    self._replay(x, y, width, height)
                except Exception as exc:
                    _log.warning('strip tap replay failed: %s', exc)
                GLib.timeout_add(_RESTORE_MS, self._end_replay)
            threading.Thread(target=_work, name='xx-strip-tap',
                             daemon=True).start()
            return GLib.SOURCE_REMOVE

        def _end_replay(self) -> bool:
            self._set_passthrough(False)
            return GLib.SOURCE_REMOVE

        def _set_passthrough(self, on: bool) -> bool:
            surface = self.get_surface()
            if surface is None:
                return False
            try:
                import cairo
                surface.set_input_region(
                    cairo.Region() if on else cairo.Region(
                        cairo.RectangleInt(0, 0, max(1, self.get_width()),
                                           max(1, self.get_height()))))
            except Exception:
                return False
            # A frame commits the new region (the strip draws nothing).
            self.queue_draw()
            return True


def create_bottom_gesture(on_action=None):
    """A BottomGestureStrip on a layer-shell compositor, else None (no
    GTK, or a nested dev session; lisgd then keeps the bottom edge)."""
    if not (_HAS_GTK and _HAS_LAYER):
        return None
    try:
        if not LayerShell.is_supported():
            return None
    except Exception:
        return None
    return BottomGestureStrip(on_action=on_action)
