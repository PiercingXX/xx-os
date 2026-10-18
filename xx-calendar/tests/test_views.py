"""Headless tests for the L5 pure view-model helpers (views.py)."""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from xx_calendar import views
from xx_calendar.store import CalendarRecord, CalendarStore

UTC = dt.timezone.utc


def _seed_store():
    store = CalendarStore(":memory:")
    store.create_calendar(CalendarRecord(id="local", display_name="Phone"))
    store.create_calendar(CalendarRecord(id="work", display_name="Work"))
    return store


def _put(uid, calendar_id, ics_text, start_ms, end_ms, all_day=False,
         rrule="", status=""):
    from xx_calendar.store import EventRecord

    store = getattr(_put, "store")
    store.put_event(EventRecord(uid=uid, calendar_id=calendar_id, ics=ics_text,
                                dtstart=start_ms, dtend=end_ms,
                                all_day=all_day, rrule=rrule, status=status))


def test_route_for_covers_all_views():
    assert views.route_for("SCHEDULE") == "schedule"
    assert views.route_for("DAY") == "day"
    assert views.route_for("WEEK") == "week"
    assert views.route_for("MONTH") == "month"
    assert views.route_for("bogus") == "schedule"
    assert views.route_for(None) == "schedule"


def test_start_route_clamps_unimplemented_to_schedule():
    assert views.start_route("SCHEDULE") == "schedule"
    assert views.start_route("MONTH") == "month"
    assert views.start_route("DAY") == "schedule"
    assert views.start_route("WEEK") == "schedule"


def test_parse_date_arg_strict():
    assert views.parse_date_arg("2026-10-16") == dt.date(2026, 10, 16)
    assert views.parse_date_arg("2026-02-30") is None
    assert views.parse_date_arg("16/10/2026") is None
    assert views.parse_date_arg("") is None
    assert views.parse_date_arg(None) is None
    assert views.parse_date_arg("2026-10-16T00:00") is None


def test_parse_app_uri():
    good = "io.piercingxx.XXCalendar://date/2026-10-16"
    assert views.parse_app_uri(good) == dt.date(2026, 10, 16)
    assert views.parse_app_uri(
        "io.piercingxx.XXCalendar://date?date=2026-10-16",
    ) == dt.date(2026, 10, 16)
    assert views.parse_app_uri(
        "io.piercingxx.XXCalendar://date/not-a-date") is None
    assert views.parse_app_uri("https://example.com/date/2026-10-16") is None
    assert views.parse_app_uri(None) is None
    assert views.parse_app_uri("") is None


def test_swipe_direction_ports_android_rule():
    assert views.swipe_direction(-80.0, 5.0, 56.0) == 1
    assert views.swipe_direction(80.0, 5.0, 56.0) == -1
    assert views.swipe_direction(-80.0, 90.0, 56.0) == 0
    assert views.swipe_direction(10.0, 0.0, 56.0) == 0


def test_schedule_window_jump_and_focus():
    window = views.ScheduleWindow(today=dt.date(2026, 10, 16))
    assert window.start_date <= dt.date(2026, 9, 1)
    assert window.end_date >= dt.date(2026, 11, 30)
    assert window.on_current_month(dt.date(2026, 10, 5))
    window.jump_to(dt.date(2026, 3, 10))
    assert window.focus_date == dt.date(2026, 3, 10)
    assert window.start_date == dt.date(2026, 2, 1)
    assert window.end_date == dt.date(2026, 4, 30)
    assert not window.on_current_month(dt.date(2026, 10, 5))
    inside = dt.date(2026, 3, 20)
    window.focus_on(inside)
    assert window.focus_date == inside
    assert window.start_date == dt.date(2026, 2, 1)
    window.focus_on(dt.date(2026, 9, 1))
    assert window.focus_date == dt.date(2026, 9, 1)
    assert window.start_date == dt.date(2026, 8, 1)
    before = window.revision
    window.force_refresh()
    assert window.revision == before + 1


def test_schedule_window_extend_grows_both_ways():
    window = views.ScheduleWindow(today=dt.date(2026, 10, 16))
    start, end = window.start_date, window.end_date
    window.extend_backward()
    assert window.start_date < start
    window.extend_forward()
    assert window.end_date > end


