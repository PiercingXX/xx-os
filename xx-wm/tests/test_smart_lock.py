"""Smart Lock (Android parity): rule table, event-driven trust, lock lines.

Headless: smart_lock.py is GTK-free (gi only inside start(), which tests
never reach — a FakeBus is injected instead). lock_screen/power_menu run
as unbound methods on hand-built namespaces, the test_lock_screen idiom,
so no widget is ever constructed.
"""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import smart_lock
from smart_lock import (
    SmartLockMonitor,
    SmartLockState,
    current_state,
    evaluate,
    explicit_lock_pending,
    is_configured,
    lock_line_for,
    note_explicit_lock,
    note_pin_unlock,
)

NOW = 1_700_000_000.0
BT = 'AA:BB:CC:DD:EE:FF'
BSSID = '11:22:33:44:55:66'
HOME = {'ssid': 'Home', 'bssid': BSSID}


@pytest.fixture(autouse=True)
def _fresh_runtime():
    smart_lock._reset_runtime_state()
    smart_lock._reset_live_monitor()
    yield
    smart_lock._reset_runtime_state()
    smart_lock._reset_live_monitor()


def _state(**overrides):
    base = dict(
        pin_set=True,
        configured=True,
        trusted_bt=[BT],
        trusted_ssid=None,
        boot_pin_done=True,
        last_pin_time=NOW - 60.0,
        now=NOW,
        fail_count=0,
        lockout_active=False,
        force_pin_until_next_unlock=False,
    )
    base.update(overrides)
    return SmartLockState(**base)


# ------------------------------------------------------------------
# Fakes shaped like the real D-Bus API
# ------------------------------------------------------------------

class _FakeVariant:
    """unpack() yields the TUPLE of out-args, like a real Gio reply."""

    def __init__(self, payload):
        self._payload = payload

    def unpack(self):
        return (self._payload,)


class _FakeResult:
    def __init__(self, payload=None, error=None):
        self._payload = payload
        self._error = error


class _FakeParams:
    def __init__(self, values):
        self._values = values

    def unpack(self):
        return self._values


class FakeBus:
    """Mirrors Gio.DBusConnection semantics: signal_subscribe records the
    full 8-arg form (trailing user_data included), call() defers the
    (conn, result) callback until the test answers, and call_finish()
    raises on transport errors like the real one."""

    def __init__(self):
        self.calls: list[dict] = []
        self.subs: dict[int, dict] = {}
        self.unsubscribed: list[int] = []
        self._next_id = 1

    def signal_subscribe(self, sender, iface, member, path, arg0,
                         flags, callback, user_data):
        sid = self._next_id
        self._next_id += 1
        self.subs[sid] = {
            'sender': sender, 'iface': iface, 'member': member,
            'path': path, 'callback': callback, 'user_data': user_data,
        }
        return sid

    def signal_unsubscribe(self, sid):
        self.unsubscribed.append(sid)
        self.subs.pop(sid, None)

    def call(self, bus_name, obj_path, iface, method, params, reply_type,
             flags, timeout, cancellable, callback):
        self.calls.append({
            'bus': bus_name, 'path': obj_path, 'iface': iface,
            'method': method, 'callback': callback,
        })

    def call_finish(self, result):
        if result._error is not None:
            raise result._error
        return _FakeVariant(result._payload)

    def answer(self, index, payload):
        rec = self.calls[index]
        rec['callback'](self, _FakeResult(payload))

    def fail(self, index, error=None):
        rec = self.calls[index]
        rec['callback'](self, _FakeResult(error=error or Exception('boom')))

    def fire(self, sid, sender, path, iface, signal, params_tuple):
        """Deliver a signal the way Gio does when user_data was passed
        (even None): the 7-arg handler form."""
        rec = self.subs[sid]
        rec['callback'](self, sender, path, iface, signal,
                        _FakeParams(params_tuple), rec['user_data'])

    def sub_id(self, member, sender=None):
        for sid, rec in self.subs.items():
            if rec['member'] == member and (
                    sender is None or rec['sender'] == sender):
                return sid
        raise KeyError(member)


def _bluez_objects(devices, adapter=True):
    """devices: (path, address, connected, alias-or-None[, bonded]).
    Shaped like BlueZ: a connected device is Paired+Bonded with services
    resolved and AVRCP up; an adapter object sits next to the devices."""
    objects = {}
    if adapter:
        objects['/org/bluez/hci0'] = {'org.bluez.Adapter1': {
            'Address': '00:00:00:00:00:01', 'Powered': True}}
    for path, address, connected, alias, *rest in devices:
        bonded = rest[0] if rest else True
        objects[path] = {
            'org.bluez.Device1': {
                'Address': address, 'Connected': connected, 'Alias': alias,
                'Paired': bonded, 'Bonded': bonded,
                'ServicesResolved': connected,
            },
            'org.bluez.MediaControl1': {'Connected': connected},
        }
    return objects


