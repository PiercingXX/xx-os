"""The shell talks to the on-screen keyboard that is actually running.

xx-keyboard and squeekboard both serve sm.puri.OSK0. On the FLX1
xx-keyboard ran without owning the name and every SetVisible logged
ServiceUnknown. osk.KeyboardLink finds the running keyboard (/proc) and
the name's owner (async D-Bus) and only sends SetVisible, without
auto-start, when the owner is that keyboard.

The fake bus mirrors Gio's async API: call(..., cancellable, callback)
calls callback(bus, result); call_finish(result) returns the reply tuple
or raises; signal handlers get (conn, sender, path, iface, signal,
params, user_data).
"""
import ast
import os
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / 'launcher' / 'src'
sys.path.insert(0, str(SRC))

import osk  # noqa: E402


class _Reply:
    def __init__(self, *values):
        self._values = values

    def unpack(self):
        return self._values


class _Error(Exception):
    pass


class FakeBus:
    def __init__(self):
        self.calls: list = []        # (dest, method, params, flags, cb)
        self.subscriptions: list = []

    def signal_subscribe(self, sender, iface, member, path, arg0, flags,
                         callback, user_data):
        self.subscriptions.append((member, arg0, callback, user_data))
        return len(self.subscriptions)

    def call(self, dest, path, iface, method, params, reply_type, flags,
             timeout, cancellable, callback):
        self.calls.append((dest, method, params, flags, callback))

    def call_finish(self, result):
        if isinstance(result, Exception):
            raise result
        return result

    # helpers ----------------------------------------------------------
    def pending(self, method):
        return [c for c in self.calls if c[1] == method]

    def answer(self, method, result):
        for call in list(self.calls):
            if call[1] == method:
                self.calls.remove(call)
                call[4](self, result)
                return
        raise AssertionError(f'no pending {method}')

    def set_visible_calls(self):
        return [(c[0], c[2].unpack()[0], c[3]) for c in self.calls
                if c[1] == 'SetVisible']

    def owner_changed(self, new_owner):
        from gi.repository import GLib
        for member, _arg0, callback, user_data in self.subscriptions:
            if member == 'NameOwnerChanged':
                callback(self, 'org.freedesktop.DBus', '/org/freedesktop/DBus',
                         'org.freedesktop.DBus', 'NameOwnerChanged',
                         GLib.Variant('(sss)', (osk.OSK_NAME, '', new_owner)),
                         user_data)


def _proc(tmp_path, procs):
    """Fake /proc: {pid: comm}, all owned by this uid."""
    root = tmp_path / 'proc'
    root.mkdir()
    (root / 'self').mkdir()
    for pid, comm in procs.items():
        d = root / str(pid)
        d.mkdir()
        (d / 'comm').write_text(comm + '\n')
    return root


@pytest.fixture
def real_gi():
    try:
        from gi.repository import Gio, GLib  # noqa: F401
        GLib.Variant('(b)', (True,))
        GLib.VariantType.new('(s)')
        Gio.DBusCallFlags.NO_AUTO_START  # noqa: B018
    except Exception:
        pytest.skip('real GLib/Gio unavailable')


class TestRunningKeyboard:
    def test_xx_keyboard_wins_over_squeekboard(self, tmp_path):
        proc = _proc(tmp_path, {10: 'squeekboard', 20: 'xx-keyboard', 30: 'bash'})
        assert osk.running_keyboard(proc) == 'xx-keyboard'

    def test_truncated_stevia_comm_is_recognised(self, tmp_path):
        proc = _proc(tmp_path, {10: 'phosh-osk-stevi'})
        assert osk.running_keyboard(proc) == 'phosh-osk-stevia'

    def test_other_users_keyboards_are_ignored(self, tmp_path):
        proc = _proc(tmp_path, {10: 'squeekboard'})
        assert osk.running_keyboard(proc, uid=os.getuid() + 1) is None

    def test_none_running(self, tmp_path):
        assert osk.running_keyboard(_proc(tmp_path, {1: 'systemd'})) is None


def _link(tmp_path, procs):
    bus = FakeBus()
    return osk.KeyboardLink(bus, proc=_proc(tmp_path, procs)), bus


