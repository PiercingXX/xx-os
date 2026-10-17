"""Installed app sizes for the drawer's "APK size" sort (Android parity).

Android sorts by APK size; the Linux counterpart is the installed size
of whatever ships the app: the Flatpak (one ``flatpak list`` call) or
the distro package that owns its .desktop file (one batched ``pacman
-Qi`` / ``dpkg-query`` call, or ``apk info -s`` per package). Lookups are
blocking, so ``SizeCache.ensure`` runs them on a worker thread and calls
back on the main loop; until then unknown apps sort as size 0.
"""
from __future__ import annotations

import re
import threading
from typing import Callable

_UNITS = {
    '': 1, 'b': 1, 'bytes': 1,
    'k': 1000, 'kb': 1000, 'kib': 1024,
    'm': 1000 ** 2, 'mb': 1000 ** 2, 'mib': 1024 ** 2,
    'g': 1000 ** 3, 'gb': 1000 ** 3, 'gib': 1024 ** 3,
}
_SIZE_RE = re.compile(r'([0-9]+(?:[.,][0-9]+)?)\s*([A-Za-z]*)')


def parse_size(text: str) -> int:
    """Bytes from '247.5 MB', '9.59 MiB', '123 KiB' or '2048'; 0 if not."""
    match = _SIZE_RE.search(str(text or '').replace(' ', ' '))
    if match is None:
        return 0
    number = float(match.group(1).replace(',', '.'))
    unit = _UNITS.get(match.group(2).casefold())
    if unit is None:
        return 0
    return int(number * unit)


def parse_flatpak_sizes(output: str) -> dict[str, int]:
    """``flatpak list --app --columns=application,size`` → {id: bytes}."""
    sizes: dict[str, int] = {}
    for line in (output or '').splitlines():
        parts = line.split('\t')
        if len(parts) >= 2 and parts[0].strip():
            sizes[parts[0].strip()] = parse_size(parts[1])
    return sizes


def parse_pacman_sizes(output: str) -> dict[str, int]:
    """``pacman -Qi pkg…`` → {pkg: bytes} from Name / Installed Size."""
    sizes: dict[str, int] = {}
    name = None
    for line in (output or '').splitlines():
        key, _sep, value = line.partition(':')
        key = key.strip()
        if key == 'Name':
            name = value.strip()
        elif key == 'Installed Size' and name:
            sizes[name] = parse_size(value)
    return sizes


def parse_dpkg_sizes(output: str) -> dict[str, int]:
    """``dpkg-query -W -f '${Package}\\t${Installed-Size}\\n'`` (KiB)."""
    sizes: dict[str, int] = {}
    for line in (output or '').splitlines():
        parts = line.split('\t')
        if len(parts) >= 2 and parts[0].strip():
            try:
                sizes[parts[0].strip()] = int(parts[1].strip()) * 1024
            except ValueError:
                continue
    return sizes


def compute_sizes(entries, run=None, resolve=None) -> dict[str, int]:
    """(Blocking: worker only.) {app_id: installed bytes} for ``entries``."""
    import system_settings

    run = run or system_settings._run
    if resolve is None:
        from settings_apps import resolve_owner_for_entry as resolve
    flatpaks: dict[str, str] = {}
    owners: dict[str, list[str]] = {}
    backend = None
    for entry in entries:
        try:
            info = resolve(entry)
        except Exception:
            continue
        if info.get('flatpak'):
            flatpaks[entry.app_id] = info['flatpak']
        elif info.get('owner'):
            backend = info.get('backend') or backend
            owners.setdefault(info['owner'], []).append(entry.app_id)
    sizes: dict[str, int] = {}
    if flatpaks:
        try:
            done = run(['flatpak', 'list', '--app',
                        '--columns=application,size'], timeout=20)
            by_id = parse_flatpak_sizes(done.stdout)
        except Exception:
            by_id = {}
        for app_id, ref in flatpaks.items():
            sizes[app_id] = by_id.get(ref, 0)
    package_sizes: dict[str, int] = {}
    names = sorted(owners)
    try:
        if names and backend == 'pacman':
            package_sizes = parse_pacman_sizes(
                run(['pacman', '-Qi', *names], timeout=30).stdout)
        elif names and backend == 'apt':
            package_sizes = parse_dpkg_sizes(run(
                ['dpkg-query', '-W', '-f', '${Package}\t${Installed-Size}\n',
                 *names], timeout=30).stdout)
        elif names and backend == 'apk':
            for name in names:
                out = run(['apk', 'info', '-s', name], timeout=15).stdout
                lines = [ln for ln in (out or '').splitlines() if ln.strip()]
                if len(lines) >= 2:
                    package_sizes[name] = parse_size(lines[1])
    except Exception:
        package_sizes = {}
    for name, app_ids in owners.items():
        for app_id in app_ids:
            sizes[app_id] = package_sizes.get(name, 0)
    return sizes


class SizeCache:
    """App sizes for the drawer sort, filled once per app set."""

    def __init__(self, compute: Callable = compute_sizes) -> None:
        self._compute = compute
        self._sizes: dict[str, int] = {}
        self._key: frozenset = frozenset()
        self._busy = False

    def size(self, app_id: str) -> int:
        return self._sizes.get(app_id, 0)

    def ensure(self, entries, on_ready: Callable[[], None]) -> bool:
        """Start a background lookup when the app set changed; True when
        the cached sizes already cover ``entries``."""
        key = frozenset(e.app_id for e in entries)
        if key == self._key:
            return True
        if self._busy:
            return False
        self._busy = True
        entries = list(entries)

        def _work() -> None:
            try:
                sizes = self._compute(entries)
            except Exception:
                sizes = {}
            from gi.repository import GLib

            def _done() -> bool:
                self._sizes = dict(sizes)
                self._key = key
                self._busy = False
                on_ready()
                return False

            GLib.idle_add(_done)

        threading.Thread(target=_work, daemon=True).start()
        return False
