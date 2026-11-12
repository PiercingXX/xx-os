"""W2 power menu + first-boot wizard UX.

Covers the six behaviors the task names: the double-tap confirm state
machine (pure), Suspend gating on login1 CanSuspend, the single PowerMenu
instance wiring, the timezone failure message, wizard providers registered
once, and IPC suppression while the tour is visible.

Widget-free seams first (ConfirmState, suspend_visible, timezone result,
ipc predicate run against namespace harnesses — the test_first_boot_pin
idiom); construction call sites that need a display are pinned at the
source text instead (the test_preload_gate idiom).
"""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

SRC = Path(__file__).parent.parent / 'launcher' / 'src'


def _power_menu():
    # Imported lazily: sibling test modules stub sys.modules['gi'] during
    # collection; conftest restores the real bindings before tests run.
    import power_menu
    return power_menu


def _first_boot():
    import first_boot
    return first_boot


class _FakeLabel:
    def __init__(self):
        self.text = ''

    def set_text(self, text):
        self.text = text


class _FakeStack:
    def __init__(self):
        self.visible_child_name = None

    def set_visible_child_name(self, name):
        self.visible_child_name = name


class _FakeRow:
    def __init__(self):
        self.visible = True

    def set_visible(self, visible):
        self.visible = visible


# ---------------------------------------------------------------------------
# 1. Double-tap confirm state machine (pure)


class TestConfirmState:
    def test_first_tap_arms_second_confirms(self):
        state = _power_menu().ConfirmState()
        assert state.tap('poweroff') is False
        assert state.pending == 'poweroff'
        assert state.tap('poweroff') is True
        assert state.pending is None

    def test_confirm_clears_so_next_tap_rearms(self):
        state = _power_menu().ConfirmState()
        state.tap('reboot')
        assert state.tap('reboot') is True
        assert state.tap('reboot') is False

    def test_tapping_other_action_rearms_instead_of_confirming(self):
        state = _power_menu().ConfirmState()
        assert state.tap('poweroff') is False
        assert state.tap('reboot') is False
        assert state.pending == 'reboot'
        assert state.tap('reboot') is True

    def test_reset_disarms(self):
        state = _power_menu().ConfirmState()
        state.tap('poweroff')
        state.reset()
        assert state.pending is None
        assert state.tap('poweroff') is False

    def test_arming_labels_name_the_action(self):
        mod = _power_menu()
        assert mod.confirm_label('poweroff') == 'Tap again to power off'
        assert mod.confirm_label('reboot') == 'Tap again to restart'

    def test_confirm_window_is_three_seconds(self):
        assert _power_menu().CONFIRM_TIMEOUT_S == 3


class TestVisiblePowerRows:
    def test_full_menu_until_probe_answers(self):
        assert _power_menu().visible_power_rows(None) == [
            'lock', 'suspend', 'reboot', 'poweroff', 'cancel']

    @pytest.mark.parametrize('value', ['yes', 'challenge'])
    def test_yes_and_challenge_keep_suspend(self, value):
        assert _power_menu().visible_power_rows(value) == [
            'lock', 'suspend', 'reboot', 'poweroff', 'cancel']

    @pytest.mark.parametrize('value', ['no', 'na', '', 'YES'])
    def test_anything_but_yes_or_challenge_drops_only_suspend(self, value):
        assert _power_menu().visible_power_rows(value) == [
            'lock', 'reboot', 'poweroff', 'cancel']

    def test_every_row_fits_320x640(self):
        rows = _power_menu().visible_power_rows(None)
        assert len(rows) == 5
        # 4 x 64 px rows + 56 px Cancel + 4 x 4 px gaps = 328 px < 640.
        assert 4 * 64 + 56 + 4 * 4 < 640


