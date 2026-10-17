"""The window's non-widget logic: text helpers, the store, the player
model over the local backend, the art loader and the downloads reader.
No display, no network."""

from __future__ import annotations

import pytest

from xx_music.cache import CacheEntry, CacheIndex
from xx_music.engine import (AlbumRef, ArtistRef, Item, ItemKind, Rating,
                             Shelf, Track)
from xx_music.paths import AppPaths
from xx_music.queue import Queue
from xx_music.ui import fmt
from xx_music.ui.art import ArtLoader
from xx_music.ui.downloads import read_done
from xx_music.ui.player import (PAUSED, PLAYING, STOPPED, LocalBackend,
                                PlayerModel)
from xx_music.ui.store import RECENTS_KEPT, UiStore


def _track(n: int, seconds: float = 200.0) -> Track:
    return Track(f"t{n}", f"Song {n}", (ArtistRef("Band", "a1"),),
                 AlbumRef("Record", "al1"), seconds)


# -- fmt ----------------------------------------------------------------------

def test_durations_and_sizes():
    assert fmt.duration(None) == ""
    assert fmt.duration(187) == "3:07"
    assert fmt.duration(3723) == "1:02:03"
    assert fmt.clock(None) == "0:00"
    assert fmt.size(0) == "0 MB"
    assert fmt.size(5 * 2**20) == "5 MB"
    assert fmt.size(int(1.5 * 2**30)) == "1.5 GB"
    assert fmt.songs(1) == "1 song"
    assert fmt.songs(12) == "12 songs"


def test_initials_skip_small_words():
    assert fmt.initials("The Long Table") == "LT"
    assert fmt.initials("Signal") == "S"
    assert fmt.initials("") == "?"
    assert fmt.initials("Marlow & Vane") == "MV"


def test_origin_text():
    track = _track(1)
    assert fmt.origin_text({}) == ""
    assert fmt.origin_text({"type": "album", "title": "Record"}) == "Record"
    assert fmt.origin_text({"type": "radio"}, track) == "Radio · Song 1"
    assert fmt.origin_text({"type": "offline"}) == "On this phone"


def test_item_line_drops_repeated_kind():
    artist = Item(ItemKind.ARTIST, "a1", "Band", "Artist")
    assert fmt.item_line(artist) == "Artist"
    album = Item(ItemKind.ALBUM, "al1", "Record", "2024 · Band")
    assert fmt.item_line(album) == "Album · 2024 · Band"


# -- store --------------------------------------------------------------------

def test_home_cache_round_trip():
    store = UiStore(AppPaths("xx-radio-test"), "fake")
    shelves = [Shelf("Quick picks", (Item(ItemKind.TRACK, "t1", "Song 1",
                                          "Band", "", _track(1)),)),
               Shelf("Moods", (Item(ItemKind.MOOD, "m1", "Chill",
                                    "Moods"),))]
    store.save_home(shelves)
    loaded, saved_at = store.load_home()
    assert loaded == shelves
    assert saved_at > 0


def test_home_cache_survives_garbage():
    paths = AppPaths("xx-radio-test")
    store = UiStore(paths, "fake")
    store.home_path().parent.mkdir(parents=True, exist_ok=True)
    store.home_path().write_text("{not json", encoding="utf-8")
    assert store.load_home() == ([], 0.0)


def test_recents_newest_first_deduped_and_capped():
    store = UiStore(AppPaths("xx-radio-test"))
    for n in range(RECENTS_KEPT + 3):
        store.add_recent(f"query {n}")
    store.add_recent("QUERY 5")
    recents = store.recents()
    assert recents[0] == "QUERY 5"
    assert len(recents) == RECENTS_KEPT
    assert sum(q.casefold() == "query 5" for q in recents) == 1
    store.clear_recents()
    assert store.recents() == []


def test_history_ratings_and_prefs():
    store = UiStore(AppPaths("xx-radio-test"))
    store.note_played(_track(1))
    store.note_played(_track(2))
    store.note_played(_track(1))
    assert [t.id for t in store.history()] == ["t1", "t2"]
    store.set_rating(_track(3), Rating.LIKE)
    store.set_rating(_track(4), Rating.DISLIKE)
    assert [t.id for t in store.liked()] == ["t3"]
    store.set_rating(_track(3), Rating.NONE)
    assert store.liked() == []
    assert store.prefs()["wifi_only"] is True
    assert store.prefs()["welcome_done"] is False
    store.set_pref("welcome_done", True)
    assert store.prefs()["welcome_done"] is True
    store.set_pref("storage_cap_gb", 5)
    assert store.prefs()["storage_cap_gb"] == 5


