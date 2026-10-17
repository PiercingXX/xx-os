"""L3 daemon tests: reconcile, inbound pipeline, retry queue, lock.

Reconcile and pipeline tests run against the real SQLite store plus
small fake modem buses; no display, no live D-Bus.
"""

import fcntl
import os

from txxt import blocking, daemon, mm_client
from txxt.blocking import InboundFilter, MessageDisposition
from txxt.daemon import (
    InboundPipeline,
    ReconcileCounts,
    RetryQueue,
    acquire_single_instance,
    reconcile_mms_messages,
    reconcile_records,
    retry_delay_ms,
    seeds_with_addresses,
    sms_timestamp_millis,
)
from txxt.settings import Settings
from txxt.store import Store

MODEM = "/org/freedesktop/ModemManager1/Modem/0"


class _TupleReply:
    """Tuple-of-out-args reply per XXWM_RULES.md."""

    def __init__(self, values):
        self._values = tuple(values)

    def unpack(self):
        return self._values


class _FakeParams:
    def __init__(self, values):
        self._values = tuple(values)

    def unpack(self):
        return self._values


def record(path, number, text, outgoing=False, stamp="2024-06-01T12:00:00+00:00"):
    return mm_client.SmsRecord(
        path=path, number=number, text=text, timestamp=stamp,
        pdu_type=2 if outgoing else 1, state=mm_client.STATE_RECEIVED,
        outgoing=outgoing,
    )


class FakeModemStore:
    """Fake modem object store: delete removes the path (True once)."""

    def __init__(self):
        self.deleted = []
        self.fail = set()

    def __call__(self, path):
        self.deleted.append(path)
        if path in self.fail:
            return False
        return True


def bodies(store):
    return sorted(
        item.body
        for conv in store.get_all_conversations()
        for item in store.get_messages_for_conversation(conv.id)
    )


# ---- timestamps ----

class TestSmsTimestamp:
    def test_iso_parses_to_millis(self):
        assert sms_timestamp_millis("2024-06-01T12:00:00+00:00") == 1717243200000

    def test_garbage_is_zero_and_still_imports_once(self):
        assert sms_timestamp_millis("not-a-date") == 0
        assert sms_timestamp_millis("") == 0


# ---- reconcile ----

class TestReconcileRecords:
    def test_imports_missing_then_deletes(self):
        store = Store.memory()
        modem = FakeModemStore()
        counts = reconcile_records(
            [record("/s/1", "+1555", "hello"), record("/s/2", "+1555", "hi")],
            store, modem,
        )
        assert (counts.imported, counts.deleted) == (2, 2)
        assert bodies(store) == ["hello", "hi"]
        assert store.pending_delete_rows() == []
        assert store.get_meta("last_reconcile_millis") is not None

    def test_match_window_prevents_duplicates(self):
        store = Store.memory()
        store.persist_inbound_sms("+15551234567", "hello", 1717243200000)
        modem = FakeModemStore()
        counts = reconcile_records(
            [record("/s/1", "(555) 123-4567", "hello")], store, modem
        )
        assert (counts.imported, counts.already_known, counts.deleted) == (0, 1, 1)
        assert bodies(store) == ["hello"]

    def test_failed_delete_keeps_flag_and_keeps_row(self):
        store = Store.memory()
        modem = FakeModemStore()
        modem.fail.add("/s/1")
        counts = reconcile_records([record("/s/1", "+1555", "hello")], store, modem)
        assert (counts.imported, counts.delete_failed) == (1, 1)
        flagged = store.pending_delete_rows()
        assert len(flagged) == 1 and flagged[0].body == "hello"

    def test_crash_recovery_redeletes_without_duplicating(self):
        store = Store.memory()
        modem = FakeModemStore()
        modem.fail.add("/s/1")
        reconcile_records([record("/s/1", "+1555", "hello")], store, modem)
        assert len(store.pending_delete_rows()) == 1
        modem.fail.clear()
        counts = reconcile_records([record("/s/1", "+1555", "hello")], store, modem)
        assert (counts.imported, counts.already_known, counts.deleted) == (0, 1, 1)
        assert bodies(store) == ["hello"]
        assert store.pending_delete_rows() == []

    def test_flagged_rows_count_as_recovered(self):
        store = Store.memory()
        mid = store.persist_inbound_sms("+1555", "old", 1717243200000)
        store.mark_pending_delete(mid)
        counts = reconcile_records([], store, FakeModemStore())
        assert counts.flagged_recovered == 1

    def test_outgoing_rows_import_as_sent(self):
        store = Store.memory()
        counts = reconcile_records(
            [record("/s/9", "+1555", "on my way", outgoing=True)],
            store, FakeModemStore(),
        )
        assert counts.imported == 1
        rows = [
            item for conv in store.get_all_conversations()
            for item in store.get_messages_for_conversation(conv.id)
        ]
        assert [(r.direction, r.sent) for r in rows] == [("OUTGOING", True)]


