#!@python@
"""Privileged helper for XX-WM features that need root.

D-Bus system service ``io.piercingxx.XXWM.Helper1`` (D-Bus activated,
exits after 60 s idle). The shell itself never runs as root; it talks
to this service through :mod:`helper_client`.

Only ``Gio``/``GLib`` (imported lazily so the pure logic below stays
importable without PyGObject) and the standard library are used: no
``shell=True`` anywhere, ``nft`` is always invoked with argv lists.
"""
from __future__ import annotations

import argparse
import json
import os
import posixpath
import shutil
import subprocess
import sys
import time
from pathlib import PurePosixPath

BUS_NAME = 'io.piercingxx.XXWM.Helper1'
OBJECT_PATH = '/io/piercingxx/XXWM/Helper1'
INTERFACE = BUS_NAME

ERROR_NOT_AUTHORIZED = BUS_NAME + '.Error.NotAuthorized'
ERROR_INVALID_ARGS = 'org.freedesktop.DBus.Error.InvalidArgs'
ERROR_FAILED = 'org.freedesktop.DBus.Error.Failed'

ACTION_CHARGE = BUS_NAME + '.set-charge-limit'
ACTION_NETWORK = BUS_NAME + '.app-network'
ACTION_FREEZE = BUS_NAME + '.freeze'
ACTION_CLEAN = BUS_NAME + '.clean-system'
ACTION_READLOG = BUS_NAME + '.read-log'
ACTION_REFRESH = BUS_NAME + '.refresh-packages'
ACTION_UPGRADE = BUS_NAME + '.upgrade-system'
ACTION_REMOVE = BUS_NAME + '.remove-package'
ACTION_SERVICE = BUS_NAME + '.service-manage'
ACTION_SERVICE_REMOTE = BUS_NAME + '.service-remote'
ACTION_RFKILL = BUS_NAME + '.rfkill'
ACTION_FW_OPEN = BUS_NAME + '.firewall-open'
ACTION_FW_RESTRICT = BUS_NAME + '.firewall-restrict'
ACTION_POWER = BUS_NAME + '.power-profile'

# Destructive actions pass AllowUserInteraction (flag 1) so the in-shell
# polkit agent can prompt; harmless probes stay non-interactive (flag 0).
ALLOW_INTERACTION = 0x1

# Packages the RemovePackage method always refuses. Exact names plus the
# linux* / postmarketos-base* kernel families: removing any of these can
# leave the phone unbootable or without privilege escalation, the shell
# or its stack. Mirrored for UX pre-checks by settings_apps; a test pins
# the two implementations to the same verdicts.
PROTECTED_EXACT = frozenset({
    'base', 'alpine-base', 'musl', 'openrc', 'systemd', 'apk-tools',
    'pacman', 'doas', 'sudo',
    'xx-wm', 'gtk4', 'python3', 'phoc', 'squeekboard', 'networkmanager',
    'modemmanager',
})
PROTECTED_PREFIXES = ('linux', 'postmarketos-base')

# apk/pacman package names: lowercase alnum plus . + - _ (leading alnum).
# Anything else (spaces, slashes, shell metacharacters, NUL) is rejected
# before any backend runs, so the argv list can never escape the package
# slot. 128 chars bounds absurd input without touching real names.
_PKG_RE = None


def _pkg_re():
    global _PKG_RE
    if _PKG_RE is None:
        import re
        _PKG_RE = re.compile(r'[a-z0-9][a-z0-9.+_-]{0,127}')
    return _PKG_RE


def is_protected_package(name: str) -> bool:
    """True when ``name`` must never be removed through the helper."""
    lowered = str(name or '').lower()
    if lowered in PROTECTED_EXACT:
        return True
    return any(lowered.startswith(prefix)
               for prefix in PROTECTED_PREFIXES)


def validate_package_name(pkg: object) -> str:
    """Return ``pkg`` when it is a safe single package name.

    Raises InvalidArgs for anything malformed and NotAuthorized for the
    protected list (from the caller's perspective it is simply not
    permitted).
    """
    if not isinstance(pkg, str):
        raise HelperError(ERROR_INVALID_ARGS, 'package name is not text')
    if _pkg_re().fullmatch(pkg) is None:
        raise HelperError(ERROR_INVALID_ARGS,
                          'invalid package name: %r' % (pkg,))
    if is_protected_package(pkg):
        raise _denied('protected package: %s' % (pkg,))
    return pkg

POLKIT_BUS = 'org.freedesktop.PolicyKit1'
POLKIT_PATH = '/org/freedesktop/PolicyKit1/Authority'
POLKIT_IFACE = POLKIT_BUS + '.Authority'
DBUS_BUS = 'org.freedesktop.DBus'
DBUS_PATH = '/org/freedesktop/DBus'
DBUS_IFACE = DBUS_BUS

NFT_FAMILY = 'inet'
NFT_TABLE = 'xxwm'
NFT_CHAIN = 'output'

NFT_IN_TABLE = 'xx_wm'
NFT_IN_CHAIN = 'input'

NFT_IN_TABLE_LEGACY = 'xxwm-inbound'

INBOUND_FILE_NAME = 'inbound.json'
NFT_DROPIN_DIR = '/etc/nftables.d'
NFT_DROPIN_NAME = '10-xxwm.nft'

# Curated services ManageService may touch — nothing else, in
# particular never xx-wm itself, dbus or logind. Mirrors the
# ServicesSection catalog (launcher/src/settings_services.py); the
# ssh entry covers both spellings (Arch sshd.service, apt ssh.service)
# and adbd is the wireless-debugging daemon (apt only; hidden
# elsewhere).
SERVICE_ALLOW = ('modem', 'eg25', 'network', 'bluetooth', 'ssh',
                 'tailscale', 'waydroid', 'adbd')
SERVICE_ACTIONS = ('start', 'stop', 'restart', 'enable', 'disable')
SERVICE_UNITS = {
    'modem': {'systemd': ('ModemManager.service',),
              'openrc': ('modemmanager',)},
    'eg25': {'systemd': ('eg25-manager.service',),
             'openrc': ('eg25-manager',)},
    'network': {'systemd': ('NetworkManager.service',),
                'openrc': ('networkmanager',)},
    'bluetooth': {'systemd': ('bluetooth.service',),
                  'openrc': ('bluetooth',)},
    'ssh': {'systemd': ('sshd.service', 'ssh.service'),
            'openrc': ('sshd',)},
    'tailscale': {'systemd': ('tailscaled.service',),
                  'openrc': ('tailscale', 'tailscaled')},
    'waydroid': {'systemd': ('waydroid-container.service',),
                 'openrc': ('waydroid',)},
    'adbd': {'systemd': ('adbd.service',),
             'openrc': ('adbd',)},
}

RFKILL_ALLOW = ('wifi', 'bluetooth')

# Power profiles the SetPowerProfile method accepts. The helper maps each
# to a cpufreq governor per policy (phones like the FLX1 have no
# power-profiles-daemon driver, only these sysfs knobs).
POWER_PROFILES = ('power-saver', 'balanced', 'performance')

# Preferred governors per profile, first offered win. Only governors a
# policy lists in scaling_available_governors are ever written.
GOVERNOR_PREFERENCE = {
    'power-saver': ('powersave', 'conservative'),
    'balanced': ('schedutil', 'ondemand'),
    'performance': ('performance',),
}

CPUFREQ_REL = os.path.join('devices', 'system', 'cpu', 'cpufreq')

# Inbound presets SetInboundPreset may toggle. Mirrors the
# FirewallSection catalog (launcher/src/settings_firewall.py): ports are
# ints or (lo, hi) ranges per protocol.
INBOUND_PRESETS = {
    'nearby': {'tcp': (53317,), 'udp': (53317,)},
    'xxdb': {'tcp': ((37000, 37999),), 'udp': ()},
    'ssh': {'tcp': (22,), 'udp': ()},
    'kdeconnect': {'tcp': ((1714, 1764),), 'udp': ((1714, 1764),)},
    'syncthing': {'tcp': (22000,), 'udp': (22000, 21027)},
}

