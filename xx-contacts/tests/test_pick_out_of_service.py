"""Pick out of the service: the D-Bus service launches the GUI.

The service never touches GTK: ``Pick`` holds the caller's invocation,
spawns ``xx-contacts --pick KIND --reply-to TOKEN`` (or the injected
``pick_launcher`` in tests), and settles the call once the GUI reports
back over ``PickReply``. The caller-visible contract is unchanged:
``Pick(s kind) -> (s uid, s value)``, cancel is ``("", "")``.

Fakes mirror the real library exactly: parameters are real
``GLib.Variant`` tuples and the invocation exposes only
``return_value`` / ``return_dbus_error``.
"""

import subprocess
import sys

import pytest
from gi.repository import GLib

from xx_contacts import service_client
from xx_contacts.phones import LookupIndex
from xx_contacts.service import (
    ERROR_INVALID_ARGUMENT,
    ERROR_NOT_IMPLEMENTED,
    INTERFACE,
    OBJECT_PATH,
    ContactsService,
)
from xx_contacts.store import ContactData, ContactStore, PhoneEntry


class FakeInvocation:
    def __init__(self):
        self.reply = None
        self.error = None

    def return_value(self, parameters):
        self.reply = parameters

    def return_dbus_error(self, name, message):
        self.error = (name, message)


@pytest.fixture
def service():
    store = ContactStore(":memory:")
    handle = ContactsService(store, LookupIndex(), "US")
    yield handle
    store.close()


def _seed(service) -> str:
    uid = service._store.create(ContactData(
        display_name="Ada Lovelace",
        phones=[PhoneEntry(number_raw="+1 555 0100",
                           number_e164="+15550100",
                           number_digits="15550100",
                           type="CELL", is_primary=True)]))
    service._rebuild_index()
    return uid


def _call(service, method, signature, *args):
    invocation = FakeInvocation()
    params = GLib.Variant(signature, args) if signature else None
    service.handle_method_call(None, ":1.1", OBJECT_PATH,
                               INTERFACE, method, params, invocation)
    return invocation


def test_pick_holds_call_until_gui_replies(service):
    ada_uid = _seed(service)
    launched = {}

    def fake_launcher(kind, token):
        launched["kind"] = kind
        launched["token"] = token
        return True

    service.pick_launcher = fake_launcher
    invocation = _call(service, "Pick", "(s)", "phone")
    assert invocation.error is None and invocation.reply is None
    assert service.pick_pending()
    assert launched["kind"] == "phone"
    assert launched["token"]
    assert service.reply_pick(launched["token"], ada_uid, "+1 555 0100")
    assert invocation.error is None
    assert invocation.reply.get_type_string() == "(ss)"
    assert invocation.reply.unpack() == (ada_uid, "+1 555 0100")
    assert not service.pick_pending()


def test_pick_cancel_replies_empty(service):
    _seed(service)
    launched = {}
    service.pick_launcher = lambda kind, token: bool(
        launched.setdefault("token", token) or True)
    invocation = _call(service, "Pick", "(s)", "contact")
    assert service.reply_pick(launched["token"], "", "")
    assert invocation.reply.unpack() == ("", "")
    assert not service.pick_pending()


def test_second_pick_while_open_fails(service):
    _seed(service)
    launched = {}
    service.pick_launcher = lambda kind, token: bool(
        launched.setdefault("token", token) or True)
    first = _call(service, "Pick", "(s)", "phone")
    second = _call(service, "Pick", "(s)", "email")
    assert second.reply is None
    assert second.error[0] == ERROR_INVALID_ARGUMENT
    assert service.pick_pending()
    assert service.reply_pick(launched["token"], "", "")
    assert first.reply.unpack() == ("", "")
    assert not service.pick_pending()


def test_pick_launch_failure_is_not_implemented(service):
    _seed(service)
    service.pick_launcher = lambda _kind, _token: False
    invocation = _call(service, "Pick", "(s)", "phone")
    assert invocation.reply is None
    assert invocation.error[0] == ERROR_NOT_IMPLEMENTED
    assert not service.pick_pending()


def test_pick_rejects_unknown_kind_before_launch(service):
    launched = []
    service.pick_launcher = lambda kind, token: launched.append(
        (kind, token)) or True
    invocation = _call(service, "Pick", "(s)", "pager")
    assert invocation.reply is None
    assert invocation.error[0] == ERROR_INVALID_ARGUMENT
    assert launched == []
    assert not service.pick_pending()


def test_pick_reply_dispatch_replies_empty_tuple(service):
    _seed(service)
    launched = {}
    service.pick_launcher = lambda kind, token: bool(
        launched.setdefault("token", token) or True)
    held = _call(service, "Pick", "(s)", "phone")
    reply = _call(service, "PickReply", "(sss)", launched["token"],
                  "uid-1", "+1 555 0100")
    assert reply.error is None
    assert reply.reply.unpack() == ()
    assert held.reply.unpack() == ("uid-1", "+1 555 0100")
    assert not service.pick_pending()


def test_pick_reply_with_unknown_token_fails(service):
    reply = _call(service, "PickReply", "(sss)", "no-such-token",
                  "uid-1", "+1 555 0100")
    assert reply.reply is None
    assert reply.error[0] == ERROR_INVALID_ARGUMENT