class TestReconcileMms:
    def test_mms_metadata_imports_as_photo_placeholder(self):
        from txxt.conversations_core import COLLAPSED_PHOTO_PLACEHOLDER

        store = Store.memory()
        modem = FakeModemStore()
        counts = reconcile_mms_messages(
            [("/m/1", {"Recipients": ["+1555"], "Text": "cap",
                       "ContentLocation": "http://mms/1"})],
            store, modem,
        )
        # The mmsd copy is retained for tap-to-fetch (bug_txxt finding 2).
        assert (counts.imported, counts.deleted) == (1, 0)
        assert modem.deleted == []
        rows = [
            item for conv in store.get_all_conversations()
            for item in store.get_messages_for_conversation(conv.id)
        ]
        assert [(r.transport, r.body) for r in rows] == [
            ("MMS", COLLAPSED_PHOTO_PLACEHOLDER)
        ]

    def test_mms_idempotent_on_second_pass(self):
        store = Store.memory()
        pair = ("/m/1", {"Recipients": ["+1555"], "Text": "cap",
                          "Date": "2024-06-01T12:00:00+00:00"})
        reconcile_mms_messages([pair], store, FakeModemStore())
        counts = reconcile_mms_messages([pair], store, FakeModemStore())
        assert (counts.imported, counts.already_known, counts.deleted) == (0, 1, 0)

    def test_dateless_mms_imports_once_and_retains(self):
        store = Store.memory()
        pair = ("/m/1", {"Recipients": ["+1555"], "Text": "cap"})
        counts = reconcile_mms_messages([pair], store, FakeModemStore())
        assert (counts.imported, counts.deleted) == (1, 0)
        again = reconcile_mms_messages([pair], store, FakeModemStore())
        assert (again.imported, again.already_known) == (0, 1)
        assert len([
            item for conv in store.get_all_conversations()
            for item in store.get_messages_for_conversation(conv.id)
        ]) == 1


# ---- inbound pipeline ----

class FakeNotifyBus:
    def __init__(self):
        self.posts = []
        self.hint_posts = []
        self.next_id = 100

    def call_sync(self, bus, path, iface, method, params, *rest):
        self.next_id += 1

        class Reply:
            def __init__(self, nid):
                self.nid = nid

            def unpack(inner_self):
                return (self.next_id,)

        unpacked = params.unpack()
        summary = unpacked[3]
        body = unpacked[4]
        self.posts.append((summary, body))
        self.hint_posts.append(dict(unpacked[6]))
        return Reply(self.next_id)


class FakeContacts:
    def display_name(self, number):
        return {"+1555": "Zed"}.get(number, number)


def pipeline(store, **kwargs):
    kwargs.setdefault("filter_provider", lambda: InboundFilter())
    kwargs.setdefault("contacts", FakeContacts())
    return InboundPipeline(store, **kwargs)


