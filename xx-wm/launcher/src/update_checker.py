from __future__ import annotations

import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from gi.repository import GLib

from shell_log import get_logger

_log = get_logger('update')

CHECK_INTERVAL = 24 * 3600
SNOOZE_INTERVAL = 7 * 24 * 3600

# First match wins; each entry maps a terminal binary to the argv that runs a
# shell command string inside it. GNOME Console first: FuriOS/PureOS phones
# ship it (as kgx, with gnome-console as the long name) instead of the
# desktop terminals below.
_TERMINALS: list[tuple[str, Callable[[str, str], list[str]]]] = [
    ('kgx', lambda sh, cmd: ['kgx', '-e', sh, '-c', cmd]),
    ('gnome-console', lambda sh, cmd: ['gnome-console', '-e', sh, '-c', cmd]),
    ('kitty', lambda sh, cmd: ['kitty', sh, '-c', cmd]),
    ('foot', lambda sh, cmd: ['foot', sh, '-c', cmd]),
    ('alacritty', lambda sh, cmd: ['alacritty', '-e', sh, '-c', cmd]),
    ('gnome-terminal', lambda sh, cmd: ['gnome-terminal', '--', sh, '-c', cmd]),
    ('xterm', lambda sh, cmd: ['xterm', '-e', sh, '-c', cmd]),
]


def terminal_argv(cmd: str) -> tuple[str, list[str]] | None:
    """(binary, argv) running `cmd` in the first available terminal
    emulator, or None when no terminal is installed. Pure lookup apart
    from the which() probes."""
    if shutil.which('bash'):
        # read -rsn1 exits on any single key — including the Escape the shell's
        # back gesture synthesizes — so an edge swipe closes the terminal.
        shell = 'bash'
        full = (f'{cmd}; echo; echo "Done — swipe from the screen edge '
                'to close."; read -rsn1 _')
    else:
        shell = 'sh'
        full = f'{cmd}; echo; echo "Done — press Enter to close."; read _'
    for binary, build in _TERMINALS:
        if shutil.which(binary):
            return binary, build(shell, full)
    return None


# The owner's update-system.sh prompts (Arch news, conflict retries,
# pacdiff) and asks sudo for a password, so it wants a real terminal.
# Only these four are probed, in this order: kgx is what FuriOS/PureOS
# phones ship, then the usual phone-friendly fallbacks. Without any of
# them the script still runs headless: with no TTY its ask_yes takes the
# defaults, but sudo has no polkit/pkexec path (the script calls
# sudo/doas directly), so headless needs cached sudo or passwordless
# doas and skips every N-default prompt (mirror refresh, pacdiff).
_UPDATE_TERMINALS: list[tuple[str, Callable[[str, str], list[str]]]] = [
    ('kgx', lambda sh, cmd: ['kgx', '-e', sh, '-c', cmd]),
    ('foot', lambda sh, cmd: ['foot', sh, '-c', cmd]),
    ('gnome-terminal', lambda sh, cmd: ['gnome-terminal', '--', sh, '-c', cmd]),
    ('xterm', lambda sh, cmd: ['xterm', '-e', sh, '-c', cmd]),
]


def update_terminal_argv(cmd: str, which=None) -> tuple[str, list[str]] | None:
    """(binary, argv) running `cmd` in the first available update terminal
    (kgx, foot, gnome-terminal, xterm), or None when none is installed."""
    probe = which or shutil.which
    if probe('bash'):
        shell = 'bash'
        full = (f'{cmd}; echo; echo "Done — swipe from the screen edge '
                'to close."; read -rsn1 _')
    else:
        shell = 'sh'
        full = f'{cmd}; echo; echo "Done — press Enter to close."; read _'
    for binary, build in _UPDATE_TERMINALS:
        if probe(binary):
            return binary, build(shell, full)
    return None


def xx_apply_suffix(which=None) -> str:
    """' && xx-apps-update --apply' when the updater is installed, else ''.

    One tap updates everything: the owner script first, then the XX apps
    in the same terminal session. ``&&`` (not ``;``) so an aborted owner
    run does not roll into app installs.
    """
    probe = which or shutil.which
    try:
        if probe('xx-apps-update'):
            return ' && xx-apps-update --apply'
    except Exception:
        pass
    return ''


def plan_update(script_path: str, which=None) -> tuple[str, str, list[str] | None]:
    """How Settings runs the owner script: 'terminal' with the argv to
    open, 'headless' with the argv to run detached (stdin closed, so the
    script's prompts take their defaults), or 'missing' when the script
    is not installed and the built-in flow stays in charge."""
    path = Path(script_path).expanduser()
    if not path.exists():
        return 'missing', f'Update script not found: {path}', None
    cmd = shlex.quote(str(path)) + xx_apply_suffix(which=which)
    found = update_terminal_argv(cmd, which=which)
    if found is not None:
        binary, argv = found
        return 'terminal', binary, argv
    return ('headless',
            'No terminal emulator found — running without prompts.', [str(path)])


def run_owner_headless(script_path: str, on_line, on_done):
    """Run the owner script with stdin detached on a worker thread.

    Each stdout line streams back through on_line(line) on the main
    loop; on_done(returncode) closes the run (-1 when the script could
    not even start). Returns the worker thread so tests can join it."""
    def _headless_worker() -> None:
        try:
            proc = subprocess.Popen(
                [str(script_path)], stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                text=True, close_fds=True)
        except OSError as error:
            _log.warning('headless update failed to start: %s', error)
            GLib.idle_add(on_done, -1)
            return
        assert proc.stdout is not None
        for line in proc.stdout:
            GLib.idle_add(on_line, line.rstrip('\n'))
        GLib.idle_add(on_done, proc.wait())

    thread = threading.Thread(target=_headless_worker, daemon=True)
    thread.start()
    return thread


