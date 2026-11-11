"""Automatic (opportunistic) suspend, phone style.

After the display has been blanked for ``suspend_after`` seconds the shell
asks logind to suspend (``Manager.Suspend(false)`` — no root needed for
the active session; elogind implements it too). Right before every
attempt it re-checks the blockers below and, while any holds, backs off
and re-checks (10 s doubling, capped at 45 s). It never suspends while
the display is lit: a wake cancels everything.

Blockers (``blockers()``, pure):

- the display is lit;
- a live call (call_wiring's ``_call_live``; logind also refuses while
  the call's 'block' sleep inhibitor is held);
- any idle inhibitor: the ScreenSaver service (apps / phoc video), the
  Caffeine tile, or the shell's own 'call' reason;
- an MPRIS player in Playing state (music with the screen off);
- charging, unless ``suspend_when_charging`` is true;
- the flag file ``$XDG_RUNTIME_DIR/xx-wm/no-suspend`` (scripts: touch it
  to hold the phone awake, remove it to release);
- a connected wireless-debugging (xxdb) client;
- xx-clock: an alarm or timer is ringing, the next alarm/timer is within
  ``ALARM_GUARD_S``, or one is armed at all while no RTC wake can be
  set (see below), or the clock daemon did not answer.

Alarms. xx-clock's daemon exposes its next fire time (``GetState`` ->
``next.fire_at_ms`` and running timers' ``ends_at_ms``). A suspended
phone only fires it if something arms an RTC wake, and the session has
no unprivileged way to (systemd *user* timers ignore ``WakeSystem=``
without CAP_WAKE_ALARM, logind has no wakealarm API, and
``/sys/class/rtc/rtc0/wakealarm`` is root-only). Until the root helper
grows a wake-alarm method (``rtc_wake`` hook below), an armed alarm
blocks automatic suspend entirely: alarms win over battery. xx-calendar
has no reminder scheduler or next-reminder API yet, so calendar
reminders are not considered (they do not fire with the screen off
today either).

Logind inhibitors are respected implicitly: a refused or failed Suspend
is logged and retried after the capped back-off. After resume the
display is still blank, so the next attempt waits ``RESUME_GRACE_S``
(time for the modem / notifications that woke the phone to be handled);
a modem-alive probe is logged a few seconds after resume (recovery is a
follow-up).
"""
from __future__ import annotations

import json
import os
import time
from typing import Callable, NamedTuple

from shell_log import get_logger

_log = get_logger('auto_suspend')

# Default off: wake sources (modem ring/SMS wake on Halium and mainline
# kernels, RTC alarms) are unverified on the target devices, and an armed
# alarm cannot wake a suspended phone yet. Opt in from Settings → Battery.
DEFAULT_SUSPEND_AFTER = 0
SUSPEND_AFTER_CHOICES = ((0, 'Off'), (60, '1 min'), (300, '5 min'),
                         (900, '15 min'))

BACKOFF_START_S = 10
BACKOFF_CAP_S = 45
RESUME_GRACE_S = 30
MODEM_CHECK_DELAY_S = 5
# Never suspend this close to a known alarm/timer, even with RTC wake.
ALARM_GUARD_S = 120

FLAG_NAME = 'no-suspend'

CLOCK_BUS = 'io.piercingxx.XXClock.Daemon'
CLOCK_PATH = '/io/piercingxx/XXClock'
CLOCK_IFACE = 'io.piercingxx.XXClock.Daemon'

_LOGIN1 = 'org.freedesktop.login1'
_LOGIN1_PATH = '/org/freedesktop/login1'
_LOGIN1_MANAGER = 'org.freedesktop.login1.Manager'

_NO_SERVICE_ERRORS = ('ServiceUnknown', 'NameHasNoOwner', 'ServiceNotFound')


# ---------------------------------------------------------------------------
# Pure decisions
# ---------------------------------------------------------------------------

