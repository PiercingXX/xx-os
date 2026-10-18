"""People controller tests: sections, sheets, tag writes, empty states."""
from xx_dialer.store import BIZ_TIER, BLOCKED_GROUP, MirrorRow, Store
from xx_dialer.ui_people import (
    PeopleController,
    PeopleDeps,
    person_marks,
    section_people,
)

NOW = 1_700_000_000_000


def _seed(store: Store) -> None:
    store.upsert_mirror([
        MirrorRow("uid-ada", "+14155550100", "Ada", True, None, False,
                  NOW),
        MirrorRow("uid-bo", "+14155550200", "Bo Dahl", False, None,
                  False, NOW),
        MirrorRow("uid-cy", "+14155550300", "Cy", False, None, False,
                  NOW),
        MirrorRow("uid-cy2", "+14155550301", "Cy", False, None, False,
                  NOW),
        MirrorRow("uid-zed", "+14155550400", "Zed", False, None, False,
                  NOW),
    ])
    store.add_star("uid-ada")
    store.upsert_tier("uid-bo", BIZ_TIER, NOW)
    store.upsert_tier("uid-cy", BLOCKED_GROUP, NOW)
    store.add_blocked("+14155550300")
    store.add_blocked("+14155550301")


class FakeClient:
    def __init__(self, present=True):
        self.is_present = present
        self.set_tags_calls: list[tuple[str, list[str]]] = []
        self.stars: dict[str, bool] = {}
        self.business: dict[str, bool] = {}
        self.blocked: dict[str, bool] = {}

    def set_star(self, uid: str, starred: bool) -> None:
        self.stars[uid] = starred
        self.set_tags_calls.append((uid, ["starred"] if starred else []))

    def set_business(self, uid: str, business: bool) -> None:
        self.business[uid] = business
        self.set_tags_calls.append(
            (uid, ["business"] if business else []))

    def set_blocked(self, uid: str, blocked: bool) -> list[str]:
        self.blocked[uid] = blocked
        self.set_tags_calls.append((uid, ["blocked"] if blocked else []))
        return []


def _controller(tmp_path, client=None, notices=None):
    notes = notices if notices is not None else []
    return PeopleController(PeopleDeps(
        db_file=tmp_path / "calls.db", home=tmp_path,
        contacts=client, notice=notes.append))


def test_sections_assign_each_contact_once(tmp_path, store):
    _seed(store)
    snapshot = _controller(tmp_path).load()
    by_title = dict(snapshot.sections)
    assert [person.uid for person in by_title["Starred"]] == ["uid-ada"]
    assert [person.uid for person in by_title["Business"]] == ["uid-bo"]
    assert [person.uid for person in by_title["Blocked"]] == ["uid-cy"]
    assert [person.uid for person in by_title["Everyone"]] == [
        "uid-cy2", "uid-zed"]
    seen = [person.uid for _title, people in snapshot.sections
            for person in people]
    assert sorted(seen) == ["uid-ada", "uid-bo", "uid-cy", "uid-cy2",
                            "uid-zed"]


def test_blocked_beats_starred_for_section(tmp_path, store):
    _seed(store)
    store.add_star("uid-cy")
    snapshot = _controller(tmp_path).load()
    by_title = dict(snapshot.sections)
    assert [person.uid for person in by_title["Blocked"]] == ["uid-cy"]
    assert all(person.uid != "uid-cy" for person in by_title["Starred"])


def test_person_marks():
    from xx_dialer.ui_people import Person

    assert person_marks(Person("u", "A", ["+1"], True, True, False)) == \
        "* Biz"
    assert person_marks(Person("u", "A", ["+1"])) == ""
    # A row never repeats its section heading.
    assert person_marks(Person("u", "A", ["+1"], True, True, False),
                        "Starred") == "Biz"
    assert person_marks(Person("u", "A", ["+1"], False, True, False),
                        "Business") == ""
    assert person_marks(Person("u", "A", ["+1"], True, False, True),
                        "Blocked") == "*"


def _zed(snapshot):
    for person in dict(snapshot.sections)["Everyone"]:
        if person.uid == "uid-zed":
            return person
    raise AssertionError("uid-zed missing from Everyone")


def test_star_goes_through_contacts_client(tmp_path, store):
    _seed(store)
    client = FakeClient()
    controller = _controller(tmp_path, client)
    person = _zed(controller.load())
    controller.set_star(person, True)
    assert client.stars == {"uid-zed": True}


def test_business_and_block_go_through_client(tmp_path, store):
    _seed(store)
    client = FakeClient()
    controller = _controller(tmp_path, client)
    snapshot = controller.load()
    zed = _zed(snapshot)
    controller.set_business(zed, True)
    controller.set_blocked(zed, True)
    assert client.business == {"uid-zed": True}
    assert client.blocked == {"uid-zed": True}


def test_offline_edits_fall_back_to_local_tables(tmp_path, store):
    _seed(store)
    controller = _controller(tmp_path, None)
    snapshot = controller.load()
    assert snapshot.contacts_present is False
    zed = _zed(snapshot)
    controller.set_star(zed, True)
    controller.set_business(zed, True)
    controller.set_blocked(zed, True)
    with Store(tmp_path / "calls.db") as check:
        assert check.is_starred("uid-zed")
        assert "uid-zed" in check.keys_for_tier(BIZ_TIER)
        assert "uid-zed" in check.keys_for_tier(BLOCKED_GROUP)
        assert check.is_blocked("+14155550400")


def test_stranger_block_lands_in_local_block_table(tmp_path, store):
    _seed(store)
    notices: list[str] = []
    controller = _controller(tmp_path, None, notices)
    controller.block_number("+14155559999")
    with Store(tmp_path / "calls.db") as check:
        assert check.is_blocked("+14155559999")
    # The row moving into Blocked is the answer: no toast.
    assert notices == []


def test_empty_states_name_the_cause(tmp_path, store):
    snapshot = _controller(tmp_path, None).load()
    assert snapshot.empty_reason.startswith("Contacts are unavailable")
    snapshot = _controller(tmp_path, FakeClient()).load()
    assert snapshot.empty_reason == ""


def test_section_people_is_pure_over_store(tmp_path, store):
    _seed(store)
    sections = section_people(store)
    assert [title for title, _people in sections] == [
        "Starred", "Business", "Blocked", "Everyone"]
    cy = dict(sections)["Blocked"][0]
    assert cy.numbers == ["+14155550300"]
