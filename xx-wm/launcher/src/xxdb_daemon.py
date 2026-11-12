"""xxdb phone daemon: TLS-over-TCP JSON-lines RPC server.

Stdlib only. The daemon never runs as root, binds Wi-Fi addresses only,
and refuses every command from untrusted (unpaired) clients.
"""
from __future__ import annotations

import base64
import logging
import os
import pty
import shutil
import signal
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
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


class _Session:
    def __init__(self, addr: str) -> None:
        self.addr = addr
        self.tls_fp: str | None = None
        self.tls_der: bytes | None = None
        self.paired_fp: str | None = None
        self.name = addr


def _default_shell_runner(argv: list[str], timeout: int = 120) -> tuple[int, str, str]:
    result = subprocess.run(argv, capture_output=True, text=True,
                            timeout=timeout)
    return result.returncode, result.stdout[-1_000_000:], result.stderr[-1_000_000:]


def _default_screenshot() -> bytes:
    env = dict(os.environ)
    if 'WAYLAND_DISPLAY' not in env:
        runtime = env.get('XDG_RUNTIME_DIR', '')
        if runtime and os.path.exists(os.path.join(runtime, 'wayland-1')):
            env['WAYLAND_DISPLAY'] = 'wayland-1'
    try:
        result = subprocess.run(['grim', '-'], capture_output=True,
                                timeout=30, env=env)
    except FileNotFoundError:
        raise DaemonError('grim is not installed: screenshots unavailable')
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
                              timeout=10).returncode == 0
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
            capture_output=True, text=True, timeout=15)
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
                 clock=None) -> None:
        self.cert_dir = cert_dir if cert_dir is not None else xxdb.data_dir()
        self.trust = trust_store if trust_store is not None else xxdb.TrustStore()
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
        self._clock = clock
        self.pairing = xxdb.PairingSession()
        self.system_access = xxdb.SystemAccess()
        self.port = 0
        self.addrs: list[str] = []
        self.phone_fp = ''
        self._sockets: list[socket.socket] = []
        _run = False
        self._running = _run
        self._threads: list[threading.Thread] = []
        self._follow_stops: dict[int, threading.Event] = {}
        self._pty_sessions: dict = {}
        self._stream_sinks: dict = {}
        self._sink_lock = threading.Lock()
        self._avahi_proc = None
        self._home = str(Path.home())

    def _now(self) -> float:
        import time
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
            self._advertise()
            _log.info('xxdb listening on %s port %d',
                      ','.join(addrs), port)
            return addrs, port
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
        closed) and the pin dies with the daemon on the next disable."""
        context = getattr(self, '_ssl_context', None)
        if context is None or not cert_pem:
            return
        try:
            context.load_verify_locations(cadata=cert_pem)
        except (ssl.SSLError, ValueError):
            pass

    def stop(self) -> None:
        self._running = False
        for sock in self._sockets:
            try:
                sock.close()
            except OSError:
                pass
        self._sockets = []
        for stop in list(self._follow_stops.values()):
            stop.set()
        proc, self._avahi_proc = self._avahi_proc, None
        if proc is not None:
            try:
                proc.terminate()
            except OSError:
                pass

    def _accept_loop(self, sock: socket.socket) -> None:
        while self._running:
            try:
                conn, addr = sock.accept()
            except OSError:
                return
            try:
                tls_conn = self._ssl_context.wrap_socket(conn, server_side=True)
            except (OSError, ssl.SSLError):
                try:
                    conn.close()
                except OSError:
                    pass
                continue
            thread = threading.Thread(target=self._serve,
                                      args=(tls_conn, addr), daemon=True)
            thread.start()
            self._threads.append(thread)

    def _serve(self, conn: ssl.SSLSocket, addr) -> None:
        session = _Session(addr[0])
        try:
            session.tls_fp, session.tls_der = _peer_fingerprint(conn)
        except (OSError, ValueError):
            pass
        _log.info('xxdb connection from %s (trusted=%s)', session.addr,
                  self._authorized(session))
        if self.on_connect is not None:
            try:
                self.on_connect(self._display_name(session))
            except (TypeError, ValueError):
                pass
        reader = FrameReader()
        send_lock = threading.Lock()
        owned_streams: list = []

        def _send(obj: dict) -> None:
            with send_lock:
                conn.sendall(xxdb.encode_message(obj))

        try:
            while self._running:
                try:
                    chunk = conn.recv(65536)
                except (OSError, ssl.SSLError):
                    break
                if not chunk:
                    break
                try:
                    messages = reader.feed(chunk)
                except xxdb.FrameError:
                    break
                for message in messages:
                    if self.on_activity is not None:
                        try:
                            self.on_activity()
                        except (TypeError, ValueError):
                            pass
                    for response in self.handle_request(session, message):
                        try:
                            _send(response)
                        except (OSError, ssl.SSLError):
                            return
                        if (isinstance(response, dict)
                                and response.get('stream')
                                in ('pty-started', 'log-follow')):
                            with self._sink_lock:
                                self._stream_sinks[response.get('id')] = _send
                            owned_streams.append(response.get('id'))
        finally:
            with self._sink_lock:
                for key in owned_streams:
                    self._stream_sinks.pop(key, None)
            for stop in [self._follow_stops.pop(key, None)
                         for key in list(self._follow_stops)
                         if key in owned_streams or key[0] in owned_streams]:
                if stop is not None:
                    stop.set()
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass
            _log.info('xxdb disconnect from %s', session.addr)
            if self.on_disconnect is not None:
                try:
                    self.on_disconnect(self._display_name(session))
                except (TypeError, ValueError):
                    pass

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
        host_pem = message.get('host_cert_pem', '')
        host_name = str(message.get('host_name', session.addr))[:64]
        mac_hex = str(message.get('mac', ''))
        code_guess = str(message.get('code', ''))
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
        ok, detail = self.pairing.attempt(code_guess, mac_hex,
                                          self.phone_fp, host_der, self._now())
        if not ok:
            yield {'id': request_id, 'ok': False, 'error': detail}
            return
        fingerprint = xxdb.cert_fingerprint(host_der)
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
        del session
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
            yield from self._pty_spawn(request_id, message)
            return
        if 'input' in message or message.get('close'):
            yield from self._pty_write(request_id, message)
            return
        yield {'id': request_id, 'ok': False,
               'error': 'shell needs argv or interactive:true'}

    def _pty_spawn(self, request_id, message):
        import fcntl
        argv = message.get('argv') or [os.environ.get('SHELL', '/bin/sh')]
        if not isinstance(argv, list) or not argv:
            argv = [os.environ.get('SHELL', '/bin/sh')]
        pid, fd = pty.fork()
        if pid == 0:
            try:
                os.execvp(argv[0], list(argv))
            finally:
                os._exit(127)
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        stop = threading.Event()
        self._pty_sessions[request_id] = (pid, fd, stop)
        thread = threading.Thread(target=self._pump_pty,
                                  args=(request_id, pid, fd, stop),
                                  daemon=True)
        thread.start()
        yield {'id': request_id, 'ok': True, 'stream': 'pty-started'}

    def _pump_pty(self, request_id, pid, fd, stop) -> None:
        import errno
        import time
        while not stop.is_set():
            try:
                chunk = os.read(fd, 65536)
            except OSError as error:
                if error.errno in (errno.EAGAIN, errno.EIO):
                    if not self._pid_alive(pid):
                        break
                    time.sleep(0.05)
                    continue
                break
            if not chunk:
                break
            self._emit_stream(request_id, {'id': request_id, 'ok': True,
                                           'stream': 'pty-output',
                                           'data': chunk.decode(
                                               'utf-8', errors='replace')})
        try:
            os.close(fd)
        except OSError:
            pass
        self._pty_sessions.pop(request_id, None)
        self._emit_stream(request_id, {'id': request_id, 'ok': True,
                                       'stream': 'pty-done'})

    def _emit_stream(self, request_id, obj: dict) -> None:
        with self._sink_lock:
            sink = self._stream_sinks.get(request_id)
        if sink is None:
            return
        try:
            sink(obj)
        except (OSError, ValueError, TypeError, ssl.SSLError):
            pass

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _pty_write(self, request_id, message):
        entry = self._pty_sessions.get(request_id)
        if entry is None:
            yield {'id': request_id, 'ok': False,
                   'error': 'no such interactive shell'}
            return
        _pid, fd, stop = entry
        if message.get('close'):
            stop.set()
            yield {'id': request_id, 'ok': True, 'stream': 'pty-done'}
            return
        data = str(message.get('input', ''))
        try:
            os.write(fd, data.encode())
        except OSError as error:
            yield {'id': request_id, 'ok': False, 'error': str(error)}
            return
        yield {'id': request_id, 'ok': True, 'stream': 'pty-ack'}

    def _cmd_logcat(self, session: _Session, request_id, message):
        del session
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
        key = (request_id, id(stop))
        self._follow_stops[key] = stop
        yield {'id': request_id, 'ok': True, 'stream': 'log-follow'}
        thread = threading.Thread(target=self._follow_log,
                                  args=(request_id, key, stop), daemon=True)
        thread.start()

    def _follow_log(self, request_id, key, stop) -> None:
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
                self._emit_stream(request_id, {'id': request_id, 'ok': True,
                                               'stream': 'log',
                                               'source': 'shell.log',
                                               'line': line})
        self._follow_stops.pop(key, None)

    def _cmd_screenshot(self, session: _Session, request_id, _message):
        del session
        try:
            png = self.screenshot_provider()
        except DaemonError as error:
            yield {'id': request_id, 'ok': False, 'error': str(error)}
            return
        yield {'id': request_id, 'ok': True, 'format': 'png',
               'data': base64.b64encode(png).decode('ascii')}

    def _resolve_push_path(self, message) -> tuple[Path | None, str, str | None]:
        raw = str(message.get('path', ''))
        if not raw:
            return None, 'missing path', None
        inside = xxdb.check_inside_home(raw, self._home)
        if inside is not None:
            return inside, '', None
        if not message.get('system'):
            return None, (f'refused: {raw} is outside $HOME '
                          '(retry with system:true and confirm on the phone)'), None
        token = message.get('token')
        label = self.system_access.consume(token, self._now())
        if label is None:
            pending_token = self.system_access.request(raw, self._now())
            return None, 'needs-confirm', pending_token
        candidate = Path(raw)
        if not candidate.is_absolute():
            return None, 'system paths must be absolute', None
        return candidate, '', None

    def _cmd_push(self, session: _Session, request_id, message):
        del session
        target, error, token = self._resolve_push_path(message)
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
        target, error, token = self._resolve_push_path(message)
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
                                    timeout=300)
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

