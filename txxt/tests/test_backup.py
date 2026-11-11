"""L2 backup/import tests: ports of the Android backup suite.

Each section cites its Android source. Fixtures mirror the Android
samples so the interchange behavior is pinned, not just the shape:

- ``BackupJsonTest`` / core ``BackupExportTest`` -> TestCanonicalCodec
- ``ConversationExporterTest`` -> TestBuildBackup
- ``RestoreIdempotencyTest`` -> TestRestoreIdempotency
- ``MapperTest`` -> TestRowMappers (adapted: the Linux store rows ARE the
  entities, so the round-trips run through real SQLite)
- ``SmsBackupCodecTest`` -> TestSmsCodec
- ``SmsExportBudgetTest`` -> TestExportBudget
- ``PlatformSmsImportTest`` -> TestPlatformMatch
- ``SettingsBackupTest`` (settings-through-JSON half) -> TestSettingsBlocklist
- L2-only behavior (transactions, photos, XML, interchange) -> TestImportExport
"""

import base64
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from txxt import backup
from txxt.backup import BackupError
from txxt.store import Store

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def sample_backup_data():
    return {
        "version": backup.BACKUP_VERSION,
        "messages": [
            {"id": 1, "threadId": 10, "address": "+15551234567",
             "body": "hello", "date": 1700000000000},
            {"id": 2, "threadId": 10, "address": "+15551234567",
             "body": "world", "date": 1700000001000},
        ],
        "settings": {"quietHoursStart": "22:00", "quietHoursEnd": "07:00"},
        "blocklist": ["+15559998888", "spam"],
        "starred": ["+15551234567"],
    }


def sample_sms_payload():
    return backup.SmsBackupPayload(
        sms=(
            backup.SmsRecord(address="+15551234567", date=1700000000000,
                             date_sent=1700000000500, type=1, read=True,
                             body="hello", thread_id=10),
            backup.SmsRecord(address="+15559998888", date=1700000001000,
                             date_sent=1700000001000, type=2, read=False,
                             body="world", thread_id=11),
        ),
        mms=(
            backup.MmsRecord(
                mms_id=42,
                addresses=("+15551234567", "+15550001111"),
                date=1700000002000,
                read=True,
                message_box=1,
                thread_id=10,
                text_parts=(backup.MmsTextPart(
                    content_type="text/plain", text="a caption"),),
                parts=(backup.MmsPartRef(
                    part_id=7, ext="jpg", content_type="image/jpeg"),),
            ),
        ),
    )


