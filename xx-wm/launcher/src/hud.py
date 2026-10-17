"""
In-shell volume/brightness HUD (plan T2).

A brief GTK4 layer-shell OVERLAY window that flashes the current volume or
brightness level after a hardware key press. The shell owns every surface it
draws (see design.md "System surfaces"), so this replaces the external `wob`
daemon: no separate process, no IPC socket — just a short-lived overlay that
auto-hides after ~1s.

Headless-safe: on a host without PyGObject (or without the gtk4-layer-shell
compositor seam), the overlay is never created and every public method
silently no-ops, so wiring volume/brightness through this module can never
break on a device where the HUD cannot init.
"""
from __future__ import annotations

import logging

_log = logging.getLogger('hud')

from config import ShellConfig, ThemePreset

_HAS_GTK = True
try:
    import gi
except ImportError:
    _HAS_GTK = False

if _HAS_GTK:
    try:
        gi.require_version('Gtk4LayerShell', '1.0')
        _HAS_LAYER = True
    except (ValueError, ImportError):
        _HAS_LAYER = False

    gi.require_version('Gtk', '4.0')
    from gi.repository import GLib, Gtk

    if _HAS_LAYER:
        from gi.repository import Gtk4LayerShell as LayerShell

_HUD_HOLD_MS          = 1000
_HUD_FADE_STEPS       = 10
_HUD_FADE_INTERVAL_MS = 20

_HUD_FALLBACK_SCREEN_PX = 720


def hud_top_margin(screen_height: int, hud_height: int = 0) -> int:
    """TOP margin centering the HUD in the top third of the screen
    (owner: "middle top 1/3"): its middle sits at height / 6."""
    try:
        height = int(screen_height)
    except (TypeError, ValueError):
        height = _HUD_FALLBACK_SCREEN_PX
    try:
        own = max(0, int(hud_height))
    except (TypeError, ValueError):
        own = 0
    return max(0, int(height / 6 - own / 2))


def _screen_height() -> int:
    """Current output height; falls back to the 360x720 target."""
    try:
        from gi.repository import Gdk
        display = Gdk.Display.get_default()
        # GTK4 has no primary monitor; the old get_primary_monitor() call
        # always raised, so the HUD sat at 20% of a guessed 720 px screen.
        monitors = display.get_monitors() if display is not None else None
        monitor = (monitors.get_item(0)
                   if monitors is not None and monitors.get_n_items() else None)
        geometry = monitor.get_geometry() if monitor is not None else None
        height = geometry.height if geometry is not None else None
        if height:
            return int(height)
    except Exception:
        pass
    return _HUD_FALLBACK_SCREEN_PX


def volume_label(pct: int) -> str:
    """HUD caption for a volume level: 'Muted' at 0, else 'Volume N%'."""
    pct = max(0, min(100, int(pct)))
    return 'Muted' if pct == 0 else f'Volume {pct}%'


def brightness_label(pct: int) -> str:
    """HUD caption for a brightness level."""
    pct = max(0, min(100, int(pct)))
    return f'Brightness {pct}%'

def theme_css(preset: ThemePreset) -> str:
    return f"""
    .piercing-hud-box {{
        background: {preset.surface};
        border-radius: 20px;
        border: 1px solid {preset.border};
        padding: 12px 16px;
    }}
    .piercing-hud-icon {{
        color: {preset.foreground};
    }}
    .piercing-hud-label {{
        color: {preset.foreground};
        font-size: 12pt;
    }}
    .piercing-hud-level {{
        min-width: 140px;
        min-height: 6px;
        border-radius: 3px;
        background: alpha({preset.foreground}, 0.18);
    }}
    .piercing-hud-level > trough > progress {{
        background: {preset.foreground};
        border-radius: 3px;
    }}
"""


