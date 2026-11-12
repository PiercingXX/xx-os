"""ShellConfig durability + PIN hardening: atomic saves (crash-safe replace,
0600 modes) and PBKDF2 pin storage with transparent legacy-sha256 upgrade.
Pure logic, no GTK imports."""
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from config import ShellConfig


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    # ShellConfig() reads ~/.config/xx-wm/config.json in its constructor;
    # point HOME at a temp dir so the dev box's real shell config never
    # leaks into tests.
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))


def _config(tmp_path):
    config = ShellConfig()
    config.config_dir = tmp_path / 'cfg'
    config.config_path = config.config_dir / 'config.json'
    return config


class TestAtomicSave:
    def test_crash_mid_save_leaves_previous_config_intact(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        config.set_pin('123456')
        before = json.loads(config.config_path.read_text(encoding='utf-8'))
        assert before['pin_hash'].startswith('pbkdf2$')

        def crash(*_args, **_kwargs):
            raise OSError('simulated crash mid-write')

        monkeypatch.setattr(os, 'fsync', crash)
        config.data['theme'] = 'ocean'
        with pytest.raises(OSError):
            config.save()

        after = json.loads(config.config_path.read_text(encoding='utf-8'))
        assert after == before
        assert list(config.config_dir.glob('*.tmp')) == []

    def test_saved_file_mode_is_0600(self, tmp_path):
        config = _config(tmp_path)
        config.set_pin('123456')
        mode = stat.S_IMODE(os.stat(config.config_path).st_mode)
        assert mode == 0o600


class TestSkipUnchangedSave:
    """eMMC wear guard: save() must not touch the flash when the serialized
    payload is byte-identical to what's on disk; a changed payload still
    writes."""

    @pytest.fixture
    def _replace_spy(self, monkeypatch):
        """Count os.replace calls (one per real disk write) while passing
        them through to the real filesystem."""
        calls: list[tuple] = []
        real_replace = os.replace

        def _spy(src, dst, **kwargs):
            calls.append((src, dst))
            return real_replace(src, dst, **kwargs)

        monkeypatch.setattr(os, 'replace', _spy)
        return calls

    def test_second_identical_save_performs_no_write(self, tmp_path, _replace_spy):
        config = _config(tmp_path)
        config.data['theme'] = 'ocean'
        config.save()
        assert len(_replace_spy) == 1  # baseline: first save writes
        on_disk = config.config_path.read_text(encoding='utf-8')

        config.save()  # no mutation since the last save
        assert len(_replace_spy) == 1  # skipped: no second write
        assert config.config_path.read_text(encoding='utf-8') == on_disk
        assert list(config.config_dir.glob('*.tmp')) == []  # no orphan tmp

    def test_mutated_payload_still_writes(self, tmp_path, _replace_spy):
        config = _config(tmp_path)
        config.save()
        assert len(_replace_spy) == 1

        config.data['theme'] = 'forest'
        config.save()
        assert len(_replace_spy) == 2

    def test_externally_edited_file_is_rewritten(self, tmp_path, _replace_spy):
        """Read-compare (not a cached flag): an external edit makes the next
        save write again even with unchanged in-memory data."""
        config = _config(tmp_path)
        config.save()
        assert len(_replace_spy) == 1

        config.config_path.write_text('{}\n', encoding='utf-8')
        config.save()  # same in-memory data, but disk content differs
        assert len(_replace_spy) == 2
        assert json.loads(config.config_path.read_text(encoding='utf-8')) != {}

    def test_corrupt_file_on_disk_still_saves(self, tmp_path, _replace_spy):
        """Invalid UTF-8 / garbage on disk must not kill save(): the write
        path self-heals it (parity with load()'s tolerance of junk)."""
        config = _config(tmp_path)
        config.save()
        config.config_path.write_bytes(b'\xff\xfe not json \x00')
        config.data['theme'] = 'ocean'
        config.save()
        assert len(_replace_spy) == 2
        assert json.loads(config.config_path.read_text(encoding='utf-8'))[
            'theme'] == 'ocean'

    def test_first_save_with_missing_file_writes(self, tmp_path, _replace_spy):
        config = _config(tmp_path)
        config.save()
        assert len(_replace_spy) == 1
        assert config.config_path.exists()

    def test_skip_path_re_tightens_loosened_mode_without_rewrite(
            self, tmp_path, _replace_spy):
        """An external chmod (e.g. a backup tool leaving 0644) must not
        persist just because the content is identical: the skip path
        re-tightens the mode without paying for a flash write."""
        config = _config(tmp_path)
        config.data['theme'] = 'ocean'
        config.save()
        assert len(_replace_spy) == 1

        os.chmod(config.config_path, 0o644)
        config.save()  # identical payload, loosened mode
        assert len(_replace_spy) == 1  # still skipped: no rewrite
        mode = stat.S_IMODE(os.stat(config.config_path).st_mode)
        assert mode == 0o600

    def test_skip_path_with_tight_mode_makes_no_chmod_call(
            self, tmp_path, _replace_spy, monkeypatch):
        """The chmod-on-skip is conditional: an already-0600 file is left
        entirely untouched (no syscall churn on every no-op save)."""
        config = _config(tmp_path)
        config.save()
        assert len(_replace_spy) == 1

        chmods: list[tuple] = []
        real_chmod = os.chmod

        def _spy(path, mode):
            chmods.append((path, mode))
            return real_chmod(path, mode)

        monkeypatch.setattr(os, 'chmod', _spy)

        config.save()  # identical payload, mode already 0600
        assert len(_replace_spy) == 1
        assert chmods == []


class TestPinHashing:
    def test_pbkdf2_pin_roundtrip(self, tmp_path):
        config = _config(tmp_path)
        config.set_pin('135790')
        assert config.pin_hash.startswith('pbkdf2$')
        assert config.verify_pin('135790') is True
        assert config.verify_pin('000000') is False

    def test_legacy_sha256_pin_verifies_and_upgrades(self, tmp_path):
        config = _config(tmp_path)
        legacy = hashlib.sha256(b'246810').hexdigest()
        config.data['pin_hash'] = legacy
        config.save()

        assert config.verify_pin('246810') is True

        stored = json.loads(config.config_path.read_text(encoding='utf-8'))['pin_hash']
        assert stored.startswith('pbkdf2$')
        assert stored != legacy

        reloaded = _config(tmp_path)
        reloaded.load()
        assert reloaded.verify_pin('246810') is True

    def test_wrong_pin_rejected_and_legacy_left_unupgraded(self, tmp_path):
        config = _config(tmp_path)
        legacy = hashlib.sha256(b'246810').hexdigest()
        config.data['pin_hash'] = legacy
        config.save()

        assert config.verify_pin('111111') is False
        assert config.pin_hash == legacy

    @pytest.mark.parametrize('junk', [
        '',
        'garbage',
        'deadbeef',
        'pbkdf2$$ab$cd',
        'pbkdf2$notanint$ab$cd',
        'pbkdf2$100000$zz$cd',
        'pbkdf2$100000$abcd$not-hex',
        'héllo-non-ascii',
    ])
    def test_corrupt_stored_hash_fails_safe(self, tmp_path, junk):
        config = _config(tmp_path)
        config.data['pin_hash'] = junk
        assert config.verify_pin('123456') is False

    def test_absent_pin_still_verifies_true(self, tmp_path):
        config = _config(tmp_path)
        assert config.pin_hash is None
        assert config.verify_pin('999999') is True


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
