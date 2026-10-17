"""Lane lp_drawer parity tests: web row, normalized search, auto-launch
suppression, persisted drawer_sort, pinned browse, row markers, drawer
close, desktop actions, app-info pages, uninstall planning.

Pure helpers live in app_index/drawer_page/app_item_actions
so they run headless; _populate_apps/_on_stack_swipe/show_app_menu wiring
is driven through bare doubles with stubbed row makers (no display).
"""
import os
import sys
import time
import types
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / 'launcher' / 'src'
sys.path.insert(0, str(SRC))

gi = pytest.importorskip('gi')

import app_index
import drawer_page
from app_index import AppEntry, AppIndex
from drawer_page import DrawerPage


@pytest.fixture(autouse=True)
def _evict_window_modules():
    """`import window` binds real Gtk widget classes; test_folders imports
    window against fake gi and needs a fresh import, so never leak ours."""
    yield
    for name in ('window', 'drawer_page'):
        sys.modules.pop(name, None)


_DRAWER_BROADWAY_SMOKE = '''
import glob
import os
import subprocess
import sys
import time
import types

src_dir, run_dir = sys.argv[1], sys.argv[2]
display = sys.argv[3] if len(sys.argv) > 3 else ':71'
os.environ['GDK_BACKEND'] = 'broadway'
os.environ['BROADWAY_DISPLAY'] = display
home = os.path.join(run_dir, 'home')
os.makedirs(home, exist_ok=True)
os.environ['HOME'] = home
os.environ['XDG_CONFIG_HOME'] = os.path.join(home, '.config')
os.environ['XDG_CACHE_HOME'] = os.path.join(home, '.cache')
os.environ['XDG_DATA_HOME'] = os.path.join(home, '.local', 'share')
os.environ['XDG_RUNTIME_DIR'] = run_dir
port = sys.argv[4] if len(sys.argv) > 4 else '0'
daemon = subprocess.Popen(
    ['gtk4-broadwayd', '--port', port, display],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
    gi.require_version('Adw', '1')
    from gi.repository import Adw, Gtk, GLib

    import window as window_mod
    from app_index import AppEntry

    failures = []
    sys.excepthook = lambda *exc: failures.append(repr(exc[1]))

    def _pump(seconds):
        _loop = GLib.MainLoop()
        GLib.timeout_add(int(seconds * 1000), _loop.quit)
        _loop.run()

    def _wait_until(predicate, timeout_s=10.0):
        _deadline = time.time() + timeout_s
        while time.time() < _deadline:
            _pump(0.05)
            try:
                if predicate():
                    break
            except Exception:
                pass
        else:
            try:
                return bool(predicate())
            except Exception:
                return False
        _pump(0.2)
        try:
            return bool(predicate())
        except Exception:
            return False

    def check(cond, label):
        if not cond:
            failures.append(label)

    def desktop_app(filename, actions=()):
        app = types.SimpleNamespace()
        app.get_filename = lambda: filename
        app.list_actions = lambda: list(actions)
        names = {'new-window': 'New Window'}
        app.get_action_name = lambda action: names.get(action, action)
        launched = []
        app.launch_action = lambda action, ctx=None: launched.append(action)
        app.launched_actions = launched
        return app

    def entry(app_id, name, filename='', actions=()):
        search_text = ' '.join(
            p for p in [name, app_id] if p).casefold()
        return AppEntry(app_id=app_id, name=name, description='',
                        executable='', search_text=search_text,
                        desktop_app=desktop_app(filename, actions))

    new_file = os.path.join(run_dir, 'calc.desktop')
    open(new_file, 'w').write('[Desktop Entry]\\n')
    old_file = os.path.join(run_dir, 'aaa.desktop')
    open(old_file, 'w').write('[Desktop Entry]\\n')
    past = time.time() - 7200
    os.utime(old_file, (past, past))
    cafe_file = os.path.join(run_dir, 'cafe.desktop')
    open(cafe_file, 'w').write('[Desktop Entry]\\n')

    app = Adw.Application(application_id='io.test.LpDrawer')
    win = window_mod.ShellWindow(app)
    win.present()
    win.set_default_size(360, 720)
    if not _wait_until(lambda: win.is_visible()):
        failures.append('drawer smoke: window never became visible')

    calc = entry('calc.desktop', 'Calculator', new_file, ('new-window',))
    win.app_index.entries = [
        entry('aaa.desktop', 'Aaa', old_file),
        calc,
        entry('cafe.desktop', 'Café', cafe_file),
    ]
    win.config.set_pinned(['calc.desktop'])
    win.config.set_drawer_sort('default')

    def labels():
        found = []

        def _walk(widget):
            yield widget
            child = widget.get_first_child()
            while child is not None:
                yield from _walk(child)
                child = child.get_next_sibling()

        for widget in _walk(win.apps_list):
            if isinstance(widget, Gtk.Label):
                found.append(widget.get_text())
        return found

    win.stack.set_visible_child_name('apps')
    win._populate_apps('')
    texts = labels()
    check(any('⊙' in text and 'Calculator' in text for text in texts),
          'pinned marker row, got %r' % (texts,))
    check(any('•' in text and 'Calculator' in text for text in texts),
          'new marker row, got %r' % (texts,))
    check(any(text == 'Aaa' for text in texts),
          'plain row unmarked, got %r' % (texts,))

    win._populate_apps('cafe')
    check(any('Café' in text for text in labels()), 'fold search row')

    win._populate_apps('zzz-no-match')
    check(any(text == 'Web: zzz-no-match' for text in labels()),
          'web row, got %r' % (labels(),))

    win._populate_apps('!hi there')
    texts = labels()
    check(any(text == 'Web: hi there' for text in texts),
          'bang web row, got %r' % (texts,))
    check(not any('Calculator' in text for text in texts), 'bang hides apps')

    win._toggle_sort_mode(None)
    check(win.config.drawer_sort == 'alphabetical', 'toggle 1 -> A-Z')
    check(win._sort_btn.get_label() == 'A-Z', 'toggle 1 label')
    for mode in ('alphabetical_no_priority', 'install_date', 'apk_size',
                 'default'):
        win._toggle_sort_mode(None)
        check(win.config.drawer_sort == mode, 'toggle -> ' + mode)

    helper = window_mod._drawer(win)
    win.stack.set_visible_child_name('apps')
    helper._on_drawer_gap_tap(None, 1, 180.0, 10.0)
    check(win.stack.get_visible_child_name() == 'home', 'gap tap goes home')
    win.stack.set_visible_child_name('apps')

    actions = win.item_actions._build_app_menu_actions(win.apps_list, calc)
    names = [label for label, _thunk in actions]
    check('New Window' in names, 'desktop action row, got %r' % (names,))
    check('Uninstall' in names, 'uninstall row, got %r' % (names,))
    thunk = dict(actions)['New Window']
    thunk()
    check(calc.desktop_app.launched_actions == ['new-window'],
          'desktop action launches')

    _wait_until(lambda: not failures, timeout_s=3.0)
    _pump(0.5)
    check(not failures, 'failures: %r' % (failures,))
    print('DRAWER SMOKE OK')
finally:
    daemon.terminate()
'''


