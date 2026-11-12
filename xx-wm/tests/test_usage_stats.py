"""Tests for screen-time / usage statistics (lane f_usage).

Covers toplevel state-array decoding and activated tracking
(toplevel_manager), the UsageTracker accounting/storage/query API
(usage_stats), the `usage_stats_enabled` config key, and the minimal
main.py wiring (tracker factory, notification counting, lock hooks).
Headless: no display, no compositor, no pywayland needed.
"""
import datetime
import json
import stat
import struct
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import toplevel_manager
from toplevel_manager import ToplevelManager, decode_state_array
from usage_stats import UsageTracker


def _pack(*states: int) -> bytes:
    return struct.pack('<' + 'I' * len(states), *states) if states else b''


class Clock:
    def __init__(self, ts: float) -> None:
        self.ts = float(ts)

    def __call__(self) -> float:
        return self.ts

    def advance(self, secs: float) -> None:
        self.ts += secs


def _noon_today() -> float:
    return datetime.datetime.now().replace(
        hour=12, minute=0, second=0, microsecond=0).timestamp()


def _tracker(tmp_path: Path, clock: Clock, **kwargs) -> UsageTracker:
    kwargs.setdefault('background_writes', False)
    return UsageTracker(
        data_dir=tmp_path / 'usage', now=clock, **kwargs)


# -- wall-clock jumps and the writer thread -------------------------------


def test_wall_clock_jump_is_not_screen_time(tmp_path: Path) -> None:
    wall = Clock(_noon_today() - 30 * 86400)
    mono = Clock(1000.0)
    tracker = _tracker(tmp_path, wall, monotonic=mono)
    tracker.on_activated_changed('org.a.App')
    mono.advance(60)
    wall.advance(30 * 86400 + 60)
    tracker.on_activated_changed(None)
    assert tracker.daily_totals(datetime.date.today()) == {'org.a.App': 60}
    assert len(tracker._days) == 1


def test_one_span_is_capped(tmp_path: Path) -> None:
    wall = Clock(_noon_today() - 20 * 3600)
    mono = Clock(0.0)
    tracker = _tracker(tmp_path, wall, monotonic=mono)
    tracker.on_activated_changed('org.a.App')
    mono.advance(20 * 3600)
    wall.advance(20 * 3600)
    tracker.on_activated_changed(None)
    total = sum(sum(day.apps.values()) for day in tracker._days.values())
    assert total == 12 * 3600


def test_flush_writes_off_the_calling_thread(tmp_path: Path, monkeypatch) -> None:
    import threading
    import usage_stats
    clock = Clock(_noon_today())
    tracker = UsageTracker(data_dir=tmp_path / 'usage', now=clock)
    threads = []
    real = usage_stats.UsageTracker._write_day

    def _spy(self, day, record):
        threads.append(threading.current_thread())
        real(self, day, record)

    monkeypatch.setattr(usage_stats.UsageTracker, '_write_day', _spy)
    tracker.on_activated_changed('org.a.App')
    clock.advance(30)
    tracker.flush(force=True)
    tracker.wait_writes()
    assert threads and threading.current_thread() not in threads
    assert (tmp_path / 'usage' / (datetime.date.today().isoformat() + '.json')).exists()


# -- state-array decoding -------------------------------------------------


def test_decode_activated_bytes() -> None:
    assert decode_state_array(_pack(2)) == (2,)


def test_decode_multiple_states_in_order() -> None:
    assert decode_state_array(_pack(0, 1, 3)) == (0, 1, 3,)


def test_decode_empty_bytes() -> None:
    assert decode_state_array(b'') == ()


def test_decode_trailing_partial_word_ignored() -> None:
    assert decode_state_array(_pack(2) + b'\x00') == (2,)


def test_decode_accepts_bytearray_and_memoryview() -> None:
    assert decode_state_array(bytearray(_pack(1, 2))) == (1, 2)
    assert decode_state_array(memoryview(_pack(3))) == (3,)


def test_decode_accepts_int_lists() -> None:
    assert decode_state_array([0, 2]) == (0, 2)
    assert decode_state_array((2,)) == (2,)


def test_decode_rejects_garbage() -> None:
    assert decode_state_array(None) == ()
    assert decode_state_array(2) == ()
    assert decode_state_array('activated') == ()


