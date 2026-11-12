"""Docked surfaces under xxwm: top bar, launcher on Super, keybind sheet."""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import textwrap
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / 'launcher' / 'src'))

desk_bar = desk_launcher = desk_session = keybind_sheet = xxwm_backend = None
search_results = None


@pytest.fixture(autouse=True)
def _modules():
    """Imported at test time, not collection time: other test modules
    stub `gi` while they are collected, and these modules build GTK
    classes on import."""
    global desk_bar, desk_launcher, desk_session, keybind_sheet
    global xxwm_backend, search_results
    import desk_bar as _bar
    import desk_launcher as _launcher
    import desk_session as _session
    import keybind_sheet as _sheet
    import xxwm_backend as _backend
    from drawer_page import search_results as _search
    desk_bar, desk_launcher, desk_session = _bar, _launcher, _session
    keybind_sheet, xxwm_backend, search_results = _sheet, _backend, _search


def _state(outputs, windows=()):
    return {'outputs': list(outputs), 'windows': list(windows)}


HANDSET = {'name': 'DSI-1', 'x': 0, 'y': 0, 'w': 360, 'h': 720,
           'handset': True, 'workspace': 2, 'focused': False, 'title': ''}
MONITOR = {'name': 'HDMI-A-1', 'x': 360, 'y': 0, 'w': 1280, 'h': 720,
           'handset': False, 'workspace': 1, 'focused': True,
           'title': 'notes.md - nvim'}


# ---- bar ---------------------------------------------------------------

class TestBarState:
    def test_only_monitors_get_a_bar(self):
        state = _state([HANDSET, MONITOR])
        assert desk_bar.desk_outputs(state) == [MONITOR]
        assert desk_bar.desk_outputs(_state([HANDSET])) == []
        assert desk_bar.desk_outputs(None) == []

    def test_zero_sized_output_gets_none(self):
        gone = dict(MONITOR, w=0, h=0)
        assert desk_bar.desk_outputs(_state([gone])) == []

    def test_workspaces_in_use_plus_current(self):
        windows = [{'workspace': 4}, {'workspace': 10}, {'workspace': 4}]
        items = desk_bar.workspace_items(_state([MONITOR], windows), MONITOR)
        assert items == [(1, True), (4, False), (10, False)]

    def test_handset_workspace_is_not_listed_on_the_monitor(self):
        windows = [{'workspace': 2}, {'workspace': 3}]
        state = _state([HANDSET, MONITOR], windows)
        assert desk_bar.workspace_items(state, MONITOR) == [(1, True), (3, False)]

    def test_title_and_key(self):
        assert desk_bar.output_title(MONITOR) == 'notes.md - nvim'
        assert desk_bar.output_title({}) == ''
        assert desk_bar.output_key(MONITOR) == 'HDMI-A-1'
        assert desk_bar.output_key({'x': 5, 'y': 6}) == '5,6'

    def test_monitor_match_by_name_then_geometry(self):
        monitors = [('DSI-1', 0, 0, 360, 720), ('HDMI-A-1', 360, 0, 1280, 720)]
        assert desk_bar.match_monitor(monitors, MONITOR) == 1
        unnamed = dict(MONITOR, name='')
        assert desk_bar.match_monitor(monitors, unnamed) == 1
        assert desk_bar.match_monitor(monitors, dict(MONITOR, name='DP-9', x=9)) is None

    def test_clock_and_counts(self):
        now = datetime(2026, 11, 5, 9, 4)
        assert desk_bar.clock_parts(now, '09:04') == ('Thu, Nov 5', '09:04')
        assert desk_bar.notification_text(0) == ''
        assert desk_bar.notification_text(3) == '3'
        assert desk_bar.CALL_TEXT['active'] == 'On call'

    def test_css_follows_every_preset(self):
        from config import THEME_PRESETS
        for preset in THEME_PRESETS.values():
            css = (desk_bar.theme_css(preset)
                   + desk_launcher.theme_css(preset)
                   + keybind_sheet.theme_css(preset))
            assert preset.background in css and preset.foreground in css
            hexes = set(re.findall(r'#[0-9a-fA-F]{6}\b', css))
            from config import WARNING_ORANGE
            allowed = {preset.background, preset.foreground, preset.muted,
                       preset.accent, WARNING_ORANGE}
            assert hexes <= allowed, hexes - allowed


