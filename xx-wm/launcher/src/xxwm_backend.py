"""Window list and switching through the xxwm window manager (river).

river gives clients a read-only window list but no way to activate or close
a window; the window manager owns that, over its control socket. This
backend speaks that socket and offers ToplevelManager the same contract as
WaylandToplevelBackend:

    available(), connect(cb), connect_state(cb), activate(h), close(h),
    shutdown()

Handles are xxwm window identifiers (strings, never reused). Socket I/O
runs on worker threads only; nothing here blocks the GTK main loop.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading

log = logging.getLogger('xxwm_backend')

_REQUEST_TIMEOUT = 4.0
_RETRY_MIN = 0.5
_RETRY_MAX = 5.0


def socket_path() -> str | None:
    """Where xxwm listens: $XXWM_SOCKET, else
    $XDG_RUNTIME_DIR/xxwm-<WAYLAND_DISPLAY>.sock (as xxwm computes it)."""
    explicit = os.environ.get('XXWM_SOCKET')
    if explicit:
        return explicit
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    if not runtime:
        return None
    display = os.environ.get('WAYLAND_DISPLAY') or 'wayland-0'
    display = display.rsplit('/', 1)[-1]
    return os.path.join(runtime, f'xxwm-{display}.sock')


def present() -> bool:
    """True when an xxwm control socket exists for this session. A cheap
    file check, safe on the main loop; the backend connects later."""
    path = socket_path()
    return bool(path) and os.path.exists(path)


def request(line: str, path: str | None = None,
            timeout: float = _REQUEST_TIMEOUT) -> str:
    """Send one request and return the answer. Blocking: worker threads
    only."""
    target = path or socket_path()
    if not target:
        raise OSError('no xxwm socket path')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(target)
        sock.sendall(line.encode() + b'\n')
        chunks = []
        while True:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)
    return b''.join(chunks).decode(errors='replace').strip()


def _idle_add(fn, *args) -> None:
    from gi.repository import GLib
    GLib.idle_add(lambda: fn(*args) and False)


def request_async(line: str, on_done=None, path: str | None = None,
                  post=None) -> None:
    """Send one request on a worker thread; ``on_done(answer)`` runs on
    the main loop (answer is None when xxwm could not be reached)."""
    post = post or _idle_add

    def run() -> None:
        try:
            answer = request(line, path)
        except OSError as exc:
            log.warning('xxwm %r failed: %s', line, exc)
            answer = None
        else:
            if answer.startswith('error'):
                log.warning('xxwm %r: %s', line, answer)
        if on_done is not None:
            post(on_done, answer)
    threading.Thread(target=run, daemon=True, name='xx-wm-xxwm-req').start()


def parse_state_line(line: str) -> tuple[str, object] | None:
    """One line of `subscribe state`: ('state', {'outputs': [..],
    'windows': [..]}) or ('shell', verb); None for anything else."""
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    verb = obj.get('shell')
    if isinstance(verb, str) and verb:
        return ('shell', verb)
    outputs, windows = obj.get('outputs'), obj.get('windows')
    if isinstance(outputs, list) and isinstance(windows, list):
        return ('state', {
            'outputs': [o for o in outputs if isinstance(o, dict)],
            'windows': [w for w in windows if isinstance(w, dict)],
        })
    return None


class XxwmStateWatcher:
    """Follows xxwm's `subscribe state` stream on a worker thread: the
    outputs and windows on every change (the docked top bar) and the
    verbs xxwm hands the shell (`launcher`, `bar`, `keybinds`). Both
    callbacks run on the main loop."""

    def __init__(self, on_state, on_shell, path: str | None = None,
                 post=None) -> None:
        self._path = path or socket_path()
        self._on_state = on_state
        self._on_shell = on_shell
        self._post = post or _idle_add
        self._closing = False
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None and self._path:
            self._thread = threading.Thread(
                target=self._run, daemon=True, name='xx-wm-xxwm-state')
            self._thread.start()

    def shutdown(self) -> None:
        self._closing = True
        sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def feed(self, line: str) -> None:
        parsed = parse_state_line(line.strip()) if line.strip() else None
        if parsed is None:
            return
        kind, value = parsed
        self._post(self._on_state if kind == 'state' else self._on_shell,
                   value)

    def _run(self) -> None:
        delay = _RETRY_MIN
        while not self._closing:
            try:
                self._follow()
                delay = _RETRY_MIN
            except OSError as exc:
                if self._closing:
                    return
                log.info('xxwm state stream lost (%s); retrying', exc)
            if self._closing:
                return
            threading.Event().wait(delay)
            delay = min(delay * 2, _RETRY_MAX)

    def _follow(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock = sock
        try:
            sock.connect(self._path)
            sock.sendall(b'subscribe state\n')
            with sock.makefile('r', encoding='utf-8',
                               errors='replace') as stream:
                for line in stream:
                    if self._closing:
                        return
                    self.feed(line)
            raise OSError('xxwm closed the state stream')
        finally:
            self._sock = None
            try:
                sock.close()
            except OSError:
                pass


class XxwmBackend:
    """ToplevelManager backend fed by xxwm's `subscribe` stream."""

    def __init__(self, path: str | None = None) -> None:
        self._path = path or socket_path()
        self._manager_cb = None
        self._state_cb = None
        self._known: dict[str, tuple[str, str]] = {}
        self._focused: str | None = None
        self._lock = threading.Lock()
        self._closing = False
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None

    # -- backend contract -------------------------------------------------

    def available(self) -> bool:
        return bool(self._path) and not self._closing

    def connect(self, callback) -> None:
        self._manager_cb = callback
        with self._lock:
            known = dict(self._known)
        for handle, (app_id, title) in known.items():
            callback(handle, app_id, title)
        if self._thread is None and self._path:
            self._thread = threading.Thread(
                target=self._run, daemon=True, name='xx-wm-xxwm')
            self._thread.start()

    def connect_state(self, callback) -> None:
        self._state_cb = callback

    def connect_focus(self, callback) -> None:
        self._state_cb = callback

    def activate(self, handle: object) -> None:
        self._send_async(f'focus {handle}')

    def close(self, handle: object) -> None:
        self._send_async(f'close {handle}')

    def shutdown(self) -> None:
        self._closing = True
        sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    # -- internals ------------------------------------------------------------

    def _send_async(self, line: str) -> None:
        def run() -> None:
            try:
                answer = request(line, self._path)
            except OSError as exc:
                log.warning('xxwm %r failed: %s', line, exc)
                return
            if answer.startswith('error'):
                log.warning('xxwm %r: %s', line, answer)
        threading.Thread(target=run, daemon=True, name='xx-wm-xxwm-req').start()

    def _run(self) -> None:
        delay = _RETRY_MIN
        while not self._closing:
            try:
                self._subscribe()
                delay = _RETRY_MIN
            except OSError as exc:
                if self._closing:
                    return
                log.info('xxwm subscription lost (%s); retrying', exc)
            if self._closing:
                return
            threading.Event().wait(delay)
            delay = min(delay * 2, _RETRY_MAX)

    def _subscribe(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock = sock
        try:
            sock.connect(self._path)
            sock.sendall(b'subscribe\n')
            with sock.makefile('r', encoding='utf-8', errors='replace') as stream:
                for line in stream:
                    if self._closing:
                        return
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        items = json.loads(line)
                    except ValueError:
                        log.warning('xxwm sent a bad window list: %.80r', line)
                        continue
                    if isinstance(items, list):
                        self.apply_snapshot(items)
            raise OSError('xxwm closed the subscription')
        finally:
            self._sock = None
            try:
                sock.close()
            except OSError:
                pass

    def apply_snapshot(self, items: list) -> None:
        """Diff a window list from xxwm against what was last reported and
        emit the backend callbacks (adds/changes, removals, focus)."""
        seen: dict[str, tuple[str, str]] = {}
        focused = None
        for item in items:
            if not isinstance(item, dict):
                continue
            handle = item.get('id')
            if not isinstance(handle, str) or not handle:
                continue
            app_id = item.get('app_id') if isinstance(item.get('app_id'), str) else ''
            title = item.get('title') if isinstance(item.get('title'), str) else ''
            seen[handle] = (app_id, title)
            if item.get('focused') is True:
                focused = handle
        with self._lock:
            old = self._known
            self._known = seen
            old_focus = self._focused
            self._focused = focused
        cb = self._manager_cb
        if cb is not None:
            for handle, info in seen.items():
                if old.get(handle) != info:
                    cb(handle, info[0], info[1])
            for handle in old:
                if handle not in seen:
                    cb(handle, None, None)
        state = self._state_cb
        if state is not None and focused != old_focus:
            if old_focus is not None and old_focus in seen:
                state(old_focus, False)
            if focused is not None:
                state(focused, True)
