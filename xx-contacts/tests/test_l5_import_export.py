"""L5 import/export tests: fixtures, duplicates, backup, bulk, EDS.

Covers the task gates: ``contacts.vcf`` + ``groups.json`` round trips
(export, re-import, no duplicates, lossless), a 2,000-contact import
in one transaction emitting one ``Changed``, and the EDS import
against a fake EBook client.
"""

from pathlib import Path

import pytest
from gi.repository import Gio, GLib

from xx_contacts import eds as eds_module
from xx_contacts import import_export as import_export_module
from xx_contacts import vcard as vcard_module
from xx_contacts.app_config import AppConfig
from xx_contacts.phones import LookupIndex
from xx_contacts.service import (
    ERROR_INVALID_ARGUMENT,
    INTERFACE,
    OBJECT_PATH,
    ContactsService,
)
from xx_contacts.store import ContactData, ContactStore, PhoneEntry

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


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

    def emit_signal(self, destination, path, interface, signal,
                    parameters):
        self.signals.append((destination, path, interface, signal,
                             parameters))
        return True


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


def _mapped(record):
    return {
        "name": record.display_name,
        "given": record.given,
        "family": record.family,
        "phones": [(p.number_raw, p.type) for p in record.phones],
        "emails": [(e.address, e.type) for e in record.emails],
        "org": record.org,
        "title": record.title,
        "bday": record.birthday,
        "nick": record.nickname,
        "note": record.note,
        "groups": sorted(record.groups),
        "tags": (record.starred, record.business, record.blocked),
        "extra": sorted((e.prop, e.params, e.value, e.group)
                        for e in record.extra),
    }


def _backup_import(service):
    """Parse the fixtures, fold groups.json, Import: the UI's flow."""
    incoming = import_export_module.parse_text(_fixture("contacts.vcf"),
                                               "US")
    applied, unmapped = import_export_module.fold_groups_json(
        incoming, _fixture("groups.json"))
    created, updated, skipped = service.import_vcards(
        import_export_module.backup_to_text(incoming))
    return incoming, applied, unmapped, created, updated, skipped


# ------------------------------------------------------------- fixtures


def test_fixture_parses_mixed_versions_and_multi_cards():
    incoming = import_export_module.parse_text(_fixture("contacts.vcf"),
                                               "US")
    assert len(incoming) == 4
    by_name = {card.data.display_name: card for card in incoming
               if card.data.display_name}
    assert set(by_name) == {"Ada Lovelace", "Grace Hopper", "Alan Turing"}
    assert by_name["Ada Lovelace"].hint is None
    assert by_name["Ada Lovelace"].raw_uids == ["0r1-aaa111"]
    assert by_name["Grace Hopper"].raw_uids == ["0r1-bbb222"]
    alan = by_name["Alan Turing"]
    assert alan.hint == "64eebae7-ddee-4b13-8ff5-515fded82237"
    assert by_name["Ada Lovelace"].data.starred
    assert "Friends" in by_name["Ada Lovelace"].data.groups
    assert any(e.prop == "PHOTO" for e in
               by_name["Ada Lovelace"].data.extra)


def test_groups_fixture_folds_with_one_unmapped_member():
    incoming = import_export_module.parse_text(_fixture("contacts.vcf"),
                                               "US")
    applied, unmapped = import_export_module.fold_groups_json(
        incoming, _fixture("groups.json"))
    assert applied == 4
    assert unmapped == 1
    by_name = {card.data.display_name: card.data for card in incoming
               if card.data.display_name}
    assert "Friends" in by_name["Grace Hopper"].groups
    assert by_name["Grace Hopper"].starred
    assert by_name["Alan Turing"].blocked
    assert "Blocked" in by_name["Alan Turing"].groups


def test_groups_json_rejects_bad_shapes():
    with pytest.raises(ValueError):
        import_export_module.parse_groups_json("{not json")
    with pytest.raises(ValueError):
        import_export_module.parse_groups_json('{"title": "x"}')
    assert import_export_module.parse_groups_json("  ") == []


