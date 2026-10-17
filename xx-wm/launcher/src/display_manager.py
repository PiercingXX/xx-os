"""
Display power management.

Real power-off goes over zwlr_output_power_manager_v1 (see output_power.py),
which cuts panel/GPU power; the backlight level is still saved/restored via
brightnessctl and always switched off too (hwcomposer phoc only stops rendering),
with the wlopm binary kept only as a last fallback — it is not shipped on
target devices. Blank state is tracked internally (self._blanked), so we
never depend on querying any backend to wake back up.

Wake sources and the output name are auto-detected, not hardcoded: every
readable /dev/input/event* node is watched (reads are non-exclusive) and
_on_event filters by code, so power/touch/volume wake the display whatever
event number they land on and whatever the device. This replaces an earlier
FP5-specific map (event0-3 / HWCOMPOSER-1) that trapped other devices dark.

The fingerprint reader node is resolved once at init: the FP_INPUT_DEV env
var overrides, else /sys/class/input/*/name is scanned for reader
identifiers (fingerprint/goodix/focaltech, case-insensitive);
/dev/input/event3 remains the fallback when detection finds nothing.

Hardware shortcuts (handled via evdev):
  - Power short press                  → blank / wake (toggle)
  - Power long-press (≥600ms)          → on_power_menu()
  - Power + Volume Down simultaneously → on_screenshot()
  - Volume Up / Down                   → +5% / -5% media volume
  - Media/headset keys (press only)    → on_media_key('play-pause'/'next'/'previous')
  - Touch                              → idle reset only (never wakes: pocket safety)
"""
from __future__ import annotations

import logging
import os
import struct
import subprocess
import threading
import time
from typing import Callable

from gi.repository import GLib

import backlight

try:
    from output_power import OutputPower
except Exception:
    OutputPower = None  # type: ignore[assignment,misc]

log = logging.getLogger(__name__)

_EV_FMT  = 'llHHi'
_EV_SIZE = struct.calcsize(_EV_FMT)

EV_KEY    = 1
EV_ABS    = 3
ABS_MT_TRACKING_ID = 57   # touch-down when value >= 0
KEY_POWER     = 116
KEY_VOLUMEDOWN = 114
KEY_VOLUMEUP   = 115
KEY_NEXTSONG     = 163
KEY_PLAYPAUSE    = 164
KEY_PREVIOUSSONG = 165
KEY_PLAYCD  = 200
KEY_PAUSECD = 201
KEY_MEDIA   = 226

# Headset/media key routing: evdev code → MPRIS action name shared with
# media_controls (MprisWatcher.handle_media_key drives the active player).
# KEY_MEDIA is the single headset button: a press toggles play/pause.
_MEDIA_KEY_ACTIONS = {
    KEY_PLAYPAUSE: 'play-pause',
    KEY_NEXTSONG: 'next',
    KEY_PREVIOUSSONG: 'previous',
    KEY_PLAYCD: 'play-pause',
    KEY_PAUSECD: 'play-pause',
    KEY_MEDIA: 'play-pause',
}

_FP_IDENTIFIERS = ('fingerprint', 'goodix', 'focaltech')

_POWER_LONG_PRESS_MS = 600   # ms hold to trigger power menu instead of blank

# Reader-thread flood gate: touch-move frames (~120 Hz), accelerometers and
# EV_SYN/EV_MSC must never reach the GTK loop. Only the keys the manager
# acts on plus throttled touch-downs are posted (see filter_evdev_data).
_EV_READ_BATCH = 32          # events per os.read (was: one read per event)
_TOUCH_POST_MIN_GAP_MS = 250  # at most one touch-down post per device per window


