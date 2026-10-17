"""Lane wm_services tests: sharing backends with fake runners.

Firewall, SSH, wireless debugging (ADB), KDE Connect and Syncthing.
Every backend runs with injected fakes (never real services, packages,
the firewall or the network); the helper core runs against tmp dirs
and a recording ``nft``; UI logic (visibility, subtitles, inline
errors) is tested without a display.
"""
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'helper'))

import settings_page  # noqa: E402  (first: section modules bind its _Section)
import settings_connect as connect  # noqa: E402
import settings_firewall as firewall  # noqa: E402
import settings_services as services  # noqa: E402
import xx_wm_helper as helper  # noqa: E402

SRC = Path(__file__).parent.parent / 'launcher' / 'src'
APPS_SH = Path(__file__).parent.parent / 'scripts' / 'apps.sh'


def _completed(cmd, code=0, stdout='', stderr=''):
    return subprocess.CompletedProcess(cmd, code, stdout, stderr)


class FakeRun:
    """Recording argv-list stand-in (no shell, no real system)."""

    def __init__(self, mapping=None):
        self.cmds = []
        self.mapping = dict(mapping or {})

    def __call__(self, argv, timeout=10):
        assert isinstance(argv, list), argv
        assert argv and all(isinstance(a, str) for a in argv), argv
        self.cmds.append((list(argv), timeout))
        for key, proc in self.mapping.items():
            if argv[:len(key)] == list(key):
                return proc
        return _completed(argv)


def _which_map(present):
    def which(name):
        return '/usr/bin/%s' % name if name in present else None
    return which


def make_core(tmp_path, run=None, **over):
    kw = dict(state_file=str(tmp_path / 'state.json'),
              nft_dropin_dir=str(tmp_path / 'nftables.d'),
              run=run or FakeRun())
    kw.update(over)
    return helper.HelperCore(**kw)


# ------------------------------------------------------- per-distro packages


class TestPackagesPerDistro:
    def test_kdeconnect_syncthing_everywhere(self):
        for feature in ('kdeconnect', 'syncthing'):
            assert connect.packages_for(feature, 'apk') == (feature,)
            assert connect.packages_for(feature, 'apt') == (feature,)
            assert connect.packages_for(feature, 'pacman') == (feature,)

    def test_ssh_names(self):
        assert connect.packages_for('ssh', 'apt') == ('openssh-server',)
        assert connect.packages_for('ssh', 'apk') == ('openssh',)
        assert connect.packages_for('ssh', 'pacman') == ('openssh',)

    def test_adbd_apt_only(self):
        assert connect.packages_for('adbd', 'apt') == (
            'adbd', 'android-tools-adbd')
        assert connect.packages_for('adbd', 'apk') == ()
        assert connect.packages_for('adbd', 'pacman') == ()

    def test_unknown_feature_or_distro_rejected(self):
        with pytest.raises(KeyError):
            connect.packages_for('http-alt', 'apt')
        with pytest.raises(KeyError):
            connect.packages_for('ssh', 'yum')


# ------------------------------------------------------- per-distro commands


class TestUserUnitArgv:
    def test_start_stop_restart_enable_disable(self):
        assert connect.user_unit_argv(
            'kdeconnectd.service', 'start') == [
            'systemctl', '--user', 'start', 'kdeconnectd.service']
        assert connect.user_unit_argv(
            'syncthing.service', 'enable') == [
            'systemctl', '--user', 'enable', 'syncthing.service']
        for action in ('start', 'stop', 'restart', 'enable', 'disable'):
            argv = connect.user_unit_argv('syncthing.service', action)
            assert argv[:3] == ['systemctl', '--user', action]

    def test_rejects_unknown_unit_or_action(self):
        for unit, action in (('sshd.service', 'start'),
                             ('kdeconnectd.service', 'mask'),
                             ('xx-wm', 'start'),
                             ('syncthing.service', 'reenable')):
            with pytest.raises(ValueError):
                connect.user_unit_argv(unit, action)

    def test_never_a_shell_string(self):
        argv = connect.user_unit_argv('syncthing.service', 'restart')
        assert isinstance(argv, list)
        assert all(isinstance(a, str) for a in argv)


