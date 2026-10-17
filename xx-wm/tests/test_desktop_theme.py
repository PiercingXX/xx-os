"""Desktop theme bridge (wm_apptheme lane).

Third-party apps only see the desktop settings and gtk.css; desktop_theme
mirrors the shell's resolved preset there. tmp HOME everywhere, fake Gio
settings and a recording spawn: never the real gsettings, never the real
dconf, never the developer's config.
"""
import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import desktop_theme  # noqa: E402
from desktop_theme import (  # noqa: E402
    MANAGED_MARKER,
    apply_gsettings,
    apply_now,
    color_scheme_for,
    gtk_css_paths,
    gtk_theme_for,
    is_dark,
    render_css,
    should_write,
    write_managed_css,
)

try:
    from xx_ui.theme import THEME_PRESETS
except ImportError:
    from config import THEME_PRESETS

DARK_KEYS = {'amoled', 'graphite', 'forest', 'ocean', 'aura', 'burgundy'}
LIGHT_KEYS = {'paper', 'mist'}


def _preset(**overrides):
    base = dict(THEME_PRESETS['amoled'].__dict__)
    base.update(overrides)
    return types.SimpleNamespace(**base)


class FakeSettings:
    def __init__(self, fail=False) -> None:
        self.written: dict[str, str] = {}
        self._fail = fail

    def set_string(self, key: str, value: str) -> None:
        if self._fail:
            raise RuntimeError('dconf down')
        self.written[key] = value


class SpawnRecorder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.waited = 0

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))

        class Handle:
            def wait(inner_self):
                self.waited += 1
                return 0

        return Handle()


def test_every_known_preset_classified():
    assert set(THEME_PRESETS) == DARK_KEYS | LIGHT_KEYS


@pytest.mark.parametrize('key', sorted(DARK_KEYS))
def test_dark_presets_prefer_dark(key):
    assert is_dark(THEME_PRESETS[key])
    assert color_scheme_for(THEME_PRESETS[key]) == 'prefer-dark'


@pytest.mark.parametrize('key', sorted(LIGHT_KEYS))
def test_light_presets_prefer_light(key):
    assert not is_dark(THEME_PRESETS[key])
    assert color_scheme_for(THEME_PRESETS[key]) == 'prefer-light'


def test_custom_colours_follow_luminance_not_names():
    assert color_scheme_for(_preset(background='#F3EEE2')) == 'prefer-light'
    assert color_scheme_for(_preset(background='#0d0b14')) == 'prefer-dark'


def test_gtk_theme_adw_variants_when_installed(tmp_path):
    installed = tmp_path / 'themes'
    (installed / 'adw-gtk3-dark').mkdir(parents=True)
    (installed / 'adw-gtk3').mkdir()
    assert gtk_theme_for(True, [installed]) == 'adw-gtk3-dark'
    assert gtk_theme_for(False, [installed]) == 'adw-gtk3'


def test_gtk_theme_adwaita_fallback(tmp_path):
    empty = tmp_path / 'themes'
    empty.mkdir()
    assert gtk_theme_for(True, [empty]) == 'Adwaita-dark'
    assert gtk_theme_for(False, [empty]) == 'Adwaita'


def _css_map(preset) -> dict[str, str]:
    out = {}
    for line in render_css(preset).splitlines():
        if line.startswith('@define-color'):
            _, name, value = line.split()
            out[name] = value.rstrip(';')
    return out


def test_css_covers_the_libadwaita_names():
    colours = _css_map(THEME_PRESETS['amoled'])
    for name in ('window_bg_color', 'window_fg_color', 'view_bg_color',
                 'view_fg_color', 'headerbar_bg_color', 'headerbar_fg_color',
                 'card_bg_color', 'popover_bg_color', 'dialog_bg_color',
                 'sidebar_bg_color', 'accent_bg_color', 'accent_color',
                 'accent_fg_color'):
        assert name in colours, name


def test_amoled_gives_true_black_and_signal_white_accent():
    colours = _css_map(THEME_PRESETS['amoled'])
    assert colours['window_bg_color'] == '#000000'
    assert colours['view_bg_color'] == '#000000'
    assert colours['sidebar_bg_color'] == '#000000'
    assert colours['accent_bg_color'] == '#d8d8d8'
    assert colours['accent_color'] == '#d8d8d8'
    assert colours['accent_fg_color'] == '#000000'


def test_paper_maps_light_with_dark_text_on_accent():
    colours = _css_map(THEME_PRESETS['paper'])
    assert colours['window_bg_color'] == '#F3EEE2'
    assert colours['window_fg_color'] == '#151515'
    assert colours['accent_bg_color'] == '#262626'
    assert colours['accent_fg_color'] == '#ffffff'


def test_css_carries_managed_marker_first():
    assert render_css(THEME_PRESETS['mist']).splitlines()[0] == MANAGED_MARKER