_WPA2_PSK = 0x100 | 0x8 | 0x80    # key-mgmt PSK, pair/group CCMP


def _nm_objects(connections):
    """connections: (path, ssid, type, state[, bssid[, rsn_flags]]).
    Shaped like NetworkManager's /org/freedesktop ObjectManager: the
    active connection names its device, the device its access point."""
    objects = {}
    for index, (path, ssid, ctype, state, *rest) in enumerate(connections):
        bssid = rest[0] if rest else BSSID
        rsn = rest[1] if len(rest) > 1 else _WPA2_PSK
        device = '/org/freedesktop/NetworkManager/Devices/%d' % (index + 3)
        ap = '/org/freedesktop/NetworkManager/AccessPoint/%d' % (index + 7)
        objects[path] = {'org.freedesktop.NetworkManager.Connection.Active': {
            'Id': ssid, 'Type': ctype, 'State': state, 'Devices': [device],
            'SpecificObject': ap,
        }}
        objects[device] = {
            'org.freedesktop.NetworkManager.Device': {'Interface': 'wlan0'},
            'org.freedesktop.NetworkManager.Device.Wireless': {
                'ActiveAccessPoint': ap},
        }
        objects[ap] = {'org.freedesktop.NetworkManager.AccessPoint': {
            'Ssid': list(ssid.encode()), 'HwAddress': bssid,
            'Flags': 1 if rsn else 0, 'WpaFlags': 0, 'RsnFlags': rsn,
        }}
    return objects


class _StubConfig:
    """Duck-typed ShellConfig: only what smart_lock reads."""

    def __init__(self, pin='123456', bt=(), ssids=(), last_pin=0.0,
                 lock_notifs='summary'):
        self.pin_hash = pin
        self._bt = list(bt)
        self._ss = list(ssids)
        self._last_pin = last_pin
        self.recorded: list[float] = []
        self.lock_screen_notifications = lock_notifs

    @property
    def smart_lock_bt_devices(self):
        return list(self._bt)

    @property
    def smart_lock_wifi_ssids(self):
        return list(self._ss)

    @property
    def smart_lock_last_pin_time(self):
        return self._last_pin

    def record_smart_lock_pin_time(self, timestamp):
        self._last_pin = timestamp
        self.recorded.append(timestamp)

    def set_smart_lock_bt_devices(self, addresses):
        self._bt = list(addresses)

    def verify_pin(self, pin):
        return self.pin_hash is not None and pin == '123456'

    def clear_lockout_state(self):
        pass

    def set_lockout_state(self, _fails, _until):
        pass


def _monitor(config, bus=None, notified=None):
    bus = bus if bus is not None else FakeBus()
    mon = SmartLockMonitor(
        config,
        on_trust_changed=(
            (lambda: notified.append(True)) if notified is not None else None),
        bus=bus,
    )
    return mon, bus


# ------------------------------------------------------------------
# Pure rule table: state -> (requires_pin, reason)
# ------------------------------------------------------------------

class TestRuleTable:
    def test_trusted_swipe_unlocks_without_pin(self):
        assert evaluate(_state()) == (False, '')

    def test_no_pin_set_never_requires(self):
        assert evaluate(_state(pin_set=False, configured=False,
                               trusted_bt=[])) == (False, '')

    def test_untrusted_requires_pin_with_nearby_reason(self):
        assert evaluate(_state(trusted_bt=[], trusted_ssid=None)) == (
            True, 'PIN required — no trusted device nearby')

    def test_unconfigured_phone_shows_no_reason(self):
        requires, reason = evaluate(_state(configured=False, trusted_bt=[]))
        assert requires is True
        assert reason == ''

    def test_unconfigured_phone_with_pin_uses_normal_lock(self):
        requires, reason = evaluate(
            _state(configured=False, trusted_bt=[], trusted_ssid=None))
        assert (requires, reason) == (True, '')

    def test_boot_requires_pin_until_first_pin_unlock(self):
        assert evaluate(_state(boot_pin_done=False))[1] == (
            'PIN required — unlock with PIN after restart')

    def test_stale_pin_clock_requires_pin(self):
        old = _state(last_pin_time=NOW - (4 * 3600 + 1))
        assert evaluate(old)[1] == (
            'PIN required — PIN not entered for over 4 hours')

    def test_exactly_four_hours_still_trusted(self):
        assert evaluate(_state(last_pin_time=NOW - 4 * 3600)) == (False, '')

    def test_five_failed_attempts_require_pin(self):
        assert evaluate(_state(fail_count=5))[1] == (
            'PIN required — too many attempts')

    def test_four_failed_attempts_still_trusted(self):
        assert evaluate(_state(fail_count=4)) == (False, '')

    def test_live_lockout_requires_pin_without_fails(self):
        assert evaluate(_state(lockout_active=True))[1] == (
            'PIN required — too many attempts')

    def test_explicit_lock_requires_pin(self):
        assert evaluate(_state(force_pin_until_next_unlock=True))[1] == (
            'PIN required — locked manually')

    def test_attempts_beat_explicit_lock_in_reason(self):
        requires, reason = evaluate(_state(fail_count=5, force_pin_until_next_unlock=True))
        assert (requires, reason) == (
            True, 'PIN required — too many attempts')

    def test_explicit_lock_beats_stale_clock_in_reason(self):
        requires, reason = evaluate(
            _state(force_pin_until_next_unlock=True, last_pin_time=0.0, boot_pin_done=True))
        assert (requires, reason) == (True, 'PIN required — locked manually')

    def test_restart_beats_stale_clock_in_reason(self):
        requires, reason = evaluate(
            _state(boot_pin_done=False, last_pin_time=0.0))
        assert (requires, reason) == (
            True, 'PIN required — unlock with PIN after restart')

    def test_wifi_trust_counts(self):
        assert evaluate(_state(trusted_bt=[], trusted_ssid='Home')) == (
            False, '')


