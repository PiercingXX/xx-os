"""App standby (Android Doze / app-standby parity).

Freeze apps the user isn't using so they stop burning battery, thaw
instantly on return. Every app the shell launches is placed into its own
transient systemd user scope (StartTransientUnit on the session bus);
freezing uses FreezeUnit/ThawUnit (systemd 246+). Where systemd is not the
user manager (OpenRC devices) the controller degrades to pid records plus
per-process SIGSTOP/SIGCONT — same observable behavior for the common
case, documented in docs/config.md.

The standby POLICY (should_standby) is a pure function of timestamps and
exemption flags so it is table-testable. All D-Bus/subprocess probing
(MPRIS, PipeWire, Calls owner, battery) runs in a worker thread and posts
back via GLib.idle_add, so the GTK main loop never blocks. Async D-Bus
calls never pass user_data: per the runtime traps, the callback then gets
exactly (conn, result), so per-call context travels in closures.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field

from gi.repository import Gio, GLib

from shell_log import get_logger

_log = get_logger('standby')

SYSTEMD_DEST = 'org.freedesktop.systemd1'
SYSTEMD_PATH = '/org/freedesktop/systemd1'
SYSTEMD_MANAGER = 'org.freedesktop.systemd1.Manager'

DEFAULT_STANDBY_MINUTES = 10
BLANK_FREEZE_SECONDS = 120
SAVER_BLANK_FREEZE_SECONDS = 30
NOTIF_ACTION_EXEMPT_SECONDS = 60
SAVER_AUTO_BATTERY_PCT = 15
SAVER_AUTO_CLEAR_PCT = 20
SWEEP_SECONDS = 30
EXEMPT_REFRESH_SECONDS = 60

# Voice-call apps are never frozen: freezing the dialer mid-call (or the
# ringing path) would drop audio. Matched on the normalized app id; the
# live org.gnome.Calls bus owner is attributed by pid on top of this.
TELEPHONY_APP_IDS = frozenset({
    'org.gnome.calls', 'calls', 'xx-dialer', 'xx-phone',
})
# xx-keyboard ships as io.piercingxx.XXKeyboard (normalized: xxkeyboard,
# no hyphen); squeekboard/stevia stay for the fallback and the old name.
KEYBOARD_MARKERS = ('squeekboard', 'stevia', 'xx-keyboard', 'xxkeyboard')

# Messaging (TxxT/Chatty) and clock/alarm apps are never frozen: freezing
# the SMS daemon would stall incoming texts, and freezing the clock would
# silence alarms. Matched on the normalized app id like TELEPHONY_APP_IDS.
SMS_APP_IDS = frozenset({
    'chatty', 'sm.puri.chatty', 'org.sm.puri.chatty',
    'xx-sms', 'xx-txtt',
})
CLOCK_APP_IDS = frozenset({
    'org.gnome.clocks', 'gnome-clocks', 'org.gnome.clock',
    'xx-clock', 'xx-alarm',
})
BUILTIN_EXEMPT_APP_IDS = TELEPHONY_APP_IDS | SMS_APP_IDS | CLOCK_APP_IDS

# Desktop-file categories that are never frozen: mail, chat and VoIP apps
# have to keep their connections up to receive anything while blanked.
EXEMPT_CATEGORIES = frozenset({
    'email', 'chat', 'instantmessaging', 'softphone', 'telephony',
})

# Built-in exemptions as the settings UI lists them (read-only).
BUILTIN_EXEMPTION_LABELS = (
    'Dialer', 'TxxT daemon', 'Calls', 'Clock & alarms', 'Email & chat',
    'Media playing',
)


def is_builtin_exempt(app_id: str) -> bool:
    """True for ids the shell never freezes (dialer, texts, calls, clock):
    media playing is dynamic (MPRIS/audio) and handled by the caller."""
    return normalize_app_id(app_id) in BUILTIN_EXEMPT_APP_IDS


def builtin_exemption_labels() -> list[str]:
    """Display names of the always-exempt surfaces for the settings UI."""
    return list(BUILTIN_EXEMPTION_LABELS)

_MPRIS_PREFIX = 'org.mpris.MediaPlayer2.'
_CALLS_BUS_NAME = 'org.gnome.Calls'
_UPOWER_DISPLAY = '/org/freedesktop/UPower/devices/DisplayDevice'


def normalize_app_id(app_id: str) -> str:
    """Compare app ids stripped of `.desktop` and case: mute keys, drawer
    ids and notification desktop-entry hints all meet here."""
    text = (app_id or '').strip()
    if text.lower().endswith('.desktop'):
        text = text[:-8]
    return text.casefold()


_SCOPE_CLEAN_RE = re.compile(r'[^A-Za-z0-9_.-]')


def sanitize_app_id(app_id: str) -> str:
    """Scope-name fragment for an app id: unit names allow dots, so only
    anything outside [A-Za-z0-9_.-] becomes `_`."""
    return _SCOPE_CLEAN_RE.sub('_', (app_id or '').strip()) or 'unknown'


def scope_name_for(app_id: str, pid: int) -> str:
    """Transient scope name for one launched process, e.g.
    app-xxwm-org.gnome.Calls.desktop-1234.scope."""
    return f'app-xxwm-{sanitize_app_id(app_id)}-{int(pid)}.scope'


STANDBY_SCOPE_PREFIX = 'app-xxwm-'
STANDBY_SCOPE_SUFFIX = '.scope'
STANDBY_SCOPE_PATTERN = 'app-xxwm-*.scope'
STOPPED_REL_PATH = os.path.join('xx-wm', 'standby-stopped.json')
FROZEN_UNITS_REL_PATH = os.path.join('xx-wm', 'standby-frozen-units.json')
DBUS_LIST_TIMEOUT_MS = 500
SHUTDOWN_UNIT_TIMEOUT_MS = 200
SHUTDOWN_BUDGET_S = 1.0


def parse_standby_scope(name: str) -> tuple[str | None, int | None]:
    """Split our transient scope back into (app-id fragment, pid).

    Returns (None, None) for foreign units; (fragment, None) when the
    pid suffix is missing or malformed (the unit is still thawed, just
    not re-adopted as a pid-tracked app)."""
    if not isinstance(name, str):
        return (None, None)
    if not name.startswith(STANDBY_SCOPE_PREFIX):
        return (None, None)
    if not name.endswith(STANDBY_SCOPE_SUFFIX):
        return (None, None)
    inner = name[len(STANDBY_SCOPE_PREFIX):-len(STANDBY_SCOPE_SUFFIX)]
    if not inner or '-' not in inner:
        return (None, None)
    app_frag, _, pid_text = inner.rpartition('-')
    if not app_frag:
        return (None, None)
    try:
        pid = int(pid_text)
    except (TypeError, ValueError):
        return (app_frag, None)
    if pid <= 0:
        return (app_frag, None)
    return (app_frag, pid)


def dbus_bus_name_for_app(app_id: str) -> str:
    """Well-known bus name for a DBusActivatable app: the app id without
    the `.desktop` suffix, case preserved (bus names are case-sensitive)."""
    text = (app_id or '').strip()
    if text.lower().endswith('.desktop'):
        text = text[:-8]
    return text


def unit_patterns_for_app(app_id: str) -> list[str]:
    """ListUnitsByPatterns patterns covering the units a D-Bus-activated
    app runs as: dbus-broker transients (`dbus-:<n>-<bus>@<n>.service`),
    classic/template `dbus-<bus>[.service|@<n>.service]`, and GNOME-spawned
    `app-<launcher>-<app id>-<n>.scope`. The full bus name stays literal
    in every pattern so unrelated units never match the query itself."""
    bus = dbus_bus_name_for_app(app_id)
    full = (app_id or '').strip()
    patterns: list[str] = []
    if bus:
        patterns.extend((
            f'dbus-*-{bus}@*.service',
            f'dbus-{bus}.service',
            f'dbus-{bus}@*.service',
            f'app-*-{bus}-*.scope',
        ))
    if full and full != bus:
        patterns.append(f'app-*-{full}-*.scope')
    seen: set[str] = set()
    ordered = [pat for pat in patterns if pat and pat not in seen
               and not seen.add(pat)]
    return ordered


def _unit_name(entry: object) -> str | None:
    if isinstance(entry, (tuple, list)) and entry:
        name = entry[0]
    else:
        name = entry
    return name if isinstance(name, str) and name else None


def select_app_units(units: object, app_id: str) -> list[str]:
    """Pick this app's units out of a ListUnits reply (or plain names).

    Only exact bus-name matches count: `org.example.App` never selects
    `org.example.AppOther`, and our own `app-xxwm-*` scopes are left to
    the re-adopt path, never to D-Bus resolution."""
    bus = dbus_bus_name_for_app(app_id)
    full = (app_id or '').strip()
    candidates = {cand for cand in (bus, full) if cand}
    if not candidates:
        return []
    entries = units if isinstance(units, (list, tuple)) else []
    matched: list[str] = []
    for entry in entries:
        name = _unit_name(entry)
        if name is None or name in matched:
            continue
        if name.startswith(STANDBY_SCOPE_PREFIX):
            continue
        hit = False
        if name.startswith('dbus-') and name.endswith('.service'):
            rest = name[len('dbus-'):-len('.service')]
            if name.startswith('dbus-:'):
                if '@' in rest:
                    dash = rest.find('-')
                    candidate = rest[dash + 1:rest.rfind('@')] if dash >= 0 else ''
                    hit = candidate in candidates
            elif '@' in rest:
                base, _, inst = rest.partition('@')
                hit = bool(inst) and base in candidates
            else:
                hit = rest in candidates
        elif name.startswith('app-') and name.endswith('.scope'):
            middle = name[len('app-'):-len('.scope')]
            head, _, tail = middle.rpartition('-')
            if head and tail.isdigit():
                hit = any(head == cand or head.endswith('-' + cand)
                          for cand in candidates)
        if hit:
            matched.append(name)
    return matched


def _runtime_dir() -> str:
    return os.environ.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}'


def stopped_file_path() -> str:
    """Degraded-path ledger: runtime dir lifetime matches the need (gone
    on reboot, so no stale SIGSTOP can survive a boot cycle)."""
    return os.path.join(_runtime_dir(), STOPPED_REL_PATH)


def frozen_units_file_path() -> str:
    """Ledger of units this shell froze, thawed at the next start so a
    crash or deploy (which skips the quit path) never leaves apps frozen."""
    return os.path.join(_runtime_dir(), FROZEN_UNITS_REL_PATH)


_LEDGER_UNIT_RE = re.compile(r'^[A-Za-z0-9:_.@-]+\.(scope|service)$')


def _read_json_list(path: str) -> list:
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _write_private_json(path: str, data: object) -> None:
    """Write a runtime ledger: directory 0700, file 0600."""
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump(data, handle)


def _proc_stat_fields(pid: object, proc_root: str) -> list[str] | None:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return None
    if pid_int <= 0:
        return None
    try:
        with open(os.path.join(proc_root, str(pid_int), 'stat'), 'r',
                  encoding='utf-8') as handle:
            data = handle.read()
    except OSError:
        return None
    return data.rpartition(')')[2].split()


def proc_starttime(pid: int, proc_root: str = '/proc') -> int | None:
    """Process start time (clock ticks since boot, /proc stat field 22)
    or None when the pid is gone/unreadable. Used to never signal a
    recycled pid."""
    fields = _proc_stat_fields(pid, proc_root)
    try:
        return int(fields[19]) if fields else None
    except (IndexError, ValueError):
        return None


def proc_parent(pid: int, proc_root: str = '/proc') -> int | None:
    """Parent pid (/proc stat field 4), None when unreadable."""
    fields = _proc_stat_fields(pid, proc_root)
    try:
        return int(fields[1]) if fields else None
    except (IndexError, ValueError):
        return None


def proc_unit(pid: int, proc_root: str = '/proc') -> str | None:
    """Innermost systemd unit (.scope/.service) the process lives in,
    from /proc/<pid>/cgroup; None when unreadable or not in a unit."""
    try:
        with open(os.path.join(proc_root, str(int(pid)), 'cgroup'), 'r',
                  encoding='utf-8') as handle:
            text = handle.read()
    except (OSError, TypeError, ValueError):
        return None
    for line in text.splitlines():
        path = line.split(':', 2)[-1]
        for part in reversed(path.split('/')):
            if part.endswith('.scope') or part.endswith('.service'):
                return part
    return None


def expand_pids(pids: object, proc_root: str = '/proc',
                max_depth: int = 32) -> tuple[set[int], set[str]]:
    """Pids plus all their ancestors, and the units they run in.

    An app's audio or MPRIS player is often a child process (a browser
    content process, a helper), so the app is matched by being an
    ancestor of the playing pid or sharing its unit (scope)."""
    lineage: set[int] = set()
    units: set[str] = set()
    for pid in pids or ():
        try:
            cur = int(pid)
        except (TypeError, ValueError):
            continue
        unit = proc_unit(cur, proc_root)
        if unit:
            units.add(unit)
        for _depth in range(max_depth):
            if cur <= 1 or cur in lineage:
                break
            lineage.add(cur)
            parent = proc_parent(cur, proc_root)
            if parent is None:
                break
            cur = parent
    return lineage, units


def _unit_gone(error: object) -> bool:
    """FreezeUnit/ThawUnit failed because the unit no longer exists or is
    not running: the app exited, so there is nothing to freeze."""
    name = ''
    try:
        name = Gio.DBusError.get_remote_error(error) or ''
    except Exception:
        name = ''
    text = f'{name} {error}'
    return any(marker in text for marker in (
        'NoSuchUnit', 'UnitInactive', 'not loaded', 'not active'))


def _desktop_app_info_class() -> object:
    """GioUnix.DesktopAppInfo on GLib 2.86+, Gio's (deprecated) before."""
    try:
        import gi
        gi.require_version('GioUnix', '2.0')
        from gi.repository import GioUnix
        return GioUnix.DesktopAppInfo
    except (ImportError, ValueError, AttributeError):
        return Gio.DesktopAppInfo


