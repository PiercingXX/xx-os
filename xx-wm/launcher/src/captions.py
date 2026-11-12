"""Live-captions shell side: quick-settings tile plus volume-row toggle.

Consumer of ``io.piercingxx.Captions1`` (normative contract:
``ref/xx-captions/src/xx_captions/dbus_api.py``; reference client
``ref/xx-captions/src/xx_captions/client.py``). Every D-Bus call is
async off the GTK thread's back: replies unpack to a TUPLE of out-args
(``reply.unpack()[0]``), ``reply_type`` is a ``GLib.VariantType`` or
``None``, async callbacks take ``(conn, result)`` with no trailing
``user_data``, and both signal subscriptions filter on
``sender=BUS_NAME``.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

BUS_NAME = 'io.piercingxx.Captions1'
OBJECT_PATH = '/io/piercingxx/Captions1'
INTERFACE = 'io.piercingxx.Captions1'
PROPS_IFACE = 'org.freedesktop.DBus.Properties'

OVERLAY_APP_ID = 'io.piercingxx.Captions'
OVERLAY_ARGV = ('xx-captions', '--overlay')

PLAYBACK_SOURCE = 'playback'
SOURCES = ('playback', 'call', 'mic')

DESKTOP_ENTRY = 'io.piercingxx.Captions.desktop'
ACTIVATION_FILE = 'io.piercingxx.Captions1.service'

TILE_STATES = ('off', 'starting', 'on', 'no-model', 'error')

# Catalog file lists mirrored from xx-captions config.MODEL_CATALOG: the
# shell only needs presence checks (every file present == installed),
# so just the relative file names travel here, not URLs or checksums.
_MODEL_FILES = {
    'sherpa-onnx-streaming-zipformer-en-2023-06-26': (
        'encoder.onnx', 'decoder.onnx', 'joiner.onnx', 'tokens.txt'),
    'sherpa-onnx-streaming-zipformer-en-20M-2023-02-17': (
        'encoder.onnx', 'decoder.onnx', 'joiner.onnx', 'tokens.txt'),
    'vosk-model-small-en-us-0.15': (
        'am/final.mdl', 'graph/HCLr.fst', 'graph/Gr.fst',
        'conf/model.conf'),
}


def tile_state(active: bool, starting: bool = False,
               installed: bool = True, error: bool = False) -> str:
    """Quick-settings tile visual. Precedence is fixed by the contract:
    error beats missing-model beats on beats starting beats off."""
    if error:
        return 'error'
    if not installed:
        return 'no-model'
    if active:
        return 'on'
    if starting:
        return 'starting'
    return 'off'


_UNREACHABLE = ('ServiceUnknown', 'NameHasNoOwner', 'NoReply', 'TimedOut',
                'Disconnected')


def service_absent(exc: BaseException) -> bool:
    """True when the error only says the service is not running."""
    msg = str(exc)
    return 'ServiceUnknown' in msg or 'NameHasNoOwner' in msg


def error_text(exc: BaseException) -> str:
    """Tile text for a failed call: the service's own message, without
    GLib's 'g-dbus-error-quark: GDBus.Error:<name>:' wrapper, and
    'Unavailable' when the service is not running or not answering."""
    msg = str(exc)
    m = re.search(r'GDBus\.Error:([\w.]+):?\s*(.*)', msg, re.S)
    if not m:
        return msg
    if m.group(1).rsplit('.', 1)[-1] in _UNREACHABLE:
        return 'Unavailable'
    return m.group(2).strip() or 'Error'


def tile_label(state: str, error: str | None = None) -> str:
    """Second-line tile text for a tile state. Error text is shown
    verbatim (the tile widget ellipsizes it); every other state is a
    short fixed string that fits a 320 px screen."""
    if state == 'error':
        return (error or '').strip() or 'Error'
    return {'on': 'On', 'starting': 'Starting…',
            'no-model': 'No model'}.get(state, 'Off')


def _data_dirs() -> list[Path]:
    home = os.environ.get('XDG_DATA_HOME') or str(
        Path.home() / '.local' / 'share')
    dirs = [Path(home)]
    fallback = os.environ.get('XDG_DATA_DIRS') or '/usr/local/share:/usr/share'
    for entry in fallback.split(':'):
        if entry:
            dirs.append(Path(entry))
    return dirs


def captions_installed() -> bool:
    """True when xx-captions is installed: its binary, its desktop entry,
    or the service activation file. The tile hides entirely otherwise."""
    try:
        if shutil.which('xx-captions') is not None:
            return True
    except Exception:
        pass
    try:
        for base in _data_dirs():
            if (base / 'applications' / DESKTOP_ENTRY).is_file():
                return True
            if (base / 'dbus-1' / 'services' / ACTIVATION_FILE).is_file():
                return True
    except Exception:
        pass
    return False


def models_dir(home: Path | None = None) -> Path:
    root = Path(home) if home is not None else Path.home()
    return root / '.local' / 'share' / 'xx-captions' / 'models'


def is_model_installed(model_id: str, home: Path | None = None) -> bool:
    """Every catalog file present, no network, no side effects."""
    files = _MODEL_FILES.get(model_id)
    if not files:
        return False
    root = models_dir(home) / model_id
    try:
        return all((root / name).is_file() for name in files)
    except Exception:
        return False


def any_model_installed(home: Path | None = None) -> bool:
    """True when at least one catalog model is fully installed."""
    try:
        return any(is_model_installed(mid, home) for mid in _MODEL_FILES)
    except Exception:
        return False


def start_params(source: str):
    """Variant for Start(source); only catalog sources reach D-Bus."""
    from gi.repository import GLib

    if source not in SOURCES:
        raise ValueError(f'source must be one of {SOURCES}')
    return GLib.Variant('(s)', (source,))


def parse_active_source(unpacked: tuple) -> tuple[bool, str]:
    """(active, source) from a GetAll reply's unpacked tuple."""
    if not isinstance(unpacked, tuple) or not unpacked:
        raise ValueError('D-Bus reply unpacks to a non-empty tuple')
    props = unpacked[0]
    get = props.get if hasattr(props, 'get') else (lambda k, d=None: d)
    active = get('Active', False)
    source = get('Source', '')
    if hasattr(active, 'unpack'):
        active = active.unpack()
    if hasattr(source, 'unpack'):
        source = source.unpack()
    return bool(active), str(source)


