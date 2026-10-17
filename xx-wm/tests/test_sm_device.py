"""Lane sm_device: device info, night light, logs, keyboard layout, lock.

Headless tests for the pure backends (worker-thread only — never the
main loop) plus registry/install contracts. Fakes mirror the real
signatures per XXWM_RULES.md. Nothing here runs real package, service,
firewall, gamma or password changes.
"""
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).parent.parent / 'launcher' / 'src'
HELPER_DIR = Path(__file__).parent.parent / 'launcher' / 'helper'
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HELPER_DIR))

import xx_wm_helper as helper  # noqa: E402
import settings_page  # noqa: E402  (first: section modules bind its _Section)


# ------------------------------------------------------- device info backend


class TestDeviceName:
    def test_devicetree_model_wins(self, tmp_path):
        import settings_device as dev
        fw = tmp_path / 'firmware' / 'devicetree' / 'base'
        fw.mkdir(parents=True)
        (fw / 'model').write_bytes(b'Purism Librem 5\x00')
        dmi = tmp_path / 'dmi' / 'id'
        dmi.mkdir(parents=True)
        (dmi / 'product_name').write_text('PC\n')
        name = dev.read_device_name(
            dt_model=str(fw / 'model'), dmi_dir=str(dmi))
        assert name == 'Purism Librem 5'

    def test_dmi_product_fallback(self, tmp_path):
        import settings_device as dev
        dmi = tmp_path / 'dmi'
        dmi.mkdir()
        (dmi / 'product_name').write_text('OnePlus 6T\n')
        name = dev.read_device_name(
            dt_model=str(tmp_path / 'missing'), dmi_dir=str(dmi))
        assert name == 'OnePlus 6T'

    def test_dmi_vendor_version_fallback(self, tmp_path):
        import settings_device as dev
        dmi = tmp_path / 'dmi'
        dmi.mkdir()
        (dmi / 'sys_vendor').write_text('QEMU\n')
        (dmi / 'product_version').write_text('2.12\n')
        name = dev.read_device_name(
            dt_model=str(tmp_path / 'missing'), dmi_dir=str(dmi))
        assert name == 'QEMU 2.12'

    def test_blank_files_fall_through(self, tmp_path):
        import settings_device as dev
        dmi = tmp_path / 'dmi'
        dmi.mkdir()
        (dmi / 'product_name').write_text('\n')
        name = dev.read_device_name(
            dt_model=str(tmp_path / 'missing'), dmi_dir=str(dmi))
        assert name and name != '\n'


class TestOsCpuMem:
    def test_os_pretty_name(self):
        import settings_device as dev
        text = ('NAME="postmarketOS"\nVERSION_ID="23.12"\n'
                'PRETTY_NAME="postmarketOS v23.12"\n')
        assert dev.parse_os_release(text) == 'postmarketOS v23.12'

    def test_os_pretty_name_missing(self):
        import settings_device as dev
        assert dev.parse_os_release('NAME=Foo\n') == 'Unknown'

    def test_cpu_summary(self):
        import settings_device as dev
        assert dev.format_cpu(8, 'aarch64') == '8 cores (aarch64)'

    def test_mem_summary_with_swap(self):
        import settings_device as dev
        meminfo = ('MemTotal:        3992852 kB\n'
                   'MemAvailable:    1024000 kB\n'
                   'SwapTotal:       2097148 kB\n'
                   'SwapFree:        2097148 kB\n')
        swaps = ('Filename\t\t\t\tType\t\tSize\tUsed\n'
                 '/dev/zram0                              partition\t'
                 '2097148\t0\n')
        text = dev.format_mem(meminfo, swaps, zram_size=2097148 * 1024)
        assert 'RAM' in text and 'zram' in text

    def test_uptime_format(self):
        import settings_device as dev
        assert dev.format_uptime(90) == '1m'
        assert dev.format_uptime(3700) == '1h 01m'
        assert dev.format_uptime(90000) == '1d 1h'

    def test_thermal_grouped_by_type(self):
        import settings_device as dev
        zones = [
            {'type': 'cpu-thermal', 'temp': 42000},
            {'type': 'cpu-thermal', 'temp': 44000},
            {'type': 'battery', 'temp': 31000},
        ]
        grouped = dev.group_thermal(zones)
        assert grouped['cpu-thermal'] == '44.0°C'
        assert grouped['battery'] == '31.0°C'


