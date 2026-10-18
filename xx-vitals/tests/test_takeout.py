"""Takeout import: header-driven CSV + weight JSON parse, validated storage."""

import json

from xx_vitals.core import takeout
from xx_vitals.store.db import Database


def _db():
    db = Database(":memory:")
    db.init()
    return db


CSV = (
    "Start time,Step count,Distance (m),Move Minutes count,Heart Points,"
    "Calories (kcal),Average heart rate (bpm),Unknown column\r\n"
    "2026-09-01 08:00:00,5000,3500.5,30,25,220.5,70,x\r\n"
    "2026-09-02 08:00:00,,,,,,,\r\n"
    "not-a-date,10,,,,,,\r\n"
    "2026-09-03 08:00:00,abc,,,,,,\r\n"
)


def test_daily_csv_parses_and_skips_bad_rows():
    parsed = takeout.parse_daily_csv(CSV)
    assert [day.date for day in parsed.days] == ["2026-09-01"]
    day = parsed.days[0]
    assert (day.steps, day.active_min, day.effort_points) == (5000, 30, 25)
    assert day.distance_m == 3500.5 and day.hr_avg == 70
    assert parsed.rows_skipped == 3


def test_daily_csv_empty_and_unknown_headers():
    assert takeout.parse_daily_csv("").days == []
    assert takeout.parse_daily_csv("Foo,Bar\n1,2\n").days == []


def test_daily_csv_bom_tolerated():
    parsed = takeout.parse_daily_csv("﻿Start time,Step count\n2026-09-01,100\n")
    assert [day.date for day in parsed.days] == ["2026-09-01"]


