"""In-shell restore flow: no file chooser (a toplevel would open UNDER the
layer-shell TOP surface). "Restore from backup" lists xx-wm-backup-*.json
from ~ and ~/Documents (newest first, date labels, max 10) plus Cancel in
the themed action menu; picking one confirms, then restores from the path.

Headless: window.py is imported against a minimal fake gi stack and driven
on bare ShellWindow instances; backup discovery runs against a tmp HOME."""
import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


@pytest.fixture
def window_module(monkeypatch):
    """Import window.py with just enough gi.repository to satisfy its
    module-level imports (ShellWindow subclasses Adw.ApplicationWindow)."""

    def _require_version(name, _version):
        if name == 'Gtk4LayerShell':
            raise ValueError('not available headlessly')

    repo = types.ModuleType('gi.repository')
    for name in ('Gdk', 'GLib', 'Gtk', 'Pango', 'Gio'):
        setattr(repo, name, types.ModuleType(f'gi.repository.{name}'))
    adw = types.ModuleType('gi.repository.Adw')
    adw.ApplicationWindow = type('ApplicationWindow', (), {})
    repo.Adw = adw

    gi = types.ModuleType('gi')
    gi.require_version = _require_version
    gi.repository = repo

    monkeypatch.setitem(sys.modules, 'gi', gi)
    monkeypatch.setitem(sys.modules, 'gi.repository', repo)
    monkeypatch.delitem(sys.modules, 'window', raising=False)

    import window
    return window


def _write_backup(directory: Path, stamp: str) -> Path:
    payload = {'version': 1, 'home_slots': [], 'theme': 'amoled',
               'font': 'jetbrains-mono-nerd'}
    path = directory / f'xx-wm-backup-{stamp}.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    return path


@pytest.fixture
def home_with_backups(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    docs = home / 'Documents'
    docs.mkdir(parents=True)
    _write_backup(home, '20240102_080000')
    _write_backup(docs, '20240304_120000')
    _write_backup(docs, '20240203_090000')
    (home / 'notes.txt').write_text('not a backup', encoding='utf-8')
    monkeypatch.setenv('HOME', str(home))
    return home


class _Menus:
    def __init__(self):
        self.calls: list[tuple] = []

    def _show_action_menu(self, anchor, title, actions):
        self.calls.append((anchor, title, list(actions)))


def _restore_session(window_module, tmp_path, monkeypatch):
    from config import ShellConfig
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    (tmp_path / 'home').mkdir(parents=True, exist_ok=True)
    menus = _Menus()
    statuses: list[str] = []
    applied: list[str] = []
    shell = types.SimpleNamespace(
        config=ShellConfig(),
        item_actions=menus,
        _restore_btn=object(),
        _show_status=statuses.append,
        _apply_config_change=lambda: applied.append('applied'),
    )
    _write_backup(tmp_path / 'home', '20240102_080000')
    shell._restore_from_path = (
        window_module.ShellWindow._restore_from_path.__get__(shell))
    shell._confirm_restore_backup = (
        window_module.ShellWindow._confirm_restore_backup.__get__(shell))
    window_module.ShellWindow._on_restore_backup(shell, object())
    return menus, statuses, applied


def _by_label(actions):
    return {label: callback for label, callback in actions}


class TestRestoreMenu:
    def test_lists_backups_newest_first_with_date_labels_and_cancel(
            self, window_module, home_with_backups):
        shell = types.SimpleNamespace(
            item_actions=_Menus(), _restore_btn=object(),
            _show_status=lambda message: None,
        )
        window_module.ShellWindow._on_restore_backup(shell, object())
        menus = shell.item_actions
        assert len(menus.calls) == 1
        _anchor, title, actions = menus.calls[0]
        assert title == 'Restore from backup'
        assert [label for label, _cb in actions] == [
            '2024-03-04 12:00', '2024-02-03 09:00', '2024-01-02 08:00',
            'Cancel',
        ]

    def test_empty_shows_status_and_no_menu(
            self, window_module, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))
        (tmp_path / 'home').mkdir(parents=True, exist_ok=True)
        statuses: list[str] = []
        menus = _Menus()
        shell = types.SimpleNamespace(
            item_actions=menus, _restore_btn=object(),
            _show_status=statuses.append,
        )
        window_module.ShellWindow._on_restore_backup(shell, object())
        assert statuses == ['No backups found in ~ or ~/Documents']
        assert menus.calls == []

    def test_no_file_chooser_left(self, window_module):
        import inspect

        import settings_page
        src = inspect.getsource(settings_page.SettingsPage._on_restore_backup)
        assert 'FileChooser' not in src
        assert 'get_filename' not in src
        assert '_show_action_menu' in src