class TestInboundPipeline:
    def test_delivered_notifies_sender_only(self):
        store = Store.memory()
        bus = FakeNotifyBus()
        outcome = pipeline(store, notify_bus=bus).handle_sms("+1555", "secret text")
        assert outcome.disposition is MessageDisposition.DELIVER
        assert outcome.notified and outcome.message_id is not None
        assert bus.posts == [("Zed", "")]
        row = store.get_message(outcome.message_id)
        assert row is not None and not row.is_read

    def test_blocked_stored_silently_and_read(self):
        store = Store.memory()
        bus = FakeNotifyBus()
        filt = InboundFilter(blocked_addresses={"+1555"})
        outcome = pipeline(
            store, filter_provider=lambda: filt, notify_bus=bus
        ).handle_sms("+1555", "spam")
        assert outcome.disposition is MessageDisposition.BLOCK
        assert not outcome.notified and bus.posts == []
        row = store.get_message(outcome.message_id)
        assert row is not None and row.is_read

    def test_quarantined_held_with_notice(self):
        store = Store.memory()
        bus = FakeNotifyBus()
        filt = InboundFilter(quarantine_unknown_senders=True)
        outcome = pipeline(
            store, filter_provider=lambda: filt, notify_bus=bus
        ).handle_sms("+1999", "hello?")
        assert outcome.disposition is MessageDisposition.QUARANTINE
        assert outcome.notified
        row = store.get_message(outcome.message_id)
        assert row is not None
        conv = store.get_conversation(row.conversation_id)
        assert conv is not None and conv.is_quarantined

    def test_content_posture_includes_preview(self):
        store = Store.memory()
        bus = FakeNotifyBus()
        settings = Settings(lock_screen_privacy="content", alert_style="silent")
        outcome = pipeline(
            store, notify_bus=bus, settings_loader=lambda: settings
        ).handle_sms("+1555", "preview me")
        assert outcome.notified and bus.posts == [("Zed", "preview me")]

    def test_nothing_posture_posts_nothing(self):
        store = Store.memory()
        bus = FakeNotifyBus()
        settings = Settings(lock_screen_privacy="nothing")
        outcome = pipeline(
            store, notify_bus=bus, settings_loader=lambda: settings
        ).handle_sms("+1555", "quiet")
        assert not outcome.notified and bus.posts == []

    def test_no_bus_still_stores(self):
        store = Store.memory()
        outcome = pipeline(store).handle_sms("+1555", "kept")
        assert not outcome.notified and bodies(store) == ["kept"]

    def test_notification_maps_to_conversation(self):
        store = Store.memory()
        pipe = pipeline(store, notify_bus=FakeNotifyBus())
        outcome = pipe.handle_sms("+1555", "hi")
        assert pipe.conversation_for_notification(outcome.notification_id) == \
            outcome.conversation_id

    def test_notification_map_evicts_oldest_past_cap(self):
        from txxt.daemon import NOTIFICATION_MAP_CAP

        store = Store.memory()
        pipe = pipeline(store, notify_bus=FakeNotifyBus())
        first = pipe.handle_sms("+1555", "one")
        for index in range(NOTIFICATION_MAP_CAP + 5):
            pipe.handle_sms(f"+1600{index:04d}", f"msg {index}")
        assert len(pipe._notification_map) == NOTIFICATION_MAP_CAP
        assert pipe.conversation_for_notification(
            first.notification_id) is None

    def test_unknown_notification_id_maps_to_nothing(self):
        store = Store.memory()
        pipe = pipeline(store, notify_bus=FakeNotifyBus())
        pipe.handle_sms("+1555", "hi")
        assert pipe.conversation_for_notification(999999) is None

    def test_action_target_hint_rides_posted_cards(self):
        from txxt import notify as notify_mod

        store = Store.memory()
        bus = FakeNotifyBus()
        target = notify_mod.action_target(
            "io.piercingxx.TxxT.Daemon",
            "/io/piercingxx/TxxT/Daemon",
            "io.piercingxx.TxxT1",
        )
        assert target == (
            "io.piercingxx.TxxT.Daemon "
            "/io/piercingxx/TxxT/Daemon io.piercingxx.TxxT1"
        )
        outcome = pipeline(
            store, notify_bus=bus, action_target=target,
        ).handle_sms("+1555", "hi")
        assert outcome.notified
        assert bus.hint_posts[-1][notify_mod.ACTION_TARGET_HINT] == target

    def test_no_action_target_means_no_hint(self):
        from txxt import notify as notify_mod

        store = Store.memory()
        bus = FakeNotifyBus()
        outcome = pipeline(store, notify_bus=bus).handle_sms("+1555", "hi")
        assert outcome.notified
        assert notify_mod.ACTION_TARGET_HINT not in bus.hint_posts[-1]


# ---- retry queue ----

class TestRetryQueue:
    def test_delay_doubles_then_caps(self):
        assert retry_delay_ms(0) == 5_000
        assert retry_delay_ms(1) == 10_000
        assert retry_delay_ms(20) == daemon.RETRY_MAX_DELAY_MS

    def test_due_and_sent(self):
        now = [1_000_000]
        queue = RetryQueue(now_millis=lambda: now[0])
        queue.add("+1555", "hi", 7)
        # Fresh sends wait out one base delay (bug_txxt finding 21).
        assert queue.due() == []
        now[0] += daemon.RETRY_BASE_DELAY_MS
        assert len(queue.due()) == 1
        key = next(iter(queue._items))
        queue.mark_attempted(key)
        assert queue.due() == []
        now[0] += daemon.RETRY_MAX_DELAY_MS + 1
        assert len(queue.due()) == 1
        queue.mark_sent(key)
        assert len(queue) == 0

    def test_exhausted_rows_hold_forever(self):
        now = [0]
        queue = RetryQueue(now_millis=lambda: now[0])
        key = queue.add("+1555", "hi", 7)
        for _ in range(daemon.RETRY_MAX_ATTEMPTS):
            queue.mark_attempted(key)
            now[0] += daemon.RETRY_MAX_DELAY_MS + 1
        assert len(queue.held_items()) == 1
        assert queue.due() == []

    def test_seed_resolves_address_from_conversation(self):
        store = Store.memory()
        mid = store.persist_outgoing_sms("+15551234567", "retry me", now_millis=5)
        seeds = seeds_with_addresses(store, store.pending_outgoing_sms())
        assert [(s.address, s.body, s.id) for s in seeds] == [
            ("+15551234567", "retry me", mid)
        ]

    def test_seed_never_resets_live_backoff(self):
        queue = RetryQueue(now_millis=lambda: 0)
        seed = daemon.RetrySeed(address="+1555", body="hi", id=7)
        queue.seed_pending([seed])
        key = next(iter(queue._items))
        queue.mark_attempted(key)
        due_at = queue._items[key].next_due_millis
        queue.seed_pending([seed])
        assert queue._items[key].attempts == 1
        assert queue._items[key].next_due_millis == due_at


