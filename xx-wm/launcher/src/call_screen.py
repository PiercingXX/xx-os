"""Shell side of dialer call screening (``io.piercingxx.XXDialer.Policy1``).

Service ``io.piercingxx.XXDialer.Policy`` at ``/io/piercingxx/XXDialer/Policy``
(the policy endpoint has its own bus name: the dialer GUI's
``application_id`` already owns ``io.piercingxx.XXDialer``).

Mirrors the contract in ref/xx-dialer/docs/shell-consult.md: one async
``CheckNumber`` per incoming call with auto-start allowed, 2000 ms
timeout, fail-open to ring; fire-and-forget ``NoteCall`` after every
call ends. Never probes for the name first: the service is
D-Bus-activatable, so the consult call itself starts it.
"""
from __future__ import annotations
from shell_log import log_exc

BUS_NAME = "io.piercingxx.XXDialer.Policy"
OBJECT_PATH = "/io/piercingxx/XXDialer/Policy"
INTERFACE = "io.piercingxx.XXDialer.Policy1"

CONSULT_TIMEOUT_MS = 2_000
FAIL_OPEN_VERDICT = "ring"
FAIL_OPEN_REASON = "fail-open"

VALID_VERDICTS = frozenset({"ring", "silence", "block"})

NOTE_TIMEOUT_MS = 2_000


def _gio_glib(gio, glib):
    if gio is not None and glib is not None:
        return gio, glib
    from gi.repository import Gio as _gio, GLib as _glib
    return _gio if gio is None else gio, _glib if glib is None else glib


def consult_check_number(number, on_verdict, bus=None, gio=None,
                         glib=None,
                         timeout_ms: int = CONSULT_TIMEOUT_MS,
                         on_reached=None) -> None:
    """Async CheckNumber, fail-open to ring.

    Calls directly with auto-start allowed (flags NONE) so activation
    happens through the call itself. ``on_verdict(verdict, reason)``
    fires exactly once on the main loop. ``on_reached(True)`` fires when
    the service answered (even with an unusable verdict, which still
    fail-opens); ``on_reached(False)`` when it never did.
    """
    def _report_reached(reached: bool) -> None:
        if on_reached is not None:
            try:
                on_reached(bool(reached))
            except Exception:
                log_exc('call_screen._report_reached: on_reached')

    try:
        gio, glib = _gio_glib(gio, glib)
    except ImportError:
        _report_reached(False)
        on_verdict(FAIL_OPEN_VERDICT, FAIL_OPEN_REASON)
        return
    try:
        if bus is None:
            bus = gio.bus_get_sync(gio.BusType.SESSION, None)
        if bus is None:
            _report_reached(False)
            on_verdict(FAIL_OPEN_VERDICT, FAIL_OPEN_REASON)
            return
    except Exception:
        _report_reached(False)
        on_verdict(FAIL_OPEN_VERDICT, FAIL_OPEN_REASON)
        return

    raw = number if isinstance(number, str) else ""

    def _on_called(conn, result) -> None:
        try:
            verdict, reason = conn.call_finish(result).unpack()
        except Exception:
            _report_reached(False)
            on_verdict(FAIL_OPEN_VERDICT, FAIL_OPEN_REASON)
            return
        _report_reached(True)
        if verdict not in VALID_VERDICTS:
            on_verdict(FAIL_OPEN_VERDICT, FAIL_OPEN_REASON)
            return
        on_verdict(str(verdict), str(reason))

    try:
        bus.call(
            BUS_NAME, OBJECT_PATH, INTERFACE, "CheckNumber",
            glib.Variant("(s)", (raw,)),
            glib.VariantType.new("(ss)"),
            gio.DBusCallFlags.NONE, timeout_ms, None, _on_called,
        )
    except Exception:
        _report_reached(False)
        on_verdict(FAIL_OPEN_VERDICT, FAIL_OPEN_REASON)


def note_call(number, direction: str, started_ms: int, duration_ms: int,
              disposition: str, bus=None, gio=None, glib=None,
              timeout_ms: int = NOTE_TIMEOUT_MS) -> None:
    """Fire-and-forget NoteCall; transport errors only log at DEBUG."""
    try:
        gio, glib = _gio_glib(gio, glib)
    except ImportError:
        return
    try:
        if bus is None:
            bus = gio.bus_get_sync(gio.BusType.SESSION, None)
        if bus is None:
            return
    except Exception:
        return

    raw_number = number if isinstance(number, str) else ""
    try:
        params = glib.Variant(
            "(ssxxs)",
            (raw_number, str(direction), int(started_ms),
             int(duration_ms), str(disposition)),
        )
    except Exception:
        return

    def _on_done(conn, result) -> None:
        try:
            conn.call_finish(result)
        except Exception as err:
            from shell_log import get_logger
            get_logger("call_screen").debug("NoteCall failed: %s", err)

    try:
        bus.call(
            BUS_NAME, OBJECT_PATH, INTERFACE, "NoteCall",
            params, None,
            gio.DBusCallFlags.NONE, timeout_ms, None, _on_done,
        )
    except Exception as err:
        from shell_log import get_logger
        get_logger("call_screen").debug("NoteCall failed: %s", err)
