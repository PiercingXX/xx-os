"""Docked mode on river: what the phone does while a monitor is connected.

xxwm moves the phone's work to the monitor when one appears and back
when it goes (wm/README.md); this follows the same events over its
control socket (``subscribe state``, the stream the docked top bar
follows too) and handles the phone side, by
the ``docked_phone_screen`` setting:

- ``trackpad`` (default): a black full-screen surface covers the
  handset and turns touches into pointer input on the monitor
  (trackpad.py); the backlight goes to 0 (the panel stays powered, so
  touches still arrive); the on-screen keyboard is turned off.
- ``second-screen``: the handset stays a normal handset output.
- ``off``: the handset output is powered off and covered (touches do
  nothing); the on-screen keyboard is turned off.

While docked, calls, banners, the power menu and the lock screen show
on the monitor. Undocking undoes all of it.

Socket I/O runs on a worker thread; everything else on the GTK main
loop. The pure parts (parse_outputs, plan_for, mode_of) carry the
logic and are what the tests pin.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import weakref
from pathlib import Path
from typing import NamedTuple

log = logging.getLogger('dock')

CONFIG_KEY = 'docked_phone_screen'
TRACKPAD = 'trackpad'
SECOND_SCREEN = 'second-screen'
OFF = 'off'
MODES = (TRACKPAD, SECOND_SCREEN, OFF)
DEFAULT_MODE = TRACKPAD
MODE_LABELS = {TRACKPAD: 'Trackpad', SECOND_SCREEN: 'Second screen',
               OFF: 'Off'}

_OSK_SCHEMA = 'org.gnome.desktop.a11y.applications'
_OSK_KEY = 'screen-keyboard-enabled'
_RETRY_MIN = 0.5
_RETRY_MAX = 5.0


def mode_of(config) -> str:
    """The configured mode; anything unknown reads as the default."""
    try:
        get = getattr(config, 'get', None)
        value = get(CONFIG_KEY, DEFAULT_MODE) if callable(get) else (
            config.data.get(CONFIG_KEY, DEFAULT_MODE))
    except Exception:
        value = DEFAULT_MODE
    return value if value in MODES else DEFAULT_MODE


class Output(NamedTuple):
    x: int
    y: int
    w: int
    h: int
    handset: bool
    wl_output: int | None
    name: str = ''

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.w, self.h)


class Layout(NamedTuple):
    handset: Output | None
    monitors: tuple

    @property
    def docked(self) -> bool:
        return bool(self.monitors)

    @property
    def monitor(self) -> Output | None:
        return self.monitors[0] if self.monitors else None

    @property
    def rects(self) -> list:
        outs = list(self.monitors)
        if self.handset is not None:
            outs.append(self.handset)
        return [o.rect for o in outs]


UNDOCKED = Layout(None, ())


def _int(value, default=0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_outputs(items) -> Layout:
    """xxwm's ``outputs`` answer as a Layout: the first handset output,
    and every other (desktop) output. Docked = at least one desktop
    output. Junk entries are skipped."""
    handset = None
    monitors = []
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, dict):
            continue
        w, h = _int(item.get('w')), _int(item.get('h'))
        if w <= 0 or h <= 0:
            continue
        name = item.get('wl_output')
        connector = item.get('name')
        out = Output(_int(item.get('x')), _int(item.get('y')), w, h,
                     item.get('handset') is True,
                     name if isinstance(name, int)
                     and not isinstance(name, bool) else None,
                     connector if isinstance(connector, str) else '')
        if out.handset:
            if handset is None:
                handset = out
        else:
            monitors.append(out)
    return Layout(handset, tuple(monitors))


class Plan(NamedTuple):
    cover: bool         # black full-screen surface on the handset
    trackpad: bool      # the cover turns touches into pointer input
    dark: bool          # handset backlight at 0
    power_off: bool     # handset output powered off
    osk_off: bool       # on-screen keyboard turned off
    on_monitor: bool    # calls, banners, menus, lock UI on the monitor


IDLE = Plan(False, False, False, False, False, False)


def plan_for(mode: str, docked: bool, lock_ui_on_handset: bool = False,
             has_handset: bool = True) -> Plan:
    """What the phone does in ``mode``. While a lock started before the
    dock still has its UI on the handset, the handset stays lit until
    the unlock (that is where the PIN goes)."""
    if not docked:
        return IDLE
    if mode not in MODES:
        mode = DEFAULT_MODE
    if mode == SECOND_SCREEN or not has_handset:
        return Plan(False, False, False, False, False, True)
    lit = lock_ui_on_handset
    return Plan(cover=True, trackpad=mode == TRACKPAD,
                dark=not lit, power_off=mode == OFF and not lit,
                osk_off=True, on_monitor=True)


def gdk_monitor_for(display, out: Output | None):
    """The GdkMonitor for ``out``: by connector name, else by position
    and size; None when GDK does not know it (yet)."""
    if display is None or out is None:
        return None
    try:
        model = display.get_monitors()
        monitors = [model.get_item(i) for i in range(model.get_n_items())]
    except Exception:
        return None
    if out.name:
        for monitor in monitors:
            try:
                if monitor.get_connector() == out.name:
                    return monitor
            except Exception:
                pass
    for monitor in monitors:
        try:
            geo = monitor.get_geometry()
        except Exception:
            continue
        if (geo.x, geo.y, geo.width, geo.height) == out.rect:
            return monitor
    return None


# -- surfaces that follow the dock ---------------------------------------

_current: DockController | None = None
_followers: weakref.WeakSet = weakref.WeakSet()


def current() -> DockController | None:
    return _current


def surface_monitor():
    """The GdkMonitor shell surfaces (calls, banners, menus, lock UI)
    belong on now, or None for the compositor default (the handset)."""
    controller = _current
    return controller.surface_monitor() if controller is not None else None


def sync_surface(window) -> None:
    """Called by a surface before it shows: register it and put it on the
    right output (its ``follow_dock(monitor)`` does the placing)."""
    try:
        _followers.add(window)
    except TypeError:
        return
    _follow(window, surface_monitor())


def _follow(window, monitor) -> None:
    if getattr(window, '_dock_monitor', None) is monitor and getattr(
            window, '_dock_placed', False):
        return
    follow = getattr(window, 'follow_dock', None)
    if not callable(follow):
        return
    try:
        follow(monitor)
        window._dock_monitor = monitor
        window._dock_placed = True
    except Exception as exc:  # noqa: BLE001 - a surface stays where it was
        log.warning('dock: %s could not move: %s',
                    type(window).__name__, exc)


def place_layer_surface(window, monitor, phone_anchors, desk_anchors,
                        desk_size=None, phone_margin: int = 0,
                        desk_margin: int = 16, phone_exclusive=None,
                        desk_exclusive=None, layer_shell=None) -> None:
    """Put a layer-shell window on ``monitor`` (None: the compositor's
    default output, the handset) with the anchors, margins and exclusive
    zone for that place. Anchors are edge names ('top', 'bottom',
    'left', 'right'). On the monitor the surface is a card of
    ``desk_size`` (None keeps the window's size)."""
    if layer_shell is None:
        try:
            from gi.repository import Gtk4LayerShell as layer_shell
        except (ImportError, ValueError):
            return
    is_layer = getattr(layer_shell, 'is_layer_window', None)
    if callable(is_layer) and not is_layer(window):
        return
    edges = {'top': layer_shell.Edge.TOP, 'bottom': layer_shell.Edge.BOTTOM,
             'left': layer_shell.Edge.LEFT, 'right': layer_shell.Edge.RIGHT}
    desk = monitor is not None
    anchors = desk_anchors if desk else phone_anchors
    margin = desk_margin if desk else phone_margin
    for name, edge in edges.items():
        layer_shell.set_anchor(window, edge, name in anchors)
        layer_shell.set_margin(window, edge, margin if name in anchors else 0)
    exclusive = desk_exclusive if desk else phone_exclusive
    if exclusive is not None:
        layer_shell.set_exclusive_zone(window, exclusive)
    if desk_size is not None:
        if desk:
            if getattr(window, '_dock_phone_size', None) is None:
                window._dock_phone_size = window.get_default_size()
            window.set_default_size(*desk_size)
        elif getattr(window, '_dock_phone_size', None) is not None:
            window.set_default_size(*window._dock_phone_size)
    layer_shell.set_monitor(window, monitor)


# -- the handset cover ----------------------------------------------------

def _make_cover_window(on_touch):
    """The black full-screen OVERLAY surface for the handset. Touch
    events go to ``on_touch(kind, seq, x, y, t)``; kind is 'begin',
    'update', 'end' or 'cancel'."""
    import gi
    gi.require_version('Gtk', '4.0')
    from gi.repository import Gtk
    try:
        gi.require_version('Gtk4LayerShell', '1.0')
        from gi.repository import Gtk4LayerShell as LayerShell
    except (ImportError, ValueError):
        LayerShell = None

    window = Gtk.Window(title='XX-WM trackpad')
    window.set_decorated(False)
    _black(window)
    if LayerShell is not None and LayerShell.is_supported():
        LayerShell.init_for_window(window)
        LayerShell.set_namespace(window, 'xx-wm-trackpad')
        LayerShell.set_layer(window, LayerShell.Layer.OVERLAY)
        for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM,
                     LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
            LayerShell.set_anchor(window, edge, True)
        LayerShell.set_exclusive_zone(window, -1)
        LayerShell.set_keyboard_mode(window, LayerShell.KeyboardMode.NONE)
    body = Gtk.Box()
    body.set_hexpand(True)
    body.set_vexpand(True)
    window.set_child(body)
    attach_touch(window, on_touch)
    return window


_black_css = None


def _black(window) -> None:
    """Paint ``window`` and its child pure black (the dark handset)."""
    global _black_css
    from gi.repository import Gtk
    from config import SCREEN_OFF_BLACK
    if _black_css is None:
        _black_css = Gtk.CssProvider()
        _black_css.load_from_data((
            'window.xx-dock-cover, window.xx-dock-cover > * '
            '{ background: %s; }' % SCREEN_OFF_BLACK).encode())
        Gtk.StyleContext.add_provider_for_display(
            window.get_display(), _black_css,
            Gtk.STYLE_PROVIDER_PRIORITY_USER)
    window.add_css_class('xx-dock-cover')


def attach_touch(window, on_touch) -> None:
    """Feed ``window``'s raw touch events to ``on_touch``."""
    from gi.repository import Gdk, Gtk
    kinds = {
        Gdk.EventType.TOUCH_BEGIN: 'begin',
        Gdk.EventType.TOUCH_UPDATE: 'update',
        Gdk.EventType.TOUCH_END: 'end',
        Gdk.EventType.TOUCH_CANCEL: 'cancel',
    }
    legacy = Gtk.EventControllerLegacy.new()

    def _event(controller, event) -> bool:
        # PyGObject passes the signal's GdkEvent (a fundamental type) as
        # None; the controller hands out the same event by method.
        if event is None:
            event = controller.get_current_event()
        if event is None:
            return False
        kind = kinds.get(event.get_event_type())
        if kind is None:
            return False
        seq = event.get_event_sequence()
        ok, x, y = event.get_position()
        if seq is None or not ok:
            return True
        on_touch(kind, hash(seq), float(x), float(y), int(event.get_time()))
        return True

    legacy.connect('event', _event)
    window.add_controller(legacy)


# -- the controller -------------------------------------------------------

class DockController:
    """Follows xxwm's outputs and applies ``plan_for`` to the phone."""

    def __init__(self, config, output_power=None, is_locked=None,
                 display=None, socket_path=None,
                 state_dir: Path | None = None) -> None:
        self._config = config
        self._output_power = output_power
        self._is_locked = is_locked or (lambda: False)
        self._display = display
        self._socket_path = socket_path
        self._state_dir = state_dir
        self._layout = UNDOCKED
        self._plan = IDLE
        self._applied = IDLE
        self._cover = None
        self._cover_monitor = None
        self._gestures = None
        self._sink = None
        self._pointer = None
        self._pointer_state = 'none'
        self._saved_backlight: int | None = None
        self._powered_off: int | None = None
        self._surface_monitor = None
        self._closing = False
        self._sock = None
        self._thread: threading.Thread | None = None

    # -- public --------------------------------------------------------

    @property
    def layout(self) -> Layout:
        return self._layout

    @property
    def plan(self) -> Plan:
        return self._plan

    def docked(self) -> bool:
        return self._layout.docked

    def mode(self) -> str:
        return mode_of(self._config)

    def surface_monitor(self):
        return self._surface_monitor

    def start(self) -> None:
        global _current
        _current = self
        try:
            import session_lock
            session_lock.set_ui_monitor_picker(self.surface_monitor)
            session_lock.add_state_listener(self.refresh)
            session_lock.set_cover_hook(self._decorate_lock_cover)
        except Exception as exc:  # noqa: BLE001
            log.warning('dock: lock hooks unavailable: %s', exc)
        register = getattr(self._output_power, 'add_mode_listener', None)
        if callable(register):
            register(self._on_power_mode)
        # xxwm's feed and GDK's monitor list come over different sockets:
        # whichever is later re-places the surfaces.
        try:
            self._monitors = self._gdk_display().get_monitors()
            self._monitors.connect('items-changed',
                                   lambda *_a: self.refresh())
        except Exception as exc:  # noqa: BLE001
            log.info('dock: no monitor list to follow: %s', exc)
        self._restore_stale_osk()
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run, daemon=True, name='xx-wm-dock')
            self._thread.start()

    def stop(self) -> None:
        global _current
        self._closing = True
        sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self._layout = UNDOCKED
        self.refresh()
        if _current is self:
            _current = None

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            return
        try:
            self._config.set(CONFIG_KEY, mode)
        except Exception as exc:  # noqa: BLE001
            log.warning('dock: could not save %s: %s', CONFIG_KEY, exc)
        self.refresh()

    def apply_config(self) -> None:
        """Config hot reload: the mode may have changed."""
        self.refresh()

    def on_outputs(self, items) -> None:
        """xxwm's outputs list (main loop)."""
        layout = parse_outputs(items)
        if layout == self._layout:
            return
        was = self._layout.docked
        self._layout = layout
        if layout.docked != was:
            log.info('dock: %s', 'docked' if layout.docked else 'undocked')
        self.refresh()

    def on_blank_changed(self, blanked: bool) -> None:
        """Display manager woke the screens: the handset backlight came
        back with them, so a dark handset goes dark again."""
        if not blanked and self._applied.dark:
            self._set_dark(True, force=True)

    def refresh(self) -> None:
        """Work out the plan for the current layout, mode and lock, and
        apply what changed."""
        layout = self._layout
        plan = plan_for(self.mode(), layout.docked,
                        self._lock_ui_on_handset(),
                        layout.handset is not None)
        self._plan = plan
        self._apply(plan)

    # -- applying ------------------------------------------------------

    def _apply(self, plan: Plan) -> None:
        old = self._applied
        self._applied = plan
        self._place_surfaces(plan)
        if plan.cover:
            self._show_cover(trackpad=plan.trackpad)
        elif old.cover or self._cover is not None:
            self._hide_cover()
        if plan.trackpad:
            self._ensure_pointer()
        if plan.power_off != old.power_off or (
                plan.power_off and self._powered_off != self._handset_name()):
            self._set_power_off(plan.power_off)
        if plan.dark != old.dark:
            self._set_dark(plan.dark)
        if plan.osk_off != old.osk_off:
            self._set_osk_off(plan.osk_off)

    def _handset_name(self) -> int | None:
        handset = self._layout.handset
        return handset.wl_output if handset is not None else None

    def _gdk_display(self):
        if self._display is not None:
            return self._display
        try:
            from gi.repository import Gdk
            return Gdk.Display.get_default()
        except Exception:
            return None

    def _place_surfaces(self, plan: Plan) -> None:
        monitor = None
        if plan.on_monitor:
            monitor = gdk_monitor_for(self._gdk_display(), self._layout.monitor)
        if monitor is self._surface_monitor:
            return
        self._surface_monitor = monitor
        for window in list(_followers):
            was_visible = False
            try:
                was_visible = bool(window.get_visible())
            except Exception:
                pass
            _follow(window, monitor)
            if was_visible:
                try:
                    if not window.get_visible():
                        window.present()
                except Exception:
                    pass

    def _lock_ui_on_handset(self) -> bool:
        try:
            if not self._is_locked():
                return False
            import session_lock
            ui = session_lock.ui_monitor()
        except Exception:
            return False
        if ui is None:
            return False
        handset = gdk_monitor_for(self._gdk_display(), self._layout.handset)
        return handset is not None and ui == handset

    # -- cover + trackpad ----------------------------------------------

    def _on_touch(self, kind: str, seq, x: float, y: float, t: int) -> None:
        gestures = self._gestures
        if gestures is None or not self._applied.trackpad:
            return
        if kind == 'begin':
            gestures.begin(seq, x, y, t)
        elif kind == 'update':
            gestures.update(seq, x, y, t)
        elif kind == 'end':
            gestures.end(seq, x, y, t)
        else:
            gestures.cancel(seq, t)

    def _show_cover(self, trackpad: bool) -> None:
        handset = gdk_monitor_for(self._gdk_display(), self._layout.handset)
        if self._cover is None:
            try:
                self._cover = _make_cover_window(self._on_touch)
            except Exception as exc:  # noqa: BLE001
                log.warning('dock: no handset cover: %s', exc)
                return
        if handset is not self._cover_monitor:
            try:
                from gi.repository import Gtk4LayerShell as LayerShell
                LayerShell.set_monitor(self._cover, handset)
            except Exception:
                pass
            self._cover_monitor = handset
        if not self._cover.get_visible():
            self._cover.present()
            log.info('dock: handset covered (%s)',
                     'trackpad' if trackpad else 'off')

    def _hide_cover(self) -> None:
        if self._gestures is not None and self._sink is not None:
            import trackpad
            self._gestures = trackpad.TouchpadGestures(self._sink)
        cover = self._cover
        if cover is not None and cover.get_visible():
            cover.set_visible(False)
            log.info('dock: handset uncovered')

    def _ensure_pointer(self) -> None:
        if self._pointer_state == 'none':
            import trackpad
            self._pointer_state = 'connecting'
            self._pointer = trackpad.VirtualPointer()
            self._pointer.connect_async(self._on_pointer_ready)
        self._update_area()

    def _on_pointer_ready(self, ok: bool) -> None:
        import trackpad
        if not ok:
            self._pointer_state = 'failed'
            log.warning('dock: no virtual pointer; the trackpad is off')
            return
        self._pointer_state = 'ready'
        self._sink = trackpad.PointerSink(self._pointer.pointer,
                                          self._pointer.flush)
        self._gestures = trackpad.TouchpadGestures(self._sink)
        self._update_area()

    def _update_area(self) -> None:
        monitor = self._layout.monitor
        if self._sink is None or monitor is None:
            return
        import trackpad
        self._sink.set_area(monitor.rect,
                            trackpad.layout_bounds(self._layout.rects))

    def _decorate_lock_cover(self, cover, monitor) -> None:
        """A session-lock cover on the dark handset is the trackpad too,
        so the lock UI on the monitor can be used from the phone."""
        if not self._applied.trackpad:
            return
        handset = gdk_monitor_for(self._gdk_display(), self._layout.handset)
        if handset is None or monitor != handset:
            return
        try:
            attach_touch(cover, self._on_touch)
            _black(cover)
        except Exception as exc:  # noqa: BLE001
            log.warning('dock: lock cover is no trackpad: %s', exc)

    # -- backlight, output power, keyboard ------------------------------

    def _set_dark(self, dark: bool, force: bool = False) -> None:
        import backlight
        backlight.hold_dark(dark)

        import als_brightness

        def work() -> None:
            if dark:
                current = backlight.read_raw()
                if current and not force:
                    self._saved_backlight = current
                elif current and self._saved_backlight is None:
                    self._saved_backlight = current
                als_brightness.apply_backlight(0)
            elif self._saved_backlight:
                als_brightness.apply_backlight(self._saved_backlight)
                self._saved_backlight = None

        # The display manager's blank, wake and restore writes run on the
        # same serial worker; queued after them, dark lands last.
        als_brightness.run_backlight_io(work)

    def _on_power_mode(self, on: bool) -> None:
        # The display manager restores the backlight on every mode-on
        # (wake); it ran first, so dark goes back on top of it.
        if on and self._applied.dark:
            self._set_dark(True, force=True)

    def _set_power_off(self, off: bool) -> None:
        backend = self._output_power
        set_output = getattr(backend, 'set_output', None)
        if not callable(set_output):
            if off:
                log.info('dock: no output power; the handset stays on, black')
            return
        if off:
            name = self._handset_name()
            if name is None:
                return
            if self._powered_off is not None and self._powered_off != name:
                set_output(self._powered_off, True)
            if set_output(name, False):
                self._powered_off = name
                log.info('dock: handset output off')
        elif self._powered_off is not None:
            set_output(self._powered_off, True)
            self._powered_off = None
            log.info('dock: handset output on')

    def _osk_marker(self) -> Path:
        base = self._state_dir or Path(
            os.environ.get('XDG_STATE_HOME')
            or Path.home() / '.local' / 'state') / 'xx-wm'
        return Path(base) / 'dock-osk-off'

    def _set_osk_off(self, off: bool) -> None:
        marker = self._osk_marker()
        if off:
            if _osk_enabled() is True and _set_osk_enabled(False):
                try:
                    marker.parent.mkdir(parents=True, exist_ok=True,
                                        mode=0o700)
                    marker.write_text('1')
                except OSError as exc:
                    log.warning('dock: osk marker: %s', exc)
            try:
                import osk
                osk.set_visible(False)
            except Exception:
                pass
        elif marker.exists():
            _set_osk_enabled(True)
            try:
                marker.unlink()
            except OSError:
                pass

    def _restore_stale_osk(self) -> None:
        """A shell that died docked left the keyboard off: turn it back
        on (docking again turns it off again)."""
        if self._osk_marker().exists():
            self._set_osk_off(False)

    # -- the xxwm feed (worker thread) ----------------------------------

    def _run(self) -> None:
        delay = _RETRY_MIN
        while not self._closing:
            try:
                self._subscribe()
                delay = _RETRY_MIN
            except OSError as exc:
                if self._closing:
                    return
                log.info('dock: xxwm state stream lost (%s); retrying', exc)
            if self._closing:
                return
            threading.Event().wait(delay)
            delay = min(delay * 2, _RETRY_MAX)

    def _subscribe(self) -> None:
        import xxwm_backend
        path = self._socket_path or xxwm_backend.socket_path()
        if not path:
            raise OSError('no xxwm socket path')
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock = sock
        try:
            sock.connect(path)
            sock.sendall(b'subscribe state\n')
            with sock.makefile('r', encoding='utf-8',
                               errors='replace') as stream:
                for line in stream:
                    if self._closing:
                        return
                    items = _parse_line(line)
                    if items is not None:
                        _post(self.on_outputs, items)
            raise OSError('xxwm closed the state stream')
        finally:
            self._sock = None
            try:
                sock.close()
            except OSError:
                pass


