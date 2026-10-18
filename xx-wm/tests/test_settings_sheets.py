"""Settings pickers open in-window sheets, and Home & launcher matches the
Android launcher's preference screen.

Regression for "tapping Alignment and Clock do nothing": every Settings
picker went through AppItemActions._show_popover, a Gtk.Popover on the
shell's layer-shell surface. phoc dismisses that popup as soon as the
shell flips its keyboard mode (a tap sets NONE, a menu sets EXCLUSIVE),
so the menu closed about a frame after it mapped. Pickers now draw in an
in-window sheet (action_sheet.py). The Broadway probe drives the real
ShellWindow: each Home & launcher picker must open a sheet that lives in
the shell window itself (no separate popup surface), stays open after a
tap-outside + layer-policy pass, and applies the picked value.

Headless tests pin the Android parity contract (titles, choices,
defaults, order) and the behaviour behind each row.
"""
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / 'launcher' / 'src'
sys.path.insert(0, str(SRC))


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    return tmp_path / 'home'


# ---------------------------------------------------------------------------
# Android parity: the page contract


class TestParityContract:
    def test_android_titles_and_order(self):
        src = (SRC / 'settings_launcher.py').read_text(encoding='utf-8')
        build = src.split('    def build(self, win) -> list:')[1].split(
            '    def refresh(self, win)')[0]
        titles = [
            "'Home Screen'", "'Default Launcher'", "'Home Slot Count'",
            "'Text Alignment'", "'Date/Time Mode'", "'Show Status Bar'",
            "'Widgets'", "'Temperature Unit'",
            "'App Drawer'", "'Auto-show Keyboard'", "'Sort Mode'",
            "'Hidden Apps'",
            "'Gestures'", "'Enable Swipe Left'", "'Swipe Left App'",
            "'Enable Swipe Right'", "'Swipe Right App'",
            "'Swipe Down Action'", "'Double Tap to Lock'",
            "'Home Button Opens Recents'",
        ]
        positions = [build.index(title) for title in titles]
        assert positions == sorted(positions), titles

    def test_android_choice_labels(self):
        import settings_page  # noqa: F401 - settings_launcher imports it
        import settings_launcher as sl
        assert [label for _v, label in sl._SLOT_COUNT_CHOICES] == [
            'None', '1', '2', '3', '4', '5', '6', '7', '8']
        assert [label for _v, label in sl._ALIGN_CHOICES] == [
            'Left', 'Center', 'Right']
        assert [label for _v, label in sl._DATE_TIME_CHOICES] == [
            'Off', 'Date only', 'Date and time']
        assert [label for _v, label in sl._TEMP_CHOICES] == [
            'Fahrenheit', 'Celsius']
        assert [label for _v, label in sl._SORT_CHOICES] == [
            'Default (pinned first)', 'Alphabetical',
            'Alphabetical (no priority)', 'Install date', 'App size']
        assert [label for _v, label in sl._SWIPE_DOWN_CHOICES] == [
            'Expand notifications', 'Web search', 'Disabled']
        assert sl.WIDGETS_SUMMARY == 'Enable, reorder, and set tap actions'
        assert sl.GESTURES_SUMMARY == (
            'What every swipe, tap, and long-press does')

    def test_android_defaults(self, home):
        from config import ShellConfig
        from gesture_config import GestureConfig, home_swipe_action
        cfg = ShellConfig()
        assert cfg.home_alignment == 'center'
        assert cfg.date_time_mode == 'date_time'
        assert cfg.status_bar_visible is False
        assert cfg.auto_show_keyboard is False
        assert cfg.drawer_sort == 'default'
        assert cfg.swipe_left_enabled is True
        assert cfg.swipe_right_enabled is True
        assert cfg.swipe_down_action == 'notifications'
        assert cfg.home_to_recents is False
        assert cfg.weather_temp_unit == 'fahrenheit'
        gc = GestureConfig()
        assert gc.get('double_tap_home') == 'none'
        assert home_swipe_action(gc, 'swipe_left_home') == 'camera'
        assert home_swipe_action(gc, 'swipe_right_home') == 'dialer'

    def test_system_swipes_left_the_launcher_page(self):
        import settings_page
        cats = {key: sections for key, _t, sections
                in settings_page.SETTINGS_CATEGORIES}
        assert cats['home'] == ('launcher',)
        assert 'gestures' in cats['system']

    def test_reset_home_layout_lives_with_backup(self):
        src = (SRC / 'settings_page.py').read_text(encoding='utf-8')
        backup = src.split('class BackupSection')[1].split('\nclass ')[0]
        assert 'confirm_reset_home' in backup


