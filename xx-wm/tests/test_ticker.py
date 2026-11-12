"""The shared ticker: minute-aligned clocks, one source at a time, asleep
while the display is blank (only while_blank jobs run), catch-up on wake;
and the shell's periodic checks living on it instead of GLib timers of
their own."""
import types

import pytest


class _Loop:
    """Fake GLib timeouts on a fake wall clock."""

    def __init__(self, now=1000.0):
        self.now = now
        self.sources = {}
        self.next_id = 0

    def add(self, ms, cb):
        self.next_id += 1
        self.sources[self.next_id] = (self.now + ms / 1000.0, cb)
        return self.next_id

    def remove(self, src):
        self.sources.pop(src, None)

    def advance(self, seconds):
        end = self.now + seconds
        while True:
            due = [(t, sid) for sid, (t, _cb) in self.sources.items()
                   if t <= end]
            if not due:
                break
            t, sid = min(due)
            self.now = max(self.now, t)
            _t, cb = self.sources.pop(sid)
            if cb():
                self.sources[sid] = (self.now, cb)
        self.now = end


def _ticker(loop):
    import ticker
    return ticker.Ticker(timeout_add=loop.add, source_remove=loop.remove,
                         clock=lambda: loop.now)


@pytest.fixture
def shared(monkeypatch):
    import ticker
    loop = _Loop()
    fake = _ticker(loop)
    monkeypatch.setattr(ticker, '_TICKER', fake)
    return types.SimpleNamespace(loop=loop, ticker=fake)


class TestTicker:
    def test_minute_job_wakes_once_a_minute_on_the_boundary(self):
        loop = _Loop(now=1000.0)  # 16:40 past the epoch minute
        tick = _ticker(loop)
        runs = []
        tick.add(lambda: runs.append(loop.now), 60, align_minute=True)
        loop.advance(600)
        assert len(runs) == 10
        assert all(abs(t % 60 - 0.05) < 0.01 for t in runs)

    def test_one_source_for_many_jobs(self):
        loop = _Loop()
        tick = _ticker(loop)
        for period in (30, 60, 60):
            tick.add(lambda: None, period)
        assert len(loop.sources) == 1

    def test_blank_sleeps_and_wake_catches_up(self):
        loop = _Loop()
        tick = _ticker(loop)
        clock, status, guard = [], [], []
        tick.add(lambda: clock.append(loop.now), 60, align_minute=True)
        tick.add(lambda: status.append(loop.now), 60)
        tick.add(lambda: guard.append(loop.now), 60, while_blank=True)
        tick.set_blanked(True)
        loop.advance(600)
        assert clock == [] and status == []
        assert len(guard) == 10
        tick.set_blanked(False)
        assert len(clock) == 1 and len(status) == 1

    def test_blank_with_only_screen_jobs_leaves_no_source(self):
        loop = _Loop()
        tick = _ticker(loop)
        tick.add(lambda: None, 60, align_minute=True)
        tick.set_blanked(True)
        assert loop.sources == {}

    def test_false_drops_the_job(self):
        loop = _Loop()
        tick = _ticker(loop)
        runs = []
        handle = tick.add(lambda: runs.append(1) or False, 30)
        loop.advance(120)
        assert runs == [1]
        assert not tick.has(handle)
        assert loop.sources == {}

    def test_remove_and_failing_job(self):
        loop = _Loop()
        tick = _ticker(loop)
        handle = tick.add(lambda: 1 / 0, 30)
        loop.advance(65)
        assert tick.has(handle)
        tick.remove(handle)
        assert loop.sources == {}


class TestClockSettings:
    def test_gsettings_built_once(self, monkeypatch):
        import clock_format
        from gi.repository import Gio
        built = []
        real = Gio.Settings.new_full
        monkeypatch.setattr(clock_format, '_settings', None)
        monkeypatch.setattr(
            Gio.Settings, 'new_full',
            lambda *a: built.append(1) or real(*a))
        for _ in range(5):
            clock_format.resolve_24h(types.SimpleNamespace(clock_24h=None))
        assert len(built) <= 1


