"""Tests for screen capture: screenshots, recording, scrolling stitches.

Screenshot/record flows run with inline workers and fake compositors;
the stitcher runs on synthetic frames; the virtual-pointer XML order is
pinned (request opcodes come from declaration order).
"""
import datetime
import os
import signal
import sys
import types
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import capture  # noqa: E402
import capture_stitch  # noqa: E402
import display_manager  # noqa: E402
import wayland_bindings  # noqa: E402

DATA_DIR = Path(__file__).parent.parent / 'launcher' / 'data'


@pytest.fixture
def inline(monkeypatch):
    """Run workers and main-loop posts synchronously."""
    monkeypatch.setattr(capture, '_spawn_worker', lambda target: target())
    monkeypatch.setattr(capture, '_post_to_main',
                        lambda callback, *args: callback(*args))


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    return tmp_path


class _FakeShade:
    def __init__(self):
        self.added = []
        self.dismissed = []

    def add_notification(self, notif_id, app_name, summary, body='',
                         desktop_entry='', actions=None, hints=None):
        normalized = []
        for item in actions or []:
            if len(item) == 3:
                normalized.append((item[0], item[1], item[2]))
            elif callable(item[1]):
                normalized.append((item[0], item[0], item[1]))
            else:
                normalized.append((item[0], item[1], None))
        self.added.append({
            'id': notif_id, 'app': app_name, 'summary': summary,
            'body': body, 'actions': normalized,
        })

    def dismiss(self, notif_id, notify_daemon=True):
        self.dismissed.append(notif_id)


@pytest.fixture
def shade(monkeypatch):
    fake = _FakeShade()
    shell = types.SimpleNamespace(_shade=fake)
    monkeypatch.setattr(capture, '_shell_provider', lambda: shell)
    return fake


def _action_map(entry):
    return {key: label for key, label, _cb in entry['actions']}


def _action_cbs(entry):
    return {key: cb for key, _label, cb in entry['actions']}


# --- filename scheme --------------------------------------------------------


class TestFilenameScheme:
    def test_screenshot_name(self):
        moment = datetime.datetime(2026, 10, 16, 12, 34, 56)
        assert (capture.screenshot_filename(moment)
                == 'Screenshot_2026-10-16_12-34-56.png')

    def test_recording_name(self):
        moment = datetime.datetime(2026, 10, 16, 12, 34, 56)
        assert (capture.recording_filename(moment)
                == 'Screenrecording_2026-10-16_12-34-56.mp4')

    def test_recording_elapsed_format(self):
        assert capture.format_recording_text(12) == 'Recording… 00:12'
        assert capture.format_recording_text(75) == 'Recording… 01:15'
        assert capture.format_recording_text(0) == 'Recording… 00:00'


# --- screenshot flow --------------------------------------------------------


