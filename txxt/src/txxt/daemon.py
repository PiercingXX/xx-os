"""``txxt-daemon``: inbox reconcile, inbound pipeline, retry queue.

The background half of L3 (the GUI lane owns the foreground). Runs
headless under the systemd user unit ``txxt-daemon.service`` or the XDG
autostart entry — a lock file guarantees only one instance either way:

- Startup reconcile: ``List`` the modem store, import what SQLite lacks
  (L1's 5-minute ``find_missing`` match within one direction, never
  duplicates), then ``Delete`` after the row is committed (crash-safe
  via the ``pending_delete`` flag: rows flagged but never deleted match
  again on the next pass and are re-deleted, never re-imported). MMS
  copies are deleted only after the photo is on disk, so an untapped
  ``[Photo]`` placeholder never loses its source.
- Inbound pipeline: ``Messaging.Added`` (wait for RECEIVED first, else
  the text is a multipart prefix) through L1's ``InboundFilter`` —
  delivered rows notify, quarantined rows are held with a sender-only
  notice, blocked rows are stored silently (marked read, no signal).
- Retry queue: failed/pending outbound SMS re-drive with exponential
  backoff; exhausted rows stay held and visibly unsent, never dropped.
- Notifications via ``org.freedesktop.Notifications`` with the
  sender-only default body and a ``Reply`` action that opens the thread
  in the UI (freedesktop actions carry no text entry).

Threading (XXWM_RULES.md): the GTK-free main loop never blocks. All
system-bus I/O runs in a ``ThreadPoolExecutor`` (blocking ``*_sync``
helpers) or as armed async calls; worker results return via
``GLib.idle_add``. Nothing here imports Gtk/Adw.
"""

from __future__ import annotations

import fcntl
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from txxt import backup  # noqa: E402
from txxt import blocking  # noqa: E402
from txxt import mm_client  # noqa: E402
from txxt import notify  # noqa: E402
from txxt import phone_numbers  # noqa: E402
from txxt import store as store_mod  # noqa: E402
from txxt import APP_ID, DAEMON_BUS_NAME  # noqa: E402

log = logging.getLogger("txxt-daemon")

LOCK_NAME = "txxt-daemon.lock"
RETRY_PUMP_MS = 5_000
RECONCILE_META_KEY = "last_reconcile_millis"

RETRY_BASE_DELAY_MS = 5_000
RETRY_MAX_DELAY_MS = 15 * 60 * 1000
RETRY_MAX_ATTEMPTS = 8


# ---- XDG paths ----

def data_dir() -> Path:
    root = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(root) / "txxt"


def db_path() -> Path:
    return data_dir() / "txxt.db"


def media_root() -> Path:
    return data_dir()


def lock_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or "/tmp"
    return Path(runtime) / LOCK_NAME


def cache_dir() -> Path:
    root = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(root) / "txxt"


def mms_dir(message_id: int) -> Path:
    return data_dir() / "mms" / str(int(message_id))


