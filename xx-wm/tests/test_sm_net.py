"""Lane sm_net tests: WiFi/Bluetooth completeness, mic picker, services, firewall.

Backends run with injected fakes (never real package/service/firewall
changes); helper core runs against tmp dirs and a recording ``nft``;
widget flows build against recorder fakes (the test_w2_settings idiom).
"""
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'helper'))

import system_settings  # noqa: E402
import xx_wm_helper as helper  # noqa: E402

SRC = Path(__file__).parent.parent / 'launcher' / 'src'


def _completed(cmd, code=0, stdout='', stderr=''):
    return subprocess.CompletedProcess(cmd, code, stdout=stdout, stderr=stderr)


def _vbool(value=True):
    from gi.repository import GLib
    return GLib.Variant('v', GLib.Variant('b', bool(value)))


def _vstr(value):
    from gi.repository import GLib
    return GLib.Variant('v', GLib.Variant('s', str(value)))


def _run_fn(mapping):
    """Fake ``run``: (program, tuple-of-middle-args) -> CompletedProcess."""
    calls = []

    def run(cmd, timeout=15):
        calls.append((list(cmd), timeout))
        for key, proc in mapping.items():
            if cmd[:1] == [key[0]] and list(cmd[1:1 + len(key[1])]) == list(key[1]):
                return proc
        return _completed(cmd)
    run.calls = calls
    return run


# ------------------------------------------------------------- WiFi backend


class TestNmRunning:
    def test_running(self):
        run = _run_fn({('nmcli', ('-t', '-f', 'RUNNING', 'general')):
                       _completed([], stdout='running\n')})
        assert system_settings.nm_running(run=run) is True

    def test_not_running(self):
        run = _run_fn({('nmcli', ('-t', '-f', 'RUNNING', 'general')):
                       _completed([], stdout='connecting\n')})
        assert system_settings.nm_running(run=run) is False

    def test_missing_nmcli(self):
        def run(cmd, timeout=15):
            raise OSError('no nmcli')
        assert system_settings.nm_running(run=run) is False


class TestWifiSwitch:
    def test_enabled_states(self):
        for stdout, expected in (('enabled\n', True), ('disabled\n', False)):
            run = _run_fn({('nmcli', ('-t', '-f', 'WIFI', 'general')):
                           _completed([], stdout=stdout)})
            assert system_settings.wifi_enabled(run=run) is expected

    def test_unknown_on_error(self):
        run = _run_fn({('nmcli', ('-t', '-f', 'WIFI', 'general')):
                       _completed([], code=1)})
        assert system_settings.wifi_enabled(run=run) is None

    def test_set_on_off_argv(self):
        run = _run_fn({})
        assert system_settings.set_wifi_enabled(True, run=run) is True
        assert system_settings.set_wifi_enabled(False, run=run) is True
        argvs = [cmd for cmd, _t in run.calls]
        assert ['nmcli', 'radio', 'wifi', 'on'] in argvs
        assert ['nmcli', 'radio', 'wifi', 'off'] in argvs

    def test_set_failure(self):
        def run(cmd, timeout=15):
            return _completed(cmd, code=1)
        assert system_settings.set_wifi_enabled(True, run=run) is False


class TestWifiDisconnectForget:
    def test_disconnect_argv(self):
        run = _run_fn({})
        ok, msg = system_settings.disconnect_wifi('HomeNet', run=run)
        assert ok and 'HomeNet' in msg
        assert [c for c, _ in run.calls] == [
            ['nmcli', 'connection', 'down', 'HomeNet']]

    def test_forget_argv(self):
        run = _run_fn({})
        ok, msg = system_settings.forget_wifi('HomeNet', run=run)
        assert ok and 'HomeNet' in msg
        assert [c for c, _ in run.calls] == [
            ['nmcli', 'connection', 'delete', 'HomeNet']]

    def test_disconnect_failure_message(self):
        run = _run_fn({('nmcli', ('connection', 'down', 'HomeNet')):
                       _completed([], code=10,
                                  stderr='Error: no such connection.')})
        ok, msg = system_settings.disconnect_wifi('HomeNet', run=run)
        assert not ok
        assert 'no such connection' in msg


ACTIVE_CONNS = 'HomeNet:802-11-wireless:wlan0\nWired:ethernet:eth0\n'
DEV_SHOW = """\
GENERAL.DEVICE:                         wlan0
GENERAL.HWADDR:                         AA:BB:CC:DD:EE:FF
IP4.ADDRESS[1]:                         192.168.1.5/24
IP4.GATEWAY:                            192.168.1.1
IP4.DNS[1]:                             192.168.1.1
IP4.DNS[2]:                             9.9.9.9
"""


class TestWifiDetails:
    def _run(self):
        return _run_fn({
            ('nmcli', ('-t', '-f', 'NAME,TYPE,DEVICE', 'connection',
                       'show', '--active')): _completed([], stdout=ACTIVE_CONNS),
            ('nmcli', ('-t', 'device', 'show', 'wlan0')):
                _completed([], stdout=DEV_SHOW),
        })

    def test_details_fields(self):
        details = system_settings.wifi_details('HomeNet', run=self._run())
        assert details == {'ip': '192.168.1.5', 'gateway': '192.168.1.1',
                           'dns': '192.168.1.1, 9.9.9.9',
                           'mac': 'AA:BB:CC:DD:EE:FF'}

    def test_unknown_ssid_is_none(self):
        assert system_settings.wifi_details(
            'Nope', run=self._run()) is None

    def test_summary_text(self):
        details = system_settings.wifi_details('HomeNet', run=self._run())
        text = system_settings.wifi_details_text('HomeNet', details)
        assert '192.168.1.5' in text and 'AA:BB:CC:DD:EE:FF' in text

    def test_missing_details_text(self):
        assert 'unavailable' in system_settings.wifi_details_text(
            'HomeNet', None).lower()


class TestHiddenNetwork:
    def test_open_hidden_adds_flag(self):
        calls = []

        def run(cmd, timeout=15):
            calls.append(list(cmd))
            return _completed(cmd)
        ok, _msg = system_settings.connect_wifi('Hidden', run=run,
                                                hidden=True)
        assert ok
        assert calls == [['nmcli', 'dev', 'wifi', 'connect', 'Hidden',
                          'hidden', 'yes']]

    def test_psk_hidden_profile_sets_hidden(self):
        from test_system_settings import _FakeNm
        nm = _FakeNm()
        ok, _msg = system_settings.connect_wifi('Hidden', 'pw', run=nm.run,
                                                hidden=True)
        assert ok
        add_cmd = nm.calls[1][0]
        assert 'wifi.hidden' in add_cmd
        assert add_cmd[add_cmd.index('wifi.hidden') + 1] == 'yes'


# ------------------------------------------------------- Bluetooth backend


class FakeBluezBus:
    """Recording BlueZ bus: GetManagedObjects raises (→ canonical path
    fallback) unless objects are given; Get returns True; Set/call ok."""

    def __init__(self, objects=None):
        self.calls = []
        self.objects = objects

    def call_sync(self, bus, path, iface, method, params, reply,
                  flags, timeout, cancel):
        self.calls.append((path, '%s.%s' % (iface, method)))
        if method == 'GetManagedObjects':
            if self.objects is None:
                raise Exception('org.bluez.Error.NotReady')
            return SimpleNamespace(unpack=lambda: (self.objects,))
        if method == 'Get':
            return SimpleNamespace(unpack=lambda: (_vbool(),))
        return None


def _patch_bluez(monkeypatch, bus):
    monkeypatch.setattr(system_settings, '_bluez_bus', lambda: bus)
    monkeypatch.setattr(system_settings, '_adapter_path',
                        lambda b=None: '/org/bluez/hci0')


