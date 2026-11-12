"""Real-bus tests: activation, idle re-activation, sender-filtered Changed.

Each driver runs under ``dbus-run-session`` with ``XDG_DATA_DIRS``
pointing at a configured copy of the shipped
``data/io.piercingxx.Contacts.service.in``, so the test starts
nothing: the first method call activates the service from the built
file. Skip when ``dbus-run-session`` is missing.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
SRC = REPO / "src"
COMMON = REPO.parent / "common"
DATA = REPO / "data"

BUS_LINE = "Name=io.piercingxx.Contacts"

ADA_CARD = ("BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Ada Lovelace\r\n"
            "TEL;TYPE=CELL:+1 614 555 0100\r\nEND:VCARD\r\n")

ACTIVATION_DRIVER = """
import sys
import time
from gi.repository import Gio, GLib
sys.path.insert(0, __SRC__)
BUS = "io.piercingxx.Contacts"
OBJ = "/io/piercingxx/Contacts"
IFACE = "io.piercingxx.Contacts1"
CARD = __CARD__
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
def lookup(number):
    reply = bus.call_sync(BUS, OBJ, IFACE, "LookupNumber",
                          GLib.Variant("(s)", (number,)),
                          GLib.VariantType.new("(ssas)"),
                          Gio.DBusCallFlags.NONE, 20000, None)
    return reply.unpack()
def create(card):
    reply = bus.call_sync(BUS, OBJ, IFACE, "Create",
                          GLib.Variant("(s)", (card,)),
                          GLib.VariantType.new("(s)"),
                          Gio.DBusCallFlags.NONE, 20000, None)
    return reply.unpack()[0]
def name_owner():
    try:
        reply = bus.call_sync("org.freedesktop.DBus",
                              "/org/freedesktop/DBus",
                              "org.freedesktop.DBus", "GetNameOwner",
                              GLib.Variant("(s)", (BUS,)),
                              GLib.VariantType.new("(s)"),
                              Gio.DBusCallFlags.NONE, 5000, None)
        return reply.unpack()[0]
    except Exception:
        return ""
print("lookup-before", lookup("+1 614-555-0100"), flush=True)
uid = create(CARD)
print("created", uid, flush=True)
print("lookup-after", lookup("+1 614-555-0100"), flush=True)
print("owner-before-sleep", bool(name_owner()), flush=True)
time.sleep(__SLEEP__)
print("owner-after-idle", repr(name_owner()), flush=True)
print("lookup-reactivated", lookup("+1 614-555-0100"), flush=True)
"""

CHANGED_DRIVER = """
import sys
from gi.repository import Gio, GLib
sys.path.insert(0, __SRC__)
from xx_contacts import service_client
BUS = "io.piercingxx.Contacts"
OBJ = "/io/piercingxx/Contacts"
IFACE = "io.piercingxx.Contacts1"
CARD = __CARD__
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
own = bus.get_unique_name()
got = []
loop = GLib.MainLoop()
def on_changed(conn, sender, path, iface, signal, params):
    got.append((sender, params.unpack()))
    if len(got) >= 2:
        loop.quit()
bus.signal_subscribe(BUS, IFACE, "Changed", OBJ, None,
                     Gio.DBusSignalFlags.NONE, on_changed)
uid = service_client.create_sync(bus, CARD)
rev = service_client.update_tags_sync(bus, uid, add=("starred",))
GLib.timeout_add_seconds(10, loop.quit)
loop.run()
print("received", len(got), flush=True)
assert len(got) == 2, got
for sender, _params in got:
    assert sender != own, (sender, own)
sender, (got_rev, got_uids) = got[-1]
assert (got_rev, got_uids) == (rev, [uid]), (got[-1], rev, uid)
before = len(got)
bus.emit_signal(None, OBJ, IFACE, "Changed",
                GLib.Variant("(tas)", (999, ["nobody"])))