class TestScreenshotFlow:
    def test_saves_and_notifies_with_actions(
            self, inline, tmp_home, shade, monkeypatch):
        seen = []

        def _grim(path):
            seen.append(path)
            Path(path).touch()
            return True

        monkeypatch.setattr(capture, '_run_grim', _grim)
        monkeypatch.setattr(capture, 'grim_available', lambda: True)
        monkeypatch.setattr(capture, 'flash', lambda: seen.append('flash'))
        capture.take_screenshot()

        shots = [p for p in seen if p != 'flash']
        assert len(shots) == 1
        assert shots[0].startswith(
            os.path.join(str(tmp_home), 'Pictures', 'Screenshots',
                         'Screenshot_'))
        assert shots[0].endswith('.png')
        assert 'flash' in seen
        assert len(shade.added) == 1
        entry = shade.added[0]
        assert entry['id'] == capture.SCREENSHOT_NOTIF_ID
        assert entry['summary'] == 'Screenshot saved'
        assert (set(_action_map(entry))
                == {'Share', 'Delete', 'Capture more'})

    def test_grim_failure_notifies_nothing(
            self, inline, tmp_home, shade, monkeypatch):
        monkeypatch.setattr(capture, '_run_grim', lambda path: False)
        monkeypatch.setattr(capture, 'grim_available', lambda: True)
        capture.take_screenshot()
        assert shade.added == []

    def test_grim_absent_is_silent(self, inline, shade, monkeypatch):
        monkeypatch.setattr(capture, 'grim_available', lambda: False)
        ran = []
        monkeypatch.setattr(capture, '_run_grim',
                            lambda path: ran.append(path) or True)
        capture.take_screenshot()
        assert ran == []
        assert shade.added == []

    def test_display_manager_delegates(self, monkeypatch):
        calls = []
        monkeypatch.setattr(capture, 'take_screenshot',
                            lambda: calls.append(True))
        display_manager._take_screenshot()
        assert calls == [True]

    def test_share_uses_portal_openuri_ask(self, tmp_path, monkeypatch):
        import capture as cap

        class _Variant:
            def __init__(self, *args):
                self.args = args

            def unpack(self):
                return _deep(self.args[1])

        def _deep(value):
            if isinstance(value, _Variant):
                return _deep(value.args[1])
            if isinstance(value, tuple):
                return tuple(_deep(v) for v in value)
            if isinstance(value, dict):
                return {k: _deep(v) for k, v in value.items()}
            return value

        calls = []

        class _Bus:
            def call(self, *args):
                calls.append(args)

        glib = types.SimpleNamespace(Variant=_Variant)
        gio = types.SimpleNamespace(
            BusType=types.SimpleNamespace(SESSION='session'),
            DBusCallFlags=types.SimpleNamespace(NONE=0),
            bus_get_sync=lambda *a: _Bus(),
        )
        monkeypatch.setattr(cap, 'GLib', glib)
        monkeypatch.setattr(cap, 'Gio', gio)
        target = tmp_path / 'Screenshot_2026-10-16_12-34-56.png'
        target.touch()
        assert cap.share_file(str(target), bus=_Bus()) is True
        assert len(calls) == 1
        (bus_name, obj_path, iface, method,
         params, _rtype, _flags, _timeout, _cancel, _cb) = calls[0]
        assert bus_name == 'org.freedesktop.portal.Desktop'
        assert obj_path == '/org/freedesktop/portal/desktop'
        assert iface == 'org.freedesktop.portal.OpenURI'
        assert method == 'OpenURI'
        parent, uri, options = params.unpack()
        assert parent == ''
        assert uri == target.absolute().as_uri()
        assert options == {'ask': True}

    def test_delete_removes_file_and_dismisses(
            self, inline, tmp_home, shade):
        target = tmp_home / 'shot.png'
        target.touch()
        capture.delete_file(str(target), capture.SCREENSHOT_NOTIF_ID)
        assert not target.exists()
        assert shade.dismissed == [capture.SCREENSHOT_NOTIF_ID]

    def test_flash_constants(self):
        assert capture._FLASH_MS == 200
        assert capture._FLASH_OPACITY == pytest.approx(0.3)

    def test_countdown_labels(self):
        assert capture.countdown_labels() == ['3', '2', '1']


class TestScreenshotWhileBlanked:
    def test_blanked_skips_grim_with_screen_off_notice(
            self, inline, tmp_home, shade, monkeypatch):
        ran = []
        monkeypatch.setattr(capture, '_run_grim',
                            lambda path: ran.append(path) or True)
        monkeypatch.setattr(capture, 'grim_available', lambda: True)
        capture.set_blanked(True)
        try:
            capture.take_screenshot()
        finally:
            capture.set_blanked(False)
        assert ran == []
        assert len(shade.added) == 1
        assert shade.added[0]['summary'] == 'Screen is off'

    def test_grim_runs_with_a_timeout(self, monkeypatch):
        import subprocess

        seen = {}

        def _boom(*args, **kwargs):
            seen.update(kwargs)
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=10)

        monkeypatch.setattr(subprocess, 'run', _boom)
        assert capture._run_grim('/tmp/shot.png') is False
        assert 'timeout' in seen


# --- recorder command + lifecycle -------------------------------------------


class _FakeProc:
    def __init__(self, cmd, **kwargs):
        self.cmd = list(cmd)
        self.kwargs = kwargs
        self.signals = []
        self.waited = None
        self.killed = False
        self.returncode = None

    def poll(self):
        return self.returncode

    def send_signal(self, sig):
        self.signals.append(sig)

    def wait(self, timeout=None):
        self.waited = timeout
        return 0

    def kill(self):
        self.killed = True

    def terminate(self):
        pass


