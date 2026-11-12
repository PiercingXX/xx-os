"""Tests for backup.py — round-trip and malformed-payload no-write guarantee."""
import copy
import json
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from backup import (
    backup_export_dir,
    backup_label,
    discover_backups,
    export_backup,
    parse_backup_timestamp,
    restore_backup,
    restored_charge_limit,
    validate_backup,
    write_backup_file,
)
from config import ShellConfig


@pytest.fixture
def populated_config(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    cfg = ShellConfig()
    cfg.set_home_slots([
        {'type': 'app', 'label': 'Notes', 'app_id': 'notes.desktop'},
        {'type': 'folder', 'label': 'Tools', 'folder': [
            {'label': 'Calc', 'app_id': 'calc.desktop'}]},
    ])
    cfg.data.update({
        'app_labels': {'notes.desktop': 'Notes'},
        'pinned': ['notes.desktop'],
        'hidden_apps': ['junk.desktop'],
        'theme': 'forest',
        'prefer_dark': True,
        'sound_notifications': False,
        'dnd_schedules': [{'days': [0], 'start': '22:00', 'end': '06:30'}],
        'dnd_starred_numbers': ['+15550102222'],
        'focus_apps': ['chat.desktop'],
        'weather_lat': 52.52,
        'weather_lon': 13.4,
        'lock_screen_notifications': 'count',
        'pin_hash': 'SECRET',
        'launch_counts': {'notes.desktop': 9},
        'muted_apps': {'chat': 9e12},
        'focus_break_until': 9e12,
    })
    cfg.save()
    return cfg


class TestExport:
    def test_excludes_secrets_and_transient_state(self, populated_config):
        backup = export_backup(populated_config)
        assert 'pin_hash' not in json.dumps(backup)
        assert 'launch_counts' not in backup
        assert 'muted_apps' not in backup
        assert 'focus_break_until' not in backup

    def test_covers_prefs(self, populated_config):
        backup = export_backup(populated_config)
        assert backup['version'] == 1
        assert backup['theme'] == 'forest'
        assert backup['dnd_schedules'] == [{'days': [0], 'start': '22:00', 'end': '06:30'}]
        assert backup['focus_apps'] == ['chat.desktop']
        assert backup['weather_lat'] == 52.52
        assert backup['lock_screen_notifications'] == 'count'
        assert backup['pinned'] == ['notes.desktop']


class TestRoundTrip:
    def test_export_restore_onto_fresh_config(self, populated_config, tmp_path, monkeypatch):
        backup = export_backup(populated_config)

        monkeypatch.setenv('HOME', str(tmp_path / 'home2'))
        fresh = ShellConfig()
        assert restore_backup(fresh, copy.deepcopy(backup))

        assert fresh.home_slots == populated_config.home_slots
        assert fresh.data['app_labels'] == {'notes.desktop': 'Notes'}
        assert fresh.pinned == ['notes.desktop']
        assert fresh.hidden_apps == ['junk.desktop']
        assert fresh.data['theme'] == 'forest'
        assert fresh.data['dnd_starred_numbers'] == ['+15550102222']
        assert fresh.data['focus_apps'] == ['chat.desktop']
        assert fresh.data['weather_lat'] == 52.52
        assert fresh.lock_screen_notifications == 'count'
        assert fresh.data.get('pin_hash') is None
        # A second export matches the first — nothing drifted
        assert export_backup(fresh) == backup


class TestMalformedPayloads:
    @pytest.fixture
    def fresh(self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))
        return ShellConfig()

    @pytest.mark.parametrize('mutate', [
        lambda p: p.update(version=2),
        lambda p: p.update(home_slots='not a list'),
        lambda p: p.update(home_slots=[{'type': 'app'}] * 9),
        lambda p: p.update(home_slots=[{'type': 'wallpaper'}]),
        lambda p: p.update(widgets='no'),
        lambda p: p.update(theme='custom', custom_background='red'),
        lambda p: p.update(text_size_scale=9.0),
        lambda p: p.update(auto_lock_timeout=-5),
        lambda p: p.update(home_alignment='justified'),
        lambda p: p.update(lock_screen_notifications='banners'),
        lambda p: p.update(dnd_enabled='yes'),
        lambda p: p.update(focus_apps='chat'),
        lambda p: p.update(weather_lat='north'),
        lambda p: p.update(gestures={'made_up_gesture': 'home'}),
        lambda p: p.update(gestures={'swipe_left_home': 'explode'}),
        lambda p: p.update(gestures={'swipe_left_home': 'launch:'}),
    ])
    def test_invalid_payload_writes_nothing(self, fresh, mutate):
        payload = {'version': 1, 'home_slots': [], 'theme': 'amoled',
                   'font': 'jetbrains-mono-nerd'}
        mutate(payload)
        before = copy.deepcopy(fresh.data)
        ok, error = validate_backup(payload)
        assert not ok and error
        assert not restore_backup(fresh, payload)
        assert fresh.data == before
        assert not fresh.config_path.exists()

    def test_truncated_json_is_callers_problem_but_never_a_dict(self, fresh):
        try:
            payload = json.loads('{"version": 1, "home_slo')
        except json.JSONDecodeError:
            payload = None
        assert payload is None
        assert not restore_backup(fresh, payload or [])


