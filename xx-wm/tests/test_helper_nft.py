"""Root helper and nftables: caller strings never become nft grammar,
per-app blocks fail closed, and the Firewall switch is a real drop policy.

The render, parse and validation tests need no nft. The namespace tests
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

import settings_page  # noqa: E402,F401  (first: section modules bind _Section)
import settings_firewall as firewall  # noqa: E402
import xx_wm_helper as helper  # noqa: E402

UID = 1000
APP = 'app-xxwm-org.example.App.desktop-1234.scope'
DATA = Path(__file__).parent.parent / 'launcher' / 'data'

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
    proc = tmp_path / 'proc'
    (proc / 'net').mkdir(parents=True)
    return SimpleNamespace(cg=str(cg), slice=str(slice_dir), leaf=str(leaf),
                           mounts=str(mounts), proc=str(proc),
                           state=str(tmp_path / 'state.json'),
                           dropin=str(tmp_path / 'nftables.d'))


def make_core(layout, run=None, **over):
    kw = dict(sysfs=str(Path(layout.cg).parent / 'sys'),
              cgroup_root=layout.cg, mounts_file=layout.mounts,
              state_file=layout.state, nft_bin='nft',
              nft_dropin_dir=layout.dropin, proc_root=layout.proc,
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


# --------------------------------------------- firewall: render (no nft)


def _chain(doc):
    return next(item['chain'] for item in doc['nftables']
                if 'chain' in item and 'add' not in item)


def _adds(doc, kind):
    return [item['add'][kind] for item in doc['nftables']
            if 'add' in item and kind in item['add']]


class TestFirewallRender:
    def test_json_chain_is_a_drop_policy(self, layout):
        doc = make_core(layout).render_inbound_json({'ssh'})
        chain, = _adds(doc, 'chain')
        assert chain['table'] == helper.NFT_IN_TABLE
        assert chain['hook'] == 'input'
        assert chain['policy'] == 'drop'
        json.dumps(doc)

    def test_base_rules_come_first_then_presets(self, layout, monkeypatch):
        monkeypatch.setattr(helper, 'ALPHA_ALLOW_CONTAINER_BRIDGES', False)
        rules = _adds(make_core(layout).render_inbound_json(
            {'syncthing'}), 'rule')
        lefts = [rule['expr'][0]['match']['left'] for rule in rules]
        assert lefts[0] == {'ct': {'key': 'state'}}
        assert rules[0]['expr'][0]['match']['right'] == {
            'set': ['established', 'related']}
        assert lefts[1] == {'meta': {'key': 'iif'}}
        assert {'meta': {'key': 'l4proto'}} in lefts[2:4]
        rights = [rule['expr'][0]['match']['right'] for rule in rules[2:4]]
        assert rights == ['icmp', 'ipv6-icmp']
        dhcp = [[m['match']['right'] for m in rule['expr'][:2]]
                for rule in rules[4:6]]
        assert dhcp == [[67, 68], [547, 546]]
        comments = [rule.get('comment') for rule in rules[6:]]
        assert comments == ['xxwm syncthing'] * 3
        assert all(rule['expr'][-1] == {'accept': None} for rule in rules)

    def test_ssh_safeguard_rule_is_marked(self, layout):
        rules = _adds(make_core(layout).render_inbound_json(
            set(), keep_ssh=True), 'rule')
        assert rules[-1]['comment'] == helper.NFT_KEEP_SSH_COMMENT
        assert rules[-1]['expr'][0]['match']['right'] == 22

    def test_dropin_text_matches_shell_mirror(self, layout):
        text = make_core(layout).render_inbound_nft({'ssh', 'kdeconnect'})
        bridges = helper.active_container_bridges()
        assert 'policy drop;' in text
        assert 'ct state established,related accept' in text
        assert 'iif "lo" accept' in text
        assert 'meta l4proto ipv6-icmp accept' in text
        assert 'udp sport 67 udp dport 68 accept' in text
        assert 'tcp dport 1714-1764 accept comment "xxwm kdeconnect";' \
            in text
        assert text == firewall.render_nft_rules({'ssh', 'kdeconnect'},
                                                 bridges)


def _bridge_rules(rules):
    return [rule for rule in rules
            if rule.get('comment') == helper.NFT_CONTAINER_COMMENT]


class TestContainerBridges:
    """Alpha only: Android containers on a host bridge get DHCP and DNS
    from the host's dnsmasq; nothing else from the bridge is accepted."""

    BRIDGES = ['andromeda0', 'lxcbr0', 'waydroid0']

    def test_rules_present_when_flag_on(self, layout, monkeypatch):
        monkeypatch.setattr(helper, 'ALPHA_ALLOW_CONTAINER_BRIDGES', True)
        assert list(helper.active_container_bridges()) == self.BRIDGES
        rules = _bridge_rules(_adds(make_core(layout).render_inbound_json(
            set()), 'rule'))
        assert len(rules) == 3

    def test_rules_absent_when_flag_off(self, layout, monkeypatch):
        monkeypatch.setattr(helper, 'ALPHA_ALLOW_CONTAINER_BRIDGES', False)
        assert helper.active_container_bridges() == ()
        core = make_core(layout)
        doc = core.render_inbound_json({'ssh'}, keep_ssh=True)
        assert _bridge_rules(_adds(doc, 'rule')) == []
        assert 'iifname' not in json.dumps(doc)
        text = core.render_inbound_nft({'ssh'})
        assert 'iifname' not in text
        assert helper.NFT_CONTAINER_COMMENT not in text

    def test_rules_match_only_bridges_and_dhcp_dns(self, layout,
                                                   monkeypatch):
        monkeypatch.setattr(helper, 'ALPHA_ALLOW_CONTAINER_BRIDGES', True)
        rules = _bridge_rules(_adds(make_core(layout).render_inbound_json(
            {'syncthing'}), 'rule'))
        specs = []
        for rule in rules:
            iface, port, verdict = rule['expr']
            assert iface == {'match': {
                'op': '==', 'left': {'meta': {'key': 'iifname'}},
                'right': {'set': self.BRIDGES}}}
            assert verdict == {'accept': None}
            match = port['match']
            assert match['op'] == '=='
            assert match['left']['payload']['field'] == 'dport'
            specs.append((match['left']['payload']['protocol'],
                          match['right']))
        assert specs == [('udp', 67), ('udp', 53), ('tcp', 53)]

    def test_no_blanket_accept_on_a_bridge(self, layout, monkeypatch):
        monkeypatch.setattr(helper, 'ALPHA_ALLOW_CONTAINER_BRIDGES', True)
        for rule in _adds(make_core(layout).render_inbound_json(
                {'ssh'}, keep_ssh=True), 'rule'):
            if 'iifname' in json.dumps(rule):
                assert len(rule['expr']) == 3, rule

    def test_rules_follow_base_rules_before_presets(self, layout,
                                                    monkeypatch):
        monkeypatch.setattr(helper, 'ALPHA_ALLOW_CONTAINER_BRIDGES', True)
        rules = _adds(make_core(layout).render_inbound_json(
            {'syncthing'}, keep_ssh=True), 'rule')
        comments = [rule.get('comment') for rule in rules]
        assert comments[6:9] == [helper.NFT_CONTAINER_COMMENT] * 3
        assert comments[-1] == helper.NFT_KEEP_SSH_COMMENT

    def test_dropin_text(self, layout, monkeypatch):
        monkeypatch.setattr(helper, 'ALPHA_ALLOW_CONTAINER_BRIDGES', True)
        text = make_core(layout).render_inbound_nft(set())
        sets = 'iifname { "andromeda0", "lxcbr0", "waydroid0" }'
        for spec in ('udp dport 67', 'udp dport 53', 'tcp dport 53'):
            assert ('        %s %s accept comment "%s";'
                    % (sets, spec, helper.NFT_CONTAINER_COMMENT)) in text
        assert text.count('iifname') == 3

    def test_bridge_rules_are_not_read_back_as_presets(self, layout,
                                                       monkeypatch):
        monkeypatch.setattr(helper, 'ALPHA_ALLOW_CONTAINER_BRIDGES', True)
        core = make_core(layout)
        doc = core.render_inbound_json(set())
        listing = json.dumps({'nftables': [
            item['add'] for item in doc['nftables']
            if 'add' in item and ('chain' in item['add']
                                  or 'rule' in item['add'])]})
        parsed = core.parse_nft_ruleset(listing)
        assert parsed['blocking'] is True
        assert parsed['open_ports'] == set()


