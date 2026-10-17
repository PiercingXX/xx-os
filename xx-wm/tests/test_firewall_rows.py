"""Lane wm_firewall_rows: one row per feature; the firewall follows.

Derivation (features -> ruleset) for every feature combination, the
helper allowlist/argv, migration of the old preset rows' allow-state,
firewall-off behaviour (no table), and one test that no Settings page
renders the same feature twice (from the settings-map IA tree).
Fakes and tmp dirs only: no nft, services or network.
"""
import itertools
import json
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'helper'))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'common'))

import settings_page  # noqa: E402  (first: section modules bind its _Section)
import settings_firewall as firewall  # noqa: E402
import settings_connect as connect  # noqa: E402
import settings_services as services  # noqa: E402
import helper_client  # noqa: E402
import xx_wm_helper as helper  # noqa: E402

SRC = Path(__file__).parent.parent / 'launcher' / 'src'
DOCS = Path(__file__).parent.parent / 'docs'


def _completed(cmd, code=0, stdout='', stderr=''):
    return subprocess.CompletedProcess(cmd, code, stdout, stderr)


class FakeRun:
    """Recording argv-list stand-in (no shell, no real system)."""

    def __init__(self, status_out='', ruleset=''):
        self.cmds = []
        self.status_out = status_out
        self.ruleset = ruleset

    def __call__(self, argv):
        assert isinstance(argv, list), argv
        assert argv and all(isinstance(a, str) for a in argv), argv
        self.cmds.append(list(argv))
        if argv[:2] == ['ufw', 'status']:
            return _completed(argv, stdout=self.status_out)
        if argv[:3] == ['nft', 'list', 'ruleset']:
            return _completed(argv, stdout=self.ruleset)
        return _completed(argv)


def make_core(tmp_path, run=None, **over):
    kw = dict(sysfs=str(tmp_path / 'sys'),
              cgroup_root=str(tmp_path / 'cg'),
              mounts_file=str(tmp_path / 'mounts'),
              state_file=str(tmp_path / 'state.json'),
              nft_bin='nft',
              nft_dropin_dir=str(tmp_path / 'nftables.d'),
              run=run or FakeRun())
    kw.update(over)
    core = helper.HelperCore(**kw)
    core.nft_available = lambda: True
    return core


# ------------------------------------------------------- derivation (pure)


class TestDerivation:
    def test_features_match_presets(self):
        assert set(firewall.FEATURES) == set(firewall.PRESETS)
        assert set(firewall.FEATURES) == set(helper.INBOUND_PRESETS)

    def test_every_feature_combination(self):
        names = list(firewall.FEATURES)
        assert len(names) == 5
        seen = 0
        for bits in itertools.product((False, True), repeat=len(names)):
            features = dict(zip(names, bits))
            assert firewall.features_to_allowed(features) == {
                name for name, on in features.items() if on}
            seen += 1
        assert seen == 32

    def test_unknown_names_ignored(self):
        features = {'ssh': True, 'http-alt': True, 'gopher': True}
        assert firewall.features_to_allowed(features) == {'ssh'}

    def test_all_off_is_empty(self):
        assert firewall.features_to_allowed(
            {name: False for name in firewall.FEATURES}) == set()


class TestSubtitle:
    def test_names_what_is_allowed(self):
        assert firewall.subtitle_for_allowed(
            {'ssh', 'kdeconnect'},
            enabled=True, backend='nftables') == 'Allowing SSH, KDE Connect'

    def test_single_feature(self):
        assert firewall.subtitle_for_allowed(
            {'xxdb'}, enabled=True,
            backend='nftables') == 'Allowing Wireless debugging'

    def test_feature_order_not_sorted(self):
        text = firewall.subtitle_for_allowed(
            set(firewall.FEATURES), enabled=True, backend='nftables')
        assert text.index('SSH') < text.index('Wireless debugging') \
            < text.index('KDE Connect') < text.index('Syncthing') \
            < text.index('Nearby share')

    def test_empty_blocks(self):
        assert firewall.subtitle_for_allowed(
            set(), enabled=True,
            backend='nftables') == 'Blocking all incoming'

    def test_disabled_is_off(self):
        assert firewall.subtitle_for_allowed(
            {'ssh'}, enabled=False, backend='nftables') == 'Off'

    def test_no_backend_is_honest(self):
        text = firewall.subtitle_for_allowed(
            {'ssh'}, enabled=False, backend='none')
        assert 'no inbound firewall' in text


