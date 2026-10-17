from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import NamedTuple, Callable

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import Gdk, GLib, Gio, Gtk
from als_brightness import ALSBrightness, apply_backlight_pct
import backlight
from config import ShellConfig, ThemePreset


def _spawn_worker_thread(target: Callable[[], None]):
    """Run target off the GTK main thread; never raises.

    Slow D-Bus probes and subprocess reads belong here. Results must come
    back via _post_to_main — GTK widgets stay on the main thread.
    """
    try:
        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        return thread
    except Exception:
        try:
            target()
        except Exception:
            pass
        return None


def _post_to_main(callback: Callable, *args):
    """Deliver callback results to the GTK main thread.

    Falls back to a direct call when GLib.idle_add is unavailable, which
    only happens under the headless test seams.
    """
    idle_add = getattr(GLib, 'idle_add', None)
    if idle_add is None:
        return callback(*args)
    try:
        idle_add(callback, *args)
    except Exception:
        pass


_NM_PATH = '/org/freedesktop/NetworkManager'
_NM_IFACE = 'org.freedesktop.NetworkManager'
_BLUEZ_ADAPTER_PATH = '/org/bluez/hci0'
_BLUEZ_ADAPTER_IFACE = 'org.bluez.Adapter1'
_PROPS_IFACE = 'org.freedesktop.DBus.Properties'


def theme_css(preset: ThemePreset) -> str:
    # Tiles render through the shared glyph-tile builder (common/xx_ui),
    # so this sheet only owns the panel chrome the design system has no
    # component for: the slider card. Colors are the brand opacity ramp
    # of the preset foreground (3.5% raised, 10% line, 25% shade, 80%
    # strong) so every preset gets the same hierarchy as AMOLED.
    fg = preset.foreground
    return f"""
.qa-panel {{
    background: transparent;
    color: {fg};
}}
.qa-sep {{
    min-height: 0;
    margin: 0;
    background: none;
}}
.qa-sliders {{
    margin-top: 22px;
    padding: 6px 14px;
    border-radius: 16px;
    background: alpha({fg}, 0.035);
    border: 1px solid alpha({fg}, 0.10);
}}
.qa-row {{
    min-height: 32px;
    padding: 2px 0;
}}
.qa-sliders > .qa-row + .qa-row {{
    border-top: 1px solid alpha({fg}, 0.10);
}}
.qa-row-glyph {{
    color: alpha({fg}, 0.80);
    -gtk-icon-size: 18px;
    min-width: 20px;
}}
.qa-readout {{
    font-size: 11px;
    font-feature-settings: "tnum";
    color: {preset.muted};
    min-width: 28px;
}}
.qa-slider-label {{
    font-size: 10px;
    letter-spacing: 0.16em;
    color: {preset.muted};
}}
.qa-row-btn {{
    font-size: 10px;
    letter-spacing: 0.12em;
    min-height: 28px;
    padding: 0 10px;
    border-radius: 8px;
    background: none;
    box-shadow: none;
    border: 1px solid alpha({fg}, 0.10);
    color: alpha({fg}, 0.80);
}}
.qa-row-btn:active {{
    background: {preset.surface_alt};
}}
.qa-row-btn:checked {{
    background: {fg};
    color: {preset.background};
    border-color: {fg};
}}
.shade-slider {{
    padding: 0;
    margin: 0;
    min-height: 28px;
}}
.shade-slider trough {{
    background: alpha({fg}, 0.25);
    min-height: 2px;
    border-radius: 1px;
    outline: none;
}}
.shade-slider highlight {{
    background: {fg};
    min-height: 2px;
    border-radius: 1px;
}}
.shade-slider slider {{
    background: {fg};
    border: none;
    box-shadow: none;
    outline: none;
    border-radius: 2px;
    min-width: 4px;
    min-height: 18px;
    margin: -8px 0;
}}
.shade-slider:disabled highlight,
.shade-slider:disabled slider {{
    background: alpha({fg}, 0.25);
}}
"""


_ICON_DIR = Path(__file__).with_name('icons')

# Tile keys whose glyph file carries another name.
_TILE_GLYPHS = {'tailscale': 'tailnet', 'auto_br': 'auto-br'}


def tile_icon_name(key: str) -> str:
    """Symbolic icon a tile draws (installed next to this module)."""
    return f'xx-shade-{_TILE_GLYPHS.get(key, key)}-symbolic'


def ensure_shade_icons(display=None) -> None:
    """Put the shade glyph directory on the display's icon-theme search
    path (once). Symbolic icons recolor with the theme via CSS color."""
    try:
        display = display if display is not None else Gdk.Display.get_default()
        if display is None:
            return
        icon_theme = Gtk.IconTheme.get_for_display(display)
        path = str(_ICON_DIR)
        if path not in list(icon_theme.get_search_path() or []):
            icon_theme.add_search_path(path)
    except Exception:
        pass


# --- DBus helpers (best-effort, all failures are silent) ---

def _dbus_system() -> Gio.DBusConnection | None:
    try:
        return Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    except GLib.Error:
        return None


def _nm_get(prop: str) -> bool | None:
    bus = _dbus_system()
    if not bus:
        return None
    try:
        result = bus.call_sync(
            'org.freedesktop.NetworkManager',
            '/org/freedesktop/NetworkManager',
            'org.freedesktop.DBus.Properties', 'Get',
            GLib.Variant('(ss)', ('org.freedesktop.NetworkManager', prop)),
            GLib.VariantType('(v)'),
            Gio.DBusCallFlags.NONE, 800, None,
        )
        return bool(result.unpack()[0])
    except GLib.Error:
        return None


def _nm_set(prop: str, value: bool) -> None:
    bus = _dbus_system()
    if not bus:
        return
    try:
        bus.call_sync(
            'org.freedesktop.NetworkManager',
            '/org/freedesktop/NetworkManager',
            'org.freedesktop.DBus.Properties', 'Set',
            GLib.Variant('(ssv)', ('org.freedesktop.NetworkManager', prop, GLib.Variant('b', value))),
            None, Gio.DBusCallFlags.NONE, 800, None,
        )
    except GLib.Error:
        pass


_NM_DEV_WIFI = 2  # NetworkManager DeviceType


def _nm_has_wifi_device() -> bool:
    """False also covers NM being down, so the Hotspot tile hides entirely."""
    bus = _dbus_system()
    if not bus:
        return False
    try:
        devices = bus.call_sync(
            'org.freedesktop.NetworkManager',
            '/org/freedesktop/NetworkManager',
            'org.freedesktop.DBus.Properties', 'Get',
            GLib.Variant('(ss)', ('org.freedesktop.NetworkManager', 'Devices')),
            GLib.VariantType('(v)'),
            Gio.DBusCallFlags.NONE, 800, None,
        ).unpack()[0]
        for dev_path in devices:
            dtype = bus.call_sync(
                'org.freedesktop.NetworkManager', dev_path,
                'org.freedesktop.DBus.Properties', 'Get',
                GLib.Variant('(ss)',
                             ('org.freedesktop.NetworkManager.Device',
                              'DeviceType')),
                GLib.VariantType('(v)'),
                Gio.DBusCallFlags.NONE, 800, None,
            ).unpack()[0]
            if int(dtype) == _NM_DEV_WIFI:
                return True
    except GLib.Error:
        return False
    return False