class FakeBusForPump:
    def __init__(self):
        self.objects = {
            MODEM: {"org.freedesktop.ModemManager1.Modem": {},
                    "org.freedesktop.ModemManager1.Modem.Messaging": {}}
        }

    def call_sync(self, bus, path, iface, method, params, *rest):
        if method == "GetManagedObjects":
            return _TupleReply((dict(self.objects),))
        if method == "Delete":
            return _TupleReply(())
        raise AssertionError(method)


class TestPumpRetry:
    def _daemon(self, store, sender, **kwargs):
        kwargs.setdefault("system_bus_fn", lambda: FakeBusForPump())
        kwargs.setdefault("session_bus_fn", lambda: None)
        kwargs.setdefault("sender", sender)
        return daemon.Daemon(store, **kwargs)

    def test_success_marks_sent(self):
        store = Store.memory()
        mid = store.persist_outgoing_sms("+1555", "go", now_millis=9)
        sent = []
        d = self._daemon(store, lambda b, m, a, t: sent.append((a, t)) or (True, "/s/1"))
        d.startup_reconcile()
        counts = d.pump_retry()
        assert counts.resent == 1
        assert store.get_message(mid).sent is True
        assert sent == [("+1555", "go")]

    def test_failure_backs_off_then_holds(self):
        now = [0]
        store = Store.memory()
        store.persist_outgoing_sms("+1555", "go", now_millis=9)
        retry = RetryQueue(now_millis=lambda: now[0])
        d = self._daemon(
            store, lambda b, m, a, t: (False, "no signal"), retry=retry
        )
        d.startup_reconcile()
        assert d.pump_retry().resend_failed == 1
        assert d.pump_retry().resend_failed == 0  # backing off, nothing due
        for _ in range(daemon.RETRY_MAX_ATTEMPTS):
            now[0] += daemon.RETRY_MAX_DELAY_MS + 1
            d.pump_retry()
        assert len(retry.held_items()) == 1

    def test_startup_reconcile_seeds_pending(self):
        store = Store.memory()
        store.persist_outgoing_sms("+1555", "queued", now_millis=9)
        d = self._daemon(store, lambda b, m, a, t: (True, "/s/2"))
        d.startup_reconcile()
        assert d.pump_retry().resent == 1


class FakeRaceBus(FakeBusForPump):
    """One SMS row; ``Delete`` removes it like the real modem store.

    A deleted object is gone: a later ``GetAll``/``List`` no longer
    sees it, so the startup/signal overlap can only double-import when
    the daemon's own bookkeeping lets it through.
    """

    def __init__(self):
        super().__init__()
        self.sms_lists = {MODEM: ["/s/race"]}
        self.sms_props = {"/s/race": {
            "Number": "tel:+15550001111", "Text": "race",
            "State": 3, "PduType": 1,
            "Timestamp": "2024-06-01T12:00:00+00:00",
        }}
        self.deletes = []

    def call_sync(self, bus, path, iface, method, params, *rest):
        if method == "GetManagedObjects":
            return _TupleReply((dict(self.objects),))
        if method == "List":
            return _TupleReply((list(self.sms_lists.get(path, [])),))
        if method == "GetAll":
            props = self.sms_props.get(path)
            if props is None:
                raise OSError("gone")
            return _TupleReply((dict(props),))
        if method == "Delete":
            deleted = params.unpack()[0]
            self.deletes.append(deleted)
            self.sms_props.pop(deleted, None)
            for listed in self.sms_lists.values():
                if deleted in listed:
                    listed.remove(deleted)
            return _TupleReply(())
        raise AssertionError(method)