class TestBtDeviceActions:
    def test_disconnect_calls_device_disconnect(self, monkeypatch):
        bus = FakeBluezBus()
        _patch_bluez(monkeypatch, bus)
        ok, msg = system_settings.disconnect_bt_device('AA:BB:CC:DD:EE:FF')
        assert ok and msg == 'Disconnected'
        assert ('/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF',
                'org.bluez.Device1.Disconnect') in bus.calls

    def test_disconnect_blocked_error_surfaces(self, monkeypatch):
        class BlockedBus(FakeBluezBus):
            def call_sync(self, *args):
                raise Exception('GDBus org.bluez.Error.Blocked: soft blocked')
        bus = BlockedBus()
        _patch_bluez(monkeypatch, bus)
        ok, msg = system_settings.disconnect_bt_device('AA:BB:CC:DD:EE:FF')
        assert not ok
        assert 'Blocked' in msg

    def test_forget_calls_remove_device(self, monkeypatch):
        bus = FakeBluezBus()
        _patch_bluez(monkeypatch, bus)
        ok, msg = system_settings.forget_bt_device('AA:BB:CC:DD:EE:FF')
        assert ok and 'Forgotten' in msg
        assert ('/org/bluez/hci0',
                'org.bluez.Adapter1.RemoveDevice') in bus.calls

    def test_rename_sets_alias(self, monkeypatch):
        bus = FakeBluezBus()
        _patch_bluez(monkeypatch, bus)
        ok, msg = system_settings.rename_bt_device(
            'AA:BB:CC:DD:EE:FF', 'My Pods')
        assert ok
        assert ('/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF',
                'org.freedesktop.DBus.Properties.Set') in bus.calls

    def test_rename_rejects_blank(self, monkeypatch):
        _patch_bluez(monkeypatch, FakeBluezBus())
        ok, msg = system_settings.rename_bt_device(
            'AA:BB:CC:DD:EE:FF', '   ')
        assert not ok and 'empty' in msg.lower()


class TestBtPower:
    def test_power_round_trip(self, monkeypatch):
        bus = FakeBluezBus()
        _patch_bluez(monkeypatch, bus)
        assert system_settings.bt_powered() is True
        assert system_settings.set_bt_powered(False) is True
        assert ('/org/bluez/hci0',
                'org.freedesktop.DBus.Properties.Set') in bus.calls

    def test_power_unknown_when_down(self, monkeypatch):
        monkeypatch.setattr(system_settings, '_bluez_bus',
                            lambda: (_ for _ in ()).throw(
                                Exception('no bluez')))
        assert system_settings.bt_powered() is None
        assert system_settings.set_bt_powered(True) is False


def _rfkill_tree(tmp_path, entries):
    base = tmp_path / 'class' / 'rfkill'
    for name, rtype, soft in entries:
        node = base / name
        node.mkdir(parents=True)
        (node / 'type').write_text(rtype + '\n')
        (node / 'soft').write_text(str(soft) + '\n')
    return str(base)


class TestBtRfkill:
    def test_blocked_when_soft_file_set(self, tmp_path):
        _rfkill_tree(tmp_path, [('rfkill0', 'bluetooth', 1)])
        assert system_settings.bt_soft_blocked(
            sysfs=str(tmp_path)) is True

    def test_clear_when_unblocked(self, tmp_path):
        _rfkill_tree(
            tmp_path, [('rfkill0', 'bluetooth', 0),
                       ('rfkill1', 'wlan', 1)])
        assert system_settings.bt_soft_blocked(
            sysfs=str(tmp_path)) is False

    def test_clear_when_absent(self, tmp_path):
        assert system_settings.bt_soft_blocked(
            sysfs=str(tmp_path)) is False

    def test_unblock_writes_event_when_writable(self):
        written = {}

        def fake_access(path, mode):
            return True

        def fake_open(path, flags):
            written['path'] = path
            return 7

        def fake_write(fd, data):
            written['fd'] = fd
            written['buf'] = bytes(data)
            return len(data)

        def fake_close(fd):
            written['closed'] = fd
        assert system_settings.rfkill_unblock(
            'bluetooth', dev='/dev/rfkill', _access=fake_access,
            _open=fake_open, _write=fake_write,
            _close=fake_close) is True
        assert written['closed'] == 7
        assert written['path'] == '/dev/rfkill'
        assert written['buf'] == system_settings.rfkill_event_bytes(
            system_settings.RFKILL_TYPE_BLUETOOTH)

    def test_unblock_false_when_not_writable(self):
        assert system_settings.rfkill_unblock(
            'bluetooth', dev='/dev/rfkill',
            _access=lambda p, m: False) is False

    def test_unblock_rejects_unknown_type(self):
        assert system_settings.rfkill_unblock(
            'nfc', _access=lambda p, m: True) is False

    def test_event_bytes_shape(self):
        import struct
        for rtype in ('wifi', 'bluetooth'):
            raw = system_settings.rfkill_event_bytes(
                system_settings.RFKILL_TYPES[rtype])
            idx, rtype_b, op, soft, hard = struct.unpack('<IBBBB', raw)
            assert op == system_settings.RFKILL_OP_CHANGE_ALL
            assert soft == 0


# ---------------------------------------------------------- Microphone


SOURCES_JSON = '''[
  {"name": "alsa_input.usb", "description": "USB Mic",
    "properties": {"device.class": "sound"}},
  {"name": "alsa_input.pci", "description": "Internal Mic",
    "properties": {"device.class": "sound"}},
  {"name": "alsa_output.usb.monitor", "description": "Monitor of USB",
    "properties": {"device.class": "monitor"}},
  {"name": "echo_cancel", "description": "Echo Cancel",
    "properties": {"device.class": "monitor"}},
  {"description": "nameless"}
]'''


class TestSources:
    def test_excludes_monitors_marks_default(self):
        sources = system_settings.parse_pactl_sources(
            SOURCES_JSON, 'alsa_input.pci')
        assert [s.description for s in sources] == ['USB Mic', 'Internal Mic']
        assert [s.is_default for s in sources] == [False, True]

    def test_invalid_json(self):
        assert system_settings.parse_pactl_sources('nope', 'x') == []

    def test_list_and_set_argv(self):
        run = _run_fn({
            ('pactl', ('-f', 'json', 'list', 'sources')):
                _completed([], stdout=SOURCES_JSON),
            ('pactl', ('get-default-source',)):
                _completed([], stdout='alsa_input.pci\n'),
        })
        sources = system_settings.list_audio_sources(run=run)
        assert [s.name for s in sources] == ['alsa_input.usb',
                                            'alsa_input.pci']
        assert system_settings.set_default_source(
            'alsa_input.usb', run=run) is True
        assert ['pactl', 'set-default-source',
                'alsa_input.usb'] in [c for c, _ in run.calls]

    def test_unavailable_is_none(self):
        def run(cmd, timeout=15):
            raise OSError('no pactl')
        assert system_settings.list_audio_sources(run=run) is None


OUTPUTS_JSON = '''[
  {"name": "alsa_output.usb", "description": "USB Headset",
    "properties": {"device.class": "sound"}},
  {"name": "alsa_output.spdif", "description": "Digital Stereo (IEC958)",
    "properties": {"device.class": "sound"}},
  {"name": "null_music", "description": "Music",
    "properties": {"device.class": "sound", "node.virtual": true}}
]'''


class TestOutputFiltering:
    def test_hides_iec958_and_virtual_by_default(self):
        sinks = system_settings.parse_pactl_sinks(OUTPUTS_JSON, 'x')
        assert [s.name for s in sinks] == ['alsa_output.usb']

    def test_shown_when_not_hiding(self):
        sinks = system_settings.parse_pactl_sinks(
            OUTPUTS_JSON, 'x', hide_special=False)
        assert len(sinks) == 3


# ------------------------------------------------------------ Services


class TestDetectInit:
    def test_systemd(self, tmp_path):
        (tmp_path / 'run' / 'systemd' / 'system').mkdir(parents=True)
        assert system_settings.detect_init(root=str(tmp_path)) == 'systemd'

    def test_openrc(self, tmp_path):
        (tmp_path / 'sbin').mkdir(parents=True)
        (tmp_path / 'sbin' / 'openrc-run').write_text('x')
        assert system_settings.detect_init(root=str(tmp_path)) == 'openrc'

    def test_unknown(self, tmp_path):
        assert system_settings.detect_init(root=str(tmp_path)) == 'unknown'