def _parse_line(line: str):
    """The outputs list from one ``subscribe state`` line
    ({"outputs":[..],"windows":[..]}); None for shell verbs
    ({"shell":..}) and anything else."""
    line = line.strip()
    if not line:
        return None
    try:
        state = json.loads(line)
    except ValueError:
        log.warning('dock: bad state line from xxwm: %.80r', line)
        return None
    if not isinstance(state, dict):
        return None
    outputs = state.get('outputs')
    return outputs if isinstance(outputs, list) else None


def _post(callback, *args) -> None:
    try:
        from gi.repository import GLib
    except Exception:
        callback(*args)
        return
    GLib.idle_add(lambda: (callback(*args), False)[1])


def _osk_settings():
    try:
        from gi.repository import Gio
        source = Gio.SettingsSchemaSource.get_default()
        if source is None or source.lookup(_OSK_SCHEMA, True) is None:
            return None
        return Gio.Settings.new(_OSK_SCHEMA)
    except Exception:
        return None


def _osk_enabled() -> bool | None:
    settings = _osk_settings()
    if settings is None:
        return None
    try:
        return bool(settings.get_boolean(_OSK_KEY))
    except Exception:
        return None


def _set_osk_enabled(enabled: bool) -> bool:
    settings = _osk_settings()
    if settings is None:
        return False
    try:
        return bool(settings.set_boolean(_OSK_KEY, bool(enabled)))
    except Exception:
        return False