def test_apply_writes_both_gtk_css_files(tmp_path):
    assert apply_now(THEME_PRESETS['forest'], config_home=tmp_path,
                     theme_dirs=[tmp_path], settings=FakeSettings(),
                     spawn=SpawnRecorder())
    for version in ('gtk-4.0', 'gtk-3.0'):
        text = (tmp_path / version / 'gtk.css').read_text(encoding='utf-8')
        assert text.splitlines()[0] == MANAGED_MARKER
        assert '@define-color window_bg_color #10261B;' in text


def test_managed_file_is_refreshed(tmp_path):
    target = tmp_path / 'gtk-4.0' / 'gtk.css'
    target.parent.mkdir(parents=True)
    target.write_text(MANAGED_MARKER + '\n@define-color window_bg_color #000;\n',
                      encoding='utf-8')
    assert write_managed_css(target, render_css(THEME_PRESETS['ocean']))
    assert '@define-color window_bg_color #0F1C2E;' in target.read_text()


def test_foreign_file_is_never_overwritten(tmp_path):
    target = tmp_path / 'gtk.css'
    owned = '/* mine, hands off */\n@define-color window_bg_color pink;\n'
    target.write_text(owned, encoding='utf-8')
    assert not should_write(target)
    assert not write_managed_css(target, render_css(THEME_PRESETS['ocean']))
    assert target.read_text(encoding='utf-8') == owned


def test_empty_file_is_left_alone(tmp_path):
    target = tmp_path / 'gtk.css'
    target.write_text('', encoding='utf-8')
    assert not should_write(target)
    assert not write_managed_css(target, render_css(THEME_PRESETS['ocean']))
    assert target.read_text(encoding='utf-8') == ''


def test_write_is_atomic_tmp_plus_rename(tmp_path, monkeypatch):
    target = tmp_path / 'gtk-4.0' / 'gtk.css'
    replaced = []
    real_replace = os.replace

    def recording_replace(src, dst):
        replaced.append((Path(src), Path(dst)))
        assert Path(src).parent == Path(dst).parent
        return real_replace(src, dst)

    monkeypatch.setattr(os, 'replace', recording_replace)
    assert write_managed_css(target, render_css(THEME_PRESETS['aura']))
    assert len(replaced) == 1
    assert replaced[0][1] == target
    assert target.read_text(encoding='utf-8').splitlines()[0] == MANAGED_MARKER
    leftovers = [p for p in target.parent.iterdir() if p != target]
    assert leftovers == []


def test_gsettings_uses_gio_when_schema_exists():
    settings = FakeSettings()
    spawned = SpawnRecorder()
    assert apply_gsettings('prefer-dark', 'adw-gtk3-dark',
                           settings=settings, spawn=spawned)
    assert settings.written == {'color-scheme': 'prefer-dark',
                                'gtk-theme': 'adw-gtk3-dark'}
    assert spawned.calls == []


def test_gsettings_falls_back_to_fire_and_forget_spawn(monkeypatch):
    monkeypatch.setattr(desktop_theme, '_gio_settings', lambda *a, **k: None)
    spawned = SpawnRecorder()
    assert apply_gsettings('prefer-dark', 'adw-gtk3-dark',
                           settings=None, spawn=spawned)
    assert spawned.calls == [
        ['gsettings', 'set', 'org.gnome.desktop.interface',
         'color-scheme', 'prefer-dark'],
        ['gsettings', 'set', 'org.gnome.desktop.interface',
         'gtk-theme', 'adw-gtk3-dark'],
    ]
    assert spawned.waited == 0


def test_gio_failure_falls_back_to_spawn():
    spawned = SpawnRecorder()
    assert apply_gsettings('prefer-light', 'Adwaita',
                           settings=FakeSettings(fail=True), spawn=spawned)
    assert [c[-2:] for c in spawned.calls] == [['color-scheme', 'prefer-light'],
                                              ['gtk-theme', 'Adwaita']]


def test_gsettings_total_failure_reports_false(monkeypatch):
    monkeypatch.setattr(desktop_theme, '_gio_settings', lambda *a, **k: None)

    def missing(argv, **kwargs):
        raise FileNotFoundError('no gsettings here')

    assert not apply_gsettings('prefer-dark', 'Adwaita-dark',
                               settings=None, spawn=missing)


def test_tmp_home_honoured_without_xdg_config_home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv('XDG_CONFIG_HOME', raising=False)
    gtk4, gtk3 = gtk_css_paths()
    assert gtk4 == tmp_path / '.config' / 'gtk-4.0' / 'gtk.css'
    assert gtk3 == tmp_path / '.config' / 'gtk-3.0' / 'gtk.css'
    assert apply_now(THEME_PRESETS['burgundy'], settings=FakeSettings(),
                     spawn=SpawnRecorder(), theme_dirs=[tmp_path])
    assert gtk4.is_file() and gtk3.is_file()


def test_xdg_config_home_wins_over_home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'xdg'))
    gtk4, _gtk3 = gtk_css_paths()
    assert gtk4 == tmp_path / 'xdg' / 'gtk-4.0' / 'gtk.css'
