from __future__ import annotations

import gi
import os
from pathlib import Path
from string import Template

_LAYER_SHELL = False
try:
    gi.require_version('Gtk4LayerShell', '1.0')
    _LAYER_SHELL = True
except ValueError:
    pass

from gi.repository import Adw, Gdk, GLib, Gtk, Pango

if _LAYER_SHELL:
    from gi.repository import Gtk4LayerShell as LayerShell

from app_index import AppEntry, AppIndex
from app_item_actions import AppItemActions
# ThemePreset/THEME_PRESETS re-export xx_ui.theme (via config); resolve_theme
# below delegates custom-color resolution to the same module, so the shell
# never carries its own copy of the palette or the shade rule.
from config import (
    DEFAULT_CONFIG,
    ShellConfig,
    ThemePreset,
    THEME_PRESETS,
)
from gesture_config import GestureConfig

# lisgd owns the bezel. In-window home swipes that start this close to an
# edge would otherwise double-fire with gesture.back / short-home / long-recents.
_EDGE_GUARD_PX = 72


def origin_is_screen_edge(
        origin: tuple[float, float] | None, width: float,
        margin: float = _EDGE_GUARD_PX) -> bool:
    """True when a swipe began on the left or right bezel."""
    if origin is None or width <= 0:
        return False
    x = origin[0]
    return x <= margin or x >= width - margin


def origin_is_bottom_edge(
        origin: tuple[float, float] | None, height: float,
        margin: float = _EDGE_GUARD_PX) -> bool:
    """True when a swipe began on the bottom bezel."""
    if origin is None or height <= 0:
        return False
    return origin[1] >= height - margin


def origin_is_top_edge(
        origin: tuple[float, float] | None,
        margin: float = _EDGE_GUARD_PX) -> bool:
    """True when a swipe began at the top edge (shade territory)."""
    if origin is None:
        return False
    try:
        return float(origin[1]) <= margin
    except (TypeError, ValueError):
        return False


_PAGE_ORDER = ['home', 'apps', 'settings']

# Pages whose widgets take text input (drawer search, settings entries and
# every entry popover anchored on them). Showing one needs keyboard focus,
# which on phoc's layer-shell v3 means TOP + EXCLUSIVE (ON_DEMAND is v4+).
_EDITABLE_STACK_PAGES = ('apps', 'settings')

# Names that moved to the split modules (home_page, drawer_page,
# settings_page, call_wiring) but stay importable from here, so old
# `window.X` paths keep working. Resolved lazily (PEP 562): importing this
# module never imports the split modules, and vice versa, so there is no
# import-time cycle.
_MOVED_NAMES = {
    '_SETTINGS_SECTION_KEYS': 'settings_page',
    '_GESTURE_TITLES': 'settings_page',
    '_SYSTEM_GESTURE_TITLES': 'settings_page',
    '_ACTION_LABELS': 'settings_page',
    '_VERB_LABELS': 'settings_page',
    '_UPDATE_NOTIF_ID': 'settings_page',
    '_WEB_SEARCH_URL': 'drawer_page',
}