class TestRecorderCommand:
    def test_defaults_leave_choice_to_wf_recorder(self):
        cmd = capture.build_recorder_command('/tmp/out.mp4')
        assert cmd == ['wf-recorder', '-f', '/tmp/out.mp4']

    def test_h264_flags_only_when_encoder_exists(self):
        cmd = capture.build_recorder_command(
            '/tmp/out.mp4', has_h264=True)
        assert cmd == ['wf-recorder', '-f', '/tmp/out.mp4',
                       '-c', 'libx264', '-p', 'preset=ultrafast']

    def test_audio_only_when_enabled(self):
        assert '--audio' not in capture.build_recorder_command('/t.mp4')
        assert '--audio' in capture.build_recorder_command(
            '/t.mp4', record_audio=True)

    def test_full_command(self):
        assert capture.build_recorder_command(
            '/t.mp4', record_audio=True, has_h264=True) == [
            'wf-recorder', '-f', '/t.mp4',
            '-c', 'libx264', '-p', 'preset=ultrafast', '--audio']


def _recorder(monkeypatch, **kwargs):
    rec = capture.ScreenRecorder(**kwargs)
    procs = []
    monkeypatch.setattr(capture, '_spawn_worker', lambda target: target())
    monkeypatch.setattr(capture, '_post_to_main',
                        lambda callback, *args: callback(*args))
    monkeypatch.setattr(capture, 'recorder_available', lambda: True)
    monkeypatch.setattr(capture, 'probe_h264', lambda: False)
    monkeypatch.setattr(capture, '_record_audio_enabled', lambda: False)
    monkeypatch.setattr(capture, 'show_countdown', lambda done: done())

    def _fake_spawn(cmd, **kw):
        proc = _FakeProc(cmd, **kw)
        procs.append(proc)
        return proc

    rec._spawn = _fake_spawn
    glib = types.SimpleNamespace(
        timeout_add_seconds=lambda *a, **k: 7,
        source_remove=lambda *a, **k: None,
    )
    monkeypatch.setattr(capture, 'GLib', glib)
    return rec, procs


class TestRecorderLifecycle:
    def test_start_posts_persistent_notification_with_stop(
            self, tmp_home, shade, monkeypatch):
        rec, procs = _recorder(monkeypatch)
        assert rec.start() is True
        assert len(procs) == 1
        out = procs[0].cmd[2]
        assert procs[0].cmd[:2] == ['wf-recorder', '-f']
        assert out.startswith(os.path.join(
            str(tmp_home), 'Videos', 'Screenrecordings',
            'Screenrecording_'))
        assert out.endswith('.mp4')
        assert rec.is_recording()
        assert len(shade.added) == 1
        entry = shade.added[0]
        assert entry['id'] == capture.RECORDING_NOTIF_ID
        assert entry['summary'] == 'Recording… 00:00'
        assert set(_action_map(entry)) == {'Stop'}

    def test_start_refuses_second_session(self, shade, monkeypatch):
        rec, _procs = _recorder(monkeypatch)
        assert rec.start() is True
        assert rec.start() is False

    def test_start_hidden_without_tool(self, shade, monkeypatch):
        rec, procs = _recorder(monkeypatch)
        monkeypatch.setattr(capture, 'recorder_available', lambda: False)
        assert rec.start() is False
        assert procs == []

    def test_stop_sends_sigint_and_reaps_in_worker(
            self, shade, monkeypatch):
        rec, procs = _recorder(monkeypatch)
        rec.start()
        assert rec.stop() is True
        assert procs[0].signals == [signal.SIGINT]
        assert procs[0].waited == 10
        assert not rec.is_recording()
        assert shade.dismissed == [capture.RECORDING_NOTIF_ID]
        assert rec.stop() is False

    def test_stop_action_fires_from_notification(
            self, shade, monkeypatch):
        rec, procs = _recorder(monkeypatch)
        rec.start()
        cbs = _action_cbs(shade.added[0])
        cbs['Stop']()
        assert procs[0].signals == [signal.SIGINT]
        assert not rec.is_recording()


