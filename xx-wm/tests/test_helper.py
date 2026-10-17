"""Privileged helper tests (F_HELPER).

Sysfs, the cgroup tree, the mounts table and the state file are tmp
dirs/files; ``nft`` never executes (a recording fake stands in for
``subprocess.run`` and asserts the argv-list contract — no shell);
polkit and the bus are scripted fakes. Nothing here needs root, a
display, or the real helper installed.
"""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'helper'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import xx_wm_helper as helper
import helper_client

UID = 1000
APP = 'app@test.service'


@pytest.fixture()
def layout(tmp_path):
    sysfs = tmp_path / 'sys'
    bat = sysfs / 'class' / 'power_supply' / 'BAT0'
    bat.mkdir(parents=True)
    (bat / 'charge_control_end_threshold').write_text('100\n')
    (bat / 'charge_control_start_threshold').write_text('95\n')
    cg = tmp_path / 'cg'
    leaf = cg / 'user.slice' / ('user-%d.slice' % UID) / APP
    leaf.mkdir(parents=True)
    (leaf / 'cgroup.freeze').write_text('0\n')
    (cg / 'cgroup.freeze').write_text('0\n')
    mounts = tmp_path / 'mounts'
    mounts.write_text('cgroup2 %s cgroup2 rw,nosuid,nodev 0 0\n' % cg)
    return SimpleNamespace(
        sysfs=str(sysfs), cg=str(cg), leaf=str(leaf),
        mounts=str(mounts), state=str(tmp_path / 'state.json'),
    )


class FakeRun:
    """Recording stand-in for subprocess.run: argv lists only, no shell."""

    def __init__(self):
        self.cmds = []

    def __call__(self, argv):
        assert isinstance(argv, list), argv
        assert argv and all(isinstance(a, str) for a in argv), argv
        assert 'shell=True' not in repr(argv)
        self.cmds.append(list(argv))
        return SimpleNamespace(returncode=0, stdout='', stderr='')


def make_core(layout, run=None, **over):
    kw = dict(sysfs=layout.sysfs, cgroup_root=layout.cg,
              mounts_file=layout.mounts, state_file=layout.state,
              nft_bin='nft', run=run or FakeRun())
    kw.update(over)
    core = helper.HelperCore(**kw)
    core.nft_available = lambda: True
    return core


def app_path(layout):
    return '%s/user.slice/user-%d.slice/%s' % (layout.cg, UID, APP)


# ------------------------------------------------------------ capabilities

def test_capabilities_all_present(layout):
    caps = make_core(layout).get_capabilities()
    assert caps == {'charge_limit_supported': True, 'nftables': True,
                    'cgroup2': True, 'freezer': True}


def test_capabilities_empty_system(layout, tmp_path):
    core = helper.HelperCore(
        sysfs=str(tmp_path / 'nosys'), cgroup_root=str(tmp_path / 'nocg'),
        mounts_file=str(tmp_path / 'nomounts'),
        state_file=str(tmp_path / 's.json'), nft_bin='nft-definitely-missing')
    assert core.get_capabilities() == {
        'charge_limit_supported': False, 'nftables': False,
        'cgroup2': False, 'freezer': False}


def test_charge_limit_unsupported_without_battery_attr(tmp_path):
    (tmp_path / 'class' / 'power_supply' / 'AC').mkdir(parents=True)
    core = helper.HelperCore(sysfs=str(tmp_path))
    assert core.get_capabilities()['charge_limit_supported'] is False


# ------------------------------------------------------------ charge limit

def test_charge_limit_clamps_and_writes_start(layout):
    core = make_core(layout)
    assert core.set_charge_limit(30) == 50
    assert Path(layout.sysfs, 'class/power_supply/BAT0',
                'charge_control_end_threshold').read_text() == '50\n'
    assert Path(layout.sysfs, 'class/power_supply/BAT0',
                'charge_control_start_threshold').read_text() == '45\n'
    assert core.set_charge_limit(200) == 100
    assert json.loads(Path(layout.state).read_text())['charge_limit'] == 100


def test_charge_limit_missing_start_file_ok(layout):
    os.remove(Path(layout.sysfs, 'class/power_supply/BAT0',
                   'charge_control_start_threshold'))
    assert make_core(layout).set_charge_limit(80) == 80