def __getattr__(name: str):
    if name in _MOVED_NAMES:
        import importlib
        return getattr(importlib.import_module(_MOVED_NAMES[name]), name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


# Split-module helpers: composition, not mixins. Each helper holds only a
# reference to the window; all state stays on ShellWindow. Module-level
# functions (not methods) so the delegators below also work when they are
# invoked unbound on bare namespaces that are not ShellWindow instances.
# Imports stay inside the accessors: neither side imports the other at
# module import time, so there are no circular imports.
def _home(win):
    helper = getattr(win, '_home_page', None)
    if helper is None:
        from home_page import HomePage
        helper = HomePage(win)
        win._home_page = helper
    return helper


def _drawer(win):
    helper = getattr(win, '_drawer_page', None)
    if helper is None:
        from drawer_page import DrawerPage
        helper = DrawerPage(win)
        win._drawer_page = helper
    return helper


def _settings(win):
    helper = getattr(win, '_settings_page', None)
    if helper is None:
        from settings_page import SettingsPage
        helper = SettingsPage(win)
        win._settings_page = helper
    return helper


def _calls(win):
    helper = getattr(win, '_call_wiring', None)
    if helper is None:
        from call_wiring import CallWiring
        helper = CallWiring(win)
        win._call_wiring = helper
    return helper


def _home_press_to_recents(win) -> bool:
    """True when a Home press should open recents (home_to_recents).

    Reads only plain attributes so bare test doubles without a config
    keep the old go-home path."""
    config = getattr(win, 'config', None)
    if not getattr(config, 'home_to_recents', False):
        return False
    try:
        if win.stack.get_visible_child_name() != 'home':
            return False
    except Exception:
        return False
    for attr in ('_switcher', '_shade', '_dialer'):
        try:
            surface = getattr(win, attr, None)
            if surface is not None and surface.get_visible():
                return False
        except Exception:
            pass
    return True


def _sync_home_label(item: dict, names: dict, labels: dict) -> bool:
    """Refresh one slot/member label from the index; True when rewritten."""
    app_id = item.get('app_id')
    if not app_id or item.get('cmd'):
        return False
    if app_id in labels:
        return False
    name = names.get(str(app_id))
    if not name or item.get('label') == name:
        return False
    item['label'] = name
    return True


def _sync_home_labels(config, entries) -> None:
    """Refresh never-renamed slot/member labels from the desktop index.

    A .desktop Name change (update, rebrand) flows into every home slot
    and folder member holding that app unless the user renamed it
    (config.app_labels). Command slots keep their labels."""
    names = {entry.app_id: entry.name for entry in entries}
    labels = config.app_labels
    slots = config.home_slots
    changed = False
    for slot in slots:
        if _sync_home_label(slot, names, labels):
            changed = True
        if slot.get('type') == 'folder':
            for member in slot.get('folder') or []:
                if _sync_home_label(member, names, labels):
                    changed = True
    if changed:
        config.set_home_slots(slots)


def _purge_removed_from_config(config, removed: set) -> None:
    """Drop uninstalled app ids from the home config (monitor path).

    Covers home slots, folder members, pins, hidden apps and rename
    labels. Command slots, built-in members and Waydroid entries
    (bare package names, no .desktop suffix) are never matched."""
    if not removed:
        return

    def _gone(item: dict) -> bool:
        app_id = item.get('app_id')
        return (isinstance(app_id, str) and app_id.endswith('.desktop')
                and not item.get('cmd') and app_id in removed)

    slots = [slot for slot in config.home_slots
             if not (slot.get('type') == 'app' and _gone(slot))]
    for slot in slots:
        if slot.get('type') == 'folder':
            members = [member for member in (slot.get('folder') or [])
                       if not _gone(member)]
            if len(members) != len(slot.get('folder') or []):
                slot['folder'] = members
    if slots != config.home_slots:
        config.set_home_slots(slots)
    pinned = [app_id for app_id in config.pinned if app_id not in removed]
    if pinned != config.pinned:
        config.set_pinned(pinned)
    hidden = [app_id for app_id in config.hidden_apps
              if app_id not in removed]
    if hidden != config.hidden_apps:
        config.set_hidden_apps(hidden)
    for app_id in set(config.app_labels) & set(removed):
        config.set_app_label(app_id, None)


def decide_layer_policy(page: str | None, popover_open: bool,
                        over_apps: bool) -> tuple[str, str]:
    """Pure layer/keyboard decision for the shell window.

    Returns (layer_name, keyboard_mode_name) as plain strings so headless
    tests can exercise the table without layer-shell bindings; the caller
    maps them onto LayerShell.Layer / LayerShell.KeyboardMode.
    """
    if page in _EDITABLE_STACK_PAGES or popover_open:
        return ('TOP', 'EXCLUSIVE')
    if over_apps:
        return ('TOP', 'NONE')
    return ('BOTTOM', 'NONE')

# Swipe-up split: a short flick raises the keyboard, a fast or long swipe
# opens the app drawer. The stack's GestureSwipe path is velocity-only;
# the home page's capture drag (see _build_root) decides by distance AND
# release velocity (lisgd does distance).
_LONG_SWIPE_UP_VEL = 900
_SHORT_SWIPE_UP_VEL = 300

# Capture-drag thresholds: the drag claims the touch only after this much
# vertical-dominant travel, so taps, long-presses and horizontal swipes
# never get stolen. At/above _SWIPE_UP_DRAWER_PX of upward travel the
# drawer opens regardless of speed.
_SWIPE_UP_CLAIM_PX = 24
_SWIPE_UP_DRAWER_PX = 120


def should_claim_vertical_drag(offset_x: float, offset_y: float) -> bool:
    """True once a drag owns the touch: vertical-dominant travel past the
    claim threshold. Pure so the threshold is unit-testable — small taps
    and horizontal swipes must never claim."""
    return (abs(offset_y) > _SWIPE_UP_CLAIM_PX
            and abs(offset_y) > abs(offset_x) * 1.5)


def decide_swipe_up_action(
    distance_px: float, velocity_y: float,
) -> str | None:
    """Map an upward drag's travel and release velocity to an action.

    Returns 'drawer' for a fast or long swipe up, 'keyboard' for a short
    one, None when the movement is too small or too slow (a tap, a hold,
    or a list-scroll release). Only upward movement counts: distance is
    up-positive px, velocity is px/s in GTK coordinates (up is negative).
    Pure so the distance/velocity table is unit-testable.
    """
    if distance_px < _SWIPE_UP_CLAIM_PX or velocity_y > -_SHORT_SWIPE_UP_VEL:
        return None
    if (distance_px >= _SWIPE_UP_DRAWER_PX
            or velocity_y <= -_LONG_SWIPE_UP_VEL):
        return 'drawer'
    return 'keyboard'


def swipe_release_velocity(
    samples: list[tuple[float, float, float]],
) -> tuple[float, float]:
    """Release velocity in px/s over the trailing ~120 ms of a drag.

    Samples are (monotonic_ms, x, y) from drag-begin/update. (0, 0) when
    fewer than two samples exist or time stands still. Pure so the window
    calculation is unit-testable.
    """
    if len(samples) < 2:
        return (0.0, 0.0)
    window_ms = 120.0
    t1, x1, y1 = samples[-1]
    t0, x0, y0 = samples[0]
    for stamp, x, y in reversed(samples[:-1]):
        if t1 - stamp > window_ms:
            break
        t0, x0, y0 = stamp, x, y
    dt = (t1 - t0) / 1000.0
    if dt <= 0:
        return (0.0, 0.0)
    return ((x1 - x0) / dt, (y1 - y0) / dt)

def resolve_theme(config: ShellConfig) -> ThemePreset:
    """Active ThemePreset, with theme == 'custom' resolved from the stored
    custom_background color (docs/config.md "Appearance").

    Custom resolution lives in xx_ui.theme; a missing or garbage color
    silently falls back to the default preset (config-compat invariant:
    old configs and junk never crash the shell).
    """
    if str(config.data.get('theme') or '') != 'custom':
        return config.theme
    from xx_ui import theme as _xx_theme
    try:
        resolved = _xx_theme._resolve(dict(config.data))
    except Exception:
        resolved = None
    return resolved if resolved is not None else config.theme


def _xx_render(config: ShellConfig, preset: ThemePreset) -> str:
    """Shared design-system sheet for a resolved preset (pure)."""
    from xx_ui import css as _xx_css
    from xx_ui import theme as _xx_theme
    red, green, blue = (int(preset.background[i:i + 2], 16) for i in (1, 3, 5))
    return _xx_css.render(_xx_theme.Theme(
        key=preset.key,
        background=preset.background,
        surface=preset.surface,
        surface_alt=preset.surface_alt,
        border=preset.border,
        foreground=preset.foreground,
        muted=preset.muted,
        accent=preset.accent,
        font_family=config.font_family,
        scale=config.text_size_scale,
        dark=(0.299 * red + 0.587 * green + 0.114 * blue) / 255 < 0.5,
    ))


def system_prefers_dark() -> bool | None:
    """Desktop color-scheme preference, None when unreadable (schema
    missing, settings daemon down). Local GSettings read, never blocks."""
    try:
        from gi.repository import Gio
        settings = Gio.Settings.new('org.gnome.desktop.interface')
        return settings.get_string('color-scheme') == 'prefer-dark'
    except Exception:
        return None


def effective_dark(config: ShellConfig) -> bool:
    """Light/dark decision for appearance_mode (docs/config.md
    "Appearance"): theme follows prefer_dark, light/dark force it, system
    follows the desktop with prefer_dark as the offline fallback."""
    mode = getattr(config, 'appearance_mode', 'theme')
    if mode not in ('theme', 'light', 'dark', 'system'):
        mode = 'theme'
    if mode == 'light':
        return False
    if mode == 'dark':
        return True
    if mode == 'system':
        system = system_prefers_dark()
        if system is not None:
            return system
    try:
        return bool(config.prefer_dark)
    except AttributeError:
        return True


def _dark_preset_for(config: ShellConfig) -> ThemePreset:
    """The user's dark preset: the current theme when it is dark,
    amoled otherwise (light themes cannot be forced dark)."""
    current = resolve_theme(config)
    try:
        red, green, blue = (int(current.background[i:i + 2], 16)
                            for i in (1, 3, 5))
        dark = (0.299 * red + 0.587 * green + 0.114 * blue) / 255 < 0.5
    except (TypeError, ValueError):
        dark = True
    return current if dark else THEME_PRESETS['amoled']


def resolve_appearance_preset(config: ShellConfig) -> ThemePreset:
    """Active preset for appearance_mode: theme keeps resolve_theme,
    light is Paper, dark is the user's dark preset, system picks Paper
    or the user's dark preset from the desktop color-scheme."""
    mode = getattr(config, 'appearance_mode', 'theme')
    if mode not in ('theme', 'light', 'dark', 'system'):
        mode = 'theme'
    if mode == 'light':
        return THEME_PRESETS['paper']
    if mode == 'dark':
        return _dark_preset_for(config)
    if mode == 'system':
        if effective_dark(config):
            return _dark_preset_for(config)
        return THEME_PRESETS['paper']
    return resolve_theme(config)


def _scaled_pt(base: float, scale: float) -> str:
    return '%gpt' % round(base * scale, 1)


def launcher_text_css(scale: float) -> str:
    """Launcher type sizes the static style.css leaves fixed (clock, date,
    drawer rows): scaled overrides loaded through the window provider, so
    text_size_scale hot-reloads them like the shared sheet's tokens."""
    return """
.display-clock {{
    font-size: {_clock};
}}
.display-date {{
    font-size: {_date};
}}
.dim-label {{
    font-size: {_dim};
}}
.app-name {{
    font-size: {_app};
}}
.app-subtitle {{
    font-size: {_sub};
}}
entry.drawer-search, searchentry.drawer-search {{
    font-size: {_search};
}}
.alpha-jump {{
    font-size: {_alpha};
}}
""".format(
        _clock=_scaled_pt(54, scale),
        _date=_scaled_pt(12, scale),
        _dim=_scaled_pt(11.5, scale),
        _app=_scaled_pt(14, scale),
        _sub=_scaled_pt(10.5, scale),
        _search=_scaled_pt(13, scale),
        _alpha=_scaled_pt(10, scale),
    )


# Shell-only surfaces on top of the shared sheet, same ${token} variables.
# Everything the shared stylesheet paints (entries, lists, popovers,
# switches, buttons) is deliberately NOT repeated here.
_SHELL_SHEET_TEMPLATE = Template("""
.shell-root {
    background: ${background};
    color: ${foreground};
}

.shell-root scrolledwindow.home-scroll,
.shell-root scrolledwindow.home-scroll > viewport {
    background: transparent;
    border: none;
}

.dim-label,
.eyebrow {
    color: ${muted};
}

.action-link {
    color: ${accent};
}

/* Long-press menus render outside .shell-root, so the shared popover
   surface rule needs its content padding here. */
popover.action-menu > contents {
    padding: 6px;
}

/* The drawer list sits on the plain background — no inset panel. */
.shell-root .drawer-sheet {
    background: transparent;
}
.shell-root .drawer-sheet scrolledwindow,
.shell-root .drawer-sheet scrolledwindow > viewport {
    background: transparent;
}

/* Stock error dialogs (power-menu failure path) follow the preset
   instead of the default light theme. */
message-dialog,
message-dialog decoration {
    background: ${surface};
    color: ${foreground};
}
message-dialog label {
    color: ${foreground};
}
""")


def _back_env() -> dict[str, str]:
    """Env for the back-key injection children (wtype/waydroid): target the
    Wayland session the shell actually runs on, not a hardcoded wayland-0 —
    same rule as display_manager._WL_ENV / home_launcher._WAYDROID_ENV."""
    return {
        'WAYLAND_DISPLAY': os.environ.get('WAYLAND_DISPLAY') or 'wayland-0',
        'XDG_RUNTIME_DIR': f'/run/user/{os.getuid()}',
    }


def _set_osk_visible(visible: bool) -> None:
    """Toggle squeekboard's visibility over D-Bus (headless seam for 20.1).

    Both _show_keyboard() and _hide_keyboard() route through here. It is the
    headless seam the tap-outside test drives: monkeypatching Gio's
    bus_get_sync lets a test assert the SetVisible payload without a display.

    The bus round-trip runs on a worker thread: tap and swipe paths call
    this synchronously, and a 500 ms call_sync on the GTK thread would
    stall the gesture that asked for the keyboard. Returns the thread so
    tests can join it.
    """
    import threading

    def _work() -> None:
        from gi.repository import Gio
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call_sync(
                'sm.puri.OSK0', '/sm/puri/OSK0', 'sm.puri.OSK0', 'SetVisible',
                GLib.Variant('(b)', (visible,)), None,
                Gio.DBusCallFlags.NONE, 500, None,
            )
        except Exception as error:
            # A D-Bus failure (squeekboard absent, bus down) must not crash
            # the shell, but it is a real fault on a data path — log it
            # rather than silently dropping the show/hide request.
            from shell_log import get_logger
            get_logger('window').warning(
                'set-OSK-visible(%s) over D-Bus failed: %s', visible, error)

    worker = threading.Thread(target=_work, daemon=True, name='osk-visible')
    worker.start()
    return worker


class ShellWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application) -> None:
        super().__init__(application=application, title='PiercingXX')
        self.add_css_class('xx-wm')

        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.init_for_window(self)
            LayerShell.set_layer(self, LayerShell.Layer.BOTTOM)
            for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM,
                         LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
                LayerShell.set_anchor(self, edge, True)
            # Zone 0 (not -1): the shell shrinks above other surfaces'
            # exclusive zones — critically the OSK, so the drawer search
            # field rides up above the keyboard instead of hiding under it
            LayerShell.set_exclusive_zone(self, 0)
        else:
            self.set_default_size(420, 860)
            self.fullscreen()

        self.config = ShellConfig()
        self.gesture_config = GestureConfig()
        from dnd import DndState
        from focus_mode import FocusState
        self.dnd_state = DndState(self.config)
        self.focus_state = FocusState(self.config)
        try:
            # One address book for the shell: warms the xx-contacts cache
            # before the first call and feeds starred contacts to DnD.
            from contacts import shared_book
            shared_book(self.config)
        except Exception:
            pass
        self.app_index = AppIndex()
        self.app_index.refresh()

        try:
            from default_layout import apply_default_layout, remap_superseded_home
            apply_default_layout(self.config, self.gesture_config)
            remap_superseded_home(self.config, self.app_index)
        except Exception:
            pass  # first-boot seeding must never block the shell

        # Blank-driven auto-lock: DisplayManager reports blank/wake via
        # on_blank_changed; _lock_src is the pending "dark long enough"
        # timer, cancelled on wake. _blanked mirrors the display state.
        self._lock_src: int | None = None
        self._blanked = False
        # Layer/keyboard focus state for _apply_layer_policy: _over_apps is
        # set by present_over_apps/drop_to_background, _popover_count tracks
        # open app_item_actions popovers (entry dialogs need key focus).
        self._over_apps = False
        self._popover_count = 0
        # Capture-drag swipe-up state: samples are (monotonic_ms, x, y)
        # from the latest drag-begin/update; claimed once the threshold owns
        # the touch (see should_claim_vertical_drag).
        self._swipe_drag_samples: list[tuple[float, float, float]] = []
        self._swipe_drag_claimed = False
        self._pick_slot_mode = False
        # Change-app target for the pick mode: the next drawer tap replaces
        # slots[_pick_slot_target] instead of appending a new slot.
        self._pick_slot_target: int | None = None
        # When set to a gesture key ('swipe_left_home'/'swipe_right_home'),
        # the next drawer tap binds that app to the gesture instead of launching
        self._pick_gesture_key: str | None = None
        # When set to a widget key, the next drawer tap binds that app to
        # the widget's tap action instead of launching
        self._pick_widget_tap: str | None = None
        # When True, the next drawer tap adds that app to the standby
        # never-freeze list instead of launching (settings standby card)
        self._pick_exempt_mode = False
        self._gesture_binding_labels: dict[str, Gtk.Label] = {}
        self._system_gesture_labels: dict[str, Gtk.Label] = {}
        self._drawer_open_folder: int | None = None
        self._drawer_folder_header: Gtk.ListBoxRow | None = None
        self._last_search_results: list[AppEntry] = []
        self._call_ui: object | None = None
        self._call_bar: object | None = None
        self.xxdb_controller = None
        self._xxdb_switch_updating = False
        self._xxdb_switch = None
        self._xxdb_status = None
        self._xxdb_hosts_box = None
        self._xxdb_pending_box = None
        self._shade: object | None = None
        self._switcher: object | None = None
        self._dialer: object | None = None
        self._power_menu: object | None = None
        self._back_layer: object | None = None
        self._swipe_origin: tuple[float, float] | None = None
        self._theme_buttons: dict[str, Gtk.Button] = {}
        # Built on first switcher open: a second pywayland Display in
        # ShellWindow.__init__ raced the GTK connection on this compositor.
        self._toplevel_manager = None
        self._thumbs = None
        self._output_capture = None
        self._raised_for_settings = False
        self._layer_is_top = False
        self._search_restore_bottom = False

        from modem_monitor import ModemMonitor
        self._modem_monitor = ModemMonitor(
            on_incoming=self._on_call_incoming,
            on_answered=self._on_call_answered,
            on_ended=self._on_call_ended,
            on_calls_changed=self._on_calls_changed,
            on_sms=self._on_sms_received,
        )
        self._calls_owner_warned = False
        self._chatty_owner_warned = False
        self._txtt_owner_warned = False
        self._sms_notif_seq = 0

        self.base_provider = Gtk.CssProvider()
        self.theme_provider = Gtk.CssProvider()
        self._load_css()

        self.stack = Gtk.Stack(
            hexpand=True,
            vexpand=True,
            transition_duration=200,
            transition_type=Gtk.StackTransitionType.NONE,
        )
        # Each page sizes to itself: a homogeneous stack would stretch every
        # page to the widest/tallest one (the settings page hit 624 px wide),
        # pushing the window off a 360 px phone screen.
        self.stack.set_hhomogeneous(False)
        self.stack.set_vhomogeneous(False)
        self.stack.set_interpolate_size(False)
        # Leaving the drawer disarms the pick modes so a later tap launches
        self.stack.connect(
            'notify::visible-child-name', self._on_stack_page_changed)

        self.item_actions = AppItemActions(
            self.config,
            on_changed=self._refresh_after_item_action,
            on_status=self._show_status,
            focus_state=self.focus_state,
            on_popover_changed=self._on_item_popover,
            on_keyboard=self._on_entry_keyboard,
            real_name_fn=lambda app_id: next(
                (entry.name for entry in self.app_index.entries
                 if entry.app_id == app_id), None),
            on_launch=self.drop_to_background,
            on_open_page=self.open_app_page,
            on_muted=self._mute_app_notifications,
            on_uninstall=self._on_drawer_uninstall,
        )

        # Flat search with the text after the icon, like xx_ui's search_bar
        self.apps_search = Gtk.SearchEntry(placeholder_text='Search')
        self.apps_search.add_css_class('drawer-search')
        self.apps_search.connect('search-changed', self._on_apps_search_changed)
        self.apps_search.connect('activate', self._on_apps_search_activate)
        self.apps_search.connect('notify::has-focus', self._on_search_focus)
        # SearchEntry focus lives on the inner GtkText, so has-focus on the
        # entry itself is often False. A focus controller + a claimed tap
        # raise squeekboard; EXCLUSIVE keyboard mode is armed while typing.
        search_focus = Gtk.EventControllerFocus.new()
        # Arm on enter only: leave fires when focus moves to the inner
        # GtkText and would hide the OSK on the same tap that opened it.
        search_focus.connect('enter', lambda *_: self._arm_search_keyboard())
        self.apps_search.add_controller(search_focus)
        search_tap = Gtk.GestureClick.new()
        search_tap.set_touch_only(False)
        search_tap.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        search_tap.connect('pressed', self._on_search_pressed)
        self.apps_search.add_controller(search_tap)

        self.status_label = Gtk.Label(xalign=0)
        self.status_label.add_css_class('dim-label')

        self.app_count_label = Gtk.Label(xalign=0)
        self.app_count_label.add_css_class('dim-label')

        self.clock_label = Gtk.Label(xalign=0)
        self.clock_label.add_css_class('display-clock')
        # An 81 pt clock at large text scales is wider than the phone:
        # ellipsize instead of forcing the home page wider.
        self.clock_label.set_ellipsize(Pango.EllipsizeMode.END)

        self.date_label = Gtk.Label(xalign=0)
        self.date_label.add_css_class('display-date')
        self.date_label.add_css_class('dim-label')
        self.date_label.set_ellipsize(Pango.EllipsizeMode.END)

        self.weather_label = Gtk.Label(xalign=0)
        self.weather_label.add_css_class('display-date')
        self.weather_label.add_css_class('dim-label')
        self.weather_label.set_ellipsize(Pango.EllipsizeMode.END)

        self.status_strip = Gtk.Label(xalign=0)
        self.status_strip.add_css_class('dim-label')
        # SSIDs can be long; never let the strip force the home page wider.
        self.status_strip.set_ellipsize(Pango.EllipsizeMode.END)

        self.apps_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.apps_list.add_css_class('text-list')

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self._build_root())
        self.set_content(self.toast_overlay)

        # The display owns idle: auto-lock follows blank/wake reports from
        # DisplayManager (on_blank_changed), never motion over this window —
        # app use produces no shell-window input, so a motion timer would
        # lock the phone mid-use.
        key_ctrl = Gtk.EventControllerKey.new()
        key_ctrl.connect('key-pressed', self._on_key_pressed)
        self.add_controller(key_ctrl)

        self._apply_theme()
        self._refresh_clock()
        self._populate_apps()
        self._refresh_status()
        self._setup_config_monitor()
        self._setup_app_monitor()
        GLib.timeout_add_seconds(1, self._tick_clock)
        GLib.timeout_add_seconds(60, self._tick_status)

        from update_checker import UpdateChecker
        self._update_checker = UpdateChecker(
            self.config, on_updates_available=self._on_updates_available,
        )
        # Set the resting BOTTOM + NONE explicitly (previously the keyboard
        # mode was never set, leaving it at the compositor default NONE only
        # by accident and the layer at whatever init_for_window left).
        self._apply_layer_policy()
        # Apps mapped outside the shell must still land above it: the
        # manager degrades to unavailable headless, so this never breaks
        # construction.
        self._wire_foreign_toplevels()
        GLib.idle_add(self._warm_toplevel_manager)
        GLib.timeout_add(2500, self._tick_thumb_capture)
        # SIGUSR1 respawns Python only; pick up migrated lisgd verbs now.
        GLib.idle_add(self._sync_system_gestures)
        try:
            self._attach_osk(self.apn_entry)
            self._attach_osk(self.apn_user_entry)
            self._attach_osk(self.apn_pass_entry)
        except Exception:
            pass

    def _warm_toplevel_manager(self) -> bool:
        self._ensure_toplevel_manager()
        self._ensure_thumbs()
        self._ensure_capture()
        return False

    def _ensure_thumbs(self):
        if self._thumbs is None:
            from thumbnail_store import ThumbnailStore
            self._thumbs = ThumbnailStore()
        return self._thumbs

    def _ensure_capture(self):
        if self._output_capture is None:
            from output_capture import OutputCapture
            self._output_capture = OutputCapture()
        return self._output_capture

    def _overlays_block_capture(self) -> bool:
        if getattr(self, '_blanked', False):
            return True
        try:
            if self._call_live():
                return True
        except Exception:
            pass
        if getattr(self, '_layer_is_top', False):
            return True
        if self._switcher is not None and self._switcher.get_visible():
            return True
        if self._shade is not None and self._shade.get_visible():
            return True
        lock = getattr(self, '_lock_screen', None)
        if lock is not None and lock.get_visible():
            return True
        power = getattr(self, '_power_menu', None)
        if power is not None and power.get_visible():
            return True
        return False

    def _tick_thumb_capture(self) -> bool:
        self._maybe_capture_thumb()
        return True

    def _capture_thumb_once(self) -> bool:
        self._maybe_capture_thumb()
        return False

    def _maybe_capture_thumb(self) -> None:
        if self._overlays_block_capture():
            return
        mgr = self._toplevel_manager
        if mgr is None:
            return
        app_id = mgr.activated_app_id()
        if not app_id:
            return
        thumbs = self._ensure_thumbs()
        from shell_log import get_logger
        started = self._ensure_capture().capture(
            lambda thumb, aid=app_id: (
                get_logger('window').info(
                    'thumb stored %s %sx%s', aid, thumb.width, thumb.height),
                thumbs.put(aid, thumb),
            ))
        if started:
            get_logger('window').info('capturing thumb for %s', app_id)

    def _load_css(self) -> None:
        style_path = Path(__file__).with_name('style.css')
        self.base_provider.load_from_path(str(style_path))
        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(display, self.base_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.StyleContext.add_provider_for_display(display, self.theme_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)

    # -- split-module helpers live at module level (below): composition,
    # not mixins. Each helper holds only a reference to the window; all
    # state stays on ShellWindow.

    def _build_root(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.add_css_class('shell-root')

        self.stack.add_titled(self._build_home_page(), 'home', 'Home')
        self.stack.add_titled(self._build_apps_page(), 'apps', 'Apps')
        self.stack.add_titled(self._build_settings_page(), 'settings', 'Settings')

        swipe = Gtk.GestureSwipe.new()
        swipe.set_touch_only(False)
        swipe.connect('swipe', self._on_stack_swipe)
        self.stack.add_controller(swipe)

        # Swipe-up capture drag: the swipe above sits in the default BUBBLE
        # phase, so a swipe starting on a home slot button is claimed by
        # the button's click gesture first and never reaches it. This drag
        # runs in CAPTURE (before the buttons) and claims only after a
        # vertical-dominant threshold, so taps, long-press edit mode and
        # horizontal swipes keep working; on release it decides drawer vs
        # keyboard by distance and velocity. Only the home page claims —
        # drawer/settings vertical drags scroll their lists via the swipe
        # path above.
        drag = Gtk.GestureDrag.new()
        drag.set_touch_only(False)
        drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        drag.connect('drag-begin', self._on_swipe_drag_begin)
        drag.connect('drag-update', self._on_swipe_drag_update)
        drag.connect('drag-end', self._on_swipe_drag_end)
        self.stack.add_controller(drag)

        # Tap-outside → hide the OSK (20.1): a tap that lands on a non-editable
        # widget means the user is done typing, so drop squeekboard.
        tap = Gtk.GestureClick.new()
        tap.set_touch_only(False)
        tap.connect('pressed', self._on_tap_outside)
        self.stack.add_controller(tap)

        root.append(self.stack)
        return root

    def _drawer_swipe_down_home(self) -> bool:
        """True when a drawer swipe-down closes the drawer instead of
        opening the shade: the list is parked at the top and the swipe did
        not start at the top edge (the shade stays reachable from there)."""
        from drawer_page import decide_drawer_swipe_down, drawer_list_at_top
        return decide_drawer_swipe_down(
            origin_is_top_edge(self._swipe_origin),
            drawer_list_at_top(self)) == 'home'

    def _on_stack_swipe(self, _gesture: Gtk.GestureSwipe, vel_x: float, vel_y: float) -> None:
        # Vertical swipes match the PiercingXX Android launcher: up opens the
        # app drawer, down runs the configured action (default: shade)
        current = self.stack.get_visible_child_name()
        if abs(vel_y) > abs(vel_x) * 1.5:
            if vel_y > 300:
                if current == 'apps' and self._drawer_swipe_down_home():
                    self.go_home()
                    return
                self._dispatch_gesture_action(
                    self.gesture_config.get('swipe_down_top') or 'notification_shade')
            elif vel_y < 0:
                if current == 'settings':
                    self._leave_settings()
                elif current == 'apps':
                    try:
                        self.apps_search.grab_focus()
                    except Exception:
                        pass
                else:
                    # Bottom bezel is lisgd (short = home, long = recents).
                    # Mid-display swipe-up on home still opens the app drawer.
                    height = self.get_height() if hasattr(self, 'get_height') else 0
                    if origin_is_bottom_edge(self._swipe_origin, height):
                        return
                    self.stack.set_visible_child_name('apps')
            return
        if abs(vel_y) > abs(vel_x):
            return
        # Swipe right in the drawer collapses an open folder drop-down first
        folder = getattr(self, '_drawer_open_folder', None)
        if current == 'apps' and vel_x > 200 and folder is not None:
            self._drawer_open_folder = None
            self._populate_apps(self.apps_search.get_text())
            return
        if (current == 'apps' and vel_x > 200
                and getattr(self, '_drawer_open_hidden', False)):
            self._drawer_open_hidden = False
            self._populate_apps(self.apps_search.get_text())
            return
        # Home swipes dispatch their configured apps (design.md "Gestures");
        # an unbound ('none') swipe falls through to page navigation so the
        # drawer stays reachable without lisgd.
        if current == 'home' and not self._home_launcher.edit_mode:
            # Sideways on home is app-launch only (launcher parity) — an
            # unbound direction does nothing; the drawer is a swipe up away.
            # Bezel starts belong to lisgd (back), not launch:Files.
            if abs(vel_x) > 200:
                width = self.get_width() if hasattr(self, 'get_width') else 0
                if origin_is_screen_edge(self._swipe_origin, width):
                    return
                action = self.gesture_config.get(
                    'swipe_left_home' if vel_x < -200 else 'swipe_right_home')
                if action != 'none':
                    self._dispatch_gesture_action(action)
            return
        # Settings: a horizontal swipe is back (subpage to the category
        # menu, menu to home), so there is always a way out by gesture
        if current == 'settings' and abs(vel_x) > 200:
            self._settings_back()
            return
        idx = _PAGE_ORDER.index(current) if current in _PAGE_ORDER else 0
        if vel_x < -200 and idx < len(_PAGE_ORDER) - 1:
            self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
            self.stack.set_visible_child_name(_PAGE_ORDER[idx + 1])
        elif vel_x > 200 and idx > 0:
            self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)
            self.stack.set_visible_child_name(_PAGE_ORDER[idx - 1])

    def _on_swipe_drag_begin(self, _gesture, start_x: float,
                               start_y: float) -> None:
        from gi.repository import GLib
        now_ms = GLib.get_monotonic_time() / 1000.0
        self._swipe_origin = (start_x, start_y)
        self._swipe_drag_samples = [(now_ms, start_x, start_y)]
        self._swipe_drag_claimed = False

    def _on_swipe_drag_update(self, gesture, offset_x: float,
                              offset_y: float) -> None:
        from gi.repository import GLib
        now_ms = GLib.get_monotonic_time() / 1000.0
        if self._swipe_drag_samples:
            _t, start_x, start_y = self._swipe_drag_samples[0]
            self._swipe_drag_samples.append(
                (now_ms, start_x + offset_x, start_y + offset_y))
        if (not self._swipe_drag_claimed
                and self.stack.get_visible_child_name() == 'home'
                and should_claim_vertical_drag(offset_x, offset_y)):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self._swipe_drag_claimed = True

    def _on_swipe_drag_end(self, _gesture, offset_x: float,
                           offset_y: float) -> None:
        if not self._swipe_drag_claimed:
            return
        self._swipe_drag_claimed = False
        _vel_x, vel_y = swipe_release_velocity(self._swipe_drag_samples)
        self._swipe_drag_samples = []
        if offset_y >= 0:
            # Claimed downward drag: same action as a swipe down, with the
            # same speed floor so a slow list scroll never opens the shade.
            if vel_y > _SHORT_SWIPE_UP_VEL:
                self._dispatch_gesture_action(
                    self.gesture_config.get('swipe_down_top')
                    or 'notification_shade')
            return
        action = decide_swipe_up_action(-offset_y, vel_y)
        if action == 'drawer':
            self.stack.set_visible_child_name('apps')
        elif action == 'keyboard':
            self._show_keyboard()

    def _dispatch_gesture_action(self, action: str) -> None:
        """Run a gesture_config action, including launch:<app_id> bindings."""
        if action.startswith('launch:'):
            self._launch_app_id(action[len('launch:'):])
            return
        if action == 'notification_shade':
            self._show_shade()
        elif action == 'search':
            # Opens the drawer with keyboard focus in the search field
            self.stack.set_visible_child_name('apps')
            self.apps_search.grab_focus()
        elif action == 'home':
            self.go_home()
        elif action == 'app_switcher':
            self._show_switcher()
        elif action == 'lock_screen':
            self._show_lock_screen()
        elif action == 'settings':
            self._open_settings()
        elif action == 'dialer':
            self._open_dialer()
        elif action == 'back':
            self._handle_back()
        elif action == 'camera':
            camera = next(
                (e for e in self.app_index.entries if 'camera' in e.name.casefold()), None)
            if camera is not None:
                self._launch_entry(camera)

    def _show_focus_notice(self, label: str) -> None:
        actions = [(f'Take a break ({m} min)',
                    lambda mins=m: self._start_focus_break(mins))
                   for m in (5, 10, 15)]
        actions.append(('Turn off Focus', self._turn_off_focus))
        self.item_actions._show_action_menu(self.stack, f'{label} is paused', actions)

    def _start_focus_break(self, minutes: int) -> None:
        self.focus_state.start_break(minutes)
        self._refresh_after_item_action()
        self._show_status(f'Focus paused for {minutes} minutes.')

    def _turn_off_focus(self) -> None:
        self.focus_state.set_enabled(False)
        self._refresh_after_item_action()
        self._show_status('Focus turned off.')

    def preload_gesture_apps(self) -> None:
        """Warm the swipe-bound apps at session start so the gesture opens a
        resident process instead of cold-starting it (the camera through the
        android HAL can take a minute-plus cold). Launched apps briefly map
        above home; the delayed present_over_apps() re-raises the shell, and
        the next real launch drops it back so the warm window comes forward.
        """
        from gi.repository import Gio
        ids: list[str] = []
        for key in ('swipe_left_home', 'swipe_right_home'):
            action = self.gesture_config.get(key) or 'none'
            if action == 'camera':
                entry = next((e for e in self.app_index.entries
                              if 'camera' in e.name.casefold()), None)
                if entry:
                    ids.append(entry.app_id)
            elif action.startswith('launch:'):
                ids.append(action[len('launch:'):])
        for app_id in ids:
            try:
                info = Gio.DesktopAppInfo.new(app_id)
                if info is not None:
                    info.launch([], None)
            except Exception:
                continue
        if ids:
            GLib.timeout_add_seconds(5, self._reraise_after_preload)

    def _reraise_after_preload(self) -> bool:
        self.stack.set_visible_child_name('home')
        self.present_over_apps()
        return GLib.SOURCE_REMOVE

    def _apply_layer_policy(self) -> None:
        """Central layer/keyboard switch: editable pages and open entry
        popovers need TOP + EXCLUSIVE (phoc v3 has no ON_DEMAND), home over
        apps stays TOP + NONE, and home at rest is BOTTOM + NONE. Without
        layer-shell this is a no-op — the window is a normal window then."""
        if not (_LAYER_SHELL and LayerShell.is_supported()):
            return
        page = self.stack.get_visible_child_name()
        layer_name, kb_name = decide_layer_policy(
            page, self._popover_count > 0, self._over_apps)
        LayerShell.set_layer(self, getattr(LayerShell.Layer, layer_name))
        LayerShell.set_keyboard_mode(
            self, getattr(LayerShell.KeyboardMode, kb_name))

    def _on_item_popover(self, opened: bool) -> None:
        self._popover_count = max(0, self._popover_count + (1 if opened else -1))
        self._apply_layer_policy()

    def go_home(self) -> None:
        """Home is a third destination: hide recents/shade, raise the launcher.

        Recents swipe-down / tap-dim / back resume the current app; this
        path is the Pixel short swipe-up (and the in-shell `home` action).
        With `home_to_recents`, pressing Home while already on home with
        nothing on top opens the switcher instead.
        """
        if _home_press_to_recents(self):
            self._show_switcher()
            return
        switcher = getattr(self, '_switcher', None)
        if switcher is not None and switcher.get_visible():
            switcher.hide_switcher()
        shade = getattr(self, '_shade', None)
        if shade is not None and shade.get_visible():
            shade.hide_shade()
        self.stack.set_visible_child_name('home')
        self.present_over_apps()

    def present_over_apps(self) -> None:
        """Raise the shell above regular app windows (the go-home gesture).

        Home lives on the BOTTOM layer, so a focused app covers it; until
        phoc grows foreign-toplevel management this hop to the TOP layer is
        what makes 'swipe up → home' work while an app is open. An editable
        page keeps EXCLUSIVE so typing still works up there."""
        self._over_apps = True
        self._layer_is_top = True
        self._apply_layer_policy()

    def drop_to_background(self) -> None:
        """Return the shell to BOTTOM + NONE so launched apps show above.

        Explicit, not via the page policy: the drawer may still be open
        underneath the fresh app window, and the policy would keep the shell
        raised and keyboard-grabbing over the app. The next page change,
        popover event or present_over_apps re-applies the policy."""
        self._over_apps = False
        self._layer_is_top = False
        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.set_layer(self, LayerShell.Layer.BOTTOM)
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        GLib.timeout_add(400, self._capture_thumb_once)

    def _wire_foreign_toplevels(self, manager=None) -> None:
        """Yield to apps mapped outside the shell (D-Bus activation,
        systemd-run, notification actions): a new foreign toplevel means a
        fresh app window, whether or not the shell launched it."""
        try:
            if manager is None:
                from toplevel_manager import ToplevelManager
                manager = ToplevelManager()
            self._toplevel_manager = manager
            try:
                listed = list(manager.list())
                self._known_toplevels = {t.handle for t in listed}
                self._known_app_ids = {t.app_id for t in listed}
                self._known_generation = getattr(manager, 'generation', 0)
            except Exception:
                self._known_toplevels = set()
                self._known_app_ids = set()
                self._known_generation = getattr(manager, 'generation', 0)
            on_change = getattr(manager, 'on_change', None)
            if callable(on_change):
                on_change(self._on_foreign_toplevels)
        except Exception:
            pass

    def _on_foreign_toplevels(self) -> None:
        manager = getattr(self, '_toplevel_manager', None)
        if manager is None:
            return
        try:
            listed = list(manager.list())
            current = {t.handle for t in listed}
            current_ids = {t.app_id for t in listed}
        except Exception:
            return
        try:
            known = getattr(self, '_known_toplevels', None)
            if not isinstance(known, set):
                known = set()
            known_ids = getattr(self, '_known_app_ids', None)
            if not isinstance(known_ids, set):
                known_ids = set(current_ids)
            old_generation = getattr(self, '_known_generation', None)
            generation = getattr(manager, 'generation', 0)
            if old_generation is None:
                old_generation = generation
        except Exception:
            known = set()
            known_ids = set(current_ids)
            old_generation = getattr(manager, 'generation', 0)
            generation = old_generation
        try:
            self._known_toplevels = set(current)
            self._known_generation = generation
            # A resync clears the registry and replays every open window
            # under fresh handles: the empty clear carries the old app ids
            # over, so the replay adopts instead of yielding. An empty
            # event otherwise means everything really closed, so the
            # baseline resets and a later re-map yields.
            if current or generation == old_generation:
                self._known_app_ids = set(current_ids)
        except Exception:
            pass
        if (current - known and generation == old_generation
                and current_ids - known_ids):
            self._yield_to_foreign_app()

    def _yield_to_foreign_app(self) -> None:
        """Get out of a fresh app window's way, exactly as for a launch the
        shell made itself: home page, BOTTOM + NONE, overlays closed."""
        try:
            stack = getattr(self, 'stack', None)
            if stack is not None:
                stack.set_visible_child_name('home')
        except Exception:
            pass
        try:
            self._drawer_open_folder = None
        except Exception:
            pass
        for attr, hide in (('_shade', 'hide_shade'),
                           ('_switcher', 'hide_switcher')):
            try:
                surface = getattr(self, attr, None)
                if surface is not None and surface.get_visible():
                    getattr(surface, hide)()
            except Exception:
                pass
        try:
            self.drop_to_background()
        except Exception:
            pass
        try:
            self._apply_layer_policy()
        except Exception:
            pass

    def _launch_app_id(self, app_id: str) -> None:
        from gi.repository import Gio
        try:
            info = Gio.DesktopAppInfo.new(app_id)
        except Exception:
            info = None
        if info is None:
            # Uninstalled target — behave like 'none' per Workstream 8.1
            return
        tracker = getattr(self, 'standby', None)
        if tracker is not None:
            try:
                tracker.thaw_for_launch(app_id)
            except Exception:
                pass
        try:
            if tracker is not None:
                from app_standby import launch_with_pid
                launch_with_pid(
                    info,
                    on_pid=lambda pid: tracker.track(app_id, pid),
                )
            else:
                info.launch([], None)
            self.config.record_launch(app_id)
            self.drop_to_background()
        except GLib.Error as error:
            self._show_status(f'Failed to launch: {error.message}')

    # -- home page (home_page.py) -----------------------------------------
    def _build_home_page(self) -> Gtk.Widget:
        return _home(self)._build_home_page()

    def _open_dialer(self) -> None:
        from dialer import Dialer
        if self._dialer and self._dialer.get_visible():
            return
        d = Dialer(dnd_state=self.dnd_state, config=self.config)
        d.set_application(self.get_application())
        # Lazy mid-session construction must render the CURRENT preset, not
        # whatever the injected config resolved to at construction inside
        # the surface (apply_theme() there is only a standalone fallback).
        d.apply_theme(resolve_appearance_preset(self.config))
        d.present()
        self._dialer = d

    def _launch_slot(self, slot: dict) -> None:
        """Launch an app or command from a home slot."""
        return _home(self)._launch_slot(slot)

    def _handle_back(self) -> None:
        """Called by BackGestureLayer on edge swipe from either side."""
        import subprocess
        # Home first: collapse an open folder, then leave edit mode.
        launcher = getattr(self, '_home_launcher', None)
        if launcher is not None:
            try:
                if launcher.close_folder():
                    return
            except Exception:
                pass
            try:
                if launcher.edit_mode:
                    launcher.set_edit_mode(False)
                    return
            except Exception:
                pass
        # Overlay first (switcher sits above the shade), then the shade.
        if self._switcher and self._switcher.get_visible():
            self._switcher.hide_switcher()
            return
        if self._shade and self._shade.get_visible():
            self._shade.hide_shade()
            return
        # 3. Close dialer if open
        if self._dialer and self._dialer.get_visible():
            self._dialer.close()
            return
        # 4. Navigate back within the shell stack
        current = self.stack.get_visible_child_name()
        if current == 'settings':
            self._settings_back()
            return
        if current == 'apps':
            self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)
            self.stack.set_visible_child_name('home')
            return
        # 5. Nothing shell-owned is open — send back to whatever is focused
        env = {**os.environ, **_back_env()}
        try:
            subprocess.Popen(['wtype', '-k', 'Escape'], env=env,
                             close_fds=True, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            pass
        # Always try Android KEYCODE_BACK — exits silently if Waydroid isn't running.
        # Deliberately no status check: check_output blocks the main thread for up to 1s.
        try:
            subprocess.Popen(
                ['waydroid', 'shell', 'input', 'keyevent', '4'],
                env=env, close_fds=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            pass

    def _build_widget_block(self) -> None:
        return _home(self)._build_widget_block()

    def _current_tap(self, key: str) -> object:
        # Read at dispatch time so a hot-reloaded config takes effect
        return _home(self)._current_tap(key)

    def _wire_widget_tap(self, widget: Gtk.Widget, key: str) -> None:
        return _home(self)._wire_widget_tap(widget, key)

    def _on_clock_tapped(self, n_press: int) -> None:
        return _home(self)._on_clock_tapped(n_press)

    def _dispatch_widget_tap(self, key: str, tap: object) -> None:
        return _home(self)._dispatch_widget_tap(key, tap)

    def _launch_first_matching(self, needle: str) -> None:
        return _home(self)._launch_first_matching(needle)

    def _init_weather(self) -> None:
        return _home(self)._init_weather()

    def _tick_weather(self) -> bool:
        return _home(self)._tick_weather()

    def _refresh_weather(self, force: bool = False) -> None:
        return _home(self)._refresh_weather(force=force)

    def is_locked(self) -> bool:
        """True while the lock screen is up; IPC verbs consult this."""
        lock = getattr(self, '_lock_screen', None)
        return lock is not None and bool(lock.get_visible())

    def _show_lock_screen(self) -> None:
        self._disarm_search_keyboard()
        lock = getattr(self, '_lock_screen', None)
        if lock is not None:
            if not lock.get_visible():
                lock.prepare()
            lock.present()
            lock.set_visible(True)
            lock.on_shown()
            self._reassert_call_ui_above_lock()
            return
        from lock_screen import LockScreen
        # The app owns the shared MPRIS watcher (main.py, next to the
        # DisplayManager); the lock card renders transport-only from it.
        mpris = getattr(self.get_application(), '_mpris', None)
        self._lock_screen = LockScreen(
            on_unlock=self._dismiss_lock_screen,
            config=self.config,
            get_notifications=lambda: self._ensure_shade().notifications_snapshot(),
            dnd_active_fn=lambda: self.dnd_state.is_active(),
            on_open_shade=self._show_shade,
            media_watcher=mpris,
        )
        self._lock_screen.set_application(self.get_application())
        self._lock_screen.present()
        self._lock_screen.on_shown()
        self._reassert_call_ui_above_lock()

    def _reassert_call_ui_above_lock(self) -> None:
        """A live call stays reachable: re-present the call UI above the
        lock (both are OVERLAY; the last mapped wins)."""
        return _calls(self)._reassert_call_ui_above_lock()

    def _dismiss_lock_screen(self) -> None:
        lock = getattr(self, '_lock_screen', None)
        if lock is not None:
            lock.set_visible(False)

    def try_fingerprint_unlock(self) -> None:
        lock = getattr(self, '_lock_screen', None)
        if lock is not None and lock.get_visible():
            lock.try_fingerprint_unlock()

    def _ensure_shade(self) -> object:
        if self._shade is None:
            from notification_shade import NotificationShade
            # The app (XXWMApplication) owns the Hud; the shade is created lazily
            # after _show_shell sets _hud, so it is always present here. getattr
            # keeps this safe even if the shade is ever built before the HUD.
            app = self.get_application()
            hud = getattr(app, '_hud', None)
            # The app also owns the NotificationDaemon: passing it makes our
            # name ownership authoritative and lets shade dismissals report
            # back to sending apps.
            daemon = getattr(app, '_notif_daemon', None)
            # The app also owns the MPRIS watcher (main.py builds it next
            # to the DisplayManager); the shade card renders from it.
            mpris = getattr(app, '_mpris', None)
            self._shade = NotificationShade(
                dnd_state=self.dnd_state,
                focus_state=self.focus_state,
                on_open_settings=self.open_settings,
                on_power=self._show_power_menu,
                hud=hud,
                config=self.config,
                notif_daemon=daemon,
                media_watcher=mpris,
            )
            self._shade.set_application(self.get_application())
            # Same current-preset rule as _open_dialer: the shade can be
            # built long after startup, under a hot-reloaded theme.
            self._shade.apply_theme(resolve_appearance_preset(self.config))
        return self._shade

    def _show_shade(self) -> None:
        self._ensure_shade().show_shade()

    # -- settings page (settings_page.py) ------------------------------------
    def open_settings(self, section: str | None = None) -> None:
        """Show the settings page, scrolled to a quick-tile section.

        `section` is one of 'wifi' | 'bluetooth' | 'mobile' (or None for the
        top); unknown values open the top. The linked section also refreshes:
        a WiFi scan for 'wifi', a device list for 'bluetooth'. Raises over
        apps via _open_settings, then refreshes the linked section."""
        self._open_settings(section)
        try:
            _settings(self).open_settings(section)
        except Exception:
            pass

    def _scroll_settings_to(self, section: str | None) -> bool:
        return _settings(self)._scroll_settings_to(section)

    def _show_power_menu(self) -> None:
        # Single instance: the application owns the power menu (hardware key
        # path); the shade reuses it instead of building a second one.
        app_menu = getattr(self.get_application(), '_power_menu', None)
        if app_menu is not None:
            self._power_menu = app_menu
        elif self._power_menu is None:
            from power_menu import PowerMenu
            self._power_menu = PowerMenu(
                config=self.config, on_lock=self._show_lock_screen)
            self._power_menu.set_application(self.get_application())
            # Same current-preset rule as _open_dialer.
            self._power_menu.apply_theme(resolve_appearance_preset(self.config))
        self._power_menu.show_menu()

    def _show_keyboard(self) -> None:
        """Force the on-screen keyboard up via squeekboard's D-Bus interface."""
        _set_osk_visible(True)

    def _hide_keyboard(self) -> None:
        """Hide squeekboard when focus leaves an editable widget (20.1)."""
        _set_osk_visible(False)

    def _set_layer_keyboard_exclusive(self, exclusive: bool) -> None:
        if not (_LAYER_SHELL and LayerShell.is_supported()):
            return
        # phoc layer-shell v3 ignores ON_DEMAND. EXCLUSIVE while Search is
        # focused; NONE otherwise so the compositor does not keep the
        # launcher grabbing the keyboard (that also stalls power/lock).
        mode = (LayerShell.KeyboardMode.EXCLUSIVE if exclusive
                else LayerShell.KeyboardMode.NONE)
        LayerShell.set_keyboard_mode(self, mode)

    def _arm_search_keyboard(self) -> None:
        self._set_layer_keyboard_exclusive(True)
        self._show_keyboard()
        # Hop TOP while typing so the drawer search field stays above Colemak
        # (exclusive-zone 0 on BOTTOM is the intent; the hop is the fallback
        # when phoc still covers the field). Restore BOTTOM on dismiss unless
        # Settings already owns the TOP layer.
        if not getattr(self, '_layer_is_top', False):
            self._search_restore_bottom = True
            self.present_over_apps()

    def _disarm_search_keyboard(self) -> None:
        self._set_layer_keyboard_exclusive(False)
        self._hide_keyboard()
        if (getattr(self, '_search_restore_bottom', False)
                and not getattr(self, '_raised_for_settings', False)):
            self.drop_to_background()
        self._search_restore_bottom = False

    def _on_entry_keyboard(self, show: bool) -> None:
        if show:
            self._arm_search_keyboard()
        else:
            self._disarm_search_keyboard()

    def _attach_osk(self, widget: Gtk.Widget) -> None:
        """Raise squeekboard when an Entry on this layer-shell window is tapped."""
        from osk import attach
        attach(widget, on_show=self._arm_search_keyboard)

    def _on_search_pressed(self, gesture: Gtk.GestureClick, _n_press: int,
                           _x: float, _y: float) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.apps_search.grab_focus()
        self._arm_search_keyboard()

    def _widget_is_search_or_editable(self, widget) -> bool:
        search = getattr(self, 'apps_search', None)
        while widget is not None:
            if search is not None and widget is search:
                return True
            if isinstance(widget, Gtk.Editable):
                return True
            widget = widget.get_parent() if hasattr(widget, 'get_parent') else None
        return False

    def _on_tap_outside(self, _gesture: Gtk.GestureClick, _n_press: int,
                        x: float, y: float) -> None:
        """Hide squeekboard when a tap lands outside any editable widget.

        The shell's only editable widget is the drawer search entry; a tap
        anywhere else (home, the app list, settings) means the user is done
        typing, so drop the OSK instead of leaving it floating (20.1). This
        lives in the shell's focus handling, not per-surface hacks.
        """
        try:
            widget = self.stack.pick(x, y, Gtk.PickFlags.DEFAULT)
        except Exception as error:
            # pick() failed — we cannot tell what the tap landed on, so do not
            # guess by dismissing the OSK. Log it instead of masking the fault.
            from shell_log import get_logger
            get_logger('window').warning(
                'tap-outside pick(%s, %s) failed: %s', x, y, error)
            return
        if self._widget_is_search_or_editable(widget):
            return
        self._set_layer_keyboard_exclusive(False)
        self._hide_keyboard()

    def _open_settings(self, section: str | None = None) -> None:
        """Show the in-shell Settings page above any focused app.

        Settings is a stack page of the BOTTOM launcher window. present()
        alone leaves it under the app; hopping to TOP is what makes the
        shade Settings button (and the drawer row) actually appear.
        Leaving settings drops back to BOTTOM so the user is not trapped
        on a TOP overlay that needs a hard reboot to escape.
        """
        self._disarm_search_keyboard()
        self._raised_for_settings = True
        self.stack.set_visible_child_name('settings')
        self.present_over_apps()
        if section is not None:
            try:
                self._scroll_settings_to(section)
            except Exception:
                pass

    def _leave_settings(self) -> None:
        self._disarm_search_keyboard()
        if self.stack.get_visible_child_name() == 'settings':
            self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)
            self.stack.set_visible_child_name('home')
        # The next visit starts at the category menu.
        inner = getattr(self, '_settings_stack', None)
        if inner is not None:
            inner.set_visible_child_name('menu')
        if getattr(self, '_raised_for_settings', False):
            self._raised_for_settings = False
            self.drop_to_background()

    def _ensure_toplevel_manager(self):
        if self._toplevel_manager is None:
            from toplevel_manager import ToplevelManager
            self._toplevel_manager = ToplevelManager()
        return self._toplevel_manager

    def _show_switcher(self) -> None:
        self._disarm_search_keyboard()
        if self._switcher is not None and self._switcher.get_visible():
            self._switcher.hide_switcher()
            return
        manager = self._ensure_toplevel_manager()
        # Re-bind each open: a live foreign-toplevel client can stop seeing
        # new windows after the first snapshot, so recents goes empty while
        # Calculator is still on screen. A fresh bind matches a standalone
        # client and lists what is actually open.
        try:
            manager.resync()
        except Exception:
            pass
        if self._switcher is None:
            from app_switcher import AppSwitcher
            self._switcher = AppSwitcher(
                manager=manager, config=self.config,
                thumbnails=self._ensure_thumbs(),
                on_activated=self.drop_to_background,
            )
            self._switcher.set_application(self.get_application())
            self._switcher.apply_theme(resolve_appearance_preset(self.config))
        else:
            try:
                self._switcher.attach_manager(manager)
            except Exception:
                pass
        from shell_log import get_logger
        try:
            get_logger('window').info(
                'showing switcher (%d toplevels, available=%s)',
                len(manager.list()), manager.available)
        except Exception:
            pass
        self._switcher.show_switcher()
        GLib.timeout_add(50, self._refresh_switcher_if_visible)
        GLib.timeout_add(200, self._refresh_switcher_if_visible)

    def _refresh_switcher_if_visible(self) -> bool:
        switcher = self._switcher
        try:
            visible = switcher is not None and switcher.get_visible()
        except Exception:
            return False
        if not visible:
            return False
        manager = self._toplevel_manager
        from shell_log import get_logger
        try:
            n = len(manager.list()) if manager is not None else -1
            avail = manager.available if manager is not None else False
            get_logger('window').info(
                'switcher refresh (%d toplevels, available=%s)', n, avail)
        except Exception:
            pass
        try:
            switcher.refresh()
        except Exception:
            pass
        return False

    # -- app drawer (drawer_page.py) ------------------------------------------
    def _build_apps_page(self) -> Gtk.Widget:
        return _drawer(self)._build_apps_page()

    def _toggle_sort_mode(self, _btn: Gtk.Button) -> None:
        return _drawer(self)._toggle_sort_mode(_btn)

    def _home_slot_app_ids(self) -> set[str]:
        return _drawer(self)._home_slot_app_ids()

    def _drawer_folder_slots(self) -> list[tuple[int, dict, list[tuple[int, dict]]]]:
        """Folder slots the drawer shows, in home-slot order (folders first).

        Members keep their original folder-list indices so the shared member
        menu edits the right entry; members whose app is not installed are
        skipped at render, and empty folders never appear.
        """
        return _drawer(self)._drawer_folder_slots()

    @staticmethod
    def _install_time(entry: AppEntry) -> float:
        from drawer_page import DrawerPage
        return DrawerPage._install_time(entry)

    def _on_alpha_jump(self, _btn: Gtk.Button, letter: str) -> None:
        return _drawer(self)._on_alpha_jump(_btn, letter)

    def _on_search_focus(self, entry: Gtk.SearchEntry, _pspec=None) -> None:
        # GTK4 SearchEntry focus is on the inner GtkText; has-focus() on the
        # entry is often False even while the user is typing. Only ARM here:
        # a False notify would hide the OSK on the same tap that opened it.
        contains = getattr(entry, 'contains_focus', None)
        focused = bool(contains()) if callable(contains) else bool(entry.has_focus())
        if focused:
            self._arm_search_keyboard()

    def _on_stack_page_changed(self, stack: Gtk.Stack, _param: object) -> None:
        name = stack.get_visible_child_name()
        # Leaving the drawer disarms both pick modes so a later tap launches
        if name != 'apps':
            self._pick_slot_mode = False
            self._pick_slot_target = None
            self._pick_gesture_key = None
            self._pick_widget_tap = None
            self._pick_exempt_mode = False
            try:
                self._disarm_search_keyboard()
            except Exception:
                pass
        if name != 'settings' and getattr(self, '_raised_for_settings', False):
            self._raised_for_settings = False
            try:
                self.drop_to_background()
            except Exception:
                pass
        try:
            self._apply_layer_policy()
        except Exception:
            pass

    def _gesture_binding_text(self, key: str) -> str:
        return _settings(self)._gesture_binding_text(key)

    def _refresh_gesture_labels(self) -> None:
        return _settings(self)._refresh_gesture_labels()

    def _build_gestures_card(self) -> Gtk.Widget:
        return _settings(self)._build_gestures_card()

    def _pick_system_gesture(self, anchor: Gtk.Widget, key: str, title: str) -> None:
        return _settings(self)._pick_system_gesture(anchor, key, title)

    def _set_system_gesture(self, key: str, value: str) -> None:
        self.gesture_config.set(key, value)
        self._refresh_gesture_labels()
        self._restart_system_gestures()

    def _reset_system_gesture(self, key: str) -> None:
        self.gesture_config.reset(key)
        self._refresh_gesture_labels()
        self._restart_system_gestures()

    def _restart_system_gestures(self) -> None:
        self._sync_system_gestures(notify=True)

    def _sync_system_gestures(self, notify: bool = False) -> bool:
        from gesture_config import GestureConfig
        if not isinstance(self.gesture_config, GestureConfig):
            return False
        import shutil
        from gesture_bindings import restart_lisgd
        ipc = shutil.which('xx-wm-ipc') or '/usr/bin/xx-wm-ipc'
        bindir = str(Path(ipc).parent)
        try:
            ok = restart_lisgd(bindir, self.gesture_config)
        except Exception:
            ok = False
        if notify and getattr(self, 'status_label', None) is not None:
            try:
                self._show_status(
                    'System gestures updated.' if ok
                    else 'System gestures apply at the next session.')
            except Exception:
                pass
        return False

    def _pick_gesture_app(self, key: str, title: str) -> None:
        return _settings(self)._pick_gesture_app(key, title)

    def _clear_gesture_app(self, key: str) -> None:
        return _settings(self)._clear_gesture_app(key)

    def _build_settings_page(self) -> Gtk.Widget:
        return _settings(self)._build_settings_page()

    def _settings_back(self) -> None:
        """Back inside Settings: a subpage returns to the category menu,
        the menu leaves Settings."""
        return _settings(self).back()

    def _build_appearance_card(self) -> Gtk.Widget:
        """Theme picker: every preset plus custom as swatches — a 40 px
        rounded square of the preset background with an "Aa" in its
        foreground, the name captioned under it, the selected one ringed.
        Tapping applies live; Custom opens a hex-entry dialog. The
        Appearance settings section gives it its caps label; the grid
        itself sits directly on the page, not in a bordered card."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.set_name('appearance_card')
        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        grid.set_column_homogeneous(True)
        self._theme_buttons = {}
        # Display-level, installed once: a widget-context provider never
        # reached the swatch boxes (they rendered unpainted), and the
        # settings page can be rebuilt — reuse, never accumulate.
        provider = getattr(self, '_swatch_provider', None)
        if provider is None:
            provider = Gtk.CssProvider()
            self._swatch_provider = provider
            display = Gdk.Display.get_default()
            if display is not None:
                Gtk.StyleContext.add_provider_for_display(
                    display, provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)
        keys = list(THEME_PRESETS) + ['custom']
        for index, key in enumerate(keys):
            btn = Gtk.Button()
            btn.add_css_class('theme-swatch')
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            inner.set_halign(Gtk.Align.CENTER)
            swatch = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            swatch.set_size_request(40, 40)
            swatch.set_halign(Gtk.Align.CENTER)
            swatch.add_css_class('swatch')
            swatch.add_css_class(f'swatch-{key}')
            glyph = Gtk.Label(label='Aa')
            glyph.set_halign(Gtk.Align.CENTER)
            glyph.set_valign(Gtk.Align.CENTER)
            glyph.set_hexpand(True)
            glyph.set_vexpand(True)
            swatch.append(glyph)
            name = 'Custom' if key == 'custom' else THEME_PRESETS[key].name
            caption = Gtk.Label(label=name)
            caption.add_css_class('dim-label')
            caption.add_css_class('swatch-cap')
            caption.set_max_width_chars(10)
            # Preset names are short: unconditional ellipsize misfires on
            # fractional rounding and shows "Graphit…" for fitting text.
            if len(name) > 10:
                caption.set_ellipsize(Pango.EllipsizeMode.END)
            else:
                caption.set_ellipsize(Pango.EllipsizeMode.NONE)
            inner.append(swatch)
            inner.append(caption)
            btn.set_child(inner)
            if key == 'custom':
                self._custom_swatch_btn = btn
            btn.connect('clicked', lambda _b, k=key: self._on_swatch_picked(k))
            self._theme_buttons[key] = btn
            grid.attach(btn, index % 3, index // 3, 1, 1)
        card.append(grid)
        self._refresh_theme_buttons()
        return card

    def _refresh_theme_buttons(self) -> None:
        current = str(self.config.data.get('theme', DEFAULT_CONFIG['theme']))
        resolved = resolve_appearance_preset(self.config)
        custom_bg = getattr(self.config, 'custom_background', None)
        rules = [
            '.theme-swatch { background: transparent; border: none; '
            'box-shadow: none; padding: 4px; }',
            '.swatch { border-radius: 12px; }',
            '.swatch-cap { font-size: 11px; }',
        ]
        for key, preset in THEME_PRESETS.items():
            rules.append(
                f".swatch-{key} {{ background: {preset.background}; "
                f"color: {preset.foreground}; }}")
        rules.append(
            f".swatch-custom {{ background: {custom_bg or resolved.background}; "
            f"color: {resolved.foreground}; }}")
        rules.append(
            '.theme-swatch.sel .swatch { box-shadow: 0 0 0 2px '
            f'{resolved.foreground}; }}')
        provider = getattr(self, '_swatch_provider', None)
        if provider is not None:
            provider.load_from_data('\n'.join(rules).encode('utf-8'))
        for key, btn in self._theme_buttons.items():
            if key == current:
                btn.add_css_class('sel')
            else:
                btn.remove_css_class('sel')

    def _on_swatch_picked(self, key: str) -> None:
        if key == 'custom':
            self._show_custom_theme_dialog()
            return
        self._on_theme_picked(key)

    def _show_custom_theme_dialog(self) -> None:
        anchor = getattr(self, '_custom_swatch_btn', None) or self.stack
        current = getattr(self.config, 'custom_background', None) or ''

        def _commit(text: str) -> None:
            if not self.config.set_custom_background(text):
                self._show_status('Use a hex color like #3a2a10.')
                return
            channel = text.lstrip('#')
            red, green, blue = (int(channel[i:i + 2], 16) for i in (0, 2, 4))
            self.config.set_prefer_dark(
                (0.299 * red + 0.587 * green + 0.114 * blue) / 255 < 0.5)
            self._refresh_theme_buttons()
            self._apply_theme()
            self._retheme_surfaces()

        self.item_actions._show_entry_dialog(
            anchor, 'Custom color', current, 'Apply', _commit)

    def _on_theme_picked(self, key: str) -> None:
        self.config.set_theme(key)
        preset = THEME_PRESETS.get(key)
        if preset is not None:
            bg = preset.background.lstrip('#')
            if len(bg) == 6:
                r, g, b = (int(bg[i:i + 2], 16) for i in (0, 2, 4))
                luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
                self.config.set_prefer_dark(luminance < 0.5)
        self._refresh_theme_buttons()
        self._apply_theme()
        self._retheme_surfaces()

    # -- Developer section: wireless debugging (xxdb) ----------------------

    def _build_developer_card(self) -> Gtk.Widget:
        return _settings(self)._build_developer_card()

    def _refresh_xxdb_section(self) -> None:
        return _settings(self)._refresh_xxdb_section()

    def _on_xxdb_switch_flipped(self, switch, _pspec) -> None:
        return _settings(self)._on_xxdb_switch_flipped(switch, _pspec)

    def _on_xxdb_pair_clicked(self) -> None:
        return _settings(self)._on_xxdb_pair_clicked()

    def _on_xxdb_forget(self, fingerprint: str) -> None:
        return _settings(self)._on_xxdb_forget(fingerprint)

    def _on_xxdb_confirm(self, token: str, allow: bool) -> None:
        return _settings(self)._on_xxdb_confirm(token, allow)


    # -- system settings sections (design.md "Settings scope") -------------

    @staticmethod
    def _run_bg(work, on_done) -> None:
        from settings_page import SettingsPage
        return SettingsPage._run_bg(work, on_done)

    @staticmethod
    def _replace_box_children(box: Gtk.Box, children: list[Gtk.Widget]) -> None:
        from settings_page import SettingsPage
        return SettingsPage._replace_box_children(box, children)

    def _section_placeholder(self, text: str) -> Gtk.Label:
        return _settings(self)._section_placeholder(text)

    def _refresh_system_sections(self) -> None:
        return _settings(self)._refresh_system_sections()

    def _show_battery(self, status: str | None) -> None:
        return _settings(self)._show_battery(status)

    def _show_sinks(self, sinks) -> None:
        return _settings(self)._show_sinks(sinks)

    def _on_sink_selected(self, sink) -> None:
        return _settings(self)._on_sink_selected(sink)

    def _show_sources(self, sources) -> None:
        return _settings(self)._show_sources(sources)

    def _on_source_selected(self, source) -> None:
        return _settings(self)._on_source_selected(source)

    def _refresh_wifi(self) -> None:
        return _settings(self)._refresh_wifi()

    def _show_wifi(self, networks) -> None:
        return _settings(self)._show_wifi(networks)

    def _on_wifi_selected(self, net, widget: Gtk.Widget) -> None:
        return _settings(self)._on_wifi_selected(net, widget)

    def _on_saved_wifi_selected(self, ssid: str, widget: Gtk.Widget) -> None:
        return _settings(self)._on_saved_wifi_selected(ssid, widget)

    def _on_add_hidden(self) -> None:
        return _settings(self)._on_add_hidden()

    def _on_wifi_switch_flipped(self, switch, pspec) -> None:
        return _settings(self)._on_wifi_switch_flipped(switch, pspec)

    def _on_start_network_service(self) -> None:
        return _settings(self)._on_start_network_service()

    def _connect_wifi(self, ssid: str, password: str | None,
                      security: str | None = None,
                      hidden: bool = False) -> None:
        return _settings(self)._connect_wifi(ssid, password, security,
                                             hidden)

    def _refresh_bluetooth(self, scan: bool = False) -> None:
        return _settings(self)._refresh_bluetooth(scan)

    def _show_bt_devices(self, devices) -> None:
        return _settings(self)._show_bt_devices(devices)

    def _on_bt_selected(self, dev) -> None:
        return _settings(self)._on_bt_selected(dev)

    def _on_bt_switch_flipped(self, switch, pspec) -> None:
        return _settings(self)._on_bt_switch_flipped(switch, pspec)

    def _on_bt_unblock(self) -> None:
        return _settings(self)._on_bt_unblock()

    def _on_start_bluetooth_service(self) -> None:
        return _settings(self)._on_start_bluetooth_service()

    def _apply_theme(self, preset: ThemePreset | None = None) -> None:
        from font_theme import apply_global_font
        from home_launcher import theme_css as home_theme_css
        apply_global_font(self.config.font_family)
        theme = (preset if preset is not None
                 else resolve_appearance_preset(self.config))
        style_manager = Adw.StyleManager.get_default()
        if preset is not None:
            red, green, blue = (int(theme.background[i:i + 2], 16)
                                for i in (1, 3, 5))
            dark = (0.299 * red + 0.587 * green + 0.114 * blue) / 255 < 0.5
        else:
            dark = effective_dark(self.config)
        if dark:
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        else:
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)

        base = _xx_render(self.config, theme)
        shell = _SHELL_SHEET_TEMPLATE.substitute(
            background=theme.background,
            surface=theme.surface,
            surface_alt=theme.surface_alt,
            border=theme.border,
            foreground=theme.foreground,
            muted=theme.muted,
            accent=theme.accent,
        )
        scale = self.config.text_size_scale
        import settings_ui
        css = (base + '\n' + shell + '\n'
               + home_theme_css(theme, scale) + '\n'
               + launcher_text_css(scale) + '\n'
               + settings_ui.css(theme, scale))
        self.theme_provider.load_from_data(css.encode('utf-8'))

    def _refresh_clock(self) -> None:
        return _home(self)._refresh_clock()

    def _tick_clock(self) -> bool:
        return _home(self)._tick_clock()

    def _refresh_status(self) -> None:
        return _home(self)._refresh_status()

    def set_privacy_snapshot(self, snapshot: dict | None) -> None:
        """Feed the privacy monitor snapshot; the status strip gains the
        `● mic · cam · loc` tail while something is in use."""
        return _home(self).set_privacy_snapshot(snapshot)

    def _tick_status(self) -> bool:
        return _home(self)._tick_status()

    def _refresh_index(self, _button: Gtk.Button | None = None) -> None:
        self.app_index.refresh()
        self._sync_home_labels_from_index()
        self._home_launcher.refresh()
        self._populate_apps(self.apps_search.get_text())
        self._show_status('Application index rebuilt.')

    def _populate_apps(self, query: str = '') -> None:
        return _drawer(self)._populate_apps(query)

    def _scroll_apps_to_top(self) -> bool:
        return _drawer(self)._scroll_apps_to_top()

    def _refresh_after_item_action(self) -> None:
        # Keep any open folder drop-down open, mirroring the Android launcher
        self._home_launcher.refresh(preserve_folder=True)
        self._populate_apps(self.apps_search.get_text())

    def open_app_page(self, page: str, entry: AppEntry) -> bool:
        """Open the existing per-app settings page for an App info button."""
        from app_item_actions import open_app_page
        return open_app_page(self, page, entry)

    def _mute_app_notifications(self, app_id: str) -> None:
        """Disable-for… also clears the app's live notifications: the
        shade drops its rows silently, then the daemon reports each one
        closed (NotificationClosed reason 3) to the sending app."""
        shade = getattr(self, '_shade', None)
        if shade is None:
            return
        try:
            ids = shade.close_app_notifications(app_id)
        except Exception:
            return
        if not ids:
            return
        daemon = getattr(self.get_application(), '_notif_daemon', None)
        if daemon is None:
            return
        for notif_id in ids:
            try:
                daemon.close_for_mute(notif_id)
            except Exception:
                pass

    # -- home edit mode (home_page.py) -----------------------------------------

    def _on_home_long_press(self, gesture: Gtk.GestureLongPress, _x: float, _y: float) -> None:
        return _home(self)._on_home_long_press(gesture, _x, _y)

    def _on_home_background_tap(self, n_press: int) -> None:
        return _home(self)._on_home_background_tap(n_press)

    def _on_edit_action(self, action: str, widget: Gtk.Widget) -> None:
        return _home(self)._on_edit_action(action, widget)

    def _add_app_slot(self, entry: AppEntry) -> None:
        return _home(self)._add_app_slot(entry)

    def _create_folder_slot(self, name: str) -> None:
        return _home(self)._create_folder_slot(name)

    def _move_slot(self, idx: int, delta: int) -> None:
        return _home(self)._move_slot(idx, delta)

    def _remove_slot(self, idx: int, widget: Gtk.Widget | None = None) -> None:
        return _home(self)._remove_slot(idx, widget)

    def _rename_slot(self, widget: Gtk.Widget, idx: int) -> None:
        return _home(self)._rename_slot(widget, idx)

    def _replace_app_slot(self, idx: int, entry: AppEntry) -> None:
        return _home(self)._replace_app_slot(idx, entry)

    def _make_drawer_folder_row(self, slot: dict, slot_index: int) -> Gtk.ListBoxRow:
        return _drawer(self)._make_drawer_folder_row(slot, slot_index)

    def _make_drawer_member_row(self, slot_index: int, member_index: int, member: dict) -> Gtk.ListBoxRow:
        return _drawer(self)._make_drawer_member_row(
            slot_index, member_index, member)

    def _toggle_drawer_folder(self, slot_index: int) -> None:
        return _drawer(self)._toggle_drawer_folder(slot_index)

    def _toggle_hidden(self) -> None:
        return _drawer(self)._toggle_hidden()

    def _center_drawer_folder(self) -> bool:
        return _drawer(self)._center_drawer_folder()

    def _add_long_press(self, widget: Gtk.Widget, callback) -> None:
        return _drawer(self)._add_long_press(widget, callback)

    def _make_settings_row(self) -> Gtk.ListBoxRow:
        return _drawer(self)._make_settings_row()

    def _make_web_row(self, query: str) -> Gtk.ListBoxRow:
        return _drawer(self)._make_web_row(query)

    def _replace_rows(
        self,
        list_box: Gtk.ListBox,
        entries: list[AppEntry],
        empty_message: str,
        row_maker: object = None,
    ) -> None:
        return _drawer(self)._replace_rows(
            list_box, entries, empty_message, row_maker)

    def _on_key_pressed(self, _ctrl: Gtk.EventControllerKey, keyval: int, *_) -> bool:
        # Power button → force redraw to wake display if HWComposer idled
        if keyval in (Gdk.KEY_PowerOff, 0x1008ff2a, 0x1008ff18):
            self.queue_draw()
            return True
        return False

    def _setup_app_monitor(self) -> None:
        """Live drawer: Gio.AppInfoMonitor fires when apps install, update
        or vanish; debounced into a silent index rebuild (no manual
        refresh, no toast). The monitor reference is held so the signal
        stays connected for the window lifetime."""
        self._app_monitor = None
        self._app_refresh_timer: int | None = None
        try:
            from gi.repository import Gio
            monitor_cls = getattr(Gio, 'AppInfoMonitor', None)
            if monitor_cls is None:
                return
            monitor = monitor_cls.get()
            monitor.connect('changed', self._on_app_info_changed)
            self._app_monitor = monitor
        except Exception:
            self._app_monitor = None

    def _on_app_info_changed(self, _monitor) -> None:
        if self._app_refresh_timer is not None:
            GLib.source_remove(self._app_refresh_timer)
        self._app_refresh_timer = GLib.timeout_add(500, self._reload_app_index)

    def _reload_app_index(self) -> bool:
        self._app_refresh_timer = None
        try:
            previous = {entry.app_id for entry in self.app_index.entries}
            self.app_index.refresh()
            current = {entry.app_id for entry in self.app_index.entries}
            if current:
                # A real uninstall: present before, gone from a non-empty
                # rescan. A transient empty scan purges nothing.
                removed = previous - current
                if removed:
                    _purge_removed_from_config(self.config, removed)
            _sync_home_labels(self.config, self.app_index.entries)
            self._home_launcher.refresh(preserve_folder=True)
            self._populate_apps(self.apps_search.get_text())
        except Exception:
            pass
        return GLib.SOURCE_REMOVE

    def _sync_home_labels_from_index(self) -> None:
        """Refresh never-renamed slot/member labels from the desktop index."""
        try:
            _sync_home_labels(self.config, self.app_index.entries)
        except Exception:
            pass

    def _purge_uninstalled_apps(self, removed: set[str]) -> None:
        """Drop uninstalled app ids from the home config (monitor path)."""
        try:
            _purge_removed_from_config(self.config, removed)
        except Exception:
            pass

    def _setup_config_monitor(self) -> None:
        """Hot reload: editing ~/.config/xx-wm/* in a terminal is a
        first-class workflow (design.md "Settings scope"). Debounced; invalid
        files keep the last good config."""
        from gi.repository import Gio
        self._config_reload_timer: int | None = None
        self._config_monitors = []
        gestures_path = self.config.config_dir / 'gestures.json'
        for path in (self.config.config_path, gestures_path):
            try:
                monitor = Gio.File.new_for_path(str(path)).monitor_file(
                    Gio.FileMonitorFlags.NONE, None)
                monitor.connect('changed', self._on_config_file_changed)
                self._config_monitors.append(monitor)
            except GLib.Error:
                pass

    def _on_config_file_changed(self, _monitor, _file, _other, event) -> None:
        from gi.repository import Gio
        if event not in (Gio.FileMonitorEvent.CHANGED,
                         Gio.FileMonitorEvent.CHANGES_DONE_HINT,
                         Gio.FileMonitorEvent.CREATED,
                         Gio.FileMonitorEvent.RENAMED):
            return
        if self._config_reload_timer is not None:
            GLib.source_remove(self._config_reload_timer)
        self._config_reload_timer = GLib.timeout_add(300, self._reload_config)

    def _reload_config(self) -> bool:
        self._config_reload_timer = None
        import json
        loaded: dict | None = None
        try:
            if self.config.config_path.exists():
                loaded = json.loads(self.config.config_path.read_text(encoding='utf-8'))
                if not isinstance(loaded, dict):
                    raise ValueError('config root must be an object')
        except (OSError, json.JSONDecodeError, ValueError) as error:
            from shell_log import get_logger
            get_logger('window').warning('config reload skipped: %s', error)
            return GLib.SOURCE_REMOVE
        # Gestures re-read is cheap and side-effect free
        self.gesture_config = GestureConfig()
        # Skip the UI rebuild when the change was our own save
        if loaded is not None and loaded == self.config.data:
            return GLib.SOURCE_REMOVE
        self.config.data = dict(DEFAULT_CONFIG)
        self.config.load()
        self._apply_config_change()
        return GLib.SOURCE_REMOVE

    def _apply_config_change(self) -> None:
        """Re-apply everything a config edit can influence."""
        self._apply_theme()
        self._build_widget_block()
        self._home_launcher.refresh()
        self._populate_apps(self.apps_search.get_text())
        self._refresh_weather()
        self._resync_blank_lock()
        self._retheme_surfaces()

    def _retheme_surfaces(self) -> None:
        """Fan the freshly resolved preset out to every constructed overlay
        surface. They draw outside .shell-root with their own display-level
        providers, so a hot-reloaded theme edit must reach each one; fonts
        already propagate display-wide via font_theme's global provider."""
        preset = resolve_appearance_preset(self.config)
        try:
            surfaces = (self._shade, self._dialer, self._call_ui,
                        self._call_bar, self._power_menu, self._switcher)
        except AttributeError:
            surfaces = tuple(getattr(self, attr, None) for attr in (
                '_shade', '_dialer', '_call_ui', '_call_bar',
                '_power_menu', '_switcher'))
        for surface in surfaces:
            if surface is not None:
                surface.apply_theme(preset)
        lock = getattr(self, '_lock_screen', None)
        if lock is not None:
            lock.apply_theme(preset)
        back = getattr(self, '_back_layer', None)
        if back is not None:
            back.apply_theme(preset)
        # The HUD lives on the application (main.py wires it into the shade
        # from there); getattr-guarded so bare test namespaces still fan out.
        get_app = getattr(self, 'get_application', None)
        app = get_app() if callable(get_app) else None
        if app is not None:
            hud = getattr(app, '_hud', None)
            if hud is not None:
                hud.apply_theme(preset)
            banner = getattr(app, '_banner', None)
            if banner is not None:
                banner.apply_theme(preset)
            # The application owns the single power menu instance; the
            # window reuses it instead of building a second one. Fan out
            # here only when it is not the window's own menu (which the
            # loop above already themed): same instance, themed once.
            for menu in (getattr(app, '_power_menu', None),):
                if (menu is not None
                        and menu is not getattr(self, '_power_menu', None)):
                    menu.apply_theme(preset)
        try:
            self._refresh_theme_buttons()
        except Exception:
            pass

    def on_blank_changed(self, blanked: bool) -> None:
        """DisplayManager callback: the screen locks after
        auto_lock_timeout seconds of being blanked (0 = on blank), and a
        wake always cancels the pending lock."""
        self._blanked = bool(blanked)
        self._cancel_pending_lock()
        if blanked:
            self._schedule_blank_lock()

    def _resync_blank_lock(self) -> None:
        """Re-evaluate the blank-driven lock after a config change: a new
        auto_lock_timeout applies to the current blank state."""
        self.on_blank_changed(self._blanked)

    def _cancel_pending_lock(self) -> None:
        if self._lock_src is not None:
            GLib.source_remove(self._lock_src)
            self._lock_src = None

    def _schedule_blank_lock(self) -> None:
        if self._call_live():
            return
        timeout = self.config.auto_lock_timeout
        if timeout <= 0:
            self._show_lock_screen()
            return
        self._lock_src = GLib.timeout_add_seconds(
            timeout, self._on_blank_lock_timeout)

    def _on_blank_lock_timeout(self) -> bool:
        self._lock_src = None
        if self._blanked and not self._call_live():
            self._show_lock_screen()
        return False

    def _call_live(self) -> bool:
        """True while any tracked call is non-terminated (ringing, dialing,
        active, or held) — the phone must never auto-lock under it."""
        return _calls(self)._call_live()

    def _on_apn_save(self, values: dict) -> None:
        return _settings(self)._on_apn_save(values)

    @staticmethod
    def _gsm_connection_name() -> str:
        from settings_page import SettingsPage
        return SettingsPage._gsm_connection_name()

    def _apply_apn(self, apn: str, user: str, pwd: str) -> None:
        return _settings(self)._apply_apn(apn, user, pwd)

    # -- call wiring (call_wiring.py) ------------------------------------------
    def _ensure_call_surfaces(self) -> tuple:
        return _calls(self)._ensure_call_surfaces()

    def silence_ringer(self) -> None:
        """Power-key press while ringing only silences the ringer: the call
        stays live on the surface. Falls back to the sound loop when the
        surface (or its ringer) is not up yet."""
        return _calls(self).silence_ringer()

    def _on_call_incoming(self, caller: str, number: str) -> None:
        return _calls(self)._on_call_incoming(caller, number)

    def _sync_call_inhibit(self, calls: list) -> None:
        """Hold the display awake while any call is non-terminated; the
        blank-driven auto-lock consults the same snapshot and stands down."""
        return _calls(self)._sync_call_inhibit(calls)

    def _on_calls_changed(self, calls: list) -> None:
        return _calls(self)._on_calls_changed(calls)

    def _on_call_answered(self, caller: str, number: str) -> None:
        return _calls(self)._on_call_answered(caller, number)

    def _on_call_ended(self) -> None:
        return _calls(self)._on_call_ended()

    def _on_sms_received(self, number: str, text: str) -> None:
        return _calls(self)._on_sms_received(number, text)

    def _expand_call_ui(self) -> None:
        # CallBar's expand button: re-present the full call surface
        return _calls(self)._expand_call_ui()

    def _make_app_row(self, entry: AppEntry) -> Gtk.ListBoxRow:
        return _drawer(self)._make_app_row(entry)

    def _on_drawer_uninstall(self, anchor: Gtk.Widget,
                             entry: AppEntry) -> None:
        """Drawer long-press Uninstall: the single helper-path flow."""
        import settings_apps

        settings_apps.begin_uninstall(self, anchor, entry)

    def _launch_entry(self, entry: AppEntry) -> None:
        return _drawer(self)._launch_entry(entry)

    def _on_apps_search_changed(self, entry: Gtk.SearchEntry) -> None:
        return _drawer(self)._on_apps_search_changed(entry)

    def _on_apps_search_activate(self, entry: Gtk.SearchEntry) -> None:
        return _drawer(self)._on_apps_search_activate(entry)

    def _open_web_search(self, query: str) -> None:
        return _drawer(self)._open_web_search(query)

    def _on_update_clicked(self, _btn: Gtk.Button | None = None) -> None:
        return _settings(self)._on_update_clicked(_btn)

    def _on_update_now(self, _btn: Gtk.Button | None = None) -> None:
        return _settings(self)._on_update_now(_btn)

    def _on_dots_update(self, _btn: Gtk.Button | None = None) -> None:
        return _settings(self)._on_dots_update(_btn)

    def _refresh_apps_section(self) -> None:
        return _settings(self)._refresh_apps_section()

    def _on_check_updates_clicked(self, _btn: Gtk.Button) -> None:
        return _settings(self)._on_check_updates_clicked(_btn)

    def _on_manual_check_result(self, count: int | None) -> None:
        return _settings(self)._on_manual_check_result(count)

    def _on_updates_available(self, count: int) -> None:
        return _settings(self)._on_updates_available(count)

    def _snooze_updates(self) -> None:
        return _settings(self)._snooze_updates()

    def _show_status(self, message: str) -> None:
        self.status_label.set_text(message)
        self.toast_overlay.add_toast(Adw.Toast.new(message))
    def _on_export_backup(self) -> None:
        return _settings(self)._on_export_backup()

    def _on_restore_backup(self, anchor: Gtk.Widget | None = None) -> None:
        # A file chooser is a normal toplevel: it would open UNDER this
        # layer-shell TOP surface. Restore picks from an in-shell menu of
        # the xx-wm-backup-*.json files in ~ and ~/Documents instead.
        return _settings(self)._on_restore_backup(anchor)

    def _confirm_restore_backup(self, anchor: Gtk.Widget | None,
                                path: Path, label: str) -> None:
        return _settings(self)._confirm_restore_backup(anchor, path, label)

    def _restore_from_path(self, filepath: str | None) -> None:
        return _settings(self)._restore_from_path(filepath)

    def _get_device_name(self) -> str:
        return _settings(self)._get_device_name()

