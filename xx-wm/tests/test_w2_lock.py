"""W2 lock screen + idle behavior (blank-driven auto-lock).

Headless: real widgets are never constructed. window/main/lock_screen/
display_manager are imported once against a minimal fake gi stack (the
test_restore_dialog / test_theme_hot_reload idiom) and driven as unbound
methods on hand-built namespaces; the GLib seam below records timers and
runs idle callbacks synchronously so scheduling is observable.
"""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


def _install_fake_gi():
    glib = types.ModuleType('gi.repository.GLib')
    glib.SOURCE_REMOVE = False
    glib.SOURCE_CONTINUE = True
    glib.Error = type('Error', (Exception,), {})
    glib._rec = {
        'idle': [], 'timeouts': [], 'millis': [], 'removed': [],
        'sync_idle': True, 'next_id': 0, 'mono': 1_000_000_000,
    }

    def _idle_add(cb, *args):
        glib._rec['idle'].append((cb, args))
        if glib._rec['sync_idle']:
            cb(*args)
        return 0

    def _timeout_add_seconds(secs, cb, *_args):
        glib._rec['timeouts'].append((secs, cb))
        glib._rec['next_id'] += 1
        return glib._rec['next_id']

    def _timeout_add(ms, cb, *_args):
        glib._rec['millis'].append((ms, cb))
        glib._rec['next_id'] += 1
        return glib._rec['next_id']

    glib.idle_add = _idle_add
    glib.timeout_add_seconds = _timeout_add_seconds
    glib.timeout_add = _timeout_add
    glib.source_remove = lambda tid: glib._rec['removed'].append(tid)
    glib.get_monotonic_time = lambda: glib._rec['mono']
    glib.Variant = lambda sig, value: types.SimpleNamespace(
        signature=sig, value=value)

    gdk = types.ModuleType('gi.repository.Gdk')
    gdk.Display = types.SimpleNamespace(get_default=lambda: None)

    gio = types.ModuleType('gi.repository.Gio')
    gio.BusType = types.SimpleNamespace(SYSTEM=1, SESSION=2)
    gio.DBusCallFlags = types.SimpleNamespace(NONE=0)

    gtk = types.ModuleType('gi.repository.Gtk')
    gtk.Window = type('Window', (), {})
    gtk.Box = type('Box', (), {})
    gtk.Editable = type('Editable', (), {})
    gtk.PickFlags = types.SimpleNamespace(DEFAULT=0)

    adw = types.ModuleType('gi.repository.Adw')
    adw.Application = type('Application', (), {})
    adw.ApplicationWindow = type('ApplicationWindow', (), {})
    adw.StyleManager = types.SimpleNamespace(
        get_default=lambda: types.SimpleNamespace(
            set_color_scheme=lambda scheme: None))
    adw.ColorScheme = types.SimpleNamespace(FORCE_DARK=1, FORCE_LIGHT=2)

    pango = types.ModuleType('gi.repository.Pango')
    pango.EllipsizeMode = types.SimpleNamespace(END=3)

    gi_mod = types.ModuleType('gi')
    gi_rep = types.ModuleType('gi.repository')

    def _require_version(namespace, version):
        if namespace == 'Gtk4LayerShell':
            raise ValueError(f'{namespace} not available')

    gi_mod.require_version = _require_version
    gi_mod.repository = gi_rep
    sys.modules['gi'] = gi_mod
    sys.modules['gi.repository'] = gi_rep
    for name, mod in (('GLib', glib), ('Gdk', gdk), ('Gio', gio),
                      ('Gtk', gtk), ('Adw', adw), ('Pango', pango)):
        sys.modules[f'gi.repository.{name}'] = mod
    return glib


_glib = _install_fake_gi()

_GI_BOUND_MODS = ('window', 'main', 'display_manager', 'lock_screen',
                  'modem_monitor', 'first_boot', 'notif_daemon', 'ipc')
_saved_mods = {name: sys.modules.get(name) for name in _GI_BOUND_MODS}
for _mod in _GI_BOUND_MODS:
    sys.modules.pop(_mod, None)

import display_manager  # noqa: E402
import lock_screen  # noqa: E402
import modem_monitor  # noqa: E402
import window  # noqa: E402
import main  # noqa: E402

from config import DEFAULT_CONFIG, ShellConfig  # noqa: E402

# Leave sys.modules exactly as found: sibling files monkeypatch their own
# collection-bound module objects while src methods re-import by name at
# call time, so a stale entry here would silently rebind their seams.
for _mod in _GI_BOUND_MODS:
    _saved = _saved_mods[_mod]
    if _saved is None:
        sys.modules.pop(_mod, None)
    else:
        sys.modules[_mod] = _saved
del _saved_mods, _saved


@pytest.fixture(autouse=True)
def _reset_glib_rec():
    _glib._rec.update({
        'idle': [], 'timeouts': [], 'millis': [], 'removed': [],
        'sync_idle': True, 'next_id': 0, 'mono': 1_000_000_000,
    })
    lock_screen.LockScreen._fp_usable = None
    lock_screen.LockScreen._fp_probing = False
    yield
    lock_screen.LockScreen._fp_usable = None
    lock_screen.LockScreen._fp_probing = False


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))


def _config_with(**overrides):
    cfg = ShellConfig.__new__(ShellConfig)
    cfg.data = dict(DEFAULT_CONFIG)
    cfg.data.update(overrides)
    return cfg