class TestRecorderLifetime:
    def test_spawned_on_main_thread_tied_to_shell(self, shade, monkeypatch):
        rec, procs = _recorder(monkeypatch)
        posted = []
        monkeypatch.setattr(capture, '_post_to_main',
                            lambda cb, *args: posted.append((cb, args)))
        rec.start()
        assert procs == []
        for cb, args in posted:
            cb(*args)
        assert len(procs) == 1
        assert procs[0].kwargs['preexec_fn'] is capture._die_with_shell
        assert rec.is_recording()

    def test_recorder_exit_ends_the_session(self, shade, monkeypatch):
        rec, procs = _recorder(monkeypatch, blank_fn=lambda: False,
                               battery_fn=lambda: 50)
        rec.start()
        procs[0].returncode = 1
        assert rec._on_tick() is False
        assert not rec.is_recording()
        assert shade.dismissed == [capture.RECORDING_NOTIF_ID]

    def test_low_space_stops_recording(self, shade, monkeypatch):
        rec, procs = _recorder(monkeypatch, blank_fn=lambda: False,
                               battery_fn=lambda: 50)
        rec.start()
        monkeypatch.setattr(capture, 'free_bytes', lambda path: 10 << 20)
        assert rec.check_auto_stop() is True
        assert procs[0].signals == [signal.SIGINT]

    def test_low_space_refuses_start(self, shade, monkeypatch):
        rec, procs = _recorder(monkeypatch)
        monkeypatch.setattr(capture, 'free_bytes', lambda path: 10 << 20)
        rec.start()
        assert procs == []
        assert not rec.is_recording()

    def test_scrolling_capture_hides_shade_and_drops_layer(self, monkeypatch):
        calls = []
        shell = types.SimpleNamespace(
            hide_shade=lambda: calls.append('shade'),
            drop_to_background=lambda: calls.append('drop'))
        monkeypatch.setattr(capture, '_shell_provider', lambda: shell)
        capture._hide_shell_surfaces()
        assert calls == ['shade', 'drop']


class TestRecorderAutoStop:
    @pytest.mark.parametrize('blanked,pct,expected', [
        (True, 80, True),
        (True, None, True),
        (False, 5, True),
        (False, 0, True),
        (False, 6, False),
        (False, 50, False),
        (False, None, False),
    ])
    def test_should_auto_stop_matrix(self, blanked, pct, expected):
        assert capture.should_auto_stop(blanked, pct) is expected

    def test_blank_stops_recording(self, shade, monkeypatch):
        rec, procs = _recorder(
            monkeypatch, blank_fn=lambda: True,
            battery_fn=lambda: 80)
        rec.start()
        assert rec.check_auto_stop() is True
        assert procs[0].signals == [signal.SIGINT]

    def test_low_battery_stops_recording(self, shade, monkeypatch):
        rec, procs = _recorder(
            monkeypatch, blank_fn=lambda: False,
            battery_fn=lambda: 5)
        rec.start()
        assert rec.check_auto_stop() is True
        assert procs[0].signals == [signal.SIGINT]

    def test_healthy_session_continues(self, shade, monkeypatch):
        rec, procs = _recorder(
            monkeypatch, blank_fn=lambda: False,
            battery_fn=lambda: 50)
        rec.start()
        assert rec.check_auto_stop() is False
        assert procs[0].signals == []


# --- Record tile gating ------------------------------------------------------


def _bare_panel():
    import quick_actions
    panel = quick_actions.QuickActionsPanel.__new__(
        quick_actions.QuickActionsPanel)
    panel._dnd = None
    panel._focus = None
    panel._hud = None
    panel._als = types.SimpleNamespace(available=lambda: False)
    panel._location = types.SimpleNamespace(
        available=lambda: False, is_active=lambda: False,
        set_enabled=lambda v: None)
    return panel


class TestRecordTile:
    def test_tile_hidden_without_wf_recorder(self, monkeypatch):
        import quick_actions
        monkeypatch.setattr(quick_actions, '_recorder_available',
                            lambda: False)
        keys = [t.key for t in _bare_panel()._tiles()]
        assert 'record' not in keys

    def test_tile_shown_in_expanded_tier(self, monkeypatch):
        import quick_actions
        monkeypatch.setattr(quick_actions, '_recorder_available',
                            lambda: True)
        tiles = {t.key: t for t in _bare_panel()._tiles()}
        assert tiles['record'].tier == 2
        assert tiles['record'].label == 'Record'

    def test_tile_state_routes_to_recorder(self, monkeypatch):
        import quick_actions
        states = {'recording': False}
        fake = types.SimpleNamespace(
            is_recording=lambda: states['recording'],
            set_recording=lambda v: states.update(recording=bool(v)),
        )
        monkeypatch.setattr(capture, 'get_recorder', lambda: fake)
        monkeypatch.setattr(quick_actions, '_recorder_available',
                            lambda: True)
        tiles = {t.key: t for t in _bare_panel()._tiles()}
        assert tiles['record'].get_state() is False
        tiles['record'].set_state(True)
        assert states == {'recording': True}
        assert tiles['record'].get_state() is True

    def test_gate_degrades_without_capture(self, monkeypatch):
        import quick_actions
        monkeypatch.setitem(sys.modules, 'capture', None)
        assert quick_actions._recorder_available() is False


