"""Addrfix: dialable address vs match key, display formatting, names."""

import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from txxt import backup, phone_numbers
from txxt.contacts import ContactsResolver, ContactHit, DisplayNameCache
from txxt.daemon import seeds_with_addresses
from txxt.list_presenter import present_conversations
from txxt.store import SCHEMA_VERSION, Store

ROOT = Path(__file__).resolve().parent.parent
REF_SRC = next(
    (p for p in (Path(os.environ.get("XX_CONTACTS_SRC", "/nonexistent")),
                 ROOT / "ref" / "xx-contacts" / "src",
                 ROOT.parent / "xx-contacts" / "src")
     if (p / "xx_contacts" / "__main__.py").exists()),
    ROOT / "ref" / "xx-contacts" / "src")
TXXT_SRC = ROOT / "src"


class TestCanonicalDialable:
    def test_plus_form_kept(self):
        assert phone_numbers.canonical_dialable("+15559998888") == "+15559998888"

    def test_uk_plus_kept(self):
        assert phone_numbers.canonical_dialable("+443000000000") == "+443000000000"

    def test_us_national_infers_plus_one(self):
        assert phone_numbers.canonical_dialable("555-123-4567") == "+15551234567"

    def test_bare_uk_without_plus_stays_raw(self):
        assert phone_numbers.canonical_dialable("443000000000") == "443000000000"

    def test_short_and_alpha_stay_trimmed(self):
        assert phone_numbers.canonical_dialable(" 911 ") == "911"
        assert phone_numbers.canonical_dialable("VERIFY") == "VERIFY"

    def test_upgrade_prefers_plus(self):
        assert phone_numbers.should_replace_dialable("15559998888", "+15559998888") is True
        assert phone_numbers.should_replace_dialable("+15559998888", "15559998888") is False
        assert phone_numbers.should_replace_dialable("+1555", "+1555") is False


class TestFormatDisplay:
    def test_nanp_reads_nationally(self):
        assert phone_numbers.format_display_configured(
            "+16145550142") == "(614) 555-0142"
        assert phone_numbers.format_display_configured(
            "6145550142") == "(614) 555-0142"

    def test_foreign_numbers_keep_their_country_code(self):
        assert phone_numbers.format_display_configured(
            "+442079460958") == "+44 20 7946 0958"

    def test_unparseable_numbers_come_back_as_they_came(self):
        assert phone_numbers.format_display_configured(
            "+15550100200") == "+1 555-010-0200"
        assert phone_numbers.format_display_configured(
            "+447700900123") == "+44 7700 900123"

    def test_short_and_empty_passthrough(self):
        assert phone_numbers.format_display_configured("911") == "911"
        assert phone_numbers.format_display_configured("22395") == "22395"
        assert phone_numbers.format_display_configured("AMAZON") == "AMAZON"
        assert phone_numbers.format_display_configured("*86") == "*86"
        assert phone_numbers.format_display_configured("") == ""
        assert phone_numbers.format_display_configured(None) == ""

    def test_home_country_follows_the_user(self):
        assert phone_numbers.format_display_configured(
            "+442079460958", "GB") == "020 7946 0958"


def make_v6_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE conversations (id INTEGER NOT NULL PRIMARY KEY,"
        " participantAddresses TEXT NOT NULL, isStarred INTEGER NOT NULL DEFAULT 0,"
        " isPinned INTEGER NOT NULL DEFAULT 0, isArchived INTEGER NOT NULL DEFAULT 0,"
        " sortOrder TEXT NOT NULL DEFAULT 'PINNED_FIRST')")
    conn.execute(
        "CREATE TABLE messages (id INTEGER NOT NULL PRIMARY KEY,"
        " conversationId INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,"
        " direction TEXT NOT NULL, transport TEXT NOT NULL, body TEXT NOT NULL,"
        " timestampMillis INTEGER NOT NULL, senderAddress TEXT,"
        " isRead INTEGER NOT NULL DEFAULT 0)")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()


