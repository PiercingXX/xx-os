"""lp_settings: Launcher section config keys, appearance mode, text scaling,
alignment, clock format and the logs viewer backend.

Headless: pure logic only (no display). UI assembly is covered by the
settings snap sheets plus the Broadway smoke test.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'common'))

import config as config_mod  # noqa: E402
import settings_page  # noqa: E402,F401 -- first: feature sections import back


@pytest.fixture(autouse=True)
def _forget_window_module():
    """These tests import the real window module; drop it afterwards so later
    files that import window under fake gi get a fresh module."""
    had = 'window' in sys.modules
    yield
    if not had:
        sys.modules.pop('window', None)


@pytest.fixture
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))


def _cfg(_isolated_home=None):
    return config_mod.ShellConfig()


class TestAppearanceMode:
    def test_default_is_theme(self):
        assert config_mod.DEFAULT_CONFIG['appearance_mode'] == 'theme'

    def test_round_trip(self, _isolated_home):
        cfg = _cfg()
        assert cfg.appearance_mode == 'theme'
        cfg.set_appearance_mode('dark')
        assert cfg.appearance_mode == 'dark'
        assert cfg.data['appearance_mode'] == 'dark'

    def test_all_modes_round_trip(self, _isolated_home):
        cfg = _cfg()
        for mode in ('theme', 'light', 'dark', 'system'):
            cfg.set_appearance_mode(mode)
            assert cfg.appearance_mode == mode

    def test_junk_set_ignored(self, _isolated_home):
        cfg = _cfg()
        cfg.set_appearance_mode('dark')
        cfg.set_appearance_mode('neon')
        assert cfg.appearance_mode == 'dark'

    def test_corrupt_reads_theme(self, _isolated_home):
        cfg = _cfg()
        cfg.data['appearance_mode'] = 'neon'
        assert cfg.appearance_mode == 'theme'
        cfg.data['appearance_mode'] = None
        assert cfg.appearance_mode == 'theme'


class TestGenericGetSet:
    def test_get_returns_default_for_missing(self, _isolated_home):
        cfg = _cfg()
        assert cfg.get('weather_temp_unit', 'fahrenheit') == 'fahrenheit'
        assert cfg.get('clock_24h', None) is True  # 24-hour default
        assert cfg.get('drawer_sort', 'default') == 'default'
        assert cfg.get('home_to_recents', False) is False

    def test_set_persists_and_reloads(self, _isolated_home):
        cfg = _cfg()
        cfg.set('weather_temp_unit', 'celsius')
        assert cfg.get('weather_temp_unit', 'fahrenheit') == 'celsius'
        again = config_mod.ShellConfig()
        assert again.get('weather_temp_unit', 'fahrenheit') == 'celsius'


class TestWidgetSetters:
    def test_enable_round_trip(self, _isolated_home):
        cfg = _cfg()
        assert cfg.widgets['weather']['enabled'] is True
        cfg.set_widget_enabled('weather', False)
        assert cfg.widgets['weather']['enabled'] is False
        assert cfg.ordered_widgets() == [
            ('time', cfg.widgets['time']),
            ('date', cfg.widgets['date']),
            ('battery', cfg.widgets['battery']),
        ]

    def test_unknown_widget_ignored(self, _isolated_home):
        cfg = _cfg()
        cfg.set_widget_enabled('stocks', False)
        assert 'stocks' not in cfg.widgets

    def test_move_swaps_order(self, _isolated_home):
        cfg = _cfg()
        cfg.move_widget('weather', -1)
        ordered = [key for key, _conf in cfg.ordered_widgets()]
        assert ordered == ['time', 'weather', 'date', 'battery']
        cfg.move_widget('weather', +1)
        ordered = [key for key, _conf in cfg.ordered_widgets()]
        assert ordered == ['time', 'date', 'weather', 'battery']

    def test_move_at_edge_is_noop(self, _isolated_home):
        cfg = _cfg()
        before = cfg.ordered_widgets()
        cfg.move_widget('time', -1)
        cfg.move_widget('battery', +1)
        assert [k for k, _c in cfg.ordered_widgets()] == [
            k for k, _c in before]

    def test_tap_round_trip(self, _isolated_home):
        cfg = _cfg()
        cfg.set_widget_tap('weather', 'none')
        assert cfg.widgets['weather']['tap'] == 'none'
        cfg.set_widget_tap('date', {'app': 'org.gnome.Calendar'})
        assert cfg.widgets['date']['tap'] == {'app': 'org.gnome.Calendar'}
        cfg.set_widget_tap('date', 'bogus-tap')
        assert cfg.widgets['date']['tap'] == {'app': 'org.gnome.Calendar'}

    def test_clear_app_labels(self, _isolated_home):
        cfg = _cfg()
        cfg.set_app_label('a.desktop', 'Aye')
        cfg.clear_app_labels()
        assert cfg.app_labels == {}

    def test_setters_do_not_mutate_defaults(self, _isolated_home):
        cfg = _cfg()
        cfg.set_widget_enabled('weather', False)
        cfg.move_widget('date', +1)
        cfg.set_widget_tap('time', 'none')
        assert config_mod.DEFAULT_CONFIG['widgets']['weather'] == {
            'enabled': True, 'order': 3, 'tap': 'default'}
        assert config_mod.DEFAULT_CONFIG['widgets']['time'] == {
            'enabled': True, 'order': 1, 'tap': 'default'}


class TestBackupAppearance:
    def test_export_carries_mode(self, _isolated_home):
        import backup
        cfg = _cfg()
        cfg.set_appearance_mode('dark')
        assert backup.export_backup(cfg)['appearance_mode'] == 'dark'

    def test_validate_rejects_mode(self, _isolated_home):
        import backup
        cfg = _cfg()
        payload = backup.export_backup(cfg)
        payload['appearance_mode'] = 'neon'
        ok, _error = backup.validate_backup(payload)
        assert ok is False

    def test_restore_applies_mode(self, _isolated_home):
        import backup
        cfg = _cfg()
        payload = backup.export_backup(cfg)
        payload['appearance_mode'] = 'light'
        assert backup.restore_backup(cfg, payload) is True
        assert cfg.appearance_mode == 'light'


class TestMonospaceFont:
    def test_generic_monospace_listed(self):
        from xx_ui import theme
        assert 'monospace' in theme.FONT_FAMILIES
        assert config_mod.FONT_FAMILIES is theme.FONT_FAMILIES

    def test_shell_accepts_monospace(self, _isolated_home):
        cfg = _cfg()
        cfg.set_font('monospace')
        assert cfg.data['font'] == 'monospace'
        assert cfg.font_family == 'Monospace'


class TestAppearanceResolution:
    def test_effective_dark_matrix(self, _isolated_home):
        import window
        cfg = _cfg()
        cfg.set_appearance_mode('theme')
        cfg.set_prefer_dark(True)
        assert window.effective_dark(cfg) is True
        cfg.set_prefer_dark(False)
        assert window.effective_dark(cfg) is False
        cfg.set_appearance_mode('light')
        cfg.set_prefer_dark(True)
        assert window.effective_dark(cfg) is False
        cfg.set_appearance_mode('dark')
        cfg.set_prefer_dark(False)
        assert window.effective_dark(cfg) is True

    def test_system_falls_back_to_prefer_dark(self, _isolated_home, monkeypatch):
        import window
        monkeypatch.setattr(window, 'system_prefers_dark', lambda: None)
        cfg = _cfg()
        cfg.set_appearance_mode('system')
        cfg.set_prefer_dark(True)
        assert window.effective_dark(cfg) is True
        cfg.set_prefer_dark(False)
        assert window.effective_dark(cfg) is False

    def test_system_dark_uses_system_value(self, _isolated_home, monkeypatch):
        import window
        cfg = _cfg()
        cfg.set_appearance_mode('system')
        monkeypatch.setattr(window, 'system_prefers_dark', lambda: True)
        assert window.effective_dark(cfg) is True
        monkeypatch.setattr(window, 'system_prefers_dark', lambda: False)
        assert window.effective_dark(cfg) is False

    def test_light_resolves_paper(self, _isolated_home):
        import window
        cfg = _cfg()
        cfg.set_appearance_mode('light')
        assert window.resolve_appearance_preset(cfg).key == 'paper'

    def test_dark_resolves_dark_preset(self, _isolated_home):
        import window
        cfg = _cfg()
        cfg.set_appearance_mode('dark')
        preset = window.resolve_appearance_preset(cfg)
        assert preset.key in ('amoled', 'graphite', 'forest', 'ocean',
                              'aura', 'burgundy', 'custom')

    def test_theme_mode_keeps_today(self, _isolated_home):
        import window
        cfg = _cfg()
        cfg.set_appearance_mode('theme')
        assert (window.resolve_appearance_preset(cfg).key
                == window.resolve_theme(cfg).key)

    def test_system_prefers_dark_guarded(self, monkeypatch):
        import window
        real_new = None
        try:
            from gi.repository import Gio
            real_new = Gio.Settings.new
        except Exception:
            pass

        def _missing(_schema):
            raise Exception('no such schema')

        monkeypatch.setattr('gi.repository.Gio.Settings.new', _missing)
        assert window.system_prefers_dark() is None
        if real_new is not None:
            monkeypatch.setattr(
                'gi.repository.Gio.Settings.new', real_new)


class TestLauncherTextScaling:
    def test_home_css_scales(self):
        from home_launcher import theme_css
        from config import THEME_PRESETS
        base = theme_css(THEME_PRESETS['amoled'])
        large = theme_css(THEME_PRESETS['amoled'], scale=1.5)
        assert 'font-size: 27pt' in base
        assert 'font-size: 40.5pt' in large

    def test_launcher_sheet_scales(self):
        import window
        base = window.launcher_text_css(1.0)
        large = window.launcher_text_css(1.5)
        assert 'font-size: 54pt' in base
        assert 'font-size: 14pt' in base
        assert 'font-size: 81pt' in large
        assert 'font-size: 21pt' in large
        assert base != large


class TestDrawerAlignment:
    def test_xalign_matrix(self):
        import drawer_page
        assert drawer_page.drawer_xalign(
            SimpleNamespace(config=SimpleNamespace(home_alignment='left'))
        ) == 0.0
        assert drawer_page.drawer_xalign(
            SimpleNamespace(config=SimpleNamespace(home_alignment='right'))
        ) == 1.0
        assert drawer_page.drawer_xalign(
            SimpleNamespace(config=SimpleNamespace(home_alignment='center'))
        ) == 0.5
        assert drawer_page.drawer_xalign(SimpleNamespace()) == 0.5


class TestGestureEditValue:
    def test_edit_valid_only_for_long_press_home(self, _isolated_home):
        from gesture_config import _is_valid_value, ACTION_LABELS
        assert _is_valid_value('long_press_home', 'edit') is True
        assert _is_valid_value('double_tap_home', 'edit') is False
        assert ACTION_LABELS['edit'] == 'Edit mode'

    def test_long_press_accepts_edit(self, _isolated_home):
        from gesture_config import GestureConfig
        gestures = GestureConfig()
        gestures.set('long_press_home', 'edit')
        assert gestures.get('long_press_home') == 'edit'


class TestLogTail:
    def test_tail_reads_last_lines(self, tmp_path):
        import settings_launcher
        path = tmp_path / 'shell.log'
        path.write_text(''.join('line %d\n' % i for i in range(600)),
                        encoding='utf-8')
        text = settings_launcher.tail_log_lines(str(path), 400)
        lines = text.splitlines()
        assert len(lines) == 400
        assert lines[0] == 'line 200'
        assert lines[-1] == 'line 599'

    def test_tail_short_file(self, tmp_path):
        import settings_launcher
        path = tmp_path / 'shell.log'
        path.write_text('only\n', encoding='utf-8')
        assert settings_launcher.tail_log_lines(
            str(path), 400) == 'only\n'

    def test_tail_missing_file(self, tmp_path):
        import settings_launcher
        assert settings_launcher.tail_log_lines(
            str(tmp_path / 'nope.log'), 400) == ''


class TestResetHomeLayout:
    def test_clears_and_reseeds(self, _isolated_home):
        import settings_launcher
        from gesture_config import GestureConfig
        cfg = _cfg()
        cfg.set_home_slots([{'type': 'app', 'label': 'X',
                             'app_id': 'x.desktop'}])
        cfg.set_app_label('x.desktop', 'X')
        gestures = GestureConfig()
        gestures.set('swipe_left_home', 'launch:y.desktop')
        settings_launcher.reset_home_layout(cfg, gestures)
        assert all(slot.get('app_id') != 'x.desktop'
                   for slot in cfg.home_slots)
        assert 'x.desktop' not in cfg.app_labels
        assert cfg.default_layout_applied is True
        left = gestures.get('swipe_left_home')
        assert left == 'none' or left.startswith('launch:')
        assert left != 'launch:y.desktop'
        fresh = GestureConfig()
        assert fresh.get('swipe_right_home') == 'camera'


class TestLauncherSectionRegistered:
    def test_section_key_and_deep_link(self):
        keys = [key for key, _cls in settings_page.SETTINGS_SECTIONS]
        assert 'launcher' in keys
        assert 'launcher' in settings_page._SETTINGS_SECTION_KEYS
        gestures = keys.index('gestures')
        assert keys.index('launcher') < gestures

    def test_meson_lists_new_module(self):
        meson = (Path(__file__).parent.parent
                 / 'launcher' / 'meson.build').read_text(encoding='utf-8')
        assert 'src/settings_launcher.py' in meson

    def test_no_blocking_calls_in_new_module(self):
        import ast
        tree = ast.parse((Path(__file__).parent.parent / 'launcher' / 'src'
                          / 'settings_launcher.py').read_text(
                              encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                assert not (
                    isinstance(func, ast.Attribute)
                    and func.attr == 'run'
                    and isinstance(func.value, ast.Name)
                    and func.value.id == 'subprocess'
                ), 'subprocess.run on the settings path'
                assert not (
                    isinstance(func, ast.Attribute)
                    and func.attr == 'call_sync'
                ), 'call_sync on the settings path'
