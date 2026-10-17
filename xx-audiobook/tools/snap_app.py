#!/usr/bin/env python3
"""The real window for scripts/snap.py, wired without the network.

SNAP_SCENARIO=seeded: the seeded HOME plus DemoApi (home shelves, browse
views, collections, covers, stats, reviews and zones answer in-process)
and a signed-in admin session from an in-memory credential store (a
dummy token, no password, the hub probe answers nothing); no player
service, so commands apply locally. SNAP_SCENARIO=empty: a fresh HOME,
signed out (the Sign in screen). Neither touches libsecret, the hub
probe, NetworkManager or a socket.
"""

from __future__ import annotations

import os
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS_DIR)

import demo_seed  # noqa: E402

from xx_audiobook import api as api_module  # noqa: E402
from xx_audiobook import auth as auth_module  # noqa: E402
from xx_audiobook import main as main_module  # noqa: E402
from xx_audiobook.ui.context import AppContext  # noqa: E402


def session_store(signed_in: bool) -> auth_module.SessionStore:
    stored = None
    if signed_in:
        me = demo_seed.ME
        stored = auth_module.StoredCredentials(
            origin=demo_seed.ORIGIN, token="demo-token",
            user_id=me["user_id"], username=me["username"],
            display_name=me["display_name"], role="admin")
    coordinator = auth_module.AuthCoordinator(
        store=auth_module.InMemoryCredentialStore(stored),
        probe=lambda: None)
    return auth_module.SessionStore(coordinator,
                                    debug_override=lambda: None)


def make_context() -> AppContext:
    if os.environ.get("SNAP_SCENARIO", "seeded") == "empty":
        return AppContext(api_module.AudiobookApi(),
                          session=session_store(False))
    return AppContext(demo_seed.DemoApi(), session=session_store(True))


if __name__ == "__main__":
    raise SystemExit(main_module.main([], context_factory=make_context))
