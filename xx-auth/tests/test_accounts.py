"""Account-flow tests. Translated from the `ReviewPassTest` add/list/
transfer/import cases plus `AccountListScreenTest` search and
`AccountDetailScreenTest` edit/delete/move behaviour."""
import os

import pytest

from xx_auth import accounts
from xx_auth.accounts import (
    AccountCard, add_error_message, add_from_text, add_manual,
    edit_account, filter_cards, import_error_message, import_vault_text,
    transfer_uri,
)
from xx_auth.keystore import SecretCipher
from xx_auth.otp_uri import parse as parse_otp
from xx_auth.store import VaultStore

SECRET = "JBSWY3DPEHPK3PXP"


@pytest.fixture()
def cipher():
    return SecretCipher(os.urandom(32))


@pytest.fixture()
def store():
    return VaultStore().load()


def test_paste_migration_adds_every_account(store, cipher):
    from xx_auth import base32
    import base64
    import urllib.parse

    def varint(out, value):
        part = value
        while part & ~0x7F:
            out.append((part & 0x7F) | 0x80)
            part >>= 7
        out.append(part)

    def blob(name, issuer):
        out = bytearray()
        for number, raw in ((1, base32.decode(SECRET)), (2, name.encode()), (3, issuer.encode())):
            varint(out, (number << 3) | 2)
            varint(out, len(raw))
            out += raw
        for number in (4, 5, 6):
            varint(out, number << 3)
            varint(out, 1 if number != 6 else 2)
        return bytes(out)

    payload = bytearray()
    for entry in (blob("x:one", "x"), blob("two", "y")):
        varint(payload, (1 << 3) | 2)
        varint(payload, len(entry))
        payload += entry
    uri = "otpauth-migration://offline?data=" + urllib.parse.quote(base64.b64encode(bytes(payload)).decode())
    result = add_from_text(store, uri, cipher)
    assert len(result.accounts) == 2
    assert [a.label for a in store.get_all()] == ["one", "two"]
    assert result.summary() == "Added 2 accounts."


def test_add_errors_are_plain_sentences(store, cipher):
    with pytest.raises(ValueError) as bad:
        add_from_text(store, "not a secret!", cipher)
    assert add_error_message(bad.value) == "That secret isn't valid Base32 (letters A–Z and digits 2–7)."
    with pytest.raises(ValueError) as period:
        add_from_text(store, f"otpauth://totp/a?secret={SECRET}&period=0", cipher)
    assert add_error_message(period.value).startswith("The period")


def test_unreadable_row_does_not_take_list_down(store, cipher):
    other = SecretCipher(os.urandom(32))
    add_from_text(store, f"otpauth://totp/Good:a?secret={SECRET}", cipher)
    add_from_text(store, f"otpauth://totp/Lost:b?secret={SECRET}", other)
    cards = accounts.account_cards(store, 59_000, cipher)
    assert len(cards) == 2
    assert not cards[0].unreadable
    assert cards[1].unreadable and cards[1].code == "------"


def test_search_matches_issuer_or_account():
    def card(cid, issuer, label):
        return AccountCard(cid, issuer, label, "000000", 1, 1.0)

    cards = [card("1", "GitHub", "alice"), card("2", "Proton", "bob@pm.me"), card("3", "", "GITlab-ci")]
    assert [c.id for c in filter_cards(cards, " git ")] == ["1", "3"]
    assert [c.id for c in filter_cards(cards, "PM.ME")] == ["2"]
    assert len(filter_cards(cards, None)) == 3
    assert len(filter_cards(cards, "")) == 3


def test_transfer_uri_round_trips(store, cipher):
    added = add_from_text(
        store, f"otpauth://hotp/Bank:me?secret={SECRET}&counter=42&digits=8&algorithm=SHA256", cipher,
    ).accounts[0]
    parsed = parse_otp(transfer_uri(store, added.id, cipher))
    assert (parsed.type, parsed.label, parsed.issuer, parsed.algorithm,
            parsed.digits, parsed.counter) == ("hotp", "me", "Bank", "SHA256", 8, 42)


