"""Pure conversation-list and thread presenters (headless).

Ports the pure slices of the Android thread UI:

- ``ui/ConversationListPresenter.kt`` -> :func:`row_title`,
  :func:`present_conversations` (title/snippet/timestamp/unread)
- ``ui/ConversationSearchFilter.kt`` -> :func:`filter_rows`
- ``ui/ThreadSearchFilter.kt`` -> :func:`filter_message_rows`
- ``ui/ThreadMessagePresenter.kt`` -> :func:`message_state`
  (alignment lives in the GTK layer; the outcome text lives here)
- ``ui/MessageLinks.kt`` -> :func:`find_links` (offsets only, never
  fetched — the browser's business, and only after the operator taps)
- ``ui/SendGate.kt`` lives in :mod:`txxt.compose`; the ``[Photo]``
  collapsed marker is :data:`txxt.conversations_core.COLLAPSED_PHOTO_PLACEHOLDER`

No GTK/D-Bus imports: every function here runs under headless pytest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from txxt import conversations_core as core

#: Title for a conversation with no remote participant (Android parity).
UNTITLED = "(no recipient)"

#: An outgoing row still unsent after this long counts as held by the
#: retry queue (it matches the daemon's maximum backoff step), so the
#: thread offers a Retry action. Younger unsent rows are still queued.
HELD_AFTER_MILLIS = 15 * 60 * 1000

THREAD_URI_SCHEME = "io.piercingxx.TxxT"


def count_label(count: int, word: str, plural: str | None = None) -> str:
    """``1 number`` / ``2 numbers`` / ``0 numbers``: one place for counts."""
    return f"{count} {word if count == 1 else (plural or word + 's')}"


@dataclass
class ConversationRowView:
    conversation_id: int
    title: str
    snippet: str
    timestamp_millis: int | None
    unread_count: int
    muted: bool = False
    pinned: bool = False
    starred: bool = False
    archived: bool = False
    quarantined: bool = False
    address: str = ""
    marks: str = ""


@dataclass(frozen=True)
class Link:
    start: int
    end: int
    url: str


def row_title(addresses, display_name=lambda s: s) -> str:
    """One-line title for ``addresses``; never blank.

    Each address resolves through ``display_name`` first (the contacts
    resolver); a resolver returning blank falls back to the raw
    address. Shared by the list and the thread header so the two can
    never disagree about what a conversation is called.
    """
    parts = []
    for address in addresses or []:
        try:
            resolved = display_name(address)
        except Exception:
            resolved = address
        text = (resolved or "").strip() or (address or "").strip()
        if text:
            parts.append(text)
    return ", ".join(parts) if parts else UNTITLED


def snippet_of(body: str | None, muted: bool = False, transport: str = "SMS") -> str:
    """One-line list snippet: first line, trimmed, muted-prefixed."""
    line = (body or "").splitlines()
    text = line[0].strip() if line else ""
    if not text and (transport or "").upper() == "MMS":
        text = core.COLLAPSED_PHOTO_PLACEHOLDER
    if muted:
        return f"muted · {text}" if text else "muted"
    return text


def format_time(timestamp_millis: int | None, now_millis: int) -> str:
    """Short timestamp for a list row: time today, weekday this week."""
    if timestamp_millis is None:
        return ""
    day = datetime.fromtimestamp(timestamp_millis / 1000).astimezone()
    now = datetime.fromtimestamp(now_millis / 1000).astimezone()
    if day.date() == now.date():
        return day.strftime("%H:%M")
    if (now.date() - day.date()).days == 1:
        return "Yesterday"
    if 1 < (now.date() - day.date()).days < 7:
        return day.strftime("%A")
    return day.strftime("%Y-%m-%d")


def _first_address(participant_addresses: str) -> str:
    for part in (participant_addresses or "").split("\u0001"):
        if part.strip():
            return part.strip()
    return ""


def _dialable_for_row(row) -> list[str]:
    try:
        dialables = row.dialable_list()  # type: ignore[attr-defined]
    except Exception:
        dialables = None
    if dialables:
        cleaned = [part.strip() for part in dialables if part.strip()]
        if cleaned:
            return cleaned
    try:
        fallback = row.primary_dialable()  # type: ignore[attr-defined]
    except Exception:
        fallback = ""
    if fallback and fallback.strip():
        return [fallback.strip()]
    if hasattr(row, "addresses"):
        try:
            return [part for part in row.addresses() if part.strip()]
        except Exception:
            pass
    raw = getattr(row, "participant_addresses", "") or ""
    return [part.strip() for part in raw.split("\u0001") if part.strip()]


def _primary_dialable_for_row(row) -> str:
    dialables = _dialable_for_row(row)
    if dialables:
        return dialables[0]
    return _first_address(getattr(row, "participant_addresses", "") or "")


def present_conversations(
    rows, messages_of, display_name=lambda s: s, now_millis: int = 0,
    include_hidden: bool = False,
) -> list[ConversationRowView]:
    """Map store rows to ordered list-row view states.

    ``messages_of(conversation_id)`` returns that thread's message
    rows. Archived and quarantined threads stay off the main list
    (``include_hidden`` keeps them for review surfaces). Order is
    pinned-first, then recency — the same rule as
    ``core.ConversationList``.
    """
    views: list[ConversationRowView] = []
    for row in rows:
        if not include_hidden and not core.show_on_main_list(
            row.is_archived, row.is_quarantined
        ):
            continue
        messages = list(messages_of(row.id) or [])
        latest = max(
            messages, key=lambda m: m.timestamp_millis, default=None,
        )
        unread = sum(
            1 for m in messages
            if m.direction == "INCOMING" and not m.is_read
        )
        addresses = _dialable_for_row(row)
        muted = core.is_mute_active(
            row.is_muted, row.muted_until_millis, now_millis,
        )
        if latest is not None:
            snippet = snippet_of(latest.body, muted, latest.transport)
            stamp: int | None = latest.timestamp_millis
        else:
            snippet = snippet_of("", muted)
            stamp = None
        marks = "".join(
            label for flag, label in (
                (row.is_pinned, " · Pinned"),
                (muted, " · Muted"),
                (row.is_starred, " · Starred"),
            ) if flag
        )
        views.append(ConversationRowView(
            conversation_id=row.id,
            title=row_title(addresses, display_name),
            snippet=snippet,
            timestamp_millis=stamp,
            unread_count=unread,
            muted=muted,
            pinned=row.is_pinned,
            starred=row.is_starred,
            archived=row.is_archived,
            quarantined=row.is_quarantined,
            address=_primary_dialable_for_row(row),
            marks=marks,
        ))
    views.sort(key=lambda v: (
        0 if v.pinned else 1, -(v.timestamp_millis or 0),
    ))
    return views


def filter_rows(
    views: list[ConversationRowView], query: str,
) -> list[ConversationRowView]:
    """List search: title, snippet or address contains the query."""
    text = (query or "").strip().lower()
    if not text:
        return list(views)
    return [
        view for view in views
        if text in view.title.lower()
        or text in view.snippet.lower()
        or text in view.address.lower()
    ]


def filter_message_rows(rows, query: str) -> list:
    """In-thread search: body contains the query (blank matches all)."""
    text = (query or "").strip().lower()
    if not text:
        return list(rows)
    return [row for row in rows if text in (row.body or "").lower()]


def step_match(current: int, direction: int, total: int) -> int:
    """Move the in-thread match cursor with wraparound; -1 when empty."""
    if total <= 0:
        return -1
    return (current + direction) % total


def message_state(
    direction: str, sent: bool, now_millis: int, timestamp_millis: int,
) -> tuple[str, bool]:
    """(delivery label, show_retry) for one thread row.

    Inbound rows carry no label (the timestamp is the whole state).
    Outbound rows still inside the backoff window read Queued; rows
    the retry queue has held past :data:`HELD_AFTER_MILLIS` read
    Held with a Retry action. Retrying a held row is safe: held
    entries never fire again, so the fresh send cannot duplicate one
    already in flight.
    """
    if (direction or "").upper() != "OUTGOING":
        return "", False
    if sent:
        return "Sent", False
    age = now_millis - (timestamp_millis or now_millis)
    if age > HELD_AFTER_MILLIS:
        return "Held — not sent", True
    return "Queued", False


def thread_uri_for(conversation_id: int) -> str:
    return f"{THREAD_URI_SCHEME}://thread/{int(conversation_id)}"


def parse_thread_uri(uri: str) -> int | None:
    """Conversation id from an ``io.piercingxx.TxxT://thread/ID`` URI."""
    text = (uri or "").strip()
    if "://" not in text:
        return None
    scheme, _, rest = text.partition("://")
    if scheme.lower() != THREAD_URI_SCHEME.lower():
        return None
    parts = [part for part in rest.split("/") if part]
    if len(parts) != 2 or parts[0].lower() != "thread":
        return None
    try:
        value = int(parts[1])
    except ValueError:
        return None
    return value if value > 0 else None