def test_pick_reply_with_wrong_arity_fails(service):
    reply = _call(service, "PickReply", "(ss)", "token", "uid")
    assert reply.reply is None
    assert reply.error[0] == ERROR_INVALID_ARGUMENT


def test_reply_pick_returns_false_for_unknown_token(service):
    assert service.reply_pick("no-such-token", "uid", "value") is False


def test_service_module_never_imports_gtk():
    import pathlib

    text = (pathlib.Path(__file__).parent.parent
            / "src" / "xx_contacts" / "service.py").read_text(
                encoding="utf-8")
    assert "present_pick_dialog" not in text
    assert "picker_ui" not in text
    assert "Gtk" not in text


def test_launch_argv_carries_pick_kind_token_and_db(tmp_path,
                                                    monkeypatch):
    db_path = str(tmp_path / "contacts.db")
    store = ContactStore(db_path)
    try:
        handle = ContactsService(store, LookupIndex(), "US")
        seen = {}

        class _Proc:
            pass

        def fake_popen(argv, **kwargs):
            seen["argv"] = list(argv)
            return _Proc()

        monkeypatch.setattr("subprocess.Popen", fake_popen)
        assert handle._launch_pick_gui("email", "token-9") is True
        argv = seen["argv"]
        assert argv[:4] == [sys.executable, "-m", "xx_contacts",
                            "--pick"]
        assert argv[4] == "email"
        assert argv[5:7] == ["--reply-to", "token-9"]
        assert argv[7:] == ["--db", db_path]
    finally:
        store.close()


def test_reply_pick_client_helpers_shape():
    import gi

    gi.require_version("GLib", "2.0")

    class FakeReply:
        def unpack(self):
            return ()

    class FakeBusConnection:
        def __init__(self):
            self.calls = []

        def call_sync(self, bus_name, path, interface, method,
                      parameters, reply_type, flags, timeout,
                      cancellable):
            self.calls.append((method, parameters.unpack()))
            return FakeReply()

    conn = FakeBusConnection()
    service_client.reply_pick_sync(conn, "token-1", "uid-7",
                                   "ada@example.org")
    assert conn.calls == [(("PickReply"), ("token-1", "uid-7",
                                           "ada@example.org"))]


PRIVATE_BUS_DRIVER = """
import sys
import threading
import time
sys.path.insert(0, __SRC__)
sys.path.insert(0, __COMMON__)
from gi.repository import Gio, GLib
from xx_contacts import service_client
from xx_contacts.phones import LookupIndex
from xx_contacts.service import (
    BUS_NAME, OBJECT_PATH, ContactsService, build_service)
from xx_contacts.store import ContactData, PhoneEntry

service = build_service(__DB__)
service._store.create(ContactData(
    display_name="Ada Lovelace",
    phones=[PhoneEntry(number_raw="+1 555 0100",
                       number_e164="+15550100",
                       number_digits="15550100",
                       type="CELL", is_primary=True)]))
service._rebuild_index()
loop = GLib.MainLoop()

def fake_launcher(kind, token):
    assert kind == "phone", kind
    def _gui():
        time.sleep(0.2)
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        service_client.reply_pick_sync(bus, token, "uid-ada",
                                       "+1 555 0100")
        GLib.idle_add(loop.quit)
    threading.Thread(target=_gui, daemon=True).start()
    return True

service.pick_launcher = fake_launcher

def on_bus_acquired(connection, _name):
    service.register_on(connection)

def on_name_acquired(_connection, _name):
    def _caller():
        time.sleep(0.5)
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            result = service_client.pick_sync(bus, "phone")
            print("PICK_RESULT: %r" % (result,), flush=True)
        except Exception as exc:
            print("PICK_ERROR: %r" % (exc,), flush=True)
        finally:
            GLib.idle_add(loop.quit)
    threading.Thread(target=_caller, daemon=True).start()

def on_name_lost(_connection, _name):
    print("PICK_ERROR: lost the bus name", flush=True)
    loop.quit()

Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME,
                 Gio.BusNameOwnerFlags.NONE,
                 on_bus_acquired, on_name_acquired, on_name_lost)
GLib.timeout_add_seconds(30, loop.quit)
loop.run()
print("DRIVER_DONE", flush=True)
"""


def test_pick_round_trip_over_a_private_bus(tmp_path):
    import shutil

    if shutil.which("dbus-run-session") is None:
        pytest.skip("dbus-run-session is not installed")
    db_path = str(tmp_path / "contacts.db")
    ContactStore(db_path).close()
    repo = __import__("pathlib").Path(__file__).parent.parent
    src = str(repo / "src")
    common = str(repo.parent / "common")
    driver = PRIVATE_BUS_DRIVER.replace("__SRC__", repr(src)).replace(
        "__COMMON__", repr(common)).replace("__DB__", repr(db_path))
    script = tmp_path / "pick_bus_driver.py"
    script.write_text(driver, encoding="utf-8")
    completed = subprocess.run(
        ["dbus-run-session", "--", sys.executable, str(script)],
        capture_output=True, text=True, timeout=90)
    assert completed.returncode == 0, (
        completed.stdout + completed.stderr)
    assert "PICK_RESULT: ('uid-ada', '+1 555 0100')" in completed.stdout, (
        completed.stdout + completed.stderr)
    assert "DRIVER_DONE" in completed.stdout