def _bluez_get() -> bool | None:
    bus = _dbus_system()
    if not bus:
        return None
    try:
        result = bus.call_sync(
            'org.bluez', '/org/bluez/hci0',
            'org.freedesktop.DBus.Properties', 'Get',
            GLib.Variant('(ss)', ('org.bluez.Adapter1', 'Powered')),
            GLib.VariantType('(v)'),
            Gio.DBusCallFlags.NONE, 800, None,
        )
        return bool(result.unpack()[0])
    except GLib.Error:
        return None


def _bluez_set(value: bool) -> None:
    bus = _dbus_system()
    if not bus:
        return
    try:
        bus.call_sync(
            'org.bluez', '/org/bluez/hci0',
            'org.freedesktop.DBus.Properties', 'Set',
            GLib.Variant('(ssv)', ('org.bluez.Adapter1', 'Powered', GLib.Variant('b', value))),
            None, Gio.DBusCallFlags.NONE, 800, None,
        )
    except GLib.Error:
        pass


def _toggle_airplane(enabled: bool) -> None:
    _nm_set('WirelessEnabled', not enabled)
    _nm_set('WwanEnabled', not enabled)
    _bluez_set(not enabled)


def _get_airplane_state() -> bool | None:
    """Airplane reads back ON only while both radios report disabled.

    Either property unknown (service down) means unknown — never a guess.
    Runs on a worker thread; callers must not invoke it on the main loop.
    """
    wifi = _nm_get('WirelessEnabled')
    wwan = _nm_get('WwanEnabled')
    if wifi is None or wwan is None:
        return None
    return (not wifi) and (not wwan)


def format_tile_state(key: str, active: bool, detail: str | None = None) -> str:
    """Second-line text under a tile label: the connected SSID / BT device
    while on, plain On/Off otherwise."""
    if key == 'tailscale' and detail:
        # 'Sign in' / 'No permission' read better than a bare Off
        return detail
    if not active:
        return 'Off'
    if key in ('wifi', 'bt', 'nope') and detail:
        return detail
    return 'On'


_SIGN_IN_TEXT = 'Sign in'  # tailscale.SIGN_IN, kept import-free here


def tile_needs_attention(key: str, active: bool,
                         detail: str | None = None) -> bool:
    """True while a tile wants the user (dot + text-weight state line):
    today only a Tailnet that needs a browser sign-in."""
    return key == 'tailscale' and not active and detail == _SIGN_IN_TEXT


_TILE_SETTINGS_SECTIONS = {
    'wifi': 'wifi',
    'bt': 'bluetooth',
    'data': 'mobile',
    'airplane': 'mobile',
    'night': 'display',
    'tailscale': 'services',
    'nope': 'nope',
}


def tile_settings_section(key: str) -> str | None:
    """Settings page section a tile long-press opens (None = top of page)."""
    return _TILE_SETTINGS_SECTIONS.get(key)


# State line a tile shows before the first async probe lands.
_TILE_NEUTRAL_TEXT = '…'


def _has_backlight() -> bool:
    """True when the screen has a backlight node; without one the
    brightness row would be a dead slider, so the panel hides it. Halium
    panels (FLX1) are LED-class (leds/lcd-backlight), not backlight-class:
    checking only /sys/class/backlight hid the slider there."""
    try:
        return backlight.find() is not None
    except OSError:
        return False


def _get_wifi_ssid() -> str | None:
    """Connected SSID without triggering a scan (--rescan no); None when
    offline or nmcli is absent. Worker thread only: it spawns a process."""
    import shutil
    if shutil.which('nmcli') is None:
        return None
    from system_status import wifi_ssid
    try:
        result = wifi_ssid()
    except Exception:
        return None
    result = (result or '').strip()
    return result or None


def _variant_text(value: object) -> object:
    unpack = getattr(value, 'unpack', None)
    if callable(unpack):
        try:
            return unpack()
        except Exception:
            return value
    return value


def _get_bt_device_name() -> str | None:
    """Name of a Connected BlueZ device via ObjectManager; None when none.
    Worker thread only: synchronous D-Bus with a short timeout."""
    bus = _dbus_system()
    if bus is None:
        return None
    try:
        objects = bus.call_sync(
            'org.bluez', '/',
            'org.freedesktop.DBus.ObjectManager', 'GetManagedObjects',
            None,
            GLib.VariantType('(a{oa{sa{sv}}})'),
            Gio.DBusCallFlags.NONE, 800, None,
        ).unpack()[0]
    except Exception:
        return None
    if not isinstance(objects, dict):
        return None
    for interfaces in objects.values():
        if not isinstance(interfaces, dict):
            continue
        props = interfaces.get('org.bluez.Device1')
        if not isinstance(props, dict):
            continue
        if not bool(_variant_text(props.get('Connected', False))):
            continue
        for prop in ('Name', 'Alias'):
            name = _variant_text(props.get(prop, ''))
            if isinstance(name, str) and name:
                return name
        address = _variant_text(props.get('Address', ''))
        if isinstance(address, str) and address:
            return address
    return None


def _query_tile_details() -> dict[str, str]:
    """Per-tile detail strings (SSID / BT name); best-effort, worker only."""
    details: dict[str, str] = {}
    try:
        ssid = _get_wifi_ssid()
    except Exception:
        ssid = None
    if ssid:
        details['wifi'] = ssid
    try:
        name = _get_bt_device_name()
    except Exception:
        name = None
    if name:
        details['bt'] = name
    if _TS_LAST is not None:
        details['tailscale'] = _TS_LAST.detail
    return details


def _toggle_torch(enabled: bool) -> None:
    for led in Path('/sys/class/leds').glob('*torch*'):
        bright = led / 'brightness'
        max_p = led / 'max_brightness'
        try:
            max_val = int(max_p.read_text()) if max_p.exists() else 1
            bright.write_text(str(max_val if enabled else 0))
        except OSError:
            pass


_NM_ACTIVE_TYPE_AP = 'ap'  # ActiveConnection.Type while a hotspot is up


def _active_hotspot_path(bus: Gio.DBusConnection) -> str | None:
    """Resolve the active hotspot by NM's Type='ap', never by profile name:
    the profile behind it may be renamed or auto-created. Raises GLib.Error
    when NM is unreachable."""
    conns = bus.call_sync(
        'org.freedesktop.NetworkManager',
        '/org/freedesktop/NetworkManager',
        'org.freedesktop.DBus.Properties', 'Get',
        GLib.Variant('(ss)', ('org.freedesktop.NetworkManager',
                              'ActiveConnections')),
        GLib.VariantType('(v)'),
        Gio.DBusCallFlags.NONE, 800, None,
    ).unpack()[0]
    for path in conns:
        actype = bus.call_sync(
            'org.freedesktop.NetworkManager', path,
            'org.freedesktop.DBus.Properties', 'Get',
            GLib.Variant('(ss)',
                         ('org.freedesktop.NetworkManager.Connection.Active',
                          'Type')),
            GLib.VariantType('(v)'),
            Gio.DBusCallFlags.NONE, 800, None,
        ).unpack()[0]
        if actype == _NM_ACTIVE_TYPE_AP:
            return path
    return None


def _toggle_hotspot(enabled: bool) -> None:
    if enabled:
        try:
            subprocess.Popen(['nmcli', 'device', 'wifi', 'hotspot'],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True)
        except OSError:
            pass
        return
    bus = _dbus_system()
    if bus is None:
        return
    try:
        active = _active_hotspot_path(bus)
    except GLib.Error:
        return
    if active is None:
        return
    try:
        bus.call_sync(
            'org.freedesktop.NetworkManager',
            '/org/freedesktop/NetworkManager',
            'org.freedesktop.NetworkManager', 'DeactivateConnection',
            GLib.Variant('(o)', (active,)), None,
            Gio.DBusCallFlags.NONE, 800, None,
        )
    except GLib.Error:
        pass


