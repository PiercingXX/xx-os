from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from pathlib import Path


# Theme truth lives in common/xx_ui (installed as the xx_ui package);
# this module keeps owning config read/write and re-exports the names the
# shell imports from here, so every surface renders from one preset table.
def _xx_ui_theme():
    try:
        from xx_ui import theme as module
        return module
    except ImportError:
        pass
    import sys
    fallback = Path(__file__).resolve().parents[3] / 'common'
    if fallback.is_dir() and str(fallback) not in sys.path:
        sys.path.insert(0, str(fallback))
    from xx_ui import theme as module
    return module


_theme = _xx_ui_theme()
ThemePreset = _theme.ThemePreset
THEME_PRESETS = _theme.THEME_PRESETS
FONT_FAMILIES = _theme.FONT_FAMILIES
_derive_shades = _theme._derive_shades
del _theme, _xx_ui_theme

# Semantic colors that must not track the theme (universal meanings).
DANGER_RED = '#ff6b6b'
ON_DANGER_FG = '#ffffff'
WARNING_ORANGE = '#ff9a3c'
DESTRUCTIVE_TINT_BG = '#2a1010'

# Stored pin_hash formats: legacy bare sha256 hex, or
# pbkdf2$<iterations>$<salt_hex>$<hash_hex> (self-describing so the
# iteration count can change without a migration).
_PIN_HASH_PREFIX = 'pbkdf2$'
_PBKDF2_ITERATIONS = 100_000

DEFAULT_CONFIG = {
    'theme': 'amoled',
    'font': 'jetbrains-mono-nerd',
    'pinned': [],
    'hidden_apps': [],
    'prefer_dark': True,
    'appearance_mode': 'theme',
    'auto_lock_timeout': 0,
    'display_blank_timeout': 60,
    'text_size_scale': 1.0,
    # Home screen only, on top of text_size_scale (owner: home 20% smaller).
    'home_scale': 0.8,
    'home_alignment': 'center',
    'update_script': '~/.scripts/PiercingXX-Settings-Menu/update-system.sh',
    'update_last_check': 0.0,
    'update_snooze_until': 0.0,
    'home_slots': [],
    'home_to_recents': False,
    'search_auto_launch': False,
    'drawer_sort': 'default',
    # Android launcher parity (docs/launcher-settings-parity.md). Slot
    # count None = every slot shows (configs from before the setting).
    'home_slot_count': None,
    'date_time_mode': 'date_time',
    'status_bar_visible': False,
    'auto_show_keyboard': False,
    'swipe_left_enabled': True,
    'swipe_right_enabled': True,
    # None until migrated from gestures.json swipe_down_top (window init).
    'swipe_down_action': None,
    'preload_gesture_apps': False,
    'default_layout_applied': False,
    'app_labels': {},
    'muted_apps': {},
    'lock_screen_notifications': 'summary',
    'lock_screen_medical_id': True,
    'medical_id': {},
    'smart_lock_bt_devices': [],
    'smart_lock_wifi_ssids': [],
    'smart_lock_last_pin_time': 0.0,
    'dnd_enabled': False,
    'dnd_schedules': [],
    'dnd_starred_numbers': [],
    'focus_enabled': False,
    'focus_apps': [],
    'focus_schedules': [],
    'focus_break_until': 0.0,
    'app_standby_minutes': 10,
    'standby_exempt': [],
    'battery_saver': False,
    'battery_saver_auto': True,
    'battery_saver_auto_pct': 15,
    'privacy_indicators': True,
    'network_denied_apps': [],
    'sound_ringtone': True,
    'sound_notifications': True,
    'record_audio': False,
    'usage_stats_enabled': True,
    'charge_limit': None,
    'app_time_limits': {},
    'app_limit_ignored': {},
    'widgets': {
        'time': {'enabled': True, 'order': 1, 'tap': 'default'},
        'date': {'enabled': True, 'order': 2, 'tap': 'default'},
        'weather': {'enabled': True, 'order': 3, 'tap': 'default'},
        'battery': {'enabled': True, 'order': 4, 'tap': 'default'},
    },
    'weather_lat': None,
    'weather_lon': None,
    'weather_location': '',
    'weather_temp_unit': 'fahrenheit',
    'weather_auto_location': True,
    'clock_24h': None,
    'xxdb_enabled': False,
    'xxdb_persistent': False,
    'keyboard_layout': 'us',
    'night_light': False,
    'night_light_temp': 2200,
    'night_light_schedule': False,
    # Top status bar height in logical px; None = the device default
    # (status_bar.resolve_height). Device-local: not in backups.
    'status_bar_height': None,
}

_WEATHER_TEMP_UNITS = ('fahrenheit', 'celsius')


def same_app_id(first: object, second: object) -> bool:
    """True when two app ids name the same app: trailing `.desktop` and
    case are ignored (drawer ids carry the suffix, notification
    desktop-entry hints usually do not). Pure."""
    if not first or not second:
        return False

    def _norm(value: object) -> str:
        text = str(value)
        return (text[:-8] if text.endswith('.desktop') else text).casefold()

    return _norm(first) == _norm(second)

# Free-form string keys (docs/config.md); a container value here would
# crash set_text() consumers when the settings page is built.
_STRING_KEYS = ('apn', 'apn_user', 'apn_pass', 'weather_location',
                'keyboard_layout')

# Drawer sort modes, the Android launcher's values (docs/config.md):
# 'default' = A–Z with pinned apps first in their manual order,
# 'alphabetical' = A–Z with pinned apps first (A–Z among themselves),
# 'alphabetical_no_priority' = plain A–Z, 'install_date' = newest first,
# 'apk_size' = largest installed size first; pinned float on all but
# alphabetical_no_priority.
DRAWER_SORTS = ('default', 'alphabetical', 'alphabetical_no_priority',
                'install_date', 'apk_size')