class FakeSystemdBus:
    """Minimal org.freedesktop.systemd1 Manager: unit path + props."""

    def __init__(self, units):
        self.units = units
        self.calls = []

    def call_sync(self, bus, path, iface, method, params, reply,
                  flags, timeout, cancel):
        self.calls.append((path, '%s.%s' % (iface, method)))
        if method == 'GetUnit':
            (name,) = params.unpack()
            if name not in self.units:
                raise Exception('org.freedesktop.systemd1.NoSuchUnit')
            return SimpleNamespace(unpack=lambda: (
                '/org/freedesktop/systemd1/unit/%s' % name,))
        if method == 'Get':
            (_iface, prop) = params.unpack()
            unit = path.rsplit('/', 1)[-1]
            value = self.units[unit][prop]
            return SimpleNamespace(unpack=lambda: (_vstr(value),))
        if method == 'GetUnitFileState':
            (name,) = params.unpack()
            return SimpleNamespace(
                unpack=lambda: (self.units[name]['UnitFileState'],))
        raise AssertionError(method)


class TestSystemdState:
    def _bus(self):
        return FakeSystemdBus({
            'NetworkManager.service': {'ActiveState': 'active',
                                       'UnitFileState': 'enabled'},
            'sshd.service': {'ActiveState': 'inactive',
                             'UnitFileState': 'disabled'},
        })

    def test_active_enabled(self):
        state = system_settings.systemd_unit_state(
            'NetworkManager.service', bus=self._bus())
        assert state == ('active', 'enabled')

    def test_inactive_disabled(self):
        state = system_settings.systemd_unit_state(
            'sshd.service', bus=self._bus())
        assert state == ('inactive', 'disabled')

    def test_unknown_unit_is_none(self):
        assert system_settings.systemd_unit_state(
            'nope.service', bus=self._bus()) is None


RC_UPDATE_SHOW = """\
         NetworkManager | default
               bluetooth | default
                    sshd |
"""


class TestOpenrcState:
    def _run(self, status_code=0):
        return _run_fn({
            ('rc-service', ('bluetooth', 'status')):
                _completed([], code=status_code,
                           stdout=' * status: started\n'),
            ('rc-update', ('show',)):
                _completed([], stdout=RC_UPDATE_SHOW),
        })

    def test_running_enabled(self):
        assert system_settings.openrc_service_state(
            'bluetooth', run=self._run()) == (True, True)

    def test_stopped(self):
        assert system_settings.openrc_service_state(
            'bluetooth', run=self._run(status_code=3)) == (False, True)

    def test_not_enabled(self):
        assert system_settings.openrc_service_state(
            'sshd', run=self._run()) == (True, False)

    def test_unavailable_is_none(self):
        def run(cmd, timeout=15):
            raise OSError('no openrc')
        assert system_settings.openrc_service_state(
            'bluetooth', run=run) == (None, None)


FAILED_SYSTEMCTL = """\
NetworkManager.service loaded failed failed Network Manager
sshd.service loaded failed failed OpenSSH daemon
"""


class TestFailedUnits:
    def test_systemd_failed(self):
        run = _run_fn({('systemctl', ('--failed', '--no-pager',
                                      '--no-legend', '--plain')):
                       _completed([], stdout=FAILED_SYSTEMCTL)})
        assert system_settings.list_failed_units(
            'systemd', run=run) == ['NetworkManager.service', 'sshd.service']

    def test_systemd_status_glyph_is_not_the_unit(self):
        # systemd marks failed rows with "●"; the Failed list showed that
        # glyph as the unit's name.
        out = '● NetworkManager-wait-online.service loaded failed failed X\n'
        run = _run_fn({('systemctl', ('--failed', '--no-pager',
                                      '--no-legend', '--plain')):
                       _completed([], stdout=out)})
        assert system_settings.list_failed_units('systemd', run=run) == [
            'NetworkManager-wait-online.service']

    def test_openrc_crashed(self):
        out = 'bluetooth [ started ]\nsshd [ crashed ]\n'
        run = _run_fn({('rc-status', ('default',)):
                       _completed([], stdout=out)})
        assert system_settings.list_failed_units(
            'openrc', run=run) == ['sshd']

    def test_unknown_init_empty(self):
        assert system_settings.list_failed_units('unknown') == []


# ------------------------------------------------------------ Firewall pure

import settings_page  # noqa: E402  (first: section modules bind its _Section)
import settings_firewall as firewall  # noqa: E402
import settings_services as services  # noqa: E402


class TestInboundPresets:
    def test_only_named_presets(self):
        assert sorted(firewall.PRESETS) == [
            'kdeconnect', 'nearby', 'ssh', 'syncthing', 'xxdb']

    def test_nearby_ports(self):
        assert firewall.preset_ufw_specs('nearby') == [
            '53317/tcp', '53317/udp']

    def test_xxdb_range_covers_daemon(self):
        import xxdb
        specs = firewall.preset_ufw_specs('xxdb')
        assert specs == ['37000:37999/tcp']
        assert xxdb.PORT_MIN == 37000 and xxdb.PORT_MAX == 37999

    def test_kde_range(self):
        assert firewall.preset_ufw_specs('kdeconnect') == [
            '1714:1764/tcp', '1714:1764/udp']

    def test_syncthing_ports(self):
        assert firewall.preset_ufw_specs('syncthing') == [
            '22000/tcp', '22000/udp', '21027/udp']

    def test_ssh(self):
        assert firewall.preset_ufw_specs('ssh') == ['22/tcp']

    def test_unknown_preset_rejected(self):
        with pytest.raises(KeyError):
            firewall.preset_ufw_specs('http-alt')

    def test_nft_render_only_allowlisted(self):
        text = firewall.render_nft_rules({'nearby', 'ssh'})
        assert 'tcp dport 53317 accept' in text
        assert 'tcp dport 22 accept' in text
        assert '1714-1764' not in text

    def test_nft_render_range(self):
        text = firewall.render_nft_rules({'kdeconnect'})
        assert 'tcp dport 1714-1764 accept' in text
        assert 'udp dport 1714-1764 accept' in text

    def test_nft_render_rejects_unknown(self):
        with pytest.raises(KeyError):
            firewall.render_nft_rules({'http-alt'})

    def test_nft_render_honesty_note(self):
        text = firewall.render_nft_rules(set())
        assert 'cannot override' in text


class TestServiceCatalog:
    def test_curated_keys(self):
        assert [s.key for s in services.SERVICES] == [
            'modem', 'eg25', 'network', 'bluetooth', 'ssh', 'adbd',
            'tailscale', 'waydroid']

    def test_protected_units_absent(self):
        text = repr([(s.key, s.systemd_units, s.openrc_names)
                     for s in services.SERVICES])
        for banned in ('xx-wm', 'dbus', 'logind', 'systemd-'):
            assert banned not in text

    def test_ssh_alias_resolves(self):
        ssh = next(s for s in services.SERVICES if s.key == 'ssh')
        assert 'sshd.service' in ssh.systemd_units
        assert 'ssh.service' in ssh.systemd_units

    def test_tailscale_matches_openrc_service_name(self):
        tailscale = next(s for s in services.SERVICES if s.key == 'tailscale')
        assert 'tailscale' in tailscale.openrc_names
        assert 'tailscaled.service' in tailscale.systemd_units

    def test_state_text(self):
        assert services.state_text(
            ('active', 'enabled')) == 'On, starts at boot'
        assert services.state_text(('inactive', 'disabled')) == 'Off'
        assert services.state_text((None, None)) == 'Unavailable'
        assert services.state_text(
            ('failed', 'enabled')) == 'Failed, starts at boot'


# ------------------------------------------------------ Helper: services


class FakeHelperRun:
    """Recording argv-list stand-in for subprocess.run (no shell)."""

    def __init__(self, available=('nft', 'ufw'), status_out='',
                 ruleset=''):
        self.cmds = []
        self.available = set(available)
        self.status_out = status_out
        self.ruleset = ruleset

    def __call__(self, argv):
        assert isinstance(argv, list), argv
        assert argv and all(isinstance(a, str) for a in argv), argv
        self.cmds.append(list(argv))
        if argv[:2] == ['ufw', 'status']:
            return SimpleNamespace(returncode=0, stdout=self.status_out,
                                   stderr='')
        if argv[:3] == ['nft', 'list', 'ruleset']:
            return SimpleNamespace(returncode=0, stdout=self.ruleset,
                                   stderr='')
        return SimpleNamespace(returncode=0, stdout='', stderr='')


def make_helper_core(tmp_path, run=None, **over):
    kw = dict(sysfs=str(tmp_path / 'sys'),
              cgroup_root=str(tmp_path / 'cg'),
              mounts_file=str(tmp_path / 'mounts'),
              state_file=str(tmp_path / 'state.json'),
              nft_bin='nft',
              nft_dropin_dir=str(tmp_path / 'nftables.d'),
              run=run or FakeHelperRun())
    kw.update(over)
    return helper.HelperCore(**kw)