class TestNetAddrs:
    def test_ip_brief_parsed(self):
        import settings_device as dev
        out = ('lo               UNKNOWN        127.0.0.1/8 ::1/128 \n'
               'wlan0            UP             192.168.1.5/24 '
               'fe80::1/64 \n')
        addrs = dev.parse_ip_brief(out)
        assert addrs['wlan0'] == ['192.168.1.5', 'fe80::1']
        assert 'lo' not in addrs

    def test_mac_read_from_sysfs(self, tmp_path):
        import settings_device as dev
        net = tmp_path / 'net'
        (net / 'wlan0').mkdir(parents=True)
        (net / 'wlan0' / 'address').write_text('aa:bb:cc:dd:ee:ff\n')
        (net / 'lo').mkdir()
        (net / 'lo' / 'address').write_text('00:00:00:00:00:00\n')
        macs = dev.read_iface_macs(str(net))
        assert macs == {'wlan0': 'aa:bb:cc:dd:ee:ff'}


class TestBatteryHealth:
    def test_health_from_upower_props(self):
        import settings_device as dev
        props = {
            'Energy': 40.0, 'EnergyFull': 45.0,
            'EnergyFullDesign': 50.0, 'ChargeCycles': 123,
            'Temperature': 301.0, 'Voltage': 4.1,
            'Percentage': 89, 'State': 2,
        }
        health = dev.format_battery_health(props)
        assert health['health'] == '90%'
        assert health['cycles'] == '123'
        assert health['temperature'] == '27.9°C'
        assert health['voltage'] == '4.1 V'

    def test_health_degrades_without_design_energy(self):
        import settings_device as dev
        health = dev.format_battery_health({'Percentage': 50, 'State': 1})
        assert health['health'] == 'Unknown'


class TestModem:
    def test_imei_hidden_by_default(self):
        import settings_device as dev
        props = {'Manufacturer': 'Quectel', 'Model': 'EG25-G',
                 'Revision': 'EG25GGBR07A08M2G',
                 'EquipmentIdentifier': '864567041234567'}
        shown = dev.format_modem(props, reveal_imei=False)
        assert '864567041234567' not in shown
        assert 'Quectel' in shown and 'EG25-G' in shown

    def test_imei_revealed_on_tap_only(self):
        import settings_device as dev
        props = {'Manufacturer': 'Quectel',
                 'EquipmentIdentifier': '864567041234567'}
        assert '864567041234567' in dev.format_modem(props, reveal_imei=True)

    def test_no_modem_is_plain_text(self):
        import settings_device as dev
        assert dev.format_modem(None) == 'No modem detected.'


# ------------------------------------------------- night light + profiles


class TestNightLight:
    def _which(self, names):
        def lookup(name):
            return '/usr/bin/%s' % name if name in names else None
        return lookup

    def test_tool_prefers_wlsunset(self):
        import settings_display as disp
        assert disp.night_tool(
            which=self._which(('wlsunset', 'gammastep'))) == 'wlsunset'

    def test_tool_falls_back_to_gammastep(self):
        import settings_display as disp
        assert disp.night_tool(
            which=self._which(('gammastep',))) == 'gammastep'

    def test_tool_none_when_missing(self):
        import settings_display as disp
        assert disp.night_tool(which=lambda _n: None) is None

    def test_temp_clamped_to_range(self):
        import settings_display as disp
        assert disp.clamp_temp(100) == 2200
        assert disp.clamp_temp(7000) == 6500
        assert disp.clamp_temp(3000) == 3000
        assert disp.DEFAULT_TEMP == 2200

    def test_wlsunset_manual_argv(self):
        import settings_display as disp
        argv = disp.spawn_argv('wlsunset', 2200, None, None)
        assert argv[0] == 'wlsunset'
        assert '-t' in argv and '2200' in argv

    def test_wlsunset_schedule_argv_carries_coords(self):
        import settings_display as disp
        argv = disp.spawn_argv('wlsunset', 2200, 48.85, 2.35)
        joined = ' '.join(argv)
        assert '48.85' in joined and '2.35' in joined

    def test_gammastep_argv(self):
        import settings_display as disp
        argv = disp.spawn_argv('gammastep', 3000, None, None)
        assert argv[0] == 'gammastep'
        assert '3000:3000' in ' '.join(argv) or '3000' in argv

    def test_sunrise_sunset_equator_equinox(self):
        import settings_display as disp
        rise, set_ = disp.sunrise_sunset(0.0, 0.0, 80)
        assert rise == pytest.approx(6.0, abs=0.5)
        assert set_ == pytest.approx(18.0, abs=0.5)

    def test_night_active_overnight(self):
        import settings_display as disp
        assert disp.night_active(22.0, 6.0, 18.0) is True
        assert disp.night_active(12.0, 6.0, 18.0) is False
        assert disp.night_active(5.0, 6.0, 18.0) is True