# ------------------------------------------------------------- duplicates


def test_plan_matches_uid_then_name_phone_then_creates():
    store = ContactStore(":memory:")
    try:
        ada = ContactData(
            display_name="Ada Lovelace",
            phones=[PhoneEntry(number_raw="+1 555 0100",
                               number_e164="+15550100",
                               number_digits="15550100")])
        ada_uid = store.create(
            ada, "64eebae7-ddee-4b13-8ff5-515fded82237")
        incoming = import_export_module.parse_text(
            "BEGIN:VCARD\r\nVERSION:4.0\r\n"
            f"UID:{ada_uid}\r\nFN:Ada Lovelace\r\n"
            "TEL;TYPE=CELL:+1 555 0100\r\nEND:VCARD\r\n"
            "BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Ada Lovelace\r\n"
            "TEL;TYPE=CELL:+1 555 0100\r\nEND:VCARD\r\n"
            "BEGIN:VCARD\r\nVERSION:4.0\r\nFN:New Person\r\n"
            "TEL;TYPE=CELL:+1 555 9999\r\nEND:VCARD\r\n"
            "BEGIN:VCARD\r\nVERSION:4.0\r\n"
            "NOTE:nothing identifiable\r\nEND:VCARD\r\n", "US")
        plan = import_export_module.plan_import(store.all_records(),
                                                incoming, "US")
        assert plan.update_count == 2
        assert {u.uid for u in plan.updates} == {ada_uid}
        assert plan.new_count == 1
        assert plan.skipped == 1
    finally:
        store.close()


def test_plan_name_match_requires_phone_too():
    store = ContactStore(":memory:")
    try:
        uid = store.create(ContactData(
            display_name="Ada Lovelace",
            phones=[PhoneEntry(number_raw="+1 555 0100",
                               number_e164="+15550100",
                               number_digits="15550100")]))
        incoming = import_export_module.parse_text(
            "BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Ada Lovelace\r\n"
            "TEL;TYPE=CELL:+1 555 9999\r\nEND:VCARD\r\n", "US")
        plan = import_export_module.plan_import(store.all_records(),
                                                incoming, "US")
        assert plan.new_count == 1
        assert plan.updates == []
        _ = uid
    finally:
        store.close()


def test_plan_collapses_within_file_duplicates():
    store = ContactStore(":memory:")
    try:
        text = ("BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Ada\r\n"
                "TEL:+1 555 0100\r\nEND:VCARD\r\n") * 2
        incoming = import_export_module.parse_text(text, "US")
        plan = import_export_module.plan_import(store.all_records(),
                                                incoming, "US")
        assert plan.new_count == 1
        assert plan.update_count == 1
    finally:
        store.close()


def test_summarize_lists_counts_before_writing():
    plan = import_export_module.ImportPlan(skipped=1)
    body = import_export_module.summarize(plan)
    assert "0 new" in body and "0 updates" in body and "1 skipped" in body


def test_counts_pluralise_by_count():
    plan = import_export_module.ImportPlan(updates=[object()])
    body = import_export_module.summarize(plan, groups_applied=1)
    assert "1 update," in body and "1 group membership." in body
    assert (import_export_module.exported_line(1, "/x.vcf")
            == "Exported 1 contact to /x.vcf.")
    assert "2 contacts" in import_export_module.exported_line(2, "/x.vcf")


# ------------------------------------------------------- backup round trip