def _shell_ns(**overrides):
    """Hand-built ShellWindow namespace: unbound methods run against this."""
    shell = object.__new__(window.ShellWindow)
    shell.config = _config_with(auto_lock_timeout=0)
    shell._modem_monitor = types.SimpleNamespace(calls=lambda: [])
    shell._display_mgr = None
    shell._lock_screen = None
    shell._call_ui = None
    shell._lock_src = None
    shell._blanked = False
    for key, value in overrides.items():
        setattr(shell, key, value)
    return shell


def _live_call(state=None):
    if state is None:
        state = modem_monitor._STATE_ACTIVE
    return modem_monitor.CallInfo(path='/call/0', number='+15550001111',
                                  direction=1, state=state)


class TestBlankDrivenLock:
    def test_zero_locks_immediately_on_blank(self):
        shell = _shell_ns()
        locked = []
        shell._show_lock_screen = lambda: locked.append(True)
        window.ShellWindow.on_blank_changed(shell, True)
        assert locked == [True]
        assert shell._lock_src is None
        assert _glib._rec['timeouts'] == []

    def test_n_seconds_schedules_and_fires(self):
        shell = _shell_ns()
        shell.config = _config_with(auto_lock_timeout=120)
        locked = []
        shell._show_lock_screen = lambda: locked.append(True)
        window.ShellWindow.on_blank_changed(shell, True)
        assert locked == []
        assert _glib._rec['timeouts'] == [(120, shell._on_blank_lock_timeout)]
        assert shell._lock_src is not None
        window.ShellWindow._on_blank_lock_timeout(shell)
        assert locked == [True]
        assert shell._lock_src is None

    def test_wake_cancels_pending_lock(self):
        shell = _shell_ns()
        shell.config = _config_with(auto_lock_timeout=120)
        locked = []
        shell._show_lock_screen = lambda: locked.append(True)
        window.ShellWindow.on_blank_changed(shell, True)
        timer_id = shell._lock_src
        assert timer_id is not None
        window.ShellWindow.on_blank_changed(shell, False)
        assert _glib._rec['removed'] == [timer_id]
        assert shell._lock_src is None
        assert locked == []

    def test_fired_timer_after_wake_does_not_lock(self):
        shell = _shell_ns()
        shell.config = _config_with(auto_lock_timeout=120)
        locked = []
        shell._show_lock_screen = lambda: locked.append(True)
        window.ShellWindow.on_blank_changed(shell, True)
        cb = _glib._rec['timeouts'][0][1]
        window.ShellWindow.on_blank_changed(shell, False)
        cb()
        assert locked == []

    def test_blank_and_wake_reach_the_lock_screen(self):
        seen = []
        shell = _shell_ns(_lock_screen=types.SimpleNamespace(
            on_blank_changed=seen.append))
        shell._show_lock_screen = lambda: None
        window.ShellWindow.on_blank_changed(shell, True)
        window.ShellWindow.on_blank_changed(shell, False)
        assert seen == [True, False]

    def test_resync_applies_new_timeout_while_blanked(self):
        shell = _shell_ns()
        shell.config = _config_with(auto_lock_timeout=120)
        locked = []
        shell._show_lock_screen = lambda: locked.append(True)
        window.ShellWindow.on_blank_changed(shell, True)
        first_id = shell._lock_src
        shell.config = _config_with(auto_lock_timeout=30)
        window.ShellWindow._resync_blank_lock(shell)
        assert _glib._rec['removed'] == [first_id]
        assert _glib._rec['timeouts'][-1][0] == 30

    def test_no_motion_or_key_idle_timer_remains(self):
        assert not hasattr(window.ShellWindow, '_setup_idle_timer')
        assert not hasattr(window.ShellWindow, '_reset_idle_timer')
        assert not hasattr(window.ShellWindow, '_on_idle_timeout')


class TestNoLockDuringLiveCall:
    def test_blank_with_zero_timeout_skips_lock(self):
        shell = _shell_ns()
        shell._modem_monitor = types.SimpleNamespace(
            calls=lambda: [_live_call()])
        locked = []
        shell._show_lock_screen = lambda: locked.append(True)
        window.ShellWindow.on_blank_changed(shell, True)
        assert locked == []
        assert shell._lock_src is None

    def test_blank_with_timeout_schedules_nothing(self):
        shell = _shell_ns()
        shell.config = _config_with(auto_lock_timeout=120)
        shell._modem_monitor = types.SimpleNamespace(
            calls=lambda: [_live_call()])
        window.ShellWindow.on_blank_changed(shell, True)
        assert _glib._rec['timeouts'] == []
        assert shell._lock_src is None

    def test_fired_timer_with_live_call_does_not_lock(self):
        shell = _shell_ns()
        shell.config = _config_with(auto_lock_timeout=120)
        locked = []
        shell._show_lock_screen = lambda: locked.append(True)
        window.ShellWindow.on_blank_changed(shell, True)
        shell._modem_monitor = types.SimpleNamespace(
            calls=lambda: [_live_call()])
        window.ShellWindow._on_blank_lock_timeout(shell)
        assert locked == []

    def test_terminated_call_does_not_block_lock(self):
        shell = _shell_ns()
        shell._modem_monitor = types.SimpleNamespace(calls=lambda: [
            _live_call(state=modem_monitor._STATE_TERMINATED)])
        locked = []
        shell._show_lock_screen = lambda: locked.append(True)
        window.ShellWindow.on_blank_changed(shell, True)
        assert locked == [True]

    def test_calls_changed_holds_and_releases_idle_inhibit(self):
        mgr = types.SimpleNamespace(
            inhibited=[], inhibit_idle=lambda r, a: mgr.inhibited.append((r, a)))
        shell = _shell_ns(_display_mgr=mgr)
        window.ShellWindow._on_calls_changed(shell, [_live_call()])
        assert mgr.inhibited == [('call', True)]
        window.ShellWindow._on_calls_changed(
            shell, [_live_call(state=modem_monitor._STATE_TERMINATED)])
        assert mgr.inhibited[-1] == ('call', False)


    def test_call_ending_while_blanked_arms_the_lock(self):
        shell = _shell_ns()
        calls = [_live_call()]
        shell._modem_monitor = types.SimpleNamespace(calls=lambda: calls)
        locked = []
        shell._show_lock_screen = lambda: locked.append(True)
        shell.is_locked = lambda: False
        shell._schedule_blank_lock = (
            lambda: window.ShellWindow._schedule_blank_lock(shell))
        window.ShellWindow._on_calls_changed(shell, calls)
        window.ShellWindow.on_blank_changed(shell, True)
        assert locked == []
        calls[:] = []
        window.ShellWindow._on_calls_changed(shell, [])
        assert locked == [True]