def _get_hotspot_state() -> bool | None:
    bus = _dbus_system()
    if bus is None:
        return None
    try:
        return _active_hotspot_path(bus) is not None
    except GLib.Error:
        return None


# --- Location (GeoClue2 master switch) ---

# GeoClue2 has no enable/disable control on its Manager; clients negotiate
# with a registered authorization agent (the same lever GNOME Shell and phosh
# pull). This tile is a global master switch, not per-app policy: while ON
# every client is granted up to EXACT accuracy, while OFF MaxAccuracyLevel=0
# denies all clients (and revokes live ones).
_GEOCLUE_NAME = 'org.freedesktop.GeoClue2'
_GEOCLUE_MGR_PATH = '/org/freedesktop/GeoClue2/Manager'
_GEOCLUE_MGR_IFACE = 'org.freedesktop.GeoClue2.Manager'
_GEOCLUE_CLIENT_IFACE = 'org.freedesktop.GeoClue2.Client'
_AGENT_IFACE = 'org.freedesktop.GeoClue2.Agent'
_AGENT_PATH = '/org/xxwm/GeoClueAgent'
_AGENT_XML = (
    '<node>'
    f'<interface name="{_AGENT_IFACE}">'
    '<method name="AuthorizeApp">'
    '<arg type="s" direction="in" name="app_id"/>'
    '<arg type="u" direction="in" name="req_accuracy"/>'
    '<arg type="u" direction="out" name="allowed_accuracy"/>'
    '</method>'
    '<property name="MaxAccuracyLevel" type="u" access="read"/>'
    '</interface>'
    '</node>'
)

# GClueAccuracyLevel: NONE=0 … EXACT=8
_ACC_EXACT = 8


def max_accuracy_changed_params(level: int, glib=None):
    """``(sa{sv}as)`` PropertiesChanged body for MaxAccuracyLevel, built in
    one step with a plain dict (never nest a prebuilt container Variant)."""
    glib = glib if glib is not None else GLib
    return glib.Variant('(sa{sv}as)', (
        _AGENT_IFACE,
        {'MaxAccuracyLevel': glib.Variant('u', int(level))},
        [],
    ))


class LocationState:
    """GeoClue2 toggle without root.

    Geoclue serves ONE agent system-wide, so registration is lazy (first
    enable) and the slot is released on disable; if another agent owns the
    slot (e.g. phosh's prompting agent) the tile hides. The daemon's name
    owner is watched so state stays honest across geoclue restarts.
    """

    def __init__(self, on_change: Callable[[], None] | None = None) -> None:
        self.enabled = False
        self.on_change = on_change
        self._obj_id: int | None = None
        self._owner: str | None = None
        self._rejected = False
        try:
            Gio.bus_watch_name(
                Gio.BusType.SYSTEM, _GEOCLUE_NAME,
                Gio.BusNameWatcherFlags.NONE,
                self._on_owner_appeared, self._on_owner_vanished)
        except Exception:
            pass

    def active_clients(self, bus=None) -> list[str]:
        """Desktop ids with a live location fix (clients that called Start
        and are still active). Worker thread only; [] when geoclue is
        unreachable. The privacy monitor consumes this instead of running
        its own agent — geoclue serves one agent system-wide."""
        if bus is None:
            bus = _dbus_system()
        if bus is None:
            return []
        try:
            paths = bus.call_sync(
                _GEOCLUE_NAME, _GEOCLUE_MGR_PATH, _GEOCLUE_MGR_IFACE,
                'GetClients', None,
                GLib.VariantType('(ao)'),
                Gio.DBusCallFlags.NONE, 1500, None,
            ).unpack()[0]
        except Exception:
            return []
        users: list[str] = []
        for path in paths or ():
            try:
                props = bus.call_sync(
                    _GEOCLUE_NAME, str(path),
                    'org.freedesktop.DBus.Properties', 'GetAll',
                    GLib.Variant('(s)', (_GEOCLUE_CLIENT_IFACE,)),
                    GLib.VariantType('(a{sv})'),
                    Gio.DBusCallFlags.NONE, 1500, None,
                ).unpack()[0]
            except Exception:
                continue
            if not isinstance(props, dict):
                continue
            if not bool(_variant_text(props.get('Active', False))):
                continue
            desktop = _variant_text(props.get('DesktopId', ''))
            label = str(desktop).strip() if desktop else ''
            if label and label not in users:
                users.append(label)
        return users

    def available(self) -> bool:
        """Read-only probe: daemon reachable and no live rejection. Never
        registers — claiming the system's single agent slot must wait for an
        explicit enable."""
        if self._rejected:
            return False
        bus = _dbus_system()
        if bus is None:
            return False
        try:
            bus.call_sync(
                _GEOCLUE_NAME, _GEOCLUE_MGR_PATH,
                'org.freedesktop.DBus.Properties', 'Get',
                GLib.Variant('(ss)', (_GEOCLUE_NAME + '.Manager', 'InUse')),
                GLib.VariantType('(v)'),
                Gio.DBusCallFlags.NONE, 800, None,
            )
        except GLib.Error:
            return False
        return True

    def is_active(self) -> bool:
        # Honest ON requires a live registration with the current owner.
        return self.enabled and self._obj_id is not None

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        bus = _dbus_system()
        if bus is None:
            return
        if self.enabled:
            self._activate(bus)
        else:
            self._deactivate(bus)

    def _activate(self, bus: Gio.DBusConnection) -> None:
        if not self._register(bus):
            self.enabled = False
            self._rejected = True
        else:
            self._rejected = False
            self._notify_max_accuracy(bus)
        self._fire_change()

    def _deactivate(self, bus: Gio.DBusConnection) -> None:
        # Revoke before releasing the slot so the daemon sees MaxAccuracyLevel=0.
        self._notify_max_accuracy(bus)
        self._unregister(bus)
        self._fire_change()

    def _fire_change(self) -> None:
        if self.on_change is None:
            return
        try:
            self.on_change()
        except Exception:
            pass

    def _register(self, bus: Gio.DBusConnection) -> bool:
        if self._obj_id is not None:
            return True
        obj_id = None
        try:
            info = Gio.DBusNodeInfo.new_for_xml(_AGENT_XML)
            obj_id = bus.register_object(
                _AGENT_PATH, info.interfaces[0],
                self._on_method_call, self._on_get_property, None)
            bus.call_sync(
                _GEOCLUE_NAME, _GEOCLUE_MGR_PATH,
                _GEOCLUE_NAME + '.Manager', 'RegisterAgent',
                GLib.Variant('(o)', (_AGENT_PATH,)), None,
                Gio.DBusCallFlags.NONE, 2000, None,
            )
        except Exception:
            if obj_id is not None:
                try:
                    bus.unregister_object(obj_id)
                except Exception:
                    pass
            return False
        self._obj_id = obj_id
        return True

    def _unregister(self, bus: Gio.DBusConnection) -> None:
        if self._obj_id is None:
            return
        try:
            bus.call_sync(
                _GEOCLUE_NAME, _GEOCLUE_MGR_PATH,
                _GEOCLUE_NAME + '.Manager', 'UnregisterAgent',
                GLib.Variant('(o)', (_AGENT_PATH,)), None,
                Gio.DBusCallFlags.NONE, 2000, None,
            )
        except Exception:
            pass
        self._drop_export(bus)

    def _drop_export(self, bus: Gio.DBusConnection | None) -> None:
        if self._obj_id is None:
            return
        if bus is not None:
            try:
                bus.unregister_object(self._obj_id)
            except Exception:
                pass
        self._obj_id = None

    def _on_owner_appeared(self, _conn, _name, owner: str) -> None:
        if owner == self._owner:
            return
        # A new owner means the old daemon (and our registration with it)
        # died, even when no vanish was observed first.
        self._drop_export(_dbus_system())
        self._owner = owner
        self._rejected = False
        if self.enabled:
            bus = _dbus_system()
            if bus is not None:
                self._activate(bus)
                return
        self._fire_change()

    def _on_owner_vanished(self, _conn, _name) -> None:
        if self._owner is None:
            return
        self._owner = None
        was_registered = self._obj_id is not None
        self._drop_export(_dbus_system())
        if was_registered:
            self._fire_change()

    def _max_accuracy(self) -> int:
        return _ACC_EXACT if self.enabled else 0

    def _allow(self, req_accuracy: int) -> int:
        return min(int(req_accuracy), _ACC_EXACT) if self.enabled else 0

    def _notify_max_accuracy(self, bus: Gio.DBusConnection) -> None:
        if self._obj_id is None:
            return
        try:
            bus.emit_signal(
                None, _AGENT_PATH, 'org.freedesktop.DBus.Properties',
                'PropertiesChanged',
                max_accuracy_changed_params(self._max_accuracy()),
            )
        except Exception:
            pass

    def _on_method_call(self, _conn, _sender, _path, _iface, method,
                        params, invocation) -> None:
        if method == 'AuthorizeApp':
            req = params.unpack()[1]
            invocation.return_value(GLib.Variant('(u)', (self._allow(req),)))
            return
        invocation.return_error_by_name(
            'org.freedesktop.DBus.Error.UnknownMethod')

    def _on_get_property(self, _conn, _sender, _path, _iface, prop):
        if prop == 'MaxAccuracyLevel':
            return GLib.Variant('u', self._max_accuracy())
        return None


