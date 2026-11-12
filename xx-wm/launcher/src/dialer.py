"""Telephony helpers the shell still needs: place a voice call and the
emergency-call path the lock screen uses. The Phone app is xx-dialer."""
from __future__ import annotations

import re
import subprocess
import time

from gi.repository import Gio, GLib


_CALL_PATH_RE = re.compile(r'/org/freedesktop/ModemManager1/Call/\d+')
_MMCLI_TIMEOUT_S = 15


def _resolved_modem() -> str:
    """Modem selector for mmcli, resolved from D-Bus: modems re-index after
    resets, so the hardcoded index 0 breaks. Falls back to '0' offline."""
    try:
        from modem_monitor import resolve_modem_path
        return resolve_modem_path() or '0'
    except Exception:
        return '0'


def place_call(number: str) -> tuple[bool, str]:
    """Place a voice call to ``number``; (success, detail for the status line).

    Prefers Voice.CreateCall + Call.Start over D-Bus; only when that
    transport fails does it fall back to mmcli (create, then start via the
    returned object path — mmcli has no single dial verb, -o selects Call
    objects). Blocking: call only from a worker thread."""
    try:
        from modem_monitor import voice_create_and_start
        ok, detail = voice_create_and_start(number)
        if ok:
            return True, detail
    except Exception as err:
        detail = str(err)
    modem = _resolved_modem()
    ok, out = _run_mmcli(['-m', modem, f'--voice-create-call=number={number}'])
    if not ok:
        return False, out.strip()
    match = _CALL_PATH_RE.search(out)
    if match is None:
        return False, 'modem returned no call path'
    ok, out = _run_mmcli(['-m', modem, '-o', match.group(0), '--start'])
    return ok, out.strip()


def _run_mmcli(args: list[str]) -> tuple[bool, str]:
    """Run mmcli; returns (success, stdout on success or error detail)."""
    try:
        proc = subprocess.run(
            ['mmcli', *args], capture_output=True, text=True, timeout=_MMCLI_TIMEOUT_S,
        )
    except FileNotFoundError:
        return False, 'mmcli not installed'
    except subprocess.TimeoutExpired:
        return False, 'mmcli timed out'
    if proc.returncode != 0:
        detail = proc.stderr.strip() or f'mmcli exited with status {proc.returncode}'
        return False, detail
    return True, proc.stdout


#: Emergency numbers on every network (3GPP TS 22.101: 112 and 911 are
#: emergency calls wherever the phone is). 999, 000, 110, 119 and the
#: like are emergency numbers in some countries and ordinary, sometimes
#: billable, numbers in others (100, 101, 118 and 08 among them), so they
#: come only from the modem/SIM list for the network the phone is on.
BUILTIN_EMERGENCY_NUMBERS = frozenset({'112', '911'})

EMERGENCY_REFUSAL = 'Only emergency calls from the lock screen'
EMERGENCY_NO_SIM = 'no SIM card'

_MODEM3GPP_IFACE = 'org.freedesktop.ModemManager1.Modem.Modem3gpp'
_MODEM_IFACE = 'org.freedesktop.ModemManager1.Modem'
_SIM_IFACE = 'org.freedesktop.ModemManager1.Sim'
_PROPS_IFACE = 'org.freedesktop.DBus.Properties'
_MM_BUS = 'org.freedesktop.ModemManager1'
_NM_BUS = 'org.freedesktop.NetworkManager'
_NM_PATH = '/org/freedesktop/NetworkManager'
_DBUS_TIMEOUT_MS = 5000
_MODEM_ENABLE_TIMEOUT_MS = 30_000
#: MMModemState DISABLED / DISABLING: airplane mode, or radio switched off.
_MODEM_STATES_OFF = frozenset({3, 4})
_MODEM_STATE_FAILED = -1
#: MMModemStateFailedReason SIM_MISSING / SIM_ERROR.
_FAILED_REASONS_NO_SIM = frozenset({2, 3})
#: After switching the radio back on, the modem needs a few seconds for
#: limited service; the call is retried this many times, this far apart.
_RADIO_RETRIES = 3
_RADIO_RETRY_S = 3.0
_DBUS_ERROR_PREFIX = re.compile(r'^GDBus\.Error:[\w.\-]+:\s*')


