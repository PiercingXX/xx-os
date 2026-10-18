"""Software-update flow helpers: reboot notice, update log, dots row.

File reads and the ``xx-apps-update --check`` probe only (no D-Bus): the
blocking package commands live in system_settings (worker threads) and the
privileged apk work in the root helper. ``home``/``sysfs`` paths are
injectable so tests never touch the real machine. ``run_xx_apps_check``
shells out and must run on a worker thread, never the main loop.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess

LOG_TAIL_LINES = 15
XX_APPS_BINARY = 'xx-apps-update'
XX_APPS_CHECK_TIMEOUT = 60
XX_APPS_APPLY_TIMEOUT = 1800
XX_APPS_UP_TO_DATE = 'All XX apps are up to date.'

OWNER_SCRIPT_REL = os.path.join(
    '.scripts', 'PiercingXX-Settings-Menu', 'update-system.sh')

_ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
_STEP_RE = re.compile(r'^  (\S(?:.*\S)?)\s+(ok|skipped|FAILED)\s*$')
_REBOOT_HEAD_RE = re.compile(r'^Reboot recommended:\s*$')
_REBOOT_ITEM_RE = re.compile(r'^  - (.*\S)\s*$')
_LOG_RE = re.compile(r'^Full log:\s*(\S.*\S|\S)\s*$')


def log_dir(home: str | None = None) -> str:
    """The shell's update-log directory (matches update-system.sh's
    ``$PX_STATE_DIR/logs`` layout for one shared history)."""
    base = home if home is not None else os.path.expanduser('~')
    return os.path.join(base, '.local', 'state', 'piercingxx', 'logs')


def latest_update_log(home: str | None = None,
                      directory: str | None = None,
                      tail_lines: int = LOG_TAIL_LINES,
                      ) -> tuple[str, str] | None:
    """(filename, tail) of the newest ``update-*.log``, or None when no
    log exists yet. Only the tail is read: logs can be megabytes."""
    folder = directory if directory is not None else log_dir(home)
    try:
        names = sorted(name for name in os.listdir(folder)
                       if name.startswith('update-') and name.endswith('.log'))
    except OSError:
        return None
    if not names:
        return None
    path = os.path.join(folder, names[-1])
    try:
        with open(path, encoding='utf-8', errors='replace') as handle:
            lines = handle.read().splitlines()
    except OSError:
        return None
    tail = [line for line in lines if line.strip()][-tail_lines:]
    return names[-1], '\n'.join(tail) if tail else '(empty log)'


def new_log_path(home: str | None = None) -> str:
    """Fresh timestamped log path for an update run starting now; the
    parent directory is created (mode 0700) like every other XDG state
    dir the shell owns."""
    import time
    folder = log_dir(home)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime())
    return os.path.join(folder, 'update-%s.log' % stamp)


def append_log(path: str, text: str) -> None:
    """Append one update step's output to the run log; never raises."""
    try:
        with open(path, 'a', encoding='utf-8') as handle:
            handle.write(text)
            if not text.endswith('\n'):
                handle.write('\n')
    except OSError:
        pass


def reboot_required(uname_release: str | None = None,
                    lib_modules: str = '/lib/modules',
                    usr_lib_modules: str = '/usr/lib/modules',
                    flag_file: str = '/var/run/reboot-required',
                    ) -> str | None:
    """Why a restart is needed to finish updates, or None.

    Mirrors update-system.sh: the running kernel was replaced (its
    module directory is gone) or a package left a reboot-required flag.
    """
    release = uname_release
    if release is None:
        release = os.uname().release
    gone = (not os.path.isdir(os.path.join(lib_modules, release))
            and not os.path.isdir(os.path.join(usr_lib_modules, release)))
    if gone:
        return 'Running kernel %s was replaced.' % (release,)
    if os.path.isfile(flag_file):
        return 'Installed packages requested a restart.'
    return None


def owner_script_path(script: str | None = None,
                      home: str | None = None) -> str | None:
    """Resolved owner update-script path when it exists, else None.

    ``script`` overrides (the configured update_script value); otherwise
    the piercing-dots layout (``~/.scripts/PiercingXX-Settings-Menu/``,
    where the installer and the script's own self-update put it)."""
    if script:
        candidate = os.path.expanduser(script)
    else:
        base = home if home is not None else os.path.expanduser('~')
        candidate = os.path.join(base, OWNER_SCRIPT_REL)
    return candidate if os.path.isfile(candidate) else None