class TestPowerProfiles:
    def test_bus_names_in_priority_order(self):
        import settings_display as disp
        assert disp.PROFILE_BUS_NAMES == (
            'org.freedesktop.UPower.PowerProfiles',
            'net.hadess.PowerProfiles',
        )

    def test_active_profile_from_getall_tuple(self):
        import settings_display as disp
        reply = ({'ActiveProfile': 'balanced',
                  'Profiles': [{'Profile': 'balanced'}]},)
        assert disp.parse_active_profile(reply) == 'balanced'

    def test_missing_profile_is_none(self):
        import settings_display as disp
        assert disp.parse_active_profile(({},)) is None
        assert disp.parse_active_profile(None) is None


# ------------------------------------------------------------------- logs


class TestLogBackends:
    def test_journal_argv_this_boot(self):
        import settings_logs as logs
        argv = logs.journal_argv('errors-boot')
        assert argv[:3] == ['journalctl', '-b', '-p']
        assert 'err..alert' in argv

    def test_journal_argv_prev_boot(self):
        import settings_logs as logs
        assert '-1' in logs.journal_argv('errors-prev')

    def test_journal_argv_user(self):
        import settings_logs as logs
        assert '--user' in logs.journal_argv('user')

    def test_output_capped(self):
        import settings_logs as logs
        big = 'x' * (logs.MAX_LOG_BYTES + 100)
        assert len(logs.cap_text(big)) <= logs.MAX_LOG_BYTES

    def test_secrets_scrubbed(self):
        import settings_logs as logs
        text = ('connecting ssid=Home psk:mysecretpassword\n'
                'user password=hunter2 ok\n'
                'pin: 1234 entered\n'
                'plain line stays\n')
        scrubbed = logs.scrub_secrets(text)
        assert 'mysecretpassword' not in scrubbed
        assert 'hunter2' not in scrubbed
        assert '1234' not in scrubbed
        assert 'plain line stays' in scrubbed


class TestHelperReadLog:
    @pytest.fixture()
    def layout(self, tmp_path):
        logdir = tmp_path / 'logs'
        logdir.mkdir()
        (logdir / 'messages').write_text('sys line 1\nsys line 2\n')
        (logdir / 'xx-wm.log').write_text('shell up\n')
        return SimpleNamespace(
            logdir=str(logdir),
            state=str(tmp_path / 'state.json'),
        )

    def make_core(self, layout, **over):
        kw = dict(state_file=layout.state,
                  log_dir=layout.logdir, run=lambda argv: SimpleNamespace(
                      returncode=0, stdout='', stderr=''))
        kw.update(over)
        return helper.HelperCore(**kw)

    def test_allowlist_reads_plain_file(self, layout):
        core = self.make_core(layout)
        assert core.read_log('messages') == 'sys line 1\nsys line 2\n'

    def test_unknown_name_rejected(self, layout):
        core = self.make_core(layout)
        with pytest.raises(helper.HelperError) as exc:
            core.read_log('/etc/shadow')
        assert exc.value.dbus_name == helper.ERROR_INVALID_ARGS

    def test_output_size_capped(self, layout, tmp_path):
        big = tmp_path / 'logs' / 'messages'
        big.write_text('y' * (helper.MAX_LOG_BYTES + 5000))
        core = self.make_core(layout)
        assert len(core.read_log('messages')) <= helper.MAX_LOG_BYTES

    def test_missing_file_is_failed_not_crash(self, layout, tmp_path):
        (tmp_path / 'logs' / 'messages').unlink()
        core = self.make_core(layout)
        with pytest.raises(helper.HelperError) as exc:
            core.read_log('messages')
        assert exc.value.dbus_name == helper.ERROR_FAILED

    def test_dmesg_through_helper(self, layout):
        def run(argv):
            assert argv[0] == 'dmesg'
            assert '--level=err,warn' in argv
            return SimpleNamespace(returncode=0, stdout='kern warn\n',
                                   stderr='')
        core = self.make_core(layout, run=run)
        assert core.read_log('dmesg') == 'kern warn\n'

    def test_service_dispatches_read_log(self, layout):
        core = self.make_core(layout)
        seen = []

        class FakeInvocation:
            def return_value(self, value):
                seen.append(('ok', value))

            def return_dbus_error(self, name, msg):
                seen.append(('err', name, msg))

        svc = helper.HelperService(
            connection=None, core=core,
            authorize=lambda _c, _s, _a, _i, cb: cb(True, 1000, None))
        svc._on_authorized(FakeInvocation(), 'ReadLog', ('messages',),
                           True, 1000, None)
        assert seen and seen[0][0] == 'ok'

    def test_read_log_action_is_read_only(self):
        text = (Path(__file__).parent.parent / 'launcher' / 'data'
                / 'polkit-1' / 'actions'
                / 'io.piercingxx.XXWM.Helper.policy').read_text()
        assert 'action id="io.piercingxx.XXWM.Helper1.read-log"' in text
        assert text.count('<allow_active>yes</allow_active>') == 9

    def test_introspection_lists_read_log(self):
        assert '<method name="ReadLog">' in helper.INTROSPECT_XML


