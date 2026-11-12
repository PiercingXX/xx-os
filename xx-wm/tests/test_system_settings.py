"""Tests for system_settings.py parsers (pure logic, injected output)."""
import os
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from system_settings import _key_mgmt_for, connect_wifi, parse_nmcli_wifi, parse_pactl_sinks

NMCLI_OUTPUT = """\
*:HomeNet:87:WPA2
 :HomeNet:45:WPA2
 :CoffeeShop:62:
 :Neighbor\\:5G:31:WPA3
 ::99:WPA2
 :Weak:notanumber:WPA2
"""


class TestWifiParsing:
    def test_dedupes_keeps_strongest_and_in_use_first(self):
        nets = parse_nmcli_wifi(NMCLI_OUTPUT)
        ssids = [n.ssid for n in nets]
        assert ssids[0] == 'HomeNet'
        assert nets[0].in_use
        assert nets[0].signal == 87
        assert ssids.count('HomeNet') == 1

    def test_open_network_and_hidden_ssid(self):
        nets = parse_nmcli_wifi(NMCLI_OUTPUT)
        coffee = next(n for n in nets if n.ssid == 'CoffeeShop')
        assert coffee.security == 'open'
        assert all(n.ssid for n in nets)

    def test_escaped_colon_in_ssid(self):
        nets = parse_nmcli_wifi(NMCLI_OUTPUT)
        neighbor = next(n for n in nets if n.ssid == 'Neighbor:5G')
        assert neighbor.signal == 31
        assert neighbor.security == 'WPA3'

    def test_bad_signal_defaults_zero(self):
        nets = parse_nmcli_wifi(NMCLI_OUTPUT)
        weak = next(n for n in nets if n.ssid == 'Weak')
        assert weak.signal == 0

    def test_empty_output(self):
        assert parse_nmcli_wifi('') == []


PACTL_JSON = '''[
  {"name": "alsa_output.usb", "description": "USB Headset"},
  {"name": "alsa_output.internal", "description": "Speakers"},
  {"description": "nameless"}
]'''


class TestSinkParsing:
    def test_marks_default(self):
        sinks = parse_pactl_sinks(PACTL_JSON, 'alsa_output.internal')
        assert [s.description for s in sinks] == ['USB Headset', 'Speakers']
        assert [s.is_default for s in sinks] == [False, True]

    def test_invalid_json(self):
        assert parse_pactl_sinks('nope', 'x') == []

    def test_non_list_json(self):
        assert parse_pactl_sinks('{"a": 1}', 'x') == []


def _completed(cmd: list[str], code: int = 0, stderr: str = '',
               stdout: str = '') -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(cmd, code, stdout=stdout, stderr=stderr)


def _passwd_paths(calls: list[tuple[list[str], int]]) -> list[str]:
    return [cmd[cmd.index('passwd-file') + 1]
            for cmd, _timeout in calls if 'passwd-file' in cmd]


