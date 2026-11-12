"""Storage use and safe cleanup (lane sm_storage, ref clean-system.sh).

Read-only sizing plus two cleanup paths: user-level work that runs on a
worker thread with no privilege (thumbnails, trash, closed-browser
caches, user Flatpak unused runtimes and stopped-app caches), and root
cache work through the helper's fixed-allowlist CleanSystem. Downloads
and user media are sized only and never cleaned.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import settings_page
import settings_ui as ui
from shell_log import log_exc

USER_CLEAN_CATEGORIES = ('thumbnails', 'trash', 'browsers',
                         'flatpak-unused', 'flatpak-caches')

ROOT_CLEAN_CATEGORIES = ('apk-cache', 'paccache-keep', 'paccache-uninstalled',
                         'apt-clean', 'journal-vacuum')

# (row title, what the cleanup keeps or skips) per category.
CATEGORY_LABELS = {
    'thumbnails': ('Thumbnail cache', ''),
    'trash': ('Trash', ''),
    'browsers': ('Browser caches', 'closed browsers only'),
    'flatpak-unused': ('Flatpak runtimes', 'unused ones only'),
    'flatpak-caches': ('Flatpak app caches', 'stopped apps only'),
    'apk-cache': ('Package cache', 'apk'),
    'paccache-keep': ('Package cache', 'keeps 2 versions'),
    'paccache-uninstalled': ('Uninstalled packages', 'cached copies'),
    'apt-clean': ('Package cache', 'apt'),
    'journal-vacuum': ('System journal', 'keeps 2 weeks'),
}

READONLY_LABELS = {
    'downloads': 'Downloads',
    'screenrecordings': 'Screen recordings',
    'screenshots': 'Screenshots',
}

BROWSERS = (
    ('Firefox', 'mozilla/firefox', ('firefox', 'firefox-bin')),
    ('LibreWolf', 'librewolf', ('librewolf',)),
    ('Chromium', 'chromium', ('chromium', 'chromium-browser')),
    ('Chrome', 'google-chrome', ('chrome', 'google-chrome')),
    ('Brave', 'BraveSoftware', ('brave', 'brave-browser')),
    ('Vivaldi', 'vivaldi', ('vivaldi-bin', 'vivaldi')),
)

APK_CACHE_DEFAULT = '/etc/apk/cache'
PACMAN_CACHE_DEFAULT = '/var/cache/pacman/pkg'
SYSTEMD_DIR_DEFAULT = '/run/systemd/system'

UNMERGED_SUFFIXES = ('.apk-new', '.pacnew', '.pacsave', '.dpkg-new',
                     '.dpkg-dist', '.ucf-dist', '.rpmnew', '.rpmsave')


def _run(argv: list[str], timeout: int = 60):
    """Sole subprocess chokepoint; callers run on worker threads only."""
    return subprocess.run(argv, capture_output=True, text=True,
                          timeout=timeout, check=False)


def format_bytes(num) -> str:
    """Largest whole-ish unit, matching the desktop script's IEC style."""
    try:
        value = float(num)
    except (TypeError, ValueError):
        return '0 B'
    if value < 0:
        value = 0
    for unit in ('B', 'kB', 'MB', 'GB', 'TB'):
        if value < 1024 or unit == 'TB':
            if unit == 'B':
                return '%d B' % int(value)
            return '%.1f %s' % (value, unit)
        value /= 1024
    return '%.1f TB' % value


def fs_usage(path: str) -> tuple:
    """(total, free) bytes for `path` via statvfs; (None, None) when lost."""
    try:
        info = os.statvfs(path)
    except OSError:
        return None, None
    return info.f_frsize * info.f_blocks, info.f_frsize * info.f_bavail


def dir_size(path: str) -> int:
    """Bytes under `path`, following the desktop script's du -sb."""
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.lstat(os.path.join(root, name)).st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def home_dir() -> str:
    return os.path.expanduser('~')


def cache_home() -> str:
    return os.environ.get('XDG_CACHE_HOME') or os.path.join(home_dir(),
                                                           '.cache')


def data_home() -> str:
    return os.environ.get('XDG_DATA_HOME') or os.path.join(
        home_dir(), '.local', 'share')


def detect_pkg(which=None):
    """Package family for apk (postmarketOS) and pacman (Arch), apt trivial."""
    lookup = which or shutil.which
    if lookup('apk'):
        return 'apk'
    if lookup('pacman'):
        return 'pacman'
    if lookup('apt-get') or lookup('apt'):
        return 'apt'
    return None


