"""Tests for the lock screen: notification line filtering (pure logic),
live-config PIN decisions, and the fingerprint verify invocation."""
import types
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from lock_lines import _FAIL_THRESHOLD, _lockout_secs, lock_screen_lines

FEED = [('Chat', 'New message from Sam'), ('Email', '2 unread'), ('', 'orphan summary')]


class TestLockScreenLines:
    def test_summary_mode(self):
        lines = lock_screen_lines(FEED, 'summary', dnd_active=False)
        assert lines == [
            'Chat — New message from Sam',
            'Email — 2 unread',
            'orphan summary',
        ]

    def test_count_mode(self):
        assert lock_screen_lines(FEED, 'count', dnd_active=False) == ['3 notifications']
        assert lock_screen_lines(FEED[:1], 'count', dnd_active=False) == ['1 notification']

    def test_off_mode(self):
        assert lock_screen_lines(FEED, 'off', dnd_active=False) == []

    def test_dnd_hides_everything(self):
        assert lock_screen_lines(FEED, 'summary', dnd_active=True) == []
        assert lock_screen_lines(FEED, 'count', dnd_active=True) == []

    def test_empty_feed(self):
        assert lock_screen_lines([], 'summary', dnd_active=False) == []
        assert lock_screen_lines([], 'count', dnd_active=False) == []

    def test_empty_app_name_filtered_from_join(self):
        # An empty app name must not leave a leading ' — ' in the line; the
        # summary alone is kept.
        assert lock_screen_lines([('', 'orphan summary')], 'summary', dnd_active=False) \
            == ['orphan summary']
        assert lock_screen_lines([('Chat', ''), ('', '')], 'summary', dnd_active=False) \
            == ['Chat']


class TestLockoutSecs:
    def test_below_threshold_no_lockout(self):
        assert _lockout_secs(_FAIL_THRESHOLD - 1) == 0
        assert _lockout_secs(0) == 0

    def test_at_threshold_starts_lockout(self):
        assert _lockout_secs(_FAIL_THRESHOLD) == 30

    def test_scales_with_failures(self):
        assert _lockout_secs(_FAIL_THRESHOLD + 1) == 60

    def test_capped_at_five_minutes(self):
        assert _lockout_secs(_FAIL_THRESHOLD + 100) == 300


class _FakeLabel:
    def __init__(self):
        self.text = ''
        self.css: list[str] = []

    def set_text(self, text):
        self.text = text

    def add_css_class(self, name):
        if name not in self.css:
            self.css.append(name)

    def remove_css_class(self, name):
        if name in self.css:
            self.css.remove(name)


class _FakeRevealer:
    def __init__(self):
        self.revealed = False

    def set_reveal_child(self, value):
        self.revealed = bool(value)


class _FakeVisibility:
    def __init__(self):
        self.visible = True

    def set_visible(self, value):
        self.visible = bool(value)


class _FakeNotifBox:
    def __init__(self):
        self.visible = True

    def get_first_child(self):
        return None

    def remove(self, _child):
        pass

    def append(self, _child):
        pass

    def set_visible(self, value):
        self.visible = bool(value)


class _FakeGridPane:
    def __init__(self):
        self.homogeneous = True
        self.vexpand = True

    def set_row_homogeneous(self, value):
        self.homogeneous = bool(value)

    def set_vexpand(self, value):
        self.vexpand = bool(value)


