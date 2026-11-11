"""Port of Android's PhoneDisplayTest (contacts, dialer core, TxxT) plus the
country-lookup precedence tests, driven against the real libphonenumber
engine (the Python ``phonenumbers`` package — the same engine the dialer
variant of the Android rule runs on).

Needs ``phonenumbers`` installed (``py3-phonenumbers`` on pmOS/Alpine,
``python-phonenumbers`` on Arch); skips otherwise. The no-library
fallback has its own test below and always runs.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

phonenumbers = pytest.importorskip("phonenumbers")

import xx_phone  # noqa: E402


def us(raw):
    return xx_phone.display(raw, "US")


def test_us_national_number_reads_nationally():
    assert us("6145550142") == "(614) 555-0142"


def test_us_number_with_country_code_reads_nationally():
    assert us("+16145550142") == "(614) 555-0142"
    assert us("+1 614 555 0101") == "(614) 555-0101"


def test_dialer_reference_number_reads_nationally():
    assert us("+12065550133") == "(206) 555-0133"
    assert us("2065550133") == "(206) 555-0133"


def test_uk_number_keeps_its_country_code():
    assert us("+442079460958") == "+44 20 7946 0958"
    assert us("011 44 20 7946 0958") == "+44 20 7946 0958"


def test_home_country_follows_the_user():
    assert xx_phone.display("+442079460958", "GB") == "020 7946 0958"
    assert xx_phone.display("+16145550142", "gb") == "+1 614-555-0142"


def test_short_code_is_shown_as_is():
    assert us("22395") == "22395"


def test_emergency_number_is_shown_as_is():
    assert us("911") == "911"


def test_alphanumeric_sender_is_shown_as_is():
    assert us("AMAZON") == "AMAZON"
    assert us("*86") == "*86"
    assert us("alerts@example.com") == "alerts@example.com"


def test_spaces_and_dashes_are_regrouped():
    assert us("614-555-0142") == "(614) 555-0142"
    assert us(" 614 555 0142 ") == "(614) 555-0142"
    assert us("(614)555-0142") == "(614) 555-0142"


def test_empty_input_stays_empty():
    assert us("") == ""
    assert us("   ") == ""
    assert us(None) == ""


def test_seven_digit_local_number_gets_local_grouping():
    assert us("5550142") == "555-0142"


def test_unknown_country_still_formats_international_numbers():
    assert xx_phone.display("+16145550142", "") == "+1 614-555-0142"
    assert xx_phone.display("6145550142", "") == "6145550142"


def test_a_broken_library_leaves_the_number_alone(monkeypatch):
    def _boom(number, iso):
        raise RuntimeError("no telephony")

    monkeypatch.setattr(xx_phone, "_to_e164", _boom)
    monkeypatch.setattr(xx_phone, "_group", _boom)
    assert us("+16145550199") == "+16145550199"


def test_country_comes_from_network_then_sim_then_locale():
    assert xx_phone.country_iso("us", "ca", "FR") == "US"
    assert xx_phone.country_iso("", "ca", "FR") == "CA"
    assert xx_phone.country_iso(None, " ", "FR") == "FR"
    assert xx_phone.country_iso(None, None, "") == ""


def test_country_iso_skips_blanks_and_malformed():
    assert xx_phone.country_iso("  ", "12", "U1") == ""
    assert xx_phone.country_iso("USA", "ca", None) == "CA"
    assert xx_phone.country_iso(None, None, "de") == "DE"


def test_keypad_groups_as_you_type():
    assert xx_phone.as_you_type("614555", "US") == "614-555"
    assert xx_phone.as_you_type("6145550142", "US") == "(614) 555-0142"
    assert xx_phone.as_you_type("*#06#", "US") == "*#06#"
    assert xx_phone.as_you_type("", "US") == ""


def test_user_country_prefers_network_over_sim_over_locale():
    assert xx_phone.user_country("310260", "26201",
                                 {"LANG": "en_GB.UTF-8"}) == "US"
    assert xx_phone.user_country("", "26201",
                                 {"LANG": "en_GB.UTF-8"}) == "DE"
    assert xx_phone.user_country("", "",
                                 {"LANG": "en_GB.UTF-8"}) == "GB"


def test_user_country_reads_sim_mcc():
    assert xx_phone.user_country("", "302720", {"LANG": "C"}) == "CA"


def test_user_country_unknown_mcc_falls_through():
    assert xx_phone.user_country("999999", None,
                                 {"LANG": "fr_FR.UTF-8"}) == "FR"
    assert xx_phone.user_country("999999", "000000", {"LANG": "C"}) == ""


def test_user_country_locale_kinds():
    assert xx_phone.user_country(None, None, {"LC_ALL": "de_DE@euro"}) == "DE"
    assert xx_phone.user_country(None, None, {"LC_MESSAGES": "es_MX.UTF-8",
                                              "LANG": "en_US.UTF-8"}) == "MX"
    assert xx_phone.user_country(None, None, {}) == ""
    assert xx_phone.user_country(None, None, {"LANG": "POSIX"}) == ""


def test_user_country_caches_about_sixty_seconds(monkeypatch):
    now = [1_000.0]
    monkeypatch.setattr(xx_phone, "_auto_sources",
                        lambda: ("310260", None))
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("LC_MESSAGES", "C")
    monkeypatch.setenv("LANG", "C")
    assert xx_phone.user_country(_now=lambda: now[0]) == "US"
    monkeypatch.setattr(xx_phone, "_auto_sources",
                        lambda: ("999999", None))
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    now[0] += 59.0
    assert xx_phone.user_country(_now=lambda: now[0]) == "US"
    now[0] += 2.0
    assert xx_phone.user_country(_now=lambda: now[0]) == "FR"


def test_mcc_table_only_names_regions_the_library_knows():
    known = set(phonenumbers.SUPPORTED_REGIONS)
    for mcc, iso in xx_phone.mcc_to_iso().items():
        assert len(mcc) == 3 and mcc.isdigit(), mcc
        assert iso in known, (mcc, iso)
    table = xx_phone.mcc_to_iso()
    assert table["310"] == "US"
    assert table["302"] == "CA"
    assert table["234"] == "GB"
    assert table["262"] == "DE"
    assert table["208"] == "FR"
    assert table["214"] == "ES"
    assert table["222"] == "IT"
    assert table["404"] == "IN"
    assert table["505"] == "AU"
    assert table["724"] == "BR"
    assert table["334"] == "MX"


def test_display_never_returns_blank_for_a_number():
    assert xx_phone.display("+16145550142", "US")
    assert xx_phone.display("22395", "US")


def test_display_country_prefers_live_country_then_fallback(monkeypatch):
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("LC_MESSAGES", "C")
    monkeypatch.setenv("LANG", "C")
    monkeypatch.setattr(xx_phone, "_auto_sources", lambda: ("310260", None))
    monkeypatch.setattr(xx_phone, "_auto_country", None)
    assert xx_phone.display_country("DE") == "US"
    monkeypatch.setattr(xx_phone, "_auto_sources", lambda: (None, None))
    monkeypatch.setattr(xx_phone, "_auto_country", None)
    assert xx_phone.display_country("DE") == "DE"
    monkeypatch.setattr(xx_phone, "_auto_country", None)
    assert xx_phone.display_country(None) == ""
    monkeypatch.setattr(xx_phone, "_auto_country", None)
    assert xx_phone.display_country("") == ""


# -- numbers_match ----------------------------------------------------------


def test_same_number_written_differently_matches():
    assert xx_phone.numbers_match("+16145550142", "(614) 555-0142", "US")
    assert xx_phone.numbers_match("1 614 555 0142", "+1 614-555-0142", "US")
    assert xx_phone.numbers_match("+442079460958", "020 7946 0958", "GB")
    assert xx_phone.numbers_match("+442079460958", "0044 20 7946 0958", "GB")


def test_cross_area_code_numbers_sharing_seven_digits_never_match():
    assert not xx_phone.numbers_match("6145550142", "3125550142", "US")
    assert not xx_phone.numbers_match("+16145550142", "+13125550142", "US")
    assert not xx_phone.numbers_match("+16145550142", "(312) 555-0142", "")


def test_a_bare_seven_digit_tail_never_matches_a_full_number():
    assert not xx_phone.numbers_match("5550142", "+16145550142", "US")
    assert not xx_phone.numbers_match("+16145550142", "555-0142", "US")


def test_national_number_matches_only_in_its_own_country():
    assert not xx_phone.numbers_match("+442079460958", "02079460958", "US")
    assert not xx_phone.numbers_match("+16145550142", "+446145550142", "US")


def test_unknown_country_still_matches_a_country_coded_form():
    assert xx_phone.numbers_match("+16145550142", "6145550142", "")


def test_short_codes_match_only_the_same_digits():
    assert xx_phone.numbers_match("22395", "22395", "US")
    assert not xx_phone.numbers_match("911", "6145550911", "US")
    assert not xx_phone.numbers_match("40404", "+16145540404", "US")


def test_alphanumeric_senders_and_codes_match_by_text():
    assert xx_phone.numbers_match("AMAZON", "amazon", "US")
    assert xx_phone.numbers_match("*86", "*86", "US")
    assert not xx_phone.numbers_match("AMAZON", "VERIFY", "US")
    assert not xx_phone.numbers_match("AMAZON", "6145550142", "US")
    assert not xx_phone.numbers_match("", "", "US")
    assert not xx_phone.numbers_match(None, "6145550142", "US")


def test_matching_without_the_library_keeps_a_safe_rule(monkeypatch):
    monkeypatch.setattr(xx_phone, "_phonenumbers", None)
    monkeypatch.setattr(xx_phone, "_phonenumbers_tried", True)
    assert xx_phone.numbers_match("+16145550142", "(614) 555-0142", "US")
    assert xx_phone.numbers_match("+442079460958", "020 7946 0958", "GB")
    assert not xx_phone.numbers_match("6145550142", "3125550142", "US")
    assert not xx_phone.numbers_match("5550142", "+16145550142", "US")
    assert not xx_phone.numbers_match("+16145550142", "+446145550142", "US")


def test_matching_never_blocks_on_the_modem(monkeypatch):
    def _no_bus():
        raise AssertionError("numbers_match must not ask ModemManager")

    monkeypatch.setattr(xx_phone, "_auto_sources", _no_bus)
    monkeypatch.setattr(xx_phone, "_auto_country", None)
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    assert xx_phone.numbers_match("6145550142", "+16145550142")


def test_dtmf_tone_accepts_only_the_sixteen_tones():
    for tone in "0123456789*#ABCD":
        assert xx_phone.dtmf_tone(tone) == tone
    assert xx_phone.dtmf_tone(" 5 ") == "5"
    assert xx_phone.dtmf_tone("d") == "D"
    for bad in ("+", ",", "p", "w", "E", "x", "12", "", " ", None, 5):
        assert xx_phone.dtmf_tone(bad) is None, bad
