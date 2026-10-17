"""D-Bus service tests: Contacts1 dispatch over Gio shapes (L1).

Fakes mirror the real library exactly: parameters are real GLib.Variant
tuples, replies must be tuples (``reply.unpack()[0]`` is the first out-arg),
and the invocation exposes only ``return_value`` / ``return_dbus_error``.
"""

import pytest
from gi.repository import Gio, GLib

from xx_contacts.phones import LookupIndex
from xx_contacts.service import (
    BUS_NAME,
    ERROR_INVALID_ARGUMENT,
    ERROR_NOT_FOUND,
    ERROR_NOT_IMPLEMENTED,
    INTERFACE,
    INTROSPECTION_XML,
    OBJECT_PATH,
    ContactsService,
)
from xx_contacts.store import ContactData, ContactStore, PhoneEntry


class FakeInvocation:
    def __init__(self):
        self.reply = None
        self.error = None

    def return_value(self, parameters):
        self.reply = parameters

    def return_dbus_error(self, name, message):
        self.error = (name, message)


class FakeConnection:
    def __init__(self):
        self.signals = []

    def emit_signal(self, destination, path, interface, signal, parameters):
        self.signals.append((destination, path, interface, signal,
                             parameters))
        return True


ADA_CARD = ("BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Ada Lovelace\r\n"
            "TEL;TYPE=CELL:+1 555 0100\r\nEND:VCARD\r\n")


@pytest.fixture
def service():
    store = ContactStore(":memory:")
    handle = ContactsService(store, LookupIndex(), "US")
    handle._connection = FakeConnection()
    yield handle
    store.close()


def _call(service, method, signature, *args):
    invocation = FakeInvocation()
    params = GLib.Variant(signature, args) if signature else None
    service.handle_method_call(None, ":1.1", OBJECT_PATH, INTERFACE, method,
                               params, invocation)
    return invocation


def _out(invocation):
    assert invocation.error is None, invocation.error
    assert invocation.reply is not None
    assert invocation.reply.get_type_string().startswith("(")
    return invocation.reply.unpack()


def test_introspection_lists_full_interface():
    node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
    assert len(node.interfaces) == 1
    iface = node.interfaces[0]
    assert iface.name == INTERFACE
    methods = {m.name: m for m in iface.methods}
    assert set(methods) == {"LookupNumber", "LookupEmail", "Search", "Get",
                            "List", "Create", "Update", "Delete", "SetTags",
                            "UpdateTags", "Import", "Pick", "PickReply"}
    assert methods["LookupNumber"].in_args[0].signature == "s"
    assert methods["List"].in_args[0].signature == "t"
    assert methods["SetTags"].in_args[1].signature == "as"
    assert methods["Search"].out_args[0].signature == "a(sss)"
    assert [a.signature for a in methods["UpdateTags"].in_args] == [
        "s", "as", "as"]
    assert [a.signature for a in methods["UpdateTags"].out_args] == ["t"]
    assert [a.signature for a in methods["Import"].in_args] == ["s"]
    assert [a.signature for a in methods["Import"].out_args] == [
        "u", "u", "u"]
    assert [a.signature for a in methods["Pick"].in_args] == ["s"]
    assert [a.signature for a in methods["Pick"].out_args] == ["s", "s"]
    assert [a.signature for a in methods["PickReply"].in_args] == [
        "s", "s", "s"]
    assert methods["PickReply"].out_args == []
    signals = {s.name: s for s in iface.signals}
    assert set(signals) == {"Changed"}
    assert signals["Changed"].args[0].signature == "t"


def test_create_lookup_get_round_trip(service):
    created = _call(service, "Create", "(s)", ADA_CARD)
    (uid,) = _out(created)
    assert uid

    lookup = _call(service, "LookupNumber", "(s)", "5550100")
    found_uid, name, tags = _out(lookup)
    assert (found_uid, name, tags) == (uid, "Ada Lovelace", [])

    get = _call(service, "Get", "(s)", uid)
    (card,) = _out(get)
    assert "FN:Ada Lovelace" in card


def test_lookup_miss_returns_empty_tuple(service):
    lookup = _call(service, "LookupNumber", "(s)", "5559999")
    assert _out(lookup) == ("", "", [])