class TestDrawerBroadwaySmoke:
    def test_real_drawer_rows_markers_web_sort_gap(self, tmp_path):
        import shutil
        import socket as _socket
        import subprocess as _subprocess
        if shutil.which('gtk4-broadwayd') is None:
            pytest.skip('needs gtk4-broadwayd')
        pytest.importorskip('gi')
        run_dir = tmp_path / 'broadway'
        run_dir.mkdir()

        def _display_live(number: int) -> bool:
            path = run_dir / f'broadway{number + 1}.socket'
            if not path.exists():
                return False
            try:
                with _socket.socket(
                        _socket.AF_UNIX, _socket.SOCK_STREAM) as sock:
                    sock.settimeout(2)
                    sock.connect(str(path))
            except OSError:
                return False
            return True

        display = next(
            (f':{number}' for number in range(71, 100)
             if not _display_live(number)),
            ':71')
        with _socket.socket(
                _socket.AF_INET, _socket.SOCK_STREAM) as _probe:
            _probe.bind(('127.0.0.1', 0))
            port = str(_probe.getsockname()[1])
        script = tmp_path / 'drawer_broadway_smoke.py'
        script.write_text(_DRAWER_BROADWAY_SMOKE)
        proc = _subprocess.run(
            [sys.executable, str(script), str(SRC), str(run_dir), display,
             port],
            capture_output=True, text=True, timeout=180)
        assert 'DRAWER SMOKE OK' in proc.stdout, (
            proc.returncode, proc.stdout[-3000:], proc.stderr[-3000:])
        noise = ('layer_shell_global', 'startup signal has been emitted',
                 'frame_timings_submitted')
        criticals = [line for line in proc.stderr.splitlines()
                     if 'CRITICAL' in line
                     and not any(mark in line for mark in noise)]
        assert not criticals, '\n'.join(criticals)


