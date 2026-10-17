from __future__ import annotations

import subprocess
import threading
from pathlib import Path

_SUPPLY_ROOT = Path('/sys/class/power_supply')


def _supplies(root: Path = _SUPPLY_ROOT) -> list[Path]:
    try:
        return sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return []


def _read_supply(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except (OSError, ValueError):
        return None


def _battery_supply(root: Path = _SUPPLY_ROOT) -> Path | None:
    """The main battery supply: type Battery whose scope is not Device.

    The first glob match is often a peripheral (headset, stylus) battery, so
    prefer a system battery and only fall back to any supply with capacity.
    A missing scope file counts as eligible — not every kernel exposes it.
    """
    first_capacity: Path | None = None
    for supply in _supplies(root):
        capacity = supply / 'capacity'
        try:
            int(capacity.read_text().strip())
        except (OSError, ValueError):
            continue
        if first_capacity is None:
            first_capacity = supply
        supply_type = _read_supply(supply / 'type')
        if supply_type != 'Battery':
            continue
        scope = _read_supply(supply / 'scope')
        if scope is not None and scope == 'Device':
            continue
        return supply
    return first_capacity


def battery_pct(root: Path = _SUPPLY_ROOT) -> int | None:
    """Read battery level from sysfs. Returns 0–100 or None if unavailable."""
    supply = _battery_supply(root)
    if supply is None:
        return None
    try:
        return int((supply / 'capacity').read_text().strip())
    except (OSError, ValueError):
        return None


def battery_charging(root: Path = _SUPPLY_ROOT) -> bool:
    """True if any non-peripheral power supply reports Charging or Full."""
    candidates = [s for s in _supplies(root)
                 if _read_supply(s / 'scope') != 'Device']
    for supply in candidates or _supplies(root):
        status = _read_supply(supply / 'status')
        if status in ('Charging', 'Full'):
            return True
    return False


def battery_label() -> str:
    pct = battery_pct()
    if pct is None:
        return ''
    charging = battery_charging()
    marker = '+' if charging else ''
    return f'{pct}%{marker}'


def wifi_ssid() -> str | None:
    """Return connected WiFi SSID, or None if not connected.

    Never rescans: `dev wifi list` rescans when its cache is older than 30 s
    and then blocks up to the 2 s timeout on the calling thread, which used
    to freeze the home screen every minute.
    """
    try:
        out = subprocess.check_output(
            ['nmcli', '-t', '-f', 'ACTIVE,SSID', 'dev', 'wifi',
             'list', '--rescan', 'no'],
            text=True, timeout=5,
        )
        for line in out.splitlines():
            if line.startswith('yes:'):
                return line[4:] or None
    except (FileNotFoundError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired):
        pass
    return None


_MM1 = 'org.freedesktop.ModemManager1'
_MODEM_IFACE = 'org.freedesktop.ModemManager1.Modem'


def modem_paths() -> list[str]:
    """D-Bus object paths of every ModemManager modem, or []."""
    try:
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        objects = bus.call_sync(
            _MM1, '/org/freedesktop/ModemManager1',
            'org.freedesktop.DBus.ObjectManager', 'GetManagedObjects',
            None, GLib.VariantType('(a{oa{sa{sv}}})'),
            Gio.DBusCallFlags.NONE, 4000, None,
        ).unpack()[0]
    except Exception:
        return []
    return [path for path, ifaces in objects.items()
            if _MODEM_IFACE in ifaces]


def mobile_signal() -> str | None:
    """Return mobile signal strength label (bars) or None if no modem."""
    modems = modem_paths()
    if not modems:
        return None
    try:
        out = subprocess.check_output(
            ['mmcli', '-m', modems[0], '--output-keyvalue'],
            text=True, timeout=5,
        )
        for line in out.splitlines():
            if 'signal-quality.value' in line:
                pct = int(line.split(':')[1].strip())
                bars = min(4, pct // 25)
                return '▂▄▆█'[:bars] if bars > 0 else '·'
    except Exception:
        pass
    return None


def status_snapshot() -> dict:
    """One poll of what the home strip and the top status bar show:
    ``ssid``, ``signal`` (bars, only probed without Wi-Fi), ``battery``
    (0-100 or None) and ``charging``. Blocking (nmcli/mmcli): workers only."""
    ssid = wifi_ssid()
    return {
        'ssid': ssid,
        'signal': None if ssid else mobile_signal(),
        'battery': battery_pct(),
        'charging': battery_charging(),
    }


def format_status_line(snapshot: dict) -> str:
    """The home strip line for a status_snapshot(). Pure."""
    parts: list[str] = []
    if snapshot.get('ssid'):
        parts.append(snapshot['ssid'])
    elif snapshot.get('signal'):
        parts.append(snapshot['signal'])
    pct = snapshot.get('battery')
    if pct is not None:
        parts.append(f"{pct}%{'+' if snapshot.get('charging') else ''}")
    return '  ·  '.join(parts)


def status_line() -> str:
    """Single-line status string for the home screen header."""
    return format_status_line(status_snapshot())


def status_line_async(callback, on_snapshot=None) -> None:
    """Collect status_line() off the GTK thread, deliver on the main loop.

    `callback(line)` fires exactly once via GLib.idle_add, even on failure
    (with ''). Never raises. With `on_snapshot`, the worker polls once via
    status_snapshot() and also delivers the dict (after the line) to the
    top status bar: one poll feeds both, no second poller.
    """
    from gi.repository import GLib

    def _worker() -> None:
        snapshot = None
        try:
            if on_snapshot is not None:
                snapshot = status_snapshot()
                line = format_status_line(snapshot)
            else:
                line = status_line()
        except Exception:
            line = ''

        def _deliver() -> bool:
            callback(line)
            if snapshot is not None:
                try:
                    on_snapshot(snapshot)
                except Exception:
                    pass
            return False
        GLib.idle_add(_deliver)

    threading.Thread(target=_worker, daemon=True).start()