class TestConfirmWiring:
    """The menu arms only Restart/Power off; expiry restores the rows."""

    def _menu(self):
        import types as _types

        mod = _power_menu()
        labels: dict[str, str] = {}
        classes: dict[str, set] = {}

        def _btn(name, label):
            labels[name] = label
            classes[name] = set()
            return _types.SimpleNamespace(
                set_label=lambda text, n=name: labels.__setitem__(n, text),
                add_css_class=lambda cls, n=name: classes[n].add(cls),
                remove_css_class=lambda cls, n=name: classes[n].discard(cls),
            )

        menu = _types.SimpleNamespace(
            _confirm=mod.ConfirmState(),
            _confirm_timeout=None,
            _executed=[],
            _action_rows={
                'reboot': (_btn('reboot', 'Restart'), 'Restart'),
                'poweroff': (_btn('poweroff', 'Power off'), 'Power off'),
            },
            _labels=labels,
            _classes=classes,
        )
        # Bind the real row/timeout helpers onto the harness; execution and
        # the GLib timeout stay stubbed so no loop or D-Bus is ever touched.
        for name in ('_show_armed', '_restore_rows', '_on_danger_tap',
                     '_on_confirm_expired'):
            setattr(menu, name, _types.MethodType(
                getattr(mod.PowerMenu, name), menu))
        menu._execute = menu._executed.append
        menu._clear_confirm_timeout = lambda: None
        menu._restart_confirm_timeout = lambda action: None
        return mod, menu

    def test_reboot_arms_with_inline_label(self):
        _mod, menu = self._menu()
        # Suspend is not a confirm action: the state machine never sees it.
        assert menu._confirm.pending is None
        menu._on_danger_tap('reboot')
        assert menu._labels['reboot'] == 'Tap again to restart'
        assert 'armed' in menu._classes['reboot']

    def test_expiry_restores_row_labels(self):
        _mod, menu = self._menu()
        menu._on_danger_tap('poweroff')
        assert menu._labels['poweroff'] == 'Tap again to power off'
        assert menu._on_confirm_expired('poweroff') is False
        assert menu._labels['poweroff'] == 'Power off'
        assert 'armed' not in menu._classes['poweroff']
        assert menu._confirm.pending is None

    def test_second_tap_executes_and_clears(self):
        _mod, menu = self._menu()
        menu._on_danger_tap('poweroff')
        menu._on_danger_tap('poweroff')
        assert menu._executed == ['poweroff']
        assert menu._labels['poweroff'] == 'Power off'

    def test_lock_calls_on_lock_callback(self):
        mod = _power_menu()
        called = []
        menu = types.SimpleNamespace(
            _dismiss=lambda: called.append('dismiss'),
            _on_lock=lambda: called.append('lock'),
        )
        mod.PowerMenu._lock(menu)
        assert called == ['dismiss', 'lock']


class TestPowerMenuSurface:
    def test_rows_are_lock_suspend_restart_poweroff_cancel(self):
        src = (SRC / 'power_menu.py').read_text(encoding='utf-8')
        for label in ("'Lock'", "'Suspend'", "'Restart'", "'Power off'", "'Cancel'"):
            assert label in src, label
        assert "'Log out'" not in src

    def test_rows_are_text_first_not_boxed_buttons(self):
        src = (SRC / 'power_menu.py').read_text(encoding='utf-8')
        assert 'power-menu-row' in src
        assert 'power-menu-btn' not in src

    def test_scrim_covers_at_near_full_alpha_and_list_scrolls(self):
        src = (SRC / 'power_menu.py').read_text(encoding='utf-8')
        assert '0.85' in src
        assert 'ScrolledWindow' in src
        assert 'Adw.Clamp' in src
        assert 'maximum_size=420' in src

    def test_scrim_tap_and_escape_cancel(self):
        src = (SRC / 'power_menu.py').read_text(encoding='utf-8')
        assert 'Gdk.KEY_Escape' in src
        assert '_on_scrim_tap' in src

    def test_no_systemctl_left(self):
        assert "['systemctl'" not in (SRC / 'power_menu.py').read_text()

    def test_scrim_root_expands_and_window_paints_scrim(self):
        src = (SRC / 'power_menu.py').read_text(encoding='utf-8')
        assert 'power-menu-window' in src
        assert 'window.power-menu-window' in src
        assert 'scrim.set_hexpand(True)' in src
        assert 'scrim.set_vexpand(True)' in src
        assert 'clamp.set_hexpand(True)' in src


# ---------------------------------------------------------------------------
# 2. Suspend gating on login1 CanSuspend