def _desktop_app(filename='', actions=()):
    app = types.SimpleNamespace()
    app._filename = filename
    app._actions = {action: action.title().replace('-', ' ')
                    for action in actions}
    app.get_filename = lambda: app._filename
    app.list_actions = lambda: [a for a in actions]
    app.get_action_name = lambda action: app._actions.get(action, action)
    launched = []
    app.launch_action = lambda action, ctx=None: launched.append(action)
    app.launched_actions = launched
    return app


def _entry(app_id, name, description='', keywords='',
           filename='', actions=()):
    search_text = ' '.join(
        p for p in [name, description, keywords, app_id] if p).casefold()
    return AppEntry(app_id=app_id, name=name, description=description,
                    executable='', search_text=search_text,
                    desktop_app=_desktop_app(filename, actions))


@pytest.fixture
def index():
    idx = AppIndex()
    idx.entries = sorted([
        _entry('cafe.desktop', 'Café', 'Coffee place'),
        _entry('eink.desktop', 'E-Ink Reader'),
        _entry('calc.desktop', 'Calculator', 'Do math'),
        _entry('files.desktop', 'Files', 'Browse documents'),
    ], key=lambda e: e.name.casefold())
    return idx


class TestFoldForSearch:
    def test_accent_folds(self):
        assert drawer_page.fold_for_search('Café') == 'cafe'

    def test_separators_drop(self):
        assert drawer_page.fold_for_search('E-Ink') == 'eink'
        assert drawer_page.fold_for_search('a_b+c,d.e f') == 'abcdef'

    def test_app_index_exposes_same_fold(self):
        assert app_index.fold_for_search('Café') == 'cafe'


class TestAccentInsensitiveSearch:
    def test_cafe_finds_cafe(self, index):
        assert [e.name for e in index.search('cafe')] == ['Café']

    def test_eink_finds_e_ink(self, index):
        assert [e.name for e in index.search('eink')] == ['E-Ink Reader']

    def test_separator_query_matches_plain_name(self, index):
        assert [e.name for e in index.search('e-ink')] == ['E-Ink Reader']

    def test_plain_search_still_works(self, index):
        assert [e.name for e in index.search('calc')] == ['Calculator']
        assert index.search('zzz') == []


class TestWebRowQuery:
    def test_no_results_shows_web_row(self):
        assert drawer_page.web_search_row_query('zzz', 0) == 'zzz'

    def test_results_hide_web_row(self):
        assert drawer_page.web_search_row_query('calc', 2) is None

    def test_bang_query_shows_web_row(self):
        assert drawer_page.web_search_row_query('!cats', 0) == 'cats'

    def test_bare_bang_shows_no_row(self):
        assert drawer_page.web_search_row_query('!', 0) is None

    def test_blank_query_shows_no_row(self):
        assert drawer_page.web_search_row_query('  ', 0) is None