# -- player model ---------------------------------------------------------------

class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_player_model_plays_and_ticks_locally():
    clock = Clock()
    model = PlayerModel(LocalBackend(), clock=clock)
    assert not model.has_queue and model.status == STOPPED
    seen = []
    model.connect(lambda: seen.append(model.status))
    model.play([_track(1), _track(2)], 0, {"type": "album",
                                           "title": "Record"})
    assert model.track.id == "t1" and model.playing
    assert [t.id for t in model.queue] == ["t1", "t2"]
    clock.now += 30
    assert 29.9 < model.position_s < 30.1
    assert 0.14 < model.fraction < 0.16
    model.play_pause()
    assert model.status == PAUSED
    clock.now += 30
    assert model.position_s < 31
    model.next()
    assert model.track.id == "t2"
    assert seen


def test_player_model_queue_commands():
    model = PlayerModel(LocalBackend(), clock=Clock())
    model.play([_track(1)], 0, {})
    model.enqueue([_track(2), _track(3)])
    model.enqueue([_track(4)], play_next=True)
    assert [t.id for _i, t in model.up_next()] == ["t4", "t2", "t3"]
    model.move(3, 1)
    assert [t.id for t in model.queue] == ["t1", "t3", "t4", "t2"]
    model.remove(1)
    assert [t.id for t in model.queue] == ["t1", "t4", "t2"]
    model.cycle_repeat()
    assert model.repeat == "all"
    model.cycle_repeat()
    assert model.repeat == "one"


def test_rating_toggles_and_dislike_leaves_queue():
    backend = LocalBackend()
    model = PlayerModel(backend, clock=Clock())
    model.play([_track(1), _track(2)], 0, {})
    model.rate("t2", Rating.LIKE)
    assert model.rating("t2") == Rating.LIKE
    model.rate("t2", Rating.LIKE)
    assert model.rating("t2") == Rating.NONE
    model.rate("t2", Rating.DISLIKE)
    assert [t.id for t in model.queue] == ["t1"]


def test_sleep_counts_down_while_playing():
    clock = Clock()
    model = PlayerModel(LocalBackend(), clock=clock)
    model.play([_track(1, 600)], 0, {})
    model.set_sleep(15 * 60)
    clock.now += 60
    assert 839 < model.sleep_remaining_s < 841
    model.set_sleep(-1)
    assert model.sleep_end_of_song and model.sleep_remaining_s is None
    model.set_sleep(0)
    assert model.sleep is None


def test_restored_queue_is_paused():
    queue = Queue()
    queue.replace([_track(1), _track(2)], 1, {})
    model = PlayerModel(LocalBackend(queue, PAUSED, 12.0), clock=Clock())
    assert model.track.id == "t2" and not model.playing
    assert model.position_s == 12.0
    assert PLAYING != model.status


def _snap(**changes) -> dict:
    snapshot = {"status": PLAYING, "track": _track(1, 215.0).to_dict(),
                "position_s": 10.0, "position_at": 100.0,
                "advancing": True, "duration_s": 0.0, "index": 0,
                "length": 1, "version": 0}
    snapshot.update(changes)
    return snapshot


def test_position_is_anchored_to_when_the_service_measured_it():
    """Delivery time does not count: the Changed signal arriving 0.4 s
    after the service measured 10 s means 10.4 s now."""
    clock = Clock()
    model = PlayerModel(clock=clock)
    clock.now = 100.4
    model.apply_snapshot(_snap())
    assert model.position_s == pytest.approx(10.4)
    clock.now += 1.0
    assert model.position_s == pytest.approx(11.4)


def test_position_stands_still_unless_advancing():
    """Resolving, buffering or a seek landing: Playing, but not moving."""
    clock = Clock()
    model = PlayerModel(clock=clock)
    model.apply_snapshot(_snap(advancing=False))
    clock.now += 5.0
    assert model.playing and model.position_s == 10.0
    model.apply_snapshot(_snap(status=PAUSED, position_at=clock.now))
    clock.now += 5.0
    assert model.position_s == 10.0


def test_foreign_or_missing_stamps_anchor_on_arrival():
    clock = Clock()
    model = PlayerModel(clock=clock)
    for stamp in (40.0, 160.0, "x"):
        model.apply_snapshot(_snap(position_at=stamp))
        assert model.position_s == pytest.approx(10.0)
    older = _snap()
    del older["position_at"], older["advancing"]
    model.apply_snapshot(older)
    clock.now += 2.0
    assert model.position_s == pytest.approx(12.0)