class TestIsLockedAndReassert:
    def test_no_lock_surface_is_unlocked(self):
        assert window.ShellWindow.is_locked(_shell_ns()) is False

    def test_hidden_lock_is_unlocked(self):
        lock = types.SimpleNamespace(get_visible=lambda: False)
        assert window.ShellWindow.is_locked(_shell_ns(_lock_screen=lock)) is False

    def test_visible_lock_is_locked(self):
        lock = types.SimpleNamespace(get_visible=lambda: True)
        assert window.ShellWindow.is_locked(_shell_ns(_lock_screen=lock)) is True

    def test_show_lock_represents_call_ui_above_lock(self):
        presented = []
        lock = types.SimpleNamespace(
            get_visible=lambda: True,
            prepare=lambda: presented.append('prepare'),
            present=lambda: presented.append('lock'),
            set_visible=lambda v: presented.append(('lock-visible', v)),
            on_shown=lambda: presented.append('shown'))
        call_ui = types.SimpleNamespace(
            present=lambda: presented.append('call-ui'))
        shell = _shell_ns(_lock_screen=lock, _call_ui=call_ui)
        shell._modem_monitor = types.SimpleNamespace(
            calls=lambda: [_live_call()])
        window.ShellWindow._show_lock_screen(shell)
        assert presented[-1] == 'call-ui'
        assert 'lock' in presented

    def test_show_lock_without_live_call_leaves_call_ui_down(self):
        presented = []
        lock = types.SimpleNamespace(
            get_visible=lambda: True,
            prepare=lambda: presented.append('prepare'),
            present=lambda: presented.append('lock'),
            set_visible=lambda v: presented.append(('lock-visible', v)),
            on_shown=lambda: presented.append('shown'))
        call_ui = types.SimpleNamespace(
            present=lambda: presented.append('call-ui'))
        shell = _shell_ns(_lock_screen=lock, _call_ui=call_ui)
        window.ShellWindow._show_lock_screen(shell)
        assert 'call-ui' not in presented
        assert 'shown' in presented

    def test_incoming_call_while_locked_wakes_and_presents(
            self, monkeypatch):
        monkeypatch.setitem(sys.modules, 'modem_monitor', modem_monitor)
        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner',
                            lambda name: False)
        shown = []
        call_ui = types.SimpleNamespace(
            show_incoming=lambda c, n: shown.append(('incoming', c, n)),
            present=lambda: shown.append('present'))
        woken = []
        mgr = types.SimpleNamespace(wake=lambda: woken.append(True))
        dnd = types.SimpleNamespace(is_active=lambda: False,
                                    note_call=lambda n: shown.append(('noted', n)))
        shell = _shell_ns(_display_mgr=mgr)
        shell.dnd_state = dnd
        shell.config = _config_with(auto_lock_timeout=0,
                                    sound_ringtone=False)
        shell._lock_screen = types.SimpleNamespace(
            get_visible=lambda: True)
        shell._ensure_call_surfaces = lambda: (call_ui, None)
        window.ShellWindow._on_call_incoming(shell, 'Mom', '+15550001111')
        assert ('incoming', 'Mom', '+15550001111') in shown
        assert woken == [True]
        assert 'present' in shown

    def test_incoming_call_while_unlocked_wakes_without_lock_present(
            self, monkeypatch):
        # A blanked but unlocked phone must not ring dark: the display
        # wakes either way; only the above-lock re-present is lock-only.
        monkeypatch.setitem(sys.modules, 'modem_monitor', modem_monitor)
        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner',
                            lambda name: False)
        shown = []
        call_ui = types.SimpleNamespace(
            show_incoming=lambda c, n: shown.append(('incoming', c, n)),
            present=lambda: shown.append('present'))
        woken = []
        mgr = types.SimpleNamespace(wake=lambda: woken.append(True))
        dnd = types.SimpleNamespace(is_active=lambda: False,
                                    note_call=lambda n: None)
        shell = _shell_ns(_display_mgr=mgr)
        shell.dnd_state = dnd
        shell.config = _config_with(auto_lock_timeout=0,
                                    sound_ringtone=False)
        shell._ensure_call_surfaces = lambda: (call_ui, None)
        window.ShellWindow._on_call_incoming(shell, 'Mom', '+15550001111')
        assert ('incoming', 'Mom', '+15550001111') in shown
        assert woken == [True]
        assert 'present' not in shown


