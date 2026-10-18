"""Tests for Nope-Mode: derived state, the launch gate, silent drops.

Ports the Android JVM tests (NopeController truth table, midnight-crossing
schedules, notification cancel/no-op) onto the shell model, pins every
launch path behind the one gate, and proves old configs migrate safely.
No test touches the network, accounts, or real apps: closing is driven
through a fake toplevel manager.
"""
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).parent.parent / 'launcher' / 'src'
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'common'))


import settings_page
from config import DEFAULT_CONFIG, ShellConfig
from nope_mode import (
    BREAK_MINUTES,
    NopeState,
    blocks_window,
    close_blocked_windows,
)

ALL_WEEK = [0, 1, 2, 3, 4, 5, 6]
NIGHTLY = [{'days': list(ALL_WEEK), 'start': '20:00', 'end': '08:00'}]

# Monday 2026-08-10 and Tuesday 2026-08-11, matching the Android tests.
MON_NOON = datetime(2026, 8, 10, 12, 0)
MON_2100 = datetime(2026, 8, 10, 21, 0)
MON_2000 = datetime(2026, 8, 10, 20, 0)
TUE_0300 = datetime(2026, 8, 11, 3, 0)
TUE_0800 = datetime(2026, 8, 11, 8, 0)


@pytest.fixture
def nope(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    state = NopeState(ShellConfig())
    state.set_schedules(NIGHTLY)
    state.set_app_blocked('com.example.game.desktop', True)
    return state


def _on(state, now=MON_2100, now_ts=1000.0):
    state._config.data['nope_enabled'] = True
    state._config.data['nope_override'] = 'force'
    state._config.data['nope_override_until'] = 0.0
    return state.is_active(now, now_ts)


class TestDerivedState:
    """The Android §6 truth table: master gates, override decides."""

    def test_master_off_means_everything_off(self, nope):
        assert not nope.is_active(MON_2100, now_ts=1000.0)
        assert not nope.is_blocked('com.example.game.desktop',
                                   MON_2100, 1000.0)

    def test_none_with_inactive_schedule_is_inactive(self, nope):
        nope.set_enabled(True)
        assert not nope.is_active(MON_NOON, now_ts=1000.0)

    def test_none_with_active_schedule_is_active(self, nope):
        nope.set_enabled(True)
        assert nope.is_active(MON_2100, now_ts=1000.0)

    def test_force_indefinite_is_active_regardless(self, nope):
        assert _on(nope, MON_NOON)
        assert _on(nope, MON_2100)

    def test_force_timed_before_expiry_is_active(self, nope):
        nope.set_enabled(True)
        nope.set_force(until_ts=2000.0)
        assert nope.is_active(MON_NOON, now_ts=1000.0)

    def test_force_timed_at_expiry_falls_back_to_schedule(self, nope):
        nope.set_enabled(True)
        nope.set_force(until_ts=1000.0)
        assert nope.is_active(MON_2100, now_ts=1000.0)
        assert not nope.is_active(MON_NOON, now_ts=1000.0)

    def test_break_running_holds_off_an_active_schedule(self, nope):
        nope.set_enabled(True)
        nope._config.data['nope_override'] = 'break'
        nope._config.data['nope_override_until'] = 1000.0 + 20 * 60
        assert not nope.is_active(MON_2100, now_ts=1000.0)

    def test_break_expired_auto_resumes(self, nope):
        nope.set_enabled(True)
        nope._config.data['nope_override'] = 'break'
        nope._config.data['nope_override_until'] = 500.0
        assert nope.is_active(MON_2100, now_ts=1000.0)
        assert not nope.is_active(MON_NOON, now_ts=1000.0)

    def test_break_too_far_in_future_is_a_moved_clock(self, nope):
        nope.set_enabled(True)
        nope._config.data['nope_override'] = 'break'
        nope._config.data['nope_override_until'] = 1000.0 + 2 * 3600
        assert nope.is_active(MON_2100, now_ts=1000.0)
        assert not nope.is_active(MON_NOON, now_ts=1000.0)

    def test_toggle_turns_on_from_off(self, nope):
        assert nope.toggle(MON_NOON, now_ts=1000.0)
        assert nope.is_active(MON_NOON, now_ts=1000.0)

    def test_toggle_turns_off_a_forced_session(self, nope):
        _on(nope, MON_NOON)
        assert not nope.toggle(MON_NOON, now_ts=1000.0)

    def test_toggle_off_during_schedule_disables_master(self, nope):
        nope.set_enabled(True)
        assert nope.is_active(MON_2100, now_ts=1000.0)
        assert not nope.toggle(MON_2100, now_ts=1000.0)
        assert not nope.enabled


class TestSchedules:
    """Midnight-crossing windows are the common path, not the edge."""

    def test_wrap_covers_both_sides_of_midnight(self, nope):
        nope.set_enabled(True)
        assert nope.is_active(MON_2100, now_ts=1.0)
        assert nope.is_active(TUE_0300, now_ts=1.0)
        assert not nope.is_active(MON_NOON, now_ts=1.0)

    def test_start_boundary_inclusive_end_exclusive(self, nope):
        nope.set_enabled(True)
        assert nope.is_active(MON_2000, now_ts=1.0)
        assert nope.is_active(datetime(2026, 8, 11, 7, 59), now_ts=1.0)
        assert not nope.is_active(TUE_0800, now_ts=1.0)

    def test_wrap_belongs_to_the_start_day(self, nope):
        nope.set_schedules(
            [{'days': [0], 'start': '20:00', 'end': '08:00'}])
        nope.set_enabled(True)
        assert nope.is_active(MON_2100, now_ts=1.0)
        assert nope.is_active(TUE_0300, now_ts=1.0)
        assert not nope.is_active(datetime(2026, 8, 11, 21, 0), now_ts=1.0)
        assert not nope.is_active(datetime(2026, 8, 12, 3, 0), now_ts=1.0)

    def test_disabled_schedule_and_empty_list_are_inactive(self, nope):
        nope.set_enabled(True)
        nope.set_schedules(
            [{'days': list(ALL_WEEK), 'start': '20:00', 'end': '08:00'}])
        nope._config.data['nope_schedules'] = []
        assert not nope.is_active(MON_2100, now_ts=1.0)

    def test_malformed_schedules_never_raise(self, nope):
        nope.set_enabled(True)
        nope._config.data['nope_schedules'] = [
            'nope', None, 42, {'days': 'x', 'start': '20:00', 'end': '08:00'},
            {'days': [0], 'start': '99:99', 'end': '08:00'},
        ]
        assert not nope.is_active(MON_2100, now_ts=1.0)

    def test_set_schedules_cleans_but_keeps_shape(self, nope):
        nope.set_schedules([
            {'days': [0, 'x'], 'start': '22:00', 'end': '06:30'},
            'junk',
        ])
        assert nope.schedules == [
            {'days': [0], 'start': '22:00', 'end': '06:30'}]


class TestBlockedApps:
    def test_blocked_needs_active_and_chosen(self, nope):
        assert not nope.is_blocked('com.example.game.desktop',
                                   MON_2100, 1000.0)
        _on(nope, MON_2100)
        assert nope.is_blocked('com.example.game.desktop', MON_2100, 1000.0)
        assert nope.is_blocked('com.example.game', MON_2100, 1000.0)
        assert not nope.is_blocked('org.gnome.Calculator', MON_2100, 1000.0)

    def test_empty_id_never_blocked(self, nope):
        _on(nope)
        assert not nope.is_blocked('', MON_2100, 1000.0)
        assert not nope.is_blocked(None, MON_2100, 1000.0)

    def test_unblock_removes_either_form(self, nope):
        nope.set_app_blocked('com.example.game', False)
        assert nope.nope_apps == []


class TestSilentDrops:
    """Nope drops; only Focus holds for later. The queue stays empty."""

    def test_silences_blocked_app(self, nope):
        _on(nope)
        assert nope.silences('com.example.game.desktop')
        assert nope.silences('', 'com.example.game')
        assert not nope.silences('org.gnome.Calculator')
        nope.clear_override()
        nope.set_enabled(True)
        assert not nope.silences('com.example.game.desktop',
                                 now=MON_NOON, now_ts=1000.0)

    def test_no_hold_queue_behind_silences(self, nope):
        _on(nope)
        assert nope.silences('com.example.game.desktop')
        assert not hasattr(nope, 'hold')
        assert not hasattr(nope, 'release_all')


class TestBreaks:
    def test_break_choices_mirror_focus_mode(self):
        assert BREAK_MINUTES == (5, 15, 30)

    def test_break_auto_resumes(self, nope):
        nope.set_enabled(True)
        ok, _reason = nope.start_break(5, MON_2100, 1000.0)
        assert ok
        assert not nope.is_active(MON_2100, now_ts=1000.0 + 4 * 60)
        assert nope.is_active(MON_2100, now_ts=1000.0 + 6 * 60)

    def test_break_refused_when_inactive(self, nope):
        ok, reason = nope.start_break(5, MON_NOON, 1000.0)
        assert not ok
        assert 'not active' in reason

    def test_break_refused_inside_min_interval(self, nope):
        nope.set_enabled(True)
        ok, _r = nope.start_break(5, MON_2100, 1000.0)
        assert ok
        nope.end_break()
        ok, reason = nope.start_break(
            5, MON_2100, 1000.0 + 10 * 60)
        assert not ok
        assert 'min to wait' in reason

    def test_budget_exhaustion_blocks(self, nope):
        base = MON_2100.timestamp()
        nope._config.data['nope_budget'] = 1
        nope._config.data['nope_min_interval'] = 0
        nope.set_enabled(True)
        ok, _r = nope.start_break(5, MON_2100, base)
        assert ok
        nope.end_break()
        ok, reason = nope.start_break(5, MON_2100, base + 1000.0)
        assert not ok
        assert 'No breaks left' in reason

    def test_friction_locked_while_active(self, nope):
        _on(nope)
        assert not nope.can_edit_friction(MON_2100, 1000.0)
        nope.set_enabled(False)
        assert nope.can_edit_friction(MON_2100, 1000.0)

    def test_end_break_is_not_a_bypass(self, nope):
        nope.set_enabled(True)
        nope.start_break(5, MON_2100, 1000.0)
        nope.end_break()
        assert nope.is_active(MON_2100, now_ts=1000.0)


class TestNoticeAndTile:
    def test_notice_names_app_and_lift_time(self, nope):
        nope.set_enabled(True)
        text = nope.notice_text('Game', MON_2100, 1000.0)
        assert text.startswith('Nope.')
        assert 'Game' in text
        assert '08:00' in text

    def test_tile_detail_counts_down_to_boundary(self, nope):
        nope.set_enabled(True)
        assert nope.tile_detail(MON_2100, 1000.0) == 'On · until 08:00'
        assert nope.tile_detail(MON_NOON, 1000.0) is None

    def test_format_tile_state(self):
        import quick_actions
        fmt = quick_actions.format_tile_state
        assert fmt('nope', True, 'On · until 17:00') == 'On · until 17:00'
        assert fmt('nope', True, None) == 'On'
        assert fmt('nope', False, 'On · until 17:00') == 'Off'

    def test_tile_registry(self):
        import quick_actions
        tile = next(t for t in quick_actions._TILES if t.key == 'nope')
        assert tile.label == 'Nope'
        assert len(tile.label) <= 8
        assert tile.tier == 2
        assert quick_actions.tile_settings_section('nope') == 'nope'
        assert quick_actions.tile_icon_name('nope') == \
            'xx-shade-nope-symbolic'

    def test_tile_toggle_writes_state_and_notifies(self, nope):
        import quick_actions
        panel = quick_actions.QuickActionsPanel.__new__(
            quick_actions.QuickActionsPanel)
        panel._nope = nope
        panel._tile_details = {}
        refreshed = []
        panel.refresh_states = lambda: refreshed.append(1)
        seen = []
        panel._on_nope_changed = lambda active: seen.append(active)
        panel._set_nope(True)
        assert nope.is_active()
        assert refreshed and seen == [True]
        panel._set_nope(False)
        assert not nope.is_active()
        assert seen == [True, False]


class _FakeHandle:
    def __init__(self, app_id):
        self.app_id = app_id
        self.handle = object()


class _FakeManager:
    def __init__(self, app_ids):
        self._topos = [_FakeHandle(a) for a in app_ids]
        self.closed = []

    def list(self):
        return list(self._topos)

    def close(self, handle):
        self.closed.append(handle)


class TestCloseBlocked:
    def test_only_blocked_windows_close(self, nope):
        _on(nope)
        manager = _FakeManager(
            ['com.example.game.desktop', 'org.gnome.Calculator.desktop'])
        closed = close_blocked_windows(manager, nope)
        assert closed == ['com.example.game.desktop']
        assert len(manager.closed) == 1

    def test_broken_manager_never_raises(self, nope):
        _on(nope)
        assert close_blocked_windows(object(), nope) == []


class TestGateEnumeration:
    """One test naming every launch path: each must consult the gate."""

    PATHS = [
        ('window.py', 'def _launch_app_id', '_nope_blocks'),
        ('window.py', 'def _dispatch_gesture_action', '_launch_app_id'),
        ('window.py', 'def preload_gesture_apps', 'is_blocked'),
        ('home_page.py', 'def _launch_slot', 'blocks_window'),
        ('drawer_page.py', 'def _launch_entry', 'blocks_window'),
        ('notification_shade.py', 'def _on_notif_tapped',
         '_nope_blocks_notif'),
        ('notification_shade.py', 'def _invoke_notif_action',
         '_nope_blocks_notif'),
        ('banner.py', 'def dispatch_tap', 'allow_fn'),
        ('banner.py', 'def _on_tap', '_nope_allows'),
        ('app_switcher.py', 'def _focus_app', 'is_blocked'),
        ('app_item_actions.py', 'def _run_desktop_action', 'is_blocked'),
        ('main.py', 'def _on_notification', 'silences'),
    ]

    def test_every_path_consults_the_gate(self):
        missing = []
        for filename, func, token in self.PATHS:
            text = (SRC / filename).read_text(encoding='utf-8')
            needle = func + '('
            starts = []
            at = 0
            while True:
                found = text.find(needle, at)
                if found == -1:
                    break
                starts.append(found)
                at = found + len(needle)
            assert starts, f'{filename} lost {func}'
            bodies = []
            for start in starts:
                depth_end = text.find('\n    def ', start + len(needle))
                bodies.append(
                    text[start:depth_end if depth_end != -1 else len(text)])
            if not any(token in body for body in bodies):
                missing.append(f'{filename}:{func} lacks {token}')
        assert not missing, missing

    def test_search_reaches_the_drawer_gate(self):
        text = (SRC / 'drawer_page.py').read_text(encoding='utf-8')
        for func in ('def _on_apps_search_changed',
                     'def _on_apps_search_activate'):
            assert func in text
            assert '_launch_entry' in text


class TestBlocksWindow:
    def test_blocked_shows_notice_and_stops(self, nope):
        _on(nope)
        shown = []

        class Win:
            nope_state = nope

            def _show_nope_notice(self, label):
                shown.append(label)

        assert blocks_window(Win(), 'com.example.game.desktop', 'Game')
        assert shown == ['Game']

    def test_allowed_passes_silently(self, nope):
        _on(nope)

        class Win:
            nope_state = nope

            def _show_nope_notice(self, label):
                raise AssertionError('must not notify')

        assert not blocks_window(Win(), 'org.gnome.Calculator')

    def test_window_without_state_never_blocks(self):
        assert not blocks_window(object(), 'com.example.game.desktop')


class TestBannerTapGate:
    def test_blocked_tap_reports_blocked_and_launches_nothing(self, nope):
        from banner import BannerState, dispatch_tap
        _on(nope)
        launched = []
        state = BannerState()
        state.show(7, 'Game', 'Hi', '', 'com.example.game.desktop',
                   [('default', 'Open')], {})
        result = dispatch_tap(
            state, None, lambda entry: launched.append(entry),
            allow_fn=lambda entry: not nope.is_blocked(entry))
        assert result == 'blocked'
        assert launched == []

    def test_allowed_tap_still_launches(self, nope):
        from banner import BannerState, dispatch_tap
        launched = []
        state = BannerState()
        state.show(7, 'Calc', 'Hi', '', 'org.gnome.Calculator.desktop',
                   [], {})
        result = dispatch_tap(
            state, None, lambda entry: launched.append(entry),
            allow_fn=lambda entry: not nope.is_blocked(entry))
        assert result == 'launch'
        assert launched == ['org.gnome.Calculator.desktop']


class TestConfigMigration:
    def test_new_keys_have_safe_defaults(self):
        for key, default in [
            ('nope_enabled', False),
            ('nope_apps', []),
            ('nope_schedules', []),
            ('nope_override', 'none'),
            ('nope_override_until', 0.0),
            ('nope_break_log', []),
            ('nope_min_interval', 30),
            ('nope_budget', 3),
        ]:
            assert DEFAULT_CONFIG[key] == default

    def test_legacy_config_without_nope_keys_loads(self, tmp_path,
                                                   monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))
        state = NopeState(ShellConfig())
        assert state.enabled is False
        assert state.nope_apps == []
        assert state.schedules == []
        assert state.override == 'none'
        assert not state.is_active()

    def test_malformed_values_fail_soft(self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))
        config = ShellConfig()
        config.data.update({
            'nope_apps': 'junk',
            'nope_schedules': 'junk',
            'nope_override': 'junk',
            'nope_override_until': 'junk',
            'nope_break_log': 'junk',
        })
        state = NopeState(config)
        assert state.nope_apps == []
        assert state.schedules == []
        assert state.override == 'none'
        assert state.override_until == 0.0
        assert state.break_log == []
        assert not state.is_active()

    def test_state_round_trips_through_config(self, tmp_path, monkeypatch):
        home = tmp_path / 'home'
        monkeypatch.setenv('HOME', str(home))
        first = NopeState(ShellConfig())
        first.set_app_blocked('a.desktop', True)
        first.set_schedules(NIGHTLY)
        first.set_force()
        second = NopeState(ShellConfig())
        assert second.nope_apps == ['a.desktop']
        assert second.schedules == NIGHTLY
        assert second.override == 'force'