def pkg_cache_dirs(pkg, apk_cache: str = APK_CACHE_DEFAULT,
                   pacman_cache: str = PACMAN_CACHE_DEFAULT) -> list:
    """Cache dirs worth sizing; the apk dir only when it actually exists."""
    if pkg == 'apk':
        return [apk_cache] if os.path.isdir(apk_cache) else []
    if pkg == 'pacman':
        return [pacman_cache]
    if pkg == 'apt':
        return ['/var/cache/apt/archives']
    return []


def root_categories_for(pkg, systemd_dir: str = SYSTEMD_DIR_DEFAULT) -> list:
    """Root cleanup rows that make sense on this device."""
    rows = []
    if pkg == 'apk':
        rows.append('apk-cache')
    elif pkg == 'pacman':
        rows.extend(('paccache-keep', 'paccache-uninstalled'))
    elif pkg == 'apt':
        rows.append('apt-clean')
    if os.path.isdir(systemd_dir):
        rows.append('journal-vacuum')
    return rows


def thumbnail_dirs(cache=None) -> list:
    """Existing thumbnail dirs; emptied, never removed."""
    base = cache if cache is not None else cache_home()
    found = []
    for path in (os.path.join(base, 'thumbnails'),
                 os.path.join(home_dir(), '.thumbnails')):
        if os.path.isdir(path):
            found.append(path)
    return found


def trash_dirs(home=None, uid=None, media_roots=None) -> list:
    """Home trash plus per-volume .Trash-<uid>, like the desktop script."""
    base = home if home is not None else home_dir()
    num = uid if uid is not None else os.getuid()
    found = []
    candidates = [os.path.join(base, '.local', 'share', 'Trash')]
    if media_roots is None:
        media_roots = ('/media', '/run/media', '/mnt')
    for root in media_roots:
        candidates.append(os.path.join(root, '.Trash-%d' % num))
        try:
            vols = sorted(os.listdir(root))
        except OSError:
            continue
        for vol in vols:
            candidates.append(os.path.join(root, vol, '.Trash-%d' % num))
            candidates.append(os.path.join(root, vol, vol,
                                            '.Trash-%d' % num))
    for path in candidates:
        if os.path.isdir(path) and path not in found:
            found.append(path)
    return found


def browser_cache_entries(cache=None, run=_run) -> list:
    """(name, dir) of installed browsers that are NOT currently running."""
    base = cache if cache is not None else cache_home()
    found = []
    for name, rel, procs in BROWSERS:
        path = os.path.join(base, rel)
        if not os.path.isdir(path):
            continue
        running = False
        for proc in procs:
            try:
                result = run(['pgrep', '-x', proc])
            except Exception:
                continue
            if getattr(result, 'returncode', 1) == 0:
                running = True
                break
        if not running:
            found.append((name, path))
    return found


def flatpak_cache_dirs(home=None) -> list:
    """~/.var/app/<id>/cache dirs that exist."""
    base = os.path.join(home if home is not None else home_dir(),
                        '.var', 'app')
    try:
        apps = sorted(os.listdir(base))
    except OSError:
        return []
    return [os.path.join(base, app, 'cache') for app in apps
            if os.path.isdir(os.path.join(base, app, 'cache'))]


def parse_flatpak_ps(output: str) -> list:
    """Application ids from `flatpak ps` (first line is a header)."""
    running = []
    for line in (output or '').splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 2:
            running.append(fields[1])
    return running


def flatpak_running(run=_run) -> list:
    try:
        result = run(['flatpak', 'ps', '--columns=application'])
    except Exception:
        return []
    if getattr(result, 'returncode', 1) != 0:
        return []
    return parse_flatpak_ps(getattr(result, 'stdout', '') or '')


def parse_journal_size(output: str):
    """'480.0M' from `journalctl --disk-usage`, None when unparseable."""
    for line in (output or '').splitlines():
        if 'take up' in line:
            middle = line.split('take up', 1)[1].strip().split()
            if middle:
                return middle[0]
    return None


def journal_size(run=_run):
    if not os.path.isdir(SYSTEMD_DIR_DEFAULT):
        return None
    try:
        result = run(['journalctl', '--disk-usage'])
    except Exception:
        return None
    if getattr(result, 'returncode', 1) != 0:
        return None
    return parse_journal_size(getattr(result, 'stdout', '') or '')


