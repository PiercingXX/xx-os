from __future__ import annotations

import gi
import threading
import time
from collections.abc import Callable

_LAYER_SHELL = False
try:
    gi.require_version('Gtk4LayerShell', '1.0')
    _LAYER_SHELL = True
except ValueError:
    pass

gi.require_version('Gtk', '4.0')

from gi.repository import Gdk, Gio, GLib, Gtk
from config import DESTRUCTIVE_TINT_BG, DANGER_RED, ON_DANGER_FG, ShellConfig, ThemePreset

if _LAYER_SHELL:
    from gi.repository import Gtk4LayerShell as LayerShell


def theme_css(preset: ThemePreset) -> str:
    # Toggle tiles render through the shared xx-tile builder
    # (common/xx_ui); this sheet owns the header type, the info cards
    # and the answer / end-call buttons.
    return f"""
.call-root {{
    background: {preset.background};
    color: {preset.foreground};
}}
.call-caller {{
    font-size: 32pt;
    font-weight: 400;
    color: {preset.foreground};
}}
.call-number {{
    font-size: 14pt;
    color: {preset.muted};
}}
.call-status {{
    font-size: 11pt;
    color: {preset.muted};
    letter-spacing: 0.14em;
}}
.call-timer {{
    font-size: 36pt;
    font-weight: 300;
    color: {preset.foreground};
    font-variant-numeric: tabular-nums;
}}
.call-card {{
    background: {preset.surface};
    border-radius: 16px;
    padding: 12px 16px;
}}
.call-card-icon {{
    color: {preset.accent};
}}
.call-btn {{
    font-size: 12pt;
    min-width: 100px;
    min-height: 56px;
    border-radius: 20px;
    border: none;
    padding: 0;
}}
.btn-accept {{
    background: {preset.foreground};
    color: {preset.background};
}}
.btn-accept:hover {{ background: {preset.surface_alt}; }}
.btn-decline {{
    background: transparent;
    color: {preset.foreground};
    border: 1px solid {preset.border};
}}
.btn-decline:hover {{ background: {preset.surface_alt}; }}
.btn-hangup {{
    background: {DANGER_RED};
    color: {ON_DANGER_FG};
}}
.btn-hangup:hover {{ background: {DESTRUCTIVE_TINT_BG}; }}
.btn-mute, .btn-speaker {{
    background: {preset.surface};
    color: {preset.foreground};
}}
.btn-mute:hover, .btn-speaker:hover {{ background: {preset.surface_alt}; }}
.call-bar-root {{
    background: {preset.surface};
    color: {preset.foreground};
    padding: 8px 16px;
}}
.call-bar-label {{
    font-size: 11pt;
    color: {preset.foreground};
}}
"""


def _is_live(call) -> bool:
    """A tracked call the surface must stay up for (anything but terminated)."""
    from modem_monitor import _STATE_TERMINATED
    return call.state != _STATE_TERMINATED


def primary_call(calls: list):
    """Snapshot pick, highest precedence first: incoming ringing, active,
    outgoing dialing/ringing-out, waiting, held, then any other live call."""
    from modem_monitor import (
        _DIR_INCOMING, _DIR_OUTGOING, _STATE_ACTIVE, _STATE_DIALING,
        _STATE_HELD, _STATE_RINGING_IN, _STATE_RINGING_OUT, _STATE_WAITING,
    )
    live = [c for c in calls if _is_live(c)]
    if not live:
        return None

    def _first(pred):
        return next((c for c in live if pred(c)), None)

    return (
        _first(lambda c: c.direction == _DIR_INCOMING and c.state == _STATE_RINGING_IN)
        or _first(lambda c: c.state == _STATE_ACTIVE)
        or _first(lambda c: c.direction == _DIR_OUTGOING
                  and c.state in (_STATE_DIALING, _STATE_RINGING_OUT))
        or _first(lambda c: c.state == _STATE_WAITING)
        or _first(lambda c: c.state == _STATE_HELD)
        or live[0]
    )


def waiting_call(calls: list, primary=None):
    """Second call arriving while one is live (MM state 6), if any."""
    from modem_monitor import _STATE_WAITING
    if primary is None:
        primary = primary_call(calls)
    for call in calls:
        if (_is_live(call) and call.state == _STATE_WAITING
                and (primary is None or call.path != primary.path)):
            return call
    return None


def select_page(calls: list) -> str | None:
    """Pure snapshot→page mapping: 'incoming' | 'outgoing' | 'active' | None
    when no live call remains (the surface must close itself)."""
    from modem_monitor import (
        _DIR_INCOMING, _DIR_OUTGOING, _STATE_DIALING, _STATE_RINGING_IN,
        _STATE_RINGING_OUT, _STATE_WAITING,
    )
    primary = primary_call(calls)
    if primary is None:
        return None
    if (primary.direction == _DIR_INCOMING
            and primary.state in (_STATE_RINGING_IN, _STATE_WAITING)):
        return 'incoming'
    if (primary.direction == _DIR_OUTGOING
            and primary.state in (_STATE_DIALING, _STATE_RINGING_OUT)):
        return 'outgoing'
    return 'active'


def format_elapsed(started_at: float, now: float) -> str:
    """Call timer text from monotonic timestamps (immune to clock jumps)."""
    elapsed = max(0, int(now - started_at))
    mins, secs = divmod(elapsed, 60)
    return f'{mins}:{secs:02d}'


def display_name(number: str) -> str:
    """Contact-book name for the remote number, else the formatted number."""
    if not number:
        return ''
    try:
        from contacts import shared_book, display_number
        return shared_book().lookup_number(number) or display_number(number)
    except Exception:
        return number


def caller_initials(name: str) -> str:
    """Avatar initials: first letters of the first two name words.

    Pure, so the digit-only remote side falls back to an empty (dashed)
    disc instead of digit soup.
    """
    words = [word for word in (name or '').split()
             if word and word[0].isalpha()]
    return ''.join(word[0].upper() for word in words[:2])


_EARPIECE_PROFILES = ('Voice Call', 'voice-call', 'HiFi Voice Call')
_EARPIECE_PORTS = ('output-earpiece', '[Out] Earpiece', 'Earpiece')
_SPEAKER_PROFILES = ('HiFi', 'hifi', 'A2DP Sink')
_SPEAKER_PORTS = ('output-speaker', '[Out] Speaker', 'Speaker')


