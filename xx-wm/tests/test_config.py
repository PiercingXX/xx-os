"""Tests for config.py - pure logic, no GTK imports."""
import json
from pathlib import Path

import pytest

# Add launcher/src to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from config import DEFAULT_CONFIG, ShellConfig


class TestShellConfig:
    """Tests for ShellConfig class."""

    @pytest.fixture(autouse=True)
    def _isolated_home(self, tmp_path, monkeypatch):
        # ShellConfig() reads ~/.config/xx-wm/config.json in its
        # constructor; point HOME at a temp dir so the dev box's real shell
        # config never leaks into tests.
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))

    def test_default_config_has_home_slots(self):
        """DEFAULT_CONFIG must include home_slots key."""
        assert 'home_slots' in DEFAULT_CONFIG
        assert isinstance(DEFAULT_CONFIG['home_slots'], list)

    def test_default_config_has_default_layout_applied(self):
        """DEFAULT_CONFIG must include default_layout_applied key."""
        assert 'default_layout_applied' in DEFAULT_CONFIG
        assert isinstance(DEFAULT_CONFIG['default_layout_applied'], bool)

    def test_home_slots_property_empty_by_default(self, tmp_path):
        """home_slots returns empty list when not set."""
        config = ShellConfig()
        config.config_dir = tmp_path / 'config'
        config.config_path = config.config_dir / 'config.json'
        config.load()
        assert config.home_slots == []

    def test_home_slots_validation(self, tmp_path):
        """home_slots validates slot types and caps at 8."""
        config = ShellConfig()
        config.config_dir = tmp_path / 'config'
        config.config_path = config.config_dir / 'config.json'
        
        # Create slots with various valid/invalid entries
        slots = [
            {'type': 'app', 'label': 'Test App', 'app_id': 'test.desktop'},
            {'type': 'folder', 'label': 'Test Folder', 'folder': []},
            {'type': 'invalid', 'label': 'Bad'},  # Invalid type
            'not a dict',  # Not a dict
            {'type': 'app', 'label': 'App2', 'cmd': ['echo', 'hello']},
        ]
        config.set_home_slots(slots)
        
        result = config.home_slots
        assert len(result) == 3  # Only valid entries
        
        # Check first slot
        assert result[0]['type'] == 'app'
        assert result[0]['label'] == 'Test App'
        assert result[0]['app_id'] == 'test.desktop'
        
        # Check second slot (folder)
        assert result[1]['type'] == 'folder'
        assert result[1]['label'] == 'Test Folder'
        
        # Check third slot (cmd-based app)
        assert result[2]['type'] == 'app'
        assert result[2]['cmd'] == ['echo', 'hello']

    def test_home_slots_cap_at_8(self, tmp_path):
        """home_slots caps at 8 entries."""
        config = ShellConfig()
        config.config_dir = tmp_path / 'config'
        config.config_path = config.config_dir / 'config.json'
        
        slots = [{'type': 'app', 'label': f'Slot {i}', 'app_id': f'slot{i}.desktop'} for i in range(10)]
        config.set_home_slots(slots)
        
        result = config.home_slots
        assert len(result) == 8

    def test_set_default_layout_applied(self, tmp_path):
        """set_default_layout_applied persists correctly."""
        config = ShellConfig()
        config.config_dir = tmp_path / 'config'
        config.config_path = config.config_dir / 'config.json'
        
        config.set_default_layout_applied(True)
        config.load()  # Reload from file
        
        assert config.default_layout_applied is True

    def test_new_defaults_match_android_overhaul(self):
        """Defaults carried over from the launcher overhaul (ea84fbf)."""
        assert DEFAULT_CONFIG['font'] == 'jetbrains-mono-nerd'
        assert DEFAULT_CONFIG['home_alignment'] == 'center'
        widgets = DEFAULT_CONFIG['widgets']
        ordered = sorted(widgets, key=lambda k: widgets[k]['order'])
        assert ordered == ['time', 'date', 'weather', 'battery']
        assert widgets['weather']['enabled'] is True

    def test_app_labels_roundtrip(self, tmp_path):
        config = ShellConfig()
        config.config_dir = tmp_path / 'config'
        config.config_path = config.config_dir / 'config.json'

        config.set_app_label('org.gnome.Calendar', 'Calendar')
        assert config.label_for('org.gnome.Calendar', 'GNOME Calendar') == 'Calendar'
        assert config.label_for('unknown.app', 'Fallback') == 'Fallback'

        config.set_app_label('org.gnome.Calendar', None)
        assert config.label_for('org.gnome.Calendar', 'GNOME Calendar') == 'GNOME Calendar'

    def test_muted_apps_window(self, tmp_path):
        config = ShellConfig()
        config.config_dir = tmp_path / 'config'
        config.config_path = config.config_dir / 'config.json'

        now = 1_000_000.0
        config.set_app_muted('org.example.Chat', now + 3600, now=now)
        assert config.is_app_muted('org.example.Chat', now=now) is True
        assert config.is_app_muted('org.example.Chat', now=now + 3599) is True
        assert config.is_app_muted('org.example.Chat', now=now + 3601) is False
        assert config.is_app_muted('org.other.App', now=now) is False
        assert config.is_app_muted('', now=now) is False

    def test_muted_apps_expired_entries_pruned(self, tmp_path):
        config = ShellConfig()
        config.config_dir = tmp_path / 'config'
        config.config_path = config.config_dir / 'config.json'

        now = 1_000_000.0
        config.set_app_muted('a.app', now + 10, now=now)
        config.set_app_muted('b.app', now + 3600, now=now + 20)  # a.app expired by now+20
        assert 'a.app' not in config.muted_apps
        assert 'b.app' in config.muted_apps

    def test_retired_launch_counts_are_dropped_on_save(self, tmp_path):
        config = ShellConfig()
        config.config_dir = tmp_path / 'config'
        config.config_path = config.config_dir / 'config.json'
        config.config_dir.mkdir(parents=True)
        config.config_path.write_text(
            '{"theme": "ocean", "launch_counts": {"a.desktop": 3}}')
        config.load()
        assert 'launch_counts' not in config.data
        assert not hasattr(config, 'record_launch')
        config.save()
        assert 'launch_counts' not in config.config_path.read_text()

    def test_migration_from_old_config(self, tmp_path):
        """Old configs without home_slots should work."""
        config = ShellConfig()
        config.config_dir = tmp_path / 'config'
        config.config_path = config.config_dir / 'config.json'

        # Write an old-style config
        config.data = {'theme': 'amoled', 'font': 'space-mono'}
        config.save()

        # Load it back
        config.load()

        assert config.theme.key == 'amoled'
        assert config.home_slots == []
        assert config.default_layout_applied is False


