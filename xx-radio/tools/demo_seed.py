#!/usr/bin/env python3
"""Seed a HOME with a demo xx-radio state through the real stores.

Usage: python3 xx-radio/tools/demo_seed.py OUT_HOME

The catalog is the fake engine's, widened with more invented artists,
albums and songs (placeholder names, no real art: every tile is a
monochrome initials tile). Writes the queue (``Queue.save``), finished
downloads (``CacheIndex``), the Home cache, recent searches, history,
likes and settings (``UiStore``). Also holds the in-process stand-ins
the snap app uses: DemoEngine, DemoAccount, DemoControl, DemoFirstRun.
No sockets, no keyring, no player service. Idempotent.
"""

from __future__ import annotations

import os
import sys
import time

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
LANE_ROOT = os.path.dirname(os.path.dirname(TOOLS_DIR))
sys.path.insert(0, os.path.join(LANE_ROOT, "common"))
sys.path.insert(0, os.path.join(LANE_ROOT, "xx-radio", "src"))

from xx_music import engine as e  # noqa: E402
from xx_music import fake_engine as fe  # noqa: E402

ARTISTS = {
    "ar-lumen": "Lumen Drive",
    "ar-north": "North Hollow",
    "ar-paper": "Paper Satellites",
    "ar-copper": "Copper Tide",
    "ar-low": "Low Season",
    "ar-quiet": "Quiet Engines",
    "ar-velvet": "Velvet Static",
    "ar-marlow": "Marlow & Vane",
}
ALBUMS = {
    "al-tide": ("Tidewater", "ar-lumen", "2024"),
    "al-maps": ("Afterglow Maps", "ar-lumen", "2022"),
    "al-ember": ("Ember Season", "ar-north", "2023"),
    "al-signal": ("Signal", "ar-paper", "2025"),
    "al-salt": ("Salt Lines", "ar-copper", "2021"),
    "al-ferry": ("Late Ferries", "ar-low", "2024"),
    "al-idle": ("Idle Hands", "ar-quiet", "2025"),
    "al-bloom": ("Static Bloom", "ar-velvet", "2023"),
    "al-rooms": ("Two Rooms", "ar-marlow", "2020"),
}
_SONGS = {
    "al-tide": ("Glass Harbor", "Low Orbit", "Night Ferry", "Undertow",
                "Lantern Room"),
    "al-maps": ("Paper Compass", "Coastline Static", "Afterglow"),
    "al-ember": ("Cinder Road", "Hollow Bells", "Kindling", "First Frost"),
    "al-signal": ("Static Garden", "Signal Fires", "Quiet Machines",
                  "Relay"),
    "al-salt": ("Salt Lines", "Breakwater", "Green Flash"),
    "al-ferry": ("Late Ferries", "Harbour Lights Out", "Slow Return",
                 "Deckhand"),
    "al-idle": ("Idle Hands", "Gearbox Lullaby", "Overtime"),
    "al-bloom": ("Static Bloom", "Velvet Hours", "Neon Moth", "Tape Hiss"),
    "al-rooms": ("Two Rooms", "Hallway Song", "The Long Table"),
}
TRACKS = []
_n = 0
for _album, _titles in _SONGS.items():
    for _title in _titles:
        _n += 1
        TRACKS.append((f"tr-{_n:03d}", _title, ALBUMS[_album][1], _album,
                       150 + (_n * 37) % 170))
TRACKS = tuple(TRACKS)
PLAYLISTS = {
    "pl-focus": ("Deep Focus", ("tr-018", "tr-004", "tr-020", "tr-030",
                                "tr-002", "tr-024")),
    "pl-drive": ("Night Drive", ("tr-003", "tr-001", "tr-016", "tr-027",
                                 "tr-011", "tr-028", "tr-005")),
    "pl-coffee": ("Slow Morning", ("tr-021", "tr-026", "tr-033", "tr-012")),
    "pl-run": ("Uphill", ("tr-014", "tr-008", "tr-025", "tr-029")),
    "pl-rain": ("Rain on Glass", ("tr-022", "tr-013", "tr-031", "tr-009")),
}
MOODS = (
    ("mo-chill", "Chill", "Moods"), ("mo-focus", "Focus", "Moods"),
    ("mo-energy", "Energy", "Moods"), ("mo-commute", "Commute", "Moods"),
    ("mo-sleep", "Sleep", "Moods"), ("mo-romance", "Romance", "Moods"),
    ("mo-indie", "Indie", "Genres"), ("mo-electronic", "Electronic",
                                      "Genres"),
    ("mo-folk", "Folk", "Genres"), ("mo-jazz", "Jazz", "Genres"),
    ("mo-ambient", "Ambient", "Genres"),
)
LYRIC_LINES = (
    "Lights on the water, slow and low",
    "Somebody waiting where the ferries go",
    "I kept the ticket in my coat",
    "Folded twice, the way you wrote",
    "",
    "Glass on the harbor, glass in the sky",
    "Every horn a long goodbye",
    "I count the cranes along the quay",
    "One for you and one for me",
    "",
    "Hold the rope, hold the line",
    "Morning comes in its own time",
)

