"""Worker-backed operations against temp trees (no real system)."""
import os

from xx_files import jobs
from xx_files.jobs import Cancelled, Job
from xx_files.ops import count_visible, discover_roots, do_delete_forever
from xx_files.ops import do_empty_trash, do_mkdir, do_paste, do_rename
from xx_files.ops import list_dir_sync
from xx_files.trash_info import list_entries


def test_list_dir_ok_with_counts(tmp_path):
    docs = tmp_path / "Documents"
    docs.mkdir()
    (docs / "a.txt").write_text("hi")
    (docs / ".hidden").write_text("x")
    result = jobs.run_sync(list_dir_sync, str(docs), False)
    assert result["status"] == "ok"
    names = [r["name"] for r in result["rows"]]
    assert names == ["a.txt"]
    assert result["rows"][0]["count"] is None


def test_list_dir_subdir_counts(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "one.txt").write_text("1")
    (sub / "two.txt").write_text("2")
    result = jobs.run_sync(list_dir_sync, str(tmp_path), False)
    row = next(r for r in result["rows"] if r["name"] == "sub")
    assert (row["count"], row["hidden"]) == (2, 0)


def test_list_dir_blocked_on_file(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("x")
    assert jobs.run_sync(list_dir_sync, str(target),
                         False)["status"] == "blocked"


def test_list_dir_missing_is_failed(tmp_path):
    result = jobs.run_sync(list_dir_sync, str(tmp_path / "nope"), False)
    assert result["status"] == "failed"
    assert result["message"]


def test_list_dir_cancelled(tmp_path):
    job = Job()
    job.cancel()
    try:
        list_dir_sync(job, str(tmp_path))
    except Cancelled:
        return
    raise AssertionError("expected Cancelled")


def test_count_visible(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / ".h").write_text("x")
    assert count_visible(str(tmp_path), False) == (1, 1)
    assert count_visible(str(tmp_path), True) == (2, 0)


def test_discover_roots(tmp_path):
    (tmp_path / "Documents").mkdir()
    roots = discover_roots(str(tmp_path), [("USB", str(tmp_path))])
    labels = [r.label for r in roots]
    assert labels[0] == "Home"
    assert "Documents" in labels
    assert "Downloads" not in labels
    assert "USB" in labels


def test_mkdir_and_rename(tmp_path):
    made = jobs.run_sync(do_mkdir, str(tmp_path / "New"))
    assert os.path.isdir(made)
    renamed = jobs.run_sync(do_rename, made, str(tmp_path / "Old"))
    assert os.path.isdir(renamed)
    assert not os.path.exists(made)


def test_paste_copy_uniquifies(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "note.txt").write_text("hello")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "note.txt").write_text("taken")
    made = jobs.run_sync(do_paste, [(str(src / "note.txt"), False)],
                         str(cwd))
    assert made == [str(cwd / "note (2).txt")]
    assert open(made[0]).read() == "hello"
    assert (src / "note.txt").exists()


def test_paste_cut_moves(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "note.txt").write_text("hello")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    made = jobs.run_sync(do_paste, [(str(src / "note.txt"), True)],
                         str(cwd))
    assert made == [str(cwd / "note.txt")]
    assert not (src / "note.txt").exists()


def test_paste_progress_and_cancel(tmp_path):
    seen = []
    src = tmp_path / "src"
    src.mkdir()
    (src / "note.txt").write_text("hello")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    made = jobs.run_sync(do_paste, [(str(src / "note.txt"), False)],
                         str(cwd),
                         on_progress=lambda d, t: seen.append((d, t)))
    assert made == [str(cwd / "note.txt")]
    assert seen and seen[-1][0] == seen[-1][1]
    job = Job()
    job.cancel()
    try:
        do_paste(job, [(str(src / "note.txt"), False)], str(cwd))
    except Cancelled:
        return
    raise AssertionError("expected Cancelled")


def _seed_trash(monkeypatch, tmp_path):
    data = tmp_path / "data"
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    files = data / "Trash" / "files"
    info = data / "Trash" / "info"
    files.mkdir(parents=True)
    info.mkdir(parents=True)
    (files / "gone.txt").write_text("bye")
    (info / "gone.txt.trashinfo").write_text(
        "[Trash Info]\nPath=/home/u/gone.txt\n"
        "DeletionDate=2026-10-01T10:00:00\n")
    return files, info


def test_trash_list_and_empty(monkeypatch, tmp_path):
    files, _info = _seed_trash(monkeypatch, tmp_path)
    entries = list_entries(str(files), str(files.parent / "info"))
    assert [e.display_name for e in entries] == ["gone.txt"]
    assert entries[0].original_path == "/home/u/gone.txt"
    removed = jobs.run_sync(do_empty_trash)
    assert removed == 1
    assert list(os.scandir(str(files))) == []


def test_delete_forever_removes_tree(tmp_path):
    victim = tmp_path / "album"
    victim.mkdir()
    (victim / "shot.jpg").write_text("img")
    jobs.run_sync(do_delete_forever, [str(victim)])
    assert not victim.exists()


def test_read_kids_sync_reports_sizes_and_counts(tmp_path):
    from xx_files.main import FilesWindow

    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "a.txt").write_text("hello")
    (sub / ".hidden").write_text("x")
    (tmp_path / "f.bin").write_bytes(b"x" * 100)
    _path, kids = FilesWindow._read_kids_sync(Job(), str(tmp_path),
                                              False)
    by_name = {kid["name"]: kid for kid in kids}
    assert by_name["f.bin"]["size"] == 100
    assert (by_name["sub"]["count"],
            by_name["sub"]["hidden"]) == (1, 1)