def test_android_backup_import_remaps_uids_and_folds_groups(service):
    conn = service._connection
    incoming, applied, unmapped, created, updated, skipped = (
        _backup_import(service))
    assert (applied, unmapped) == (4, 1)
    assert (created, updated, skipped) == (3, 0, 1)
    assert len(conn.signals) == 1
    revision, uids = conn.signals[0][4].unpack()
    assert revision == 1 and sorted(uids) == sorted(
        service._store.all_uids())
    assert service._store.count() == 3
    records = {r.display_name: r for r in service._store.all_records()}
    assert records["Alan Turing"].uid == (
        "64eebae7-ddee-4b13-8ff5-515fded82237")
    assert records["Ada Lovelace"].uid != "0r1-aaa111"
    assert records["Ada Lovelace"].starred
    assert sorted(records["Ada Lovelace"].groups) == ["Friends"]
    assert records["Grace Hopper"].starred
    assert records["Alan Turing"].blocked
    assert "Blocked" in records["Alan Turing"].groups
    _ = incoming


def test_export_reimport_has_no_duplicates_and_is_lossless(service):
    _backup_import(service)
    before = {r.uid: _mapped(r) for r in service._store.all_records()}
    exported = import_export_module.export_text(
        service._store.all_records())
    assert exported.count("BEGIN:VCARD") == 3

    fresh = ContactStore(":memory:")
    try:
        second = ContactsService(fresh, LookupIndex(), "US")
        second._connection = FakeConnection()
        created, updated, skipped = second.import_vcards(exported)
        assert (created, updated, skipped) == (3, 0, 0)
        again = second.import_vcards(exported)
        assert again == (0, 3, 0)
        assert fresh.count() == 3
        after = {r.uid: _mapped(r) for r in fresh.all_records()}
        assert after == before
        assert import_export_module.export_groups_json(
            fresh.all_records()) == import_export_module.export_groups_json(
                service._store.all_records())
    finally:
        fresh.close()


# ------------------------------------------------------------- bulk Import


def test_bulk_import_uses_one_transaction_and_one_changed(service):
    cards = "".join(
        f"BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Person {index}\r\n"
        f"TEL;TYPE=CELL:+1 555 {1000 + index}\r\nEND:VCARD\r\n"
        for index in range(2000))
    before = service._store.get_revision()
    created, updated, skipped = service.import_vcards(cards)
    assert (created, updated, skipped) == (2000, 0, 0)
    assert service._store.get_revision() == before + 1
    assert service._store.count() == 2000
    assert len(service._connection.signals) == 1
    revision, uids = service._connection.signals[0][4].unpack()
    assert revision == before + 1
    assert len(uids) == 2000


def test_import_dispatch_replies_created_updated_skipped(service):
    card = ("BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Ada Lovelace\r\n"
            "TEL:+1 555 0100\r\nEND:VCARD\r\n")
    first = _call(service, "Import", "(s)", card)
    assert _out(first) == (1, 0, 0)
    second = _call(service, "Import", "(s)", card)
    assert _out(second) == (0, 1, 0)
    assert len(service._connection.signals) == 2


def test_import_rejects_input_without_cards(service):
    invocation = _call(service, "Import", "(s)", "FN:lonely line\r\n")
    assert invocation.error is not None
    assert invocation.error[0] == ERROR_INVALID_ARGUMENT


def test_introspection_lists_import_with_count_signatures():
    node = Gio.DBusNodeInfo.new_for_xml(
        __import__("xx_contacts.service",
                   fromlist=["INTROSPECTION_XML"]).INTROSPECTION_XML)
    methods = {m.name: m for m in node.interfaces[0].methods}
    assert "Import" in methods
    assert [a.signature for a in methods["Import"].in_args] == ["s"]
    assert [a.signature for a in methods["Import"].out_args] == [
        "u", "u", "u"]


# ------------------------------------------------------------------- EDS