def _app_with_shell(locked):
    app = object.__new__(main.XXWMApplication)
    calls = []
    shell = types.SimpleNamespace(
        _locked=locked,
        is_locked=lambda: shell._locked,
        _show_lock_screen=lambda: calls.append('lock'),
        _show_shade=lambda: calls.append('shade'),
        _show_switcher=lambda: calls.append('switcher'),
        _show_keyboard=lambda: calls.append('keyboard'),
        _handle_back=lambda: calls.append('back'),
        stack=types.SimpleNamespace(
            set_visible_child_name=lambda name: calls.append(
                ('home', name))),
        present_over_apps=lambda: calls.append('over-apps'),
        _switcher=None,
        _shade=None,
        go_home=lambda: (
            shell.stack.set_visible_child_name('home'),
            shell.present_over_apps()),
        _apply_layer_policy=lambda: calls.append('layer'),
    )
    app._shell = shell
    app._show_welcome_tour = lambda: calls.append('welcome')
    return app, calls


class TestIpcGatedWhileLocked:
    SILENCED = ['shade.show', 'gesture.shade', 'switcher.show',
                'gesture.switcher', 'gesture.home', 'gesture.keyboard',
                'gesture.back', 'welcome']

    @pytest.mark.parametrize('verb', SILENCED)
    def test_verbs_ignored_while_locked(self, verb):
        app, calls = _app_with_shell(locked=True)
        app._on_ipc_command(verb)
        assert calls == []

    def test_lock_still_allowed_while_locked(self):
        app, calls = _app_with_shell(locked=True)
        app._on_ipc_command('lock')
        assert calls == ['lock']

    def test_hides_still_run_while_locked(self):
        app, calls = _app_with_shell(locked=True)
        shade = types.SimpleNamespace(
            hide_shade=lambda: calls.append('shade-hide'))
        switcher = types.SimpleNamespace(
            hide_switcher=lambda: calls.append('switcher-hide'))
        app._shell._shade = shade
        app._shell._switcher = switcher
        app._on_ipc_command('shade.hide')
        app._on_ipc_command('switcher.hide')
        assert calls == ['shade-hide', 'switcher-hide']

    @pytest.mark.parametrize('verb,expected', [
        ('shade.show', ['shade']),
        ('gesture.shade', ['shade']),
        ('switcher.show', ['switcher']),
        ('gesture.switcher', ['switcher']),
        ('gesture.keyboard', ['keyboard']),
        ('welcome', ['welcome']),
        ('lock', ['lock']),
    ])
    def test_verbs_work_while_unlocked(self, verb, expected):
        app, calls = _app_with_shell(locked=False)
        app._on_ipc_command(verb)
        assert calls == expected

    def test_gesture_home_and_back_while_unlocked(self):
        app, calls = _app_with_shell(locked=False)
        app._shell._back_layer = types.SimpleNamespace(
            flash_back=lambda v: calls.append(('flash', v)))
        app._on_ipc_command('gesture.home')
        assert ('home', 'home') in calls
        assert 'over-apps' in calls
        app._on_ipc_command('gesture.back')
        assert 'back' in calls
        assert ('flash', True) in calls


class TestIpcPageHome:
    def test_page_home_raises_shell_like_gesture(self):
        app, calls = _app_with_shell(locked=False)
        app._on_ipc_command('page.home')
        assert ('home', 'home') in calls
        assert 'over-apps' in calls

    def test_other_pages_still_switch_stack(self):
        app, calls = _app_with_shell(locked=False)
        app._on_ipc_command('page.apps')
        assert calls == [('home', 'apps'), 'layer']


class TestIpcReplayBeforePresent:
    def _preshell_app(self):
        app = object.__new__(main.XXWMApplication)
        app._shell = None
        app._pending_ipc = []
        return app

    def test_pre_present_commands_queue_in_order(self):
        app = self._preshell_app()
        app._on_ipc_command('gesture.home')
        app._on_ipc_command('page.apps')
        assert app._pending_ipc == ['gesture.home', 'page.apps']

    def test_replay_runs_queued_commands_in_order(self):
        app, calls = _app_with_shell(locked=False)
        app._pending_ipc = ['gesture.home', 'page.apps']
        app._replay_pending_ipc()
        assert ('home', 'home') in calls
        assert 'over-apps' in calls
        assert ('home', 'apps') in calls
        assert app._pending_ipc == []


def _display_mgr(monkeypatch, **kwargs):
    monkeypatch.setattr(display_manager, '_all_event_devices',
                        lambda: ['/dev/input/eventX'])
    monkeypatch.setattr(display_manager, '_watch_evdev', lambda *a, **k: None)
    monkeypatch.setattr(display_manager, '_detect_fp_node',
                        lambda: '/dev/input/event9')
    monkeypatch.setattr(display_manager, '_brightnessctl',
                        lambda *a: '200')
    monkeypatch.setattr(display_manager, '_wlopm', lambda mode: True)
    return display_manager.DisplayManager(**kwargs)


