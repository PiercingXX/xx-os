"""Kill artist: blocked store, filtering, queue purge, persistence, undo."""

from __future__ import annotations

import json
from pathlib import Path

from xx_music import blocked as B
from xx_music import policy as P
from xx_music.blocked import BlockedArtist, BlockedArtists, purge_queue
from xx_music.engine import ArtistRef, Item, ItemKind, Track
from xx_music.fake_engine import FakeEngine
from xx_music.paths import AppPaths
from xx_music.queue import Queue
from xx_music.ui.store import PREF_DEFAULTS, UiStore


def _store(tmp_path: Path) -> BlockedArtists:
    return BlockedArtists(tmp_path / "blocked-artists.json")


def _track(tid: str, artists: tuple = ()) -> Track:
    return Track(id=tid, title=tid, artists=artists)


def _ar(name: str, aid: str = "") -> ArtistRef:
    return ArtistRef(name, aid)


# -- matching -----------------------------------------------------------------

def test_id_match_wins_when_names_collide(tmp_path):
    blocked = _store(tmp_path)
    blocked.block("ar-1", "Same Name")
    assert blocked.is_blocked_artist("ar-1", "Same Name")
    assert not blocked.is_blocked_artist("ar-2", "Same Name")
    assert blocked.is_track_blocked(
        _track("t1", (_ar("Same Name", "ar-1"),)))
    assert not blocked.is_track_blocked(
        _track("t2", (_ar("Same Name", "ar-2"),)))


def test_name_match_only_when_no_id_known(tmp_path):
    blocked = _store(tmp_path)
    blocked.block("", "Nameless Band")
    assert blocked.is_blocked_artist("", "nameless  band")
    assert blocked.is_blocked_artist("", "NAMELESS BAND")
    assert not blocked.is_blocked_artist("ar-9", "Nameless Band")
    assert blocked.is_track_blocked(_track("t1", (_ar("Nameless Band"),)))
    assert not blocked.is_track_blocked(
        _track("t2", (_ar("Nameless Band", "ar-9"),)))


def test_featured_artist_blocked_when_any_credited(tmp_path):
    blocked = _store(tmp_path)
    blocked.block("ar-north", "North Hollow")
    song = _track("t1", (_ar("Lumen Drive", "ar-lumen"),
                         _ar("North Hollow", "ar-north")))
    assert blocked.is_track_blocked(song)
    clean = _track("t2", (_ar("Lumen Drive", "ar-lumen"),
                          _ar("Paper Satellites", "ar-paper")))
    assert not blocked.is_track_blocked(clean)


# -- every result type ---------------------------------------------------------

def _blocked_lumen(tmp_path: Path) -> BlockedArtists:
    blocked = _store(tmp_path)
    blocked.block("ar-lumen", "Lumen Drive")
    return blocked


def test_search_results_filtered(tmp_path):
    blocked = _blocked_lumen(tmp_path)
    engine = P.FilteredEngine(FakeEngine(), blocked)
    results = engine.search("lumen")
    assert results.tracks == ()
    assert results.artists == ()
    assert all("Lumen" not in a.title for a in results.albums)
    fresh = FakeEngine().search("lumen")
    assert fresh.tracks and fresh.artists


def test_home_and_charts_and_mood_shelves_filtered(tmp_path):
    blocked = _blocked_lumen(tmp_path)
    engine = P.FilteredEngine(FakeEngine(), blocked)
    for shelf in engine.home():
        for item in shelf.items:
            if item.kind == ItemKind.TRACK and item.track is not None:
                assert not blocked.is_track_blocked(item.track)
            if item.kind == ItemKind.ARTIST:
                assert item.id != "ar-lumen"
    for shelf in engine.charts():
        for item in shelf.items:
            if item.kind == ItemKind.TRACK and item.track is not None:
                assert not blocked.is_track_blocked(item.track)
            if item.kind == ItemKind.ARTIST:
                assert item.id != "ar-lumen"
    for shelf in engine.mood_shelves("mo-chill"):
        assert shelf.items