def test_charge_limit_no_battery_fails(layout, tmp_path):
    core = make_core(layout, sysfs=str(tmp_path))
    with pytest.raises(helper.HelperError) as exc:
        core.set_charge_limit(80)
    assert exc.value.dbus_name == helper.ERROR_FAILED


# ---------------------------------------------------------- path validation

@pytest.mark.parametrize('bad', [
    '../escape', 'relative/path', '', '/etc/passwd',
    '/sys/fs/cgroup/user.slice/user-1000.slice/../other.slice',
    '/sys/fs/cgroup/user.slice/user-1001.slice/app@test.service',
    '/sys/fs/cgroup/system.slice/app@test.service',
    '/sys/fs/cgroup/user.slice/user-1000.slice/',
    '/sys/fs/cgroup//user.slice/user-1000.slice/app@test.service',
    '/sys/fs/cgroup/user.slice/user-1000.slice/app@test.service\x00x',
])
def test_app_cgroup_rejects_bad_paths(bad):
    core = helper.HelperCore()
    with pytest.raises(helper.HelperError) as exc:
        core.validate_app_cgroup(bad, 1000)
    assert exc.value.dbus_name == helper.ERROR_NOT_AUTHORIZED


def test_app_cgroup_accepts_own_slice(layout):
    core = make_core(layout)
    assert core.validate_app_cgroup(app_path(layout), UID) == app_path(layout)
    prefix = core.user_slice_prefix(UID)
    assert core.validate_app_cgroup(prefix, UID) == prefix


