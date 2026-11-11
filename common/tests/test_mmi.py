"""USSD/MMI detector and the async USSD session (xx_phone.mmi).

The session runs against a fake bus that records every async call and
lets the test deliver replies and PropertiesChanged signals in any order
(the real ModemManager order between Initiate's reply and the State
update is undefined).
"""
import pytest

from xx_phone import mmi

MODEM = '/org/freedesktop/ModemManager1/Modem/0'


# ------------------------------------------------------------- detector

@pytest.mark.parametrize('raw,kind', [
    ('*#06#', 'imei'),
    (' *#06# ', 'imei'),
    ('*100#', 'ussd'),
    ('*123*1#', 'ussd'),
    ('#100#', 'ussd'),
    ('##002#', 'ussd'),
    ('**21*5550100#', 'ussd'),     # call forwarding MMI → Initiate too
    ('*#21#', 'ussd'),
    ('*#*#4636#*#*', 'call'),      # Android secret code: not USSD-shaped
    ('#31#5550100', 'call'),       # caller-ID prefix, a real call
    ('*21*+15550100#', 'call'),    # '+' is not USSD (GNOME Calls rule)
    ('*100', 'call'),
    ('100#', 'call'),
    ('*', 'call'),
    ('#', 'call'),
    ('*#', 'call'),
    ('##', 'call'),
    ('*1#2#', 'call'),
    ('+15550100', 'call'),
    ('5550100', 'call'),
    ('', 'call'),
    (None, 'call'),
    ('*١٠٠#', 'call'),             # non-ASCII digits never pass
])
def test_classify_table(raw, kind):
    assert mmi.classify(raw) == kind


def test_is_ussd_matches_gnome_calls_shape():
    assert mmi.is_ussd('*#06#')
    assert not mmi.is_ussd('*#06# ')  # callers normalize first
    assert not mmi.is_ussd(123)


def test_imeis_from_objects_prefers_3gpp_imei():
    objects = {
        MODEM: {
            mmi.MODEM_IFACE: {'EquipmentIdentifier': '111'},
            mmi.MODEM3GPP_IFACE: {'Imei': '359000000000001'},
        },
        '/org/freedesktop/ModemManager1/Modem/1': {
            mmi.MODEM_IFACE: {'EquipmentIdentifier': '359000000000002'},
        },
        '/other': {'x': {}},
    }
    assert mmi.imeis_from_objects(objects) == [
        '359000000000001', '359000000000002']
    assert mmi.imei_text(['1']) == '1'
    assert 'IMEI 2: b' in mmi.imei_text(['a', 'b'])
    assert 'No IMEI' in mmi.imei_text([])


def test_plain_error_strips_dbus_names():
    class Err(Exception):
        message = ('GDBus.Error:org.freedesktop.ModemManager1.Error.Core.'
                   'WrongState: ussd session already active')
    assert mmi.plain_error(Err()) == 'Ussd session already active'
    assert mmi.plain_error(Exception('')) == 'The modem reported an error.'


# ------------------------------------------------------------- fake bus

class Variant:
    def __init__(self, fmt, value):
        self.fmt, self.value = fmt, value


class FakeGLib:
    Variant = Variant


class Reply:
    def __init__(self, value):
        self._value = value

    def unpack(self):
        return self._value


class Params:
    def __init__(self, value):
        self._value = value

    def unpack(self):
        return self._value


class FakeBus:
    def __init__(self):
        self.pending = []
        self.log = []
        self.subs = {}
        self.unsubscribed = []
        self._next = 1

    def call(self, bus_name, path, iface, method, params, reply_type,
             flags, timeout, cancellable, callback):
        assert reply_type is None
        args = params.value if params is not None else None
        self.log.append((path, iface, method, args))
        self.pending.append((method, args, callback))

    def call_finish(self, result):
        if isinstance(result, Exception):
            raise result
        return result

    def signal_subscribe(self, sender, iface, member, path, arg0, flags,
                         callback, user_data):
        sid = self._next
        self._next += 1
        self.subs[sid] = (iface, member, path, arg0, callback)
        return sid

    def signal_unsubscribe(self, sid):
        self.unsubscribed.append(sid)
        self.subs.pop(sid, None)

    def methods(self):
        return [entry[2] for entry in self.log]

    def complete(self, method, value, args=None):
        for index, (name, call_args, callback) in enumerate(self.pending):
            if name == method and (args is None or call_args == args):
                del self.pending[index]
                callback(self, value if isinstance(value, Exception)
                         else Reply(value))
                return
        raise AssertionError('no pending %s in %r' % (method, self.pending))

    def emit_props(self, changed):
        for iface, member, path, arg0, callback in list(self.subs.values()):
            if member == 'PropertiesChanged':
                callback(self, ':1.1', path, 'org.freedesktop.DBus.Properties',
                         member, Params((mmi.USSD_IFACE, changed, [])), None)


