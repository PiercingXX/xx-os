"""Milestone 8 acceptance: background tick, notifications, snooze.

Service tick against a stub Gmail HTTP server (real SyncEngine +
real urllib transport): new mail fires exactly one grouped
notification, a quiet tick fires none, more mail replaces the same
id with the new count. Snooze round-trips through a simulated
reboot, battery saver stretches the interval, actions execute once,
and a Broadway smoke opens the app via the open-thread URI while the
service runs. Headless except the Broadway smoke, which skips when
gtk4-broadwayd is missing.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

import sys as _sys

from xx_email import config as config_mod
from xx_email import daemon as daemon_mod
from xx_email import db
from xx_email import mailbox as mailbox_mod
from xx_email import notify as notify_mod
from xx_email import setup as setup_mod
from xx_email import snooze as snooze_mod
from xx_email.gmail_client import urllib_transport
from xx_email.sync_service import SyncEngine
from xx_email.token_store import MemoryTokenBackend, TokenStore

ACCOUNT = "user@dev.xxemail"
NOW_MS = 1_700_000_000_000


@pytest.fixture(autouse=True)
def _keep_suite_gi_free():
    """Drop gi modules this file pulls in at run time.

    test_theme / test_token_store assert process-wide that gi was
    never imported. The Notify path genuinely needs GLib at run
    time, so purge what we added after each test; every gi import
    here is lazy, and GLib/Gio re-import cleanly (no display held).
    """
    yield
    for name in [mod for mod in _sys.modules
                 if mod == "gi" or mod.startswith("gi.")]:
        del _sys.modules[name]

SRC = Path(__file__).resolve().parent.parent / "src" / "xx_email"
ROOT = Path(__file__).resolve().parent.parent


class StubGmail:
    """Gmail-shaped stub on 127.0.0.1 with an ephemeral port."""

    def __init__(self):
        self.log = []
        self.phase = 1
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                stub.serve(self)

            def do_POST(self):  # noqa: N802
                stub.serve(self)

            def log_message(self, *args):
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )

    @property
    def base_url(self):
        port = self._server.server_address[1]
        return f"http://127.0.0.1:{port}/"

    def start(self):
        self._thread.start()

    def stop(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5.0)

    def serve(self, handler):
        parsed = urllib.parse.urlparse(handler.path)
        length = int(handler.headers.get("Content-Length") or 0)
        body = handler.rfile.read(length) if length else b""
        query = urllib.parse.parse_qs(parsed.query)
        self.log.append({
            "method": handler.command,
            "path": parsed.path,
            "query": query,
            "auth": handler.headers.get("Authorization"),
            "body": body,
        })
        status, payload = self.route(handler.command, parsed.path,
                                     query)
        raw = json.dumps(payload).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)

    def route(self, method, path, query):
        histories = {"1": "100", "2": "120", "3": "120", "4": "140"}
        if path.endswith("/profile"):
            return (200, {"emailAddress": ACCOUNT,
                           "historyId": histories[str(self.phase)],
                           "messagesTotal": 9, "threadsTotal": 9})
        if path.endswith("/labels"):
            return (200, {"labels": [{"id": "INBOX", "name": "INBOX",
                                      "type": "system"}]})
        if path.endswith("/threads") and not path.endswith("modify"):
            return (200, {"threads": [{"id": "t1", "snippet": "s",
                                       "historyId": "1"}],
                           "resultSizeEstimate": 1})
        if path.endswith("/history"):
            return (200, self._history())
        if "/threads/" in path and not path.endswith("modify"):
            tid = path.rsplit("/threads/", 1)[1]
            return (200, self._thread_payload(tid))
        if path.endswith("/modify"):
            return (200, {})
        raise AssertionError("unexpected call: " + method + path)

    def _history(self):
        if self.phase == 2:
            items = [_added("h1", "t2")]
            return {"history": items, "historyId": "120"}
        if self.phase == 4:
            items = [_added("h2", "t3"), _added("h3", "t4")]
            return {"history": items, "historyId": "140"}
        return {"history": [], "historyId": "120"}

    def _thread_payload(self, tid):
        senders = {"t1": "Ada <ada@example.com>",
                   "t2": "Bob <bob@example.com>",
                   "t3": "Carol <carol@example.com>",
                   "t4": "Dave <dave@example.com>"}
        dates = {"t1": "1000", "t2": "2000",
                 "t3": "3000", "t4": "4000"}
        return {
            "id": tid, "snippet": "snip", "historyId": "5",
            "messages": [{
                "id": "m-" + tid, "threadId": tid,
                "labelIds": ["INBOX", "UNREAD", "CATEGORY_PERSONAL"],
                "snippet": "secret-snippet", "historyId": "5",
                "internalDate": dates[tid],
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [
                        {"name": "Subject",
                         "value": "Secret subject " + tid},
                        {"name": "From", "value": senders[tid]}]}}],
        }


def _added(item_id, tid):
    return {"id": item_id,
            "messagesAdded": [{"message": {"id": "m-" + tid,
                                           "threadId": tid}}]}


@pytest.fixture
def stub():
    server = StubGmail()
    server.start()
    yield server
    server.stop()


class FakeResult:
    def __init__(self, server_id: int) -> None:
        self._server_id = server_id

    def unpack(self):
        return (self._server_id,)


class FakeBus:
    """Session-bus stand-in recording Notify calls."""

    def __init__(self, server_id: int = 41) -> None:
        self.calls: list[dict] = []
        self._server_id = server_id

    def call(self, bus, path, iface, method, params, reply_type,
             flags, timeout, cancellable, callback) -> None:
        self.calls.append({"bus": bus, "path": path, "iface": iface,
                           "method": method, "params": params,
                           "reply_type": reply_type})
        if callback is not None:
            callback(self, FakeResult(self._server_id))

    def call_finish(self, result):
        return result


class FakeClient:
    def __init__(self, fail: bool = False) -> None:
        self.modified: list = []
        self.fail = fail

    def modify_thread(self, thread_id, add=None, remove=None):
        if self.fail:
            raise OSError("offline")
        self.modified.append((thread_id, add, remove))
        return {}


def make_engine(conn, stub):
    return SyncEngine(conn, urllib_transport,
                      tokens={ACCOUNT: "tok"}, refresh=None,
                      base_url=stub.base_url,
                      clock=lambda: NOW_MS,
                      retry_sleep=lambda seconds: None,
                      retry_jitter=lambda: 0.0)


def tick(conn, stub, bus, client, shell=None):
    return daemon_mod.run_tick(
        conn, lambda _account: make_engine(conn, stub), bus,
        lambda _account: client, now_ms=NOW_MS,
        app_config=config_mod.AppConfig(notifications_enabled=True),
        shell_config={} if shell is None else shell)


def _notifies(bus):
    return [call for call in bus.calls
            if call["method"] == "Notify"]


# ------------------------------------------------------- service tick


def test_tick_notifies_once_replaces_same_id(stub, tmp_path):
    conn = db.open_db(str(tmp_path / "mail.db"))
    try:
        db.upsert_account(conn, db.Account(
            email=ACCOUNT, display_name="Dev", history_id=None))
        bus, client = FakeBus(), FakeClient()
        first = tick(conn, stub, bus, client)
        assert first["outcomes"][0].ok is True
        assert _notifies(bus) == [], "first sync must never notify"

        stub.phase = 2
        second = tick(conn, stub, bus, client)
        assert second["outcomes"][0].new_ids == ["t2"]
        notifies = _notifies(bus)
        assert len(notifies) == 1
        params = notifies[0]["params"].unpack()
        app_name, replaces_id = params[0], params[1]
        summary, body, actions = params[3], params[4], params[5]
        assert app_name == "io.piercingxx.XXEmail"
        assert replaces_id == \
            notify_mod.notification_id_for_account(ACCOUNT)
        assert summary == "New mail"
        assert body == "Bob", body
        assert "Secret subject" not in body
        assert "secret-snippet" not in body
        assert actions == ["default", "Open", "archive", "Archive",
                           "mark-read", "Mark read"]
        assert notifies[0]["iface"] == \
            "org.freedesktop.Notifications"
        reply_type = notifies[0]["reply_type"]
        assert type(reply_type).__name__ == "VariantType"
        assert reply_type.dup_string() == "(u)"
        assert second["notified"] == {ACCOUNT: 41}

        stub.phase = 3
        tick(conn, stub, bus, client)
        assert len(_notifies(bus)) == 1, "quiet tick notifies"

        stub.phase = 4
        fourth = tick(conn, stub, bus, client)
        assert sorted(fourth["outcomes"][0].new_ids) == ["t3", "t4"]
        notifies = _notifies(bus)
        assert len(notifies) == 2
        again = notifies[1]["params"].unpack()
        assert again[1] == replaces_id, "updates replace, not stack"
        assert again[3] == "2 new messages"
        assert "Carol" in again[4] and "Dave" in again[4]
        assert "Secret subject" not in again[4]
        assert client.modified == [], "no wakes means no modifies"
    finally:
        conn.close()


def test_tick_respects_posture_off_and_disabled(stub, tmp_path):
    conn = db.open_db(str(tmp_path / "mail.db"))
    try:
        db.upsert_account(conn, db.Account(
            email=ACCOUNT, display_name="Dev", history_id=None))
        bus, client = FakeBus(), FakeClient()
        tick(conn, stub, bus, client)
        stub.phase = 2
        outcome = daemon_mod.run_tick(
            conn, lambda _a: make_engine(conn, stub), bus,
            lambda _a: client, now_ms=NOW_MS,
            app_config=config_mod.AppConfig(
                notifications_enabled=True),
            shell_config={"lock_screen_notifications": "off"})
        assert outcome["payloads"] == {}
        assert _notifies(bus) == []
        outcome = daemon_mod.run_tick(
            conn, lambda _a: make_engine(conn, stub), bus,
            lambda _a: client, now_ms=NOW_MS,
            app_config=config_mod.AppConfig(
                notifications_enabled=False),
            shell_config={})
        assert outcome["payloads"] == {}
        assert _notifies(bus) == []
    finally:
        conn.close()


def test_notification_ids_stable_and_grouped():
    first = notify_mod.notification_id_for_account(ACCOUNT)
    assert first == notify_mod.notification_id_for_account(
        "USER@dev.xxemail")
    assert first != 0
    assert first != notify_mod.notification_id_for_account(
        "other@example.com")
    rows = [db.Thread(account_email=ACCOUNT, id="t1",
                       from_name="Ada", from_address="a@x"),
            db.Thread(account_email=ACCOUNT, id="t2",
                       from_name="", from_address="b@y")]
    payload = notify_mod.build_notification(ACCOUNT, rows, "summary")
    assert payload["summary"] == "2 new messages"
    assert payload["body"] == "Ada, b@y"
    assert payload["replaces_id"] == first
    assert payload["thread_id"] == "t1"
    assert notify_mod.build_notification(ACCOUNT, rows, "off") is None
    counted = notify_mod.build_notification(ACCOUNT, rows, "count")
    assert counted["body"] == "2 new messages"
    single = notify_mod.build_notification(ACCOUNT, rows[:1])
    assert single["summary"] == "New mail"
    assert single["body"] == "Ada"


def _seed_thread(conn, tid="t-snooze", unread=True):
    db.upsert_account(conn, db.Account(
        email=ACCOUNT, display_name="Dev", history_id="100",
        last_sync_at=NOW_MS))
    labels = "INBOX,UNREAD" if unread else "INBOX"
    db.upsert_threads(conn, [db.Thread(
        account_email=ACCOUNT, id=tid, snippet="s", subject="Sub",
        from_address="Ada <ada@example.com>", from_name="Ada",
        date=NOW_MS, message_count=1,
        unread_count=1 if unread else 0, has_attachments=False,
        starred=False, in_inbox=True, categories="",
        labels_csv=labels)])


# ------------------------------------------------- snooze round trip


def test_snooze_round_trip_survives_reboot(tmp_path):
    path = tmp_path / "mail.db"
    wake_at = NOW_MS + 3_600_000
    conn = db.open_db(str(path))
    try:
        _seed_thread(conn)
        mailbox_mod.apply_local_action(conn, ACCOUNT, "SNOOZE",
                                       ["t-snooze"], wake_at=wake_at)
        row = db.get_thread(conn, ACCOUNT, "t-snooze")
        assert row.in_inbox is False
        assert "INBOX" not in row.labels_csv.split(",")
        assert row.snoozed_until == wake_at
        wake = db.get_snooze_wake(conn, ACCOUNT, "t-snooze")
        assert wake is not None and wake.target_at == wake_at
    finally:
        conn.close()

    rebooted = db.open_db(str(path))
    try:
        assert db.get_snooze_wake(
            rebooted, ACCOUNT, "t-snooze") is not None
        client = FakeClient()
        early = snooze_mod.process_due_wakes(
            rebooted, lambda _a: client, NOW_MS)
        assert early == {"woken": [], "failed": []}
        assert client.modified == []
        assert db.get_snooze_wake(
            rebooted, ACCOUNT, "t-snooze") is not None

        late = snooze_mod.process_due_wakes(
            rebooted, lambda _a: client, wake_at)
        assert late == {"woken": ["t-snooze"], "failed": []}
        assert client.modified == [("t-snooze", ["INBOX"], None)]
        row = db.get_thread(rebooted, ACCOUNT, "t-snooze")
        assert row.in_inbox is True
        assert "INBOX" in row.labels_csv.split(",")
        assert row.snoozed_until is None
        assert db.get_snooze_wake(
            rebooted, ACCOUNT, "t-snooze") is None
    finally:
        rebooted.close()


def test_snooze_wake_retries_until_it_lands(tmp_path):
    conn = db.open_db(str(tmp_path / "mail.db"))
    try:
        _seed_thread(conn)
        wake_at = NOW_MS - 1_000
        mailbox_mod.apply_local_action(conn, ACCOUNT, "SNOOZE",
                                       ["t-snooze"], wake_at=wake_at)
        failing = FakeClient(fail=True)
        missed = snooze_mod.process_due_wakes(
            conn, lambda _a: failing, NOW_MS)
        assert missed == {"woken": [], "failed": ["t-snooze"]}
        assert db.get_snooze_wake(
            conn, ACCOUNT, "t-snooze") is not None
        assert db.get_thread(
            conn, ACCOUNT, "t-snooze").in_inbox is False
        client = FakeClient()
        caught = snooze_mod.process_due_wakes(
            conn, lambda _a: client, NOW_MS)
        assert caught == {"woken": ["t-snooze"], "failed": []}
        assert db.get_thread(
            conn, ACCOUNT, "t-snooze").in_inbox is True
    finally:
        conn.close()


def test_uninstall_warning_and_sign_out_restore(tmp_path):
    assert "All Mail" in snooze_mod.UNINSTALL_WARNING
    assert "snooz" in snooze_mod.UNINSTALL_WARNING.lower()
    conn = db.open_db(str(tmp_path / "mail.db"))
    try:
        _seed_thread(conn)
        store = TokenStore(MemoryTokenBackend())
        mailbox_mod.apply_local_action(conn, ACCOUNT, "SNOOZE",
                                       ["t-snooze"],
                                       wake_at=NOW_MS + 60_000)
        restored = []
        outcome = setup_mod.sign_out(
            conn, store, ACCOUNT, data_dir=tmp_path,
            restore=lambda a, t: restored.append((a, t)))
        assert restored == [(ACCOUNT, "t-snooze")]
        assert snooze_mod.UNINSTALL_WARNING in outcome["warnings"]
        assert db.all_accounts(conn) == []

        _seed_thread(conn, tid="t-stranded")
        mailbox_mod.apply_local_action(conn, ACCOUNT, "SNOOZE",
                                       ["t-stranded"],
                                       wake_at=NOW_MS + 60_000)
        outcome = setup_mod.sign_out(conn, store, ACCOUNT,
                                     data_dir=tmp_path)
        assert snooze_mod.UNINSTALL_WARNING in outcome["warnings"]
    finally:
        conn.close()


# ----------------------------------------------- battery-saver pace


def test_effective_interval_stretches_under_saver():
    assert daemon_mod.effective_interval_minutes(15, False) == 15
    assert daemon_mod.effective_interval_minutes(15, True) == 30
    assert daemon_mod.effective_interval_minutes(30, True) == 60
    assert daemon_mod.effective_interval_minutes(30, False) == 30
    assert daemon_mod.effective_interval_minutes(5, False) == 15
    assert daemon_mod.effective_interval_minutes(5, True) == 30


def test_battery_saver_read_from_shell_config(tmp_path, monkeypatch):
    assert daemon_mod.read_battery_saver({}) is False
    assert daemon_mod.read_battery_saver(
        {"battery_saver": True}) is True
    shell_config = tmp_path / "shell-config.json"
    shell_config.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setenv(daemon_mod.SHELL_CONFIG_ENV,
                       str(shell_config))
    assert daemon_mod.read_battery_saver() is False
    shell_config.write_text(json.dumps({"battery_saver": True}),
                            encoding="utf-8")
    assert daemon_mod.read_battery_saver() is True
    shell_config.write_text("not json", encoding="utf-8")
    assert daemon_mod.read_battery_saver() is False


def test_tick_pacing_and_state(tmp_path):
    state = tmp_path / "daemon_state.json"
    assert daemon_mod.read_last_tick(state) is None
    assert daemon_mod.should_tick(None, NOW_MS, 15) is True
    assert daemon_mod.should_tick(NOW_MS - 14 * 60_000, NOW_MS,
                                  15) is False
    assert daemon_mod.should_tick(NOW_MS - 16 * 60_000, NOW_MS,
                                  15) is True
    assert daemon_mod.should_tick(NOW_MS - 29 * 60_000, NOW_MS,
                                  30) is False
    daemon_mod.write_last_tick(NOW_MS, state)
    assert daemon_mod.read_last_tick(state) == NOW_MS
    assert daemon_mod.should_tick(NOW_MS, NOW_MS + 1, 15) is False


# -------------------------------------------------- action handling


def test_action_target_routes_known_ids_only():
    payload = {"thread_id": "t1"}
    server_map = {41: (ACCOUNT, payload)}
    assert daemon_mod.action_target(server_map, 41) == (ACCOUNT,
                                                        payload)
    assert daemon_mod.action_target(server_map, 42) is None
    assert daemon_mod.action_target(server_map, "nope") is None


def test_archive_action_executes_once_unknown_ignored(tmp_path):
    conn = db.open_db(str(tmp_path / "mail.db"))
    try:
        _seed_thread(conn)
        _seed_thread(conn, tid="t-read")
        handler = daemon_mod.ActionHandler()
        client = FakeClient()
        assert handler.handle(conn, client, ACCOUNT, "archive",
                              "t-snooze") is True
        assert client.modified == [("t-snooze", None, ["INBOX"])]
        row = db.get_thread(conn, ACCOUNT, "t-snooze")
        assert row.in_inbox is False
        assert handler.handle(conn, client, ACCOUNT, "archive",
                              "t-snooze") is False
        assert len(client.modified) == 1, "archive runs once"
        assert handler.handle(conn, client, ACCOUNT, "archive",
                              "t-ghost") is False
        assert len(client.modified) == 1, "unknown ids ignored"
        assert handler.handle(conn, client, ACCOUNT, "snooze",
                              "t-read") is False
        assert len(client.modified) == 1, "unknown actions ignored"
        assert handler.handle(conn, client, ACCOUNT, "mark-read",
                              "t-read") is True
        assert client.modified[-1] == ("t-read", None, ["UNREAD"])
        assert db.get_thread(
            conn, ACCOUNT, "t-read").unread_count == 0
    finally:
        conn.close()


def test_open_uri_round_trip():
    uri = notify_mod.open_thread_uri(ACCOUNT, "t9")
    assert uri.startswith("io.piercingxx.XXEmail://")
    assert notify_mod.parse_open_thread_uri(uri) == (ACCOUNT, "t9")
    account, thread = notify_mod.parse_open_thread_uri(
        "io.piercingxx.XXEmail://thread/a@x/t9")
    assert (account, thread) == ("a@x", "t9")
    assert notify_mod.parse_open_thread_uri(
        "io.other://thread?a=t") == (None, None)
    assert notify_mod.parse_open_thread_uri("") == (None, None)
    assert notify_mod.parse_open_target_args(
        ["--open-thread", "t9",
         "--account", ACCOUNT]) == (ACCOUNT, "t9")
    assert notify_mod.parse_open_target_args([uri]) == (ACCOUNT, "t9")
    assert notify_mod.parse_open_target_args([]) is None
    assert notify_mod.parse_open_target_args(
        ["--open-thread"]) is None


# ------------------------------------------------------- static pacts


def test_daemon_never_owns_gui_bus_name():
    from xx_email import __app_id__

    assert daemon_mod.DAEMON_BUS_NAME == \
        "io.piercingxx.XXEmail.Daemon"
    assert daemon_mod.DAEMON_BUS_NAME != __app_id__
    assert notify_mod.NOTIF_BUS_NAME == \
        "org.freedesktop.Notifications"
    daemon_source = (SRC / "daemon.py").read_text(encoding="utf-8")
    assert "io.piercingxx.XXEmail.Daemon" in daemon_source
    notify_source = (SRC / "notify.py").read_text(encoding="utf-8")
    assert "org.freedesktop.Notifications" in notify_source


def test_notify_reply_types_are_variant_types():
    source = (SRC / "notify.py").read_text(encoding="utf-8")
    assert 'VariantType.new("(u)")' in source
    assert 'reply_type="' not in source
    assert "reply_type='" not in source


def test_gui_handles_open_and_units_exist():
    main_source = (SRC / "main.py").read_text(encoding="utf-8")
    assert "HANDLES_OPEN" in main_source
    assert "--open-thread" in main_source
    assert "--daemon-tick" in main_source
    service = (ROOT / "data" / "systemd" /
               "xx-email-sync.service.in").read_text(
                   encoding="utf-8")
    assert "Type=oneshot" in service
    assert "xx-email --daemon-tick" in service
    timer = (ROOT / "data" / "systemd" /
             "xx-email-sync.timer").read_text(encoding="utf-8")
    assert "OnUnitActiveSec=15min" in timer
    assert "Persistent=true" in timer
    desktop = (ROOT / "data" /
               "io.piercingxx.XXEmail.desktop.in").read_text(
                   encoding="utf-8")
    assert "DBusActivatable=true" in desktop
    assert "x-scheme-handler/io.piercingxx.XXEmail" in desktop


# ------------------------------------------------------ broadway smoke


_DRIVER = str(Path(__file__).parent / "daemon_broadway_driver.py")


def _socket_path(display: str) -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR",
                             f"/run/user/{os.getuid()}")
    return os.path.join(runtime, f"broadway{int(display[1:]) + 1}.socket")


def _display_live(display: str) -> bool:
    path = _socket_path(display)
    if not os.path.exists(path):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(2)
            sock.connect(path)
    except OSError:
        return False
    return True


@pytest.fixture(scope="module")
def broadway_env():
    if shutil.which("gtk4-broadwayd") is None:
        pytest.skip("gtk4-broadwayd is missing")
    daemon_runtime = os.environ.get("XDG_RUNTIME_DIR",
                                    f"/run/user/{os.getuid()}")
    for number in range(71, 90):
        display = f":{number}"
        if _display_live(display):
            continue
        try:
            os.makedirs(os.path.dirname(_socket_path(display)),
                        exist_ok=True)
        except OSError:
            pass
        try:
            os.unlink(_socket_path(display))
        except OSError:
            pass
        with socket.socket(socket.AF_INET,
                           socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        proc = subprocess.Popen(
            ["gtk4-broadwayd", "--port", str(port), display],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(200):
            if os.path.exists(_socket_path(display)):
                break
            import time as _time

            _time.sleep(0.05)
        else:
            proc.terminate()
            continue
        try:
            yield (display, daemon_runtime)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
        return
    pytest.skip("no free broadway display")


def _seed_broadway_store() -> None:
    setup_mod.ensure_app_dirs()
    conn = db.open_db(
        config_mod.data_dir() / db.DB_FILE_NAME)
    try:
        db.upsert_account(conn, db.Account(
            email=ACCOUNT, display_name="Dev User",
            history_id="100", last_sync_at=NOW_MS))
        db.upsert_threads(conn, [db.Thread(
            account_email=ACCOUNT, id="t-open",
            snippet="See you at noon", subject="Lunch?",
            from_address="Ada <ada@example.com>", from_name="Ada",
            date=NOW_MS, message_count=1, unread_count=1,
            has_attachments=False, starred=False, in_inbox=True,
            categories="", labels_csv="INBOX,UNREAD")])
        db.upsert_messages(conn, [db.Message(
            account_email=ACCOUNT, id="m-open", thread_id="t-open",
            subject="Lunch?", from_address="Ada <ada@example.com>",
            to_csv=ACCOUNT, cc_csv="", date=NOW_MS,
            snippet="See you at noon", read=False, starred=False,
            has_attachments=False, labels_csv="INBOX,UNREAD",
            message_id_header="<open@example.com>",
            body_plain="See you at noon", body_fetched=True)])
    finally:
        conn.close()


def test_open_thread_uri_while_service_runs(broadway_env):
    _seed_broadway_store()
    display, daemon_runtime = broadway_env
    env = dict(os.environ)
    env["GDK_BACKEND"] = "broadway"
    env["BROADWAY_DISPLAY"] = display
    env["XDG_RUNTIME_DIR"] = daemon_runtime
    env["PYTHONPATH"] = os.pathsep.join((
        str(Path(__file__).resolve().parent.parent / "src"),
        str(Path(__file__).resolve().parent.parent.parent /
            "common"),
    ))
    completed = subprocess.run(
        [sys.executable, _DRIVER, "smoke"],
        env=env, capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, \
        f"daemon driver failed: {completed.stderr[-2000:]}"
    markers = {}
    for line in completed.stdout.splitlines():
        if ": " in line:
            key, _, value = line.partition(": ")
            markers[key.strip()] = value.strip()
    assert markers.get("TICK_NOTIFIED") == "41"
    assert markers.get("URI_OK", "").startswith(
        "io.piercingxx.XXEmail://")
    assert markers.get("HANDLES_OPEN") == "yes"
    assert markers.get("PAGES_OK") == "mailbox thread"
    assert markers.get("ERRORS") == "none"
