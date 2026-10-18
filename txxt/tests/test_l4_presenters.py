"""L4 presenter tests: list rows, thread search, send gate, links, URIs.

Headless: every behavior lives in the pure presenter modules
(``txxt.list_presenter``, ``txxt.compose``), so no display is needed.
"""

from datetime import datetime

from txxt import compose as compose_mod
from txxt.compose import SendGate
from txxt.contacts import ContactsResolver
from txxt.list_presenter import (
    HELD_AFTER_MILLIS,
    UNTITLED,
    Link,
    count_label,
    filter_message_rows,
    filter_rows,
    find_links,
    format_time,
    message_state,
    normalize_link,
    parse_open_thread_argv,
    parse_thread_uri,
    present_conversations,
    row_title,
    snippet_of,
    step_match,
    thread_uri_for,
    url_at,
)
from txxt.store import Store

NOW = 1_750_000_000_000


def display_number(number: str) -> str:
    """Format-tolerant like the real resolver: the store keeps digits."""
    digits = "".join(ch for ch in (number or "") if ch.isdigit())
    return {"15551234567": "Alice"}.get(digits, number)


def seed_thread(store, address, bodies, outgoing=()):
    for index, body in enumerate(bodies):
        if index in outgoing:
            store.persist_outgoing_sms(address, body)
        else:
            store.persist_inbound_sms(address, body, NOW + index * 1000)
    row = store.get_conversation(store.find_or_create_conversation(address))
    return row


# ---- titles (never blank) ----

def test_row_title_resolves_names():
    assert row_title(["+15551234567"], display_number) == "Alice"


def test_row_title_falls_back_to_number_when_blank():
    assert row_title(["+1555"], lambda s: "  ") == "+1555"


def test_row_title_empty_is_untitled():
    assert row_title([]) == UNTITLED
    assert row_title([], display_number) == UNTITLED


def test_resolver_display_never_blank():
    assert ContactsResolver([]).display_name("+1999") == "+1999"


# ---- snippets ----

def test_snippet_is_first_line_trimmed():
    assert snippet_of("  hello\nsecond line  ") == "hello"


def test_snippet_empty_is_empty():
    assert snippet_of("") == ""
    assert snippet_of(None) == ""


def test_snippet_muted_prefix():
    assert snippet_of("hello", muted=True) == "muted · hello"
    assert snippet_of("", muted=True) == "muted"


def test_snippet_photo_placeholder_for_mms():
    assert snippet_of("", transport="MMS") == "[Photo]"
    assert snippet_of("[Photo]", transport="MMS") == "[Photo]"


# ---- list rows over the real store ----

def test_present_rows_carry_snippet_time_unread():
    store = Store.memory()
    seed_thread(store, "+15551234567", ["hello", "second"])
    views = present_conversations(
        store.get_all_conversations(),
        store.get_messages_for_conversation,
        display_number, NOW + 60_000,
    )
    assert len(views) == 1
    view = views[0]
    assert view.title == "Alice"
    assert view.snippet == "second"
    assert view.timestamp_millis == NOW + 1000
    assert view.unread_count == 2
    assert view.address == "+15551234567"
    assert format_time(view.timestamp_millis, NOW + 60_000)


def test_unread_counts_only_inbound_unread():
    store = Store.memory()
    seed_thread(store, "+1555", ["in one", "out one"], outgoing={1})
    views = present_conversations(
        store.get_all_conversations(),
        store.get_messages_for_conversation,
        display_number, NOW,
    )
    assert views[0].unread_count == 1


def test_mark_read_clears_badge_and_unread_marks_again():
    store = Store.memory()
    seed_thread(store, "+1555", ["hello"])
    row = store.get_conversation(store.find_or_create_conversation("+1555"))
    store.mark_conversation_read(row.id)
    views = present_conversations(
        store.get_all_conversations(),
        store.get_messages_for_conversation,
        display_number, NOW,
    )
    assert views[0].unread_count == 0
    store.mark_conversation_unread(row.id)
    views = present_conversations(
        store.get_all_conversations(),
        store.get_messages_for_conversation,
        display_number, NOW,
    )
    assert views[0].unread_count == 1