def find_unmerged_configs(etc_root: str = '/etc') -> list:
    """Unmerged .apk-new/.pacnew-style files, relative to `etc_root`."""
    found = []
    for root, _dirs, files in os.walk(etc_root):
        for name in files:
            if name.endswith(UNMERGED_SUFFIXES):
                found.append(os.path.relpath(os.path.join(root, name),
                                             etc_root))
    return sorted(found)


def parse_failed_units(output: str) -> list:
    """Unit names from `systemctl --failed` (header and footer skipped)."""
    units = []
    for line in (output or '').splitlines():
        stripped = line.strip()
        if not stripped or 'listed.' in stripped:
            continue
        if stripped.startswith('UNIT'):
            continue
        if stripped.startswith(('●', '*')):
            fields = stripped[1:].strip().split()
            if fields:
                units.append(fields[0])
            continue
        unit = stripped.split()[0]
        if '.' in unit:
            units.append(unit)
    return units


def failed_units(run=_run) -> list:
    try:
        result = run(['systemctl', '--failed', '--no-legend', '--plain'],
                     timeout=30)
    except Exception:
        return []
    if getattr(result, 'returncode', 1) != 0:
        return []
    return parse_failed_units(getattr(result, 'stdout', '') or '')


def _remove_tree(path: str) -> None:
    if os.path.islink(path) or not os.path.isdir(path):
        try:
            os.remove(path)
        except OSError:
            pass
        return
    shutil.rmtree(path, ignore_errors=True)


def _empty_dir_contents(path: str, remove=_remove_tree) -> None:
    try:
        entries = sorted(os.listdir(path))
    except OSError:
        return
    for entry in entries:
        try:
            remove(os.path.join(path, entry))
        except OSError:
            pass


def clean_user_categories(selected, home=None, uid=None, media_roots=None,
                          run=_run, remove=_remove_tree) -> list:
    """Empty user-level caches; returns the categories actually cleaned.

    Never touches Downloads or user media: only thumbnail dirs, trash
    dirs, closed-browser caches, `flatpak uninstall --unused --user`
    and stopped-app Flatpak caches.
    """
    base = home if home is not None else home_dir()
    num = uid if uid is not None else os.getuid()
    if home is None:
        cache = cache_home()
    else:
        cache = os.environ.get('XDG_CACHE_HOME') or os.path.join(
            base, '.cache')
    cleaned = []
    if 'thumbnails' in selected:
        for path in thumbnail_dirs(cache=cache):
            _empty_dir_contents(path, remove)
        cleaned.append('thumbnails')
    if 'trash' in selected:
        for path in trash_dirs(home=base, uid=num, media_roots=media_roots):
            for sub in ('files', 'info', 'expunged'):
                full = os.path.join(path, sub)
                if os.path.isdir(full) and full != path:
                    _empty_dir_contents(full, remove)
        cleaned.append('trash')
    if 'browsers' in selected:
        for _name, path in browser_cache_entries(cache=cache, run=run):
            _empty_dir_contents(path, remove)
        cleaned.append('browsers')
    if 'flatpak-unused' in selected:
        try:
            result = run(['flatpak', 'uninstall', '--unused', '--user',
                          '-y'])
        except Exception:
            result = None
        if result is not None and getattr(result, 'returncode', 1) == 0:
            cleaned.append('flatpak-unused')
    if 'flatpak-caches' in selected:
        running = set(flatpak_running(run=run))
        for path in flatpak_cache_dirs(home=base):
            app = os.path.basename(os.path.dirname(path))
            if app in running:
                continue
            _empty_dir_contents(path, remove)
        cleaned.append('flatpak-caches')
    return cleaned


def category_size(category: str, pkg=None) -> int | None:
    """Reclaimable bytes estimate, or None when only known at clean time."""
    base = home_dir()
    if category == 'thumbnails':
        return sum(dir_size(p) for p in thumbnail_dirs())
    if category == 'trash':
        return sum(dir_size(p) for p in trash_dirs(home=base))
    if category == 'browsers':
        return sum(dir_size(p) for _n, p in browser_cache_entries())
    if category == 'flatpak-caches':
        return sum(dir_size(p) for p in flatpak_cache_dirs(home=base))
    if category == 'flatpak-unused':
        return None
    if category in ('apk-cache', 'paccache-keep', 'paccache-uninstalled',
                    'apt-clean'):
        return sum(dir_size(p) for p in pkg_cache_dirs(pkg))
    if category == 'journal-vacuum':
        return None
    return None