def run_terminal_command(cmd: str) -> tuple[bool, str]:
    """Open `cmd` in the first available terminal emulator (fire-and-
    forget, never waited on)."""
    found = terminal_argv(cmd)
    if found is None:
        return False, 'No terminal emulator found (kitty, foot, alacritty, ...).'
    binary, argv = found
    try:
        subprocess.Popen(argv, close_fds=True)
        return True, binary
    except OSError as e:
        _log.warning('failed to launch %s: %s', binary, e)
        return False, 'Could not open a terminal.'


def run_update_script(script_path: str) -> tuple[bool, str]:
    """Open the update script in the first available terminal emulator."""
    path = Path(script_path).expanduser()
    if not path.exists():
        return False, f'Update script not found: {path}'

    return run_terminal_command(shlex.quote(str(path)))


# Package indexes `apt list --upgradable` / `apk list --upgradable` read go
# stale: without a recent refresh they under-report, so a 0 from a stale
# index must never be presented as "up to date". A backend is only trusted
# while one of its index paths has been written within _INDEX_MAX_AGE. The
# shell never refreshes indexes itself — that needs root.
# `checkupdates` syncs its own temporary database on every run, so it
# needs no freshness gate: gating it on /var/lib/pacman/sync only hides
# real updates behind a stale system sync.
_INDEX_PATHS: dict[str, list[str]] = {
    'apt': ['/var/lib/apt/lists'],
    'apk': ['/var/cache/apk', '/lib/apk/db'],
    'pacman': ['/var/lib/pacman/sync'],
}
_INDEX_MAX_AGE = 24 * 3600


def _newest_mtime(path: str) -> float | None:
    """Newest mtime of a file or directory tree root (one level), or None
    when the path cannot be inspected."""
    import os
    try:
        newest = os.path.getmtime(path)
        if os.path.isdir(path):
            with os.scandir(path) as entries:
                for entry in entries:
                    try:
                        newest = max(newest, entry.stat().st_mtime)
                    except OSError:
                        pass
        return newest
    except OSError:
        return None


def _index_fresh(binary: str, max_age_s: float = _INDEX_MAX_AGE,
                 now: float | None = None) -> bool:
    """True when `binary` has a package index fresh enough to trust."""
    candidates = _INDEX_PATHS.get(binary)
    if not candidates:
        return True
    now = time.time() if now is None else now
    for candidate in candidates:
        mtime = _newest_mtime(candidate)
        if mtime is not None and now - mtime <= max_age_s:
            return True
    return False


def count_updates() -> int | None:
    """Best-effort count of upgradable packages across the distros we
    target. None means unknown: no backend installed, every usable
    backend's index is stale, or the listing failed — callers must report
    that as unknown, never as "up to date"."""
    checks: list[tuple[str, list[str], Callable[[str], int]]] = [
        ('apk', ['apk', 'list', '--upgradable'],
         lambda out: sum(1 for line in out.splitlines() if line.strip())),
        ('checkupdates', ['checkupdates'],
         lambda out: sum(1 for line in out.splitlines() if line.strip())),
        ('apt', ['apt', 'list', '--upgradable'],
         lambda out: sum(1 for line in out.splitlines()[1:] if line.strip())),
        ('pacman', ['pacman', '-Qu'],
         lambda out: sum(1 for line in out.splitlines() if line.strip())),
    ]
    for binary, argv, parse in checks:
        if not shutil.which(binary):
            continue
        if not _index_fresh(binary):
            _log.info('update count via %s skipped: package index is stale',
                      binary)
            continue
        try:
            result = subprocess.run(
                argv, capture_output=True, text=True, timeout=120, check=False,
            )
            return parse(result.stdout)
        except (OSError, subprocess.TimeoutExpired) as e:
            _log.warning('update count via %s failed: %s', binary, e)
            return None
    return None


class UpdateChecker:
    """Once-a-day update availability check with a one-week snooze.

    on_updates_available(count) fires on the main loop when a scheduled check
    finds updates. check_now() bypasses the daily/snooze gates and always
    reports through on_result(count).
    """

    def __init__(
        self,
        config: object,
        on_updates_available: Callable[[int], None],
    ) -> None:
        self._config = config
        self._on_updates_available = on_updates_available
        self._checking = False
        GLib.timeout_add_seconds(90, self._initial_check)
        GLib.timeout_add_seconds(3600, self._tick)

    def _initial_check(self) -> bool:
        self.maybe_check()
        return False

    def _tick(self) -> bool:
        self.maybe_check()
        return True

    def maybe_check(self) -> None:
        now = time.time()
        if now < self._config.update_snooze_until:
            return
        if now - self._config.update_last_check < CHECK_INTERVAL:
            return
        self._start_check(self._report_scheduled)

    def check_now(self, on_result: Callable[[int | None], None]) -> None:
        self._start_check(on_result)

    def _start_check(self, report: Callable[[int | None], None]) -> None:
        if self._checking:
            return
        self._checking = True

        def worker() -> None:
            count = count_updates()
            GLib.idle_add(self._finish_check, count, report)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_check(self, count: int | None,
                      report: Callable[[int | None], None]) -> bool:
        self._checking = False
        self._config.set_update_last_check(time.time())
        if count is None:
            _log.info('update check: status unknown (stale index)')
        else:
            _log.info('update check: %d packages upgradable', count)
        report(count)
        return False

    def _report_scheduled(self, count: int | None) -> None:
        if count is not None and count > 0:
            self._on_updates_available(count)

    def snooze(self) -> None:
        self._config.set_update_snooze_until(time.time() + SNOOZE_INTERVAL)
