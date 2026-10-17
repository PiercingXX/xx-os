"""Owner update-system.sh as THE Settings update path.

Covers install-path selection, the terminal/headless runner choice, the
log-summary parse, the fallback to the built-in flow, and the installer
no longer warning about a phone profile. The real owner script and any
package manager are never executed: shell fixtures stand in for them.
"""
import os
import types

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOTSTRAP = os.path.join(REPO, 'scripts', 'bootstrap-dots.sh')
INSTALL_SH = os.path.join(REPO, 'scripts', 'install.sh')

OWNER_REL = os.path.join(
    '.scripts', 'PiercingXX-Settings-Menu', 'update-system.sh')


def _which(present):
    def probe(binary):
        return '/usr/bin/' + binary if binary in present else None
    return probe


# --- install-path selection -------------------------------------------

class TestInstallPath:
    def test_config_default_is_the_dots_layout(self):
        import config
        assert (config.DEFAULT_CONFIG['update_script'] ==
                '~/.scripts/PiercingXX-Settings-Menu/update-system.sh')

    def test_helper_default_matches_config(self):
        import config
        import settings_updates
        assert (os.path.join('~', settings_updates.OWNER_SCRIPT_REL) ==
                config.DEFAULT_CONFIG['update_script'])

    def test_bootstrap_installs_that_path(self):
        with open(BOOTSTRAP, encoding='utf-8') as handle:
            text = handle.read()
        assert '.scripts/PiercingXX-Settings-Menu/update-system.sh' in text
        assert '.scripts/lib/common.sh' in text

    def test_bootstrap_does_not_vendor_the_script(self):
        with open(BOOTSTRAP, encoding='utf-8') as handle:
            lines = handle.read().splitlines()
        assert len(lines) < 120

    def test_installer_runs_bootstrap_without_profile_warning(self):
        with open(INSTALL_SH, encoding='utf-8') as handle:
            text = handle.read()
        assert 'bootstrap-dots.sh' in text
        assert 'phone profile not applied yet' not in text
        assert 'phone profile' not in text

    def test_bootstrap_claims_no_phone_profile(self):
        with open(BOOTSTRAP, encoding='utf-8') as handle:
            text = handle.read()
        assert '--profile phone' not in text
        assert 'phone profile' not in text


# --- runner choice -----------------------------------------------------

SAMPLE_SUMMARY = (
    '\nUpdate summary\n'
    '  System packages (apk)       ok\n'
    '  Firmware (fwupd)            skipped\n'
    '  Flatpak                     FAILED\n'
    '\nReboot recommended:\n'
    '  - running kernel 6.6.7 was replaced\n'
    '\nFull log: /home/u/.local/state/piercingxx/logs/update-20260101.log\n'
    'System update finished.\n'
)


class TestRunnerChoice:
    def test_update_terminal_order(self):
        import update_checker
        names = [name for name, _build in update_checker._UPDATE_TERMINALS]
        assert names == ['kgx', 'foot', 'gnome-terminal', 'xterm']

    def test_kgx_preferred(self, tmp_path):
        import update_checker
        script = tmp_path / 'update-system.sh'
        script.write_text('#!/bin/sh\n')
        kind, detail, argv = update_checker.plan_update(
            str(script), which=_which({'kgx', 'foot', 'bash'}))
        assert kind == 'terminal'
        assert detail == 'kgx'
        assert argv[0] == 'kgx'

    def test_falls_through_in_order(self, tmp_path):
        import update_checker
        script = tmp_path / 'update-system.sh'
        script.write_text('#!/bin/sh\n')
        kind, _d, argv = update_checker.plan_update(
            str(script), which=_which({'xterm', 'bash'}))
        assert kind == 'terminal'
        assert argv[0] == 'xterm'
        kind, _d, argv = update_checker.plan_update(
            str(script), which=_which({'gnome-terminal', 'xterm', 'bash'}))
        assert argv[0] == 'gnome-terminal'
        kind, _d, argv = update_checker.plan_update(
            str(script), which=_which({'foot', 'xterm', 'bash'}))
        assert argv[0] == 'foot'

    def test_no_terminal_means_headless(self, tmp_path):
        import update_checker
        script = tmp_path / 'update-system.sh'
        script.write_text('#!/bin/sh\n')
        kind, _detail, argv = update_checker.plan_update(
            str(script), which=_which({'bash'}))
        assert kind == 'headless'
        assert argv == [str(script)]

    def test_kitty_is_not_an_update_terminal(self, tmp_path):
        import update_checker
        script = tmp_path / 'update-system.sh'
        script.write_text('#!/bin/sh\n')
        kind, _detail, _argv = update_checker.plan_update(
            str(script), which=_which({'kitty', 'bash'}))
        assert kind == 'headless'

    def test_missing_script_stays_missing(self, tmp_path):
        import update_checker
        kind, _detail, argv = update_checker.plan_update(
            str(tmp_path / 'update-system.sh'), which=_which({'kgx'}))
        assert kind == 'missing'
        assert argv is None