class TestShouldAutoLaunch:
    def test_single_result_launches(self):
        assert drawer_page.should_auto_launch('calc', 1, False, True) is True

    def test_disabled_never_launches(self):
        assert drawer_page.should_auto_launch('calc', 1, False, False) is False

    def test_leading_space_suppresses(self):
        assert drawer_page.should_auto_launch(' calc', 1, False, True) is False

    def test_settings_row_match_suppresses(self):
        assert drawer_page.should_auto_launch('sett', 1, True, True) is False

    def test_bang_never_launches(self):
        assert drawer_page.should_auto_launch('!cats', 1, False, True) is False

    def test_zero_or_many_results_never_launch(self):
        assert drawer_page.should_auto_launch('c', 0, False, True) is False
        assert drawer_page.should_auto_launch('c', 3, False, True) is False

    def test_settings_row_visibility(self):
        assert drawer_page.settings_row_visible('') is True
        assert drawer_page.settings_row_visible('launcher settings') is True
        assert drawer_page.settings_row_visible('sett') is True
        assert drawer_page.settings_row_visible('calc') is False


class TestDrawerSortConfig:
    def test_default_is_default(self, tmp_path, monkeypatch):
        from config import ShellConfig
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))
        assert ShellConfig().drawer_sort == 'default'

    def test_setter_persists_valid_modes(self, tmp_path, monkeypatch):
        from config import ShellConfig
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))
        cfg = ShellConfig()
        for mode in ('default', 'alphabetical', 'alphabetical_no_priority',
                     'install_date', 'apk_size'):
            cfg.set_drawer_sort(mode)
            assert cfg.drawer_sort == mode
        assert ShellConfig().drawer_sort == 'apk_size'

    def test_legacy_modes_migrate_to_android_names(self, tmp_path,
                                                   monkeypatch):
        import json
        from config import ShellConfig
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))
        cfg = ShellConfig()
        cfg.set_drawer_sort('az')
        assert cfg.drawer_sort == 'alphabetical_no_priority'
        path = tmp_path / 'home' / '.config' / 'xx-wm' / 'config.json'
        data = json.loads(path.read_text())
        data['drawer_sort'] = 'install'
        path.write_text(json.dumps(data))
        assert ShellConfig().drawer_sort == 'install_date'

    def test_setter_rejects_unknown(self, tmp_path, monkeypatch):
        from config import ShellConfig
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))
        cfg = ShellConfig()
        cfg.set_drawer_sort('bogus')
        assert cfg.drawer_sort == 'default'

    def test_next_cycles_the_android_modes(self):
        order = ['default', 'alphabetical', 'alphabetical_no_priority',
                 'install_date', 'apk_size']
        for here, there in zip(order, order[1:] + order[:1]):
            assert drawer_page.next_drawer_sort(here) == there
        assert drawer_page.next_drawer_sort('az') == 'install_date'

    def test_sort_browse_list_matches_android(self):
        from types import SimpleNamespace as E
        apps = [E(app_id=a) for a in ('a', 'b', 'c', 'd')]
        sizes = {'a': 1, 'b': 9, 'c': 5, 'd': 7}
        installed = {'a': 4, 'b': 1, 'c': 3, 'd': 2}
        pins = ['d', 'b']
        ids = lambda rows: [r.app_id for r in rows]  # noqa: E731
        sort = drawer_page.sort_browse_list
        assert ids(sort(apps, 'default', pins)) == ['d', 'b', 'a', 'c']
        assert ids(sort(apps, 'alphabetical', pins)) == ['b', 'd', 'a', 'c']
        assert ids(sort(apps, 'alphabetical_no_priority', pins)) == [
            'a', 'b', 'c', 'd']
        assert ids(sort(apps, 'install_date', pins,
                        install_time=lambda e: installed[e.app_id])) == [
            'd', 'b', 'a', 'c']
        assert ids(sort(apps, 'apk_size', [],
                        size_of=lambda e: sizes[e.app_id])) == [
            'b', 'd', 'c', 'a']

    def test_backup_round_trip(self, tmp_path, monkeypatch):
        from config import ShellConfig
        import backup
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))
        cfg = ShellConfig()
        cfg.set_drawer_sort('install_date')
        exported = backup.export_backup(cfg)
        assert exported['drawer_sort'] == 'install_date'
        valid, _ = backup.validate_backup(exported)
        assert valid is True
        # A backup written before the Android names still restores.
        old = dict(exported, drawer_sort='az')
        assert backup.validate_backup(old)[0] is True
        bad = dict(exported, drawer_sort='bogus')
        valid, _ = backup.validate_backup(bad)
        assert valid is False