def test_stream_duration_wins_and_bounds_the_position():
    clock = Clock()
    model = PlayerModel(clock=clock)
    model.apply_snapshot(_snap())
    assert model.duration_s == 215.0
    model.apply_snapshot(_snap(duration_s=214.4, position_s=214.0))
    assert model.duration_s == 214.4
    clock.now += 3.0
    assert model.position_s == 214.4
    assert model.until_next_step(300) is None


def test_seek_and_track_change_start_from_their_own_position():
    clock = Clock()
    model = PlayerModel(clock=clock)
    model.apply_snapshot(_snap())
    clock.now += 1.0
    model.seek(42.0)
    assert model.position_s == 42.0
    model.apply_snapshot(_snap(position_s=42.0, position_at=clock.now,
                               advancing=False))
    clock.now += 0.2
    assert model.position_s == 42.0
    model.apply_snapshot(_snap(position_s=42.05, position_at=clock.now))
    clock.now += 1.0
    assert model.position_s == pytest.approx(43.05)
    model.apply_snapshot(_snap(track=_track(2, 180.0).to_dict(),
                               position_s=0.0, position_at=clock.now))
    clock.now += 0.25
    assert model.position_s == pytest.approx(0.25)
    assert model.duration_s == 180.0


def test_ticks_land_on_the_next_second_or_pixel():
    """The clock turns over on whole seconds; a bar moves when the song
    reaches its next pixel (215 s over 430 px: every half second)."""
    clock = Clock()
    model = PlayerModel(clock=clock)
    model.apply_snapshot(_snap(position_s=10.25))
    assert model.until_next_step() == pytest.approx(0.75)
    assert model.until_next_step(430) == pytest.approx(0.25)
    assert model.until_next_step(43) == pytest.approx(0.75)
    model.apply_snapshot(_snap(advancing=False))
    assert model.until_next_step(430) is None


def test_elapsed_never_rounds_up():
    assert fmt.elapsed(59.9) == "0:59"
    assert fmt.clock(59.9) == "1:00"
    assert fmt.elapsed(60.0) == "1:00"
    assert fmt.elapsed(214.7, 214.7) == "3:35"
    assert fmt.elapsed(None) == fmt.elapsed(-1.0) == "0:00"


# -- art loader -------------------------------------------------------------------

def test_art_loader_shares_one_fetch(tmp_path):
    fetched = []
    posted = []

    def fetch(url: str) -> bytes:
        fetched.append(url)
        return b"image"

    loader = ArtLoader(tmp_path / "art", sizer=lambda url, px: f"{url}={px}",
                       fetch=fetch, post=posted.append, workers=1)
    loader._texture = lambda data: ("texture", data)
    got = []
    loader.request("https://img/a", 96, got.append)
    loader.request("https://img/a", 96, got.append)
    loader.request("", 96, got.append)
    loader._pool.shutdown(wait=True)
    for callback in posted:
        callback()
    assert fetched == ["https://img/a=96"]
    assert got == [("texture", b"image"), ("texture", b"image")]
    assert len(list((tmp_path / "art").iterdir())) == 1


def test_art_loader_serves_disk_cache(tmp_path):
    def boom(_url):
        raise AssertionError("must not fetch")

    first = ArtLoader(tmp_path, fetch=lambda _u: b"x", post=lambda cb: cb(),
                      workers=1)
    first._texture = lambda data: data
    first.request("https://img/b", 64, lambda _t: None)
    first._pool.shutdown(wait=True)
    second = ArtLoader(tmp_path, fetch=boom, post=lambda cb: cb(),
                       workers=1)
    second._texture = lambda data: data
    got = []
    second.request("https://img/b", 64, got.append)
    second._pool.shutdown(wait=True)
    assert got == [b"x"]


# -- downloads -------------------------------------------------------------------

def test_read_done_lists_pinned_songs_read_only(tmp_path):
    db = tmp_path / "music.db"
    index = CacheIndex(db)
    index.upsert(CacheEntry("t1", "fake", "Song 1", "Band", "Record", 200.0,
                            4 * 2**20, pinned=True, cached_at=2.0))
    index.upsert(CacheEntry("t2", "fake", "Song 2", "Band", "", 100.0,
                            2**20, pinned=False, cached_at=3.0))
    index.close()
    rows = read_done(db)
    assert [r.track.id for r in rows] == ["t1"]
    assert rows[0].size_bytes == 4 * 2**20
    assert rows[0].track.artist_line == "Band"
    assert read_done(tmp_path / "missing.db") == []
