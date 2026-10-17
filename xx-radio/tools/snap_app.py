#!/usr/bin/env python3
"""The real XX-Radio window for scripts/snap.py, wired without a network.

SNAP_SCENARIO picks the state:
  seeded       the seeded HOME, signed in, a queue playing (default)
  signed-out   the seeded HOME, not signed in
  empty        a fresh HOME: no queue, no cache, no history
  firstrun     a fresh HOME with the first-run screen
SNAP_ENGINE (ready | token-missing | failed | updating) and
SNAP_FIRSTRUN (running | failed | done) script the engine stand-ins.
SNAP_MINIMAL=1 saves Minimal Mode in the HOME's settings first, so the
window starts in it exactly as it would after a restart.
The player is a LocalBackend over the seeded queue (no service), the
engine the demo FakeEngine; nothing touches libsecret or a socket.
"""

from __future__ import annotations

import os
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS_DIR)

import demo_seed  # noqa: E402

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio  # noqa: E402

from xx_music.queue import Queue  # noqa: E402
from xx_music.ui.player import PAUSED, PLAYING, LocalBackend, PlayerModel  # noqa: E402
from xx_radio.main import SPEC  # noqa: E402
from xx_radio.ui.window import build_window  # noqa: E402

SCENARIO = os.environ.get("SNAP_SCENARIO", "seeded")


def _radio(track):
    return [demo_seed.track(t[0]) for t in demo_seed.TRACKS[5:14]
            if t[0] != track.id]


def make_window(app):
    signed_in = SCENARIO == "seeded"
    engine = demo_seed.DemoEngine(signed_in=signed_in)
    paths = SPEC.paths()
    queue = Queue.load(paths.queue_path()) if SCENARIO in (
        "seeded", "signed-out") else Queue()
    if os.environ.get("SNAP_MINIMAL") == "1":
        from xx_music.ui.store import UiStore

        UiStore(paths, engine.key).set_pref("minimal_mode", True)
    paused = os.environ.get("SNAP_PAUSED") == "1"
    backend = LocalBackend(queue, PAUSED if paused else PLAYING, 83.0,
                           radio=_radio)
    player = PlayerModel(backend, clock=lambda: 0.0)
    first = None
    if SCENARIO == "firstrun":
        first = demo_seed.DemoFirstRun(
            os.environ.get("SNAP_FIRSTRUN", "running"))
    return build_window(
        app, SPEC, engine, player=player,
        manager=demo_seed.DemoAccount(
            signed_in, fail=os.environ.get("SNAP_SIGNIN") == "fail"),
        control=demo_seed.DemoControl(os.environ.get("SNAP_ENGINE",
                                                     "ready")),
        first_run=first, welcome=False,
        downloads=demo_seed.demo_downloads
        if SCENARIO != "empty" else (lambda _ui: []))


def main() -> int:
    app = Adw.Application(application_id="io.piercingxx.XXRadio.Snap",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.connect("activate", lambda a: make_window(a).present())
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
