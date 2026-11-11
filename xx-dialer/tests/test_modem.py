"""VoiceClient over a fake MM1 system bus with the real signatures.

The fake mirrors the traps: every reply unpacks to a TUPLE of out-args,
async callbacks take exactly two args, signal handlers take exactly
seven, and ``reply_type`` is always None (never a plain string).
"""
from xx_dialer import modem
from xx_dialer.modem import (
    CALL_IFACE,
    OBJMGR_IFACE,
    PROPS_IFACE,
    VOICE_IFACE,
    VoiceClient,
)

MODEM_A = "/org/freedesktop/ModemManager1/Modem/0"
MODEM_B = "/org/freedesktop/ModemManager1/Modem/1"
CALL_1 = "/org/freedesktop/ModemManager1/Call/0"


class FakeVariant:
    def __init__(self, fmt, value):
        self.fmt = fmt
        self.value = value

    def unpack(self):
        return self.value


class FakeGlib:
    Error = type("Error", (Exception,), {})

    def Variant(self, fmt, value):
        return FakeVariant(fmt, value)


class FakeGio:
    BusType = type("BusType", (), {"SYSTEM": "system", "SESSION": "s"})
    DBusCallFlags = type("Flags", (), {"NONE": 0})
    DBusSignalFlags = type("Flags", (), {"NONE": 0})

    def __init__(self, bus=None):
        self._bus = bus

    def bus_get_sync(self, *_args):
        return self._bus


def _as_str(value):
    if isinstance(value, FakeVariant):
        return _as_str(value.value)
    return value


def _as_dict(value):
    if isinstance(value, FakeVariant):
        value = value.value
    if isinstance(value, tuple) and len(value) == 1:
        value = value[0]
    if isinstance(value, FakeVariant):
        value = value.value
    return {key: _as_str(val) for key, val in dict(value).items()}


class FakeMM1:
    """In-memory ModemManager with the MM 1.22 Voice/Call shapes."""

    def __init__(self):
        self.objects = {}
        self.calls = {}
        self.methods = []
        self.next_call = 0

    def add_modem(self, path, with_voice=True):
        ifaces = {modem.MODEM_IFACE: {}}
        if with_voice:
            ifaces[VOICE_IFACE] = {}
        self.objects[path] = ifaces

    def add_call(self, path, state, direction, number):
        self.calls[path] = {"State": state, "Direction": direction,
                            "Number": number}

    def dispatch_sync(self, path, iface, method, params):
        self.methods.append((path, iface, method, params))
        if (iface, method) == (OBJMGR_IFACE, "GetManagedObjects"):
            return FakeVariant("(a{oa{sa{sv}}})", (dict(self.objects),))
        if (iface, method) == (VOICE_IFACE, "CreateCall"):
            props = _as_dict(params)
            number = props.get("number", "")
            self.next_call += 1
            call_path = (
                "/org/freedesktop/ModemManager1/Call/%d" % self.next_call)
            self.calls[call_path] = {"State": modem.CALL_STATE_DIALING,
                                     "Direction":
                                     modem.CALL_DIRECTION_OUTGOING,
                                     "Number": number}
            return FakeVariant("(o)", (call_path,))
        if (iface, method) == (VOICE_IFACE, "ListCalls"):
            return FakeVariant("(ao)", (sorted(self.calls),))
        if (iface, method) == (CALL_IFACE, "Start"):
            return FakeVariant("()", ())
        if method in ("Accept", "Hangup", "Hold", "Unhold"):
            return FakeVariant("()", ())
        if (iface, method) == (CALL_IFACE, "SendDtmf"):
            return FakeVariant("()", ())
        if (iface, method) == (PROPS_IFACE, "GetAll"):
            props = dict(self.calls.get(path, {}))
            return FakeVariant("(a{sv})", (props,))
        raise AssertionError("unexpected sync %s.%s" % (iface, method))


