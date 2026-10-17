"""W1 system-integration tests: subprocess/D-Bus fakes for every fixed bug.

Covers status collection (async, no-rescan wifi, D-Bus modem, battery
preference), secured WiFi profiles (in test_system_settings.py), Bluetooth
(adapter resolution, agent, Trusted, async discovery), login1 power actions,
the brightness backend chain, and update freshness/terminals.
"""
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import als_brightness
import system_settings
import system_status
import update_checker

SRC = Path(__file__).parent.parent / 'launcher' / 'src'


# ---------------------------------------------------------------------------
# 1. system_status


def _write_supply(root: Path, name: str, files: dict[str, str]) -> None:
    supply = root / name
    supply.mkdir(parents=True, exist_ok=True)
    for filename, content in files.items():
        (supply / filename).write_text(content)


class TestBatteryPreference:
    def test_prefers_system_battery_over_device_scope(self, tmp_path):
        _write_supply(tmp_path, '00-headset', {
            'type': 'Battery\n', 'scope': 'Device\n',
            'capacity': '10\n', 'status': 'Discharging\n'})
        _write_supply(tmp_path, 'BAT0', {
            'type': 'Battery\n', 'scope': 'System\n',
            'capacity': '87\n', 'status': 'Discharging\n'})
        assert system_status.battery_pct(tmp_path) == 87

    def test_missing_scope_file_counts_as_eligible(self, tmp_path):
        _write_supply(tmp_path, 'BAT0', {
            'type': 'Battery\n', 'capacity': '64\n',
            'status': 'Discharging\n'})
        assert system_status.battery_pct(tmp_path) == 64

    def test_device_scope_excluded_from_charging(self, tmp_path):
        _write_supply(tmp_path, '00-headset', {
            'type': 'Battery\n', 'scope': 'Device\n',
            'capacity': '10\n', 'status': 'Charging\n'})
        _write_supply(tmp_path, 'BAT0', {
            'type': 'Battery\n', 'scope': 'System\n',
            'capacity': '87\n', 'status': 'Discharging\n'})
        assert system_status.battery_charging(tmp_path) is False

    def test_no_supplies_gives_none(self, tmp_path):
        assert system_status.battery_pct(tmp_path) is None


class TestWifiNoRescan:
    def test_uses_cached_scan_without_iwgetid(self, monkeypatch):
        seen: list[list[str]] = []

        def fake_check_output(cmd, **kwargs):
            seen.append(list(cmd))
            assert cmd[0] == 'nmcli'
            return 'yes:HomeNet\nno:CoffeeShop\n'

        monkeypatch.setattr(subprocess, 'check_output', fake_check_output)
        assert system_status.wifi_ssid() == 'HomeNet'
        assert len(seen) == 1
        assert '--rescan' in seen[0] and 'no' in seen[0]
        assert 'iwgetid' not in SRC.joinpath('system_status.py').read_text()

    def test_reports_none_when_disconnected(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, 'check_output',
            lambda cmd, **kwargs: 'no:CoffeeShop\n')
        assert system_status.wifi_ssid() is None


class TestModemByPath:
    def test_mmcli_called_with_dbus_path(self, monkeypatch):
        seen: list[list[str]] = []
        path = '/org/freedesktop/ModemManager1/Modem/3'
        monkeypatch.setattr(system_status, 'modem_paths', lambda: [path])

        def fake_check_output(cmd, **kwargs):
            seen.append(list(cmd))
            return 'modem.generic.signal-quality.value : 75\n'

        monkeypatch.setattr(subprocess, 'check_output', fake_check_output)
        assert system_status.mobile_signal() == '▂▄▆'
        assert seen == [['mmcli', '-m', path, '--output-keyvalue']]

    def test_no_modem_gives_none(self, monkeypatch):
        monkeypatch.setattr(system_status, 'modem_paths', lambda: [])
        assert system_status.mobile_signal() is None