# --------------------------------------------------------------- keyboard


class TestKeyboard:
    def test_default_key_parses(self):
        import settings_keyboard as kb
        assert kb.parse_layout_key('us+colemak') == ('us', 'colemak')
        assert kb.parse_layout_key('us') == ('us', '')

    def test_round_trip(self):
        import settings_keyboard as kb
        assert kb.format_layout_key('gb', '') == 'gb'
        assert kb.format_layout_key('us', 'dvorak') == 'us+dvorak'

    def test_gsettings_value(self):
        import settings_keyboard as kb
        value = kb.gsettings_sources('us', 'colemak')
        assert value == "[('xkb', 'us+colemak')]"

    def test_presets_cover_required_set(self):
        import settings_keyboard as kb
        keys = {label: (layout, variant)
                for label, layout, variant in kb.KEYBOARD_PRESETS}
        assert keys['US Colemak'] == ('us', 'colemak')
        assert keys['Colemak-DH'] == ('us', 'colemak_dh')
        assert keys['QWERTY'] == ('us', '')
        assert keys['Dvorak'] == ('us', 'dvorak')
        assert keys['UK'] == ('gb', '')
        assert keys['DE'] == ('de', '')

    @pytest.mark.parametrize('layout,variant', [
        ('us', 'colemak'), ('de', ''), ('fr', 'oss'),
    ])
    def test_valid_custom_accepted(self, layout, variant):
        import settings_keyboard as kb
        assert kb.validate_custom(layout, variant) is None

    @pytest.mark.parametrize('layout,variant', [
        ('', 'colemak'), ('us;rm', ''), ('us', 'a b'), ('U' * 40, ''),
    ])
    def test_invalid_custom_rejected(self, layout, variant):
        import settings_keyboard as kb
        assert kb.validate_custom(layout, variant) is not None

    def test_session_reads_config_key(self):
        text = (Path(__file__).parent.parent / 'launcher' / 'data'
                / 'xx-wm-session.in').read_text()
        assert 'keyboard_layout' in text
        assert "cfg.get('keyboard_layout', 'us')" in text
        assert 'piercing-num' in text

    def test_config_default_and_backup(self, tmp_path, monkeypatch):
        import config
        import backup
        assert config.DEFAULT_CONFIG['keyboard_layout'] == 'us'
        monkeypatch.setenv('HOME', str(tmp_path))
        cfg = config.ShellConfig()
        cfg.set('keyboard_layout', 'de')
        exported = backup.export_backup(cfg)
        assert exported['keyboard_layout'] == 'de'
        ok, _err = backup.validate_backup(dict(exported, version=1))
        assert ok


# ------------------------------------------------------------------- lock