class TestBlankChangedNotification:
    def test_blank_and_wake_notify(self, monkeypatch):
        states = []
        mgr = _display_mgr(monkeypatch,
                           on_blank_changed=lambda b: states.append(b))
        _glib._rec['timeouts'].clear()
        mgr._blank()
        mgr._wake()
        assert states == [True, False]

    def test_power_short_press_blank_notifies(self, monkeypatch):
        states = []
        mgr = _display_mgr(monkeypatch,
                           on_blank_changed=lambda b: states.append(b))
        mgr._on_event('/dev/input/eventX', display_manager.EV_KEY,
                      display_manager.KEY_POWER, 1)
        mgr._on_event('/dev/input/eventX', display_manager.EV_KEY,
                      display_manager.KEY_POWER, 0)
        assert states == [True]

    def test_power_short_press_wake_notifies(self, monkeypatch):
        states = []
        mgr = _display_mgr(monkeypatch,
                           on_blank_changed=lambda b: states.append(b))
        mgr._blanked = True
        mgr._on_event('/dev/input/eventX', display_manager.EV_KEY,
                      display_manager.KEY_POWER, 1)
        mgr._on_event('/dev/input/eventX', display_manager.EV_KEY,
                      display_manager.KEY_POWER, 0)
        assert states == [False]

    def test_inhibited_idle_blank_stays_quiet(self, monkeypatch):
        states = []
        mgr = _display_mgr(monkeypatch,
                           on_blank_changed=lambda b: states.append(b))
        mgr.inhibit_idle('call', True)
        mgr._on_idle()
        assert states == []

    def test_power_blank_notifies_despite_inhibit(self, monkeypatch):
        states = []
        mgr = _display_mgr(monkeypatch,
                           on_blank_changed=lambda b: states.append(b))
        mgr.inhibit_idle('call', True)
        mgr._on_event('/dev/input/eventX', display_manager.EV_KEY,
                      display_manager.KEY_POWER, 1)
        mgr._on_event('/dev/input/eventX', display_manager.EV_KEY,
                      display_manager.KEY_POWER, 0)
        assert states == [True]


class TestKeySizeForWidth:
    @pytest.mark.parametrize('width,expected', [
        (320, 96), (360, 96), (720, 96), (1000, 96),
        (304, 96), (300, 94), (240, 74), (224, 69),
        (200, 64), (100, 64), (0, 64), (-50, 64),
    ])
    def test_table(self, width, expected):
        assert lock_screen.key_size_for_width(width) == expected

    def test_garbage_width_falls_back_to_minimum(self):
        assert lock_screen.key_size_for_width('junk') == 64
        assert lock_screen.key_size_for_width(None) == 64

    def test_three_keys_plus_gutters_fit_320px(self):
        size = lock_screen.key_size_for_width(320)
        assert 3 * size + 2 * lock_screen._KEYPAD_SPACING <= 320

    def test_apply_sets_requests_and_skips_repeats(self):
        seen = []
        widgets = [types.SimpleNamespace(
            set_size_request=lambda w, h: seen.append((w, h)))
            for _ in range(3)]
        lock = types.SimpleNamespace(_key_size=0, _key_widgets=widgets)
        lock_screen.LockScreen._apply_keypad_size(lock, 96)
        assert seen == [(96, 76), (96, 76), (96, 76)]
        lock_screen.LockScreen._apply_keypad_size(lock, 96)
        assert seen == [(96, 76), (96, 76), (96, 76)]

    def test_alloc_handler_derives_size_from_width(self):
        seen = []
        widgets = [types.SimpleNamespace(
            set_size_request=lambda w, h: seen.append((w, h)))]
        lock = types.SimpleNamespace(_key_size=96, _key_widgets=widgets)
        lock._apply_keypad_size = lambda size: \
            lock_screen.LockScreen._apply_keypad_size(lock, size)
        alloc = types.SimpleNamespace(width=240)
        lock_screen.LockScreen._on_keypad_alloc(lock, None, alloc)
        assert seen == [(74, 59)]


class TestKeypadKeySize:
    """Height-aware keypad sizing: min(width-based, height-based), 56..96.

    `width` is the horizontal space for the keypad (the key_size_for_width
    convention); `height` is the vertical space for dots + 4 key rows +
    Unlock + spacing once the clock block collapses on reveal.
    """

    @pytest.mark.parametrize('width,height,expected', [
        (272, 504, 79),    # 320x640 revealed (margins + collapsed clock off)
        (312, 584, 96),    # 360x720 revealed: width governs
        (312, 644, 96),    # 360x780 logical (1080x2340 @ scale 3)
        (272, 426, 56),    # starved height clamps at the floor
        (1000, 2000, 96),  # huge viewports clamp at the ceiling
        (200, 200, 56),
    ])
    def test_table(self, width, height, expected):
        assert lock_screen.keypad_key_size(width, height) == expected

    def test_missing_height_falls_back_to_width_only(self):
        assert lock_screen.keypad_key_size(320, None) == 96
        assert lock_screen.keypad_key_size(240, None) == 74

    def test_garbage_width_falls_back_to_minimum(self):
        assert lock_screen.keypad_key_size('junk', 500) == 56
        assert lock_screen.keypad_key_size(None, None) == 56

    def test_height_never_grows_keys_past_width_size(self):
        assert lock_screen.keypad_key_size(240, 5000) == 74
        assert lock_screen.keypad_key_size(240, 5000) == \
            lock_screen.key_size_for_width(240)

    @pytest.mark.parametrize('screen_w,screen_h', [
        (320, 640), (360, 720), (360, 780),
    ])
    def test_revealed_keypad_fits_small_phones(self, screen_w, screen_h):
        size = lock_screen.keypad_key_size(
            screen_w - lock_screen._KEYPAD_SIDE_MARGINS,
            screen_h - lock_screen._COLLAPSED_CLOCK_RESERVE
            - lock_screen._KEYPAD_HEIGHT_MARGIN)
        assert 56 <= size <= 96
        key_h = (size * 4) // 5
        total = (4 * key_h + lock_screen._PIN_ROW_GAPS
                 + lock_screen._PIN_VERTICAL_CHROME)
        avail = (screen_h - lock_screen._COLLAPSED_CLOCK_RESERVE
                 - lock_screen._KEYPAD_HEIGHT_MARGIN)
        assert total <= avail


class _W2FakeRevealer:
    def __init__(self):
        self.revealed = False

    def set_reveal_child(self, value):
        self.revealed = bool(value)


class _W2FakeVis:
    def __init__(self):
        self.visible = True

    def set_visible(self, value):
        self.visible = bool(value)