def filter_evdev_data(
    data: bytes,
    path: str,
    fp_node: str,
    now_ms: float,
    last_touch_ms: float | None = None,
) -> tuple[list[tuple[str, int, int, int]], float | None]:
    """Pure reader-thread gate over raw evdev bytes.

    Decodes the complete events in data (a trailing partial event is left
    for the caller to buffer) and returns only the (path, type, code, value)
    tuples the manager acts on: power/volume keys on any node, EV_KEY
    presses on the fingerprint node, media/headset key presses on any node,
    and touch-downs (ABS_MT_TRACKING_ID >= 0) throttled to one post per
    _TOUCH_POST_MIN_GAP_MS per device. Media keys post press-downs only:
    releases and auto-repeats carry no state and would double-toggle.
    Everything else — EV_SYN, EV_MSC, absolute motion, key releases
    elsewhere — is dropped here so it never queues a GTK callback.
    """
    wanted: list[tuple[str, int, int, int]] = []
    end = len(data) // _EV_SIZE * _EV_SIZE
    for offset in range(0, end, _EV_SIZE):
        _sec, _usec, ev_type, code, value = struct.unpack(
            _EV_FMT, data[offset:offset + _EV_SIZE])
        if ev_type == EV_KEY:
            if (code in (KEY_POWER, KEY_VOLUMEUP, KEY_VOLUMEDOWN)
                    or (path == fp_node and value == 1)):
                wanted.append((path, ev_type, code, value))
            elif code in _MEDIA_KEY_ACTIONS and value == 1:
                wanted.append((path, ev_type, code, value))
        elif (ev_type == EV_ABS and code == ABS_MT_TRACKING_ID
                and value >= 0):
            if (last_touch_ms is None
                    or now_ms - last_touch_ms >= _TOUCH_POST_MIN_GAP_MS):
                wanted.append((path, ev_type, code, value))
                last_touch_ms = now_ms
    return wanted, last_touch_ms

# Wake devices and the output name used to be hardcoded per-device (the FP5's
# event0-3 / HWCOMPOSER-1). That silently traps every other device in the dark:
# the backlight blanks but the wrong input nodes / output name mean nothing can
# wake it. Both are now auto-detected at runtime — see _all_event_devices() and
# _detect_output().
_IDLE_SECS = 60


def _all_event_devices() -> list[str]:
    """Every readable /dev/input/event* node. Reading is non-exclusive (no
    EVIOCGRAB), so watching them all is harmless — _on_event filters by code."""
    import glob
    return sorted(d for d in glob.glob('/dev/input/event*') if os.access(d, os.R_OK))


def _detect_fp_node(sysfs_dir: str = '/sys/class/input') -> str:
    """Fingerprint reader evdev node. FP_INPUT_DEV wins outright; else the
    input device whose sysfs name matches a known reader identifier is mapped
    to its event* child. Legacy /dev/input/event3 fallback keeps devices
    where detection finds nothing working until verified per-device."""
    override = os.environ.get('FP_INPUT_DEV')
    if override:
        return override
    import glob
    for name_path in sorted(glob.glob(os.path.join(sysfs_dir, 'input*', 'name'))):
        try:
            with open(name_path, encoding='utf-8', errors='replace') as f:
                name = f.read().strip()
        except OSError:
            continue
        if not any(tag in name.lower() for tag in _FP_IDENTIFIERS):
            continue
        events = sorted(glob.glob(os.path.join(os.path.dirname(name_path), 'event*')))
        if events:
            return '/dev/input/' + os.path.basename(events[0])
    return '/dev/input/event3'


def _detect_output() -> str:
    """First output wlopm reports; '*' (all outputs) if it can't be parsed —
    wlopm accepts '*' for on/off, so blanking still works either way."""
    try:
        out = subprocess.check_output(
            ['wlopm'], env=_WL_ENV, timeout=2, text=True, stderr=subprocess.DEVNULL,
        )
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] in ('on', 'off'):
                return parts[0]
    except Exception:
        pass
    return '*'


_WLOPM_OUTPUT: str | None = None


def _output() -> str:
    global _WLOPM_OUTPUT
    if _WLOPM_OUTPUT is None:
        _WLOPM_OUTPUT = _detect_output()
    return _WLOPM_OUTPUT

_WL_ENV = {
    **os.environ,
    # Honor the session we were launched into; wayland-0 is only the fallback.
    'WAYLAND_DISPLAY': os.environ.get('WAYLAND_DISPLAY') or 'wayland-0',
    'XDG_RUNTIME_DIR': f'/run/user/{os.getuid()}',
}


# ---------------------------------------------------------------------------
# Display control helpers
# ---------------------------------------------------------------------------

