"""Phone-integration backends: KDE Connect, Syncthing, adbd, SSH address.

GTK-free pure logic plus thin worker-only probes. Every blocking call
goes through an injectable ``run`` (the caller passes
``system_settings._run`` from a settings-page worker thread), so this
module never touches the main loop and tests never touch real
services, packages, the firewall or the network.
"""
from __future__ import annotations

import shutil

KDECONNECT_UNIT = 'kdeconnectd.service'
SYNCTHING_UNIT = 'syncthing.service'
SYNCTHING_URL = 'http://127.0.0.1:8384'

KDE_BIN = 'kdeconnect-cli'
SYNCTHING_BIN = 'syncthing'
ADB_BIN = 'adbd'

SSH_PORT = 22
ADB_PORT = 5555

USER_UNITS = ('kdeconnectd.service', 'syncthing.service')
USER_ACTIONS = ('start', 'stop', 'restart', 'enable', 'disable')

PACKAGES = {
    'ssh': {'apk': ('openssh',), 'apt': ('openssh-server',),
            'pacman': ('openssh',)},
    'kdeconnect': {'apk': ('kdeconnect',), 'apt': ('kdeconnect',),
                   'pacman': ('kdeconnect',)},
    'syncthing': {'apk': ('syncthing',), 'apt': ('syncthing',),
                  'pacman': ('syncthing',)},
    'adbd': {'apk': (), 'apt': ('adbd', 'android-tools-adbd'),
             'pacman': ()},
}

BINARIES = {
    'kdeconnect': KDE_BIN,
    'syncthing': SYNCTHING_BIN,
    'adbd': ADB_BIN,
}


def packages_for(feature: str, distro: str) -> tuple:
    """Installer package names for ``feature`` on ``distro``.

    ``distro`` is one of apk/apt/pacman. Empty means the distro has no
    package for the daemon (adbd on apk/pacman): the row hides there.
    Raises KeyError outside the curated features.
    """
    return tuple(PACKAGES[feature][distro])


def binary_available(name: str, which=None) -> bool:
    return (which or shutil.which)(name) is not None


def adbd_available(which=None) -> bool:
    return binary_available(ADB_BIN, which)


def user_unit_argv(unit: str, action: str) -> list:
    """``systemctl --user`` argv for a user unit (unprivileged).

    Allowlisted units and actions only; anything else raises
    ValueError so callers can never reach another unit.
    """
    if unit not in USER_UNITS or action not in USER_ACTIONS:
        raise ValueError('unknown user service or action: %r %r'
                         % (unit, action))
    return ['systemctl', '--user', action, unit]


def parse_is_active(stdout: str, returncode: int) -> tuple:
    """(is_active, state) from ``systemctl --user is-active``."""
    state = (stdout or '').strip().splitlines()
    word = state[0].strip() if state else ''
    if not word:
        return None, ''
    return (True if returncode == 0 and word == 'active' else False), word


def parse_is_enabled(stdout: str, returncode: int) -> tuple:
    """(is_enabled, state) from ``systemctl --user is-enabled``."""
    state = (stdout or '').strip().splitlines()
    word = state[0].strip() if state else ''
    if not word:
        return None, ''
    return (returncode == 0 and word == 'enabled'), word


def parse_kdeconnect_devices(output: str) -> list:
    """Device descriptions from ``kdeconnect-cli --list-devices``.

    Header lines, blanks and the no-devices notice are skipped; every
    other line is one device.
    """
    devices = []
    for line in (output or '').splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if lowered.startswith(('available devices', 'paired devices',
                               'reachable devices')):
            continue
        if 'no devices' in lowered:
            continue
        devices.append(stripped)
    return devices


def parse_sshd_password_allowed(text: str) -> bool | None:
    """True when passwords are allowed, False for key-only, None unknown.

    Last uncommented ``PasswordAuthentication`` wins; ``yes``/``no``
    only, anything else reads as unknown.
    """
    found = None
    for line in (text or '').splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        head, _, value = stripped.partition('#')
        fields = head.split()
        if len(fields) < 2 or fields[0].lower() != 'passwordauthentication':
            continue
        option = fields[1].lower()
        if option == 'yes':
            found = True
        elif option == 'no':
            found = False
    return found


def ssh_password_allowed(path: str = '/etc/ssh/sshd_config'
                         ) -> bool | None:
    """Password-auth verdict from the daemon config, None when unreadable.

    Worker only (file read). Never writes: enabling SSH preserves
    whatever is configured and never flips password auth on silently.
    """
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            text = handle.read()
    except OSError:
        return None
    return parse_sshd_password_allowed(text)


def ssh_port_listening(ss_output: str, port: int = SSH_PORT) -> bool:
    """True when ``ss -tln`` output shows a listener on ``port``."""
    needle = ':%d' % port
    for line in (ss_output or '').splitlines():
        fields = line.split()
        if not fields or fields[0] in ('Netid', 'State'):
            continue
        for field in fields:
            if needle in field and ':' in field:
                return True
    return False


def first_lan_address(ip_brief_output: str) -> str | None:
    """First non-loopback address from ``ip -brief addr show``."""
    for line in (ip_brief_output or '').splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[0] == 'lo':
            continue
        for field in fields[2:]:
            if '/' in field:
                addr = field.split('/')[0]
                if addr and not addr.startswith('127.'):
                    return addr
    return None