def test_pinned_first_then_recency():
    store = Store.memory()
    seed_thread(store, "+1001", ["old"], )
    seed_thread(store, "+1002", ["new"])
    rows = {r.participant_addresses: r for r in store.get_all_conversations()}
    store.toggle_pin(rows["1001"].id)
    views = present_conversations(
        store.get_all_conversations(),
        store.get_messages_for_conversation,
        display_number, NOW,
    )
    assert [v.address for v in views] == ["+1001", "+1002"]


def test_archived_and_quarantined_hidden_unless_included():
    store = Store.memory()
    seed_thread(store, "+1555", ["hello"])
    row = store.get_conversation(store.find_or_create_conversation("+1555"))
    flags = row.flags().archive()
    store.set_flags(row.id, flags)
    loader = store.get_messages_for_conversation
    assert present_conversations(
        store.get_all_conversations(), loader, display_number, NOW) == []
    shown = present_conversations(
        store.get_all_conversations(), loader, display_number, NOW,
        include_hidden=True)
    assert len(shown) == 1 and shown[0].archived


def test_empty_thread_has_no_timestamp():
    store = Store.memory()
    cid = store.find_or_create_conversation("+1555")
    _ = cid
    views = present_conversations(
        store.get_all_conversations(),
        store.get_messages_for_conversation,
        display_number, NOW,
    )
    assert views[0].timestamp_millis is None
    assert format_time(None, NOW) == ""


def test_photo_placeholder_row():
    store = Store.memory()
    store.persist_inbound_mms_metadata("+1555", NOW)
    views = present_conversations(
        store.get_all_conversations(),
        store.get_messages_for_conversation,
        display_number, NOW,
    )
    assert views[0].snippet == "[Photo]"


def test_muted_thread_snippet_prefix_and_marks():
    store = Store.memory()
    seed_thread(store, "+1555", ["hello"])
    row = store.get_conversation(store.find_or_create_conversation("+1555"))
    store.set_flags(row.id, row.flags().mute())
    views = present_conversations(
        store.get_all_conversations(),
        store.get_messages_for_conversation,
        display_number, NOW,
    )
    assert views[0].snippet == "muted · hello"
    assert "Muted" in views[0].marks


# ---- list search ----

def test_list_search_matches_title_snippet_address():
    store = Store.memory()
    seed_thread(store, "+15551234567", ["dinner plans"])
    seed_thread(store, "+1999", ["dentist"])
    views = present_conversations(
        store.get_all_conversations(),
        store.get_messages_for_conversation,
        display_number, NOW,
    )
    assert [v.address for v in filter_rows(views, "alice")] == ["+15551234567"]
    assert [v.address for v in filter_rows(views, "DINNER")] == ["+15551234567"]
    assert [v.address for v in filter_rows(views, "1999")] == ["+1999"]
    assert len(filter_rows(views, "  ")) == 2
    assert filter_rows(views, "nope") == []


# ---- timestamps ----

def test_format_time_buckets():
    midday = int(datetime(2024, 6, 3, 12, 0).astimezone().timestamp() * 1000)
    morning = int(datetime(2024, 6, 3, 9, 30).astimezone().timestamp() * 1000)
    assert format_time(morning, midday) == "09:30"
    sunday = int(datetime(2024, 6, 2, 12, 0).astimezone().timestamp() * 1000)
    assert format_time(sunday, midday) == "Yesterday"
    friday = int(datetime(2024, 5, 31, 12, 0).astimezone().timestamp() * 1000)
    assert format_time(friday, midday) == "Friday"
    old = int(datetime(2024, 1, 9, 12, 0).astimezone().timestamp() * 1000)
    assert format_time(old, midday) == "2024-01-09"


# ---- in-thread search ----