# ---------------------------------------------------------------------------
# Config: new prefs, validation and migration


class TestParityConfig:
    def test_slot_count_visible_and_empty(self, home):
        from config import ShellConfig
        cfg = ShellConfig()
        slots = [{'type': 'app', 'label': n, 'app_id': n + '.desktop'}
                 for n in ('A', 'B', 'C')]
        cfg.set_home_slots(slots)
        assert cfg.home_slot_count is None
        assert cfg.effective_slot_count() == 3
        assert len(cfg.visible_home_slots()) == 3
        assert cfg.empty_home_slots() == 0
        cfg.set_home_slot_count(5)
        assert cfg.visible_home_slots() == cfg.home_slots
        assert cfg.empty_home_slots() == 2
        cfg.set_home_slot_count(2)
        assert [s['label'] for s in cfg.visible_home_slots()] == ['A', 'B']
        assert len(cfg.home_slots) == 3  # past the count stays stored
        cfg.set_home_slot_count(42)
        assert cfg.home_slot_count == 8
        cfg.set_home_slot_count(0)
        assert cfg.visible_home_slots() == []

    def test_enums_reject_garbage(self, home):
        from config import ShellConfig
        cfg = ShellConfig()
        cfg.set_date_time_mode('sometimes')
        assert cfg.date_time_mode == 'date_time'
        cfg.set_swipe_down_action('fly')
        assert cfg.swipe_down_action == 'notifications'
        cfg.data['swipe_left_enabled'] = 'no'
        assert cfg.swipe_left_enabled is True

    def test_swipe_down_migrates_from_top_slot(self, home):
        from config import ShellConfig, migrate_launcher_prefs
        cfg = ShellConfig()
        off = types.SimpleNamespace(get=lambda _k: 'none')
        assert migrate_launcher_prefs(cfg, off) is True
        assert cfg.swipe_down_action == 'disabled'
        assert migrate_launcher_prefs(cfg, off) is False  # once
        fresh = ShellConfig()
        fresh.data['swipe_down_action'] = None
        shade = types.SimpleNamespace(get=lambda _k: 'notification_shade')
        migrate_launcher_prefs(fresh, shade)
        assert fresh.swipe_down_action == 'notifications'

    def test_double_tap_migrates_to_switch_values(self, home):
        from gesture_config import GestureConfig
        path = home / '.config' / 'xx-wm' / 'gestures.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'double_tap_home': 'settings'}))
        assert GestureConfig().get('double_tap_home') == 'none'
        assert json.loads(path.read_text())['double_tap_home'] == 'none'
        path.write_text(json.dumps({'double_tap_home': 'lock_screen'}))
        assert GestureConfig().get('double_tap_home') == 'lock_screen'
        with pytest.raises(ValueError):
            GestureConfig().set('double_tap_home', 'camera')

    def test_backup_round_trip_and_old_backups(self, home, tmp_path,
                                              monkeypatch):
        import backup
        from config import ShellConfig
        cfg = ShellConfig()
        cfg.set_home_slot_count(6)
        cfg.set_date_time_mode('date_only')
        cfg.set_swipe_down_action('web_search')
        cfg.set_status_bar_visible(True)
        cfg.set_swipe_right_enabled(False)
        exported = backup.export_backup(cfg)
        assert backup.validate_backup(exported) == (True, None)
        monkeypatch.setenv('HOME', str(tmp_path / 'other'))
        fresh = ShellConfig()
        assert backup.restore_backup(fresh, dict(exported))
        assert fresh.home_slot_count == 6
        assert fresh.date_time_mode == 'date_only'
        assert fresh.swipe_down_action == 'web_search'
        assert fresh.status_bar_visible is True
        assert fresh.swipe_right_enabled is False
        old = dict(exported, gestures={'double_tap_home': 'settings'})
        assert backup.validate_backup(old)[0] is True
        assert backup.validate_backup(
            dict(exported, home_slot_count=9))[0] is False