class TestHeadlessRun:
    def test_lines_stream_and_rc_returns(self, tmp_path, monkeypatch):
        import update_checker

        class _Immediate:
            @staticmethod
            def idle_add(func, *args):
                func(*args)
                return 0

        monkeypatch.setattr(update_checker, 'GLib', _Immediate)
        script = tmp_path / 'update-system.sh'
        script.write_text('#!/bin/sh\n'
                          'echo first\necho second\nexit 3\n')
        script.chmod(0o755)
        seen: list = []
        done: list = []
        thread = update_checker.run_owner_headless(
            str(script), seen.append, done.append)
        thread.join(timeout=30)
        assert seen == ['first', 'second']
        assert done == [3]

    def test_unstartable_script_reports_minus_one(self, tmp_path,
                                                  monkeypatch):
        import update_checker

        class _Immediate:
            @staticmethod
            def idle_add(func, *args):
                func(*args)
                return 0

        monkeypatch.setattr(update_checker, 'GLib', _Immediate)
        missing = tmp_path / 'no-such-dir' / 'update-system.sh'
        done: list = []
        thread = update_checker.run_owner_headless(
            str(missing), lambda _line: None, done.append)
        thread.join(timeout=30)
        assert done == [-1]


# --- log streaming parse -----------------------------------------------

class TestParseOwnerSummary:
    def test_steps_failures_reboot_and_log(self):
        import settings_updates
        result = settings_updates.parse_owner_summary(SAMPLE_SUMMARY)
        assert ('System packages (apk)', 'ok') in result['steps']
        assert ('Firmware (fwupd)', 'skipped') in result['steps']
        assert ('Flatpak', 'FAILED') in result['steps']
        assert result['failures'] == 1
        assert result['reboot_reasons'] == [
            'running kernel 6.6.7 was replaced']
        assert result['log_path'] == (
            '/home/u/.local/state/piercingxx/logs/update-20260101.log')
        assert result['finished'] is True
        assert result['core_failed'] is False

    def test_ansi_colors_are_stripped(self):
        import settings_updates
        text = ('\x1b[0;34mUpdate summary\x1b[0m\n'
                '  \x1b[0;32mFlatpak\x1b[0m'
                '                   \x1b[0;32mok\x1b[0m\n'
                '\x1b[0;32mSystem update finished.\x1b[0m\n')
        result = settings_updates.parse_owner_summary(text)
        assert ('Flatpak', 'ok') in result['steps']
        assert result['finished'] is True

    def test_core_failure_is_not_finished(self):
        import settings_updates
        text = ('Update summary\n'
                '  System packages (apk)       FAILED\n'
                'Full log: /tmp/u.log\n'
                'The package upgrade did not complete. Log: /tmp/u.log\n')
        result = settings_updates.parse_owner_summary(text)
        assert result['failures'] == 1
        assert result['finished'] is False
        assert result['core_failed'] is True

    def test_empty_output_is_unknown(self):
        import settings_updates
        result = settings_updates.parse_owner_summary('')
        assert result['steps'] == []
        assert result['failures'] == 0
        assert result['reboot_reasons'] == []
        assert result['log_path'] is None
        assert result['finished'] is False
        assert result['core_failed'] is False


# --- Settings wiring: owner path vs built-in fallback -------------------

def _page_self(monkeypatch, present, backend='apk'):
    import settings_page
    import settings_updates
    import system_settings
    calls: dict = {}
    monkeypatch.setattr(settings_updates, 'owner_script_path',
                        lambda script: present)
    monkeypatch.setattr(system_settings, 'detect_backend',
                        lambda: backend)
    self = types.SimpleNamespace(
        _win=types.SimpleNamespace(
            config=types.SimpleNamespace(update_script='~/u.sh')),
        _confirm_owner_update=lambda _a, s: calls.setdefault('owner', s),
        _confirm_native_update=lambda _a: calls.setdefault('native', True),
        _confirm_terminal_update=lambda _a: calls.setdefault(
            'terminal', True),
    )
    win = self._win
    win._on_update_clicked = lambda _a: calls.setdefault('clicked', True)
    return settings_page, self, calls


class TestUpdateNowRouting:
    def test_owner_script_is_the_update_path(self, monkeypatch):
        settings_page, self, calls = _page_self(
            monkeypatch, '/home/u/' + OWNER_REL)
        settings_page.SettingsPage._on_update_now(self, object())
        assert calls == {'owner': '/home/u/' + OWNER_REL}

    def test_missing_script_falls_back_to_native(self, monkeypatch):
        settings_page, self, calls = _page_self(
            monkeypatch, None, backend='apk')
        settings_page.SettingsPage._on_update_now(self, object())
        assert calls == {'native': True}

    def test_missing_script_falls_back_to_terminal(self, monkeypatch):
        settings_page, self, calls = _page_self(
            monkeypatch, None, backend='pacman')
        settings_page.SettingsPage._on_update_now(self, object())
        assert calls == {'terminal': True}

    def test_missing_script_unknown_backend_clicks_through(self, monkeypatch):
        settings_page, self, calls = _page_self(
            monkeypatch, None, backend=None)
        settings_page.SettingsPage._on_update_now(self, object())
        assert calls == {'clicked': True}