class TestAdbdServiceArgv:
    def test_systemd_argv(self, tmp_path, monkeypatch):
        run = FakeRun()
        core = make_core(tmp_path, run)
        monkeypatch.setattr(core, 'init_system', lambda: 'systemd')
        monkeypatch.setattr(core, 'resolve_unit',
                            lambda key: 'adbd.service')
        assert core.manage_service('adbd', 'start') == 'start'
        assert run.cmds[-1][0] == ['systemctl', 'start', 'adbd.service']

    def test_openrc_argv(self, tmp_path, monkeypatch):
        run = FakeRun()
        core = make_core(tmp_path, run)
        monkeypatch.setattr(core, 'init_system', lambda: 'openrc')
        assert core.manage_service('adbd', 'stop') == 'stop'
        assert run.cmds[-1][0] == ['rc-service', 'adbd', 'stop']

    def test_set_user_unit_success_and_failure(self):
        run = FakeRun()
        connect.set_user_unit('syncthing.service', 'enable', run)
        assert run.cmds[-1][0] == ['systemctl', '--user', 'enable',
                                   'syncthing.service']
        failing = FakeRun({('systemctl',): _completed(
            ['systemctl'], code=1, stderr='Failed to connect to bus')})
        with pytest.raises(OSError, match='Failed to connect to bus'):
            connect.set_user_unit('syncthing.service', 'start', failing)


# ------------------------------------------------------------- status parsing


class TestSystemctlParsing:
    def test_is_active(self):
        assert connect.parse_is_active('active\n', 0) == (True, 'active')
        assert connect.parse_is_active('inactive\n', 3) == (False, 'inactive')
        assert connect.parse_is_active('failed\n', 3) == (False, 'failed')
        assert connect.parse_is_active('', 1) == (None, '')

    def test_is_enabled(self):
        assert connect.parse_is_enabled('enabled\n', 0) == (True, 'enabled')
        assert connect.parse_is_enabled('disabled\n', 1) == (
            False, 'disabled')
        assert connect.parse_is_enabled('', 1) == (None, '')


class TestKdeconnectParsing:
    def test_devices_listed_headers_skipped(self):
        out = ('Available devices:\n'
               '  My Phone (a1b2c3)\n'
               '  Tablet (d4e5f6)\n')
        assert connect.parse_kdeconnect_devices(out) == [
            'My Phone (a1b2c3)', 'Tablet (d4e5f6)']

    def test_no_devices_is_empty(self):
        assert connect.parse_kdeconnect_devices(
            'No devices found\n') == []
        assert connect.parse_kdeconnect_devices('') == []


class TestSshdConfigParsing:
    def test_last_wins_comments_ignored(self):
        text = ('#PasswordAuthentication yes\n'
                'PasswordAuthentication no\n')
        assert connect.parse_sshd_password_allowed(text) is False
        text = ('PasswordAuthentication no\n'
                'PasswordAuthentication yes\n')
        assert connect.parse_sshd_password_allowed(text) is True

    def test_unknown_when_absent(self):
        assert connect.parse_sshd_password_allowed('# empty\n') is None
        assert connect.parse_sshd_password_allowed('') is None


class TestSshNetParsing:
    SS = ('Netid State Recv-Q Local Address:Port\n'
          'tcp LISTEN 0 0.0.0.0:22 0.0.0.0:*\n'
          'tcp LISTEN 0 [::]:22000 [::]:*\n')

    def test_port_22_found_others_not(self):
        assert connect.ssh_port_listening(self.SS) is True
        assert connect.ssh_port_listening(self.SS, port=5555) is False
        assert connect.ssh_port_listening('Netid State\n') is False

    def test_first_lan_address_skips_loopback(self):
        brief = ('lo UNKNOWN 127.0.0.1/8 ::1/128\n'
                 'wlan0 UP 192.0.2.10/24 fe80::1/64\n')
        assert connect.first_lan_address(brief) == '192.0.2.10'
        assert connect.first_lan_address('lo UNKNOWN 127.0.0.1/8\n') is None


# ------------------------------------------------------------------- probes


def _user_mapping(active='active', enabled='enabled', devices=''):
    return {
        ('systemctl', '--user', 'is-active'): _completed(
            ['systemctl'], code=0 if active == 'active' else 3,
            stdout=active + '\n'),
        ('systemctl', '--user', 'is-enabled'): _completed(
            ['systemctl'], code=0 if enabled == 'enabled' else 1,
            stdout=enabled + '\n'),
        ('kdeconnect-cli',): _completed(
            ['kdeconnect-cli'], stdout=devices),
    }


