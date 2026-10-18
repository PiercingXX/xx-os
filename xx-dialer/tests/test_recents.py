"""Lane 4 headless tests: pure recents logic, store seams, controller."""
from datetime import datetime, timezone

from xx_dialer import config as app_config
from xx_dialer import contacts_client, recents
from xx_dialer.store import (
    CALL_TYPE_INCOMING,
    CALL_TYPE_MISSED,
    CALL_TYPE_OUTGOING,
    CallRow,
    MirrorRow,
    ScreenRow,
    Store,
)

WALL = 1789651200000

ADA = "+14155550100"
BO = "+14155550200"


def _call(key, date, number, call_type=CALL_TYPE_INCOMING,
          duration=0, name=None):
    return recents.PlatformCall(key=key, time_ms=date, raw=number,
                                e164=number, cached_name=name,
                                missed=False,
                                outgoing=call_type == CALL_TYPE_OUTGOING,
                                duration_s=duration)


def _log(row_id, at, e164, verdict, reason="", mode="enforcing",
         answered=False):
    return recents.ScreenEntry(row_id=row_id, at=at, e164=e164,
                               verdict=verdict, reason=reason,
                               mode=mode, answered=answered)


# ---- merge ---------------------------------------------------------------

def test_merge_pairs_closest_first():
    calls = [_call(1, WALL, ADA), _call(2, WALL + 30_000, ADA)]
    logs = [_log(10, WALL + 31_000, ADA, "Silence", "unknown caller"),
            _log(11, WALL + 1_000, ADA, "Ring", "starred")]
    merged = recents.merge(calls, logs)
    assert [row.key for row in merged] == [2, 1]
    by_key = {row.key: row for row in merged}
    assert by_key[2].screen_id == 10
    assert by_key[1].screen_id == 11
    assert by_key[2].glyph == recents.GLYPH_SILENCED
    assert by_key[1].glyph == recents.GLYPH_RING


def test_merge_outside_proximity_stays_unscreened():
    calls = [_call(1, WALL, ADA)]
    logs = [_log(10, WALL + recents.PROXIMITY_MS + 1, ADA,
                 "Silence", "unknown caller")]
    (row,) = recents.merge(calls, logs)
    assert row.glyph == recents.GLYPH_NONE
    assert row.screen_id is None


def test_merge_withheld_pairs_but_never_groups():
    calls = [_call(1, WALL, None), _call(2, WALL + 1_000, None)]
    for call in calls:
        call.e164 = None
    logs = [_log(10, WALL + 500, None, "Silence", "hidden caller")]
    merged = recents.merge(calls, logs)
    assert merged[0].screen_id == 10
    assert merged[1].screen_id is None
    grouped = recents.group(merged, True, set())
    assert len(grouped) == 2
    assert all(isinstance(item, recents.Single) for item in grouped)


def test_glyph_of_unknown_verdict_is_none():
    assert recents.glyph_of("Bogus") == recents.GLYPH_NONE
    assert recents.glyph_of(None) == recents.GLYPH_NONE


# ---- grouping --------------------------------------------------------------

def _merged_row(key, date, e164):
    return recents.MergedCall(key=key, e164=e164, raw=e164,
                              cached_name=None, display_name=None,
                              time_ms=date, missed=False,
                              outgoing=False, duration_s=0)


def test_group_collapses_consecutive_runs():
    rows = [_merged_row(1, WALL + 2, ADA), _merged_row(2, WALL + 1, ADA),
            _merged_row(3, WALL, BO)]
    grouped = recents.group(rows, True, set())
    assert isinstance(grouped[0], recents.Group)
    assert grouped[0].identity == ADA
    assert [row.key for row in grouped[0].calls] == [1, 2]
    assert isinstance(grouped[1], recents.Single)


def test_group_off_and_expanded():
    rows = [_merged_row(1, WALL + 1, ADA), _merged_row(2, WALL, ADA)]
    assert len(recents.group(rows, False, set())) == 2
    (grouped,) = recents.group(rows, True, {ADA})
    assert grouped.expanded is True


# ---- filters / chips / lines -------------------------------------------------