# ---- keybind sheet -------------------------------------------------------

def _default_binds() -> list[dict]:
    """The table `xxwm msg binds` returns with no wm.conf, rebuilt from
    the Rust defaults so the sheet is tested against the real binds."""
    text = (REPO / 'wm' / 'src' / 'config.rs').read_text()
    block = text.split('pub const DEFAULTS: &str = r#"', 1)[1].split('"#;', 1)[0]
    binds = [{'combo': 'Super', 'action': 'launcher'}]
    for line in block.splitlines():
        if line.startswith('bind '):
            _b, combo, action = line.split(' ', 2)
            binds.append({'combo': combo, 'action': action})
    for n in range(1, 11):
        key = n % 10
        binds += [{'combo': f'Super+{key}', 'action': f'workspace {n}'},
                  {'combo': f'Super+Shift+{key}', 'action': f'send {n}'},
                  {'combo': f'Super+Alt+{key}', 'action': f'send-silent {n}'}]
    return binds


class TestKeybindSheet:
    def test_combo_and_spawn_labels(self):
        assert keybind_sheet.pretty_combo('Super+slash') == 'Super+/'
        assert keybind_sheet.pretty_combo('Super+grave') == 'Super+`'
        assert keybind_sheet.pretty_combo('XF86AudioPlay') == 'Play'
        assert keybind_sheet.spawn_label('kitty') == 'Kitty'
        assert keybind_sheet.spawn_label('kitty tmux') == 'Tmux'
        assert keybind_sheet.spawn_label(
            'kitty ~/.scripts/Note-Scripts/open_daily_note.sh --split'
        ) == 'Open daily note split'
        assert keybind_sheet.spawn_label(
            'waterfox || flatpak run net.waterfox.waterfox') == 'Waterfox'
        assert keybind_sheet.spawn_label(
            'kitty flatpak run io.missioncenter.MissionCenter') == 'MissionCenter'
        assert keybind_sheet.spawn_label(
            'bash ~/.scripts/Control-Scripts/wm-clipboard-menu.sh') == 'Clipboard menu'
        assert keybind_sheet.spawn_label(
            'xx-wm-ipc lock || loginctl lock-session') == 'Lock'

    def test_default_table_compacts(self):
        sections = dict(keybind_sheet.bind_sections(_default_binds()))
        assert list(sections) == ['Windows', 'Workspaces', 'Shell', 'Apps', 'Media']
        windows = dict((label, keys) for keys, label in sections['Windows'])
        assert windows['Focus'] == 'Super+H/J/K/L  Super+arrows'
        assert windows['Move'] == 'Super+Shift+H/J/K/L  Super+Shift+arrows'
        assert windows['Close'] == 'Super+Q  Super+Shift+Q'
        assert sections['Workspaces'] == [
            ('Super+1…0', 'Go to workspace'),
            ('Super+Shift+1…0', 'Send window'),
            ('Super+Alt+1…0', 'Send window, stay')]
        shell = dict((label, keys) for keys, label in sections['Shell'])
        assert shell == {'Launcher': 'Super  Alt+Tab', 'Top bar': 'Super+R',
                         'Keybinds': 'Super+/', 'Lock': 'Super+`'}

    def test_every_bind_is_shown_once(self):
        binds = _default_binds()
        rows = [r for _s, items in keybind_sheet.bind_sections(binds) for r in items]
        labels = [label for _k, label in rows]
        assert len(labels) == len(set(labels))
        # 8 directional binds per verb, 10 per workspace verb, collapse.
        assert len(rows) < len(binds) - 40

    def test_override_shows_as_written(self):
        binds = [{'combo': 'Super+W', 'action': 'spawn foot'},
                 {'combo': 'Super+H', 'action': 'focus left'}]
        sections = dict(keybind_sheet.bind_sections(binds))
        assert sections['Apps'] == [('Super+W', 'Foot')]
        # Only one direction bound: no compaction, the plain row.
        assert sections['Windows'] == [('Super+H', 'Focus left')]

    def test_bad_entries_are_ignored(self):
        assert keybind_sheet.bind_sections([None, {'combo': 1}, {}]) == []

    @pytest.mark.parametrize('columns', [1, 2, 3])
    def test_columns_keep_order_and_never_end_on_a_caption(self, columns):
        sections = keybind_sheet.bind_sections(_default_binds())
        cols = keybind_sheet.split_columns(sections, columns)
        assert 1 <= len(cols) <= columns
        flat = [item for col in cols for item in col]
        expected = []
        for title, rows in sections:
            expected.append(('caption', title))
            expected += [('row', k, label) for k, label in rows]
        assert flat == expected
        for col in cols:
            assert col and col[-1][0] != 'caption'

    def test_three_columns_balance(self):
        sections = keybind_sheet.bind_sections(_default_binds())
        cols = keybind_sheet.split_columns(sections, 3)
        heights = [sum(2 if i[0] == 'caption' else 1 for i in c) for c in cols]
        assert max(heights) - min(heights) <= 6, heights

    def test_column_count_follows_width(self):
        assert keybind_sheet.column_count(1280) == 3
        assert keybind_sheet.column_count(800) == 2
        assert keybind_sheet.column_count(360) == 1


