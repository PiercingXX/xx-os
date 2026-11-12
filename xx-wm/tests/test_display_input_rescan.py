"""Input nodes that come and go: /dev/input is watched and readers reopen.

A node that re-enumerates (the power key on a reprobed controller) must be
reopened, or the phone blanks with no way to wake. DisplayManager is driven
with a recording GLib seam (timers are fired by hand) and fake reader /
directory-monitor seams; the real reader thread and the real Gio monitor
are exercised separately.
"""
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import display_manager  # noqa: E402


class _GLib:
    SOURCE_REMOVE = False
    SOURCE_CONTINUE = True

    def __init__(self):
        self.timers: list = []

    @staticmethod
    def idle_add(fn, *a):
        fn(*a)
        return 0

    def timeout_add(self, ms, cb, *_a):
        self.timers.append((ms, cb))
        return len(self.timers)

    @staticmethod
    def timeout_add_seconds(_s, _cb, *_a):
        return 99

    @staticmethod
    def source_remove(_tid):
        return None

    def fire(self):
        due = [cb for _ms, cb in self.timers]
        self.timers.clear()
        for cb in due:
            cb()


@pytest.fixture
def env(monkeypatch):
    glib = _GLib()
    monkeypatch.setattr(display_manager, 'GLib', glib)
    nodes = ['/dev/input/event0', '/dev/input/event1']
    monkeypatch.setattr(display_manager, '_all_event_devices',
                        lambda: list(nodes))
    started: list = []
    monkeypatch.setattr(
        display_manager, '_watch_evdev',
        lambda path, _fp, _cb, on_exit=None: started.append((path, on_exit)))
    monitors: list = []
    monkeypatch.setattr(display_manager, '_monitor_input_dir',
                        lambda cb: monitors.append(cb) or object(),
                        raising=False)
    monkeypatch.setattr(display_manager, '_detect_fp_node', lambda: '')
    monkeypatch.setattr(display_manager, '_make_output_power', lambda: None)

    def _build():
        return display_manager.DisplayManager()

    return _build, glib, nodes, started, monitors


def test_every_node_gets_a_reader_and_the_dir_is_watched(env):
    build, _glib, _nodes, started, monitors = env
    mgr = build()
    assert [p for p, _ in started] == ['/dev/input/event0', '/dev/input/event1']
    assert len(monitors) == 1
    assert mgr._wake_devs == ['/dev/input/event0', '/dev/input/event1']


def test_reader_exit_reopens_the_node_when_it_returns(env):
    build, glib, _nodes, started, _monitors = env
    mgr = build()
    path, on_exit = started[0]
    on_exit()
    assert path not in mgr._wake_devs
    assert glib.timers, 'a rescan is scheduled'
    glib.fire()
    assert [p for p, _ in started].count(path) == 2
    assert path in mgr._wake_devs


def test_new_node_in_dev_input_is_opened(env):
    build, glib, nodes, started, monitors = env
    mgr = build()
    nodes.append('/dev/input/event7')
    monitors[0]('/dev/input/event7')
    monitors[0]('/dev/input/mouse0')
    assert len(glib.timers) == 1
    glib.fire()
    assert started[-1][0] == '/dev/input/event7'
    assert '/dev/input/event7' in mgr._wake_devs


def test_late_exit_of_a_replaced_reader_keeps_the_new_one(env):
    build, glib, _nodes, started, _monitors = env
    mgr = build()
    old_exit = started[0][1]
    old_exit()
    glib.fire()
    old_exit()
    assert '/dev/input/event0' in mgr._wake_devs


def test_exit_drops_a_hold_that_node_can_no_longer_release(env):
    build, _glib, _nodes, started, _monitors = env
    mgr = build()
    mgr._on_event('/dev/input/event1', display_manager.EV_KEY,
                  display_manager.KEY_VOLUMEDOWN, 1)
    assert mgr._voldown_held is True
    started[1][1]()
    assert mgr._voldown_held is False


def test_idle_blank_comes_back_once_a_node_is_readable(env):
    build, glib, nodes, _started, monitors = env
    nodes.clear()
    mgr = build()
    assert mgr._idle_enabled is False
    nodes.append('/dev/input/event2')
    monitors[0]('/dev/input/event2')
    glib.fire()
    assert mgr._idle_enabled is True


def test_fingerprint_node_is_redetected_on_rescan(env, monkeypatch):
    build, glib, _nodes, started, monitors = env
    mgr = build()
    monkeypatch.setattr(display_manager, '_detect_fp_node',
                        lambda: '/dev/input/event4')
    monitors[0]('/dev/input/event4')
    glib.fire()
    assert mgr._fp_node == '/dev/input/event4'


def test_reader_on_a_vanishing_node_posts_on_exit(monkeypatch, tmp_path):
    done = threading.Event()
    monkeypatch.setattr(display_manager, 'GLib', _GLib())
    node = tmp_path / 'event5'
    node.write_bytes(b'')   # read() hits EOF at once, like a removed node
    display_manager._watch_evdev(str(node), '', lambda *a: None,
                                 on_exit=done.set)
    assert done.wait(2)


def test_reader_that_cannot_open_posts_on_exit(monkeypatch, tmp_path):
    done = threading.Event()
    monkeypatch.setattr(display_manager, 'GLib', _GLib())
    display_manager._watch_evdev(str(tmp_path / 'gone'), '',
                                 lambda *a: None, on_exit=done.set)
    assert done.wait(2)


def test_input_dir_monitor_reports_new_nodes(tmp_path):
    try:
        from gi.repository import Gio, GLib
        Gio.File.new_for_path(str(tmp_path))
    except Exception:
        pytest.skip('real Gio unavailable')
    seen: list = []
    monitor = display_manager._monitor_input_dir(seen.append, str(tmp_path))
    assert monitor is not None
    (tmp_path / 'event9').write_bytes(b'')
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + 3
    while not seen and time.monotonic() < deadline:
        ctx.iteration(False)
        time.sleep(0.01)
    monitor.cancel()
    assert any(path.endswith('event9') for path in seen)