def test_filters():
    silenced = _merged_row(1, WALL, ADA)
    silenced.glyph = recents.GLYPH_SILENCED
    blocked = _merged_row(2, WALL, BO)
    blocked.glyph = recents.GLYPH_BLOCKED
    missed = _merged_row(3, WALL, "+1999")
    missed.missed = True
    assert recents.passes(recents.FILTER_ALL, silenced)
    assert recents.passes(recents.FILTER_SILENCED, silenced)
    assert not recents.passes(recents.FILTER_SILENCED, blocked)
    assert recents.passes(recents.FILTER_BLOCKED, blocked)
    assert recents.passes(recents.FILTER_MISSED, missed)
    assert not recents.passes(recents.FILTER_MISSED, silenced)


def test_verdict_lines():
    assert recents.verdict_line(
        recents.GLYPH_SILENCED, "unknown caller",
        False) == "Silenced: unknown caller"
    assert recents.verdict_line(
        recents.GLYPH_BLOCKED, "blocklist",
        False) == "Blocked: blocklist"
    assert recents.verdict_line(recents.GLYPH_SILENCED, "",
                                False) == "Silenced: policy"
    assert recents.verdict_line(recents.GLYPH_RING, "starred",
                                False) == "Rang: starred"
    assert recents.verdict_line(recents.GLYPH_RING, "",
                                False) == ""
    assert recents.verdict_line(recents.GLYPH_NONE, "x",
                                False) == ""


def test_verdict_line_observed_suffix_only_on_quiet_rows():
    watched = recents.verdict_line(recents.GLYPH_SILENCED,
                                   "unknown caller", True)
    assert watched.endswith("watching — it rang")
    assert recents.verdict_line(recents.GLYPH_RING, "starred",
                                True) == "Rang: starred"


def test_direction_chips():
    row = _merged_row(1, WALL, ADA)
    assert recents.direction_chip(row) == "Incoming"
    row.outgoing = True
    assert recents.direction_chip(row) == "Outgoing"
    row.outgoing = False
    row.missed = True
    assert recents.direction_chip(row) == "Missed"
    row.glyph = recents.GLYPH_SILENCED
    assert recents.direction_chip(row) == "Missed"
    row.missed = False
    assert recents.direction_chip(row) == "Silenced"
    row.glyph = recents.GLYPH_BLOCKED
    row.missed = True
    assert recents.direction_chip(row) == "Blocked"


# ---- missed / unread -----------------------------------------------------------

def test_is_missed_call():
    assert recents.is_missed_call(CALL_TYPE_OUTGOING, 0, None) is False
    assert recents.is_missed_call(CALL_TYPE_MISSED, 0, None) is True
    assert recents.is_missed_call(CALL_TYPE_INCOMING, 0, None) is True
    assert recents.is_missed_call(CALL_TYPE_INCOMING, 0, True) is False
    assert recents.is_missed_call(CALL_TYPE_INCOMING, 0,
                                  False) is True
    assert recents.is_missed_call(CALL_TYPE_INCOMING, 12,
                                  None) is False
    assert recents.is_missed_call(0, 0, None) is False


def test_unread_missed_and_clear_rule():
    rows = [_merged_row(1, WALL + 5_000, ADA),
            _merged_row(2, WALL - 5_000, BO)]
    rows[0].missed = True
    rows[1].missed = True
    assert recents.unread_missed(rows, WALL) == 1
    assert recents.unread_missed(rows, WALL + 10_000) == 0
    assert recents.should_clear(True, False) is True
    assert recents.should_clear(False, True) is True
    assert recents.should_clear(False, False) is False


# ---- batcher / missed copy / digest ----------------------------------------------

def test_batcher_single_then_batch_then_single():
    first = recents.batch_next(recents.BatchState(), WALL)
    assert isinstance(first, recents.BatchSingle)
    second = recents.batch_next(first.state, WALL + 30_000)
    assert isinstance(second, recents.BatchBatch)
    assert second.count == 2
    third = recents.batch_next(second.state,
                               WALL + 30_000 + recents.BATCH_WINDOW_MS
                               + 1)
    assert isinstance(third, recents.BatchSingle)
    assert third.state.count == 1


def test_batcher_boundary_is_inside():
    first = recents.batch_next(recents.BatchState(), WALL)
    again = recents.batch_next(first.state,
                               WALL + recents.BATCH_WINDOW_MS)
    assert isinstance(again, recents.BatchBatch)