class TestClockJumps:
    def test_backwards_jump_requires_pin(self):
        """NTP/manual correction moves now before the last PIN unlock, so
        elapsed goes negative — trust must expire, not extend forever."""
        assert evaluate(_state(last_pin_time=NOW, now=NOW - 3600)) == (
            True, 'PIN required — PIN not entered for over 4 hours')

    def test_forward_jump_requires_pin(self):
        """A forward jump past a fresh unlock expires trust immediately."""
        assert evaluate(
            _state(last_pin_time=NOW - 60.0, now=NOW + 5 * 3600)) == (
            True, 'PIN required — PIN not entered for over 4 hours')


class TestSanitizers:
    def test_bt_upper_cases_and_dedupes(self):
        assert smart_lock.sanitize_bt_devices(
            ['aa:bb:cc:dd:ee:ff', 'AA:BB:CC:DD:EE:FF', '', 42, None]) == [BT]

    def test_bt_rejects_non_lists(self):
        assert smart_lock.sanitize_bt_devices('AA:BB:CC:DD:EE:FF') == []
        assert smart_lock.sanitize_bt_devices(None) == []

    def test_ssids_verbatim_and_case_sensitive(self):
        lower = {'ssid': 'home', 'bssid': BSSID.lower()}
        assert smart_lock.sanitize_ssids([HOME, lower, HOME]) == [
            HOME, {'ssid': 'home', 'bssid': BSSID}]

    def test_name_only_and_malformed_entries_dropped(self):
        """Old configs stored bare names: they disappear, nothing crashes."""
        assert smart_lock.sanitize_ssids([
            'Home', '', None, {'ssid': 'Home'}, {'bssid': BSSID},
            {'ssid': '', 'bssid': BSSID}, {'ssid': 'Home', 'bssid': 'nope'},
            {'ssid': 'Home', 'bssid': 7}, HOME]) == [HOME]

    def test_ssids_reject_non_lists(self):
        assert smart_lock.sanitize_ssids('Home') == []
        assert smart_lock.sanitize_ssids(None) == []


class TestLockLine:
    def test_unconfigured_shows_nothing(self):
        assert lock_line_for(_state(configured=False, trusted_bt=[])) == ''

    def test_trust_names_the_device(self):
        assert lock_line_for(_state()) == f'Kept unlocked by {BT}'

    def test_trust_names_the_network(self):
        assert lock_line_for(
            _state(trusted_bt=[], trusted_ssid='Home')) == (
            'Kept unlocked by Home')

    def test_device_preferred_over_network(self):
        assert lock_line_for(
            _state(trusted_bt=['Car Audio'],
                   trusted_ssid='Home')) == 'Kept unlocked by Car Audio'

    def test_pin_required_line_carries_reason(self):
        assert lock_line_for(_state(force_pin_until_next_unlock=True)) == (
            'PIN required — locked manually')

    def test_every_reason_starts_with_pin_required(self):
        for kwargs in ({'fail_count': 5}, {'force_pin_until_next_unlock': True},
                       {'boot_pin_done': False},
                       {'last_pin_time': 0.0},
                       {'trusted_bt': [], 'trusted_ssid': None}):
            line = lock_line_for(_state(**kwargs))
            assert line.startswith('PIN required'), kwargs


# ------------------------------------------------------------------
# Boot / 4h / lockout / explicit-lock gates through current_state
# ------------------------------------------------------------------