class TestManageServiceAllowlist:
    def test_rejects_non_allowlisted(self, tmp_path):
        core = make_helper_core(tmp_path)
        for name in ('xx-wm', 'dbus', 'dbus-broker', 'systemd-logind',
                     'logind', 'polkit', 'reboot.target', '../../etc/passwd',
                     'NetworkManager; rm -rf /', ''):
            with pytest.raises(helper.HelperError) as exc:
                core.manage_service(name, 'start')
            assert exc.value.dbus_name == helper.ERROR_INVALID_ARGS, name

    def test_rejects_bad_action(self, tmp_path):
        core = make_helper_core(tmp_path)
        with pytest.raises(helper.HelperError) as exc:
            core.manage_service('bluetooth', 'mask')
        assert exc.value.dbus_name == helper.ERROR_INVALID_ARGS

    def test_systemd_argv(self, tmp_path, monkeypatch):
        run = FakeHelperRun()
        core = make_helper_core(tmp_path, run)
        monkeypatch.setattr(core, 'init_system', lambda: 'systemd')
        monkeypatch.setattr(core, 'resolve_unit',
                            lambda key: 'bluetooth.service')
        assert core.manage_service('bluetooth', 'restart') == 'restart'
        assert run.cmds[-1] == ['systemctl', 'restart', 'bluetooth.service']

    def test_openrc_argv(self, tmp_path, monkeypatch):
        run = FakeHelperRun()
        core = make_helper_core(tmp_path, run)
        monkeypatch.setattr(core, 'init_system', lambda: 'openrc')
        assert core.manage_service('bluetooth', 'start') == 'start'
        assert run.cmds[-1] == ['rc-service', 'bluetooth', 'start']
        assert core.manage_service('bluetooth', 'enable') == 'enable'
        assert run.cmds[-1] == ['rc-update', 'add', 'bluetooth', 'default']
        assert core.manage_service('bluetooth', 'disable') == 'disable'
        assert run.cmds[-1] == ['rc-update', 'del', 'bluetooth', 'default']

    def test_no_shell_anywhere(self, tmp_path, monkeypatch):
        run = FakeHelperRun()
        core = make_helper_core(tmp_path, run)
        monkeypatch.setattr(core, 'init_system', lambda: 'openrc')
        for action in ('start', 'stop', 'restart', 'enable', 'disable'):
            core.manage_service('ssh', action)
        assert all(isinstance(cmd, list) for cmd in run.cmds)


class TestSetRfkill:
    def test_allowlist(self, tmp_path):
        run = FakeHelperRun()
        core = make_helper_core(tmp_path, run)
        assert core.set_rfkill('bluetooth', False) is False
        assert core.set_rfkill('wifi', True) is True
        with pytest.raises(helper.HelperError) as exc:
            core.set_rfkill('nfc', False)
        assert exc.value.dbus_name == helper.ERROR_INVALID_ARGS

    def test_argv(self, tmp_path):
        run = FakeHelperRun()
        core = make_helper_core(tmp_path, run)
        core.set_rfkill('bluetooth', False)
        assert run.cmds[-1] == ['rfkill', 'unblock', 'bluetooth']
        core.set_rfkill('wifi', True)
        assert run.cmds[-1] == ['rfkill', 'block', 'wifi']


UFW_ACTIVE = ('Status: active\n53317/tcp ALLOW Anywhere\n'
              '53317/udp ALLOW Anywhere\n22/tcp ALLOW Anywhere\n')


class TestHelperInboundPresets:
    def test_rejects_unknown_preset(self, tmp_path):
        core = make_helper_core(tmp_path, FakeHelperRun())
        with pytest.raises(helper.HelperError) as exc:
            core.set_inbound_preset('http-alt', True)
        assert exc.value.dbus_name == helper.ERROR_INVALID_ARGS

    def test_ufw_allow_delete_argv(self, tmp_path, monkeypatch):
        run = FakeHelperRun()
        core = make_helper_core(tmp_path, run)
        monkeypatch.setattr(core, 'firewall_backend', lambda: 'ufw')
        assert core.set_inbound_preset('nearby', True) is True
        assert ['ufw', 'allow', '53317/tcp'] in run.cmds
        assert ['ufw', 'allow', '53317/udp'] in run.cmds
        assert core.set_inbound_preset('nearby', False) is False
        assert ['ufw', '--force', 'delete', 'allow', '53317/tcp'] in run.cmds

    def test_nft_writes_state_and_rules(self, tmp_path, monkeypatch):
        run = FakeHelperRun(available=('nft',))
        core = make_helper_core(tmp_path, run)
        monkeypatch.setattr(core, 'firewall_backend', lambda: 'nftables')
        assert core.set_inbound_preset('ssh', True) is True
        inbound = core.load_inbound()
        assert inbound['allowed'] == ['ssh']
        assert any(cmd[:3] == ['nft', 'add', 'table'] for cmd in run.cmds)
        assert any('tcp dport 22' in ' '.join(cmd) for cmd in run.cmds)

    def test_nft_reload_never_touches_other_tables(self, tmp_path,
                                                   monkeypatch):
        run = FakeHelperRun(available=('nft',))
        core = make_helper_core(tmp_path, run)
        monkeypatch.setattr(core, 'firewall_backend', lambda: 'nftables')
        core.set_inbound_preset('ssh', True)
        for cmd in run.cmds:
            assert 'filter' not in cmd or 'xxwm' in cmd \
                or 'xx_wm' in cmd or 'xxwm-inbound' in cmd


class TestFirewallState:
    def test_ufw_active(self, tmp_path, monkeypatch):
        run = FakeHelperRun(status_out=UFW_ACTIVE)
        core = make_helper_core(tmp_path, run)
        monkeypatch.setattr(core, 'firewall_backend', lambda: 'ufw')
        state = core.get_firewall_state()
        assert state['backend'] == 'ufw'
        assert state['enabled'] is True
        assert state['allowed']['nearby'] is True
        assert state['allowed']['ssh'] is True
        assert state['allowed']['syncthing'] is False

    def test_ufw_inactive(self, tmp_path, monkeypatch):
        run = FakeHelperRun(status_out='Status: inactive\n')
        core = make_helper_core(tmp_path, run)
        monkeypatch.setattr(core, 'firewall_backend', lambda: 'ufw')
        state = core.get_firewall_state()
        assert state['enabled'] is False

    def test_none_backend(self, tmp_path, monkeypatch):
        core = make_helper_core(tmp_path, FakeHelperRun(available=()))
        monkeypatch.setattr(core, 'firewall_backend', lambda: 'none')
        state = core.get_firewall_state()
        assert state == {'backend': 'none', 'enabled': False,
                         'allowed': {}, 'foreign_drop': False}

    def test_foreign_drop_detected(self, tmp_path, monkeypatch):
        ruleset = 'table inet filter {\n chain input {\n policy drop;\n }\n}\n'
        run = FakeHelperRun(available=('nft',), ruleset=ruleset)
        core = make_helper_core(tmp_path, run)
        monkeypatch.setattr(core, 'firewall_backend', lambda: 'nftables')
        assert core.get_firewall_state()['foreign_drop'] is True

    def test_enable_disable_ufw(self, tmp_path, monkeypatch):
        run = FakeHelperRun()
        core = make_helper_core(tmp_path, run)
        monkeypatch.setattr(core, 'firewall_backend', lambda: 'ufw')
        assert core.set_firewall_enabled(True) is True
        assert ['ufw', '--force', 'enable'] in run.cmds
        assert core.set_firewall_enabled(False) is False
        assert ['ufw', 'disable'] in run.cmds

    def test_enable_keeps_ssh(self, tmp_path, monkeypatch):
        run = FakeHelperRun()
        core = make_helper_core(tmp_path, run)
        monkeypatch.setattr(core, 'firewall_backend', lambda: 'ufw')
        monkeypatch.setenv('SSH_CONNECTION', '1.2.3.4 22 5.6.7.8 555')
        core.set_firewall_enabled(True)
        assert ['ufw', 'allow', '22/tcp'] in run.cmds


