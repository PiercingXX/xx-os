"""File backup to removable or network storage (ref backup-restore.sh).

Pure transfer logic: volume listing, destination safety rules, rsync
argv building and dry-run parsing. Long work (dry run, transfer with
progress) runs on worker threads; only `_run_capture` and
`run_transfer` touch subprocess.
"""
from __future__ import annotations

import os
import re
import socket
import subprocess

BACKUP_DIRNAME = 'xx-backup'

DANGEROUS_DESTS = frozenset((
    '/', '/bin', '/boot', '/dev', '/etc', '/home', '/lib', '/lib64',
    '/opt', '/proc', '/root', '/run', '/sbin', '/srv', '/sys', '/usr',
    '/var',
))


def _run_capture(argv: list, timeout: int = 300):
    """Sole capturing subprocess chokepoint; worker threads only."""
    return subprocess.run(argv, capture_output=True, text=True,
                          timeout=timeout, check=False)


def home_dir() -> str:
    return os.path.expanduser('~')


def backup_sources(home=None) -> list:
    """Source folders: media dirs that exist plus xx-* app data."""
    base = home if home is not None else home_dir()
    found = []
    for rel in ('Pictures', 'Documents', 'Videos'):
        path = os.path.join(base, rel)
        if os.path.isdir(path):
            found.append(path)
    appdata = os.path.join(base, '.local', 'share')
    try:
        names = sorted(os.listdir(appdata))
    except OSError:
        names = []
    for name in names:
        if name.startswith('xx-') and os.path.isdir(
                os.path.join(appdata, name)):
            found.append(os.path.join(appdata, name))
    return found


def list_volumes(user=None, run_media=None, media_root='/media') -> list:
    """Mounted removable destinations: /run/media/$USER/* and /media/*."""
    if user is None:
        try:
            user = os.getlogin()
        except OSError:
            user = os.environ.get('USER', '')
    roots = []
    if run_media is None and user:
        run_media = '/run/media/%s' % user
    if run_media:
        roots.append(run_media)
    roots.append(media_root)
    found = []
    for root in roots:
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for name in names:
            path = os.path.join(root, name)
            if os.path.isdir(path) and path not in found:
                found.append(path)
    return found


def backup_host() -> str:
    """This phone's folder name under xx-backup/: the hostname, made safe
    as one path component."""
    try:
        name = socket.gethostname()
    except OSError:
        name = ''
    name = re.sub(r'[^A-Za-z0-9._-]', '-', name or '').strip('.-')
    return name or 'phone'


def backup_target(volume: str, src: str, host=None) -> str:
    """<volume>/xx-backup/<host>/<folder>/ for one source folder. Never
    the volume itself: Mirror deletes whatever else sits at the
    destination, and Restore copies the destination back whole."""
    folder = os.path.basename(os.path.normpath(src)) if src else ''
    if not volume or folder in ('', '.', '..', os.sep):
        return ''
    return os.path.join(volume, BACKUP_DIRNAME,
                        host if host is not None else backup_host(), folder)


def dangerous_dest(dest: str, home=None) -> bool:
    """True for /, system trees — never usable as a destination."""
    return os.path.normpath(dest) in DANGEROUS_DESTS


def validate_transfer(src: str, dest: str, mirror: bool,
                      home=None) -> tuple:
    """(ok, error): the script's dangerous-destination and nesting rules."""
    base = home if home is not None else home_dir()
    if not src or not os.path.isdir(src):
        return False, 'Source folder does not exist.'
    if not dest:
        return False, 'Pick a destination folder.'
    src_norm = os.path.normpath(src)
    dest_norm = os.path.normpath(dest)
    if src_norm == dest_norm:
        return False, 'Source and destination are the same.'
    if dangerous_dest(dest_norm, home=base):
        return False, 'Refusing to write to %s.' % dest_norm
    if mirror and dest_norm == os.path.normpath(base):
        return False, 'Refusing to mirror into your whole home folder.'
    if mirror and os.path.ismount(dest_norm):
        return False, 'Refusing to mirror onto a whole drive.'
    return True, None