class TestMigration:
    def test_schema_is_seven(self):
        assert SCHEMA_VERSION == 7
        assert Store.memory().schema_version == 7

    def test_digits_only_backfilled_from_newest_raw(self, tmp_path):
        path = str(tmp_path / "txxt.db")
        make_v6_db(path)
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO conversations (id, participantAddresses) VALUES (7, '15559998888')")
        conn.execute(
            "INSERT INTO conversations (id, participantAddresses)"
            " VALUES (8, '443000000000')")
        conn.execute(
            "INSERT INTO messages (id, conversationId, direction, transport, body,"
            " timestampMillis, senderAddress, isRead)"
            " VALUES (9, 7, 'INCOMING', 'SMS', 'old', 1000, '15559998888', 0)")
        conn.execute(
            "INSERT INTO messages (id, conversationId, direction, transport, body,"
            " timestampMillis, senderAddress, isRead)"
            " VALUES (10, 7, 'INCOMING', 'SMS', 'new', 2000, '+15559998888', 0)")
        conn.execute(
            "INSERT INTO messages (id, conversationId, direction, transport, body,"
            " timestampMillis, senderAddress, isRead)"
            " VALUES (11, 8, 'INCOMING', 'SMS', 'uk', 3000, '+443000000000', 0)")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()
        store = Store(path)
        try:
            assert store.schema_version == 7
            assert store.get_conversation(7).participant_addresses == "15559998888"
            assert store.get_conversation(7).dialable_address == "+15559998888"
            assert store.get_conversation(8).dialable_address == "+443000000000"
            assert store.get_conversation(8).primary_dialable() == "+443000000000"
        finally:
            store.close()

    def test_outgoing_only_best_effort_documented(self, tmp_path):
        path = str(tmp_path / "txxt.db")
        make_v6_db(path)
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO conversations (id, participantAddresses)"
            " VALUES (9, '443000000000')")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()
        store = Store(path)
        try:
            row = store.get_conversation(9)
            assert row.participant_addresses == "443000000000"
            assert row.dialable_address == "443000000000"
        finally:
            store.close()

    def test_v6_digits_only_migrates_to_7(self, tmp_path):
        path = str(tmp_path / "txxt.db")
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE conversations (id INTEGER NOT NULL PRIMARY KEY,"
            " participantAddresses TEXT NOT NULL, isStarred INTEGER NOT NULL DEFAULT 0,"
            " isPinned INTEGER NOT NULL DEFAULT 0, isArchived INTEGER NOT NULL DEFAULT 0,"
            " sortOrder TEXT NOT NULL DEFAULT 'PINNED_FIRST',"
            " isMuted INTEGER NOT NULL DEFAULT 0,"
            " isQuarantined INTEGER NOT NULL DEFAULT 0,"
            " mutedUntilMillis INTEGER NOT NULL DEFAULT 0)")
        conn.execute(
            "CREATE TABLE messages (id INTEGER NOT NULL PRIMARY KEY,"
            " conversationId INTEGER NOT NULL REFERENCES conversations(id)"
            " ON DELETE CASCADE, direction TEXT NOT NULL, transport TEXT NOT NULL,"
            " body TEXT NOT NULL, timestampMillis INTEGER NOT NULL,"
            " senderAddress TEXT, isRead INTEGER NOT NULL DEFAULT 0,"
            " sent INTEGER NOT NULL DEFAULT 1, contentLocation TEXT,"
            " mediaPath TEXT, pendingDelete INTEGER NOT NULL DEFAULT 0)")
        conn.execute(
            "CREATE TABLE blocklist (kind TEXT NOT NULL, value TEXT NOT NULL,"
            " PRIMARY KEY (kind, value))")
        conn.execute("CREATE TABLE starred (address TEXT NOT NULL PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE settings (key TEXT NOT NULL PRIMARY KEY,"
            " value TEXT NOT NULL)")
        conn.execute(
            "CREATE TABLE meta (key TEXT NOT NULL PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO conversations (id, participantAddresses)"
            " VALUES (21, '15550100200')")
        conn.execute(
            "INSERT INTO messages (id, conversationId, direction, transport, body,"
            " timestampMillis, senderAddress, isRead, sent)"
            " VALUES (31, 21, 'INCOMING', 'SMS', 'hi', 5000, '+15550100200', 0, 1)")
        conn.execute("PRAGMA user_version = 6")
        conn.commit()
        conn.close()
        store = Store(path)
        try:
            assert store.schema_version == 7
            row = store.get_conversation(21)
            assert row.participant_addresses == "15550100200"
            assert row.dialable_address == "+15550100200"
        finally:
            store.close()

    def test_new_writes_keep_key_but_dial_plus(self):
        store = Store.memory()
        cid = store.find_or_create_conversation("+15559998888")
        row = store.get_conversation(cid)
        assert row.participant_addresses == "15559998888"
        assert row.primary_dialable() == "+15559998888"

    def test_later_plus_upgrades_digits_row(self):
        store = Store.memory()
        cid = store.find_or_create_conversation("15559998888")
        assert store.get_conversation(cid).primary_dialable() == "+15559998888"
        same = store.find_or_create_conversation("+15559998888")
        assert same == cid
        assert store.get_conversation(cid).primary_dialable() == "+15559998888"