# -- backend _on_state plumbing --------------------------------------------


def _bare_backend():
    backend = toplevel_manager.WaylandToplevelBackend.__new__(
        toplevel_manager.WaylandToplevelBackend)
    calls: list[tuple[object, bool]] = []
    backend._state_cb = lambda handle, active: calls.append((handle, active))
    return backend, calls


def test_backend_state_reports_bytes_activation() -> None:
    backend, calls = _bare_backend()
    handle = object()
    backend._on_state(handle, _pack(0, 2))
    assert calls == [(id(handle), True)]


def test_backend_state_reports_deactivation() -> None:
    backend, calls = _bare_backend()
    handle = object()
    backend._on_state(handle, _pack(0, 1))
    assert calls == [(id(handle), False)]


def test_backend_state_empty_means_inactive() -> None:
    backend, calls = _bare_backend()
    handle = object()
    backend._on_state(handle, b'')
    assert calls == [(id(handle), False)]


def test_backend_state_without_subscriber_is_noop() -> None:
    backend = toplevel_manager.WaylandToplevelBackend.__new__(
        toplevel_manager.WaylandToplevelBackend)
    backend._state_cb = None
    backend._on_state(object(), _pack(2))  # must not raise


# -- manager activated tracking --------------------------------------------


class FakeStateBackend:
    """Backend double with the state-reporting contract."""

    def __init__(self, available: bool = True) -> None:
        self._available = available
        self._callback = None
        self._state_callback = None

    def available(self) -> bool:
        return self._available

    def connect(self, callback) -> None:
        self._callback = callback

    def connect_state(self, callback) -> None:
        self._state_callback = callback

    def activate(self, handle: object) -> None:
        pass

    def close(self, handle: object) -> None:
        pass

    def add(self, handle: object, app_id: str, title: str) -> None:
        self._callback(handle, app_id, title)

    def remove(self, handle: object) -> None:
        self._callback(handle, None, None)

    def state(self, handle: object, activated: bool) -> None:
        self._state_callback(handle, activated)


@pytest.fixture
def state_backend() -> FakeStateBackend:
    return FakeStateBackend()


@pytest.fixture
def state_manager(state_backend: FakeStateBackend) -> ToplevelManager:
    return ToplevelManager(backend=state_backend)


def test_activated_app_reported(
    state_backend: FakeStateBackend, state_manager: ToplevelManager,
) -> None:
    seen: list = []
    state_manager.on_activated_changed(seen.append)
    state_backend.add('h1', 'org.a.App', 'A')
    state_backend.add('h2', 'org.b.App', 'B')
    state_backend.state('h1', True)
    assert seen == ['org.a.App']
    assert state_manager.active_app_id == 'org.a.App'
    by_handle = {t.handle: t for t in state_manager.list()}
    assert by_handle['h1'].activated is True
    assert by_handle['h2'].activated is False


def test_deactivation_reports_none(
    state_backend: FakeStateBackend, state_manager: ToplevelManager,
) -> None:
    seen: list = []
    state_manager.on_activated_changed(seen.append)
    state_backend.add('h1', 'org.a.App', 'A')
    state_backend.state('h1', True)
    state_backend.state('h1', False)
    assert seen == ['org.a.App', None]
    assert state_manager.active_app_id is None


def test_shell_focus_reports_none(
    state_backend: FakeStateBackend, state_manager: ToplevelManager,
) -> None:
    seen: list = []
    state_manager.on_activated_changed(seen.append)
    state_backend.add('h1', 'org.a.App', 'A')
    state_backend.state('h1', True)
    state_backend.state('h1', False)
    state_backend.add('hs', 'io.piercingxx.XXWM', 'Shell')
    state_backend.state('hs', True)
    assert seen == ['org.a.App', None]
    assert state_manager.active_app_id is None


def test_keyboard_activation_never_reports(
    state_backend: FakeStateBackend, state_manager: ToplevelManager,
) -> None:
    seen: list = []
    state_manager.on_activated_changed(seen.append)
    state_backend.add('hk', 'squeekboard', 'Keyboard')
    state_backend.state('hk', True)
    assert seen == []
    assert state_manager.active_app_id is None


