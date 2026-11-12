"""Backup v1 tests: byte-compatible read/write/restore (L4).

Headless: no display, no GTK import.
"""
import json
import os
import stat
from pathlib import Path

from xx_calendar import backup
from xx_calendar.backup import (
    CORRUPT,
    FORMAT_VERSION,
    Ok,
    read,
    write,
)
from xx_calendar.models import CalendarKey
from xx_calendar.store import CalendarRecord, CalendarStore


def _ref_calendar_dir():
    here = Path(__file__).resolve()
    # Lane copies carry ref/; the real checkout keeps the Android sources
    # in ../../android/ (../xx-calendar would be this Linux repo itself).
    for candidate in (here.parent.parent / "ref" / "xx-calendar",
                      here.parent.parent.parent.parent / "android"
                      / "xx-calendar"):
        if (candidate / "app").is_dir():
            return candidate
    return None


def _full_settings():
    return {
        "default_view": "MONTH",
        "start_day_of_week": "SUNDAY",
        "week_numbers": True,
        "show_declined": True,
        "dim_past": False,
        "density": "COMPACT",
        "default_duration_min": 90,
        "default_notification_min": 60,
        "all_day_notify_hour": 8,
        "all_day_notify_days_before": 2,
        "lock_screen_title": True,
        "daily_agenda": True,
        "heads_up": True,
        "hide_auto_added": False,
        "background": "AMOLED_NIGHT",
        "font": "JETBRAINS_MONO",
        "text_size_scale": 1.25,
        "auto_added_filter_mode": "CALENDAR",
    }


def _sigils():
    return {
        CalendarKey("1", "you@example.com"): 1,
        CalendarKey("42", 'work "shared"; team\\ops'): 4,
    }


def test_document_shape_carries_app_tag_and_version():
    text = write({}, {})
    assert '"app": "calendar"' in text
    assert '"version": 1' in text
    assert FORMAT_VERSION == 1
    assert '"settings"' in text
    assert '"sigils": []' in text


def test_write_then_read_round_trips():
    parsed = read(write(_full_settings(), _sigils()))
    assert isinstance(parsed, Ok)
    assert parsed.snapshot.settings["default_view"] == "MONTH"
    assert parsed.snapshot.settings["text_size_scale"] == 1.25
    assert parsed.snapshot.sigils == _sigils()


def test_defaults_round_trip():
    parsed = read(write({}, {}))
    assert isinstance(parsed, Ok)
    assert parsed.snapshot.settings["default_view"] == "SCHEDULE"
    assert parsed.snapshot.sigils == {}


def test_writer_is_deterministic():
    assert write(_full_settings(), _sigils()) == write(_full_settings(), _sigils())


def test_tricky_account_names_survive():
    tricky = {CalendarKey("7", 'a"b\\c\td'): 2}
    parsed = read(write({}, tricky))
    assert isinstance(parsed, Ok)
    assert parsed.snapshot.sigils == tricky


def test_empty_object_restores_defaults():
    parsed = read("{}")
    assert isinstance(parsed, Ok)
    assert parsed.snapshot.settings["default_view"] == "SCHEDULE"
    assert parsed.snapshot.sigils == {}


def test_unknown_keys_ignored_and_enums_fall_back():
    parsed = read('{"future_section":{"x":1},"settings":'
                  '{"unknown_key":"whatever","dim_past":false}}')
    assert isinstance(parsed, Ok)
    assert parsed.snapshot.settings["dim_past"] is False
    bad = read('{"settings":{"default_view":"TELEPORT","density":"cozy",'
               '"auto_added_filter_mode":"MAYBE"}}')
    assert isinstance(bad, Ok)
    assert bad.snapshot.settings["default_view"] == "SCHEDULE"
    assert bad.snapshot.settings["density"] == "COMFORTABLE"
    assert bad.snapshot.settings["auto_added_filter_mode"] == "METADATA"


def test_malformed_sigil_rows_skipped_individually():
    text = json.dumps({
        "sigils": [
            {"calendarId": 1, "accountName": "ok@x", "tier": 0},
            {"accountName": "no-id", "tier": 1},
            {"calendarId": 2, "tier": 1},
            {"calendarId": 3, "accountName": "bad-tier", "tier": 99},
            {"calendarId": 4, "accountName": "no-tier"},
            "not an object",
            {"calendarId": 5, "accountName": "also-ok@x", "tier": 5},
        ],
    })
    parsed = read(text)
    assert isinstance(parsed, Ok)
    assert parsed.snapshot.sigils == {
        CalendarKey("1", "ok@x"): 0,
        CalendarKey("5", "also-ok@x"): 5,
    }


def test_corrupt_inputs_report_corrupt():
    for bad in ["", "   ", "not json", "{", '{"settings":}',
                '{"settings":{"week_numbers":tru}}', "[1,2,3]",
                '"just a string"',
                '{"settings":{"week_numbers":false},}']:
        assert isinstance(read(bad), backup.Corrupt), bad
    assert CORRUPT is not None


def test_leading_bom_tolerated():
    parsed = read("\ufeff{\"settings\":{\"daily_agenda\":true}}")
    assert isinstance(parsed, Ok)
    assert parsed.snapshot.settings["daily_agenda"] is True


def test_truncated_backup_is_corrupt_not_partial():
    full = write(_full_settings(), _sigils())
    assert isinstance(read(full[:len(full) // 2]), backup.Corrupt)


def test_deeply_nested_junk_is_corrupt():
    depth = 10000
    assert isinstance(read("[" * depth + "]" * depth), backup.Corrupt)


def test_android_source_keys_match():
    ref = _ref_calendar_dir()
    if ref is None:
        import pytest

        pytest.skip("no xx-calendar reference checkout")
    store_kt = (ref / "app" / "src" / "main" / "java" / "com" / "piercingxx"
                / "calendar" / "settings" / "SettingsStore.kt")
    backup_kt = (ref / "app" / "src" / "main" / "java" / "com" / "piercingxx"
                 / "calendar" / "settings" / "BackupJson.kt")
    if not store_kt.exists() or not backup_kt.exists():
        import pytest

        pytest.skip("reference settings sources missing")
    names = store_kt.read_text(encoding="utf-8")
    for key in backup.SETTINGS_KEYS:
        assert key in names, key


def test_restore_is_wholesale_and_corrupt_writes_nothing(tmp_path):
    store = CalendarStore(":memory:")
    try:
        store.create_calendar(CalendarRecord(id="1", display_name="One",
                                             account_name="you@example.com"))
        settings_path = tmp_path / "settings.json"
        snapshot = backup.restore(store, settings_path,
                                  write(_full_settings(), _sigils()))
        assert snapshot.settings["default_view"] == "MONTH"
        assert store.get_calendar("1").sigil_tier == 1
        before = settings_path.read_text(encoding="utf-8")
        try:
            backup.restore(store, settings_path, "{broken")
        except ValueError:
            pass
        else:
            raise AssertionError("corrupt restore must raise")
        assert settings_path.read_text(encoding="utf-8") == before
        assert store.get_calendar("1").sigil_tier == 1
    finally:
        store.close()


def test_backup_file_writes_are_0600(tmp_path):
    target = tmp_path / "sub" / "backup.json"
    os.makedirs(str(target.parent), mode=0o755)
    backup.write_backup_file(str(target), _full_settings(), _sigils())
    assert stat.S_IMODE(os.stat(str(target)).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(str(target.parent)).st_mode) == 0o700
    parsed = read(Path(str(target)).read_text(encoding="utf-8"))
    assert isinstance(parsed, Ok)