# The fake engine reads these module tables at call time.
fe._ARTISTS = ARTISTS
fe._ALBUMS = ALBUMS
fe._TRACKS = TRACKS
fe._PLAYLISTS = PLAYLISTS
fe._MOODS = MOODS

QUEUE_IDS = ("tr-003", "tr-001", "tr-016", "tr-027", "tr-011", "tr-028",
             "tr-005", "tr-020", "tr-030")
QUEUE_INDEX = 1
DONE_IDS = ("tr-001", "tr-002", "tr-003", "tr-004", "tr-018", "tr-020",
            "tr-021", "tr-026")
ACTIVE_IDS = (("tr-016", 0.42), )
QUEUED_IDS = ("tr-027", "tr-011")
FAILED_IDS = ("tr-031",)
HISTORY_IDS = ("tr-001", "tr-018", "tr-012", "tr-024", "tr-007", "tr-033",
               "tr-015", "tr-029")
LIKED_IDS = ("tr-001", "tr-018", "tr-021", "tr-026", "tr-011")
RECENTS = ("north hollow", "night ferry", "velvet static", "deep focus",
           "lumen")


def track(track_id: str) -> e.Track:
    return fe._track(track_id)


class DemoEngine(fe.FakeEngine):
    """The fake engine with the wider catalog and richer Home shelves."""

    key = "fake"
    name = "YouTube Music"

    def home(self) -> list[e.Shelf]:
        self._enter("home")
        pick = [track(t[0]) for t in TRACKS[::3]][:12]
        return [
            e.Shelf("Quick picks", tuple(fe._track_item(t) for t in pick)),
            e.Shelf("Albums for you", tuple(fe._album_item(a)
                                            for a in ALBUMS)),
            e.Shelf("Mixed for you", tuple(fe._playlist_item(p)
                                           for p in PLAYLISTS)),
            e.Shelf("Artists you might like", tuple(
                fe._artist_item(a) for a in ARTISTS)),
        ]

    def mood_shelves(self, mood_id: str) -> list[e.Shelf]:
        self._enter("mood_shelves")
        lists = tuple(fe._playlist_item(p) for p in PLAYLISTS)
        return [e.Shelf("Playlists", lists),
                e.Shelf("From the community", lists[::-1])]

    def artist(self, artist_id: str) -> e.ArtistPage:
        page = super().artist(artist_id)
        singles = tuple(
            e.Item(e.ItemKind.ALBUM, f"si-{t.id}", t.title,
                   f"{2025 - i} · Single")
            for i, t in enumerate(page.top_tracks[:3]))
        albums = tuple(e.Item(a.kind, a.id, a.title,
                              f"{ALBUMS[a.id][2]} · Album")
                       for a in page.albums)
        return e.ArtistPage(page.id, page.name, "", page.top_tracks, albums,
                            singles, page.related, page.radio_seed)

    def lyrics(self, track_id: str) -> e.Lyrics | None:
        self._enter("lyrics")
        track(track_id)
        return e.Lyrics(tuple(
            e.LyricLine(text, 50000 + 7000 * index)
            for index, text in enumerate(LYRIC_LINES)), "Demo lyrics")


# -- stand-ins for the account manager and the engine set-up ----------------------

class DemoAccount:
    def __init__(self, signed_in: bool = False, oauth: bool = False,
                 fail: bool = False) -> None:
        from xx_radio.account import METHOD_BROWSER, METHOD_OAUTH

        self.signed_in = signed_in
        self.method = METHOD_OAUTH if oauth else METHOD_BROWSER
        self.fail = fail

    def state(self):
        from xx_radio.account import AccountState

        if not self.signed_in:
            return AccountState(False)
        return AccountState(True, self.method, "Demo listener",
                            None, catalog_only=self.method == "oauth")

    def sign_in_with_headers(self, pasted, account_name="", brand_user=None):
        from xx_radio.youtube import auth

        if self.fail:
            raise auth.ValidationError("demo: rejected")
        self.signed_in = True
        return self.state()

    def sign_out(self):
        self.signed_in = False
        return self.state()

    def remember_oauth_client(self, client_id, secret):
        pass

    def start_oauth(self, client_id, secret):
        from xx_radio.youtube import auth

        return auth.DeviceCode("demo-device", "KQVW-HRTD",
                               "https://www.google.com/device")

    def finish_oauth(self, client_id, secret, code, *args, **kwargs):
        time.sleep(600)
        return self.state()


def _components(token_ok: bool = True):
    ok, missing = e.ComponentState.OK, e.ComponentState.MISSING
    return (
        e.Component("catalog", "Catalog", ok, "ytmusicapi 1.12.3",
                    needed_for="Search and browse"),
        e.Component("resolver", "Stream resolver", ok, "yt-dlp 2026.08.19",
                    needed_for="Playback"),
        e.Component("solver", "Challenge solver", ok, "node 24.15.0",
                    needed_for="Playback"),
        e.Component("token", "Token helper", ok if token_ok else missing,
                    "bgutil 1.2.2" if token_ok else "",
                    needed_for="Playback"),
    )