# Values written before the Android set: read and migrated on load.
LEGACY_DRAWER_SORTS = {'az': 'alphabetical_no_priority',
                       'install': 'install_date'}

# Android launcher list preferences (values as on Android).
SLOT_COUNT_MAX = 8
DATE_TIME_MODES = ('off', 'date_only', 'date_time')
SWIPE_DOWN_ACTIONS = ('notifications', 'web_search', 'disabled')

# Light/dark switch owned by the Launcher settings section (docs/config.md
# "Appearance"): 'theme' keeps the preset-driven behavior, light/dark force
# it, 'system' follows the desktop color-scheme.
APPEARANCE_MODES = ('theme', 'light', 'dark', 'system')

# Escalating-lockout resume point (fail count + wall-clock lockout-until).
# Private device-local state: kept out of backups and restores.
_LOCKOUT_STATE_KEY = 'lock_screen_lockout'

# Medical ID fields (docs/config.md): every value is optional; unknown
# keys are dropped by sanitize_medical_id, bad types ignored, never crash.
_MEDICAL_ID_STRING_KEYS = (
    'name', 'date_of_birth', 'blood_type', 'allergies',
    'medications', 'conditions', 'notes',
)
_MEDICAL_ID_CONTACT_KEYS = ('name', 'number', 'relation')


def sanitize_medical_id(raw: object) -> dict:
    """Validated Medical ID: known string fields kept only when strings,
    organ_donor only when a bool, emergency_contacts only well-formed
    entries. Bad types are dropped, never raised — the same fail-soft
    rule as the container-key guards in load(). Pure."""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for key in _MEDICAL_ID_STRING_KEYS:
        value = raw.get(key)
        if isinstance(value, str):
            out[key] = value
    donor = raw.get('organ_donor')
    if isinstance(donor, bool):
        out['organ_donor'] = donor
    contacts = raw.get('emergency_contacts')
    if isinstance(contacts, list):
        clean: list[dict] = []
        for entry in contacts:
            if not isinstance(entry, dict):
                continue
            contact = {}
            for key in _MEDICAL_ID_CONTACT_KEYS:
                value = entry.get(key)
                if isinstance(value, str):
                    contact[key] = value
            # A row with neither a name nor a number dials nothing.
            if contact.get('name') or contact.get('number'):
                clean.append(contact)
        out['emergency_contacts'] = clean
    return out