class TestMigrationRule:
    def test_allowed_before_counts_as_on(self):
        features = {name: False for name in firewall.FEATURES}
        assert firewall.migrate_legacy_allowed(['ssh'], features) == {'ssh'}

    def test_union_with_live_features(self):
        features = {name: False for name in firewall.FEATURES}
        features['nearby'] = True
        assert firewall.migrate_legacy_allowed(
            ['ssh', 'syncthing'], features) == {
            'ssh', 'syncthing', 'nearby'}

    def test_state_mapping_accepted_unknown_dropped(self):
        features = {name: False for name in firewall.FEATURES}
        legacy = {'ssh': True, 'xxdb': False, 'http-alt': True}
        assert firewall.migrate_legacy_allowed(legacy, features) == {'ssh'}

    def test_empty_legacy_derives_purely(self):
        features = {name: False for name in firewall.FEATURES}
        features['kdeconnect'] = True
        assert firewall.migrate_legacy_allowed([], features) == {
            'kdeconnect'}


class TestFeatureInputs:
    def test_user_service_active(self):
        assert connect.feature_active(
            {'installed': True, 'active': True}) is True
        assert connect.feature_active(
            {'installed': True, 'active': False}) is False
        assert connect.feature_active(
            {'installed': False, 'active': None}) is False
        assert connect.feature_active(None) is False

    def test_share_visible_from_own_config(self, tmp_path):
        assert firewall.share_visible(tmp_path) is False
        conf = tmp_path / '.config' / 'xx-share'
        conf.mkdir(parents=True)
        (conf / 'config.json').write_text(
            json.dumps({'visible': True}), encoding='utf-8')
        assert firewall.share_visible(tmp_path) is True
        (conf / 'config.json').write_text(
            json.dumps({'visible': False}), encoding='utf-8')
        assert firewall.share_visible(tmp_path) is False
        (conf / 'config.json').write_text('{not json', encoding='utf-8')
        assert firewall.share_visible(tmp_path) is False

    def test_probe_pins_beat_probing(self, tmp_path):
        features = firewall.probe_features(
            ssh=True, xxdb=True, kdeconnect=False, syncthing=True,
            nearby=False, home=tmp_path)
        assert features == {'ssh': True, 'xxdb': True,
                            'kdeconnect': False, 'syncthing': True,
                            'nearby': False}

    def test_probe_never_raises(self, tmp_path):
        features = firewall.probe_features(home=tmp_path)
        assert set(features) == set(firewall.FEATURES)
        assert all(isinstance(on, bool) for on in features.values())


# ------------------------------------------------------- helper allowlist


class FakeInvocation:
    def __init__(self):
        self.value = None
        self.error = None

    def return_value(self, value):
        self.value = value

    def return_dbus_error(self, name, message):
        self.error = (name, message)