def test_month_grid_math():
    assert views.leading_days(2026, 9, 0) == 1
    assert views.leading_days(2026, 9, 6) == 2
    weeks = views.build_weeks(2026, 9, 0)
    assert len(weeks) == 5
    assert all(len(row) == 7 for row in weeks)
    first = weeks[0][0]
    assert first == (dt.date(2026, 8, 31), False)
    assert (dt.date(2026, 9, 1), True) in weeks[0]
    assert (dt.date(2026, 9, 30), True) in weeks[-1]
    assert views.weekday_labels(0)[0] == "MO"
    assert views.weekday_labels(6)[0] == "SU"
    assert views.week_number(dt.date(2026, 10, 16)) == 42
    assert views.month_label(2026, 8) == "AUGUST 2026"
    assert views.day_header(dt.date(2026, 8, 24)) == "MON 24 AUG"
    assert views.month_pager_page(2026, 10, 2026, 10) == 0
    assert views.month_pager_page(2026, 11, 2026, 10) == 1


def test_time_range_and_cell_summary():
    zone = ZoneInfo("UTC")
    start = int(dt.datetime(2026, 3, 2, 9, 0, tzinfo=UTC).timestamp() * 1000)
    end = start + 15 * 60 * 1000
    assert views.time_range(start, end, zone) == "09:00 – 09:15"
    assert views.time_range(start, start, zone) == "09:00"
    assert views.month_cell_summary(0) == ""
    assert views.month_cell_summary(1) == "·"
    assert views.month_cell_summary(3) == "···"
    assert views.month_cell_summary(5) == "··· +2"
    assert views.month_cell_summary(-1) == ""


def test_sigil_for_tier():
    assert views.sigil_for_tier(0) == "▌"
    assert views.sigil_for_tier(3) == "▒"
    assert views.sigil_for_tier(9) == "·"
    assert views.sigil_for_tier("nope") == "·"