class TestKeyboardLink:
    def test_no_owner_sends_nothing_and_notes_it_once(
            self, real_gi, tmp_path, caplog):
        link, bus = _link(tmp_path, {20: 'xx-keyboard'})
        link.set_visible(True)
        bus.answer('GetNameOwner', _Error('NameHasNoOwner'))
        link.set_visible(False)
        link.set_visible(True)
        assert bus.set_visible_calls() == []
        assert not [r for r in caplog.records if r.levelname == 'WARNING']

    def test_running_keyboard_owning_the_name_gets_setvisible(
            self, real_gi, tmp_path):
        from gi.repository import Gio
        link, bus = _link(tmp_path, {20: 'xx-keyboard'})
        link.set_visible(True)
        assert bus.set_visible_calls() == []   # held until the owner is known
        bus.answer('GetNameOwner', _Reply(':1.7'))
        bus.answer('GetConnectionUnixProcessID', _Reply(20))
        assert bus.set_visible_calls() == [
            ('sm.puri.OSK0', True, Gio.DBusCallFlags.NO_AUTO_START)]
        link.set_visible(False)
        assert bus.set_visible_calls()[-1][1] is False

    def test_newest_request_wins_while_resolving(self, real_gi, tmp_path):
        link, bus = _link(tmp_path, {20: 'squeekboard'})
        link.set_visible(True)
        link.set_visible(False)
        bus.answer('GetNameOwner', _Reply(':1.7'))
        bus.answer('GetConnectionUnixProcessID', _Reply(20))
        assert [v for _d, v, _f in bus.set_visible_calls()] == [False]

    def test_another_keyboard_holding_the_name_is_not_asked(
            self, real_gi, tmp_path):
        link, bus = _link(tmp_path, {20: 'xx-keyboard', 30: 'squeekboard'})
        link.set_visible(True)
        bus.answer('GetNameOwner', _Reply(':1.9'))
        bus.answer('GetConnectionUnixProcessID', _Reply(30))
        assert bus.set_visible_calls() == []

    def test_owner_change_is_followed(self, real_gi, tmp_path):
        link, bus = _link(tmp_path, {20: 'xx-keyboard'})
        link.set_visible(True)
        bus.answer('GetNameOwner', _Error('NameHasNoOwner'))
        assert bus.set_visible_calls() == []
        bus.owner_changed(':1.20')                 # xx-keyboard took the name
        bus.answer('GetConnectionUnixProcessID', _Reply(20))
        link.set_visible(True)
        assert len(bus.set_visible_calls()) == 1
        bus.owner_changed('')                      # and lost it
        link.set_visible(False)
        assert len(bus.set_visible_calls()) == 1

    def test_unknown_owner_process_is_still_asked(self, real_gi, tmp_path):
        link, bus = _link(tmp_path, {20: 'xx-keyboard'})
        link.set_visible(True)
        bus.answer('GetNameOwner', _Reply(':1.7'))
        bus.answer('GetConnectionUnixProcessID', _Error('no pid'))
        assert len(bus.set_visible_calls()) == 1


def test_reply_types_are_variant_types_or_none():
    """Real PyGObject rejects a str reply_type (a silent no-op behind a
    try/except). Every bus.call in osk.py passes VariantType.new(...) or
    None as its sixth argument."""
    tree = ast.parse((SRC / 'osk.py').read_text(encoding='utf-8'))
    seen = 0
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'call' and len(node.args) >= 6):
            seen += 1
            reply = node.args[5]
            ok = (isinstance(reply, ast.Constant) and reply.value is None) or (
                isinstance(reply, ast.Call)
                and isinstance(reply.func, ast.Attribute)
                and reply.func.attr == 'new'
                and ast.unparse(reply.func.value).endswith('VariantType'))
            assert ok, ast.unparse(node)
    assert seen >= 3


# ------------------------------------------------ real private bus

_OSK_XML = '''
<node>
  <interface name="sm.puri.OSK0">
    <method name="SetVisible"><arg type="b" name="visible" direction="in"/></method>
    <property name="Visible" type="b" access="read"/>
  </interface>
</node>
'''


@pytest.fixture()
def private_bus(real_gi):
    import shutil
    import subprocess
    if shutil.which('dbus-daemon') is None:
        pytest.skip('needs dbus-daemon')
    from gi.repository import Gio
    proc = subprocess.Popen(
        ['dbus-daemon', '--session', '--nofork', '--print-address=1'],
        stdout=subprocess.PIPE, text=True)
    try:
        address = proc.stdout.readline().strip()
        flags = (Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                 | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION)
        conns = [Gio.DBusConnection.new_for_address_sync(
            address, flags, None, None) for _ in range(2)]
        yield conns
        for conn in conns:
            conn.close_sync(None)
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def _spin(until, seconds=3.0):
    import time
    from gi.repository import GLib
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while not until() and time.monotonic() < deadline:
        ctx.iteration(False)
        time.sleep(0.002)
    return until()


def test_setvisible_reaches_the_running_keyboard_over_a_real_bus(
        private_bus, tmp_path):
    from gi.repository import Gio, GLib
    service_conn, client_conn = private_bus
    got: list = []

    def _method(conn, sender, path, iface, method, params, invocation):
        got.append((method, params.unpack()))
        invocation.return_value(None)

    node = Gio.DBusNodeInfo.new_for_xml(_OSK_XML)
    link = osk.KeyboardLink(client_conn, proc=_proc(
        tmp_path, {os.getpid(): 'xx-keyboard'}))
    link.set_visible(True)          # nobody owns the name yet
    assert _spin(lambda: link._resolved)
    assert got == []

    reg = service_conn.register_object(
        osk.OSK_PATH, node.interfaces[0], _method, None, None)
    service_conn.call_sync(
        'org.freedesktop.DBus', '/org/freedesktop/DBus',
        'org.freedesktop.DBus', 'RequestName',
        GLib.Variant('(su)', (osk.OSK_NAME, 0)),
        GLib.VariantType.new('(u)'), Gio.DBusCallFlags.NONE, 2000, None)
    # NameOwnerChanged arrives, the owner PID (this process, posing as
    # xx-keyboard) resolves, and the next request goes through.
    assert _spin(lambda: link._owner is not None and link._resolved)
    link.set_visible(True)
    assert _spin(lambda: got)
    assert got == [('SetVisible', (True,))]
    service_conn.unregister_object(reg)