def test_missed_copy():
    assert recents.missed_title(1) == "Missed call"
    assert recents.missed_title(3) == "3 missed calls"
    assert recents.missed_display_count(0) == 1
    assert recents.missed_display_count(4) == 4
    assert recents.uses_inbox_style(1) is False
    assert recents.uses_inbox_style(2) is True
    assert recents.inbox_lines(["a", " ", "b", "c"], 2) == ["a", "b"]


def test_silenced_card_line():
    assert recents.silenced_title(1) == "Silenced call"
    assert recents.silenced_title(4) == "4 silenced calls"
    shown = "(415) 555-0100"
    assert recents.silenced_card_line(
        ADA, "unknown caller") == "%s · unknown caller" % shown
    assert recents.silenced_card_line(
        ADA, "Silenced · unknown caller").startswith(shown)
    assert recents.silenced_card_line(None, "") == "Silenced"


def test_digest_stash_cap_and_due():
    current: list[recents.DigestEntry] = []
    for minute in range(5):
        current = recents.stash_digest(
            current, recents.DigestEntry(WALL + minute, ADA, "r"),
            cap=3)
    assert len(current) == 3
    assert recents.digest_due(WALL + recents.DIGEST_PERIOD_MS, WALL)
    assert not recents.digest_due(WALL + recents.DIGEST_PERIOD_MS - 1,
                                  WALL)


# ---- formatters ------------------------------------------------------------------

def test_format_when_buckets():
    tz = timezone.utc
    noon = int(datetime(2026, 10, 15, 12, 0, tzinfo=tz).timestamp()
               * 1000)
    assert recents.format_when(noon - 60_000, noon, tz) == "11:59"
    day = 24 * 60 * 60 * 1000
    assert recents.format_when(noon - day, noon, tz) == "Yest"
    assert recents.format_when(noon - 3 * day, noon, tz) == "Mon"
    assert recents.format_when(noon - 10 * day, noon,
                               tz) == "Oct 5"


def test_recent_outgoing_weekday():
    call_ms = WALL
    outgoing = [WALL - 60_000, WALL - 90_000, WALL + 60_000]
    assert recents.recent_outgoing_weekday(outgoing, call_ms) == \
        datetime.fromtimestamp((WALL - 60_000) / 1000,
                               timezone.utc).strftime("%a")
    assert recents.recent_outgoing_weekday([WALL + 1], call_ms) is None
    assert recents.recent_outgoing_weekday(
        [WALL - recents.RECENT_OUTGOING_MS - 1], call_ms) is None


def test_display_name_and_duration():
    row = _merged_row(1, WALL, ADA)
    row.display_name = "Ada"
    assert recents.display_name_of(row) == "Ada"
    row.display_name = None
    row.cached_name = "Cached Ada"
    assert recents.display_name_of(row) == "Cached Ada"
    row.cached_name = None
    assert recents.display_name_of(row) == "(415) 555-0100"
    row.raw = None
    row.e164 = None
    assert recents.display_name_of(row) == "Withheld"
    assert recents.duration_text(65, False) == "1:05"
    assert recents.duration_text(65, True) == ""
    assert recents.duration_text(0, False) == ""


def test_starred_strip_distinct():
    rows = [MirrorRow("u1", ADA, "Ada", True, None, False, 1),
            MirrorRow("u1", "+1999", "Ada", True, None, False, 1),
            MirrorRow("u2", BO, "Bo", False, None, False, 1)]
    assert recents.starred_strip(rows) == [("u1", "Ada", ADA)]


# ---- store seams -------------------------------------------------------------------

def test_recent_calls_order_and_delete_keeps_screen(store):
    store.add_call(CallRow(date=WALL, number=ADA,
                           call_type=CALL_TYPE_INCOMING, duration=0))
    store.add_call(CallRow(date=WALL + 1, number=BO,
                           call_type=CALL_TYPE_OUTGOING, duration=7))
    rows = store.recent_calls()
    assert [row.number for row in rows] == [BO, ADA]
    assert store.delete_call(rows[0].row_id) is True
    assert store.delete_call(999999) is False
    assert [row.number for row in store.recent_calls()] == [ADA]
    store.log_screen(ScreenRow(at=WALL, verdict="Silence",
                              reason="unknown caller", e164=ADA))
    assert store.delete_call(rows[1].row_id) is True
    assert store.latest_for(ADA) is not None