def parse_open_thread_argv(argv: list[str]) -> int | None:
    """Conversation id from ``txxt --open-thread ID`` or a thread URI arg."""
    args = list(argv or [])
    for index, token in enumerate(args):
        if token == "--open-thread" and index + 1 < len(args):
            try:
                value = int(args[index + 1])
            except ValueError:
                return None
            return value if value > 0 else None
    for token in args:
        found = parse_thread_uri(token)
        if found is not None:
            return found
    return None


#: URI schemes that open a conversation for an address (RFC 5724).
SMS_URI_SCHEMES = frozenset({"sms", "smsto", "mms", "mmsto"})


@dataclass(frozen=True)
class SmsTarget:
    """One parsed ``sms:``-family URI: recipients plus optional body."""
    recipients: tuple = ()
    body: str = ""


def parse_sms_uri(uri: str) -> SmsTarget | None:
    """Recipients + body from an ``sms:``-family URI (RFC 5724).

    ``sms:+1614…?body=…`` carries comma-separated recipients and an
    optional ``body`` query field; ``smsto:``/``mms:``/``mmsto:`` parse
    identically. Percent escapes decode (`+` stays literal in
    recipients — it is part of the number — but means space in the
    body). Returns None when no recipient survives.
    """
    from urllib.parse import unquote, unquote_plus

    text = (uri or "").strip()
    scheme, sep, rest = text.partition(":")
    if not sep or scheme.strip().lower() not in SMS_URI_SCHEMES:
        return None
    path, _, query = rest.partition("?")
    if path.startswith("//"):
        path = path[2:]
    recipients = tuple(
        unquote(item.strip())
        for item in path.split(",")
        if item.strip()
    )
    if not recipients:
        return None
    body = ""
    for field in query.split("&"):
        name, eq, value = field.partition("=")
        if eq and name.strip().lower() == "body":
            body = unquote_plus(value)
            break
    return SmsTarget(recipients=recipients, body=body)


