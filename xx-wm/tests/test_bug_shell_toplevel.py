"""Lane bug_shell findings 1+2: toplevel thread marshalling and resync lifecycle.

Finding 1: registry change callbacks must reach the GTK main thread. The old
``GLib.main_depth() == 0`` probe is per-thread, so on the wayland thread it
always reads 0 and GTK widgets were touched off-thread.
Finding 2: every recents open leaked a Wayland client (DeferredWaylandBackend
had no shutdown), and the resync replay hid recents again as "new" windows.
"""
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from toplevel_manager import DeferredWaylandBackend, ToplevelManager


@pytest.fixture(autouse=True)
def _isolate_modules():
    """Leave no imported shell module behind: sibling files import window
    and friends against their own gi seams (test_folders idiom)."""
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        sys.modules.pop(name, None)


class RecordingGLib:
    """Faithful idle_add semantics: queue, never run synchronously.

    Real GLib.main_depth() is per-thread: 0 on any thread that does not own
    the default context (in particular every worker thread, loop or not).
    """

    def __init__(self) -> None:
        self.queued: list = []

    def idle_add(self, callback, *args):
        self.queued.append((callback, args))
        return len(self.queued)

    def main_depth(self):
        return 0

    def drain(self):
        queued, self.queued = self.queued, []
        for callback, args in queued:
            callback(*args)


class GLibProxy:
    """Recording idle_add over the real GLib.

    Modules imported while the proxy is installed (window, app_standby,
    …) keep working afterwards: every other attribute delegates to the
    real bindings, so no import-time binding is left pointing at a stub.
    """

    def __init__(self, rec, real):
        self._rec = rec
        self._real = real

    def idle_add(self, callback, *args):
        return self._rec.idle_add(callback, *args)

    def main_depth(self):
        return self._rec.main_depth()

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture
def glib(monkeypatch):
    """Route toplevel_manager's lazy GLib import at a recording double.

    Patched as an attribute of the real gi.repository package: that is
    what ``from gi.repository import GLib`` reads (a sys.modules entry
    alone is bypassed once the real submodule was imported).
    """
    import gi.repository
    rec = RecordingGLib()
    monkeypatch.setattr(
        gi.repository, 'GLib', GLibProxy(rec, gi.repository.GLib))
    return rec


class FakeBackend:
    def __init__(self) -> None:
        self._callback = None
        self._focus_cb = None
        self.shutdown_calls = 0

    def available(self) -> bool:
        return True

    def connect(self, callback) -> None:
        self._callback = callback

    def connect_focus(self, callback) -> None:
        self._focus_cb = callback

    def shutdown(self) -> None:
        self.shutdown_calls += 1

    def add(self, handle, app_id, title) -> None:
        self._callback(handle, app_id, title)

    def remove(self, handle) -> None:
        self._callback(handle, None, None)

    def focus(self, handle, activated=True) -> None:
        if self._focus_cb is not None:
            self._focus_cb(handle, activated)


class TestOffThreadMarshalling:
    def test_change_callback_runs_on_main_thread(self, glib) -> None:
        backend = FakeBackend()
        manager = ToplevelManager(backend=backend)
        seen_threads: list = []
        manager.on_change(
            lambda: seen_threads.append(threading.current_thread()))
        worker = threading.Thread(
            target=backend.add, args=('h1', 'org.a.App', 'A'))
        worker.start()
        worker.join()
        assert seen_threads == [], \
            'GTK change callback must not run on the wayland thread'
        assert len(glib.queued) == 1
        glib.drain()
        assert seen_threads == [threading.main_thread()]

    def test_activated_callback_runs_on_main_thread(self, glib) -> None:
        backend = FakeBackend()
        manager = ToplevelManager(backend=backend)
        backend.add('h1', 'org.a.App', 'A')
        glib.drain()
        seen: list = []
        manager.on_activated_changed(
            lambda app_id: seen.append(
                (app_id, threading.current_thread())))
        worker = threading.Thread(
            target=backend.focus, args=('h1', True))
        worker.start()
        worker.join()
        assert [t for _, t in seen] == [], \
            'activated callbacks must not run on the wayland thread'
        glib.drain()
        assert [(a, t) for a, t in seen] == [('org.a.App', threading.main_thread())]

    def test_main_thread_still_delivers_synchronously(self, glib) -> None:
        backend = FakeBackend()
        manager = ToplevelManager(backend=backend)
        calls: list = []
        manager.on_change(lambda: calls.append(manager.list()))
        backend.add('h1', 'org.a.App', 'A')
        assert len(calls) == 1
        assert glib.queued == []


