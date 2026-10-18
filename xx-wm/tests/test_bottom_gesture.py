"""Bottom-edge gesture strip (bottom_gesture.py): Android gesture nav
handled by the shell, because lisgd's fixed-third length classes and lack
of a pause made the switcher unreachable on the FLX1.

Headless: the classifier is pure and driven with synthetic timelines
(t_ms, dx, dy; dy negative is up). GTK methods and the window/main wiring
run as unbound methods on hand-built namespaces (the test_status_bar
idiom). Modules import inside fixtures, after conftest restored gi.
"""
import importlib
import json
import sys
import types
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / 'launcher' / 'src'
sys.path.insert(0, str(SRC))


@pytest.fixture
def bg():
    return importlib.import_module('bottom_gesture')


@pytest.fixture(autouse=True)
def _isolate_gestures(tmp_path, monkeypatch):
    import gesture_config
    monkeypatch.setattr(gesture_config, '_config_path',
                        lambda: tmp_path / 'gestures.json')


def _drag(points, height=1000.0):
    """Linear drag through (t_ms, dx, dy) points, one sample per 10 ms
    (a touchscreen's report rate), the way GTK delivers drag-update."""
    samples = [(float(points[0][0]), float(points[0][1]), float(points[0][2]))]
    for (t0, x0, y0), (t1, x1, y1) in zip(points, points[1:]):
        steps = max(1, int((t1 - t0) // 10))
        for i in range(1, steps + 1):
            f = i / steps
            samples.append((t0 + (t1 - t0) * f, x0 + (x1 - x0) * f,
                            y0 + (y1 - y0) * f))
    return samples


def _swipe(bg, points, height=1000.0, poll_every=50):
    """Feed a BottomSwipe like the strip does (updates plus the pause
    poll); returns (live outcome, release outcome)."""
    samples = _drag(points, height)
    swipe = bg.BottomSwipe(height, samples[0][0])
    live = None
    next_poll = samples[0][0] + poll_every
    for t, dx, dy in samples[1:-1]:
        while next_poll <= t:
            live = live or swipe.poll(next_poll)
            next_poll += poll_every
        swipe.update(t, dx, dy)
    t, dx, dy = samples[-1]
    while next_poll <= t:
        live = live or swipe.poll(next_poll)
        next_poll += poll_every
    return live, swipe.release(t, dx, dy)


# ---- thresholds -------------------------------------------------------

class TestThresholds:
    def test_named_constants_match_android_feel(self, bg):
        assert 16 <= bg.STRIP_HEIGHT <= 20
        assert 0.12 <= bg.PAUSE_MIN_FRAC <= 0.15
        assert bg.SLOW_RELEASE_FRAC == pytest.approx(0.25)
        assert 200 <= bg.PAUSE_MS <= 300
        # Well under lisgd's 1/3-screen "medium" class (the FLX1 bug).
        assert bg.PAUSE_MIN_FRAC < 1 / 3 and bg.SLOW_RELEASE_FRAC < 1 / 3

    def test_scale_with_output_height(self, bg):
        small, big = bg.thresholds(800), bg.thresholds(1600)
        for a, b in zip(small, big):
            assert b == pytest.approx(2 * a)

    def test_flx1_values(self, bg):
        th = bg.thresholds(1000)  # FLX1 portrait, logical px
        assert th.pause_min == pytest.approx(120)
        assert th.slow_release == pytest.approx(250)
        assert th.flick_speed == pytest.approx(0.9)  # px per ms

    def test_garbage_height_never_divides_by_zero(self, bg):
        assert bg.thresholds(0).home_min > 0


# ---- classifier -------------------------------------------------------

class TestClassifier:
    def test_quick_flick_goes_home(self, bg):
        # 150 px in 100 ms: 1.5 px/ms, a flick.
        live, out = _swipe(bg, [(0, 0, 0), (100, 0, -150)])
        assert (live, out) == (None, bg.HOME)

    def test_long_fast_flick_still_goes_home(self, bg):
        live, out = _swipe(bg, [(0, 0, 0), (200, 0, -600)])
        assert (live, out) == (None, bg.HOME)

    def test_swipe_and_pause_opens_the_switcher_live(self, bg):
        # Up 160 px (16%), then hold still: recents while the finger is down.
        live, out = _swipe(bg, [(0, 0, 0), (250, 0, -160), (700, 0, -160)])
        assert live == bg.SWITCHER
        assert out is None  # the release does not fire a second action

    def test_pause_waits_for_pause_ms(self, bg):
        samples = _drag([(0, 0, 0), (250, 0, -160)])
        swipe = bg.BottomSwipe(1000, 0)
        for t, dx, dy in samples[1:]:
            swipe.update(t, dx, dy)
        assert swipe.poll(250 + bg.PAUSE_MS - 60) is None
        assert swipe.poll(250 + bg.PAUSE_MS + 10) == bg.SWITCHER
        assert swipe.poll(250 + bg.PAUSE_MS + 60) is None  # once only

    def test_small_jitter_still_counts_as_a_pause(self, bg):
        live, _ = _swipe(bg, [(0, 0, 0), (250, 0, -160), (400, 4, -165),
                              (600, -2, -158), (800, 0, -160)])
        assert live == bg.SWITCHER

    def test_moving_finger_is_not_a_pause(self, bg):
        # Slow but steady: 0.2 px/ms never stays within the slop for 250 ms.
        live, out = _swipe(bg, [(0, 0, 0), (1000, 0, -200)])
        assert live is None
        assert out == bg.HOME  # 20% slow: below the slow-release mark

    def test_pause_too_low_is_not_the_switcher(self, bg):
        # Hold at 8%: below PAUSE_MIN_FRAC.
        live, out = _swipe(bg, [(0, 0, 0), (200, 0, -80), (800, 0, -80)])
        assert live is None
        assert out == bg.HOME

    def test_slow_release_past_a_quarter_opens_the_switcher(self, bg):
        live, out = _swipe(bg, [(0, 0, 0), (1500, 0, -300)])
        assert (live, out) == (None, bg.SWITCHER)

    def test_slow_release_below_a_quarter_goes_home(self, bg):
        live, out = _swipe(bg, [(0, 0, 0), (1500, 0, -220)])
        assert (live, out) == (None, bg.HOME)

    def test_too_short_does_nothing(self, bg):
        assert _swipe(bg, [(0, 0, 0), (120, 0, -35)]) == (None, None)

    def test_downward_does_nothing(self, bg):
        assert _swipe(bg, [(0, 0, 0), (120, 0, 40)]) == (None, None)

    def test_tap(self, bg):
        assert _swipe(bg, [(0, 0, 0), (80, 3, -2)]) == (None, bg.TAP)

    def test_long_press_without_moving_is_a_tap(self, bg):
        assert _swipe(bg, [(0, 0, 0), (900, 1, 0)]) == (None, bg.TAP)

    def test_sideways_right_is_previous_left_is_next(self, bg):
        assert _swipe(bg, [(0, 0, 0), (250, 200, -10)])[1] == bg.PREVIOUS
        assert _swipe(bg, [(0, 0, 0), (250, -200, -10)])[1] == bg.NEXT

    def test_short_sideways_does_nothing(self, bg):
        assert _swipe(bg, [(0, 0, 0), (250, 60, 0)]) == (None, None)

    def test_diagonal_up_is_not_sideways(self, bg):
        out = _swipe(bg, [(0, 0, 0), (100, 120, -200)])[1]
        assert out == bg.HOME

    def test_sideways_hold_is_not_a_pause(self, bg):
        live, _ = _swipe(bg, [(0, 0, 0), (200, 300, -130), (800, 300, -130)])
        assert live is None

    @pytest.mark.parametrize('height', [800, 1000, 1333, 1600])
    def test_outcomes_hold_at_every_height(self, bg, height):
        """The same physical gesture (as fractions of the screen) gives
        the same outcome at any scale."""
        h = float(height)
        flick = [(0, 0, 0), (100, 0, -0.15 * h)]
        pause = [(0, 0, 0), (250, 0, -0.16 * h), (700, 0, -0.16 * h)]
        slow = [(0, 0, 0), (1500, 0, -0.30 * h)]
        short = [(0, 0, 0), (150, 0, -0.03 * h)]
        assert _swipe(bg, flick, h) == (None, bg.HOME)
        assert _swipe(bg, pause, h) == (bg.SWITCHER, None)
        assert _swipe(bg, slow, h) == (None, bg.SWITCHER)
        assert _swipe(bg, short, h) == (None, None)

    def test_release_speed_uses_the_last_window(self, bg):
        # Fast start, then a crawl at the end: the release is slow.
        samples = _drag([(0, 0, 0), (100, 0, -260), (600, 0, -280)])
        assert bg.release_speed(samples) < 0.1
        assert bg.classify_release(samples, 1000) == bg.SWITCHER

    def test_empty_inputs(self, bg):
        assert bg.classify_release([], 1000) is None
        assert bg.is_pause([], 100, 1000) is False
        assert bg.release_speed([]) == 0.0


# ---- verbs: the gestures.json mapping lisgd used ------------------------

class TestVerbs:
    def test_defaults(self, bg):
        from gesture_config import GestureConfig
        gc = GestureConfig()
        assert bg.verb_for(bg.HOME, gc) == 'gesture.home'
        assert bg.verb_for(bg.SWITCHER, gc) == 'gesture.switcher'
        assert bg.verb_for(bg.TAP, gc) is None
        assert bg.verb_for(bg.PREVIOUS, gc) is None

    def test_owner_mapping_is_honoured(self, bg, tmp_path):
        from gesture_config import GestureConfig
        (tmp_path / 'gestures.json').write_text(json.dumps({
            'swipe_up_short': 'app_switcher',
            'swipe_up_long': 'gesture.home',
        }), encoding='utf-8')
        gc = GestureConfig()
        assert bg.verb_for(bg.HOME, gc) == 'gesture.switcher'
        assert bg.verb_for(bg.SWITCHER, gc) == 'gesture.home'

    def test_same_resolution_as_lisgd(self, bg, tmp_path):
        from gesture_bindings import resolve_verb
        from gesture_config import GestureConfig
        (tmp_path / 'gestures.json').write_text(json.dumps({
            'swipe_up_short': 'camera',  # valid but unmapped
            'swipe_up_long': 'search',
        }), encoding='utf-8')
        gc = GestureConfig()
        assert bg.verb_for(bg.HOME, gc) == resolve_verb(gc, 'swipe_up_short')
        assert bg.verb_for(bg.SWITCHER, gc) == resolve_verb(gc, 'swipe_up_long')
        assert bg.verb_for(bg.SWITCHER, gc) == 'gesture.keyboard'


# ---- lisgd: no bottom-edge bindings while the strip is up ----------------

class TestLisgdBindings:
    def test_strip_drops_every_bottom_binding(self):
        from gesture_bindings import generate_bindings
        full = generate_bindings('/usr/bin')
        stripped = generate_bindings('/usr/bin', bottom_strip=True)
        assert any(',B,' in line for line in full)
        assert not any(line.split(',')[2] == 'B' for line in stripped)
        assert stripped == [line for line in full
                            if line.split(',')[2] != 'B']
        assert stripped == [
            '1,UD,T,*,R,/usr/bin/xx-wm-ipc gesture.shade',
            '1,LR,L,*,R,/usr/bin/xx-wm-ipc gesture.back',
            '1,RL,R,*,R,/usr/bin/xx-wm-ipc gesture.back',
        ]

    def test_default_keeps_the_bottom_for_the_wrapper(self):
        from gesture_bindings import generate_bindings
        assert generate_bindings('/usr/bin')[0] == \
            '1,DU,B,S,R,/usr/bin/xx-wm-ipc gesture.home'

    def test_rebinding_survives_the_strip(self, tmp_path):
        from gesture_bindings import generate_bindings
        (tmp_path / 'gestures.json').write_text(json.dumps({
            'swipe_left_edge': 'gesture.home'}), encoding='utf-8')
        got = generate_bindings('/usr/bin', bottom_strip=True)
        assert got[-1].endswith('gesture.home')

    def test_is_bottom_edge(self):
        from gesture_bindings import is_bottom_edge
        assert is_bottom_edge('1,DU,B,M,R')
        assert not is_bottom_edge('1,UD,T,*,R')
        assert not is_bottom_edge('garbage')

    def test_restart_passes_the_flag(self):
        from gesture_bindings import restart_lisgd
        spawned = []
        assert restart_lisgd(
            '/usr/bin', touch_dev='/dev/input/event3',
            spawn=lambda argv, **_k: spawned.append(argv),
            kill_fn=lambda: None, which=lambda _n: '/usr/bin/lisgd',
            bottom_strip=True)
        assert not any(',B,' in arg for arg in spawned[0])

    @pytest.mark.parametrize('has_strip', [True, False])
    def test_shell_restart_follows_the_strip(self, monkeypatch, has_strip):
        import gesture_bindings
        import window
        from gesture_config import GestureConfig
        seen = {}
        monkeypatch.setattr(
            gesture_bindings, 'restart_lisgd',
            lambda bindir, gc, **kw: seen.update(kw) or True)
        shell = types.SimpleNamespace(
            gesture_config=GestureConfig(),
            _bottom_gesture=object() if has_strip else None)
        window.ShellWindow._sync_system_gestures(shell)
        assert seen == {'bottom_strip': has_strip}


# ---- layer ---------------------------------------------------------------

class _FakeLayerShell:
    Layer = types.SimpleNamespace(BACKGROUND=0, BOTTOM=1, TOP=2, OVERLAY=3)
    Edge = types.SimpleNamespace(LEFT=0, RIGHT=1, TOP=2, BOTTOM=3)
    KeyboardMode = types.SimpleNamespace(NONE=0, EXCLUSIVE=1)

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def _record(*args):
            self.calls.append((name,) + args[1:])
        return _record


class TestLayer:
    def test_overlay_strip_on_the_bottom_edge(self, bg):
        ls = _FakeLayerShell()
        sizes = []
        win = types.SimpleNamespace(
            set_default_size=lambda w, h: sizes.append(('default', w, h)),
            set_size_request=lambda w, h: sizes.append(('request', w, h)))
        bg.configure_layer(win, bg.STRIP_HEIGHT, ls)
        # OVERLAY: phoc routes input to it above a fullscreen view.
        assert ('set_layer', ls.Layer.OVERLAY) in ls.calls
        anchors = {c[1]: c[2] for c in ls.calls if c[0] == 'set_anchor'}
        assert anchors == {ls.Edge.BOTTOM: True, ls.Edge.LEFT: True,
                           ls.Edge.RIGHT: True, ls.Edge.TOP: False}
        # Never resizes apps; stays on the edge under the keyboard.
        assert ('set_exclusive_zone', -1) in ls.calls
        assert ('set_keyboard_mode', ls.KeyboardMode.NONE) in ls.calls
        assert ('request', -1, bg.STRIP_HEIGHT) in sizes

    def test_strip_is_invisible(self, bg):
        css = bg.CSS.decode()
        assert 'rgba(0, 0, 0, 0.004)' in css
        assert 'border' not in css and 'color:' not in css


# ---- visibility --------------------------------------------------------

class TestVisibility:
    @pytest.mark.parametrize('locked,wizard,want', [
        (False, False, True), (True, False, False),
        (False, True, False), (True, True, False),
    ])
    def test_table(self, bg, locked, wizard, want):
        assert bg.decide_visible(locked, wizard) is want

    def test_same_rule_as_the_status_bar(self, bg):
        import status_bar
        for locked in (False, True):
            for wizard in (False, True):
                assert bg.decide_visible(locked, wizard) == \
                    status_bar.decide_visible(locked, wizard)

    def _strip(self, visible):
        rec = []
        strip = types.SimpleNamespace(
            get_visible=lambda: visible,
            present=lambda: rec.append('present'),
            set_visible=lambda v: rec.append(('set_visible', v)),
            _cancel=lambda: rec.append('cancel'))
        return strip, rec

    def test_sync_hides_and_shows(self, bg):
        cls = bg.BottomGestureStrip
        strip, rec = self._strip(True)
        cls.sync_visibility(strip, True, False)
        assert rec == ['cancel', ('set_visible', False)]
        strip, rec = self._strip(False)
        cls.sync_visibility(strip, False, False)
        assert rec == ['present']
        strip, rec = self._strip(True)
        cls.sync_visibility(strip, False, False)
        assert rec == []

    def test_raise_remaps_only_a_visible_strip(self, bg):
        cls = bg.BottomGestureStrip
        strip, rec = self._strip(True)
        cls.raise_strip(strip)
        assert rec == ['cancel', ('set_visible', False), 'present']
        strip, rec = self._strip(False)
        cls.raise_strip(strip)
        assert rec == []

    def test_window_sync_drives_strip_and_bar(self):
        import window
        seen = []
        strip = types.SimpleNamespace(
            sync_visibility=lambda locked, wiz: seen.append(('strip', locked, wiz)))
        app = types.SimpleNamespace(_tour_visible=True)
        shell = types.SimpleNamespace(
            _bottom_gesture=strip, _status_bar=None,
            is_locked=lambda: False, get_application=lambda: app)
        window.ShellWindow._sync_status_bar(shell)
        app._tour_visible = False
        shell.is_locked = lambda: True
        window.ShellWindow._sync_status_bar(shell)
        assert seen == [('strip', False, True), ('strip', True, False)]

    def test_built_after_first_boot_only(self):
        text = (SRC / 'main.py').read_text(encoding='utf-8')
        show_shell = text[text.index('    def _show_shell(self)'):
                          text.index('    def _start_status_bar(self)')]
        assert 'self._start_bottom_gesture()' in show_shell
        activate = text[text.index('    def do_activate(self)'):
                        text.index('    def _show_shell(self)')]
        assert '_start_bottom_gesture' not in activate

    def test_recents_raises_the_strip(self):
        text = (SRC / 'window.py').read_text(encoding='utf-8')
        body = text[text.index('    def _show_switcher(self)'):
                    text.index('    def _refresh_switcher_if_visible')]
        assert body.index('show_switcher()') < body.index('raise_strip()')


# ---- main wiring ---------------------------------------------------------

class TestMainWiring:
    def _app(self, switcher_visible=False, locked=False):
        import main
        from gesture_config import GestureConfig
        fired, switched = [], []
        shell = types.SimpleNamespace(
            gesture_config=GestureConfig(),
            is_locked=lambda: locked,
            switch_adjacent_app=lambda a: switched.append(a) or True,
            _switcher=types.SimpleNamespace(
                get_visible=lambda: switcher_visible))
        app = types.SimpleNamespace(
            _shell=shell, _on_ipc_command=fired.append)
        return main.XXWMApplication._on_bottom_gesture, app, fired, switched

    def test_home_and_switcher_go_through_ipc(self, bg):
        handler, app, fired, _ = self._app()
        handler(app, bg.HOME)
        handler(app, bg.SWITCHER)
        assert fired == ['gesture.home', 'gesture.switcher']

    def test_swipe_up_inside_recents_goes_home(self, bg):
        handler, app, fired, _ = self._app(switcher_visible=True)
        handler(app, bg.SWITCHER)
        assert fired == ['gesture.home']

    def test_sideways_switches_apps(self, bg):
        handler, app, fired, switched = self._app()
        handler(app, bg.PREVIOUS)
        handler(app, bg.NEXT)
        assert switched == [bg.PREVIOUS, bg.NEXT] and fired == []

    def test_sideways_does_nothing_while_locked(self, bg):
        handler, app, _, switched = self._app(locked=True)
        handler(app, bg.PREVIOUS)
        assert switched == []

    def test_start_wires_the_strip(self, bg, monkeypatch):
        import main
        built = {}

        class _Strip:
            def set_application(self, app):
                built['app'] = app

        monkeypatch.setattr(
            bg, 'create_bottom_gesture',
            lambda on_action=None: built.setdefault('cb', on_action) and _Strip())
        shell = types.SimpleNamespace(
            _sync_status_bar=lambda: built.setdefault('synced', True))
        app = types.SimpleNamespace(_shell=shell, _on_bottom_gesture=object())
        main.XXWMApplication._start_bottom_gesture(app)
        assert built['cb'] is app._on_bottom_gesture
        assert isinstance(shell._bottom_gesture, _Strip)
        assert built['synced'] and built['app'] is app

    def test_no_layer_shell_means_no_strip(self, bg, monkeypatch):
        import main
        monkeypatch.setattr(bg, 'create_bottom_gesture', lambda **_kw: None)
        shell = types.SimpleNamespace(_bottom_gesture=None)
        main.XXWMApplication._start_bottom_gesture(
            types.SimpleNamespace(_shell=shell))
        assert shell._bottom_gesture is None


# ---- previous / next app -------------------------------------------------

class TestAdjacentApp:
    def _tops(self, active):
        from toplevel_manager import Toplevel
        return [Toplevel(app_id=n, title=n, handle=n, activated=n == active)
                for n in ('a', 'b', 'c')]

    def test_neighbours_in_recents_order(self, bg):
        tops = self._tops('b')
        assert bg.adjacent_toplevel(tops, bg.PREVIOUS).handle == 'a'
        assert bg.adjacent_toplevel(tops, bg.NEXT).handle == 'c'

    def test_no_wrap_and_no_active(self, bg):
        assert bg.adjacent_toplevel(self._tops('a'), bg.PREVIOUS) is None
        assert bg.adjacent_toplevel(self._tops('c'), bg.NEXT) is None
        assert bg.adjacent_toplevel(self._tops(None), bg.NEXT) is None
        assert bg.adjacent_toplevel([], bg.NEXT) is None
        assert bg.adjacent_toplevel(self._tops('b'), bg.HOME) is None

    def test_window_activates_and_yields(self):
        import window
        calls = []
        tops = self._tops('b')
        manager = types.SimpleNamespace(
            list=lambda: tops, activate=lambda h: calls.append(('activate', h)))
        shell = types.SimpleNamespace(
            _toplevel_manager=manager, _home_showing=lambda: False,
            drop_to_background=lambda: calls.append('drop'))
        assert window.ShellWindow.switch_adjacent_app(shell, 'next') is True
        assert calls == [('activate', 'c'), 'drop']

    def test_window_does_nothing_on_home(self):
        import window
        shell = types.SimpleNamespace(
            _toplevel_manager=None, _home_showing=lambda: True)
        assert window.ShellWindow.switch_adjacent_app(shell, 'next') is False


# ---- tap pass-through ------------------------------------------------------

class TestTapPassThrough:
    def test_click_is_motion_press_hold_release(self):
        import capture
        rec = []
        pointer = types.SimpleNamespace(
            motion_absolute=lambda *a: rec.append(('motion',) + a[1:]),
            frame=lambda: rec.append('frame'),
            button=lambda t, b, s: rec.append(('button', b, s)))
        capture.emit_click(pointer, 80.4, 955.6, 480, 960, 1000,
                           flush=lambda: rec.append('flush'),
                           sleep=lambda s: rec.append(('sleep', s)))
        assert rec == [
            ('motion', 80, 955, 480, 960), 'frame',
            ('button', capture.BTN_LEFT, capture.BUTTON_PRESSED), 'frame',
            'flush', ('sleep', capture.CLICK_HOLD_SECS),
            ('button', capture.BTN_LEFT, capture.BUTTON_RELEASED), 'frame',
            'flush',
        ]
        # GTK drops a click whose press and release land together.
        assert capture.CLICK_HOLD_SECS >= 0.03

    def test_tap_clears_input_then_replays_at_the_output_point(
            self, bg, monkeypatch):
        timeouts = []
        monkeypatch.setattr(bg.GLib, 'timeout_add',
                            lambda ms, fn, *a: timeouts.append((ms, fn, a)))
        modes = []
        strip = types.SimpleNamespace(
            _size=(480, 960), _origin=(80.0, 13.0),
            get_height=lambda: bg.STRIP_HEIGHT,
            _set_passthrough=lambda on: modes.append(on) or True,
            _start_replay=object())
        bg.BottomGestureStrip._pass_tap(strip)
        assert modes == [True]
        ms, fn, args = timeouts[0]
        assert fn is strip._start_replay
        assert args == (80.0, 960 - bg.STRIP_HEIGHT + 13.0, 480, 960)

    def test_no_surface_no_replay(self, bg, monkeypatch):
        timeouts = []
        monkeypatch.setattr(bg.GLib, 'timeout_add',
                            lambda *a: timeouts.append(a))
        strip = types.SimpleNamespace(
            _size=(480, 960), _origin=(1.0, 1.0), get_height=lambda: 18,
            _set_passthrough=lambda on: False)
        bg.BottomGestureStrip._pass_tap(strip)
        assert timeouts == []

    def test_release_dispatch(self, bg):
        fired, passed = [], []
        swipe = bg.BottomSwipe(1000, 0)
        swipe.update(50, 0, -150)
        strip = types.SimpleNamespace(
            _swipe=swipe, _cancel=lambda: None,
            _pass_tap=lambda: passed.append(True),
            _fire=fired.append)
        bg.BottomGestureStrip._on_drag_end(strip, None, 0, -150)
        assert fired == [bg.HOME] and passed == []
