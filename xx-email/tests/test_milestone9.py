"""Milestone 9 acceptance: settings/accounts/logs/backup/polish.

Headless: log redaction + ring/file behavior, versioned backup
export/import round-trip and validate-before-write, the shell
DnD/mute/focus notification gate, the single hints-dict seam (real
GLib Variant, never nested a{sv}), and static guards pinning the
PreferencesDialog + portal chooser + logs wiring. The Broadway
smoke below opens every page at 320x640 and gates minimum widths;
it skips when gtk4-broadwayd is missing.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from xx_email import backup as backup_mod
from xx_email import config as config_mod
from xx_email import db
from xx_email import log as log_mod
from xx_email import notify as notify_mod

ACCOUNT = "user@dev.xxemail"
NOW_MS = 1_700_000_000_000
MON_10AM = datetime(2024, 1, 8, 10, 0, tzinfo=timezone.utc)


SRC = Path(__file__).resolve().parent.parent / "src" / "xx_email"
UI = SRC / "ui"


@pytest.fixture
def conn():
    handle = db.connect(":memory:")
    db.migrate(handle)
    yield handle
    handle.close()


def _seed(conn, email=ACCOUNT):
    db.upsert_account(conn, db.Account(
        email=email, display_name="Dev", history_id="100",
        last_sync_at=NOW_MS))


# ------------------------------------------------------------------ redaction


def test_redaction_scrubs_tokens_headers_codes_and_addresses():
    dirty = ("POST token ya29.ABC to ada@example.com with "
             "refresh_token=1//xyz and "
             "https://x/oauth?code=authcode9&state=s")
    clean = log_mod.redact(dirty)
    assert "ya29.ABC" not in clean
    assert "ada@example.com" not in clean
    assert "***@" in clean
    assert "1//xyz" not in clean
    assert "authcode9" not in clean
    assert "state=s" in clean


def test_redaction_scrubs_bearer_and_authorization_header():
    dirty = ("Authorization: Bearer ya29.secret\n"
             "mailed bob@example.com the id_token={\"id_token\": \"eyJ9\"}")
    clean = log_mod.redact(dirty)
    assert "ya29.secret" not in clean
    assert "bob@example.com" not in clean
    assert "eyJ9" not in clean
    assert "[REDACTED]" in clean


def test_redaction_keeps_ordinary_prose():
    assert log_mod.redact("the token expired") == "the token expired"
    assert log_mod.redact("refresh your tokens daily") == \
        "refresh your tokens daily"


def test_redaction_is_idempotent_and_empty_safe():
    once = log_mod.redact("Bearer abc bob@example.com")
    assert log_mod.redact(once) == once
    assert log_mod.redact("") == ""
    assert "Bearer [REDACTED]" in once


# ------------------------------------------------------------ ring + file log


def test_ring_caps_at_300_and_tail_reads_file(tmp_path):
    app_log = log_mod.AppLog(tmp_path / "state")
    app_log.init(tmp_path / "state")
    for index in range(320):
        app_log.i("sync", f"pass {index:d}")
    assert len(app_log.dump().splitlines()) == log_mod.RING_SIZE
    assert "pass 0" not in app_log.dump()
    assert "pass 319" in app_log.dump()
    assert "pass 319" in app_log.tail(5)
    assert app_log.log_path() == tmp_path / "state" / log_mod.LOG_NAME


def test_file_rotates_past_cap_and_share_is_redacted(tmp_path):
    app_log = log_mod.AppLog()
    app_log.init(tmp_path)
    app_log.i("auth", "stored grant for ada@example.com Bearer abc")
    for _ in range(4000):
        app_log.i("sync", "x" * 200)
    names = {path.name for path in tmp_path.iterdir()}
    assert log_mod.LOG_NAME in names
    assert log_mod.PREV_NAME in names
    shared = app_log.share_text()
    assert "ada@example.com" not in shared
    assert "Bearer abc" not in shared


# --------------------------------------------------------------------- backup


def _seed_wake_and_page(conn):
    _seed(conn)
    db.upsert_threads(conn, [db.Thread(
        account_email=ACCOUNT, id="t1", date=NOW_MS,
        subject="Hi", from_address="a@x", from_name="A")])
    db.upsert_snooze_wake(conn, db.SnoozeWake(
        account_email=ACCOUNT, thread_id="t1",
        target_at=NOW_MS + 60_000, created_at=NOW_MS))
    db.upsert_folder_page(conn, db.FolderPage(
        account_email=ACCOUNT, folder_key="inbox",
        next_page_token="tok9"))


def test_export_never_carries_tokens_or_bodies(conn, tmp_path):
    _seed_wake_and_page(conn)
    payload = backup_mod.export_backup(conn)
    raw = json.dumps(payload)
    assert payload["kind"] == "xx-email-backup"
    assert payload["version"] == 1
    assert payload["settings"]["last_account"] is not None
    assert len(payload["snooze_wakes"]) == 1
    assert len(payload["folder_pages"]) == 1
    assert "refresh" not in raw.lower()
    assert "Bearer" not in raw
    out = backup_mod.write_export_file(tmp_path / "b.json", payload)
    assert backup_mod.read_export_file(out)["kind"] == \
        "xx-email-backup"


def test_restore_round_trips_wakes_pages_and_settings(conn, tmp_path):
    _seed_wake_and_page(conn)
    payload = backup_mod.export_backup(conn)
    payload["settings"]["undo_seconds"] = 20
    fresh = db.connect(":memory:")
    db.migrate(fresh)
    try:
        _seed(fresh)
        result = backup_mod.restore_backup(
            fresh, payload, data_dir=tmp_path,
            config_path=tmp_path / "config.json")
        assert result["restored"]["wakes"] == 1
        assert result["restored"]["pages"] == 1
        assert db.get_snooze_wake(fresh, ACCOUNT, "t1") is not None
        assert db.get_folder_page(
            fresh, ACCOUNT, "inbox").next_page_token == "tok9"
        assert json.loads(
            (tmp_path / "config.json").read_text(
                encoding="utf-8"))["undo_seconds"] == 20
    finally:
        fresh.close()


def test_restore_requeues_payload_backed_sends(conn, tmp_path):
    from xx_email.domain import OutboxFiles

    _seed(conn)
    files_dir = tmp_path / "files"
    entry_id = db.insert_outbox(conn, db.OutboxEntry(
        account_email=ACCOUNT, kind="SEND", subject="Hello",
        size=3, target_at=NOW_MS, state="QUEUED", created_at=NOW_MS))
    OutboxFiles.write_new(files_dir, entry_id, b"abc")
    db.set_outbox_payload(conn, entry_id,
                          OutboxFiles.relative_path(entry_id), 3)
    payload = backup_mod.export_backup(conn)
    assert payload["outbox"][0]["payload"] == \
        f"outbox/{entry_id:d}.eml"
    fresh = db.connect(":memory:")
    db.migrate(fresh)
    try:
        _seed(fresh)
        db.insert_outbox(fresh, db.OutboxEntry(
            account_email=ACCOUNT, kind="SEND", subject="Decoy",
            size=1, target_at=NOW_MS, state="CANCELLED",
            created_at=NOW_MS))
        result = backup_mod.restore_backup(
            fresh, payload, data_dir=files_dir,
            config_path=tmp_path / "config.json")
        assert result["restored"]["outbox"] == 1
        rows = [row for row in db.outbox_for_account(fresh, ACCOUNT)
                if row.state == "QUEUED"]
        assert len(rows) == 1
        assert rows[0].subject == "Hello"
        assert (files_dir / f"outbox/{rows[0].id:d}.eml"
                ).read_bytes() == b"abc"
        assert not (files_dir / f"outbox/{entry_id:d}.eml").exists() \
            or rows[0].id == entry_id
    finally:
        fresh.close()


def test_restore_validates_before_writing(conn, tmp_path):
    _seed(conn)
    config_path = tmp_path / "config.json"
    config_mod.save(config_mod.AppConfig(undo_seconds=7),
                    config_path)
    with pytest.raises(backup_mod.BackupError):
        backup_mod.restore_backup(conn, {"kind": "nope"},
                                  data_dir=tmp_path,
                                  config_path=config_path)
    with pytest.raises(backup_mod.BackupError):
        backup_mod.restore_backup(
            conn, {"kind": "xx-email-backup", "version": 99,
                   "settings": {}, "snooze_wakes": [],
                   "folder_pages": [], "outbox": []},
            data_dir=tmp_path, config_path=config_path)
    bad_wake = backup_mod.export_backup(conn)
    bad_wake["snooze_wakes"] = [{"account": ACCOUNT}]
    with pytest.raises(backup_mod.BackupError):
        backup_mod.restore_backup(conn, bad_wake, data_dir=tmp_path,
                                  config_path=config_path)
    assert db.all_snooze_wakes(conn) == []
    assert config_mod.load(config_path).undo_seconds == 7


def test_restore_skips_unknown_accounts_and_missing_payloads(
        conn, tmp_path):
    _seed(conn)
    payload = backup_mod.export_backup(conn)
    payload["snooze_wakes"] = [
        {"account": "ghost@example.com", "thread": "t9",
         "target_at": NOW_MS, "created_at": NOW_MS}]
    payload["outbox"] = [
        {"account": ACCOUNT, "kind": "SEND", "thread": None,
         "subject": "Gone", "size": 3, "target_at": NOW_MS,
         "state": "QUEUED", "attempts": 0, "created_at": NOW_MS,
         "payload": "outbox/424242.eml"}]
    result = backup_mod.restore_backup(
        conn, payload, data_dir=tmp_path,
        config_path=tmp_path / "config.json")
    assert result["restored"] == {"wakes": 0, "pages": 0,
                                 "outbox": 0}
    assert len(result["skipped"]) == 2
    assert db.all_snooze_wakes(conn) == []


# ------------------------------------------------------- shell policy gate


def test_policy_matrix_mute_dnd_focus():
    assert notify_mod.notification_allowed({}, now=MON_10AM) is True
    assert notify_mod.notification_allowed(
        {"dnd_enabled": True}, now=MON_10AM) is False
    muted = {"muted_apps":
             {notify_mod.APP_ID: MON_10AM.timestamp() + 60.0}}
    assert notify_mod.notification_allowed(muted, now=MON_10AM) \
        is False
    expired = {"muted_apps": {notify_mod.APP_ID:
                              MON_10AM.timestamp() - 1.0}}
    assert notify_mod.notification_allowed(expired, now=MON_10AM) \
        is True
    focus = {"focus_enabled": True,
             "focus_apps": [notify_mod.APP_ID]}
    assert notify_mod.notification_allowed(focus, now=MON_10AM) \
        is False
    other = {"focus_enabled": True, "focus_apps": ["other.app"]}
    assert notify_mod.notification_allowed(other, now=MON_10AM) \
        is True
    pause = dict(focus, focus_break_until=MON_10AM.timestamp() + 60)
    assert notify_mod.notification_allowed(pause, now=MON_10AM) \
        is True


def test_dnd_schedule_matcher_covers_overnight_and_malformed():
    day = {"days": [0], "start": "09:00", "end": "11:00"}
    assert notify_mod.dnd_active({"dnd_schedules": [day]},
                                 MON_10AM) is True
    assert notify_mod.dnd_active({"dnd_schedules": [day]},
                                 MON_10AM.replace(hour=8)) is False
    overnight = {"days": [0], "start": "22:00", "end": "06:30"}
    late = MON_10AM.replace(hour=23, minute=30)
    early = datetime(2024, 1, 9, 5, 0, tzinfo=timezone.utc)
    assert notify_mod.dnd_active({"dnd_schedules": [overnight]},
                                 late) is True
    assert notify_mod.dnd_active({"dnd_schedules": [overnight]},
                                 early) is True
    assert notify_mod.dnd_active({"dnd_schedules": [overnight]},
                                 MON_10AM) is False
    broken = {"days": "mon", "start": "9am", "end": "nope"}
    assert notify_mod.dnd_active({"dnd_schedules": [broken]},
                                 MON_10AM) is False
    assert notify_mod.dnd_active({"dnd_schedules": "soon"},
                                 MON_10AM) is False


def test_focus_schedule_holds_only_listed_apps():
    sched = {"days": [0], "start": "09:00", "end": "17:00"}
    config = {"focus_schedules": [sched],
              "focus_apps": [notify_mod.APP_ID + ".desktop"]}
    assert notify_mod.focus_holds_app(
        config, MON_10AM, MON_10AM.timestamp()) is True
    assert notify_mod.focus_holds_app(
        {"focus_schedules": [sched], "focus_apps": []},
        MON_10AM, MON_10AM.timestamp()) is False


# Real GLib must build and unpack the Notify Variant, but importing
# gi twice in one pytest process mixes metaclass generations, so
# these checks run in notify_tick_driver.py (fresh interpreter,
# markers on stdout) instead of in-process.


_TICK_DRIVER = str(Path(__file__).parent / "notify_tick_driver.py")


def _seed_tick_db(path: Path) -> None:
    conn = db.open_db(str(path))
    try:
        db.upsert_account(conn, db.Account(
            email=ACCOUNT, display_name="Dev", history_id="120",
            last_sync_at=NOW_MS))
        db.upsert_threads(conn, [db.Thread(
            account_email=ACCOUNT, id="t2", snippet="s",
            subject="Sub", from_address="Bob <bob@example.com>",
            from_name="Bob", date=NOW_MS, message_count=1,
            unread_count=1, has_attachments=False, starred=False,
            in_inbox=True, categories="",
            labels_csv="INBOX,UNREAD")])
    finally:
        conn.close()


def _run_tick_driver(mode: str, db_path: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join((
        str(Path(__file__).resolve().parent.parent / "src"),
        str(Path(__file__).resolve().parent.parent.parent /
            "common"),
    ))
    completed = subprocess.run(
        [sys.executable, _TICK_DRIVER, mode, str(db_path)],
        env=env, capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, \
        f"tick driver failed: {completed.stderr[-2000:]}"
    markers = {}
    for line in completed.stdout.splitlines():
        if ": " in line:
            key, _, value = line.partition(": ")
            markers[key.strip()] = value.strip()
    assert markers.get("ERRORS") == "none", \
        completed.stdout[-2000:]
    return markers


def test_tick_holds_notifications_under_dnd_but_still_syncs(tmp_path):
    db_path = tmp_path / "mail.db"
    _seed_tick_db(db_path)
    markers = _run_tick_driver("tick-held", db_path)
    assert markers.get("OUTCOMES_OK") == "True"
    assert markers.get("PAYLOADS") == "[]"
    assert markers.get("NOTIFIES") == "0"


def test_tick_notifies_when_shell_policy_is_clear(tmp_path):
    db_path = tmp_path / "mail.db"
    _seed_tick_db(db_path)
    markers = _run_tick_driver("tick-clear", db_path)
    assert markers.get("OUTCOMES_OK") == "True"
    assert markers.get("PAYLOADS") == f"['{ACCOUNT}']"
    assert markers.get("NOTIFIES") == "1"
    assert markers.get("APP") == "io.piercingxx.XXEmail"
    assert markers.get("REPLACES") == str(
        notify_mod.notification_id_for_account(ACCOUNT))
    assert markers.get("SUMMARY") == "New mail"
    assert markers.get("BODY") == "Bob"
    assert markers.get("HINTS") == \
        "{'desktop-entry': 'io.piercingxx.XXEmail', " \
        "'category': 'email.arrived', 'urgency': 1, " \
        "'x-xx-wm-action-target': 'io.piercingxx.XXEmail.Daemon " \
        "/io/piercingxx/XXEmail/Daemon io.piercingxx.XXEmail.Daemon1'}"
    assert markers.get("REPLYTYPE") == "(u)"


# ------------------------------------------------------------- hints seam


def test_hints_seam_builds_one_real_variant(tmp_path):
    markers = _run_tick_driver("hints", tmp_path / "unused.db")
    assert markers.get("HINT_KEYS") == \
        "['category', 'desktop-entry', 'urgency', " \
        "'x-xx-wm-action-target']"
    assert markers.get("HINT_TYPES_OK") == "True"
    assert markers.get("TUPLE_APP") == "io.piercingxx.XXEmail"
    assert markers.get("TUPLE_REPLACES") == "7"
    assert markers.get("TUPLE_HINTS") == \
        "{'desktop-entry': 'io.piercingxx.XXEmail', " \
        "'category': 'email.arrived', 'urgency': 1, " \
        "'x-xx-wm-action-target': 'io.piercingxx.XXEmail.Daemon " \
        "/io/piercingxx/XXEmail/Daemon io.piercingxx.XXEmail.Daemon1'}"
    assert markers.get("TUPLE_LEN") == "8"
    source = (SRC / "notify.py").read_text(encoding="utf-8")
    assert source.count("notification_hints()") >= 2
    assert "TODO" not in source
    assert "x-xx-wm-action-target" in source


# ------------------------------------------------------- static page guards


def test_settings_is_a_page_of_setting_rows():
    text = (UI / "settings_page.py").read_text(encoding="utf-8")
    assert "SettingsPage" in text
    assert "settings_navigation_page" in text
    assert "Adw.PreferencesDialog" not in text
    assert "Adw.PreferencesWindow" not in text
    assert "Adw.MessageDialog" not in text
    # Every Android setting with a real effect, minus theme_mode:
    # the phone theme comes from the shell (xx_ui), so no per-app
    # theme row.
    for key in ("swipe_right", "swipe_left", "undo_seconds",
                "sync_minutes", "send_and_archive",
                "notifications_enabled", "remote_images", "client_id",
                "last_account"):
        assert key in text, key
    assert "theme_mode" not in text
    assert "Gtk.FileDialog" in text
    assert "UNINSTALL_WARNING" in text or "uninstall" in text.lower()
    assert "TODO" not in text


def test_logs_page_copies_redacted_text():
    text = (UI / "logs_page.py").read_text(encoding="utf-8")
    assert "get_clipboard" in text
    assert "share_text" in text or "load_logs" in text
    assert "scrubbed" in text
    assert "TODO" not in text


def test_main_wires_settings_dialog_and_logs_page():
    text = (SRC / "main.py").read_text(encoding="utf-8")
    assert "SettingsPage" in text
    assert "SettingsDialog" not in text
    assert "LogsPage" in text
    assert "_push_logs" in text
    assert "_push_settings" in text
    assert "_push_folders" in text
    assert "revoke_grant" in text
    assert "request_sign_out" in text
    for banned in ("subprocess", "time.sleep", "call_sync",
                   "lookup_sync", "store_sync", "clear_sync",
                   "bus_get_sync"):
        assert banned not in text, banned


def test_remove_flow_warns_before_tokens_drop():
    text = (SRC / "setup.py").read_text(encoding="utf-8")
    assert "UNINSTALL_WARNING" in text
    assert text.index("restore") < text.index("store.remove"), \
        "snooze restore must precede token removal"


def _shell_tree() -> Path:
    root = Path(__file__).resolve().parent.parent
    for candidate in (root / "ref" / "xx-wm", root.parent / "xx-wm"):
        if (candidate / "docs" / "config.md").is_file():
            return candidate
    pytest.skip("xx-wm source not found")


def test_shell_policy_keys_match_documented_config():
    """The DnD/mute/focus keys we read are the shell's public API."""
    docs = (_shell_tree() / "docs" / "config.md").read_text(
        encoding="utf-8")
    for key in ("muted_apps", "dnd_enabled", "dnd_schedules",
                "focus_enabled", "focus_apps", "focus_schedules",
                "focus_break_until", "lock_screen_notifications"):
        assert f"`{key}`" in docs, key
    source = (SRC / "notify.py").read_text(encoding="utf-8")
    for key in ("muted_apps", "dnd_enabled", "dnd_schedules",
                "focus_enabled", "focus_apps", "focus_schedules",
                "focus_break_until"):
        assert f'"{key}"' in source, key
    daemon_source = (SRC / "daemon.py").read_text(encoding="utf-8")
    assert "notification_allowed" in daemon_source