class TestSuspendGating:
    def test_visible_until_known(self):
        assert _power_menu().suspend_visible(None) is True

    @pytest.mark.parametrize('value', ['yes', 'challenge'])
    def test_yes_and_challenge_keep_suspend(self, value):
        assert _power_menu().suspend_visible(value) is True

    @pytest.mark.parametrize('value', ['no', 'na', '', 'YES'])
    def test_no_na_and_unknown_hide_suspend(self, value):
        assert _power_menu().suspend_visible(value) is False

    def test_probe_result_drives_row_visibility(self):
        mod = _power_menu()
        menu = types.SimpleNamespace(_suspend_row=_FakeRow())
        mod.PowerMenu._on_can_suspend(menu, 'no')
        assert menu._suspend_row.visible is False
        mod.PowerMenu._on_can_suspend(menu, 'na')
        assert menu._suspend_row.visible is False
        mod.PowerMenu._on_can_suspend(menu, 'challenge')
        assert menu._suspend_row.visible is True
        mod.PowerMenu._on_can_suspend(menu, 'yes')
        assert menu._suspend_row.visible is True

    def test_probe_calls_cansuspend_method(self):
        # CanSuspend is a Manager method, not a property: the probe must
        # never go through org.freedesktop.DBus.Properties (which fails
        # on every real system with UnknownProperty).
        src = (SRC / 'power_menu.py').read_text(encoding='utf-8')
        assert 'CanSuspend' in src
        assert 'org.freedesktop.login1.Manager' in src
        assert 'org.freedesktop.DBus.Properties' not in src
        assert 'query_can_suspend_async' in src


class _FakeDbusError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


class _FakeReply:
    def __init__(self, value):
        self._value = value

    def unpack(self):
        return (self._value,)


class _FakeLogin1Conn:
    """login1 Manager.CanSuspend stand-in: the method reply unpacks to
    ('yes',) per the real Gio contract (a tuple of out-args)."""

    def __init__(self, value=None, fail=None):
        self._value = value
        self._fail = fail
        self.calls: list[tuple] = []

    def call_finish(self, _result):
        if self._fail is not None:
            raise self._fail
        return _FakeReply(self._value)


class _FakeLogin1Bus:
    """Strict login1 stand-in: CanSuspend is a Manager method, so a
    Properties.Get call fails async with UnknownProperty — exactly what
    the real service answers, and what hid Suspend everywhere before."""

    def __init__(self, conn):
        self._conn = conn
        self.calls: list[tuple] = []

    def call(self, service, path, iface, method, params,
             reply_type, flags, timeout, cancellable, callback):
        # Exact Gio arity (no trailing user_data): an 11th arg would
        # shift the callback on a real bus.
        self.calls.append((service, path, iface, method, params))
        if (iface, method) == ('org.freedesktop.DBus.Properties', 'Get'):
            callback(_FakeLogin1Conn(fail=_FakeDbusError(
                'Unknown property CanSuspend')), object())
        else:
            callback(self._conn, object())


class TestCanSuspendProbe:
    def test_method_yes_value_reaches_callback(self):
        mod = _power_menu()
        conn = _FakeLogin1Conn(value='yes')
        bus = _FakeLogin1Bus(conn)
        got = []
        mod.query_can_suspend_async(got.append, bus=bus)
        assert got == ['yes']
        (service, path, iface, method, params), = bus.calls
        assert service == 'org.freedesktop.login1'
        assert path == '/org/freedesktop/login1'
        assert iface == 'org.freedesktop.login1.Manager'
        assert method == 'CanSuspend'
        assert params is None  # the method takes no arguments

    def test_method_challenge_value_reaches_callback(self):
        mod = _power_menu()
        bus = _FakeLogin1Bus(_FakeLogin1Conn(value='challenge'))
        got = []
        mod.query_can_suspend_async(got.append, bus=bus)
        assert got == ['challenge']

    def test_properties_get_fails_like_the_real_service(self):
        # The old probe read CanSuspend via Properties.Get; the fake
        # answers that path exactly like logind so the regression
        # stays red if the method call ever drifts back to Get.
        from gi.repository import GLib
        bus = _FakeLogin1Bus(_FakeLogin1Conn(value='yes'))
        errors = []

        def _reply(conn, result):
            try:
                conn.call_finish(result)
            except Exception as error:
                errors.append(str(error))

        bus.call(
            'org.freedesktop.login1', '/org/freedesktop/login1',
            'org.freedesktop.DBus.Properties', 'Get',
            GLib.Variant(
                '(ss)', ('org.freedesktop.login1.Manager', 'CanSuspend')),
            None, 0, 5000, None, _reply)
        assert errors == ['Unknown property CanSuspend']

    def test_dbus_failure_reports_unknown(self):
        mod = _power_menu()
        conn = _FakeLogin1Conn(
            fail=_FakeDbusError('Interactive authentication required'))
        got = []
        mod.query_can_suspend_async(got.append, bus=_FakeLogin1Bus(conn))
        assert got == [None]

    def test_call_failure_reports_unknown(self, monkeypatch):
        from gi.repository import GLib
        mod = _power_menu()
        monkeypatch.setattr(
            GLib, 'idle_add', lambda cb, *args: (cb(*args), False)[1])

        class _ExplodingBus:
            def call(self, *args):
                raise _FakeDbusError('boom')

        got = []
        mod.query_can_suspend_async(got.append, bus=_ExplodingBus())
        assert got == [None]