class TestDeferredShutdown:
    def test_deferred_backend_exposes_shutdown(self) -> None:
        assert callable(getattr(DeferredWaylandBackend, 'shutdown', None))

    def test_shutdown_before_ready_aborts_prepare(self, monkeypatch) -> None:
        import threading as _threading

        import toplevel_manager as _tm
        import wayland_bindings as _wb
        monkeypatch.setattr(
            _wb, 'ensure_bindings',
            lambda: {'WlSeat': object,
                     'ZwlrForeignToplevelManagerV1': object})
        events: list = []

        class _FakeReal:
            def __init__(self, **kwargs) -> None:
                events.append('built')

            def connect(self, callback) -> None:
                events.append('wired')

            def shutdown(self) -> None:
                events.append('down')

        monkeypatch.setattr(_tm, 'WaylandToplevelBackend', _FakeReal)
        backend = DeferredWaylandBackend.__new__(DeferredWaylandBackend)
        backend._lock = _threading.Lock()
        backend._callback = lambda *a: events.append('callback')
        backend._state_callback = None
        backend._ready_callbacks = []
        backend._real = None
        backend._shutdown_requested = False
        backend.shutdown()
        backend._prepare()
        assert backend._real is None
        assert 'wired' not in events
        assert 'callback' not in events
        assert events.count('down') == 1

    def test_shutdown_delegates_to_real_backend(self) -> None:
        backend = DeferredWaylandBackend.__new__(DeferredWaylandBackend)
        import threading as _threading
        backend._lock = _threading.Lock()
        real_calls: list = []

        class _Real:
            def shutdown(self) -> None:
                real_calls.append('down')

        backend._real = _Real()
        backend._callback = None
        backend._state_callback = None
        backend._ready_callbacks = []
        backend._shutdown_requested = False
        backend.shutdown()
        assert real_calls == ['down']

    def test_resync_shuts_down_replaced_backend(self) -> None:
        first = FakeBackend()
        manager = ToplevelManager(backend=first)
        first.add('h1', 'org.a.App', 'A')
        manager.resync(backend=FakeBackend())
        assert first.shutdown_calls == 1


class TestResyncReplay:
    def _shell(self, window):
        import types as _types

        class FakeStack:
            def __init__(self):
                self.page = 'settings'
                self.calls: list = []

            def set_visible_child_name(self, page):
                self.calls.append(page)
                self.page = page

            def get_visible_child_name(self):
                return self.page

        class FakeOverlay:
            def __init__(self):
                self._visible = True
                self.hides = 0

            def get_visible(self):
                return self._visible

            def hide_shade(self):
                self._visible = False
                self.hides += 1

            def hide_switcher(self):
                self._visible = False
                self.hides += 1

        shell = _types.SimpleNamespace(
            stack=FakeStack(),
            _over_apps=True,
            _popover_count=0,
            _drawer_open_folder=None,
            _shade=FakeOverlay(),
            _switcher=FakeOverlay(),
            _toplevel_manager=None,
            _known_toplevels=set(),
        )
        shell._on_foreign_toplevels = (
            lambda: window.ShellWindow._on_foreign_toplevels(shell))
        shell._yield_to_foreign_app = (
            lambda: window.ShellWindow._yield_to_foreign_app(shell))
        shell.drop_to_background = (
            lambda: window.ShellWindow.drop_to_background(shell))
        shell._apply_layer_policy = (
            lambda: window.ShellWindow._apply_layer_policy(shell))
        return shell

    def test_resync_replay_does_not_hide_switcher(self, glib) -> None:
        import window
        shell = self._shell(window)
        manager = ToplevelManager(backend=FakeBackend())
        window.ShellWindow._wire_foreign_toplevels(shell, manager)
        manager._backend.add('h1', 'org.example.App', 'App')
        shell.stack.page = 'home'
        shell._over_apps = False
        shell._shade._visible = False
        shell._switcher._visible = True
        # Recents open re-binds: registry clears (empty flash), then the new
        # connection replays the same app under a new handle.
        second = FakeBackend()
        manager.resync(backend=second)
        second.add('h2', 'org.example.App', 'App')
        assert shell._switcher._visible is True, \
            'resync replay must not hide the just-opened switcher'
        assert shell.stack.page == 'home'

    def test_genuinely_new_app_still_yields(self, glib) -> None:
        import window
        shell = self._shell(window)
        manager = ToplevelManager(backend=FakeBackend())
        window.ShellWindow._wire_foreign_toplevels(shell, manager)
        manager._backend.add('h1', 'org.example.App', 'App')
        assert shell.stack.page == 'home'

    def test_new_app_after_resync_still_yields(self, glib) -> None:
        import window
        shell = self._shell(window)
        manager = ToplevelManager(backend=FakeBackend())
        window.ShellWindow._wire_foreign_toplevels(shell, manager)
        manager._backend.add('h1', 'org.example.App', 'App')
        shell.stack.page = 'settings'
        shell._over_apps = True
        second = FakeBackend()
        manager.resync(backend=second)
        second.add('h2', 'org.example.App', 'App')
        assert shell.stack.page == 'settings'
        second.add('h3', 'org.other.App', 'Other')
        assert shell.stack.page == 'home'

    def test_reclose_then_remap_yields(self, glib) -> None:
        import window
        shell = self._shell(window)
        manager = ToplevelManager(backend=FakeBackend())
        window.ShellWindow._wire_foreign_toplevels(shell, manager)
        manager._backend.add('h1', 'org.example.App', 'App')
        shell.stack.page = 'settings'
        shell._over_apps = True
        manager._backend.remove('h1')
        assert shell.stack.page == 'settings'
        manager._backend.add('h2', 'org.example.App', 'App')
        assert shell.stack.page == 'home'


