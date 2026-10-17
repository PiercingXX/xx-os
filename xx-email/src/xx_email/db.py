"""sqlite3 store for XX Email (stdlib only, no GTK).

Ports Room XxEmailDb v3 (ref/xx-email data/db/): the same 8 tables and
column semantics, historyId as opaque TEXT, file-backed outbox from day
one (no legacy base64 column on fresh installs), and the DAO query set
including exact-token instr() label matching and inbox-category
queries. FTS4 becomes FTS5 external-content over the same 4 columns
(subject/snippet/from/to - never bodies).

Schema version lives in PRAGMA user_version. Fresh installs start at
v3; the v1->v2->v3 migration code exists so a future Android-export
import of a v1/v2-era dump still applies.

Concurrency: one connection per thread (or a single worker-thread
owner), WAL mode, parameterized queries only.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from xx_email.domain import FtsEscaper

SCHEMA_VERSION = 3
DB_FILE_NAME = "xxemail.db"

_V1_TABLES = (
    "CREATE TABLE IF NOT EXISTS accounts"
    " (email TEXT NOT NULL, displayName TEXT NOT NULL,"
    " historyId INTEGER, lastSyncAt INTEGER, PRIMARY KEY(email))",
    "CREATE TABLE IF NOT EXISTS labels"
    " (accountEmail TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL,"
    " type TEXT, colorBg TEXT, colorText TEXT,"
    " PRIMARY KEY(accountEmail, id))",
    "CREATE TABLE IF NOT EXISTS threads"
    " (accountEmail TEXT NOT NULL, id TEXT NOT NULL, snippet TEXT NOT NULL,"
    " subject TEXT NOT NULL, fromAddress TEXT NOT NULL, fromName TEXT NOT NULL,"
    " date INTEGER NOT NULL, messageCount INTEGER NOT NULL,"
    " unreadCount INTEGER NOT NULL, hasAttachments INTEGER NOT NULL,"
    " starred INTEGER NOT NULL, inInbox INTEGER NOT NULL,"
    " categories TEXT NOT NULL, labelsCsv TEXT NOT NULL, snoozedUntil INTEGER,"
    " PRIMARY KEY(accountEmail, id))",
    "CREATE TABLE IF NOT EXISTS messages"
    " (accountEmail TEXT NOT NULL, id TEXT NOT NULL, threadId TEXT NOT NULL,"
    " subject TEXT NOT NULL, fromAddress TEXT NOT NULL, toCsv TEXT NOT NULL,"
    " ccCsv TEXT NOT NULL, date INTEGER NOT NULL, snippet TEXT NOT NULL,"
    " read INTEGER NOT NULL, starred INTEGER NOT NULL,"
    " hasAttachments INTEGER NOT NULL, labelsCsv TEXT NOT NULL,"
    " messageIdHeader TEXT, bodyHtml TEXT, bodyPlain TEXT,"
    " bodyFetched INTEGER NOT NULL, PRIMARY KEY(accountEmail, id))",
    "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING FTS4"
    "(subject, snippet, fromAddress, toCsv, content=messages)",
    "CREATE TABLE IF NOT EXISTS outbox"
    " (id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,"
    " accountEmail TEXT NOT NULL, kind TEXT NOT NULL, threadId TEXT,"
    " rfc822Base64 TEXT, subject TEXT NOT NULL, targetAt INTEGER NOT NULL,"
    " state TEXT NOT NULL, attempts INTEGER NOT NULL, error TEXT,"
    " createdAt INTEGER NOT NULL)",
)

_V3_TABLES = (
    "CREATE TABLE IF NOT EXISTS accounts"
    " (email TEXT NOT NULL, displayName TEXT NOT NULL,"
    " historyId TEXT, lastSyncAt INTEGER, PRIMARY KEY(email))",
    "CREATE TABLE IF NOT EXISTS labels"
    " (accountEmail TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL,"
    " type TEXT, colorBg TEXT, colorText TEXT,"
    " PRIMARY KEY(accountEmail, id))",
    "CREATE TABLE IF NOT EXISTS threads"
    " (accountEmail TEXT NOT NULL, id TEXT NOT NULL, snippet TEXT NOT NULL,"
    " subject TEXT NOT NULL, fromAddress TEXT NOT NULL, fromName TEXT NOT NULL,"
    " date INTEGER NOT NULL, messageCount INTEGER NOT NULL,"
    " unreadCount INTEGER NOT NULL, hasAttachments INTEGER NOT NULL,"
    " starred INTEGER NOT NULL, inInbox INTEGER NOT NULL,"
    " categories TEXT NOT NULL, labelsCsv TEXT NOT NULL, snoozedUntil INTEGER,"
    " PRIMARY KEY(accountEmail, id))",
    "CREATE TABLE IF NOT EXISTS messages"
    " (accountEmail TEXT NOT NULL, id TEXT NOT NULL, threadId TEXT NOT NULL,"
    " subject TEXT NOT NULL, fromAddress TEXT NOT NULL, toCsv TEXT NOT NULL,"
    " ccCsv TEXT NOT NULL, date INTEGER NOT NULL, snippet TEXT NOT NULL,"
    " read INTEGER NOT NULL, starred INTEGER NOT NULL,"
    " hasAttachments INTEGER NOT NULL, labelsCsv TEXT NOT NULL,"
    " messageIdHeader TEXT, bodyHtml TEXT, bodyPlain TEXT,"
    " bodyFetched INTEGER NOT NULL, attachmentsJson TEXT,"
    " PRIMARY KEY(accountEmail, id))",
    "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING FTS5"
    "(subject, snippet, fromAddress, toCsv,"
    " content=messages, content_rowid=rowid)",
    "CREATE TABLE IF NOT EXISTS outbox"
    " (id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,"
    " accountEmail TEXT NOT NULL, kind TEXT NOT NULL, threadId TEXT,"
    " rfc822Base64 TEXT, path TEXT, size INTEGER NOT NULL DEFAULT 0,"
    " subject TEXT NOT NULL, targetAt INTEGER NOT NULL,"
    " state TEXT NOT NULL, attempts INTEGER NOT NULL, error TEXT,"
    " createdAt INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS snooze_wakes"
    " (accountEmail TEXT NOT NULL, threadId TEXT NOT NULL,"
    " targetAt INTEGER NOT NULL, createdAt INTEGER NOT NULL,"
    " PRIMARY KEY(accountEmail, threadId))",
    "CREATE TABLE IF NOT EXISTS folder_pages"
    " (accountEmail TEXT NOT NULL, folderKey TEXT NOT NULL,"
    " nextPageToken TEXT, PRIMARY KEY(accountEmail, folderKey))",
)

_FTS4_SYNC_TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS messages_fts_before_update BEFORE UPDATE ON messages BEGIN"
    " DELETE FROM messages_fts WHERE docid = old.rowid; END",
    "CREATE TRIGGER IF NOT EXISTS messages_fts_before_delete BEFORE DELETE ON messages BEGIN"
    " DELETE FROM messages_fts WHERE docid = old.rowid; END",
    "CREATE TRIGGER IF NOT EXISTS messages_fts_after_update AFTER UPDATE ON messages BEGIN"
    " INSERT INTO messages_fts(docid, subject, snippet, fromAddress, toCsv)"
    " SELECT rowid, subject, snippet, fromAddress, toCsv FROM messages"
    " WHERE rowid = new.rowid; END",
    "CREATE TRIGGER IF NOT EXISTS messages_fts_after_insert AFTER INSERT ON messages BEGIN"
    " INSERT INTO messages_fts(docid, subject, snippet, fromAddress, toCsv)"
    " SELECT rowid, subject, snippet, fromAddress, toCsv FROM messages"
    " WHERE rowid = new.rowid; END",
)

_FTS5_SYNC_TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS messages_fts_after_insert AFTER INSERT ON messages BEGIN"
    " INSERT INTO messages_fts(rowid, subject, snippet, fromAddress, toCsv)"
    " VALUES (new.rowid, new.subject, new.snippet, new.fromAddress, new.toCsv); END",
    "CREATE TRIGGER IF NOT EXISTS messages_fts_after_delete AFTER DELETE ON messages BEGIN"
    " INSERT INTO messages_fts(messages_fts, rowid, subject, snippet, fromAddress, toCsv)"
    " VALUES ('delete', old.rowid, old.subject, old.snippet, old.fromAddress, old.toCsv); END",
    "CREATE TRIGGER IF NOT EXISTS messages_fts_after_update AFTER UPDATE ON messages BEGIN"
    " INSERT INTO messages_fts(messages_fts, rowid, subject, snippet, fromAddress, toCsv)"
    " VALUES ('delete', old.rowid, old.subject, old.snippet, old.fromAddress, old.toCsv);"
    " INSERT INTO messages_fts(rowid, subject, snippet, fromAddress, toCsv)"
    " VALUES (new.rowid, new.subject, new.snippet, new.fromAddress, new.toCsv); END",
)


def escape_fts_prefix_query(raw: str) -> str:
    """Turn free-text input into a safe FTS MATCH prefix query.

    Single source is domain.FtsEscaper (the Fts.kt port); FTS5 accepts
    the same ``"token"*`` shape. Kept here so existing queries and
    tests keep working.
    """
    return FtsEscaper.escape_prefix_query(raw)


@dataclass
class Account:
    email: str
    display_name: str = ""
    history_id: str | None = None
    last_sync_at: int | None = None


@dataclass
class Label:
    account_email: str
    id: str
    name: str
    type: str | None = None
    color_bg: str | None = None
    color_text: str | None = None


@dataclass
class Thread:
    account_email: str
    id: str
    snippet: str = ""
    subject: str = ""
    from_address: str = ""
    from_name: str = ""
    date: int = 0
    message_count: int = 0
    unread_count: int = 0
    has_attachments: bool = False
    starred: bool = False
    in_inbox: bool = True
    categories: str = ""
    labels_csv: str = ""
    snoozed_until: int | None = None


@dataclass
class Message:
    account_email: str
    id: str
    thread_id: str
    subject: str = ""
    from_address: str = ""
    to_csv: str = ""
    cc_csv: str = ""
    date: int = 0
    snippet: str = ""
    read: bool = False
    starred: bool = False
    has_attachments: bool = False
    labels_csv: str = ""
    message_id_header: str | None = None
    body_html: str | None = None
    body_plain: str | None = None
    body_fetched: bool = False
    attachments_json: str | None = None


@dataclass
class OutboxEntry:
    id: int = 0
    account_email: str = ""
    kind: str = "SEND"
    thread_id: str | None = None
    rfc822_base64: str | None = None
    path: str | None = None
    size: int = 0
    subject: str = ""
    target_at: int = 0
    state: str = "QUEUED"
    attempts: int = 0
    error: str | None = None
    created_at: int = 0


@dataclass
class SnoozeWake:
    account_email: str
    thread_id: str
    target_at: int
    created_at: int = 0


@dataclass
class FolderPage:
    account_email: str
    folder_key: str
    next_page_token: str | None = None


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a store connection. Use one connection per thread."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _set_user_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(f"PRAGMA user_version={version:d}")


def create_v1_schema(conn: sqlite3.Connection) -> None:
    """Build the Android v1 baseline (historyId INTEGER, FTS4, legacy outbox).

    Used by migration tests and by a future Android-export import of a
    v1-era dump; never used for fresh installs.
    """
    with conn:
        for ddl in _V1_TABLES:
            conn.execute(ddl)
        for trigger in _FTS4_SYNC_TRIGGERS:
            conn.execute(trigger)
        _set_user_version(conn, 1)


def _migrate_1_to_2(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS accounts_new"
        " (email TEXT NOT NULL, displayName TEXT NOT NULL,"
        " historyId TEXT, lastSyncAt INTEGER, PRIMARY KEY(email))",
    )
    conn.execute(
        "INSERT INTO accounts_new (email, displayName, historyId, lastSyncAt)"
        " SELECT email, displayName, CAST(historyId AS TEXT), lastSyncAt"
        " FROM accounts",
    )
    conn.execute("DROP TABLE accounts")
    conn.execute("ALTER TABLE accounts_new RENAME TO accounts")
    conn.execute("ALTER TABLE outbox ADD COLUMN path TEXT")
    conn.execute("ALTER TABLE outbox ADD COLUMN size INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS snooze_wakes"
        " (accountEmail TEXT NOT NULL, threadId TEXT NOT NULL,"
        " targetAt INTEGER NOT NULL, createdAt INTEGER NOT NULL,"
        " PRIMARY KEY(accountEmail, threadId))",
    )


def _migrate_2_to_3(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE messages ADD COLUMN attachmentsJson TEXT")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS folder_pages"
        " (accountEmail TEXT NOT NULL, folderKey TEXT NOT NULL,"
        " nextPageToken TEXT, PRIMARY KEY(accountEmail, folderKey))",
    )


def migrate(conn: sqlite3.Connection) -> int:
    """Bring any older store to v3. Never destructive. Returns new version."""
    version = user_version(conn)
    if version >= SCHEMA_VERSION:
        return version
    with conn:
        if version < 1:
            for ddl in _V3_TABLES:
                conn.execute(ddl)
            for trigger in _FTS5_SYNC_TRIGGERS:
                conn.execute(trigger)
        else:
            if version < 2:
                _migrate_1_to_2(conn)
            if version < 3:
                _migrate_2_to_3(conn)
        _set_user_version(conn, SCHEMA_VERSION)
    return SCHEMA_VERSION


def open_db(path: str | Path) -> sqlite3.Connection:
    """Connect and migrate to the current schema version."""
    conn = connect(path)
    migrate(conn)
    return conn


def _bool(value: object) -> bool:
    return bool(value)


def upsert_account(conn: sqlite3.Connection, account: Account) -> None:
    with conn:
        conn.execute(
            "INSERT INTO accounts (email, displayName, historyId, lastSyncAt)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(email) DO UPDATE SET displayName=excluded.displayName,"
            " historyId=excluded.historyId, lastSyncAt=excluded.lastSyncAt",
            (account.email, account.display_name, account.history_id,
             account.last_sync_at),
        )


def get_account(conn: sqlite3.Connection, email: str) -> Account | None:
    row = conn.execute("SELECT * FROM accounts WHERE email = ?",
                       (email,)).fetchone()
    if row is None:
        return None
    return Account(email=row["email"], display_name=row["displayName"],
                   history_id=row["historyId"],
                   last_sync_at=row["lastSyncAt"])


def all_accounts(conn: sqlite3.Connection) -> list[Account]:
    return [Account(email=r["email"], display_name=r["displayName"],
                    history_id=r["historyId"], last_sync_at=r["lastSyncAt"])
            for r in conn.execute("SELECT * FROM accounts ORDER BY email")]


def update_sync_point(conn: sqlite3.Connection, email: str,
                      history_id: str | None, synced_at: int) -> None:
    with conn:
        conn.execute(
            "UPDATE accounts SET historyId = ?, lastSyncAt = ? WHERE email = ?",
            (history_id, synced_at, email),
        )


def delete_account(conn: sqlite3.Connection, email: str) -> None:
    with conn:
        for table in ("accounts", "labels", "threads", "messages",
                      "outbox", "snooze_wakes", "folder_pages"):
            if table == "accounts":
                conn.execute("DELETE FROM accounts WHERE email = ?", (email,))
            else:
                conn.execute(f"DELETE FROM {table} WHERE accountEmail = ?",
                             (email,))


def upsert_labels(conn: sqlite3.Connection, labels: list[Label]) -> None:
    with conn:
        conn.executemany(
            "INSERT INTO labels (accountEmail, id, name, type, colorBg, colorText)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(accountEmail, id) DO UPDATE SET name=excluded.name,"
            " type=excluded.type, colorBg=excluded.colorBg,"
            " colorText=excluded.colorText",
            [(label.account_email, label.id, label.name, label.type,
              label.color_bg, label.color_text) for label in labels],
        )


def labels_for_account(conn: sqlite3.Connection, account: str) -> list[Label]:
    return [Label(account_email=r["accountEmail"], id=r["id"], name=r["name"],
                  type=r["type"], color_bg=r["colorBg"],
                  color_text=r["colorText"])
            for r in conn.execute(
                "SELECT * FROM labels WHERE accountEmail = ? ORDER BY name",
                (account,))]


def delete_labels_for_account(conn: sqlite3.Connection, account: str) -> None:
    with conn:
        conn.execute("DELETE FROM labels WHERE accountEmail = ?", (account,))


def _thread_from_row(row: sqlite3.Row) -> Thread:
    return Thread(
        account_email=row["accountEmail"], id=row["id"],
        snippet=row["snippet"], subject=row["subject"],
        from_address=row["fromAddress"], from_name=row["fromName"],
        date=row["date"], message_count=row["messageCount"],
        unread_count=row["unreadCount"],
        has_attachments=_bool(row["hasAttachments"]),
        starred=_bool(row["starred"]), in_inbox=_bool(row["inInbox"]),
        categories=row["categories"], labels_csv=row["labelsCsv"],
        snoozed_until=row["snoozedUntil"],
    )


def upsert_threads(conn: sqlite3.Connection, threads: list[Thread]) -> None:
    with conn:
        conn.executemany(
            "INSERT INTO threads (accountEmail, id, snippet, subject, fromAddress,"
            " fromName, date, messageCount, unreadCount, hasAttachments, starred,"
            " inInbox, categories, labelsCsv, snoozedUntil)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(accountEmail, id) DO UPDATE SET snippet=excluded.snippet,"
            " subject=excluded.subject, fromAddress=excluded.fromAddress,"
            " fromName=excluded.fromName, date=excluded.date,"
            " messageCount=excluded.messageCount, unreadCount=excluded.unreadCount,"
            " hasAttachments=excluded.hasAttachments, starred=excluded.starred,"
            " inInbox=excluded.inInbox, categories=excluded.categories,"
            " labelsCsv=excluded.labelsCsv, snoozedUntil=excluded.snoozedUntil",
            [(t.account_email, t.id, t.snippet, t.subject, t.from_address,
              t.from_name, t.date, t.message_count, t.unread_count,
              int(t.has_attachments), int(t.starred), int(t.in_inbox),
              t.categories, t.labels_csv, t.snoozed_until) for t in threads],
        )


def get_thread(conn: sqlite3.Connection, account: str,
               thread_id: str) -> Thread | None:
    row = conn.execute(
        "SELECT * FROM threads WHERE accountEmail = ? AND id = ?",
        (account, thread_id)).fetchone()
    return _thread_from_row(row) if row is not None else None


def inbox_category(conn: sqlite3.Connection, account: str, category: str,
                   include_empty: bool) -> list[Thread]:
    return [_thread_from_row(r) for r in conn.execute(
        "SELECT * FROM threads WHERE accountEmail = ? AND inInbox = 1"
        " AND snoozedUntil IS NULL"
        " AND ((? = 1 AND categories = '')"
        " OR (? <> '' AND instr(',' || categories || ',', ',' || ? || ',') > 0))"
        " ORDER BY date DESC LIMIT 200",
        (account, int(include_empty), category, category))]


def count_inbox_category(conn: sqlite3.Connection, account: str, category: str,
                         include_empty: bool) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) FROM threads WHERE accountEmail = ? AND inInbox = 1"
        " AND snoozedUntil IS NULL"
        " AND ((? = 1 AND categories = '')"
        " OR (? <> '' AND instr(',' || categories || ',', ',' || ? || ',') > 0))",
        (account, int(include_empty), category, category)).fetchone()[0])


def starred_threads(conn: sqlite3.Connection, account: str) -> list[Thread]:
    return [_thread_from_row(r) for r in conn.execute(
        "SELECT * FROM threads WHERE accountEmail = ? AND starred = 1"
        " AND instr(',' || labelsCsv || ',', ',TRASH,') = 0"
        " ORDER BY date DESC", (account,))]


def snoozed_threads(conn: sqlite3.Connection, account: str) -> list[Thread]:
    return [_thread_from_row(r) for r in conn.execute(
        "SELECT * FROM threads WHERE accountEmail = ?"
        " AND snoozedUntil IS NOT NULL ORDER BY snoozedUntil ASC",
        (account,))]


def threads_with_label(conn: sqlite3.Connection, account: str,
                       label_id: str, limit: int = 200) -> list[Thread]:
    return [_thread_from_row(r) for r in conn.execute(
        "SELECT * FROM threads WHERE accountEmail = ?"
        " AND instr(',' || labelsCsv || ',', ',' || ? || ',') > 0"
        " ORDER BY date DESC LIMIT ?",
        (account, label_id, limit))]


def count_with_label(conn: sqlite3.Connection, account: str,
                     label_id: str) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) FROM threads WHERE accountEmail = ?"
        " AND instr(',' || labelsCsv || ',', ',' || ? || ',') > 0",
        (account, label_id)).fetchone()[0])


def all_mail(conn: sqlite3.Connection, account: str,
             limit: int = 500) -> list[Thread]:
    return [_thread_from_row(r) for r in conn.execute(
        "SELECT * FROM threads WHERE accountEmail = ?"
        " ORDER BY date DESC LIMIT ?", (account, limit))]


def set_snoozed(conn: sqlite3.Connection, account: str, thread_id: str,
                wake_at: int | None) -> None:
    with conn:
        conn.execute(
            "UPDATE threads SET snoozedUntil = ?"
            " WHERE accountEmail = ? AND id = ?",
            (wake_at, account, thread_id),
        )


def set_inbox_and_labels(conn: sqlite3.Connection, account: str,
                         thread_id: str, in_inbox: bool,
                         labels_csv: str) -> None:
    with conn:
        conn.execute(
            "UPDATE threads SET inInbox = ?, labelsCsv = ?"
            " WHERE accountEmail = ? AND id = ?",
            (int(in_inbox), labels_csv, account, thread_id),
        )


def delete_threads_for_account(conn: sqlite3.Connection, account: str) -> None:
    with conn:
        conn.execute("DELETE FROM threads WHERE accountEmail = ?", (account,))


def inbox_ids(conn: sqlite3.Connection, account: str) -> list[str]:
    """Ids of non-snoozed inbox threads (pre-sync snapshot for notify)."""
    return [row[0] for row in conn.execute(
        "SELECT id FROM threads WHERE accountEmail = ? AND inInbox = 1"
        " AND snoozedUntil IS NULL ORDER BY date DESC", (account,))]


def unread_inbox(conn: sqlite3.Connection, account: str) -> list[Thread]:
    """Non-snoozed inbox threads with unread mail, newest first."""
    return [_thread_from_row(row) for row in conn.execute(
        "SELECT * FROM threads WHERE accountEmail = ? AND inInbox = 1"
        " AND snoozedUntil IS NULL AND unreadCount > 0"
        " ORDER BY date DESC", (account,))]


def delete_threads_by_ids(conn: sqlite3.Connection, account: str,
                           thread_ids: list[str]) -> None:
    with conn:
        conn.executemany(
            "DELETE FROM threads WHERE accountEmail = ? AND id = ?",
            [(account, thread_id) for thread_id in thread_ids])


def _message_from_row(row: sqlite3.Row) -> Message:
    return Message(
        account_email=row["accountEmail"], id=row["id"],
        thread_id=row["threadId"], subject=row["subject"],
        from_address=row["fromAddress"], to_csv=row["toCsv"],
        cc_csv=row["ccCsv"], date=row["date"], snippet=row["snippet"],
        read=_bool(row["read"]), starred=_bool(row["starred"]),
        has_attachments=_bool(row["hasAttachments"]),
        labels_csv=row["labelsCsv"], message_id_header=row["messageIdHeader"],
        body_html=row["bodyHtml"], body_plain=row["bodyPlain"],
        body_fetched=_bool(row["bodyFetched"]),
        attachments_json=row["attachmentsJson"]
        if "attachmentsJson" in row.keys() else None,
    )


def upsert_messages(conn: sqlite3.Connection,
                    messages: list[Message]) -> None:
    with conn:
        conn.executemany(
            "INSERT INTO messages (accountEmail, id, threadId, subject, fromAddress,"
            " toCsv, ccCsv, date, snippet, read, starred, hasAttachments,"
            " labelsCsv, messageIdHeader, bodyHtml, bodyPlain, bodyFetched,"
            " attachmentsJson)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(accountEmail, id) DO UPDATE SET threadId=excluded.threadId,"
            " subject=excluded.subject, fromAddress=excluded.fromAddress,"
            " toCsv=excluded.toCsv, ccCsv=excluded.ccCsv, date=excluded.date,"
            " snippet=excluded.snippet, read=excluded.read,"
            " starred=excluded.starred, hasAttachments=excluded.hasAttachments,"
            " labelsCsv=excluded.labelsCsv,"
            " messageIdHeader=excluded.messageIdHeader, bodyHtml=excluded.bodyHtml,"
            " bodyPlain=excluded.bodyPlain, bodyFetched=excluded.bodyFetched,"
            " attachmentsJson=excluded.attachmentsJson",
            [(m.account_email, m.id, m.thread_id, m.subject, m.from_address,
              m.to_csv, m.cc_csv, m.date, m.snippet, int(m.read),
              int(m.starred), int(m.has_attachments), m.labels_csv,
              m.message_id_header, m.body_html, m.body_plain,
              int(m.body_fetched), m.attachments_json)
             for m in messages],
        )


def upsert_message_metadata(conn: sqlite3.Connection,
                            messages: list[Message]) -> None:
    """Upsert sync metadata without touching downloaded bodies.

    Hydration only carries metadata format: including body columns
    here would overwrite cached bodies with NULL on every sync and
    break offline reading. Bodies are written solely through
    update_message_bodies after an explicit full fetch.
    """
    with conn:
        conn.executemany(
            "INSERT INTO messages (accountEmail, id, threadId, subject, fromAddress,"
            " toCsv, ccCsv, date, snippet, read, starred, hasAttachments,"
            " labelsCsv, messageIdHeader, bodyFetched)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)"
            " ON CONFLICT(accountEmail, id) DO UPDATE SET threadId=excluded.threadId,"
            " subject=excluded.subject, fromAddress=excluded.fromAddress,"
            " toCsv=excluded.toCsv, ccCsv=excluded.ccCsv, date=excluded.date,"
            " snippet=excluded.snippet, read=excluded.read,"
            " starred=excluded.starred, hasAttachments=excluded.hasAttachments,"
            " labelsCsv=excluded.labelsCsv,"
            " messageIdHeader=excluded.messageIdHeader",
            [(m.account_email, m.id, m.thread_id, m.subject, m.from_address,
              m.to_csv, m.cc_csv, m.date, m.snippet, int(m.read),
              int(m.starred), int(m.has_attachments), m.labels_csv,
              m.message_id_header) for m in messages],
        )


def messages_for_thread(conn: sqlite3.Connection, account: str,
                        thread_id: str) -> list[Message]:
    return [_message_from_row(r) for r in conn.execute(
        "SELECT * FROM messages WHERE accountEmail = ? AND threadId = ?"
        " ORDER BY date ASC", (account, thread_id))]


def get_message(conn: sqlite3.Connection, account: str,
                message_id: str) -> Message | None:
    row = conn.execute(
        "SELECT * FROM messages WHERE accountEmail = ? AND id = ?",
        (account, message_id)).fetchone()
    return _message_from_row(row) if row is not None else None


def update_message_bodies(conn: sqlite3.Connection, account: str,
                          message_id: str, html: str | None,
                          plain: str | None,
                          attachments_json: str | None) -> None:
    with conn:
        conn.execute(
            "UPDATE messages SET bodyHtml = ?, bodyPlain = ?, bodyFetched = 1,"
            " attachmentsJson = ? WHERE accountEmail = ? AND id = ?",
            (html, plain, attachments_json, account, message_id),
        )


def delete_messages_for_account(conn: sqlite3.Connection,
                                account: str) -> None:
    with conn:
        conn.execute("DELETE FROM messages WHERE accountEmail = ?", (account,))


def delete_message(conn: sqlite3.Connection, account: str,
                   message_id: str) -> None:
    with conn:
        conn.execute(
            "DELETE FROM messages WHERE accountEmail = ? AND id = ?",
            (account, message_id))


def delete_messages_for_thread(conn: sqlite3.Connection, account: str,
                               thread_id: str) -> None:
    with conn:
        conn.execute(
            "DELETE FROM messages WHERE accountEmail = ? AND threadId = ?",
            (account, thread_id))


def count_for_thread(conn: sqlite3.Connection, account: str,
                     thread_id: str) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) FROM messages WHERE accountEmail = ?"
        " AND threadId = ?", (account, thread_id)).fetchone()[0])


def search_local(conn: sqlite3.Connection, account: str,
                 raw_query: str) -> list[Message]:
    """Offline search over subject/snippet/from/to (never bodies)."""
    query = escape_fts_prefix_query(raw_query)
    if not query:
        return []
    return [_message_from_row(r) for r in conn.execute(
        "SELECT messages.* FROM messages"
        " JOIN messages_fts ON messages_fts.rowid = messages.rowid"
        " WHERE messages_fts MATCH ? AND messages.accountEmail = ?"
        " ORDER BY messages.date DESC LIMIT 200",
        (query, account))]


def _outbox_from_row(row: sqlite3.Row) -> OutboxEntry:
    keys = row.keys()
    return OutboxEntry(
        id=row["id"], account_email=row["accountEmail"], kind=row["kind"],
        thread_id=row["threadId"],
        rfc822_base64=row["rfc822Base64"] if "rfc822Base64" in keys else None,
        path=row["path"] if "path" in keys else None,
        size=row["size"] if "size" in keys else 0,
        subject=row["subject"], target_at=row["targetAt"],
        state=row["state"], attempts=row["attempts"], error=row["error"],
        created_at=row["createdAt"],
    )


def insert_outbox(conn: sqlite3.Connection, entry: OutboxEntry) -> int:
    with conn:
        cursor = conn.execute(
            "INSERT INTO outbox (accountEmail, kind, threadId, rfc822Base64,"
            " path, size, subject, targetAt, state, attempts, error, createdAt)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entry.account_email, entry.kind, entry.thread_id,
             entry.rfc822_base64, entry.path, entry.size, entry.subject,
             entry.target_at, entry.state, entry.attempts, entry.error,
             entry.created_at),
        )
        return int(cursor.lastrowid)


def get_outbox(conn: sqlite3.Connection, entry_id: int) -> OutboxEntry | None:
    row = conn.execute("SELECT * FROM outbox WHERE id = ?",
                       (entry_id,)).fetchone()
    return _outbox_from_row(row) if row is not None else None


def queued_outbox(conn: sqlite3.Connection,
                    account: str | None = None) -> list[OutboxEntry]:
    if account is None:
        return [_outbox_from_row(r) for r in conn.execute(
            "SELECT * FROM outbox WHERE state = 'QUEUED'"
            " ORDER BY targetAt ASC")]
    return [_outbox_from_row(r) for r in conn.execute(
        "SELECT * FROM outbox WHERE state = 'QUEUED'"
        " AND accountEmail = ? ORDER BY targetAt ASC", (account,))]


def sending_outbox(conn: sqlite3.Connection,
                   account: str | None = None) -> list[OutboxEntry]:
    """Rows stuck SENDING: a dead worker left them, recover to FAILED."""
    if account is None:
        return [_outbox_from_row(r) for r in conn.execute(
            "SELECT * FROM outbox WHERE state = 'SENDING'"
            " ORDER BY createdAt ASC")]
    return [_outbox_from_row(r) for r in conn.execute(
        "SELECT * FROM outbox WHERE state = 'SENDING'"
        " AND accountEmail = ? ORDER BY createdAt ASC", (account,))]


def open_outbox_for_account(conn: sqlite3.Connection,
                            account: str) -> list[OutboxEntry]:
    return [_outbox_from_row(r) for r in conn.execute(
        "SELECT * FROM outbox WHERE accountEmail = ?"
        " AND state IN ('QUEUED', 'FAILED', 'SENDING')"
        " ORDER BY CASE state WHEN 'FAILED' THEN 0 WHEN 'SENDING' THEN 1"
        " ELSE 2 END, createdAt DESC", (account,))]


def failed_outbox_for_account(conn: sqlite3.Connection,
                              account: str) -> list[OutboxEntry]:
    return [_outbox_from_row(r) for r in conn.execute(
        "SELECT * FROM outbox WHERE accountEmail = ? AND state = 'FAILED'"
        " ORDER BY createdAt ASC", (account,))]


def set_outbox_state(conn: sqlite3.Connection, entry_id: int, state: str,
                     error: str | None = None) -> None:
    with conn:
        conn.execute("UPDATE outbox SET state = ?, error = ? WHERE id = ?",
                     (state, error, entry_id))


def increment_outbox_attempts(conn: sqlite3.Connection, entry_id: int) -> None:
    with conn:
        conn.execute("UPDATE outbox SET attempts = attempts + 1 WHERE id = ?",
                     (entry_id,))


def set_outbox_payload(conn: sqlite3.Connection, entry_id: int,
                       path: str | None, size: int) -> None:
    with conn:
        conn.execute("UPDATE outbox SET path = ?, size = ? WHERE id = ?",
                     (path, size, entry_id))


def clear_legacy_payload(conn: sqlite3.Connection, entry_id: int) -> None:
    with conn:
        conn.execute("UPDATE outbox SET rfc822Base64 = NULL WHERE id = ?",
                     (entry_id,))


def queued_outbox_ids(conn: sqlite3.Connection, account: str) -> list[int]:
    return [int(r[0]) for r in conn.execute(
        "SELECT id FROM outbox WHERE accountEmail = ? AND state = 'QUEUED'",
        (account,))]


def cancel_outbox_if_queued(conn: sqlite3.Connection, entry_id: int) -> bool:
    """Flip QUEUED->CANCELLED only while still queued (undo side of the CAS)."""
    with conn:
        cursor = conn.execute(
            "UPDATE outbox SET state = 'CANCELLED'"
            " WHERE id = ? AND state = 'QUEUED'", (entry_id,))
        return cursor.rowcount > 0


def claim_outbox_if_queued(conn: sqlite3.Connection, entry_id: int) -> bool:
    """Flip QUEUED->SENDING only while still queued (worker side of the CAS)."""
    with conn:
        cursor = conn.execute(
            "UPDATE outbox SET state = 'SENDING'"
            " WHERE id = ? AND state = 'QUEUED'", (entry_id,))
        return cursor.rowcount > 0


def outbox_for_account(conn: sqlite3.Connection,
                       account: str) -> list[OutboxEntry]:
    return [_outbox_from_row(r) for r in conn.execute(
        "SELECT * FROM outbox WHERE accountEmail = ?", (account,))]


def delete_outbox(conn: sqlite3.Connection, entry_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM outbox WHERE id = ?", (entry_id,))


def delete_outbox_for_account(conn: sqlite3.Connection, account: str) -> None:
    with conn:
        conn.execute("DELETE FROM outbox WHERE accountEmail = ?", (account,))


def upsert_folder_page(conn: sqlite3.Connection, page: FolderPage) -> None:
    with conn:
        conn.execute(
            "INSERT INTO folder_pages (accountEmail, folderKey, nextPageToken)"
            " VALUES (?, ?, ?)"
            " ON CONFLICT(accountEmail, folderKey) DO UPDATE SET"
            " nextPageToken=excluded.nextPageToken",
            (page.account_email, page.folder_key, page.next_page_token),
        )


def delete_folder_page(conn: sqlite3.Connection, account: str,
                       key: str) -> None:
    with conn:
        conn.execute(
            "DELETE FROM folder_pages WHERE accountEmail = ? AND folderKey = ?",
            (account, key))


def get_folder_page(conn: sqlite3.Connection, account: str,
                    key: str) -> FolderPage | None:
    row = conn.execute(
        "SELECT * FROM folder_pages WHERE accountEmail = ? AND folderKey = ?",
        (account, key)).fetchone()
    if row is None:
        return None
    return FolderPage(account_email=row["accountEmail"],
                      folder_key=row["folderKey"],
                      next_page_token=row["nextPageToken"])


def delete_folder_pages_for_account(conn: sqlite3.Connection,
                                    account: str) -> None:
    with conn:
        conn.execute("DELETE FROM folder_pages WHERE accountEmail = ?",
                     (account,))


def all_folder_pages(conn: sqlite3.Connection) -> list[FolderPage]:
    rows = conn.execute(
        "SELECT * FROM folder_pages ORDER BY accountEmail, folderKey")
    return [FolderPage(account_email=r["accountEmail"],
                       folder_key=r["folderKey"],
                       next_page_token=r["nextPageToken"]) for r in rows]


def upsert_snooze_wake(conn: sqlite3.Connection, wake: SnoozeWake) -> None:
    with conn:
        conn.execute(
            "INSERT INTO snooze_wakes (accountEmail, threadId, targetAt, createdAt)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(accountEmail, threadId) DO UPDATE SET"
            " targetAt=excluded.targetAt, createdAt=excluded.createdAt",
            (wake.account_email, wake.thread_id, wake.target_at,
             wake.created_at),
        )


def get_snooze_wake(conn: sqlite3.Connection, account: str,
                    thread_id: str) -> SnoozeWake | None:
    row = conn.execute(
        "SELECT * FROM snooze_wakes WHERE accountEmail = ? AND threadId = ?",
        (account, thread_id)).fetchone()
    if row is None:
        return None
    return SnoozeWake(account_email=row["accountEmail"],
                      thread_id=row["threadId"], target_at=row["targetAt"],
                      created_at=row["createdAt"])


def due_snooze_wakes(conn: sqlite3.Connection, now: int) -> list[SnoozeWake]:
    return [SnoozeWake(account_email=r["accountEmail"],
                       thread_id=r["threadId"], target_at=r["targetAt"],
                       created_at=r["createdAt"])
            for r in conn.execute(
                "SELECT * FROM snooze_wakes WHERE targetAt <= ?"
                " ORDER BY targetAt ASC", (now,))]


def snooze_wakes_for_account(conn: sqlite3.Connection,
                             account: str) -> list[SnoozeWake]:
    return [SnoozeWake(account_email=r["accountEmail"],
                       thread_id=r["threadId"], target_at=r["targetAt"],
                       created_at=r["createdAt"])
            for r in conn.execute(
                "SELECT * FROM snooze_wakes WHERE accountEmail = ?"
                " ORDER BY targetAt ASC", (account,))]


def delete_snooze_wake(conn: sqlite3.Connection, account: str,
                       thread_id: str) -> None:
    with conn:
        conn.execute(
            "DELETE FROM snooze_wakes WHERE accountEmail = ? AND threadId = ?",
            (account, thread_id),
        )


def delete_snooze_wakes_for_account(conn: sqlite3.Connection,
                                    account: str) -> None:
    with conn:
        conn.execute("DELETE FROM snooze_wakes WHERE accountEmail = ?",
                     (account,))


def all_snooze_wakes(conn: sqlite3.Connection) -> list[SnoozeWake]:
    return [SnoozeWake(account_email=r["accountEmail"],
                       thread_id=r["threadId"], target_at=r["targetAt"],
                       created_at=r["createdAt"])
            for r in conn.execute(
                "SELECT * FROM snooze_wakes"
                " ORDER BY accountEmail, threadId")]


#: Posted summary cards remembered for HandleNotificationAction. One
#: row per server id; the poller exits while cards stay live, so the
#: activatable actions service reads them back from here. Bounded:
#: the oldest rows evict past the cap.
POSTED_NOTIF_CAP = 64

_POSTED_NOTIF_DDL = (
    "CREATE TABLE IF NOT EXISTS notif_actions"
    " (serverId INTEGER PRIMARY KEY, accountEmail TEXT NOT NULL,"
    " threadId TEXT NOT NULL, postedAt INTEGER NOT NULL)"
)


def record_posted_notif(conn: sqlite3.Connection, server_id: int,
                        account: str, thread_id: str,
                        posted_at: int) -> None:
    """Remember one posted card; oldest rows evict past the cap."""
    try:
        notif_id = int(server_id)
    except (TypeError, ValueError):
        return
    if notif_id <= 0:
        return
    try:
        stamp = int(posted_at)
    except (TypeError, ValueError):
        stamp = 0
    with conn:
        conn.execute(_POSTED_NOTIF_DDL)
        conn.execute(
            "INSERT INTO notif_actions"
            " (serverId, accountEmail, threadId, postedAt)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(serverId) DO UPDATE SET accountEmail=?,"
            " threadId=?, postedAt=?",
            (notif_id, account or "", thread_id or "", stamp,
             account or "", thread_id or "", stamp),
        )
        conn.execute(
            "DELETE FROM notif_actions WHERE serverId NOT IN"
            " (SELECT serverId FROM notif_actions"
            " ORDER BY postedAt DESC, serverId DESC LIMIT ?)",
            (POSTED_NOTIF_CAP,),
        )


def posted_notif_for(conn: sqlite3.Connection,
                     server_id: int) -> tuple[str, str] | None:
    """(account, thread) posted under `server_id`, else None."""
    try:
        notif_id = int(server_id)
    except (TypeError, ValueError):
        return None
    conn.execute(_POSTED_NOTIF_DDL)
    row = conn.execute(
        "SELECT accountEmail, threadId FROM notif_actions"
        " WHERE serverId = ?",
        (notif_id,)).fetchone()
    if row is None:
        return None
    return (row["accountEmail"], row["threadId"])
