"""Playback engine screen backend: states, versions, update/undo/drop."""

import datetime
import hashlib
import io
import os
import tarfile

import pytest

from xx_radio import engine_status, setup
from xx_radio.engine_status import STEPS, PlaybackEngine
from xx_radio.setup import RunResult, SetupError
from xx_radio.youtube import probe


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
                return result or RunResult(tuple(args))
        raise AssertionError(f"unexpected command: {args}")


def _fake_venv(path):
    python = path / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("#!/bin/sh\n")
    os.chmod(python, 0o755)
    (path / "pyvenv.cfg").write_text("home = /usr\n")


def _wire_update(engine, runner,
                 versions="ytmusicapi 1.12.3\nyt-dlp 2026.08.19\n"):
    def make_venv(args, cwd):
        _fake_venv(setup.new_venv_dir(engine))

    runner.when(str(setup.uv_path(engine)), "venv", side=make_venv)
    runner.when(str(setup.uv_path(engine)), "pip")
    runner.when(str(setup.venv_python(setup.new_venv_dir(engine))), "-c",
                result=RunResult((), 0, versions, ""))
    return runner


def _fetch(data):
    def fetch(url):
        return data
    fetch.data = data
    return fetch


def _checksums(data):
    return {setup.platform_tag(): hashlib.sha256(data).hexdigest()}


def _ready_installed():
    return probe.Installed(
        catalog="1.12.3", resolver="2026.08.19", ejs="1.0",
        token_plugin="1.2.2", token_generator=True,
        runtimes=(probe.Runtime("node", "/usr/bin/node", (24, 0, 0)),))


def _engine(tmp_path, **kwargs):
    kwargs.setdefault("probe_fn", _ready_installed)
    kwargs.setdefault("today", datetime.date(2026, 10, 17))
    kwargs.setdefault("system", {name: "" for name in setup.ENGINE_PACKAGES})
    return PlaybackEngine(tmp_path / "engine", **kwargs)


def test_snapshot_ready_lists_system_and_engine_versions(tmp_path):
    runner = FakeRunner()
    engine_dir = tmp_path / "engine"
    _fake_venv(engine_dir / "venv")
    live_python = str(setup.venv_python(engine_dir / "venv"))
    runner.when(live_python, "-c",
                result=RunResult((), 0, "ytmusicapi 9.9.9\n", ""))
    engine = _engine(tmp_path, runner=runner,
                     system={"ytmusicapi": "1.12.3", "yt-dlp": "2026.08.19",
                             "yt-dlp-ejs": "", "bgutil-ytdlp-pot-provider": ""})
    snap = engine.snapshot()
    assert snap.state == engine_status.READY
    assert snap.venv_active
    assert not snap.has_previous
    assert snap.can_play and snap.can_browse
    by_name = {package.name: package for package in snap.packages}
    assert by_name["ytmusicapi"].system == "1.12.3"
    assert by_name["ytmusicapi"].engine == "9.9.9"
    assert by_name["ytmusicapi"].active == "9.9.9"
    assert by_name["yt-dlp"].active == "2026.08.19"


def test_snapshot_token_missing_without_helper(tmp_path):
    found = probe.Installed(
        catalog="1.12.3", resolver="2026.08.19", ejs="1.0",
        runtimes=(probe.Runtime("node", "/usr/bin/node", (24, 0, 0)),))
    engine = _engine(tmp_path, probe_fn=lambda: found)
    snap = engine.snapshot()
    assert snap.state == engine_status.TOKEN_MISSING
    assert not snap.can_play
    assert [part.key for part in snap.components] == [
        "catalog", "resolver", "solver", "token"]


def test_snapshot_never_raises_on_probe_failure(tmp_path):
    def boom():
        raise OSError("no dist info")

    snap = _engine(tmp_path, probe_fn=boom).snapshot()
    assert snap.state == engine_status.TOKEN_MISSING
    assert snap.last_error


def test_update_end_to_end_reports_each_step(tmp_path):
    runner = FakeRunner()
    data = _tarball()
    engine = _engine(tmp_path, runner=runner, fetch=_fetch(data))
    _wire_update(tmp_path / "engine", runner)
    seen_online = []

    steps = []
    versions = engine.update(
        on_progress=steps.append,
        online_check=lambda python: seen_online.append(python),
        uv_checksums=_checksums(data))
    assert steps == list(STEPS)
    assert versions == {"ytmusicapi": "1.12.3", "yt-dlp": "2026.08.19"}
    assert seen_online == [setup.venv_python(
        setup.new_venv_dir(engine._engine_dir))]
    snap = engine.snapshot()
    assert snap.state == engine_status.READY
    assert snap.venv_active
    assert not snap.last_error


def test_failed_update_keeps_live_engine_and_reports_failed(tmp_path):
    engine_dir = tmp_path / "engine"
    _fake_venv(engine_dir / "venv")
    (engine_dir / "venv" / "marker.txt").write_text("live")
    runner = FakeRunner()
    data = _tarball()
    engine = _engine(tmp_path, runner=runner, fetch=_fetch(data))
    _wire_update(engine_dir, runner)
    runner.rules = [rule for rule in runner.rules
                    if rule[0][:2] != (str(setup.uv_path(engine_dir)), "pip")]
    runner.when(str(setup.uv_path(engine_dir)), "pip",
                result=RunResult((), 1, "", "index unreachable"))
    with pytest.raises(SetupError):
        engine.update(on_progress=lambda _step: None,
                      uv_checksums=_checksums(data))
    snap = engine.snapshot()
    assert snap.state == engine_status.UPDATE_FAILED
    assert "index unreachable" in snap.last_error
    assert (engine_dir / "venv" / "marker.txt").read_text() == "live"
    assert not setup.prev_venv_dir(engine_dir).exists()


def test_undo_and_drop_clear_state(tmp_path):
    engine_dir = tmp_path / "engine"
    _fake_venv(engine_dir / "venv")
    _fake_venv(engine_dir / "venv.new")
    engine = _engine(tmp_path)
    setup.promote(engine_dir)
    assert setup.prev_venv_dir(engine_dir).is_dir()
    engine.undo()
    assert setup.venv_ok(engine_dir / "venv")
    engine.drop_to_system()
    snap = engine.snapshot()
    assert not snap.venv_active
    assert snap.state in (engine_status.READY, engine_status.TOKEN_MISSING)


def test_concurrent_update_is_refused(tmp_path):
    engine = _engine(tmp_path)
    engine._updating = True
    with pytest.raises(SetupError):
        engine.update()


def test_snapshot_async_delivers_on_the_main_loop(tmp_path, monkeypatch):
    import threading
    from gi.repository import GLib
    from xx_radio import engine_status
    engine = engine_status.PlaybackEngine(tmp_path)
    marker = object()
    monkeypatch.setattr(engine, "snapshot", lambda: marker)
    loop, got = GLib.MainLoop(), []

    def done(snap):
        got.append((snap, threading.current_thread() is threading.main_thread()))
        loop.quit()

    engine.snapshot_async(done)
    GLib.timeout_add_seconds(5, loop.quit)
    loop.run()
    assert got == [(marker, True)]
