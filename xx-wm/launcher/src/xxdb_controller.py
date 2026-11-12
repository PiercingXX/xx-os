"""xxdb controller: enable/disable state, pairing codes, idle auto-off.

GTK-free: the shell (window.py/main.py) drives it from the main loop and
renders its state. All blocking work runs in worker threads; results come
back through `post(fn)`, which is GLib.idle_add in the shell and a direct
call in tests. Daemon callbacks arrive on connection threads and also go
through `post`, so every state change and notification runs on the main
loop. Heavy daemon construction is injected via daemon_factory.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import xxdb

_log = logging.getLogger('piercing.xxdb')

NOTIF_ID = 999904
NOTIF_TITLE = 'Wireless debugging'
LOCK_PROBE_TIMEOUT_S = 2.0


def _default_daemon_factory(controller) -> object:
    from xxdb_daemon import XxdbDaemon
    return XxdbDaemon(
        cert_dir=controller.cert_dir,
        trust_store=controller.trust,
        **controller.daemon_callbacks(),
    )


class XxdbController:
    def __init__(self, config, post=None, clock=None,
                 home: Path | None = None,
                 daemon_factory=None,
                 on_notify=None,
                 on_unnotify=None) -> None:
        self.config = config
        self._post = post if post is not None else (lambda fn: fn())
        self._clock = clock
        self._home = home if home is not None else Path.home()
        self._daemon_factory = (daemon_factory if daemon_factory is not None
                                else _default_daemon_factory)
        self.on_notify = on_notify
        self.on_unnotify = on_unnotify
        self.lock_probe = None
        self.cert_dir = xxdb.data_dir(self._home)
        self.trust = xxdb.TrustStore(home=self._home,
                                     max_age_s=self._trust_max_age())
        self.daemon = None
        self.enabled = False
        self.starting = False
        self.error = ''
        self.last_activity = self._now()
        self.active_clients = 0
        self.client_names: set[str] = set()
        self.pairing_info: tuple[str, float] | None = None
        self._generation = 0

    def _now(self) -> float:
        import time
        return self._clock() if self._clock is not None else time.time()

    def _trust_max_age(self) -> float:
        return xxdb.trust_max_age_s(self.config.data.get(
            'xxdb_trust_days', xxdb.DEFAULT_TRUST_DAYS))

    def _run_bg(self, work, generation: int | None = None) -> None:
        thread = threading.Thread(target=self._guarded,
                                  args=(work, generation), daemon=True)
        thread.start()

    def _guarded(self, work, generation: int | None = None) -> None:
        try:
            work()
        except (OSError, ValueError, RuntimeError) as error:
            _log.error('xxdb background work failed: %s', error)
            message = str(error)
            self._post(lambda: self._fail(message, generation))

    def daemon_callbacks(self) -> dict:
        """Daemon callbacks bound to the current enable: a late callback
        from a daemon that was already switched off changes nothing."""
        generation = self._generation

        def _hop(fn):
            return lambda *args: self._post(
                lambda: fn(generation, *args))

        return {
            'on_connect': _hop(self._client_opened),
            'on_disconnect': _hop(self._client_closed),
            'on_activity': self._on_activity,
            'on_system_request': _hop(lambda _gen: self._state_changed()),
            'is_locked': self.locked_now,
            'allow_while_locked': self.allow_while_locked,
        }

    def allow_while_locked(self) -> bool:
        return bool(self.config.data.get('xxdb_allow_while_locked', False))

    def locked_now(self) -> bool:
        """Lock state for a connection thread: asks `lock_probe` on the
        main loop and waits for the answer. No answer counts as locked."""
        probe = self.lock_probe
        if probe is None:
            return False
        answer: list[bool] = []
        done = threading.Event()

        def _ask():
            try:
                answer.append(bool(probe()))
            except Exception:
                answer.append(True)
            done.set()
            return False

        if threading.current_thread() is threading.main_thread():
            _ask()
        else:
            self._post(_ask)
            if not done.wait(LOCK_PROBE_TIMEOUT_S):
                return True
        return answer[0]

    def ensure_boot_state(self) -> bool:
        """Apply the restart rule: off unless persistence is configured.

        Returns True when the shell should re-enable wireless debugging.
        """
        persistent = bool(self.config.data.get('xxdb_persistent', False))
        wanted = bool(self.config.data.get('xxdb_enabled', False))
        if not persistent:
            self.config.data['xxdb_enabled'] = False
            try:
                self.config.save()
            except OSError:
                pass
            return False
        return wanted

    def request_enable(self) -> None:
        if self.enabled or self.starting:
            return
        self._generation += 1
        generation = self._generation
        self.starting = True
        self.error = ''
        self.trust.max_age_s = self._trust_max_age()
        self._state_changed()
        self._run_bg(lambda: self._enable_work(generation), generation)

    def _enable_work(self, generation: int) -> None:
        daemon = self._daemon_factory(self)
        daemon.start()
        self._post(lambda: self._enabled(daemon, generation))

    def _current(self, generation: int | None) -> bool:
        return generation is None or generation == self._generation

    def _enabled(self, daemon, generation: int | None = None) -> None:
        if not self._current(generation) or not self.starting:
            # Switched off while the daemon was starting: off wins.
            self._run_bg(daemon.stop)
            return
        self.daemon = daemon
        self.enabled = True
        self.starting = False
        self.error = ''
        self.last_activity = self._now()
        self.config.data['xxdb_enabled'] = True
        try:
            self.config.save()
        except OSError:
            pass
        self._push_notification()
        self._state_changed()

    def _fail(self, message: str, generation: int | None = None) -> None:
        if not self._current(generation):
            return
        self.enabled = False
        self.starting = False
        self.error = message
        self._state_changed()

    def request_disable(self, reason: str = 'user') -> None:
        if not self.enabled and not self.starting:
            return
        self._generation += 1
        daemon, self.daemon = self.daemon, None
        self.enabled = False
        self.starting = False
        self.active_clients = 0
        self.client_names = set()
        self.pairing_info = None
        self.config.data['xxdb_enabled'] = False
        try:
            self.config.save()
        except OSError:
            pass
        if daemon is not None:
            try:
                daemon.stop()
            except Exception:
                _log.exception('xxdb stop failed')
        _log.info('xxdb disabled (%s)', reason)
        if self.on_unnotify is not None:
            try:
                self.on_unnotify()
            except (TypeError, ValueError):
                pass
        self._state_changed()

    on_state_changed = None

    def _state_changed(self) -> None:
        callback = self.on_state_changed
        if callback is not None:
            try:
                callback()
            except (TypeError, ValueError):
                pass

    def status_text(self) -> str:
        if self.error:
            return f'Error: {self.error}'
        if self.starting:
            return 'Starting…'
        if not self.enabled or self.daemon is None:
            return 'Off'
        port = getattr(self.daemon, 'port', 0)
        addrs = getattr(self.daemon, 'addrs', [])
        addr = addrs[0] if addrs else '?'
        text = f'On — {addr}:{port}'
        if self.active_clients:
            names = ', '.join(sorted(self.client_names))
            text += f' — connected: {names}'
        return text

    def issue_pairing_code(self) -> tuple[str, str, str, float] | None:
        """New 6-digit code, the shown address, the phone certificate's
        fingerprint prefix the host must see, and the expiry epoch."""
        if not self.enabled or self.daemon is None:
            return None
        code, expires = self.daemon.pairing.issue(self._now())
        addrs = getattr(self.daemon, 'addrs', [])
        port = getattr(self.daemon, 'port', 0)
        shown = f'{addrs[0]}:{port}' if addrs else f'?:{port}'
        fingerprint = xxdb.fingerprint_prefix(
            getattr(self.daemon, 'phone_fp', ''))
        self.pairing_info = (code, expires)
        self._on_activity()
        return code, shown, fingerprint, expires

    def trusted_hosts(self) -> list[dict]:
        return self.trust.list()

    def forget_host(self, fingerprint: str) -> bool:
        removed = self.trust.remove(fingerprint)
        daemon = self.daemon
        if daemon is not None:
            try:
                daemon.drop_host(fingerprint)
            except Exception:
                _log.exception('xxdb could not drop a forgotten host')
        return removed

    def pending_system_requests(self) -> list[tuple[str, str]]:
        if self.daemon is None:
            return []
        return self.daemon.system_access.pending(self._now())

    def confirm_system_request(self, token: str, allow: bool) -> bool:
        if self.daemon is None:
            return False
        ok = self.daemon.system_access.confirm(token, allow, self._now())
        self._state_changed()
        return ok

    def idle_check(self) -> bool:
        """Called on a timer by the shell. Returns False to stop the timer."""
        if xxdb.idle_expired(self.enabled, self.active_clients,
                             self.last_activity, self._now()):
            _log.info('xxdb auto-off after 60 minutes idle')
            self.request_disable(reason='idle-timeout')
            return False
        return True

    def _on_activity(self) -> None:
        self.last_activity = self._now()

    def _on_client_open(self, name: str) -> None:
        """Connection-thread entry point; hops to the main loop."""
        generation = self._generation
        self._post(lambda: self._client_opened(generation, name))

    def _on_client_close(self, name: str) -> None:
        generation = self._generation
        self._post(lambda: self._client_closed(generation, name))

    def _client_opened(self, generation: int, name: str) -> None:
        if not self._current(generation) or not (self.enabled or self.starting):
            return
        self.active_clients += 1
        self.client_names.add(str(name))
        self._on_activity()
        self._push_notification()
        self._state_changed()

    def _client_closed(self, generation: int, name: str) -> None:
        if not self._current(generation) or not (self.enabled or self.starting):
            return
        self.active_clients = max(0, self.active_clients - 1)
        if not self.active_clients:
            self.client_names = set()
        else:
            self.client_names.discard(str(name))
        self._on_activity()
        self._push_notification()
        self._state_changed()

    def _push_notification(self) -> None:
        if self.on_notify is None or not self.enabled:
            return
        if self.client_names:
            body = f'Connected: {", ".join(sorted(self.client_names))}'
        else:
            body = 'Waiting for a paired host'
        try:
            self.on_notify(NOTIF_TITLE, body)
        except (TypeError, ValueError):
            pass
