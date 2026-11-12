"""Custom-theme rendering reaches every secondary surface (slice W1-B).

``window.resolve_theme`` is the canonical preset resolver: theme == 'custom'
derives background/surface shades from the stored ``custom_background`` and
takes text/accent from the canonical preset by luminance (WS22/WS27). The
main window routed through it in WS27; hud / app_switcher /
quick_actions / lock_screen kept building their own snapshot and styling
with the DEFAULT preset under custom themes. This file pins the fix: every
one of those surfaces resolves its display preset through resolve_theme at
CSS-build time from an injected live config, while no-arg construction
keeps working (backward-compat invariant).

Live re-theme on hot reload is covered in test_theme_hot_reload.py; this
file still pins construction-time resolve_theme and that apply_theme exists
on HUD, switcher, lock, back overlay, and the quick-actions panel.

Headless seams: modules are imported once against a shared fake gi stack
(test_theme_hot_reload idiom, extended for the switcher's toplevel_manager
chain); surface instances are built with ``__new__`` so only the preset-
resolution seam runs; window.py itself is executed exactly once at import,
then dropped from sys.modules — the lazy ``from window import resolve_theme``
inside each surface is served a detached-function stub (the function object
keeps its own globals alive), so tests never re-execute window.py against
whatever gi stack happens to be installed when they run.

hud is the one exception to module-level import: test_hud_module pins
``_HAS_GTK is False`` on hud's FIRST import, so this file must never cache
a GTK-capable hud. Its overlay seam is driven through a scoped fixture that
imports hud fresh against an importable fake gi and restores whatever was
cached before (test_lock_screen's lock_module idiom).
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
    of window.py and the surface modules (plus their import chains:
    quick_actions/als_brightness/app_index/app_item_actions/contacts and
    app_switcher/toplevel_manager). Same stack as test_theme_hot_reload."""
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
        # window/notification_shade/lock_screen/hud probe
        # Gtk4LayerShell and treat ValueError as 'absent'.
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

import app_switcher  # noqa: E402  (needs fake gi modules installed first)
import lock_screen  # noqa: E402
import notification_shade  # noqa: E402
import quick_actions  # noqa: E402
import window  # noqa: E402  (resolve_theme comes from here, exactly once)

# Detach the resolver, then leave the module cache as we found it: later
# test files import window under their own fake gi and rely on getting a
# freshly executed module (test_theme_hot_reload hygiene).
_RESOLVE_THEME = window.resolve_theme
_RESOLVE_APPEARANCE = window.resolve_appearance_preset
sys.modules.pop('window', None)


def _gi_keys() -> list[str]:
    return [k for k in sys.modules if k == 'gi' or k.startswith('gi.')]


@pytest.fixture()
def hud_mod():
    """hud imported fresh with an IMPORTABLE fake gi, so ``_HAS_GTK`` is
    True and the ``_HudWindow`` seam exists. Both the hud cache and the gi
    namespace are restored afterwards: test_hud_module pins hud's first-
    import branch, so this file must leave no trace either way."""
    saved_hud = sys.modules.pop('hud', None)
    saved_gi = {k: sys.modules[k] for k in _gi_keys()}
    _install_fake_gi()
    try:
        import hud
        yield hud
    finally:
        sys.modules.pop('hud', None)
        if saved_hud is not None:
            sys.modules['hud'] = saved_hud
        for k in _gi_keys():
            sys.modules.pop(k, None)
        sys.modules.update(saved_gi)


@pytest.fixture(autouse=True)
def _serve_detached_resolver():
    """Serve the lazy `from window import resolve_theme` in every surface.

    window.py ran once above under this file's fake gi and was then removed
    from sys.modules; the detached function still carries its globals, so a
    namespace stub under the 'window' name satisfies the lazy import without
    re-executing window.py (whose module body needs a gi stack).
    """
    sentinel = object()
    saved = sys.modules.get('window', sentinel)
    sys.modules['window'] = types.SimpleNamespace(
        resolve_theme=_RESOLVE_THEME,
        resolve_appearance_preset=_RESOLVE_APPEARANCE)
    yield
    if saved is sentinel:
        sys.modules.pop('window', None)
    else:
        sys.modules['window'] = saved


def _config_with(**overrides) -> ShellConfig:
    """ShellConfig stripped of host-config influence: data is fully replaced
    so every property below resolves from exactly the given overrides."""
    cfg = ShellConfig.__new__(ShellConfig)
    cfg.data = dict(overrides)
    return cfg


CUSTOM_BG = '#2A1018'


def _surface_with_config(cls, config):
    """Bare instance carrying only what _display_preset() reads."""
    surf = cls.__new__(cls)
    surf._config = config
    return surf