# ---------------------------------------------------------------------------
# Behaviour behind the rows


def _swipe_win(window, config, gestures, actions):
    return types.SimpleNamespace(
        config=config, gesture_config=gestures,
        _dispatch_gesture_action=actions.append,
        _open_web_search_prompt=lambda: actions.append('web-prompt'))


@pytest.fixture
def window_mod():
    pytest.importorskip('gi')
    import window
    return window


class TestSwipeBehaviour:
    def test_side_swipes_follow_enable_and_defaults(self, window_mod):
        actions = []
        cfg = types.SimpleNamespace(swipe_left_enabled=True,
                                    swipe_right_enabled=False)
        gestures = types.SimpleNamespace(get=lambda _k: 'none')
        win = _swipe_win(window_mod, cfg, gestures, actions)
        window_mod.home_side_swipe(win, 'swipe_left_home')
        window_mod.home_side_swipe(win, 'swipe_right_home')
        assert actions == ['camera']
        cfg.swipe_right_enabled = True
        window_mod.home_side_swipe(win, 'swipe_right_home')
        assert actions == ['camera', 'dialer']
        bound = types.SimpleNamespace(get=lambda _k: 'launch:skippy.desktop')
        win.gesture_config = bound
        window_mod.home_side_swipe(win, 'swipe_left_home')
        assert actions[-1] == 'launch:skippy.desktop'

    def test_swipe_down_action(self, window_mod):
        actions = []
        cfg = types.SimpleNamespace(swipe_down_action='notifications')
        win = _swipe_win(window_mod, cfg, None, actions)
        window_mod.home_swipe_down(win)
        cfg.swipe_down_action = 'web_search'
        window_mod.home_swipe_down(win)
        cfg.swipe_down_action = 'disabled'
        window_mod.home_swipe_down(win)
        assert actions == ['notification_shade', 'web-prompt']


class TestHomeWidgets:
    def _win(self, mode):
        class Block:
            def __init__(self):
                self.children = []

            def get_first_child(self):
                return None

            def append(self, widget):
                self.children.append(widget)

        labels = {name: types.SimpleNamespace(name=name, set_xalign=lambda _x: None)
                  for name in ('time', 'date', 'weather', 'battery')}
        cfg = types.SimpleNamespace(
            date_time_mode=mode, home_alignment='center',
            ordered_widgets=lambda: [(k, {}) for k in labels])
        win = types.SimpleNamespace(
            config=cfg, _widget_block=Block(),
            clock_label=labels['time'], date_label=labels['date'],
            weather_label=labels['weather'], status_strip=labels['battery'],
            _widget_taps_wired=set(labels))
        return win

    @pytest.mark.parametrize('mode,shown', [
        ('date_time', ['time', 'date', 'weather', 'battery']),
        ('date_only', ['date', 'weather', 'battery']),
        ('off', ['weather', 'battery']),
    ])
    def test_date_time_mode(self, mode, shown):
        from home_page import HomePage
        win = self._win(mode)
        HomePage(win)._build_widget_block()
        assert [w.name for w in win._widget_block.children] == shown


class TestShowStatusBar:
    def test_content_follows_the_android_switch(self):
        import status_bar
        assert status_bar.content_visible(
            types.SimpleNamespace(status_bar_visible=True)) is True
        assert status_bar.content_visible(
            types.SimpleNamespace(status_bar_visible=False)) is False
        assert status_bar.content_visible(None) is False