class TestStatusAsync:
    def test_callback_fires_on_idle_add_with_line(self, monkeypatch):
        from gi.repository import GLib
        delivered: list = []
        done = threading.Event()

        monkeypatch.setattr(system_status, 'status_line', lambda: 'HomeNet  87%')
        monkeypatch.setattr(
            GLib, 'idle_add', lambda cb, *args: (delivered.append(cb()), False)[1])

        system_status.status_line_async(lambda line: (delivered.append(line), done.set()))
        assert done.wait(5)
        assert 'HomeNet  87%' in delivered

    def test_failure_still_delivers_empty_string(self, monkeypatch):
        from gi.repository import GLib
        got: list = []
        done = threading.Event()

        def boom():
            raise RuntimeError('nmcli wedged')

        monkeypatch.setattr(system_status, 'status_line', boom)
        monkeypatch.setattr(
            GLib, 'idle_add', lambda cb, *args: (cb(), False)[1])

        system_status.status_line_async(lambda line: (got.append(line), done.set()))
        assert done.wait(5)
        assert got == ['']


# ---------------------------------------------------------------------------
# 3. Bluetooth

class _FakeCall:
    def __init__(self, result):
        self.result = result

    def unpack(self):
        return (self.result,)


class _FakeLeaf:
    def __init__(self, value):
        self.value = value

    def unpack(self):
        return self.value


class FakeBluez:
    ADAPTER = '/org/bluez/hci1'
    DEVICE = '/org/bluez/hci1/dev_AA_BB_CC_DD_EE_FF'
    ADDRESS = 'AA:BB:CC:DD:EE:FF'

    def __init__(self, powered: bool = True, pair_error: str | None = None):
        self.powered = powered
        self.pair_error = pair_error
        self.calls: list[tuple] = []
        self.async_calls: list[tuple] = []
        self.registered: list = []
        self.unregistered: list = []
        self.props = {
            (self.ADAPTER, 'Powered'): powered,
            (self.DEVICE, 'Trusted'): False,
        }

    @property
    def objects(self):
        return {
            self.ADAPTER: {'org.bluez.Adapter1': {}},
            self.DEVICE: {'org.bluez.Device1': {'Address': self.ADDRESS}},
        }

    def call(self, service, path, iface, method, params, *rest):
        self.async_calls.append((service, path, iface, method, params))
        return None

    def call_sync(self, service, path, iface, method, params, *rest):
        self.calls.append((service, path, iface, method, params))
        if method == 'GetManagedObjects':
            return _FakeCall(self.objects)
        if method == 'Get':
            _iface, prop = params.unpack()
            return _FakeCall(_FakeLeaf(self.props.get((path, prop), False)))
        if method == 'Set':
            _iface, prop, value = params.unpack()
            self.props[(path, prop)] = (
                value.unpack() if hasattr(value, 'unpack') else value)
            return _FakeCall(None)
        if method == 'Pair' and self.pair_error is not None:
            raise FakeDbusError(self.pair_error)
        return _FakeCall(None)

    def register_object(self, path, _iface, _handler):
        self.registered.append(path)
        return 7

    def unregister_object(self, reg_id):
        self.unregistered.append(reg_id)

    def methods(self):
        return [method for _s, _p, _i, method, _a in self.calls]


class FakeDbusError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def _fake_bluez(monkeypatch, bus: FakeBluez):
    monkeypatch.setattr(system_settings, '_bluez_bus', lambda: bus)
    return bus