class FakeInvocation:
    def __init__(self):
        self.value = None
        self.error = None

    def return_value(self, value):
        self.value = value

    def return_dbus_error(self, name, message):
        self.error = (name, message)


def _auth_service(monkeypatch, core, seen):
    def authorize(conn, sender, action, interactive, callback):
        seen.append((action, interactive))
        callback(True, 1000, None)
    return helper.HelperService(connection=object(), core=core,
                                authorize=authorize)


class TestHelperPolicy:
    def test_sshd_start_needs_admin_interactive(self, tmp_path,
                                                monkeypatch):
        run = FakeHelperRun()
        core = make_helper_core(tmp_path, run)
        monkeypatch.setattr(core, 'init_system', lambda: 'openrc')
        seen = []
        service = _auth_service(monkeypatch, core, seen)
        invocation = FakeInvocation()
        service._on_method_call(
            service.connection, ':1.7', '/p', 'i', 'ManageService',
            SimpleNamespace(unpack=lambda: ('ssh', 'start')), invocation)
        assert invocation.error is None
        assert seen == [(helper.ACTION_SERVICE_REMOTE, True)]

    def test_sshd_enable_needs_admin(self, tmp_path, monkeypatch):
        run = FakeHelperRun()
        core = make_helper_core(tmp_path, run)
        monkeypatch.setattr(core, 'init_system', lambda: 'openrc')
        seen = []
        service = _auth_service(monkeypatch, core, seen)
        invocation = FakeInvocation()
        service._on_method_call(
            service.connection, ':1.7', '/p', 'i', 'ManageService',
            SimpleNamespace(unpack=lambda: ('ssh', 'enable')), invocation)
        assert invocation.error is None
        assert seen == [(helper.ACTION_SERVICE_REMOTE, True)]

    def test_plain_restart_is_allow_active(self, tmp_path, monkeypatch):
        run = FakeHelperRun()
        core = make_helper_core(tmp_path, run)
        monkeypatch.setattr(core, 'init_system', lambda: 'openrc')
        seen = []
        service = _auth_service(monkeypatch, core, seen)
        invocation = FakeInvocation()
        service._on_method_call(
            service.connection, ':1.7', '/p', 'i', 'ManageService',
            SimpleNamespace(unpack=lambda: ('bluetooth', 'restart')),
            invocation)
        assert invocation.error is None
        assert seen == [(helper.ACTION_SERVICE, False)]

    def test_open_preset_needs_admin(self, tmp_path, monkeypatch):
        run = FakeHelperRun()
        core = make_helper_core(tmp_path, run)
        monkeypatch.setattr(core, 'firewall_backend', lambda: 'ufw')
        seen = []
        service = _auth_service(monkeypatch, core, seen)
        invocation = FakeInvocation()
        service._on_method_call(
            service.connection, ':1.7', '/p', 'i', 'SetInboundPreset',
            SimpleNamespace(unpack=lambda: ('ssh', True)), invocation)
        assert invocation.error is None
        assert seen == [(helper.ACTION_FW_OPEN, True)]

    def test_close_preset_is_allow_active(self, tmp_path, monkeypatch):
        run = FakeHelperRun()
        core = make_helper_core(tmp_path, run)
        monkeypatch.setattr(core, 'firewall_backend', lambda: 'ufw')
        seen = []
        service = _auth_service(monkeypatch, core, seen)
        invocation = FakeInvocation()
        service._on_method_call(
            service.connection, ':1.7', '/p', 'i', 'SetInboundPreset',
            SimpleNamespace(unpack=lambda: ('ssh', False)), invocation)
        assert invocation.error is None
        assert seen == [(helper.ACTION_FW_RESTRICT, False)]

    def test_disable_firewall_needs_admin(self, tmp_path, monkeypatch):
        run = FakeHelperRun()
        core = make_helper_core(tmp_path, run)
        monkeypatch.setattr(core, 'firewall_backend', lambda: 'ufw')
        seen = []
        service = _auth_service(monkeypatch, core, seen)
        invocation = FakeInvocation()
        service._on_method_call(
            service.connection, ':1.7', '/p', 'i', 'SetFirewallEnabled',
            SimpleNamespace(unpack=lambda: (False,)), invocation)
        assert invocation.error is None
        assert seen == [(helper.ACTION_FW_OPEN, True)]

    def test_rfkill_is_allow_active(self, tmp_path):
        core = make_helper_core(tmp_path, FakeHelperRun())
        seen = []
        service = _auth_service(None, core, seen)
        invocation = FakeInvocation()
        service._on_method_call(
            service.connection, ':1.7', '/p', 'i', 'SetRfkill',
            SimpleNamespace(unpack=lambda: ('bluetooth', False)),
            invocation)
        assert invocation.error is None
        assert seen == [(helper.ACTION_RFKILL, False)]

    def test_firewall_state_needs_no_auth(self, tmp_path):
        def explosive(conn, sender, action, interactive, callback):
            raise AssertionError('GetFirewallState must not authorize')
        service = helper.HelperService(
            connection=None, core=make_helper_core(tmp_path),
            authorize=explosive)
        invocation = FakeInvocation()
        service._on_method_call(None, ':1.7', '/p', 'i', 'GetFirewallState',
                                None, invocation)
        assert invocation.error is None

    def test_introspection_lists_new_methods(self):
        for method in ('ManageService', 'SetRfkill', 'SetInboundPreset',
                       'SetFirewallEnabled', 'GetFirewallState'):
            assert ('<method name="%s">' % method) in helper.INTROSPECT_XML


class TestCatalogMirror:
    def test_helper_presets_match_shell(self):
        assert (sorted(helper.INBOUND_PRESETS)
                == sorted(firewall.PRESETS))
        for name in firewall.PRESETS:
            assert (helper.HelperCore.ufw_specs(name)
                    == firewall.preset_ufw_specs(name))

    def test_service_catalog_matches_shell(self):
        assert (sorted(helper.SERVICE_ALLOW)
                == sorted(entry.key for entry in services.SERVICES))
        for entry in services.SERVICES:
            table = helper.SERVICE_UNITS[entry.key]
            assert tuple(table['systemd']) == tuple(entry.systemd_units)
            assert tuple(table['openrc']) == tuple(entry.openrc_names)

    def test_shell_catalog_has_no_banned_units(self):
        for entry in services.SERVICES:
            for unit in (tuple(entry.systemd_units)
                         + tuple(entry.openrc_names)):
                assert 'xx-wm' not in unit
                assert 'dbus' not in unit
                assert 'logind' not in unit


class TestFirewallStateVariant:
    def test_payload_round_trips_real_glib(self, tmp_path):
        pytest.importorskip('gi')

        def explosive(conn, sender, action, interactive, callback):
            raise AssertionError('GetFirewallState must not authorize')

        service = helper.HelperService(
            connection=None, core=make_helper_core(tmp_path),
            authorize=explosive)
        invocation = FakeInvocation()
        service._on_method_call(None, ':1.7', '/p', 'i',
                                'GetFirewallState', None, invocation)
        assert invocation.error is None
        state = invocation.value.unpack()[0]
        assert set(state) == {'backend', 'enabled', 'allowed',
                              'foreign_drop'}
        assert isinstance(state['allowed'], dict)

    def test_non_allowlisted_service_never_reaches_auth(self, tmp_path):
        def explosive(conn, sender, action, interactive, callback):
            raise AssertionError('rejected method must not authorize')

        service = helper.HelperService(
            connection=object(),
            core=make_helper_core(tmp_path), authorize=explosive)
        invocation = FakeInvocation()
        service._on_method_call(
            service.connection, ':1.7', '/p', 'i', 'ManageService',
            SimpleNamespace(unpack=lambda: ('xx-wm', 'start')), invocation)
        assert invocation.error is not None
        assert invocation.error[0] == helper.ERROR_INVALID_ARGS


# ------------------------------------------------------ helper_client shapes

import helper_client  # noqa: E402


def _capture_client(monkeypatch):
    calls = []

    def fake_call(method, params, on_done):
        calls.append((method, params))
        on_done(True, None)
    monkeypatch.setattr(helper_client, '_call_helper', fake_call)
    return calls


