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

import logging as _logging
from typing import Any, Callable

BUS_NAME = 'io.piercingxx.XXWM.Helper1'
OBJECT_PATH = '/io/piercingxx/XXWM/Helper1'
INTERFACE = BUS_NAME

HELPER_NOT_INSTALLED = 'helper not installed'

_CALL_TIMEOUT_MS = 25_000
_QUICK_TIMEOUT_MS = 10_000
# G_MAXINT: no client timeout. These calls may wait on the polkit agent
# while the user types the device password; a helper that dies still
# answers through the bus with NoReply.
_NO_TIMEOUT_MS = 2 ** 31 - 1
_METHOD_TIMEOUT_MS = {
    'GetCapabilities': _QUICK_TIMEOUT_MS,
    'GetFirewallState': _QUICK_TIMEOUT_MS,
    'HasPin': _QUICK_TIMEOUT_MS,
    'VerifyPin': _QUICK_TIMEOUT_MS,
    'SetPin': _QUICK_TIMEOUT_MS,
    'ClearPin': _QUICK_TIMEOUT_MS,
    'SetChargeLimit': _QUICK_TIMEOUT_MS,
    'SetRfkill': _QUICK_TIMEOUT_MS,
    'SetPowerProfile': _QUICK_TIMEOUT_MS,
    'SetWakeAlarm': _QUICK_TIMEOUT_MS,
    'ClearWakeAlarm': _QUICK_TIMEOUT_MS,
    'UpgradeSystem': _NO_TIMEOUT_MS,
    'RemovePackage': _NO_TIMEOUT_MS,
    'ManageService': _NO_TIMEOUT_MS,
    'ReadLog': _NO_TIMEOUT_MS,
    'CleanSystem': _NO_TIMEOUT_MS,
    'SetInboundPreset': _NO_TIMEOUT_MS,
    'SetInboundAllowed': _NO_TIMEOUT_MS,
    'SetFirewallEnabled': _NO_TIMEOUT_MS,
}

_DBUS_NAME = 'org.freedesktop.DBus'
_DBUS_PATH = '/org/freedesktop/DBus'


def call_timeout_ms(method: str) -> int:
    return _METHOD_TIMEOUT_MS.get(method, _CALL_TIMEOUT_MS)

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


def _call_helper(method: str, params, on_done: ResultCallback, *,
                 whole: bool = False) -> None:
    """One async helper call; ``params`` is a GLib.Variant or None.
    ``whole`` hands back every out-arg as a tuple instead of the first."""
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
            if whole:
                on_done(True, tuple(out))
                return
            on_done(True, out[0] if out else None)

        try:
            bus.call(
                BUS_NAME, OBJECT_PATH, INTERFACE, method, params, None,
                Gio.DBusCallFlags.NONE, call_timeout_ms(method), None,
                _on_reply,
            )
        except Exception as err:
            _fail(str(err))

    try:
        Gio.bus_get(Gio.BusType.SYSTEM, None, _on_bus)
    except Exception as err:
        _fail(str(err))