# ------------------------------------------- firewall: state from nft -j


def _ruleset(policy='drop', ports=(), extra=()):
    items = [{'metainfo': {'json_schema_version': 1}},
             {'table': {'family': 'inet', 'name': 'xx_wm'}},
             {'chain': {'family': 'inet', 'table': 'xx_wm', 'name': 'input',
                        'type': 'filter', 'hook': 'input', 'prio': 0,
                        'policy': policy}}]
    for proto, port, comment in ports:
        right = {'range': list(port)} if isinstance(port, tuple) else port
        rule = {'family': 'inet', 'table': 'xx_wm', 'chain': 'input',
                'expr': [{'match': {'op': '==', 'left': {'payload': {
                    'protocol': proto, 'field': 'dport'}}, 'right': right}},
                    {'accept': None}]}
        if comment:
            rule['comment'] = comment
        items.append({'rule': rule})
    items.extend(extra)
    return json.dumps({'nftables': items})


def _state(layout, ruleset):
    core = make_core(layout, FakeRun(stdout=ruleset))
    core.firewall_backend = lambda: 'nftables'
    return core, core.get_firewall_state()


class TestFirewallState:
    def test_reads_json_listing(self, layout):
        core, _state_ = _state(layout, _ruleset())
        core.get_firewall_state()
        assert ['nft', '-j', 'list', 'ruleset'] in core._run.cmds

    def test_port_22000_is_not_port_22(self, layout):
        _core, state = _state(layout, _ruleset(ports=(
            ('tcp', 22000, 'xxwm syncthing'),
            ('udp', 22000, 'xxwm syncthing'),
            ('udp', 21027, 'xxwm syncthing'))))
        assert state['enabled'] is True
        assert state['allowed']['syncthing'] is True
        assert state['allowed']['ssh'] is False

    def test_range_must_match_exactly(self, layout):
        _core, state = _state(layout, _ruleset(ports=(
            ('tcp', (1714, 1764), 'xxwm kdeconnect'),)))
        assert state['allowed']['kdeconnect'] is False
        _core, state = _state(layout, _ruleset(ports=(
            ('tcp', (1714, 1764), 'xxwm kdeconnect'),
            ('udp', (1714, 1764), 'xxwm kdeconnect'))))
        assert state['allowed']['kdeconnect'] is True

    def test_accept_policy_is_not_blocking(self, layout):
        _core, state = _state(layout, _ruleset(policy='accept'))
        assert state['enabled'] is False

    def test_safeguard_rule_is_not_the_ssh_feature(self, layout):
        _core, state = _state(layout, _ruleset(ports=(
            ('tcp', 22, helper.NFT_KEEP_SSH_COMMENT),)))
        assert state['allowed']['ssh'] is False

    def test_foreign_drop_ignores_our_own_chain(self, layout):
        _core, state = _state(layout, _ruleset())
        assert state['foreign_drop'] is False
        other = {'chain': {'family': 'inet', 'table': 'filter',
                           'name': 'input', 'type': 'filter',
                           'hook': 'input', 'prio': 0, 'policy': 'drop'}}
        _core, state = _state(layout, _ruleset(extra=(other,)))
        assert state['foreign_drop'] is True

    def test_no_table_is_off(self, layout):
        _core, state = _state(layout, json.dumps({'nftables': []}))
        assert state['enabled'] is False


