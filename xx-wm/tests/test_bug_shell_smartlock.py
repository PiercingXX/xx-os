"""Lane bug_shell finding 8: Smart Lock must fail closed.

When a BlueZ / NetworkManager GetManagedObjects query fails, the monitor
must drop the cached trust instead of keeping stale "trusted device"
state that later grants a PIN-less unlock.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import smart_lock
from smart_lock import SmartLockMonitor

BT = 'AA:BB:CC:DD:EE:FF'


@pytest.fixture(autouse=True)
def _isolate_modules():
    """Leave no imported shell module behind: sibling files import window
    and friends against their own gi seams (test_folders idiom)."""
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        sys.modules.pop(name, None)


@pytest.fixture(autouse=True)
def _fresh_runtime():
    smart_lock._reset_runtime_state()
    smart_lock._reset_live_monitor()
    yield
    smart_lock._reset_runtime_state()
    smart_lock._reset_live_monitor()


class _FakeVariant:
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
    def __init__(self):
        self.calls: list = []
        self.subs: dict = {}
        self._next_id = 1

    def signal_subscribe(self, sender, iface, member, path, arg0,
                         flags, callback, user_data):
        sid = self._next_id
        self._next_id += 1
        self.subs[sid] = {'sender': sender, 'iface': iface, 'member': member,
                          'path': path, 'callback': callback,
                          'user_data': user_data}
        return sid

    def signal_unsubscribe(self, sid):
        self.subs.pop(sid, None)

    def call(self, bus_name, obj_path, iface, method, params, reply_type,
             flags, timeout, cancellable, callback):
        self.calls.append({'bus': bus_name, 'path': obj_path,
                           'iface': iface, 'method': method,
                           'callback': callback})

    def call_finish(self, result):
        if result._error is not None:
            raise result._error
        return _FakeVariant(result._payload)

    def answer(self, index, payload):
        rec = self.calls[index]
        rec['callback'](self, _FakeResult(payload=payload))

    def fail(self, index):
        rec = self.calls[index]
        rec['callback'](self, _FakeResult(error=Exception('transport down')))

    def sub_id(self, member, sender=None):
        for sid, rec in self.subs.items():
            if rec['member'] == member and (
                    sender is None or rec['sender'] == sender):
                return sid
        raise AssertionError(f'no subscription for {member}')

    def fire(self, sid, *args):
        self.subs[sid]['callback'](self, *args)


class _StubConfig:
    def __init__(self, bt=(), ssids=()):
        self.smart_lock_bt_devices = list(bt)
        self.smart_lock_wifi_ssids = list(ssids)


def _bluez_objects(devices):
    objects = {}
    for path, address, connected, alias in devices:
        props = {'Address': address, 'Connected': connected,
                 'Paired': True, 'Bonded': True,
                 'ServicesResolved': connected}
        if alias is not None:
            props['Alias'] = alias
        objects[path] = {'org.bluez.Device1': props,
                         'org.bluez.MediaControl1': {'Connected': connected}}
    return objects


BSSID = '11:22:33:44:55:66'


def _nm_objects(connections):
    objects = {}
    for path, ssid, conn_type, state in connections:
        objects[path] = {'org.freedesktop.NetworkManager.Connection.Active': {
            'Id': ssid, 'Type': conn_type, 'State': state,
            'Devices': ['/dev/w'], 'SpecificObject': '/ap/0'}}
        objects['/dev/w'] = {
            'org.freedesktop.NetworkManager.Device.Wireless': {
                'ActiveAccessPoint': '/ap/0'}}
        objects['/ap/0'] = {'org.freedesktop.NetworkManager.AccessPoint': {
            'Ssid': list(ssid.encode()), 'HwAddress': BSSID,
            'RsnFlags': 0x100}}
    return objects


def _monitor(config, notified=None):
    bus = FakeBus()
    mon = SmartLockMonitor(config, bus=bus)
    if notified is not None:
        mon._listeners.append(lambda: notified.append('changed'))
    return mon, bus


class TestFailureClearsTrust:
    def test_bluez_failure_drops_cached_device(self):
        notified: list = []
        mon, bus = _monitor(_StubConfig(bt=[BT]), notified)
        bus.answer(0, _bluez_objects([('/d/0', BT, True, None)]))
        bus.answer(1, _nm_objects([]))
        assert mon.trusted_bt == [BT]
        notified.clear()
        sid = bus.sub_id('PropertiesChanged', sender='org.bluez')
        bus.fire(sid, 'org.bluez', '/d/0',
                 'org.freedesktop.DBus.Properties', 'PropertiesChanged',
                 _FakeParams(('org.bluez.Device1', {'Connected': False}, [])),
                 None)
        bus.fail(len(bus.calls) - 1)
        assert mon.trusted_bt == [], \
            'a failed re-query must not keep stale trusted-device state'
        assert notified != []

    def test_nm_failure_drops_cached_ssid(self):
        notified: list = []
        mon, bus = _monitor(_StubConfig(ssids=[{'ssid': 'Home', 'bssid': BSSID}]), notified)
        bus.answer(0, _bluez_objects([]))
        bus.answer(1, _nm_objects([('/a/0', 'Home', '802-11-wireless', 2)]))
        assert mon.trusted_ssid == 'Home'
        notified.clear()
        sid = bus.sub_id('PropertiesChanged',
                         sender='org.freedesktop.NetworkManager')
        bus.fire(sid, 'org.freedesktop.NetworkManager', '/a/0',
                 'org.freedesktop.DBus.Properties', 'PropertiesChanged',
                 _FakeParams(('org.freedesktop.NetworkManager', {}, [])),
                 None)
        bus.fail(len(bus.calls) - 1)
        assert mon.trusted_ssid is None, \
            'a failed re-query must not keep stale trusted-network state'
        assert notified != []


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
