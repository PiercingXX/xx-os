"""
System settings backends — the config file can't own these (design.md
"Settings scope"): WiFi networks, Bluetooth pairing, sound output devices,
battery status. Everything degrades to "unavailable" on machines without the
relevant stack; nothing here may take the shell down.

WiFi goes through nmcli (NetworkManager's supported CLI — far less code than
raw D-Bus AP enumeration for identical results); Bluetooth through BlueZ
D-Bus; sound through pactl; battery through UPower D-Bus.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Callable

_RunFn = Callable[..., subprocess.CompletedProcess]


def _run(cmd: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


# ------------------------------------------------------------------ WiFi

@dataclass(frozen=True)
class WifiNetwork:
    ssid: str
    signal: int
    security: str
    in_use: bool


def parse_nmcli_wifi(output: str) -> list[WifiNetwork]:
    """Parse `nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY dev wifi` terse output.
    Terse mode escapes ':' inside values as '\\:'."""
    import re
    networks: dict[str, WifiNetwork] = {}
    for line in output.splitlines():
        parts = [p.replace('\\:', ':') for p in re.split(r'(?<!\\):', line)]
        if len(parts) < 4:
            continue
        in_use = parts[0].strip() == '*'
        ssid = parts[1].strip()
        if not ssid:
            continue
        try:
            signal = int(parts[2])
        except ValueError:
            signal = 0
        security = parts[3].strip() or 'open'
        existing = networks.get(ssid)
        if existing is None or signal > existing.signal or in_use:
            networks[ssid] = WifiNetwork(ssid, signal, security, in_use or
                                         (existing.in_use if existing else False))
    return sorted(networks.values(), key=lambda n: (not n.in_use, -n.signal))


def list_wifi(run: _RunFn = _run) -> list[WifiNetwork] | None:
    """None → WiFi stack unavailable; [] → no networks found."""
    try:
        result = run(['nmcli', '-t', '-f', 'IN-USE,SSID,SIGNAL,SECURITY',
                      'dev', 'wifi', 'list'], timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return parse_nmcli_wifi(result.stdout)


def nm_running(run: _RunFn = _run) -> bool:
    """True when NetworkManager reports RUNNING=running (ref wifi-manager)."""
    try:
        result = run(['nmcli', '-t', '-f', 'RUNNING', 'general'], timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == 'running'


def wifi_enabled(run: _RunFn = _run) -> bool | None:
    """WiFi radio state: True/False, None when it cannot be read."""
    try:
        result = run(['nmcli', '-t', '-f', 'WIFI', 'general'], timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    state = result.stdout.strip().lower()
    if state == 'enabled':
        return True
    if state == 'disabled':
        return False
    return None


def set_wifi_enabled(enabled: bool, run: _RunFn = _run) -> bool:
    """Switch the WiFi radio on or off through NetworkManager's polkit."""
    try:
        result = run(['nmcli', 'radio', 'wifi',
                      'on' if enabled else 'off'], timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _wifi_result(ssid: str, result: subprocess.CompletedProcess) -> tuple[bool, str]:
    if result.returncode == 0:
        return True, f'Connected to {ssid}'
    detail = (result.stderr or result.stdout or '').strip().splitlines()
    return False, detail[-1] if detail else 'Connection failed'


def disconnect_wifi(ssid: str, run: _RunFn = _run) -> tuple[bool, str]:
    """Take the active `ssid` profile down (NetworkManager polkit)."""
    try:
        result = run(['nmcli', 'connection', 'down', ssid], timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return False, 'NetworkManager unavailable'
    if result.returncode == 0:
        return True, f'Disconnected from {ssid}'
    detail = (result.stderr or result.stdout or '').strip().splitlines()
    return False, detail[-1] if detail else 'Disconnect failed'


def forget_wifi(ssid: str, run: _RunFn = _run) -> tuple[bool, str]:
    """Delete the saved `ssid` profile (NetworkManager polkit)."""
    try:
        result = run(['nmcli', 'connection', 'delete', ssid], timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False, 'NetworkManager unavailable'
    if result.returncode == 0:
        return True, f'Forgot {ssid}'
    detail = (result.stderr or result.stdout or '').strip().splitlines()
    return False, detail[-1] if detail else 'Forget failed'


def wifi_details(ssid: str, run: _RunFn = _run) -> dict | None:
    """IP, gateway, DNS and MAC of the active `ssid` connection.

    None when the profile is not active or NetworkManager can't be asked.
    Terse `device show` lines split on the FIRST colon only: MACs and
    IPv6 addresses hold colons of their own."""
    try:
        active = run(['nmcli', '-t', '-f', 'NAME,TYPE,DEVICE', 'connection',
                      'show', '--active'], timeout=10)
        if active.returncode != 0:
            return None
        import re
        device = None
        for line in active.stdout.splitlines():
            # NAME may hold escaped colons; TYPE and DEVICE never do.
            parts = [p.replace('\\:', ':')
                     for p in re.split(r'(?<!\\):', line)]
            if len(parts) < 3:
                continue
            name = ':'.join(parts[:-2]).strip()
            kind, dev = parts[-2].strip(), parts[-1].strip()
            if (name == ssid and kind in ('wifi', '802-11-wireless')
                    and dev):
                device = dev
                break
        if device is None:
            return None
        shown = run(['nmcli', '-t', 'device', 'show', device], timeout=10)
        if shown.returncode != 0:
            return None
    except (OSError, subprocess.TimeoutExpired):
        return None
    fields: dict[str, list[str]] = {}
    for line in shown.stdout.splitlines():
        key, sep, value = line.partition(':')
        if not sep:
            continue
        fields.setdefault(key.strip(), []).append(value.strip())
    addresses = [addr.split('/')[0]
                 for addr in fields.get('IP4.ADDRESS[1]', []) if addr]
    dns = [server for index in ('IP4.DNS[1]', 'IP4.DNS[2]')
           for server in fields.get(index, []) if server]
    macs = fields.get('GENERAL.HWADDR', [])
    gateways = fields.get('IP4.GATEWAY', [])
    return {
        'ip': addresses[0] if addresses else '',
        'gateway': gateways[0] if gateways else '',
        'dns': ', '.join(dns),
        'mac': macs[0] if macs else '',
    }


def wifi_details_text(ssid: str, details: dict | None) -> str:
    """One multiline summary for the Details menu; never raises."""
    if not details:
        return f'{ssid}\nDetails unavailable.'
    def _line(label, value):
        return f'{label} {value or "unknown"}'
    return '\n'.join([
        ssid,
        _line('IP', details.get('ip')),
        _line('Gateway', details.get('gateway')),
        _line('DNS', details.get('dns')),
        _line('MAC', details.get('mac')),
    ])


def _key_mgmt_for(security: str | None) -> str:
    """nmcli wifi-sec.key-mgmt for a scan's SECURITY field. SAE only when
    the scan reports WPA3 and nothing older — mixed APs still take wpa-psk,
    which negotiates whatever the AP offers."""
    upper = (security or '').upper()
    if 'WPA3' in upper and 'WPA2' not in upper and 'WPA1' not in upper:
        return 'sae'
    return 'wpa-psk'


def saved_wifi_ssids(run: _RunFn = _run) -> list[str] | None:
    """SSIDs with a saved NetworkManager profile (the Smart Lock picker).

    None → NetworkManager unavailable; [] → no saved Wi-Fi networks."""
    import re
    try:
        result = run(['nmcli', '-t', '-f', 'NAME,TYPE', 'connection',
                      'show'], timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    ssids: set[str] = set()
    for line in result.stdout.splitlines():
        # Terse mode escapes ':' inside names as '\:'; TYPE never holds
        # one, so every part but the last belongs to the name.
        parts = [p.replace('\\:', ':') for p in re.split(r'(?<!\\):', line)]
        if len(parts) < 2:
            continue
        name = ':'.join(parts[:-1]).strip()
        if not name:
            continue
        if parts[-1].strip().lower() in ('wifi', '802-11-wireless'):
            ssids.add(name)
    return sorted(ssids, key=str.casefold)


def _existing_profiles(run: _RunFn) -> set[str] | None:
    """Names of known NM profiles, or None when NM can't be asked."""
    try:
        result = run(['nmcli', '-t', '-f', 'NAME', 'connection', 'show'],
                     timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return {line.replace('\\:', ':').strip()
            for line in result.stdout.splitlines() if line.strip()}


def _is_secrets_error(result: subprocess.CompletedProcess) -> bool:
    text = ((result.stderr or '') + '\n' + (result.stdout or '')).lower()
    return 'secret' in text or 'wrong password' in text or 'bad psk' in text


def connect_wifi(ssid: str, password: str | None = None,
                 run: _RunFn = _run,
                 security: str | None = None,
                 hidden: bool = False) -> tuple[bool, str]:
    """Connect to `ssid`. Secrets go through nmcli's passwd-file channel
    (nmcli(1): `connection up ... [passwd-file file]`, lines of the form
    `802-11-wireless-security.psk:<password>`) so the PSK never appears in
    argv, where any local user could read it via /proc/<pid>/cmdline. The
    temp file is 0600 and removed on every path.

    A profile that already exists is brought `up` directly (with the new
    PSK via passwd-file when one is given) instead of stacking another
    duplicate. A new profile is created exactly once, with
    wifi-sec.key-mgmt set — without it NM ignores the passwd-file PSK and
    secured networks never connect. A profile created here is deleted
    again when activation fails on secrets; pre-existing profiles are
    never deleted."""
    if not password:
        cmd = ['nmcli', 'dev', 'wifi', 'connect', ssid]
        if hidden:
            cmd += ['hidden', 'yes']
        try:
            result = run(cmd, timeout=45)
        except (OSError, subprocess.TimeoutExpired):
            return False, 'NetworkManager unavailable'
        return _wifi_result(ssid, result)

    profiles = _existing_profiles(run)
    if profiles is None:
        return False, 'NetworkManager unavailable'
    created = False
    if ssid not in profiles:
        add_cmd = ['nmcli', 'connection', 'add', 'type', 'wifi',
                   'con-name', ssid, 'ssid', ssid,
                   'wifi-sec.key-mgmt', _key_mgmt_for(security)]
        if hidden:
            add_cmd += ['wifi.hidden', 'yes']
        try:
            add = run(add_cmd, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            return False, 'NetworkManager unavailable'
        if add.returncode == 0:
            created = True
        # A nonzero add usually means a racing connect created the profile
        # first ("profile exists") — only `up` matters, so carry on.

    fd, passwd_path = tempfile.mkstemp(prefix='xxwm-psk-')
    try:
        with os.fdopen(fd, 'w') as passwd_file:
            passwd_file.write(f'802-11-wireless-security.psk:{password}\n')
        try:
            result = run(['nmcli', 'connection', 'up', ssid,
                          'passwd-file', passwd_path], timeout=45)
        except (OSError, subprocess.TimeoutExpired):
            return False, 'NetworkManager unavailable'
        ok, message = _wifi_result(ssid, result)
        if not ok and created and _is_secrets_error(result):
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                run(['nmcli', 'connection', 'delete', ssid], timeout=15)
        return ok, message
    finally:
        with contextlib.suppress(OSError):
            os.unlink(passwd_path)


# ------------------------------------------------------------------ Sound

@dataclass(frozen=True)
class AudioSink:
    name: str
    description: str
    is_default: bool


# Descriptions the output list hides unless asked (ref
# audio-input-manager.sh PIERCINGXX_AUDIO_SKIP): S/PDIF "Digital Stereo
# (IEC958)" ports, plus virtual sinks that only loop audio back.
_OUTPUT_SKIP_RE = 'iec958'


def _is_virtual_sink(entry: dict) -> bool:
    props = entry.get('properties')
    return isinstance(props, dict) and props.get('node.virtual') is True


def parse_pactl_sinks(list_json: str, default_name: str,
                      hide_special: bool = True) -> list[AudioSink]:
    try:
        sinks = json.loads(list_json)
    except json.JSONDecodeError:
        return []
    out: list[AudioSink] = []
    for sink in sinks if isinstance(sinks, list) else []:
        if not isinstance(sink, dict):
            continue
        name = str(sink.get('name', ''))
        if not name:
            continue
        description = str(sink.get('description', name))
        if hide_special and (_OUTPUT_SKIP_RE in description.lower()
                             or _is_virtual_sink(sink)):
            continue
        out.append(AudioSink(
            name=name,
            description=description,
            is_default=(name == default_name),
        ))
    return out


def list_audio_sinks(run: _RunFn = _run) -> list[AudioSink] | None:
    try:
        listing = run(['pactl', '-f', 'json', 'list', 'sinks'], timeout=10)
        default = run(['pactl', 'get-default-sink'], timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if listing.returncode != 0:
        return None
    return parse_pactl_sinks(listing.stdout, (default.stdout or '').strip())


def set_default_sink(name: str, run: _RunFn = _run) -> bool:
    try:
        return run(['pactl', 'set-default-sink', name], timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


# -------------------------------------------------------------- Microphone

@dataclass(frozen=True)
class AudioSource:
    name: str
    description: str
    is_default: bool


def parse_pactl_sources(list_json: str,
                        default_name: str) -> list[AudioSource]:
    """PipeWire/Pulse sources minus monitor ports (`.monitor` names and
    `device.class=monitor`, ref audio-input-manager.sh)."""
    try:
        sources = json.loads(list_json)
    except json.JSONDecodeError:
        return []
    out: list[AudioSource] = []
    for source in sources if isinstance(sources, list) else []:
        if not isinstance(source, dict):
            continue
        name = str(source.get('name', ''))
        if not name or name.endswith('.monitor'):
            continue
        props = source.get('properties')
        if isinstance(props, dict) and props.get('device.class') == 'monitor':
            continue
        out.append(AudioSource(
            name=name,
            description=str(source.get('description', name)),
            is_default=(name == default_name),
        ))
    return out


def list_audio_sources(run: _RunFn = _run) -> list[AudioSource] | None:
    try:
        listing = run(['pactl', '-f', 'json', 'list', 'sources'], timeout=10)
        default = run(['pactl', 'get-default-source'], timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if listing.returncode != 0:
        return None
    return parse_pactl_sources(listing.stdout, (default.stdout or '').strip())


def set_default_source(name: str, run: _RunFn = _run) -> bool:
    try:
        return run(['pactl', 'set-default-source', name],
                   timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


# ------------------------------------------------------------------ Battery

_UPOWER_STATES = {1: 'charging', 2: 'discharging', 3: 'empty',
                  4: 'full', 5: 'pending charge', 6: 'pending discharge'}


def battery_status() -> str | None:
    """`87% — discharging, 3h 12m left` from UPower's display device."""
    try:
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        result = bus.call_sync(
            'org.freedesktop.UPower',
            '/org/freedesktop/UPower/devices/DisplayDevice',
            'org.freedesktop.DBus.Properties', 'GetAll',
            GLib.Variant('(s)', ('org.freedesktop.UPower.Device',)),
            GLib.VariantType('(a{sv})'),
            Gio.DBusCallFlags.NONE, 2000, None,
        ).unpack()[0]
    except Exception:
        return None
    if not result.get('IsPresent'):
        return None
    pct = int(result.get('Percentage', 0))
    state = _UPOWER_STATES.get(int(result.get('State', 0)), 'unknown')
    text = f'{pct}% — {state}'
    seconds = int(result.get('TimeToEmpty', 0) if state == 'discharging'
                  else result.get('TimeToFull', 0))
    if seconds > 0:
        hours, minutes = divmod(seconds // 60, 60)
        text += f', {hours}h {minutes:02d}m ' + (
            'left' if state == 'discharging' else 'to full')
    return text


# ------------------------------------------------------------------ Bluetooth

@dataclass(frozen=True)
class BtDevice:
    address: str
    name: str
    paired: bool
    connected: bool


def _bluez_bus():
    from gi.repository import Gio
    return Gio.bus_get_sync(Gio.BusType.SYSTEM, None)


def list_bt_devices() -> list[BtDevice] | None:
    """None → BlueZ unavailable. Known + discovered devices."""
    try:
        from gi.repository import Gio, GLib
        bus = _bluez_bus()
        objects = bus.call_sync(
            'org.bluez', '/', 'org.freedesktop.DBus.ObjectManager',
            'GetManagedObjects', None,
            GLib.VariantType('(a{oa{sa{sv}}})'),
            Gio.DBusCallFlags.NONE, 4000, None,
        ).unpack()[0]
    except Exception:
        return None
    devices = []
    for _path, ifaces in objects.items():
        props = ifaces.get('org.bluez.Device1')
        if not props:
            continue
        devices.append(BtDevice(
            address=str(props.get('Address', '')),
            name=str(props.get('Name') or props.get('Alias') or props.get('Address', '?')),
            paired=bool(props.get('Paired')),
            connected=bool(props.get('Connected')),
        ))
    return sorted(devices, key=lambda d: (not d.connected, not d.paired, d.name.casefold()))


_BLUEZ = 'org.bluez'
_ADAPTER_IFACE = 'org.bluez.Adapter1'
_DEVICE_IFACE = 'org.bluez.Device1'
_AGENT_IFACE = 'org.bluez.Agent1'
_AGENT_MGR = 'org.bluez.AgentManager1'
_AGENT_PATH = '/org/xxwm/Agent'
_AGENT_CAPABILITY = 'NoInputNoOutput'

_AGENT_XML = (
    '<node><interface name="org.bluez.Agent1">'
    '<method name="Release"/>'
    '<method name="RequestPinCode">'
    '<arg type="o" name="device" direction="in"/>'
    '<arg type="s" name="pincode" direction="out"/>'
    '</method>'
    '<method name="DisplayPinCode">'
    '<arg type="o" name="device" direction="in"/>'
    '<arg type="s" name="pincode" direction="in"/>'
    '</method>'
    '<method name="RequestPasskey">'
    '<arg type="o" name="device" direction="in"/>'
    '<arg type="u" name="passkey" direction="out"/>'
    '</method>'
    '<method name="DisplayPasskey">'
    '<arg type="o" name="device" direction="in"/>'
    '<arg type="u" name="passkey" direction="in"/>'
    '<arg type="q" name="entered" direction="in"/>'
    '</method>'
    '<method name="RequestConfirmation">'
    '<arg type="o" name="device" direction="in"/>'
    '<arg type="u" name="passkey" direction="in"/>'
    '</method>'
    '<method name="RequestAuthorization">'
    '<arg type="o" name="device" direction="in"/>'
    '</method>'
    '<method name="AuthorizeService">'
    '<arg type="o" name="device" direction="in"/>'
    '<arg type="s" name="uuid" direction="in"/>'
    '</method>'
    '<method name="Cancel"/>'
    '</interface></node>'
)


def _managed_objects(bus) -> dict:
    from gi.repository import Gio, GLib
    return bus.call_sync(
        _BLUEZ, '/', 'org.freedesktop.DBus.ObjectManager',
        'GetManagedObjects', None,
        GLib.VariantType('(a{oa{sa{sv}}})'),
        Gio.DBusCallFlags.NONE, 4000, None,
    ).unpack()[0]


def _adapter_path(bus=None) -> str | None:
    """First BlueZ adapter from the ObjectManager — never a hardcoded hci0."""
    try:
        bus = bus if bus is not None else _bluez_bus()
        for path, ifaces in _managed_objects(bus).items():
            if _ADAPTER_IFACE in ifaces:
                return str(path)
    except Exception:
        pass
    return None


def _device_path(bus, adapter: str, address: str) -> str:
    """Object path of a device: looked up by address, else the canonical
    BlueZ form under the resolved adapter."""
    try:
        for path, ifaces in _managed_objects(bus).items():
            props = ifaces.get(_DEVICE_IFACE)
            if props and str(props.get('Address', '')).upper() == address.upper():
                return str(path)
    except Exception:
        pass
    return adapter + '/dev_' + address.upper().replace(':', '_')


def _get_prop(bus, path: str, iface: str, prop: str):
    from gi.repository import Gio, GLib
    value = bus.call_sync(
        _BLUEZ, path, 'org.freedesktop.DBus.Properties', 'Get',
        GLib.Variant('(ss)', (iface, prop)),
        GLib.VariantType('(v)'),
        Gio.DBusCallFlags.NONE, 5000, None,
    ).unpack()[0]
    return value.unpack() if hasattr(value, 'unpack') else value


def _set_prop(bus, path: str, iface: str, prop: str,
              signature: str, value) -> None:
    from gi.repository import Gio, GLib
    bus.call_sync(
        _BLUEZ, path, 'org.freedesktop.DBus.Properties', 'Set',
        GLib.Variant('(ssv)', (iface, prop, GLib.Variant(signature, value))),
        None, Gio.DBusCallFlags.NONE, 5000, None,
    )


def _ensure_adapter_powered(bus, adapter: str) -> None:
    """Power the adapter on when it is off; best-effort, never raises."""
    try:
        if not _get_prop(bus, adapter, _ADAPTER_IFACE, 'Powered'):
            _set_prop(bus, adapter, _ADAPTER_IFACE, 'Powered', 'b', True)
    except Exception:
        pass


def _on_agent_method(_conn, _sender, _path, _iface, method: str,
                     _params, invocation) -> None:
    """NoInputNoOutput agent: confirm everything, display nothing."""
    from gi.repository import GLib
    try:
        if method == 'RequestPinCode':
            invocation.return_value(GLib.Variant('(s)', ('',)))
        elif method == 'RequestPasskey':
            invocation.return_value(GLib.Variant('(u)', (0,)))
        else:
            invocation.return_value(None)
    except Exception as error:
        with contextlib.suppress(Exception):
            invocation.return_error_literal(0, 'Rejected',
                                            getattr(error, 'message', None)
                                            or str(error))


def _register_agent(bus, capability: str = _AGENT_CAPABILITY):
    """Export the pairing agent and make it the default. Returns the
    registration id, or None when BlueZ refused (pairing still attempted)."""
    from gi.repository import Gio, GLib
    try:
        iface = Gio.DBusNodeInfo.new_for_xml(_AGENT_XML).interfaces[0]
        reg_id = bus.register_object(_AGENT_PATH, iface, _on_agent_method)
        bus.call_sync(_BLUEZ, '/org/bluez', _AGENT_MGR, 'RegisterAgent',
                      GLib.Variant('(os)', (_AGENT_PATH, capability)),
                      None, Gio.DBusCallFlags.NONE, 5000, None)
        bus.call_sync(_BLUEZ, '/org/bluez', _AGENT_MGR, 'RequestDefaultAgent',
                      GLib.Variant('(o)', (_AGENT_PATH,)),
                      None, Gio.DBusCallFlags.NONE, 5000, None)
        return reg_id
    except Exception:
        return None


def _unregister_agent(bus, reg_id) -> None:
    from gi.repository import Gio, GLib
    if reg_id is not None:
        with contextlib.suppress(Exception):
            bus.unregister_object(reg_id)
    with contextlib.suppress(Exception):
        bus.call_sync(_BLUEZ, '/org/bluez', _AGENT_MGR, 'UnregisterAgent',
                      GLib.Variant('(o)', (_AGENT_PATH,)),
                      None, Gio.DBusCallFlags.NONE, 5000, None)


def _start_bt_discovery_sync(seconds: int) -> bool:
    try:
        from gi.repository import Gio, GLib
        bus = _bluez_bus()
        adapter = _adapter_path(bus)
        if adapter is None:
            return False
        _ensure_adapter_powered(bus, adapter)
        bus.call_sync(_BLUEZ, adapter, _ADAPTER_IFACE,
                      'StartDiscovery', None, None,
                      Gio.DBusCallFlags.NONE, 5000, None)
        GLib.timeout_add_seconds(seconds, _stop_bt_discovery, adapter)
        return True
    except Exception:
        return False


def start_bt_discovery(seconds: int = 12, on_done=None) -> bool:
    """Kick off discovery off the GTK thread; results appear in the next
    list_bt_devices() call. `on_done(ok)` fires on the main loop."""
    import threading
    from gi.repository import GLib

    def _worker() -> None:
        ok = _start_bt_discovery_sync(seconds)

        def _deliver() -> bool:
            if on_done is not None:
                on_done(ok)
            return False
        GLib.idle_add(_deliver)

    threading.Thread(target=_worker, daemon=True).start()
    return True


def _stop_bt_discovery(adapter: str | None = None) -> bool:
    """StopDiscovery without blocking the timeout callback that fires it.

    The reply is never consumed (this runs one-shot from a GLib timeout),
    so the async call is a genuine off-loop send; failures stay silent,
    as when the sync version raised.
    """
    try:
        from gi.repository import Gio
        bus = _bluez_bus()
        target = adapter or _adapter_path(bus)
        if target is None:
            return False
        bus.call(_BLUEZ, target, _ADAPTER_IFACE,
                 'StopDiscovery', None, None,
                 Gio.DBusCallFlags.NONE, 5000, None, None)
    except Exception:
        pass
    return False


def pair_bt_device(address: str,
                   agent_capability: str = _AGENT_CAPABILITY,
                   ) -> tuple[bool, str]:
    """Pair, trust, then connect. The agent is registered only for the
    duration of the pairing. Device-gated for real verification."""
    try:
        bus = _bluez_bus()
    except Exception as error:
        return False, getattr(error, 'message', None) or str(error)
    adapter = _adapter_path(bus)
    if adapter is None:
        return False, 'No Bluetooth adapter found'
    _ensure_adapter_powered(bus, adapter)
    path = _device_path(bus, adapter, address)
    reg_id = _register_agent(bus, agent_capability)
    try:
        from gi.repository import Gio
        try:
            bus.call_sync(_BLUEZ, path, _DEVICE_IFACE, 'Pair',
                          None, None, Gio.DBusCallFlags.NONE, 30000, None)
        except Exception as error:
            message = getattr(error, 'message', None) or str(error)
            if 'AlreadyExists' in message:
                return connect_bt_device(address)
            return False, message
        try:
            _set_prop(bus, path, _DEVICE_IFACE, 'Trusted', 'b', True)
        except Exception:
            pass
        try:
            bus.call_sync(_BLUEZ, path, _DEVICE_IFACE, 'Connect',
                          None, None, Gio.DBusCallFlags.NONE, 30000, None)
        except Exception as error:
            return False, getattr(error, 'message', None) or str(error)
        return True, 'Paired and connected'
    finally:
        _unregister_agent(bus, reg_id)


def connect_bt_device(address: str) -> tuple[bool, str]:
    try:
        bus = _bluez_bus()
        adapter = _adapter_path(bus)
        if adapter is None:
            return False, 'No Bluetooth adapter found'
        path = _device_path(bus, adapter, address)
        from gi.repository import Gio
        flags = Gio.DBusCallFlags.NONE
        bus.call_sync(_BLUEZ, path, _DEVICE_IFACE, 'Connect', None, None,
                      flags, 30000, None)
        return True, 'Connected'
    except Exception as error:
        return False, getattr(error, 'message', None) or str(error)


def disconnect_bt_device(address: str) -> tuple[bool, str]:
    """Device1.Disconnect one paired device; never raises."""
    try:
        from gi.repository import Gio
        bus = _bluez_bus()
        adapter = _adapter_path(bus)
        if adapter is None:
            return False, 'No Bluetooth adapter found'
        path = _device_path(bus, adapter, address)
        bus.call_sync(_BLUEZ, path, _DEVICE_IFACE, 'Disconnect', None, None,
                      Gio.DBusCallFlags.NONE, 15000, None)
        return True, 'Disconnected'
    except Exception as error:
        return False, getattr(error, 'message', None) or str(error)


def forget_bt_device(address: str) -> tuple[bool, str]:
    """Adapter1.RemoveDevice: drop pairing (the UI confirms first)."""
    try:
        from gi.repository import Gio, GLib
        bus = _bluez_bus()
        adapter = _adapter_path(bus)
        if adapter is None:
            return False, 'No Bluetooth adapter found'
        path = _device_path(bus, adapter, address)
        bus.call_sync(_BLUEZ, adapter, _ADAPTER_IFACE, 'RemoveDevice',
                      GLib.Variant('(o)', (path,)), None,
                      Gio.DBusCallFlags.NONE, 15000, None)
        return True, 'Forgotten'
    except Exception as error:
        return False, getattr(error, 'message', None) or str(error)


def rename_bt_device(address: str, alias: str) -> tuple[bool, str]:
    """Set the device Alias (the name shown for it); never raises."""
    name = (alias or '').strip()
    if not name:
        return False, 'Name is empty'
    try:
        bus = _bluez_bus()
        adapter = _adapter_path(bus)
        if adapter is None:
            return False, 'No Bluetooth adapter found'
        path = _device_path(bus, adapter, address)
        _set_prop(bus, path, _DEVICE_IFACE, 'Alias', 's', name)
        return True, 'Renamed'
    except Exception as error:
        return False, getattr(error, 'message', None) or str(error)


def bt_powered(bus=None) -> bool | None:
    """Adapter1 Powered: True/False, None when BlueZ can't be asked."""
    try:
        own = bus if bus is not None else _bluez_bus()
        adapter = _adapter_path(own)
        if adapter is None:
            return None
        return bool(_get_prop(own, adapter, _ADAPTER_IFACE, 'Powered'))
    except Exception:
        return None


def set_bt_powered(enabled: bool, bus=None) -> bool:
    """Switch the adapter power through BlueZ (its own polkit)."""
    try:
        own = bus if bus is not None else _bluez_bus()
        adapter = _adapter_path(own)
        if adapter is None:
            return False
        _set_prop(own, adapter, _ADAPTER_IFACE, 'Powered', 'b',
                  bool(enabled))
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ rfkill

RFKILL_TYPE_WLAN = 1
RFKILL_TYPE_BLUETOOTH = 2
RFKILL_TYPES = {'wifi': RFKILL_TYPE_WLAN, 'bluetooth': RFKILL_TYPE_BLUETOOTH}
RFKILL_OP_CHANGE_ALL = 3


def rfkill_event_bytes(rfkill_type: int) -> bytes:
    """One `struct rfkill_event` unblocking every switch of `rfkill_type`:
    idx 0, op CHANGE_ALL, soft 0 (unblock). `<IBBBB` is u32 idx + u8
    type/op/soft/hard."""
    import struct
    return struct.pack('<IBBBB', 0, rfkill_type, RFKILL_OP_CHANGE_ALL, 0, 0)


def bt_soft_blocked(sysfs: str = '/sys') -> bool:
    """True when an rfkill switch of type bluetooth reports soft-blocked
    (ref bluetooth-manager.sh). Missing sysfs reads as not blocked."""
    base = os.path.join(sysfs, 'class', 'rfkill')
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return False
    for name in names:
        try:
            with open(os.path.join(base, name, 'type'),
                      encoding='utf-8') as handle:
                kind = handle.read().strip()
            if kind != 'bluetooth':
                continue
            with open(os.path.join(base, name, 'soft'),
                      encoding='utf-8') as handle:
                if handle.read().strip() == '1':
                    return True
        except OSError:
            continue
    return False


def rfkill_unblock(rtype: str, dev: str = '/dev/rfkill',
                   _access=os.access, _open=os.open,
                   _write=os.write, _close=os.close) -> bool:
    """Unblock `rtype` (wifi/bluetooth) through /dev/rfkill, which logind
    hands to the active session (uaccess) — no root needed when writable.
    False when the device is not writable: the caller falls back to the
    privileged helper's SetRfkill. Never raises."""
    rfkill_type = RFKILL_TYPES.get(rtype)
    if rfkill_type is None:
        return False
    try:
        if not _access(dev, os.W_OK):
            return False
        fd = _open(dev, os.O_WRONLY | os.O_CLOEXEC)
    except OSError:
        return False
    try:
        _write(fd, rfkill_event_bytes(rfkill_type))
        return True
    except OSError:
        return False
    finally:
        with contextlib.suppress(OSError):
            _close(fd)


# ----------------------------------------------------------------- Services

_SYSTEMD_BUS = 'org.freedesktop.systemd1'
_SYSTEMD_MANAGER_PATH = '/org/freedesktop/systemd1'
_SYSTEMD_MANAGER_IFACE = 'org.freedesktop.systemd1.Manager'
_SYSTEMD_UNIT_IFACE = 'org.freedesktop.systemd1.Unit'


def detect_init(root: str = '/') -> str:
    """Running init: 'systemd' when its runtime dir exists (never infer
    from the package manager — pmOS ships apk with OpenRC or systemd),
    'openrc' when openrc-run is installed, else 'unknown'."""
    if os.path.isdir(os.path.join(root, 'run', 'systemd', 'system')):
        return 'systemd'
    for candidate in ('sbin/openrc-run', 'bin/openrc-run',
                      'usr/bin/openrc-run', 'usr/sbin/openrc-run'):
        if os.path.isfile(os.path.join(root, candidate)):
            return 'openrc'
    return 'unknown'


def _systemd_bus():
    from gi.repository import Gio
    return Gio.bus_get_sync(Gio.BusType.SYSTEM, None)


def _systemd_unpack(reply):
    value = reply.unpack()[0]
    return value.unpack() if hasattr(value, 'unpack') else value


def systemd_unit_state(unit: str, bus=None) -> tuple | None:
    """(ActiveState, UnitFileState) for `unit`; None when unknown.

    Unprivileged read on the system bus. `bus` is injectable for tests."""
    try:
        from gi.repository import Gio, GLib
        own = bus if bus is not None else _systemd_bus()
        path = own.call_sync(
            _SYSTEMD_BUS, _SYSTEMD_MANAGER_PATH, _SYSTEMD_MANAGER_IFACE,
            'GetUnit', GLib.Variant('(s)', (unit,)),
            GLib.VariantType('(o)'),
            Gio.DBusCallFlags.NONE, 5000, None,
        ).unpack()[0]
        active = _systemd_unpack(own.call_sync(
            _SYSTEMD_BUS, path, 'org.freedesktop.DBus.Properties', 'Get',
            GLib.Variant('(ss)', (_SYSTEMD_UNIT_IFACE, 'ActiveState')),
            GLib.VariantType('(v)'),
            Gio.DBusCallFlags.NONE, 5000, None,
        ))
        enabled = own.call_sync(
            _SYSTEMD_BUS, _SYSTEMD_MANAGER_PATH, _SYSTEMD_MANAGER_IFACE,
            'GetUnitFileState', GLib.Variant('(s)', (unit,)),
            GLib.VariantType('(s)'),
            Gio.DBusCallFlags.NONE, 5000, None,
        ).unpack()[0]
        return (str(active), str(enabled))
    except Exception:
        return None


def openrc_service_state(svc: str,
                         run: _RunFn = _run) -> tuple[bool | None,
                                                      bool | None]:
    """(running, enabled-in-default) for an OpenRC service.

    Unprivileged: `rc-service status` exits 0 while running;
    `rc-update show` lists `name | runlevels`. (None, None) when OpenRC
    can't be asked."""
    try:
        status = run(['rc-service', svc, 'status'], timeout=10)
        shown = run(['rc-update', 'show'], timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return (None, None)
    if shown.returncode != 0:
        return (None, None)
    enabled = False
    for line in shown.stdout.splitlines():
        name, sep, levels = line.partition('|')
        if sep and name.strip() == svc:
            enabled = 'default' in levels.split()
            break
    return (status.returncode == 0, enabled)


def list_failed_units(init: str, run: _RunFn = _run,
                      bus=None) -> list[str]:
    """Read-only failed/crashed units for the Failed list. Never raises."""
    try:
        if init == 'systemd':
            result = run(['systemctl', '--failed', '--no-pager',
                          '--no-legend'], timeout=15)
            if result.returncode != 0:
                return []
            return [line.split()[0] for line in result.stdout.splitlines()
                    if line.split()]
        if init == 'openrc':
            result = run(['rc-status', 'default'], timeout=15)
            if result.returncode != 0:
                return []
            return [line.split()[0] for line in result.stdout.splitlines()
                    if 'crashed' in line and line.split()]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return []


# ------------------------------------------------- software updates (sm_auth)
#
# Distro package and Flatpak backends for the Settings System section.
# Every command routes through the module `_run` chokepoint and runs on
# settings-page worker threads, never the GTK loop. Helpers take an
# injectable `run` so tests never execute the real package managers.

def detect_backend(which=None) -> str | None:
    """'apk' (postmarketOS) first, then 'pacman' (Arch), then 'apt'
    where trivial; None when no supported manager is installed."""
    which_fn = which or shutil.which
    for binary in ('apk', 'pacman', 'apt'):
        if which_fn(binary):
            return binary
    return None


def power_for_update(sysfs: str = '/sys') -> tuple[bool, str]:
    """Update power guard: AC mains online, a Charging/Full battery, or
    at least 30% charge. Reads sysfs directly (no D-Bus round trip).

    Returns (ok, reason): a device with no readable battery is on mains
    power by definition, so unknown allows with a note instead of
    stranding desktop and VM installs behind a sensor they lack.
    """
    base = os.path.join(sysfs, 'class', 'power_supply')
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return True, 'No battery detected.'
    if not names:
        return True, 'No battery detected.'
    for name in names:
        try:
            with open(os.path.join(base, name, 'type'),
                      encoding='utf-8') as handle:
                kind = handle.read().strip()
        except OSError:
            continue
        if kind != 'Mains':
            continue
        try:
            with open(os.path.join(base, name, 'online'),
                      encoding='utf-8') as handle:
                if handle.read().strip() == '1':
                    return True, 'AC power connected.'
        except OSError:
            continue
    best = -1
    status = ''
    for name in names:
        try:
            with open(os.path.join(base, name, 'type'),
                      encoding='utf-8') as handle:
                if handle.read().strip() != 'Battery':
                    continue
            with open(os.path.join(base, name, 'capacity'),
                      encoding='utf-8') as handle:
                pct = int(handle.read().strip())
            try:
                with open(os.path.join(base, name, 'status'),
                          encoding='utf-8') as handle:
                    status = handle.read().strip()
            except OSError:
                status = ''
        except (OSError, ValueError):
            continue
        best = max(best, pct)
        if status in ('Charging', 'Full'):
            return True, 'Battery %s (%d%%).' % (status.lower(), pct)
    if best < 0:
        return True, 'No battery detected.'
    if best >= 30:
        return True, 'Battery at %d%%.' % best
    return False, 'Battery at %d%% — connect AC power or charge to 30%%.' % best


def parse_flatpak_updates(output: str) -> int:
    """Count `flatpak remote-ls --updates` lines (one ref per line)."""
    return sum(1 for line in (output or '').splitlines() if line.strip())


def flatpak_updates_available(run: _RunFn = _run) -> int | None:
    """Unprivileged Flatpak update count; None when Flatpak is missing
    or the listing failed."""
    try:
        result = run(['flatpak', 'remote-ls', '--updates'], timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return parse_flatpak_updates(result.stdout)


def flatpak_update_all(run: _RunFn = _run) -> tuple[bool, str]:
    """`flatpak update -y` for user installs, then system ones (system
    auth goes through Flatpak's own polkit prompt, served by the
    in-shell agent). Returns (ok, combined output tail)."""
    outputs = []
    for scope in ('--user', '--system'):
        try:
            result = run(['flatpak', 'update', scope, '-y',
                          '--noninteractive'], timeout=600)
        except (OSError, subprocess.TimeoutExpired) as error:
            return False, 'Flatpak update (%s) failed: %s' % (scope, error)
        outputs.append(result.stdout or '')
        if result.returncode != 0:
            detail = ((result.stderr or result.stdout or '').strip()
                      .splitlines())
            return False, 'Flatpak update (%s) failed: %s' % (
                scope, detail[-1] if detail else 'exit %d' % result.returncode)
    return True, ''.join(outputs)[-4000:]


def parse_apk_who_owns(output: str) -> tuple[str | None, str]:
    """Owner package from `apk info --who-owns <path>` output: the path
    `is owned by <pkg>-<version>`, with the version suffix stripped.
    (None, 'apk') when nothing owns the path."""
    import re
    match = re.search(r'is owned by (\S+)\s*$', output or '',
                      re.MULTILINE)
    if not match:
        return None, 'apk'
    owned = match.group(1)
    versioned = re.match(r'^(.*)-[0-9][^-]*-r[0-9]+$', owned)
    return (versioned.group(1) if versioned else owned), 'apk'


def resolve_apk_owner(desktop_path: str,
                      run: _RunFn = _run) -> tuple[str | None, str]:
    """(owner, 'apk') for a .desktop path; unowned paths give (None)."""
    try:
        result = run(['apk', 'info', '--who-owns', desktop_path], timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None, 'apk'
    if result.returncode != 0:
        return None, 'apk'
    return parse_apk_who_owns(result.stdout)


def resolve_pacman_owner(desktop_path: str,
                         run: _RunFn = _run) -> tuple[str | None, str]:
    """(owner, 'pacman') via `pacman -Qqo`; unowned paths give (None)."""
    try:
        result = run(['pacman', '-Qqo', desktop_path], timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None, 'pacman'
    if result.returncode != 0:
        return None, 'pacman'
    name = (result.stdout or '').strip().splitlines()
    if not name or not name[0].strip():
        return None, 'pacman'
    return name[0].strip(), 'pacman'


def resolve_dpkg_owner(desktop_path: str,
                       run: _RunFn = _run) -> tuple[str | None, str]:
    """(owner, 'apt') via `dpkg -S`; unowned paths give (None)."""
    try:
        result = run(['dpkg', '-S', desktop_path], timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None, 'apt'
    if result.returncode != 0:
        return None, 'apt'
    for line in (result.stdout or '').splitlines():
        if not line.strip():
            continue
        name = line.split(':')[0].split(',')[0].strip()
        if name:
            return name, 'apt'
    return None, 'apt'


def parse_flatpak_app_id(desktop_text: str) -> str | None:
    """The `X-Flatpak` app id from a .desktop file, if it has one."""
    for line in (desktop_text or '').splitlines():
        if line.startswith('X-Flatpak='):
            app_id = line.split('=', 1)[1].strip()
            if app_id:
                return app_id
    return None


def removal_preview(pkg: str, backend: str,
                    run: _RunFn = _run) -> str:
    """What the manager would remove: `apk del -s` / `pacman -Rns
    --print`, shown verbatim in the confirm dialog."""
    argv = (['apk', 'del', '-s', pkg] if backend == 'apk'
            else ['pacman', '-Rns', '--print', pkg])
    try:
        result = run(argv, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        return 'Preview unavailable: %s' % (error,)
    output = ((result.stdout or '') + (result.stderr or '')).strip()
    if not output:
        return 'No preview output.'
    lines = output.splitlines()
    if len(lines) > 25:
        lines = lines[:25] + ['… (%d more lines)' % (len(lines) - 25)]
    return '\n'.join(lines)


def flatpak_installation(app_id: str, run: _RunFn = _run) -> str | None:
    """'user' when the app is a user install, 'system' when system,
    else None. User installs need no root to remove."""
    for scope in ('--user', '--system'):
        try:
            result = run(['flatpak', 'list', '--app', scope,
                          '--columns=application'], timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            continue
        if any(line.strip() == app_id
               for line in (result.stdout or '').splitlines()):
            return scope[2:]
    return None


def flatpak_uninstall(app_id: str, installation: str,
                      run: _RunFn = _run) -> tuple[bool, str]:
    """`flatpak uninstall` for one app id; system installs auth through
    Flatpak's own polkit prompt. Returns (ok, message)."""
    if installation not in ('user', 'system'):
        return False, 'Unknown Flatpak installation.'
    try:
        result = run(['flatpak', 'uninstall', '--%s' % installation,
                      '-y', app_id], timeout=300)
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, 'Flatpak uninstall failed: %s' % (error,)
    if result.returncode != 0:
        detail = ((result.stderr or result.stdout or '').strip()
                  .splitlines())
        return False, detail[-1] if detail else 'Flatpak uninstall failed.'
    return True, 'Uninstalled %s.' % (app_id,)
