"""Store behavior: round-trips, ordering, runtime, garbage tolerance."""


import pytest

from xx_clock.models import Alarm, TimerItem
from xx_clock.store import Store


@pytest.fixture()
def store(tmp_path):
    owned = Store(path=tmp_path / "clock.db")
    yield owned
    owned.close()


def test_first_run_creates_dirs_and_db(tmp_path, monkeypatch):
    from xx_clock import paths
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "fresh-data"))
    assert not (tmp_path / "fresh-data").exists()
    owned = Store()
    try:
        assert paths.db_path().is_file()
        assert (tmp_path / "fresh-data" / "xx-clock").stat().st_mode & 0o700 == 0o700
    finally:
        owned.close()


def test_alarms_round_trip_and_sort_by_time(store):
    first = store.next_id()
    store.save_alarm(Alarm(id=first, hour=8, minute=0, label="late"))
    second = store.next_id()
    store.save_alarm(Alarm(id=second, hour=6, minute=30, label="early"))
    assert [(a.hour, a.minute) for a in store.alarms()] == [(6, 30), (8, 0)]
    assert store.get_alarm(first).label == "late"
    assert store.get_alarm(999_999) is None


def test_garbage_rows_do_not_abort_the_listing(store):
    good = store.next_id()
    store.save_alarm(Alarm(id=good, hour=7, minute=0))
    with store._lock:
        store._db.execute("INSERT INTO alarms(id, hour, minute, days_mask, label, enabled,"
                          " vibrate, sound_uri) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                          (good + 1, 25, 0, 0, "garbage", 1, 1, None))
        store._db.execute("INSERT INTO timers(id, duration_ms, state, ends_at_ms,"
                          " remaining_ms, label) VALUES(?, ?, ?, ?, ?, ?)",
                          (good + 2, 60_000, "BOGUS", 0, 0, ""))
        store._db.commit()
    assert [a.id for a in store.alarms()] == [good]
    assert store.timers() == []


def test_delete_clears_runtime(store):
    alarm_id = store.next_id()
    store.save_alarm(Alarm(id=alarm_id, hour=7, minute=0))
    store.set_scheduled_fire(alarm_id, 123)
    store.set_ringing(alarm_id, True)
    store.delete_alarm(alarm_id)
    assert store.get_alarm(alarm_id) is None
    assert store.scheduled_fire(alarm_id) == 0
    assert not store.is_ringing(alarm_id)


def test_runtime_defaults_and_ringing_wins_newest(store):
    assert store.scheduled_fire(4242) == 0
    assert store.snoozed_until(4242) == 0
    assert not store.is_ringing(4242)
    assert store.ringing_id() is None
    first, second = store.next_id(), store.next_id()
    store.set_ringing(first, True)
    store.set_ringing(second, True)
    assert store.ringing_id() == max(first, second)
    store.clear_runtime(second)
    assert store.ringing_id() == first


def test_timers_round_trip(store):
    timer_id = store.next_id()
    store.save_timer(TimerItem(id=timer_id, duration_ms=90_000, state="PAUSED",
                               ends_at_ms=0, remaining_ms=30_000, label="tea"))
    timer = store.get_timer(timer_id)
    assert timer is not None
    assert (timer.duration_ms, timer.state, timer.remaining_ms) == (90_000, "PAUSED", 30_000)
    store.delete_timer(timer_id)
    assert store.get_timer(timer_id) is None


def test_ids_keep_rising_above_wall_clock(store):
    import time as _time
    before = int(_time.time() * 1000)
    minted = [store.next_id() for _ in range(3)]
    assert minted == sorted(minted)
    assert len(set(minted)) == 3
    assert all(value > before for value in minted)


def test_corrupt_db_file_quarantines_and_starts_empty(tmp_path):
    from xx_clock.store import Store as StoreClass
    path = tmp_path / "broken.db"
    path.write_bytes(b"not a database at all")
    owned = StoreClass(path=path)
    try:
        assert owned.alarms() == []
        assert owned.timers() == []
        assert len(list(tmp_path.glob("broken.db.corrupt-*"))) == 1
    finally:
        owned.close()
