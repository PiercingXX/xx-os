"""xxdb controller: enable/disable state, pairing codes, idle auto-off.

GTK-free: the shell (window.py/main.py) drives it from the main loop and
renders its state. All blocking work runs in worker threads; results come
back through `post(fn)`, which is GLib.idle_add in the shell and a direct
call in tests. Heavy daemon construction is injected via daemon_factory.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import xxdb

_log = logging.getLogger('piercing.xxdb')

NOTIF_ID = 999904
NOTIF_TITLE = 'Wireless debugging'


def _default_daemon_factory(controller) -> object:
    from xxdb_daemon import XxdbDaemon
    return XxdbDaemon(
        cert_dir=controller.cert_dir,
        trust_store=controller.trust,
        on_connect=controller._on_client_open,
        on_disconnect=controller._on_client_close,
        on_activity=controller._on_activity,
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
        self.cert_dir = xxdb.data_dir(self._home)
        self.trust = xxdb.TrustStore(home=self._home)
        self.daemon = None
        self.enabled = False
        self.starting = False
        self.error = ''
        self.last_activity = self._now()
        self.active_clients = 0
        self.client_names: set[str] = set()
        self.pairing_info: tuple[str, float] | None = None

    def _now(self) -> float:
        import time
        return self._clock() if self._clock is not None else time.time()

    def _run_bg(self, work) -> None:
        thread = threading.Thread(target=self._guarded, args=(work,),
                                  daemon=True)
        thread.start()

    def _guarded(self, work) -> None:
        try:
            work()
        except (OSError, ValueError, RuntimeError) as error:
            _log.error('xxdb background work failed: %s', error)
            message = str(error)
            self._post(lambda: self._fail(message))

    def ensure_boot_state(self) -> bool:
        """Apply the restart rule: off unless persistence is configured.

        Returns True when the shell should re-enable wireless debugging.
        """
        persistent = bool(self.config.data.get('xxdb_persistent', False))
        wanted = bool(self.config.data.get('xxdb_enabled', False))
        if not persistent:
            if wanted or self.config.data.get('xxdb_enabled') is None:
                pass
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
        self.starting = True
        self.error = ''
        self._state_changed()
        self._run_bg(self._enable_work)

    def _enable_work(self) -> None:
        daemon = self._daemon_factory(self)
        daemon.start()
        self._post(lambda: self._enabled(daemon))

    def _enabled(self, daemon) -> None:
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

    def _fail(self, message: str) -> None:
        self.enabled = False
        self.starting = False
        self.error = message
        self._state_changed()

    def request_disable(self, reason: str = 'user') -> None:
        if not self.enabled and not self.starting:
            return
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
                pass
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
        return self.trust.remove(fingerprint)

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
        self.active_clients += 1
        self.client_names.add(str(name))
        self._on_activity()
        self._push_notification()
        self._state_changed()

    def _on_client_close(self, name: str) -> None:
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