class _LockHarness:
    """Headless stand-in for the LockScreen widget surface (repo idiom: GTK
    widgets are never constructed under pytest — see test_switcher_cards).
    The real LockScreen decision methods run against this namespace self."""

    def __init__(self, config):
        self._config = config
        self._pin = ''
        self._fail_count = 0
        self._lockout_src = None
        self._open_shade_after_unlock = False
        self._pin_revealer = _FakeRevealer()
        self._swipe_hint = _FakeVisibility()
        self.dots_label = _FakeLabel()
        self.error_label = _FakeLabel()
        self.clock_label = _FakeLabel()
        self.date_label = _FakeVisibility()
        self._notif_box = _FakeNotifBox()
        self._lock_grid = _FakeGridPane()
        self._top_pane = _FakeGridPane()
        self._key_size = 0
        self._key_widgets: list = []
        self._get_notifications = lambda: []
        self._dnd_active = lambda: False
        self.visible = True
        self.unlocked = 0

    def get_width(self):
        return 360

    def get_height(self):
        return 720

    def _on_unlock(self):
        self.unlocked += 1

    def _on_open_shade(self):
        pass

    def set_visible(self, value):
        self.visible = bool(value)

    def _refresh_dots(self):
        _lock_screen_module().LockScreen._refresh_dots(self)

    def _refresh_notifications(self):
        _lock_screen_module().LockScreen._refresh_notifications(self)

    def _shake(self):
        _lock_screen_module().LockScreen._shake(self)

    def _unlock(self):
        _lock_screen_module().LockScreen._unlock(self)

    def _apply_keypad_size(self, size):
        _lock_screen_module().LockScreen._apply_keypad_size(self, size)

    def _fit_keypad_to_screen(self):
        _lock_screen_module().LockScreen._fit_keypad_to_screen(self)

    def _set_clock_compact(self, compact):
        _lock_screen_module().LockScreen._set_clock_compact(self, compact)

    def _persist_lockout_state(self, until):
        _lock_screen_module().LockScreen._persist_lockout_state(self, until)


def _lock_screen_module():
    # Imported lazily: sibling test modules stub sys.modules['gi'] during
    # collection; conftest restores the real bindings before tests run.
    import lock_screen
    return lock_screen


def _set_pin_out_of_band(pin):
    """The documented checklist workflow: edit ~/.config/xx-wm/config.json
    out-of-band while the shell keeps running."""
    writer = _lock_screen_module().ShellConfig()
    writer.set_pin(pin)


def _remove_pin_out_of_band():
    writer = _lock_screen_module().ShellConfig()
    del writer.data['pin_hash']
    writer.save()


def _hot_reload(config):
    """Mirror window.ShellWindow._reload_config: the window resets its live
    instance's data in place and re-reads the file from disk."""
    from config import DEFAULT_CONFIG
    config.data = dict(DEFAULT_CONFIG)
    config.load()


class TestPinConfigIsLive:
    """S2 regression: the cached LockScreen must consult the CURRENT stored
    pin_hash at every lock/unlock decision, not a construction-time snapshot."""

    @pytest.fixture(autouse=True)
    def _isolated_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))

    @pytest.fixture(autouse=True)
    def _no_real_glib_sources(self, monkeypatch):
        monkeypatch.setattr(
            _lock_screen_module().GLib, 'timeout_add', lambda *a, **k: 1)

    def test_pin_added_after_startup_is_enforced_on_next_lock(self):
        lock_screen = _lock_screen_module()
        config = lock_screen.ShellConfig()
        assert config.pin_hash is None
        lock = _LockHarness(config)
        lock_screen.LockScreen.prepare(lock)
        lock_screen.LockScreen._swipe_up(lock)
        assert lock.unlocked == 1  # baseline: no PIN → swipe unlocks directly

        _set_pin_out_of_band('123456')
        _hot_reload(config)
        assert config.pin_hash is not None

        lock.unlocked = 0
        lock_screen.LockScreen.prepare(lock)
        lock_screen.LockScreen._swipe_up(lock)
        assert lock.unlocked == 0  # no longer unlocks directly…
        assert lock._pin_revealer.revealed is True  # …it now demands the PIN

        lock._pin = '999999'
        lock_screen.LockScreen._check_pin(lock)
        assert lock.unlocked == 0  # wrong PIN does not unlock
        assert 'Incorrect PIN' in lock.error_label.text

        lock._pin = '123456'
        lock_screen.LockScreen._check_pin(lock)
        assert lock.unlocked == 1  # correct PIN unlocks

    def test_pin_removed_after_startup_unlocks_directly_again(self):
        lock_screen = _lock_screen_module()
        _set_pin_out_of_band('123456')
        config = lock_screen.ShellConfig()
        assert config.pin_hash is not None
        lock = _LockHarness(config)
        lock_screen.LockScreen._swipe_up(lock)
        assert lock.unlocked == 0
        assert lock._pin_revealer.revealed is True  # PIN enforced at start

        _remove_pin_out_of_band()
        _hot_reload(config)
        assert config.pin_hash is None

        lock._pin_revealer.revealed = False
        lock_screen.LockScreen._swipe_up(lock)
        assert lock.unlocked == 1  # follows NEW config: direct unlock
        assert lock._pin_revealer.revealed is False  # keypad never demanded

    def test_changed_pin_takes_effect_without_restart(self):
        lock_screen = _lock_screen_module()
        _set_pin_out_of_band('111111')
        config = lock_screen.ShellConfig()
        lock = _LockHarness(config)

        lock._pin = '111111'
        lock_screen.LockScreen._check_pin(lock)
        assert lock.unlocked == 1  # old PIN valid against old hash

        _set_pin_out_of_band('222222')
        _hot_reload(config)

        lock.unlocked = 0
        lock._pin = '111111'
        lock_screen.LockScreen._check_pin(lock)
        assert lock.unlocked == 0  # old PIN rejected against the NEW hash

        lock._pin = '222222'
        lock_screen.LockScreen._check_pin(lock)
        assert lock.unlocked == 1  # new PIN accepted