def _session(modem_path=MODEM):
    bus = FakeBus()
    changes = []
    session = mmi.UssdSession(bus, FakeGLib, modem_path=modem_path,
                              on_change=lambda s: changes.append(s.state))
    return bus, session, changes


def _get_state_args():
    return (mmi.USSD_IFACE, 'State')


# --------------------------------------------------------------- session

def test_initiate_reply_then_session_ends():
    bus, session, _ = _session()
    session.start('*100#')
    assert session.busy
    assert bus.methods() == ['Get']
    sub = next(iter(bus.subs.values()))
    assert sub[:4] == ('org.freedesktop.DBus.Properties', 'PropertiesChanged',
                       MODEM, mmi.USSD_IFACE)
    bus.complete('Get', (mmi.STATE_IDLE,))
    assert bus.log[-1] == (MODEM, mmi.USSD_IFACE, 'Initiate', ('*100#',))
    bus.complete('Initiate', ('Balance: 5.00',))
    assert session.messages == ['Balance: 5.00']
    assert not session.busy
    bus.complete('Get', (mmi.STATE_IDLE,), _get_state_args())
    assert session.state == mmi.STATE_IDLE
    assert not session.awaiting_reply
    assert mmi.state_text(session) == 'Session ended.'
    # The property echo of the same text is deduped.
    bus.emit_props({'NetworkNotification': 'Balance: 5.00'})
    assert session.messages == ['Balance: 5.00']


def test_stale_session_is_cancelled_before_initiate():
    bus, session, _ = _session()
    session.start('*100#')
    bus.complete('Get', (mmi.STATE_USER_RESPONSE,))
    assert bus.methods()[-1] == 'Cancel'
    assert 'Initiate' not in bus.methods()
    bus.complete('Cancel', ())
    assert bus.methods()[-1] == 'Initiate'


def test_menu_respond_and_dedupe():
    bus, session, _ = _session()
    session.start('*123#')
    bus.complete('Get', (mmi.STATE_IDLE,))
    # State update and NetworkRequest arrive before the Initiate reply.
    bus.emit_props({'State': mmi.STATE_USER_RESPONSE,
                    'NetworkRequest': '1. Balance\n2. Data'})
    assert session.messages == ['1. Balance\n2. Data']
    bus.complete('Initiate', ('1. Balance\n2. Data',))
    assert session.messages == ['1. Balance\n2. Data']
    bus.complete('Get', (mmi.STATE_USER_RESPONSE,), _get_state_args())
    assert session.awaiting_reply
    assert mmi.state_text(session) == 'The network is waiting for your reply.'

    session.respond('  1 ')
    assert bus.log[-1] == (MODEM, mmi.USSD_IFACE, 'Respond', ('1',))
    assert not session.awaiting_reply
    session.respond('2')  # ignored while in flight
    assert bus.methods().count('Respond') == 1
    bus.emit_props({'State': mmi.STATE_IDLE,
                    'NetworkNotification': 'Balance 5.00'})
    bus.complete('Respond', ('Balance 5.00',))
    assert session.messages == ['1. Balance\n2. Data', 'Balance 5.00']


def test_same_menu_again_after_a_new_reply_is_shown():
    bus, session, _ = _session()
    session.start('*123#')
    bus.complete('Get', (mmi.STATE_IDLE,))
    bus.complete('Initiate', ('Menu',))
    bus.complete('Get', (mmi.STATE_USER_RESPONSE,), _get_state_args())
    session.respond('9')
    bus.complete('Respond', ('Menu',))
    assert session.messages == ['Menu', 'Menu']


