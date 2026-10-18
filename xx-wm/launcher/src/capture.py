"""Screenshots, screen recording, and scrolling screenshots (Android parity).

grim captures and wf-recorder records when installed; every absence
degrades honestly (no capture, hidden Record tile) instead of faking
output. All subprocesses, sleeps, and Wayland round-trips run in worker
threads — the GTK loop only ever sees GLib.idle_add callbacks and short
timeout ticks.

Wiring: main.py calls configure() once the shell exists so notifications
and overlays reach the shade; DisplayManager delegates its Power +
Volume-down hook here via take_screenshot(); the quick-actions Record
tile drives get_recorder().
"""

from __future__ import annotations

import datetime
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

_HAS_GTK = True
try:
    import gi
except ImportError:
    _HAS_GTK = False

if _HAS_GTK:
    try:
        gi.require_version('Gtk4LayerShell', '1.0')
        _HAS_LAYER = True
    except (ValueError, ImportError):
        _HAS_LAYER = False

    gi.require_version('Gtk', '4.0')
    from gi.repository import Gio, GLib, Gtk

    if _HAS_LAYER:
        from gi.repository import Gtk4LayerShell as LayerShell


def _wl_env() -> dict[str, str]:
    return {
        **os.environ,
        'WAYLAND_DISPLAY': os.environ.get('WAYLAND_DISPLAY') or 'wayland-0',
        'XDG_RUNTIME_DIR': f'/run/user/{os.getuid()}',
    }


def _spawn_worker(target: Callable[[], None]):
    try:
        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        return thread
    except Exception:
        return None


def _post_to_main(callback: Callable, *args) -> None:
    idle_add = getattr(GLib, 'idle_add', None)
    if idle_add is None:
        try:
            callback(*args)
        except Exception:
            pass
        return
    try:
        idle_add(callback, *args)
    except Exception:
        pass


# Shell hook: main.py installs a provider returning the live shell window
# (or None before it exists) so this module never imports window.py.
_shell_provider: Callable[[], object | None] | None = None

# Latest blank state, fed by main.py's blank fan-out; the recorder's
# monitor stops the recording when the screen goes dark.
_blanked = False


def configure(
    shell_provider: Callable[[], object | None] | None = None,
) -> None:
    global _shell_provider
    _shell_provider = shell_provider


def set_blanked(blanked: bool) -> None:
    global _blanked
    _blanked = bool(blanked)


def _shell() -> object | None:
    provider = _shell_provider
    if provider is None:
        return None
    try:
        return provider()
    except Exception:
        return None


def _shade() -> object | None:
    shell = _shell()
    if shell is None:
        return None
    try:
        ensure = getattr(shell, '_ensure_shade', None)
        if callable(ensure):
            return ensure()
    except Exception:
        pass
    return getattr(shell, '_shade', None)


def _notify(notif_id: int, app_name: str, summary: str, body: str = '',
            actions: list | None = None) -> None:
    shade = _shade()
    if shade is None:
        return
    try:
        shade.add_notification(
            notif_id, app_name, summary, body, '', actions or [], None)
    except Exception as exc:
        log.warning('capture notification failed: %s', exc)


def _dismiss_notif(notif_id: int) -> None:
    shade = _shade()
    if shade is None:
        return
    try:
        shade.dismiss(notif_id, notify_daemon=False)
    except Exception:
        pass


# Shell-internal ids: never came from the notification daemon, so the
# shade must not echo-close them over D-Bus (same rule as 999901 there).
SCREENSHOT_NOTIF_ID = 999902
RECORDING_NOTIF_ID = 999903


# ---------------------------------------------------------------------------
# Screenshots
# ---------------------------------------------------------------------------

def screenshot_filename(now: datetime.datetime | None = None) -> str:
    moment = now if now is not None else datetime.datetime.now()
    return 'Screenshot_' + moment.strftime('%Y-%m-%d_%H-%M-%S') + '.png'


def screenshots_dir() -> str:
    return os.path.expanduser('~/Pictures/Screenshots')


def grim_available() -> bool:
    return shutil.which('grim') is not None


