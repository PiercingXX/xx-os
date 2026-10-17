"""Wait-until-condition helpers for Broadway/real-GTK tests.

Fixed sleeps flake under load: the Broadway round trip can outlast
them, so a page, row, or socket read lands mid-transition. Poll a
condition in small slices with a generous timeout instead, and fail
with a message naming the condition that never held.

Driver side (GTK process, Broadway display): :func:`pump` runs one
main-loop slice; :func:`pump_until` repeats slices until
``predicate`` holds, then settles briefly so a just-mapped widget
finishes its transition. In-process tests that already drive
``GLib.MainContext`` directly use :func:`iterate_until` instead.

Parent side (pytest, no GTK needed): :func:`poll_until` and
:func:`wait_until` poll a plain predicate with ``time.sleep`` for
daemon sockets, ports, and subprocess markers.

Pure module: ``gi`` is imported lazily inside the pump functions, so
importing this module never pins a GDK backend.
"""

from __future__ import annotations

import time

#: Slice for driver-side polling: short enough to react fast, long
#: enough to let a Broadway frame round-trip.
POLL_SLICE_S = 0.05

#: Settle after the condition holds: a just-mapped page still runs
#: its transition before reads are stable.
SETTLE_S = 0.2


def pump(seconds: float) -> None:
    """Run the default GLib main loop for ``seconds``."""
    from gi.repository import GLib

    loop = GLib.MainLoop()
    GLib.timeout_add(int(seconds * 1000), loop.quit)
    loop.run()


def pump_until(predicate, timeout_s: float = 8.0,
               settle_s: float = SETTLE_S) -> bool:
    """Pump until ``predicate`` holds; True when it did in time.

    Exceptions from ``predicate`` count as "not yet" while polling;
    the final check reports the real value. The caller asserts on
    the result with a message naming the condition.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        pump(POLL_SLICE_S)
        try:
            if predicate():
                break
        except Exception:
            pass
    else:
        try:
            return bool(predicate())
        except Exception:
            return False
    if settle_s > 0:
        pump(settle_s)
    try:
        return bool(predicate())
    except Exception:
        return False


def iterate_until(predicate, timeout_s: float = 8.0,
                  interval_s: float = 0.02) -> bool:
    """Drain the default main context until ``predicate`` holds.

    For in-process tests that drive ``GLib.MainContext`` directly
    instead of running a ``MainLoop``. Returns True when the
    predicate held in time; the caller asserts with a message.
    """
    from gi.repository import GLib

    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(interval_s)
        context.iteration(False)
    try:
        return bool(predicate())
    except Exception:
        return False


def poll_until(predicate, timeout_s: float = 15.0,
               interval_s: float = 0.05) -> bool:
    """Poll a plain predicate; True when it held in time.

    No GTK involved: for daemon sockets, ports, and files. Never
    raises; daemon-startup loops with display fallback use this.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(interval_s)
    try:
        return bool(predicate())
    except Exception:
        return False


def wait_until(predicate, timeout_s: float = 15.0,
               interval_s: float = 0.05, message: str = "condition never held",
               ) -> None:
    """Poll ``predicate``; raise ``AssertionError(message)`` on timeout."""
    if not poll_until(predicate, timeout_s, interval_s):
        raise AssertionError(message)