def test_eds_import_is_read_only_against_fake_client(service):
    ada_card = ("BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Ada Lovelace\r\n"
                "TEL;TYPE=CELL:+1 555 0100\r\nEND:VCARD\r\n")
    fresh_card = ("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Eds Person\r\n"
                  "TEL;CELL:+1 555 7777\r\nEND:VCARD\r\n")
    service.import_vcards(ada_card)
    client = eds_module.FakeEBookClient(
        {"eds-1": ada_card, "eds-2": fresh_card})
    snapshot = (client.list_uids(), client.list_vcards())
    incoming = eds_module.read_import_cards(client, "US")
    assert len(incoming) == 2
    assert (client.list_uids(), client.list_vcards()) == snapshot
    created, updated, skipped = service.import_vcards(
        "".join(client.list_vcards()))
    assert (created, updated, skipped) == (1, 1, 0)


def test_eds_publish_mirrors_one_way_and_is_off_by_default(tmp_path):
    assert AppConfig().publish_to_eds is False
    cfg = AppConfig(publish_to_eds=True)
    path = str(tmp_path / "config.json")
    cfg.save(path)
    assert AppConfig.load(path).publish_to_eds is True
    assert AppConfig.load(str(tmp_path / "missing.json")
                          ).publish_to_eds is False

    store = ContactStore(":memory:")
    try:
        uid = store.create(ContactData(
            display_name="Ada Lovelace",
            phones=[PhoneEntry(number_raw="+1 555 0100",
                               number_e164="+15550100",
                               number_digits="15550100")]))
        stale_uid = "11111111-1111-4111-8111-111111111111"
        client = eds_module.FakeEBookClient({stale_uid: (
            "BEGIN:VCARD\r\nVERSION:4.0\r\n"
            f"UID:{stale_uid}\r\n"
            "FN:Stale\r\nEND:VCARD\r\n")})
        written, removed = eds_module.sync_to_eds(store.all_records(),
                                                  client)
        assert (written, removed) == (1, 1)
        assert client.list_uids() == [uid]
        assert "FN:Ada Lovelace" in client._cards[uid]
        store.delete(uid)
        written2, removed2 = eds_module.sync_to_eds(store.all_records(),
                                                    client)
        assert (written2, removed2) == (0, 1)
        assert client.list_uids() == []
    finally:
        store.close()


def test_eds_unavailable_without_typelibs():
    assert eds_module.is_available() is False
    with pytest.raises(RuntimeError, match="not installed"):
        eds_module.EBookClient()


def test_publisher_stays_quiet_when_disabled():
    calls = []

    def _make_client():
        calls.append(True)
        return eds_module.FakeEBookClient()

    publisher = eds_module.EdsPublisher(_make_client, list, enabled=False)
    publisher.schedule()
    assert calls == []


# ------------------------------------------------------- client + policy


def test_import_sync_targets_service_with_count_reply():
    from xx_contacts import service_client
    from xx_contacts.service import BUS_NAME, OBJECT_PATH

    class FakeReply:
        def unpack(self):
            return (3, 1, 2)

    class FakeBus:
        def __init__(self):
            self.calls = []

        def call_sync(self, bus_name, path, interface, method,
                      parameters, reply_type, flags, timeout,
                      cancellable):
            self.calls.append((bus_name, path, interface, method,
                               parameters.unpack() if parameters is not None
                               else ()))
            return FakeReply()

    conn = FakeBus()
    assert service_client.import_sync(conn, "cards") == (3, 1, 2)
    bus_name, path, iface, method, args = conn.calls[0]
    assert (bus_name, path, iface, method) == (
        BUS_NAME, OBJECT_PATH, INTERFACE, "Import")
    assert args == ("cards",)


def test_import_reply_type_is_variant_type_not_string():
    from xx_contacts import service_client

    seen = []

    class TypeSniffConnection:
        def call_sync(self, *args):
            seen.append(args[5])

            class FakeReply:
                def unpack(self):
                    return (0, 0, 0)

            return FakeReply()

    assert service_client.import_sync(TypeSniffConnection(), "x") == (0, 0,
                                                                      0)
    assert isinstance(seen[0], GLib.VariantType)
    assert seen[0].dup_string() == "(uuu)"