def test_recents_viewed_watermark(store):
    assert store.get_recents_viewed() == 0
    store.set_recents_viewed(WALL)
    assert store.get_recents_viewed() == WALL
    store.set_recents_viewed(WALL + 1)
    assert store.get_recents_viewed() == WALL + 1


def test_silence_digest_round_trip_and_cap(store):
    assert store.read_digest() == []
    for minute in range(4):
        store.stash_silenced(WALL + minute, ADA, "unknown caller",
                             cap=3)
    assert len(store.read_digest()) == 3
    store.clear_digest()
    assert store.read_digest() == []
    assert store.get_digest_flushed_at() == 0
    store.set_digest_flushed_at(WALL)
    assert store.get_digest_flushed_at() == WALL


# ---- config --------------------------------------------------------------------------

def test_voicemail_number_default_and_round_trip(tmp_path):
    assert app_config.load(home=tmp_path)["voicemail_number"] == ""
    data = app_config.load(home=tmp_path)
    data["voicemail_number"] = "+14155550199"
    app_config.save(data, home=tmp_path)
    assert app_config.load(home=tmp_path)["voicemail_number"] == \
        "+14155550199"


# ---- controller -------------------------------------------------------------------------

def _seeded_db(db_file):
    handle = Store(db_file)
    handle.upsert_mirror([
        MirrorRow("uid-ada", ADA, "Ada", True, None, False, WALL),
        MirrorRow("uid-bo", BO, "Bo Dahl", False, None, False, WALL),
    ])
    handle.upsert_tier("uid-bo", "biz", WALL)
    ada_call = handle.add_call(
        CallRow(date=WALL, number=ADA, call_type=CALL_TYPE_INCOMING,
                duration=0, cached_name=None))
    handle.add_call(CallRow(date=WALL - 60_000, number=ADA,
                            call_type=CALL_TYPE_INCOMING, duration=45))
    handle.add_call(CallRow(date=WALL - 120_000, number=BO,
                            call_type=CALL_TYPE_OUTGOING, duration=30))
    handle.log_screen(ScreenRow(at=WALL, verdict="Silence",
                               reason="unknown caller", e164=ADA,
                               mode="enforcing"))
    handle.log_screen(ScreenRow(at=WALL - 60_000, verdict="Ring",
                               reason="saved contact", e164=ADA,
                               mode="enforcing", answered=True))
    handle.close()
    return ada_call


class _Voice:
    def __init__(self):
        self.placed = []

    def place(self, number, on_done):
        self.placed.append(number)
        on_done(True, "/call/0")


def _deps(db_file, **over):
    from xx_dialer.ui_recents import RecentsDeps

    notices: list[str] = []
    params = {"db_file": db_file,
              "notice": notices.append,
              "now_ms": lambda: WALL + 5_000,
              "launch_sms": lambda uri: over.get("sms", []).append(uri)
              if "sms" in over else None,
              "copy_text": lambda text: over.get("copied", []).append(
                  text) if "copied" in over else None}
    params.update({key: value for key, value in over.items()
                   if key in ("voice", "contacts", "home")})
    return RecentsDeps(**params), notices


def test_controller_load_rows_starred_and_unread(tmp_path):
    from xx_dialer.ui_recents import RecentsController

    db_file = tmp_path / "calls.db"
    _seeded_db(db_file)
    deps, _notices = _deps(db_file)
    controller = RecentsController(deps)
    controller.load()
    assert len(controller.merged) == 3
    assert controller.starred == [("uid-ada", "Ada", ADA)]
    assert controller.unread == 1
    assert controller.merged[0].display_name == "Ada"
    assert controller.merged[0].glyph == recents.GLYPH_SILENCED
    answered = [call for call in controller.merged
                if call.duration_s == 45][0]
    assert answered.missed is False
    controller.mark_viewed()
    assert controller.unread == 0
    with Store(db_file) as store:
        assert store.get_recents_viewed() == WALL + 5_000


def test_controller_filter_group_and_deeplink(tmp_path):
    from xx_dialer.ui_recents import RecentsController

    db_file = tmp_path / "calls.db"
    _seeded_db(db_file)
    deps, _notices = _deps(db_file)
    controller = RecentsController(deps)
    controller.load()
    controller.filter_name = recents.FILTER_SILENCED
    assert len(controller.visible_calls()) == 1
    controller.filter_name = recents.FILTER_ALL
    controller.deeplink = ADA
    assert {call.key for call in controller.visible_calls()} == \
        {call.key for call in controller.merged if call.e164 == ADA}
    controller.deeplink = None
    controller.grouping_on = True
    groups = [item for item in controller.items()
              if item.kind == "header"]
    assert len(groups) == 1 and groups[0].count == 2
    controller.toggle_group(ADA)
    assert sum(1 for item in controller.items()
               if item.kind == "item") == 3