def test_pages_filtered(tmp_path):
    blocked = _blocked_lumen(tmp_path)
    engine = P.FilteredEngine(FakeEngine(), blocked)
    album = engine.album("al-tide")
    assert album.tracks == ()
    artist = engine.artist("ar-lumen")
    assert artist.top_tracks == ()
    playlist = engine.playlist("pl-drive")
    assert all(not blocked.is_track_blocked(t) for t in playlist.tracks)
    assert len(playlist.tracks) < len(FakeEngine().playlist("pl-drive").tracks)
    from xx_music.engine import LibrarySection

    signed = P.FilteredEngine(FakeEngine(signed_in=True), blocked)
    library = signed.library(LibrarySection.SONGS)
    assert all(not blocked.is_track_blocked(t.track) for t in library
               if t.track is not None)


def test_artist_items_and_album_subtitle_filtered(tmp_path):
    blocked = _store(tmp_path)
    blocked.block("ar-x", "Lumen Drive")
    blocked.block("", "Paper Satellites")
    items = [
        Item(ItemKind.ARTIST, "ar-x", "Lumen Drive", "Artist"),
        Item(ItemKind.ARTIST, "ar-y", "Lumen Drive", "Artist"),
        Item(ItemKind.ALBUM, "al-1", "Signal", "2025 · Paper Satellites"),
        Item(ItemKind.ALBUM, "al-2", "Tidewater", "2024 · Lumen Drive"),
        Item(ItemKind.PLAYLIST, "pl-1", "Mix", "Demo"),
    ]
    kept = P.filter_items(items, blocked)
    kept_ids = {(i.kind, i.id) for i in kept}
    assert (ItemKind.ARTIST, "ar-x") not in kept_ids
    assert (ItemKind.ARTIST, "ar-y") in kept_ids
    assert (ItemKind.ALBUM, "al-1") not in kept_ids
    assert (ItemKind.PLAYLIST, "pl-1") in kept_ids


# -- radio refill ---------------------------------------------------------------

def test_radio_refill_skips_blocked(tmp_path):
    blocked = _store(tmp_path)
    blocked.block("ar-north", "North Hollow")
    engine = FakeEngine()
    seed = "tr-004"
    kept = P.fetch_radio_filtered(engine, seed, blocked, 5, set())
    assert kept
    assert all(not blocked.is_track_blocked(t) for t in kept)
    assert len(kept) == 5
    single = [t for t in engine.radio(seed, limit=5, exclude=frozenset())
              if not blocked.is_track_blocked(t)]
    assert len(kept) >= len(single)


def test_radio_never_runs_dry_when_unblocked_remain(tmp_path):
    blocked = _store(tmp_path)
    blocked.block("ar-lumen", "Lumen Drive")
    engine = P.FilteredEngine(FakeEngine(), blocked)
    got = engine.radio("tr-004", limit=4)
    assert len(got) == 4
    assert all(not blocked.is_track_blocked(t) for t in got)


# -- queue purge -----------------------------------------------------------------

def _queue_of(*specs: tuple[str, str, str]) -> Queue:
    queue = Queue()
    tracks = [_track(tid, (_ar(name, aid),)) for tid, name, aid in specs]
    queue.replace(tracks, start=0)
    return queue


def test_queue_purge_removes_blocked_and_keeps_current(tmp_path):
    blocked = _store(tmp_path)
    blocked.block("ar-bad", "Bad Band")
    queue = _queue_of(("t1", "Good", "ar-good"), ("t2", "Bad", "ar-bad"),
                      ("t3", "Good", "ar-good"))
    queue.play_at(0)
    removed, current_gone = purge_queue(queue, blocked.is_track_blocked)
    assert (removed, current_gone) == (1, False)
    assert [t.id for t in queue.tracks] == ["t1", "t3"]
    assert queue.current().id == "t1"


def test_queue_purge_skips_current_to_next(tmp_path):
    blocked = _store(tmp_path)
    blocked.block("ar-bad", "Bad Band")
    queue = _queue_of(("t1", "Bad", "ar-bad"), ("t2", "Good", "ar-good"),
                      ("t3", "Bad", "ar-bad"), ("t4", "Good", "ar-good"))
    queue.play_at(0)
    removed, current_gone = purge_queue(queue, blocked.is_track_blocked)
    assert removed == 2
    assert current_gone is True
    assert [t.id for t in queue.tracks] == ["t2", "t4"]
    assert queue.current().id == "t2"