class TestRestoreConfirm:
    def test_pick_confirms_then_restores(
            self, window_module, tmp_path, monkeypatch):
        menus, statuses, applied = _restore_session(
            window_module, tmp_path, monkeypatch)
        _anchor, _title, actions = menus.calls[0]
        _by_label(actions)['2024-01-02 08:00']()
        assert len(menus.calls) == 2
        _anchor, title, confirm_actions = menus.calls[1]
        assert title == ('Restore backup from 2024-01-02 08:00? '
                        'This replaces your home layout and settings.')
        _by_label(confirm_actions)['Restore']()
        assert applied == ['applied']
        assert statuses == ['Backup restored successfully']

    def test_confirm_cancel_restores_nothing(
            self, window_module, tmp_path, monkeypatch):
        menus, statuses, applied = _restore_session(
            window_module, tmp_path, monkeypatch)
        _anchor, _title, actions = menus.calls[0]
        _by_label(actions)['2024-01-02 08:00']()
        _anchor, _title, confirm_actions = menus.calls[1]
        _by_label(confirm_actions)['Cancel']()
        assert applied == []
        assert statuses == []

    def test_list_cancel_restores_nothing(
            self, window_module, tmp_path, monkeypatch):
        menus, statuses, applied = _restore_session(
            window_module, tmp_path, monkeypatch)
        _anchor, _title, actions = menus.calls[0]
        _by_label(actions)['Cancel']()
        assert applied == []
        assert statuses == []
        assert len(menus.calls) == 1

    def test_invalid_payload_reports_without_applying(
            self, window_module, tmp_path, monkeypatch):
        from config import ShellConfig
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))
        home = tmp_path / 'home'
        home.mkdir(parents=True, exist_ok=True)
        bad = home / 'xx-wm-backup-20240102_080000.json'
        bad.write_text('{"version": 99}', encoding='utf-8')
        statuses: list[str] = []
        applied: list[str] = []
        shell = types.SimpleNamespace(
            config=ShellConfig(),
            _show_status=statuses.append,
            _apply_config_change=lambda: applied.append('applied'),
        )
        shell._restore_from_path = (
            window_module.ShellWindow._restore_from_path.__get__(shell))
        shell._restore_from_path(str(bad))
        assert applied == []
        assert len(statuses) == 1
        assert statuses[0].startswith('Invalid backup:')


class TestExportDestination:
    def _export_shell(self, window_module):
        from config import ShellConfig
        statuses: list[str] = []
        shell = types.SimpleNamespace(
            config=ShellConfig(), _show_status=statuses.append)
        window_module.ShellWindow._on_export_backup(shell)
        return statuses

    def test_export_prefers_documents(
            self, window_module, tmp_path, monkeypatch):
        home = tmp_path / 'home'
        (home / 'Documents').mkdir(parents=True)
        monkeypatch.setenv('HOME', str(home))
        statuses = self._export_shell(window_module)
        exported = list((home / 'Documents').glob('xx-wm-backup-*.json'))
        assert len(exported) == 1
        assert list(home.glob('xx-wm-backup-*.json')) == []
        assert statuses == [f'Backup exported to {exported[0]}']

    def test_export_is_owner_only(
            self, window_module, tmp_path, monkeypatch):
        home = tmp_path / 'home'
        home.mkdir(parents=True)
        monkeypatch.setenv('HOME', str(home))
        self._export_shell(window_module)
        exported, = home.glob('xx-wm-backup-*.json')
        assert (exported.stat().st_mode & 0o777) == 0o600

    def test_export_failure_is_reported_not_raised(
            self, window_module, tmp_path, monkeypatch):
        home = tmp_path / 'home'
        home.mkdir(parents=True)
        monkeypatch.setenv('HOME', str(home))
        import backup
        monkeypatch.setattr(backup, 'backup_export_dir',
                            lambda: home / 'gone')
        statuses = self._export_shell(window_module)
        assert len(statuses) == 1
        assert statuses[0].startswith('Could not export backup:')

    def test_export_falls_back_to_home(
            self, window_module, tmp_path, monkeypatch):
        home = tmp_path / 'home'
        home.mkdir(parents=True)
        monkeypatch.setenv('HOME', str(home))
        statuses = self._export_shell(window_module)
        exported = list(home.glob('xx-wm-backup-*.json'))
        assert len(exported) == 1
        assert statuses == [f'Backup exported to {exported[0]}']