def test_user_response_without_request_reads_network_request():
    bus, session, _ = _session()
    session.start('*123#')
    bus.complete('Get', (mmi.STATE_IDLE,))
    bus.emit_props({'State': mmi.STATE_USER_RESPONSE})
    bus.complete('Get', ('Pick one',), (mmi.USSD_IFACE, 'NetworkRequest'))
    assert session.messages == ['Pick one']


def test_notification_ignored_while_waiting_for_user():
    bus, session, _ = _session()
    session.start('*1#')
    bus.complete('Get', (mmi.STATE_IDLE,))
    bus.emit_props({'State': mmi.STATE_USER_RESPONSE, 'NetworkRequest': 'Q',
                    'NetworkNotification': 'old'})
    assert session.messages == ['Q']


def test_resolves_a_ussd_modem_when_none_given():
    bus, session, _ = _session(modem_path=None)
    session.start('*100#')
    assert bus.methods() == ['GetManagedObjects']
    bus.complete('GetManagedObjects', ({
        '/org/freedesktop/ModemManager1/Modem/3': {mmi.MODEM_IFACE: {}},
        '/org/freedesktop/ModemManager1/Modem/4': {
            mmi.MODEM_IFACE: {}, mmi.USSD_IFACE: {}},
    },))
    assert session.modem_path == '/org/freedesktop/ModemManager1/Modem/4'
    assert bus.methods()[-1] == 'Get'


def test_no_ussd_modem_is_a_plain_error():
    bus, session, _ = _session(modem_path=None)
    session.start('*100#')
    bus.complete('GetManagedObjects', ({MODEM: {mmi.MODEM_IFACE: {}}},))
    assert session.error == 'This modem cannot send USSD codes right now.'
    assert not session.busy
    bus2, session2, _ = _session(modem_path=None)
    session2.start('*100#')
    bus2.complete('GetManagedObjects', ({},))
    assert session2.error == 'No modem found.'


def test_initiate_error_is_plain_text():
    bus, session, _ = _session()
    session.start('*100#')
    bus.complete('Get', (mmi.STATE_IDLE,))

    class Err(Exception):
        message = ('GDBus.Error:org.freedesktop.ModemManager1.Error.Core.'
                   'Failed: network timeout')
    bus.complete('Initiate', Err())
    assert session.error == 'Network timeout'
    assert not session.busy and session.messages == []
    assert mmi.state_text(session) == ''


def test_no_bus_fails_soft():
    session = mmi.UssdSession(None, FakeGLib, modem_path=MODEM)
    session.start('*100#')
    assert 'system bus' in session.error


def test_cancel_ends_session_and_ignores_late_replies():
    bus, session, _ = _session()
    session.start('*100#')
    bus.complete('Get', (mmi.STATE_IDLE,))
    session.cancel()
    assert bus.methods()[-1] == 'Cancel'
    assert session.closed and bus.unsubscribed
    bus.complete('Initiate', ('late',))
    assert session.messages == []


def test_close_on_idle_session_sends_no_cancel():
    bus, session, _ = _session()
    session.start('*100#')
    bus.complete('Get', (mmi.STATE_IDLE,))
    bus.complete('Initiate', ('done',))
    bus.complete('Get', (mmi.STATE_IDLE,), _get_state_args())
    session.cancel()
    assert 'Cancel' not in bus.methods()


def test_fetch_imei_one_async_call():
    bus = FakeBus()
    got = []
    mmi.fetch_imei(bus, lambda imeis, err: got.append((imeis, err)))
    assert bus.methods() == ['GetManagedObjects']
    bus.complete('GetManagedObjects', ({
        MODEM: {mmi.MODEM_IFACE: {'EquipmentIdentifier': '359'}}},))
    assert got == [(['359'], '')]
    got.clear()
    mmi.fetch_imei(None, lambda imeis, err: got.append((imeis, err)))
    assert got[0][0] == [] and got[0][1]


def test_dialog_module_imports_without_touching_gtk():
    import importlib
    import sys

    sys.modules.pop('xx_phone.ussd_dialog', None)
    module = importlib.import_module('xx_phone.ussd_dialog')
    assert callable(module.open_ussd_dialog)
    assert callable(module.open_imei_dialog)
    source = open(module.__file__, encoding='utf-8').read()
    assert 'call_sync' not in source and 'bus_get_sync' not in source