class TestSendUsesDialable:
    def test_seeds_use_plus_for_international(self):
        store = Store.memory()
        store.persist_inbound_sms("+443000000000", "hello", 1700000000000)
        seeds = seeds_with_addresses(store, store.pending_outgoing_sms())
        assert seeds == []
        mid = store.persist_outgoing_sms("+443000000000", "reply", now_millis=1700000000001)
        seeds = seeds_with_addresses(store, store.pending_outgoing_sms())
        assert [(s.address, s.id) for s in seeds] == [("+443000000000", mid)]

    def test_daemon_send_preserves_plus(self):
        from txxt.daemon import Daemon

        store = Store.memory()
        seen: list = []

        class Inline:
            def submit(self, fn, *args):
                fn(*args)

        def failing_sender(bus, modem, address, body):
            seen.append((address, body))
            return False, "no modem"

        daemon = Daemon(
            store, sender=failing_sender, system_bus_fn=lambda: object(),
            session_bus_fn=lambda: None, idle_add=lambda fn, *a: fn(*a),
        )
        daemon._executor = Inline()
        daemon._modem_path = "/modem/0"
        mid = daemon.send_message("+443000000000", "hello")
        assert seen == [("+443000000000", "hello")]
        assert store.get_message(mid) is not None

    def test_list_view_address_is_dialable(self):
        store = Store.memory()
        store.persist_inbound_sms("+443000000000", "hello", 1700000000000)

        def display(number: str) -> str:
            return number

        views = present_conversations(
            store.get_all_conversations(), store.get_messages_for_conversation,
            display, 1700000001000,
        )
        assert views[0].address == "+443000000000"


class TestBackupFillsDialable:
    def test_android_import_fills_dialable(self):
        store = Store.memory()
        text = backup.serialize({
            "version": backup.BACKUP_VERSION,
            "messages": [
                {"id": 1, "threadId": 10, "address": "+443000000000",
                 "body": "hello", "date": 1700000000000},
            ],
            "settings": {}, "blocklist": [], "starred": [],
        })
        backup.import_backup_json(store, text)
        rows = store.get_all_conversations()
        assert len(rows) == 1
        assert rows[0].primary_dialable() == "+443000000000"

    def test_sbr_import_fills_dialable(self):
        store = Store.memory()
        xml = (
            "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
            '<smses count="1">'
            '<sms protocol="0" address="+443000000000" date="1700000000000"'
            ' type="1" subject="null" body="hello" read="1" />'
            "</smses>"
        )
        backup.import_sbr_xml(store, xml)
        rows = store.get_all_conversations()
        assert len(rows) == 1
        assert rows[0].primary_dialable() == "+443000000000"


