"""Tests for gesture_bindings.py — generated lisgd system-level bindings.

gestures.json stores action names (home, app_switcher, ...); lisgd can only
exec xx-wm-ipc verbs, so ACTION_TO_VERB maps them at generation time.
LEGACY_BINDINGS pins the valid-but-unmapped fallback (DEFAULT_VERBS);
DEFAULT_BINDINGS pins the mapped schema defaults.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Add launcher/src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import gesture_config
from gesture_bindings import (
    ACTION_TO_VERB, DEFAULT_VERBS, _kill_lisgd, detect_touch_device,
    generate_bindings, restart_lisgd,
)
from gesture_config import GestureConfig

# Fallback verbs for valid-but-unmapped values (launch:, camera, none).
# Truly invalid values are dropped at load, revealing the default action
# mapping instead. Order matters: it is lisgd's argument order.
LEGACY_BINDINGS = [
    '1,DU,B,S,R,/usr/bin/xx-wm-ipc gesture.keyboard',
    '1,DU,B,L,R,/usr/bin/xx-wm-ipc gesture.home',
    '1,UD,T,*,R,/usr/bin/xx-wm-ipc gesture.shade',
    '1,LR,L,*,R,/usr/bin/xx-wm-ipc gesture.back',
    '1,RL,R,*,R,/usr/bin/xx-wm-ipc gesture.back',
]

# Defaults resolve through _DEFAULTS action names mapped via ACTION_TO_VERB.
DEFAULT_BINDINGS = [
    '1,DU,B,S,R,/usr/bin/xx-wm-ipc gesture.home',
    '1,DU,B,L,R,/usr/bin/xx-wm-ipc gesture.switcher',
    '1,UD,T,*,R,/usr/bin/xx-wm-ipc gesture.shade',
    '1,LR,L,*,R,/usr/bin/xx-wm-ipc gesture.back',
    '1,RL,R,*,R,/usr/bin/xx-wm-ipc gesture.back',
]


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    monkeypatch.setattr(gesture_config, '_config_path',
                        lambda: tmp_path / 'gestures.json')


class TestDefaultActionNameMapping:
    def test_defaults_map_action_names_to_verbs(self):
        assert generate_bindings('/usr/bin') == DEFAULT_BINDINGS

    def test_bindir_is_substituted(self):
        out = generate_bindings('/usr/local/bin')
        assert out[0] == '1,DU,B,S,R,/usr/local/bin/xx-wm-ipc gesture.home'
        assert len(out) == len(DEFAULT_BINDINGS)

    def test_default_verbs_cover_every_slot(self):
        from gesture_bindings import _LISGD_GEOMETRY
        assert set(DEFAULT_VERBS) == set(_LISGD_GEOMETRY)

    def test_explicit_action_names_map(self, tmp_path):
        (tmp_path / 'gestures.json').write_text(json.dumps({
            'swipe_up_short': 'home',
            'swipe_up_long': 'app_switcher',
            'swipe_down_top': 'notification_shade',
            'swipe_left_edge': 'back',
        }), encoding='utf-8')
        assert generate_bindings('/usr/bin') == DEFAULT_BINDINGS
        assert ACTION_TO_VERB['home'] == 'gesture.home'
        assert ACTION_TO_VERB['app_switcher'] == 'gesture.switcher'
        assert ACTION_TO_VERB['notification_shade'] == 'gesture.shade'
        assert ACTION_TO_VERB['back'] == 'gesture.back'
        assert ACTION_TO_VERB['search'] == 'gesture.keyboard'

    def test_launch_home_swipes_do_not_change_lisgd(self, tmp_path):
        (tmp_path / 'gestures.json').write_text(json.dumps({
            'swipe_left_home': 'launch:htop.desktop',
            'swipe_right_home': 'launch:org.gnome.Nautilus.desktop',
        }), encoding='utf-8')
        assert generate_bindings('/usr/bin') == DEFAULT_BINDINGS


class TestRebinding:
    def test_verb_override_rebinds_slot_in_place(self, tmp_path):
        (tmp_path / 'gestures.json').write_text(json.dumps({
            'swipe_up_short': 'gesture.switcher',
            'swipe_left_edge': 'gesture.home',
        }), encoding='utf-8')
        got = generate_bindings('/usr/bin')
        assert got[0].endswith('gesture.switcher')
        assert got[3].endswith('gesture.home')
        assert got[4].endswith('gesture.home')

    def test_action_names_map_to_ipc_verbs(self, tmp_path):
        (tmp_path / 'gestures.json').write_text(json.dumps({
            'swipe_up_short': 'home',
            'swipe_up_long': 'app_switcher',
            'swipe_down_top': 'notification_shade',
            'swipe_left_edge': 'back',
        }), encoding='utf-8')
        assert generate_bindings('/usr/bin') == [
            '1,DU,B,S,R,/usr/bin/xx-wm-ipc gesture.home',
            '1,DU,B,L,R,/usr/bin/xx-wm-ipc gesture.switcher',
            '1,UD,T,*,R,/usr/bin/xx-wm-ipc gesture.shade',
            '1,LR,L,*,R,/usr/bin/xx-wm-ipc gesture.back',
            '1,RL,R,*,R,/usr/bin/xx-wm-ipc gesture.back',
        ]

    def test_search_action_maps_to_keyboard(self, tmp_path):
        (tmp_path / 'gestures.json').write_text(
            json.dumps({'swipe_up_short': 'search'}), encoding='utf-8')
        assert generate_bindings('/usr/bin')[0].endswith('gesture.keyboard')

    def test_launch_bindings_do_not_replace_lisgd_verb(self, tmp_path):
        (tmp_path / 'gestures.json').write_text(json.dumps({
            'swipe_up_short': 'launch:htop.desktop',
        }), encoding='utf-8')
        assert generate_bindings('/usr/bin')[0].endswith('gesture.keyboard')

    def test_launch_home_swipes_do_not_change_lisgd(self, tmp_path):
        (tmp_path / 'gestures.json').write_text(json.dumps({
            'swipe_left_home': 'launch:htop.desktop',
            'swipe_right_home': 'launch:org.gnome.Nautilus.desktop',
        }), encoding='utf-8')
        assert generate_bindings('/usr/bin') == DEFAULT_BINDINGS

    def test_legacy_both_recents_migrates_lisgd_to_pixel_split(self, tmp_path):
        (tmp_path / 'gestures.json').write_text(json.dumps({
            'swipe_up_short': 'app_switcher',
            'swipe_up_long': 'app_switcher',
            'swipe_left_home': 'launch:htop.desktop',
            'swipe_right_home': 'launch:org.gnome.Nautilus.desktop',
        }), encoding='utf-8')
        assert generate_bindings('/usr/bin') == DEFAULT_BINDINGS

    def test_action_to_verb_covers_settings_names(self):
        assert ACTION_TO_VERB == {
            'home': 'gesture.home',
            'app_switcher': 'gesture.switcher',
            'notification_shade': 'gesture.shade',
            'back': 'gesture.back',
            'search': 'gesture.keyboard',
        }

    def test_invalid_values_fall_back_silently(self, tmp_path):
        (tmp_path / 'gestures.json').write_text(json.dumps({
            'swipe_up_short': 'explode',
            'swipe_up_long': 'launch:org.some.App.desktop',
            'swipe_down_top': 'camera',
            'swipe_left_edge': 'none',
        }), encoding='utf-8')
        got = generate_bindings('/usr/bin')
        # explode is dropped → schema default home → gesture.home
        assert got[0].endswith('gesture.home')
        # launch: is in-shell; lisgd keeps DEFAULT_VERBS for the slot
        assert got[1].endswith('gesture.home')
        # camera is a valid unmapped action → DEFAULT_VERBS shade
        assert got[2].endswith('gesture.shade')
        assert got[3].endswith('gesture.back')

    def test_unmapped_action_uses_default_verb(self, tmp_path):
        (tmp_path / 'gestures.json').write_text(json.dumps({
            'swipe_up_short': 'camera',
        }), encoding='utf-8')
        assert generate_bindings('/usr/bin')[0].endswith('gesture.keyboard')

    def test_garbage_falls_back_to_mapped_default(self, tmp_path):
        (tmp_path / 'gestures.json').write_text(json.dumps({
            'swipe_up_short': 'explode',
        }), encoding='utf-8')
        assert generate_bindings('/usr/bin') == DEFAULT_BINDINGS

    def test_non_system_slots_are_ignored(self, tmp_path):
        (tmp_path / 'gestures.json').write_text(
            '{"squeeze": "gesture.home", "double_tap_home": "gesture.shade"}',
            encoding='utf-8',
        )
        assert generate_bindings('/usr/bin') == DEFAULT_BINDINGS

    def test_corrupt_config_file_falls_back(self, tmp_path):
        (tmp_path / 'gestures.json').write_text('{not json', encoding='utf-8')
        assert generate_bindings('/usr/bin') == DEFAULT_BINDINGS


class TestGestureConfigVerbValues:
    def test_verb_accepted_for_lisgd_slot_and_persisted(self, tmp_path):
        gc = GestureConfig()
        gc.set('swipe_up_short', 'gesture.switcher')
        assert GestureConfig().get('swipe_up_short') == 'gesture.switcher'

    def test_verb_rejected_for_non_lisgd_slot(self):
        with pytest.raises(ValueError):
            GestureConfig().set('squeeze', 'gesture.home')

    def test_unknown_verb_rejected(self):
        with pytest.raises(ValueError):
            GestureConfig().set('swipe_up_short', 'gesture.explode')

    def test_load_ignores_verb_for_non_lisgd_slot(self, tmp_path):
        (tmp_path / 'gestures.json').write_text('{"squeeze": "gesture.home"}',
                                                encoding='utf-8')
        assert GestureConfig().get('squeeze') == 'assistant'


class TestLisgdRestart:
    def test_detects_direct_touch(self, tmp_path, monkeypatch):
        monkeypatch.delenv('PIERCING_TOUCH_DEV', raising=False)
        node = tmp_path / 'event6'
        (node / 'device').mkdir(parents=True)
        (node / 'device' / 'properties').write_text('2\n')
        assert detect_touch_device(tmp_path) == '/dev/input/event6'

    def test_skips_non_direct(self, tmp_path, monkeypatch):
        monkeypatch.delenv('PIERCING_TOUCH_DEV', raising=False)
        node = tmp_path / 'event1'
        (node / 'device').mkdir(parents=True)
        (node / 'device' / 'properties').write_text('0\n')
        assert detect_touch_device(tmp_path) is None

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv('PIERCING_TOUCH_DEV', '/dev/input/event9')
        assert detect_touch_device() == '/dev/input/event9'

    def test_restart_spawns_lisgd_with_bindings(self):
        spawned: list[list[str]] = []
        killed: list[bool] = []
        assert restart_lisgd(
            '/usr/bin',
            touch_dev='/dev/input/event6',
            spawn=lambda argv, **_k: spawned.append(argv),
            kill_fn=lambda: killed.append(True),
            which=lambda name: '/usr/bin/lisgd' if name == 'lisgd' else None,
        )
        assert killed == [True]
        assert spawned[0][:4] == ['/usr/bin/lisgd', '-d', '/dev/input/event6', '-g']
        assert spawned[0][4].endswith('gesture.home')

    def test_restart_fails_without_touch(self):
        assert restart_lisgd(
            '/usr/bin',
            touch_dev='',
            spawn=lambda *_a, **_k: None,
            kill_fn=lambda: None,
            which=lambda _n: '/usr/bin/lisgd',
        ) is False

    def test_kill_is_fire_and_forget_popen(self, monkeypatch):
        spawned: list = []
        monkeypatch.setattr(subprocess, 'Popen',
                            lambda argv, **_k: spawned.append(argv))
        _kill_lisgd()
        assert spawned == [['pkill', '-x', 'lisgd']]


class TestCli:
    def test_stdout_matches_defaults_with_empty_home(self, tmp_path):
        env = dict(os.environ, HOME=str(tmp_path))
        script = Path(__file__).parent.parent / 'launcher' / 'src' / 'gesture_bindings.py'
        proc = subprocess.run(
            [sys.executable, str(script), '--bindir', '/usr/bin'],
            capture_output=True, text=True, env=env, timeout=30, check=True)
        assert proc.stdout.splitlines() == DEFAULT_BINDINGS


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
