"""Hot-reload fan-out + custom theme resolution (W2-Y).

Editing ``~/.config/xx-wm/config.json`` is the advertised workflow
(design.md "Themes": theme changes apply instantly; todo.md hot-reload
item). The shell window owns the live ``ShellConfig`` and must propagate
every reload past its own ``.shell-root``: the overlay surfaces (shade,
call UI/bar, dialer, SMS view, power menu) draw outside ``.shell-root``
with their own display-level providers, so ``window._reload_config`` fans
the freshly resolved preset out to each constructed surface.

``theme == 'custom'`` resolves to a preset built from the stored
``custom_background`` color (docs/config.md "Appearance"); garbage colors
silently fall back to the default preset (config-compat invariant).

Headless seams: ``window.py`` is imported once against a minimal fake gi
stack (test_restore_dialog idiom); the surface modules are imported
against shared fake gi at module import time (test_call_ui_flow idiom);
surface instances are built with ``object.__new__`` plus a recording CSS
provider so ``apply_theme`` is exercised without constructing widgets.
"""
import inspect
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from config import ShellConfig, ThemePreset, THEME_PRESETS  # noqa: E402


def _install_fake_gi() -> None:
    """Minimal gi.repository stand-ins covering every import-time reference
    of window.py and the five surface modules (plus their import chains:
    quick_actions/als_brightness/app_index/app_item_actions/contacts)."""
    glib = types.ModuleType('gi.repository.GLib')
    glib.SOURCE_REMOVE = False
    glib.SOURCE_CONTINUE = True
    glib.Error = type('Error', (Exception,), {})
    glib.idle_add = lambda *a, **k: 1
    glib.timeout_add = lambda *a, **k: 1
    glib.timeout_add_seconds = lambda *a, **k: 1
    glib.source_remove = lambda *a, **k: None

    gdk = types.ModuleType('gi.repository.Gdk')
    gdk.Display = types.SimpleNamespace(get_default=lambda: None)

    gio = types.ModuleType('gi.repository.Gio')

    gtk = types.ModuleType('gi.repository.Gtk')
    # Class-definition-time base classes only; widgets are never built.
    gtk.Window = type('Window', (), {})
    gtk.Box = type('Box', (), {})
    gtk.Editable = type('Editable', (), {})
    gtk.PickFlags = types.SimpleNamespace(DEFAULT=0)

    glib.Variant = lambda sig, value: types.SimpleNamespace(signature=sig, value=value)

    adw = types.ModuleType('gi.repository.Adw')
    adw.ApplicationWindow = type('ApplicationWindow', (), {})
    adw.StyleManager = types.SimpleNamespace(
        get_default=lambda: types.SimpleNamespace(set_color_scheme=lambda scheme: None))
    adw.ColorScheme = types.SimpleNamespace(FORCE_DARK=1, FORCE_LIGHT=2)

    pango = types.ModuleType('gi.repository.Pango')

    def _require_version(namespace, version):
        # window/notification_shade/power_menu probe Gtk4LayerShell and
        # treat ValueError as 'absent'.
        if namespace == 'Gtk4LayerShell':
            raise ValueError(f'{namespace} not available')

    gi_mod = types.ModuleType('gi')
    gi_rep = types.ModuleType('gi.repository')
    gi_mod.require_version = _require_version
    gi_mod.repository = gi_rep
    sys.modules['gi'] = gi_mod
    sys.modules['gi.repository'] = gi_rep
    for name, mod in (('GLib', glib), ('Gdk', gdk), ('Gio', gio),
                      ('Gtk', gtk), ('Adw', adw), ('Pango', pango)):
        sys.modules[f'gi.repository.{name}'] = mod


_install_fake_gi()

import window  # noqa: E402  (needs fake gi modules installed first)
import call_ui  # noqa: E402
import dialer  # noqa: E402
import notification_shade  # noqa: E402
import power_menu  # noqa: E402
import sms  # noqa: E402

# Leave the module cache as we found it: later test files (e.g.
# test_keyboard_dismiss) import window under their own fake gi and rely on
# getting a freshly executed module whose gi globals match their stubs.
# Our local `window` name keeps referencing the module built above.
sys.modules.pop('window', None)