class TestLegacyDirMigration:
    """piercing-shell → xx-wm is a one-time directory rename, not a key merge."""

    @pytest.fixture(autouse=True)
    def _isolated_home(self, tmp_path, monkeypatch):
        home = tmp_path / 'home'
        home.mkdir()
        monkeypatch.setenv('HOME', str(home))

    def test_migrates_legacy_dir_when_xx_wm_absent(self):
        home = Path.home()
        legacy = home / '.config' / 'piercing-shell'
        legacy.mkdir(parents=True)
        slot = {
            'type': 'app',
            'label': 'Notes',
            'app_id': 'org.gnome.TextEditor.desktop',
        }
        (legacy / 'config.json').write_text(json.dumps({
            'theme': 'amoled',
            'default_layout_applied': True,
            'home_slots': [slot],
        }), encoding='utf-8')
        (legacy / 'gestures.json').write_text(json.dumps({
            'swipe_left_home': 'launch:htop.desktop',
        }), encoding='utf-8')

        config = ShellConfig()
        xx_wm = home / '.config' / 'xx-wm'
        assert not (home / '.config' / 'piercing-shell').exists()
        assert xx_wm.is_dir()
        assert config.config_dir == xx_wm
        assert config.theme.key == 'amoled'
        assert config.default_layout_applied is True
        assert config.home_slots == [slot]
        gestures = json.loads((xx_wm / 'gestures.json').read_text(encoding='utf-8'))
        assert gestures['swipe_left_home'] == 'launch:htop.desktop'
        assert 'pin_hash' not in config.data
        disk = json.loads((xx_wm / 'config.json').read_text(encoding='utf-8'))
        assert 'pin_hash' not in disk or not disk.get('pin_hash')

    def test_does_not_migrate_when_xx_wm_exists(self):
        home = Path.home()
        legacy = home / '.config' / 'piercing-shell'
        xx_wm = home / '.config' / 'xx-wm'
        legacy.mkdir(parents=True)
        xx_wm.mkdir(parents=True)
        (legacy / 'keep-me.json').write_text('{"from":"legacy"}', encoding='utf-8')
        (xx_wm / 'config.json').write_text(
            json.dumps({'theme': 'paper'}), encoding='utf-8')

        config = ShellConfig()
        assert legacy.is_dir()
        assert (legacy / 'keep-me.json').read_text(encoding='utf-8') == (
            '{"from":"legacy"}')
        assert config.theme.key == 'paper'
        assert not (xx_wm / 'keep-me.json').exists()


class TestLoadTypeGuards:
    """A hand-edited config with a scalar where a list/dict belongs must
    fall back to the default at merge time, not leak into self.data."""

    @pytest.fixture(autouse=True)
    def _isolated_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))

    def _config_with_disk_payload(self, tmp_path, payload):
        config = ShellConfig()
        config.config_dir = tmp_path / 'config'
        config.config_path = config.config_dir / 'config.json'
        config.config_dir.mkdir(parents=True, exist_ok=True)
        config.config_path.write_text(json.dumps(payload), encoding='utf-8')
        config.load()
        return config

    def test_corrupt_pinned_loads_default(self, tmp_path):
        config = self._config_with_disk_payload(tmp_path, {'pinned': 'foo'})
        assert config.data['pinned'] == []
        assert config.pinned == []

    @pytest.mark.parametrize('key,default', [
        ('hidden_apps', []),
        ('home_slots', []),
        ('dnd_schedules', []),
        ('focus_apps', []),
        ('app_labels', {}),
        ('muted_apps', {}),
    ])
    def test_corrupt_container_keys_load_defaults(self, tmp_path, key, default):
        config = self._config_with_disk_payload(tmp_path, {key: 'foo'})
        assert config.data[key] == default

    def test_corrupt_widgets_load_full_default(self, tmp_path):
        config = self._config_with_disk_payload(tmp_path, {'widgets': 42})
        assert config.data['widgets'] == DEFAULT_CONFIG['widgets']

    @pytest.mark.parametrize('key', ['apn', 'apn_user', 'apn_pass'])
    def test_corrupt_string_keys_dropped(self, tmp_path, key):
        config = self._config_with_disk_payload(tmp_path, {key: ['junk']})
        assert config.data.get(key, '') == ''

    def test_unknown_and_scalar_keys_pass_through(self, tmp_path):
        payload = {'pin_hash': 'deadbeef', 'auto_lock_timeout': 30}
        config = self._config_with_disk_payload(tmp_path, payload)
        assert config.data['pin_hash'] == 'deadbeef'
        assert config.data['auto_lock_timeout'] == 30


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