def _window_ms(start_day, end_day):
    start = dt.datetime(start_day.year, start_day.month, start_day.day,
                        tzinfo=UTC)
    end = dt.datetime(end_day.year, end_day.month, end_day.day,
                      tzinfo=UTC) + dt.timedelta(days=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _unwrap(instances):
    return [{k: (v.get_int64() if k in ("start", "end", "recurrence_id")
                 else v.get_string() if k in ("uid", "calendar_id",
                                              "status", "title")
                 else v.get_boolean())
             for k, v in item.items()} for item in instances]


def test_query_instances_matches_list_instances():
    from xx_calendar.service import CalendarService

    store = _seed_store()
    service = CalendarService(store, start_worker=False)
    try:
        day = dt.datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
        base = int(day.timestamp() * 1000)
        one_shot = "\r\n".join([
            "BEGIN:VEVENT", "UID:solo-1", "DTSTAMP:20260101T000000Z",
            "DTSTART:20260303T100000Z", "DTEND:20260303T110000Z",
            "SUMMARY:Solo", "END:VEVENT"])
        weekly = "\r\n".join([
            "BEGIN:VEVENT", "UID:weekly-1", "DTSTAMP:20260101T000000Z",
            "DTSTART:20260302T090000Z", "DTEND:20260302T100000Z",
            "RRULE:FREQ=WEEKLY;BYDAY=MO", "SUMMARY:Standup", "END:VEVENT"])
        all_day = "\r\n".join([
            "BEGIN:VEVENT", "UID:allday-1", "DTSTAMP:20260101T000000Z",
            "DTSTART;VALUE=DATE:20260304", "SUMMARY:Holiday", "END:VEVENT"])
        _put.store = store
        _put("solo-1", "local", one_shot, base + 86400000, base + 90000000)
        _put("weekly-1", "work", weekly, base, base + 3600000,
             rrule="FREQ=WEEKLY;BYDAY=MO")
        _put("allday-1", "local", all_day, base + 2 * 86400000,
             base + 3 * 86400000, all_day=True)
        start_ms, end_ms = _window_ms(dt.date(2026, 3, 1), dt.date(2026, 3, 31))
        direct = views.query_instances(store, start_ms, end_ms, ["local", "work"])
        via_service = _unwrap(service.list_instances(start_ms, end_ms, ["local", "work"]))
        assert direct == via_service
        assert {item["uid"] for item in direct} == {
            "solo-1", "weekly-1", "allday-1"}
        assert sum(1 for item in direct
                   if item["uid"] == "weekly-1") == 5
        unfiltered = views.query_instances(store, start_ms, end_ms, None)
        assert len(unfiltered) == len(direct)
        filtered = views.query_instances(store, start_ms, end_ms, ["local"])
        assert {item["uid"] for item in filtered} == {"solo-1", "allday-1"}
        with pytest.raises(ValueError):
            views.query_instances(store, end_ms, start_ms, [])
    finally:
        service.stop()


def test_query_instances_matches_overrides():
    from xx_calendar import ics as ics_module
    from xx_calendar.service import CalendarService

    store = _seed_store()
    service = CalendarService(store, start_worker=False)
    try:
        day = dt.datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
        base = int(day.timestamp() * 1000)
        parent = "\r\n".join([
            "BEGIN:VEVENT", "UID:series-9", "DTSTAMP:20260101T000000Z",
            "DTSTART:20260302T090000Z", "DTEND:20260302T100000Z",
            "RRULE:FREQ=WEEKLY;BYDAY=MO", "SUMMARY:Series", "END:VEVENT"])
        third = base + 2 * 7 * 86400000
        override = "\r\n".join([
            "BEGIN:VEVENT", "UID:series-9", "DTSTAMP:20260101T000000Z",
            "DTSTART:20260316T110000Z", "DTEND:20260316T120000Z",
            "RECURRENCE-ID:20260316T090000Z", "SUMMARY:Moved",
            "END:VEVENT"])
        _put.store = store
        _put("series-9", "local", parent, base, base + 3600000,
             rrule="FREQ=WEEKLY;BYDAY=MO")
        from xx_calendar.store import EventRecord

        store.put_event(EventRecord(uid="series-9", recurrence_id=third,
                                    calendar_id="local", ics=override,
                                    dtstart=third + 7200000,
                                    dtend=third + 10800000))
        start_ms, end_ms = _window_ms(dt.date(2026, 3, 1), dt.date(2026, 3, 31))
        direct = views.query_instances(store, start_ms, end_ms, ["local", "work"])
        via_service = _unwrap(service.list_instances(start_ms, end_ms, ["local", "work"]))
        assert direct == via_service
        moved = [i for i in direct if i["recurrence_id"] == third]
        assert len(moved) == 1
        assert moved[0]["title"] == "Moved"
        assert moved[0]["is_override"] is True
        parsed = ics_module.parse_events(override)
        assert parsed and parsed[0].summary() == "Moved"
    finally:
        service.stop()


def test_group_agenda_buckets_and_multiday():
    zone = ZoneInfo("UTC")
    monday = int(dt.datetime(2026, 3, 2, 9, 0,
                             tzinfo=UTC).timestamp() * 1000)
    instances = [
        {"uid": "a", "calendar_id": "local", "title": "Morning",
         "start": monday, "end": monday + 3600000, "all_day": False,
         "recurrence_id": 0, "is_override": False, "status": ""},
        {"uid": "b", "calendar_id": "local", "title": "Trip",
         "start": monday, "end": monday + 2 * 86400000, "all_day": False,
         "recurrence_id": 0, "is_override": False, "status": ""},
        {"uid": "c", "calendar_id": "local", "title": "Holiday",
         "start": monday, "end": monday + 86400000, "all_day": True,
         "recurrence_id": 0, "is_override": False, "status": ""},
    ]
    days = views.group_agenda(instances, zone, dt.date(2026, 3, 2),
                              dt.date(2026, 3, 4))
    assert [d.date for d in days] == [dt.date(2026, 3, 2),
                                     dt.date(2026, 3, 3),
                                     dt.date(2026, 3, 4)]
    assert [r.uid for r in days[0].all_day] == ["c"]
    assert [r.uid for r in days[0].timed] == ["a", "b"]
    assert [r.uid for r in days[1].timed] == ["b"]
    by_date = views.events_by_date(instances, zone, dt.date(2026, 3, 2),
                                   dt.date(2026, 3, 4))
    assert [r.uid for r in by_date[dt.date(2026, 3, 2)]] == ["c", "a", "b"]


def test_build_create_ics_round_trip():
    from xx_calendar import ics as ics_module

    start = int(dt.datetime(2026, 3, 2, 9, 0, tzinfo=UTC).timestamp() * 1000)
    text = views.build_create_ics("  Team lunch ", False, start,
                                  start + 3600000, reminder_minutes=10)
    events = ics_module.parse_events(text)
    assert len(events) == 1
    assert events[0].summary() == "Team lunch"
    assert events[0].dtstart()[0] == start
    assert events[0].dtend()[0] == start + 3600000
    assert events[0].reminder_minutes() == [10]
    day = int(dt.datetime(2026, 3, 4, 0, 0, tzinfo=UTC).timestamp() * 1000)
    all_day = views.build_create_ics("", True, day, day)
    parsed = ics_module.parse_events(all_day)[0]
    assert parsed.summary() == "(untitled)"
    assert parsed.dtstart()[1] is True


# ---------------------------------------------------- UX pass: page copy


def test_page_titles_read_as_words_not_caps():
    assert views.month_title(2026, 10) == "October 2026"
    assert views.day_title(dt.date(2026, 8, 15)) == "Sat 15 Aug"


def test_repeat_label_names_rules_never_raw_rrule():
    assert views.repeat_label("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR") \
        == "Weekdays"
    assert views.repeat_label("byday=MO,TU,WE,TH,FR;FREQ=WEEKLY") \
        == "Weekdays"
    assert views.repeat_label("FREQ=DAILY") == "Daily"
    assert views.repeat_label("FREQ=WEEKLY;BYDAY=MO,WE") == "Custom"
    assert "FREQ" not in views.repeat_label("FREQ=MONTHLY;COUNT=3")


def test_weekly_repeats_on_the_start_weekday():
    # A bare FREQ=WEEKLY recurs on DTSTART's own weekday; the old preset
    # pinned every weekly event to Mondays.
    assert dict(views.REPEAT_CHOICES)["Weekly"] == "FREQ=WEEKLY"


def test_untouched_custom_rule_survives_the_editor():
    rule = "FREQ=WEEKLY;BYDAY=MO,WE"
    choices = views.repeat_choices(rule)
    index = views.repeat_index(rule, choices)
    assert index is not None and choices[index] == ("Custom", rule)
    assert views.repeat_index("", choices) == 0
    standup = "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
    assert views.repeat_choices(standup) == list(views.REPEAT_CHOICES)
    assert views.REPEAT_CHOICES[views.repeat_index(standup)][0] == \
        "Weekdays"


def test_reminder_label_reads_in_words():
    assert views.reminder_label(10) == "10 min before"
    assert views.reminder_label(60) == "1 hour before"
    assert views.reminder_label(120) == "2 hours before"
    assert views.reminder_label(1440) == "1 day before"


def test_when_label_timed_and_all_day_spans():
    assert views.when_label(dt.datetime(2026, 8, 4, 6, 30),
                            dt.datetime(2026, 8, 4, 7, 30), False) \
        == "Tue 4 Aug 2026 · 06:30 – 07:30"
    # DTEND of an all-day event is exclusive: 5..8 Aug is Wed 5 – Fri 7.
    assert views.when_label(dt.datetime(2026, 8, 5), dt.datetime(2026, 8, 8),
                            True) == "Wed 5 – Fri 7 Aug 2026"
    assert views.when_label(dt.datetime(2026, 8, 21),
                            dt.datetime(2026, 8, 22), True) \
        == "Fri 21 Aug 2026"


def test_new_event_prefills_a_real_start_and_end():
    now = dt.datetime(2026, 10, 17, 14, 20)
    assert views.default_range(now.date(), now, 30) == \
        ("2026-10-17 15:00", "2026-10-17 15:30")
    assert views.default_range(dt.date(2026, 10, 20), now, 60) == \
        ("2026-10-20 09:00", "2026-10-20 10:00")
    late = dt.datetime(2026, 10, 17, 23, 40)
    assert views.default_range(late.date(), late, 30)[0].startswith(
        "2026-10-17")


def test_all_day_switch_retimes_entry_text():
    assert views.retime_text("2026-10-20 09:00", True) == "2026-10-20"
    assert views.retime_text("2026-10-20", False, 10) == "2026-10-20 10:00"
    assert views.retime_text("next tuesday", True) == "next tuesday"


def test_weekly_event_from_the_editor_recurs_on_its_own_weekday():
    from xx_calendar import ics as ics_module
    from xx_calendar import recurrence

    start = int(dt.datetime(2026, 10, 15, 9, tzinfo=UTC).timestamp() * 1000)
    text = views.build_create_ics("Swim", False, start, start + 1800000,
                                  rrule=dict(views.REPEAT_CHOICES)["Weekly"])
    event = ics_module.parse_events(text)[0]
    days = {dt.datetime.fromtimestamp(i.start_utc / 1000, UTC).strftime("%a")
            for i in recurrence.expand_vevent(event, start,
                                              start + 30 * 86400000)}
    assert days == {"Thu"}


def test_week_number_column_labels_rows_by_their_monday():
    from xx_calendar.month_grid import iso_week

    sunday_start = views.build_weeks(2026, 8, 6)
    # Row one runs Sun 26 Jul .. Sat 1 Aug: ISO week 31, not 30.
    assert sunday_start[0][0][0] == dt.date(2026, 7, 26)
    assert iso_week(sunday_start[0]) == 31
    assert iso_week(views.build_weeks(2026, 8, 0)[0]) == 31