def _config_with(**overrides) -> ShellConfig:
    """ShellConfig stripped of host-config influence: data is fully replaced
    so every property below resolves from exactly the given overrides."""
    cfg = ShellConfig.__new__(ShellConfig)
    cfg.data = dict(overrides)
    return cfg


class FakeProvider:
    """Records the last sheet handed to load_from_data (bytes like GTK)."""

    def __init__(self) -> None:
        self.data: bytes | None = None

    def load_from_data(self, data) -> None:
        self.data = bytes(data)


class RecordingSurface:
    def __init__(self) -> None:
        self.applied: list[ThemePreset] = []

    def apply_theme(self, preset) -> None:
        self.applied.append(preset)


def _hex_sum(color: str) -> int:
    return sum(int(color[i:i + 2], 16) for i in (1, 3, 5))


class TestResolveTheme:
    def test_known_preset_passes_through(self):
        assert window.resolve_theme(
            _config_with(theme='forest')) is THEME_PRESETS['forest']

    def test_unknown_key_falls_back_to_default(self):
        assert window.resolve_theme(
            _config_with(theme='no-such-theme')) is THEME_PRESETS['amoled']

    def test_custom_resolves_stored_color_as_background(self):
        preset = window.resolve_theme(
            _config_with(theme='custom', custom_background='#2A1018'))
        assert isinstance(preset, ThemePreset)
        assert preset.key == 'custom'
        assert preset.background == '#2A1018'

    def test_custom_accepts_lowercase_hex(self):
        preset = window.resolve_theme(
            _config_with(theme='custom', custom_background='#2a1018'))
        assert preset.background == '#2a1018'

    def test_custom_dark_background_takes_amoled_text_family(self):
        preset = window.resolve_theme(
            _config_with(theme='custom', custom_background='#101010'))
        for field in ('foreground', 'muted', 'accent'):
            assert getattr(preset, field) == getattr(THEME_PRESETS['amoled'], field)

    def test_custom_light_background_takes_paper_text_family(self):
        preset = window.resolve_theme(
            _config_with(theme='custom', custom_background='#F3EEE2'))
        for field in ('foreground', 'muted', 'accent'):
            assert getattr(preset, field) == getattr(THEME_PRESETS['paper'], field)

    def test_custom_shades_derive_darker_than_background(self):
        preset = window.resolve_theme(
            _config_with(theme='custom', custom_background='#2A1018'))
        assert preset.surface != preset.background
        assert preset.surface_alt != preset.surface
        assert _hex_sum(preset.border) < _hex_sum(preset.surface_alt)
        assert _hex_sum(preset.surface_alt) < _hex_sum(preset.surface)
        assert _hex_sum(preset.surface) < _hex_sum(preset.background)

    def test_custom_shades_preserve_hue(self):
        # Red-channel-only derivation collapsed tinted backgrounds to gray.
        preset = window.resolve_theme(
            _config_with(theme='custom', custom_background='#001030'))
        for field in ('surface', 'surface_alt', 'border'):
            hexval = getattr(preset, field)
            r, g, b = (int(hexval[i:i + 2], 16) for i in (1, 3, 5))
            assert b > g > r, f'{field} lost the blue tint: {hexval}'

    def test_near_black_custom_gets_amoled_separation(self):
        # Pure black has no headroom to darken into; amoled's trio is the
        # canonical separation for that regime.
        preset = window.resolve_theme(
            _config_with(theme='custom', custom_background='#000000'))
        assert (preset.surface, preset.surface_alt, preset.border) == (
            THEME_PRESETS['amoled'].surface,
            THEME_PRESETS['amoled'].surface_alt,
            THEME_PRESETS['amoled'].border)

    @pytest.mark.parametrize(
        'bad',
        [None, '', 'nothex', '#12345', '#1234567', '123456', '#GGHHII',
         {'r': 1}, 32768, '#2A1018\n', '\n#2A1018'],
    )
    def test_custom_garbage_color_silently_falls_back_to_default(self, bad):
        preset = window.resolve_theme(
            _config_with(theme='custom', custom_background=bad))
        assert preset is THEME_PRESETS['amoled']

    def test_custom_without_color_key_falls_back_to_default(self):
        assert window.resolve_theme(
            _config_with(theme='custom')) is THEME_PRESETS['amoled']