def test_search_lists_and_updates(service):
    uid = _out(_call(service, "Create", "(s)", ADA_CARD))[0]
    results = _out(_call(service, "Search", "(su)", "ada", 10))[0]
    assert results == [(uid, "Ada Lovelace", "+1 555 0100")]

    listing = _out(_call(service, "List", "(t)", 0))
    revision, changed, deleted = listing
    assert revision == 1 and changed == [uid] and deleted == []

    update = _call(service, "Update", "(ss)", uid, ADA_CARD.replace(
        "Ada Lovelace", "Ada King"))
    assert _out(update) == ()
    assert _out(_call(service, "Get", "(s)", uid))[0].count(
        "FN:Ada King") == 1

    service.set_tags(uid, ["starred"])
    assert _out(_call(service, "LookupNumber", "(s)", "5550100"))[2] == [
        "starred"]

    delete = _call(service, "Delete", "(s)", uid)
    assert _out(delete) == ()
    revision2, changed2, deleted2 = _out(_call(service, "List", "(t)", 1))
    assert revision2 == 4 and changed2 == [] and deleted2 == [uid]


def test_get_update_delete_unknown_uid(service):
    assert _call(service, "Get", "(s)",
                 "00000000-0000-4000-8000-000000000000").error[0] \
        == ERROR_NOT_FOUND
    assert _call(service, "Update", "(ss)",
                 "00000000-0000-4000-8000-000000000000",
                 ADA_CARD).error[0] == ERROR_NOT_FOUND
    assert _call(service, "Delete", "(s)",
                 "00000000-0000-4000-8000-000000000000").error[0] \
        == ERROR_NOT_FOUND
    assert _call(service, "SetTags", "(sas)",
                 "00000000-0000-4000-8000-000000000000",
                 ["starred"]).error[0] == ERROR_NOT_FOUND


def test_create_rejects_non_vcard(service):
    invocation = _call(service, "Create", "(s)", "FN:lonely line\r\n")
    assert invocation.error is not None
    assert invocation.error[0] == ERROR_INVALID_ARGUMENT


def test_set_tags_rejects_unknown_tag(service):
    uid = _out(_call(service, "Create", "(s)", ADA_CARD))[0]
    invocation = _call(service, "SetTags", "(sas)", uid, ["vip"])
    assert invocation.error is not None
    assert invocation.error[0] == ERROR_INVALID_ARGUMENT


def test_pick_without_a_display_is_not_implemented(service):
    service.pick_presenter = lambda _kind, _rows, _done: False
    invocation = _call(service, "Pick", "(s)", "phone")
    assert invocation.reply is None
    assert invocation.error is not None
    assert invocation.error[0] == ERROR_NOT_IMPLEMENTED
    assert not service.pick_pending()


def test_unknown_method_is_invalid_argument(service):
    invocation = _call(service, "Pick2", "(s)", "phone")
    assert invocation.error is not None
    assert invocation.error[0] == ERROR_INVALID_ARGUMENT


def test_changed_signal_on_writes(service):
    conn = service._connection
    uid = _out(_call(service, "Create", "(s)", ADA_CARD))[0]
    assert len(conn.signals) == 1
    _dest, path, iface, signal, params = conn.signals[-1]
    assert (path, iface, signal) == (OBJECT_PATH, INTERFACE, "Changed")
    assert params.unpack() == (1, [uid])
    _call(service, "SetTags", "(sas)", uid, ["business"])
    assert conn.signals[-1][4].unpack() == (2, [uid])
    _call(service, "Update", "(ss)", uid, ADA_CARD)
    assert conn.signals[-1][4].unpack() == (3, [uid])
    _call(service, "Delete", "(s)", uid)
    assert conn.signals[-1][4].unpack() == (4, [uid])


def test_update_tags_round_trip_returns_revision(service):
    uid = _out(_call(service, "Create", "(s)", ADA_CARD))[0]
    before = service._store.get_revision()
    updated = _call(service, "UpdateTags", "(sasas)", uid, ["starred"], [])
    (revision,) = _out(updated)
    assert revision == before + 1
    assert _out(_call(service, "LookupNumber", "(s)", "5550100"))[2] == [
        "starred"]
    assert service._connection.signals[-1][4].unpack() == (revision, [uid])
    updated2 = _call(service, "UpdateTags", "(sasas)", uid, ["blocked"],
                     ["starred"])
    (revision2,) = _out(updated2)
    assert _out(_call(service, "LookupNumber", "(s)", "5550100"))[2] == [
        "blocked"]
    assert service._connection.signals[-1][4].unpack() == (revision2, [uid])