def test_weight_json_parses_points_and_skips_bad():
    nanos = 1_700_000_000_000_000_000
    doc = {"point": [
        {"startTimeNanos": str(nanos), "value": [{"fpVal": 80.5}]},
        {"startTimeNanos": "junk", "value": [{"fpVal": 80.0}]},
        {"startTimeNanos": str(nanos), "value": []},
    ]}
    parsed = takeout.parse_weight_json(json.dumps(doc))
    assert [(w.measured_at_ms, w.weight_kg) for w in parsed.weights] == [
        (nanos // 1_000_000, 80.5)]
    assert parsed.rows_skipped == 2
    assert takeout.parse_weight_json("nope").weights == []


def test_detect_kind_by_name_and_content():
    assert takeout.detect_kind("Daily activity metrics.csv", "x") == "daily_csv"
    assert takeout.detect_kind("raw_com.google.weight_1.json",
                                '{"point": []}') == "weight_json"
    assert takeout.detect_kind("notes.json", '{"point": []}') is None
    assert takeout.detect_kind("data.json", "{}") is None
    assert takeout.detect_kind("data.txt", "x") is None


def test_import_csv_stores_days_and_queues_them():
    from xx_vitals.ui import profile

    db = _db()
    message = profile.import_takeout(db, "Daily activity metrics.csv", CSV, 1_000)
    assert "1 day and" in message and "0 weights" in message
    assert db.get_daily("takeout", "2026-09-01").steps == 5000
    assert db.pending_count() == 1


def test_import_weight_json_validates_range_like_manual_entry():
    from xx_vitals.ui import profile

    nanos = 1_700_000_000_000_000_000
    doc = {"point": [
        {"startTimeNanos": str(nanos), "value": [{"fpVal": 80.5}]},
        {"startTimeNanos": str(nanos + 1_000_000_000), "value": [{"fpVal": 5.0}]},
    ]}
    message = profile.import_takeout(
        _db(), "raw_com.google.weight_1.json", json.dumps(doc), 1_000)
    assert "0 days and 1 weight," in message
    assert "(1 row skipped)" in message


def test_import_rejects_unknown_files():
    from xx_vitals.ui import profile

    db = _db()
    message = profile.import_takeout(db, "notes.txt", "hello", 1_000)
    assert "not a Takeout" in message
    assert db.pending_count() == 0


TCX = """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Activities>
    <Activity Sport="Running">
      <Id>2026-09-01T08:00:00Z</Id>
      <Lap StartTime="2026-09-01T08:00:00Z">
        <TotalTimeSeconds>1800</TotalTimeSeconds>
        <DistanceMeters>3000</DistanceMeters>
        <Calories>200</Calories>
        <AverageHeartRateBpm><Value>140</Value></AverageHeartRateBpm>
        <MaximumHeartRateBpm><Value>155</Value></MaximumHeartRateBpm>
        <Track>
          <Trackpoint><Position>
            <LatitudeDegrees>47.0</LatitudeDegrees>
            <LongitudeDegrees>8.0</LongitudeDegrees>
          </Position></Trackpoint>
          <Trackpoint><Position>
            <LatitudeDegrees>47.1</LatitudeDegrees>
            <LongitudeDegrees>8.1</LongitudeDegrees>
          </Position></Trackpoint>
        </Track>
      </Lap>
    </Activity>
    <Activity>
      <Id>2026-09-02T08:00:00Z</Id>
      <Lap StartTime="2026-09-02T08:00:00Z">
        <TotalTimeSeconds>600</TotalTimeSeconds>
      </Lap>
    </Activity>
  </Activities>
</TrainingCenterDatabase>
"""


def test_tcx_parses_sport_and_laps_and_skips_sportless():
    parsed = takeout.parse_tcx(TCX)
    assert len(parsed.activities) == 1
    session = parsed.activities[0]
    assert session.type == "run"
    assert session.ended_at_ms - session.started_at_ms == 1_800_000
    assert session.distance_m == 3000 and session.calories_kcal == 200
    assert (session.hr_avg, session.hr_max) == (140, 155)
    assert "LineString" in (session.track_geojson or "")
    assert parsed.rows_skipped == 1


def test_tcx_without_namespace_and_garbage():
    bare = TCX.replace(
        "<TrainingCenterDatabase xmlns=\"http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2\">",
        "<TrainingCenterDatabase>",
    )
    assert len(takeout.parse_tcx(bare).activities) == 1
    assert takeout.parse_tcx("not xml at all").activities == []


HC_JSON = json.dumps({"records": [
    {"measured_at": "2026-09-01T08:00:00Z", "weight_kg": 80.5},
    {"measured_at": 1_700_000_000_000, "weight_kg": 79.0},
    {"measured_at": "junk", "weight_kg": 80.0},
    {"measured_at": "2026-09-02T08:00:00Z"},
]})


def test_healthconnect_json_parses_iso_and_millis():
    parsed = takeout.parse_healthconnect_json(HC_JSON)
    assert [w.weight_kg for w in parsed.weights] == [80.5, 79.0]
    assert parsed.rows_skipped == 2
    assert takeout.parse_healthconnect_json("nope").weights == []


def test_detect_kind_covers_tcx_hc_and_zip():
    assert takeout.detect_kind("run.tcx", "<x/>") == "tcx"
    assert takeout.detect_kind("x.xml", TCX[:2000]) == "tcx"
    assert takeout.detect_kind("export.json", HC_JSON) == "healthconnect_json"
    assert takeout.detect_kind("takeout.zip", "PK...") == "takeout_zip"


def test_stream_parse_matches_text_parse_with_progress():
    seen: list = []
    streamed = takeout.parse_daily_csv_stream(
        iter(CSV.splitlines()), on_progress=lambda done, total: seen.append(done))
    plain = takeout.parse_daily_csv(CSV)
    assert [day.date for day in streamed.days] == [day.date for day in plain.days]
    assert streamed.rows_skipped == plain.rows_skipped
    assert seen and seen[-1] == 4


def test_import_tcx_stores_activity_and_queues_it():
    from xx_vitals.ui import profile

    db = _db()
    message = profile.import_takeout(db, "run.tcx", TCX, 1_000)
    assert "1 activity (" in message
    assert db.count_activities() == 1
    assert db.pending_count() == 1


def test_import_healthconnect_json_validates_range():
    from xx_vitals.ui import profile

    db = _db()
    message = profile.import_takeout(db, "export.json", HC_JSON, 1_000)
    assert "0 days and 2 weights" in message
    assert db.count_weights() == 2


def test_import_zip_reads_csv_tcx_and_weight_members(tmp_path):
    import zipfile

    from xx_vitals.ui import profile

    nanos = 1_700_000_000_000_000_000
    weight_doc = {"point": [
        {"startTimeNanos": str(nanos), "value": [{"fpVal": 80.5}]}]}
    archive = tmp_path / "takeout.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(
            "Takeout/Fit/Daily activity metrics/Daily activity metrics.csv", CSV)
        bundle.writestr("Takeout/Fit/Activities/run.tcx", TCX)
        bundle.writestr(
            "Takeout/Fit/All Data/raw_com.google.weight_1.json",
            json.dumps(weight_doc))
        bundle.writestr("Takeout/README.txt", "ignore me")
    progress: list = []
    db = _db()
    message = profile.import_takeout_zip(
        db, archive, 1_000, on_progress=lambda done, total: progress.append((done, total)))
    assert "1 day and 1 weight, 1 activity" in message
    assert db.count_dailies() == 1
    assert db.count_activities() == 1
    assert db.count_weights() == 1
    assert db.pending_count() == 3
    assert progress and progress[-1][1] == 4


def test_import_zip_without_known_members_reports_empty(tmp_path):
    import zipfile

    from xx_vitals.ui import profile

    archive = tmp_path / "empty.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("notes.txt", "hi")
    assert "No daily metrics" in profile.import_takeout_zip(_db(), archive, 1_000)


def test_import_batch_is_a_single_transaction():
    import pytest

    from xx_vitals.store.db import DailyMetric

    db = _db()
    good = DailyMetric(source_id="takeout", date="2026-09-01", steps=5, derived_mask=0)
    bad = DailyMetric(source_id="takeout", date=None, steps=5, derived_mask=0)
    with pytest.raises(Exception):
        db.import_batch([good, bad], now_ms=1_000)
    assert db.get_daily("takeout", "2026-09-01") is None
    assert db.pending_count() == 0


def test_import_replaces_queued_daily_for_same_date():
    from xx_vitals.ui import profile

    db = _db()
    profile.import_takeout(db, "Daily activity metrics.csv", CSV, 1_000)
    profile.import_takeout(db, "Daily activity metrics.csv", CSV, 2_000)
    assert db.pending_count() == 1