class TestPinGates:
    def test_boot_gate_lasts_until_first_pin_unlock(self):
        config = _StubConfig(bt=[BT])
        state = current_state(config, now=NOW)
        assert evaluate(state)[1].endswith('after restart')
        note_pin_unlock(config, now=NOW)
        state = current_state(config, now=NOW)
        assert state.boot_pin_done is True
        assert state.last_pin_time == NOW

    def test_pin_unlock_records_time_and_clears_explicit_lock(self):
        config = _StubConfig()
        note_explicit_lock()
        assert explicit_lock_pending() is True
        note_pin_unlock(config, now=NOW + 5)
        assert explicit_lock_pending() is False
        assert config.recorded == [NOW + 5]

    def test_explicit_lock_forces_pin_while_trusted(self):
        config = _StubConfig(bt=[BT])
        note_pin_unlock(config, now=NOW)
        mon, bus = _monitor(config)
        smart_lock._LIVE = mon
        bus.answer(0, _bluez_objects([('/d/0', BT, True, None)]))
        bus.answer(1, _nm_objects([]))
        assert evaluate(current_state(config, now=NOW)) == (False, '')
        note_explicit_lock()
        requires, reason = evaluate(current_state(config, now=NOW))
        assert (requires, reason) == (True, 'PIN required — locked manually')

    def test_is_configured_needs_a_non_empty_list(self):
        assert is_configured(_StubConfig()) is False
        assert is_configured(_StubConfig(bt=[BT])) is True
        assert is_configured(_StubConfig(ssids=[HOME])) is True

    def test_stale_clock_flows_through_current_state(self):
        config = _StubConfig(ssids=[HOME], last_pin=NOW - 5 * 3600)
        note_pin_unlock(config, now=NOW - 5 * 3600)
        mon, bus = _monitor(config)
        smart_lock._LIVE = mon
        bus.answer(0, _bluez_objects([]))
        bus.answer(1, _nm_objects([('/a/0', 'Home', '802-11-wireless', 2)]))
        requires, reason = evaluate(current_state(config, now=NOW))
        assert (requires, reason) == (
            True, 'PIN required — PIN not entered for over 4 hours')

    def test_fail_count_flows_through_current_state(self):
        config = _StubConfig(bt=[BT])
        note_pin_unlock(config, now=NOW)
        mon, bus = _monitor(config)
        smart_lock._LIVE = mon
        bus.answer(0, _bluez_objects([('/d/0', BT, True, None)]))
        bus.answer(1, _nm_objects([]))
        assert evaluate(current_state(config, now=NOW)) == (False, '')
        state = current_state(config, fail_count=5, now=NOW)
        assert evaluate(state)[1] == 'PIN required — too many attempts'
        state = current_state(config, lockout_active=True, now=NOW)
        assert evaluate(state)[1] == 'PIN required — too many attempts'


# ------------------------------------------------------------------
# Event-driven BlueZ trust
# ------------------------------------------------------------------

