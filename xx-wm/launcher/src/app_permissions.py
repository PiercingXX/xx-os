"""Per-app permissions backend (no UI yet; the settings surface lands later).

Network per app goes through the root helper
(helper_client.set_app_network): the app's systemd scope cgroup is blocked
before its first packets. Scope paths are never assumed — they are resolved
by reading /proc/<pid>/cgroup, so a stale or wrong path can never block an
innocent cgroup.

Camera/microphone/location per app go through the xdg-desktop-portal
PermissionStore (tables `devices` and `location`). That store governs
portal-mediated access only (Flatpak and portal-using apps): native apps
reach /dev/video* and PipeWire directly, so for those the privacy
indicators are the control surface. See docs/config.md.

All D-Bus calls here are synchronous and belong on a worker thread, never
the GTK main loop. Every reply is a TUPLE of out-args: unpack()[0] holds
the value, and a{sX} maps may carry GLib.Variants that need unpacking.
"""
from __future__ import annotations

import os
from pathlib import PurePath

from shell_log import get_logger, log_exc


_log = get_logger('permissions')

CGROUP_ROOT = '/sys/fs/cgroup'
PROC_ROOT = '/proc'

PERMISSION_STORE_BUS = 'org.freedesktop.impl.portal.PermissionStore'
PERMISSION_STORE_PATH = '/org/freedesktop/impl/portal/PermissionStore'
PERMISSION_STORE_IFACE = PERMISSION_STORE_BUS

DEVICES_TABLE = 'devices'
DEVICE_CAMERA = 'camera'
DEVICE_MICROPHONE = 'microphone'
DEVICE_SPEAKERS = 'speakers'

LOCATION_TABLE = 'location'
LOCATION_ID = 'location'

PERMISSION_YES = ('yes',)
PERMISSION_NO = ('no',)


def normalize_app_id(app_id: str) -> str:
    """Compare app ids stripped of `.desktop` and case (matches the
    standby/mute normalization so one denial list meets every caller)."""
    text = (app_id or '').strip()
    if text.lower().endswith('.desktop'):
        text = text[:-8]
    return text.casefold()


def expected_scope_relpath(uid: int, scope: str) -> str:
    """Where systemd puts a user scope, relative to the cgroup2 mount."""
    return ('user.slice/user-%d.slice/user@%d.service/app.slice/%s'
            % (int(uid), int(uid), scope))


def parse_proc_cgroup(text: str) -> list[str]:
    """cgroup paths from /proc/<pid>/cgroup content (`hid:id:path` lines)."""
    paths = []
    for line in (text or '').splitlines():
        _hid, sep, rest = line.partition(':')
        if not sep:
            continue
        _cid, sep2, path = rest.partition(':')
        if not sep2 or not path.startswith('/'):
            continue
        paths.append(path)
    return paths


