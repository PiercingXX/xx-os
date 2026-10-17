"""Background poll service for milestone 8 (no GTK, worker process).

The poller lives in the systemd user service xx-email-sync.service
(fired by xx-email-sync.timer), which the shell never freezes — only
shell-launched apps are frozen, so polling stays alive while the UI
may be paused. Each tick runs one SyncEngine pass per account (which
already drains the outbox), sends one grouped notification per
account with new mail, and restores due snooze wakes. Wake rows are
SQLite-persisted and the timer is Persistent, so missed wakes catch
up on boot.

Battery saver stretches the interval: the shell's battery_saver flag
(~/.config/xx-wm/config.json) doubles the effective poll interval;
manual refresh in the UI still works immediately.

The service owns io.piercingxx.XXEmail.Daemon when it needs a bus
name — never the GUI application_id (a daemon holding the app id
makes the GUI exit as a "secondary instance"). libsecret is touched
only through TokenStoreWorker on worker threads, never any main
loop: this process has no GTK loop at all.

Blocking by contract: every function here may block on network,
sqlite, or the Secret Service. The GUI never imports this module on
its main thread.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from xx_email import db
from xx_email import notify as notify_mod
from xx_email import snooze as snooze_mod

DAEMON_BUS_NAME = "io.piercingxx.XXEmail.Daemon"

SHELL_CONFIG_ENV = "XX_WM_CONFIG"


def shell_config_path() -> Path:
    override = os.environ.get(SHELL_CONFIG_ENV)
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "xx-wm" / "config.json"


def load_shell_config(path: Path | None = None) -> dict:
    """Shell config as a dict; missing/invalid files yield {}."""
    target = path if path is not None else shell_config_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}

DEFAULT_INTERVAL_MINUTES = 15
BATTERY_SAVER_FACTOR = 2
STATE_FILE_NAME = "daemon_state.json"


def effective_interval_minutes(config_minutes: int,
                               battery_saver: bool) -> int:
    """Poll interval: config floor of 15 min, doubled under saver."""
    try:
        base = int(config_minutes)
    except (TypeError, ValueError):
        base = DEFAULT_INTERVAL_MINUTES
    if base < DEFAULT_INTERVAL_MINUTES:
        base = DEFAULT_INTERVAL_MINUTES
    if bool(battery_saver):
        base *= BATTERY_SAVER_FACTOR
    return base


def read_battery_saver(shell_config: dict | None = None) -> bool:
    """Shell battery_saver flag; missing/invalid config means off."""
    if shell_config is None:
        try:
            shell_config = load_shell_config()
        except Exception:
            return False
    try:
        return bool((shell_config or {}).get("battery_saver", False))
    except Exception:
        return False


def state_path(path: Path | None = None) -> Path:
    """State file holding the last tick (XDG_STATE_HOME/xx-email/)."""
    if path is not None:
        return Path(path)
    from xx_email import config as config_mod

    return config_mod.state_dir() / STATE_FILE_NAME


def read_last_tick(path: Path | None = None) -> int | None:
    """Last completed tick in epoch ms, or None when never."""
    target = state_path(path)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        value = int(data.get("last_tick_ms", 0))
    except (TypeError, ValueError):
        return None
    return value or None


def write_last_tick(now_ms: int, path: Path | None = None) -> Path:
    """Atomically record a completed tick (tmp file + rename)."""
    target = state_path(path)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(target.parent, 0o700)
    except OSError:
        pass
    payload = json.dumps({"last_tick_ms": int(now_ms)}) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent),
                                    prefix=target.name + ".",
                                    suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        Path(tmp_name).replace(target)
    except BaseException:
        try:
            Path(tmp_name).unlink()
        except OSError:
            pass
        raise
    return target


def should_tick(last_tick_ms: int | None, now_ms: int,
                interval_minutes: int) -> bool:
    """True when the effective interval has elapsed (or never ticked).

    A clock that moved backwards also ticks: skipping a poll because
    the clock jumped is how mail silently goes unstuck.
    """
    if last_tick_ms is None or last_tick_ms <= 0:
        return True
    if now_ms < last_tick_ms:
        return True
    return (now_ms - last_tick_ms) >= int(interval_minutes) * 60_000


class ActionHandler:
    """Execute notification actions once; unknown ids are ignored.

    handle() applies the local-first action (mailbox parity) then the
    server op on the calling worker thread. Each (account, action,
    thread) runs at most once per process: repeats return False with
    no further server call, and ids with no local row are ignored
    without touching the network.
    """

    def __init__(self) -> None:
        self._handled: set[tuple[str, str, str]] = set()

    def handled(self) -> set[tuple[str, str, str]]:
        return set(self._handled)

    def handle(self, conn, client, account: str,
               action: str, thread_id: str) -> bool:
        from xx_email import mailbox as mailbox_mod

        key = (account or "", action or "", thread_id or "")
        if not account or not thread_id:
            return False
        if action not in (notify_mod.ACTION_ARCHIVE,
                          notify_mod.ACTION_MARK_READ):
            return False
        if key in self._handled:
            return False
        try:
            row = db.get_thread(conn, account, thread_id)
        except Exception:
            return False
        if row is None:
            return False
        op = "ARCHIVE" if action == notify_mod.ACTION_ARCHIVE \
            else "MARK_READ"
        try:
            undo = mailbox_mod.apply_local_action(conn, account, op,
                                                  [thread_id])
        except Exception:
            return False
        try:
            for kind, tid, add, remove in undo.server_ops:
                if kind == "modify":
                    client.modify_thread(tid, add or None,
                                         remove or None)
                elif kind == "trash":
                    client.trash_thread(tid)
                elif kind == "untrash":
                    client.untrash_thread(tid)
        except Exception:
            try:
                undo.revert(conn)
            except Exception:
                pass
            return False
        self._handled.add(key)
        return True


def run_tick(conn, make_engine, connection, client_for_account, *,
             now_ms: int, app_config=None,
             shell_config: dict | None = None,
             posted_sink=None) -> dict:
    """One background pass: sync, notify, outbox (in engine), wakes.

    make_engine(account) builds a SyncEngine; connection is the
    session-bus handle for Notify (or None to skip delivery while
    still computing payloads); client_for_account(account) builds a
    Gmail client for wake restores. posted_sink(server_id, account,
    thread_id) records each posted card for the actions service.
    Returns {"outcomes", "notified" ({account: server_id}),
    "payloads" ({account: payload}), "wakes"}.
    """
    if app_config is None:
        from xx_email import config as config_mod

        try:
            app_config = config_mod.load()
        except Exception:
            app_config = None
    if shell_config is None:
        try:
            shell_config = load_shell_config()
        except Exception:
            shell_config = {}
    posture = notify_mod.lock_posture(shell_config or {})
    enabled = bool(getattr(app_config, "notifications_enabled", True))
    from datetime import datetime, timezone

    try:
        tick_moment = datetime.fromtimestamp(int(now_ms) / 1000,
                                             tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        tick_moment = None
    shell_held = tick_moment is not None and not \
        notify_mod.notification_allowed(shell_config or {},
                                        now=tick_moment)
    notified: dict[str, int] = {}
    payloads: dict[str, dict] = {}
    outcomes = []
    try:
        accounts = [item.email for item in db.all_accounts(conn)]
    except Exception:
        accounts = []
    for account in accounts:
        try:
            engine = make_engine(account)
        except Exception:
            continue
        try:
            outcome = engine.sync_account(account)
        except Exception:
            continue
        outcomes.append(outcome)
        if not getattr(outcome, "ok", False):
            continue
        rows = []
        for thread_id in list(getattr(outcome, "new_ids", []) or []):
            try:
                row = db.get_thread(conn, account, thread_id)
            except Exception:
                row = None
            if row is not None:
                rows.append(row)
        if not rows or not enabled or shell_held:
            continue
        payload = notify_mod.build_notification(account, rows,
                                                posture)
        if payload is None or connection is None:
            continue
        payloads[account] = payload

        def _recorded(server_id: int, _account=account) -> None:
            try:
                sid = int(server_id)
            except (TypeError, ValueError):
                sid = 0
            notified[_account] = sid
            if posted_sink is not None and sid > 0:
                try:
                    thread_id = (payloads.get(_account) or {}).get(
                        "thread_id", "")
                except Exception:
                    thread_id = ""
                try:
                    posted_sink(sid, _account, thread_id or "")
                except Exception:
                    pass

        try:
            notify_mod.send_notification(connection, payload,
                                         done=_recorded)
        except Exception:
            continue
    try:
        wakes = snooze_mod.process_due_wakes(conn, client_for_account,
                                             int(now_ms))
    except Exception:
        wakes = {"woken": [], "failed": []}
    return {"outcomes": outcomes, "notified": notified,
            "payloads": payloads, "wakes": wakes}


def action_target(server_map: dict, server_id: int):
    """Route an ActionInvoked id: (account, payload) or None."""
    try:
        key = int(server_id)
    except (TypeError, ValueError):
        return None
    return server_map.get(key)


#: How long the tick waits for Notify replies before exiting.
REPLY_WAIT_SECONDS = 15


def collect_posted_replies(result) -> None:
    """Pump the main loop until every Notify reply lands, or timeout.

    The posted_sink calls in run_tick fire on these replies, so the
    actions service can route taps after this process exits. Quits
    early once every payload has its server id. Never raises.
    """
    try:
        payloads = result.get("payloads") or {}
        notified = result.get("notified") or {}
        if not payloads or len(notified) >= len(payloads):
            return
    except Exception:
        return
    try:
        from gi.repository import GLib
    except (ImportError, ValueError):
        return
    loop = GLib.MainLoop()

    def _tick() -> bool:
        try:
            if len(notified) >= len(payloads):
                loop.quit()
                return False
        except Exception:
            pass
        return True

    GLib.timeout_add(250, _tick)
    GLib.timeout_add_seconds(REPLY_WAIT_SECONDS, loop.quit)
    try:
        loop.run()
    except Exception:
        pass


def run_daemon_tick() -> int:
    """Production one-shot tick for xx-email-sync.service. Exit code."""
    from xx_email import config as config_mod
    from xx_email import gmail_client as gmail_mod
    from xx_email import setup as setup_mod
    from xx_email import sync_service as sync_mod
    from xx_email import token_store as token_mod

    try:
        setup_mod.ensure_app_dirs()
    except Exception:
        pass
    try:
        stored = config_mod.load()
    except Exception:
        stored = None
    saver = read_battery_saver()
    base = getattr(stored, "sync_minutes",
                   DEFAULT_INTERVAL_MINUTES) if stored else \
        DEFAULT_INTERVAL_MINUTES
    interval = effective_interval_minutes(base, saver)
    now_ms = int(time.time() * 1000)
    try:
        last = read_last_tick()
    except Exception:
        last = None
    if not should_tick(last, now_ms, interval):
        return 0

    def _open():
        from xx_email import db as db_mod

        return db_mod.open_db(
            setup_mod.ensure_app_dirs()["data"] / db_mod.DB_FILE_NAME)

    conn = _open()
    try:
        def _make_engine(account: str):
            worker = token_mod.TokenStoreWorker(token_mod.TokenStore(
                token_mod.SecretTokenBackend()))
            refresh = sync_mod.production_refresh(worker)
            return sync_mod.SyncEngine(
                conn, gmail_mod.urllib_transport, tokens={},
                refresh=lambda email,
                rejected: refresh(email, rejected),
                files_dir=config_mod.data_dir(),
                send_and_archive=bool(
                    stored is not None and stored.send_and_archive))

        def _client_for(account: str):
            return production_client_for(account)

        try:
            from gi.repository import Gio

            bus = Gio.bus_get_sync(None, None)
        except Exception:
            bus = None
        sink = posted_sink_for_db(
            setup_mod.ensure_app_dirs()["data"] / db.DB_FILE_NAME)
        result = run_tick(conn, _make_engine, bus, _client_for,
                          now_ms=now_ms, app_config=stored,
                          posted_sink=sink)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    try:
        write_last_tick(now_ms)
    except Exception:
        pass
    if bus is not None and result.get("payloads"):
        collect_posted_replies(result)
    return 0


class _LiveClient:
    """Thin per-account Gmail client holding its token worker."""

    def __init__(self, account: str, worker, seed: str) -> None:
        from xx_email import gmail_client as gmail_mod
        from xx_email import sync_service as sync_mod

        refresh = sync_mod.production_refresh(worker)
        self._client = gmail_mod.GmailClient(
            get_token=lambda: seed,
            refresh_token=lambda rejected: refresh(account,
                                                   rejected))
        self._worker = worker

    def modify_thread(self, thread_id: str, add=None,
                      remove=None):
        return self._client.modify_thread(thread_id, add, remove)

    def close(self) -> None:
        try:
            self._worker.close()
        except Exception:
            pass


def production_client_for(account: str) -> _LiveClient:
    """Live Gmail client for one account (worker thread only)."""
    from xx_email import sync_service as sync_mod
    from xx_email import token_store as token_mod

    worker = token_mod.TokenStoreWorker(token_mod.TokenStore(
        token_mod.SecretTokenBackend()))
    try:
        seed = sync_mod.production_refresh(worker)(account, "")
    except Exception:
        worker.close()
        raise
    return _LiveClient(account, worker, seed)


def posted_sink_for_db(db_path):
    """posted_sink writing through its own connection per call.

    Each Notify reply may arrive on the main-loop thread after the
    tick's own connection closed, and connections never cross
    threads, so every call opens, writes, and closes.
    """
    def _sink(server_id: int, account: str, thread_id: str) -> None:
        try:
            stamp = int(time.time() * 1000)
        except Exception:
            stamp = 0
        try:
            handle = db.open_db(db_path)
        except Exception:
            return
        try:
            db.record_posted_notif(handle, server_id, account,
                                   thread_id, stamp)
        except Exception:
            pass
        finally:
            try:
                handle.close()
            except Exception:
                pass

    return _sink
