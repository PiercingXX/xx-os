"""Phones tests: normalization, E.164, lookup index rules (L1)."""

from xx_contacts.phones import (
    LookupIndex,
    digits_of,
    normalize,
    to_e164,
)


def test_digits_of():
    assert digits_of("+1 (555) 010-0") == "15550100"
    assert digits_of("") == ""
    assert digits_of(None) == ""


def test_e164_explicit_forms():
    assert to_e164("+1 555 0100") == "+15550100"
    assert to_e164("+44 20 7946 0958") == "+442079460958"
    assert to_e164("001 555 0100") == "+15550100"
    assert to_e164("+1 555 0100", default_region="FR") == "+15550100"


def test_e164_us_national_shapes():
    assert to_e164("5550100123") == "+15550100123"
    assert to_e164("15550100123") == "+15550100123"
    assert to_e164("(555) 010-0123") == "+15550100123"


def test_e164_unknown_is_none():
    assert to_e164("") is None
    assert to_e164(None) is None
    assert to_e164("abc") is None
    assert to_e164("12") is None
    assert to_e164("+" + "1" * 20) is None
    assert to_e164("06 12 34 56 78", default_region="FR") is None


def test_normalize_tuple():
    assert normalize("+1 555 0100") == ("15550100", "+15550100")


def _entry(uid, raw, region="US"):
    from xx_contacts.phones import normalize as norm

    digits, e164 = norm(raw, region)
    return (raw, e164, digits)


def _index():
    index = LookupIndex()
    index.upsert("uid-ada", "Ada Lovelace",
                 [_entry("u1", "+1 555 0100")], ("starred",))
    index.upsert("uid-grace", "Grace Hopper",
                 [_entry("u2", "+1 202 555 0100")], ())
    return index


def test_lookup_tail_match_and_tags():
    hit = _index().lookup("5550100")
    assert hit is not None
    assert (hit.uid, hit.display_name, hit.tags) == (
        "uid-ada", "Ada Lovelace", ("starred",))


def test_lookup_needs_seven_digits():
    index = _index()
    assert index.lookup("555010") is None
    assert index.lookup("") is None
    assert index.lookup("nonsense") is None


def test_exact_e164_beats_longer_suffix():
    index = LookupIndex()
    index.upsert("uid-short", "Short",
                 [_entry("s", "+1 555 0100")], ())
    index.upsert("uid-long", "Long",
                 [_entry("l", "+1 202 555 0100")], ())
    hit = index.lookup("+1 555 0100")
    assert hit is not None and hit.uid == "uid-short"


def test_whole_number_picks_the_right_area_code():
    index = LookupIndex()
    index.upsert("uid-a", "A", [_entry("a", "+1 202 555 0100")], ())
    index.upsert("uid-b", "B", [_entry("b", "+1 303 555 0100")], ())
    hit = index.lookup("1 202 555 0100")
    assert hit is not None and hit.uid == "uid-a"
    hit = index.lookup("(303) 555-0100")
    assert hit is not None and hit.uid == "uid-b"


def test_shared_last_seven_digits_is_another_person():
    index = LookupIndex()
    index.upsert("uid-cbus", "Columbus", [_entry("c", "+1 614 555 0142")], ())
    index.upsert("uid-local", "Local", [_entry("l", "555-0199")], ())
    assert index.lookup("312-555-0142") is None
    assert index.lookup("+13125550142") is None
    assert index.lookup("5550142") is None
    assert index.lookup("+1 614 555 0199") is None
    assert index.lookup("614.555.0142").uid == "uid-cbus"


def test_region_decides_national_numbers():
    index = LookupIndex("GB")
    index.upsert("uid-uk", "London", [_entry("u", "+44 20 7946 0958", "GB")],
                 ())
    index.upsert("uid-us", "Seattle", [_entry("s", "+1 206 794 0958", "GB")],
                 ())
    assert index.lookup("020 7946 0958").uid == "uid-uk"
    assert index.lookup("2067940958") is None


def test_tie_break_is_deterministic():
    index = LookupIndex()
    index.upsert("uid-b", "B", [_entry("x", "+1 555 0100")], ())
    index.upsert("uid-a", "A", [_entry("y", "+1 555 0100")], ())
    first = index.lookup("5550100")
    second = index.lookup("+1 555 0100")
    assert first is not None and second is not None
    assert first.uid == second.uid == "uid-a"


def test_incremental_upsert_and_remove():
    index = _index()
    assert index.lookup("5550100").uid == "uid-ada"
    index.upsert("uid-ada", "Ada Lovelace", [_entry("u1", "+1 555 9999")], ())
    assert index.lookup("5550100") is None
    assert index.lookup("2025550100").uid == "uid-grace"
    assert index.lookup("5559999").uid == "uid-ada"
    index.remove("uid-ada")
    assert index.lookup("5559999") is None
    assert index.lookup("2025550100").uid == "uid-grace"
    assert index.contact_count == 1


def test_rebuild_replaces_everything():
    index = _index()
    index.rebuild([("uid-z", "Zed", [_entry("z", "+1 555 0001")], ())])
    assert index.lookup("5550100") is None
    hit = index.lookup("5550001")
    assert hit is not None and hit.uid == "uid-z"
