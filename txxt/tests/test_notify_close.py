"""Notifications leave the shade once their thread is read.

txxt posted a card per inbound SMS but never called
``CloseNotification``, so cards for threads the user had already read
stayed in the shade (Marathon closes them on read). The daemon now
closes every card it posted for a conversation on ``MarkRead`` and when
the thread really opens (``OpenThread`` or a card's Reply/Open action).
"""

from gi.repository import GLib

from txxt import daemon, notify
from txxt.blocking import InboundFilter
from txxt.daemon import TXXT_IFACE, TXXT_OBJECT_PATH, Daemon, InboundPipeline
from txxt.store import Store


class NotifyBus:
    """org.freedesktop.Notifications: Notify returns ids, Close records."""

    def __init__(self):
        self.next_id = 500
        self.closed = []
        self.fail_close = False

    def call_sync(self, bus, path, iface, method, params, *rest):
        if method == "Notify":
            self.next_id += 1
            return GLib.Variant("(u)", (self.next_id,))
        if method == "CloseNotification":
            if self.fail_close:
                raise OSError("no server")
            self.closed.append(params.unpack()[0])
            return GLib.Variant("()", ())
        raise AssertionError(method)


class Invocation:
    def __init__(self):
        self.reply = None
        self.error = None

    def return_value(self, parameters):
        self.reply = parameters

    def return_dbus_error(self, name, message):
        self.error = (name, message)


class InlineExecutor:
    def submit(self, fn, *args):
        fn(*args)


def _setup():
    store = Store.memory()
    bus = NotifyBus()
    pipe = InboundPipeline(store, filter_provider=lambda: InboundFilter(),
                           notify_bus=bus)
    d = Daemon(store, pipeline=pipe, system_bus_fn=lambda: None,
               session_bus_fn=lambda: None, idle_add=lambda fn, *a: fn(*a))
    d._executor = InlineExecutor()
    return store, bus, pipe, d


def test_close_notification_sends_the_id():
    bus = NotifyBus()
    assert notify.close_notification(bus, 42) is True
    assert bus.closed == [42]
    bus.fail_close = True
    assert notify.close_notification(bus, 43) is False


def test_mark_read_closes_every_card_of_that_thread_only():
    store, bus, pipe, d = _setup()
    first = pipe.handle_sms("+16145550142", "one", 1_000)
    second = pipe.handle_sms("+16145550142", "two", 2_000)
    other = pipe.handle_sms("+13125550199", "elsewhere", 3_000)
    assert first.conversation_id == second.conversation_id
    invocation = Invocation()
    d.handle_method_call(
        None, None, TXXT_OBJECT_PATH, TXXT_IFACE, "MarkRead",
        GLib.Variant("(x)", (first.conversation_id,)), invocation)
    assert invocation.error is None
    assert sorted(bus.closed) == sorted(
        [first.notification_id, second.notification_id])
    assert pipe.conversation_for_notification(first.notification_id) is None
    assert pipe.conversation_for_notification(other.notification_id) == \
        other.conversation_id
    bus.closed.clear()
    d.handle_method_call(
        None, None, TXXT_OBJECT_PATH, TXXT_IFACE, "MarkRead",
        GLib.Variant("(x)", (first.conversation_id,)), Invocation())
    assert bus.closed == []


def test_open_thread_closes_cards_once_the_ui_opened(monkeypatch):
    _store, bus, pipe, d = _setup()
    posted = pipe.handle_sms("+16145550142", "hello", 1_000)
    monkeypatch.setattr(daemon, "open_thread", lambda cid: False)
    d.handle_method_call(
        None, None, TXXT_OBJECT_PATH, TXXT_IFACE, "OpenThread",
        GLib.Variant("(x)", (posted.conversation_id,)), Invocation())
    assert bus.closed == []
    monkeypatch.setattr(daemon, "open_thread", lambda cid: True)
    d.handle_method_call(
        None, None, TXXT_OBJECT_PATH, TXXT_IFACE, "OpenThread",
        GLib.Variant("(x)", (posted.conversation_id,)), Invocation())
    assert bus.closed == [posted.notification_id]


def test_card_action_closes_the_threads_cards(monkeypatch):
    _store, bus, pipe, d = _setup()
    first = pipe.handle_sms("+16145550142", "one", 1_000)
    second = pipe.handle_sms("+16145550142", "two", 2_000)
    monkeypatch.setattr(daemon, "open_thread", lambda cid: True)
    assert d.handle_notification_action(first.notification_id, "reply")
    assert sorted(bus.closed) == sorted(
        [first.notification_id, second.notification_id])


def test_close_failure_never_fails_mark_read():
    store, bus, pipe, d = _setup()
    posted = pipe.handle_sms("+16145550142", "hello", 1_000)
    bus.fail_close = True
    invocation = Invocation()
    d.handle_method_call(
        None, None, TXXT_OBJECT_PATH, TXXT_IFACE, "MarkRead",
        GLib.Variant("(x)", (posted.conversation_id,)), invocation)
    assert invocation.error is None
    assert invocation.reply.unpack() == ()