class TestGestureVerbSlots:
    """gesture_bindings landed IPC verbs for the lisgd-driven slots, so
    backups made after rebinding carry verb values. validate_backup must use
    gesture_config's own slot/value rule instead of rejecting verbs."""

    @staticmethod
    def _payload(gestures):
        return {'version': 1, 'home_slots': [], 'theme': 'amoled',
                'font': 'jetbrains-mono-nerd', 'gestures': gestures}

    @pytest.fixture
    def fresh(self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))
        return ShellConfig()

    def test_lisgd_slot_verb_validates_and_restores(self, fresh):
        payload = self._payload({'swipe_up_short': 'gesture.switcher'})
        ok, error = validate_backup(payload)
        assert ok and error is None

        assert restore_backup(fresh, payload)
        from gesture_config import GestureConfig
        assert GestureConfig().get('swipe_up_short') == 'gesture.switcher'

    @pytest.mark.parametrize('slot,verb', [
        ('swipe_up_short', 'gesture.switcher'),
        ('swipe_up_long', 'gesture.home'),
        ('swipe_down_top', 'gesture.shade'),
        ('swipe_left_edge', 'gesture.back'),
        ('swipe_left_edge', 'gesture.keyboard'),
    ])
    def test_every_lisgd_slot_accepts_every_verb(self, slot, verb):
        ok, error = validate_backup(self._payload({slot: verb}))
        assert ok and error is None

    def test_actions_still_accepted_on_any_slot(self):
        ok, error = validate_backup(self._payload({
            'double_tap_home': 'camera',
            'squeeze': 'launch:org.some.App.desktop',
        }))
        assert ok and error is None

    def test_verb_on_non_system_slot_rejected(self):
        ok, error = validate_backup(
            self._payload({'double_tap_home': 'gesture.home'}))
        assert not ok
        assert 'unknown gesture action' in error

    def test_garbage_still_rejected(self):
        ok, error = validate_backup(self._payload({'swipe_up_short': 'explode'}))
        assert not ok
        assert 'unknown gesture action' in error


class TestHostileFontFamily:
    """Defense-in-depth: a hostile custom_font_family must be cleaned at
    validation time so it can never reach CSS interpolation."""

    HOSTILE = "Evil'; } window { background: url(http://x) } \\ `'"

    @pytest.fixture
    def fresh(self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))
        return ShellConfig()

    def _payload(self):
        return {'version': 1, 'home_slots': [], 'theme': 'custom',
                'custom_background': '#112233',
                'font': 'custom', 'custom_font_family': self.HOSTILE}

    @pytest.mark.parametrize('hostile', [
        "Evil'; } window { background: red } '",
        'Back\\slash',
        'Tick`Name',
        'Semi;colon',
        'Quote"Name',
    ])
    def test_validate_cleans_css_breakers(self, hostile):
        payload = {'version': 1, 'home_slots': [], 'theme': 'custom',
                   'custom_background': '#112233',
                   'font': 'custom', 'custom_font_family': hostile}
        ok, error = validate_backup(payload)
        assert ok and error is None
        cleaned = payload['custom_font_family']
        assert cleaned
        for ch in '\'"`\\;{}':
            assert ch not in cleaned

    def test_validate_rejects_non_string_family(self):
        payload = {'version': 1, 'home_slots': [], 'theme': 'amoled',
                   'font': 'custom', 'custom_font_family': 123}
        ok, error = validate_backup(payload)
        assert not ok
        assert 'custom_font_family' in error

    def test_restore_writes_sanitized_family(self, fresh):
        payload = self._payload()
        assert validate_backup(payload)[0]
        assert restore_backup(fresh, payload)
        family = fresh.data['custom_font_family']
        assert family
        for ch in '\'"`\\;{}':
            assert ch not in family

    def test_fully_hostile_family_falls_back_to_default(self, fresh):
        from config import FONT_FAMILIES, DEFAULT_CONFIG
        payload = {'version': 1, 'home_slots': [], 'theme': 'custom',
                   'custom_background': '#112233',
                   'font': 'custom', 'custom_font_family': '\';}{'}
        assert validate_backup(payload)[0]
        assert restore_backup(fresh, payload)
        assert fresh.data['custom_font_family'] == FONT_FAMILIES[DEFAULT_CONFIG['font']]


