"""APN save: the gsm profile is written through NetworkManager's D-Bus
settings API (password never in argv), a profile is created when none
exists, and the status line reports what NetworkManager answered.

The fake bus mirrors Gio.DBusConnection.call_sync and answers with real
GLib.Variant replies shaped like NetworkManager's."""
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

gi = pytest.importorskip('gi')
try:
    from gi.repository import GLib
    GLib.Variant('s', 'probe')
    GLib.VariantType.new('(s)')
except Exception:  # a stub gi installed by another test module
    pytest.skip('real GLib required', allow_module_level=True)

import system_settings  # noqa: E402

SETTINGS = '/org/freedesktop/NetworkManager/Settings'
ETH = SETTINGS + '/1'
GSM = SETTINGS + '/2'


def _settings(groups):
    return GLib.Variant('(a{sa{sv}})', (groups,))


def _eth_settings():
    return {
        'connection': {'id': GLib.Variant('s', 'Wired'),
                       'type': GLib.Variant('s', '802-3-ethernet')},
    }


def _gsm_settings():
    return {
        'connection': {'id': GLib.Variant('s', 'Carrier'),
                       'type': GLib.Variant('s', 'gsm'),
                       'uuid': GLib.Variant('s', 'u-gsm'),
                       'autoconnect': GLib.Variant('b', True)},
        'gsm': {'apn': GLib.Variant('s', 'old.apn'),
                'username': GLib.Variant('s', 'olduser')},
        'ipv4': {'method': GLib.Variant('s', 'auto'),
                 'dns': GLib.Variant('au', [16843009])},
    }


class FakeNmBus:
    def __init__(self, profiles, fail=None):
        self.profiles = profiles
        self.fail = fail
        self.calls = []
        self.written = None

    def call_sync(self, bus_name, path, iface, method, params, reply_type,
                  flags, timeout, cancellable):
        assert bus_name == 'org.freedesktop.NetworkManager'
        assert reply_type is None or isinstance(reply_type, GLib.VariantType)
        self.calls.append((path, method))
        if method == self.fail:
            raise GLib.Error.new_literal(
                GLib.quark_from_string('g-dbus-error-quark'),
                'GDBus.Error:org.freedesktop.NetworkManager.PermissionDenied:'
                ' Insufficient privileges', 0)
        if method == 'ListConnections':
            assert (path, iface) == (
                SETTINGS, 'org.freedesktop.NetworkManager.Settings')
            return GLib.Variant('(ao)', (list(self.profiles),))
        if method == 'GetSettings':
            return _settings(self.profiles[path])
        if method == 'GetSecrets':
            assert params.unpack() == ('gsm',)
            return _settings({'gsm': {'pin': GLib.Variant('s', '1234')}})
        if method in ('Update', 'AddConnection'):
            assert params.get_type_string() == '(a{sa{sv}})'
            self.written = (path, method,
                            system_settings.nm_settings_groups(
                                params.get_child_value(0)))
            if method == 'AddConnection':
                return GLib.Variant('(o)', (SETTINGS + '/9',))
            return GLib.Variant('()', ())
        raise AssertionError(method)


def _strings(group):
    return {key: value.unpack() for key, value in group.items()}


@pytest.fixture
def no_subprocess(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError('APN save must not run a program')
    monkeypatch.setattr(subprocess, 'run', _boom)
    monkeypatch.setattr(subprocess, 'Popen', _boom)


def test_updates_existing_gsm_profile_keeping_other_settings(no_subprocess):
    bus = FakeNmBus({ETH: _eth_settings(), GSM: _gsm_settings()})
    ok, message = system_settings.apply_apn('internet', '', 's3cret',
                                            bus=bus)
    assert (ok, message) == (True, 'APN saved.')
    path, method, groups = bus.written
    assert (path, method) == (GSM, 'Update')
    gsm = _strings(groups['gsm'])
    assert gsm == {'apn': 'internet', 'password': 's3cret',
                   'password-flags': 0, 'pin': '1234'}
    assert _strings(groups['connection'])['uuid'] == 'u-gsm'
    assert groups['ipv4']['dns'].get_type_string() == 'au'
    assert groups['ipv4']['dns'].unpack() == [16843009]


def test_creates_a_gsm_profile_when_there_is_none(no_subprocess):
    bus = FakeNmBus({ETH: _eth_settings()})
    ok, message = system_settings.apply_apn('internet', 'me', 'pw', bus=bus)
    assert (ok, message) == (True, 'APN saved.')
    path, method, groups = bus.written
    assert (path, method) == (SETTINGS, 'AddConnection')
    connection = _strings(groups['connection'])
    assert connection['type'] == 'gsm' and connection['autoconnect'] is True
    assert len(connection['uuid']) == 36
    assert _strings(groups['gsm']) == {'apn': 'internet', 'username': 'me',
                                       'password': 'pw', 'password-flags': 0}
    assert _strings(groups['ipv4']) == {'method': 'auto'}


def test_failure_is_reported_not_called_saved(no_subprocess):
    bus = FakeNmBus({GSM: _gsm_settings()}, fail='Update')
    ok, message = system_settings.apply_apn('internet', '', '', bus=bus)
    assert not ok
    assert message == 'Could not save APN: Insufficient privileges'


def test_settings_status_waits_for_networkmanager(monkeypatch):
    import settings_page
    statuses = []
    win = SimpleNamespace(_show_status=statuses.append,
                          _run_bg=lambda work, done: done(work()))
    page = settings_page.SettingsPage(win)
    monkeypatch.setattr(system_settings, 'apply_apn',
                        lambda apn, user, pwd: (False, 'Could not save APN: x'))
    page._apply_apn('internet', '', 'pw')
    assert statuses == ['Saving APN…', 'Could not save APN: x']
    monkeypatch.setattr(system_settings, 'apply_apn',
                        lambda apn, user, pwd: (True, 'APN saved.'))
    page._apply_apn('internet', '', 'pw')
    assert statuses[-1] == 'APN saved.'
