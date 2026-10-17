"""GLib scheduling policy with a fake main loop (no display, no waiting)."""

from xx_clock import request_codes as codes
from xx_clock.models import Alarm, TimerItem
from xx_clock.scheduler import Scheduler


class FakeGLib:
    def __init__(self, now_ms=1_000_000):
        self.now_ms = now_ms
        self.armed: dict[int, int] = {}
        self.cancelled: list[int] = []
        self._next = 1

    def timeout_add(self, delay_ms, _callback):
        source = self._next
        self._next += 1
        self.armed[source] = delay_ms
        return source

    def source_remove(self, source):
        self.cancelled.append(source)
        self.armed.pop(source, None)


def _alarm(alarm_id=7):
    return Alarm(id=alarm_id, hour=7, minute=30)


def test_schedule_alarm_arms_delay_until_deadline():
    glib = FakeGLib(now_ms=1_000_000)
    sched = Scheduler(glib, clock=lambda: glib.now_ms)
    fired = []
    sched.schedule_alarm(_alarm(), 1_000_000 + 60_000, fired.append)
    assert glib.armed
    assert list(glib.armed.values()) == [60_000]
    assert sched.pending() == 1


def test_overdue_deadline_arms_immediately():
    glib = FakeGLib(now_ms=2_000_000)
    sched = Scheduler(glib, clock=lambda: glib.now_ms)
    sched.schedule_alarm(_alarm(), 1_000_000, lambda eid: None)
    assert list(glib.armed.values()) == [0]


def test_reschedule_replaces_source_and_cancel_removes_it():
    glib = FakeGLib()
    sched = Scheduler(glib, clock=lambda: glib.now_ms)
    sched.schedule_alarm(_alarm(), 2_000_000, lambda eid: None)
    first = list(glib.armed)
    sched.schedule_alarm(_alarm(), 3_000_000, lambda eid: None)
    assert first[0] in glib.cancelled
    assert sched.pending() == 1
    sched.cancel_alarm(7)
    assert sched.pending() == 0
    assert len(glib.armed) == 0


def test_schedule_timers_arms_running_only_and_drops_stale():
    glib = FakeGLib(now_ms=1_000_000)
    sched = Scheduler(glib, clock=lambda: glib.now_ms)
    timers = [TimerItem(id=1, duration_ms=60_000, state="RUNNING",
                        ends_at_ms=1_060_000, remaining_ms=0, label=""),
              TimerItem(id=2, duration_ms=60_000, state="PAUSED",
                        ends_at_ms=0, remaining_ms=30_000, label="")]
    sched.schedule_timers(timers, lambda tid: None)
    assert sched.pending() == 1
    sched.schedule_timers([], lambda tid: None)
    assert sched.pending() == 0


def test_truncation_twins_keep_distinct_sources():
    glib = FakeGLib()
    sched = Scheduler(glib, clock=lambda: glib.now_ms)
    twin = 1_750_000_000_123
    sched.schedule_alarm(_alarm(twin), 2_000_000, lambda eid: None)
    sched.schedule_alarm(_alarm(twin + (1 << 32)), 2_000_000, lambda eid: None)
    assert sched.pending() == 2


def test_kinds_do_not_share_sources():
    assert codes.request_code(codes.REQ_KIND_ALARM, 9) != codes.request_code(
        codes.REQ_KIND_TIMER, 9)