class TestBlueZTrust:
    def test_subscribes_and_enumerates_on_start(self):
        mon, bus = _monitor(_StubConfig(bt=[BT]))
        members = sorted(rec['member'] for rec in bus.subs.values())
        assert members == ['InterfacesAdded', 'InterfacesRemoved',
                           'PropertiesChanged', 'PropertiesChanged']
        senders = sorted(rec['sender'] for rec in bus.subs.values()
                         if rec['member'] == 'PropertiesChanged')
        assert senders == ['org.bluez', 'org.freedesktop.NetworkManager']
        assert [c['method'] for c in bus.calls] == [
            'GetManagedObjects', 'GetManagedObjects']
        assert bus.calls[0]['bus'] == 'org.bluez'
        mon.stop()

    def test_signal_handler_takes_seven_args(self):
        """The XXWM_RULES trap: user_data was passed (None), so Gio
        delivers (conn, sender, path, iface, signal, params, user_data)."""
        import inspect
        params = inspect.signature(
            SmartLockMonitor._on_bt_properties_changed).parameters
        assert list(params)[1:] == [
            '_conn', '_sender', '_path', '_iface', '_signal', 'params',
            '_user_data']

    def test_connected_trusted_device_grants_trust(self):
        notified = []
        mon, bus = _monitor(_StubConfig(bt=[BT]), notified=notified)
        bus.answer(0, _bluez_objects([('/d/0', BT, True, 'Car Audio')]))
        bus.answer(1, _nm_objects([]))
        assert mon.trusted_bt == ['Car Audio']
        assert notified != []

    def test_match_is_case_insensitive_and_falls_back_to_address(self):
        mon, bus = _monitor(_StubConfig(bt=[BT.lower()]))
        bus.answer(0, _bluez_objects([('/d/0', BT, True, None)]))
        bus.answer(1, _nm_objects([]))
        assert mon.trusted_bt == [BT]
        mon.stop()

    def test_untrusted_or_disconnected_devices_ignored(self):
        mon, bus = _monitor(_StubConfig(bt=[BT]))
        bus.answer(0, _bluez_objects([
            ('/d/0', 'FF:FF:FF:FF:FF:FF', True, 'Stranger'),
            ('/d/1', BT, False, 'Mine'),
        ]))
        bus.answer(1, _nm_objects([]))
        assert mon.trusted_bt == []
        mon.stop()

    def test_disconnect_event_clears_trust_without_locking(self):
        """Android behavior: losing trust never locks the phone now — the
        bus sees exactly one re-query and no lock/unlock path exists."""
        assert not hasattr(SmartLockMonitor, 'unlock')
        assert not hasattr(SmartLockMonitor, 'lock_now')
        notified = []
        mon, bus = _monitor(_StubConfig(bt=[BT]), notified=notified)
        bus.answer(0, _bluez_objects([('/d/0', BT, True, None)]))
        bus.answer(1, _nm_objects([]))
        assert mon.trusted_bt == [BT]
        calls_before = len(bus.calls)
        notified.clear()
        sid = bus.sub_id('PropertiesChanged', sender='org.bluez')
        bus.fire(sid, 'org.bluez', '/d/0',
                 'org.freedesktop.DBus.Properties', 'PropertiesChanged',
                 ('org.bluez.Device1', {'Connected': False}, []))
        assert len(bus.calls) == calls_before + 1
        assert bus.calls[-1]['method'] == 'GetManagedObjects'
        bus.answer(len(bus.calls) - 1,
                   _bluez_objects([('/d/0', BT, False, None)]))
        assert mon.trusted_bt == []
        assert notified != []

    def test_unrelated_property_changes_cause_no_requery(self):
        mon, bus = _monitor(_StubConfig(bt=[BT]))
        bus.answer(0, _bluez_objects([]))
        bus.answer(1, _nm_objects([]))
        calls_before = len(bus.calls)
        sid = bus.sub_id('PropertiesChanged', sender='org.bluez')
        bus.fire(sid, 'org.bluez', '/d/0',
                 'org.freedesktop.DBus.Properties', 'PropertiesChanged',
                 ('org.bluez.Adapter1', {'Powered': True}, []))
        bus.fire(sid, 'org.bluez', '/d/0',
                 'org.freedesktop.DBus.Properties', 'PropertiesChanged',
                 ('org.bluez.Device1', {'RSSI': -60}, []))
        assert len(bus.calls) == calls_before
        mon.stop()

    def test_membership_change_requeries(self):
        mon, bus = _monitor(_StubConfig(bt=[BT]))
        bus.answer(0, _bluez_objects([]))
        bus.answer(1, _nm_objects([]))
        calls_before = len(bus.calls)
        bus.fire(bus.sub_id('InterfacesAdded'), 'org.bluez', '/',
                 'org.freedesktop.DBus.ObjectManager', 'InterfacesAdded',
                 ('/d/9', {'org.bluez.Device1': {}}))
        assert len(bus.calls) == calls_before + 1
        mon.stop()

    def test_transport_failure_clears_trust(self):
        """Fail closed: a re-query that errors must drop the cached trust
        instead of granting PIN-less unlock with no device present."""
        notified = []
        mon, bus = _monitor(_StubConfig(bt=[BT]), notified=notified)
        bus.answer(0, _bluez_objects([('/d/0', BT, True, None)]))
        bus.answer(1, _nm_objects([]))
        assert mon.trusted_bt == [BT]
        notified.clear()
        sid = bus.sub_id('PropertiesChanged', sender='org.bluez')
        bus.fire(sid, 'org.bluez', '/d/0',
                 'org.freedesktop.DBus.Properties', 'PropertiesChanged',
                 ('org.bluez.Device1', {'Connected': False}, []))
        bus.fail(len(bus.calls) - 1)
        assert mon.trusted_bt == []
        assert notified != []
        mon.stop()

    def test_malformed_reply_ignored(self):
        mon, bus = _monitor(_StubConfig(bt=[BT]))
        bus.fail(0)
        bus.fail(1)
        assert mon.trusted_bt == []
        assert mon.trusted_ssid is None
        mon.stop()


# ------------------------------------------------------------------
# Event-driven NetworkManager trust
# ------------------------------------------------------------------

