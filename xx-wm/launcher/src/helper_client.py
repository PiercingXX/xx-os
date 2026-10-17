"""Async client for the privileged helper service (``f_helper``).

The shell never runs as root: battery charge limits, per-app network
blocks and cgroup freezing go through the ``io.piercingxx.XXWM.Helper1``
system-bus service. Every call here is async on the caller's GLib main
loop — ``on_done(ok, result)`` fires exactly once with ``(True, value)``
or ``(False, error_string)`` — and a missing helper degrades to the
``HELPER_NOT_INSTALLED`` error instead of raising.

Gio is imported lazily so this module stays importable without
PyGObject (tests, doc builds); the first call on a system without Gio
reports failure through the callback.
"""
from __future__ import annotations

from typing import Any, Callable

BUS_NAME = 'io.piercingxx.XXWM.Helper1'
OBJECT_PATH = '/io/piercingxx/XXWM/Helper1'
INTERFACE = BUS_NAME

HELPER_NOT_INSTALLED = 'helper not installed'

_CALL_TIMEOUT_MS = 25_000

ResultCallback = Callable[[bool, Any], None]

_capabilities_cache: dict | None = None


def _gio():
    from gi.repository import Gio, GLib
    return Gio, GLib


def invalidate_capabilities() -> None:
    """Drop the cached capability probe (call when the helper is installed
    or upgraded mid-session)."""
    global _capabilities_cache
    _capabilities_cache = None


def _is_missing_service(message: str) -> bool:
    lowered = (message or '').lower()
    return ('serviceunknown' in lowered or 'namehasnoowner' in lowered
            or 'activat' in lowered or 'no such service' in lowered)


def _call_helper(method: str, params, on_done: ResultCallback) -> None:
    """One async helper call; ``params`` is a GLib.Variant or None."""
    try:
        Gio, _GLib = _gio()
    except ImportError as err:
        on_done(False, 'D-Bus unavailable: %s' % err)
        return

    def _fail(message: str) -> None:
        on_done(False, HELPER_NOT_INSTALLED
                if _is_missing_service(message) else message)

    def _on_bus(source, result) -> None:
        try:
            bus = Gio.bus_get_finish(result)
        except Exception as err:
            _fail(str(err))
            return

        def _on_reply(conn, reply_result) -> None:
            try:
                reply = conn.call_finish(reply_result)
            except Exception as err:
                _fail(str(err))
                return
            try:
                out = reply.unpack()
            except Exception as err:
                _fail(str(err))
                return
            on_done(True, out[0] if out else None)

        try:
            bus.call(
                BUS_NAME, OBJECT_PATH, INTERFACE, method, params, None,
                Gio.DBusCallFlags.NONE, _CALL_TIMEOUT_MS, None, _on_reply,
            )
        except Exception as err:
            _fail(str(err))

    try:
        Gio.bus_get(Gio.BusType.SYSTEM, None, _on_bus)
    except Exception as err:
        _fail(str(err))


def get_capabilities(on_done: ResultCallback, *, refresh: bool = False) -> None:
    """Probe helper capabilities (cached per session unless ``refresh``).

    ``on_done(True, {'charge_limit_supported': bool, 'nftables': bool,
    'cgroup2': bool, 'freezer': bool})`` or ``(False, error)`` where a
    missing helper yields ``HELPER_NOT_INSTALLED``.
    """
    if _capabilities_cache is not None and not refresh:
        on_done(True, dict(_capabilities_cache))
        return

    def _on_reply(ok: bool, result) -> None:
        if ok and isinstance(result, dict):
            global _capabilities_cache
            _capabilities_cache = dict(result)
            on_done(True, dict(result))
            return
        on_done(False, result)

    _call_helper('GetCapabilities', None, _on_reply)


def set_charge_limit(percent: int, on_done: ResultCallback) -> None:
    """Ask the helper to clamp and apply a battery charge limit (50..100).

    ``on_done(True, None)`` on success, ``(False, error)`` otherwise.
    """
    try:
        Gio, GLib = _gio()
        params = GLib.Variant('(u)', (max(0, int(percent)),))
    except ImportError as err:
        on_done(False, 'D-Bus unavailable: %s' % err)
        return
    except (TypeError, ValueError):
        on_done(False, 'charge limit is not a number: %r' % (percent,))
        return
    _call_helper('SetChargeLimit', params,
                 lambda ok, _result: on_done(ok, None if ok else _result))