def normalize_emergency_number(raw: str) -> str:
    """Digits-only form of a dial string for emergency matching: spaces,
    dashes, dots, parens and a leading + never change which service is
    dialed, so they are stripped before comparison. Pure."""
    if not isinstance(raw, str):
        return ''
    return re.sub(r'\D', '', raw)


def is_emergency_number(raw: str, extra: object = None) -> bool:
    """True when ``raw`` dials an emergency service: the built-in list
    union the modem's own list (``extra``). Exact match on the
    digits-only form — a longer number merely containing 112 is not an
    emergency call. Pure."""
    norm = normalize_emergency_number(raw)
    if not norm:
        return False
    if norm in BUILTIN_EMERGENCY_NUMBERS:
        return True
    if isinstance(extra, (list, tuple, set, frozenset)):
        for candidate in extra:
            if isinstance(candidate, str) and normalize_emergency_number(candidate) == norm:
                return True
    return False


def _str_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, str) and item]


def get_modem_emergency_numbers(bus=None, modem: str | None = None,
                                timeout_ms: int = _DBUS_TIMEOUT_MS) -> list[str]:
    """Emergency numbers the modem/SIM reports: the Modem3gpp
    EmergencyNumbers property, then the SIM's EmergencyNumbers property
    via the modem's Sim path. [] on any failure — every source is
    optional, and a modem without either list still places the built-in
    numbers. Blocking: call only from a worker thread."""
    try:
        if bus is None:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        if bus is None:
            return []
        if modem is None:
            from modem_monitor import resolve_modem_path
            modem = resolve_modem_path()
        if not modem:
            return []
        found: list[str] = []
        try:
            props = dict(bus.call_sync(
                _MM_BUS, modem, _PROPS_IFACE, 'GetAll',
                GLib.Variant('(s)', (_MODEM3GPP_IFACE,)), None,
                Gio.DBusCallFlags.NONE, timeout_ms, None,
            ).unpack()[0])
            found.extend(_str_list(props.get('EmergencyNumbers')))
        except Exception:
            pass
        try:
            sim_path = bus.call_sync(
                _MM_BUS, modem, _PROPS_IFACE, 'Get',
                GLib.Variant('(ss)', (_MODEM_IFACE, 'Sim')), None,
                Gio.DBusCallFlags.NONE, timeout_ms, None,
            ).unpack()[0]
        except Exception:
            sim_path = None
        if sim_path and sim_path != '/':
            try:
                sim_props = dict(bus.call_sync(
                    _MM_BUS, str(sim_path), _PROPS_IFACE, 'GetAll',
                    GLib.Variant('(s)', (_SIM_IFACE,)), None,
                    Gio.DBusCallFlags.NONE, timeout_ms, None,
                ).unpack()[0])
                found.extend(_str_list(sim_props.get('EmergencyNumbers')))
            except Exception:
                pass
        seen: set[str] = set()
        out: list[str] = []
        for number in found:
            norm = normalize_emergency_number(number)
            if norm and norm not in seen:
                seen.add(norm)
                out.append(norm)
        return out
    except Exception:
        return []


def _system_bus(bus=None):
    if bus is not None:
        return bus
    try:
        return Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    except Exception:
        return None


def _voice_modem(modem: str | None) -> str | None:
    if modem:
        return modem
    try:
        from modem_monitor import resolve_modem_path
        return resolve_modem_path()
    except Exception:
        return None


