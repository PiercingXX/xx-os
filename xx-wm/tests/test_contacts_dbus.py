"""ContactBook xx-contacts D-Bus migration (wm_contacts1).

The shell's ContactBook now reads primarily from the xx-contacts session
service (List/Get into an in-memory cache, Changed signals for live
updates, async LookupNumber for caller ID) and keeps the local JSON/VCF
readers as the offline fallback. All bus work is async; sync search and
lookup read memory only.

Fake Gio objects below mirror the real signatures exactly: replies are
tuples (``reply.unpack()[0]`` is the first out-arg), async ``call``
callbacks take exactly ``(conn, result)`` with no user_data, and the
Changed handler takes ``(conn, sender, path, iface, signal, params)``.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import contacts

SRC = Path(__file__).parent.parent / 'launcher' / 'src'

XX_CONTACTS = next(
    (p for p in (Path(__file__).parent.parent / 'ref' / 'xx-contacts',
                 Path(__file__).parent.parent.parent / 'xx-contacts')
     if (p / 'src' / 'xx_contacts' / 'service.py').exists()),
    Path(__file__).parent.parent / 'ref' / 'xx-contacts')

ADA_VCARD = ('BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Ada Lovelace\r\n'
             'TEL;TYPE=CELL:+15550100\r\nX-STARRED:1\r\nEND:VCARD\r\n')
BOB_VCARD = ('BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Bob Jones\r\n'
             'TEL;TYPE=HOME:+15550101\r\nEND:VCARD\r\n')
CARA_VCARD = ('BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Cara Cole\r\n'
              'TEL;TYPE=CELL:+15550200\r\nEND:VCARD\r\n')


def _local_book(monkeypatch, tmp_path, entries=()):
    path = tmp_path / 'contacts.json'
    path.write_text(json.dumps(
        [{'name': name, 'numbers': numbers}
         for name, numbers in entries]), encoding='utf-8')
    monkeypatch.setattr(contacts, '_LOCAL_JSON', path)
    monkeypatch.setattr(contacts, '_VCF_SEARCH_PATHS', [])
    monkeypatch.setattr(contacts, '_gi', lambda: (None, None))
    return contacts.ContactBook()


class TestServiceShapes:
    def test_constants_match_xx_contacts_xml(self):
        service_py = (XX_CONTACTS / 'src' / 'xx_contacts' / 'service.py')
        if not service_py.exists():
            pytest.skip('xx-contacts source not found')
        xml = service_py.read_text(encoding='utf-8')
        assert contacts.CONTACTS_BUS in xml
        assert contacts.CONTACTS_PATH in xml
        assert contacts.CONTACTS_IFACE in xml
        assert contacts.CONTACTS_BUS == 'io.piercingxx.Contacts'
        assert contacts.CONTACTS_PATH == '/io/piercingxx/Contacts'
        assert contacts.CONTACTS_IFACE == 'io.piercingxx.Contacts1'
        for method, sigs in (
                ('LookupNumber', ['type="s" name="number"',
                                  'type="s" name="uid"',
                                  'type="s" name="display_name"',
                                  'type="as" name="tags"']),
                ('Search', ['type="s" name="query"', 'type="u" name="limit"',
                             'type="a(sss)"']),
                ('Get', ['type="s" name="uid"', 'type="s" name="vcard"']),
                ('List', ['type="t" name="since_revision"',
                           'type="t" name="revision"',
                           'type="as" name="changed_uids"',
                           'type="as" name="deleted_uids"'])):
            block = re.search(
                r'<method name="%s">(.*?)</method>' % method, xml, re.S)
            assert block is not None, method
            for sig in sigs:
                assert sig in block.group(1), (method, sig)
        assert '<signal name="Changed">' in xml

    def test_dbus_calls_are_async_with_tuple_replies(self):
        text = (SRC / 'contacts.py').read_text(encoding='utf-8')
        assert 'GLib.VariantType.new(CONTACTS_LIST_REPLY)' in text
        assert 'GLib.VariantType.new(CONTACTS_GET_REPLY)' in text
        assert 'GLib.VariantType.new(CONTACTS_LOOKUP_REPLY)' in text
        assert 'call_sync(' not in text
        assert 'revision, changed, deleted = reply.unpack()' in text
        assert '_uid, name, _tags = reply.unpack()' in text
        assert '(vcard,) = reply.unpack()' in text
        assert "'Changed'" in text
        assert 'CONTACTS_BUS, CONTACTS_IFACE' in text


class TestServiceVcardParsing:
    def test_name_numbers_and_starred(self):
        contact = contacts.contact_from_service_vcard('u1', ADA_VCARD)
        assert contact is not None
        assert contact.name == 'Ada Lovelace'
        assert contact.numbers == ['+15550100']
        assert contact.uid == 'u1'
        assert contact.starred is True

    def test_unstarred(self):
        contact = contacts.contact_from_service_vcard('u2', BOB_VCARD)
        assert contact is not None
        assert contact.starred is False

    def test_escapes_and_folding(self):
        vcard = ('BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Ann\\; Lee\\, Jr\r\n'
                 'TEL;TYPE=CELL:+1 555 0100\r\nEND:VCARD\r\n')
        contact = contacts.contact_from_service_vcard('u9', vcard)
        assert contact is not None
        assert contact.name == 'Ann; Lee, Jr'

    def test_numberless_card_is_dropped(self):
        vcard = 'BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Ghost\r\nEND:VCARD\r\n'
        assert contacts.contact_from_service_vcard('u0', vcard) is None
        assert contacts.contact_from_service_vcard('u0', '') is None


class TestMergeStarredNumbers:
    def test_union_sorted_digits_only(self):
        assert contacts.merge_starred_numbers(
            ['+1 (999)', '', '15550100'], ['+15550100', '+15550200']) == [
            '15550100', '15550200', '1999']

    def test_existing_entries_never_drop(self):
        assert contacts.merge_starred_numbers(['123'], []) == ['123']
        assert contacts.merge_starred_numbers([], []) == []


class TestLocalFallback:
    def test_lookup_search_without_service(self, monkeypatch, tmp_path):
        book = _local_book(monkeypatch, tmp_path, [
            ('Ada Lovelace', ['+15550100']),
            ('Bob Jones', ['+15550101'])])
        assert book.dbus_ready is False
        assert book.lookup_number('+1 (555) 0100') == 'Ada Lovelace'
        assert book.lookup_number('5559999') is None
        assert [c.name for c in book.search('ada')] == ['Ada Lovelace']
        assert [c.name for c in book.search('0101')] == ['Bob Jones']
        assert book.search('') == []
        assert len(book.all) == 2
        assert book.starred_numbers() == []

    def test_empty_home_without_service(self, monkeypatch, tmp_path):
        book = _local_book(monkeypatch, tmp_path)
        assert book.lookup_number('5550100') is None
        assert book.search('ada') == []
        assert book.all == []

    def test_async_lookup_falls_back_without_gi(self, monkeypatch, tmp_path):
        book = _local_book(monkeypatch, tmp_path,
                           [('Ada Lovelace', ['+15550100'])])
        seen = []
        book.lookup_number_async('5550100', seen.append)
        assert seen == ['Ada Lovelace']
        book.lookup_number_async('5559999', seen.append)
        assert seen == ['Ada Lovelace', None]


class _FakeVariant:
    def __init__(self, fmt, value):
        self.fmt = fmt
        self.value = value


class _FakeVariantType:
    @staticmethod
    def new(sig):
        return sig


class _FakeReply:
    def __init__(self, out_args):
        self._out_args = out_args

    def unpack(self):
        return self._out_args


class _FakeParams:
    def __init__(self, value):
        self._value = value

    def unpack(self):
        return self._value


class _FakeConn:
    """Scripted async connection: call() answers from canned replies."""

    def __init__(self, script):
        self.script = script
        self.calls = []
        self.subscriptions = []
        self.unsubscribed = []

    def call(self, bus, path, iface, method, params, reply_type, flags,
             timeout, cancellable, callback):
        assert cancellable is None
        assert isinstance(reply_type, str)
        self.calls.append((bus, path, iface, method, params, reply_type,
                           timeout))
        answer = self.script[method].pop(0)
        if isinstance(answer, Exception):
            raise answer
        callback(self, answer)

    def call_finish(self, result):
        if isinstance(result, Exception):
            raise result
        return result

    def signal_subscribe(self, sender, iface, member, path, arg0, flags,
                         callback):
        self.subscriptions.append(
            (sender, iface, member, path, callback))
        return 42

    def signal_unsubscribe(self, sub_id):
        self.unsubscribed.append(sub_id)


class _FakeGio:
    BusType = SimpleNamespace(SESSION=2)
    DBusCallFlags = SimpleNamespace(NONE=0)
    DBusSignalFlags = SimpleNamespace(NONE=0)

    def __init__(self, conn=None, bus_error=None):
        self._conn = conn
        self._bus_error = bus_error

    def bus_get(self, _bus_type, _cancellable, callback):
        callback(None, 'bus-result')

    def bus_get_finish(self, _result):
        if self._bus_error is not None:
            raise self._bus_error
        return self._conn


class _FakeGLib:
    Variant = _FakeVariant
    VariantType = _FakeVariantType


def _scripted_gio(monkeypatch, script, bus_error=None):
    conn = _FakeConn(script)
    gio = _FakeGio(conn, bus_error)
    monkeypatch.setattr(contacts, '_gi', lambda: (gio, _FakeGLib))
    return gio, conn


def _seed_local(monkeypatch, tmp_path):
    path = tmp_path / 'contacts.json'
    path.write_text(json.dumps(
        [{'name': 'Local Friend', 'numbers': ['+15559999']}]),
        encoding='utf-8')
    monkeypatch.setattr(contacts, '_LOCAL_JSON', path)
    monkeypatch.setattr(contacts, '_VCF_SEARCH_PATHS', [])


def _dbus_book(monkeypatch, tmp_path, script, bus_error=None):
    _seed_local(monkeypatch, tmp_path)
    _scripted_gio(monkeypatch, script, bus_error)
    return contacts.ContactBook()


class TestDbusCache:
    def test_list_get_fill_cache(self, monkeypatch, tmp_path):
        book = _dbus_book(monkeypatch, tmp_path, {
            'List': [_FakeReply((2, ['u1', 'u2'], []))],
            'Get': [_FakeReply((ADA_VCARD,)),
                    _FakeReply((BOB_VCARD,))],
        })
        assert book.dbus_ready is True
        assert book.lookup_number('5550100') == 'Ada Lovelace'
        assert book.lookup_number('+15550101') == 'Bob Jones'
        assert book.lookup_number('5559999') == 'Local Friend'
        assert [c.name for c in book.search('ada')] == ['Ada Lovelace']
        assert [c.name for c in book.search('1555', limit=4)] == [
            'Ada Lovelace', 'Bob Jones', 'Local Friend']
        assert book.starred_numbers() == ['15550100']
        assert {c.name for c in book.all} == {
            'Ada Lovelace', 'Bob Jones', 'Local Friend'}

    def test_changed_signal_refreshes_incrementally(
            self, monkeypatch, tmp_path):
        _, conn = _scripted_gio(monkeypatch, {
            'List': [_FakeReply((2, ['u1', 'u2'], [])),
                     _FakeReply((5, ['u2', 'u3'], ['u1']))],
            'Get': [_FakeReply((ADA_VCARD,)),
                    _FakeReply((BOB_VCARD,)),
                    _FakeReply((BOB_VCARD,)),
                    _FakeReply((CARA_VCARD,))],
        })
        _seed_local(monkeypatch, tmp_path)
        book = contacts.ContactBook()
        assert book.lookup_number('5550100') == 'Ada Lovelace'
        _sender, _iface, member, _path, handler = conn.subscriptions[0]
        assert member == 'Changed'
        assert _sender == 'io.piercingxx.Contacts'
        lists_before = len([c for c in conn.calls if c[3] == 'List'])
        handler(conn, 'io.piercingxx.Contacts', '/io/piercingxx/Contacts',
                'io.piercingxx.Contacts1', 'Changed',
                _FakeParams((2, ['u9'])))
        assert len([c for c in conn.calls if c[3] == 'List']) == lists_before
        handler(conn, 'io.piercingxx.Contacts', '/io/piercingxx/Contacts',
                'io.piercingxx.Contacts1', 'Changed',
                _FakeParams((5, ['u2', 'u3'])))
        assert book.lookup_number('5550100') is None
        assert book.lookup_number('5550200') == 'Cara Cole'
        assert book.lookup_number('5550101') == 'Bob Jones'
        assert book.starred_numbers() == []

    def test_get_miss_drops_uid(self, monkeypatch, tmp_path):
        book = _dbus_book(monkeypatch, tmp_path, {
            'List': [_FakeReply((1, ['u1', 'ghost'], []))],
            'Get': [_FakeReply((ADA_VCARD,)),
                    RuntimeError('not found')],
        })
        assert book.lookup_number('5550100') == 'Ada Lovelace'
        assert book.dbus_ready is True

    def test_list_failure_keeps_fallback(self, monkeypatch, tmp_path):
        book = _dbus_book(monkeypatch, tmp_path, {
            'List': [RuntimeError('no service')],
        })
        assert book.dbus_ready is False
        assert book.lookup_number('5559999') == 'Local Friend'
        assert book.lookup_number('5550100') is None

    def test_bus_missing_keeps_fallback(self, monkeypatch, tmp_path):
        book = _dbus_book(monkeypatch, tmp_path, {},
                          bus_error=RuntimeError('no bus'))
        assert book.dbus_ready is False
        assert book.lookup_number('5559999') == 'Local Friend'

    def test_close_unsubscribes(self, monkeypatch, tmp_path):
        _, conn = _scripted_gio(monkeypatch, {
            'List': [_FakeReply((0, [], []))],
        })
        _seed_local(monkeypatch, tmp_path)
        book = contacts.ContactBook()
        assert book.dbus_ready is True
        book.close()
        assert conn.unsubscribed == [42]


class TestLookupNumberAsync:
    def test_live_answer_wins(self, monkeypatch, tmp_path):
        _seed_local(monkeypatch, tmp_path)
        _scripted_gio(monkeypatch, {
            'LookupNumber': [
                _FakeReply(('u1', 'Ada Lovelace', ['starred']))],
        })
        book = contacts.ContactBook()
        seen = []
        book.lookup_number_async('5550100', seen.append)
        assert seen == ['Ada Lovelace']

    def test_miss_falls_back_to_cache(self, monkeypatch, tmp_path):
        _seed_local(monkeypatch, tmp_path)
        _scripted_gio(monkeypatch, {
            'LookupNumber': [_FakeReply(('', '', [])),
                             _FakeReply(('', '', []))],
        })
        book = contacts.ContactBook()
        seen = []
        book.lookup_number_async('5559999', seen.append)
        assert seen == ['Local Friend']
        book.lookup_number_async('5550000', seen.append)
        assert seen == ['Local Friend', None]

    def test_error_falls_back_to_cache(self, monkeypatch, tmp_path):
        _seed_local(monkeypatch, tmp_path)
        _scripted_gio(monkeypatch, {
            'LookupNumber': [RuntimeError('timeout')],
        })
        book = contacts.ContactBook()
        seen = []
        errors = []
        book.lookup_number_async('5559999', seen.append, errors.append)
        assert seen == ['Local Friend']


class TestStarredBridge:
    def test_sync_merges_into_config(self, monkeypatch, tmp_path):
        book = _dbus_book(monkeypatch, tmp_path, {
            'List': [_FakeReply((2, ['u1', 'u2'], []))],
            'Get': [_FakeReply((ADA_VCARD,)),
                    _FakeReply((BOB_VCARD,))],
        })
        saved = []
        config = SimpleNamespace(
            data={'dnd_starred_numbers': ['+1999']},
            save=lambda: saved.append(True))
        assert book.sync_starred_to_dnd(config) == [
            '15550100', '1999']
        assert config.data['dnd_starred_numbers'] == ['15550100', '1999']
        assert saved == [True]

    def test_sync_accepts_dnd_state(self, monkeypatch, tmp_path):
        book = _dbus_book(monkeypatch, tmp_path, {
            'List': [_FakeReply((1, ['u1'], []))],
            'Get': [_FakeReply((ADA_VCARD,))],
        })
        config = SimpleNamespace(data={'dnd_starred_numbers': []},
                                 save=lambda: None)
        dnd = SimpleNamespace(_config=config)
        assert book.sync_starred_to_dnd(dnd) == ['15550100']
        assert config.data['dnd_starred_numbers'] == ['15550100']

    def test_auto_sync_on_refresh(self, monkeypatch, tmp_path):
        _seed_local(monkeypatch, tmp_path)
        _scripted_gio(monkeypatch, {
            'List': [_FakeReply((1, ['u1'], []))],
            'Get': [_FakeReply((ADA_VCARD,))],
        })
        config = SimpleNamespace(data={'dnd_starred_numbers': []},
                                 save=lambda: None)
        contacts.ContactBook(config=config)
        assert config.data['dnd_starred_numbers'] == ['15550100']

    def test_no_config_handle_is_safe(self, monkeypatch, tmp_path):
        book = _dbus_book(monkeypatch, tmp_path, {
            'List': [_FakeReply((1, ['u1'], []))],
            'Get': [_FakeReply((ADA_VCARD,))],
        })
        assert book.sync_starred_to_dnd() == ['15550100']


_DRIVER = r'''
import json
import os
import re
import sys

launcher_src, xxcontacts_src, home, mode = sys.argv[1:5]
os.environ['HOME'] = home
sys.path.insert(0, launcher_src)
sys.path.insert(0, xxcontacts_src)

from gi.repository import Gio, GLib
from xx_contacts.service import (
    BUS_NAME, INTERFACE, INTROSPECTION_XML, OBJECT_PATH)
import contacts

local_dir = os.path.join(home, '.local', 'share', 'xx-wm')
os.makedirs(local_dir, exist_ok=True)
local_path = os.path.join(local_dir, 'contacts.json')
with open(local_path, 'w', encoding='utf-8') as handle:
    json.dump([{'name': 'Local Friend', 'numbers': ['+15559999']}],
              handle)


class FakeContactsService:
    def __init__(self):
        self.records = {
            'u-ada': ('Ada Lovelace', ['+15550100'], True),
            'u-bob': ('Bob Jones', ['+15550101'], False),
        }
        self.history = [(1, 'changed', 'u-ada'), (2, 'changed', 'u-bob')]
        self.revision = 2
        self.connection = None

    def register(self, connection):
        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        connection.register_object(
            OBJECT_PATH, node.interfaces[0], self.on_method)
        self.connection = connection

    def vcard(self, uid):
        name, numbers, starred = self.records[uid]
        lines = ['BEGIN:VCARD', 'VERSION:4.0', 'FN:' + name]
        lines += ['TEL;TYPE=CELL:' + n for n in numbers]
        if starred:
            lines.append('X-STARRED:1')
        lines.append('END:VCARD')
        return '\r\n'.join(lines) + '\r\n'

    def lookup(self, number):
        wanted = re.sub(r'[^\d]', '', number)
        for uid, (name, numbers, starred) in self.records.items():
            for raw in numbers:
                digits = re.sub(r'[^\d]', '', raw)
                tail = min(7, len(wanted), len(digits))
                if tail and wanted[-tail:] == digits[-tail:]:
                    return (uid, name, ['starred'] if starred else [])
        return ('', '', [])

    def search(self, query, limit):
        query = query.casefold()
        rows = []
        for uid, (name, numbers, _starred) in self.records.items():
            if query in name.casefold():
                rows.append((uid, name, numbers[0] if numbers else ''))
        return rows[:limit]

    def on_method(self, _connection, _sender, _path, _iface, method,
                  params, invocation):
        args = params.unpack()
        if method == 'List':
            (since,) = args
            changed = [u for (rev, kind, u) in self.history
                       if rev > since and kind == 'changed'
                       and u in self.records]
            deleted = [u for (rev, kind, u) in self.history
                       if rev > since and kind == 'deleted']
            invocation.return_value(
                GLib.Variant('(tasas)',
                             (self.revision, changed, deleted)))
        elif method == 'Get':
            (uid,) = args
            if uid not in self.records:
                invocation.return_dbus_error(
                    INTERFACE + '.NotFound', 'unknown contact')
            else:
                invocation.return_value(
                    GLib.Variant('(s)', (self.vcard(uid),)))
        elif method == 'LookupNumber':
            (number,) = args
            uid, name, tags = self.lookup(number)
            invocation.return_value(
                GLib.Variant('(ssas)', (uid, name, tags)))
        elif method == 'Search':
            query, limit = args
            invocation.return_value(
                GLib.Variant('(a(sss))',
                             (self.search(query, limit),)))
        else:
            invocation.return_dbus_error(
                INTERFACE + '.InvalidArgument', 'no such method')


class FakeConfig:
    def __init__(self):
        self.data = {'dnd_starred_numbers': ['+1999']}
        self.saved = 0

    def save(self):
        self.saved += 1


def poll_until(loop, ready, limit=300):
    state = {'ticks': 0}

    def _poll():
        state['ticks'] += 1
        if ready() or state['ticks'] >= limit:
            loop.quit()
            return False
        return True

    GLib.timeout_add(50, _poll)
    loop.run()


def async_lookup(book, number, timeout_s=15):
    loop = GLib.MainLoop()
    result = {}

    def _guard():
        result['timeout'] = True
        loop.quit()
        return False

    guard = GLib.timeout_add_seconds(timeout_s, _guard)

    def _done(name):
        result['name'] = name
        GLib.source_remove(guard)
        loop.quit()

    book.lookup_number_async(number, _done)
    loop.run()
    assert 'timeout' not in result, 'async lookup timed out'
    return result.get('name')


if mode == 'fallback':
    book = contacts.ContactBook()
    loop = GLib.MainLoop()
    GLib.timeout_add_seconds(2, lambda: (loop.quit(), False)[1])
    loop.run()
    assert book.dbus_ready is False, 'no service should be ready'
    assert book.lookup_number('5559999') == 'Local Friend'
    assert book.lookup_number('5550100') is None
    assert [c.name for c in book.search('local')] == ['Local Friend']
    loop2 = GLib.MainLoop()
    seen = []

    def _quit():
        loop2.quit()
        return False

    GLib.timeout_add_seconds(15, _quit)
    book.lookup_number_async(
        '5559999', lambda name: (seen.append(name), loop2.quit()))
    loop2.run()
    assert seen == ['Local Friend'], seen
    print('CONTACTS-FALLBACK-OK', flush=True)
    sys.exit(0)

service = FakeContactsService()
loop = GLib.MainLoop()
owned = {}


def on_bus_acquired(connection, _name):
    service.register(connection)


def on_name_acquired(_connection, _name):
    owned['yes'] = True
    loop.quit()


Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME,
                 Gio.BusNameOwnerFlags.NONE,
                 on_bus_acquired, on_name_acquired, None)
GLib.timeout_add_seconds(15, lambda: loop.quit() or False)
loop.run()
assert owned.get('yes'), 'fake service did not own its bus name'

config = FakeConfig()
book = contacts.ContactBook(config=config)
poll_until(loop, lambda: book.dbus_ready)
assert book.dbus_ready, 'service cache never synced'
assert book.lookup_number('5550100') == 'Ada Lovelace'
assert book.lookup_number('+15550101') == 'Bob Jones'
assert book.lookup_number('5559999') == 'Local Friend'
assert [c.name for c in book.search('ada')] == ['Ada Lovelace']
assert book.starred_numbers() == ['15550100']
assert config.data['dnd_starred_numbers'] == ['15550100', '1999'], (
    config.data)
assert config.saved >= 1

bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
search_result = {}


def on_search(conn, result):
    try:
        reply = conn.call_finish(result)
    except Exception as exc:
        search_result['error'] = str(exc)
    else:
        search_result['rows'] = reply.unpack()
    loop.quit()


bus.call(
    BUS_NAME, OBJECT_PATH, INTERFACE, 'Search',
    GLib.Variant('(su)', ('ada', 8)),
    GLib.VariantType.new('(a(sss))'),
    Gio.DBusCallFlags.NONE, 10000, None, on_search)
GLib.timeout_add_seconds(15, lambda: loop.quit() or False)
loop.run()
assert 'error' not in search_result, search_result
assert search_result['rows'][0][0][1] == 'Ada Lovelace', search_result

service.records['u-cara'] = ('Cara Cole', ['+15550200'], False)
name, numbers, _starred = service.records['u-bob']
service.records['u-bob'] = (name, numbers, True)
del service.records['u-ada']
service.history += [(3, 'changed', 'u-cara'), (4, 'changed', 'u-bob'),
                    (5, 'deleted', 'u-ada')]
service.revision = 5
service.connection.emit_signal(
    None, OBJECT_PATH, INTERFACE, 'Changed',
    GLib.Variant('(tas)', (5, ['u-cara', 'u-bob', 'u-ada'])))
poll_until(loop, lambda: book._dbus_revision >= 5)
assert book._dbus_revision >= 5, 'Changed refresh never landed'
assert book.lookup_number('5550100') is None
assert book.lookup_number('5550200') == 'Cara Cole'
assert book.lookup_number('5550101') == 'Bob Jones'
assert [c.name for c in book.search('cara')] == ['Cara Cole']
assert book.starred_numbers() == ['15550101']
assert config.data['dnd_starred_numbers'] == [
    '15550100', '15550101', '1999'], config.data

assert async_lookup(book, '5550101') == 'Bob Jones'
assert async_lookup(book, '5559999') == 'Local Friend'
assert async_lookup(book, '5550000') is None
print('CONTACTS-DBUS-OK', flush=True)
'''


def _need_private_bus():
    if shutil.which('dbus-run-session') is None:
        pytest.skip('dbus-run-session is not installed')
    pytest.importorskip('gi.repository.Gio')
    xx_src = XX_CONTACTS / 'src'
    if not (xx_src / 'xx_contacts' / 'service.py').exists():
        pytest.skip('xx-contacts source not found')
    return xx_src


def _run_driver(tmp_path, xx_src, home, mode):
    driver = tmp_path / ('contacts_%s_driver.py' % mode)
    driver.write_text(_DRIVER, encoding='utf-8')
    env = dict(os.environ)
    env['HOME'] = str(home)
    proc = subprocess.run(
        ['dbus-run-session', '--', sys.executable, str(driver),
         str(SRC), str(xx_src), str(home), mode],
        capture_output=True, text=True, timeout=120, env=env)
    return proc


class TestPrivateBus:
    def test_fake_service_end_to_end(self, tmp_path):
        xx_src = _need_private_bus()
        home = tmp_path / 'home'
        home.mkdir()
        proc = _run_driver(tmp_path, xx_src, home, 'dbus')
        assert proc.returncode == 0, (
            proc.stderr[-3000:] + '\n' + proc.stdout[-3000:])
        assert 'CONTACTS-DBUS-OK' in proc.stdout

    def test_fallback_with_no_service(self, tmp_path):
        _need_private_bus()
        home = tmp_path / 'home'
        home.mkdir()
        proc = _run_driver(tmp_path, XX_CONTACTS / 'src', home, 'fallback')
        assert proc.returncode == 0, (
            proc.stderr[-3000:] + '\n' + proc.stdout[-3000:])
        assert 'CONTACTS-FALLBACK-OK' in proc.stdout


def test_shared_book_is_one_instance_and_takes_the_config(monkeypatch):
    import contacts
    monkeypatch.setattr(contacts, "_SHARED", None)
    monkeypatch.setattr(contacts.ContactBook, "_start_async_refresh",
                        lambda self: None)
    first = contacts.shared_book()
    assert first._config is None
    cfg = object()
    assert contacts.shared_book(cfg) is first
    assert first._config is cfg
    assert contacts.shared_book(object())._config is cfg