class TestNetworkManagerTrust:
    def test_activated_trusted_ssid_grants_trust(self):
        mon, bus = _monitor(_StubConfig(ssids=[HOME]))
        bus.answer(0, _bluez_objects([]))
        bus.answer(1, _nm_objects([('/a/0', 'Home', '802-11-wireless', 2)]))
        assert mon.trusted_ssid == 'Home'
        mon.stop()

    def test_activating_or_wired_or_unknown_ssid_ignored(self):
        mon, bus = _monitor(_StubConfig(ssids=[HOME]))
        bus.answer(0, _bluez_objects([]))
        bus.answer(1, _nm_objects([
            ('/a/0', 'Home', '802-11-wireless', 1),
            ('/a/1', 'Home', '802-11-wired', 2),
            ('/a/2', 'Cafe', '802-11-wireless', 2),
        ]))
        assert mon.trusted_ssid is None
        mon.stop()

    def test_nm_property_change_requeries(self):
        mon, bus = _monitor(_StubConfig(ssids=[HOME]))
        bus.answer(0, _bluez_objects([]))
        bus.answer(1, _nm_objects([]))
        assert mon.trusted_ssid is None
        calls_before = len(bus.calls)
        sid = bus.sub_id('PropertiesChanged',
                         sender='org.freedesktop.NetworkManager')
        bus.fire(sid, 'org.freedesktop.NetworkManager',
                 '/org/freedesktop/NetworkManager',
                 'org.freedesktop.DBus.Properties', 'PropertiesChanged',
                 ('org.freedesktop.NetworkManager',
                  {'ActiveConnections': []}, []))
        assert len(bus.calls) == calls_before + 1
        bus.answer(len(bus.calls) - 1,
                   _nm_objects([('/a/0', 'Home', '802-11-wireless', 2)]))
        assert mon.trusted_ssid == 'Home'
        mon.stop()

    def test_active_connection_state_change_requeries(self):
        mon, bus = _monitor(_StubConfig(ssids=[HOME]))
        bus.answer(0, _bluez_objects([]))
        bus.answer(1, _nm_objects([]))
        calls_before = len(bus.calls)
        sid = bus.sub_id('PropertiesChanged',
                         sender='org.freedesktop.NetworkManager')
        bus.fire(sid, 'org.freedesktop.NetworkManager', '/a/0',
                 'org.freedesktop.DBus.Properties', 'PropertiesChanged',
                 ('org.freedesktop.NetworkManager.Connection.Active',
                  {'State': 2}, []))
        assert len(bus.calls) == calls_before + 1
        mon.stop()

    def test_unrelated_nm_signal_ignored(self):
        mon, bus = _monitor(_StubConfig(ssids=[HOME]))
        bus.answer(0, _bluez_objects([]))
        bus.answer(1, _nm_objects([]))
        calls_before = len(bus.calls)
        sid = bus.sub_id('PropertiesChanged',
                         sender='org.freedesktop.NetworkManager')
        bus.fire(sid, 'org.freedesktop.NetworkManager', '/dev/0',
                 'org.freedesktop.DBus.Properties', 'PropertiesChanged',
                 ('org.freedesktop.NetworkManager.Device.Wireless',
                  {'LastScan': 42}, []))
        assert len(bus.calls) == calls_before
        mon.stop()

    def test_never_polls(self):
        """No timer source may exist: trust moves only on signals and
        explicit refresh() calls (initial enum + lock time)."""
        import ast
        tree = ast.parse(Path(smart_lock.__file__).read_text())
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                called.add(node.attr)
        assert not (called & {'timeout_add', 'timeout_add_seconds'})


# ------------------------------------------------------------------
# Lock-screen integration (unbound methods on a harness namespace)
# ------------------------------------------------------------------

class _FakeLabel:
    def __init__(self):
        self.text = ''
        self.visible = True

    def set_text(self, text):
        self.text = text

    def set_visible(self, value):
        self.visible = bool(value)

    def add_css_class(self, _name):
        pass

    def remove_css_class(self, _name):
        pass


class _FakeRevealer:
    def __init__(self):
        self.revealed = False

    def set_reveal_child(self, value):
        self.revealed = bool(value)


class _FakeVisibility:
    def __init__(self):
        self.visible = True
        self.sensitive = True

    def set_visible(self, value):
        self.visible = bool(value)

    def set_sensitive(self, value):
        self.sensitive = bool(value)


class _FakeNotifBox:
    def get_first_child(self):
        return None

    def set_visible(self, _value):
        pass


class _LockHarness:
    def __init__(self, config):
        self._config = config
        self._pin = ''
        self._fail_count = 0
        self._lockout_src = None
        self._open_shade_after_unlock = False
        self._pin_revealer = _FakeRevealer()
        self._swipe_hint = _FakeVisibility()
        self._notif_box = _FakeNotifBox()
        self._trust_label = _FakeLabel()
        self.dots_label = _FakeLabel()
        self.error_label = _FakeLabel()
        self.lockout_label = _FakeLabel()
        self._get_notifications = lambda: []
        self._dnd_active = lambda: False
        self.visible = True
        self.unlocked = 0
        self.key_size = 0
        self._key_size = 0
        self._key_widgets: list = []
        self._keypad_btns: list = []

    def _on_unlock(self):
        self.unlocked += 1

    def _unlock(self):
        _lock_screen().LockScreen._unlock(self)

    def _shake(self):
        _lock_screen().LockScreen._shake(self)

    def _smart_lock_requires_pin(self):
        return _lock_screen().LockScreen._smart_lock_requires_pin(self)

    def _refresh_trust_line(self):
        return _lock_screen().LockScreen._refresh_trust_line(self)

    def _apply_keypad_size(self, size):
        _lock_screen().LockScreen._apply_keypad_size(self, size)

    def _fit_keypad_to_screen(self):
        _lock_screen().LockScreen._fit_keypad_to_screen(self)

    def _persist_lockout_state(self, until):
        _lock_screen().LockScreen._persist_lockout_state(self, until)

    def _set_clock_compact(self, compact):
        _lock_screen().LockScreen._set_clock_compact(self, compact)

    def _refresh_dots(self):
        _lock_screen().LockScreen._refresh_dots(self)

    def _refresh_notifications(self):
        _lock_screen().LockScreen._refresh_notifications(self)

    def _on_open_shade(self):
        pass

    def set_visible(self, value):
        self.visible = bool(value)

    def get_width(self):
        return 360

    def get_height(self):
        return 720