def ensure_private_dir(path: Path) -> Path:
    """Create an XDG dir (mode 0700) before files or SQLite open in it."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def acquire_single_instance(path: Path | None = None) -> object | None:
    """Exclusive non-blocking lock; None when another instance holds it."""
    target = Path(path) if path is not None else lock_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(target), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


# ---- retry queue ----

def retry_delay_ms(attempts: int) -> int:
    """Backoff before the next try after ``attempts`` failures."""
    return min(RETRY_BASE_DELAY_MS * (2**attempts), RETRY_MAX_DELAY_MS)


@dataclass
class QueuedSend:
    """One outbound message awaiting (re)transmission (SMS or MMS)."""

    key: str
    address: str
    body: str
    message_id: int
    attempts: int = 0
    next_due_millis: int = 0
    held: bool = False
    transport: str = "SMS"
    files: tuple = ()


class RetryQueue:
    """Exponential-backoff resend queue (port of ``MmsDownloadRetry``).

    Pure over its clock: ``now_millis`` is injected so tests pin the
    schedule. Exhausted rows are held, never dropped — the thread keeps
    showing them unsent.
    """

    def __init__(self, now_millis=None) -> None:
        self._items: dict[str, QueuedSend] = {}
        self._now = now_millis or (lambda: int(time.time() * 1000))
        self._lock = threading.RLock()

    def add(
        self, address: str, body: str, message_id: int,
        due_now: bool = False,
    ) -> str:
        """Queue one outgoing SMS.

        Fresh sends stay undue for one base delay: the caller transmits
        synchronously first, and an immediate due date would let the
        5-second retry tick resend before that transmit returns. Seeds
        for already-waiting rows pass ``due_now``.
        """
        key = f"{message_id}:{phone_numbers.conversation_key(address) or address.strip()}"
        with self._lock:
            item = self._items.get(key)
            if item is None:
                delay = 0 if due_now else RETRY_BASE_DELAY_MS
                self._items[key] = QueuedSend(
                    key=key, address=address, body=body, message_id=message_id,
                    next_due_millis=self._now() + delay,
                )
            return key

    def add_mms(
        self, address: str, body: str, message_id: int,
        files: tuple | list = (),
        due_now: bool = False,
    ) -> str:
        """Queue one outgoing MMS (staged file paths ride along for retry)."""
        key = f"mms:{message_id}:{phone_numbers.conversation_key(address) or address.strip()}"
        with self._lock:
            item = self._items.get(key)
            if item is None:
                delay = 0 if due_now else RETRY_BASE_DELAY_MS
                self._items[key] = QueuedSend(
                    key=key, address=address, body=body, message_id=message_id,
                    next_due_millis=self._now() + delay, transport="MMS",
                    files=tuple(files or ()),
                )
            return key

    def seed_mms(self, store, rows) -> int:
        """Queue unconfirmed outgoing MMS rows (media_path rides as files).

        ``rows`` are :class:`MessageRow` with ``id``/``body``/
        ``media_path``; the destination resolves from each row's
        conversation via ``seeds_with_addresses``-style lookup on the
        caller side — here each row is re-resolved through ``store`` so
        a second reconcile never resets live backoff.
        """
        count = 0
        for seed in seeds_with_addresses(store, rows):
            try:
                row = store.get_message(seed.id)
                files = (row.media_path,) if row and row.media_path else ()
            except Exception:
                files = ()
            self.add_mms(seed.address, seed.body, seed.id, files, due_now=True)
            count += 1
        return count

    def seed_pending(self, rows) -> int:
        """Queue every unconfirmed outgoing row that is not queued yet.

        ``rows`` carry ``address``/``body``/``id`` (see
        :func:`seeds_with_addresses`); rows already queued keep their
        backoff state so a second reconcile never resets the schedule.
        Seeds are due at once: their first attempt long predates this
        process, unlike a fresh :meth:`add`.
        """
        count = 0
        for row in rows:
            if not row.address:
                continue
            self.add(row.address, row.body, row.id, due_now=True)
            count += 1
        return count

    def due(self) -> list[QueuedSend]:
        with self._lock:
            now = self._now()
            return [
                item for item in self._items.values()
                if not item.held and item.next_due_millis <= now
            ]

    def claim(self, key: str) -> QueuedSend | None:
        """Take a due item for one transmission, pushing its due date out.

        The push (without bumping ``attempts``) closes the window where
        two concurrent pumps both see the item due and both transmit;
        the loser of the race finds nothing due and skips. Success
        removes the item via :meth:`mark_sent`, failure re-arms it via
        :meth:`mark_attempted`.
        """
        with self._lock:
            item = self._items.get(key)
            if item is None or item.held:
                return None
            if item.next_due_millis > self._now():
                return None
            item.next_due_millis = self._now() + retry_delay_ms(item.attempts)
            return item

    def mark_attempted(self, key: str) -> QueuedSend | None:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            item.attempts += 1
            if item.attempts >= RETRY_MAX_ATTEMPTS:
                item.held = True
            else:
                item.next_due_millis = self._now() + retry_delay_ms(item.attempts)
            return item

    def mark_sent(self, key: str) -> None:
        with self._lock:
            self._items.pop(key, None)

    def held_items(self) -> list[QueuedSend]:
        with self._lock:
            return [item for item in self._items.values() if item.held]

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


@dataclass
class RetrySeed:
    """One unconfirmed outgoing row resolved to its destination address."""

    address: str
    body: str
    id: int


def seeds_with_addresses(store, rows) -> list[RetrySeed]:
    """Resolve each pending row's destination from its conversation.

    An outgoing row carries no ``sender_address`` by model contract, so
    the destination comes from the conversation's dialable address (never
    the digits-only match key).
    """
    seeds = []
    for row in rows:
        address = ""
        try:
            conversation = store.get_conversation(row.conversation_id)
        except Exception:
            conversation = None
        if conversation is not None:
            try:
                address = conversation.primary_dialable() or ""
            except Exception:
                address = ""
            if not address.strip():
                for part in conversation.addresses():
                    if part.strip():
                        address = part
                        break
        if address:
            seeds.append(RetrySeed(address=address, body=row.body, id=row.id))
    return seeds


# ---- reconcile ----

@dataclass
class ReconcileCounts:
    imported: int = 0
    deleted: int = 0
    delete_failed: int = 0
    already_known: int = 0
    resent: int = 0
    resend_failed: int = 0
    resend_held: int = 0
    flagged_recovered: int = 0


def known_from_store(store) -> list[backup.KnownMessage]:
    """Every stored message reduced to the duplicate-match triple."""
    known: list[backup.KnownMessage] = []
    for conversation in store.get_all_conversations():
        participants = conversation.participant_addresses
        for item in store.get_messages_for_conversation(conversation.id):
            known.append(backup.KnownMessage(
                participants=participants,
                timestamp_millis=item.timestamp_millis,
                body=item.body,
            ))
    return known


def sms_timestamp_millis(stamp: str) -> int:
    """MM ``Timestamp`` (ISO-8601) to epoch millis; 0 when unparseable.

    A 0 stamp never matches a known row, so the row imports once and is
    then deleted — it cannot loop, because the modem copy is gone.
    """
    text = (stamp or "").strip()
    if not text:
        return 0
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def _known_with_ids(store) -> list[tuple]:
    """Stored messages as ``(id, participants, timestamp, body, direction,
    transport, pending_delete, content_location)``."""
    rows = []
    for conversation in store.get_all_conversations():
        participants = conversation.participant_addresses
        for item in store.get_messages_for_conversation(conversation.id):
            rows.append((
                item.id, participants, item.timestamp_millis, item.body,
                item.direction, item.transport, bool(item.pending_delete),
                item.content_location or "",
            ))
    return rows


def _mms_identity_matches(
    row_location: str, content_location: str | None,
    row_stamp: int, stamp: int,
) -> bool:
    """Whether an mmsd message is the same one a stored MMS row came from.

    mmsd-tng exposes no per-message id besides the object path (which the
    store never records), so identity is the content location when both
    sides carry one, else the exact sender date: two distinct MMS almost
    never share a second, while a re-seen mmsd copy keeps its date.
    Dateless pairs match so one placeholder covers one dateless copy.
    """
    row_location = (row_location or "").strip()
    needle = (content_location or "").strip()
    if row_location or needle:
        return bool(row_location and needle and row_location == needle)
    if stamp and row_stamp:
        return row_stamp == stamp
    return not stamp and not row_stamp


def _match_ids(
    candidates: list[tuple],
    address: str, stamp: int, body: str | None,
    direction: str = "INCOMING", transport: str = "SMS",
    pending_only: bool = False, content_location: str | None = None,
) -> list[int]:
    """Ids of stored rows matching a modem row.

    SMS matches sender + 5-minute window + body within one direction of
    one transport: an outgoing row never stands in for an incoming
    message. The live path passes ``pending_only`` so only a row still
    flagged ``pending_delete`` (this modem copy, imported but not yet
    deleted) counts — a same-worded earlier message is a new message.
    MMS (``body`` None, the store holds ``[Photo]``) matches by sender
    plus :func:`_mms_identity_matches`.
    """
    matched = []
    for entry in candidates:
        (row_id, participants, timestamp, known_body,
         row_direction, row_transport, pending, row_location) = entry
        if row_direction != direction or row_transport != transport:
            continue
        if pending_only and not pending:
            continue
        if not backup.same_sender(participants, address):
            continue
        if transport == "MMS":
            if _mms_identity_matches(row_location, content_location, timestamp, stamp):
                matched.append(row_id)
            continue
        if body is not None and known_body != body:
            continue
        if abs(timestamp - stamp) > backup.MATCH_WINDOW_MS:
            continue
        matched.append(row_id)
    return matched


def reconcile_records(records, store, delete_fn) -> ReconcileCounts:
    """Import missing modem-store rows, delete-after-import.

    ``records`` are :class:`mm_client.SmsRecord`; ``delete_fn(path)``
    deletes one modem object and returns True on success. The
    ``pending_delete`` dance is explicit: flag the fresh row, delete the
    modem copy, clear on success. An already-imported modem copy (crash
    between commit and delete) matches again, is re-deleted instead of
    re-imported, and its stale flag is cleared with it.
    """
    counts = ReconcileCounts()
    counts.flagged_recovered = len(store.pending_delete_rows())
    if not records:
        return counts
    candidates = _known_with_ids(store)
    known_in = [
        backup.KnownMessage(participants=p, timestamp_millis=t, body=b)
        for _, p, t, b, direction, transport, _, _ in candidates
        if direction == "INCOMING" and transport == "SMS"
    ]
    known_out = [
        backup.KnownMessage(participants=p, timestamp_millis=t, body=b)
        for _, p, t, b, direction, transport, _, _ in candidates
        if direction == "OUTGOING" and transport == "SMS"
    ]
    platform = [
        backup.PlatformRow(
            address=record.number,
            date_millis=sms_timestamp_millis(record.timestamp),
            type=backup.SMS_TYPE_SENT if record.outgoing else backup.SMS_TYPE_INBOX,
            body=record.text,
            read=record.outgoing,
        )
        for record in records
    ]
    missing_in = {
        (row.address, row.date_millis, row.body)
        for row in backup.find_missing(
            [row for row in platform if row.type == backup.SMS_TYPE_INBOX],
            known_in,
        )
    }
    missing_out = {
        (row.address, row.date_millis, row.body)
        for row in backup.find_missing(
            [row for row in platform if row.type == backup.SMS_TYPE_SENT],
            known_out,
        )
    }
    for record, row in zip(records, platform):
        direction = "OUTGOING" if record.outgoing else "INCOMING"
        missing = missing_out if record.outgoing else missing_in
        if (row.address, row.date_millis, row.body) not in missing:
            counts.already_known += 1
            if delete_fn(record.path):
                counts.deleted += 1
                for row_id in _match_ids(
                    candidates, row.address, row.date_millis, row.body,
                    direction=direction,
                ):
                    store.clear_pending_delete(row_id)
            else:
                counts.delete_failed += 1
            continue
        if record.outgoing:
            message_id = store.persist_outgoing_sms(
                row.address, row.body, now_millis=row.date_millis or None
            )
            store.mark_sent(message_id)
        else:
            message_id = store.persist_inbound_sms(row.address, row.body, row.date_millis)
        candidates.append((
            message_id, row.address, row.date_millis, row.body,
            direction, "SMS", True, "",
        ))
        counts.imported += 1
        store.mark_pending_delete(message_id)
        if delete_fn(record.path):
            counts.deleted += 1
            store.clear_pending_delete(message_id)
        else:
            counts.delete_failed += 1
    store.set_meta(RECONCILE_META_KEY, str(int(time.time() * 1000)))
    return counts


#: Message ``Status`` values mmsd-tng reports for receivable MMS
#: (``doc/message-api.txt``: received, then read after ``MarkRead``).
_MMS_IMPORTABLE_STATUSES = frozenset({"received", "read"})


def _mms_status_skippable(props: dict) -> bool:
    """Whether an mmsd message must not become an inbound MMS row.

    mmsd-tng lists sent, draft and failed-notification copies alongside
    received ones; only received/read messages are inbound photos. A
    missing status (older fakes) still imports.
    """
    status = props.get("Status", props.get("status", None))
    if status is None or not str(status).strip():
        return False
    return str(status).strip().lower() not in _MMS_IMPORTABLE_STATUSES


def _mms_row_fetched(store, message_id: int) -> bool:
    """Whether an MMS row already has its photo on disk."""
    try:
        row = store.get_message(message_id)
    except Exception:
        return False
    return bool(row is not None and row.media_path)


def reconcile_mms_messages(messages, store, delete_fn) -> ReconcileCounts:
    """mmsd-tng startup pass: import missing MMS metadata rows ([Photo]
    until tapped), delete only after the photo is on disk. ``messages``
    are ``(path, props)`` pairs from :class:`mm_client.MmsAdapter`.

    The mmsd copy is retained until :meth:`Daemon.fetch_mms` (or the
    auto-download prefetch) copies the attachment: deleting the
    placeholder's source first loses every photo the user never taps.
    A re-seen copy matches its placeholder (sender plus
    :func:`_mms_identity_matches`) and is skipped, never re-imported;
    it is deleted only once the matching row carries ``media_path``.
    """
    counts = ReconcileCounts()
    if not messages:
        return counts
    candidates = _known_with_ids(store)
    for path, props in messages:
        if _mms_status_skippable(props):
            continue
        address = mms_sender_of(props)
        if not address:
            continue
        stamp = sms_timestamp_millis(str(props.get("Date") or props.get("date") or ""))
        location = str(
            props.get("ContentLocation") or props.get("content_location") or ""
        ).strip() or None
        matched = _match_ids(
            candidates, address, stamp, None,
            transport="MMS", content_location=location,
        )
        if matched:
            counts.already_known += 1
            if any(_mms_row_fetched(store, row_id) for row_id in matched):
                if delete_fn(path):
                    counts.deleted += 1
                    for row_id in matched:
                        store.clear_pending_delete(row_id)
                else:
                    counts.delete_failed += 1
            continue
        message_id = store.persist_inbound_mms_metadata(
            address, stamp, content_location=location,
        )
        conversation = store.get_conversation(
            store.get_message(message_id).conversation_id
        )
        candidates.append((
            message_id,
            conversation.participant_addresses if conversation else address,
            stamp, "", "INCOMING", "MMS", True, location or "",
        ))
        counts.imported += 1
        store.mark_pending_delete(message_id)
    return counts


def split_mms_attachment(entry) -> tuple[str, str, str, int, int] | None:
    """One mmsd-tng attachment entry as ``(id, type, file, offset, length)``.

    The documented ``Message.Attachments`` struct is ``(ssstt)``: the
    file holds ``length`` bytes at ``offset`` (parts share one stored
    file). Older triples carry ``(id, type, file)`` for the whole file
    (offset 0, length 0). Anything else is not an attachment.
    """
    try:
        parts = tuple(entry)
    except TypeError:
        return None
    if len(parts) == 5:
        name, content_type, path, offset, length = parts
        try:
            offset, length = int(offset), int(length)
        except (TypeError, ValueError):
            return None
    elif len(parts) == 3:
        name, content_type, path = parts
        offset, length = 0, 0
    else:
        return None
    return (str(name), str(content_type), str(path), offset, length)


def mms_content_type(props: dict) -> str | None:
    """Best-effort MIME type for an mmsd-tng message property map."""
    for key in ("ContentType", "content_type", "AttachmentType", "Type"):
        value = props.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    attachments = props.get("Attachments") or props.get("attachments") or []
    for entry in list(attachments):
        split = split_mms_attachment(entry)
        if split is None:
            continue
        _name, content_type, _path, _offset, _length = split
        if content_type.strip():
            return content_type.strip()
    return None


def mms_sender_of(props: dict) -> str:
    """Sender address carried by an mmsd-tng property map.

    ``Sender`` is the sender (``doc/message-api.txt``); ``Recipients``
    are the destinations (usually our own number on inbound), so they
    only fill in when no sender is carried, e.g. sent copies.
    """
    sender = str(props.get("Sender") or props.get("sender") or "").strip()
    if sender:
        return sender
    recipients = props.get("Recipients") or props.get("recipients") or []
    for candidate in list(recipients):
        text = str(candidate).strip()
        if text and text != "insert-address-token":
            return text
    return ""


def stage_mms_attachments(
    text: str, prepared_images: list[bytes], staging: Path,
) -> tuple[list[tuple[str, str, str]], str]:
    """Write text/image files for one mmsd-tng ``SendMessage`` call.

    Returns the ``(filename, content-type, file-path)`` attachment
    triples in send order (text, images) plus the SMIL body, which
    rides as the middle ``SendMessage`` variant — never as a staged
    file, so the MMS carries exactly one SMIL. Raises OSError when
    staging fails.
    """
    from txxt import media as media_mod

    ensure_private_dir(staging)
    staged: list[tuple[str, str, str]] = []
    text_name = ""
    if (text or "").strip():
        text_name = "text_0.txt"
        (staging / text_name).write_bytes((text or "").strip().encode("utf-8"))
        staged.append((text_name, "text/plain", str(staging / text_name)))
    image_names = []
    for index, payload in enumerate(prepared_images):
        name = f"image_{index}.jpg"
        (staging / name).write_bytes(bytes(payload))
        staged.append((name, "image/jpeg", str(staging / name)))
        image_names.append(name)
    smil = media_mod.build_smil(
        image_names[0] if image_names else None,
        text_name or None,
    )
    return staged, smil


# ---- inbound pipeline ----

#: Posted notification ids remembered for HandleNotificationAction.
#: Dicts keep insertion order, so the oldest entry evicts first.
NOTIFICATION_MAP_CAP = 64


@dataclass
class PipelineOutcome:
    disposition: blocking.MessageDisposition
    message_id: int | None = None
    conversation_id: int | None = None
    notified: bool = False
    notification_id: int | None = None


class InboundPipeline:
    """MM ``Added`` rows through L1's ``InboundFilter`` into SQLite."""

    def __init__(
        self, store, filter_provider=None, contacts=None,
        notify_bus=None, settings_loader=None, auto_reply_send=None,
        action_target: str | None = None,
    ) -> None:
        self._store = store
        self._filters = filter_provider or (lambda: blocking.live_current())
        self._contacts = contacts
        self._notify_bus = notify_bus
        self._settings = settings_loader
        self._auto_reply_send = auto_reply_send
        self._action_target = action_target
        self._notification_map: dict[int, int] = {}
        # Posted notification ids stay in memory only: the daemon runs
        # for the whole session (no idle-exit), while server ids die
        # with the shell daemon on reboot, so persisting buys nothing.

    def _display_name(self, number: str) -> str:
        if self._contacts is not None:
            try:
                return self._contacts.display_name(number)
            except Exception:
                pass
        try:
            from txxt import phone_numbers as _phones

            settings = self._settings() if self._settings else None
            return _phones.format_display_configured(number,
                                                     settings) or number
        except Exception:
            return number

    def _posture(self) -> tuple[str, str]:
        """(lock_screen_privacy, alert_style) from settings, defaults kept."""
        if self._settings is None:
            return "sender-only", "sound"
        try:
            settings = self._settings()
        except Exception:
            return "sender-only", "sound"
        privacy = getattr(settings, "lock_screen_privacy", "sender-only")
        style = getattr(settings, "alert_style", "sound")
        return privacy, style

    def handle_sms(
        self, number: str, text: str, date_millis: int | None = None
    ) -> PipelineOutcome:
        """Filter, store, and (unless silent) notify for one inbound SMS."""
        stamp = date_millis if date_millis is not None else int(time.time() * 1000)
        try:
            disposition, _reason = self._filters().evaluate(number, text or "")
        except Exception:
            disposition = blocking.MessageDisposition.DELIVER
        if disposition is blocking.MessageDisposition.BLOCK:
            message_id = self._store.persist_inbound_sms(
                number, text or "", stamp, surface=False
            )
            self._store.mark_message_read(message_id)
            return PipelineOutcome(disposition, message_id, notified=False)
        quarantined = disposition is blocking.MessageDisposition.QUARANTINE
        message_id = self._store.persist_inbound_sms(
            number, text or "", stamp, quarantined=quarantined
        )
        conversation_id = None
        try:
            row = self._store.get_message(message_id)
            conversation_id = row.conversation_id if row else None
        except Exception:
            conversation_id = None
        notified, notification_id = self._notify_inbound(
            number, text or "", quarantined, conversation_id, stamp
        )
        return PipelineOutcome(
            disposition, message_id, conversation_id, notified, notification_id,
        )

    def _notify_inbound(
        self, number: str, text: str, quarantined: bool,
        conversation_id: int | None, stamp_millis: int | None = None,
    ) -> tuple[bool, int | None]:
        if self._notify_bus is None:
            return False, None
        if stamp_millis is not None:
            try:
                if self._store.is_muted(number, stamp_millis):
                    return False, None
            except Exception:
                pass
        privacy, style = self._posture()
        if privacy == "nothing":
            return False, None
        preview = text if privacy == "content" else None
        if quarantined and preview is None:
            preview = ""
        try:
            notification_id = notify.notify_message(
                self._notify_bus, number,
                display_name=self._display_name,
                preview_body=preview,
                alert_style=style,
                action_target=self._action_target,
            )
        except Exception:
            return False, None
        if notification_id is not None and conversation_id is not None:
            self._notification_map[notification_id] = conversation_id
            while len(self._notification_map) > NOTIFICATION_MAP_CAP:
                self._notification_map.pop(
                    next(iter(self._notification_map)))
        return notification_id is not None, notification_id

    def conversation_for_notification(self, notification_id: int) -> int | None:
        return self._notification_map.get(notification_id)

    def _auto_reply_config(self) -> tuple[bool, dict]:
        if self._settings is None:
            return False, {}
        try:
            settings = self._settings()
        except Exception:
            return False, {}
        enabled = bool(getattr(settings, "mms_auto_reply", False))
        overrides = getattr(settings, "mms_auto_reply_overrides", None) or {}
        return enabled, dict(overrides) if isinstance(overrides, dict) else {}

    def handle_mms_attachment(
        self, sender: str, content_type: str | None,
    ) -> tuple[bool, bool]:
        """Audio-drop gate for one inbound MMS part.

        Returns ``(stored, auto_replied)``: audio parts are dropped
        unstored (never downloaded, never kept); when the optional
        auto-reply is enabled the voice-refusal SMS is queued via the
        ``auto_reply_send`` seam. Non-audio parts return ``(True, False)``
        so the caller stores them.
        """
        from txxt import receive_policy as policy

        if policy.decide_attachment(content_type) is not policy.AttachmentDecision.DROP_UNSTORED:
            return True, False
        enabled, overrides = self._auto_reply_config()
        if not policy.should_auto_reply(enabled, sender, overrides):
            return False, False
        sender_fn = self._auto_reply_send
        if sender_fn is None:
            return False, False
        try:
            sender_fn(sender, policy.AUTO_REPLY_BODY)
        except Exception:
            return False, False
        return False, True