_SLIDER_DEFAULT_PCT = 50
_SLIDER_APPLY_DEBOUNCE_MS = 200


def _read_sysfs_brightness_pct(
    backlight_root: Path = Path('/sys/class/backlight'),
) -> int | None:
    """Brightness percent from kernel backlight sysfs nodes (unprivileged
    instant reads, the same nodes brightnessctl reports); None when no
    node parses."""
    for path in backlight_root.glob('*/brightness'):
        try:
            current = int(path.read_text())
            max_val = int((path.parent / 'max_brightness').read_text())
            if max_val:
                return int(current * 100 / max_val)
        except (OSError, ValueError):
            pass
    return None


def _get_brightness_pct() -> int:
    pct = _read_sysfs_brightness_pct()
    if pct is None:
        pct = backlight.read_pct()  # LED-class panels (Halium)
    return _SLIDER_DEFAULT_PCT if pct is None else pct


def _set_brightness_pct(pct: int) -> None:
    try:
        subprocess.Popen(backlight.brightnessctl_args() + ['set', f'{max(1, pct)}%'],
                         close_fds=True)
        return
    except FileNotFoundError:
        pass
    # brightnessctl is absent: logind's Session.SetBrightness next, sysfs
    # only as a last resort (direct writes fail with EACCES as the user).
    apply_backlight_pct(pct)


def _get_volume_pct() -> int:
    try:
        out = subprocess.check_output(
            ['pactl', 'get-sink-volume', '@DEFAULT_SINK@'],
            text=True, timeout=1,
        )
        for token in out.split():
            if token.endswith('%'):
                return int(token.rstrip('%'))
    except Exception:
        pass
    return _SLIDER_DEFAULT_PCT


def _set_volume_pct(pct: int) -> None:
    try:
        subprocess.Popen(['pactl', 'set-sink-volume', '@DEFAULT_SINK@', f'{pct}%'], close_fds=True)
    except FileNotFoundError:
        pass


def _read_slider_values() -> tuple[int, int]:
    """One getter batch per expand: (brightness_pct, volume_pct)."""
    return _get_brightness_pct(), _get_volume_pct()


# --- Tile definitions ---

class _TileDef(NamedTuple):
    key: str
    label: str
    get_state: Callable[[], bool | None]
    set_state: Callable[[bool], None]
    tier: int  # 1 = always shown, 2 = expanded only


def _recorder_available() -> bool:
    """True when wf-recorder is installed; without it the Record tile
    hides entirely instead of offering a fake fallback."""
    try:
        import capture
        return bool(capture.recorder_available())
    except Exception:
        return False


def _get_record_state() -> bool | None:
    try:
        import capture
        return bool(capture.get_recorder().is_recording())
    except Exception:
        return None


def _set_record_state(value: bool) -> None:
    try:
        import capture
        capture.get_recorder().set_recording(bool(value))
    except Exception:
        pass


def _night_light_available() -> bool:
    """True when wlsunset or gammastep is installed; without either the
    Night tile hides entirely instead of offering a dead toggle."""
    import shutil
    try:
        return shutil.which('wlsunset') is not None or \
            shutil.which('gammastep') is not None
    except Exception:
        return False


def _display_module():
    import settings_display
    return settings_display


def _get_night_state() -> bool | None:
    try:
        return _display_module().tile_get_night()
    except Exception:
        return None


def _set_night_state(value: bool) -> None:
    try:
        _display_module().tile_set_night(bool(value))
    except Exception:
        pass


def _tailscale_available() -> bool:
    """True when the tailscale CLI is installed; without it the tile hides."""
    try:
        import tailscale
        return tailscale.available()
    except Exception:
        return False


def _captions_installed() -> bool:
    """True when xx-captions is installed; without it the Captions tile
    hides entirely instead of offering a dead toggle."""
    try:
        import captions
        return bool(captions.captions_installed())
    except Exception:
        return False


def _captions_controller(panel: object) -> object | None:
    return getattr(panel, '_captions', None)


def _get_captions_state(panel: object) -> bool | None:
    ctrl = _captions_controller(panel)
    if ctrl is None:
        return None
    try:
        return bool(ctrl.active)
    except Exception:
        return None


def _set_captions_state(panel: object, value: bool) -> None:
    ctrl = _captions_controller(panel)
    if ctrl is None:
        return
    try:
        ctrl.set_enabled(bool(value))
    except Exception:
        pass


def _make_captions_controller(on_change) -> object | None:
    try:
        import captions
        return captions.CaptionsController(on_change=on_change)
    except Exception:
        return None


def _open_captions_overlay() -> None:
    try:
        import captions
        captions.open_overlay()
    except Exception:
        pass


# Last Tailscale status read by the tile-state worker; the detail query in
# the same refresh reuses it instead of running the CLI twice.
_TS_LAST = None


def _get_tailscale_state() -> bool | None:
    global _TS_LAST
    import tailscale
    _TS_LAST = tailscale.status()
    if _TS_LAST.state == tailscale.UNAVAILABLE:
        return None
    return _TS_LAST.state == tailscale.RUNNING


def _get_saver_state() -> bool | None:
    """Battery-saver tile state: the standby controller's effective saver
    (manual toggle or automatic low-battery). Reached through the
    process-wide registry so the tile list needs no panel wiring."""
    try:
        from app_standby import get_standby
        return bool(get_standby().saver_effective)
    except Exception:
        return None