class ShellConfig:
    def __init__(self) -> None:
        self.config_dir = Path.home() / '.config' / 'xx-wm'
        self.config_path = self.config_dir / 'config.json'
        # One-time migration from the pre-rename config dir (piercing-shell).
        legacy_dir = Path.home() / '.config' / 'piercing-shell'
        if legacy_dir.is_dir() and not self.config_dir.exists():
            legacy_dir.rename(self.config_dir)
        self.data = dict(DEFAULT_CONFIG)
        self.load()

    def load(self) -> None:
        if not self.config_path.exists():
            return

        try:
            loaded = json.loads(self.config_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return

        if not isinstance(loaded, dict):
            return

        for key, value in loaded.items():
            default = DEFAULT_CONFIG.get(key)
            # A scalar under a list/dict default ("pinned": "foo") would be
            # iterated char-by-char downstream; keep the default instead.
            if isinstance(default, (list, dict)) and not isinstance(
                    value, type(default)):
                continue
            if key in _STRING_KEYS and not isinstance(value, str):
                continue
            self.data[key] = value
        legacy_sort = LEGACY_DRAWER_SORTS.get(self.data.get('drawer_sort'))
        if legacy_sort is not None:
            # 'az'/'install' predate the Android sort names; the next
            # save writes the new name.
            self.data['drawer_sort'] = legacy_sort

    def save(self) -> None:
        payload = json.dumps(self.data, indent=2) + '\n'
        # eMMC wear guard: saves fire on every app launch and every settings
        # touch, often with nothing actually changed. Skip the flash write
        # when the serialized payload is already what's on disk; a mutated
        # payload (launch_counts per launch) still writes normally.
        try:
            if self.config_path.read_text(encoding='utf-8') == payload:
                # Content is identical, but an external chmod (e.g. a backup
                # tool) may have loosened the mode since the last write;
                # re-tighten without paying for the flash write.
                if stat.S_IMODE(self.config_path.stat().st_mode) != 0o600:
                    os.chmod(self.config_path, 0o600)
                return
        except (OSError, ValueError):
            pass  # missing, unreadable, or corrupt file — take the write path
        self.config_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self.config_path.with_name(self.config_path.name + '.tmp')
        # 0600 at open(): the file never exists world-readable, even briefly.
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self.config_path)
        finally:
            # After a successful replace the tmp name is gone, so this only
            # ever deletes a mid-write orphan.
            tmp_path.unlink(missing_ok=True)
        os.chmod(self.config_path, 0o600)

    def get(self, key: str, default=None):
        """Read any key with a fallback, so settings rows for keys owned
        by other lanes keep working before those lanes land."""
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        """Write any key (docs/config.md); sibling lanes add validation."""
        self.data[key] = value
        self.save()

    @property
    def theme(self) -> ThemePreset:
        key = str(self.data.get('theme', DEFAULT_CONFIG['theme']))
        return THEME_PRESETS.get(key, THEME_PRESETS[DEFAULT_CONFIG['theme']])

    @property
    def font_family(self) -> str:
        key = str(self.data.get('font', DEFAULT_CONFIG['font']))
        if key == 'custom':
            family = self.data.get('custom_font_family')
            if family:
                return str(family)
        return FONT_FAMILIES.get(key, FONT_FAMILIES[DEFAULT_CONFIG['font']])

    @property
    def pinned(self) -> list[str]:
        pinned = self.data.get('pinned', DEFAULT_CONFIG['pinned'])
        if isinstance(pinned, list):
            return [str(item) for item in pinned]
        return []

    @property
    def prefer_dark(self) -> bool:
        return bool(self.data.get('prefer_dark', DEFAULT_CONFIG['prefer_dark']))

    def set_theme(self, key: str) -> None:
        if key in THEME_PRESETS:
            self.data['theme'] = key
            self.save()

    def set_font(self, key: str) -> None:
        if key in FONT_FAMILIES or (key == 'custom' and self.data.get('custom_font_family')):
            self.data['font'] = key
            self.save()

    def set_prefer_dark(self, enabled: bool) -> None:
        self.data['prefer_dark'] = enabled
        self.save()

    @property
    def appearance_mode(self) -> str:
        mode = self.data.get('appearance_mode', DEFAULT_CONFIG['appearance_mode'])
        return mode if mode in APPEARANCE_MODES else DEFAULT_CONFIG['appearance_mode']

    def set_appearance_mode(self, mode: str) -> None:
        if mode in APPEARANCE_MODES:
            self.data['appearance_mode'] = mode
            self.save()

    def set_pinned(self, app_ids: list[str]) -> None:
        self.data['pinned'] = app_ids[:8]
        self.save()

    @property
    def pin_hash(self) -> str | None:
        value = self.data.get('pin_hash')
        # Only a truly absent key means "no PIN"; an empty/corrupt value must
        # fail closed (verify_pin rejects) rather than collapse to unlocked.
        return str(value) if value is not None else None

    def set_pin(self, pin: str) -> None:
        salt = os.urandom(16)
        digest = hashlib.pbkdf2_hmac('sha256', pin.encode(), salt, _PBKDF2_ITERATIONS)
        self.data['pin_hash'] = (
            f'{_PIN_HASH_PREFIX}{_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}'
        )
        self.save()

    def verify_pin(self, pin: str) -> bool:
        stored = self.pin_hash
        if stored is None:
            return True
        if stored.startswith(_PIN_HASH_PREFIX):
            return self._verify_pbkdf2_pin(pin, stored)
        # Bytes comparison: compare_digest rejects non-ASCII str inputs, and
        # a tampered/corrupt stored value must fail closed, not raise.
        legacy_ok = hmac.compare_digest(
            stored.encode(), hashlib.sha256(pin.encode()).hexdigest().encode(),
        )
        if legacy_ok:
            try:
                self.set_pin(pin)
            except OSError:
                # Upgrade persist is best-effort: an unwritable config must
                # not turn a correct PIN into a raised exception.
                pass
        return legacy_ok

    @staticmethod
    def _verify_pbkdf2_pin(pin: str, stored: str) -> bool:
        try:
            _, iterations, salt_hex, hash_hex = stored.split('$')
            expected = bytes.fromhex(hash_hex)
            digest = hashlib.pbkdf2_hmac(
                'sha256', pin.encode(), bytes.fromhex(salt_hex), int(iterations),
            )
        except ValueError:
            return False
        return hmac.compare_digest(digest, expected)

    @property
    def auto_lock_timeout(self) -> int:
        val = self.data.get('auto_lock_timeout', DEFAULT_CONFIG['auto_lock_timeout'])
        try:
            return max(0, int(val))
        except (TypeError, ValueError):
            return int(DEFAULT_CONFIG['auto_lock_timeout'])

    def set_auto_lock_timeout(self, seconds: int) -> None:
        self.data['auto_lock_timeout'] = max(0, seconds)
        self.save()

    @property
    def display_blank_timeout(self) -> int:
        val = self.data.get('display_blank_timeout',
                            DEFAULT_CONFIG['display_blank_timeout'])
        try:
            return max(0, int(val))
        except (TypeError, ValueError):
            return int(DEFAULT_CONFIG['display_blank_timeout'])

    def set_display_blank_timeout(self, seconds: int) -> None:
        self.data['display_blank_timeout'] = max(0, seconds)
        self.save()

    def get_lockout_state(self) -> tuple[int, float]:
        """Escalating-lockout resume point: (wrong-attempt count,
        lockout-until wall-clock epoch). Private device-local state —
        never backed up, never restored — so a shell restart cannot
        reset the escalation. Corrupt values fail open to (0, 0.0)."""
        raw = self.data.get(_LOCKOUT_STATE_KEY)
        if not isinstance(raw, dict):
            return (0, 0.0)
        try:
            fails = max(0, int(raw.get('fails', 0)))
            until = max(0.0, float(raw.get('until', 0.0)))
        except (TypeError, ValueError):
            return (0, 0.0)
        return (fails, until)

    def set_lockout_state(self, fails: int, until: float) -> None:
        self.data[_LOCKOUT_STATE_KEY] = {
            'fails': max(0, int(fails)),
            'until': max(0.0, float(until)),
        }
        self.save()

    def clear_lockout_state(self) -> None:
        if _LOCKOUT_STATE_KEY in self.data:
            del self.data[_LOCKOUT_STATE_KEY]
            self.save()

    @property
    def hidden_apps(self) -> list[str]:
        val = self.data.get('hidden_apps', [])
        if isinstance(val, list):
            return [str(x) for x in val]
        return []

    def set_hidden_apps(self, app_ids: list[str]) -> None:
        self.data['hidden_apps'] = list(app_ids)
        self.save()

    @property
    def text_size_scale(self) -> float:
        try:
            return max(0.5, min(2.0, float(self.data.get('text_size_scale', 1.0))))
        except (TypeError, ValueError):
            return 1.0

    @property
    def home_scale(self) -> float:
        try:
            return max(0.5, min(1.5, float(self.data.get('home_scale', 0.8))))
        except (TypeError, ValueError):
            return 0.8

    def set_home_scale(self, scale: float) -> None:
        self.data['home_scale'] = max(0.5, min(1.5, round(float(scale), 2)))
        self.save()

    def set_text_size_scale(self, scale: float) -> None:
        self.data['text_size_scale'] = max(0.5, min(2.0, round(scale, 2)))
        self.save()

    @property
    def home_alignment(self) -> str:
        val = str(self.data.get('home_alignment', 'center'))
        return val if val in ('left', 'center', 'right') else 'center'

    def set_home_alignment(self, alignment: str) -> None:
        if alignment in ('left', 'center', 'right'):
            self.data['home_alignment'] = alignment
            self.save()

    @property
    def update_script(self) -> str:
        val = self.data.get('update_script', DEFAULT_CONFIG['update_script'])
        return str(val) if val else str(DEFAULT_CONFIG['update_script'])

    @property
    def update_last_check(self) -> float:
        try:
            return max(0.0, float(self.data.get('update_last_check', 0.0)))
        except (TypeError, ValueError):
            return 0.0

    def set_update_last_check(self, timestamp: float) -> None:
        self.data['update_last_check'] = max(0.0, timestamp)
        self.save()

    @property
    def update_snooze_until(self) -> float:
        try:
            return max(0.0, float(self.data.get('update_snooze_until', 0.0)))
        except (TypeError, ValueError):
            return 0.0

    def set_update_snooze_until(self, timestamp: float) -> None:
        self.data['update_snooze_until'] = max(0.0, timestamp)
        self.save()

    @property
    def launch_counts(self) -> dict[str, int]:
        val = self.data.get('launch_counts', {})
        if not isinstance(val, dict):
            return {}
        out: dict[str, int] = {}
        for k, v in val.items():
            try:
                out[str(k)] = int(v)
            except (TypeError, ValueError):
                continue
        return out

    # eMMC wear: launch counts accumulate in memory and reach flash at
    # most once per minute, plus once on shutdown (see flush_launch_counts).
    _LAUNCH_FLUSH_SECONDS = 60.0

    def record_launch(self, app_id: str) -> None:
        import time
        counts = self.launch_counts
        counts[app_id] = counts.get(app_id, 0) + 1
        self.data['launch_counts'] = counts
        now = time.monotonic()
        last = getattr(self, '_last_launch_flush', None)
        if last is None or now - last >= self._LAUNCH_FLUSH_SECONDS:
            self.save()
            self._last_launch_flush = now
        else:
            self._launch_dirty = True

    def flush_launch_counts(self) -> None:
        """Write pending launch counts to flash. Called on shutdown; a no-op
        when nothing was recorded since the last flush."""
        import time
        if not getattr(self, '_launch_dirty', False):
            return
        self._launch_dirty = False
        self.save()
        self._last_launch_flush = time.monotonic()

    @property
    def default_layout_applied(self) -> bool:
        return bool(self.data.get('default_layout_applied', False))

    def set_default_layout_applied(self, value: bool) -> None:
        self.data['default_layout_applied'] = bool(value)
        self.save()

    @property
    def home_slots(self) -> list[dict]:
        slots = self.data.get('home_slots', DEFAULT_CONFIG['home_slots'])
        if isinstance(slots, list):
            return slots
        return []

    def set_home_slots(self, slots: list[dict]) -> None:
        validated = []
        for slot in slots[:8]:
            if not isinstance(slot, dict):
                continue
            slot_type = slot.get('type')
            if slot_type not in ('app', 'folder'):
                continue
            validated.append({
                'type': slot_type,
                'label': str(slot.get('label', '')),
                'app_id': str(slot.get('app_id')) if slot.get('app_id') else None,
                'cmd': slot.get('cmd') if isinstance(slot.get('cmd'), list) else None,
                'folder': slot.get('folder') if isinstance(slot.get('folder'), list) else None,
            })
        self.data['home_slots'] = validated
        self.save()
    @property
    def app_labels(self) -> dict[str, str]:
        val = self.data.get('app_labels', {})
        if isinstance(val, dict):
            return {str(k): str(v) for k, v in val.items()}
        return {}

    def set_app_label(self, app_id: str, label: str | None) -> None:
        labels = self.app_labels
        if label:
            labels[app_id] = label
        else:
            labels.pop(app_id, None)
        self.data['app_labels'] = labels
        self.save()

    def label_for(self, app_id: str, fallback: str) -> str:
        return self.app_labels.get(app_id, fallback)

    def clear_app_labels(self) -> None:
        self.data['app_labels'] = {}
        self.save()

    @property
    def muted_apps(self) -> dict[str, float]:
        val = self.data.get('muted_apps', {})
        if not isinstance(val, dict):
            return {}
        out: dict[str, float] = {}
        for k, v in val.items():
            try:
                out[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
        return out

    @staticmethod
    def _norm_app_id(app_id: str) -> str:
        # Mute keys come from drawer app ids (with .desktop) and notification
        # desktop-entry hints (usually without); compare them stripped.
        return app_id[:-8] if app_id.endswith('.desktop') else app_id

    def set_app_muted(self, app_id: str, until_epoch: float, now: float | None = None) -> None:
        if now is None:
            import time
            now = time.time()
        muted = {k: v for k, v in self.muted_apps.items() if v > now}
        muted[self._norm_app_id(app_id)] = float(until_epoch)
        self.data['muted_apps'] = muted
        self.save()

    def is_app_muted(self, app_id: str, now: float | None = None) -> bool:
        if not app_id:
            return False
        if now is None:
            import time
            now = time.time()
        return any(
            until > now and same_app_id(key, app_id)
            for key, until in self.muted_apps.items()
        )

    def muted_until(self, app_id: str, now: float | None = None) -> float | None:
        """Mute deadline for an app id (matched like is_app_muted), or None
        when it is not currently muted. Powers the app-menu subtitle."""
        if not app_id:
            return None
        if now is None:
            import time
            now = time.time()
        for key, until in self.muted_apps.items():
            if until > now and same_app_id(key, app_id):
                return until
        return None

    @property
    def weather_temp_unit(self) -> str:
        """Open-Meteo temperature unit (docs/config.md): `fahrenheit`
        unless explicitly `celsius`; corrupt values fail soft to default."""
        value = self.data.get('weather_temp_unit',
                              DEFAULT_CONFIG['weather_temp_unit'])
        return value if value in _WEATHER_TEMP_UNITS else 'fahrenheit'

    def set_weather_temp_unit(self, unit: str) -> None:
        if unit in _WEATHER_TEMP_UNITS:
            self.data['weather_temp_unit'] = unit
            self.save()

    @property
    def weather_auto_location(self) -> bool:
        """Coarse GeoClue fix when no manual coordinates are set. On by
        default; corrupt values fail open to enabled."""
        value = self.data.get('weather_auto_location',
                              DEFAULT_CONFIG['weather_auto_location'])
        return value if isinstance(value, bool) else True

    def set_weather_auto_location(self, enabled: bool) -> None:
        self.data['weather_auto_location'] = bool(enabled)
        self.save()

    @property
    def clock_24h(self) -> bool | None:
        """Clock hour mode: True (24h) / False (12h, no AM/PM) / None to
        follow `org.gnome.desktop.interface clock-format`. Corrupt values
        fail soft to None (follow system)."""
        value = self.data.get('clock_24h', DEFAULT_CONFIG['clock_24h'])
        return value if isinstance(value, bool) else None

    def set_clock_24h(self, value: bool | None) -> None:
        if value is None or isinstance(value, bool):
            self.data['clock_24h'] = value
            self.save()

    @property
    def lock_screen_notifications(self) -> str:
        val = str(self.data.get('lock_screen_notifications', 'summary'))
        return val if val in ('summary', 'count', 'off') else 'summary'

    @property
    def medical_id(self) -> dict:
        """Validated Medical ID (docs/config.md): all fields optional,
        bad types dropped by sanitize_medical_id, never crash."""
        return sanitize_medical_id(self.data.get('medical_id'))

    def set_medical_id(self, medical: dict) -> None:
        self.data['medical_id'] = sanitize_medical_id(medical)
        self.save()

    @property
    def lock_screen_medical_id(self) -> bool:
        """Medical info visible from the lock screen. Default true; a
        corrupt non-bool value fails open to visible — and emergency
        calling itself can never be disabled by this flag."""
        value = self.data.get('lock_screen_medical_id', True)
        return value if isinstance(value, bool) else True

    def set_lock_screen_medical_id(self, enabled: bool) -> None:
        self.data['lock_screen_medical_id'] = bool(enabled)
        self.save()

    @property
    def smart_lock_bt_devices(self) -> list[str]:
        """Trusted Bluetooth addresses for Smart Lock (docs/config.md):
        upper-cased, opt-in, empty by default."""
        from smart_lock import sanitize_bt_devices
        return sanitize_bt_devices(self.data.get('smart_lock_bt_devices'))

    def set_smart_lock_bt_devices(self, addresses: list[str]) -> None:
        from smart_lock import sanitize_bt_devices
        self.data['smart_lock_bt_devices'] = sanitize_bt_devices(addresses)
        self.save()

    @property
    def smart_lock_wifi_ssids(self) -> list[str]:
        """Trusted Wi-Fi SSIDs for Smart Lock: verbatim, opt-in, empty."""
        from smart_lock import sanitize_ssids
        return sanitize_ssids(self.data.get('smart_lock_wifi_ssids'))

    def set_smart_lock_wifi_ssids(self, ssids: list[str]) -> None:
        from smart_lock import sanitize_ssids
        self.data['smart_lock_wifi_ssids'] = sanitize_ssids(ssids)
        self.save()

    @property
    def smart_lock_last_pin_time(self) -> float:
        """Wall-clock epoch of the last successful PIN unlock (Smart Lock
        4-hour clock). Device-local transient like the lockout resume
        point: never backed up, never restored. Corrupt values read as
        never, which fails closed to PIN-required."""
        try:
            return max(0.0, float(self.data.get('smart_lock_last_pin_time', 0.0)))
        except (TypeError, ValueError):
            return 0.0

    def record_smart_lock_pin_time(self, timestamp: float) -> None:
        self.data['smart_lock_last_pin_time'] = max(0.0, float(timestamp))
        self.save()

    @property
    def sound_ringtone(self) -> bool:
        return bool(self.data.get('sound_ringtone', True))

    @property
    def sound_notifications(self) -> bool:
        return bool(self.data.get('sound_notifications', True))

    @property
    def record_audio(self) -> bool:
        return bool(self.data.get(
            'record_audio', DEFAULT_CONFIG['record_audio']))

    def set_record_audio(self, enabled: bool) -> None:
        self.data['record_audio'] = bool(enabled)
        self.save()

    @property
    def usage_stats_enabled(self) -> bool:
        return bool(self.data.get(
            'usage_stats_enabled', DEFAULT_CONFIG['usage_stats_enabled']))

    def set_usage_stats_enabled(self, enabled: bool) -> None:
        self.data['usage_stats_enabled'] = bool(enabled)
        self.save()

    @property
    def charge_limit(self) -> int | None:
        """Charge-limit choice in percent, None when Off (docs/config.md):
        an int outside 50..100 reads as Off rather than crashing."""
        value = self.data.get('charge_limit', None)
        if value is None:
            return None
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if 50 <= number <= 100 else None

    def set_charge_limit(self, percent: int | None) -> None:
        if percent is None:
            self.data['charge_limit'] = None
        else:
            try:
                number = int(percent)
            except (TypeError, ValueError):
                number = 100
            self.data['charge_limit'] = max(50, min(100, number))
        self.save()

    @property
    def app_time_limits(self) -> dict:
        """Per-app daily screen-time limits in minutes (docs/config.md):
        {app_id: minutes}; bad shapes read as no limits, never crash."""
        raw = self.data.get('app_time_limits', {})
        if not isinstance(raw, dict):
            return {}
        out: dict = {}
        for key, value in raw.items():
            try:
                minutes = int(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if isinstance(key, str) and key and minutes > 0:
                out[key] = minutes
        return out

    def set_app_time_limit(self, app_id: str, minutes: int) -> None:
        limits = self.app_time_limits
        if minutes <= 0:
            limits.pop(app_id, None)
        else:
            limits[app_id] = int(minutes)
        self.data['app_time_limits'] = limits
        self.save()

    @property
    def app_limit_ignored(self) -> dict:
        """{app_id: 'YYYY-MM-DD'} the limit was ignored for (docs/config.md):
        only today's rows matter; bad shapes read as none, never crash."""
        raw = self.data.get('app_limit_ignored', {})
        if not isinstance(raw, dict):
            return {}
        return {k: v for k, v in raw.items()
                if isinstance(k, str) and isinstance(v, str) and v}

    def set_app_limit_ignored(self, app_id: str, day: str | None) -> None:
        ignored = self.app_limit_ignored
        if day is None:
            ignored.pop(app_id, None)
        else:
            ignored[app_id] = str(day)
        self.data['app_limit_ignored'] = ignored
        self.save()

    @property
    def home_to_recents(self) -> bool:
        return bool(self.data.get('home_to_recents', False))

    def set_home_to_recents(self, enabled: bool) -> None:
        self.data['home_to_recents'] = bool(enabled)
        self.save()

    @property
    def search_auto_launch(self) -> bool:
        return bool(self.data.get('search_auto_launch', False))

    def set_search_auto_launch(self, enabled: bool) -> None:
        self.data['search_auto_launch'] = bool(enabled)
        self.save()

    @property
    def drawer_sort(self) -> str:
        val = self.data.get('drawer_sort', DEFAULT_CONFIG['drawer_sort'])
        val = LEGACY_DRAWER_SORTS.get(val, val)
        return val if val in DRAWER_SORTS else 'default'

    def set_drawer_sort(self, mode: str) -> None:
        mode = LEGACY_DRAWER_SORTS.get(mode, mode)
        if mode in DRAWER_SORTS:
            self.data['drawer_sort'] = mode
            self.save()

    # -- Android launcher parity prefs --------------------------------------

    @property
    def home_slot_count(self) -> int | None:
        """Visible home slots (Android "Home Slot Count", 0–8), or None
        when never set: then every stored slot shows."""
        value = self.data.get('home_slot_count')
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return max(0, min(SLOT_COUNT_MAX, value))

    def effective_slot_count(self) -> int:
        """The count the home screen shows (settings row value)."""
        count = self.home_slot_count
        if count is None:
            return min(SLOT_COUNT_MAX, len(self.home_slots))
        return count

    def set_home_slot_count(self, count: int) -> None:
        try:
            count = int(count)
        except (TypeError, ValueError):
            return
        self.data['home_slot_count'] = max(0, min(SLOT_COUNT_MAX, count))
        self.save()

    def visible_home_slots(self) -> list[dict]:
        """The slots home shows: the first ``home_slot_count`` (slots
        past the count stay stored, as on Android)."""
        slots = self.home_slots
        count = self.home_slot_count
        return slots if count is None else slots[:count]

    def empty_home_slots(self) -> int:
        """Placeholder rows ("· · ·") under the filled visible slots."""
        count = self.home_slot_count
        if count is None:
            return 0
        return max(0, count - len(self.home_slots))

    @property
    def date_time_mode(self) -> str:
        value = self.data.get('date_time_mode', 'date_time')
        return value if value in DATE_TIME_MODES else 'date_time'

    def set_date_time_mode(self, mode: str) -> None:
        if mode in DATE_TIME_MODES:
            self.data['date_time_mode'] = mode
            self.save()

    @property
    def status_bar_visible(self) -> bool:
        return self.data.get('status_bar_visible') is True

    def set_status_bar_visible(self, enabled: bool) -> None:
        self.data['status_bar_visible'] = bool(enabled)
        self.save()

    @property
    def auto_show_keyboard(self) -> bool:
        return self.data.get('auto_show_keyboard') is True

    def set_auto_show_keyboard(self, enabled: bool) -> None:
        self.data['auto_show_keyboard'] = bool(enabled)
        self.save()

    @property
    def swipe_left_enabled(self) -> bool:
        return self.data.get('swipe_left_enabled', True) is not False

    def set_swipe_left_enabled(self, enabled: bool) -> None:
        self.data['swipe_left_enabled'] = bool(enabled)
        self.save()

    @property
    def swipe_right_enabled(self) -> bool:
        return self.data.get('swipe_right_enabled', True) is not False

    def set_swipe_right_enabled(self, enabled: bool) -> None:
        self.data['swipe_right_enabled'] = bool(enabled)
        self.save()

    @property
    def swipe_down_action(self) -> str:
        value = self.data.get('swipe_down_action')
        return value if value in SWIPE_DOWN_ACTIONS else 'notifications'

    def set_swipe_down_action(self, action: str) -> None:
        if action in SWIPE_DOWN_ACTIONS:
            self.data['swipe_down_action'] = action
            self.save()

    @property
    def preload_gesture_apps(self) -> bool:
        """Opt-in warm-up of swipe-bound apps at session start. Default off:
        preloading is counter to the minimalism directive on weak hardware."""
        return bool(self.data.get('preload_gesture_apps', DEFAULT_CONFIG['preload_gesture_apps']))

    def set_preload_gesture_apps(self, enabled: bool) -> None:
        self.data['preload_gesture_apps'] = bool(enabled)
        self.save()

    @property
    def widgets(self) -> dict[str, dict]:
        """Widget config merged over defaults; unknown keys ignored."""
        defaults = DEFAULT_CONFIG['widgets']
        val = self.data.get('widgets')
        merged: dict[str, dict] = {}
        for key, default in defaults.items():
            entry = dict(default)
            if isinstance(val, dict) and isinstance(val.get(key), dict):
                user = val[key]
                if isinstance(user.get('enabled'), bool):
                    entry['enabled'] = user['enabled']
                try:
                    entry['order'] = int(user.get('order', entry['order']))
                except (TypeError, ValueError):
                    pass
                tap = user.get('tap')
                if tap in ('default', 'none', 'refresh') or (
                    isinstance(tap, dict) and tap.get('app')
                ):
                    entry['tap'] = tap
            merged[key] = entry
        return merged

    def ordered_widgets(self) -> list[tuple[str, dict]]:
        widgets = self.widgets
        return sorted(
            ((k, v) for k, v in widgets.items() if v.get('enabled')),
            key=lambda item: item[1].get('order', 99),
        )

    def _mutable_widgets(self) -> dict:
        """Owned copy of the widget map. data starts as a shallow
        DEFAULT_CONFIG copy, so writing into it directly would corrupt
        the defaults for every later instance."""
        widgets = self.data.get('widgets')
        if not isinstance(widgets, dict):
            widgets = {}
        widgets = dict(widgets)
        self.data['widgets'] = widgets
        return widgets

    def set_widget_enabled(self, key: str, enabled: bool) -> None:
        if key not in DEFAULT_CONFIG['widgets']:
            return
        widgets = self._mutable_widgets()
        entry = dict(widgets.get(key) or {})
        entry['enabled'] = bool(enabled)
        widgets[key] = entry
        self.save()

    def move_widget(self, key: str, delta: int) -> None:
        """Swap a widget's order with its display neighbor. Edge moves and
        unknown keys are no-ops."""
        ordered = [name for name, _conf in self.ordered_widgets()]
        if key not in ordered:
            return
        idx = ordered.index(key)
        other = idx + (1 if delta > 0 else -1)
        if not (0 <= other < len(ordered)):
            return
        orders = {name: conf.get('order', 99)
                  for name, conf in self.widgets.items()}
        orders[key], orders[ordered[other]] = (
            orders[ordered[other]], orders[key])
        widgets = self._mutable_widgets()
        for name, order in orders.items():
            entry = dict(widgets.get(name) or {})
            entry['order'] = order
            widgets[name] = entry
        self.save()

    def set_widget_order(self, keys: list[str]) -> bool:
        """Apply a whole widget order (the reorder sheet). Anything but a
        permutation of the known widget keys leaves the order alone."""
        if sorted(keys) != sorted(DEFAULT_CONFIG['widgets']):
            return False
        widgets = self._mutable_widgets()
        for index, name in enumerate(keys):
            entry = dict(widgets.get(name) or {})
            entry['order'] = index
            widgets[name] = entry
        self.save()
        return True

    def set_widget_tap(self, key: str, tap) -> None:
        if key not in DEFAULT_CONFIG['widgets']:
            return
        valid = tap in ('default', 'none', 'refresh') or (
            isinstance(tap, dict) and tap.get('app'))
        if not valid:
            return
        widgets = self._mutable_widgets()
        entry = dict(widgets.get(key) or {})
        entry['tap'] = tap
        widgets[key] = entry
        self.save()

    @property
    def custom_font_family(self) -> str | None:
        val = self.data.get('custom_font_family')
        return str(val) if val else None

    @property
    def custom_background(self) -> str | None:
        val = self.data.get('custom_background')
        return str(val) if val else None

    @property
    def app_standby_minutes(self) -> int:
        try:
            return max(0, int(self.data.get(
                'app_standby_minutes',
                DEFAULT_CONFIG['app_standby_minutes'])))
        except (TypeError, ValueError):
            return int(DEFAULT_CONFIG['app_standby_minutes'])

    def set_app_standby_minutes(self, minutes: int) -> None:
        try:
            self.data['app_standby_minutes'] = max(0, int(minutes))
        except (TypeError, ValueError):
            return
        self.save()

    @property
    def standby_exempt(self) -> list[str]:
        val = self.data.get('standby_exempt', [])
        if isinstance(val, list):
            return [str(x) for x in val]
        return []

    def set_standby_exempt(self, app_ids: list[str]) -> None:
        self.data['standby_exempt'] = [str(x) for x in app_ids]
        self.save()

    @property
    def battery_saver(self) -> bool:
        return bool(self.data.get(
            'battery_saver', DEFAULT_CONFIG['battery_saver']))

    def set_battery_saver(self, enabled: bool) -> None:
        self.data['battery_saver'] = bool(enabled)
        self.save()

    @property
    def battery_saver_auto(self) -> bool:
        return bool(self.data.get(
            'battery_saver_auto', DEFAULT_CONFIG['battery_saver_auto']))

    def set_battery_saver_auto(self, enabled: bool) -> None:
        self.data['battery_saver_auto'] = bool(enabled)
        self.save()

    @property
    def battery_saver_auto_pct(self) -> int:
        """Auto-saver battery percent (docs/config.md): clamped 1..100,
        corrupt values fail to the compiled-in default."""
        try:
            from app_standby import SAVER_AUTO_BATTERY_PCT
        except ImportError:
            SAVER_AUTO_BATTERY_PCT = 15
        try:
            return max(1, min(100, int(self.data.get(
                'battery_saver_auto_pct', SAVER_AUTO_BATTERY_PCT))))
        except (TypeError, ValueError):
            return SAVER_AUTO_BATTERY_PCT

    def set_battery_saver_auto_pct(self, pct: int) -> None:
        try:
            self.data['battery_saver_auto_pct'] = max(1, min(100, int(pct)))
        except (TypeError, ValueError):
            return
        self.save()

    @property
    def privacy_indicators(self) -> bool:
        """Mic/camera/location indicators on home and in the shade
        (docs/config.md). Default on; corrupt values fail open to shown."""
        value = self.data.get('privacy_indicators', True)
        return value if isinstance(value, bool) else True

    def set_privacy_indicators(self, enabled: bool) -> None:
        self.data['privacy_indicators'] = bool(enabled)
        self.save()

    @property
    def network_denied_apps(self) -> list[str]:
        val = self.data.get('network_denied_apps', [])
        if isinstance(val, list):
            return [str(x) for x in val]
        return []

    def set_network_denied_apps(self, app_ids: list[str]) -> None:
        self.data['network_denied_apps'] = [str(x) for x in app_ids]
        self.save()

    @property
    def xxdb_enabled(self) -> bool:
        return bool(self.data.get('xxdb_enabled', False))

    @property
    def xxdb_persistent(self) -> bool:
        """Keep wireless debugging on across restarts. Default off: xxdb
        turns itself off on every shell restart/reboot unless opted in."""
        return bool(self.data.get('xxdb_persistent', False))

    def set_xxdb_persistent(self, enabled: bool) -> None:
        self.data['xxdb_persistent'] = bool(enabled)
        self.save()

    def set_custom_background(self, color: str) -> bool:
        """Set custom background color. Returns True if valid."""
        import re
        if not re.match(r'^#[0-9a-fA-F]{6}$', color):
            return False
        self.data['theme'] = 'custom'
        self.data['custom_background'] = color
        self.save()
        return True


def should_preload_gesture_apps(config: 'ShellConfig', real_session: bool) -> bool:
    """Gate for the session-start app preload (20.4).

    Preloading warms swipe-bound apps into RAM so a gesture opens a resident
    process instead of cold-starting it. It is counter to the minimalism
    directive on weak hardware, so it only runs in a real XX-WM session
    (never under a host shell over Phosh) AND with the opt-in
    ``preload_gesture_apps`` config key set (default off).
    """
    return bool(real_session) and config.preload_gesture_apps


def install_custom_font(config: 'ShellConfig', path: str,
                        run: object = None) -> tuple[bool, str]:
    """Install a user .ttf/.otf into ~/.local/share/fonts and switch the shell
    to it (`font: 'custom'`, `custom_font_family`). Custom font files are
    deliberately excluded from backups. Returns (ok, message)."""
    import shutil
    import subprocess
    runner = run or subprocess.run

    src = Path(path).expanduser()
    if src.suffix.lower() not in ('.ttf', '.otf'):
        return False, 'Font must be a .ttf or .otf file.'
    if not src.is_file():
        return False, f'No such file: {src}'

    dest_dir = Path.home() / '.local' / 'share' / 'fonts'
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        shutil.copy2(src, dest)
    except OSError as error:
        return False, f'Could not install font: {error}'

    family = src.stem
    try:
        result = runner(['fc-scan', '--format', '%{family}', str(dest)],
                        capture_output=True, text=True, timeout=5)
        scanned = (result.stdout or '').split(',')[0].strip()
        if scanned:
            family = scanned
    except Exception:
        pass

    try:
        runner(['fc-cache', '-f'], capture_output=True, timeout=30)
    except Exception:
        pass

    config.data['custom_font_family'] = family
    config.data['font'] = 'custom'
    config.save()
    return True, family


def migrate_launcher_prefs(config, gestures) -> bool:
    """One-time moves onto the Android launcher prefs; True when saved.

    The home swipe-down used to run the system top-edge slot
    (gestures.json swipe_down_top); it now has its own Android pref,
    seeded from that slot: 'none' becomes 'disabled', anything else
    keeps opening the shade ('notifications')."""
    data = getattr(config, 'data', None)
    if not isinstance(data, dict):
        return False
    if data.get('swipe_down_action') in SWIPE_DOWN_ACTIONS:
        return False
    try:
        top = gestures.get('swipe_down_top') if gestures is not None else None
    except Exception:
        top = None
    data['swipe_down_action'] = 'disabled' if top == 'none' else 'notifications'
    try:
        config.save()
    except Exception:
        return False
    return True
