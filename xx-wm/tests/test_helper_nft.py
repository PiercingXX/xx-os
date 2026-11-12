"""Root helper and nftables: caller strings never become nft grammar,
and per-app blocks fail closed.

The validation and transaction tests need no nft. The namespace tests
run HelperCore against the real ``nft`` inside ``unshare -rn`` (a private
user + network namespace, so the host ruleset is never touched) and skip
where unshare, nft or unprivileged user namespaces are unavailable, or
where the kernel lacks the expression a test needs.
"""
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

HELPER_DIR = Path(__file__).parent.parent / 'launcher' / 'helper'
sys.path.insert(0, str(HELPER_DIR))
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import xx_wm_helper as helper  # noqa: E402

UID = 1000
APP = 'app-xxwm-org.example.App.desktop-1234.scope'

HOSTILE = (
    'x" ; flush ruleset ; "',
    'x;flush ruleset',
    'include "/etc/shadow"',
    'x { }',
    'x\nflush ruleset',
    'x y',
    'x\\x2d',
)


class FakeRun:
    """argv-list stand-in for subprocess.run; records stdin too."""

    def __init__(self, fail=False, stderr='', stdout=''):
        self.calls = []
        self.fail = fail
        self.stderr = stderr
        self.stdout = stdout

    def __call__(self, argv, input=None):
        assert isinstance(argv, list), argv
        assert argv and all(isinstance(a, str) for a in argv), argv
        self.calls.append((list(argv), input))
        if self.fail:
            return SimpleNamespace(returncode=1, stdout='',
                                   stderr=self.stderr)
        return SimpleNamespace(returncode=0, stdout=self.stdout, stderr='')

    @property
    def cmds(self):
        return [argv for argv, _input in self.calls]

    def scripts(self):
        return [data for argv, data in self.calls
                if argv[:1] == ['nft'] and data is not None]


@pytest.fixture()
def layout(tmp_path):
    cg = tmp_path / 'cg'
    slice_dir = cg / 'user.slice' / ('user-%d.slice' % UID)
    leaf = slice_dir / 'user@1000.service' / 'app.slice' / APP
    leaf.mkdir(parents=True)
    mounts = tmp_path / 'mounts'
    mounts.write_text('cgroup2 %s cgroup2 rw 0 0\n' % cg)
    return SimpleNamespace(cg=str(cg), slice=str(slice_dir), leaf=str(leaf),
                           mounts=str(mounts),
                           state=str(tmp_path / 'state.json'))


def make_core(layout, run=None, **over):
    kw = dict(sysfs=str(Path(layout.cg).parent / 'sys'),
              cgroup_root=layout.cg, mounts_file=layout.mounts,
              state_file=layout.state, nft_bin='nft',
              run=run or FakeRun())
    kw.update(over)
    core = helper.HelperCore(**kw)
    core.nft_available = lambda: True
    return core


def rel(layout, path):
    return os.path.relpath(path, layout.cg)


# ------------------------------------------------------ path validation


class TestCgroupValidation:
    @pytest.mark.parametrize('name', HOSTILE)
    def test_hostile_component_refused_even_when_it_exists(
            self, layout, name):
        bad = os.path.join(layout.slice, name)
        os.makedirs(bad, exist_ok=True)
        with pytest.raises(helper.HelperError) as exc:
            make_core(layout).validate_app_cgroup(bad, UID)
        assert exc.value.dbus_name == helper.ERROR_NOT_AUTHORIZED

    def test_missing_directory_refused(self, layout):
        gone = os.path.join(layout.slice, 'app-xxwm-gone-1.scope')
        with pytest.raises(helper.HelperError) as exc:
            make_core(layout).validate_app_cgroup(gone, UID)
        assert exc.value.dbus_name == helper.ERROR_NOT_AUTHORIZED

    def test_real_scope_names_pass(self, layout):
        assert make_core(layout).validate_app_cgroup(
            layout.leaf, UID) == layout.leaf

    def test_block_of_missing_scope_refused_unblock_allowed(self, layout):
        run = FakeRun()
        core = make_core(layout, run)
        gone = os.path.join(layout.slice, 'app-xxwm-gone-1.scope')
        with pytest.raises(helper.HelperError):
            core.set_app_network(gone, False, UID)
        assert run.calls == []
        assert core.set_app_network(gone, True, UID) is True

    def test_match_string_refuses_grammar(self, layout):
        core = make_core(layout)
        with pytest.raises(helper.HelperError):
            core.cgroup_nft_match(os.path.join(layout.slice, 'x" drop'))


