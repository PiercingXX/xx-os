"""Wave 6 UI-pass lane (w6_ui_pass): Wave 2 items 1/2/3/9/10 + Wave 4.

Headless unit seams for every behavior this lane changed, plus the
Broadway real-widget smoke (home + drawer at phone size). Import idiom
follows the repo: ``launcher/src`` on sys.path, real gi when present.
"""
import subprocess
import sys
import types
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / 'launcher' / 'src'
sys.path.insert(0, str(SRC))

gi = pytest.importorskip('gi')

from weather import WeatherProvider  # noqa: E402
from app_index import (  # noqa: E402
    _is_shell_or_launcher,
    desktop_visible,
)


# ---------------------------------------------------------------------------
# Item 2: weather location setting


class _Cfg:
    def __init__(self, data):
        self.data = data


class TestWeatherLocation:
    def test_lat_lon_win_over_location_string(self):
        provider = WeatherProvider(
            _Cfg({'weather_lat': 1.0, 'weather_lon': 2.0,
                  'weather_location': '48.85,2.35'}))
        assert provider.coordinates() == (1.0, 2.0)

    def test_location_string_parsed_as_fallback(self):
        provider = WeatherProvider(_Cfg({'weather_location': ' 48.85, 2.35 '}))
        assert provider.coordinates() == (48.85, 2.35)

    @pytest.mark.parametrize('raw', [
        '', 'Paris', '48.85', '48.85;', '12,13,14', '91,0', '0,181',
        'abc,def', None, 42, {'lat': 1},
    ])
    def test_location_garbage_yields_no_coordinates(self, raw):
        assert WeatherProvider.parse_location(raw) is None

    def test_no_coordinates_at_all(self):
        assert WeatherProvider(_Cfg({})).coordinates() is None


# ---------------------------------------------------------------------------
# Item 2: home hides the weather line until real data arrives


class TestHomeWeatherHidden:
    def _page(self, seen):
        import home_page

        class FakeLabel:
            def set_text(self, text):
                seen['text'] = text

            def set_visible(self, visible):
                seen['visible'] = visible

        class FakeWeather:
            def __init__(self, text):
                self._text = text

            def refresh(self, on_done, force=False):
                on_done(self._text)

        win = types.SimpleNamespace(
            weather_label=FakeLabel(), _weather=FakeWeather(seen.pop('feed')))
        page = home_page.HomePage.__new__(home_page.HomePage)
        page._win = win
        return page

    def test_placeholder_hides_the_line(self):
        from weather import PLACEHOLDER
        seen = {'feed': PLACEHOLDER}
        self._page(seen)._refresh_weather()
        assert seen.get('visible') is False
        assert 'text' not in seen

    def test_real_data_shows_the_line(self):
        seen = {'feed': '18° Clear'}
        self._page(seen)._refresh_weather()
        assert seen.get('visible') is True
        assert seen.get('text') == '18° Clear'


# ---------------------------------------------------------------------------
# Item 1: unified theme registry — home sheet in the window provider


class TestThemeRegistry:
    def test_home_sheet_loads_through_window_provider(self):
        import window
        from config import ShellConfig

        class FakeProvider:
            def __init__(self):
                self.data = None

            def load_from_data(self, data):
                self.data = bytes(data)

        cfg = ShellConfig.__new__(ShellConfig)
        cfg.data = dict(window.DEFAULT_CONFIG)
        cfg.data['theme'] = 'custom'
        cfg.data['custom_background'] = '#2A1018'
        shell = window.ShellWindow.__new__(window.ShellWindow)
        shell.config = cfg
        shell.theme_provider = FakeProvider()
        window.ShellWindow._apply_theme(shell)
        assert b'.home-item-btn' in shell.theme_provider.data
        assert b'.drawer-sheet' in shell.theme_provider.data
        assert b'#2A1018' in shell.theme_provider.data
        assert b'message-dialog' in shell.theme_provider.data

    def test_home_build_registers_no_display_provider(self):
        src = (SRC / 'home_page.py').read_text(encoding='utf-8')
        assert 'add_provider_for_display' not in src

    def test_home_sheet_uses_resolved_preset_not_config_theme(self):
        src = (SRC / 'home_page.py').read_text(encoding='utf-8')
        assert 'config.theme' not in src