def _wlopm(mode: str) -> bool:
    try:
        r = subprocess.run(
            ['wlopm', f'--{mode}', _output()],
            env=_WL_ENV, timeout=3,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return r.returncode == 0
    except Exception:
        return False


def _brightnessctl(*args: str) -> str | None:
    try:
        return subprocess.check_output(
            backlight.brightnessctl_args() + list(args),
            text=True, timeout=2, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def _make_output_power() -> object | None:
    """Best-effort real output-power backend. Construction returns at once
    (bindings + connect run off the main loop); None when the module or
    pywayland is unavailable — blanking falls back to the backlight."""
    if OutputPower is None:
        return None
    try:
        return OutputPower()
    except Exception:
        return None


def _take_screenshot() -> None:
    from capture import take_screenshot
    take_screenshot()


def _set_volume(delta: str) -> None:
    try:
        subprocess.Popen(
            ['pactl', 'set-sink-volume', '@DEFAULT_SINK@', delta],
            close_fds=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass


def _get_volume_pct() -> int:
    """Current default-sink volume as a 0-100 int. Falls back to 50 on any
    failure so the HUD still shows a level rather than crashing on a device
    without pactl."""
    try:
        out = subprocess.check_output(
            ['pactl', 'get-sink-volume', '@DEFAULT_SINK@'],
            text=True, timeout=2, stderr=subprocess.DEVNULL,
        )
        for token in out.split():
            if token.endswith('%'):
                return max(0, min(100, int(token.rstrip('%'))))
    except Exception:
        pass
    return 50


# ---------------------------------------------------------------------------
# DisplayManager
# ---------------------------------------------------------------------------

class DisplayManager:
    """
    Idle/wake lifecycle + hardware button shortcuts.
    Always queries wlopm for actual display state so phoc's own idle timer
    blanking is handled transparently.
    """

    def __init__(
        self,
        on_wake: Callable[[], None] | None = None,
        on_power_menu: Callable[[], None] | None = None,
        on_screenshot: Callable[[], None] | None = None,
        on_fingerprint: Callable[[], None] | None = None,
        hud: object | None = None,
        get_blank_timeout: Callable[[], int] | None = None,
        on_blank_changed: Callable[[bool], None] | None = None,
        output_power: object | None = None,
        on_power_press: Callable[[], None] | None = None,
        on_media_key: Callable[[str], bool | None] | None = None,
    ) -> None:
        self._on_wake        = on_wake
        self._on_power_menu  = on_power_menu
        self._on_screenshot  = on_screenshot
        self._on_fingerprint = on_fingerprint
        # Power-key press hook (the shell silences the call ringer here):
        # fired on key-down, before the blank/wake toggle on key-up.
        self._on_power_press = on_power_press
        # Headset/media-key hook (the shell routes to the active MPRIS
        # player here): fired with 'play-pause'/'next'/'previous' on
        # key-down. Never let the hook break input handling.
        self._on_media_key = on_media_key
        # Blank-state subscriber (the shell's auto-lock schedule): fired
        # with True on every blank path (idle or power key) and False on
        # every wake, so the lock follows the display, not shell input.
        self._on_blank_changed = on_blank_changed
        # Volume/brightness HUD overlay (plan T3/T4). Optional: when absent the
        # volume keys still adjust audio, just without an on-screen indicator.
        self._hud = hud
        # Idle-blank timeout source (shell passes a live-config lambda so hot
        # reloads apply on the next reset_idle); None keeps the legacy default.
        self._get_blank_timeout = get_blank_timeout
        # Idle inhibitors (reason strings, e.g. an active call): while any is
        # held the idle timer never blanks the display.
        self._inhibitors: set[str] = set()

        self._saved_brightness: int   = 200
        self._idle_src: int | None    = None
        self._last_touch_time: float  = 0.0
        self._DOUBLE_TAP_MS = 600
        # Source of truth for blank state. No backend is queryable on every
        # device, so we track it ourselves and never depend on one to wake —
        # kept in sync with the compositor through the output-power mode
        # events (see _watch_output_power), never assumed.
        self._blanked = False

        # Real power-off backend (zwlr_output_power_manager_v1). Builds in
        # the background; tests pass a fake with the same `available` /
        # `set_all(on)` surface instead.
        self._output_power = (
            output_power if output_power is not None
            else _make_output_power()
        )
        # The compositor keeps output-power mode across shell restarts, so
        # a fresh manager must never assume lit: reconcile with the
        # compositor now (fake backends bound at construction) and on every
        # later bind (the real backend connects on the main loop).
        self._watch_output_power()

        # Power button long-press tracking
        self._power_down_at: float | None = None   # monotonic time of key-down
        self._power_long_src: int | None  = None   # GLib timer for long-press
        self._power_long_fired = False             # menu already shown this press
        # A power press that fired the screenshot combo or the long-press
        # menu is consumed: its key-up must not blank, whichever key is
        # released first. Cleared on the next power key-down.
        self._power_press_consumed = False

        # Combo state: both keys must be logically held simultaneously
        self._power_held   = False
        self._voldown_held = False

        # Watch every readable input node so power/volume wake the display
        # regardless of which event number the device landed on. The reader
        # threads filter in-thread (filter_evdev_data) so only actionable
        # events are posted to the GTK loop.
        self._fp_node = _detect_fp_node()
        self._wake_devs = _all_event_devices()
        for dev in self._wake_devs:
            _watch_evdev(dev, self._fp_node, self._on_event)

        # Safety: only ever blank the backlight if we actually have a way to
        # wake it back up. With no readable input device, leave the screen on.
        self._idle_enabled = bool(self._wake_devs)
        self.reset_idle()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def wake(self) -> None:
        """Wake the display if blanked, else just push the idle timer out."""
        if self._blanked:
            self._wake()
        else:
            self.reset_idle()

    def inhibit_idle(self, reason: str, active: bool) -> None:
        """Hold or release a named idle inhibitor (e.g. an active call).
        While any inhibitor is held the idle timer never blanks."""
        if active:
            self._inhibitors.add(reason)
        else:
            self._inhibitors.discard(reason)

    def reset_idle(self) -> None:
        if self._idle_src is not None:
            GLib.source_remove(self._idle_src)
            self._idle_src = None
        if not getattr(self, '_idle_enabled', True):
            return
        timeout = self._blank_timeout_secs()
        if timeout <= 0:
            return
        self._idle_src = GLib.timeout_add_seconds(timeout, self._on_idle)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _blank_timeout_secs(self) -> int:
        if self._get_blank_timeout is None:
            return _IDLE_SECS
        try:
            return max(0, int(self._get_blank_timeout()))
        except (TypeError, ValueError):
            return _IDLE_SECS

    def _on_idle(self) -> bool:
        self._idle_src = None
        if self._inhibitors:
            self.reset_idle()
            return GLib.SOURCE_REMOVE
        self._blank()
        return GLib.SOURCE_REMOVE

    def _backend_available(self, backend: object) -> bool:
        try:
            avail = backend.available  # type: ignore[union-attr]
            if callable(avail):
                avail = avail()
            return bool(avail)
        except Exception:
            return False

    def _watch_output_power(self) -> None:
        """Mirror the compositor-reported output-power state.

        Registers for the backend's bind/mode/failed events when it offers
        them (duck-typed so old fakes without listeners keep working),
        then reconciles immediately for backends that are already bound.
        """
        backend = getattr(self, '_output_power', None)
        if backend is None:
            return
        for method, callback in (
            ('add_ready_listener', self._adopt_output_power_state),
            ('add_mode_listener', self._on_output_power_mode),
            ('add_fail_listener', self._on_output_power_failed),
        ):
            register = getattr(backend, method, None)
            if callable(register):
                try:
                    register(callback)
                except Exception as exc:
                    log.warning('output power listener %s failed: %s',
                                method, exc)
        self._adopt_output_power_state()

    def _adopt_output_power_state(self) -> None:
        """Startup/bind reconciler: force every output on and restore the
        backlight, so a restart while dark cannot trap the next power-key
        press into blanking an already-dark screen. Skipped once this run
        has blanked on its own — that darkness is ours, not a stale
        compositor mode from before the restart."""
        if self._blanked:
            return
        backend = getattr(self, '_output_power', None)
        if backend is None or not self._backend_available(backend):
            return
        self._set_output_power(True)
        self._restore_backlight()
        self._blanked = False
        self.reset_idle()
        self._notify_blank_changed()

    def _restore_backlight(self) -> None:
        """Bring the backlight back only when it currently reads off.

        Unconditional restore would clobber the user's level on every
        restart (the saved level is only trustworthy within one run);
        a dark backlight after forcing outputs on means nobody restored
        it, so the saved level (or the constructor default) beats dark.
        """
        current = _brightnessctl('get')
        if current is not None and current.isdigit() and int(current) == 0:
            _brightnessctl('set', str(max(1, self._saved_brightness)))

    def _on_output_power_mode(self, on: bool) -> None:
        """The compositor reports an output-power mode change — including
        one made by someone else. Mirror it instead of our assumed state;
        a mode-on with a dark backlight also restores it, or the screen
        stays dark despite the compositor reporting on. Reports only real
        transitions: the startup force-on replays the mode we already
        mirror, and subscribers only care about change."""
        blanked = not on
        changed = blanked != self._blanked
        self._blanked = blanked
        if on:
            self._restore_backlight()
        self.reset_idle()
        if changed:
            self._notify_blank_changed()

    def _on_output_power_failed(self) -> None:
        """The compositor refused our mode change: that change never
        happened, so the mirrored blank state stays as it was. The backend
        already marked itself dead; future blank/wake use backlight/wlopm.
        """
        log.warning('output power mode change refused by compositor; '
                    'keeping mirrored blank state (%s)', self._blanked)

    def _set_output_power(self, on: bool) -> bool:
        """Power all outputs off/on over the output-power protocol. True
        only when the protocol actually handled it; False (never raises)
        means the caller must fall back to backlight/wlopm."""
        backend = getattr(self, '_output_power', None)
        if backend is None:
            return False
        try:
            avail = backend.available
            if callable(avail):
                avail = avail()
            if not avail:
                return False
            ok = bool(backend.set_all(on))
        except Exception as exc:
            log.warning('output power mode change failed: %s', exc)
            return False
        if not ok:
            log.warning('output power mode change refused; falling back')
        return ok

    def _blank(self) -> None:
        bright = _brightnessctl('get')
        if bright and bright.isdigit() and int(bright) > 0:
            self._saved_brightness = int(bright)
        self._blanked = True
        # The backlight goes off even when the protocol handled the
        # output: on Halium's hwcomposer phoc, output power off only
        # stops rendering and the panel stays lit (black, not off).
        protocol_ok = self._set_output_power(False)
        _brightnessctl('set', '0')
        if not protocol_ok:
            _wlopm('off')
        self._notify_blank_changed()

    def _wake(self) -> None:
        try:
            self._blanked = False
            if not self._set_output_power(True):
                _wlopm('on')
            _brightnessctl('set', str(max(1, self._saved_brightness)))
        except Exception as exc:
            # The screen must never stay dark: force the backlight up even
            # when the protocol or wlopm path blew up mid-wake.
            log.warning('wake failed: %s; forcing backlight restore', exc)
            _brightnessctl('set', str(max(1, self._saved_brightness)))
        self.reset_idle()
        self._notify_blank_changed()
        if self._on_wake:
            GLib.idle_add(self._on_wake)

    def _notify_blank_changed(self) -> None:
        cb = getattr(self, '_on_blank_changed', None)
        if cb is not None:
            cb(self._blanked)

    def _on_event(self, path: str, ev_type: int, code: int, value: int) -> bool:
        if ev_type != EV_KEY and ev_type != EV_ABS:
            return GLib.SOURCE_REMOVE

        # --- Power button ---
        if ev_type == EV_KEY and code == KEY_POWER:
            if value == 1:  # key down
                # A press while ringing only silences the ringer; the blank
                # toggle still happens on key-up below. Never let the hook
                # break power handling.
                if self._on_power_press is not None:
                    try:
                        self._on_power_press()
                    except Exception:
                        pass
                self._power_held = True
                self._power_long_fired = False
                self._power_press_consumed = False
                self._power_down_at = time.monotonic()
                # Check Power+VolDown combo immediately
                if self._voldown_held:
                    self._cancel_long_press()
                    self._trigger_screenshot()
                    return GLib.SOURCE_REMOVE
                # Start long-press timer
                self._power_long_src = GLib.timeout_add(
                    _POWER_LONG_PRESS_MS, self._on_power_long_press
                )
            elif value == 0:  # key up
                self._power_held = False
                was_long = self._cancel_long_press()
                if (not was_long and not self._power_press_consumed
                        and not self._voldown_held):
                    # Short press toggles blank/wake using our own state
                    if not self._blanked:
                        if self._idle_src is not None:
                            GLib.source_remove(self._idle_src)
                            self._idle_src = None
                        # No immediate lock here: _blank() notifies the
                        # blank subscriber, which schedules the lock after
                        # auto_lock_timeout seconds of darkness.
                        self._blank()
                    else:
                        self._wake()
            return GLib.SOURCE_REMOVE

        # --- Volume Down ---
        if ev_type == EV_KEY and code == KEY_VOLUMEDOWN:
            if value == 1:
                self._voldown_held = True
                if self._power_held:
                    self._cancel_long_press()
                    self._trigger_screenshot()
                else:
                    _set_volume('-5%')
                    self._show_volume_hud()
                    self.reset_idle()
            elif value == 0:
                self._voldown_held = False
            return GLib.SOURCE_REMOVE

        # --- Volume Up ---
        if ev_type == EV_KEY and code == KEY_VOLUMEUP:
            if value == 1:
                _set_volume('+5%')
                self._show_volume_hud()
                self.reset_idle()
            return GLib.SOURCE_REMOVE

        # --- Headset / media keys: press only, any node; repeats and
        # releases are dropped by filter_evdev_data, but a direct call
        # with any other value must stay a no-op too.
        if ev_type == EV_KEY and code in _MEDIA_KEY_ACTIONS:
            if value == 1:
                self.reset_idle()
                callback = getattr(self, '_on_media_key', None)
                if callable(callback):
                    try:
                        callback(_MEDIA_KEY_ACTIONS[code])
                    except Exception:
                        pass
            return GLib.SOURCE_REMOVE

        # --- Fingerprint touch → wake if off, else try auth ---
        if path == self._fp_node and ev_type == EV_KEY and value == 1:
            if self._blanked:
                self._wake()
            elif self._on_fingerprint:
                GLib.idle_add(self._on_fingerprint)
            return GLib.SOURCE_REMOVE

        # --- Touchscreen: never wakes a blanked screen (a phone in a pocket
        # must stay dark); while lit a touch only pushes the idle timer out.
        # Only the power key and the fingerprint node wake.
        if ev_type == EV_ABS and code == ABS_MT_TRACKING_ID and value >= 0:
            if not self._blanked:
                self._last_touch_time = 0.0
                self.reset_idle()

        return GLib.SOURCE_REMOVE

    def _on_power_long_press(self) -> bool:
        self._power_long_src = None
        self._power_long_fired = True
        self._power_press_consumed = True
        if self._on_power_menu:
            GLib.idle_add(self._on_power_menu)
        return GLib.SOURCE_REMOVE

    def _cancel_long_press(self) -> bool:
        """Cancel a pending long-press timer. Returns True only when the
        long-press already fired (power menu shown), so the key-up caller can
        suppress the short-press blank; a timer cancelled before firing was
        just a short press."""
        if self._power_long_src is not None:
            GLib.source_remove(self._power_long_src)
            self._power_long_src = None
        return self._power_long_fired

    def _trigger_screenshot(self) -> None:
        self._power_press_consumed = True
        if self._blanked:
            self._wake()
        if self._on_screenshot:
            GLib.idle_add(self._on_screenshot)
        else:
            _take_screenshot()

    def _show_volume_hud(self) -> None:
        """Flash the current volume level on the HUD overlay (if one is wired)."""
        if self._hud is not None:
            self._hud.show_volume(_get_volume_pct())


# ---------------------------------------------------------------------------
# evdev reader
# ---------------------------------------------------------------------------

def _watch_evdev(
    path: str,
    fp_node: str,
    callback: Callable[[str, int, int, int], bool],
) -> None:
    def _reader() -> None:
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            return
        last_touch_ms: float | None = None
        buf = b''
        try:
            while True:
                try:
                    chunk = os.read(fd, _EV_READ_BATCH * _EV_SIZE)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                cut = len(buf) // _EV_SIZE * _EV_SIZE
                if not cut:
                    continue
                wanted, last_touch_ms = filter_evdev_data(
                    buf[:cut], path, fp_node,
                    time.monotonic() * 1000.0, last_touch_ms,
                )
                buf = buf[cut:]
                for _p, t, c, v in wanted:
                    GLib.idle_add(
                        lambda p=_p, et=t, ec=c, ev=v: callback(p, et, ec, ev)
                    )
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    t = threading.Thread(target=_reader, daemon=True, name=f'evdev:{path}')
    t.start()