class TestDuplicateImportRace:
    """An SMS arriving during the startup reconcile is stored once."""

    def _race_daemon(self, store, bus):
        return daemon.Daemon(
            store,
            system_bus_fn=lambda: bus,
            session_bus_fn=lambda: None,
        )

    @staticmethod
    def _no_poll(monkeypatch):
        """Single GetAll instead of the worker-thread RECEIVING backoff."""
        monkeypatch.setattr(
            mm_client, "wait_received_props",
            lambda bus, path: mm_client.get_sms_properties(bus, path),
        )

    def test_reconcile_then_signal_imports_once(self, monkeypatch):
        self._no_poll(monkeypatch)
        store = Store.memory()
        bus = FakeRaceBus()
        posts = FakeNotifyBus()
        d = daemon.Daemon(
            store,
            pipeline=InboundPipeline(store, notify_bus=posts),
            system_bus_fn=lambda: bus,
            session_bus_fn=lambda: None,
        )
        assert d.startup_reconcile().imported == 1
        d._fetch_and_handle("/s/race")
        assert bodies(store) == ["race"]
        # The modem copy is gone after the reconcile delete, so the late
        # signal finds nothing and stores (and notifies) nothing new.
        assert bus.deletes == ["/s/race"]
        assert posts.posts == []

    def test_signal_then_reconcile_imports_once(self, monkeypatch):
        self._no_poll(monkeypatch)
        store = Store.memory()
        bus = FakeRaceBus()
        d = self._race_daemon(store, bus)
        d._fetch_and_handle("/s/race")
        counts = d.startup_reconcile()
        assert (counts.imported, counts.already_known) == (0, 0)
        assert bodies(store) == ["race"]

    def test_concurrent_reconcile_and_signal_import_once(self, monkeypatch):
        import threading

        self._no_poll(monkeypatch)
        store = Store.memory()
        bus = FakeRaceBus()
        d = self._race_daemon(store, bus)
        gate = threading.Barrier(2)
        outcomes = []

        def _reconcile():
            gate.wait(timeout=10)
            outcomes.append(d.startup_reconcile().imported)

        def _signal():
            gate.wait(timeout=10)
            d._fetch_and_handle("/s/race")
            outcomes.append("signal-done")

        workers = [
            threading.Thread(target=_reconcile),
            threading.Thread(target=_signal),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=30)
        assert not any(worker.is_alive() for worker in workers)
        assert bodies(store) == ["race"]
        assert "signal-done" in outcomes
        assert [v for v in outcomes if isinstance(v, int)] in ([0], [1])


# ---- single instance ----

class TestSingleInstance:
    def test_second_holder_loses(self, tmp_path):
        first = acquire_single_instance(tmp_path / "txxt.lock")
        assert first is not None
        try:
            assert acquire_single_instance(tmp_path / "txxt.lock") is None
        finally:
            fcntl.flock(first, fcntl.LOCK_UN)
            os.close(first)

    def test_reacquire_after_release(self, tmp_path):
        fd = acquire_single_instance(tmp_path / "txxt.lock")
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        assert acquire_single_instance(tmp_path / "txxt.lock") is not None


# ---- daemon signal/action wiring ----

class InlineExecutor:
    def submit(self, fn, *args):
        fn(*args)


class TestDaemonWiring:
    def test_action_reply_opens_thread(self, monkeypatch):
        store = Store.memory()
        pipe = InboundPipeline(store, notify_bus=FakeNotifyBus())
        outcome = pipe.handle_sms("+1555", "hi")
        d = daemon.Daemon(store, pipeline=pipe)
        d._executor = InlineExecutor()
        opened = []
        monkeypatch.setattr(daemon, "open_thread", lambda cid: opened.append(cid))
        d.on_action_invoked(
            None, None, None, None, None,
            _FakeParams((outcome.notification_id, "reply")), None,
        )
        assert opened == [outcome.conversation_id]

    def test_unknown_action_ignored(self, monkeypatch):
        store = Store.memory()
        d = daemon.Daemon(store)
        d._executor = InlineExecutor()
        monkeypatch.setattr(
            daemon, "open_thread",
            lambda cid: (_ for _ in ()).throw(AssertionError("must not open")),
        )
        d.on_action_invoked(None, None, None, None, None, _FakeParams((1, "nope")), None)

    def test_messaging_added_ignores_non_received(self):
        store = Store.memory()
        d = daemon.Daemon(store)
        d.on_messaging_added(
            None, None, None, None, None, _FakeParams(("/s/1", False)), None,
        )
        assert bodies(store) == []

    def test_open_thread_without_desktop_is_false(self):
        assert daemon.open_thread(12345) is False

    def test_reconcile_counts_default(self):
        assert ReconcileCounts().imported == 0


def test_blocking_live_filter_default_delivers():
    assert blocking.live_current().evaluate("+1555", "hi")[0] is MessageDisposition.DELIVER


# ---- bug_txxt finding 2: inbound MMS photo lost with default settings ----