class TestAdapterResolution:
    def test_pair_uses_object_manager_adapter(self, monkeypatch):
        bus = _fake_bluez(monkeypatch, FakeBluez())
        ok, msg = system_settings.pair_bt_device(FakeBluez.ADDRESS)
        assert (ok, msg) == (True, 'Paired and connected')
        targets = {path for _s, path, _i, _m, _a in bus.calls}
        assert FakeBluez.ADAPTER in targets
        assert not any('hci0' in str(call) for call in bus.calls)

    def test_no_adapter_is_a_clear_error(self, monkeypatch):
        _fake_bluez(monkeypatch, FakeBluez())
        monkeypatch.setattr(system_settings, '_adapter_path', lambda bus=None: None)
        ok, msg = system_settings.pair_bt_device(FakeBluez.ADDRESS)
        assert not ok
        assert msg == 'No Bluetooth adapter found'

    def test_powers_adapter_on_when_off(self, monkeypatch):
        bus = _fake_bluez(monkeypatch, FakeBluez(powered=False))
        system_settings.pair_bt_device(FakeBluez.ADDRESS)
        sets = [call for call in bus.calls if call[3] == 'Set']
        powered_sets = [call for call in sets
                        if call[4].unpack()[1] == 'Powered']
        assert powered_sets
        assert bus.props[(FakeBluez.ADAPTER, 'Powered')] is True


class TestPairingAgent:
    def test_agent_registered_during_pair_and_released(self, monkeypatch):
        bus = _fake_bluez(monkeypatch, FakeBluez())
        system_settings.pair_bt_device(FakeBluez.ADDRESS)
        assert bus.registered == [system_settings._AGENT_PATH]
        reg_calls = [call for call in bus.calls if call[3] == 'RegisterAgent']
        assert len(reg_calls) == 1
        assert reg_calls[0][4].unpack() == (
            system_settings._AGENT_PATH, 'NoInputNoOutput')
        assert bus.unregistered == [7]
        assert 'UnregisterAgent' in bus.methods()

    def test_trusted_set_between_pair_and_connect(self, monkeypatch):
        bus = _fake_bluez(monkeypatch, FakeBluez())
        system_settings.pair_bt_device(FakeBluez.ADDRESS)
        order = bus.methods()
        assert order.index('Pair') < order.index('Set') < order.index('Connect')
        trust_sets = [call for call in bus.calls
                      if call[3] == 'Set' and call[4].unpack()[1] == 'Trusted']
        assert len(trust_sets) == 1
        assert trust_sets[0][4].unpack()[2] is True

    def test_already_exists_falls_back_to_connect(self, monkeypatch):
        _fake_bluez(monkeypatch, FakeBluez(pair_error='AlreadyExists'))
        ok, msg = system_settings.connect_bt_device(FakeBluez.ADDRESS)
        assert (ok, msg) == (True, 'Connected')


class TestDiscoveryAsync:
    def test_discovery_runs_off_thread_and_reports(self, monkeypatch):
        from gi.repository import GLib
        bus = _fake_bluez(monkeypatch, FakeBluez())
        timeouts: list = []
        done = threading.Event()
        got: list = []
        monkeypatch.setattr(
            GLib, 'timeout_add_seconds',
            lambda secs, cb, *args: timeouts.append((secs, cb, args)) or 1)
        monkeypatch.setattr(
            GLib, 'idle_add', lambda cb, *args: (cb(), False)[1])

        started = system_settings.start_bt_discovery(
            12, on_done=lambda ok: (got.append(ok), done.set()))
        assert started is True
        assert done.wait(5)
        assert got == [True]
        assert 'StartDiscovery' in bus.methods()
        assert timeouts and timeouts[0][0] == 12
        assert timeouts[0][2] == (FakeBluez.ADAPTER,)

    def test_no_adapter_reports_false(self, monkeypatch):
        from gi.repository import GLib
        _fake_bluez(monkeypatch, FakeBluez())
        monkeypatch.setattr(system_settings, '_adapter_path', lambda bus=None: None)
        monkeypatch.setattr(
            GLib, 'idle_add', lambda cb, *args: (cb(), False)[1])
        done = threading.Event()
        got: list = []
        system_settings.start_bt_discovery(
            12, on_done=lambda ok: (got.append(ok), done.set()))
        assert done.wait(5)
        assert got == [False]


