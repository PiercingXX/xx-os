"""A superseded folder listing never paints (no GTK needed)."""
from types import SimpleNamespace

from xx_files import jobs
from xx_files.main import FilesWindow
from xx_files.prefs import Settings


def _window():
    painted = []
    win = SimpleNamespace(
        _list_job=None, _kids={}, cwd="/a", settings=Settings(),
        _alive=True, rows=[], _list_error={"status": "ok"}, mode="browse",
        expanded=set())
    win._render_rows = lambda: painted.append(
        (list(win.rows), dict(win._list_error)))
    return win, painted


def test_late_cancelled_listing_is_not_an_error(monkeypatch):
    calls = []
    monkeypatch.setattr(jobs, "submit",
                        lambda *a, **k: calls.append(k) or k["job"])
    win, painted = _window()
    FilesWindow.refresh(win)          # launch listing
    win.cwd = "/b"
    FilesWindow.refresh(win)          # the folder the user opened
    first, second = calls
    assert first["job"].cancelled
    row = {"name": "x", "path": "/b/x", "is_dir": False, "size": 1,
           "modified": 0.0}
    second["on_done"]({"status": "ok", "rows": [row]})
    # The first job's Cancelled (and any late result) lands afterwards.
    first["on_error"](jobs.Cancelled())
    first["on_done"]({"status": "ok", "rows": []})
    assert painted == [([row], {"status": "ok", "rows": [row]})]
    assert win.rows == [row]
    assert win._list_error["status"] == "ok"


def test_current_listing_failure_still_shows(monkeypatch):
    calls = []
    monkeypatch.setattr(jobs, "submit",
                        lambda *a, **k: calls.append(k) or k["job"])
    win, painted = _window()
    FilesWindow.refresh(win)
    calls[0]["on_error"](OSError("Input/output error"))
    assert win._list_error == {"status": "failed",
                               "message": "Input/output error"}
    assert len(painted) == 1