def test_activation_switches_to_latest(
    state_backend: FakeStateBackend, state_manager: ToplevelManager,
) -> None:
    seen: list = []
    state_manager.on_activated_changed(seen.append)
    state_backend.add('h1', 'org.a.App', 'A')
    state_backend.add('h2', 'org.b.App', 'B')
    state_backend.state('h1', True)
    state_backend.state('h2', True)
    assert seen[-1] == 'org.b.App'
    state_backend.state('h1', False)
    assert state_manager.active_app_id == 'org.b.App'
    assert seen[-1] == 'org.b.App'


def test_removing_active_window_reports_none(
    state_backend: FakeStateBackend, state_manager: ToplevelManager,
) -> None:
    seen: list = []
    state_manager.on_activated_changed(seen.append)
    state_backend.add('h1', 'org.a.App', 'A')
    state_backend.state('h1', True)
    state_backend.remove('h1')
    assert seen == ['org.a.App', None]


def test_unknown_app_id_waits_for_app_id_event(
    state_backend: FakeStateBackend, state_manager: ToplevelManager,
) -> None:
    seen: list = []
    state_manager.on_activated_changed(seen.append)
    state_backend.state('h9', True)  # no registry entry yet: no crash
    assert seen == []
    state_backend.add('h9', '', '')
    assert seen == []
    state_backend.add('h9', 'org.a.App', 'A')
    assert seen == ['org.a.App']


def test_no_duplicate_callback_without_change(
    state_backend: FakeStateBackend, state_manager: ToplevelManager,
) -> None:
    seen: list = []
    state_manager.on_activated_changed(seen.append)
    state_backend.add('h1', 'org.a.App', 'A')
    state_backend.state('h1', True)
    state_backend.add('h1', 'org.a.App', 'A (retitled)')
    assert seen == ['org.a.App']


def test_bad_activated_callback_isolated(
    state_backend: FakeStateBackend, state_manager: ToplevelManager,
) -> None:
    def boom(app_id) -> None:
        raise RuntimeError('boom')

    state_manager.on_activated_changed(boom)
    state_backend.add('h1', 'org.a.App', 'A')
    state_backend.state('h1', True)  # must not raise
    assert state_manager.active_app_id == 'org.a.App'


def test_backend_without_state_hook_tolerated() -> None:
    seen: list = []

    class PlainBackend:
        def __init__(self) -> None:
            self._callback = None

        def available(self) -> bool:
            return True

        def connect(self, callback) -> None:
            self._callback = callback

        def activate(self, handle: object) -> None:
            pass

        def close(self, handle: object) -> None:
            pass

    manager = ToplevelManager(backend=PlainBackend())
    manager.on_activated_changed(seen.append)
    assert manager.active_app_id is None
    assert seen == []


# -- tracker: activation accounting ----------------------------------------


def test_foreground_seconds_per_app(tmp_path: Path) -> None:
    clock = Clock(_noon_today())
    tracker = _tracker(tmp_path, clock)
    day = datetime.date.today()
    tracker.on_activated_changed('org.a.App')
    clock.advance(60)
    tracker.on_activated_changed('org.b.App')
    clock.advance(30)
    tracker.on_activated_changed(None)
    assert tracker.daily_totals(day) == {'org.a.App': 60, 'org.b.App': 30}


def test_no_app_active_accrues_nothing(tmp_path: Path) -> None:
    clock = Clock(_noon_today())
    tracker = _tracker(tmp_path, clock)
    clock.advance(120)
    assert tracker.daily_totals(datetime.date.today()) == {}


def test_blank_pauses_counting_and_forces_flush(tmp_path: Path) -> None:
    clock = Clock(_noon_today())
    tracker = _tracker(tmp_path, clock)
    day = datetime.date.today()
    tracker.on_activated_changed('org.a.App')
    clock.advance(60)
    tracker.on_blank_changed(True)
    path = tmp_path / 'usage' / (day.isoformat() + '.json')
    assert path.exists()
    assert json.loads(path.read_text(encoding='utf-8'))['apps'] == {'org.a.App': 60}
    clock.advance(600)
    tracker.on_blank_changed(False)
    clock.advance(30)
    assert tracker.daily_totals(day) == {'org.a.App': 90}