class _FakeNm:
    """Fake nmcli with a profile store keyed by UUID, answering the real
    terse formats: `-t -f UUID,TYPE connection show [--active]`, the
    multi-profile `-t -f connection.uuid,... connection show uuid U ...`
    (one `field:value` line each, a blank line between profiles), and
    add/up/down/delete by UUID. `profiles` maps a name to its SSID (the
    name is the SSID when given as a plain set)."""

    def __init__(self, profiles=(), up_code: int = 0, up_stderr: str = '',
                 add_code: int = 0, active=(), key_mgmt='wpa-psk'):
        self.calls: list[tuple[list[str], int]] = []
        if not isinstance(profiles, dict):
            profiles = {name: name for name in profiles}
        self.store = {}
        for index, (name, ssid) in enumerate(sorted(profiles.items())):
            self.store['uuid-%d' % index] = {'id': name, 'ssid': ssid,
                                             'key_mgmt': key_mgmt}
        self.active = {uuid for uuid, p in self.store.items()
                       if p['id'] in active}
        self.store['uuid-eth'] = {'id': 'Wired'}
        self.up_code = up_code
        self.up_stderr = up_stderr
        self.add_code = add_code
        self.modes: dict[str, int] = {}
        self.contents: dict[str, str] = {}

    @property
    def profiles(self) -> set:
        return {p['id'] for p in self.store.values() if 'ssid' in p}

    def run(self, cmd, timeout=15):
        cmd = list(cmd)
        self.calls.append((cmd, timeout))
        if cmd[:4] == ['nmcli', '-t', '-f', 'UUID,TYPE']:
            rows = [(uuid, '802-11-wireless' if 'ssid' in p
                     else '802-3-ethernet')
                    for uuid, p in sorted(self.store.items())
                    if '--active' not in cmd or uuid in self.active]
            return _completed(cmd, stdout=''.join(
                f'{uuid}:{kind}\n' for uuid, kind in rows))
        if cmd[:3] == ['nmcli', '-t', '-f'] and 'uuid' in cmd:
            blocks = []
            for uuid in cmd[cmd.index('show') + 2::2]:
                p = self.store[uuid]
                ssid = p['ssid'].replace(':', '\\:')
                blocks.append(f"connection.uuid:{uuid}\n"
                              f"connection.id:{p['id']}\n"
                              f"802-11-wireless.ssid:{ssid}\n"
                              f"802-11-wireless-security.key-mgmt:"
                              f"{p['key_mgmt']}\n")
            return _completed(cmd, stdout='\n'.join(blocks))
        if 'delete' in cmd:
            assert cmd[cmd.index('delete') + 1] == 'uuid'
            self.store.pop(cmd[cmd.index('delete') + 2], None)
            return _completed(cmd)
        if 'add' in cmd:
            if self.add_code == 0:
                uuid = cmd[cmd.index('connection.uuid') + 1]
                self.store[uuid] = {
                    'id': cmd[cmd.index('con-name') + 1],
                    'ssid': cmd[cmd.index('ssid') + 1],
                    'key_mgmt': cmd[cmd.index('wifi-sec.key-mgmt') + 1]}
            return _completed(cmd, self.add_code)
        if 'up' in cmd:
            assert cmd[cmd.index('up') + 1] == 'uuid'
            if 'passwd-file' in cmd:
                path = cmd[cmd.index('passwd-file') + 1]
                self.modes[path] = stat.S_IMODE(os.stat(path).st_mode)
                self.contents[path] = Path(path).read_text()
            return _completed(cmd, self.up_code, self.up_stderr)
        if 'down' in cmd:
            assert cmd[cmd.index('down') + 1] == 'uuid'
            return _completed(cmd)
        return _completed(cmd)

    def uuid_of(self, name: str) -> str:
        return next(uuid for uuid, p in self.store.items()
                    if p.get('id') == name)


class TestConnectWifiOpenNetwork:
    def test_open_network_single_connect_no_secrets(self):
        nm = _FakeNm()
        ok, msg = connect_wifi('CoffeeShop', run=nm.run)
        assert ok
        assert msg == 'Connected to CoffeeShop'
        assert nm.calls[-1][0] == ['nmcli', 'dev', 'wifi', 'connect',
                                   'CoffeeShop']
        assert [c for c, _t in nm.calls if 'connect' in c] == [
            nm.calls[-1][0]]


