#!/usr/bin/env python3
"""The real window for scripts/snap.py, wired without the network.

SNAP_SCENARIO=seeded: the seeded HOME plus DemoApi (home shelves, browse
views, collections and covers answer in-process; signed in, no player
service, so commands apply locally). SNAP_SCENARIO=empty: a fresh HOME,
signed out. Neither touches libsecret, the hub probe or a socket.
"""

from __future__ import annotations

import os
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS_DIR)

import demo_seed  # noqa: E402

from xx_audiobook import api as api_module  # noqa: E402
from xx_audiobook import main as main_module  # noqa: E402
from xx_audiobook.ui.context import AppContext  # noqa: E402


def make_context() -> AppContext:
    if os.environ.get("SNAP_SCENARIO", "seeded") == "empty":
        return AppContext(api_module.AudiobookApi())
    ctx = AppContext(demo_seed.DemoApi())
    ctx.signed_in = True
    return ctx


if __name__ == "__main__":
    raise SystemExit(main_module.main([], context_factory=make_context))
