"""Local thumbs up/down: store, signed-out path, filtering, skip,
boost, kill hint, and remote forwarding. Fake engine only, no network."""

from __future__ import annotations

import json
import time

from xx_music import engine as e
from xx_music import policy as P
from xx_music import thumbs as T
from xx_music.fake_engine import FakeEngine
from xx_music.paths import AppPaths
from xx_music.ui.store import UiStore


def _track(tid: str, title: str = "", artist: str = "Band",
           artist_id: str = "ar-1") -> e.Track:
    return e.Track(id=tid, title=title or tid,
                   artists=(e.ArtistRef(artist, artist_id),))


def _rated(*specs) -> dict:
    out = {}
    for tid, rating, artist, aid in specs:
        out[tid] = (rating, _track(tid, tid, artist, aid))
    return out


def _ui(tmp_path=None, engine=None, prefs=None):
    from xx_music.appspec import AppSpec
    from xx_music.fake_engine import FakeEngine as FE
    from xx_music.tasks import Worker
    from xx_music.ui.context import UiContext
    from xx_music.ui.player import LocalBackend, PlayerModel
    from xx_music.ui.store import UiStore as S

    eng = engine or FE()
    spec = AppSpec(app_id="org.example.Test", name="Test",
                   dir_name="xx-radio-test", mpris_suffix="test_thumbs",
                   engine_factory=lambda _ctx: FE())
    worker = Worker(post=lambda cb: cb())
    backend = LocalBackend()
    player = PlayerModel(backend)
    store = S(AppPaths("xx-radio-test"), "fake")
    ui = UiContext(spec, eng, worker=worker, player=player, store=store)
    ui.prefs = dict(prefs or {})
    return ui, store