def set_app_network(cgroup_path: str, allowed: bool,
                    on_done: ResultCallback) -> None:
    """Block (``allowed`` False) or unblock network for one app cgroup.

    ``on_done(True, None)`` on success, ``(False, error)`` otherwise.
    """
    try:
        Gio, GLib = _gio()
        params = GLib.Variant('(sb)', (str(cgroup_path), bool(allowed)))
    except ImportError as err:
        on_done(False, 'D-Bus unavailable: %s' % err)
        return
    _call_helper('SetAppNetwork', params,
                 lambda ok, _result: on_done(ok, None if ok else _result))


def freeze_cgroup(cgroup_path: str, frozen: bool,
                  on_done: ResultCallback) -> None:
    """Freeze (``frozen`` True) or thaw one app cgroup (non-systemd path).

    ``on_done(True, None)`` on success, ``(False, error)`` otherwise.
    """
    try:
        Gio, GLib = _gio()
        params = GLib.Variant('(sb)', (str(cgroup_path), bool(frozen)))
    except ImportError as err:
        on_done(False, 'D-Bus unavailable: %s' % err)
        return
    _call_helper('FreezeCgroup', params,
                 lambda ok, _result: on_done(ok, None if ok else _result))


def clean_system(categories: list[str], on_done: ResultCallback) -> None:
    """Ask the helper to run fixed-allowlist cache cleanups.

    ``categories`` names only the helper's allowlist (``apk-cache``,
    ``paccache-keep``, ``paccache-uninstalled``, ``apt-clean``,
    ``journal-vacuum``); anything else is rejected helper-side.
    ``on_done(True, [cleaned...])`` on success, ``(False, error)``
    otherwise.
    """
    try:
        Gio, GLib = _gio()
        params = GLib.Variant('(as)', ([str(c) for c in categories],))
    except ImportError as err:
        on_done(False, 'D-Bus unavailable: %s' % err)
        return
    except (TypeError, ValueError) as err:
        on_done(False, 'clean categories must be strings: %s' % err)
        return

    def _done(ok: bool, result) -> None:
        on_done(ok, list(result) if ok else result)

    _call_helper('CleanSystem', params, _done)


def read_log(name: str, on_done: ResultCallback) -> None:
    """Ask the helper for an allowlisted log tail ('messages', 'xx-wm',
    'dmesg'). ``on_done(True, text)`` on success, ``(False, error)``
    otherwise. Read-only: the helper caps the size and the shell
    scrubs secrets before display.
    """
    try:
        Gio, GLib = _gio()
        params = GLib.Variant('(s)', (str(name),))
    except ImportError as err:
        on_done(False, 'D-Bus unavailable: %s' % err)
        return
    _call_helper('ReadLog', params, lambda ok, result: on_done(
        ok, str(result or '') if ok else result))


def refresh_package_index(on_done: ResultCallback) -> None:
    """Refresh the apk package index (harmless, ``allow_active=yes``).

    ``on_done(True, None)`` on success, ``(False, error)`` otherwise.
    """
    _call_helper('RefreshPackageIndex', None,
                 lambda ok, _result: on_done(ok, None if ok else _result))


def upgrade_system(on_done: ResultCallback) -> None:
    """Run ``apk upgrade`` through the helper (``auth_admin_keep``: the
    in-shell polkit agent prompts for the device password).

    ``on_done(True, output)`` with the upgrade output tail for the
    shell's update log, ``(False, error)`` otherwise.
    """
    _call_helper('UpgradeSystem', None, on_done)


def remove_package(pkg: str, on_done: ResultCallback) -> None:
    """Remove one distro package (``auth_admin_keep``: the in-shell
    polkit agent prompts; the helper refuses the protected list).

    ``on_done(True, None)`` on success, ``(False, error)`` otherwise.
    """
    try:
        _Gio, GLib = _gio()
        params = GLib.Variant('(s)', (str(pkg),))
    except ImportError as err:
        on_done(False, 'D-Bus unavailable: %s' % err)
        return
    _call_helper('RemovePackage', params,
                 lambda ok, _result: on_done(ok, None if ok else _result))