class TestSettingsHelpers:
    def test_schedule_summary(self):
        from settings_nope import schedule_summary
        assert schedule_summary(NIGHTLY[0]) == \
            '20:00 → 08:00 · every day · overnight'
        assert schedule_summary(
            {'days': [0, 1, 2, 3, 4], 'start': '20:00',
             'end': '08:00'}) == '20:00 → 08:00 · weeknights · overnight'
        assert schedule_summary({}) == 'Invalid schedule'

    def test_schedule_form_parsing(self):
        from settings_nope import parse_schedule_form
        sched, error = parse_schedule_form(
            {'days': '0,1,2,3,4,5,6', 'start': '20:00', 'end': '08:00'})
        assert error is None
        assert sched == {'days': [0, 1, 2, 3, 4, 5, 6], 'start': '20:00',
                         'end': '08:00'}
        assert parse_schedule_form(
            {'days': 'funday', 'start': '20:00', 'end': '08:00'})[1]
        assert parse_schedule_form(
            {'days': '0', 'start': '8pm', 'end': '08:00'})[1]
        named, error = parse_schedule_form(
            {'days': 'Mon-Fri', 'start': '20:00', 'end': '08:00'})
        assert error is None and named['days'] == [0, 1, 2, 3, 4]
        assert parse_schedule_form(
            {'days': 'monday, Sat', 'start': '20:00',
             'end': '08:00'})[0]['days'] == [0, 5]

    def test_blocked_summary(self):
        from settings_nope import blocked_summary
        assert blocked_summary([]) == 'None'
        assert blocked_summary(['Game', 'Zebra']) == 'Game, Zebra'
        assert blocked_summary(['a', 'b', 'c']) == '3 apps'

    def test_app_choices_sorted_and_flagged(self, nope):
        from settings_nope import app_choices
        entries = [SimpleNamespace(app_id='z.desktop', name='Zebra'),
                   SimpleNamespace(app_id='com.example.game.desktop',
                                   name='Game')]
        win = SimpleNamespace(
            app_index=SimpleNamespace(entries=entries),
            config=SimpleNamespace(
                label_for=lambda app_id, fallback: fallback),
            nope_state=nope)
        assert app_choices(win) == [
            ('com.example.game.desktop', 'Game', True),
            ('z.desktop', 'Zebra', False),
        ]

    def test_nope_section_registered(self):
        keys = dict(settings_page.SETTINGS_SECTIONS)
        assert keys['nope'].title == 'Nope-Mode'
        placed = [key for _cat, _t, keys in
                  settings_page.SETTINGS_CATEGORIES for key in keys]
        assert placed.count('nope') == 1
        assert 'nope' in settings_page._SETTINGS_SECTION_KEYS
