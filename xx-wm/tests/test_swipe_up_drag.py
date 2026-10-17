"""Swipe-up capture drag: vertical swipes anywhere on home.

The stack's GestureSwipe sits in the default BUBBLE phase, so a swipe
starting on a home slot button was claimed by the button's click gesture
and never reached the stack. A capture-phase GestureDrag now owns those
touches instead — but only after a vertical-dominant drag threshold, so
taps, long-press edit mode and horizontal swipes keep working. On
release the drag decides drawer (fast/long) vs keyboard (short) by
distance and velocity.
"""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


@pytest.fixture(scope='module')
def window_mod():
    """Window module (imported at test time, after conftest restores the
    real gi stack when available). Collection order decides which gi
    stack window bound its Gtk name to — a minimal fake from another
    module may lack EventSequenceState, so provide the CLAIMED token the
    drag handler needs when it is missing (a no-op against real Gtk)."""
    import types as _types

    import window as mod

    gtk = getattr(mod, 'Gtk', None)
    if gtk is not None and not hasattr(gtk, 'EventSequenceState'):
        gtk.EventSequenceState = _types.SimpleNamespace(CLAIMED='claimed')
    return mod


class TestClaimThreshold:
    def test_tap_jitter_never_claims(self, window_mod):
        assert window_mod.should_claim_vertical_drag(0, 0) is False
        assert window_mod.should_claim_vertical_drag(2, -5) is False
        assert window_mod.should_claim_vertical_drag(-3, 8) is False

    def test_threshold_boundary(self, window_mod):
        claim = window_mod._SWIPE_UP_CLAIM_PX
        assert window_mod.should_claim_vertical_drag(0, -claim) is False
        assert window_mod.should_claim_vertical_drag(0, -(claim + 1)) is True

    def test_vertical_dominance_required(self, window_mod):
        assert window_mod.should_claim_vertical_drag(60, -10) is False
        assert window_mod.should_claim_vertical_drag(-80, 20) is False
        assert window_mod.should_claim_vertical_drag(0, -40) is True
        assert window_mod.should_claim_vertical_drag(5, 50) is True

    def test_down_also_claims(self, window_mod):
        assert window_mod.should_claim_vertical_drag(0, 40) is True


class TestDecideSwipeUpAction:
    def test_fast_short_flick_opens_drawer(self, window_mod):
        assert window_mod.decide_swipe_up_action(40, -2000) == 'drawer'

    def test_slow_long_drag_opens_drawer(self, window_mod):
        assert window_mod.decide_swipe_up_action(150, -350) == 'drawer'

    def test_drawer_distance_boundary(self, window_mod):
        drawer_px = window_mod._SWIPE_UP_DRAWER_PX
        assert window_mod.decide_swipe_up_action(drawer_px, -350) == 'drawer'
        assert (window_mod.decide_swipe_up_action(drawer_px - 1, -350)
                == 'keyboard')

    def test_short_flick_raises_keyboard(self, window_mod):
        assert window_mod.decide_swipe_up_action(40, -500) == 'keyboard'
        assert window_mod.decide_swipe_up_action(60, -301) == 'keyboard'

    def test_below_claim_distance_is_dead(self, window_mod):
        """A tap that somehow reaches release (fast thumb bounce) still
        does nothing: below the claim threshold there is no action."""
        assert window_mod.decide_swipe_up_action(5, -5000) is None
        assert window_mod.decide_swipe_up_action(0, -5000) is None

    def test_slow_release_is_dead(self, window_mod):
        """A list-scroll release (slow despite the travel) acts on nothing."""
        assert window_mod.decide_swipe_up_action(60, -100) is None
        assert window_mod.decide_swipe_up_action(60, 0) is None


class TestReleaseVelocity:
    def test_single_sample_is_zero(self, window_mod):
        assert window_mod.swipe_release_velocity([(0.0, 1.0, 2.0)]) == (0.0, 0.0)

    def test_no_samples_is_zero(self, window_mod):
        assert window_mod.swipe_release_velocity([]) == (0.0, 0.0)

    def test_stopped_clock_is_zero(self, window_mod):
        assert window_mod.swipe_release_velocity(
            [(10.0, 0.0, 0.0), (10.0, 5.0, 5.0)]) == (0.0, 0.0)

    def test_straight_flick(self, window_mod):
        vx, vy = window_mod.swipe_release_velocity(
            [(0.0, 100.0, 500.0), (100.0, 100.0, 450.0)])
        assert vx == pytest.approx(0.0)
        assert vy == pytest.approx(-500.0)

    def test_old_history_ignored(self, window_mod):
        """A long slow press ending in a fast flick measures the flick."""
        vx, vy = window_mod.swipe_release_velocity([
            (0.0, 100.0, 900.0),
            (400.0, 100.0, 900.0),
            (500.0, 100.0, 800.0),
        ])
        assert vx == pytest.approx(0.0)
        assert vy == pytest.approx(-1000.0)