def _self_exclude(src: str, dest: str) -> list:
    """Exclude the backup from itself when dest nests inside src."""
    prefix = os.path.normpath(src) + os.sep
    if os.path.normpath(dest).startswith(prefix):
        rel = os.path.normpath(dest)[len(prefix):].split(os.sep)[0]
        return ['--exclude=/%s/' % rel]
    return []


def build_rsync_argv(src: str, dest: str, mirror: bool) -> list:
    """Transfer argv: -aH, --delete only for opt-in Mirror mode."""
    argv = ['rsync', '-aH', '--info=stats1,progress2']
    if mirror:
        argv.append('--delete')
    argv.extend(_self_exclude(src, dest))
    argv.append(src.rstrip('/') + '/')
    argv.append(dest.rstrip('/') + '/')
    return argv


def build_dry_run_argv(src: str, dest: str, mirror: bool) -> list:
    """Preview argv: -aHn --itemize-changes, never touching anything."""
    argv = ['rsync', '-aHn', '--itemize-changes']
    if mirror:
        argv.append('--delete')
    argv.extend(_self_exclude(src, dest))
    argv.append(src.rstrip('/') + '/')
    argv.append(dest.rstrip('/') + '/')
    return argv


def parse_dry_run_counts(output: str) -> tuple:
    """(copy, delete) from --itemize-changes, like the desktop preview."""
    copy = delete = 0
    for line in (output or '').splitlines():
        if line.startswith('*deleting'):
            delete += 1
        elif len(line) > 1 and line[0] in '<>ch.' and line[1] == 'f':
            copy += 1
    return copy, delete


def dry_run_counts(src: str, dest: str, mirror: bool,
                   run=_run_capture) -> tuple:
    """Preview on a worker: (ok, 'N files to copy, M to delete')."""
    try:
        result = run(build_dry_run_argv(src, dest, mirror))
    except Exception as err:
        return False, 'Preview failed: %s' % err
    if getattr(result, 'returncode', 1) not in (0, 23, 24):
        stderr = (getattr(result, 'stderr', '') or '').strip()
        return False, 'Preview failed: %s' % (stderr or 'rsync error')
    copy, delete = parse_dry_run_counts(getattr(result, 'stdout', ''))
    text = '%d file%s to copy' % (copy, '' if copy == 1 else 's')
    if delete:
        text += ', %d to delete' % delete
    return True, text


def run_transfer(src: str, dest: str, mirror: bool, total: int,
                 on_progress, run_progress=None) -> tuple:
    """Copy on a worker thread with progress; (ok, error-or-None).

    `on_progress(fraction)` fires from this worker — the caller
    re-posts to the main loop. `total` is the dry-run copy count.
    """
    import subprocess as _sp
    argv = build_rsync_argv(src, dest, mirror)
    try:
        # rsync creates only the last path component.
        os.makedirs(dest, exist_ok=True)
        proc = _sp.Popen(argv, stdout=_sp.PIPE, stderr=_sp.STDOUT,
                         text=True, bufsize=1)
    except OSError as err:
        return False, 'Backup failed to start: %s' % err
    try:
        done = 0
        assert proc.stdout is not None
        for line in proc.stdout:
            if len(line) > 1 and line[0] in '<>ch.' and line[1] == 'f':
                done += 1
                if total > 0:
                    try:
                        on_progress(min(1.0, done / total))
                    except Exception:
                        pass
        code = proc.wait(timeout=3600)
    except Exception as err:
        try:
            proc.kill()
        except Exception:
            pass
        return False, 'Backup failed: %s' % err
    if code not in (0, 23, 24):
        return False, 'Backup failed (rsync exit %d).' % code
    try:
        on_progress(1.0)
    except Exception:
        pass
    return True, None


def progress_fraction(line: str) -> float | None:
    """Fraction from an rsync --info=progress2 'x% ' line, else None."""
    match = re.search(r'(\d{1,3})%', line or '')
    if not match:
        return None
    return min(1.0, int(match.group(1)) / 100.0)