class TestStopDiscoveryAsync:
    def test_stop_sends_async_without_sync_call(self, monkeypatch):
        bus = _fake_bluez(monkeypatch, FakeBluez())
        assert system_settings._stop_bt_discovery(FakeBluez.ADAPTER) is False
        assert len(bus.async_calls) == 1
        assert bus.async_calls[0][:4] == (
            'org.bluez', FakeBluez.ADAPTER,
            system_settings._ADAPTER_IFACE, 'StopDiscovery')
        assert bus.calls == []

    def test_stop_without_adapter_is_noop(self, monkeypatch):
        bus = _fake_bluez(monkeypatch, FakeBluez())
        monkeypatch.setattr(system_settings, '_adapter_path',
                            lambda bus=None: None)
        assert system_settings._stop_bt_discovery(None) is False
        assert bus.async_calls == []


# ---------------------------------------------------------------------------
# 4. power_menu (login1, no systemctl)

class FakeLogin:
    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.calls: list[tuple] = []

    def call_sync(self, service, path, iface, method, params, *rest):
        self.calls.append((service, path, iface, method, params))
        if self.fail is not None:
            raise self.fail
        return None


def _power_mod():
    import power_menu
    return power_menu


class TestLoginPower:
    def test_suspend_reboot_poweroff_map_to_login1(self):
        mod = _power_mod()
        for action, method in (('suspend', 'Suspend'), ('reboot', 'Reboot'),
                               ('poweroff', 'PowerOff')):
            bus = FakeLogin()
            ok, msg = mod.power_action_sync(action, bus=bus)
            assert (ok, msg) == (True, '')
            (service, path, iface, called, params), = bus.calls
            assert service == 'org.freedesktop.login1'
            assert path == '/org/freedesktop/login1'
            assert iface == 'org.freedesktop.login1.Manager'
            assert called == method
            assert params.unpack() == (False,)

    def test_unknown_action_is_an_error(self):
        mod = _power_mod()
        ok, msg = mod.power_action_sync('hibernate', bus=FakeLogin())
        assert not ok
        assert 'hibernate' in msg

    def test_dbus_failure_surfaces_as_text(self):
        mod = _power_mod()
        bus = FakeLogin(fail=FakeDbusError('Interactive authentication required'))
        ok, msg = mod.power_action_sync('reboot', bus=bus)
        assert not ok
        assert msg == 'Interactive authentication required'

    def test_async_delivers_on_idle_add(self, monkeypatch):
        from gi.repository import GLib
        mod = _power_mod()
        monkeypatch.setattr(
            GLib, 'idle_add', lambda cb, *args: (cb(), False)[1])
        done = threading.Event()
        got: list = []
        mod.power_action_async(
            'suspend', bus=FakeLogin(),
            on_done=lambda ok, msg: (got.append((ok, msg)), done.set()))
        assert done.wait(5)
        assert got == [(True, '')]

    def test_no_systemctl_left(self):
        assert "['systemctl'" not in SRC.joinpath('power_menu.py').read_text()


# ---------------------------------------------------------------------------
# 5. brightness chain