def readonly_sizes() -> dict:
    """Sizes only, never cleaned: Downloads and user media folders."""
    base = home_dir()
    return {
        'downloads': dir_size(os.path.join(base, 'Downloads')),
        'screenrecordings': dir_size(os.path.join(
            base, 'Videos', 'Screenrecordings')),
        'screenshots': dir_size(os.path.join(
            base, 'Pictures', 'Screenshots')),
    }


def collect_storage() -> dict:
    """Worker payload: usage, per-category sizes and read-only reports."""
    pkg = detect_pkg()
    payload: dict = {
        'root': fs_usage('/'),
        'home': fs_usage(home_dir()),
        'pkg': pkg,
        'sizes': {},
        'journal': journal_size(),
        'readonly': readonly_sizes(),
        'unmerged': find_unmerged_configs(),
        'failed': failed_units(),
    }
    for category in (USER_CLEAN_CATEGORIES
                     + tuple(root_categories_for(pkg))):
        try:
            payload['sizes'][category] = category_size(category, pkg)
        except Exception:
            payload['sizes'][category] = None
    return payload


def usage_line(total, free) -> str:
    """'3.1 GB free of 12.0 GB'; 'Unknown' when statvfs failed."""
    if total is None or free is None:
        return 'Unknown'
    return '%s free of %s' % (format_bytes(free), format_bytes(total))