# ---- bus-name ownership + UI activation ----

def own_bus_name(
    on_bus_acquired=None, on_acquired=None, on_lost=None,
    bus_type=Gio.BusType.SESSION, name: str = DAEMON_BUS_NAME,
) -> int:
    """Own the daemon bus name while running (ownership convention, §4)."""
    noop = lambda _conn, _n: None  # noqa: E731
    return Gio.bus_own_name(
        bus_type, name, Gio.BusNameOwnerFlags.NONE,
        on_bus_acquired or noop,
        on_acquired or noop,
        on_lost or noop,
    )


# ---- session-bus API for the UI (``io.piercingxx.TxxT1``) ----
#
# The daemon owns ``io.piercingxx.TxxT.Daemon`` (never the GTK app id,
# which the GUI registers itself) so the UI (L4) sends and marks read
# through these methods — never directly via ModemManager, and never
# by writing the SQLite file behind the daemon's back (a second
# process could not wake the retry queue). The UI refreshes off the
# ``MessagesChanged`` signal instead of polling.

TXXT_OBJECT_PATH = "/io/piercingxx/TxxT/Daemon"
TXXT_IFACE = "io.piercingxx.TxxT1"

ERROR_INVALID_ARGUMENT = TXXT_IFACE + ".InvalidArgument"