GLib.timeout_add_seconds(2, loop.quit)
loop.run()
assert len(got) == before, got
print("CHANGED_SENDER_OK", rev, uid, flush=True)
"""


def _need_runner():
    if shutil.which("dbus-run-session") is None:
        pytest.skip("dbus-run-session is not installed")


def _configure_tree(tmp_path: Path) -> dict:
    """Render the shipped .in files into a runnable prefix; return env."""
    bin_dir = tmp_path / "bin"
    services_dir = tmp_path / "xdg" / "dbus-1" / "services"
    bin_dir.mkdir(parents=True)
    services_dir.mkdir(parents=True)
    launcher = DATA / "xx-contacts.in"
    text = launcher.read_text(encoding="utf-8")
    assert "@python@" in text and "@app_dir@" in text
    launcher_path = bin_dir / "xx-contacts"
    launcher_path.write_text(
        "#!" + sys.executable + "\n" + text.split("\n", 1)[1]
        .replace("@python@", sys.executable)
        .replace("@app_dir@", str(SRC)),
        encoding="utf-8")
    launcher_path.chmod(0o755)
    service_in = DATA / "io.piercingxx.Contacts.service.in"
    service_text = service_in.read_text(encoding="utf-8")
    assert "[D-BUS Service]" in service_text
    assert BUS_LINE in service_text
    assert "SystemdService" not in service_text
    db_path = tmp_path / "contacts.db"
    (services_dir / "io.piercingxx.Contacts.service").write_text(
        service_text.replace("@bindir@", str(bin_dir)),
        encoding="utf-8")
    xdg = str(tmp_path / "xdg")
    session_xdg = xdg + ":" + os.environ.get(
        "XDG_DATA_DIRS", "/usr/local/share:/usr/share")
    env = dict(os.environ)
    env["XDG_DATA_DIRS"] = session_xdg
    env["PYTHONPATH"] = (str(SRC) + ":" + str(COMMON) + ":"
                         + env.get("PYTHONPATH", ""))
    return {"env": env, "db": str(db_path), "launcher": str(launcher_path)}


def _fix_service_exec(tmp_path: Path, db: str) -> None:
    path = (tmp_path / "xdg" / "dbus-1" / "services"
            / "io.piercingxx.Contacts.service")
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("Exec="):
            lines.append(line + f" --db {db}")
        else:
            lines.append(line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_session(driver_source: str, name: str, tmp_path: Path, env: dict,
                 timeout: int) -> subprocess.CompletedProcess:
    driver = tmp_path / name
    driver.write_text(driver_source, encoding="utf-8")
    return subprocess.run(
        ["dbus-run-session", "--", sys.executable, str(driver)],
        env=env, capture_output=True, text=True, timeout=timeout)


def test_shipped_service_file_activates_and_reactivates(tmp_path):
    _need_runner()
    tree = _configure_tree(tmp_path)
    _fix_service_exec(tmp_path, tree["db"])
    tree["env"]["XX_CONTACTS_IDLE_SECONDS"] = "2"
    sleep = 8
    driver = ACTIVATION_DRIVER.replace("__SRC__", repr(str(SRC)))
    driver = driver.replace("__CARD__", repr(ADA_CARD))
    driver = driver.replace("__SLEEP__", str(sleep))
    completed = _run_session(driver, "activation_driver.py", tmp_path,
                             tree["env"], 120)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    lines = completed.stdout.splitlines()
    before = next(line for line in lines if line.startswith("lookup-before"))
    assert "('', '', [])" in before, before
    created = next(line for line in lines if line.startswith("created"))
    uid = created.split()[-1]
    assert uid and uid.count("-") == 4, created
    after = next(line for line in lines if line.startswith("lookup-after"))
    assert uid in after and "Ada Lovelace" in after, after
    owned = next(line for line in lines if line.startswith("owner-before-sleep"))
    assert "True" in owned, owned
    gone = next(line for line in lines if line.startswith("owner-after-idle"))
    assert "''" in gone, gone
    revived = next(line for line in lines if line.startswith("lookup-reactivated"))
    assert uid in revived and "Ada Lovelace" in revived, revived


def test_changed_reaches_sender_filtered_subscriber(tmp_path):
    _need_runner()
    tree = _configure_tree(tmp_path)
    _fix_service_exec(tmp_path, tree["db"])
    tree["env"]["XX_CONTACTS_IDLE_SECONDS"] = "30"
    driver = CHANGED_DRIVER.replace("__SRC__", repr(str(SRC)))
    driver = driver.replace("__CARD__", repr(ADA_CARD))
    completed = _run_session(driver, "changed_driver.py", tmp_path,
                             tree["env"], 90)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert "CHANGED_SENDER_OK" in completed.stdout


ASYNC_DRIVER = """
import sys
from gi.repository import Gio, GLib
sys.path.insert(0, __SRC__)
from xx_contacts import service_client
CARD = __CARD__
results = []
loop = GLib.MainLoop()
def fail(name):
    def _fail(exc):
        results.append((name + "-error", (str(exc),)))
        loop.quit()
    return _fail
service_client.create_async(
    CARD, lambda uid: (
        results.append(("created", (uid,))),
        service_client.update_tags_async(
            uid, ("starred",), (), lambda rev: (
                results.append(("tagged", (rev,))),
                service_client.delete_async(
                    uid, lambda: (
                        results.append(("deleted", ())),
                        loop.quit()),
                    fail("delete"))),
            fail("tags"))),
    fail("create"))
GLib.timeout_add_seconds(15, loop.quit)
loop.run()
print("async-results", results, flush=True)
names = [name for name, _args in results]
assert names == ["created", "tagged", "deleted"], results
assert results[0][1][0].count("-") == 4, results
assert isinstance(results[1][1][0], int), results
print("ASYNC_OK", flush=True)
"""


def test_async_helpers_round_trip_over_the_bus(tmp_path):
    _need_runner()
    tree = _configure_tree(tmp_path)
    _fix_service_exec(tmp_path, tree["db"])
    tree["env"]["XX_CONTACTS_IDLE_SECONDS"] = "30"
    driver = ASYNC_DRIVER.replace("__SRC__", repr(str(SRC)))
    driver = driver.replace("__CARD__", repr(ADA_CARD))
    completed = _run_session(driver, "async_driver.py", tmp_path,
                             tree["env"], 90)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert "ASYNC_OK" in completed.stdout


def test_meson_installs_activation_without_systemd(tmp_path):
    build = (REPO / "meson.build").read_text(encoding="utf-8")
    assert "data/io.piercingxx.Contacts.service.in" in build
    assert "dbus-1" in build and "services" in build
