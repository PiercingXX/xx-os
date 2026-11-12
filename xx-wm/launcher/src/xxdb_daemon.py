"""xxdb phone daemon: TLS-over-TCP JSON-lines RPC server.

Stdlib only. The daemon never runs as root, binds Wi-Fi addresses only,
and refuses every command from untrusted (unpaired) clients. Its
callbacks (on_connect, on_disconnect, on_activity, on_system_request,
is_locked) run on connection threads: the owner hops to its main loop.
"""
from __future__ import annotations

import base64
import logging
import os
import pty
import select
import shutil
import signal
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import xxdb
from xxdb import FrameReader

_log = logging.getLogger('piercing.xxdb')

VERSION = '1'
INSTALL_DEST = '/usr/share/xx-wm'
SHELL_MAIN_SUFFIX = 'xx-wm/main.py'
RESTART_DELAY_S = 0.5

_IPC_VERBS = frozenset({
    'lock', 'shade.show', 'shade.hide', 'switcher.show', 'switcher.hide',
    'gesture.back', 'gesture.home', 'gesture.shade', 'gesture.keyboard',
    'gesture.switcher', 'welcome',
})


class DaemonError(RuntimeError):
    pass


def check_not_root() -> None:
    if os.geteuid() == 0:
        raise DaemonError('xxdb refuses to run as root: '
                          'start it as the session user')


def ensure_cert(cert_dir: Path, common_name: str = 'xxdb-phone',
                run=None) -> tuple[Path, Path, str]:
    """Create the self-signed phone cert on first enable. Returns
    (key_path, cert_path, fingerprint_hex); both files are 0600."""
    runner = run if run is not None else subprocess.run
    cert_dir.mkdir(parents=True, exist_ok=True)
    key_path = cert_dir / xxdb.KEY_FILE
    cert_path = cert_dir / xxdb.CERT_FILE
    if key_path.exists() and cert_path.exists():
        fingerprint = xxdb.cert_fingerprint(
            ssl.PEM_cert_to_DER_cert(cert_path.read_text(encoding='utf-8')))
        _tighten(key_path)
        _tighten(cert_path)
        return key_path, cert_path, fingerprint
    if shutil.which('openssl') is None:
        raise DaemonError('openssl is required to create the xxdb '
                          'certificate but was not found')
    result = runner(
        ['openssl', 'req', '-x509', '-newkey', 'rsa:2048',
         '-keyout', str(key_path), '-out', str(cert_path),
         '-days', '825', '-nodes', '-subj', f'/CN={common_name}'],
        capture_output=True, text=True, timeout=120)
    if result.returncode != 0 or not (key_path.exists() and cert_path.exists()):
        raise DaemonError('openssl could not create the xxdb certificate')
    _tighten(key_path)
    _tighten(cert_path)
    fingerprint = xxdb.cert_fingerprint(
        ssl.PEM_cert_to_DER_cert(cert_path.read_text(encoding='utf-8')))
    return key_path, cert_path, fingerprint