class TestHomeSlotCount:
    def _win(self, home):
        from config import ShellConfig
        cfg = ShellConfig()
        cfg.set_home_slots([{'type': 'app', 'label': n, 'app_id': n}
                            for n in ('A', 'B')])
        refreshed = []
        win = types.SimpleNamespace(
            config=cfg,
            stack=types.SimpleNamespace(set_visible_child_name=lambda _n: None),
            _home_launcher=types.SimpleNamespace(
                refresh=lambda **_k: refreshed.append(1)),
            _populate_apps=lambda _t: None,
            apps_search=types.SimpleNamespace(get_text=lambda: ''),
            _show_status=lambda _m: None,
            item_actions=None)
        return win

    def _entry(self, app_id):
        return types.SimpleNamespace(app_id=app_id, name=app_id)

    def test_add_fills_the_first_empty_slot(self, home):
        from home_page import HomePage
        win = self._win(home)
        win.config.set_home_slot_count(4)
        HomePage(win)._add_app_slot(self._entry('C'))
        assert [s['label'] for s in win.config.home_slots] == ['A', 'B', 'C']
        assert win.config.home_slot_count == 4
        assert win.config.empty_home_slots() == 1

    def test_add_past_a_full_count_grows_it(self, home):
        from home_page import HomePage
        win = self._win(home)
        win.config.set_home_slot_count(1)
        HomePage(win)._add_app_slot(self._entry('C'))
        # Inserted right after the visible slot, and shown.
        assert [s['label'] for s in win.config.home_slots] == ['A', 'C', 'B']
        assert win.config.home_slot_count == 2

    def test_remove_shrinks_the_count(self, home):
        from home_page import HomePage
        win = self._win(home)
        win.config.set_home_slot_count(2)
        HomePage(win)._remove_slot(0)
        assert win.config.home_slot_count == 1
        assert win.config.empty_home_slots() == 0

    def test_launcher_renders_placeholders(self):
        pytest.importorskip('gi')
        from home_launcher import EMPTY_SLOT_TEXT, HomeLauncher
        tapped = []
        launcher = HomeLauncher(
            get_slots_fn=lambda: [{'type': 'app', 'label': 'A'}],
            get_empty_count_fn=lambda: 2,
            on_empty_tap=lambda: tapped.append(1))
        rows = []
        child = launcher.get_first_child()
        while child is not None:
            rows.append(child)
            child = child.get_next_sibling()
        texts = [row.get_child().get_text() for row in rows]
        assert texts == ['A', EMPTY_SLOT_TEXT, EMPTY_SLOT_TEXT]
        assert rows[1].has_css_class('empty-slot')
        rows[2].emit('clicked')
        assert tapped == [1]


class TestAppSize:
    def test_parsers(self):
        import app_size
        assert app_size.parse_size('247.5 MB') == 247_500_000
        assert app_size.parse_size('9.59 MiB') == int(9.59 * 1024 ** 2)
        assert app_size.parse_size('nonsense') == 0
        assert app_size.parse_flatpak_sizes(
            'org.a.App\t2.4 MB\n') == {'org.a.App': 2_400_000}
        assert app_size.parse_pacman_sizes(
            'Name            : foo\nInstalled Size  : 1.00 KiB\n') == {
            'foo': 1024}
        assert app_size.parse_dpkg_sizes('foo\t10\n') == {'foo': 10240}

    def test_compute_sizes_batches_per_backend(self):
        import app_size
        entries = [types.SimpleNamespace(app_id=a) for a in ('a', 'b', 'c')]
        owners = {'a': {'flatpak': 'org.a'},
                  'b': {'owner': 'pkg-b', 'backend': 'pacman'},
                  'c': {'owner': None}}
        calls = []

        def run(cmd, timeout=15):
            calls.append(cmd[:2])
            out = ('org.a\t3 MB\n' if cmd[0] == 'flatpak' else
                   'Name : pkg-b\nInstalled Size : 2 KiB\n')
            return types.SimpleNamespace(stdout=out, returncode=0)

        sizes = app_size.compute_sizes(
            entries, run=run, resolve=lambda e: owners[e.app_id])
        assert sizes == {'a': 3_000_000, 'b': 2048}
        assert calls == [['flatpak', 'list'], ['pacman', '-Qi']]