INTROSPECTION_XML = """\
<node>
  <interface name="io.piercingxx.TxxT1">
    <method name="Send">
      <arg direction="in" type="s" name="address"/>
      <arg direction="in" type="s" name="body"/>
      <arg direction="out" type="x" name="message_id"/>
    </method>
    <method name="SendMms">
      <arg direction="in" type="as" name="recipients"/>
      <arg direction="in" type="s" name="text"/>
      <arg direction="in" type="as" name="file_paths"/>
      <arg direction="out" type="x" name="message_id"/>
    </method>
    <method name="FetchMms">
      <arg direction="in" type="x" name="message_id"/>
      <arg direction="out" type="s" name="media_path"/>
    </method>
    <method name="OpenThread">
      <arg direction="in" type="x" name="conversation_id"/>
    </method>
    <method name="MarkRead">
      <arg direction="in" type="x" name="conversation_id"/>
    </method>
    <method name="HandleNotificationAction">
      <arg direction="in" type="u" name="id"/>
      <arg direction="in" type="s" name="action_key"/>
    </method>
    <signal name="MessagesChanged">
      <arg type="as" name="conversation_ids"/>
    </signal>
  </interface>
</node>
"""


def open_thread(conversation_id: int) -> bool:
    """Open a thread in the UI for a notification Reply/Open action.

    Freedesktop actions carry no text entry, so the action routes to the
    conversation page where the reply is composed. Best-effort: False
    when the UI is not installed (the L4 lane owns the ``--open-thread``
    flag this launches).
    """
    try:
        from gi.repository import GioUnix

        factory = GioUnix.DesktopAppInfo.new
    except Exception:
        factory = Gio.DesktopAppInfo.new
    try:
        info = factory(f"{APP_ID}.desktop")
        if info is None:
            return False
        return bool(info.launch_uris(
            [f"{APP_ID}://thread/{int(conversation_id)}"], None
        ))
    except Exception:
        return False


# ---- daemon runtime ----