def test_lock_pauses_counting_and_counts_unlock(tmp_path: Path) -> None:
    clock = Clock(_noon_today())
    tracker = _tracker(tmp_path, clock)
    day = datetime.date.today()
    tracker.on_activated_changed('org.a.App')
    clock.advance(10)
    tracker.set_locked(True)
    path = tmp_path / 'usage' / (day.isoformat() + '.json')
    assert path.exists()
    clock.advance(100)
    tracker.set_locked(False)
    clock.advance(20)
    assert tracker.daily_totals(day) == {'org.a.App': 30}
    assert tracker.unlocks(day) == 1


def test_unlock_counted_only_on_transition(tmp_path: Path) -> None:
    clock = Clock(_noon_today())
    tracker = _tracker(tmp_path, clock)
    day = datetime.date.today()
    tracker.set_locked(False)
    assert tracker.unlocks(day) == 0
    tracker.set_locked(True)
    tracker.set_locked(True)
    tracker.set_locked(False)
    assert tracker.unlocks(day) == 1


def test_blank_chain_reaches_both_subscribers(tmp_path: Path) -> None:
    """The tracker chains; it never replaces the shell's own subscriber."""
    clock = Clock(_noon_today())
    tracker = _tracker(tmp_path, clock)
    day = datetime.date.today()
    shell_seen: list[bool] = []

    def chained(blanked: bool) -> None:
        shell_seen.append(blanked)
        tracker.on_blank_changed(blanked)

    tracker.on_activated_changed('org.a.App')
    clock.advance(10)
    chained(True)
    clock.advance(100)
    chained(False)
    clock.advance(10)
    assert shell_seen == [True, False]
    assert tracker.daily_totals(day) == {'org.a.App': 20}


def test_midnight_session_splits_across_days(tmp_path: Path) -> None:
    midnight = (datetime.datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0))
    clock = Clock((midnight - datetime.timedelta(seconds=60)).timestamp())
    tracker = _tracker(tmp_path, clock)
    tracker.on_activated_changed('org.a.App')
    clock.advance(120)
    tracker.on_activated_changed(None)
    prev_day = (midnight - datetime.timedelta(seconds=1)).date()
    assert tracker.daily_totals(prev_day) == {'org.a.App': 60}
    assert tracker.daily_totals(midnight.date()) == {'org.a.App': 60}


def test_notifications_counted_per_app(tmp_path: Path) -> None:
    clock = Clock(_noon_today())
    tracker = _tracker(tmp_path, clock)
    day = datetime.date.today()
    tracker.note_notification('org.a.App')
    tracker.note_notification('org.a.App')
    tracker.note_notification('org.b.App')
    tracker.note_notification('')
    assert tracker.notifications(day) == {'org.a.App': 2, 'org.b.App': 1}


# -- tracker: flush throttling and storage ----------------------------------


def test_no_writes_per_event(tmp_path: Path) -> None:
    # Activation changes, notifications and queries never touch the disk;
    # only flush (throttled) and blank/lock (forced) write.
    clock = Clock(_noon_today())
    tracker = _tracker(tmp_path, clock)
    tracker.on_activated_changed('org.a.App')
    clock.advance(60)
    tracker.on_activated_changed('org.b.App')
    tracker.note_notification('org.a.App')
    tracker.daily_totals(datetime.date.today())
    tracker.week(datetime.date.today())
    assert not (tmp_path / 'usage').exists() or not any(
        (tmp_path / 'usage').iterdir())


def test_flush_throttled_to_five_minutes(tmp_path: Path) -> None:
    clock = Clock(_noon_today())
    tracker = _tracker(tmp_path, clock)
    day = datetime.date.today()
    path = tmp_path / 'usage' / (day.isoformat() + '.json')
    tracker.on_activated_changed('org.a.App')
    clock.advance(10)
    assert tracker.flush() is False
    assert not path.exists()
    clock.advance(300)
    assert tracker.flush() is True
    before = json.loads(path.read_text(encoding='utf-8'))
    assert before['apps'] == {'org.a.App': 310}
    clock.advance(60)
    assert tracker.flush() is False
    assert json.loads(path.read_text(encoding='utf-8')) == before
    clock.advance(300)
    assert tracker.flush() is True
    after = json.loads(path.read_text(encoding='utf-8'))
    assert after['apps'] == {'org.a.App': 670}
    assert not list((tmp_path / 'usage').glob('*.tmp'))