class TestHelperClientShapes:
    def test_manage_service_params(self, monkeypatch):
        calls = _capture_client(monkeypatch)
        done = []
        helper_client.manage_service(
            'bluetooth', 'restart', lambda ok, res: done.append((ok, res)))
        assert done == [(True, None)]
        (method, params), = calls
        assert method == 'ManageService'
        assert params.get_type_string() == '(ss)'
        assert params.unpack() == ('bluetooth', 'restart')

    def test_set_rfkill_params(self, monkeypatch):
        calls = _capture_client(monkeypatch)
        helper_client.set_rfkill('wifi', True, lambda *_a: None)
        (method, params), = calls
        assert method == 'SetRfkill'
        assert params.get_type_string() == '(sb)'
        assert params.unpack() == ('wifi', True)

    def test_preset_params(self, monkeypatch):
        calls = _capture_client(monkeypatch)
        helper_client.set_inbound_preset('ssh', False, lambda *_a: None)
        (method, params), = calls
        assert method == 'SetInboundPreset'
        assert params.unpack() == ('ssh', False)

    def test_firewall_enabled_params(self, monkeypatch):
        calls = _capture_client(monkeypatch)
        helper_client.set_firewall_enabled(True, lambda *_a: None)
        (method, params), = calls
        assert method == 'SetFirewallEnabled'
        assert params.get_type_string() == '(b)'

    def test_firewall_state_dict(self, monkeypatch):
        state = {'backend': 'none', 'enabled': False, 'allowed': {},
                 'foreign_drop': False}

        def fake_call(method, params, on_done):
            assert method == 'GetFirewallState'
            on_done(True, state)
        monkeypatch.setattr(helper_client, '_call_helper', fake_call)
        done = []
        helper_client.get_firewall_state(
            lambda ok, res: done.append((ok, res)))
        assert done == [(True, state)]


# ------------------------------------------------------------- Widget flows


class _FakeWidget:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.children = []
        self.classes = []
        self.props = {}
        self.handlers = {}

    def __getattr__(self, name):
        # Any other setter the settings row builders call is recorded.
        if name.startswith('set_'):
            return lambda *args: self.props.__setitem__(
                name[4:], args[0] if len(args) == 1 else args)
        raise AttributeError(name)

    @classmethod
    def new(cls, *args, **kwargs):
        return cls(*args, **kwargs)

    @classmethod
    def new_with_label(cls, text):
        return cls(label=text)

    def append(self, child):
        self.children.append(child)
        try:
            child._parent = self
        except AttributeError:
            pass

    def remove(self, child):
        if child in self.children:
            self.children.remove(child)

    def get_first_child(self):
        return self.children[0] if self.children else None

    def get_next_sibling(self):
        parent = getattr(self, '_parent', None)
        if parent is None:
            return None
        siblings = parent.children
        try:
            index = siblings.index(self)
        except ValueError:
            return None
        return siblings[index + 1] if index + 1 < len(siblings) else None

    def set_child(self, child):
        self.child = child

    def get_child(self):
        return getattr(self, 'child', None) or _FakeWidget()

    def add_css_class(self, name):
        self.classes.append(name)

    def remove_css_class(self, name):
        if name in self.classes:
            self.classes.remove(name)

    def connect(self, signal, callback, *args):
        self.handlers[signal] = (callback, args)
        return 1

    def set_hexpand(self, value):
        self.props['hexpand'] = value

    def set_vexpand(self, value):
        self.props['vexpand'] = value

    def set_xalign(self, value):
        self.props['xalign'] = value

    def set_halign(self, value):
        self.props['halign'] = value

    def set_valign(self, value):
        self.props['valign'] = value

    def set_can_focus(self, value):
        self.props['can_focus'] = value

    def set_size_request(self, width, height):
        self.props['size_request'] = (width, height)

    def set_margin_top(self, value):
        self.props['margin_top'] = value

    def set_margin_bottom(self, value):
        self.props['margin_bottom'] = value

    def set_margin_start(self, value):
        self.props['margin_start'] = value

    def set_margin_end(self, value):
        self.props['margin_end'] = value

    def set_policy(self, horizontal, vertical):
        self.props['policy'] = (horizontal, vertical)

    def set_wrap(self, value):
        self.props['wrap'] = value

    def set_wrap_mode(self, value):
        self.props['wrap_mode'] = value

    def set_max_width_chars(self, value):
        self.props['max_width_chars'] = value

    def set_ellipsize(self, value):
        self.props['ellipsize'] = value

    def set_text(self, value):
        self.props['text'] = value
        if isinstance(self, _FakeLabel):
            self.props['label'] = value

    def get_text(self):
        return self.props.get('text', self.kwargs.get('label', ''))

    def set_label(self, value):
        self.props['label'] = value

    def set_visibility(self, value):
        self.props['visibility'] = value

    def set_visible(self, value):
        self.props['visible'] = bool(value)

    def set_width_chars(self, value):
        self.props['width_chars'] = value

    def set_sensitive(self, value):
        self.props['sensitive'] = bool(value)

    def set_active(self, value):
        self.props['active'] = bool(value)

    def get_active(self):
        return self.props.get('active', False)

    def set_input_purpose(self, value):
        self.props['input_purpose'] = value

    def set_max_width_chars_entry(self, value):
        self.props['max_width_chars'] = value

    def get_allocation(self):
        return self.props.get('allocation', SimpleNamespace(x=0, y=0))

    def get_vadjustment(self):
        return self.props['vadjustment']


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
    def require_version(namespace, version):
        if namespace == 'Gtk4LayerShell':
            raise ValueError('no layer shell')
        return None

    gi = types.ModuleType('gi')
    gi.require_version = require_version
    repo = types.ModuleType('gi.repository')
    gi.repository = repo

    def make(name):
        return types.ModuleType('gi.repository.%s' % name)

    adw, gdk, glib, gtk, pango, gio = (
        make('Adw'), make('Gdk'), make('GLib'), make('Gtk'),
        make('Pango'), make('Gio'),
    )
    adw.ApplicationWindow = type('ApplicationWindow', (), {})

    class _FakeImage(_FakeWidget):
        @classmethod
        def new_from_icon_name(cls, name):
            image = cls()
            image.props['icon_name'] = name
            return image

        def set_pixel_size(self, px):
            self.props['pixel_size'] = px

    gtk.Box = _FakeBox
    gtk.Label = _FakeLabel
    gtk.Button = _FakeButton
    gtk.Entry = _FakeWidget
    gtk.Image = _FakeImage
    gtk.ScrolledWindow = _FakeWidget
    gtk.Switch = _FakeSwitch
    gtk.Align = SimpleNamespace(START=0, END=1, CENTER=2, FILL=3)
    gtk.Orientation = SimpleNamespace(HORIZONTAL=0, VERTICAL=1)
    gtk.PolicyType = SimpleNamespace(NEVER=0, AUTOMATIC=1)
    gtk.InputPurpose = SimpleNamespace(NUMBER=0, PASSWORD=1)
    pango.WrapMode = SimpleNamespace(WORD=0, WORD_CHAR=1, CHAR=2)
    pango.EllipsizeMode = SimpleNamespace(NONE=0, START=1, MIDDLE=2, END=3)
    repo.Adw, repo.Gdk, repo.GLib, repo.Gtk, repo.Pango, repo.Gio = (
        adw, gdk, glib, gtk, pango, gio)
    glib.idle_add = lambda callback, *args: (callback(*args), 0)[1]
    glib.timeout_add = lambda _ms, callback, *args: (callback(*args), 0)[1]
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
    statuses = []
    menus = []
    entries = []
    win = SimpleNamespace(
        config=SimpleNamespace(charge_limit=None,
                               label_for=lambda app_id, name: name),
        app_index=SimpleNamespace(entries=[]),
        stack=SimpleNamespace(
            visible=None,
            set_visible_child_name=lambda name: setattr(
                win.stack, 'visible', name)),
        item_actions=SimpleNamespace(
            _show_action_menu=lambda anchor, title, choices: menus.append(
                (title, [(label, cb) for label, cb in choices])),
            _show_entry_dialog=lambda *args, **kwargs: entries.append(
                (args, kwargs))),
        _run_bg=lambda work, on_done: _sync_bg(work, on_done),
        _show_status=statuses.append,
        _show_sources=lambda sources: None,
        present=lambda: None,
        gesture_config=SimpleNamespace(get=lambda key: 'none'),
        _gesture_binding_labels={},
        _system_gesture_labels={},
        _statuses=statuses,
        _menus=menus,
        _entries=entries,
    )
    for key, value in overrides.items():
        setattr(win, key, value)
    return win