def _lock_screen():
    import lock_screen
    return lock_screen


def _trust_world(config):
    """Live monitor behind current_state, BlueZ trust answered."""
    bus = FakeBus()
    smart_lock.ensure_monitor(config, bus=bus)
    return bus


class TestLockScreenTrust:
    @pytest.fixture(autouse=True)
    def _no_glib_side_effects(self, monkeypatch):
        ls = _lock_screen()
        monkeypatch.setattr(
            ls.GLib, 'timeout_add', lambda *a, **k: 1)
        monkeypatch.setattr(
            ls.GLib, 'idle_add', lambda cb, *a: cb(*a) or 1)

    def _answer_trusted(self, bus):
        bus.answer(0, _bluez_objects([('/d/0', BT, True, None)]))
        bus.answer(1, _nm_objects([]))

    def test_swipe_unlocks_without_keypad_when_trusted(self):
        ls = _lock_screen()
        config = _StubConfig(bt=[BT])
        note_pin_unlock(config)
        bus = _trust_world(config)
        self._answer_trusted(bus)
        lock = _LockHarness(config)
        ls.LockScreen._swipe_up(lock)
        assert lock.unlocked == 1
        assert lock._pin_revealer.revealed is False

    def test_trust_alone_never_unlocks_without_swipe(self):
        ls = _lock_screen()
        config = _StubConfig(bt=[BT])
        note_pin_unlock(config)
        bus = _trust_world(config)
        lock = _LockHarness(config)
        self._answer_trusted(bus)
        assert lock.unlocked == 0
        ls.LockScreen._refresh_trust_line(lock)
        assert lock.unlocked == 0
        assert lock._trust_label.text == f'Kept unlocked by {BT}'

    def test_swipe_reveals_keypad_when_untrusted(self):
        ls = _lock_screen()
        config = _StubConfig(bt=[BT])
        note_pin_unlock(config)
        bus = _trust_world(config)
        bus.answer(0, _bluez_objects([]))
        bus.answer(1, _nm_objects([]))
        lock = _LockHarness(config)
        lock._fit_keypad_to_screen = lambda: None
        ls.LockScreen._swipe_up(lock)
        assert lock.unlocked == 0
        assert lock._pin_revealer.revealed is True

    def test_disconnect_between_locks_restores_keypad(self):
        ls = _lock_screen()
        config = _StubConfig(bt=[BT])
        note_pin_unlock(config)
        bus = _trust_world(config)
        self._answer_trusted(bus)
        lock = _LockHarness(config)
        ls.LockScreen._swipe_up(lock)
        assert lock.unlocked == 1
        sid = bus.sub_id('PropertiesChanged', sender='org.bluez')
        bus.fire(sid, 'org.bluez', '/d/0',
                 'org.freedesktop.DBus.Properties', 'PropertiesChanged',
                 ('org.bluez.Device1', {'Connected': False}, []))
        bus.answer(len(bus.calls) - 1,
                   _bluez_objects([('/d/0', BT, False, None)]))
        lock.unlocked = 0
        lock._pin_revealer.revealed = False
        lock._fit_keypad_to_screen = lambda: None
        ls.LockScreen._swipe_up(lock)
        assert lock.unlocked == 0
        assert lock._pin_revealer.revealed is True

    def test_successful_pin_extends_trust_clock(self):
        ls = _lock_screen()
        config = _StubConfig(bt=[BT], last_pin=0.0)
        note_explicit_lock()
        lock = _LockHarness(config)
        lock._pin = '123456'
        ls.LockScreen._check_pin(lock)
        assert lock.unlocked == 1
        assert config.recorded != []
        assert explicit_lock_pending() is False

    def test_wrong_pin_still_demands_pin_while_trusted(self):
        ls = _lock_screen()
        config = _StubConfig(bt=[BT])
        note_pin_unlock(config)
        bus = _trust_world(config)
        self._answer_trusted(bus)
        lock = _LockHarness(config)
        lock._pin = '000000'
        ls.LockScreen._check_pin(lock)
        assert lock.unlocked == 0
        assert 'Incorrect PIN' in lock.error_label.text

    def test_trust_line_hidden_when_unconfigured(self):
        ls = _lock_screen()
        lock = _LockHarness(_StubConfig())
        ls.LockScreen._refresh_trust_line(lock)
        assert lock._trust_label.text == ''
        assert lock._trust_label.visible is False

    def test_trust_line_shows_pin_reason_when_configured(self):
        ls = _lock_screen()
        lock = _LockHarness(_StubConfig(bt=[BT]))
        ls.LockScreen._refresh_trust_line(lock)
        assert lock._trust_label.text.startswith('PIN required')
        assert lock._trust_label.visible is True

    def test_prepare_reevaluates_at_lock_time(self):
        ls = _lock_screen()
        config = _StubConfig(bt=[BT])
        bus = _trust_world(config)
        calls_before = len(bus.calls)
        lock = _LockHarness(config)
        lock._emg_digits = ''
        lock._emg_display = _FakeLabel()
        lock._emg_status = _FakeLabel()
        lock._emergency_revealer = _FakeRevealer()
        lock._medical_revealer = _FakeRevealer()
        lock._emergency_btn = _FakeVisibility()
        lock.clock_label = _FakeLabel()
        lock.date_label = _FakeVisibility()
        lock._lock_grid = types.SimpleNamespace(
            set_row_homogeneous=lambda v: None)
        lock._top_pane = types.SimpleNamespace(set_vexpand=lambda v: None)
        ls.LockScreen.prepare(lock)
        assert len(bus.calls) == calls_before + 2
        assert lock._trust_label.text.startswith('PIN required')