class TestShellWiring:
    def test_display_blank_pauses_the_ticker(self, shared):
        import main
        app = types.SimpleNamespace(
            _shell=None, _idle_blank_hooks=lambda _b: None)
        main.XXWMApplication._on_display_blank(app, True)
        assert shared.ticker.blanked is True
        main.XXWMApplication._on_display_blank(app, False)
        assert shared.ticker.blanked is False

    def test_home_clock_and_status_are_ticker_jobs(self):
        from pathlib import Path
        src = (Path(__file__).parent.parent / 'launcher' / 'src'
               / 'window.py').read_text(encoding='utf-8')
        assert 'timeout_add_seconds(1, self._tick_clock)' not in src
        assert ('get_ticker().add(self._tick_clock, 60, align_minute=True)'
                in src)
        assert 'timeout_add_seconds(60, self._tick_status)' not in src

    def test_focus_release_runs_only_while_something_is_held(self, shared):
        import main
        from focus_mode import FocusState
        config = types.SimpleNamespace(
            data={'focus_enabled': True, 'focus_apps': ['org.example.Chat']},
            save=lambda: None)
        focus = FocusState(config)
        shown = []
        shade = types.SimpleNamespace(
            add_notification=lambda *a: shown.append(a),
            dismiss=lambda *a, **k: None)
        app = main.XXWMApplication()
        app._maybe_banner = lambda *a, **k: None
        app._usage_tracker = None
        app._shell = types.SimpleNamespace(
            config=types.SimpleNamespace(
                is_app_muted=lambda _a: False, sound_notifications=False,
                label_for=lambda _a, n: n),
            focus_state=focus, dnd_state=None, nope_state=None,
            _ensure_shade=lambda: shade, _shade=shade,
            app_index=types.SimpleNamespace(entries=[]))
        assert len(shared.ticker) == 0
        app._on_notification(5, 'Chat', 'S', 'B', 'org.example.Chat', {})
        app._on_notification(6, 'Chat', 'S', 'B', 'org.example.Chat', {})
        assert len(shared.ticker) == 1
        shared.loop.advance(60)
        assert shown == [] and len(shared.ticker) == 1
        focus.set_enabled(False)
        shared.loop.advance(30)
        assert len(shown) == 2
        assert len(shared.ticker) == 0

    def test_xxdb_idle_check_only_while_on(self, shared):
        import main
        ctrl = types.SimpleNamespace(enabled=False, starting=False,
                                     idle_check=lambda: True)
        app = types.SimpleNamespace(_xxdb=ctrl, _xxdb_job=None)
        app._tick_xxdb = types.MethodType(main.XXWMApplication._tick_xxdb, app)
        main.XXWMApplication._arm_xxdb_idle(app)
        assert len(shared.ticker) == 0
        ctrl.enabled = True
        main.XXWMApplication._arm_xxdb_idle(app)
        main.XXWMApplication._arm_xxdb_idle(app)
        assert len(shared.ticker) == 1
        job = shared.ticker._jobs[app._xxdb_job]
        assert job.while_blank  # an open port still closes on time
        ctrl.enabled = False
        main.XXWMApplication._arm_xxdb_idle(app)
        assert len(shared.ticker) == 0

    def test_dnd_toggle_redraws_flags_at_once(self):
        from dnd import DndState
        config = types.SimpleNamespace(data={}, save=lambda: None)
        state = DndState(config)
        calls = []
        state.on_change = lambda: calls.append(1)
        state.set_enabled(True)
        state.set_enabled(False)
        assert calls == [1, 1]


class TestStandbyOff:
    def _ctl(self, minutes, saver_auto=True):
        import app_standby
        spawned = []
        config = types.SimpleNamespace(
            app_standby_minutes=minutes, standby_exempt=[],
            battery_saver_auto=saver_auto, battery_saver=False)
        ctl = app_standby.StandbyController(
            config=config, pw_dump_runner=lambda: spawned.append(1) or '[]')
        ctl._session_bus_factory = lambda: None
        ctl._system_bus_factory = lambda: None
        return ctl, spawned

    def _join(self, thread):
        if thread is not None:
            thread.join(5)

    def test_disabled_standby_runs_no_pw_dump(self, monkeypatch):
        import app_standby
        threads = []
        real = app_standby.threading.Thread

        def _thread(*a, **k):
            t = real(*a, **k)
            threads.append(t)
            return t
        monkeypatch.setattr(app_standby.threading, 'Thread', _thread)
        ctl, spawned = self._ctl(0)
        ctl._refresh_tick()
        for t in threads:
            t.join(5)
        assert spawned == []
        assert [t.name for t in threads] == ['xx-wm-standby-battery']

    def test_disabled_standby_and_saver_does_nothing(self, monkeypatch):
        import app_standby
        threads = []
        monkeypatch.setattr(app_standby.threading, 'Thread',
                            lambda *a, **k: threads.append(k) or
                            types.SimpleNamespace(start=lambda: None))
        ctl, spawned = self._ctl(0, saver_auto=False)
        ctl._refresh_tick()
        assert threads == [] and spawned == []

    def test_enabled_standby_still_probes_audio(self, monkeypatch):
        import app_standby
        threads = []
        real = app_standby.threading.Thread

        def _thread(*a, **k):
            t = real(*a, **k)
            threads.append(t)
            return t
        monkeypatch.setattr(app_standby.threading, 'Thread', _thread)
        monkeypatch.setattr(app_standby.GLib, 'idle_add',
                            lambda *a, **k: 0)
        ctl, spawned = self._ctl(10)
        ctl._refresh_tick()
        for t in threads:
            t.join(5)
        assert spawned == [1]