def _sync_bg(work, on_done):
    try:
        result = work()
    except Exception:
        result = None
    on_done(result)


def _walk(widget):
    yield widget
    if isinstance(widget, _FakeWidget):
        for child in widget.children:
            yield from _walk(child)
        direct = getattr(widget, 'child', None)
        if isinstance(direct, _FakeWidget):
            yield from _walk(direct)


def _texts(box):
    found = []
    for node in _walk(box):
        if isinstance(node, _FakeLabel) and node.kwargs.get('label'):
            found.append(node.kwargs.get('label'))
        if isinstance(node, _FakeButton) and node.kwargs.get('label'):
            found.append(node.kwargs.get('label'))
    return found


class TestWifiSelectedMenu:
    def _win(self, fake_gi):
        win = _bare_win()
        win._settings_page = settings_page.SettingsPage(win)
        return win

    def test_connected_row_offers_menu(self, fake_gi):
        win = self._win(fake_gi)
        net = SimpleNamespace(ssid='HomeNet', security='WPA2', in_use=True)
        settings_page.SettingsPage(win)._on_wifi_selected(net, object())
        assert len(win._menus) == 1
        title, choices = win._menus[0]
        assert 'HomeNet' in title
        assert [label for label, _cb in choices] == [
            'Disconnect', 'Forget', 'Details']

    def test_disconnect_runs_and_refreshes(self, fake_gi, monkeypatch):
        win = self._win(fake_gi)
        called = []
        monkeypatch.setattr(
            system_settings, 'disconnect_wifi',
            lambda ssid, run=None: (called.append(ssid), (True, 'ok'))[1])
        refreshed = []
        win._refresh_wifi = lambda: refreshed.append(1)
        net = SimpleNamespace(ssid='HomeNet', security='WPA2', in_use=True)
        page = settings_page.SettingsPage(win)
        page._on_wifi_selected(net, object())
        _title, choices = win._menus[0]
        dict(choices)['Disconnect']()
        assert called == ['HomeNet']
        assert refreshed == [1]

    def test_forget_runs_without_second_popup(self, fake_gi, monkeypatch):
        # Owner: no confirmation popups for direct actions. Forget in the
        # network's menu is the decision (Android forgets at once too).
        win = self._win(fake_gi)
        forgot = []
        monkeypatch.setattr(
            system_settings, 'forget_wifi',
            lambda ssid, run=None: (forgot.append(ssid), (True, 'ok'))[1])
        refreshed = []
        win._refresh_wifi = lambda: refreshed.append(1)
        net = SimpleNamespace(ssid='HomeNet', security='WPA2', in_use=True)
        page = settings_page.SettingsPage(win)
        page._on_wifi_selected(net, object())
        _title, choices = win._menus[0]
        dict(choices)['Forget']()
        assert len(win._menus) == 1
        assert forgot == ['HomeNet']
        assert refreshed == [1]

    def test_details_show_fields(self, fake_gi, monkeypatch):
        win = self._win(fake_gi)
        monkeypatch.setattr(
            system_settings, 'wifi_details', lambda ssid, run=None: {'ip': '1.2.3.4',
                                                             'gateway': '1.2.3.1',
                                                             'dns': '9.9.9.9',
                                                             'mac': 'AA'})
        monkeypatch.setattr(
            system_settings, 'wifi_details_text',
            lambda ssid, details: 'IP 1.2.3.4\ngateway 1.2.3.1')
        net = SimpleNamespace(ssid='HomeNet', security='WPA2', in_use=True)
        page = settings_page.SettingsPage(win)
        page._on_wifi_selected(net, object())
        _title, choices = win._menus[0]
        dict(choices)['Details']()
        assert len(win._menus) == 2
        assert '1.2.3.4' in win._menus[1][0]


class TestBtSelectedMenu:
    def _page(self, fake_gi):
        win = _bare_win()
        return settings_page.SettingsPage(win), win

    def test_unpaired_pairs(self, fake_gi, monkeypatch):
        page, win = self._page(fake_gi)
        monkeypatch.setattr(
            system_settings, 'pair_bt_device',
            lambda address: (True, 'Paired and connected'))
        win._refresh_bluetooth = lambda scan=False: None
        dev = SimpleNamespace(name='Pods', address='AA:BB', paired=False,
                              connected=False)
        page._on_bt_selected(dev)
        assert win._statuses and 'Pairing' in win._statuses[0]

    def test_paired_row_offers_menu(self, fake_gi):
        page, win = self._page(fake_gi)
        dev = SimpleNamespace(name='Pods', address='AA:BB', paired=True,
                              connected=True)
        page._on_bt_selected(dev)
        assert len(win._menus) == 1
        _title, choices = win._menus[0]
        assert [label for label, _cb in choices] == [
            'Disconnect', 'Forget', 'Rename']

    def test_disconnected_offers_connect(self, fake_gi):
        page, win = self._page(fake_gi)
        dev = SimpleNamespace(name='Pods', address='AA:BB', paired=True,
                              connected=False)
        page._on_bt_selected(dev)
        _title, choices = win._menus[0]
        assert [label for label, _cb in choices] == [
            'Connect', 'Forget', 'Rename']

    def test_forget_runs_without_second_popup(self, fake_gi, monkeypatch):
        page, win = self._page(fake_gi)
        forgot = []
        monkeypatch.setattr(
            system_settings, 'forget_bt_device',
            lambda address: (forgot.append(address), (True, 'Forgotten'))[1])
        win._refresh_bluetooth = lambda scan=False: None
        dev = SimpleNamespace(name='Pods', address='AA:BB', paired=True,
                              connected=True)
        page._on_bt_selected(dev)
        _title, choices = win._menus[0]
        dict(choices)['Forget']()
        assert len(win._menus) == 1
        assert forgot == ['AA:BB']

    def test_rename_opens_entry(self, fake_gi, monkeypatch):
        page, win = self._page(fake_gi)
        monkeypatch.setattr(
            system_settings, 'rename_bt_device',
            lambda address, alias: (True, 'Renamed'))
        win._refresh_bluetooth = lambda scan=False: None
        dev = SimpleNamespace(name='Pods', address='AA:BB', paired=True,
                              connected=True)
        page._on_bt_selected(dev)
        _title, choices = win._menus[0]
        dict(choices)['Rename']()
        assert len(win._entries) == 1
        (args, _kwargs) = win._entries[0]
        assert args[1] == 'Rename'


class TestMicPicker:
    def test_sources_render_with_default(self, fake_gi):
        from gi.repository import Gtk
        win = _bare_win()
        page = settings_page.SettingsPage(win)
        win._source_list_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL)
        sources = [SimpleNamespace(name='usb', description='USB Mic',
                                   is_default=False),
                   SimpleNamespace(name='pci', description='Internal Mic',
                                   is_default=True)]
        page._show_sources(sources)
        texts = _texts(win._source_list_box)
        assert 'USB Mic' in texts
        assert 'Default input' in texts

    def test_tap_sets_default(self, fake_gi, monkeypatch):
        from gi.repository import Gtk
        win = _bare_win()
        page = settings_page.SettingsPage(win)
        win._source_list_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL)
        picked = []
        monkeypatch.setattr(
            system_settings, 'set_default_source',
            lambda name, run=None: picked.append(name) or True)
        monkeypatch.setattr(
            system_settings, 'list_audio_sources', lambda run=None: [])
        source = SimpleNamespace(name='usb', description='USB Mic',
                                 is_default=False)
        page._on_source_selected(source)
        assert picked == ['usb']


class TestArmConfirm:
    def test_matches_power_menu_machine(self):
        import power_menu
        for cls in (services.ConfirmState, power_menu.ConfirmState):
            state = cls()
            assert state.tap('stop:bluetooth') is False
            assert state.tap('stop:bluetooth') is True
            assert state.tap('stop:bluetooth') is False
            state.tap('stop:bluetooth')
            state.reset()
            assert state.tap('stop:bluetooth') is False