SYSFS_DEFAULT = '/sys'
CGROUP_ROOT_DEFAULT = '/sys/fs/cgroup'
MOUNTS_FILE_DEFAULT = '/proc/mounts'
STATE_DIR_DEFAULT = '/var/lib/xx-wm-helper'
STATE_FILE_NAME = 'state.json'

CHARGE_MIN = 50
CHARGE_MAX = 100
CHARGE_START_DELTA = 5

IDLE_EXIT_SECONDS = 60

INTROSPECT_XML = """\
<node>
  <interface name="io.piercingxx.XXWM.Helper1">
    <method name="GetCapabilities">
      <arg name="capabilities" type="a{sv}" direction="out"/>
    </method>
    <method name="SetChargeLimit">
      <arg name="percent" type="u" direction="in"/>
    </method>
    <method name="SetAppNetwork">
      <arg name="cgroup_path" type="s" direction="in"/>
      <arg name="allowed" type="b" direction="in"/>
    </method>
    <method name="FreezeCgroup">
      <arg name="cgroup_path" type="s" direction="in"/>
      <arg name="frozen" type="b" direction="in"/>
    </method>
    <method name="CleanSystem">
      <arg name="categories" type="as" direction="in"/>
      <arg name="cleaned" type="as" direction="out"/>
    </method>
    <method name="ReadLog">
      <arg name="name" type="s" direction="in"/>
      <arg name="text" type="s" direction="out"/>
    </method>
    <method name="RefreshPackageIndex">
    </method>
    <method name="UpgradeSystem">
      <arg name="output" type="s" direction="out"/>
    </method>
    <method name="RemovePackage">
      <arg name="pkg" type="s" direction="in"/>
    </method>
    <method name="ManageService">
      <arg name="name" type="s" direction="in"/>
      <arg name="action" type="s" direction="in"/>
    </method>
    <method name="SetRfkill">
      <arg name="type" type="s" direction="in"/>
      <arg name="blocked" type="b" direction="in"/>
    </method>
    <method name="SetPowerProfile">
      <arg name="profile" type="s" direction="in"/>
    </method>
    <method name="SetInboundPreset">
      <arg name="name" type="s" direction="in"/>
      <arg name="allowed" type="b" direction="in"/>
    </method>
    <method name="SetInboundAllowed">
      <arg name="names" type="as" direction="in"/>
    </method>
    <method name="SetFirewallEnabled">
      <arg name="enabled" type="b" direction="in"/>
    </method>
    <method name="GetFirewallState">
      <arg name="state" type="a{sv}" direction="out"/>
    </method>
  </interface>
</node>
"""

# Cache cleanup the shell may run as root. Fixed argv lists only: no
# package is ever removed, no firewall or service state changes, so the
# polkit action stays allow_active=yes like the other entries here.
CLEAN_SYSTEM_ALLOWLIST = {
    'apk-cache': ['apk', 'cache', 'clean'],
    'paccache-keep': ['paccache', '-rk2'],
    'paccache-uninstalled': ['paccache', '-ruk0'],
    'apt-clean': ['apt-get', 'clean'],
    'journal-vacuum': ['journalctl', '--vacuum-time=2weeks',
                       '--vacuum-size=500M'],
}

APK_CACHE_DIR_DEFAULT = '/etc/apk/cache'
SYSTEMD_DIR_DEFAULT = '/run/systemd/system'
LOG_DIR_DEFAULT = '/var/log'

# ReadLog allowlist: fixed names only, never free-form paths. 'dmesg'
# runs the kernel log at err/warn level (for dmesg_restrict=1); the
# rest are plain files under the log dir. Read-only, size-capped, so
# the polkit action stays allow_active=yes like the other entries here.
LOG_READ_ALLOWLIST = {
    'messages': 'messages',
    'xx-wm': 'xx-wm.log',
    'dmesg': None,
}
MAX_LOG_BYTES = 128 * 1024


class HelperError(Exception):
    """A helper failure mapped to a D-Bus error name."""

    def __init__(self, dbus_name: str, message: str) -> None:
        super().__init__(message)
        self.dbus_name = dbus_name


def _denied(message: str) -> HelperError:
    return HelperError(ERROR_NOT_AUTHORIZED, message)


def _failed(message: str) -> HelperError:
    return HelperError(ERROR_FAILED, message)


def clamp_charge_limit(percent: int) -> int:
    """Clamp a requested charge limit into the safe 50..100 window."""
    try:
        value = int(percent)
    except (TypeError, ValueError):
        raise _failed('charge limit is not a number') from None
    return max(CHARGE_MIN, min(CHARGE_MAX, value))


def _default_run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=30,
    )


