"""Cache cap, free-space floor, LRU eviction and the offline index."""

import pytest

from xx_music.cache import (
    DEFAULT_CAP_BYTES,
    MIN_FREE_BYTES,
    VALID_CAP_CHOICES_GB,
    CacheEntry,
    CacheIndex,
    CacheManager,
    plan_eviction,
)

GB = 2**30


@pytest.fixture()
def index(tmp_path):
    db = CacheIndex(tmp_path / "music.db")
    yield db
    db.close()


@pytest.fixture()
def manager(tmp_path, index):
    cache_dir = tmp_path / "audio"
    cache_dir.mkdir()
    return CacheManager(cache_dir, index,
                        free_bytes_fn=lambda: 64 * GB)


def _entry(track_id, size, played=0.0, pinned=False):
    return CacheEntry(track_id=track_id, engine="fake", title=track_id,
                      artist="Somebody", size_bytes=size,
                      last_played=played, pinned=pinned)


def _plant(manager, track_id, size):
    path = manager.complete_path(track_id)
    path.write_bytes(b"x" * size)
    return path


# -- the planner ----------------------------------------------------------

def test_plan_picks_lru_first_and_stops_when_full():
    entries = [_entry("a", 100, played=3.0),
               _entry("b", 100, played=1.0),
               _entry("c", 100, played=2.0)]
    assert plan_eviction(entries, 150) == ["b", "c"]
    assert plan_eviction(entries, 100) == ["b"]


def test_plan_never_evicts_pinned_or_protected():
    entries = [_entry("old", 500, played=1.0, pinned=True),
               _entry("cur", 500, played=2.0),
               _entry("next", 500, played=3.0)]
    assert plan_eviction(entries, 100, protected={"cur", "next"}) == []
    assert plan_eviction(entries, 100, protected={"cur"}) == ["next"]


def test_plan_needs_nothing_evicts_nothing():
    entries = [_entry("a", 100, played=1.0)]
    assert plan_eviction(entries, 0) == []
    assert plan_eviction(entries, -5) == []


def test_plan_too_small_to_help_evicts_nothing():
    entries = [_entry("a", 50, played=1.0)]
    assert plan_eviction(entries, 5000) == []


# -- cap and floor ----------------------------------------------------------

def test_default_cap_is_2gb_and_floor_is_1gb():
    assert DEFAULT_CAP_BYTES == 2 * GB
    assert MIN_FREE_BYTES == 1 * GB
    assert VALID_CAP_CHOICES_GB == (1, 2, 5, 10, 20)


def test_effective_cap_is_the_cap_when_space_is_plenty(manager):
    assert manager.effective_cap() == 2 * GB


def test_free_space_floor_shrinks_the_cap(tmp_path, index):
    tight = CacheManager(tmp_path / "audio", index,
                         free_bytes_fn=lambda: GB + GB // 2)
    _plant(tight, "a", 100)
    assert tight.effective_cap() == 100 + GB // 2
    assert tight.effective_cap() < tight.cap_bytes


def test_floor_never_goes_negative(tmp_path, index):
    bare = CacheManager(tmp_path / "audio", index,
                        free_bytes_fn=lambda: 0)
    assert bare.effective_cap() == 0


def test_cap_choices_validated(manager):
    manager.set_cap_gb(5)
    assert manager.cap_bytes == 5 * GB
    with pytest.raises(ValueError):
        manager.set_cap_gb(3)
    with pytest.raises(ValueError):
        manager.set_cap_bytes(0)


# -- eviction ---------------------------------------------------------------

def test_evict_drops_lru_but_keeps_pinned(manager, index):
    _plant(manager, "old", 400)
    _plant(manager, "mid", 400)
    _plant(manager, "new", 400)
    for track_id, played, pinned in (("old", 1.0, False),
                                     ("mid", 2.0, True),
                                     ("new", 3.0, False)):
        manager.register(_entry(track_id, 400, played, pinned))
    manager.set_cap_bytes(800)
    evicted = manager.evict()
    assert evicted == ["old"]
    assert not manager.is_cached("old")
    assert manager.is_cached("mid") and manager.is_cached("new")
    assert index.get("old") is None


def test_evict_never_touches_current_or_window(manager, index):
    for num in range(4):
        track_id = f"t{num}"
        _plant(manager, track_id, 300)
        manager.register(_entry(track_id, 300, played=float(num)))
    manager.set_cap_bytes(100)
    evicted = manager.evict(protected={"t0", "t1", "t2", "t3"})
    assert evicted == []
    assert manager.usage_bytes() == 1200


def test_evict_makes_room_for_an_incoming_track(manager, index):
    _plant(manager, "a", 300)
    _plant(manager, "b", 300)
    manager.register(_entry("a", 300, played=1.0))
    manager.register(_entry("b", 300, played=2.0))
    evicted = manager.evict(need_bytes=250)
    assert evicted == ["a"]
    assert manager.usage_bytes() == 300


# -- the browsable index ------------------------------------------------------

def test_index_round_trip_and_totals(index):
    index.upsert(CacheEntry(track_id="t1", engine="fake", title="Glass",
                            artist="Lumen", album="Tide", duration_s=214.0,
                            size_bytes=512, mime="audio/webm"))
    got = index.get("t1")
    assert got is not None and got.title == "Glass"
    assert got.artist == "Lumen" and got.size_bytes == 512
    assert index.totals() == (1, 512)


def test_index_search_and_recent_order(index):
    index.upsert(_entry("t1", 10, played=5.0))
    index.upsert(CacheEntry(track_id="t2", engine="fake",
                            title="Night Ferry", artist="Lumen",
                            size_bytes=10, last_played=9.0))
    found = index.find("ferry")
    assert [entry.track_id for entry in found] == ["t2"]
    assert [entry.track_id for entry in index.list_all()] == ["t2", "t1"]


def test_index_pins_and_plays(index):
    index.upsert(_entry("t1", 10))
    index.set_pinned("t1", True)
    index.mark_played("t1", 123.0)
    got = index.get("t1")
    assert got is not None and got.pinned and got.last_played == 123.0


def test_sync_from_disk_drops_gone_files(manager, index):
    _plant(manager, "here", 64)
    manager.register(_entry("here", 64))
    manager.register(_entry("gone", 64))
    manager.sync_from_disk()
    assert index.get("gone") is None
    assert index.get("here") is not None