SBR_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<smses count="3">
<sms protocol="0" address="+15551234567" date="1700000000000" type="1" subject="null" body="hello" toa="null" sc_toa="null" service_center="null" read="1" status="-1" locked="0" />
<sms protocol="0" address="+15551234567" date="1700000001000" type="2" subject="null" body="on my way" toa="null" sc_toa="null" service_center="null" read="1" status="-1" locked="0" />
<mms date="1700000002" msg_box="1" read="1">
<parts>
<part seq="0" ct="text/plain" text="a caption" />
<part seq="1" ct="image/jpeg" data="AQIDBAU=" />
</parts>
<addrs>
<addr address="insert-address-token" type="137" charset="106" />
<addr address="+15551234567" type="137" charset="106" />
</addrs>
</mms>
</smses>"""


def dumped_store(store):
    convs = sorted(
        (row.id, row.participant_addresses) for row in store.get_all_conversations()
    )
    msgs = []
    for row in store.get_all_conversations():
        for item in store.get_messages_for_conversation(row.id):
            msgs.append((item.id, item.conversation_id, item.direction,
                         item.transport, item.body, item.timestamp_millis,
                         item.sender_address, item.is_read))
    return (convs, sorted(msgs), sorted(store.blocklist_list()),
            sorted(store.starred_list()))


class TestCanonicalCodec:
    """Port of ``BackupJsonTest`` (plus the distinct ``BackupExportTest`` bits)."""

    def test_round_trip_reproduces_the_full_backup_payload(self):
        data = sample_backup_data()
        assert backup.deserialize(backup.serialize(data)) == data

    def test_round_trip_preserves_each_section_exactly(self):
        data = sample_backup_data()
        back = backup.deserialize(backup.serialize(data))
        assert back["messages"] == data["messages"]
        assert back["settings"] == data["settings"]
        assert back["blocklist"] == data["blocklist"]
        assert back["starred"] == data["starred"]

    def test_empty_sections_round_trip(self):
        data = {"version": 1, "messages": [], "settings": {},
                "blocklist": [], "starred": []}
        assert backup.deserialize(backup.serialize(data)) == data

    def test_escaped_characters_round_trip(self):
        data = sample_backup_data()
        data["messages"] = [
            {"id": 7, "threadId": 1, "address": 'a"b\\c',
             "body": "line1\nline2\ttab", "date": 1},
        ]
        data["settings"] = {"note": 'quote " and slash \\'}
        assert backup.deserialize(backup.serialize(data)) == data

    def test_serialize_emits_the_canonical_validating_format_shape(self):
        text = backup.serialize(sample_backup_data())
        assert f'"version":{backup.BACKUP_VERSION}' in text
        assert '"messages":[{"id":1,"threadId":10' in text
        assert '"settings":{"quietHoursStart":"22:00"' in text
        assert '"blocklist":["+15559998888","spam"]' in text
        assert '"starred":["+15551234567"]' in text

    def test_wrong_version_is_rejected(self):
        text = backup.serialize(sample_backup_data()).replace(
            f'"version":{backup.BACKUP_VERSION}',
            f'"version":{backup.BACKUP_VERSION + 1}',
        )
        with pytest.raises(BackupError, match="not supported"):
            backup.deserialize(text)

    def test_missing_version_is_rejected(self):
        with pytest.raises(BackupError, match="version"):
            backup.deserialize('{"messages":[],"settings":{},"blocklist":[],"starred":[]}')

    def test_negative_message_id_is_rejected(self):
        text = backup.serialize(sample_backup_data()).replace('"id":1,', '"id":-1,', 1)
        with pytest.raises(BackupError, match="message.id"):
            backup.deserialize(text)

    def test_negative_thread_id_is_rejected(self):
        text = backup.serialize(sample_backup_data()).replace(
            '"threadId":10,', '"threadId":-10,', 1)
        with pytest.raises(BackupError, match="message.threadId"):
            backup.deserialize(text)

    def test_negative_date_is_rejected(self):
        text = backup.serialize(sample_backup_data()).replace(
            '"date":1700000000000', '"date":-1700000000000', 1)
        with pytest.raises(BackupError, match="message.date"):
            backup.deserialize(text)

    def test_non_numeric_id_is_rejected(self):
        text = backup.serialize(sample_backup_data()).replace('"id":1,', '"id":"1",', 1)
        with pytest.raises(BackupError, match="message.id"):
            backup.deserialize(text)

    def test_malformed_json_is_rejected(self):
        with pytest.raises(BackupError):
            backup.deserialize("{not json")

    def test_trailing_garbage_after_the_payload_is_rejected(self):
        with pytest.raises(BackupError, match="trailing"):
            backup.deserialize(backup.serialize(sample_backup_data()) + " trailing")

    def test_non_object_root_is_rejected(self):
        with pytest.raises(BackupError, match="root"):
            backup.deserialize("[1,2,3]")

    def test_rejections_are_value_error_subtypes(self):
        with pytest.raises(BackupError):
            backup.deserialize("{}")
        with pytest.raises(ValueError):
            backup.deserialize("{}")

    def test_reimport_of_the_same_payload_is_a_noop(self):
        text = backup.serialize(sample_backup_data())
        assert backup.deserialize(text) == backup.deserialize(text)

    def test_supported_version_imports(self):
        assert backup.deserialize(
            backup.serialize(sample_backup_data()))["version"] == backup.BACKUP_VERSION


def live_store():
    store = Store.memory()
    first = store.persist_inbound_sms("+15551234567", "hello", 1700000000000)
    second = store.persist_inbound_sms("+15551234567", "world", 1700000001000)
    return store, first, second


class TestBuildBackup:
    """Port of ``ConversationExporterTest`` (minus the SAF/Activity wire-in)."""

    def _rows(self, store):
        convs = store.get_all_conversations()
        msgs = []
        for conv in convs:
            msgs.extend(store.get_messages_for_conversation(conv.id))
        return convs, msgs

    def test_messages_map_conversation_id_to_thread_id_and_preserve_ids(self):
        store = Store.memory()
        cid_a = store.find_or_create_conversation("+15551234567")
        cid_b = store.find_or_create_conversation("+15559998888")
        first = store.persist_inbound_sms("+15551234567", "hello", 1700000000000)
        second = store.persist_inbound_sms("+15559998888", "other", 1700000001000)
        convs, msgs = self._rows(store)
        export = backup.build_backup(convs, msgs, {"quietHoursStart": "22:00"},
                                     ["spam"], ["+15551234567"])
        assert export.backup["version"] == backup.BACKUP_VERSION
        by_id = {item["id"]: item for item in export.backup["messages"]}
        assert by_id[first]["threadId"] == cid_a
        assert by_id[second]["threadId"] == cid_b
        assert export.backup["settings"] == {"quietHoursStart": "22:00"}
        assert export.backup["blocklist"] == ["spam"]
        assert export.backup["starred"] == ["+15551234567"]

    def test_outgoing_message_falls_back_to_the_conversation_participant(self):
        store = Store.memory()
        cid = store.find_or_create_conversation("+15551234567")
        store.persist_outgoing_sms("+15551234567", "sent by me", 1700000000000)
        convs, msgs = self._rows(store)
        export = backup.build_backup(convs, msgs, {}, [], [])
        assert export.backup["messages"][0]["address"] == (
            store.get_conversation(cid).primary_dialable())
        assert export.backup["messages"][0]["threadId"] == cid

    def test_messages_with_a_media_path_are_counted_as_photos_not_in_the_json(self):
        from txxt.store import ConversationRow, MessageRow
        conv = ConversationRow(id=10, participant_addresses="+15551234567")
        msgs = [
            MessageRow(id=1, conversation_id=10, body="p1",
                       timestamp_millis=1700000000000, media_path="/data/p1.jpg"),
            MessageRow(id=2, conversation_id=10, body="p2",
                       timestamp_millis=1700000001000, media_path="/data/p2.jpg"),
            MessageRow(id=3, conversation_id=10, body="plain text",
                       timestamp_millis=1700000002000),
        ]
        export = backup.build_backup([conv], msgs, {}, [], [])
        assert export.photo_count == 2
        assert len(export.backup["messages"]) == 3

    def test_no_media_path_means_zero_photos_left_behind(self):
        store, _, _ = live_store()
        convs, msgs = self._rows(store)
        assert backup.build_backup(convs, msgs, {}, [], []).photo_count == 0

    def test_exported_payload_round_trips_through_the_codec(self):
        store, _, _ = live_store()
        convs, msgs = self._rows(store)
        export = backup.build_backup(convs, msgs, {"quietHoursStart": "22:00"},
                                     ["spam"], ["+15551234567"])
        assert backup.deserialize(backup.serialize(export.backup)) == export.backup

    def test_exported_at_is_an_iso8601_instant_of_the_export_time(self):
        before = datetime.now(timezone.utc)
        store, _, _ = live_store()
        convs, msgs = self._rows(store)
        export = backup.build_backup(convs, msgs, {}, [], [])
        after = datetime.now(timezone.utc)
        parsed = datetime.fromisoformat(export.exported_at)
        assert before <= parsed <= after

    def test_an_exported_conversation_restores_onto_the_same_message_entities(self):
        store, _, _ = live_store()
        convs, msgs = self._rows(store)
        export = backup.build_backup(convs, msgs, {"quietHoursStart": "22:00"},
                                     ["spam"], ["+15551234567"])
        plan = backup.plan_restore(backup.deserialize(backup.serialize(export.backup)))
        assert {item.backup_id for item in plan.messages} == {item.id for item in msgs}
        assert {item.thread_id for item in plan.messages} == {conv.id for conv in convs}
        assert len(plan.threads) == len(convs)


class TestRestoreIdempotency:
    """Port of ``RestoreIdempotencyTest`` over the real store."""

    def test_plan_is_a_pure_function_of_the_payload(self):
        data = backup.deserialize(backup.serialize(sample_backup_data()))
        assert backup.plan_restore(data) == backup.plan_restore(data)

    def test_plan_maps_each_thread_to_one_conversation_and_each_message(self):
        data = sample_backup_data()
        data["messages"].append(
            {"id": 3, "threadId": 20, "address": "+15559998888",
             "body": "other thread", "date": 1700000002000})
        plan = backup.plan_restore(backup.deserialize(backup.serialize(data)))
        assert {thread.thread_id for thread in plan.threads} == {10, 20}
        assert len(plan.messages) == 3

    def test_restoring_the_same_payload_twice_yields_the_same_final_state(self):
        store = Store.memory()
        text = backup.serialize(sample_backup_data())
        backup.import_backup_json(store, text)
        first = dumped_store(store)
        result = backup.import_backup_json(store, text)
        assert dumped_store(store) == first
        assert result.imported == 0
        assert result.skipped == 2

    def test_restoring_an_empty_payload_is_a_noop_and_stays_a_noop(self):
        store = Store.memory()
        text = backup.serialize({"version": 1, "messages": [], "settings": {},
                                 "blocklist": [], "starred": []})
        backup.import_backup_json(store, text)
        backup.import_backup_json(store, text)
        assert dumped_store(store) == ([], [], [], [])

    def test_rerunning_after_a_partial_restore_converges_to_the_same_state(self):
        store = Store.memory()
        text = backup.serialize(sample_backup_data())
        backup.import_backup_json(store, text)
        full = dumped_store(store)
        for conv in store.get_all_conversations():
            store.delete_messages_for_conversation(conv.id)
        result = backup.import_backup_json(store, text)
        assert dumped_store(store) == full
        assert result.imported == 2

    def test_restore_preserves_backup_ids_when_free(self):
        store = Store.memory()
        backup.import_backup_json(store, backup.serialize(sample_backup_data()))
        assert store.get_message(1) is not None
        assert store.get_message(2) is not None
        assert store.get_message(1).conversation_id == 10

    def test_restore_never_overwrites_a_live_row_on_id_collision(self):
        store = Store.memory()
        live_id = store.persist_inbound_sms("+19998887777", "live text", 1700000000000)
        result = backup.import_backup_json(store, backup.serialize(sample_backup_data()))
        assert store.get_message(live_id).body == "live text"
        bodies = [item.body for conv in store.get_all_conversations()
                  for item in store.get_messages_for_conversation(conv.id)]
        assert "live text" in bodies
        assert "hello" in bodies and "world" in bodies
        assert result.imported == 2


class TestRowMappers:
    """Adapted port of ``MapperTest``: rows round-trip through SQLite."""

    def test_message_maps_to_a_row_and_back_losslessly(self):
        store = Store.memory()
        cid = store.find_or_create_conversation("+15550001111")
        mid = store.persist_outgoing_mms("+15550001111", "see attached",
                                         media_path="/m/1.jpg", now_millis=1234)
        row = store.get_message(mid)
        assert row.conversation_id == cid
        assert row.direction == "OUTGOING"
        assert row.transport == "MMS"
        assert row.body == "see attached"
        assert row.timestamp_millis == 1234
        assert row.media_path == "/m/1.jpg"
        assert row.is_read is True
        assert row.sent is False

    def test_message_row_stores_enums_as_names(self):
        store, first, _ = live_store()
        row = store.get_message(first)
        assert row.direction == "INCOMING"
        assert row.transport == "SMS"

    def test_conversation_row_joins_and_splits_participant_addresses(self):
        from txxt.store import ConversationRow
        store = Store.memory()
        store.upsert_conversation(ConversationRow(
            id=5, participant_addresses="+15550001111" + chr(1) + "+15550002222"))
        assert store.get_conversation(5).addresses() == ["+15550001111", "+15550002222"]

    def test_self_only_conversation_stores_empty_address_string(self):
        from txxt.store import ConversationRow
        store = Store.memory()
        store.upsert_conversation(ConversationRow(id=1, participant_addresses=""))
        assert store.get_conversation(1).addresses() == []

    def test_flags_map_to_a_row_and_back_losslessly(self):
        from txxt import conversations_core as core
        from txxt.store import ConversationRow
        store = Store.memory()
        store.upsert_conversation(ConversationRow(id=1, participant_addresses="x"))
        flags = core.ConversationFlags(
            is_pinned=True, is_archived=True, is_muted=False,
            is_quarantined=True, muted_until_millis=99,
            sort_order=core.ConversationSortOrder.UNREAD_FIRST)
        assert store.set_flags(1, flags) is True
        assert store.get_conversation(1).flags() == flags

    def test_default_flags_map_to_default_row_fields(self):
        from txxt.store import ConversationRow
        store = Store.memory()
        store.upsert_conversation(ConversationRow(id=1, participant_addresses="x"))
        row = store.get_conversation(1)
        assert row.is_pinned is False
        assert row.is_archived is False
        assert row.is_quarantined is False
        assert row.muted_until_millis == 0
        assert row.sort_order == "PINNED_FIRST"

    def test_starred_flag_persists_through_the_row(self):
        from txxt.store import ConversationRow
        store = Store.memory()
        store.upsert_conversation(ConversationRow(
            id=1, participant_addresses="x", is_starred=True))
        assert store.get_conversation(1).is_starred is True


class TestSmsCodec:
    """Port of ``SmsBackupCodecTest``."""

    def test_round_trip_reproduces_sms_and_mms_records(self):
        payload = sample_sms_payload()
        assert backup.decode_sms_payload(backup.encode_sms_payload(payload)) == payload

    def test_attachment_references_survive_the_round_trip(self):
        back = backup.decode_sms_payload(backup.encode_sms_payload(sample_sms_payload()))
        part = back.mms[0].parts[0]
        assert part.part_id == 7
        assert part.ext == "jpg"
        assert part.content_type == "image/jpeg"
        assert part.export_path(back.mms[0].mms_id) == "mms/42/7.jpg"

    def test_truncated_and_note_round_trip(self):
        payload = backup.SmsBackupPayload(sms=(), mms=(), truncated=True,
                                          note="dropped 3 oldest attachment(s)")
        back = backup.decode_sms_payload(backup.encode_sms_payload(payload))
        assert back.truncated is True
        assert back.note == "dropped 3 oldest attachment(s)"

    def test_empty_sms_and_mms_lists_round_trip(self):
        payload = backup.SmsBackupPayload(sms=(), mms=())
        assert backup.decode_sms_payload(backup.encode_sms_payload(payload)) == payload

    def test_escaped_characters_round_trip(self):
        payload = backup.SmsBackupPayload(
            sms=(backup.SmsRecord(address='a"b\\c', date=1, date_sent=1, type=1,
                                  read=False, body="line1\nline2\ttab", thread_id=1),),
            mms=())
        back = backup.decode_sms_payload(backup.encode_sms_payload(payload))
        assert back.sms == payload.sms

    def test_mms_with_no_binary_parts_round_trips(self):
        payload = backup.SmsBackupPayload(
            sms=(),
            mms=(backup.MmsRecord(mms_id=1, addresses=("+15551234567",), date=1,
                                  read=True, message_box=2, thread_id=1,
                                  text_parts=(backup.MmsTextPart(
                                      "text/plain", "just text"),)),))
        back = backup.decode_sms_payload(backup.encode_sms_payload(payload))
        assert back == payload
        assert back.mms[0].parts == ()

    def test_malformed_json_is_rejected(self):
        with pytest.raises(BackupError):
            backup.decode_sms_payload("{not json")

    def test_missing_sms_array_is_rejected(self):
        with pytest.raises(BackupError, match="sms"):
            backup.decode_sms_payload('{"mms":[],"truncated":false,"note":null}')

    def test_mime_and_extension_helpers_mirror_android(self):
        assert backup.extension_for_mime("image/jpeg") == "jpg"
        assert backup.extension_for_mime("image/png") == "png"
        assert backup.extension_for_mime("video/3gpp") == "3gp"
        assert backup.extension_for_mime("application/octet-stream") == "bin"
        assert backup.mime_for_extension("jpg") == "image/jpeg"
        assert backup.mime_for_extension("zzz") == "application/octet-stream"
        assert backup.mms_date_to_millis(1700000002) == 1700000002000
        assert backup.mms_date_to_millis(1700000002000) == 1700000002000
        assert backup.millis_to_mms_date_seconds(1700000002000) == 1700000002


class TestExportBudget:
    """Port of ``SmsExportBudgetTest``."""

    def _attachment(self, mms_id, part_id, date_millis, size_bytes):
        return backup.Attachment(backup.AttachmentKey(mms_id, part_id),
                                 date_millis, size_bytes)

    def test_everything_fits_under_budget(self):
        attachments = [self._attachment(1, 1, 100, 10), self._attachment(2, 1, 200, 10)]
        result = backup.select_attachments(0, attachments, 100)
        assert result.truncated is False
        assert result.kept == frozenset({backup.AttachmentKey(1, 1),
                                         backup.AttachmentKey(2, 1)})
        assert result.dropped_count == 0

    def test_over_budget_drops_the_oldest_attachments_first(self):
        oldest = self._attachment(1, 1, 100, 40)
        middle = self._attachment(2, 1, 200, 40)
        newest = self._attachment(3, 1, 300, 40)
        result = backup.select_attachments(0, [oldest, middle, newest], 80)
        assert result.truncated is True
        assert result.kept == frozenset({newest.key, middle.key})
        assert oldest.key not in result.kept
        assert result.dropped_count == 1
        assert result.dropped_bytes == 40

    def test_a_smaller_older_attachment_is_not_kept_just_because_it_fits_a_gap(self):
        newest = self._attachment(1, 1, 500, 1000000000)
        middle = self._attachment(1, 2, 400, 1500000000)
        oldest = self._attachment(1, 3, 300, 300000000)
        result = backup.select_attachments(0, [newest, middle, oldest], 2000000000)
        assert result.truncated is True
        assert result.kept == frozenset({newest.key})
        assert result.dropped_count == 2
        assert oldest.key not in result.kept
        assert middle.key not in result.kept

    def test_base_bytes_count_against_the_budget_before_any_attachment(self):
        result = backup.select_attachments(
            95, [self._attachment(1, 1, 100, 10)], 100)
        assert result.truncated is True
        assert result.kept == frozenset()

    def test_no_attachments_never_truncates(self):
        result = backup.select_attachments(0, [], 10)
        assert result.truncated is False
        assert result.kept == frozenset()

    def test_default_budget_is_2gb(self):
        assert backup.DEFAULT_BUDGET_BYTES == 2 * 1024 * 1024 * 1024


class TestPlatformMatch:
    """Port of ``PlatformSmsImportTest`` over the pure matchers."""

    def _known(self):
        return [backup.KnownMessage("16145550100", 1000000, "hello"),
                backup.KnownMessage("16145550100", 2000000, "again")]

    def test_a_row_already_held_within_the_window_is_not_missing(self):
        rows = [backup.PlatformRow("+1 614 555 0100", 1000000 + 90000, 1, "hello", True),
                backup.PlatformRow("6145550100", 1000000 - 90000, 1, "hello", True)]
        assert backup.find_missing(rows, self._known()) == []

    def test_a_new_body_a_far_off_timestamp_or_a_new_number_is_missing(self):
        new_body = backup.PlatformRow("+16145550100", 1000000, 1, "different", False)
        far_off = backup.PlatformRow("+16145550100", 1000000 + backup.MATCH_WINDOW_MS + 1,
                                     1, "hello", False)
        new_number = backup.PlatformRow("+16145550199", 1000000, 2, "hello", True)
        assert backup.find_missing([new_body, far_off, new_number],
                                   self._known()) == [new_body, far_off, new_number]

    def test_an_imported_dup_is_removed_while_original_and_singles_stay(self):
        moment = 1000000
        late = moment + backup.IMPORTED_ID_GAP_MS + 10000
        participants = {1: "16145550100", 2: "6145550100"}
        original = backup.StoredMessage(moment + 5, 1, "SMS", moment, "hello")
        imported_dup = backup.StoredMessage(late, 2, "SMS", moment + 1000, "hello")
        imported_single = backup.StoredMessage(late + 1, 1, "SMS",
                                               moment - 900000, "older text")
        mms_lookalike = backup.StoredMessage(late + 2, 1, "MMS", moment, "hello")
        assert backup.find_duplicate_ids(
            [original, imported_dup, imported_single, mms_lookalike],
            participants) == [imported_dup.id]


class TestSettingsBlocklist:
    """Applicable half of ``SettingsBackupTest``: stores through the JSON."""

    def test_store_settings_round_trip_through_the_backup_json(self, tmp_path):
        store = Store.memory()
        store.set_setting("txxt_theme", "forest")
        store.set_setting("muted", True)
        store.set_setting("flags", {"a": [1, 2]})
        target = tmp_path / "txxt.json"
        backup.export_backup(store, target)
        text = json.loads(target.read_text(encoding="utf-8"))
        assert text["settings"]["txxt_theme"] == "forest"
        first = Store.memory()
        backup.import_backup_json(first, target.read_text(encoding="utf-8"))
        assert first.get_setting("txxt_theme") == "forest"
        assert first.get_setting("muted") is True
        assert first.get_setting("flags") == {"a": [1, 2]}

    def test_blocklist_kinds_and_starred_round_trip_exactly(self, tmp_path):
        store = Store.memory()
        store.blocklist_add("sender", "+15559998888")
        store.blocklist_add("keyword", "lottery")
        store.blocklist_add("phrase", "free money")
        store.star("+15551234567")
        backup.export_backup(store, tmp_path / "txxt.json")
        first = Store.memory()
        backup.import_backup_json(first, (tmp_path / "txxt.json").read_text())
        assert sorted(first.blocklist_list()) == sorted(store.blocklist_list())
        assert first.starred_list() == ["+15551234567"]

    def test_flat_android_style_blocklist_imports_with_the_digit_heuristic(self):
        store = Store.memory()
        data = sample_backup_data()
        data["blocklist"] = ["+15559998888", "spam"]
        result = backup.import_backup(store, backup.deserialize(backup.serialize(data)))
        assert result.blocklist_entries == 2
        assert ("sender", "+15559998888") in store.blocklist_list()
        assert ("keyword", "spam") in store.blocklist_list()

    def test_partial_settings_merge_leaves_live_only_keys_alone(self):
        store = Store.memory()
        store.set_setting("live_key", "live")
        backup.import_backup_json(store, backup.serialize(sample_backup_data()))
        assert store.get_setting("live_key") == "live"
        assert store.get_setting("quietHoursStart") == "22:00"

    def test_opaque_setting_values_never_break_the_import(self):
        store = Store.memory()
        data = sample_backup_data()
        data["settings"] = {"future": "whatever", "count": "42"}
        result = backup.import_backup(store, backup.deserialize(backup.serialize(data)))
        assert result.settings_applied == 2
        assert store.get_setting("future") == "whatever"


class TestStrictImport:
    """Garbage and future versions are refused with zero rows written."""

    def test_malformed_json_writes_nothing(self):
        store = Store.memory()
        store.persist_inbound_sms("+10000000000", "live", 1700000000000)
        before = dumped_store(store)
        with pytest.raises(BackupError):
            backup.import_backup_json(store, "{nope")
        assert dumped_store(store) == before

    def test_future_version_writes_nothing(self):
        store = Store.memory()
        text = backup.serialize(sample_backup_data()).replace('"version":1', '"version":2')
        with pytest.raises(BackupError, match="not supported"):
            backup.import_backup_json(store, text)
        assert dumped_store(store) == ([], [], [], [])

    def test_range_violation_writes_nothing(self):
        store = Store.memory()
        text = backup.serialize(sample_backup_data()).replace('"id":1,', '"id":-1,', 1)
        with pytest.raises(BackupError):
            backup.import_backup_json(store, text)
        assert dumped_store(store) == ([], [], [], [])

    def test_mid_restore_failure_rolls_back_the_whole_transaction(self):
        store = Store.memory()
        store.set_setting("keep", "live")
        data = sample_backup_data()
        data[backup.BLOCKLIST_DETAILED_KEY] = [{"kind": "nope", "value": "x"}]
        with pytest.raises(BackupError, match="unknown blocklist kind"):
            backup.import_backup(store, backup.deserialize(backup.serialize(data)))
        assert dumped_store(store) == ([], [], [], [])
        assert store.get_setting("keep") == "live"

    def test_malformed_xml_writes_nothing(self):
        store = Store.memory()
        with pytest.raises(BackupError, match="XML"):
            backup.import_sbr_xml(store, "<smses><sms")
        assert dumped_store(store) == ([], [], [], [])

    def test_non_smses_root_is_refused(self):
        store = Store.memory()
        with pytest.raises(BackupError, match="smses"):
            backup.import_sbr_xml(store, "<backup><sms /></backup>")
        assert dumped_store(store) == ([], [], [], [])

    def test_unknown_entry_is_refused(self):
        store = Store.memory()
        with pytest.raises(BackupError, match="unsupported backup entry"):
            backup.import_sbr_xml(store, "<smses><mms2 /></smses>")
        assert dumped_store(store) == ([], [], [], [])


class TestPhotoSiblings:
    """``mms/<id>/`` siblings out of the JSON, counted honestly."""

    def _photo_store(self, tmp_path):
        photo = tmp_path / "shot.jpg"
        photo.write_bytes(b"\xff\xd8fakejpeg")
        store = Store.memory()
        mid = store.persist_outgoing_mms("+15551234567", "[Photo]",
                                         media_path=str(photo), now_millis=1700000000000)
        return store, mid, photo

    def test_export_writes_siblings_and_records_filenames(self, tmp_path):
        store, mid, _ = self._photo_store(tmp_path)
        out = backup.export_backup(store, tmp_path / "backup" / "txxt.json")
        assert out["photo_count"] == 1
        assert out["photo_files"] == 1
        assert out["missing_photos"] == 0
        sibling = tmp_path / "backup" / "mms" / str(mid) / "shot.jpg"
        assert sibling.is_file()
        payload = json.loads((tmp_path / "backup" / "txxt.json").read_text())
        assert payload["messages"][0]["photos"] == [f"mms/{mid}/shot.jpg"]

    def test_export_counts_absent_photo_files_honestly(self, tmp_path):
        store = Store.memory()
        store.persist_outgoing_mms("+15551234567", "[Photo]",
                                   media_path=str(tmp_path / "gone.jpg"),
                                   now_millis=1700000000000)
        out = backup.export_backup(store, tmp_path / "txxt.json")
        assert out["photo_count"] == 1
        assert out["photo_files"] == 0
        assert out["missing_photos"] == 1
        payload = json.loads((tmp_path / "txxt.json").read_text())
        assert "photos" not in payload["messages"][0]

    def test_export_budget_drops_oldest_photos_first(self, tmp_path):
        store = Store.memory()
        for index in range(3):
            photo = tmp_path / f"p{index}.jpg"
            photo.write_bytes(b"y" * 10000)
            store.persist_outgoing_mms("+1555000", "[Photo]", media_path=str(photo),
                                       now_millis=1700000000000 + index)
        out = backup.export_backup(store, tmp_path / "txxt.json", budget_bytes=20000)
        assert out["truncated"] is True
        assert out["dropped_photos"] == 2
        assert out["photo_files"] == 1
        assert out["note"] and "oldest" in out["note"]

    def test_import_restores_photo_bytes_and_sets_mms_transport(self, tmp_path):
        store, mid, _ = self._photo_store(tmp_path)
        backup.export_backup(store, tmp_path / "backup" / "txxt.json")
        media = tmp_path / "media"
        first = Store.memory()
        result = backup.import_backup_json(
            first, (tmp_path / "backup" / "txxt.json").read_text(),
            photo_dir=tmp_path / "backup", media_root=media)
        assert result.photos_restored == 1
        assert result.missing_photos == 0
        row = first.get_message(mid)
        assert row.transport == "MMS"
        assert row.media_path is not None
        assert Path(row.media_path).read_bytes() == b"\xff\xd8fakejpeg"

    def test_import_counts_absent_siblings_honestly_and_keeps_the_text(self, tmp_path):
        store = Store.memory()
        result = backup.import_backup_json(
            store, backup.serialize({
                "version": 1,
                "messages": [{"id": 9, "threadId": 9, "address": "+15551234567",
                              "body": "caption", "date": 1700000000000,
                              "photos": ["mms/9/shot.jpg"]}],
                "settings": {}, "blocklist": [], "starred": []}),
            photo_dir=tmp_path, media_root=tmp_path / "media")
        assert result.imported == 1
        assert result.missing_photos == 1
        assert result.photos_restored == 0
        row = store.get_message(9)
        assert row.body == "caption"
        assert row.transport == "SMS"
        assert row.media_path is None

    def test_export_import_export_is_stable(self, tmp_path):
        store, _, _ = self._photo_store(tmp_path)
        backup.export_backup(store, tmp_path / "one" / "txxt.json")
        middle = Store.memory()
        backup.import_backup_json(
            middle, (tmp_path / "one" / "txxt.json").read_text(),
            photo_dir=tmp_path / "one", media_root=tmp_path / "media")
        backup.export_backup(middle, tmp_path / "two" / "txxt.json")
        first_msgs = json.loads((tmp_path / "one" / "txxt.json").read_text())["messages"]
        second_msgs = json.loads((tmp_path / "two" / "txxt.json").read_text())["messages"]
        assert first_msgs == second_msgs


class TestAndroidInterchange:
    """A literal Android-shaped backup imports with ids preserved."""

    ANDROID_JSON = (
        '{"version":1,"messages":['
        '{"id":41,"threadId":7,"address":"+15551234567","body":"hello","date":1700000000000},'
        '{"id":42,"threadId":7,"address":"+15551234567","body":"world","date":1700000001000}'
        '],"settings":{"quietHoursStart":"22:00"},'
        '"blocklist":["+15559998888"],"starred":["+15551234567"]}'
    )

    def test_android_json_imports_with_ids_and_sections(self):
        store = Store.memory()
        result = backup.import_backup_json(store, self.ANDROID_JSON)
        assert result.imported == 2
        assert result.settings_applied == 1
        assert result.blocklist_entries == 1
        assert result.starred_entries == 1
        assert store.get_message(41).body == "hello"
        assert store.get_message(42).conversation_id == 7
        assert store.get_setting("quietHoursStart") == "22:00"
        assert store.starred_list() == ["+15551234567"]

    def test_android_json_reimports_without_duplicating(self):
        store = Store.memory()
        backup.import_backup_json(store, self.ANDROID_JSON)
        before = dumped_store(store)
        result = backup.import_backup_json(store, self.ANDROID_JSON)
        assert dumped_store(store) == before
        assert result.imported == 0


class TestSmsJsonImport:
    """Suite ``sms.json`` restores through the same upsert path."""

    def test_sms_rows_import_by_type_and_skip_unknown_types(self):
        store = Store.memory()
        result = backup.import_sms_json(
            store, backup.encode_sms_payload(sample_sms_payload()))
        assert result.imported == 3
        bodies = [item.body for conv in store.get_all_conversations()
                  for item in store.get_messages_for_conversation(conv.id)]
        assert "hello" in bodies and "world" in bodies and "a caption" in bodies

    def test_mms_part_file_restores_from_the_export_dir(self, tmp_path):
        export = tmp_path / "export"
        part = export / "mms" / "42" / "7.jpg"
        part.parent.mkdir(parents=True)
        part.write_bytes(b"imagedata")
        store = Store.memory()
        result = backup.import_sms_json(
            store, backup.encode_sms_payload(sample_sms_payload()),
            export_dir=export, media_root=tmp_path / "media")
        assert result.photos_restored == 1
        assert result.missing_photos == 0

    def test_absent_mms_part_counts_as_missing(self, tmp_path):
        store = Store.memory()
        result = backup.import_sms_json(
            store, backup.encode_sms_payload(sample_sms_payload()),
            export_dir=tmp_path / "empty", media_root=tmp_path / "media")
        assert result.imported == 3
        assert result.missing_photos == 1
        assert result.photos_restored == 0


class TestSbrXmlImport:
    """SMS Backup & Restore XML (``smses/sms``, ``mms/parts/part``)."""

    def test_sms_and_mms_import_with_photo_bytes(self, tmp_path):
        store = Store.memory()
        result = backup.import_sbr_xml(store, SBR_XML, media_root=tmp_path / "media")
        assert result.imported == 3
        assert result.photos_restored == 1
        assert result.missing_photos == 0
        rows = [item for conv in store.get_all_conversations()
                for item in store.get_messages_for_conversation(conv.id)]
        by_body = {item.body: item for item in rows}
        assert by_body["hello"].direction == "INCOMING"
        assert by_body["on my way"].direction == "OUTGOING"
        assert by_body["a caption"].transport == "MMS"
        assert by_body["a caption"].timestamp_millis == 1700000002000
        assert Path(by_body["a caption"].media_path).read_bytes() == bytes([1, 2, 3, 4, 5])

    def test_unknown_sms_types_are_skipped(self):
        store = Store.memory()
        text = ('<smses count="1"><sms address="+15550001111" date="1700000000000"'
                ' type="3" body="draft" read="1" /></smses>')
        assert backup.import_sbr_xml(store, text).imported == 0
        assert dumped_store(store) == ([], [], [], [])

    def test_mms_photo_from_a_sibling_file(self, tmp_path):
        sibling = tmp_path / "pic.jpg"
        sibling.write_bytes(b"siblingbytes")
        text = ('<smses count="1"><mms date="1700000002" msg_box="1" read="1">'
                '<parts><part seq="0" ct="text/plain" text="hi" />'
                '<part seq="1" ct="image/jpeg" filename="pic.jpg" /></parts>'
                '<addrs><addr address="+15551234567" type="137" /></addrs>'
                '</mms></smses>')
        store = Store.memory()
        result = backup.import_sbr_xml(store, text, photo_dir=tmp_path,
                                       media_root=tmp_path / "media")
        assert result.photos_restored == 1
        assert result.missing_photos == 0

    def test_mms_photo_without_data_or_sibling_counts_as_missing(self, tmp_path):
        text = ('<smses count="1"><mms date="1700000002" msg_box="1" read="1">'
                '<parts><part seq="0" ct="text/plain" text="hi" />'
                '<part seq="1" ct="image/jpeg" /></parts>'
                '<addrs><addr address="+15551234567" type="137" /></addrs>'
                '</mms></smses>')
        store = Store.memory()
        result = backup.import_sbr_xml(store, text, media_root=tmp_path / "media")
        assert result.imported == 1
        assert result.missing_photos == 1
        assert result.photos_restored == 0

    def test_double_import_never_duplicates(self, tmp_path):
        store = Store.memory()
        backup.import_sbr_xml(store, SBR_XML, media_root=tmp_path / "media")
        before = dumped_store(store)
        result = backup.import_sbr_xml(store, SBR_XML, media_root=tmp_path / "media")
        assert dumped_store(store) == before
        assert result.imported == 0

    def test_fixture_file_imports(self, tmp_path):
        text = (FIXTURES / "sbr_sample.xml").read_text(encoding="utf-8")
        store = Store.memory()
        result = backup.import_sbr_xml(store, text, media_root=tmp_path / "media")
        assert result.imported == 3
        assert result.photos_restored == 1

    def test_base64_blob_decodes_to_bytes(self):
        assert base64.b64decode("AQIDBAU=") == bytes([1, 2, 3, 4, 5])