class _TapAdapter:
    """Fake mmsd-tng holding one photo message (documented 5-tuples)."""

    def __init__(self, photo_bytes):
        self.photo_bytes = photo_bytes
        self.messages = {
            "/org/ofono/mms/message/7": {
                "Sender": "+15550100999",
                "Recipients": ["+15550100000"],
                "Date": "2024-06-01T12:00:00+00:00",
                "Status": "received",
                "Attachments": None,  # filled by carrier_path()
            }
        }
        self.deleted = []

    def carrier_path(self, tmp_path):
        carrier = tmp_path / "carrier.bin"
        carrier.write_bytes(b"PDUHEAD" + self.photo_bytes + b"PDUTAIL")
        offset = len(b"PDUHEAD")
        self.messages["/org/ofono/mms/message/7"]["Attachments"] = [
            ("image_0.jpg", "image/jpeg", str(carrier), offset, len(self.photo_bytes)),
        ]
        return str(carrier)

    def list_messages(self):
        return list(self.messages)

    def get_message(self, path):
        return self.messages.get(path)

    def delete_message(self, path):
        if path in self.messages:
            del self.messages[path]
            self.deleted.append(path)
            return True
        return False


class TestMmsPhotoRetained:
    def test_reconcile_keeps_mmsd_copy_until_tap(self, tmp_path, monkeypatch):

        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        store = Store.memory()
        adapter = _TapAdapter(b"\xff\xd8-photo\xff\xd9")
        adapter.carrier_path(tmp_path)
        counts = reconcile_mms_messages(
            [("/org/ofono/mms/message/7", adapter.messages["/org/ofono/mms/message/7"])],
            store, adapter.delete_message,
        )
        assert counts.imported == 1 and counts.deleted == 0
        assert "/org/ofono/mms/message/7" in adapter.messages

    def test_tap_fetches_retained_copy_then_deletes(self, tmp_path, monkeypatch):
        from txxt.daemon import Daemon

        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        store = Store.memory()
        adapter = _TapAdapter(b"\xff\xd8-photo\xff\xd9")
        adapter.carrier_path(tmp_path)
        props = adapter.messages["/org/ofono/mms/message/7"]
        counts = reconcile_mms_messages(
            [("/org/ofono/mms/message/7", props)], store, adapter.delete_message,
        )
        assert counts.imported == 1
        row = next(
            item for conv in store.get_all_conversations()
            for item in store.get_messages_for_conversation(conv.id)
        )
        daemon = Daemon(
            store, system_bus_fn=lambda: None, session_bus_fn=lambda: None,
        )
        daemon._mms = adapter
        found = daemon.fetch_mms(row.id)
        assert found and open(found, "rb").read() == b"\xff\xd8-photo\xff\xd9"
        assert store.get_message(row.id).media_path == found
        assert adapter.messages == {}
        assert store.pending_delete_rows() == []

    def test_second_pass_neither_duplicates_nor_deletes(self, tmp_path):
        store = Store.memory()
        adapter = _TapAdapter(b"\xff\xd8-photo\xff\xd9")
        adapter.carrier_path(tmp_path)
        props = adapter.messages["/org/ofono/mms/message/7"]
        reconcile_mms_messages([("/org/ofono/mms/message/7", props)], store, adapter.delete_message)
        counts = reconcile_mms_messages(
            [("/org/ofono/mms/message/7", props)], store, adapter.delete_message,
        )
        assert (counts.imported, counts.already_known, counts.deleted) == (0, 1, 0)
        assert "/org/ofono/mms/message/7" in adapter.messages

    def test_same_sender_new_date_imports_again(self):
        store = Store.memory()
        first = {
            "Sender": "+1555", "Date": "2024-06-01T12:00:00+00:00",
            "Status": "received",
        }
        second = {
            "Sender": "+1555", "Date": "2024-06-01T12:03:00+00:00",
            "Status": "received",
        }
        modem = FakeModemStore()
        assert reconcile_mms_messages([("/m/1", first)], store, modem).imported == 1
        counts = reconcile_mms_messages([("/m/2", second)], store, modem)
        assert counts.imported == 1
        assert len([
            item for conv in store.get_all_conversations()
            for item in store.get_messages_for_conversation(conv.id)
        ]) == 2

    def test_content_location_disambiguates_same_window(self):
        store = Store.memory()
        stamp = "2024-06-01T12:00:00+00:00"
        first = {
            "Sender": "+1555", "Date": stamp, "Status": "received",
            "ContentLocation": "http://mms/one",
        }
        second = {
            "Sender": "+1555", "Date": stamp, "Status": "received",
            "ContentLocation": "http://mms/two",
        }
        modem = FakeModemStore()
        assert reconcile_mms_messages([("/m/1", first)], store, modem).imported == 1
        assert reconcile_mms_messages([("/m/2", second)], store, modem).imported == 1

    def test_sent_mmsd_copy_is_not_imported_as_inbound(self):
        store = Store.memory()
        modem = FakeModemStore()
        props = {
            "Recipients": ["+1555"], "Date": "2024-06-01T12:00:00+00:00",
            "Status": "sent",
        }
        counts = reconcile_mms_messages([("/m/9", props)], store, modem)
        assert (counts.imported, counts.deleted) == (0, 0)
        assert store.get_all_conversations() == []


