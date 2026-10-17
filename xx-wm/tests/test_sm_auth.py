"""Lane sm_auth: polkit agent, updates, uninstall, owner update script.

Helper methods run against recording fakes — never the real package
manager, services or firewall. UI sections build against recorder fakes
where GTK is needed; pre-existing suites keep their own idioms.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).parent.parent / 'launcher' / 'src'
HELPER_DIR = Path(__file__).parent.parent / 'launcher' / 'helper'

sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HELPER_DIR))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'common'))

import xx_wm_helper as helper  # noqa: E402


UID = 1000


class FakeRun:
    def __init__(self, stdout='', returncode=0, stderr=''):
        self.cmds = []
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr

    def __call__(self, argv):
        assert isinstance(argv, list), argv
        assert argv and all(isinstance(a, str) for a in argv), argv
        self.cmds.append(list(argv))
        return SimpleNamespace(returncode=self.returncode,
                               stdout=self.stdout, stderr=self.stderr)


def _which_map(present):
    return lambda binary: '/usr/bin/' + binary if binary in present else None


# ------------------------------------------------------- RefreshPackageIndex

class TestRefreshPackageIndex:
    def test_runs_apk_update(self):
        run = FakeRun(stdout='OK\n')
        core = helper.HelperCore(run=run)
        assert core.refresh_package_index(
            which=_which_map({'apk'})) is None
        assert run.cmds == [['apk', 'update']]

    def test_without_apk_fails(self):
        core = helper.HelperCore(run=FakeRun())
        with pytest.raises(helper.HelperError) as exc:
            core.refresh_package_index(which=_which_map(set()))
        assert exc.value.dbus_name == helper.ERROR_FAILED

    def test_failed_update_surfaces(self):
        run = FakeRun(returncode=1, stderr='network down\n')
        core = helper.HelperCore(run=run)
        with pytest.raises(helper.HelperError) as exc:
            core.refresh_package_index(which=_which_map({'apk'}))
        assert exc.value.dbus_name == helper.ERROR_FAILED
        assert 'network down' in str(exc.value)


# ------------------------------------------------------------ UpgradeSystem

class TestUpgradeSystem:
    def test_runs_apk_upgrade_and_returns_output(self):
        run = FakeRun(stdout='Upgrading foo\nOK\n')
        core = helper.HelperCore(run=run)
        out = core.upgrade_system(which=_which_map({'apk'}))
        assert run.cmds == [['apk', 'upgrade']]
        assert 'Upgrading foo' in out

    def test_without_apk_fails(self):
        core = helper.HelperCore(run=FakeRun())
        with pytest.raises(helper.HelperError) as exc:
            core.upgrade_system(which=_which_map({'pacman'}))
        assert exc.value.dbus_name == helper.ERROR_FAILED

    def test_failed_upgrade_surfaces(self):
        run = FakeRun(returncode=1, stderr='boom\n')
        core = helper.HelperCore(run=run)
        with pytest.raises(helper.HelperError) as exc:
            core.upgrade_system(which=_which_map({'apk'}))
        assert exc.value.dbus_name == helper.ERROR_FAILED


# ------------------------------------------------------------ RemovePackage

PROTECTED = [
    'base', 'alpine-base', 'musl', 'openrc', 'systemd', 'apk-tools',
    'pacman', 'doas', 'sudo', 'linux-lts', 'linux-firmware',
    'postmarketos-base', 'postmarketos-base-nofde', 'xx-wm', 'gtk4',
    'python3', 'phoc', 'squeekboard', 'networkmanager', 'modemmanager',
]

EVIL = ['', ' ', 'foo bar', '../x', '/bin/sh', '-f', 'x;y', 'x$y',
        'x`y`', 'x\ny', 'pkg\x00name', 'a' * 300]


class TestRemovePackage:
    @pytest.mark.parametrize('pkg', PROTECTED)
    def test_protected_list_refused(self, pkg):
        core = helper.HelperCore(run=FakeRun())
        with pytest.raises(helper.HelperError) as exc:
            core.remove_package(pkg, which=_which_map({'apk'}))
        assert exc.value.dbus_name == helper.ERROR_NOT_AUTHORIZED

    @pytest.mark.parametrize('pkg', EVIL)
    def test_malformed_names_rejected(self, pkg):
        core = helper.HelperCore(run=FakeRun())
        with pytest.raises(helper.HelperError) as exc:
            core.remove_package(pkg, which=_which_map({'apk'}))
        assert exc.value.dbus_name in (helper.ERROR_INVALID_ARGS,
                                       helper.ERROR_NOT_AUTHORIZED)

    def test_apk_removal_argv(self):
        run = FakeRun()
        core = helper.HelperCore(run=run)
        assert core.remove_package('vim', which=_which_map({'apk'})) == 'apk'
        assert run.cmds == [['apk', 'del', 'vim']]

    def test_pacman_removal_argv(self):
        run = FakeRun()
        core = helper.HelperCore(run=run)
        assert core.remove_package(
            'vim', which=_which_map({'pacman'})) == 'pacman'
        assert run.cmds == [['pacman', '-Rns', '--noconfirm', 'vim']]

    def test_no_backend_fails(self):
        core = helper.HelperCore(run=FakeRun())
        with pytest.raises(helper.HelperError) as exc:
            core.remove_package('vim', which=_which_map(set()))
        assert exc.value.dbus_name == helper.ERROR_FAILED

    def test_never_runs_a_shell(self):
        run = FakeRun()
        core = helper.HelperCore(run=run)
        core.remove_package('htop', which=_which_map({'apk'}))
        for cmd in run.cmds:
            assert isinstance(cmd, list)
            assert not any(';' in a or '|' in a for a in cmd)


# ------------------------------------------- interactive authorization flags

class FlagConn:
    """Fake bus recording the CheckAuthorization flags per action."""

    def __init__(self, uid):
        self.uid = uid
        self.flags = {}

    def call(self, bus_name, path, iface, method, params, reply_type,
             flags, timeout, cancellable, callback):
        if method == 'GetConnectionUnixUser':
            callback(self, _FakeResult(self.uid))
        elif method == 'CheckAuthorization':
            subject, action, details, check_flags = params.unpack()
            self.flags[action] = check_flags
            callback(self, _FakeAuth(True))
        else:  # pragma: no cover
            raise AssertionError(method)

    def call_finish(self, result):
        return result


class _FakeResult:
    def __init__(self, payload):
        self.payload = payload

    def unpack(self):
        return (self.payload,)


class _FakeAuth:
    def __init__(self, authorized):
        self.authorized = authorized

    def unpack(self):
        from gi.repository import GLib
        return GLib.Variant(
            '((bba{ss}))', ((bool(self.authorized), False, {}),)).unpack()


class TestInteractiveFlags:
    def test_new_destructive_actions_pass_interaction_flag(self):
        pytest.importorskip('gi')
        conn = FlagConn(UID)
        for method, action in (
                ('UpgradeSystem', helper.ACTION_UPGRADE),
                ('RemovePackage', helper.ACTION_REMOVE)):
            done = []
            helper.check_polkit_authorization(
                conn, ':1.7', action,
                lambda ok, uid, err: done.append((ok, uid, err)),
                interactive=True)
            assert done == [(True, UID, None)]
        assert conn.flags[helper.ACTION_UPGRADE] == 1
        assert conn.flags[helper.ACTION_REMOVE] == 1

    def test_existing_actions_stay_non_interactive(self):
        pytest.importorskip('gi')
        conn = FlagConn(UID)
        for action in (helper.ACTION_CHARGE, helper.ACTION_NETWORK,
                       helper.ACTION_FREEZE, helper.ACTION_REFRESH):
            done = []
            helper.check_polkit_authorization(
                conn, ':1.7', action,
                lambda ok, uid, err: done.append((ok, uid, err)))
            assert done == [(True, UID, None)]
            assert conn.flags[action] == 0

    def test_service_dispatches_new_methods(self):
        run = FakeRun(stdout='ok\n')
        seen = []

        def authorize(conn, sender, action, interactive, callback):
            seen.append(action)
            callback(True, UID, None)

        core = helper.HelperCore(run=run)
        service = helper.HelperService(connection=object(), core=core,
                                       authorize=authorize)

        class Invocation:
            def __init__(self):
                self.value = None
                self.error = None

            def return_value(self, value):
                self.value = value

            def return_dbus_error(self, name, message):
                self.error = (name, message)

        calls = [
            ('RefreshPackageIndex', (),
             _which_map({'apk'}), [['apk', 'update']]),
            ('UpgradeSystem', (), _which_map({'apk'}),
             [['apk', 'upgrade']]),
            ('RemovePackage', ('vim',), _which_map({'apk'}),
             [['apk', 'del', 'vim']]),
        ]
        import unittest.mock as mock
        for method, args, which, _expected in calls:
            invocation = Invocation()
            with mock.patch('shutil.which', which):
                service._on_method_call(
                    service.connection, ':1.7', '/p', 'i', method,
                    SimpleNamespace(unpack=lambda a=args: a), invocation)
            assert invocation.error is None, (method, invocation.error)
            assert invocation.value is not None
        assert seen == [helper.ACTION_REFRESH, helper.ACTION_UPGRADE,
                        helper.ACTION_REMOVE]

    def test_introspection_lists_new_methods(self):
        assert 'name="RefreshPackageIndex"' in helper.INTROSPECT_XML
        assert 'name="UpgradeSystem"' in helper.INTROSPECT_XML
        assert 'name="RemovePackage"' in helper.INTROSPECT_XML


# ------------------------------------------------------------- shipped files

def test_policy_new_actions():
    text = (HELPER_DIR.parent / 'data' / 'polkit-1' / 'actions'
            / 'io.piercingxx.XXWM.Helper.policy').read_text()
    assert 'action id="io.piercingxx.XXWM.Helper1.refresh-packages"' in text
    assert 'action id="io.piercingxx.XXWM.Helper1.upgrade-system"' in text
    assert 'action id="io.piercingxx.XXWM.Helper1.remove-package"' in text
    assert text.count('<allow_active>yes</allow_active>') == 10
    assert text.count('<allow_active>auth_admin_keep</allow_active>') == 4
    for action in ('set-charge-limit', 'app-network', 'freeze'):
        assert 'action id="io.piercingxx.XXWM.Helper1.%s"' % action in text


# ------------------------------------------------------------------- client

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
        self.params = []

    def call(self, bus_name, path, iface, method, params, reply_type,
             flags, timeout, cancellable, callback):
        self.calls.append((bus_name, path, iface, method))
        self.params.append(params)
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
    import helper_client
    monkeypatch.setattr(helper_client, '_gio', lambda: (FakeGio, FakeGLib))
    monkeypatch.setattr(helper_client, '_capabilities_cache', None)
    FakeGio.conn = None
    FakeGio.bus_error = None
    helper_client.invalidate_capabilities()
    return FakeGio


class TestNewClientCalls:
    def test_refresh_upgrade_remove(self, fake_gio):
        import helper_client
        FakeGio.conn = FakeClientConn(FakeClientReply(None))
        done = []
        helper_client.refresh_package_index(
            lambda ok, res: done.append((ok, res)))
        FakeGio.conn = FakeClientConn(FakeClientReply('out\n'))
        helper_client.upgrade_system(lambda ok, res: done.append((ok, res)))
        FakeGio.conn = FakeClientConn(FakeClientReply(None))
        helper_client.remove_package(
            'vim', lambda ok, res: done.append((ok, res)))
        assert done == [(True, None), (True, 'out\n'), (True, None)]
        assert [c[3] for c in FakeGio.conn.calls] == ['RemovePackage']

    def test_remove_error_propagates(self, fake_gio):
        import helper_client
        FakeGio.conn = FakeClientConn(error=Exception(
            'GDBus.Error:io.piercingxx.XXWM.Helper1.Error.NotAuthorized: x'))
        done = []
        helper_client.remove_package(
            'vim', lambda ok, res: done.append((ok, res)))
        assert done[0][0] is False
        assert 'NotAuthorized' in done[0][1]


# ------------------------------------------------------------- update_checker

class TestCheckupdatesGate:
    def test_checkupdates_has_no_index_gate(self):
        import update_checker
        assert 'checkupdates' not in update_checker._INDEX_PATHS

    def test_checkupdates_counts_with_stale_sync_dir(self, monkeypatch,
                                                    tmp_path):
        import update_checker
        monkeypatch.setattr(
            update_checker.shutil, 'which',
            lambda binary: '/usr/bin/' + binary
            if binary == 'checkupdates' else None)
        (tmp_path / 'sync').mkdir()
        monkeypatch.setattr(update_checker, '_INDEX_PATHS',
                            {'pacman': [str(tmp_path / 'sync')]})
        import subprocess
        monkeypatch.setattr(
            update_checker.subprocess, 'run',
            lambda *a, **k: subprocess.CompletedProcess(
                a[0], 0, stdout='a 1\nb 2\nc 3\n', stderr=''))
        assert update_checker.count_updates() == 3


# --------------------------------------------------- package backend parsing

class _Sysfs:
    def __init__(self, root):
        self.root = Path(root)

    def supply(self, name, type_, **attrs):
        base = self.root / 'class' / 'power_supply' / name
        base.mkdir(parents=True, exist_ok=True)
        (base / 'type').write_text(type_ + '\n')
        for key, value in attrs.items():
            (base / key).write_text(str(value) + '\n')
        return str(self.root)


class TestPowerGuard:
    def test_ac_online_allows(self, tmp_path):
        import system_settings
        sysfs = _Sysfs(tmp_path).supply('AC', 'Mains', online='1')
        ok, reason = system_settings.power_for_update(sysfs=sysfs)
        assert ok is True
        assert 'AC' in reason

    def test_charging_battery_allows(self, tmp_path):
        import system_settings
        sysfs = _Sysfs(tmp_path).supply(
            'BAT0', 'Battery', capacity='12', status='Charging')
        assert system_settings.power_for_update(sysfs=sysfs)[0] is True

    def test_full_battery_allows(self, tmp_path):
        import system_settings
        sysfs = _Sysfs(tmp_path).supply(
            'BAT0', 'Battery', capacity='100', status='Full')
        assert system_settings.power_for_update(sysfs=sysfs)[0] is True

    def test_low_discharging_battery_blocks(self, tmp_path):
        import system_settings
        sysfs = _Sysfs(tmp_path).supply(
            'BAT0', 'Battery', capacity='29', status='Discharging')
        ok, reason = system_settings.power_for_update(sysfs=sysfs)
        assert ok is False
        assert '29%' in reason

    def test_thirty_percent_is_enough(self, tmp_path):
        import system_settings
        sysfs = _Sysfs(tmp_path).supply(
            'BAT0', 'Battery', capacity='30', status='Discharging')
        assert system_settings.power_for_update(sysfs=sysfs)[0] is True

    def test_no_battery_reports_unknown_but_allows(self, tmp_path):
        import system_settings
        ok, _reason = system_settings.power_for_update(
            sysfs=str(tmp_path))
        assert ok is True


class TestBackendDetect:
    def test_apk_first(self):
        import system_settings
        assert system_settings.detect_backend(
            which=_which_map({'apt', 'apk', 'pacman'})) == 'apk'

    def test_pacman_before_apt(self):
        import system_settings
        assert system_settings.detect_backend(
            which=_which_map({'apt', 'pacman'})) == 'pacman'

    def test_apt_trivial(self):
        import system_settings
        assert system_settings.detect_backend(
            which=_which_map({'apt'})) == 'apt'

    def test_none_without_manager(self):
        import system_settings
        assert system_settings.detect_backend(
            which=_which_map(set())) is None


class TestFlatpakCount:
    def test_counts_update_lines(self):
        import system_settings
        assert system_settings.parse_flatpak_updates(
            'org.foo.App\norg.bar.App\n') == 2
        assert system_settings.parse_flatpak_updates('\n  \n') == 0

    def test_worker_counts_through_run(self):
        import system_settings
        calls = []

        def run(cmd, timeout=15):
            calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout='a\nb\n', stderr='')

        assert system_settings.flatpak_updates_available(run=run) == 2
        assert calls[0][:2] == ['flatpak', 'remote-ls']
        assert '--updates' in calls[0]

    def test_missing_flatpak_is_none(self):
        import system_settings

        def run(cmd, timeout=15):
            raise OSError('no flatpak')

        assert system_settings.flatpak_updates_available(run=run) is None


class TestOwnerPreview:
    def test_apk_owner_parses_version_off(self):
        import system_settings
        owner, backend = system_settings.parse_apk_who_owns(
            '/usr/share/applications/vlc.desktop is owned by vlc-3.0.21-r0\n')
        assert (owner, backend) == ('vlc', 'apk')

    def test_apk_owner_unowned_is_none(self):
        import system_settings
        assert system_settings.parse_apk_who_owns('ERROR: foo: no owner\n') == \
            (None, 'apk')

    def test_flatpak_key_parse(self):
        import system_settings
        text = ('[Desktop Entry]\nName=Foo\n'
                'X-Flatpak=org.example.Foo\n'
                'X-Flatpak-RenamedFrom=org.example.Old;\n')
        assert system_settings.parse_flatpak_app_id(text) == 'org.example.Foo'
        assert system_settings.parse_flatpak_app_id('[Desktop Entry]\n') is None

    def test_installation_user_preferred(self):
        import system_settings

        def run(cmd, timeout=15):
            if '--user' in cmd:
                return SimpleNamespace(returncode=0, stdout='org.x.App\n',
                                       stderr='')
            return SimpleNamespace(returncode=0, stdout='', stderr='')

        assert system_settings.flatpak_installation('org.x.App',
                                                    run=run) == 'user'

    def test_installation_system_fallback(self):
        import system_settings

        def run(cmd, timeout=15):
            if '--user' in cmd:
                return SimpleNamespace(returncode=0, stdout='', stderr='')
            return SimpleNamespace(returncode=0, stdout='org.x.App\n',
                                       stderr='')

        assert system_settings.flatpak_installation('org.x.App',
                                                    run=run) == 'system'


# ---------------------------------------------------------- settings_updates

class TestRebootLogDots:
    def test_kernel_modules_gone_means_reboot(self, tmp_path):
        import settings_updates
        reason = settings_updates.reboot_required(
            uname_release='6.6.7-0-lts', lib_modules=str(tmp_path / 'lib'),
            usr_lib_modules=str(tmp_path / 'usr'), flag_file=str(tmp_path))
        assert reason is not None
        assert '6.6.7' in reason

    def test_present_modules_no_reboot(self, tmp_path):
        import settings_updates
        mods = tmp_path / 'lib'
        (mods / '6.6.7-0-lts').mkdir(parents=True)
        assert settings_updates.reboot_required(
            uname_release='6.6.7-0-lts', lib_modules=str(mods),
            usr_lib_modules=str(tmp_path / 'usr'),
            flag_file=str(tmp_path / 'flag')) is None

    def test_reboot_required_flag(self, tmp_path):
        import settings_updates
        mods = tmp_path / 'lib'
        (mods / 'r').mkdir(parents=True)
        flag = tmp_path / 'reboot-required'
        flag.write_text('')
        reason = settings_updates.reboot_required(
            uname_release='r', lib_modules=str(mods),
            usr_lib_modules=str(tmp_path / 'usr'), flag_file=str(flag))
        assert reason is not None

    def test_latest_log_is_newest_tail(self, tmp_path):
        import settings_updates
        logs = tmp_path / '.local' / 'state' / 'piercingxx' / 'logs'
        logs.mkdir(parents=True)
        (logs / 'update-20240101-000000.log').write_text('old\n')
        (logs / 'update-20240202-000000.log').write_text(
            '\n'.join('line%d' % i for i in range(50)) + '\n')
        name, tail = settings_updates.latest_update_log(home=str(tmp_path))
        assert name == 'update-20240202-000000.log'
        assert 'line49' in tail
        assert 'line0' not in tail.splitlines()[0:1]

    def test_no_log_is_none(self, tmp_path):
        import settings_updates
        assert settings_updates.latest_update_log(
            home=str(tmp_path)) is None

    def test_owner_script_present_in_dots_layout(self, tmp_path):
        import settings_updates
        assert settings_updates.owner_script_path(
            home=str(tmp_path)) is None
        target = (tmp_path / '.scripts' / 'PiercingXX-Settings-Menu'
                  / 'update-system.sh')
        target.parent.mkdir(parents=True)
        target.write_text('#!/bin/sh\n')
        assert settings_updates.owner_script_path(
            home=str(tmp_path)) == str(target)

    def test_format_count_never_lies(self):
        import settings_updates
        assert 'unknown' in settings_updates.format_count(None, None)
        assert 'up to date' in settings_updates.format_count(0, 0)
        assert 'up to date' in settings_updates.format_count(0, None)
        text = settings_updates.format_count(2, 1)
        assert '3 updates' in text
        assert 'Flatpak' in text


# ------------------------------------------------------------- settings_apps

class TestAppsSection:
    def test_registered_and_gestures_last(self):
        import settings_page
        keys = [key for key, _cls in settings_page.SETTINGS_SECTIONS]
        assert 'apps' in keys
        assert keys[-1] == 'gestures'
        assert 'apps' in settings_page._SETTINGS_SECTION_KEYS

    def test_section_shape(self):
        import settings_page
        section = dict(settings_page.SETTINGS_SECTIONS)['apps']()
        assert isinstance(section, settings_page._Section)
        assert section.key == 'apps'

    def test_protected_parity_with_helper(self):
        import settings_apps
        corpus = ['vim', 'htop', 'firefox', 'org.example.App'] + PROTECTED
        for name in corpus:
            assert settings_apps.is_protected(name) == \
                helper.is_protected_package(name), name


# -------------------------------------------------------------- polkit agent

class TestPolkitAgent:
    def test_first_unix_user_wins(self):
        polkit_agent = pytest.importorskip('polkit_agent')
        idents = [SimpleNamespace(kind='group'),
                  SimpleNamespace(kind='user', name='amy'),
                  SimpleNamespace(kind='user', name='bob')]
        assert polkit_agent.pick_identity(idents).name == 'amy'

    def test_no_user_identity_is_none(self):
        polkit_agent = pytest.importorskip('polkit_agent')
        assert polkit_agent.pick_identity(
            [SimpleNamespace(kind='group')]) is None
        assert polkit_agent.pick_identity([]) is None

    def test_user_display_name(self):
        polkit_agent = pytest.importorskip('polkit_agent')
        assert polkit_agent.display_name_for_uid(0) == 'root'
        assert isinstance(polkit_agent.display_name_for_uid(65534), str)

    def test_listener_subclass_registers(self):
        pytest.importorskip('gi')
        polkit_agent = pytest.importorskip('polkit_agent')
        listener = polkit_agent.ShellListener()
        assert isinstance(listener, polkit_agent.base_listener_type())

    def test_start_without_bus_never_raises(self, monkeypatch):
        polkit_agent = pytest.importorskip('polkit_agent')
        monkeypatch.setattr(polkit_agent, 'register_listener',
                            lambda *a: (_ for _ in ()).throw(
                                RuntimeError('no bus')))
        assert polkit_agent.start_agent() is None

    def test_cancel_completes_pending(self):
        polkit_agent = pytest.importorskip('polkit_agent')
        completed = []
        pending = polkit_agent.Pending(cookie='c', action_id='a',
                                      message='m',
                                      finish=lambda ok: completed.append(ok))
        fake_session = SimpleNamespace(
            cancelled=[], cancel=lambda: fake_session.cancelled.append(1))
        pending.session = fake_session
        pending.cancel('dialog gone')
        assert fake_session.cancelled == [1]
        assert completed == [False]

    def test_password_never_logged(self):
        import re
        text = (SRC / 'polkit_agent.py').read_text(encoding='utf-8')
        is_log = re.compile(
            r'(\blog\b|\b_log\b|get_logger|print\s*\(|logging)').search
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith('#'):
                continue
            lowered = stripped.lower()
            if is_log(lowered):
                assert 'password' not in lowered, lineno
                assert 'secret' not in lowered, lineno

    def test_no_python_finish_call(self):
        text = (SRC / 'polkit_agent.py').read_text(encoding='utf-8')
        assert 'initiate_authentication_finish' not in text


# -------------------------------------------------- static project contracts

class TestStaticContracts:
    def test_meson_lists_new_modules(self):
        meson = (SRC.parent / 'meson.build').read_text(encoding='utf-8')
        for name in ('polkit_agent.py', 'settings_updates.py',
                     'settings_apps.py'):
            assert "'src/%s'" % name in meson
            assert (SRC / name).is_file()

    def test_new_modules_never_block_main_loop(self):
        import ast
        for name in ('settings_updates.py', 'settings_apps.py',
                     'polkit_agent.py'):
            tree = ast.parse((SRC / name).read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if isinstance(func, ast.Attribute):
                    assert func.attr not in (
                        'run', 'check_output', 'call_sync', 'wait'), \
                        (name, func.attr)

    def test_main_starts_polkit_agent(self):
        text = (SRC / 'main.py').read_text(encoding='utf-8')
        assert 'polkit_agent' in text
        assert 'start_agent' in text

    def test_drawer_menu_offers_uninstall(self):
        text = (SRC / 'app_item_actions.py').read_text(encoding='utf-8')
        assert 'Uninstall' in text

    def test_registry_order_pins_apps(self):
        import settings_page
        keys = [key for key, _cls in settings_page.SETTINGS_SECTIONS]
        assert keys.index('apps') < keys.index('gestures')
        assert len(keys) == len(set(keys))
