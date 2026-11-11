"""Port of core/PhoneNumbersTest.kt.

Locks the format-tolerant sender comparison: every variant a carrier or
contact book might produce for one number matches; short codes never
suffix-match; email gateways match exactly and never cross-match numbers.
"""

from txxt.phone_numbers import conversation_key, matches, normalize


def test_identical_formats_normalize_to_their_digits():
    assert normalize("+15551234567") == "15551234567"
    assert normalize("  +15551234567  ") == "15551234567"
    assert normalize("+15551234567".upper()) == "15551234567"
    assert normalize("+1 555 1234567") == normalize("+15551234567")


def test_punctuation_variants_strip_to_digits():
    assert normalize("555-123-4567") == "5551234567"
    assert normalize("(555) 123 4567") == "5551234567"
    assert normalize("(555)-123.4567") == "5551234567"


def test_email_addresses_keep_verbatim_trimmed_lowercase_form():
    assert normalize(" Alice@Carrier.Example.Com ") == "alice@carrier.example.com"


def test_garbage_without_digits_or_at_sign_normalizes_to_empty():
    assert normalize("no-digits-here") == ""


def test_identical_formats_match():
    assert matches("+15551234567", "+15551234567")


def test_plus_one_matches_bare_ten_digit_number():
    assert matches("+15551234567", "5551234567")
    assert matches("5551234567", "+15551234567")


def test_punctuation_variants_all_refer_to_one_number():
    variants = [
        "+15551234567",
        "555-123-4567",
        "(555) 123 4567",
        "+1 555 1234567",
        "  +1 (555) 123-4567 ",
    ]
    for a in variants:
        for b in variants:
            assert matches(a, b), f"{a} vs {b} must match"


def test_matching_is_case_insensitive_and_trims_whitespace():
    assert matches("  +1 555 1234 ", "+15551234")


def test_country_code_suffix_matches_in_both_directions():
    assert matches("+15551234567", "5551234567")
    assert matches("5551234567", "+15551234567")


def test_different_numbers_do_not_match_even_with_shared_suffix_digits():
    assert not matches("+15551234567", "+15551299999")
    assert not matches("5551234567", "5551234568")


def test_short_codes_below_seven_digits_are_not_suffix_matched():
    assert not matches("911", "5551234911")
    assert not matches("40404", "155512340404")
    assert not matches("123456", "+1555123123456")


def test_a_bare_seven_digit_tail_is_a_different_sender():
    assert not matches("+15551234567", "1234567")
    assert not matches("1234567", "+15551234567")
    assert not matches("+15551234567", "234567")


def test_other_area_code_sharing_seven_digits_is_a_different_sender():
    assert not matches("+16145550142", "+13125550142")
    assert not matches("(614) 555-0142", "312-555-0142")
    assert matches("(614) 555-0142", "+1 614 555 0142")


def test_region_reads_the_national_side():
    assert matches("+442079460958", "020 7946 0958", region="GB")
    assert not matches("+442079460958", "020 7946 0958", region="US")


def test_email_addresses_match_exactly_ignoring_case():
    assert matches("Alice@Carrier.Example.COM", "alice@carrier.example.com")


def test_different_email_addresses_do_not_match():
    assert not matches("alice@carrier.example.com", "bob@carrier.example.com")
    assert not matches("alice@carrier.example.com", "alice@other.example.com")


def test_an_email_address_never_matches_a_phone_number():
    assert not matches("5551234567@carrier.example.com", "5551234567")
    assert not matches("alice@example.com", "1234567890")


def test_garbage_without_identity_never_matches_anything():
    assert not matches("!!!", "???")
    assert not matches("!!!", "!!!")
    assert not matches("", "")
    assert not matches("!!!", "5551234567")


def test_alphanumeric_senders_key_on_the_trimmed_lowercase_token():
    assert conversation_key("VERIFY") == "verify"
    assert conversation_key("AMAZON") == "amazon"
    assert conversation_key("T-Mobile") == "t-mobile"
    assert conversation_key("!!!") == ""


def test_alphanumeric_senders_match_case_insensitively_and_stay_distinct():
    assert matches("VERIFY", "verify")
    assert matches("T-Mobile", "t-mobile")
    assert not matches("VERIFY", "AMAZON")
    assert not matches("VERIFY", "5551234567")