# ---- launcher -------------------------------------------------------------

def _entry(app_id, name):
    return SimpleNamespace(app_id=app_id, name=name)


class TestLauncherRows:
    def test_nothing_until_typed(self):
        assert desk_launcher.launcher_rows([_entry('a', 'A')], '') == []
        assert desk_launcher.launcher_rows([], '   ') == []

    def test_matches_then_settings_then_web(self):
        calc = _entry('calc.desktop', 'Calculator')
        rows = desk_launcher.launcher_rows([calc], 'calc')
        assert rows == [('app', calc)]
        rows = desk_launcher.launcher_rows([], 'launcher set')
        assert rows[0] == ('settings', None)
        assert rows[-1] == ('web', 'launcher set')
        assert desk_launcher.launcher_rows([], '!news') == [('web', 'news')]
        assert desk_launcher.launcher_rows([], 'zzz') == [('web', 'zzz')]

    def test_labels_use_renames(self):
        row = ('app', _entry('org.gnome.Calculator.desktop', 'Calculator'))
        assert desk_launcher.row_label(
            row, lambda app_id, name: 'Calc') == 'Calc'
        assert desk_launcher.row_label(('settings', None)) == 'Launcher Settings'
        assert desk_launcher.row_label(('web', 'x')) == 'Web: x'

    def test_keys(self):
        move = desk_launcher.key_move
        assert move('Down', False, False, False) == 1
        assert move('Up', False, False, False) == -1
        assert move('Tab', False, False, False) == 1
        assert move('ISO_Left_Tab', False, True, False) == -1
        assert move('j', True, False, False) == 1
        assert move('k', True, False, False) == -1
        # Plain j/k type while the entry has the keys...
        assert move('j', False, False, False) is None
        # ...and move once the list has them.
        assert move('j', False, False, True) == 1
        assert move('k', False, False, True) == -1
        assert move('a', False, False, True) is None

    def test_step_clamps(self):
        assert desk_launcher.step(0, -1, 5) == 0
        assert desk_launcher.step(4, 1, 5) == 4
        assert desk_launcher.step(1, 1, 5) == 2
        assert desk_launcher.step(3, 1, 0) == 0


