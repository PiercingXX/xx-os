"""YouTubeMusicEngine: contract signatures and catalog wiring.

The fake client mirrors the real ytmusicapi 1.12.3 signatures one by one
(checked against the installed library), answering with the recorded
fixtures; the engine must pass the same keywords the real client takes.
"""

import copy
import inspect
import json
from pathlib import Path

import pytest

from xx_music import engine as e
from xx_radio.youtube import catalog, engine as yt

FIXTURES = Path(__file__).parent / "fixtures" / "ytmusic"
CONTRACT = ("status", "sign_in", "sign_out", "search", "suggestions", "home",
            "moods", "mood_shelves", "charts", "library", "album", "artist",
            "playlist", "radio", "lyrics", "rate", "resolve", "art_url")
CATALOG_METHODS = ("search", "suggestions", "home", "moods", "mood_shelves",
                   "charts", "library", "album", "artist", "playlist",
                   "radio", "lyrics", "rate", "art_url", "status")


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


SONG = {"videoId": "v1", "title": "Song", "artists": [{"name": "A",
                                                       "id": "UC1"}],
        "album": {"name": "Al", "id": "MPRE1"}, "duration_seconds": 200,
        "thumbnails": [], "isExplicit": False}


class FakeYTMusic:
    """Fixture-backed stand-in with the real YTMusic signatures."""

    def __init__(self):
        self.calls = []
        self.answers = {}
        self.timed_lyrics = None

    def _run(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        return copy.deepcopy(self.answers.get(name))

    def search(self, query, filter=None, scope=None, limit=20,
               ignore_spelling=False):
        return self._run("search", query, filter=filter, limit=limit)

    def get_search_suggestions(self, query, detailed_runs=False):
        return self._run("get_search_suggestions", query)

    def get_home(self, limit=3):
        return self._run("get_home")

    def get_mood_categories(self):
        return self._run("get_mood_categories")

    def get_mood_playlists(self, params):
        return self._run("get_mood_playlists", params)

    def get_charts(self, country="ZZ"):
        return self._run("get_charts", country)

    def get_album(self, browseId):
        return self._run("get_album", browseId)

    def get_artist(self, channelId):
        return self._run("get_artist", channelId)

    def get_playlist(self, playlistId, limit=None, related=False,
                     suggestions_limit=0):
        return self._run("get_playlist", playlistId, limit)

    def get_watch_playlist(self, videoId=None, playlistId=None, limit=25,
                           radio=False, shuffle=False):
        return self._run("get_watch_playlist", videoId, limit, radio)

    def get_lyrics(self, browseId, timestamps=False):
        self.calls.append(("get_lyrics", (browseId,),
                           {"timestamps": timestamps}))
        if timestamps:
            return copy.deepcopy(self.timed_lyrics)
        return copy.deepcopy(self.answers.get("get_lyrics"))

    def get_library_playlists(self, limit=25):
        return self._run("get_library_playlists")

    def get_library_songs(self, limit=25, validate_responses=False,
                          order=None):
        return self._run("get_library_songs")

    def get_library_albums(self, limit=25, order=None):
        return self._run("get_library_albums")

    def get_library_artists(self, limit=25, order=None):
        return self._run("get_library_artists")

    def get_liked_songs(self, limit=100):
        return self._run("get_liked_songs")

    def get_history(self):
        return self._run("get_history")

    def rate_song(self, videoId, rating):
        return self._run("rate_song", videoId, rating)


def _engine(tmp_path, authed=False):
    fake = FakeYTMusic()
    auth = {"cookie": "x"} if authed else None
    engine = yt.YouTubeMusicEngine(tmp_path,
                                   catalog.Catalog(auth=auth, client=fake))
    return engine, fake


def test_overrides_keep_contract_signatures():
    for name in CONTRACT:
        base = inspect.signature(getattr(e.Engine, name))
        ours = inspect.signature(getattr(yt.YouTubeMusicEngine, name))
        assert list(base.parameters) == list(ours.parameters), name
    for name in CATALOG_METHODS:
        assert name in yt.YouTubeMusicEngine.__dict__, name
    for name in ("sign_in", "sign_out", "resolve"):
        assert name in yt.YouTubeMusicEngine.__dict__, name
    bare = yt.YouTubeMusicEngine("/tmp")
    for name, args, kind in (
            ("sign_in", ({},), e.ErrorKind.UNSUPPORTED),
            ("sign_out", (), e.ErrorKind.UNSUPPORTED),
            ("resolve", ("x",), e.ErrorKind.HELPER_MISSING)):
        with pytest.raises(e.EngineError) as caught:
            getattr(bare, name)(*args)
        assert caught.value.kind == kind, name


def test_capabilities_are_the_catalog_half():
    assert yt.YouTubeMusicEngine.capabilities == frozenset({
        e.SEARCH, e.SUGGEST, e.HOME, e.MOODS, e.CHARTS, e.LIBRARY, e.RADIO,
        e.LYRICS, e.RATE})
    engine, _ = _engine("/tmp")
    assert engine.key == "ytm" and engine.name == "YouTube Music"
    assert engine.supports(e.SEARCH) and not engine.supports(e.STREAM)
    assert not engine.supports(e.SIGN_IN)


def test_search_uses_server_filter_and_limit(tmp_path):
    engine, fake = _engine(tmp_path)
    fake.answers["search"] = load("search_songs.json")
    results = engine.search("get lucky", e.ItemKind.TRACK, limit=20)
    assert len(results.tracks) == 20
    assert fake.calls[-1][2] == {"filter": "songs", "limit": 20}
    fake.answers["search"] = load("search_all.json")
    engine.search("daft punk")
    assert fake.calls[-1][2] == {"filter": None, "limit": 20}


def test_search_caps_to_limit(tmp_path):
    # ytmusicapi returns a whole page (20) even when asked for 3: seen on
    # the FLX1
    engine, fake = _engine(tmp_path)
    fake.answers["search"] = load("search_songs.json")
    assert len(engine.search("get lucky", e.ItemKind.TRACK, limit=3).tracks) == 3


def test_search_blank_and_mood_make_no_call(tmp_path):
    engine, fake = _engine(tmp_path)
    assert engine.search("   ").empty
    assert engine.search("x", e.ItemKind.MOOD).empty
    assert fake.calls == []
    assert engine.suggestions("  ") == []
    assert fake.calls == []


def test_suggestions_home_moods(tmp_path):
    engine, fake = _engine(tmp_path)
    fake.answers["get_search_suggestions"] = load("search_suggestions.json")
    assert engine.suggestions("daft")[:1] == ["daft punk"]
    fake.answers["get_home"] = load("home.json")
    assert [s.title for s in engine.home()] == [
        "Quick picks", "Today's biggest hits", "Trending community playlists",
        "Throwbacks", "Trending songs for you"]
    fake.answers["get_mood_categories"] = load("mood_categories.json")
    assert len(engine.moods()) == 38
    fake.answers["get_mood_playlists"] = [
        {"playlistId": "PL1", "title": "Chill", "thumbnails": []}]
    shelves = engine.mood_shelves("ggMPOg1uX1JOQWZFeDByc2Jm")
    assert shelves[0].items[0].id == "PL1"
    assert fake.calls[-1] == ("get_mood_playlists",
                              ("ggMPOg1uX1JOQWZFeDByc2Jm",), {})


def test_charts_region_mapping(tmp_path):
    engine, fake = _engine(tmp_path)
    fake.answers["get_charts"] = {
        "countries": {}, "videos": [{"title": "Daily", "playlistId": "PL1",
                                     "thumbnails": []}],
        "artists": [{"title": "A", "browseId": "UC1", "thumbnails": []}]}
    assert [s.title for s in engine.charts()] == ["Top videos", "Top artists"]
    assert fake.calls[-1] == ("get_charts", ("ZZ",), {})
    engine.charts("us")
    assert fake.calls[-1] == ("get_charts", ("US",), {})
    fake.answers["get_charts"] = {"countries": {}, "videos": [],
                                  "artists": []}
    assert engine.charts("XX") == []


def test_pages(tmp_path):
    engine, fake = _engine(tmp_path)
    fake.answers["get_album"] = load("album.json")
    album = engine.album("MPREb_K8qWMWVqXGi")
    assert album.title == "Random Access Memories" and len(album.tracks) == 13
    assert fake.calls[-1][1] == ("MPREb_K8qWMWVqXGi",)
    fake.answers["get_artist"] = load("artist.json")
    artist = engine.artist("UC_kRDKYrUlrbtrSiyu5Tflg")
    assert artist.name == "Daft Punk" and len(artist.top_tracks) == 5
    fake.answers["get_playlist"] = load("playlist.json")
    page = engine.playlist("OLAK5uy_mvGpNqcAglCR8WbCLLHnIitRBdLjho0ZI")
    assert len(page.tracks) == 116
    assert fake.calls[-1][1][:2] == (
        "OLAK5uy_mvGpNqcAglCR8WbCLLHnIitRBdLjho0ZI", 200)


def test_radio_drops_seed_and_excluded(tmp_path):
    engine, fake = _engine(tmp_path)
    fake.answers["get_watch_playlist"] = load("watch_playlist.json")
    tracks = engine.radio("4D7u5KF7SP8", limit=3)
    assert [t.id for t in tracks] == ["iU7oF4OXZSE", "tOaWhRATZqk",
                                      "rnO-MflYxCw"]
    assert fake.calls[-1][1] == ("4D7u5KF7SP8", 4, True)
    tracks = engine.radio("4D7u5KF7SP8", limit=25,
                          exclude=frozenset({"iU7oF4OXZSE"}))
    ids = [t.id for t in tracks]
    assert "4D7u5KF7SP8" not in ids and "iU7oF4OXZSE" not in ids
    assert len(ids) == 25
    assert fake.calls[-1][1] == ("4D7u5KF7SP8", 27, True)


def test_lyrics_plain_timed_and_missing(tmp_path):
    engine, fake = _engine(tmp_path)
    fake.answers["get_watch_playlist"] = load("watch_playlist.json")
    fake.answers["get_lyrics"] = load("lyrics.json")
    lyrics = engine.lyrics("4D7u5KF7SP8")
    assert lyrics is not None and len(lyrics.lines) == 8
    assert ("get_lyrics", ("MPLYt_K8qWMWVqXGi-8",),
            {"timestamps": False}) in fake.calls

    fake.calls.clear()
    fake.answers["get_lyrics"] = {"lyrics": "x", "source": None,
                                  "hasTimestamps": True}
    fake.timed_lyrics = {"lyrics": [{"lyricLine": "hi",
                                     "cueRange": {
                                         "startTimeMilliseconds": "900"}}],
                         "source": None, "hasTimestamps": True}
    lyrics = engine.lyrics("4D7u5KF7SP8")
    assert lyrics is not None and lyrics.synced
    assert ("get_lyrics", ("MPLYt_K8qWMWVqXGi-8",),
            {"timestamps": True}) in fake.calls

    watch = load("watch_playlist.json")
    watch["lyrics"] = None
    fake.answers["get_watch_playlist"] = watch
    fake.calls.clear()
    assert engine.lyrics("4D7u5KF7SP8") is None
    assert all(call[0] != "get_lyrics" for call in fake.calls)


def test_library_needs_sign_in(tmp_path):
    engine, fake = _engine(tmp_path)
    for section in e.LibrarySection:
        with pytest.raises(e.EngineError) as caught:
            engine.library(section)
        assert caught.value.kind == e.ErrorKind.AUTH_REQUIRED
    assert fake.calls == []


def test_library_sections_when_signed_in(tmp_path):
    engine, fake = _engine(tmp_path, authed=True)
    fake.answers["get_library_playlists"] = [
        {"playlistId": "PL1", "title": "Mix", "thumbnails": []}]
    assert engine.library(e.LibrarySection.PLAYLISTS)[0].id == "PL1"
    fake.answers["get_library_songs"] = [SONG]
    songs = engine.library(e.LibrarySection.SONGS)
    assert songs[0].kind == e.ItemKind.TRACK and songs[0].track.id == "v1"
    fake.answers["get_library_albums"] = [
        {"browseId": "MPRE1", "title": "Al", "thumbnails": []}]
    assert engine.library(e.LibrarySection.ALBUMS)[0].id == "MPRE1"
    fake.answers["get_library_artists"] = [
        {"browseId": "UC1", "artist": "A", "thumbnails": []}]
    assert engine.library(e.LibrarySection.ARTISTS)[0].title == "A"
    fake.answers["get_liked_songs"] = {"id": "LM", "tracks": [SONG]}
    assert engine.library(e.LibrarySection.LIKED)[0].id == "v1"
    fake.answers["get_history"] = [SONG]
    assert engine.library(e.LibrarySection.HISTORY)[0].id == "v1"


def test_rate_needs_sign_in_and_maps_rating(tmp_path):
    engine, fake = _engine(tmp_path)
    with pytest.raises(e.EngineError) as caught:
        engine.rate("v1", e.Rating.LIKE)
    assert caught.value.kind == e.ErrorKind.AUTH_REQUIRED
    engine, fake = _engine(tmp_path, authed=True)
    engine.rate("v1", e.Rating.LIKE)
    assert fake.calls[-1][0] == "rate_song"
    assert fake.calls[-1][1][0] == "v1"
    assert fake.calls[-1][1][1] == "LIKE"
    engine.rate("v1", e.Rating.DISLIKE)
    assert fake.calls[-1][1][1] == "DISLIKE"


def test_broken_fixture_counts_toward_update_banner(tmp_path):
    engine, fake = _engine(tmp_path)
    fake.answers["get_home"] = {"not": "shelves"}
    for _ in range(3):
        with pytest.raises(e.EngineError) as caught:
            engine.home()
        assert caught.value.kind == e.ErrorKind.SOURCE_CHANGED
    assert engine.catalog.health.needs_update("home")


def test_art_url_and_status(tmp_path):
    engine, _ = _engine(tmp_path)
    assert engine.art_url("https://img/a=w60-h60-l90-rj", 128).endswith(
        "=w128-h128-l90-rj")
    status = engine.status()
    assert [c.key for c in status.components] == [
        "catalog", "resolver", "solver", "token"]
    assert not status.signed_in
    engine, _ = _engine(tmp_path, authed=True)
    assert engine.status().signed_in
