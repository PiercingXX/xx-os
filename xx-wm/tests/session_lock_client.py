"""A minimal lock client for test_session_lock_river.py: session_lock's
SessionLock with plain windows, reporting its events on stdout.

    python3 session_lock_client.py hold     lock and stay locked
    python3 session_lock_client.py cycle    lock, unlock once locked, exit
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
from gi.repository import GLib, Gtk  # noqa: E402

import session_lock  # noqa: E402


def say(line: str) -> None:
    print(line, flush=True)


def window() -> Gtk.Window:
    win = Gtk.Window()
    win.set_child(Gtk.Box())
    return win


def main() -> int:
    mode = sys.argv[1]
    Gtk.init()
    say(f'backend {session_lock.backend()}')
    loop = GLib.MainLoop()
    state = {}

    def ui():
        say('ui placed')
        return window()

    def ended(failed: bool) -> None:
        say('failed' if failed else 'unlocked')
        loop.quit()

    lock = session_lock.SessionLock(ui, window, on_ended=ended)
    state['lock'] = lock
    real_locked = lock._on_locked

    def on_locked(*args):
        real_locked(*args)
        say('locked')
        if mode == 'cycle':
            GLib.idle_add(lambda: lock.unlock() or False)

    lock._on_locked = on_locked
    if not lock.lock():
        return 1
    loop.run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