class TestConnectWifiPassword:
    """The PSK must travel via nmcli's passwd-file channel (nmcli(1):
    `connection up ... [passwd-file file]`), never on the command line."""

    def test_password_never_in_argv_secret_via_passwd_file(self):
        nm = _FakeNm()
        ok, msg = connect_wifi('HomeNet', 'hunter2', run=nm.run)
        assert ok
        assert msg == 'Connected to HomeNet'
        argv_words = [word for cmd, _t in nm.calls for word in cmd]
        assert 'hunter2' not in argv_words
        add_cmd = next(cmd for cmd, _t in nm.calls if 'add' in cmd)
        up_cmd = next(cmd for cmd, _t in nm.calls if 'up' in cmd)
        assert add_cmd[:6] == ['nmcli', 'connection', 'add', 'type',
                               'wifi', 'con-name']
        assert add_cmd[add_cmd.index('ssid') + 1] == 'HomeNet'
        assert add_cmd[add_cmd.index('wifi-sec.key-mgmt') + 1] == 'wpa-psk'
        new_uuid = add_cmd[add_cmd.index('connection.uuid') + 1]
        assert up_cmd[:5] == ['nmcli', 'connection', 'up', 'uuid', new_uuid]
        assert up_cmd[5] == 'passwd-file'
        path = up_cmd[6]
        assert nm.contents[path] == '802-11-wireless-security.psk:hunter2\n'
        assert not Path(path).exists()

    def test_temp_file_is_0600_while_nmcli_runs(self):
        nm = _FakeNm()
        connect_wifi('HomeNet', 'hunter2', run=nm.run)
        assert list(nm.modes.values()) == [0o600]

    def test_saved_profile_brings_up_by_uuid_without_adding(self):
        nm = _FakeNm(profiles={'HomeNet'})
        ok, msg = connect_wifi('HomeNet', 'newpsk', run=nm.run)
        assert ok
        assert msg == 'Connected to HomeNet'
        assert not any('add' in cmd for cmd, _t in nm.calls)
        up_cmd = nm.calls[-1][0]
        assert up_cmd[:5] == ['nmcli', 'connection', 'up', 'uuid',
                              nm.uuid_of('HomeNet')]
        assert up_cmd[5] == 'passwd-file'

    def test_saved_profile_needs_no_password(self):
        nm = _FakeNm(profiles={'My home': 'HomeNet'})
        ok, msg = connect_wifi('HomeNet', None, run=nm.run,
                               security='WPA2')
        assert ok and msg == 'Connected to HomeNet'
        assert nm.calls[-1][0] == ['nmcli', 'connection', 'up', 'uuid',
                                   nm.uuid_of('My home')]

    def test_profile_named_like_the_ssid_is_not_used(self):
        nm = _FakeNm(profiles={'HomeNet': 'OtherSSID'})
        connect_wifi('HomeNet', 'hunter2', run=nm.run)
        up_cmd = nm.calls[-1][0]
        assert up_cmd[4] != nm.uuid_of('HomeNet')
        assert any('add' in cmd for cmd, _t in nm.calls)

    def test_saved_enterprise_profile_keeps_eap(self):
        nm = _FakeNm(profiles={'Corp'}, key_mgmt='wpa-eap')
        ok, _msg = connect_wifi('Corp', 'pw', run=nm.run,
                                security='WPA2 802.1X')
        assert ok
        assert not any('add' in cmd for cmd, _t in nm.calls)
        assert list(nm.contents.values()) == ['802-1x.password:pw\n']

    def test_unsaved_enterprise_network_is_not_forced_to_psk(self):
        nm = _FakeNm()
        ok, msg = connect_wifi('Corp', 'pw', run=nm.run,
                               security='WPA2 802.1X')
        assert not ok
        assert 'enterprise' in msg
        assert not any('add' in cmd or 'up' in cmd for cmd, _t in nm.calls)

    def test_race_on_add_is_tolerated(self):
        nm = _FakeNm(add_code=10)
        real_run = nm.run

        def run(cmd, timeout=15):
            result = real_run(cmd, timeout)
            if 'add' in cmd:
                nm.store['uuid-race'] = {'id': 'HomeNet', 'ssid': 'HomeNet',
                                         'key_mgmt': 'wpa-psk'}
            return result

        ok, msg = connect_wifi('HomeNet', 'hunter2', run=run)
        assert ok
        assert msg == 'Connected to HomeNet'
        assert nm.calls[-1][0][:5] == ['nmcli', 'connection', 'up', 'uuid',
                                       'uuid-race']
        paths = _passwd_paths(nm.calls)
        assert len(paths) == 1
        assert not Path(paths[0]).exists()

    def test_wpa3_only_profile_uses_sae(self):
        nm = _FakeNm()
        connect_wifi('HomeNet', 'hunter2', run=nm.run, security='WPA3')
        add_cmd = next(cmd for cmd, _t in nm.calls if 'add' in cmd)
        assert add_cmd[add_cmd.index('wifi-sec.key-mgmt') + 1] == 'sae'

    def test_mixed_security_stays_wpa_psk(self):
        for security in (None, '', 'WPA2', 'WPA1 WPA2', 'WPA2 WPA3'):
            nm = _FakeNm()
            connect_wifi('HomeNet', 'hunter2', run=nm.run, security=security)
            add_cmd = next(cmd for cmd, _t in nm.calls if 'add' in cmd)
            assert add_cmd[add_cmd.index('wifi-sec.key-mgmt') + 1] == 'wpa-psk'

    def test_key_mgmt_helper(self):
        assert _key_mgmt_for('WPA3') == 'sae'
        assert _key_mgmt_for('wpa3') == 'sae'
        assert _key_mgmt_for('WPA2') == 'wpa-psk'
        assert _key_mgmt_for('WPA2 WPA3') == 'wpa-psk'
        assert _key_mgmt_for(None) == 'wpa-psk'
        assert _key_mgmt_for('open') == 'wpa-psk'

    def test_created_profile_deleted_on_bad_secrets(self):
        nm = _FakeNm(
            up_code=4,
            up_stderr='Error: Connection activation failed: '
                      'Secrets were required, but not provided.')
        ok, msg = connect_wifi('HomeNet', 'wrong', run=nm.run)
        assert not ok
        assert 'Secrets were required' in msg
        assert 'HomeNet' not in nm.profiles
        delete_cmd = nm.calls[-1][0]
        assert delete_cmd[:4] == ['nmcli', 'connection', 'delete', 'uuid']
        paths = _passwd_paths(nm.calls)
        assert all(not Path(path).exists() for path in paths)

    def test_preexisting_profile_kept_on_bad_secrets(self):
        nm = _FakeNm(
            profiles={'HomeNet'}, up_code=4,
            up_stderr='Error: Secrets were required, but not provided.')
        ok, _msg = connect_wifi('HomeNet', 'wrong', run=nm.run)
        assert not ok
        assert 'HomeNet' in nm.profiles
        assert not any('delete' in cmd for cmd, _t in nm.calls)

    def test_non_secrets_failure_keeps_created_profile(self):
        nm = _FakeNm(up_code=4, up_stderr='Error: Connection activation failed')
        ok, msg = connect_wifi('HomeNet', 'hunter2', run=nm.run)
        assert not ok
        assert msg == 'Error: Connection activation failed'
        assert 'HomeNet' in nm.profiles
        assert not any('delete' in cmd for cmd, _t in nm.calls)

    def test_file_removed_when_nmcli_missing(self):
        def run(cmd, timeout=15):
            raise OSError('no nmcli')

        ok, msg = connect_wifi('HomeNet', 'hunter2', run=run)
        assert not ok
        assert msg == 'NetworkManager unavailable'

    def test_open_network_unavailable_message(self):
        def run(cmd, timeout=15):
            raise OSError('no nmcli')

        ok, msg = connect_wifi('CoffeeShop', run=run)
        assert not ok
        assert msg == 'NetworkManager unavailable'


