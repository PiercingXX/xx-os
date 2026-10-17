"""Daemon over a private bus: real methods, real signals, GUI coexistence.

The GUI must start while the daemon runs (a shared bus name would make it
exit as a secondary instance), every write goes through the daemon, and
the daemon is the only RingingChanged emitter.
"""

import json
import threading
import time as _time

import pytest

pytestmark = pytest.mark.skipif(
    __import__("shutil").which("dbus-daemon") is None,
    reason="dbus-daemon is missing")

from xx_clock import (  # noqa: E402
    DAEMON_BUS_NAME,
    DAEMON_INTERFACE,
    DAEMON_OBJECT_PATH,
    prefs,
)
from xx_clock.coordinator import Hooks  # noqa: E402
from xx_clock.daemon import Api, ClockDaemon  # noqa: E402
from xx_clock.store import Store  # noqa: E402


def _require_gio():
    import gi
    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gio, GLib
    return Gio, GLib


class Harness:
    def __init__(self, tmp_path, monkeypatch, address):
        monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", address)
        Gio, GLib = _require_gio()
        self._Gio, self._GLib = Gio, GLib
        self.store = Store(path=tmp_path / "daemon.db")
        self.rings: list = []
        daemon_ref: dict = {}

        def _ring_start(entry_id, is_alarm):
            self.rings.append((entry_id, is_alarm))
            daemon = daemon_ref.get("daemon")
            label = ""
            if is_alarm:
                alarm = self.store.get_alarm(entry_id)
                label = alarm.label if alarm else ""
            else:
                timer = self.store.get_timer(entry_id)
                label = timer.label if timer else ""
            if daemon is not None:
                daemon.emit_ringing_changed(entry_id, True,
                                            "alarm" if is_alarm else "timer", label)
            return True

        hooks = Hooks(ring_start=_ring_start)
        self.api = Api(self.store, settings=prefs.Settings(), hooks=hooks)
        self.daemon = ClockDaemon(api=self.api)
        daemon_ref["daemon"] = self.daemon
        self.daemon_conn = Gio.DBusConnection.new_for_address_sync(
            address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        self.thread = threading.Thread(
            target=self.daemon.run, args=(self.daemon_conn,), daemon=True)
        self.thread.start()
        deadline = _time.time() + 10
        while self.daemon._bus is None and _time.time() < deadline:
            _time.sleep(0.05)
        assert self.daemon._bus is not None, "daemon never owned its bus name"
        self.bus = Gio.DBusConnection.new_for_address_sync(
            address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        self.proxy = Gio.DBusProxy.new_sync(
            self.bus, Gio.DBusProxyFlags.NONE, None,
            DAEMON_BUS_NAME, DAEMON_OBJECT_PATH, DAEMON_INTERFACE, None)

    def close(self):
        try:
            self.daemon._loop.quit()
        except Exception:
            pass
        self.thread.join(timeout=10)
        for conn in (self.bus, self.daemon_conn):
            try:
                conn.close_sync(None)
            except Exception:
                pass
        self.store.close()

    def call(self, method, params, reply_type):
        Gio, _GLib = self._Gio, self._GLib
        reply = self.proxy.call_sync(
            method, params, Gio.DBusCallFlags.NONE, 5000, None)
        assert reply.get_type_string() == reply_type, reply.get_type_string()
        return reply.unpack()

    def wait_signal(self, test_bus, signal, action, timeout_s=5.0):
        Gio, GLib = self._Gio, self._GLib
        seen: list = []
        loop = GLib.MainLoop()
        sub = test_bus.signal_subscribe(
            None, DAEMON_INTERFACE, signal, DAEMON_OBJECT_PATH, None,
            0, lambda *args: (seen.append(args[5].unpack()), loop.quit()))
        GLib.timeout_add(int(timeout_s * 1000), loop.quit)
        try:
            test_bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus", "GetId", None,
                GLib.VariantType.new("(s)"),
                Gio.DBusCallFlags.NONE, 5000, None)
            action()
            loop.run()
        finally:
            test_bus.signal_unsubscribe(sub)
        return seen[0] if seen else None


def test_save_get_and_timer_round_trip(private_bus, tmp_path, monkeypatch):
    harness = Harness(tmp_path, monkeypatch, private_bus)
    try:
        _Gio, GLib = harness._Gio, harness._GLib
        (alarm_id,) = harness.call("SaveAlarm", GLib.Variant("(s)", (json.dumps(
            {"hour": 7, "minute": 30, "label": "Bus"}),)), "(x)")
        assert alarm_id > 0
        (raw,) = harness.call("GetState", None, "(s)")
        state = json.loads(raw)
        assert state["alarms"][0]["label"] == "Bus"
        assert state["next"]["id"] == alarm_id
        (bad,) = harness.call("SaveAlarm", GLib.Variant("(s)", ("{nope",)), "(x)")
        assert bad == -1
        (timer_id,) = harness.call(
            "StartTimer", GLib.Variant("(xs)", (120_000, "tea")), "(x)")
        assert timer_id > 0
        (ok,) = harness.call("PauseTimer", GLib.Variant("(x)", (timer_id,)), "(b)")
        assert ok is True
        (missing,) = harness.call("DeleteAlarm", GLib.Variant("(x)", (424242,)), "(b)")
        assert missing is False
    finally:
        harness.close()


def _fresh_conn(Gio, address):
    return Gio.DBusConnection.new_for_address_sync(
        address,
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)


def test_state_changed_emitted_on_save(private_bus, tmp_path, monkeypatch):
    harness = Harness(tmp_path, monkeypatch, private_bus)
    try:
        Gio, GLib = harness._Gio, harness._GLib
        bus = _fresh_conn(Gio, private_bus)
        try:
            (saved,) = harness.call("SaveAlarm", GLib.Variant("(s)", (json.dumps(
                {"hour": 6, "minute": 0}),)), "(x)")
            assert saved > 0
            seen = harness.wait_signal(
                bus, "StateChanged",
                lambda: harness.call("ToggleAlarm", GLib.Variant("(xb)", (saved, False)),
                                     "(b)"))
            assert seen is not None
        finally:
            bus.close_sync(None)
    finally:
        harness.close()


def test_ringing_changed_emitted_by_daemon_only(private_bus, tmp_path, monkeypatch):
    harness = Harness(tmp_path, monkeypatch, private_bus)
    try:
        _Gio, GLib = harness._Gio, harness._GLib
        (alarm_id,) = harness.call(
            "SaveAlarm", GLib.Variant("(s)", (json.dumps(
                {"hour": 7, "minute": 0, "label": "Wake"}),)), "(x)")
        assert alarm_id > 0
        bus = _fresh_conn(_Gio, private_bus)
        try:
            fired = harness.wait_signal(
                bus, "RingingChanged",
                lambda: harness.api.coordinator.fire_alarm(alarm_id))
        finally:
            bus.close_sync(None)
        assert fired, "no RingingChanged emitted"
        entry_id, ringing, kind, label = fired
        assert (entry_id, ringing, kind, label) == (alarm_id, True, "alarm", "Wake")
    finally:
        harness.close()


def test_resync_arms_direct_store_writes(private_bus, tmp_path, monkeypatch):
    import time as _clock_time

    from xx_clock.models import Alarm
    harness = Harness(tmp_path, monkeypatch, private_bus)
    try:
        alarm_id = harness.store.next_id()
        harness.store.save_alarm(Alarm(id=alarm_id, hour=7, minute=0))
        assert harness.store.scheduled_fire(alarm_id) == 0
        (ok,) = harness.call("Resync", None, "(b)")
        assert ok is True
        assert harness.store.scheduled_fire(alarm_id) > int(_clock_time.time() * 1000)
    finally:
        harness.close()


def test_gui_upgrades_to_daemon_without_blocking(private_bus, tmp_path, monkeypatch):
    from xx_clock.service_client import ClockService
    harness = Harness(tmp_path, monkeypatch, private_bus)
    try:
        GLib = harness._GLib
        service = ClockService(defer_daemon=True)
        assert service.daemon_available is False
        finished: list = []
        service.upgrade_to_daemon(finished.append)
        deadline = _time.time() + 10
        while not finished and _time.time() < deadline:
            GLib.MainContext.default().iteration(False)
            _time.sleep(0.02)
        assert finished == [True], finished
        assert service.daemon_available is True
        assert service.get_state()["alarms"] == []
    finally:
        harness.close()


def test_gui_starts_while_daemon_runs(private_bus, tmp_path, monkeypatch):
    harness = Harness(tmp_path, monkeypatch, private_bus)
    try:
        from xx_clock.service_client import ClockService
        service = ClockService()
        assert service.daemon_available is True
        state = service.get_state()
        assert state["alarms"] == []
        alarm_id = service.save_alarm({"hour": 9, "minute": 15})
        assert alarm_id > 0
        assert service.toggle_alarm(alarm_id, False) is True
    finally:
        harness.close()
