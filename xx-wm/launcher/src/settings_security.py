"""Opt-in malware scan and report-only security check (ref virus-scan.sh).

Clamscan only (never clamd), lowered priority, hard size limits. The
section hides itself when clamscan is not installed. Package
verification (`apk audit --system`, `pacman -Qkk`) runs unprivileged on
a worker and only reports; socket/SUID/authorized_keys findings diff
against a saved baseline.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess

import settings_page
import settings_ui as ui

MAX_FILESIZE_MB = 1024
MAX_SCANSIZE_MB = 2048
MAX_RECURSION = 32
MAX_FILES = 20000

LIMIT_RE = 'Heuristics.Limits.Exceeded'


def _run(argv: list, timeout: int = 120):
    """Sole subprocess chokepoint; callers run on worker threads only."""
    return subprocess.run(argv, capture_output=True, text=True,
                          timeout=timeout, check=False)


def clamscan_available(which=None) -> bool:
    return (which or shutil.which)('clamscan') is not None


def build_clamscan_argv(target: str, recursive: bool, nice: bool = True,
                        ionice: bool = True) -> list:
    """Low-priority clamscan with the desktop script's size limits."""
    argv: list = []
    if nice:
        argv.extend(('nice', '-n', '19'))
    if ionice and shutil.which('ionice') is not None:
        argv.extend(('ionice', '-c3'))
    argv.extend((
        'clamscan',
        '--max-filesize=%dM' % MAX_FILESIZE_MB,
        '--max-scansize=%dM' % MAX_SCANSIZE_MB,
        '--max-recursion=%d' % MAX_RECURSION,
        '--max-files=%d' % MAX_FILES,
        '--alert-exceeds-max=yes',
        '--cross-fs=no',
        '--follow-dir-symlinks=0',
        '--follow-file-symlinks=0',
        '--detect-pua=yes',
    ))
    if recursive:
        argv.append('-r')
    argv.append(target)
    return argv


def parse_clamscan_output(output: str) -> list:
    """(path, signature) detections; size-limit skips are not infections."""
    found = []
    for line in (output or '').splitlines():
        if not line.endswith(' FOUND'):
            continue
        if LIMIT_RE in line:
            continue
        body = line[:-len(' FOUND')]
        path, _, sig = body.rpartition(': ')
        if path and sig:
            found.append((path, sig))
    return found


def default_quarantine_dir(home=None) -> str:
    base = home if home is not None else os.path.expanduser('~')
    data = os.environ.get('XDG_DATA_HOME') or os.path.join(
        base, '.local', 'share')
    return os.path.join(data, 'piercingxx', 'quarantine')


quarantine_dir = default_quarantine_dir


def quarantine_file(path: str, signature: str,
                    quarantine_dir=None) -> str:
    """Move an infected file aside, chmod 0400, leave a metadata sidecar."""
    import time
    dest_dir = quarantine_dir or default_quarantine_dir()
    os.makedirs(dest_dir, mode=0o700, exist_ok=True)
    with open(path, 'rb') as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    try:
        mode = '%o' % (os.stat(path).st_mode & 0o7777)
    except OSError:
        mode = 'unknown'
    stamp = time.strftime('%Y%m%d-%H%M%S')
    dest = os.path.join(dest_dir, '%s-%d.quar' % (stamp, os.getpid()))
    os.rename(path, dest)
    os.chmod(dest, 0o400)
    with open(dest.replace('.quar', '.meta'), 'w',
              encoding='utf-8') as handle:
        handle.write('path=%s\nsignature=%s\nsha256=%s\nmode=%s\ndate=%s\n'
                     % (path, signature, digest, mode,
                        time.strftime('%F %T')))
    return dest


def baseline_dir(home=None) -> str:
    base = home if home is not None else os.path.expanduser('~')
    data = os.environ.get('XDG_DATA_HOME') or os.path.join(
        base, '.local', 'share')
    return os.path.join(data, 'piercingxx', 'baseline')


def baseline_diff(name: str, lines: list, state_dir=None) -> tuple:
    """Diff `lines` against the saved baseline; first run only saves.

    Returns (changed, diff_lines) with '< old' / '> new' markers.
    """
    directory = state_dir or baseline_dir()
    os.makedirs(directory, mode=0o700, exist_ok=True)
    current = sorted(lines)
    path = os.path.join(directory, name)
    try:
        with open(path, encoding='utf-8') as handle:
            saved = handle.read().splitlines()
    except OSError:
        saved = None
    if saved is None:
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write('\n'.join(current) + '\n' if current else '')
        return False, []
    if saved == current:
        return False, []
    old, new = set(saved), set(current)
    diff = ['< %s' % line for line in sorted(old - new)]
    diff += ['> %s' % line for line in sorted(new - old)]
    with open(os.path.join(directory, '%s.new' % name), 'w',
              encoding='utf-8') as handle:
        handle.write('\n'.join(current) + '\n' if current else '')
    return True, diff