class TestInboundAllowedPolicy:
    def test_nonempty_needs_admin_interactive(self):
        assert helper.policy_for(
            'SetInboundAllowed', (['ssh'],)) == (
            helper.ACTION_FW_OPEN, True)

    def test_empty_is_plain_restrict(self):
        assert helper.policy_for(
            'SetInboundAllowed', ([],)) == (
            helper.ACTION_FW_RESTRICT, False)

    def test_unknown_names_or_shapes_refused(self):
        assert helper.policy_for(
            'SetInboundAllowed', (['http-alt'],)) is None
        assert helper.policy_for('SetInboundAllowed', ('ssh',)) is None
        assert helper.policy_for('SetInboundAllowed', ()) is None

    def test_dispatch_authorizes_own_action(self, tmp_path, monkeypatch):
        run = FakeRun()
        core = make_core(tmp_path, run)
        monkeypatch.setattr(core, 'firewall_backend', lambda: 'nftables')
        seen = []

        def authorize(conn, sender, action, interactive, callback):
            seen.append((action, interactive))
            callback(True, 1000, None)

        service = helper.HelperService(
            connection=object(), core=core, authorize=authorize)
        invocation = FakeInvocation()
        service._on_method_call(
            service.connection, ':1.7', '/p', 'i', 'SetInboundAllowed',
            SimpleNamespace(unpack=lambda: (['ssh'],)), invocation)
        assert invocation.error is None
        assert seen == [(helper.ACTION_FW_OPEN, True)]
        assert core.load_inbound()['allowed'] == ['ssh']


class TestInboundAllowedArgv:
    def test_rejects_outside_allowlist(self, tmp_path):
        core = make_core(tmp_path, FakeRun())
        for names in (['http-alt'], ['ssh', 'gopher'], 'ssh', None):
            with pytest.raises(helper.HelperError) as exc:
                core.set_inbound_allowed(names)
            assert exc.value.dbus_name == helper.ERROR_INVALID_ARGS

    def test_nft_reconciles_owned_table_only(self, tmp_path, monkeypatch):
        run = FakeRun()
        core = make_core(tmp_path, run)
        monkeypatch.setattr(core, 'firewall_backend', lambda: 'nftables')
        assert core.set_inbound_allowed(['ssh', 'nearby']) == [
            'nearby', 'ssh']
        assert run.cmds
        for cmd in run.cmds:
            assert isinstance(cmd, list)
            joined = ' '.join(cmd)
            assert 'table inet filter' not in joined
            assert 'table inet mangle' not in joined
            assert 'table inet nat' not in joined
        assert any(cmd[:3] == ['nft', 'add', 'table']
                   and 'xx_wm' in cmd for cmd in run.cmds)
        assert any('tcp dport 22' in ' '.join(cmd) for cmd in run.cmds)
        assert any('tcp dport 53317' in ' '.join(cmd)
                   for cmd in run.cmds)

    def test_nft_persists_and_drops_in(self, tmp_path, monkeypatch):
        run = FakeRun()
        core = make_core(tmp_path, run)
        monkeypatch.setattr(core, 'firewall_backend', lambda: 'nftables')
        core.set_inbound_allowed(['xxdb'])
        saved = json.loads(
            Path(core.inbound_file()).read_text(encoding='utf-8'))
        assert saved['allowed'] == ['xxdb']
        assert saved['migrated'] is True
        dropin = Path(core.nft_dropin_dir) / helper.NFT_DROPIN_NAME
        assert 'tcp dport 37000-37999 accept' in dropin.read_text(
            encoding='utf-8')

    def test_ufw_reconcile_argv(self, tmp_path, monkeypatch):
        status = ('Status: active\n22/tcp ALLOW Anywhere\n'
                  '1714:1764/tcp ALLOW Anywhere\n')
        run = FakeRun(status_out=status)
        core = make_core(tmp_path, run)
        monkeypatch.setattr(core, 'firewall_backend', lambda: 'ufw')
        assert core.set_inbound_allowed(['nearby']) == ['nearby']
        assert ['ufw', 'allow', '53317/tcp'] in run.cmds
        assert ['ufw', 'allow', '53317/udp'] in run.cmds
        assert ['ufw', '--force', 'delete', 'allow',
                '22/tcp'] in run.cmds
        assert ['ufw', '--force', 'delete', 'allow',
                '1714:1764/tcp'] in run.cmds

    def test_no_backend_fails(self, tmp_path, monkeypatch):
        core = make_core(tmp_path, FakeRun())
        monkeypatch.setattr(core, 'firewall_backend', lambda: 'none')
        with pytest.raises(helper.HelperError):
            core.set_inbound_allowed(['ssh'])


