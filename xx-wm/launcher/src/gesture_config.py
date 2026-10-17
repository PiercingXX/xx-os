from __future__ import annotations

import json
from pathlib import Path

def _config_path() -> Path:
    # Resolved per-instance so a redirected HOME (tests) is honored
    return Path.home() / '.config' / 'xx-wm' / 'gestures.json'

# Gesture slot → default action. Pixel split: short swipe-up is home,
# long swipe-up is recents. Devices that still have the previous pair
# (both slots = app_switcher) are migrated once in _load.
_DEFAULTS: dict[str, str] = {
    'swipe_down_top':        'notification_shade',
    'swipe_up_short':        'home',
    'swipe_up_long':         'app_switcher',
    'swipe_left_edge':       'back',
    'long_press_bottom':     'search',
    # Android launcher parity: "Double Tap to Lock" is off by default,
    # and an unset home swipe ('none') opens its default app (left:
    # camera, right: dialer — see home_swipe_action).
    'double_tap_home':       'none',
    'long_press_home':       'edit',
    'swipe_left_home':       'none',
    'swipe_right_home':      'none',
    'squeeze':               'assistant',
    'fingerprint_swipe':     'notification_shade',
    'double_press_power':    'camera',
}

VALID_ACTIONS: frozenset[str] = frozenset({
    'home', 'app_switcher', 'notification_shade', 'back',
    'search', 'lock_screen', 'settings', 'camera', 'dialer',
    'assistant', 'none',
})

# Slots materialized as system-level lisgd bindings at session start (see
# gesture_bindings.py). These also accept an IPC verb as their gestures.json
# value, so rebinding can retarget what lisgd fires over any focused app.
LISGD_SLOTS: frozenset[str] = frozenset({
    'swipe_up_short', 'swipe_up_long', 'swipe_down_top', 'swipe_left_edge',
})

# IPC gesture verbs the shell dispatches (main.py _on_ipc_command; the
# ipc.py protocol line). The complete valid verb set for LISGD_SLOTS.
IPC_VERBS: frozenset[str] = frozenset({
    'gesture.back', 'gesture.home', 'gesture.shade',
    'gesture.keyboard', 'gesture.switcher',
})


# Home side swipes: the app a swipe opens while no app is bound
# (Android: left opens the camera, right the dialer).
HOME_SWIPE_DEFAULTS: dict[str, str] = {
    'swipe_left_home': 'camera',
    'swipe_right_home': 'dialer',
}

# "Double Tap to Lock" is a switch: the slot holds lock_screen or none.
DOUBLE_TAP_VALUES = ('lock_screen', 'none')


def home_swipe_action(gestures, key: str) -> str:
    """Action a home side swipe runs: the bound app, else its default."""
    try:
        action = gestures.get(key) or 'none'
    except Exception:
        action = 'none'
    if action == 'none':
        return HOME_SWIPE_DEFAULTS.get(key, 'none')
    return action


def is_valid_action(action: str) -> bool:
    """Fixed actions plus 'launch:<app_id>' bindings for arbitrary apps."""
    if action in VALID_ACTIONS:
        return True
    return action.startswith('launch:') and len(action) > len('launch:')


def _is_valid_value(key: str, value: str) -> bool:
    """Actions are valid everywhere; verbs only for system-level slots."""
    if key == 'double_tap_home':
        return value in DOUBLE_TAP_VALUES
    if is_valid_action(value):
        return True
    if key == 'long_press_home' and value == 'edit':
        return True
    return key in LISGD_SLOTS and value in IPC_VERBS

# Human-readable names for the settings UI
ACTION_LABELS: dict[str, str] = {
    'edit':                'Edit mode',
    'home':                'Home',
    'app_switcher':        'App Switcher',
    'notification_shade':  'Notification Shade',
    'back':                'Back',
    'search':              'Search',
    'lock_screen':         'Lock Screen',
    'settings':            'Settings',
    'camera':              'Camera',
    'dialer':              'Dialer',
    'assistant':           'Assistant',
    'none':                'Do Nothing',
}

GESTURE_LABELS: dict[str, str] = {
    'swipe_down_top':       'Swipe down from top',
    'swipe_up_short':       'Short swipe up from bottom',
    'swipe_up_long':        'Long swipe up from bottom',
    'swipe_left_edge':      'Swipe in from right edge',
    'long_press_bottom':    'Long-press bottom edge',
    'double_tap_home':      'Double-tap home background',
    'long_press_home':      'Long-press home background',
    'swipe_left_home':      'Swipe left on home',
    'swipe_right_home':     'Swipe right on home',
    'squeeze':              'Squeeze (Active Edge)',
    'fingerprint_swipe':    'Fingerprint sensor swipe',
    'double_press_power':   'Double-press power button',
}


class GestureConfig:
    def __init__(self) -> None:
        self._path = _config_path()
        self._map: dict[str, str] = dict(_DEFAULTS)
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        disk: dict[str, str] = {}
        migrated = False
        for key, action in raw.items():
            if (key == 'double_tap_home' and isinstance(action, str)
                    and action not in DOUBLE_TAP_VALUES
                    and is_valid_action(action)):
                # Double-tap used to take any action; it is the Android
                # "Double Tap to Lock" switch now, so anything but the
                # lock turns it off.
                self._map[key] = 'none'
                migrated = True
                continue
            if key in _DEFAULTS and isinstance(action, str) and _is_valid_value(key, action):
                self._map[key] = action
                disk[key] = action
        # Previous schema defaulted both bottom-up slots to recents so the
        # short/long split was unused. Rewrite only that exact on-disk pair;
        # a file that set only one of them (or already has home/recents)
        # is left alone.
        if (disk.get('swipe_up_short') == 'app_switcher'
                and disk.get('swipe_up_long') == 'app_switcher'):
            self._map['swipe_up_short'] = 'home'
            migrated = True
        if migrated:
            self.save()

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._map, indent=2) + '\n', encoding='utf-8')

    def get(self, gesture: str) -> str:
        return self._map.get(gesture, 'none')

    def set(self, gesture: str, action: str) -> None:
        if gesture not in _DEFAULTS:
            raise ValueError(f'Unknown gesture: {gesture}')
        if not _is_valid_value(gesture, action):
            raise ValueError(f'Unknown action: {action}')
        self._map[gesture] = action
        self.save()

    def reset(self, gesture: str) -> None:
        if gesture in _DEFAULTS:
            self._map[gesture] = _DEFAULTS[gesture]
            self.save()

    def all(self) -> list[tuple[str, str]]:
        """Return (gesture_key, action) pairs in definition order."""
        return [(k, self._map.get(k, 'none')) for k in _DEFAULTS]