class DictBackend:
    def __init__(self, hits=None):
        self.hits = hits or {}

    def lookup(self, number):
        return self.hits.get(number)

    def search(self, query, limit=50):
        return []

    def set_tags(self, uid, tags):
        return True


class TestDisplayCache:
    def test_fast_never_blocks_then_resolves(self):
        backend = DictBackend(hits={
            "+15550100200": ContactHit("+15550100200", "uid-g", "Grace Hopper", ()),
        })
        resolver = ContactsResolver([backend])
        cache = DisplayNameCache(resolver, now=lambda: 0.0)
        assert cache.fast("+15550100200") == "+1 555-010-0200"
        assert cache.resolve_blocking("+15550100200") == "Grace Hopper"
        assert cache.fast("+15550100200") == "Grace Hopper"

    def test_ttl_expiry_and_changed_invalidate(self):
        now = [0.0]
        backend = DictBackend(hits={
            "+15550100200": ContactHit("+15550100200", "uid-g", "Grace Hopper", ()),
        })
        resolver = ContactsResolver([backend])
        cache = DisplayNameCache(resolver, now=lambda: now[0])
        assert cache.resolve_blocking("+15550100200") == "Grace Hopper"
        now[0] += 61.0
        assert cache.fast("+15550100200") == "+1 555-010-0200"

        class Params:
            def unpack(self):
                return (1, ["uid-g"])

        cache.resolve_blocking("+15550100200")
        cache.handle_contacts_changed(None, None, None, None, None, Params(), None)
        assert cache.fast("+15550100200") == "+1 555-010-0200"

    def test_unsaved_number_shows_formatted_and_format_runs_on_every_read(
            self):
        queries = []

        class CountingBackend(DictBackend):
            def lookup(self, number):
                queries.append(number)
                return None

        cache = DisplayNameCache(ContactsResolver([CountingBackend()]),
                                 now=lambda: 0.0)
        assert cache.resolve_blocking("+16145550101") == "(614) 555-0101"
        assert cache.fast("+16145550101") == "(614) 555-0101"
        assert cache.cached_count() == 1
        import xx_phone

        real_display = xx_phone.display
        try:
            xx_phone.display = (lambda raw, country=None:
                                "+1 614-555-0101")
            assert cache.fast("+16145550101") == "+1 614-555-0101"
        finally:
            xx_phone.display = real_display
        assert queries == ["+16145550101"], queries

    def test_saved_name_is_never_run_through_the_formatter(self):
        backend = DictBackend(hits={
            "+16145550101": ContactHit("+16145550101", "uid-a",
                                       "Ada Lovelace", ()),
        })
        cache = DisplayNameCache(ContactsResolver([backend]),
                                 now=lambda: 0.0)
        real_configured = phone_numbers.format_display_configured
        try:
            def _boom(raw, settings=None):
                raise AssertionError("names are not numbers")

            phone_numbers.format_display_configured = _boom
            assert cache.resolve_blocking("+16145550101") == "Ada Lovelace"
            assert cache.fast("+16145550101") == "Ada Lovelace"
        finally:
            phone_numbers.format_display_configured = real_configured

    def test_throwing_backend_shows_the_number(self):
        class ThrowingBackend(DictBackend):
            def lookup(self, number):
                raise OSError("dead provider")

        cache = DisplayNameCache(ContactsResolver([ThrowingBackend()]),
                                 now=lambda: 0.0)
        assert cache.resolve_blocking("+16145550101") == "(614) 555-0101"
        assert cache.fast("+16145550101") == "(614) 555-0101"

    def test_failing_formatter_leaves_the_raw_address(self):
        cache = DisplayNameCache(ContactsResolver([DictBackend()]),
                                 now=lambda: 0.0)
        real_configured = phone_numbers.format_display_configured
        try:
            def _boom(raw, settings=None):
                raise RuntimeError("no telephony")

            phone_numbers.format_display_configured = _boom
            assert cache.resolve_blocking("+16145550101") == "+16145550101"
        finally:
            phone_numbers.format_display_configured = real_configured

    def test_non_numeric_sender_keeps_its_own_cache_entry(self):
        backend = DictBackend(hits={
            "MYBANK": ContactHit("MYBANK", "uid-m", "My Bank", ()),
        })
        cache = DisplayNameCache(ContactsResolver([backend]),
                                 now=lambda: 0.0)
        assert cache.resolve_blocking("MYBANK") == "My Bank"
        assert cache.resolve_blocking("VERIFY") == "VERIFY"
        assert cache.cached_count() == 2

    def test_swapping_the_provider_invalidates_cached_answers(self):
        cache = DisplayNameCache(
            ContactsResolver([DictBackend(hits={
                "+15550001111": ContactHit("+15550001111", "uid-a",
                                           "Ada Lovelace", ()),
            })]), now=lambda: 0.0)
        assert cache.resolve_blocking("+15550001111") == "Ada Lovelace"
        cache.set_resolver(ContactsResolver([DictBackend()]))
        assert cache.resolve_blocking("+15550001111") == "+1 555-000-1111"

    def test_formatting_variance_shares_one_cache_entry(self):
        queries = []

        class CountingBackend(DictBackend):
            def lookup(self, number):
                queries.append(number)
                return ContactHit(number, "uid-a", "Ada Lovelace", ())

        cache = DisplayNameCache(ContactsResolver([CountingBackend()]),
                                 now=lambda: 0.0)
        assert cache.resolve_blocking("+15550001111") == "Ada Lovelace"
        assert cache.resolve_blocking("  +1 (555) 000-1111  ") == \
            "Ada Lovelace"
        assert len(queries) == 1, queries
        assert cache.cached_count() == 1