class TestMigration:
    def test_first_write_unions_old_rows(self, tmp_path, monkeypatch):
        run = FakeRun()
        core = make_core(tmp_path, run)
        monkeypatch.setattr(core, 'firewall_backend', lambda: 'nftables')
        Path(core.inbound_file()).write_text(
            json.dumps({'enabled': True,
                        'allowed': ['ssh', 'syncthing']}),
            encoding='utf-8')
        assert core.set_inbound_allowed([]) == ['ssh', 'syncthing']
        assert core.load_inbound()['migrated'] is True
        assert any('tcp dport 22' in ' '.join(cmd) for cmd in run.cmds)
        assert any('tcp dport 22000' in ' '.join(cmd)
                   for cmd in run.cmds)
        assert all(cmd[0] in ('nft', 'ufw') for cmd in run.cmds)

    def test_second_write_derives_purely(self, tmp_path, monkeypatch):
        run = FakeRun()
        core = make_core(tmp_path, run)
        monkeypatch.setattr(core, 'firewall_backend', lambda: 'nftables')
        Path(core.inbound_file()).write_text(
            json.dumps({'enabled': True,
                        'allowed': ['ssh', 'syncthing']}),
            encoding='utf-8')
        core.set_inbound_allowed([])
        assert core.set_inbound_allowed(['nearby']) == ['nearby']

    def test_matches_ui_rule(self, tmp_path, monkeypatch):
        run = FakeRun()
        core = make_core(tmp_path, run)
        monkeypatch.setattr(core, 'firewall_backend', lambda: 'nftables')
        legacy = ['ssh', 'xxdb']
        Path(core.inbound_file()).write_text(
            json.dumps({'enabled': None, 'allowed': legacy}),
            encoding='utf-8')
        features = {name: False for name in firewall.FEATURES}
        features['syncthing'] = True
        derived = sorted(firewall.features_to_allowed(features))
        assert core.set_inbound_allowed(derived) == sorted(
            firewall.migrate_legacy_allowed(legacy, features))

    def test_old_keys_left_alone(self, tmp_path, monkeypatch):
        run = FakeRun()
        core = make_core(tmp_path, run)
        monkeypatch.setattr(core, 'firewall_backend', lambda: 'nftables')
        Path(core.inbound_file()).write_text(
            json.dumps({'enabled': True, 'allowed': ['ssh']}),
            encoding='utf-8')
        core.set_inbound_allowed(['nearby'])
        saved = json.loads(
            Path(core.inbound_file()).read_text(encoding='utf-8'))
        assert saved['enabled'] is True
        assert saved['allowed'] == ['nearby', 'ssh']