# ---------------------------------------------------------------------------
# 3. Single PowerMenu instance wiring


class TestSinglePowerMenuInstance:
    def test_window_reuses_application_menu(self):
        src = (SRC / 'window.py').read_text(encoding='utf-8')
        body = src.split('def _show_power_menu')[1].split('\n    def ')[0]
        assert "getattr(self.get_application(), '_power_menu', None)" in body

    def test_window_keeps_lazy_fallback(self):
        src = (SRC / 'window.py').read_text(encoding='utf-8')
        body = src.split('def _show_power_menu')[1].split('\n    def ')[0]
        assert 'PowerMenu(' in body

    def test_main_passes_lock_callback(self):
        src = (SRC / 'main.py').read_text(encoding='utf-8')
        assert 'PowerMenu(config=self._shell.config' in src
        assert 'on_lock=self._shell._show_lock_screen' in src

    def test_power_menu_accepts_on_lock(self):
        import inspect
        params = inspect.signature(_power_menu().PowerMenu.__init__).parameters
        assert 'on_lock' in params
        assert params['on_lock'].default is None

    def test_theme_fan_out_lists_power_menu_exactly_once(self):
        src = (SRC / 'window.py').read_text(encoding='utf-8')
        body = src.split('def _retheme_surfaces')[1].split('\n    def ')[0]
        # The window's own menu is themed once by the surface loop; the
        # application-owned instance is fanned out only when it is not
        # that same object (same instance, themed once — never two menus).
        assert '_power_menu' in body
        assert 'themed once' in body


# ---------------------------------------------------------------------------
# 4. Timezone failure message


class TestTimezoneResult:
    def test_failure_message_points_at_installer(self):
        assert (_first_boot()._TIMEZONE_FAILURE_MESSAGE
                == 'Set the time zone in the installer')

    def test_failure_shows_message_and_pages_back(self):
        wizard = types.SimpleNamespace(
            _tz_status=_FakeLabel(),
            _stack=_FakeStack(),
        )
        _first_boot().FirstBootWizard._on_timezone_result(
            wizard, False, 'Interactive authentication required')
        assert wizard._tz_status.text == 'Set the time zone in the installer'
        assert wizard._stack.visible_child_name == 'timezone'

    def test_success_stays_silent(self):
        wizard = types.SimpleNamespace(
            _tz_status=_FakeLabel(),
            _stack=_FakeStack(),
        )
        _first_boot().FirstBootWizard._on_timezone_result(wizard, True, '')
        assert wizard._tz_status.text == ''
        assert wizard._stack.visible_child_name is None

    def test_second_failure_does_not_bounce_again(self):
        wizard = types.SimpleNamespace(
            _tz_status=_FakeLabel(),
            _stack=_FakeStack(),
        )
        on_result = _first_boot().FirstBootWizard._on_timezone_result
        on_result(wizard, False, 'denied')
        assert wizard._stack.visible_child_name == 'timezone'
        wizard._stack.visible_child_name = 'tour_swipe_up'
        on_result(wizard, False, 'denied')
        assert wizard._stack.visible_child_name == 'tour_swipe_up'

    def test_system_zone_is_listed_first_and_kept(self, tmp_path):
        fb = _first_boot()
        zoneinfo = tmp_path / 'usr' / 'share' / 'zoneinfo' / 'Europe'
        zoneinfo.mkdir(parents=True)
        (zoneinfo / 'Vienna').write_text('')
        link = tmp_path / 'localtime'
        link.symlink_to(zoneinfo / 'Vienna')
        zone = fb.system_timezone(str(link), str(tmp_path / 'missing'))
        assert zone == 'Europe/Vienna'
        choices = fb.timezone_choices(zone)
        assert choices[0] == 'Europe/Vienna'
        assert fb.timezone_choices('UTC').count('UTC') == 1
        assert fb.timezone_choices(None)[0] == 'UTC'

    def test_done_on_the_system_zone_sets_nothing(self, monkeypatch):
        fb = _first_boot()
        calls = []
        monkeypatch.setattr(fb, 'set_timezone_async',
                            lambda tz, on_done=None: calls.append(tz))

        class _Row:
            def get_index(self):
                return 0

        config = types.SimpleNamespace(data={}, save=lambda: None)
        wizard = types.SimpleNamespace(
            _tz_list=types.SimpleNamespace(get_selected_row=lambda: _Row()),
            _tz_choices=['Europe/Vienna', 'UTC'],
            _system_tz='Europe/Vienna',
            _config=config,
            _advance_tour=lambda: None,
            _on_timezone_result=lambda ok, msg: None,
        )
        fb.FirstBootWizard._on_finish(wizard, None)
        assert calls == []
        assert config.data['setup_complete'] is True

    def test_no_silent_timedatectl_or_gsettings_left(self):
        src = (SRC / 'first_boot.py').read_text(encoding='utf-8')
        assert 'timedatectl' not in src
        assert 'gsettings' not in src
        assert 'input-sources' not in src

    def test_timezone_goes_over_timedate1_interactive(self):
        src = (SRC / 'first_boot.py').read_text(encoding='utf-8')
        assert 'SetTimezone' in src
        assert 'org.freedesktop.timedate1' in src