def test_queue_purge_empty_when_all_blocked(tmp_path):
    blocked = _store(tmp_path)
    blocked.block("ar-bad", "Bad Band")
    queue = _queue_of(("t1", "Bad", "ar-bad"), ("t2", "Bad", "ar-bad"))
    queue.play_at(1)
    removed, current_gone = purge_queue(queue, blocked.is_track_blocked)
    assert removed == 2
    assert current_gone is True
    assert queue.tracks == [] and queue.index == -1


# -- persistence -------------------------------------------------------------------

def test_persistence_round_trip(tmp_path):
    path = tmp_path / "blocked-artists.json"
    first = BlockedArtists(path)
    first.block("ar-1", "First Band")
    first.block("", "Second Band")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(raw, list) and len(raw) == 2
    second = BlockedArtists(path)
    assert second.is_blocked_artist("ar-1", "First Band")
    assert second.is_blocked_artist("", "Second Band")
    assert len(second.list()) == 2


def test_store_block_unblock_round_trip():
    store = UiStore(AppPaths("xx-radio-test"), "fake")
    store.unblock_artist("ar-1", "First Band")
    store.unblock_artist("", "Second Band")
    entry, listed = store.block_artist("ar-1", "First Band")
    assert entry is not None and entry.id == "ar-1"
    assert any(e.id == "ar-1" for e in listed)
    store.block_artist("", "Second Band")
    assert len(store.blocked_list()) == 2
    remaining = store.unblock_artist("ar-1", "First Band")
    assert all(e.id != "ar-1" for e in remaining)
    store.unblock_artist("", "Second Band")
    assert store.blocked_list() == []


def test_undo_restores_visibility(tmp_path):
    blocked = _store(tmp_path)
    engine = P.FilteredEngine(FakeEngine(), blocked)
    assert engine.search("lumen").tracks
    blocked.block("ar-lumen", "Lumen Drive")
    assert engine.search("lumen").tracks == ()
    assert blocked.unblock("ar-lumen", "Lumen Drive")
    assert engine.search("lumen").tracks


# -- downloads and settings ---------------------------------------------------------

def test_download_rows_hidden_until_unblocked(tmp_path):
    from xx_music.ui.context import DONE

    class Row:
        def __init__(self, track):
            self.track = track
            self.state = DONE

    blocked = _store(tmp_path)
    rows = [Row(_track("t1", (_ar("Bad Band", "ar-bad"),))),
            Row(_track("t2", (_ar("Good", "ar-good"),)))]
    assert len(P.filter_download_rows(rows, None)) == 2
    blocked.block("ar-bad", "Bad Band")
    kept = P.filter_download_rows(rows, blocked)
    assert [r.track.id for r in kept] == ["t2"]
    blocked.unblock("ar-bad", "Bad Band")
    assert len(P.filter_download_rows(rows, blocked)) == 2


def test_remove_downloads_setting_defaults_off():
    assert PREF_DEFAULTS["remove_blocked_downloads"] is False
    store = UiStore(AppPaths("xx-radio-test"), "fake")
    assert store.prefs()["remove_blocked_downloads"] is False
    store.set_pref("remove_blocked_downloads", True)
    assert store.prefs()["remove_blocked_downloads"] is True
    store.set_pref("remove_blocked_downloads", False)


def test_settings_page_logic_helpers():
    assert B.confirm_text("North Hollow") == (
        "Kill North Hollow?",
        "Their songs leave your queue and won't be suggested again.")
    assert B.killed_text("North Hollow") == "Killed North Hollow"
    assert B.blocked_summary(0) == "None"
    assert B.blocked_summary(1) == "1 artist"
    assert B.blocked_summary(3) == "3 artists"
    from xx_music.ui.kill import targets_for_track

    track = _track("t1", (_ar("A Band", "ar-a"), _ar("A Band", "ar-a"),
                          _ar("B Band", "ar-b")))
    assert targets_for_track(track) == [("ar-a", "A Band"),
                                        ("ar-b", "B Band")]
    assert targets_for_track(_track("t2", ())) == []