def find_scope_cgroup(scope: str, pid: int,
                      proc_root: str = PROC_ROOT,
                      cgroup_root: str = CGROUP_ROOT) -> str | None:
    """Full cgroupfs path for a tracked scope, verified against
    /proc/<pid>/cgroup. Returns None unless the process actually lives in
    a cgroup named exactly `scope` — never a guessed path."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0 or not scope or '/' in scope or scope in ('.', '..'):
        return None
    try:
        with open(os.path.join(proc_root, str(pid), 'cgroup'),
                  encoding='utf-8') as handle:
            text = handle.read()
    except OSError:
        return None
    for path in parse_proc_cgroup(text):
        if path.rsplit('/', 1)[-1] == scope:
            return cgroup_root.rstrip('/') + path
    return None


def is_flatpak_desktop_path(path: object) -> bool:
    """True when a .desktop file path lives under a Flatpak exports tree
    (`/var/lib/flatpak/...`, `~/.local/share/flatpak/...`). The settings
    UI tags those apps as Flatpak (store-governed) vs native."""
    try:
        return 'flatpak' in PurePath(str(path or '')).parts
    except Exception:
        return False


def app_is_flatpak(entry: object) -> bool:
    """True when a drawer AppEntry resolves to a Flatpak-exported desktop
    file. Unknown or unreadable entries read as native, never Flatpak."""
    try:
        filename = entry.desktop_app.get_filename()
    except Exception:
        return False
    if not filename:
        return False
    return is_flatpak_desktop_path(filename)


def is_network_denied(app_id: str, denied_ids) -> bool:
    """True when app_id is in the network_denied_apps list."""
    wanted = normalize_app_id(app_id)
    for entry in denied_ids or ():
        if normalize_app_id(str(entry)) == wanted:
            return True
    return False


def enforce_network_policy(app_id: str, cgroup_path: str | None,
                           denied_ids, set_network) -> bool:
    """Apply the network policy for one placed scope. Returns True when a
    block was requested through set_network(cgroup_path, allowed)."""
    if not cgroup_path or not is_network_denied(app_id, denied_ids):
        return False
    try:
        set_network(cgroup_path, False)
    except Exception:
        log_exc('app_permissions: network block for %s' % app_id)
        return False
    return True


def default_network_blocker(cgroup_path: str, allowed: bool) -> None:
    """helper_client.set_app_network as a fire-and-forget blocker."""
    try:
        import helper_client
    except ImportError as error:
        _log.info('network block unavailable: %s', error)
        return

    def _done(ok: bool, result) -> None:
        if not ok:
            _log.info('network block for %s refused: %s',
                      cgroup_path, result)

    try:
        helper_client.set_app_network(cgroup_path, bool(allowed), _done)
    except Exception:
        log_exc('app_permissions: set_app_network')


def _glib():
    from gi.repository import Gio, GLib
    return Gio, GLib


def _deep(value: object) -> object:
    """Unpack a GLib.Variant, passing natives through (a{sX} values may
    stay Variants inside real D-Bus replies)."""
    unpack = getattr(value, 'unpack', None)
    if callable(unpack):
        try:
            return _deep(unpack())
        except Exception:
            return value
    return value


def lookup_permissions(bus: object, table: str,
                       entry_id: str) -> dict[str, list[str]]:
    """app_id -> permission list from PermissionStore Lookup(table, id).

    The reply unpacks to a single a{sas} map; unknown table/id reads as an
    empty map, never an error to the caller.
    """
    Gio, GLib = _glib()
    try:
        reply = bus.call_sync(
            PERMISSION_STORE_BUS, PERMISSION_STORE_PATH,
            PERMISSION_STORE_IFACE, 'Lookup',
            GLib.Variant('(ss)', (str(table), str(entry_id))),
            GLib.VariantType('(a{sas})'),
            Gio.DBusCallFlags.NONE, 2000, None,
        ).unpack()[0]
    except Exception:
        return {}
    raw = _deep(reply)
    if not isinstance(raw, dict):
        return {}
    parsed: dict[str, list[str]] = {}
    for app, perms in raw.items():
        perms = _deep(perms)
        try:
            parsed[str(app)] = [str(p) for p in perms]
        except TypeError:
            continue
    return parsed


def set_permission(bus: object, table: str, entry_id: str,
                   app_id: str, permissions) -> None:
    """PermissionStore SetPermission (create the entry when missing)."""
    Gio, GLib = _glib()
    bus.call_sync(
        PERMISSION_STORE_BUS, PERMISSION_STORE_PATH,
        PERMISSION_STORE_IFACE, 'SetPermission',
        GLib.Variant('(sbssas)', (str(table), True, str(entry_id),
                                  str(app_id), [str(p) for p in permissions])),
        None, Gio.DBusCallFlags.NONE, 2000, None,
    )


def delete_permission(bus: object, table: str, entry_id: str,
                      app_id: str) -> None:
    """PermissionStore DeletePermission (reset the app to the ask default)."""
    Gio, GLib = _glib()
    bus.call_sync(
        PERMISSION_STORE_BUS, PERMISSION_STORE_PATH,
        PERMISSION_STORE_IFACE, 'DeletePermission',
        GLib.Variant('(sss)', (str(table), str(entry_id), str(app_id))),
        None, Gio.DBusCallFlags.NONE, 2000, None,
    )


def permission_allowed(permissions) -> bool | None:
    """True/False for a stored ['yes']/['no'], None for ask/unset."""
    try:
        perms = [str(p).lower() for p in permissions or ()]
    except TypeError:
        return None
    if 'yes' in perms:
        return True
    if 'no' in perms:
        return False
    return None


def get_device_permission(bus: object, device_id: str,
                          app_id: str) -> bool | None:
    """Stored camera/microphone/speakers grant for one app."""
    perms = lookup_permissions(bus, DEVICES_TABLE, device_id)
    for stored_app, stored_perms in perms.items():
        if normalize_app_id(stored_app) == normalize_app_id(app_id):
            return permission_allowed(stored_perms)
    return None


def set_device_permission(bus: object, device_id: str, app_id: str,
                          allowed: bool | None) -> None:
    """Grant (True), deny (False), or reset to ask (None) a device."""
    if allowed is None:
        delete_permission(bus, DEVICES_TABLE, device_id, app_id)
    elif allowed:
        set_permission(bus, DEVICES_TABLE, device_id, app_id,
                       PERMISSION_YES)
    else:
        set_permission(bus, DEVICES_TABLE, device_id, app_id, PERMISSION_NO)


def get_location_permission(bus: object, app_id: str) -> bool | None:
    """Stored location grant for one app."""
    perms = lookup_permissions(bus, LOCATION_TABLE, LOCATION_ID)
    for stored_app, stored_perms in perms.items():
        if normalize_app_id(stored_app) == normalize_app_id(app_id):
            return permission_allowed(stored_perms)
    return None


def set_location_permission(bus: object, app_id: str,
                            allowed: bool | None) -> None:
    """Grant (True), deny (False), or reset to ask (None) location."""
    if allowed is None:
        delete_permission(bus, LOCATION_TABLE, LOCATION_ID, app_id)
    elif allowed:
        set_permission(bus, LOCATION_TABLE, LOCATION_ID, app_id,
                       PERMISSION_YES)
    else:
        set_permission(bus, LOCATION_TABLE, LOCATION_ID, app_id,
                       PERMISSION_NO)