class TestFirewallOff:
    def test_disable_removes_live_table_and_dropin(
            self, tmp_path, monkeypatch):
        run = FakeRun()
        core = make_core(tmp_path, run)
        monkeypatch.setattr(core, 'firewall_backend', lambda: 'nftables')
        core.set_inbound_allowed(['ssh'])
        dropin = Path(core.nft_dropin_dir) / helper.NFT_DROPIN_NAME
        assert dropin.is_file()
        assert core.set_firewall_enabled(False) is False
        assert ['nft', 'delete', 'table', 'inet', 'xx_wm'] in [
            cmd for cmd in run.cmds]
        assert not dropin.exists()
        assert core.load_inbound()['enabled'] is False

    def test_feature_write_while_off_persists_without_table(
            self, tmp_path, monkeypatch):
        run = FakeRun()
        core = make_core(tmp_path, run)
        monkeypatch.setattr(core, 'firewall_backend', lambda: 'nftables')
        Path(core.inbound_file()).write_text(
            json.dumps({'enabled': False, 'allowed': [],
                        'migrated': True}),
            encoding='utf-8')
        assert core.set_inbound_allowed(['ssh']) == ['ssh']
        assert core.load_inbound()['allowed'] == ['ssh']
        assert ['nft', 'add', 'table', 'inet', 'xx_wm'] not in [
            cmd for cmd in run.cmds]
        dropin = Path(core.nft_dropin_dir) / helper.NFT_DROPIN_NAME
        assert not dropin.exists()

    def test_boot_reapply_keeps_off(self, tmp_path, monkeypatch):
        run = FakeRun()
        core = make_core(tmp_path, run)
        monkeypatch.setattr(core, 'firewall_backend', lambda: 'nftables')
        Path(core.inbound_file()).write_text(
            json.dumps({'enabled': False, 'allowed': ['ssh'],
                        'migrated': True}),
            encoding='utf-8')
        core.apply_saved()
        assert not any(cmd[:3] == ['nft', 'add', 'table']
                       for cmd in run.cmds)


class TestHelperClientShape:
    def test_allowed_params(self, monkeypatch):
        from gi.repository import GLib
        calls = []

        def fake_call(method, params, on_done):
            calls.append((method, params))
            on_done(True, None)

        monkeypatch.setattr(helper_client, '_call_helper', fake_call)
        done = []
        helper_client.set_inbound_allowed(
            ['ssh'], lambda ok, res: done.append((ok, res)))
        assert done == [(True, None)]
        (method, params), = calls
        assert method == 'SetInboundAllowed'
        assert isinstance(params, GLib.Variant)
        assert params.get_type_string() == '(as)'
        assert params.unpack() == (['ssh'],)


# ------------------------------------------------------- reapply (follow)


def _sync_bg(work, on_done):
    try:
        result = work()
    except Exception:
        result = None
    on_done(result)


class TestReapply:
    def _client(self, monkeypatch, state, sets):
        def fake_get(on_done):
            on_done(True, dict(state))

        def fake_set(names, on_done):
            sets.append(list(names))
            on_done(True, None)

        monkeypatch.setattr(helper_client, 'get_firewall_state', fake_get)
        monkeypatch.setattr(helper_client, 'set_inbound_allowed', fake_set)

    def test_skips_write_when_matching(self, monkeypatch):
        import settings_page as page
        monkeypatch.setattr(page, '_run_bg', _sync_bg)
        monkeypatch.setattr(
            firewall, 'probe_features',
            lambda xxdb_enabled=False: {'ssh': True, 'xxdb': False,
                                        'kdeconnect': False,
                                        'syncthing': False,
                                        'nearby': False})
        sets = []
        self._client(monkeypatch, {'backend': 'nftables', 'enabled': True,
                                   'allowed': {'ssh': True},
                                   'foreign_drop': False}, sets)
        done = []
        firewall.reapply(SimpleNamespace(xxdb_controller=None),
                         lambda ok, res: done.append((ok, res)))
        assert sets == []
        assert done == [(True, None)]

    def test_writes_derived_when_drifted(self, monkeypatch):
        import settings_page as page
        monkeypatch.setattr(page, '_run_bg', _sync_bg)
        monkeypatch.setattr(
            firewall, 'probe_features',
            lambda xxdb_enabled=False: {'ssh': False, 'xxdb': True,
                                        'kdeconnect': False,
                                        'syncthing': False,
                                        'nearby': False})
        sets = []
        self._client(monkeypatch, {'backend': 'nftables', 'enabled': True,
                                   'allowed': {'ssh': True},
                                   'foreign_drop': False}, sets)
        firewall.reapply(SimpleNamespace(xxdb_controller=None))
        assert sets == [['xxdb']]

    def test_programs_nothing_without_backend(self, monkeypatch):
        import settings_page as page
        monkeypatch.setattr(page, '_run_bg', _sync_bg)
        monkeypatch.setattr(
            firewall, 'probe_features',
            lambda xxdb_enabled=False: {'ssh': True, 'xxdb': False,
                                        'kdeconnect': False,
                                        'syncthing': False,
                                        'nearby': False})
        sets = []
        self._client(monkeypatch, {'backend': 'none', 'enabled': False,
                                   'allowed': {}, 'foreign_drop': False},
                     sets)
        done = []
        firewall.reapply(SimpleNamespace(xxdb_controller=None),
                         lambda ok, res: done.append((ok, res)))
        assert sets == []
        assert done == [(True, None)]

    def test_xxdb_switch_counts_as_on(self, monkeypatch):
        import settings_page as page
        monkeypatch.setattr(page, '_run_bg', _sync_bg)
        seen = {}
        monkeypatch.setattr(
            firewall, 'probe_features',
            lambda xxdb_enabled=False: seen.update(
                xxdb=xxdb_enabled) or {'ssh': False, 'xxdb': xxdb_enabled,
                                       'kdeconnect': False,
                                       'syncthing': False, 'nearby': False})
        sets = []
        self._client(monkeypatch, {'backend': 'nftables', 'enabled': True,
                                   'allowed': {}, 'foreign_drop': False},
                     sets)
        ctrl = SimpleNamespace(enabled=False, starting=True)
        firewall.reapply(SimpleNamespace(xxdb_controller=ctrl))
        assert seen == {'xxdb': True}
        assert sets == [['xxdb']]