def accept_baseline(name: str, state_dir=None) -> None:
    """Promote the last '.new' snapshot to the saved baseline."""
    directory = state_dir or baseline_dir()
    pending = os.path.join(directory, '%s.new' % name)
    if os.path.isfile(pending):
        os.replace(pending, os.path.join(directory, name))


def parse_ss_listening(output: str) -> list:
    """'proto local:port' lines from `ss -tuln` (header skipped)."""
    found = []
    for line in (output or '').splitlines():
        fields = line.split()
        if not fields:
            continue
        if fields[0] in ('Netid', 'State'):
            continue
        if len(fields) >= 6 and ':' in (fields[4] or ''):
            found.append('%s %s' % (fields[0], fields[4]))
        elif len(fields) >= 5 and fields[0] in (
                'LISTEN', 'UNCONN') and ':' in fields[3]:
            found.append('tcp %s' % fields[3])
    return sorted(set(found))


def hash_file(path: str):
    """sha256 hex of one file; None when unreadable."""
    try:
        with open(path, 'rb') as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None


def package_verify_argv(which=None):
    """Read-only package verification for apk and pacman (apt: debsums)."""
    lookup = which or shutil.which
    if lookup('apk'):
        return ['apk', 'audit', '--system']
    if lookup('pacman'):
        return ['pacman', '-Qkk']
    if lookup('debsums'):
        return ['debsums', '-s']
    return None


def collect_security_report(run=_run, state_dir=None,
                            suid_roots=None) -> dict:
    """Report-only findings with baseline diffs; runs on a worker."""
    report: dict = {'listening': (False, []), 'suid': (False, []),
                    'keys': (False, []), 'packages': None}
    try:
        sockets = run(['ss', '-tuln'], timeout=30)
        lines = parse_ss_listening(
            getattr(sockets, 'stdout', '') or '') \
            if getattr(sockets, 'returncode', 1) == 0 else []
    except Exception:
        lines = []
    report['listening'] = baseline_diff('listening', lines,
                                        state_dir=state_dir)
    try:
        found = run(['find'] + list(suid_roots or ['/']) + [
            '-xdev', '-perm', '/6000', '-type', 'f'], timeout=600)
        if getattr(found, 'returncode', 1) == 0:
            suid = sorted((getattr(found, 'stdout', '') or '').splitlines())
        else:
            suid = []
    except Exception:
        suid = []
    report['suid'] = baseline_diff('suid', suid, state_dir=state_dir)
    keys = []
    ssh = os.path.join(os.path.expanduser('~'), '.ssh')
    for name in ('authorized_keys', 'authorized_keys2'):
        digest = hash_file(os.path.join(ssh, name))
        if digest:
            keys.append('%s:%s' % (name, digest))
    report['keys'] = baseline_diff('authorized_keys', keys,
                                   state_dir=state_dir)
    argv = package_verify_argv()
    if argv is not None:
        try:
            result = run(argv, timeout=600)
        except Exception as err:
            result = None
            report['packages'] = 'Verification failed: %s' % err
        if result is not None:
            out = (getattr(result, 'stdout', '') or '').strip()
            report['packages'] = out if out else 'No altered files.'
    return report


