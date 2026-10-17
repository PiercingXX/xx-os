"""Top status bar: a thin Layer.TOP strip that reserves the camera area.

Phones with a punch-hole front camera need the top few millimetres kept
free of app content, the way Android's status bar does. This surface is
anchored TOP + LEFT + RIGHT with an exclusive zone equal to its height,
so phoc lays maximized apps (and every zone-0 layer surface: the home /
drawer / settings shell window, the shade, banners) out BELOW it. Home
therefore never adds its own top inset; the compositor does it once.

Content follows the house look (common/DESIGN.md, BRAND-GUIDE.md): the
theme background as a solid strip (the Android launcher paints its status
bar with the theme background too), the clock at the left in the theme's
house mono, and battery, network and the important state glyphs at the
right. The centre stays empty because that is where the camera hole sits.

Data comes from the shell's existing sources, never a new poller: the
window's 1 s clock tick (clock_format.format_clock / resolve_24h, DnD and
Focus state), its 60 s status poll (system_status.status_snapshot, run off
the main loop) and the privacy monitor's push.

A downward swipe on the bar opens the notification shade (same verb as
lisgd's top-edge `gesture.shade`; show_shade is idempotent, so both firing
for one swipe is harmless).

Visibility: hidden while the lock screen or the first-boot / welcome
wizard is up. Fullscreen apps need nothing here: phoc does not draw
Layer.TOP surfaces over a fullscreen view and does not apply exclusive
zones to fullscreen geometry.

Headless-safe like banner.py: without PyGObject only the pure helpers
exist; without layer-shell support create_status_bar() returns None (a
dev run nested in another compositor gets no bar).
"""
from __future__ import annotations

import math
from pathlib import Path

_HAS_GTK = True
try:
    import gi
except ImportError:
    _HAS_GTK = False

_HAS_LAYER = False
if _HAS_GTK:
    try:
        gi.require_version('Gtk4LayerShell', '1.0')
        _HAS_LAYER = True
    except (ValueError, ImportError):
        _HAS_LAYER = False
    try:
        gi.require_version('Gtk', '4.0')
        from gi.repository import Gtk
    except (ValueError, ImportError):
        _HAS_GTK = False
    if _HAS_GTK and _HAS_LAYER:
        try:
            from gi.repository import Gtk4LayerShell as LayerShell
        except ImportError:
            _HAS_LAYER = False

# ---- height -----------------------------------------------------------

# Config key (docs/config.md): logical px, or null for the device default.
CONFIG_KEY = 'status_bar_height'

# Generic phone default: about Android's 24-28 dp status bar.
DEFAULT_HEIGHT = 28
MIN_HEIGHT = 20
MAX_HEIGHT = 96


def cutout_bar_height(hole_top_mm: float, hole_diameter_mm: float,
                      panel_height_mm: float, logical_height: int,
                      clearance_px: int = 2) -> int:
    """Logical-px bar height that puts a top punch-hole inside the bar.

    The hole's lower edge sits ``hole_top_mm + hole_diameter_mm`` below
    the top of the panel; the panel's vertical density is
    ``logical_height / panel_height_mm`` logical px per mm. Round up and
    add a small clearance so the hole never touches the bar's edge."""
    density = float(logical_height) / float(panel_height_mm)
    return int(math.ceil((hole_top_mm + hole_diameter_mm) * density)) + int(
        clearance_px)


# FuriPhone FLX1 (FuriOS, hwcomposer mode 720x1600 at phoc scale 1.2 ->
# 600x1333 logical px; the compositor reports the panel as 63x129 mm):
#   vertical density   1333 px / 129 mm      = 10.33 logical px per mm
#   hole               ~3.5-4 mm across, top edge ~2 mm below the panel top
#   hole lower edge    2 mm + 4 mm (upper estimate) = 6.0 mm
#                      6.0 mm x 10.33 = 62.0 -> 62 logical px
#   bar                62 + 2 px clearance = 64 logical px (about 6.2 mm)
# The hole then spans roughly y 21-62 of a 64 px bar, horizontally centred
# (63 mm wide -> 9.5 px/mm -> a ~38 px hole around x = 300). The real
# panel is 6.67" (about 155 mm tall), so if the reported 129 mm is low the
# bar is only taller in millimetres: the hole stays inside either way.
# At runtime the same formula runs against the monitor phoc reports, so a
# later phoc scale change (scale 2 -> 800 px tall -> 40 px) keeps the hole
# covered; 64 is the fallback when no monitor size is known.
# Settings > Display > Status bar height fine-tunes it on the phone.
FLX1_HOLE_MM = (2.0, 4.0)  # (top edge, diameter), upper estimates
FLX1_HEIGHT = cutout_bar_height(*FLX1_HOLE_MM, 129.0, 1333)