def parse_sms_target_argv(argv: list[str]) -> SmsTarget | None:
    """First ``sms:``-family URI in argv (desktop-file ``%U`` launches)."""
    for token in list(argv or []):
        found = parse_sms_uri(token)
        if found is not None:
            return found
    return None


_TLDS = (
    "com|net|org|edu|gov|mil|int|io|co|me|tv|app|dev|ai|gg|sh|ly|xyz|"
    "info|biz|online|site|tech|store|blog|cloud|page|link|shop|news|"
    "live|life|world|us|uk|ca|au|nz|de|fr|es|it|nl|se|no|fi|dk|ch|"
    "at|be|ie|pl|pt|br|mx|jp|kr|cn|in|ru|za"
)
_CANDIDATE = re.compile(
    r"(?:https?://|www\.)[^\s<>\"'“”‘’\]\)]+"
    r"|(?:[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?\.)+(?:" + _TLDS + r")"
    r"(?:/[^\s<>\"'“”‘’\]\)]*)?",
)
_SCHEME = re.compile(r"^https?://", re.IGNORECASE)
_TLD_SET = set(_TLDS.split("|"))
_TRAILING = ".,;:!?)'\"“”‘’]"


def _trim_trailing(raw: str) -> str:
    while raw and raw[-1] in _TRAILING:
        raw = raw[:-1]
    return raw


def normalize_link(raw: str) -> str:
    if _SCHEME.match(raw):
        return raw
    return f"https://{raw}"


def find_links(text: str) -> list[Link]:
    """Every URL in ``text`` as (start, end, url); never fetched."""
    out: list[Link] = []
    for match in _CANDIDATE.finditer(text or ""):
        raw = _trim_trailing(match.group(0))
        if not raw or "." not in raw:
            continue
        if raw.lower().startswith("www."):
            host = raw[4:].split("/")[0]
            if "." not in host:
                continue
        elif not _SCHEME.match(raw):
            host = raw.split("/")[0]
            labels = host.split(".")
            if len(labels) < 2 or labels[-1].lower() not in _TLD_SET:
                continue
        start = match.start()
        out.append(Link(start, start + len(raw), normalize_link(raw)))
    return out


def url_at(links: list[Link], offset: int) -> str | None:
    """The URL covering ``offset``, or None for plain text."""
    for link in links or []:
        if link.start <= offset < link.end:
            return link.url
    return None