def suspend_after_secs(config: object) -> int:
    """`suspend_after` seconds from config (0 = off). Negative or
    malformed values read as off."""
    getter = getattr(config, 'get', None)
    if not callable(getter):
        return DEFAULT_SUSPEND_AFTER
    try:
        value = int(getter('suspend_after', DEFAULT_SUSPEND_AFTER))
    except (TypeError, ValueError):
        return DEFAULT_SUSPEND_AFTER
    return max(0, value)


def suspend_when_charging(config: object) -> bool:
    getter = getattr(config, 'get', None)
    if not callable(getter):
        return False
    try:
        return bool(getter('suspend_when_charging', False))
    except Exception:
        return False


def suspend_after_label(seconds: int) -> str:
    for value, label in SUSPEND_AFTER_CHOICES:
        if value == seconds:
            return label
    if seconds <= 0:
        return 'Off'
    if seconds % 60 == 0:
        return '%d min' % (seconds // 60)
    return '%d s' % seconds


def next_backoff(previous: int | None) -> int:
    """10 → 20 → 40 → 45 → 45 … seconds between blocked re-checks."""
    if not previous or previous <= 0:
        return BACKOFF_START_S
    return min(BACKOFF_CAP_S, previous * 2)


class AlarmInfo(NamedTuple):
    """What xx-clock said. ``known`` False = the daemon did not answer
    (unknown state blocks); ``next_ms`` None = nothing armed."""
    known: bool = True
    next_ms: int | None = None
    ringing: bool = False


NO_ALARMS = AlarmInfo(known=True, next_ms=None, ringing=False)
ALARMS_UNKNOWN = AlarmInfo(known=False)


def parse_clock_state(state_json: str) -> AlarmInfo:
    """xx-clock GetState JSON -> AlarmInfo: earliest of the next armed
    alarm (``next.fire_at_ms``) and every RUNNING timer's ``ends_at_ms``."""
    try:
        state = json.loads(state_json)
    except (TypeError, ValueError):
        return ALARMS_UNKNOWN
    if not isinstance(state, dict):
        return ALARMS_UNKNOWN
    candidates: list[int] = []
    upcoming = state.get('next')
    if isinstance(upcoming, dict):
        try:
            candidates.append(int(upcoming.get('fire_at_ms')))
        except (TypeError, ValueError):
            pass
    for timer in state.get('timers') or ():
        if not isinstance(timer, dict):
            continue
        if str(timer.get('state', '')).upper() != 'RUNNING':
            continue
        try:
            candidates.append(int(timer.get('ends_at_ms')))
        except (TypeError, ValueError):
            pass
    return AlarmInfo(known=True,
                     next_ms=min(candidates) if candidates else None,
                     ringing=bool(state.get('ringing')))


def alarm_blocker(alarm: AlarmInfo, now_ms: int,
                  rtc_wake_available: bool) -> str | None:
    """Why the alarm state forbids suspending now, or None."""
    if not alarm.known:
        return 'alarm state unknown'
    if alarm.ringing:
        return 'alarm ringing'
    if alarm.next_ms is None:
        return None
    if alarm.next_ms - now_ms <= ALARM_GUARD_S * 1000:
        return 'alarm soon'
    if not rtc_wake_available:
        return 'alarm armed (no RTC wake)'
    return None


class SuspendContext(NamedTuple):
    blanked: bool
    call_live: bool = False
    inhibitors: frozenset = frozenset()
    media_playing: bool = False
    charging: bool = False
    allow_on_charger: bool = False
    flag_file: bool = False
    debug_clients: int = 0
    alarm: AlarmInfo = NO_ALARMS
    now_ms: int = 0
    rtc_wake_available: bool = False


def blockers(ctx: SuspendContext) -> list[str]:
    """Every reason not to suspend right now ([] = go)."""
    out: list[str] = []
    if not ctx.blanked:
        out.append('display on')
    if ctx.call_live:
        out.append('call')
    if ctx.inhibitors:
        out.append('inhibited: ' + ', '.join(sorted(ctx.inhibitors)))
    if ctx.media_playing:
        out.append('media playing')
    if ctx.charging and not ctx.allow_on_charger:
        out.append('charging')
    if ctx.flag_file:
        out.append('no-suspend flag')
    if ctx.debug_clients > 0:
        out.append('wireless debugging session')
    reason = alarm_blocker(ctx.alarm, ctx.now_ms, ctx.rtc_wake_available)
    if reason:
        out.append(reason)
    return out


def flag_path(environ=os.environ) -> str | None:
    runtime = environ.get('XDG_RUNTIME_DIR')
    if not runtime:
        return None
    return os.path.join(runtime, 'xx-wm', FLAG_NAME)


def flag_present(environ=os.environ) -> bool:
    path = flag_path(environ)
    return bool(path) and os.path.exists(path)


def _error_is_no_service(error: object) -> bool:
    text = getattr(error, 'message', None) or str(error)
    return any(name in text for name in _NO_SERVICE_ERRORS)


# ---------------------------------------------------------------------------
# Async D-Bus edges (all fail soft)
# ---------------------------------------------------------------------------

def query_clock_async(on_done: Callable[[AlarmInfo], None]) -> None:
    """xx-clock GetState on the session bus -> on_done(AlarmInfo).
    No clock installed = no alarms; any other failure = unknown."""
    try:
        from gi.repository import Gio
    except Exception:
        on_done(NO_ALARMS)
        return

    def _reply(conn, result) -> None:
        try:
            (state,) = conn.call_finish(result).unpack()
        except Exception as exc:
            if _error_is_no_service(exc):
                on_done(NO_ALARMS)
            else:
                _log.warning('xx-clock state unavailable: %s', exc)
                on_done(ALARMS_UNKNOWN)
            return
        on_done(parse_clock_state(state))

    def _bus(_src, result) -> None:
        try:
            bus = Gio.bus_get_finish(result)
            # Auto-start allowed (docs/dbus.md); the daemon runs at login.
            bus.call(CLOCK_BUS, CLOCK_PATH, CLOCK_IFACE, 'GetState', None,
                     None, Gio.DBusCallFlags.NONE, 3000, None, _reply)
        except Exception as exc:
            _log.warning('xx-clock query failed: %s', exc)
            on_done(ALARMS_UNKNOWN)

    try:
        Gio.bus_get(Gio.BusType.SESSION, None, _bus)
    except Exception as exc:
        _log.warning('xx-clock query failed: %s', exc)
        on_done(ALARMS_UNKNOWN)


def logind_suspend_async(on_done: Callable[[bool, str], None]) -> None:
    """logind Manager.Suspend(false) -> on_done(ok, message). Non-
    interactive: an inhibitor or a polkit 'challenge' fails the call
    instead of prompting."""
    try:
        from gi.repository import Gio, GLib
    except Exception as exc:
        on_done(False, str(exc))
        return

    def _reply(conn, result) -> None:
        try:
            conn.call_finish(result)
        except Exception as exc:
            on_done(False, getattr(exc, 'message', None) or str(exc))
            return
        on_done(True, '')

    def _bus(_src, result) -> None:
        try:
            bus = Gio.bus_get_finish(result)
            bus.call(_LOGIN1, _LOGIN1_PATH, _LOGIN1_MANAGER, 'Suspend',
                     GLib.Variant('(b)', (False,)), None,
                     Gio.DBusCallFlags.NONE, 25000, None, _reply)
        except Exception as exc:
            on_done(False, str(exc))

    try:
        Gio.bus_get(Gio.BusType.SYSTEM, None, _bus)
    except Exception as exc:
        on_done(False, str(exc))


def modem_alive_check_async() -> None:
    """Post-resume hook point: log what ModemManager reports. Recovery
    (re-enable / restart a modem that did not come back) is a follow-up."""
    try:
        from gi.repository import Gio
    except Exception:
        return

    def _reply(conn, result) -> None:
        try:
            (objects,) = conn.call_finish(result).unpack()
        except Exception as exc:
            _log.info('post-resume modem check: ModemManager absent (%s)',
                      getattr(exc, 'message', None) or exc)
            return
        states = []
        for _path, ifaces in (objects or {}).items():
            modem = (ifaces or {}).get('org.freedesktop.ModemManager1.Modem')
            if isinstance(modem, dict):
                states.append(modem.get('State'))
        if not states:
            _log.info('post-resume modem check: no modem')
            return
        _log.info('post-resume modem check: %d modem(s), states %s',
                  len(states), states)

    def _bus(_src, result) -> None:
        try:
            bus = Gio.bus_get_finish(result)
            bus.call('org.freedesktop.ModemManager1',
                     '/org/freedesktop/ModemManager1',
                     'org.freedesktop.DBus.ObjectManager',
                     'GetManagedObjects', None, None,
                     Gio.DBusCallFlags.NO_AUTO_START, 5000, None, _reply)
        except Exception as exc:
            _log.info('post-resume modem check skipped: %s', exc)

    try:
        Gio.bus_get(Gio.BusType.SYSTEM, None, _bus)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------

def _false() -> bool:
    return False


class AutoSuspendController:
    """Blank-driven suspend scheduler. Every probe is an injected callable
    (main.py wires the live shell; tests pass fakes); every effect is
    asynchronous. ``rtc_wake(epoch_s) -> bool`` arms an RTC wake when a
    privileged backend exists (None today)."""

    def __init__(self, config: object,
                 is_blanked: Callable[[], bool],
                 call_live: Callable[[], bool] = _false,
                 inhibit_reasons: Callable[[], set] = lambda: set(),
                 media_playing: Callable[[], bool] = _false,
                 charging: Callable[[], bool] = _false,
                 debug_clients: Callable[[], int] = lambda: 0,
                 flag_present: Callable[[], bool] = flag_present,
                 query_clock: Callable = query_clock_async,
                 suspend: Callable = logind_suspend_async,
                 modem_check: Callable[[], None] = modem_alive_check_async,
                 rtc_wake: Callable[[float], bool] | None = None,
                 timeout_add: Callable | None = None,
                 source_remove: Callable | None = None,
                 wall_ms: Callable[[], int] | None = None) -> None:
        self._config = config
        self._is_blanked = is_blanked
        self._call_live = call_live
        self._inhibit_reasons = inhibit_reasons
        self._media_playing = media_playing
        self._charging = charging
        self._debug_clients = debug_clients
        self._flag_present = flag_present
        self._query_clock = query_clock
        self._suspend = suspend
        self._modem_check = modem_check
        self._rtc_wake = rtc_wake
        self._timeout_add = timeout_add
        self._source_remove = source_remove
        self._wall_ms = wall_ms or (lambda: int(time.time() * 1000))
        self._src = None
        self._gen = 0
        self._backoff: int | None = None
        self._last_blockers: list[str] | None = None
        self.in_flight = False

    # -- timers -------------------------------------------------------------

    def _add(self, seconds: int, fn: Callable[[], bool]):
        add = self._timeout_add
        if add is None:
            from gi.repository import GLib
            add = GLib.timeout_add_seconds
        return add(max(1, int(seconds)), fn)

    def _cancel(self) -> None:
        src, self._src = self._src, None
        if src is None:
            return
        remove = self._source_remove
        try:
            if remove is None:
                from gi.repository import GLib
                remove = GLib.source_remove
            remove(src)
        except Exception:
            pass

    def _arm(self, seconds: int) -> None:
        self._cancel()
        gen = self._gen
        try:
            self._src = self._add(seconds, lambda: self._on_timer(gen))
        except Exception as exc:
            _log.warning('auto-suspend timer unavailable: %s', exc)

    @property
    def armed(self) -> bool:
        return self._src is not None

    # -- inputs ---------------------------------------------------------------

    def note_blank(self, blanked: bool) -> None:
        """DisplayManager blank fan-out: arm on blank, cancel on wake."""
        self._gen += 1
        self._cancel()
        self._backoff = None
        self._last_blockers = None
        self.in_flight = False
        if not blanked:
            return
        after = suspend_after_secs(self._config)
        if after > 0:
            self._arm(after)

    def note_resumed(self) -> None:
        """logind PrepareForSleep(false)."""
        self.in_flight = False
        try:
            self._add(MODEM_CHECK_DELAY_S, self._run_modem_check)
        except Exception:
            pass
        self._gen += 1
        self._backoff = None
        if self._safe(self._is_blanked) and suspend_after_secs(self._config):
            _log.info('resumed while blank: next suspend check in %d s',
                      RESUME_GRACE_S)
            self._arm(RESUME_GRACE_S)

    def _run_modem_check(self) -> bool:
        try:
            self._modem_check()
        except Exception as exc:
            _log.warning('post-resume modem check failed: %s', exc)
        return False

    # -- the check ----------------------------------------------------------------

    def _on_timer(self, gen: int) -> bool:
        self._src = None
        if gen != self._gen:
            return False
        self.check_now()
        return False

    def check_now(self) -> None:
        """Gather blockers (xx-clock asynchronously) and suspend or back
        off."""
        if not self._safe(self._is_blanked) \
                or not suspend_after_secs(self._config):
            return
        gen = self._gen
        try:
            self._query_clock(lambda alarm: self._decide(gen, alarm))
        except Exception as exc:
            _log.warning('xx-clock query raised: %s', exc)
            self._decide(gen, ALARMS_UNKNOWN)

    def context(self, alarm: AlarmInfo) -> SuspendContext:
        try:
            reasons = frozenset(self._inhibit_reasons() or ())
        except Exception:
            reasons = frozenset()
        try:
            clients = int(self._debug_clients() or 0)
        except Exception:
            clients = 0
        return SuspendContext(
            blanked=self._safe(self._is_blanked),
            call_live=self._safe(self._call_live),
            inhibitors=reasons,
            media_playing=self._safe(self._media_playing),
            charging=self._safe(self._charging),
            allow_on_charger=suspend_when_charging(self._config),
            flag_file=self._safe(self._flag_present),
            debug_clients=clients,
            alarm=alarm,
            now_ms=self._wall_ms(),
            rtc_wake_available=self._rtc_wake is not None,
        )

    def _decide(self, gen: int, alarm: AlarmInfo) -> None:
        if gen != self._gen or self.in_flight:
            return
        ctx = self.context(alarm)
        if not ctx.blanked or not suspend_after_secs(self._config):
            return
        reasons = blockers(ctx)
        if reasons:
            self._back_off(reasons)
            return
        if alarm.next_ms is not None and self._rtc_wake is not None:
            wake_at = (alarm.next_ms - ALARM_GUARD_S * 1000) / 1000.0
            try:
                armed = bool(self._rtc_wake(wake_at))
            except Exception as exc:
                _log.warning('RTC wake failed: %s', exc)
                armed = False
            if not armed:
                self._back_off(['RTC wake not armed'])
                return
        _log.info('auto-suspend: display blank, nothing blocking — suspending')
        self.in_flight = True
        try:
            self._suspend(lambda ok, msg: self._on_suspend_done(gen, ok, msg))
        except Exception as exc:
            self._on_suspend_done(gen, False, str(exc))

    def _back_off(self, reasons: list[str]) -> None:
        self._backoff = next_backoff(self._backoff)
        if reasons != self._last_blockers:
            _log.info('auto-suspend blocked (%s); re-check in %d s',
                      '; '.join(reasons), self._backoff)
            self._last_blockers = list(reasons)
        self._arm(self._backoff)

    def _on_suspend_done(self, gen: int, ok: bool, message: str) -> None:
        if ok:
            # Suspend returns once logind has queued the sleep; the resume
            # arrives as PrepareForSleep(false) -> note_resumed().
            return
        self.in_flight = False
        _log.warning('auto-suspend refused: %s; retry in %d s',
                     message or 'unknown error', BACKOFF_CAP_S)
        if gen == self._gen and self._safe(self._is_blanked):
            self._backoff = BACKOFF_CAP_S
            self._arm(BACKOFF_CAP_S)

    @staticmethod
    def _safe(probe: Callable[[], object]) -> bool:
        try:
            return bool(probe())
        except Exception:
            return False