class _FakeListBox:
    def __init__(self):
        self.rows = []

    def insert(self, row, _pos):
        self.rows.append(('insert', row))

    def append(self, row):
        self.rows.append(('append', row))

    def set_valign(self, _align):
        pass


def _populate_win(entries, monkeypatch, query='', pinned=(),
                  hidden=(), home_ids=(), sort='default', labels=None):
    """Bare window double driving DrawerPage._populate_apps headless."""
    markers = []

    class Win:
        pass

    win = Win()
    idx = AppIndex()
    idx.entries = sorted(list(entries), key=lambda e: e.name.casefold())
    win._install_time = lambda entry: 0.0
    win._scroll_apps_to_top = lambda: False
    win.app_index = idx
    win.config = types.SimpleNamespace(
        app_labels=dict(labels or {}), hidden_apps=list(hidden),
        pinned=list(pinned), drawer_sort=sort,
        label_for=lambda app_id, fallback: dict(labels or {}).get(
            app_id, fallback))
    win._home_slot_app_ids = lambda: set(home_ids)
    win._drawer_folder_slots = lambda: []
    win._drawer_open_folder = None
    win._drawer_open_hidden = False
    win._alpha_letter_rows = {}
    win._last_search_results = []
    win.apps_search = types.SimpleNamespace(
        get_text=lambda: query, set_text=lambda _t: None)
    win.apps_list = _FakeListBox()
    win.apps_scroller = types.SimpleNamespace(
        get_vadjustment=lambda: types.SimpleNamespace(
            set_value=lambda _v: None, get_value=lambda: 0.0),
        get_height=lambda: 600)
    win.app_count_label = types.SimpleNamespace(set_text=lambda _t: None)
    win.focus_state = types.SimpleNamespace(is_paused_app=lambda _a: False)
    page = DrawerPage(win)
    made = {}

    def _record(kind):
        def _make(entry=None):
            markers.append((kind, getattr(entry, 'app_id', entry)))
            return ('row', kind, getattr(entry, 'app_id', entry))
        return _make

    win._replace_rows = lambda box, found, _msg, maker: [
        markers.append(('app', e.app_id)) or box.append(('row', e.app_id))
        for e in found]
    win._make_app_row = _record('app')
    win._make_settings_row = lambda: markers.append(
        ('settings', None)) or ('row', 'settings')
    win._make_web_row = lambda q: markers.append(
        ('web', q)) or ('row', 'web', q)
    made['markers'] = markers
    return win, page, made


class TestPopulateApps:
    def test_web_row_appended_when_no_match(self, monkeypatch):
        entries = [_entry('calc.desktop', 'Calculator')]
        win, page, made = _populate_win(entries, monkeypatch, query='zzz')
        page._populate_apps('zzz')
        assert ('web', 'zzz') in made['markers']

    def test_bang_query_shows_only_web_row(self, monkeypatch):
        entries = [_entry('calc.desktop', 'Calculator')]
        win, page, made = _populate_win(entries, monkeypatch, query='!cats')
        page._populate_apps('!cats')
        kinds = [kind for kind, _ in made['markers']]
        assert ('web', 'cats') in made['markers']
        assert 'app' not in kinds

    def test_pinned_stays_in_browse_from_home(self, monkeypatch):
        entries = [_entry('calc.desktop', 'Calculator'),
                   _entry('phone.desktop', 'Phone')]
        win, page, made = _populate_win(
            entries, monkeypatch, pinned=['calc.desktop'],
            home_ids={'calc.desktop', 'phone.desktop'})
        page._populate_apps('')
        shown = [app_id for kind, app_id in made['markers']
                 if kind == 'app']
        assert 'calc.desktop' in shown
        assert 'phone.desktop' not in shown

    def test_az_has_no_pin_priority(self, monkeypatch):
        entries = [_entry('calc.desktop', 'Calculator'),
                   _entry('aaa.desktop', 'Aaa')]
        win, page, made = _populate_win(
            entries, monkeypatch, pinned=['calc.desktop'], sort='az')
        page._populate_apps('')
        shown = [app_id for kind, app_id in made['markers']
                 if kind == 'app']
        assert shown == ['aaa.desktop', 'calc.desktop']

    def test_default_sorts_pinned_first(self, monkeypatch):
        entries = [_entry('calc.desktop', 'Calculator'),
                   _entry('aaa.desktop', 'Aaa')]
        win, page, made = _populate_win(
            entries, monkeypatch, pinned=['calc.desktop'], sort='default')
        page._populate_apps('')
        shown = [app_id for kind, app_id in made['markers']
                 if kind == 'app']
        assert shown == ['calc.desktop', 'aaa.desktop']

    def test_renamed_label_match_is_fold_insensitive(self, monkeypatch):
        entries = [_entry('cafe.desktop', 'Café')]
        win, page, made = _populate_win(
            entries, monkeypatch, query='kaffee',
            labels={'cafe.desktop': 'Kaffee'})
        page._populate_apps('kaffee')
        assert ('app', 'cafe.desktop') in made['markers']


