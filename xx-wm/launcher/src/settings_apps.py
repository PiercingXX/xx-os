"""Settings Apps section and the app-uninstall flow.

Entry points (one implementation, the helper path): a drawer
long-press "Uninstall" row and the taps in this section's installed
list. Owner lookup is apk (`apk info --who-owns`), pacman
(`pacman -Qqo`), dpkg (`dpkg -S`, lookup only) or the `X-Flatpak`
desktop key. The confirm dialog shows the `apk del -s` /
`pacman -Rns --print` preview; removal of distro packages goes
through the root helper's RemovePackage (auth_admin_keep, protected
list enforced server-side), Flatpak removals through `flatpak
uninstall` (user installs need no root, system ones auth through
Flatpak's own polkit prompt).

Everything blocking runs on settings-page worker threads; this module
itself never touches subprocess or D-Bus directly.
"""
from __future__ import annotations

import settings_page
import settings_ui as ui

# Mirror of the helper's protected list for UX pre-checks (the helper
# enforces it server-side; a test pins both to the same verdicts).
_PROTECTED_EXACT = frozenset({
    'base', 'alpine-base', 'musl', 'openrc', 'systemd', 'apk-tools',
    'pacman', 'doas', 'sudo',
    'xx-wm', 'gtk4', 'python3', 'phoc', 'squeekboard', 'networkmanager',
    'modemmanager',
})
_PROTECTED_PREFIXES = ('linux', 'postmarketos-base')


def is_protected(name: str) -> bool:
    """True when ``name`` must never be removed (mirrors the helper)."""
    lowered = str(name or '').lower()
    if lowered in _PROTECTED_EXACT:
        return True
    return any(lowered.startswith(prefix)
               for prefix in _PROTECTED_PREFIXES)


def _status(win, message: str) -> None:
    try:
        win._show_status(message)
    except Exception:
        pass


def _display_name(win, entry) -> str:
    try:
        return win.config.label_for(entry.app_id, entry.name)
    except Exception:
        return getattr(entry, 'name', None) or getattr(
            entry, 'app_id', 'app')


def _run_bg(work, on_done) -> None:
    return settings_page._run_bg(work, on_done)


# ------------------------------------------------------------ owner lookup

def resolve_owner_for_entry(entry) -> dict:
    """(Blocking: call on a worker.) Who provides ``entry``'s desktop
    file: ``{'flatpak': app_id}``, ``{'owner': pkg, 'backend': backend}``
    or ``{'owner': None, ...}`` with the reason in ``note``."""
    import system_settings

    filename = None
    try:
        filename = entry.desktop_app.get_filename()
    except Exception:
        filename = None
    text = None
    if filename:
        try:
            with open(filename, encoding='utf-8', errors='replace') as handle:
                text = handle.read(65536)
        except OSError:
            text = None
    if text:
        app_id = system_settings.parse_flatpak_app_id(text)
        if app_id:
            return {'flatpak': app_id, 'owner': None, 'backend': 'flatpak',
                    'path': filename}
    backend = system_settings.detect_backend()
    if backend == 'apk' and filename:
        owner, _ = system_settings.resolve_apk_owner(filename)
        return {'flatpak': None, 'owner': owner, 'backend': 'apk',
                'path': filename}
    if backend == 'pacman' and filename:
        owner, _ = system_settings.resolve_pacman_owner(filename)
        return {'flatpak': None, 'owner': owner, 'backend': 'pacman',
                'path': filename}
    if backend == 'apt' and filename:
        owner, _ = system_settings.resolve_dpkg_owner(filename)
        return {'flatpak': None, 'owner': owner, 'backend': 'apt',
                'path': filename}
    return {'flatpak': None, 'owner': None, 'backend': backend,
            'path': filename,
            'note': 'No package manager found.' if backend is None
                    else 'No owning package found.'}


# ------------------------------------------------------------ uninstall flow

def begin_uninstall(win, anchor, entry) -> None:
    """Start the uninstall flow for one drawer/Apps-section app."""
    display = _display_name(win, entry)
    _status(win, 'Resolving package for %s…' % display)
    _run_bg(lambda: resolve_owner_for_entry(entry),
            lambda info: _on_owner_resolved(win, anchor, entry, display,
                                            info))


def _on_owner_resolved(win, anchor, entry, display: str, info) -> None:
    if not isinstance(info, dict):
        _status(win, 'Could not determine the package for %s.' % display)
        return
    if info.get('flatpak'):
        _uninstall_flatpak_start(win, anchor, display, info['flatpak'])
        return
    owner = info.get('owner')
    backend = info.get('backend')
    if not owner:
        if backend == 'apt':
            _status(win, 'Uninstall for apt packages is not supported here.')
        else:
            _status(win, 'Could not determine the package for %s.' % display)
        return
    if is_protected(owner):
        _status(win, '%s is a protected system package.' % owner)
        return
    if backend == 'apt':
        _status(win, 'Uninstall for apt packages is not supported here.')
        return
    _status(win, 'Previewing removal of %s…' % owner)
    _run_bg(lambda: _preview(owner, backend),
            lambda preview: _confirm_repo_removal(win, anchor, display,
                                                  owner, preview))


def _preview(owner: str, backend: str) -> str:
    import system_settings

    return system_settings.removal_preview(owner, backend)