def _touch(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_text('{}', encoding='utf-8')
    return path


class TestBackupDiscovery:
    def test_finds_home_and_documents_newest_first(self, tmp_path):
        home = tmp_path / 'home'
        docs = home / 'Documents'
        docs.mkdir(parents=True)
        _touch(home, 'xx-wm-backup-20240102_080000.json')
        _touch(docs, 'xx-wm-backup-20240304_120000.json')
        _touch(docs, 'xx-wm-backup-20240203_090000.json')
        _touch(home, 'notes.txt')

        found = discover_backups([docs, home])
        assert [p.name for p in found] == [
            'xx-wm-backup-20240304_120000.json',
            'xx-wm-backup-20240203_090000.json',
            'xx-wm-backup-20240102_080000.json',
        ]

    def test_missing_directories_give_empty_list(self, tmp_path):
        assert discover_backups([tmp_path / 'nope']) == []

    def test_limit_caps_choices(self, tmp_path):
        home = tmp_path / 'home'
        home.mkdir()
        for day in range(1, 15):
            _touch(home, f'xx-wm-backup-202401{day:02d}_080000.json')
        found = discover_backups([home], limit=10)
        assert len(found) == 10
        assert found[0].name == 'xx-wm-backup-20240114_080000.json'


class TestBackupLabels:
    def test_label_is_parsed_date_and_time(self):
        assert backup_label('xx-wm-backup-20240304_120000.json') == '2024-03-04 12:00'

    def test_unparseable_name_falls_back_to_stem(self):
        assert backup_label('xx-wm-backup-manual.json') == 'xx-wm-backup-manual'

    def test_bad_calendar_date_gives_no_timestamp(self):
        assert parse_backup_timestamp('xx-wm-backup-20241345_999999.json') is None
        assert parse_backup_timestamp('notes.txt') is None


class TestExportDestination:
    def test_prefers_documents_when_present(self, tmp_path, monkeypatch):
        home = tmp_path / 'home'
        (home / 'Documents').mkdir(parents=True)
        monkeypatch.setenv('HOME', str(home))
        assert backup_export_dir() == home / 'Documents'

    def test_falls_back_to_home(self, tmp_path, monkeypatch):
        home = tmp_path / 'home'
        home.mkdir()
        monkeypatch.setenv('HOME', str(home))
        assert backup_export_dir() == home


class TestChargeLimitRestore:
    @pytest.fixture
    def limited(self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))
        cfg = ShellConfig()
        cfg.set_charge_limit(80)
        return cfg

    def _payload(self, **extra):
        payload = {'version': 1, 'home_slots': [], 'theme': 'amoled',
                   'font': 'jetbrains-mono-nerd'}
        payload.update(extra)
        return payload

    def test_backup_without_limit_keeps_the_enforced_one(self, limited):
        assert restore_backup(limited, self._payload())
        assert limited.charge_limit == 80
        assert restored_charge_limit(self._payload()) == (False, None)

    def test_restore_leaves_the_limit_to_the_helper(self, limited):
        assert restore_backup(limited, self._payload(charge_limit=None))
        assert limited.charge_limit == 80
        assert restored_charge_limit(self._payload(charge_limit=None)) == (
            True, None)
        assert restored_charge_limit(self._payload(charge_limit=90)) == (
            True, 90)


class TestWriteBackupFile:
    def test_owner_only_and_no_temp_left(self, tmp_path):
        from datetime import datetime
        path = write_backup_file({'version': 1, 'medical_id': {'name': 'A'}},
                                 tmp_path, now=datetime(2026, 1, 2, 3, 4, 5))
        assert path == tmp_path / 'xx-wm-backup-20260102_030405.json'
        assert (path.stat().st_mode & 0o777) == 0o600
        assert json.loads(path.read_text())['medical_id'] == {'name': 'A'}
        assert [p.name for p in tmp_path.iterdir()] == [path.name]

    def test_failed_write_raises_and_leaves_nothing(self, tmp_path):
        with pytest.raises(TypeError):
            write_backup_file({'version': 1, 'bad': object()}, tmp_path)
        assert list(tmp_path.iterdir()) == []
        with pytest.raises(OSError):
            write_backup_file({'version': 1}, tmp_path / 'missing')