class SecuritySection(settings_page._Section):
    """Opt-in malware scan plus a report-only security check.

    Hidden entirely (no widgets) when clamscan is not installed.
    """

    key = 'security'
    title = 'Security'

    def __init__(self) -> None:
        self._status = None
        self._results_box = None
        self._scan_rows: list = []
        self._report = None
        self._check_box = None
        self._check_row = None
        self._accept_row = None
        self._scan_btn = None
        self._detections: list = []
        self._scanning = False

    def build(self, win) -> list:
        if not clamscan_available():
            return []
        self._scan_btn = ui.nav_row(
            'Scan a file…', 'Pick a file or folder by path',
            lambda r: self._open_scan_file(win, r))
        self._scan_rows = [
            ui.nav_row('Scan Downloads', 'ClamAV, on demand',
                       lambda _r: self._on_scan_downloads(win)),
            self._scan_btn]
        scan = ui.group('Malware scan', list(self._scan_rows), note='')
        self._results_box = scan[1]
        self._status = scan[2]
        self._check_row = ui.nav_row(
            'Run security check', 'Listening ports, SUID files, SSH keys, '
            'packages', lambda _r: self._on_check(win))
        self._accept_row = ui.nav_row(
            'Accept as new baseline', 'Stop flagging these changes',
            lambda _r: self._on_accept(win))
        check = ui.group('Security check', [self._check_row], note='')
        self._check_box = check[1]
        self._report = check[2]
        return scan + check

    def refresh(self, win) -> None:
        return None

    # ------------------------------------------------------------ behavior

    def _on_scan_downloads(self, win) -> None:
        import os as _os
        target = _os.path.join(_os.path.expanduser('~'), 'Downloads')
        if not _os.path.isdir(target):
            self._say('No Downloads folder.')
            return
        self._start_scan(win, target, recursive=True)

    def _open_scan_file(self, win, anchor):
        return ui.form_sheet(
            win, anchor, 'Scan a file',
            [('path', 'File or folder', '~/Downloads/', 'text')],
            'Scan', lambda values: self._on_scan_file(win, values))

    def _on_scan_file(self, win, values: dict) -> str | None:
        """Sheet commit: an error string keeps the sheet open."""
        target = str(values.get('path', '')).strip()
        if not target:
            return 'Enter a file to scan.'
        import os as _os
        target = _os.path.expanduser(target)
        if not _os.path.exists(target):
            return 'File does not exist.'
        self._start_scan(win, target, recursive=_os.path.isdir(target))
        return None

    def _start_scan(self, win, target: str, recursive: bool) -> None:
        from settings_page import _run_bg
        if self._scanning:
            return
        self._scanning = True
        self._say('Scanning…')

        def _work():
            try:
                result = _run(build_clamscan_argv(target, recursive))
            except Exception as err:
                return {'error': str(err)}
            if getattr(result, 'returncode', 1) not in (0, 1):
                stderr = (getattr(result, 'stderr', '') or '').strip()
                return {'error': stderr or 'Scan failed.'}
            return {'found': parse_clamscan_output(
                getattr(result, 'stdout', '') or '')}

        _run_bg(_work, lambda res: self._on_scan_done(win, target, res))

    def _on_scan_done(self, win, target: str, res) -> None:
        self._scanning = False
        if not isinstance(res, dict):
            return
        if res.get('error'):
            self._say(res['error'])
            return
        found = res.get('found') or []
        self._detections = list(found)
        if not found:
            self._say('No threats found in %s.' % target)
        else:
            self._say('%d threat%s found.' % (
                len(found), '' if len(found) == 1 else 's'))
        self._render_results(win)

    def _render_results(self, win) -> None:
        if self._results_box is None:
            return
        rows = [ui.action_row(
            sig, 'Quarantine',
            lambda _r, p=path, s=sig: self._on_quarantine(win, p, s),
            subtitle=path) for path, sig in self._detections[:20]]
        ui.fill(self._results_box, list(self._scan_rows) + rows)

    def _on_quarantine(self, win, path: str, sig: str) -> None:
        win.item_actions._show_action_menu(
            self._scan_btn,
            'Quarantine %s? It is moved out of reach.' % path,
            [('Quarantine',
              lambda: self._do_quarantine(win, path, sig)),
             ('Cancel', lambda: None)])

    def _do_quarantine(self, win, path: str, sig: str) -> None:
        from settings_page import _run_bg

        def _work():
            try:
                return {'dest': quarantine_file(path, sig)}
            except Exception as err:
                return {'error': str(err)}

        def _done(res) -> None:
            if not isinstance(res, dict):
                return
            if res.get('error'):
                self._say('Quarantine failed: %s' % res['error'])
                return
            self._detections = [(p, s) for p, s in self._detections
                                if p != path]
            self._render_results(win)
            self._say('Quarantined.')

        _run_bg(_work, _done)

    def _show_accept(self, visible: bool) -> None:
        if self._check_box is None:
            return
        ui.fill(self._check_box, [self._check_row] + (
            [self._accept_row] if visible else []))

    def _on_check(self, win) -> None:
        from settings_page import _run_bg
        ui.set_subtitle(self._check_row, 'Checking…')
        self._show_accept(False)

        def _work():
            try:
                return collect_security_report()
            except Exception as err:
                return {'error': str(err)}

        _run_bg(_work, lambda res: self._on_report(win, res))

    def _on_report(self, win, res) -> None:
        if not isinstance(res, dict) or res.get('error'):
            ui.set_subtitle(self._check_row, res.get('error')
                            if isinstance(res, dict) else 'Check failed.')
            return
        parts = []
        for label, key in (('Listening sockets', 'listening'),
                           ('SUID binaries', 'suid'),
                           ('SSH keys', 'keys')):
            changed, diff = res.get(key, (False, []))
            if changed:
                parts.append('%s changed: %s' % (
                    label, '; '.join(diff[:6])))
            else:
                parts.append('%s: no changes.' % label)
        packages = res.get('packages')
        if packages is None:
            parts.append('Package verification: unavailable.')
        else:
            short = packages if len(packages) < 400 else (
                packages[:400] + '…')
            parts.append('Packages: %s' % short)
        ui.say(self._report, ' '.join(parts))
        changed_any = any(res.get(key, (False, []))[0] for key in (
            'listening', 'suid', 'keys'))
        self._show_accept(bool(changed_any))
        ui.set_subtitle(self._check_row, 'Changes found · review below'
                        if changed_any else 'No changes since the baseline')

    def _on_accept(self, win) -> None:
        for name in ('listening', 'suid', 'authorized_keys'):
            try:
                accept_baseline(name)
            except OSError:
                pass
        self._show_accept(False)
        ui.set_subtitle(self._check_row, 'Baseline saved')

    def _say(self, text: str) -> None:
        ui.say(self._status, text)