class _RecordingProvider:
    """Fake Gtk.CssProvider: records load_from_data payloads."""

    def __init__(self, loads: list):
        self._loads = loads

    def load_from_data(self, data):
        self._loads.append(data)


class TestThemeProviderDedupe:
    """LockScreen can be built repeatedly; the display-level theme provider
    must be registered once and its data reloaded — stacking a new provider
    per construction accumulates without bound (font_theme idiom)."""

    @pytest.fixture
    def lock_module(self, monkeypatch):
        """Fresh lock_screen import bound to a recording fake gi stack.
        The previous module binding is restored afterwards so later tests
        never see the fakes baked into its globals."""
        loads: list[bytes] = []
        added: list[object] = []

        gdk = types.ModuleType('gi.repository.Gdk')
        gdk.Display = types.SimpleNamespace(get_default=lambda: object())
        gtk = types.ModuleType('gi.repository.Gtk')
        gtk.Window = type('Window', (), {})  # class LockScreen(Gtk.Window)
        gtk.CssProvider = lambda: _RecordingProvider(loads)
        gtk.StyleContext = types.SimpleNamespace(
            add_provider_for_display=lambda _d, p, _prio: added.append(p))
        gtk.STYLE_PROVIDER_PRIORITY_APPLICATION = 800
        glib = types.ModuleType('gi.repository.GLib')

        repo = types.ModuleType('gi.repository')
        repo.Gdk, repo.Gtk, repo.GLib = gdk, gtk, glib
        gi = types.ModuleType('gi')

        def _require_version(namespace, _version):
            if namespace == 'Gtk4LayerShell':
                raise ValueError(f'{namespace} not available')

        gi.require_version = _require_version
        gi.repository = repo

        monkeypatch.setitem(sys.modules, 'gi', gi)
        monkeypatch.setitem(sys.modules, 'gi.repository', repo)
        monkeypatch.setitem(sys.modules, 'gi.repository.Gdk', gdk)
        monkeypatch.setitem(sys.modules, 'gi.repository.Gtk', gtk)
        monkeypatch.setitem(sys.modules, 'gi.repository.GLib', glib)

        saved = sys.modules.pop('lock_screen', None)
        try:
            import lock_screen
            yield types.SimpleNamespace(
                module=lock_screen, loads=loads, added=added)
        finally:
            if saved is not None:
                sys.modules['lock_screen'] = saved
            else:
                sys.modules.pop('lock_screen', None)

    def test_repeated_constructions_register_one_provider(self, lock_module):
        from config import THEME_PRESETS
        ls = lock_module.module
        assert ls._theme_provider is None  # fresh module state

        ls._apply_lock_theme(THEME_PRESETS['amoled'])
        ls._apply_lock_theme(THEME_PRESETS['amoled'])  # second construction

        assert len(lock_module.added) == 1  # registered once per display
        assert len(lock_module.loads) == 2  # sheet data reloaded per build

    def test_theme_change_reaches_the_shared_provider(self, lock_module):
        from config import THEME_PRESETS
        ls = lock_module.module
        ls._apply_lock_theme(THEME_PRESETS['amoled'])
        ls._apply_lock_theme(THEME_PRESETS['paper'])

        latest = lock_module.loads[-1]
        assert THEME_PRESETS['paper'].background.encode() in latest
        assert THEME_PRESETS['amoled'].background.encode() not in latest

    def test_headless_no_display_never_touches_providers(
            self, lock_module, monkeypatch):
        ls = lock_module.module
        monkeypatch.setattr(ls.Gdk.Display, 'get_default', lambda: None)

        ls._apply_lock_theme(__import__('config').THEME_PRESETS['amoled'])

        assert ls._theme_provider is None
        assert lock_module.added == []
        assert lock_module.loads == []