def test_day_file_mode_is_0600(tmp_path: Path) -> None:
    clock = Clock(_noon_today())
    tracker = _tracker(tmp_path, clock)
    tracker.on_activated_changed('org.a.App')
    clock.advance(5)
    assert tracker.flush(force=True) is True
    path = tmp_path / 'usage' / (datetime.date.today().isoformat() + '.json')
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_prune_keeps_35_days(tmp_path: Path) -> None:
    clock = Clock(_noon_today())
    usage = tmp_path / 'usage'
    usage.mkdir(parents=True)
    today = datetime.date.today()
    for age in (0, 34, 35, 40):
        day = today - datetime.timedelta(days=age)
        (usage / (day.isoformat() + '.json')).write_text(
            json.dumps({'apps': {}, 'unlocks': 0, 'notifications': {}}),
            encoding='utf-8')
    (usage / 'not-a-day.json').write_text('{}', encoding='utf-8')
    _tracker(tmp_path, clock)
    remaining = {p.name for p in usage.iterdir()}
    assert (today - datetime.timedelta(days=40)).isoformat() + '.json' not in remaining
    assert (today - datetime.timedelta(days=35)).isoformat() + '.json' not in remaining
    assert (today - datetime.timedelta(days=34)).isoformat() + '.json' in remaining
    assert today.isoformat() + '.json' in remaining
    assert 'not-a-day.json' in remaining


def test_corrupt_day_file_reads_empty(tmp_path: Path) -> None:
    clock = Clock(_noon_today())
    usage = tmp_path / 'usage'
    usage.mkdir(parents=True)
    (usage / (datetime.date.today().isoformat() + '.json')).write_text(
        '{broken', encoding='utf-8')
    tracker = _tracker(tmp_path, clock)
    assert tracker.daily_totals(datetime.date.today()) == {}
    assert tracker.unlocks(datetime.date.today()) == 0


# -- tracker: disabled mode -------------------------------------------------


def test_disabled_records_nothing_and_leaves_files_alone(tmp_path: Path) -> None:
    clock = Clock(_noon_today())
    usage = tmp_path / 'usage'
    usage.mkdir(parents=True)
    day = datetime.date.today()
    existing = usage / (day.isoformat() + '.json')
    payload = {'apps': {'org.old.App': 42}, 'unlocks': 3, 'notifications': {}}
    existing.write_text(json.dumps(payload), encoding='utf-8')
    mtime = existing.stat().st_mtime_ns
    tracker = _tracker(tmp_path, clock, enabled=False)
    tracker.on_activated_changed('org.a.App')
    clock.advance(600)
    tracker.on_blank_changed(True)
    tracker.set_locked(True)
    tracker.set_locked(False)
    tracker.note_notification('org.a.App')
    assert tracker.flush(force=True) is False
    assert json.loads(existing.read_text(encoding='utf-8')) == payload
    assert existing.stat().st_mtime_ns == mtime
    assert {p.name for p in usage.iterdir()} == {existing.name}
    assert tracker.daily_totals(day) == {}
    assert tracker.unlocks(day) == 0
    assert tracker.notifications(day) == {}
    week = tracker.week(day)
    assert len(week) == 7 and all(total == 0 for _, total in week)


# -- tracker: queries --------------------------------------------------------


def test_week_returns_seven_days_ending_on_date(tmp_path: Path) -> None:
    base = _noon_today()
    today = datetime.date.today()
    clock = Clock(base - 86400)
    tracker = _tracker(tmp_path, clock)
    tracker.on_activated_changed('org.a.App')
    clock.advance(100)
    tracker.on_activated_changed(None)
    clock.ts = base
    tracker.on_activated_changed('org.b.App')
    clock.advance(50)
    tracker.on_activated_changed(None)
    week = tracker.week(today)
    assert [d for d, _ in week] == [
        today - datetime.timedelta(days=i) for i in range(6, -1, -1)]
    assert week[-2] == (today - datetime.timedelta(days=1), 100)
    assert week[-1] == (today, 50)
    assert week[0] == (today - datetime.timedelta(days=6), 0)


def test_queries_accept_iso_strings(tmp_path: Path) -> None:
    clock = Clock(_noon_today())
    tracker = _tracker(tmp_path, clock)
    day = datetime.date.today()
    tracker.on_activated_changed('org.a.App')
    clock.advance(15)
    tracker.on_activated_changed(None)
    tracker.note_notification('org.a.App')
    assert tracker.daily_totals(day.isoformat()) == {'org.a.App': 15}
    assert tracker.notifications(day.isoformat()) == {'org.a.App': 1}