def _default_desktop_lookup(app_id: str) -> dict:
    """Desktop-file facts standby needs: StartupWMClass (alias for the
    toplevel app_id), categories, Flatpak export, DBusActivatable."""
    text = (app_id or '').strip()
    if not text:
        return {}
    desktop_id = text if text.lower().endswith('.desktop') else text + '.desktop'
    try:
        info = _desktop_app_info_class().new(desktop_id)
    except Exception:
        info = None
    if info is None:
        return {}
    facts: dict = {}
    try:
        facts['wm_class'] = info.get_startup_wm_class() or ''
    except Exception:
        pass
    try:
        facts['categories'] = [c for c in (info.get_categories() or '')
                               .split(';') if c]
    except Exception:
        pass
    try:
        filename = info.get_filename() or ''
        facts['flatpak'] = ('flatpak' in filename.split(os.sep)
                            or bool(info.has_key('X-Flatpak')))
    except Exception:
        pass
    try:
        facts['dbus_activatable'] = bool(info.get_boolean('DBusActivatable'))
    except Exception:
        pass
    return facts


def is_telephony_app(app_id: str) -> bool:
    return normalize_app_id(app_id) in TELEPHONY_APP_IDS


def is_keyboard_app(app_id: str) -> bool:
    lowered = normalize_app_id(app_id)
    return any(marker in lowered for marker in KEYBOARD_MARKERS)


def should_standby(
    app_id: str, *, now: float, last_active: float | None,
    screen_blanked: bool, blank_since: float | None,
    standby_minutes: int, blank_seconds: int = BLANK_FREEZE_SECONDS,
    mpris_playing: bool = False, audio_busy: bool = False,
    is_telephony: bool = False, is_keyboard: bool = False,
    exempt_ids: tuple | list = (),
    last_notif_action: float | None = None,
    notif_action_window: int = NOTIF_ACTION_EXEMPT_SECONDS,
) -> bool:
    """Pure standby policy: True when the app is a freeze candidate.

    Screen on: idle longer than standby_minutes (0 disables everything).
    Screen blank: blanked longer than blank_seconds. Exemptions (media
    playing, live audio, telephony, keyboard, user list, recent
    notification-action use) always win. Unknown activation state
    (last_active None on, blank_since None while blanked) never freezes.
    """
    try:
        minutes = int(standby_minutes)
    except (TypeError, ValueError):
        minutes = DEFAULT_STANDBY_MINUTES
    if minutes <= 0:
        return False
    if mpris_playing or audio_busy or is_telephony or is_keyboard:
        return False
    if is_builtin_exempt(app_id):
        return False
    wanted = normalize_app_id(app_id)
    for exempt in exempt_ids or ():
        if normalize_app_id(str(exempt)) == wanted:
            return False
    if last_notif_action is not None:
        try:
            if float(now) - float(last_notif_action) < notif_action_window:
                return False
        except (TypeError, ValueError):
            pass
    if screen_blanked:
        if blank_since is None:
            return False
        try:
            return float(now) - float(blank_since) >= float(blank_seconds)
        except (TypeError, ValueError):
            return False
    if last_active is None:
        return False
    try:
        return float(now) - float(last_active) >= minutes * 60
    except (TypeError, ValueError):
        return False


