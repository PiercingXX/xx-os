#!@python@
"""Privileged helper for XX-WM features that need root.

D-Bus system service ``io.piercingxx.XXWM.Helper1`` (D-Bus activated,
exits after 60 s idle). The shell itself never runs as root; it talks
to this service through :mod:`helper_client`.

Only ``Gio``/``GLib`` (imported lazily so the pure logic below stays
importable without PyGObject) and the standard library are used: no
``shell=True`` anywhere. Rulesets reach ``nft`` on stdin as one
transaction, never as argv grammar.
"""
from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
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
ACTION_CLEAN_JOURNAL = BUS_NAME + '.clean-journal'
ACTION_READLOG = BUS_NAME + '.read-log'
ACTION_REFRESH = BUS_NAME + '.refresh-packages'
ACTION_UPGRADE = BUS_NAME + '.upgrade-system'
ACTION_REMOVE = BUS_NAME + '.remove-package'
ACTION_SERVICE = BUS_NAME + '.service-manage'
ACTION_SERVICE_REMOTE = BUS_NAME + '.service-remote'
ACTION_SERVICE_CRITICAL = BUS_NAME + '.service-critical'
ACTION_RFKILL = BUS_NAME + '.rfkill'
ACTION_FW_OPEN = BUS_NAME + '.firewall-open'
ACTION_FW_RESTRICT = BUS_NAME + '.firewall-restrict'
ACTION_FW_ENABLE = BUS_NAME + '.firewall-enable'
ACTION_POWER = BUS_NAME + '.power-profile'
ACTION_WAKE = BUS_NAME + '.wake-alarm'

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

# Marks the port-22 rule the helper adds while sshd listens or is
# enabled, so it is never read back as the SSH feature's own preset.
NFT_KEEP_SSH_COMMENT = 'xxwm keep-ssh'

# Alpha only: goes away when xx-os leaves alpha (set False, or delete it
# and CONTAINER_BRIDGES). Android containers on a host bridge (Andromeda,
# Waydroid, plain LXC) get their address and DNS from a dnsmasq on that
# bridge, so with the drop policy on their network dies. While True the
# chain accepts DHCP (udp 67) and DNS (udp/tcp 53) arriving on these
# bridges, and nothing else from them.
ALPHA_ALLOW_CONTAINER_BRIDGES = True
CONTAINER_BRIDGES = ('andromeda0', 'lxcbr0', 'waydroid0')
CONTAINER_BRIDGE_PORTS = (('udp', 67), ('udp', 53), ('tcp', 53))
NFT_CONTAINER_COMMENT = 'xxwm container-bridge'


def active_container_bridges() -> tuple:
    """The bridges the input chain lets DHCP and DNS in from: none once
    ALPHA_ALLOW_CONTAINER_BRIDGES is off."""
    return CONTAINER_BRIDGES if ALPHA_ALLOW_CONTAINER_BRIDGES else ()

# Every cgroup path component below the user slice must match this
# before it may appear inside an nft string literal: no quotes, spaces,
# separators, braces, backslashes or newlines can reach nft's lexer.
_CGROUP_COMPONENT_RE = re.compile(r'[A-Za-z0-9_.:@\-]+')
_CGROUP_REL_RE = re.compile(r'[A-Za-z0-9_.:@\-]+(?:/[A-Za-z0-9_.:@\-]+)*')
_USER_SLICE_RE = re.compile(r'user\.slice/user-([0-9]+)\.slice(?:/|$)')

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
# Stopping or disabling these cuts calls, SMS and the network.
CRITICAL_SERVICES = ('modem', 'eg25', 'network')
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
PROC_ROOT_DEFAULT = '/proc'
CGROUP_ROOT_DEFAULT = '/sys/fs/cgroup'
MOUNTS_FILE_DEFAULT = '/proc/mounts'
STATE_DIR_DEFAULT = '/var/lib/xx-wm-helper'
STATE_FILE_NAME = 'state.json'

CHARGE_MIN = 50
CHARGE_MAX = 100
CHARGE_START_DELTA = 5

IDLE_EXIT_SECONDS = 60

# Package jobs (UpgradeSystem, RemovePackage, RefreshPackageIndex,
# CleanSystem) run with no kill timeout: SIGKILLing apk/pacman
# mid-transaction can leave a half-installed system, and a slow mirror or
# a big cache outlasts any fixed limit. The method replies with a job id
# at once and JobFinished(job_id, rc, tail) goes to the caller when the
# last command exits. CleanSystem's tail lists the cleaned categories,
# one per line.
JOB_TAIL_LINES = 40
JOB_TAIL_CHARS = 4000

POLKIT_TIMEOUT_MS = 10_000
# G_MAXINT: an interactive check waits for as long as the password dialog
# is open (the bus still answers NoReply if polkitd goes away).
POLKIT_INTERACTIVE_TIMEOUT_MS = 2 ** 31 - 1

# RTC wake alarm (SetWakeAlarm/ClearWakeAlarm): automatic suspend arms one
# hardware wake ahead of the next xx-clock alarm. The helper owns at most
# one wake (recorded in WAKE_FILE_NAME because the daemon exits when idle)
# and never clobbers a pending wake some other program set. Absolute epoch
# seconds only, a little in the future and at most WAKE_MAX_AHEAD_S out,
# so a caller can neither wake the phone every few seconds nor park a
# wake years away.
RTC_WAKEALARM_REL = os.path.join('class', 'rtc', 'rtc0', 'wakealarm')
# wakealarm is absolute in the RTC's own clock, which need not keep wall
# time: the FLX1s RTC sits in 2010 while the system clock is NTP-set. The
# offset between the two is read from since_epoch at every call.
RTC_SINCE_EPOCH_REL = os.path.join('class', 'rtc', 'rtc0', 'since_epoch')
WAKE_FILE_NAME = 'wake.json'
WAKE_MIN_AHEAD_S = 5
WAKE_MAX_AHEAD_S = 48 * 3600

# PIN store (HasPin/VerifyPin/SetPin/ClearPin): one root-owned 0600 JSON
# record per uid under a 0700 dir, so no process running as the user can
# read the hash and brute-force it offline. The escalation mirrors the
# shell's lock_lines._lockout_secs (a test pins the two together) and is
# persisted here, root-side, so a user process can neither reset it nor
# outrun it by restarting the helper.
PIN_DIR_NAME = 'pin'
PIN_MAX_LEN = 64
PIN_FAIL_THRESHOLD = 5
PIN_LOCKOUT_STEP = 30
PIN_LOCKOUT_CAP = 300
# scrypt cost: 2^15 x 8 = 32 MiB, a few hundred ms on a phone SoC — paid
# by this process, never by the shell's main loop.
PIN_SCRYPT_N = 2 ** 15
PIN_SCRYPT_R = 8
PIN_SCRYPT_P = 1
PIN_PBKDF2_ITERATIONS = 600_000
PIN_METHODS = ('HasPin', 'VerifyPin', 'SetPin', 'ClearPin')

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
      <arg name="job_id" type="s" direction="out"/>
    </method>
    <method name="ReadLog">
      <arg name="name" type="s" direction="in"/>
      <arg name="text" type="s" direction="out"/>
    </method>
    <method name="RefreshPackageIndex">
      <arg name="job_id" type="s" direction="out"/>
    </method>
    <method name="UpgradeSystem">
      <arg name="job_id" type="s" direction="out"/>
    </method>
    <method name="RemovePackage">
      <arg name="pkg" type="s" direction="in"/>
      <arg name="job_id" type="s" direction="out"/>
    </method>
    <signal name="JobFinished">
      <arg name="job_id" type="s"/>
      <arg name="rc" type="i"/>
      <arg name="tail" type="s"/>
    </signal>
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
    <method name="SetWakeAlarm">
      <arg name="epoch" type="t" direction="in"/>
    </method>
    <method name="ClearWakeAlarm">
    </method>
    <method name="HasPin">
      <arg name="has_pin" type="b" direction="out"/>
    </method>
    <method name="VerifyPin">
      <arg name="pin" type="s" direction="in"/>
      <arg name="matched" type="b" direction="out"/>
      <arg name="retry_after" type="u" direction="out"/>
      <arg name="has_pin" type="b" direction="out"/>
    </method>
    <method name="SetPin">
      <arg name="old_pin" type="s" direction="in"/>
      <arg name="new_pin" type="s" direction="in"/>
      <arg name="done" type="b" direction="out"/>
      <arg name="retry_after" type="u" direction="out"/>
    </method>
    <method name="ClearPin">
      <arg name="old_pin" type="s" direction="in"/>
      <arg name="done" type="b" direction="out"/>
      <arg name="retry_after" type="u" direction="out"/>
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