def test_merge_keeps_existing(store, cipher):
    add_from_text(store, f"otpauth://totp/A:one?secret={SECRET}", cipher)
    imported, skipped = import_vault_text(
        store, f"otpauth://totp/A:one?secret={SECRET}\notpauth://totp/B:two?secret={SECRET}",
        None, cipher, merge=True,
    )
    assert (imported, skipped) == (1, 1)
    assert [a.label for a in store.get_all()] == ["one", "two"]


def test_replace_writes_ciphertext_only(store, cipher):
    from xx_auth import base32

    import_vault_text(store, f"otpauth://totp/A:one?secret={SECRET}", None, cipher)
    row = store.get_all()[0]
    assert row.secret != base32.decode(SECRET)
    assert cipher.decrypt(row.secret) == base32.decode(SECRET)


def test_malformed_entry_skipped_not_fatal(store, cipher):
    import json

    add_from_text(store, f"otpauth://totp/A:one?secret={SECRET}", cipher)
    text = accounts.export_vault_text(store, None, cipher)
    root = json.loads(text)
    root["db"]["entries"].insert(0, {"type": "totp", "info": {"secret": "!!"}})
    imported, skipped = import_vault_text(store, json.dumps(root), None, cipher)
    assert (imported, skipped) == (1, 1)


def test_wrong_password_plain_sentence(store, cipher):
    add_from_text(store, f"otpauth://totp/A:one?secret={SECRET}", cipher)
    text = accounts.export_vault_text(store, "right", cipher)
    with pytest.raises(ValueError) as wrong:
        import_vault_text(store, text, "wrong", cipher)
    assert import_error_message(wrong.value) == "Wrong password."
    imported, _ = import_vault_text(store, text, "right", cipher)
    assert imported == 1


def test_non_vault_rejected_plainly():
    with pytest.raises(ValueError) as bad:
        import_vault_text(VaultStore().load(), "hello world", None, SecretCipher(os.urandom(32)))
    assert import_error_message(bad.value) == "That isn't an Aegis vault file or a list of otpauth:// links."


def test_manual_entry_validates(store, cipher):
    account = add_manual(store, "Example", "alice", SECRET, False, "sha256", 8, 60, 0, cipher)
    assert (account.algorithm, account.digits, account.period, account.counter) == ("SHA256", 8, 60, None)
    with pytest.raises(ValueError):
        add_manual(store, "", "", "!!!", False, "SHA1", 6, 30, 0, cipher)
    with pytest.raises(ValueError):
        add_manual(store, "E", "a", SECRET, True, "SHA1", 6, 30, -1, cipher)


def test_edit_move_delete(store, cipher):
    first = add_from_text(store, f"otpauth://totp/A:one?secret={SECRET}", cipher).accounts[0]
    second = add_from_text(store, f"otpauth://totp/B:two?secret={SECRET}", cipher).accounts[0]
    edit_account(store, first.id, "A2", "one2", "Work")
    assert store.get_by_id(first.id).issuer == "A2"
    assert store.get_by_id(first.id).group == "Work"
    assert accounts.reorder_account(store, second.id, 0) == [second.id, first.id]
    assert store.get_all()[0].id == second.id
    accounts.delete_account(store, first.id)
    assert store.get_by_id(first.id) is None


def test_hotp_advance_only_on_purpose(store, cipher):
    hotp = add_from_text(store, f"otpauth://hotp/B:me?secret={SECRET}&counter=7", cipher).accounts[0]
    totp = add_from_text(store, f"otpauth://totp/A:me?secret={SECRET}", cipher).accounts[0]
    before = accounts.account_cards(store, 59_000, cipher)
    assert store.advance_counter(hotp.id).counter == 8
    assert store.advance_counter(totp.id) is None
    after = accounts.account_cards(store, 59_000, cipher)
    assert before[0].code != after[0].code
    assert store.get_by_id(hotp.id).counter == 8


def test_bare_secret_gets_secret_name(store, cipher):
    from xx_auth import base32

    account = add_from_text(store, "MZXW6YTBOI", cipher).accounts[0]
    assert account.label == "Secret MZXW"
    assert cipher.decrypt(account.secret) == base32.decode("MZXW6YTBOI")