class TestConstructorBackCompat:
    """Every surface stays constructible without a config argument: the new
    parameter is optional and the no-arg call still binds."""

    def test_config_param_is_optional_everywhere(self):
        for cls in (call_ui.CallUI, call_ui.CallBar, dialer.Dialer,
                    notification_shade.NotificationShade, power_menu.PowerMenu,
                    sms.SMSConversation):
            params = inspect.signature(cls.__init__).parameters
            assert 'config' in params, cls.__name__
            assert params['config'].default is None, cls.__name__

    def test_surfaces_bind_with_no_args(self):
        # bind() validates a no-arg call against the real signature without
        # constructing GTK widgets (impossible headlessly).
        for cls in (call_ui.CallUI, dialer.Dialer,
                    notification_shade.NotificationShade, power_menu.PowerMenu,
                    sms.SMSConversation):
            inspect.signature(cls).bind()
        inspect.signature(call_ui.CallBar).bind(on_expand=lambda: None)


class TestSurfaceApplyTheme:
    def test_explicit_preset_lands_in_the_provider(self):
        surf = sms.SMSConversation.__new__(sms.SMSConversation)
        surf._config = _config_with(theme='amoled')
        surf._theme_provider = FakeProvider()
        surf.apply_theme(THEME_PRESETS['forest'])
        assert b'#10261B' in surf._theme_provider.data

    def test_no_arg_falls_back_to_injected_config_preset(self):
        surf = dialer.Dialer.__new__(dialer.Dialer)
        surf._config = _config_with(theme='ocean')
        surf._theme_provider = FakeProvider()
        surf.apply_theme()
        assert b'#0F1C2E' in surf._theme_provider.data

    @pytest.mark.parametrize('module,cls_name', [
        (notification_shade, 'NotificationShade'),
        (call_ui, 'CallUI'),
        (call_ui, 'CallBar'),
        (dialer, 'Dialer'),
        (sms, 'SMSConversation'),
        (power_menu, 'PowerMenu'),
    ])
    def test_every_surface_has_apply_theme_idiom(self, module, cls_name):
        cls = getattr(module, cls_name)
        surf = cls.__new__(cls)
        surf._config = _config_with(theme='mist')
        surf._theme_provider = FakeProvider()
        surf.apply_theme(THEME_PRESETS['mist'])
        assert b'#E6EDF5' in surf._theme_provider.data