def parse_owner_summary(text: str) -> dict:
    """Result of an owner update-system.sh run from its own output.

    Reads the end-of-run table (``Update summary`` with ok/skipped/FAILED
    rows), the ``Reboot recommended:`` reasons, and the ``Full log:``
    path. Returns steps, failure count, reboot reasons, the log path,
    and whether the run finished (vs the package upgrade dying early).
    ANSI colors are stripped first: live output has them, log files do
    not. Unknown stays unknown: no table means no verdict either way."""
    steps: list[tuple[str, str]] = []
    reboot_reasons: list[str] = []
    log_path: str | None = None
    in_reboot = False
    for raw in (text or '').splitlines():
        line = _ANSI_RE.sub('', raw)
        step = _STEP_RE.match(line)
        if step is not None:
            steps.append((step.group(1), step.group(2)))
            in_reboot = False
            continue
        if _REBOOT_HEAD_RE.match(line):
            in_reboot = True
            continue
        if in_reboot:
            item = _REBOOT_ITEM_RE.match(line)
            if item is not None:
                reason = item.group(1)
                if reason not in reboot_reasons:
                    reboot_reasons.append(reason)
                continue
            if line.strip():
                in_reboot = False
        logged = _LOG_RE.match(line)
        if logged is not None:
            log_path = logged.group(1)
    failures = sum(1 for _label, status in steps if status == 'FAILED')
    plain = _ANSI_RE.sub('', text or '')
    finished = 'System update finished.' in plain
    core_failed = ('did not complete' in plain
                   or 'System update failed' in plain)
    return {'steps': steps, 'failures': failures,
            'reboot_reasons': reboot_reasons, 'log_path': log_path,
            'finished': finished, 'core_failed': core_failed}


def format_count(repo: int | None, flatpak: int | None) -> str:
    """One status line for a check result: unknown stays unknown, never
    a false 'up to date'."""
    if repo is None and flatpak is None:
        return 'Update status unknown — package index is stale.'
    total = (repo or 0) + (flatpak or 0)
    if total == 0:
        return 'System is up to date.'
    plural = 's' if total != 1 else ''
    bits = []
    if repo:
        bits.append('%d package%s' % (repo, 's' if repo != 1 else ''))
    if flatpak:
        bits.append('%d Flatpak%s' % (flatpak, 's' if flatpak != 1 else ''))
    return '%d update%s available (%s).' % (total, plural, ', '.join(bits))


def xx_apps_binary(which=None) -> str | None:
    """Resolved ``xx-apps-update`` binary, or None when not installed."""
    probe = which or shutil.which
    try:
        found = probe(XX_APPS_BINARY)
    except Exception:
        return None
    return found or None


def xx_apply_command(which=None) -> list[str] | None:
    """``xx-apps-update --apply`` argv, or None when not installed."""
    binary = xx_apps_binary(which=which)
    return [binary, '--apply'] if binary else None


def run_xx_apps_check(which=None, run=None,
                      timeout: int = XX_APPS_CHECK_TIMEOUT,
                      ) -> tuple[int | None, str]:
    """(returncode, output) of ``xx-apps-update --check``.

    Shells out with a timeout: call only on a worker thread. (None, '')
    when the updater is not installed or the probe itself fails — callers
    report that as unknown, never as up to date.
    """
    binary = xx_apps_binary(which=which)
    if binary is None:
        return None, ''
    run_fn = run or subprocess.run
    try:
        result = run_fn([binary, '--check'], capture_output=True, text=True,
                        timeout=timeout, check=False)
    except Exception:
        return None, ''
    return result.returncode, result.stdout or ''


def parse_xx_check_output(text: str) -> tuple[int, list[str]]:
    """(count, app names) from ``xx-apps-update --check`` output."""
    apps: list[str] = []
    for line in (text or '').splitlines():
        line = line.strip()
        if not line or line == XX_APPS_UP_TO_DATE:
            continue
        head = line.split(' ', 1)[0]
        if head and '/' not in head and '\\' not in head:
            apps.append(head)
    return len(apps), apps


def xx_status_title(returncode: int | None, output: str) -> tuple[str, str]:
    """(title, subtitle) for the Settings XX-apps row: one line plus the
    app names underneath. Unknown stays unknown, never 'up to date'."""
    if returncode == 0:
        return 'XX apps: up to date', ''
    if returncode == 10:
        count, apps = parse_xx_check_output(output)
        if count == 0:
            return 'XX apps: status unknown', ''
        plural = 's' if count != 1 else ''
        return ('XX apps: %d update%s' % (count, plural), ', '.join(apps))
    return 'XX apps: status unknown', ''