class TestBrightnessChain:
    def test_brightnessctl_wins_and_short_circuits(self, monkeypatch):
        seen: list = []
        monkeypatch.setattr(
            als_brightness, '_brightnessctl_set',
            lambda value, device=None: seen.append((value, device)) or True)
        monkeypatch.setattr(
            als_brightness, 'login1_set_brightness',
            lambda *a, **k: seen.append('login1') or True)
        assert als_brightness.apply_backlight(
            120, device='intel_backlight') is True
        assert seen == [(120, 'intel_backlight')]

    def test_login1_before_sysfs_last_resort(self, monkeypatch):
        order: list = []
        monkeypatch.setattr(
            als_brightness, '_brightnessctl_set', lambda *a, **k: False)
        monkeypatch.setattr(
            als_brightness, 'login1_set_brightness',
            lambda *a, **k: order.append('login1') or False)
        monkeypatch.setattr(
            als_brightness, '_sysfs_set_brightness',
            lambda *a, **k: order.append('sysfs') or False)
        assert als_brightness.apply_backlight(
            120, device='intel_backlight') is False
        assert order == ['login1', 'sysfs']

    def test_total_failure_logged_once(self, monkeypatch, caplog):
        import logging
        monkeypatch.setattr(als_brightness, '_failure_logged', False)
        monkeypatch.setattr(
            als_brightness, '_brightnessctl_set', lambda *a, **k: False)
        monkeypatch.setattr(
            als_brightness, 'login1_set_brightness', lambda *a, **k: False)
        monkeypatch.setattr(
            als_brightness, '_sysfs_set_brightness', lambda *a, **k: False)
        with caplog.at_level(logging.WARNING, logger='piercing.brightness'):
            assert als_brightness.apply_backlight(50, device='x') is False
            assert als_brightness.apply_backlight(60, device='x') is False
        warnings = [rec for rec in caplog.records
                    if rec.name == 'piercing.brightness']
        assert len(warnings) == 1

    def test_login1_uses_session_set_brightness(self):
        bus = FakeLogin()
        assert als_brightness.login1_set_brightness(
            'intel_backlight', 120, bus=bus) is True
        (service, path, iface, method, params), = bus.calls
        assert service == 'org.freedesktop.login1'
        assert path == '/org/freedesktop/login1/session/auto'
        assert iface == 'org.freedesktop.login1.Session'
        assert method == 'SetBrightness'
        assert params.unpack() == ('backlight', 'intel_backlight', 120)

    def test_login1_failure_is_false(self):
        bus = FakeLogin(fail=FakeDbusError('no logind'))
        assert als_brightness.login1_set_brightness(
            'intel_backlight', 120, bus=bus) is False

    def test_als_apply_routes_through_chain(self, monkeypatch):
        seen: list = []
        als = als_brightness.ALSBrightness.__new__(als_brightness.ALSBrightness)
        als._max_brightness = 200
        als._backlight_path = None
        monkeypatch.setattr(
            als_brightness, 'apply_backlight',
            lambda value, **kwargs: seen.append((value, kwargs)) or True)
        als._apply(0.5)
        assert seen[0][0] == 100

    def test_slider_falls_back_past_brightnessctl(self, monkeypatch):
        import quick_actions
        monkeypatch.setattr(
            subprocess, 'Popen',
            lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError('nope')))
        seen: list = []
        monkeypatch.setattr(
            quick_actions, 'apply_backlight_pct',
            lambda pct: seen.append(pct) or True)
        quick_actions._set_brightness_pct(60)
        assert seen == [60]

    def test_slider_keeps_brightnessctl_argv(self, monkeypatch):
        import quick_actions
        calls: list = []
        monkeypatch.setattr(
            subprocess, 'Popen', lambda cmd, **k: calls.append(cmd))
        quick_actions._set_brightness_pct(72)
        assert calls == [['brightnessctl', 'set', '72%']]


# ---------------------------------------------------------------------------
# 6. update_checker

class TestTerminals:
    def test_gnome_console_first(self):
        names = [name for name, _argv in update_checker._TERMINALS]
        assert names[:2] == ['kgx', 'gnome-console']

    def test_kgx_preferred_when_present(self, monkeypatch, tmp_path):
        script = tmp_path / 'update.sh'
        script.write_text('#!/bin/sh\n')
        monkeypatch.setattr(
            update_checker.shutil, 'which',
            lambda binary: '/usr/bin/' + binary
            if binary in ('kgx', 'bash') else None)
        calls: list = []
        monkeypatch.setattr(
            update_checker.subprocess, 'Popen',
            lambda cmd, **k: calls.append(cmd))
        ok, detail = update_checker.run_update_script(str(script))
        assert (ok, detail) == (True, 'kgx')
        assert calls[0][:2] == ['kgx', '-e']