class FakeBus:
    def __init__(self, mm):
        self.mm = mm
        self.calls = []
        self.pending = []
        self._results = []
        self.subs = []
        self.unsubs = []
        self._next_id = 1

    def call(self, bus_name, path, iface, method, params=None,
             reply_type=None, flags=None, timeout=-1, cancellable=None,
             callback=None, *user_data):
        value = params.unpack() if params is not None else None
        self.calls.append({"bus": bus_name, "path": path, "iface": iface,
                           "method": method, "params": value,
                           "reply_type": reply_type, "timeout": timeout,
                           "user_data": user_data})
        self.pending.append((path, iface, method, value, callback,
                             user_data))

    def call_sync(self, bus_name, path, iface, method, params=None,
                  reply_type=None, flags=None, timeout=-1,
                  cancellable=None):
        value = params.unpack() if params is not None else None
        self.calls.append({"bus": bus_name, "path": path, "iface": iface,
                           "method": method, "params": value,
                           "reply_type": reply_type, "timeout": timeout,
                           "user_data": ()})
        return self.mm.dispatch_sync(path, iface, method, value)

    def call_finish(self, _result):
        payload = self._results.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return payload

    def signal_subscribe(self, sender, iface, member, path, arg0,
                         flags, callback, user_data):
        self.subs.append({"sender": sender, "iface": iface,
                          "member": member, "path": path,
                          "callback": callback, "user_data": user_data})
        sid = self._next_id
        self._next_id += 1
        return sid

    def signal_unsubscribe(self, sid):
        self.unsubs.append(sid)

    def drain(self):
        while self.pending:
            path, iface, method, value, callback, user_data = \
                self.pending.pop(0)
            try:
                reply = self.mm.dispatch_sync(path, iface, method, value)
            except Exception as err:
                self._results.append(err)
            else:
                self._results.append(reply)
            callback(self, object(), *user_data)

    def emit(self, member, path, signal_path, params):
        for sub in self.subs:
            if sub["member"] == member and sub["path"] in (path, None):
                sub["callback"](self, modem.MM1, signal_path,
                                sub["iface"], member, params,
                                sub["user_data"])


def _wired(on_calls=None, on_state=None):
    mm = FakeMM1()
    mm.add_modem(MODEM_A, with_voice=True)
    bus = FakeBus(mm)
    gio = FakeGio(bus)
    seen_calls = []
    seen_states = []
    client = VoiceClient(
        on_calls=on_calls or seen_calls.append,
        on_state=on_state or (lambda *a: seen_states.append(a)),
        bus=bus, gio=gio, glib=FakeGlib(),
        spawn=lambda fn: fn(), idle=lambda fn, *a: fn(*a))
    assert client.start()
    bus.drain()
    return client, bus, mm, seen_calls, seen_states


def test_enumerate_resolves_voice_modem():
    client, bus, mm, _calls, _states = _wired()
    assert client.modem_path() == MODEM_A
    assert bus.calls[0]["method"] == "GetManagedObjects"
    assert bus.calls[0]["params"] is None


def test_resolve_prefers_voice_and_sorts():
    mm = FakeMM1()
    mm.add_modem(MODEM_B, with_voice=False)
    mm.add_modem(MODEM_A, with_voice=True)
    bus = FakeBus(mm)
    assert modem.resolve_modem_path(bus=bus) == MODEM_A


def test_place_resolves_modem_per_operation():
    client, bus, mm, _calls, _states = _wired()
    results = []
    client.place("+14155550100", lambda ok, d: results.append((ok, d)))
    assert results[0][0] is True
    created = [c for c in bus.calls if c["method"] == "CreateCall"]
    assert len(created) == 1 and created[0]["path"] == MODEM_A
    assert created[0]["bus"] == modem.MM1
    assert created[0]["iface"] == VOICE_IFACE
    assert _as_dict(created[0]["params"])["number"] == "+14155550100"

    mm.add_modem(MODEM_B, with_voice=True)
    mm.objects.pop(MODEM_A)
    client._modems.pop(MODEM_A, None)
    client._voice_subs.pop(MODEM_A, None)
    results.clear()
    client.place("+14155550200", lambda ok, d: results.append((ok, d)))
    assert results[0][0] is True
    created = [c for c in bus.calls if c["method"] == "CreateCall"]
    assert created[-1]["path"] == MODEM_B


def test_place_empty_number_fails_without_bus():
    client, bus, _mm, _calls, _states = _wired()
    results = []
    before = len(bus.calls)
    client.place("   ", lambda ok, d: results.append((ok, d)))
    assert results and results[0][0] is False
    assert len(bus.calls) == before