def manage_service(name: str, action: str, on_done: ResultCallback) -> None:
    """Start/stop/restart/enable/disable one curated service by key
    (``bluetooth``, ``ssh``, … — the helper allowlists the rest).

    ``on_done(True, None)`` on success, ``(False, error)`` otherwise.
    """
    try:
        Gio, GLib = _gio()
        params = GLib.Variant('(ss)', (str(name), str(action)))
    except ImportError as err:
        on_done(False, 'D-Bus unavailable: %s' % err)
        return
    _call_helper('ManageService', params,
                 lambda ok, _result: on_done(ok, None if ok else _result))


def set_power_profile(profile: str, on_done: ResultCallback) -> None:
    """Apply a CPU power profile (``power-saver``, ``balanced``,
    ``performance``) through the helper's allowlisted governor map.

    ``on_done(True, None)`` on success, ``(False, error)`` otherwise.
    """
    try:
        Gio, GLib = _gio()
        params = GLib.Variant('(s)', (str(profile),))
    except ImportError as err:
        on_done(False, 'D-Bus unavailable: %s' % err)
        return
    _call_helper('SetPowerProfile', params,
                 lambda ok, _result: on_done(ok, None if ok else _result))


def set_rfkill(rtype: str, blocked: bool, on_done: ResultCallback) -> None:
    """Block (``blocked`` True) or unblock the wifi/bluetooth radio.

    ``on_done(True, None)`` on success, ``(False, error)`` otherwise.
    """
    try:
        Gio, GLib = _gio()
        params = GLib.Variant('(sb)', (str(rtype), bool(blocked)))
    except ImportError as err:
        on_done(False, 'D-Bus unavailable: %s' % err)
        return
    _call_helper('SetRfkill', params,
                 lambda ok, _result: on_done(ok, None if ok else _result))


def set_inbound_preset(name: str, allowed: bool,
                       on_done: ResultCallback) -> None:
    """Allow (``allowed`` True) or forbid one inbound preset's ports.

    ``on_done(True, None)`` on success, ``(False, error)`` otherwise.
    """
    try:
        Gio, GLib = _gio()
        params = GLib.Variant('(sb)', (str(name), bool(allowed)))
    except ImportError as err:
        on_done(False, 'D-Bus unavailable: %s' % err)
        return
    _call_helper('SetInboundPreset', params,
                 lambda ok, _result: on_done(ok, None if ok else _result))


def set_inbound_allowed(names, on_done: ResultCallback) -> None:
    """Replace the whole inbound allowed set with the features' derived
    presets (``SetInboundAllowed``).

    ``names`` lists only the curated preset names; anything else is
    rejected helper-side. ``on_done(True, None)`` on success,
    ``(False, error)`` otherwise.
    """
    try:
        Gio, GLib = _gio()
        params = GLib.Variant('(as)', ([str(name) for name in names],))
    except ImportError as err:
        on_done(False, 'D-Bus unavailable: %s' % err)
        return
    except TypeError as err:
        on_done(False, 'inbound names must be a list: %s' % err)
        return
    _call_helper('SetInboundAllowed', params,
                 lambda ok, _result: on_done(ok, None if ok else _result))


def set_firewall_enabled(enabled: bool, on_done: ResultCallback) -> None:
    """Enable (``enabled`` True) or disable the inbound firewall.

    ``on_done(True, None)`` on success, ``(False, error)`` otherwise.
    """
    try:
        Gio, GLib = _gio()
        params = GLib.Variant('(b)', (bool(enabled),))
    except ImportError as err:
        on_done(False, 'D-Bus unavailable: %s' % err)
        return
    _call_helper('SetFirewallEnabled', params,
                 lambda ok, _result: on_done(ok, None if ok else _result))


def get_firewall_state(on_done: ResultCallback) -> None:
    """Read backend, on/off and per-preset inbound state (no auth needed).

    ``on_done(True, {'backend': ..., 'enabled': bool,
    'allowed': {name: bool}, 'foreign_drop': bool})`` or ``(False, error)``
    where a missing helper yields ``HELPER_NOT_INSTALLED``.
    """
    def _on_reply(ok: bool, result) -> None:
        if not ok:
            on_done(False, HELPER_NOT_INSTALLED
                    if _is_missing_service(str(result)) else result)
            return
        if isinstance(result, dict):
            on_done(True, {key: result.get(key) for key in
                           ('backend', 'enabled', 'allowed',
                            'foreign_drop')})
            return
        on_done(False, result)

    _call_helper('GetFirewallState', None, _on_reply)
