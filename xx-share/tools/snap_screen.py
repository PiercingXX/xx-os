#!/usr/bin/env python3
"""Navigation payload for scripts/snap.py: drive xx-share to one screen.

Runs with ``win`` (the main window) and ``app`` in scope. SCREEN picks
the screen: share, first, sending, sent, error, history, history-empty,
settings, settings-empty, receive, pin. Peers are network
state, so they are injected through the window's real peer-render path
from the demo_seed roster (never on the first-launch screens).
"""

import gc
import os
import sys
from pathlib import Path

SCREEN = os.environ.get("SCREEN", "share")
SNAP_W = int(os.environ.get("SNAP_W", "360"))
SNAP_H = int(os.environ.get("SNAP_H", "720"))

sys.path.insert(0, str(Path(os.environ["SNAP_SCRIPT"]).parent))
from demo_seed import PEERS  # noqa: E402


def _share_window():
    for obj in gc.get_objects():
        if type(obj).__name__ == "ShareWindow":
            return obj
    raise RuntimeError("ShareWindow not found")


def _fit_sheet():
    """Hide the main window and size the one visible sheet to the phone,
    like a compositor maximizing a modal on a phone screen."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    sw.window.set_visible(False)
    for top in Gtk.Window.list_toplevels():
        if top is not sw.window and top.get_visible():
            top.set_default_size(SNAP_W, SNAP_H)
            top.set_size_request(SNAP_W, SNAP_H)


PEER_DICTS = [
    {"alias": alias, "fingerprint": fingerprint, "addr": addr,
     "device_type": kind, "port": 53317}
    for alias, fingerprint, addr, kind in PEERS
]

sw = _share_window()
FIRST = SCREEN in ("first", "history-empty", "settings-empty")
if not FIRST:
    sw._peers = [dict(peer) for peer in PEER_DICTS]
    sw._render_peers()
    sw.refresh_favorites()
    sw._select_peer(PEER_DICTS[0]["fingerprint"])

if SCREEN in ("history", "settings"):
    sw._show_tab(SCREEN)
elif SCREEN in ("history-empty", "settings-empty"):
    sw._show_tab(SCREEN.split("-")[0])
elif SCREEN == "receive":
    sw.show_incoming("sess-demo", "Mara's Pixel",
                     ["IMG_20240612_184410.jpg", "birthday-dinner.mp4",
                      "notes.txt", "holiday-cabin.jpg"],
                     50725158)
    _fit_sheet()
elif SCREEN in ("sending", "sent", "error", "pin"):
    sw.files = sw.files[:1]
    sw.refresh_files()
    if SCREEN == "sending":
        sw.current_transfer = "tx-demo"
        sw._idle_bus_progress("tx-demo", 12582912, 29360128)
    elif SCREEN == "sent":
        sw._idle_done("Sent to Mara's Pixel", True)
    elif SCREEN == "pin":
        from service import PIN_NEEDED

        sw._idle_done(PIN_NEEDED, False)
    else:
        sw._idle_done("Mara's Pixel declined", False)