class TestFingerprintVerifyArgv:
    """S3 regression: fprintd-verify takes the finger via -f; the username is
    positional (defaulting to the invoking user). '-f <user>' can never match,
    so fingerprint unlock previously always failed."""

    def test_verify_invocation_is_bare_fprintd_verify(self, monkeypatch):
        lock_screen = _lock_screen_module()
        calls = []

        def _fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return types.SimpleNamespace(returncode=0)

        monkeypatch.setattr(lock_screen.subprocess, 'run', _fake_run)
        idle = []
        monkeypatch.setattr(
            lock_screen.GLib, 'idle_add',
            lambda cb, *a: idle.append((cb, a)) or 1)

        results = []
        lock = types.SimpleNamespace(
            _fp_running=False,
            _fp_result=lambda matched: results.append(matched),
        )
        lock_screen.LockScreen._fp_thread(lock)

        # Exact argv pin, mirroring the nmcli discipline in test_quick_tiles.
        assert [cmd for cmd, _kw in calls] == [['fprintd-verify']]
        assert calls[0][1] == {'timeout': 10, 'capture_output': True}

        assert idle and idle[0][1] == (True,)
        idle[0][0](True)
        assert results == [True]

    def test_verify_failure_still_reports_not_matched(self, monkeypatch):
        lock_screen = _lock_screen_module()
        monkeypatch.setattr(
            lock_screen.subprocess, 'run',
            lambda cmd, **k: types.SimpleNamespace(returncode=1))
        idle = []
        monkeypatch.setattr(
            lock_screen.GLib, 'idle_add',
            lambda cb, *a: idle.append((cb, a)) or 1)

        lock = types.SimpleNamespace(
            _fp_running=False,
            _fp_result=lambda matched: None,
        )
        lock_screen.LockScreen._fp_thread(lock)
        assert idle[0][1] == (False,)


def test_revealed_keypad_sits_low_and_clock_clears_cutout(monkeypatch):
    # FLX1 at 600x1333: the revealed keypad hung from the top (clock under
    # the punch-hole, lower half empty). Compact anchors it to the bottom.
    import types
    mod = _lock_screen_module()
    align = types.SimpleNamespace(END='end', FILL='fill')
    monkeypatch.setattr(mod, 'Gtk', types.SimpleNamespace(Align=align))

    class _Box:
        def __init__(self):
            self.valign = None

        def set_valign(self, value):
            self.valign = value

    ns = types.SimpleNamespace(_mid_box=_Box())
    mod.LockScreen._set_clock_compact(ns, True)
    assert ns._mid_box.valign == 'end'
    mod.LockScreen._set_clock_compact(ns, False)
    assert ns._mid_box.valign == 'fill'

    import status_bar
    monkeypatch.setattr(status_bar, 'resolve_height', lambda config, panel=None: 64)
    assert mod._cutout_inset(object()) == 64
    monkeypatch.setattr(status_bar, 'resolve_height',
                        lambda config, panel=None: 1 / 0)
    assert mod._cutout_inset(object()) == 0