class Daemon:
    """Headless runtime: reconcile once, then signals + retry pump."""

    def __init__(
        self, store, pipeline: InboundPipeline | None = None,
        retry: RetryQueue | None = None,
        system_bus_fn=None, session_bus_fn=None,
        sender=None, mms_sender=None, idle_add=None,
        settings_loader=None,
    ) -> None:
        self._store = store
        self._pipeline = pipeline or InboundPipeline(store)
        self._settings_loader = settings_loader
        self._retry = retry if retry is not None else RetryQueue()
        self._system_bus_fn = system_bus_fn or mm_client.system_bus
        self._session_bus_fn = session_bus_fn or mm_client.session_bus
        self._sender = sender or mm_client.create_and_send
        self._mms_sender = mms_sender
        self._idle_add = idle_add or GLib.idle_add
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="txxt")
        self._loop: GLib.MainLoop | None = None
        self._modem_path: str | None = None
        self._mms: mm_client.MmsAdapter | None = None
        self._notif_bus = None
        # Serializes every modem-store import/delete path: D-Bus signals
        # arrive whenever they like, including mid-reconcile, so the
        # "check known -> persist -> delete" sequence below always holds
        # this lock. Duplicates are caught by content match within one
        # direction and transport (SMS: sender + 5-minute window + body;
        # MMS: sender + identity), not by modem path — the modem may
        # reuse a path after delete, but a stored row never matches a
        # message it isn't.
        self._modem_lock = threading.Lock()
        # Only one retry pump transmits at a time: the 5-second tick
        # submits to a 3-worker pool, so a slow modem would otherwise
        # stack overlapping pumps that resend the same queued row.
        self._pump_lock = threading.Lock()
        self._dbus_connection = None

    def _drop_modem_path(self) -> None:
        """Forget the cached modem path so the next operation re-resolves.

        Modems re-index after resets (``/Modem/0`` becomes ``/Modem/1``);
        any failed modem call may mean the cached path is stale.
        """
        self._modem_path = None

    # -- startup (worker thread) --

    def startup_reconcile(self) -> ReconcileCounts:
        """List the modem store, import what's missing, delete-after-import,
        then seed the retry queue from unconfirmed outbound rows."""
        total = ReconcileCounts()
        bus = self._safe(self._system_bus_fn)
        modem = mm_client.resolve_modem_path(bus) if bus is not None else None
        self._modem_path = modem
        if bus is not None and modem is not None:
            records = mm_client.list_records(bus, modem) or []
            with self._modem_lock:
                merged = reconcile_records(
                    records, self._store,
                    lambda path: mm_client.delete_sms(bus, modem, path),
                )
            total.imported += merged.imported
            total.deleted += merged.deleted
            total.delete_failed += merged.delete_failed
            total.already_known += merged.already_known
            total.flagged_recovered += merged.flagged_recovered
        session = self._safe(self._session_bus_fn)
        if session is not None:
            adapter = mm_client.MmsAdapter(session)
            if adapter.available():
                self._mms = adapter
                pairs = []
                for path in adapter.list_messages() or []:
                    props = adapter.get_message(path)
                    if props is not None:
                        pairs.append((path, props))
                with self._modem_lock:
                    merged = reconcile_mms_messages(
                        pairs, self._store, adapter.delete_message,
                    )
                total.imported += merged.imported
                total.deleted += merged.deleted
        if total.imported:
            self._emit_store_changed()
        pending = self._safe(self._store.pending_outgoing_sms) or []
        self._retry.seed_pending(seeds_with_addresses(self._store, pending))
        pending_mms = self._safe(self._store.pending_outgoing_mms) or []
        if pending_mms:
            try:
                self._retry.seed_mms(self._store, pending_mms)
            except Exception:
                pass
        return total

    def pump_retry(self) -> ReconcileCounts:
        """Send every due queued SMS/MMS (worker thread); successes mark
        rows sent and clear modem copies, failures back off, exhausted
        rows hold."""
        if not self._pump_lock.acquire(blocking=False):
            return ReconcileCounts()
        try:
            return self._pump_retry_locked()
        finally:
            self._pump_lock.release()

    def _pump_retry_locked(self) -> ReconcileCounts:
        counts = ReconcileCounts()
        bus = self._safe(self._system_bus_fn)
        modem = self._modem_path or (
            mm_client.resolve_modem_path(bus) if bus is not None else None
        )
        self._modem_path = modem
        if bus is None or modem is None:
            self._drop_modem_path()
        for snapshot in self._retry.due():
            item = self._retry.claim(snapshot.key)
            if item is None:
                continue
            if getattr(item, "transport", "SMS") == "MMS":
                self._pump_one_mms(item, counts)
                continue
            if bus is None or modem is None:
                counts.resend_failed += 1
                self._retry.mark_attempted(item.key)
                continue
            try:
                ok, detail = self._sender(bus, modem, item.address, item.body)
            except Exception:
                ok, detail = False, "sender raised"
            if ok:
                self._safe(self._store.mark_sent, item.message_id)
                try:
                    deleted = mm_client.delete_sms(bus, modem, str(detail))
                except Exception:
                    deleted = False
                if not deleted:
                    self._drop_modem_path()
                self._retry.mark_sent(item.key)
                counts.resent += 1
            else:
                self._drop_modem_path()
                updated = self._retry.mark_attempted(item.key)
                log.warning("resend %s failed: %s", item.message_id, detail)
                if updated is not None and updated.held:
                    counts.resend_held += 1
                else:
                    counts.resend_failed += 1
        return counts

    def _pump_one_mms(self, item, counts) -> None:
        from txxt import media as media_mod

        try:
            row = self._store.get_message(item.message_id)
        except Exception:
            row = None
        staged_paths = list(getattr(item, "files", ()) or ())
        if row is not None and row.media_path and row.media_path not in staged_paths:
            staged_paths.append(row.media_path)
        staged = []
        for path in staged_paths:
            name = Path(path).name or "image_0.jpg"
            kind = "text/plain" if name.endswith(".txt") else "image/jpeg"
            staged.append((name, kind, path))
        if not staged:
            updated = self._retry.mark_attempted(item.key)
            if updated is not None and updated.held:
                counts.resend_held += 1
            else:
                counts.resend_failed += 1
            return
        image_name = next(
            (name for name, kind, _path in staged if kind == "image/jpeg"), None,
        )
        text_name = next(
            (name for name, kind, _path in staged if kind == "text/plain"), None,
        )
        smil = media_mod.build_smil(image_name, text_name)
        ok, detail = self._transmit_mms(item.address, smil, staged)
        if ok:
            self._safe(self._store.mark_sent, item.message_id)
            self._retry.mark_sent(item.key)
            counts.resent += 1
        else:
            updated = self._retry.mark_attempted(item.key)
            log.warning("mms resend %s failed: %s", item.message_id, detail)
            if updated is not None and updated.held:
                counts.resend_held += 1
            else:
                counts.resend_failed += 1

    # -- live signals (main loop arms, workers act) --

    def on_messaging_added(
        self, _conn, _sender, _path, _iface, _sig, params, _user_data
    ) -> None:
        sms_path, received = mm_client.parse_added_params(params)
        if not sms_path or not received:
            return
        self._executor.submit(self._fetch_and_handle, sms_path)

    def _fetch_and_handle(self, sms_path: str) -> None:
        bus = self._safe(self._system_bus_fn)
        if bus is None:
            return
        props = mm_client.wait_received_props(bus, sms_path)
        if props is None:
            return
        modem = self._modem_path or mm_client.resolve_modem_path(bus)
        with self._modem_lock:
            # Re-read under the lock: the startup reconcile may have
            # imported and deleted this copy after the wait above
            # returned. A gone object means already handled — return.
            # Fresh props also stay correct when the modem reuses paths.
            fresh = mm_client.get_sms_properties(bus, sms_path)
            if fresh is None:
                return
            record = mm_client.record_from_props(sms_path, fresh)
            if record is None:
                return
            stamp = sms_timestamp_millis(record.timestamp)
            matched = _match_ids(
                _known_with_ids(self._store),
                record.number, stamp, record.text,
                pending_only=True,
            )
            if matched:
                # The reconcile already archived this modem copy (still
                # flagged): re-delete it instead of storing (and
                # notifying) twice. A same-worded earlier message is not
                # flagged, so it falls through and stores as new.
                if modem is not None and mm_client.delete_sms(bus, modem, sms_path):
                    for row_id in matched:
                        self._store.clear_pending_delete(row_id)
                else:
                    self._drop_modem_path()
                return
            outcome = self._pipeline.handle_sms(
                record.number, record.text, stamp or None,
            )
            if outcome.message_id is not None:
                if modem is not None:
                    if not mm_client.delete_sms_after_import(
                        bus, modem, sms_path, self._store, outcome.message_id
                    ):
                        self._drop_modem_path()
                stored_id = outcome.message_id
            else:
                stored_id = None
        if stored_id is not None:
            self._emit_store_changed(outcome.conversation_id)

    def on_mms_added(
        self, _conn, _sender, _path, _iface, _sig, params, _user_data
    ) -> None:
        try:
            unpacked = params.unpack()
            mms_path = str(unpacked[0])
        except Exception:
            return
        self._executor.submit(self._fetch_and_handle_mms, mms_path)

    def _mms_adapter(self):
        if self._mms is not None:
            return self._mms
        session = self._safe(self._session_bus_fn)
        if session is None:
            return None
        adapter = mm_client.MmsAdapter(session)
        self._mms = adapter
        return adapter

    def _fetch_and_handle_mms(self, mms_path: str) -> None:
        adapter = self._mms_adapter()
        if adapter is None:
            return
        props = adapter.get_message(mms_path) or {}
        sender = mms_sender_of(props)
        stored, _replied = self._pipeline.handle_mms_attachment(
            sender, mms_content_type(props),
        )
        if not stored:
            try:
                adapter.delete_message(mms_path)
            except Exception:
                pass
            log.info("mms added %s audio-dropped", mms_path)
            return
        with self._modem_lock:
            pre_id = self._prefetch_mms_photo(adapter, props, sender)
            merged = reconcile_mms_messages(
                [(mms_path, props)],
                self._store, adapter.delete_message,
            )
        if merged.imported or pre_id is not None:
            self._emit_store_changed()
        log.info("mms added %s imported=%d", mms_path, merged.imported)

    def _prefetch_mms_photo(self, adapter, props: dict, sender: str) -> int | None:
        """Copy this MMS photo now when auto-download is on.

        The row is persisted first so the reconcile below matches it as
        already-known (sender + identity) and only deletes the modem
        copy. Off by default: MMS stays tap-to-fetch.
        """
        _ = adapter
        if not self._auto_download_enabled():
            return None
        stamp = sms_timestamp_millis(str(props.get("Date") or props.get("date") or ""))
        if not (sender or "").strip():
            return None
        try:
            location = str(
                props.get("ContentLocation") or props.get("content_location") or ""
            ).strip() or None
            if _match_ids(
                _known_with_ids(self._store), sender, stamp, None,
                transport="MMS", content_location=location,
            ):
                return None
            message_id = self._store.persist_inbound_mms_metadata(
                sender, stamp,
                content_location=str(
                    props.get("ContentLocation") or props.get("content_location") or ""
                ) or None,
            )
        except Exception:
            return None
        try:
            found = self._copy_mms_attachment(props, message_id)
        except Exception:
            found = None
        if found:
            try:
                self._store.set_media_path(message_id, found)
            except Exception:
                pass
        return message_id

    def on_action_invoked(
        self, _conn, _sender, _path, _iface, _sig, params, _user_data
    ) -> None:
        parsed = notify.parse_action_invoked(params)
        if parsed is None:
            return
        notification_id, action = parsed
        if action not in (notify.ACTION_REPLY, notify.ACTION_OPEN):
            return
        conversation_id = self._pipeline.conversation_for_notification(notification_id)
        if conversation_id is None:
            return
        self._executor.submit(open_thread, conversation_id)

    # -- session-bus API (io.piercingxx.TxxT1 on the daemon name) --

    def register_on(self, connection) -> int:
        """Export ``TxxT1`` on a live Gio.DBusConnection; the connection
        is kept so store changes can emit ``MessagesChanged``."""
        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        registration_id = connection.register_object(
            TXXT_OBJECT_PATH, node.interfaces[0], self.handle_method_call
        )
        self._dbus_connection = connection
        return registration_id

    def handle_method_call(
        self, _connection, _sender, _object_path, _interface_name,
        method_name, parameters, invocation,
    ) -> None:
        """Gio dispatch: unpacking plus executor submit only, so the main
        loop never blocks; the worker replies via ``invocation`` through
        ``idle_add``."""
        try:
            args = parameters.unpack() if parameters is not None else ()
        except Exception as exc:
            invocation.return_dbus_error(
                ERROR_INVALID_ARGUMENT, f"cannot unpack args: {exc}"
            )
            return
        try:
            if method_name == "Send":
                address, body = args
                self._executor.submit(
                    self._do_send_and_reply,
                    invocation, str(address), str(body),
                )
            elif method_name == "SendMms":
                recipients, text, file_paths = args
                self._executor.submit(
                    self._do_send_mms_and_reply,
                    invocation, list(recipients), str(text),
                    [str(p) for p in list(file_paths)],
                )
            elif method_name == "FetchMms":
                (message_id,) = args
                self._executor.submit(
                    self._do_fetch_mms_and_reply,
                    invocation, int(message_id),
                )
            elif method_name in ("OpenThread", "MarkRead"):
                (conversation_id,) = args
                worker = (
                    self._do_open_and_reply
                    if method_name == "OpenThread"
                    else self._do_mark_read_and_reply
                )
                self._executor.submit(worker, invocation, int(conversation_id))
            elif method_name == "HandleNotificationAction":
                notification_id, action_key = args
                self._executor.submit(
                    self._do_notification_action_and_reply,
                    invocation, int(notification_id), str(action_key),
                )
            else:
                invocation.return_dbus_error(
                    ERROR_INVALID_ARGUMENT, f"unknown method: {method_name}"
                )
        except (TypeError, ValueError) as exc:
            invocation.return_dbus_error(
                ERROR_INVALID_ARGUMENT, f"bad arguments: {exc}"
            )

    def _do_send_and_reply(self, invocation, address: str, body: str) -> None:
        try:
            message_id = self.send_message(address, body)
        except ValueError as exc:
            self._idle_add(
                invocation.return_dbus_error, ERROR_INVALID_ARGUMENT, str(exc)
            )
            return
        except Exception as exc:
            self._idle_add(
                invocation.return_dbus_error, ERROR_INVALID_ARGUMENT,
                f"send failed: {exc}",
            )
            return
        self._idle_add(
            invocation.return_value, GLib.Variant("(x)", (int(message_id),))
        )

    def _do_send_mms_and_reply(
        self, invocation, recipients: list, text: str, file_paths: list,
    ) -> None:
        try:
            message_id = self.send_mms(recipients, text, file_paths)
        except ValueError as exc:
            self._idle_add(
                invocation.return_dbus_error, ERROR_INVALID_ARGUMENT, str(exc)
            )
            return
        except Exception as exc:
            self._idle_add(
                invocation.return_dbus_error, ERROR_INVALID_ARGUMENT,
                f"send failed: {exc}",
            )
            return
        self._idle_add(
            invocation.return_value, GLib.Variant("(x)", (int(message_id),))
        )

    def _do_fetch_mms_and_reply(self, invocation, message_id: int) -> None:
        try:
            media_path = self.fetch_mms(message_id)
        except ValueError as exc:
            self._idle_add(
                invocation.return_dbus_error, ERROR_INVALID_ARGUMENT, str(exc)
            )
            return
        except Exception as exc:
            self._idle_add(
                invocation.return_dbus_error, ERROR_INVALID_ARGUMENT,
                f"fetch failed: {exc}",
            )
            return
        self._idle_add(
            invocation.return_value, GLib.Variant("(s)", (str(media_path or ""),))
        )

    def _do_open_and_reply(self, invocation, conversation_id: int) -> None:
        try:
            open_thread(conversation_id)
        except Exception as exc:
            log.warning("open thread %s failed: %s", conversation_id, exc)
        self._idle_add(invocation.return_value, GLib.Variant("()", ()))

    def handle_notification_action(self, notification_id: int, action_key: str) -> bool:
        """Shell action-target entry: open the thread for a posted card.

        Only notification ids this daemon posted (tracked in the
        pipeline's notification map) act — anything else, and any
        action key the daemon never posted, is a silent no-op so a
        replayed or foreign id can never open an unrelated thread.
        """
        if action_key not in (notify.ACTION_REPLY, notify.ACTION_OPEN):
            return False
        try:
            conversation_id = self._pipeline.conversation_for_notification(
                int(notification_id)
            )
        except (TypeError, ValueError):
            return False
        if conversation_id is None:
            return False
        try:
            open_thread(conversation_id)
        except Exception as exc:
            log.warning("notification action %s failed: %s", notification_id, exc)
            return False
        return True

    def _do_notification_action_and_reply(
        self, invocation, notification_id: int, action_key: str,
    ) -> None:
        try:
            self.handle_notification_action(notification_id, action_key)
        except Exception as exc:
            log.warning("notification action %s failed: %s", notification_id, exc)
        self._idle_add(invocation.return_value, GLib.Variant("()", ()))

    def _auto_download_enabled(self) -> bool:
        if self._settings_loader is None:
            return False
        try:
            return bool(getattr(self._settings_loader(), "mms_auto_download", False))
        except Exception:
            return False

    def _do_mark_read_and_reply(self, invocation, conversation_id: int) -> None:
        try:
            self._store.mark_conversation_read(conversation_id)
        except Exception as exc:
            self._idle_add(
                invocation.return_dbus_error, ERROR_INVALID_ARGUMENT,
                f"mark read failed: {exc}",
            )
            return
        self._emit_store_changed(conversation_id)
        self._idle_add(invocation.return_value, GLib.Variant("()", ()))

    def send_message(self, address: str, body: str) -> int:
        """Persist an outgoing SMS and transmit it (worker thread).

        The row enters the retry queue first; a transmit success marks
        it sent and clears the modem copy, a failure leaves it queued
        with backoff. Returns the message id either way — failures are
        retried, never dropped. Raises ValueError on empty address/body.
        """
        target = (address or "").strip()
        text = (body or "").strip()
        if not target or not text:
            raise ValueError("address and body must not be empty")
        message_id = self._store.persist_outgoing_sms(target, text)
        try:
            row = self._store.get_message(message_id)
            conversation_id = row.conversation_id if row else None
        except Exception:
            conversation_id = None
        key = self._retry.add(target, text, message_id)
        bus = self._safe(self._system_bus_fn)
        modem = self._modem_path or (
            mm_client.resolve_modem_path(bus) if bus is not None else None
        )
        self._modem_path = modem
        if bus is None or modem is None:
            self._drop_modem_path()
            self._retry.mark_attempted(key)
        else:
            try:
                ok, detail = self._sender(bus, modem, target, text)
            except Exception as exc:
                ok, detail = False, str(exc)
            if ok:
                self._safe(self._store.mark_sent, message_id)
                try:
                    deleted = mm_client.delete_sms(bus, modem, str(detail))
                except Exception:
                    deleted = False
                if not deleted:
                    self._drop_modem_path()
                self._retry.mark_sent(key)
            else:
                self._drop_modem_path()
                self._retry.mark_attempted(key)
                log.warning("send %s failed: %s", message_id, detail)
        self._emit_store_changed(conversation_id)
        return message_id

    def send_mms(
        self, recipients, text: str = "", file_paths=None,
    ) -> int:
        """Persist an outgoing MMS and transmit it via mmsd-tng.

        Every ``file_paths`` entry is scrubbed and fit (fail closed:
        an unscrubbable photo raises ValueError and nothing sends), the
        scrubbed bytes are staged with the text for the real
        ``SendMessage(as, v, a(sss))`` shape (SMIL as the middle
        variant), and one MMS row per recipient enters the retry queue.
        Returns the first message id; failures queue with backoff, never
        drop. Raises ValueError on empty recipients or on (empty text
        and no photos).
        """
        from txxt import media as media_mod

        dialables = [
            phone_numbers.canonical_dialable_configured(str(r))
            for r in (recipients or [])
            if str(r).strip()
        ]
        if not dialables:
            raise ValueError("at least one recipient is required")
        caption = (text or "").strip()
        sources = [str(p) for p in (file_paths or []) if str(p).strip()]
        if not caption and not sources:
            raise ValueError("text or at least one photo is required")
        budget = media_mod.max_image_bytes()
        prepared: list[bytes] = []
        for source in sources:
            try:
                with open(source, "rb") as handle:
                    raw = handle.read()
            except OSError as exc:
                raise ValueError(f"cannot read photo: {source}: {exc}")
            fitted = media_mod.prepare_for_send(raw, budget)
            if fitted is None:
                raise ValueError(f"photo refused (unscrubbable): {source}")
            prepared.append(fitted)
        first_id: int | None = None
        first_conversation: int | None = None
        staged_all: list[str] = []
        for target in dialables:
            from txxt.conversations_core import COLLAPSED_PHOTO_PLACEHOLDER

            placeholder = caption or COLLAPSED_PHOTO_PLACEHOLDER
            message_id = self._store.persist_outgoing_mms(
                target, placeholder, media_path=None,
            )
            if first_id is None:
                first_id = message_id
            try:
                row = self._store.get_message(message_id)
                conversation_id = row.conversation_id if row else None
            except Exception:
                conversation_id = None
            if first_conversation is None:
                first_conversation = conversation_id
            staging = ensure_private_dir(cache_dir() / f"mms-{message_id}")
            try:
                staged, smil = stage_mms_attachments(caption, prepared, staging)
            except OSError as exc:
                raise ValueError(f"cannot stage photo: {exc}")
            staged_all = [triple[2] for triple in staged]
            image_paths = [path for name, kind, path in staged if kind == "image/jpeg"]
            try:
                self._store.set_media_path(
                    message_id, (image_paths or staged_all[:1] or [None])[0],
                )
            except Exception:
                pass
            key = self._retry.add_mms(target, placeholder, message_id, staged_all)
            ok, detail = self._transmit_mms(target, smil, staged)
            if ok:
                self._safe(self._store.mark_sent, message_id)
                self._retry.mark_sent(key)
            else:
                self._retry.mark_attempted(key)
                log.warning("mms send %s failed: %s", message_id, detail)
        assert first_id is not None
        self._emit_store_changed(first_conversation)
        return first_id

    def _transmit_mms(self, target: str, smil: str, staged) -> tuple[bool, str]:
        """One mmsd-tng ``SendMessage`` attempt; (True, path)/(False, detail)."""
        sender = self._mms_sender
        if sender is not None:
            try:
                return sender([target], smil, staged)
            except Exception as exc:
                return False, str(exc)
        adapter = self._mms_adapter()
        if adapter is None:
            return False, "mmsd-tng unavailable"
        try:
            return adapter.send_message([target], smil, staged)
        except Exception as exc:
            return False, str(exc)

    def fetch_mms(self, message_id: int) -> str:
        """On-disk photo for one MMS row, fetching it on first tap.

        Returns the existing ``media_path`` when the file is already on
        disk; otherwise searches the mmsd-tng store for the row's content
        location/sender, copies the first image attachment under
        ``$XDG_DATA_HOME/txxt/mms/<id>/``, records it, and returns it.
        Returns "" when nothing is fetchable (still ``[Photo]``).
        """
        try:
            row = self._store.get_message(int(message_id))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"bad message id: {exc}")
        if row is None:
            raise ValueError("unknown message")
        if row.media_path:
            try:
                if Path(row.media_path).is_file():
                    return row.media_path
            except Exception:
                pass
        adapter = self._mms_adapter()
        if adapter is None:
            return row.media_path or ""
        needle_location = (row.content_location or "").strip()
        needle_sender = (row.sender_address or "").strip()
        needle_stamp = row.timestamp_millis or 0
        try:
            paths = adapter.list_messages() or []
        except Exception:
            return row.media_path or ""
        matches = []
        for mms_path in paths:
            try:
                props = adapter.get_message(mms_path) or {}
            except Exception:
                continue
            location = str(
                props.get("ContentLocation") or props.get("content_location") or ""
            ).strip()
            sender = mms_sender_of(props)
            if needle_location and location != needle_location:
                continue
            if not needle_location and needle_sender and sender != needle_sender:
                try:
                    if not phone_numbers.matches(sender, needle_sender):
                        continue
                except Exception:
                    continue
            prop_stamp = sms_timestamp_millis(
                str(props.get("Date") or props.get("date") or "")
            )
            matches.append((mms_path, props, prop_stamp))
        ordered = sorted(
            matches,
            key=lambda entry: (
                0 if (needle_stamp and entry[2] and entry[2] == needle_stamp) else 1
            ),
        )
        for mms_path, props, _prop_stamp in ordered:
            found = self._copy_mms_attachment(props, int(message_id))
            if found:
                try:
                    self._store.set_media_path(int(message_id), found)
                except Exception:
                    pass
                try:
                    if adapter.delete_message(mms_path):
                        self._store.clear_pending_delete(int(message_id))
                except Exception:
                    pass
                self._emit_store_changed(
                    row.conversation_id if row else None,
                )
                return found
        return row.media_path or ""

    def _copy_mms_attachment(self, props: dict, message_id: int) -> str | None:
        """Copy the first image attachment in ``props`` to the MMS dir.

        Documented entries are ``(id, type, file, offset, length)``: the
        bytes live at ``offset`` for ``length`` bytes inside the shared
        stored file, so only that slice is copied — never the whole PDU
        blob. Older triples name the whole file and copy it in full.
        """
        candidates: list[tuple[str, int, int]] = []
        attachments = props.get("Attachments") or props.get("attachments") or []
        for entry in list(attachments):
            split = split_mms_attachment(entry)
            if split is None:
                continue
            _name, content_type, path, offset, length = split
            if str(content_type).lower().startswith("audio/"):
                continue
            if str(path).strip():
                candidates.append((str(path).strip(), offset, length))
        for key in ("Attachment", "Path", "Data", "File", "Image"):
            value = props.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append((value.strip(), 0, 0))
        dest_dir = ensure_private_dir(mms_dir(message_id))
        for index, (source, offset, length) in enumerate(candidates):
            try:
                if not Path(source).is_file():
                    continue
                with open(source, "rb") as handle:
                    if length > 0:
                        handle.seek(offset)
                        chunk = handle.read(length)
                    else:
                        chunk = handle.read()
            except (OSError, ValueError):
                continue
            if not chunk:
                continue
            dest = dest_dir / f"photo-{index}.jpg"
            try:
                dest.write_bytes(chunk)
                return str(dest)
            except OSError:
                continue
        return None

    def _emit_store_changed(self, conversation_id: int | None = None) -> None:
        """Emit ``MessagesChanged`` for one thread, or for every thread
        when the affected set is bulk (a reconcile pass)."""
        if conversation_id is None:
            try:
                ids = [str(row.id) for row in self._store.get_all_conversations()]
            except Exception:
                return
        else:
            ids = [str(conversation_id)]
        self._emit_messages_changed(ids)

    def _emit_messages_changed(self, conversation_ids) -> None:
        connection = self._dbus_connection
        if connection is None:
            return
        try:
            connection.emit_signal(
                None, TXXT_OBJECT_PATH, TXXT_IFACE, "MessagesChanged",
                GLib.Variant("(as)", ([str(cid) for cid in conversation_ids],)),
            )
        except Exception as exc:
            log.warning("MessagesChanged emit failed: %s", exc)

    def _on_bus_acquired(self, connection, _name) -> None:
        try:
            self.register_on(connection)
        except Exception as exc:
            log.warning("txxt bus object failed: %s", exc)

    # -- main loop --

    def run(self) -> int:
        lock_fd = acquire_single_instance()
        if lock_fd is None:
            log.warning("another txxt-daemon holds the lock; exiting")
            return 0
        _ = lock_fd
        ensure_private_dir(data_dir())
        ensure_private_dir(cache_dir())
        own_bus_name(self._on_bus_acquired)
        system = self._safe(self._system_bus_fn)
        if system is not None:
            try:
                system.signal_subscribe(
                    mm_client.MM_BUS_NAME, mm_client.MESSAGING_IFACE, "Added", None,
                    None, Gio.DBusSignalFlags.NONE, self.on_messaging_added, None,
                )
            except Exception as err:
                log.warning("messaging subscribe failed: %s", err)
        session = self._safe(self._session_bus_fn)
        if session is not None:
            try:
                mms = mm_client.MmsAdapter(session)
                mms.subscribe_added(self.on_mms_added)
                notify.subscribe_action_invoked(session, self.on_action_invoked)
            except Exception as err:
                log.warning("session subscribe failed: %s", err)
        self._executor.submit(self._startup_and_log)
        GLib.timeout_add(RETRY_PUMP_MS, self._on_retry_tick)
        self._loop = GLib.MainLoop()
        try:
            self._loop.run()
        except KeyboardInterrupt:
            pass
        return 0

    def _startup_and_log(self) -> None:
        try:
            counts = self.startup_reconcile()
        except Exception as err:
            log.warning("startup reconcile failed: %s", err)
            return
        log.info(
            "reconcile imported=%d deleted=%d known=%d recovered=%d",
            counts.imported, counts.deleted,
            counts.already_known, counts.flagged_recovered,
        )

    def _on_retry_tick(self) -> bool:
        self._executor.submit(self._pump_and_log)
        return True

    def _pump_and_log(self) -> None:
        try:
            counts = self.pump_retry()
        except Exception as err:
            log.warning("retry pump failed: %s", err)
            return
        if counts.resent or counts.resend_failed or counts.resend_held:
            log.info(
                "retry resent=%d failed=%d held=%d",
                counts.resent, counts.resend_failed, counts.resend_held,
            )

    @staticmethod
    def _safe(fn, *args):
        try:
            return fn(*args)
        except Exception:
            return None