def open_overlay() -> bool:
    """Launch the caption overlay (first run shows its model-download
    screen). Fire-and-forget: a second launch just activates the
    overlay's single Gtk.Application instance."""
    try:
        subprocess.Popen(list(OVERLAY_ARGV),
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True)
        return True
    except Exception:
        return False


class CaptionsController:
    """Shell-side connection: Start/Stop plus property watch for the tile.

    Pass a fake bus in tests; otherwise connect() opens the real session
    bus. All async completions land on the GLib main loop thread, where
    on_change fires with no arguments (same shape as LocationState).
    """

    def __init__(self, on_change=None, bus=None, home=None,
                 launcher=None, on_text=None) -> None:
        self._on_change = on_change
        self.on_text = on_text
        self._bus = bus
        self._home = home
        self._launch = launcher or open_overlay
        self._subs: list[int] = []
        self.active = False
        self.source = ''
        self.starting = False
        self.error: str | None = None
        self._overlay_started = False
        self._generation = 0

    @property
    def connected(self) -> bool:
        return self._bus is not None

    @property
    def installed(self) -> bool:
        return bool(any_model_installed(self._home))

    @property
    def tile_state(self) -> str:
        return tile_state(self.active, self.starting, self.installed,
                          self.error is not None)

    @property
    def label(self) -> str:
        return tile_label(self.tile_state, self.error)

    def connect(self) -> bool:
        """Open the session bus and subscribe; False when unavailable."""
        if self._bus is not None:
            self._subscribe()
            self.refresh()
            return True
        try:
            from gi.repository import Gio
        except ImportError:
            return False
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception:
            self._bus = None
            return False
        if self._bus is None:
            return False
        self._subscribe()
        self.refresh()
        return True

    def _subscribe(self) -> None:
        from gi.repository import Gio

        bus = self._bus
        self._subs.append(bus.signal_subscribe(
            BUS_NAME, INTERFACE, 'Text', OBJECT_PATH,
            None, Gio.DBusSignalFlags.NONE, self._on_text))
        self._subs.append(bus.signal_subscribe(
            BUS_NAME, PROPS_IFACE, 'PropertiesChanged', OBJECT_PATH,
            None, Gio.DBusSignalFlags.NONE, self._on_properties_changed))

    def _on_text(self, _conn, _sender, _path, _iface, _signal, params) -> None:
        try:
            text, final = params.unpack()
            if not isinstance(text, str) or not text:
                return
            if not isinstance(final, bool):
                return
        except Exception:
            return
        if callable(self.on_text):
            try:
                self.on_text(str(text), bool(final))
            except Exception:
                pass

    def _on_properties_changed(self, _conn, _sender, _path, _iface,
                               _signal, params) -> None:
        try:
            _iface_name, changed, _invalidated = params.unpack()
        except Exception:
            return
        get = changed.get if hasattr(changed, 'get') else (lambda k,
                                                           d=None: d)
        active = get('Active', None)
        source = get('Source', None)
        if hasattr(active, 'unpack'):
            active = active.unpack()
        if hasattr(source, 'unpack'):
            source = source.unpack()
        if active is None and source is None:
            return
        if active is not None:
            self.active = bool(active)
            if self.active:
                self.starting = False
                self.error = None
        if source is not None:
            self.source = str(source)
        self._fire()

    def set_enabled(self, enabled: bool) -> None:
        """Tap handler: enabling launches the overlay then Start('playback');
        with no model installed the tap only opens the overlay's download
        screen. Disabling calls Stop."""
        self._generation += 1
        generation = self._generation
        self.error = None
        if not enabled:
            self.starting = False
            self._stop(generation)
            self._fire()
            return
        if not self.installed:
            self.starting = False
            self._ensure_overlay(force=True)
            self._fire()
            return
        self.starting = True
        self._fire()
        self._ensure_overlay()
        self._start_playback(generation)

    def open_model_screen(self) -> None:
        """Long-press handler: open the overlay's model screen."""
        self._ensure_overlay(force=True)

    def _ensure_overlay(self, force: bool = False) -> None:
        if self._overlay_started and not force:
            return
        try:
            if self._launch():
                self._overlay_started = True
        except Exception:
            pass

    def _start_playback(self, generation: int) -> None:
        bus = self._bus
        if bus is None:
            self.starting = False
            self.error = 'not connected'
            self._fire()
            return
        try:
            params = start_params(PLAYBACK_SOURCE)
        except Exception as exc:
            self.starting = False
            self.error = error_text(exc)
            self._fire()
            return
        from gi.repository import Gio

        try:
            bus.call(
                BUS_NAME, OBJECT_PATH, INTERFACE, 'Start', params, None,
                Gio.DBusCallFlags.NONE, 5000, None,
                lambda conn, result: self._finish_start(
                    conn, result, generation))
        except Exception as exc:
            self.starting = False
            self.error = error_text(exc)
            self._fire()

    def _finish_start(self, conn, result, generation: int) -> None:
        if generation != self._generation:
            return
        try:
            conn.call_finish(result)
        except Exception as exc:
            self.starting = False
            self.error = error_text(exc)
            self._fire()
            return
        self.refresh()
        self._fire()

    def _stop(self, generation: int) -> None:
        bus = self._bus
        if bus is None:
            return
        from gi.repository import Gio

        try:
            # Stopping a service that is not running must not start it.
            bus.call(
                BUS_NAME, OBJECT_PATH, INTERFACE, 'Stop', None, None,
                Gio.DBusCallFlags.NO_AUTO_START, 5000, None,
                lambda conn, result: self._finish_stop(
                    conn, result, generation))
        except Exception as exc:
            self.error = error_text(exc)
            self._fire()

    def _finish_stop(self, conn, result, generation: int) -> None:
        if generation != self._generation:
            return
        try:
            conn.call_finish(result)
        except Exception as exc:
            self.error = error_text(exc)
        self.refresh()
        self._fire()

    def refresh(self) -> None:
        """Async GetAll for Active/Source. Never starts the service: the
        shade builds this panel at login, and reading the tile must not
        load the speech model."""
        bus = self._bus
        if bus is None:
            return
        from gi.repository import Gio, GLib

        try:
            bus.call(
                BUS_NAME, OBJECT_PATH, PROPS_IFACE, 'GetAll',
                GLib.Variant('(s)', (INTERFACE,)),
                GLib.VariantType.new('(a{sv})'),
                Gio.DBusCallFlags.NO_AUTO_START, 5000, None,
                lambda conn, result: self._finish_refresh(conn, result))
        except Exception:
            pass

    def _finish_refresh(self, conn, result) -> None:
        try:
            reply = conn.call_finish(result)
            self.active, self.source = parse_active_source(reply.unpack())
            if self.active:
                self.starting = False
            # A good read is the current truth: an older error is stale.
            if not self.starting:
                self.error = None
        except Exception as exc:
            if service_absent(exc):
                # Not running is plain off, not an error.
                self.active = False
                if not self.starting:
                    self.error = None
            else:
                self.error = error_text(exc)
        self._fire()

    def _fire(self) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change()
        except Exception:
            pass

    def close(self) -> None:
        bus = self._bus
        for sub in self._subs:
            try:
                bus.signal_unsubscribe(sub)
            except Exception:
                pass
        self._subs = []
        self._bus = None