# ---------------------------------------------------------------------------
# Broadway: the real ShellWindow, real clicks on the Settings rows


_SHEETS_BROADWAY = r'''
import glob
import json
import os
import subprocess
import sys
import time

src_dir, run_dir = sys.argv[1], sys.argv[2]
display = ':93'
os.environ['GDK_BACKEND'] = 'broadway'
os.environ['BROADWAY_DISPLAY'] = display
home = os.path.join(run_dir, 'home')
os.makedirs(os.path.join(home, '.config', 'xx-wm'), exist_ok=True)
apps = os.path.join(home, '.local', 'share', 'applications')
os.makedirs(apps, exist_ok=True)
for app_id, name in (('calc', 'Calculator'), ('notes', 'Notes')):
    with open(os.path.join(apps, app_id + '.desktop'), 'w') as fh:
        fh.write('[Desktop Entry]\nName=%s\nExec=true\nType=Application\n'
                 % name)
with open(os.path.join(home, '.config', 'xx-wm', 'config.json'), 'w') as fh:
    json.dump({'theme': 'amoled', 'hidden_apps': ['notes.desktop'],
               'default_layout_applied': True}, fh)
os.environ['HOME'] = home
os.environ['XDG_CONFIG_HOME'] = os.path.join(home, '.config')
os.environ['XDG_CACHE_HOME'] = os.path.join(home, '.cache')
os.environ['XDG_DATA_HOME'] = os.path.join(home, '.local', 'share')
os.environ['XDG_DATA_DIRS'] = os.path.join(home, '.local', 'share')
os.environ['XDG_RUNTIME_DIR'] = run_dir
daemon = subprocess.Popen(['gtk4-broadwayd', display],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _i in range(200):
        if glob.glob(os.path.join(run_dir, 'broadway*.socket')):
            break
        time.sleep(0.05)
    else:
        print('SHEETS FAIL: no broadway socket')
        sys.exit(1)
    sys.path.insert(0, src_dir)
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from gi.repository import Adw, GLib, Gtk

    import window as window_mod

    failures = []
    app = Adw.Application(application_id='io.test.SettingsSheets')
    win = window_mod.ShellWindow(app)
    win.present()
    win.unfullscreen()
    win.set_default_size(360, 720)

    def walk(widget):
        yield widget
        child = widget.get_first_child()
        while child is not None:
            yield from walk(child)
            child = child.get_next_sibling()

    def row(title):
        page = win._settings_stack.get_visible_child()
        for w in walk(page):
            lbl = getattr(w, 'title_label', None)
            if lbl is not None and lbl.get_text() == title:
                return w
        raise AssertionError('no row %r' % title)

    def tap(widget):
        if isinstance(widget, Gtk.Button):
            widget.emit('clicked')
        else:
            widget.get_first_child().emit('clicked')

    def pump(seconds=0.3):
        loop = GLib.MainLoop()
        GLib.timeout_add(int(seconds * 1000), loop.quit)
        loop.run()

    def sheet_rows():
        sheet = win._sheet_host.current
        return [w for w in walk(sheet.content)
                if isinstance(w, Gtk.Button) and w.has_css_class('xx-menu-row')]

    def row_label(button):
        for w in walk(button):
            if isinstance(w, Gtk.Label):
                return w.get_text()
        return ''

    def check(cond, msg):
        if not cond:
            failures.append(msg)

    pump(1.0)
    win.open_settings(None)
    win._settings_page.show_category('home')
    pump(0.8)

    pickers = ['Home Slot Count', 'Text Alignment', 'Date/Time Mode',
               'Clock Format', 'Widgets', 'Temperature Unit',
               'Manual Location', 'Sort Mode', 'Hidden Apps', 'Gestures',
               'Swipe Left App', 'Swipe Right App', 'Swipe Down Action']
    for title in pickers:
        win._sheet_host.close_all()
        tap(row(title))
        # What the phone does on every tap: tap-outside drops the
        # keyboard mode, opening a menu re-applies the layer policy.
        win._set_layer_keyboard_exclusive(False)
        win._apply_layer_policy()
        pump(0.3)
        sheet = win._sheet_host.current
        check(sheet is not None, '%s: no sheet' % title)
        if sheet is None:
            continue
        check(sheet.get_mapped(), '%s: sheet not mapped' % title)
        check(sheet.layer.get_native() is win,
              '%s: sheet is not drawn in the shell window' % title)
        popovers = [w for w in walk(win) if isinstance(w, Gtk.Popover)]
        check(not popovers, '%s: a Gtk.Popover exists' % title)
        print('SHEET OK', title)

    # A picked value applies and closes the sheet.
    win._sheet_host.close_all()
    tap(row('Text Alignment'))
    pump(0.2)
    left = [b for b in sheet_rows() if row_label(b) == 'Left']
    check(len(left) == 1, 'no Left row')
    left and left[0].emit('clicked')
    pump(0.2)
    check(win.config.home_alignment == 'left', 'alignment not applied')
    check(not win._sheet_host.is_open(), 'sheet stayed open')
    check(row('Text Alignment').subtitle_label.get_text() == 'Left',
          'row value not refreshed')

    # Swipe Right App: the picker lists apps and binds the pick.
    tap(row('Swipe Right App'))
    pump(0.2)
    calc = [b for b in sheet_rows() if row_label(b) == 'Calculator']
    check(len(calc) == 1, 'Calculator missing from Select App')
    calc and calc[0].emit('clicked')
    pump(0.2)
    check(win.gesture_config.get('swipe_right_home') == 'launch:calc.desktop',
          'swipe app not bound: %r' % win.gesture_config.get('swipe_right_home'))
    check(row('Swipe Right App').subtitle_label.get_text() == 'Calculator',
          'swipe app summary not shown')

    # Hidden Apps: a tap shows the app again.
    tap(row('Hidden Apps'))
    pump(0.2)
    notes = [b for b in sheet_rows() if row_label(b) == 'Notes']
    check(len(notes) == 1, 'Notes missing from Hidden Apps')
    notes and notes[0].emit('clicked')
    pump(0.2)
    check('notes.desktop' not in win.config.hidden_apps, 'not unhidden')

    # Widgets sheet: a switch flips the widget and the sheet stays open.
    tap(row('Widgets'))
    pump(0.2)
    switches = [w for w in walk(win._sheet_host.current.content)
                if isinstance(w, Gtk.Switch)]
    check(len(switches) == 4, 'widgets sheet switches: %d' % len(switches))
    if switches:
        switches[0].set_active(False)
        pump(0.2)
        first = sorted(win.config.widgets,
                       key=lambda k: win.config.widgets[k].get('order', 99))[0]
        check(win.config.widgets[first]['enabled'] is False,
              'widget switch not applied')
        check(win._sheet_host.is_open(), 'widgets sheet closed on flip')

    # Back closes a sheet before anything else.
    check(win._sheet_host.is_open(), 'no sheet before back')
    win._handle_back()
    check(not win._sheet_host.is_open(), 'back left the sheet open')

    # Other pages' pickers use the same sheet (Display › Mode).
    win._settings_page.show_category('display')
    pump(0.5)
    tap(row('Mode'))
    pump(0.2)
    check(win._sheet_host.is_open(), 'display Mode picker: no sheet')

    assert not failures, failures
    print('SHEETS SMOKE OK')
finally:
    daemon.terminate()
'''


class TestBroadwaySettingsSheets:
    def test_every_home_picker_opens_an_in_window_sheet(self, tmp_path):
        if shutil.which('gtk4-broadwayd') is None:
            pytest.skip('needs gtk4-broadwayd')
        pytest.importorskip('gi')
        run_dir = tmp_path / 'broadway'
        run_dir.mkdir()
        script = tmp_path / 'sheets_broadway.py'
        script.write_text(_SHEETS_BROADWAY)
        proc = subprocess.run(
            [sys.executable, str(script), str(SRC), str(run_dir)],
            capture_output=True, text=True, timeout=180)
        assert 'SHEETS SMOKE OK' in proc.stdout, (
            proc.returncode, proc.stdout[-3000:], proc.stderr[-3000:])