def ensure_radio_on(bus=None, modem: str | None = None,
                    timeout_ms: int = _DBUS_TIMEOUT_MS) -> bool:
    """Airplane mode must not stop an emergency call: switch mobile radio
    back on (NetworkManager WwanEnabled, which airplane mode clears) and
    Enable the modem when ModemManager holds it disabled. True when it
    switched something on. Best effort; blocking: worker thread only."""
    bus = _system_bus(bus)
    if bus is None:
        return False
    changed = False
    try:
        wwan = bus.call_sync(
            _NM_BUS, _NM_PATH, _PROPS_IFACE, 'Get',
            GLib.Variant('(ss)', (_NM_BUS, 'WwanEnabled')), None,
            Gio.DBusCallFlags.NONE, timeout_ms, None,
        ).unpack()[0]
        if wwan is False:
            bus.call_sync(
                _NM_BUS, _NM_PATH, _PROPS_IFACE, 'Set',
                GLib.Variant('(ssv)', (_NM_BUS, 'WwanEnabled',
                                       GLib.Variant('b', True))), None,
                Gio.DBusCallFlags.NONE, timeout_ms, None,
            )
            changed = True
    except Exception:
        pass
    modem = _voice_modem(modem)
    if not modem:
        return changed
    try:
        state = int(bus.call_sync(
            _MM_BUS, modem, _PROPS_IFACE, 'Get',
            GLib.Variant('(ss)', (_MODEM_IFACE, 'State')), None,
            Gio.DBusCallFlags.NONE, timeout_ms, None,
        ).unpack()[0])
    except Exception:
        return changed
    if state in _MODEM_STATES_OFF:
        try:
            bus.call_sync(
                _MM_BUS, modem, _MODEM_IFACE, 'Enable',
                GLib.Variant('(b)', (True,)), None,
                Gio.DBusCallFlags.NONE, _MODEM_ENABLE_TIMEOUT_MS, None,
            )
            changed = True
        except Exception:
            pass
    return changed


def modem_sim_missing(bus=None, modem: str | None = None,
                      timeout_ms: int = _DBUS_TIMEOUT_MS) -> bool:
    """True when the modem reports no usable SIM: no Sim object, or State
    FAILED for a missing or broken SIM. False when that cannot be read.
    Blocking: worker thread only."""
    bus = _system_bus(bus)
    modem = _voice_modem(modem)
    if bus is None or not modem:
        return False
    try:
        props = dict(bus.call_sync(
            _MM_BUS, modem, _PROPS_IFACE, 'GetAll',
            GLib.Variant('(s)', (_MODEM_IFACE,)), None,
            Gio.DBusCallFlags.NONE, timeout_ms, None,
        ).unpack()[0])
    except Exception:
        return False
    try:
        if (int(props.get('State', 0)) == _MODEM_STATE_FAILED
                and int(props.get('StateFailedReason', 0))
                in _FAILED_REASONS_NO_SIM):
            return True
    except (TypeError, ValueError):
        pass
    return 'Sim' in props and str(props.get('Sim') or '/') == '/'


def call_failure_detail(detail: object) -> str:
    """A dial error for the status line: the D-Bus error name dropped
    ("GDBus.Error:org.freedesktop.ModemManager1.Error.Core.WrongState:
    ..." reads as its message)."""
    return _DBUS_ERROR_PREFIX.sub('', str(detail or '').strip())


def place_emergency_call(number: str, extra: object = None) -> tuple[bool, str]:
    """Place ``number`` only when it dials an emergency service; anything
    else is refused without placing a call. Blocking: call only from a
    worker thread — ModemManager places emergency calls even without a
    SIM when the modem and network allow, no unlock needed. Airplane mode
    is switched off for the call (the call is retried while the radio
    comes up), and a failure with no SIM says so."""
    if not is_emergency_number(number, extra):
        # The caller's list may be empty or stale (read before the modem
        # registered): ask the modem again before refusing.
        if not is_emergency_number(number, get_modem_emergency_numbers()):
            return False, EMERGENCY_REFUSAL
    dial = normalize_emergency_number(number)
    try:
        from modem_monitor import voice_create_and_start
        radio_switched_on = ensure_radio_on()
        ok, detail = voice_create_and_start(dial)
        retries = _RADIO_RETRIES if radio_switched_on else 0
        while not ok and retries > 0:
            retries -= 1
            time.sleep(_RADIO_RETRY_S)
            ok, detail = voice_create_and_start(dial)
    except Exception as err:
        ok, detail = False, str(err)
    if ok:
        return True, detail
    if modem_sim_missing():
        return False, EMERGENCY_NO_SIM
    return False, call_failure_detail(detail)