def test_tracker_reads_previously_flushed_data(tmp_path: Path) -> None:
    clock = Clock(_noon_today())
    day = datetime.date.today()
    first = _tracker(tmp_path, clock)
    first.on_activated_changed('org.a.App')
    clock.advance(25)
    first.on_activated_changed(None)
    first.set_locked(True)
    first.set_locked(False)
    assert first.flush(force=True) is True
    second = _tracker(tmp_path, clock)
    assert second.daily_totals(day) == {'org.a.App': 25}
    assert second.unlocks(day) == 1


# -- config ------------------------------------------------------------------


def test_usage_stats_enabled_defaults_true(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    from config import DEFAULT_CONFIG, ShellConfig
    assert DEFAULT_CONFIG['usage_stats_enabled'] is True
    assert ShellConfig().usage_stats_enabled is True


def test_usage_stats_enabled_round_trips(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    from config import ShellConfig
    config = ShellConfig()
    config.set_usage_stats_enabled(False)
    assert ShellConfig().usage_stats_enabled is False
    config.set_usage_stats_enabled(True)
    assert ShellConfig().usage_stats_enabled is True


# -- main.py wiring ------------------------------------------------------------


def _quiet_app():
    import main
    app = main.XXWMApplication()
    app._maybe_banner = lambda *args, **kwargs: None
    return app


def _shell_ns(**overrides):
    config = types.SimpleNamespace(
        is_app_muted=lambda app_id: False, sound_notifications=False)
    shade_calls: list[tuple] = []
    shade = types.SimpleNamespace(
        add_notification=lambda *args: shade_calls.append(args))
    ns = dict(config=config, focus_state=None, dnd_state=None,
              _ensure_shade=lambda: shade, shade_calls=shade_calls)
    ns.update(overrides)
    return types.SimpleNamespace(**ns)


def test_make_tracker_disabled_returns_none() -> None:
    import main
    config = types.SimpleNamespace(usage_stats_enabled=False)
    assert main._make_usage_tracker(config) is None


def test_make_tracker_enabled_builds_tracker(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    import main
    config = types.SimpleNamespace(usage_stats_enabled=True)
    tracker = main._make_usage_tracker(config)
    assert isinstance(tracker, UsageTracker)
    assert tracker.enabled is True


def test_notification_hook_counts_delivered_only(tmp_path) -> None:
    clock = Clock(_noon_today())
    app = _quiet_app()
    app._shell = _shell_ns()
    app._usage_tracker = UsageTracker(
        data_dir=tmp_path / 'usage', now=clock)
    day = datetime.date.today()
    app._on_notification(1, 'App A', 'Hi', 'body', 'org.a.App', {})
    assert app._usage_tracker.notifications(day) == {'org.a.App': 1}
    assert app._shell.shade_calls and app._shell.shade_calls[0][0] == 1


def test_notification_hook_ignores_muted(tmp_path) -> None:
    clock = Clock(_noon_today())
    app = _quiet_app()
    muted = types.SimpleNamespace(
        is_app_muted=lambda app_id: True, sound_notifications=False)
    app._shell = _shell_ns(config=muted)
    app._usage_tracker = UsageTracker(
        data_dir=tmp_path / 'usage', now=clock)
    app._on_notification(2, 'App A', 'Hi', 'body', 'org.a.App', {})
    assert app._usage_tracker.notifications(datetime.date.today()) == {}
    assert app._shell.shade_calls == []


def test_lock_hooks_feed_tracker(tmp_path) -> None:
    clock = Clock(_noon_today())
    app = _quiet_app()
    calls: list[str] = []
    app._shell = types.SimpleNamespace(
        _show_lock_screen=lambda: calls.append('show'),
        _dismiss_lock_screen=lambda: calls.append('dismiss'))
    app._usage_tracker = UsageTracker(
        data_dir=tmp_path / 'usage', now=clock)
    app._wire_usage_lock()
    app._shell._show_lock_screen()
    app._shell._dismiss_lock_screen()
    assert calls == ['show', 'dismiss']
    assert app._usage_tracker.unlocks(datetime.date.today()) == 1