def test_gui_import_writes_only_through_the_service():
    from pathlib import Path as _Path

    src = (_Path(__file__).parent.parent / "src" / "xx_contacts"
           / "import_export_ui.py").read_text(encoding="utf-8")
    assert "service_client" in src
    assert "import_sync" in src
    assert "emit_changed" not in src
    assert "store.create" not in src
    assert "store.update" not in src
    assert ".delete(" not in src


def test_import_export_core_and_eds_do_no_store_writes():
    from pathlib import Path as _Path

    base = _Path(__file__).parent.parent / "src" / "xx_contacts"
    core = (base / "import_export.py").read_text(encoding="utf-8")
    assert "ContactStore" not in core
    assert ".create(" not in core
    eds = (base / "eds.py").read_text(encoding="utf-8")
    assert "ContactStore" not in eds


def test_new_ui_has_no_gobject_int_properties():
    from pathlib import Path as _Path

    src = (_Path(__file__).parent.parent / "src" / "xx_contacts"
           / "import_export_ui.py").read_text(encoding="utf-8")
    assert "GObject.Property(type=int)" not in src


def test_new_ui_uses_real_gtk_signals_and_portal_dialogs():
    import re
    from pathlib import Path as _Path

    from test_l4_policy import _gi_signal_names

    base = _Path(__file__).parent.parent / "src" / "xx_contacts"
    src = (base / "import_export_ui.py").read_text(encoding="utf-8")
    assert "Gtk.FileDialog" in src
    assert "FileChooserDialog" not in src
    assert "dialog.run(" not in src
    assert "get_filename" not in src
    known = _gi_signal_names()
    unknown = []
    for signal in re.findall(r"connect\(\"([^\"]+)\"", src):
        if signal.split("::")[0] not in known:
            unknown.append(signal)
    assert unknown == []
    assert "MessageDialog" not in src
    assert ".choose(self._parent" in src


def test_list_entry_point_opens_import_export_page():
    from pathlib import Path as _Path

    src = (_Path(__file__).parent.parent / "src" / "xx_contacts"
           / "list_ui.py").read_text(encoding="utf-8")
    assert "ImportExportPage" in src
    assert "Import/export" in src
    assert "arrives in L5" not in src


# ------------------------------------------------------- reference format


def _ref_path(*parts: str) -> Path | None:
    root = Path(__file__).parent.parent
    # Lane copies carry ref/; the real checkout keeps Android sources in
    # ../../android/.
    for base in (root / "ref", root.parent.parent / "android"):
        candidate = base.joinpath(*parts)
        if candidate.exists():
            return candidate
    return None


def test_android_codec_shape_parses_with_this_codec():
    codec = _ref_path("xx-contacts", "app", "src", "main", "java",
                      "com", "piercingxx", "xxcontacts", "backup",
                      "VCardCodec.kt")
    if codec is None:
        pytest.skip("no Android reference tree available")
    text = codec.read_text(encoding="utf-8")
    assert "PHOTO:data:image/jpeg;base64," in text
    sample = ("BEGIN:VCARD\r\nVERSION:4.0\r\nUID:0r1-9x\r\nFN:Ref Case\r\n"
              "N:Case;Ref;;;\r\nTEL;TYPE=CELL:+1 555 0303\r\n"
              "EMAIL;TYPE=HOME:ref@example.com\r\n"
              "ADR:;;9 Ref Lane;;;;\r\nNOTE:codec shape\r\n"
              "ORG:Ref\r\nTITLE:Case\r\nBDAY:2000-01-02\r\n"
              "NICKNAME:Ref\r\nX-STARRED:1\r\n"
              "PHOTO:data:image/jpeg;base64,/9j/AAA=\r\nEND:VCARD\r\n")
    data, _hint = vcard_module.contact_from_vcard(sample, "US")
    assert data.display_name == "Ref Case"
    assert [p.number_raw for p in data.phones] == ["+1 555 0303"]
    assert data.starred
    assert import_export_module.android_uids_of_data(data) == ["0r1-9x"]
