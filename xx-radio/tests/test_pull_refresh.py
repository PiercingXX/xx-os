"""Pull down at the top of a page to refresh it (owner: "swipe down on
xx-radio to refresh")."""
import inspect

import pytest

from xx_music.ui import common


class _Scroll:
    def __init__(self):
        self.handlers = {}

    def connect(self, signal, handler):
        self.handlers[signal] = handler


def test_top_overshoot_refreshes_once_per_pull(monkeypatch):
    gtk = pytest.importorskip('gi.repository.Gtk')
    import time
    now = [100.0]
    monkeypatch.setattr(time, 'monotonic', lambda: now[0])
    calls = []
    scroll = _Scroll()
    common.pull_to_refresh(scroll, lambda: calls.append(1))
    fire = scroll.handlers['edge-overshot']
    fire(scroll, gtk.PositionType.TOP)
    fire(scroll, gtk.PositionType.TOP)          # same pull: ignored
    fire(scroll, gtk.PositionType.BOTTOM)       # bottom edge: ignored
    now[0] += common.PULL_REFRESH_GAP_S + 0.1
    fire(scroll, gtk.PositionType.TOP)
    assert calls == [1, 1]


@pytest.mark.parametrize('module', ['home', 'library', 'search', 'pages'])
def test_pages_wire_pull_to_refresh(module):
    import importlib
    src = inspect.getsource(importlib.import_module(f'xx_music.ui.{module}'))
    assert 'pull_to_refresh(' in src