class TestRowMarkers:
    def test_pinned_and_new_marks(self):
        title = drawer_page.row_title('Calc', pinned=True, is_new=True)
        assert '⊙' in title and '•' in title
        assert title.startswith('Calc')

    def test_plain_row_has_no_marks(self):
        assert drawer_page.row_title('Calc', pinned=False,
                                     is_new=False) == 'Calc'

    def test_accessible_label_carries_facts(self):
        label = drawer_page.accessible_label(
            'Calc', pinned=True, is_new=True)
        assert 'pinned' in label and 'new app' in label

    def test_is_new_uses_install_mtime(self, tmp_path):
        target = tmp_path / 'new.desktop'
        target.write_text('x')
        entry = _entry('new.desktop', 'New', filename=str(target))
        assert drawer_page.app_is_new(
            entry, now=time.time()) is True
        old = tmp_path / 'old.desktop'
        old.write_text('x')
        past = time.time() - 7200
        os.utime(old, (past, past))
        old_entry = _entry('old.desktop', 'Old', filename=str(old))
        assert drawer_page.app_is_new(
            old_entry, now=time.time()) is False

    def test_gap_fraction_is_twelve_percent(self):
        assert drawer_page.SHEET_GAP_FRACTION == pytest.approx(0.12)

    def test_gap_tap_closes_only_in_gap(self):
        assert drawer_page.gap_tap_closes(10.0, 720.0) is True
        assert drawer_page.gap_tap_closes(700.0, 720.0) is False


class TestDrawerSwipeDown:
    def test_decision_table(self):
        decide = drawer_page.decide_drawer_swipe_down
        assert decide(True, True) == 'shade'
        assert decide(True, False) == 'shade'
        assert decide(False, True) == 'home'
        assert decide(False, False) == 'shade'

    def _swipe_ns(self, window_mod, origin, value=0.0):
        import types as _types

        class Stack:
            def __init__(self):
                self.page = 'apps'

            def get_visible_child_name(self):
                return self.page

            def set_visible_child_name(self, name):
                self.page = name

        actions = []
        ns = _types.SimpleNamespace(
            stack=Stack(),
            gesture_config={'swipe_down_top': 'notification_shade'},
            _swipe_origin=origin,
            apps_scroller=_types.SimpleNamespace(
                get_vadjustment=lambda: _types.SimpleNamespace(
                    get_value=lambda: value)),
            get_height=lambda: 720,
            get_width=lambda: 360,
            go_home=lambda: actions.append('home'),
            _dispatch_gesture_action=actions.append,
        )
        ns._drawer_swipe_down_home = _types.MethodType(
            window_mod.ShellWindow._drawer_swipe_down_home, ns)
        return ns, actions

    def test_swipe_down_at_top_goes_home(self, monkeypatch):
        import window as window_mod
        ns, actions = self._swipe_ns(window_mod, (180.0, 400.0))
        window_mod.ShellWindow._on_stack_swipe(ns, None, 0.0, 400.0)
        assert actions == ['home']

    def test_swipe_down_from_top_edge_opens_shade(self, monkeypatch):
        import window as window_mod
        ns, actions = self._swipe_ns(window_mod, (180.0, 4.0))
        window_mod.ShellWindow._on_stack_swipe(ns, None, 0.0, 400.0)
        assert actions == ['notification_shade']

    def test_swipe_down_mid_list_opens_shade(self, monkeypatch):
        import window as window_mod
        ns, actions = self._swipe_ns(window_mod, (180.0, 400.0), value=250.0)
        window_mod.ShellWindow._on_stack_swipe(ns, None, 0.0, 400.0)
        assert actions == ['notification_shade']