# ------------------------------------------- per-app blocking, fail closed


class TestAppBlockTransaction:
    def test_one_atomic_script_on_stdin(self, layout):
        run = FakeRun()
        core = make_core(layout, run)
        core.set_app_network(layout.leaf, False, UID)
        assert run.cmds == [['nft', '-f', '-']]
        script = run.scripts()[0]
        assert 'delete table inet xxwm' in script
        assert ('socket cgroupv2 level 5 "%s" drop'
                % rel(layout, layout.leaf)) in script
        assert 'policy accept' in script
        assert json.loads(Path(layout.state).read_text())['blocked'] == [
            layout.leaf]

    def test_failure_keeps_state_and_hides_stderr(self, layout):
        core = make_core(layout, FakeRun())
        core.set_app_network(layout.leaf, False, UID)
        other = os.path.join(os.path.dirname(layout.leaf),
                             'app-xxwm-other-9.scope')
        os.makedirs(other)
        failing = FakeRun(fail=True,
                          stderr='Error: root:$6$secret:19000::::::\n')
        core._run = failing
        with pytest.raises(helper.HelperError) as exc:
            core.set_app_network(other, False, UID)
        assert exc.value.dbus_name == helper.ERROR_FAILED
        assert 'secret' not in str(exc.value)
        assert 'root:' not in str(exc.value)
        assert json.loads(Path(layout.state).read_text())['blocked'] == [
            layout.leaf]
        # One transaction: nothing flushed outside it, nothing half-done.
        assert all(argv == ['nft', '-f', '-'] for argv in failing.cmds)

    def test_service_error_never_carries_nft_stderr(self, layout):
        core = make_core(layout, FakeRun(fail=True,
                                         stderr='Error: /etc/shadow:1\n'))

        def allow(conn, sender, action, interactive, callback):
            callback(True, UID, None)

        service = helper.HelperService(connection=object(), core=core,
                                       authorize=allow)
        seen = {}
        invocation = SimpleNamespace(
            return_value=lambda value: seen.setdefault('value', value),
            return_dbus_error=lambda name, msg: seen.setdefault(
                'error', (name, msg)))
        service._on_method_call(
            service.connection, ':1.7', '/p', 'i', 'SetAppNetwork',
            SimpleNamespace(unpack=lambda: (layout.leaf, False)), invocation)
        name, message = seen['error']
        assert name == helper.ERROR_FAILED
        assert 'shadow' not in message and 'Error:' not in message

    def test_stale_scopes_pruned_not_fatal(self, layout):
        gone = os.path.join(os.path.dirname(layout.leaf),
                            'app-xxwm-gone-7.scope')
        Path(layout.state).write_text(json.dumps({'blocked': [gone]}))
        run = FakeRun()
        core = make_core(layout, run)
        core.set_app_network(layout.leaf, False, UID)
        script = run.scripts()[0]
        assert 'gone-7' not in script
        assert rel(layout, layout.leaf) in script
        assert json.loads(Path(layout.state).read_text())['blocked'] == [
            layout.leaf]

    def test_apply_saved_revalidates_persisted_paths(self, layout):
        hostile = os.path.join(layout.slice, 'x" ; flush ruleset ; "')
        os.makedirs(hostile)
        outside = os.path.join(layout.cg, 'system.slice')
        os.makedirs(outside)
        Path(layout.state).write_text(json.dumps({'blocked': [
            hostile, outside, layout.leaf,
            os.path.join(layout.slice, 'app-xxwm-gone-1.scope')]}))
        run = FakeRun()
        applied = make_core(layout, run).apply_saved()
        assert applied['errors'] == []
        assert applied['blocked'] == [layout.leaf]
        script, = run.scripts()
        assert 'flush ruleset' not in script
        assert 'system.slice' not in script
        assert json.loads(Path(layout.state).read_text())['blocked'] == [
            layout.leaf]

    def test_boot_without_user_slice_is_not_an_error(self, layout):
        Path(layout.state).write_text(json.dumps({'blocked': [
            os.path.join(layout.cg, 'user.slice', 'user-1001.slice',
                         'app-xxwm-a-1.scope')]}))
        applied = make_core(layout, FakeRun()).apply_saved()
        assert applied['errors'] == []
        assert applied['blocked'] == []


# ---------------------------------------------- real nft in a namespace