# ---------------------------------------------------------------------------
# Item 3: drawer index filters


class _FakeInfo:
    def __init__(self, lists):
        self._lists = lists

    def get_string_list(self, key):
        if key not in self._lists:
            raise AttributeError(key)
        return self._lists[key]


class TestDesktopVisibility:
    def test_no_lists_visible_everywhere(self):
        assert desktop_visible(_FakeInfo({}), {'xx-wm'}) is True

    def test_only_show_in_match(self):
        info = _FakeInfo({'OnlyShowIn': ['GNOME', 'XX-WM']})
        assert desktop_visible(info, {'xx-wm'}) is True

    def test_only_show_in_mismatch(self):
        info = _FakeInfo({'OnlyShowIn': ['GNOME']})
        assert desktop_visible(info, {'xx-wm'}) is False

    def test_not_show_in_match(self):
        info = _FakeInfo({'NotShowIn': ['XX-WM']})
        assert desktop_visible(info, {'xx-wm'}) is False

    def test_missing_getter_counts_as_no_lists(self):
        assert desktop_visible(object(), {'xx-wm'}) is True


class TestShellLauncherDenylist:
    @pytest.mark.parametrize('app_id', [
        'gnome-shell.desktop', 'org.gnome.Shell', 'phosh',
        'wofi.desktop', 'rofi', 'fuzzel', 'dmenu', 'ulauncher',
    ])
    def test_shells_and_launchers_hidden(self, app_id):
        assert _is_shell_or_launcher(app_id) is True

    @pytest.mark.parametrize('app_id', [
        'org.gnome.Calculator.desktop', 'firefox.desktop', 'org.gnome.Notes',
    ])
    def test_regular_apps_kept(self, app_id):
        assert _is_shell_or_launcher(app_id) is False


# ---------------------------------------------------------------------------
# Item 3: drawer launch has no success toast


class TestDrawerLaunchSilent:
    def _win(self, ok=True):
        import drawer_page

        status = []
        dropped = []
        recorded = []

        class FakeFocus:
            def is_paused_app(self, app_id):
                return False

        class FakeIndex:
            def launch(self, entry, tracker=None):
                return ok, None if ok else 'nope'

        class FakeConfig:
            def record_launch(self, app_id):
                recorded.append(app_id)

            def label_for(self, app_id, fallback):
                return fallback

        win = types.SimpleNamespace(
            _pick_slot_mode=False, _pick_gesture_key=None,
            _pick_exempt_mode=False, focus_state=FakeFocus(),
            app_index=FakeIndex(), config=FakeConfig(),
            _show_status=status.append,
            drop_to_background=lambda: dropped.append(True),
        )
        page = drawer_page.DrawerPage.__new__(drawer_page.DrawerPage)
        page._win = win
        return page, status, dropped, recorded

    def test_success_drops_to_background_without_toast(self):
        from app_index import AppEntry
        page, status, dropped, recorded = self._win(ok=True)
        entry = AppEntry(app_id='x.desktop', name='X', description='',
                         executable='', search_text='x', desktop_app=None)
        page._launch_entry(entry)
        assert dropped == [True]
        assert status == []
        assert recorded == ['x.desktop']

    def test_failure_still_reports(self):
        from app_index import AppEntry
        page, status, dropped, recorded = self._win(ok=False)
        entry = AppEntry(app_id='x.desktop', name='X', description='',
                         executable='', search_text='x', desktop_app=None)
        page._launch_entry(entry)
        assert dropped == []
        assert len(status) == 1 and 'Failed to launch' in status[0]


# ---------------------------------------------------------------------------
# Items 2/3: touch targets and sheet wiring (source pins; sizes on Broadway)