class _Index:
    def __init__(self, entries):
        self.entries = entries

    def search(self, query):
        q = query.strip().casefold()
        return [e for e in self.entries if q in e.name.casefold()]


class TestSharedSearch:
    def _win(self, labels=None):
        entries = [_entry('org.gnome.Calculator.desktop', 'Calculator'),
                   _entry('kitty.desktop', 'kitty')]
        return SimpleNamespace(
            app_index=_Index(entries),
            config=SimpleNamespace(app_labels=labels or {}))

    def test_rename_matches(self):
        win = self._win({'kitty.desktop': 'Terminal'})
        assert [e.app_id for e in search_results(win, 'term')] == ['kitty.desktop']

    def test_bang_matches_no_app(self):
        assert search_results(self._win(), '!calc') == []

    def test_empty_query_is_everything(self):
        assert len(search_results(self._win(), '')) == 2


# ---- xxwm stream ------------------------------------------------------------

class TestStateLines:
    def test_parse(self):
        state = {'outputs': [MONITOR], 'windows': []}
        assert xxwm_backend.parse_state_line(json.dumps(state)) == ('state', state)
        assert xxwm_backend.parse_state_line('{"shell":"launcher"}') == (
            'shell', 'launcher')
        assert xxwm_backend.parse_state_line('[]') is None
        assert xxwm_backend.parse_state_line('nope') is None
        assert xxwm_backend.parse_state_line('{"shell":""}') is None

    def test_watcher_against_a_socket(self, tmp_path):
        path = str(tmp_path / 'xxwm.sock')
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(1)
        got = {'request': None}
        state = {'outputs': [HANDSET, MONITOR], 'windows': []}

        def serve():
            conn, _ = srv.accept()
            got['request'] = conn.makefile('r').readline().strip()
            conn.sendall((json.dumps(state) + '\n{"shell":"keybinds"}\n'
                          'garbage\n').encode())
            conn.close()
        threading.Thread(target=serve, daemon=True).start()

        states, verbs = [], []
        done = threading.Event()

        def post(fn, value):
            fn(value)
            if verbs:
                done.set()
        watcher = xxwm_backend.XxwmStateWatcher(
            states.append, verbs.append, path=path, post=post)
        watcher.start()
        assert done.wait(5)
        watcher.shutdown()
        srv.close()
        assert got['request'] == 'subscribe state'
        assert states == [state]
        assert verbs == ['keybinds']


# ---- session ------------------------------------------------------------------

class _FakeView:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name.startswith('set_'):
            return lambda *a: self.calls.append((name, a))
        raise AttributeError(name)


class _FakeBar:
    made = []

    def __init__(self, monitor, on_workspace=None):
        self.monitor = monitor
        self.view = _FakeView()
        self.destroyed = False
        _FakeBar.made.append(self)

    def set_application(self, _app):
        pass

    def present(self):
        pass

    def destroy(self):
        self.destroyed = True


