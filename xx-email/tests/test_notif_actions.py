"""Notification actions after the poller exits (posted-id guard).

Cards carry x-xx-wm-action-target at an activatable service; the
service only acts on server ids the poller recorded, bounded and
persisted in the notif_actions table. gi stays out of this process:
the dispatch check runs in the tick driver subprocess, like the
other real-Variant assertions.
"""

import os
import subprocess
import sys
from pathlib import Path

from xx_email import actions as actions_mod
from xx_email import daemon as daemon_mod
from xx_email import db
from xx_email import notify as notify_mod
from xx_email.sync_service import SyncOutcome

ACCOUNT = "user@dev.xxemail"
NOW_MS = 1_700_000_000_000

SRC = Path(__file__).resolve().parent.parent / "src" / "xx_email"
ROOT = Path(__file__).resolve().parent.parent
_DRIVER = Path(__file__).resolve().parent / "notify_tick_driver.py"


def _run_actions_driver(db_path: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join((
        str(Path(__file__).resolve().parent.parent / "src"),
        str(Path(__file__).resolve().parent.parent.parent /
            "common"),
    ))
    completed = subprocess.run(
        [sys.executable, _DRIVER, "actions", str(db_path)],
        env=env, capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, \
        f"actions driver failed: {completed.stderr[-2000:]}"
    markers = {}
    for line in completed.stdout.splitlines():
        if ": " in line:
            key, _, value = line.partition(": ")
            markers[key.strip()] = value.strip()
    assert markers.get("ERRORS") == "none", \
        completed.stdout[-2000:]
    return markers


def _seed_thread(conn, tid="t1"):
    db.upsert_account(conn, db.Account(
        email=ACCOUNT, display_name="Dev", history_id="100",
        last_sync_at=NOW_MS))
    db.upsert_threads(conn, [db.Thread(
        account_email=ACCOUNT, id=tid, snippet="s", subject="Sub",
        from_address="ada@example.com", from_name="Ada",
        date=NOW_MS, message_count=1, unread_count=1,
        has_attachments=False, starred=False, in_inbox=True,
        categories="", labels_csv="INBOX,UNREAD")])


class FakeClient:
    def __init__(self) -> None:
        self.modified: list = []
        self.closed = 0

    def modify_thread(self, thread_id, add=None, remove=None):
        self.modified.append((thread_id, add, remove))
        return {}

    def trash_thread(self, thread_id):
        self.modified.append((thread_id, "trash", None))
        return {}

    def untrash_thread(self, thread_id):
        self.modified.append((thread_id, "untrash", None))
        return {}

    def close(self):
        self.closed += 1


def _service(path, **kwargs):
    kwargs.setdefault("db_opener", lambda: db.open_db(str(path)))
    kwargs.setdefault("client_for", lambda account: FakeClient())
    kwargs.setdefault("opener", lambda uri: None)
    return actions_mod.ActionsService(**kwargs)


def test_posted_ids_round_trip_and_reject_unknown(tmp_path):
    conn = db.open_db(str(tmp_path / "mail.db"))
    try:
        assert db.posted_notif_for(conn, 41) is None
        db.record_posted_notif(conn, 41, ACCOUNT, "t1", NOW_MS)
        assert db.posted_notif_for(conn, 41) == (ACCOUNT, "t1")
        assert db.posted_notif_for(conn, 42) is None
        assert db.posted_notif_for(conn, "nope") is None
        db.record_posted_notif(conn, 0, ACCOUNT, "t1", NOW_MS)
        db.record_posted_notif(conn, -3, ACCOUNT, "t1", NOW_MS)
        db.record_posted_notif(conn, "nope", ACCOUNT, "t1", NOW_MS)
        assert db.posted_notif_for(conn, 0) is None
    finally:
        conn.close()


def test_posted_table_evicts_oldest_past_cap(tmp_path):
    conn = db.open_db(str(tmp_path / "mail.db"))
    try:
        for index in range(db.POSTED_NOTIF_CAP + 5):
            db.record_posted_notif(conn, 100 + index, ACCOUNT,
                                   "t%d" % index, NOW_MS + index)
        count = conn.execute(
            "SELECT COUNT(*) FROM notif_actions").fetchone()[0]
        assert count == db.POSTED_NOTIF_CAP
        assert db.posted_notif_for(conn, 100) is None
        assert db.posted_notif_for(conn, 100 + db.POSTED_NOTIF_CAP +
                                   4) == (ACCOUNT, "t%d" %
                                          (db.POSTED_NOTIF_CAP + 4))
    finally:
        conn.close()


def _seed_db(path):
    conn = db.open_db(str(path))
    try:
        _seed_thread(conn)
    finally:
        conn.close()


def test_service_ignores_unposted_id(tmp_path):
    path = tmp_path / "mail.db"
    _seed_db(path)
    made = []
    service = _service(
        path, client_for=lambda account: made.append(account))
    assert service.handle_notification_action(999, "archive") is False
    assert made == []
    assert service.handle_notification_action("nope", "archive") \
        is False


def test_service_rejects_unknown_key(tmp_path):
    path = tmp_path / "mail.db"
    _seed_db(path)
    conn = db.open_db(str(path))
    try:
        db.record_posted_notif(conn, 41, ACCOUNT, "t1", NOW_MS)
    finally:
        conn.close()
    opened = []
    service = _service(path, opener=opened.append)
    assert service.handle_notification_action(41, "snooze") is False
    assert opened == []


def test_service_archives_posted_card_once(tmp_path):
    path = tmp_path / "mail.db"
    _seed_db(path)
    conn = db.open_db(str(path))
    try:
        db.record_posted_notif(conn, 41, ACCOUNT, "t1", NOW_MS)
    finally:
        conn.close()
    client = FakeClient()
    service = _service(
        path, client_for=lambda account: client,
        handler=daemon_mod.ActionHandler())
    assert service.handle_notification_action(41, "archive") is True
    assert ("t1", None, ["INBOX"]) in client.modified
    check = db.open_db(str(path))
    try:
        assert db.get_thread(check, ACCOUNT, "t1").in_inbox is False
    finally:
        check.close()
    assert service.handle_notification_action(41, "archive") is False
    assert client.closed >= 1


def test_service_opens_posted_card(tmp_path):
    path = tmp_path / "mail.db"
    _seed_db(path)
    conn = db.open_db(str(path))
    try:
        db.record_posted_notif(conn, 41, ACCOUNT, "t1", NOW_MS)
    finally:
        conn.close()
    opened = []
    service = _service(path, opener=opened.append)
    assert service.handle_notification_action(41, "default") is True
    assert opened == [notify_mod.open_thread_uri(ACCOUNT, "t1")]


def test_method_dispatch_replies_empty_tuple(tmp_path):
    markers = _run_actions_driver(tmp_path / "mail.db")
    assert markers.get("VALUE") == "()"
    assert markers.get("ERROR") == "None"
    assert markers.get("OPENED") == \
        "['%s']" % notify_mod.open_thread_uri(ACCOUNT, "t1")
    assert markers.get("UNKNOWN_VALUE") == "()"
    assert markers.get("UNKNOWN_ERROR") == "None"
    assert markers.get("BAD_ERROR") == "present"


def test_action_target_matches_service() -> None:
    assert notify_mod.ACTION_TARGET_HINT == "x-xx-wm-action-target"
    assert notify_mod.ACTION_BUS_NAME == daemon_mod.DAEMON_BUS_NAME
    assert notify_mod.action_target_value() == \
        "%s %s %s" % (daemon_mod.DAEMON_BUS_NAME,
                      actions_mod.OBJECT_PATH, actions_mod.INTERFACE)
    assert "HandleNotificationAction" in actions_mod.INTROSPECTION_XML
    service_file = (ROOT / "data" / "dbus-1" /
                    "io.piercingxx.XXEmail.Daemon.service.in")
    assert ("Name=%s" % daemon_mod.DAEMON_BUS_NAME) in \
        service_file.read_text(encoding="utf-8")
    meson = (ROOT / "meson.build").read_text(encoding="utf-8")
    assert "io.piercingxx.XXEmail.Daemon.service" in meson
    main_source = (SRC / "main.py").read_text(encoding="utf-8")
    assert "--actions-service" in main_source


def _tick_db(path):
    conn = db.open_db(str(path))
    _seed_thread(conn)
    return conn


def test_run_tick_records_posted_cards(tmp_path, monkeypatch):
    conn = _tick_db(tmp_path / "mail.db")
    try:
        outcome = SyncOutcome(email=ACCOUNT, ok=True,
                              new_ids=["t1"])
        sent = []

        def _fake_send(connection, payload, done=None,
                       expire_ms=15000):
            sent.append(dict(payload))
            if done is not None:
                done(4242)

        monkeypatch.setattr(notify_mod, "send_notification",
                            _fake_send)
        sunk = []
        result = daemon_mod.run_tick(
            conn, lambda account: _Engine(outcome), object(),
            lambda account: FakeClient(), now_ms=NOW_MS,
            app_config=_Config(),
            shell_config={},
            posted_sink=lambda sid, account, thread: sunk.append(
                (sid, account, thread)))
        assert result["notified"] == {ACCOUNT: 4242}
        assert sunk == [(4242, ACCOUNT, "t1")]
        assert sent[0]["thread_id"] == "t1"
    finally:
        conn.close()


def test_posted_sink_for_db_round_trips(tmp_path):
    db_path = tmp_path / "mail.db"
    conn = db.open_db(str(db_path))
    conn.close()
    sink = daemon_mod.posted_sink_for_db(db_path)
    sink(4242, ACCOUNT, "t1")
    sink(0, ACCOUNT, "t1")
    handle = db.open_db(str(db_path))
    try:
        assert db.posted_notif_for(handle, 4242) == (ACCOUNT, "t1")
        assert db.posted_notif_for(handle, 0) is None
    finally:
        handle.close()


def test_collect_posted_replies_skips_without_gi_need(tmp_path):
    daemon_mod.collect_posted_replies({"payloads": {}, "notified": {}})
    daemon_mod.collect_posted_replies(
        {"payloads": {"a@x": {}}, "notified": {"a@x": 7}})


class _Engine:
    def __init__(self, outcome) -> None:
        self._outcome = outcome

    def sync_account(self, account):
        return self._outcome


class _Config:
    notifications_enabled = True
