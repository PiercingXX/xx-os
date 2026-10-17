"""Headless driver for milestone 9 Notify assertions (not collected).

The Notify path needs the real GLib to build and unpack its
`(susssasa{sv}i)` Variant, but importing gi a second time in one
pytest process mixes metaclass generations — later attribute access
dies with a metaclass conflict and old instances stop unpacking.
These checks therefore run here, in a fresh interpreter, printing
KEY: value markers. Any unexpected exception exits nonzero so the
parent test fails instead of passing on empty output. No display is
needed (GLib only, never Gtk); argv[2] is the seeded db file for
the tick modes.
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))

from gi.repository import GLib  # noqa: E402

from xx_email import config as config_mod  # noqa: E402
from xx_email import daemon as daemon_mod  # noqa: E402
from xx_email import db as db_mod  # noqa: E402
from xx_email import notify as notify_mod  # noqa: E402
from xx_email.sync_service import SyncOutcome  # noqa: E402

ACCOUNT = "user@dev.xxemail"


class _QuietResult:
    def __init__(self, server_id: int) -> None:
        self._server_id = server_id

    def unpack(self):
        return (self._server_id,)


class _RecordingBus:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def call(self, bus, path, iface, method, params, reply_type,
             flags, timeout, cancellable, callback) -> None:
        self.calls.append({"method": method, "params": params,
                           "reply_type": reply_type})
        if callback is not None:
            callback(self, _QuietResult(41))

    def call_finish(self, result):
        return result


class _QuietClient:
    def modify_thread(self, thread_id, add=None, remove=None):
        return {}


def _engine_for():
    outcome = SyncOutcome(email=ACCOUNT, ok=True,
                          new_ids=["t2"], checkpoint="120")

    def _engine(_account):
        class _Engine:
            def sync_account(self, _email):
                return outcome

            def close(self):
                pass

        return _Engine()

    return _engine


def _mode_tick(db_path: str, shell: dict) -> None:
    conn = db_mod.open_db(db_path)
    try:
        bus = _RecordingBus()
        result = daemon_mod.run_tick(
            conn, _engine_for(), bus, lambda _a: _QuietClient(),
            now_ms=1_700_000_000_000,
            app_config=config_mod.AppConfig(
                notifications_enabled=True),
            shell_config=shell)
    finally:
        conn.close()
    outcomes = result["outcomes"]
    print(f"OUTCOMES_OK: "
          f"{all(getattr(o, 'ok', False) for o in outcomes)}")
    print(f"PAYLOADS: {sorted(result['payloads'])}")
    notifies = [call for call in bus.calls
                if call["method"] == "Notify"]
    print(f"NOTIFIES: {len(notifies)}")
    print(f"NOTIFIED: {result['notified']}")
    for call in notifies:
        unpacked = call["params"].unpack()
        print(f"APP: {unpacked[0]}")
        print(f"REPLACES: {unpacked[1]}")
        print(f"SUMMARY: {unpacked[3]}")
        print(f"BODY: {unpacked[4]}")
        print(f"ACTIONS: {list(unpacked[5])}")
        print(f"HINTS: {dict(unpacked[6])}")
        print(f"REPLYTYPE: {call['reply_type'].dup_string()}")


def _mode_actions(db_path: str) -> None:
    from xx_email import actions as actions_mod

    conn = db_mod.open_db(db_path)
    try:
        db_mod.upsert_account(conn, db_mod.Account(
            email=ACCOUNT, display_name="Dev", history_id="100",
            last_sync_at=1_700_000_000_000))
        db_mod.upsert_threads(conn, [db_mod.Thread(
            account_email=ACCOUNT, id="t1", snippet="s",
            subject="Sub", from_address="ada@example.com",
            from_name="Ada", date=1_700_000_000_000,
            message_count=1, unread_count=1, has_attachments=False,
            starred=False, in_inbox=True, categories="",
            labels_csv="INBOX,UNREAD")])
        db_mod.record_posted_notif(conn, 77, ACCOUNT, "t1",
                                   1_700_000_000_000)
    finally:
        conn.close()
    opened = []
    service = actions_mod.ActionsService(
        db_opener=lambda: db_mod.open_db(db_path),
        client_for=lambda _account: _QuietClient(),
        opener=opened.append, spawn=lambda fn: fn(),
        idle=lambda fn, *args: fn(*args))

    class _Params:
        def __init__(self, values) -> None:
            self._values = values

        def unpack(self):
            return self._values

    class _Invocation:
        def __init__(self) -> None:
            self.value = None
            self.error = None

        def return_value(self, variant):
            self.value = variant

        def return_dbus_error(self, name, message):
            self.error = (name, message)

    invocation = _Invocation()
    service.handle_method_call(None, None, actions_mod.OBJECT_PATH,
                               actions_mod.INTERFACE,
                               "HandleNotificationAction",
                               _Params((77, "default")), invocation)
    print(f"VALUE: {invocation.value.unpack()!r}")
    print(f"ERROR: {invocation.error!r}")
    print(f"OPENED: {opened!r}")

    unknown = _Invocation()
    service.handle_method_call(None, None, actions_mod.OBJECT_PATH,
                               actions_mod.INTERFACE,
                               "HandleNotificationAction",
                               _Params((999, "archive")), unknown)
    print(f"UNKNOWN_VALUE: {unknown.value.unpack()!r}")
    print(f"UNKNOWN_ERROR: {unknown.error!r}")

    bad = _Invocation()
    service.handle_method_call(None, None, actions_mod.OBJECT_PATH,
                               actions_mod.INTERFACE, "Nope",
                               _Params((77, "default")), bad)
    print(f"BAD_ERROR: {'present' if bad.error else 'missing'}")


def _mode_hints() -> None:
    hints = notify_mod.notification_hints()
    print(f"HINT_KEYS: {sorted(hints)}")
    print(f"HINT_TYPES_OK: "
          f"{all(isinstance(v, GLib.Variant) for v in hints.values())}")
    params = notify_mod._notify_variant("s", "b", [], 7)
    unpacked = params.unpack()
    print(f"TUPLE_APP: {unpacked[0]}")
    print(f"TUPLE_REPLACES: {unpacked[1]}")
    print(f"TUPLE_HINTS: {dict(unpacked[6])}")
    print(f"TUPLE_LEN: {len(unpacked)}")


def main() -> int:
    try:
        mode = sys.argv[1]
        if mode == "tick-held":
            _mode_tick(sys.argv[2], {"dnd_enabled": True})
        elif mode == "tick-clear":
            _mode_tick(sys.argv[2], {})
        elif mode == "hints":
            _mode_hints()
        elif mode == "actions":
            _mode_actions(sys.argv[2])
        else:
            print(f"unknown mode {mode}")
            return 2
    except Exception:
        traceback.print_exc()
        return 1
    print("ERRORS: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