def test_call_primitives_hit_call_interface():
    client, bus, _mm, _calls, _states = _wired()
    results = []
    client.accept(CALL_1, lambda ok, d: results.append(("a", ok)))
    client.hangup(CALL_1, lambda ok, d: results.append(("h", ok)))
    client.hold(CALL_1, lambda ok, d: results.append(("ho", ok)))
    client.unhold(CALL_1, lambda ok, d: results.append(("u", ok)))
    client.send_dtmf(CALL_1, "5", lambda ok, d: results.append(("d", ok)))
    bus.drain()
    by_method = {c["method"]: c for c in bus.calls}
    for method in ("Accept", "Hangup", "Hold", "Unhold", "SendDtmf"):
        assert by_method[method]["iface"] == CALL_IFACE
        assert by_method[method]["path"] == CALL_1
    dtmf = by_method["SendDtmf"]["params"]
    assert dtmf == ("5",)
    assert [name for name, _ok in results] == ["a", "h", "ho", "u", "d"]
    assert all(ok for _name, ok in results)


def test_send_dtmf_rejects_invalid_digit():
    client, bus, _mm, _calls, _states = _wired()
    results = []
    before = len(bus.calls)
    client.send_dtmf(CALL_1, "x", lambda ok, d: results.append((ok, d)))
    assert results == [(False, "invalid DTMF digit: 'x'")]
    assert len(bus.calls) == before


def test_every_async_call_uses_no_reply_type_or_user_data():
    client, bus, _mm, _calls, _states = _wired()
    results = []
    client.accept(CALL_1, lambda ok, d: results.append(ok))
    client.hangup(CALL_1, lambda ok, d: results.append(ok))
    bus.drain()
    assert bus.calls
    for call in bus.calls:
        assert call["reply_type"] is None
        assert call["user_data"] == ()


def test_call_added_adopted_with_real_tuple_shapes():
    states = []
    client, bus, mm, _calls, _states = _wired(
        on_state=lambda *a: states.append(a))
    mm.add_call(CALL_1, modem.CALL_STATE_RINGING_IN,
                modem.CALL_DIRECTION_INCOMING, "tel:+14155550100")
    bus.emit("CallAdded", MODEM_A, MODEM_A,
             FakeVariant("(o)", (CALL_1,)))
    bus.drain()
    tracked = {c.path: c for c in client.calls()}
    assert tracked[CALL_1].number == "+14155550100"
    assert tracked[CALL_1].state == modem.CALL_STATE_RINGING_IN
    assert tracked[CALL_1].direction == modem.CALL_DIRECTION_INCOMING
    state_subs = [s for s in bus.subs if s["member"] == "StateChanged"]
    assert len(state_subs) == 1
    assert state_subs[0]["path"] == CALL_1
    assert state_subs[0]["iface"] == CALL_IFACE


def test_call_deleted_drops_call():
    client, bus, mm, _calls, _states = _wired()
    mm.add_call(CALL_1, modem.CALL_STATE_ACTIVE,
                modem.CALL_DIRECTION_OUTGOING, "+14155550100")
    bus.emit("CallAdded", MODEM_A, MODEM_A,
             FakeVariant("(o)", (CALL_1,)))
    bus.drain()
    assert CALL_1 in {c.path for c in client.calls()}
    bus.emit("CallDeleted", MODEM_A, MODEM_A,
             FakeVariant("(o)", (CALL_1,)))
    assert CALL_1 not in {c.path for c in client.calls()}


def test_state_changed_updates_and_reports_seven_arg_shape():
    states = []
    client, bus, mm, _calls, _states = _wired(
        on_state=lambda *a: states.append(a))
    mm.add_call(CALL_1, modem.CALL_STATE_RINGING_IN,
                modem.CALL_DIRECTION_INCOMING, "+14155550100")
    bus.emit("CallAdded", MODEM_A, MODEM_A,
             FakeVariant("(o)", (CALL_1,)))
    bus.drain()
    bus.emit("StateChanged", CALL_1, CALL_1,
             FakeVariant("(iiu)", (modem.CALL_STATE_RINGING_IN,
                                   modem.CALL_STATE_ACTIVE, 3)))
    tracked = {c.path: c for c in client.calls()}
    assert tracked[CALL_1].state == modem.CALL_STATE_ACTIVE
    assert states == [(CALL_1, modem.CALL_STATE_RINGING_IN,
                       modem.CALL_STATE_ACTIVE, 3)]
    bus.emit("StateChanged", CALL_1, CALL_1,
             FakeVariant("(iiu)", (modem.CALL_STATE_ACTIVE,
                                   modem.CALL_STATE_TERMINATED, 4)))
    assert client.calls() == []


