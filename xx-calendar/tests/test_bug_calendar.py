"""Regression tests for lane bug_calendar (review findings 7,8,9,28,29,33)."""
import datetime as dt
from datetime import timezone
from zoneinfo import ZoneInfo

import pytest

from xx_calendar.service import CalendarService, iter_vevent_blocks
from xx_calendar.store import CalendarStore


@pytest.fixture
def service():
    store = CalendarStore(":memory:")
    handle = CalendarService(store, start_worker=False)
    handle._connection = None
    yield handle
    handle.stop()
    store.close()


def _utc(iso):
    return int(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


WEEKLY = "\r\n".join([
    "BEGIN:VEVENT", "UID:weekly-1", "DTSTAMP:20260101T000000Z",
    "DTSTART:20260302T090000Z", "DTEND:20260302T100000Z",
    "RRULE:FREQ=WEEKLY;BYDAY=MO", "SUMMARY:Standup", "END:VEVENT"])

ONE_SHOT = "\r\n".join([
    "BEGIN:VEVENT", "UID:one-1", "DTSTAMP:20260101T000000Z",
    "DTSTART:20260302T090000Z", "DTEND:20260302T100000Z",
    "SUMMARY:Solo", "END:VEVENT"])


def test_7_folded_description_survives_block_split():
    from xx_calendar import ics as ics_module
    from xx_calendar.ics import ICalProperty

    long_desc = "A" * 200
    event = ics_module.VEvent()
    event.props.append(ICalProperty(name="UID", value="fold-1", modified=True))
    event.props.append(ICalProperty(name="DTSTAMP", value="20260101T000000Z",
                                    modified=True))
    event.props.append(ICalProperty(name="DTSTART", value="20260302T090000Z",
                                    modified=True))
    event.props.append(ICalProperty(name="DTEND", value="20260302T100000Z",
                                    modified=True))
    event.set_text("DESCRIPTION", long_desc)
    serialized = event.serialize()
    assert "\r\n " in serialized
    cal = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + serialized + "\r\nEND:VCALENDAR\r\n"
    blocks = list(iter_vevent_blocks(cal))
    assert len(blocks) == 1
    assert "\r\n " in blocks[0]
    parsed = ics_module.parse_events(blocks[0])
    assert parsed and parsed[0].description() == long_desc


def test_7_folded_import_round_trip(service):
    from xx_calendar import ics as ics_module
    from xx_calendar.ics import ICalProperty

    long_desc = "B" * 200
    event = ics_module.VEvent()
    event.props.append(ICalProperty(name="UID", value="fold-imp", modified=True))
    event.props.append(ICalProperty(name="DTSTAMP", value="20260101T000000Z",
                                    modified=True))
    event.props.append(ICalProperty(name="DTSTART", value="20260302T090000Z",
                                    modified=True))
    event.props.append(ICalProperty(name="DTEND", value="20260302T100000Z",
                                    modified=True))
    event.set_text("DESCRIPTION", long_desc)
    cal = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + event.serialize()
           + "\r\nEND:VCALENDAR\r\n")
    created, _, _ = service.import_ics("local", cal)
    assert created == 1
    reparsed = ics_module.parse_events(service.get("fold-imp"))
    assert reparsed and reparsed[0].description() == long_desc


def test_8_moved_instance_does_not_reappear(service):
    service.create("local", WEEKLY)
    start, end = _utc("2026-03-01T00:00:00Z"), _utc("2026-05-01T00:00:00Z")
    before = service.list_instances(start, end, ["local"])
    assert len(before) == 9
    second = before[1]["start"].get_int64()
    moved = ONE_SHOT.replace("UID:one-1", "UID:weekly-1").replace(
        "DTSTART:20260302T090000Z", "DTSTART:20260615T110000Z").replace(
        "DTEND:20260302T100000Z", "DTEND:20260615T120000Z").replace(
        "SUMMARY:Solo", "SUMMARY:Moved")
    service.update("weekly-1", moved, "this", second)
    after = service.list_instances(start, end, ["local"])
    starts = [i["start"].get_int64() for i in after]
    assert second not in starts
    assert len(after) == len(before) - 1