class TestServiceFollowHooks:
    def test_ssh_action_reapplies(self, monkeypatch):
        import helper_client as client
        monkeypatch.setattr(
            client, 'manage_service',
            lambda name, action, on_done: on_done(True, None))
        section = services.ServicesSection()
        section.refresh = lambda win: None
        calls = []
        monkeypatch.setattr(
            firewall, 'reapply',
            lambda win, on_done=None: calls.append(win))
        entry = next(e for e in services.SERVICES if e.key == 'ssh')
        win = SimpleNamespace(_show_status=lambda *a: None)
        section._apply(win, entry, 'start')
        assert calls == [win]

    def test_unrelated_service_skips_reapply(self, monkeypatch):
        import helper_client as client
        monkeypatch.setattr(
            client, 'manage_service',
            lambda name, action, on_done: on_done(True, None))
        section = services.ServicesSection()
        section.refresh = lambda win: None
        calls = []
        monkeypatch.setattr(
            firewall, 'reapply',
            lambda win, on_done=None: calls.append(win))
        entry = next(e for e in services.SERVICES if e.key == 'bluetooth')
        win = SimpleNamespace(_show_status=lambda *a: None)
        section._apply(win, entry, 'restart')
        assert calls == []

    def test_user_unit_action_reapplies(self, monkeypatch):
        monkeypatch.setattr(
            settings_page, '_run_bg',
            lambda work, done: done(work()))
        monkeypatch.setattr(
            connect, 'set_user_unit',
            lambda unit, action, run=None: None)
        section = services.ServicesSection()
        section.refresh = lambda win: None
        calls = []
        monkeypatch.setattr(
            firewall, 'reapply',
            lambda win, on_done=None: calls.append(win))
        entry = next(e for e in services.USER_SERVICES
                     if e.key == 'syncthing')
        win = SimpleNamespace(_show_status=lambda *a: None)
        section._apply_user(win, entry, 'start')
        assert calls == [win]


# ------------------------------------------- no feature rendered twice