def test_hangup_all_only_hangs_live_calls():
    client, bus, mm, _calls, _states = _wired()
    live = "/org/freedesktop/ModemManager1/Call/10"
    dead = "/org/freedesktop/ModemManager1/Call/11"
    mm.add_call(live, modem.CALL_STATE_ACTIVE,
                modem.CALL_DIRECTION_OUTGOING, "+14155550100")
    for path in (live, dead):
        bus.emit("CallAdded", MODEM_A, MODEM_A,
                 FakeVariant("(o)", (path,)))
    bus.drain()
    results = []
    client.hangup_all(lambda ok, d: results.append((ok, d)))
    bus.drain()
    hung = [c["path"] for c in bus.calls if c["method"] == "Hangup"]
    assert hung == [live]
    assert results == [(True, 1)]


def test_hold_and_accept_holds_active_then_accepts_waiting():
    client, bus, mm, _calls, _states = _wired()
    active = "/org/freedesktop/ModemManager1/Call/20"
    waiting = "/org/freedesktop/ModemManager1/Call/21"
    mm.add_call(active, modem.CALL_STATE_ACTIVE,
                modem.CALL_DIRECTION_OUTGOING, "+14155550100")
    mm.add_call(waiting, modem.CALL_STATE_WAITING,
                modem.CALL_DIRECTION_INCOMING, "+14155550200")
    for path in (active, waiting):
        bus.emit("CallAdded", MODEM_A, MODEM_A,
                 FakeVariant("(o)", (path,)))
    bus.drain()
    results = []
    client.hold_and_accept(waiting, lambda ok, d: results.append(ok))
    bus.drain()
    ordered = [c["method"] for c in bus.calls
               if c["method"] in ("Hold", "Accept")]
    assert ordered == ["Hold", "Accept"]
    assert results == [True]


def test_send_dtmf_uses_the_shared_filter():
    client, bus, _mm, _calls, _states = _wired()
    results = []
    for bad in ("+", ",", "12", ""):
        client.send_dtmf(CALL_1, bad, lambda ok, d: results.append(ok))
    client.send_dtmf(CALL_1, "d", lambda ok, d: results.append(ok))
    bus.drain()
    assert results == [False, False, False, False, True]
    sent = [c["params"] for c in bus.calls if c["method"] == "SendDtmf"]
    assert sent == [("D",)]


class _Watch:
    def __init__(self):
        self.args = None
        self.unwatched = []

    def watch(self, bus_type, name, flags, appeared, vanished):
        self.args = (bus_type, name, appeared, vanished)
        return 7


def _watched():
    watch = _Watch()
    mm = FakeMM1()
    mm.add_modem(MODEM_A, with_voice=True)
    mm.add_call(CALL_1, modem.CALL_STATE_ACTIVE,
                modem.CALL_DIRECTION_INCOMING, "tel:+14155550100")
    bus = FakeBus(mm)
    gio = FakeGio(bus)
    gio.BusNameWatcherFlags = type("W", (), {"NONE": 0})
    gio.bus_watch_name = watch.watch
    gio.bus_unwatch_name = watch.unwatched.append
    seen = []
    client = VoiceClient(on_calls=seen.append, bus=bus, gio=gio,
                         glib=FakeGlib(), spawn=lambda fn: fn(),
                         idle=lambda fn, *a: fn(*a))
    assert client.start()
    bus.drain()
    return client, bus, mm, watch, seen


def test_modem_manager_restart_drops_and_re_enumerates():
    client, bus, mm, watch, seen = _watched()
    assert watch.args[:2] == ("system", modem.MM1)
    appeared, vanished = watch.args[2], watch.args[3]
    appeared(None, modem.MM1, ":1.5")
    assert [c["method"] for c in bus.calls].count("GetManagedObjects") == 1
    assert [c.path for c in client.calls()] == [CALL_1]
    vanished(None, modem.MM1)
    assert client.calls() == []
    assert client.modem_path() is None
    assert seen[-1] == []
    assert client._voice_subs == {} and client._subs == {}
    appeared(None, modem.MM1, ":1.9")
    bus.drain()
    assert [c["method"] for c in bus.calls].count("GetManagedObjects") == 2
    assert client.modem_path() == MODEM_A
    assert [c.path for c in client.calls()] == [CALL_1]


def test_new_owner_without_vanish_resets_too():
    client, bus, _mm, watch, seen = _watched()
    appeared = watch.args[2]
    appeared(None, modem.MM1, ":1.5")
    appeared(None, modem.MM1, ":1.6")
    assert seen[-1] == []
    bus.drain()
    assert [c.path for c in client.calls()] == [CALL_1]


def test_stop_unwatches_modem_manager():
    client, _bus, _mm, watch, _seen = _watched()
    client.stop()
    assert watch.unwatched == [7]