def test_9_this_with_zero_rid_is_rejected(service):
    service.create("local", WEEKLY)
    start, end = _utc("2026-03-01T00:00:00Z"), _utc("2026-05-01T00:00:00Z")
    before = service.list_instances(start, end, ["local"])
    count = len(before)
    with pytest.raises(ValueError):
        service.delete("weekly-1", "this", 0)
    with pytest.raises(ValueError):
        service.update("weekly-1", WEEKLY, "this", 0)
    with pytest.raises(ValueError):
        service.delete("weekly-1", "this-and-following", 0)
    parent = service._store.get_event("weekly-1")
    assert parent.rrule == "FREQ=WEEKLY;BYDAY=MO"
    assert parent.dtstart == _utc("2026-03-02T09:00:00Z")
    after = service.list_instances(start, end, ["local"])
    assert len(after) == count


def test_9_plain_instances_report_start_as_rid(service):
    service.create("local", ONE_SHOT)
    service.create("local", WEEKLY)
    start, end = _utc("2026-03-01T00:00:00Z"), _utc("2026-03-10T00:00:00Z")
    found = service.list_instances(start, end, ["local"])
    solo = [i for i in found if i["uid"].get_string() == "one-1"][0]
    assert solo["recurrence_id"].get_int64() == solo["start"].get_int64()
    regular = [i for i in found if i["uid"].get_string() == "weekly-1"
               and not i["is_override"].get_boolean()][0]
    assert regular["recurrence_id"].get_int64() == regular["start"].get_int64()


def test_28_all_day_east_of_utc_keeps_date():
    from xx_calendar.main import parse_sheet_datetime

    tokyo = ZoneInfo("Asia/Tokyo")
    start_ms = parse_sheet_datetime("2026-11-05", True, tokyo)
    expected = int(dt.datetime(2026, 11, 5, tzinfo=timezone.utc).timestamp() * 1000)
    assert start_ms == expected
    from xx_calendar import views

    ics_text = views.build_create_ics("Holiday", True, start_ms, start_ms + 86400000)
    from xx_calendar import ics as ics_module

    event = ics_module.parse_events(ics_text)[0]
    assert event.dtstart()[0] == expected


def test_28_timed_uses_per_date_offset():
    from xx_calendar.main import parse_sheet_datetime

    zone = ZoneInfo("America/New_York")
    got = parse_sheet_datetime("2026-11-05 09:00", False, zone)
    assert got == _utc("2026-11-05T14:00:00Z")


def test_28_local_zone_is_iana_with_dst(monkeypatch):
    from xx_calendar.main import local_zone

    monkeypatch.setenv("TZ", "America/New_York")
    zone = local_zone()
    assert getattr(zone, "key", None) == "America/New_York"
    october = dt.datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc).astimezone(zone)
    november = dt.datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc).astimezone(zone)
    assert october.utcoffset() != november.utcoffset()


def test_29_cancelled_override_survives_export_import(service):
    service.create("local", WEEKLY)
    start, end = _utc("2026-03-01T00:00:00Z"), _utc("2026-05-01T00:00:00Z")
    before = service.list_instances(start, end, ["local"])
    victim = before[1]["start"].get_int64()
    service.delete("weekly-1", "this", victim)
    pruned = service.list_instances(start, end, ["local"])
    assert victim not in [i["start"].get_int64() for i in pruned]
    exported = service.export_ics(["local"])
    assert "CANCELLED" in exported
    fresh = CalendarStore(":memory:")
    second = CalendarService(fresh, start_worker=False)
    second._connection = None
    try:
        second.import_ics("local", exported)
        revived = second.list_instances(start, end, ["local"])
        assert victim not in [i["start"].get_int64() for i in revived]
        assert len(revived) == len(pruned)
    finally:
        second.stop()
        fresh.close()


def test_33_partial_migration_recovers(tmp_path):
    import sqlite3

    path = str(tmp_path / "calendar.db")
    store = CalendarStore(path)
    store.create_calendar(
        __import__("xx_calendar.store", fromlist=["CalendarRecord"]).CalendarRecord(
            id="local", display_name="L"))
    store.close()
    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    recovered = CalendarStore(path)
    try:
        assert recovered.get_schema_version() == 2
        assert recovered.get_calendar("local").display_name == "L"
    finally:
        recovered.close()
    reopened = CalendarStore(path)
    try:
        assert reopened.get_schema_version() == 2
    finally:
        reopened.close()
