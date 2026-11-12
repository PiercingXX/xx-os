"""First-run Setup probes: real state, never cached guesses.

Each probe reads live system state on a worker thread (never the GTK
main loop) and reports ``(ok, detail)`` for one Setup row:

- xx-contacts reachable or at least D-Bus activatable,
- the policy service activatable (``ListActivatableNames`` — never a
  ``GetNameOwner`` gate, which reports an activatable-but-idle service
  as absent and would keep it dead; see ``docs/shell-consult.md``),
- ModemManager present with a Voice-capable modem,
- the shell consulting us (screen rows the shell's ``CheckNumber``
  wrote, per ``docs/shell-consult.md``),
- a notification server reachable, plus a test-ring sender.

Pure mappers take fetched data so headless tests drive them without a
bus; the ``real_*`` fetchers do the Gio calls. All Gio imports stay
inside functions so this module loads without gi.
"""
from __future__ import annotations

from dataclasses import dataclass

from xx_dialer import modem as modem_mod
from xx_dialer.service import BUS_NAME as POLICY_BUS_NAME

CONTACTS_BUS_NAME = "io.piercingxx.Contacts"
NOTIFY_BUS_NAME = "org.freedesktop.Notifications"


@dataclass(frozen=True)
class Probe:
    ok: bool
    detail: str


def _names_shape(fetched) -> tuple[set, set]:
    owned, activatable = fetched
    return (set(owned or ()), set(activatable or ()))


def probe_contacts(fetched) -> Probe:
    owned, activatable = _names_shape(fetched)
    if CONTACTS_BUS_NAME in owned:
        return Probe(True, "Contacts app is running.")
    if CONTACTS_BUS_NAME in activatable:
        return Probe(True, "Contacts app is installed; starts on demand.")
    return Probe(False, "Contacts app is not installed or not running; "
                        "names and stars are unavailable.")


def probe_policy(fetched) -> Probe:
    owned, activatable = _names_shape(fetched)
    if POLICY_BUS_NAME in owned:
        return Probe(True, "Screening service is running.")
    if POLICY_BUS_NAME in activatable:
        return Probe(True, "Screening service is installed; "
                           "the first call starts it.")
    return Probe(False, "Screening service is not installed; "
                        "calls ring unfiltered.")


def probe_modem(objects: dict | None) -> Probe:
    if objects is None:
        return Probe(False, "ModemManager is not reachable.")
    modems = [path for path in objects
              if modem_mod.MODEM_IFACE in objects[path]]
    if not modems:
        return Probe(False, "No modem found.")
    voice = [path for path in modems
             if modem_mod.VOICE_IFACE in objects[path]]
    if not voice:
        return Probe(False, "Modem found, but none handles voice calls.")
    return Probe(True, "Voice-capable modem found.")


def probe_notifications(fetched) -> Probe:
    owned, _activatable = _names_shape(fetched)
    if NOTIFY_BUS_NAME in owned:
        return Probe(True, "Notification server is running.")
    return Probe(False, "No notification server; "
                        "missed-call cards have nowhere to go.")


def probe_consult(store) -> Probe:
    """Has the shell's consult reached us (a screening was recorded)?"""
    try:
        rows = store.last_screened(1)
    except Exception:
        return Probe(False, "Screening log is unreadable.")
    if not rows:
        return Probe(False, "No screenings recorded yet; "
                            "the next incoming call is the first test.")
    latest = rows[0]
    return Probe(True, "Last screening %s (%s)." % (
        _ago_label(latest.at), latest.reason or latest.verdict))


def _ago_label(at_ms: int) -> str:
    import time

    age_s = max(0, int(time.time() * 1000) - int(at_ms)) // 1000
    if age_s < 60:
        return "just now"
    if age_s < 3600:
        return "%d min ago" % (age_s // 60)
    if age_s < 86400:
        return "%d h ago" % (age_s // 3600)
    return "%d d ago" % (age_s // 86400)


def real_session_names() -> tuple[list, list]:
    """Owned + activatable session-bus names. Worker thread only."""
    from gi.repository import Gio

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    if bus is None:
        return ([], [])
    try:
        owned = bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "ListNames",
            None, None, Gio.DBusCallFlags.NONE, 5000, None,
        ).unpack()[0]
    except Exception:
        owned = []
    try:
        activatable = bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "ListActivatableNames",
            None, None, Gio.DBusCallFlags.NONE, 5000, None,
        ).unpack()[0]
    except Exception:
        activatable = []
    return (list(owned), list(activatable))


def real_modem_objects() -> dict | None:
    """MM1 managed objects, or None when unreachable. Worker only."""
    from gi.repository import Gio

    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    except Exception:
        return None
    if bus is None:
        return None
    try:
        objects = bus.call_sync(
            modem_mod.MM1, modem_mod.MM1_PATH,
            modem_mod.OBJMGR_IFACE, "GetManagedObjects",
            None, None, Gio.DBusCallFlags.NONE, 8000, None,
        ).unpack()[0]
    except Exception:
        return None
    try:
        return dict(objects)
    except Exception:
        return None


def send_test_ring(on_done=None, bus=None, gio=None,
                   glib=None) -> None:
    """Post one test notification through the sound-hint path.

    Reuses the notifier's params shape (plain-dict ``a{sv}`` hints, no
    nested prebuilt Variant). Async with a two-arg callback; failures
    report through ``on_done(False, detail)``, never raise.
    """
    from xx_dialer import ringer

    if gio is None or glib is None:
        try:
            from gi.repository import Gio as _gio, GLib as _glib
        except ImportError:
            if on_done is not None:
                on_done(False, "no bus")
            return
        gio = _gio if gio is None else gio
        glib = _glib if glib is None else glib
    try:
        if bus is None:
            bus = gio.bus_get_sync(gio.BusType.SESSION, None)
        if bus is None:
            raise RuntimeError("no session bus")
    except Exception as err:
        if on_done is not None:
            on_done(False, str(err))
        return

    def _done(conn, result) -> None:
        try:
            conn.call_finish(result)
        except Exception as err:
            if on_done is not None:
                on_done(False, str(err))
            return
        if on_done is not None:
            on_done(True, "")

    try:
        params = ringer.notify_params(
            glib, "dialer", 0, "Test ring",
            "If you saw and heard this, ringing works.",
            [], {"sound-name": "phone-incoming-call"})
        bus.call(NOTIFY_BUS_NAME, "/org/freedesktop/Notifications",
                 NOTIFY_BUS_NAME, "Notify", params, None,
                 gio.DBusCallFlags.NONE, 5000, None, _done)
    except Exception as err:
        if on_done is not None:
            on_done(False, str(err))