# (module, class whose seam is driven) — the W1-B surfaces importable at
# module scope; hud is covered separately via the hud_mod fixture.
SURFACES = [
    ('app_switcher', app_switcher.AppSwitcher),
    ('quick_actions', quick_actions.QuickActionsPanel),
    ('lock_screen', lock_screen.LockScreen),
]

_MODULES = {
    'app_switcher': app_switcher,
    'quick_actions': quick_actions,
    'lock_screen': lock_screen,
}


class TestCustomThemeReachesSurfaces:
    # Fields each surface sheet must carry for a custom preset: the
    # panel's tiles moved to the shared xx-tile builder (common/xx_ui),
    # so its own sheet carries the slider trough (surface_alt) while the
    # tile surface arrives through the design-system sheet.
    _CUSTOM_FIELDS = {
        'app_switcher': ('surface', 'foreground'),
        'quick_actions': ('surface_alt', 'foreground'),
        'lock_screen': ('surface', 'foreground'),
    }

    @pytest.mark.parametrize('name, cls', SURFACES)
    def test_display_preset_resolves_the_custom_palette(self, name, cls):
        """Injected custom-theme config → resolve_theme's derived preset,
        never the DEFAULT fallback the raw .theme lookup used to produce."""
        surf = _surface_with_config(
            cls, _config_with(theme='custom', custom_background=CUSTOM_BG))
        preset = surf._display_preset()
        assert isinstance(preset, ThemePreset)
        assert preset.key == 'custom'
        assert preset.background == CUSTOM_BG

    @pytest.mark.parametrize('name, cls', SURFACES)
    def test_surface_sheet_renders_the_custom_color(self, name, cls):
        """End to end: injected config → resolved preset → that surface's
        pure sheet carries the derived custom palette (what actually gets
        painted)."""
        surf = _surface_with_config(
            cls, _config_with(theme='custom', custom_background=CUSTOM_BG))
        preset = surf._display_preset()
        sheet = _MODULES[name].theme_css(preset)
        for field in self._CUSTOM_FIELDS[name]:
            assert getattr(preset, field) in sheet, field

    def test_overlay_resolves_the_custom_palette(self, hud_mod):
        """Same contract for the HUD overlay window (scoped import — see
        hud_mod): its sheet must carry the derived custom palette."""
        surf = _surface_with_config(
            hud_mod._HudWindow,
            _config_with(theme='custom', custom_background=CUSTOM_BG))
        preset = surf._display_preset()
        sheet = hud_mod.theme_css(preset)
        assert preset.surface in sheet
        assert preset.foreground in sheet

    def test_custom_preset_differs_from_default_fallback(self):
        """The bug this slice fixes: under theme='custom' the surfaces used to
        style with the DEFAULT preset. The resolved palette must not be it."""
        surf = _surface_with_config(
            app_switcher.AppSwitcher,
            _config_with(theme='custom', custom_background=CUSTOM_BG))
        assert surf._display_preset() is not THEME_PRESETS['amoled']


@pytest.fixture()
def scoped_modules():
    """Modules imported here under this file's fake gi are dropped again,
    so later files import them against their own gi."""
    before = {name: sys.modules.get(name) for name in ('banner', 'power_menu')}
    yield
    for name, mod in before.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


class TestAppearanceModeReachesOverlays:
    """lock, HUD, banner and the app-owned power menu honour
    appearance_mode like the shade and home do."""

    def test_lock_follows_light_mode(self):
        surf = _surface_with_config(
            lock_screen.LockScreen,
            _config_with(theme='amoled', appearance_mode='light'))
        assert surf._display_preset() is THEME_PRESETS['paper']

    def test_hud_follows_light_mode(self, hud_mod):
        surf = _surface_with_config(
            hud_mod._HudWindow,
            _config_with(theme='amoled', appearance_mode='light'))
        assert surf._display_preset() is THEME_PRESETS['paper']

    def test_banner_follows_light_mode(self, scoped_modules):
        import banner
        if not banner._HAS_GTK:
            src = (Path(banner.__file__).read_text(encoding='utf-8')
                   .split('def _display_preset')[1].split('def ')[0])
            assert 'resolve_appearance_preset' in src
            return
        surf = _surface_with_config(
            banner.BannerWindow,
            _config_with(theme='amoled', appearance_mode='light'))
        assert surf._display_preset() is THEME_PRESETS['paper']

    def test_power_menu_follows_custom_and_light(self, scoped_modules):
        import power_menu
        surf = _surface_with_config(
            power_menu.PowerMenu,
            _config_with(theme='custom', custom_background=CUSTOM_BG))
        assert surf._display_preset().background == CUSTOM_BG
        surf = _surface_with_config(
            power_menu.PowerMenu,
            _config_with(theme='amoled', appearance_mode='light'))
        assert surf._display_preset() is THEME_PRESETS['paper']

    def test_lock_hot_reload_reaches_its_media_card(self, monkeypatch):
        applied = []
        monkeypatch.setattr(lock_screen, '_apply_lock_theme',
                            lambda preset: applied.append(('lock', preset)))
        surf = _surface_with_config(lock_screen.LockScreen,
                                    _config_with(theme='forest'))
        surf._media = types.SimpleNamespace(
            apply_theme=lambda preset: applied.append(('media', preset)))
        surf.apply_theme(THEME_PRESETS['paper'])
        assert applied == [('lock', THEME_PRESETS['paper']),
                           ('media', THEME_PRESETS['paper'])]