def test_app_cgroup_rejects_symlink_escape(layout, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    link = Path(app_path(layout) + '-link')
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('symlinks unavailable')
    with pytest.raises(helper.HelperError) as exc:
        make_core(layout).validate_app_cgroup(str(link), UID)
    assert exc.value.dbus_name == helper.ERROR_NOT_AUTHORIZED


# ------------------------------------------------------------- app network

def _block_rule(layout, leaf=APP):
    rel = 'user.slice/user-%d.slice/%s' % (UID, leaf)
    return ['nft', 'add', 'rule', 'inet', 'xxwm', 'output',
            'socket', 'cgroupv2', 'level', '3', '"%s"' % rel, 'drop']


def test_set_app_network_block_and_unblock(layout):
    run = FakeRun()
    core = make_core(layout, run)
    assert core.set_app_network(app_path(layout), False, UID) is False
    assert run.cmds[:4] == [
        ['nft', 'add', 'table', 'inet', 'xxwm'],
        ['nft', 'add', 'chain', 'inet', 'xxwm', 'output',
         '{', 'type', 'filter', 'hook', 'output', 'priority', '0',
         ';', 'policy', 'accept', ';', '}'],
        ['nft', 'flush', 'chain', 'inet', 'xxwm', 'output'],
        _block_rule(layout),
    ]
    assert json.loads(Path(layout.state).read_text())['blocked'] == [
        app_path(layout)]
    n = len(run.cmds)
    assert core.set_app_network(app_path(layout), True, UID) is True
    assert run.cmds[n:] == [
        ['nft', 'add', 'table', 'inet', 'xxwm'],
        ['nft', 'add', 'chain', 'inet', 'xxwm', 'output',
         '{', 'type', 'filter', 'hook', 'output', 'priority', '0',
         ';', 'policy', 'accept', ';', '}'],
        ['nft', 'flush', 'chain', 'inet', 'xxwm', 'output'],
    ]
    assert json.loads(Path(layout.state).read_text())['blocked'] == []


def test_set_app_network_rejects_other_uid(layout):
    with pytest.raises(helper.HelperError) as exc:
        make_core(layout).set_app_network(app_path(layout), False, UID + 1)
    assert exc.value.dbus_name == helper.ERROR_NOT_AUTHORIZED


def test_nft_failure_surfaces_as_failed(layout):
    def failing(argv):
        return SimpleNamespace(returncode=1, stdout='',
                               stderr='Error: boom\n')

    with pytest.raises(helper.HelperError) as exc:
        make_core(layout, failing).set_app_network(
            app_path(layout), False, UID)
    assert exc.value.dbus_name == helper.ERROR_FAILED
    assert 'boom' in str(exc.value)


def test_nft_level_from_path_depth(layout):
    core = make_core(layout)
    assert core.cgroup_nft_level(app_path(layout)) == 3
    assert core.cgroup_nft_match(app_path(layout)) == (
        '"user.slice/user-%d.slice/%s"' % (UID, APP))


# ------------------------------------------------------------------ freeze

def test_freeze_and_thaw(layout):
    core = make_core(layout)
    assert core.freeze_cgroup(app_path(layout), True, UID) is True
    assert Path(app_path(layout), 'cgroup.freeze').read_text() == '1\n'
    assert core.freeze_cgroup(app_path(layout), False, UID) is False
    assert Path(app_path(layout), 'cgroup.freeze').read_text() == '0\n'


def test_freeze_rejects_other_uid(layout):
    with pytest.raises(helper.HelperError) as exc:
        make_core(layout).freeze_cgroup(app_path(layout), True, UID + 1)
    assert exc.value.dbus_name == helper.ERROR_NOT_AUTHORIZED


def test_freeze_without_cgroup2_fails(layout, tmp_path):
    (tmp_path / 'mounts').write_text('tmpfs / tmpfs rw 0 0\n')
    core = make_core(layout, mounts_file=str(tmp_path / 'mounts'))
    with pytest.raises(helper.HelperError) as exc:
        core.freeze_cgroup(app_path(layout), True, UID)
    assert exc.value.dbus_name == helper.ERROR_FAILED


# --------------------------------------------------- persistence / re-apply

def test_state_corrupt_degrades_to_defaults(layout):
    Path(layout.state).write_text('{not json')
    assert make_core(layout).load_state() == {
        'charge_limit': None, 'blocked': [], 'power_profile': None}


def test_apply_saved_reapplies_charge_and_blocks(layout):
    run = FakeRun()
    first = make_core(layout, run)
    first.set_charge_limit(75)
    first.set_app_network(app_path(layout), False, UID)
    saved = json.loads(Path(layout.state).read_text())
    assert saved == {'blocked': [app_path(layout)], 'charge_limit': 75,
                     'power_profile': None}

    (Path(layout.sysfs, 'class/power_supply/BAT0',
          'charge_control_end_threshold').write_text('100\n'))
    run2 = FakeRun()
    second = make_core(layout, run2)
    applied = second.apply_saved()
    assert applied['errors'] == []
    assert applied['charge_limit'] == 75
    assert applied['blocked'] == [app_path(layout)]
    assert Path(layout.sysfs, 'class/power_supply/BAT0',
                'charge_control_end_threshold').read_text() == '75\n'
    assert run2.cmds[-1] == _block_rule(layout)


def test_apply_saved_collects_errors_per_leg(layout):
    Path(layout.state).write_text(json.dumps(
        {'charge_limit': 80, 'blocked': [app_path(layout)]}))
    (Path(layout.sysfs, 'class/power_supply/BAT0',
          'charge_control_end_threshold').write_text('100\n'))

    def failing(argv):
        return SimpleNamespace(returncode=1, stdout='', stderr='no nft\n')

    core = make_core(layout, failing)
    core.nft_available = lambda: False
    applied = core.apply_saved()
    assert applied['charge_limit'] == 80
    assert applied['blocked'] == []
    assert applied['errors'] == ['nftables not available']


# ------------------------------------------------------ service/auth dispatch

class FakeInvocation:
    def __init__(self):
        self.value = None
        self.error = None

    def return_value(self, value):
        self.value = value

    def return_dbus_error(self, name, message):
        self.error = (name, message)


def allow_all(conn, sender, action, interactive, callback):
    allow_all.seen.append((action, interactive))
    callback(True, UID, None)


def deny_all(conn, sender, action, interactive, callback):
    deny_all.seen.append((action, interactive))
    callback(False, UID, None)


allow_all.seen = []
deny_all.seen = []


def test_service_authorizes_each_method_with_own_action(layout):
    allow_all.seen.clear()
    run = FakeRun()
    service = helper.HelperService(connection=object(), core=make_core(
        layout, run), authorize=allow_all)
    for method, action, args in [
        ('SetChargeLimit', helper.ACTION_CHARGE, (80,)),
        ('SetAppNetwork', helper.ACTION_NETWORK, (app_path(layout), False)),
        ('FreezeCgroup', helper.ACTION_FREEZE, (app_path(layout), True)),
    ]:
        invocation = FakeInvocation()
        service._on_method_call(service.connection, ':1.7', '/p', 'i',
                                method, SimpleNamespace(unpack=lambda: args),
                                invocation)
        assert invocation.error is None, (method, invocation.error)
        assert invocation.value is not None
    assert allow_all.seen == [(helper.ACTION_CHARGE, False),
                              (helper.ACTION_NETWORK, False),
                              (helper.ACTION_FREEZE, False)]


def test_service_denied_maps_to_not_authorized(layout):
    service = helper.HelperService(connection=object(),
                                   core=make_core(layout),
                                   authorize=deny_all)
    invocation = FakeInvocation()
    service._on_method_call(service.connection, ':1.7', '/p', 'i',
                            'SetChargeLimit',
                            SimpleNamespace(unpack=lambda: (80,)), invocation)
    assert invocation.error is not None
    assert invocation.error[0] == helper.ERROR_NOT_AUTHORIZED


def test_service_uid_scoping_uses_sender_uid_not_path(layout):
    seen = {}
    core = make_core(layout)
    real_set_app_network = core.set_app_network

    def spy(path, allowed, uid):
        seen['uid'] = uid
        return real_set_app_network(path, allowed, uid)

    core.set_app_network = spy

    def authorize_as_other(conn, sender, action, interactive, callback):
        callback(True, UID + 1, None)

    service = helper.HelperService(connection=object(), core=core,
                                   authorize=authorize_as_other)
    invocation = FakeInvocation()
    service._on_method_call(service.connection, ':1.9', '/p', 'i',
                            'SetAppNetwork', SimpleNamespace(
                                unpack=lambda: (app_path(layout), False)),
                            invocation)
    assert seen['uid'] == UID + 1
    assert invocation.error is not None
    assert invocation.error[0] == helper.ERROR_NOT_AUTHORIZED


def test_service_capabilities_need_no_auth(layout):
    def explosive(conn, sender, action, interactive, callback):
        raise AssertionError('GetCapabilities must not authorize')

    service = helper.HelperService(connection=None, core=make_core(layout),
                                   authorize=explosive)
    invocation = FakeInvocation()
    service._on_method_call(None, ':1.7', '/p', 'i', 'GetCapabilities',
                            None, invocation)
    assert invocation.error is None
    assert set(invocation.value.unpack()[0]) == {
        'charge_limit_supported', 'nftables', 'cgroup2', 'freezer'}


def test_service_unknown_method_is_invalid_args(layout):
    service = helper.HelperService(connection=object(),
                                   core=make_core(layout),
                                   authorize=allow_all)
    invocation = FakeInvocation()
    service._on_method_call(service.connection, ':1.7', '/p', 'i', 'Reboot',
                            None, invocation)
    assert invocation.error[0] == helper.ERROR_INVALID_ARGS


def test_service_idle_expiry():
    clock = SimpleNamespace(now=1000.0)
    service = helper.HelperService(authorize=allow_all,
                                   now=lambda: clock.now)
    assert service.idle_expired() is False
    clock.now += helper.IDLE_EXIT_SECONDS - 1
    assert service.idle_expired() is False
    service.touch()
    clock.now += helper.IDLE_EXIT_SECONDS - 1
    assert service.idle_expired() is False
    clock.now += 1
    assert service.idle_expired() is True


# ------------------------------------------- polkit/uid chain against gi

class FakeResult:
    def __init__(self, payload):
        self.payload = payload

    def unpack(self):
        return (self.payload,)


class FakeAuthResult:
    """A real ``CheckAuthorization`` reply: ONE out-arg, the struct
    ``(bba{ss})`` = (is_authorized, is_challenge, details), so
    ``unpack()`` is ``((bool, bool, {...}),)``. Built with a real
    ``GLib.Variant`` round-trip — a bare-bool fake hid the
    ``bool(tuple)``-is-always-True authorization bypass, so this fake
    must never be more forgiving than the bus."""

    def __init__(self, authorized, challenge=False, details=None):
        self.authorized = authorized
        self.challenge = challenge
        self.details = {} if details is None else details

    def unpack(self):
        from gi.repository import GLib
        return GLib.Variant(
            '((bba{ss}))',
            ((bool(self.authorized), bool(self.challenge),
              dict(self.details),),),
        ).unpack()


class FakeAuthFailure:
    """A failed ``CheckAuthorization`` call: ``call_finish`` raises, as a
    real D-Bus error does."""

    def __init__(self, error):
        self.error = error


class ScriptConn:
    """Fake system bus speaking the real Gio callback contract:
    ``call(..., cancellable, callback)`` with a two-arg callback."""

    #: CheckAuthorization flags per action: only auth_admin_keep actions
    #: (upgrade, removal) pass AllowUserInteraction (1).
    EXPECTED_FLAGS = {}

    def __init__(self, uid, authorized=True, challenge=False,
                 details=None, auth_error=None):
        self.uid = uid
        self.authorized = authorized
        self.challenge = challenge
        self.details = {} if details is None else details
        self.auth_error = auth_error
        self.calls = []

    def call(self, bus_name, path, iface, method, params, reply_type,
             flags, timeout, cancellable, callback):
        self.calls.append((bus_name, path, iface, method))
        if method == 'GetConnectionUnixUser':
            assert (bus_name, path, iface) == (
                helper.DBUS_BUS, helper.DBUS_PATH, helper.DBUS_IFACE)
            callback(self, FakeResult(self.uid))
        elif method == 'CheckAuthorization':
            assert params.get_type_string() == '((sa{sv})sa{ss}u)'
            subject, action, details, flags = params.unpack()
            assert subject[0] == 'system-bus-name'
            assert subject[1]['name'] == ':1.7'
            assert action in (helper.ACTION_CHARGE, helper.ACTION_NETWORK,
                              helper.ACTION_FREEZE, helper.ACTION_CLEAN,
                              helper.ACTION_READLOG, helper.ACTION_REFRESH,
                              helper.ACTION_UPGRADE, helper.ACTION_REMOVE,
                              helper.ACTION_SERVICE,
                              helper.ACTION_SERVICE_REMOTE,
                              helper.ACTION_RFKILL, helper.ACTION_FW_OPEN,
                              helper.ACTION_FW_RESTRICT)
            assert details == {}
            assert flags == self.EXPECTED_FLAGS.get(action, 0), action
            if self.auth_error is not None:
                callback(self, FakeAuthFailure(self.auth_error))
            else:
                callback(self, FakeAuthResult(
                    self.authorized, self.challenge, self.details))
        else:
            raise AssertionError(method)

    def call_finish(self, result):
        if isinstance(result, FakeAuthFailure):
            raise result.error
        return result


def test_polkit_chain_subject_action_and_uid():
    pytest.importorskip('gi')
    conn = ScriptConn(UID, True)
    done = []
    helper.check_polkit_authorization(
        conn, ':1.7', helper.ACTION_CHARGE,
        lambda ok, uid, err: done.append((ok, uid, err)))
    assert done == [(True, UID, None)]
    assert [c[3] for c in conn.calls] == [
        'GetConnectionUnixUser', 'CheckAuthorization']


def test_polkit_denial_propagates():
    pytest.importorskip('gi')
    conn = ScriptConn(UID, False)
    done = []
    helper.check_polkit_authorization(
        conn, ':1.7', helper.ACTION_FREEZE,
        lambda ok, uid, err: done.append((ok, uid, err)))
    assert done == [(False, UID, None)]


def test_polkit_denial_with_challenge_and_details_still_denies():
    pytest.importorskip('gi')
    conn = ScriptConn(UID, False, challenge=True,
                      details={'polkit.action.id': helper.ACTION_CHARGE})
    done = []
    helper.check_polkit_authorization(
        conn, ':1.7', helper.ACTION_CHARGE,
        lambda ok, uid, err: done.append((ok, uid, err)))
    assert done == [(False, UID, None)]


def test_polkit_error_denies_without_authorizing():
    pytest.importorskip('gi')
    conn = ScriptConn(
        UID, True,
        auth_error=Exception(
            'GDBus.Error:org.freedesktop.PolicyKit1.Error.Failed: boom'))
    done = []
    helper.check_polkit_authorization(
        conn, ':1.7', helper.ACTION_NETWORK,
        lambda ok, uid, err: done.append((ok, uid, err)))
    assert len(done) == 1
    assert done[0][0] is False
    assert done[0][1] == UID
    assert 'boom' in done[0][2]


def test_service_auth_error_maps_to_not_authorized(layout):
    def failing_auth(conn, sender, action, interactive, callback):
        callback(False, UID, 'polkit exploded')

    service = helper.HelperService(connection=object(),
                                   core=make_core(layout),
                                   authorize=failing_auth)
    invocation = FakeInvocation()
    service._on_method_call(service.connection, ':1.7', '/p', 'i',
                            'SetChargeLimit',
                            SimpleNamespace(unpack=lambda: (80,)), invocation)
    assert invocation.error is not None
    assert invocation.error[0] == helper.ERROR_NOT_AUTHORIZED
    assert 'polkit exploded' in invocation.error[1]


def test_service_unknown_method_never_reaches_core_or_auth(layout):
    def explosive(conn, sender, action, interactive, callback):
        raise AssertionError('unknown method must not authorize')

    run = FakeRun()
    service = helper.HelperService(connection=object(),
                                   core=make_core(layout, run),
                                   authorize=explosive)
    invocation = FakeInvocation()
    service._on_method_call(service.connection, ':1.7', '/p', 'i', 'Reboot',
                            None, invocation)
    assert invocation.error is not None
    assert invocation.error[0] == helper.ERROR_INVALID_ARGS
    assert run.cmds == []
    assert not os.path.exists(layout.state)


# ------------------------------------------------------------------ client

class FakeVariant:
    def __init__(self, signature, value):
        self.signature = signature
        self.value = value

    def unpack(self):
        return self.value


class FakeClientReply:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error

    def unpack(self):
        if self.error is not None:
            raise self.error
        return (self.payload,)


class FakeClientConn:
    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    def call(self, bus_name, path, iface, method, params, reply_type,
             flags, timeout, cancellable, callback):
        self.calls.append((bus_name, path, iface, method))
        callback(self, 'token')

    def call_finish(self, _token):
        if self.error is not None:
            raise self.error
        return self.reply


class FakeGio:
    class BusType:
        SYSTEM = 'system'

    class DBusCallFlags:
        NONE = 0

    Variant = FakeVariant
    conn = None
    bus_error = None

    @classmethod
    def bus_get(cls, _bus_type, _cancellable, callback):
        if cls.bus_error is not None:
            raise cls.bus_error
        callback(None, cls.conn)

    @classmethod
    def bus_get_finish(cls, result):
        return result


class FakeGLib:
    Variant = FakeVariant


@pytest.fixture()
def fake_gio(monkeypatch):
    monkeypatch.setattr(helper_client, '_gio', lambda: (FakeGio, FakeGLib))
    monkeypatch.setattr(helper_client, '_capabilities_cache', None)
    FakeGio.conn = None
    FakeGio.bus_error = None
    helper_client.invalidate_capabilities()
    return FakeGio


def test_client_missing_helper_reports_not_installed(fake_gio):
    FakeGio.bus_error = Exception(
        'GDBus.Error:org.freedesktop.DBus.Error.ServiceUnknown: '
        'The name io.piercingxx.XXWM.Helper1 was not provided')
    done = []
    helper_client.get_capabilities(lambda ok, res: done.append((ok, res)))
    assert done == [(False, helper_client.HELPER_NOT_INSTALLED)]


def test_client_capabilities_cached(fake_gio):
    FakeGio.conn = FakeClientConn(FakeClientReply(
        {'charge_limit_supported': True, 'nftables': False,
         'cgroup2': True, 'freezer': False}))
    done = []
    helper_client.get_capabilities(lambda ok, res: done.append((ok, res)))
    helper_client.get_capabilities(lambda ok, res: done.append((ok, res)))
    assert done[0] == done[1]
    assert done[0][0] is True
    assert len(FakeGio.conn.calls) == 1
    helper_client.get_capabilities(
        lambda ok, res: done.append((ok, res)), refresh=True)
    assert len(FakeGio.conn.calls) == 2


def test_client_mutating_calls_pass_params(fake_gio):
    FakeGio.conn = FakeClientConn(FakeClientReply(None))
    done = []
    helper_client.set_charge_limit(80, lambda ok, res: done.append((ok, res)))
    helper_client.set_app_network('/x', False,
                                  lambda ok, res: done.append((ok, res)))
    helper_client.freeze_cgroup('/x', True,
                                lambda ok, res: done.append((ok, res)))
    assert done == [(True, None)] * 3
    assert [c[3] for c in FakeGio.conn.calls] == [
        'SetChargeLimit', 'SetAppNetwork', 'FreezeCgroup']


def test_client_helper_error_propagates(fake_gio):
    FakeGio.conn = FakeClientConn(error=Exception(
        'GDBus.Error:io.piercingxx.XXWM.Helper1.Error.NotAuthorized: nope'))
    done = []
    helper_client.set_charge_limit(80, lambda ok, res: done.append((ok, res)))
    assert done[0][0] is False
    assert 'NotAuthorized' in done[0][1]


# ------------------------------------------------------- shipped data files

DATA = Path(__file__).parent.parent / 'launcher' / 'data'


def test_shipped_policy_actions_and_defaults():
    text = (DATA / 'polkit-1' / 'actions'
            / 'io.piercingxx.XXWM.Helper.policy').read_text()
    for action in ('io.piercingxx.XXWM.Helper1.set-charge-limit',
                   'io.piercingxx.XXWM.Helper1.app-network',
                   'io.piercingxx.XXWM.Helper1.freeze',
                   'io.piercingxx.XXWM.Helper1.clean-system',
                   'io.piercingxx.XXWM.Helper1.read-log',
                   'io.piercingxx.XXWM.Helper1.refresh-packages',
                   'io.piercingxx.XXWM.Helper1.service-manage',
                   'io.piercingxx.XXWM.Helper1.rfkill',
                   'io.piercingxx.XXWM.Helper1.power-profile',
                   'io.piercingxx.XXWM.Helper1.firewall-restrict'):
        assert ('action id="%s"' % action) in text
    for action in ('io.piercingxx.XXWM.Helper1.upgrade-system',
                   'io.piercingxx.XXWM.Helper1.remove-package',
                   'io.piercingxx.XXWM.Helper1.service-remote',
                   'io.piercingxx.XXWM.Helper1.firewall-open'):
        assert ('action id="%s"' % action) in text
    assert text.count('<allow_active>yes</allow_active>') == 10
    assert text.count('<allow_active>auth_admin_keep</allow_active>') == 4
    assert text.count('<allow_inactive>no</allow_inactive>') == 14
    assert text.count('<allow_any>no</allow_any>') == 14


def test_shipped_bus_policy_allows_default_callers_without_at_console():
    text = (DATA / 'dbus-1' / 'system.d'
            / 'io.piercingxx.XXWM.Helper1.conf').read_text()
    assert 'at_console' not in text
    assert '<policy user="root">' in text
    assert '<allow own="io.piercingxx.XXWM.Helper1"/>' in text
    assert text.count('<allow own=') == 1
    assert '<policy context="default">' in text
    for iface in ('io.piercingxx.XXWM.Helper1',
                  'org.freedesktop.DBus.Introspectable',
                  'org.freedesktop.DBus.Peer',
                  'org.freedesktop.DBus.Properties'):
        assert 'send_interface="%s"' % iface in text
    assert 'send_destination="io.piercingxx.XXWM.Helper1"' in text


def test_shipped_activation_runs_helper_as_root():
    text = (DATA / 'dbus-1' / 'system-services'
            / 'io.piercingxx.XXWM.Helper1.service').read_text()
    assert 'Name=io.piercingxx.XXWM.Helper1' in text
    assert 'Exec=@libexecdir@/xx-wm-helper' in text
    assert 'User=root' in text


def test_shipped_boot_units_reapply_saved():
    systemd = (DATA / 'systemd' / 'xx-wm-helper-boot.service').read_text()
    assert 'Type=oneshot' in systemd
    assert 'ExecStart=@libexecdir@/xx-wm-helper --apply-saved' in systemd
    openrc = (DATA / 'openrc' / 'xx-wm-helper-boot').read_text()
    assert openrc.startswith('#!/sbin/openrc-run')
    assert '@libexecdir@/xx-wm-helper' in openrc
    assert '--apply-saved' in openrc