DEVICE_HEIGHTS = {
    'furiphone-flx1': FLX1_HEIGHT,
}
# Devices whose default comes from a top-centre punch-hole.
DEVICE_HOLES_MM = {
    'furiphone-flx1': FLX1_HOLE_MM,
}

_DRM_ROOT = Path('/sys/class/drm')


def detect_device(drm_root: Path = _DRM_ROOT) -> str | None:
    """Device key for a per-device default, or None.

    Mirrors scripts/install.sh detect_phoc_device: the FLX1 is the only
    device whose DRM connector is HWCOMPOSER-1. A sysfs directory glob, so
    it is safe on the main loop."""
    try:
        if any(Path(drm_root).glob('card*-HWCOMPOSER-1')):
            return 'furiphone-flx1'
    except OSError:
        pass
    return None


def clamp_height(value) -> int | None:
    """An int height within MIN..MAX, or None for a missing/garbage value
    (bools are garbage, not 0/1)."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        height = int(value)
    except (TypeError, ValueError):
        return None
    return max(MIN_HEIGHT, min(MAX_HEIGHT, height))


def device_default_height(device: str | None, panel=None) -> int:
    """Per-device default. ``panel`` is (logical px, mm) along the long
    (portrait-vertical) axis as the compositor reports it; with a sane
    panel a punch-hole device recomputes its height for the live scale."""
    hole = DEVICE_HOLES_MM.get(device or '')
    if hole is not None and panel is not None:
        try:
            logical, mm = float(panel[0]), float(panel[1])
        except (TypeError, ValueError, IndexError):
            logical = mm = 0.0
        if logical > 0 and mm > 0:
            height = clamp_height(cutout_bar_height(*hole, mm, logical))
            if height is not None:
                return height
    return DEVICE_HEIGHTS.get(device or '', DEFAULT_HEIGHT)


def monitor_panel(display) -> tuple[int, int] | None:
    """(logical px, mm) of the first monitor's long axis, or None."""
    try:
        monitors = display.get_monitors()
        monitor = monitors.get_item(0) if monitors.get_n_items() else None
        if monitor is None:
            return None
        geometry = monitor.get_geometry()
        logical = max(geometry.width, geometry.height)
        mm = max(monitor.get_width_mm(), monitor.get_height_mm())
    except Exception:
        return None
    return (logical, mm) if logical > 0 and mm > 0 else None


def resolve_height(config, device: str | None = None, panel=None) -> int:
    """Effective bar height: the config key wins, else the device default
    (scale-aware with ``panel``), else DEFAULT_HEIGHT. ``device`` is
    detected when not given."""
    explicit = None
    try:
        getter = getattr(config, 'get', None)
        if callable(getter):
            explicit = getter(CONFIG_KEY, None)
        else:
            explicit = getattr(config, 'data', {}).get(CONFIG_KEY)
    except Exception:
        explicit = None
    height = clamp_height(explicit)
    if height is not None:
        return height
    if device is None:
        device = detect_device()
    return device_default_height(device, panel)


def configure_layer(window, height: int, layer_shell) -> None:
    """Make ``window`` the top status strip: Layer.TOP, anchored TOP + LEFT
    + RIGHT (not BOTTOM), exclusive zone == height so phoc lays maximized
    apps out below it, never takes keyboard focus."""
    layer_shell.init_for_window(window)
    set_namespace = getattr(layer_shell, 'set_namespace', None)
    if callable(set_namespace):
        set_namespace(window, 'xx-wm-statusbar')
    layer_shell.set_layer(window, layer_shell.Layer.TOP)
    layer_shell.set_anchor(window, layer_shell.Edge.TOP, True)
    layer_shell.set_anchor(window, layer_shell.Edge.LEFT, True)
    layer_shell.set_anchor(window, layer_shell.Edge.RIGHT, True)
    layer_shell.set_anchor(window, layer_shell.Edge.BOTTOM, False)
    layer_shell.set_keyboard_mode(window, layer_shell.KeyboardMode.NONE)
    apply_zone(window, height, layer_shell)