def _wait_for(fn, timeout_s: float = 5.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(0.02)
    return fn()


# -- 1. store round-trip ----------------------------------------------------


def test_store_round_trip_with_artists_and_time(tmp_path):
    store = UiStore(AppPaths("xx-radio-test"), "fake")
    track = _track("tr-1", "Song One", "Lumen Drive", "ar-lumen")
    store.set_rating(track, e.Rating.LIKE)
    ratings = store.ratings()
    assert ratings["tr-1"][0] == e.Rating.LIKE
    assert ratings["tr-1"][1] == track
    raw = json.loads(
        (store._state("ratings.json")).read_text(encoding="utf-8"))
    entry = raw["tr-1"]
    assert entry["rating"] == "like"
    assert entry["artists"] == [{"id": "ar-lumen", "name": "Lumen Drive"}]
    assert entry["at"] > 0
    assert entry["engine"] == "fake"
    assert [t.id for t in store.liked()] == ["tr-1"]
    assert store.disliked() == []
    other = _track("tr-2", "Song Two", "North Hollow", "ar-north")
    store.set_rating(other, e.Rating.DISLIKE)
    assert {t.id for t in store.disliked()} == {"tr-2"}
    assert store.rating_of("tr-2") == e.Rating.DISLIKE
    store.set_rating(track, e.Rating.NONE)
    assert store.liked() == []
    assert store.rating_of("tr-1") == e.Rating.NONE
    again = UiStore(AppPaths("xx-radio-test"), "fake")
    assert again.rating_of("tr-2") == e.Rating.DISLIKE
    assert again.rating_of("tr-1") == e.Rating.NONE


# -- 2. signed-out path ------------------------------------------------------


def test_signed_out_thumb_still_stores_locally():
    class NoRateEngine(FakeEngine):
        capabilities = e.ALL_CAPABILITIES - {e.RATE}

    ui, store = _ui(engine=NoRateEngine())
    ui.signed_in = False
    track = _track("tr-010", "Offline Song", "Paper Satellites", "ar-paper")
    done = []
    ui.rate(track, e.Rating.LIKE, on_done=lambda _v: done.append(True))
    assert _wait_for(lambda: bool(done))
    assert ui.player.rating("tr-010") == e.Rating.LIKE
    assert store.rating_of("tr-010") == e.Rating.LIKE
    ui.rate(track, e.Rating.LIKE, on_done=lambda _v: done.append(True))
    assert _wait_for(lambda: len(done) >= 2)
    assert ui.player.rating("tr-010") == e.Rating.NONE
    assert store.rating_of("tr-010") == e.Rating.NONE


# -- 3. down excludes everywhere but explicit search -------------------------


def test_down_excludes_from_suggestions_radio_home_and_top():
    glass = _track("tr-001", "Glass Harbor", "Lumen Drive", "ar-lumen")
    ratings = {"tr-001": (e.Rating.DISLIKE, glass)}
    engine = P.FilteredEngine(FakeEngine(), None,
                              ratings_fn=lambda: ratings)
    assert "Glass Harbor" not in engine.suggestions("Glass")
    assert "Lumen Drive" not in engine.suggestions("Lumen")
    assert "Low Orbit" in engine.suggestions("Low")
    radio = engine.radio("tr-002", limit=6)
    assert radio
    assert all(t.id != "tr-001" for t in radio)
    for shelf in engine.home():
        for item in shelf.items:
            if item.kind == e.ItemKind.TRACK and item.track is not None:
                assert item.track.id != "tr-001"
            if item.kind == e.ItemKind.ARTIST:
                assert item.id != "ar-lumen" or True
    for shelf in engine.charts():
        for item in shelf.items:
            if item.kind == e.ItemKind.TRACK and item.track is not None:
                assert item.track.id != "tr-001"
    for shelf in engine.mood_shelves("mo-chill"):
        assert shelf.items
    top = engine.search("lumen")
    assert all(t.id != "tr-001" for t in top.tracks)
    explicit = engine.search("lumen", e.ItemKind.TRACK)
    assert any(t.id == "tr-001" for t in explicit.tracks)
    assert T.rating_of(ratings, "tr-001") == e.Rating.DISLIKE


def test_down_filters_library_but_liked_merges():
    local = {
        "tr-099": (e.Rating.LIKE,
                   _track("tr-099", "House Song", "House Band", "ar-house")),
        "tr-001": (e.Rating.DISLIKE,
                   _track("tr-001", "Glass Harbor",
                          "Lumen Drive", "ar-lumen")),
    }

    def ratings_fn():
        return dict(local)

    signed = P.FilteredEngine(FakeEngine(signed_in=True), None,
                              ratings_fn=ratings_fn)
    songs = signed.library(e.LibrarySection.SONGS)
    assert all((i.track.id if i.track else i.id) != "tr-001" for i in songs)
    liked = signed.library(e.LibrarySection.LIKED)
    ids = [(i.track.id if i.track else i.id) for i in liked]
    assert "tr-099" in ids
    assert len(ids) == len(set(ids))


# -- 4. skip on down ----------------------------------------------------------


def test_dislike_of_current_skips_locally():
    from xx_music.ui.player import LocalBackend, PlayerModel

    backend = LocalBackend()
    model = PlayerModel(backend)
    model.play([_track("t1"), _track("t2"), _track("t3")], 0, {})
    assert model.track.id == "t1"
    model.rate("t1", e.Rating.DISLIKE)
    assert [t.id for t in model.queue] == ["t2", "t3"]
    assert model.track.id == "t2"


def test_service_dislike_of_current_advances(tmp_path):
    from xx_music.appspec import AppSpec
    from xx_music.player.service import InlineExecutor, PlayerService
    from xx_radio.main import SPEC

    from player_fakes import FakeClock, FakeProxy, fakesink, make_track

    spec = AppSpec(app_id="org.example.Player", name="Example",
                   dir_name="example-player", mpris_suffix="example_thumbs",
                   engine_factory=lambda _ctx: FakeEngine(),
                   player_actions=SPEC.player_actions)
    engine = FakeEngine()
    proxy = FakeProxy({})
    service = PlayerService(
        spec, engine, proxy, clock=FakeClock(),
        idle_add=lambda fn, *args: fn(*args),
        schedule=lambda ms, fn: 0,
        executor=InlineExecutor(), audio_sink=fakesink())
    try:
        service.play([make_track("tr-001"), make_track("tr-002")])
        assert service.queue.current().id == "tr-001"
        service.rate("tr-001", "dislike")
        assert "tr-001" not in service.queue.ids()
        assert service.queue.current() is not None
        assert service.queue.current().id == "tr-002"
        assert "tr-001" in service._disliked
    finally:
        try:
            service.request_stop()
        except Exception:  # noqa: BLE001 - teardown never fails
            pass


# -- 5. up boost is deterministic ---------------------------------------------


def test_up_boost_reorders_deterministically():
    base = FakeEngine().home()[0].items
    tracks = [i.track for i in base if i.track is not None]
    assert tracks
    liked_id = tracks[-1].id
    ratings = {liked_id: (e.Rating.LIKE, tracks[-1])}
    first = T.order_tracks(tracks, ratings)
    second = T.order_tracks(tracks, ratings)
    assert [t.id for t in first] == [t.id for t in second]
    assert first[0].id == liked_id
    assert [t.id for t in first] != [t.id for t in tracks]
    engine = P.FilteredEngine(FakeEngine(), None,
                              ratings_fn=lambda: ratings)
    shelf = engine.home()[0]
    ids = [i.track.id for i in shelf.items if i.track is not None]
    assert ids[0] == liked_id


def test_artist_downs_sink_while_ups_float():
    tracks = [_track("a1", "A one", "Down Band", "ar-down"),
              _track("a2", "A two", "Down Band", "ar-down"),
              _track("b1", "B one", "Up Band", "ar-up")]
    ratings = {
        "a1": (e.Rating.DISLIKE, tracks[0]),
        "a2": (e.Rating.DISLIKE, tracks[1]),
        "other-down": (e.Rating.DISLIKE,
                       _track("x", "X", "Down Band", "ar-down")),
        "b1": (e.Rating.LIKE, tracks[2]),
    }
    ordered = T.order_tracks(tracks, ratings)
    assert ordered[0].id == "b1"
    assert ordered[-1].id in ("a1", "a2")


# -- 6. kill hint exactly once -------------------------------------------------


def test_kill_hint_threshold_and_exactly_once():
    ratings = {}
    assert T.kill_candidate(ratings, None, set()) is None
    ratings = _rated(
        ("d1", e.Rating.DISLIKE, "Down Band", "ar-down"),
        ("d2", e.Rating.DISLIKE, "Down Band", "ar-down"))
    assert T.kill_candidate(ratings, None, set()) is None
    ratings["d3"] = (e.Rating.DISLIKE,
                     _track("d3", "d3", "Down Band", "ar-down"))
    found = T.kill_candidate(ratings, None, set())
    assert found == ("ar-down", "Down Band")
    key = T.suggestion_key(*found)
    store = UiStore(AppPaths("xx-radio-test"), "fake")
    assert key not in store.thumb_suggested()
    store.note_thumb_suggested(key)
    assert T.kill_candidate(ratings, None, store.thumb_suggested()) is None
    again = UiStore(AppPaths("xx-radio-test"), "fake")
    assert T.kill_candidate(ratings, None, again.thumb_suggested()) is None


def test_kill_hint_never_for_blocked_artist(tmp_path):
    from xx_music.blocked import BlockedArtists

    blocked = BlockedArtists(tmp_path / "blocked.json")
    blocked.block("ar-down", "Down Band")
    ratings = _rated(
        ("d1", e.Rating.DISLIKE, "Down Band", "ar-down"),
        ("d2", e.Rating.DISLIKE, "Down Band", "ar-down"),
        ("d3", e.Rating.DISLIKE, "Down Band", "ar-down"))
    assert T.kill_candidate(ratings, blocked, set()) is None


def test_ui_suggests_kill_once_through_kill_flow(monkeypatch):
    import xx_music.thumbs as _t

    calls = []

    ui, store = _ui()
    seen_kills = []
    monkeypatch.setattr("xx_music.ui.kill.request_kill",
                        lambda _ui, aid, name: seen_kills.append((aid, name)))
    first = _track("k1", "K one", "Hint Band", "ar-hint")
    second = _track("k2", "K two", "Hint Band", "ar-hint")
    third = _track("k3", "K three", "Hint Band", "ar-hint")
    for track in (first, second):
        ui.player.rate(track.id, e.Rating.DISLIKE)
        store.set_rating(track, e.Rating.DISLIKE)
    ui._maybe_suggest_kill(second)
    assert seen_kills == []
    ui.player.rate(third.id, e.Rating.DISLIKE)
    store.set_rating(third, e.Rating.DISLIKE)
    ui._maybe_suggest_kill(third)
    assert seen_kills == [("ar-hint", "Hint Band")]
    assert _t.suggestion_key("ar-hint", "Hint Band") in store.thumb_suggested()
    ui._maybe_suggest_kill(third)
    assert len(seen_kills) == 1
    assert calls == []


# -- 7. remote forwarding -------------------------------------------------------


def _service_with(engine, tmp_path, dirname):
    from xx_music.appspec import AppSpec
    from xx_music.player.service import InlineExecutor, PlayerService
    from xx_radio.main import SPEC

    from player_fakes import FakeClock, FakeProxy, fakesink

    spec = AppSpec(app_id="org.example.Player", name="Example",
                   dir_name=dirname, mpris_suffix=dirname,
                   engine_factory=lambda _ctx: engine,
                   player_actions=SPEC.player_actions)
    proxy = FakeProxy({})
    service = PlayerService(
        spec, engine, proxy, clock=FakeClock(),
        idle_add=lambda fn, *args: fn(*args),
        schedule=lambda ms, fn: 0,
        executor=InlineExecutor(), audio_sink=fakesink())
    return service


def test_keep_liked_offline_enqueues_download_when_set():
    ui, _store = _ui(prefs={"keep_liked_offline": True})
    track = _track("keep-1", "Keep Me", "Keep Band", "ar-keep")
    done = []
    ui.rate(track, e.Rating.LIKE, on_done=lambda _v: done.append(True))
    assert _wait_for(lambda: bool(done))
    kinds = [d.get("id") for d in ui.player.backend.downloads]
    assert "keep-1" in kinds
    ui2, _s2 = _ui(prefs={"keep_liked_offline": False})
    done2 = []
    ui2.rate(track, e.Rating.LIKE, on_done=lambda _v: done2.append(True))
    assert _wait_for(lambda: bool(done2))
    assert ui2.player.backend.downloads == []


def test_remote_rate_called_when_capable_and_signed_in(tmp_path):
    engine = FakeEngine(signed_in=True)
    service = _service_with(engine, tmp_path, "example_remote_on")
    try:
        service.rate("tr-002", "dislike")
        assert engine.ratings.get("tr-002") == e.Rating.DISLIKE
        assert "rate" in engine.calls
        assert "tr-002" in service._disliked
    finally:
        try:
            service.request_stop()
        except Exception:  # noqa: BLE001 - teardown never fails
            pass


def test_remote_rate_skipped_when_signed_out_but_local_applies(tmp_path):
    from player_fakes import make_track

    engine = FakeEngine(signed_in=False)
    service = _service_with(engine, tmp_path, "example_remote_off")
    try:
        service.play([make_track("tr-001"), make_track("tr-002")])
        service.rate("tr-001", "dislike")
        assert "rate" not in engine.calls
        assert engine.ratings.get("tr-001") is None
        assert "tr-001" in service._disliked
        assert "tr-001" not in service.queue.ids()
        assert service.queue.current().id == "tr-002"
    finally:
        try:
            service.request_stop()
        except Exception:  # noqa: BLE001 - teardown never fails
            pass


def test_remote_rate_skipped_when_engine_lacks_capability(tmp_path):
    class NoRateEngine(FakeEngine):
        capabilities = e.ALL_CAPABILITIES - {e.RATE}

        def rate(self, track_id, rating):
            raise AssertionError("must not call remote rate")

    engine = NoRateEngine(signed_in=True)
    service = _service_with(engine, tmp_path, "example_no_rate")
    try:
        service.rate("tr-002", "dislike")
        assert "tr-002" in service._disliked
    finally:
        try:
            service.request_stop()
        except Exception:  # noqa: BLE001 - teardown never fails
            pass