# ------------------------------------------------------------------
# Power menu explicit lock + config + backup
# ------------------------------------------------------------------

class TestExplicitLockPath:
    def test_power_menu_lock_sets_force_pin(self):
        import power_menu
        called = []
        ns = types.SimpleNamespace(
            _dismiss=lambda: called.append('dismiss'),
            _on_lock=lambda: called.append('lock'))
        power_menu.PowerMenu._lock(ns)
        assert called == ['dismiss', 'lock']
        assert explicit_lock_pending() is True

    def test_force_pin_cleared_only_by_pin_unlock(self):
        config = _StubConfig()
        note_explicit_lock()
        assert explicit_lock_pending() is True
        note_pin_unlock(config)
        assert explicit_lock_pending() is False


class TestSmartLockConfig:
    @pytest.fixture(autouse=True)
    def _isolated_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))

    def _config(self):
        from config import ShellConfig
        return ShellConfig()

    def test_defaults_empty_and_opt_in(self):
        from config import DEFAULT_CONFIG
        assert DEFAULT_CONFIG['smart_lock_bt_devices'] == []
        assert DEFAULT_CONFIG['smart_lock_wifi_ssids'] == []
        config = self._config()
        assert config.smart_lock_bt_devices == []
        assert config.smart_lock_wifi_ssids == []
        assert config.smart_lock_last_pin_time == 0.0
        assert is_configured(config) is False

    def test_setters_round_trip_and_normalize(self):
        config = self._config()
        config.set_smart_lock_bt_devices(['aa:bb:cc:dd:ee:ff'])
        config.set_smart_lock_wifi_ssids(
            [{'ssid': 'Home', 'bssid': BSSID.lower()}])
        assert config.smart_lock_bt_devices == [BT]
        assert config.smart_lock_wifi_ssids == [HOME]
        assert is_configured(config) is True
        reread = self._config()
        assert reread.smart_lock_bt_devices == [BT]
        assert reread.smart_lock_wifi_ssids == [HOME]

    def test_old_name_only_config_loads_unconfigured(self):
        import json
        config = self._config()
        config.config_dir.mkdir(parents=True, exist_ok=True)
        config.config_path.write_text(json.dumps(
            {'smart_lock_wifi_ssids': ['Home', 'Work']}), encoding='utf-8')
        old = self._config()
        assert old.smart_lock_wifi_ssids == []
        assert is_configured(old) is False

    def test_corrupt_values_fail_soft(self):
        config = self._config()
        config.data['smart_lock_bt_devices'] = 'not-a-list'
        config.data['smart_lock_last_pin_time'] = 'soon'
        assert config.smart_lock_bt_devices == []
        assert config.smart_lock_last_pin_time == 0.0

    def test_pin_clock_persists(self):
        config = self._config()
        config.record_smart_lock_pin_time(NOW)
        assert self._config().smart_lock_last_pin_time == NOW

    def test_backup_carries_no_trust_and_no_clock(self):
        """A backup file can come from anywhere: it never grants trust."""
        import backup
        config = self._config()
        config.set_smart_lock_bt_devices([BT])
        config.set_smart_lock_wifi_ssids([HOME])
        config.record_smart_lock_pin_time(NOW)
        exported = backup.export_backup(config)
        assert not any(key.startswith('smart_lock') for key in exported)
        ok, error = backup.validate_backup(exported)
        assert ok, error
        fresh = self._config()
        fresh.set_smart_lock_bt_devices([])
        fresh.set_smart_lock_wifi_ssids([])
        fresh.record_smart_lock_pin_time(123.0)
        exported['smart_lock_bt_devices'] = ['11:11:11:11:11:11']
        exported['smart_lock_wifi_ssids'] = [
            {'ssid': 'Evil', 'bssid': '11:11:11:11:11:11'}]
        exported['smart_lock_last_pin_time'] = 999.0
        assert backup.restore_backup(fresh, exported) is True
        assert fresh.smart_lock_bt_devices == []
        assert fresh.smart_lock_wifi_ssids == []
        assert fresh.smart_lock_last_pin_time == 123.0