class _W2FakeClock:
    def __init__(self):
        self.css: list = []

    def add_css_class(self, name):
        if name not in self.css:
            self.css.append(name)

    def remove_css_class(self, name):
        if name in self.css:
            self.css.remove(name)


class _W2FakeKeyWidget:
    def __init__(self):
        self.requests: list = []

    def set_size_request(self, w, h):
        self.requests.append((w, h))


def _reveal_ns(screen_w=320, screen_h=640):
    """Namespace LockScreen for the swipe-up reveal path."""
    grid = types.SimpleNamespace(homogeneous=True)
    grid.set_row_homogeneous = lambda v: setattr(
        grid, 'homogeneous', bool(v))
    top = types.SimpleNamespace(vexpand=True)
    top.set_vexpand = lambda v: setattr(top, 'vexpand', bool(v))
    ns = types.SimpleNamespace(
        _config=_config_with(pin_hash='deadbeef'),
        _pin='',
        _pin_revealer=_W2FakeRevealer(),
        _swipe_hint=_W2FakeVis(),
        clock_label=_W2FakeClock(),
        date_label=_W2FakeVis(),
        _notif_box=_W2FakeVis(),
        _lock_grid=grid,
        _top_pane=top,
        _key_size=0,
        _key_widgets=[_W2FakeKeyWidget()],
    )
    ns.get_width = lambda: screen_w
    ns.get_height = lambda: screen_h
    ns._apply_keypad_size = types.MethodType(
        lock_screen.LockScreen._apply_keypad_size, ns)
    ns._fit_keypad_to_screen = types.MethodType(
        lock_screen.LockScreen._fit_keypad_to_screen, ns)
    ns._set_clock_compact = types.MethodType(
        lock_screen.LockScreen._set_clock_compact, ns)
    return ns


class TestKeypadRevealCollapsesClock:
    def test_swipe_up_compacts_clock_and_sizes_keys(self):
        ns = _reveal_ns()
        lock_screen.LockScreen._swipe_up(ns)
        assert ns._pin_revealer.revealed is True
        assert ns._swipe_hint.visible is False
        assert 'compact' in ns.clock_label.css
        assert ns.date_label.visible is False
        assert ns._notif_box.visible is False
        assert ns._lock_grid.homogeneous is False
        assert ns._top_pane.vexpand is False
        assert ns._key_widgets[0].requests == [(79, 63)]

    def test_compact_restores(self):
        ns = _reveal_ns()
        lock_screen.LockScreen._swipe_up(ns)
        lock_screen.LockScreen._set_clock_compact(ns, False)
        assert 'compact' not in ns.clock_label.css
        assert ns.date_label.visible is True
        assert ns._notif_box.visible is True
        assert ns._lock_grid.homogeneous is True
        assert ns._top_pane.vexpand is True

    def test_fit_reads_window_geometry(self):
        ns = _reveal_ns(360, 720)
        lock_screen.LockScreen._fit_keypad_to_screen(ns)
        assert ns._key_widgets[0].requests == [(96, 76)]


class _FakeFpBus:
    """net.reactivated.Fprint stand-in: GetDefaultDevice then
    ListEnrolledFingers, with unpack() tuples like real Gio replies."""

    def __init__(self, device_path, fingers=None, calls=None):
        self._device_path = device_path
        self._fingers = fingers if fingers is not None else []
        self.calls = calls if calls is not None else []

    def call_sync(self, *args):
        method = args[3]
        self.calls.append(method)
        if method == 'GetDefaultDevice':
            return types.SimpleNamespace(
                unpack=lambda: (self._device_path,))
        assert method == 'ListEnrolledFingers'
        return types.SimpleNamespace(unpack=lambda: (list(self._fingers),))