class HelperCore:
    """Pure helper logic: sysfs/nft/cgroup work with injectable roots.

    ``sysfs``/``cgroup_root``/``mounts_file``/``state_file``/``nft_bin``
    exist so tests can point the helper at tmp dirs and a fake ``nft``;
    in production they keep their defaults. ``run`` replaces
    :func:`subprocess.run` (same argv-list contract, no shell).
    """

    def __init__(
        self,
        *,
        sysfs: str = SYSFS_DEFAULT,
        cgroup_root: str = CGROUP_ROOT_DEFAULT,
        mounts_file: str = MOUNTS_FILE_DEFAULT,
        state_file: str = os.path.join(STATE_DIR_DEFAULT, STATE_FILE_NAME),
        nft_bin: str = 'nft',
        nft_dropin_dir: str = NFT_DROPIN_DIR,
        log_dir: str = LOG_DIR_DEFAULT,
        run=_default_run,
    ) -> None:
        self.sysfs = sysfs
        self.cgroup_root = cgroup_root
        self.mounts_file = mounts_file
        self.state_file = state_file
        self.nft_bin = nft_bin
        self.nft_dropin_dir = nft_dropin_dir
        self.log_dir = log_dir
        self._run = run

    # ---------------------------------------------------------- capabilities

    def battery_dirs(self) -> list[str]:
        """Every power_supply entry exposing a charge-end threshold."""
        base = os.path.join(self.sysfs, 'class', 'power_supply')
        try:
            names = sorted(os.listdir(base))
        except OSError:
            return []
        found = []
        for name in names:
            end = os.path.join(base, name, 'charge_control_end_threshold')
            if os.path.isfile(end):
                found.append(os.path.join(base, name))
        return found

    def nft_available(self) -> bool:
        return shutil.which(self.nft_bin) is not None

    def cgroup2_mounted(self) -> bool:
        """True when ``cgroup_root`` sits under a mounted cgroup2 hierarchy."""
        if not os.path.isdir(self.cgroup_root):
            return False
        try:
            with open(self.mounts_file, 'r', encoding='utf-8') as handle:
                for line in handle:
                    fields = line.split()
                    if len(fields) >= 3 and fields[2] == 'cgroup2':
                        mountpoint = fields[1]
                        if (self.cgroup_root == mountpoint
                                or self.cgroup_root.startswith(mountpoint + '/')):
                            return True
        except OSError:
            return False
        return False

    def get_capabilities(self) -> dict:
        """Capability probe for the shell (no authorization required)."""
        return {
            'charge_limit_supported': bool(self.battery_dirs()),
            'nftables': self.nft_available(),
            'cgroup2': self.cgroup2_mounted(),
            'freezer': os.path.isfile(
                os.path.join(self.cgroup_root, 'cgroup.freeze')),
        }

    # ------------------------------------------------------------ charge limit

    def _write_file(self, path: str, text: str) -> None:
        try:
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(text)
        except OSError as err:
            raise _failed('%s: %s' % (path, err.strerror or err)) from None

    def set_charge_limit(self, percent: int) -> int:
        """Clamp ``percent`` to 50..100, write every battery, persist."""
        limit = clamp_charge_limit(percent)
        batteries = self.battery_dirs()
        if not batteries:
            raise _failed('no battery with charge_control_end_threshold found')
        for battery in batteries:
            self._write_file(
                os.path.join(battery, 'charge_control_end_threshold'),
                '%d\n' % limit,
            )
            start = os.path.join(battery, 'charge_control_start_threshold')
            if os.path.isfile(start):
                self._write_file(start, '%d\n' % (limit - CHARGE_START_DELTA))
        state = self.load_state()
        state['charge_limit'] = limit
        self.save_state(state)
        return limit

    # -------------------------------------------------------- path validation

    def user_slice_prefix(self, uid: int) -> str:
        return '%s/user.slice/user-%d.slice' % (self.cgroup_root, uid)

    def validate_app_cgroup(self, path: str, uid: int) -> str:
        """Confine ``path`` to the caller's own user slice.

        Anything malformed (non-absolute, ``..``, redundant separators,
        trailing slash, NUL) or outside
        ``<cgroup>/user.slice/user-<uid>.slice/`` raises NotAuthorized —
        from the caller's perspective it is simply not permitted. Symlinks
        are resolved too so a link inside the slice cannot point out.
        """
        prefix = self.user_slice_prefix(int(uid))
        if not isinstance(path, str) or not path or '\x00' in path:
            raise _denied('invalid cgroup path')
        if not posixpath.isabs(path):
            raise _denied('cgroup path must be absolute: %r' % (path,))
        if posixpath.normpath(path) != path:
            raise _denied('cgroup path is not canonical: %r' % (path,))
        if path != prefix and not path.startswith(prefix + '/'):
            raise _denied('cgroup path outside your user slice: %r' % (path,))
        real = os.path.realpath(path)
        real_prefix = os.path.realpath(prefix)
        if real != real_prefix and not real.startswith(real_prefix + '/'):
            raise _denied('cgroup path escapes your user slice: %r' % (path,))
        return path

    # ------------------------------------------------------------------ nft

    def cgroup_nft_level(self, path: str) -> int:
        """Depth of ``path`` below the cgroup2 mount for ``socket cgroupv2
        level N`` matching: nft compares the ancestor at that depth, so a
        full-path match needs the path's own depth."""
        rel = posixpath.relpath(path, self.cgroup_root)
        if rel == '.':
            return 1
        return max(1, len(PurePosixPath(rel).parts))

    def cgroup_nft_match(self, path: str) -> str:
        """Match string for ``path``: relative to the cgroup2 mount (as the
        kernel reports it) and double-quoted inside the argv element so
        systemd-style escapes (``\\\\x2d``) survive nft's own parsing."""
        rel = posixpath.relpath(path, self.cgroup_root)
        return '"%s"' % rel

    def _nft(self, argv: list[str], *, allow_exists: bool = False) -> str:
        cmd = [self.nft_bin] + argv
        try:
            proc = self._run(cmd)
        except OSError as err:
            raise _failed('nft failed to start: %s' % err) from None
        stderr = getattr(proc, 'stderr', '') or ''
        if proc.returncode != 0:
            if allow_exists and ('already exists' in stderr
                                 or 'File exists' in stderr):
                return ''
            raise _failed('nft %s: %s' % (
                ' '.join(argv), stderr.strip() or 'exit %d' % proc.returncode))
        return getattr(proc, 'stdout', '') or ''

    def apply_blocked(self, blocked: list[str]) -> None:
        """Reconcile the ``inet xxwm`` table with ``blocked`` cgroup paths:
        ensure table + output chain, flush our chain, add one drop rule
        per path. Our chain is the only thing flushed — other tables are
        never touched."""
        self._nft(['add', 'table', NFT_FAMILY, NFT_TABLE], allow_exists=True)
        self._nft([
            'add', 'chain', NFT_FAMILY, NFT_TABLE, NFT_CHAIN,
            '{', 'type', 'filter', 'hook', 'output', 'priority', '0',
            ';', 'policy', 'accept', ';', '}',
        ], allow_exists=True)
        self._nft(['flush', 'chain', NFT_FAMILY, NFT_TABLE, NFT_CHAIN])
        for path in sorted(blocked):
            self._nft([
                'add', 'rule', NFT_FAMILY, NFT_TABLE, NFT_CHAIN,
                'socket', 'cgroupv2', 'level',
                str(self.cgroup_nft_level(path)),
                self.cgroup_nft_match(path), 'drop',
            ])

    def set_app_network(self, cgroup_path: str, allowed: bool, uid: int) -> bool:
        """Block (``allowed`` False) or unblock a caller-owned app cgroup,
        then persist the blocked set and re-apply the nft chain."""
        path = self.validate_app_cgroup(cgroup_path, uid)
        if not self.nft_available():
            raise _failed('nftables not available')
        state = self.load_state()
        blocked = [p for p in state.get('blocked', []) if isinstance(p, str)]
        if allowed and path in blocked:
            blocked.remove(path)
        elif not allowed and path not in blocked:
            blocked.append(path)
        state['blocked'] = sorted(blocked)
        self.apply_blocked(state['blocked'])
        self.save_state(state)
        return bool(allowed)

    # -------------------------------------------------------- clean system

    def clean_system(self, categories, *,
                     apk_cache_dir: str = APK_CACHE_DIR_DEFAULT,
                     systemd_dir: str = SYSTEMD_DIR_DEFAULT) -> dict:
        """Run fixed-allowlist cache cleanups, skipping unusable entries.

        ``categories`` must all name :data:`CLEAN_SYSTEM_ALLOWLIST`;
        anything else raises InvalidArgs, so callers can never smuggle
        in a free-form command. ``apk-cache`` runs only when the apk
        cache dir exists, ``journal-vacuum`` only on systemd, and any
        entry whose binary is missing is skipped. Returns
        ``{'cleaned': [...], 'skipped': [...]}``.
        """
        if not isinstance(categories, (list, tuple)):
            raise HelperError(ERROR_INVALID_ARGS,
                              'categories must be a string array')
        selected = []
        for category in categories:
            if not isinstance(category, str) \
                    or category not in CLEAN_SYSTEM_ALLOWLIST:
                raise HelperError(ERROR_INVALID_ARGS,
                                  'unknown clean category: %r' % (category,))
            if category not in selected:
                selected.append(category)
        cleaned: list[str] = []
        skipped: list[str] = []
        for category in selected:
            if category == 'apk-cache' \
                    and not os.path.isdir(apk_cache_dir):
                skipped.append(category)
                continue
            if category == 'journal-vacuum' \
                    and not os.path.isdir(systemd_dir):
                skipped.append(category)
                continue
            argv = CLEAN_SYSTEM_ALLOWLIST[category]
            if shutil.which(argv[0]) is None:
                skipped.append(category)
                continue
            try:
                proc = self._run(argv)
            except OSError as err:
                raise _failed('clean %s failed to start: %s'
                              % (category, err)) from None
            if getattr(proc, 'returncode', 1) == 127:
                skipped.append(category)
                continue
            if getattr(proc, 'returncode', 1) != 0:
                stderr = (getattr(proc, 'stderr', '') or '').strip()
                raise _failed('clean %s: %s'
                              % (category, stderr or 'exit %d'
                                 % getattr(proc, 'returncode', 1)))
            cleaned.append(category)
        return {'cleaned': cleaned, 'skipped': skipped}

    # -------------------------------------------------------------- read log

    def read_log(self, name) -> str:
        """Tail of an allowlisted system log, capped at MAX_LOG_BYTES.

        ``name`` must be exactly one of LOG_READ_ALLOWLIST — anything
        else raises InvalidArgs, so callers can never smuggle in a
        free-form path. Read-only: no shell, no writes, no secrets
        filtering here (the shell scrubs before display).
        """
        if not isinstance(name, str) or name not in LOG_READ_ALLOWLIST:
            raise HelperError(ERROR_INVALID_ARGS,
                              'unknown log name: %r' % (name,))
        if name == 'dmesg':
            try:
                proc = self._run(['dmesg', '--level=err,warn'])
            except OSError as err:
                raise _failed('dmesg failed to start: %s' % err) from None
            if getattr(proc, 'returncode', 1) != 0:
                stderr = (getattr(proc, 'stderr', '') or '').strip()
                raise _failed('dmesg: %s' % (
                    stderr or 'exit %d' % getattr(proc, 'returncode', 1)))
            return self._cap_log(getattr(proc, 'stdout', '') or '')
        path = os.path.join(self.log_dir, LOG_READ_ALLOWLIST[name])
        try:
            with open(path, 'rb') as handle:
                handle.seek(0, 2)
                size = handle.tell()
                handle.seek(max(0, size - MAX_LOG_BYTES))
                data = handle.read(MAX_LOG_BYTES + 1)
        except OSError as err:
            raise _failed('%s: %s' % (path, err.strerror or err)) from None
        text = data.decode('utf-8', errors='replace')
        if size > MAX_LOG_BYTES:
            cut = text.find('\n')
            text = text[cut + 1:] if cut != -1 else text
        return text

    @staticmethod
    def _cap_log(text: str) -> str:
        data = text.encode('utf-8', errors='replace')
        if len(data) > MAX_LOG_BYTES:
            text = data[-MAX_LOG_BYTES:].decode('utf-8',
                                                errors='replace')
            cut = text.find('\n')
            text = text[cut + 1:] if cut != -1 else text
        return text

    # ---------------------------------------------------------------- freeze

    def freeze_cgroup(self, cgroup_path: str, frozen: bool, uid: int) -> bool:
        """Write cgroup.freeze for a caller-owned cgroup (cgroup v2).

        Used on devices without systemd's FreezeUnit. Freeze state is
        inherently transient, so unlike the charge limit and the blocked
        set it is not persisted."""
        path = self.validate_app_cgroup(cgroup_path, uid)
        if not self.cgroup2_mounted():
            raise _failed('cgroup v2 not mounted')
        self._write_file(os.path.join(path, 'cgroup.freeze'),
                         '1\n' if frozen else '0\n')
        return bool(frozen)

    # ------------------------------------------------- software maintenance

    def _backend(self, which=None) -> str | None:
        """'apk' when present, else 'pacman', else None. ``which`` is
        injectable so tests never probe the real machine."""
        which_fn = which or shutil.which
        if which_fn('apk'):
            return 'apk'
        if which_fn('pacman'):
            return 'pacman'
        return None

    def refresh_package_index(self, which=None) -> None:
        """Refresh the apk index (``apk update``): harmless and
        non-destructive, so its policy stays ``allow_active=yes``.

        Never ``pacman -Sy``: a partial pacman sync breaks the system on
        Arch, so Arch counts come from ``checkupdates`` (own temp db) and
        upgrades run in a terminal instead."""
        which_fn = which or shutil.which
        if not which_fn('apk'):
            raise _failed('apk not available on this system')
        try:
            proc = self._run(['apk', 'update'])
        except OSError as err:
            raise _failed('apk update failed to start: %s' % err) from None
        if proc.returncode != 0:
            raise _failed('apk update: %s' % (
                (getattr(proc, 'stderr', '') or '').strip()
                or 'exit %d' % proc.returncode))

    def upgrade_system(self, which=None) -> str:
        """Run ``apk upgrade`` and return the combined output tail for the
        shell's update log. Interactive ``auth_admin_keep``: the agent
        prompts for the device password."""
        if (which or shutil.which)('apk') is None:
            raise _failed('apk not available on this system')
        try:
            proc = self._run(['apk', 'upgrade'])
        except OSError as err:
            raise _failed('apk upgrade failed to start: %s' % err) from None
        output = ((getattr(proc, 'stdout', '') or '')
                  + (getattr(proc, 'stderr', '') or ''))
        if proc.returncode != 0:
            raise _failed('apk upgrade: %s' % (
                output.strip()[-2000:] or 'exit %d' % proc.returncode))
        return output[-4000:]

    def remove_package(self, pkg: str, which=None) -> str:
        """Remove one validated, non-protected distro package.

        ``auth_admin_keep`` with interaction: the agent prompts for the
        device password. Returns the backend used ('apk' or 'pacman').
        """
        name = validate_package_name(pkg)
        backend = self._backend(which)
        if backend is None:
            raise _failed('no supported package manager on this system')
        argv = (['apk', 'del', name] if backend == 'apk'
                else ['pacman', '-Rns', '--noconfirm', name])
        try:
            proc = self._run(argv)
        except OSError as err:
            raise _failed('%s failed to start: %s' % (argv[0], err)) from None
        if proc.returncode != 0:
            raise _failed('%s %s: %s' % (
                argv[0], name,
                ((getattr(proc, 'stderr', '') or '').strip()
                 or 'exit %d' % proc.returncode)))
        return backend

    # ------------------------------------------------------- init/services

    def _run_cmd(self, argv: list[str]) -> str:
        """Run an argv list (never a shell); nonzero exits become Failed."""
        try:
            proc = self._run(argv)
        except OSError as err:
            raise _failed('%s failed to start: %s' % (argv[0], err)) from None
        stderr = getattr(proc, 'stderr', '') or ''
        if proc.returncode != 0:
            raise _failed('%s: %s' % (
                ' '.join(argv), stderr.strip() or 'exit %d' % proc.returncode))
        return getattr(proc, 'stdout', '') or ''

    def init_system(self) -> str:
        """'systemd' when its runtime dir exists (never inferred from the
        package manager: pmOS ships apk with OpenRC or systemd), 'openrc'
        when rc-service is installed, else 'unknown'."""
        if os.path.isdir('/run/systemd/system'):
            return 'systemd'
        if shutil.which('rc-service') is not None:
            return 'openrc'
        return 'unknown'

    def resolve_unit(self, key: str) -> str:
        """Concrete unit/service name for `key` on this init, probing the
        known spellings (sshd.service vs ssh.service) in order."""
        table = SERVICE_UNITS[key]
        init = self.init_system()
        if init == 'systemd':
            for unit in table['systemd']:
                try:
                    self._run_cmd(['systemctl', 'cat', unit])
                    return unit
                except HelperError:
                    continue
            return table['systemd'][0]
        if init == 'openrc':
            for name in table['openrc']:
                if os.path.isfile(os.path.join('/etc', 'init.d', name)):
                    return name
            return table['openrc'][0]
        raise _failed('unsupported init system: %s' % init)

    def manage_service(self, name: str, action: str) -> str:
        """start/stop/restart/enable/disable one allowlisted service.

        Anything outside SERVICE_ALLOW/SERVICE_ACTIONS is InvalidArgs —
        this is the only gate, so no caller can reach another unit, let
        alone xx-wm, dbus or logind. Argv lists only, never a shell."""
        if name not in SERVICE_ALLOW or action not in SERVICE_ACTIONS:
            raise HelperError(
                ERROR_INVALID_ARGS,
                'unknown service or action: %r %r' % (name, action))
        unit = self.resolve_unit(name)
        init = self.init_system()
        if init == 'systemd':
            if action in ('start', 'stop', 'restart'):
                self._run_cmd(['systemctl', action, unit])
            else:
                self._run_cmd(['systemctl', action, unit])
        elif init == 'openrc':
            if action in ('start', 'stop', 'restart'):
                self._run_cmd(['rc-service', unit, action])
            elif action == 'enable':
                self._run_cmd(['rc-update', 'add', unit, 'default'])
            else:
                self._run_cmd(['rc-update', 'del', unit, 'default'])
        else:
            raise _failed('unsupported init system: %s' % init)
        return action

    # ---------------------------------------------------------------- rfkill

    def set_rfkill(self, rtype: str, blocked: bool) -> bool:
        """Block/unblock the wifi or bluetooth rfkill switch. Only those
        two types are accepted; anything else is InvalidArgs."""
        if rtype not in RFKILL_ALLOW:
            raise HelperError(ERROR_INVALID_ARGS,
                              'unknown rfkill type: %r' % (rtype,))
        self._run_cmd(['rfkill', 'block' if blocked else 'unblock', rtype])
        return bool(blocked)

    # ------------------------------------------------------- power profile

    def cpufreq_root(self) -> str:
        return os.path.join(self.sysfs, CPUFREQ_REL)

    def cpufreq_policies(self) -> list[str]:
        """Every cpufreq policy dir, sorted. Empty without the driver."""
        root = self.cpufreq_root()
        try:
            names = sorted(os.listdir(root))
        except OSError:
            return []
        return [os.path.join(root, name) for name in names
                if name.startswith('policy')
                and os.path.isdir(os.path.join(root, name))]

    def policy_available_governors(self, policy: str) -> list[str]:
        try:
            with open(os.path.join(policy, 'scaling_available_governors'),
                      'r', encoding='utf-8') as handle:
                return handle.read().split()
        except OSError:
            return []

    @staticmethod
    def governor_for_profile(profile: str, available) -> str | None:
        """First preferred governor the policy offers, else None (the
        policy is then left alone, never forced to an unoffered value)."""
        for governor in GOVERNOR_PREFERENCE.get(profile, ()):
            if governor in (available or ()):
                return governor
        return None

    def set_power_profile(self, profile: str) -> str:
        """Apply a power profile through the cpufreq governors.

        ``profile`` must name POWER_PROFILES — anything else is
        InvalidArgs, so callers can never smuggle in a free-form governor
        or path. Only governors the policy offers are written; policies
        offering none of the preferred governors are skipped. The choice
        persists for the boot re-apply. Writing governors is
        non-destructive, so the polkit action stays allow_active=yes.
        """
        if profile not in POWER_PROFILES:
            raise HelperError(ERROR_INVALID_ARGS,
                              'unknown power profile: %r' % (profile,))
        policies = self.cpufreq_policies()
        if not policies:
            raise _failed('no cpufreq policies found')
        for policy in policies:
            governor = self.governor_for_profile(
                profile, self.policy_available_governors(policy))
            if governor is not None:
                self._write_file(
                    os.path.join(policy, 'scaling_governor'),
                    '%s\n' % governor)
        state = self.load_state()
        state['power_profile'] = profile
        self.save_state(state)
        return profile

    # ------------------------------------------------------- inbound firewall

    def inbound_file(self) -> str:
        directory = os.path.dirname(self.state_file) or '.'
        return os.path.join(directory, INBOUND_FILE_NAME)

    def load_inbound(self) -> dict:
        """Persisted ``{'enabled': bool|None, 'allowed': [preset],
        'migrated': bool}``; corrupt state degrades to defaults.
        ``migrated`` marks the one-time upgrade from the old per-preset
        rows to the feature-derived ruleset (read once, then left
        alone)."""
        try:
            with open(self.inbound_file(), 'r', encoding='utf-8') as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            return {'enabled': None, 'allowed': [], 'migrated': False}
        if not isinstance(raw, dict):
            return {'enabled': None, 'allowed': [], 'migrated': False}
        enabled = raw.get('enabled')
        allowed = raw.get('allowed')
        names = [name for name in allowed
                 if isinstance(name, str) and name in INBOUND_PRESETS] \
            if isinstance(allowed, list) else []
        return {
            'enabled': enabled if isinstance(enabled, bool) else None,
            'allowed': sorted(names),
            'migrated': bool(raw.get('migrated')),
        }

    def save_inbound(self, state: dict) -> None:
        directory = os.path.dirname(self.inbound_file()) or '.'
        try:
            os.makedirs(directory, mode=0o755, exist_ok=True)
            tmp = os.path.join(directory, '.inbound.json.tmp')
            with open(tmp, 'w', encoding='utf-8') as handle:
                json.dump(state, handle, indent=2, sort_keys=True)
                handle.write('\n')
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.inbound_file())
        except OSError as err:
            raise _failed('cannot persist inbound state: %s'
                          % (err.strerror or err)) from None

    def firewall_backend(self) -> str:
        """'nftables' when nft exists (present everywhere: a drop-in
        under /etc/nftables.d), else 'ufw' when installed, else 'none'."""
        if self.nft_available():
            return 'nftables'
        if shutil.which('ufw') is not None:
            return 'ufw'
        return 'none'

    @staticmethod
    def ufw_specs(name: str) -> list[str]:
        """ufw rule specs for a preset (`53317/tcp`, `1714:1764/udp`)."""
        preset = INBOUND_PRESETS[name]

        def _spec(port, proto: str) -> str:
            if isinstance(port, tuple):
                return '%d:%d/%s' % (port[0], port[1], proto)
            return '%d/%s' % (port, proto)

        return ([_spec(port, 'tcp') for port in preset['tcp']]
                + [_spec(port, 'udp') for port in preset['udp']])

    @staticmethod
    def nft_port(port) -> str:
        if isinstance(port, tuple):
            return '%d-%d' % (port[0], port[1])
        return str(port)

    def render_inbound_nft(self, allowed) -> str:
        lines = ['# xx-wm inbound presets (managed; do not edit).',
                 '# An accept here cannot override a drop in another '
                 'base chain.',
                 'table inet %s {' % NFT_IN_TABLE,
                 '    chain %s {' % NFT_IN_CHAIN,
                 '        type filter hook input priority filter; '
                 'policy accept;']
        for name in sorted(allowed):
            preset = INBOUND_PRESETS[name]
            for port in preset['tcp']:
                lines.append('        tcp dport %s accept comment "xxwm %s";'
                             % (self.nft_port(port), name))
            for port in preset['udp']:
                lines.append('        udp dport %s accept comment "xxwm %s";'
                             % (self.nft_port(port), name))
        lines += ['    }', '}']
        return '\n'.join(lines) + '\n'

    def write_inbound_dropin(self, allowed) -> None:
        """Persist the pmOS-style nftables drop-in for the allowed set."""
        try:
            os.makedirs(self.nft_dropin_dir, mode=0o755, exist_ok=True)
            tmp = os.path.join(self.nft_dropin_dir,
                               '.%s.tmp' % NFT_DROPIN_NAME)
            with open(tmp, 'w', encoding='utf-8') as handle:
                handle.write(self.render_inbound_nft(allowed))
            os.chmod(tmp, 0o644)
            os.replace(tmp, os.path.join(self.nft_dropin_dir,
                                         NFT_DROPIN_NAME))
        except OSError as err:
            raise _failed('cannot write nftables drop-in: %s'
                          % (err.strerror or err)) from None

    def _drop_legacy_inbound(self) -> None:
        """Best-effort removal of the pre-rename owned table; anything
        else on the system is never touched."""
        try:
            self._nft(['delete', 'table', NFT_FAMILY,
                       NFT_IN_TABLE_LEGACY])
        except HelperError:
            pass

    def _remove_inbound_dropin(self) -> None:
        """Best-effort removal of our nftables drop-in (firewall off
        means no table, live or at boot)."""
        try:
            os.remove(os.path.join(self.nft_dropin_dir, NFT_DROPIN_NAME))
        except OSError:
            pass

    def _clear_inbound_nft(self) -> None:
        """Remove the owned live table and drop-in; other tables are
        never touched."""
        try:
            self._nft(['flush', 'chain', NFT_FAMILY, NFT_IN_TABLE,
                       NFT_IN_CHAIN])
        except HelperError:
            pass
        try:
            self._nft(['delete', 'table', NFT_FAMILY, NFT_IN_TABLE])
        except HelperError:
            pass
        self._drop_legacy_inbound()
        self._remove_inbound_dropin()

    def apply_inbound(self, allowed) -> None:
        """Reconcile the live `inet xx_wm` table with `allowed`:
        our own table and chain only — other tables are never touched."""
        self._drop_legacy_inbound()
        self._nft(['add', 'table', NFT_FAMILY, NFT_IN_TABLE],
                  allow_exists=True)
        self._nft([
            'add', 'chain', NFT_FAMILY, NFT_IN_TABLE, NFT_IN_CHAIN,
            '{', 'type', 'filter', 'hook', 'input', 'priority', 'filter',
            ';', 'policy', 'accept', ';', '}',
        ], allow_exists=True)
        self._nft(['flush', 'chain', NFT_FAMILY, NFT_IN_TABLE, NFT_IN_CHAIN])
        for name in sorted(allowed):
            preset = INBOUND_PRESETS[name]
            for port in preset['tcp']:
                self._nft(['add', 'rule', NFT_FAMILY, NFT_IN_TABLE,
                           NFT_IN_CHAIN, 'tcp', 'dport',
                           self.nft_port(port), 'accept'])
            for port in preset['udp']:
                self._nft(['add', 'rule', NFT_FAMILY, NFT_IN_TABLE,
                           NFT_IN_CHAIN, 'udp', 'dport',
                           self.nft_port(port), 'accept'])

    def _nft_preset_allowed(self, ruleset: str, name: str) -> bool:
        preset = INBOUND_PRESETS[name]
        for port in list(preset['tcp']) + list(preset['udp']):
            if 'dport %s' % self.nft_port(port) not in ruleset:
                return False
        return True

    def get_firewall_state(self) -> dict:
        """Backend, on/off and per-preset state for the section. The ufw
        answer is the live ruleset; the nftables answer is the live
        ruleset plus our persisted set. `foreign_drop` flags a drop in
        another base chain, which our accepts cannot override."""
        backend = self.firewall_backend()
        if backend == 'ufw':
            try:
                proc = self._run(['ufw', 'status'])
                status = getattr(proc, 'stdout', '') or ''
            except OSError as err:
                raise _failed('ufw status failed: %s' % err) from None
            lines = status.splitlines()
            enabled = bool(lines) and lines[0].strip() == 'Status: active'
            return {
                'backend': 'ufw',
                'enabled': enabled,
                'allowed': {name: all(spec in status for spec in
                                      self.ufw_specs(name))
                            for name in sorted(INBOUND_PRESETS)},
                'foreign_drop': enabled,
            }
        if backend == 'nftables':
            try:
                proc = self._run(['nft', 'list', 'ruleset'])
                ruleset = getattr(proc, 'stdout', '') or ''
            except OSError as err:
                raise _failed('nft list failed: %s' % err) from None
            installed = 'table %s %s' % (NFT_FAMILY, NFT_IN_TABLE) in ruleset
            persisted = self.load_inbound()
            return {
                'backend': 'nftables',
                'enabled': installed,
                'allowed': {name: (self._nft_preset_allowed(ruleset, name)
                                   if installed
                                   else name in persisted['allowed'])
                            for name in sorted(INBOUND_PRESETS)},
                'foreign_drop': 'policy drop' in ruleset,
            }
        return {'backend': 'none', 'enabled': False, 'allowed': {},
                'foreign_drop': False}

    def set_inbound_preset(self, name: str, allowed: bool) -> bool:
        """Allow (open) or forbid (close) one preset's ports. Presets only:
        anything outside INBOUND_PRESETS is InvalidArgs."""
        if name not in INBOUND_PRESETS:
            raise HelperError(ERROR_INVALID_ARGS,
                              'unknown inbound preset: %r' % (name,))
        backend = self.firewall_backend()
        if backend == 'ufw':
            for spec in self.ufw_specs(name):
                if allowed:
                    self._run_cmd(['ufw', 'allow', spec])
                else:
                    self._run_cmd(['ufw', '--force', 'delete',
                                   'allow', spec])
        elif backend == 'nftables':
            if not self.nft_available():
                raise _failed('nftables not available')
            state = self.load_inbound()
            current = set(state['allowed'])
            if allowed:
                current.add(name)
            else:
                current.discard(name)
            state['allowed'] = sorted(current)
            self.apply_inbound(state['allowed'])
            self.write_inbound_dropin(state['allowed'])
            self.save_inbound(state)
        else:
            raise _failed('no firewall backend available')
        if backend == 'ufw':
            state = self.load_inbound()
            current = set(state['allowed'])
            if allowed:
                current.add(name)
            else:
                current.discard(name)
            state['allowed'] = sorted(current)
            self.save_inbound(state)
        return bool(allowed)

    def set_firewall_enabled(self, enabled: bool) -> bool:
        """Enable (restrict incoming) or disable the firewall. Enabling
        keeps SSH reachable on a remote box (ref firewall-manager.sh):
        port 22 is allowed when SSH_CONNECTION is set."""
        backend = self.firewall_backend()
        if backend == 'ufw':
            if enabled:
                self._run_cmd(['ufw', 'default', 'deny', 'incoming'])
                self._run_cmd(['ufw', 'default', 'allow', 'outgoing'])
                if os.environ.get('SSH_CONNECTION'):
                    self._run_cmd(['ufw', 'allow', '22/tcp'])
                self._run_cmd(['ufw', '--force', 'enable'])
            else:
                self._run_cmd(['ufw', 'disable'])
        elif backend == 'nftables':
            if enabled:
                state = self.load_inbound()
                self.apply_inbound(state['allowed'])
                self.write_inbound_dropin(state['allowed'])
                state['enabled'] = True
                self.save_inbound(state)
            else:
                self._clear_inbound_nft()
                state = self.load_inbound()
                state['enabled'] = False
                self.save_inbound(state)
        else:
            raise _failed('no firewall backend available')
        return bool(enabled)

    def _reconcile_ufw(self, wanted: list) -> None:
        """Bring the ufw ruleset to ``wanted``: allow missing preset
        specs, delete present specs outside it. Argv lists only."""
        try:
            proc = self._run(['ufw', 'status'])
            status = getattr(proc, 'stdout', '') or ''
        except OSError as err:
            raise _failed('ufw status failed: %s' % err) from None
        wanted_set = set(wanted)
        for name in sorted(INBOUND_PRESETS):
            for spec in self.ufw_specs(name):
                if name in wanted_set and spec not in status:
                    self._run_cmd(['ufw', 'allow', spec])
                elif name not in wanted_set and spec in status:
                    self._run_cmd(['ufw', '--force', 'delete',
                                   'allow', spec])

    def set_inbound_allowed(self, names) -> list:
        """Replace the whole owned inbound set with the features'
        derived presets (``SetInboundAllowed``).

        Presets only: anything outside INBOUND_PRESETS is InvalidArgs.
        Migration: the first call unions the derived set with the old
        per-preset rows' stored allow-state, so a previously allowed
        port stays allowed (its feature counts as on for the firewall
        only) without starting any service; the stored keys are read
        once, then left alone. Later calls derive purely.
        Firewall off (``enabled`` False) persists the set but leaves
        no table behind.
        """
        if not isinstance(names, (list, tuple, set, frozenset)):
            raise HelperError(ERROR_INVALID_ARGS,
                              'inbound names must be a list: %r' % (names,))
        wanted = sorted(names)
        for name in wanted:
            if name not in INBOUND_PRESETS:
                raise HelperError(ERROR_INVALID_ARGS,
                                  'unknown inbound preset: %r' % (name,))
        state = self.load_inbound()
        if not state['migrated']:
            wanted = sorted(set(wanted) | set(state['allowed']))
            state['migrated'] = True
        state['allowed'] = wanted
        backend = self.firewall_backend()
        if backend == 'ufw':
            self._reconcile_ufw(wanted)
        elif backend == 'nftables':
            if state.get('enabled') is False:
                self._clear_inbound_nft()
            else:
                self.apply_inbound(wanted)
                self.write_inbound_dropin(wanted)
        else:
            raise _failed('no firewall backend available')
        self.save_inbound(state)
        return wanted

    # ----------------------------------------------------------------- state

    def load_state(self) -> dict:
        """Saved ``{'charge_limit': int|None, 'blocked': [str],
        'power_profile': str|None}``; missing or corrupt state degrades
        to defaults rather than failing boot."""
        try:
            with open(self.state_file, 'r', encoding='utf-8') as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            return {'charge_limit': None, 'blocked': [],
                    'power_profile': None}
        if not isinstance(raw, dict):
            return {'charge_limit': None, 'blocked': [],
                    'power_profile': None}
        limit = raw.get('charge_limit')
        blocked = raw.get('blocked')
        profile = raw.get('power_profile')
        return {
            'charge_limit': limit if isinstance(limit, int) else None,
            'blocked': [p for p in blocked if isinstance(p, str)]
            if isinstance(blocked, list) else [],
            'power_profile': profile if profile in POWER_PROFILES else None,
        }

    def save_state(self, state: dict) -> None:
        """Atomic persist (tmp file + rename) so a crash mid-write can
        never leave half a JSON document for the next boot."""
        directory = os.path.dirname(self.state_file) or '.'
        try:
            os.makedirs(directory, mode=0o755, exist_ok=True)
            tmp = os.path.join(directory, '.state.json.tmp')
            with open(tmp, 'w', encoding='utf-8') as handle:
                json.dump(state, handle, indent=2, sort_keys=True)
                handle.write('\n')
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.state_file)
        except OSError as err:
            raise _failed('cannot persist state: %s'
                          % (err.strerror or err)) from None

    def apply_saved(self) -> dict:
        """Boot re-apply: charge limit, power profile, then the nft blocked
        set, then the inbound preset set. One leg failing must not skip
        the others, so errors are collected and reported together."""
        state = self.load_state()
        applied: dict = {'charge_limit': None, 'power_profile': None,
                         'blocked': [], 'errors': []}
        limit = state.get('charge_limit')
        if isinstance(limit, int):
            try:
                applied['charge_limit'] = self.set_charge_limit(limit)
            except HelperError as err:
                applied['errors'].append(str(err))
        profile = state.get('power_profile')
        if profile in POWER_PROFILES:
            try:
                applied['power_profile'] = self.set_power_profile(profile)
            except HelperError as err:
                applied['errors'].append(str(err))
        blocked = [p for p in state.get('blocked', []) if isinstance(p, str)]
        if blocked:
            if not self.nft_available():
                applied['errors'].append('nftables not available')
            else:
                try:
                    self.apply_blocked(blocked)
                    applied['blocked'] = sorted(blocked)
                except HelperError as err:
                    applied['errors'].append(str(err))
        inbound = self.load_inbound()
        if inbound.get('enabled') is False:
            self._remove_inbound_dropin()
        elif inbound['allowed'] or inbound['enabled']:
            try:
                if self.firewall_backend() == 'nftables':
                    self.apply_inbound(inbound['allowed'])
                    self.write_inbound_dropin(inbound['allowed'])
                    applied['inbound'] = sorted(inbound['allowed'])
                else:
                    applied['inbound'] = sorted(inbound['allowed'])
            except HelperError as err:
                applied['errors'].append(str(err))
        return applied