class TestChangePin:
    def _configured(self, tmp_path):
        import config as _config
        cfg = _config.ShellConfig.__new__(_config.ShellConfig)
        cfg.data = dict(_config.DEFAULT_CONFIG)
        cfg.config_dir = tmp_path / 'cfg'
        cfg.config_path = cfg.config_dir / 'config.json'
        cfg.set_pin('1234')
        cfg.clear_lockout_state()
        return cfg

    def test_change_with_correct_current(self, tmp_path):
        import settings_lock as lock
        cfg = self._configured(tmp_path)
        ok, _msg = lock.change_pin(cfg, '1234', '5678', '5678')
        assert ok
        assert cfg.verify_pin('5678')

    def test_wrong_current_refused(self, tmp_path):
        import settings_lock as lock
        cfg = self._configured(tmp_path)
        ok, _msg = lock.change_pin(cfg, '0000', '5678', '5678')
        assert not ok
        assert cfg.verify_pin('1234')

    def test_mismatched_new_refused(self, tmp_path):
        import settings_lock as lock
        cfg = self._configured(tmp_path)
        ok, _msg = lock.change_pin(cfg, '1234', '5678', '8765')
        assert not ok
        assert cfg.verify_pin('1234')

    def test_lockout_blocks_change(self, tmp_path):
        import settings_lock as lock
        import time as _time
        cfg = self._configured(tmp_path)
        cfg.set_lockout_state(5, _time.time() + 300)
        ok, msg = lock.change_pin(cfg, '1234', '5678', '5678')
        assert not ok
        assert 'ock' in msg.lower()

    def test_no_pin_digits_logged(self, tmp_path, caplog):
        import settings_lock as lock
        cfg = self._configured(tmp_path)
        with caplog.at_level(logging.DEBUG):
            lock.change_pin(cfg, '1234', '5678', '5678')
            lock.change_pin(cfg, '0000', '9999', '9999')
        for record in caplog.records:
            if record.name.startswith('settings_lock'):
                assert '1234' not in record.getMessage()
                assert '5678' not in record.getMessage()
                assert '9999' not in record.getMessage()

    def test_passwd_uses_terminal_table(self, monkeypatch):
        import update_checker as updater
        import shutil
        monkeypatch.setattr(
            shutil, 'which',
            lambda name: '/usr/bin/%s' % name if name in (
                'bash', 'foot') else None)
        binary, argv = updater.terminal_argv('passwd')
        assert binary == 'foot'
        assert 'passwd' in ' '.join(argv)


# ------------------------------------------------- registry and install set


class TestSmDeviceRegistry:
    def test_new_sections_registered_gestures_last(self):
        keys = [key for key, _cls in settings_page.SETTINGS_SECTIONS]
        assert keys[-1] == 'gestures'
        assert len(keys) == len(set(keys))
        for key in ('display', 'keyboard', 'lock'):
            assert key in keys
            assert keys.count(key) == 1
        for _key, cls in settings_page.SETTINGS_SECTIONS:
            assert issubclass(cls, settings_page._Section)

    def test_deep_link_keys(self):
        for key in ('display', 'keyboard', 'lock', 'storage'):
            assert key in settings_page._SETTINGS_SECTION_KEYS

    def test_meson_lists_new_modules(self):
        meson = (Path(__file__).parent.parent / 'launcher'
                 / 'meson.build').read_text(encoding='utf-8')
        for name in ('src/settings_device.py', 'src/settings_display.py',
                     'src/settings_keyboard.py', 'src/settings_lock.py',
                     'src/settings_logs.py'):
            assert "'%s'" % name in meson

    def test_night_tile_registered(self):
        import quick_actions
        assert 'night' in [tile.key for tile in quick_actions._TILES]
        assert quick_actions.tile_settings_section('night') == 'display'

    def test_night_tile_hides_without_tools(self, monkeypatch):
        import shutil
        import quick_actions
        monkeypatch.setattr(shutil, 'which', lambda _name: None)
        assert quick_actions._night_light_available() is False

    def test_night_light_config_keys(self):
        import config
        assert config.DEFAULT_CONFIG['night_light'] is False
        assert config.DEFAULT_CONFIG['night_light_temp'] == 2200
        assert config.DEFAULT_CONFIG['night_light_schedule'] is False

    def test_install_ships_wlsunset(self):
        text = (Path(__file__).parent.parent / 'scripts'
                / 'install.sh').read_text(encoding='utf-8')
        assert 'wlsunset' in text
