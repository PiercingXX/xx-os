"""Helper package jobs: UpgradeSystem / RemovePackage /
RefreshPackageIndex / CleanSystem never get a kill timeout, reply with a
job id and report through JobFinished.

The package manager never runs: ``subprocess`` is replaced by fakes that
simulate a job running past the old 30 s timeout. The client side runs
against fakes speaking the real Gio callback contract, and one test
drives the real helper object and the real client over a private
dbus-daemon (never the host's buses).
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'helper'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import xx_wm_helper as helper  # noqa: E402
import helper_client  # noqa: E402

UID = 1000
SENDER = ':1.42'
NO_TIMEOUT = 2 ** 31 - 1


def _which(present):
    return lambda binary: '/usr/bin/' + binary if binary in present else None


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Invocation:
    def __init__(self):
        self.value = None
        self.error = None

    def return_value(self, value):
        self.value = value

    def return_dbus_error(self, name, message):
        self.error = (name, message)


class SignalConn:
    def __init__(self):
        self.signals = []

    def emit_signal(self, destination, path, iface, name, params):
        self.signals.append((destination, path, iface, name, params))


class HeldSpawn:
    """Starts nothing; the test decides when (and how) the job exits."""

    def __init__(self):
        self.jobs = []

    def __call__(self, argv, on_exit):
        self.jobs.append((list(argv), on_exit))


def allow(conn, sender, action, interactive, callback):
    callback(True, UID, None)


def _call(service, method, args=()):
    invocation = Invocation()
    service._on_method_call(service.connection, SENDER, '/p', 'i', method,
                            SimpleNamespace(unpack=lambda: args), invocation)
    return invocation


def _service(spawn, clock=None, authorize=allow, run=None):
    core = helper.HelperCore(spawn=spawn,
                             **({'run': run} if run is not None else {}))
    return helper.HelperService(connection=SignalConn(), core=core,
                                authorize=authorize,
                                now=clock or Clock())


@pytest.fixture()
def apk(monkeypatch):
    monkeypatch.setattr(helper.shutil, 'which', _which({'apk'}))


def _pump_until(predicate, seconds=5.0):
    from gi.repository import GLib
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        ctx.iteration(False)
        time.sleep(0.005)
    return predicate()


# ------------------------------------------------- no kill timeout on jobs

class SlowPackageManager:
    """Stands in for subprocess.run/Popen: the job takes 45 s of
    simulated time, so any timeout under that kills it, exactly as
    subprocess does (TimeoutExpired after killing the child)."""

    DURATION = 45.0

    def __init__(self):
        self.killed = []
        self.started = []

    def run(self, argv, timeout=None, **_kw):
        self.started.append(list(argv))
        if timeout is not None and timeout < self.DURATION:
            self.killed.append(list(argv))
            raise subprocess.TimeoutExpired(argv, timeout)
        return subprocess.CompletedProcess(argv, 0, stdout='OK: 3 packages\n',
                                           stderr='')

    def popen(self, argv, **_kw):
        manager = self
        manager.started.append(list(argv))

        class Proc:
            returncode = None

            def communicate(self, input=None, timeout=None):
                if timeout is not None and timeout < manager.DURATION:
                    manager.killed.append(list(argv))
                    raise subprocess.TimeoutExpired(argv, timeout)
                self.returncode = 0
                return (b'(1/3) Upgrading foo\nOK: 3 packages\n', None)

            def kill(self):
                manager.killed.append(list(argv))

        return Proc()


class TestNoKillTimeout:
    @pytest.mark.parametrize('method,args,argv', [
        ('UpgradeSystem', (), ['apk', 'upgrade']),
        ('RemovePackage', ('vim',), ['apk', 'del', 'vim']),
        ('RefreshPackageIndex', (), ['apk', 'update']),
        ('CleanSystem', (['apt-clean'],), ['apt-get', 'clean']),
    ])
    def test_job_past_30s_is_not_killed(self, monkeypatch, method,
                                        args, argv):
        monkeypatch.setattr(helper.shutil, 'which',
                            _which({'apk', 'apt-get'}))
        pytest.importorskip('gi')
        slow = SlowPackageManager()
        monkeypatch.setattr(helper.subprocess, 'run', slow.run)
        monkeypatch.setattr(helper.subprocess, 'Popen', slow.popen)
        # Production defaults: no injected run/spawn.
        service = helper.HelperService(connection=SignalConn(),
                                       core=helper.HelperCore(),
                                       authorize=allow)
        invocation = _call(service, method, args)
        assert invocation.error is None, invocation.error
        (job_id,) = invocation.value.unpack()
        assert _pump_until(lambda: service.connection.signals)
        assert slow.started == [argv]
        assert slow.killed == []
        (dest, path, iface, name, params) = service.connection.signals[0]
        assert (dest, path, iface, name) == (
            SENDER, helper.OBJECT_PATH, helper.INTERFACE, 'JobFinished')
        assert params.get_type_string() == '(sis)'
        got_id, rc, tail = params.unpack()
        assert (got_id, rc) == (job_id, 0)
        if method == 'CleanSystem':
            assert tail == 'apt-clean'
        else:
            assert 'OK: 3 packages' in tail

    def test_default_spawn_never_passes_a_timeout(self, monkeypatch):
        pytest.importorskip('gi')
        slow = SlowPackageManager()
        monkeypatch.setattr(helper.subprocess, 'Popen', slow.popen)
        done = []
        helper._default_spawn(['apk', 'upgrade'],
                              lambda rc, out: done.append((rc, out)))
        assert _pump_until(lambda: done)
        assert done[0][0] == 0
        assert slow.killed == []

    def test_unstartable_job_reports_rc(self, monkeypatch):
        pytest.importorskip('gi')

        def broken(argv, **_kw):
            raise FileNotFoundError(2, 'No such file', argv[0])

        monkeypatch.setattr(helper.subprocess, 'Popen', broken)
        done = []
        helper._default_spawn(['apk', 'upgrade'],
                              lambda rc, out: done.append((rc, out)))
        assert _pump_until(lambda: done)
        assert done[0][0] == -1
        assert 'failed to start' in done[0][1]


# ------------------------------------------------------ JobFinished signal

class TestJobFinished:
    def test_reply_is_a_job_id_and_signal_carries_rc(self, apk):
        pytest.importorskip('gi')
        spawn = HeldSpawn()
        clock = Clock()
        service = _service(spawn, clock)
        invocation = _call(service, 'UpgradeSystem')
        (job_id,) = invocation.value.unpack()
        assert invocation.value.get_type_string() == '(s)'
        assert job_id and service.connection.signals == []
        clock.now += 31 * 60
        argv, on_exit = spawn.jobs[0]
        assert argv == ['apk', 'upgrade']
        on_exit(3, 'ERROR: unable to lock database\n')
        (dest, _path, _iface, name, params) = service.connection.signals[0]
        assert dest == SENDER and name == 'JobFinished'
        assert params.unpack() == (job_id, 3,
                                   'ERROR: unable to lock database')
        assert service.jobs == {}

    def test_validation_errors_reply_without_a_job(self, monkeypatch):
        monkeypatch.setattr(helper.shutil, 'which', _which({'apk'}))
        spawn = HeldSpawn()
        service = _service(spawn)
        invocation = _call(service, 'RemovePackage', ('musl',))
        assert invocation.error[0] == helper.ERROR_NOT_AUTHORIZED
        assert spawn.jobs == [] and service.jobs == {}

    def test_second_job_refused_while_one_runs(self, apk):
        pytest.importorskip('gi')
        spawn = HeldSpawn()
        service = _service(spawn)
        assert _call(service, 'UpgradeSystem').error is None
        second = _call(service, 'RemovePackage', ('vim',))
        assert second.error[0] == helper.ERROR_FAILED
        assert len(spawn.jobs) == 1

    def test_spawn_raising_still_finishes_the_job(self, apk):
        pytest.importorskip('gi')

        def spawn(argv, on_exit):
            raise RuntimeError('no threads')

        service = _service(spawn)
        invocation = _call(service, 'UpgradeSystem')
        (job_id,) = invocation.value.unpack()
        params = service.connection.signals[0][4]
        assert params.unpack()[:2] == (job_id, -1)
        assert service.jobs == {}

    def test_tail_is_capped(self):
        out = ''.join('line %d\n' % n for n in range(500))
        tail = helper.job_tail(out)
        assert tail.splitlines()[-1] == 'line 499'
        assert len(tail.splitlines()) == helper.JOB_TAIL_LINES
        assert len(helper.job_tail('x' * 10_000)) == helper.JOB_TAIL_CHARS

    def test_introspection_declares_job_api(self):
        Gio = pytest.importorskip('gi.repository.Gio')
        node = Gio.DBusNodeInfo.new_for_xml(helper.INTROSPECT_XML)
        iface = node.interfaces[0]
        signal = iface.lookup_signal('JobFinished')
        assert [a.signature for a in signal.args] == ['s', 'i', 's']
        for method in ('UpgradeSystem', 'RemovePackage',
                       'RefreshPackageIndex', 'CleanSystem'):
            out = iface.lookup_method(method).out_args
            assert [a.signature for a in out] == ['s']


# ------------------------------------------------------- CleanSystem steps

class TestCleanJob:
    @pytest.fixture()
    def tools(self, monkeypatch):
        monkeypatch.setattr(helper.shutil, 'which',
                            _which({'paccache', 'apt-get'}))

    def test_steps_run_one_after_another(self, tools):
        pytest.importorskip('gi')
        spawn = HeldSpawn()
        service = _service(spawn)
        invocation = _call(service, 'CleanSystem',
                           (['paccache-keep', 'apt-clean'],))
        (job_id,) = invocation.value.unpack()
        assert [argv for argv, _cb in spawn.jobs] == [['paccache', '-rk2']]
        spawn.jobs[0][1](0, 'removed 3 packages\n')
        assert [argv for argv, _cb in spawn.jobs][1] == ['apt-get', 'clean']
        assert service.connection.signals == []
        spawn.jobs[1][1](0, '')
        params = service.connection.signals[0][4]
        assert params.unpack() == (job_id, 0, 'paccache-keep\napt-clean')
        assert service.jobs == {}

    def test_missing_tool_is_skipped(self, tools):
        pytest.importorskip('gi')
        spawn = HeldSpawn()
        service = _service(spawn)
        (job_id,) = _call(service, 'CleanSystem',
                          (['paccache-keep', 'apt-clean'],)).value.unpack()
        spawn.jobs[0][1](127, 'paccache: not found\n')
        spawn.jobs[1][1](0, '')
        params = service.connection.signals[0][4]
        assert params.unpack() == (job_id, 0, 'apt-clean')

    def test_a_failing_step_ends_the_job(self, tools):
        pytest.importorskip('gi')
        spawn = HeldSpawn()
        service = _service(spawn)
        (job_id,) = _call(service, 'CleanSystem',
                          (['paccache-keep', 'apt-clean'],)).value.unpack()
        spawn.jobs[0][1](1, 'working\nerror: no space left\n')
        assert len(spawn.jobs) == 1
        params = service.connection.signals[0][4]
        assert params.unpack() == (
            job_id, 1, 'clean paccache-keep: error: no space left')
        assert service.jobs == {}

    def test_nothing_to_run_finishes_at_once(self, monkeypatch):
        pytest.importorskip('gi')
        monkeypatch.setattr(helper.shutil, 'which', _which(set()))
        spawn = HeldSpawn()
        service = _service(spawn)
        (job_id,) = _call(service, 'CleanSystem',
                          (['apt-clean'],)).value.unpack()
        assert spawn.jobs == []
        params = service.connection.signals[0][4]
        assert params.unpack() == (job_id, 0, '')

    def test_unknown_category_replies_without_a_job(self, tools):
        spawn = HeldSpawn()
        service = _service(spawn)
        invocation = _call(service, 'CleanSystem', (['/bin/rm'],))
        assert invocation.error[0] == helper.ERROR_INVALID_ARGS
        assert spawn.jobs == [] and service.jobs == {}


# --------------------------------------------------------------- idle exit

class TestIdleHeld:
    def test_no_idle_exit_while_a_job_runs(self, apk):
        pytest.importorskip('gi')
        spawn = HeldSpawn()
        clock = Clock()
        service = _service(spawn, clock)
        _call(service, 'UpgradeSystem')
        clock.now += 30 * 60
        assert service.idle_expired() is False
        spawn.jobs[0][1](0, 'OK\n')
        assert service.idle_expired() is False
        clock.now += helper.IDLE_EXIT_SECONDS
        assert service.idle_expired() is True

    def test_no_idle_exit_while_the_password_dialog_is_open(self, apk):
        pytest.importorskip('gi')
        pending = []

        def slow_auth(conn, sender, action, interactive, callback):
            pending.append(callback)

        clock = Clock()
        service = _service(HeldSpawn(), clock, authorize=slow_auth)
        invocation = _call(service, 'UpgradeSystem')
        clock.now += 5 * 60
        assert service.idle_expired() is False
        pending[0](False, UID, None)
        assert invocation.error[0] == helper.ERROR_NOT_AUTHORIZED
        assert service.idle_expired() is False
        clock.now += helper.IDLE_EXIT_SECONDS
        assert service.idle_expired() is True

    def test_authorize_raising_releases_the_hold(self):
        def broken(conn, sender, action, interactive, callback):
            raise RuntimeError('bus gone')

        clock = Clock()
        service = _service(HeldSpawn(), clock, authorize=broken)
        invocation = _call(service, 'SetChargeLimit', (80,))
        assert invocation.error[0] == helper.ERROR_FAILED
        clock.now += helper.IDLE_EXIT_SECONDS
        assert service.idle_expired() is True


# ------------------------------------------------- polkit check timeout

class PolkitConn:
    def __init__(self):
        self.timeouts = {}

    def call(self, bus_name, path, iface, method, params, reply_type,
             flags, timeout, cancellable, callback):
        from gi.repository import GLib
        if method == 'GetConnectionUnixUser':
            callback(self, GLib.Variant('(u)', (UID,)))
            return
        _subject, action, _details, _flags = params.unpack()
        self.timeouts[action] = timeout
        callback(self, GLib.Variant('((bba{ss}))', ((True, False, {}),)))

    def call_finish(self, result):
        return result


def test_interactive_polkit_check_waits_for_the_dialog():
    pytest.importorskip('gi')
    conn = PolkitConn()
    done = []
    helper.check_polkit_authorization(
        conn, SENDER, helper.ACTION_UPGRADE,
        lambda ok, uid, err: done.append(ok), interactive=True)
    helper.check_polkit_authorization(
        conn, SENDER, helper.ACTION_CHARGE,
        lambda ok, uid, err: done.append(ok))
    assert done == [True, True]
    assert conn.timeouts[helper.ACTION_UPGRADE] == NO_TIMEOUT
    assert conn.timeouts[helper.ACTION_CHARGE] == 10_000


# ------------------------------------------------------------- the client

class ClientVariant:
    def __init__(self, value):
        self.value = value

    def unpack(self):
        return self.value


class JobConn:
    """Fake system bus: records each call's timeout, replies with
    ``job_id``, and lets the test emit signals the way GDBus delivers
    them, ``(conn, sender, path, iface, signal, params, user_data)``."""

    def __init__(self, job_id='job-1', error=None):
        self.job_id = job_id
        self.error = error
        self.timeouts = {}
        self.subs = {}
        self.pending = []
        self._next = 0

    def signal_subscribe(self, sender, iface, member, path, arg0, flags,
                         callback, user_data):
        self._next += 1
        self.subs[self._next] = (sender, member, arg0, callback, user_data)
        return self._next

    def signal_unsubscribe(self, sub_id):
        del self.subs[sub_id]

    def call(self, bus_name, path, iface, method, params, reply_type,
             flags, timeout, cancellable, callback):
        self.timeouts[method] = timeout
        self.pending.append(callback)

    def reply(self):
        self.pending.pop(0)(self, 'token')

    def call_finish(self, _token):
        if self.error is not None:
            raise self.error
        return ClientVariant((self.job_id,))

    def emit(self, member, values):
        for sender, sub_member, arg0, cb, data in list(self.subs.values()):
            if sub_member == member:
                cb(self, sender, '/p', 'i', member, ClientVariant(values),
                   data)


class ClientGio:
    class BusType:
        SYSTEM = 'system'

    class DBusCallFlags:
        NONE = 0

    class DBusSignalFlags:
        NONE = 0

    conn = None

    @classmethod
    def bus_get(cls, _bus_type, _cancellable, callback):
        callback(None, cls.conn)

    @classmethod
    def bus_get_finish(cls, result):
        return result


class ClientGLib:
    class Variant:
        def __init__(self, signature, value):
            self.signature = signature
            self.value = value

        def unpack(self):
            return self.value


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(helper_client, '_gio', lambda: (ClientGio,
                                                        ClientGLib))
    ClientGio.conn = JobConn()
    return ClientGio.conn


class TestClientJobs:
    def test_upgrade_waits_for_job_finished(self, client):
        done = []
        helper_client.upgrade_system(lambda ok, res: done.append((ok, res)))
        client.reply()
        assert done == []
        client.emit('JobFinished', ('other-job', 0, 'nope'))
        assert done == []
        client.emit('JobFinished', ('job-1', 0, 'OK: 3 packages'))
        assert done == [(True, 'OK: 3 packages')]
        assert client.subs == {}

    def test_failed_job_reports_its_tail(self, client):
        done = []
        helper_client.remove_package(
            'vim', lambda ok, res: done.append((ok, res)))
        client.reply()
        client.emit('JobFinished', ('job-1', 1, 'ERROR: vim: busy\n'))
        assert done == [(False, 'ERROR: vim: busy')]

    def test_failed_job_without_output_reports_exit(self, client):
        done = []
        helper_client.upgrade_system(lambda ok, res: done.append((ok, res)))
        client.reply()
        client.emit('JobFinished', ('job-1', 2, ''))
        assert done == [(False, 'exit 2')]

    def test_signal_racing_ahead_of_the_reply(self, client):
        done = []
        helper_client.upgrade_system(lambda ok, res: done.append((ok, res)))
        client.emit('JobFinished', ('job-1', 0, 'done'))
        assert done == []
        client.reply()
        assert done == [(True, 'done')]

    def test_helper_vanishing_fails_the_job(self, client):
        done = []
        helper_client.upgrade_system(lambda ok, res: done.append((ok, res)))
        client.reply()
        client.emit('NameOwnerChanged', (helper_client.BUS_NAME, ':1.9', ''))
        assert done and done[0][0] is False
        client.emit('JobFinished', ('job-1', 0, 'late'))
        assert len(done) == 1

    def test_old_instance_exiting_before_activation_is_ignored(self,
                                                               client):
        done = []
        helper_client.upgrade_system(lambda ok, res: done.append((ok, res)))
        client.emit('NameOwnerChanged', (helper_client.BUS_NAME, ':1.8', ''))
        client.emit('NameOwnerChanged', (helper_client.BUS_NAME, '', ':1.9'))
        client.reply()
        assert done == []
        client.emit('JobFinished', ('job-1', 0, 'ok'))
        assert done == [(True, 'ok')]

    def test_auth_refusal_fails_once_and_unsubscribes(self, client):
        client.error = Exception(
            'GDBus.Error:io.piercingxx.XXWM.Helper1.Error.NotAuthorized: no')
        done = []
        helper_client.upgrade_system(lambda ok, res: done.append((ok, res)))
        client.reply()
        assert done[0][0] is False and 'NotAuthorized' in done[0][1]
        assert client.subs == {}

    def test_job_signals_subscribed_from_the_helper_only(self, client):
        helper_client.upgrade_system(lambda ok, res: None)
        subs = {member: (sender, arg0)
                for sender, member, arg0, _cb, _d in client.subs.values()}
        assert subs['JobFinished'][0] == helper_client.BUS_NAME
        assert subs['NameOwnerChanged'] == ('org.freedesktop.DBus',
                                            helper_client.BUS_NAME)


    def test_refresh_waits_for_job_finished(self, client):
        done = []
        helper_client.refresh_package_index(
            lambda ok, res: done.append((ok, res)))
        client.reply()
        assert done == []
        client.emit('JobFinished', ('job-1', 0, 'OK: 20000 distinct\n'))
        assert done == [(True, None)]

    def test_refresh_failure_reports_the_tail(self, client):
        done = []
        helper_client.refresh_package_index(
            lambda ok, res: done.append((ok, res)))
        client.reply()
        client.emit('JobFinished', ('job-1', 1, 'ERROR: network down\n'))
        assert done == [(False, 'ERROR: network down')]

    def test_clean_reports_the_cleaned_categories(self, client):
        done = []
        helper_client.clean_system(['paccache-keep', 'apt-clean'],
                                   lambda ok, res: done.append((ok, res)))
        client.reply()
        assert done == []
        client.emit('JobFinished', ('job-1', 0, 'paccache-keep\napt-clean'))
        assert done == [(True, ['paccache-keep', 'apt-clean'])]

    def test_clean_with_nothing_cleaned(self, client):
        done = []
        helper_client.clean_system(['apt-clean'],
                                   lambda ok, res: done.append((ok, res)))
        client.reply()
        client.emit('JobFinished', ('job-1', 0, ''))
        assert done == [(True, [])]


class TestClientTimeouts:
    def test_job_start_waits_for_the_password(self, client):
        helper_client.upgrade_system(lambda ok, res: None)
        helper_client.remove_package('vim', lambda ok, res: None)
        assert client.timeouts['UpgradeSystem'] == NO_TIMEOUT
        assert client.timeouts['RemovePackage'] == NO_TIMEOUT

    def test_quick_calls_are_short(self, client):
        helper_client.get_capabilities(lambda ok, res: None, refresh=True)
        helper_client.has_pin(lambda ok, res: None)
        helper_client.set_charge_limit(80, lambda ok, res: None)
        for method in ('GetCapabilities', 'HasPin', 'SetChargeLimit'):
            assert client.timeouts[method] <= 10_000, method

    def test_prompting_calls_wait_too(self, client):
        helper_client.manage_service('ssh', 'start', lambda ok, res: None)
        helper_client.set_firewall_enabled(False, lambda ok, res: None)
        assert client.timeouts['ManageService'] == NO_TIMEOUT
        assert client.timeouts['SetFirewallEnabled'] == NO_TIMEOUT

    def test_every_interactive_method_has_no_timeout(self):
        interactive = {method for method, (_a, inter)
                       in helper._METHOD_ACTIONS.items() if inter}
        interactive |= {'ManageService', 'SetInboundPreset',
                        'SetInboundAllowed', 'SetFirewallEnabled'}
        for method in interactive:
            assert helper_client.call_timeout_ms(method) == NO_TIMEOUT, (
                method)


# ------------------------------------------ real bus, real helper object

@pytest.fixture()
def private_bus():
    if shutil.which('dbus-daemon') is None:
        pytest.skip('needs dbus-daemon')
    Gio = pytest.importorskip('gi.repository.Gio')
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


def test_job_over_a_real_bus(private_bus, monkeypatch, apk):
    from gi.repository import Gio, GLib
    helper_conn, client_conn = private_bus
    spawn = HeldSpawn()
    service = helper.HelperService(
        connection=helper_conn, core=helper.HelperCore(spawn=spawn),
        authorize=allow)
    service.register()
    owned = []
    Gio.bus_own_name_on_connection(
        helper_conn, helper.BUS_NAME, Gio.BusNameOwnerFlags.NONE,
        lambda *a: owned.append(True), None)
    assert _pump_until(lambda: owned)

    class RealishGio:
        BusType = SimpleNamespace(SYSTEM='system')
        DBusCallFlags = Gio.DBusCallFlags
        DBusSignalFlags = Gio.DBusSignalFlags

        @staticmethod
        def bus_get(_bus_type, _cancellable, callback):
            GLib.idle_add(lambda: callback(None, client_conn) and False)

        @staticmethod
        def bus_get_finish(result):
            return result

    monkeypatch.setattr(helper_client, '_gio', lambda: (RealishGio, GLib))
    done = []
    try:
        helper_client.upgrade_system(lambda ok, res: done.append((ok, res)))
        assert _pump_until(lambda: spawn.jobs)
        assert len(service.jobs) == 1
        spawn.jobs[0][1](0, '(1/1) Upgrading foo\nOK: 1 package\n')
        assert _pump_until(lambda: done)
        assert done == [(True, '(1/1) Upgrading foo\nOK: 1 package')]
    finally:
        service.unregister()