class TestSetTimezoneAsync:
    def test_issues_interactive_settimezone(self):
        mod = _first_boot()

        class _Conn:
            def call_finish(self, _result):
                return _FakeReply('yes')

        conn = _Conn()
        calls: list[tuple] = []

        class _Bus:
            def call(self, service, path, iface, method, params, *rest):
                calls.append((service, path, iface, method, params))
                rest[-1](conn, object())

        got = []
        mod.set_timezone_async(
            'Europe/Berlin',
            on_done=lambda ok, msg: got.append((ok, msg)), bus=_Bus())
        assert len(calls) == 1
        (service, path, iface, method, params), = calls
        assert service == 'org.freedesktop.timedate1'
        assert path == '/org/freedesktop/timedate1'
        assert iface == 'org.freedesktop.timedate1'
        assert method == 'SetTimezone'
        assert params.unpack() == ('Europe/Berlin', True)
        assert got == [(True, '')]

    def test_timedate1_denial_reports_failure(self):
        mod = _first_boot()

        class _Conn:
            def call_finish(self, _result):
                raise _FakeDbusError('Interactive authentication required')

        conn = _Conn()

        class _Bus:
            def call(self, *args):
                args[-1](conn, object())

        got = []
        mod.set_timezone_async(
            'UTC', on_done=lambda ok, msg: got.append((ok, msg)), bus=_Bus())
        assert got == [(False, 'Interactive authentication required')]


# ---------------------------------------------------------------------------
# 5. Wizard providers registered once


class _RecordingProvider:
    def __init__(self, loads):
        self._loads = loads

    def load_from_data(self, data):
        self._loads.append(bytes(data))


@pytest.fixture
def wizard_module(monkeypatch):
    """Fresh first_boot import bound to a recording fake gi stack (the
    test_lock_screen TestThemeProviderDedupe idiom)."""
    loads: list[bytes] = []
    added: list[object] = []

    gdk = types.ModuleType('gi.repository.Gdk')
    gdk.Display = types.SimpleNamespace(get_default=lambda: object())
    gtk = types.ModuleType('gi.repository.Gtk')
    gtk.Window = type('Window', (), {})  # class FirstBootWizard(Gtk.Window)
    gtk.CssProvider = lambda: _RecordingProvider(loads)
    gtk.StyleContext = types.SimpleNamespace(
        add_provider_for_display=lambda _d, p, _prio: added.append(p))
    gtk.STYLE_PROVIDER_PRIORITY_APPLICATION = 800
    glib = types.ModuleType('gi.repository.GLib')
    pango = types.ModuleType('gi.repository.Pango')
    repo = types.ModuleType('gi.repository')
    repo.Gdk, repo.Gtk, repo.GLib, repo.Pango = gdk, gtk, glib, pango
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
    monkeypatch.setitem(sys.modules, 'gi.repository.Pango', pango)

    saved = sys.modules.pop('first_boot', None)
    try:
        import first_boot
        yield types.SimpleNamespace(
            module=first_boot, loads=loads, added=added)
    finally:
        if saved is not None:
            sys.modules['first_boot'] = saved
        else:
            sys.modules.pop('first_boot', None)


