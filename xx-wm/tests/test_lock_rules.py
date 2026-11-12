"""PIN rules shared by the wizard and Settings, the PIN check in front of
security changes, Remove PIN, and a reset config.json never replacing a
PIN the root helper still holds.

Headless: settings sheets run on the recorder fakes from test_w5_settings;
helper calls go to a scripted transport over the real PinStore
(test_lock_hardening)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import helper_client  # noqa: E402
import test_lock_hardening as _hardening  # noqa: E402
import test_w5_settings as _w5  # noqa: E402
from pin_rules import MAX_PIN, valid_pin  # noqa: E402
from test_lock_hardening import UID, FakeHelper  # noqa: E402
from test_w5_settings import _labels, _sheet_win  # noqa: E402

# Shared fixtures, re-bound here so pytest finds them in this module.
cfg = _hardening.cfg
clock = _hardening.clock
store = _hardening.store
fake_gtk = _w5.fake_gtk


class ClearingHelper(FakeHelper):
    def install(self, monkeypatch):
        super().install(monkeypatch)
        monkeypatch.setattr(helper_client, 'clear_pin', self.clear_pin)
        return self

    def clear_pin(self, old, on_done):
        self.calls.append('ClearPin')
        if not self.reachable:
            return self._down(on_done)
        on_done(True, self.store.clear_pin(UID, old))


def _commit(box):
    button = box.children[-1]
    callback, _args = button.handlers['clicked']
    callback(button)


# ------------------------------------------------------------ valid_pin

class TestValidPin:
    @pytest.mark.parametrize('pin', ['123456', '000000', '9' * MAX_PIN])
    def test_accepts_six_to_cap_ascii_digits(self, pin):
        assert valid_pin(pin)

    @pytest.mark.parametrize('pin', [
        '', '1', '12345', '12345a', 'abcdef', '123 456', '9' * (MAX_PIN + 1),
        '١٢٣٤٥٦',        # Arabic-Indic digits: str.isdigit() says yes
        '²²²²²²', None, 123456,
    ])
    def test_refuses_everything_else(self, pin):
        assert not valid_pin(pin)

    def test_cap_is_the_lock_screen_entry_cap(self):
        from lock_screen import _MAX_PIN
        assert MAX_PIN == _MAX_PIN


class TestChangePinUsesValidPin:
    def _local(self, cfg, monkeypatch):
        monkeypatch.setenv('XX_WM_PIN_HELPER', '0')
        cfg.set_pin('111111')
        return cfg

    @pytest.mark.parametrize('new', ['1', '12345', '1234ab', '12 3456'])
    def test_short_or_non_digit_refused(self, cfg, monkeypatch, new):
        import settings_lock
        cfg = self._local(cfg, monkeypatch)
        ok, msg = settings_lock.change_pin(cfg, '111111', new, new)
        assert ok is False and 'digits' in msg
        assert cfg.verify_pin('111111')

    def test_helper_path_validates_before_any_call(self, cfg, store,
                                                   monkeypatch):
        import settings_lock
        fake = FakeHelper(store).install(monkeypatch)
        store.set_pin(UID, '', '111111')
        cfg.set_pin_in_helper()
        ok, _msg = settings_lock.change_pin(cfg, '111111', '7', '7',
                                            on_async=lambda *a: None)
        assert ok is False and fake.calls == []

    def test_wizard_refuses_what_settings_refuses(self):
        import first_boot
        from test_first_boot_pin import _wizard
        wizard = _wizard()
        wizard._pin_buf = '12345x'
        first_boot.FirstBootWizard._on_pin_next(wizard, None)
        assert wizard._stack.visible_child_name is None
        assert first_boot.valid_pin is valid_pin


# ------------------------------------------------- PIN check in settings

class TestAskPin:
    def _win(self, cfg):
        win = _sheet_win()
        win.config = cfg
        return win

    def test_no_pin_runs_at_once_without_a_sheet(self, fake_gtk, cfg):
        import settings_lock
        win = self._win(cfg)
        ran = []
        assert settings_lock.ask_pin(win, object(), 'T', 'Go',
                                     lambda: ran.append(1)) is None
        assert ran == [1] and win.popovers == []

    def test_wrong_pin_keeps_sheet_and_escalates(self, fake_gtk, cfg,
                                                 monkeypatch):
        import settings_lock
        monkeypatch.setenv('XX_WM_PIN_HELPER', '0')
        cfg.set_pin('246810')
        win = self._win(cfg)
        ran = []
        box, entries = settings_lock.ask_pin(win, object(), 'Trust X',
                                             'Trust', lambda: ran.append(1))
        assert list(entries) == ['pin']
        entries['pin'].set_text('000000')
        _commit(box)
        (_content, popover), = win.popovers
        assert ran == [] and popover.down is False
        assert 'Current PIN is wrong.' in _labels(box)
        assert entries['pin'].get_text() == ''
        assert cfg.get_lockout_state()[0] == 1

    def test_right_pin_runs_and_closes(self, fake_gtk, cfg, monkeypatch):
        import settings_lock
        monkeypatch.setenv('XX_WM_PIN_HELPER', '0')
        cfg.set_pin('246810')
        win = self._win(cfg)
        ran = []
        box, entries = settings_lock.ask_pin(win, object(), 'T', 'Go',
                                             lambda: ran.append(1))
        entries['pin'].set_text('246810')
        _commit(box)
        assert ran == [1] and win.popovers[0][1].down is True

    def test_live_lockout_refuses_even_the_right_pin(self, fake_gtk, cfg,
                                                     monkeypatch):
        import time

        import settings_lock
        monkeypatch.setenv('XX_WM_PIN_HELPER', '0')
        cfg.set_pin('246810')
        now = time.time()
        cfg.set_lockout_state(5, now + 300, start=now)
        win = self._win(cfg)
        ran = []
        box, entries = settings_lock.ask_pin(win, object(), 'T', 'Go',
                                             lambda: ran.append(1))
        entries['pin'].set_text('246810')
        _commit(box)
        assert ran == []

    def test_helper_held_pin_is_checked_by_the_helper(
            self, fake_gtk, cfg, store, monkeypatch):
        import settings_lock
        fake = FakeHelper(store).install(monkeypatch)
        store.set_pin(UID, '', '135790')
        cfg.set_pin_in_helper()
        win = self._win(cfg)
        ran = []
        box, entries = settings_lock.ask_pin(win, object(), 'T', 'Go',
                                             lambda: ran.append(1))
        entries['pin'].set_text('000000')
        _commit(box)
        assert ran == [] and 'VerifyPin' in fake.calls
        entries['pin'].set_text('135790')
        _commit(box)
        assert ran == [1]

    def test_unreachable_helper_refuses(self, fake_gtk, cfg, store,
                                        monkeypatch):
        import settings_lock
        FakeHelper(store, reachable=False).install(monkeypatch)
        cfg.set_pin_in_helper()
        win = self._win(cfg)
        ran = []
        box, entries = settings_lock.ask_pin(win, object(), 'T', 'Go',
                                             lambda: ran.append(1))
        entries['pin'].set_text('135790')
        _commit(box)
        assert ran == []
        assert 'Could not reach the PIN service.' in _labels(box)


# ----------------------------------------------------------- Remove PIN

class TestRemovePin:
    def test_local_wrong_pin_refused_and_escalates(self, cfg, monkeypatch):
        import settings_lock
        monkeypatch.setenv('XX_WM_PIN_HELPER', '0')
        cfg.set_pin('246810')
        ok, msg = settings_lock.remove_pin(cfg, '000000')
        assert ok is False and msg == 'Current PIN is wrong.'
        assert cfg.pin_hash is not None
        assert cfg.get_lockout_state()[0] == 1

    def test_local_right_pin_removes(self, cfg, monkeypatch):
        import settings_lock
        monkeypatch.setenv('XX_WM_PIN_HELPER', '0')
        cfg.set_pin('246810')
        assert settings_lock.remove_pin(cfg, '246810') == (
            True, 'PIN removed.')
        assert cfg.pin_hash is None

    def test_helper_pin_cleared_root_side(self, cfg, store, monkeypatch):
        import settings_lock
        ClearingHelper(store).install(monkeypatch)
        store.set_pin(UID, '', '246810')
        cfg.set_pin_in_helper()
        later = []
        ok, _msg = settings_lock.remove_pin(
            cfg, '000000', on_async=lambda *a: later.append(a))
        assert ok is None and later == [(False, 'Current PIN is wrong.')]
        assert store.has_pin(UID) and cfg.pin_in_helper
        settings_lock.remove_pin(cfg, '246810',
                                 on_async=lambda *a: later.append(a))
        assert later[-1] == (True, 'PIN removed.')
        assert not store.has_pin(UID) and cfg.pin_hash is None

    def test_row_only_while_a_pin_is_set(self, fake_gtk):
        import settings_lock
        win = _sheet_win()
        section = settings_lock.LockSection()
        section.build(win)
        assert section._remove_row.get_visible() is False
        win.config.pin_hash = 'set'
        section.refresh(win)
        assert section._remove_row.get_visible() is True

    def test_sheet_verifies_first(self, fake_gtk, cfg, monkeypatch):
        import settings_lock
        monkeypatch.setenv('XX_WM_PIN_HELPER', '0')
        cfg.set_pin('246810')
        win = _sheet_win()
        win.config = cfg
        section = settings_lock.LockSection()
        section.build(win)
        _box, entries = section._open_remove(win, object())
        assert list(entries) == ['current']
        assert section._on_remove_pin(win, {'current': '000000'}) == (
            'Current PIN is wrong.')
        assert cfg.pin_hash is not None
        assert section._on_remove_pin(win, {'current': '246810'}) is None
        assert cfg.pin_hash is None
        assert section._remove_row.get_visible() is False


# ------------------------------------- a reset config never replaces a PIN

class TestWizardKeepsHelperPin:
    def test_reset_config_cannot_set_a_new_pin(self, cfg, store,
                                               monkeypatch):
        import first_boot
        FakeHelper(store).install(monkeypatch)
        store.set_pin(UID, '', '135790')
        assert cfg.pin_hash is None  # config.json deleted or reset
        first_boot._store_pin(cfg, '000000')
        assert cfg.pin_in_helper
        assert store.verify_pin(UID, '135790')[0] is True
        out = []
        helper_client.pin_verify(cfg, '000000', lambda *a: out.append(a))
        assert out[0][0] is False

    def test_new_phone_still_sets_the_pin(self, cfg, store, monkeypatch):
        import first_boot
        FakeHelper(store).install(monkeypatch)
        first_boot._store_pin(cfg, '135790')
        assert cfg.pin_in_helper
        assert store.verify_pin(UID, '135790')[0] is True

    def test_no_helper_keeps_the_local_hash(self, cfg, monkeypatch):
        import first_boot
        monkeypatch.setenv('XX_WM_PIN_HELPER', '0')
        first_boot._store_pin(cfg, '135790')
        assert cfg.verify_pin('135790')


# ---------------------------------------------------------- Smart Lock trust

from test_smart_lock import (  # noqa: E402
    BSSID,
    BT,
    HOME,
    FakeBus,
    _bluez_objects,
    _monitor,
    _nm_objects,
)
from test_smart_lock import _StubConfig as _TrustConfig  # noqa: E402

_DEV = '/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF'


def _device(**props):
    base = {'Address': BT, 'Alias': 'Car', 'Connected': True,
            'Paired': True, 'Bonded': True, 'ServicesResolved': True}
    base.update(props)
    return base


def _bt_world(device_props, extra=None, profile=True):
    objects = {'/org/bluez/hci0': {'org.bluez.Adapter1': {}}}
    ifaces = {'org.bluez.Device1': device_props}
    if profile:
        ifaces['org.bluez.MediaControl1'] = {'Connected': True}
    objects[_DEV] = ifaces
    objects.update(extra or {})
    return objects


def _bt_trust(objects, bt=(BT,)):
    config = _TrustConfig(bt=list(bt))
    mon, bus = _monitor(config)
    bus.answer(0, objects)
    bus.answer(1, _nm_objects([]))
    return mon, config


class TestBluetoothTrustRules:
    def test_fully_connected_bonded_device_trusts(self):
        mon, _cfg = _bt_trust(_bt_world(_device()))
        assert mon.trusted_bt == ['Car']

    @pytest.mark.parametrize('missing', [
        {'Paired': False}, {'Bonded': False}, {'ServicesResolved': False},
        {'Connected': False}])
    def test_each_condition_is_required(self, missing):
        mon, _cfg = _bt_trust(_bt_world(_device(**missing)))
        assert mon.trusted_bt == []

    def test_old_bluez_without_bonded_does_not_trust(self):
        props = _device()
        del props['Bonded']
        mon, _cfg = _bt_trust(_bt_world(props))
        assert mon.trusted_bt == []

    def test_link_without_a_profile_does_not_trust(self):
        """A spoofed address can bring up a bare link; no profile = no trust."""
        mon, _cfg = _bt_trust(_bt_world(_device(), profile=False))
        assert mon.trusted_bt == []

    @pytest.mark.parametrize('extra, ifaces', [
        ({_DEV + '/sep1/fd0': {'org.bluez.MediaTransport1': {
            'State': 'idle'}}}, {}),
        ({}, {'org.bluez.Network1': {'Connected': True}}),
        ({}, {'org.bluez.Input1': {'ReconnectMode': 'any'}}),
    ])
    def test_audio_pan_or_hid_counts_as_a_profile(self, extra, ifaces):
        objects = _bt_world(_device(), extra=extra, profile=False)
        objects[_DEV].update(ifaces)
        mon, _cfg = _bt_trust(objects)
        assert mon.trusted_bt == ['Car']

    def test_transport_of_another_device_does_not_count(self):
        other = '/org/bluez/hci0/dev_11_11_11_11_11_11/sep1/fd0'
        objects = _bt_world(_device(), profile=False,
                            extra={other: {'org.bluez.MediaTransport1': {}}})
        mon, _cfg = _bt_trust(objects)
        assert mon.trusted_bt == []

    def test_unbonded_entry_is_dropped_from_config(self):
        stale = '11:22:33:44:55:66'
        objects = _bt_world(_device())
        objects['/org/bluez/hci0/dev_11'] = {'org.bluez.Device1': {
            'Address': stale, 'Paired': False, 'Bonded': False}}
        _mon, config = _bt_trust(objects, bt=(BT, stale, '22:22:22:22:22:22'))
        assert config.smart_lock_bt_devices == [BT]

    def test_list_kept_while_bluez_has_no_adapter(self):
        objects = _bt_world(_device())
        del objects['/org/bluez/hci0']
        _mon, config = _bt_trust(objects, bt=(BT, '22:22:22:22:22:22'))
        assert config.smart_lock_bt_devices == [BT, '22:22:22:22:22:22']

    @pytest.mark.parametrize('iface, changed', [
        ('org.bluez.Device1', {'ServicesResolved': True}),
        ('org.bluez.Device1', {'Bonded': False}),
        ('org.bluez.MediaControl1', {'Connected': True}),
    ])
    def test_trust_relevant_changes_requery(self, iface, changed):
        mon, bus = _monitor(_TrustConfig(bt=[BT]))
        before = len(bus.calls)
        sid = bus.sub_id('PropertiesChanged', sender='org.bluez')
        bus.fire(sid, 'org.bluez', _DEV, 'org.freedesktop.DBus.Properties',
                 'PropertiesChanged', (iface, changed, []))
        assert len(bus.calls) == before + 1


def _wifi_trust(connections, trusted=(HOME,)):
    mon, bus = _monitor(_TrustConfig(ssids=list(trusted)))
    bus.answer(0, _bluez_objects([]))
    bus.answer(1, _nm_objects(connections))
    return mon, bus


class TestWifiTrustRules:
    def test_queries_networkmanager_where_its_object_manager_is(self):
        _mon, bus = _monitor(_TrustConfig(ssids=[HOME]))
        nm = [c for c in bus.calls
              if c['bus'] == 'org.freedesktop.NetworkManager']
        assert nm and all(c['path'] == '/org/freedesktop' for c in nm)

    def test_trusted_ssid_and_bssid_on_wpa2_trusts(self):
        mon, _bus = _wifi_trust([('/a/0', 'Home', '802-11-wireless', 2)])
        assert mon.trusted_ssid == 'Home'

    def test_same_name_other_access_point_does_not_trust(self):
        mon, _bus = _wifi_trust(
            [('/a/0', 'Home', '802-11-wireless', 2, 'DE:AD:BE:EF:00:01')])
        assert mon.trusted_ssid is None

    def test_profile_name_is_not_the_ssid(self):
        """The old check read the profile Id; the AP's own SSID decides."""
        objects = _nm_objects([('/a/0', 'Home', '802-11-wireless', 2)])
        ap = next(v for v in objects.values()
                  if 'org.freedesktop.NetworkManager.AccessPoint' in v)
        ap['org.freedesktop.NetworkManager.AccessPoint']['Ssid'] = list(
            b'Elsewhere')
        mon, bus = _monitor(_TrustConfig(ssids=[HOME]))
        bus.answer(0, _bluez_objects([]))
        bus.answer(1, objects)
        assert mon.trusted_ssid is None

    @pytest.mark.parametrize('rsn', [0, 0x800, 0x1000, 0x800 | 0x1000])
    def test_open_and_owe_never_trust(self, rsn):
        mon, _bus = _wifi_trust(
            [('/a/0', 'Home', '802-11-wireless', 2, BSSID, rsn)])
        assert mon.trusted_ssid is None

    @pytest.mark.parametrize('rsn', [0x100, 0x200, 0x400, 0x2000])
    def test_psk_eap_sae_suite_b_trust(self, rsn):
        mon, _bus = _wifi_trust(
            [('/a/0', 'Home', '802-11-wireless', 2, BSSID, rsn)])
        assert mon.trusted_ssid == 'Home'

    def test_old_name_only_entry_trusts_nothing(self):
        mon, _bus = _wifi_trust([('/a/0', 'Home', '802-11-wireless', 2)],
                                trusted=('Home',))
        assert mon.trusted_ssid is None

    def test_roaming_requeries(self):
        mon, bus = _monitor(_TrustConfig(ssids=[HOME]))
        before = len(bus.calls)
        sid = bus.sub_id('PropertiesChanged',
                         sender='org.freedesktop.NetworkManager')
        bus.fire(sid, 'org.freedesktop.NetworkManager', '/dev/3',
                 'org.freedesktop.DBus.Properties', 'PropertiesChanged',
                 ('org.freedesktop.NetworkManager.Device.Wireless',
                  {'ActiveAccessPoint': '/ap/9'}, []))
        assert len(bus.calls) == before + 1

    def test_connected_ap_reports_security(self):
        import smart_lock
        objects = _nm_objects(
            [('/a/0', 'Cafe', '802-11-wireless', 2, BSSID.lower(), 0)])
        assert smart_lock.connected_ap(objects) == {
            'ssid': 'Cafe', 'bssid': BSSID, 'secure': False}
        assert smart_lock.connected_ap(_nm_objects([])) is None


class TestPickerQueries:
    def test_bonded_query_is_async_and_filters(self):
        import smart_lock
        bus = FakeBus()
        out = []
        smart_lock.query_bonded_bt_devices(out.append, bus=bus)
        assert out == [] and bus.calls[0]['bus'] == 'org.bluez'
        bus.answer(0, _bluez_objects([
            ('/d/0', BT, False, 'Buds'),
            ('/d/1', '11:11:11:11:11:11', False, 'Speaker', False)]))
        assert [(d.address, d.name) for d in out[0]] == [(BT, 'Buds')]

    def test_wifi_query_reports_none_empty_or_ap(self):
        import smart_lock
        bus = FakeBus()
        out = []
        for _ in range(3):
            smart_lock.query_connected_wifi(out.append, bus=bus)
        bus.fail(0)
        bus.answer(1, _nm_objects([]))
        bus.answer(2, _nm_objects([('/a/0', 'Home', '802-11-wireless', 2)]))
        assert out == [None, {}, {'ssid': 'Home', 'bssid': BSSID,
                                  'secure': True}]
