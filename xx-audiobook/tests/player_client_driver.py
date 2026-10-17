"""PlayerClient driver for test_player_client.py (under dbus-run-session).

Runs the REAL PlayerService (FakeBackend, in-memory DB, offline) on the
private bus with a book loaded, then drives it the way the GUI does: the
M3 client calls from a worker thread, StateChanged through subscribe()
on the main loop. Prints PASS or raises.
"""

import os
import sys
import tempfile
import threading

HOME = tempfile.mkdtemp(prefix="xx-audiobook-client-")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = os.path.join(HOME, "config")
sys.path.insert(0, os.environ["SRC_DIR"])

import gi  # noqa: E402

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from xx_audiobook.player import FakeBackend, PlayerClient  # noqa: E402
from xx_audiobook.service.player_service import (  # noqa: E402
    PLAYER_BUS, PlayerService)
from xx_audiobook.store import Database  # noqa: E402
from xx_audiobook.timeline import (  # noqa: E402
    BookTimeline, Chapter, LoadedBook, Track)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    service = PlayerService(Database.in_memory(), None, FakeBackend())
    service.finish_open((LoadedBook(
        "it-9", "Driver Book", "Driver Author", None, None,
        BookTimeline(
            [Track("a", "file:///d/a.m4b", 0, 600000, "audio/mp4")],
            [Chapter(0, "One", 0, 300000),
             Chapter(1, "Two", 300000, 600000)])), 100000, 1.0))
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    service.register_on(connection)
    Gio.bus_own_name_on_connection(connection, PLAYER_BUS,
                                   Gio.BusNameOwnerFlags.NONE, None, None)
    client = PlayerClient()
    seen = []
    client.subscribe(lambda state: seen.append(state))
    loop = GLib.MainLoop()
    result = {}

    def work():
        try:
            state = client.get_state()
            check(state["item_id"] == "it-9", f"state: {state}")
            check(state["position_us"] == 100000 * 1000, "resume point")
            client.skip_forward()
            check(client.get_state()["position_us"] == 130000 * 1000,
                  "skip forward 30 s")
            client.skip_back()
            client.next_chapter()
            check(client.get_state()["chapter_index"] == 1, "next chapter")
            client.previous_chapter()
            client.seek_relative_us(-1000000)
            client.set_rate(1.5)
            check(abs(client.get_state()["rate"] - 1.5) < 1e-6, "rate")
            client.sleep(600000)
            check(client.get_state()["sleep_remaining_ms"] > 0, "sleep")
            client.cancel_sleep()
            client.sleep_end_of_chapter()
            check(client.get_state()["sleep_end_of_chapter"], "eoc")
            client.cancel_sleep()
            conflict = client.check_conflict("it-9")
            check(conflict.get("needs_prompt") is False, f"{conflict}")
            client.resolve_conflict("it-9", True)
            client.play()
            check(client.get_state()["status"] == "Playing", "playing")
            client.pause()
        except Exception as exc:  # noqa: BLE001 - reported below
            result["error"] = exc
        GLib.idle_add(loop.quit)

    threading.Thread(target=work, daemon=True).start()
    GLib.timeout_add_seconds(20, loop.quit)
    loop.run()
    if "error" in result:
        raise result["error"]
    check(seen, "no StateChanged reached subscribe()")
    check(any(s.get("rate") == 1.5 for s in seen), "rate signal")
    print("PASS")


if __name__ == "__main__":
    main()