if _HAS_GTK:

    class _HudWindow(Gtk.Window):
        """Centered overlay that flashes a level (volume or brightness) then fades."""

        def __init__(self, config: ShellConfig | None = None) -> None:
            super().__init__()
            self._anim_src: int | None = None
            self._fade_step = 0
            # The shell threads its LIVE config so theme == 'custom' renders
            # the derived palette; the fallback keeps direct construction
            # working (pre-existing snapshot behavior).
            self._config = config if config is not None else ShellConfig()

            self.set_decorated(False)
            self.set_resizable(False)

            if _HAS_LAYER and LayerShell.is_supported():
                LayerShell.init_for_window(self)
                LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
                LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
                LayerShell.set_anchor(self, LayerShell.Edge.BOTTOM, False)
                LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
                LayerShell.set_anchor(self, LayerShell.Edge.RIGHT, True)
                LayerShell.set_exclusive_zone(self, 0)
                LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
                LayerShell.set_margin(
                    self, LayerShell.Edge.TOP,
                    hud_top_margin(_screen_height()))

            # Registered once; apply_theme() reloads this same provider so hot
            # reloads never stack a new display provider per call.
            self._theme_provider = Gtk.CssProvider()
            self._theme_provider.load_from_data(
                theme_css(self._display_preset()).encode('utf-8'))
            Gtk.StyleContext.add_provider_for_display(
                self.get_display(), self._theme_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

            win_css = Gtk.CssProvider()
            win_css.load_from_data(b'window { background: transparent; }')
            self.get_style_context().add_provider(
                win_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 10,
            )

            self._icon = Gtk.Image.new_from_icon_name(
                'audio-volume-medium-symbolic')
            self._icon.add_css_class('piercing-hud-icon')
            self._icon.set_pixel_size(24)
            self._icon.set_valign(Gtk.Align.CENTER)

            self._label = Gtk.Label(label='50%', xalign=0)
            self._label.add_css_class('piercing-hud-label')
            self._label.set_max_width_chars(14)

            self._level = Gtk.ProgressBar()
            self._level.add_css_class('piercing-hud-level')
            self._level.set_fraction(0.5)
            self._level.set_show_text(False)
            self._level.set_hexpand(True)

            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            text.set_hexpand(True)
            text.set_valign(Gtk.Align.CENTER)
            text.append(self._label)
            text.append(self._level)

            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            box.add_css_class('piercing-hud-box')
            box.set_halign(Gtk.Align.CENTER)
            box.set_valign(Gtk.Align.START)
            box.append(self._icon)
            box.append(text)
            self._box = box
            self.set_child(box)

            # The OVERLAY still receives input while visible (~1.2 s
            # per volume press); drop the input region so touches pass through.
            self.connect('realize', self._on_realize)

            self.set_opacity(0.0)

        def apply_theme(self, preset: ThemePreset | None = None) -> None:
            """Reload this overlay's display-level sheet. The shell window
            passes the freshly resolved preset on hot-reload fan-out;
            standalone falls back to the injected config's own preset."""
            data = theme_css(
                preset if preset is not None else self._display_preset())
            self._theme_provider.load_from_data(data.encode('utf-8'))

        def _on_realize(self, _widget) -> None:
            self._drop_input()

        def _drop_input(self) -> None:
            """Empty input region so the overlay never swallows touches.
            Without cairo, keep the TOP + LEFT + RIGHT strip (sized to
            content) so the rest of the screen stays live and the box
            stays horizontally centered."""
            try:
                import cairo
            except ImportError:
                cairo = None
            surface = self.get_surface()
            if cairo is not None and surface is not None:
                surface.set_input_region(cairo.Region())
                return
            if _HAS_LAYER and LayerShell.is_supported():
                LayerShell.set_anchor(self, LayerShell.Edge.BOTTOM, False)
                LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
                LayerShell.set_anchor(self, LayerShell.Edge.RIGHT, True)

        def _display_preset(self) -> ThemePreset:
            """Preset this overlay renders with: window.resolve_theme over
            the injected config, so theme == 'custom' derives its palette
            instead of falling back to the default preset (W1-B). Lazy
            import: window.py sits above this module in the shell stack,
            and a module-level import would drag GTK requirements into
            headless contexts that import hud for its silent-absence path.
            """
            from window import resolve_theme
            return resolve_theme(self._config)

        def show_volume(self, pct: int) -> None:
            """Display a volume level ('Volume N%', 'Muted' at 0)."""
            pct = max(0, min(100, int(pct)))
            if pct == 0:
                icon = 'audio-volume-muted-symbolic'
            elif pct < 34:
                icon = 'audio-volume-low-symbolic'
            elif pct < 67:
                icon = 'audio-volume-medium-symbolic'
            else:
                icon = 'audio-volume-high-symbolic'
            self._icon.set_from_icon_name(icon)
            self._flash(volume_label(pct), pct)

        def show_brightness(self, pct: int) -> None:
            """Display a brightness level ('Brightness N%')."""
            pct = max(0, min(100, int(pct)))
            self._icon.set_from_icon_name('display-brightness-symbolic')
            self._flash(brightness_label(pct), pct)

        def show_level(self, pct: int) -> None:
            """Display a clamped 0-100 level and schedule the auto-hide fade."""
            pct = max(0, min(100, int(pct)))
            self._flash(f'{pct}%', pct)

        def _place_at_top_fraction(self) -> None:
            if _HAS_LAYER and LayerShell.is_supported():
                try:
                    _min, natural, _b1, _b2 = self.measure(
                        Gtk.Orientation.VERTICAL, -1)
                except Exception:
                    natural = 0
                try:
                    LayerShell.set_margin(
                        self, LayerShell.Edge.TOP,
                        hud_top_margin(_screen_height(), natural))
                except Exception:
                    pass

        def _flash(self, text: str, pct: int) -> None:
            self._place_at_top_fraction()
            self._label.set_text(text)
            self._level.set_fraction(pct / 100.0)

            if self._anim_src is not None:
                GLib.source_remove(self._anim_src)
                self._anim_src = None
            self.set_opacity(1.0)
            self.set_visible(True)
            self.present()
            self._anim_src = GLib.timeout_add(_HUD_HOLD_MS, self._begin_fade)

        def _begin_fade(self) -> bool:
            self._anim_src = None
            self._fade_step = 0
            self._anim_src = GLib.timeout_add(_HUD_FADE_INTERVAL_MS, self._tick_fade)
            return GLib.SOURCE_REMOVE

        def _tick_fade(self) -> bool:
            self._fade_step += 1
            alpha = 1.0 - self._fade_step / _HUD_FADE_STEPS
            if alpha <= 0.0:
                self.set_opacity(0.0)
                self.set_visible(False)
                self._anim_src = None
                return GLib.SOURCE_REMOVE
            self.set_opacity(alpha)
            return GLib.SOURCE_CONTINUE


class Hud:
    """Volume/brightness HUD overlay. Silent no-op when GTK or the layer shell is absent."""

    def __init__(self, app=None, config: ShellConfig | None = None) -> None:
        # Live ShellConfig when the shell threads it; a fresh snapshot keeps
        # direct no-arg construction working. Held here and handed to the
        # overlay so its sheet resolves through window.resolve_theme.
        self._config = config if config is not None else ShellConfig()
        self._window = _HudWindow(self._config) if _HAS_GTK else None
        if self._window is None:
            _log.info('HUD disabled: PyGObject unavailable')
        if app is not None:
            self.set_application(app)

    def set_application(self, app) -> None:
        if self._window is not None:
            self._window.set_application(app)

    def show_volume(self, pct: int) -> None:
        if self._window is not None:
            self._window.show_volume(pct)

    def show_brightness(self, pct: int) -> None:
        if self._window is not None:
            self._window.show_brightness(pct)

    def show_level(self, pct: int) -> None:
        if self._window is not None:
            self._window.show_level(pct)

    def apply_theme(self, preset: ThemePreset | None = None) -> None:
        """Reload the overlay's sheet (hot-reload fan-out); silent no-op
        headlessly, like every other method here."""
        if self._window is not None:
            self._window.apply_theme(preset)