class _FakeWidget:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.children = []
        self.classes = []
        self.props = {}
        self.handlers = {}

    def __getattr__(self, name):
        if name.startswith('set_'):
            return lambda *args: self.props.__setitem__(
                name[4:], args[0] if len(args) == 1 else args)
        raise AttributeError(name)

    @classmethod
    def new(cls, *args, **kwargs):
        return cls(*args, **kwargs)

    def append(self, child):
        self.children.append(child)

    def remove(self, child):
        if child in self.children:
            self.children.remove(child)

    def get_first_child(self):
        return self.children[0] if self.children else None

    def get_next_sibling(self):
        return None

    def set_child(self, child):
        self.child = child

    def get_child(self):
        return getattr(self, 'child', None) or _FakeWidget()

    def add_css_class(self, name):
        self.classes.append(name)

    def connect(self, signal, callback, *args):
        self.handlers[signal] = (callback, args)
        return 1

    def set_text(self, value):
        self.props['text'] = value
        if isinstance(self, _FakeLabel):
            self.props['label'] = value

    def get_text(self):
        return self.props.get('text', self.kwargs.get('label', ''))

    def set_label(self, value):
        self.props['label'] = value

    def set_visible(self, value):
        self.props['visible'] = bool(value)

    def set_active(self, value):
        self.props['active'] = bool(value)

    def get_active(self):
        return self.props.get('active', False)


class _FakeLabel(_FakeWidget):
    pass


class _FakeButton(_FakeWidget):
    pass


class _FakeBox(_FakeWidget):
    pass


class _FakeSwitch(_FakeWidget):
    pass


@pytest.fixture()
def fake_gi():
    gi = types.ModuleType('gi')
    gi.require_version = lambda namespace, version: None
    repo = types.ModuleType('gi.repository')
    gi.repository = repo

    def make(name):
        return types.ModuleType('gi.repository.%s' % name)

    adw, gdk, glib, gtk, pango, gio = (
        make('Adw'), make('Gdk'), make('GLib'), make('Gtk'),
        make('Pango'), make('Gio'),
    )

    class _FakeImage(_FakeWidget):
        @classmethod
        def new_from_icon_name(cls, name):
            image = cls()
            image.props['icon_name'] = name
            return image

    gtk.Box = _FakeBox
    gtk.Label = _FakeLabel
    gtk.Button = _FakeButton
    gtk.Image = _FakeImage
    gtk.Switch = _FakeSwitch
    gtk.Align = SimpleNamespace(START=0, END=1, CENTER=2, FILL=3)
    gtk.Orientation = SimpleNamespace(HORIZONTAL=0, VERTICAL=1)
    gtk.PolicyType = SimpleNamespace(NEVER=0, AUTOMATIC=1)
    pango.WrapMode = SimpleNamespace(WORD=0, WORD_CHAR=1, CHAR=2)
    pango.EllipsizeMode = SimpleNamespace(NONE=0, START=1, MIDDLE=2, END=3)
    repo.Adw, repo.Gdk, repo.GLib, repo.Gtk, repo.Pango, repo.Gio = (
        adw, gdk, glib, gtk, pango, gio)
    glib.idle_add = lambda callback, *args: (callback(*args), 0)[1]
    glib.timeout_add_seconds = lambda _s, callback, *a: (callback(*a), 0)[1]

    installed = {
        'gi': gi, 'gi.repository': repo,
        'gi.repository.Adw': adw, 'gi.repository.Gdk': gdk,
        'gi.repository.GLib': glib, 'gi.repository.Gtk': gtk,
        'gi.repository.Pango': pango, 'gi.repository.Gio': gio,
    }
    saved = {name: sys.modules.get(name) for name in installed}
    import xx_ui
    saved_widgets = sys.modules.pop('xx_ui.widgets', None)
    saved_widgets_attr = xx_ui.__dict__.pop('widgets', None)
    sys.modules.update(installed)
    try:
        yield gtk
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        if saved_widgets is not None:
            sys.modules['xx_ui.widgets'] = saved_widgets
        else:
            sys.modules.pop('xx_ui.widgets', None)
        if saved_widgets_attr is not None:
            xx_ui.widgets = saved_widgets_attr
        elif 'widgets' in xx_ui.__dict__:
            del xx_ui.__dict__['widgets']


