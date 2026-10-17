"""Minimal Mode: the vocabulary, the setting, the art gate, the live switch.

The pure half checks the tone/opacity mapping and the row text the
screens print; the setting's round-trip through config.json; the art
loader and the player service refusing art while it is on; the CSS for
every theme preset. The live half runs the REAL window under Broadway
(tests/broadway_minimal_probe.py): switching from Settings rebuilds every
screen without a restart and no art request leaves while it is on.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from xx_music.engine import AlbumRef, ArtistRef, Item, ItemKind, Track
from xx_music.paths import AppPaths
from xx_music.ui import minimal
from xx_music.ui.art import ArtLoader
from xx_music.ui.store import PREF_DEFAULTS, UiStore

PROBE = Path(__file__).parent / "broadway_minimal_probe.py"


@pytest.fixture(autouse=True)
def _fresh_state():
    minimal.reset()
    yield
    minimal.reset()


def _track(title="Glass Harbor", artist="Lumen Drive"):
    return Track(id="t1", title=title, artists=(ArtistRef(artist, "a1"),),
                 album=AlbumRef("Tidewater", "al1"), duration_s=214,
                 art_url="https://art.invalid/t1.jpg")


# -- tones ---------------------------------------------------------------------

def test_tones_map_to_weight_and_opacity_only():
    Tone = minimal.Tone
    assert minimal.tone_style(Tone.STRONG) == (1.0, 700)
    assert minimal.tone_style(Tone.PLAIN) == (0.9, 400)
    assert minimal.tone_style(Tone.QUIET) == (0.5, 400)
    for tone in Tone:
        assert minimal.tone_style(tone, enabled=False) == (0.25, 400)
        assert minimal.tone_class(tone, enabled=False) == "xx-mm-off"
    assert minimal.toggled(True) is Tone.STRONG
    assert minimal.toggled(False) is Tone.QUIET
    assert minimal.tone_class(Tone.STRONG) == "xx-mm-strong"


def test_css_uses_each_themes_foreground_never_fixed_white():
    from xx_ui import theme as xx_theme

    from xx_music.ui import css

    keys = xx_theme.preset_keys()
    assert {"amoled", "paper", "graphite", "aura"} <= set(keys)
    foregrounds = set()
    for key in keys:
        path = Path(os.environ["HOME"]) / f"{key}.json"
        path.write_text(json.dumps({"theme": key}), encoding="utf-8")
        theme = xx_theme.load(path)
        foregrounds.add(theme.foreground)
        sheet = css.render(theme)
        fg = theme.foreground
        assert f"label.xx-mm-strong, button.xx-mm-btn.xx-mm-strong label " \
               f"{{\n  color: {fg}; font-weight: 700; }}" in sheet, key
        assert f"color: alpha({fg}, 0.9)" in sheet, key
        assert f"color: alpha({fg}, 0.5)" in sheet, key
        assert f"color: alpha({fg}, 0.25)" in sheet, key
        block = sheet[sheet.index("/* Minimal Mode"):]
        assert "#fff" not in block.lower() and "white" not in block, key
    assert len(foregrounds) > 1  # light and dark presets both covered


# -- row text ----------------------------------------------------------------------

def test_title_artist_rows_and_suffixes():
    assert minimal.title_artist("Glass Harbor", "Lumen Drive") == \
        "Glass Harbor — Lumen Drive"
    assert minimal.title_artist("Solo", "") == "Solo"
    assert minimal.title_artist("Lumen Drive", "lumen drive") == \
        "Lumen Drive"
    assert minimal.title_artist("Night Drive", "7 songs", opens=True) == \
        "Night Drive — 7 songs  ▸"
    assert minimal.title_artist("A", "B", downloaded=True,
                                resolving=True) == "A — B  ↓  …"


def test_item_text_per_kind():
    track = _track()
    song = Item(ItemKind.TRACK, "t1", track.title, "ignored", "", track)
    assert minimal.item_text(song) == "Glass Harbor — Lumen Drive"
    assert minimal.item_text(song, downloaded=True).endswith("  ↓")
    album = Item(ItemKind.ALBUM, "al1", "Tidewater", "2024 · Lumen Drive")
    assert minimal.item_text(album) == "Tidewater — 2024 · Lumen Drive  ▸"
    playlist = Item(ItemKind.PLAYLIST, "p1", "Mix", "Playlist")
    assert minimal.item_text(playlist) == "Mix  ▸"
    artist = Item(ItemKind.ARTIST, "a1", "Lumen Drive", "Artist")
    assert minimal.item_text(artist) == "Lumen Drive  ▸"
    mood = Item(ItemKind.MOOD, "m1", "Chill", "Moods")
    assert minimal.item_text(mood) == "Chill  ▸"


def test_brackets_words_and_markers():
    assert minimal.bracket("play") == "[ play ]"
    assert minimal.tab_word("Home", True) == "HOME"
    assert minimal.tab_word("Library", False) == "library"
    assert minimal.playing_title("Glass Harbor", True) == "▸ Glass Harbor"
    assert minimal.playing_title("Glass Harbor", False) == "Glass Harbor"
    assert minimal.on_off(True) == "on" and minimal.on_off(False) == "off"
    assert minimal.sentence_word("Open Lumen Drive") == \
        "open Lumen Drive"


def test_shelves_cap_rows_behind_more():
    items = list(range(8))
    assert minimal.shelf_cut(items, False) == ([0, 1, 2, 3, 4], "more…")
    assert minimal.shelf_cut(items, True) == (items, "less")
    assert minimal.shelf_cut(items[:5], False) == (items[:5], "")
    assert minimal.SHELF_ROWS == 5


# -- the setting ---------------------------------------------------------------------

def test_setting_is_off_by_default_and_round_trips():
    assert PREF_DEFAULTS["minimal_mode"] is False
    paths = AppPaths("xx-radio-test")
    store = UiStore(paths)
    assert store.prefs()["minimal_mode"] is False
    store.set_pref("minimal_mode", True)
    assert UiStore(paths).prefs()["minimal_mode"] is True
    raw = json.loads(paths.config_path().read_text(encoding="utf-8"))
    assert raw["minimal_mode"] is True
    store.set_pref("minimal_mode", False)
    assert UiStore(paths).prefs()["minimal_mode"] is False


def test_switch_notifies_listeners_and_drops_dead_pages():
    calls = []

    class Page:
        def rebuild(self) -> None:
            calls.append("page")

    page = Page()
    minimal.listen(page.rebuild)
    minimal.listen(lambda: calls.append("fn"))
    assert minimal.set_active(True) is True
    assert minimal.active() and calls == ["page", "fn"]
    assert minimal.set_active(True) is False
    del page
    import gc

    gc.collect()
    minimal.set_active(False)
    assert calls == ["page", "fn", "fn"]
    assert minimal.listener_count() == 1


# -- no art while minimal --------------------------------------------------------------

def test_art_loader_refuses_every_request_while_minimal(tmp_path):
    fetched = []
    loader = ArtLoader(tmp_path, fetch=lambda url: fetched.append(url) or b"",
                       post=lambda cb: cb())
    loader.enabled = False
    delivered = []
    for px in (64, 96, 240):
        loader.request("https://art.invalid/a.jpg", px, delivered.append)
    loader.request("file:///nonexistent.jpg", 64, delivered.append)
    loader.shutdown()
    assert fetched == [] and delivered == []
    assert loader.refused == 4 and loader.fetches == 0
    assert not any(tmp_path.iterdir())


def test_ui_context_switch_gates_the_loader():
    from xx_music.fake_engine import FakeEngine
    from xx_music.ui.context import UiContext
    from xx_radio.main import SPEC

    loader = ArtLoader(None, fetch=lambda _u: b"", post=lambda cb: cb())
    ui = UiContext(SPEC, FakeEngine(), art=loader)
    ui.set_minimal(True)
    assert ui.minimal and not loader.enabled
    ui.set_minimal(False)
    assert not ui.minimal and loader.enabled
    loader.shutdown()


def test_player_service_shows_no_art_while_minimal(tmp_path):
    from xx_music.player.service import PlayerService

    paths = AppPaths("xx-radio-test")
    cached = paths.art_cache_dir() / "t1"
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"jpeg")
    fake = type("S", (), {"paths": paths})()
    fake.minimal_mode = lambda: PlayerService.minimal_mode(fake)
    track = _track()
    assert PlayerService.art_url_for(fake, track) == cached.as_uri()
    UiStore(paths).set_pref("minimal_mode", True)
    assert PlayerService.art_url_for(fake, track) == ""


# -- the live switch (real window, Broadway) ---------------------------------------------

def _broadway(tmp_path):
    broadwayd = shutil.which("gtk4-broadwayd")
    if broadwayd is None:
        pytest.skip("gtk4-broadwayd missing")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    env = dict(os.environ)
    env["XDG_RUNTIME_DIR"] = str(run_dir)
    for number in range(30, 120):
        proc = subprocess.Popen([broadwayd, f":{number}"],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, env=env)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and proc.poll() is None:
            if list(run_dir.glob("broadway*.socket")):
                return proc, number, run_dir
            time.sleep(0.1)
        proc.terminate()
        proc.wait(timeout=10)
    pytest.skip("could not start gtk4-broadwayd")


def test_live_switch_rebuilds_every_screen_without_art(tmp_path):
    proc, display, run_dir = _broadway(tmp_path)
    try:
        home = tmp_path / "home"
        home.mkdir()
        env = dict(os.environ)
        env.update(XDG_RUNTIME_DIR=str(run_dir), GDK_BACKEND="broadway",
                   BROADWAY_DISPLAY=f":{display}", HOME=str(home),
                   DBUS_SESSION_BUS_ADDRESS="unix:path=/nonexistent-test-bus")
        for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
                    "XDG_STATE_HOME"):
            env[var] = str(home / var.lower())
        root = Path(__file__).parent.parent
        env["PYTHONPATH"] = os.pathsep.join([
            str(root / "src"), str(root.parent / "common"),
            env.get("PYTHONPATH", "")])
        completed = subprocess.run([sys.executable, str(PROBE)],
                                   capture_output=True, text=True,
                                   timeout=180, env=env)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    output = (completed.stdout or "") + (completed.stderr or "")
    print(output)
    assert "FAIL:" not in output
    assert "ERROR:" not in output
    assert "DONE" in output
    assert completed.returncode == 0, output