class TestFingerprintProbe:
    def _bus_with(self, monkeypatch, bus):
        gio = sys.modules['gi.repository.Gio']
        monkeypatch.setattr(gio, 'bus_get_sync',
                            lambda bus_type, _c: bus, raising=False)

    def test_dbus_enrolled_device_reports_true(self, monkeypatch):
        calls = []
        self._bus_with(monkeypatch, _FakeFpBus(
            '/net/reactivated/Fprint/Device/0', fingers=['right-index'],
            calls=calls))
        assert lock_screen._fp_dbus_enrolled() is True
        assert calls == ['GetDefaultDevice', 'ListEnrolledFingers']

    def test_dbus_no_device_means_not_usable(self, monkeypatch):
        self._bus_with(monkeypatch, _FakeFpBus('/'))
        assert lock_screen._fp_dbus_enrolled() is False

    def test_dbus_no_enrolled_fingers_means_not_usable(self, monkeypatch):
        self._bus_with(monkeypatch, _FakeFpBus(
            '/net/reactivated/Fprint/Device/0', fingers=[]))
        assert lock_screen._fp_dbus_enrolled() is False

    def test_dbus_failure_falls_back_to_none(self, monkeypatch):
        gio = sys.modules['gi.repository.Gio']
        monkeypatch.setattr(gio, 'bus_get_sync',
                            lambda *a, **k: (_ for _ in ()).throw(
                                RuntimeError('no bus')), raising=False)
        assert lock_screen._fp_dbus_enrolled() is None

    def test_probe_prefers_dbus_over_subprocess(self, monkeypatch):
        monkeypatch.setattr(lock_screen, '_fp_dbus_enrolled', lambda: True)

        def _explode(*a, **k):
            raise AssertionError('subprocess must not run')
        monkeypatch.setattr(lock_screen.subprocess, 'run', _explode)
        assert lock_screen._fp_probe_sync() is True

    def test_probe_uses_subprocess_only_when_dbus_unreachable(
            self, monkeypatch):
        monkeypatch.setattr(lock_screen, '_fp_dbus_enrolled', lambda: None)
        monkeypatch.setattr(
            lock_screen.subprocess, 'run',
            lambda *a, **k: types.SimpleNamespace(
                returncode=0, stdout='found finger right-index\n', stderr=''))
        assert lock_screen._fp_probe_sync() is True

    def test_probe_negative_dbus_skips_subprocess(self, monkeypatch):
        monkeypatch.setattr(lock_screen, '_fp_dbus_enrolled', lambda: False)

        def _explode(*a, **k):
            raise AssertionError('subprocess must not run')
        monkeypatch.setattr(lock_screen.subprocess, 'run', _explode)
        assert lock_screen._fp_probe_sync() is False

    def test_list_fallback_rejects_bare_install(self, monkeypatch):
        monkeypatch.setattr(
            lock_screen.subprocess, 'run',
            lambda *a, **k: types.SimpleNamespace(
                returncode=0, stdout='no fingers enrolled\n', stderr=''))
        assert lock_screen._fp_list_enrolled() is False

    def test_available_is_cache_only(self, monkeypatch):
        def _explode(*a, **k):
            raise AssertionError('must not touch subprocess or D-Bus')
        monkeypatch.setattr(lock_screen.subprocess, 'run', _explode)
        lock_screen.LockScreen._fp_usable = None
        assert lock_screen.LockScreen._fp_available() is False
        lock_screen.LockScreen._fp_usable = True
        assert lock_screen.LockScreen._fp_available() is True
        lock_screen.LockScreen._fp_usable = False
        assert lock_screen.LockScreen._fp_available() is False

    def test_refresh_spawns_one_worker_and_coalesces(self, monkeypatch):
        import threading
        started = []

        def _fake_thread(*a, **k):
            started.append((a, k))
            return types.SimpleNamespace(start=lambda: started.append('go'))
        monkeypatch.setattr(threading, 'Thread', _fake_thread)
        lock = types.SimpleNamespace(_fp_probe_thread=lambda: None)
        lock_screen.LockScreen.refresh_fp_state(lock)
        assert lock_screen.LockScreen._fp_probing is True
        assert started[0][1]['name'] == 'fp-probe'
        assert started[0][1]['daemon'] is True
        lock_screen.LockScreen.refresh_fp_state(lock)
        assert started == [started[0], 'go']

    def test_probe_done_updates_cache_and_autostarts(self, monkeypatch):
        import threading
        monkeypatch.setattr(threading, 'Thread',
                            lambda *a, **k: types.SimpleNamespace(
                                start=lambda: None))
        events = []
        lock = types.SimpleNamespace(
            _pin='', _lockout_src=None, _fp_running=False,
            get_visible=lambda: True,
            _update_fp_hint=lambda: events.append('hint'),
            try_fingerprint_unlock=lambda: events.append('verify'))
        lock_screen.LockScreen._fp_probe_done(lock, True)
        assert lock_screen.LockScreen._fp_usable is True
        assert events == ['hint', 'verify']

    def test_probe_done_skips_verify_when_hidden_or_typing(self, monkeypatch):
        import threading
        monkeypatch.setattr(threading, 'Thread',
                            lambda *a, **k: types.SimpleNamespace(
                                start=lambda: None))
        for attrs in ({'visible': False, 'pin': ''},
                      {'visible': True, 'pin': '12'}):
            events = []
            lock = types.SimpleNamespace(
                _pin=attrs['pin'], _lockout_src=None, _fp_running=False,
                get_visible=lambda v=attrs['visible']: v,
                _update_fp_hint=lambda: events.append('hint'),
                try_fingerprint_unlock=lambda: events.append('verify'))
            lock_screen.LockScreen._fp_probe_done(lock, True)
            assert events == ['hint']


class _FakeLockLabel:
    def __init__(self):
        self.text = ''

    def set_text(self, text):
        self.text = text

    def set_label(self, text):
        self.text = text

    def add_css_class(self, _name):
        pass

    def remove_css_class(self, _name):
        pass


class _FakeKey:
    def __init__(self):
        self.sensitive = True

    def set_sensitive(self, value):
        self.sensitive = bool(value)


def _lock_harness(config):
    """Headless LockScreen surface: the real decision methods run against
    this namespace, with internal helpers delegated back to the class
    (the test_lock_screen _LockHarness idiom)."""
    lock = types.SimpleNamespace(
        _config=config, _pin='', _fail_count=0, _lockout_src=None,
        _fp_running=False, _open_shade_after_unlock=False,
        dots_label=_FakeLockLabel(), error_label=_FakeLockLabel(),
        lockout_label=_FakeLockLabel(), _fp_hint=_FakeLockLabel(),
        _keypad_btns=[_FakeKey() for _ in range(3)],
        visible=True, unlocked=0,
        get_visible=lambda: lock.visible,
        set_visible=lambda v: setattr(lock, 'visible', bool(v)),
        _on_unlock=lambda: setattr(
            lock, 'unlocked', lock.unlocked + 1),
        _on_open_shade=lambda: None)
    lock._refresh_dots = lambda: lock_screen.LockScreen._refresh_dots(lock)
    lock._persist_lockout_state = lambda u: \
        lock_screen.LockScreen._persist_lockout_state(lock, u)
    lock._shake = lambda: lock_screen.LockScreen._shake(lock)
    lock._set_keypad_sensitive = lambda s: \
        lock_screen.LockScreen._set_keypad_sensitive(lock, s)
    lock._tick_lockout = lambda: lock_screen.LockScreen._tick_lockout(lock)
    lock._start_lockout = lambda s: lock_screen.LockScreen._start_lockout(
        lock, s)
    lock._unlock = lambda: lock_screen.LockScreen._unlock(lock)
    return lock


