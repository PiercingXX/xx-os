"""NetworkManager state: pure monitor units plus a fake NM round trip.

The round trip runs a helper under its own private bus
(``dbus-run-session``), which owns the real NetworkManager name,
answers GetAll, and emits PropertiesChanged while the watcher follows
along. It skips when dbus-run-session is missing.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from gi.repository import GLib

from xx_music.netstate import (
    BUS_NAME,
    CONNECTIVITY_FULL,
    CONNECTIVITY_NONE,
    INTERFACE,
    OBJECT_PATH,
    PROPS_INTERFACE,
    NetworkMonitor,
    NMWatcher,
    is_metered,
    is_online,
)

ROOT = Path(__file__).resolve().parent.parent


def test_metered_values():
    assert is_metered(1) and is_metered(3)
    assert not is_metered(0)
    assert not is_metered(2)
    assert not is_metered(4)
    assert not is_metered(None)
    assert not is_metered("bogus")


def test_online_only_none_is_offline():
    assert is_online(4) and is_online(2) and is_online(3)
    assert not is_online(1)
    assert is_online(0)
    assert is_online(None)


def test_update_from_props_with_raw_ints():
    monitor = NetworkMonitor()
    monitor.update_from_props({"Metered": 1, "Connectivity": 4})
    assert monitor.snapshot.metered and monitor.snapshot.online
    assert monitor.prefetch_depth == 2


def test_update_from_props_with_variants():
    monitor = NetworkMonitor()
    monitor.update_from_props({
        "Metered": GLib.Variant("u", 3),
        "Connectivity": GLib.Variant("u", 1),
    })
    assert monitor.snapshot.metered and not monitor.snapshot.online
    assert monitor.snapshot.connectivity == 1


def test_unrelated_props_leave_state_alone():
    monitor = NetworkMonitor()
    before = monitor.snapshot
    monitor.update_from_props({"State": 70})
    assert monitor.snapshot == before


def test_no_service_is_unmetered_online():
    monitor = NetworkMonitor()
    monitor.update(metered_raw=1, connectivity_raw=1)
    monitor.set_no_service()
    assert monitor.snapshot.online and not monitor.snapshot.metered
    assert monitor.prefetch_depth == 8


def test_subscribe_fires_once_per_change():
    monitor = NetworkMonitor()
    seen = []
    stop = monitor.subscribe(seen.append)
    monitor.update(metered_raw=1)
    monitor.update(metered_raw=1)
    assert len(seen) == 1 and seen[0].metered
    stop()
    monitor.update(metered_raw=2)
    assert len(seen) == 1


def test_properties_changed_for_other_interfaces_ignored():
    monitor = NetworkMonitor()
    watcher = NMWatcher(monitor)
    params = GLib.Variant("(sa{sv}as)", (
        "org.freedesktop.DBus", {"Metered": GLib.Variant("u", 1)}, []))
    watcher._on_properties_changed(None, BUS_NAME, OBJECT_PATH,
                                   PROPS_INTERFACE, "PropertiesChanged",
                                   params)
    assert not monitor.snapshot.metered


def test_properties_changed_updates_state():
    monitor = NetworkMonitor()
    watcher = NMWatcher(monitor)
    params = GLib.Variant("(sa{sv}as)", (
        INTERFACE, {"Metered": GLib.Variant("u", 1),
                    "Connectivity": GLib.Variant("u", 4)}, []))
    watcher._on_properties_changed(None, BUS_NAME, OBJECT_PATH,
                                   PROPS_INTERFACE, "PropertiesChanged",
                                   params)
    assert monitor.snapshot.metered and monitor.snapshot.online


def test_missing_service_keeps_defaults():
    monitor = NetworkMonitor()

    def no_bus():
        raise OSError("no bus here")

    watcher = NMWatcher(monitor, connection_fn=no_bus)
    watcher.start()
    assert monitor.snapshot.online and not monitor.snapshot.metered


_HELPER = '''
import sys
sys.path.insert(0, SRC)

from gi.repository import Gio, GLib

from xx_music import netstate
from xx_music.netstate import (
    BUS_NAME, INTERFACE, OBJECT_PATH, PROPS_INTERFACE, NetworkMonitor,
    NMWatcher,
)

XML = """
<node>
  <interface name="org.freedesktop.DBus.Properties">
    <method name="GetAll">
      <arg type="s" name="interface" direction="in"/>
      <arg type="a{sv}" name="properties" direction="out"/>
    </method>
  </interface>
  <interface name="org.freedesktop.NetworkManager">
    <property type="u" name="Metered" access="read"/>
    <property type="u" name="Connectivity" access="read"/>
  </interface>