# --- stitcher on synthetic frames --------------------------------------------


BLACK = (0, 0, 0)
GRAY = (128, 128, 128)
WHITE = (255, 255, 255)
RED = (200, 30, 30)
GREEN = (30, 180, 30)
BLUE = (30, 30, 200)


def _frame(colors, width=8):
    flat = b''.join(bytes(color) * width for color in colors)
    assert len(flat) == width * len(colors) * 3
    return (width, len(colors), flat)


def _rows(frame):
    width, height, rgb = frame
    stride = width * 3
    return [rgb[y * stride:(y + 1) * stride] for y in range(height)]


class TestStitcher:
    def test_overlap_stitches_once(self):
        first = _frame([GRAY] * 10 + [WHITE] * 10)
        second = _frame([WHITE] * 10 + [BLACK] * 10)
        width, height, rgb = capture_stitch.stitch([first, second])
        assert (width, height) == (8, 30)
        rows = _rows((width, height, rgb))
        assert rows[:10] == _rows(first)[:10]
        assert rows[10:20] == _rows(first)[10:20]
        assert rows[20:] == _rows(second)[10:20]

    def test_different_overlap_sizes(self):
        first = _frame([RED] * 6 + [GREEN] * 14)
        second = _frame([GREEN] * 14 + [BLUE] * 6)
        _w, height, _rgb = capture_stitch.stitch([first, second])
        assert height == 6 + 14 + 6
        narrow = _frame([RED] * 16 + [GREEN] * 8)
        narrow2 = _frame([GREEN] * 8 + [BLUE] * 16)
        _w, height2, _rgb = capture_stitch.stitch([narrow, narrow2])
        assert height2 == 16 + 8 + 16

    def test_sticky_header_footer_excluded_from_match(self):
        header = [GRAY] * 2
        footer = [BLACK] * 2
        first = _frame(header + [RED] * 10 + [GREEN] * 10 + footer)
        second = _frame(header + [GREEN] * 10 + [BLUE] * 10 + footer)
        width, height, rgb = capture_stitch.stitch([first, second])
        assert (width, height) == (8, 2 + 10 + 10 + 10 + 2)
        rows = _rows((width, height, rgb))
        assert rows[:2] == _rows(first)[:2]
        assert rows[-2:] == _rows(first)[-2:]

    def test_identical_frames_add_no_rows(self):
        frame = _frame([RED] * 12 + [GREEN] * 12)
        width, height, rgb = capture_stitch.stitch([frame, frame])
        assert (width, height) == (8, 24)
        assert rgb == frame[2]

    def test_frames_equal(self):
        frame = _frame([RED] * 4)
        assert capture_stitch.frames_equal(frame, (8, 4, frame[2])) is True
        other = _frame([BLUE] * 4)
        assert capture_stitch.frames_equal(frame, other) is False

    def test_width_mismatch_rejected(self):
        with pytest.raises(ValueError):
            capture_stitch.stitch([(8, 2, b'\0' * 48),
                                   (4, 2, b'\0' * 24)])

    def test_pixbuf_round_trip(self):
        pytest.importorskip('gi')
        try:
            from gi.repository import GdkPixbuf  # noqa: F401
        except Exception:
            pytest.skip('GdkPixbuf unavailable')
        frame = _frame([RED] * 6 + [GREEN] * 6, width=16)
        width, height, rgb = frame
        path = os.path.join(
            os.path.dirname(str(DATA_DIR)), 'tmp_stitch_test.png')
        try:
            assert capture_stitch.save_png(width, height, rgb, path) is True
            loaded = capture_stitch.load_png(path)
            assert loaded is not None
            assert loaded[0] == width and loaded[1] == height
            assert loaded[2] == rgb
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def test_load_missing_returns_none(self):
        assert capture_stitch.load_png('/nonexistent/shot.png') is None


# --- virtual-pointer protocol -------------------------------------------------