def apply_zone(window, height: int, layer_shell) -> None:
    """Size the strip and reserve exactly that much at the top edge."""
    window.set_default_size(-1, int(height))
    window.set_size_request(-1, int(height))
    layer_shell.set_exclusive_zone(window, int(height))


# ---- visibility -------------------------------------------------------

def content_visible(config) -> bool:
    """Android "Show Status Bar" (Home & launcher, off by default): the
    clock and status glyphs show only when it is on. The strip itself
    always stays, so apps keep starting below the camera hole (Android
    keeps the cutout clear the same way while its bar is hidden)."""
    return getattr(config, 'status_bar_visible', False) is True


def decide_visible(locked: bool, wizard_visible: bool) -> bool:
    """Shown over home and apps; hidden under the lock screen and the
    first-boot / welcome wizard (both draw their own full-screen layout).
    Fullscreen apps are the compositor's job (see module docstring)."""
    return not (locked or wizard_visible)


# ---- gesture ----------------------------------------------------------

# A pull this far down (and vertical-dominant) opens the shade. The touch
# stays with the bar's surface after it leaves the strip (Wayland implicit
# grab), so the pull can continue over the app below.
SHADE_PULL_PX = 16


def is_shade_pull(offset_x: float, offset_y: float) -> bool:
    """True for a downward, vertical-dominant drag past SHADE_PULL_PX."""
    try:
        dx, dy = float(offset_x), float(offset_y)
    except (TypeError, ValueError):
        return False
    return dy >= SHADE_PULL_PX and dy > abs(dx) * 1.5


# ---- content ----------------------------------------------------------

LOW_BATTERY_PCT = 15


def network_kind(snapshot: dict | None) -> str | None:
    """'wifi' when an SSID is connected, 'cell' with modem signal, else
    None (system_status.status_snapshot only probes the modem without
    Wi-Fi, the same precedence as the home status strip)."""
    snap = snapshot or {}
    if snap.get('ssid'):
        return 'wifi'
    if snap.get('signal'):
        return 'cell'
    return None


def signal_bars(signal: str | None) -> int:
    """0-4 bars from mobile_signal()'s `▂▄▆█` prefix (`·` is 0)."""
    if not signal:
        return 0
    return sum(1 for ch in str(signal) if ch in '▂▄▆█')


def battery_text(snapshot: dict | None) -> str:
    """`84%`, `84%+` while charging, '' without a battery (same format as
    the home status strip)."""
    snap = snapshot or {}
    pct = snap.get('battery')
    if pct is None:
        return ''
    try:
        pct = int(pct)
    except (TypeError, ValueError):
        return ''
    return f'{pct}%' + ('+' if snap.get('charging') else '')


def battery_low(snapshot: dict | None) -> bool:
    snap = snapshot or {}
    try:
        pct = int(snap.get('battery'))
    except (TypeError, ValueError):
        return False
    return pct <= LOW_BATTERY_PCT and not snap.get('charging')


def right_glyphs(snapshot: dict | None, dnd: bool = False,
                 focus: bool = False, privacy: bool = False) -> list[str]:
    """Right-cluster items, left to right: privacy dot, Focus, DnD,
    network, battery. Only what is active or known appears."""
    items: list[str] = []
    if privacy:
        items.append('privacy')
    if focus:
        items.append('focus')
    if dnd:
        items.append('dnd')
    net = network_kind(snapshot)
    if net is not None:
        items.append(net)
    if battery_text(snapshot):
        items.append('battery')
    return items


def theme_css(preset) -> str:
    """Strip chrome on the brand ramp: theme background, clock at 90% of
    the foreground, glyphs at 80%, low battery in the Warn color."""
    bg = preset.background
    fg = preset.foreground
    from config import WARNING_ORANGE
    return f"""
window.xx-statusbar {{
    background: {bg};
}}
.xx-statusbar-root {{
    background: {bg};
    color: alpha({fg}, 0.90);
    padding: 0 16px;
}}
.xx-statusbar-time {{
    font-size: 13px;
    font-weight: 500;
    font-feature-settings: "tnum";
    color: alpha({fg}, 0.90);
}}
.xx-statusbar-battery {{
    font-size: 12px;
    font-feature-settings: "tnum";
    color: alpha({fg}, 0.90);
}}
.xx-statusbar-battery.low {{
    color: {WARNING_ORANGE};
}}
.xx-statusbar-glyph {{
    color: alpha({fg}, 0.80);
    -gtk-icon-size: 14px;
}}
.xx-statusbar-privacy {{
    font-size: 9px;
    color: {preset.accent};
}}
"""