class TestFallbackAndGarbageContracts:
    @pytest.mark.parametrize('name, cls', SURFACES)
    def test_known_preset_passes_through(self, name, cls):
        surf = _surface_with_config(cls, _config_with(theme='forest'))
        assert surf._display_preset() is THEME_PRESETS['forest']

    @pytest.mark.parametrize('name, cls', SURFACES)
    def test_unknown_theme_key_still_falls_back_to_default(self, name, cls):
        """The config-compat invariant flows through resolve_theme unchanged:
        junk keys render the default preset, never a crash."""
        surf = _surface_with_config(cls, _config_with(theme='no-such-theme'))
        assert surf._display_preset() is THEME_PRESETS['amoled']

    @pytest.mark.parametrize('name, cls', SURFACES)
    def test_custom_without_usable_color_falls_back_to_default(self, name, cls):
        surf = _surface_with_config(cls, _config_with(theme='custom'))
        assert surf._display_preset() is THEME_PRESETS['amoled']

    def test_hud_overlay_falls_back_to_default(self, hud_mod):
        surf = _surface_with_config(
            hud_mod._HudWindow, _config_with(theme='no-such-theme'))
        assert surf._display_preset() is THEME_PRESETS['amoled']


class TestConstructorBackCompat:
    """Every surface stays constructible without a config argument: the new
    parameter is optional and the no-arg call still binds."""

    def test_config_param_is_optional_everywhere(self):
        for cls in (app_switcher.AppSwitcher,
                    quick_actions.QuickActionsPanel):
            params = inspect.signature(cls.__init__).parameters
            assert 'config' in params, cls.__name__
            assert params['config'].default is None, cls.__name__

    def test_hud_config_param_is_optional(self, hud_mod):
        params = inspect.signature(hud_mod.Hud.__init__).parameters
        assert 'config' in params
        assert params['config'].default is None

    def test_surfaces_bind_with_no_args(self):
        # bind() validates a no-arg call against the real signature without
        # constructing GTK widgets (impossible headlessly).
        inspect.signature(app_switcher.AppSwitcher).bind()
        inspect.signature(quick_actions.QuickActionsPanel).bind()

    def test_hud_binds_with_no_args(self, hud_mod):
        inspect.signature(hud_mod.Hud).bind()


class TestShadeThreadsConfigIntoPanel:
    def test_panel_signature_accepts_config(self):
        params = inspect.signature(
            quick_actions.QuickActionsPanel.__init__).parameters
        assert 'config' in params
        assert params['config'].default is None

    def test_notification_shade_forwards_its_live_config(self):
        """The shade embeds the panel, so its LIVE config is the only thread
        down: the construction line must pass config=self._config (source
        assertion — NotificationShade builds real widgets in __init__)."""
        src = inspect.getsource(notification_shade.NotificationShade.__init__)
        panel_call = src.split('QuickActionsPanel(')[1]
        assert 'config=self._config' in panel_call


class TestApplyThemeSeam:
    """Hot-reload fan-out (1.8) needs apply_theme on every overlay that
    previously only themed at construction."""

    def test_surfaces_expose_apply_theme(self):
        for cls in (app_switcher.AppSwitcher, lock_screen.LockScreen,
                    quick_actions.QuickActionsPanel):
            assert callable(getattr(cls, 'apply_theme', None)), cls.__name__

    def test_hud_exposes_apply_theme(self, hud_mod):
        assert callable(getattr(hud_mod.Hud, 'apply_theme', None))
        assert callable(getattr(hud_mod._HudWindow, 'apply_theme', None))


class _RecordingSurface:
    def __init__(self) -> None:
        self.applied: list[ThemePreset] = []

    def apply_theme(self, preset) -> None:
        self.applied.append(preset)