class TestDesktopActions:
    def test_lists_localized_actions(self):
        import app_item_actions
        entry = _entry('edit.desktop', 'Editor',
                       actions=('new-window', 'open-file'))
        items = app_item_actions.AppItemActions.desktop_action_items(entry)
        assert items == [('New Window', 'new-window'),
                         ('Open File', 'open-file')]

    def test_no_actions_is_empty(self):
        import app_item_actions
        assert app_item_actions.AppItemActions.desktop_action_items(
            _entry('plain.desktop', 'Plain')) == []

    def test_launch_action_delegates(self):
        import app_item_actions
        entry = _entry('edit.desktop', 'Editor', actions=('new-window',))
        assert app_item_actions.AppItemActions.launch_desktop_action(
            entry, 'new-window') is True
        assert entry.desktop_app.launched_actions == ['new-window']

    def test_menu_lists_desktop_actions(self):
        import app_item_actions
        entry = _entry('edit.desktop', 'Editor', actions=('new-window',))
        config = types.SimpleNamespace(
            hidden_apps=[], pinned=[],
            label_for=lambda app_id, fallback: fallback)
        act = app_item_actions.AppItemActions(
            config, on_changed=lambda: None, on_status=lambda _m: None)
        labels = [label for label, _ in
                  act._build_app_menu_actions(None, entry)]
        assert 'New Window' in labels
        assert labels.index('New Window') < labels.index('Rename')


class TestAppInfoActions:
    def test_info_buttons_cover_pages(self):
        import app_item_actions
        entry = _entry('edit.desktop', 'Editor')
        config = types.SimpleNamespace(
            hidden_apps=[], pinned=[],
            label_for=lambda app_id, fallback: fallback)
        focus = types.SimpleNamespace(is_focus_app=lambda _a: False)
        act = app_item_actions.AppItemActions(
            config, on_changed=lambda: None, on_status=lambda _m: None,
            focus_state=focus)
        pages = dict(act.info_page_items(entry))
        assert set(pages.values()) >= {'permissions', 'network access',
                                       'standby exemption',
                                       'screen-time limit', 'disable'}
        assert 'focus' in ' '.join(pages.values())

    def test_open_routes_each_page(self):
        import app_item_actions
        entry = _entry('edit.desktop', 'Editor')
        calls = []
        focus = types.SimpleNamespace(
            is_focus_app=lambda _a: False,
            set_app_focused=lambda _a, _f: calls.append(('focus', _f)))
        win = types.SimpleNamespace(
            open_settings=calls.append,
            focus_state=focus,
            config=types.SimpleNamespace(
                standby_exempt=[],
                set_standby_exempt=lambda ids: calls.append(
                    ('exempt', list(ids)))),
            _settings_page=types.SimpleNamespace(
                section=lambda _k: types.SimpleNamespace(
                    _open_app_view=lambda _w, app: calls.append(
                        ('perm-view', app)),
                    _select=lambda _w, app: calls.append(
                        ('select', app)),
                    _render_exempt=lambda _w: None)),
        )
        for page in ('permissions', 'network access', 'standby exemption',
                     'screen-time limit'):
            app_item_actions.open_app_page(win, page, entry)
        assert 'permissions' in calls
        assert ('perm-view', 'edit.desktop') in calls
        assert ('exempt', ['edit.desktop']) in calls
        assert ('select', 'edit.desktop') in calls