class TestTouchTargets:
    def test_alpha_strip_minimum_is_44px(self):
        import re
        css = (SRC / 'style.css').read_text(encoding='utf-8')
        block = re.search(r'\.alpha-jump \{(.*?)\}', css, re.S).group(1)
        assert 'min-height: 44px' in block
        assert 'min-width: 44px' in block

    def test_alpha_strip_scrolls_inside_the_drawer(self):
        src = (SRC / 'drawer_page.py').read_text(encoding='utf-8')
        assert '_alpha_scroller' in src

    def test_home_edit_controls_minimum_is_44px(self):
        src = (SRC / 'home_launcher.py').read_text(encoding='utf-8')
        assert '.home-edit-ctl' in src and 'min-height: 44px' in src

    def test_drawer_sheet_class_wired(self):
        src = (SRC / 'drawer_page.py').read_text(encoding='utf-8')
        assert "'drawer-sheet'" in src


# ---------------------------------------------------------------------------
# Wave 4: shutdown flush wiring + main-loop lint gate


class TestShutdownFlush:
    def test_do_shutdown_flushes_launch_counts(self):
        src = (SRC / 'main.py').read_text(encoding='utf-8')
        assert 'flush_launch_counts' in src


class TestMainloopLint:
    def test_no_blocking_calls_reachable_from_main_loop(self):
        proc = subprocess.run(
            [sys.executable, str(SRC.parent.parent / 'scripts'
                                / 'check_mainloop_blocks.py')],
            capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# Broadway: real home + drawer pages at phone size


_W6_BROADWAY_SMOKE = '''\\
"""Real ShellWindow on Broadway: no placeholder, 44px A-Z, surface drawer."""
import glob
import os
import subprocess
import sys
import time

src_dir, run_dir = sys.argv[1], sys.argv[2]
os.environ['GDK_BACKEND'] = 'broadway'
home = os.path.join(run_dir, 'home')
os.makedirs(home, exist_ok=True)
os.environ['HOME'] = home
os.environ['XDG_CONFIG_HOME'] = os.path.join(home, '.config')
os.environ['XDG_CACHE_HOME'] = os.path.join(home, '.cache')
os.environ['XDG_DATA_HOME'] = os.path.join(home, '.local', 'share')
os.environ['XDG_RUNTIME_DIR'] = run_dir
# A probed free display (a fixed number collided with parallel runs).
_common = src_dir
while _common != os.path.dirname(_common) and not os.path.isdir(
        os.path.join(_common, 'common', 'xx_testing')):
    _common = os.path.dirname(_common)
sys.path.insert(0, os.path.join(_common, 'common'))
from xx_testing import broadway_display
daemon, display = broadway_display.start(run_dir=run_dir)
os.environ['BROADWAY_DISPLAY'] = display
try:
    for _i in range(200):
        if glob.glob(os.path.join(run_dir, 'broadway*.socket')):
            break
        time.sleep(0.05)
    else:
        print('SMOKE FAIL: no broadway socket')
        sys.exit(1)
    sys.path.insert(0, src_dir)
    import gi
    gi.require_version('Gtk', '4.0')
    from gi.repository import Adw, Gtk, GLib, Gdk

    import window as window_mod
    import first_boot

    failures = []
    sys.excepthook = lambda *exc: failures.append(repr(exc[1]))

    app = Adw.Application(application_id='io.test.W6Ui')
    win = window_mod.ShellWindow(app)
    win.present()
    win.unfullscreen()
    win.set_default_size(360, 720)

    wiz = first_boot.FirstBootWizard(lambda: None)
    wiz.present()
    wiz.unfullscreen()

    def _walk(widget):
        yield widget
        child = widget.get_first_child()
        while child is not None:
            yield from _walk(child)
            child = child.get_next_sibling()

    def _min_height(widget):
        minimum, _natural = widget.measure(
            Gtk.Orientation.VERTICAL, -1)[:2]
        return minimum

    def _min_width(widget):
        minimum, _natural = widget.measure(
            Gtk.Orientation.HORIZONTAL, -1)[:2]
        return minimum

    def step_checks():
        try:
            home_page = win.stack.get_child_by_name('home')
            texts = [w.get_text() for w in _walk(home_page)
                     if isinstance(w, Gtk.Label)]
            assert not any('--\\u00b0' in t for t in texts), texts
            assert win.weather_label.get_visible() is False, (
                repr(win.weather_label.get_text()))

            alpha_box = win._alpha_scroller.get_child()
            buttons = [w for w in _walk(alpha_box)
                       if isinstance(w, Gtk.Button)]
            assert len(buttons) == 27, len(buttons)
            shorts = [(b.get_label(), _min_height(b)) for b in buttons
                      if _min_height(b) < 44]
            assert not shorts, shorts

            sheet = [w for w in _walk(win.stack.get_child_by_name('apps'))
                     if w.has_css_class('drawer-sheet')]
            assert sheet, 'no drawer-sheet container'

            class Recorder:
                def load_from_data(self, data):
                    self.data = bytes(data)

            rec = Recorder()
            win.theme_provider = rec
            win._apply_theme()
            from config import THEME_PRESETS
            surface = THEME_PRESETS['amoled'].surface.encode()
            assert b'.drawer-sheet' in rec.data, 'sheet rule missing'
            assert surface in rec.data, 'surface color missing'
            assert b'.home-item-btn' in rec.data, 'home sheet missing'

            win._home_launcher.set_edit_mode(True)
            edits = [w for w in _walk(win._home_launcher)
                     if isinstance(w, Gtk.Button)
                     and (w.has_css_class('home-edit-ctl')
                          or w.has_css_class('home-edit-action'))]
            assert edits, 'no edit-mode controls'
            small = [(_min_height(b)) for b in edits
                     if _min_height(b) < 44]
            assert not small, small
            win._home_launcher.set_edit_mode(False)

            assert win._app_monitor is not None, 'no AppInfoMonitor'

            for name in ('home', 'apps'):
                page = win.stack.get_child_by_name(name)
                min_w = _min_width(page)
                min_h = _min_height(page)
                print(f'PAGE {name}: min {min_w}x{min_h}')
                assert min_w <= 360, (name, min_w)
                assert min_h <= 720, (name, min_h)

            wiz_child = wiz.get_child()
            wiz_min = _min_width(wiz_child)
            print(f'WIZARD: min width {wiz_min}')
            assert wiz_min <= 320, wiz_min

            print(f'WINDOW: allocated '
                  f'{win.get_width()}x{win.get_height()}')
        except Exception as exc:
            failures.append(repr(exc))
        finally:
            loop.quit()
        return False

    def guard(step):
        def run():
            try:
                return step()
            except Exception as exc:
                failures.append(repr(exc))
                loop.quit()
                return False
        return run

    loop = GLib.MainLoop()
    GLib.timeout_add(1500, guard(step_checks))
    GLib.timeout_add(12000, guard(lambda: (
        failures.append('timeout'), loop.quit(), False)[2]))
    loop.run()
    assert not failures, failures
    print('W6 SMOKE OK')
finally:
    daemon.terminate()
'''


class TestBroadwayW6:
    def test_real_home_and_drawer_at_phone_size(self, tmp_path):
        import shutil
        if shutil.which('gtk4-broadwayd') is None:
            pytest.skip('needs gtk4-broadwayd')
        pytest.importorskip('gi')
        run_dir = tmp_path / 'broadway'
        run_dir.mkdir()
        script = tmp_path / 'w6_broadway_smoke.py'
        script.write_text(_W6_BROADWAY_SMOKE)
        proc = subprocess.run(
            [sys.executable, str(script), str(SRC), str(run_dir)],
            capture_output=True, text=True, timeout=180)
        assert 'W6 SMOKE OK' in proc.stdout, (
            proc.returncode, proc.stdout[-3000:], proc.stderr[-3000:])