def main(argv: list[str] | None = None) -> int:
    """``txxt --daemon`` entry: single instance, reconcile, serve."""
    _ = argv
    logging.basicConfig(level=logging.INFO)
    store = store_mod.Store(str(db_path()))
    from txxt import settings as settings_mod  # noqa: E402
    from txxt.contacts import GioContactsBackend, session_resolver  # noqa: E402
    from txxt.known_senders import KnownSenderChecker, install_live_lookup  # noqa: E402

    session = None
    try:
        session = mm_client.session_bus()
    except Exception:
        session = None
    resolver = session_resolver(session)
    checker = KnownSenderChecker(resolver=resolver, store=store)
    try:
        blocking.live_apply_from_store(store)
    except Exception as exc:
        log.warning("rule-table apply failed: %s", exc)
    try:
        install_live_lookup(checker)
    except Exception as exc:
        log.warning("known-sender lookup install failed: %s", exc)
    if session is not None:
        try:
            GioContactsBackend(session).subscribe_changed(
                checker.handle_contacts_changed
            )
        except Exception as exc:
            log.warning("contacts Changed subscribe failed: %s", exc)
    daemon_holder: dict = {}

    def _auto_reply_send(address: str, body: str) -> None:
        target = daemon_holder.get("daemon")
        if target is None:
            return
        try:
            target.send_message(address, body)
        except Exception as exc:
            log.warning("mms auto-reply to %s failed: %s", address, exc)

    def _live_filter():
        """Rule tables re-read per message: GUI edits apply immediately."""
        try:
            blocking.live_apply_from_store(
                store, known_sender_lookup=checker.is_known,
            )
        except Exception as exc:
            log.warning("rule-table apply failed: %s", exc)
        return blocking.live_current()

    pipeline = InboundPipeline(
        store,
        filter_provider=_live_filter,
        contacts=resolver,
        notify_bus=session,
        settings_loader=settings_mod.load,
        auto_reply_send=_auto_reply_send,
        action_target=notify.action_target(
            DAEMON_BUS_NAME, TXXT_OBJECT_PATH, TXXT_IFACE,
        ),
    )
    daemon = Daemon(store, pipeline=pipeline, settings_loader=settings_mod.load)
    daemon_holder["daemon"] = daemon
    return daemon.run()
