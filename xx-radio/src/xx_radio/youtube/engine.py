"""The YouTube Music engine (unofficial; see README.md).

Catalog over ytmusicapi plus stream resolution over the resolver child
and sign-in over the account manager. Pass a resolver to advertise
``STREAM`` and an account manager to advertise ``SIGN_IN``; without
them ``resolve`` reports ``HELPER_MISSING`` and sign-in stays
``UNSUPPORTED``, while the catalog keeps working on system packages.

Constructing the engine does no I/O and imports neither ytmusicapi nor
yt-dlp; every blocking call runs on the caller's worker thread.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

from xx_music import engine as e
from xx_radio.youtube import art, catalog, parse, probe

CAPABILITIES = frozenset({
    e.SEARCH, e.SUGGEST, e.HOME, e.MOODS, e.CHARTS, e.LIBRARY, e.RADIO,
    e.LYRICS, e.RATE,
})

_SEARCH_FILTER = {
    e.ItemKind.TRACK: "songs",
    e.ItemKind.ALBUM: "albums",
    e.ItemKind.ARTIST: "artists",
    e.ItemKind.PLAYLIST: "playlists",
}


def _like_status(rating: e.Rating) -> Any:
    try:
        from ytmusicapi.models.content.enums import LikeStatus
    except ImportError:
        return {"like": "LIKE", "dislike": "DISLIKE"}.get(rating.value,
                                                          "INDIFFERENT")
    return {e.Rating.LIKE: LikeStatus.LIKE,
            e.Rating.DISLIKE: LikeStatus.DISLIKE}.get(
                rating, LikeStatus.INDIFFERENT)


class YouTubeMusicEngine(e.Engine):
    key = "ytm"
    name = "YouTube Music"
    capabilities = CAPABILITIES

    def __init__(self, engine_dir: Path | str,
                 catalog_client: catalog.Catalog | None = None,
                 resolver: Any | None = None,
                 account: Any | None = None) -> None:
        self.engine_dir = Path(engine_dir)
        self.catalog = (catalog_client if catalog_client is not None
                        else catalog.Catalog())
        self.resolver = resolver
        self.account = account
        caps = set(CAPABILITIES)
        if resolver is not None:
            caps.add(e.STREAM)
        if account is not None:
            caps.add(e.SIGN_IN)
        self.capabilities = frozenset(caps)

    def _account_signed_in(self) -> bool:
        if self.account is None:
            return False
        try:
            return bool(self.account.state().signed_in)
        except Exception:
            return False

    def _is_signed_in(self) -> bool:
        return bool(self.catalog.signed_in or self._account_signed_in())

    def status(self) -> e.EngineStatus:
        try:
            found = probe.probe(self.engine_dir)
            status = probe.status_from(found, _dt.date.today())
        except Exception:
            status = probe.status_from(probe.Installed(), _dt.date.today())
        return e.EngineStatus(
            can_browse=status.can_browse, can_play=status.can_play,
            signed_in=self._is_signed_in(), components=status.components)

    def sign_in(self, credentials: dict[str, str]) -> str:
        if self.account is None:
            self._unsupported("sign_in")
        try:
            pasted = (credentials.get("paste") or credentials.get("headers")
                      or credentials.get("cookie") or "")
            label = (credentials.get("account")
                     or credentials.get("account_name") or "")
            brand = credentials.get("brand_user")
            state = self.account.sign_in_with_headers(pasted, label, brand)
        except e.EngineError:
            raise
        except Exception as exc:
            raise e.EngineError(e.ErrorKind.OTHER, str(exc)[:200]) from exc
        return state.account_name

    def sign_out(self) -> None:
        if self.account is None:
            self._unsupported("sign_out")
        try:
            self.account.sign_out()
        except e.EngineError:
            raise
        except Exception as exc:
            raise e.EngineError(e.ErrorKind.OTHER, str(exc)[:200]) from exc

    def resolve(self, track_id: str, *, low_data: bool = False
                ) -> e.StreamSource:
        if self.resolver is None:
            raise e.EngineError(e.ErrorKind.HELPER_MISSING,
                                "youtube: playback engine is not set up")
        try:
            if self.account is not None:
                signed = self._account_signed_in()
                try:
                    self.resolver.signed_in = signed
                except AttributeError:
                    pass
                if signed:
                    try:
                        self.account.ensure_cookie_jar()
                    except Exception:
                        pass
            return self.resolver.resolve(track_id, low_data=low_data)
        except e.EngineError:
            raise
        except Exception as exc:
            raise e.EngineError(e.ErrorKind.OTHER, str(exc)[:200]) from exc

    def search(self, query: str, kind: e.ItemKind | None = None,
               limit: int = 20) -> e.SearchResults:
        if not query.strip():
            return e.SearchResults()
        if kind == e.ItemKind.MOOD:
            return e.SearchResults()
        found = self.catalog.fetch(
            e.SEARCH, "search", parse.parse_search, query,
            filter=_SEARCH_FILTER.get(kind) if kind is not None else None,
            limit=limit)
        # ytmusicapi's limit is a page-size floor, not a cap
        return e.SearchResults(
            tracks=found.tracks[:limit], albums=found.albums[:limit],
            artists=found.artists[:limit], playlists=found.playlists[:limit])

    def suggestions(self, query: str) -> list[str]:
        if not query.strip():
            return []
        return self.catalog.fetch(e.SUGGEST, "get_search_suggestions",
                                  parse.parse_suggestions, query)

    def home(self) -> list[e.Shelf]:
        return self.catalog.fetch(e.HOME, "get_home", parse.parse_home)

    def moods(self) -> list[e.Mood]:
        return self.catalog.fetch(e.MOODS, "get_mood_categories",
                                  parse.parse_moods)

    def mood_shelves(self, mood_id: str) -> list[e.Shelf]:
        return self.catalog.fetch(e.MOODS, "get_mood_playlists",
                                  parse.parse_mood_playlists, mood_id)

    def charts(self, region: str = "") -> list[e.Shelf]:
        country = region.strip().upper() or "ZZ"
        return self.catalog.fetch(e.CHARTS, "get_charts", parse.parse_charts,
                                  country)

    def _library_via_account(
            self, section: e.LibrarySection) -> list[e.Item] | None:
        if self.account is None or self.catalog.signed_in:
            return None
        if not self._account_signed_in():
            return None
        try:
            raw = self.account.fetch_library(section, limit=100)
        except e.EngineError:
            raise
        except Exception as exc:
            raise e.EngineError(e.ErrorKind.OTHER, str(exc)[:200]) from exc
        try:
            if section == e.LibrarySection.PLAYLISTS:
                return parse.parse_library_playlists(raw)
            if section == e.LibrarySection.SONGS:
                tracks = parse.parse_library_songs(raw)
                return [e.Item(e.ItemKind.TRACK, t.id, t.title,
                               t.artist_line, t.art_url, t) for t in tracks]
            if section == e.LibrarySection.ALBUMS:
                return parse.parse_library_albums(raw)
            if section == e.LibrarySection.ARTISTS:
                return parse.parse_library_artists(raw)
            if section == e.LibrarySection.LIKED:
                tracks = parse.parse_liked_songs(raw)
                return [e.Item(e.ItemKind.TRACK, t.id, t.title,
                               t.artist_line, t.art_url, t) for t in tracks]
            return parse.parse_history(raw)
        except e.EngineError as exc:
            self.catalog.health.note_failure(e.LIBRARY, exc.kind)
            raise
        except Exception as exc:
            self.catalog.health.note_failure(e.LIBRARY, e.ErrorKind.OTHER)
            raise e.EngineError(e.ErrorKind.OTHER,
                                f"youtube: library: {exc!r}") from exc

    def library(self, section: e.LibrarySection) -> list[e.Item]:
        via_account = self._library_via_account(section)
        if via_account is not None:
            self.catalog.health.note_success(e.LIBRARY)
            return via_account
        if not self.catalog.signed_in:
            raise e.EngineError(e.ErrorKind.AUTH_REQUIRED, "youtube: library")
        if section == e.LibrarySection.PLAYLISTS:
            return self.catalog.fetch(e.LIBRARY, "get_library_playlists",
                                      parse.parse_library_playlists, limit=100)
        if section == e.LibrarySection.SONGS:
            tracks = self.catalog.fetch(e.LIBRARY, "get_library_songs",
                                        parse.parse_library_songs, limit=100)
            return [e.Item(e.ItemKind.TRACK, t.id, t.title, t.artist_line,
                           t.art_url, t) for t in tracks]
        if section == e.LibrarySection.ALBUMS:
            return self.catalog.fetch(e.LIBRARY, "get_library_albums",
                                      parse.parse_library_albums, limit=100)
        if section == e.LibrarySection.ARTISTS:
            return self.catalog.fetch(e.LIBRARY, "get_library_artists",
                                      parse.parse_library_artists, limit=100)
        if section == e.LibrarySection.LIKED:
            tracks = self.catalog.fetch(e.LIBRARY, "get_liked_songs",
                                        parse.parse_liked_songs, limit=100)
            return [e.Item(e.ItemKind.TRACK, t.id, t.title, t.artist_line,
                           t.art_url, t) for t in tracks]
        tracks = self.catalog.fetch(e.LIBRARY, "get_history",
                                    parse.parse_history)
        return tracks

    def album(self, album_id: str) -> e.AlbumPage:
        return self.catalog.fetch(
            e.SEARCH, "get_album", lambda raw: parse.parse_album(raw,
                                                                 album_id),
            browseId=album_id)

    def artist(self, artist_id: str) -> e.ArtistPage:
        return self.catalog.fetch(
            e.SEARCH, "get_artist", lambda raw: parse.parse_artist(
                raw, artist_id), channelId=artist_id)

    def playlist(self, playlist_id: str, limit: int = 200) -> e.PlaylistPage:
        return self.catalog.fetch(
            e.SEARCH, "get_playlist", lambda raw: parse.parse_playlist(
                raw, playlist_id), playlist_id, limit)

    def radio(self, seed_track_id: str, limit: int = 25,
              exclude: frozenset[str] = frozenset()) -> list[e.Track]:
        tracks = self.catalog.fetch(
            e.RADIO, "get_watch_playlist", parse.parse_watch_playlist,
            videoId=seed_track_id, radio=True,
            limit=limit + len(exclude) + 1)
        return [t for t in tracks
                if t.id != seed_track_id and t.id not in exclude][:limit]

    def lyrics(self, track_id: str) -> e.Lyrics | None:
        browse_id = self.catalog.fetch(
            e.LYRICS, "get_watch_playlist", parse.parse_watch_lyrics_id,
            videoId=track_id, limit=1)
        if browse_id is None:
            return None
        raw = self.catalog.fetch(e.LYRICS, "get_lyrics", lambda got: got,
                                 browse_id)
        if isinstance(raw, dict) and raw.get("hasTimestamps"):
            return self.catalog.fetch(e.LYRICS, "get_lyrics",
                                      parse.parse_lyrics, browse_id,
                                      timestamps=True)
        try:
            result = parse.parse_lyrics(raw)
        except e.EngineError as exc:
            self.catalog.health.note_failure(e.LYRICS, exc.kind)
            raise
        self.catalog.health.note_success(e.LYRICS)
        return result

    def rate(self, track_id: str, rating: e.Rating) -> None:
        if not self.catalog.signed_in and self._account_signed_in():
            try:
                self.account.set_rating(track_id, rating)
            except e.EngineError:
                raise
            except Exception as exc:
                raise e.EngineError(e.ErrorKind.OTHER,
                                    str(exc)[:200]) from exc
            self.catalog.health.note_success(e.RATE)
            return
        if not self.catalog.signed_in:
            raise e.EngineError(e.ErrorKind.AUTH_REQUIRED, "youtube: rate")
        self.catalog.fetch(e.RATE, "rate_song", lambda got: None, track_id,
                           _like_status(rating))

    def art_url(self, url: str, px: int) -> str:
        return art.sized(url, px)


def create(context) -> YouTubeMusicEngine:
    """Engine factory named by xx_radio.main.SPEC."""
    from xx_radio.account import AccountManager
    from xx_radio.youtube import resolver as resolver_mod
    from xx_radio.youtube.auth import LibsecretStore

    paths = context.paths
    engine_dir = paths.engine_dir()
    app_runtime = paths.runtime_dir()
    if app_runtime.name == paths.dir_name and app_runtime.parent != app_runtime:
        account_runtime = app_runtime.parent
    else:
        account_runtime = app_runtime
    try:
        helper_ready = probe.generator_present(engine_dir)
    except Exception:
        helper_ready = False
    account = AccountManager(LibsecretStore(), account_runtime)
    wired = resolver_mod.Resolver(
        engine_dir, runtime_dir=app_runtime, signed_in=False,
        helper_ready=bool(helper_ready))
    return YouTubeMusicEngine(engine_dir, resolver=wired, account=account)