def _run_job(method: str, params, on_done: ResultCallback) -> None:
    """Start a helper package job and report when it ends.

    The call itself only returns a job id (after the polkit prompt); the
    result arrives as ``JobFinished(job_id, rc, tail)``, so the package
    manager can run as long as it needs. ``on_done(True, tail)`` for
    rc 0, ``(False, tail or 'exit N')`` otherwise, once. The helper
    vanishing mid-job fails the job instead of waiting forever.
    """
    try:
        Gio, _GLib = _gio()
    except ImportError as err:
        on_done(False, 'D-Bus unavailable: %s' % err)
        return

    state = {'bus': None, 'subs': [], 'job': None, 'done': False,
             'vanished': False}
    finished: dict[str, tuple] = {}

    def _finish(ok: bool, result) -> None:
        if state['done']:
            return
        state['done'] = True
        bus = state['bus']
        for sub in state['subs']:
            try:
                bus.signal_unsubscribe(sub)
            except Exception:
                pass
        state['subs'] = []
        on_done(ok, result)

    def _fail(message: str) -> None:
        _finish(False, HELPER_NOT_INSTALLED
                if _is_missing_service(message) else message)

    def _deliver(rc: int, tail: str) -> None:
        if rc == 0:
            _finish(True, tail)
        else:
            _finish(False, (tail or '').strip() or 'exit %d' % rc)

    def _on_job_finished(_conn, _sender, _path, _iface, _signal, params,
                         *_user_data) -> None:
        try:
            job_id, rc, tail = params.unpack()
        except Exception:
            return
        if state['job'] is None:
            finished[job_id] = (int(rc), str(tail))
        elif job_id == state['job']:
            _deliver(int(rc), str(tail))

    def _on_owner_changed(_conn, _sender, _path, _iface, _signal, params,
                          *_user_data) -> None:
        try:
            _name, _old, new = params.unpack()
        except Exception:
            return
        # An old instance idle-exiting right before activation is not
        # this job's helper going away.
        state['vanished'] = not new
        if new:
            return
        if state['job'] is not None:
            _finish(False, 'helper stopped before the job finished')

    def _on_bus(source, result) -> None:
        try:
            bus = Gio.bus_get_finish(result)
        except Exception as err:
            _fail(str(err))
            return
        state['bus'] = bus

        def _on_reply(conn, reply_result) -> None:
            try:
                job_id = str(conn.call_finish(reply_result).unpack()[0])
            except Exception as err:
                _fail(str(err))
                return
            state['job'] = job_id
            if job_id in finished:
                _deliver(*finished.pop(job_id))
            elif state['vanished']:
                _finish(False, 'helper stopped before the job finished')

        try:
            # Subscribed before the call: JobFinished can race the reply.
            state['subs'].append(bus.signal_subscribe(
                BUS_NAME, INTERFACE, 'JobFinished', OBJECT_PATH, None,
                Gio.DBusSignalFlags.NONE, _on_job_finished, None))
            state['subs'].append(bus.signal_subscribe(
                _DBUS_NAME, _DBUS_NAME, 'NameOwnerChanged', _DBUS_PATH,
                BUS_NAME, Gio.DBusSignalFlags.NONE, _on_owner_changed, None))
            bus.call(
                BUS_NAME, OBJECT_PATH, INTERFACE, method, params, None,
                Gio.DBusCallFlags.NONE, call_timeout_ms(method), None,
                _on_reply,
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
    """Run ``apk upgrade`` as a helper job (``auth_admin_keep``: the
    in-shell polkit agent prompts for the device password).

    ``on_done(True, output)`` with the upgrade output tail for the
    shell's update log once JobFinished arrives, ``(False, error)``
    otherwise.
    """
    _run_job('UpgradeSystem', None, on_done)


def remove_package(pkg: str, on_done: ResultCallback) -> None:
    """Remove one distro package (``auth_admin_keep``: the in-shell
    polkit agent prompts; the helper refuses the protected list).

    ``on_done(True, None)`` once the removal job finished cleanly,
    ``(False, error)`` otherwise.
    """
    try:
        _Gio, GLib = _gio()
        params = GLib.Variant('(s)', (str(pkg),))
    except ImportError as err:
        on_done(False, 'D-Bus unavailable: %s' % err)
        return
    _run_job('RemovePackage', params,
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


def set_wake_alarm(epoch: int, on_done: ResultCallback) -> None:
    """Arm the RTC to wake a suspended phone at absolute ``epoch`` seconds
    (the helper allows 5 s .. 48 h ahead and owns a single wake).

    ``on_done(True, None)`` on success, ``(False, error)`` otherwise.
    """
    try:
        Gio, GLib = _gio()
        params = GLib.Variant('(t)', (max(0, int(epoch)),))
    except ImportError as err:
        on_done(False, 'D-Bus unavailable: %s' % err)
        return
    except (TypeError, ValueError, OverflowError):
        on_done(False, 'wake time is not a number: %r' % (epoch,))
        return
    _call_helper('SetWakeAlarm', params,
                 lambda ok, _result: on_done(ok, None if ok else _result))


def clear_wake_alarm(on_done: ResultCallback) -> None:
    """Drop the helper's RTC wake (a wake another program set is kept).

    ``on_done(True, None)`` on success, ``(False, error)`` otherwise.
    """
    _call_helper('ClearWakeAlarm', None,
                 lambda ok, _result: on_done(ok, None if ok else _result))


# ------------------------------------------------------------- PIN store
#
# The PIN hash lives root-side (HasPin/VerifyPin/SetPin/ClearPin, scoped to
# the caller's bus uid, with a persisted lockout). config.json keeps only
# the marker ``config.HELPER_PIN_MARKER`` so every "is a PIN set?" check
# stays synchronous. Installs without the helper keep the legacy local
# pbkdf2 hash in config (dev/test, older installs); a later successful
# unlock migrates it. PIN text is never logged.

_log = _logging.getLogger('helper_client')
_pin_logged: set[str] = set()

# (matched_or_done: bool, retry_after_secs: int, error: str | None)
PinCallback = Callable[[bool, int, Any], None]


def _log_pin_once(key: str, level: int, message: str) -> None:
    if key not in _pin_logged:
        _pin_logged.add(key)
        _log.log(level, message)


def pin_helper_enabled(environ=None) -> bool:
    """Whether PINs move into the helper: in the real session only
    (XX_WM_SESSION=1, like desktop_theme.in_session), so tests and dev
    runs never touch the device's root-side record. XX_WM_PIN_HELPER=0/1
    overrides. A PIN already held by the helper (config marker) is always
    verified there regardless."""
    import os
    env = os.environ if environ is None else environ
    override = env.get('XX_WM_PIN_HELPER')
    if override in ('0', '1'):
        return override == '1'
    return env.get('XX_WM_SESSION') == '1'


def has_pin(on_done: ResultCallback) -> None:
    """``on_done(True, bool)`` or ``(False, error)``."""
    _call_helper('HasPin', None, lambda ok, result: on_done(
        ok, bool(result) if ok else result))


def verify_pin(pin: str, on_done: ResultCallback) -> None:
    """``on_done(True, (matched, retry_after, has_pin))`` or
    ``(False, error)``. The KDF runs in the helper process."""
    try:
        _Gio, GLib = _gio()
        params = GLib.Variant('(s)', (str(pin),))
    except ImportError as err:
        on_done(False, 'D-Bus unavailable: %s' % err)
        return
    _call_helper('VerifyPin', params, on_done, whole=True)


def set_pin(old: str, new: str, on_done: ResultCallback) -> None:
    """``on_done(True, (done, retry_after))`` or ``(False, error)``."""
    try:
        _Gio, GLib = _gio()
        params = GLib.Variant('(ss)', (str(old), str(new)))
    except ImportError as err:
        on_done(False, 'D-Bus unavailable: %s' % err)
        return
    _call_helper('SetPin', params, on_done, whole=True)


def clear_pin(old: str, on_done: ResultCallback) -> None:
    """``on_done(True, (done, retry_after))`` or ``(False, error)``."""
    try:
        _Gio, GLib = _gio()
        params = GLib.Variant('(s)', (str(old),))
    except ImportError as err:
        on_done(False, 'D-Bus unavailable: %s' % err)
        return
    _call_helper('ClearPin', params, on_done, whole=True)


def _in_helper(config) -> bool:
    # getattr: duck-typed configs (tests, older callers) predate the marker.
    return bool(getattr(config, 'pin_in_helper', False))


def _note_unavailable(error) -> None:
    if error == HELPER_NOT_INSTALLED:
        _log_pin_once('pin-local', _logging.INFO,
                      'PIN helper not installed; PIN stays in config.json')
    else:
        _log_pin_once('pin-error', _logging.WARNING,
                      'PIN helper unreachable: %s' % (error,))


def migrate_pin(config, pin: str, on_done=None) -> None:
    """Move a locally stored PIN into the helper.

    Called after the local hash verified ``pin`` (unlock, or a PIN just
    set through config.set_pin). On success the config copy is replaced
    by the marker — only if the stored hash is still the one we started
    from, so a concurrent change is never clobbered. Helper missing:
    the config hash stays (logged once). ``on_done(migrated: bool)``.
    """
    finish = on_done or (lambda _migrated: None)
    started_from = config.pin_hash
    if (started_from is None or _in_helper(config)
            or not pin_helper_enabled()):
        finish(False)
        return

    def _adopt() -> None:
        if config.pin_hash == started_from:
            try:
                config.set_pin_in_helper()
            except OSError:
                finish(False)
                return
            _log_pin_once('pin-migrated', _logging.INFO,
                          'PIN moved to the root helper')
            finish(True)
            return
        finish(False)

    def _on_verified(ok: bool, result) -> None:
        # A record already held there (stale, or a previous half-done
        # migration) is adopted only when it matches this very PIN.
        if ok and isinstance(result, tuple) and result and result[0]:
            _adopt()
            return
        _log_pin_once('pin-stale', _logging.WARNING,
                      'helper holds a different PIN record; '
                      'PIN stays in config.json')
        finish(False)

    def _on_set(ok: bool, result) -> None:
        if not ok:
            _note_unavailable(result)
            finish(False)
            return
        if isinstance(result, tuple) and result and result[0]:
            _adopt()
            return
        verify_pin(pin, _on_verified)

    set_pin('', pin, _on_set)


def pin_verify(config, pin: str, on_result: PinCallback) -> None:
    """Verify an unlock PIN: ``on_result(matched, retry_after, error)``.

    No PIN: matches at once. Helper-held PIN: async VerifyPin, failing
    closed when the helper is unreachable (``error`` set). Local hash:
    verified synchronously as before, then migrated on success.
    """
    if config.pin_hash is None:
        on_result(True, 0, None)
        return

    def _verify_local() -> None:
        try:
            matched = bool(config.verify_pin(pin))
        except Exception:
            matched = False
        on_result(matched, 0, None)
        if matched:
            try:
                migrate_pin(config, pin)
            except Exception:
                pass

    if not _in_helper(config):
        _verify_local()
        return

    def _reply(ok: bool, result) -> None:
        if not ok:
            _note_unavailable(result)
            on_result(False, 0, 'PIN service unavailable')
            return
        try:
            matched, retry, has = result
        except (TypeError, ValueError):
            on_result(False, 0, 'PIN service error')
            return
        if not has:
            # The helper lost the record (state wiped by root): treat the
            # marker as stale, exactly like an absent config key. A local
            # hash under an assumed marker (startup_pin_gate) stands.
            _log_pin_once('pin-lost', _logging.WARNING,
                          'helper has no PIN record; clearing the marker')
            drop = getattr(config, 'drop_helper_pin', None) or config.clear_pin
            try:
                drop()
            except OSError:
                pass
            if config.pin_hash is None:
                on_result(True, 0, None)
            else:
                _verify_local()
            return
        on_result(bool(matched), int(retry or 0), None)

    verify_pin(pin, _reply)


def pin_change(config, current: str, new: str,
               on_result: PinCallback) -> None:
    """Store ``new`` as the PIN: ``on_result(done, retry_after, error)``.

    Helper-held PIN: SetPin(current, new) — the helper verifies
    ``current`` and applies its lockout. Otherwise (no PIN, or a local
    hash the caller has already verified against ``current``): the PIN
    is written to config first, so it is never lost, then moved into
    the helper when it answers.
    """
    if _in_helper(config):
        def _reply(ok: bool, result) -> None:
            if not ok:
                _note_unavailable(result)
                on_result(False, 0, 'PIN service unavailable')
                return
            try:
                done, retry = result
            except (TypeError, ValueError):
                on_result(False, 0, 'PIN service error')
                return
            on_result(bool(done), int(retry or 0), None)

        set_pin(current, new, _reply)
        return
    try:
        config.set_pin(new)
    except Exception:
        on_result(False, 0, 'Could not save the new PIN.')
        return
    on_result(True, 0, None)
    try:
        migrate_pin(config, new)
    except Exception:
        pass


# dbus-daemon's (and dbus-broker's) standard system-bus activation
# directories; meson installs the helper's file under $datadir.
HELPER_SERVICE_DIRS = (
    '/usr/local/share/dbus-1/system-services',
    '/usr/share/dbus-1/system-services',
    '/lib/dbus-1/system-services',
    '/etc/dbus-1/system-services',
)
STARTUP_PIN_WAIT_MS = 3000


def helper_installed(dirs=None) -> bool:
    """Whether the system bus can start the helper: its D-Bus activation
    file is installed. A helper that was never installed cannot hold a
    PIN, so its silence never locks anyone out."""
    import os
    name = BUS_NAME + '.service'
    return any(os.path.isfile(os.path.join(d, name))
               for d in (HELPER_SERVICE_DIRS if dirs is None else dirs))


def _add_timeout(ms: int, fn) -> None:
    _Gio, GLib = _gio()
    GLib.timeout_add(ms, fn)


def startup_pin_gate(config, on_decided, on_late=None, *,
                     wait_ms: int = STARTUP_PIN_WAIT_MS) -> None:
    """Startup: does a PIN guard this session, and whose? ``on_decided
    (locked)`` fires exactly once, before anything is shown.

    Locked: config holds the marker, or the helper reports a PIN
    (pin_sync restores the marker). A local hash in config is asked
    about too: when the helper holds a PIN, the helper's PIN wins and
    the local hash is replaced by the marker (a hash written into
    config.json while the marker was lost must not become the PIN). No
    answer — an error, or nothing within ``wait_ms`` — fails closed
    while PINs live in the helper and the helper is installed
    (helper_installed): the marker is assumed for this process
    (config.assume_pin_in_helper, not written to disk), so the PIN pad
    verifies through the helper once it answers; pin_verify drops it if
    the helper holds no PIN after all, and a local hash then stands.
    Unlocked: no PIN in config, and the helper answers that it holds
    none, PINs do not live in the helper in this run
    (pin_helper_enabled), or it is not installed.

    A late HasPin answer reconciles: no PIN drops an assumed marker (a
    lock that is up then opens with a swipe, or with the local PIN); a
    PIN persists the marker, and calls ``on_late()`` when the session
    was let up unlocked.
    """
    late = on_late or (lambda: None)
    state = {'decided': False, 'locked': False, 'assumed': False}
    local = config.pin_hash is not None and not _in_helper(config)

    def _decide(locked: bool) -> None:
        state['decided'] = True
        state['locked'] = locked
        on_decided(locked)

    def _adopt() -> None:
        if local:
            _log_pin_once('pin-helper-wins', _logging.WARNING,
                          'helper holds the PIN; local hash in config '
                          'replaced by the marker')
        try:
            config.set_pin_in_helper()
        except (AttributeError, OSError):
            pass

    def _unanswered() -> None:
        if not pin_helper_enabled() or not helper_installed():
            _decide(local)
            return
        assume = getattr(config, 'assume_pin_in_helper', None)
        if callable(assume):
            assume()
            state['assumed'] = True
        _log.warning('PIN helper did not answer; locked until it does')
        _decide(True)

    def _answer(held) -> None:
        if not state['decided']:
            if held is None:
                _unanswered()
                return
            if held and local:
                _adopt()
            _decide(bool(held) or config.pin_hash is not None)
            return
        if held is None:
            return
        assumed, state['assumed'] = state['assumed'], False
        if held:
            if assumed or local:
                _adopt()
            if not state['locked']:
                late()
        elif assumed:
            try:
                config.drop_helper_pin()
            except (AttributeError, OSError):
                pass

    if config.pin_hash is not None and (_in_helper(config)
                                        or not pin_helper_enabled()):
        _decide(True)
        return
    try:
        if local:
            has_pin(lambda ok, result: _answer(bool(result) if ok else None))
        else:
            pin_sync(config, _answer)
    except Exception:
        if state['decided']:
            raise  # from on_decided, not from asking the helper
        _answer(None)
    if state['decided']:
        return

    def _timeout() -> bool:
        if not state['decided']:
            _unanswered()
        return False

    try:
        _add_timeout(wait_ms, _timeout)
    except Exception:
        _timeout()


def pin_sync(config, on_done=None) -> None:
    """Startup heal: a helper record with no config marker (config.json
    reset or restored) re-adds the marker so the lock still demands the
    PIN. ``on_done(has_pin: bool | None)``; None = helper unavailable."""
    finish = on_done or (lambda _has: None)
    if config.pin_hash is not None:
        finish(True)
        return
    if not pin_helper_enabled():
        finish(None)
        return

    def _reply(ok: bool, result) -> None:
        if not ok:
            finish(None)
            return
        if result and config.pin_hash is None:
            try:
                config.set_pin_in_helper()
            except OSError:
                pass
        finish(bool(result))

    has_pin(_reply)