def _netns_ok():
    if shutil.which('unshare') is None or shutil.which('nft') is None:
        return False
    try:
        proc = subprocess.run(['unshare', '-rn', 'nft', 'list', 'ruleset'],
                              capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


needs_netns = pytest.mark.skipif(
    not _netns_ok(),
    reason='needs unshare, nft and unprivileged user namespaces')

_NETNS_DRIVER = textwrap.dedent('''
    import json, os, subprocess, sys
    sys.path.insert(0, sys.argv[1])
    import xx_wm_helper as helper
    args = json.loads(sys.argv[2])

    def nft(*argv, data=None):
        return subprocess.run(['nft', *argv], input=data,
                              capture_output=True, text=True)

    out = {}
    core = helper.HelperCore(state_file=args['state'])
    if args['mode'] == 'hostile':
        nft('add', 'table', 'inet', 'sentinel')
        errors = []
        for name in args['names']:
            path = core.user_slice_prefix(args['uid']) + '/' + name
            try:
                core.set_app_network(path, False, args['uid'])
                errors.append(None)
            except helper.HelperError as err:
                errors.append([err.dbus_name, str(err)])
        out['errors'] = errors
    elif args['mode'] == 'probe':
        out['rc'] = nft('-j', '-c', '-f', '-', data=args['doc']).returncode
        out['err'] = nft('-j', '-c', '-f', '-', data=args['doc']).stderr
    elif args['mode'] == 'block':
        prefix = core.user_slice_prefix(args['uid'])
        core.set_app_network(prefix, False, args['uid'])
        out['listed'] = nft('list', 'table', 'inet', 'xxwm').stdout
    out['ruleset'] = json.loads(nft('-j', 'list', 'ruleset').stdout)
    print(json.dumps(out))
''')


def _in_netns(tmp_path, **args):
    script = tmp_path / 'netns_driver.py'
    script.write_text(_NETNS_DRIVER)
    args.setdefault('state', str(tmp_path / 'state.json'))
    proc = subprocess.run(
        ['unshare', '-rn', sys.executable, str(script), str(HELPER_DIR),
         json.dumps(args)],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _tables(ruleset):
    return {(item['table']['family'], item['table']['name'])
            for item in ruleset['nftables'] if 'table' in item}


def _kernel_accepts(tmp_path, doc):
    out = _in_netns(tmp_path, mode='probe', doc=json.dumps(doc))
    if out['rc'] == 0:
        return True
    # The JSON parser itself must never reject our documents; only a
    # kernel missing an expression module may.
    assert 'Could not process rule' in out['err'], out['err']
    return False


@needs_netns
class TestRealNft:
    def test_hostile_paths_never_reach_nft(self, tmp_path):
        names = list(HOSTILE) + ['definitely-missing-%d.scope' % os.getpid()]
        out = _in_netns(tmp_path, mode='hostile', uid=os.getuid(),
                        names=names)
        assert len(out['errors']) == len(names)
        for error in out['errors']:
            assert error is not None
            assert error[0] == helper.ERROR_NOT_AUTHORIZED
            assert 'Error:' not in error[1]
        tables = _tables(out['ruleset'])
        assert ('inet', 'sentinel') in tables
        assert ('inet', 'xxwm') not in tables
        assert not (tmp_path / 'state.json').exists()

    def test_cgroup_block_applies(self, tmp_path):
        prefix = '/sys/fs/cgroup/user.slice/user-%d.slice' % os.getuid()
        if not os.path.isdir(prefix):
            pytest.skip('no systemd user slice on this host')
        probe = {'nftables': [
            {'add': {'table': {'family': 'inet', 'name': 'probe'}}},
            {'add': {'chain': {'family': 'inet', 'table': 'probe',
                               'name': 'o', 'type': 'filter',
                               'hook': 'output', 'prio': 0,
                               'policy': 'accept'}}},
            {'add': {'rule': {'family': 'inet', 'table': 'probe',
                              'chain': 'o', 'expr': [
                                  {'match': {'op': '==', 'left': {
                                      'socket': {'key': 'mark'}},
                                      'right': 1}},
                                  {'accept': None}]}}}]}
        if not _kernel_accepts(tmp_path, probe):
            pytest.skip('kernel lacks nft_socket')
        out = _in_netns(tmp_path, mode='block', uid=os.getuid())
        assert ('socket cgroupv2 level 2 "user.slice/user-%d.slice" drop'
                % os.getuid()) in out['listed']