ICON_NAMES = {
    'wifi': 'xx-shade-wifi-symbolic',
    'dnd': 'xx-shade-dnd-symbolic',
    'focus': 'xx-shade-focus-symbolic',
}

# Keep-out at the centre for the camera hole (wider than the FLX1's ~38 px
# hole so the clock and glyphs never crowd it).
CENTER_KEEPOUT_PX = 64


if _HAS_GTK:

    def _draw_bars(area, cr, width, height, view) -> None:
        """Four rising bars; the first ``bars`` at full glyph color, the
        rest at 25% (the shade-ramp stop for disabled)."""
        try:
            color = area.get_color()  # GTK >= 4.10
        except AttributeError:
            color = area.get_style_context().get_color()
        bars = max(0, min(4, int(getattr(view, '_cell_bars', 0))))
        gap = 1.5
        bar_w = (width - gap * 3) / 4.0
        for index in range(4):
            bar_h = height * (index + 1) / 4.0
            alpha = color.alpha if index < bars else color.alpha * 0.25
            cr.set_source_rgba(color.red, color.green, color.blue, alpha)
            cr.rectangle(index * (bar_w + gap), height - bar_h, bar_w, bar_h)
            cr.fill()

    class StatusBarView(Gtk.CenterBox):
        """The strip's content: clock | camera keep-out | glyphs. A plain
        widget so headless snapshots can compose it above home."""

        def __init__(self) -> None:
            super().__init__()
            self.add_css_class('xx-statusbar-root')
            self.set_hexpand(True)
            self._snapshot: dict = {}
            self._dnd = False
            self._focus = False
            self._privacy = False
            self._cell_bars = 0

            self.time_label = Gtk.Label(xalign=0)
            self.time_label.add_css_class('xx-statusbar-time')
            self.time_label.set_valign(Gtk.Align.CENTER)
            self.set_start_widget(self.time_label)

            keepout = Gtk.Box()
            keepout.set_size_request(CENTER_KEEPOUT_PX, -1)
            self.set_center_widget(keepout)

            self._right = Gtk.Box(spacing=7)
            self._right.set_valign(Gtk.Align.CENTER)
            self._right.set_halign(Gtk.Align.END)
            self.set_end_widget(self._right)

            self._privacy_dot = Gtk.Label(label='●')
            self._privacy_dot.add_css_class('xx-statusbar-privacy')
            self._icons = {}
            for key in ('focus', 'dnd', 'wifi'):
                icon = Gtk.Image.new_from_icon_name(ICON_NAMES[key])
                icon.add_css_class('xx-statusbar-glyph')
                icon.set_pixel_size(14)
                self._icons[key] = icon
            self._cell = Gtk.DrawingArea()
            self._cell.add_css_class('xx-statusbar-glyph')
            self._cell.set_content_width(13)
            self._cell.set_content_height(11)
            self._cell.set_valign(Gtk.Align.CENTER)
            self._cell.set_draw_func(_draw_bars, self)
            self._battery = Gtk.Label()
            self._battery.add_css_class('xx-statusbar-battery')
            self._widgets = {
                'privacy': self._privacy_dot,
                'focus': self._icons['focus'],
                'dnd': self._icons['dnd'],
                'wifi': self._icons['wifi'],
                'cell': self._cell,
                'battery': self._battery,
            }
            for key in ('privacy', 'focus', 'dnd', 'wifi', 'cell', 'battery'):
                widget = self._widgets[key]
                widget.set_visible(False)
                self._right.append(widget)

        def set_time(self, text: str) -> None:
            if self.time_label.get_text() != text:
                self.time_label.set_text(text)

        def set_flags(self, dnd: bool, focus: bool) -> None:
            dnd, focus = bool(dnd), bool(focus)
            if (dnd, focus) != (self._dnd, self._focus):
                self._dnd, self._focus = dnd, focus
                self._render()

        def set_privacy(self, active: bool) -> None:
            if bool(active) != self._privacy:
                self._privacy = bool(active)
                self._render()

        def set_status(self, snapshot: dict | None) -> None:
            self._snapshot = dict(snapshot or {})
            self._render()

        def _render(self) -> None:
            shown = set(right_glyphs(self._snapshot, self._dnd,
                                     self._focus, self._privacy))
            for key, widget in self._widgets.items():
                widget.set_visible(key in shown)
            self._battery.set_text(battery_text(self._snapshot))
            if battery_low(self._snapshot):
                self._battery.add_css_class('low')
            else:
                self._battery.remove_css_class('low')
            self._cell_bars = signal_bars(self._snapshot.get('signal'))
            self._cell.queue_draw()

    class StatusBar(Gtk.Window):
        """The layer-shell surface holding a StatusBarView."""

        def __init__(self, config=None, on_open_shade=None,
                     device: str | None = None) -> None:
            super().__init__()
            self._config = config
            self._on_open_shade = on_open_shade
            self._device = device if device is not None else detect_device()
            self._pull_fired = False
            self._panel = monitor_panel(self.get_display())
            self.height = resolve_height(config, self._device, self._panel)
            self.set_decorated(False)
            self.set_resizable(False)
            self.add_css_class('xx-statusbar')
            configure_layer(self, self.height, LayerShell)

            try:
                from quick_actions import ensure_shade_icons
                ensure_shade_icons(self.get_display())
            except Exception:
                pass
            self._theme_provider = Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_display(
                self.get_display(), self._theme_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
            self.apply_theme()

            self.view = StatusBarView()
            self.view.set_visible(content_visible(config))
            self.set_child(self.view)

            drag = Gtk.GestureDrag.new()
            drag.set_touch_only(False)
            drag.connect('drag-begin', self._on_drag_begin)
            drag.connect('drag-update', self._on_drag_update)
            self.add_controller(drag)

        # -- data --------------------------------------------------------
        def set_time(self, text: str) -> None:
            self.view.set_time(text)

        def set_flags(self, dnd: bool, focus: bool) -> None:
            self.view.set_flags(dnd, focus)

        def set_status(self, snapshot: dict | None) -> None:
            self.view.set_status(snapshot)

        def set_privacy(self, active: bool) -> None:
            self.view.set_privacy(active)

        # -- config / theme ------------------------------------------------
        def apply_theme(self, preset=None) -> None:
            if preset is None:
                try:
                    from window import resolve_appearance_preset
                    preset = resolve_appearance_preset(self._config)
                except Exception:
                    from config import THEME_PRESETS
                    preset = THEME_PRESETS['amoled']
            self._theme_provider.load_from_data(
                theme_css(preset).encode('utf-8'))

        def apply_config(self) -> None:
            """Re-read the height (Settings slider, config hot reload)
            and Show Status Bar."""
            view = getattr(self, 'view', None)
            if view is not None:
                view.set_visible(content_visible(self._config))
            height = resolve_height(self._config, self._device,
                                    getattr(self, '_panel', None))
            if height != self.height:
                self.height = height
                apply_zone(self, height, LayerShell)

        def sync_visibility(self, locked: bool, wizard_visible: bool) -> None:
            visible = decide_visible(locked, wizard_visible)
            if visible == bool(self.get_visible()):
                return
            if visible:
                self.present()
            else:
                self.set_visible(False)

        # -- gesture -------------------------------------------------------
        def _on_drag_begin(self, _gesture, _x, _y) -> None:
            self._pull_fired = False

        def _on_drag_update(self, _gesture, dx, dy) -> None:
            if self._pull_fired or not is_shade_pull(dx, dy):
                return
            self._pull_fired = True
            if callable(self._on_open_shade):
                self._on_open_shade()


def create_status_bar(config=None, on_open_shade=None):
    """A StatusBar on a layer-shell compositor, else None (no GTK, or a
    nested dev session where a top-level strip would be a stray window)."""
    if not (_HAS_GTK and _HAS_LAYER):
        return None
    try:
        if not LayerShell.is_supported():
            return None
    except Exception:
        return None
    return StatusBar(config=config, on_open_shade=on_open_shade)