def test_offline_mix_skips_blocked(tmp_path):
    from xx_music.cache import CacheEntry

    blocked = _store(tmp_path)
    blocked.block("", "Bad Band")
    entries = [CacheEntry("t1", artist="Bad Band"),
               CacheEntry("t2", artist="Good")]
    from xx_music.downloads import build_offline_mix as dl_mix

    assert [e.track_id for e in dl_mix(entries, {}, limit=5)] == ["t1", "t2"]
    assert [e.track_id for e in dl_mix(entries, {}, limit=5,
                                       blocked=blocked)] == ["t2"]
    tracks = [_track("t1", (_ar("Bad Band", "ar-bad"),)),
              _track("t2", (_ar("Good", "ar-good"),))]
    blocked2 = _store(tmp_path / "two")
    blocked2.block("ar-bad", "Bad Band")
    assert [t.id for t in P.build_offline_mix(
        tracks, None, set(), limit=5, blocked=blocked2)] == ["t2"]


def test_blocked_entry_shape(tmp_path):
    blocked = _store(tmp_path)
    entry = blocked.block("ar-1", "First Band")
    assert isinstance(entry, BlockedArtist)
    assert entry.id == "ar-1" and entry.name == "First Band"
    assert entry.blocked_at > 0
    assert blocked.block("", "") is None


def _test_ui(tmp_path, tracks, prefs=None):
    from xx_music.appspec import AppSpec
    from xx_music.fake_engine import FakeEngine as FE
    from xx_music.tasks import Worker
    from xx_music.ui.context import UiContext
    from xx_music.ui.player import LocalBackend, PlayerModel
    from xx_music.ui.store import UiStore

    spec = AppSpec(app_id="org.example.Test", name="Test",
                   dir_name="xx-radio-test", mpris_suffix="test_kill",
                   engine_factory=lambda _ctx: FE())
    worker = Worker(post=lambda cb: cb())
    queue = Queue()
    queue.replace(list(tracks), start=0)
    backend = LocalBackend(queue)
    player = PlayerModel(backend)
    store = UiStore(AppPaths("xx-radio-test"), "fake")
    ui = UiContext(spec, FE(), worker=worker, player=player, store=store)
    ui.prefs = dict(prefs or {})
    return ui


def test_ui_purge_skips_current_without_worker():
    ui = _test_ui(None, [_track("t1", (_ar("Bad", "ar-bad"),)),
                         _track("t2", (_ar("Good", "ar-good"),))])
    ui.blocked.block("ar-bad", "Bad")
    ui._purge_blocked_from_queue()
    assert [t.id for t in ui.player.queue] == ["t2"]
    assert ui.player.track.id == "t2"


def test_kill_hides_downloads_and_removes_when_set():
    import time

    from xx_music.ui.context import DONE
    from xx_music.ui.player import LocalBackend

    class Row:
        def __init__(self, track):
            self.track = track
            self.state = DONE

    good = _track("t-good", (_ar("Good", "ar-good"),))
    bad = _track("t-bad", (_ar("Bad", "ar-bad"),))
    ui = _test_ui(None, [good, bad], prefs={"remove_blocked_downloads": True})
    rows = [Row(good), Row(bad)]
    ui.hooks.downloads = lambda _ui: list(rows)
    ui.downloads = list(rows)
    backend = ui.player.backend
    assert isinstance(backend, LocalBackend)
    done = []
    ui.kill_artist("ar-bad", "Bad", on_done=lambda _e: done.append(True))
    deadline = time.time() + 5.0
    while not done and time.time() < deadline:
        time.sleep(0.02)
    assert done
    assert [t.id for t in ui.player.queue] == ["t-good"]
    assert "t-bad" in backend.removed
    deadline = time.time() + 5.0
    while [r.track.id for r in ui.downloads] != ["t-good"] \
            and time.time() < deadline:
        time.sleep(0.02)
    assert [r.track.id for r in ui.downloads] == ["t-good"]