def _confirm_repo_removal(win, anchor, display: str, owner: str,
                          preview: str) -> None:
    if anchor is None:
        _status(win, 'Cannot confirm here.')
        return
    lines = (preview or '').splitlines()[:12]
    text = '\n'.join(lines) if lines else 'No preview output.'
    title = 'Remove %s (%s)?\n%s' % (display, owner, text)
    try:
        actions = win.item_actions
    except Exception:
        _status(win, 'Cannot confirm here.')
        return
    actions._show_action_menu(
        anchor, title,
        [('Uninstall', lambda: _execute_repo_removal(win, owner)),
         ('Cancel', lambda: None)])


def _execute_repo_removal(win, owner: str) -> None:
    import helper_client

    _status(win, 'Uninstalling %s… (authenticate if asked)' % owner)
    helper_client.remove_package(
        owner, lambda ok, result: _on_removed(win, ok, result, owner))


def _on_removed(win, ok: bool, result, owner: str) -> None:
    if ok:
        _status(win, 'Uninstalled %s.' % owner)
        _refresh_apps(win)
    else:
        _status(win, 'Uninstall failed: %s' % (result or 'unknown error'))


def _uninstall_flatpak_start(win, anchor, display: str, app_id: str) -> None:
    _run_bg(lambda: _installation(app_id),
            lambda installation: _on_flatpak_scope(
                win, anchor, display, app_id, installation))


def _installation(app_id: str):
    import system_settings

    return system_settings.flatpak_installation(app_id)


def _on_flatpak_scope(win, anchor, display: str, app_id: str,
                      installation) -> None:
    if installation not in ('user', 'system'):
        _status(win, 'Could not find %s in Flatpak.' % display)
        return
    if anchor is None:
        _status(win, 'Cannot confirm here.')
        return
    try:
        actions = win.item_actions
    except Exception:
        _status(win, 'Cannot confirm here.')
        return
    scope = 'user install (no password needed)' if installation == 'user' \
        else 'system install (authenticate if asked)'
    actions._show_action_menu(
        anchor, 'Remove %s (%s, %s)?' % (display, app_id, scope),
        [('Uninstall',
          lambda: _execute_flatpak_removal(win, app_id, installation)),
         ('Cancel', lambda: None)])


def _execute_flatpak_removal(win, app_id: str, installation: str) -> None:
    import system_settings

    _status(win, 'Uninstalling %s…' % app_id)
    _run_bg(lambda: system_settings.flatpak_uninstall(app_id, installation),
            lambda result: _on_flatpak_removed(win, result, app_id))


def _on_flatpak_removed(win, result, app_id: str) -> None:
    ok, message = result if result else (False, 'Uninstall failed.')
    _status(win, message if ok else 'Uninstall failed: %s' % message)
    if ok:
        _refresh_apps(win)


def _refresh_apps(win) -> None:
    try:
        win.app_index.refresh()
    except Exception:
        pass
    try:
        win._populate_apps('')
    except Exception:
        pass
    try:
        win._refresh_apps_section()
    except Exception:
        pass


# ------------------------------------------------------------------ section

# app_id -> package subtitle ('' when nothing owns the desktop file),
# filled once per app by a settings worker: owner lookups shell out.
_PACKAGES: dict = {}


def package_subtitle(info) -> str:
    """Apps-row subtitle from resolve_owner_for_entry: the package name,
    'Flatpak · <id>', or '' (never the desktop id)."""
    if not isinstance(info, dict):
        return ''
    if info.get('flatpak'):
        return 'Flatpak · %s' % info['flatpak']
    return str(info.get('owner') or '')


def lookup_packages(entries) -> dict:
    """(Blocking: worker only.) Package subtitles for apps not cached."""
    found = {}
    for entry in entries:
        app_id = getattr(entry, 'app_id', None)
        if not app_id or app_id in _PACKAGES:
            continue
        try:
            found[app_id] = package_subtitle(resolve_owner_for_entry(entry))
        except Exception:
            found[app_id] = ''
    return found


class AppsSection(settings_page._Section):
    """Settings section: the installed-apps list; a tap starts the
    uninstall flow for that app."""

    key = 'apps'
    title = 'Installed apps'

    def __init__(self) -> None:
        self._list_box = None
        self._looking = False

    def build(self, win) -> list:
        built = ui.group(self.title, note='Tap an app to review and '
                         'uninstall it. System packages are protected.')
        self._list_box = built[1]
        self._render(win)
        return built

    def refresh(self, win) -> None:
        self._render(win)
        try:
            entries = list(win.app_index.entries)
        except Exception:
            return
        if self._looking or all(getattr(e, 'app_id', None) in _PACKAGES
                                for e in entries):
            return
        self._looking = True
        _run_bg(lambda: lookup_packages(entries),
                lambda found: self._on_packages(win, found))

    def _on_packages(self, win, found) -> None:
        self._looking = False
        if isinstance(found, dict):
            _PACKAGES.update(found)
        self._render(win)

    def _render(self, win) -> None:
        if self._list_box is None:
            return
        try:
            entries = list(win.app_index.entries)
        except Exception:
            entries = []
        rows = [ui.row(_display_name(win, entry),
                       _PACKAGES.get(getattr(entry, 'app_id', ''), ''),
                       on_activate=lambda r, e=entry: begin_uninstall(
                           win, r, e))
                for entry in entries]
        # Nothing indexed yet: the card hides instead of saying so.
        ui.fill(self._list_box, rows, hide_empty=True)