# ------------------------------------------------------- authorization (Gio)

def _require_gio():
    """Gio/GLib are imported here, not at module top, so the logic above
    stays importable (and unit-testable) without PyGObject."""
    from gi.repository import Gio, GLib
    return Gio, GLib


def get_connection_uid(connection, sender, callback) -> None:
    """Async ``GetConnectionUnixUser``: ``callback(uid|None, error|None)``."""
    Gio, GLib = _require_gio()

    def _on_user(conn, result) -> None:
        try:
            uid = int(conn.call_finish(result).unpack()[0])
        except Exception as err:
            callback(None, str(err))
            return
        callback(uid, None)

    connection.call(
        DBUS_BUS, DBUS_PATH, DBUS_IFACE, 'GetConnectionUnixUser',
        GLib.Variant('(s)', (sender,)),
        None, Gio.DBusCallFlags.NONE, 5_000, None, _on_user,
    )


def check_polkit_authorization(connection, sender, action, callback,
                                interactive: bool = False) -> None:
    """Async polkit check with subject ``system-bus-name`` = sender.

    ``callback(authorized, uid, error)`` fires exactly once: ``uid`` is
    the sender's Unix uid for slice scoping, ``error`` a detail string.
    ``interactive`` passes AllowUserInteraction so the session's agent
    prompts for the device password; it is True only for actions whose
    policy is ``auth_admin_keep`` (upgrade, removal, sshd/adbd start/enable,
    opening an inbound port, disabling the firewall).
    """
    Gio, GLib = _require_gio()

    def _on_uid(uid, uid_err) -> None:
        if uid_err is not None or uid is None:
            callback(False, None, uid_err or 'unknown sender')
            return

        def _on_checked(conn, result) -> None:
            try:
                # CheckAuthorization returns ONE out-arg, the struct
                # (bba{ss}) = (is_authorized, is_challenge, details), so
                # the reply unpacks to ((bool, bool, {...}),) and only
                # [0][0] is the verdict. bool() of the struct itself is
                # always True and would authorize every caller.
                authorized = bool(conn.call_finish(result).unpack()[0][0])
            except Exception as err:
                callback(False, uid, str(err))
                return
            callback(authorized, uid, None)

        # Params are one struct of four: the subject struct, the action id,
        # the details dict, the flags. Nested plain tuples convert; a
        # pre-built Variant in struct position does not on this PyGObject.
        params = GLib.Variant(
            '((sa{sv})sa{ss}u)',
            (('system-bus-name', {'name': GLib.Variant('s', sender)}),
             action, {}, ALLOW_INTERACTION if interactive else 0),
        )
        try:
            connection.call(
                POLKIT_BUS, POLKIT_PATH, POLKIT_IFACE, 'CheckAuthorization',
                params,
                None, Gio.DBusCallFlags.NONE, 10_000, None, _on_checked,
            )
        except Exception as err:
            callback(False, uid, str(err))

    get_connection_uid(connection, sender, _on_uid)


