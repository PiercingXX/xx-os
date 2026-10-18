"""Broadway smoke test for the compose lane (needs a real display).

Starts gtk4-broadwayd, seeds the real store with real-looking rows
(including Android-style millisecond dates), then runs
tests/compose_broadway_driver.py in a subprocess: it builds the real
Mailbox, Compose (new + reply prefill) and Thread pages, pumps the
main loop for ~1 s, and reports focus markers. Skips cleanly only
when gtk4-broadwayd is missing; any driver exception fails.
"""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from xx_email import config as config_mod
from xx_email import db as db_mod
from xx_email import outbox as outbox_mod
from xx_email import setup as setup_mod

ACCOUNT = "user@dev.xxemail"
NOW_MS = 1_769_000_000_000

_DRIVER = str(Path(__file__).parent / "compose_broadway_driver.py")


def _socket_path(display: str) -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR",
                             f"/run/user/{os.getuid()}")
    return os.path.join(runtime, f"broadway{int(display[1:]) + 1}.socket")


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


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
    for number in range(51, 70):
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
        proc = subprocess.Popen(
            ["gtk4-broadwayd", "--port", str(_free_tcp_port()),
             display],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(200):
            if os.path.exists(_socket_path(display)):
                break
            time.sleep(0.05)
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


def _seed_store() -> None:
    setup_mod.ensure_app_dirs()
    conn = db_mod.open_db(config_mod.data_dir() / db_mod.DB_FILE_NAME)
    try:
        db_mod.upsert_account(conn, db_mod.Account(
            email=ACCOUNT, display_name="Dev User",
            history_id="18446744073709551615",
            last_sync_at=NOW_MS))
        db_mod.upsert_threads(conn, [
            db_mod.Thread(
                account_email=ACCOUNT, id="t-seed-1",
                snippet="Tacos at noon", subject="Lunch?",
                from_address="Ada <ada@example.com>",
                from_name="Ada", date=NOW_MS, message_count=1,
                unread_count=1, has_attachments=False,
                starred=False, in_inbox=True,
                categories="CATEGORY_PERSONAL", labels_csv="INBOX"),
            db_mod.Thread(
                account_email=ACCOUNT, id="t-seed-2",
                snippet="Build is green", subject="CI",
                from_address="ci@example.com", from_name="",
                date=NOW_MS - 3_600_000, message_count=1,
                unread_count=0, has_attachments=False,
                starred=True, in_inbox=True, categories="",
                labels_csv="INBOX,STARRED"),
        ])
        db_mod.upsert_messages(conn, [db_mod.Message(
            account_email=ACCOUNT, id="m-seed-1",
            thread_id="t-seed-1", subject="Lunch?",
            from_address="Ada <ada@example.com>",
            to_csv=ACCOUNT, cc_csv="", date=NOW_MS,
            snippet="Tacos at noon", read=False, starred=False,
            has_attachments=False, labels_csv="INBOX",
            message_id_header="<seed-parent@example.com>",
            body_plain="Tacos at noon", body_fetched=True)])
        outbox_mod.enqueue_fields(
            conn, config_mod.data_dir(), ACCOUNT,
            "scheduled@example.com", "", "", "Board deck",
            "See attached", scheduled_at=NOW_MS + 3_600_000,
            undo_seconds=10, now_ms=NOW_MS)
    finally:
        conn.close()


def _run_driver(broadway: tuple) -> dict:
    display, daemon_runtime = broadway
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
    assert completed.returncode == 0, (
        f"compose driver failed: {completed.stderr[-2000:]}")
    markers = {}
    for line in completed.stdout.splitlines():
        if ": " in line:
            key, _, value = line.partition(": ")
            markers[key.strip()] = value.strip()
    assert markers, "compose driver printed no markers"
    return markers


def test_compose_pages_build_and_pump_without_exceptions(
        broadway_env):
    _seed_store()
    markers = _run_driver(broadway_env)
    assert markers.get("ERRORS") == "none"
    assert markers.get("PAGES_OK") == "mailbox compose reply thread"
    assert markers.get("MAILBOX_TABS") == "5"
    assert markers.get("REAUTH_BANNER") == "True"
    assert markers.get("REAUTH_CAPTION_EMPTY") == "True"
    assert markers.get("HINT_ROWS") == "0"


def test_explicit_open_focuses_empty_to_only(broadway_env):
    _seed_store()
    markers = _run_driver(broadway_env)
    assert markers.get("NEW_TO_FOCUSED") == "True"
    assert markers.get("REPLY_ENTRY_FOCUSED") == "False"
    assert "ada@example.com" in markers.get("REPLY_TO", "")
    assert markers.get("REPLY_REFS") == "yes"