def _free_display():
    for number in range(5, 100):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", 8080 + number))
        except OSError:
            probe.close()
            continue
        probe.close()
        return number
    return None


_BROADWAY_HELPER = r'''
import os
import sys
import time
import subprocess
import threading
from pathlib import Path

txxt_src, ref_src, db_path = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, txxt_src)
sys.path.insert(0, os.path.join(os.path.dirname(txxt_src), "..", "common"))

from gi.repository import GLib, Gio

from txxt.contacts import GioContactsBackend

BUS = "io.piercingxx.Contacts"
PATH = "/io/piercingxx/Contacts"
IFACE = "io.piercingxx.Contacts1"

env = {**os.environ, "PYTHONPATH": ref_src}
svc = subprocess.Popen([sys.executable, "-m", "xx_contacts",
                        "--service", "--db", db_path], env=env)
try:
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def call(method, args, sig):
        return bus.call_sync(
            BUS, PATH, IFACE, method,
            GLib.Variant(sig, args) if sig else None,
            None, 0, 5000, None).unpack()

    deadline = time.monotonic() + 15
    while True:
        try:
            call("List", (0,), "(t)")
            break
        except Exception:
            if time.monotonic() > deadline:
                raise AssertionError("service did not appear on the bus")
            time.sleep(0.2)

    vcard = ("BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Grace Hopper\r\n"
             "TEL:+15550100200\r\nEND:VCARD\r\n")
    (uid,) = call("Create", (vcard,), "(s)")
    assert uid, "service created no uid"

    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Adw, Gio as Gio2, GLib as GLib2, Gtk

    from txxt.main import TxxtWindow

    errors: list = []

    def pump(seconds):
        context = GLib2.MainContext.default()
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            while context.pending():
                context.iteration(False)
            time.sleep(0.02)
            context.iteration(False)
        assert errors == [], f"errors during pump: {errors!r}"

    old_hook = sys.excepthook
    sys.excepthook = lambda t, v, tb: errors.append(f"{t.__name__}: {v}")

    app = Adw.Application(
        application_id="io.piercingxx.TxxT.AddrfixSmoke",
        flags=Gio2.ApplicationFlags.FLAGS_NONE,
    )
    app.register(None)
    window = TxxtWindow(app)
    window.present()
    pump(2.0)
    deadline = time.monotonic() + 15
    titles = []
    while time.monotonic() < deadline:
        pump(1.0)
        titles = [
            window._list_page._model.get_item(i).title
            for i in range(window._list_page._model.get_n_items())
        ]
        if any(t == "Grace Hopper" for t in titles):
            break
    assert any(t == "Grace Hopper" for t in titles), f"Grace missing: {titles!r}"
    cid = None
    for i in range(window._list_page._model.get_n_items()):
        if window._list_page._model.get_item(i).title == "Grace Hopper":
            cid = int(window._list_page._model.get_item(i).conversation_id)
            break
    assert cid, f"no Grace conversation id in {titles!r}"
    window.open_thread(cid)
    pump(2.0)
    deadline = time.monotonic() + 15
    header = ""
    while time.monotonic() < deadline:
        pump(1.0)
        try:
            header = window._thread_pages[-1].header_title
        except Exception:
            header = ""
        if header == "GRACE HOPPER":
            break
    assert header == "GRACE HOPPER", f"thread header missed Grace: {header!r}"
    print("ADDRFIX-SMOKE-OK", flush=True)
finally:
    try:
        svc.terminate()
    except Exception:
        pass
    try:
        svc.wait(timeout=10)
    except Exception:
        try:
            svc.kill()
        except Exception:
            pass
'''


