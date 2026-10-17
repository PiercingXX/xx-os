"""Diagnostics block and redaction: no cookie, SAPISID hash or token survives."""

import json

from xx_music.diagnostics import ErrorRecord, collect, redact_mapping, redact_text


SID = "fake-SID-0001"
SAPISID = "fake-SAPISID-0002"
TOKEN = "ya29.fake-token-0003"
SAPISID_HASH = "SAPISIDHASH 1700000000_abcdef1234567890"


def test_redaction_masks_cookie_authorization_and_tokens():
    headers = {"cookie": f"SID={SID}; SAPISID={SAPISID}",
               "authorization": f"Bearer {TOKEN}",
               "x-goog-authuser": "0",
               "user-agent": "test-agent"}
    masked = redact_mapping(headers)
    assert masked["cookie"] == "***"
    assert masked["authorization"] == "***"
    assert masked["x-goog-authuser"] == "0"
    assert masked["user-agent"] == "test-agent"
    assert SID not in json.dumps(masked)


def test_redact_text_masks_every_secret_shape():
    line = (f"resolve with cookie: SID={SID}; SAPISID={SAPISID} "
            f"auth {SAPISID_HASH} bearer {TOKEN} "
            f"access_token={TOKEN} refresh_token={TOKEN} done")
    scrubbed = redact_text(line)
    assert SID not in scrubbed
    assert SAPISID not in scrubbed
    assert TOKEN not in scrubbed
    assert "abcdef1234567890" not in scrubbed
    assert "SAPISIDHASH" in scrubbed


def test_redact_text_masks_stored_credential_dicts():
    stored = json.dumps({"headers": {"cookie": f"SID={SID}; YSC=x"},
                         "user": "brand-1"})
    scrubbed = redact_text(stored)
    assert SID not in scrubbed
    assert "brand-1" in scrubbed


def test_redact_text_keeps_non_secrets():
    line = "x-goog-authuser: 0 user-agent: test-agent retry_after_s: 30"
    assert redact_text(line) == line
    assert redact_text("Playback helper missing") == "Playback helper missing"
    assert redact_text("token generator built") == "token generator built"


def test_redact_text_is_idempotent():
    line = ("cookie: SID=fake; SAPISID=x token=ya29.fake "
            "SAPISIDHASH 1700000000_abcdef done")
    once = redact_text(line)
    assert redact_text(once) == once


def test_collect_builds_one_redacted_block():
    text = collect(
        "Demo", "0.1.0",
        engine_lines=[f"demo 1.2.3 (in use) cookie={SID}"],
        capabilities={"search": "ok", "stream": "degraded"},
        errors=[ErrorRecord("2026-10-17T12:00:01+00:00", "search",
                            "source-changed"),
                ErrorRecord("2026-10-17T12:00:02+00:00", "stream", "timeout")],
        max_errors=20)
    assert text.startswith("Demo 0.1.0\n")
    assert "demo 1.2.3 (in use)" in text
    assert "search: ok" in text
    assert "2026-10-17T12:00:01+00:00 search source-changed" in text
    assert SID not in text
    assert TOKEN not in text


def test_collect_limits_errors_and_names_empty():
    errors = [ErrorRecord(f"2026-10-17T12:00:0{n}+00:00", "search", "timeout")
              for n in range(5)]
    text = collect("Demo", "0.1.0", errors=errors, max_errors=2)
    assert "12:00:03" in text
    assert "12:00:04" in text
    assert "12:00:00" not in text
    assert "(none)" not in text
    assert "(none)" in collect("Demo", "0.1.0")


def test_collect_redacts_tokens_in_versions_and_kinds():
    text = collect("Demo", "0.1.0",
                   engine_lines=[f"token {TOKEN}"],
                   capabilities={"search": f"ok {SAPISID_HASH}"})
    assert TOKEN not in text
    assert "abcdef1234567890" not in text


def test_diagnostics_and_applog_share_one_redaction():
    from xx_music import applog

    assert applog.redact_text is redact_text