class DemoControl:
    """EngineControl stand-in: states by name, scripted updates."""

    def __init__(self, state: str = "ready") -> None:
        self.state = state

    def snapshot(self):
        from xx_radio import engine_status as es

        packages = (
            es.PackageVersions("ytmusicapi", "1.11.5", "1.12.3"),
            es.PackageVersions("yt-dlp", "2026.06.09", "2026.08.19"),
            es.PackageVersions("yt-dlp-ejs", "", "0.8.0"),
            es.PackageVersions("bgutil-ytdlp-pot-provider", "", "1.2.2"
                               if self.state != "token-missing" else ""),
        )
        state = {"ready": es.READY, "token-missing": es.TOKEN_MISSING,
                 "failed": es.UPDATE_FAILED,
                 "updating": es.READY}[self.state]
        return es.Snapshot(
            state, _components(self.state != "token-missing"), packages,
            venv_active=True, has_previous=True,
            last_error=("could not install packages: network unreachable"
                        if self.state == "failed" else ""),
            can_browse=True, can_play=self.state != "token-missing")

    def update(self, on_progress):
        from xx_radio import engine_status as es

        for step in es.STEPS[:3]:
            on_progress(step)
            time.sleep(0.05)
        time.sleep(600)

    def undo(self):
        pass

    def drop(self):
        pass

    def setup_token(self):
        time.sleep(600)


class DemoFirstRun:
    """FirstRunState stand-in: set-up runs through scripted steps."""

    def __init__(self, outcome: str = "running") -> None:
        self.outcome = outcome

    def snapshot(self):
        from xx_radio import firstrun

        return firstrun.FirstRunSnapshot(firstrun.NEEDS_SETUP)

    def setup_async(self, *, on_progress=None, on_done=None, on_error=None,
                    **_kwargs):
        from gi.repository import GLib

        from xx_radio import engine_status as es

        steps = list(es.STEPS)
        stop = {"running": 3, "failed": 3, "done": len(steps)}[self.outcome]

        def run(index: int = 0) -> bool:
            if index < stop:
                on_progress(steps[index])
                GLib.timeout_add(40, lambda: run(index + 1))
                return False
            if self.outcome == "failed":
                on_error(RuntimeError("could not install packages: "
                                      "network unreachable"))
            elif self.outcome == "done":
                on_done({})
            return False

        GLib.timeout_add(40, run)


def demo_downloads(_ui):
    from xx_music.ui.context import (ACTIVE, DONE, FAILED, QUEUED,
                                     DownloadRow)

    rows = [DownloadRow(track(tid), ACTIVE, frac) for tid, frac in ACTIVE_IDS]
    rows += [DownloadRow(track(tid), QUEUED, 0.0, "Waiting for Wi-Fi")
             for tid in QUEUED_IDS]
    rows += [DownloadRow(track(tid), FAILED, 0.0,
                         "Not available here · tap to retry")
             for tid in FAILED_IDS]
    rows += [DownloadRow(track(tid), DONE, 1.0, "",
                         int((3.1 + i * 0.7) * 2**20))
             for i, tid in enumerate(DONE_IDS)]
    return rows


# -- seeding ------------------------------------------------------------------------

def seed(home: str) -> None:
    for var, sub in (("XDG_CONFIG_HOME", ".config"),
                     ("XDG_DATA_HOME", ".local/share"),
                     ("XDG_CACHE_HOME", ".cache"),
                     ("XDG_STATE_HOME", ".local/state")):
        os.environ[var] = os.path.join(home, sub)
    os.environ["HOME"] = home
    from xx_music.cache import CacheEntry, CacheIndex
    from xx_music.queue import Queue
    from xx_music.ui.home import fetch_home
    from xx_music.ui.store import UiStore
    from xx_radio.main import SPEC

    paths = SPEC.paths()
    paths.ensure_app_dirs()
    queue = Queue()
    queue.replace([track(t) for t in QUEUE_IDS], QUEUE_INDEX,
                  {"type": "playlist", "id": "pl-drive",
                   "title": "Night Drive"})
    queue.save(paths.queue_path())
    index = CacheIndex(paths.db_path())
    for i, tid in enumerate(DONE_IDS):
        song = track(tid)
        index.upsert(CacheEntry(
            tid, "fake", song.title, song.artist_line,
            song.album.title if song.album else "", song.duration_s,
            int((3.1 + i * 0.7) * 2**20), "audio/ogg", True,
            cached_at=1792000000.0 - i * 3600))
    index.close()
    store = UiStore(paths, "fake")
    store.save_home(fetch_home(DemoEngine()))
    for query in reversed(RECENTS):
        store.add_recent(query)
    for tid in reversed(HISTORY_IDS):
        store.note_played(track(tid))
    for tid in LIKED_IDS:
        store.set_rating(track(tid), e.Rating.LIKE)
    store.set_pref("storage_cap_gb", 2)
    store.set_pref("first_run_done", True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: demo_seed.py OUT_HOME")
    seed(os.path.abspath(sys.argv[1]))
    print("seeded", sys.argv[1])
