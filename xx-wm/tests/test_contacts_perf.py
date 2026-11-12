"""ContactBook off the startup path: the fallback files are read on a
worker, the first service sync keeps a bounded number of Get calls in
flight, and a repeated caller-ID lookup (the call timer asks every
second) does not rescan the book until the contacts change."""
import json
import threading
import time
from types import SimpleNamespace

import pytest

import contacts

VCARD = ('BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Person %d\r\n'
         'TEL;TYPE=CELL:+1555%04d\r\nEND:VCARD\r\n')


class _Reply:
    def __init__(self, out_args):
        self._out_args = out_args

    def unpack(self):
        return self._out_args


class _AsyncConn:
    """Holds every Get until the test answers it, like a real bus."""

    def __init__(self, uids):
        self.uids = uids
        self.pending = []
        self.gets = 0

    def call(self, _bus, _path, _iface, method, params, _reply_type, _flags,
             _timeout, _cancellable, callback):
        if method == 'List':
            callback(self, _Reply((1, list(self.uids), [])))
        elif method == 'Get':
            self.gets += 1
            uid = params.value[0]
            self.pending.append((uid, callback))

    def call_finish(self, result):
        return result

    def answer_one(self):
        uid, callback = self.pending.pop(0)
        n = int(uid[1:])
        callback(self, _Reply((VCARD % (n, n),)))

    def signal_subscribe(self, *_a):
        return 1


class _Gio:
    BusType = SimpleNamespace(SESSION=2)
    DBusCallFlags = SimpleNamespace(NONE=0)
    DBusSignalFlags = SimpleNamespace(NONE=0)

    def __init__(self, conn):
        self._conn = conn

    def bus_get(self, _t, _c, callback):
        callback(None, 'bus')

    def bus_get_finish(self, _r):
        return self._conn


class _GLib:
    class Variant:
        def __init__(self, fmt, value):
            self.fmt, self.value = fmt, value

    class VariantType:
        @staticmethod
        def new(sig):
            return sig


@pytest.fixture
def local_file(monkeypatch, tmp_path):
    path = tmp_path / 'contacts.json'
    path.write_text(json.dumps([{'name': 'Local Friend',
                                 'numbers': ['+15559999']}]),
                    encoding='utf-8')
    monkeypatch.setattr(contacts, '_LOCAL_JSON', path)
    monkeypatch.setattr(contacts, '_VCF_SEARCH_PATHS', [])
    monkeypatch.setattr(contacts, '_gi', lambda: (None, None))
    return path


class TestFallbackOffTheMainThread:
    def test_construction_does_not_read_the_files(self, monkeypatch,
                                                  local_file):
        readers = []
        gate = threading.Event()
        real = contacts.ContactBook._reload

        def _slow_reload(self):
            readers.append(threading.current_thread())
            gate.wait(2)
            real(self)
        monkeypatch.setattr(contacts.ContactBook, '_reload', _slow_reload)
        started = time.perf_counter()
        book = contacts.ContactBook()
        assert time.perf_counter() - started < 0.2
        gate.set()
        book._fallback_ready.wait(2)
        assert readers and readers[0] is not threading.main_thread()
        assert book.lookup_number('5559999') == 'Local Friend'

    def test_an_early_lookup_waits_for_the_read(self, local_file):
        book = contacts.ContactBook()
        assert book.lookup_number('+15559999') == 'Local Friend'


class TestBoundedFirstSync:
    def test_gets_stay_within_the_window(self, monkeypatch, local_file):
        conn = _AsyncConn(['u%d' % n for n in range(50)])
        monkeypatch.setattr(contacts, '_gi', lambda: (_Gio(conn), _GLib))
        book = contacts.ContactBook()
        assert len(conn.pending) == contacts._MAX_GETS_IN_FLIGHT
        peak = len(conn.pending)
        while conn.pending:
            conn.answer_one()
            peak = max(peak, len(conn.pending))
        assert peak <= contacts._MAX_GETS_IN_FLIGHT
        assert conn.gets == 50
        assert book.dbus_ready is True
        assert book.lookup_number('+15550049') == 'Person 49'


class TestLookupMemo:
    def test_repeat_lookup_does_not_rescan(self, monkeypatch, local_file):
        conn = _AsyncConn(['u1', 'u2'])
        monkeypatch.setattr(contacts, '_gi', lambda: (_Gio(conn), _GLib))
        book = contacts.ContactBook()
        while conn.pending:
            conn.answer_one()
        book._fallback_ready.wait(2)
        matches = []
        real = contacts._numbers_match
        monkeypatch.setattr(contacts, '_numbers_match',
                            lambda a, b: matches.append(1) or real(a, b))
        assert book.lookup_number('+15550002') == 'Person 2'
        first = len(matches)
        assert first > 0
        for _ in range(60):  # a minute of the call timer
            assert book.lookup_number('+15550002') == 'Person 2'
            assert book.lookup_number('+15550777') is None
        after_misses = len(matches)
        for _ in range(60):
            book.lookup_number('+15550002')
            book.lookup_number('+15550777')
        assert len(matches) == after_misses
        # A contact change drops the memo.
        book._apply_list_result(conn, 2, [], ['u2'])
        assert book.lookup_number('+15550002') is None
        assert len(matches) > after_misses