class TestProbes:
    def test_kdeconnect_full_state(self):
        run = FakeRun(_user_mapping(
            devices='Available devices:\n  My Phone (a1)\n'))
        info = connect.kdeconnect_probe(
            run, _which_map(('kdeconnect-cli',)))
        assert info['installed'] is True
        assert (info['active'], info['enabled']) == (True, True)
        assert info['active_state'] == 'active'
        assert info['enabled_state'] == 'enabled'
        assert info['devices'] == ['My Phone (a1)']
        assert info['error'] is None

    def test_not_installed_when_binary_missing(self):
        run = FakeRun()
        for probe in (connect.kdeconnect_probe, connect.syncthing_probe):
            info = probe(run, _which_map(()))
            assert info['installed'] is False
            assert run.cmds == []

    def test_run_failure_becomes_error_not_raise(self):
        def boom(argv, timeout=10):
            raise OSError('no bus')
        info = connect.syncthing_probe(boom, _which_map(('syncthing',)))
        assert info['installed'] is True
        assert info['error'] == 'no bus'
        assert info['active'] is None

    def test_adbd_hidden_when_unavailable(self):
        assert connect.adbd_probe(
            FakeRun(), _which_map(())) is None
        assert connect.adbd_available(_which_map(())) is False

    def test_adbd_present_reports_state(self):
        mapping = {('systemctl', 'is-active'): _completed(
            ['systemctl'], code=0, stdout='active\n')}
        info = connect.adbd_probe(FakeRun(mapping),
                                  _which_map(('adbd',)))
        assert info == {'installed': True, 'active': True, 'enabled': None,
                        'active_state': 'active', 'enabled_state': None,
                        'error': None}

    def test_ssh_net_probe(self):
        mapping = {
            ('ss',): _completed(['ss'], stdout=TestSshNetParsing.SS),
            ('ip',): _completed(
                ['ip'], stdout='wlan0 UP 192.0.2.10/24\n'),
        }
        assert connect.ssh_net_probe(FakeRun(mapping)) == {
            'listening': True, 'address': '192.0.2.10'}

    def test_ssh_password_file(self, tmp_path):
        conf = tmp_path / 'sshd_config'
        conf.write_text('PasswordAuthentication no\n',
                        encoding='utf-8')
        assert connect.ssh_password_allowed(str(conf)) is False
        assert connect.ssh_password_allowed(
            str(tmp_path / 'missing')) is None


# ----------------------------------------------------------------- subtitles


class TestSubtitles:
    def test_user_service_states(self):
        assert connect.describe_user_service(
            False, None, None) == 'Not installed'
        assert connect.describe_user_service(
            True, None, None,
            error='no bus') == 'Unavailable · no bus'
        assert connect.describe_user_service(
            True, 'active', 'enabled',
            ['My Phone (a1)']) == 'On, starts at boot · My Phone (a1)'
        assert connect.describe_user_service(
            True, 'inactive', 'disabled') == 'Off'
        assert connect.describe_user_service(
            True, None, None) == 'Unavailable'

    def test_ssh_subtitle(self):
        assert connect.describe_ssh(
            'On, starts at boot', True, '192.0.2.10',
            False) == 'On, starts at boot · 192.0.2.10:22 · key-only'
        assert connect.describe_ssh(
            'On', True, None, True) == 'On · port 22 open · password allowed'
        assert connect.describe_ssh('Off', False, None,
                                    None) == 'Off'


# ------------------------------------------- nft ruleset (owned table only)


class TestNftOwnedTable:
    def test_shell_render_uses_owned_table(self):
        text = firewall.render_nft_rules({'ssh', 'kdeconnect', 'syncthing'})
        assert 'table inet xx_wm {' in text
        assert 'tcp dport 22 accept' in text
        assert 'tcp dport 1714-1764 accept' in text
        assert 'tcp dport 22000 accept' in text
        assert 'xxwm-inbound' not in text
        assert 'table inet filter' not in text

    def test_helper_render_matches_shell(self):
        core = make_core(Path('/tmp'), FakeRun())
        assert core.render_inbound_nft(
            {'ssh'}) == firewall.render_nft_rules({'ssh'})

    def test_reconcile_touches_only_owned_tables(self, tmp_path,
                                                 monkeypatch):
        run = FakeRun()
        core = make_core(tmp_path, run)
        monkeypatch.setattr(core, 'firewall_backend', lambda: 'nftables')
        core.set_inbound_preset('ssh', True)
        assert run.cmds
        for cmd, _timeout in run.cmds:
            assert isinstance(cmd, list)
            joined = ' '.join(cmd)
            assert 'table inet filter' not in joined
            assert 'table inet mangle' not in joined
            assert 'table inet nat' not in joined
        assert ['nft', 'delete', 'table', 'inet', 'xxwm-inbound'] in [
            cmd for cmd, _timeout in run.cmds]
        assert any(cmd[:3] == ['nft', 'add', 'table']
                   and 'xx_wm' in cmd for cmd, _timeout in run.cmds)

    def test_state_reads_owned_table(self, tmp_path, monkeypatch):
        ruleset = ('table inet xx_wm {\n'
                   '\tchain input {\n'
                   '\t\ttcp dport 22 accept\n'
                   '\t}\n}\n')
        run = FakeRun({('nft', 'list'): _completed(
            ['nft'], stdout=ruleset)})
        core = make_core(tmp_path, run)
        monkeypatch.setattr(core, 'firewall_backend', lambda: 'nftables')
        state = core.get_firewall_state()
        assert state['backend'] == 'nftables'
        assert state['enabled'] is True
        assert state['allowed']['ssh'] is True
        assert state['allowed']['syncthing'] is False

    def test_nftables_preferred_over_ufw(self, tmp_path, monkeypatch):
        import shutil
        core = make_core(tmp_path, FakeRun())
        monkeypatch.setattr(core, 'nft_available', lambda: True)
        monkeypatch.setattr(shutil, 'which',
                            lambda name: '/usr/bin/%s' % name)
        assert core.firewall_backend() == 'nftables'
        monkeypatch.setattr(core, 'nft_available', lambda: False)
        assert core.firewall_backend() == 'ufw'