def test_controller_call_message_copy(tmp_path):
    from xx_dialer.ui_recents import RecentsController

    db_file = tmp_path / "calls.db"
    _seeded_db(db_file)
    sms: list[str] = []
    copied: list[str] = []
    voice = _Voice()
    deps, _notices = _deps(db_file, voice=voice, sms=sms,
                           copied=copied)
    controller = RecentsController(deps)
    controller.load()
    call = controller.merged[0]
    controller.call_back(call)
    assert voice.placed == [ADA]
    controller.message(call)
    assert sms == ["sms:%s" % ADA]
    controller.copy_number(call)
    assert copied == [ADA]


def test_controller_block_local_and_via_contacts(tmp_path):
    from xx_dialer.ui_recents import RecentsController
    from xx_dialer.ui_recents import RecentsDeps

    db_file = tmp_path / "calls.db"
    _seeded_db(db_file)

    class _Contacts:
        def __init__(self):
            self.blocked: list[str] = []

        def set_blocked(self, uid, blocked):
            self.blocked.append((uid, blocked))
            return []

    notices: list[str] = []
    controller = RecentsController(
        RecentsDeps(db_file=db_file, notice=notices.append,
                    now_ms=lambda: WALL))
    controller.load()
    stranger = [call for call in controller.merged
                if call.e164 == BO][0]
    controller.block(stranger)
    assert notices == [], "a direct block pops no toast"
    with Store(db_file) as store:
        assert store.is_blocked(BO) is True

    contacts = _Contacts()
    controller2 = RecentsController(
        RecentsDeps(db_file=db_file, contacts=contacts,
                    notice=notices.append, now_ms=lambda: WALL))
    controller2.load()
    ada = [call for call in controller2.merged
           if call.e164 == ADA][0]
    controller2.block(ada)
    assert contacts.blocked == [("uid-ada", True)]


def test_controller_star_and_business_need_contact(tmp_path):
    from xx_dialer.ui_recents import RecentsController
    from xx_dialer.ui_recents import RecentsDeps

    db_file = tmp_path / "calls.db"
    _seeded_db(db_file)
    notices: list[str] = []
    controller = RecentsController(
        RecentsDeps(db_file=db_file, notice=notices.append,
                    now_ms=lambda: WALL))
    controller.load()
    stranger = [call for call in controller.merged
                if call.e164 == BO][0]
    controller.ring_next_time(stranger)
    assert "save it to contacts" in notices[-1]
    controller.add_to_business(stranger)
    assert "save it to contacts" in notices[-1]


def test_controller_add_contact_and_delete(tmp_path):
    from xx_dialer.ui_recents import RecentsController
    from xx_dialer.ui_recents import RecentsDeps

    db_file = tmp_path / "calls.db"
    _seeded_db(db_file)

    class _Contacts:
        def __init__(self):
            self.created: list[str] = []

        def create_async(self, vcard, on_done, on_error=None):
            self.created.append(vcard)
            on_done("uid-new")

    contacts = _Contacts()
    notices: list[str] = []
    controller = RecentsController(
        RecentsDeps(db_file=db_file, contacts=contacts,
                    notice=notices.append, now_ms=lambda: WALL))
    controller.load()
    call = controller.merged[0]
    controller.add_contact(call)
    assert len(contacts.created) == 1
    assert "TEL;TYPE=CELL:%s" % ADA in contacts.created[0]
    assert notices == [], "a saved contact pops no toast"
    assert controller.delete(call) is True
    assert all(row.key != call.key for row in controller.merged)

    lonely = RecentsController(
        RecentsDeps(db_file=db_file, notice=notices.append,
                    now_ms=lambda: WALL))
    lonely.load()
    lonely.add_contact(lonely.merged[0])
    assert notices[-1] == "Contacts app is not installed."


# ---- keypad buffer --------------------------------------------------------------------------