def _pactl_card_names() -> list[str]:
    """Card NAMES from `pactl list cards` (worker thread only: blocking).

    Cards are addressed by name, never by index — under pipewire-pulse
    numeric ids are object ids that shift between boots."""
    import subprocess
    try:
        proc = subprocess.run(
            ['pactl', 'list', 'cards'], capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    names = []
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith('Name:'):
            name = stripped.split('Name:', 1)[1].strip()
            if name:
                names.append(name)
    return names


def _run_pactl_hunts(hunts: list[list[list[str]]]) -> None:
    """Run pactl hunts sequentially (worker thread only): within each hunt
    the first command that exits 0 wins. Failures stay silent — not every
    device/OS uses the same profile or port names."""
    import subprocess
    for hunt in hunts:
        for cmd in hunt:
            try:
                proc = subprocess.run(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=10,
                )
            except (FileNotFoundError, subprocess.SubprocessError):
                return
            if proc.returncode == 0:
                break


def _set_audio_route(earpiece: bool) -> None:
    """pactl fallback for in-call audio routing: set-card-profile by card
    NAME plus the earpiece/speaker sink port, sequentially in one worker
    thread so the GTK loop never blocks. Only used when callaudiod is
    absent; prefer CallAudioRouter, which selects the backend."""
    profiles = _EARPIECE_PROFILES if earpiece else _SPEAKER_PROFILES
    ports = _EARPIECE_PORTS if earpiece else _SPEAKER_PORTS

    def _work() -> None:
        hunts = [[[ 'pactl', 'set-card-profile', card, profile]
                   for profile in profiles]
                 for card in _pactl_card_names()]
        hunts.append([['pactl', 'set-sink-port', '@DEFAULT_SINK@', port]
                      for port in ports])
        _run_pactl_hunts(hunts)

    threading.Thread(target=_work, daemon=True).start()


def _set_source_mute(muted: bool) -> None:
    """pactl fallback for microphone mute (worker thread; see _set_audio_route)."""
    import subprocess

    def _work() -> None:
        try:
            subprocess.run(
                ['pactl', 'set-source-mute', '@DEFAULT_SOURCE@',
                 '1' if muted else '0'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            pass

    threading.Thread(target=_work, daemon=True).start()


_CALLAUDIO_BUS_NAME = 'org.mobian_project.CallAudio'
_CALLAUDIO_PATH = '/org/mobian_project/CallAudio'
_CALLAUDIO_IFACE = 'org.mobian_project.CallAudio'
_CALL_MODE = 1
_DEFAULT_MODE = 0

_DBUS_BUS_NAME = 'org.freedesktop.DBus'
_DBUS_PATH = '/org/freedesktop/DBus'
_DBUS_IFACE = 'org.freedesktop.DBus'


def _callaudio_call(bus, method: str, params) -> None:
    """Fire-and-forget async callaudiod method; failures only log."""
    from shell_log import get_logger

    def _on_done(conn, result) -> None:
        try:
            conn.call_finish(result)
        except Exception as err:  # noqa: BLE001 - audio must never break calls
            get_logger('call_ui').warning('callaudiod %s failed: %s', method, err)

    try:
        bus.call(
            _CALLAUDIO_BUS_NAME, _CALLAUDIO_PATH, _CALLAUDIO_IFACE, method,
            params, None, Gio.DBusCallFlags.NONE, 5_000, None, _on_done,
        )
    except GLib.Error as err:
        get_logger('call_ui').warning('callaudiod %s failed: %s', method, err)


class CallAudioRouter:
    """In-call audio routing: callaudiod when it owns its session-bus name,
    otherwise the pactl fallback. Backend presence is probed asynchronously
    and cached; every bus call is async and every pactl run is a worker
    thread, so the GTK loop never blocks."""

    def __init__(self, session_bus=None, run_pactl=None, run_mute=None) -> None:
        # Seams for tests: session_bus() -> connection|None,
        # run_pactl(earpiece) and run_mute(muted) replace the workers.
        self._session_bus = session_bus
        self._run_pactl = run_pactl
        self._run_mute = run_mute
        self._callaudio: bool | None = None
        self._in_call = False
        self._speaker = False

    def _bus(self):
        try:
            if self._session_bus is not None:
                return self._session_bus()
            return Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception:
            return None

    def _dispatch(self, callaudio_fn, pactl_fn) -> None:
        """Run one action on callaudiod when present, else the pactl path."""
        if self._callaudio is True:
            callaudio_fn(self._bus())
            return
        if self._callaudio is False or self._bus() is None:
            pactl_fn()
            return

        def _on_owner(conn, result) -> None:
            try:
                present = bool(conn.call_finish(result).unpack()[0])
            except Exception:
                present = False
            self._callaudio = present
            if present:
                callaudio_fn(conn)
            else:
                pactl_fn()

        self._bus().call(
            _DBUS_BUS_NAME, _DBUS_PATH, _DBUS_IFACE, 'GetNameOwner',
            GLib.Variant('(s)', (_CALLAUDIO_BUS_NAME,)), None,
            Gio.DBusCallFlags.NONE, 2_000, None, _on_owner,
        )

    def _pactl_route(self, earpiece: bool) -> None:
        if self._run_pactl is not None:
            self._run_pactl(earpiece)
        else:
            _set_audio_route(earpiece)

    def _pactl_mute(self, muted: bool) -> None:
        if self._run_mute is not None:
            self._run_mute(muted)
        else:
            _set_source_mute(muted)

    def enter_call(self) -> None:
        """Select call mode; entering twice is a no-op (idempotent)."""
        if self._in_call:
            return
        self._in_call = True
        self._dispatch(
            lambda bus: _callaudio_call(
                bus, 'SelectMode', GLib.Variant('(u)', (_CALL_MODE,))),
            lambda: self._pactl_route(True),
        )

    def exit_call(self) -> None:
        """Back to default mode once the last call ends; idempotent."""
        if not self._in_call:
            return
        self._in_call = False
        self._speaker = False
        self._dispatch(
            lambda bus: _callaudio_call(
                bus, 'SelectMode', GLib.Variant('(u)', (_DEFAULT_MODE,))),
            lambda: self._pactl_route(False),
        )

    def set_speaker(self, enabled: bool) -> None:
        self._speaker = enabled
        self._dispatch(
            lambda bus: _callaudio_call(
                bus, 'EnableSpeaker', GLib.Variant('(b)', (enabled,))),
            lambda: self._pactl_route(not enabled),
        )

    def set_mic_mute(self, muted: bool) -> None:
        self._dispatch(
            lambda bus: _callaudio_call(
                bus, 'MuteMic', GLib.Variant('(b)', (muted,))),
            lambda: self._pactl_mute(muted),
        )


_FEEDBACK_BUS_NAME = 'org.sigxcpu.Feedback'
_FEEDBACK_PATH = '/org/sigxcpu/Feedback'
_FEEDBACK_IFACE = 'org.sigxcpu.Feedback'
_RINGER_APP_ID = 'io.piercingxx.XXWM'
_RINGER_EVENT = 'phone-incoming-call'


def trigger_feedback_params(glib=None):
    """``(ssa{sv}i)`` params for feedbackd TriggerFeedback, built in one
    step with a plain dict (never nest a prebuilt container Variant)."""
    glib = glib if glib is not None else GLib
    return glib.Variant('(ssa{sv}i)',
                        (_RINGER_APP_ID, _RINGER_EVENT, {}, 0))


#: Synthesized originals (see data/sounds/NOTICE-original-sounds.txt):
#: the default ringtone, and the unknown-caller tone the dialer picks
#: for unknown-but-allowed-to-ring verdicts (Android: xx_unknown).
RINGTONE_DEFAULT = 'ringtone.ogg'
RINGTONE_UNKNOWN = 'unknown.ogg'


def _ringer_sound_play(name: str = RINGTONE_DEFAULT) -> None:
    import sound
    sound.play(name, loop=True)


def _ringer_sound_stop() -> None:
    import sound
    sound.stop()


class Ringer:
    """Incoming-call ringer with no new assets: feedbackd plays the system
    ringtone, vibrates, and respects the silent profile; the sound loop is
    only the fallback when feedbackd is absent. Every bus call is async."""

    def __init__(self, session_bus=None, play=None, stop=None) -> None:
        # Seams for tests: session_bus() -> connection|None; play/stop
        # replace the sound fallback.
        self._session_bus = session_bus
        self._play = play
        self._stop = stop
        self._feedback_id: int | None = None
        self._ringing = False
        self._tone = RINGTONE_DEFAULT

    @property
    def ringing(self) -> bool:
        return self._ringing

    def _bus(self):
        try:
            if self._session_bus is not None:
                return self._session_bus()
            return Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception:
            return None

    def _sound_play(self) -> None:
        if self._play is not None:
            self._play()
        else:
            _ringer_sound_play(self._tone)

    def _sound_stop(self) -> None:
        if self._stop is not None:
            self._stop()
        else:
            _ringer_sound_stop()

    def start(self, tone: str = RINGTONE_DEFAULT) -> None:
        """Ring once; repeated starts while ringing are a no-op."""
        if self._ringing:
            return
        self._ringing = True
        self._tone = tone or RINGTONE_DEFAULT
        bus = self._bus()
        if bus is None:
            self._sound_play()
            return

        def _on_triggered(conn, result) -> None:
            try:
                reply = conn.call_finish(result).unpack()
            except Exception:
                reply = None
            self._finish_start(reply)

        try:
            # Timeout 0 loops the event until EndFeedback; -1 would play
            # only the event's default duration (once, then silence).
            bus.call(
                _FEEDBACK_BUS_NAME, _FEEDBACK_PATH, _FEEDBACK_IFACE,
                'TriggerFeedback',
                trigger_feedback_params(),
                None, Gio.DBusCallFlags.NONE, 2_000, None, _on_triggered,
            )
        except GLib.Error:
            self._finish_start(None)

    def _finish_start(self, reply) -> None:
        if not self._ringing:
            # Stopped while the trigger was in flight: release the stray id.
            if reply is not None:
                try:
                    self._end_feedback(int(reply[0]))
                except (TypeError, ValueError, IndexError):
                    pass
            return
        if reply is None:
            # No feedbackd on the bus: fall back to the sound loop.
            self._sound_play()
            return
        try:
            self._feedback_id = int(reply[0])
        except (TypeError, ValueError, IndexError):
            self._sound_play()

    def _end_feedback(self, feedback_id: int) -> None:
        bus = self._bus()
        if bus is None:
            return

        def _on_ended(conn, result) -> None:
            try:
                conn.call_finish(result)
            except Exception:
                pass

        try:
            bus.call(
                _FEEDBACK_BUS_NAME, _FEEDBACK_PATH, _FEEDBACK_IFACE,
                'EndFeedback', GLib.Variant('(u)', (feedback_id,)),
                None, Gio.DBusCallFlags.NONE, 2_000, None, _on_ended,
            )
        except GLib.Error:
            pass

    def stop(self) -> None:
        """Stop on every terminal path; always safe to call twice."""
        self._ringing = False
        feedback_id, self._feedback_id = self._feedback_id, None
        if feedback_id is not None:
            self._end_feedback(feedback_id)
        self._sound_stop()

    def silence(self) -> None:
        """Power-key entry: quiet the ringer without touching call state."""
        self.stop()


_SENSOR_BUS_NAME = 'net.hadess.SensorProxy'
_SENSOR_PATH = '/net/hadess/SensorProxy'
_SENSOR_IFACE = 'net.hadess.SensorProxy'
_SENSOR_PROPS_IFACE = 'org.freedesktop.DBus.Properties'


class ProximityMonitor:
    """Ear-proximity blanking while a call is active and speaker is off:
    claims proximity from iio-sensor-proxy and blanks/unblanks the display
    through the shell's DisplayManager, ignoring touches on the call UI
    while near. An absent sensor (or bus) is a silent no-op."""

    def __init__(self, system_bus=None) -> None:
        # Seam for tests: system_bus() -> connection|None.
        self._system_bus = system_bus
        self._claimed = False
        self._near = False
        self._blanked_by_us = False
        self._sub_id: int | None = None
        self._display = None
        self._surface = None

    @property
    def claimed(self) -> bool:
        return self._claimed

    @property
    def near(self) -> bool:
        return self._near

    def _bus(self):
        try:
            if self._system_bus is not None:
                return self._system_bus()
            return Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except Exception:
            return None

    def start(self, display_mgr, surface) -> None:
        """Claim proximity for this call (idempotent; refreshes the refs)."""
        self._display = display_mgr
        self._surface = surface
        if self._claimed or display_mgr is None:
            return
        bus = self._bus()
        if bus is None:
            return
        try:
            bus.call(
                _SENSOR_BUS_NAME, _SENSOR_PATH, _SENSOR_IFACE,
                'ClaimProximity', None, None,
                Gio.DBusCallFlags.NONE, 2_000, None, self._on_claimed,
            )
        except GLib.Error:
            pass

    def _on_claimed(self, conn, result) -> None:
        try:
            conn.call_finish(result)
        except Exception:
            return  # no proximity sensor: stay a no-op
        if self._display is None:
            self._release(conn)
            return
        self._claimed = True
        bus = self._bus() or conn
        try:
            self._sub_id = bus.signal_subscribe(
                _SENSOR_BUS_NAME, _SENSOR_PROPS_IFACE, 'PropertiesChanged',
                _SENSOR_PATH, None, Gio.DBusSignalFlags.NONE,
                self._on_properties_changed, None,
            )
        except GLib.Error:
            self._sub_id = None
        try:
            bus.call(
                _SENSOR_BUS_NAME, _SENSOR_PATH, _SENSOR_PROPS_IFACE, 'Get',
                GLib.Variant('(ss)', (_SENSOR_IFACE, 'ProximityNear')), None,
                Gio.DBusCallFlags.NONE, 2_000, None, self._on_have_near,
            )
        except GLib.Error:
            pass

    def _on_properties_changed(
        self, _conn, _sender, _path, _iface, _signal, params, _user_data,
    ) -> None:
        try:
            unpacked = params.unpack()
        except Exception:
            return
        if unpacked[0] != _SENSOR_IFACE:
            return
        try:
            changed = dict(unpacked[1])
        except (TypeError, ValueError):
            return
        if 'ProximityNear' not in changed:
            return
        self._apply_near(bool(changed['ProximityNear']))

    def _on_have_near(self, conn, result) -> None:
        try:
            value = conn.call_finish(result).unpack()[0]
            near = value.unpack() if hasattr(value, 'unpack') else bool(value)
        except Exception:
            return
        self._apply_near(near)

    def _apply_near(self, near: bool) -> None:
        self._near = near
        if self._surface is not None:
            try:
                self._surface.set_sensitive(not near)
            except Exception:
                pass
        if self._display is None:
            return
        try:
            if near:
                self._display._blank()
                self._blanked_by_us = True
            else:
                self._blanked_by_us = False
                self._display.wake()
        except Exception:
            pass

    def _release(self, bus) -> None:
        try:
            bus.call(
                _SENSOR_BUS_NAME, _SENSOR_PATH, _SENSOR_IFACE,
                'ReleaseProximity', None, None,
                Gio.DBusCallFlags.NONE, 2_000, None, None,
            )
        except GLib.Error:
            pass

    def stop(self) -> None:
        """Release the claim and restore touch + display; always idempotent."""
        bus = self._bus()
        if self._sub_id is not None and bus is not None:
            try:
                bus.signal_unsubscribe(self._sub_id)
            except Exception:
                pass
        self._sub_id = None
        if self._claimed:
            self._claimed = False
            if bus is not None:
                self._release(bus)
        self._near = False
        if self._surface is not None:
            try:
                self._surface.set_sensitive(True)
            except Exception:
                pass
            self._surface = None
        display, self._display = self._display, None
        if self._blanked_by_us and display is not None:
            self._blanked_by_us = False
            try:
                display.wake()
            except Exception:
                pass


def _resolve_call_path() -> str | None:
    from modem_monitor import active_call_path
    return active_call_path()


def _resolve_modem_path() -> str | None:
    """Cached modem path only (no bus I/O): safe on the GTK thread."""
    try:
        from modem_monitor import _last_monitor
        if _last_monitor is None:
            return None
        return _last_monitor.modem_path()
    except Exception:
        return None


# Completion contract shared with modem_monitor: (success, error_message).
CallResultCallback = Callable[[bool, str | None], None]
# Injectable transport seam: start the async action, deliver via callback.
CallActionFn = Callable[[str, CallResultCallback], None]


def _log_action_failure(action: str, call_path: str | None,
                        error: str | None = None) -> None:
    from shell_log import get_logger
    detail = f': {error}' if error else ''
    get_logger('call_ui').warning('failed to %s call (path=%s)%s', action, call_path, detail)


def _default_accept(call_path: str, on_done: CallResultCallback) -> None:
    from modem_monitor import accept_call
    accept_call(call_path, on_done)


def _default_hangup(call_path: str, on_done: CallResultCallback) -> None:
    from modem_monitor import hangup_call
    hangup_call(call_path, on_done)


# Injectable seam for the waiting-call "Hold & answer" action.
CallHoldFn = Callable[[str, CallResultCallback], None]
# Injectable seam for in-call DTMF digits.
CallDtmfFn = Callable[[str, str, CallResultCallback], None]


def _default_hold_accept(modem_path: str, on_done: CallResultCallback) -> None:
    """Asynchronously HoldAndAccept the active call on ``modem_path`` so the
    waiting call can be answered; same delivery contract as accept_call."""
    from modem_monitor import _last_monitor
    from shell_log import get_logger

    def _fail(message: str) -> None:
        get_logger('call_ui').warning('%s HoldAndAccept failed: %s', modem_path, message)
        on_done(False, message)

    monitor = _last_monitor
    bus = monitor._bus if monitor is not None else None
    if bus is None:
        _fail('system bus unavailable')
        return

    def _on_called(conn, result) -> None:
        try:
            conn.call_finish(result)
        except Exception as err:  # noqa: BLE001 - exactly-once contract
            _fail(getattr(err, 'message', None) or str(err))
            return
        on_done(True, None)

    try:
        from modem_monitor import _MM1, _VOICE_IFACE
        bus.call(
            _MM1, modem_path, _VOICE_IFACE, 'HoldAndAccept', None, None,
            Gio.DBusCallFlags.NONE, 5_000, None, _on_called,
        )
    except GLib.Error as err:
        _fail(getattr(err, 'message', None) or str(err))


def _default_dtmf(call_path: str, digit: str, on_done: CallResultCallback) -> None:
    """Asynchronously SendDtmf on a live call; same contract as accept_call."""
    from shell_log import get_logger

    def _fail(message: str) -> None:
        get_logger('call_ui').warning(
            'SendDtmf %s on %s failed: %s', digit, call_path, message)
        on_done(False, message)

    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    except Exception as err:
        _fail(str(err))
        return
    if bus is None:
        _fail('system bus unavailable')
        return

    def _on_sent(conn, result) -> None:
        try:
            conn.call_finish(result)
        except Exception as err:  # noqa: BLE001 - exactly-once contract
            _fail(getattr(err, 'message', None) or str(err))
            return
        on_done(True, None)

    try:
        from modem_monitor import _CALL_IFACE, _MM1
        bus.call(
            _MM1, call_path, _CALL_IFACE, 'SendDtmf',
            GLib.Variant('(s)', (digit,)), None,
            Gio.DBusCallFlags.NONE, 5_000, None, _on_sent,
        )
    except GLib.Error as err:
        _fail(getattr(err, 'message', None) or str(err))


class CallBar(Gtk.Window):
    """Persistent in-call bar shown at the top of home screen during an active call."""

    def __init__(self, on_expand: Callable[[], None],
                 config: ShellConfig | None = None) -> None:
        super().__init__(title='PiercingXX Call Bar')

        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.init_for_window(self)
            LayerShell.set_layer(self, LayerShell.Layer.TOP)
            LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
            LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
            LayerShell.set_anchor(self, LayerShell.Edge.RIGHT, True)
            LayerShell.set_anchor(self, LayerShell.Edge.BOTTOM, False)
            LayerShell.set_exclusive_zone(self, 48)
        else:
            self.set_default_size(420, 48)

        self._on_expand = on_expand
        # The shell window threads its LIVE config so hot reloads reach this
        # surface; the fallback keeps direct no-arg construction working.
        self._config = config if config is not None else ShellConfig()
        self._shown = False
        self._theme_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self._theme_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 5,
        )
        self.apply_theme()
        self.set_child(self._build())

    def apply_theme(self, preset: ThemePreset | None = None) -> None:
        """(Re)load this bar's display-level sheet. The shell window passes
        the freshly resolved preset on hot-reload fan-out; standalone falls
        back to the injected config's own preset."""
        data = theme_css(preset if preset is not None else self._config.theme)
        self._theme_provider.load_from_data(data.encode('utf-8'))

    def _build(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        root.add_css_class('call-bar-root')

        self._label = Gtk.Label(label='In call', xalign=0)
        self._label.add_css_class('call-bar-label')
        self._label.set_hexpand(True)

        expand_btn = Gtk.Button(label='↓')
        expand_btn.add_css_class('flat')
        expand_btn.add_css_class('call-bar-label')
        expand_btn.connect('clicked', lambda _b: self._on_expand())

        root.append(self._label)
        root.append(expand_btn)
        return root

    def show_bar(self, number: str) -> None:
        text = number or 'In call'
        if self._shown and self._label.get_text() == text:
            return
        self._label.set_text(text)
        if not self._shown:
            self._shown = True
            self.present()

    def hide_bar(self) -> None:
        if not self._shown:
            return
        self._shown = False
        self.hide()

    def update(self, caller: str, timer_text: str) -> None:
        self._label.set_text(f'{caller}  ·  {timer_text}')


def _fit_page(page):
    """A call page that scrolls instead of growing the window: on a
    320x640 screen the active page (avatar, timer, six tiles, End call)
    needs ~680 px. Where it fits, the page fills the viewport and its
    spacers center it exactly as before."""
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.set_has_frame(False)
    scroller.set_child(page)
    return scroller


class CallUI(Gtk.Window):
    """
    Full-screen call surface, rendered from the modem monitor snapshot
    (render_snapshot) instead of the legacy incoming/answered/ended
    adapters: incoming ringing, outgoing dialing, active (timer, mute,
    speaker, DTMF keypad, hang up), and a waiting-second-call banner.
    Minimize collapses to the CallBar; the surface always stays
    recoverable — failures and empty snapshots close it, and a Close
    button appears when the modem stops answering.
    """

    def __init__(
        self,
        on_accept: Callable[[], None] | None = None,
        on_decline: Callable[[], None] | None = None,
        on_hangup: Callable[[], None] | None = None,
        accept_fn: CallActionFn | None = None,
        hangup_fn: CallActionFn | None = None,
        hold_accept_fn: CallHoldFn | None = None,
        dtmf_fn: CallDtmfFn | None = None,
        config: ShellConfig | None = None,
    ) -> None:
        super().__init__(title='PiercingXX Call')

        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.init_for_window(self)
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM,
                         LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
                LayerShell.set_anchor(self, edge, True)
            LayerShell.set_exclusive_zone(self, -1)
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        else:
            self.set_default_size(420, 860)
            self.fullscreen()

        self._on_accept = on_accept or (lambda: None)
        self._on_decline = on_decline or (lambda: None)
        self._on_hangup = on_hangup or (lambda: None)
        self._accept_fn = accept_fn or _default_accept
        self._hangup_fn = hangup_fn or _default_hangup
        self._hold_accept_fn = hold_accept_fn or _default_hold_accept
        self._dtmf_fn = dtmf_fn or _default_dtmf
        # The shell window threads its LIVE config so hot reloads reach this
        # surface; the fallback keeps direct no-arg construction working.
        self._config = config if config is not None else ShellConfig()
        self._incoming_call_path: str | None = None
        self._incoming_path: str | None = None
        self._primary_path: str | None = None
        self._waiting_path: str | None = None
        self._action_path: str | None = None
        self._pending_action: str | None = None
        self._surface_live = False
        self._minimized = False
        self._presented_page: str | None = None
        self._presented_path: str | None = None
        self._live_snapshot: list = []
        self._current_caller = ''
        self._current_number = ''
        self._muted = False
        self._speakerphone = False
        self._timed_path: str | None = None
        self._call_start_mono: float | None = None
        self._timer_id: int | None = None
        self._close_src: int | None = None
        self._bar = None
        self._display_mgr = None
        self._close_btns: list = []
        self._ringer = Ringer()
        self._audio = CallAudioRouter()
        self._proximity = ProximityMonitor()

        self._theme_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self._theme_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 5,
        )
        self.apply_theme()

        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=180)
        self._stack.add_named(_fit_page(self._build_incoming()), 'incoming')
        self._stack.add_named(_fit_page(self._build_outgoing()), 'outgoing')
        self._stack.add_named(_fit_page(self._build_active()), 'active')

        root = Gtk.Box()
        root.add_css_class('call-root')
        root.set_hexpand(True)
        root.set_vexpand(True)
        root.append(self._stack)
        self.set_child(root)

    def bind_bar(self, bar) -> None:
        """Attach the CallBar: per-second timer updates and minimize target."""
        self._bar = bar

    def apply_theme(self, preset: ThemePreset | None = None) -> None:
        """(Re)load this surface's display-level sheet. The shell window
        passes the freshly resolved preset on hot-reload fan-out; standalone
        falls back to the injected config's own preset."""
        data = theme_css(preset if preset is not None else self._config.theme)
        self._theme_provider.load_from_data(data.encode('utf-8'))

    def _page_shell(self, status_text: str, centered: bool = False) -> tuple:
        """Shared full-screen page: status + avatar + caller + number over
        a bottom action area, with Minimize and a hidden modem-timeout
        Close."""
        from gi.repository import Pango
        from xx_ui import widgets
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.set_margin_top(64)
        page.set_margin_start(20)
        page.set_margin_end(20)
        xalign = 0.5 if centered else 0.0

        status = Gtk.Label(label=status_text.upper(), xalign=xalign)
        status.add_css_class('call-status')
        status.set_hexpand(True)

        avatar = widgets.avatar('', 88, dashed=True)
        avatar.set_halign(
            Gtk.Align.CENTER if centered else Gtk.Align.START)
        avatar.set_margin_top(24)
        avatar.set_margin_bottom(8)

        caller = Gtk.Label(label='', xalign=xalign)
        caller.add_css_class('call-caller')
        caller.set_hexpand(True)
        caller.set_wrap(True)
        caller.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        caller.set_max_width_chars(12)

        number = Gtk.Label(label='', xalign=xalign)
        number.add_css_class('call-number')
        number.set_hexpand(True)
        number.set_wrap(True)
        number.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        number.set_max_width_chars(24)
        if centered:
            # A name that wraps keeps every line on the center axis, not
            # a left-ragged block under a centered avatar.
            caller.set_justify(Gtk.Justification.CENTER)
            number.set_justify(Gtk.Justification.CENTER)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)

        page.append(status)
        page.append(avatar)
        page.append(caller)
        page.append(number)
        page.append(spacer)
        return page, caller, number, avatar

    @staticmethod
    def _set_avatar(avatar, name: str) -> None:
        """Paint an avatar disc from a caller name; unknown sides stay a
        dashed empty disc. Getattr-guarded by callers so bare test
        harnesses (built without __init__) keep working."""
        try:
            initials = caller_initials(name)
            avatar.set_text(initials)
            if initials:
                avatar.remove_css_class('dashed')
            else:
                avatar.add_css_class('dashed')
        except Exception:
            pass

    def _paint_avatar(self, key: str, name: str) -> None:
        """Repaint a page's avatar disc from the caller name. The getattr
        keeps bare test harnesses (built without __init__) working."""
        avatar = getattr(self, key, None)
        if avatar is not None:
            self._set_avatar(avatar, name)

    def _page_footer(self, page: Gtk.Widget) -> None:
        """Minimize + hidden Close row shared by every page."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        row.set_halign(Gtk.Align.CENTER)
        row.set_margin_bottom(24)

        minimize_btn = Gtk.Button(label='Minimize')
        minimize_btn.add_css_class('call-btn')
        minimize_btn.add_css_class('btn-decline')
        minimize_btn.connect('clicked', self._on_minimize_clicked)
        row.append(minimize_btn)

        close_btn = Gtk.Button(label='Close')
        close_btn.add_css_class('call-btn')
        close_btn.add_css_class('btn-decline')
        close_btn.set_visible(False)
        close_btn.connect('clicked', self._on_close_clicked)
        row.append(close_btn)
        self._close_btns.append(close_btn)

        page.append(row)

    def _build_incoming(self) -> Gtk.Widget:
        page, caller, number, avatar = self._page_shell(
            'Incoming call', centered=True)
        self._inc_caller, self._inc_number = caller, number
        self._inc_avatar = avatar

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_row.set_hexpand(True)
        btn_row.set_homogeneous(True)
        btn_row.set_margin_bottom(24)

        decline_btn = Gtk.Button(label='Decline')
        decline_btn.add_css_class('call-btn')
        decline_btn.add_css_class('btn-decline')
        decline_btn.connect('clicked', self._on_decline_clicked)
        decline_btn.set_hexpand(True)

        accept_btn = Gtk.Button(label='Answer')
        accept_btn.add_css_class('call-btn')
        accept_btn.add_css_class('btn-accept')
        accept_btn.connect('clicked', self._on_accept_clicked)
        accept_btn.set_hexpand(True)

        btn_row.append(decline_btn)
        btn_row.append(accept_btn)
        page.append(btn_row)
        self._page_footer(page)
        return page

    def _build_outgoing(self) -> Gtk.Widget:
        page, caller, number, avatar = self._page_shell('Calling…')
        self._out_caller, self._out_number = caller, number
        self._out_avatar = avatar

        hangup_btn = Gtk.Button(label='End call')
        hangup_btn.add_css_class('call-btn')
        hangup_btn.add_css_class('btn-hangup')
        hangup_btn.set_hexpand(True)
        hangup_btn.set_margin_bottom(24)
        hangup_btn.connect('clicked', self._on_hangup_clicked)

        page.append(hangup_btn)
        self._page_footer(page)
        return page

    def _build_active(self) -> Gtk.Widget:
        from xx_ui import widgets
        page, caller, number, avatar = self._page_shell(
            'Active call', centered=True)
        self._act_caller, self._act_number = caller, number
        self._act_avatar = avatar

        self._act_timer = Gtk.Label(label='0:00', xalign=0.5)
        self._act_timer.add_css_class('call-timer')
        self._act_timer.set_hexpand(True)
        self._act_timer.set_margin_top(8)
        self._act_timer.set_margin_bottom(8)
        page.append(self._act_timer)

        # Waiting second call (MM state 6): Hold & answer / Decline.
        self._waiting_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self._waiting_box.add_css_class('call-card')
        self._waiting_box.set_halign(Gtk.Align.CENTER)
        self._waiting_box.set_margin_bottom(12)
        self._waiting_box.set_visible(False)
        self._waiting_label = Gtk.Label(label='', xalign=0)
        self._waiting_label.add_css_class('call-bar-label')
        self._waiting_label.set_wrap(True)
        _wait_icon = widgets.icon_image('call-start-symbolic', 20)
        _wait_icon.add_css_class('call-card-icon')
        self._waiting_box.append(_wait_icon)
        hold_btn = Gtk.Button(label='Hold & answer')
        hold_btn.add_css_class('call-btn')
        hold_btn.add_css_class('btn-accept')
        hold_btn.connect('clicked', self._on_hold_answer_clicked)
        wait_decline_btn = Gtk.Button(label='Decline')
        wait_decline_btn.add_css_class('call-btn')
        wait_decline_btn.add_css_class('btn-decline')
        wait_decline_btn.connect('clicked', self._on_waiting_decline_clicked)
        self._waiting_box.append(self._waiting_label)
        self._waiting_box.append(hold_btn)
        self._waiting_box.append(wait_decline_btn)
        page.append(self._waiting_box)

        self._mute_btn = widgets.status_tile(
            'Mute', '', False, self._on_mute_toggled)

        self._speaker_btn = widgets.status_tile(
            'Speaker', '', False, self._on_speaker_toggled)

        self._keypad_btn = widgets.status_tile(
            'Keypad', '', False, self._on_keypad_toggled)
        self._hold_btn = widgets.status_tile(
            'Hold', '', False, self._on_hold_toggled)
        self._add_btn = widgets.status_tile(
            'Add call', '', False, self._on_add_call_toggled)
        # 'BT' as on the shade's tile: three caps tiles across must fit a
        # 360 px screen at large text (BLUETOOTH alone outgrew a third).
        self._bt_btn = widgets.status_tile(
            'BT', '', False, self._on_bt_toggled)
        for tile in (self._mute_btn, self._speaker_btn, self._keypad_btn,
                     self._hold_btn, self._add_btn, self._bt_btn):
            # Two-word labels (ADD CALL) wrap rather than widen the grid.
            name = tile.get_child().get_first_child()
            if name is not None:
                widgets.wrap_to_fit(name)

        tiles = Gtk.Grid(row_spacing=8, column_spacing=8,
                         column_homogeneous=True)
        tiles.set_hexpand(True)
        tiles.set_margin_bottom(16)
        for col, tile in enumerate((self._mute_btn, self._keypad_btn,
                                   self._speaker_btn)):
            tiles.attach(tile, col, 0, 1, 1)
        for col, tile in enumerate((self._hold_btn, self._add_btn,
                                   self._bt_btn)):
            tiles.attach(tile, col, 1, 1, 1)
        page.append(tiles)

        self._keypad_grid = Gtk.Grid(row_spacing=8, column_spacing=8)
        self._keypad_grid.set_halign(Gtk.Align.CENTER)
        self._keypad_grid.set_margin_bottom(16)
        self._keypad_grid.set_visible(False)
        for idx, digit in enumerate('123456789*0#'):
            key = Gtk.Button(label=digit)
            key.add_css_class('call-btn')
            key.add_css_class('btn-mute')
            key.connect('clicked', self._on_dtmf_clicked, digit)
            self._keypad_grid.attach(key, idx % 3, idx // 3, 1, 1)
        page.append(self._keypad_grid)

        hangup_btn = Gtk.Button(label='End call')
        hangup_btn.add_css_class('call-btn')
        hangup_btn.add_css_class('btn-hangup')
        hangup_btn.set_hexpand(True)
        hangup_btn.set_margin_bottom(24)
        hangup_btn.connect('clicked', self._on_hangup_clicked)
        page.append(hangup_btn)
        self._page_footer(page)
        return page

    def start_ringing(self, tone: str = RINGTONE_DEFAULT) -> None:
        """Start the incoming-call ringer (idempotent; window gates on DnD)."""
        self._ringer.start(tone)

    def stop_ringing(self) -> None:
        self._ringer.stop()

    def silence_ringer(self) -> None:
        """Power-key entry: quiet the ringer without touching call state."""
        self._ringer.silence()

    def show_incoming(self, caller: str, number: str, call_path: str | None = None) -> None:
        path = call_path or _resolve_call_path()
        self._incoming_call_path = path
        self._incoming_path = path
        self._primary_path = path
        self._pending_action = None
        self._surface_live = True
        self._current_caller = caller
        self._current_number = number
        self._inc_caller.set_text(caller or number)
        self._inc_number.set_text(number if caller else '')
        self._paint_avatar('_inc_avatar', caller or number)
        self._stack.set_visible_child_name('incoming')
        self._maybe_present(path, 'incoming')

    def show_outgoing(self, caller: str, number: str, call_path: str | None = None) -> None:
        path = call_path or _resolve_call_path()
        self._primary_path = path
        self._pending_action = None
        self._surface_live = True
        self._current_caller = caller
        self._current_number = number
        self._out_caller.set_text(caller or number)
        self._out_number.set_text(number if caller else '')
        self._paint_avatar('_out_avatar', caller or number)
        self._stack.set_visible_child_name('outgoing')
        self._maybe_present(path, 'outgoing')

    def show_active(
        self, caller: str, number: str, started_at: float | None = None,
    ) -> None:
        # Attached mid-call (answered before the UI appeared, or shell
        # started during the call): resolve the path so hangup can work.
        path = self._primary_path or _resolve_call_path()
        self._incoming_call_path = self._incoming_call_path or path
        self._primary_path = path
        self._pending_action = None
        self._surface_live = True
        self._current_caller = caller
        self._current_number = number
        self._act_caller.set_text(caller or number)
        self._act_number.set_text(number if caller else '')
        self._paint_avatar('_act_avatar', caller or number)
        self._adopt_timer_base(path, started_at)
        self._reset_toggles()
        self._stack.set_visible_child_name('active')
        self._maybe_present(path, 'active')
        self._refresh_timer_label()

    def _adopt_timer_base(self, path: str | None, started_at: float | None) -> None:
        """Pin the timer to the call's ACTIVE transition: re-renders and
        HELD→ACTIVE flips keep the running base; only a new call resets it."""
        if started_at is not None and (
                self._timed_path != path or self._call_start_mono is None):
            self._call_start_mono = started_at
            self._timed_path = path
        elif self._call_start_mono is None:
            self._call_start_mono = time.monotonic()
            self._timed_path = path

    def _reset_toggles(self) -> None:
        self._muted = False
        self._speakerphone = False
        for btn, state in ((getattr(self, '_mute_btn', None), False),
                           (getattr(self, '_speaker_btn', None), False),
                           (getattr(self, '_keypad_btn', None), False),
                           (getattr(self, '_hold_btn', None), False),
                           (getattr(self, '_add_btn', None), False),
                           (getattr(self, '_bt_btn', None), False)):
            if btn is not None:
                try:
                    btn.set_active(state)
                except Exception:
                    pass
        grid = getattr(self, '_keypad_grid', None)
        if grid is not None:
            try:
                grid.set_visible(False)
            except Exception:
                pass

    def render_snapshot(self, calls: list, display_mgr=None) -> None:
        """Rebuild the surface from the modem monitor snapshot.

        Owns the page, the timer base, audio mode, proximity, the ringer
        (stopped whenever nothing is ringing in), and the CallBar. A new
        primary path un-minimizes; an empty snapshot closes everything."""
        if display_mgr is not None:
            self._display_mgr = display_mgr
        live = [c for c in calls if _is_live(c)]
        self._live_snapshot = list(live)
        if not live:
            self._close_all()
            return
        primary = primary_call(calls)
        if primary is None:  # defensive: _is_live disagrees with the pick
            self._close_all()
            return
        if primary.path != self._primary_path:
            self._minimized = False
        waiting = waiting_call(calls, primary)
        page = select_page(calls)
        name = display_name(primary.number)
        title = name or primary.number or 'Unknown call'
        number = primary.number
        if page == 'incoming':
            self.show_incoming(name, number, primary.path)
        elif page == 'outgoing':
            self.show_outgoing(name, number, primary.path)
        else:
            self._render_active_call(primary, waiting, name, number)
        if page != 'incoming':
            # The ringer only ever covers incoming ringing; anything else
            # (answered elsewhere, outgoing, held) silences it.
            try:
                self._ringer.stop()
            except Exception:
                pass
        self._update_audio(live)
        self._update_proximity(live)
        if self._bar is not None:
            try:
                self._bar.show_bar(title)
            except Exception:
                pass
        self._ensure_tick()

    def _primary_held(self) -> bool:
        """True while the snapshot's primary call sits HELD (the HOLD
        tile's inverted visual tracks this, never a local guess)."""
        from modem_monitor import _STATE_HELD
        primary = primary_call(self._live_snapshot)
        return primary is not None and primary.state == _STATE_HELD

    def _sync_hold_tile(self) -> None:
        """Paint the HOLD tile from the snapshot; a programmatic set that
        changes nothing fires no toggled, so the handler below only ever
        sees real user taps."""
        hold = getattr(self, '_hold_btn', None)
        if hold is None:
            return
        try:
            held = self._primary_held()
        except Exception:
            return
        try:
            if hold.get_active() != held:
                hold.set_active(held)
        except Exception:
            pass

    def _render_active_call(self, primary, waiting, name: str, number: str) -> None:
        self._primary_path = primary.path
        self._surface_live = True
        self._current_caller = name
        self._current_number = number
        self._act_caller.set_text(name or number or 'Unknown call')
        self._act_number.set_text(number if name and name != number else '')
        self._paint_avatar('_act_avatar', name or number)
        self._sync_hold_tile()
        self._adopt_timer_base(primary.path, primary.started_at)
        self._update_waiting(waiting)
        self._stack.set_visible_child_name('active')
        self._maybe_present(primary.path, 'active')
        self._refresh_timer_label()

    def _update_waiting(self, waiting) -> None:
        self._waiting_path = waiting.path if waiting is not None else None
        box = getattr(self, '_waiting_box', None)
        if box is None:
            return
        if waiting is None:
            box.set_visible(False)
            return
        name = display_name(waiting.number) or waiting.number or 'Unknown call'
        self._waiting_label.set_text(f'Second call: {name}')
        box.set_visible(True)

    def _update_audio(self, live: list) -> None:
        from modem_monitor import _STATE_ACTIVE
        if any(c.state == _STATE_ACTIVE for c in live):
            try:
                self._audio.enter_call()
            except Exception:
                pass

    def _update_proximity(self, live: list) -> None:
        from modem_monitor import _STATE_ACTIVE
        active = any(c.state == _STATE_ACTIVE for c in live)
        if active and not self._speakerphone and self._display_mgr is not None:
            try:
                self._proximity.start(self._display_mgr, self)
            except Exception:
                pass
        else:
            try:
                self._proximity.stop()
            except Exception:
                pass

    def _maybe_present(self, path: str | None, page: str) -> None:
        """Map the surface, but never steal it back: outgoing dialing stays
        on the CallBar, and a minimized/failed surface stays down until the
        user expands it or a new call arrives."""
        if page == 'outgoing' or self._minimized:
            return
        if ((self._presented_page, self._presented_path) == (page, path)
                and self._surface_live):
            return
        self._presented_page, self._presented_path = page, path
        self.present()

    def minimize(self) -> None:
        """Collapse to the CallBar (tap the bar to expand again)."""
        self._minimized = True
        if self._bar is not None:
            try:
                self._bar.show_bar(
                    self._current_caller or self._current_number or 'In call')
            except Exception:
                pass
        self.hide()

    def expand(self) -> None:
        self._minimized = False
        self.present()

    def end_call(self) -> None:
        self._close_all()

    def _close_all(self) -> None:
        """Last call gone: stop everything (ringer, audio, proximity,
        timer, bar) and drop every in-flight guard."""
        self._stop_tick()
        self._cancel_action_timeout()
        self._hide_close()
        try:
            self._ringer.stop()
        except Exception:
            pass
        try:
            self._audio.exit_call()
        except Exception:
            pass
        try:
            self._proximity.stop()
        except Exception:
            pass
        self._live_snapshot = []
        self._incoming_call_path = None
        self._incoming_path = None
        self._primary_path = None
        self._waiting_path = None
        # Any in-flight accept/hangup reply is now stale; drop the guard so
        # the next call starts with responsive buttons.
        self._pending_action = None
        self._action_path = None
        self._surface_live = False
        self._minimized = False
        self._presented_page = None
        self._presented_path = None
        self._timed_path = None
        self._call_start_mono = None
        if self._bar is not None:
            try:
                self._bar.hide_bar()
            except Exception:
                pass
        try:
            self.hide()
        except Exception:
            pass

    def _recover_close(self) -> None:
        """A failed Accept/Hangup closes the surface but keeps the call on
        the CallBar, so the user can expand and retry."""
        self._hide_close()
        try:
            self.hide()
        except Exception:
            pass
        if self._bar is not None and self._live_snapshot:
            primary = primary_call(self._live_snapshot)
            if primary is not None:
                title = (display_name(primary.number) or primary.number
                         or 'In call')
                try:
                    self._bar.show_bar(title)
                except Exception:
                    pass

    def _ensure_tick(self) -> None:
        if self._timer_id is None and self._live_snapshot:
            self._timer_id = GLib.timeout_add_seconds(1, self._tick_timer)

    def _stop_tick(self) -> None:
        if self._timer_id is not None:
            try:
                GLib.source_remove(self._timer_id)
            except Exception:
                pass
            self._timer_id = None

    def _timer_text(self) -> str:
        primary = primary_call(self._live_snapshot)
        if (primary is not None and self._call_start_mono is not None
                and self._timed_path == primary.path):
            return format_elapsed(self._call_start_mono, time.monotonic())
        if primary is not None and primary.started_at is not None:
            return format_elapsed(primary.started_at, time.monotonic())
        return '0:00'

    def _refresh_timer_label(self) -> None:
        try:
            self._act_timer.set_text(self._timer_text())
        except Exception:
            pass

    def _tick_timer(self) -> bool:
        if not self._live_snapshot:
            self._timer_id = None
            return False
        self._refresh_timer_label()
        if self._bar is not None:
            primary = primary_call(self._live_snapshot)
            if primary is not None:
                try:
                    self._bar.update(
                        display_name(primary.number) or primary.number
                        or 'In call',
                        self._timer_text(),
                    )
                except Exception:
                    pass
        return True

    def _show_close(self) -> None:
        for btn in self._close_btns:
            try:
                btn.set_visible(True)
            except Exception:
                pass

    def _hide_close(self) -> None:
        for btn in self._close_btns:
            try:
                btn.set_visible(False)
            except Exception:
                pass

    def _cancel_action_timeout(self) -> None:
        if self._close_src is not None:
            try:
                GLib.source_remove(self._close_src)
            except Exception:
                pass
            self._close_src = None
        self._hide_close()

    def _on_action_timeout(self) -> bool:
        """The modem stopped answering: offer a visible Close fallback."""
        self._close_src = None
        if self._pending_action is not None:
            self._show_close()
        return False

    def _on_close_clicked(self, _btn: Gtk.Button) -> None:
        # Force-close a wedged action; the live call (if any) stays on the
        # CallBar and any late reply is dropped by the cleared guard.
        self._pending_action = None
        self._action_path = None
        self._cancel_action_timeout()
        self._recover_close()

    def _on_minimize_clicked(self, _btn: Gtk.Button) -> None:
        self.minimize()

    def _begin_call_action(
        self,
        action: str,
        path: str | None,
        fn: CallActionFn,
        finish: Callable[[str, bool, str | None], None],
    ) -> None:
        """
        Dispatch an async call action with double-fire + staleness guards.

        - One action in flight at a time: clicks while ``_pending_action`` is
          set are ignored, so a wedged bus can't stack transitions.
        - A 10 s watchdog surfaces a Close button when the modem stops
          answering; the completion callback only acts while the surface is
          still live for the dispatched call.
        """
        if self._pending_action is not None:
            return
        if path is None:
            _log_action_failure(action, None, 'no active call')
            return
        self._pending_action = action
        self._action_path = path
        self._cancel_action_timeout()
        self._close_src = GLib.timeout_add_seconds(10, self._on_action_timeout)

        def _on_done(success: bool, error: str | None) -> None:
            self._pending_action = None
            self._action_path = None
            self._cancel_action_timeout()
            if not self._surface_live:
                return  # call ended/closed while the request was in flight
            finish(path, success, error)

        fn(path, _on_done)

    def _on_accept_clicked(self, _btn: Gtk.Button) -> None:
        self._begin_call_action(
            'accept', self._incoming_path, self._accept_fn, self._finish_accept)

    def _finish_accept(self, call_path: str, success: bool, error: str | None) -> None:
        if not success:
            _log_action_failure('accept', call_path, error)
            self._recover_close()
            return
        self._ringer.stop()
        self._on_accept()
        self.show_active(self._current_caller, self._current_number)

    def _on_decline_clicked(self, _btn: Gtk.Button) -> None:
        self._begin_call_action(
            'decline', self._incoming_path, self._hangup_fn, self._finish_decline)

    def _finish_decline(self, call_path: str, success: bool, error: str | None) -> None:
        if not success:
            _log_action_failure('decline', call_path, error)
            self._recover_close()
            return
        self._ringer.stop()
        self._on_decline()
        self._incoming_call_path = None
        self._incoming_path = None
        self.hide()

    def _on_hangup_clicked(self, _btn: Gtk.Button) -> None:
        self._begin_call_action(
            'hang up', self._primary_path, self._hangup_fn, self._finish_hangup)

    def _finish_hangup(self, call_path: str, success: bool, error: str | None) -> None:
        if not success:
            _log_action_failure('hang up', call_path, error)
            self._recover_close()
            return
        self._ringer.stop()
        self._on_hangup()
        self.end_call()

    def _on_hold_answer_clicked(self, _btn: Gtk.Button) -> None:
        modem = _resolve_modem_path()
        if modem is None:
            _log_action_failure('hold & answer', self._waiting_path, 'no modem found')
            return
        self._begin_call_action(
            'hold & answer', modem, self._hold_accept_fn, self._finish_hold_accept)

    def _finish_hold_accept(self, modem_path: str, success: bool, error: str | None) -> None:
        if not success:
            _log_action_failure('hold & answer', modem_path, error)
            self._recover_close()
            return
        # The snapshot (active→held, waiting→active) re-renders the surface.

    def _on_waiting_decline_clicked(self, _btn: Gtk.Button) -> None:
        self._begin_call_action(
            'decline waiting', self._waiting_path, self._hangup_fn,
            self._finish_waiting_decline)

    def _finish_waiting_decline(
        self, call_path: str, success: bool, error: str | None,
    ) -> None:
        if not success:
            _log_action_failure('decline waiting', call_path, error)
            return
        # Disarm re-decline; the banner itself hides on the next snapshot,
        # which is the single source of truth for waiting calls.
        self._waiting_path = None

    def _on_dtmf_clicked(self, _btn: Gtk.Button, digit: str) -> None:
        path = self._primary_path
        if path is None:
            _log_action_failure('DTMF', None, 'no active call')
            return

        def _on_sent(success: bool, error: str | None) -> None:
            if not success:
                _log_action_failure('DTMF', path, error)

        self._dtmf_fn(path, digit, _on_sent)

    def _on_keypad_toggled(self, btn: Gtk.ToggleButton) -> None:
        grid = getattr(self, '_keypad_grid', None)
        if grid is not None:
            try:
                grid.set_visible(btn.get_active())
            except Exception:
                pass

    def _on_hold_toggled(self, btn: Gtk.ToggleButton) -> None:
        # A waiting second call holds the active one for real (same action
        # as the waiting banner); a lone call cannot hold over D-Bus, so
        # the tile snaps back to the snapshot's HELD truth instead of
        # faking a hold.
        if self._waiting_path is not None and btn.get_active():
            self._on_hold_answer_clicked(btn)
            return
        try:
            btn.set_active(self._primary_held())
        except Exception:
            pass

    def _on_add_call_toggled(self, btn: Gtk.ToggleButton) -> None:
        # Collapse to the CallBar so a second call can be placed from the
        # dialer; the tile is momentary and snaps back at once.
        if btn.get_active():
            try:
                btn.set_active(False)
            except Exception:
                pass
        self.minimize()

    def _on_bt_toggled(self, btn: Gtk.ToggleButton) -> None:
        # No Bluetooth audio route exists on this shell: in-call audio
        # rides CallAudioRouter behind the SPEAKER tile, so the toggle
        # snaps back instead of faking a route.
        if btn.get_active():
            try:
                btn.set_active(False)
            except Exception:
                pass

    def _on_mute_toggled(self, btn: Gtk.ToggleButton) -> None:
        self._muted = btn.get_active()
        try:
            self._audio.set_mic_mute(self._muted)
        except Exception:
            pass

    def _on_speaker_toggled(self, btn: Gtk.ToggleButton) -> None:
        self._speakerphone = btn.get_active()
        try:
            self._audio.set_speaker(self._speakerphone)
        except Exception:
            pass
        self._update_proximity(self._live_snapshot)

    def _set_microphone_mute(self, muted: bool) -> None:
        self._audio.set_mic_mute(muted)

    def _set_speakerphone(self, enabled: bool) -> None:
        self._audio.set_speaker(enabled)