class TestServiceSectionUI:
    def test_build_title_first(self, fake_gi):
        win = _bare_win()
        built = services.ServicesSection().build(win)
        assert 'xx-set-caption' in built[0].classes

    def test_stop_arms_then_confirms(self, fake_gi, monkeypatch):
        import helper_client as client
        win = _bare_win()
        section = services.ServicesSection()
        section.build(win)
        monkeypatch.setattr(section, 'refresh', lambda win: None)
        calls = []
        monkeypatch.setattr(
            client, 'manage_service',
            lambda name, action, on_done: (
                calls.append((name, action)), on_done(True, None))[1])
        key = section._stop_key('bluetooth')
        assert section._on_stop(win, key) == 'armed'
        assert calls == []
        assert section._on_stop(win, key) == 'confirmed'
        assert calls == [('bluetooth', 'stop')]

    def test_failed_units_read_only(self, fake_gi):
        win = _bare_win()
        section = services.ServicesSection()
        section.build(win)
        section._show_failed(win, ['sshd.service'])
        assert 'sshd.service' in _texts(section._failed_box)


class TestFirewallSectionUI:
    def test_build_title_first(self, fake_gi):
        win = _bare_win()
        built = firewall.FirewallSection().build(win)
        assert 'xx-set-caption' in built[0].classes

    def test_toggle_calls_helper_without_popup(self, fake_gi, monkeypatch):
        import helper_client as client
        win = _bare_win()
        section = firewall.FirewallSection()
        section.build(win)
        calls = []
        monkeypatch.setattr(
            client, 'set_firewall_enabled',
            lambda enabled, on_done: (
                calls.append(enabled), on_done(True, None))[1])
        monkeypatch.setattr(
            client, 'get_firewall_state',
            lambda on_done: on_done(True, {
                'backend': 'nftables', 'enabled': True,
                'allowed': {}, 'foreign_drop': False}))
        reapplies = []
        monkeypatch.setattr(
            firewall, 'reapply',
            lambda win, on_done=None: reapplies.append(win))
        section._on_toggle(win, True)
        assert calls == [True]
        assert reapplies == [win]
        assert win._menus == []

    def test_none_backend_honest(self, fake_gi):
        win = _bare_win()
        section = firewall.FirewallSection()
        section.build(win)
        section._show_state(win, {'backend': 'none', 'enabled': False,
                                  'allowed': {}, 'foreign_drop': False})
        assert 'no inbound firewall' in section._status_text().lower()


# ------------------------------------------------- registry / static guards

NEW_FILES = ('settings_services.py', 'settings_firewall.py')


class TestSmRegistry:
    def test_keys_registered_gestures_last(self):
        keys = [key for key, _cls in settings_page.SETTINGS_SECTIONS]
        for key in ('services', 'firewall'):
            assert key in keys
        assert keys[-1] == 'gestures'

    def test_sections_shape(self):
        for key, cls in settings_page.SETTINGS_SECTIONS:
            if key in ('services', 'firewall'):
                assert issubclass(cls, settings_page._Section), key
                assert cls().key == key

    def test_deep_link_keys(self):
        for key in ('services', 'firewall'):
            assert key in settings_page._SETTINGS_SECTION_KEYS

    def test_meson_lists_new_files(self):
        meson = (SRC.parent / 'meson.build').read_text(encoding='utf-8')
        for name in NEW_FILES:
            assert "'src/%s'" % name in meson
            assert (SRC / name).is_file()

    def test_no_blocking_io_or_focus_grabs(self):
        for name in NEW_FILES:
            text = (SRC / name).read_text(encoding='utf-8')
            assert 'call_sync' not in text, name
            assert 'subprocess' not in text, name
            assert 'time.sleep' not in text, name
            assert 'grab_focus' not in text, name
            assert 'Gtk.Entry(' not in text, name

    def test_only_known_signals(self, fake_gi):
        win = _bare_win()
        for key, cls in settings_page.SETTINGS_SECTIONS:
            if key in ('services', 'firewall'):
                built = cls().build(win)
                assert built and built[0] is not None


# --------------------------------------- unified policy_for coverage (rebase)

POLICY_XML = (Path(__file__).parent.parent / 'launcher' / 'data' /
              'polkit-1' / 'actions' /
              'io.piercingxx.XXWM.Helper.policy')
DOCS_MD = (Path(__file__).parent.parent / 'docs' / 'helper.md')


def _policy_defaults():
    import xml.etree.ElementTree as xml
    root = xml.parse(str(POLICY_XML)).getroot()
    defaults = {}
    for action in root.iter('action'):
        action_id = action.get('id')
        allow_active = action.find('./defaults/allow_active').text.strip()
        assert action_id not in defaults, action_id
        defaults[action_id] = allow_active
    return defaults


def _every_branch():
    """(method, args) for every helper method and argument branch."""
    cases = [
        ('SetChargeLimit', (80,)),
        ('SetAppNetwork', ('/sys/fs/cgroup/user.slice/user-1000.slice/x',
                           True)),
        ('FreezeCgroup', ('/sys/fs/cgroup/user.slice/user-1000.slice/x',
                          False)),
        ('CleanSystem', (['apk-cache'],)),
        ('ReadLog', ('dmesg',)),
        ('RefreshPackageIndex', ()),
        ('UpgradeSystem', ()),
        ('RemovePackage', ('vim',)),
        ('SetRfkill', ('wifi', True)),
        ('SetRfkill', ('bluetooth', False)),
    ]
    for name in helper.SERVICE_ALLOW:
        for action in helper.SERVICE_ACTIONS:
            cases.append(('ManageService', (name, action)))
    for name in helper.INBOUND_PRESETS:
        cases.append(('SetInboundPreset', (name, True)))
        cases.append(('SetInboundPreset', (name, False)))
    cases.append(('SetFirewallEnabled', (True,)))
    cases.append(('SetFirewallEnabled', (False,)))
    return cases


class TestUnifiedPolicy:
    def test_every_branch_maps_to_a_shipped_action(self):
        defaults = _policy_defaults()
        for method, args in _every_branch():
            policy = helper.policy_for(method, args)
            assert policy is not None, (method, args)
            action, interactive = policy
            assert action in defaults, (method, args, action)
            assert interactive is (defaults[action] == 'auth_admin_keep'), (
                method, args, action, interactive)

    def test_ssh_remote_branches_are_interactive(self):
        for args in (('ssh', 'start'), ('ssh', 'enable')):
            assert helper.policy_for('ManageService', args) == (
                helper.ACTION_SERVICE_REMOTE, True)

    def test_no_auth_probes_and_unknown_methods_refused(self):
        for method, args in (('GetCapabilities', ()),
                             ('GetFirewallState', ()),
                             ('NoSuchMethod', ()),
                             ('ManageService', ('xx-wm', 'start')),
                             ('ManageService', ('ssh', 'mask')),
                             ('ManageService', ('ssh',)),
                             ('SetInboundPreset', ('http-alt', True)),
                             ('SetInboundPreset', ('ssh',)),
                             ('SetFirewallEnabled', ())):
            assert helper.policy_for(method, args) is None, (method, args)

    def test_static_methods_ignore_argument_shapes(self):
        assert helper.policy_for('SetChargeLimit', ('bogus',)) == (
            helper.ACTION_CHARGE, False)

    def test_policy_lists_every_action_exactly_once(self):
        defaults = _policy_defaults()
        assert sorted(defaults) == sorted([
            helper.ACTION_CHARGE, helper.ACTION_NETWORK, helper.ACTION_FREEZE,
            helper.ACTION_CLEAN, helper.ACTION_READLOG, helper.ACTION_REFRESH,
            helper.ACTION_UPGRADE, helper.ACTION_REMOVE,
            helper.ACTION_SERVICE, helper.ACTION_SERVICE_REMOTE,
            helper.ACTION_RFKILL, helper.ACTION_FW_OPEN,
            helper.ACTION_FW_RESTRICT, helper.ACTION_POWER,
        ])

    def test_docs_list_every_method_and_action(self):
        text = DOCS_MD.read_text(encoding='utf-8')
        for method in ('GetCapabilities', 'SetChargeLimit', 'SetAppNetwork',
                       'FreezeCgroup', 'CleanSystem', 'ReadLog',
                       'RefreshPackageIndex', 'UpgradeSystem',
                       'RemovePackage', 'ManageService', 'SetRfkill',
                       'SetInboundPreset', 'SetFirewallEnabled',
                       'GetFirewallState'):
            assert text.count('`%s' % method) >= 1, method
        for action in _policy_defaults():
            short = action.split('.')[-1]
            assert text.count(short) >= 1, action
