"""xxdb pairing + trust store: HMAC accept/reject/burn, expiry, 0600."""
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import xxdb

PHONE_FP = 'ab' * 32
HOST_FP = 'cd' * 32


@pytest.fixture
def session():
    return xxdb.PairingSession(clock=lambda: 1000.0)


def _mac(code, fp=PHONE_FP, host=HOST_FP):
    return xxdb.pairing_mac(code, fp, host)


def test_pairing_accept(session):
    code, _expires = session.issue(now=1000.0)
    ok, _ = session.attempt(_mac(code), PHONE_FP, HOST_FP, now=1001.0)
    assert ok is True


def test_pairing_reject_wrong_code(session):
    code, _ = session.issue(now=1000.0)
    wrong = '000000' if code != '000000' else '111111'
    ok, reason = session.attempt(_mac(wrong), PHONE_FP, HOST_FP, now=1001.0)
    assert ok is False
    assert reason == 'wrong pairing code'
    assert session.tries == 1
    ok, _ = session.attempt(_mac(code), PHONE_FP, HOST_FP, now=1001.0)
    assert ok is True


def test_pairing_reject_wrong_mac(session):
    session.issue(now=1000.0)
    ok, _ = session.attempt('00' * 32, PHONE_FP, HOST_FP, now=1001.0)
    assert ok is False


def test_pairing_binds_phone_fingerprint(session):
    """A man-in-the-middle's certificate: the host's HMAC was computed over
    the fingerprint it saw, which is not the phone's."""
    code, _ = session.issue(now=1000.0)
    ok, _ = session.attempt(_mac(code, fp='ee' * 32), PHONE_FP, HOST_FP,
                            now=1001.0)
    assert ok is False


def test_pairing_binds_host_cert(session):
    code, _ = session.issue(now=1000.0)
    ok, _ = session.attempt(_mac(code, host='ee' * 32), PHONE_FP, HOST_FP,
                            now=1001.0)
    assert ok is False


def test_pairing_key_derives_from_code_and_both_fingerprints():
    base = xxdb.pairing_key('123456', PHONE_FP, HOST_FP)
    assert base != xxdb.pairing_key('123457', PHONE_FP, HOST_FP)
    assert base != xxdb.pairing_key('123456', 'ee' * 32, HOST_FP)
    assert base != xxdb.pairing_key('123456', PHONE_FP, 'ee' * 32)


def test_pairing_burned_after_three_misses(session):
    session.issue(now=1000.0)
    assert session.attempt(_mac('1'), PHONE_FP, HOST_FP, now=1001.0)[1] \
        == 'wrong pairing code'
    assert session.attempt(_mac('2'), PHONE_FP, HOST_FP, now=1001.0)[1] \
        == 'wrong pairing code'
    ok, reason = session.attempt(_mac('3'), PHONE_FP, HOST_FP, now=1001.0)
    assert ok is False
    assert reason == 'pairing code burned'
    ok, reason = session.attempt(_mac('3'), PHONE_FP, HOST_FP, now=1001.0)
    assert ok is False
    assert 'no active' in reason


def test_pairing_single_use(session):
    code, _ = session.issue(now=1000.0)
    assert session.attempt(_mac(code), PHONE_FP, HOST_FP,
                           now=1001.0)[0] is True
    ok, reason = session.attempt(_mac(code), PHONE_FP, HOST_FP, now=1001.0)
    assert ok is False
    assert 'no active' in reason


def test_pairing_expires(session):
    code, _ = session.issue(now=1000.0)
    ok, reason = session.attempt(_mac(code), PHONE_FP, HOST_FP,
                                 now=1000.0 + xxdb.PAIR_CODE_TTL_S + 1)
    assert ok is False
    assert reason == 'pairing code expired'


def test_pairing_code_format():
    for _ in range(25):
        code = xxdb.new_pairing_code()
        assert len(code) == 6 and code.isdigit()


def test_fingerprint_prefix_shown_with_the_code():
    fp = '0123456789abcdef' + 'f' * 48
    assert xxdb.fingerprint_prefix(fp) == '0123 4567 89ab cdef'
    assert xxdb.normalize_fingerprint('0123 4567:89AB-cdef') == \
        '0123456789abcdef'


def test_trust_store_roundtrip_and_perms(tmp_path):
    store = xxdb.TrustStore(path=tmp_path / 'trusted.json')
    assert store.list() == []
    store.add('ab' * 32, 'laptop', 'PEM', now=1000.0)
    assert store.contains('ab' * 32)
    assert store.name_of('ab' * 32) == 'laptop'
    mode = stat.S_IMODE(os.stat(tmp_path / 'trusted.json').st_mode)
    assert mode == 0o600
    again = xxdb.TrustStore(path=tmp_path / 'trusted.json')
    assert again.contains('ab' * 32)
    assert again.list()[0]['name'] == 'laptop'
    assert store.remove('ab' * 32) is True
    assert store.remove('ab' * 32) is False
    assert store.list() == []


def test_trust_expires_after_max_age_since_last_use(tmp_path):
    now = [1000.0]
    day = 86400.0
    store = xxdb.TrustStore(path=tmp_path / 'trusted.json',
                            max_age_s=7 * day, clock=lambda: now[0])
    store.add('ab' * 32, 'laptop', 'PEM', now=now[0])
    now[0] += 6 * day
    assert store.contains('ab' * 32)
    store.touch('ab' * 32)
    now[0] += 6 * day
    assert store.contains('ab' * 32), 'a use restarts the clock'
    now[0] += 2 * day
    assert not store.contains('ab' * 32)
    assert store.list() == []
    again = xxdb.TrustStore(path=tmp_path / 'trusted.json',
                            max_age_s=7 * day, clock=lambda: now[0])
    assert again.list() == [], 'an expired host is dropped from the file'


def test_trust_max_age_is_clamped():
    assert xxdb.trust_max_age_s(7) == 7 * 86400
    assert xxdb.trust_max_age_s(0) == 86400
    assert xxdb.trust_max_age_s(99999) == xxdb.MAX_TRUST_DAYS * 86400
    assert xxdb.trust_max_age_s('junk') == xxdb.DEFAULT_TRUST_DAYS * 86400


def test_trust_store_rejects_malformed(tmp_path):
    path = tmp_path / 'trusted.json'
    path.write_text('{"hosts": [{"fingerprint": "zzz", "name": "x"}]}')
    store = xxdb.TrustStore(path=path)
    assert store.list() == []
    path.write_text('not json{{')
    store = xxdb.TrustStore(path=path)
    assert store.list() == []


def test_fingerprint_is_sha256_hex():
    assert xxdb.cert_fingerprint(b'abc') \
        == 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