class TestDeskSession:
    def _session(self, monkeypatch, locked=False):
        monkeypatch.setattr(desk_bar, 'DeskBar', _FakeBar, raising=False)
        _FakeBar.made = []
        shell = SimpleNamespace(is_locked=lambda: locked,
                                get_application=lambda: None)
        requests = []
        session = desk_session.DeskSession(
            shell, request=lambda line, cb=None: requests.append(line))
        monitors = {'DSI-1': object(), 'HDMI-A-1': object()}
        session._monitor_list = lambda: [
            ('DSI-1', 0, 0, 360, 720, monitors['DSI-1']),
            ('HDMI-A-1', 360, 0, 1280, 720, monitors['HDMI-A-1'])]
        return session, requests, monitors

    def test_bar_on_the_monitor_only(self, monkeypatch):
        session, _req, monitors = self._session(monkeypatch)
        session._on_state(_state([HANDSET, MONITOR], [{'workspace': 1}]))
        assert list(session.bars) == ['HDMI-A-1']
        bar = session.bars['HDMI-A-1']
        assert bar.monitor is monitors['HDMI-A-1']
        assert ('set_workspaces', ([(1, True)],)) in bar.view.calls
        assert ('set_title', ('notes.md - nvim',)) in bar.view.calls

    def test_bar_goes_with_the_monitor_and_comes_back(self, monkeypatch):
        session, _req, _m = self._session(monkeypatch)
        session._on_state(_state([HANDSET, MONITOR]))
        first = session.bars['HDMI-A-1']
        session._on_state(_state([HANDSET]))
        assert session.bars == {} and first.destroyed
        session._on_state(_state([HANDSET, MONITOR]))
        assert session.bars['HDMI-A-1'] is not first

    def test_super_r_toggles(self, monkeypatch):
        session, _req, _m = self._session(monkeypatch)
        session._on_state(_state([HANDSET, MONITOR]))
        session._on_shell('bar')
        assert session.bars == {}
        session._on_shell('bar')
        assert list(session.bars) == ['HDMI-A-1']

    def test_verbs_ignored_while_locked(self, monkeypatch):
        session, _req, _m = self._session(monkeypatch, locked=True)
        session._on_state(_state([HANDSET, MONITOR]))
        session._on_shell('bar')
        assert list(session.bars) == ['HDMI-A-1']

    def test_workspace_click_names_the_output(self, monkeypatch):
        session, requests, _m = self._session(monkeypatch)
        session._on_state(_state([HANDSET, MONITOR]))
        session._show_workspace(session.bars['HDMI-A-1'], 3)
        assert requests == ['workspace 3 HDMI-A-1']

    def test_new_bar_starts_with_the_shell_sources(self, monkeypatch):
        session, _req, _m = self._session(monkeypatch)
        session.set_status({'battery': 50})
        session.set_call('active')
        session.set_notifications(2)
        session._on_state(_state([HANDSET, MONITOR]))
        calls = session.bars['HDMI-A-1'].view.calls
        assert ('set_status', ({'battery': 50},)) in calls
        assert ('set_call', ('active',)) in calls
        assert ('set_notifications', (2,)) in calls

    def test_focused_monitor(self, monkeypatch):
        session, _req, monitors = self._session(monkeypatch)
        session._on_state(_state([HANDSET, MONITOR]))
        assert session.focused_monitor() is monitors['HDMI-A-1']
        session._on_state(_state([dict(HANDSET, focused=True),
                                  dict(MONITOR, focused=False)]))
        assert session.focused_monitor() is monitors['DSI-1']

    def test_binds_answer(self):
        assert desk_session._parse_binds('[{"combo":"Super","action":"launcher"}]')
        assert desk_session._parse_binds('error: x') == []
        assert desk_session._parse_binds(None) == []

    def test_nothing_without_xxwm(self, monkeypatch, tmp_path):
        monkeypatch.setenv('XXWM_SOCKET', str(tmp_path / 'none.sock'))
        assert desk_session.start_desk_session(SimpleNamespace()) is None


class TestWiring:
    def test_new_modules_install(self):
        meson = (REPO / 'launcher' / 'meson.build').read_text()
        for name in ('desk_bar', 'desk_launcher', 'desk_session',
                     'keybind_sheet'):
            assert f"'src/{name}.py'" in meson

    def test_shell_sources_reach_the_desk(self):
        src = REPO / 'launcher' / 'src'
        home = (src / 'home_page.py').read_text()
        assert 'desk.set_clock(' in home and 'desk.set_status(' in home
        assert "'_desk'" in (src / 'call_wiring.py').read_text()
        assert 'on_count_changed' in (src / 'notification_shade.py').read_text()
        assert '_start_desk()' in (src / 'main.py').read_text()