class TestRestoreNeedsPin:
    """A backup dropped into ~/Documents (Nearby Share, Syncthing) must not
    restore lock settings without the owner's PIN."""

    def _shell(self, window_module, tmp_path, monkeypatch, pin=None):
        from config import ShellConfig
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))
        monkeypatch.setenv('XX_WM_PIN_HELPER', '0')
        (tmp_path / 'home').mkdir(parents=True, exist_ok=True)
        _write_backup(tmp_path / 'home', '20240102_080000')
        statuses: list[str] = []
        applied: list[str] = []
        shell = types.SimpleNamespace(
            config=ShellConfig(), item_actions=_Menus(),
            _restore_btn=object(), _show_status=statuses.append,
            _apply_config_change=lambda: applied.append('applied'))
        if pin is not None:
            shell.config.set_pin(pin)
        shell._restore_from_path = (
            window_module.ShellWindow._restore_from_path.__get__(shell))
        shell._confirm_restore_backup = (
            window_module.ShellWindow._confirm_restore_backup.__get__(shell))
        window_module.ShellWindow._on_restore_backup(shell, object())
        _by_label(shell.item_actions.calls[0][2])['2024-01-02 08:00']()
        return shell, statuses, applied

    def test_restore_waits_for_the_right_pin(
            self, window_module, tmp_path, monkeypatch):
        import settings_ui
        sheets = []
        monkeypatch.setattr(
            settings_ui, 'form_sheet',
            lambda win, anchor, title, fields, label, on_commit, **_k:
            sheets.append((title, [f[0] for f in fields], on_commit))
            or (object(), {}))
        shell, statuses, applied = self._shell(
            window_module, tmp_path, monkeypatch, pin='246810')
        _by_label(shell.item_actions.calls[1][2])['Restore']()
        assert applied == []
        (title, fields, on_commit), = sheets
        assert (title, fields) == ('Restore backup', ['pin'])
        assert on_commit({'pin': '000000'}) == 'Current PIN is wrong.'
        assert applied == [] and statuses == []
        assert on_commit({'pin': '246810'}) is None
        assert applied == ['applied']
        assert statuses == ['Backup restored successfully']

    def test_no_pin_restores_straight_away(
            self, window_module, tmp_path, monkeypatch):
        import settings_ui
        monkeypatch.setattr(settings_ui, 'form_sheet',
                            lambda *a, **k: pytest.fail('no PIN to ask'))
        shell, _statuses, applied = self._shell(
            window_module, tmp_path, monkeypatch)
        _by_label(shell.item_actions.calls[1][2])['Restore']()
        assert applied == ['applied']

    def test_restored_auto_lock_timeout_is_capped(self, tmp_path,
                                                  monkeypatch):
        from backup import AUTO_LOCK_TIMEOUT_MAX, restore_backup
        from config import ShellConfig
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))
        config = ShellConfig()
        payload = {'version': 1, 'home_slots': [],
                   'auto_lock_timeout': 10 ** 9}
        assert restore_backup(config, payload) is True
        assert config.auto_lock_timeout == AUTO_LOCK_TIMEOUT_MAX == 1800
        payload['auto_lock_timeout'] = 300
        restore_backup(config, payload)
        assert config.auto_lock_timeout == 300


class TestRestoreChargeLimit:
    def _restore(self, window_module, tmp_path, monkeypatch, payload,
                 helper_ok=True):
        from config import ShellConfig
        import helper_client
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))
        (tmp_path / 'home').mkdir(parents=True, exist_ok=True)
        calls = []
        monkeypatch.setattr(
            helper_client, 'set_charge_limit',
            lambda pct, cb: (calls.append(pct),
                             cb(helper_ok, None if helper_ok else 'denied')))
        statuses: list[str] = []
        shell = types.SimpleNamespace(
            config=ShellConfig(), _show_status=statuses.append,
            _apply_config_change=lambda: None)
        shell.config.set_charge_limit(80)
        path = tmp_path / 'home' / 'xx-wm-backup-20240102_080000.json'
        path.write_text(json.dumps(payload), encoding='utf-8')
        window_module.ShellWindow._restore_from_path(shell, str(path))
        return shell, calls, statuses

    def _payload(self, **extra):
        payload = {'version': 1, 'home_slots': [], 'theme': 'amoled',
                   'font': 'jetbrains-mono-nerd'}
        payload.update(extra)
        return payload

    def test_backup_without_limit_keeps_it(
            self, window_module, tmp_path, monkeypatch):
        shell, calls, _ = self._restore(
            window_module, tmp_path, monkeypatch, self._payload())
        assert calls == [] and shell.config.charge_limit == 80

    def test_limit_goes_through_the_helper(
            self, window_module, tmp_path, monkeypatch):
        shell, calls, _ = self._restore(
            window_module, tmp_path, monkeypatch,
            self._payload(charge_limit=None))
        assert calls == [100] and shell.config.charge_limit is None

    def test_helper_refusal_keeps_the_enforced_limit(
            self, window_module, tmp_path, monkeypatch):
        shell, calls, statuses = self._restore(
            window_module, tmp_path, monkeypatch,
            self._payload(charge_limit=60), helper_ok=False)
        assert calls == [60] and shell.config.charge_limit == 80
        assert statuses[-1] == 'Charge limit not restored: denied'