def _set_saver_state(value: bool) -> None:
    try:
        from app_standby import get_standby
        get_standby().set_saver(bool(value))
    except Exception:
        pass


_TILES: list[_TileDef] = [
    _TileDef('wifi',     'WiFi',     lambda: _nm_get('WirelessEnabled'), lambda v: _nm_set('WirelessEnabled', v), 1),
    _TileDef('bt',       'BT',       _bluez_get,                          _bluez_set,                              1),
    _TileDef('data',     'Data',     lambda: _nm_get('WwanEnabled'),      lambda v: _nm_set('WwanEnabled', v),     1),
    _TileDef('airplane', 'Airplane', _get_airplane_state,                _toggle_airplane,                        1),
    _TileDef('torch',    'Torch',    lambda: None,                        _toggle_torch,                           2),
    _TileDef('dnd',      'DnD',      lambda: None,                        lambda _v: None,                         2),
    _TileDef('focus',    'Focus',    lambda: None,                        lambda _v: None,                         2),
    _TileDef('nope',     'Nope',     lambda: None,                        lambda _v: None,                         2),
    _TileDef('saver',    'Saver',    _get_saver_state,                    _set_saver_state,                        2),
    _TileDef('record',   'Record',   _get_record_state,                   _set_record_state,                     2),
    _TileDef('captions', 'Captions', lambda: None,                        lambda _v: None,                         2),
    _TileDef('auto_br',  'Auto',     lambda: None,                        lambda _v: None,                         2),
    _TileDef('location', 'Location', lambda: None,                        lambda _v: None,                         2),
    _TileDef('hotspot',  'Hotspot',  _get_hotspot_state,                  _toggle_hotspot,                         2),
    _TileDef('night',    'Night',    _get_night_state,                    _set_night_state,                        2),
    _TileDef('tailscale', 'Tailnet',  _get_tailscale_state,               lambda _v: None,                         2),
]