# ---- bug_txxt finding 3: inbound SMS/MMS treated as duplicates ----

class _LiveSmsBus:
    """Fake system bus: one inbound SMS plus a one-modem ObjectManager."""

    def __init__(self, props, modem=MODEM):
        self.props = dict(props) if props is not None else None
        self.modem = modem
        self.deletes = []

    def call_sync(self, bus, path, iface, method, params, *rest):
        if method == "GetManagedObjects":
            return _TupleReply(({
                self.modem: {
                    "org.freedesktop.ModemManager1.Modem": {},
                    "org.freedesktop.ModemManager1.Modem.Messaging": {},
                },
            },))
        if method == "GetAll":
            if self.props is None:
                raise OSError("gone")
            return _TupleReply((dict(self.props),))
        if method == "Delete":
            self.deletes.append(params.unpack()[0])
            return _TupleReply(())
        raise AssertionError(method)


def _live_props(number, text, stamp="2024-06-01T12:00:00+00:00"):
    return {
        "Number": f"tel:{number}", "Text": text,
        "State": 3, "PduType": 1, "Timestamp": stamp,
    }


class TestLiveDuplicateScope:
    def _daemon(self, store, bus, **kwargs):
        posts = FakeNotifyBus()
        pipe = InboundPipeline(store, notify_bus=posts)
        under_test = daemon.Daemon(
            store, pipeline=pipe,
            system_bus_fn=lambda: bus, session_bus_fn=lambda: None,
        )
        return under_test, posts

    def test_incoming_matching_my_outgoing_is_stored(self):
        store = Store.memory()
        store.persist_outgoing_sms("+1555", "ok", now_millis=1717243200000)
        bus = _LiveSmsBus(_live_props("+1555", "ok"))
        daemon, posts = self._daemon(store, bus)
        modem = FakeModemStore()
        counts = reconcile_records(
            [record("/s/1", "+1555", "ok")], store, modem,
        )
        assert counts.imported == 1
        assert bodies(store) == ["ok", "ok"]

    def test_repeat_text_twice_both_kept_live(self):
        store = Store.memory()
        bus = _LiveSmsBus(_live_props("+1555", "again"))
        daemon, posts = self._daemon(store, bus)
        daemon._fetch_and_handle("/s/1")
        daemon._fetch_and_handle("/s/2")
        assert bodies(store) == ["again", "again"]
        assert len(posts.posts) == 2

    def test_outgoing_does_not_swallow_incoming_live(self):
        store = Store.memory()
        store.persist_outgoing_sms("+1555", "ok", now_millis=1717243200000)
        bus = _LiveSmsBus(_live_props("+1555", "ok"))
        daemon, posts = self._daemon(store, bus)
        daemon._fetch_and_handle("/s/9")
        assert bodies(store) == ["ok", "ok"]
        assert len(posts.posts) == 1

    def test_flagged_row_still_redeletes_without_dup(self):
        store = Store.memory()
        mid = store.persist_inbound_sms("+1555", "again", 1717243200000)
        store.mark_pending_delete(mid)
        bus = _LiveSmsBus(_live_props("+1555", "again"))
        daemon, posts = self._daemon(store, bus)
        daemon._fetch_and_handle("/s/1")
        assert bodies(store) == ["again"]
        assert posts.posts == []
        assert bus.deletes == ["/s/1"]
        assert store.pending_delete_rows() == []

    def test_mms_sender_wins_over_recipients(self):
        store = Store.memory()
        modem = FakeModemStore()
        props = {
            "Sender": "+15550100999",
            "Recipients": ["+15550100000"],
            "Date": "2024-06-01T12:00:00+00:00",
            "Status": "received",
        }
        assert reconcile_mms_messages([("/m/1", props)], store, modem).imported == 1
        conv = store.get_all_conversations()[0]
        assert "15550100999" in conv.participant_addresses
        assert "15550100000" not in conv.participant_addresses


# ---- bug_txxt finding 21: the same SMS can be sent twice ----