def test_keypad_buffer_and_voicemail():
    from xx_dialer.ui_keypad import (
        KeypadBuffer,
        dial_target,
        resolve_voicemail_number,
    )

    buffer = KeypadBuffer()
    assert buffer.can_call is False
    buffer.append("5")
    buffer.append("55")
    assert buffer.digits == "555" and buffer.can_call is True
    buffer.backspace()
    assert buffer.digits == "55"
    buffer.set(ADA)
    assert buffer.digits == ADA
    buffer.clear()
    assert buffer.digits == ""
    assert resolve_voicemail_number(
        {"voicemail_number": "  "}) is None
    assert resolve_voicemail_number(
        {"voicemail_number": "+1999"}) == "+1999"
    assert dial_target("555-0100") is not None


# ---- GObject int64 rule --------------------------------------------------------------------------

def test_recent_item_keeps_millisecond_ids():
    from xx_dialer.ui_recents import RecentItem

    item = RecentItem()
    item.row_id = WALL
    item.date = WALL + 42
    assert int(item.row_id) == WALL
    assert int(item.date) == WALL + 42


def test_recent_item_id_properties_are_int64():
    from gi.repository import GObject

    from xx_dialer.ui_recents import RecentItem

    assert RecentItem.find_property(
        "row-id").value_type == GObject.TYPE_INT64
    assert RecentItem.find_property(
        "date").value_type == GObject.TYPE_INT64


# ---- contacts Create ------------------------------------------------------------------------------

class _FakeVariant:
    def __init__(self, value):
        self.value = value

    def unpack(self):
        return self.value


class _FakeGlib:
    def Variant(self, fmt, value):
        return _FakeVariant((fmt, value))


class _FakeGio:
    DBusCallFlags = type("Flags", (), {"NONE": 0})


class _FakeContactsBus:
    def __init__(self, uid="uid-new"):
        self.calls = []
        self.uid = uid
        self._reply = _FakeVariant((uid,))

    def call(self, bus_name, path, iface, method, params, *_args,
             **_kwargs):
        self.calls.append((method, params.unpack()))
        for arg in _args:
            if callable(arg):
                arg(self, object())
                break

    def call_finish(self, _result):
        return self._reply


def test_new_contact_vcard_round_trips():
    vcard = contacts_client.new_contact_vcard(ADA)
    assert "TEL;TYPE=CELL:%s" % ADA in vcard
    name, numbers = contacts_client.parse_vcard(vcard)
    assert numbers == [ADA]
    assert name == ADA


def test_create_async_sends_vcard(store):
    from xx_dialer.contacts_client import ContactsClient

    bus = _FakeContactsBus()
    client = ContactsClient(store)
    client._bus = bus
    client._gio = _FakeGio()
    client._glib = _FakeGlib()
    client._present = True
    done: list[str] = []
    client.create_async("VCARD", done.append)
    assert bus.calls[0][0] == "Create"
    assert done == ["uid-new"]


def test_create_async_absent_reports_error(store):
    from xx_dialer.contacts_client import ContactsClient

    client = ContactsClient(store)
    errors: list[Exception] = []
    client.create_async("VCARD", lambda _uid: None,
                        errors.append)
    assert len(errors) == 1


def test_ui_modules_import_without_display():
    import xx_dialer.ui_keypad  # noqa: F401
    import xx_dialer.ui_recents  # noqa: F401


def test_deps_dataclasses_accept_documented_kwargs(tmp_path):
    from xx_dialer.main import DialerDeps
    from xx_dialer.ui_keypad import KeypadDeps
    from xx_dialer.ui_recents import RecentsDeps

    db_file = tmp_path / "calls.db"
    RecentsDeps(db_file=db_file, home=tmp_path, voice=None,
                contacts=None, launch_sms=None, copy_text=None,
                notice=None, now_ms=None)
    KeypadDeps(db_file=db_file, home=tmp_path, voice=None,
               contacts=None, notice=None)
    DialerDeps(db_file=db_file, home=tmp_path, voice=None,
               contacts=None)


def test_ringer_has_no_reply_type_string_or_gtk():
    from pathlib import Path

    text = (Path(__file__).parent.parent / "src" / "xx_dialer"
            / "ringer.py").read_text(encoding="utf-8")
    assert "VariantType" not in text
    assert "gi.repository.Gtk" not in text
    assert "gi.repository.Adw" not in text