class TestWifiProfilesBySsid:
    def test_forget_deletes_only_profiles_with_that_ssid(self):
        from system_settings import forget_wifi
        nm = _FakeNm(profiles={'HomeNet': 'Neighbor', 'Home 2': 'HomeNet'})
        ok, msg = forget_wifi('HomeNet', run=nm.run)
        assert ok and msg == 'Forgot HomeNet'
        assert nm.profiles == {'HomeNet'}

    def test_forget_unknown_ssid_deletes_nothing(self):
        from system_settings import forget_wifi
        nm = _FakeNm(profiles={'HomeNet': 'Neighbor'})
        ok, _msg = forget_wifi('HomeNet', run=nm.run)
        assert not ok
        assert nm.profiles == {'HomeNet'}

    def test_disconnect_acts_on_the_active_uuid(self):
        from system_settings import disconnect_wifi
        nm = _FakeNm(profiles={'Cafe': 'Cafe:5G', 'Other': 'Other'},
                     active={'Cafe'})
        ok, msg = disconnect_wifi('Cafe:5G', run=nm.run)
        assert ok and msg == 'Disconnected from Cafe:5G'
        assert nm.calls[-1][0] == ['nmcli', 'connection', 'down', 'uuid',
                                   nm.uuid_of('Cafe')]

    def test_saved_ssids_come_from_the_profiles(self):
        from system_settings import saved_wifi_ssids
        nm = _FakeNm(profiles={'Home': 'b:net', 'Work': 'a-net',
                               'dup': 'a-net'})
        assert saved_wifi_ssids(run=nm.run) == ['a-net', 'b:net']