class TestExecuteOwnerUpdate:
    def _self(self, tmp_path):
        statuses: list = []
        self = types.SimpleNamespace(
            _win=types.SimpleNamespace(),
            _update_status_text=statuses.append,
            _owner_log_path='',
            _on_owner_line=lambda _line: statuses.append('line'),
            _on_owner_done=lambda _rc, _p: statuses.append('done'),
        )
        return self, statuses

    def test_terminal_plan_opens_terminal(self, tmp_path, monkeypatch):
        import settings_page
        import update_checker
        self, statuses = self._self(tmp_path)
        monkeypatch.setattr(
            update_checker, 'plan_update',
            lambda _s, which=None: ('terminal', 'kgx', ['kgx', '-e']))
        opened: list = []
        monkeypatch.setattr(
            'subprocess.Popen',
            lambda argv, **_k: opened.append(argv) or types.SimpleNamespace())
        settings_page.SettingsPage._execute_owner_update(self, '/u.sh')
        assert opened == [['kgx', '-e']]
        assert statuses == ['Update running in terminal.']

    def test_headless_plan_streams_to_a_fresh_log(self, tmp_path,
                                                  monkeypatch):
        import settings_page
        import settings_updates
        import update_checker
        self, statuses = self._self(tmp_path)
        log_path = str(tmp_path / 'update-x.log')
        monkeypatch.setattr(
            update_checker, 'plan_update',
            lambda _s, which=None: ('headless', 'no terminal here',
                                    ['/u.sh']))
        monkeypatch.setattr(settings_updates, 'new_log_path',
                            lambda home=None: log_path)
        seen: dict = {}

        def fake_headless(script, on_line, on_done):
            seen['script'] = script
            on_line('Starting system update (alpine)...')
            on_line('  Flatpak                     ok')
            on_line('System update finished.')
            on_done(0)

        monkeypatch.setattr(update_checker, 'run_owner_headless',
                            fake_headless)
        settings_page.SettingsPage._execute_owner_update(self, '/u.sh')
        assert seen['script'] == '/u.sh'
        assert self._owner_log_path == log_path
        assert statuses[0].startswith('Updating system…')
        assert statuses[-1] == 'done'

    def test_owner_line_appends_to_log(self, tmp_path):
        import settings_page
        self, _statuses = self._self(tmp_path)
        log_path = str(tmp_path / 'update-x.log')
        self._owner_log_path = log_path
        settings_page.SettingsPage._on_owner_line(self, '  Flatpak   ok')
        with open(log_path, encoding='utf-8') as handle:
            assert 'Flatpak' in handle.read()

    def _done_self(self):
        statuses: list = []
        notes: list = []

        class _Shade:
            def add_notification(self, *args):
                notes.append(args)

        self = types.SimpleNamespace(
            _win=types.SimpleNamespace(_ensure_shade=lambda: _Shade()),
            _update_status_text=statuses.append,
            _refresh_updates_section=lambda: statuses.append('refresh'),
        )
        return self, statuses, notes

    def test_done_reports_result_and_reboot(self, tmp_path, monkeypatch):
        import settings_page
        import settings_updates
        self, statuses, notes = self._done_self()
        log_path = tmp_path / 'update-x.log'
        log_path.write_text(SAMPLE_SUMMARY, encoding='utf-8')
        monkeypatch.setattr(settings_updates, 'reboot_required',
                            lambda **_k: None)
        settings_page.SettingsPage._on_owner_done(self, 0, str(log_path))
        assert statuses[0] == 'System update finished.'
        assert statuses[-1] == 'refresh'
        assert notes and notes[0][2] == 'System update finished'
        assert '6.6.7 was replaced' in notes[0][3]

    def test_done_failure_names_failed_steps(self, tmp_path, monkeypatch):
        import settings_page
        import settings_updates
        self, statuses, notes = self._done_self()
        log_path = tmp_path / 'update-x.log'
        log_path.write_text(SAMPLE_SUMMARY, encoding='utf-8')
        monkeypatch.setattr(settings_updates, 'reboot_required',
                            lambda **_k: None)
        settings_page.SettingsPage._on_owner_done(self, 1, str(log_path))
        assert statuses[0] == 'System update failed: 1 step(s) failed.'
        assert notes == []

    def test_done_unstartable_is_plain(self, tmp_path, monkeypatch):
        import settings_page
        import settings_updates
        self, statuses, _notes = self._done_self()
        monkeypatch.setattr(settings_updates, 'reboot_required',
                            lambda **_k: None)
        settings_page.SettingsPage._on_owner_done(
            self, -1, str(tmp_path / 'missing.log'))
        assert statuses[0] == 'System update failed to start.'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