def _tighten(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _peer_fingerprint(tls_socket: ssl.SSLSocket) -> tuple[str | None, bytes | None]:
    try:
        der = tls_socket.getpeercert(binary_form=True)
    except (ValueError, ssl.SSLError):
        return None, None
    if not der:
        return None, None
    return xxdb.cert_fingerprint(bytes(der)), bytes(der)


def _stream_key_ok(request_id) -> bool:
    return isinstance(request_id, (int, str)) and not isinstance(
        request_id, bool)


def _session_members(sid: int, proc_root: str = '/proc') -> list[int]:
    try:
        names = os.listdir(proc_root)
    except OSError:
        return []
    members = []
    for name in names:
        if not name.isdigit():
            continue
        try:
            if os.getsid(int(name)) == sid:
                members.append(int(name))
        except OSError:
            continue
    return members


class _Pty:
    """One interactive shell: its own session (pty.fork calls setsid). A
    hangup reaches its process group and every process in its session,
    including background jobs that job control moved to groups of their
    own."""

    def __init__(self, pid: int, fd: int) -> None:
        self.pid = pid
        self.fd = fd
        self.stop = threading.Event()
        self._lock = threading.Lock()
        self._reaped = False

    def hangup(self) -> None:
        self.stop.set()
        self._signal(signal.SIGHUP)

    def _signal(self, sig) -> None:
        # Only while the shell is unreaped: until then its pid (= the
        # session and group id) cannot be reused by another process.
        with self._lock:
            if self._reaped:
                return
            try:
                os.killpg(self.pid, sig)
            except (ProcessLookupError, PermissionError):
                pass
            for pid in _session_members(self.pid):
                try:
                    os.kill(pid, sig)
                except (ProcessLookupError, PermissionError):
                    pass

    def _wait(self, block: bool) -> tuple[bool, int | None]:
        try:
            pid, status = os.waitpid(self.pid, 0 if block else os.WNOHANG)
        except ChildProcessError:
            with self._lock:
                self._reaped = True
            return True, None
        if pid == 0:
            return False, None
        with self._lock:
            self._reaped = True
        return True, os.waitstatus_to_exitcode(status)

    def reap(self, grace: float = 2.0) -> int | None:
        """Hang up the process group (what a closing terminal does), wait
        for the shell, SIGKILL the group after `grace`. Exit code back."""
        self._signal(signal.SIGHUP)
        deadline = time.monotonic() + grace
        done, code = self._wait(block=False)
        while not done and time.monotonic() < deadline:
            time.sleep(0.05)
            done, code = self._wait(block=False)
        if not done:
            self._signal(signal.SIGKILL)
            done, code = self._wait(block=True)
        return code


class _Session:
    """One connection. Stream state (PTYs, log follows) lives here, keyed
    by the request id this connection chose, so connections never meet."""

    def __init__(self, addr: str, conn=None) -> None:
        self.addr = addr
        self.conn = conn
        self.tls_fp: str | None = None
        self.tls_der: bytes | None = None
        self.paired_fp: str | None = None
        self.name = addr
        self.authed = False
        self.announced = False
        self.closed = False
        self.send = None
        self.lock = threading.Lock()
        self.ptys: dict = {}
        self.follows: dict = {}

    def fingerprints(self) -> set[str]:
        return {fp for fp in (self.tls_fp, self.paired_fp) if fp}

    def emit(self, obj: dict) -> None:
        sink = self.send
        if sink is None or self.closed:
            return
        try:
            sink(obj)
        except (OSError, ValueError, TypeError, ssl.SSLError):
            pass

    def end_streams(self) -> None:
        with self.lock:
            ptys = list(self.ptys.values())
            follows = list(self.follows.values())
        for entry in ptys:
            entry.hangup()
        for stop in follows:
            stop.set()

    def close(self) -> None:
        self.closed = True
        self.end_streams()
        conn = self.conn
        if conn is not None:
            try:
                socket.socket.shutdown(conn, socket.SHUT_RDWR)
            except (OSError, ValueError):
                pass


def _default_shell_runner(argv: list[str], timeout: int = 120) -> tuple[int, str, str]:
    result = subprocess.run(argv, capture_output=True, text=True,
                            timeout=timeout, check=False)
    return result.returncode, result.stdout[-1_000_000:], result.stderr[-1_000_000:]


def _default_screenshot() -> bytes:
    env = dict(os.environ)
    if 'WAYLAND_DISPLAY' not in env:
        runtime = env.get('XDG_RUNTIME_DIR', '')
        if runtime and os.path.exists(os.path.join(runtime, 'wayland-1')):
            env['WAYLAND_DISPLAY'] = 'wayland-1'
    try:
        result = subprocess.run(['grim', '-'], capture_output=True,
                                timeout=30, env=env, check=False)
    except FileNotFoundError:
        raise DaemonError(
            'grim is not installed: screenshots unavailable') from None
    if result.returncode != 0 or not result.stdout:
        raise DaemonError('grim failed to capture the screen')
    return result.stdout


def _default_ipc_forward(verb: str) -> None:
    runtime = os.environ.get('XDG_RUNTIME_DIR') or str(
        Path.home() / '.local' / 'share' / 'xx-wm')
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(2.0)
        sock.connect(os.path.join(runtime, 'xx-wm.sock'))
        sock.sendall(verb.encode('utf-8'))
    finally:
        sock.close()


def _default_sudo_check() -> bool:
    try:
        return subprocess.run(['sudo', '-n', 'true'], capture_output=True,
                              timeout=10, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _is_shell_process(proc_root: str, pid: int) -> bool:
    try:
        cmdline = Path(proc_root, str(pid), 'cmdline').read_bytes()
    except OSError:
        return False
    text = ' '.join(p.decode(errors='replace') for p in cmdline.split(b'\0'))
    return 'python' in text and SHELL_MAIN_SUFFIX in text


def _default_shell_restarter(proc_root: str = '/proc', self_pid=None,
                             kill=os.kill):
    """(pid, fire): the shell's python process and the SIGUSR1 that makes
    the /usr/bin/xx-wm supervisor respawn it. When the daemon runs inside
    the shell, that is this process; `fire` runs after the reply is sent."""
    me = os.getpid() if self_pid is None else self_pid
    if _is_shell_process(proc_root, me):
        target = me
    else:
        try:
            names = os.listdir(proc_root)
        except OSError:
            names = []
        matches = [int(name) for name in names
                   if name.isdigit() and int(name) != me
                   and _is_shell_process(proc_root, int(name))]
        if len(matches) != 1:
            raise DaemonError(
                f'no unique shell process to restart (found {len(matches)})')
        target = matches[0]
    return target, lambda: kill(target, signal.SIGUSR1)


def _default_input(action: str, params: dict) -> tuple[bool, str]:
    import xxdb_input
    return xxdb_input.inject(action, params)


def _tail_lines(path: Path, limit: int) -> list[str]:
    try:
        with open(path, 'rb') as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 262_144))
            data = handle.read()
    except OSError:
        return []
    lines = data.decode('utf-8', errors='replace').splitlines()
    return lines[-limit:]