# ------------------------------------- firewall: enabling and its auth


class TestFirewallEnable:
    def test_enable_needs_admin_interactive(self):
        assert helper.policy_for('SetFirewallEnabled', (True,)) == (
            helper.ACTION_FW_ENABLE, True)
        assert helper.policy_for('SetFirewallEnabled', (False,)) == (
            helper.ACTION_FW_OPEN, True)

    def test_policy_file_enable_is_auth_admin_keep(self):
        import xml.etree.ElementTree as xml
        root = xml.parse(str(DATA / 'polkit-1' / 'actions'
                             / 'io.piercingxx.XXWM.Helper.policy')).getroot()
        action = next(a for a in root.iter('action')
                      if a.get('id') == helper.ACTION_FW_ENABLE)
        assert action.find('defaults/allow_active').text == 'auth_admin_keep'

    def test_enable_installs_drop_chain_as_json(self, layout):
        run = FakeRun()
        core = make_core(layout, run)
        core.firewall_backend = lambda: 'nftables'
        core.ssh_keep_open = lambda: False
        core.set_firewall_enabled(True)
        docs = [json.loads(data) for argv, data in run.calls
                if argv == ['nft', '-j', '-f', '-']]
        assert len(docs) == 1
        chain, = _adds(docs[0], 'chain')
        assert chain['policy'] == 'drop'
        assert core.load_inbound()['enabled'] is True

    def test_feature_change_never_enables_by_itself(self, layout):
        run = FakeRun()
        core = make_core(layout, run)
        core.firewall_backend = lambda: 'nftables'
        core.ssh_keep_open = lambda: False
        core.set_inbound_allowed(['ssh'])
        assert not any('add' in argv and 'xx_wm' in argv
                       for argv in run.cmds)
        assert [data for _argv, data in run.calls if data] == []
        assert core.load_inbound()['allowed'] == ['ssh']

    def test_nft_safeguard_keeps_ssh_when_sshd_listens(self, layout):
        Path(layout.proc, 'net', 'tcp').write_text(
            '  sl  local_address rem_address   st\n'
            '   0: 00000000:0016 00000000:0000 0A 00000000:00000000\n')
        run = FakeRun()
        core = make_core(layout, run)
        core.firewall_backend = lambda: 'nftables'
        core.set_firewall_enabled(True)
        doc = json.loads(run.scripts()[-1])
        assert _adds(doc, 'rule')[-1]['comment'] == \
            helper.NFT_KEEP_SSH_COMMENT