def test_update_tags_rejects_unknown_tag_and_unknown_uid(service):
    uid = _out(_call(service, "Create", "(s)", ADA_CARD))[0]
    assert _call(service, "UpdateTags", "(sasas)", uid, ["vip"],
                 []).error[0] == ERROR_INVALID_ARGUMENT
    assert _call(service, "UpdateTags", "(sasas)", uid, [],
                 ["vip"]).error[0] == ERROR_INVALID_ARGUMENT
    assert _call(service, "UpdateTags", "(sasas)",
                 "00000000-0000-4000-8000-000000000000", ["starred"],
                 []).error[0] == ERROR_NOT_FOUND


def test_create_materializes_embedded_photo(tmp_path):
    import base64

    from xx_contacts import photos as photos_module

    db_path = str(tmp_path / "c.db")
    store = ContactStore(db_path)
    try:
        handle = ContactsService(store, LookupIndex(), "US")
        handle._connection = FakeConnection()
        raw = _test_jpeg_bytes()
        card = ("BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Ada Lovelace\r\n"
                "PHOTO:data:image/jpeg;base64,"
                + base64.b64encode(raw).decode("ascii")
                + "\r\nEND:VCARD\r\n")
        uid = _out(_call(handle, "Create", "(s)", card))[0]
        record = store.get(uid)
        assert record.photo_path.endswith(f"{uid}.jpg")
        assert photos_module.photo_file_mode_ok(record.photo_path)
        assert not any(line.prop == "PHOTO" for line in record.extra)
        fetched = _out(_call(handle, "Get", "(s)", uid))[0]
        assert "FN:Ada Lovelace" in fetched
    finally:
        store.close()


def test_update_replaces_photo_and_drops_orphan(tmp_path):
    import base64

    from xx_contacts import photos as photos_module

    db_path = str(tmp_path / "c.db")
    store = ContactStore(db_path)
    try:
        handle = ContactsService(store, LookupIndex(), "US")
        handle._connection = FakeConnection()
        uid = _out(_call(handle, "Create", "(s)", ADA_CARD))[0]
        card = ("BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Ada Lovelace\r\n"
                "PHOTO:data:image/jpeg;base64,"
                + base64.b64encode(_test_jpeg_bytes()).decode("ascii")
                + "\r\nEND:VCARD\r\n")
        assert _out(_call(handle, "Update", "(ss)", uid, card)) == ()
        first_path = store.get(uid).photo_path
        assert first_path and photos_module.photo_file_mode_ok(first_path)
        assert _out(_call(handle, "Update", "(ss)", uid, ADA_CARD)) == ()
        assert store.get(uid).photo_path == ""
        import os

        assert not os.path.exists(first_path)
    finally:
        store.close()


def test_update_keeps_uri_photo_path_without_deleting(tmp_path):
    db_path = str(tmp_path / "c.db")
    store = ContactStore(db_path)
    try:
        handle = ContactsService(store, LookupIndex(), "US")
        handle._connection = FakeConnection()
        kept = str(tmp_path / "photos" / "keep.jpg")
        card = ("BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Ada Lovelace\r\n"
                f"PHOTO;VALUE=URI:{kept}\r\n"
                "END:VCARD\r\n")
        uid = _out(_call(handle, "Create", "(s)", card))[0]
        assert store.get(uid).photo_path == kept
        assert _out(_call(handle, "Update", "(ss)", uid, card)) == ()
        assert store.get(uid).photo_path == kept
    finally:
        store.close()


def _test_jpeg_bytes(width: int = 64, height: int = 64) -> bytes:
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8,
                                  width, height)
    pixbuf.fill(0x336699ff)
    _ok, raw = pixbuf.save_to_bufferv("jpeg", ["quality"], ["90"])
    return bytes(raw)


def test_service_rebuilds_index_from_store(tmp_path):
    path = str(tmp_path / "c.db")
    store = ContactStore(path)
    data = ContactData(display_name="Ada Lovelace",
                       phones=[PhoneEntry(number_raw="+1 555 0100",
                                           number_e164="+15550100",
                                           number_digits="15550100")])
    uid = store.create(data, "64eebae7-ddee-4b13-8ff5-515fded82237")
    store.close()
    store2 = ContactStore(path)
    try:
        handle = ContactsService(store2, LookupIndex(), "US")
        assert handle.lookup_number("5550100")[0] == uid
        assert BUS_NAME == "io.piercingxx.Contacts"
    finally:
        store2.close()
