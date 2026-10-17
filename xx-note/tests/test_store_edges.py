"""Vault edge cases: files are truth, trash expiry, search, index cache."""
from pathlib import Path

from xx_note import frontmatter as fm
from xx_note import store as store_mod


def _vault(tmp_path: Path) -> store_mod.Vault:
    vault = store_mod.Vault(tmp_path / "Notes")
    vault.ensure()
    return vault


def test_create_writes_slug_filename_with_frontmatter(tmp_path):
    vault = _vault(tmp_path)
    note = vault.create("Grocery list", "- [ ] milk\n")
    assert note.path.endswith("-grocery-list.md")
    text = vault.read(note.path)
    assert text is not None
    doc = fm.parse(text)
    assert doc.id == note.id
    assert doc.title == "Grocery list"
    assert "- [ ] milk" in doc.body_text


def test_scan_skips_id_less_files(tmp_path):
    vault = _vault(tmp_path)
    (vault.notes_dir / "stray.md").write_text("# no frontmatter\n", encoding="utf-8")
    vault.create("Real", "body")
    assert [n.title for n in vault.scan()] == ["Real"]


def test_trash_and_restore_round_trip(tmp_path):
    vault = _vault(tmp_path)
    note = vault.create("Temp", "body")
    assert vault.trash_note(note.path)
    assert vault.scan() == []
    assert len(vault.scan_trash()) == 1
    assert vault.restore(note.path)
    assert [n.title for n in vault.scan()] == ["Temp"]


def test_expire_removes_only_old_trash(tmp_path):
    import time

    vault = _vault(tmp_path)
    old = vault.create("Old", "x")
    vault.trash_note(old.path)
    trash_file = vault.notes_dir / ".xxnote" / "trash" / old.path
    text = trash_file.read_text(encoding="utf-8")
    doc = fm.parse(text)

    def _backdate(keys: fm.OwnedKeys) -> None:
        keys.trashed_at = "2020-01-01T00:00:00Z"

    trash_file.write_text(doc.rewritten(_backdate), encoding="utf-8")
    fresh = vault.create("Fresh", "y")
    vault.trash_note(fresh.path)
    removed = vault.expire_trash(int(time.time() * 1000))
    assert removed == 1
    assert [n.title for n in vault.scan_trash()] == ["Fresh"]


def test_search_matches_title_and_body(tmp_path):
    vault = _vault(tmp_path)
    vault.create("Lisbon trip", "trams and custards")
    vault.create("Groceries", "oat milk")
    hits = vault.search("trams")
    assert [n.title for n in hits] == ["Lisbon trip"]
    assert vault.search("---") == []


def test_base_snapshots_round_trip(tmp_path):
    vault = _vault(tmp_path)
    note = vault.create("Sync me", "v1")
    assert vault.base_of(note.id) is None
    vault.set_base(note.id, "v1", '"e1"')
    assert vault.base_of(note.id) == "v1"
    assert vault.etag_of(note.id) == '"e1"'
    assert note.id in vault.outbox()
    vault.clear_outbox(note.id)
    assert note.id not in vault.outbox()


def test_first_run_creates_tree_with_0700(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    from xx_note import paths as paths_mod

    notes = home / "Documents" / "Notes"
    paths_mod.ensure_notes_tree(notes)
    assert notes.is_dir()
    assert (notes / ".xxnote" / "trash").is_dir()
    assert oct((notes / ".xxnote").stat().st_mode & 0o777) == "0o700"
