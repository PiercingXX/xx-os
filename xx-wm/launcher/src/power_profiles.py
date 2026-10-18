"""Power profiles that work on phones.

power-profiles-daemon is used only while it runs with a REAL driver
(anything but its placeholder driver, which does nothing on ARM
phones). Otherwise the privileged helper sets the cpufreq governor per
policy (power-saver → powersave, balanced → schedutil, performance →
performance, each falling back within what the policy offers). The
choice is remembered in config and re-applied at session start; battery
saver always selects power-saver.

Pure stdlib: sysfs roots and the PPD property dict are injectable so
tests run on fake sysfs without D-Bus or root.
"""
from __future__ import annotations

import os

PROFILES = ('power-saver', 'balanced', 'performance')

# What Settings shows for each profile (the raw names stay the config
# and D-Bus values).
PROFILE_LABELS = {'power-saver': 'Power saver', 'balanced': 'Balanced',
                  'performance': 'Performance'}


def profile_label(name: str) -> str:
    """Display text for a profile name; anything else passes through."""
    return PROFILE_LABELS.get(str(name), str(name))

CPUFREQ_ROOT = '/sys/devices/system/cpu/cpufreq'

_DRIVER_KEYS = ('Driver', 'CpuDriver', 'PlatformDriver')
_PLACEHOLDER = 'placeholder'


def _cfg_get(config, key: str, default):
    try:
        get = getattr(config, 'get', None)
        if callable(get):
            return get(key, default)
        data = getattr(config, 'data', None)
        if isinstance(data, dict):
            return data.get(key, default)
        return getattr(config, key, default)
    except Exception:
        return default


def configured_profile(config) -> str:
    """Remembered profile choice, or '' when unset or invalid."""
    value = _cfg_get(config, 'power_profile', '')
    return value if value in PROFILES else ''


def effective_profile(config, saver_on: bool) -> str:
    """Profile to enforce: battery saver always wins, otherwise the
    remembered choice, defaulting to balanced."""
    if saver_on:
        return 'power-saver'
    return configured_profile(config) or 'balanced'


def has_real_driver(props) -> bool:
    """True when PPD's GetAll properties show a non-placeholder driver.

    Every profile entry stuck on the placeholder driver means setting a
    profile has no effect, so the shell must use the helper instead. An
    empty or missing profile list is unusable; entries without any
    driver detail (older daemons, unit fakes) count as real so a
    running daemon keeps working as before.
    """
    if not isinstance(props, dict):
        return False
    entries = props.get('Profiles') or []
    if not entries:
        return False
    seen = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        drivers = [str(entry.get(key)) for key in _DRIVER_KEYS
                   if entry.get(key) is not None]
        if not drivers:
            continue
        seen = True
        if any(driver != _PLACEHOLDER for driver in drivers):
            return True
    return not seen


def select_backend(ppd_name, props, cpufreq_present: bool) -> str:
    """'ppd' while the daemon runs with a real driver, else 'helper'
    while cpufreq policies exist, else 'none'."""
    if ppd_name and has_real_driver(props):
        return 'ppd'
    if cpufreq_present:
        return 'helper'
    return 'none'


def cpufreq_policies(root: str = CPUFREQ_ROOT) -> list[str]:
    """Every cpufreq policy dir under ``root``, sorted."""
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    return [os.path.join(root, name) for name in names
            if name.startswith('policy')
            and os.path.isdir(os.path.join(root, name))]


def cpufreq_present(root: str = CPUFREQ_ROOT) -> bool:
    return bool(cpufreq_policies(root))


def read_available_governors(policy_path: str) -> list[str]:
    try:
        with open(os.path.join(policy_path, 'scaling_available_governors'),
                  'r', encoding='utf-8') as handle:
            return handle.read().split()
    except OSError:
        return []


def read_governor(policy_path: str) -> str | None:
    try:
        with open(os.path.join(policy_path, 'scaling_governor'),
                  'r', encoding='utf-8') as handle:
            return handle.read().strip() or None
    except OSError:
        return None


def available_choices(backend: str, ppd_profiles) -> list[str]:
    """Profile names the picker offers for ``backend``."""
    if backend == 'ppd':
        return [str(name) for name in (ppd_profiles or ())
                if str(name) in PROFILES]
    if backend == 'helper':
        return list(PROFILES)
    return []


def current_label(active_ppd, config, saver_on: bool) -> str:
    """One live label for the Battery row and the Display picker: the
    saver wins, then the daemon's live profile, then the remembered
    choice."""
    if saver_on:
        return 'power-saver'
    if active_ppd in PROFILES:
        return str(active_ppd)
    return configured_profile(config) or 'Unknown'
