from __future__ import annotations

import os
import socket
from collections.abc import Callable
from pathlib import Path

from gi.repository import GLib

from shell_log import get_logger

_log = get_logger('ipc')

# Connections are read without blocking (the watch callbacks run on the
# UI thread); a client that connects and never finishes its line is
# dropped after this long.
CONN_TIMEOUT_S = 0.5
_MAX_LINE = 256

# IPC verbs dropped while the first-boot tour is visible: lisgd's real
# gestures would otherwise fire under the tour's own swipe steps.
TOUR_SUPPRESSED_VERBS = frozenset(
    {'gesture.shade', 'gesture.switcher', 'gesture.home'})

# Page-driving verbs for rigs and scripts (no gesture timing needed):
# page.home / page.apps / page.settings switch the stack page, and
# settings.open[:section] reuses the settings deep-link
# (open_settings(section): wifi | bluetooth | mobile deep-link, anything
# else opens the top of the page).
PAGE_COMMANDS = {
    'page.home': 'home',
    'page.apps': 'apps',
    'page.settings': 'settings',
}


def parse_page_command(command: str) -> tuple[str, str | None] | None:
    """Split a page-driving IPC command.

    Returns ('page', <stack name>) for page.home/apps/settings and
    ('settings.open', <section | None>) for settings.open[:section];
    None for anything else. Pure so rigs and tests share the dispatch
    table with the application.
    """
    page = PAGE_COMMANDS.get(command)
    if page is not None:
        return ('page', page)
    if command == 'settings.open' or command.startswith('settings.open:'):
        return ('settings.open', command.partition(':')[2] or None)
    return None


# Verbs that arrive before the shell exists (the first-boot wizard can be
# up for minutes) queue for the first present. Only this many are kept.
PENDING_LIMIT = 8

# Touches on whatever was on screen when they happened: replaying them
# after the wizard would go home, open recents or the shade unasked.
_STALE_BEFORE_PRESENT = ('gesture.', 'shade.', 'switcher.')


def queue_pending_command(pending: list[str], command: str,
                          limit: int = PENDING_LIMIT) -> None:
    """Queue a verb for the shell's first present, bounded: gesture and
    shade/switcher verbs are dropped, a repeated verb moves to the end
    instead of queueing twice, and past `limit` the oldest falls off."""
    if command.startswith(_STALE_BEFORE_PRESENT):
        return
    if command in pending:
        pending.remove(command)
    pending.append(command)
    del pending[:-limit]


def suppress_during_tour(command: str, tour_visible: bool) -> bool:
    """True when `command` must be dropped because the tour is up."""
    return bool(tour_visible) and command in TOUR_SUPPRESSED_VERBS


def _socket_path() -> str:
    runtime = os.environ.get('XDG_RUNTIME_DIR') or str(Path.home() / '.local' / 'share' / 'xx-wm')
    return os.path.join(runtime, 'xx-wm.sock')


def _socket_is_live(path: str) -> bool:
    try:
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.5)
        probe.connect(path)
        probe.close()
    except OSError:
        return False
    return True