class _FakeStack:
    def __init__(self, page: str) -> None:
        self._page = page
        self.shown: list[str] = []

    def get_visible_child_name(self) -> str:
        return self._page

    def set_visible_child_name(self, name: str) -> None:
        self._page = name
        self.shown.append(name)


class _FakeGesture:
    def __init__(self) -> None:
        self.states: list = []

    def set_state(self, state) -> None:
        self.states.append(state)


class _FakeGLib:
    now_us = 0

    @classmethod
    def get_monotonic_time(cls) -> int:
        return cls.now_us


def _handler_ns(window_mod, monkeypatch, page='home'):
    """Bare namespace driving the unbound drag handlers with a stub clock."""
    glib_repo = types.ModuleType('gi.repository')
    glib_repo.GLib = _FakeGLib
    monkeypatch.setitem(sys.modules, 'gi.repository', glib_repo)
    dispatched: list[str] = []
    keyboards: list[bool] = []
    ns = types.SimpleNamespace(
        _swipe_drag_samples=[],
        _swipe_drag_claimed=False,
        stack=_FakeStack(page),
        gesture_config={'swipe_down_top': 'notification_shade'},
        _dispatch_gesture_action=dispatched.append,
        _show_keyboard=lambda: keyboards.append(True),
    )
    bound = types.SimpleNamespace(
        begin=lambda g, x, y: window_mod.ShellWindow._on_swipe_drag_begin(
            ns, g, x, y),
        update=lambda g, x, y: window_mod.ShellWindow._on_swipe_drag_update(
            ns, g, x, y),
        end=lambda g, x, y: window_mod.ShellWindow._on_swipe_drag_end(
            ns, g, x, y),
    )
    return ns, bound, dispatched, keyboards