class _ThreadDisplay:
    """Display stand-in for the wayland thread: no fd, records teardown."""

    def __init__(self) -> None:
        self.disconnect_threads: list[threading.Thread] = []

    def flush(self) -> None:
        pass

    def dispatch(self, block: bool = False) -> None:
        pass

    def disconnect(self) -> None:
        self.disconnect_threads.append(threading.current_thread())


def _threaded_backend(display):
    import os
    import queue
    import toplevel_manager
    backend = toplevel_manager.WaylandToplevelBackend.__new__(
        toplevel_manager.WaylandToplevelBackend)
    backend._display = display
    backend._closing = False
    backend._requests = queue.Queue()
    backend._handles = {}
    backend._teardown_lock = threading.Lock()
    backend._torn_down = False
    backend._wakeup_r, backend._wakeup_w = os.pipe()
    os.set_blocking(backend._wakeup_r, False)
    backend._thread = threading.Thread(target=backend._thread_loop, daemon=True)
    return backend


class TestWaylandThreadOwnsTeardown:
    def test_shutdown_lets_the_thread_disconnect(self, monkeypatch) -> None:
        import select as select_mod
        import toplevel_manager
        timeouts = []
        real_select = select_mod.select

        def _select(r, w, x, *timeout):
            timeouts.append(timeout)
            return real_select(r, w, x, *timeout)

        monkeypatch.setattr(toplevel_manager.select, 'select', _select)
        display = _ThreadDisplay()
        backend = _threaded_backend(display)
        thread = backend._thread
        thread.start()
        while not timeouts:
            pass
        backend.shutdown()
        thread.join(2)
        assert not thread.is_alive()
        assert display.disconnect_threads == [thread]
        assert backend._wakeup_r is None and backend._wakeup_w is None
        assert all(t == () for t in timeouts)

    def test_shutdown_without_thread_tears_down_inline(self) -> None:
        display = _ThreadDisplay()
        backend = _threaded_backend(display)
        backend.shutdown()
        backend.shutdown()
        assert display.disconnect_threads == [threading.current_thread()]
        assert backend._wakeup_w is None

    def test_only_eagain_is_retried(self) -> None:
        import errno
        import toplevel_manager
        backend = toplevel_manager.WaylandToplevelBackend.__new__(
            toplevel_manager.WaylandToplevelBackend)
        backend._closing = False
        assert not backend._dispatch_error_is_fatal(
            OSError(errno.EAGAIN, 'Resource temporarily unavailable'))
        assert not backend._dispatch_error_is_fatal(
            RuntimeError('Failed with error: 11'))
        assert backend._dispatch_error_is_fatal(
            RuntimeError('Failed with error: 110'))
        assert backend._dispatch_error_is_fatal(
            RuntimeError('protocol error 211 on object 11'))


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