# ---- real widgets ------------------------------------------------------------

_SMOKE = textwrap.dedent('''\
    import os
    import sys

    src_dir, common_dir, run_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    os.environ['GDK_BACKEND'] = 'broadway'
    os.environ['XDG_RUNTIME_DIR'] = run_dir
    sys.path.insert(0, common_dir)
    from xx_testing import broadway_display
    daemon, display = broadway_display.start(run_dir=run_dir)
    os.environ['BROADWAY_DISPLAY'] = display
    try:
        sys.path.insert(0, src_dir)
        import gi
        gi.require_version('Gtk', '4.0')
        from gi.repository import GLib, Gtk
        from types import SimpleNamespace

        failures = []
        sys.excepthook = lambda *exc: failures.append(repr(exc[1]))
        GLib.log_set_always_fatal(GLib.LogLevelFlags.LEVEL_CRITICAL)

        import desk_bar
        import desk_launcher
        import keybind_sheet
        from config import THEME_PRESETS

        provider = Gtk.CssProvider()
        preset = THEME_PRESETS['graphite']
        provider.load_from_data((desk_bar.theme_css(preset)
                                 + desk_launcher.theme_css(preset)
                                 + keybind_sheet.theme_css(preset)).encode())

        view = desk_bar.DeskBarView(on_workspace=lambda n: None)
        view.set_workspaces([(1, True), (3, False)])
        view.set_title('A very long window title ' * 8)
        view.set_clock('Thu, Nov 12', '09:41')
        view.set_status({'battery': 12, 'charging': False, 'ssid': 'home'})
        view.set_flags(True, False)
        view.set_call('active')
        view.set_notifications(4)
        win = Gtk.Window()
        win.set_default_size(1280, 28)
        win.set_child(view)
        win.present()

        entries = [SimpleNamespace(app_id='calc.desktop', name='Calculator'),
                   SimpleNamespace(app_id='kitty.desktop', name='kitty')]

        class Index:
            def __init__(self):
                self.entries = entries

            def search(self, q):
                q = q.strip().casefold()
                return [e for e in entries if q in e.name.casefold()]

        shell = SimpleNamespace(
            app_index=Index(),
            config=SimpleNamespace(app_labels={}, search_auto_launch=False,
                                   label_for=lambda a, n: n),
            focus_state=SimpleNamespace(is_paused_app=lambda a: False))
        launcher = desk_launcher.DeskLauncher(shell)
        launcher._refresh('c')
        assert len(launcher._rows) == 2, launcher._rows
        launcher._selected = 1
        launcher._paint_selection()

        sheet = keybind_sheet.KeybindSheet()
        sheet._fill(keybind_sheet.bind_sections(
            [{'combo': 'Super+Q', 'action': 'close'},
             {'combo': 'Super+W', 'action': 'spawn kitty'}]), 2)

        loop = GLib.MainLoop()
        GLib.timeout_add(1000, loop.quit)
        loop.run()
        if failures:
            print('SMOKE FAIL', failures)
        else:
            print('SMOKE OK')
    finally:
        daemon.terminate()
''')


def test_real_widgets_on_broadway(tmp_path):
    if shutil.which('gtk4-broadwayd') is None:
        pytest.skip('needs gtk4-broadwayd')
    pytest.importorskip('gi')
    run_dir = tmp_path / 'run'
    run_dir.mkdir()
    script = tmp_path / 'desk_smoke.py'
    script.write_text(_SMOKE)
    env = dict(os.environ, XDG_CONFIG_HOME=str(tmp_path / 'config'))
    proc = subprocess.run(
        [sys.executable, str(script), str(REPO / 'launcher' / 'src'),
         str(REPO.parent / 'common'), str(run_dir)],
        capture_output=True, text=True, timeout=120, env=env)
    assert 'SMOKE OK' in proc.stdout, (
        proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:])