class TestLockoutPersistence:
    def test_wrong_pin_persists_fail_count(self, isolated_home):
        config = ShellConfig()
        config.set_pin('123456')
        lock = _lock_harness(config)
        lock._pin = '000000'
        lock_screen.LockScreen._check_pin(lock)
        assert lock._fail_count == 1
        fails, until = ShellConfig().get_lockout_state()
        assert (fails, until) == (1, 0.0)
        assert 'Incorrect PIN' in lock.error_label.text

    def test_escalating_lockout_persists_until(self, isolated_home):
        import time
        config = ShellConfig()
        config.set_pin('123456')
        lock = _lock_harness(config)
        before = time.time()
        for _ in range(5):
            lock._pin = '000000'
            lock_screen.LockScreen._check_pin(lock)
        fails, until = ShellConfig().get_lockout_state()
        assert fails == 5
        assert before + 25 <= until <= time.time() + 35
        assert lock._lockout_src is not None

    def test_restart_resumes_armed_lockout(self, isolated_home):
        import time
        config = ShellConfig()
        config.set_pin('123456')
        config.set_lockout_state(5, time.time() + 120)
        fresh = ShellConfig()
        lock = _lock_harness(fresh)
        lock_screen.LockScreen._resume_lockout_state(lock)
        assert lock._fail_count == 5
        assert lock._lockout_src is not None
        assert 'Try again in' in lock.lockout_label.text
        assert all(not b.sensitive for b in lock._keypad_btns)

    def test_restart_keeps_count_but_skips_expired_lockout(
            self, isolated_home):
        import time
        config = ShellConfig()
        config.set_pin('123456')
        config.set_lockout_state(5, time.time() - 60)
        lock = _lock_harness(ShellConfig())
        lock_screen.LockScreen._resume_lockout_state(lock)
        assert lock._fail_count == 5
        assert lock._lockout_src is None

    def test_correct_pin_clears_persisted_state(self, isolated_home):
        config = ShellConfig()
        config.set_pin('123456')
        lock = _lock_harness(config)
        lock._pin = '000000'
        lock_screen.LockScreen._check_pin(lock)
        assert config.get_lockout_state()[0] == 1
        lock._pin = '123456'
        lock_screen.LockScreen._check_pin(lock)
        assert lock.unlocked == 1
        assert ShellConfig().get_lockout_state() == (0, 0.0)
        assert 'lock_screen_lockout' not in ShellConfig().data


class TestLockoutConfig:
    def test_default_auto_lock_is_zero(self):
        assert DEFAULT_CONFIG['auto_lock_timeout'] == 0
        cfg = ShellConfig.__new__(ShellConfig)
        cfg.data = dict(DEFAULT_CONFIG)
        assert cfg.auto_lock_timeout == 0

    def test_old_values_still_accepted(self):
        cfg = ShellConfig.__new__(ShellConfig)
        cfg.data = dict(DEFAULT_CONFIG, auto_lock_timeout=120)
        assert cfg.auto_lock_timeout == 120

    def test_lockout_state_round_trip(self, isolated_home):
        import time
        config = ShellConfig()
        assert config.get_lockout_state() == (0, 0.0)
        config.set_lockout_state(3, time.time() + 60)
        fails, until = ShellConfig().get_lockout_state()
        assert fails == 3
        assert until > time.time()
        config.clear_lockout_state()
        assert ShellConfig().get_lockout_state() == (0, 0.0)

    def test_corrupt_lockout_state_reads_zero(self):
        cfg = ShellConfig.__new__(ShellConfig)
        for bad in ({'fails': 'junk'}, {'fails': 2, 'until': 'junk'},
                    'junk', None, {'fails': -4, 'until': -9}):
            cfg.data = dict(DEFAULT_CONFIG, lock_screen_lockout=bad)
            fails, until = cfg.get_lockout_state()
            assert fails >= 0 and until >= 0.0


class TestLockoutBackupExclusion:
    def test_export_excludes_lockout_state(self, isolated_home):
        import json
        from backup import _EXCLUDED_KEYS, export_backup
        assert set(_EXCLUDED_KEYS) == {
            'pin_hash', 'lock_screen_lockout', 'smart_lock_bt_devices',
            'smart_lock_wifi_ssids', 'smart_lock_last_pin_time'}
        config = ShellConfig()
        config.set_pin('123456')
        config.set_lockout_state(5, 9e12)
        backup = export_backup(config)
        assert 'lock_screen_lockout' not in json.dumps(backup)
        assert 'pin_hash' not in json.dumps(backup)

    def test_restore_preserves_live_lockout(self, isolated_home):
        import time
        from backup import export_backup, restore_backup
        config = ShellConfig()
        config.set_pin('123456')
        config.set_home_slots([
            {'type': 'app', 'label': 'Notes', 'app_id': 'notes.desktop'}])
        payload = export_backup(config)
        config.set_lockout_state(4, time.time() + 90)
        assert restore_backup(config, payload) is True
        fails, until = config.get_lockout_state()
        assert fails == 4
        assert until > time.time()

    def test_restore_ignores_smuggled_lockout_key(self, isolated_home):
        from backup import export_backup, restore_backup
        config = ShellConfig()
        payload = export_backup(config)
        payload['lock_screen_lockout'] = {'fails': 99, 'until': 9e12}
        assert restore_backup(config, payload) is True
        assert config.get_lockout_state() == (0, 0.0)