class TestReloadFanOut:
    def test_retheme_reaches_every_constructed_surface(self):
        shade, dialer_s, call_ui_s, call_bar, menu = (
            RecordingSurface() for _ in range(5))
        switcher, lock, back = (RecordingSurface() for _ in range(3))
        hud, app_menu = RecordingSurface(), RecordingSurface()
        app = types.SimpleNamespace(_hud=hud, _power_menu=app_menu)
        shell = types.SimpleNamespace(
            config=_config_with(theme='custom', custom_background='#2A1018'),
            _shade=shade, _dialer=dialer_s, _call_ui=call_ui_s,
            _call_bar=call_bar, _power_menu=menu,
            _switcher=switcher, _lock_screen=lock, _back_layer=back,
            get_application=lambda: app,
        )
        window.ShellWindow._retheme_surfaces(shell)
        expected = window.resolve_theme(shell.config)
        assert expected.background == '#2A1018'
        for surf in (shade, dialer_s, call_ui_s, call_bar, menu,
                     switcher, lock, back, hud, app_menu):
            assert surf.applied == [expected]

    def test_retheme_skips_surfaces_never_constructed(self):
        shell = types.SimpleNamespace(
            config=_config_with(theme='ocean'),
            _shade=None, _dialer=None, _call_ui=None,
            _call_bar=None, _power_menu=None,
            _switcher=None, _lock_screen=None, _back_layer=None,
            get_application=lambda: None,
        )
        # Must be a silent no-op, never an AttributeError.
        window.ShellWindow._retheme_surfaces(shell)

    def test_retheme_reaches_hud_and_app_power_menu(self):
        hud, app_menu, win_menu = (RecordingSurface() for _ in range(3))
        app = types.SimpleNamespace(_hud=hud, _power_menu=app_menu)
        shell = types.SimpleNamespace(
            config=_config_with(theme='paper'),
            _shade=None, _dialer=None, _call_ui=None,
            _call_bar=None, _power_menu=win_menu,
            get_application=lambda: app,
        )
        window.ShellWindow._retheme_surfaces(shell)
        expected = window.resolve_theme(shell.config)
        assert hud.applied == [expected]
        assert app_menu.applied == [expected]
        assert win_menu.applied == [expected]

    def test_shade_apply_theme_fans_to_quick_actions(self):
        qa = RecordingSurface()
        surf = notification_shade.NotificationShade.__new__(
            notification_shade.NotificationShade)
        surf._config = _config_with(theme='mist')
        surf._theme_provider = FakeProvider()
        surf.quick_actions = qa
        surf.apply_theme(THEME_PRESETS['mist'])
        assert qa.applied == [THEME_PRESETS['mist']]

    def test_apply_config_change_ends_with_surface_fan_out(self):
        events = []
        shell = object.__new__(window.ShellWindow)
        shell.config = _config_with(theme='paper')
        shell._apply_theme = lambda preset=None: events.append('theme')
        shell._build_widget_block = lambda: events.append('widgets')
        shell._home_launcher = types.SimpleNamespace(
            refresh=lambda preserve_folder=False: events.append('home'))
        shell._populate_apps = lambda query: events.append('apps')
        shell.apps_search = types.SimpleNamespace(get_text=lambda: '')
        shell._refresh_weather = lambda: events.append('weather')
        shell._resync_blank_lock = lambda: events.append('blank-lock')
        shell._retheme_surfaces = lambda: events.append('surfaces')
        window.ShellWindow._apply_config_change(shell)
        assert events == ['theme', 'widgets', 'home', 'apps', 'weather',
                          'blank-lock', 'surfaces']


class TestWindowUsesResolvedPreset:
    def test_apply_theme_loads_custom_color_into_shell_root(self, monkeypatch):
        applied_fonts = []
        import font_theme
        monkeypatch.setattr(font_theme, 'apply_global_font',
                            lambda family: applied_fonts.append(family))
        shell = object.__new__(window.ShellWindow)
        shell.config = _config_with(theme='custom',
                                    custom_background='#2A1018',
                                    font='jetbrains-mono-nerd')
        shell.theme_provider = FakeProvider()
        window.ShellWindow._apply_theme(shell)
        assert b'#2A1018' in shell.theme_provider.data
        assert applied_fonts == ['JetBrainsMono Nerd Font, JetBrains Mono, Monospace']


class TestBackEnvRespectsSession:
    def test_prefers_session_display(self, monkeypatch):
        monkeypatch.setenv('WAYLAND_DISPLAY', 'wayland-9')
        assert window._back_env()['WAYLAND_DISPLAY'] == 'wayland-9'

    def test_falls_back_without_env(self, monkeypatch):
        monkeypatch.delenv('WAYLAND_DISPLAY', raising=False)
        assert window._back_env()['WAYLAND_DISPLAY'] == 'wayland-0'

    def test_empty_env_falls_back(self, monkeypatch):
        monkeypatch.setenv('WAYLAND_DISPLAY', '')
        assert window._back_env()['WAYLAND_DISPLAY'] == 'wayland-0'

    def test_runtime_dir_is_uid_scoped(self):
        import os
        assert window._back_env()['XDG_RUNTIME_DIR'] == f'/run/user/{os.getuid()}'


class TestFontsPropagateViaGlobalProvider:
    def test_surface_sheets_never_hardcode_a_font_family(self):
        # Fonts reach the surfaces display-wide through font_theme's global
        # provider; a family baked into a surface sheet would defeat the
        # hot-reloaded font setting.
        for module in (notification_shade, call_ui, dialer, sms, power_menu):
            sheet = module.theme_css(THEME_PRESETS['paper']).lower()
            assert 'font-family' not in sheet, module.__name__