def test_no_32bit_gobject_ids_in_new_pages():
    """Ids from millisecond timestamps need 64-bit storage."""
    for name in ("settings_page.py", "logs_page.py"):
        text = (UI / name).read_text(encoding="utf-8")
        assert "GObject" not in text, name
    for path in (UI / "common.py", SRC / "main.py"):
        text = path.read_text(encoding="utf-8")
        assert "GObject.Property(type=int)" not in text, path


def test_settings_hooks_cover_accounts_logs_and_backup():
    text = (SRC / "main.py").read_text(encoding="utf-8")
    assert "class _SettingsHooks" in text
    assert "class _LogsHooks" in text
    for method in ("load_state", "add_account", "remove_account",
                   "revoke_account", "open_logs", "export_backup",
                   "import_backup", "load_logs"):
        assert f"def {method}" in text, method
    assert 'mode="add"' in text


# ------------------------------------------------------ broadway audit


_DRIVER = str(Path(__file__).parent / "settings_broadway_driver.py")


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


def test_every_page_fits_360x720(broadway_env):
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
        [sys.executable, _DRIVER, "audit"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert completed.returncode == 0, \
        f"settings driver failed: {completed.stderr[-2000:]}"
    markers = {}
    for line in completed.stdout.splitlines():
        if ": " in line:
            key, _, value = line.partition(": ")
            markers[key.strip()] = value.strip()
    assert markers.get("ERRORS") == "none"
    assert markers.get("PAGES_OK") == \
        "mailbox thread search compose setup logs settings"
    # Budget is the 360 px snap canvas: the design-system bottom
    # tabs and chip bar carry fixed minima (~356 px) tuned for it,
    # and the exact-fit gate is the 360x720 snap with zero warnings.
    for name in ("mailbox", "thread", "search", "compose", "setup",
                 "logs", "settings"):
        assert f"MIN-ERROR {name}" not in markers, \
            markers.get(f"MIN-ERROR {name}")
        raw = markers.get(f"MIN {name}", "9999")
        assert raw.isdigit(), f"{name} reported no width: {raw!r}"
        assert int(raw) <= 360, \
            f"{name} needs {raw} px — overflows 360x720"