UFW_HEAD = 'Status: active\nLogging: on (low)\n'


class TestUfw:
    def _core(self, layout, status=''):
        run = FakeRun(stdout=status)
        core = make_core(layout, run)
        core.firewall_backend = lambda: 'ufw'
        core.init_system = lambda: 'unknown'
        return run, core

    def test_keeps_22_when_sshd_listens_without_ssh_connection(
            self, layout, monkeypatch):
        monkeypatch.delenv('SSH_CONNECTION', raising=False)
        Path(layout.proc, 'net', 'tcp6').write_text(
            '  sl  local_address                         rem_address   st\n'
            '   0: 00000000000000000000000000000000:0016 '
            '00000000000000000000000000000000:0000 0A\n')
        run, core = self._core(layout)
        core.set_firewall_enabled(True)
        assert ['ufw', 'allow', '22/tcp'] in run.cmds
        assert run.cmds.index(['ufw', 'allow', '22/tcp']) < run.cmds.index(
            ['ufw', '--force', 'enable'])

    def test_keeps_22_when_sshd_enabled_but_stopped(self, layout):
        run, core = self._core(layout)
        core.init_system = lambda: 'systemd'
        core.set_firewall_enabled(True)
        assert ['ufw', 'allow', '22/tcp'] in run.cmds

    def test_no_22_when_sshd_is_off(self, layout, monkeypatch):
        monkeypatch.setenv('SSH_CONNECTION', '1.2.3.4 5 6.7.8.9 22')
        Path(layout.proc, 'net', 'tcp').write_text(
            '  sl  local_address rem_address   st\n'
            '   0: 00000000:55F0 00000000:0000 0A\n'
            '   1: 0100007F:0016 0200007F:9C40 01\n')
        run, core = self._core(layout)
        core.set_firewall_enabled(True)
        assert ['ufw', 'allow', '22/tcp'] not in run.cmds

    def test_enabled_needs_default_deny(self, layout):
        _run, core = self._core(
            layout, UFW_HEAD + 'Default: allow (incoming), allow (outgoing)\n')
        assert core.get_firewall_state()['enabled'] is False
        _run, core = self._core(
            layout, UFW_HEAD + 'Default: deny (incoming), allow (outgoing)\n')
        assert core.get_firewall_state()['enabled'] is True

    def test_spec_match_is_per_rule_not_substring(self, layout):
        status = (UFW_HEAD + 'Default: deny (incoming), allow (outgoing)\n\n'
                  'To                         Action      From\n'
                  '--                         ------      ----\n'
                  '1722/tcp                   ALLOW IN    Anywhere\n')
        _run, core = self._core(layout, status)
        assert core.get_firewall_state()['allowed']['ssh'] is False