def _run_grim(path: str) -> bool:
    try:
        result = subprocess.run(
            ['grim', path],
            env=_wl_env(), timeout=10,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except Exception:
        return False


def take_screenshot() -> None:
    """Power + Volume-down entry point: grim in a worker, then notify."""
    _spawn_worker(_screenshot_work)


def _screenshot_work() -> None:
    if _blanked:
        # grim waits for a compositor frame that never comes while the
        # output is off — skip instead of blocking the worker on it.
        _post_to_main(
            _notify, SCREENSHOT_NOTIF_ID, 'Screenshot', 'Screen is off')
        return
    if not grim_available():
        log.info('screenshot skipped: grim is not installed')
        return
    target_dir = screenshots_dir()
    try:
        os.makedirs(target_dir, exist_ok=True)
    except OSError as exc:
        log.warning('screenshot failed: %s', exc)
        return
    path = os.path.join(target_dir, screenshot_filename())
    ok = _run_grim(path)
    _post_to_main(_on_screenshot_done, path, ok)


def _on_screenshot_done(path: str, ok: bool) -> bool:
    if not ok:
        return False
    flash()
    _notify(
        SCREENSHOT_NOTIF_ID, 'Screenshot', 'Screenshot saved',
        os.path.basename(path),
        actions=[
            ('Share', lambda: share_file(path)),
            ('Delete', lambda: delete_file(path, SCREENSHOT_NOTIF_ID)),
            ('Capture more', capture_scrolling),
        ],
    )
    return False


def share_file(path: str, bus=None) -> bool:
    """Offer the file to the user via the OpenURI portal (ask: true).

    The chooser — not the shell — picks the target, so a screenshot can
    go to any app without the shell hardcoding share destinations.
    """
    try:
        uri = Path(path).absolute().as_uri()
    except Exception:
        return False
    try:
        if bus is None:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        params = GLib.Variant('(ssa{sv})', (
            '', uri, {'ask': GLib.Variant('b', True)}))
        bus.call(
            'org.freedesktop.portal.Desktop',
            '/org/freedesktop/portal/desktop',
            'org.freedesktop.portal.OpenURI', 'OpenURI',
            params, None, Gio.DBusCallFlags.NONE, -1, None,
            _on_share_done,
        )
        return True
    except Exception as exc:
        log.warning('share failed: %s', exc)
        return False


def _on_share_done(conn, result) -> None:
    try:
        conn.call_finish(result)
    except Exception as exc:
        log.warning('share portal call failed: %s', exc)


def delete_file(path: str, notif_id: int | None = None) -> None:
    def _work() -> None:
        try:
            os.remove(path)
        except OSError as exc:
            log.warning('delete failed: %s', exc)
            return
        if notif_id is not None:
            _post_to_main(_dismiss_notif, notif_id)

    _spawn_worker(_work)


# Brief white flash as shutter feedback. The color is the point — a
# camera flash is white under any theme — so it intentionally skips the
# theme palette instead of tinting with it.
_FLASH_MS = 200
_FLASH_OPACITY = 0.3


def flash() -> None:
    if not _HAS_GTK:
        return
    try:
        _FlashOverlay().present()
    except Exception:
        pass


if _HAS_GTK:

    class _FlashOverlay(Gtk.Window):
        """Full-screen white overlay shown for _FLASH_MS at 30% opacity."""

        def __init__(self) -> None:
            super().__init__()
            self.set_decorated(False)
            self.set_resizable(False)
            if _HAS_LAYER and LayerShell.is_supported():
                LayerShell.init_for_window(self)
                LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
                for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM,
                             LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
                    LayerShell.set_anchor(self, edge, True)
                LayerShell.set_exclusive_zone(self, 0)
                LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
            css = Gtk.CssProvider()
            css.load_from_data(
                f'window {{ background: #ffffff; opacity: {_FLASH_OPACITY}; }}'
                .encode('utf-8'))
            self.get_style_context().add_provider(
                css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 10)
            self.connect('realize', self._on_realize)
            GLib.timeout_add(_FLASH_MS, self._on_timeout)

        def _on_realize(self, _widget) -> None:
            try:
                import cairo
            except ImportError:
                return
            try:
                surface = self.get_surface()
                if surface is not None:
                    surface.set_input_region(cairo.Region())
            except Exception:
                pass

        def _on_timeout(self) -> bool:
            try:
                self.destroy()
            except Exception:
                pass
            return False


# ---------------------------------------------------------------------------
# Screen recording
# ---------------------------------------------------------------------------

COUNTDOWN_STEPS = (3, 2, 1)
_LOW_BATTERY_PCT = 5
_MONITOR_INTERVAL_SECS = 5


def recordings_dir() -> str:
    return os.path.expanduser('~/Videos/Screenrecordings')


def recording_filename(now: datetime.datetime | None = None) -> str:
    moment = now if now is not None else datetime.datetime.now()
    return 'Screenrecording_' + moment.strftime('%Y-%m-%d_%H-%M-%S') + '.mp4'


def recorder_available() -> bool:
    return shutil.which('wf-recorder') is not None


def probe_h264() -> bool:
    """True when ffmpeg knows libx264, so -c libx264 stays honest.

    Worker thread only: it reads a subprocess pipe.
    """
    try:
        out = subprocess.run(
            ['ffmpeg', '-hide_banner', '-encoders'],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return False
    return 'libx264' in (out or '')


def build_recorder_command(
    output: str,
    record_audio: bool = False,
    has_h264: bool = False,
) -> list[str]:
    """wf-recorder argv. libx264 flags are added only when the encoder
    was probed; --audio only when the user enabled record_audio."""
    cmd = ['wf-recorder', '-f', output]
    if has_h264:
        cmd += ['-c', 'libx264', '-p', 'preset=ultrafast']
    if record_audio:
        cmd += ['--audio']
    return cmd


def format_recording_text(seconds: float) -> str:
    total = max(0, int(seconds))
    return f'Recording… {total // 60:02d}:{total % 60:02d}'


def should_auto_stop(blanked: bool, battery_pct: int | None) -> bool:
    """Stop the recording when the screen blanks or the battery is
    critical (≤5%). Unknown battery never stops anything by itself."""
    if blanked:
        return True
    return battery_pct is not None and battery_pct <= _LOW_BATTERY_PCT


def _record_audio_enabled() -> bool:
    shell = _shell()
    config = getattr(shell, 'config', None) if shell is not None else None
    if config is None:
        try:
            from config import ShellConfig
            config = ShellConfig()
        except Exception:
            return False
    try:
        return bool(config.record_audio)
    except Exception:
        return False


def _battery_now() -> int | None:
    try:
        from system_status import battery_pct
        return battery_pct()
    except Exception:
        return None


def countdown_labels() -> list[str]:
    return [str(step) for step in COUNTDOWN_STEPS]


def show_countdown(on_done: Callable[[], None]) -> None:
    """3-2-1 banner, then on_done. Headless: on_done runs at once."""
    if not _HAS_GTK:
        on_done()
        return
    try:
        _CountdownOverlay(list(COUNTDOWN_STEPS), on_done).present()
    except Exception:
        on_done()


if _HAS_GTK:

    class _CountdownOverlay(Gtk.Window):
        """Centered 3-2-1 banner on the OVERLAY layer, ~1 s per step."""

        def __init__(self, steps: list[int], on_done) -> None:
            super().__init__()
            self._steps = list(steps)
            self._on_done = on_done
            self.set_decorated(False)
            self.set_resizable(False)
            if _HAS_LAYER and LayerShell.is_supported():
                LayerShell.init_for_window(self)
                LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
                LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
                LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
                LayerShell.set_anchor(self, LayerShell.Edge.RIGHT, True)
                LayerShell.set_exclusive_zone(self, 0)
                LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
            self._label = Gtk.Label(label=str(self._steps[0]) if self._steps else '')
            self.set_child(self._label)
            self.connect('realize', self._on_realize)
            GLib.timeout_add_seconds(1, self._on_tick)

        def _on_realize(self, _widget) -> None:
            try:
                import cairo
                surface = self.get_surface()
                if surface is not None:
                    surface.set_input_region(cairo.Region())
            except Exception:
                pass

        def _on_tick(self) -> bool:
            self._steps.pop(0)
            if not self._steps:
                try:
                    self.destroy()
                except Exception:
                    pass
                try:
                    self._on_done()
                except Exception:
                    pass
                return False
            try:
                self._label.set_text(str(self._steps[0]))
            except Exception:
                pass
            return True


class ScreenRecorder:
    """One wf-recorder session. The tile drives set_recording(); the
    blank fan-out and the battery monitor drive stop().

    start() shows the countdown, then spawns in a worker. stop() sends
    SIGINT and reaps in a worker so the GTK loop never waits on the
    encoder's trailer write.
    """

    def __init__(
        self,
        battery_fn: Callable[[], int | None] | None = None,
        blank_fn: Callable[[], bool] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._output = ''
        self._started_at = 0.0
        self._tick_id: int | None = None
        self._spawn = subprocess.Popen
        self._battery_fn = battery_fn or _battery_now
        self._blank_fn = blank_fn or (lambda: _blanked)

    def is_recording(self) -> bool:
        with self._lock:
            return self._proc is not None

    def set_recording(self, active: bool) -> bool:
        if active:
            return self.start()
        return self.stop()

    def start(self) -> bool:
        if self.is_recording() or not recorder_available():
            return False
        show_countdown(self._start_now)
        return True

    def _start_now(self) -> None:
        _spawn_worker(self._start_work)

    def _start_work(self) -> None:
        if self.is_recording():
            return
        try:
            os.makedirs(recordings_dir(), exist_ok=True)
        except OSError as exc:
            log.warning('recording failed: %s', exc)
            return
        output = os.path.join(recordings_dir(), recording_filename())
        cmd = build_recorder_command(
            output,
            record_audio=_record_audio_enabled(),
            has_h264=probe_h264(),
        )
        try:
            proc = self._spawn(
                cmd, env=_wl_env(), close_fds=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (FileNotFoundError, OSError) as exc:
            log.warning('recording failed: %s', exc)
            return
        with self._lock:
            if self._proc is not None:
                try:
                    proc.terminate()
                except Exception:
                    pass
                return
            self._proc = proc
            self._output = output
            self._started_at = time.monotonic()
        _post_to_main(self._on_started)

    def _on_started(self) -> bool:
        self._notify_elapsed()
        try:
            self._tick_id = GLib.timeout_add_seconds(1, self._on_tick)
        except Exception:
            self._tick_id = None
        return False

    def _elapsed(self) -> float:
        return time.monotonic() - self._started_at

    def _notify_elapsed(self) -> None:
        _notify(
            RECORDING_NOTIF_ID, 'Screen recorder',
            format_recording_text(self._elapsed()),
            os.path.basename(self._output),
            actions=[('Stop', lambda: self.stop())],
        )

    def _on_tick(self) -> bool:
        if not self.is_recording():
            self._tick_id = None
            return False
        if self.check_auto_stop():
            self._tick_id = None
            return False
        self._notify_elapsed()
        return True

    def check_auto_stop(self) -> bool:
        """Poll blank + battery; stop when either trips. Returns True
        when the recording was stopped here."""
        try:
            blanked = bool(self._blank_fn())
        except Exception:
            blanked = False
        try:
            pct = self._battery_fn()
        except Exception:
            pct = None
        if not should_auto_stop(blanked, pct):
            return False
        reason = 'screen blanked' if blanked else 'low battery'
        log.info('stopping recording: %s', reason)
        self.stop(reason=reason)
        return True

    def stop(self, reason: str = 'user') -> bool:
        with self._lock:
            proc = self._proc
            self._proc = None
            tick_id, self._tick_id = self._tick_id, None
        if proc is None:
            return False
        if tick_id is not None:
            try:
                GLib.source_remove(tick_id)
            except Exception:
                pass
        try:
            proc.send_signal(signal.SIGINT)
        except Exception as exc:
            log.warning('recording stop signal failed: %s', exc)
        _spawn_worker(lambda: self._reap(proc, reason))
        return True

    def _reap(self, proc: subprocess.Popen, reason: str) -> None:
        try:
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        log.info('recording stopped (%s): %s', reason, self._output)
        _post_to_main(_dismiss_notif, RECORDING_NOTIF_ID)


_recorder: ScreenRecorder | None = None


def get_recorder() -> ScreenRecorder:
    """Process-wide recorder the Record tile and the blank fan-out share
    (same registry pattern as app_standby.get_standby)."""
    global _recorder
    if _recorder is None:
        _recorder = ScreenRecorder()
    return _recorder


# ---------------------------------------------------------------------------
# Scrolling screenshots
# ---------------------------------------------------------------------------

# A scroll steps the focused app by ~70% of the screen height per frame,
# split into small finger-axis events so apps see a drag, not a jump.
# Frames settle ~250 ms before each grim; at most 8 frames per capture.
_SCROLL_FRACTION = 0.7
_SCROLL_STEPS = 5
_SCROLL_SETTLE_SECS = 0.25
_SCROLL_MAX_FRAMES = 8

# wl_pointer conventions reused by the virtual-pointer protocol: axis 0
# is vertical scroll, source 1 is a finger on a touch device.
AXIS_VERTICAL = 0
AXIS_SOURCE_FINGER = 1


def emit_scroll(pointer, height_px: int, time_ms: int = 0) -> None:
    """Inject one ~70%-height finger scroll. Request order is the wire
    order: axis_source, then the axis events, then the closing frame."""
    step = float(height_px) * _SCROLL_FRACTION / _SCROLL_STEPS
    pointer.axis_source(AXIS_SOURCE_FINGER)
    for _ in range(_SCROLL_STEPS):
        pointer.axis(int(time_ms), AXIS_VERTICAL, step)
    pointer.frame()


# Linux input event code of the primary button; wl_pointer button states.
BTN_LEFT = 0x110
BUTTON_RELEASED = 0
BUTTON_PRESSED = 1
# Press-to-release hold of an injected click. GTK drops a press and a
# release that arrive in the same dispatch (the button never clicks).
CLICK_HOLD_SECS = 0.05


def emit_click(pointer, x: float, y: float, width: int, height: int,
               time_ms: int = 0, flush=None, sleep=time.sleep) -> None:
    """Inject one primary click at layout (x, y) of a ``width`` x
    ``height`` output: absolute motion, press, a short hold, release,
    each framed and flushed (``flush``) so the app sees them apart."""
    flush = flush or (lambda: None)
    stamp = int(time_ms) & 0xFFFFFFFF
    pointer.motion_absolute(stamp, max(0, int(x)), max(0, int(y)),
                            max(1, int(width)), max(1, int(height)))
    pointer.frame()
    pointer.button(stamp, BTN_LEFT, BUTTON_PRESSED)
    pointer.frame()
    flush()
    sleep(CLICK_HOLD_SECS)
    hold_ms = int(CLICK_HOLD_SECS * 1000)
    pointer.button((stamp + hold_ms) & 0xFFFFFFFF, BTN_LEFT, BUTTON_RELEASED)
    pointer.frame()
    flush()


def _screen_height() -> int:
    try:
        from gi.repository import Gdk
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() if display is not None else None
        geometry = monitor.get_geometry() if monitor is not None else None
        height = geometry.height if geometry is not None else None
        if height:
            return int(height)
    except Exception:
        pass
    return 720


class VirtualScroller:
    """One-shot finger-scroll injector over zwlr_virtual_pointer_manager_v1.

    Bindings come from the installed pywayland's own scanner (see
    wayland_bindings); without pywayland, bindings, or a compositor
    offering the manager, connect() reports False and the scrolling
    capture keeps the frames it already has. Worker thread only.
    """

    def __init__(self, bind_fn=None, display_cls=None) -> None:
        self._bind_fn = bind_fn
        self._display_cls = display_cls
        self._display = None
        self._registry = None
        self._pointer = None

    def connect(self) -> bool:
        try:
            import wayland_bindings
        except ImportError:
            return False
        bind_fn = self._bind_fn or wayland_bindings.ensure_protocol_bindings
        try:
            bindings = bind_fn(
                wayland_bindings.VIRTUAL_POINTER_XML_NAMES,
                wayland_bindings.VIRTUAL_POINTER_CLASS_NAMES,
            )
        except Exception:
            return False
        if not bindings:
            return False
        try:
            display_cls = self._display_cls
            if display_cls is None:
                from pywayland.client import Display
                display_cls = Display
            return self._connect_with(bindings, display_cls)
        except Exception as exc:
            log.info('virtual pointer unavailable: %s', exc)
            return False

    def _connect_with(self, bindings, display_cls) -> bool:
        display = display_cls()
        display.connect()
        # pywayland resolves new_id event args through the live WlRegistry
        # proxy, so it must stay referenced for the connection's lifetime.
        self._registry = display.get_registry()
        registry = self._registry
        state: dict = {}

        def _on_global(reg, name: int, interface: str, version: int) -> None:
            if interface == 'wl_seat':
                state['seat'] = reg.bind(name, bindings['WlSeat'], 1)
            elif interface == 'zwlr_virtual_pointer_manager_v1':
                state['manager'] = reg.bind(
                    name, bindings['ZwlrVirtualPointerManagerV1'],
                    min(2, version))

        registry.dispatcher['global'] = _on_global
        display.dispatch(block=True)
        display.flush()
        display.dispatch(block=True)
        display.flush()
        seat = state.get('seat')
        manager = state.get('manager')
        if seat is None or manager is None:
            try:
                display.disconnect()
            except Exception:
                pass
            self._registry = None
            return False
        self._display = display
        self._pointer = manager.create_virtual_pointer(seat)
        display.flush()
        return True

    def scroll(self, height_px: int) -> bool:
        if self._pointer is None or self._display is None:
            return False
        try:
            emit_scroll(self._pointer, height_px,
                        int(time.monotonic() * 1000))
            self._display.flush()
            return True
        except Exception as exc:
            log.warning('virtual scroll failed: %s', exc)
            return False

    def click(self, x: float, y: float, width: int, height: int) -> bool:
        """One primary click at layout (x, y) (bottom_gesture's tap
        pass-through). Worker thread only."""
        if self._pointer is None or self._display is None:
            return False
        try:
            emit_click(self._pointer, x, y, width, height,
                       int(time.monotonic() * 1000),
                       flush=self._display.flush)
            return True
        except Exception as exc:
            log.warning('virtual click failed: %s', exc)
            return False

    def close(self) -> None:
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
                pass


def capture_scrolling() -> None:
    """'Capture more' entry point: scroll + grim in a worker, then stitch."""
    _spawn_worker(_scrolling_work)


def _hide_shell_surfaces() -> None:
    shell = _shell()
    if shell is None:
        return
    for method in ('hide_shade', 'drop_to_background'):
        try:
            fn = getattr(shell, method, None)
            if callable(fn):
                fn()
                break
        except Exception:
            pass
    for attr in ('_banner', '_switcher'):
        try:
            surface = getattr(shell, attr, None)
            hide = getattr(surface, 'hide_banner', None) or getattr(
                surface, 'hide_switcher', None)
            if callable(hide):
                hide()
        except Exception:
            pass


def _scrolling_work() -> None:
    if not grim_available():
        log.info('scrolling capture skipped: grim is not installed')
        return
    import tempfile
    import capture_stitch
    _post_to_main(_hide_shell_surfaces)
    time.sleep(_SCROLL_SETTLE_SECS)
    height = _screen_height()
    scroller = VirtualScroller()
    can_scroll = scroller.connect()
    frames: list[tuple[int, int, bytes]] = []
    try:
        with tempfile.TemporaryDirectory(prefix='xx-wm-scroll-') as tmp:
            for index in range(_SCROLL_MAX_FRAMES):
                if index:
                    if can_scroll:
                        scroller.scroll(height)
                    time.sleep(_SCROLL_SETTLE_SECS)
                path = os.path.join(tmp, f'frame_{index}.png')
                if not _run_grim(path):
                    break
                frame = capture_stitch.load_png(path)
                if frame is None:
                    break
                if frames and capture_stitch.frames_equal(frames[-1], frame):
                    frames.append(frame)
                    break
                frames.append(frame)
    finally:
        scroller.close()
    if len(frames) < 2:
        if frames:
            _post_to_main(_notify_single_frame, frames[0])
        return
    try:
        width, tall, rgb = capture_stitch.stitch(frames)
    except ValueError as exc:
        log.warning('scrolling stitch failed: %s', exc)
        _post_to_main(_notify_single_frame, frames[0])
        return
    target_dir = screenshots_dir()
    try:
        os.makedirs(target_dir, exist_ok=True)
    except OSError as exc:
        log.warning('scrolling capture failed: %s', exc)
        return
    path = os.path.join(target_dir, screenshot_filename())
    if not capture_stitch.save_png(width, tall, rgb, path):
        _post_to_main(_notify_single_frame, frames[0])
        return
    _post_to_main(_on_screenshot_done, path, True)


def _notify_single_frame(frame: tuple[int, int, bytes]) -> bool:
    import tempfile
    import capture_stitch
    try:
        with tempfile.NamedTemporaryFile(
                suffix='.png', delete=False,
                dir=screenshots_dir()) as tmp:
            path = tmp.name
        if not capture_stitch.save_png(frame[0], frame[1], frame[2], path):
            try:
                os.remove(path)
            except OSError:
                pass
            return False
    except OSError:
        return False
    return _on_screenshot_done(path, True)
