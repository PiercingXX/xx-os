"""Broadway smoke test: the real window against a seeded database.

The GTK half runs in ``l5_smoke_child.py`` as a subprocess (same pattern
as the D-Bus tests): a Broadway display connection that outlives its
daemon kills the process that opened it, so the pytest process itself
never touches GDK. Skips cleanly when ``gtk4-broadwayd`` is missing.
"""

import datetime as dt
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
UTC = dt.timezone.utc


def _broadway_port(display):
    return 8080 + display


def _wait_port(port, timeout=15.0):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            import time as _time

            _time.sleep(0.1)
    return False


def _seed_db(path):
    from xx_calendar.store import (
        CalendarRecord,
        CalendarStore,
        EventRecord,
    )

    store = CalendarStore(str(path))
    store.create_calendar(CalendarRecord(
        id="local", display_name="On This Phone", account_name="device"))
    store.create_calendar(CalendarRecord(
        id="work", display_name="Work stuff that has a very long name",
        account_name="office"))
    today = dt.datetime.now(UTC).replace(hour=9, minute=0, second=0,
                                         microsecond=0)
    base = int(today.timestamp() * 1000)
    one = "\r\n".join([
        "BEGIN:VEVENT", "UID:smoke-solo", "DTSTAMP:20260101T000000Z",
        "DTSTART:%s" % today.strftime("%Y%m%dT%H%M%SZ"),
        "DTEND:%s" % (today + dt.timedelta(hours=1)).strftime(
            "%Y%m%dT%H%M%SZ"),
        "SUMMARY:A very long meeting title that must ellipsize on a "
        "320 pixel phone screen",
        "END:VEVENT"])
    store.put_event(EventRecord(uid="smoke-solo", calendar_id="local",
                                ics=one, dtstart=base,
                                dtend=base + 3600000))
    monday = today - dt.timedelta(days=today.weekday())
    monday = monday.replace(hour=9, minute=0)
    mbase = int(monday.timestamp() * 1000)
    weekly = "\r\n".join([
        "BEGIN:VEVENT", "UID:smoke-weekly", "DTSTAMP:20260101T000000Z",
        "DTSTART:%s" % monday.strftime("%Y%m%dT%H%M%SZ"),
        "DTEND:%s" % (monday + dt.timedelta(hours=1)).strftime(
            "%Y%m%dT%H%M%SZ"),
        "RRULE:FREQ=WEEKLY;BYDAY=MO", "SUMMARY:Standup", "END:VEVENT"])
    store.put_event(EventRecord(uid="smoke-weekly", calendar_id="work",
                                ics=weekly, dtstart=mbase,
                                dtend=mbase + 3600000,
                                rrule="FREQ=WEEKLY;BYDAY=MO"))
    moved_day = monday + dt.timedelta(weeks=1, hours=2)
    rid = int((monday + dt.timedelta(weeks=1)).timestamp() * 1000)
    override = "\r\n".join([
        "BEGIN:VEVENT", "UID:smoke-weekly", "DTSTAMP:20260101T000000Z",
        "DTSTART:%s" % moved_day.strftime("%Y%m%dT%H%M%SZ"),
        "DTEND:%s" % (moved_day + dt.timedelta(hours=1)).strftime(
            "%Y%m%dT%H%M%SZ"),
        "RECURRENCE-ID:%s" % (monday + dt.timedelta(
            weeks=1)).strftime("%Y%m%dT%H%M%SZ"),
        "SUMMARY:Standup moved", "END:VEVENT"])
    store.put_event(EventRecord(uid="smoke-weekly", recurrence_id=rid,
                                calendar_id="work", ics=override,
                                dtstart=rid + 7200000,
                                dtend=rid + 10800000))
    holiday = today.date() + dt.timedelta(days=3)
    all_day = "\r\n".join([
        "BEGIN:VEVENT", "UID:smoke-holiday", "DTSTAMP:20260101T000000Z",
        "DTSTART;VALUE=DATE:%s" % holiday.strftime("%Y%m%d"),
        "SUMMARY:Day off", "END:VEVENT"])
    start_ms = int(dt.datetime(holiday.year, holiday.month, holiday.day,
                               tzinfo=UTC).timestamp() * 1000)
    store.put_event(EventRecord(uid="smoke-holiday", calendar_id="local",
                                ics=all_day, dtstart=start_ms,
                                dtend=start_ms + 86400000, all_day=True))
    store.close()


def test_real_window_schedule_and_month(tmp_path):
    if shutil.which("gtk4-broadwayd") is None:
        pytest.skip("gtk4-broadwayd is not installed")
    display = 37
    daemon = subprocess.Popen(
        ["gtk4-broadwayd", f":{display:d}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not _wait_port(_broadway_port(display)):
            pytest.skip("gtk4-broadwayd did not listen")
        db_path = tmp_path / "smoke.db"
        _seed_db(db_path)
        env = dict(os.environ)
        env["GDK_BACKEND"] = "broadway"
        env["BROADWAY_DISPLAY"] = f":{display:d}"
        src = str(TESTS_DIR.parent / "src")
        common = str(TESTS_DIR.parent.parent / "common")
        env["PYTHONPATH"] = os.pathsep.join(
            [src, common] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH")
                             else []))
        child = subprocess.run(
            [sys.executable, str(TESTS_DIR / "l5_smoke_child.py"),
             str(db_path)],
            capture_output=True, text=True, env=env, timeout=90)
        assert child.returncode == 0, \
            f"smoke child failed:\n{child.stdout}\n{child.stderr}"
        assert "SMOKE=ok" in child.stdout, child.stdout
        for marker in ("EMPTY_NOTHING=ok", "NO_OVERFLOW=ok",
                       "BACK_STACK=ok"):
            assert marker in child.stdout, child.stdout
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=10)
        except subprocess.TimeoutExpired:
            daemon.kill()