def _default_run(argv: list[str],
                 input: str | None = None) -> subprocess.CompletedProcess[str]:
    # argv is a list (no shell) built from constants and allowlisted values.
    return subprocess.run(  # noqa: S603
        argv, input=input, capture_output=True, text=True, timeout=30,
        check=False,
    )


def job_tail(output: str) -> str:
    """The last JOB_TAIL_LINES lines of a job's output, capped to
    JOB_TAIL_CHARS: package manager progress, never arguments or env."""
    lines = (output or '').rstrip().splitlines()[-JOB_TAIL_LINES:]
    return '\n'.join(lines)[-JOB_TAIL_CHARS:]


def _default_spawn(argv: list[str], on_exit) -> None:
    """Run a long job on a worker thread with no timeout; ``on_exit(rc,
    output)`` fires once on the helper's main loop. A job that cannot
    start reports rc -1 with the reason as its output."""
    _Gio, GLib = _require_gio()

    def _post(rc: int, output: str) -> None:
        def _deliver() -> bool:
            on_exit(rc, output)
            return False
        GLib.idle_add(_deliver)

    def _work() -> None:
        try:
            # argv is a list (no shell) from the package-job allowlist.
            proc = subprocess.Popen(  # noqa: S603
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,

                stderr=subprocess.STDOUT)
            raw, _ = proc.communicate()
        except OSError as err:
            _post(-1, '%s failed to start: %s' % (argv[0], err))
            return
        _post(proc.returncode, raw.decode('utf-8', 'replace'))

    threading.Thread(target=_work, name='xx-wm-helper-job',
                     daemon=True).start()


