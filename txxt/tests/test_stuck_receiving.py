"""Multipart SMS stuck in RECEIVING (Chatty chatty-mm-account.c port).

ModemManager keeps a multipart SMS in RECEIVING until every part is in.
A part that never arrives leaves the row stuck; enough stuck rows fill
the modem/SIM store and new SMS silently stop arriving. The startup
reconcile keeps a row that may still complete, and gives up on it a week
after its timestamp (three days after first sight without one, a full
week from first sight when the clock is behind the timestamp): what text
it has imports through the normal save-then-delete path, a blank row is
just deleted.
"""

import json
import time

from txxt import daemon, mm_client
from txxt.store import Store

MODEM = "/org/freedesktop/ModemManager1/Modem/0"
DAY = 24 * 60 * 60
NOW = 1_800_000_000.0  # 2027-01-15


def iso(seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(seconds))


class _Reply:
    def __init__(self, values):
        self._values = tuple(values)

    def unpack(self):
        return self._values


class StoreBus:
    """MM1 modem store: List/GetAll/Delete with real tuple replies."""

    def __init__(self, rows):
        self.objects = {MODEM: {mm_client.MODEM_IFACE: {},
                                mm_client.MESSAGING_IFACE: {}}}
        self.rows = dict(rows)
        self.deletes = []

    def call_sync(self, bus, path, iface, method, params, *rest):
        if method == "GetManagedObjects":
            return _Reply((dict(self.objects),))
        if method == "List":
            return _Reply((sorted(self.rows),))
        if method == "GetAll":
            if path not in self.rows:
                raise OSError("gone")
            return _Reply((dict(self.rows[path]),))
        if method == "Delete":
            gone = params.unpack()[0]
            self.deletes.append(gone)
            self.rows.pop(gone, None)
            return _Reply(())
        raise AssertionError(method)


def row(text, state, stamp, number="tel:+16145550142"):
    return {"Number": number, "Text": text, "State": state,
            "PduType": 1, "Timestamp": stamp}


def bodies(store):
    return sorted(
        item.body
        for conv in store.get_all_conversations()
        for item in store.get_messages_for_conversation(conv.id)
    )


def _daemon(store, bus, monkeypatch, now=NOW):
    monkeypatch.setattr(daemon.time, "time", lambda: now)
    d = daemon.Daemon(store, system_bus_fn=lambda: bus,
                      session_bus_fn=lambda: None)
    watched = []
    d._watch_receiving = watched.append
    return d, watched


RECEIVING = mm_client.STATE_RECEIVING
RECEIVED = mm_client.STATE_RECEIVED


class TestReceivingExpired:
    def test_week_after_the_timestamp(self):
        sent = NOW - 7 * DAY
        assert mm_client.receiving_expired(iso(sent), NOW, NOW)
        assert not mm_client.receiving_expired(iso(sent + 60), NOW, NOW)

    def test_three_days_after_first_sight_without_a_timestamp(self):
        assert not mm_client.receiving_expired("", NOW, NOW)
        assert not mm_client.receiving_expired("junk", NOW, NOW - 2 * DAY)
        assert mm_client.receiving_expired("", NOW, NOW - 3 * DAY)

    def test_clock_behind_the_timestamp_counts_a_week_from_first_sight(self):
        future = iso(NOW + 400 * DAY)  # the phone thinks it is 1970-ish
        assert not mm_client.receiving_expired(future, NOW, NOW)
        assert not mm_client.receiving_expired(future, NOW, NOW - 6 * DAY)
        assert mm_client.receiving_expired(future, NOW, NOW - 7 * DAY)


class TestListStore:
    def test_blank_receiving_rows_are_reported_not_dropped(self):
        bus = StoreBus({
            "/s/1": row("hello", RECEIVED, iso(NOW)),
            "/s/2": row("", RECEIVING, iso(NOW - 9 * DAY)),
            "/s/3": row("", RECEIVED, iso(NOW)),
        })
        records, blanks = mm_client.list_store(bus, MODEM)
        assert [r.path for r in records] == ["/s/1"]
        assert blanks == [("/s/2", iso(NOW - 9 * DAY))]
        assert [r.path for r in mm_client.list_records(bus, MODEM)] == ["/s/1"]