</node>
"""

state = {"Metered": 2, "Connectivity": 4}
conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)


def on_props(conn, sender, path, iface, method, params, invocation):
    (name,) = params.unpack()
    if method != "GetAll" or name != INTERFACE:
        invocation.return_dbus_error(
            "org.freedesktop.DBus.Error.UnknownMethod", "no such method")
        return
    invocation.return_value(GLib.Variant("(a{sv})", ({
        "Metered": GLib.Variant("u", state["Metered"]),
        "Connectivity": GLib.Variant("u", state["Connectivity"]),
    },)))


def on_nm(*args):
    pass


node = Gio.DBusNodeInfo.new_for_xml(XML)
conn.register_object(OBJECT_PATH, node.interfaces[0], on_props)
conn.register_object(OBJECT_PATH, node.interfaces[1], on_nm)
reply = conn.call_sync(
    "org.freedesktop.DBus", "/org/freedesktop/DBus",
    "org.freedesktop.DBus", "RequestName",
    GLib.Variant("(su)", (BUS_NAME, 0)),
    GLib.VariantType.new("(u)"),
    Gio.DBusCallFlags.NONE, 5000, None)
assert reply.unpack()[0] in (1, 2, 3, 4), reply.unpack()

monitor = NetworkMonitor()
watcher = NMWatcher(monitor, connection=conn)
watcher.start()
seen = []
monitor.subscribe(seen.append)
loop = GLib.MainLoop()
failed = []


def emit(changed):
    conn.emit_signal(None, OBJECT_PATH, PROPS_INTERFACE,
                     "PropertiesChanged",
                     GLib.Variant("(sa{sv}as)", (INTERFACE, {
                         key: GLib.Variant("u", value)
                         for key, value in changed.items()
                     }, [])))


def check(what, cond):
    if not cond:
        failed.append(what)
        print("FAIL " + what, flush=True)
        loop.quit()
        return False
    return True


def step_initial():
    if not check("initial", (not monitor.snapshot.metered
                             and monitor.snapshot.online)):
        return False
    state["Metered"] = 1
    emit({"Metered": 1})
    GLib.timeout_add(300, step_metered)
    return False


def step_metered():
    if not check("metered", monitor.snapshot.metered):
        return False
    if not check("depth", monitor.prefetch_depth == 2):
        return False
    state["Connectivity"] = 1
    emit({"Connectivity": 1})
    GLib.timeout_add(300, step_offline)
    return False


def step_offline():
    if not check("offline", not monitor.snapshot.online):
        return False
    if not check("seen", len(seen) >= 2):
        return False
    print("NM-BUS-OK", flush=True)
    loop.quit()
    return False


def give_up():
    failed.append("timeout")
    print("FAIL timeout", flush=True)
    loop.quit()
    return False


GLib.timeout_add(400, step_initial)
GLib.timeout_add(10000, give_up)
loop.run()
sys.exit(1 if failed else 0)
'''


def test_fake_nm_round_trip_on_private_bus(tmp_path):
    if shutil.which("dbus-run-session") is None:
        pytest.skip("dbus-run-session is not installed")
    helper = tmp_path / "nm_bus_helper.py"
    helper.write_text(_HELPER.replace("SRC", repr(str(ROOT / "src"))))
    env = {**os.environ,
           "PYTHONPATH": os.pathsep.join(str(ROOT / "src"))}
    proc = subprocess.run(
        ["dbus-run-session", "--", sys.executable, str(helper)],
        env=env, capture_output=True, text=True, timeout=120)
    assert "NM-BUS-OK" in proc.stdout, (
        f"helper failed (rc={proc.returncode}):\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}")
    assert proc.returncode == 0
    assert CONNECTIVITY_NONE == 1
    assert CONNECTIVITY_FULL == 4