def describe_user_service(installed: bool, active_state, enabled_state,
                          devices=None, error=None) -> str:
    """Row subtitle for a user service: true state plus failure reason.

    Mirrors the services section wording (On/Off/Failed/Unavailable
    plus starts-at-boot) without importing its UI module.
    """
    if not installed:
        return 'Not installed'
    if error:
        return 'Unavailable · %s' % error
    if active_state is None:
        return 'Unavailable'
    if active_state in ('active', 'activating', True):
        base = 'On'
    elif active_state == 'failed':
        base = 'Failed'
    else:
        base = 'Off'
    if enabled_state == 'enabled' or enabled_state is True:
        base += ', starts at boot'
    if devices:
        base += ' · %s' % (
            ', '.join(devices[:2]) + ('…'
                                      if len(devices) > 2 else ''))
    return base


def describe_ssh(base: str, listening: bool, address=None,
                 password_allowed=None) -> str:
    """SSH row subtitle: base state plus address and key-only note."""
    text = base or 'Unavailable'
    if listening and address:
        text += ' · %s:%d' % (address, SSH_PORT)
    elif listening:
        text += ' · port %d open' % SSH_PORT
    if password_allowed is True:
        text += ' · password allowed'
    elif password_allowed is False:
        text += ' · key-only'
    return text


def _default_run(argv, timeout: int = 10):
    import system_settings
    return system_settings._run(argv, timeout=timeout)


def _call(run, argv: list, timeout: int):
    if run is None:
        run = _default_run
    try:
        return run(argv, timeout=timeout)
    except Exception as err:
        return err


def probe_user_service(unit: str, binary: str, run=None, which=None,
                       list_argv=None) -> dict:
    """Worker-only probe: installed/active/enabled/devices/error."""
    if not binary_available(binary, which):
        return {'installed': False, 'active': None, 'enabled': None,
                'active_state': None, 'enabled_state': None,
                'devices': [], 'error': None}
    active = _call(run, ['systemctl', '--user', 'is-active', unit], 10)
    if isinstance(active, Exception):
        return {'installed': True, 'active': None, 'enabled': None,
                'active_state': None, 'enabled_state': None,
                'devices': [], 'error': str(active)}
    enabled = _call(run, ['systemctl', '--user', 'is-enabled', unit], 10)
    if isinstance(enabled, Exception):
        return {'installed': True, 'active': None, 'enabled': None,
                'active_state': None, 'enabled_state': None,
                'devices': [], 'error': str(enabled)}
    is_active, active_state = parse_is_active(
        getattr(active, 'stdout', '') or '',
        getattr(active, 'returncode', 1))
    is_enabled, enabled_state = parse_is_enabled(
        getattr(enabled, 'stdout', '') or '',
        getattr(enabled, 'returncode', 1))
    devices: list = []
    if list_argv:
        listing = _call(run, list_argv, 10)
        if not isinstance(listing, Exception) and getattr(
                listing, 'returncode', 1) == 0:
            devices = parse_kdeconnect_devices(
                getattr(listing, 'stdout', '') or '')
    return {'installed': True, 'active': is_active, 'enabled': is_enabled,
            'active_state': active_state or None,
            'enabled_state': enabled_state or None,
            'devices': devices, 'error': None}


def kdeconnect_probe(run=None, which=None) -> dict:
    return probe_user_service(
        KDECONNECT_UNIT, KDE_BIN, run, which,
        list_argv=[KDE_BIN, '--list-devices'])


def syncthing_probe(run=None, which=None) -> dict:
    return probe_user_service(SYNCTHING_UNIT, SYNCTHING_BIN, run, which)


def adbd_probe(run=None, which=None):
    """None when the daemon is unavailable (the row hides)."""
    if not adbd_available(which):
        return None
    active = _call(run, ['systemctl', 'is-active', 'adbd.service'], 10)
    if isinstance(active, Exception):
        return {'installed': True, 'active': None, 'enabled': None,
                'active_state': None, 'enabled_state': None,
                'error': str(active)}
    is_active, active_state = parse_is_active(
        getattr(active, 'stdout', '') or '',
        getattr(active, 'returncode', 1))
    return {'installed': True, 'active': is_active, 'enabled': None,
            'active_state': active_state or None, 'enabled_state': None,
            'error': None}


def ssh_net_probe(run=None) -> dict:
    """Listening flag plus first LAN address for the SSH row."""
    heard = _call(run, ['ss', '-tln'], 10)
    shown = _call(run, ['ip', '-brief', 'addr', 'show'], 10)
    if isinstance(heard, Exception):
        return {'listening': False, 'address': None}
    listening = ssh_port_listening(getattr(heard, 'stdout', '') or '')
    address = None
    if not isinstance(shown, Exception) and getattr(
            shown, 'returncode', 1) == 0:
        address = first_lan_address(getattr(shown, 'stdout', '') or '')
    return {'listening': listening, 'address': address}


def set_user_unit(unit: str, action: str, run=None) -> None:
    """Run one allowlisted user-service action, raising on failure."""
    argv = user_unit_argv(unit, action)
    proc = _call(run, argv, 30)
    if isinstance(proc, Exception):
        raise proc
    if getattr(proc, 'returncode', 1) != 0:
        detail = ((getattr(proc, 'stderr', '') or '')
                  or (getattr(proc, 'stdout', '') or '')).strip()
        raise OSError(detail.splitlines()[-1] if detail else 'exit %d'
                      % getattr(proc, 'returncode', 1))