class TestDragHandlers:
    def test_tap_never_claims(self, window_mod, monkeypatch):
        ns, bound, _dispatched, keyboards = _handler_ns(window_mod,
                                                         monkeypatch)
        gesture = _FakeGesture()
        _FakeGLib.now_us = 0
        bound.begin(gesture, 100.0, 500.0)
        _FakeGLib.now_us = 30_000
        bound.update(gesture, 2.0, -5.0)
        assert ns._swipe_drag_claimed is False
        assert gesture.states == []
        bound.end(gesture, 2.0, -5.0)
        assert ns.stack.shown == []
        assert keyboards == []

    def test_vertical_swipe_claims_on_home(self, window_mod, monkeypatch):
        ns, bound, _dispatched, _keyboards = _handler_ns(window_mod,
                                                         monkeypatch)
        gesture = _FakeGesture()
        bound.begin(gesture, 100.0, 500.0)
        bound.update(gesture, 1.0, -30.0)
        assert ns._swipe_drag_claimed is True
        assert len(gesture.states) == 1

    def test_vertical_drag_never_claims_off_home(self, window_mod,
                                                monkeypatch):
        """Drawer/settings vertical drags scroll their lists: the drag
        must not steal them."""
        for page in ('apps', 'settings'):
            ns, bound, _dispatched, _keyboards = _handler_ns(
                window_mod, monkeypatch, page=page)
            gesture = _FakeGesture()
            bound.begin(gesture, 100.0, 500.0)
            bound.update(gesture, 0.0, -200.0)
            assert ns._swipe_drag_claimed is False
            assert gesture.states == []
            bound.end(gesture, 0.0, -200.0)
            assert ns.stack.shown == []

    def test_horizontal_swipe_claims_as_side_swipe(self, window_mod,
                                                   monkeypatch):
        # Side swipes work from anywhere on home, slots included (the
        # full-width slot buttons used to swallow them): the capture drag
        # claims a sideways drag and runs the side swipe on release.
        ns, bound, dispatched, _keyboards = _handler_ns(window_mod,
                                                        monkeypatch)
        gesture = _FakeGesture()
        _FakeGLib.now_us = 0
        bound.begin(gesture, 100.0, 500.0)
        _FakeGLib.now_us = 100_000
        bound.update(gesture, 120.0, -10.0)
        assert ns._swipe_drag_claimed is True
        bound.end(gesture, 120.0, -10.0)
        assert dispatched == ['dialer']  # no app bound: Android's dialer

    def test_short_slow_side_drag_does_nothing(self, window_mod,
                                               monkeypatch):
        ns, bound, dispatched, _keyboards = _handler_ns(window_mod,
                                                        monkeypatch)
        gesture = _FakeGesture()
        _FakeGLib.now_us = 0
        bound.begin(gesture, 200.0, 500.0)
        _FakeGLib.now_us = 900_000
        bound.update(gesture, -40.0, 0.0)
        bound.end(gesture, -40.0, 0.0)
        assert dispatched == []

    def test_side_swipe_thresholds(self, window_mod):
        assert window_mod.decide_side_swipe(-100.0, 0.0) == 'left'
        assert window_mod.decide_side_swipe(140.0, 0.0) == 'right'
        assert window_mod.decide_side_swipe(-60.0, -900.0) == 'left'
        assert window_mod.decide_side_swipe(-60.0, -100.0) is None
        assert window_mod.should_claim_horizontal_drag(30.0, 5.0)
        assert not window_mod.should_claim_horizontal_drag(30.0, 25.0)

    def test_fast_swipe_up_opens_drawer(self, window_mod, monkeypatch):
        ns, bound, _dispatched, keyboards = _handler_ns(window_mod,
                                                        monkeypatch)
        gesture = _FakeGesture()
        _FakeGLib.now_us = 0
        bound.begin(gesture, 100.0, 500.0)
        _FakeGLib.now_us = 50_000
        bound.update(gesture, 0.0, -150.0)
        assert ns._swipe_drag_claimed is True
        bound.end(gesture, 0.0, -150.0)
        assert ns.stack.shown == ['apps']
        assert keyboards == []

    def test_short_swipe_up_raises_keyboard(self, window_mod, monkeypatch):
        ns, bound, _dispatched, keyboards = _handler_ns(window_mod,
                                                        monkeypatch)
        gesture = _FakeGesture()
        _FakeGLib.now_us = 0
        bound.begin(gesture, 100.0, 500.0)
        _FakeGLib.now_us = 100_000
        bound.update(gesture, 0.0, -40.0)
        assert ns._swipe_drag_claimed is True
        bound.end(gesture, 0.0, -40.0)
        assert ns.stack.shown == []
        assert keyboards == [True]

    def test_slow_upwards_scroll_does_nothing(self, window_mod, monkeypatch):
        ns, bound, _dispatched, keyboards = _handler_ns(window_mod,
                                                        monkeypatch)
        gesture = _FakeGesture()
        _FakeGLib.now_us = 0
        bound.begin(gesture, 100.0, 500.0)
        _FakeGLib.now_us = 800_000
        bound.update(gesture, 0.0, -60.0)
        assert ns._swipe_drag_claimed is True
        bound.end(gesture, 0.0, -60.0)
        assert ns.stack.shown == []
        assert keyboards == []

    def test_fast_swipe_down_runs_configured_action(self, window_mod,
                                                   monkeypatch):
        ns, bound, dispatched, _keyboards = _handler_ns(window_mod,
                                                       monkeypatch)
        gesture = _FakeGesture()
        _FakeGLib.now_us = 0
        bound.begin(gesture, 100.0, 200.0)
        _FakeGLib.now_us = 80_000
        bound.update(gesture, 0.0, 60.0)
        assert ns._swipe_drag_claimed is True
        bound.end(gesture, 0.0, 60.0)
        assert dispatched == ['notification_shade']

    def test_slow_downwards_scroll_does_nothing(self, window_mod,
                                               monkeypatch):
        ns, bound, dispatched, _keyboards = _handler_ns(window_mod,
                                                       monkeypatch)
        gesture = _FakeGesture()
        _FakeGLib.now_us = 0
        bound.begin(gesture, 100.0, 200.0)
        _FakeGLib.now_us = 900_000
        bound.update(gesture, 0.0, 60.0)
        bound.end(gesture, 0.0, 60.0)
        assert dispatched == []


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