def test_thread_search_filters_and_blank_matches_all():
    store = Store.memory()
    seed_thread(store, "+1555", ["dinner tonight", "dentist tomorrow"])
    rows = store.get_messages_for_conversation(
        store.find_or_create_conversation("+1555"))
    assert [r.body for r in filter_message_rows(rows, "DINNER")] == ["dinner tonight"]
    assert len(filter_message_rows(rows, "  ")) == 2


def test_match_cursor_wraps_and_empty_is_minus_one():
    assert step_match(0, 1, 3) == 1
    assert step_match(2, 1, 3) == 0
    assert step_match(0, -1, 3) == 2
    assert step_match(0, 1, 0) == -1


# ---- delivery state (queued / held + Retry) ----

def test_delivery_states():
    assert message_state("OUTGOING", True, NOW, NOW - 1000) == ("Sent", False)
    assert message_state("OUTGOING", False, NOW, NOW - 1000) == ("Queued", False)
    label, retry = message_state(
        "OUTGOING", False, NOW, NOW - HELD_AFTER_MILLIS - 1)
    assert label == "Held — not sent" and retry is True
    assert message_state("INCOMING", True, NOW, NOW) == ("", False)


def test_retry_only_after_hold_window():
    label, retry = message_state(
        "OUTGOING", False, NOW, NOW - HELD_AFTER_MILLIS)
    assert (label, retry) == ("Queued", False)


# ---- send gate + compose validation ----

def test_send_gate_one_at_a_time():
    gate = SendGate()
    assert not gate.is_sending
    assert gate.begin() is True
    assert gate.is_sending
    assert gate.begin() is False
    gate.end()
    assert not gate.is_sending
    assert gate.begin() is True


def test_validate_outgoing_rejects_blanks():
    assert compose_mod.validate_outgoing("", "hi") is not None
    assert compose_mod.validate_outgoing("+1555", "   ") is not None
    assert compose_mod.validate_outgoing("+1555", "hi") is None


# ---- thread URIs and --open-thread ----

def test_thread_uri_round_trip():
    assert thread_uri_for(12) == "io.piercingxx.TxxT://thread/12"
    assert parse_thread_uri(thread_uri_for(12)) == 12


def test_parse_thread_uri_rejects():
    assert parse_thread_uri("") is None
    assert parse_thread_uri("io.piercingxx.TxxT://thread/") is None
    assert parse_thread_uri("io.piercingxx.TxxT://thread/abc") is None
    assert parse_thread_uri("io.piercingxx.TxxT://thread/0") is None
    assert parse_thread_uri("https://example.com/thread/12") is None
    assert parse_thread_uri("io.piercingxx.TxxT://other/12") is None


def test_parse_open_thread_argv():
    assert parse_open_thread_argv(["txxt", "--open-thread", "7"]) == 7
    assert parse_open_thread_argv(["txxt", "io.piercingxx.TxxT://thread/9"]) == 9
    assert parse_open_thread_argv(["txxt"]) is None
    assert parse_open_thread_argv(["txxt", "--open-thread", "abc"]) is None


# ---- links (offsets only, never fetched) ----

def test_find_links_shapes():
    links = find_links("see https://example.com/a and example.net!")
    assert links[0] == Link(4, 25, "https://example.com/a")
    assert links[1].url == "https://example.net"
    assert links[1].end == len("see https://example.com/a and example.net")


def test_find_links_skips_non_links():
    assert find_links("read notes.txt then v1.2 and Mr.Smith") == []
    assert find_links("") == []


def test_find_links_www_gets_scheme():
    assert find_links("go www.example.org/x")[0].url == "https://www.example.org/x"


def test_url_at_and_normalize():
    links = find_links("go https://example.com now")
    assert url_at(links, 5) == "https://example.com"
    assert url_at(links, 0) is None
    assert url_at([], 3) is None
    assert normalize_link("https://example.com") == "https://example.com"
    assert normalize_link("example.com") == "https://example.com"


def test_count_label_pluralises_by_count():
    assert count_label(1, "number") == "1 number"
    assert count_label(2, "term") == "2 terms"
    assert count_label(0, "thread") == "0 threads"
    assert count_label(3, "match", "matches") == "3 matches"