class StorageSection(settings_page._Section):
    """Settings section: disk use, safe cleanup, read-only reports."""

    key = 'storage'
    title = 'Storage'

    def __init__(self) -> None:
        self._usage = None
        self._system_row = None
        self._home_row = None
        self._rows_box = None
        self._other_box = None
        self._reports = None
        self._status = None
        self._clean_btn = None
        self._switches: dict = {}
        self._sizes: dict = {}
        self._pkg = None

    def build(self, win) -> list:
        self._system_row = ui.row('System', 'Measuring…')
        self._home_row = ui.row('Home', 'Measuring…')
        self._reports = ui.nav_row('Warnings', 'Checking…',
                                   lambda r: self._show_reports(win, r))
        self._report_text = ''
        self._usage = self._system_row.subtitle_label
        usage = ui.group(self.title, [self._system_row, self._home_row,
                                      self._reports])
        self._clean_btn = ui.nav_row('Clean selected', '',
                                     lambda _r: self._on_clean_pressed(win))
        clean = ui.group('Free up space', note='')
        self._rows_box = clean[1]
        self._status = clean[2]
        other = ui.group('Other space (read only)')
        self._other_box = other[1]
        self._render_rows(win)
        return usage + clean + other

    def refresh(self, win) -> None:
        from settings_page import _run_bg
        _run_bg(collect_storage, lambda res: self._on_state(win, res))

    # ------------------------------------------------------------ behavior

    def _categories(self) -> list:
        rows = list(USER_CLEAN_CATEGORIES)
        rows.extend(root_categories_for(self._pkg or detect_pkg()))
        return rows

    def _render_rows(self, win) -> None:
        if self._rows_box is None:
            return
        rows = []
        for category in self._categories():
            size = self._sizes.get(category)
            self._switches.setdefault(category, True)
            title, note = CATEGORY_LABELS.get(category, (category, ''))
            parts = [format_bytes(size)] if size is not None else []
            parts += [note] if note else []
            rows.append(ui.switch_row(
                title, ' · '.join(parts) or 'Size unknown',
                self._switches[category],
                lambda active, c=category: self._on_pick(c, active)))
        reclaim = sum((self._sizes.get(c) or 0) for c in self._selected()
                      if isinstance(self._sizes.get(c), int))
        ui.set_subtitle(self._clean_btn, 'Frees about %s' % format_bytes(
            reclaim))
        ui.fill(self._rows_box, rows + [self._clean_btn])
        readonly = self._sizes.get('readonly', {}) or {}
        ui.fill(self._other_box, [
            ui.row(label, format_bytes(readonly[key])
                   if readonly.get(key) is not None else '—')
            for key, label in READONLY_LABELS.items()])

    def _on_pick(self, category: str, active: bool) -> None:
        self._switches[category] = bool(active)
        reclaim = sum((self._sizes.get(c) or 0) for c in self._selected()
                      if isinstance(self._sizes.get(c), int))
        ui.set_subtitle(self._clean_btn, 'Frees about %s' % format_bytes(
            reclaim))

    def _selected(self) -> list:
        return [c for c in self._categories()
                if self._switches.get(c, True)]

    def _on_state(self, win, res) -> None:
        try:
            root, home = res['root'], res['home']
        except (TypeError, KeyError):
            return
        self._pkg = res.get('pkg')
        self._sizes = dict(res.get('sizes') or {})
        self._sizes['readonly'] = res.get('readonly') or {}
        ui.set_subtitle(self._system_row, usage_line(*root))
        ui.set_subtitle(self._home_row, usage_line(*home))
        self._render_rows(win)
        self._render_reports(res)

    def _render_reports(self, res) -> None:
        if self._reports is None:
            return
        lines, counts = [], []
        unmerged = res.get('unmerged') or []
        if unmerged:
            counts.append('%d unmerged config%s' % (
                len(unmerged), '' if len(unmerged) == 1 else 's'))
            lines.append('Unmerged configs: %s' % ', '.join(unmerged[:5])
                         + ('…' if len(unmerged) > 5 else ''))
        failed = res.get('failed') or []
        if failed:
            counts.append('%d failed unit%s' % (
                len(failed), '' if len(failed) == 1 else 's'))
            lines.append('Failed units: %s' % ', '.join(failed[:5])
                         + ('…' if len(failed) > 5 else ''))
        journal = res.get('journal')
        if journal:
            counts.append('journal %s' % journal)
            lines.append('Journal: %s' % journal)
        self._report_text = '\n'.join(lines)
        ui.set_subtitle(self._reports,
                        ' · '.join(counts) if counts else 'None')
        # Nothing to report: no row (a "None" that opens an empty sheet
        # carries nothing).
        try:
            self._reports.set_visible(bool(counts))
        except Exception:
            log_exc('settings_storage._render_reports: _reports.set_visible')

    def _show_reports(self, win, anchor) -> None:
        win.item_actions._show_action_menu(
            anchor, 'Warnings', [('Close', lambda: None)],
            subtitle=self._report_text or None)

    def _on_clean_pressed(self, win) -> None:
        selected = self._selected()
        if not selected:
            self._say('Nothing selected.')
            return
        reclaim = sum((self._sizes.get(c) or 0) for c in selected
                      if isinstance(self._sizes.get(c), int))
        text = 'Clean selected caches? Frees about %s.' % format_bytes(
            reclaim)
        win.item_actions._show_action_menu(
            self._clean_btn, text,
            [('Clean', lambda: self._run_clean(win, selected)),
             ('Cancel', lambda: None)])

    def _run_clean(self, win, selected: list) -> None:
        from settings_page import _run_bg
        self._say('Cleaning…')
        user = [c for c in selected if c in USER_CLEAN_CATEGORIES]
        root = [c for c in selected if c in ROOT_CLEAN_CATEGORIES]

        def _work():
            freed = 0
            for category in user:
                try:
                    before = category_size(category, self._pkg)
                except Exception:
                    before = None
                try:
                    done = clean_user_categories([category])
                except Exception:
                    done = []
                if category in done and isinstance(before, int):
                    freed += before
            return freed

        def _on_user_done(freed) -> None:
            # Main loop again, so the async helper callback can fire.
            if not root:
                self._finish_clean(win, freed, [], None)
                return
            try:
                import helper_client
            except ImportError as err:
                self._finish_clean(win, freed, [], str(err))
                return
            try:
                helper_client.clean_system(
                    root,
                    lambda ok, result: self._on_root_done(win, freed, ok,
                                                         result))
            except Exception as err:
                self._finish_clean(win, freed, [], str(err))

        _run_bg(_work, _on_user_done)

    def _on_root_done(self, win, freed: int, ok: bool, result) -> None:
        if not ok:
            self._finish_clean(win, freed, [], str(result))
            return
        self._finish_clean(win, freed, list(result or []), None)

    def _finish_clean(self, win, freed: int, root_done: list,
                      root_error) -> None:
        total = freed
        for category in root_done:
            size = self._sizes.get(category)
            if isinstance(size, int):
                total += size
        if root_error:
            self._say('Cleanup partly failed: %s' % root_error)
        else:
            self._say('Freed %s.' % format_bytes(total))
        self.refresh(win)

    def _say(self, text: str) -> None:
        ui.say(self._status, text)