# Method -> (polkit action, interactive): interactive passes
# AllowUserInteraction and is reserved for auth_admin_keep actions.
# Argument-dependent methods (ManageService, SetInboundPreset,
# SetInboundAllowed, SetFirewallEnabled) are NOT listed here:
# policy_for() below decides
# them from the already-validated arguments. SetPowerProfile is listed
# but policy_for() still allowlists its profile first. GetCapabilities
# and GetFirewallState need no auth and are dispatched before any
# lookup.
_METHOD_ACTIONS = {
    'SetChargeLimit': (ACTION_CHARGE, False),
    'SetAppNetwork': (ACTION_NETWORK, False),
    'FreezeCgroup': (ACTION_FREEZE, False),
    'RefreshPackageIndex': (ACTION_REFRESH, False),
    'UpgradeSystem': (ACTION_UPGRADE, True),
    'RemovePackage': (ACTION_REMOVE, True),
    'CleanSystem': (ACTION_CLEAN, False),
    'ReadLog': (ACTION_READLOG, False),
    'SetRfkill': (ACTION_RFKILL, False),
    'SetPowerProfile': (ACTION_POWER, False),
}


def policy_for(method: str, args) -> tuple | None:
    """(polkit action, interactive) for a method call, or None.

    Every call goes through here. Plain methods map statically.
    Argument-dependent ones decide from the already-validated
    arguments: opening remote access (ssh/adbd start/enable), opening an
    inbound port or disabling the firewall needs auth_admin_keep with
    user interaction (True); closing a port, enabling the firewall and
    every other allowlisted service change stay allow_active (False).
    None means refused before any authorization: unknown methods,
    unauthenticated probes (GetCapabilities, GetFirewallState) and
    anything outside the allowlists.
    """
    if method == 'SetPowerProfile':
        try:
            (profile,) = args
        except (TypeError, ValueError):
            return None
        if profile not in POWER_PROFILES:
            return None
        return _METHOD_ACTIONS[method]
    if method == 'ManageService':
        try:
            name, action = args
        except (TypeError, ValueError):
            return None
        if name not in SERVICE_ALLOW or action not in SERVICE_ACTIONS:
            return None
        if name in ('ssh', 'adbd') and action in ('start', 'enable'):
            return (ACTION_SERVICE_REMOTE, True)
        return (ACTION_SERVICE, False)
    if method == 'SetInboundPreset':
        try:
            name, allowed = args
        except (TypeError, ValueError):
            return None
        if name not in INBOUND_PRESETS:
            return None
        return (ACTION_FW_OPEN, True) if allowed else (ACTION_FW_RESTRICT,
                                                      False)
    if method == 'SetInboundAllowed':
        try:
            (names,) = args
        except (TypeError, ValueError):
            return None
        if not isinstance(names, (list, tuple)):
            return None
        if any(name not in INBOUND_PRESETS for name in names):
            return None
        return (ACTION_FW_OPEN, True) if names else (ACTION_FW_RESTRICT,
                                                    False)
    if method == 'SetFirewallEnabled':
        try:
            (enabled,) = args
        except (TypeError, ValueError):
            return None
        return (ACTION_FW_RESTRICT, False) if enabled else (ACTION_FW_OPEN,
                                                           True)
    return _METHOD_ACTIONS.get(method)