class IPCServer:
    """
    Lightweight Unix-socket IPC server. Line-based protocol:
      lock | shade.show | shade.hide | switcher.show | switcher.hide |
      gesture.back | gesture.home | gesture.shade | gesture.keyboard |
      gesture.switcher | welcome | page.home | page.apps | page.settings |
      settings.open[:section]
    There is deliberately no unlock verb: the socket must never bypass the
    lock screen. Other surfaces or external scripts (e.g. wake hook) connect,
    send one line, disconnect.
    """

    def __init__(self, handler: Callable[[str], None]) -> None:
        self._handler = handler
        self._sock: socket.socket | None = None
        self._watch_id: int | None = None
        # Connections still sending: conn -> [buffer, watch id, timeout id].
        self._conns: dict[socket.socket, list] = {}
        self._start()

    def _start(self) -> None:
        path = _socket_path()
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if _socket_is_live(path):
            _log.error(
                'ipc socket %r already serves a shell; not starting a second server', path)
            return
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(path)
        # Default umask would leave the socket traversable by other users.
        os.chmod(path, 0o600)
        sock.listen(8)
        sock.setblocking(False)
        self._sock = sock

        self._watch_id = GLib.io_add_watch(
            sock.fileno(), GLib.IOCondition.IN, self._on_incoming, sock)

    def _on_incoming(self, _fd: int, _condition: GLib.IOCondition, srv: socket.socket) -> bool:
        try:
            conn, _ = srv.accept()
        except OSError:
            return True
        try:
            conn.setblocking(False)
        except OSError:
            conn.close()
            return True
        # Clients send one line and close, so the line is usually already
        # here; otherwise the rest arrives through a watch on the conn.
        self._conns[conn] = [b'', None, None]
        if not self._read_conn(conn):
            self._conns[conn][1:] = [
                GLib.io_add_watch(
                    conn.fileno(),
                    GLib.IOCondition.IN | GLib.IOCondition.HUP
                    | GLib.IOCondition.ERR,
                    self._on_conn_ready, conn),
                GLib.timeout_add(int(CONN_TIMEOUT_S * 1000),
                                 self._on_conn_timeout, conn),
            ]
        return True

    def _read_conn(self, conn: socket.socket) -> bool:
        """Read what is there without blocking. True once the connection
        is finished (line complete, EOF, error or too long) and handled."""
        state = self._conns[conn]
        buf = state[0]
        done = False
        while True:
            try:
                chunk = conn.recv(_MAX_LINE)
            except BlockingIOError:
                break
            except OSError:
                done = True
                break
            if not chunk:
                done = True
                break
            buf += chunk
            if b'\n' in buf or len(buf) >= _MAX_LINE:
                done = True
                break
        if not done:
            state[0] = buf
            return False
        self._close_conn(conn)
        line = buf[:_MAX_LINE].split(b'\n', 1)[0]
        command = line.decode('utf-8', errors='replace').strip()
        if command:
            GLib.idle_add(self._dispatch, command)
        return True

    def _on_conn_ready(self, _fd: int, _condition: GLib.IOCondition,
                       conn: socket.socket) -> bool:
        state = self._conns.get(conn)
        if state is None:
            return False
        watch_id, state[1] = state[1], None  # False removes this watch
        if self._read_conn(conn):
            return False
        state[1] = watch_id
        return True

    def _on_conn_timeout(self, conn: socket.socket) -> bool:
        state = self._conns.get(conn)
        if state is not None:
            state[2] = None  # returning False removes this timer
            self._close_conn(conn)
        return False

    def _close_conn(self, conn: socket.socket) -> None:
        state = self._conns.pop(conn, None)
        if state is not None:
            _buf, watch_id, timeout_id = state
            if watch_id:
                GLib.source_remove(watch_id)
            if timeout_id:
                GLib.source_remove(timeout_id)
        try:
            conn.close()
        except OSError:
            pass

    def _dispatch(self, command: str) -> bool:
        try:
            self._handler(command)
        except Exception:
            _log.exception('ipc handler failed for %r', command)
        return False

    def stop(self) -> None:
        # Remove the watch before closing the fd so no callback can fire on
        # a closed socket; the None reset guards a double stop.
        if self._watch_id:
            GLib.source_remove(self._watch_id)
            self._watch_id = None
        for conn in list(self._conns):
            self._close_conn(conn)
        if self._sock:
            try:
                self._sock.close()
                os.unlink(_socket_path())
            except OSError:
                pass
            self._sock = None


def ipc_send(command: str) -> None:
    """Send a command to the running shell IPC server. No-op if server isn't up."""
    path = _socket_path()
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        sock.connect(path)
        sock.send(command.encode('utf-8'))
        sock.close()
    except OSError:
        pass
