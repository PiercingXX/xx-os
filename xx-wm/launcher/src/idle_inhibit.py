"""Process-wide idle-inhibit hub: who is keeping the screen on.

Two producers feed the DisplayManager's named idle inhibitors through
here: the org.freedesktop.ScreenSaver service (reason ``screensaver``,
held while any app — or phoc forwarding a Wayland zwp_idle_inhibit —
holds a cookie) and the Caffeine quick tile (reason ``caffeine``). The
hub outlives the order things are built in: reasons set before the
DisplayManager exists are replayed when it attaches.

Automatic suspend (auto_suspend.py) reads ``active_reasons()`` right
before suspending: any held reason blocks it.

Caffeine turns itself off when the screen goes off (power key, lock) and
after ``caffeine_minutes`` (default 60; 0 = until turned off). The
deadline is a plain monotonic timestamp checked on every read plus one
GLib timer, so a missed timer can never keep the screen on forever.
"""
from __future__ import annotations

import time
from typing import Callable

from shell_log import get_logger

_log = get_logger('idle_inhibit')

REASON_SCREENSAVER = 'screensaver'
REASON_CAFFEINE = 'caffeine'

CAFFEINE_DEFAULT_MINUTES = 60


def caffeine_minutes(config: object) -> int:
    """`caffeine_minutes` from config (any object with .get), clamped
    0..1440; malformed or missing values read as the 60 min default."""
    getter = getattr(config, 'get', None)
    if not callable(getter):
        return CAFFEINE_DEFAULT_MINUTES
    try:
        value = int(getter('caffeine_minutes', CAFFEINE_DEFAULT_MINUTES))
    except (TypeError, ValueError):
        return CAFFEINE_DEFAULT_MINUTES
    return max(0, min(1440, value))


class IdleInhibitHub:
    """Named idle reasons mirrored onto a DisplayManager.

    ``timeout_add(seconds, fn)`` / ``source_remove(id)`` and ``clock`` are
    injectable so tests run without a main loop.
    """

    def __init__(self, timeout_add: Callable | None = None,
                 source_remove: Callable | None = None,
                 clock: Callable[[], float] | None = None) -> None:
        self._reasons: set[str] = set()
        self._display = None
        self._timeout_add = timeout_add
        self._source_remove = source_remove
        self._clock = clock or time.monotonic
        self._caffeine_deadline: float | None = None   # None = no deadline
        self._caffeine_on = False
        self._caffeine_src = None
        self._listeners: list[Callable[[], None]] = []

    # -- wiring -----------------------------------------------------------

    def attach(self, display_mgr: object) -> None:
        """Bind the DisplayManager and replay every held reason."""
        self._display = display_mgr
        for reason in sorted(self._reasons):
            self._push(reason, True)

    def add_listener(self, callback: Callable[[], None]) -> None:
        """Called (no args) whenever the held reasons change."""
        if callback not in self._listeners:
            self._listeners.append(callback)

    def _push(self, reason: str, active: bool) -> None:
        display = self._display
        if display is None:
            return
        try:
            display.inhibit_idle(reason, active)
        except Exception as exc:
            _log.warning('idle inhibit %s=%s failed: %s', reason, active, exc)

    def _notify(self) -> None:
        for callback in list(self._listeners):
            try:
                callback()
            except Exception as exc:
                _log.warning('idle inhibit listener failed: %s', exc)

    # -- reasons ------------------------------------------------------------

    def set(self, reason: str, active: bool) -> None:
        active = bool(active)
        if active == (reason in self._reasons):
            return
        if active:
            self._reasons.add(reason)
        else:
            self._reasons.discard(reason)
        _log.info('idle inhibit %s %s', reason, 'held' if active else 'released')
        self._push(reason, active)
        self._notify()

    def active_reasons(self) -> set[str]:
        self._expire_caffeine()
        return set(self._reasons)

    def wake(self) -> None:
        """SimulateUserActivity: wake a blanked display, else push the
        idle timer out."""
        display = self._display
        if display is None:
            return
        try:
            display.wake()
        except Exception as exc:
            _log.warning('simulated user activity failed: %s', exc)

    # -- caffeine -----------------------------------------------------------

    def caffeine_active(self) -> bool:
        self._expire_caffeine()
        return self._caffeine_on

    def set_caffeine(self, enabled: bool, minutes: int = 0) -> None:
        """Keep the screen on (minutes > 0 arms an auto-off)."""
        self._cancel_caffeine_timer()
        self._caffeine_on = bool(enabled)
        self._caffeine_deadline = None
        if enabled and minutes > 0:
            self._caffeine_deadline = self._clock() + minutes * 60
            self._arm_caffeine_timer(minutes * 60)
        self.set(REASON_CAFFEINE, self._caffeine_on)

    def clear_caffeine(self, why: str = '') -> None:
        """Turn Caffeine off (screen off, lock, timeout). No-op when off."""
        if not self._caffeine_on:
            return
        _log.info('caffeine off (%s)', why or 'cleared')
        self.set_caffeine(False)

    def _expire_caffeine(self) -> None:
        deadline = self._caffeine_deadline
        if self._caffeine_on and deadline is not None \
                and self._clock() >= deadline:
            self.clear_caffeine('timeout')

    def _arm_caffeine_timer(self, seconds: int) -> None:
        add = self._timeout_add
        if add is None:
            try:
                from gi.repository import GLib
                add = GLib.timeout_add_seconds
            except Exception:
                return
        try:
            self._caffeine_src = add(int(seconds), self._on_caffeine_timer)
        except Exception as exc:
            _log.warning('caffeine timer unavailable: %s', exc)

    def _cancel_caffeine_timer(self) -> None:
        src, self._caffeine_src = self._caffeine_src, None
        if src is None:
            return
        remove = self._source_remove
        if remove is None:
            try:
                from gi.repository import GLib
                remove = GLib.source_remove
            except Exception:
                return
        try:
            remove(src)
        except Exception:
            pass

    def _on_caffeine_timer(self) -> bool:
        self._caffeine_src = None
        self.clear_caffeine('timeout')
        return False


_hub: IdleInhibitHub | None = None


def get_hub() -> IdleInhibitHub:
    """The process-wide hub (the quick tile, the ScreenSaver service, the
    power menu and auto-suspend all reach the same instance)."""
    global _hub
    if _hub is None:
        _hub = IdleInhibitHub()
    return _hub
