"""Device info backends for Settings → About and Battery (lane sm_device).

All blocking I/O (subprocess, D-Bus call_sync, sysfs/proc walks) runs
here only on worker threads via the two chokepoints below; the settings
page calls them through _run_bg and renders the results with idle_add.
Pure parse/format helpers stay directly testable.
"""
from __future__ import annotations

import os
import platform
import subprocess

_DT_MODEL_DEFAULT = '/sys/firmware/devicetree/base/model'
_DMI_DIR_DEFAULT = '/sys/devices/virtual/dmi/id'


def _run(argv: list[str], timeout: int = 10):
    """Sole subprocess chokepoint; callers run on worker threads only."""
    return subprocess.run(argv, capture_output=True, text=True,
                          timeout=timeout, check=False)


def dbus_call_sync(bus, name: str, path: str, iface: str, method: str,
                   params=None, reply_type=None, timeout_ms: int = 3000):
    """Sole D-Bus sync chokepoint; callers run on worker threads only."""
    from gi.repository import Gio
    return bus.call_sync(
        name, path, iface, method, params, reply_type,
        Gio.DBusCallFlags.NONE, timeout_ms, None,
    )


def _read_text(path: str) -> str | None:
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            return handle.read().strip().strip('\x00').strip()
    except OSError:
        return None


def read_device_name(dt_model: str = _DT_MODEL_DEFAULT,
                     dmi_dir: str = _DMI_DIR_DEFAULT) -> str:
    """ARM devicetree model first, then DMI, then platform fallback."""
    model = _read_text(dt_model)
    if model:
        return model
    product = _read_text(os.path.join(dmi_dir, 'product_name'))
    if product:
        return product
    vendor = _read_text(os.path.join(dmi_dir, 'sys_vendor')) or ''
    version = _read_text(os.path.join(dmi_dir, 'product_version')) or ''
    combined = ('%s %s' % (vendor, version)).strip()
    if combined:
        return combined
    return '%s %s' % (platform.system(), platform.release())


def parse_os_release(text: str) -> str:
    """PRETTY_NAME from os-release text, 'Unknown' when absent."""
    for line in (text or '').splitlines():
        if line.startswith('PRETTY_NAME='):
            return line.split('=', 1)[1].strip().strip('"').strip("'")
    return 'Unknown'


def os_summary(path: str = '/etc/os-release') -> str:
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            return parse_os_release(handle.read())
    except OSError:
        return 'Unknown'


def format_cpu(cores: int | None, arch: str) -> str:
    """'8 cores (aarch64)' from a core count and platform.machine()."""
    try:
        count = int(cores) if cores else 0
    except (TypeError, ValueError):
        count = 0
    return '%d core%s (%s)' % (count, '' if count == 1 else 's', arch or '?')


def cpu_summary() -> str:
    return format_cpu(os.cpu_count(), platform.machine())


def format_uptime(seconds: float) -> str:
    """'3d 2h', '1h 01m' or '5m' from seconds since boot."""
    try:
        total = max(0, int(seconds))
    except (TypeError, ValueError):
        return '?'
    minutes, _secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return '%dd %dh' % (days, hours)
    if hours:
        return '%dh %02dm' % (hours, minutes)
    return '%dm' % minutes