def _bare_win(**overrides):
    win = SimpleNamespace(
        config=SimpleNamespace(charge_limit=None),
        xxdb_controller=None,
        _show_status=lambda *args: None,
        _refresh_xxdb_section=lambda: None,
    )
    for key, value in overrides.items():
        setattr(win, key, value)
    return win


def _walk(widget):
    yield widget
    if isinstance(widget, _FakeWidget):
        for child in widget.children:
            yield from _walk(child)
        direct = getattr(widget, 'child', None)
        if isinstance(direct, _FakeWidget):
            yield from _walk(direct)


def _texts(widgets):
    found = []
    for widget in widgets:
        for node in _walk(widget):
            label = None
            if isinstance(node, (_FakeLabel, _FakeButton)):
                label = (node.props.get('label')
                         or node.kwargs.get('label'))
            if label:
                found.append(label)
    return found


def _section_texts(key, fake_gi):
    """Rendered titles for one settings-map tree section."""
    win = _bare_win()
    if key == 'firewall':
        section = firewall.FirewallSection()
        built = section.build(win)
        section._show_state(win, {'backend': 'nftables', 'enabled': True,
                                  'allowed': {'ssh': True},
                                  'foreign_drop': False})
        return _texts(built)
    if key == 'nearby':
        import settings_page as page
        cls = next(cls for k, cls in page.SETTINGS_SECTIONS if k == key)
        return _texts(cls().build(win))
    if key == 'developer':
        page = settings_page.SettingsPage(win)
        return _texts([page._build_developer_card()])
    if key == 'services':
        section = services.ServicesSection()
        built = section.build(win)
        section._render(win, {
            'init': 'systemd',
            'states': {'ssh': ('active', 'enabled')},
            'eg25': False, 'failed': [],
            'ssh_net': {'listening': True, 'address': '192.0.2.10'},
            'ssh_pw': False, 'adbd': False,
            'user': {
                'kdeconnect': {'installed': True, 'active': True,
                               'enabled': False, 'active_state': 'active',
                               'enabled_state': 'disabled',
                               'devices': [], 'error': None},
                'syncthing': {'installed': True, 'active': True,
                              'enabled': False, 'active_state': 'active',
                              'enabled_state': 'disabled',
                              'devices': [], 'error': None}},
        })
        return _texts(built)
    raise KeyError(key)


class TestOneRowPerFeature:
    FEATURE_SECTIONS = {
        'SSH server': 'services',
        'Wireless debugging': 'developer',
        'KDE Connect': 'services',
        'Syncthing': 'services',
        'Nearby share': 'nearby',
    }

    def test_each_feature_in_exactly_one_section(self, fake_gi):
        tree = {key: sections
                for key, _title, sections in settings_page.SETTINGS_CATEGORIES}
        assert {'developer', 'services', 'firewall'} <= set(tree['system'])
        by_section = {key: _section_texts(key, fake_gi)
                      for key in ('developer', 'services', 'firewall',
                                  'nearby')}
        for title, home in self.FEATURE_SECTIONS.items():
            owners = [key for key, texts in by_section.items()
                      if title in texts]
            assert owners == [home], (title, owners)

    def test_firewall_is_one_row(self, fake_gi):
        texts = _section_texts('firewall', fake_gi)
        assert 'Firewall' in texts
        for title in self.FEATURE_SECTIONS:
            assert title not in texts
        assert 'Allowing SSH' in texts

    def test_deep_link_still_lands(self):
        assert settings_page.category_for('firewall') == 'system'
        assert 'firewall' in settings_page._SETTINGS_SECTION_KEYS

    def test_map_doc_has_no_firewall_overlap(self):
        text = (DOCS / 'settings-map.md').read_text(encoding='utf-8')
        assert '`firewall`' in text
        overlap = text.split('## Still overlapping')[1]
        assert 'SSH' not in overlap
        assert 'Wireless debugging' not in overlap
        assert 'Nearby share' not in overlap