def saver_effective_minutes(base_minutes: int, saver_on: bool) -> int:
    """Battery saver halves the idle timeout (floor 1); an explicit 0
    (standby disabled) stays disabled under the saver."""
    try:
        base = int(base_minutes)
    except (TypeError, ValueError):
        base = DEFAULT_STANDBY_MINUTES
    if base <= 0:
        return 0
    if saver_on:
        return max(1, base // 2)
    return base


def saver_blank_seconds(saver_on: bool) -> int:
    return SAVER_BLANK_FREEZE_SECONDS if saver_on else BLANK_FREEZE_SECONDS


def saver_auto_threshold(config: object) -> int:
    """Auto-saver battery percent from config (settings UI selectable,
    default SAVER_AUTO_BATTERY_PCT): clamped to 1..100, corrupt reads fail
    to the default."""
    try:
        pct = int(getattr(config, 'battery_saver_auto_pct',
                           SAVER_AUTO_BATTERY_PCT))
    except (TypeError, ValueError):
        return SAVER_AUTO_BATTERY_PCT
    return max(1, min(100, pct))


def parse_pw_dump_pids(payload: str) -> set[int]:
    """Pids holding PipeWire nodes (streams/devices), from `pw-dump` JSON.

    Client objects are just daemon connections, not streams, so only
    Node-type (or typeless) objects count — an idle app merely linked
    against PipeWire must not read as busy.
    """
    return audio_pids_or_unknown(payload) or set()


def audio_pids_or_unknown(payload: object) -> set[int] | None:
    """parse_pw_dump_pids, but None when the dump is missing or not a
    pw-dump array: a failed probe means "unknown", never "nobody plays"."""
    if not isinstance(payload, str):
        return None
    try:
        objects = json.loads(payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(objects, list):
        return None
    pids: set[int] = set()
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        if obj.get('type', 'PipeWire:Interface:Node') != 'PipeWire:Interface:Node':
            continue
        info = obj.get('info')
        props = info.get('props') if isinstance(info, dict) else None
        if not isinstance(props, dict):
            continue
        try:
            pids.add(int(props.get('application.process.id')))
        except (TypeError, ValueError):
            continue
    return pids


def _deep(value: object) -> object:
    """Unpack a GLib.Variant (or pass natives through): real D-Bus replies
    arrive as a tuple of out-args, and `a{sv}` values may stay Variants."""
    unpack = getattr(value, 'unpack', None)
    if callable(unpack):
        try:
            return _deep(unpack())
        except Exception:
            return value
    return value


def _get_prop(bus: object, dest: str, obj_path: str,
              iface: str, prop: str) -> object:
    reply = bus.call_sync(
        dest, obj_path, 'org.freedesktop.DBus.Properties', 'Get',
        GLib.Variant('(ss)', (iface, prop)),
        GLib.VariantType('(v)'),
        Gio.DBusCallFlags.NONE, 2000, None,
    )
    return _deep(reply.unpack()[0])


def query_mpris_playing(session_bus: object) -> set[str]:
    """App ids (desktop file names) with an MPRIS player in Playing state,
    via the DesktopEntry property — best-effort, worker thread only."""
    playing: set[str] = set()
    try:
        names = session_bus.call_sync(
            'org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'ListNames', None,
            GLib.VariantType('(as)'),
            Gio.DBusCallFlags.NONE, 2000, None,
        ).unpack()[0]
    except Exception:
        return playing
    for name in names or ():
        if not isinstance(name, str) or not name.startswith(_MPRIS_PREFIX):
            continue
        try:
            if str(_get_prop(session_bus, name, '/org/mpris/MediaPlayer2',
                             'org.mpris.MediaPlayer2.Player',
                             'PlaybackStatus')) != 'Playing':
                continue
            entry = _get_prop(session_bus, name, '/org/mpris/MediaPlayer2',
                              'org.mpris.MediaPlayer2', 'DesktopEntry')
        except Exception:
            continue
        if entry:
            text = str(entry)
            playing.add(text if text.endswith('.desktop') else text + '.desktop')
    return playing


def _name_owner_pid(session_bus: object, name: str) -> int | None:
    """Pid behind a bus name; the bus daemon answers even when the owner
    is frozen."""
    try:
        return int(session_bus.call_sync(
            'org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'GetConnectionUnixProcessID',
            GLib.Variant('(s)', (name,)),
            GLib.VariantType('(u)'),
            Gio.DBusCallFlags.NONE, 2000, None,
        ).unpack()[0])
    except Exception:
        return None


def query_mpris_players(session_bus: object,
                        skip_pids: object = ()) -> list[dict]:
    """Every MPRIS player: {'name', 'pid', 'entry', 'playing'}.

    The pid comes from the bus daemon, so a frozen player is still
    attributed; its own properties are not asked for (pids in skip_pids)
    because a frozen process never answers."""
    players: list[dict] = []
    try:
        names = session_bus.call_sync(
            'org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'ListNames', None,
            GLib.VariantType('(as)'),
            Gio.DBusCallFlags.NONE, 2000, None,
        ).unpack()[0]
    except Exception:
        return players
    skip = set(skip_pids or ())
    for name in names or ():
        if not isinstance(name, str) or not name.startswith(_MPRIS_PREFIX):
            continue
        pid = _name_owner_pid(session_bus, name)
        player = {'name': name, 'pid': pid, 'entry': '', 'playing': False}
        players.append(player)
        if pid is not None and pid in skip:
            continue
        try:
            player['playing'] = str(_get_prop(
                session_bus, name, '/org/mpris/MediaPlayer2',
                'org.mpris.MediaPlayer2.Player', 'PlaybackStatus')) == 'Playing'
        except Exception:
            pass
        try:
            entry = _get_prop(session_bus, name, '/org/mpris/MediaPlayer2',
                              'org.mpris.MediaPlayer2', 'DesktopEntry')
            player['entry'] = str(entry) if entry else ''
        except Exception:
            pass
    return players


def query_calls_owner_pid(session_bus: object) -> int | None:
    """Pid of the process owning org.gnome.Calls, if the name is owned."""
    try:
        owner = session_bus.call_sync(
            'org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'GetNameOwner',
            GLib.Variant('(s)', (_CALLS_BUS_NAME,)),
            GLib.VariantType('(s)'),
            Gio.DBusCallFlags.NONE, 2000, None,
        ).unpack()[0]
        pid = session_bus.call_sync(
            'org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'GetConnectionUnixProcessID',
            GLib.Variant('(s)', (str(owner),)),
            GLib.VariantType('(u)'),
            Gio.DBusCallFlags.NONE, 2000, None,
        ).unpack()[0]
        return int(pid)
    except Exception:
        return None


def query_battery(system_bus: object) -> tuple[float | None, bool]:
    """(percent, charging) from UPower's display device; (None, False)
    when unknown so the saver auto-rule leaves state unchanged."""
    try:
        props = system_bus.call_sync(
            'org.freedesktop.UPower', _UPOWER_DISPLAY,
            'org.freedesktop.DBus.Properties', 'GetAll',
            GLib.Variant('(s)', ('org.freedesktop.UPower.Device',)),
            GLib.VariantType('(a{sv})'),
            Gio.DBusCallFlags.NONE, 2000, None,
        ).unpack()[0]
    except Exception:
        return (None, False)
    if not isinstance(props, dict) or not _deep(props.get('IsPresent', False)):
        return (None, False)
    try:
        pct = float(_deep(props.get('Percentage', 0)))
    except (TypeError, ValueError):
        return (None, False)
    try:
        state = int(_deep(props.get('State', 0)))
    except (TypeError, ValueError):
        state = 0
    return (pct, state == 1)


def launch_with_pid(info: object, on_pid: object = None) -> object:
    """Launch a Gio.DesktopAppInfo reporting the child pid.

    Uses launch_uris_as_manager with a pid callback; on older GLib without
    it, falls back to plain launch() (no pid, no scope). Callback errors
    never break the launch.
    """
    as_manager = getattr(info, 'launch_uris_as_manager', None)
    if on_pid is not None and callable(as_manager):
        def _on_pid(appinfo: object, pid: int, _user_data: object = None) -> None:
            try:
                on_pid(int(pid))
            except Exception:
                pass
        return as_manager([], None, GLib.SpawnFlags.SEARCH_PATH,
                           None, None, _on_pid, None)
    return info.launch([], None)


@dataclass
class _TrackedApp:
    app_id: str
    pids: set[int] = field(default_factory=set)
    starttimes: dict[int, int] = field(default_factory=dict)
    scopes: dict[int, str] = field(default_factory=dict)
    dbus_units: set[str] = field(default_factory=set)
    names: set[str] = field(default_factory=set)
    last_active: float | None = None
    last_notif_action: float | None = None
    frozen: bool = False
    frozen_units: set[str] = field(default_factory=set)
    stopped: set[int] = field(default_factory=set)


def _default_session_bus() -> object | None:
    try:
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except Exception:
        return None


def _default_system_bus() -> object | None:
    try:
        return Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    except Exception:
        return None


def _default_pw_dump() -> str | None:
    """pw-dump JSON; None when it ran and failed (unknown: freeze nothing).
    Without pw-dump installed there is no PipeWire to ask, so the answer
    is an empty dump and MPRIS stays the media guard."""
    try:
        out = subprocess.run(['pw-dump'], capture_output=True, text=True,
                             timeout=10)
    except FileNotFoundError:
        return '[]'
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


class StandbyController:
    """Tracks shell-launched apps in systemd scopes and freezes the idle.

    Only apps placed here via track() (i.e. launched through the shell's
    launch paths) are ever frozen — the shell, phoc and anything else on
    the system are never touched. Activation observation prefers the
    ToplevelManager.on_activated_changed signal (f_usage lane); without it
    the manager list is polled for an `activated` attribute, and with
    neither observable the sweep freezes nothing.
    """

    def __init__(self, config: object = None,
                 session_bus_factory: object = None,
                 system_bus_factory: object = None,
                 force_degraded: bool = False,
                 clock: object = None, manager: object = None,
                 pw_dump_runner: object = None,
                 network_denied=None, network_blocker=None,
                 proc_root: str = '/proc',
                 cgroup_root: str = '/sys/fs/cgroup',
                 stopped_file: str | None = None,
                 on_saver_changed=None,
                 starttime_reader=None,
                 desktop_lookup=None,
                 frozen_units_file: str | None = None) -> None:
        self._config = config
        self._session_bus_factory = session_bus_factory or _default_session_bus
        self._system_bus_factory = system_bus_factory or _default_system_bus
        self._force_degraded = bool(force_degraded)
        self._network_denied = network_denied
        self._network_blocker = network_blocker
        self._proc_root = proc_root or '/proc'
        self._cgroup_root = cgroup_root or '/sys/fs/cgroup'
        self._stopped_file_override = stopped_file
        if frozen_units_file is None and stopped_file:
            frozen_units_file = os.path.join(
                os.path.dirname(stopped_file), 'standby-frozen-units.json')
        self._frozen_units_override = frozen_units_file
        self._starttime_reader = starttime_reader
        self._desktop_lookup = desktop_lookup or _default_desktop_lookup
        self._desktop_cache: dict[str, dict] = {}
        self._aliases: dict[str, str] = {}
        self._clock = clock or time.time
        self._manager = manager
        self._manager_ready = manager is not None
        self._subscribed = False
        self._observable: bool | None = None
        self._apps: dict[str, _TrackedApp] = {}
        self._blanked = False
        self._blank_since: float | None = None
        self._mpris_playing: set[str] = set()
        self._mpris_pids: set[int] = set()
        self._mpris_units: set[str] = set()
        self._player_keys: set[str] = set()
        self._player_pids: set[int] = set()
        self._player_units: set[str] = set()
        self._audio_known = True
        self._audio_pids: set[int] = set()
        self._audio_units: set[str] = set()
        self._calls_pids: set[int] = set()
        self._battery: tuple[float | None, bool] = (None, False)
        self._manual_saver = False
        self._auto_saver = False
        self._was_low: bool | None = None
        self._on_saver_changed = on_saver_changed
        self._last_saver_notified: bool | None = None
        self._notif_apps: dict[int, str] = {}
        self._sweep_src: int | None = None
        self._refresh_src: int | None = None
        self._session_bus: object | None = None
        self._session_bus_failed = False
        self._pw_dump = pw_dump_runner or _default_pw_dump

    # -- config (tolerates config=None: compiled-in defaults) ---------------

    def _now(self, now: float | None = None) -> float:
        return self._clock() if now is None else now

    @property
    def _standby_minutes(self) -> int:
        return getattr(self._config, 'app_standby_minutes',
                        DEFAULT_STANDBY_MINUTES)

    @property
    def _standby_exempt(self) -> list:
        return getattr(self._config, 'standby_exempt', [])

    @property
    def _saver_auto_enabled(self) -> bool:
        return getattr(self._config, 'battery_saver_auto', True)

    def _manual_saver_on(self) -> bool:
        if self._config is not None:
            try:
                return bool(self._config.battery_saver)
            except Exception:
                pass
        return self._manual_saver

    @property
    def saver_effective(self) -> bool:
        """Manual toggle or automatic low-battery saver."""
        return bool(self._manual_saver_on() or self._auto_saver)

    def set_saver(self, enabled: bool) -> None:
        """User toggle: writes the manual key; turning off also drops the
        automatic latch (it re-engages only on a fresh low-battery edge)."""
        enabled = bool(enabled)
        if self._config is not None:
            try:
                self._config.set_battery_saver(enabled)
            except Exception:
                self._manual_saver = enabled
        else:
            self._manual_saver = enabled
        if not enabled:
            self._auto_saver = False
        self._maybe_notify_saver()

    def _maybe_notify_saver(self) -> None:
        """Fire on_saver_changed on effective-saver edges (manual and
        automatic): battery saver selects the power-saver profile."""
        try:
            effective = bool(self.saver_effective)
        except Exception:
            return
        if effective == self._last_saver_notified:
            return
        self._last_saver_notified = effective
        hook = self._on_saver_changed
        if hook is None:
            return
        try:
            hook(effective)
        except Exception as error:
            _log.info('saver power hook failed: %s', error)

    # -- tracking ------------------------------------------------------------

    def _bus(self) -> object | None:
        if self._session_bus is None and not self._session_bus_failed:
            try:
                self._session_bus = self._session_bus_factory()
            except Exception as error:
                _log.info('standby session bus unavailable: %s', error)
                self._session_bus_failed = True
        return self._session_bus

    # One record per app: the drawer's desktop id (`X.desktop`), the
    # toplevel app_id (`X`) and the desktop file's StartupWMClass all
    # resolve to the same normalized key.

    def _key(self, app_id: object) -> str:
        norm = normalize_app_id(str(app_id or ''))
        return self._aliases.get(norm, norm)

    def _record(self, app_id: object,
                create: bool = True) -> _TrackedApp | None:
        key = self._key(app_id)
        if not key:
            return None
        rec = self._apps.get(key)
        if rec is None and create:
            rec = self._apps[key] = _TrackedApp(app_id=str(app_id))
        if rec is not None and create:
            rec.names.add(str(app_id))
        return rec

    def _desktop_info(self, app_id: object) -> dict:
        raw = str(app_id or '').strip()
        if raw in self._desktop_cache:
            return self._desktop_cache[raw]
        try:
            facts = self._desktop_lookup(raw) if raw else {}
        except Exception:
            facts = {}
        if not isinstance(facts, dict):
            facts = {}
        self._desktop_cache[raw] = facts
        return facts

    def add_alias(self, alias: str, app_id: str) -> None:
        """Map another id for the same app (StartupWMClass, a toplevel
        app_id that differs from the desktop id) onto app_id's record,
        merging a record already kept under the alias."""
        src = normalize_app_id(str(alias or ''))
        dst = self._key(app_id)
        if not src or not dst or src == dst:
            return
        self._aliases[src] = dst
        for name, target in list(self._aliases.items()):
            if target == src:
                self._aliases[name] = dst
        other = self._apps.pop(src, None)
        if other is None:
            return
        rec = self._apps.get(dst)
        if rec is None:
            self._apps[dst] = other
        else:
            self._merge(rec, other)

    @staticmethod
    def _merge(rec: _TrackedApp, other: _TrackedApp) -> None:
        rec.pids |= other.pids
        rec.starttimes.update(other.starttimes)
        rec.scopes.update(other.scopes)
        rec.dbus_units |= other.dbus_units
        rec.names |= other.names
        rec.stopped |= other.stopped
        rec.frozen_units |= other.frozen_units
        rec.frozen = rec.frozen or other.frozen
        for attr in ('last_active', 'last_notif_action'):
            stamps = [v for v in (getattr(rec, attr), getattr(other, attr))
                      if v is not None]
            setattr(rec, attr, max(stamps) if stamps else None)

    def _learn_aliases(self, app_id: str) -> None:
        wm_class = self._desktop_info(app_id).get('wm_class')
        if isinstance(wm_class, str) and wm_class.strip():
            self.add_alias(wm_class, app_id)

    def _starttime(self, pid: int) -> int | None:
        reader = self._starttime_reader
        if reader is None:
            return proc_starttime(pid, self._proc_root)
        try:
            return reader(pid)
        except Exception:
            return None

    def track(self, app_id: str, pid: int, now: float | None = None) -> None:
        """Record a freshly launched child and place it in its own scope.
        Launch counts as use: last_active starts now. The pid's start
        time is kept so a recycled pid is never signalled."""
        now = self._now(now)
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return
        if pid <= 0:
            return
        self._learn_aliases(app_id)
        rec = self._record(app_id)
        if rec is None:
            return
        rec.app_id = str(app_id)
        rec.pids.add(pid)
        started = self._starttime(pid)
        if started is None:
            rec.starttimes.pop(pid, None)
        else:
            rec.starttimes[pid] = started
        rec.last_active = now
        if self._force_degraded:
            return
        bus = self._bus()
        if bus is None:
            return
        scope = scope_name_for(app_id, pid)
        rec.scopes[pid] = scope
        params = GLib.Variant(
            '(ssa(sv)a(sa(sv)))',
            (scope, 'fail', [('PIDs', GLib.Variant('au', [pid]))], []),
        )
        try:
            bus.call(SYSTEMD_DEST, SYSTEMD_PATH, SYSTEMD_MANAGER,
                     'StartTransientUnit', params, None,
                     Gio.DBusCallFlags.NONE, -1, None,
                     self._on_scope_placed(app_id, pid))
        except Exception as error:
            _log.info('scope for %s pid %d not placed: %s', app_id, pid, error)
            rec.scopes.pop(pid, None)

    def _on_scope_placed(self, app_id: str, pid: int) -> object:
        def _done(conn: object, res: object) -> None:
            try:
                conn.call_finish(res)
            except Exception as error:
                rec = self._record(app_id, create=False)
                if rec is not None:
                    rec.scopes.pop(pid, None)
                _log.info('scope for %s pid %d not placed: %s',
                          app_id, pid, error)
                return
            self._maybe_block_network(app_id, pid)
        return _done

    def set_network_policy(self, denied=None, blocker=None) -> None:
        """Per-app network denials: `denied` is a list or a callable
        returning one (so config hot-reloads apply); `blocker` takes
        (cgroup_path, allowed) and defaults to the root helper call."""
        self._network_denied = denied
        if blocker is not None:
            self._network_blocker = blocker

    def _network_denied_ids(self) -> list:
        if callable(self._network_denied):
            try:
                return list(self._network_denied() or ())
            except Exception:
                return []
        if self._network_denied is not None:
            try:
                return list(self._network_denied)
            except TypeError:
                return []
        try:
            return list(self._config.network_denied_apps or ())
        except Exception:
            return []

    def network_not_enforced(self, app_id: str) -> bool:
        """True when app_id is in network_denied_apps but a launch cannot
        be blocked: a Flatpak runs in flatpak's own scope, and a D-Bus
        activated app is started by the bus, outside any shell scope."""
        try:
            from app_permissions import is_network_denied
            if not is_network_denied(app_id, self._network_denied_ids()):
                return False
        except Exception:
            return False
        facts = self._desktop_info(app_id)
        return bool(facts.get('flatpak') or facts.get('dbus_activatable'))

    def _log_network_enforcement(self, app_id: str) -> None:
        if not self.network_not_enforced(app_id):
            return
        why = ('Flatpak apps run outside the launch scope'
               if self._desktop_info(app_id).get('flatpak')
               else 'D-Bus activation starts it outside the shell')
        _log.warning('network block for %s not enforced: %s', app_id, why)

    def scope_names_for(self, app_id: str) -> dict[int, str]:
        """Tracked pid -> scope names for one app ({} when unknown)."""
        rec = self._record(app_id, create=False)
        if rec is None:
            return {}
        return dict(rec.scopes)

    def cgroup_path_for(self, app_id: str,
                        pid: int | None = None) -> str | None:
        """Verified /sys/fs/cgroup path for one tracked scope: the proc
        cgroup must actually name the scope, never a constructed guess."""
        rec = self._record(app_id, create=False)
        if rec is None:
            return None
        try:
            from app_permissions import find_scope_cgroup
        except ImportError:
            return None
        pids = [int(pid)] if pid is not None else sorted(rec.scopes)
        for cand in pids:
            scope = rec.scopes.get(cand)
            if not scope:
                continue
            path = find_scope_cgroup(scope, cand,
                                     proc_root=self._proc_root,
                                     cgroup_root=self._cgroup_root)
            if path:
                return path
        return None

    def _maybe_block_network(self, app_id: str, pid: int) -> None:
        """Launch enforcement for network_denied_apps: the scope was just
        placed, so block its cgroup now — before the app's first packets
        where the schedule allows (see docs/config.md for the residual
        race: the pid runs before its scope exists, so the earliest
        packets can escape)."""
        try:
            from app_permissions import (default_network_blocker,
                                         enforce_network_policy,
                                         normalize_app_id)
        except ImportError:
            return
        try:
            denied = self._network_denied_ids()
        except Exception:
            return
        if not any(normalize_app_id(str(e)) == normalize_app_id(app_id)
                   for e in denied):
            return
        rec = self._record(app_id, create=False)
        scope = rec.scopes.get(pid) if rec is not None else None
        if not scope:
            return
        try:
            from app_permissions import find_scope_cgroup
            path = find_scope_cgroup(scope, pid,
                                     proc_root=self._proc_root,
                                     cgroup_root=self._cgroup_root)
        except Exception:
            return
        blocker = self._network_blocker or default_network_blocker
        enforce_network_policy(app_id, path, denied, blocker)

    # -- thaw ----------------------------------------------------------------

    def thaw_for_launch(self, app_id: str, now: float | None = None) -> None:
        """Thaw BEFORE relaunching from home/drawer; relaunch counts as use.
        Both shell launch paths call this first, so it also logs a network
        denial the launch cannot enforce."""
        self._log_network_enforcement(app_id)
        rec = self._record(app_id, create=False)
        if rec is not None:
            self._thaw(rec)
            rec.last_active = self._now(now)

    def note_activated(self, app_id: str, now: float | None = None) -> None:
        """The app's toplevel was activated (switcher, tap, any means):
        thaw it and stamp use."""
        rec = self._record(app_id)
        if rec is None:
            return
        self._thaw(rec)
        rec.last_active = self._now(now)

    def _thaw(self, rec: _TrackedApp) -> None:
        if not rec.frozen and not rec.stopped:
            return
        bus = None if self._force_degraded else self._bus()
        units = sorted(set(rec.scopes.values()) | set(rec.dbus_units)
                       | set(rec.frozen_units))
        if bus is not None:
            for unit in units:
                try:
                    bus.call(SYSTEMD_DEST, SYSTEMD_PATH, SYSTEMD_MANAGER,
                             'ThawUnit', GLib.Variant('(s)', (unit,)), None,
                             Gio.DBusCallFlags.NONE, -1, None,
                             self._on_unit_done('ThawUnit', unit))
                except Exception as error:
                    _log.info('thaw %s failed: %s', unit, error)
            self._forget_frozen_units(units)
        conted = sorted(rec.stopped)
        for pid in conted:
            self._signal_pid(rec, pid, signal.SIGCONT)
        if conted:
            self._forget_stopped(conted)
        rec.stopped.clear()
        rec.frozen_units.clear()
        rec.frozen = False

    def activate_with_thaw(self, manager: object, app_id: str,
                           handle: object,
                           on_activated: object = None,
                           now: float | None = None) -> None:
        """Switcher and neighbour-gesture path: thaw BEFORE the activation
        reaches the compositor.

        ThawUnit/SIGCONT are dispatched first, then manager.activate, then
        on_activated, so a frozen app is running by the time its window
        is raised."""
        rec = self._record(app_id)
        if rec is not None:
            self._thaw(rec)
            rec.last_active = self._now(now)
        manager.activate(handle)
        if on_activated is not None:
            on_activated()

    def close_with_thaw(self, manager: object, app_id: str,
                        handle: object, now: float | None = None) -> None:
        """Switcher close: a frozen client never handles the close request,
        so thaw it first (and stamp use, in case it asks to save)."""
        rec = self._record(app_id, create=False)
        if rec is not None:
            self._thaw(rec)
            rec.last_active = self._now(now)
        manager.close(handle)

    def note_media_key(self, now: float | None = None) -> list[str]:
        """A headset/media key was pressed: thaw every frozen app that owns
        an MPRIS player, before the key reaches the player. Returns the
        app ids thawed."""
        thawed: list[str] = []
        for key, rec in list(self._apps.items()):
            if not rec.frozen and not rec.stopped:
                continue
            units = set(rec.scopes.values()) | rec.dbus_units
            if (key in self._player_keys or rec.pids & self._player_pids
                    or units & self._player_units):
                self._thaw(rec)
                rec.last_active = self._now(now)
                thawed.append(rec.app_id)
        return thawed

    def wrap_media_key(self, handler: object) -> object:
        """Media-key callback that thaws a frozen player, then runs handler."""
        def _on_media_key(action: str, *args: object) -> object:
            try:
                self.note_media_key()
            except Exception as error:
                _log.info('standby media-key thaw failed: %s', error)
            if handler is None:
                return False
            return handler(action, *args)
        return _on_media_key

    def _on_unit_done(self, what: str, scope: str) -> object:
        def _done(conn: object, res: object) -> None:
            try:
                conn.call_finish(res)
            except Exception as error:
                _log.info('%s %s failed: %s', what, scope, error)
        return _done

    # -- freeze ---------------------------------------------------------------

    def _ensure_dbus_units_sync(self, rec: _TrackedApp) -> list[str]:
        """Resolve a pid-less app's units via the user manager (best
        effort, one bounded local call) and cache them on the record.

        Only ever called for apps the shell launched or saw as a toplevel
        (records exist solely for those), and select_app_units keeps units
        that do not belong to this app out even if the manager lists them.
        """
        if self._force_degraded:
            return []
        bus = self._bus()
        if bus is None:
            return []
        patterns = unit_patterns_for_app(rec.app_id)
        if not patterns:
            return []
        reply = bus.call_sync(
            SYSTEMD_DEST, SYSTEMD_PATH, SYSTEMD_MANAGER,
            'ListUnitsByPatterns',
            GLib.Variant('(asas)', ([], patterns)),
            GLib.VariantType('(a(ssssssouso))'),
            Gio.DBusCallFlags.NONE, DBUS_LIST_TIMEOUT_MS, None,
        )
        matched = select_app_units(reply.unpack()[0], rec.app_id)
        for unit in matched:
            rec.dbus_units.add(unit)
        return matched

    def _freeze(self, rec: _TrackedApp) -> bool:
        if rec.frozen:
            return True
        self._prune_pids(rec)
        bus = None if self._force_degraded else self._bus()
        if not rec.pids and not rec.scopes and not rec.dbus_units:
            try:
                self._ensure_dbus_units_sync(rec)
            except Exception as error:
                _log.info('standby unit resolve for %s failed: %s',
                          rec.app_id, error)
        for pid, scope in sorted(rec.scopes.items()):
            if bus is None:
                self._stop_pid(rec, pid)
                continue
            self._dispatch_freeze(bus, rec, scope,
                                  self._on_freeze_done(rec, pid, scope),
                                  fallback_pid=pid)
        for pid in sorted(rec.pids):
            if pid not in rec.scopes:
                self._stop_pid(rec, pid)
        if not rec.pids and not rec.scopes and bus is not None:
            for unit in sorted(rec.dbus_units):
                self._dispatch_freeze(bus, rec, unit,
                                      self._on_dbus_freeze_done(rec, unit))
        rec.frozen = bool(rec.frozen_units or rec.stopped)
        return rec.frozen

    def _dispatch_freeze(self, bus: object, rec: _TrackedApp, unit: str,
                         callback: object,
                         fallback_pid: int | None = None) -> None:
        rec.frozen_units.add(unit)
        self._remember_frozen_units([unit])
        try:
            bus.call(SYSTEMD_DEST, SYSTEMD_PATH, SYSTEMD_MANAGER,
                     'FreezeUnit', GLib.Variant('(s)', (unit,)), None,
                     Gio.DBusCallFlags.NONE, -1, None, callback)
        except Exception as error:
            _log.info('freeze %s failed: %s', unit, error)
            rec.frozen_units.discard(unit)
            self._forget_frozen_units([unit])
            if fallback_pid is not None:
                self._stop_pid(rec, fallback_pid)

    def _settle_frozen(self, rec: _TrackedApp) -> None:
        if not rec.frozen_units and not rec.stopped:
            rec.frozen = False

    def _on_freeze_done(self, rec: _TrackedApp, pid: int,
                        scope: str) -> object:
        def _done(conn: object, res: object) -> None:
            try:
                conn.call_finish(res)
            except Exception as error:
                rec.frozen_units.discard(scope)
                self._forget_frozen_units([scope])
                if _unit_gone(error):
                    # The scope is gone: the app exited. Its pid may
                    # already belong to something else, so never stop it.
                    _log.info('FreezeUnit %s: app exited', scope)
                    rec.scopes.pop(pid, None)
                    rec.pids.discard(pid)
                    rec.starttimes.pop(pid, None)
                else:
                    _log.info('FreezeUnit failed, stopping pid %d: %s',
                              pid, error)
                    self._stop_pid(rec, pid)
                self._settle_frozen(rec)
        return _done

    def _on_dbus_freeze_done(self, rec: _TrackedApp, unit: str) -> object:
        def _done(conn: object, res: object) -> None:
            try:
                conn.call_finish(res)
            except Exception as error:
                _log.info('FreezeUnit %s failed: %s', unit, error)
                rec.frozen_units.discard(unit)
                self._forget_frozen_units([unit])
                rec.dbus_units.discard(unit)
                self._settle_frozen(rec)
        return _done

    # -- pid safety ------------------------------------------------------------

    def _pid_alive(self, rec: _TrackedApp, pid: int) -> bool:
        """The pid still is the process tracked: same start time as at
        track(). Unknown start times never verify."""
        recorded = rec.starttimes.get(pid)
        if recorded is None:
            return False
        return self._starttime(pid) == recorded

    def _drop_pid(self, rec: _TrackedApp, pid: int) -> None:
        rec.pids.discard(pid)
        rec.starttimes.pop(pid, None)
        if pid in rec.stopped:
            rec.stopped.discard(pid)
            self._forget_stopped([pid])

    def _prune_pids(self, rec: _TrackedApp) -> int:
        dead = [pid for pid in sorted(rec.pids)
                if not self._pid_alive(rec, pid)]
        for pid in dead:
            self._drop_pid(rec, pid)
        return len(dead)

    def _signal_pid(self, rec: _TrackedApp, pid: int, sig: int) -> bool:
        if pid <= 0 or not self._pid_alive(rec, pid):
            self._drop_pid(rec, pid)
            return False
        try:
            os.kill(pid, sig)
        except OSError:
            self._drop_pid(rec, pid)
            return False
        return True

    def _stop_pid(self, rec: _TrackedApp, pid: int) -> bool:
        if not self._signal_pid(rec, pid, signal.SIGSTOP):
            return False
        rec.stopped.add(pid)
        self._record_stopped(pid, rec.starttimes.get(pid))
        return True

    # -- ledgers ($XDG_RUNTIME_DIR, thawed at the next start) ------------------

    def _frozen_units_path(self) -> str:
        if self._frozen_units_override:
            return self._frozen_units_override
        return frozen_units_file_path()

    def _load_frozen_units(self) -> list[str]:
        return [unit for unit in _read_json_list(self._frozen_units_path())
                if isinstance(unit, str) and _LEDGER_UNIT_RE.match(unit)]

    def _save_frozen_units(self, units: list[str]) -> None:
        path = self._frozen_units_path()
        try:
            if units:
                _write_private_json(path, units)
            else:
                os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as error:
            _log.info('standby frozen-units ledger write failed: %s', error)

    def _remember_frozen_units(self, units: object) -> None:
        current = self._load_frozen_units()
        added = [unit for unit in units if unit not in current]
        if added:
            self._save_frozen_units(current + added)

    def _forget_frozen_units(self, units: object) -> None:
        gone = set(units)
        current = self._load_frozen_units()
        kept = [unit for unit in current if unit not in gone]
        if len(kept) != len(current):
            self._save_frozen_units(kept)

    def _thaw_ledger_units(self) -> list[str]:
        """Thaw what a previous shell froze and never thawed (crash, deploy,
        SIGUSR1 restart). systemd keeps a frozen unit frozen forever."""
        units = self._load_frozen_units()
        if not units or self._force_degraded:
            return []
        bus = self._bus()
        if bus is None:
            return []
        for unit in units:
            try:
                bus.call(SYSTEMD_DEST, SYSTEMD_PATH, SYSTEMD_MANAGER,
                         'ThawUnit', GLib.Variant('(s)', (unit,)), None,
                         Gio.DBusCallFlags.NONE, -1, None,
                         self._on_unit_done('ThawUnit', unit))
            except Exception as error:
                _log.info('thaw %s failed: %s', unit, error)
        self._save_frozen_units([])
        return units

    def _stopped_path(self) -> str:
        if self._stopped_file_override:
            return self._stopped_file_override
        return stopped_file_path()

    def _load_stopped(self) -> list:
        return _read_json_list(self._stopped_path())

    def _write_stopped(self, entries: list) -> None:
        try:
            _write_private_json(self._stopped_path(), entries)
        except OSError as error:
            _log.info('standby stopped ledger write failed: %s', error)

    def _record_stopped(self, pid: int, starttime: int | None = None) -> None:
        if starttime is None:
            starttime = self._starttime(pid)
        if starttime is None:
            return
        entries = [entry for entry in self._load_stopped()
                   if isinstance(entry, dict) and entry.get('pid') != pid]
        entries.append({'pid': int(pid), 'starttime': starttime})
        self._write_stopped(entries)

    def _forget_stopped(self, pids: object) -> None:
        try:
            gone = {int(pid) for pid in pids}
        except (TypeError, ValueError):
            return
        entries = self._load_stopped()
        if not entries:
            return
        kept = [entry for entry in entries
                if not (isinstance(entry, dict)
                        and entry.get('pid') in gone)]
        if len(kept) == len(entries):
            return
        self._write_stopped(kept)

    def _clear_stopped(self) -> None:
        try:
            os.unlink(self._stopped_path())
        except OSError:
            pass

    def _restore_stopped_file(self) -> list[int]:
        entries = self._load_stopped()
        self._clear_stopped()
        conted: list[int] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                pid = int(entry.get('pid'))
            except (TypeError, ValueError):
                continue
            if pid <= 0:
                continue
            try:
                recorded = entry.get('starttime')
                recorded_int = int(recorded) if recorded is not None else None
            except (TypeError, ValueError):
                continue
            if recorded_int is None:
                continue
            current = self._starttime(pid)
            if current is None or current != recorded_int:
                continue
            try:
                os.kill(pid, signal.SIGCONT)
            except OSError:
                continue
            conted.append(pid)
        return conted

    @property
    def frozen_apps(self) -> list[str]:
        return [rec.app_id for rec in self._apps.values() if rec.frozen]

    # -- activation observation -------------------------------------------------

    def attach_manager(self, manager: object) -> None:
        self._manager = manager
        self._manager_ready = True
        self._subscribed = False

    def _manager_conn(self) -> object | None:
        if not self._manager_ready:
            self._manager_ready = True
            try:
                from toplevel_manager import ToplevelManager
                self._manager = ToplevelManager()
            except Exception as error:
                _log.info('standby toplevel manager unavailable: %s', error)
                self._manager = None
        return self._manager

    def _observe_activations(self) -> None:
        mgr = self._manager_conn()
        if mgr is None:
            self._observable = False
            return
        activated_signal = getattr(mgr, 'on_activated_changed', None)
        if callable(activated_signal):
            if not self._subscribed:
                try:
                    activated_signal(self._handle_manager_activated)
                    self._subscribed = True
                except Exception as error:
                    _log.info('standby activation signal refused: %s', error)
            self._observable = True
        try:
            toplevels = list(mgr.list())
        except Exception:
            self._observable = False
            return
        if not toplevels:
            # No windows to learn from: a previous observation stands, but
            # an unknown state never becomes observable on an empty list.
            return
        if any(hasattr(top, 'activated') for top in toplevels):
            self._observable = True
            for top in toplevels:
                try:
                    active = bool(getattr(top, 'activated', False))
                except Exception:
                    continue
                if active:
                    app_id = getattr(top, 'app_id', None)
                    if isinstance(app_id, str) and app_id:
                        self.note_activated(app_id)
        else:
            self._observable = False

    def _handle_manager_activated(self, *args: object, **kwargs: object) -> None:
        """Activation-signal callback (f_usage lane owns the exact signature):
        accept an app id string, a toplevel with .app_id, or a handle
        resolvable through the attached manager's list()."""
        app_id = kwargs.get('app_id')
        if not isinstance(app_id, str):
            app_id = None
            for arg in args:
                if isinstance(arg, str) and arg:
                    app_id = arg
                    break
                inner = getattr(arg, 'app_id', None)
                if isinstance(inner, str) and inner:
                    app_id = inner
                    break
        if app_id is None and args and self._manager is not None:
            try:
                for top in self._manager.list():
                    if getattr(top, 'handle', None) == args[0]:
                        cand = getattr(top, 'app_id', None)
                        if isinstance(cand, str) and cand:
                            app_id = cand
                        break
            except Exception:
                pass
        if app_id:
            try:
                self.note_activated(app_id)
            except Exception:
                pass

    # -- saver ------------------------------------------------------------------

    def _update_saver_auto(self) -> None:
        if not self._saver_auto_enabled:
            self._auto_saver = False
            self._was_low = None
            return
        pct, charging = self._battery
        if pct is None:
            return
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            return
        threshold = saver_auto_threshold(self._config)
        clear = max(SAVER_AUTO_CLEAR_PCT, threshold + 5)
        low = pct <= threshold
        if charging:
            # Unplugging while still low is a fresh low edge: re-arm.
            self._auto_saver = False
            self._was_low = False
            return
        if pct > clear:
            self._auto_saver = False
        elif low and not self._was_low:
            self._auto_saver = True
        self._was_low = low

    # -- sweep ------------------------------------------------------------------

    def sweep(self, now: float | None = None) -> list[str]:
        """Freeze idle candidates; returns the app ids frozen by this pass."""
        now = self._now(now)
        self._observe_activations()
        self._update_saver_auto()
        self._maybe_notify_saver()
        if not self._observable:
            return []
        saver = self.saver_effective
        minutes = saver_effective_minutes(self._standby_minutes, saver)
        blank_s = saver_blank_seconds(saver)
        exempt = self._standby_exempt
        exempt_keys = {self._key(e) for e in exempt or ()}
        mpris = {self._key(a) for a in self._mpris_playing}
        frozen_now: list[str] = []
        for key in list(self._apps):
            rec = self._apps[key]
            dead = self._prune_pids(rec)
            empty = (not rec.pids and not rec.scopes and not rec.dbus_units
                     and not rec.stopped and not rec.frozen)
            if empty and (dead or rec.last_active is None):
                del self._apps[key]
                continue
            if rec.frozen:
                continue
            if key in exempt_keys or self._rec_exempt(rec):
                continue
            units = set(rec.scopes.values()) | rec.dbus_units
            if should_standby(
                    rec.app_id, now=now, last_active=rec.last_active,
                    screen_blanked=self._blanked, blank_since=self._blank_since,
                    standby_minutes=minutes, blank_seconds=blank_s,
                    mpris_playing=(key in mpris
                                   or bool(rec.pids & self._mpris_pids)
                                   or bool(units & self._mpris_units)),
                    audio_busy=(not self._audio_known
                                or bool(rec.pids & self._audio_pids)
                                or bool(units & self._audio_units)),
                    is_telephony=is_telephony_app(rec.app_id) or bool(
                        rec.pids & self._calls_pids),
                    is_keyboard=is_keyboard_app(rec.app_id),
                    exempt_ids=exempt,
                    last_notif_action=rec.last_notif_action):
                if self._freeze(rec):
                    frozen_now.append(rec.app_id)
        return frozen_now

    def _rec_exempt(self, rec: _TrackedApp) -> bool:
        """Built-in and category exemptions over every id the app goes by
        (desktop id, toplevel app_id)."""
        for name in {rec.app_id} | rec.names:
            if is_builtin_exempt(name) or is_keyboard_app(name):
                return True
            categories = self._desktop_info(name).get('categories') or ()
            if any(str(cat).casefold() in EXEMPT_CATEGORIES
                   for cat in categories):
                return True
        return False

    def _standby_on(self) -> bool:
        return saver_effective_minutes(self._standby_minutes, False) > 0

    def _sweep_tick(self) -> bool:
        try:
            if self._standby_on():
                self.sweep()
            else:
                # Standby off: nothing to freeze, only the battery saver.
                self._update_saver_auto()
                self._maybe_notify_saver()
        except Exception as error:
            _log.warning('standby sweep failed: %s', error)
        return True

    def _refresh_tick(self) -> bool:
        """Standby off spawns no pw-dump and asks no media players; the
        automatic battery saver still needs the battery level."""
        try:
            if self._standby_on():
                self.refresh_exemptions_async()
            elif self._saver_auto_enabled:
                self.refresh_battery_async()
        except Exception as error:
            _log.warning('standby refresh failed: %s', error)
        return True

    def refresh_battery_async(self) -> object | None:
        def _work() -> None:
            batt: tuple[float | None, bool] = (None, False)
            try:
                sysbus = self._system_bus_factory()
            except Exception:
                sysbus = None
            if sysbus is not None:
                try:
                    batt = query_battery(sysbus)
                except Exception:
                    pass
            try:
                GLib.idle_add(self._apply_battery, batt)
            except Exception as error:
                _log.info('standby battery post failed: %s', error)
        try:
            thread = threading.Thread(target=_work, daemon=True,
                                      name='xx-wm-standby-battery')
            thread.start()
            return thread
        except Exception as error:
            _log.info('standby battery thread failed: %s', error)
            return None

    def _apply_battery(self, batt: tuple) -> bool:
        self._battery = batt
        return False

    # -- exemption refresh (worker thread, never the main loop) -----------------

    def refresh_exemptions_async(self) -> object | None:
        frozen_pids = {pid for rec in self._apps.values() if rec.frozen
                       for pid in rec.pids}
        proc_root = self._proc_root

        def _work() -> None:
            players: list[dict] = []
            calls_pid: int | None = None
            audio: set[int] | None = None
            batt: tuple[float | None, bool] = (None, False)
            try:
                bus = self._session_bus_factory()
            except Exception:
                bus = None
            if bus is not None:
                try:
                    players = query_mpris_players(bus, skip_pids=frozen_pids)
                except Exception:
                    pass
                try:
                    calls_pid = query_calls_owner_pid(bus)
                except Exception:
                    pass
            try:
                audio = audio_pids_or_unknown(self._pw_dump())
            except Exception:
                audio = None
            mpris = set()
            for player in players:
                entry = player.get('entry')
                if player.get('playing') and entry:
                    mpris.add(entry if entry.endswith('.desktop')
                              else entry + '.desktop')
            playing_pids, playing_units = expand_pids(
                [p['pid'] for p in players
                 if p.get('playing') and p.get('pid')], proc_root)
            player_pids, player_units = expand_pids(
                [p['pid'] for p in players if p.get('pid')], proc_root)
            audio_units: set[str] = set()
            if audio is not None:
                audio, audio_units = expand_pids(audio, proc_root)
            calls_pids, _calls_units = expand_pids(
                [calls_pid] if calls_pid else (), proc_root)
            extra = {
                'mpris_pids': playing_pids, 'mpris_units': playing_units,
                'player_pids': player_pids, 'player_units': player_units,
                'player_entries': {p['entry'] for p in players
                                   if p.get('entry')},
                'audio_units': audio_units, 'calls_pids': calls_pids,
            }
            try:
                sysbus = self._system_bus_factory()
            except Exception:
                sysbus = None
            if sysbus is not None:
                try:
                    batt = query_battery(sysbus)
                except Exception:
                    pass
            try:
                GLib.idle_add(self._apply_exemptions, mpris, calls_pid,
                              audio, batt, extra)
            except Exception as error:
                _log.info('standby refresh post failed: %s', error)
        try:
            thread = threading.Thread(target=_work, daemon=True,
                                      name='xx-wm-standby-exempt')
            thread.start()
            return thread
        except Exception as error:
            _log.info('standby refresh thread failed: %s', error)
            return None

    def _apply_exemptions(self, mpris: set[str], calls_pid: int | None,
                          audio: set[int] | None, batt: tuple,
                          extra: dict | None = None) -> bool:
        """Main-loop side of the refresh. audio None means the PipeWire
        probe failed: unknown, so nothing freezes until it answers."""
        try:
            extra = extra or {}
            self._mpris_playing = set(mpris or ())
            self._mpris_pids = set(extra.get('mpris_pids') or ())
            self._mpris_units = set(extra.get('mpris_units') or ())
            self._player_pids = set(extra.get('player_pids') or ())
            self._player_units = set(extra.get('player_units') or ())
            self._player_keys = {self._key(e) for e in
                                 extra.get('player_entries') or ()}
            self._calls_pids = {calls_pid} if calls_pid else set()
            self._calls_pids |= set(extra.get('calls_pids') or ())
            if audio is None:
                self._audio_known = False
            else:
                self._audio_known = True
                self._audio_pids = set(audio)
                self._audio_units = set(extra.get('audio_units') or ())
            self._battery = batt
        except Exception as error:
            _log.info('standby apply exemptions failed: %s', error)
        return False

    def start(self) -> None:
        self.stop()
        try:
            self._restore_stopped_file()
        except Exception as error:
            _log.info('standby stopped restore failed: %s', error)
        try:
            self._thaw_ledger_units()
        except Exception as error:
            _log.info('standby frozen-units thaw failed: %s', error)
        try:
            self._readopt_scopes_async()
        except Exception as error:
            _log.info('standby scope re-adopt failed: %s', error)
        try:
            from ticker import get_ticker
            self._refresh_tick()
            # Blank-screen freezing is the point of standby: these keep
            # running while the display is dark.
            self._sweep_src = get_ticker().add(
                self._sweep_tick, SWEEP_SECONDS, while_blank=True)
            self._refresh_src = get_ticker().add(
                self._refresh_tick, EXEMPT_REFRESH_SECONDS, while_blank=True)
        except Exception as error:
            _log.info('standby start failed: %s', error)

    def _readopt_scopes_async(self) -> None:
        """Thaw leftover frozen scopes after a shell restart/crash and keep
        managing them. systemd keeps a frozen unit frozen until ThawUnit, so
        without this the apps look dead until reboot. Simple version: thaw
        every matching scope (ThawUnit on a running unit is harmless)."""
        if self._force_degraded:
            return
        bus = self._bus()
        if bus is None:
            return
        params = GLib.Variant('(asas)', ([], [STANDBY_SCOPE_PATTERN]))
        bus.call(SYSTEMD_DEST, SYSTEMD_PATH, SYSTEMD_MANAGER,
                 'ListUnitsByPatterns', params, None,
                 Gio.DBusCallFlags.NONE, -1, None, self._on_readopt_list)

    def _on_readopt_list(self, conn: object, res: object) -> None:
        try:
            reply = conn.call_finish(res)
        except Exception as error:
            _log.info('standby scope list failed: %s', error)
            return
        try:
            units = reply.unpack()[0]
        except Exception as error:
            _log.info('standby scope list unpack failed: %s', error)
            return
        now = self._now()
        for entry in units or ():
            name = _unit_name(entry)
            if name is None:
                continue
            try:
                conn.call(SYSTEMD_DEST, SYSTEMD_PATH, SYSTEMD_MANAGER,
                          'ThawUnit', GLib.Variant('(s)', (name,)), None,
                          Gio.DBusCallFlags.NONE, -1, None,
                          self._on_unit_done('ThawUnit', name))
            except Exception as error:
                _log.info('thaw %s failed: %s', name, error)
            app_id, pid = parse_standby_scope(name)
            if app_id is None:
                continue
            self._learn_aliases(app_id)
            rec = self._record(app_id)
            if rec is None:
                continue
            if pid is not None:
                rec.scopes[pid] = name
                started = self._readopt_starttime(name, pid)
                if started is not None:
                    rec.pids.add(pid)
                    rec.starttimes[pid] = started
            else:
                rec.dbus_units.add(name)
            rec.last_active = now
            rec.frozen = False

    def _readopt_starttime(self, scope: str, pid: int) -> int | None:
        """A pid named in a leftover scope is only adopted for signals when
        it still lives in that scope (it may have been recycled since)."""
        try:
            from app_permissions import find_scope_cgroup
            if not find_scope_cgroup(scope, pid, proc_root=self._proc_root,
                                     cgroup_root=self._cgroup_root):
                return None
        except Exception:
            return None
        return self._starttime(pid)

    def shutdown_thaw(self) -> None:
        """Synchronous best-effort thaw of everything tracked, bounded to
        ~1 s total. Called from the application's quit path so no frozen
        scope or SIGSTOP'd process is left behind when the shell exits."""
        try:
            deadline = time.monotonic() + SHUTDOWN_BUDGET_S
        except Exception:
            deadline = 0.0
        bus = None
        if not self._force_degraded:
            try:
                bus = self._bus()
            except Exception as error:
                _log.info('standby shutdown bus unavailable: %s', error)
                bus = None
        thawed: set[str] = set()
        for rec in list(self._apps.values()):
            for unit in sorted(set(rec.scopes.values()) | set(rec.dbus_units)
                               | set(rec.frozen_units)):
                try:
                    remaining = deadline - time.monotonic()
                except Exception:
                    remaining = 0.0
                if remaining <= 0:
                    break
                if bus is None:
                    continue
                try:
                    bus.call_sync(
                        SYSTEMD_DEST, SYSTEMD_PATH, SYSTEMD_MANAGER,
                        'ThawUnit', GLib.Variant('(s)', (unit,)),
                        GLib.VariantType('(o)'),
                        Gio.DBusCallFlags.NONE,
                        max(1, min(SHUTDOWN_UNIT_TIMEOUT_MS,
                                   int(remaining * 1000))), None)
                    thawed.add(unit)
                except Exception as error:
                    if _unit_gone(error):
                        thawed.add(unit)
            try:
                expired = deadline - time.monotonic() <= 0
            except Exception:
                expired = True
            for pid in sorted(rec.stopped):
                if expired:
                    break
                self._signal_pid(rec, pid, signal.SIGCONT)
            rec.stopped.clear()
            rec.frozen_units.clear()
            rec.frozen = False
        try:
            self._clear_stopped()
        except Exception:
            pass
        try:
            self._forget_frozen_units(thawed)
        except Exception:
            pass

    def stop(self) -> None:
        for attr in ('_sweep_src', '_refresh_src'):
            src = getattr(self, attr)
            if src is not None:
                try:
                    from ticker import get_ticker
                    get_ticker().remove(src)
                except Exception:
                    pass
                setattr(self, attr, None)

    # -- screen + notifications ---------------------------------------------------

    def note_screen_blank(self, blanked: bool, now: float | None = None) -> None:
        now = self._now(now)
        self._blanked = bool(blanked)
        self._blank_since = now if blanked else None

    def note_notification_shown(self, notif_id: int, app_id: str) -> None:
        try:
            if app_id:
                self._notif_apps[int(notif_id)] = str(app_id)
        except (TypeError, ValueError):
            return
        while len(self._notif_apps) > 512:
            self._notif_apps.pop(next(iter(self._notif_apps)))

    def note_notification_closed(self, notif_id: int) -> None:
        try:
            self._notif_apps.pop(int(notif_id), None)
        except (TypeError, ValueError):
            pass

    def note_notification_action(self, app_id: str,
                                 now: float | None = None) -> None:
        """A notification action fired for this app: exempt it for 60 s and
        thaw it so it can respond."""
        rec = self._record(app_id)
        if rec is None:
            return
        rec.last_notif_action = self._now(now)
        self._thaw(rec)

    def attach_daemon(self, daemon: object) -> None:
        """Wrap the notification daemon's invoke_action so action taps map
        back to the owning app via the shown-notification registry."""
        if daemon is None or getattr(daemon, '_standby_wrapped', False):
            return
        original = getattr(daemon, 'invoke_action', None)
        if not callable(original):
            return
        controller = self

        def _invoke(notif_id: int, action_key: str) -> object:
            try:
                app_id = controller._notif_apps.get(int(notif_id))
                if app_id:
                    controller.note_notification_action(app_id)
            except Exception:
                pass
            return original(notif_id, action_key)

        try:
            daemon.invoke_action = _invoke
            daemon._standby_wrapped = True
        except Exception as error:
            _log.info('standby daemon wrap failed: %s', error)


_default_controller: StandbyController | None = None


def get_standby(config: object = None) -> StandbyController:
    """Process-wide controller (the quick-settings tile reaches it here;
    main.py replaces it with the configured one via configure_standby)."""
    global _default_controller
    if _default_controller is None:
        _default_controller = StandbyController(config=config)
    return _default_controller


def current_standby() -> StandbyController | None:
    """The configured controller, or None before the shell set one up
    (never creates one: callers fall back to acting directly)."""
    return _default_controller


def configure_standby(config: object = None, **kwargs: object) -> StandbyController:
    global _default_controller
    _default_controller = StandbyController(config=config, **kwargs)
    return _default_controller


def install_saver_check_gates(shell: object, saver: object) -> None:
    """Pause automatic weather/update checks while battery saver is on.

    Manual checks still run: weather tap-refresh passes force=True and the
    update button calls check_now(), neither of which is gated."""
    checker = getattr(shell, '_update_checker', None)
    if checker is not None and not getattr(checker, '_standby_gated', False):
        try:
            original = checker.maybe_check

            def _gated_check(_original: object = original) -> None:
                if saver.saver_effective:
                    return
                return _original()

            checker.maybe_check = _gated_check
            checker._standby_gated = True
        except Exception as error:
            _log.info('standby update gate failed: %s', error)
    weather = getattr(shell, '_weather', None)
    if weather is not None and not getattr(weather, '_standby_gated', False):
        try:
            original = weather.refresh

            def _gated_refresh(on_done: object, force: bool = False,
                               _original: object = original) -> None:
                if not force and saver.saver_effective:
                    return
                return _original(on_done, force=force)

            weather.refresh = _gated_refresh
            weather._standby_gated = True
        except Exception as error:
            _log.info('standby weather gate failed: %s', error)

