"""Tests for gesture_config.py action validation."""
from pathlib import Path

import pytest

# Add launcher/src to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import gesture_config
from gesture_config import GestureConfig, is_valid_action


class TestActionValidation:
    def test_fixed_actions_valid(self):
        assert is_valid_action('camera')
        assert is_valid_action('none')
        assert is_valid_action('notification_shade')

    def test_launch_actions_valid(self):
        assert is_valid_action('launch:skippy-pwa.desktop')
        assert is_valid_action('launch:org.gnome.Calculator.desktop')

    def test_invalid_actions_rejected(self):
        assert not is_valid_action('launch:')
        assert not is_valid_action('explode')
        assert not is_valid_action('')


class TestGestureConfig:
    @pytest.fixture(autouse=True)
    def _isolate_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gesture_config, '_config_path',
                            lambda: tmp_path / 'gestures.json')

    def test_home_swipe_defaults(self):
        gc = GestureConfig()
        # Unset on both sides (Android): left opens the camera, right the
        # dialer; first boot binds Skippy left and the camera right.
        assert gc.get('swipe_left_home') == 'none'
        assert gc.get('swipe_right_home') == 'none'
        from gesture_config import home_swipe_action
        assert home_swipe_action(gc, 'swipe_left_home') == 'camera'
        assert home_swipe_action(gc, 'swipe_right_home') == 'dialer'
        gc.set('swipe_left_home', 'launch:skippy.desktop')
        assert home_swipe_action(gc, 'swipe_left_home') == (
            'launch:skippy.desktop')

    def test_pixel_nav_defaults(self):
        gc = GestureConfig()
        assert gc.get('swipe_up_short') == 'home'
        assert gc.get('swipe_up_long') == 'app_switcher'

    def test_migrates_legacy_both_recents_pair(self, tmp_path):
        (tmp_path / 'gestures.json').write_text(
            '{"swipe_up_short": "app_switcher",'
            ' "swipe_up_long": "app_switcher",'
            ' "swipe_left_home": "launch:htop.desktop",'
            ' "swipe_right_home": "launch:org.gnome.Nautilus.desktop"}',
            encoding='utf-8',
        )
        gc = GestureConfig()
        assert gc.get('swipe_up_short') == 'home'
        assert gc.get('swipe_up_long') == 'app_switcher'
        assert gc.get('swipe_left_home') == 'launch:htop.desktop'
        assert gc.get('swipe_right_home') == 'launch:org.gnome.Nautilus.desktop'

    def test_does_not_migrate_partial_recents_override(self, tmp_path):
        (tmp_path / 'gestures.json').write_text(
            '{"swipe_up_short": "app_switcher"}', encoding='utf-8')
        gc = GestureConfig()
        assert gc.get('swipe_up_short') == 'app_switcher'
        assert gc.get('swipe_up_long') == 'app_switcher'

    def test_set_launch_action_persists(self):
        gc = GestureConfig()
        gc.set('swipe_left_home', 'launch:skippy-pwa.desktop')
        assert GestureConfig().get('swipe_left_home') == 'launch:skippy-pwa.desktop'

    def test_set_rejects_unknown_action(self):
        gc = GestureConfig()
        with pytest.raises(ValueError):
            gc.set('swipe_left_home', 'launch:')
        with pytest.raises(ValueError):
            gc.set('swipe_left_home', 'bogus')

    def test_load_ignores_invalid_saved_actions(self, tmp_path):
        (tmp_path / 'gestures.json').write_text(
            '{"swipe_left_home": "bogus", "swipe_right_home": "launch:app.desktop"}',
            encoding='utf-8',
        )
        gc = GestureConfig()
        assert gc.get('swipe_left_home') == 'none'
        assert gc.get('swipe_right_home') == 'launch:app.desktop'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