class HelperService:
    """D-Bus object: auth dispatch around a :class:`HelperCore`.

    ``authorize``/``get_uid`` are injectable for tests; in production
    they are :func:`check_polkit_authorization` (which also resolves the
    uid). ``now`` injects the clock for the idle timer.
    """

    def __init__(self, connection=None, core=None, authorize=None,
                 now=time.monotonic) -> None:
        self.connection = connection
        self.core = core if core is not None else HelperCore()
        self._authorize = authorize or check_polkit_authorization
        self._now = now
        self._last_activity = self._now()
        self._registration_id: int | None = None

    def touch(self) -> None:
        self._last_activity = self._now()

    def idle_expired(self) -> bool:
        """True once 60 s passed without a method call (pure; the GLib
        timer in :func:`main` quits the loop on it)."""
        return (self._now() - self._last_activity) >= IDLE_EXIT_SECONDS

    # ------------------------------------------------------------- dispatch

    def register(self) -> None:
        Gio, _GLib = _require_gio()
        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECT_XML)
        self._registration_id = self.connection.register_object(
            OBJECT_PATH, node.interfaces[0],
            self._on_method_call, None, None,
        )

    def unregister(self) -> None:
        if self.connection is not None and self._registration_id is not None:
            self.connection.unregister_object(self._registration_id)
            self._registration_id = None

    def _on_method_call(self, conn, sender, obj_path, iface, method,
                        params, invocation) -> None:
        self.touch()
        if method == 'GetCapabilities':
            try:
                caps = self.core.get_capabilities()
            except Exception as err:
                invocation.return_dbus_error(ERROR_FAILED, str(err))
                return
            Gio, GLib = _require_gio()
            payload = {key: GLib.Variant('b', bool(value))
                       for key, value in caps.items()}
            invocation.return_value(GLib.Variant('(a{sv})', (payload,)))
            return
        if method == 'GetFirewallState':
            try:
                state = self.core.get_firewall_state()
            except Exception as err:
                invocation.return_dbus_error(ERROR_FAILED, str(err))
                return
            Gio, GLib = _require_gio()
            payload = {
                'backend': GLib.Variant('s', str(state.get('backend'))),
                'enabled': GLib.Variant('b', bool(state.get('enabled'))),
                'allowed': GLib.Variant(
                    'a{sb}', {key: bool(value) for key, value in
                               (state.get('allowed') or {}).items()}),
                'foreign_drop': GLib.Variant(
                    'b', bool(state.get('foreign_drop'))),
            }
            invocation.return_value(GLib.Variant('(a{sv})', (payload,)))
            return
        args = params.unpack() if params is not None else ()
        policy = policy_for(method, args)
        if policy is None or conn is None:
            invocation.return_dbus_error(
                ERROR_INVALID_ARGS, 'unknown method: %s' % (method,))
            return
        action, interactive = policy
        try:
            self._authorize(
                conn, sender, action, interactive,
                lambda ok, uid, err: self._on_authorized(
                    invocation, method, args, ok, uid, err),
            )
        except Exception as err:
            invocation.return_dbus_error(ERROR_FAILED, str(err))

    def _on_authorized(self, invocation, method, args, authorized, uid,
                       auth_error) -> None:
        if auth_error is not None or not authorized or uid is None:
            invocation.return_dbus_error(
                ERROR_NOT_AUTHORIZED,
                auth_error or 'not authorized for %s' % (method,))
            return
        try:
            if method == 'SetChargeLimit':
                (percent,) = args
                self.core.set_charge_limit(int(percent))
            elif method == 'SetAppNetwork':
                cgroup_path, allowed = args
                self.core.set_app_network(str(cgroup_path), bool(allowed),
                                          int(uid))
            elif method == 'FreezeCgroup':
                cgroup_path, frozen = args
                self.core.freeze_cgroup(str(cgroup_path), bool(frozen),
                                        int(uid))
            elif method == 'CleanSystem':
                (cats,) = args
                out = self.core.clean_system(
                    [str(c) for c in cats] if isinstance(cats, list) else cats)
                Gio, GLib = _require_gio()
                invocation.return_value(
                    GLib.Variant('(as)', (list(out.get('cleaned', [])),)))
                return
            elif method == 'ReadLog':
                (log_name,) = args
                text = self.core.read_log(str(log_name))
                Gio, GLib = _require_gio()
                invocation.return_value(GLib.Variant('(s)', (text,)))
                return
            elif method == 'RefreshPackageIndex':
                self.core.refresh_package_index()
            elif method == 'UpgradeSystem':
                Gio, GLib = _require_gio()
                invocation.return_value(
                    GLib.Variant('(s)', (self.core.upgrade_system(),)))
                return
            elif method == 'RemovePackage':
                (pkg,) = args
                self.core.remove_package(str(pkg))
            elif method == 'ManageService':
                name, action = args
                self.core.manage_service(str(name), str(action))
            elif method == 'SetRfkill':
                rtype, blocked = args
                self.core.set_rfkill(str(rtype), bool(blocked))
            elif method == 'SetPowerProfile':
                (profile,) = args
                self.core.set_power_profile(str(profile))
            elif method == 'SetInboundPreset':
                name, allowed = args
                self.core.set_inbound_preset(str(name), bool(allowed))
            elif method == 'SetInboundAllowed':
                (names,) = args
                self.core.set_inbound_allowed(
                    [str(name) for name in names])
            elif method == 'SetFirewallEnabled':
                (enabled,) = args
                self.core.set_firewall_enabled(bool(enabled))
            else:
                invocation.return_dbus_error(
                    ERROR_INVALID_ARGS, 'unknown method: %s' % (method,))
                return
        except HelperError as err:
            invocation.return_dbus_error(err.dbus_name, str(err))
        except Exception as err:
            invocation.return_dbus_error(ERROR_FAILED, str(err))
            return
        Gio, GLib = _require_gio()
        invocation.return_value(GLib.Variant('()', ()))