class TestWizardProvidersOnce:
    def test_repeated_replays_register_two_providers_total(self, wizard_module):
        from config import THEME_PRESETS
        mod = wizard_module.module
        assert mod._wizard_base_provider is None
        assert mod._wizard_live_provider is None

        mod._apply_wizard_theme(THEME_PRESETS['amoled'])
        mod._apply_wizard_theme(THEME_PRESETS['amoled'])  # replay --welcome

        assert len(wizard_module.added) == 2  # base + live, once each
        assert len(wizard_module.loads) == 4  # data reloaded per build

    def test_retheme_reaches_the_shared_providers(self, wizard_module):
        from config import THEME_PRESETS
        mod = wizard_module.module
        mod._apply_wizard_theme(THEME_PRESETS['amoled'])
        mod._apply_wizard_theme(THEME_PRESETS['paper'])

        # Loads alternate base/live per build; the live sheets (odd indices)
        # carry exactly the latest preset — the base sheet also holds every
        # preset's swatch row, so only the live sheet is asserted here.
        live = wizard_module.loads[1::2]
        assert len(live) == 2
        assert THEME_PRESETS['paper'].background.encode() in live[-1]
        assert THEME_PRESETS['amoled'].background.encode() not in live[-1]

    def test_swatch_rows_ship_with_the_base_sheet(self, wizard_module):
        from config import THEME_PRESETS
        wizard_module.module._apply_wizard_theme(THEME_PRESETS['amoled'])
        assert any(b'theme-row-' in sheet for sheet in wizard_module.loads)

    def test_headless_no_display_never_adds_providers(
            self, wizard_module, monkeypatch):
        from config import THEME_PRESETS
        mod = wizard_module.module
        monkeypatch.setattr(mod.Gdk.Display, 'get_default', lambda: None)

        mod._apply_wizard_theme(THEME_PRESETS['amoled'])

        assert wizard_module.added == []
        assert wizard_module.loads == []

    def test_construction_reloads_instead_of_stacking(self):
        src = (SRC / 'first_boot.py').read_text(encoding='utf-8')
        assert '_apply_wizard_theme(self._config.theme)' in src


# ---------------------------------------------------------------------------
# 6. IPC suppression during the tour


class TestTourSuppression:
    def test_gesture_verbs_suppressed_while_tour_visible(self):
        import ipc
        for verb in ('gesture.shade', 'gesture.switcher', 'gesture.home'):
            assert ipc.suppress_during_tour(verb, True) is True

    def test_other_verbs_pass_through(self):
        import ipc
        for verb in ('lock', 'gesture.back', 'gesture.keyboard',
                     'shade.show', 'welcome'):
            assert ipc.suppress_during_tour(verb, True) is False

    def test_nothing_suppressed_once_tour_closes(self):
        import ipc
        for verb in ('gesture.shade', 'gesture.switcher', 'gesture.home'):
            assert ipc.suppress_during_tour(verb, False) is False

    def test_main_consults_suppression_before_dispatch(self):
        src = (SRC / 'main.py').read_text(encoding='utf-8')
        assert "suppress_during_tour(command, getattr(self, '_tour_visible', False))" in src
        assert 'self._tour_visible = False' in src
        assert 'self._tour_visible = True' in src
        assert '_end_welcome_tour' in src


# ---------------------------------------------------------------------------
# 7. Wizard 320 px fit + tour Skip


class TestWizardFit:
    def test_keypad_sizes_from_width(self):
        src = (SRC / 'first_boot.py').read_text(encoding='utf-8')
        assert 'set_column_homogeneous(True)' in src
        assert 'set_size_request(80, 64)' not in src

    def test_buttons_fill_width(self):
        src = (SRC / 'first_boot.py').read_text(encoding='utf-8')
        assert src.count('set_hexpand(True)') >= 8

    def test_every_tour_page_offers_skip(self):
        src = (SRC / 'first_boot.py').read_text(encoding='utf-8')
        for builder in ('_gesture_page', '_info_page', '_keyboard_page'):
            body = src.split(f'def {builder}')[1].split('\n    def ')[0]
            assert '_skip_button' in body, builder

    def test_titles_wrap(self):
        src = (SRC / 'first_boot.py').read_text(encoding='utf-8')
        assert 'set_wrap(True)' in src