def read_uptime(path: str = '/proc/uptime') -> float | None:
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return float(handle.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _parse_kb(meminfo: str, key: str) -> int:
    for line in (meminfo or '').splitlines():
        if line.startswith(key):
            try:
                return int(line.split()[1])
            except (ValueError, IndexError):
                return 0
    return 0


def _format_gb(kb: int) -> str:
    return '%.1f GB' % (kb / 1048576.0)


def format_mem(meminfo: str, swaps: str, zram_size: int | None) -> str:
    """'RAM 3.8 GB · zram 2.0 GB' from /proc/meminfo, /proc/swaps text and
    the zram disksize in bytes (None when no zram device)."""
    total = _parse_kb(meminfo, 'MemTotal:')
    parts = ['RAM %s' % _format_gb(total)] if total else ['RAM ?']
    swap_total = _parse_kb(meminfo, 'SwapTotal:')
    has_zram = any(line.split()[0].startswith('/dev/zram')
                   for line in (swaps or '').splitlines()[1:] if line.split())
    if has_zram and zram_size:
        parts.append('zram %s' % _format_gb(zram_size // 1024))
    elif swap_total:
        parts.append('swap %s' % _format_gb(swap_total))
    return ' · '.join(parts)


def mem_summary(meminfo_path: str = '/proc/meminfo',
                swaps_path: str = '/proc/swaps',
                zram_path: str = '/sys/block/zram0/disksize') -> str:
    try:
        with open(meminfo_path, 'r', encoding='utf-8',
                  errors='replace') as handle:
            meminfo = handle.read()
    except OSError:
        meminfo = ''
    try:
        with open(swaps_path, 'r', encoding='utf-8',
                  errors='replace') as handle:
            swaps = handle.read()
    except OSError:
        swaps = ''
    try:
        with open(zram_path, 'r', encoding='utf-8') as handle:
            zram_size = int(handle.read().strip())
    except (OSError, ValueError):
        zram_size = None
    return format_mem(meminfo, swaps, zram_size)


def read_thermal_zones(root: str = '/sys/class/thermal') -> list[dict]:
    """[{type, temp_milli_c}] for every readable thermal zone."""
    zones = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return zones
    for name in names:
        if not name.startswith('thermal_zone'):
            continue
        zone_dir = os.path.join(root, name)
        kind = _read_text(os.path.join(zone_dir, 'type')) or name
        try:
            with open(os.path.join(zone_dir, 'temp'), 'r',
                      encoding='utf-8') as handle:
                temp = int(handle.read().strip())
        except (OSError, ValueError):
            continue
        zones.append({'type': kind, 'temp': temp})
    return zones


def group_thermal(zones: list[dict]) -> dict[str, str]:
    """Hottest reading per zone type, formatted in °C."""
    hottest: dict[str, int] = {}
    for zone in zones:
        kind = str(zone.get('type', '?'))
        try:
            temp = int(zone.get('temp', 0))
        except (TypeError, ValueError):
            continue
        if temp > hottest.get(kind, -1):
            hottest[kind] = temp
    return {kind: '%.1f°C' % (temp / 1000.0)
            for kind, temp in sorted(hottest.items())}


def parse_ip_brief(output: str) -> dict[str, list[str]]:
    """Interface → addresses from `ip -brief addr show` (loopback out)."""
    addrs: dict[str, list[str]] = {}
    for line in (output or '').splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        iface = fields[0]
        if iface == 'lo':
            continue
        ips = [field.split('/')[0] for field in fields[2:]
               if '/' in field]
        if ips:
            addrs[iface] = ips
    return addrs


def read_iface_macs(root: str = '/sys/class/net') -> dict[str, str]:
    """Interface → MAC, skipping the all-zero loopback address."""
    macs = {}
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return macs
    for name in names:
        mac = _read_text(os.path.join(root, name, 'address')) or ''
        if mac and mac != '00:00:00:00:00:00':
            macs[name] = mac
    return macs


def net_summary() -> tuple[dict[str, list[str]], dict[str, str]]:
    """(ips, macs): empty dicts when the stack is unavailable. Worker only."""
    try:
        result = _run(['ip', '-brief', 'addr', 'show'], timeout=10)
        ips = parse_ip_brief(result.stdout) if result.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired):
        ips = {}
    return ips, read_iface_macs()


def _modem_props_sync() -> dict | None:
    """First ModemManager modem's properties, None when none. Worker only."""
    from gi.repository import GLib
    try:
        from gi.repository import Gio
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        objects = dbus_call_sync(
            bus, 'org.freedesktop.ModemManager1',
            '/org/freedesktop/ModemManager1',
            'org.freedesktop.DBus.ObjectManager',
            'GetManagedObjects', None,
            GLib.VariantType('(a{oa{sa{sv}}})'), 4000,
        ).unpack()[0]
    except Exception:
        return None
    for _path, ifaces in (objects or {}).items():
        props = ifaces.get('org.freedesktop.ModemManager1.Modem')
        if props:
            return dict(props)
    return None


def modem_summary() -> dict | None:
    """Worker entry: modem properties with the IMEI kept in the dict (the
    UI reveals it only behind a tap and it is never logged)."""
    return _modem_props_sync()


def battery_health_sync() -> dict | None:
    """UPower battery details for the health rows. None when no battery.
    Worker only."""
    from gi.repository import GLib
    try:
        from gi.repository import Gio
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        paths = dbus_call_sync(
            bus, 'org.freedesktop.UPower', '/org/freedesktop/UPower',
            'org.freedesktop.UPower', 'EnumerateDevices', None,
            GLib.VariantType('(ao)'), 3000,
        ).unpack()[0]
        for path in paths or []:
            props = dbus_call_sync(
                bus, 'org.freedesktop.UPower', path,
                'org.freedesktop.DBus.Properties', 'GetAll',
                GLib.Variant('(s)', ('org.freedesktop.UPower.Device',)),
                GLib.VariantType('(a{sv})'), 3000,
            ).unpack()[0]
            if int(props.get('Type', 0)) == 2:
                return dict(props)
    except Exception:
        return None
    return None


def format_battery_health(props: dict | None) -> dict[str, str]:
    """Health/cycles/temperature/voltage strings; 'Unknown' when missing."""
    out = {'health': 'Unknown', 'cycles': 'Unknown',
           'temperature': 'Unknown', 'voltage': 'Unknown'}
    if not props:
        return out
    try:
        full = float(props['EnergyFull'])
        design = float(props['EnergyFullDesign'])
        if design > 0:
            out['health'] = '%d%%' % round(100.0 * full / design)
    except (KeyError, TypeError, ValueError):
        pass
    cycles = props.get('ChargeCycles')
    try:
        out['cycles'] = '%d' % int(cycles) if cycles is not None else 'Unknown'
    except (TypeError, ValueError):
        pass
    temp = props.get('Temperature')
    try:
        # UPower reports Celsius, and 0 when the battery has no sensor.
        if float(temp) != 0.0:
            out['temperature'] = '%.1f°C' % float(temp)
    except (TypeError, ValueError):
        pass
    voltage = props.get('Voltage')
    try:
        out['voltage'] = '%.1f V' % float(voltage)
    except (TypeError, ValueError):
        pass
    return out