# ---------------------------------------------- real nft in a namespace


def _netns_ok():
    if shutil.which('unshare') is None or shutil.which('nft') is None:
        return False
    try:
        proc = subprocess.run(['unshare', '-rn', 'nft', 'list', 'ruleset'],
                              capture_output=True, text=True, timeout=20,
                              check=False)
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
    core = helper.HelperCore(state_file=args['state'],
                             nft_dropin_dir=args['dropin'],
                             proc_root=args['proc'])
    core.ssh_keep_open = lambda: False
    core.init_system = lambda: 'unknown'
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
    elif args['mode'] == 'firewall':
        core.set_inbound_allowed(args['allowed'])
        core.set_firewall_enabled(True)
        out['state'] = core.get_firewall_state()
        out['live'] = json.loads(nft('-j', 'list', 'ruleset').stdout)
        core.set_firewall_enabled(False)
        out['after'] = core.get_firewall_state()
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
    args.setdefault('dropin', str(tmp_path / 'nftables.d'))
    proc_dir = tmp_path / 'proc' / 'net'
    proc_dir.mkdir(parents=True, exist_ok=True)
    args.setdefault('proc', str(tmp_path / 'proc'))
    proc = subprocess.run(
        ['unshare', '-rn', sys.executable, str(script), str(HELPER_DIR),
         json.dumps(args)],
        capture_output=True, text=True, timeout=60, check=False)
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

    def test_firewall_json_parses_and_is_a_drop_policy(self, tmp_path):
        core = helper.HelperCore(proc_root=str(tmp_path))
        doc = core.render_inbound_json(
            {'ssh', 'syncthing', 'kdeconnect'}, keep_ssh=True)
        if not _kernel_accepts(tmp_path, doc):
            pytest.skip('kernel lacks an nft expression module (nft_ct)')
        out = _in_netns(tmp_path, mode='firewall',
                        allowed=['syncthing', 'kdeconnect'])
        state = out['state']
        assert state['enabled'] is True
        assert state['allowed']['syncthing'] is True
        assert state['allowed']['kdeconnect'] is True
        assert state['allowed']['ssh'] is False
        assert state['foreign_drop'] is False
        assert out['after']['enabled'] is False
        bridge = [item['rule'] for item in out['live']['nftables']
                  if 'rule' in item and item['rule'].get('comment')
                  == helper.NFT_CONTAINER_COMMENT]
        assert len(bridge) == 3
        assert all(rule['expr'][0]['match']['left']
                   == {'meta': {'key': 'iifname'}} for rule in bridge)

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
