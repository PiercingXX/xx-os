"""radio_wire: YouTube wiring, first run, service entry and GUI side.

No network, no real accounts, no real system: fixtures and fakes only.
The live D-Bus round trip lives in test_wire_bus.py.
"""

import datetime
import hashlib
import io
import json
import os
import stat
import tarfile
from pathlib import Path

import pytest

from xx_music import engine as e
from xx_radio import firstrun, service as service_mod
from xx_radio import setup
from xx_radio.account import AccountManager
from xx_radio.main import SPEC
from xx_radio.youtube import catalog, engine as yt
from xx_radio.youtube import probe, resolver
from xx_radio.youtube.auth import FakeSecretStore

FIXTURES = Path(__file__).parent / "fixtures" / "ytmusic"
AUTH_FIXTURES = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeYTMusic:
    def __init__(self):
        self.calls = []
        self.answers = {}

    def _run(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        import copy
        return copy.deepcopy(self.answers.get(name))

    def search(self, query, filter=None, scope=None, limit=20,
               ignore_spelling=False):
        return self._run("search", query, filter=filter, limit=limit)

    def get_library_playlists(self, limit=25):
        return self._run("get_library_playlists")

    def get_library_songs(self, limit=25, validate_responses=False,
                          order=None):
        return self._run("get_library_songs")

    def get_liked_songs(self, limit=100):
        return self._run("get_liked_songs")

    def get_history(self):
        return self._run("get_history")

    def rate_song(self, videoId, rating):
        return self._run("rate_song", videoId, rating)


class FakeAccountClient:
    def __init__(self):
        self.calls = []
        self.songs = []
        self.rated = []

    def get_library_playlists(self, limit=None):
        self.calls.append(("get_library_playlists", limit))
        return [{"playlistId": "PL1", "title": "Mix", "thumbnails": []}]

    def get_library_songs(self, limit=25):
        self.calls.append(("get_library_songs", limit))
        return list(self.songs)[:limit]

    def get_library_albums(self, limit=25):
        self.calls.append(("get_library_albums", limit))
        return []

    def get_library_artists(self, limit=25):
        self.calls.append(("get_library_artists", limit))
        return []

    def get_liked_songs(self, limit=25):
        self.calls.append(("get_liked_songs", limit))
        return {"id": "LM", "tracks": list(self.songs)[:limit]}

    def get_history(self):
        self.calls.append(("get_history",))
        return []

    def rate_song(self, videoId, rating):
        self.calls.append(("rate_song", videoId, rating))
        self.rated.append((videoId, rating))


def _ok_result(track_id):
    import time
    return {"id": track_id, "ok": True,
            "uri": f"https://upstream.example/{track_id}",
            "headers": {}, "mime": "audio/webm",
            "expires_at": time.time() + 3600,
            "size_bytes": 100, "gain_db": None}


def _wired(tmp_path, with_account=False):
    fake = FakeYTMusic()
    seen = []

    def run(payload):
        seen.append(payload)
        return {"results": [_ok_result(item["id"])
                            for item in payload["items"]]}

    wired = resolver.Resolver(tmp_path / "engine", run=run)
    if with_account:
        client = FakeAccountClient()
        account = AccountManager(
            FakeSecretStore(), tmp_path / "run",
            lambda headers, user: client)
    else:
        client, account = None, None
    engine = yt.YouTubeMusicEngine(
        tmp_path / "engine", catalog.Catalog(client=fake),
        resolver=wired, account=account)
    return engine, fake, wired, seen, client, account


def test_stream_and_sign_in_advertised_only_when_wired(tmp_path):
    bare = yt.YouTubeMusicEngine(tmp_path)
    assert not bare.supports(e.STREAM) and not bare.supports(e.SIGN_IN)
    assert yt.YouTubeMusicEngine.capabilities == yt.CAPABILITIES
    engine, _, _, _, _, _ = _wired(tmp_path)
    assert engine.supports(e.STREAM) and not engine.supports(e.SIGN_IN)
    engine, _, _, _, _, account = _wired(tmp_path, with_account=True)
    assert account is not None
    assert engine.supports(e.STREAM) and engine.supports(e.SIGN_IN)


def test_resolve_without_resolver_is_helper_missing(tmp_path):
    engine = yt.YouTubeMusicEngine(tmp_path)
    with pytest.raises(e.EngineError) as caught:
        engine.resolve("v1")
    assert caught.value.kind == e.ErrorKind.HELPER_MISSING


def test_catalog_works_while_playback_needs_setup(tmp_path):
    engine, fake, _, _, _, _ = _wired(tmp_path)
    fake.answers["search"] = _load("search_songs.json")
    assert len(engine.search("get lucky", limit=3).tracks) == 3
    bare = yt.YouTubeMusicEngine(
        tmp_path, catalog.Catalog(client=fake))
    with pytest.raises(e.EngineError) as caught:
        bare.resolve("v1")
    assert caught.value.kind == e.ErrorKind.HELPER_MISSING


def test_resolve_delegates_to_fake_resolver_no_network(tmp_path):
    engine, _, _, seen, _, _ = _wired(tmp_path)
    source = engine.resolve("song-1")
    assert source.uri == "https://upstream.example/song-1"
    assert seen and seen[0]["items"] == [{"id": "song-1",
                                          "low_data": False}]
    engine.resolve("song-2", low_data=True)
    assert seen[-1]["items"] == [{"id": "song-2", "low_data": True}]


def test_sign_in_library_and_rate_via_account(tmp_path):
    engine, _, _, _, client, _ = _wired(tmp_path, with_account=True)
    pasted = (AUTH_FIXTURES / "auth_headers_block.txt").read_text()
    name = engine.sign_in({"paste": pasted, "account": "Tube"})
    assert name == "Tube"
    assert engine.status().signed_in
    assert engine.supports(e.SIGN_IN)
    playlists = engine.library(e.LibrarySection.PLAYLISTS)
    assert playlists and playlists[0].id == "PL1"
    engine.rate("v1", e.Rating.LIKE)
    assert ("rate_song", "v1", "LIKE") in client.calls
    engine.sign_out()
    assert not engine.status().signed_in
    with pytest.raises(e.EngineError) as caught:
        engine.library(e.LibrarySection.PLAYLISTS)
    assert caught.value.kind == e.ErrorKind.AUTH_REQUIRED


def test_sign_in_without_account_stays_unsupported(tmp_path):
    engine, _, _, _, _, _ = _wired(tmp_path)
    with pytest.raises(e.EngineError) as caught:
        engine.sign_in({"paste": "Cookie: x"})
    assert caught.value.kind == e.ErrorKind.UNSUPPORTED


def test_create_wires_resolver_and_account_without_io(tmp_path,
                                                     monkeypatch):
    from xx_music.appspec import EngineContext

    monkeypatch.setenv("HOME", str(tmp_path))
    for var, sub in (("XDG_DATA_HOME", ".local/share"),
                     ("XDG_CACHE_HOME", ".cache"),
                     ("XDG_STATE_HOME", ".local/state"),
                     ("XDG_CONFIG_HOME", ".config")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    context = EngineContext(SPEC, SPEC.paths(), "player")
    engine = yt.create(context)
    assert engine.resolver is not None and engine.account is not None
    assert engine.supports(e.STREAM) and engine.supports(e.SIGN_IN)


# -- first run -------------------------------------------------------------


def _tarball(payload=b"fake-uv-binary"):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        info = tarfile.TarInfo("uv-9.9/uv")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.rules = []

    def when(self, *prefix, result=None, side=None):
        self.rules.append((tuple(prefix), result, side))
        return self

    def __call__(self, args, *, cwd=None):
        self.calls.append((list(args), cwd))
        for prefix, result, side in self.rules:
            if tuple(args[:len(prefix)]) == prefix:
                if side is not None:
                    side(args, cwd)
                return result or setup.RunResult(tuple(args))
        raise AssertionError(f"unexpected command: {args}")


def _fake_venv(path):
    python = path / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("#!/bin/sh\n")
    os.chmod(python, 0o755)
    (path / "pyvenv.cfg").write_text("home = /usr\n")


def _ready_installed():
    return probe.Installed(
        catalog="1.12.3", resolver="2026.08.19", ejs="1.0",
        token_plugin="1.2.2", token_generator=True,
        runtimes=(probe.Runtime("node", "/usr/bin/node", (24, 0, 0)),))


def test_firstrun_needs_setup_without_venv(tmp_path):
    state = firstrun.FirstRunState(
        tmp_path / "engine", probe_fn=_ready_installed,
        today=datetime.date(2026, 10, 17),
        system={name: "" for name in setup.ENGINE_PACKAGES})
    assert state.venv_present() is False
    snap = state.snapshot()
    assert snap.state == firstrun.NEEDS_SETUP
    assert not snap.healthy and not snap.venv_active


def test_firstrun_ready_with_healthy_venv(tmp_path):
    engine_dir = tmp_path / "engine"
    _fake_venv(engine_dir / "venv")
    runner = FakeRunner()
    live_python = str(setup.venv_python(engine_dir / "venv"))
    runner.when(live_python, "-c", result=setup.RunResult(
        (), 0, "ytmusicapi 1.12.3\nyt-dlp 2026.08.19\n"
        "yt-dlp-ejs 0.8.0\nbgutil-ytdlp-pot-provider 1.2.2\n", ""))
    state = firstrun.FirstRunState(
        engine_dir, runner=runner, probe_fn=_ready_installed,
        today=datetime.date(2026, 10, 17),
        system={name: "" for name in setup.ENGINE_PACKAGES})
    snap = state.snapshot()
    assert snap.state == firstrun.READY
    assert snap.healthy and snap.venv_active


def test_firstrun_setup_async_drives_setup_on_worker(tmp_path):
    engine_dir = tmp_path / "engine"
    runner = FakeRunner()
    versions_out = ("ytmusicapi 1.12.3\nyt-dlp 2026.08.19\n"
                    "yt-dlp-ejs 0.8.0\nbgutil-ytdlp-pot-provider 1.2.2\n")

    def make_venv(args, cwd):
        _fake_venv(setup.new_venv_dir(engine_dir))

    runner.when(str(setup.uv_path(engine_dir)), "venv", side=make_venv)
    runner.when(str(setup.uv_path(engine_dir)), "pip")
    runner.when(str(setup.venv_python(setup.new_venv_dir(engine_dir))),
                "-c", result=setup.RunResult((), 0, versions_out, ""))
    runner.when(str(setup.venv_python(setup.venv_dir(engine_dir))),
                "-c", result=setup.RunResult((), 0, versions_out, ""))
    data = _tarball()

    def fetch(url):
        return data
    checksums = {setup.platform_tag(): hashlib.sha256(data).hexdigest()}
    state = firstrun.FirstRunState(
        engine_dir, runner=runner, fetch=fetch,
        probe_fn=_ready_installed,
        today=datetime.date(2026, 10, 17),
        system={name: "" for name in setup.ENGINE_PACKAGES})
    from gi.repository import GLib
    loop, done, failed = GLib.MainLoop(), {}, {}

    def on_done(versions):
        done.update(versions)
        loop.quit()

    def on_error(exc):
        failed["error"] = exc
        loop.quit()

    state.setup_async(on_done=on_done, on_error=on_error,
                      uv_checksums=checksums)
    GLib.timeout_add_seconds(30, loop.quit)
    loop.run()
    assert not failed, failed
    assert done.get("ytmusicapi") == "1.12.3"
    assert state.snapshot().state == firstrun.READY


def test_firstrun_snapshot_async_lands_on_main_loop(tmp_path):
    import threading
    from gi.repository import GLib
    state = firstrun.FirstRunState(
        tmp_path / "engine", probe_fn=_ready_installed,
        today=datetime.date(2026, 10, 17),
        system={name: "" for name in setup.ENGINE_PACKAGES})
    loop, got = GLib.MainLoop(), []

    def callback(snap):
        got.append((snap.state,
                    threading.current_thread() is threading.main_thread()))
        loop.quit()

    state.snapshot_async(callback)
    GLib.timeout_add_seconds(10, loop.quit)
    loop.run()
    assert got and got[0][0] == firstrun.NEEDS_SETUP and got[0][1] is True


# -- service entry ----------------------------------------------------------


def test_service_builds_dirs_and_wired_engine(tmp_path, monkeypatch):
    from xx_music.fake_engine import FakeEngine

    from player_fakes import FakeProxy, fakesink
    monkeypatch.setenv("HOME", str(tmp_path))
    for var, sub in (("XDG_DATA_HOME", ".local/share"),
                     ("XDG_CACHE_HOME", ".cache"),
                     ("XDG_STATE_HOME", ".local/state"),
                     ("XDG_CONFIG_HOME", ".config")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("XX_RADIO_AUDIOSINK", "fakesink")
    media = tmp_path / "media"
    media.mkdir()
    engine = FakeEngine(media_dir=media)
    proxy = FakeProxy({})
    service = service_mod.build_service(SPEC, engine=engine, proxy=proxy,
                                        audio_sink=fakesink())
    try:
        assert service.engine is engine
        assert service.proxy is proxy
        paths = SPEC.paths()
        assert paths.audio_cache_dir().is_dir()
        assert paths.engine_dir().is_dir()
        assert stat.S_IMODE(paths.data_dir().stat().st_mode) == 0o700
        assert service.check_idle(False, service._clock() + 61) == "exit"
    finally:
        service.request_stop()


def test_service_activation_file_matches_owned_names():
    text = (Path(__file__).parent.parent / "data"
            / "io.piercingxx.XXRadio.Player.service.in").read_text()
    name = next(line.split("=", 1)[1].strip() for line in text.splitlines()
                if line.startswith("Name="))
    assert name == SPEC.player_bus_name != SPEC.app_id


def test_player_service_entry_routes_to_wired_service(monkeypatch):
    import xx_radio.main as main_mod
    seen = {}

    def fake_main(spec):
        seen["spec"] = spec
        return 7

    monkeypatch.setattr(service_mod, "main", fake_main)
    assert main_mod.main(["--player-service"]) == 7
    assert seen["spec"] is SPEC


def test_gui_side_starts_async_and_keeps_placeholder():
    import ast
    from xx_radio import main as main_mod

    assert main_mod.SPEC.tab_keys() == ["home", "search", "library",
                                        "settings"]
    text = Path(main_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(text)
    calls = [getattr(node.func, "attr", "")
             for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert "bus_get" in calls
    for banned in ("call_sync", "check_output", "subprocess.run",
                   "time.sleep"):
        assert banned not in text
    assert "PlayerClient" in text
    assert "build_window" in text