class QuickActionsPanel(Gtk.Box):
    """
    Quick-actions tile grid + brightness/volume sliders.
    Embed in NotificationShade. Call expand(True/False) to show tier-2 tiles.
    Brightness and volume sliders stay visible (not gated on expand).
    """

    def __init__(self, dnd_state: object | None = None,
                 focus_state: object | None = None,
                 nope_state: object | None = None,
                 on_nope_changed=None,
                 hud: object | None = None,
                 config: ShellConfig | None = None,
                 on_open_settings: object | None = None,
                 on_open_uri: object | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class('qa-panel')
        ensure_shade_icons()

        self._dnd = dnd_state
        self._focus = focus_state
        self._nope = nope_state
        self._on_nope_changed = on_nope_changed
        self._on_open_settings = on_open_settings
        self._on_open_uri = on_open_uri
        # Volume/brightness HUD overlay (plan T3/T4). Optional: when absent the
        # sliders still adjust brightness/volume, just without an on-screen
        # indicator (silent absence).
        self._hud = hud
        # The shade threads its LIVE config down so theme == 'custom' renders
        # the derived palette; the fallback keeps direct no-arg construction
        # working (pre-existing snapshot behavior).
        self._config = config if config is not None else ShellConfig()
        self._tile_buttons: dict[str, Gtk.ToggleButton] = {}
        self._tile_state: dict[str, bool] = {}
        self._tile_details: dict[str, str] = {}
        self._state_labels: dict[str, Gtk.Label] = {}
        self._readouts: dict[str, Gtk.Label] = {}
        self._updating = False
        self._als = ALSBrightness()
        self._location = LocationState(on_change=self._on_location_changed)
        self._captions = _make_captions_controller(self._on_captions_changed)
        # Hardware gates (torch/ALS/hotspot/location) cache after first probe
        # so rebuilds never re-hit D-Bus or sysfs from the main thread.
        self._hw_gate_cache: dict[str, bool] = {}
        self._worker_threads: list = []
        self._slider_pending: dict[str, int] = {}
        self._slider_timers: dict[str, int] = {}
        self._syncing_sliders = False
        self._slider_appliers = {
            'bright': self._apply_brightness,
            'vol': _set_volume_pct,
        }

        self._theme_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            self._theme_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 3,
        )
        self.apply_theme()

        # Fast tier only: the slow D-Bus gates (hotspot/location) fill in
        # from a worker so first paint never waits on service timeouts.
        self._build()
        self._probe_slow_gates_async()
        self._connect_captions_async()
        self.refresh_states()
        self._subscribe_hardware_signals()

    def apply_theme(self, preset: ThemePreset | None = None) -> None:
        """Reload this panel's single display-level sheet.

        The shade forwards its own hot-reload here so tiles restyle too.
        """
        try:
            resolved = preset if preset is not None else self._display_preset()
        except Exception:
            return
        try:
            self._theme_provider.load_from_data(
                theme_css(resolved).encode('utf-8'))
        except Exception:
            pass

    def _display_preset(self) -> ThemePreset:
        """Preset this panel renders with: window.resolve_theme over the
        injected config, so theme == 'custom' derives its palette instead of
        falling back to the default preset (W1-B). Lazy import: window.py
        sits above this module in the shell stack, and a module-level import
        would drag its GTK requirements into headless contexts that import
        quick_actions for its tile/slider logic.
        """
        from window import resolve_theme
        return resolve_theme(self._config)

    def _cached_gate(self, key: str, probe: Callable[[], bool]) -> bool:
        """Hardware-gate probe with a per-panel cache.

        Panels built headlessly in tests carry no cache and probe directly.
        """
        cache = getattr(self, '_hw_gate_cache', None)
        if cache is None:
            return bool(probe())
        if key not in cache:
            cache[key] = bool(probe())
        return cache[key]

    def invalidate_gate_cache(self, key: str | None = None) -> None:
        """Forget cached hardware gates (e.g. geoclue restarted)."""
        cache = getattr(self, '_hw_gate_cache', None)
        if cache is None:
            return
        if key is None:
            cache.clear()
        else:
            cache.pop(key, None)

    def _tiles(self, include_slow_gates: bool = True) -> list[_TileDef]:
        tiles = []
        cache = getattr(self, '_hw_gate_cache', None)
        for tile in _TILES:
            # Hardware/service-gated tiles stay hidden without their backend
            if tile.key == 'auto_br' and not self._cached_gate(
                    'auto_br', self._als.available):
                continue
            if tile.key == 'torch' and not self._cached_gate(
                    'torch',
                    lambda: any(Path('/sys/class/leds').glob('*torch*'))):
                continue
            if tile.key == 'record' and not self._cached_gate(
                    'record', _recorder_available):
                continue
            if tile.key == 'captions':
                if not self._cached_gate('captions', _captions_installed):
                    continue
                tile = tile._replace(
                    get_state=lambda: _get_captions_state(self),
                    set_state=lambda v: _set_captions_state(self, v),
                )
            if tile.key == 'hotspot':
                if (not include_slow_gates and cache is not None
                        and 'hotspot' not in cache):
                    continue
                if not self._cached_gate('hotspot', _nm_has_wifi_device):
                    continue
            if tile.key == 'night' and not self._cached_gate(
                    'night', _night_light_available):
                continue
            if tile.key == 'tailscale':
                if not self._cached_gate('tailscale', _tailscale_available):
                    continue
                tile = tile._replace(set_state=self._set_tailscale)
            if tile.key == 'location':
                if (not include_slow_gates and cache is not None
                        and 'location' not in cache):
                    continue
                if not self._cached_gate('location', self._location.available):
                    continue
                tile = tile._replace(
                    get_state=lambda: bool(self._location.is_active()),
                    set_state=self._location.set_enabled,
                )
            if tile.key == 'dnd':
                if self._dnd is None:
                    continue
                tile = tile._replace(
                    get_state=lambda: bool(self._dnd.is_active()),
                    set_state=lambda v: self._dnd.set_enabled(v),
                )
            if tile.key == 'focus':
                if self._focus is None:
                    continue
                tile = tile._replace(
                    get_state=lambda: bool(self._focus.is_active()),
                    set_state=lambda v: self._focus.set_enabled(v),
                )
            if tile.key == 'nope':
                if getattr(self, '_nope', None) is None:
                    continue
                tile = tile._replace(
                    get_state=lambda: bool(self._nope.is_active()),
                    set_state=self._set_nope,
                )
            tiles.append(tile)
        return tiles

    def _spawn_panel_worker(self, target: Callable[[], None]):
        """Run target off the main thread, tracked for tests to join."""
        thread = _spawn_worker_thread(target)
        workers = getattr(self, '_worker_threads', None)
        if workers is None:
            self._worker_threads = workers = []
        if thread is not None:
            workers.append(thread)
        return thread

    def _probe_slow_gates_async(self) -> None:
        """Resolve the D-Bus hardware gates (hotspot/location) off-thread.

        First paint only ever waits on the fast sysfs/None gates; newly
        available tiles are inserted (in tier order) when the probe lands.
        """
        cache = getattr(self, '_hw_gate_cache', None)
        if cache is None:
            return
        pending = [k for k in ('hotspot', 'location') if k not in cache]
        if not pending:
            return

        def _work() -> None:
            fresh: dict[str, bool] = {}
            try:
                if 'hotspot' in pending:
                    try:
                        fresh['hotspot'] = bool(_nm_has_wifi_device())
                    except Exception:
                        fresh['hotspot'] = False
                if 'location' in pending:
                    try:
                        fresh['location'] = bool(self._location.available())
                    except Exception:
                        fresh['location'] = False
            except Exception:
                return
            _post_to_main(self._finish_slow_gates, fresh)

        self._spawn_panel_worker(_work)

    def _finish_slow_gates(self, fresh: dict[str, bool] | None) -> bool:
        cache = getattr(self, '_hw_gate_cache', None)
        if cache is not None:
            for key, value in (fresh or {}).items():
                cache.setdefault(key, bool(value))
        try:
            tier2 = [t for t in self._tiles() if t.tier == 2]
        except Exception:
            return False
        buttons = getattr(self, '_tile_buttons', None)
        if buttons is None:
            return False
        # Re-lay out every tier-2 tile in order: attaching only the new ones
        # at their list index lands them on cells already taken by tiles
        # that were laid out without them (Location covered Tailscale).
        try:
            for tile in tier2:
                btn = buttons.get(tile.key) or self._make_tile(tile)
                if btn.get_parent() is self.tier2_grid:
                    self.tier2_grid.remove(btn)
            for idx, tile in enumerate(tier2):
                self.tier2_grid.attach(
                    buttons[tile.key], idx % 4, idx // 4, 1, 1)
        except Exception:
            pass
        try:
            self.refresh_states()
        except Exception:
            pass
        return False

    def _build(self) -> None:
        tiles = self._tiles(include_slow_gates=False)
        tier1 = [t for t in tiles if t.tier == 1]
        tier2 = [t for t in tiles if t.tier == 2]

        self.tier1_grid = Gtk.Grid(row_spacing=8, column_spacing=8,
                                     column_homogeneous=True)
        for col, tile in enumerate(tier1):
            self.tier1_grid.attach(self._make_tile(tile), col, 0, 1, 1)

        self.tier2_grid = Gtk.Grid(row_spacing=8, column_spacing=8,
                                   column_homogeneous=True)
        # Tier 2 continues the tier-1 grid: same 8 px gutter between rows.
        self.tier2_grid.set_margin_top(8)
        for idx, tile in enumerate(tier2):
            self.tier2_grid.attach(self._make_tile(tile), idx % 4, idx // 4, 1, 1)

        # Zero-height hook kept for the expand contract: the design runs
        # tier 2 straight into the slider card with no rule between.
        self.sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self.sep.add_css_class('qa-sep')

        self.sliders_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.sliders_box.add_css_class('qa-sliders')

        self._build_sliders()

        self.append(self.tier1_grid)
        self.append(self.tier2_grid)
        self.append(self.sep)
        self.append(self.sliders_box)

        self.tier2_grid.set_visible(False)
        self.sep.set_visible(False)
        self.sliders_box.set_visible(True)
        self._sync_sliders_to_live_state()

    def _make_tile(self, tile: _TileDef) -> Gtk.ToggleButton:
        # Shared design-system glyph tile: symbolic glyph over a caps
        # label and a live state line, inverted while on. The state label
        # stays reachable for the async SSID/device refreshes.
        from xx_ui import widgets
        btn = widgets.glyph_tile(
            tile_icon_name(tile.key), tile.label, _TILE_NEUTRAL_TEXT, False,
            on_toggle=lambda b: self._on_tile_toggled(b, tile))
        btn.set_hexpand(True)
        btn.set_tooltip_text(tile.label)
        self._state_labels[tile.key] = widgets.glyph_tile_state(btn)
        self._wire_tile_press(btn, tile)
        self._tile_buttons[tile.key] = btn
        return btn

    def _wire_tile_press(self, btn: Gtk.ToggleButton, tile: _TileDef) -> None:
        """Long-press wiring for one tile, factored out of _make_tile so
        headless tests drive the exact mapping without constructing real
        widgets (real Gtk segfaults without a display)."""
        if tile.key == 'focus':
            # Long-press → take a break (focus suspends, auto-resumes)
            long_press = Gtk.GestureLongPress.new()
            long_press.set_touch_only(False)
            long_press.connect('pressed', self._on_focus_long_press, btn)
            btn.add_controller(long_press)
        else:
            settings_press = Gtk.GestureLongPress.new()
            settings_press.set_touch_only(False)
            settings_press.connect(
                'pressed', self._on_tile_long_press, tile.key)
            btn.add_controller(settings_press)

    def _on_tile_long_press(self, gesture: Gtk.GestureLongPress,
                            _x: float, _y: float, key: str) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        if key == 'captions':
            # Long-press opens the overlay's model screen, not Settings.
            ctrl = _captions_controller(self)
            try:
                if ctrl is not None:
                    ctrl.open_model_screen()
                else:
                    _open_captions_overlay()
            except Exception:
                pass
            return
        callback = getattr(self, '_on_open_settings', None)
        if not callable(callback):
            return
        section = tile_settings_section(key)
        try:
            callback(section)
        except TypeError:
            try:
                callback()
            except Exception:
                pass

    def _on_focus_long_press(self, gesture: Gtk.GestureLongPress,
                             _x: float, _y: float, btn: Gtk.Widget) -> None:
        if self._focus is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        popover = Gtk.Popover()
        popover.add_css_class('action-menu')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        for minutes in (5, 10, 15):
            action = Gtk.Button(label=f'Take a break — {minutes} min')
            action.add_css_class('flat')
            action.add_css_class('menu-action')
            action.connect(
                'clicked',
                lambda _b, m=minutes, p=popover: self._start_focus_break(m, p))
            box.append(action)
        popover.set_child(box)
        popover.set_parent(btn)
        popover.connect('closed', lambda p: p.unparent())
        popover.popup()

    def _start_focus_break(self, minutes: int, popover: Gtk.Popover) -> None:
        popover.popdown()
        self._focus.start_break(minutes)
        self.refresh_states()

    def _build_sliders(self) -> None:
        # One raised card: glyph, 2 px track with a 4x18 thumb, numeric
        # readout. The words stay as tooltips for the glyph-only rows.
        readouts = getattr(self, '_readouts', None)
        if readouts is None:
            self._readouts = readouts = {}
        for label_text, key, glyph in [('Brightness', 'bright', 'brightness'),
                                       ('Volume', 'vol', 'volume')]:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.add_css_class('qa-row')
            icon = Gtk.Image.new_from_icon_name(f'xx-shade-{glyph}-symbolic')
            icon.set_pixel_size(18)
            icon.add_css_class('qa-row-glyph')
            icon.set_tooltip_text(label_text)
            slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
            slider.add_css_class('shade-slider')
            slider.set_hexpand(True)
            slider.set_draw_value(False)
            slider.set_tooltip_text(label_text)
            slider.set_value(_SLIDER_DEFAULT_PCT)
            readout = Gtk.Label(label=str(_SLIDER_DEFAULT_PCT), xalign=1)
            readout.add_css_class('qa-readout')
            readouts[key] = readout
            if key == 'bright':
                # Adjust brightness AND flash the level on the HUD (T4).
                slider.connect(
                    'value-changed',
                    lambda s: self._on_brightness_changed(int(s.get_value())))
            else:
                slider.connect(
                    'value-changed',
                    lambda s: self._on_volume_changed(int(s.get_value())))
            row.append(icon)
            row.append(slider)
            row.append(readout)
            self.sliders_box.append(row)

            if key == 'bright':
                self._bright_slider = slider
                self._bright_row = row
                row.set_visible(_has_backlight())
            else:
                self._vol_slider = slider


    def _set_readout(self, key: str, pct: int) -> None:
        readout = (getattr(self, '_readouts', None) or {}).get(key)
        if readout is None:
            return
        try:
            readout.set_text(str(int(pct)))
        except Exception:
            pass

    def _on_brightness_changed(self, pct: int) -> None:
        """Brightness slider moved: the trailing value is applied (with a HUD
        flash) once per drag after the debounce window."""
        self._set_readout('bright', pct)
        self._queue_slider_apply('bright', pct)

    def _on_volume_changed(self, pct: int) -> None:
        self._set_readout('vol', pct)
        self._queue_slider_apply('vol', pct)

    def _queue_slider_apply(self, key: str, pct: int) -> None:
        if self._syncing_sliders:
            return
        self._slider_pending[key] = pct
        timer = self._slider_timers.get(key)
        if timer is not None:
            GLib.source_remove(timer)
        self._slider_timers[key] = GLib.timeout_add(
            _SLIDER_APPLY_DEBOUNCE_MS, self._flush_slider_apply, key)

    def _flush_slider_apply(self, key: str) -> bool:
        self._slider_timers.pop(key, None)
        pct = self._slider_pending.pop(key, None)
        if pct is not None:
            self._slider_appliers[key](pct)
        return False

    def _apply_brightness(self, pct: int) -> None:
        # No HUD flash: the owner doesn't want a brightness popup; the slider
        # itself shows the level.
        _set_brightness_pct(pct)

    def _on_tile_toggled(self, btn: Gtk.ToggleButton, tile: _TileDef) -> None:
        if self._updating:
            return
        new_state = btn.get_active()
        if tile.key == 'auto_br':
            self._toggle_auto_brightness(new_state)
        else:
            # Setters are single synchronous property writes (no scans, no
            # long timeouts); the location test pins the immediate snap-back,
            # so toggles stay on the main thread by allow-list entry.
            try:
                tile.set_state(new_state)
            except Exception:
                pass
        if tile.key == 'location':
            # Registration may have failed: show the actual state, never the
            # requested one.
            self._apply_tile_state(tile.key, self._location.is_active())
            return
        if tile.key == 'captions':
            # Async Start/Stop: paint the controller state
            # (starting/error), never the requested toggle.
            self._refresh_captions_ui()
            return
        if tile.key == 'tailscale':
            # up/down finish on a worker; show progress (dashed WORKING
            # border), then the real state
            lbl = self._state_labels.get('tailscale')
            if lbl is not None:
                lbl.set_text(_TILE_NEUTRAL_TEXT)
            self._set_tile_class('tailscale', 'working', True)
            self._set_tile_class('tailscale', 'attention', False)
            return
        self._apply_tile_ui(tile.key, new_state)

    def _set_nope(self, enabled: bool) -> None:
        """Shade tap: toggle derived state, then let the shell close what
        is already open (the panel never touches toplevels itself)."""
        nope = getattr(self, '_nope', None)
        if nope is None:
            return
        try:
            if enabled:
                nope.set_enabled(True)
                nope.set_force()
            elif nope.is_active():
                nope.toggle()
            active = bool(nope.is_active())
        except Exception:
            return
        try:
            self.refresh_states()
        except Exception:
            pass
        callback = getattr(self, '_on_nope_changed', None)
        if callable(callback):
            try:
                callback(active)
            except Exception:
                pass

    def _set_tailscale(self, enabled: bool) -> None:
        def _work() -> None:
            import tailscale
            try:
                result = tailscale.up() if enabled else tailscale.down()
            except Exception:
                result = tailscale.status()
            _post_to_main(self._apply_tailscale, result)

        self._spawn_panel_worker(_work)

    def _apply_tailscale(self, result) -> bool:
        import tailscale
        self._set_tile_class('tailscale', 'working', False)
        self._apply_tile_state('tailscale', result.state == tailscale.RUNNING,
                               result.detail)
        opener = getattr(self, '_on_open_uri', None)
        if result.auth_url and callable(opener):
            try:
                opener(result.auth_url)
            except Exception:
                pass
        return False

    def _toggle_auto_brightness(self, enabled: bool) -> None:
        if enabled:
            if not self._als.available():
                # No ALS found — show tile as inactive
                GLib.idle_add(self._apply_tile_state, 'auto_br', False)
                return
            self._als.start()
            # Disable manual brightness slider while auto is active
            if hasattr(self, '_bright_slider'):
                self._bright_slider.set_sensitive(False)
        else:
            self._als.stop()
            if hasattr(self, '_bright_slider'):
                self._bright_slider.set_sensitive(True)

    def _on_location_changed(self) -> None:
        btn = self._tile_buttons.get('location')
        if btn is None:
            return
        self.invalidate_gate_cache('location')
        shown = self._cached_gate('location', self._location.available)
        btn.set_visible(shown)
        self._apply_tile_state('location',
                               shown and self._location.is_active())

    def _connect_captions_async(self) -> None:
        """Open the captions service off-thread; live state lands through
        the controller's on_change callback on the main loop."""
        ctrl = _captions_controller(self)
        if ctrl is None:
            return

        def _work() -> None:
            try:
                ctrl.connect()
            except Exception:
                pass

        self._spawn_panel_worker(_work)

    def _on_captions_changed(self) -> None:
        try:
            self._refresh_captions_ui()
        except Exception:
            pass

    def _captions_visual(self) -> tuple:
        """(button_active, highlighted, label, error) from the controller."""
        ctrl = _captions_controller(self)
        if ctrl is None:
            raise RuntimeError('no captions controller')
        return (bool(ctrl.active),
                ctrl.tile_state in ('on', 'starting'),
                str(ctrl.label), ctrl.error)

    def _refresh_captions_ui(self) -> None:
        """Repaint the tile and the volume-row toggle from the controller."""
        try:
            button_active, _visual, _label, _error = self._captions_visual()
        except Exception:
            return
        self._updating = True
        try:
            btn = (getattr(self, '_tile_buttons', None) or {}).get('captions')
            if btn is not None:
                btn.set_active(button_active)
        except Exception:
            pass
        finally:
            self._updating = False
        self._paint_captions_tile()

    def _paint_captions_tile(self) -> None:
        try:
            _active, visual, label, error = self._captions_visual()
        except Exception:
            return
        btn = (getattr(self, '_tile_buttons', None) or {}).get('captions')
        if btn is not None:
            try:
                if visual:
                    btn.add_css_class('active')
                else:
                    btn.remove_css_class('active')
            except Exception:
                pass
            try:
                starting = _captions_controller(self).tile_state == 'starting'
            except Exception:
                starting = False
            self._set_tile_class('captions', 'working', starting)
            if error:
                try:
                    btn.set_tooltip_text(str(error))
                except Exception:
                    pass
        lbl = (getattr(self, '_state_labels', None) or {}).get('captions')
        if lbl is not None:
            try:
                lbl.set_text(label)
            except Exception:
                pass

    def _query_tile_states(self) -> dict[str, bool]:
        """Read every tile state; blocking D-Bus goes here, off-thread."""
        states: dict[str, bool] = {}
        for tile in self._tiles():
            try:
                state = tile.get_state()
            except Exception:
                state = None
            if state is not None:
                states[tile.key] = state
        return states

    def _apply_queried_states(self, states: dict[str, bool] | None,
                              details: dict[str, str] | None = None) -> bool:
        if details is not None:
            try:
                self._tile_details = dict(details)
            except Exception:
                pass
        for key, state in (states or {}).items():
            try:
                if details is None:
                    self._apply_tile_state(key, state)
                else:
                    self._apply_tile_state(key, state, details.get(key))
            except Exception:
                pass
        return False

    def _refresh_all_states(self) -> bool:
        self._apply_queried_states(self._query_tile_states())
        return False

    def _subscribe_hardware_signals(self) -> None:
        """Track external radio changes live.

        NetworkManager WirelessEnabled/WwanEnabled and BlueZ Adapter1
        Powered can move under us (gnome-control-center, rfkill, another
        session); PropertiesChanged on either re-reads tile states instead
        of showing stale switches until the next manual refresh.
        """
        try:
            bus = _dbus_system()
        except Exception:
            return
        if bus is None:
            return
        try:
            bus.signal_subscribe(
                'org.freedesktop.NetworkManager', _PROPS_IFACE,
                'PropertiesChanged', _NM_PATH, None,
                Gio.DBusSignalFlags.NONE,
                self._on_hardware_properties_changed, None,
            )
            bus.signal_subscribe(
                'org.bluez', _PROPS_IFACE, 'PropertiesChanged',
                _BLUEZ_ADAPTER_PATH, None, Gio.DBusSignalFlags.NONE,
                self._on_hardware_properties_changed, None,
            )
        except Exception:
            pass

    def _on_hardware_properties_changed(
        self, _conn, _sender, path, _iface, _signal, params, _user_data,
    ) -> None:
        try:
            changed_iface, changed, _invalid = params.unpack()
        except Exception:
            return
        try:
            if path == _NM_PATH and changed_iface == _NM_IFACE:
                if any(k in changed for k in ('WirelessEnabled', 'WwanEnabled')):
                    self.refresh_states()
            elif (path == _BLUEZ_ADAPTER_PATH
                    and changed_iface == _BLUEZ_ADAPTER_IFACE):
                if 'Powered' in changed:
                    self.refresh_states()
        except Exception:
            pass

    def _apply_tile_state(self, key: str, state: bool,
                            detail: str | None = None) -> None:
        self._tile_state[key] = state
        if detail is not None:
            try:
                self._tile_details[key] = detail
            except Exception:
                pass
        elif key in getattr(self, '_tile_details', {}):
            detail = self._tile_details[key]
        btn = self._tile_buttons.get(key)
        if btn:
            self._updating = True
            btn.set_active(state)
            self._updating = False
        self._apply_tile_ui(key, state, detail)

    def _apply_tile_ui(self, key: str, state: bool,
                       detail: str | None = None) -> None:
        if key == 'captions':
            self._paint_captions_tile()
            return
        if detail is None:
            stored = getattr(self, '_tile_details', None) or {}
            detail = stored.get(key)
        btn = self._tile_buttons.get(key)
        if btn:
            if state:
                btn.add_css_class('active')
            else:
                btn.remove_css_class('active')
        self._set_tile_class(key, 'attention',
                             tile_needs_attention(key, state, detail))
        lbl = self._state_labels.get(key)
        if lbl:
            lbl.set_text(format_tile_state(key, state, detail))

    def _set_tile_class(self, key: str, css: str, on: bool) -> None:
        """Toggle a look class (working/attention) on one tile."""
        btn = (getattr(self, '_tile_buttons', None) or {}).get(key)
        if btn is None:
            return
        try:
            if on:
                btn.add_css_class(css)
            else:
                btn.remove_css_class(css)
        except Exception:
            pass

    def expand(self, expanded: bool) -> None:
        self.tier2_grid.set_visible(expanded)
        self.sep.set_visible(expanded)
        if expanded:
            self._sync_sliders_to_live_state_async()

    def sync_sliders_async(self) -> None:
        """sync_sliders without blocking: the reads (brightnessctl,
        pactl) run on a worker and the values land on the main loop."""
        self._sync_sliders_to_live_state_async()

    def sync_sliders(self) -> None:
        self._sync_sliders_to_live_state()

    def _sync_sliders_to_live_state(self) -> None:
        # One getter batch per expand feeds every slider; the sync guard keeps
        # the set_value round-trip from queueing a redundant apply.
        bright, vol = _read_slider_values()
        self._apply_slider_values(bright, vol)

    def _apply_slider_values(self, bright: int, vol: int) -> bool:
        self._syncing_sliders = True
        try:
            self._bright_slider.set_value(bright)
            self._vol_slider.set_value(vol)
        finally:
            self._syncing_sliders = False
        self._set_readout('bright', bright)
        self._set_readout('vol', vol)
        return False

    def _sync_sliders_to_live_state_async(self) -> None:
        """brightnessctl/pactl reads leave the main thread on expand."""
        def _work() -> None:
            try:
                vals = _read_slider_values()
            except Exception:
                return
            _post_to_main(self._apply_slider_values, *vals)

        self._spawn_panel_worker(_work)

    def refresh_states(self) -> None:
        """Re-read every tile state off-thread; results apply on the main loop."""
        def _work() -> None:
            try:
                states = self._query_tile_states()
            except Exception:
                return
            try:
                details = _query_tile_details()
            except Exception:
                details = {}
            try:
                nope = getattr(self, '_nope', None)
                if nope is not None:
                    detail = nope.tile_detail()
                    if detail:
                        details['nope'] = detail
            except Exception:
                pass
            _post_to_main(self._apply_queried_states, states, details)

        self._spawn_panel_worker(_work)