# ------------------------------------------------------- helper allowlist


class TestHelperAllowlist:
    def test_catalogs_mirror_including_adbd(self):
        assert sorted(helper.SERVICE_ALLOW) == sorted(
            entry.key for entry in services.SERVICES)
        assert 'adbd' in helper.SERVICE_ALLOW
        table = helper.SERVICE_UNITS['adbd']
        entry = next(e for e in services.SERVICES if e.key == 'adbd')
        assert tuple(table['systemd']) == tuple(entry.systemd_units)
        assert tuple(table['openrc']) == tuple(entry.openrc_names)

    def test_adbd_start_needs_admin_interactive(self):
        assert helper.policy_for(
            'ManageService', ('adbd', 'start')) == (
            helper.ACTION_SERVICE_REMOTE, True)
        assert helper.policy_for(
            'ManageService', ('adbd', 'enable')) == (
            helper.ACTION_SERVICE_REMOTE, True)

    def test_adbd_stop_is_plain_service_action(self):
        assert helper.policy_for(
            'ManageService', ('adbd', 'stop')) == (
            helper.ACTION_SERVICE, False)

    def test_user_units_outside_helper_allowlist(self):
        for name in ('kdeconnect', 'syncthing', 'kdeconnectd',
                     'syncthing.service'):
            with pytest.raises(helper.HelperError) as exc:
                make_core(Path('/tmp'), FakeRun()).manage_service(
                    name, 'start')
            assert exc.value.dbus_name == helper.ERROR_INVALID_ARGS

    def test_helper_never_touches_password_auth(self):
        text = (Path(__file__).parent.parent / 'launcher' / 'helper'
                / 'xx_wm_helper.py').read_text(encoding='utf-8')
        assert 'PasswordAuthentication' not in text
        assert 'sshd_config' not in text


# ------------------------------------------------- hidden row, true state


class TestVisibility:
    def test_adbd_hidden_without_binary(self):
        keys = [e.key for e in services.visible_system_entries(
            eg25=False, adbd=False)]
        assert 'adbd' not in keys
        assert 'eg25' not in keys
        assert 'ssh' in keys

    def test_adbd_shown_with_binary(self):
        keys = [e.key for e in services.visible_system_entries(adbd=True)]
        assert 'adbd' in keys

    def test_user_services_never_hidden(self):
        assert [e.key for e in services.USER_SERVICES] == [
            'kdeconnect', 'syncthing']
        assert connect.describe_user_service(
            False, None, None) == 'Not installed'

    def test_probe_reports_true_state(self, monkeypatch):
        import system_settings
        monkeypatch.setattr(system_settings, 'detect_init',
                            lambda: 'systemd')
        monkeypatch.setattr(system_settings, 'systemd_unit_state',
                            lambda unit: None)
        monkeypatch.setattr(system_settings, 'list_failed_units',
                            lambda init: [])
        monkeypatch.setattr(
            connect, 'ssh_net_probe',
            lambda run=None: {'listening': True, 'address': '192.0.2.10'})
        monkeypatch.setattr(connect, 'ssh_password_allowed',
                            lambda path='/etc/ssh/sshd_config': False)
        monkeypatch.setattr(connect, 'adbd_available',
                            lambda which=None: False)
        monkeypatch.setattr(
            connect, 'kdeconnect_probe',
            lambda run=None, which=None: {
                'installed': False, 'active': None, 'enabled': None,
                'active_state': None, 'enabled_state': None,
                'devices': [], 'error': None})
        monkeypatch.setattr(
            connect, 'syncthing_probe',
            lambda run=None, which=None: {
                'installed': True, 'active': False, 'enabled': False,
                'active_state': 'inactive', 'enabled_state': 'disabled',
                'devices': [], 'error': None})
        res = services.ServicesSection()._probe()
        assert res['ssh_net'] == {'listening': True,
                                  'address': '192.0.2.10'}
        assert res['ssh_pw'] is False
        assert res['adbd'] is False
        assert res['user']['kdeconnect']['installed'] is False
        assert res['user']['syncthing']['active'] is False