class HelperCore:
    """Pure helper logic: sysfs/nft/cgroup work with injectable roots.

    ``sysfs``/``cgroup_root``/``mounts_file``/``state_file``/``nft_bin``
    exist so tests can point the helper at tmp dirs and a fake ``nft``;
    in production they keep their defaults. ``run`` replaces
    :func:`subprocess.run` (same argv-list contract, no shell; rulesets
    go in through its ``input`` keyword as nft's stdin) for short
    commands; ``spawn(argv, on_exit)`` starts the long package jobs.
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
        wake_file: str | None = None,
        proc_root: str = PROC_ROOT_DEFAULT,
        run=_default_run,
        spawn=_default_spawn,
        now=time.time,
    ) -> None:
        self.sysfs = sysfs
        self.proc_root = proc_root
        self.cgroup_root = cgroup_root
        self.mounts_file = mounts_file
        self.state_file = state_file
        self.nft_bin = nft_bin
        self.nft_dropin_dir = nft_dropin_dir
        self.log_dir = log_dir
        self.wake_file = wake_file or os.path.join(
            os.path.dirname(state_file) or '.', WAKE_FILE_NAME)
        self._run = run
        self.spawn = spawn
        self._now = now

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

    def validate_app_cgroup(self, path: str, uid: int, *,
                            must_exist: bool = True) -> str:
        """Confine ``path`` to the caller's own user slice.

        Anything malformed (non-absolute, ``..``, redundant separators,
        trailing slash, NUL) or outside
        ``<cgroup>/user.slice/user-<uid>.slice/`` raises NotAuthorized —
        from the caller's perspective it is simply not permitted. Every
        component below the slice must match ``[A-Za-z0-9_.:@-]+`` and,
        unless ``must_exist`` is False (dropping a block), the path must
        be an existing directory. Symlinks are resolved too so a link
        inside the slice cannot point out.
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
        below = path[len(prefix) + 1:]
        if below and not all(_CGROUP_COMPONENT_RE.fullmatch(part)
                             for part in below.split('/')):
            raise _denied('cgroup path has unsupported characters: %r'
                          % (path,))
        if must_exist and not os.path.isdir(path):
            raise _denied('cgroup path does not exist: %r' % (path,))
        real = os.path.realpath(path)
        real_prefix = os.path.realpath(prefix)
        if real != real_prefix and not real.startswith(real_prefix + '/'):
            raise _denied('cgroup path escapes your user slice: %r' % (path,))
        return path

    def revalidate_blocked(self, path: object) -> str | None:
        """A persisted blocked path that still passes validation for the
        uid its own user slice names, else None (malformed, outside
        every user slice, or the scope is gone)."""
        if not isinstance(path, str):
            return None
        root = self.cgroup_root.rstrip('/') + '/'
        if not path.startswith(root):
            return None
        match = _USER_SLICE_RE.match(path[len(root):])
        if match is None:
            return None
        try:
            return self.validate_app_cgroup(path, int(match.group(1)))
        except (HelperError, ValueError):
            return None

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
        """Quoted nft string literal for ``path``, relative to the cgroup2
        mount (as the kernel reports it). Refuses anything outside the
        validated character set, so the literal can never end early."""
        rel = posixpath.relpath(path, self.cgroup_root)
        if _CGROUP_REL_RE.fullmatch(rel) is None:
            raise _denied('cgroup path has unsupported characters: %r'
                          % (path,))
        return '"%s"' % rel

    def _nft_failed(self, what: str, proc) -> HelperError:
        """Log nft's stderr for the journal; the caller only ever gets a
        fixed message, never text nft echoed back from its input."""
        stderr = (getattr(proc, 'stderr', '') or '').strip()
        if stderr:
            print('xx-wm-helper: nft %s: %s' % (what, stderr[:400]),
                  file=sys.stderr)
        return _failed('nft refused the %s (exit %d)'
                       % (what, getattr(proc, 'returncode', 1)))

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
            raise self._nft_failed(' '.join(argv[:2]) or 'command', proc)
        return getattr(proc, 'stdout', '') or ''

    def _nft_stdin(self, flags: list[str], data: str, what: str) -> None:
        """Feed one ruleset to ``nft <flags> -f -``. nft applies the whole
        input as a single transaction, so a failure changes nothing."""
        try:
            proc = self._run([self.nft_bin] + flags + ['-f', '-'],
                             input=data)
        except OSError as err:
            raise _failed('nft failed to start: %s' % err) from None
        if proc.returncode != 0:
            raise self._nft_failed(what, proc)

    def render_blocked_nft(self, blocked) -> str:
        """The ``inet xxwm`` table as one nft script: add-then-delete
        replaces the table whole, then one drop rule per blocked path.

        Text, not JSON: nft's JSON parser has no ``socket cgroupv2``
        (only transparent, mark and wildcard), so the path goes in as a
        string literal that :meth:`cgroup_nft_match` has restricted."""
        lines = ['add table %s %s' % (NFT_FAMILY, NFT_TABLE),
                 'delete table %s %s' % (NFT_FAMILY, NFT_TABLE),
                 'table %s %s {' % (NFT_FAMILY, NFT_TABLE),
                 '    chain %s {' % NFT_CHAIN,
                 '        type filter hook output priority 0; '
                 'policy accept;']
        for path in sorted(blocked):
            lines.append('        socket cgroupv2 level %d %s drop'
                         % (self.cgroup_nft_level(path),
                            self.cgroup_nft_match(path)))
        lines += ['    }', '}']
        return '\n'.join(lines) + '\n'

    def apply_blocked(self, blocked) -> list[str]:
        """Replace the ``inet xxwm`` table with drop rules for ``blocked``
        in one transaction and return the paths actually applied.

        Paths whose scope is gone (the app exited, or no user slice yet
        at boot) or that no longer validate are dropped first. If nft
        still refuses (a scope vanished in between), the previous table
        stays live, so existing blocks never fall away, and one retry
        runs with the re-checked set. Other tables are never touched."""
        live = sorted({p for p in map(self.revalidate_blocked, blocked)
                       if p})
        try:
            self._nft_stdin([], self.render_blocked_nft(live),
                            'per-app block table')
        except HelperError:
            again = sorted({p for p in map(self.revalidate_blocked, live)
                            if p})
            if again == live:
                raise
            live = again
            self._nft_stdin([], self.render_blocked_nft(live),
                            'per-app block table')
        return live

    def set_app_network(self, cgroup_path: str, allowed: bool, uid: int) -> bool:
        """Block (``allowed`` False) or unblock a caller-owned app cgroup,
        then re-apply the nft table and persist what is live. When nft
        refuses, nothing is persisted and the previous table stays."""
        path = self.validate_app_cgroup(cgroup_path, uid,
                                        must_exist=not allowed)
        if not self.nft_available():
            raise _failed('nftables not available')
        state = self.load_state()
        blocked = [p for p in state.get('blocked', []) if isinstance(p, str)]
        if allowed and path in blocked:
            blocked.remove(path)
        elif not allowed and path not in blocked:
            blocked.append(path)
        live = self.apply_blocked(blocked)
        if not allowed and path not in live:
            raise _failed('the app is no longer running')
        state['blocked'] = live
        self.save_state(state)
        return bool(allowed)

    # -------------------------------------------------------- clean system

    def clean_steps(self, categories, *,
                    apk_cache_dir: str = APK_CACHE_DIR_DEFAULT,
                    systemd_dir: str = SYSTEMD_DIR_DEFAULT) -> dict:
        """The fixed-allowlist cache cleanups to run, as a job's steps.

        ``categories`` must all name :data:`CLEAN_SYSTEM_ALLOWLIST`;
        anything else raises InvalidArgs, so callers can never smuggle
        in a free-form command. ``apk-cache`` runs only when the apk
        cache dir exists, ``journal-vacuum`` only on systemd, and any
        entry whose binary is missing is skipped. Returns
        ``{'steps': [(category, argv), ...], 'skipped': [...]}``.
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
        steps: list[tuple[str, list[str]]] = []
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
            steps.append((category, list(argv)))
        return {'steps': steps, 'skipped': skipped}

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

    def refresh_argv(self, which=None) -> list[str]:
        """argv for the apk index refresh job (``apk update``): harmless
        and non-destructive, so its policy stays ``allow_active=yes``.

        Never ``pacman -Sy``: a partial pacman sync breaks the system on
        Arch, so Arch counts come from ``checkupdates`` (own temp db) and
        upgrades run in a terminal instead."""
        if not (which or shutil.which)('apk'):
            raise _failed('apk not available on this system')
        return ['apk', 'update']

    def upgrade_argv(self, which=None) -> list[str]:
        """argv for the ``apk upgrade`` job. Interactive
        ``auth_admin_keep``: the agent prompts for the device password."""
        if (which or shutil.which)('apk') is None:
            raise _failed('apk not available on this system')
        return ['apk', 'upgrade']

    def remove_argv(self, pkg: str, which=None) -> list[str]:
        """argv for the job removing one validated, non-protected distro
        package. ``auth_admin_keep`` with interaction: the agent prompts
        for the device password."""
        name = validate_package_name(pkg)
        backend = self._backend(which)
        if backend is None:
            raise _failed('no supported package manager on this system')
        return (['apk', 'del', name] if backend == 'apk'
                else ['pacman', '-Rns', '--noconfirm', name])

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

    @staticmethod
    def inbound_port_rules(allowed, keep_ssh: bool = False) -> list:
        """``(proto, port, comment)`` accepts for the allowed presets, in
        name order, then the SSH safeguard. Constants only: names outside
        INBOUND_PRESETS raise KeyError."""
        rules = []
        for name in sorted(allowed):
            preset = INBOUND_PRESETS[name]
            for proto in ('tcp', 'udp'):
                for port in preset[proto]:
                    rules.append((proto, port, 'xxwm %s' % name))
        if keep_ssh:
            rules.append(('tcp', 22, NFT_KEEP_SSH_COMMENT))
        return rules

    def render_inbound_json(self, allowed, keep_ssh: bool = False) -> dict:
        """The ``inet xx_wm`` input chain as an ``nft -j`` document.

        A real policy: drop by default; accept established/related
        traffic, loopback, ICMP and ICMPv6 (neighbour discovery), DHCP
        and DHCPv6 client replies, container-bridge DHCP and DNS (alpha
        only), then the allowed presets. Add-then-delete replaces our
        table whole in the same transaction."""
        table = {'family': NFT_FAMILY, 'name': NFT_IN_TABLE}
        where = {'family': NFT_FAMILY, 'table': NFT_IN_TABLE,
                 'chain': NFT_IN_CHAIN}

        def match(left, right, op='=='):
            return {'match': {'op': op, 'left': left, 'right': right}}

        def port(proto, field, value):
            right = ({'range': [value[0], value[1]]}
                     if isinstance(value, tuple) else value)
            return match({'payload': {'protocol': proto, 'field': field}},
                         right)

        accept = {'accept': None}
        exprs = [
            [match({'ct': {'key': 'state'}},
                   {'set': ['established', 'related']}, op='in'), accept],
            [match({'meta': {'key': 'iif'}}, 'lo'), accept],
            [match({'meta': {'key': 'l4proto'}}, 'icmp'), accept],
            [match({'meta': {'key': 'l4proto'}}, 'ipv6-icmp'), accept],
            [port('udp', 'sport', 67), port('udp', 'dport', 68), accept],
            [port('udp', 'sport', 547), port('udp', 'dport', 546), accept],
        ]
        rules = [dict(where, expr=expr) for expr in exprs]
        bridges = active_container_bridges()
        for proto, value in CONTAINER_BRIDGE_PORTS if bridges else ():
            rules.append(dict(where, expr=[
                match({'meta': {'key': 'iifname'}}, {'set': list(bridges)}),
                port(proto, 'dport', value), accept],
                comment=NFT_CONTAINER_COMMENT))
        for proto, value, comment in self.inbound_port_rules(allowed,
                                                             keep_ssh):
            rules.append(dict(where, expr=[port(proto, 'dport', value),
                                           accept], comment=comment))
        return {'nftables': [
            {'add': {'table': dict(table)}},
            {'delete': {'table': dict(table)}},
            {'add': {'table': dict(table)}},
            {'add': {'chain': {'family': NFT_FAMILY, 'table': NFT_IN_TABLE,
                               'name': NFT_IN_CHAIN, 'type': 'filter',
                               'hook': 'input', 'prio': 0,
                               'policy': 'drop'}}},
        ] + [{'add': {'rule': rule}} for rule in rules]}

    def render_inbound_nft(self, allowed, keep_ssh: bool = False) -> str:
        """The boot drop-in: the same chain as :meth:`render_inbound_json`
        in nft's text syntax (constants only)."""
        lines = ['# xx-wm inbound firewall (managed; do not edit).',
                 '# An accept here cannot override a drop in another '
                 'base chain.',
                 'table inet %s {' % NFT_IN_TABLE,
                 '    chain %s {' % NFT_IN_CHAIN,
                 '        type filter hook input priority filter; '
                 'policy drop;',
                 '        ct state established,related accept',
                 '        iif "lo" accept',
                 '        meta l4proto icmp accept',
                 '        meta l4proto ipv6-icmp accept',
                 '        udp sport 67 udp dport 68 accept',
                 '        udp sport 547 udp dport 546 accept']
        bridges = active_container_bridges()
        for proto, port in CONTAINER_BRIDGE_PORTS if bridges else ():
            lines.append('        iifname { %s } %s dport %d accept '
                         'comment "%s";'
                         % (', '.join('"%s"' % name for name in bridges),
                            proto, port, NFT_CONTAINER_COMMENT))
        for proto, port, comment in self.inbound_port_rules(allowed,
                                                            keep_ssh):
            lines.append('        %s dport %s accept comment "%s";'
                         % (proto, self.nft_port(port), comment))
        lines += ['    }', '}']
        return '\n'.join(lines) + '\n'

    def write_inbound_dropin(self, allowed, keep_ssh: bool = False) -> None:
        """Persist the pmOS-style nftables drop-in for the allowed set."""
        try:
            os.makedirs(self.nft_dropin_dir, mode=0o755, exist_ok=True)
            tmp = os.path.join(self.nft_dropin_dir,
                               '.%s.tmp' % NFT_DROPIN_NAME)
            with open(tmp, 'w', encoding='utf-8') as handle:
                handle.write(self.render_inbound_nft(allowed, keep_ssh))
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
            self._nft(['delete', 'table', NFT_FAMILY, NFT_IN_TABLE])
        except HelperError:
            pass
        self._drop_legacy_inbound()
        self._remove_inbound_dropin()

    def apply_inbound(self, allowed, keep_ssh: bool = False) -> None:
        """Replace the live `inet xx_wm` table with the drop-policy chain
        in one ``nft -j`` transaction: our own table only, other tables
        are never touched, and a refusal leaves the old table live."""
        self._drop_legacy_inbound()
        self._nft_stdin(['-j'], json.dumps(
            self.render_inbound_json(allowed, keep_ssh)), 'firewall chain')

    def _install_inbound(self, allowed) -> None:
        keep = self.ssh_keep_open()
        self.apply_inbound(allowed, keep)
        self.write_inbound_dropin(allowed, keep)

    # ------------------------------------------------- SSH reachability

    def ssh_listening(self) -> bool:
        """True when something listens on TCP port 22 in our network
        namespace (sshd, or systemd's ssh.socket)."""
        for name in ('tcp', 'tcp6'):
            try:
                with open(os.path.join(self.proc_root, 'net', name),
                          encoding='ascii', errors='replace') as handle:
                    lines = handle.read().splitlines()[1:]
            except OSError:
                continue
            for line in lines:
                fields = line.split()
                if (len(fields) > 3 and fields[3] == '0A'
                        and fields[1].rsplit(':', 1)[-1].upper() == '0016'):
                    return True
        return False

    def ssh_enabled(self) -> bool:
        """True when the SSH service starts at boot."""
        init = self.init_system()
        if init == 'systemd':
            for unit in SERVICE_UNITS['ssh']['systemd'] + ('ssh.socket',):
                try:
                    proc = self._run(['systemctl', 'is-enabled', '--quiet',
                                      unit])
                except OSError:
                    return False
                if getattr(proc, 'returncode', 1) == 0:
                    return True
            return False
        if init == 'openrc':
            return any(os.path.exists(os.path.join(
                '/etc', 'runlevels', 'default', name))
                for name in SERVICE_UNITS['ssh']['openrc'])
        return False

    def ssh_keep_open(self) -> bool:
        """Keep port 22 reachable when the firewall goes on. Decided from
        the system, not the caller's environment: this daemon is D-Bus
        activated, so SSH_CONNECTION is never set here."""
        return self.ssh_listening() or self.ssh_enabled()

    # ------------------------------------------------- firewall state

    @staticmethod
    def _rule_open_port(rule: dict):
        """``(proto, port)`` for a ``<proto> dport <port|lo-hi> accept``
        rule from ``nft -j``, else None."""
        expr = rule.get('expr')
        if not isinstance(expr, list) or len(expr) != 2 \
                or expr[1] != {'accept': None}:
            return None
        match = expr[0].get('match') if isinstance(expr[0], dict) else None
        if not isinstance(match, dict) or match.get('op') != '==':
            return None
        payload = (match.get('left') or {}).get('payload') \
            if isinstance(match.get('left'), dict) else None
        if not isinstance(payload, dict) or payload.get('field') != 'dport' \
                or payload.get('protocol') not in ('tcp', 'udp'):
            return None
        right = match.get('right')
        if isinstance(right, bool):
            return None
        if isinstance(right, int):
            return (payload['protocol'], right)
        if isinstance(right, dict) and isinstance(right.get('range'), list) \
                and len(right['range']) == 2 \
                and all(isinstance(v, int) for v in right['range']):
            return (payload['protocol'], tuple(right['range']))
        return None

    def parse_nft_ruleset(self, text: str) -> dict:
        """From ``nft -j list ruleset``: whether our input chain is a live
        drop policy, which preset ports it opens (the SSH safeguard rule
        excluded), and whether another base chain drops input."""
        try:
            items = json.loads(text or '{}').get('nftables', [])
        except (ValueError, AttributeError):
            items = []
        if not isinstance(items, list):
            items = []
        blocking = False
        foreign_drop = False
        open_ports = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            chain = item.get('chain')
            if isinstance(chain, dict):
                ours = (chain.get('family') == NFT_FAMILY
                        and chain.get('table') == NFT_IN_TABLE)
                is_drop = (chain.get('hook') == 'input'
                           and chain.get('policy') == 'drop')
                if ours and chain.get('name') == NFT_IN_CHAIN:
                    blocking = is_drop
                elif not ours and is_drop:
                    foreign_drop = True
            rule = item.get('rule')
            if isinstance(rule, dict) \
                    and rule.get('family') == NFT_FAMILY \
                    and rule.get('table') == NFT_IN_TABLE \
                    and rule.get('chain') == NFT_IN_CHAIN \
                    and rule.get('comment') != NFT_KEEP_SSH_COMMENT:
                spec = self._rule_open_port(rule)
                if spec is not None:
                    open_ports.add(spec)
        return {'blocking': blocking, 'open_ports': open_ports,
                'foreign_drop': foreign_drop}

    @staticmethod
    def preset_open(name: str, open_ports) -> bool:
        preset = INBOUND_PRESETS[name]
        return all((proto, port) in open_ports
                   for proto in ('tcp', 'udp') for port in preset[proto])

    def _ufw_status(self) -> dict:
        """``ufw status verbose``: active, default-deny incoming, and the
        allowed rule specs as whole tokens (``22/tcp`` never matches
        ``1722/tcp``)."""
        try:
            proc = self._run(['ufw', 'status', 'verbose'])
            status = getattr(proc, 'stdout', '') or ''
        except OSError as err:
            raise _failed('ufw status failed: %s' % err) from None
        lines = status.splitlines()
        active = bool(lines) and lines[0].strip() == 'Status: active'
        deny_in = any(line.startswith('Default:')
                      and ('deny (incoming)' in line
                           or 'reject (incoming)' in line)
                      for line in lines)
        specs = set()
        for line in lines:
            fields = line.split()
            if len(fields) >= 2 and fields[1] == 'ALLOW':
                specs.add(fields[0])
        return {'active': active, 'deny_in': deny_in, 'specs': specs}

    def get_firewall_state(self) -> dict:
        """Backend, on/off and per-preset state for the section.

        ``enabled`` is True only when incoming traffic is really being
        dropped: our input chain is live with ``policy drop`` (nftables)
        or ufw is active with a deny/reject incoming default. Presets are
        read from the live rules; with the firewall off, nftables reports
        the persisted set. `foreign_drop` flags a drop in another base
        chain, which our accepts cannot override."""
        backend = self.firewall_backend()
        if backend == 'ufw':
            ufw = self._ufw_status()
            enabled = ufw['active'] and ufw['deny_in']
            return {
                'backend': 'ufw',
                'enabled': enabled,
                'allowed': {name: all(spec in ufw['specs'] for spec in
                                      self.ufw_specs(name))
                            for name in sorted(INBOUND_PRESETS)},
                'foreign_drop': enabled,
            }
        if backend == 'nftables':
            try:
                proc = self._run([self.nft_bin, '-j', 'list', 'ruleset'])
            except OSError as err:
                raise _failed('nft list failed: %s' % err) from None
            if getattr(proc, 'returncode', 1) != 0:
                raise self._nft_failed('ruleset listing', proc)
            live = self.parse_nft_ruleset(getattr(proc, 'stdout', '') or '')
            persisted = self.load_inbound()
            enabled = live['blocking']
            return {
                'backend': 'nftables',
                'enabled': enabled,
                'allowed': {name: (self.preset_open(name,
                                                    live['open_ports'])
                                   if enabled
                                   else name in persisted['allowed'])
                            for name in sorted(INBOUND_PRESETS)},
                'foreign_drop': live['foreign_drop'],
            }
        return {'backend': 'none', 'enabled': False, 'allowed': {},
                'foreign_drop': False}

    def set_inbound_preset(self, name: str, allowed: bool) -> bool:
        """Allow (open) or forbid (close) one preset's ports. Presets only:
        anything outside INBOUND_PRESETS is InvalidArgs. nftables only
        programs the chain while the firewall is on."""
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
            if state.get('enabled') is True:
                self._install_inbound(state['allowed'])
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
        """Enable (drop incoming except the base rules and allowed
        presets) or disable the firewall. Enabling keeps port 22 open
        while sshd listens or is enabled (:meth:`ssh_keep_open`), so
        turning the firewall on cannot lock out a phone reached over
        SSH."""
        backend = self.firewall_backend()
        if backend == 'ufw':
            if enabled:
                self._run_cmd(['ufw', 'default', 'deny', 'incoming'])
                self._run_cmd(['ufw', 'default', 'allow', 'outgoing'])
                if self.ssh_keep_open():
                    self._run_cmd(['ufw', 'allow', '22/tcp'])
                self._run_cmd(['ufw', '--force', 'enable'])
            else:
                self._run_cmd(['ufw', 'disable'])
        elif backend == 'nftables':
            if enabled:
                state = self.load_inbound()
                self._install_inbound(state['allowed'])
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
        present = self._ufw_status()['specs']
        wanted_set = set(wanted)
        for name in sorted(INBOUND_PRESETS):
            for spec in self.ufw_specs(name):
                if name in wanted_set and spec not in present:
                    self._run_cmd(['ufw', 'allow', spec])
                elif name not in wanted_set and spec in present:
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
        Only an explicitly enabled firewall (``enabled`` True, set by
        SetFirewallEnabled) gets a table: a feature change alone never
        turns the drop policy on, and off leaves no table behind.
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
            if state.get('enabled') is True:
                self._install_inbound(wanted)
            else:
                self._clear_inbound_nft()
        else:
            raise _failed('no firewall backend available')
        self.save_inbound(state)
        return wanted

    # ------------------------------------------------------- RTC wake alarm

    def wakealarm_path(self) -> str:
        return os.path.join(self.sysfs, RTC_WAKEALARM_REL)

    def _rtc_offset(self) -> int:
        """System clock minus RTC clock, in seconds; 0 when since_epoch
        is missing or unreadable (the RTC is then taken to keep wall
        time)."""
        try:
            with open(os.path.join(self.sysfs, RTC_SINCE_EPOCH_REL), 'r',
                      encoding='ascii') as handle:
                rtc_now = int(handle.read().strip())
        except (OSError, ValueError):
            return 0
        return int(self._now()) - rtc_now if rtc_now > 0 else 0

    def _read_wakealarm(self) -> int | None:
        """Pending wake in the RTC's clock; None when unset (the kernel
        reads back an empty file) or unreadable."""
        try:
            with open(self.wakealarm_path(), 'r', encoding='ascii') as handle:
                text = handle.read().strip()
        except (OSError, ValueError):
            return None
        try:
            value = int(text)
        except ValueError:
            return None
        return value if value > 0 else None

    def _load_wake_owned(self) -> int | None:
        try:
            with open(self.wake_file, 'r', encoding='utf-8') as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            return None
        value = raw.get('epoch') if isinstance(raw, dict) else None
        return value if isinstance(value, int) and value > 0 else None

    def _save_wake_owned(self, epoch: int | None) -> None:
        if epoch is None:
            try:
                os.remove(self.wake_file)
            except FileNotFoundError:
                pass
            except OSError as err:
                raise _failed('cannot drop wake record: %s'
                              % (err.strerror or err)) from None
            return
        directory = os.path.dirname(self.wake_file) or '.'
        try:
            os.makedirs(directory, mode=0o755, exist_ok=True)
            tmp = os.path.join(directory, '.%s.tmp' % WAKE_FILE_NAME)
            with open(tmp, 'w', encoding='utf-8') as handle:
                json.dump({'epoch': int(epoch)}, handle)
                handle.write('\n')
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.wake_file)
        except OSError as err:
            raise _failed('cannot persist wake record: %s'
                          % (err.strerror or err)) from None

    def set_wake_alarm(self, epoch) -> int:
        """Arm the RTC to wake the system at absolute ``epoch`` seconds.

        The epoch must lie WAKE_MIN_AHEAD_S..WAKE_MAX_AHEAD_S ahead. The
        kernel refuses a new alarm while one is pending, so the old one is
        cleared with ``0`` first — but only when it is ours (or already
        past): another program's pending wake is left alone and the call
        fails. ``epoch`` is wall time; what is written, compared and
        recorded is that instant in the RTC's own clock."""
        if isinstance(epoch, bool) or not isinstance(epoch, int):
            raise HelperError(ERROR_INVALID_ARGS, 'wake time is not an integer')
        now = int(self._now())
        if epoch < now + WAKE_MIN_AHEAD_S:
            raise HelperError(ERROR_INVALID_ARGS, 'wake time is not in the '
                              'future')
        if epoch > now + WAKE_MAX_AHEAD_S:
            raise HelperError(ERROR_INVALID_ARGS, 'wake time is more than '
                              '%d h ahead' % (WAKE_MAX_AHEAD_S // 3600))
        path = self.wakealarm_path()
        if not os.path.isfile(path):
            raise _failed('no RTC wake alarm on this device')
        pending = self._read_wakealarm()
        owned = self._load_wake_owned()
        offset = self._rtc_offset()
        rtc_epoch = epoch - offset
        if pending is not None and pending > now - offset and pending != owned:
            raise _failed('RTC wake alarm already set by another program')
        self._write_file(path, '0')
        self._write_file(path, str(rtc_epoch))
        self._save_wake_owned(rtc_epoch)
        return epoch

    def clear_wake_alarm(self) -> bool:
        """Drop the helper's wake. Clears the RTC only when the pending
        alarm is still the one we set; True when something was cleared."""
        owned = self._load_wake_owned()
        cleared = False
        if owned is not None and self._read_wakealarm() == owned:
            self._write_file(self.wakealarm_path(), '0')
            cleared = True
        self._save_wake_owned(None)
        return cleared

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
        set, then the inbound firewall. One leg failing must not skip
        the others, so errors are collected and reported together.
        Persisted blocked paths are validated again (a tampered or stale
        entry is dropped, never handed to nft) and the pruned set saved;
        the firewall chain is installed only when it was enabled."""
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
                    live = self.apply_blocked(blocked)
                    applied['blocked'] = live
                    if live != sorted(blocked):
                        fresh = self.load_state()
                        fresh['blocked'] = live
                        self.save_state(fresh)
                except HelperError as err:
                    applied['errors'].append(str(err))
        inbound = self.load_inbound()
        if inbound.get('enabled') is not True:
            self._remove_inbound_dropin()
        else:
            try:
                if self.firewall_backend() == 'nftables':
                    self._install_inbound(inbound['allowed'])
                applied['inbound'] = sorted(inbound['allowed'])
            except HelperError as err:
                applied['errors'].append(str(err))
        return applied


# ------------------------------------------------------------- PIN store

def pin_lockout_secs(fail_count: int) -> int:
    """Seconds of lockout after ``fail_count`` total wrong PINs — the
    same escalation as the shell's ``lock_lines._lockout_secs`` (5 free
    tries, then 30 s per extra miss, capped at 5 min)."""
    if fail_count < PIN_FAIL_THRESHOLD:
        return 0
    return min((fail_count - PIN_FAIL_THRESHOLD + 1) * PIN_LOCKOUT_STEP,
               PIN_LOCKOUT_CAP)


def _valid_pin_text(pin: object) -> bool:
    return (isinstance(pin, str) and 0 < len(pin) <= PIN_MAX_LEN
            and '\x00' not in pin)


class PinStore:
    """Per-uid PIN hashes, verification and the persisted lockout.

    Every method takes the caller's uid as resolved from the bus
    (``GetConnectionUnixUser``), never from an argument, so a caller can
    only ever reach its own record. ``root`` and ``now`` (wall clock)
    are injectable for tests, as are the scrypt cost parameters.
    """

    def __init__(self, *, root: str = os.path.join(STATE_DIR_DEFAULT,
                                                   PIN_DIR_NAME),
                 now=time.time, scrypt_n: int = PIN_SCRYPT_N,
                 pbkdf2_iterations: int = PIN_PBKDF2_ITERATIONS) -> None:
        self.root = root
        self._now = now
        self._scrypt_n = scrypt_n
        self._pbkdf2_iterations = pbkdf2_iterations

    # ---------------------------------------------------------- storage

    def record_path(self, uid: int) -> str:
        uid = int(uid)
        if uid < 0:
            raise HelperError(ERROR_INVALID_ARGS, 'bad uid')
        return os.path.join(self.root, '%d.json' % uid)

    def _load(self, uid: int) -> dict:
        try:
            with open(self.record_path(uid), 'r', encoding='utf-8') as fh:
                raw = json.load(fh)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            # A corrupt record must fail closed: keep "a PIN exists" with
            # a hash nothing matches, never collapse to "no PIN".
            return {'hash': 'corrupt'}
        return raw if isinstance(raw, dict) else {'hash': 'corrupt'}

    def _save(self, uid: int, record: dict) -> None:
        path = self.record_path(uid)
        try:
            os.makedirs(self.root, mode=0o700, exist_ok=True)
            os.chmod(self.root, 0o700)
            tmp = path + '.tmp'
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                         | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                json.dump(record, fh, sort_keys=True)
                fh.write('\n')
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except OSError as err:
            raise _failed('cannot persist PIN record: %s'
                          % (err.strerror or err)) from None

    def _delete(self, uid: int) -> None:
        try:
            os.unlink(self.record_path(uid))
        except FileNotFoundError:
            pass
        except OSError as err:
            raise _failed('cannot remove PIN record: %s'
                          % (err.strerror or err)) from None

    # -------------------------------------------------------------- KDF

    def hash_pin(self, pin: str) -> str:
        import hashlib
        salt = os.urandom(16)
        scrypt = getattr(hashlib, 'scrypt', None)
        if scrypt is not None:
            n, r, p = self._scrypt_n, PIN_SCRYPT_R, PIN_SCRYPT_P
            digest = scrypt(pin.encode(), salt=salt, n=n, r=r, p=p,
                            maxmem=256 * r * n + (1 << 20), dklen=32)
            return 'scrypt$%d$%d$%d$%s$%s' % (n, r, p, salt.hex(),
                                              digest.hex())
        iters = self._pbkdf2_iterations
        digest = hashlib.pbkdf2_hmac('sha256', pin.encode(), salt, iters)
        return 'pbkdf2$%d$%s$%s' % (iters, salt.hex(), digest.hex())

    @staticmethod
    def check_hash(pin: str, stored: object) -> bool:
        import hashlib
        import hmac
        if not isinstance(stored, str):
            return False
        try:
            parts = stored.split('$')
            if parts[0] == 'scrypt' and len(parts) == 6:
                n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
                expected = bytes.fromhex(parts[5])
                digest = hashlib.scrypt(
                    pin.encode(), salt=bytes.fromhex(parts[4]), n=n, r=r,
                    p=p, maxmem=256 * r * n + (1 << 20), dklen=len(expected))
            elif parts[0] == 'pbkdf2' and len(parts) == 4:
                expected = bytes.fromhex(parts[3])
                digest = hashlib.pbkdf2_hmac(
                    'sha256', pin.encode(), bytes.fromhex(parts[2]),
                    int(parts[1]))
            else:
                return False
        except (ValueError, TypeError, AttributeError, MemoryError):
            return False
        return hmac.compare_digest(digest, expected)

    # ---------------------------------------------------------- lockout

    def _retry_after(self, record: dict) -> int:
        """Seconds left on the persisted lockout. Like the shell's
        resume_lockout_secs: never longer than the fail count earns,
        and a clock that went back before the lockout began clears the
        running lockout (the count stays)."""
        fails = record.get('fails', 0)
        fails = fails if isinstance(fails, int) and fails > 0 else 0
        until = record.get('until', 0.0)
        start = record.get('start', 0.0)
        try:
            until = float(until)
            start = float(start)
        except (TypeError, ValueError):
            until = start = 0.0
        now = self._now()
        if start > 0 and now < start:
            return 0
        if fails <= 0 or until <= now:
            return 0
        return max(0, min(int(until - now + 0.999), pin_lockout_secs(fails)))

    def _check(self, uid: int, record: dict, pin: str) -> tuple:
        """(matched, retry_after) against an existing record, updating
        and persisting the fail counter. A live lockout refuses without
        running the KDF at all."""
        retry = self._retry_after(record)
        if retry > 0:
            return False, retry
        if _valid_pin_text(pin) and self.check_hash(pin, record.get('hash')):
            if record.get('fails') or record.get('until'):
                record = {'hash': record.get('hash'), 'v': 1}
                self._save(uid, record)
            return True, 0
        fails = record.get('fails', 0)
        fails = (fails if isinstance(fails, int) and fails > 0 else 0) + 1
        secs = pin_lockout_secs(fails)
        now = self._now()
        record = dict(record, fails=fails, until=now + secs if secs else 0.0,
                      start=now if secs else 0.0)
        self._save(uid, record)
        return False, secs

    # ---------------------------------------------------------- methods

    def has_pin(self, uid: int) -> bool:
        return bool(self._load(uid).get('hash'))

    def verify_pin(self, uid: int, pin: str) -> tuple:
        """(matched, retry_after, has_pin)."""
        record = self._load(uid)
        if not record.get('hash'):
            return False, 0, False
        matched, retry = self._check(uid, record, pin)
        return matched, retry, True

    def set_pin(self, uid: int, old: str, new: str) -> tuple:
        """(done, retry_after). Without a record ``old`` is ignored; with
        one it must verify first (counting toward the lockout)."""
        if not _valid_pin_text(new):
            raise HelperError(ERROR_INVALID_ARGS, 'invalid PIN')
        record = self._load(uid)
        if record.get('hash'):
            matched, retry = self._check(uid, record, old)
            if not matched:
                return False, retry
        self._save(uid, {'hash': self.hash_pin(new), 'v': 1})
        return True, 0

    def clear_pin(self, uid: int, old: str) -> tuple:
        """(done, retry_after): removes the record once ``old`` verifies."""
        record = self._load(uid)
        if not record.get('hash'):
            return True, 0
        matched, retry = self._check(uid, record, old)
        if not matched:
            return False, retry
        self._delete(uid)
        return True, 0


# ------------------------------------------------------- authorization (Gio)

def _require_gio():
    """Gio/GLib are imported here, not at module top, so the logic above
    stays importable (and unit-testable) without PyGObject."""
    # gi.repository is generated at runtime; there are no stubs to check.
    from gi.repository import Gio, GLib  # pyright: ignore[reportAttributeAccessIssue]
    return Gio, GLib


def get_connection_uid(connection, sender, callback) -> None:
    """Async ``GetConnectionUnixUser``: ``callback(uid|None, error|None)``."""
    Gio, GLib = _require_gio()

    def _on_user(conn, result) -> None:
        try:
            uid = int(conn.call_finish(result).unpack()[0])
        except Exception as err:  # noqa: BLE001 (callback fires once)
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
    opening an inbound port, turning the firewall on or off).
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
            except Exception as err:  # noqa: BLE001 (any failure denies)
                callback(False, uid, str(err))
                return
            callback(authorized, uid, None)

        # Params are the subject struct, the action id, the details dict,
        # the flags and the cancellation id (empty: never cancelled).
        # polkitd rejects the call without the last one ("does not match
        # expected type ((sa{sv})sa{ss}us)"), which denied every action.
        # Nested plain tuples convert; a pre-built Variant in struct
        # position does not on this PyGObject.
        params = GLib.Variant(
            '((sa{sv})sa{ss}us)',
            (('system-bus-name', {'name': GLib.Variant('s', sender)}),
             action, {}, ALLOW_INTERACTION if interactive else 0, ''),
        )
        try:
            connection.call(
                POLKIT_BUS, POLKIT_PATH, POLKIT_IFACE, 'CheckAuthorization',
                params,
                None, Gio.DBusCallFlags.NONE,
                POLKIT_INTERACTIVE_TIMEOUT_MS if interactive
                else POLKIT_TIMEOUT_MS,
                None, _on_checked,
            )
        except Exception as err:  # noqa: BLE001 (any failure denies)
            callback(False, uid, str(err))

    get_connection_uid(connection, sender, _on_uid)


def _polkit_authorize(connection, sender, action, interactive: bool,
                      callback) -> None:
    """HelperService's ``authorize`` contract over
    :func:`check_polkit_authorization`, whose callback comes first."""
    check_polkit_authorization(connection, sender, action, callback,
                               interactive=interactive)


# Method -> (polkit action, interactive): interactive passes
# AllowUserInteraction and is reserved for auth_admin_keep actions.
# Argument-dependent methods (ManageService, CleanSystem,
# SetInboundPreset, SetInboundAllowed, SetFirewallEnabled) are NOT
# listed here:
# policy_for() below decides
# them from the already-validated arguments. SetPowerProfile is listed
# but policy_for() still allowlists its profile first, and SetWakeAlarm
# its argument shape (the time window is checked by HelperCore against
# its clock). GetCapabilities
# and GetFirewallState need no auth and are dispatched before any
# lookup.
_METHOD_ACTIONS = {
    'SetChargeLimit': (ACTION_CHARGE, False),
    'SetAppNetwork': (ACTION_NETWORK, False),
    'FreezeCgroup': (ACTION_FREEZE, False),
    'RefreshPackageIndex': (ACTION_REFRESH, False),
    'UpgradeSystem': (ACTION_UPGRADE, True),
    'RemovePackage': (ACTION_REMOVE, True),
    'ReadLog': (ACTION_READLOG, True),
    'SetRfkill': (ACTION_RFKILL, False),
    'SetPowerProfile': (ACTION_POWER, False),
    'SetWakeAlarm': (ACTION_WAKE, False),
    'ClearWakeAlarm': (ACTION_WAKE, False),
}


def policy_for(method: str, args) -> tuple | None:
    """(polkit action, interactive) for a method call, or None.

    Every call goes through here. Plain methods map statically.
    Argument-dependent ones decide from the already-validated
    arguments: opening remote access (ssh/adbd start/enable), stopping or
    disabling the modem or network, vacuuming the journal, opening an
    inbound port, and turning the firewall on (it drops new inbound
    connections, SSH included unless sshd is up) or off need
    auth_admin_keep with user interaction (True); closing a port and
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
    if method == 'SetWakeAlarm':
        try:
            (epoch,) = args
        except (TypeError, ValueError):
            return None
        if isinstance(epoch, bool) or not isinstance(epoch, int):
            return None
        return _METHOD_ACTIONS[method]
    if method == 'ClearWakeAlarm':
        if tuple(args or ()) != ():
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
        if name in CRITICAL_SERVICES and action in ('stop', 'disable'):
            return (ACTION_SERVICE_CRITICAL, True)
        return (ACTION_SERVICE, False)
    if method == 'CleanSystem':
        try:
            (cats,) = args
        except (TypeError, ValueError):
            return None
        if not isinstance(cats, (list, tuple)):
            return None
        if 'journal-vacuum' in cats:
            return (ACTION_CLEAN_JOURNAL, True)
        return (ACTION_CLEAN, False)
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
        if not isinstance(enabled, bool):
            return None
        return (ACTION_FW_ENABLE, True) if enabled else (ACTION_FW_OPEN,
                                                         True)
    return _METHOD_ACTIONS.get(method)


class HelperService:
    """D-Bus object: auth dispatch around a :class:`HelperCore`.

    ``authorize(conn, sender, action, interactive, callback)`` and
    ``get_uid`` are injectable for tests; in production they are
    :func:`_polkit_authorize` (polkit, which also resolves the uid) and
    :func:`get_connection_uid`. ``now`` injects the clock for the idle
    timer, which is held off while a polkit check or a package job is
    still running.
    """

    def __init__(self, connection=None, core=None, authorize=None,
                 now=time.monotonic, pin_store=None, get_uid=None) -> None:
        self.connection = connection
        self.core = core if core is not None else HelperCore()
        self._authorize = authorize or _polkit_authorize

        # PIN methods need no polkit action: the caller's bus uid selects
        # the record, so every user can only touch their own PIN.
        self.pins = pin_store if pin_store is not None else PinStore()
        self._get_uid = get_uid or get_connection_uid
        self._now = now
        self._last_activity = self._now()
        self._registration_id: int | None = None
        self._pending_auth = 0
        # job_id -> caller's unique bus name (JobFinished goes only there).
        self.jobs: dict[str, str | None] = {}

    def touch(self) -> None:
        self._last_activity = self._now()

    def idle_expired(self) -> bool:
        """True once 60 s passed without a method call and nothing is in
        flight (pure; the GLib timer in :func:`main` quits the loop on it).
        Exiting during a password prompt or a package job would drop the
        caller's reply or orphan the job's JobFinished."""
        if self._pending_auth or self.jobs:
            return False
        return (self._now() - self._last_activity) >= IDLE_EXIT_SECONDS

    # ------------------------------------------------------------- dispatch

    def register(self) -> None:
        if self.connection is None:
            raise RuntimeError('register() needs a bus connection')
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
            except Exception as err:  # noqa: BLE001 (D-Bus reply boundary)
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
            except Exception as err:  # noqa: BLE001 (D-Bus reply boundary)
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
        if method in PIN_METHODS:
            self._dispatch_pin(conn, sender, method, args, invocation)
            return
        policy = policy_for(method, args)
        if policy is None or conn is None:
            invocation.return_dbus_error(
                ERROR_INVALID_ARGS, 'unknown method: %s' % (method,))
            return
        action, interactive = policy
        self._pending_auth += 1
        answered = []

        def _on_auth(ok, uid, err) -> None:
            if answered:
                return
            answered.append(True)
            self._pending_auth -= 1
            self.touch()
            self._on_authorized(invocation, method, args, ok, uid, err,
                                sender=sender)

        try:
            self._authorize(conn, sender, action, interactive, _on_auth)
        except Exception as err:  # noqa: BLE001 (D-Bus reply boundary)
            if not answered:
                answered.append(True)
                self._pending_auth -= 1
            invocation.return_dbus_error(ERROR_FAILED, str(err))

    def _dispatch_pin(self, conn, sender, method, args, invocation) -> None:
        """PIN methods: resolve the sender's uid, then run against that
        uid's record only. Argument shapes are checked before the bus
        round-trip; PIN text never reaches an error message or log."""
        expected = {'HasPin': 0, 'VerifyPin': 1, 'SetPin': 2, 'ClearPin': 1}
        if (conn is None or not isinstance(args, tuple)
                or len(args) != expected[method]
                or not all(isinstance(a, str) for a in args)):
            invocation.return_dbus_error(
                ERROR_INVALID_ARGS, 'bad arguments for %s' % (method,))
            return

        def _on_uid(uid, err) -> None:
            if err is not None or uid is None:
                invocation.return_dbus_error(
                    ERROR_NOT_AUTHORIZED, err or 'unknown sender')
                return
            self._run_pin(invocation, method, args, int(uid))

        try:
            self._get_uid(conn, sender, _on_uid)
        except Exception as err:  # noqa: BLE001 (D-Bus reply boundary)
            invocation.return_dbus_error(ERROR_FAILED, str(err))

    def _run_pin(self, invocation, method, args, uid: int) -> None:
        self.touch()
        try:
            if method == 'HasPin':
                sig, out = '(b)', (self.pins.has_pin(uid),)
            elif method == 'VerifyPin':
                matched, retry, has = self.pins.verify_pin(uid, args[0])
                sig, out = '(bub)', (bool(matched), int(retry), bool(has))
            elif method == 'SetPin':
                done, retry = self.pins.set_pin(uid, args[0], args[1])
                sig, out = '(bu)', (bool(done), int(retry))
            else:
                done, retry = self.pins.clear_pin(uid, args[0])
                sig, out = '(bu)', (bool(done), int(retry))
        except HelperError as err:
            invocation.return_dbus_error(err.dbus_name, str(err))
            return
        except Exception as err:  # noqa: BLE001 (type only: no PIN in reply)
            invocation.return_dbus_error(ERROR_FAILED, type(err).__name__)
            return
        Gio, GLib = _require_gio()
        invocation.return_value(GLib.Variant(sig, out))

    def _begin_job(self, invocation, sender) -> str:
        """Reply with a fresh job id. One package job at a time: apk and
        pacman hold an exclusive lock anyway."""
        if self.jobs:
            raise _failed('another package operation is still running')
        job_id = uuid.uuid4().hex
        self.jobs[job_id] = sender
        Gio, GLib = _require_gio()
        invocation.return_value(GLib.Variant('(s)', (job_id,)))
        return job_id

    def _spawn_step(self, job_id: str, argv: list[str], on_exit) -> None:
        try:
            self.core.spawn(argv, on_exit)
        except Exception as err:  # noqa: BLE001 (the job must still finish)
            self._finish_job(job_id, -1, '%s failed to start: %s' % (
                argv[0], type(err).__name__))

    def _start_job(self, invocation, argv: list[str], sender) -> None:
        """Reply with a job id, then run ``argv`` with no timeout."""
        job_id = self._begin_job(invocation, sender)
        self._spawn_step(job_id, argv, lambda rc, output: self._finish_job(
            job_id, rc, output))

    def _start_clean_job(self, invocation, steps, sender) -> None:
        """Reply with a job id, then run the cleanup steps one after
        another. A step whose tool turns out missing (127) is skipped; any
        other failure ends the job with that step's last output line.
        Success lists the cleaned categories, one per line."""
        job_id = self._begin_job(invocation, sender)
        cleaned: list[str] = []

        def _run(index: int) -> None:
            if index == len(steps):
                self._finish_job(job_id, 0, '\n'.join(cleaned))
                return
            category, argv = steps[index]

            def _exit(rc, output) -> None:
                if rc == 0:
                    cleaned.append(category)
                elif rc != 127:
                    last = (output or '').strip().splitlines()[-1:]
                    self._finish_job(job_id, rc, 'clean %s: %s' % (
                        category, last[0] if last else 'exit %d' % rc))
                    return
                _run(index + 1)

            self._spawn_step(job_id, argv, _exit)

        _run(0)

    def _finish_job(self, job_id: str, rc, output) -> None:
        if job_id not in self.jobs:
            return
        sender = self.jobs.pop(job_id)
        self.touch()
        if self.connection is None:
            return
        Gio, GLib = _require_gio()
        try:
            self.connection.emit_signal(
                sender, OBJECT_PATH, INTERFACE, 'JobFinished',
                GLib.Variant('(sis)', (job_id, int(rc), job_tail(output))))
        except GLib.Error as err:
            print('xx-wm-helper: JobFinished not delivered: %s' % err,
                  file=sys.stderr)

    def _on_authorized(self, invocation, method, args, authorized, uid,
                       auth_error, sender=None) -> None:
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
                plan = self.core.clean_steps(
                    [str(c) for c in cats] if isinstance(cats, list) else cats)
                self._start_clean_job(invocation, plan['steps'], sender)
                return
            elif method == 'ReadLog':
                (log_name,) = args
                text = self.core.read_log(str(log_name))
                Gio, GLib = _require_gio()
                invocation.return_value(GLib.Variant('(s)', (text,)))
                return
            elif method == 'RefreshPackageIndex':
                self._start_job(invocation, self.core.refresh_argv(), sender)
                return
            elif method == 'UpgradeSystem':
                self._start_job(invocation, self.core.upgrade_argv(), sender)
                return
            elif method == 'RemovePackage':
                (pkg,) = args
                self._start_job(invocation, self.core.remove_argv(str(pkg)),
                                sender)
                return
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
            elif method == 'SetWakeAlarm':
                (epoch,) = args
                self.core.set_wake_alarm(int(epoch))
            elif method == 'ClearWakeAlarm':
                self.core.clear_wake_alarm()
            else:
                invocation.return_dbus_error(
                    ERROR_INVALID_ARGS, 'unknown method: %s' % (method,))
                return
        except HelperError as err:
            # One reply per invocation: an error must not fall through to
            # the success reply below.
            invocation.return_dbus_error(err.dbus_name, str(err))
            return
        except Exception as err:  # noqa: BLE001 (D-Bus reply boundary)
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
        help='re-apply the persisted charge limit, nft blocks and '
             'firewall, then exit '
             '(used by the boot service; takes no bus name)',
    )
    return parser


# dbus-daemon starts system services with no PATH at all, so which() and
# exec fell back to /bin:/usr/bin and missed nft and rfkill in /usr/sbin
# (FLX1s: GetFirewallState answered backend "none").
SAFE_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'


def ensure_path(environ=os.environ) -> None:
    if not environ.get('PATH'):
        environ['PATH'] = SAFE_PATH


def main(argv: list[str] | None = None) -> int:
    ensure_path()
    args = build_arg_parser().parse_args(argv)
    if args.apply_saved:
        applied = HelperCore().apply_saved()
        for err in applied.get('errors', []):
            print('xx-wm-helper: apply-saved: %s' % err, file=sys.stderr)
        return 0 if not applied.get('errors') else 1

    Gio, GLib = _require_gio()
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    except GLib.Error as err:
        print('xx-wm-helper: no system bus: %s' % err, file=sys.stderr)
        return 1
    service = HelperService(bus)
    try:
        service.register()
    except GLib.Error as err:
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