def _journalctl_snapshot(lines: int) -> list[str]:
    if shutil.which('journalctl') is None:
        return []
    try:
        result = subprocess.run(
            ['journalctl', '--user', '-n', str(lines), '--no-pager',
             '--output=short'],
            capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return result.stdout.splitlines()


class XxdbDaemon:
    def __init__(self, cert_dir: Path | None = None,
                 trust_store: xxdb.TrustStore | None = None,
                 allow_loopback: bool = False,
                 shell_runner=None,
                 screenshot_provider=None,
                 shell_log_path: Path | None = None,
                 stderr_log_path: Path | None = None,
                 ipc_forwarder=None,
                 input_injector=None,
                 sudo_check=None,
                 shell_restarter=None,
                 install_dest: str = INSTALL_DEST,
                 on_connect=None,
                 on_disconnect=None,
                 on_activity=None,
                 on_system_request=None,
                 is_locked=None,
                 allow_while_locked=None,
                 clock=None) -> None:
        self.cert_dir = cert_dir if cert_dir is not None else xxdb.data_dir()
        self.trust = trust_store if trust_store is not None else xxdb.TrustStore(
            max_age_s=xxdb.trust_max_age_s(xxdb.DEFAULT_TRUST_DAYS))
        self.allow_loopback = allow_loopback
        self.shell_runner = shell_runner or _default_shell_runner
        self.screenshot_provider = screenshot_provider or _default_screenshot
        default_log = Path.home() / '.local' / 'share' / 'xx-wm' / 'shell.log'
        self.shell_log_path = shell_log_path or default_log
        default_err = Path.home() / '.local' / 'share' / 'xx-wm' / 'stderr.log'
        self.stderr_log_path = stderr_log_path or default_err
        self.ipc_forwarder = ipc_forwarder or _default_ipc_forward
        self.input_injector = input_injector or _default_input
        self.sudo_check = sudo_check or _default_sudo_check
        self.shell_restarter = shell_restarter or _default_shell_restarter
        self.install_dest = install_dest
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.on_activity = on_activity
        self.on_system_request = on_system_request
        self.is_locked = is_locked or (lambda: False)
        self.allow_while_locked = allow_while_locked or (lambda: False)
        self._clock = clock
        self.pairing = xxdb.PairingSession()
        self.system_access = xxdb.SystemAccess()
        self.port = 0
        self.addrs: list[str] = []
        self.phone_fp = ''
        self._sockets: list[socket.socket] = []
        self._running = False
        self._stopped = False
        self._threads: list[threading.Thread] = []
        self._conn_lock = threading.Lock()
        self._sessions: set[_Session] = set()
        self._open = 0
        self._unauth = 0
        self._avahi_proc = None
        self._home = str(Path.home())

    def _now(self) -> float:
        return self._clock() if self._clock is not None else time.time()

    def start(self) -> tuple[list[str], int]:
        check_not_root()
        _key, _cert, self.phone_fp = ensure_cert(self.cert_dir)
        entries = xxdb.interface_entries()
        addrs = xxdb.select_bind_addresses(entries, self.allow_loopback)
        if not addrs:
            raise DaemonError('no Wi-Fi interface is up: refusing to bind '
                              'cellular or unknown interfaces')
        last_error = None
        for _attempt in range(5):
            if self._stopped:
                break
            port = xxdb.pick_port()
            self._running = True
            try:
                self._bind_all(addrs, port)
            except OSError as error:
                self._running = False
                last_error = error
                continue
            self.addrs = addrs
            self.port = port
            if self._stopped:
                self.stop()
                break
            self._advertise()
            _log.info('xxdb listening on %s port %d',
                      ','.join(addrs), port)
            return addrs, port
        if self._stopped:
            raise DaemonError('wireless debugging was turned off')
        raise DaemonError(f'could not bind a port in '
                          f'{xxdb.PORT_MIN}-{xxdb.PORT_MAX}: {last_error}')

    def _bind_all(self, addrs: list[str], port: int) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.cert_dir / xxdb.CERT_FILE),
                                str(self.cert_dir / xxdb.KEY_FILE))
        context.verify_mode = ssl.CERT_OPTIONAL
        context.check_hostname = False
        self._ssl_context = context
        for entry in self.trust.list():
            self._trust_host_cert(entry.get('cert_pem', ''))
        sockets = []
        try:
            for addr in addrs:
                raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                raw.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                raw.bind((addr, port))
                raw.listen(4)
                sockets.append(raw)
        except BaseException:
            for sock in sockets:
                try:
                    sock.close()
                except OSError:
                    pass
            raise
        self._sockets = sockets
        for sock in sockets:
            thread = threading.Thread(target=self._accept_loop, args=(sock,),
                                      daemon=True)
            thread.start()
            self._threads.append(thread)

    def _advertise(self) -> None:
        if shutil.which('avahi-publish-service') is None:
            return
        import socket as _socket
        try:
            proc = subprocess.Popen(
                ['avahi-publish-service', f'xxdb-{_socket.gethostname()}',
                 '_xxdb._tcp', str(self.port)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._avahi_proc = proc
        except OSError:
            self._avahi_proc = None

    def _trust_host_cert(self, cert_pem: str) -> None:
        """Pin one more host certificate into the live TLS context so its
        client-cert handshake verifies. The context only ever gains pins:
        forgetting a host removes it from the trust store (RPC auth fails
        closed, and its live sessions are dropped) and the pin dies with
        the daemon on the next disable."""
        context = getattr(self, '_ssl_context', None)
        if context is None or not cert_pem:
            return
        try:
            context.load_verify_locations(cadata=cert_pem)
        except (ssl.SSLError, ValueError):
            pass

    def stop(self) -> None:
        """Off means off: listeners, live sessions and their shells."""
        self._stopped = True
        self._running = False
        for sock in self._sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        self._sockets = []
        with self._conn_lock:
            sessions = list(self._sessions)
        for session in sessions:
            session.close()
        proc, self._avahi_proc = self._avahi_proc, None
        if proc is not None:
            try:
                proc.terminate()
            except OSError:
                pass

    def drop_host(self, fingerprint: str) -> int:
        """Close every live session of one host (after it was forgotten)."""
        with self._conn_lock:
            sessions = [s for s in self._sessions
                        if fingerprint in s.fingerprints()]
        for session in sessions:
            session.close()
        return len(sessions)

    def live_sessions(self) -> int:
        with self._conn_lock:
            return len(self._sessions)

    def _callback(self, callback, *args) -> bool:
        if callback is None:
            return False
        try:
            callback(*args)
        except Exception:
            _log.exception('xxdb callback failed')
            return False
        return True

    def _accept_loop(self, sock: socket.socket) -> None:
        while self._running:
            try:
                conn, addr = sock.accept()
            except OSError:
                return
            with self._conn_lock:
                admit = (self._running
                         and self._open < xxdb.MAX_CONNECTIONS
                         and self._unauth < xxdb.MAX_UNAUTH_CONNECTIONS)
                if admit:
                    self._open += 1
                    self._unauth += 1
            if not admit:
                try:
                    conn.close()
                except OSError:
                    pass
                continue
            thread = threading.Thread(target=self._serve,
                                      args=(conn, addr), daemon=True)
            thread.start()

    def _serve(self, raw: socket.socket, addr) -> None:
        session = _Session(addr[0])
        try:
            self._run_session(raw, session)
        finally:
            with self._conn_lock:
                self._open -= 1
                if not session.authed:
                    self._unauth -= 1
                self._sessions.discard(session)
            session.close()
            for sock in (session.conn, raw):
                if sock is None:
                    continue
                try:
                    sock.close()
                except OSError:
                    pass
            _log.info('xxdb disconnect from %s', session.addr)
            if session.announced:
                self._callback(self.on_disconnect, self._display_name(session))

    def _run_session(self, raw: socket.socket, session: _Session) -> None:
        raw.settimeout(xxdb.HANDSHAKE_TIMEOUT_S)
        try:
            conn = self._ssl_context.wrap_socket(raw, server_side=True)
        except (OSError, ssl.SSLError, ValueError):
            return
        session.conn = conn
        with self._conn_lock:
            if not self._running:
                return
            self._sessions.add(session)
        session.tls_fp, session.tls_der = _peer_fingerprint(conn)
        reader = FrameReader(xxdb.PREAUTH_MAX_LINE_BYTES)
        send_lock = threading.Lock()

        def _send(obj: dict) -> None:
            with send_lock:
                conn.sendall(xxdb.encode_message(obj))

        session.send = _send
        deadline = time.monotonic() + xxdb.UNAUTH_LIFETIME_S
        trusted = self._authorized(session)
        _log.info('xxdb connection from %s (trusted=%s)', session.addr,
                  trusted)
        if trusted:
            self._authorize_session(session, reader)
        while self._running and not session.closed:
            if not session.authed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                conn.settimeout(remaining)
            try:
                chunk = conn.recv(65536)
            except (OSError, ssl.SSLError):
                return
            if not chunk:
                return
            try:
                messages = reader.feed(chunk)
            except xxdb.FrameError:
                return
            for message in messages:
                if session.authed:
                    self._callback(self.on_activity)
                for response in self.handle_request(session, message):
                    try:
                        _send(response)
                    except (OSError, ssl.SSLError):
                        return
                if not session.authed and self._authorized(session):
                    self._authorize_session(session, reader)

    def _authorize_session(self, session: _Session, reader) -> None:
        reader.max_line = xxdb.MAX_LINE_BYTES
        with self._conn_lock:
            if not session.authed:
                session.authed = True
                self._unauth -= 1
        try:
            session.conn.settimeout(None)
        except (OSError, AttributeError):
            pass
        for fingerprint in session.fingerprints():
            self.trust.touch(fingerprint, self._now())
        session.announced = self._callback(self.on_connect,
                                           self._display_name(session))

    def _display_name(self, session: _Session) -> str:
        for fingerprint in (session.paired_fp, session.tls_fp):
            if fingerprint:
                name = self.trust.name_of(fingerprint)
                if name:
                    return name
        return session.addr

    def _authorized(self, session: _Session) -> bool:
        return ((session.tls_fp is not None and self.trust.contains(session.tls_fp))
                or (session.paired_fp is not None
                    and self.trust.contains(session.paired_fp)))

    def _locked(self) -> bool:
        try:
            if self.allow_while_locked():
                return False
            return bool(self.is_locked())
        except Exception:
            _log.exception('xxdb lock check failed')
            return True

    def handle_request(self, session: _Session, message: dict):
        request_id = message.get('id')
        cmd = message.get('cmd')

        def _refused(error: str) -> dict:
            return {'id': request_id, 'ok': False, 'error': error}

        if not isinstance(cmd, str):
            yield _refused('bad-request')
            return
        _log.info('xxdb cmd %s id=%r from %s', cmd, request_id, session.addr)
        if cmd not in xxdb.PUBLIC_COMMANDS and not self._authorized(session):
            yield _refused('not-paired: pair this host first')
            return
        if cmd not in xxdb.LOCKED_COMMANDS and self._locked():
            yield _refused('locked: unlock the phone first')
            return
        handler = getattr(self, f'_cmd_{cmd.replace("-", "_")}', None)
        if handler is None:
            yield _refused('unknown-command')
            return
        try:
            yield from handler(session, request_id, message)
        except DaemonError as error:
            yield _refused(str(error))
        except (OSError, ValueError, KeyError, struct.error) as error:
            yield _refused(f'failed: {error}')

    def _cmd_pair(self, session: _Session, request_id, message):
        host_pem = str(message.get('host_cert_pem', ''))
        host_name = str(message.get('host_name', session.addr))[:64]
        mac_hex = str(message.get('mac', ''))
        try:
            host_der = (bytes(session.tls_der) if session.tls_der
                        else ssl.PEM_cert_to_DER_cert(host_pem))
        except (ValueError, ssl.SSLError):
            yield {'id': request_id, 'ok': False,
                   'error': 'unreadable host certificate'}
            return
        if not self.pairing.code:
            yield {'id': request_id, 'ok': False,
                   'error': 'no active pairing code: ask the phone for one'}
            return
        fingerprint = xxdb.cert_fingerprint(host_der)
        ok, detail = self.pairing.attempt(mac_hex, self.phone_fp,
                                          fingerprint, self._now())
        if not ok:
            yield {'id': request_id, 'ok': False, 'error': detail}
            return
        stored_pem = host_pem
        if session.tls_der:
            try:
                stored_pem = ssl.DER_cert_to_PEM_cert(bytes(session.tls_der))
            except (ValueError, ssl.SSLError):
                pass
        self.trust.add(fingerprint, host_name or session.addr,
                       stored_pem, self._now())
        self._trust_host_cert(stored_pem)
        session.paired_fp = fingerprint
        session.name = host_name or session.addr
        _log.info('xxdb paired host %s fp=%s', session.name,
                  fingerprint[:16])
        yield {'id': request_id, 'ok': True, 'phone_fp': self.phone_fp,
               'host_fp': fingerprint}

    def _cmd_hello(self, session: _Session, request_id, _message):
        import getpass
        yield {'id': request_id, 'ok': True, 'server': 'xxdb',
               'version': VERSION, 'user': getpass.getuser()}

    def _cmd_shell(self, session: _Session, request_id, message):
        if 'argv' in message:
            argv = message['argv']
            if (not isinstance(argv, list) or not argv
                    or len(argv) > 64
                    or not all(isinstance(a, str) for a in argv)):
                yield {'id': request_id, 'ok': False,
                       'error': 'argv must be a non-empty list of strings'}
                return
            try:
                returncode, out, err = self.shell_runner(argv)
            except subprocess.TimeoutExpired:
                yield {'id': request_id, 'ok': False, 'error': 'timed out'}
                return
            except (OSError, ValueError) as error:
                yield {'id': request_id, 'ok': False, 'error': str(error)}
                return
            yield {'id': request_id, 'ok': True, 'returncode': returncode,
                   'stdout': out, 'stderr': err}
            return
        if message.get('interactive'):
            yield from self._pty_spawn(session, request_id)
            return
        if 'input' in message or message.get('close'):
            yield from self._pty_write(session, request_id, message)
            return
        yield {'id': request_id, 'ok': False,
               'error': 'shell needs argv or interactive:true'}

    def _pty_spawn(self, session: _Session, request_id):
        import fcntl
        if not _stream_key_ok(request_id):
            yield {'id': request_id, 'ok': False,
                   'error': 'an interactive shell needs a number or string id'}
            return
        with session.lock:
            busy = request_id in session.ptys
        if busy:
            yield {'id': request_id, 'ok': False,
                   'error': 'an interactive shell already uses this id'}
            return
        argv = [os.environ.get('SHELL', '/bin/sh')]
        pid, fd = pty.fork()
        if pid == 0:
            try:
                os.execvp(argv[0], argv)
            finally:
                os._exit(127)
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        entry = _Pty(pid, fd)
        with session.lock:
            session.ptys[request_id] = entry
        if session.closed:
            entry.hangup()
        thread = threading.Thread(target=self._pump_pty,
                                  args=(session, request_id, entry),
                                  daemon=True)
        thread.start()
        yield {'id': request_id, 'ok': True, 'stream': 'pty-started'}

    def _pump_pty(self, session: _Session, request_id, entry: _Pty) -> None:
        while not entry.stop.is_set():
            try:
                ready, _w, _x = select.select([entry.fd], [], [], 0.5)
            except (OSError, ValueError):
                break
            if not ready:
                continue
            try:
                chunk = os.read(entry.fd, 65536)
            except BlockingIOError:
                continue
            except OSError:
                break
            if not chunk:
                break
            session.emit({'id': request_id, 'ok': True,
                          'stream': 'pty-output',
                          'data': chunk.decode('utf-8', errors='replace')})
        returncode = entry.reap()
        try:
            os.close(entry.fd)
        except OSError:
            pass
        with session.lock:
            if session.ptys.get(request_id) is entry:
                del session.ptys[request_id]
        session.emit({'id': request_id, 'ok': True, 'stream': 'pty-done',
                      'returncode': returncode})

    def _pty_write(self, session: _Session, request_id, message):
        entry = None
        if _stream_key_ok(request_id):
            with session.lock:
                entry = session.ptys.get(request_id)
        if entry is None:
            yield {'id': request_id, 'ok': False,
                   'error': 'no such interactive shell'}
            return
        if message.get('close'):
            entry.hangup()
            yield {'id': request_id, 'ok': True, 'stream': 'pty-ack'}
            return
        data = str(message.get('input', ''))
        try:
            os.write(entry.fd, data.encode())
        except OSError as error:
            yield {'id': request_id, 'ok': False, 'error': str(error)}
            return
        yield {'id': request_id, 'ok': True, 'stream': 'pty-ack'}

    def _cmd_logcat(self, session: _Session, request_id, message):
        try:
            limit = max(1, min(2000, int(message.get('lines', 200))))
        except (TypeError, ValueError):
            limit = 200
        follow = bool(message.get('follow', False))
        for line in _tail_lines(self.shell_log_path, limit):
            yield {'id': request_id, 'ok': True, 'stream': 'log',
                   'source': 'shell.log', 'line': line}
        for line in _tail_lines(self.stderr_log_path, limit):
            yield {'id': request_id, 'ok': True, 'stream': 'log',
                   'source': 'stderr.log', 'line': line}
        for line in _journalctl_snapshot(limit)[-limit:]:
            yield {'id': request_id, 'ok': True, 'stream': 'log',
                   'source': 'journal', 'line': line}
        if not follow:
            yield {'id': request_id, 'ok': True, 'stream': 'log-done'}
            return
        stop = threading.Event()
        key = object()
        with session.lock:
            session.follows[key] = stop
        if session.closed:
            stop.set()
        yield {'id': request_id, 'ok': True, 'stream': 'log-follow'}
        thread = threading.Thread(target=self._follow_log,
                                  args=(session, request_id, key, stop),
                                  daemon=True)
        thread.start()

    def _follow_log(self, session: _Session, request_id, key, stop) -> None:
        try:
            size = self.shell_log_path.stat().st_size
        except OSError:
            size = 0
        while not stop.is_set():
            stop.wait(0.5)
            if stop.is_set():
                break
            try:
                current = self.shell_log_path.stat().st_size
            except OSError:
                continue
            if current < size:
                size = 0
            if current <= size:
                continue
            try:
                with open(self.shell_log_path, 'rb') as handle:
                    handle.seek(size)
                    data = handle.read(262_144)
            except OSError:
                continue
            size = current
            for line in data.decode('utf-8', errors='replace').splitlines():
                session.emit({'id': request_id, 'ok': True, 'stream': 'log',
                              'source': 'shell.log', 'line': line})
        with session.lock:
            session.follows.pop(key, None)

    def _cmd_screenshot(self, session: _Session, request_id, _message):
        del session
        try:
            png = self.screenshot_provider()
        except DaemonError as error:
            yield {'id': request_id, 'ok': False, 'error': str(error)}
            return
        yield {'id': request_id, 'ok': True, 'format': 'png',
               'data': base64.b64encode(png).decode('ascii')}

    def _resolve_push_path(self, message,
                           op: str) -> tuple[Path | None, str, str | None]:
        raw = str(message.get('path', ''))
        if not raw:
            return None, 'missing path', None
        inside = xxdb.check_inside_home(raw, self._home)
        if inside is not None:
            return inside, '', None
        if not message.get('system'):
            return None, (f'refused: {raw} is outside $HOME '
                          '(retry with system:true and confirm on the phone)'), None
        candidate = Path(os.path.expanduser(raw))
        if not candidate.is_absolute():
            return None, 'system paths must be absolute', None
        resolved = Path(os.path.realpath(candidate))
        bound = f'{op}:{resolved}'
        token = message.get('token')
        token = str(token) if token else None
        now = self._now()
        if self.system_access.consume(token, now, bound=bound) is not None:
            return resolved, '', None
        if not self.system_access.waiting(token, bound, now):
            verb = 'Write' if op == 'push' else 'Read'
            token = self.system_access.request(f'{verb} {resolved}', now,
                                               bound=bound)
            self._callback(self.on_system_request)
        return None, 'needs-confirm', token

    def _cmd_push(self, session: _Session, request_id, message):
        del session
        target, error, token = self._resolve_push_path(message, 'push')
        if target is None:
            response = {'id': request_id, 'ok': False, 'error': error}
            if token:
                response['token'] = token
            yield response
            return
        data_b64 = str(message.get('data', ''))
        if len(data_b64) * 3 // 4 > xxdb.MAX_FILE_BYTES + 16:
            yield {'id': request_id, 'ok': False,
                   'error': 'file exceeds the 512 MB limit'}
            return
        try:
            data = base64.b64decode(data_b64, validate=True)
        except (ValueError, base64.binascii.Error):
            yield {'id': request_id, 'ok': False, 'error': 'bad base64 data'}
            return
        if len(data) > xxdb.MAX_FILE_BYTES:
            yield {'id': request_id, 'ok': False,
                   'error': 'file exceeds the 512 MB limit'}
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        except OSError as error:
            yield {'id': request_id, 'ok': False, 'error': str(error)}
            return
        yield {'id': request_id, 'ok': True, 'bytes': len(data),
               'path': str(target)}

    def _cmd_pull(self, session: _Session, request_id, message):
        del session
        target, error, token = self._resolve_push_path(message, 'pull')
        if target is None:
            response = {'id': request_id, 'ok': False, 'error': error}
            if token:
                response['token'] = token
            yield response
            return
        try:
            size = target.stat().st_size
        except OSError as error:
            yield {'id': request_id, 'ok': False, 'error': str(error)}
            return
        if size > xxdb.MAX_FILE_BYTES:
            yield {'id': request_id, 'ok': False,
                   'error': 'file exceeds the 512 MB limit'}
            return
        try:
            data = target.read_bytes()
        except OSError as error:
            yield {'id': request_id, 'ok': False, 'error': str(error)}
            return
        yield {'id': request_id, 'ok': True, 'size': len(data),
               'data': base64.b64encode(data).decode('ascii')}

    def _cmd_install(self, session: _Session, request_id, message):
        del session
        files = message.get('files')
        if not isinstance(files, dict) or not files:
            yield {'id': request_id, 'ok': False,
                   'error': 'install needs a files object'}
            return
        total = sum(len(str(v)) for v in files.values()) * 3 // 4
        if total > xxdb.MAX_FILE_BYTES:
            yield {'id': request_id, 'ok': False,
                   'error': 'tree exceeds the 512 MB limit'}
            return
        rels: list[str] = []
        for rel in files:
            if (not isinstance(rel, str) or rel.startswith('/')
                    or '..' in Path(rel).parts):
                yield {'id': request_id, 'ok': False,
                       'error': f'refused: bad tree path {rel!r}'}
                return
            rels.append(rel)
        if not self.sudo_check():
            yield {'id': request_id, 'ok': False,
                   'error': ('refused: installing into /usr/share/xx-wm needs '
                             'root, and passwordless sudo is not configured '
                             '(sudo -n true failed)')}
            return
        stage = Path(tempfile.mkdtemp(prefix='xxdb-install-'))
        try:
            for rel in rels:
                try:
                    data = base64.b64decode(str(files[rel]), validate=True)
                except (ValueError, base64.binascii.Error):
                    yield {'id': request_id, 'ok': False,
                           'error': f'bad base64 data for {rel!r}'}
                    return
                dest = stage / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
            # The datadir stays root-owned and world-readable: rsync -a
            # alone would copy the staging owner and its 0700 mode onto it.
            rsync = shutil.which('rsync')
            if rsync:
                cmd = ['sudo', '-n', 'rsync', '-a', '--chown=root:root',
                       '--chmod=D755,F644', f'{stage}/',
                       f'{self.install_dest}/']
            else:
                for path in stage.rglob('*'):
                    os.chmod(path, 0o755 if path.is_dir() else 0o644)
                cmd = ['sudo', '-n', 'cp', '-R'] + [
                    str(p) for p in stage.iterdir()] + [self.install_dest + '/']
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=300, check=False)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or '').strip()
                yield {'id': request_id, 'ok': False,
                       'error': f'install failed: {detail}'}
                return
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        yield {'id': request_id, 'ok': True, 'count': len(rels)}

    def _cmd_restart_shell(self, session: _Session, request_id, _message):
        del session
        try:
            result = self.shell_restarter()
        except DaemonError as error:
            yield {'id': request_id, 'ok': False, 'error': str(error)}
            return
        pid, fire = result if isinstance(result, tuple) else (result, None)
        yield {'id': request_id, 'ok': True, 'pid': pid}
        if fire is not None:
            timer = threading.Timer(RESTART_DELAY_S, fire)
            timer.daemon = True
            timer.start()

    def _cmd_ipc(self, session: _Session, request_id, message):
        del session
        verb = str(message.get('verb', ''))
        if verb not in _IPC_VERBS:
            yield {'id': request_id, 'ok': False,
                   'error': f'unknown ipc verb {verb!r}'}
            return
        try:
            self.ipc_forwarder(verb)
        except OSError as error:
            yield {'id': request_id, 'ok': False,
                   'error': f'shell is not listening: {error}'}
            return
        yield {'id': request_id, 'ok': True, 'verb': verb}

    def _cmd_input(self, session: _Session, request_id, message):
        del session
        action = str(message.get('action', ''))
        params = {k: v for k, v in message.items()
                  if k not in ('id', 'cmd', 'action')}
        ok, detail = self.input_injector(action, params)
        yield {'id': request_id, 'ok': ok,
               ('message' if ok else 'error'): detail}