class TestStartupReconcile:
    def test_old_partial_is_imported_then_deleted(self, monkeypatch):
        store = Store.memory()
        bus = StoreBus({"/s/old": row("part one of", RECEIVING,
                                      iso(NOW - 8 * DAY))})
        d, watched = _daemon(store, bus, monkeypatch)
        counts = d.startup_reconcile()
        assert bodies(store) == ["part one of"]
        assert bus.deletes == ["/s/old"]
        assert (counts.imported, counts.deleted) == (1, 1)
        assert store.pending_delete_rows() == []
        assert watched == []

    def test_fresh_partial_is_left_to_complete(self, monkeypatch):
        store = Store.memory()
        bus = StoreBus({"/s/new": row("part one of", RECEIVING,
                                      iso(NOW - DAY))})
        d, watched = _daemon(store, bus, monkeypatch)
        counts = d.startup_reconcile()
        assert bodies(store) == []
        assert bus.deletes == []
        assert counts.imported == 0
        assert watched == ["/s/new"]

    def test_blank_stuck_row_is_deleted_without_a_message(self, monkeypatch):
        store = Store.memory()
        bus = StoreBus({"/s/blank": row("", RECEIVING, iso(NOW - 10 * DAY))})
        d, _watched = _daemon(store, bus, monkeypatch)
        counts = d.startup_reconcile()
        assert bodies(store) == []
        assert bus.deletes == ["/s/blank"]
        assert counts.deleted == 1

    def test_undated_row_waits_three_days_from_first_sight(self, monkeypatch):
        store = Store.memory()
        bus = StoreBus({"/s/x": row("half", RECEIVING, "")})
        d, watched = _daemon(store, bus, monkeypatch)
        d.startup_reconcile()
        assert bus.deletes == [] and watched == ["/s/x"]
        seen = json.loads(store.get_meta(daemon.RECEIVING_SEEN_META_KEY))
        assert seen == {"/s/x\t": NOW}
        d, watched = _daemon(store, bus, monkeypatch, now=NOW + 2 * DAY)
        d.startup_reconcile()
        assert bus.deletes == [] and watched == ["/s/x"]
        d, watched = _daemon(store, bus, monkeypatch, now=NOW + 3 * DAY)
        d.startup_reconcile()
        assert bus.deletes == ["/s/x"] and watched == []
        assert bodies(store) == ["half"]
        seen = json.loads(store.get_meta(daemon.RECEIVING_SEEN_META_KEY))
        assert seen == {}

    def test_complete_rows_still_reconcile_normally(self, monkeypatch):
        store = Store.memory()
        bus = StoreBus({
            "/s/done": row("whole message", RECEIVED, iso(NOW - DAY)),
            "/s/new": row("part", RECEIVING, iso(NOW - DAY)),
        })
        d, watched = _daemon(store, bus, monkeypatch)
        d.startup_reconcile()
        assert bodies(store) == ["whole message"]
        assert bus.deletes == ["/s/done"]
        assert watched == ["/s/new"]


class TestModemManagerRestart:
    def test_reappearing_modem_manager_reconciles_again(self, monkeypatch):
        store = Store.memory()
        d = daemon.Daemon(store, system_bus_fn=lambda: None,
                          session_bus_fn=lambda: None)
        submitted = []
        monkeypatch.setattr(d._executor, "submit",
                            lambda fn, *a: submitted.append(fn))
        d._modem_path = MODEM
        d.on_mm_appeared(None, mm_client.MM_BUS_NAME, ":1.4")
        assert submitted == []
        d.on_mm_vanished(None, mm_client.MM_BUS_NAME)
        assert d._modem_path is None
        d.on_mm_appeared(None, mm_client.MM_BUS_NAME, ":1.9")
        assert submitted == [d._startup_and_log]
        d.on_mm_appeared(None, mm_client.MM_BUS_NAME, ":1.12")
        assert submitted == [d._startup_and_log, d._startup_and_log]