def test_broadway_list_shows_grace_hopper(tmp_path):
    if shutil.which("dbus-run-session") is None:
        pytest.skip("dbus-run-session is not installed")
    if shutil.which("gtk4-broadwayd") is None:
        pytest.skip("gtk4-broadwayd is missing")
    if not (REF_SRC / "xx_contacts" / "__main__.py").exists():
        pytest.skip("ref/xx-contacts is missing")
    from txxt.main import ensure_data_dir
    from txxt.store import Store

    tmp = tempfile.TemporaryDirectory(prefix="txxt-addrfix")
    old_xdg = os.environ.get("XDG_DATA_HOME")
    os.environ["XDG_DATA_HOME"] = tmp.name
    proc = None
    try:
        seed_store = Store(str(ensure_data_dir() / "txxt.db"))
        try:
            seed_store.persist_inbound_sms("+15550100200", "Yes! 12:30?", 1700000000000)
        finally:
            seed_store.close()
        # The helper runs with its own HOME, and without XDG_RUNTIME_DIR GTK
        # looks for the broadway socket under $HOME/.cache: share a private
        # runtime dir between the daemon and the helper.
        run_dir = tmp_path / "run"
        run_dir.mkdir(mode=0o700)
        number = None
        for _ in range(5):
            candidate = _free_display()
            if candidate is None:
                break
            proc = subprocess.Popen(
                ["gtk4-broadwayd", f":{candidate}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env={**os.environ, "XDG_RUNTIME_DIR": str(run_dir)},
            )
            time.sleep(1.0)
            if proc.poll() is None:
                number = candidate
                break
            proc = None
        if number is None:
            pytest.fail("could not start gtk4-broadwayd on any display 5..99")
        helper = tmp_path / "addrfix_broadway_helper.py"
        helper.write_text(_BROADWAY_HELPER)
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        env = {**os.environ, "HOME": str(home),
               "PYTHONPATH": str(TXXT_SRC),
               "GDK_BACKEND": "broadway",
               "BROADWAY_DISPLAY": f":{number}",
               "XDG_RUNTIME_DIR": str(run_dir),
               "XDG_DATA_HOME": tmp.name}
        child = subprocess.run(
            ["dbus-run-session", "--", sys.executable, str(helper),
             str(TXXT_SRC), str(REF_SRC), str(tmp_path / "contacts.db")],
            env=env, capture_output=True, text=True, timeout=180)
        assert "ADDRFIX-SMOKE-OK" in child.stdout, (
            f"helper failed (rc={child.returncode}):\n"
            f"--- stdout ---\n{child.stdout}\n--- stderr ---\n{child.stderr}")
        assert child.returncode == 0
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        if old_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = old_xdg
        tmp.cleanup()
