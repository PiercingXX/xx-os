"""Bookmarks: Room cache, offline queue, flush, 404 drops."""

from xx_audiobook import api as api_module
from xx_audiobook import bookmarks as bookmarks_module
from xx_audiobook import store as store_module
from xx_audiobook.api import AudiobookApi
from xx_audiobook.bookmarks import BookmarkRepository, BookmarksState


def _repo(server, db, transport=None, now=None):
    kwargs = {} if transport is None else {"transport": transport}
    moment = now if now is not None else [1790000000000]
    return BookmarkRepository(
        AudiobookApi(server.api_base, "tok", **kwargs), db,
        now_ms=lambda: moment[0])


def _dead_transport(method, url, headers, body, timeout):
    raise api_module.ApiError.Unreachable("off")


def test_add_online_gets_server_id(fake_server):
    db = store_module.Database.in_memory()
    try:
        repo = _repo(fake_server, db)
        row = repo.add("it-1", 250.0, "  great line  ")
        assert row.id == "bm-2"
        assert row.pending is None
        assert (row.position_s, row.note) == (250.0, "great line")
        assert repo.pending_count() == 0
        assert [b.id for b in repo.observe("it-1")] == ["bm-2"]
    finally:
        db.close()


def test_offline_add_queues_then_flushes(fake_server):
    db = store_module.Database.in_memory()
    try:
        offline = _repo(fake_server, db, transport=_dead_transport)
        local = offline.add("it-1", 300.0, "later")
        assert local.id.startswith(bookmarks_module.LOCAL_PREFIX)
        assert local.pending == bookmarks_module.CREATE
        assert offline.pending_count() == 1
        assert [b.id for b in offline.observe("it-1")] == [local.id]

        online = _repo(fake_server, db)
        assert online.sync_pending() == 0
        assert online.pending_count() == 0
        rows = online.observe("it-1")
        assert [b.id for b in rows] == ["bm-2"]
        assert rows[0].note == "later"
    finally:
        db.close()


def test_offline_edit_stays_create_and_flushes_once(fake_server):
    db = store_module.Database.in_memory()
    try:
        offline = _repo(fake_server, db, transport=_dead_transport)
        local = offline.add("it-1", 300.0, "first")
        offline.edit(local, "second", 310.0)
        rows = offline.observe("it-1")
        assert len(rows) == 1
        assert (rows[0].note, rows[0].position_s) == ("second", 310.0)
        assert rows[0].pending == bookmarks_module.CREATE

        online = _repo(fake_server, db)
        assert online.sync_pending() == 0
        calls = [c for c in fake_server.calls
                 if c[0] == "POST"
                 and c[1] == "/audiobook/api/me/bookmarks"]
        assert len(calls) == 1
    finally:
        db.close()


def test_delete_never_synced_forgets_locally(fake_server):
    db = store_module.Database.in_memory()
    try:
        offline = _repo(fake_server, db, transport=_dead_transport)
        local = offline.add("it-1", 300.0, "scratch")
        posts = len([c for c in fake_server.calls
                     if c[0] == "POST"])
        offline.delete(local)
        assert offline.observe("it-1") == []
        assert offline.pending_count() == 0
        assert len([c for c in fake_server.calls
                    if c[0] == "POST"]) == posts
    finally:
        db.close()


def test_delete_synced_queues_then_flushes(fake_server):
    db = store_module.Database.in_memory()
    try:
        repo = _repo(fake_server, db)
        repo.refresh("it-1")
        assert [b.id for b in repo.observe("it-1")] == ["bm-1"]

        offline = _repo(fake_server, db, transport=_dead_transport)
        doomed = offline.observe("it-1")[0]
        offline.delete(doomed)
        assert offline.observe("it-1") == []
        assert offline.pending_count() == 1

        online = _repo(fake_server, db)
        assert online.sync_pending() == 0
        assert online.observe("it-1") == []
    finally:
        db.close()


def test_404_drops_the_row(fake_server):
    db = store_module.Database.in_memory()
    try:
        repo = _repo(fake_server, db)
        repo.refresh("it-1")
        row = repo.observe("it-1")[0]
        fake_server.queue("PATCH", "/audiobook/api/me/bookmarks/bm-1",
                          404, '{"detail": "gone"}')
        repo.edit(row, "changed")
        assert repo.observe("it-1") == []
        assert repo.pending_count() == 0
    finally:
        db.close()


def test_other_errors_stay_pending(fake_server):
    db = store_module.Database.in_memory()
    try:
        repo = _repo(fake_server, db)
        repo.refresh("it-1")
        row = repo.observe("it-1")[0]
        fake_server.queue("PATCH", "/audiobook/api/me/bookmarks/bm-1",
                          500, '{"detail": "boom"}')
        repo.edit(row, "changed")
        rows = repo.observe("it-1")
        assert len(rows) == 1
        assert rows[0].pending == bookmarks_module.EDIT
        assert repo.pending_count() == 1
    finally:
        db.close()


def test_refresh_keeps_unsent_edits(fake_server):
    db = store_module.Database.in_memory()
    try:
        offline = _repo(fake_server, db, transport=_dead_transport)
        local = offline.add("it-1", 300.0, "mine")
        online = _repo(fake_server, db)
        fake_server.queue("POST", "/audiobook/api/me/bookmarks", 500,
                          '{"detail": "boom"}')
        rows, error = online.refresh("it-1")
        assert error is None
        ids = sorted(b.id for b in rows)
        assert ids == sorted(["bm-1", local.id])
        assert online.pending_count() == 1
    finally:
        db.close()


def test_state_wrappers(fake_server):
    db = store_module.Database.in_memory()
    try:
        state = BookmarksState(
            AudiobookApi(fake_server.api_base, "tok"), db,
            now_ms=lambda: 1790000000000)
        rows, error = state.refresh("it-1")
        assert error is None
        assert [b.id for b in rows] == ["bm-1"]
        row = state.add("it-1", 10.0, "hi")
        assert row.id == "bm-2"
        assert state.pending_count() == 0
        state.edit(row, "hi!")
        assert state.observe("it-1")[0].note == "hi!"
        state.delete(row)
        assert [b.id for b in state.observe("it-1")] == ["bm-1"]
    finally:
        db.close()