class TestUpdateFreshness:
    def _which(self, monkeypatch, present: set[str]):
        monkeypatch.setattr(
            update_checker.shutil, 'which',
            lambda binary: '/usr/bin/' + binary if binary in present else None)

    def test_fresh_index_counts(self, monkeypatch):
        self._which(monkeypatch, {'apt'})
        monkeypatch.setattr(update_checker, '_index_fresh', lambda binary: True)
        monkeypatch.setattr(
            update_checker.subprocess, 'run',
            lambda *a, **k: subprocess.CompletedProcess(
                a[0], 0, stdout='Listing...\nfoo/now 1.0\nbar/now 2.0\n',
                stderr=''))
        assert update_checker.count_updates() == 2

    def test_stale_index_is_unknown_not_zero(self, monkeypatch):
        self._which(monkeypatch, {'apt', 'apk', 'pacman', 'checkupdates'})
        monkeypatch.setattr(update_checker, '_index_fresh', lambda binary: False)
        ran: list = []
        monkeypatch.setattr(
            update_checker.subprocess, 'run',
            lambda *a, **k: ran.append(a[0]))
        assert update_checker.count_updates() is None
        assert ran == []

    def test_no_backend_is_unknown(self, monkeypatch):
        self._which(monkeypatch, set())
        assert update_checker.count_updates() is None

    def test_listing_failure_is_unknown(self, monkeypatch):
        self._which(monkeypatch, {'apt'})
        monkeypatch.setattr(update_checker, '_index_fresh', lambda binary: True)

        def boom(*args, **kwargs):
            raise OSError('dpkg locked')

        monkeypatch.setattr(update_checker.subprocess, 'run', boom)
        assert update_checker.count_updates() is None

    def test_index_freshness_from_mtimes(self, monkeypatch, tmp_path):
        import os
        import time
        monkeypatch.setattr(
            update_checker, '_INDEX_PATHS', {'t': [str(tmp_path)]})
        probe = tmp_path / 'pkgcache.bin'
        probe.write_text('x')
        assert update_checker._index_fresh('t') is True
        old = time.time() - 2 * update_checker._INDEX_MAX_AGE
        os.utime(tmp_path, (old, old))
        os.utime(probe, (old, old))
        assert update_checker._index_fresh('t') is False

    def test_missing_index_path_is_not_fresh(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            update_checker, '_INDEX_PATHS',
            {'t': [str(tmp_path / 'nope')]})
        assert update_checker._index_fresh('t') is False


class _FakeUpdateConfig:
    def __init__(self):
        self.update_snooze_until = 0
        self.update_last_check = 0
        self.saved: list = []

    def set_update_last_check(self, value):
        self.saved.append(value)


class TestCheckerUnknown:
    def test_unknown_finishes_through_report(self):
        config = _FakeUpdateConfig()
        reported: list = []
        checker = update_checker.UpdateChecker(config, lambda count: None)
        assert checker._finish_check(None, reported.append) is False
        assert reported == [None]

    def test_scheduled_unknown_notifies_nothing(self):
        config = _FakeUpdateConfig()
        fired: list = []
        checker = update_checker.UpdateChecker(config, fired.append)
        checker._report_scheduled(None)
        assert fired == []

    def test_scheduled_zero_notifies_nothing(self):
        config = _FakeUpdateConfig()
        fired: list = []
        checker = update_checker.UpdateChecker(config, fired.append)
        checker._report_scheduled(0)
        assert fired == []

    def test_scheduled_count_notifies(self):
        config = _FakeUpdateConfig()
        fired: list = []
        checker = update_checker.UpdateChecker(config, fired.append)
        checker._report_scheduled(3)
        assert fired == [3]


# ---------------------------------------------------------------------------
# window.py wiring guards (minimal-touch contract)

class TestWindowWiring:
    def test_status_refresh_is_async(self):
        text = SRC.joinpath('home_page.py').read_text()
        assert 'from system_status import status_line_async' in text
        # f_privacy composes the `● mic · cam · loc` tail inside the async
        # callback, so the strip can no longer take the base line directly:
        # the contract is async delivery plus indicator composition.
        assert 'status_line_async(' in text
        assert 'format_indicator' in text
        assert 'status_strip.set_text' in text

    def test_no_hardcoded_hci0_left(self):
        assert '/org/bluez/hci0' not in SRC.joinpath(
            'system_settings.py').read_text()