class TestVirtualPointerProtocol:
    def _interfaces(self):
        tree = ET.parse(DATA_DIR / 'virtual-pointer-unstable-v1.xml')
        return {iface.get('name'): iface
                for iface in tree.getroot().iter('interface')}

    def test_manager_version_and_request_order(self):
        ifaces = self._interfaces()
        manager = ifaces['zwlr_virtual_pointer_manager_v1']
        assert manager.get('version') == '2'
        assert [req.get('name') for req in manager.iter('request')] == [
            'create_virtual_pointer',
            'create_virtual_pointer_with_output',
            'destroy',
        ]

    def test_pointer_request_order_pin(self):
        ifaces = self._interfaces()
        pointer = ifaces['zwlr_virtual_pointer_v1']
        assert [req.get('name') for req in pointer.iter('request')] == [
            'motion',
            'motion_absolute',
            'button',
            'axis',
            'frame',
            'axis_source',
            'axis_stop',
            'axis_discrete',
            'destroy',
        ]

    def test_finger_source_value(self):
        ifaces = self._interfaces()
        pointer = ifaces['zwlr_virtual_pointer_v1']
        sources = {
            entry.get('name'): entry.get('value')
            for enum in pointer.iter('enum')
            if enum.get('name') == 'axis_source'
            for entry in enum.iter('entry')
        }
        assert sources['finger'] == '1'
        assert capture.AXIS_SOURCE_FINGER == 1
        assert capture.AXIS_VERTICAL == 0

    def test_bindings_locate_the_xml(self):
        assert ('virtual-pointer-unstable-v1.xml'
                in wayland_bindings.VIRTUAL_POINTER_XML_NAMES)
        path = wayland_bindings.protocol_xml_path(
            wayland_bindings.VIRTUAL_POINTER_XML_NAME)
        assert path is not None and path.is_file()

    def test_emit_scroll_order(self):
        calls = []

        class _Pointer:
            def axis_source(self, source):
                calls.append(('axis_source', (source,)))

            def axis(self, time_ms, axis, value):
                calls.append(('axis', (time_ms, axis, value)))

            def frame(self):
                calls.append(('frame', ()))

        capture.emit_scroll(_Pointer(), 1000, time_ms=42)
        assert calls[0] == ('axis_source', (1,))
        assert calls[-1] == ('frame', ())
        axes = [c for c in calls if c[0] == 'axis']
        assert len(axes) == capture._SCROLL_STEPS
        assert all(a[1][0] == 42 and a[1][1] == 0 for a in axes)
        total = sum(a[1][2] for a in axes)
        assert total == pytest.approx(700.0)

    def test_scroller_degrades_without_bindings(self):
        scroller = capture.VirtualScroller(bind_fn=lambda *a: None)
        assert scroller.connect() is False
        assert scroller.scroll(720) is False

    def test_scroller_binds_manager_at_capped_version(self):
        seen = {}
        created = []

        class _Registry:
            def __init__(self):
                self.dispatcher = {}

            def bind(self, name, cls, version):
                seen.setdefault('binds', []).append((name, version))
                if cls is bindings['WlSeat']:
                    return ('seat', name)
                manager = _Manager()
                seen['manager'] = manager
                return manager

        events = []

        class _Display:
            def __init__(self):
                self.registry = _Registry()
                self.flushed = 0

            def connect(self):
                events.append('connect')

            def get_registry(self):
                return self.registry

            def dispatch(self, block=False):
                handler = self.registry.dispatcher.get('global')
                handler(self.registry, 7, 'wl_seat', 9)
                handler(self.registry, 9,
                        'zwlr_virtual_pointer_manager_v1', 3)

            def flush(self):
                self.flushed += 1

            def disconnect(self):
                events.append('disconnect')

        class _Manager:
            def create_virtual_pointer(self, seat):
                created.append(seat)
                pointer = types.SimpleNamespace(
                    calls=[],
                    axis_source=lambda s: created.append(('src', s)),
                    axis=lambda *a: created.append(('axis', a)),
                    frame=lambda: created.append(('frame',)),
                    destroy=lambda: created.append(('destroy',)),
                )
                return pointer

        bindings = {'WlSeat': object(),
                    'ZwlrVirtualPointerManagerV1': _Manager()}
        scroller = capture.VirtualScroller(display_cls=_Display)
        assert scroller._connect_with(bindings, _Display) is True
        assert dict(seen['binds']) == {7: 1, 9: 2}
        assert created[0] == ('seat', 7)
        assert scroller.scroll(720) is True
        assert ('src', 1) in created
        assert created[-1] == ('frame',)
        scroller.close()
        assert created[-1] == ('destroy',)


# --- config ------------------------------------------------------------------


class TestRecordAudioConfig:
    def test_default_off(self, tmp_home, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_home))
        from config import ShellConfig
        assert ShellConfig().record_audio is False

    def test_round_trip(self, tmp_home, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_home))
        from config import ShellConfig
        config = ShellConfig()
        config.set_record_audio(True)
        assert ShellConfig().record_audio is True
