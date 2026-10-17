"""Rotating redacted log file: secrets never reach the disk."""

import logging
import os
import stat

from xx_music import applog
from xx_music.diagnostics import redact_text


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _handlers(logger):
    return list(logger.handlers)


def test_log_redacts_secrets_and_creates_dirs_0700(tmp_path):
    path = tmp_path / "new-state" / "xx-radio.log"
    logger = applog.setup(path, logger_name="test-applog-basic")
    logger.warning("resolve with cookie: SID=fake-SID-0001; SAPISID=x "
                   "token=ya29.fake SAPISIDHASH 1700000000_abcdef ok")
    for handler in logger.handlers:
        handler.flush()
    text = _read(path)
    assert "fake-SID-0001" not in text
    assert "ya29.fake" not in text
    assert "abcdef" not in text
    assert "SAPISIDHASH" in text
    assert text == redact_text(text)
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_log_rotates_and_setup_is_idempotent(tmp_path):
    path = tmp_path / "xx-radio.log"
    first = applog.setup(path, max_bytes=200, backups=1,
                         logger_name="test-applog-rotate")
    for number in range(30):
        first.warning("line %d padding padding padding padding", number)
    for handler in _handlers(first):
        handler.flush()
    assert (tmp_path / "xx-radio.log.1").exists()
    second = applog.setup(path, max_bytes=200, backups=1,
                          logger_name="test-applog-rotate")
    assert second is first
    assert sum(1 for handler in first.handlers
               if getattr(handler, "baseFilename", "") == str(path)) == 1


def test_default_path_uses_the_state_dir():
    assert str(applog.default_path("xx-radio")).endswith(
        "xx-radio/xx-radio.log")


def test_existing_parent_keeps_its_permissions(tmp_path):
    existing = tmp_path / "state"
    existing.mkdir()
    os.chmod(existing, 0o755)
    applog.setup(existing / "xx-radio.log",
                 logger_name="test-applog-perms")
    assert stat.S_IMODE(existing.stat().st_mode) == 0o755
    assert logging.getLogger("test-applog-perms").handlers