# ------------------------------------------------------------------- entry

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Privileged D-Bus helper for XX-WM (runs as root).')
    parser.add_argument(
        '--apply-saved', action='store_true',
        help='re-apply the persisted charge limit and nft blocks, then exit '
             '(used by the boot service; takes no bus name)',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.apply_saved:
        applied = HelperCore().apply_saved()
        for err in applied.get('errors', []):
            print('xx-wm-helper: apply-saved: %s' % err, file=sys.stderr)
        return 0 if not applied.get('errors') else 1

    Gio, GLib = _require_gio()
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    except Exception as err:
        print('xx-wm-helper: no system bus: %s' % err, file=sys.stderr)
        return 1
    service = HelperService(bus)
    try:
        service.register()
    except Exception as err:
        print('xx-wm-helper: cannot export object: %s' % err, file=sys.stderr)
        return 1
    Gio.bus_own_name_on_connection(
        bus, BUS_NAME, Gio.BusNameOwnerFlags.NONE, None, None,
    )
    loop = GLib.MainLoop()

    def _on_tick() -> bool:
        if service.idle_expired():
            loop.quit()
            return False
        return True

    GLib.timeout_add_seconds(15, _on_tick)
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    finally:
        service.unregister()
    return 0


if __name__ == '__main__':
    sys.exit(main())