class TestRetryRace:
    def test_new_send_is_not_due_until_base_delay(self):
        from txxt.daemon import RETRY_BASE_DELAY_MS

        now = [1_000_000]
        queue = RetryQueue(now_millis=lambda: now[0])
        queue.add("+1555", "hi", 7)
        assert queue.due() == []
        now[0] += RETRY_BASE_DELAY_MS
        assert len(queue.due()) == 1

    def test_concurrent_pumps_send_once(self):
        import threading

        store = Store.memory()
        store.persist_outgoing_sms("+1555", "once", now_millis=9)
        sent = []
        gate = threading.Barrier(2)

        def _sender(bus, modem, address, body):
            try:
                gate.wait(timeout=2)
            except threading.BrokenBarrierError:
                pass
            sent.append(body)
            import time as _t
            _t.sleep(0.2)
            return True, "/s/1"

        under_test = daemon.Daemon(
            store,
            system_bus_fn=FakeBusForPump,
            session_bus_fn=lambda: None,
            sender=_sender,
            retry=RetryQueue(),
        )
        under_test.startup_reconcile()
        workers = [
            threading.Thread(target=under_test.pump_retry) for _ in range(2)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=30)
        assert sent == ["once"]


# ---- bug_txxt finding 22: modem path cached forever ----

class TestModemPathCache:
    def _daemon(self, store, objects, sender):
        bus_holder = {"bus": None}

        class _Bus(_LiveSmsBus):
            def call_sync(self, bus, path, iface, method, params, *rest):
                if method == "GetManagedObjects":
                    return _TupleReply((dict(objects),))
                return super().call_sync(bus, path, iface, method, params, *rest)

        bus_holder["bus"] = _Bus(None)
        return daemon.Daemon(
            store, system_bus_fn=lambda: bus_holder["bus"],
            session_bus_fn=lambda: None, sender=sender,
        )

    def test_failed_send_clears_cached_path(self):
        store = Store.memory()
        modem_a = "/org/freedesktop/ModemManager1/Modem/0"
        objects = {modem_a: {"org.freedesktop.ModemManager1.Modem": {}}}
        daemon = self._daemon(store, objects, lambda b, m, a, t: (False, "boom"))
        daemon.send_message("+1555", "hi")
        assert daemon._modem_path is None

    def test_retry_resolves_new_path_after_reset(self):
        store = Store.memory()
        modem_a = "/org/freedesktop/ModemManager1/Modem/0"
        modem_b = "/org/freedesktop/ModemManager1/Modem/1"
        seen = []
        objects = {modem_a: {"org.freedesktop.ModemManager1.Modem": {}}}

        def _sender(bus, modem, address, body):
            seen.append(modem)
            return (False, "boom") if modem == modem_a else (True, "/s/9")

        daemon = self._daemon(store, objects, _sender)
        daemon.send_message("+1555", "hi")
        assert daemon._modem_path is None
        objects.clear()
        objects[modem_b] = {"org.freedesktop.ModemManager1.Modem": {}}
        now = [0]
        daemon._retry = RetryQueue(now_millis=lambda: now[0])
        daemon.startup_reconcile()
        now[0] += 30_000
        counts = daemon.pump_retry()
        assert counts.resent == 1
        assert seen[-1] == modem_b


# ---- bug_txxt finding 30: block surface + mute ----

class TestBlockSurfaceAndMute:
    def test_blocked_keeps_archived_thread_hidden(self):
        store = Store.memory()
        filt = InboundFilter(blocked_addresses={"+1555"})
        pipe = pipeline(store, filter_provider=lambda: filt)
        first = pipe.handle_sms("+1555", "first")
        row = store.get_message(first.message_id)
        conv = store.get_conversation(row.conversation_id)
        flags = conv.flags()
        store.set_flags(conv.id, flags.archive())
        assert store.get_conversation(conv.id).is_archived is True
        outcome = pipe.handle_sms("+1555", "second")
        assert outcome.disposition is MessageDisposition.BLOCK
        assert store.get_conversation(conv.id).is_archived is True

    def test_muted_thread_stores_without_notifying(self):

        store = Store.memory()
        bus = FakeNotifyBus()
        pipe = pipeline(store, notify_bus=bus)
        outcome = pipe.handle_sms("+1555", "hello")
        conv = store.get_conversation(outcome.conversation_id)
        flags = conv.flags()
        store.set_flags(conv.id, flags.mute())
        assert len(bus.posts) == 1
        outcome2 = pipe.handle_sms("+1555", "quiet hello")
        assert outcome2.message_id is not None
        assert len(bus.posts) == 1
        assert outcome2.notified is False


def test_daemon_module_has_no_gtk():
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "src" / "txxt" / "daemon.py")
    imports = [
        line for line in source.read_text(encoding="utf-8").splitlines()
        if re.match(r"\s*(import|from)\s", line)
    ]
    assert not any(re.search(r"\b(Gtk|Adw|Gdk)\b", line) for line in imports)
