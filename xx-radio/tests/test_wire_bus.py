"""Wired service on a private bus: real service process, fake engine.

Uses ``xx_radio.service.build_service`` (the production wiring) with the
fake engine and a file:// proxy to fakesink, then drives it as a client
would under ``dbus-run-session``: enqueue/play, MPRIS metadata, Actions1
Like reaching the engine's ``rate()``. Never touches the host bus.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_HELPER = r'''
import sys
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gio, Gst  # noqa: E402

Gst.init(None)

from xx_music.engine import Rating  # noqa: E402
from xx_music.fake_engine import FakeEngine  # noqa: E402
from xx_radio.main import SPEC  # noqa: E402
from xx_radio import service as service_mod  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))

from player_fakes import FakeProxy, bake_ogg  # noqa: E402

BUS = SPEC.player_bus_name
PATH = SPEC.player_object_path
IFACE = SPEC.player_interface
MPATH = "/org/mpris/MediaPlayer2"
MPLAYER = "org.mpris.MediaPlayer2.Player"
PROPS = "org.freedesktop.DBus.Properties"
ACTIONS = "io.piercingxx.XXAuto.Actions1"

home = Path(__file__).parent / "helper-home"
media = home / "media"
proxied = home / "proxied"
media.mkdir(parents=True, exist_ok=True)
proxied.mkdir(parents=True, exist_ok=True)
import shutil as _shutil
for i in (1, 2, 3):
    bake_ogg(media / f"tr-{i:03d}.ogg", seconds=30.0, freq=400 + i * 60)
    _shutil.copy(media / f"tr-{i:03d}.ogg", proxied / f"tr-{i:03d}.ogg")

engine = FakeEngine(media_dir=media)
proxy = FakeProxy({f"tr-{i:03d}": proxied / f"tr-{i:03d}.ogg"
                   for i in (1, 2, 3)})
sink = Gst.ElementFactory.make("fakesink", None)
sink.set_property("sync", True)
service = service_mod.build_service(SPEC, engine=engine, proxy=proxy,
                                    audio_sink=sink)

loop = GLib.MainLoop()
state = {}
seen_changed, seen_props = [], []

conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
service.attach(conn)

conn.signal_subscribe(None, IFACE, "Changed", PATH, None,
                      Gio.DBusSignalFlags.NONE,
                      lambda *a: seen_changed.append(a[5].unpack()))
conn.signal_subscribe(None, PROPS, "PropertiesChanged", MPATH, None,
                      Gio.DBusSignalFlags.NONE,
                      lambda *a: seen_props.append(a[5].unpack()))

def _v(value):
    unpack = getattr(value, "unpack", None)
    if callable(unpack):
        try:
            return unpack()
        except Exception:
            return value
    return value


def fail(message):
    state["error"] = message
    loop.quit()


GLib.timeout_add(90000, lambda: (fail(
    "watchdog: stalled at " + state.get("stage", "start")), False)[1])


def get_all(iface, on_done):
    conn.call(BUS, MPATH, PROPS, "GetAll", GLib.Variant("(s)", (iface,)),
              GLib.VariantType.new("(a{sv})"), Gio.DBusCallFlags.NONE,
              5000, None,
              lambda c, r: on_done(c.call_finish(r).unpack()[0]))


def call_player1(method, params, on_done):
    conn.call(BUS, PATH, IFACE, method, params, None,
              Gio.DBusCallFlags.NONE, 5000, None,
              lambda c, r: on_done(c, r))


def tracks(*ids):
    import json
    from player_fakes import make_track
    return json.dumps([make_track(i).to_dict() for i in ids])


def step_play(c, r):
    try:
        c.call_finish(r).unpack()
    except Exception as exc:
        fail(f"Play failed: {exc}")
        return
    GLib.timeout_add(200, poll_playing)


def poll_playing():
    def check(props):
        props = {k: _v(v) for k, v in props.items()}
        if props["PlaybackStatus"] != "Playing":
            GLib.timeout_add(200, poll_playing)
            return
        state["stage"] = "playing"
        if service.player.uri != proxy.files["tr-001"].as_uri():
            fail(f"playbin uri not from proxy: {service.player.uri}")
            return
        if "resolve" not in engine.calls:
            fail("engine was never resolved through the proxy")
            return
        meta = {k: _v(v) for k, v in props["Metadata"].items()}
        if meta.get("xesam:title") != "tr-001":
            fail(f"wrong metadata: {meta}")
            return
        trackid = meta.get("mpris:trackid", "")
        if "-" in trackid or not trackid.startswith("/io/piercingxx/"):
            fail(f"bad trackid object path: {trackid}")
            return
        conn.call(SPEC.mpris_bus_name, MPATH, ACTIONS, "ListActions", None,
                  GLib.VariantType.new("(a(sssb))"),
                  Gio.DBusCallFlags.NONE, 5000, None, step_actions)
    try:
        get_all(MPLAYER, check)
    except Exception as exc:
        fail(f"poll failed: {exc}")
    return False


def step_actions(c, r):
    try:
        (rows,) = c.call_finish(r).unpack()
    except Exception as exc:
        fail(f"ListActions failed: {exc}")
        return
    by_id = {row[0]: row for row in rows}
    if set(by_id) != {"like", "dislike"}:
        fail(f"Actions1 must list Like/Dislike: {rows}")
        return
    conn.call(SPEC.mpris_bus_name, MPATH, ACTIONS, "ActivateAction",
              GLib.Variant("(s)", ("like",)), None,
              Gio.DBusCallFlags.NONE, 5000, None, step_liked)


def step_liked(c, r):
    try:
        c.call_finish(r).unpack()
    except Exception as exc:
        fail(f"ActivateAction failed: {exc}")
        return
    GLib.timeout_add(500, check_liked)
    return False


def check_liked():
    if engine.ratings.get("tr-001") != Rating.LIKE:
        fail(f"like never reached the engine: {engine.ratings}")
        return False
    if not seen_props:
        fail("no PropertiesChanged after the rating for xx-auto")
        return False
    call_player1("Queue", None, step_queue)
    return False


def step_queue(c, r):
    try:
        version, payload, index = c.call_finish(r).unpack()
    except Exception as exc:
        fail(f"Queue failed: {exc}")
        return
    import json
    ids = [t["id"] for t in json.loads(payload)]
    if ids != ["tr-001", "tr-002", "tr-003"] or index != 0:
        fail(f"bad queue: {ids} at {index}")
        return
    if not seen_changed:
        fail("no Changed signal seen")
        return
    own_id = Gio.bus_own_name_on_connection(
        conn, SPEC.app_id, Gio.BusNameOwnerFlags.NONE, None, None)
    Gio.bus_unown_name(own_id)
    call_player1("Snapshot", None, step_snapshot)


def step_snapshot(c, r):
    try:
        (payload,) = c.call_finish(r).unpack()
    except Exception as exc:
        fail(f"Snapshot after window exit failed: {exc}")
        return
    import json
    state_doc = json.loads(payload)
    if state_doc["track"]["id"] != "tr-001":
        fail(f"service lost state across the window exit: {state_doc}")
        return
    print("WIRE-BUS-OK")
    loop.quit()


call_player1("Play", GLib.Variant("(sis)", (tracks("tr-001", "tr-002",
                                                  "tr-003"), 0, "{}")),
             step_play)
loop.run()
service.request_stop()
assert "error" not in state, state["error"]
assert state.get("stage") == "playing", \
    f"chain stalled at {state.get('stage', 'start')}"
print("WIRE-BUS-OK")
'''


def test_wired_service_on_private_bus(tmp_path):
    if shutil.which("dbus-run-session") is None:
        pytest.skip("dbus-run-session is not installed")
    helper = tmp_path / "wire_bus_helper.py"
    helper.write_text(_HELPER)
    shutil.copy(ROOT / "tests" / "player_fakes.py",
                tmp_path / "player_fakes.py")
    env = {**os.environ,
           "PYTHONPATH": os.pathsep.join(
               (str(ROOT / "src"), str(ROOT.parent / "common"),
                str(tmp_path), os.environ.get("PYTHONPATH", "")))}
    proc = subprocess.run(
        ["dbus-run-session", "--", sys.executable, str(helper)],
        env=env, capture_output=True, text=True, timeout=180,
        cwd=str(tmp_path))
    assert proc.stdout.count("WIRE-BUS-OK") >= 1, (
        f"helper failed (rc={proc.returncode}):\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr[-4000:]}")
    assert proc.returncode == 0
