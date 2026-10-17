"""
Backup and restore — versioned JSON export of the shell configuration.

Scope (design.md "Backup / restore"): home slots, folders + membership,
pins, app rename labels, widget config + tap actions, theme, hidden apps,
gestures, prefs (sounds, DnD, Focus, lock screen, weather coords, search).
Excluded: PIN hash (security), escalating-lockout resume point and the
Smart Lock 4-hour PIN clock (device-local transients — a restore must
not clear a live lockout), launch counts (noise), active mutes and
focus breaks (transient), custom font files (only the family name
travels).
Restore validates the entire payload first and never writes on invalid input.
The export below is allowlist-built, so excluded keys can never leak in —
_EXCLUDED_KEYS pins that contract for tests.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

_EXCLUDED_KEYS = ('pin_hash', 'lock_screen_lockout')

_BACKUP_GLOB = 'xx-wm-backup-*.json'
_BACKUP_FILENAME_RE = re.compile(r'^xx-wm-backup-(\d{8})_(\d{6})\.json$')
_MAX_RESTORE_CHOICES = 10


def parse_backup_timestamp(filename: str) -> datetime | None:
    """Timestamp embedded in an export filename, or None when it is absent.

    Export names carry %Y%m%d_%H%M%S, so lexicographic order is chronological
    — but sorting parses the stamp explicitly instead of trusting that."""
    match = _BACKUP_FILENAME_RE.match(Path(filename).name)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1) + match.group(2), '%Y%m%d%H%M%S')
    except ValueError:
        return None


def backup_label(path: str | Path) -> str:
    """Menu label for a backup file: the date/time from its filename."""
    stamp = parse_backup_timestamp(str(path))
    if stamp is None:
        return Path(path).stem
    return stamp.strftime('%Y-%m-%d %H:%M')


def discover_backups(search_dirs: list[Path] | None = None,
                     limit: int = _MAX_RESTORE_CHOICES) -> list[Path]:
    """Backup files in ~ and ~/Documents, newest first, capped at `limit`.

    The restore menu runs on the layer-shell TOP layer, where a file chooser
    cannot open — so restore picks from this list instead of a dialog."""
    if search_dirs is None:
        home = Path.home()
        search_dirs = [home / 'Documents', home]
    found: list[Path] = []
    seen: set[str] = set()
    for directory in search_dirs:
        try:
            candidates = sorted(directory.glob(_BACKUP_GLOB))
        except OSError:
            continue
        for path in candidates:
            key = str(path.resolve() if path.exists() else path.absolute())
            if key not in seen:
                seen.add(key)
                found.append(path)
    found.sort(key=lambda p: (parse_backup_timestamp(p.name)
                              or datetime.min, p.name),
               reverse=True)
    return found[:limit]


def backup_export_dir() -> Path:
    """Exports land in ~/Documents when it exists, else directly in ~."""
    docs = Path.home() / 'Documents'
    return docs if docs.is_dir() else Path.home()

_BOOL_KEYS = (
    'prefer_dark',
    'home_to_recents',
    'search_auto_launch',
    'sound_ringtone',
    'sound_notifications',
    'dnd_enabled',
    'focus_enabled',
    'night_light',
    'night_light_schedule',
)

_LIST_KEYS = (
    'hidden_apps',
    'dnd_schedules',
    'dnd_starred_numbers',
    'focus_apps',
    'focus_schedules',
    'smart_lock_bt_devices',
    'smart_lock_wifi_ssids',
)


def export_backup(config) -> dict:
    backup = {
        'version': 1,
        'home_slots': config.home_slots,
        'app_labels': config.data.get('app_labels', {}),
        'pinned': config.pinned,
        'widgets': config.data.get('widgets', {}),
        'theme': config.data.get('theme', 'amoled'),
        'font': config.data.get('font', 'jetbrains-mono-nerd'),
        'text_size_scale': config.text_size_scale,
        'home_alignment': config.home_alignment,
        'drawer_sort': config.drawer_sort,
        'appearance_mode': config.appearance_mode,
        'auto_lock_timeout': config.auto_lock_timeout,
        'lock_screen_notifications': config.lock_screen_notifications,
        # Medical ID is user data, not a secret: it travels with the
        # backup, unlike pin_hash and the lockout resume point.
        'lock_screen_medical_id': config.lock_screen_medical_id,
        'medical_id': config.medical_id,
        'charge_limit': config.data.get('charge_limit'),
        'app_time_limits': config.data.get('app_time_limits', {}),
        'app_limit_ignored': config.data.get('app_limit_ignored', {}),
        'weather_lat': config.data.get('weather_lat'),
        'weather_lon': config.data.get('weather_lon'),
        'weather_temp_unit': config.weather_temp_unit,
        'weather_auto_location': config.weather_auto_location,
        'clock_24h': config.clock_24h,
        'keyboard_layout': config.data.get('keyboard_layout', 'us'),
        'night_light_temp': config.data.get('night_light_temp', 2200),
    }
    for key in _BOOL_KEYS:
        backup[key] = bool(config.data.get(key, False))
    for key in _LIST_KEYS:
        val = config.data.get(key, [])
        backup[key] = val if isinstance(val, list) else []

    if config.data.get('theme') == 'custom' and config.custom_background:
        backup['custom_background'] = config.custom_background
    if config.data.get('font') == 'custom':
        backup['custom_font_family'] = config.data.get('custom_font_family', '')

    try:
        from gesture_config import GestureConfig
        backup['gestures'] = dict(GestureConfig().all())
    except Exception:
        pass

    return backup


def validate_backup(payload: dict) -> tuple[bool, str | None]:
    """Validate the entire payload; restore only runs on a clean pass."""
    if not isinstance(payload, dict):
        return False, 'Payload must be a dictionary'

    if payload.get('version') != 1:
        return False, f'Unsupported version: {payload.get("version")}'

    home_slots = payload.get('home_slots', [])
    if not isinstance(home_slots, list):
        return False, 'home_slots must be a list'
    if len(home_slots) > 8:
        return False, 'home_slots must have at most 8 entries'
    for i, slot in enumerate(home_slots):
        if not isinstance(slot, dict):
            return False, f'home_slots[{i}] must be a dict'
        if slot.get('type') not in ('app', 'folder'):
            return False, f'home_slots[{i}].type must be app or folder'

    if not isinstance(payload.get('app_labels', {}), dict):
        return False, 'app_labels must be a dict'

    widgets = payload.get('widgets', {})
    if not isinstance(widgets, dict):
        return False, 'widgets must be a dict'
    for name, conf in widgets.items():
        if not isinstance(conf, dict):
            return False, f'widgets[{name}] must be a dict'

    if not isinstance(payload.get('theme', ''), str):
        return False, 'theme must be a string'
    if payload.get('theme') == 'custom':
        custom_bg = payload.get('custom_background')
        if not isinstance(custom_bg, str) or not re.match(r'^#[0-9a-fA-F]{6}$', custom_bg):
            return False, 'custom_background must be #RRGGBB when theme is custom'

    if not isinstance(payload.get('font', ''), str):
        return False, 'font must be a string'

    if 'custom_font_family' in payload:
        family = payload['custom_font_family']
        if not isinstance(family, str):
            return False, 'custom_font_family must be a string'
        from font_theme import sanitize_font_family
        payload['custom_font_family'] = sanitize_font_family(family)

    try:
        scale = float(payload.get('text_size_scale', 1.0))
        if not (0.5 <= scale <= 2.0):
            return False, 'text_size_scale must be between 0.5 and 2.0'
    except (TypeError, ValueError):
        return False, 'text_size_scale must be a number'

    try:
        if int(payload.get('auto_lock_timeout', 120)) < 0:
            return False, 'auto_lock_timeout must be non-negative'
    except (TypeError, ValueError):
        return False, 'auto_lock_timeout must be an integer'

    if payload.get('home_alignment', 'center') not in ('left', 'center', 'right'):
        return False, 'home_alignment must be left/center/right'

    if payload.get('drawer_sort', 'default') not in ('default', 'az', 'install'):
        return False, 'drawer_sort must be default/az/install'
    if payload.get('appearance_mode', 'theme') not in (
            'theme', 'light', 'dark', 'system'):
        return False, 'appearance_mode must be theme/light/dark/system'

    if payload.get('lock_screen_notifications', 'summary') not in ('summary', 'count', 'off'):
        return False, 'lock_screen_notifications must be summary/count/off'

    if 'lock_screen_medical_id' in payload and not isinstance(
            payload['lock_screen_medical_id'], bool):
        return False, 'lock_screen_medical_id must be a boolean'

    if 'charge_limit' in payload and payload['charge_limit'] is not None:
        try:
            number = int(payload['charge_limit'])
        except (TypeError, ValueError):
            return False, 'charge_limit must be a number or null'
        if not 50 <= number <= 100:
            return False, 'charge_limit must be between 50 and 100'

    if 'keyboard_layout' in payload and not isinstance(
            payload['keyboard_layout'], str):
        return False, 'keyboard_layout must be a string'

    if 'night_light_temp' in payload:
        try:
            temp = int(payload['night_light_temp'])
        except (TypeError, ValueError):
            return False, 'night_light_temp must be a number'
        if not 2200 <= temp <= 6500:
            return False, 'night_light_temp must be between 2200 and 6500'

    for key in ('app_time_limits', 'app_limit_ignored'):
        if key in payload and not isinstance(payload[key], dict):
            return False, f'{key} must be a dict'

    if 'medical_id' in payload:
        from config import _MEDICAL_ID_CONTACT_KEYS, _MEDICAL_ID_STRING_KEYS
        medical = payload['medical_id']
        if not isinstance(medical, dict):
            return False, 'medical_id must be a dict'
        for key in _MEDICAL_ID_STRING_KEYS:
            if key in medical and not isinstance(medical[key], str):
                return False, f'medical_id.{key} must be a string'
        if 'organ_donor' in medical and not isinstance(medical['organ_donor'], bool):
            return False, 'medical_id.organ_donor must be a boolean'
        contacts = medical.get('emergency_contacts')
        if 'emergency_contacts' in medical:
            if not isinstance(contacts, list):
                return False, 'medical_id.emergency_contacts must be a list'
            for i, entry in enumerate(contacts):
                if not isinstance(entry, dict):
                    return False, f'medical_id.emergency_contacts[{i}] must be a dict'
                for key in _MEDICAL_ID_CONTACT_KEYS:
                    if key in entry and not isinstance(entry[key], str):
                        return False, (
                            f'medical_id.emergency_contacts[{i}].{key} must be a string'
                        )

    for key in _BOOL_KEYS:
        if key in payload and not isinstance(payload[key], bool):
            return False, f'{key} must be a boolean'

    for key in _LIST_KEYS + ('pinned',):
        if key in payload and not isinstance(payload[key], list):
            return False, f'{key} must be a list'

    for key in ('weather_lat', 'weather_lon'):
        if payload.get(key) is not None:
            try:
                float(payload[key])
            except (TypeError, ValueError):
                return False, f'{key} must be a number or null'

    if payload.get('weather_temp_unit', 'fahrenheit') not in (
            'fahrenheit', 'celsius'):
        return False, 'weather_temp_unit must be fahrenheit/celsius'

    if 'clock_24h' in payload and not (
            payload['clock_24h'] is None
            or isinstance(payload['clock_24h'], bool)):
        return False, 'clock_24h must be a boolean or null'

    if 'weather_auto_location' in payload and not isinstance(
            payload['weather_auto_location'], bool):
        return False, 'weather_auto_location must be a boolean'

    gestures = payload.get('gestures', {})
    if not isinstance(gestures, dict):
        return False, 'gestures must be a dict'
    # gesture_config's own slot/value rule: actions are valid everywhere,
    # IPC verbs (gesture_bindings lisgd slots) only on the system-level slots.
    from gesture_config import _DEFAULTS, _is_valid_value
    for key, action in gestures.items():
        if key not in _DEFAULTS:
            return False, f'unknown gesture: {key}'
        if not isinstance(action, str) or not _is_valid_value(key, action):
            return False, f'unknown gesture action: {action}'

    return True, None


def restore_backup(config, payload: dict) -> bool:
    """Apply a validated payload: one config write plus one gestures write.
    Invalid payload → zero writes."""
    is_valid, _error = validate_backup(payload)
    if not is_valid:
        return False

    data = config.data
    if isinstance(payload.get('medical_id'), dict):
        from config import sanitize_medical_id
        data['medical_id'] = sanitize_medical_id(payload['medical_id'])
    if isinstance(payload.get('lock_screen_medical_id'), bool):
        data['lock_screen_medical_id'] = payload['lock_screen_medical_id']
    if payload.get('charge_limit') is None:
        data['charge_limit'] = None
    elif isinstance(payload.get('charge_limit'), int):
        data['charge_limit'] = max(50, min(100, payload['charge_limit']))
    if 'night_light_temp' in payload:
        try:
            data['night_light_temp'] = max(
                2200, min(6500, int(payload['night_light_temp'])))
        except (TypeError, ValueError):
            pass
    if isinstance(payload.get('app_time_limits'), dict):
        data['app_time_limits'] = {
            k: v for k, v in payload['app_time_limits'].items()
            if isinstance(k, str) and isinstance(v, int) and v > 0}
    if isinstance(payload.get('app_limit_ignored'), dict):
        data['app_limit_ignored'] = {
            k: v for k, v in payload['app_limit_ignored'].items()
            if isinstance(k, str) and isinstance(v, str) and v}
    passthrough = (
        ('app_labels', dict), ('widgets', dict), ('theme', str),
        ('custom_background', str), ('font', str), ('custom_font_family', str),
        ('home_alignment', str), ('appearance_mode', str),
        ('lock_screen_notifications', str),
        ('drawer_sort', str), ('pinned', list),
        ('keyboard_layout', str),
    )
    for key, expected in passthrough:
        if isinstance(payload.get(key), expected):
            data[key] = payload[key]
    for key in _BOOL_KEYS:
        if key in payload:
            data[key] = bool(payload[key])
    for key in _LIST_KEYS:
        if key in payload:
            data[key] = list(payload[key])
    for key in ('weather_lat', 'weather_lon'):
        if key in payload:
            data[key] = payload[key]
    if payload.get('weather_temp_unit') in ('fahrenheit', 'celsius'):
        data['weather_temp_unit'] = payload['weather_temp_unit']
    if 'clock_24h' in payload and (
            payload['clock_24h'] is None
            or isinstance(payload['clock_24h'], bool)):
        data['clock_24h'] = payload['clock_24h']
    if isinstance(payload.get('weather_auto_location'), bool):
        data['weather_auto_location'] = payload['weather_auto_location']
    if 'text_size_scale' in payload:
        data['text_size_scale'] = max(0.5, min(2.0, float(payload['text_size_scale'])))
    if 'auto_lock_timeout' in payload:
        data['auto_lock_timeout'] = max(0, int(payload['auto_lock_timeout']))

    # set_home_slots validates slot shape and performs the single save
    config.set_home_slots(payload.get('home_slots', []))

    if 'gestures' in payload:
        try:
            from gesture_config import GestureConfig
            gc = GestureConfig()
            for key, action in payload['gestures'].items():
                gc._map[key] = action
            gc.save()
        except Exception:
            pass

    return True