# --------------------------------------- user actions (worker, no display)


class _StubLabel:
    def __init__(self):
        self.text = ''
        self.visible = True

    def set_text(self, text):
        self.text = text

    def set_visible(self, visible):
        self.visible = bool(visible)


class TestUserActions:
    def test_failure_lands_inline_in_subtitle(self, monkeypatch):
        monkeypatch.setattr(settings_page, '_run_bg',
                            lambda work, done: done(work()))
        section = services.ServicesSection()
        label = _StubLabel()
        row = SimpleNamespace(subtitle_label=label)
        section._user_rows['syncthing'] = row
        section._user_base['syncthing'] = 'Off'

        def fake_set(unit, action, run=None):
            raise OSError('unit masked')
        monkeypatch.setattr(connect, 'set_user_unit', fake_set)
        entry = next(e for e in services.USER_SERVICES
                     if e.key == 'syncthing')
        section._apply_user(object(), entry, 'start', row)
        assert label.text == 'Off · unit masked'

    def test_success_refreshes(self, monkeypatch):
        monkeypatch.setattr(settings_page, '_run_bg',
                            lambda work, done: done(work()))
        section = services.ServicesSection()
        refreshed = []
        monkeypatch.setattr(section, 'refresh',
                            lambda win: refreshed.append(win))
        monkeypatch.setattr(connect, 'set_user_unit',
                            lambda unit, action, run=None: None)
        entry = next(e for e in services.USER_SERVICES
                     if e.key == 'kdeconnect')
        win = object()
        section._apply_user(win, entry, 'enable')
        assert refreshed == [win]

    def test_open_syncthing_web_ui(self, monkeypatch):
        opened = []

        class FakeAppInfo:
            @staticmethod
            def launch_default_for_uri(uri, _cancellable):
                opened.append(uri)

        fake_repo = types.ModuleType('gi.repository')
        fake_repo.Gio = SimpleNamespace(AppInfo=FakeAppInfo)
        monkeypatch.setitem(sys.modules, 'gi.repository', fake_repo)
        section = services.ServicesSection()
        label = _StubLabel()
        section._user_rows['syncthing'] = label
        section._user_base['syncthing'] = 'On'
        entry = next(e for e in services.USER_SERVICES
                     if e.key == 'syncthing')
        section._open_syncthing(object(), entry)
        assert opened == ['http://127.0.0.1:8384']
        assert connect.SYNCTHING_URL == 'http://127.0.0.1:8384'

    def test_open_failure_lands_inline(self, monkeypatch):
        class FailingAppInfo:
            @staticmethod
            def launch_default_for_uri(uri, _cancellable):
                raise OSError('no browser')

        fake_repo = types.ModuleType('gi.repository')
        fake_repo.Gio = SimpleNamespace(AppInfo=FailingAppInfo)
        monkeypatch.setitem(sys.modules, 'gi.repository', fake_repo)
        section = services.ServicesSection()
        label = _StubLabel()
        row = SimpleNamespace(subtitle_label=label)
        section._user_rows['syncthing'] = row
        section._user_base['syncthing'] = 'On'
        entry = next(e for e in services.USER_SERVICES
                     if e.key == 'syncthing')
        section._open_syncthing(object(), entry)
        assert label.text == 'On · no browser'


# ---------------------------------------------------------------- installer


class TestInstallerSuite:
    def test_sharing_backends_per_distro(self):
        text = APPS_SH.read_text(encoding='utf-8')
        chunk = text[text.find('# --- 17.7 Sharing backends'):]
        assert 'pkg_install openssh nftables kdeconnect syncthing' in chunk
        assert 'pkg_install openssh-server nftables kdeconnect syncthing' \
            in chunk
        assert 'pkg_install adbd || pkg_install android-tools-adbd' in chunk

    def test_no_ufw_install(self):
        text = APPS_SH.read_text(encoding='utf-8')
        assert 'pkg_install ufw' not in text

    def test_script_parses(self):
        result = subprocess.run(
            ['sh', '-n', str(APPS_SH)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