class _WatchedConfig(ShellConfig):
    """Records a snapshot of every dict assigned to ``data``."""

    def __init__(self) -> None:
        self.assigned: list[dict] = []
        super().__init__()
        self.assigned.clear()

    @property
    def data(self):
        return self._data

    @data.setter
    def data(self, value):
        self._data = value
        if hasattr(self, 'assigned'):
            self.assigned.append(dict(value))


class TestReloadConfigSwapsOnce:
    """window._reload_config builds the new data, then swaps it in once;
    gestures.json edits re-sync lisgd."""

    @pytest.fixture(autouse=True)
    def _isolated_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))

    def _shell(self, config=None):
        from gesture_config import GestureConfig
        config = config if config is not None else _WatchedConfig()
        shell = types.SimpleNamespace(
            config=config, gesture_config=GestureConfig(),
            _config_reload_timer=1, applied=0, synced=0)
        shell._apply_config_change = lambda: setattr(
            shell, 'applied', shell.applied + 1)
        shell._sync_system_gestures = lambda notify=False: setattr(
            shell, 'synced', shell.synced + 1)
        shell._reload_gestures = (
            lambda: window.ShellWindow._reload_gestures(shell))
        return shell

    def _write(self, config, payload) -> None:
        import json
        config.config_dir.mkdir(parents=True, exist_ok=True)
        config.config_path.write_text(json.dumps(payload), encoding='utf-8')

    def test_edit_is_swapped_in_complete_and_once(self):
        shell = self._shell()
        self._write(shell.config, {'theme': 'ocean', 'home_alignment': 'left'})
        window.ShellWindow._reload_config(shell)
        assert len(shell.config.assigned) == 1
        assert shell.config.assigned[0]['theme'] == 'ocean'
        assert shell.config.assigned[0]['home_alignment'] == 'left'
        assert shell.applied == 1

    def test_unreadable_file_keeps_live_data(self):
        shell = self._shell()
        shell.config.set_theme('ocean')
        live = shell.config.data
        shell.config.config_path.write_text('{"theme": "gra', encoding='utf-8')
        window.ShellWindow._reload_config(shell)
        assert shell.config.data is live and live['theme'] == 'ocean'
        assert shell.config.assigned == [] and shell.applied == 0

    def test_own_save_skips_the_rebuild(self):
        shell = self._shell()
        shell.config.set_theme('ocean')
        window.ShellWindow._reload_config(shell)
        assert shell.applied == 0

    def test_deleted_config_keeps_the_helper_pin_marker(self):
        from config import HELPER_PIN_MARKER
        shell = self._shell()
        shell.config.set_pin_in_helper()
        shell.config.config_path.unlink()
        window.ShellWindow._reload_config(shell)
        assert shell.config.pin_hash == HELPER_PIN_MARKER

    def test_lisgd_slot_edit_resyncs_lisgd(self):
        from gesture_config import GestureConfig
        shell = self._shell()
        GestureConfig().set('swipe_down_top', 'none')
        window.ShellWindow._reload_config(shell)
        assert shell.synced == 1
        assert shell.gesture_config.get('swipe_down_top') == 'none'

    def test_other_gesture_edit_leaves_lisgd_alone(self):
        from gesture_config import GestureConfig
        shell = self._shell()
        GestureConfig().set('double_tap_home', 'lock_screen')
        window.ShellWindow._reload_config(shell)
        assert shell.synced == 0
        assert shell.gesture_config.get('double_tap_home') == 'lock_screen'

    def test_gestures_reload_even_when_config_is_unreadable(self):
        from gesture_config import GestureConfig
        shell = self._shell()
        shell.config.set_theme('ocean')
        shell.config.config_path.write_text('{', encoding='utf-8')
        GestureConfig().set('swipe_left_edge', 'none')
        window.ShellWindow._reload_config(shell)
        assert shell.synced == 1
